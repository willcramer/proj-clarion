-- ============================================================
-- 0007 — llm_calls.is_stream
--
-- Add a boolean column flagging whether the LLM call used the
-- streaming transport. As of v0.7.0 every call from llm_client
-- goes through `messages.stream()` internally so the TTFT
-- (time-to-first-token) timestamp is captured on every row.
-- The flag exists so historical rows from before the refactor
-- (where ttft_ms is NULL) stay distinguishable from a brand-new
-- stream call that legitimately had no text deltas yet.
--
-- Default FALSE so older rows stay correct without a backfill.
-- The wrapper writes TRUE on every new insert post-0.7.0.
-- ============================================================

ALTER TABLE llm_calls
    ADD COLUMN IF NOT EXISTS is_stream BOOLEAN NOT NULL DEFAULT FALSE;
