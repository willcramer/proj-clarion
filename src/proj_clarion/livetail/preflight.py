"""Pre-flight ingest estimate for the live-tailer.

The v0.5 smoke test pushed 31,593 events through Alloy in 90 seconds and
hit Loki's 873,813 bytes/sec tier limit, blackholing every log batch and
spending the rest of the run in retry-with-backoff. This module estimates
the bytes/sec rate a live-tail run will produce *before* it starts, so the
SE can either tune `--batch` / `--interval` or accept the risk knowingly.

How the estimate works:

  rows_per_sec      = batch_size / poll_interval_seconds
  avg_record_bytes  = avg_payload_bytes + OTLP_ENVELOPE_OVERHEAD
  bytes_per_sec     = rows_per_sec * avg_record_bytes
  drain_seconds     = backlog_rows / rows_per_sec

Sampling: pg_column_size(payload) over 100 rows from the plan's
business_events. Cheap, well-indexed, gives a representative average
without scanning the full table.

Tier limit: read from `CLARION_LOKI_BYTES_PER_SEC` env var. If unset, the
report is informational only — no warning, no exit code change.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from sqlalchemy import Engine, text

from proj_clarion.storage.db import connect as _connect

# OTLP/HTTP log record envelope (resource attrs + attributes dict + body
# wrapper + protobuf framing) is roughly fixed-size per record. 600 bytes
# is conservative — measured empirically on Cloud OTLP gateway round-trips
# during the v0.5 smoke (32MB total / 56k records ≈ 570 bytes/record).
_OTLP_ENVELOPE_OVERHEAD_BYTES = 600

# Minimum row count to sample; lower than this and the average isn't stable
_MIN_SAMPLE_ROWS = 10


@dataclass(frozen=True)
class IngestEstimate:
    """One pre-flight estimate's result."""
    backlog_rows: int
    avg_payload_bytes: int       # average pg_column_size(payload) over the sample
    avg_record_bytes: int        # +overhead, what Alloy will send per record
    batch_size: int              # echo of input — needed for suggested_batch math
    poll_interval_seconds: float
    rows_per_sec: float
    bytes_per_sec: int
    drain_seconds: float | None  # None when rows_per_sec is 0 or no backlog
    tier_limit_bytes_per_sec: int | None  # from CLARION_LOKI_BYTES_PER_SEC, or None
    sample_rows: int             # how many rows we actually sampled

    @property
    def will_exceed_tier_limit(self) -> bool:
        return (
            self.tier_limit_bytes_per_sec is not None
            and self.bytes_per_sec > self.tier_limit_bytes_per_sec
        )

    @property
    def suggested_batch(self) -> int | None:
        """If a tier limit is set and the estimate would exceed, return a
        batch size that keeps us at 80% of the limit, with the same interval."""
        if self.tier_limit_bytes_per_sec is None or not self.will_exceed_tier_limit:
            return None
        if self.avg_record_bytes <= 0:
            return None
        target_bps = self.tier_limit_bytes_per_sec * 0.8
        target_rps = target_bps / self.avg_record_bytes
        return max(1, int(target_rps * self.poll_interval_seconds))


def _tier_limit_from_env() -> int | None:
    raw = os.environ.get("CLARION_LOKI_BYTES_PER_SEC", "").strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def _sample_payload_size(
    engine: Engine, plan_id: str, *, sample_size: int = 100,
) -> tuple[int, int]:
    """Run pg_column_size sampling against business_events. Returns
    (avg_payload_bytes, sample_rows). Returns (0, 0) when no rows match.
    """
    with engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT COUNT(*) FROM business_events WHERE plan_id = :pid"
            ),
            {"pid": plan_id},
        ).scalar()
        backlog = int(row or 0)
        if backlog == 0:
            return 0, 0

        # Sample from the head of the table — order doesn't matter for an
        # average, and this keeps the query cheap on the existing
        # (plan_id, ts DESC) index.
        result = conn.execute(
            text(
                "SELECT AVG(pg_column_size(payload))::bigint AS avg_bytes, "
                "COUNT(*) AS sampled "
                "FROM ( "
                "  SELECT payload FROM business_events "
                "  WHERE plan_id = :pid LIMIT :n "
                ") s"
            ),
            {"pid": plan_id, "n": sample_size},
        ).fetchone()
        avg_bytes = int(result[0] or 0)
        sampled = int(result[1] or 0)
    return avg_bytes, sampled


def estimate_livetail_rate(
    plan_id: str,
    *,
    batch_size: int,
    poll_interval_seconds: float,
    cursor_value: int = 0,
    engine: Engine | None = None,
) -> IngestEstimate:
    """Compute the pre-flight ingest estimate for a planned live-tail run.

    Caller is responsible for handling the result — printing, warning,
    aborting if the SE confirms.
    """
    eng = engine or _connect()

    # Backlog is rows the live-tailer would emit on a fresh start.
    with eng.connect() as conn:
        backlog_row = conn.execute(
            text(
                "SELECT COUNT(*) FROM business_events "
                "WHERE plan_id = :pid AND event_id > :cursor"
            ),
            {"pid": plan_id, "cursor": cursor_value},
        ).scalar()
    backlog = int(backlog_row or 0)

    avg_payload, sampled = _sample_payload_size(eng, plan_id)

    # Even an empty plan should produce a reasonable record-bytes default
    avg_record = (avg_payload or 0) + _OTLP_ENVELOPE_OVERHEAD_BYTES

    interval = max(0.1, poll_interval_seconds)
    rows_per_sec = batch_size / interval
    bytes_per_sec = int(rows_per_sec * avg_record)

    drain_seconds = (backlog / rows_per_sec) if rows_per_sec > 0 and backlog > 0 else None

    tier_limit = _tier_limit_from_env()

    return IngestEstimate(
        backlog_rows=backlog,
        avg_payload_bytes=avg_payload,
        avg_record_bytes=avg_record,
        batch_size=batch_size,
        poll_interval_seconds=interval,
        rows_per_sec=rows_per_sec,
        bytes_per_sec=bytes_per_sec,
        drain_seconds=drain_seconds,
        tier_limit_bytes_per_sec=tier_limit,
        sample_rows=sampled,
    )


def format_estimate(est: IngestEstimate) -> str:
    """Plain-text rendering for CLI output. Caller wraps in Rich panel."""

    def _fmt_bytes(n: int) -> str:
        for u in ("B", "KB", "MB", "GB"):
            if abs(n) < 1024:
                return f"{n:.1f} {u}" if u != "B" else f"{int(n)} {u}"
            n = n / 1024
        return f"{n:.1f} TB"

    def _fmt_secs(s: float) -> str:
        if s < 60:
            return f"{s:.0f}s"
        if s < 3600:
            return f"{s/60:.1f}m"
        return f"{s/3600:.1f}h"

    lines = [
        f"backlog rows         {est.backlog_rows:,}",
        f"avg payload bytes    {est.avg_payload_bytes:,}  (sampled {est.sample_rows} rows)",
        f"avg OTLP record      {est.avg_record_bytes:,} bytes  "
        f"(payload + ~{_OTLP_ENVELOPE_OVERHEAD_BYTES} envelope)",
        f"rate                 {est.rows_per_sec:.0f} rows/s  →  "
        f"{_fmt_bytes(est.bytes_per_sec)}/s",
    ]
    if est.drain_seconds is not None:
        lines.append(f"backlog drain        ~{_fmt_secs(est.drain_seconds)}")

    if est.tier_limit_bytes_per_sec is not None:
        margin_pct = 100 * est.bytes_per_sec / max(est.tier_limit_bytes_per_sec, 1)
        lines.append(
            f"tier limit           {_fmt_bytes(est.tier_limit_bytes_per_sec)}/s  "
            f"(estimate uses {margin_pct:.0f}%)"
        )
        if est.will_exceed_tier_limit:
            lines.append("")
            lines.append(
                f"[!] estimated bytes/sec EXCEEDS tier limit. Loki will "
                f"reject batches with HTTP 429."
            )
            sugg = est.suggested_batch
            if sugg is not None and sugg > 0:
                # Symmetric option: keep batch, raise interval to the same target.
                target_bps = est.tier_limit_bytes_per_sec * 0.8 if est.tier_limit_bytes_per_sec else 0
                target_rps = (target_bps / est.avg_record_bytes) if est.avg_record_bytes else 1
                sugg_interval = max(
                    est.poll_interval_seconds,
                    (est.batch_size / target_rps) if target_rps else est.poll_interval_seconds,
                )
                lines.append(
                    f"    suggested:  --batch {sugg}  "
                    f"(or --interval {sugg_interval:.1f}, or both)"
                )

    return "\n".join(lines)
