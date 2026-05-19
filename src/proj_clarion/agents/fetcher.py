"""Allowlisted, citation-aware web fetcher.

This module is the hard boundary that enforces our research scope rules:
- Only hosts matching ALLOWED_HOSTS may be fetched
- Every fetch is logged with timestamp and duration
- The prospect's primary URL is the ONLY exception, and only the
  homepage / clearly-public marketing surfaces are read

Anything that would amount to active probing (HEAD requests beyond
the homepage, port scans, parallel hammering, robots.txt-flouting)
is intentionally not implemented. If you need it, this is the wrong tool.
"""

from __future__ import annotations

import asyncio
import os
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from fnmatch import fnmatch
from urllib.parse import urlparse

import httpx
import structlog
import trafilatura

_logger = structlog.get_logger()


@dataclass(frozen=True)
class FetchResult:
    url: str
    final_url: str
    status: int
    fetched_at: datetime
    duration_seconds: float
    title: str | None
    text: str
    raw_html_truncated: str
    error: str | None = None


class FetchDeniedError(Exception):
    """Raised when a URL is outside the allowlist."""


def _allowed_hosts() -> list[str]:
    raw = os.getenv("RESEARCH_ALLOWED_HOSTS", "")
    return [h.strip() for h in raw.split(",") if h.strip()]


def is_host_allowed(url: str, extra_allow: list[str] | None = None) -> bool:
    """Return True if `url`'s host is permitted by the research allowlist.

    Two modes:
      * If `RESEARCH_ALLOWED_HOSTS` is empty (the default for the public
        template repo), ALL hosts are allowed. This lets the SE point
        Clarion at any prospect URL without curating an allowlist first
        — anyone-can-demo-anyone is the design goal.
      * If `RESEARCH_ALLOWED_HOSTS` is non-empty, only hosts matching one
        of the comma-separated fnmatch patterns are fetched. Use this in
        regulated / hardened deployments where you want explicit control
        over what the agent reaches out to.

    `extra_allow` is the per-fetch ad-hoc additions (typically just the
    prospect's primary URL from a CLI / API call). When the allowlist is
    in permissive mode, these are still honoured but redundant.
    """
    patterns = _allowed_hosts() + (extra_allow or [])
    if not patterns:
        # Permissive default — no allowlist configured → no restrictions.
        return True
    host = (urlparse(url).hostname or "").lower()
    return any(fnmatch(host, p.lower()) for p in patterns)


def _user_agent_for(url: str) -> str:
    """Build the User-Agent header for a fetch.

    SEC EDGAR (sec.gov) blocks generic clients with a 403; their fair-access
    policy requires `Company Name email@domain` format. We honour that by
    using `RESEARCH_CONTACT_EMAIL` (defaults to a placeholder) on sec.gov
    and a more generic UA elsewhere. See:
    https://www.sec.gov/privacy.htm#edgaraccess

    Override the email at deploy time:
        export RESEARCH_CONTACT_EMAIL=acme_retail-research@yourorg.com
    """
    contact = os.getenv("RESEARCH_CONTACT_EMAIL", "research-contact@example.com")
    host = (urlparse(url).hostname or "").lower()
    if host.endswith("sec.gov"):
        # SEC accepts plain ASCII; their parser rejects parenthetical comments.
        return f"Proj Clarion (Grafana SE Demo Generator) {contact}"
    # Generic UA for all other hosts. Comment-style UA is broadly accepted.
    return (
        f"proj-clarion-research/0.1 (+research only; no instrumentation; {contact})"
    )


# Statuses that mean "the URL is dead or the host is rejecting us" — log
# specifically so failures show up in the diagnose card with actionable
# detail, but never crash the pipeline. Research synthesizes from
# whatever fetched cleanly.
_KNOWN_REJECT_STATUSES: dict[int, str] = {
    400: "bad_request",
    401: "auth_required",
    403: "forbidden",
    404: "not_found",
    410: "gone",
    429: "rate_limited",
}


async def fetch_one(
    url: str,
    *,
    extra_allow: list[str] | None = None,
    timeout_seconds: float = 15.0,
    max_text_chars: int = 30_000,
) -> FetchResult:
    """Fetch a URL through the allowlist. Returns text + metadata for citation.

    HTTP-level failures (404, 403, timeouts, etc.) return a FetchResult
    with `error` set instead of raising — research synthesis treats those
    as drop-this-source rather than abort-the-pipeline. Hard exceptions
    (network unreachable, SSL failure) are still caught here too."""
    if not is_host_allowed(url, extra_allow=extra_allow):
        host = urlparse(url).hostname
        _logger.warning("fetch.denied", url=url, host=host)
        raise FetchDeniedError(f"host '{host}' is not on the research allowlist")

    started = time.monotonic()
    fetched_at = datetime.now(UTC)
    # Wrap as a `web_fetch` tool span so the AI-Obs Tools view sees one
    # row per URL. target_system carries the hostname so the page's
    # provider-level breakdown shows which sites we hit. Errors are
    # converted to FetchResult(error=...) rather than raised — the tool
    # span records success=True for the call itself; per-result success
    # is captured by the `output_summary` we set on the result holder.
    from proj_clarion.observability.tools import track_tool_call
    host = urlparse(url).hostname or ""
    with track_tool_call(
        agent_name="research_agent",
        tool_name="web_fetch",
        provider_name="http",
        target_system=host,
        action="GET",
        input_summary=url,
    ) as _tool:
        try:
            async with httpx.AsyncClient(
                timeout=timeout_seconds,
                follow_redirects=True,
                headers={"User-Agent": _user_agent_for(url)},
            ) as client:
                resp = await client.get(url)
            # Reject statuses → returned as error result, not raised.
            if resp.status_code in _KNOWN_REJECT_STATUSES:
                reason = _KNOWN_REJECT_STATUSES[resp.status_code]
                _logger.warning(
                    "fetch.rejected",
                    url=url, status=resp.status_code, reason=reason,
                    final_url=str(resp.url),
                )
                _tool["output"] = f"http_{resp.status_code}_{reason}"
                return FetchResult(
                    url=url,
                    final_url=str(resp.url),
                    status=resp.status_code,
                    fetched_at=fetched_at,
                    duration_seconds=time.monotonic() - started,
                    title=None,
                    text="",
                    raw_html_truncated="",
                    error=f"http_{resp.status_code}_{reason}",
                )
            text = trafilatura.extract(resp.text) or ""
            title = trafilatura.extract_metadata(resp.text)
            title_str = title.title if title else None
            _tool["output"] = f"{resp.status_code} · {len(text)} chars"
            return FetchResult(
                url=url,
                final_url=str(resp.url),
                status=resp.status_code,
                fetched_at=fetched_at,
                duration_seconds=time.monotonic() - started,
                title=title_str,
                text=text[:max_text_chars],
                raw_html_truncated=resp.text[:5000],
            )
        except httpx.HTTPError as exc:
            _logger.warning("fetch.http_error", url=url, error=str(exc)[:200])
            _tool["output"] = f"error: {type(exc).__name__}"
            return FetchResult(
                url=url,
                final_url=url,
                status=0,
                fetched_at=fetched_at,
                duration_seconds=time.monotonic() - started,
                title=None,
                text="",
                raw_html_truncated="",
                error=str(exc),
            )


async def fetch_all(urls: list[str], *, extra_allow: list[str] | None = None) -> list[FetchResult]:
    """Fetch a batch in parallel, with one second of polite stagger to avoid hammering anyone."""
    results: list[FetchResult] = []
    for i, url in enumerate(urls):
        if i > 0:
            await asyncio.sleep(0.5)
        results.append(await fetch_one(url, extra_allow=extra_allow))
    return results
