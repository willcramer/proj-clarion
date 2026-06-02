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
import re
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Body, HTTPException
from pydantic import BaseModel, Field
from sse_starlette.sse import EventSourceResponse

from proj_clarion.api.pipeline_registry import (
    cancel_pipeline,
    continue_pipeline,
    delete_pipeline,
    prune_pipelines,
    get_pipeline,
    get_pipeline_events,
    list_pipelines,
    list_pipelines_for,
    run_or_resume_phase,
    start_pipeline,
    start_pipeline_from_phase,
    stream_pipeline,
    PipelineState,
)
from proj_clarion.api.url_input import URLValidationError, normalize_company_url
from proj_clarion.storage import PipelineRepo, ProfileRepo, session_scope

router = APIRouter(prefix="/api/pipelines", tags=["pipelines"])


def _norm_host(s: str) -> str:
    """Canonical host for dedup: lowercased, scheme/www/path stripped."""
    s = (s or "").strip().lower()
    s = re.sub(r"^https?://", "", s)
    s = re.sub(r"^www\.", "", s)
    return s.split("/", 1)[0]


def _existing_profile_for_host(host: str) -> str | None:
    """Return the profile_id of an existing CompanyProfile whose source
    URL resolves to `host`, or None. Bounded scan — the library is small
    and this only runs on a build kickoff."""
    if not host:
        return None
    with session_scope() as s:
        for pid, _created, source_url in ProfileRepo().list(s, limit=1000):
            if _norm_host(source_url) == host:
                return pid
    return None

PhaseName = Literal["research", "plan", "approve", "generate", "provision", "kg-publish"]


class RunPipelineBody(BaseModel):
    url: str
    company: str | None = None
    days: int = 1
    # SE-supplied event volume override. None → planner auto-scales by
    # channel count (capped at 5K/day for safe defaults). Use 500 for
    # a smoke build that finishes in a minute.
    volume_per_day: int | None = Field(default=None, ge=100, le=100_000)
    # Optional cut-off — when set, the orchestrator stops after this
    # phase completes successfully. Use "research" for the "Just add
    # profile" flow (research the URL, store the profile, don't plan /
    # generate / provision). None = run the whole pipeline.
    stop_after_phase: PhaseName | None = None
    # Dedup backstop: a full build runs research, which would create a
    # SECOND profile for a company already in the library. We 409 in that
    # case unless the caller explicitly opts in here ("Build new anyway").
    allow_duplicate: bool = False


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
    # Optional cut-off (same semantics as RunPipelineBody). The UI sets
    # this to "plan" when building a plan from a profile so nothing is
    # provisioned until the SE approves.
    stop_after_phase: PhaseName | None = None


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
    # Phase rollup. The Builds page renders these as a per-row progress
    # bar (phases_done / 6) plus the active phase name when running.
    # Populated by PipelineRepo.list(); the inline _summarise() helpers
    # leave these at defaults until the row is persisted.
    phases_done: int = 0
    current_phase: str | None = None


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

    # Dedup backstop (also enforced client-side). A /run build always
    # researches, which would mint a second profile for a company we
    # already have. Block it with a 409 (carrying the existing profile_id)
    # unless the caller explicitly opted into a duplicate.
    if not body.allow_duplicate:
        host = _norm_host(normalized.url)
        existing = _existing_profile_for_host(host)
        if existing:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"A profile for {host} already exists ({existing}). "
                    f"Open it, build from it, or retry with allow_duplicate=true."
                ),
            )

    state = start_pipeline(
        normalized.url, body.company, days=body.days,
        volume_per_day=body.volume_per_day,
        stop_after_phase=body.stop_after_phase,
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
    # Resolve url/company from the plan's profile (or the profile directly)
    # when not supplied — parity with the assistant tool, so a re-plan by
    # profile_id alone works and reuses the existing build. (str-coerced to
    # avoid the HttpUrl psycopg adapter crash.)
    if not url and (body.profile_id or body.plan_id):
        from proj_clarion.agents.clarion_tools import _resolve_build_target
        with session_scope() as s:
            r_url, r_company, _ = _resolve_build_target(
                s, plan_id=body.plan_id, profile_id=body.profile_id,
            )
        url = url or r_url
        company = company or r_company
    if not url:
        raise HTTPException(
            status_code=400,
            detail="url is required (or a profile_id/plan_id/parent we can derive it from)",
        )
    # Same lenient normalization as /run. Inherited URLs from the
    # parent pipeline are already canonical, but pass them through
    # anyway — idempotent and protects against legacy DB rows with
    # non-canonical URLs.
    try:
        url = normalize_company_url(url).url
    except URLValidationError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    # ONE build per profile/plan: resume the canonical pipeline IN PLACE
    # (same pipeline_id) when one exists for this profile/plan, instead of
    # forking a sibling. So the UI "Re-run"/resume reuses the build — no new
    # id, no DB clutter. Falls back to a fresh row for a profile_path-only
    # resume (no ids to dedupe on) or a genuinely different URL.
    if body.profile_id or body.plan_id:
        state, _reused = run_or_resume_phase(
            starting_phase=body.phase,
            url=url, company=company, days=days,
            profile_id=body.profile_id,
            plan_id=body.plan_id,
            volume_per_day=body.volume_per_day,
            stop_after_phase=body.stop_after_phase,
        )
    else:
        state = start_pipeline_from_phase(
            starting_phase=body.phase,
            url=url, company=company, days=days,
            profile_id=body.profile_id,
            profile_path=body.profile_path,
            plan_id=body.plan_id,
            parent_pipeline_id=body.parent_pipeline_id,
            volume_per_day=body.volume_per_day,
            stop_after_phase=body.stop_after_phase,
        )
    return _summarise(state)


class ContinueBody(BaseModel):
    """Resume an existing build in place (same pipeline_id). Defaults to the
    approval gate: approve the plan, then generate → provision → kg-publish.
    plan_id/profile_id are inherited from the pipeline row when omitted."""

    starting_phase: PhaseName = "approve"
    stop_after_phase: PhaseName | None = None
    plan_id: str | None = None
    profile_id: str | None = None


@router.post("/{pipeline_id}/continue", response_model=PipelineSummary)
async def continue_endpoint(
    pipeline_id: str, body: ContinueBody = Body(default=ContinueBody()),
) -> PipelineSummary:
    """Approve + provision (or resume from any later phase) by appending to
    the SAME build — no second pipeline row. This is the deterministic
    'I clicked Approve and the build just continues' path.

    Must be `async` (not sync): continue_pipeline schedules the runner via
    asyncio.create_task, which requires the event-loop thread. A sync route
    runs in a threadpool with no running loop and would 500."""
    try:
        state = continue_pipeline(
            pipeline_id,
            starting_phase=body.starting_phase,
            stop_after_phase=body.stop_after_phase,
            plan_id=body.plan_id,
            profile_id=body.profile_id,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if state is None:
        raise HTTPException(status_code=404, detail=f"pipeline {pipeline_id} not found")
    return _summarise(state)


@router.get("", response_model=list[PipelineSummary])
def list_endpoint(
    profile_id: str | None = None,
    plan_id: str | None = None,
    status: str | None = None,
) -> list[PipelineSummary]:
    """Newest first. DB-backed, survives API restart.

    Optional `?profile_id=&plan_id=&status=` filters let the UI/agent check
    'is there already a (running) build for this profile?' — the basis for
    one-build-per-profile reuse."""
    filtered = profile_id is not None or plan_id is not None or status is not None
    pipelines = (
        list_pipelines_for(profile_id=profile_id, plan_id=plan_id, status=status, limit=200)
        if filtered else list_pipelines()
    )
    if not pipelines:
        return []
    # One batch pull from the DB enriches every row with event count,
    # completed-phase count, and current running phase. The Builds list
    # uses the phase fields for the inline progress bar.
    with session_scope() as s:
        repo = PipelineRepo()
        rows = repo.list(s, limit=200, profile_id=profile_id, plan_id=plan_id, status=status)
    by_id = {r["pipeline_id"]: r for r in rows}
    out: list[PipelineSummary] = []
    for p in pipelines:
        row = by_id.get(p.pipeline_id, {})
        summary = _summarise(p, event_count=row.get("event_count", 0))
        summary.phases_done = int(row.get("phases_done") or 0)
        summary.current_phase = row.get("current_phase")
        out.append(summary)
    return out


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


@router.post("/prune")
def prune_endpoint() -> dict[str, int]:
    """Bulk-remove failed + cancelled builds (their rows + events/phases)
    so the Builds list isn't cluttered with dead/reload-casualty runs.
    Never touches running or done builds."""
    return {"deleted": prune_pipelines(("failed", "cancelled"))}


@router.delete("/{pipeline_id}")
def delete_endpoint(pipeline_id: str) -> dict[str, bool]:
    """Hard-delete a build (cancels it first if still running, then drops
    the row + its events/phases). The escape hatch for a wedged/runaway
    build that won't converge. Leaves the produced plan/profile intact —
    use their own deletes (with Cloud cleanup) to clear provisioned
    Grafana folders + alerts."""
    if get_pipeline(pipeline_id) is None:
        raise HTTPException(status_code=404, detail=f"pipeline {pipeline_id} not found")
    return {"deleted": delete_pipeline(pipeline_id)}
