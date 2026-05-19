"""SEC EDGAR 10-K fetcher.

Why 10-K and not 10-Q or 8-K: the annual report has the richest narrative
content — Item 1 (Business), Item 1A (Risk Factors), Item 7 (MD&A). These
three items name segments, dealer networks, IT investments, transformation
programs, and risks the company is actively managing. Exactly the signals
Grafana Cloud business observability lights up.

Cost shape: the 10-K can be hundreds of pages. We extract just the three
relevant items (typically 30-80 pages combined) and trim to a hard token
ceiling before the haiku extractor sees it. Even truncated, this is the
highest signal-density source we have for public companies."""

from __future__ import annotations

import re

import httpx
import structlog

from proj_clarion.agents.external_sources.constants import (
    EDGAR_USER_AGENT, MAX_SOURCE_CHARS, SOURCE_FETCH_TIMEOUT_S,
)

_logger = structlog.get_logger()


async def fetch_edgar_10k(cik: str) -> str | None:
    """Return concatenated text from items 1, 1A, 7 of the latest 10-K.

    Returns None when the company has no 10-K filings (small caps that
    only file 10-KSB, or recent IPOs that haven't filed yet).
    """
    if not cik:
        return None
    from proj_clarion.observability.tools import track_tool_call
    with track_tool_call(
        agent_name="research_agent",
        tool_name="edgar_fetch",
        provider_name="sec_edgar",
        target_system="data.sec.gov",
        action="GET 10-K",
        input_summary=f"cik={cik}",
    ) as _tool:
        result = await _fetch_edgar_10k_impl(cik)
        _tool["output"] = "ok" if result else "no_10k"
        return result


async def _fetch_edgar_10k_impl(cik: str) -> str | None:
    """The original fetch logic, kept un-indented so the diff stays small.
    Called from fetch_edgar_10k inside the tool-span context."""
    try:
        async with httpx.AsyncClient(
            timeout=SOURCE_FETCH_TIMEOUT_S,
            headers={"User-Agent": EDGAR_USER_AGENT},
        ) as client:
            # Step 1: company submissions index → find the latest 10-K accession.
            sub = await client.get(f"https://data.sec.gov/submissions/CIK{cik}.json")
            if sub.status_code != 200:
                _logger.debug("edgar.submissions.miss", cik=cik, status=sub.status_code)
                return None

            recent = sub.json().get("filings", {}).get("recent", {})
            forms       = recent.get("form", [])
            accessions  = recent.get("accessionNumber", [])
            primary_doc = recent.get("primaryDocument", [])

            # Find the most-recent 10-K. accessionNumber needs hyphens stripped
            # for the URL path; primaryDocument is the html filename.
            acc_no: str | None = None
            doc:    str | None = None
            for i, form in enumerate(forms):
                if form == "10-K":
                    acc_no = accessions[i].replace("-", "")
                    doc    = primary_doc[i]
                    break
            if not acc_no or not doc:
                _logger.debug("edgar.no_10k", cik=cik)
                return None

            # Step 2: fetch the filing HTML and slice items 1/1A/7 out.
            doc_url = (
                f"https://www.sec.gov/Archives/edgar/data/"
                f"{int(cik)}/{acc_no}/{doc}"
            )
            html_resp = await client.get(doc_url)
            if html_resp.status_code != 200:
                _logger.debug("edgar.doc.miss", url=doc_url, status=html_resp.status_code)
                return None

            text = _strip_html_to_text(html_resp.text)
            slices = _extract_10k_items(text)
            if not slices:
                # Couldn't find item boundaries — fall back to a head snippet.
                # Better than nothing for the haiku extractor.
                return text[:MAX_SOURCE_CHARS]

            combined = "\n\n".join(
                f"--- ITEM {item_label} ---\n{body}"
                for item_label, body in slices
            )
            return combined[:MAX_SOURCE_CHARS]
    except Exception as exc:  # noqa: BLE001
        _logger.debug("edgar.skip", cik=cik, error=str(exc))
        return None


# ──────────────────────────────────────────────────────────────────
# HTML → text + item slicing
# ──────────────────────────────────────────────────────────────────


# We avoid trafilatura here because 10-K filings use idiosyncratic HTML
# (tables of contents, item anchors, in-line tables) that benefit from a
# more targeted approach: strip all tags, normalise whitespace, then
# slice by item-heading regex.
def _strip_html_to_text(html: str) -> str:
    # Drop script/style entirely
    html = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.S | re.I)
    # Replace block-level closes with newlines so item headers don't run together
    html = re.sub(r"</(p|div|tr|li|h\d|br)\s*/?>", "\n", html, flags=re.I)
    # Strip remaining tags
    text = re.sub(r"<[^>]+>", " ", html)
    # Unescape common entities
    text = (
        text.replace("&nbsp;", " ")
        .replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&#160;", " ")
    )
    # Collapse whitespace
    text = re.sub(r"[ \t\xa0]+", " ", text)
    text = re.sub(r"\n{2,}", "\n\n", text)
    return text.strip()


# Items 1, 1A, 7 are the high-signal sections. Each item header looks like
# "ITEM 1." or "Item 1A:" or "ITEM\n1." after our newline normalisation.
# Capture each item's body up to the next item header.
_ITEM_HEADER = re.compile(
    r"\bItem\s+(1A?|7A?|7|1|2|3|4|5|6|8|9|9A|9B|10|11|12|13|14|15)\b[.:\-\s]",
    re.I,
)


def _extract_10k_items(text: str) -> list[tuple[str, str]]:
    """Return [(item_label, body)] for the three items we want.

    If the regex can't lock onto item boundaries (some filers use unusual
    layout), returns [] — caller will fall back to a head snippet."""
    matches = list(_ITEM_HEADER.finditer(text))
    if not matches:
        return []

    # Build a (label, span_start, span_end) list, then keep only 1, 1A, 7.
    wanted = {"1", "1A", "7"}
    by_label: dict[str, tuple[int, int]] = {}
    for i, m in enumerate(matches):
        label = m.group(1).upper()
        if label not in wanted:
            continue
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        # 10-K table-of-contents lists items too, with short snippets between
        # them. Skip ToC hits by requiring a meaningful body length.
        if end - start < 1500:
            continue
        # Keep the FIRST substantial occurrence of each label (the body, not ToC).
        by_label.setdefault(label, (start, end))

    out: list[tuple[str, str]] = []
    for label in ("1", "1A", "7"):
        span = by_label.get(label)
        if span:
            out.append((label, text[span[0]:span[1]].strip()))
    return out
