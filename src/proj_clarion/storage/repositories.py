"""Repositories — small, focused write/read API per artifact.

Surface intentionally narrow: upsert, get, list, plus the few
state-transition helpers the v0.2 review flow needs. We don't expose
generic CRUD; if you find yourself wanting it, add a method here so
the surface stays auditable.

All methods take an explicit Session — repos don't manage transactions.
The caller wraps work in `with session_scope() as s:`.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import bindparam, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session

from proj_clarion.schemas import (
    CompanyProfile,
    DemoPlan,
    KGEdge,
    KGNode,
    KnowledgeGraph,
    ReviewState,
)


def _to_jsonable(model: Any) -> Any:
    """Pydantic → JSON-safe dict via model_dump_json round-trip (handles datetimes/URLs)."""
    return json.loads(model.model_dump_json())


# ============================================================
# ProfileRepo
# ============================================================

class ProfileRepo:
    def upsert(self, session: Session, profile: CompanyProfile) -> None:
        session.execute(
            text("""
                INSERT INTO company_profiles (profile_id, source_url, profile_json)
                VALUES (:pid, :url, :payload)
                ON CONFLICT (profile_id) DO UPDATE
                    SET source_url = EXCLUDED.source_url,
                        profile_json = EXCLUDED.profile_json
            """).bindparams(bindparam("payload", type_=JSONB)),
            {
                "pid": profile.profile_id,
                "url": str(profile.company.primary_url),
                "payload": _to_jsonable(profile),
            },
        )

    def get(self, session: Session, profile_id: str) -> CompanyProfile | None:
        row = session.execute(
            text("SELECT profile_json FROM company_profiles WHERE profile_id = :pid"),
            {"pid": profile_id},
        ).fetchone()
        if not row:
            return None
        return CompanyProfile.model_validate(row[0])

    def delete(self, session: Session, profile_id: str) -> bool:
        """Drop the profile. Cascades to demo_plans → kg_nodes/edges,
        business_events, plan_audit_log via FK ON DELETE CASCADE.
        Returns True if a row was deleted."""
        result = session.execute(
            text("DELETE FROM company_profiles WHERE profile_id = :pid"),
            {"pid": profile_id},
        )
        return (result.rowcount or 0) > 0

    def list(self, session: Session, limit: int = 50) -> list[tuple[str, datetime, str]]:
        """Return [(profile_id, created_at, source_url), ...] newest first."""
        rows = session.execute(
            text("""
                SELECT profile_id, created_at, source_url
                FROM company_profiles
                ORDER BY created_at DESC
                LIMIT :lim
            """),
            {"lim": limit},
        ).fetchall()
        return [(r[0], r[1], r[2]) for r in rows]


# ============================================================
# PlanRepo
# ============================================================

class PlanRepo:
    def upsert(self, session: Session, plan: DemoPlan) -> None:
        session.execute(
            text("""
                INSERT INTO demo_plans (plan_id, source_profile_id, plan_json, review_state)
                VALUES (:pid, :spid, :payload, :state)
                ON CONFLICT (plan_id) DO UPDATE
                    SET source_profile_id = EXCLUDED.source_profile_id,
                        plan_json         = EXCLUDED.plan_json,
                        review_state      = EXCLUDED.review_state
            """).bindparams(bindparam("payload", type_=JSONB)),
            {
                "pid": str(plan.plan_id),
                "spid": plan.source_profile_id,
                "payload": _to_jsonable(plan),
                "state": plan.review_state.value,
            },
        )

    def get(self, session: Session, plan_id: UUID | str) -> DemoPlan | None:
        row = session.execute(
            text("SELECT plan_json FROM demo_plans WHERE plan_id = :pid"),
            {"pid": str(plan_id)},
        ).fetchone()
        if not row:
            return None
        return DemoPlan.model_validate(row[0])

    def delete(self, session: Session, plan_id: UUID | str) -> bool:
        """Drop the plan. Cascades to kg_nodes, kg_edges, business_events,
        and plan_audit_log via FK ON DELETE CASCADE. Returns True if a
        row was deleted.

        Caller's responsibility: stop any kg-publish emitter still running
        for this plan, and call `provision clear` if Cloud-side dashboards
        + alerts should be torn down too. This method only touches Postgres.
        """
        result = session.execute(
            text("DELETE FROM demo_plans WHERE plan_id = :pid"),
            {"pid": str(plan_id)},
        )
        return (result.rowcount or 0) > 0

    def list(self, session: Session, limit: int = 50) -> list[tuple[UUID, datetime, str, str]]:
        """Return [(plan_id, updated_at, source_profile_id, review_state), ...] newest first."""
        rows = session.execute(
            text("""
                SELECT plan_id, updated_at, source_profile_id, review_state
                FROM demo_plans
                ORDER BY updated_at DESC
                LIMIT :lim
            """),
            {"lim": limit},
        ).fetchall()
        return [(UUID(str(r[0])), r[1], r[2], r[3]) for r in rows]

    def set_review_state(
        self,
        session: Session,
        plan_id: UUID | str,
        new_state: ReviewState,
    ) -> str | None:
        """Update review_state. Returns the previous state, or None if plan not found."""
        prev = session.execute(
            text("SELECT review_state FROM demo_plans WHERE plan_id = :pid"),
            {"pid": str(plan_id)},
        ).fetchone()
        if not prev:
            return None
        session.execute(
            text("""
                UPDATE demo_plans
                SET review_state = :st,
                    plan_json = jsonb_set(plan_json, '{review_state}', to_jsonb(CAST(:st AS text)))
                WHERE plan_id = :pid
            """),
            {"st": new_state.value, "pid": str(plan_id)},
        )
        return prev[0]


# ============================================================
# KGRepo — flattens a plan's knowledge graph into kg_nodes / kg_edges.
# JSON in demo_plans.plan_json is the source of truth; these tables exist
# to make graph-style queries easy from SQL/dashboards.
# ============================================================

class KGRepo:
    def replace(self, session: Session, plan_id: UUID | str, kg: KnowledgeGraph) -> None:
        """Wipe the plan's nodes/edges and write fresh ones. Atomic within the session."""
        # Edges first (they FK nodes)
        session.execute(
            text("DELETE FROM kg_edges WHERE plan_id = :pid"),
            {"pid": str(plan_id)},
        )
        session.execute(
            text("DELETE FROM kg_nodes WHERE plan_id = :pid"),
            {"pid": str(plan_id)},
        )
        if kg.nodes:
            session.execute(
                text("""
                    INSERT INTO kg_nodes (plan_id, node_id, node_type, subtype, label,
                                          attributes, live_state_binding)
                    VALUES (:plan_id, :node_id, :node_type, :subtype, :label,
                            :attributes, :live_state_binding)
                """).bindparams(
                    bindparam("attributes", type_=JSONB),
                    bindparam("live_state_binding", type_=JSONB),
                ),
                [self._node_row(plan_id, n) for n in kg.nodes],
            )
        if kg.edges:
            session.execute(
                text("""
                    INSERT INTO kg_edges (plan_id, edge_id, edge_type, from_node_id,
                                          to_node_id, attributes)
                    VALUES (:plan_id, :edge_id, :edge_type, :from_node_id,
                            :to_node_id, :attributes)
                """).bindparams(bindparam("attributes", type_=JSONB)),
                [self._edge_row(plan_id, e) for e in kg.edges],
            )

    def nodes_for_plan(self, session: Session, plan_id: UUID | str) -> list[KGNode]:
        rows = session.execute(
            text("""
                SELECT node_id, node_type, subtype, label, attributes, live_state_binding
                FROM kg_nodes WHERE plan_id = :pid
                ORDER BY node_type, node_id
            """),
            {"pid": str(plan_id)},
        ).fetchall()
        return [self._row_to_node(r) for r in rows]

    def edges_for_plan(self, session: Session, plan_id: UUID | str) -> list[KGEdge]:
        rows = session.execute(
            text("""
                SELECT edge_id, edge_type, from_node_id, to_node_id, attributes
                FROM kg_edges WHERE plan_id = :pid
                ORDER BY edge_id
            """),
            {"pid": str(plan_id)},
        ).fetchall()
        return [
            KGEdge(
                edge_id=r[0],
                edge_type=r[1],
                from_node_id=r[2],
                to_node_id=r[3],
                attributes=r[4] or {},
            )
            for r in rows
        ]

    def graph_for_plan(self, session: Session, plan_id: UUID | str) -> KnowledgeGraph:
        return KnowledgeGraph(
            nodes=self.nodes_for_plan(session, plan_id),
            edges=self.edges_for_plan(session, plan_id),
        )

    @staticmethod
    def _node_row(plan_id: UUID | str, n: KGNode) -> dict[str, Any]:
        subtype = n.business_subtype or n.technical_subtype or n.agentic_subtype
        return {
            "plan_id": str(plan_id),
            "node_id": n.node_id,
            "node_type": n.node_type.value,
            "subtype": subtype,
            "label": n.label,
            "attributes": n.attributes,
            "live_state_binding": (
                _to_jsonable(n.live_state_binding) if n.live_state_binding else None
            ),
        }

    @staticmethod
    def _edge_row(plan_id: UUID | str, e: KGEdge) -> dict[str, Any]:
        return {
            "plan_id": str(plan_id),
            "edge_id": e.edge_id,
            "edge_type": e.edge_type.value,
            "from_node_id": e.from_node_id,
            "to_node_id": e.to_node_id,
            "attributes": e.attributes,
        }

    @staticmethod
    def _row_to_node(r: Any) -> KGNode:
        node_id, node_type, subtype, label, attributes, lsb = r
        kw: dict[str, Any] = {
            "node_id": node_id,
            "node_type": node_type,
            "label": label,
            "attributes": attributes or {},
        }
        if node_type == "business_entity":
            kw["business_subtype"] = subtype
        elif node_type == "technical_resource":
            kw["technical_subtype"] = subtype
        elif node_type == "agentic_resource":
            kw["agentic_subtype"] = subtype
        if lsb:
            kw["live_state_binding"] = lsb
        return KGNode.model_validate(kw)


# ============================================================
# AuditRepo
# ============================================================

class AuditRepo:
    def record(
        self,
        session: Session,
        plan_id: UUID | str,
        actor: str,
        action: str,
        from_state: str | None = None,
        to_state: str | None = None,
        note: str | None = None,
    ) -> None:
        session.execute(
            text("""
                INSERT INTO plan_audit_log (plan_id, actor, action, from_state, to_state, note)
                VALUES (:pid, :actor, :action, :from_state, :to_state, :note)
            """),
            {
                "pid": str(plan_id),
                "actor": actor,
                "action": action,
                "from_state": from_state,
                "to_state": to_state,
                "note": note,
            },
        )

    def history(
        self, session: Session, plan_id: UUID | str
    ) -> list[tuple[datetime, str, str, str | None, str | None, str | None]]:
        rows = session.execute(
            text("""
                SELECT created_at, actor, action, from_state, to_state, note
                FROM plan_audit_log
                WHERE plan_id = :pid
                ORDER BY created_at ASC
            """),
            {"pid": str(plan_id)},
        ).fetchall()
        return [(r[0], r[1], r[2], r[3], r[4], r[5]) for r in rows]


# ============================================================
# PipelineRepo — full demo-build runs (not individual CLI subprocesses).
# Persisted because v0.7's in-memory dict lost everything on each API
# restart. Surface mirrors the other repos: narrow, explicit methods.
# ============================================================

class PipelineRepo:
    """One row per build, plus an append-only event log and a
    denormalised phase rollup. The event log is the SSE replay source
    of truth; the phase rollup makes /pipelines list cheap.

    All methods take a Session — caller wraps in `with session_scope()`.
    """

    # ── Pipelines (top-level row) ────────────────────────────────────

    def create(
        self,
        session: Session,
        *,
        pipeline_id: str,
        url: str,
        company: str | None,
        days: int,
        started_at: datetime,
        trigger: str = "full",
        starting_phase: str | None = None,
        parent_pipeline_id: str | None = None,
    ) -> None:
        session.execute(
            text("""
                INSERT INTO pipelines (
                    pipeline_id, url, company, days, status, started_at,
                    trigger, starting_phase, parent_pipeline_id
                )
                VALUES (
                    :pid, :url, :company, :days, 'running', :started_at,
                    :trigger, :starting_phase, :parent
                )
            """),
            {
                "pid": pipeline_id,
                "url": url,
                "company": company,
                "days": days,
                "started_at": started_at,
                "trigger": trigger,
                "starting_phase": starting_phase,
                "parent": parent_pipeline_id,
            },
        )

    def update_status(
        self,
        session: Session,
        pipeline_id: str,
        status: str,
        *,
        finished_at: datetime | None = None,
        error: str | None = None,
    ) -> None:
        session.execute(
            text("""
                UPDATE pipelines
                SET status = :status,
                    finished_at = COALESCE(:finished_at, finished_at),
                    error = COALESCE(:error, error)
                WHERE pipeline_id = :pid
            """),
            {
                "pid": pipeline_id,
                "status": status,
                "finished_at": finished_at,
                "error": error,
            },
        )

    def set_profile_id(self, session: Session, pipeline_id: str, profile_id: str) -> None:
        session.execute(
            text("UPDATE pipelines SET profile_id = :p WHERE pipeline_id = :pid"),
            {"pid": pipeline_id, "p": profile_id},
        )

    def set_plan_id(self, session: Session, pipeline_id: str, plan_id: str) -> None:
        session.execute(
            text("UPDATE pipelines SET plan_id = :p WHERE pipeline_id = :pid"),
            {"pid": pipeline_id, "p": str(plan_id)},
        )

    def get(self, session: Session, pipeline_id: str) -> dict[str, Any] | None:
        row = session.execute(
            text("""
                SELECT pipeline_id, url, company, days, status, started_at,
                       finished_at, error, profile_id, plan_id, trigger,
                       starting_phase, parent_pipeline_id
                FROM pipelines WHERE pipeline_id = :pid
            """),
            {"pid": pipeline_id},
        ).fetchone()
        if not row:
            return None
        return {
            "pipeline_id": row[0], "url": row[1], "company": row[2], "days": row[3],
            "status": row[4], "started_at": row[5], "finished_at": row[6],
            "error": row[7], "profile_id": row[8], "plan_id": str(row[9]) if row[9] else None,
            "trigger": row[10], "starting_phase": row[11], "parent_pipeline_id": row[12],
        }

    def list(
        self, session: Session, *, limit: int = 50, status: str | None = None,
    ) -> list[dict[str, Any]]:
        sql = """
            SELECT pipeline_id, url, company, days, status, started_at,
                   finished_at, error, profile_id, plan_id, trigger,
                   starting_phase, parent_pipeline_id,
                   (SELECT COUNT(*) FROM pipeline_events e WHERE e.pipeline_id = p.pipeline_id) AS event_count
            FROM pipelines p
        """
        params: dict[str, Any] = {"lim": limit}
        if status is not None:
            sql += " WHERE status = :st"
            params["st"] = status
        sql += " ORDER BY started_at DESC LIMIT :lim"
        rows = session.execute(text(sql), params).fetchall()
        return [
            {
                "pipeline_id": r[0], "url": r[1], "company": r[2], "days": r[3],
                "status": r[4], "started_at": r[5], "finished_at": r[6],
                "error": r[7], "profile_id": r[8],
                "plan_id": str(r[9]) if r[9] else None,
                "trigger": r[10], "starting_phase": r[11],
                "parent_pipeline_id": r[12], "event_count": r[13],
            }
            for r in rows
        ]

    def reap_orphans(self, session: Session) -> int:
        """On API startup, any pipelines still in `running` state are
        orphans (the asyncio task didn't survive the restart). Mark them
        failed so the UI doesn't show fake spinners forever. Returns the
        count of reaped rows."""
        result = session.execute(
            text("""
                UPDATE pipelines
                SET status = 'failed',
                    error = COALESCE(error, 'orphaned: API restarted while pipeline was running'),
                    finished_at = COALESCE(finished_at, NOW())
                WHERE status = 'running'
            """),
        )
        return result.rowcount or 0

    # ── Pipeline events (SSE replay log) ─────────────────────────────

    def append_events(
        self,
        session: Session,
        pipeline_id: str,
        events: list[dict[str, Any]],
        *,
        first_seq: int,
    ) -> None:
        """Bulk-insert a contiguous batch of events. Caller manages the
        seq counter so we don't have to query MAX(seq) on every append."""
        if not events:
            return
        session.execute(
            text("""
                INSERT INTO pipeline_events (pipeline_id, seq, event)
                VALUES (:pid, :seq, :ev)
            """).bindparams(bindparam("ev", type_=JSONB)),
            [
                {"pid": pipeline_id, "seq": first_seq + i, "ev": ev}
                for i, ev in enumerate(events)
            ],
        )

    def events(
        self,
        session: Session,
        pipeline_id: str,
        *,
        after_seq: int | None = None,
        limit: int | None = None,
    ) -> list[tuple[int, dict[str, Any]]]:
        """Return [(seq, event_json), ...] in seq order. `after_seq`
        lets a stream consumer pick up where it left off."""
        sql = "SELECT seq, event FROM pipeline_events WHERE pipeline_id = :pid"
        params: dict[str, Any] = {"pid": pipeline_id}
        if after_seq is not None:
            sql += " AND seq > :after"
            params["after"] = after_seq
        sql += " ORDER BY seq ASC"
        if limit is not None:
            sql += " LIMIT :lim"
            params["lim"] = limit
        rows = session.execute(text(sql), params).fetchall()
        return [(r[0], r[1]) for r in rows]

    def event_count(self, session: Session, pipeline_id: str) -> int:
        return session.execute(
            text("SELECT COUNT(*) FROM pipeline_events WHERE pipeline_id = :pid"),
            {"pid": pipeline_id},
        ).scalar() or 0

    # ── Phase rollup ─────────────────────────────────────────────────

    def upsert_phase(
        self,
        session: Session,
        pipeline_id: str,
        phase: str,
        *,
        status: str,
        started_at: datetime | None = None,
        finished_at: datetime | None = None,
        error: str | None = None,
        artifact: dict[str, Any] | None = None,
    ) -> None:
        session.execute(
            text("""
                INSERT INTO pipeline_phases (
                    pipeline_id, phase, status, started_at, finished_at, error, artifact
                )
                VALUES (:pid, :phase, :status, :started, :finished, :error, :artifact)
                ON CONFLICT (pipeline_id, phase) DO UPDATE
                    SET status = EXCLUDED.status,
                        started_at = COALESCE(pipeline_phases.started_at, EXCLUDED.started_at),
                        finished_at = COALESCE(EXCLUDED.finished_at, pipeline_phases.finished_at),
                        error = COALESCE(EXCLUDED.error, pipeline_phases.error),
                        artifact = COALESCE(EXCLUDED.artifact, pipeline_phases.artifact)
            """).bindparams(bindparam("artifact", type_=JSONB)),
            {
                "pid": pipeline_id, "phase": phase, "status": status,
                "started": started_at, "finished": finished_at,
                "error": error, "artifact": artifact,
            },
        )

    def phases(self, session: Session, pipeline_id: str) -> list[dict[str, Any]]:
        rows = session.execute(
            text("""
                SELECT phase, status, started_at, finished_at, error, artifact
                FROM pipeline_phases WHERE pipeline_id = :pid
                ORDER BY phase
            """),
            {"pid": pipeline_id},
        ).fetchall()
        return [
            {
                "phase": r[0], "status": r[1], "started_at": r[2],
                "finished_at": r[3], "error": r[4], "artifact": r[5],
            }
            for r in rows
        ]


# ============================================================
# DemoSessionRepo — live-telemetry windows the SE keeps open while
# demoing. Separate from PipelineRepo because the lifecycle is
# fundamentally different: a pipeline is "run the build once," a demo
# session is "keep telemetry flowing until I'm done OR 2 hours pass."
# ============================================================

class DemoSessionRepo:
    """One active session per plan; many historical rows kept for audit.

    All methods are tx-bound (take a Session) so callers control
    commit/rollback boundaries — e.g. the `/api/demo/start` endpoint
    inserts a row + spawns a subprocess + writes back the PID in the
    same transaction so a crash mid-spawn doesn't leave orphans.
    """

    def get_active(self, session: Session, plan_id: UUID | str) -> dict | None:
        """Return the currently-running session for this plan, or None.
        "Running" = status in ('starting','live'). The unique partial
        index on `demo_sessions` enforces at most one such row per plan.
        """
        row = session.execute(text(
            "SELECT id, plan_id, pid, started_at, expires_at, last_heartbeat_at, status, notes "
            "FROM demo_sessions "
            "WHERE plan_id = :p AND status IN ('starting','live') "
            "ORDER BY started_at DESC "
            "LIMIT 1"
        ), {"p": str(plan_id)}).fetchone()
        if row is None:
            return None
        return _row_to_dict(row)

    def start(
        self,
        session: Session,
        plan_id: UUID | str,
        *,
        duration_hours: float = 2.0,
    ) -> dict:
        """Create a new session row in 'starting' state.

        Raises if another session is already active for this plan
        (DB-enforced via partial unique index — won't insert).
        PID is set in a follow-up `set_pid()` call after subprocess.spawn.
        """
        row = session.execute(text(
            "INSERT INTO demo_sessions (plan_id, expires_at, status) "
            "VALUES (:p, now() + (:h || ' hours')::interval, 'starting') "
            "RETURNING id, plan_id, pid, started_at, expires_at, "
            "          last_heartbeat_at, status, notes"
        ), {"p": str(plan_id), "h": str(duration_hours)}).fetchone()
        return _row_to_dict(row)

    def set_pid(self, session: Session, session_id: int, pid: int) -> None:
        """Attach the spawned process's PID. Done in the same tx as start()
        so a crash between spawn + commit leaves a clean DB."""
        session.execute(text(
            "UPDATE demo_sessions SET pid = :pid WHERE id = :id"
        ), {"pid": pid, "id": session_id})

    def heartbeat(self, session: Session, plan_id: UUID | str) -> bool:
        """Bump last_heartbeat_at + flip status to 'live' on the active
        session for this plan. Called by the EntityEmitter on each
        export cycle. Returns True if a row was updated — emitter uses
        the False signal to detect "session was killed externally;
        stop pushing."
        """
        rc = session.execute(text(
            "UPDATE demo_sessions "
            "SET last_heartbeat_at = now(), "
            "    status = CASE WHEN status = 'starting' THEN 'live' ELSE status END "
            "WHERE plan_id = :p AND status IN ('starting','live')"
        ), {"p": str(plan_id)}).rowcount
        return rc > 0

    def stop(
        self,
        session: Session,
        plan_id: UUID | str,
        *,
        reason: str = "stopped",
    ) -> dict | None:
        """Mark the active session terminal. `reason` is one of
        'stopped' (user clicked Stop), 'expired' (sweeper hit
        expires_at), 'crashed' (heartbeat went stale)."""
        row = session.execute(text(
            "UPDATE demo_sessions "
            "SET status = :s, finished_at = now() "
            "WHERE plan_id = :p AND status IN ('starting','live') "
            "RETURNING id, plan_id, pid, started_at, expires_at, "
            "          last_heartbeat_at, status, notes"
        ), {"p": str(plan_id), "s": reason}).fetchone()
        if row is None:
            return None
        return _row_to_dict(row)

    def extend(
        self,
        session: Session,
        plan_id: UUID | str,
        *,
        additional_hours: float,
    ) -> dict | None:
        """Push expires_at forward by `additional_hours` for the active
        session. Returns the updated row, or None if no active session."""
        row = session.execute(text(
            "UPDATE demo_sessions "
            "SET expires_at = expires_at + (:h || ' hours')::interval "
            "WHERE plan_id = :p AND status IN ('starting','live') "
            "RETURNING id, plan_id, pid, started_at, expires_at, "
            "          last_heartbeat_at, status, notes"
        ), {"p": str(plan_id), "h": str(additional_hours)}).fetchone()
        if row is None:
            return None
        return _row_to_dict(row)

    def list_expired(self, session: Session) -> list[dict]:
        """Return sessions past expires_at that the sweeper should kill.
        Sweeper invokes this every ~60s, SIGTERMs each pid, then calls
        `stop(..., reason='expired')` to mark them terminal."""
        rows = session.execute(text(
            "SELECT id, plan_id, pid, started_at, expires_at, "
            "       last_heartbeat_at, status, notes "
            "FROM demo_sessions "
            "WHERE status IN ('starting','live') AND expires_at < now()"
        )).fetchall()
        return [_row_to_dict(r) for r in rows]


def _row_to_dict(row) -> dict:
    """Shared shaper — demo_sessions rows have the same columns
    everywhere we return them, so DRY this in one place."""
    return {
        "id":                 row[0],
        "plan_id":            str(row[1]),
        "pid":                row[2],
        "started_at":         row[3],
        "expires_at":         row[4],
        "last_heartbeat_at":  row[5],
        "status":             row[6],
        "notes":              row[7],
    }


__all__: Iterable[str] = [
    "ProfileRepo", "PlanRepo", "KGRepo", "AuditRepo", "PipelineRepo",
    "DemoSessionRepo",
]
