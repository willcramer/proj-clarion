-- ============================================================
-- Grafana Cloud Database Observability — least-privilege user
--
-- Creates `grafana_dbo11y`, a dedicated read-only role that has just
-- enough permission for Alloy's `database_observability.postgres`
-- collector to read pg_stat_*, information_schema, and
-- pg_stat_statements. Nothing more.
--
-- Run as a superuser, against EACH database you want telemetered.
-- For multi-database stacks, run the CONNECT + CREATE EXTENSION +
-- GRANT SELECT block once per database.
--
-- Replace placeholders before running:
--   :target_db    → the database name (e.g. 'clarion', 'app_prod')
--   :strong_pw    → a strong random password (e.g. `openssl rand -hex 24`)
--
--   psql -h DB_HOST -U postgres -v target_db=clarion \
--                                -v strong_pw="'$(openssl rand -hex 24)'" \
--                                -f init-monitoring-user.sql
--
-- (`-v` quotes the value; `'$(…)'` keeps the quotes intact for psql
--  variable substitution.)
-- ============================================================

-- ── Role creation ────────────────────────────────────────────
--
-- LOGIN: required so Alloy can authenticate.
-- NOSUPERUSER + INHERIT (defaults): no escalation.
-- NOCREATEROLE + NOCREATEDB: no role/db management.
-- pg_monitor is the canonical Postgres built-in role (pg10+):
--   pg_read_all_settings + pg_read_all_stats + pg_stat_scan_tables.
-- It covers the pg_stat_statements view contents AND the query text
-- on PG 14+ — Alloy doesn't need pg_read_server_files or anything
-- broader.

CREATE ROLE grafana_dbo11y
    WITH LOGIN
         PASSWORD :strong_pw
         NOSUPERUSER
         NOCREATEDB
         NOCREATEROLE
         NOREPLICATION
         INHERIT;

GRANT pg_monitor TO grafana_dbo11y;

-- ── Database access ──────────────────────────────────────────
--
-- Explicit CONNECT (in case the DB has REVOKED FROM PUBLIC). The
-- pg_stat_statements grant is redundant given pg_monitor → pg_read_all_stats
-- but stated explicitly so a future audit reading just the GRANTs can
-- see the intent without chasing the role-membership chain.

\connect :target_db

GRANT CONNECT ON DATABASE :target_db TO grafana_dbo11y;

-- pg_stat_statements lives in whatever schema CREATE EXTENSION puts it
-- in; default is public. Create it idempotently (no-op if it exists).
CREATE EXTENSION IF NOT EXISTS pg_stat_statements;

GRANT SELECT ON pg_stat_statements TO grafana_dbo11y;

-- ── Verification ─────────────────────────────────────────────
--
-- Run these after applying to confirm Alloy will succeed:

-- 1. Role exists with login + pg_monitor membership
--    SELECT rolname, rolcanlogin
--    FROM pg_roles WHERE rolname = 'grafana_dbo11y';
--
--    SELECT m.rolname AS member_of
--    FROM pg_auth_members am
--    JOIN pg_roles r ON r.oid = am.member
--    JOIN pg_roles m ON m.oid = am.roleid
--    WHERE r.rolname = 'grafana_dbo11y';
--    -- Expect: 'pg_monitor'
--
-- 2. Extension is installed in the target DB
--    SELECT extname, extversion FROM pg_extension WHERE extname = 'pg_stat_statements';
--
-- 3. The monitoring role can actually SELECT from the view
--    SET ROLE grafana_dbo11y;
--    SELECT COUNT(*) FROM pg_stat_statements LIMIT 1;
--    RESET ROLE;
--    -- Expect: a row count, NOT a permission-denied error.
