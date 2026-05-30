-- ============================================================
-- 0011 — plan_refinement_sessions + plan_refinement_turns
--
-- The Refine-via-chat surface on the Plan detail page accumulates
-- proposed changes across multiple turns. Sessions group those turns;
-- turns hold the per-message structured-tool-use output. The flow:
--
--   open session ──▶ user turn ──▶ assistant turn (with proposed_changes
--                                  from Claude tool_use)
--                ──▶ user turn ──▶ assistant turn
--                ──▶ … N turns …
--                ──▶ summarize (collapse proposals, cache summary)
--                ──▶ apply (decide phase, kick off pipeline)
--                ──▶ status = 'applied' (terminal)
--
-- plan_id is intentionally NOT a foreign key — a deleted plan should
-- not silently sweep away the refinement history (audit / forensics).
-- Sessions become orphans on plan delete; the API filters by plan_id
-- and tolerates plans that no longer exist.
--
-- turns FK back to sessions with ON DELETE CASCADE — a session and
-- its turns are one logical unit, never separately useful.
-- ============================================================

CREATE TABLE IF NOT EXISTS plan_refinement_sessions (
    session_id      BIGSERIAL PRIMARY KEY,
    plan_id         TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'open'
                    CHECK (status IN ('open', 'summarized', 'applied', 'cancelled')),
    summary_cache   JSONB,
    phase_decision  TEXT
                    CHECK (phase_decision IS NULL
                           OR phase_decision IN ('plan', 'research+plan', 'full')),
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Fast lookup for the "open session for this plan" query (every chat
-- turn triggers one). Partial unique index ensures one open session
-- per plan at a time — mirrors the `demo_sessions_one_active_per_plan`
-- pattern from 0003.
CREATE UNIQUE INDEX IF NOT EXISTS idx_plan_refinement_sessions_one_open_per_plan
    ON plan_refinement_sessions (plan_id)
    WHERE status = 'open';

CREATE INDEX IF NOT EXISTS idx_plan_refinement_sessions_plan_status
    ON plan_refinement_sessions (plan_id, status);

-- updated_at trigger so status / summary / phase mutations bump the
-- column without callers having to remember.
DROP TRIGGER IF EXISTS plan_refinement_sessions_touch_updated_at ON plan_refinement_sessions;
CREATE TRIGGER plan_refinement_sessions_touch_updated_at
    BEFORE UPDATE ON plan_refinement_sessions
    FOR EACH ROW
    EXECUTE FUNCTION touch_updated_at();


CREATE TABLE IF NOT EXISTS plan_refinement_turns (
    turn_id          BIGSERIAL PRIMARY KEY,
    session_id       BIGINT NOT NULL
                     REFERENCES plan_refinement_sessions(session_id) ON DELETE CASCADE,
    role             TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    content          TEXT NOT NULL,
    -- List of ProposedChange entries (kind/target/payload/rationale) emitted
    -- by Claude's tool_use block. Only populated on assistant turns; user
    -- turns hold the raw prompt and nothing structured.
    proposed_changes JSONB,
    tokens_in        INTEGER,
    tokens_out       INTEGER,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- Retrieve all turns of a session in order.
CREATE INDEX IF NOT EXISTS idx_plan_refinement_turns_session
    ON plan_refinement_turns (session_id, turn_id);
