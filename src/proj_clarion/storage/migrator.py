"""Tiny migration runner.

Reads `migrations/NNNN_name.sql` files in lexical order, applies each one
inside its own transaction, and records the filename in the `_migrations`
table so re-runs are no-ops. We deliberately do not use Alembic in v0.2 —
revisit when schema deltas get hairy.
"""

from __future__ import annotations

from importlib.resources import files
from pathlib import Path

import structlog
from sqlalchemy import Engine, text

from proj_clarion.storage.db import connect

_logger = structlog.get_logger()

_BOOTSTRAP_DDL = """
CREATE TABLE IF NOT EXISTS _migrations (
    filename    TEXT PRIMARY KEY,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
)
"""


def _migration_files() -> list[Path]:
    """All migration files, sorted lexically. Lives in this package's `migrations/`."""
    pkg = files("proj_clarion.storage.migrations")
    out: list[Path] = []
    for entry in pkg.iterdir():  # type: ignore[attr-defined]
        if entry.name.endswith(".sql"):
            out.append(Path(str(entry)))
    out.sort(key=lambda p: p.name)
    return out


def apply_migrations(engine: Engine | None = None) -> list[str]:
    """Apply every migration file not yet recorded in _migrations.

    Returns the list of filenames newly applied this call.
    """
    eng = engine or connect()
    applied_now: list[str] = []

    with eng.begin() as conn:
        conn.execute(text(_BOOTSTRAP_DDL))

    with eng.connect() as conn:
        already = {row[0] for row in conn.execute(text("SELECT filename FROM _migrations"))}

    for path in _migration_files():
        if path.name in already:
            _logger.debug("migrate.skip", file=path.name, reason="already applied")
            continue
        sql = path.read_text()
        with eng.begin() as conn:
            conn.execute(text(sql))
            conn.execute(
                text("INSERT INTO _migrations (filename) VALUES (:f)"),
                {"f": path.name},
            )
        applied_now.append(path.name)
        _logger.info("migrate.apply", file=path.name)

    return applied_now


def drop_all(engine: Engine | None = None) -> None:
    """Drop every Proj Clarion table (and the migrations log). Used by `db reset`.

    Does NOT drop the database itself or any non-Clarion tables. Order matters
    because of FK references; we use CASCADE to make it forgiving.
    """
    eng = engine or connect()
    # Drop in FK-safe order. CASCADE catches anything we miss but keeps
    # explicit dependencies obvious in this list.
    tables = [
        # system_health has no FKs but lives at the top alongside the
        # other observability tables so the family stays grouped.
        "system_health",
        # agent_tool_calls + agent_policy_violations both FK into
        # pipelines and llm_calls, so they must be dropped before either.
        # Listed first so the explicit dependency order is readable.
        "agent_policy_violations",
        "agent_tool_calls",
        "llm_evals",
        "llm_calls",
        # Clarion Assistant chat — turns FK into conversations; child
        # listed first. No FK to plans/profiles even though turns may
        # reference them via context_scope — assistant history is
        # supposed to survive deletes elsewhere in the system.
        "assistant_turns",
        "assistant_conversations",
        # plan_refinement_turns FK into plan_refinement_sessions; child
        # listed first. sessions have no FK to demo_plans (audit
        # semantics — survive a plan delete) so they go at the same
        # rank as plan_audit_log.
        "plan_refinement_turns",
        "plan_refinement_sessions",
        "plan_audit_log",
        "profile_audit_log",
        "business_events",
        "kg_edges",
        "kg_nodes",
        "pipeline_events",
        "pipeline_phases",
        "pipelines",
        "demo_plans",
        "company_profiles",
        "_migrations",
    ]
    with eng.begin() as conn:
        for t in tables:
            conn.execute(text(f"DROP TABLE IF EXISTS {t} CASCADE"))
        conn.execute(text("DROP FUNCTION IF EXISTS touch_updated_at() CASCADE"))
    _logger.info("migrate.drop_all", count=len(tables))
