"""Clarion Assistant — global chat with read-only tool access.

This is the backend for the Cmd-K assistant panel that spans every page
of the UI. Single persistent conversation per SE (or many — the UI
picks which to resume), with multi-turn tool-use loops over the
read-only Clarion tool catalog.

Endpoints (all under /api/agents/clarion):

    POST /chat                    — send a message, get an SSE stream.
                                    Multi-turn tool-use loop is server-
                                    driven; client just consumes events.
    GET  /conversations           — list active conversations (newest
                                    last-message first).
    GET  /conversations/{cid}     — full conversation with all turns.
    POST /conversations/{cid}/archive — soft-delete from the picker.

SSE events emitted by /chat:

    event: delta             — assistant text chunk
    event: tool_call         — JSON {name, input, tool_use_id} — emitted
                               once per tool the agent invokes, after
                               that iteration's text stream closes.
    event: tool_result       — JSON {tool_use_id, summary, is_error} —
                               emitted after the executor runs.
    event: done              — JSON {conversation_id, turn_count} —
                               agent loop finished.
    event: error             — string error message — terminal.

Persistence model:
    user      turn → {role, content, context_scope}
    assistant turn → {role, content, tool_calls?, tokens_in/out}
    tool      turn → {role, content="", tool_results}   (one per tool batch)

Agent loop iterates up to AGENT_MAX_ITERATIONS times. Each iteration:
    1. Rebuild Anthropic `messages` from all turns in the DB.
    2. Call client.messages.stream(...) with tools + tool_choice=auto.
    3. Stream text deltas to client as `delta` events.
    4. After stream completes, inspect final_message.content:
       - If no tool_use blocks → persist assistant turn (final), break.
       - If tool_use blocks → persist assistant turn (with tool_calls),
         emit tool_call events, execute each tool, persist tool turn
         with results, emit tool_result events, loop.

Safety:
    * Hard iteration cap (AGENT_MAX_ITERATIONS = 5) so a confused agent
      can't run away with tools.
    * Per-tool errors land in tool_result blocks with is_error=True —
      Claude sees them on the next iteration and can recover.
"""

from __future__ import annotations

import json
import os
from typing import Any, Literal

import structlog
from anthropic import Anthropic
from fastapi import APIRouter, HTTPException
from opentelemetry import trace
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from proj_clarion.agents.clarion_tools import (
    MUTATING_TOOL_NAMES,
    NEEDS_APPROVAL_TOOL_NAMES,
    TOOLS_ALL,
    execute_tool,
)
from proj_clarion.storage import (
    AssistantConversationRepo,
    AssistantTurnRepo,
    session_scope,
)

_logger = structlog.get_logger()
# Traces the agentic loop the same way agents/planner.py traces build
# phases: an `assistant.conversation` span groups each turn, the LLM calls
# nest as `gen_ai.chat` spans (via stream_anthropic), and each tool runs
# under an `execute_tool {name}` span.
_tracer = trace.get_tracer("proj-clarion.assistant")

router = APIRouter(prefix="/api/agents/clarion", tags=["clarion-assistant"])


# Cap on agent-loop iterations per /chat call. Each iteration is one
# Claude streaming call + (optionally) one tool batch + tool_result
# turn. 5 is enough for legitimate "look at X, then Y, then answer"
# chains; anything more usually means the agent is in a loop.
AGENT_MAX_ITERATIONS = 5


def _model() -> str:
    return os.getenv("ANTHROPIC_MODEL", "claude-opus-4-7")


def _client() -> Anthropic:
    key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not key:
        raise HTTPException(
            status_code=503,
            detail="ANTHROPIC_API_KEY not set; Clarion Assistant unavailable",
        )
    return Anthropic(api_key=key)


_CLARION_SYSTEM = """You are the Clarion Assistant — an AI helping a Grafana Solutions Engineer (SE) operate Proj Clarion, an end-to-end Grafana demo generator.

What Clarion does:
  1. Researches a company URL → CompanyProfile (channels, pain signals, tech stack, geographic footprint, strategic priorities)
  2. Generates a DemoPlan from the profile (KnowledgeGraph nodes, business processes, alerts, dashboards, incident script)
  3. Provisions the plan into Grafana Cloud (dashboards + alert rules + KG entities)
  4. Emits live telemetry (RED metrics, logs, traces, KG entity gauges) so a demo feels real

Conversation behaviour:
  * Answer the SE's question directly first, then explain if useful.
  * Don't dump raw JSON — summarize for a human (counts, top entries, key fields).
  * If the SE asks about specifics ("which plan", "this profile"), CALL THE TOOLS instead of guessing.
  * When you call a tool, briefly say what you're looking up ("Let me check the plans for that company…") before the call so the SE knows what's happening.
  * Reference entities by their short id + a human descriptor: "plan a8b3c2d1 (<company name from the profile>)" not just the UUID.

Vocabulary discipline (important):
  * Use ONLY the company names, vendors, products, and entity labels that actually appear in the scoped profile/plan (or that the SE typed). Never invent, assume, or suggest a real company, customer, or vendor name that isn't grounded in the data you've been given or fetched.
  * If you need a generic placeholder (illustrating a URL or a command format), use a neutral common name like "grafana.com" or "example.com" — never a real customer's name.
  * When scoped to a profile/plan, keep all wording aligned to that company and its components. Don't borrow terminology from other companies or earlier conversations.

Context awareness:
  * Each turn the SE sends includes a `context_scope` hint with whichever page they're on:
      { plan_id: "..." }    → /plans/<id>
      { profile_id: "..." } → /profiles/<id>
      { pipeline_id: "..." } → a build page
      { route: "..." }      → just the route, for general pages
    Use it to interpret "this plan" / "this profile" without forcing the SE to repeat the id.

What you can DO (you are an agent, not just a chat box — act, don't just advise):
  * run_build(url)             — start a brand-new end-to-end build from a company URL.
  * run_pipeline_phase(phase)  — run/RE-RUN a build from a phase. This is how you
                                 "execute the plan" or rebuild after refining:
                                   - phase='plan' re-plans from a profile,
                                   - phase='generate'/'provision'/'kg-publish' re-runs
                                     downstream steps on an existing plan,
                                   - phase='research' rebuilds everything.
  * extend_profile(id, prompt) — add research/entities/signals to a profile.
  * approve_plan(id)           — approve a draft plan for provisioning.
  * start_demo(id)/stop_demo(id) — control the live telemetry emitter.
  * cancel_build(pipeline_id) — cancel an in-flight BUILD. When the SE says
    "stop the build" / "cancel it", this is the tool (NOT stop_demo, which
    only stops live telemetry). If you don't have the pipeline_id, call
    list_pipelines and cancel the running one.
  * Plus read-only inspection: list_/get_ profiles, plans, pipelines, demo sessions, audit.

How to operate:
  * The SE's normal loop: a build runs, then they work WITH YOU to refine it. The
    typical refine is: extend_profile (if the change is about the company/research)
    then run_pipeline_phase(phase='plan') to regenerate the plan; or just
    run_pipeline_phase on a downstream phase if only generation/provisioning changed.
  * When the SE asks you to do something, DO IT by calling the tool — don't just
    describe the steps. Builds run in the background and are reversible, so don't
    over-ask for permission; for clearly destructive or surprising actions, confirm first.
  * After a build/extend/demo action, tell the SE what you did and give them the
    watch_url (e.g. /pipelines/<id>) so they can follow it.
  * Chain tools when it makes sense (extend → re-plan) within a single turn.
  * There is no delete tool — if the SE wants to delete a plan/profile, point them
    to the Delete button on the relevant page.
  * Builds may be gated: when the SE has approval mode on, run_build / run_pipeline_phase
    PAUSE for an explicit Approve before they actually start. So phrase build kickoffs as
    intent ("I'll start a full build for …") rather than claiming it already started. If a
    build comes back declined, acknowledge it and ask what they'd like to change — don't retry.
"""


# ──────────────────────────────────────────────────────────────────
# Request / response shapes
# ──────────────────────────────────────────────────────────────────


class ChatRequest(BaseModel):
    """One user message to the assistant."""
    model_config = {"extra": "forbid"}

    message: str = Field(..., min_length=1, description="The SE's prompt for this turn.")
    conversation_id: int | None = Field(
        default=None,
        description="Continue an existing thread, or omit to start a new one.",
    )
    context_scope: dict[str, Any] | None = Field(
        default=None,
        description=(
            "Page context at send time — e.g. {plan_id: ...} or {route: '/profiles'}. "
            "Persisted with the user turn so the agent can reason about 'this plan'."
        ),
    )
    auto_approve: bool = Field(
        default=False,
        description=(
            "When False (default), the agent pauses for explicit approval before "
            "running a build-kicking tool (run_build / run_pipeline_phase) and emits "
            "an `approval_required` event. When True, builds run without prompting."
        ),
    )


class ResumeRequest(BaseModel):
    """Resolve a pending build-approval: run it or decline it, then let
    the agent continue."""
    model_config = {"extra": "forbid"}

    decision: Literal["approve", "reject"] = Field(
        ..., description="'approve' runs the paused build tool(s); 'reject' declines them.",
    )
    auto_approve: bool = Field(
        default=False,
        description="Approval mode to apply to the CONTINUATION after this resume.",
    )


class AssistantTurnDTO(BaseModel):
    """One turn from the persisted conversation. Returned by GET endpoints."""

    turn_id:        int
    role:           str
    content:        str
    tool_calls:     list[dict[str, Any]] | None = None
    tool_results:   list[dict[str, Any]] | None = None
    context_scope:  dict[str, Any] | None = None
    tokens_in:      int | None = None
    tokens_out:     int | None = None
    created_at:     str


class AssistantConversationDTO(BaseModel):
    """Conversation row + its turns. Returned by GET /conversations/{cid}."""

    conversation_id: int
    actor:           str
    title:           str | None = None
    status:          str
    created_at:      str
    updated_at:      str
    last_message_at: str | None = None
    turns:           list[AssistantTurnDTO]


class AssistantConversationSummaryDTO(BaseModel):
    """Slim shape for the conversation picker list."""

    conversation_id: int
    title:           str | None = None
    status:          str
    created_at:      str
    last_message_at: str | None = None


# ──────────────────────────────────────────────────────────────────
# Conversation CRUD endpoints
# ──────────────────────────────────────────────────────────────────


@router.get("/conversations", response_model=list[AssistantConversationSummaryDTO])
def list_conversations(
    status: str = "active", limit: int = 50,
) -> list[AssistantConversationSummaryDTO]:
    """Newest-last-message-first list for the conversation picker."""
    with session_scope() as s:
        rows = AssistantConversationRepo().list_conversations(
            s, status=status, limit=limit,
        )
    return [
        AssistantConversationSummaryDTO(
            conversation_id=r["conversation_id"],
            title=r["title"],
            status=r["status"],
            created_at=r["created_at"].isoformat(),
            last_message_at=r["last_message_at"].isoformat() if r["last_message_at"] else None,
        )
        for r in rows
    ]


@router.get("/conversations/{conversation_id}", response_model=AssistantConversationDTO)
def get_conversation(conversation_id: int) -> AssistantConversationDTO:
    """Full conversation by id with all turns in order."""
    with session_scope() as s:
        conv = AssistantConversationRepo().get_conversation(s, conversation_id)
        if conv is None:
            raise HTTPException(status_code=404, detail=f"conversation {conversation_id} not found")
        turns = AssistantTurnRepo().list_turns(s, conversation_id)
    return AssistantConversationDTO(
        conversation_id=conv["conversation_id"],
        actor=conv["actor"],
        title=conv["title"],
        status=conv["status"],
        created_at=conv["created_at"].isoformat(),
        updated_at=conv["updated_at"].isoformat(),
        last_message_at=conv["last_message_at"].isoformat() if conv["last_message_at"] else None,
        turns=[
            AssistantTurnDTO(
                turn_id=t["turn_id"],
                role=t["role"],
                content=t["content"],
                tool_calls=t["tool_calls"],
                tool_results=t["tool_results"],
                context_scope=t["context_scope"],
                tokens_in=t["tokens_in"],
                tokens_out=t["tokens_out"],
                created_at=t["created_at"].isoformat(),
            )
            for t in turns
        ],
    )


@router.post("/conversations/{conversation_id}/archive", status_code=204)
def archive_conversation(conversation_id: int) -> None:
    """Soft-delete from the active picker. Turns are retained — open
    by id to view, or hit /conversations?status=archived to browse."""
    with session_scope() as s:
        if AssistantConversationRepo().get_conversation(s, conversation_id) is None:
            raise HTTPException(status_code=404, detail=f"conversation {conversation_id} not found")
        AssistantConversationRepo().archive_conversation(s, conversation_id)


# ──────────────────────────────────────────────────────────────────
# /chat — multi-turn tool-use loop, SSE
# ──────────────────────────────────────────────────────────────────


def _build_anthropic_messages(turns: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Reconstruct the Anthropic messages array from persisted turns.

    Mapping:
      * user turn       → {role:"user", content:str}
      * assistant turn  → {role:"assistant", content:[text_block, *tool_use_blocks]}
                          When there are no tool_calls, content collapses to str.
      * tool turn       → {role:"user", content:[tool_result_block, ...]}
                          (Anthropic treats tool results as a user message.)

    Skips empty turns (content == "" and no tool_calls/tool_results) —
    these can appear briefly between an assistant tool_use and the
    next iteration; we just don't ship them to the model.
    """
    msgs: list[dict[str, Any]] = []
    for t in turns:
        role = t["role"]
        content = t.get("content") or ""
        tool_calls = t.get("tool_calls")
        tool_results = t.get("tool_results")
        if role == "user":
            if not content:
                continue
            msgs.append({"role": "user", "content": content})
        elif role == "assistant":
            blocks: list[dict[str, Any]] = []
            if content:
                blocks.append({"type": "text", "text": content})
            for tc in (tool_calls or []):
                blocks.append({
                    "type": "tool_use",
                    "id":    tc["tool_use_id"],
                    "name":  tc["name"],
                    "input": tc.get("input") or {},
                })
            if not blocks:
                continue
            # Single text block can collapse to str — easier on the eyes
            # in DB / logs, but multi-block must stay as list.
            if len(blocks) == 1 and blocks[0]["type"] == "text":
                msgs.append({"role": "assistant", "content": blocks[0]["text"]})
            else:
                msgs.append({"role": "assistant", "content": blocks})
        elif role == "tool":
            blocks = []
            for tr in (tool_results or []):
                blocks.append({
                    "type": "tool_result",
                    "tool_use_id": tr["tool_use_id"],
                    "content":     tr["content"],
                    **({"is_error": True} if tr.get("is_error") else {}),
                })
            if not blocks:
                continue
            msgs.append({"role": "user", "content": blocks})
    return msgs


def _extract_assistant_blocks(final_message: Any) -> tuple[str, list[dict[str, Any]]]:
    """Pull narrative text + tool_use blocks out of the final message.
    Returns (text, tool_calls). Defensive against SDK shape drift."""
    text_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    for block in (getattr(final_message, "content", None) or []):
        btype = getattr(block, "type", None)
        if btype == "text":
            text_parts.append(getattr(block, "text", "") or "")
        elif btype == "tool_use":
            tool_calls.append({
                "tool_use_id": getattr(block, "id", ""),
                "name":        getattr(block, "name", ""),
                "input":       getattr(block, "input", None) or {},
            })
    return ("".join(text_parts), tool_calls)


def _summarize_result_for_event(result: Any) -> str:
    """Compact one-liner about a tool result for the SSE tool_result
    event. Full result still goes back to Claude — this is just for
    the UI to render a 'tool returned 3 rows' badge."""
    if isinstance(result, list):
        return f"{len(result)} row{'s' if len(result) != 1 else ''}"
    if isinstance(result, dict):
        if "error" in result and len(result) == 1:
            return f"error: {result['error'][:80]}"
        return f"dict with {len(result)} field{'s' if len(result) != 1 else ''}"
    return str(result)[:80]


@router.post("/chat")
def clarion_chat(body: ChatRequest) -> EventSourceResponse:
    """Send a message; stream the agent's response with tool-use loop.

    Find-or-create the conversation, persist the user turn up front
    (durable on LLM failure), then iterate the agent loop emitting
    SSE events as turns land.
    """
    # 1. Open / continue conversation. Persist user turn before the
    # LLM call so it's durable on failure.
    with session_scope() as s:
        crepo = AssistantConversationRepo()
        if body.conversation_id is None:
            conv = crepo.create_conversation(s, actor="se")
        else:
            conv = crepo.get_conversation(s, body.conversation_id)
            if conv is None:
                raise HTTPException(
                    status_code=404,
                    detail=f"conversation {body.conversation_id} not found",
                )
            if conv["status"] != "active":
                raise HTTPException(
                    status_code=409,
                    detail=f"conversation is {conv['status']!r}; cannot continue",
                )
        conversation_id = conv["conversation_id"]
        existing_title = conv["title"]
        AssistantTurnRepo().append_turn(
            s, conversation_id,
            role="user",
            content=body.message,
            context_scope=body.context_scope,
        )
        crepo.touch_last_message(s, conversation_id)

    return _stream_chat(
        conversation_id=conversation_id,
        existing_title=existing_title,
        auto_approve=body.auto_approve,
    )


@router.post("/conversations/{conversation_id}/resume")
def resume_conversation(conversation_id: int, body: ResumeRequest) -> EventSourceResponse:
    """Resolve a build that's paused awaiting approval.

    `approve` runs the pending build tool(s); `reject` declines them. Either
    way the agent loop then continues so the assistant can react (report the
    watch link, or acknowledge the decline). Streams the same SSE events as
    /chat."""
    with session_scope() as s:
        conv = AssistantConversationRepo().get_conversation(s, conversation_id)
        if conv is None:
            raise HTTPException(status_code=404, detail=f"conversation {conversation_id} not found")
        if conv["status"] != "active":
            raise HTTPException(status_code=409, detail=f"conversation is {conv['status']!r}; cannot continue")
        turns = AssistantTurnRepo().list_turns(s, conversation_id)
        existing_title = conv["title"]
    if _pending_tool_calls(turns) is None:
        raise HTTPException(status_code=409, detail="no pending approval to resume")
    return _stream_chat(
        conversation_id=conversation_id,
        existing_title=existing_title,
        auto_approve=body.auto_approve,
        resume_decision=body.decision,
    )


def _pending_tool_calls(turns: list[dict[str, Any]]) -> list[dict[str, Any]] | None:
    """If the conversation is paused awaiting build approval, return the
    unanswered tool_calls. The marker is: the last turn is an assistant
    turn carrying tool_calls with no following tool turn. Returns None
    when there's nothing pending."""
    if not turns:
        return None
    last = turns[-1]
    if last["role"] == "assistant" and last.get("tool_calls"):
        return last["tool_calls"]
    return None


def _approval_message(call: dict[str, Any]) -> str:
    """Human one-liner for the approval card."""
    name = call.get("name")
    inp = call.get("input") or {}
    if name == "run_build":
        return f"Start a full build for {inp.get('url') or 'this company'}?"
    if name == "run_pipeline_phase":
        phase = inp.get("phase") or "a"
        return f"Run the '{phase}' build phase?"
    return f"Run {name}?"


def _stream_chat(
    *, conversation_id: int, existing_title: str | None,
    auto_approve: bool, resume_decision: str | None = None,
) -> EventSourceResponse:
    """The agent loop, wrapped in an SSE response.

    `resume_decision` (approve/reject) is set only when continuing a
    build-approval pause: the pending assistant turn's tools are resolved
    first, then the normal iteration loop runs so the model reacts to the
    result."""
    client = _client()
    model = _model()

    async def event_gen() -> object:
        try:
            from proj_clarion.observability.llm_client import stream_anthropic

            final_iter_text = ""
            final_tokens_in: int | None = None
            final_tokens_out: int | None = None
            paused = False

            async def resolve_tools(calls: list[dict[str, Any]], *, decline: bool):
                """Emit + execute (or decline) a batch of tool calls, then
                persist the tool turn. Shared by the normal loop and the
                resume path."""
                results: list[dict[str, Any]] = []
                for call in calls:
                    yield {
                        "event": "tool_call",
                        "data": json.dumps({
                            "tool_use_id": call["tool_use_id"],
                            "name":        call["name"],
                            "input":       call["input"],
                            "mutating":    call["name"] in MUTATING_TOOL_NAMES,
                        }),
                    }
                    # Trace every tool the same way build phases trace their
                    # work — one span per tool, with gen_ai.* + clarion.* attrs.
                    with _tracer.start_as_current_span(f"execute_tool {call['name']}") as _tspan:
                        _tspan.set_attribute("gen_ai.operation.name", "execute_tool")
                        _tspan.set_attribute("gen_ai.tool.name", call["name"])
                        _tspan.set_attribute("gen_ai.tool.call.id", call["tool_use_id"])
                        _tspan.set_attribute(
                            "clarion.assistant.tool.mutating",
                            call["name"] in MUTATING_TOOL_NAMES,
                        )
                        if decline:
                            _tspan.set_attribute("clarion.assistant.tool.declined", True)
                            result: Any = {
                                "declined": True,
                                "message": (
                                    f"The SE declined to run {call['name']}. It was NOT "
                                    "executed. Acknowledge and ask what they'd like to adjust "
                                    "— do not retry without a new instruction."
                                ),
                            }
                            is_error = False
                        else:
                            with session_scope() as s:
                                result, is_error = execute_tool(call["name"], call["input"], s)
                            _tspan.set_attribute("clarion.assistant.tool.is_error", is_error)
                            if is_error:
                                _tspan.set_status(
                                    trace.Status(trace.StatusCode.ERROR, "tool returned an error"),
                                )

                    result_str = (
                        result if isinstance(result, str)
                        else json.dumps(result, default=str)
                    )
                    results.append({
                        "tool_use_id": call["tool_use_id"],
                        "content":     result_str,
                        "is_error":    is_error,
                    })
                    detail: dict[str, Any] = {}
                    if isinstance(result, dict):
                        for k in (
                            "message", "watch_url", "pipeline_id", "plan_id",
                            "profile_id", "status", "phase", "summary",
                        ):
                            v = result.get(k)
                            if v is not None:
                                detail[k] = v
                    yield {
                        "event": "tool_result",
                        "data": json.dumps({
                            "tool_use_id": call["tool_use_id"],
                            "summary":     _summarize_result_for_event(result),
                            "is_error":    is_error,
                            "detail":      detail,
                        }),
                    }
                with session_scope() as s:
                    AssistantTurnRepo().append_turn(
                        s, conversation_id,
                        role="tool", content="", tool_results=results,
                    )
                    AssistantConversationRepo().touch_last_message(s, conversation_id)

            with _tracer.start_as_current_span("assistant.conversation") as _conv_span:
                _conv_span.set_attribute("gen_ai.operation.name", "chat")
                _conv_span.set_attribute("gen_ai.agent.name", "clarion.assistant")
                _conv_span.set_attribute("gen_ai.conversation.id", str(conversation_id))
                _conv_span.set_attribute("clarion.assistant.conversation_id", conversation_id)
                _conv_span.set_attribute("clarion.assistant.mode", "resume" if resume_decision else "chat")
                _conv_span.set_attribute("clarion.assistant.auto_approve", auto_approve)
                if resume_decision:
                    _conv_span.set_attribute("clarion.assistant.resume_decision", resume_decision)
                # ── Resume path: resolve the paused build approval first, then
                # fall through to the loop so the model reacts to the result. ──
                if resume_decision is not None:
                    with session_scope() as s:
                        turns0 = AssistantTurnRepo().list_turns(s, conversation_id)
                    pending = _pending_tool_calls(turns0)
                    if pending:
                        async for ev in resolve_tools(pending, decline=(resume_decision == "reject")):
                            yield ev

                for _iteration in range(AGENT_MAX_ITERATIONS):
                    # Rebuild messages from the current state of the conversation
                    # on each iteration — tool turns may have been just added.
                    with session_scope() as s:
                        turns = AssistantTurnRepo().list_turns(s, conversation_id)
                    messages = _build_anthropic_messages(turns)

                    request: dict[str, Any] = {
                        "model": model,
                        "max_tokens": 4096,
                        "system": _CLARION_SYSTEM,
                        "messages": messages,
                        "tools": TOOLS_ALL,
                        "tool_choice": {"type": "auto"},
                    }

                    iter_text = ""
                    tool_calls: list[dict[str, Any]] = []
                    tokens_in = None
                    tokens_out = None

                    with stream_anthropic(
                        client, request,
                        agent_name="clarion.assistant",
                        prompt_template="clarion.assistant",
                        conversation_id=str(conversation_id),
                    ) as stream:
                        for chunk in stream.text_stream:
                            iter_text += chunk
                            yield {"event": "delta", "data": chunk}
                        try:
                            final = stream.get_final_message()
                            iter_text_final, tool_calls = _extract_assistant_blocks(final)
                            # Prefer the assembled text from the final message —
                            # it's the canonical value (handles edge cases where
                            # text_stream might miss tail chunks).
                            if iter_text_final:
                                iter_text = iter_text_final
                            usage = getattr(final, "usage", None)
                            if usage is not None:
                                tokens_in = getattr(usage, "input_tokens", None)
                                tokens_out = getattr(usage, "output_tokens", None)
                        except Exception as exc:  # noqa: BLE001
                            _logger.warning(
                                "clarion.assistant.final_unavailable",
                                conversation_id=conversation_id, error=str(exc),
                            )

                    # Persist assistant turn (text + any tool_calls).
                    with session_scope() as s:
                        AssistantTurnRepo().append_turn(
                            s, conversation_id,
                            role="assistant",
                            content=iter_text,
                            tool_calls=tool_calls if tool_calls else None,
                            tokens_in=tokens_in,
                            tokens_out=tokens_out,
                        )
                        AssistantConversationRepo().touch_last_message(s, conversation_id)

                    final_iter_text = iter_text
                    final_tokens_in = tokens_in
                    final_tokens_out = tokens_out

                    # If no tool calls, we're done.
                    if not tool_calls:
                        break

                    # ── Approval gate ── Pause before kicking off a build unless
                    # the SE has auto-approve on. The assistant turn (with the
                    # tool_use) is already persisted; we leave it unanswered and
                    # stop. The SE resolves it via the /resume endpoint, which
                    # runs or declines the pending tool and continues the loop.
                    if (not auto_approve) and any(
                        c["name"] in NEEDS_APPROVAL_TOOL_NAMES for c in tool_calls
                    ):
                        build_call = next(
                            c for c in tool_calls if c["name"] in NEEDS_APPROVAL_TOOL_NAMES
                        )
                        yield {
                            "event": "approval_required",
                            "data": json.dumps({
                                "conversation_id": conversation_id,
                                "tool_use_id":     build_call["tool_use_id"],
                                "name":            build_call["name"],
                                "input":           build_call["input"],
                                "message":         _approval_message(build_call),
                            }),
                        }
                        paused = True
                        break

                    async for ev in resolve_tools(tool_calls, decline=False):
                        yield ev
                    # Loop: next iteration will rebuild messages and call Claude
                    # again with the tool_result in context.
                else:
                    # for/else: we hit the iteration cap without breaking.
                    _logger.warning(
                        "clarion.assistant.max_iterations_reached",
                        conversation_id=conversation_id,
                    )

                # Auto-generate title on first exchange if it's still null.
                if existing_title is None and final_iter_text:
                    title = _autotitle(client, model, conversation_id)
                    if title:
                        with session_scope() as s:
                            AssistantConversationRepo().update_title(s, conversation_id, title)

                yield {
                    "event": "done",
                    "data": json.dumps({
                        "conversation_id":   conversation_id,
                        "tokens_in":         final_tokens_in,
                        "tokens_out":        final_tokens_out,
                        "awaiting_approval": paused,
                    }),
                }
        except Exception as exc:  # noqa: BLE001
            _logger.exception(
                "clarion.assistant.failed",
                conversation_id=conversation_id,
            )
            yield {"event": "error", "data": str(exc)}

    return EventSourceResponse(event_gen())


def _autotitle(client: Anthropic, model: str, conversation_id: int) -> str | None:
    """Generate a short title for the conversation picker. Uses the
    SE's first user prompt as the basis — the assistant's reply is
    sometimes empty (tool-only turn) or includes refusals that bleed
    into the title if included. The user's question is reliable.

    Returns None on any error; the picker falls back to "Untitled
    conversation · <timestamp>"."""
    with session_scope() as s:
        turns = AssistantTurnRepo().list_turns(s, conversation_id)
    first_user = next(
        (t for t in turns if t["role"] == "user" and t.get("content")),
        None,
    )
    if first_user is None:
        return None
    prompt = first_user["content"][:500]
    try:
        msg = client.messages.create(
            model=model,
            max_tokens=30,
            system=(
                "You generate concise titles. The user gives you a question "
                "they asked an assistant. Output a 3–6 word title summarizing "
                "WHAT they asked about (not an answer). Examples:\n"
                "  Input:  'How many profiles do I have?'\n"
                "  Output: Profile count check\n"
                "  Input:  'Why did the plan build fail for Grafana?'\n"
                "  Output: Grafana build failure\n"
                "Output ONLY the title — no quotes, no preamble, no period."
            ),
            messages=[{"role": "user", "content": prompt}],
        )
        for block in (msg.content or []):
            if getattr(block, "type", None) == "text":
                title = (getattr(block, "text", "") or "").strip().strip('"').strip("'").rstrip(".")
                # Reject the title if the model went off the rails and
                # produced something obviously too long or that looks
                # like an answer rather than a title.
                if title and len(title) <= 80 and "\n" not in title:
                    return title
    except Exception as exc:  # noqa: BLE001
        _logger.debug("clarion.assistant.autotitle_failed", error=str(exc))
    return None
