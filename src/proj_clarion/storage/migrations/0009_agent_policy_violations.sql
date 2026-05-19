-- ============================================================
-- 0009 — agent_policy_violations
--
-- Guardrail / anomaly detection. Each row is one violation flagged
-- against an agent's behaviour: cost spike on a single LLM call,
-- runaway output token count, excessive retries, a tool invocation
-- outside the agent's allowed scope, or a prompt-injection pattern
-- detected in external content the agent was about to process.
--
-- Why it exists: regulated buyers need the agent observability story
-- to include "what would have triggered an alert and why". This table
-- is the durable record; the same event also lands as a
-- `policy_violation` span event in Tempo so it shows up on the AI-obs
-- trace tree.
--
-- Always append; the `resolved` flag flips via a small admin route
-- (out of scope for this PR — Grafana panels filter on resolved=false).
--
-- ON DELETE SET NULL on both FKs so violation records survive a
-- pipelines/llm_calls cascade delete.
-- ============================================================

CREATE TABLE IF NOT EXISTS agent_policy_violations (
    violation_id     UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    pipeline_id      TEXT REFERENCES pipelines(pipeline_id) ON DELETE SET NULL,
    llm_call_id      TEXT REFERENCES llm_calls(call_id)     ON DELETE SET NULL,
    agent_name       TEXT NOT NULL,
    -- Vocabulary — kept open (no CHECK) so new detector types can
    -- ship without a migration:
    --   'unexpected_tool'     — agent called a tool outside its allow-set
    --   'cost_spike'          — single call cost > $0.50
    --   'output_too_long'     — output_tokens > 8000
    --   'prompt_injection'    — suspicious external-content pattern
    --   'high_attempt_count'  — attempt > 3 on same call
    --   'scope_exceeded'      — agent wrote to a read-only system
    violation_type   TEXT NOT NULL,
    -- 'low','medium','high','critical' — drives alert routing.
    severity         TEXT NOT NULL,
    -- JSONB blob with detector-specific evidence (threshold, observed
    -- value, matched pattern excerpt, etc.). The Grafana panels render
    -- this raw for the on-call investigator.
    details          JSONB NOT NULL DEFAULT '{}'::jsonb,
    resolved         BOOLEAN NOT NULL DEFAULT FALSE,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_policy_violations_pipeline
    ON agent_policy_violations (pipeline_id);

CREATE INDEX IF NOT EXISTS idx_policy_violations_severity
    ON agent_policy_violations (severity, created_at DESC)
    WHERE resolved = FALSE;

CREATE INDEX IF NOT EXISTS idx_policy_violations_type
    ON agent_policy_violations (violation_type, created_at DESC);
