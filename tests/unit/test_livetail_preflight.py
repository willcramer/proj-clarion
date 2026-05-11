"""Unit tests for the live-tail pre-flight ingest estimate.

Cover the math separately from the SQL: the SQL probe is exercised by the
livetail integration test, but the rate calculation, tier-limit comparison,
and tuning-suggestion logic deserve cheap unit coverage.
"""

from __future__ import annotations

import pytest

from proj_clarion.livetail.preflight import (
    IngestEstimate,
    format_estimate,
)


def _est(**overrides) -> IngestEstimate:
    base: dict = {
        "backlog_rows":           1000,
        "avg_payload_bytes":      400,
        "avg_record_bytes":       1000,
        "batch_size":             500,
        "poll_interval_seconds":  1.0,
        "rows_per_sec":           500.0,
        "bytes_per_sec":          500_000,
        "drain_seconds":          2.0,
        "tier_limit_bytes_per_sec": None,
        "sample_rows":            100,
    }
    base.update(overrides)
    return IngestEstimate(**base)


class TestTierLimit:
    def test_no_tier_limit_means_no_warning(self) -> None:
        est = _est(tier_limit_bytes_per_sec=None)
        assert est.will_exceed_tier_limit is False
        assert est.suggested_batch is None

    def test_within_tier_limit_no_warning(self) -> None:
        # 500KB/s estimate, 800KB/s tier → fine
        est = _est(bytes_per_sec=500_000, tier_limit_bytes_per_sec=800_000)
        assert est.will_exceed_tier_limit is False
        assert est.suggested_batch is None

    def test_exceeds_tier_limit_triggers_warning(self) -> None:
        # 5MB/s estimate, 873KB/s tier (the v0.5 smoke scenario) → warn
        est = _est(bytes_per_sec=5_000_000, tier_limit_bytes_per_sec=873_813)
        assert est.will_exceed_tier_limit is True

    def test_suggested_batch_keeps_us_under_80_pct_of_limit(self) -> None:
        """The tuning suggestion should target ~80% of the tier limit so
        we have headroom for natural variance."""
        # 1KB per record, 1s interval, 873KB/s tier → target rps = 873*0.8 = 698
        # So suggested batch ≈ 698
        est = _est(
            avg_record_bytes=1000,
            batch_size=5000,
            poll_interval_seconds=1.0,
            rows_per_sec=5000.0,
            bytes_per_sec=5_000_000,
            tier_limit_bytes_per_sec=873_813,
        )
        sugg = est.suggested_batch
        assert sugg is not None
        # 80% of 873813 / 1000 ≈ 699 rows/s, * 1.0s interval ≈ 699
        assert 600 <= sugg <= 750, f"Expected ~700, got {sugg}"

    def test_suggested_batch_returns_none_when_under_limit(self) -> None:
        est = _est(bytes_per_sec=100_000, tier_limit_bytes_per_sec=800_000)
        assert est.suggested_batch is None


class TestFormatEstimate:
    def test_basic_render_includes_all_essentials(self) -> None:
        est = _est()
        out = format_estimate(est)
        assert "backlog rows" in out
        assert "1,000" in out  # backlog formatted with thousands sep
        assert "rate" in out
        assert "500 rows/s" in out

    def test_render_omits_tier_when_unset(self) -> None:
        est = _est(tier_limit_bytes_per_sec=None)
        out = format_estimate(est)
        assert "tier limit" not in out
        assert "EXCEEDS" not in out

    def test_render_warns_and_suggests_when_over_limit(self) -> None:
        est = _est(
            bytes_per_sec=5_000_000,
            tier_limit_bytes_per_sec=873_813,
            avg_record_bytes=1000,
            batch_size=5000,
            poll_interval_seconds=1.0,
            rows_per_sec=5000.0,
        )
        out = format_estimate(est)
        assert "tier limit" in out
        assert "EXCEEDS" in out
        assert "suggested:" in out
        assert "--batch" in out

    def test_render_drain_omitted_when_no_backlog(self) -> None:
        est = _est(backlog_rows=0, drain_seconds=None)
        out = format_estimate(est)
        assert "backlog drain" not in out


class TestEdgeCases:
    def test_zero_record_bytes_doesnt_crash_suggested(self) -> None:
        """avg_record_bytes can theoretically be 0 if the table is empty
        and we still try to estimate. Don't divide by zero."""
        est = _est(
            avg_record_bytes=0,
            tier_limit_bytes_per_sec=873_813,
            bytes_per_sec=1_000_000,
        )
        # will_exceed says yes but suggested_batch should not crash
        assert est.suggested_batch is None or est.suggested_batch >= 1

    def test_drain_seconds_only_positive_when_backlog_present(self) -> None:
        no_backlog = _est(backlog_rows=0, drain_seconds=None)
        assert no_backlog.drain_seconds is None

        with_backlog = _est(backlog_rows=1000, drain_seconds=2.0)
        assert with_backlog.drain_seconds == 2.0
