# Proj Clarion dev loop
# Run `just` (no args) to see this list

set dotenv-load := true

default:
    @just --list

# === Setup ===

# Install Python deps with uv (creates .venv if missing)
install:
    uv sync --extra dev

# Bring up the local stack: Postgres only.
# Add `--profile cloud` to also bring up the PDC agent (requires GCLOUD_PDC_* vars).
up *args:
    docker compose -f deploy/docker/compose.yaml {{args}} up -d
    @echo ""
    @echo "Stack is up:"
    @echo "  Postgres:  localhost:5432  (user/pass in .env)"
    @echo "  Grafana:   use your Grafana Cloud stack — see GRAFANA_CLOUD_STACK_URL in .env"
    @echo ""

# Bring up the FULL cloud-forwarding stack (Postgres + PDC + Alloy).
# Auto-derives Mode-A vars from your existing OTEL_EXPORTER_OTLP_* in .env —
# you don't need to maintain two copies of the Cloud creds. Idempotent.
up-cloud:
    bash scripts/up_cloud.sh

# Tear down the local stack (keeps volumes)
down:
    docker compose -f deploy/docker/compose.yaml --profile cloud down

# Tear down and wipe volumes — fresh start
nuke:
    docker compose -f deploy/docker/compose.yaml down -v

# Show stack status
ps:
    docker compose -f deploy/docker/compose.yaml ps

# Tail logs from the local stack
logs *args:
    docker compose -f deploy/docker/compose.yaml logs -f {{args}}

# === Database ===

# Open a psql shell against the local Postgres
psql:
    docker compose -f deploy/docker/compose.yaml exec postgres \
        psql -U ${POSTGRES_USER:-clarion} -d ${POSTGRES_DB:-clarion}

# Apply the schema (idempotent)
db-init:
    uv run python -m proj_clarion.cli.main db init

# Drop all tables and re-apply migrations. DEV ONLY.
db-reset:
    uv run python -m proj_clarion.cli.main db reset --yes

# === The agents ===

# Run the Research agent against a URL → writes a CompanyProfile
research url:
    uv run python -m proj_clarion.cli.main research "{{url}}"

# Build a CompanyProfile from discovery notes — SKIPS the web research.
# Notes-only by default (no web access); pass `--also-fetch --url <site>`
# to layer the deep-dive enrichment on top. Then `just plan <profile>`.
#   just research-notes notes.md --company "KFC"
research-notes notes_path *args:
    uv run python -m proj_clarion.cli.main research-notes "{{notes_path}}" {{args}}

# Pretty-print the most recent CompanyProfile
show-profile:
    uv run python -m proj_clarion.cli.main profile show

# Validate all profiles in data/profiles/ against the schema
validate-profiles:
    uv run python -m proj_clarion.cli.main profile validate

# Run the Plan agent against a CompanyProfile JSON → DemoPlan in DB + on disk
plan profile_path:
    uv run python -m proj_clarion.cli.main plan run "{{profile_path}}"

# Pretty-print a plan (UUID prefix accepted, e.g. `just plan-show abc12345`)
plan-show plan_id:
    uv run python -m proj_clarion.cli.main plan show "{{plan_id}}"

# Approve a plan for provisioning. Note is required and goes to the audit log.
plan-approve plan_id note:
    uv run python -m proj_clarion.cli.main plan approve "{{plan_id}}" --note "{{note}}"

# List recent plans
plans:
    uv run python -m proj_clarion.cli.main plan list

# === Generator (v0.3) ===

# Generate business events + traces for a plan. Pass --days N for a smaller dev run.
generate plan_id *args:
    uv run python -m proj_clarion.cli.main generate run "{{plan_id}}" {{args}}

# Wipe all generated events for a plan
generate-clear plan_id:
    uv run python -m proj_clarion.cli.main generate clear "{{plan_id}}" --yes

# === Provisioning (v0.4) ===

# Build Grafana dashboards + alert rules from a plan; default DRY-RUN to disk.
# Pass --push to actually create resources in Grafana Cloud.
provision plan_id *args:
    uv run python -m proj_clarion.cli.main provision run "{{plan_id}}" {{args}}

# Delete a plan's folder + dashboards + alerts from Grafana Cloud
provision-clear plan_id:
    uv run python -m proj_clarion.cli.main provision clear "{{plan_id}}" --yes

# Delete a Grafana folder by UID directly (for orphans whose plan is gone from the DB)
provision-clear-folder folder_uid:
    uv run python -m proj_clarion.cli.main provision clear-folder "{{folder_uid}}" --yes

# List `clarion-*` Grafana folders whose plan is missing from the DB
provision-list-orphans:
    uv run python -m proj_clarion.cli.main provision list-orphans

# === Alloy + live-tail (v0.5) ===

# Probe the OTLP endpoint and report Mode A (Alloy) vs Mode B (direct to Cloud)
check-env:
    uv run python -m proj_clarion.cli.main check env

# Stream business_events as OTLP logs to Alloy → Cloud Loki. Long-running; ctrl-C to stop.
# Pass --from-start to re-emit every event from the beginning of the table.
live-tail plan_id *args:
    uv run python -m proj_clarion.cli.main live-tail run "{{plan_id}}" {{args}}

# Show cursor position and how many rows behind the table tip the live-tailer is
live-tail-status plan_id:
    uv run python -m proj_clarion.cli.main live-tail status "{{plan_id}}"

# === Knowledge Graph (v0.6) ===

# Generate model-rules + prom-rules YAMLs to disk; no push
kg-preview plan_id:
    uv run python -m proj_clarion.cli.main kg preview "{{plan_id}}"

# Push KG rules + start the entity emitter (long-running; ctrl-C to stop)
kg-publish plan_id *args:
    uv run python -m proj_clarion.cli.main kg publish "{{plan_id}}" {{args}}

# Query KG for the plan's entities and report counts
kg-verify plan_id *args:
    uv run python -m proj_clarion.cli.main kg verify "{{plan_id}}" {{args}}

# Run the KG health check (post-emit validation). Pass a plan_id (or
# omit to use the most recent plan + auto-derive customer).
# Exits non-zero on any FAIL so this gates CI / kg-publish.
kg-doctor *args:
    uv run python -m proj_clarion.cli.main kg doctor {{args}}

# === SE Web UI (v0.7) ===

# Start the FastAPI backend on http://127.0.0.1:8765 (dev mode, hot reload)
api:
    uv run uvicorn proj_clarion.api.main:app --reload --host 127.0.0.1 --port 8765

# Start the Vite dev server on http://127.0.0.1:5173 (proxies /api to the backend)
ui:
    cd ui && NODE_EXTRA_CA_CERTS=/etc/ssl/cert.pem npm run dev

# Build the UI for production (output: ui/dist/)
ui-build:
    cd ui && NODE_EXTRA_CA_CERTS=/etc/ssl/cert.pem npm run build


# === Cross-vertical smoke tests ===

# Run research → plan → approve across the default set of industry URLs.
# Catches LLM enum drift / sanitizer gaps for verticals not yet exercised.
# Pass `--bail-on-fail` for fast iteration on a sanitizer fix:
#     just smoke-test-industries --bail-on-fail
# Pass URLs after `--` to override the default list:
#     just smoke-test-industries -- https://acme-retail.com https://erp-vendor.example
smoke-test-industries *ARGS:
    uv run python scripts/smoke_test_industries.py {{ARGS}}

# Install/refresh UI deps
ui-install:
    cd ui && NODE_EXTRA_CA_CERTS=/etc/ssl/cert.pem npm install

# === Quality gates ===

test:
    uv run pytest

# Integration tests — spin up an ephemeral Postgres via testcontainers. Requires Docker.
test-integration:
    uv run pytest -m integration

lint:
    uv run ruff check .
    uv run ruff format --check .

fmt:
    uv run ruff check --fix .
    uv run ruff format .

typecheck:
    uv run mypy src/

check: lint typecheck test
    @echo "All checks passed"

# === Development helpers ===

# Run the AcmeRetail demo end-to-end (research → plan → generate)
# Stub for now — only research works in v0.1
demo-acme-retail:
    just research https://www.acme-retail.com
    just show-profile
