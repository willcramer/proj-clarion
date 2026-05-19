-- ============================================================
-- 0004 — profile_audit_log
--
-- Each row records one SE-driven extend on a CompanyProfile via
-- POST /api/profiles/{id}/extend. The UI uses this as the durable
-- audit trail (the in-page chat is a local mirror in localStorage).
--
-- Always append; the same profile can be extended many times. The
-- agent's per-field counts (channels:+1, tech_stack_signals:+2, …)
-- go in `additions` as JSONB so the global /audit page can render a
-- compact row even after the merged profile has moved on.
--
-- `applied` is FALSE when the agent decided not to add anything
-- (e.g. couldn't find sources). We still log those for visibility,
-- they explain "I asked but nothing changed" without re-running.
-- ============================================================

CREATE TABLE IF NOT EXISTS profile_audit_log (
    audit_id    BIGSERIAL PRIMARY KEY,
    profile_id  TEXT NOT NULL REFERENCES company_profiles(profile_id) ON DELETE CASCADE,
    actor       TEXT NOT NULL DEFAULT 'se',
    prompt      TEXT NOT NULL,
    summary     TEXT NOT NULL,
    additions   JSONB NOT NULL DEFAULT '{}'::jsonb,
    applied     BOOLEAN NOT NULL DEFAULT TRUE,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_profile_audit_log_profile
    ON profile_audit_log (profile_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_profile_audit_log_created
    ON profile_audit_log (created_at DESC);
