"""Server-side pipeline registry — DB-backed.

Pipelines are long-running (10-15 min) and SE users WILL navigate away
mid-build, refresh their browser, and (now) restart the API. Holding
state purely in-process meant any restart wiped two days of history.

This module dual-tracks every pipeline:

  - Postgres (`pipelines`, `pipeline_events`, `pipeline_phases`) is the
    durable source of truth. Survives API restarts; survives moves to
    other machines if you ever ship this.
  - An in-process `_LiveState` is kept ONLY while the orchestrator task
    is alive on this process. Its job is wakeup-signaling for live SSE
    consumers — replays come from the DB.

API surface (unchanged from v0.7):
  - start_pipeline(...)              spawn task + persist row
  - start_pipeline_from_phase(...)   resume from a phase, links to parent
  - list_pipelines() -> list[PipelineState]  reads from DB
  - get_pipeline(id) -> PipelineState | None  reads from DB
  - stream_pipeline(id) -> async iterator    DB replay → live tail
  - cancel_pipeline(id) -> bool

PipelineState is now a transient view object — populate it from DB rows
when callers need it. The repo is the source of truth.

Lifespan helper:
  - reap_orphans()  marks any DB-side `running` rows from a previous
    process as `failed: orphaned`. Call once at API startup.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import structlog

from proj_clarion.api.pipeline import run_demo_pipeline
from proj_clarion.storage import PipelineRepo, session_scope

_logger = structlog.get_logger()
_repo = PipelineRepo()

# Map of pipeline_id → live wake-up signal. Only present while a task is
# running on THIS process. Past pipelines and pipelines from a previous
# API process are not in here; their events are read from the DB.
_LIVE: dict[str, "_LiveState"] = {}


@dataclass
class _LiveState:
    """In-process state for an actively-running pipeline. Holds the
    asyncio task handle (so cancel can reach it) and a wake-up event
    so SSE consumers don't have to poll the DB for new events."""

    pipeline_id: str
    task: asyncio.Task[None] | None = None
    new_event: asyncio.Event = field(default_factory=asyncio.Event)
    # Last committed sequence number for events appended this run.
    # Used so multiple SSE consumers tailing the same pipeline can each
    # re-query the DB for "what's after seq N" cheaply.
    last_seq: int = -1


@dataclass
class PipelineState:
    """View object that callers consume. Built fresh from DB rows.
    Fields mirror v0.7's in-memory PipelineState so existing call sites
    (api/routes/pipelines.py::_summarise) keep working unchanged."""

    pipeline_id: str
    url: str
    company: str | None
    days: int
    started_at: datetime
    status: str
    finished_at: datetime | None = None
    error: str | None = None
    # `events` is left empty by default — callers that need the full log
    # should hit /api/pipelines/{id}/events or use stream_pipeline. The
    # field exists so _summarise can stay agnostic.
    events: list[dict[str, Any]] = field(default_factory=list)
    # Resume metadata — surfaced so the UI can chain pipelines together.
    trigger: str = "full"
    starting_phase: str | None = None
    parent_pipeline_id: str | None = None
    profile_id: str | None = None
    plan_id: str | None = None


def _row_to_state(row: dict[str, Any], events: list[dict[str, Any]] | None = None) -> PipelineState:
    return PipelineState(
        pipeline_id=row["pipeline_id"],
        url=row["url"],
        company=row["company"],
        days=row["days"],
        started_at=row["started_at"],
        status=row["status"],
        finished_at=row["finished_at"],
        error=row["error"],
        events=events or [],
        trigger=row.get("trigger", "full"),
        starting_phase=row.get("starting_phase"),
        parent_pipeline_id=row.get("parent_pipeline_id"),
        profile_id=row.get("profile_id"),
        plan_id=row.get("plan_id"),
    )


# ── Public API ───────────────────────────────────────────────────────


def list_pipelines() -> list[PipelineState]:
    """Newest first. Reads from Postgres so prior-process runs show up.

    Self-heals zombies first: any `running` row with no live task on this
    process (`_LIVE`) is an orphan from a reloaded/crashed process, so we
    reap it (+ its open phases) here. The UI polls this every few seconds,
    so a stuck build converges to `failed` on its own — no restart needed."""
    with session_scope() as s:
        try:
            reaped = _repo.reap_stale(s, set(_LIVE.keys()))
            if reaped:
                _logger.warning("pipeline_registry.reaped_stale", count=reaped)
        except Exception:  # noqa: BLE001 — never let reaping break the list
            _logger.exception("pipeline_registry.reap_stale_failed")
        rows = _repo.list(s, limit=200)
    return [_row_to_state(r) for r in rows]


def get_pipeline(pipeline_id: str) -> PipelineState | None:
    """Single-row fetch. Caller can then call /events for the full log."""
    with session_scope() as s:
        row = _repo.get(s, pipeline_id)
    return _row_to_state(row) if row else None


def list_pipelines_for(
    *, profile_id: str | None = None, plan_id: str | None = None,
    status: str | None = None, limit: int = 50,
) -> list[PipelineState]:
    """Filtered pipeline list (newest first). Lets the agent/UI ask
    'is there already a build for this profile/plan (running)?' cheaply."""
    with session_scope() as s:
        rows = _repo.list(s, limit=limit, status=status,
                          profile_id=profile_id, plan_id=plan_id)
    return [_row_to_state(r) for r in rows]


def find_pipeline_for(
    *, profile_id: str | None = None, plan_id: str | None = None,
) -> PipelineState | None:
    """The canonical (newest) pipeline for a profile or plan, or None if
    there isn't one yet. This is the row that re-runs/retries/refines REUSE
    in place — we never mint a sibling for the same profile/plan. Prefer a
    plan_id match (most specific); fall back to profile_id."""
    if plan_id:
        rows = list_pipelines_for(plan_id=plan_id, limit=1)
        if rows:
            return rows[0]
    if profile_id:
        rows = list_pipelines_for(profile_id=profile_id, limit=1)
        if rows:
            return rows[0]
    return None


def get_pipeline_events(pipeline_id: str) -> list[dict[str, Any]] | None:
    """Full event log for a pipeline. Returns None if the pipeline
    doesn't exist; empty list if it exists but has no events yet."""
    with session_scope() as s:
        row = _repo.get(s, pipeline_id)
        if row is None:
            return None
        evs = _repo.events(s, pipeline_id)
    return [ev for _seq, ev in evs]


def reap_orphans() -> int:
    """Lifespan hook: any pipeline left as `running` in Postgres at
    startup is an orphan from a prior process. Mark it failed so the
    UI doesn't show fake spinners forever."""
    with session_scope() as s:
        n = _repo.reap_orphans(s)
    if n:
        _logger.warning("pipeline_registry.reaped_orphans", count=n)
    return n


def start_pipeline(
    url: str,
    company: str | None = None,
    days: int = 1,
    *,
    volume_per_day: int | None = None,
    stop_after_phase: str | None = None,
    disabled_sources: list[str] | None = None,
    notes: str | None = None,
) -> PipelineState:
    """Full build: research → plan → approve → generate → provision → kg-publish.

    `stop_after_phase`: optional cut-off. When set, the pipeline exits
    after that phase completes successfully. Use "research" for a
    profile-only build (no plan, no Cloud-side provisioning).

    `disabled_sources` / `notes`: research-phase controls forwarded to the
    research CLI — which external source types to skip, and optional
    discovery notes to fold in as a trusted source. Transient (consumed by
    the research phase of this run); not persisted on the pipeline row."""
    return _spawn(
        url=url, company=company, days=days,
        trigger="full", starting_phase=None,
        parent_pipeline_id=None,
        runner_kwargs={
            "volume_per_day": volume_per_day,
            "stop_after_phase": stop_after_phase,
            "disabled_sources": disabled_sources,
            "notes": notes,
        },
    )


def start_pipeline_from_phase(
    *,
    starting_phase: str,
    url: str,
    company: str | None = None,
    days: int = 1,
    profile_id: str | None = None,
    profile_path: str | None = None,
    plan_id: str | None = None,
    parent_pipeline_id: str | None = None,
    volume_per_day: int | None = None,
    stop_after_phase: str | None = None,
) -> PipelineState:
    """Resume from a specific phase. Caller is responsible for providing
    whatever inputs that phase requires:
      - research: url + company
      - plan: profile_path (or profile_id)
      - approve / generate / provision / kg-publish: plan_id
    Earlier phases are skipped; the resulting pipeline_id is fresh and
    `parent_pipeline_id` links back to the failed run.
    """
    return _spawn(
        url=url, company=company, days=days,
        trigger="phase", starting_phase=starting_phase,
        parent_pipeline_id=parent_pipeline_id,
        runner_kwargs={
            "starting_phase": starting_phase,
            "profile_id": profile_id,
            "profile_path": profile_path,
            "plan_id": plan_id,
            "volume_per_day": volume_per_day,
            "stop_after_phase": stop_after_phase,
        },
    )


# Phases whose orchestrator step requires a plan_id (the plan must already
# exist). 'plan' resumes from a profile; 'research' from the url.
_PLAN_PHASES = ("approve", "generate", "provision", "kg-publish")


def continue_pipeline(
    pipeline_id: str,
    *,
    starting_phase: str = "approve",
    stop_after_phase: str | None = None,
    plan_id: str | None = None,
    profile_id: str | None = None,
    volume_per_day: int | None = None,
    supersede: bool = False,
) -> PipelineState | None:
    """Resume a build IN PLACE from `starting_phase` — appending the
    remaining phases to the SAME pipeline_id, no second build row. This is
    the single path for every re-run / retry / refine: reopen the row to
    `running` and run the orchestrator from `starting_phase`, writing events
    after the existing ones.

    Returns None if the pipeline doesn't exist.

    Running-row behavior:
      - supersede=False (default): the in-flight build already covers the
        request — return it unchanged (reuse, no new task).
      - supersede=True: the in-flight build WON'T get the job done (e.g. a
        profile extend landed after it started). Cancel it, then reopen and
        resume the SAME id. append_events is ON CONFLICT-safe so a lagging
        flush from the cancelled task can't crash the resume.

    Input rules mirror run_demo_pipeline:
      - starting_phase in {approve,generate,provision,kg-publish} → needs plan_id
      - starting_phase == 'plan'                                   → needs profile_id
      - starting_phase == 'research'                               → needs url (from the row)
    """
    row = get_pipeline(pipeline_id)
    if row is None:
        return None

    # Inherit what the build already produced.
    plan_id = plan_id or row.plan_id
    profile_id = profile_id or row.profile_id

    # If it's still running, decide: reuse it, or supersede it.
    if row.status == "running":
        if not supersede:
            return row
        # Cancel the in-flight task; the DB row flips to `cancelled`
        # immediately so the reopen below sees a terminal row.
        cancel_pipeline(pipeline_id)

    # Validate inputs for the phase we're about to resume from.
    if starting_phase in _PLAN_PHASES and not plan_id:
        raise ValueError(
            f"cannot resume from '{starting_phase}': no plan_id on this build"
        )
    if starting_phase == "plan" and not profile_id:
        raise ValueError(
            "cannot re-plan: no profile_id on this build"
        )

    with session_scope() as s:
        cur = _repo.get(s, pipeline_id)
        if cur is None:
            return None
        url = cur["url"]
        company = cur.get("company")
        days = cur.get("days") or 1
        start_seq = _repo.event_count(s, pipeline_id)
        _repo.reopen(s, pipeline_id)

    live = _LiveState(pipeline_id=pipeline_id)
    _LIVE[pipeline_id] = live
    live.task = asyncio.create_task(
        _run_one(
            live, url, company, days,
            runner_kwargs={
                "starting_phase": starting_phase,
                "stop_after_phase": stop_after_phase,
                "plan_id": plan_id,
                "profile_id": profile_id,
                "volume_per_day": volume_per_day,
                # The prior phases' done-events are already on this stream.
                "emit_reused_phases": False,
            },
            start_seq=start_seq,
        )
    )

    with session_scope() as s:
        row2 = _repo.get(s, pipeline_id)
    assert row2 is not None
    return _row_to_state(row2)


def _same_host(a: str | None, b: str | None) -> bool:
    """True if two build urls point at the same company host (so a re-run
    reuses the build in place). A missing/placeholder url → treat as same
    (we only fork a new build on a genuinely different host)."""
    from urllib.parse import urlparse

    def host(u: str | None) -> str:
        if not u or u.startswith("plan://"):
            return ""
        try:
            return (urlparse(u).hostname or "").lower().removeprefix("www.")
        except Exception:  # noqa: BLE001
            return ""

    ha, hb = host(a), host(b)
    if not ha or not hb:
        return True
    return ha == hb


def run_or_resume_phase(
    *,
    starting_phase: str,
    url: str | None,
    company: str | None = None,
    days: int = 1,
    profile_id: str | None = None,
    plan_id: str | None = None,
    volume_per_day: int | None = None,
    stop_after_phase: str | None = None,
    supersede: bool = False,
) -> tuple[PipelineState, bool]:
    """ONE build per profile/plan — the single entry point for every
    re-run / retry / refine (assistant AND UI go through here).

    Resume the canonical pipeline for this profile/plan IN PLACE (same
    pipeline_id) when one exists and the URL matches; otherwise create the
    first row. A genuinely different URL forks a NEW row, parent-linked to
    the prior one for connectedness. Returns (state, reused)."""
    existing = find_pipeline_for(plan_id=plan_id, profile_id=profile_id)
    if existing is not None and _same_host(existing.url, url):
        state = continue_pipeline(
            existing.pipeline_id,
            starting_phase=starting_phase,
            stop_after_phase=stop_after_phase,
            plan_id=plan_id,
            profile_id=profile_id,
            volume_per_day=volume_per_day,
            supersede=supersede,
        )
        if state is not None:
            return state, True

    state = start_pipeline_from_phase(
        starting_phase=starting_phase,
        url=url or f"plan://{(plan_id or profile_id or 'unknown')[:8]}",
        company=company,
        days=days,
        profile_id=profile_id if starting_phase == "plan" else None,
        plan_id=plan_id if starting_phase in _PLAN_PHASES else None,
        volume_per_day=volume_per_day,
        stop_after_phase=stop_after_phase,
        parent_pipeline_id=existing.pipeline_id if existing is not None else None,
    )
    return state, False


def cancel_pipeline(pipeline_id: str) -> bool:
    """Cancel a build. Returns True if we either signalled the live task
    or flipped a still-running DB row to `cancelled`.

    Two things happen, because signalling the task alone isn't enough: a
    phase wedged in a synchronous call (e.g. a blocking LLM/HTTP request)
    never reaches an `await`, so `task.cancel()` can't land and the UI
    would spin forever. So we ALSO mark the DB row `cancelled` right away
    and wake any SSE tail. The task (if it ever unblocks) sees the
    CancelledError and re-writes the same terminal status — idempotent.
    Orphaned pipelines from a prior process have no live task; flipping
    the DB row still lets the UI converge."""
    live = _LIVE.get(pipeline_id)
    signalled = False
    if live is not None and live.task is not None and not live.task.done():
        live.task.cancel()
        signalled = True

    flipped = False
    with session_scope() as s:
        row = _repo.get(s, pipeline_id)
        if row is not None and row["status"] == "running":
            _repo.update_status(
                s, pipeline_id, "cancelled",
                finished_at=datetime.now(timezone.utc),
            )
            flipped = True

    # Wake the SSE tail so it re-reads the now-terminal status and closes,
    # which triggers the client to reconcile to `cancelled`.
    if live is not None:
        live.new_event.set()

    return signalled or flipped


def prune_pipelines(statuses: tuple[str, ...] = ("failed", "cancelled")) -> int:
    """Bulk-delete terminal builds in the given statuses (default the
    dead/reload-casualty ones) so the Builds list isn't cluttered. Never
    touches `running` or `done`. Returns the count removed."""
    safe = [s for s in statuses if s in ("failed", "cancelled")]
    if not safe:
        return 0
    with session_scope() as s:
        return _repo.delete_by_status(s, safe)


def delete_pipeline(pipeline_id: str) -> bool:
    """Hard-delete a build: cancel it first if it's still live on this
    process (so we don't orphan a running task), then remove the pipeline
    row + its events + phases from Postgres. Returns True if a row was
    deleted. Once deleted the build cannot reappear on a list/refresh —
    this is the escape hatch for a wedged or runaway build."""
    live = _LIVE.get(pipeline_id)
    if live is not None and live.task is not None and not live.task.done():
        live.task.cancel()
        live.new_event.set()
    _LIVE.pop(pipeline_id, None)
    with session_scope() as s:
        return _repo.delete(s, pipeline_id)


async def stream_pipeline(pipeline_id: str) -> AsyncIterator[dict[str, Any]]:
    """SSE source. Replays from DB, then tails live until terminal status.

    Algorithm:
      1. Snapshot current pipeline status + max committed seq from DB.
      2. Yield every event in order from seq=0 to current.
      3. If live (task running on THIS process), wait on the wake-up
         signal and yield new events as they commit. Repeat until status
         flips terminal.
      4. If not live (terminal already, or prior-process pipeline), we're done.
    """
    # Replay phase: stream from DB in chunks so we don't blow memory on
    # a long pipeline.
    cursor = -1
    chunk = 200
    terminal = False
    while True:
        with session_scope() as s:
            row = _repo.get(s, pipeline_id)
            if row is None:
                raise KeyError(pipeline_id)
            evs = _repo.events(s, pipeline_id, after_seq=cursor, limit=chunk)
        if not evs:
            terminal = row["status"] != "running"
            break
        for seq, ev in evs:
            yield ev
            cursor = seq

    if terminal:
        return

    # Live tail phase: wait for new events; yield them as they land.
    live = _LIVE.get(pipeline_id)
    if live is None:
        # No live task on this process. Reap it IF it's been idle long
        # enough (conservative — never fail a build that's merely between
        # processes but recently active), then converge the client to
        # whatever the DB now says. If it wasn't reaped (still "running",
        # recently active), we just stop tailing; the client's periodic
        # reconcile picks up the terminal status once it does go idle.
        terminal_status: str | None = None
        terminal_error: str | None = None
        try:
            with session_scope() as s:
                _repo.reap_stale(s, set(_LIVE.keys()))
                row2 = _repo.get(s, pipeline_id)
            if row2 is not None and row2["status"] != "running":
                terminal_status = row2["status"]
                terminal_error = row2.get("error")
        except Exception:  # noqa: BLE001
            _logger.exception("pipeline_registry.stream_reap_failed")
        if terminal_status is not None:
            yield {"event": "pipeline", "status": terminal_status, "error": terminal_error}
        return

    while True:
        # Quick re-check: any events committed between our last DB read
        # and attaching the wake-up signal?
        with session_scope() as s:
            row = _repo.get(s, pipeline_id)
            evs = _repo.events(s, pipeline_id, after_seq=cursor)
        for seq, ev in evs:
            yield ev
            cursor = seq
        if row is None or row["status"] != "running":
            return
        # Wait for the next event or a heartbeat tick.
        live.new_event.clear()
        try:
            await asyncio.wait_for(live.new_event.wait(), timeout=30.0)
        except TimeoutError:
            # Heartbeat — re-loop and re-check.
            continue


# ── Internals ────────────────────────────────────────────────────────


def _spawn(
    *,
    url: str,
    company: str | None,
    days: int,
    trigger: str,
    starting_phase: str | None,
    parent_pipeline_id: str | None,
    runner_kwargs: dict[str, Any],
) -> PipelineState:
    """Persist a new pipeline row and start its background task."""
    pipeline_id = uuid4().hex[:12]
    started_at = datetime.now(timezone.utc)
    with session_scope() as s:
        _repo.create(
            s,
            pipeline_id=pipeline_id, url=url, company=company, days=days,
            started_at=started_at,
            trigger=trigger, starting_phase=starting_phase,
            parent_pipeline_id=parent_pipeline_id,
        )

    live = _LiveState(pipeline_id=pipeline_id)
    _LIVE[pipeline_id] = live
    live.task = asyncio.create_task(_run_one(live, url, company, days, runner_kwargs))

    # Return a freshly-constructed view — caller doesn't see _LiveState.
    with session_scope() as s:
        row = _repo.get(s, pipeline_id)
    assert row is not None
    return _row_to_state(row)


async def _run_one(
    live: _LiveState,
    url: str,
    company: str | None,
    days: int,
    runner_kwargs: dict[str, Any],
    *,
    start_seq: int = 0,
) -> None:
    """Background task body. Pumps run_demo_pipeline events into
    Postgres + signals live consumers. Sets terminal status on exit.

    Events are batched into the DB in small chunks (≤25 events or every
    ~250 ms) to keep write throughput reasonable for chatty phases like
    `generate` while still feeling live in the UI.
    """
    pipeline_id = live.pipeline_id
    pending: list[dict[str, Any]] = []
    next_seq = start_seq

    async def flush() -> None:
        nonlocal next_seq, pending
        if not pending:
            return
        batch = pending
        pending = []
        with session_scope() as s:
            _repo.append_events(s, pipeline_id, batch, first_seq=next_seq)
            # Update phase rollup from any phase events in this batch.
            for ev in batch:
                _maybe_update_phase(s, pipeline_id, ev)
                _maybe_link_artifact(s, pipeline_id, ev)
        next_seq += len(batch)
        live.last_seq = next_seq - 1
        live.new_event.set()

    last_flush = asyncio.get_event_loop().time()
    BATCH = 25
    BATCH_INTERVAL_S = 0.25
    terminal_status: str | None = None
    terminal_error: str | None = None

    try:
        async for ev in run_demo_pipeline(
            url, company, days=days, pipeline_id=pipeline_id, **runner_kwargs,
        ):
            pending.append(ev)
            if ev.get("event") == "pipeline":
                if ev.get("status") == "done":
                    terminal_status = "done"
                elif ev.get("status") == "failed":
                    terminal_status = "failed"
                    terminal_error = ev.get("error")
            now = asyncio.get_event_loop().time()
            if len(pending) >= BATCH or (now - last_flush) >= BATCH_INTERVAL_S:
                await flush()
                last_flush = now
        await flush()
    except asyncio.CancelledError:
        pending.append({
            "event": "pipeline", "status": "cancelled",
            "finished_at": datetime.now(timezone.utc).isoformat(),
        })
        await flush()
        terminal_status = "cancelled"
        with session_scope() as s:
            _repo.update_status(
                s, pipeline_id, "cancelled",
                finished_at=datetime.now(timezone.utc),
            )
        live.new_event.set()
        raise
    except Exception as exc:  # noqa: BLE001 — surface as terminal event
        msg = f"orchestrator crashed: {exc}"
        pending.append({"event": "pipeline", "status": "failed", "error": msg})
        await flush()
        terminal_status = "failed"
        terminal_error = msg

    # Persist final pipeline-level status. (Phase-level statuses were
    # updated incrementally by _maybe_update_phase as events flushed.)
    if terminal_status is not None:
        with session_scope() as s:
            _repo.update_status(
                s, pipeline_id, terminal_status,
                finished_at=datetime.now(timezone.utc),
                error=terminal_error,
            )
    live.new_event.set()
    # Drop the live entry so a finished pipeline's stream terminates
    # cleanly on the next iteration.
    _LIVE.pop(pipeline_id, None)


def _maybe_update_phase(s: Any, pipeline_id: str, ev: dict[str, Any]) -> None:
    """If `ev` is a phase event, sync it into pipeline_phases."""
    if ev.get("event") != "phase":
        return
    phase = ev.get("phase")
    if not phase:
        return
    status_ev = ev.get("status")
    now = datetime.now(timezone.utc)
    if status_ev == "started":
        _repo.upsert_phase(
            s, pipeline_id, phase,
            status="running", started_at=now,
        )
    elif status_ev == "done":
        artifact: dict[str, Any] | None = None
        if "profile_id" in ev:
            artifact = {"profile_id": ev["profile_id"]}
        if "plan_id" in ev:
            artifact = (artifact or {}) | {"plan_id": ev["plan_id"]}
        _repo.upsert_phase(
            s, pipeline_id, phase,
            status="done", finished_at=now, artifact=artifact,
        )
    elif status_ev == "failed":
        _repo.upsert_phase(
            s, pipeline_id, phase,
            status="failed", finished_at=now, error=ev.get("error"),
        )


def _maybe_link_artifact(s: Any, pipeline_id: str, ev: dict[str, Any]) -> None:
    """When a phase emits its produced artifact (profile_id from research,
    plan_id from plan), propagate to the pipelines row so /pipelines list
    can show the linkage at a glance.

    Bookkeeping must NEVER crash the orchestrator. If the upstream phase
    didn't persist the artifact to the table the FK points at (e.g. an
    older `research` CLI that wrote JSON only and skipped Postgres),
    swallow the FK violation and keep going — the artifact still lives
    on disk and the next phase will surface it. Logged loudly so the
    issue isn't silent."""
    if ev.get("event") != "phase" or ev.get("status") != "done":
        return
    try:
        if pid := ev.get("profile_id"):
            _repo.set_profile_id(s, pipeline_id, pid)
        if pl := ev.get("plan_id"):
            _repo.set_plan_id(s, pipeline_id, pl)
    except Exception as exc:  # noqa: BLE001 — never crash on metadata writes
        _logger.warning(
            "pipeline_registry.link_artifact_failed",
            pipeline_id=pipeline_id, event=ev,
            error=str(exc)[:200],
        )
