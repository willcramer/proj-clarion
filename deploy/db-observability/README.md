# Grafana Cloud Database Observability for self-managed Postgres

End-to-end setup to feed a self-managed PostgreSQL 14+ instance into
**Grafana Cloud Database Observability** via the latest stable
**Grafana Alloy** (verified on v1.16.1), using the
`database_observability.postgres` component for query / table /
schema telemetry + `loki.source.file` for the raw Postgres log tail.

> **Note on Alloy v1.16.x:** the `database_observability.postgres`
> component emits **structured log records into Loki** (not Prometheus
> metrics). The Cloud Database Observability app reads from those
> logs. You do **not** need a Mimir / Prometheus push for the DB obs
> surface itself — only a Loki endpoint + `logs:write` token.

Self-contained drop-in: nothing here is Clarion-specific. Lift the
folder into any project that needs Postgres DB obs.

## Files

| File | What it is |
|---|---|
| [`postgresql.conf.snippet`](./postgresql.conf.snippet) | Lines to add to your Postgres server config (`shared_preload_libraries`, `pg_stat_statements.track`, `track_io_timing`). |
| [`init-monitoring-user.sql`](./init-monitoring-user.sql) | Creates a least-privilege role `grafana_dbo11y` with `pg_monitor` + explicit `pg_stat_statements` grant. |
| [`config.alloy`](./config.alloy) | Alloy v1.16+ config (HCL-like): one `database_observability.postgres` component, one optional `loki.source.file` log tail, and a `loki.write` to Grafana Cloud. |
| [`docker-compose.yml`](./docker-compose.yml) | Optional local Alloy harness for testing the config end-to-end. |

## Order of operations

```
                      ┌──────────────┐
            STEP 1 →  │  Postgres    │  postgresql.conf.snippet
                      │  config +    │  → restart
                      │  restart     │
                      └──────────────┘
                             │
                             ▼
                      ┌──────────────┐
            STEP 2 →  │  SQL: user + │  init-monitoring-user.sql
                      │  extension   │  → run as superuser
                      └──────────────┘
                             │
                             ▼
                      ┌──────────────┐
            STEP 3 →  │  Edit config │  config.alloy
                      │  placeholders│  → fill DSN + Cloud auth
                      └──────────────┘
                             │
                             ▼
                      ┌──────────────┐
            STEP 4 →  │  Run Alloy   │  docker compose up -d
                      │  (or sysd)   │  → confirm UI shows Healthy
                      └──────────────┘
                             │
                             ▼
                      ┌──────────────┐
            STEP 5 →  │  Grafana     │  Connections →
                      │  Cloud UI    │  Database Observability
                      └──────────────┘
```

## Step 1 — Postgres server config

Append the contents of `postgresql.conf.snippet` to your
`postgresql.conf`, then restart:

```bash
sudo nano /etc/postgresql/16/main/postgresql.conf   # path varies
sudo systemctl restart postgresql
```

> `shared_preload_libraries` requires a full server restart. The
> `track_io_timing` and `pg_stat_statements.track` settings can be
> live-applied with `ALTER SYSTEM SET … ; SELECT pg_reload_conf();`
> but the preload line is what gates everything else, so you'll
> restart at least once.

Verify after restart:

```sql
SELECT name, setting FROM pg_settings
WHERE name IN ('shared_preload_libraries',
               'pg_stat_statements.track',
               'track_io_timing');
```

Expect `shared_preload_libraries` to include `pg_stat_statements`.

## Step 2 — Monitoring user

Generate a strong password, then apply the SQL against the target
database. The file uses psql variable substitution so the password
never lands in shell history:

```bash
PW="$(openssl rand -hex 24)"
psql -h DB_HOST -U postgres -d postgres \
     -v target_db=YOUR_DATABASE_NAME \
     -v strong_pw="'$PW'" \
     -f init-monitoring-user.sql

# Save $PW to a password manager / secrets vault — you'll plug it
# into config.alloy in Step 3.
```

For multi-database stacks: re-run with a different `target_db` for
each database. The role itself is global (cluster-wide); only the
`CREATE EXTENSION` + `GRANT SELECT ON pg_stat_statements` need
per-database execution.

## Step 3 — Alloy config

Open [`config.alloy`](./config.alloy) and replace these placeholders:

| Placeholder | What |
|---|---|
| `DB_HOST` | Postgres host (FQDN or IP). **NOT a PgBouncer / load-balancer endpoint** — Alloy needs direct session-stable access. |
| `DB_NAME` | Database to telemeter. |
| `grafana_dbo11y:CHANGE_ME_DB_PASSWORD` | Username + password from Step 2. URL-encode special chars. |
| `sslmode=require` | Adjust to `verify-full` for production (recommended) or `disable` only on a private VPC link. |
| `GRAFANA_CLOUD_LOKI_USERNAME` | Numeric instance ID from Cloud → Connections → Loki → "Send Logs". |
| `GRAFANA_CLOUD_API_TOKEN` | Cloud Access Policy token with the `logs:write` scope. (No `metrics:write` needed — DB obs in v1.16+ emits logs, not metrics.) |
| `logs-prod-XXX.grafana.net` | Region-specific Loki endpoint URL — same "Send Logs" page. |
| `/var/log/postgresql/postgresql-*.log` | Adjust glob if your `log_directory` / `log_filename` differ. Or comment the whole `local.file_match` + `loki.source.file` + `loki.process` block out if you don't want raw log shipping (the DB obs collector doesn't need it). |

**Don't route through PgBouncer or any pooler.** The
`database_observability.postgres` collector relies on session-level
queries against `pg_stat_*` and pg_stat_statements text retrieval that
break under transaction-mode pooling. Point Alloy directly at the
Postgres listening port.

### Env-var alternative (cleaner for prod)

Rather than hard-coding credentials in `config.alloy`, swap each
placeholder for `env("VAR")` and pass the values via the environment:

```alloy
basic_auth {
    username = env("GRAFANA_CLOUD_PROM_USERNAME")
    password = env("GRAFANA_CLOUD_API_TOKEN")
}
```

The `docker-compose.yml` already has the env-var block commented out
ready to enable.

## Step 4 — Run Alloy

### Option A — docker-compose (recommended for first test)

```bash
docker compose up -d
docker compose logs -f alloy   # confirm clean startup
```

Open <http://localhost:12345> — the Alloy UI lists every component.
Find `database_observability.postgres.selfhosted_pg` and confirm:

- **Healthy: true** (the component reports `All collectors are healthy`)
- The linked `loki.write.gc_logs` shows non-zero
  `loki_write_sent_entries_total` on `/metrics` and zero
  `loki_write_dropped_bytes_total` (any reason). Cadence is ~60s.

### Option B — systemd (long-running install)

Install Alloy via Grafana's [official packages](https://grafana.com/docs/alloy/latest/setup/install/),
copy `config.alloy` to `/etc/alloy/config.alloy`, then:

```bash
sudo systemctl enable --now alloy
sudo systemctl status alloy
journalctl -u alloy -f
```

## Step 5 — Verify in Grafana Cloud

1. Sign in to Grafana Cloud.
2. **Connections → Database Observability**. Your cluster should appear
   under the `cluster` label you set in `external_labels` (default
   `selfhosted_pg`) within ~2 minutes.
3. Drill into the cluster → expect query stats, table stats, schema
   shape, and the most recent log lines.

Common gotchas to check first if nothing shows up:

| Symptom | Likely cause |
|---|---|
| "No data" in Cloud UI but Alloy says healthy | Wrong Loki region in `loki.write.endpoint.url`. Compare to the URL on Cloud → Connections → Loki → "Send Logs". |
| Alloy startup error: `unrecognized attribute name "scrape_interval"` / `"collectors"` | Old template — those attrs don't exist on `database_observability.postgres` in Alloy v1.16+. Remove them; the component runs all default collectors on a fixed cadence. |
| Alloy startup error: `expected capsule("loki.LogsReceiver"), got capsule("storage.Appendable")` | `forward_to` was pointed at `prometheus.remote_write.*.receiver`. In v1.16+ the DB obs component emits logs, not metrics — point it at `loki.write.*.receiver`. |
| Alloy logs `pq: pg_stat_statements does not exist` | Step 1 was skipped or the server didn't restart. `SHOW shared_preload_libraries;` should include it. |
| Alloy logs `pq: permission denied for relation pg_stat_statements` | Re-run Step 2 inside the target database — the GRANT is per-DB. |
| Alloy logs `no tables detected from pg_tables` repeatedly for `datname=postgres` | Harmless — the schema_details collector iterates every DB the role can reach, and the admin `postgres` DB is empty. As long as you also see activity for your real DB name, ignore it. |
| 401 from Loki push | Token doesn't have `logs:write`. Issue a new Cloud Access Policy token with that scope and rotate the password. |
| Logs stop after a restart | Missing the `alloy_data` volume — Alloy lost its file-tail cursor and re-tailed from byte 0, but Loki dedup dropped the duplicates. Make sure the named volume is mounted. |
| Spikey latency on the Postgres host | `pg_stat_statements.max` too high for the workload. The default collectors in v1.16+ are not individually configurable on the component — if the explain-plans load is unacceptable, you'll need to throttle `auto_explain.log_min_duration` server-side instead. |

## Tuning notes

- **Scrape cadence** is fixed at ~60s inside `database_observability.postgres`
  in v1.16+. The component does not expose `scrape_interval` or
  per-collector toggles in this version; if you need a different
  cadence you'll need a newer Alloy build that re-introduces those knobs.
- **`pg_stat_statements.max = 10000`** is plenty for most apps. If you
  have huge query diversity (lots of dynamic SQL), bump to 50000 — the
  per-entry memory is small.
- **`auto_explain.log_min_duration`** is commented out in the snippet;
  enable it (and the matching loglevel) if you want full plans in
  Loki, but expect a 5-15% log-volume increase on a busy cluster.

## What this does NOT include

- TLS cert pinning on the Postgres connection (use `sslmode=verify-full`
  with a CA file pointer in the DSN when you're ready).
- Connection-pool sizing for Alloy's own DB connections — defaults are
  fine for a single instance; revisit if you point one Alloy at many DBs.
- Postgres replica observability — point a separate
  `database_observability.postgres` component at each replica DSN; they
  can share the same `remote_write` receivers downstream.
