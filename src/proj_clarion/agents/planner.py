"""Plan agent — turns a CompanyProfile into a complete DemoPlan.

Six phases, each one an async function that mutates the PlanState TypedDict.
Each phase wraps its work in an OTel span (plan.analyze_profile, plan.model_processes,
…) so the meta-observability story shows the planner as a six-step graph in Tempo.

    analyze_profile        pick audience + which 4–7 business processes to model
    model_processes        BusinessProcessModels with stable snake_case service IDs
    build_kg               two-tier KnowledgeGraph (business entities + tech resources)
    script_incident        IncidentScript: WMS-bridge-shaped degradation by default
    propose_dashboards     DashboardSpec + AlertSpec lists
    propose_tools          AssistantTool SQL-view specs

The agent intentionally calls Claude six times (one per phase). One mega-prompt
would be cheaper but harder to retry-piecewise and harder to evaluate.
"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TypedDict
from uuid import uuid4

import structlog
from anthropic import Anthropic
from opentelemetry import trace
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from proj_clarion.observability.sigil_helper import call_anthropic
from proj_clarion.schemas import (
    AlertSpec,
    AssistantTool,
    BusinessProcessModel,
    BusinessStep,
    CompanyProfile,
    CostEnvelope,
    DashboardSpec,
    DataBlueprint,
    DemoPlan,
    EdgeType,
    EventType,
    IncidentEvent,
    IncidentScript,
    InfrastructureBlueprint,
    KGEdge,
    KGNode,
    KnowledgeGraph,
    NodeType,
    ReviewState,
    TargetAudience,
)

_logger = structlog.get_logger()
_tracer = trace.get_tracer("proj-clarion.planner")


# ============================================================
# State
# ============================================================

class ProcessChoice(BaseModel):
    """Intermediate: phase 1's pick before phase 2 fleshes it out."""

    model_config = ConfigDict(extra="forbid")

    process_id: str = Field(..., pattern=r"^proc-[a-z0-9-]+$")
    name: str
    description: str
    rationale: str


class PlanState(TypedDict, total=False):
    profile: CompanyProfile
    audience: TargetAudience
    peak_event: str  # vertical-specific stress label (e.g. "Black Friday surge")
    chosen_processes: list[ProcessChoice]
    business_processes: list[BusinessProcessModel]
    knowledge_graph: KnowledgeGraph
    incident_script: IncidentScript
    dashboard_specs: list[DashboardSpec]
    alert_specs: list[AlertSpec]
    assistant_tools: list[AssistantTool]
    plan: DemoPlan | None
    errors: list[str]
    started_at: float
    sigil_conversation_id: str
    gen_id_analyze: str
    gen_ids_processes: list[str]
    gen_id_build_kg: str
    gen_id_incident: str
    gen_id_dashboards: str
    gen_id_tools: str
    # SE-supplied override for DataBlueprint.business_event_volume_per_day.
    # When None, the planner auto-scales by channel count (capped at 5K).
    volume_per_day: int


# ============================================================
# LLM helpers
# ============================================================

def _client() -> Anthropic:
    return Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])


def _model() -> str:
    return os.getenv("ANTHROPIC_MODEL", "claude-opus-4-7")


def _strip_fences(text: str) -> str:
    """Remove markdown code fences if Claude wraps output despite being told not to."""
    s = text.strip()
    if s.startswith("```"):
        s = s.split("```", 2)[1]
        if s.startswith("json"):
            s = s[4:]
        s = s.strip("` \n")
    return s


# Trailing-comma after the last element of an object/array. The most
# common LLM JSON glitch in our failure logs. Conservative — only fires
# when the comma is *immediately* before `}` or `]` (allowing whitespace).
# False positives are theoretically possible inside string values that
# happen to end with `, }`, but we have not seen this in practice.
_TRAILING_COMMA = re.compile(r",(\s*[}\]])")


def _repair_llm_json(text: str) -> str:
    """Best-effort fixups for the JSON glitches we've seen LLMs emit.

    Two passes, both safe-by-construction:
      1. Strip markdown fences if Claude wrapped output despite being told not to.
      2. Trim any trailing prose after the final `}` / `]` (e.g. "Hope this helps!").
      3. Remove trailing commas before `}` / `]`.

    We deliberately skip JSON5-style line/block comments — stripping them
    is risky because `//` appears inside URL strings and `/* */` could
    appear inside regex strings. If we ever see those in a real failure
    dump we'll add targeted handling.
    """
    s = _strip_fences(text)
    last_close = max(s.rfind("}"), s.rfind("]"))
    if 0 <= last_close < len(s) - 1:
        tail = s[last_close + 1:].strip()
        if tail and tail[0] not in ",}]":
            s = s[: last_close + 1]
    s = _TRAILING_COMMA.sub(r"\1", s)
    return s


def _dump_failed_response(agent_name: str, raw: str) -> Path:
    """Write the LLM's raw output to disk so the operator can inspect
    what actually came back when JSON parsing fails. Returns the path
    even if the write fails (we surface it in the error message either
    way) — but we tolerate write errors so a tmp-fs hiccup doesn't mask
    the real upstream error."""
    safe_name = agent_name.replace(".", "_").replace("[", "_").replace("]", "")
    ts = int(time.time())
    p = Path(f"/tmp/clarion-llm-failure-{safe_name}-{ts}.txt")
    try:
        p.write_text(raw)
    except OSError:
        pass
    return p


def _llm_json(
    system: str,
    user: str,
    *,
    agent_name: str,
    parent_generation_ids: list[str] | None = None,
    conversation_id: str = "",
    max_tokens: int = 8192,
) -> tuple[Any, str]:
    """Single Claude call → (parsed JSON, sigil generation_id).

    The generation_id is empty string when Sigil is not configured.

    Strict parse first, then a tolerant repair pass that handles the
    LLM-glitch patterns we've actually seen in production (trailing
    commas, trailing prose after the closing brace, // comments). If
    both fail, dump the raw response to /tmp so operators have evidence,
    and re-raise a JSONDecodeError enriched with a window around the
    failure point — `Expecting value: line 240 column 93 (char 35606)`
    is useless without seeing the surrounding 400 chars.
    """
    request: dict[str, Any] = {
        "model": _model(),
        "max_tokens": max_tokens,
        "system": system,
        "messages": [{"role": "user", "content": user}],
    }
    response, gen_id = call_anthropic(
        _client(),
        request,
        agent_name=agent_name,
        parent_generation_ids=parent_generation_ids,
        conversation_id=conversation_id,
        tags={"clarion.component": "planner", "clarion.phase": agent_name.split(".")[-1]},
    )
    raw = "".join(b.text for b in response.content if b.type == "text")

    try:
        return json.loads(_strip_fences(raw)), gen_id
    except json.JSONDecodeError as strict_err:
        try:
            repaired = _repair_llm_json(raw)
            data = json.loads(repaired)
            _logger.warning(
                "llm_json.repaired",
                agent=agent_name,
                strict_error=strict_err.msg,
                strict_pos=strict_err.pos,
                chars_before=len(raw),
                chars_after=len(repaired),
            )
            return data, gen_id
        except json.JSONDecodeError as repair_err:
            dump = _dump_failed_response(agent_name, raw)
            pos = repair_err.pos
            window = raw[max(0, pos - 200): pos + 200]
            _logger.error(
                "llm_json.unparseable",
                agent=agent_name,
                error=repair_err.msg,
                pos=pos,
                dump_path=str(dump),
            )
            raise json.JSONDecodeError(
                f"{repair_err.msg} | raw saved to {dump} | "
                f"window around char {pos}: …{window}…",
                repair_err.doc,
                repair_err.pos,
            ) from repair_err


def _slug(s: str) -> str:
    """Stable snake-case slug for IDs derived from names."""
    s = s.lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-")[:48]


_NODE_FIELDS = {
    "node_id", "node_type", "label", "attributes", "live_state_binding",
    "business_subtype", "technical_subtype", "agentic_subtype",
}
_EDGE_FIELDS = {"edge_id", "edge_type", "from_node_id", "to_node_id", "attributes"}

# Mirror the Literal enums in schemas/knowledge_graph.py::KGNode. Anything
# the LLM emits outside these gets coerced to a sensible catch-all so we
# never drop the whole plan because the LLM picked one out-of-vocab subtype
# for a vertical we hadn't pre-thought of (e.g. ITC emitting 'plant' for a
# manufacturing facility — the schema's 'business_unit' is the right home).
_VALID_BUSINESS_SUBTYPES = {
    "store", "region", "channel", "product_line", "fulfillment_center",
    "business_unit", "brand", "partner_program",
}
_VALID_TECHNICAL_SUBTYPES = {
    "cluster", "namespace", "service", "deployment", "database", "queue",
    "external_dependency",
}
_VALID_AGENTIC_SUBTYPES = {"agent", "tool", "model", "vector_index"}

# Catch-all targets when the LLM picks out-of-vocab values. Picked so the
# downstream KG model rules still produce something sensible — see
# kg_publish/model_rules.py for the type-name mapping.
_BUSINESS_SUBTYPE_FALLBACK = "business_unit"
_TECHNICAL_SUBTYPE_FALLBACK = "external_dependency"
_AGENTIC_SUBTYPE_FALLBACK = "tool"


# NOTE: previous versions of this file had hardcoded Python tables
# (_RETAIL_LIKE_BUSINESS_MODELS, _RETAIL_ONLY_BUSINESS_SUBTYPES) that
# rewrote LLM-emitted subtypes post-hoc — e.g. forcing `store` to
# `business_unit` for non-retail business models. That logic is now
# REMOVED. The plan's content (what the planner LLM actually emits) is
# canonical. Prompt steering does the vertical-fit work dynamically:
#   - BUILD_KG_SYSTEM contains the HARD RULE clause naming retail-only
#     subtypes
#   - `_vertical_kg_guidance(business_model)` is injected into the user
#     message with APPROPRIATE/AVOID lists per vertical
#   - Pydantic Literal validation rejects truly invalid enum values
# If the LLM ignores the prompt and produces wrong subtypes, the SE sees
# it on the EntityTypesPanel and re-tests the Plan phase rather than
# Python silently rewriting their plan.
_INCIDENT_EVENT_FIELDS = {
    "event_id", "offset_seconds", "target_kind", "target_id", "event_type",
    "magnitude", "recovery_offset_seconds", "expected_alert_id", "narrator_cue",
}
_INCIDENT_FIELDS = {
    "script_id", "title", "total_duration_minutes", "arming_mode", "events",
}
_BUSINESS_PROCESS_FIELDS = {
    "process_id", "name", "description", "business_steps", "kpis", "failure_modes",
}
_BUSINESS_STEP_FIELDS = {"step_id", "name", "kpi", "services_implementing"}
_FAILURE_MODE_FIELDS = {"name", "description", "affects_steps"}


def _sanitize_business_process_payload(data: Any) -> Any:
    """Belt-and-braces fixups for BusinessProcessModel:
    - Promote `process_name` → `name` if Claude regressed (seen on ITC run)
    - Promote `step_name` → `name` inside business_steps
    - Drop unknown top-level/nested fields (schema is extra=forbid)

    The schema's `extra=forbid` means a single stray field aborts the whole
    process model, which then cascades to no KG and no plan. We'd rather
    drop the stray field and keep going.
    """
    if not isinstance(data, dict):
        return data
    if "process_name" in data and "name" not in data:
        data["name"] = data.pop("process_name")
    steps = data.get("business_steps") or []
    new_steps = []
    for s in steps:
        if not isinstance(s, dict):
            continue
        if "step_name" in s and "name" not in s:
            s["name"] = s.pop("step_name")
        new_steps.append({k: v for k, v in s.items() if k in _BUSINESS_STEP_FIELDS})
    data["business_steps"] = new_steps
    fms = data.get("failure_modes") or []
    new_fms = []
    for fm in fms:
        if not isinstance(fm, dict):
            continue
        new_fms.append({k: v for k, v in fm.items() if k in _FAILURE_MODE_FIELDS})
    data["failure_modes"] = new_fms
    return {k: v for k, v in data.items() if k in _BUSINESS_PROCESS_FIELDS}


def _sanitize_incident_payload(data: Any) -> Any:
    """Belt-and-braces fixups for IncidentScript:
    - Promote `description` → `narrator_cue` if Claude regressed
    - Drop unknown fields on events and the script itself
    - Default missing target_kind to 'service' (most common)
    """
    if not isinstance(data, dict):
        return data
    events = data.get("events") or []
    new_events = []
    for ev in events:
        if not isinstance(ev, dict):
            continue
        if "narrator_cue" not in ev and "description" in ev:
            ev["narrator_cue"] = ev.pop("description")
        ev.setdefault("target_kind", "service")
        new_events.append({k: v for k, v in ev.items() if k in _INCIDENT_EVENT_FIELDS})
    data["events"] = new_events
    return {k: v for k, v in data.items() if k in _INCIDENT_FIELDS}


def _coerce_subtype(node: dict[str, Any]) -> None:
    """Force each subtype field onto its schema's Literal vocabulary.

    The LLM happily invents subtypes when a vertical doesn't fit our
    catalog (ITC: 'plant'; healthcare: 'clinic'; logistics: 'depot').
    Without coercion the whole KG validation aborts with `Input should
    be 'store', 'region', ...` and the plan dies. Coercing to the
    fallback keeps the demo viable; we log the mapping so we know which
    out-of-vocab values to upgrade to first-class enum members later.
    """
    for field, valid, fallback in (
        ("business_subtype", _VALID_BUSINESS_SUBTYPES, _BUSINESS_SUBTYPE_FALLBACK),
        ("technical_subtype", _VALID_TECHNICAL_SUBTYPES, _TECHNICAL_SUBTYPE_FALLBACK),
        ("agentic_subtype", _VALID_AGENTIC_SUBTYPES, _AGENTIC_SUBTYPE_FALLBACK),
    ):
        v = node.get(field)
        if v is None or v in valid:
            continue
        # Stash the original so model_rules / dashboards / debugging can
        # still see what the LLM intended.
        attrs = node.setdefault("attributes", {})
        if isinstance(attrs, dict):
            attrs.setdefault("original_subtype", v)
        _logger.warning(
            "kg_payload.subtype_coerced",
            field=field, original=v, coerced_to=fallback,
            node_id=node.get("node_id"),
        )
        node[field] = fallback


def _sanitize_kg_payload(data: Any) -> Any:
    """Belt-and-braces fixups before KG validation:
    - rename `name` → `label` if Claude regressed
    - drop unknown fields (extra="forbid" would otherwise reject)
    - default `attributes` to {} when missing
    - coerce out-of-vocab subtypes to schema-valid fallbacks (see
      `_coerce_subtype`) so one rogue enum doesn't sink the whole plan.
    """
    if not isinstance(data, dict):
        return data
    nodes = data.get("nodes") or []
    edges = data.get("edges") or []
    new_nodes = []
    for n in nodes:
        if not isinstance(n, dict):
            continue
        if "label" not in n and "name" in n:
            n["label"] = n.pop("name")
        n.setdefault("attributes", {})
        _coerce_subtype(n)
        new_nodes.append({k: v for k, v in n.items() if k in _NODE_FIELDS})
    new_edges = []
    for e in edges:
        if not isinstance(e, dict):
            continue
        e.setdefault("attributes", {})
        new_edges.append({k: v for k, v in e.items() if k in _EDGE_FIELDS})
    data["nodes"] = new_nodes
    data["edges"] = new_edges
    return data


# ============================================================
# Phase 1 — analyze_profile
# ============================================================

ANALYZE_SYSTEM = """You analyze a CompanyProfile to set up a focused, vertical-aware
observability demo. The demo simulates an SE walking a prospect through a
Grafana Cloud experience that reflects THEIR business — not a generic retail
template.

Your three outputs:
1. The default target audience: "business" (CFO/COO), "technical"
   (SRE/platform), or "pivot" (opens business-side, pivots to technical).
   Pick "pivot" unless the profile gives strong reason to stay in one lane.
2. Four to seven business processes to model — drawn from the profile's
   `business_entity_candidates`, `channels`, and `business_model`. Choose
   processes that VERTICAL-FIT the company:
     - Retail / B2C / omnichannel: order capture, store ops, fulfillment,
       returns, loyalty, peak-event readiness (Black Friday, holiday, drops)
     - Healthcare / payer / provider: appointment intake, claims processing,
       prior auth, prescription fulfillment, patient outreach
     - SaaS B2B: signup/activation, billing, API health, customer onboarding,
       feature rollout, churn-risk monitoring
     - Logistics / supply chain: shipment routing, hand-off integrity, last
       mile, capacity utilization, exception handling
     - Financial services: transaction processing, fraud screening,
       compliance reporting, customer authentication
     - Manufacturing: production line throughput, QA gating, supplier
       integration, plant utilization
   Each pick is a slice of the company's day-to-day operations whose health
   an executive would care about. Each gets a stable snake-case process_id.
3. The vertical's "stress event" that an SE would showcase as the worst-case
   demo (you'll output this in `peak_event`):
     - Retail: "Black Friday surge", "Holiday flash sale", "Product drop"
     - Healthcare: "Open enrollment surge", "Flu-season prescription burst"
     - SaaS B2B: "Quarter-end billing run", "Marketing-driven signup spike"
     - Logistics: "Peak holiday shipping volume"
     - Manufacturing: "Production line ramp-up"
     - FinServ: "Trading day open" or "Tax filing deadline"

Hard rules:
- Output JSON: {"audience": "...", "peak_event": "<short label>",
  "processes": [{"process_id": "proc-...", "name": "...", "description": "...",
  "rationale": "..."}]}
- process_id must match ^proc-[a-z0-9-]+$
- Pick processes that overlap with the company's actual channels and entity
  candidates — do not invent business lines the company isn't in
- 4 minimum, 7 maximum
- The peak_event must be specific to this company's vertical, not generic
- No markdown fences, no prose outside the JSON
"""


async def analyze_profile(state: PlanState) -> PlanState:
    with _tracer.start_as_current_span("plan.analyze_profile") as span:
        profile = state["profile"]
        span.set_attribute("plan.profile_id", profile.profile_id)

        user = (
            f"=== Company ===\n{profile.company.name} "
            f"({profile.industry_taxonomy.primary_industry}, "
            f"{profile.industry_taxonomy.business_model.value})\n\n"
            f"=== Channels ===\n"
            + "\n".join(f"- {c.channel_type}: {c.name} — {c.description}" for c in profile.channels)
            + "\n\n=== Business entity candidates ===\n"
            + "\n".join(
                f"- {e.entity_type}: {e.name}"
                + (f" — {e.description}" if e.description else "")
                for e in profile.business_entity_candidates
            )
            + "\n\n=== Strategic priorities ===\n"
            + "\n".join(f"- {p.priority}" for p in profile.recent_strategic_priorities[:5])
            + "\n\n=== Pain signals ===\n"
            + "\n".join(f"- ({p.severity}) {p.pain}" for p in profile.pain_signals[:5])
            + "\n\nProduce the JSON object."
        )

        try:
            data, gen_id = _llm_json(
                ANALYZE_SYSTEM, user,
                agent_name="clarion.planner.analyze_profile",
                conversation_id=state.get("sigil_conversation_id", ""),
                max_tokens=2048,
            )
            audience = TargetAudience(data.get("audience", "pivot"))
            processes_raw = data.get("processes", [])
            processes = [ProcessChoice.model_validate(p) for p in processes_raw]
            if not 4 <= len(processes) <= 7:
                raise ValueError(f"got {len(processes)} processes; need 4-7")
            state["audience"] = audience
            state["peak_event"] = str(data.get("peak_event", "")).strip()
            state["chosen_processes"] = processes
            state["gen_id_analyze"] = gen_id
            span.set_attribute("plan.audience", audience.value)
            span.set_attribute("plan.process_count", len(processes))
            if state["peak_event"]:
                span.set_attribute("plan.peak_event", state["peak_event"])
            if gen_id:
                span.set_attribute("sigil.generation.id", gen_id)
        except (ValidationError, ValueError, KeyError) as exc:
            state.setdefault("errors", []).append(f"analyze_profile: {exc}")
            span.record_exception(exc)
            span.set_status(trace.Status(trace.StatusCode.ERROR, str(exc)))
        return state


# ============================================================
# Phase 2 — model_processes
# ============================================================

MODEL_PROCESSES_SYSTEM = """You model a single business process for a demo.

Input: one process pick (id, name, description) plus the source CompanyProfile.

Output a BusinessProcessModel JSON object:
- business_steps: 4–8 ordered steps, each with step_id (^step-[a-z0-9-]+$),
  name, kpi (single sentence), and services_implementing (list of service IDs
  YOU choose, like "svc-checkout", "svc-wms-bridge"). Service IDs MUST be
  stable snake-case starting with `svc-` and use only the same set across all
  steps that share the service. Reuse IDs across steps where the same service
  is responsible.
- kpis: 3–5 process-level KPIs (strings)
- failure_modes: 2–3 named failure modes, each with a description and
  affects_steps (list of step_ids it can break)

Hard rules:
- Output JSON only, no prose, no fences
- step_id pattern: ^step-[a-z0-9-]+$
- Service IDs you create here are the ONLY service IDs that may appear in the
  later knowledge_graph and incident_script. Treat them as load-bearing.
"""


async def _model_one_process(
    pick: ProcessChoice,
    profile: CompanyProfile,
    *,
    parent_generation_ids: list[str],
    conversation_id: str,
) -> tuple[BusinessProcessModel, str]:
    user = (
        f"=== Process ===\n"
        f"id: {pick.process_id}\n"
        f"name: {pick.name}\n"
        f"description: {pick.description}\n"
        f"rationale: {pick.rationale}\n\n"
        f"=== Company ===\n"
        f"{profile.company.name} ({profile.industry_taxonomy.primary_industry}, "
        f"{profile.industry_taxonomy.business_model.value})\n\n"
        f"=== Tech stack signals ===\n"
        + "\n".join(
            f"- {t.component_type}: {t.vendor_or_product} ({t.confidence.value})"
            for t in profile.tech_stack_signals
        )
        + "\n\nProduce the BusinessProcessModel JSON object for this process."
    )
    data, gen_id = _llm_json(
        MODEL_PROCESSES_SYSTEM, user,
        agent_name=f"clarion.planner.model_processes[{pick.process_id}]",
        parent_generation_ids=parent_generation_ids,
        conversation_id=conversation_id,
        max_tokens=4096,
    )
    data = _sanitize_business_process_payload(data)
    data["process_id"] = pick.process_id  # always pin to phase-1 pick
    data["name"] = pick.name
    data["description"] = pick.description
    return BusinessProcessModel.model_validate(data), gen_id


async def model_processes(state: PlanState) -> PlanState:
    with _tracer.start_as_current_span("plan.model_processes") as span:
        picks = state.get("chosen_processes", [])
        span.set_attribute("plan.process_count", len(picks))
        models: list[BusinessProcessModel] = []
        process_gen_ids: list[str] = []
        errors = state.setdefault("errors", [])
        analyze_gen = state.get("gen_id_analyze", "")
        parents = [analyze_gen] if analyze_gen else []
        conversation_id = state.get("sigil_conversation_id", "")
        for pick in picks:
            try:
                with _tracer.start_as_current_span("plan.model_processes.one") as cs:
                    cs.set_attribute("plan.process_id", pick.process_id)
                    bpm, gen_id = await _model_one_process(
                        pick, state["profile"],
                        parent_generation_ids=parents,
                        conversation_id=conversation_id,
                    )
                    models.append(bpm)
                    if gen_id:
                        process_gen_ids.append(gen_id)
                        cs.set_attribute("sigil.generation.id", gen_id)
            except (ValidationError, ValueError, json.JSONDecodeError) as exc:
                errors.append(f"model_processes[{pick.process_id}]: {exc}")
                span.record_exception(exc)
        state["business_processes"] = models
        state["gen_ids_processes"] = process_gen_ids
        span.set_attribute("plan.process_models_built", len(models))
        return state


# ============================================================
# Phase 3 — build_kg
# ============================================================

BUILD_KG_SYSTEM = """You build a two-tier KnowledgeGraph for a demo. The
output must be VERTICAL-FIT — do not project retail terminology onto a
company that doesn't operate stores.

Inputs:
- The CompanyProfile (channels, geo footprint, business entity candidates,
  business_model)
- A list of BusinessProcessModels (each one already names the service IDs
  it depends on under business_steps[].services_implementing)
- Vertical guidance section telling you which business_subtype values
  are appropriate for THIS company's vertical

You must produce:
- Top tier (business_entity nodes): a hierarchy that reflects how the
  company actually operates, drawn from the profile's channels +
  business_entity_candidates + the vertical guidance. Use business_subtype
  values from this fixed enum: store, region, channel, product_line,
  fulfillment_center, business_unit, brand, partner_program.

  ⛔ HARD RULE — vertical-fit subtypes are MANDATORY:
    • `store` and `fulfillment_center` are ALLOWED if and only if
      business_model is "b2c_retail" OR "omnichannel_retail".
    • For EVERY other business_model — including the catch-all "other" —
      you MUST NOT emit business_subtype="store" or "fulfillment_center".
      Use `business_unit` (or `channel` / `brand` / `region` /
      `partner_program` / `product_line` as appropriate) instead.
    • Concrete swaps for non-retail verticals:
        - Airlines: hubs / stations → business_unit
        - Banks: branches → business_unit
        - Manufacturers: plants / facilities → business_unit
        - Hospitals: clinics / sites → business_unit
        - SaaS: tenants / regions of presence → business_unit
        - Logistics: depots / terminals → business_unit
    • Output that violates this rule WILL be auto-coerced post-hoc, so
      you save tokens by getting it right the first time.
- Bottom tier (technical_resource nodes): one cluster, one or two namespaces,
  ALL service IDs that appeared in the BusinessProcessModels (one node each),
  any databases/queues/external dependencies the services need. Use
  technical_subtype.
- Edges: every service → cluster (`runs_on`); business_entity → service
  (`serves`, top-tier endpoint to its primary service); service → database/queue
  (`depends_on`); service → external_dependency (`integrates_with`); region →
  child (`contains`); channel → child (`contains`).

Output JSON shape (literal field names — do NOT rename anything):
{
  "nodes": [
    {
      "node_id": "region-na",
      "node_type": "business_entity",
      "business_subtype": "region",
      "label": "North America",
      "attributes": {}
    },
    {
      "node_id": "svc-checkout",
      "node_type": "technical_resource",
      "technical_subtype": "service",
      "label": "checkout-svc",
      "attributes": {}
    }
  ],
  "edges": [
    {
      "edge_id": "edge-001",
      "edge_type": "runs_on",
      "from_node_id": "svc-checkout",
      "to_node_id": "cluster-prod-us",
      "attributes": {}
    }
  ]
}

Hard rules — every one is enforced:
- The field is `label`, not `name`. Use `label` for the human-readable name.
- Every node has exactly: node_id, node_type, ONE subtype field
  (business_subtype OR technical_subtype OR agentic_subtype, matching node_type),
  label, optionally attributes. NO `name`, NO other fields.
- Every edge has exactly: edge_id, edge_type, from_node_id, to_node_id,
  optionally attributes. NO other fields.
- Every node_id used in edges MUST appear in the nodes list above
- node_id pattern: ^[a-z0-9][a-z0-9_-]*$
- edge_id pattern: ^edge-[a-z0-9-]+$
- Service IDs in technical_resource nodes MUST exactly match the IDs from
  BusinessProcessModels — do not rename them
- node_type must be one of: business_entity, technical_resource, agentic_resource
- edge_type must be one of: runs_on, depends_on, integrates_with, serves, contains
- No markdown fences, no prose, no comments
"""


def _vertical_kg_guidance(business_model: str) -> str:
    """Return a short, prescriptive subtype-selection guide for the planner
    LLM, parameterized by business_model.

    The LLM has been observed projecting retail terminology — specifically
    `business_subtype="store"` — onto airlines, manufacturers, healthcare
    providers, and SaaS companies, none of which actually operate retail
    stores. BlueSky Airlines is the canonical example: airports, routes,
    cargo are the natural entities; stores are nonsensical.

    Each guidance string lists APPROPRIATE subtypes + AVOID subtypes + a
    concrete example shaping. Subtypes are limited to the schema's
    Literal enum (store, region, channel, product_line, fulfillment_center,
    business_unit, brand, partner_program) — guidance just steers
    selection within that enum, not adds new ones.
    """
    g = {
        "b2c_retail": (
            "Retail / e-commerce. APPROPRIATE: store, region, channel, brand, "
            "product_line, fulfillment_center.\n"
            "Use store for physical locations or per-location operations. "
            "Use channel for D2C web, retail, wholesale. Use region to group "
            "stores geographically. Brand for sub-brands or product collections."
        ),
        "omnichannel_retail": (
            "Omnichannel retail. APPROPRIATE: store, region, channel, brand, "
            "product_line, fulfillment_center.\n"
            "Channels typically include retail-store, D2C-web, mobile-app, "
            "marketplace, wholesale. DCs as fulfillment_center."
        ),
        "b2c_digital": (
            "Digital-only consumer business. APPROPRIATE: channel, region, "
            "brand, product_line, partner_program.\n"
            "AVOID: store, fulfillment_center — there are no physical "
            "locations. Channels are web, mobile-app, social, partner."
        ),
        "b2b_saas": (
            "B2B SaaS. APPROPRIATE: channel, region, product_line, "
            "business_unit, partner_program.\n"
            "AVOID: store, fulfillment_center — SaaS doesn't have physical "
            "locations. Use channel for go-to-market motions (self-serve, "
            "sales-led, partner). business_unit for product divisions."
        ),
        "b2b_direct": (
            "B2B direct-sales. APPROPRIATE: channel, region, business_unit, "
            "partner_program, product_line.\n"
            "AVOID: store. Channels are direct-sales, partner, online-quote. "
            "Use region for sales territories."
        ),
        "marketplace_multi_sided": (
            "Two-sided marketplace. APPROPRIATE: channel, region, "
            "business_unit, partner_program, brand.\n"
            "AVOID: store. Channels separate buyer-side and seller-side; "
            "partner_program for category partnerships."
        ),
        "manufacturing": (
            "Industrial manufacturer. APPROPRIATE: business_unit, brand, "
            "region, product_line, channel, partner_program.\n"
            "AVOID: store, fulfillment_center — manufacturers operate "
            "plants/facilities, not retail stores. Use business_unit for "
            "operating divisions (e.g. ITC's Generic-A, Generic-C, Frick); brand for "
            "product brands; channel for OEM, dealer, direct."
        ),
        "logistics": (
            "Logistics / transportation / airline. APPROPRIATE: business_unit, "
            "region, channel, brand, partner_program.\n"
            "AVOID: store, fulfillment_center — airlines operate airports / "
            "hubs / routes, freight operates terminals; none are 'stores'. "
            "Use business_unit for divisions (e.g. an airline's passenger / "
            "cargo / loyalty); channel for booking surfaces (web, mobile, "
            "GDS, agent); partner_program for alliances and OTAs."
        ),
        "financial_services": (
            "Banking / wealth / insurance. APPROPRIATE: business_unit, "
            "region, channel, product_line, partner_program.\n"
            "AVOID: store, fulfillment_center — branches are NOT 'stores'. "
            "Use business_unit for retail-banking / commercial / wealth / "
            "insurance lines; channel for digital, branch, advisor, ATM; "
            "product_line for card, loan, mortgage, account products."
        ),
        "healthcare": (
            "Healthcare provider or payer. APPROPRIATE: business_unit, "
            "region, channel, product_line, partner_program.\n"
            "AVOID: store, fulfillment_center — clinics, hospitals, and "
            "pharmacies are NOT 'stores'. Use business_unit for service "
            "lines (claims, prescriptions, member services, prior-auth); "
            "channel for member-portal, provider-portal, call-center, mobile; "
            "product_line for plan types (HMO, PPO, Medicare)."
        ),
        "media_content": (
            "Media / content / streaming. APPROPRIATE: channel, region, "
            "brand, product_line, partner_program.\n"
            "AVOID: store, fulfillment_center. Channels are streaming-app, "
            "web, ads-platform, distribution-partner. Brand for sub-properties; "
            "product_line for tier/plan."
        ),
        "other": (
            "Vertical not explicitly listed. APPROPRIATE: region, channel, "
            "business_unit, product_line, brand, partner_program.\n"
            "AVOID store + fulfillment_center unless the company demonstrably "
            "operates retail-style locations. Use the profile's "
            "business_entity_candidates as the source of truth for naming."
        ),
    }
    return g.get(business_model, g["other"])


async def build_kg(state: PlanState) -> PlanState:
    with _tracer.start_as_current_span("plan.build_kg") as span:
        profile = state["profile"]
        bpm = state.get("business_processes", [])
        # Collect all service IDs the processes referenced — these MUST be in the KG
        required_service_ids: set[str] = set()
        for p in bpm:
            for step in p.business_steps:
                required_service_ids.update(step.services_implementing)

        bm = profile.industry_taxonomy.business_model.value
        guidance = _vertical_kg_guidance(bm)
        user = (
            f"=== Company ===\n{profile.company.name}\n"
            f"business_model: {bm}\n"
            f"primary_industry: {profile.industry_taxonomy.primary_industry}\n\n"
            f"=== Vertical guidance (REQUIRED — pick subtypes from this list) ===\n"
            f"{guidance}\n\n"
            f"=== Geographic footprint ===\n"
            f"countries: {profile.geographic_footprint.countries}\n"
            f"regions: {profile.geographic_footprint.regions}\n"
            f"flagship_locations: {profile.geographic_footprint.flagship_locations}\n\n"
            f"=== Channels ===\n"
            + "\n".join(f"- channel_id={c.channel_id} type={c.channel_type} name={c.name}"
                       for c in profile.channels)
            + "\n\n=== Business entity candidates (use these names, not invented ones) ===\n"
            + "\n".join(f"- {e.entity_type}: {e.name}"
                       for e in profile.business_entity_candidates)
            + "\n\n=== Required service IDs (MUST appear as technical_resource nodes) ===\n"
            + "\n".join(f"- {s}" for s in sorted(required_service_ids))
            + "\n\n=== Business processes for context ===\n"
            + "\n".join(f"- {p.name} ({p.process_id})" for p in bpm)
            + "\n\nProduce the KnowledgeGraph JSON object."
        )

        errors = state.setdefault("errors", [])
        analyze_gen = state.get("gen_id_analyze", "")
        process_gens = state.get("gen_ids_processes", [])
        parents = [g for g in [analyze_gen, *process_gens] if g]
        try:
            # max_tokens lands on 20000 by trial: 16000 truncates ITC/<ERP-vendor>/
            # Grafana mid-edge-list (~10K tokens of output JSON), and
            # 32000 trips Anthropic SDK's non-streaming guard rail
            # (`ValueError: Streaming is required for operations that may
            # take longer than 10 minutes`). Opus 4.7's threshold is
            # 21,333 tokens — anything over needs `client.messages.stream()`.
            # 20000 sits safely below that and gives ~10K headroom over
            # the JSON sizes we've seen. If a future vertical truncates
            # at 20K, the right move is converting _llm_json to streaming
            # rather than pushing the ceiling.
            data, gen_id = _llm_json(
                BUILD_KG_SYSTEM, user,
                agent_name="clarion.planner.build_kg",
                parent_generation_ids=parents,
                conversation_id=state.get("sigil_conversation_id", ""),
                max_tokens=20000,
            )
            data = _sanitize_kg_payload(data)
            kg = KnowledgeGraph.model_validate(data)
            state["gen_id_build_kg"] = gen_id
            if gen_id:
                span.set_attribute("sigil.generation.id", gen_id)
        except (ValidationError, json.JSONDecodeError, ValueError) as exc:
            errors.append(f"build_kg: {exc}")
            span.record_exception(exc)
            return state

        # Insert any missing service nodes that the LLM forgot
        node_ids = {n.node_id for n in kg.nodes}
        for sid in required_service_ids:
            if sid not in node_ids:
                kg.nodes.append(
                    KGNode(
                        node_id=sid,
                        node_type=NodeType.TECHNICAL_RESOURCE,
                        technical_subtype="service",
                        label=sid,
                    )
                )

        # Validate referential integrity (per the brief — call it explicitly)
        ref_errors = kg.validate_referential_integrity()
        if ref_errors:
            errors.extend(f"build_kg.ref_integrity: {e}" for e in ref_errors)
            # Drop dangling edges so the plan can still validate; flag the issue
            valid_ids = {n.node_id for n in kg.nodes}
            kg.edges = [
                e for e in kg.edges
                if e.from_node_id in valid_ids and e.to_node_id in valid_ids
            ]

        state["knowledge_graph"] = kg
        span.set_attribute("plan.kg.node_count", len(kg.nodes))
        span.set_attribute("plan.kg.edge_count", len(kg.edges))
        return state


# ============================================================
# Phase 4 — script_incident
# ============================================================

SCRIPT_INCIDENT_SYSTEM = """You produce an IncidentScript for a 15-minute demo
that reflects the company's VERTICAL — not a generic SRE outage.

The shape: a vertical-specific "stress event" (the SE-supplied `peak_event`,
e.g. "Black Friday surge" for retail, "Claims storm" for healthcare,
"Quarter-end billing run" for SaaS) that simulates compounding, realistic
failures customers actually fear. Multiple events, escalating, with a
business-KPI dip visible at the top of the funnel, technical evidence
underneath, and a recovery within the demo window.

Vertical playbooks (use the one that matches the company's business_model):

- **Retail / B2C / Omnichannel** (Black Friday-shape):
    1. Traffic surge: throughput on customer-facing services jumps 3-5×
       (event_type=latency_spike on storefront/cdn, magnitude 2.5-4.0)
    2. Payment-gateway pressure: 4xx/5xx errors on payment service
       (event_type=error_burst on svc-payment-* or svc-checkout, magnitude 3-5)
    3. Inventory desync: queue back pressure on inventory bridge
       (event_type=queue_back_pressure on svc-inventory-* or svc-wms-bridge,
       magnitude 4-6)
    4. Top-line KPI drop: revenue/conversion falls visibly
       (event_type=business_kpi_drop on a flagship Store or D2C Channel,
       magnitude 2-3)
    5. Recovery: events resolve at T+11-13min so the 15m window has air

- **Healthcare** (Claims storm / Open enrollment):
    1. Surge in claims submissions (event_type=throughput_drop reversed —
       use magnitude 3+ on the claims-processing service to imply backlog)
    2. EHR sync failures (dependency_unavailable on the EHR integration svc)
    3. Prescription queue back pressure (queue_back_pressure on rx-bridge)
    4. Patient-facing KPI drop: appointments confirmed within SLA falls
       (business_kpi_drop on a Region or Channel)

- **SaaS B2B** (Quarter-end billing or signup spike):
    1. Auth surge: latency_spike on identity service
    2. API rate-limit hits: error_burst on api-gateway with magnitude 3-4
    3. Billing job backlog: queue_back_pressure on billing-svc
    4. Activation drop: business_kpi_drop on a Channel (e.g. trial signups)

- **Logistics / Supply Chain / Airline** (Peak shipping or schedule disruption):
    1. Routing or scheduling service overload (latency_spike on routing/dispatch svc)
    2. Carrier or partner integration errors (dependency_unavailable on edi/gds svc)
    3. Last-mile or hub capacity exhaustion (queue_back_pressure on dispatch-svc)
    4. On-time KPI drop: deliveries or flights below SLA (business_kpi_drop)

- **Manufacturing / Industrial** (Plant ramp / quality incident):
    1. Production line throughput drop (throughput_drop on line-control-svc)
    2. Supplier integration failures (dependency_unavailable on edi or supplier portal)
    3. QA gating backlog (queue_back_pressure on inspection-svc)
    4. Output KPI drop: units-per-hour or yield (business_kpi_drop on a BusinessUnit)

- **Financial Services** (Trading-day open or batch pressure):
    1. Auth/session service overload (latency_spike on identity-svc)
    2. Settlement or clearing dependency stall (dependency_unavailable on settlement-svc)
    3. Risk-engine queue back-pressure (queue_back_pressure on risk-svc)
    4. Customer KPI drop: transactions cleared within SLA (business_kpi_drop)

- **Healthcare** — see playbook above (claims storm / open enrollment).
- **SaaS B2B** — see playbook above (quarter-end billing).

If the company doesn't fit a playbook above, default to the canonical
"mid-stack degradation drags top-line KPI" arc:

- Event at T+4min: degradation of one mid-stack service (latency_spike or
  queue_back_pressure, magnitude 3.0-5.0)
- Event at T+5min: secondary KPI drop on a top-tier business_entity that's
  downstream of event 1 (business_kpi_drop, magnitude 2.0-3.0)
- Event at T+11min: recovery (throughput_drop with magnitude ~0.5)

Output JSON shape (literal field names — no extras, no renames; example
shows the SHAPE, not the content — your script_id/title/target_id values
must reflect THIS company's services, not the example below):
{
  "script_id": "scr-<vertical-fit-slug>",
  "title": "<short title naming the actual failing service for THIS vertical>",
  "total_duration_minutes": 15,
  "arming_mode": "historical_replay",
  "events": [
    {
      "event_id": "evt-001",
      "offset_seconds": 240,
      "target_kind": "service",
      "target_id": "<an actual service_id from the KG, NOT 'svc-wms-bridge'>",
      "event_type": "queue_back_pressure",
      "magnitude": 4.0,
      "recovery_offset_seconds": 660,
      "expected_alert_id": null,
      "narrator_cue": "Click into the failing service in Tempo"
    }
  ]
}

Hard rules — all enforced:
- Every event has exactly: event_id, offset_seconds, target_kind, target_id,
  event_type, magnitude, recovery_offset_seconds, narrator_cue. Optionally
  expected_alert_id. NO `description`, NO other fields.
- target_kind MUST be one of: service, business_entity, agent
- event_type MUST be one of: latency_spike, error_burst, throughput_drop,
  queue_back_pressure, dependency_unavailable, agent_hallucination,
  token_cost_spike, business_kpi_drop
- Every target_id MUST be a node_id from the supplied KG
- recovery_offset_seconds > offset_seconds
- recovery_offset_seconds <= total_duration_minutes * 60
- event_id pattern: ^evt-[a-z0-9-]+$
- magnitude in (0, 10]
- No fences, no prose
"""


async def script_incident(state: PlanState) -> PlanState:
    with _tracer.start_as_current_span("plan.script_incident") as span:
        kg = state.get("knowledge_graph")
        bpm = state.get("business_processes", [])
        if not kg:
            state.setdefault("errors", []).append("script_incident: no KG to target")
            return state

        services = [n for n in kg.nodes if n.node_type == NodeType.TECHNICAL_RESOURCE
                    and (n.technical_subtype == "service")]
        biz_entities = [n for n in kg.nodes if n.node_type == NodeType.BUSINESS_ENTITY]

        peak_event = state.get("peak_event", "") or "(none specified)"
        bm = state["profile"].industry_taxonomy.business_model.value
        company_name = state["profile"].company.name
        user = (
            f"=== Company ===\n{company_name} (business_model={bm})\n\n"
            f"=== Peak event the SE wants to demo ===\n{peak_event}\n\n"
            "=== Services in KG ===\n"
            + "\n".join(f"- {s.node_id} ({s.label})" for s in services)
            + "\n\n=== Business entities in KG ===\n"
            + "\n".join(f"- {b.node_id} ({b.label}, subtype={b.business_subtype})"
                       for b in biz_entities)
            + "\n\n=== Business process failure modes for context ===\n"
            + "\n".join(
                f"- {p.name}: " + "; ".join(f.name for f in p.failure_modes)
                for p in bpm
            )
            + "\n\nProduce the IncidentScript JSON object using the vertical "
              "playbook that matches this company's business_model. Aim for "
              "4-5 compounding events that realistically simulate the named "
              "peak_event, not a generic 'wms-bridge' outage."
        )

        errors = state.setdefault("errors", [])
        kg_gen = state.get("gen_id_build_kg", "")
        process_gens = state.get("gen_ids_processes", [])
        parents = [g for g in [kg_gen, *process_gens] if g]
        try:
            data, gen_id = _llm_json(
                SCRIPT_INCIDENT_SYSTEM, user,
                agent_name="clarion.planner.script_incident",
                parent_generation_ids=parents,
                conversation_id=state.get("sigil_conversation_id", ""),
                max_tokens=2048,
            )
            data = _sanitize_incident_payload(data)
            script = IncidentScript.model_validate(data)
            state["gen_id_incident"] = gen_id
            if gen_id:
                span.set_attribute("sigil.generation.id", gen_id)
        except (ValidationError, json.JSONDecodeError, ValueError) as exc:
            errors.append(f"script_incident: {exc}")
            span.record_exception(exc)
            return state

        # Belt-and-braces: drop any event whose target_id isn't actually in the KG
        valid_ids = {n.node_id for n in kg.nodes}
        bad = [e for e in script.events if e.target_id not in valid_ids]
        if bad:
            for e in bad:
                errors.append(
                    f"script_incident.bad_target: event {e.event_id} target {e.target_id}"
                )
            script = IncidentScript.model_validate({
                **json.loads(script.model_dump_json()),
                "events": [json.loads(e.model_dump_json())
                           for e in script.events if e.target_id in valid_ids],
            })

        state["incident_script"] = script
        span.set_attribute("plan.incident.event_count", len(script.events))
        return state


# ============================================================
# Phase 5 — propose_dashboards_and_alerts
# ============================================================

DASHBOARDS_SYSTEM = """You design the dashboards and alerts for a Grafana Cloud demo.

Output JSON: {"dashboards": [...], "alerts": [...]}

Dashboards (at least one of each):
- A "Business Health" dashboard, audience=business, showing KPIs from
  the business processes. KPI selection MUST be vertical-fit. The
  vertical guidance section in the user message tells you which
  business KPIs are appropriate. Do NOT default to retail KPIs
  (revenue / conversion / fulfillment SLA) for non-retail verticals.
- A "Technical Health" dashboard, audience=technical, showing service-level
  signals (latency, error rate, queue depth) for the services in the KG.
  These are universal — same shape for every vertical.
- A "Pivot" dashboard, audience=pivot, that bridges the two — a business
  KPI alongside the service traces that explain its dip.

Vertical-fit business KPIs (use these as the primary panel titles
for the Business Health dashboard, picking 3-5 that match the company):
- b2c_retail / omnichannel_retail: Revenue, Orders, Conversion Rate,
  Fulfillment SLA, Cart Abandonment, Active Stores
- b2c_digital: MAU/DAU, Session Duration, Conversion, Subscription Revenue
- b2b_saas: MRR, ARR, Active Tenants, Trial-to-Paid Conversion,
  API Request Volume, Customer Health Score
- b2b_direct: Pipeline Value, Won Deals, Avg Deal Size, Sales Cycle Length
- marketplace_multi_sided: GMV, Active Buyers, Active Sellers, Take Rate
- manufacturing: Units Produced, OEE, Inventory Value, Defect Rate,
  On-Time Delivery, Plant Utilization
- logistics (incl. airlines): Bookings, Load Factor, On-Time Performance,
  Cargo Tonnage, Revenue per ASM/RPM, Active Routes
- financial_services: Transaction Volume, Loan Originations, Average
  Account Balance, Cards Active, NPS, AUM
- healthcare: Claims Processed, Appointment Volume, Prescription Fills,
  Member Acquisition, Average Wait Time, Auth Approval Rate
- media_content: Active Viewers, Watch Time, Content Catalogue Size,
  Subscription Conversion, Ad Impressions
- other: pick from the closest-matching vertical above based on the
  profile's actual operations

Each DashboardSpec needs: dashboard_id (^dash-[a-z0-9-]+$), title,
audience, primary_panels (2–4 panel titles).

Alerts: at least one AlertSpec per failure_mode in the business processes.
Each AlertSpec needs: alert_id (^alrt-[a-z0-9-]+$), title,
business_subject_line (CFO-friendly), technical_subject_line (SRE-friendly),
datasource_kind (postgres|prometheus|loki), query (a plausible SQL/PromQL/LogQL
expression), threshold_predicate (e.g. "> 0.05"), severity, routes_to (list).

Hard rules:
- No fences, no prose
- IDs must match the patterns above
- Business Health panel titles MUST reflect the vertical's actual KPIs,
  not retail terminology projected onto a non-retail company.
"""


async def propose_dashboards_and_alerts(state: PlanState) -> PlanState:
    with _tracer.start_as_current_span("plan.propose_dashboards") as span:
        bpm = state.get("business_processes", [])
        kg = state.get("knowledge_graph")
        services = (
            [n.node_id for n in kg.nodes if n.technical_subtype == "service"] if kg else []
        )

        failure_modes_summary = []
        for p in bpm:
            for fm in p.failure_modes:
                failure_modes_summary.append(f"- {p.name} → {fm.name}: {fm.description}")

        bm = state["profile"].industry_taxonomy.business_model.value
        user = (
            f"=== Company vertical (drives KPI selection) ===\n"
            f"business_model: {bm}\n"
            f"primary_industry: {state['profile'].industry_taxonomy.primary_industry}\n\n"
            f"=== Audience default ===\n{state.get('audience', TargetAudience.PIVOT).value}\n\n"
            "=== Business processes ===\n"
            + "\n".join(f"- {p.name} ({p.process_id})  KPIs: {', '.join(p.kpis)}" for p in bpm)
            + "\n\n=== Services in KG ===\n"
            + "\n".join(f"- {s}" for s in services)
            + "\n\n=== Failure modes (one alert per) ===\n"
            + "\n".join(failure_modes_summary)
            + "\n\nProduce the dashboards+alerts JSON. Pick Business Health "
              "panel titles from the vertical-fit KPI list in the system "
              "prompt that matches business_model above."
        )

        errors = state.setdefault("errors", [])
        kg_gen = state.get("gen_id_build_kg", "")
        process_gens = state.get("gen_ids_processes", [])
        parents = [g for g in [kg_gen, *process_gens] if g]
        try:
            data, gen_id = _llm_json(
                DASHBOARDS_SYSTEM, user,
                agent_name="clarion.planner.propose_dashboards_and_alerts",
                parent_generation_ids=parents,
                conversation_id=state.get("sigil_conversation_id", ""),
                max_tokens=12000,
            )
            dashboards = [DashboardSpec.model_validate(d) for d in data.get("dashboards", [])]
            alerts = [AlertSpec.model_validate(a) for a in data.get("alerts", [])]
            state["gen_id_dashboards"] = gen_id
            if gen_id:
                span.set_attribute("sigil.generation.id", gen_id)
        except (ValidationError, json.JSONDecodeError, ValueError) as exc:
            errors.append(f"propose_dashboards_and_alerts: {exc}")
            span.record_exception(exc)
            return state

        state["dashboard_specs"] = dashboards
        state["alert_specs"] = alerts
        span.set_attribute("plan.dashboard_count", len(dashboards))
        span.set_attribute("plan.alert_count", len(alerts))
        return state


# ============================================================
# Phase 6 — propose_assistant_tools
# ============================================================

TOOLS_SYSTEM = """You design 3–5 SQL views the Grafana Assistant can use.

Each AssistantTool is a named SQL view that answers a specific business
question against the demo's Postgres tables. The relevant tables for v0.2
queries are:
- business_events(event_id, plan_id, ts, event_type, business_entity_ids,
                  payload jsonb, trace_id)
- kg_nodes(plan_id, node_id, node_type, subtype, label, attributes,
           live_state_binding)
- kg_edges(plan_id, edge_id, edge_type, from_node_id, to_node_id, attributes)

Output JSON: {"tools": [{"tool_name": "...", "description": "...",
              "sql": "CREATE VIEW ... AS SELECT ...",
              "sample_questions": [...]}, ...]}

Hard rules:
- tool_name: ^[a-z][a-z0-9_]+$
- 3–5 tools, each answering a specific business question
- SQL should be valid PostgreSQL targeting the tables above; reference
  plan_id as a parameter (use $1) so the view is parameterizable
- sample_questions: 2–4 natural-language phrasings the tool answers
- No fences, no prose
"""


async def propose_assistant_tools(state: PlanState) -> PlanState:
    with _tracer.start_as_current_span("plan.propose_tools") as span:
        bpm = state.get("business_processes", [])
        user = (
            "=== Business processes for context ===\n"
            + "\n".join(f"- {p.name}: {p.description}" for p in bpm)
            + "\n\nProduce the assistant tools JSON."
        )
        errors = state.setdefault("errors", [])
        process_gens = state.get("gen_ids_processes", [])
        parents = [g for g in process_gens if g]
        try:
            # max_tokens bumped 3072 → 8192 after a ITC run truncated the
            # 5th tool's CREATE VIEW mid-string at char 6888 (saved dump
            # confirmed: file ended at `"sql": "CREATE VIEW o`). Five
            # complex SQL views with sample_questions arrays comfortably
            # exceed 3K tokens; 8K leaves headroom for verticals like
            # ITC/AcmeRetail that fan out across many entity types.
            data, gen_id = _llm_json(
                TOOLS_SYSTEM, user,
                agent_name="clarion.planner.propose_assistant_tools",
                parent_generation_ids=parents,
                conversation_id=state.get("sigil_conversation_id", ""),
                max_tokens=8192,
            )
            tools = [AssistantTool.model_validate(t) for t in data.get("tools", [])]
            if not 3 <= len(tools) <= 5:
                raise ValueError(f"got {len(tools)} tools; need 3-5")
            state["gen_id_tools"] = gen_id
            if gen_id:
                span.set_attribute("sigil.generation.id", gen_id)
        except (ValidationError, ValueError, json.JSONDecodeError) as exc:
            errors.append(f"propose_assistant_tools: {exc}")
            span.record_exception(exc)
            return state

        state["assistant_tools"] = tools
        span.set_attribute("plan.tool_count", len(tools))
        return state


# ============================================================
# Final assembly
# ============================================================

def _infrastructure_blueprint(kg: KnowledgeGraph) -> InfrastructureBlueprint:
    """Derive deterministically from the KG so it stays consistent with what we modeled."""
    services = [n for n in kg.nodes if n.technical_subtype == "service"]
    namespaces = [n.label for n in kg.nodes if n.technical_subtype == "namespace"][:5]
    databases = [n.label for n in kg.nodes if n.technical_subtype == "database"][:5]
    queues = [n.label for n in kg.nodes if n.technical_subtype == "queue"][:5]
    externals = [n.label for n in kg.nodes if n.technical_subtype == "external_dependency"][:5]
    agentic = [n.label for n in kg.nodes if n.node_type == NodeType.AGENTIC_RESOURCE][:5]
    return InfrastructureBlueprint(
        cluster_count=max(1, sum(1 for n in kg.nodes if n.technical_subtype == "cluster")),
        namespaces=namespaces,
        services=[s.node_id for s in services],
        databases=databases,
        queues=queues,
        external_dependencies=externals,
        agentic_workloads=agentic,
    )


def _data_blueprint(
    profile: CompanyProfile,
    kg: KnowledgeGraph,
    *,
    volume_per_day: int | None = None,
) -> DataBlueprint:
    """Derive the DataBlueprint from the profile + KG.

    `volume_per_day` is a hard override — when set, skips the auto-scale
    formula entirely and uses that value. Useful when the SE wants a
    tiny smoke build (e.g. 500/day, finishes in a minute) or a stress
    test (e.g. 50K/day to validate generator throughput) without editing
    code. When None we auto-scale by channel count, capped at 5K/day so
    a default build never burns Cloud quota for hours."""
    bm = profile.industry_taxonomy.business_model.value
    # Diurnal shape per vertical. We only have 5 shapes available
    # (`retail_us`, `retail_global`, `saas_b2b`, `ecommerce_us`, `flat`)
    # — pick whichever existing shape is closest to the vertical's
    # natural traffic curve. Most B2B-ish verticals (healthcare,
    # finserv, logistics) have a workday-shaped pattern that matches
    # `saas_b2b`. Retail is `retail_us`. Marketplaces/digital media
    # smooth through `retail_global`. Others fall through to flat-ish.
    if bm in ("b2c_retail", "omnichannel_retail"):
        diurnal, weekly = "retail_us", "weekend_heavy"
    elif bm in ("b2c_digital", "media_content"):
        diurnal, weekly = "retail_global", "weekend_heavy"
    elif bm in ("b2b_saas", "b2b_direct"):
        diurnal, weekly = "saas_b2b", "weekday_heavy"
    elif bm in ("manufacturing", "logistics", "financial_services",
                "healthcare", "marketplace_multi_sided"):
        # Workday-shaped but with weekend tail for global ops; flat-ish
        # weekly because these run 24/7 with weekday emphasis.
        diurnal, weekly = "saas_b2b", "weekday_heavy"
    else:  # other / unknown
        diurnal, weekly = "retail_global", "flat"
    store_count = sum(1 for n in kg.nodes if n.business_subtype == "store")
    region_count = sum(1 for n in kg.nodes if n.business_subtype == "region")
    channel_count = sum(1 for n in kg.nodes if n.business_subtype == "channel")
    if volume_per_day is not None:
        # Floor at 100 so the schema check (ge=100) doesn't reject a
        # too-aggressive smoke setting; ceiling at 100K so a fat-fingered
        # value doesn't accidentally page someone.
        volume = max(100, min(100_000, int(volume_per_day)))
    else:
        # Demo-scale default: looks real on dashboards, finishes
        # generation+trace emission in minutes (not 30+). 7 days × ~5K/day
        # × ~6 spans/event ≈ 200K spans — well within Cloud free-tier
        # headroom and quick to flush. Per-channel scaling keeps it
        # interesting for multi-channel retailers without exploding for
        # SaaS one-channel demos.
        volume = min(5_000, max(1_500, 1_500 * max(1, channel_count)))
    return DataBlueprint(
        historical_window_days=7,
        live_tail_minutes=30,
        business_event_volume_per_day=volume,
        diurnal_pattern=diurnal,
        weekly_pattern=weekly,
        store_count=store_count,
        region_count=region_count,
        channel_count=channel_count,
    )


def _cost_envelope() -> CostEnvelope:
    return CostEnvelope(
        estimated_usd_per_demo=2.50,
        ttl_hours=8,
        hard_ceiling_usd=10.0,
    )


def _narrative(profile: CompanyProfile, audience: TargetAudience, n_processes: int) -> str:
    company = profile.company.name
    industry = profile.industry_taxonomy.primary_industry
    return (
        f"A {audience.value}-first walkthrough of {company}'s "
        f"{industry.lower()} operations, modelled across {n_processes} business "
        f"processes. The demo opens on a Business Health dashboard, hits a "
        f"mid-stack degradation around T+4min that drags a top-line KPI, and "
        f"pivots to the technical evidence — services, queues, and traces — "
        f"that explain the dip. Recovers inside the 15-minute window."
    )


def _assemble(state: PlanState) -> DemoPlan | None:
    """Stitch everything into a DemoPlan. Returns None if a required piece is missing."""
    required = ("audience", "business_processes", "knowledge_graph", "incident_script",
                "dashboard_specs", "alert_specs", "assistant_tools")
    missing = [k for k in required if not state.get(k)]
    if missing:
        state.setdefault("errors", []).append(f"assemble: missing {missing}")
        return None

    profile = state["profile"]
    kg = state["knowledge_graph"]
    audience = state["audience"]
    bpm = state["business_processes"]
    volume_per_day = state.get("volume_per_day")  # SE-supplied override, or None

    return DemoPlan(
        plan_id=uuid4(),
        schema_version="0.1.0",
        created_at=datetime.now(UTC),
        source_profile_id=profile.profile_id,
        target_audience=audience,
        narrative=_narrative(profile, audience, len(bpm)),
        business_process_models=bpm,
        infrastructure_blueprint=_infrastructure_blueprint(kg),
        data_blueprint=_data_blueprint(profile, kg, volume_per_day=volume_per_day),
        incident_script=state["incident_script"],
        knowledge_graph=kg,
        dashboard_specs=state["dashboard_specs"],
        alert_specs=state["alert_specs"],
        assistant_tools=state["assistant_tools"],
        cost_envelope=_cost_envelope(),
        review_state=ReviewState.DRAFT,
    )


# ============================================================
# Entrypoint
# ============================================================

async def run_plan(
    profile: CompanyProfile,
    *,
    volume_per_day: int | None = None,
) -> PlanState:
    """Run the full planner end-to-end. Returns final state including the DemoPlan.

    `volume_per_day` overrides the auto-derived event volume in the
    DataBlueprint. SEs use this to make a tiny smoke build that finishes
    in a minute, or a stress-test build to validate generator throughput.
    Defaults to None (auto-scale by channel count, capped at 5K/day).
    """
    state: PlanState = {
        "profile": profile,
        "errors": [],
        "started_at": time.monotonic(),
        "sigil_conversation_id": f"clarion-plan-{profile.profile_id}-{uuid4().hex[:8]}",
        "gen_ids_processes": [],
    }
    if volume_per_day is not None:
        state["volume_per_day"] = volume_per_day  # type: ignore[typeddict-unknown-key]

    with _tracer.start_as_current_span("plan.run") as root:
        root.set_attribute("plan.profile_id", profile.profile_id)

        for phase, fn in (
            ("analyze_profile", analyze_profile),
            ("model_processes", model_processes),
            ("build_kg", build_kg),
            ("script_incident", script_incident),
            ("propose_dashboards_and_alerts", propose_dashboards_and_alerts),
            ("propose_assistant_tools", propose_assistant_tools),
        ):
            _logger.info("plan.phase.start", phase=phase)
            state = await fn(state)
            _logger.info(
                "plan.phase.done",
                phase=phase,
                error_count=len(state.get("errors", [])),
            )

        plan = _assemble(state)
        state["plan"] = plan
        if plan:
            root.set_attribute("plan.plan_id", str(plan.plan_id))
            root.set_attribute("plan.kg.node_count", len(plan.knowledge_graph.nodes))
            root.set_attribute("plan.kg.edge_count", len(plan.knowledge_graph.edges))
            _logger.info("plan.run.ok", plan_id=str(plan.plan_id),
                         duration_s=time.monotonic() - state["started_at"])
        else:
            root.set_status(trace.Status(trace.StatusCode.ERROR, "plan assembly failed"))
            _logger.warning("plan.run.failed", errors=state.get("errors", []))

        return state
