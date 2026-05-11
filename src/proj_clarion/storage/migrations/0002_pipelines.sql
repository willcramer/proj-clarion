-- 0002_pipelines.sql — persisted pipeline runs, events, phase rollup
--
-- Pipelines were process-local (in-memory dict in pipeline_registry.py)
-- through v0.7. The first API restart you don't expect wipes every past
-- build's SSE event log + the list itself, so /pipelines history is
-- unreliable and the chat 503 fix that required a restart took out
-- two days of build artifacts.
--
-- These three tables make pipelines first-class persisted entities:
--   pipelines         — one row per build (includes resume-from-phase
--                       linkage via parent_pipeline_id)
--   pipeline_events   — append-only event log (SSE replay source of truth)
--   pipeline_phases   — denormalised phase rollup so /pipelines list
--                       doesn't have to re-aggregate from events
--
-- Idempotent: every CREATE uses IF NOT EXISTS. Additive only — does not
-- touch existing v0.7 tables.

-- ============================================================
-- pipelines — one row per build run (full or resume-from-phase)
-- ============================================================
CREATE TABLE IF NOT EXISTS pipelines (
    pipeline_id           TEXT PRIMARY KEY,
    url                   TEXT NOT NULL,
    company               TEXT,
    days                  INT NOT NULL DEFAULT 1,
    status                TEXT NOT NULL DEFAULT 'running',
    started_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at           TIMESTAMPTZ,
    error                 TEXT,
    -- Resolved IDs once their phases complete. ON DELETE SET NULL so a
    -- profile/plan delete doesn't take the pipeline history with it.
    profile_id            TEXT REFERENCES company_profiles(profile_id) ON DELETE SET NULL,
    plan_id               UUID REFERENCES demo_plans(plan_id) ON DELETE SET NULL,
    -- 'full' = ran from research; 'phase' = resumed from a specific phase
    trigger               TEXT NOT NULL DEFAULT 'full',
    -- For resume-from-phase: which phase did this build START from?
    starting_phase        TEXT,
    -- Origin pipeline this build resumed from (if any). ON DELETE SET NULL
    -- so deleting the parent doesn't take the resume run with it.
    parent_pipeline_id    TEXT REFERENCES pipelines(pipeline_id) ON DELETE SET NULL,
    CONSTRAINT pipelines_status_chk CHECK (status IN (
        'running', 'done', 'failed', 'cancelled'
    )),
    CONSTRAINT pipelines_trigger_chk CHECK (trigger IN ('full', 'phase')),
    CONSTRAINT pipelines_starting_phase_chk CHECK (
        starting_phase IS NULL OR starting_phase IN (
            'research', 'plan', 'approve', 'generate', 'provision', 'kg-publish'
        )
    )
);

CREATE INDEX IF NOT EXISTS idx_pipelines_started_at
    ON pipelines (started_at DESC);
CREATE INDEX IF NOT EXISTS idx_pipelines_status
    ON pipelines (status, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_pipelines_profile
    ON pipelines (profile_id);
CREATE INDEX IF NOT EXISTS idx_pipelines_plan
    ON pipelines (plan_id);


-- ============================================================
-- pipeline_events — append-only SSE event log, source of truth for
-- replay when a UI client late-joins or reloads.
-- ============================================================
CREATE TABLE IF NOT EXISTS pipeline_events (
    event_id      BIGSERIAL PRIMARY KEY,
    pipeline_id   TEXT NOT NULL REFERENCES pipelines(pipeline_id) ON DELETE CASCADE,
    seq           INT NOT NULL,
    ts            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    event         JSONB NOT NULL,
    UNIQUE (pipeline_id, seq)
);

CREATE INDEX IF NOT EXISTS idx_pipeline_events_pipeline_seq
    ON pipeline_events (pipeline_id, seq);


-- ============================================================
-- pipeline_phases — denormalised phase rollup. /pipelines list reads
-- from here so it doesn't have to scan the events table.
-- Composite PK lets us upsert per (pipeline_id, phase).
-- ============================================================
CREATE TABLE IF NOT EXISTS pipeline_phases (
    pipeline_id   TEXT NOT NULL REFERENCES pipelines(pipeline_id) ON DELETE CASCADE,
    phase         TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'pending',
    started_at    TIMESTAMPTZ,
    finished_at   TIMESTAMPTZ,
    error         TEXT,
    artifact      JSONB,
    PRIMARY KEY (pipeline_id, phase),
    CONSTRAINT pipeline_phases_status_chk CHECK (status IN (
        'pending', 'running', 'done', 'failed', 'skipped'
    )),
    CONSTRAINT pipeline_phases_phase_chk CHECK (phase IN (
        'research', 'plan', 'approve', 'generate', 'provision', 'kg-publish'
    ))
);
