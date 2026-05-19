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

    def list_all(
        self,
        session: Session,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict]:
        """Audit log across every plan, newest first. Joined with
        demo_plans → company_profiles for source URL + company so the
        global Audit page can render a "what happened" row without
        a second fetch."""
        rows = session.execute(
            text("""
                SELECT al.created_at, al.actor, al.action, al.from_state,
                       al.to_state, al.note, al.plan_id,
                       cp.source_url,
                       cp.profile_json->'company'->>'name' AS company_name
                FROM plan_audit_log al
                LEFT JOIN demo_plans dp ON dp.plan_id = al.plan_id
                LEFT JOIN company_profiles cp ON cp.profile_id = dp.source_profile_id
                ORDER BY al.created_at DESC
                LIMIT :lim OFFSET :off
            """),
            {"lim": int(limit), "off": int(offset)},
        ).fetchall()
        return [{
            "created_at":  r[0],
            "actor":       r[1],
            "action":      r[2],
            "from_state":  r[3],
            "to_state":    r[4],
            "note":        r[5],
            "plan_id":     str(r[6]) if r[6] else None,
            "url":         r[7],
            "company":     r[8],
        } for r in rows]

    def count_all(self, session: Session) -> int:
        """Total count of plan_audit_log rows for the audit pagination."""
        row = session.execute(text("SELECT COUNT(*) FROM plan_audit_log")).fetchone()
        return int(row[0]) if row else 0


# ============================================================
# PipelineRepo — full demo-build runs (not individual CLI subprocesses).
# Persisted because v0.7's in-memory dict lost everything on each API
# restart. Surface mirrors the other repos: narrow, explicit methods.
# ============================================================


# ============================================================
# ProfileAuditRepo — extend-research history per CompanyProfile.
# Mirrors AuditRepo but keyed on profile_id and records the SE prompt
# + agent summary + per-field additions counts.
# ============================================================

class ProfileAuditRepo:
    """One row per /api/profiles/{id}/extend call. Append-only.

    `additions` is a JSONB blob of {field_name: count}; we keep counts
    rather than the full added payload so the audit row stays compact
    and the actual additions live on the profile JSON itself."""

    def record(
        self,
        session: Session,
        profile_id: str,
        *,
        prompt: str,
        summary: str,
        additions: dict[str, int],
        applied: bool,
        actor: str = "se",
    ) -> None:
        session.execute(
            text("""
                INSERT INTO profile_audit_log
                  (profile_id, actor, prompt, summary, additions, applied)
                VALUES
                  (:pid, :actor, :prompt, :summary, CAST(:additions AS JSONB), :applied)
            """),
            {
                "pid": profile_id,
                "actor": actor,
                "prompt": prompt,
                "summary": summary,
                "additions": json.dumps(additions),
                "applied": applied,
            },
        )

    def history(
        self, session: Session, profile_id: str, *, limit: int = 100,
    ) -> list[dict[str, Any]]:
        """All extends for one profile, newest first. Powers the Profile
        detail page's history block + the chat-panel reload-on-mount."""
        rows = session.execute(
            text("""
                SELECT audit_id, created_at, profile_id, actor,
                       prompt, summary, additions, applied
                FROM profile_audit_log
                WHERE profile_id = :pid
                ORDER BY created_at DESC
                LIMIT :lim
            """),
            {"pid": profile_id, "lim": int(limit)},
        ).fetchall()
        return [_profile_audit_row(r) for r in rows]

    def list_all(
        self,
        session: Session,
        *,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Audit log across every profile, newest first. Joined with
        company_profiles for source_url + company name so each row
        reads on its own on the global Audit page."""
        rows = session.execute(
            text("""
                SELECT al.audit_id, al.created_at, al.profile_id, al.actor,
                       al.prompt, al.summary, al.additions, al.applied,
                       cp.source_url,
                       cp.profile_json->'company'->>'name' AS company_name
                FROM profile_audit_log al
                LEFT JOIN company_profiles cp ON cp.profile_id = al.profile_id
                ORDER BY al.created_at DESC
                LIMIT :lim OFFSET :off
            """),
            {"lim": int(limit), "off": int(offset)},
        ).fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            d = _profile_audit_row(r)
            d["url"] = r[8]
            d["company"] = r[9]
            out.append(d)
        return out

    def count_all(self, session: Session) -> int:
        row = session.execute(text("SELECT COUNT(*) FROM profile_audit_log")).fetchone()
        return int(row[0]) if row else 0


def _profile_audit_row(r) -> dict[str, Any]:
    """Shape one profile_audit_log row into the dict the API + UI
    consume. Kept narrow on purpose — `additions` is already JSONB
    so the SQLAlchemy driver hands it back as a dict."""
    return {
        "audit_id":    r[0],
        "created_at":  r[1],
        "profile_id":  r[2],
        "actor":       r[3],
        "prompt":      r[4],
        "summary":     r[5],
        "additions":   r[6] or {},
        "applied":     bool(r[7]),
    }


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
        # Two correlated subqueries: phases_done (count of completed
        # phases for this run) and current_phase (whichever phase is
        # currently running, NULL if none). The pipelines list view
        # uses these to render a per-row phase progress bar without
        # an N+1 fetch from the UI.
        sql = """
            SELECT pipeline_id, url, company, days, status, started_at,
                   finished_at, error, profile_id, plan_id, trigger,
                   starting_phase, parent_pipeline_id,
                   (SELECT COUNT(*) FROM pipeline_events e WHERE e.pipeline_id = p.pipeline_id) AS event_count,
                   (SELECT COUNT(*) FROM pipeline_phases pp
                    WHERE pp.pipeline_id = p.pipeline_id AND pp.status = 'done') AS phases_done,
                   (SELECT pp.phase FROM pipeline_phases pp
                    WHERE pp.pipeline_id = p.pipeline_id AND pp.status = 'running' LIMIT 1) AS current_phase
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
                "phases_done": int(r[14] or 0), "current_phase": r[15],
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

    def list_active(self, session: Session) -> list[dict]:
        """Return every currently-running demo session across all plans.

        "Running" = status in ('starting','live'). Used by the dashboard
        Live-demo card to surface what's emitting right now without the
        UI having to fan out per-plan /status polls. Ordered newest-first
        so the SE sees the freshest session at the top.

        Joins `demo_plans` → `company_profiles` so a single roundtrip
        gives the UI the source URL alongside the session metadata. The
        company display name (when present) is pulled from the profile
        JSON; we fall back to source_url for sessions whose plan has no
        company field set.
        """
        rows = session.execute(text(
            "SELECT ds.id, ds.plan_id, ds.pid, ds.started_at, ds.expires_at, "
            "       ds.last_heartbeat_at, ds.status, ds.notes, "
            "       cp.source_url, "
            "       cp.profile_json->'company'->>'name' AS company_name "
            "FROM demo_sessions ds "
            "JOIN demo_plans dp ON dp.plan_id = ds.plan_id "
            "JOIN company_profiles cp ON cp.profile_id = dp.source_profile_id "
            "WHERE ds.status IN ('starting','live') "
            "ORDER BY ds.started_at DESC"
        )).fetchall()
        out: list[dict] = []
        for r in rows:
            d = _row_to_dict(r)
            d["url"] = r[8]
            d["company"] = r[9]
            out.append(d)
        return out

    def list_history(
        self,
        session: Session,
        *,
        limit: int = 100,
        offset: int = 0,
        plan_id: UUID | str | None = None,
    ) -> list[dict]:
        """Audit history of demo sessions, newest first.

        Returns both terminal rows (status in stopped/expired/crashed)
        AND in-flight rows (starting/live) so an SE auditing the demo
        log sees one consistent timeline with the live row at the top.

        `plan_id` optional — when set, scopes to that plan's history
        (used by the per-plan view on Plans-detail). When omitted,
        returns sessions across every plan (used by the global
        `/demos` audit page).

        Joins through `demo_plans` → `company_profiles` so each row
        carries the source URL + company name for display without an
        additional fetch.

        `finished_at` is derived from the row state — for in-flight
        rows it stays NULL. Duration is left for the caller (the API
        layer adds a derived seconds_active field).
        """
        params: dict[str, object] = {"lim": int(limit), "off": int(offset)}
        scope = ""
        if plan_id is not None:
            scope = "AND ds.plan_id = :pid "
            params["pid"] = str(plan_id)
        rows = session.execute(text(
            "SELECT ds.id, ds.plan_id, ds.pid, ds.started_at, ds.expires_at, "
            "       ds.last_heartbeat_at, ds.status, ds.notes, ds.finished_at, "
            "       cp.source_url, "
            "       cp.profile_json->'company'->>'name' AS company_name "
            "FROM demo_sessions ds "
            "JOIN demo_plans dp ON dp.plan_id = ds.plan_id "
            "JOIN company_profiles cp ON cp.profile_id = dp.source_profile_id "
            f"WHERE 1=1 {scope}"
            "ORDER BY ds.started_at DESC "
            "LIMIT :lim OFFSET :off"
        ), params).fetchall()
        out: list[dict] = []
        for r in rows:
            d = _row_to_dict(r)
            d["finished_at"] = r[8]
            d["url"] = r[9]
            d["company"] = r[10]
            out.append(d)
        return out

    def count_history(
        self,
        session: Session,
        *,
        plan_id: UUID | str | None = None,
    ) -> int:
        """Total row count for pagination on the audit page."""
        params: dict[str, object] = {}
        scope = ""
        if plan_id is not None:
            scope = "WHERE plan_id = :pid"
            params["pid"] = str(plan_id)
        row = session.execute(
            text(f"SELECT COUNT(*) FROM demo_sessions {scope}"),
            params,
        ).fetchone()
        return int(row[0]) if row else 0

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


# ============================================================
# LlmCallRepo — one row per Anthropic LLM call. Append-only.
# ============================================================


class LlmCallRepo:
    """Per-call record of every Anthropic API hit. Rows are written
    from the OTel wrapper in `observability/llm_client.py` after each
    call completes, both for pipeline subprocess calls and SE-facing
    extend/refine calls (those write with `pipeline_id`+`phase` NULL).

    The table is the durable counterpart to the gen_ai.* spans in
    Tempo — same numbers, but queryable with plain SQL when the SE
    wants to see "what did this build cost" without leaving the UI."""

    def record(
        self,
        session: Session,
        *,
        call_id: str,
        model: str,
        agent_name: str,
        pipeline_id: str | None = None,
        phase: str | None = None,
        prompt_template: str | None = None,
        prompt_version: str | None = None,
        sigil_generation_id: str | None = None,
        input_tokens: int = 0,
        output_tokens: int = 0,
        cache_read_tokens: int = 0,
        cache_write_tokens: int = 0,
        stop_reason: str | None = None,
        cost_usd: float = 0.0,
        cache_savings_usd: float = 0.0,
        ttft_ms: int | None = None,
        attempt: int = 1,
        error_type: str | None = None,
        is_stream: bool = False,
    ) -> None:
        session.execute(
            text("""
                INSERT INTO llm_calls
                  (call_id, pipeline_id, phase, prompt_template, prompt_version,
                   model, agent_name, sigil_generation_id,
                   input_tokens, output_tokens, cache_read_tokens, cache_write_tokens,
                   stop_reason, cost_usd, cache_savings_usd, ttft_ms,
                   attempt, error_type, is_stream)
                VALUES
                  (:call_id, :pipeline_id, :phase, :prompt_template, :prompt_version,
                   :model, :agent_name, :sigil_generation_id,
                   :input_tokens, :output_tokens, :cache_read_tokens, :cache_write_tokens,
                   :stop_reason, :cost_usd, :cache_savings_usd, :ttft_ms,
                   :attempt, :error_type, :is_stream)
                ON CONFLICT (call_id) DO NOTHING
            """),
            {
                "call_id": call_id,
                "pipeline_id": pipeline_id,
                "phase": phase,
                "prompt_template": prompt_template,
                "prompt_version": prompt_version,
                "model": model,
                "agent_name": agent_name,
                "sigil_generation_id": sigil_generation_id,
                "input_tokens": int(input_tokens),
                "output_tokens": int(output_tokens),
                "cache_read_tokens": int(cache_read_tokens),
                "cache_write_tokens": int(cache_write_tokens),
                "stop_reason": stop_reason,
                "cost_usd": float(cost_usd),
                "cache_savings_usd": float(cache_savings_usd),
                "ttft_ms": ttft_ms,
                "attempt": int(attempt),
                "error_type": error_type,
                "is_stream": bool(is_stream),
            },
        )

    def for_pipeline(
        self, session: Session, pipeline_id: str, *, limit: int = 1000,
    ) -> list[dict[str, Any]]:
        """All llm_calls for one pipeline, oldest first (so the result
        reads in execution order). Limit defaults high — pipelines
        rarely make more than ~10 calls but we don't want to silently
        truncate when one does."""
        rows = session.execute(
            text("""
                SELECT call_id, pipeline_id, phase, prompt_template, prompt_version,
                       model, agent_name, sigil_generation_id,
                       input_tokens, output_tokens, cache_read_tokens, cache_write_tokens,
                       stop_reason, cost_usd, cache_savings_usd, ttft_ms,
                       attempt, error_type, created_at
                FROM llm_calls
                WHERE pipeline_id = :pid
                ORDER BY created_at ASC
                LIMIT :lim
            """),
            {"pid": pipeline_id, "lim": int(limit)},
        ).fetchall()
        return [_llm_call_row(r) for r in rows]

    def aggregate_cost_by_phase(
        self, session: Session, pipeline_id: str,
    ) -> dict[str, float]:
        """Sum of cost_usd grouped by phase for one pipeline. Used by
        the pipeline summary to show 'research: $0.12, plan: $0.34'."""
        rows = session.execute(
            text("""
                SELECT COALESCE(phase, 'unphased') AS phase, SUM(cost_usd)::float AS total
                FROM llm_calls
                WHERE pipeline_id = :pid
                GROUP BY 1
                ORDER BY 1
            """),
            {"pid": pipeline_id},
        ).fetchall()
        return {r[0]: float(r[1] or 0.0) for r in rows}


def _llm_call_row(r) -> dict[str, Any]:
    """Shape one llm_calls row into the dict the API returns. Costs
    are NUMERIC in postgres → Decimal in Python; cast to float for
    JSON-friendliness."""
    return {
        "call_id":              r[0],
        "pipeline_id":          r[1],
        "phase":                r[2],
        "prompt_template":      r[3],
        "prompt_version":       r[4],
        "model":                r[5],
        "agent_name":           r[6],
        "sigil_generation_id":  r[7],
        "input_tokens":         int(r[8] or 0),
        "output_tokens":        int(r[9] or 0),
        "cache_read_tokens":    int(r[10] or 0),
        "cache_write_tokens":   int(r[11] or 0),
        "stop_reason":          r[12],
        "cost_usd":             float(r[13] or 0.0),
        "cache_savings_usd":    float(r[14] or 0.0),
        "ttft_ms":              r[15],
        "attempt":              int(r[16] or 1),
        "error_type":           r[17],
        "created_at":           r[18],
    }


# ============================================================
# LlmEvalRepo — one row per structural eval check. Append-only.
# ============================================================


class LlmEvalRepo:
    """Per-eval record of structural / behavioural checks run after
    a pipeline phase produces its artefact. Each row is a single
    (phase, eval_name) → (passed, score) result. Append-only: re-runs
    produce additional rows so we can graph drift over time."""

    def record(
        self,
        session: Session,
        *,
        phase: str,
        eval_name: str,
        passed: bool,
        pipeline_id: str | None = None,
        score: float | None = None,
        model: str | None = None,
        prompt_version: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        session.execute(
            text("""
                INSERT INTO llm_evals
                  (pipeline_id, phase, eval_name, score, passed,
                   model, prompt_version, details)
                VALUES
                  (:pid, :phase, :name, :score, :passed,
                   :model, :version, CAST(:details AS JSONB))
            """),
            {
                "pid": pipeline_id,
                "phase": phase,
                "name": eval_name,
                "score": score,
                "passed": bool(passed),
                "model": model,
                "version": prompt_version,
                "details": json.dumps(details or {}),
            },
        )

    def for_pipeline(
        self, session: Session, pipeline_id: str,
    ) -> list[dict[str, Any]]:
        rows = session.execute(
            text("""
                SELECT eval_id, pipeline_id, phase, eval_name,
                       score, passed, model, prompt_version, details, created_at
                FROM llm_evals
                WHERE pipeline_id = :pid
                ORDER BY created_at ASC
            """),
            {"pid": pipeline_id},
        ).fetchall()
        return [_llm_eval_row(r) for r in rows]


def _llm_eval_row(r) -> dict[str, Any]:
    """Shape one llm_evals row into the dict the API returns."""
    return {
        "eval_id":         r[0],
        "pipeline_id":     r[1],
        "phase":           r[2],
        "eval_name":       r[3],
        "score":           float(r[4]) if r[4] is not None else None,
        "passed":          bool(r[5]),
        "model":           r[6],
        "prompt_version":  r[7],
        "details":         r[8] or {},
        "created_at":      r[9],
    }


# ============================================================
# AgentToolCallRepo — one row per agent tool invocation.
# Append-only.
# ============================================================


class AgentToolCallRepo:
    """Per-tool-call record. Each row is one external system reach
    (web_search / db_read / db_write / kg_read / kg_write / api_call /
    file_read / shell_exec) made by a Clarion agent.

    Written from the `track_tool_call` context manager in
    `observability/tools.py`. Read by Grafana Cloud dashboards via the
    Postgres datasource for agent-tool heatmaps + latency trends.

    Append-only: re-runs add more rows; nothing is updated in place."""

    def record(
        self,
        session: Session,
        *,
        agent_name: str,
        tool_name: str,
        pipeline_id: str | None = None,
        llm_call_id: str | None = None,
        target_system: str | None = None,
        action: str | None = None,
        input_summary: str | None = None,
        output_summary: str | None = None,
        success: bool = True,
        error_msg: str | None = None,
        duration_ms: int | None = None,
    ) -> str:
        """Insert and return the call_id (UUID as string).

        Truncates input/output/error fields defensively — the context
        manager already trims them but a direct caller might forget."""
        row = session.execute(
            text("""
                INSERT INTO agent_tool_calls
                  (pipeline_id, llm_call_id, agent_name, tool_name,
                   target_system, action, input_summary, output_summary,
                   success, error_msg, duration_ms)
                VALUES
                  (:pid, :llm_call_id, :agent, :tool,
                   :target, :action, :inp, :outp,
                   :ok, :err, :dur)
                RETURNING call_id
            """),
            {
                "pid": pipeline_id,
                "llm_call_id": llm_call_id,
                "agent": agent_name,
                "tool": tool_name,
                "target": target_system,
                "action": action,
                "inp": (input_summary or None) and str(input_summary)[:500],
                "outp": (output_summary or None) and str(output_summary)[:200],
                "ok": bool(success),
                "err": (error_msg or None) and str(error_msg)[:500],
                "dur": duration_ms,
            },
        ).fetchone()
        return str(row[0]) if row else ""

    def list_for_pipeline(
        self, session: Session, pipeline_id: str, *, limit: int = 1000,
    ) -> list[dict[str, Any]]:
        """Every tool call this pipeline made, oldest first (execution
        order). Limit defaults high — pipelines rarely make more than a
        few dozen tool calls but we don't want silent truncation."""
        rows = session.execute(
            text("""
                SELECT call_id, pipeline_id, llm_call_id, agent_name, tool_name,
                       target_system, action, input_summary, output_summary,
                       success, error_msg, duration_ms, created_at
                FROM agent_tool_calls
                WHERE pipeline_id = :pid
                ORDER BY created_at ASC
                LIMIT :lim
            """),
            {"pid": pipeline_id, "lim": int(limit)},
        ).fetchall()
        return [_agent_tool_call_row(r) for r in rows]


def _agent_tool_call_row(r) -> dict[str, Any]:
    """Shape one agent_tool_calls row into the dict the API + Grafana
    panels consume. Kept narrow on purpose."""
    return {
        "call_id":        str(r[0]),
        "pipeline_id":    r[1],
        "llm_call_id":    r[2],
        "agent_name":     r[3],
        "tool_name":      r[4],
        "target_system":  r[5],
        "action":         r[6],
        "input_summary":  r[7],
        "output_summary": r[8],
        "success":        bool(r[9]),
        "error_msg":      r[10],
        "duration_ms":    r[11],
        "created_at":     r[12],
    }


# ============================================================
# PolicyViolationRepo — one row per guardrail trip. Append-only.
# ============================================================


class PolicyViolationRepo:
    """Per-violation record. Written from `observability/policy.py`
    when a detector trips (cost_spike, output_too_long, prompt_injection,
    unexpected_tool, etc.).

    The detector functions are no-throw on this insert — a missing
    migration or DB hiccup logs at debug and continues. The OTel span
    event still fires, so policy violations are never silently lost."""

    def record(
        self,
        session: Session,
        *,
        agent_name: str,
        violation_type: str,
        severity: str,
        pipeline_id: str | None = None,
        llm_call_id: str | None = None,
        details: dict[str, Any] | None = None,
    ) -> str:
        """Insert and return violation_id (UUID as string)."""
        row = session.execute(
            text("""
                INSERT INTO agent_policy_violations
                  (pipeline_id, llm_call_id, agent_name, violation_type,
                   severity, details)
                VALUES
                  (:pid, :llm_call_id, :agent, :vtype, :sev,
                   CAST(:details AS JSONB))
                RETURNING violation_id
            """),
            {
                "pid": pipeline_id,
                "llm_call_id": llm_call_id,
                "agent": agent_name,
                "vtype": violation_type,
                "sev": severity,
                "details": json.dumps(details or {}),
            },
        ).fetchone()
        return str(row[0]) if row else ""

    def list_unresolved(
        self, session: Session, *,
        severity: str | None = None, limit: int = 200,
    ) -> list[dict[str, Any]]:
        """Open violations, newest first. Optional severity filter for
        the critical-only Grafana alert panel."""
        if severity:
            rows = session.execute(
                text("""
                    SELECT violation_id, pipeline_id, llm_call_id, agent_name,
                           violation_type, severity, details, resolved, created_at
                    FROM agent_policy_violations
                    WHERE resolved = FALSE AND severity = :sev
                    ORDER BY created_at DESC
                    LIMIT :lim
                """),
                {"sev": severity, "lim": int(limit)},
            ).fetchall()
        else:
            rows = session.execute(
                text("""
                    SELECT violation_id, pipeline_id, llm_call_id, agent_name,
                           violation_type, severity, details, resolved, created_at
                    FROM agent_policy_violations
                    WHERE resolved = FALSE
                    ORDER BY created_at DESC
                    LIMIT :lim
                """),
                {"lim": int(limit)},
            ).fetchall()
        return [_policy_violation_row(r) for r in rows]


def _policy_violation_row(r) -> dict[str, Any]:
    """Shape one agent_policy_violations row into the dict the API +
    Grafana panels consume."""
    return {
        "violation_id":   str(r[0]),
        "pipeline_id":    r[1],
        "llm_call_id":    r[2],
        "agent_name":     r[3],
        "violation_type": r[4],
        "severity":       r[5],
        "details":        r[6] or {},
        "resolved":       bool(r[7]),
        "created_at":     r[8],
    }


# ============================================================
# SystemHealthRepo — one row per heartbeat tick per service.
# Append-only with a 7-day retention sweep on each tick.
# ============================================================


class SystemHealthRepo:
    """Per-tick heartbeat of an external dependency (postgres,
    anthropic, grafana_cloud, serper, ...). Written from the lifespan
    heartbeat loop in `api/main.py`.

    Read by Grafana panels for the uptime % and latency-trend tiles."""

    def record(
        self,
        session: Session,
        *,
        service_name: str,
        status: str,
        latency_ms: int | None = None,
        error_msg: str | None = None,
    ) -> None:
        session.execute(
            text("""
                INSERT INTO system_health
                  (service_name, status, latency_ms, error_msg)
                VALUES
                  (:svc, :status, :lat, :err)
            """),
            {
                "svc": service_name,
                "status": status,
                "lat": latency_ms,
                "err": (error_msg or None) and str(error_msg)[:500],
            },
        )

    def prune(self, session: Session, *, keep_days: int = 7) -> int:
        """Delete rows older than `keep_days`. Returns rows removed.
        Called once per heartbeat tick to keep the table bounded
        without a separate scheduler/cron."""
        result = session.execute(
            text("""
                DELETE FROM system_health
                WHERE checked_at < NOW() - (:d || ' days')::interval
            """),
            {"d": str(int(keep_days))},
        )
        return result.rowcount or 0

    def latest_per_service(
        self, session: Session,
    ) -> list[dict[str, Any]]:
        """Single most-recent row per service. Used by the Grafana
        uptime-stat panel and by `/api/health/services` if/when
        we expose it for the UI."""
        rows = session.execute(
            text("""
                SELECT DISTINCT ON (service_name)
                       service_name, status, latency_ms, error_msg, checked_at
                FROM system_health
                ORDER BY service_name, checked_at DESC
            """),
        ).fetchall()
        return [
            {
                "service_name": r[0],
                "status":       r[1],
                "latency_ms":   r[2],
                "error_msg":    r[3],
                "checked_at":   r[4],
            }
            for r in rows
        ]


__all__: Iterable[str] = [
    "AgentToolCallRepo",
    "AuditRepo", "DemoSessionRepo", "KGRepo", "LlmCallRepo", "LlmEvalRepo",
    "PipelineRepo", "PlanRepo", "PolicyViolationRepo",
    "ProfileAuditRepo", "ProfileRepo", "SystemHealthRepo",
]
