"""GitHub org fetcher.

We hit the unauthenticated REST API to enumerate the org's public repos,
then for the top-K by star count we pull README + language stats. The
goal is ground-truth tech-stack signal — what they actually ship, not
what marketing pages say.

Auth note: with no GITHUB_TOKEN set the rate limit is 60 req/hr per IP.
Discovery (1 call) + listing (1 call) + top-K READMEs (5 calls) = 7 calls
per research. That's fine for development. For prod scale, set GITHUB_TOKEN
and the rate jumps to 5,000 req/hr.
"""

from __future__ import annotations

import os

import httpx
import structlog

from proj_clarion.agents.external_sources.constants import (
    GENERIC_USER_AGENT, MAX_SOURCE_CHARS, SOURCE_FETCH_TIMEOUT_S,
)

_logger = structlog.get_logger()

# Top-K repos by stars to deep-fetch. More than 5 hits rate limits fast
# and the long tail is rarely signal-bearing (forks, mirrors, archived).
_TOP_K_REPOS = 5


async def fetch_github_org(org: str) -> str | None:
    """Return a flattened summary: org bio, public repo count, top-K repos
    each with their description + primary language + README head.

    Returns None when the org doesn't exist (404) or rate-limit hit."""
    if not org:
        return None
    from proj_clarion.observability.tools import track_tool_call
    with track_tool_call(
        agent_name="research_agent",
        tool_name="github_org_fetch",
        provider_name="github",
        target_system="api.github.com",
        action="GET org",
        input_summary=f"org={org}",
    ) as _tool:
        result = await _fetch_github_org_impl(org)
        _tool["output"] = "ok" if result else "miss"
        return result


async def _fetch_github_org_impl(org: str) -> str | None:
    """Original GitHub-org fetch logic. Called from fetch_github_org
    inside a tool-span context."""
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": GENERIC_USER_AGENT,
    }
    token = os.getenv("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"

    try:
        async with httpx.AsyncClient(
            timeout=SOURCE_FETCH_TIMEOUT_S, headers=headers,
        ) as client:
            # Org metadata + repo list (sorted by stars, descending)
            org_resp = await client.get(f"https://api.github.com/users/{org}")
            if org_resp.status_code != 200:
                _logger.debug("github.org.miss", org=org, status=org_resp.status_code)
                return None
            org_data = org_resp.json()

            repos_resp = await client.get(
                f"https://api.github.com/users/{org}/repos"
                f"?per_page=100&type=public&sort=updated",
            )
            if repos_resp.status_code != 200:
                _logger.debug("github.repos.miss", org=org, status=repos_resp.status_code)
                return None
            repos = repos_resp.json() or []
            # Sort by stars locally so we don't pay for two listings.
            repos.sort(key=lambda r: int(r.get("stargazers_count") or 0), reverse=True)
            top = [r for r in repos if not r.get("fork") and not r.get("archived")][:_TOP_K_REPOS]

            chunks: list[str] = [
                f"=== GitHub org: {org} ===",
                f"description: {org_data.get('description') or '(none)'}",
                f"public_repos: {org_data.get('public_repos')}",
                f"followers: {org_data.get('followers')}",
                f"blog: {org_data.get('blog') or '(none)'}",
                "",
                f"--- TOP {len(top)} REPOS BY STARS ---",
            ]
            for r in top:
                stars = r.get("stargazers_count", 0)
                lang  = r.get("language") or "?"
                desc  = r.get("description") or ""
                topics = ", ".join((r.get("topics") or [])[:8])
                chunks.append(
                    f"{r.get('name')}  ({lang}, {stars}★)  topics: [{topics}]\n"
                    f"  {desc.strip()[:240]}"
                )
                # README head — only the first ~1500 chars, that's where the
                # tech-stack signal lives. Skip if rate-limit-near.
                readme = await _fetch_readme_head(client, org, r.get("name", ""))
                if readme:
                    chunks.append(f"  README excerpt:\n  {readme}")
            blob = "\n".join(chunks)
            return blob[:MAX_SOURCE_CHARS]
    except Exception as exc:  # noqa: BLE001
        _logger.debug("github.skip", org=org, error=str(exc))
        return None


async def _fetch_readme_head(client: httpx.AsyncClient, org: str, repo: str) -> str:
    """Fetch the README text via the GitHub raw API. Trim to head."""
    if not repo:
        return ""
    try:
        # /readme returns the file metadata + content (base64). Easier:
        # hit the raw URL via the github contents redirect.
        resp = await client.get(
            f"https://api.github.com/repos/{org}/{repo}/readme",
            headers={"Accept": "application/vnd.github.raw"},
        )
        if resp.status_code != 200:
            return ""
        # We asked for raw; GitHub returns the file bytes directly.
        text = resp.text
        # First 1500 chars — README headers + a few install/usage paras.
        # That's where tech stack tends to be mentioned.
        return text[:1500].strip()
    except Exception:  # noqa: BLE001
        return ""
