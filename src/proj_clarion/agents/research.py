"""Research agent (walking skeleton).

LangGraph-shaped pipeline:
    plan_sources -> fetch_sources -> synthesize_profile

The synthesize step uses Claude with the CompanyProfile schema as a
structured-output target. The planner uses Claude to expand "acme_retail.com"
into a small list of candidate sources from the allowlist. The fetcher
applies the hard allowlist boundary.

This is intentionally minimal in v0.1. v0.2 will add:
- broader source planning (SEC EDGAR, careers pages, engineering blogs)
- LLM-judge evaluation of citation quality
- richer structured-output retries with self-critique
"""

from __future__ import annotations

import json
import os
import time
import uuid
from datetime import UTC, datetime
from typing import Any, TypedDict

import structlog
from anthropic import Anthropic

from proj_clarion.agents.fetcher import FetchDeniedError, FetchResult, fetch_all
from proj_clarion.observability.sigil_helper import call_anthropic
from proj_clarion.schemas import CompanyProfile

_logger = structlog.get_logger()


class ResearchState(TypedDict, total=False):
    target_url: str
    company_hint: str | None
    sources_to_fetch: list[str]
    fetched: list[FetchResult]
    profile: CompanyProfile | None
    errors: list[str]
    sigil_conversation_id: str
    gen_id_plan_sources: str
    gen_id_synthesize: str


def _client() -> Anthropic:
    return Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])


def _model() -> str:
    return os.getenv("ANTHROPIC_MODEL", "claude-opus-4-7")


PLAN_SYSTEM = """You plan a research pass on a single company using only public sources.

You will be given the company's primary URL. Suggest 4-8 additional URLs that
are likely to contain useful, factual, public material about that company:
their public marketing pages (homepage, about, careers, store locator),
case studies they've published with vendors, industry analyst write-ups,
press releases, and SEC filings if the company is public.

Hard rules:
- Only suggest URLs you are confident exist as real, indexable web pages
- Stay on these domain patterns: {allowlist}
- Never suggest URLs that look like internal admin, login, or staging surfaces
- Output JSON: {{"sources": ["https://...", ...]}}
"""

SYNTHESIZE_SYSTEM = """You produce a CompanyProfile JSON object that strictly conforms
to the provided schema. You will receive raw text extracts from public sources,
each tagged with a citation_id. Every field that has a `citations` array MUST be
populated only with citation_ids that appear in the provided sources, OR the
unsourced claim MUST be added to `synthesized_flags` with a clear rationale.

Hard rules:
- Output a single JSON object, no prose, no markdown fences
- Use only citation_ids that appear in the sources list
- For employee_count_estimate, revenue figures, and any other numeric claim
  that is not directly stated in a source, leave it null OR add a SynthesizedFlag
- Set growth_direction to 'unknown' if not directly evidenced
- Confidence on tech_stack_signals must be 'high' only if the source names the vendor
"""


async def plan_sources(state: ResearchState) -> ResearchState:
    """Ask Claude for a list of supporting URLs, then enforce the allowlist."""
    allowlist = os.getenv("RESEARCH_ALLOWED_HOSTS", "")
    request = {
        "model": _model(),
        "max_tokens": 1024,
        "system": PLAN_SYSTEM.format(allowlist=allowlist),
        "messages": [
            {
                "role": "user",
                "content": (
                    f"Primary URL: {state['target_url']}\n"
                    f"Company hint (if any): {state.get('company_hint') or 'none'}\n\n"
                    "Return only the JSON object."
                ),
            }
        ],
    }
    msg, gen_id = call_anthropic(
        _client(), request,
        agent_name="clarion.research.plan_sources",
        conversation_id=state.get("sigil_conversation_id", ""),
        tags={"clarion.component": "research", "clarion.phase": "plan_sources"},
    )
    state["gen_id_plan_sources"] = gen_id
    text = "".join(b.text for b in msg.content if b.type == "text")
    try:
        parsed = json.loads(text)
        suggestions = list(parsed.get("sources", []))
    except json.JSONDecodeError:
        _logger.warning("research.plan.bad_json", text=text[:200])
        suggestions = []

    sources = [state["target_url"]] + suggestions
    state["sources_to_fetch"] = sources
    return state


async def fetch_sources(state: ResearchState) -> ResearchState:
    """Apply allowlist; fetch what passes; record everything else as a denial."""
    target_host_allow = [
        (state["target_url"].split("/")[2]),
        "*." + (state["target_url"].split("/")[2]),
    ]
    results: list[FetchResult] = []
    errors = list(state.get("errors", []))

    fetched = await fetch_all(state["sources_to_fetch"], extra_allow=target_host_allow)
    for r in fetched:
        if r.error:
            errors.append(f"fetch_error[{r.url}]: {r.error}")
        results.append(r)

    state["fetched"] = results
    state["errors"] = errors
    return state


async def synthesize_profile(state: ResearchState) -> ResearchState:
    """Hand the fetched text to Claude with the schema; validate the result."""
    schema_json = json.dumps(CompanyProfile.model_json_schema(), indent=2)

    src_blocks: list[str] = []
    for i, r in enumerate(state["fetched"]):
        if r.error or not r.text:
            continue
        cid = f"src-{i+1:03d}"
        src_blocks.append(
            f"=== {cid} ===\n"
            f"url: {r.final_url}\n"
            f"title: {r.title or '(none)'}\n"
            f"fetched_at: {r.fetched_at.isoformat()}\n"
            f"text:\n{r.text}\n"
        )

    user = (
        f"Company primary URL: {state['target_url']}\n\n"
        f"=== SCHEMA ===\n{schema_json}\n\n"
        f"=== SOURCES ===\n" + "\n".join(src_blocks) + "\n\n"
        "Produce a single CompanyProfile JSON object that validates against the schema. "
        "Use citation_ids that match the source headers above. "
        "When you cannot ground a claim in the sources, add it to synthesized_flags. "
        "Return only the JSON object."
    )

    started = time.monotonic()
    parents = [g for g in [state.get("gen_id_plan_sources", "")] if g]
    msg, gen_id = call_anthropic(
        _client(),
        {
            "model": _model(),
            "max_tokens": 8192,
            "system": SYNTHESIZE_SYSTEM,
            "messages": [{"role": "user", "content": user}],
        },
        agent_name="clarion.research.synthesize_profile",
        parent_generation_ids=parents,
        conversation_id=state.get("sigil_conversation_id", ""),
        tags={"clarion.component": "research", "clarion.phase": "synthesize_profile"},
    )
    state["gen_id_synthesize"] = gen_id
    duration = time.monotonic() - started
    raw = "".join(b.text for b in msg.content if b.type == "text").strip()
    if raw.startswith("```"):
        raw = raw.split("```", 2)[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip("` \n")

    try:
        data: dict[str, Any] = json.loads(raw)
    except json.JSONDecodeError as exc:
        state["errors"].append(f"synth.json_decode_error: {exc}")
        return state

    data.setdefault("profile_id", f"prof-{uuid.uuid4().hex[:12]}")
    data.setdefault("schema_version", "0.1.0")
    data.setdefault("generated_at", datetime.now(UTC).isoformat())
    data.setdefault("research_duration_seconds", duration)
    data.setdefault("research_model", _model())

    # Belt-and-braces sanitisation: the LLM occasionally produces enum
    # values outside the schema's Literal set (e.g. 'ats' for an
    # applicant-tracking system). Coerce any such unknown to 'other'
    # rather than letting a single rogue value torch the whole profile.
    _sanitize_research_payload(data, errors=state["errors"])

    try:
        state["profile"] = CompanyProfile.model_validate(data)
    except Exception as exc:
        state["errors"].append(f"synth.validation_error: {exc}")

    return state


def _sanitize_research_payload(data: dict[str, Any], *, errors: list[str]) -> None:
    """Mutate `data` in place to fix common LLM-structured-output drift
    so the strict pydantic validation downstream doesn't reject the
    whole profile over one bad enum value. Any coercion is recorded
    in `errors` (as a `sanitized.*` entry) so the SE can see what was
    rubbed off if they care.

    Add a new branch here whenever you see a recurring schema-violation
    pattern in production runs — better to record-and-coerce than to
    fail the whole agent."""
    from proj_clarion.schemas import TechStackSignal

    # 1. tech_stack_signals[*].component_type must be in the schema's
    #    Literal enum. Coerce ANYTHING that isn't a recognised string
    #    (including None, missing, or a bare value the LLM made up like
    #    'ats' / 'iam') to 'other'. The previous version only coerced
    #    non-None unknowns, which silently passed null values through to
    #    the strict validator.
    valid_components = set(TechStackSignal.model_fields["component_type"].annotation.__args__)
    for sig in data.get("tech_stack_signals") or []:
        if not isinstance(sig, dict):
            continue
        ct = sig.get("component_type")
        if not isinstance(ct, str) or ct not in valid_components:
            errors.append(
                f"sanitized.tech_stack_signal: coerced component_type={ct!r} → 'other'"
            )
            sig["component_type"] = "other"

    # 2. Drop signals that are otherwise unsalvageable (e.g. missing
    #    required fields). Beats them rejecting the whole profile.
    cleaned: list[Any] = []
    for sig in data.get("tech_stack_signals") or []:
        if not isinstance(sig, dict):
            continue
        # Required fields per the schema
        if not sig.get("vendor_or_product"):
            errors.append(f"sanitized.tech_stack_signal: dropped (missing vendor_or_product)")
            continue
        cleaned.append(sig)
    if "tech_stack_signals" in data:
        data["tech_stack_signals"] = cleaned


async def run_research(target_url: str, company_hint: str | None = None) -> ResearchState:
    """Run the full research pass synchronously to completion."""
    state: ResearchState = {
        "target_url": target_url,
        "company_hint": company_hint,
        "sources_to_fetch": [],
        "fetched": [],
        "profile": None,
        "errors": [],
        "sigil_conversation_id": f"clarion-research-{uuid.uuid4().hex[:12]}",
    }

    _logger.info("research.start", url=target_url)
    state = await plan_sources(state)
    _logger.info("research.plan.done", source_count=len(state["sources_to_fetch"]))

    state = await fetch_sources(state)
    fetched_ok = sum(1 for r in state["fetched"] if not r.error and r.text)
    _logger.info("research.fetch.done", ok=fetched_ok, total=len(state["fetched"]))

    state = await synthesize_profile(state)
    if state["profile"]:
        _logger.info("research.synth.ok", profile_id=state["profile"].profile_id)
    else:
        _logger.warning("research.synth.failed", errors=state["errors"])

    return state
