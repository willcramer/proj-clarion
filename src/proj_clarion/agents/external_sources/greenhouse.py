"""Greenhouse job-board fetcher.

Boards-api.greenhouse.io exposes every Greenhouse-hosted careers page as
a JSON API, unauthenticated. We fetch /v1/boards/{slug}/jobs and flatten
the postings into a single text blob.

Why jobs are gold for our positioning: job posts explicitly name:
- The tech stack the team is currently running ("we use Kubernetes, Datadog
  for APM, GitHub Actions for CI")
- Active initiatives ("rebuilding our observability pipeline", "migrating
  to a unified platform")
- Org-shape signals ("Platform SRE team", "Customer Reliability Engineering")

For Grafana Cloud business observability positioning, an SRE/Platform job
posting is a buy signal — they're staffing the team that owns the obs
stack. Caterpillar hiring 4 platform engineers = real money about to move.
"""

from __future__ import annotations

import httpx
import structlog

from proj_clarion.agents.external_sources.constants import (
    GENERIC_USER_AGENT, MAX_SOURCE_CHARS, SOURCE_FETCH_TIMEOUT_S,
)

_logger = structlog.get_logger()


async def fetch_greenhouse_jobs(slug: str) -> str | None:
    """Return a flattened text blob of all current job postings.

    Format:
        --- {title} ({location}) ---
        {content_text}

    Returns None when the board doesn't exist or has zero open postings.
    The boundary "no posts" vs "no board" matters for the orchestrator:
    we surface "no positioning signals on jobs today" rather than treat
    an empty board as a fetch failure."""
    if not slug:
        return None
    from proj_clarion.observability.tools import track_tool_call
    with track_tool_call(
        agent_name="research_agent",
        tool_name="greenhouse_fetch",
        provider_name="greenhouse",
        target_system="boards-api.greenhouse.io",
        action="GET jobs",
        input_summary=f"slug={slug}",
    ) as _tool:
        result = await _fetch_greenhouse_jobs_impl(slug)
        _tool["output"] = "ok" if result else "miss"
        return result


async def _fetch_greenhouse_jobs_impl(slug: str) -> str | None:
    """Original Greenhouse fetch logic. Called from fetch_greenhouse_jobs
    inside a tool-span context."""
    try:
        async with httpx.AsyncClient(
            timeout=SOURCE_FETCH_TIMEOUT_S,
            headers={"User-Agent": GENERIC_USER_AGENT},
        ) as client:
            # ?content=true asks the server to include the job's HTML body
            # in the response, not just title/location. Without it we'd
            # need an N+1 fetch per posting, which is expensive + ratey.
            resp = await client.get(
                f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs"
                f"?content=true"
            )
            if resp.status_code != 200:
                _logger.debug("greenhouse.miss", slug=slug, status=resp.status_code)
                return None
            jobs = resp.json().get("jobs", []) or []
            if not jobs:
                return None

            # Flatten. We cap at ~50 postings to keep token cost predictable;
            # boards with hundreds of jobs are typically marketing-page noise
            # (job aggregators) where the signal density drops anyway.
            chunks: list[str] = []
            for j in jobs[:50]:
                title    = (j.get("title") or "").strip()
                location = ((j.get("location") or {}).get("name") or "").strip()
                # content is HTML; strip tags coarsely
                body = _strip_html(j.get("content") or "")
                chunks.append(
                    f"--- {title}"
                    + (f" ({location})" if location else "")
                    + f" ---\n{body}\n"
                )
            blob = "\n".join(chunks)
            return blob[:MAX_SOURCE_CHARS]
    except Exception as exc:  # noqa: BLE001
        _logger.debug("greenhouse.skip", slug=slug, error=str(exc))
        return None


def _strip_html(html: str) -> str:
    """Greenhouse's `content` field is a small subset of HTML — drop tags,
    decode common entities, normalise whitespace. We don't pull in
    trafilatura because each job body is small and the tags are uniform."""
    import re

    text = re.sub(r"</?(p|div|li|ul|ol|br|strong|em|h\d|span)[^>]*>", "\n", html, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = (
        text.replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&#39;", "'")
        .replace("&quot;", '"')
    )
    text = re.sub(r"[ \t\xa0]+", " ", text)
    text = re.sub(r"\n{2,}", "\n", text)
    return text.strip()
