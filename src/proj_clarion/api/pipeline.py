"""End-to-end demo pipeline: URL → research → plan → approve → generate
→ provision → kg-publish, streamed phase-by-phase to the UI.

Each phase shells out to the existing CLI so we never re-implement business
logic; the runner.py subprocess plumbing is reused. Phase boundaries are
detected via DB state (newest profile_id / plan_id) and stdout markers
(kg_emitter.start for the emitter, since it never exits).

Yields `PipelineEvent` objects:
- {"event": "phase",   "phase": "research", "status": "started"}
- {"event": "log",     "phase": "research", "line": "..."}
- {"event": "phase",   "phase": "research", "status": "done", "profile_id": "prof-..."}
- {"event": "phase",   "phase": "...", "status": "failed", "error": "..."}  (terminal)
- {"event": "links",   "stack": "https://...", "dashboards_folder": "...", ...}
- {"event": "done"}                                                          (terminal)
"""

from __future__ import annotations

import asyncio
import os
import shlex
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import text

from proj_clarion.api.cloud_creds import resolve_cloud_creds
from proj_clarion.schemas import ReviewState
from proj_clarion.storage import AuditRepo, PlanRepo, session_scope

PROJECT_ROOT = Path(__file__).resolve().parents[3]
PROFILES_DIR = PROJECT_ROOT / "data" / "profiles"


# ─── Phase machinery ─────────────────────────────────────────────────


async def _spawn(
    argv: list[str],
    *,
    phase: str | None = None,
    pipeline_id: str | None = None,
) -> tuple[asyncio.subprocess.Process, asyncio.Queue[str | None]]:
    """Start a CLI subprocess with the project root as cwd and stream stdout
    into a queue. Sentinel `None` is enqueued at EOF.

    Resolved Mode-A creds are injected so any phase that emits OTel data
    (generate / kg-publish / live-tail) targets Alloy automatically when
    Alloy is running, and falls back to direct-to-Cloud otherwise.

    `phase` and `pipeline_id` are exported as `CLARION_PIPELINE_PHASE` /
    `CLARION_PIPELINE_ID` so the LLM wrapper inside the subprocess can
    stamp them onto every gen_ai span without explicit plumbing through
    every CLI command.
    """
    env = os.environ.copy()
    creds = resolve_cloud_creds()
    if creds:
        env.update(creds)
    if phase:
        env["CLARION_PIPELINE_PHASE"] = phase
    if pipeline_id:
        env["CLARION_PIPELINE_ID"] = pipeline_id

    proc = await asyncio.create_subprocess_exec(
        *argv,
        cwd=str(PROJECT_ROOT),
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )

    queue: asyncio.Queue[str | None] = asyncio.Queue()

    async def _drain() -> None:
        assert proc.stdout is not None
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            await queue.put(line.decode(errors="replace").rstrip("\n"))
        await queue.put(None)

    asyncio.create_task(_drain())  # noqa: RUF006 — fire-and-forget by design
    return proc, queue


async def _run_to_completion(
    phase: str, argv: list[str], *, pipeline_id: str | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Run a CLI subprocess to completion, yielding log events. Raises on
    non-zero exit so the orchestrator can convert that into a phase-failed
    event."""
    yield {"event": "log", "phase": phase, "line": f"$ {' '.join(shlex.quote(a) for a in argv)}"}
    proc, queue = await _spawn(argv, phase=phase, pipeline_id=pipeline_id)
    while True:
        item = await queue.get()
        if item is None:
            break
        yield {"event": "log", "phase": phase, "line": item}
    rc = await proc.wait()
    if rc != 0:
        raise RuntimeError(f"{phase} subprocess exited with code {rc}")


async def _run_until_marker(
    phase: str, argv: list[str], marker: str, timeout_seconds: float = 120.0,
    *, pipeline_id: str | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Run a long-running CLI subprocess (e.g. kg-publish) and yield log
    events until a known marker line is seen on stdout. After that the
    subprocess keeps running detached — the caller's responsibility is to
    leave it alone.

    Timeouts are belt-and-braces: if the marker doesn't appear within
    `timeout_seconds` we leave the process running but advance the
    pipeline anyway; assumption is that the user will see what's happening
    in /runs and act on it.
    """
    yield {"event": "log", "phase": phase, "line": f"$ {' '.join(shlex.quote(a) for a in argv)}"}
    proc, queue = await _spawn(argv, phase=phase, pipeline_id=pipeline_id)
    start = asyncio.get_event_loop().time()
    while True:
        try:
            item = await asyncio.wait_for(queue.get(), timeout=5.0)
        except TimeoutError:
            if asyncio.get_event_loop().time() - start > timeout_seconds:
                yield {"event": "log", "phase": phase,
                       "line": f"… marker {marker!r} not seen within {timeout_seconds:.0f}s; advancing anyway"}
                return
            continue
        if item is None:
            # Process exited before marker — that's an error
            rc = await proc.wait()
            raise RuntimeError(f"{phase} exited before marker (code {rc})")
        yield {"event": "log", "phase": phase, "line": item}
        if marker in item:
            return


# ─── Phase implementations ───────────────────────────────────────────


async def _phase_research(
    url: str, company: str | None, *, pipeline_id: str | None = None,
    disabled_sources: list[str] | None = None,
    notes: str | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Run the research agent against a URL. After it completes, find the
    newly-created profile JSON and surface its profile_id.

    `disabled_sources`: external source types (edgar_10k / github_org / …)
    the SE turned off for this build — forwarded to the CLI as repeated
    --disable-source flags.

    `notes`: optional operator discovery notes. When present we run the
    `research-notes --also-fetch` path instead of plain `research`, so the
    notes are folded in as a trusted source ON TOP of the normal web/
    external research (the source toggles still apply to that enrichment).
    """
    yield {"event": "phase", "phase": "research", "status": "started",
           "message": f"Researching {url}"}

    # Snapshot pre-run profile mtimes so we can detect the new one
    before = _snapshot_profiles()

    disable_flags: list[str] = []
    for s in disabled_sources or []:
        disable_flags += ["--disable-source", s]

    notes_text = (notes or "").strip()
    notes_file: Path | None = None
    try:
        if notes_text:
            # research-notes takes a file path argument; stage the notes in
            # a temp file for the subprocess and clean it up afterwards.
            import tempfile
            fd, tmp = tempfile.mkstemp(prefix="clarion-notes-", suffix=".md")
            os.close(fd)
            notes_file = Path(tmp)
            notes_file.write_text(notes_text)
            argv = ["uv", "run", "python", "-m", "proj_clarion.cli.main",
                    "research-notes", str(notes_file), "--url", url, "--also-fetch"]
            if company:
                argv += ["--company", company]
            argv += disable_flags
        else:
            argv = ["uv", "run", "python", "-m", "proj_clarion.cli.main", "research", url]
            if company:
                argv += ["--company", company]
            argv += disable_flags

        async for ev in _run_to_completion("research", argv, pipeline_id=pipeline_id):
            yield ev
    finally:
        if notes_file is not None:
            notes_file.unlink(missing_ok=True)

    new_profile = _newest_new_profile(before)
    if new_profile is None:
        raise RuntimeError("research completed but no new profile JSON appeared in data/profiles/")

    yield {"event": "phase", "phase": "research", "status": "done",
           "profile_id": new_profile.stem,
           "profile_path": str(new_profile.relative_to(PROJECT_ROOT))}


async def _phase_plan(
    profile_path: str, *, volume_per_day: int | None = None,
    pipeline_id: str | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Run the planner against the just-produced profile.

    Consolidation behavior: if this profile already has a plan in the
    DB, the planner still produces a fresh plan with a new UUID (the
    agent doesn't know it's a re-run), and we transplant its content
    into the existing plan's plan_id. This keeps one plan per profile
    so SEs aren't drowning in duplicates after every extend → re-run
    cycle, and downstream refs (kg_nodes, business_events, plan_audit_log)
    stay anchored on a stable id.

    `volume_per_day` is forwarded to the planner CLI so the SE can
    scale demo volume from the build form without editing code."""
    msg = f"Planning from {profile_path}"
    if volume_per_day is not None:
        msg += f" (volume override: {volume_per_day:,}/day)"
    yield {"event": "phase", "phase": "plan", "status": "started", "message": msg}

    profile_id = Path(profile_path).stem
    existing_plan_id = _stable_plan_id_for_profile(profile_id)

    argv = ["uv", "run", "python", "-m", "proj_clarion.cli.main",
            "plan", "run", profile_path]
    if volume_per_day is not None:
        argv += ["--volume-per-day", str(volume_per_day)]
    async for ev in _run_to_completion("plan", argv, pipeline_id=pipeline_id):
        yield ev

    new_plan_id = _newest_plan_for_profile(profile_id)
    if new_plan_id is None:
        raise RuntimeError("plan run completed but no new plan in the DB")

    # If this profile already had a plan, replace its content rather
    # than letting the new UUID compound the duplicate count.
    final_plan_id = new_plan_id
    if existing_plan_id and existing_plan_id != new_plan_id:
        _consolidate_plan_content(existing_plan_id, new_plan_id)
        final_plan_id = existing_plan_id
        yield {
            "event": "log", "phase": "plan",
            "line": f"reused plan_id {existing_plan_id[:8]} (replaced content from {new_plan_id[:8]})",
        }

    yield {"event": "phase", "phase": "plan", "status": "done", "plan_id": final_plan_id}


async def _phase_approve(plan_id: str) -> AsyncIterator[dict[str, Any]]:
    """In-process state transition. Auto-approve in the one-button flow;
    the SE can still review the plan in /plans/{id} either before clicking
    Build, or after the demo materializes."""
    yield {"event": "phase", "phase": "approve", "status": "started"}

    note = "auto-approved by /api/pipelines/run (one-button demo)"
    actor = os.environ.get("USER", "unknown")
    with session_scope() as s:
        prev = PlanRepo().set_review_state(s, plan_id, ReviewState.APPROVED_FOR_PROVISION)
        if prev is None:
            raise RuntimeError(f"plan {plan_id} not found for approval")
        AuditRepo().record(
            s, plan_id,
            actor=actor, action="approved",
            from_state=prev, to_state=ReviewState.APPROVED_FOR_PROVISION.value,
            note=note,
        )

    yield {"event": "log", "phase": "approve",
           "line": f"approved {plan_id[:8]} ({prev} → approved_for_provision)"}
    yield {"event": "phase", "phase": "approve", "status": "done", "plan_id": plan_id}


async def _phase_generate(
    plan_id: str, days: int, *, pipeline_id: str | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Generate events into Postgres + traces to Cloud (via Alloy if up)."""
    yield {"event": "phase", "phase": "generate", "status": "started",
           "message": f"Generating {days} day(s) of events"}
    argv = ["uv", "run", "python", "-m", "proj_clarion.cli.main",
            "generate", "run", plan_id, "--days", str(days), "--anchor-now"]
    async for ev in _run_to_completion("generate", argv, pipeline_id=pipeline_id):
        yield ev
    yield {"event": "phase", "phase": "generate", "status": "done"}


async def _phase_provision(
    plan_id: str, *, pipeline_id: str | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Push dashboards + alert rules to Cloud.

    `--sweep-orphans` is the default on the CLI, so each provision push
    also tears down folders/dashboards/alerts whose plan no longer
    exists in Postgres. Keeps Cloud-side asset count bounded by the DB."""
    yield {"event": "phase", "phase": "provision", "status": "started",
           "message": "Pushing dashboards + alerts to Cloud (with orphan sweep)"}
    argv = ["uv", "run", "python", "-m", "proj_clarion.cli.main",
            "provision", "run", plan_id, "--push"]
    async for ev in _run_to_completion("provision", argv, pipeline_id=pipeline_id):
        yield ev
    # Audit entry — what was created in Cloud, with a clickable URL to
    # the folder so an SE can jump straight to "show me the dashboards
    # this build produced". Note carries the URL inline; the UI's audit
    # panel auto-detects URLs and renders them as links.
    _audit_cloud_creates_provision(plan_id)
    yield {"event": "phase", "phase": "provision", "status": "done"}


async def _phase_kg_publish(
    plan_id: str, *, pipeline_id: str | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Push KG model rules + start the entity emitter detached. Pipeline
    advances once the emitter has logged `kg_emitter.start` — we don't
    wait for the long-running process to exit."""
    yield {"event": "phase", "phase": "kg-publish", "status": "started",
           "message": "Pushing KG rules and starting the entity emitter"}
    argv = ["uv", "run", "python", "-m", "proj_clarion.cli.main",
            "kg", "publish", plan_id]
    async for ev in _run_until_marker(
        "kg-publish", argv, marker="kg_emitter.start", pipeline_id=pipeline_id,
    ):
        yield ev
    _audit_cloud_creates_kg_publish(plan_id)
    yield {"event": "phase", "phase": "kg-publish", "status": "done",
           "message": "Emitter is running in the background; metrics flowing to Cloud."}


def _audit_cloud_creates_provision(plan_id: str) -> None:
    """Append an audit entry describing what provision pushed to Cloud.
    Failures are swallowed — audit is observability for the SE, never
    block the build on bookkeeping. Note text uses inline URLs so the
    UI's audit panel can linkify them."""
    try:
        from proj_clarion.api.links import build_grafana_links
        from proj_clarion.provision.folders import folder_uid_for_plan
        links = build_grafana_links(plan_id)
        folder_uid = folder_uid_for_plan(plan_id)
        actor = os.environ.get("USER", "pipeline")
        # Pre-resolve URLs to local variables so the f-string doesn't have
        # to nest quoted dict keys (the dashboard link's label contains
        # a literal apostrophe that breaks f-string parsing).
        dash_url = links.get("Dashboards (this plan's folder)", "(local stack)")
        alert_url = links.get("Alerts (this plan)", "(local stack)")
        note = (
            f"Provisioned to Grafana Cloud. "
            f"Folder UID: {folder_uid}. "
            f"Dashboards: {dash_url}. "
            f"Alerts: {alert_url}."
        )
        with session_scope() as s:
            AuditRepo().record(
                s, plan_id,
                actor=actor, action="cloud.provisioned",
                note=note,
            )
    except Exception as exc:  # noqa: BLE001
        # Visible in API logs but doesn't fail the phase.
        import structlog
        structlog.get_logger().warning(
            "audit.cloud_provision_failed", plan_id=plan_id, error=str(exc)[:200],
        )


def _audit_cloud_creates_kg_publish(plan_id: str) -> None:
    """Append an audit entry for the KG push: model-rules + prom-rules
    file names, KG entity catalog URL. See `_audit_cloud_creates_provision`
    for the failure-handling pattern."""
    try:
        from proj_clarion.api.links import build_grafana_links
        actor = os.environ.get("USER", "pipeline")
        # File names mirror what cli/main.py:kg_publish writes (built from
        # plan_id prefix + customer slug).
        plan_short = str(plan_id)[:8]
        links = build_grafana_links(plan_id)
        kg_url = links.get("Knowledge Graph (entity catalog)", "(local stack)")
        note = (
            f"Pushed Knowledge Graph artifacts to Grafana Cloud. "
            f"Model-rules: clarion-business-model-*-{plan_short}. "
            f"Prom-rules: clarion-entity-recording-rules-{plan_short}. "
            f"Entity emitter started (clarion_entity_info every 30s). "
            f"View entities: {kg_url}. "
        )
        with session_scope() as s:
            AuditRepo().record(
                s, plan_id,
                actor=actor, action="cloud.kg_published",
                note=note,
            )
    except Exception as exc:  # noqa: BLE001
        import structlog
        structlog.get_logger().warning(
            "audit.cloud_kg_publish_failed", plan_id=plan_id, error=str(exc)[:200],
        )


# ─── Top-level orchestrator ──────────────────────────────────────────


PIPELINE_PHASES: tuple[str, ...] = (
    "research", "plan", "approve", "generate", "provision", "kg-publish",
)


def _phase_idx(name: str) -> int:
    """Index of `name` in PIPELINE_PHASES; raises ValueError on unknown."""
    return PIPELINE_PHASES.index(name)


async def run_demo_pipeline(
    url: str,
    company: str | None = None,
    days: int = 1,
    *,
    starting_phase: str | None = None,
    stop_after_phase: str | None = None,
    profile_id: str | None = None,
    profile_path: str | None = None,
    plan_id: str | None = None,
    volume_per_day: int | None = None,
    pipeline_id: str | None = None,
    emit_reused_phases: bool = True,
    disabled_sources: list[str] | None = None,
    notes: str | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """The whole flow, or any tail of it.

    Shape: pipeline:started → run each phase from `starting_phase` (or
    'research' if None) onward → links → pipeline:done. First failure is
    a terminal event; nothing after it.

    Resume-from-phase rules:
      - starting_phase='research' or None  → no extra inputs needed
      - starting_phase='plan'               → caller must supply profile_path
                                              or profile_id (we resolve path
                                              from PROFILES_DIR)
      - starting_phase ∈ {approve, generate, provision, kg-publish}
                                            → caller must supply plan_id;
                                              profile_id is optional (we
                                              backfill from the plan row)

    When a phase's subprocess crashes mid-flight we have to emit
    phase:failed BEFORE pipeline:failed — otherwise the UI's per-row
    rendering keeps spinning forever. The active_phase tracker below
    remembers which phase last started so we know what to mark failed.
    """
    start_idx = 0 if starting_phase is None else _phase_idx(starting_phase)
    # Inclusive upper bound. None means "run everything past start_idx".
    stop_idx = (len(PIPELINE_PHASES) - 1) if stop_after_phase is None else _phase_idx(stop_after_phase)
    if stop_idx < start_idx:
        raise ValueError(
            f"stop_after_phase={stop_after_phase!r} is before starting_phase={starting_phase!r}"
        )

    # Resolve profile_path from profile_id if needed (so callers can
    # pass either form).
    if profile_path is None and profile_id is not None:
        profile_path = str(PROFILES_DIR / f"{profile_id}.json")

    # Backfill profile_id from the plan row when caller skipped research+plan.
    if start_idx >= _phase_idx("approve") and plan_id is not None and profile_id is None:
        with session_scope() as s:
            row = s.execute(
                text("SELECT source_profile_id FROM demo_plans WHERE plan_id = :p"),
                {"p": str(plan_id)},
            ).fetchone()
        if row is not None:
            profile_id = row[0]

    # Input validation — fail fast with a clear error rather than crashing
    # mid-phase with a None-deref.
    # profile_path is consumed ONLY by the plan phase. Require it just when
    # we're *starting* at plan (a plan-only resume); a full run derives it
    # from research, and approve/generate/provision/kg-publish never touch
    # it. (Previously this required it for every phase >= plan, which made
    # resuming at "approve" crash with "requires profile_path or profile_id".)
    if start_idx == _phase_idx("plan") and profile_path is None:
        raise ValueError(
            f"starting_phase={starting_phase!r} requires profile_path or profile_id"
        )
    if start_idx >= _phase_idx("approve") and plan_id is None:
        raise ValueError(
            f"starting_phase={starting_phase!r} requires plan_id"
        )

    yield {"event": "pipeline", "status": "started",
           "started_at": datetime.now(timezone.utc).isoformat(),
           "url": url, "company": company, "days": days,
           "starting_phase": starting_phase,
           "profile_id": profile_id, "plan_id": plan_id}

    # Phases before start_idx already SUCCEEDED — possibly in a parent
    # pipeline whose plan_id we inherited. From the user's perspective
    # they're done, not skipped. Emit `phase:done` carrying whatever
    # artifact we have (profile_id from research, plan_id from plan)
    # so the UI renders them as green-check completed instead of muted
    # "skipped" rows. Phases beyond start_idx stay pending until they
    # run for real.
    # When continuing the SAME pipeline in place (resume-in-place), the
    # prior phases' real `done` events are already on this pipeline's event
    # stream, so re-emitting them would duplicate (and overwrite their
    # messages). Only replay them for a fresh from-phase pipeline.
    now_iso = datetime.now(timezone.utc).isoformat()
    if emit_reused_phases:
        for i, ph in enumerate(PIPELINE_PHASES):
            if i < start_idx:
                ev: dict[str, Any] = {
                    "event": "phase", "phase": ph, "status": "done",
                    "message": "Reused from a prior build",
                    "started_at": now_iso, "finished_at": now_iso,
                }
                if ph == "research" and profile_id:
                    ev["profile_id"] = profile_id
                if ph == "plan" and plan_id:
                    ev["plan_id"] = plan_id
                yield ev

    active_phase: str | None = None  # most recent phase to emit "started"

    async def pipe(gen: AsyncIterator[dict[str, Any]]) -> AsyncIterator[dict[str, Any]]:
        """Stream events through, also tracking which phase is currently
        running so we can emit a phase-failed event on exception."""
        nonlocal active_phase
        async for ev in gen:
            if ev.get("event") == "phase":
                if ev.get("status") == "started":
                    active_phase = ev.get("phase")
                elif ev.get("status") == "done":
                    active_phase = None
            yield ev

    try:
        # Each phase is gated by BOTH the resume floor (start_idx) and
        # the early-exit ceiling (stop_idx). When stop_after_phase is
        # set to "research", we research and bail — no plan/approve/etc.
        if start_idx <= _phase_idx("research") <= stop_idx:
            async for ev in pipe(_phase_research(
                url, company, pipeline_id=pipeline_id,
                disabled_sources=disabled_sources, notes=notes,
            )):
                yield ev
                if ev.get("event") == "phase" and ev.get("status") == "done":
                    profile_id = ev.get("profile_id") or profile_id
                    profile_path = ev.get("profile_path") or profile_path

        if start_idx <= _phase_idx("plan") <= stop_idx:
            assert profile_path is not None  # validated above
            async for ev in pipe(_phase_plan(
                profile_path, volume_per_day=volume_per_day, pipeline_id=pipeline_id,
            )):
                yield ev
                if ev.get("event") == "phase" and ev.get("status") == "done":
                    plan_id = ev.get("plan_id") or plan_id

        if start_idx <= _phase_idx("approve") <= stop_idx:
            assert plan_id is not None
            async for ev in pipe(_phase_approve(plan_id)):
                yield ev

        if start_idx <= _phase_idx("generate") <= stop_idx:
            assert plan_id is not None
            async for ev in pipe(_phase_generate(plan_id, days, pipeline_id=pipeline_id)):
                yield ev

        if start_idx <= _phase_idx("provision") <= stop_idx:
            assert plan_id is not None
            async for ev in pipe(_phase_provision(plan_id, pipeline_id=pipeline_id)):
                yield ev

        if start_idx <= _phase_idx("kg-publish") <= stop_idx:
            assert plan_id is not None
            async for ev in pipe(_phase_kg_publish(plan_id, pipeline_id=pipeline_id)):
                yield ev

    except Exception as exc:  # noqa: BLE001 — convert to event for the UI
        # Mark the in-flight phase as failed so the UI doesn't show it
        # spinning forever. The pipeline-level failed event follows.
        if active_phase is not None:
            yield {"event": "phase", "phase": active_phase, "status": "failed",
                   "error": str(exc)}
        yield {"event": "pipeline", "status": "failed",
               "error": str(exc),
               "profile_id": profile_id, "plan_id": plan_id}
        return

    # ─── Final state: surface Grafana links ───
    from proj_clarion.api.links import build_grafana_links
    links = build_grafana_links(plan_id) if plan_id else {}
    yield {"event": "links", **links, "plan_id": plan_id, "profile_id": profile_id}
    yield {"event": "pipeline", "status": "done",
           "finished_at": datetime.now(timezone.utc).isoformat(),
           "plan_id": plan_id, "profile_id": profile_id}


# ─── DB helpers ──────────────────────────────────────────────────────


def _snapshot_profiles() -> dict[str, float]:
    """Map of profile path → mtime, used to detect the new profile after research."""
    if not PROFILES_DIR.exists():
        return {}
    return {str(p): p.stat().st_mtime for p in PROFILES_DIR.glob("*.json")}


def _newest_new_profile(before: dict[str, float]) -> Path | None:
    """Find the profile that's new or modified relative to the snapshot."""
    if not PROFILES_DIR.exists():
        return None
    candidates: list[tuple[float, Path]] = []
    for p in PROFILES_DIR.glob("*.json"):
        sp = str(p)
        mt = p.stat().st_mtime
        if sp not in before or mt > before[sp]:
            candidates.append((mt, p))
    if not candidates:
        return None
    candidates.sort(reverse=True)
    return candidates[0][1]


def _newest_plan_for_profile(profile_id: str) -> str | None:
    """The plan agent writes to demo_plans; pick the most recent one whose
    source_profile_id matches."""
    with session_scope() as s:
        row = s.execute(
            text("""
                SELECT plan_id::text
                FROM demo_plans
                WHERE source_profile_id = :pid
                ORDER BY created_at DESC
                LIMIT 1
            """),
            {"pid": profile_id},
        ).fetchone()
    return row[0] if row else None


def _stable_plan_id_for_profile(profile_id: str) -> str | None:
    """The OLDEST existing plan_id for this profile, the slot we want
    re-runs to consolidate INTO. Oldest because it's the one with the
    longest lineage in kg_nodes / plan_audit_log / business_events;
    replacing its content (instead of the newer dup) preserves more
    of the foreign-key history. Returns None if the profile has no
    existing plans yet."""
    with session_scope() as s:
        row = s.execute(
            text("""
                SELECT plan_id::text
                FROM demo_plans
                WHERE source_profile_id = :pid
                ORDER BY created_at ASC
                LIMIT 1
            """),
            {"pid": profile_id},
        ).fetchone()
    return row[0] if row else None


def _consolidate_plan_content(target_plan_id: str, new_plan_id: str) -> None:
    """Replace `target_plan_id`'s plan_json with `new_plan_id`'s content,
    then drop the new row. The target keeps its stable plan_id so
    every kg_node / kg_edge / business_event / plan_audit_log row
    that references the target stays valid; the new row's children
    (none yet at this point, since only research + plan have run)
    cascade-delete cleanly.

    Resets the target's review_state back to DRAFT because the plan
    content has materially changed. The approve phase the orchestrator
    runs right after this will re-set it to APPROVED_FOR_PROVISION.

    Also records a plan_audit_log entry so the SE can see in /audit
    why a plan they thought was 'approved' suddenly went back to
    'draft' (and what the new content is)."""
    from copy import deepcopy
    from uuid import UUID

    from proj_clarion.schemas import ReviewState
    from proj_clarion.storage import AuditRepo, PlanRepo, session_scope

    with session_scope() as s:
        repo = PlanRepo()
        new_plan = repo.get(s, new_plan_id)
        if new_plan is None:
            raise RuntimeError(f"consolidate: new plan {new_plan_id} not found")
        target = repo.get(s, target_plan_id)
        if target is None:
            raise RuntimeError(f"consolidate: target plan {target_plan_id} not found")

        # Build the consolidated plan: new content, original id.
        merged = deepcopy(new_plan)
        merged_data = merged.model_dump()
        merged_data["plan_id"] = UUID(target_plan_id)
        merged_data["review_state"] = ReviewState.DRAFT.value
        from proj_clarion.schemas import DemoPlan
        consolidated = DemoPlan.model_validate(merged_data)

        # Upsert flips the existing target row's plan_json to the new
        # content. Delete the now-orphaned new row.
        repo.upsert(s, consolidated)
        repo.delete(s, new_plan_id)

        # Audit: why the plan that was sitting at 'approved_for_provision'
        # is suddenly 'draft' again. Plan re-runs (after profile extends)
        # are the common cause; the note carries enough breadcrumbs for
        # the SE to reconstruct.
        actor = os.environ.get("USER", "unknown")
        AuditRepo().record(
            s, target_plan_id,
            actor=actor,
            action="plan_content_replaced",
            from_state=str(target.review_state),
            to_state=ReviewState.DRAFT.value,
            note=(
                f"Plan content replaced in-place by a fresh planner run "
                f"(consolidated from {new_plan_id[:8]}). The approve "
                f"phase will re-mark this APPROVED_FOR_PROVISION."
            ),
        )
