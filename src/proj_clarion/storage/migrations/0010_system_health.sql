-- ============================================================
-- 0010 — system_health
--
-- Synthetic uptime/health heartbeat for the systems Clarion depends
-- on: Postgres itself, Anthropic, Grafana Cloud, optional Serper.
-- One row per check per service per cycle (every ~60s). Status flips
-- to 'degraded' on slow responses (latency > 5000ms) and 'down' on
-- exception.
--
-- Why it exists: the November outage had no automated detection.
-- The customer's governance ask was "show me how you'd have known
-- this was happening". This table + the matching Grafana alert
-- close that loop.
--
-- Append-only with a cleanup expectation of 7 days (handled by a
-- DELETE in the heartbeat loop's tick rather than a separate job —
-- one trip to postgres per cycle keeps the operational surface
-- small).
-- ============================================================

CREATE TABLE IF NOT EXISTS system_health (
    id            BIGSERIAL PRIMARY KEY,
    service_name  TEXT NOT NULL,
    -- 'healthy','degraded','down'. Kept loose (no CHECK) so new
    -- states can ship without a migration.
    status        TEXT NOT NULL,
    latency_ms    INTEGER,
    error_msg     TEXT,
    checked_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_system_health_service
    ON system_health (service_name, checked_at DESC);

CREATE INDEX IF NOT EXISTS idx_system_health_checked_at
    ON system_health (checked_at DESC);
