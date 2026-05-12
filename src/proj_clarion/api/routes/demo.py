"""Demo session routes — /api/demo/*

The SE workflow:

    SE walks into a meeting → /plans/:planId → "Start demo" button
    → POST /api/demo/start spawns `kg publish --no-push-rules --emit`
      detached, writes a demo_sessions row.
    → EntityEmitter heartbeats every cycle, UI badge shows "Live · 18s ago"
    → 90 minutes pass, demo done → SE clicks "Stop"
    → POST /api/demo/stop SIGTERMs the PID, marks session 'stopped'
    → OR if SE forgets: sweeper hits expires_at (default 2h), kills it,
      marks session 'expired'.

Two routes — start/stop. Plus GET /status for the UI polling loop, and
POST /extend for the rare "demo's running long, gimme another hour."

Sweeper logic is a startup-time background task on the FastAPI app
(see api/main.py) — not a route. Runs every 60s.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.exc import IntegrityError

from proj_clarion.api.routes.plans import _resolve_plan_id
from proj_clarion.storage import DemoSessionRepo, PlanRepo, session_scope

router = APIRouter(prefix="/api/demo", tags=["demo"])

# Default session ceiling — the user gets 2 hours of free runtime, then
# the sweeper kills the emitter to stop burning Cloud quota.
DEFAULT_DURATION_HOURS = 2.0

# Cap "extend" to a sane window — we don't want to accidentally end up
# in a 30-hour demo session because the SE typoed.
MAX_TOTAL_HOURS = 8.0


# ──────────────────────────────────────────────────────────────────
# Request / response models
# ──────────────────────────────────────────────────────────────────


class StartRequest(BaseModel):
    plan_id: str
    duration_hours: float = Field(
        default=DEFAULT_DURATION_HOURS,
        ge=0.25, le=MAX_TOTAL_HOURS,
        description="How long to keep the emitter alive before auto-stop. Default 2h.",
    )
    max_entities: int | None = Field(
        default=None,
        ge=1, le=10_000,
        description=(
            "Optional cap on the number of entities the emitter materialises "
            "in Asserts. Tier-priority trim: business entities + clusters + "
            "nodes are kept, pods are cut first. Use when the full KG (often "
            "100+ pods) crowds the entity-graph view for a live demo. Empty "
            "means no cap."
        ),
    )


class StopRequest(BaseModel):
    plan_id: str


class ExtendRequest(BaseModel):
    plan_id: str
    additional_hours: float = Field(default=1.0, ge=0.25, le=4.0)


# ──────────────────────────────────────────────────────────────────
# Routes
# ──────────────────────────────────────────────────────────────────


@router.get("/history")
def list_history(
    limit: int = 100,
    offset: int = 0,
    plan_id: str | None = None,
) -> dict[str, Any]:
    """Audit log of every demo session, newest first.

    Returns both live and terminal rows on one timeline. Each row's
    duration is server-derived (seconds_active) so the UI doesn't need
    date arithmetic for ranking / sorting / display.

    Pagination is offset/limit. The UI sends `limit=10&offset=...`
    for a 10-row-per-page table; `total` is included so the pagination
    footer can show "x-y of N".

    Optional `plan_id` query scopes the history to one plan — used by
    the per-plan Demo history strip on Plans-detail. Without it, returns
    sessions across every plan.
    """
    import datetime as dt
    if limit < 1: limit = 1
    if limit > 500: limit = 500
    if offset < 0: offset = 0

    with session_scope() as s:
        repo = DemoSessionRepo()
        rows = repo.list_history(s, limit=limit, offset=offset, plan_id=plan_id)
        total = repo.count_history(s, plan_id=plan_id)

    out: list[dict[str, Any]] = []
    for r in rows:
        started = r["started_at"]
        finished = r["finished_at"]
        seconds_active: float | None = None
        if started is not None:
            start_dt = started if started.tzinfo else started.replace(tzinfo=dt.UTC)
            if finished is not None:
                end_dt = finished if finished.tzinfo else finished.replace(tzinfo=dt.UTC)
                seconds_active = (end_dt - start_dt).total_seconds()
            elif r["status"] in ("starting", "live"):
                seconds_active = (dt.datetime.now(dt.UTC) - start_dt).total_seconds()

        out.append({
            "session_id":     r["id"],
            "plan_id":        r["plan_id"],
            "pid":            r["pid"],
            "status":         r["status"],
            "started_at":     started.isoformat() if started else None,
            "finished_at":    finished.isoformat() if finished else None,
            "expires_at":     r["expires_at"].isoformat() if r["expires_at"] else None,
            "seconds_active": seconds_active,
            "url":            r.get("url"),
            "company":        r.get("company"),
            "notes":          r.get("notes"),
        })

    return {"history": out, "total": total, "limit": limit, "offset": offset}


@router.get("/sessions")
def list_sessions() -> dict[str, Any]:
    """All currently-running demo sessions, newest first.

    Used by the dashboard's Live demo card so the SE sees what's
    emitting right now without polling per-plan. Enriches each row
    with derived UI fields (seconds_since_heartbeat, seconds_until_expiry,
    health) the same way GET /status does — keeps the date arithmetic
    on the server.
    """
    import datetime as dt
    now = dt.datetime.now(dt.UTC)
    with session_scope() as s:
        rows = DemoSessionRepo().list_active(s)

    out: list[dict[str, Any]] = []
    for r in rows:
        hb = r["last_heartbeat_at"]
        seconds_since_hb: float | None = None
        if hb is not None:
            if hb.tzinfo is None:
                hb = hb.replace(tzinfo=dt.UTC)
            seconds_since_hb = (now - hb).total_seconds()
        expires_at = r["expires_at"]
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=dt.UTC)
        seconds_until_expiry = (expires_at - now).total_seconds()

        if hb is None:
            health = "starting"
        elif seconds_since_hb is not None and seconds_since_hb < 90:
            health = "live"
        else:
            health = "stale"

        out.append({
            "session_id":            r["id"],
            "plan_id":               r["plan_id"],
            "pid":                   r["pid"],
            "status":                r["status"],
            "started_at":            r["started_at"].isoformat() if r["started_at"] else None,
            "expires_at":            expires_at.isoformat(),
            "last_heartbeat_at":     hb.isoformat() if hb else None,
            "seconds_since_heartbeat": seconds_since_hb,
            "seconds_until_expiry":  max(0.0, seconds_until_expiry),
            "health":                health,
            "url":                   r.get("url"),
            "company":               r.get("company"),
        })
    return {"sessions": out}


@router.get("/status")
def get_status(plan_id: str) -> dict[str, Any]:
    """Active session for a plan, or `{ active: false }`.

    Computes "live age" (seconds since last heartbeat) so the UI can
    render the freshness badge without doing date math in JS — keeps
    the truth on the server.
    """
    with session_scope() as s:
        full_id = _resolve_plan_id(s, plan_id)
        if not full_id:
            raise HTTPException(404, f"plan {plan_id} not found")
        active = DemoSessionRepo().get_active(s, full_id)

    if active is None:
        return {"active": False, "plan_id": full_id}

    # Derive client-facing freshness from heartbeat. If the heartbeat is
    # older than 90s the emitter is probably dead — surface that as
    # "stale" so the UI can prompt the user to restart.
    import datetime as dt
    now = dt.datetime.now(dt.UTC)
    hb = active["last_heartbeat_at"]
    seconds_since_hb: float | None = None
    if hb is not None:
        if hb.tzinfo is None:
            hb = hb.replace(tzinfo=dt.UTC)
        seconds_since_hb = (now - hb).total_seconds()
    expires_at = active["expires_at"]
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=dt.UTC)
    seconds_until_expiry = (expires_at - now).total_seconds()

    # Health: live (heartbeat <90s), stale (>=90s), starting (no heartbeat yet).
    if hb is None:
        health = "starting"
    elif seconds_since_hb is not None and seconds_since_hb < 90:
        health = "live"
    else:
        health = "stale"

    return {
        "active":               True,
        "plan_id":              full_id,
        "session_id":           active["id"],
        "pid":                  active["pid"],
        "status":               active["status"],
        "started_at":           active["started_at"].isoformat() if active["started_at"] else None,
        "expires_at":           expires_at.isoformat(),
        "last_heartbeat_at":    hb.isoformat() if hb else None,
        "seconds_since_heartbeat": seconds_since_hb,
        "seconds_until_expiry": max(0.0, seconds_until_expiry),
        "health":               health,
    }


@router.post("/start")
def post_start(body: StartRequest) -> dict[str, Any]:
    """Spawn the emit-only emitter detached, persist a demo_sessions row.

    The subprocess we spawn is intentionally the existing `kg publish
    --no-push-rules --no-doctor --emit` flow — it skips the slow gcx
    rounds for rule push and just runs the EntityEmitter forever (well,
    until we SIGTERM it from /stop, or the sweeper does on expiry).

    Why detached: the API process shouldn't own the emitter's lifetime.
    If the API crashes the emitter keeps going; if the emitter crashes
    the API is fine. The sweeper reconciles via PID + heartbeat.
    """
    with session_scope() as s:
        full_id = _resolve_plan_id(s, body.plan_id)
        if not full_id:
            raise HTTPException(404, f"plan {body.plan_id} not found")
        plan = PlanRepo().get(s, full_id)
        if plan is None:
            raise HTTPException(404, f"plan {full_id} unloadable")

        repo = DemoSessionRepo()
        # Reject if there's already an active session — DB unique
        # partial index also enforces this, but failing here is
        # friendlier than a 500 from the IntegrityError.
        existing = repo.get_active(s, full_id)
        if existing is not None:
            raise HTTPException(
                409,
                detail={
                    "message": "demo session already running",
                    "session_id": existing["id"],
                    "expires_at": existing["expires_at"].isoformat() if existing["expires_at"] else None,
                },
            )

        try:
            row = repo.start(s, full_id, duration_hours=body.duration_hours)
        except IntegrityError:
            # Race: another start arrived between our get_active check
            # and our INSERT. Friendly 409 vs internal 500.
            raise HTTPException(409, "demo session already running (race)")

        # Spawn detached. Use sys.executable so we hit the same Python
        # that's running the API (== the .venv) — avoids the "module
        # not found" trap when the user's shell python is different.
        argv = [
            sys.executable, "-m", "proj_clarion.cli.main",
            "kg", "publish", str(full_id),
            "--no-push-rules", "--no-doctor", "--emit",
        ]
        if body.max_entities is not None:
            argv += ["--max-entities", str(body.max_entities)]
        # detach from the API's process group so signals to uvicorn
        # don't bubble down to the emitter (e.g. dev `--reload`).
        # Use `start_new_session=True` instead of preexec_fn on macOS
        # to dodge a Python 3.13 deprecation.
        log_dir = Path(os.environ.get("CLARION_LOG_DIR", "/tmp"))
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / f"clarion-demo-{full_id[:8]}.log"
        log_fh = open(log_path, "ab")
        proc = subprocess.Popen(
            argv,
            stdout=log_fh, stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
        )
        repo.set_pid(s, row["id"], proc.pid)

        row["pid"] = proc.pid
        row["log_path"] = str(log_path)

    return {
        "ok": True,
        "session_id": row["id"],
        "pid": row["pid"],
        "started_at": row["started_at"].isoformat(),
        "expires_at": row["expires_at"].isoformat(),
        "log_path": row.get("log_path"),
    }


@router.post("/stop")
def post_stop(body: StopRequest) -> dict[str, Any]:
    """Mark the active session 'stopped' and SIGTERM the PID.

    Order matters: mark stopped FIRST so the heartbeat update from any
    in-flight emit cycle won't flip it back to 'live'. Then signal.
    Best-effort signal — if the process is already gone (crashed,
    OS reboot), the DB row's still correctly terminal.
    """
    with session_scope() as s:
        full_id = _resolve_plan_id(s, body.plan_id)
        if not full_id:
            raise HTTPException(404, f"plan {body.plan_id} not found")
        repo = DemoSessionRepo()
        # Read PID before stopping so we can signal it after the tx commits.
        active = repo.get_active(s, full_id)
        if active is None:
            return {"ok": True, "stopped": False, "reason": "no active session"}
        pid = active["pid"]
        repo.stop(s, full_id, reason="stopped")

    if pid:
        _signal_pid(pid, signal.SIGTERM)

    return {"ok": True, "stopped": True, "pid": pid}


@router.post("/extend")
def post_extend(body: ExtendRequest) -> dict[str, Any]:
    """Push the auto-stop deadline forward. Returns the new expires_at."""
    with session_scope() as s:
        full_id = _resolve_plan_id(s, body.plan_id)
        if not full_id:
            raise HTTPException(404, f"plan {body.plan_id} not found")
        row = DemoSessionRepo().extend(s, full_id, additional_hours=body.additional_hours)

    if row is None:
        raise HTTPException(404, "no active demo session for this plan")
    return {
        "ok": True,
        "expires_at": row["expires_at"].isoformat(),
    }


# ──────────────────────────────────────────────────────────────────
# Sweeper — kills expired demo sessions. Invoked from api/main.py's
# lifespan as a background asyncio task.
# ──────────────────────────────────────────────────────────────────


def _signal_pid(pid: int, sig: int) -> None:
    """SIGTERM the PID, swallow ESRCH (already gone) and EPERM (other user)."""
    try:
        os.kill(pid, sig)
    except ProcessLookupError:
        pass
    except PermissionError:
        # Different user owns the PID — shouldn't happen in our flow but
        # don't let it crash the sweeper either.
        pass


async def reap_expired_demo_sessions() -> None:
    """Sweeper coroutine — runs forever, scans `demo_sessions` once a
    minute, SIGTERMs any past expires_at + marks them 'expired'.

    Called as `asyncio.create_task(reap_expired_demo_sessions())` from
    the FastAPI lifespan. The task is cancelled on shutdown.
    """
    import asyncio
    while True:
        try:
            with session_scope() as s:
                repo = DemoSessionRepo()
                expired = repo.list_expired(s)
                for row in expired:
                    if row["pid"]:
                        _signal_pid(row["pid"], signal.SIGTERM)
                    # Mark terminal — using the same `stop()` helper
                    # ensures the rowcount is honest and we don't double-stop.
                    repo.stop(s, row["plan_id"], reason="expired")
        except Exception:  # noqa: BLE001
            # Sweeper failures must not crash the API. Worst case the
            # SE manually clicks Stop or the OS reboots reaps the PID.
            pass
        await asyncio.sleep(60)
