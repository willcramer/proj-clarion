"""Shared constants for the external-source fetchers.

Keeps the timeout + char-cap policy in one place so every fetcher has the
same behaviour. The char-cap is the most important: it bounds the haiku
prompt size for the extractor, which bounds per-source cost. Without it,
a 600-page 10-K alone would double our research cost.
"""

from __future__ import annotations

import os

# Canonical external source-type keys, in display order. Single source of
# truth shared by the orchestrator (which fetches them), the CLI
# (--disable-source choices), and — via the API — the UI's per-source
# toggles, so the layers can't drift. Kept in this dependency-light module
# so the CLI can import it without pulling in the heavy fetcher stack.
RESEARCH_SOURCE_KEYS: tuple[str, ...] = (
    "edgar_10k", "greenhouse_jobs", "lever_jobs", "github_org", "wikidata",
)

# Human-friendly labels for each source key (UI checkboxes / CLI help).
RESEARCH_SOURCE_LABELS: dict[str, str] = {
    "edgar_10k": "SEC EDGAR (10-K)",
    "greenhouse_jobs": "Greenhouse job board",
    "lever_jobs": "Lever job board",
    "github_org": "GitHub org",
    "wikidata": "Wikidata",
}

# Per-fetch HTTP timeout. Each source is one or two HTTP calls; if any
# takes longer than this we skip it. Honest empty beats slow-blocking.
SOURCE_FETCH_TIMEOUT_S: float = 10.0

# Hard cap on the source text we hand to the haiku extractor.
# 60K chars ≈ 15K tokens for English prose, which keeps the haiku call
# under a few cents and well within the 200K context window. Sources
# longer than this get truncated to the head.
MAX_SOURCE_CHARS: int = 60_000

# SEC fair-access policy requires "Company Name email@domain" format.
# Override RESEARCH_CONTACT_EMAIL at deploy time.
EDGAR_USER_AGENT: str = (
    f"Proj Clarion (Grafana SE Demo Generator) "
    f"{os.getenv('RESEARCH_CONTACT_EMAIL', 'research-contact@example.com')}"
)

# Generic UA for all the non-SEC sources. The contact email is included
# as a comment so site operators can reach us if our traffic causes
# problems — every fetcher this package owns is read-only public data.
GENERIC_USER_AGENT: str = (
    f"proj-clarion-research/0.1 "
    f"(+research only; no instrumentation; "
    f"{os.getenv('RESEARCH_CONTACT_EMAIL', 'research-contact@example.com')})"
)
