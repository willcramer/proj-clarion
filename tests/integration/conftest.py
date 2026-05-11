"""Integration-test fixtures: ephemeral Postgres via testcontainers.

A single container per test session — fast tests roll back transactions,
slow tests use clean schemas.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, create_engine, text

# testcontainers needs Docker reachable; skip-collect cleanly if it isn't
try:
    from testcontainers.postgres import PostgresContainer
except ImportError:  # pragma: no cover
    PostgresContainer = None  # type: ignore[assignment,misc]


@pytest.fixture(scope="session")
def pg_container() -> Iterator[object]:
    """Spin up Postgres 16 once per session."""
    if PostgresContainer is None:
        pytest.skip("testcontainers not installed")
    with PostgresContainer("postgres:16-alpine") as pg:
        yield pg


@pytest.fixture(scope="session")
def engine_for_session(pg_container: object) -> Engine:
    """SQLAlchemy Engine pointed at the testcontainer."""
    url = pg_container.get_connection_url()  # type: ignore[attr-defined]
    # testcontainers returns psycopg2 URL by default; convert to psycopg (v3)
    url = url.replace("postgresql+psycopg2://", "postgresql+psycopg://")
    return create_engine(url, future=True)


@pytest.fixture()
def engine(engine_for_session: Engine, monkeypatch: pytest.MonkeyPatch) -> Iterator[Engine]:
    """Per-test engine that points proj_clarion.storage at the container.

    Patches the package's connect() and session factory directly so callers
    inside the package transparently use the test container. Wipes Clarion
    tables after each test for isolation.
    """
    from sqlalchemy.orm import sessionmaker

    from proj_clarion.storage import db as _db

    monkeypatch.setattr(_db, "connect", lambda: engine_for_session)
    test_factory = sessionmaker(bind=engine_for_session, expire_on_commit=False, future=True)
    monkeypatch.setattr(_db, "_session_factory", lambda: test_factory)

    yield engine_for_session

    with engine_for_session.begin() as conn:
        for t in (
            "plan_audit_log", "business_events", "kg_edges", "kg_nodes",
            "demo_plans", "company_profiles", "_migrations",
        ):
            conn.execute(text(f"DROP TABLE IF EXISTS {t} CASCADE"))
        conn.execute(text("DROP FUNCTION IF EXISTS touch_updated_at() CASCADE"))
