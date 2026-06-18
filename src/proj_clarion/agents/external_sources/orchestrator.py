"""Orchestrate discovery → parallel fetch → parallel haiku extraction.

Public surface:

    summaries = await gather_external_signals(target_url, company_name)
    # → [ExternalSourceSummary(source_type, source_url, summary, raw_chars), ...]

Each summary is ready to splice into the synthesize-opus prompt as a
labelled section. The synthesize prompt will treat each one as a
citation source.

Latency budget for the full call: ~25-35s typical, ≤45s worst case.
- Discovery: ~2-4s (4 parallel probes, each with a 4s timeout)
- Source fetch: ~10s worst case (5 parallel, each with a 10s timeout)
- Haiku extraction: ~5-15s (5 parallel haiku calls)

That's well under the 90s end-to-end research budget — leaves plenty of
headroom for the opus synth call that follows.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Awaitable

import structlog

from proj_clarion.agents.external_sources.constants import RESEARCH_SOURCE_KEYS
from proj_clarion.agents.external_sources.discovery import discover_handles
from proj_clarion.agents.external_sources.edgar import fetch_edgar_10k
from proj_clarion.agents.external_sources.extractor import (
    SourceType, extract_signals,
)
from proj_clarion.agents.external_sources.github import fetch_github_org
from proj_clarion.agents.external_sources.greenhouse import fetch_greenhouse_jobs
from proj_clarion.agents.external_sources.lever import fetch_lever_jobs
from proj_clarion.agents.external_sources.wikidata import fetch_wikidata

_logger = structlog.get_logger()

# The full set of external source types this orchestrator knows how to
# fetch. Defined in the dependency-light `constants` module (so the CLI can
# import the key list cheaply) and re-exported here as the typed tuple.
ALL_SOURCE_TYPES: tuple[SourceType, ...] = RESEARCH_SOURCE_KEYS  # type: ignore[assignment]


@dataclass(frozen=True)
class ExternalSourceSummary:
    """One per source that returned signal. The synthesize prompt
    receives this as a single labelled block."""

    source_type: SourceType
    source_url: str         # human-readable URL the SE can verify against
    summary: str            # haiku-extracted markdown
    raw_chars: int          # diagnostic: how big was the raw text we summarised


async def gather_external_signals(
    target_url: str,
    company_name: str | None = None,
    *,
    sigil_conversation_id: str = "",
    enabled_sources: set[SourceType] | None = None,
) -> list[ExternalSourceSummary]:
    """Run the full discovery → fetch → extract pipeline.

    Always returns a list. Empty when nothing resolved or all extractions
    came back empty. The caller (research.synthesize_profile) just splices
    each summary into its prompt as one more source block.

    `enabled_sources`: when None (default), all source types are eligible —
    the historical behaviour. When a set, only those source types are
    fetched; the rest are skipped before any network call (so the SE can
    turn off, e.g., the SEC pull on a private company). An empty set means
    "no external enrichment" and short-circuits to [].

    Failure mode philosophy: every step is allowed to no-op independently.
    A DNS hiccup on Greenhouse never blocks EDGAR. A 10-K parse failure
    never blocks the GitHub fetch. The caller sees only the signals that
    actually came back."""

    def _on(stype: SourceType) -> bool:
        return enabled_sources is None or stype in enabled_sources

    if enabled_sources is not None and not enabled_sources:
        _logger.info("external.all_disabled", url=target_url)
        return []

    handles = await discover_handles(target_url, company_name=company_name)
    name_for_haiku = company_name or _name_from_handles(handles, target_url)

    # Step 1 — fetch all sources in parallel. Each fetcher returns either
    # raw text or None. Wrapping with `_safe` keeps a hang on one source
    # from holding up the whole gather. A source is fetched only when its
    # handle resolved AND it's enabled (see `enabled_sources`).
    fetch_tasks: dict[SourceType, Awaitable[str | None]] = {}
    if handles.edgar_cik and _on("edgar_10k"):
        fetch_tasks["edgar_10k"] = _safe(fetch_edgar_10k(handles.edgar_cik))
    if handles.greenhouse_slug and _on("greenhouse_jobs"):
        fetch_tasks["greenhouse_jobs"] = _safe(fetch_greenhouse_jobs(handles.greenhouse_slug))
    if handles.lever_slug and _on("lever_jobs"):
        fetch_tasks["lever_jobs"] = _safe(fetch_lever_jobs(handles.lever_slug))
    if handles.github_org and _on("github_org"):
        fetch_tasks["github_org"] = _safe(fetch_github_org(handles.github_org))
    if handles.wikidata_url and _on("wikidata"):
        fetch_tasks["wikidata"] = _safe(fetch_wikidata(handles.wikidata_url))

    if not fetch_tasks:
        _logger.info("external.no_handles", url=target_url)
        return []

    fetched_texts = await asyncio.gather(*fetch_tasks.values())
    fetched: dict[SourceType, str] = {
        stype: text
        for stype, text in zip(fetch_tasks.keys(), fetched_texts, strict=True)
        if text
    }

    if not fetched:
        _logger.info(
            "external.all_empty",
            url=target_url,
            handles=list(fetch_tasks.keys()),
        )
        return []

    # Step 2 — haiku-extract each source's text in parallel.
    extract_tasks = [
        extract_signals(
            text,
            source_type=stype,
            company_name=name_for_haiku,
            sigil_conversation_id=sigil_conversation_id,
        )
        for stype, text in fetched.items()
    ]
    summaries = await asyncio.gather(*extract_tasks)

    # Step 3 — pair up + drop empties + attach source URLs for citation.
    out: list[ExternalSourceSummary] = []
    for (stype, raw), summary in zip(fetched.items(), summaries, strict=True):
        if not summary:
            continue
        out.append(ExternalSourceSummary(
            source_type=stype,
            source_url=_canonical_source_url(stype, handles, target_url),
            summary=summary,
            raw_chars=len(raw),
        ))

    _logger.info(
        "external.done",
        url=target_url,
        sources_resolved=list(fetched.keys()),
        sources_summarised=[s.source_type for s in out],
    )
    return out


# ──────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────


async def _safe(coro):
    """Run a coroutine, swallowing exceptions to None. Lets gather()
    across all fetchers never fail-stop the whole research."""
    try:
        return await coro
    except Exception as exc:  # noqa: BLE001
        _logger.debug("external.fetch.error", error=str(exc))
        return None


def _name_from_handles(handles, target_url: str) -> str:
    """Best-effort company name for the haiku prompt. Prefers the explicit
    discovery handles (they're more reliable than parsing the URL host),
    falls back to the URL host."""
    if handles.github_org:
        return handles.github_org
    if handles.greenhouse_slug:
        return handles.greenhouse_slug
    if handles.lever_slug:
        return handles.lever_slug
    # Fall back to the URL host without TLD.
    from urllib.parse import urlparse
    host = (urlparse(target_url).hostname or "").lower().removeprefix("www.")
    return host.rsplit(".", 1)[0] or target_url


def _canonical_source_url(stype: SourceType, handles, target_url: str) -> str:
    """Return a human-friendly URL the SE can click to verify what the
    haiku extractor summarised. Used as the citation URL in the
    synthesize prompt."""
    if stype == "edgar_10k" and handles.edgar_cik:
        return f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={handles.edgar_cik}&type=10-K"
    if stype == "greenhouse_jobs" and handles.greenhouse_slug:
        return f"https://boards.greenhouse.io/{handles.greenhouse_slug}"
    if stype == "lever_jobs" and handles.lever_slug:
        return f"https://jobs.lever.co/{handles.lever_slug}"
    if stype == "github_org" and handles.github_org:
        return f"https://github.com/{handles.github_org}"
    if stype == "wikidata":
        return "https://www.wikidata.org/"
    return target_url
