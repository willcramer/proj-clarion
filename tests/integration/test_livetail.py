"""Live-tail integration test.

Builds a tiny plan, inserts events directly into business_events, then
exercises LiveTailer._poll_once with a captured emitter to verify:

  - all rows for the plan come out in order
  - the cursor advances to the max event_id
  - a second poll with no new rows is a no-op
  - resuming from a non-zero cursor only emits post-cursor rows
  - cross-plan isolation: events for other plans don't bleed in

We don't actually exercise the OTLP exporter here — that path is generic
SDK code, and a real Alloy/Cloud round-trip is documented as a manual
smoke step. What's specific to Clarion is the SQL+cursor logic, which is
what these tests cover.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import Engine, text

from proj_clarion.livetail.cursor import Cursor
from proj_clarion.livetail.emitter import EventRow
from proj_clarion.livetail.runner import LiveTailer
from proj_clarion.storage import apply_migrations

pytestmark = pytest.mark.integration


class _CapturingEmitter:
    """Stand-in for LiveTailLogEmitter — collects every batch into a list."""

    def __init__(self) -> None:
        self.batches: list[list[EventRow]] = []
        self.started = False
        self.shut = False

    def start(self) -> None:
        self.started = True

    def emit_batch(self, rows: list[EventRow]) -> None:
        self.batches.append(list(rows))

    def shutdown(self) -> None:
        self.shut = True


def _insert_plan(engine: Engine, plan_id: str, *, profile_id: str = "prof-test") -> None:
    """Minimal company_profiles + demo_plans rows so the FK on business_events holds."""
    with engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO company_profiles (profile_id, source_url, profile_json) "
                "VALUES (:pid, :url, :payload) "
                "ON CONFLICT (profile_id) DO NOTHING"
            ),
            {"pid": profile_id, "url": "https://example.test", "payload": "{}"},
        )
        conn.execute(
            text(
                "INSERT INTO demo_plans (plan_id, source_profile_id, plan_json, review_state) "
                "VALUES (:pid, :sid, :payload, 'draft')"
            ),
            {"pid": plan_id, "sid": profile_id, "payload": "{}"},
        )


def _insert_events(
    engine: Engine, plan_id: str, n: int, *, base_ts: datetime,
) -> list[int]:
    """Insert n events; return the event_ids in insertion order."""
    inserted: list[int] = []
    with engine.begin() as conn:
        for i in range(n):
            row = conn.execute(
                text(
                    "INSERT INTO business_events "
                    "(plan_id, ts, event_type, business_entity_ids, payload, trace_id) "
                    "VALUES (:pid, :ts, :etype, :beids, :payload, :tid) "
                    "RETURNING event_id"
                ),
                {
                    "pid":     plan_id,
                    "ts":      base_ts + timedelta(seconds=i),
                    "etype":   f"test.evt_{i}",
                    "beids":   ["bid-" + str(i)],
                    "payload": '{"i": ' + str(i) + "}",
                    "tid":     None,
                },
            ).fetchone()
            inserted.append(int(row[0]))
    return inserted


@pytest.fixture()
def fresh_db(engine: Engine) -> Engine:
    apply_migrations(engine)
    return engine


def test_livetail_emits_each_row_in_order_and_advances_cursor(
    fresh_db: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan_id = str(uuid4())
    _insert_plan(fresh_db, plan_id)
    base = datetime(2026, 1, 1, tzinfo=UTC)
    ids = _insert_events(fresh_db, plan_id, n=5, base_ts=base)

    # Cursor lives under tmp_path so the test doesn't pollute data/livetail
    monkeypatch.setenv("CLARION_DATA_DIR", str(tmp_path))

    tailer = LiveTailer(plan_id, batch_size=10)
    captured = _CapturingEmitter()
    tailer._emitter = captured  # noqa: SLF001 — test injection

    with fresh_db.connect() as conn:
        n = tailer._poll_once(conn)  # noqa: SLF001

    assert n == 5
    assert len(captured.batches) == 1
    out = captured.batches[0]
    assert [r.event_id for r in out] == ids
    assert all(r.plan_id == plan_id for r in out)
    assert tailer.stats.last_event_id == ids[-1]

    # Cursor on disk should be at the max event_id now
    persisted = Cursor(plan_id, root=tmp_path / "livetail")
    assert persisted.value == ids[-1]

    # Second poll with no new rows: no batch, cursor unchanged.
    captured.batches.clear()
    with fresh_db.connect() as conn:
        n2 = tailer._poll_once(conn)  # noqa: SLF001
    assert n2 == 0
    assert captured.batches == []


def test_livetail_resumes_from_persisted_cursor(
    fresh_db: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan_id = str(uuid4())
    _insert_plan(fresh_db, plan_id)
    base = datetime(2026, 1, 1, tzinfo=UTC)
    first = _insert_events(fresh_db, plan_id, n=3, base_ts=base)

    monkeypatch.setenv("CLARION_DATA_DIR", str(tmp_path))
    # Pre-seed the cursor as if a previous run had already shipped first[1]
    Cursor(plan_id, root=tmp_path / "livetail").advance_to(first[1])

    # Insert two more events after the simulated cursor
    second = _insert_events(
        fresh_db, plan_id, n=2, base_ts=base + timedelta(minutes=1),
    )

    tailer = LiveTailer(plan_id, batch_size=10)
    captured = _CapturingEmitter()
    tailer._emitter = captured  # noqa: SLF001

    with fresh_db.connect() as conn:
        n = tailer._poll_once(conn)  # noqa: SLF001

    expected_ids = [first[2], *second]  # everything strictly after cursor
    assert n == len(expected_ids)
    assert [r.event_id for r in captured.batches[0]] == expected_ids


def test_livetail_isolates_per_plan(
    fresh_db: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    plan_a = str(uuid4())
    plan_b = str(uuid4())
    _insert_plan(fresh_db, plan_a, profile_id="prof-test-a")
    _insert_plan(fresh_db, plan_b, profile_id="prof-test-b")

    base = datetime(2026, 1, 1, tzinfo=UTC)
    a_ids = _insert_events(fresh_db, plan_a, n=4, base_ts=base)
    _insert_events(fresh_db, plan_b, n=10, base_ts=base)

    monkeypatch.setenv("CLARION_DATA_DIR", str(tmp_path))

    tailer = LiveTailer(plan_a, batch_size=100)
    captured = _CapturingEmitter()
    tailer._emitter = captured  # noqa: SLF001
    with fresh_db.connect() as conn:
        n = tailer._poll_once(conn)  # noqa: SLF001

    assert n == 4
    assert [r.event_id for r in captured.batches[0]] == a_ids
    assert all(r.plan_id == plan_a for r in captured.batches[0])
