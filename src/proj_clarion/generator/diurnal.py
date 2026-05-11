"""Diurnal + weekly weighting for synthetic event volume.

Each pattern returns a multiplier in [0, 1] for a given UTC datetime. Daily
multipliers are then composed with weekly multipliers, normalised so that
the per-day total comes out to `business_event_volume_per_day` on average.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

DiurnalPattern = Literal["retail_us", "retail_global", "saas_b2b", "ecommerce_us", "flat"]
WeeklyPattern = Literal["weekend_heavy", "weekday_heavy", "flat"]


# 24 hourly weights per pattern, indexed by UTC hour. Real customers see traffic
# in their local timezone — these approximations bake in a US-east bias for
# *_us patterns. Good enough for "looks plausible during a 15-minute demo".

_HOURLY: dict[str, tuple[float, ...]] = {
    # Retail US: heavy lunch+evening, dead overnight (UTC, so US-east shifted)
    "retail_us": (
        0.4, 0.3, 0.3, 0.3, 0.4, 0.5,   # 00–05 UTC (US night)
        0.6, 0.7, 0.8, 0.9, 1.0, 1.2,   # 06–11 UTC (US morning)
        1.4, 1.6, 1.6, 1.5, 1.4, 1.2,   # 12–17 UTC (US lunch–afternoon)
        1.1, 1.0, 0.9, 0.8, 0.7, 0.5,   # 18–23 UTC (US evening)
    ),
    # Retail global: smoother because traffic comes from multiple timezones
    "retail_global": (
        0.7, 0.6, 0.6, 0.7, 0.8, 0.9,
        1.0, 1.1, 1.2, 1.2, 1.2, 1.1,
        1.1, 1.1, 1.2, 1.2, 1.1, 1.0,
        1.0, 0.9, 0.9, 0.8, 0.8, 0.7,
    ),
    # SaaS B2B: workday-shaped, dead nights and weekends
    "saas_b2b": (
        0.2, 0.2, 0.2, 0.2, 0.3, 0.4,
        0.6, 0.8, 1.0, 1.4, 1.8, 1.8,
        1.6, 1.8, 2.0, 1.8, 1.4, 1.0,
        0.8, 0.6, 0.5, 0.4, 0.3, 0.2,
    ),
    # E-commerce US: similar to retail US but spikier at lunch + evening
    "ecommerce_us": (
        0.3, 0.2, 0.2, 0.2, 0.3, 0.4,
        0.5, 0.7, 0.9, 1.0, 1.2, 1.5,
        1.7, 1.5, 1.3, 1.4, 1.5, 1.6,
        1.8, 1.6, 1.4, 1.2, 0.9, 0.5,
    ),
    "flat": tuple([1.0] * 24),
}

# Weekday multipliers, indexed by Python weekday() (Mon=0 … Sun=6)
_WEEKLY: dict[str, tuple[float, ...]] = {
    "weekend_heavy": (0.85, 0.85, 0.9, 0.9, 1.0, 1.5, 1.4),  # Sat/Sun heavier
    "weekday_heavy": (1.2, 1.2, 1.2, 1.2, 1.1, 0.4, 0.3),    # B2B-shaped
    "flat":          (1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0),
}


def hourly_weight(pattern: str, ts: datetime) -> float:
    """Composite weight for a given timestamp under (diurnal, _) pattern."""
    weights = _HOURLY.get(pattern, _HOURLY["flat"])
    return weights[ts.hour]


def weekly_weight(pattern: str, ts: datetime) -> float:
    weights = _WEEKLY.get(pattern, _WEEKLY["flat"])
    return weights[ts.weekday()]


def composite_weight(diurnal: str, weekly: str, ts: datetime) -> float:
    """Combined weight; multiplied by the per-day baseline volume to get the
    expected event count for the bucket containing `ts`.
    """
    return hourly_weight(diurnal, ts) * weekly_weight(weekly, ts)


def per_minute_rate(diurnal: str, weekly: str, daily_volume: int, ts: datetime) -> float:
    """Expected events per minute at `ts` given daily total `daily_volume`.

    Rationale: average of `composite_weight` over 24*60 minutes is approximately
    1, so the per-minute rate is daily_volume/1440 scaled by the local weight.
    """
    return (daily_volume / 1440.0) * composite_weight(diurnal, weekly, ts)
