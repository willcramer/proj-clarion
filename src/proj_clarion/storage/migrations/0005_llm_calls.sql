-- ============================================================
-- 0005 — llm_calls
--
-- One row per Anthropic LLM call made by Clarion. Captures the
-- inputs the OTel span carries (model, tokens, cost) plus links
-- back to the originating pipeline + phase so we can answer
-- "how much did pipeline X cost" and "which prompt template
-- dominates the spend" without re-querying Tempo.
--
-- Lifecycle: insert-only. When a pipeline is deleted we set
-- `pipeline_id` to NULL rather than cascading the cost rows out
-- of existence — cost/usage history is the kind of thing we
-- regret losing.
--
-- Non-pipeline callers (agents.extend/refine, profiles.extend)
-- write rows with `pipeline_id`+`phase` NULL — they're still
-- useful for total-cost dashboards but don't roll up under any
-- particular build.
-- ============================================================

CREATE TABLE IF NOT EXISTS llm_calls (
    call_id              TEXT PRIMARY KEY,
    pipeline_id          TEXT REFERENCES pipelines(pipeline_id) ON DELETE SET NULL,
    phase                TEXT,
    prompt_template      TEXT,
    prompt_version       TEXT,
    model                TEXT NOT NULL,
    agent_name           TEXT NOT NULL,
    sigil_generation_id  TEXT,
    input_tokens         INT NOT NULL DEFAULT 0,
    output_tokens        INT NOT NULL DEFAULT 0,
    cache_read_tokens    INT NOT NULL DEFAULT 0,
    cache_write_tokens   INT NOT NULL DEFAULT 0,
    stop_reason          TEXT,
    cost_usd             NUMERIC(12, 6) NOT NULL DEFAULT 0,
    cache_savings_usd    NUMERIC(12, 6) NOT NULL DEFAULT 0,
    ttft_ms              INT,
    attempt              INT NOT NULL DEFAULT 1,
    error_type           TEXT,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT llm_calls_phase_chk CHECK (
        phase IS NULL OR phase IN ('research', 'plan', 'approve', 'generate', 'provision', 'kg-publish')
    )
);

CREATE INDEX IF NOT EXISTS idx_llm_calls_pipeline
    ON llm_calls (pipeline_id, created_at DESC)
    WHERE pipeline_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_llm_calls_phase
    ON llm_calls (phase, created_at DESC)
    WHERE phase IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_llm_calls_created
    ON llm_calls (created_at DESC);

CREATE INDEX IF NOT EXISTS idx_llm_calls_model
    ON llm_calls (model, created_at DESC);
