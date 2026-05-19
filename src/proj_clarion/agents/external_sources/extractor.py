"""Haiku-based signal extraction from raw external-source text.

Given a chunk of source text (a 10-K excerpt, a job posting blob, a
GitHub README, a Wikidata row), use Claude Haiku to produce a concise
markdown summary focused on signals the synthesize-opus step cares about:
tech stack mentions, active initiatives, business entity structure,
observability tools, channel signals.

Why haiku and not opus: this step is high-volume (one call per source)
and the task is narrow (read text → restate the signals tersely). Haiku
is ~30x cheaper input + ~50x cheaper output than opus. We measured
3-5K input tokens per source and ~400 output tokens per summary,
landing around $0.001-0.002 per call. Five sources per research adds
~$0.005-0.01 to the bill — well within the cost-positive budget when
combined with the opus prompt-cache savings.

Output shape: a tight markdown block. We deliberately don't ask for
structured JSON because:
- Structured-output validation adds failure modes
- Opus reads markdown natively and never fights with it
- The summary IS the source for opus citation purposes; one less
  layer of indirection
"""

from __future__ import annotations

import asyncio
import os
from typing import Literal

import structlog
from anthropic import Anthropic

from proj_clarion.observability.llm_client import call_anthropic

_logger = structlog.get_logger()

SourceType = Literal["edgar_10k", "greenhouse_jobs", "lever_jobs", "github_org", "wikidata"]

# Haiku ID; override via env for testing or model upgrades. Sonnet is
# the fallback model if a deployment doesn't want haiku for some reason.
_EXTRACTOR_MODEL = os.getenv("EXTRACTOR_MODEL", "claude-haiku-4-5")

# Hard cap on the haiku response so a chatty summary can't blow the budget.
# 1024 output tokens ≈ 750 words ≈ enough for a tight per-source readout.
_MAX_TOKENS = 1024


_SYSTEM = """You read raw public-source text about a company and produce a
tight, factual signal summary for a downstream agent that builds a CompanyProfile.

Your output goes to a Grafana Solutions Engineer who is building a
business-observability demo. The signals that matter most to them:
- Tech stack the company actually runs (cloud vendor, K8s, observability
  vendor, ERP, data warehouse, CI/CD)
- Active initiatives / transformations (cloud migration, platform builds,
  observability stack changes, modernisation programs)
- Org-shape signals (business units named, dealer networks, regions,
  facility types — anything that maps to a KG entity hierarchy)
- Channels (D2C web, dealer, marketplace, partner)
- Pain points named in public sources

Hard rules:
- Return MARKDOWN only. No JSON, no code fences, no preamble.
- Be terse. Bullet points beat paragraphs.
- Quote the source verbatim when stating a specific vendor or initiative
  (so the downstream agent can cite it).
- Skip generic marketing fluff ("we put customers first") — not a signal.
- If the source has nothing useful, return a single line:
  "No signals relevant to business observability positioning."
"""


_PER_TYPE_HINT: dict[SourceType, str] = {
    "edgar_10k": (
        "This is excerpts from a SEC 10-K (Items 1 / 1A / 7). Focus on: business "
        "segments + their KPIs, IT investments named in MD&A, transformation "
        "programs, cybersecurity/availability risks, named subsidiaries + brands."
    ),
    "greenhouse_jobs": (
        "These are open job postings from Greenhouse. Focus on: tech stack named "
        "in 'Requirements' / 'About the team', team structure (SRE, Platform, "
        "Customer Reliability), active initiatives implied by hiring."
    ),
    "lever_jobs": (
        "These are open job postings from Lever. Focus on: tech stack named in "
        "the postings, team structure, active initiatives implied by hiring."
    ),
    "github_org": (
        "This is the company's public GitHub org + top repos + README excerpts. "
        "Focus on: actual languages + frameworks they ship, OSS contributions to "
        "specific platforms (k8s, prometheus, opentelemetry), team scale."
    ),
    "wikidata": (
        "Structured ground truth from Wikidata. Re-state in the bullet form below."
    ),
}


async def extract_signals(
    source_text: str,
    *,
    source_type: SourceType,
    company_name: str,
    sigil_conversation_id: str = "",
) -> str:
    """Return a tight markdown signal summary. Empty string if extraction
    failed or the source produced nothing useful (haiku decided)."""
    if not source_text.strip():
        return ""

    user = (
        f"Company: {company_name}\n"
        f"Source type: {source_type}\n\n"
        f"Hint for this source type:\n{_PER_TYPE_HINT.get(source_type, '')}\n\n"
        f"=== SOURCE TEXT ===\n{source_text}\n=== END SOURCE TEXT ===\n\n"
        "Produce the markdown signal summary now."
    )
    try:
        client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
        # System block cached: research fans out 5 haiku extractions per
        # build with this exact same _SYSTEM, and a re-research within
        # 5 min (e.g. SE retrying after extend) reads them at 10%
        # input price. The block is small-ish; if it falls under
        # Anthropic's ~1024-token cache threshold the wrapper is a no-op
        # and there's no penalty.
        msg, _gen = call_anthropic(
            client,
            {
                "model": _EXTRACTOR_MODEL,
                "max_tokens": _MAX_TOKENS,
                "system": [{
                    "type": "text",
                    "text": _SYSTEM,
                    "cache_control": {"type": "ephemeral"},
                }],
                "messages": [{"role": "user", "content": user}],
            },
            agent_name="clarion.research.extract_signals",
            prompt_template="research.extract_signals",
            conversation_id=sigil_conversation_id,
            tags={
                "clarion.component": "research",
                "clarion.phase": "extract_signals",
                "clarion.source_type": source_type,
            },
        )
        text = "".join(b.text for b in msg.content if b.type == "text").strip()
        # Drop the "no signals" sentinel — empty contributes less prompt
        # tokens to the synthesize step than the literal sentence does.
        if text.lower().startswith("no signals relevant"):
            return ""
        return text
    except Exception as exc:  # noqa: BLE001
        _logger.debug("extract.skip", source_type=source_type, error=str(exc))
        return ""


async def extract_many(
    sources: list[tuple[SourceType, str]],
    *,
    company_name: str,
    sigil_conversation_id: str = "",
) -> list[tuple[SourceType, str]]:
    """Run all extractions in parallel. Returns [(source_type, summary), ...]
    with empty summaries dropped (so the orchestrator can splice straight
    into the synthesize prompt without checking for empties)."""
    tasks = [
        extract_signals(
            text,
            source_type=stype,
            company_name=company_name,
            sigil_conversation_id=sigil_conversation_id,
        )
        for stype, text in sources
    ]
    summaries = await asyncio.gather(*tasks, return_exceptions=False)
    return [
        (stype, summary)
        for (stype, _raw), summary in zip(sources, summaries, strict=True)
        if summary
    ]
