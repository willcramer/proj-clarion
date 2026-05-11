"""Run a Clarion CLI command from the UI; stream its log over SSE.

Endpoints:
- POST /api/runs                  — start a run, return its run_id
- GET  /api/runs                  — list all runs in this API process
- GET  /api/runs/{id}             — single run status
- GET  /api/runs/{id}/stream      — SSE: log lines, terminator on exit
- POST /api/runs/{id}/cancel      — SIGTERM the subprocess

The "live tail" CLI command is itself long-running and the SSE stream
is the right shape for watching its output.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sse_starlette.sse import EventSourceResponse

from proj_clarion.api.runner import (
    ALLOWED_RUNS,
    RunRequest,
    cancel_run,
    get_run,
    list_runs,
    start_run,
    stream_run_lines,
)

router = APIRouter(prefix="/api/runs", tags=["runs"])


class StartRunBody(BaseModel):
    kind: Literal["generate", "provision", "kg-publish", "live-tail"]
    plan_id: str
    extra_args: list[str] = []


class RunSummary(BaseModel):
    run_id: str
    kind: str
    plan_id: str
    started_at: datetime
    finished: bool
    return_code: int | None
    line_count: int


def _summarise(run_id: str) -> RunSummary | None:
    h = get_run(run_id)
    if h is None:
        return None
    return RunSummary(
        run_id=h.run_id,
        kind=h.kind,
        plan_id=h.plan_id,
        started_at=h.started_at,
        finished=h.finished,
        return_code=h.return_code,
        line_count=len(h.log_buffer),
    )


@router.get("", response_model=list[RunSummary])
def list_endpoint() -> list[RunSummary]:
    """Newest first. Process-local — restarting the API resets it."""
    return [
        RunSummary(
            run_id=h.run_id, kind=h.kind, plan_id=h.plan_id,
            started_at=h.started_at, finished=h.finished,
            return_code=h.return_code, line_count=len(h.log_buffer),
        )
        for h in list_runs()
    ]


@router.post("", response_model=RunSummary)
async def start_endpoint(body: StartRunBody) -> RunSummary:
    if body.kind not in ALLOWED_RUNS:
        raise HTTPException(status_code=400, detail=f"unknown run kind {body.kind!r}")
    try:
        handle = await start_run(RunRequest(
            kind=body.kind, plan_id=body.plan_id, extra_args=body.extra_args,
        ))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    summary = _summarise(handle.run_id)
    assert summary is not None  # we just added it
    return summary


@router.get("/{run_id}", response_model=RunSummary)
def get_endpoint(run_id: str) -> RunSummary:
    summary = _summarise(run_id)
    if summary is None:
        raise HTTPException(status_code=404, detail=f"run {run_id} not found")
    return summary


@router.get("/{run_id}/stream")
async def stream_endpoint(run_id: str) -> EventSourceResponse:
    """SSE stream of log lines. Final event is `exit` with the return code."""
    if get_run(run_id) is None:
        raise HTTPException(status_code=404, detail=f"run {run_id} not found")

    async def event_gen() -> object:
        async for line in stream_run_lines(run_id):
            if line.startswith("__exit__ "):
                yield {"event": "exit", "data": line.removeprefix("__exit__ ")}
                return
            yield {"event": "log", "data": line}

    return EventSourceResponse(event_gen())


@router.post("/{run_id}/cancel")
async def cancel_endpoint(run_id: str) -> dict[str, bool | str]:
    if get_run(run_id) is None:
        raise HTTPException(status_code=404, detail=f"run {run_id} not found")
    cancelled = await cancel_run(run_id)
    return {"cancelled": cancelled}
