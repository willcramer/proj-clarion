"""Tool catalog for the global Clarion Assistant.

Each tool is two pieces:
  * Anthropic tool definition (name + description + input_schema) — what
    Claude sees when deciding whether to call it.
  * Executor — runs against a SQLAlchemy session, returns a Python value
    that we JSON-serialize into the tool_result block on the next agent
    iteration.

The split between TOOLS_READONLY and TOOLS_MUTATING is enforced at the
endpoint layer — Phase A ships read-only only. Mutating tools land in
Phase C.

The result of each executor must be:
  * JSON-serializable (lists, dicts, primitives)
  * Bounded in size (the agent re-sends every tool_result on every
    iteration, so a 50KB blob costs 50KB × N iterations)

Conventions:
  * Listings use a `limit` arg (default 20, max 100) and return slim
    rows. The agent should call get_X for full detail when needed.
  * Detail tools (get_*) accept an id and return the full record.
  * Errors are raised as exceptions; the endpoint wraps them in
    tool_result blocks with is_error=True.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from sqlalchemy.orm import Session

from proj_clarion.storage import (
    AuditRepo,  # plan state-transition audit
    DemoSessionRepo,
    PipelineRepo,
    PlanRepo,
    ProfileAuditRepo,
    ProfileRepo,
)

# ──────────────────────────────────────────────────────────────────
# Executors — each returns a JSON-serializable Python value
# ──────────────────────────────────────────────────────────────────

# Hard limits so a confused agent can't ask for 10K profiles in one go.
_MAX_LIST_LIMIT = 100
_DEFAULT_LIST_LIMIT = 20


def _clamp_limit(args: dict[str, Any]) -> int:
    """Defensive clamp — Claude usually obeys the schema's max but
    let's not trust that for limits that affect query cost."""
    raw = args.get("limit", _DEFAULT_LIST_LIMIT)
    try:
        n = int(raw)
    except (TypeError, ValueError):
        n = _DEFAULT_LIST_LIMIT
    return max(1, min(_MAX_LIST_LIMIT, n))


def _exec_list_profiles(args: dict[str, Any], session: Session) -> list[dict[str, Any]]:
    """Slim listing of company profiles, newest first."""
    limit = _clamp_limit(args)
    repo = ProfileRepo()
    out: list[dict[str, Any]] = []
    for pid, created_at, source_url in repo.list(session, limit=limit):
        try:
            profile = repo.get(session, pid)
        except Exception:  # noqa: BLE001 — bad JSON in DB; skip
            continue
        if profile is None:
            continue
        out.append({
            "profile_id":       pid,
            "company_name":     profile.company.name if profile.company else None,
            "primary_url":      source_url,
            "pain_signal_count": len(profile.pain_signals or []),
            "tech_signal_count": len(profile.tech_stack_signals or []),
            "synthesized_flag_count": len(profile.synthesized_flags or []),
            "created_at":       created_at.isoformat(),
        })
    return out


def _exec_get_profile(args: dict[str, Any], session: Session) -> dict[str, Any]:
    """Full CompanyProfile JSON."""
    pid = args.get("profile_id")
    if not isinstance(pid, str) or not pid:
        raise ValueError("profile_id is required")
    profile = ProfileRepo().get(session, pid)
    if profile is None:
        raise ValueError(f"profile {pid} not found")
    import json
    return json.loads(profile.model_dump_json())


def _exec_list_plans(args: dict[str, Any], session: Session) -> list[dict[str, Any]]:
    """Slim listing of demo plans, newest-updated first. Optional
    `source_profile_id` filter scopes to plans built from one profile."""
    limit = _clamp_limit(args)
    profile_filter = args.get("source_profile_id")
    repo = PlanRepo()
    out: list[dict[str, Any]] = []
    # PlanRepo.list returns (UUID, datetime, str review_state, str profile_id) —
    # close to what we want for a slim row. Augment with counts via get().
    for row in repo.list(session, limit=limit):
        plan_id, updated_at, review_state, source_profile_id = row
        if profile_filter and source_profile_id != profile_filter:
            continue
        try:
            plan = repo.get(session, plan_id)
        except Exception:  # noqa: BLE001
            continue
        if plan is None:
            continue
        out.append({
            "plan_id":            str(plan_id),
            "plan_id_short":      str(plan_id)[:8],
            "source_profile_id":  source_profile_id,
            "review_state":       review_state,
            "process_count":      len(plan.business_process_models or []),
            "kg_node_count":      len(plan.knowledge_graph.nodes if plan.knowledge_graph else []),
            "alert_count":        len(plan.alert_specs or []),
            "dashboard_count":    len(plan.dashboard_specs or []),
            "updated_at":         updated_at.isoformat(),
        })
    return out


def _exec_get_plan(args: dict[str, Any], session: Session) -> dict[str, Any]:
    """Full DemoPlan JSON (warning: large for big KGs)."""
    pid = args.get("plan_id")
    if not isinstance(pid, str) or not pid:
        raise ValueError("plan_id is required")
    plan = PlanRepo().get(session, pid)
    if plan is None:
        raise ValueError(f"plan {pid} not found")
    import json
    return json.loads(plan.model_dump_json())


def _exec_list_pipelines(args: dict[str, Any], session: Session) -> list[dict[str, Any]]:
    """Recent pipelines, newest first. Optional `profile_id` / `plan_id` /
    `status` filter; otherwise returns all. Use status='running' to check
    whether a profile already has a build in flight before kicking one."""
    limit = _clamp_limit(args)
    profile_filter = args.get("profile_id") or None
    plan_filter    = args.get("plan_id") or None
    status_filter  = args.get("status") or None
    rows = PipelineRepo().list(
        session, limit=limit,
        profile_id=profile_filter, plan_id=plan_filter, status=status_filter,
    )
    out: list[dict[str, Any]] = []
    for r in rows:
        # PipelineRepo.list already returns dicts; pass through but
        # serialize timestamps.
        out.append({
            "pipeline_id":   r.get("pipeline_id"),
            "status":        r.get("status"),
            "url":           r.get("url"),
            "company":       r.get("company"),
            "profile_id":    r.get("profile_id"),
            "plan_id":       r.get("plan_id"),
            "started_at":    r["started_at"].isoformat() if r.get("started_at") else None,
            "finished_at":   r["finished_at"].isoformat() if r.get("finished_at") else None,
            "phases_done":   r.get("phases_done"),
            "current_phase": r.get("current_phase"),
            "event_count":   r.get("event_count"),
        })
    return out


def _exec_get_pipeline(args: dict[str, Any], session: Session) -> dict[str, Any]:
    """Single pipeline detail, including its phase rollup."""
    pid = args.get("pipeline_id")
    if not isinstance(pid, str) or not pid:
        raise ValueError("pipeline_id is required")
    row = PipelineRepo().get(session, pid)
    if row is None:
        raise ValueError(f"pipeline {pid} not found")
    return {
        "pipeline_id":   row.get("pipeline_id"),
        "status":        row.get("status"),
        "url":           row.get("url"),
        "company":       row.get("company"),
        "profile_id":    row.get("profile_id"),
        "plan_id":       row.get("plan_id"),
        "started_at":    row["started_at"].isoformat() if row.get("started_at") else None,
        "finished_at":   row["finished_at"].isoformat() if row.get("finished_at") else None,
        "phases_done":   row.get("phases_done"),
        "current_phase": row.get("current_phase"),
        "event_count":   row.get("event_count"),
        "error":         row.get("error"),
    }


def _exec_list_demo_sessions(args: dict[str, Any], session: Session) -> list[dict[str, Any]]:
    """Currently-running emitter sessions (status in active set).
    Optional `plan_id` filter."""
    plan_filter = args.get("plan_id")
    rows = DemoSessionRepo().list_active(session)
    out: list[dict[str, Any]] = []
    for r in rows:
        if plan_filter and r.get("plan_id") != plan_filter:
            continue
        out.append({
            "session_id":         r.get("id"),
            "plan_id":            str(r.get("plan_id")) if r.get("plan_id") else None,
            "pid":                r.get("pid"),
            "status":             r.get("status"),
            "started_at":         r["started_at"].isoformat() if r.get("started_at") else None,
            "expires_at":         r["expires_at"].isoformat() if r.get("expires_at") else None,
            "last_heartbeat_at":  r["last_heartbeat_at"].isoformat() if r.get("last_heartbeat_at") else None,
        })
    return out


def _exec_get_audit_log(args: dict[str, Any], session: Session) -> dict[str, Any]:
    """Recent audit entries across profile-extends and plan state
    transitions. Useful when SE asks 'what changed recently'."""
    limit = _clamp_limit(args)
    profile_audits = ProfileAuditRepo().list_all(session, limit=limit)
    plan_audits = AuditRepo().list_all(session, limit=limit)
    return {
        "profile_extends": [
            {
                "audit_id":   a["audit_id"],
                "profile_id": a["profile_id"],
                "actor":      a["actor"],
                "prompt":     a["prompt"],
                "summary":    a["summary"],
                "additions":  a["additions"],
                "applied":    a["applied"],
                "created_at": a["created_at"].isoformat(),
                "url":        a.get("url"),
                "company":    a.get("company"),
            }
            for a in profile_audits
        ],
        "plan_transitions": [
            {
                "plan_id":    a["plan_id"],
                "actor":      a["actor"],
                "action":     a["action"],
                "from_state": a["from_state"],
                "to_state":   a["to_state"],
                "note":       a["note"],
                "created_at": a["created_at"].isoformat() if a.get("created_at") else None,
                "url":        a.get("url"),
                "company":    a.get("company"),
            }
            for a in plan_audits
        ],
    }


# ──────────────────────────────────────────────────────────────────
# Mutating executors — these CHANGE state (start builds, extend
# profiles, approve plans, control the live emitter). Each one reuses
# the same code path the corresponding HTTP route uses so behaviour
# can't drift between "SE clicked the button" and "SE asked the
# assistant".
#
# Loop-safety note: run_build / run_pipeline_phase call
# asyncio.create_task under the hood (via pipeline_registry). They MUST
# run on the event loop — which they do, because the assistant's
# event_gen async generator (in assistant.py) is driven by the loop, so
# any synchronous call it makes finds the running loop. The Claude-
# calling tool (extend_profile) blocks the loop for the duration of the
# extension call; acceptable on this single-tenant, local-only app.
# ──────────────────────────────────────────────────────────────────


_VALID_PHASES = ("research", "plan", "approve", "generate", "provision", "kg-publish")


def _resolve_build_target(
    session: Session, *, plan_id: str | None, profile_id: str | None,
) -> tuple[str | None, str | None, str | None]:
    """Best-effort (url, company, source_profile_id) for a build, derived
    from a plan's source profile or a profile directly. Returns Nones it
    can't resolve — the caller validates what the chosen phase needs."""
    url: str | None = None
    company: str | None = None
    resolved_profile_id: str | None = profile_id
    try:
        if plan_id:
            plan = PlanRepo().get(session, plan_id)
            if plan is not None and plan.source_profile_id:
                resolved_profile_id = resolved_profile_id or plan.source_profile_id
        if resolved_profile_id:
            prof = ProfileRepo().get(session, resolved_profile_id)
            if prof is not None and prof.company:
                # primary_url is a Pydantic HttpUrl — coerce to str or it
                # propagates into the pipelines INSERT and psycopg raises
                # "cannot adapt type 'HttpUrl'". (This is what forced the
                # URL-as-str workaround + the duplicate build.)
                url = str(prof.company.primary_url) if prof.company.primary_url else None
                company = prof.company.name or None
    except Exception:  # noqa: BLE001 — resolution is best-effort
        pass
    return url, company, resolved_profile_id


def _exec_run_build(args: dict[str, Any], session: Session) -> dict[str, Any]:
    """Kick off a build for a company URL. By default it STOPS AT THE PLAN
    (research → plan) — nothing is written to Grafana Cloud until the SE
    reviews + approves the plan, then provisions explicitly. Pass an
    explicit `stop_after_phase` to override (e.g. 'kg-publish' to go all
    the way to a live demo in one shot). Returns the pipeline_id; the
    build runs in the background."""
    from proj_clarion.api.pipeline_registry import start_pipeline
    from proj_clarion.api.routes.pipelines import _existing_profile_for_host, _norm_host
    from proj_clarion.api.url_input import URLValidationError, normalize_company_url

    url = args.get("url")
    if not isinstance(url, str) or not url.strip():
        raise ValueError("url is required to start a build")
    try:
        normalized = normalize_company_url(url)
    except URLValidationError as exc:
        raise ValueError(f"invalid url: {exc}") from exc

    # Dedup — same hard guard the UI/route enforces, no bypass for the
    # agent. A build researches, which would mint a SECOND profile for a
    # company we already have. Block it (the agent should build from the
    # existing profile instead) unless explicitly told to allow a dup.
    if not args.get("allow_duplicate"):
        existing = _existing_profile_for_host(_norm_host(normalized.url))
        if existing:
            raise ValueError(
                f"A profile for {_norm_host(normalized.url)} already exists "
                f"({existing}). Don't research it again — build from that "
                f"profile (run_pipeline_phase phase='plan', profile_id="
                f"'{existing}'), or pass allow_duplicate=true only if the SE "
                f"explicitly wants a fresh, separate profile."
            )

    # Governance default: stop at the plan unless the caller explicitly asks
    # to go further. Keeps the tenant clean — provisioning is gated on an
    # explicit human approval.
    stop_after = args.get("stop_after_phase") or "plan"

    vpd = args.get("volume_per_day")
    state = start_pipeline(
        normalized.url,
        args.get("company"),
        days=int(args.get("days") or 1),
        volume_per_day=int(vpd) if vpd else None,
        stop_after_phase=stop_after,
    )
    gated = stop_after == "plan"
    return {
        "pipeline_id": state.pipeline_id,
        "status":      state.status,
        "url":         state.url,
        "company":     state.company,
        "stop_after_phase": stop_after,
        "watch_url":   f"/pipelines/{state.pipeline_id}",
        "message": (
            f"Build started for {state.company or state.url} — watch it at "
            f"/pipelines/{state.pipeline_id}. "
            + (
                "It stops at the plan (no Cloud changes); review and approve "
                "it to provision and go live."
                if gated else
                f"Running through {stop_after}."
            )
        ),
    }


def _exec_run_pipeline_phase(args: dict[str, Any], session: Session) -> dict[str, Any]:
    """Run (or re-run) a build from a specific phase — the core
    'execute the plan / rebuild this' capability.

      * phase='plan'                                  → re-plan from a profile
      * phase='approve'|'generate'|'provision'|'kg-publish' → run from there on a plan
      * phase='research'                              → full rebuild from the URL

    URL + company are auto-resolved from the plan's profile (or the
    given profile) so the SE doesn't have to repeat them.

    Reuses the profile/plan's existing build IN PLACE (same pipeline_id) —
    never mints a sibling for the same target."""
    from proj_clarion.api.pipeline_registry import run_or_resume_phase

    phase = args.get("phase")
    if phase not in _VALID_PHASES:
        raise ValueError(f"phase must be one of {list(_VALID_PHASES)}")

    plan_id = args.get("plan_id") or None
    profile_id = args.get("profile_id") or None

    # Phase-specific input requirements (mirror the HTTP route).
    if phase in ("approve", "generate", "provision", "kg-publish") and not plan_id:
        raise ValueError(f"phase={phase!r} requires a plan_id")
    if phase == "plan" and not profile_id and not plan_id:
        raise ValueError("phase='plan' requires a profile_id (or a plan_id to derive it from)")

    url, company, resolved_profile_id = _resolve_build_target(
        session, plan_id=plan_id, profile_id=profile_id,
    )
    # Caller-supplied url/company win over derived.
    url = args.get("url") or url
    company = args.get("company") or company
    if phase == "research" and not url:
        raise ValueError("phase='research' requires a url (none could be derived)")

    vpd = args.get("volume_per_day")
    vpd = int(vpd) if vpd else None
    # Governance: re-planning (phase='plan') stops at the plan — it doesn't
    # auto-provision after a re-plan. Downstream phases (generate/provision/
    # kg-publish) run through, since those ARE the explicit "go live" step.
    stop_after = args.get("stop_after_phase") or ("plan" if phase == "plan" else None)
    # supersede=True: the SE/agent knows the in-flight build WON'T satisfy
    # this request (e.g. an extend landed after it started) → cancel + resume
    # the SAME build. Default False reuses an in-flight build that already
    # covers the request — never starts a second one.
    supersede = bool(args.get("supersede"))

    # ONE build per profile/plan: resume the canonical pipeline in place
    # (same pipeline_id), or create the first row. No fan-out.
    state, reused = run_or_resume_phase(
        starting_phase=phase,
        url=url,
        company=company,
        days=int(args.get("days") or 1),
        profile_id=resolved_profile_id,
        plan_id=plan_id,
        volume_per_day=vpd,
        stop_after_phase=stop_after,
        supersede=supersede,
    )
    return {
        "pipeline_id": state.pipeline_id,
        "status":      state.status,
        "phase":       phase,
        "plan_id":     plan_id,
        "profile_id":  resolved_profile_id,
        "reused":      reused,
        "watch_url":   f"/pipelines/{state.pipeline_id}",
        "message": (
            f"Resumed build {state.pipeline_id} from phase '{phase}' (same build, "
            f"no new id). Watch it at /pipelines/{state.pipeline_id}."
            if reused else
            f"Started build {state.pipeline_id} from phase '{phase}'. "
            f"Watch it at /pipelines/{state.pipeline_id}."
        ),
    }


def _exec_extend_profile(args: dict[str, Any], session: Session) -> dict[str, Any]:
    """Run an SE-directed, agent-produced extension of a CompanyProfile
    (the 'Extend research' capability). Appends entities/signals/etc.
    derived from the prompt and records an audit row."""
    profile_id = args.get("profile_id")
    prompt = args.get("prompt")
    if not isinstance(profile_id, str) or not profile_id:
        raise ValueError("profile_id is required")
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("prompt is required (what to research/add)")

    from proj_clarion.api.routes.agents import _extend_profile_inline

    result = _extend_profile_inline(profile_id, prompt)
    return {
        "profile_id": profile_id,
        "summary":    result.get("summary"),
        "additions":  result.get("additions"),
        "message":    f"Profile {profile_id} extended: {result.get('summary')}",
    }


def _exec_approve_plan(args: dict[str, Any], session: Session) -> dict[str, Any]:
    """Approve a plan for provisioning (draft → approved_for_provision).
    Required before a build-from-plan can run provisioning phases."""
    plan_id = args.get("plan_id")
    if not isinstance(plan_id, str) or not plan_id:
        raise ValueError("plan_id is required")
    note = args.get("note") or "Approved via Clarion Assistant"

    from proj_clarion.api.routes.plans import ApproveRequest
    from proj_clarion.api.routes.plans import approve_plan as _approve_route

    result = _approve_route(plan_id, ApproveRequest(note=note, actor="clarion-assistant"))
    return {
        "plan_id":    result.get("plan_id"),
        "from_state": result.get("from_state"),
        "to_state":   result.get("to_state"),
        "message":    f"Plan {result.get('plan_id')} approved for provisioning.",
    }


def _exec_start_demo(args: dict[str, Any], session: Session) -> dict[str, Any]:
    """Start the live telemetry emitter for a plan (RED metrics, logs,
    traces, KG gauges flowing to Grafana Cloud)."""
    plan_id = args.get("plan_id")
    if not isinstance(plan_id, str) or not plan_id:
        raise ValueError("plan_id is required")

    from proj_clarion.api.routes.demo import StartRequest
    from proj_clarion.api.routes.demo import post_start as _start_route

    hours = args.get("duration_hours")
    max_entities = args.get("max_entities")
    req = StartRequest(
        plan_id=plan_id,
        **({"duration_hours": float(hours)} if hours else {}),
        **({"max_entities": int(max_entities)} if max_entities else {}),
    )
    result = _start_route(req)
    return {
        "plan_id":    plan_id,
        "status":     result.get("status"),
        "expires_at": result.get("expires_at"),
        "message":    f"Live demo started for plan {plan_id}.",
    }


def _exec_stop_demo(args: dict[str, Any], session: Session) -> dict[str, Any]:
    """Stop the live telemetry emitter for a plan."""
    plan_id = args.get("plan_id")
    if not isinstance(plan_id, str) or not plan_id:
        raise ValueError("plan_id is required")

    from proj_clarion.api.routes.demo import StopRequest
    from proj_clarion.api.routes.demo import post_stop as _stop_route

    result = _stop_route(StopRequest(plan_id=plan_id))
    return {
        "plan_id": plan_id,
        "stopped": result.get("stopped"),
        "message": f"Live demo stopped for plan {plan_id}.",
    }


def _exec_cancel_build(args: dict[str, Any], session: Session) -> dict[str, Any]:
    """Cancel an in-flight pipeline BUILD (not the live telemetry emitter —
    that's stop_demo). Use this when the SE asks to stop/cancel a build
    that's currently running."""
    from proj_clarion.api.pipeline_registry import cancel_pipeline

    pipeline_id = args.get("pipeline_id")
    if not isinstance(pipeline_id, str) or not pipeline_id:
        raise ValueError("pipeline_id is required to cancel a build")

    cancelled = cancel_pipeline(pipeline_id)
    # cancel_pipeline returns False when the build isn't cancellable on this
    # process — either it already finished/failed, or it was orphaned by an
    # API restart (already terminal in the DB). Report that plainly rather
    # than pretending we stopped something.
    message = (
        f"Build {pipeline_id[:8]} cancellation requested; it will wind down shortly."
        if cancelled else
        f"Build {pipeline_id[:8]} was not cancelled — it's already finished, failed, "
        "or was started before the last API restart (already terminal)."
    )
    return {
        "pipeline_id": pipeline_id,
        "cancelled":   cancelled,
        "watch_url":   f"/pipelines/{pipeline_id}",
        "message":     message,
    }


# ── Delete / cleanup executors (CRUD + Grafana-Cloud hygiene) ──────

def _exec_delete_build(args: dict[str, Any], session: Session) -> dict[str, Any]:
    """Hard-delete a build record (cancels it first if still running).
    Does NOT touch the plan/profile it produced or any Cloud resources."""
    from proj_clarion.api.pipeline_registry import delete_pipeline
    pid = args.get("pipeline_id")
    if not isinstance(pid, str) or not pid:
        raise ValueError("pipeline_id is required")
    deleted = delete_pipeline(pid)
    return {
        "pipeline_id": pid, "deleted": deleted,
        "message": f"Build {pid[:8]} {'deleted' if deleted else 'not found'}.",
    }


def _exec_clear_plan_cloud(args: dict[str, Any], session: Session) -> dict[str, Any]:
    """Remove a plan's Grafana Cloud footprint (dashboards folder + alert
    rules) but KEEP the Clarion plan — the 'clean my tenant, keep the
    plan' action."""
    from proj_clarion.api.routes.plans import clear_plan_cloud as _clear
    plan_id = args.get("plan_id")
    if not isinstance(plan_id, str) or not plan_id:
        raise ValueError("plan_id is required")
    res = _clear(plan_id)
    cleared = bool(res.get("cleared"))
    return {
        "plan_id": res.get("plan_id"), "cleared": cleared,
        "message": (
            f"Cleared Grafana Cloud resources for plan {plan_id[:8]} (plan kept)."
            if cleared else
            f"Cloud cleanup for plan {plan_id[:8]} reported an error — check the stack token/scopes."
        ),
    }


def _exec_delete_plan(args: dict[str, Any], session: Session) -> dict[str, Any]:
    """Delete a plan from Clarion. With cleanup_cloud (default true) also
    clears its Grafana Cloud folder + alerts. Cascades KG/events/audit."""
    from proj_clarion.api.routes.plans import delete_plan as _del
    plan_id = args.get("plan_id")
    if not isinstance(plan_id, str) or not plan_id:
        raise ValueError("plan_id is required")
    cleanup = args.get("cleanup_cloud")
    cleanup = True if cleanup is None else bool(cleanup)
    res = _del(plan_id, cleanup_cloud=cleanup)
    return {
        "plan_id": res.get("plan_id"), "deleted": res.get("deleted"),
        "cloud_cleaned": cleanup,
        "message": f"Plan {str(res.get('plan_id'))[:8]} deleted"
                   + (" + Cloud resources cleared." if cleanup else "."),
    }


def _exec_delete_profile(args: dict[str, Any], session: Session) -> dict[str, Any]:
    """Delete a profile from Clarion. CASCADES to every plan built from it
    (and, with cleanup_cloud, their Cloud resources)."""
    from proj_clarion.api.routes.profiles import delete_profile as _del
    profile_id = args.get("profile_id")
    if not isinstance(profile_id, str) or not profile_id:
        raise ValueError("profile_id is required")
    cleanup = args.get("cleanup_cloud")
    cleanup = True if cleanup is None else bool(cleanup)
    res = _del(profile_id, cleanup_cloud=cleanup)
    return {
        "profile_id": res.get("profile_id"), "deleted": res.get("deleted"),
        "cascaded_plans": res.get("cascaded_plans"),
        "message": f"Profile {profile_id} deleted "
                   f"({res.get('cascaded_plans')} plan(s) cascaded)"
                   + (" + Cloud cleared." if cleanup else "."),
    }


def _exec_cleanup_orphan_folders(args: dict[str, Any], session: Session) -> dict[str, Any]:
    """Remove orphan Grafana Cloud folders (clarion-* folders whose plan is
    no longer in Clarion). Pass `uids` to restrict to specific folders;
    omit to clear them all."""
    from proj_clarion.api.routes.orphans import delete_orphan as _del
    from proj_clarion.api.routes.orphans import list_orphans as _list
    only = args.get("uids")
    folders = _list()
    targets = [f for f in folders if (not only or f.uid in only)]
    deleted: list[str] = []
    for f in targets:
        try:
            _del(f.uid)
            deleted.append(f.uid)
        except Exception:  # noqa: BLE001 — keep going; report what we removed
            continue
    return {
        "deleted_count": len(deleted), "deleted_uids": deleted,
        "message": (
            f"Removed {len(deleted)} orphan Cloud folder(s)."
            if deleted else "No orphan Cloud folders to remove."
        ),
    }


# ──────────────────────────────────────────────────────────────────
# Anthropic tool definitions
# ──────────────────────────────────────────────────────────────────

_LIST_LIMIT_PROP: dict[str, Any] = {
    "type": "integer",
    "minimum": 1,
    "maximum": _MAX_LIST_LIMIT,
    "default": _DEFAULT_LIST_LIMIT,
    "description": f"Max rows to return (1–{_MAX_LIST_LIMIT}, default {_DEFAULT_LIST_LIMIT}).",
}


TOOL_LIST_PROFILES: dict[str, Any] = {
    "name": "list_profiles",
    "description": (
        "List CompanyProfiles the SE has researched, newest first. Returns "
        "slim rows (id, company_name, host, pain/tech signal counts). For "
        "the full profile contents call get_profile."
    ),
    "input_schema": {
        "type": "object",
        "properties": {"limit": _LIST_LIMIT_PROP},
        "required": [],
    },
}

TOOL_GET_PROFILE: dict[str, Any] = {
    "name": "get_profile",
    "description": (
        "Fetch the full CompanyProfile JSON by profile_id, including "
        "channels, pain signals, tech stack, geographic footprint, etc. "
        "Large — only call when you need the detail."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "profile_id": {"type": "string", "description": "Profile id (e.g. 'prof-abc123')."},
        },
        "required": ["profile_id"],
    },
}

TOOL_LIST_PLANS: dict[str, Any] = {
    "name": "list_plans",
    "description": (
        "List DemoPlans, newest-updated first. Slim rows (id, profile_id, "
        "review_state, counts of processes/KG nodes/alerts/dashboards). "
        "Optional source_profile_id filter scopes to one profile's plans."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "limit": _LIST_LIMIT_PROP,
            "source_profile_id": {
                "type": "string",
                "description": "Only return plans built from this profile.",
            },
        },
        "required": [],
    },
}

TOOL_GET_PLAN: dict[str, Any] = {
    "name": "get_plan",
    "description": (
        "Fetch the full DemoPlan JSON by plan_id — includes the knowledge "
        "graph, business processes, incident script, dashboards, alerts. "
        "Very large for plans with big KGs; only call when needed."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "plan_id": {"type": "string", "description": "Plan id (UUID)."},
        },
        "required": ["plan_id"],
    },
}

TOOL_LIST_PIPELINES: dict[str, Any] = {
    "name": "list_pipelines",
    "description": (
        "List recent pipeline runs (builds), newest first. Each row has "
        "status, url, profile_id, plan_id, started_at, phases_done, "
        "current_phase. Filter by profile_id, plan_id, and/or status. Use "
        "profile_id + status='running' to check whether a profile ALREADY has "
        "a build in flight BEFORE kicking one — there must only ever be one."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "limit":      _LIST_LIMIT_PROP,
            "profile_id": {"type": "string", "description": "Filter to pipelines for one profile."},
            "plan_id":    {"type": "string", "description": "Filter to pipelines that landed on one plan."},
            "status":     {"type": "string", "description": "Filter by status, e.g. 'running', 'failed', 'done'."},
        },
        "required": [],
    },
}

TOOL_GET_PIPELINE: dict[str, Any] = {
    "name": "get_pipeline",
    "description": (
        "Single pipeline run by id — status, url, attached profile + plan, "
        "phase progress, error message if failed."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "pipeline_id": {"type": "string", "description": "Pipeline id (UUID)."},
        },
        "required": ["pipeline_id"],
    },
}

TOOL_LIST_DEMO_SESSIONS: dict[str, Any] = {
    "name": "list_demo_sessions",
    "description": (
        "List currently-active emitter sessions (live telemetry flowing to "
        "Grafana Cloud). Returns plan_id, status, started_at, expires_at, "
        "last_heartbeat_at. Optional plan_id filter to check one plan."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "plan_id": {"type": "string", "description": "Filter to one plan's sessions."},
        },
        "required": [],
    },
}

TOOL_GET_AUDIT_LOG: dict[str, Any] = {
    "name": "get_audit_log",
    "description": (
        "Recent audit entries — profile extends and plan state transitions. "
        "Useful for 'what changed recently' / 'who approved this plan' / "
        "'what was added in the last extend'."
    ),
    "input_schema": {
        "type": "object",
        "properties": {"limit": _LIST_LIMIT_PROP},
        "required": [],
    },
}


# ──────────────────────────────────────────────────────────────────
# Mutating tool definitions
# ──────────────────────────────────────────────────────────────────

TOOL_RUN_BUILD: dict[str, Any] = {
    "name": "run_build",
    "description": (
        "Start a build for a company URL. By DEFAULT it stops at the plan "
        "(research → plan) and writes NOTHING to Grafana Cloud — the SE then "
        "reviews + approves the plan, and provisioning happens as an explicit "
        "next step. This is the normal path: build → review → approve → "
        "provision. Returns a pipeline_id immediately; tell the SE they can "
        "watch it at the returned watch_url and that it'll stop at a reviewable "
        "plan. Only set stop_after_phase to go further: 'research' = profile "
        "only; 'kg-publish' = run all the way to a live demo in one shot (use "
        "ONLY when the SE explicitly says go live / provision now). If the SE "
        "only DESCRIBED a customer or picked a scenario (no URL), infer the "
        "best-fit real company's homepage URL yourself and pass it here — don't "
        "ask for a URL unless the scenario is too ambiguous to pick a sensible "
        "representative."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "url":     {"type": "string", "description": "Company homepage URL to research and build a demo for."},
            "company": {"type": "string", "description": "Optional friendly company name (otherwise derived from the page)."},
            "days":    {"type": "integer", "minimum": 1, "maximum": 30, "description": "Days of history to backfill (default 1)."},
            "volume_per_day": {"type": "integer", "minimum": 100, "maximum": 100000, "description": "Override events/day. Omit to auto-scale. 500 = quick smoke build."},
            "stop_after_phase": {
                "type": "string",
                "enum": list(_VALID_PHASES),
                "description": "Stop after this phase succeeds. Use 'research' for profile-only.",
            },
            "allow_duplicate": {
                "type": "boolean",
                "description": "Only set true if the SE explicitly wants a fresh, separate profile for a company that already has one. Otherwise leave unset — the build is blocked when a profile for the host already exists (build from that profile instead).",
            },
        },
        "required": ["url"],
    },
}

TOOL_RUN_PIPELINE_PHASE: dict[str, Any] = {
    "name": "run_pipeline_phase",
    "description": (
        "Run or RE-RUN a build starting from a specific phase — the core "
        "'execute this plan' / 'rebuild from here' capability used when "
        "refining an existing demo. Phases: 'plan' (re-plan from a profile), "
        "'approve'/'generate'/'provision'/'kg-publish' (run from there on an "
        "existing plan), 'research' (full rebuild). URL + company are "
        "auto-derived from the plan's profile. Typical refine flow: "
        "extend_profile (if needed) then run_pipeline_phase phase='plan' to "
        "regenerate the plan from the richer profile.\n\n"
        "REUSES THE PROFILE/PLAN'S EXISTING BUILD IN PLACE — it returns the "
        "SAME pipeline_id and resumes the requested phase, never a new build "
        "row. So there is ALWAYS at most one build per profile. Calling this "
        "again after a failure just resumes that same build from the failed "
        "phase. NEVER start a second build for a profile that already has "
        "one — there is no need, and the SE has explicitly forbidden it.\n\n"
        "If a build is already RUNNING for this profile/plan: by default this "
        "reuses it (returns the running id, starts nothing new). Only set "
        "supersede=true when the in-flight build genuinely WON'T satisfy the "
        "request — e.g. you just extend_profile'd AFTER it started, so it must "
        "be cancelled and re-run with the new data. supersede cancels the "
        "in-flight run and resumes the SAME id; it does not create a new one."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "phase":      {"type": "string", "enum": list(_VALID_PHASES), "description": "Phase to start from."},
            "plan_id":    {"type": "string", "description": "Plan to build from (required for approve/generate/provision/kg-publish)."},
            "profile_id": {"type": "string", "description": "Profile to plan from (required for phase='plan' if no plan_id)."},
            "url":        {"type": "string", "description": "Override URL (usually auto-derived). A DIFFERENT host forks a new build; same host always reuses."},
            "company":    {"type": "string", "description": "Override company name (usually auto-derived)."},
            "days":       {"type": "integer", "minimum": 1, "maximum": 30, "description": "Days of history (default 1)."},
            "volume_per_day": {"type": "integer", "minimum": 100, "maximum": 100000, "description": "Override events/day."},
            "supersede":  {"type": "boolean", "description": "Cancel an in-flight build for this profile and resume the SAME id from this phase. Use ONLY when the running build won't reflect a change you just made (e.g. a fresh extend_profile). Default false reuses the in-flight build. Never creates a new build either way."},
        },
        "required": ["phase"],
    },
}

TOOL_EXTEND_PROFILE: dict[str, Any] = {
    "name": "extend_profile",
    "description": (
        "Extend a CompanyProfile with new research — add entities, pain "
        "signals, tech-stack signals, channels, strategic priorities, etc. "
        "derived from the SE's prompt. This is the 'Extend research' "
        "capability. Mutates the profile and records an audit row. After "
        "extending, you usually want to run_pipeline_phase(phase='plan') so "
        "the plan picks up the new profile data."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "profile_id": {"type": "string", "description": "Profile to extend."},
            "prompt":     {"type": "string", "description": "What to research/add, e.g. 'add their SAP S/4HANA and Snowflake stack and the Frankfurt plant'."},
        },
        "required": ["profile_id", "prompt"],
    },
}

TOOL_APPROVE_PLAN: dict[str, Any] = {
    "name": "approve_plan",
    "description": (
        "Approve a draft plan for provisioning (draft → "
        "approved_for_provision). Required before provisioning phases can "
        "run. Records an audit row with the note."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "plan_id": {"type": "string", "description": "Plan to approve."},
            "note":    {"type": "string", "description": "Approval note for the audit log."},
        },
        "required": ["plan_id"],
    },
}

TOOL_START_DEMO: dict[str, Any] = {
    "name": "start_demo",
    "description": (
        "Start the live telemetry emitter for a provisioned plan — RED "
        "metrics, logs, traces, and KG entity gauges flow to Grafana Cloud so "
        "the demo looks real. Plan must be provisioned first."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "plan_id":        {"type": "string", "description": "Plan whose demo to start."},
            "duration_hours": {"type": "number", "minimum": 0.25, "maximum": 8, "description": "Auto-stop after N hours (default 2)."},
            "max_entities":   {"type": "integer", "minimum": 1, "maximum": 10000, "description": "Optional cap on materialised entities for a cleaner entity-graph view."},
        },
        "required": ["plan_id"],
    },
}

TOOL_STOP_DEMO: dict[str, Any] = {
    "name": "stop_demo",
    "description": "Stop the live telemetry emitter for a plan.",
    "input_schema": {
        "type": "object",
        "properties": {
            "plan_id": {"type": "string", "description": "Plan whose demo to stop."},
        },
        "required": ["plan_id"],
    },
}

TOOL_CANCEL_BUILD: dict[str, Any] = {
    "name": "cancel_build",
    "description": (
        "Cancel a pipeline BUILD that's currently running (research → … → "
        "kg-publish). Use this when the SE says to stop/cancel/abort a build. "
        "This is NOT the same as stop_demo (which stops live telemetry for a "
        "finished plan). If you don't know the pipeline_id, call list_pipelines "
        "first and pick the running one."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "pipeline_id": {"type": "string", "description": "The running build's pipeline_id to cancel."},
        },
        "required": ["pipeline_id"],
    },
}

TOOL_DELETE_BUILD: dict[str, Any] = {
    "name": "delete_build",
    "description": (
        "Hard-delete a build RECORD (cancels it first if still running, then "
        "removes the row + its logs). Use for a wedged/runaway build that won't "
        "stop. Does NOT delete the plan/profile it produced or any Grafana Cloud "
        "resources — use delete_plan / clear_plan_cloud for those."
    ),
    "input_schema": {
        "type": "object",
        "properties": {"pipeline_id": {"type": "string", "description": "The build's pipeline_id."}},
        "required": ["pipeline_id"],
    },
}

TOOL_CLEAR_PLAN_CLOUD: dict[str, Any] = {
    "name": "clear_plan_cloud",
    "description": (
        "Remove a plan's Grafana Cloud footprint (its dashboards folder + alert "
        "rules) while KEEPING the Clarion plan. This is the 'clean up my tenant "
        "but don't lose the plan' action — the plan reverts to not-provisioned "
        "so it can be re-provisioned later. Use when the SE wants to tidy Cloud "
        "without deleting Clarion data."
    ),
    "input_schema": {
        "type": "object",
        "properties": {"plan_id": {"type": "string", "description": "The plan whose Cloud resources to clear."}},
        "required": ["plan_id"],
    },
}

TOOL_DELETE_PLAN: dict[str, Any] = {
    "name": "delete_plan",
    "description": (
        "Delete a plan from Clarion entirely (cascades its KG nodes/edges, "
        "generated events, audit). By default ALSO clears its Grafana Cloud "
        "folder + alerts (cleanup_cloud=true) — set false to leave Cloud as-is. "
        "Destructive; confirm with the SE first."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "plan_id": {"type": "string", "description": "The plan to delete."},
            "cleanup_cloud": {"type": "boolean", "description": "Also remove the plan's Cloud folder + alerts (default true)."},
        },
        "required": ["plan_id"],
    },
}

TOOL_DELETE_PROFILE: dict[str, Any] = {
    "name": "delete_profile",
    "description": (
        "Delete a CompanyProfile from Clarion. CASCADES to EVERY plan built from "
        "it (and, with cleanup_cloud=true, their Cloud resources). Very "
        "destructive — always confirm with the SE and name how many plans will "
        "go with it (call list_plans with source_profile_id first if unsure)."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "profile_id": {"type": "string", "description": "The profile to delete."},
            "cleanup_cloud": {"type": "boolean", "description": "Also clear each cascaded plan's Cloud resources (default true)."},
        },
        "required": ["profile_id"],
    },
}

TOOL_CLEANUP_ORPHAN_FOLDERS: dict[str, Any] = {
    "name": "cleanup_orphan_folders",
    "description": (
        "Tenant hygiene: remove orphan Grafana Cloud folders — clarion-* folders "
        "whose plan no longer exists in Clarion (left behind by a delete that "
        "skipped Cloud cleanup). Omit `uids` to clear all of them; pass `uids` to "
        "target specific folders."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "uids": {"type": "array", "items": {"type": "string"}, "description": "Optional: only these folder uids. Omit to clear all orphans."},
        },
        "required": [],
    },
}


# ──────────────────────────────────────────────────────────────────
# Registries
# ──────────────────────────────────────────────────────────────────

# Tool definitions for the Anthropic `tools` request param.
TOOLS_READONLY: list[dict[str, Any]] = [
    TOOL_LIST_PROFILES,
    TOOL_GET_PROFILE,
    TOOL_LIST_PLANS,
    TOOL_GET_PLAN,
    TOOL_LIST_PIPELINES,
    TOOL_GET_PIPELINE,
    TOOL_LIST_DEMO_SESSIONS,
    TOOL_GET_AUDIT_LOG,
]

# Tools that change state. Gated separately so a future "read-only
# mode" can ship just TOOLS_READONLY without code churn.
TOOLS_MUTATING: list[dict[str, Any]] = [
    TOOL_RUN_BUILD,
    TOOL_RUN_PIPELINE_PHASE,
    TOOL_EXTEND_PROFILE,
    TOOL_APPROVE_PLAN,
    TOOL_START_DEMO,
    TOOL_STOP_DEMO,
    TOOL_CANCEL_BUILD,
    # CRUD + Grafana-Cloud hygiene
    TOOL_DELETE_BUILD,
    TOOL_CLEAR_PLAN_CLOUD,
    TOOL_DELETE_PLAN,
    TOOL_DELETE_PROFILE,
    TOOL_CLEANUP_ORPHAN_FOLDERS,
]

# The full catalog the global assistant exposes.
TOOLS_ALL: list[dict[str, Any]] = [*TOOLS_READONLY, *TOOLS_MUTATING]

# Names of mutating tools — the endpoint/UI uses this to badge tool
# calls that change state vs. read-only lookups.
MUTATING_TOOL_NAMES: frozenset[str] = frozenset(t["name"] for t in TOOLS_MUTATING)

# Tools that PAUSE for an explicit human Approve (when approval mode is on,
# the default): the expensive build kickoffs AND the destructive
# delete/cloud-teardown actions. Everything else (lookups, extend_profile,
# approve_plan, demo controls, cancel_build) runs freely.
NEEDS_APPROVAL_TOOL_NAMES: frozenset[str] = frozenset({
    "run_build", "run_pipeline_phase",
    "delete_build", "delete_plan", "delete_profile",
    "clear_plan_cloud", "cleanup_orphan_folders",
})

# Executor lookup. Type alias kept loose because the executors all
# accept (input_dict, session) and return JSON-serializable values.
ToolExecutor = Callable[[dict[str, Any], Session], Any]

EXECUTORS_READONLY: dict[str, ToolExecutor] = {
    "list_profiles":      _exec_list_profiles,
    "get_profile":        _exec_get_profile,
    "list_plans":         _exec_list_plans,
    "get_plan":           _exec_get_plan,
    "list_pipelines":     _exec_list_pipelines,
    "get_pipeline":       _exec_get_pipeline,
    "list_demo_sessions": _exec_list_demo_sessions,
    "get_audit_log":      _exec_get_audit_log,
}

EXECUTORS_MUTATING: dict[str, ToolExecutor] = {
    "run_build":          _exec_run_build,
    "run_pipeline_phase": _exec_run_pipeline_phase,
    "extend_profile":     _exec_extend_profile,
    "approve_plan":       _exec_approve_plan,
    "start_demo":         _exec_start_demo,
    "stop_demo":          _exec_stop_demo,
    "cancel_build":       _exec_cancel_build,
    "delete_build":          _exec_delete_build,
    "clear_plan_cloud":      _exec_clear_plan_cloud,
    "delete_plan":           _exec_delete_plan,
    "delete_profile":        _exec_delete_profile,
    "cleanup_orphan_folders": _exec_cleanup_orphan_folders,
}

# Full lookup the endpoint dispatches through.
ALL_EXECUTORS: dict[str, ToolExecutor] = {
    **EXECUTORS_READONLY,
    **EXECUTORS_MUTATING,
}


def execute_tool(
    name: str, args: dict[str, Any], session: Session,
) -> tuple[Any, bool]:
    """Run a registered tool (read-only OR mutating). Returns
    (result, is_error). On error returns ({'error': msg}, True) so
    callers can fold the error into the tool_result block without
    special-case branching."""
    executor = ALL_EXECUTORS.get(name)
    if executor is None:
        return ({"error": f"unknown tool: {name}"}, True)
    try:
        return (executor(args, session), False)
    except Exception as exc:  # noqa: BLE001 — agent must see the error
        return ({"error": f"{type(exc).__name__}: {exc}"}, True)
