"""Live-tail orchestrator: Postgres → OTLP logs.

Loop:
    while not stopped:
        rows = SELECT * FROM business_events
                WHERE plan_id = :plan_id AND event_id > :cursor
                ORDER BY event_id LIMIT :batch
        emit(rows)
        if rows: cursor.advance_to(max(event_id))
        sleep(:interval)

Designed for `proj-clarion live-tail run <plan_id>` — runs in the foreground
until SIGINT/SIGTERM, flushes the OTLP exporter on the way out.
"""

from __future__ import annotations

import re
import signal
import threading
import time
from dataclasses import dataclass
from typing import Any

import structlog
from sqlalchemy import text

from proj_clarion.livetail.cursor import Cursor
from proj_clarion.livetail.emitter import EventRow, LiveTailLogEmitter
from proj_clarion.storage.db import connect as get_engine

_logger = structlog.get_logger()

_SELECT_SQL = text(
    """
    SELECT event_id, plan_id, ts, event_type, business_entity_ids, payload, trace_id
      FROM business_events
     WHERE plan_id = :plan_id
       AND event_id > :cursor
     ORDER BY event_id
     LIMIT :batch
    """
)


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "unknown"


@dataclass
class TailStats:
    rows_emitted: int = 0
    polls: int = 0
    last_event_id: int = 0


class LiveTailer:
    """One instance per plan_id."""

    def __init__(
        self,
        plan_id: str,
        *,
        customer: str | None = None,
        batch_size: int = 500,
        poll_interval_seconds: float = 1.0,
    ) -> None:
        self._plan_id = plan_id
        self._customer = customer or _slug(plan_id)
        self._batch = batch_size
        self._interval = poll_interval_seconds
        self._cursor = Cursor(plan_id)
        self._emitter = LiveTailLogEmitter(plan_id=plan_id, customer=self._customer)
        self._stopping = False
        self.stats = TailStats(last_event_id=self._cursor.value)

    def _poll_once(self, conn: Any) -> int:
        """Fetch + emit one batch. Returns row count."""
        result = conn.execute(
            _SELECT_SQL,
            {
                "plan_id": self._plan_id,
                "cursor":  self._cursor.value,
                "batch":   self._batch,
            },
        )
        rows = result.mappings().all()
        if not rows:
            return 0

        event_rows = [
            EventRow(
                event_id=r["event_id"],
                plan_id=str(r["plan_id"]),
                ts_unix_nanos=int(r["ts"].timestamp() * 1_000_000_000),
                event_type=r["event_type"],
                business_entity_ids=list(r["business_entity_ids"] or []),
                payload=r["payload"] or {},
                trace_id=r["trace_id"],
            )
            for r in rows
        ]
        self._emitter.emit_batch(event_rows)
        max_id = max(r.event_id for r in event_rows)
        self._cursor.advance_to(max_id)
        self.stats.rows_emitted += len(event_rows)
        self.stats.last_event_id = max_id
        return len(event_rows)

    def stop(self) -> None:
        """Signal the run() loop to exit cleanly."""
        self._stopping = True

    def run(self) -> None:
        """Run the poll loop. Returns when stop() is called or SIGINT received.

        Installs SIGINT/SIGTERM handlers only when called from the main
        thread (signal.signal() raises ValueError elsewhere). Background-thread
        callers should drive shutdown via .stop() instead.
        """
        self._emitter.start()

        previous_handlers: list[tuple[int, Any]] = []
        is_main_thread = threading.current_thread() is threading.main_thread()
        if is_main_thread:
            def _handler(_sig: int, _frame: Any) -> None:
                self._stopping = True

            for sig in (signal.SIGINT, signal.SIGTERM):
                previous_handlers.append((sig, signal.signal(sig, _handler)))

        engine = get_engine()
        _logger.info(
            "livetail.start",
            plan_id=self._plan_id,
            customer=self._customer,
            cursor_start=self._cursor.value,
            batch=self._batch,
            poll_interval=self._interval,
        )
        try:
            while not self._stopping:
                with engine.connect() as conn:
                    n = self._poll_once(conn)
                self.stats.polls += 1
                if n == 0:
                    # No work; sleep the full interval. With work, loop right
                    # back so we drain backlogs as fast as we can.
                    time.sleep(self._interval)
        finally:
            if is_main_thread:
                for sig, prev in previous_handlers:
                    signal.signal(sig, prev)
            self._emitter.shutdown()
            _logger.info(
                "livetail.stop",
                plan_id=self._plan_id,
                rows_emitted=self.stats.rows_emitted,
                polls=self.stats.polls,
                last_event_id=self.stats.last_event_id,
            )


def run_livetail(
    plan_id: str,
    *,
    customer: str | None = None,
    batch_size: int = 500,
    poll_interval_seconds: float = 1.0,
) -> TailStats:
    """Convenience wrapper for the CLI."""
    tailer = LiveTailer(
        plan_id,
        customer=customer,
        batch_size=batch_size,
        poll_interval_seconds=poll_interval_seconds,
    )
    tailer.run()
    return tailer.stats
