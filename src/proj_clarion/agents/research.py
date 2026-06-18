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

from proj_clarion.agents.external_sources import (
    ExternalSourceSummary, gather_external_signals,
)
from proj_clarion.agents.fetcher import FetchResult, fetch_all
from proj_clarion.observability.llm_client import call_anthropic
from proj_clarion.schemas import CompanyProfile

_logger = structlog.get_logger()


class ResearchState(TypedDict, total=False):
    target_url: str
    company_hint: str | None
    # Operator-supplied discovery notes (notes-driven research path). When
    # set, synthesize_profile injects them as a trusted source; the gather
    # steps (plan_sources/fetch_sources/gather_external) may be skipped
    # entirely. None for normal URL-based research.
    notes: str | None
    sources_to_fetch: list[str]
    fetched: list[FetchResult]
    # External-source signals (EDGAR / jobs / GitHub / Wikidata) extracted
    # via cheap haiku calls. Empty list when no external handles resolved
    # for this company. Each entry becomes a labelled source block in the
    # synthesize-opus prompt.
    external_summaries: list[ExternalSourceSummary]
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
- For `company.name`, use the widely-recognized brand / trading name people
  actually say in conversation (e.g. "Huntington Bank", "Google", "Meta"), NOT
  the formal legal / holding-company entity. Put the formal entity in
  `company.legal_name` (e.g. "Huntington Bancshares Incorporated",
  "Alphabet Inc."). When a bank-holding company operates a consumer bank, name
  the bank. Keep the name in plain English.
- Output a single JSON object, no prose, no markdown fences
- Use only citation_ids that appear in the sources list
- For employee_count_estimate, revenue figures, and any other numeric claim
  that is not directly stated in a source, leave it null OR add a SynthesizedFlag
- Set growth_direction to 'unknown' if not directly evidenced
- Confidence on tech_stack_signals must be 'high' only if the source names the vendor
- If a list-shaped field (channels, tech_stack_signals, agentic_signals,
  recent_strategic_priorities, incumbent_observability, pain_signals,
  business_entity_candidates) has no evidence in the sources, leave it as an
  empty list. Do NOT fabricate plausible-sounding entries to fill it.
"""


# ──────────────────────────────────────────────────────────────────
# Organisational archetype classification — read by the Plan agent to
# pick KG entity types that fit the company's actual shape.
#
# The catalog below is what the model picks from. We deliberately keep
# the entity hierarchies SHORT (4-5 levels) and BIZ-OBS-SHAPED — only
# entities that map to a dashboard, SLO, or alert show up. Leaf
# granularity (individual SKUs, individual clinicians, individual
# users) is excluded on purpose.
# ──────────────────────────────────────────────────────────────────

ARCHETYPE_CATALOG = """
Pick ONE archetype from this catalog for `organizational_model.archetype`.
Use 'generic' only when no named archetype is a confident fit; in that case
set `organizational_model.fallback_used = true`.

The top of every hierarchy is always 'Company' (or 'Account' when the
customer's lens is "their customers"). Never use 'Brand' as the root.

- retail              : Company -> Brand -> Region -> StoreClass -> ProductCategory
                        Fit: B2C with multiple storefronts / shopper-facing brand portfolio.
                        Examples: Starbucks, Uline, Best Buy.

- b2b_industrial      : Company -> BusinessUnit -> DealerTier -> Territory -> ProductFamily
                        Fit: industrial equipment, parts, sold through dealer networks.
                        Examples: Hyster, Caterpillar, John Deere.

- healthcare_provider : Company -> Facility -> Department -> ServiceLine
                        Fit: hospitals, clinic networks, integrated delivery systems.
                        Examples: HCA, Mayo, Kaiser Permanente (provider arm).

- healthcare_payer    : Company -> PlanType -> MemberCohort -> ProviderNetwork
                        Fit: insurance, managed care, benefits administrators.
                        Examples: UnitedHealth, Aetna, Anthem.

- saas                : Company -> PlanTier -> Region -> WorkspaceClass
                        Fit: multi-tenant software with usage-based or seat-based tiers.
                        Examples: Notion, Linear, Datadog, GitHub.

- financial_services  : Company -> CustomerSegment -> ProductType -> Channel
                        Fit: banks, broker-dealers, lenders, fintechs with multiple product lines.
                        Examples: Chase, Schwab, Capital One, Stripe.

- media               : Company -> Property -> AudienceSegment -> DistributionChannel
                        Fit: publishers, streaming, broadcasters, large content houses.
                        Examples: Disney, Netflix, NYT, Spotify.

- logistics           : Company -> Hub -> RouteClass -> CustomerSegment -> ShipmentClass
                        Fit: parcel, freight, ocean carriers, last-mile fleets.
                        Examples: FedEx, Maersk, DHL, Flexport.

- generic             : Company -> BusinessUnit -> ProductLine -> CustomerSegment
                        Fit: explicit fallback. ONLY when no named archetype is a clear
                        match. Set `fallback_used` to true. Provide a `rationale` that
                        names what didn't fit.

For `archetype_confidence`:
  high   - sources directly evidence the shape (e.g. 10-K segment reporting names
           the same business units that fit b2b_industrial)
  medium - shape is strongly implied (homepage talks about dealer network) but not
           directly stated
  low    - educated guess from industry; consider 'generic' instead

`rationale` should be one short paragraph naming the SPECIFIC evidence (e.g.
"public dealer locator + 10-K item 1 names North America, EMEA, AP business
units → b2b_industrial").
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
        prompt_template="research.plan_sources",
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


async def gather_external(state: ResearchState) -> ResearchState:
    """Pull signals from public-API sources (EDGAR / jobs / GitHub / Wikidata).

    Independent of fetch_sources — we want both: the prospect's own pages
    (which fetch_sources already grabbed) AND external corroborating data.
    Failures are silent at this layer; the orchestrator inside the
    external_sources package already swallows per-source errors so a 10-K
    miss never blocks a GitHub fetch.

    The returned summaries are pre-summarised by haiku, so the synth opus
    call sees ~500 tokens per source instead of ~15K. That's the cost
    discipline that makes adding 5 external sources fit in our budget."""
    try:
        summaries = await gather_external_signals(
            state["target_url"],
            company_name=state.get("company_hint"),
            sigil_conversation_id=state.get("sigil_conversation_id", ""),
        )
    except Exception as exc:  # noqa: BLE001
        # Even the orchestrator failing shouldn't kill research — it's
        # an enrichment, not a hard dependency.
        _logger.warning("research.external.error", error=str(exc))
        summaries = []

    state["external_summaries"] = summaries
    _logger.info(
        "research.external.done",
        count=len(summaries),
        types=[s.source_type for s in summaries],
    )
    return state


async def synthesize_profile(state: ResearchState) -> ResearchState:
    """Hand the fetched text to Claude with the schema; validate the result.

    The system prompt is structured as multiple blocks so the largest chunk
    (the JSON-schema dump + archetype catalog, which is identical every call)
    is marked `cache_control=ephemeral`. Anthropic charges 0.1x for cached
    reads, so after the first call within a 5min window the schema portion
    is effectively free. On Hyster/McDonald's/Uline-class profiles the
    schema dominates input tokens, so this is the highest-leverage saving.
    """
    schema_json = json.dumps(CompanyProfile.model_json_schema(), indent=2)

    src_blocks: list[str] = []
    src_index = 0  # incremented per emitted source so citation_ids stay unique

    # Operator-supplied discovery notes (e.g. from a customer discovery call)
    # are injected as a first-class, trusted source so the synthesize agent
    # grounds the profile in what the SE actually learned. Used by the
    # notes-driven path (run_research_from_notes); in notes-only mode this is
    # the sole source block.
    _notes = (state.get("notes") or "").strip()
    if _notes:
        src_index += 1
        src_blocks.append(
            f"=== src-{src_index:03d} ===\n"
            f"url: (operator discovery notes)\n"
            f"title: Discovery notes / SE input\n"
            f"text:\n{_notes}\n"
        )

    for r in state["fetched"]:
        if r.error or not r.text:
            continue
        src_index += 1
        cid = f"src-{src_index:03d}"
        src_blocks.append(
            f"=== {cid} ===\n"
            f"url: {r.final_url}\n"
            f"title: {r.title or '(none)'}\n"
            f"fetched_at: {r.fetched_at.isoformat()}\n"
            f"text:\n{r.text}\n"
        )

    # External-source enrichment: each summary is a haiku-distilled signal
    # block from EDGAR / Greenhouse / Lever / GitHub / Wikidata. We emit
    # them as additional citation sources so the synthesize agent can
    # cite them on tech_stack_signals / recent_strategic_priorities /
    # business_entity_candidates / etc. without breaking the citation
    # contract.
    for summary in state.get("external_summaries") or []:
        src_index += 1
        cid = f"src-{src_index:03d}"
        src_blocks.append(
            f"=== {cid} ===\n"
            f"url: {summary.source_url}\n"
            f"title: external/{summary.source_type}\n"
            f"summary (haiku-extracted from {summary.raw_chars} chars of raw source):\n"
            f"{summary.summary}\n"
        )

    # Prior-research feedback: if we've researched this same primary URL
    # before, surface the SE-accepted synthesized claims as positive signal
    # ("the SE has previously verified these claims; trust them again unless
    # the new sources contradict"). Keeps the agent from re-flagging things
    # the SE already approved. Cheap to compute, small token cost, gets
    # cached as part of the user prompt.
    prior_signal = _prior_accepted_claims_block(state["target_url"])

    user_blocks: list[dict[str, Any]] = [
        {
            "type": "text",
            "text": (
                f"Company primary URL: {state['target_url']}\n\n"
                f"=== SOURCES ===\n" + "\n".join(src_blocks) + "\n\n"
                + (prior_signal + "\n\n" if prior_signal else "")
                + "Produce a single CompanyProfile JSON object that validates "
                "against the schema in the system prompt. Pick ONE "
                "organizational_model.archetype from the catalog in the system "
                "prompt. Use citation_ids that match the source headers above. "
                "When you cannot ground a claim in the sources, add it to "
                "synthesized_flags or leave the field empty — do NOT fabricate. "
                "Return only the JSON object."
            ),
        },
    ]

    # System prompt is a list of content blocks so we can mark the schema +
    # archetype catalog (huge, identical every call) as ephemeral-cached.
    # The narrative SYNTHESIZE_SYSTEM stays uncached because it's small.
    system_blocks: list[dict[str, Any]] = [
        {"type": "text", "text": SYNTHESIZE_SYSTEM},
        {
            "type": "text",
            "text": (
                f"=== SCHEMA ===\n{schema_json}\n\n"
                f"=== ORG ARCHETYPE CATALOG ===\n{ARCHETYPE_CATALOG}"
            ),
            "cache_control": {"type": "ephemeral"},
        },
    ]

    started = time.monotonic()
    parents = [g for g in [state.get("gen_id_plan_sources", "")] if g]
    msg, gen_id = call_anthropic(
        _client(),
        {
            "model": _model(),
            "max_tokens": 8192,
            "system": system_blocks,
            "messages": [{"role": "user", "content": user_blocks}],
        },
        agent_name="clarion.research.synthesize_profile",
        prompt_template="research.synthesize_profile",
        parent_generation_ids=parents,
        conversation_id=state.get("sigil_conversation_id", ""),
        tags={"clarion.component": "research", "clarion.phase": "synthesize_profile"},
    )

    # Surface cache effectiveness in the logs so we can verify caching is
    # actually saving tokens. First call within a 5min window writes the
    # cache (cache_creation_input_tokens > 0); subsequent calls read it
    # (cache_read_input_tokens > 0). If neither moves, caching is broken.
    usage = getattr(msg, "usage", None)
    if usage is not None:
        _logger.info(
            "research.synth.tokens",
            input=getattr(usage, "input_tokens", None),
            output=getattr(usage, "output_tokens", None),
            cache_creation=getattr(usage, "cache_creation_input_tokens", None),
            cache_read=getattr(usage, "cache_read_input_tokens", None),
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
    else:
        # Structural evals: only run when validation succeeded. A
        # validation-failure path already surfaces in state["errors"]
        # and there's no profile object to inspect.
        try:
            from proj_clarion.observability.evals import run_research_evals
            run_research_evals(state["profile"], model=_model())
        except Exception as exc:  # noqa: BLE001
            _logger.warning("research.evals.skip", error=str(exc)[:200])

    return state


def _prior_accepted_claims_block(target_url: str) -> str:
    """Build a prior-feedback block from past SE decisions on the same URL.

    Reads the profile_audit_log for any prior profile sharing this target's
    URL host, surfaces rows where the SE clicked "Accept" on a synthesized
    claim (prompt starts with 'accept claim:'), and returns a small block
    the synthesize prompt can use as positive signal. Returns the empty
    string when there's no prior history — common on the first research
    pass for a given company.

    Cost: ~50-300 input tokens depending on how many claims were accepted.
    Lives in the cached user-prompt portion so re-runs within 5min are
    near-free. Worth it because re-flagging things the SE already approved
    is the most common "agent annoyance" the SE flagged in feedback."""
    # Lazy import to keep the module importable in offline test contexts
    # that don't ship the storage layer.
    try:
        from urllib.parse import urlparse

        from proj_clarion.storage import (
            ProfileAuditRepo,
            ProfileRepo,
            session_scope,
        )
    except ImportError:
        return ""

    try:
        host = (urlparse(target_url).hostname or "").lower()
        if not host:
            return ""

        accepted: list[str] = []
        with session_scope() as s:
            # Find the most-recent profile whose primary_url shares this host.
            # ProfileRepo.list returns (profile_id, created_at, url) newest-first.
            prior_profile_id: str | None = None
            for pid, _created, url in ProfileRepo().list(s, limit=20):
                try:
                    if (urlparse(url).hostname or "").lower() == host:
                        prior_profile_id = pid
                        break
                except Exception:  # noqa: BLE001
                    continue
            if prior_profile_id is None:
                return ""

            # Pull accept-claim audit rows. The accept-claim endpoint writes
            # prompts that start with the literal 'accept claim:' — we filter
            # in Python rather than via SQL LIKE because the audit history
            # is small per-profile (<100 rows typically).
            history = ProfileAuditRepo().history(s, prior_profile_id, limit=200)
            for row in history:
                prompt = row.get("prompt") or ""
                if not prompt.startswith("accept claim:"):
                    continue
                field_path = prompt[len("accept claim:"):].strip()
                if field_path:
                    accepted.append(field_path)

        if not accepted:
            return ""

        # De-dup while preserving order; cap to 20 so the block stays bounded.
        seen: set[str] = set()
        ordered: list[str] = []
        for f in accepted:
            if f in seen:
                continue
            seen.add(f)
            ordered.append(f)
        ordered = ordered[:20]

        bullets = "\n".join(f"- {f}" for f in ordered)
        return (
            "=== PRIOR SE FEEDBACK ===\n"
            "On a previous research pass for this same company URL the SE\n"
            "explicitly ACCEPTED these synthesized claims. Treat them as\n"
            "verified — surface the same fields again with the same shape\n"
            "if the new sources support them, and do NOT re-add them to\n"
            "synthesized_flags unless the new sources actively contradict:\n"
            f"{bullets}"
        )
    except Exception as exc:  # noqa: BLE001
        # Don't let a DB hiccup break research. Log + continue without signal.
        _logger.debug("research.prior_signal.skip", error=str(exc))
        return ""


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
    # Step 1 + 2 — plan + fetch the prospect's own pages.
    # Step 3 — pull external public sources in parallel with no extra
    # latency cost: fetch_sources and gather_external can both run while
    # the SE waits on the URL fetch. Keep them sequential for now because
    # plan_sources's output feeds fetch_sources, and gather_external is
    # ALREADY parallelised internally (5-way fan-out + haiku-extract).
    state = await plan_sources(state)
    _logger.info("research.plan.done", source_count=len(state["sources_to_fetch"]))

    # Run prospect-page fetch + external-source enrichment concurrently.
    # Both coroutines mutate the same `state` dict in place — they touch
    # disjoint keys (fetch_sources writes `fetched`/`errors`, gather_external
    # writes `external_summaries`), so the concurrent mutation is safe.
    # This nested gather saves ~10-15s of wall-clock on every research.
    import asyncio as _asyncio
    state, _ = await _asyncio.gather(
        fetch_sources(state),
        gather_external(state),
    )
    fetched_ok = sum(1 for r in state["fetched"] if not r.error and r.text)
    _logger.info(
        "research.fetch.done",
        ok=fetched_ok,
        total=len(state["fetched"]),
        external_summaries=len(state.get("external_summaries") or []),
    )

    state = await synthesize_profile(state)
    if state["profile"]:
        _logger.info("research.synth.ok", profile_id=state["profile"].profile_id)
    else:
        _logger.warning("research.synth.failed", errors=state["errors"])

    return state


async def run_research_from_notes(
    notes: str,
    *,
    company_hint: str | None = None,
    target_url: str | None = None,
    also_fetch: bool = False,
) -> ResearchState:
    """Build a CompanyProfile from operator-supplied discovery notes.

    Makes the web/external investigation OPTIONAL. The notes become a
    trusted source feeding the same `synthesize_profile` step the URL-based
    path uses, so the resulting profile is a drop-in for the plan phase
    (`plan run <profile>` works unchanged).

    Modes:
      * notes-only (default): skip plan_sources / fetch_sources /
        gather_external — synthesize straight from `notes`, no web access.
      * notes + web (`also_fetch=True`, requires `target_url`): run the
        normal gathering steps AND fold the notes in as a trusted source —
        the deep-dive enrichment layered on top of discovery findings.
    """
    state: ResearchState = {
        # A real URL when supplied (recorded on the profile / used for fetch);
        # otherwise a benign placeholder so target_url-dependent helpers
        # (e.g. prior-accepted-claims lookup) stay well-defined.
        "target_url": target_url or "https://discovery.local",
        "company_hint": company_hint,
        "notes": notes,
        "sources_to_fetch": [],
        "fetched": [],
        "profile": None,
        "errors": [],
        "sigil_conversation_id": f"clarion-research-notes-{uuid.uuid4().hex[:12]}",
    }

    if also_fetch and target_url:
        _logger.info("research.notes.start", mode="notes+web", url=target_url)
        state = await plan_sources(state)
        import asyncio as _asyncio
        state, _ = await _asyncio.gather(
            fetch_sources(state),
            gather_external(state),
        )
    else:
        _logger.info("research.notes.start", mode="notes-only")

    state = await synthesize_profile(state)
    if state["profile"]:
        _logger.info("research.synth.ok", profile_id=state["profile"].profile_id)
    else:
        _logger.warning("research.synth.failed", errors=state["errors"])

    return state
