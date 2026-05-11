-- 0003_demo_sessions.sql
--
-- One row per "demo session" — the live-telemetry window the SE keeps
-- open while presenting to a customer. Separate from `pipelines` because
-- the lifetime is fundamentally different: a pipeline is "run the
-- whole build", a demo session is "keep telemetry flowing until I'm
-- done demoing or 2 hours pass."
--
-- We persist it in Postgres (not just in memory) so:
--   * Restarting the API doesn't lose the running PID — the sweeper can
--     still find and reap expired sessions on cold boot.
--   * The heartbeat (written every emitter cycle) survives an API
--     reload, so the UI's "last push 18s ago" indicator is durable.
--   * Past sessions are auditable — when was this customer last live,
--     how long did it run for.

CREATE TABLE IF NOT EXISTS demo_sessions (
    id              BIGSERIAL    PRIMARY KEY,
    plan_id         UUID         NOT NULL REFERENCES demo_plans(plan_id) ON DELETE CASCADE,
    -- OS process id of the detached emitter. NULL until the spawn returns;
    -- set right after `subprocess.Popen` so the stop endpoint can SIGTERM it.
    pid             INTEGER,
    started_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    -- Hard ceiling — sweeper kills any session past expires_at.
    -- Set on creation as started_at + N hours (default 2h per the v0.7 design).
    -- Manual "Extend" pushes this forward.
    expires_at      TIMESTAMPTZ  NOT NULL,
    -- Updated by the EntityEmitter every export cycle. The UI computes
    -- "live · 18s ago" from `now() - last_heartbeat_at`. NULL until the
    -- first cycle lands (i.e. emitter spun up but hasn't pushed yet).
    last_heartbeat_at TIMESTAMPTZ,
    -- Lifecycle: 'starting' → 'live' → 'stopped' | 'expired' | 'crashed'.
    --   starting  — spawned, no heartbeat seen yet
    --   live      — heartbeat within the last 90s
    --   stopped   — user clicked Stop
    --   expired   — sweeper killed it (past expires_at)
    --   crashed   — heartbeat went stale while still under expires_at
    status          TEXT         NOT NULL DEFAULT 'starting'
                    CHECK (status IN ('starting','live','stopped','expired','crashed')),
    -- Free-form notes: spawn args, last error, etc. Not user-visible
    -- today; kept for debugging an emitter that didn't start.
    notes           JSONB        NOT NULL DEFAULT '{}'::jsonb,
    finished_at     TIMESTAMPTZ
);

-- Only one ACTIVE session per plan at a time. The UI's "Start demo"
-- button refuses if one's already running; this is the DB-level guard
-- so concurrent clicks can't race.
CREATE UNIQUE INDEX IF NOT EXISTS demo_sessions_one_active_per_plan
    ON demo_sessions (plan_id)
    WHERE status IN ('starting','live');

CREATE INDEX IF NOT EXISTS demo_sessions_plan_idx ON demo_sessions(plan_id);
CREATE INDEX IF NOT EXISTS demo_sessions_status_idx ON demo_sessions(status);
CREATE INDEX IF NOT EXISTS demo_sessions_expires_idx ON demo_sessions(expires_at) WHERE status IN ('starting','live');
