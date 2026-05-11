"""DemoPlan list + detail + approval transition for the SE review UI."""

from __future__ import annotations

import os
from datetime import datetime
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import text

from proj_clarion.schemas import DemoPlan, ReviewState
from proj_clarion.storage import AuditRepo, PipelineRepo, PlanRepo, session_scope

router = APIRouter(prefix="/api/plans", tags=["plans"])


class PlanSummary(BaseModel):
    """Lean row for the table view."""

    plan_id: str
    plan_id_short: str
    source_profile_id: str
    review_state: str
    updated_at: datetime
    process_count: int
    kg_node_count: int
    kg_edge_count: int
    alert_count: int
    dashboard_count: int
    # Placeholder rows for in-flight builds whose plan hasn't landed yet
    # — the UI shows a "Planning..." card and links to /pipelines?p=<id>.
    pending: bool = False
    pipeline_id: str | None = None
    pipeline_status: str | None = None


class ApproveRequest(BaseModel):
    note: str
    actor: str | None = None


class AuditEntry(BaseModel):
    timestamp: datetime
    actor: str
    action: str
    from_state: str | None
    to_state: str | None
    note: str | None


@router.get("", response_model=list[PlanSummary])
def list_plans(limit: int = 50, state: str | None = None) -> list[PlanSummary]:
    """Newest plans first; optionally filter by review state.

    Also returns in-flight pipelines that have a profile but no plan yet
    as `pending=true` placeholder rows, so the page isn't blank during
    the 1-3 minute window when the planner agent is running."""
    out: list[PlanSummary] = []
    with session_scope() as s:
        # In-flight builds in the planning window — research done
        # (profile_id set), plan hasn't landed yet (plan_id null).
        # Skip when caller filtered by review_state since these don't
        # have one.
        if not state:
            for p in PipelineRepo().list(s, limit=20, status="running"):
                if not p.get("profile_id") or p.get("plan_id"):
                    continue
                out.append(PlanSummary(
                    plan_id=f"pending-{p['pipeline_id']}",
                    plan_id_short=p["pipeline_id"][:8],
                    source_profile_id=p["profile_id"],
                    review_state="planning",
                    updated_at=p["started_at"],
                    process_count=0,
                    kg_node_count=0,
                    kg_edge_count=0,
                    alert_count=0,
                    dashboard_count=0,
                    pending=True,
                    pipeline_id=p["pipeline_id"],
                    pipeline_status=p["status"],
                ))

        repo = PlanRepo()
        for pid, updated_at, source_pid, review_state in repo.list(s, limit=limit):
            if state and review_state != state:
                continue
            plan = repo.get(s, pid)
            if plan is None:
                continue
            out.append(PlanSummary(
                plan_id=str(pid),
                plan_id_short=str(pid)[:8],
                source_profile_id=source_pid,
                review_state=review_state,
                updated_at=updated_at,
                process_count=len(plan.business_process_models),
                kg_node_count=len(plan.knowledge_graph.nodes),
                kg_edge_count=len(plan.knowledge_graph.edges),
                alert_count=len(plan.alert_specs),
                dashboard_count=len(plan.dashboard_specs),
            ))
    return out


@router.get("/{plan_id}", response_model=DemoPlan)
def get_plan(plan_id: str) -> DemoPlan:
    """Full plan for the tree view. Accepts unambiguous prefixes."""
    with session_scope() as s:
        full_id = _resolve_plan_id(s, plan_id)
        if not full_id:
            raise HTTPException(status_code=404, detail=f"no plan matches {plan_id!r}")
        plan = PlanRepo().get(s, full_id)
    if plan is None:
        raise HTTPException(status_code=404, detail=f"plan {full_id} not found")
    return plan


@router.get("/{plan_id}/audit", response_model=list[AuditEntry])
def plan_audit(plan_id: str) -> list[AuditEntry]:
    """Audit history for one plan, oldest first."""
    with session_scope() as s:
        full_id = _resolve_plan_id(s, plan_id)
        if not full_id:
            raise HTTPException(status_code=404, detail=f"no plan matches {plan_id!r}")
        rows = AuditRepo().history(s, full_id)
    return [
        AuditEntry(
            timestamp=ts, actor=actor, action=action,
            from_state=frm, to_state=to, note=note,
        )
        for (ts, actor, action, frm, to, note) in rows
    ]


@router.put("/{plan_id}/json", response_model=DemoPlan)
def replace_plan_json(plan_id: str, payload: dict[str, Any]) -> DemoPlan:
    """Replace the plan_json on an existing demo_plan row.

    Validates the payload against the DemoPlan schema before persisting,
    so a malformed save can't corrupt the row. KG node/edge denormalised
    tables aren't touched — only the JSON-of-record is updated. If the
    SE adds nodes via the JSON editor and wants kg_nodes/edges
    repopulated, they should re-run kg-publish (which re-derives them).

    The plan_id in the path wins over any plan_id in the payload — it's
    not legal to rename a plan via the editor.
    """
    with session_scope() as s:
        full_id = _resolve_plan_id(s, plan_id)
        if not full_id:
            raise HTTPException(status_code=404, detail=f"no plan matches {plan_id!r}")
        # Force-pin plan_id and source_profile_id from the row of record so
        # the editor can't rename or reparent.
        from sqlalchemy import text as _t
        row = s.execute(
            _t("SELECT source_profile_id FROM demo_plans WHERE plan_id = :p"),
            {"p": full_id},
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail=f"plan {full_id} not found")
        payload["plan_id"] = full_id
        payload["source_profile_id"] = row[0]
        try:
            plan = DemoPlan.model_validate(payload)
        except Exception as exc:  # noqa: BLE001 — surface validation as 400
            raise HTTPException(
                status_code=400,
                detail=f"plan JSON failed schema validation: {exc}",
            ) from exc
        PlanRepo().upsert(s, plan)
    return plan


@router.get("/{plan_id}/health")
def plan_health(plan_id: str, customer: str | None = None) -> dict[str, object]:
    """Run the KG doctor against this plan + return the checks as JSON.

    The UI surfaces this on the Plan detail page so SEs can verify the
    KG agent didn't ship broken state. CLI equivalent: `just kg-doctor <id>`.
    """
    from proj_clarion.kg_publish.doctor import run_doctor

    with session_scope() as s:
        full_id = _resolve_plan_id(s, plan_id)
        if not full_id:
            raise HTTPException(status_code=404, detail=f"no plan matches {plan_id!r}")

    report = run_doctor(plan_id=full_id, customer=customer)
    return {
        "plan_id":  report.plan_id,
        "customer": report.customer,
        "passed":   report.passed,
        "counts":   report.counts,
        "summary":  report.summary,
        "checks": [
            {"name": c.name, "status": c.status, "detail": c.detail, "fix": c.fix}
            for c in report.checks
        ],
    }


@router.delete("/{plan_id}")
def delete_plan(plan_id: str, cleanup_cloud: bool = False) -> dict[str, object]:
    """Drop the plan from Postgres. Cascades to kg_nodes/edges,
    business_events, audit log.

    With `?cleanup_cloud=true`, ALSO runs `proj-clarion provision clear`
    against the plan first to remove the dashboards folder + alert
    rules from Grafana Cloud. The Cloud cleanup is best-effort and
    runs synchronously (it's a fast gcx call); failures don't block
    the DB delete but are surfaced in the response.

    Even with cleanup_cloud=true, **metric/log/trace series in
    Mimir/Loki/Tempo are NOT deleted** — those age out of retention
    naturally over ~30 days. Same for Cloud KG entity records, which
    fade as no emitter feeds them anymore. Caller should stop any
    running kg-publish for this plan via the Runs page.
    """
    import subprocess
    from pathlib import Path

    PROJECT_ROOT = Path(__file__).resolve().parents[4]

    with session_scope() as s:
        full_id = _resolve_plan_id(s, plan_id)
        if not full_id:
            raise HTTPException(status_code=404, detail=f"no plan matches {plan_id!r}")
        # Snapshot the cascade footprint before delete so we can report
        from sqlalchemy import text as _t
        events = s.execute(_t("SELECT COUNT(*) FROM business_events WHERE plan_id = :p"), {"p": full_id}).scalar_one()
        nodes  = s.execute(_t("SELECT COUNT(*) FROM kg_nodes WHERE plan_id = :p"),       {"p": full_id}).scalar_one()
        edges  = s.execute(_t("SELECT COUNT(*) FROM kg_edges WHERE plan_id = :p"),       {"p": full_id}).scalar_one()

    cloud_result: dict[str, object] | None = None
    if cleanup_cloud:
        # Best-effort sync subprocess: `proj-clarion provision clear <plan_id> --yes`.
        # The CLI uses --yes to skip the interactive confirm.
        proc = subprocess.run(
            ["uv", "run", "python", "-m", "proj_clarion.cli.main",
             "provision", "clear", full_id, "--yes"],
            cwd=str(PROJECT_ROOT),
            capture_output=True, text=True, timeout=30,
        )
        cloud_result = {
            "ran": True,
            "ok": proc.returncode == 0,
            "stdout_tail": proc.stdout.strip().splitlines()[-3:] if proc.stdout else [],
            "stderr_tail": proc.stderr.strip().splitlines()[-3:] if proc.stderr else [],
        }

    with session_scope() as s:
        deleted = PlanRepo().delete(s, full_id)

    if not deleted:
        raise HTTPException(status_code=404, detail=f"plan {full_id} not found")

    # Clean up the on-disk JSON copy if it exists
    on_disk = PROJECT_ROOT / "data" / "plans" / f"{full_id}.json"
    file_removed = False
    try:
        if on_disk.exists():
            on_disk.unlink()
            file_removed = True
    except OSError:
        pass

    return {
        "deleted": True,
        "plan_id": full_id,
        "cascaded": {
            "business_events": int(events),
            "kg_nodes":        int(nodes),
            "kg_edges":        int(edges),
        },
        "json_file_removed": file_removed,
        "cloud_cleanup":    cloud_result,
    }


@router.post("/{plan_id}/approve")
def approve_plan(plan_id: str, body: ApproveRequest) -> dict[str, str]:
    """draft → approved_for_provision. Mirrors `proj-clarion plan approve`.

    The audit-log write is separate from the state transition; both happen
    in one session so a failure in either rolls back the other.
    """
    if not body.note.strip():
        raise HTTPException(status_code=400, detail="approval note is required")
    actor = body.actor or os.environ.get("USER", "unknown")
    with session_scope() as s:
        full_id = _resolve_plan_id(s, plan_id)
        if not full_id:
            raise HTTPException(status_code=404, detail=f"no plan matches {plan_id!r}")
        prev = PlanRepo().set_review_state(s, full_id, ReviewState.APPROVED_FOR_PROVISION)
        if prev is None:
            raise HTTPException(status_code=404, detail=f"plan {full_id} not found")
        AuditRepo().record(
            s, full_id,
            actor=actor,
            action="approved",
            from_state=prev,
            to_state=ReviewState.APPROVED_FOR_PROVISION.value,
            note=body.note,
        )
    return {
        "plan_id": full_id,
        "from_state": prev,
        "to_state": ReviewState.APPROVED_FOR_PROVISION.value,
    }


def _resolve_plan_id(session: Any, prefix_or_full: str) -> str | None:
    """Same prefix-matching the CLI does — let the UI accept short ids."""
    row = session.execute(
        text("SELECT plan_id FROM demo_plans WHERE plan_id::text = :pid"),
        {"pid": prefix_or_full},
    ).fetchone()
    if row:
        return str(row[0])
    rows = session.execute(
        text("SELECT plan_id FROM demo_plans WHERE plan_id::text LIKE :pat LIMIT 2"),
        {"pat": f"{prefix_or_full}%"},
    ).fetchall()
    if len(rows) == 1:
        return str(rows[0][0])
    return None
