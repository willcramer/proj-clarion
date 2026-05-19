-- ============================================================
-- 0008 — agent_tool_calls
--
-- Per-tool-call audit trail. Records every time an agent reaches out
-- to a system it didn't author (web search, Postgres reads/writes,
-- knowledge-graph reads/writes, Grafana Cloud API calls, file/shell).
-- One row per tool invocation, written by the `track_tool_call`
-- context manager in `observability/tools.py`.
--
-- Why it exists: regulated buyers need a queryable record of "which
-- external systems did the agent touch on this build, and what did it
-- pass them". The OTel span for each tool carries the same data but
-- spans age out of Tempo per retention. Postgres is the durable record.
--
-- Always append; never updated.
--
-- ON DELETE SET NULL on both FKs so the audit trail survives a
-- `pipelines` or `llm_calls` delete — compliance posture is
-- "audit rows outlive the things they audit".
-- ============================================================

CREATE TABLE IF NOT EXISTS agent_tool_calls (
    call_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    pipeline_id      TEXT REFERENCES pipelines(pipeline_id) ON DELETE SET NULL,
    llm_call_id      TEXT REFERENCES llm_calls(call_id)     ON DELETE SET NULL,
    agent_name       TEXT NOT NULL,
    -- Tool taxonomy — kept loose (no CHECK constraint) so new tool
    -- categories can ship without a migration. Canonical values:
    -- 'web_search','db_read','db_write','kg_read','kg_write',
    -- 'api_call','file_read','shell_exec'.
    tool_name        TEXT NOT NULL,
    -- Specific system on the receiving end of the tool call:
    -- 'postgres','serper_api','grafana_cloud_api','anthropic_api',
    -- 'knowledge_graph','filesystem',etc.
    target_system    TEXT,
    -- 'read','write','delete','query','search','execute'.
    action           TEXT,
    -- First 500 chars of the tool input. The context manager strips
    -- newlines + truncates; NO PII / secrets allowed by convention.
    input_summary    TEXT,
    -- First 200 chars of the tool output — usually a row count or
    -- status, never the raw response body.
    output_summary   TEXT,
    success          BOOLEAN NOT NULL DEFAULT TRUE,
    error_msg        TEXT,
    duration_ms      INTEGER,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_agent_tool_calls_pipeline
    ON agent_tool_calls (pipeline_id);

CREATE INDEX IF NOT EXISTS idx_agent_tool_calls_agent
    ON agent_tool_calls (agent_name, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_agent_tool_calls_system
    ON agent_tool_calls (target_system, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_agent_tool_calls_created
    ON agent_tool_calls (created_at DESC);
