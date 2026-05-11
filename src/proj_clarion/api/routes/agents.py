"""SE↔agent chat endpoints.

Two surfaces:
- `POST /api/agents/research/extend`   — extend a CompanyProfile with more
  research grounded on the existing one + the SE's prompt. Streams the
  agent's response.
- `POST /api/agents/plan/refine`       — ask the planner agent to
  reconsider a section of the DemoPlan given SE feedback.

Both stream via SSE. v0.7 keeps these read-only — the agent's response
is shown to the SE; persisting changes back to Postgres is explicit
(separate endpoint, not yet wired) so we don't silently mutate plans.

Sigil instrumentation isn't on these calls — the `sigil_helper.call_anthropic`
wrapper doesn't yet support streaming. The instrumented CLI calls
(plan run / research) still pass through it; the UI's interactive chat
takes the streaming path directly. v0.8 follow-up.
"""

from __future__ import annotations

import os
from typing import Any

from anthropic import Anthropic
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from proj_clarion.api.routes.plans import _resolve_plan_id
from proj_clarion.storage import PlanRepo, ProfileRepo, session_scope

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

The SE may ask you to:
- Reconsider a process, failure mode, or alert.
- Suggest dashboards or KG nodes that would make the demo more
  vertical-specific.
- Tighten the incident script's pacing.

The current DemoPlan is provided below as JSON. Refer to it by ids
(plan_id, process_id, alert_id, etc.) so the SE can map your
suggestions back. Do not produce JSON in your response unless the SE
explicitly asks — they want narrative guidance they can review.
"""


def _build_messages(history: list[dict[str, str]]) -> list[dict[str, Any]]:
    """Anthropic API expects {role, content} only and rejects unknown keys."""
    return [
        {"role": h["role"], "content": h["content"]}
        for h in history
        if h.get("role") in ("user", "assistant") and h.get("content")
    ]


def _stream_response(system: str, messages: list[dict[str, Any]]) -> EventSourceResponse:
    """Token-by-token SSE stream from Anthropic.

    The Anthropic stream context manager yields TextDelta events when the
    model emits text, plus other events (tool calls etc.) we don't use
    yet. We forward only the text deltas to the UI.
    """
    client = _client()

    async def event_gen() -> object:
        try:
            with client.messages.stream(
                model=_model(),
                max_tokens=2048,
                system=system,
                messages=messages,
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
    return _stream_response(system, _build_messages(body.history))


@router.post("/plan/refine")
def plan_refine(body: ChatBody) -> EventSourceResponse:
    """Stream a planner-agent response grounded on a DemoPlan."""
    with session_scope() as s:
        full_id = _resolve_plan_id(s, body.context_id)
        if not full_id:
            raise HTTPException(status_code=404, detail=f"plan {body.context_id} not found")
        plan = PlanRepo().get(s, full_id)
    if plan is None:
        raise HTTPException(status_code=404, detail=f"plan {full_id} not found")

    system = (
        _PLAN_SYSTEM
        + "\n\n=== Current DemoPlan ===\n"
        + plan.model_dump_json(indent=2)
    )
    return _stream_response(system, _build_messages(body.history))
