-- ============================================================
-- 0012 — assistant_conversations + assistant_turns
--
-- Backing store for the global Clarion Assistant (Cmd-K chat that
-- spans every page). Each conversation is one persistent thread the
-- SE can return to; each turn is one role-tagged message inside that
-- thread. Tool-use lives in JSONB columns on turns so we can replay
-- a thread exactly the way Claude saw it.
--
-- A turn's `role` is one of:
--   * 'user'      — SE typed it
--   * 'assistant' — agent emitted narrative (and optionally tool_calls)
--   * 'tool'      — backend executed a tool and shipped the result back
--                   to Claude. Stored as its own turn so a replay reads
--                   left-to-right without re-running the tool.
--
-- `context_scope` on each turn captures the page context at send time
-- (e.g. {"plan_id": "abc12345"}) so a future assistant looking at the
-- conversation can reason about "the SE was on this plan when they
-- asked X" without re-fetching.
--
-- last_message_at on the conversation is bumped by the
-- `touch_last_message` helper whenever a turn lands — powers the
-- "today / last 7d / older" buckets in the conversation picker UI.
-- ============================================================

CREATE TABLE IF NOT EXISTS assistant_conversations (
    conversation_id  BIGSERIAL PRIMARY KEY,
    actor            TEXT NOT NULL DEFAULT 'se',
    title            TEXT,
    status           TEXT NOT NULL DEFAULT 'active'
                     CHECK (status IN ('active', 'archived')),
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_message_at  TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_assistant_conversations_actor_status_recent
    ON assistant_conversations (actor, status, last_message_at DESC);

DROP TRIGGER IF EXISTS assistant_conversations_touch_updated_at ON assistant_conversations;
CREATE TRIGGER assistant_conversations_touch_updated_at
    BEFORE UPDATE ON assistant_conversations
    FOR EACH ROW
    EXECUTE FUNCTION touch_updated_at();


CREATE TABLE IF NOT EXISTS assistant_turns (
    turn_id          BIGSERIAL PRIMARY KEY,
    conversation_id  BIGINT NOT NULL
                     REFERENCES assistant_conversations(conversation_id) ON DELETE CASCADE,
    role             TEXT NOT NULL
                     CHECK (role IN ('user', 'assistant', 'tool')),
    content          TEXT NOT NULL DEFAULT '',
    -- Tool-use payloads — only one of these is populated per turn:
    --   * assistant turns may have `tool_calls` (list of Claude tool_use blocks)
    --   * tool       turns have `tool_results` (list of tool_result blocks
    --                fed back to Claude on the next iteration)
    tool_calls       JSONB,
    tool_results     JSONB,
    -- Page context pinned at the moment this turn was sent. Lets a
    -- future assistant know "the SE was viewing plan abc when they
    -- typed this" without re-deriving from elsewhere.
    context_scope    JSONB,
    tokens_in        INTEGER,
    tokens_out       INTEGER,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_assistant_turns_conv
    ON assistant_turns (conversation_id, turn_id);
