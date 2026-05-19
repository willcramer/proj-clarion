"""Wikidata SPARQL fetcher.

Wikidata is a public, open-license, no-auth source of structured company
metadata. Coverage is incomplete (smaller companies are missing) but for
public companies and mid-to-large privates it's excellent ground truth
for: parent company, subsidiaries, industry, founded year, HQ location,
official ticker.

The biggest single value: subsidiaries. Hyster-Yale owns Hyster + Yale +
Bolzoni; researching just "hyster.com" loses the multi-brand structure
that defines their KG. Wikidata surfaces it in one SPARQL query.

We don't need authentication. The SPARQL endpoint at
https://query.wikidata.org/sparql is rate-limited to ~1 req/sec from
casual clients but our research load is well under that.
"""

from __future__ import annotations

from urllib.parse import urlencode

import httpx
import structlog

from proj_clarion.agents.external_sources.constants import (
    GENERIC_USER_AGENT, MAX_SOURCE_CHARS, SOURCE_FETCH_TIMEOUT_S,
)

_logger = structlog.get_logger()


# SPARQL: find any entity whose P856 (official website) matches the target
# URL, then enrich with the company's most-useful properties. UNION on the
# URL with/without trailing slash because Wikidata's normalisation varies
# by editor. LIMIT 5 because some sites are claimed by multiple entities
# (subsidiary + parent + brand). The orchestrator + opus can sort it out.
_SPARQL_TEMPLATE = """
SELECT ?company ?companyLabel ?industryLabel ?inceptionYear
       ?parentLabel ?countryLabel ?ticker ?employees
       (GROUP_CONCAT(DISTINCT ?subsidiaryLabel; separator="|") AS ?subsidiaries)
WHERE {
  VALUES ?url { <URL_A> <URL_B> }
  ?company wdt:P856 ?url .

  OPTIONAL { ?company wdt:P452 ?industry . }
  OPTIONAL { ?company wdt:P571 ?inception . BIND(YEAR(?inception) AS ?inceptionYear) }
  OPTIONAL { ?company wdt:P749 ?parent . }
  OPTIONAL { ?company wdt:P17  ?country . }
  OPTIONAL { ?company wdt:P249 ?ticker . }
  OPTIONAL { ?company wdt:P1128 ?employees . }
  OPTIONAL { ?company wdt:P355 ?subsidiary . }

  SERVICE wikibase:label { bd:serviceParam wikibase:language "en". }
}
GROUP BY ?company ?companyLabel ?industryLabel ?inceptionYear
         ?parentLabel ?countryLabel ?ticker ?employees
LIMIT 5
"""


async def fetch_wikidata(website_url: str) -> str | None:
    """Run a SPARQL match against P856 (official website). Return a
    human-readable text block, or None if no entity matches.

    The output is intentionally a small markdown blurb (≤2KB) — Wikidata's
    value is structured ground truth, not narrative. The haiku extractor
    will pass it through largely as-is."""
    if not website_url:
        return None
    from proj_clarion.observability.tools import track_tool_call
    with track_tool_call(
        agent_name="research_agent",
        tool_name="wikidata_fetch",
        provider_name="wikidata",
        target_system="query.wikidata.org",
        action="SPARQL",
        input_summary=website_url,
    ) as _tool:
        result = await _fetch_wikidata_impl(website_url)
        _tool["output"] = "ok" if result else "miss"
        return result


async def _fetch_wikidata_impl(website_url: str) -> str | None:
    """Original Wikidata fetch. Called from fetch_wikidata inside a
    tool-span context."""
    # Try the canonical URL + a trailing-slash variant. Wikidata editors
    # are inconsistent which one they store, so VALUES with both covers it.
    url_with_slash    = website_url if website_url.endswith("/") else website_url + "/"
    url_without_slash = website_url.rstrip("/")
    sparql = (
        _SPARQL_TEMPLATE
        .replace("URL_A", url_with_slash)
        .replace("URL_B", url_without_slash)
    )
    try:
        async with httpx.AsyncClient(
            timeout=SOURCE_FETCH_TIMEOUT_S,
            headers={
                "User-Agent": GENERIC_USER_AGENT,
                "Accept": "application/sparql-results+json",
            },
        ) as client:
            resp = await client.get(
                f"https://query.wikidata.org/sparql?{urlencode({'query': sparql})}",
            )
            if resp.status_code != 200:
                _logger.debug("wikidata.miss", url=website_url, status=resp.status_code)
                return None
            rows = (resp.json().get("results") or {}).get("bindings") or []
            if not rows:
                return None

            chunks: list[str] = ["=== Wikidata ==="]
            for r in rows:
                def v(k: str) -> str:
                    return (r.get(k) or {}).get("value", "") or ""
                chunks.append(
                    f"- entity:        {v('companyLabel') or '(unlabelled)'}\n"
                    f"  industry:      {v('industryLabel') or '(none)'}\n"
                    f"  parent:        {v('parentLabel') or '(none)'}\n"
                    f"  country:       {v('countryLabel') or '(none)'}\n"
                    f"  founded:       {v('inceptionYear') or '(none)'}\n"
                    f"  ticker:        {v('ticker') or '(none)'}\n"
                    f"  employees:     {v('employees') or '(unknown)'}\n"
                    f"  subsidiaries:  {(v('subsidiaries') or '').replace('|', ', ') or '(none)'}"
                )
            blob = "\n".join(chunks)
            return blob[:MAX_SOURCE_CHARS]
    except Exception as exc:  # noqa: BLE001
        _logger.debug("wikidata.skip", url=website_url, error=str(exc))
        return None
