"""Lever job-board fetcher.

api.lever.co/v0/postings/{site} returns every posting on a Lever-hosted
careers site as JSON, unauthenticated. Same shape and value as Greenhouse
for our purposes — see greenhouse.py for the positioning rationale.

We keep this in a separate module (rather than parameterising one) because
the response shapes differ subtly (`text` vs `content`, `categories.location`
vs `location.name`) and folding them together hides those differences.
"""

from __future__ import annotations

import httpx
import structlog

from proj_clarion.agents.external_sources.constants import (
    GENERIC_USER_AGENT, MAX_SOURCE_CHARS, SOURCE_FETCH_TIMEOUT_S,
)

_logger = structlog.get_logger()


async def fetch_lever_jobs(slug: str) -> str | None:
    """Same surface as fetch_greenhouse_jobs — returns flattened text or None."""
    if not slug:
        return None
    from proj_clarion.observability.tools import track_tool_call
    with track_tool_call(
        agent_name="research_agent",
        tool_name="lever_fetch",
        provider_name="lever",
        target_system="api.lever.co",
        action="GET postings",
        input_summary=f"slug={slug}",
    ) as _tool:
        result = await _fetch_lever_jobs_impl(slug)
        _tool["output"] = "ok" if result else "miss"
        return result


async def _fetch_lever_jobs_impl(slug: str) -> str | None:
    """Original Lever fetch logic. Called from fetch_lever_jobs inside
    a tool-span context."""
    try:
        async with httpx.AsyncClient(
            timeout=SOURCE_FETCH_TIMEOUT_S,
            headers={"User-Agent": GENERIC_USER_AGENT},
        ) as client:
            resp = await client.get(
                f"https://api.lever.co/v0/postings/{slug}?mode=json"
            )
            if resp.status_code != 200:
                _logger.debug("lever.miss", slug=slug, status=resp.status_code)
                return None
            jobs = resp.json() or []
            if not isinstance(jobs, list) or not jobs:
                return None

            chunks: list[str] = []
            for j in jobs[:50]:
                title = (j.get("text") or "").strip()
                cat = j.get("categories") or {}
                location = (cat.get("location") or "").strip()
                department = (cat.get("department") or "").strip()
                # Lever puts the description in `descriptionPlain` (text-only
                # variant). When that's missing it falls back to description
                # which is HTML — coarse-strip in that case.
                body = (j.get("descriptionPlain") or "").strip()
                if not body:
                    body = _strip_html(j.get("description") or "")
                header = " · ".join(p for p in [title, department, location] if p)
                chunks.append(f"--- {header} ---\n{body}\n")
            blob = "\n".join(chunks)
            return blob[:MAX_SOURCE_CHARS]
    except Exception as exc:  # noqa: BLE001
        _logger.debug("lever.skip", slug=slug, error=str(exc))
        return None


def _strip_html(html: str) -> str:
    import re

    text = re.sub(r"</?(p|div|li|ul|ol|br|strong|em|h\d|span)[^>]*>", "\n", html, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("&nbsp;", " ").replace("&amp;", "&")
    text = re.sub(r"[ \t\xa0]+", " ", text)
    text = re.sub(r"\n{2,}", "\n", text)
    return text.strip()
