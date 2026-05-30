"""SE↔agent chat endpoints.

Two surfaces:
- `POST /api/agents/research/extend`   — extend a CompanyProfile with more
  research grounded on the existing one + the SE's prompt. Streams the
  agent's response. Read-only, no persistence (the existing
  ExtendProfilePanel does its own profile_audit_log writes via
  /api/profiles/{id}/extend).
- `POST /api/agents/plan/refine`       — ask the planner agent to
  reconsider a section of the DemoPlan given SE feedback. Streams the
  agent's narrative AND persists structured proposals from Claude
  tool-use into plan_refinement_sessions / plan_refinement_turns.
  Conversation survives page nav; Summarize/Apply read from these
  tables. Adds an SSE `proposals` event after the text stream so the
  UI can render per-turn chips without an extra fetch.
- `GET /api/agents/plan/refine/{plan_id}` — load the open refinement
  session for a plan (status + every turn). Used by the chat panel
  on mount to hydrate conversation history.

Both streaming surfaces run through `llm_client.stream_anthropic`,
which opens a `gen_ai.chat {model}` span carrying the Gen AI
semantic-convention attributes plus a `gen_ai.ttft_ms` first-token
timing. Sigil is skipped here — `sigil_helper` still only supports
non-streaming, and the SE chat surface is exploratory rather than a
pipeline artefact.
"""

from __future__ import annotations

import json
import os
from typing import Any

import structlog
from anthropic import Anthropic
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from proj_clarion.api.routes.plans import _resolve_plan_id
from proj_clarion.schemas import (
    PROPOSE_PLAN_CHANGES_TOOL,
    ProposedChange,
    collapse_proposals,
)
from proj_clarion.storage import (
    PlanRefinementSessionRepo,
    PlanRefinementTurnRepo,
    PlanRepo,
    ProfileRepo,
    session_scope,
)

_logger = structlog.get_logger()

router = APIRouter(prefix="/api/agents", tags=["agents"])


def _model() -> str:
    return os.getenv("ANTHROPIC_MODEL", "claude-opus-4-7")


def _client() -> Anthropic:
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        raise HTTPException(
            status_code=503,
            detail="ANTHROPIC_API_KEY not set; chat agent unavailable",
        )
    return Anthropic(api_key=key)


class ChatBody(BaseModel):
    """One turn of conversation. UI sends the full history each call —
    server is stateless so we don't have to chase chat-state correctness here."""

    context_id: str  # profile_id or plan_id
    history: list[dict[str, str]]  # [{"role": "user"|"assistant", "content": "..."}]


_RESEARCH_SYSTEM = """You are a research assistant helping a Grafana Solutions Engineer
deepen the Clarion CompanyProfile they have for a prospective customer.

The SE may ask you to:
- Surface additional pain signals or tech-stack signals from public sources.
- Explore a specific channel, region, or business unit in more depth.
- Reconcile contradictions in the existing profile.

The current CompanyProfile is provided below as JSON. Treat it as the
ground truth; any net-new claims you make should be additive, cited
when possible (URL or "synthesized — needs verification"), and
narrowly-scoped. Do not propose schema changes; the SE will translate
your suggestions into structured updates separately.
"""


_PLAN_SYSTEM = """You are a planning assistant helping a Grafana Solutions Engineer
refine the Clarion DemoPlan they have for an upcoming demo.

WORKFLOW EACH TURN:

1. Read the SE's question. Reply with concise narrative reasoning
   (markdown OK) — explain what you'd change and why. The SE reads
   this in the chat panel.

2. AFTER your narrative, call the `propose_plan_changes` tool to
   record the concrete changes you'd make. The SE will see those in
   a Summary view alongside your narrative and decide whether to
   apply. The plan is not mutated until they click Apply.

   Use **plan-level targets** (kg_node, kg_edge, process, alert,
   dashboard, incident_event) when the change can be made by
   re-running the plan phase.

   Use **profile-level targets** (tech_stack_signal, pain_signal,
   channel, business_entity_candidate, strategic_priority) ONLY when
   the change requires net-new research not in the current profile.
   These trigger a more expensive research + plan re-run, so be
   conservative.

3. If the SE's question is purely informational ("what processes do
   we have?"), skip the tool call — just answer.

The current DemoPlan is provided below as JSON. Refer to entities by
id (process_id, alert_id, etc.) when modifying or removing them.
"""


def _build_messages(history: list[dict[str, str]]) -> list[dict[str, Any]]:
    """Anthropic API expects {role, content} only and rejects unknown keys."""
    return [
        {"role": h["role"], "content": h["content"]}
        for h in history
        if h.get("role") in ("user", "assistant") and h.get("content")
    ]


# ──────────────────────────────────────────────────────────────────
# /research/extend — unchanged, read-only narrative stream
# ──────────────────────────────────────────────────────────────────


def _stream_response(
    system: str, messages: list[dict[str, Any]], *, prompt_template: str,
) -> EventSourceResponse:
    """Token-by-token SSE stream from Anthropic. No persistence, no tools.

    Used by research/extend, which writes its own profile_audit_log
    rows via the /profiles/{id}/extend mutation endpoint.
    """
    from proj_clarion.observability.llm_client import stream_anthropic

    client = _client()
    request = {
        "model": _model(),
        "max_tokens": 2048,
        "system": system,
        "messages": messages,
    }

    async def event_gen() -> object:
        try:
            with stream_anthropic(
                client, request,
                agent_name=f"clarion.agents.{prompt_template}",
                prompt_template=prompt_template,
            ) as stream:
                for text in stream.text_stream:
                    yield {"event": "delta", "data": text}
            yield {"event": "done", "data": ""}
        except Exception as exc:  # noqa: BLE001 — surface upstream errors as-is
            yield {"event": "error", "data": str(exc)}

    return EventSourceResponse(event_gen())


@router.post("/research/extend")
def research_extend(body: ChatBody) -> EventSourceResponse:
    """Stream a research-agent response grounded on a CompanyProfile."""
    with session_scope() as s:
        profile = ProfileRepo().get(s, body.context_id)
    if profile is None:
        raise HTTPException(status_code=404, detail=f"profile {body.context_id} not found")

    system = (
        _RESEARCH_SYSTEM
        + "\n\n=== Current CompanyProfile ===\n"
        + profile.model_dump_json(indent=2)
    )
    return _stream_response(
        system, _build_messages(body.history), prompt_template="research.extend",
    )


# ──────────────────────────────────────────────────────────────────
# /plan/refine — tool-use + persistence
# ──────────────────────────────────────────────────────────────────


def _extract_tool_proposals(final_message: Any) -> list[dict[str, Any]]:
    """Pull the `changes` list out of the propose_plan_changes tool_use
    block in the final message. Returns [] if the agent didn't call
    the tool (informational question, or model chose not to).

    Defensive against shape drift in the Anthropic SDK — we duck-type
    on `type` + `name` rather than isinstance, and tolerate missing
    fields. Worst case we return [] and the assistant turn lands with
    no proposals.
    """
    out: list[dict[str, Any]] = []
    for block in (getattr(final_message, "content", None) or []):
        if getattr(block, "type", None) != "tool_use":
            continue
        if getattr(block, "name", None) != "propose_plan_changes":
            continue
        input_obj = getattr(block, "input", None) or {}
        changes = input_obj.get("changes") if isinstance(input_obj, dict) else None
        if isinstance(changes, list):
            out.extend(c for c in changes if isinstance(c, dict))
    return out


@router.post("/plan/refine")
def plan_refine(body: ChatBody) -> EventSourceResponse:
    """Stream a planner-agent response grounded on a DemoPlan.

    Persistence model:
      * Each call lives within a `plan_refinement_session` (status=open,
        one per plan at a time — partial unique index enforces this).
      * The latest user turn is persisted BEFORE the LLM call so it
        survives an upstream failure.
      * The assistant turn (narrative + parsed tool_use proposals) is
        persisted AFTER the stream completes successfully.

    SSE events emitted:
      * `delta` — text chunks (existing behavior)
      * `proposals` — JSON list of ProposedChange dicts, once, after
        the stream completes and before `done`. UI uses this to render
        per-turn chips without an extra fetch.
      * `done` — JSON {session_id, turn_count} for the UI to refetch
        or update local state.
      * `error` — error message, terminal.
    """
    with session_scope() as s:
        full_id = _resolve_plan_id(s, body.context_id)
        if not full_id:
            raise HTTPException(status_code=404, detail=f"plan {body.context_id} not found")
        plan = PlanRepo().get(s, full_id)
    if plan is None:
        raise HTTPException(status_code=404, detail=f"plan {full_id} not found")

    # Persist the new user turn up front so it's durable even if the
    # LLM call fails. Assumption: history[-1] is the new user prompt,
    # earlier entries are echoes of already-persisted turns. This
    # contract matches what the in-flight frontend sends; once the
    # frontend goes server-backed (#27) the request body will shrink.
    latest_user = ""
    if body.history and body.history[-1].get("role") == "user":
        latest_user = body.history[-1].get("content", "").strip()

    with session_scope() as s:
        sess = PlanRefinementSessionRepo().ensure_open_session(s, full_id)
        session_id = sess["session_id"]
        if latest_user:
            PlanRefinementTurnRepo().append_turn(
                s, session_id, role="user", content=latest_user,
            )

    system = (
        _PLAN_SYSTEM
        + "\n\n=== Current DemoPlan ===\n"
        + plan.model_dump_json(indent=2)
    )
    return _stream_plan_refine(
        system=system,
        messages=_build_messages(body.history),
        session_id=session_id,
    )


def _stream_plan_refine(
    *, system: str, messages: list[dict[str, Any]], session_id: int,
) -> EventSourceResponse:
    """Stream + persist. Separate from _stream_response because the
    persistence and tool-use logic is plan-refine-specific."""
    from proj_clarion.observability.llm_client import stream_anthropic

    client = _client()
    request: dict[str, Any] = {
        "model": _model(),
        "max_tokens": 4096,
        "system": system,
        "messages": messages,
        "tools": [PROPOSE_PLAN_CHANGES_TOOL],
        # Auto: model decides whether to call the tool. Informational
        # turns ("what's in here?") shouldn't force a tool call.
        "tool_choice": {"type": "auto"},
    }

    async def event_gen() -> object:
        accumulated_text = ""
        proposals: list[dict[str, Any]] = []
        tokens_in: int | None = None
        tokens_out: int | None = None
        try:
            with stream_anthropic(
                client, request,
                agent_name="clarion.agents.plan.refine",
                prompt_template="plan.refine",
            ) as stream:
                for text_chunk in stream.text_stream:
                    accumulated_text += text_chunk
                    yield {"event": "delta", "data": text_chunk}
                # After the text stream drains, the final message has
                # the tool_use block + usage. SDK API varies between
                # versions so we tolerate failure here — at worst we
                # persist the assistant turn with no proposals.
                try:
                    final = stream.get_final_message()
                    proposals = _extract_tool_proposals(final)
                    usage = getattr(final, "usage", None)
                    if usage is not None:
                        tokens_in = getattr(usage, "input_tokens", None)
                        tokens_out = getattr(usage, "output_tokens", None)
                except Exception as exc:  # noqa: BLE001
                    _logger.warning(
                        "plan.refine.final_unavailable",
                        session_id=session_id, error=str(exc),
                    )

            # Persist assistant turn. Even when the stream produced no
            # text (rare, but possible if the model went straight to
            # tool_use), record the proposals so the conversation
            # history has structured output the UI can show.
            with session_scope() as s:
                PlanRefinementTurnRepo().append_turn(
                    s, session_id,
                    role="assistant",
                    content=accumulated_text,
                    proposed_changes=proposals if proposals else None,
                    tokens_in=tokens_in, tokens_out=tokens_out,
                )

            if proposals:
                yield {"event": "proposals", "data": json.dumps(proposals)}
            yield {
                "event": "done",
                "data": json.dumps({
                    "session_id":     session_id,
                    "proposal_count": len(proposals),
                }),
            }
        except Exception as exc:  # noqa: BLE001
            _logger.exception(
                "plan.refine.failed", session_id=session_id,
            )
            yield {"event": "error", "data": str(exc)}

    return EventSourceResponse(event_gen())


# ──────────────────────────────────────────────────────────────────
# /plan/refine/{plan_id} — hydrate the chat panel on mount
# ──────────────────────────────────────────────────────────────────


class RefineTurnDTO(BaseModel):
    """One turn shaped for the UI. JSONB columns come back as
    dict/list already (psycopg JSONB → Python)."""

    turn_id:          int
    role:             str
    content:          str
    proposed_changes: list[dict[str, Any]] | None = None
    tokens_in:        int | None = None
    tokens_out:       int | None = None
    created_at:       str


class RefineSessionDTO(BaseModel):
    """The full refinement-session payload the chat panel needs to
    render its history + decide whether Summarize/Apply are reachable."""

    session_id:      int
    plan_id:         str
    status:          str
    phase_decision:  str | None = None
    summary_cache:   dict[str, Any] | None = None
    created_at:      str
    updated_at:      str
    turns:           list[RefineTurnDTO]


class RefineHistoryEntryDTO(BaseModel):
    """One row in the history list shown above the chat panel. Slimmer
    than RefineSessionDTO — we don't ship full turns until the SE
    clicks into a specific session."""

    session_id:     int
    status:         str
    phase_decision: str | None = None
    summary_cache:  dict[str, Any] | None = None
    turn_count:     int
    created_at:     str
    updated_at:     str


# Route ordering matters in FastAPI — register more-specific paths
# (/history, /session/{id}) BEFORE the generic /{plan_id} or it would
# swallow them.

@router.get(
    "/plan/refine/{plan_id}/history",
    response_model=list[RefineHistoryEntryDTO],
)
def plan_refine_history(plan_id: str) -> list[RefineHistoryEntryDTO]:
    """All sessions for a plan, newest first. The UI uses this for
    the history tab strip — each entry is a past or open session the
    SE can pull up read-only."""
    with session_scope() as s:
        full_id = _resolve_plan_id(s, plan_id)
        if not full_id:
            raise HTTPException(status_code=404, detail=f"plan {plan_id} not found")
        rows = PlanRefinementSessionRepo().list_for_plan(s, full_id)
    return [
        RefineHistoryEntryDTO(
            session_id=r["session_id"],
            status=r["status"],
            phase_decision=r["phase_decision"],
            summary_cache=r["summary_cache"],
            turn_count=r["turn_count"],
            created_at=r["created_at"].isoformat(),
            updated_at=r["updated_at"].isoformat(),
        )
        for r in rows
    ]


@router.get(
    "/plan/refine/session/{session_id}",
    response_model=RefineSessionDTO,
)
def plan_refine_get_by_session(session_id: int) -> RefineSessionDTO:
    """Fetch a specific session by id (open OR closed) with all its
    turns. Used by the history view to render a past conversation
    read-only when the SE clicks one of the tabs."""
    with session_scope() as s:
        sess = PlanRefinementSessionRepo().get_session(s, session_id)
        if sess is None:
            raise HTTPException(status_code=404, detail=f"session {session_id} not found")
        turns = PlanRefinementTurnRepo().list_turns(s, session_id)

    return RefineSessionDTO(
        session_id=sess["session_id"],
        plan_id=sess["plan_id"],
        status=sess["status"],
        phase_decision=sess["phase_decision"],
        summary_cache=sess["summary_cache"],
        created_at=sess["created_at"].isoformat(),
        updated_at=sess["updated_at"].isoformat(),
        turns=[
            RefineTurnDTO(
                turn_id=t["turn_id"],
                role=t["role"],
                content=t["content"],
                proposed_changes=t["proposed_changes"],
                tokens_in=t["tokens_in"],
                tokens_out=t["tokens_out"],
                created_at=t["created_at"].isoformat(),
            )
            for t in turns
        ],
    )


@router.get("/plan/refine/{plan_id}", response_model=RefineSessionDTO | None)
def plan_refine_get(plan_id: str) -> RefineSessionDTO | None:
    """Return the OPEN refinement session for this plan (with all
    turns), or None if there isn't one. The UI calls this on mount
    to hydrate the chat panel.

    History of CLOSED sessions (applied / cancelled) is reachable
    separately — TBD as a /plan/refine/{plan_id}/history endpoint
    when we want the SE to scroll back to prior refinements.
    """
    with session_scope() as s:
        full_id = _resolve_plan_id(s, plan_id)
        if not full_id:
            raise HTTPException(status_code=404, detail=f"plan {plan_id} not found")
        sess = PlanRefinementSessionRepo().get_open_session(s, full_id)
        if sess is None:
            return None
        turns = PlanRefinementTurnRepo().list_turns(s, sess["session_id"])

    return RefineSessionDTO(
        session_id=sess["session_id"],
        plan_id=sess["plan_id"],
        status=sess["status"],
        phase_decision=sess["phase_decision"],
        summary_cache=sess["summary_cache"],
        created_at=sess["created_at"].isoformat(),
        updated_at=sess["updated_at"].isoformat(),
        turns=[
            RefineTurnDTO(
                turn_id=t["turn_id"],
                role=t["role"],
                content=t["content"],
                proposed_changes=t["proposed_changes"],
                tokens_in=t["tokens_in"],
                tokens_out=t["tokens_out"],
                created_at=t["created_at"].isoformat(),
            )
            for t in turns
        ],
    )


# ──────────────────────────────────────────────────────────────────
# /plan/refine/{plan_id}/summarize — collapse turn-level proposals
# into one canonical change set
# ──────────────────────────────────────────────────────────────────


class RefineSummaryDTO(BaseModel):
    """The collapsed change set returned by /summarize. Shape mirrors
    CollapsedSummary (which uses ProposedChange internally) but with
    `dict` payloads since the Pydantic models serialize that way on
    the wire anyway."""

    session_id:              int
    profile_changes:         list[dict[str, Any]]
    plan_changes:            list[dict[str, Any]]
    requires_research_rerun: bool
    targets_summary:         dict[str, int]
    turn_count:              int
    proposal_count:          int


@router.post("/plan/refine/{plan_id}/summarize", response_model=RefineSummaryDTO)
def plan_refine_summarize(plan_id: str) -> RefineSummaryDTO:
    """Collapse all turn-level proposals into one canonical change set,
    cache it on the session row, and flip status open → summarized.

    Idempotent on its own — re-collapsing the same turns yields the
    same result. Calling after status=applied would re-overwrite the
    cache, which we don't want, so we return 409 in that case.
    """
    with session_scope() as s:
        full_id = _resolve_plan_id(s, plan_id)
        if not full_id:
            raise HTTPException(status_code=404, detail=f"plan {plan_id} not found")
        sess_repo = PlanRefinementSessionRepo()
        sess = sess_repo.get_open_session(s, full_id)
        if sess is None:
            # No open session — either nothing's been refined or the
            # last one was already applied/cancelled.
            raise HTTPException(
                status_code=404,
                detail=f"no open refinement session for plan {full_id}",
            )
        if sess["status"] not in ("open", "summarized"):
            # Defensive — partial unique index already prevents this,
            # but worth surfacing if state drift happens.
            raise HTTPException(
                status_code=409,
                detail=f"session is {sess['status']!r}; cannot re-summarize",
            )
        turns = PlanRefinementTurnRepo().list_turns(s, sess["session_id"])

    # Pull proposals out of each assistant turn. JSONB list[dict] →
    # Pydantic ProposedChange (validated). Malformed entries get
    # logged and skipped rather than failing the whole summarize.
    proposals_by_turn: list[list[ProposedChange]] = []
    proposal_count = 0
    for t in turns:
        if t["role"] != "assistant":
            continue
        raw = t.get("proposed_changes") or []
        if not isinstance(raw, list):
            continue
        validated: list[ProposedChange] = []
        for entry in raw:
            if not isinstance(entry, dict):
                continue
            try:
                validated.append(ProposedChange.model_validate(entry))
            except Exception as exc:  # noqa: BLE001
                _logger.warning(
                    "refine.summarize.skip_invalid_proposal",
                    turn_id=t["turn_id"], error=str(exc),
                )
        if validated:
            proposals_by_turn.append(validated)
            proposal_count += len(validated)

    collapsed = collapse_proposals(proposals_by_turn)
    summary_payload = collapsed.model_dump(mode="json")

    # Cache + flip status. set_summary handles the open→summarized bump.
    with session_scope() as s:
        sess_repo.set_summary(s, sess["session_id"], summary_payload)

    return RefineSummaryDTO(
        session_id=sess["session_id"],
        profile_changes=summary_payload["profile_changes"],
        plan_changes=summary_payload["plan_changes"],
        requires_research_rerun=collapsed.requires_research_rerun,
        targets_summary=collapsed.targets_summary,
        turn_count=len(turns),
        proposal_count=proposal_count,
    )


# ──────────────────────────────────────────────────────────────────
# /plan/refine/{plan_id}/apply — agent-decided phase + pipeline kick-off
# ──────────────────────────────────────────────────────────────────


# Tool the phase-selection agent calls. One required `phase` enum +
# optional extension_prompt that we feed to /profiles/{id}/extend when
# the phase needs new research signals.
CHOOSE_PHASE_TOOL: dict[str, Any] = {
    "name": "choose_pipeline_phase",
    "description": (
        "Pick the cheapest pipeline phase that can produce the proposed changes. "
        "`plan` re-runs only the planner against the current profile; "
        "`research+plan` first extends the profile with new signals then re-plans; "
        "`full` starts over from URL research. Prefer the cheapest option."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "phase": {
                "type": "string",
                "enum": ["plan", "research+plan", "full"],
                "description": "Which phase the next pipeline run should start at.",
            },
            "reasoning": {
                "type": "string",
                "description": "One sentence explaining the choice. Shown to the SE in the apply confirmation dialog.",
            },
            "extension_prompt": {
                "type": "string",
                "description": (
                    "Natural-language prompt to feed the research-extension agent. "
                    "Required when phase is `research+plan` or `full`; should "
                    "summarize the profile-level signals that need to land "
                    "(NCR Voyix POS, Azure cloud, kiosk channels, etc.) so the "
                    "research agent knows what to look for."
                ),
            },
        },
        "required": ["phase", "reasoning"],
    },
}


class ApplyResponseDTO(BaseModel):
    """Returned by /apply. UI uses pipeline_id to start watching the
    live build (PipelineContext.loadPipeline)."""

    session_id:    int
    phase:         str            # 'plan' | 'research+plan' | 'full'
    reasoning:     str
    pipeline_id:   str
    profile_extended: bool         # True if we called /profiles/{id}/extend first
    extension_summary: str | None = None


@router.post("/plan/refine/{plan_id}/apply", response_model=ApplyResponseDTO)
def plan_refine_apply(plan_id: str) -> ApplyResponseDTO:
    """Take the cached summary, ask the agent which phase to re-run
    from, optionally extend the profile, and kick off a pipeline.

    Preconditions:
      * /summarize must have run successfully (summary_cache populated).
      * Session must be in status `summarized` — `open` means the SE
        hasn't summarized yet, `applied` means we already kicked off
        a pipeline for this session.
    """
    from proj_clarion.api.pipeline_registry import start_pipeline_from_phase

    # 1. Load session + plan + profile.
    with session_scope() as s:
        full_id = _resolve_plan_id(s, plan_id)
        if not full_id:
            raise HTTPException(status_code=404, detail=f"plan {plan_id} not found")
        sess_repo = PlanRefinementSessionRepo()
        sess = sess_repo.get_open_session(s, full_id)
        if sess is None:
            raise HTTPException(
                status_code=404,
                detail=f"no open refinement session for plan {full_id}",
            )
        if sess["status"] != "summarized":
            raise HTTPException(
                status_code=409,
                detail=f"session status is {sess['status']!r}; call /summarize first",
            )
        if not sess["summary_cache"]:
            raise HTTPException(
                status_code=409,
                detail="session has no cached summary; call /summarize first",
            )
        plan = PlanRepo().get(s, full_id)
        if plan is None:
            raise HTTPException(status_code=404, detail=f"plan {full_id} disappeared mid-apply")
        # source_profile_id lives on the plan; we'll need it for the
        # extend call + the pipeline run.
        profile_id = plan.source_profile_id
        profile = ProfileRepo().get(s, profile_id) if profile_id else None
        if profile is None:
            raise HTTPException(
                status_code=400,
                detail=f"plan {full_id} has no source profile to apply against",
            )
    session_id = sess["session_id"]
    summary = sess["summary_cache"]

    # 2. Ask Claude to pick the phase. Force the tool call so we
    # always get a structured answer (informational replies don't
    # make sense at this step).
    client = _client()
    decision = _choose_phase(client, summary, plan_id=full_id)
    phase = decision["phase"]
    reasoning = decision["reasoning"]
    extension_prompt = decision.get("extension_prompt") or ""

    # Defensive: if the summary requires_research_rerun but the agent
    # picked plan-only, escalate. The profile-level signals can't land
    # without a research step, so plan-only would silently drop them.
    if summary.get("requires_research_rerun") and phase == "plan":
        phase = "research+plan"
        reasoning = (
            "Escalated from plan to research+plan because the summary "
            "includes profile-level changes that require new research."
        )

    # 3. Branch on phase. Two sub-steps for research+plan / full:
    #    a) extend the profile (mutates Postgres, adds an audit row)
    #    b) start the pipeline at the right phase
    profile_extended = False
    extension_summary: str | None = None

    if phase in ("research+plan", "full") and extension_prompt:
        try:
            ext_result = _extend_profile_inline(profile_id, extension_prompt)
            profile_extended = True
            extension_summary = ext_result.get("summary")
        except HTTPException:
            # Propagate — bad API key, missing profile, etc. surface
            # cleanly via the apply endpoint.
            raise
        except Exception as exc:  # noqa: BLE001
            _logger.exception(
                "refine.apply.extend_failed",
                session_id=session_id, profile_id=profile_id,
            )
            raise HTTPException(
                status_code=502,
                detail=f"profile extension failed: {exc}",
            ) from exc

    if phase == "plan":
        state = start_pipeline_from_phase(
            starting_phase="plan",
            url=profile.company.primary_url or "",
            company=profile.company.name,
            profile_id=profile_id,
        )
    elif phase == "research+plan":
        # Profile was just extended. Re-plan against the now-richer profile.
        state = start_pipeline_from_phase(
            starting_phase="plan",
            url=profile.company.primary_url or "",
            company=profile.company.name,
            profile_id=profile_id,
        )
    else:  # "full"
        state = start_pipeline_from_phase(
            starting_phase="research",
            url=profile.company.primary_url or "",
            company=profile.company.name,
        )

    # 4. Persist decision + status. Single session_scope so a crash
    # between these writes doesn't leave half-applied state.
    with session_scope() as s:
        sess_repo.set_phase_decision(s, session_id, phase)
        sess_repo.close_session(s, session_id, "applied")

    return ApplyResponseDTO(
        session_id=session_id,
        phase=phase,
        reasoning=reasoning,
        pipeline_id=state.pipeline_id,
        profile_extended=profile_extended,
        extension_summary=extension_summary,
    )


def _choose_phase(
    client: Anthropic, summary: dict[str, Any], *, plan_id: str,
) -> dict[str, Any]:
    """Single forced-tool-use call. Returns the phase + reasoning +
    optional extension_prompt. Non-streaming because the response is
    small structured data (no need for token-by-token rendering)."""
    system = (
        "You are the phase-selection agent for Proj Clarion. Given a "
        "collapsed summary of proposed plan changes, choose the cheapest "
        "pipeline phase that can produce them. Use the `choose_pipeline_phase` "
        "tool to record your decision.\n\n"
        "Heuristics:\n"
        "- If `requires_research_rerun` is true, choose `research+plan` "
        "(or `full` if the change set fundamentally rethinks the company).\n"
        "- If only plan-level targets are present (kg_node/process/alert/etc.) "
        "AND they can plausibly be derived from the existing profile, choose `plan`.\n"
        "- If many alerts/dashboards/processes need to change at once, "
        "or the incident_script needs structural rewrite, `plan` is still "
        "the right choice — the planner agent can re-derive everything "
        "from the profile.\n"
        "- `full` is rarely correct; only when the SE clearly wants to "
        "throw out the current research entirely."
    )
    user = (
        f"Plan id: {plan_id}\n\n"
        f"Collapsed summary:\n```json\n{json.dumps(summary, indent=2)}\n```\n\n"
        "Call `choose_pipeline_phase` with your decision."
    )

    msg = client.messages.create(
        model=_model(),
        max_tokens=1024,
        system=system,
        messages=[{"role": "user", "content": user}],
        tools=[CHOOSE_PHASE_TOOL],
        tool_choice={"type": "tool", "name": "choose_pipeline_phase"},
    )
    for block in (msg.content or []):
        if getattr(block, "type", None) == "tool_use" and block.name == "choose_pipeline_phase":
            input_obj = block.input or {}
            if isinstance(input_obj, dict) and "phase" in input_obj:
                return input_obj
    raise HTTPException(
        status_code=502,
        detail="phase-selection agent did not return a tool_use block",
    )


def _extend_profile_inline(
    profile_id: str, prompt: str,
) -> dict[str, Any]:
    """Call /profiles/{id}/extend's logic without going through HTTP.
    Returns the same dict shape as ExtendResponse so the caller can
    surface the per-field additions count."""
    from proj_clarion.api.routes.profiles import (
        ExtendRequest,
        extend_profile as _extend_route,
    )
    response = _extend_route(profile_id, ExtendRequest(prompt=prompt))
    # ExtendResponse is a Pydantic model; serialize for the apply
    # response payload.
    return response.model_dump(mode="json")
