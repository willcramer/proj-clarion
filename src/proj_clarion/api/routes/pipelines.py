"""End-to-end demo pipeline routes.

Server-managed: pipelines run as background tasks keyed by ID and are
persisted to Postgres so history survives API restarts. SSE endpoint
replays buffered events from the start of a pipeline so a late-arriving
client catches up.

v0.8: + persistence (`pipelines`, `pipeline_events`, `pipeline_phases`)
      + resume-from-phase (`POST /api/pipelines/run-from-phase`)
      + per-phase rollup (`GET /api/pipelines/{id}/phases`)
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from proj_clarion.api.pipeline_registry import (
    cancel_pipeline,
    get_pipeline,
    get_pipeline_events,
    list_pipelines,
    start_pipeline,
    start_pipeline_from_phase,
    stream_pipeline,
    PipelineState,
)
from proj_clarion.api.url_input import URLValidationError, normalize_company_url
from proj_clarion.storage import PipelineRepo, session_scope

router = APIRouter(prefix="/api/pipelines", tags=["pipelines"])

PhaseName = Literal["research", "plan", "approve", "generate", "provision", "kg-publish"]


class RunPipelineBody(BaseModel):
    url: str
    company: str | None = None
    days: int = 1
    # SE-supplied event volume override. None → planner auto-scales by
    # channel count (capped at 5K/day for safe defaults). Use 500 for
    # a smoke build that finishes in a minute.
    volume_per_day: int | None = Field(default=None, ge=100, le=100_000)


class RunFromPhaseBody(BaseModel):
    """Resume a build from a specific phase. Caller supplies whatever
    inputs the chosen phase needs:
      - phase='research' → url + company
      - phase='plan' → profile_id (or profile_path)
      - phase∈{approve, generate, provision, kg-publish} → plan_id

    `parent_pipeline_id` lets the UI link the resume run back to the
    failed origin run for traceability."""

    phase: PhaseName
    url: str | None = None
    company: str | None = None
    days: int = 1
    profile_id: str | None = None
    profile_path: str | None = None
    plan_id: str | None = None
    parent_pipeline_id: str | None = None
    volume_per_day: int | None = Field(default=None, ge=100, le=100_000)


class PipelineSummary(BaseModel):
    pipeline_id: str
    url: str
    company: str | None
    days: int
    started_at: datetime
    finished_at: datetime | None
    status: str
    error: str | None
    event_count: int = 0
    # Resume metadata (None when this build started from research)
    trigger: str = "full"
    starting_phase: str | None = None
    parent_pipeline_id: str | None = None
    # Linked artifacts produced by this build (set as phases complete)
    profile_id: str | None = None
    plan_id: str | None = None
    # Set when the URL the SE submitted was rewritten (trailing slash
    # stripped, scheme added, path tail removed, etc.). Carries the
    # human-readable list of what changed so the UI can surface a small
    # "we used X" notice on the new build card.
    url_normalized_from: str | None = None
    url_normalization_hints: list[str] = Field(default_factory=list)


class PhaseRow(BaseModel):
    phase: str
    status: str
    started_at: datetime | None
    finished_at: datetime | None
    error: str | None
    artifact: dict[str, Any] | None


def _summarise(state: PipelineState, *, event_count: int = 0) -> PipelineSummary:
    return PipelineSummary(
        pipeline_id=state.pipeline_id,
        url=state.url,
        company=state.company,
        days=state.days,
        started_at=state.started_at,
        finished_at=state.finished_at,
        status=state.status,
        error=state.error,
        event_count=event_count,
        trigger=state.trigger,
        starting_phase=state.starting_phase,
        parent_pipeline_id=state.parent_pipeline_id,
        profile_id=state.profile_id,
        plan_id=state.plan_id,
    )


@router.post("/run", response_model=PipelineSummary)
async def run(body: RunPipelineBody = Body(...)) -> PipelineSummary:
    """Start a full build (research → kg-publish). Returns immediately;
    follow via /stream.

    `async def` is load-bearing: start_pipeline calls
    asyncio.create_task, which requires a running event loop. A sync
    FastAPI route runs in a thread pool so it would crash here.
    """
    # Coerce common URL variations (trailing slash, missing scheme,
    # path tails, copy-paste whitespace) into a canonical form instead
    # of failing the build with a Pydantic ValidationError. The hints
    # list is empty when the input was already canonical.
    try:
        normalized = normalize_company_url(body.url)
    except URLValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    state = start_pipeline(
        normalized.url, body.company, days=body.days,
        volume_per_day=body.volume_per_day,
    )
    summary = _summarise(state)
    if normalized.hints:
        summary.url_normalized_from = normalized.original
        summary.url_normalization_hints = normalized.hints
    return summary


@router.post("/run-from-phase", response_model=PipelineSummary)
async def run_from_phase(body: RunFromPhaseBody = Body(...)) -> PipelineSummary:
    """Resume from a specific phase. Inherits whatever's already done
    (profile/plan), runs only the requested phase + everything after."""
    # Validate inputs early so the UI gets a 400 with a clear message.
    if body.phase != "research" and not body.profile_id and not body.profile_path \
            and body.phase == "plan":
        raise HTTPException(
            status_code=400,
            detail="phase='plan' requires profile_id or profile_path",
        )
    if body.phase in ("approve", "generate", "provision", "kg-publish") \
            and not body.plan_id:
        raise HTTPException(
            status_code=400,
            detail=f"phase={body.phase!r} requires plan_id",
        )

    # Inherit url/company/days from parent if available so UI doesn't
    # have to round-trip them.
    url = body.url
    company = body.company
    days = body.days
    if body.parent_pipeline_id:
        with session_scope() as s:
            parent = PipelineRepo().get(s, body.parent_pipeline_id)
        if parent is not None:
            url = url or parent["url"]
            company = company or parent["company"]
            # Keep the requested days even if parent had a different value
    if not url:
        raise HTTPException(
            status_code=400,
            detail="url is required (or parent_pipeline_id whose url we can inherit)",
        )
    # Same lenient normalization as /run. Inherited URLs from the
    # parent pipeline are already canonical, but pass them through
    # anyway — idempotent and protects against legacy DB rows with
    # non-canonical URLs.
    try:
        url = normalize_company_url(url).url
    except URLValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    state = start_pipeline_from_phase(
        starting_phase=body.phase,
        url=url, company=company, days=days,
        profile_id=body.profile_id,
        profile_path=body.profile_path,
        plan_id=body.plan_id,
        parent_pipeline_id=body.parent_pipeline_id,
        volume_per_day=body.volume_per_day,
    )
    return _summarise(state)


@router.get("", response_model=list[PipelineSummary])
def list_endpoint() -> list[PipelineSummary]:
    """Newest first. DB-backed — survives API restart."""
    pipelines = list_pipelines()
    if not pipelines:
        return []
    # Pull event counts in one batch so we don't N+1 the DB.
    with session_scope() as s:
        repo = PipelineRepo()
        rows = repo.list(s, limit=200)
    counts = {r["pipeline_id"]: r.get("event_count", 0) for r in rows}
    return [
        _summarise(p, event_count=counts.get(p.pipeline_id, 0))
        for p in pipelines
    ]


@router.get("/{pipeline_id}", response_model=PipelineSummary)
def get_endpoint(pipeline_id: str) -> PipelineSummary:
    state = get_pipeline(pipeline_id)
    if state is None:
        raise HTTPException(status_code=404, detail=f"pipeline {pipeline_id} not found")
    with session_scope() as s:
        n = PipelineRepo().event_count(s, pipeline_id)
    return _summarise(state, event_count=n)


@router.get("/{pipeline_id}/events")
def get_events(pipeline_id: str) -> dict[str, Any]:
    """One-shot snapshot of every event so far. Useful for clients that
    just want the current state without subscribing to a stream
    (e.g. on initial page render before the SSE connects)."""
    events = get_pipeline_events(pipeline_id)
    if events is None:
        raise HTTPException(status_code=404, detail=f"pipeline {pipeline_id} not found")
    state = get_pipeline(pipeline_id)
    return {
        "pipeline_id": pipeline_id,
        "status": state.status if state else None,
        "events": events,
    }


@router.get("/{pipeline_id}/phases", response_model=list[PhaseRow])
def get_phases(pipeline_id: str) -> list[PhaseRow]:
    """Per-phase rollup so the UI doesn't have to re-aggregate from events.
    Includes start/finish timestamps, status, error, and the produced
    artifact (profile_id / plan_id) for the phase that yielded one."""
    if get_pipeline(pipeline_id) is None:
        raise HTTPException(status_code=404, detail=f"pipeline {pipeline_id} not found")
    with session_scope() as s:
        rows = PipelineRepo().phases(s, pipeline_id)
    return [PhaseRow(**r) for r in rows]


@router.get("/{pipeline_id}/stream")
async def stream_endpoint(pipeline_id: str) -> EventSourceResponse:
    """SSE: replays buffered events from the beginning, then tails new
    ones. Terminates when the pipeline reaches a terminal status."""
    if get_pipeline(pipeline_id) is None:
        raise HTTPException(status_code=404, detail=f"pipeline {pipeline_id} not found")

    async def event_gen() -> object:
        async for ev in stream_pipeline(pipeline_id):
            event_name = ev.get("event", "message")
            yield {"event": event_name, "data": json.dumps(ev)}

    return EventSourceResponse(event_gen())


@router.post("/{pipeline_id}/cancel")
def cancel_endpoint(pipeline_id: str) -> dict[str, bool]:
    if get_pipeline(pipeline_id) is None:
        raise HTTPException(status_code=404, detail=f"pipeline {pipeline_id} not found")
    return {"cancelled": cancel_pipeline(pipeline_id)}
