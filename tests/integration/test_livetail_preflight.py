"""Integration test for the pre-flight ingest estimate.

The unit tests cover the rate math and tier-comparison logic. This file
exercises the SQL probe against a real Postgres so we know
`pg_column_size(payload)` sampling and the COUNT(*) backlog query both
work against the v0.2 schema.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import Engine, text

from proj_clarion.livetail.preflight import estimate_livetail_rate
from proj_clarion.storage import apply_migrations

pytestmark = pytest.mark.integration


def _seed_plan_with_events(
    engine: Engine,
    plan_id: str,
    *,
    n_events: int,
    payload_template: dict,
) -> None:
    """Insert a plan, profile, and N business_events with a known payload size.

    Returns nothing — caller queries via plan_id.
    """
    payload_json = json.dumps(payload_template)
    base = datetime(2026, 1, 1, tzinfo=UTC)
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO company_profiles (profile_id, source_url, profile_json) "
                "VALUES ('prof-test', 'https://example.test', '{}') "
                "ON CONFLICT (profile_id) DO NOTHING"
            ),
        )
        conn.execute(
            text(
                "INSERT INTO demo_plans (plan_id, source_profile_id, plan_json, review_state) "
                "VALUES (:pid, 'prof-test', '{}', 'draft')"
            ),
            {"pid": plan_id},
        )
        for i in range(n_events):
            conn.execute(
                text(
                    "INSERT INTO business_events "
                    "(plan_id, ts, event_type, business_entity_ids, payload, trace_id) "
                    "VALUES (:pid, :ts, 'test.evt', ARRAY['x'], CAST(:payload AS JSONB), NULL)"
                ),
                {
                    "pid":     plan_id,
                    "ts":      base + timedelta(seconds=i),
                    "payload": payload_json,
                },
            )


@pytest.fixture()
def fresh_db(engine: Engine) -> Engine:
    apply_migrations(engine)
    return engine


def test_preflight_returns_zero_estimate_when_no_events(
    fresh_db: Engine, monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan_id = str(uuid4())
    _seed_plan_with_events(fresh_db, plan_id, n_events=0,
                           payload_template={"k": "v"})

    monkeypatch.delenv("CLARION_LOKI_BYTES_PER_SEC", raising=False)
    est = estimate_livetail_rate(
        plan_id, batch_size=500, poll_interval_seconds=1.0, engine=fresh_db,
    )
    assert est.backlog_rows == 0
    assert est.sample_rows == 0
    assert est.avg_payload_bytes == 0
    # avg_record_bytes = 0 + envelope overhead (600); rate is still computed
    # because batch + interval are valid
    assert est.rows_per_sec == 500.0
    assert est.drain_seconds is None  # nothing to drain


def test_preflight_samples_payload_size_and_estimates_rate(
    fresh_db: Engine, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 500-byte payload × 200 events should give a stable per-record
    average around 500 bytes payload + the OTLP envelope overhead."""
    plan_id = str(uuid4())
    # ~500 bytes of JSON
    big_payload = {"key" + str(i): "x" * 50 for i in range(8)}
    _seed_plan_with_events(fresh_db, plan_id, n_events=200,
                           payload_template=big_payload)

    monkeypatch.delenv("CLARION_LOKI_BYTES_PER_SEC", raising=False)
    est = estimate_livetail_rate(
        plan_id, batch_size=500, poll_interval_seconds=1.0, engine=fresh_db,
    )
    assert est.backlog_rows == 200
    assert est.sample_rows == 100  # default sample size
    # JSONB column size for our payload — should be in the right order of
    # magnitude. Don't assert exact bytes since pg_column_size includes
    # TOAST overhead; just bracket reasonably.
    assert 200 < est.avg_payload_bytes < 2000
    assert est.avg_record_bytes == est.avg_payload_bytes + 600
    # 500 rows/s × ~1KB each ≈ 500KB/s
    assert est.rows_per_sec == 500.0
    assert est.bytes_per_sec >= est.avg_record_bytes * 100  # sanity
    assert est.drain_seconds is not None


def test_preflight_reads_tier_limit_from_env(
    fresh_db: Engine, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """CLARION_LOKI_BYTES_PER_SEC drives will_exceed_tier_limit + suggested_batch."""
    plan_id = str(uuid4())
    _seed_plan_with_events(fresh_db, plan_id, n_events=50,
                           payload_template={"k": "v"})

    # 1KB/s tier — way below any reasonable estimate
    monkeypatch.setenv("CLARION_LOKI_BYTES_PER_SEC", "1024")
    est = estimate_livetail_rate(
        plan_id, batch_size=500, poll_interval_seconds=1.0, engine=fresh_db,
    )
    assert est.tier_limit_bytes_per_sec == 1024
    assert est.will_exceed_tier_limit is True
    assert est.suggested_batch is not None
    assert est.suggested_batch < 500  # tighter than what we requested


def test_preflight_respects_cursor_value(
    fresh_db: Engine, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Backlog count is rows ABOVE the cursor, not the table total."""
    plan_id = str(uuid4())
    _seed_plan_with_events(fresh_db, plan_id, n_events=100,
                           payload_template={"k": "v"})

    monkeypatch.delenv("CLARION_LOKI_BYTES_PER_SEC", raising=False)

    # Find the median event_id and use that as cursor
    with fresh_db.connect() as conn:
        row = conn.execute(
            text(
                "SELECT event_id FROM business_events "
                "WHERE plan_id = :pid ORDER BY event_id LIMIT 1 OFFSET 50"
            ),
            {"pid": plan_id},
        ).fetchone()
        median_id = int(row[0])

    est = estimate_livetail_rate(
        plan_id, batch_size=10, poll_interval_seconds=1.0,
        cursor_value=median_id, engine=fresh_db,
    )
    # 100 total - 51 at-or-below cursor = 49 above → backlog 49 (give or take 1)
    assert 45 <= est.backlog_rows <= 55
