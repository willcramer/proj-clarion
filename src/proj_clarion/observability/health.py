"""External-dependency health heartbeat.

Runs as an asyncio task off the FastAPI lifespan — see `api/main.py`.
Every `HEARTBEAT_INTERVAL_S` (default 60s) it probes the dependencies
Clarion needs (Postgres, Anthropic, Grafana Cloud, optional Serper)
and writes one row per service to `system_health`. Latency over
`DEGRADED_THRESHOLD_MS` (5000ms) flips the row's status to
`degraded`; a raised exception flips it to `down`.

Why this lives separate from a `/health` endpoint: a poll-from-LB
healthcheck answers "are *you* up?". This module answers "are *your
dependencies* up?" — the November outage was Anthropic flaking while
the API surface itself stayed responsive. Grafana panels read this
table for the "Service uptime %" tile.

The loop also prunes rows older than 7 days every tick. One trip to
postgres per tick beats running a separate cron job.

Public surface:
- `heartbeat_loop()` — async coroutine the lifespan awaits forever.
- `check_once()` — one immediate pass, useful for startup sanity logs.
- `HEARTBEAT_INTERVAL_S` — the tick cadence, override via
  `CLARION_HEARTBEAT_INTERVAL_S`.
"""

from __future__ import annotations

import asyncio
import os
import time
from typing import Any, Callable

import httpx
import structlog

_logger = structlog.get_logger()


# Tick cadence + degradation threshold. Both tunable via env so a
# stress test or a slow region can adjust without a code change.
HEARTBEAT_INTERVAL_S: float = float(
    os.getenv("CLARION_HEARTBEAT_INTERVAL_S", "60"),
)
DEGRADED_THRESHOLD_MS: int = int(
    os.getenv("CLARION_HEALTH_DEGRADED_MS", "5000"),
)
HEALTH_RETENTION_DAYS: int = int(
    os.getenv("CLARION_HEALTH_RETENTION_DAYS", "7"),
)
# Per-probe timeout: don't let one slow service freeze the rest.
_PROBE_TIMEOUT_S: float = 5.0


# ─── Probes ─────────────────────────────────────────────────────────


def _probe_postgres() -> int:
    """Returns latency_ms. Raises on failure."""
    from proj_clarion.storage import session_scope
    from sqlalchemy import text
    start = time.monotonic()
    with session_scope() as s:
        s.execute(text("SELECT 1"))
    return int((time.monotonic() - start) * 1000)


def _probe_anthropic() -> int:
    """Hit Anthropic's public status JSON. We deliberately don't ping
    the actual API — that costs tokens and would spam our usage. The
    statuspage is the canonical 'is the service up?' signal."""
    start = time.monotonic()
    resp = httpx.get(
        "https://status.anthropic.com/api/v2/status.json",
        timeout=_PROBE_TIMEOUT_S,
        follow_redirects=True,
    )
    resp.raise_for_status()
    return int((time.monotonic() - start) * 1000)


def _probe_grafana_cloud() -> int:
    """Same idea — hit the public statuspage rather than the tenant API."""
    start = time.monotonic()
    resp = httpx.get(
        "https://status.grafana.com/api/v2/status.json",
        timeout=_PROBE_TIMEOUT_S,
        follow_redirects=True,
    )
    resp.raise_for_status()
    return int((time.monotonic() - start) * 1000)


def _probe_serper() -> int:
    """Only run when SERPER_API_KEY is present; pings the landing page
    which is unauthenticated."""
    if not os.getenv("SERPER_API_KEY"):
        raise RuntimeError("not_configured")
    start = time.monotonic()
    resp = httpx.get(
        "https://google.serper.dev/",
        timeout=_PROBE_TIMEOUT_S,
    )
    # 200 or 401 both mean "service up" — Serper returns 401 on its
    # marketing page for unauth'd hits but the gateway is alive.
    if resp.status_code not in (200, 401, 403):
        resp.raise_for_status()
    return int((time.monotonic() - start) * 1000)


_PROBES: dict[str, Callable[[], int]] = {
    "postgres":      _probe_postgres,
    "anthropic":     _probe_anthropic,
    "grafana_cloud": _probe_grafana_cloud,
    "serper":        _probe_serper,
}


# ─── Tick + loop ────────────────────────────────────────────────────


def check_once(*, prune: bool = True) -> dict[str, dict[str, Any]]:
    """Run all probes once, write results, optionally prune.

    Returns a snapshot dict keyed by service_name with status/latency
    fields. Logged at INFO so the structured-log surface tells the
    same story as the postgres rows."""
    out: dict[str, dict[str, Any]] = {}
    for service_name, probe in _PROBES.items():
        status = "healthy"
        latency_ms: int | None = None
        error_msg: str | None = None
        try:
            latency_ms = probe()
            if latency_ms > DEGRADED_THRESHOLD_MS:
                status = "degraded"
        except Exception as exc:  # noqa: BLE001
            status = "down"
            # Skip "not configured" probes silently — they're optional.
            msg = str(exc)
            if msg == "not_configured":
                continue
            error_msg = msg[:500]

        out[service_name] = {
            "status": status,
            "latency_ms": latency_ms,
            "error_msg": error_msg,
        }

        # Persist. Best-effort: if Postgres itself is down the insert
        # also fails — the structured log is the durable witness.
        try:
            from proj_clarion.storage import SystemHealthRepo, session_scope
            with session_scope() as s:
                SystemHealthRepo().record(
                    s,
                    service_name=service_name,
                    status=status,
                    latency_ms=latency_ms,
                    error_msg=error_msg,
                )
        except Exception as exc:  # noqa: BLE001
            _logger.debug(
                "health.persist.skip",
                service=service_name, error=str(exc)[:200],
            )

    if prune:
        try:
            from proj_clarion.storage import SystemHealthRepo, session_scope
            with session_scope() as s:
                removed = SystemHealthRepo().prune(s, keep_days=HEALTH_RETENTION_DAYS)
            if removed:
                _logger.info("health.pruned", rows=removed)
        except Exception as exc:  # noqa: BLE001
            _logger.debug("health.prune.skip", error=str(exc)[:200])

    _logger.info(
        "health.tick",
        services={k: v["status"] for k, v in out.items()},
    )
    return out


async def heartbeat_loop() -> None:
    """Forever-loop the FastAPI lifespan awaits. Each iteration sleeps
    `HEARTBEAT_INTERVAL_S` then probes. The probes themselves are
    synchronous (`httpx.get` not `httpx.AsyncClient`) — we run them in
    a thread so the event loop stays unblocked for SSE and the
    pipeline orchestrator.

    Cancellation is honoured: an outer task.cancel() during shutdown
    propagates through asyncio.sleep cleanly."""
    _logger.info(
        "health.loop.start",
        interval_s=HEARTBEAT_INTERVAL_S,
        degraded_threshold_ms=DEGRADED_THRESHOLD_MS,
        retention_days=HEALTH_RETENTION_DAYS,
    )
    try:
        while True:
            try:
                await asyncio.to_thread(check_once)
            except Exception as exc:  # noqa: BLE001
                # Defensive: a bug inside check_once shouldn't kill
                # the loop. Log + sleep + retry.
                _logger.warning("health.tick.error", error=str(exc))
            await asyncio.sleep(HEARTBEAT_INTERVAL_S)
    except asyncio.CancelledError:
        _logger.info("health.loop.stop")
        raise
