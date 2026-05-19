"""Source-handle discovery.

Given a target company URL (and optionally a name), produce a best-effort
map of identifiers for the external sources we pull from:

    {
        "edgar_cik":       "0000893691"  | None,   # SEC CIK
        "github_org":      "hyster-yale" | None,
        "greenhouse_slug": "hyster"       | None,
        "lever_slug":      "hyster"       | None,
        "wikidata_url":    "https://...website..." (always returns the
                                                    canonicalised website URL
                                                    Wikidata uses as the
                                                    P856 "official website" lookup key),
    }

Design points worth knowing:
- No LLM here. Each lookup is a small HTTP call that resolves a handle.
  This keeps discovery cheap (~1-2s total) and deterministic.
- Each lookup is independently failable. A miss on Greenhouse never blocks
  EDGAR. The downstream orchestrator just skips a source whose handle is
  None.
- We deliberately keep this conservative: we don't try clever fuzzy matches.
  If a handle isn't a near-exact slug, we'd rather return None than fabricate
  the wrong company's data. False positives are much more expensive than
  empty fields.
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx
import structlog

_logger = structlog.get_logger()

# Aggressive timeout. Discovery is the "fast head" of research — if a
# lookup is slow we'd rather skip it than block all the fetches.
_DISCOVERY_TIMEOUT_S = 4.0


@dataclass(frozen=True)
class SourceHandles:
    """One row per company; each field is None when we couldn't find it."""

    edgar_cik:       str | None = None
    github_org:      str | None = None
    greenhouse_slug: str | None = None
    lever_slug:      str | None = None
    wikidata_url:    str | None = None  # canonical official website to query Wikidata


async def discover_handles(
    target_url: str,
    company_name: str | None = None,
) -> SourceHandles:
    """Run all discovery probes in parallel; return whatever resolved."""
    name = (company_name or _guess_name_from_url(target_url)).strip()
    if not name:
        return SourceHandles()

    edgar_t = _try(_discover_edgar_cik(name))
    gh_t    = _try(_discover_github_org(name))
    gh_slug = _try(_discover_greenhouse_slug(name))
    lv_slug = _try(_discover_lever_slug(name))

    edgar_cik, github_org, greenhouse_slug, lever_slug = await asyncio.gather(
        edgar_t, gh_t, gh_slug, lv_slug,
    )
    return SourceHandles(
        edgar_cik=edgar_cik,
        github_org=github_org,
        greenhouse_slug=greenhouse_slug,
        lever_slug=lever_slug,
        # Wikidata uses the canonical website URL as its identifier;
        # we just hand the target URL through and let the SPARQL query
        # do the match.
        wikidata_url=_canonicalise_url(target_url),
    )


# ──────────────────────────────────────────────────────────────────
# Per-source discovery probes
# ──────────────────────────────────────────────────────────────────


async def _discover_edgar_cik(name: str) -> str | None:
    """SEC EDGAR company search by name. Returns a 10-digit zero-padded CIK
    if a single confident match exists, else None.

    Uses the JSON endpoint at data.sec.gov/submissions/ via the company
    tickers file (data.sec.gov/files/company_tickers.json) — small, cached
    HTTP, perfect for heuristic match. The HTML browse-edgar page exists
    too but is harder to parse reliably."""
    try:
        async with httpx.AsyncClient(
            timeout=_DISCOVERY_TIMEOUT_S,
            headers={"User-Agent": "Proj Clarion (Grafana SE Demo Generator) research-contact@example.com"},
        ) as client:
            # company_tickers.json is keyed by sequential ints; values are
            # {cik_str, ticker, title}. ~10K entries, ~600KB. Cached aggressively.
            resp = await client.get("https://www.sec.gov/files/company_tickers.json")
            if resp.status_code != 200:
                return None
            data = resp.json()
            wanted = name.lower()
            # Strict-ish match: title contains all words of the company name.
            # Avoids matching "Hyster" against "Hysteresis Corp".
            name_tokens = [t for t in re.findall(r"\w+", wanted) if len(t) > 2]
            if not name_tokens:
                return None
            best: tuple[int, str] | None = None
            for entry in data.values():
                title = str(entry.get("title", "")).lower()
                if all(tok in title for tok in name_tokens):
                    # Prefer shorter titles (more focused match). Length tiebreak
                    # avoids "WALMART INC" losing to "WALMART STORES, INC. /WA/".
                    score = len(title)
                    cik = str(entry.get("cik_str", "")).zfill(10)
                    if best is None or score < best[0]:
                        best = (score, cik)
            return best[1] if best else None
    except Exception as exc:  # noqa: BLE001
        _logger.debug("discovery.edgar.skip", error=str(exc), name=name)
        return None


async def _discover_github_org(name: str) -> str | None:
    """Try plausible GitHub org slug variations against the public REST API.

    GitHub orgs are namespaced under github.com/<slug>. We try the name with
    common transformations: lowercase, hyphenated, no-spaces. First 200 response
    wins. We DON'T do a search-by-name because that returns user accounts and
    false positives; we want the org itself."""
    candidates = _slug_candidates(name)
    try:
        async with httpx.AsyncClient(timeout=_DISCOVERY_TIMEOUT_S) as client:
            for slug in candidates:
                resp = await client.get(
                    f"https://api.github.com/users/{slug}",
                    headers={"Accept": "application/vnd.github+json"},
                )
                if resp.status_code == 200:
                    body = resp.json()
                    # Only return org-type accounts. Personal accounts that
                    # happen to share the company name are noise.
                    if body.get("type") == "Organization":
                        return slug
                elif resp.status_code == 403:
                    # Rate-limited unauthenticated. Stop probing; future runs
                    # may succeed with a GITHUB_TOKEN.
                    _logger.debug("discovery.github.rate_limited")
                    return None
    except Exception as exc:  # noqa: BLE001
        _logger.debug("discovery.github.skip", error=str(exc), name=name)
    return None


async def _discover_greenhouse_slug(name: str) -> str | None:
    """Probe boards-api.greenhouse.io for a board matching the company.

    Greenhouse exposes `/v1/boards/{slug}` — a 200 indicates the board
    exists. We try a small set of candidate slugs in order; first 200 wins.
    Anti-pattern guard: we don't fuzz the slug aggressively because a 200
    on a wrong-but-real board (different company same slug) would be a
    confident lie. Stick to obvious transformations."""
    candidates = _slug_candidates(name)
    try:
        async with httpx.AsyncClient(timeout=_DISCOVERY_TIMEOUT_S) as client:
            for slug in candidates:
                resp = await client.get(f"https://boards-api.greenhouse.io/v1/boards/{slug}")
                if resp.status_code == 200:
                    return slug
    except Exception as exc:  # noqa: BLE001
        _logger.debug("discovery.greenhouse.skip", error=str(exc), name=name)
    return None


async def _discover_lever_slug(name: str) -> str | None:
    """Same idea as Greenhouse but for Lever. Their /v0/postings/{site}
    returns 200 with [] when the org exists but has no current postings."""
    candidates = _slug_candidates(name)
    try:
        async with httpx.AsyncClient(timeout=_DISCOVERY_TIMEOUT_S) as client:
            for slug in candidates:
                resp = await client.get(f"https://api.lever.co/v0/postings/{slug}?mode=json")
                # Lever returns 200 with a list when the site exists.
                # 404 means "no such site".
                if resp.status_code == 200:
                    return slug
    except Exception as exc:  # noqa: BLE001
        _logger.debug("discovery.lever.skip", error=str(exc), name=name)
    return None


# ──────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────


def _guess_name_from_url(url: str) -> str:
    """Strip protocol + www + TLD to get a usable company-name guess.

    "https://www.hyster.com/" → "hyster"
    "https://hyster-yale.com/about" → "hyster-yale"
    """
    try:
        host = urlparse(url).hostname or ""
    except Exception:  # noqa: BLE001
        return ""
    host = host.lower().removeprefix("www.")
    # Strip the rightmost TLD piece. Two-level TLDs (co.uk, com.au) are
    # rare enough that we don't bother — the worst case is "co" leaking
    # into the slug, which still works for discovery.
    return host.rsplit(".", 1)[0]


def _slug_candidates(name: str) -> list[str]:
    """Generate a small ordered list of slug variations to probe.

    Order matters: more-specific slugs first. We keep this short to avoid
    rate-limit issues and to avoid hitting wrong-company false positives."""
    n = name.lower().strip()
    if not n:
        return []
    # Tokens by non-word; useful for hyphenated names.
    tokens = re.findall(r"\w+", n)
    flat   = "".join(tokens)          # "hysteryale"
    hyphen = "-".join(tokens)         # "hyster-yale"
    first  = tokens[0] if tokens else ""
    candidates = [hyphen, flat, first]
    # De-dup preserving order; drop empties.
    seen: set[str] = set()
    out: list[str] = []
    for c in candidates:
        if c and c not in seen:
            seen.add(c)
            out.append(c)
    return out


def _canonicalise_url(url: str) -> str:
    """Wikidata stores official-website URLs with various trailing-slash /
    protocol combinations. We pass the raw URL through and let the SPARQL
    query try a couple of variations. This helper just trims tracking
    params + fragment that would confuse the match."""
    try:
        parsed = urlparse(url)
        return f"{parsed.scheme}://{parsed.netloc}{parsed.path or '/'}"
    except Exception:  # noqa: BLE001
        return url


async def _try(coro):
    """Run a coroutine, swallowing exceptions to None. Lets gather() across
    probes never fail-stop the whole discovery just because one source's
    HTTP layer hiccupped."""
    try:
        return await coro
    except Exception as exc:  # noqa: BLE001
        _logger.debug("discovery.probe.error", error=str(exc))
        return None
