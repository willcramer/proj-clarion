# Handoff prompt for Claude Code

Paste everything below the `---` line into Claude Code after you've unzipped `proj-clarion-v0.1.zip` and `cd`'d into the resulting `proj-clarion/` directory.

---

You're picking up a project called **Proj Clarion** — a Grafana App that turns a prospect URL into a live business observability demo. I've already designed the system and built v0.1 (schemas + Research agent + local stack scaffold). You're going to verify v0.1 actually runs, then build v0.2 (the Plan agent and the Postgres schema).

## Read first, in this order

1. `README.md` — what the project is and what's built
2. `docs/design.md` — full design doc with locked decisions, schemas, AcmeRetail scenario, phasing
3. `src/proj_clarion/schemas/` — the four Pydantic schemas (the contracts)
4. `tests/fixtures/acme-retail_profile.json` — what a successful Research run should produce
5. `src/proj_clarion/agents/research.py` — the existing Research agent (LangGraph-shaped walking skeleton)

Take your time on these. Do not skip the design doc — many decisions in the code make sense only with that context, and the safety posture (public-info-only research, no instrumentation of customer systems, allowlist-enforced fetching, citation-or-flag for every claim) is non-negotiable.

## Operating principles

These are firm.

- **Public information only.** The Research agent must never probe, instrument, or make non-read requests against any prospect's production systems. Every fetch goes through `agents/fetcher.py` which enforces a host allowlist. Don't bypass it.
- **Every claim is cited or flagged.** No silent invention by the LLM. If a claim has no supporting source, it goes into `synthesized_flags` with a rationale.
- **Schemas are contracts.** If you find yourself wanting to change a schema, stop and write a one-paragraph case for the change first. Schemas frozen for v0.1 means we don't change them on a whim in v0.2.
- **Tests for everything you add.** The existing `tests/unit/test_schemas.py` is the pattern: hand-built fixture, schema validates it, integrity rules pass.
- **Don't add cloud infrastructure yet.** No Terraform, no AKS/EKS, no real cluster provisioning. v0.2 is local-only. Cloud lands in v0.5+.
- **Use `just` for everything.** Don't introduce new task runners. The `justfile` is the source of truth for the dev loop.

## Stage 1: Verify v0.1 actually works

Before writing anything new, prove the existing build runs end-to-end on this machine.

1. `cp .env.example .env` and ask me for the Anthropic API key. Don't write a fake one.
2. `just install` — make sure `uv sync` succeeds. If `uv` isn't installed, install it (`curl -LsSf https://astral.sh/uv/install.sh | sh`).
3. `just test` — the schema unit tests in `tests/unit/test_schemas.py` should all pass. If anything fails, fix it before moving on. The AcmeRetail fixture in `tests/fixtures/acme-retail_profile.json` is the source of truth — the schema bends to fit it, not the other way around. Common things you might hit:
   - Pydantic v2 strict mode complaining about something I missed
   - A field type mismatch between the fixture and the schema
   - An import cycle in `schemas/__init__.py`
4. `just up` — Postgres + Grafana OSS should come up clean. `docker compose -f deploy/docker/compose.yaml ps` should show both healthy. If port 3000 or 5432 is already taken on this machine, pick alternates and update `.env.example` plus the compose file consistently.
5. `just research https://www.acme-retail.com` — this calls Anthropic. Confirm a `data/profiles/<id>.json` is produced and that `just show-profile` renders it. The output won't be as rich as the hand-curated fixture; that's expected — the prompts in `agents/research.py` are minimal in v0.1.

When all five steps pass, commit and tell me the results. Note any surprises. Don't proceed to Stage 2 until v0.1 is solid.

## Stage 2: Build v0.2 — Plan agent + database schema + SE review

The goal of v0.2 is: an SE can run `just plan <profile_path>`, an LLM produces a `DemoPlan` from the profile, the plan lands in Postgres, the SE reviews/edits it, and the SE can move it from `draft` to `approved_for_provision`. No data generation yet — that's v0.3.

### 2a. Postgres schema

Add a `src/proj_clarion/storage/` package containing:

- `schema.sql` — DDL for the v0.2 tables. At minimum:
  - `company_profiles` (profile_id PK, json blob, created_at, source_url)
  - `demo_plans` (plan_id PK UUID, source_profile_id FK, json blob, review_state, created_at, updated_at)
  - `kg_nodes` (node_id PK, plan_id FK, type, subtype, label, attributes JSONB, live_state_binding JSONB)
  - `kg_edges` (edge_id PK, plan_id FK, edge_type, from_node_id, to_node_id, attributes JSONB)
  - `business_events` (event_id PK, plan_id FK, ts TIMESTAMPTZ, event_type, business_entity_ids text[], payload JSONB, trace_id) — table only, no data inserted yet, this is for v0.3
  - Indexes on plan_id and (plan_id, ts) for the events table
- `db.py` — a thin SQLAlchemy 2.0 connection factory using `psycopg`. Read DSN from env (POSTGRES_HOST/PORT/USER/PASSWORD/DB).
- `repositories.py` — `ProfileRepo`, `PlanRepo`, `KGRepo` with `upsert`, `get`, `list`, `set_review_state` methods. Keep the surface small; we don't need full CRUD yet.
- A migration approach. For v0.2 I'd use raw SQL files in `src/proj_clarion/storage/migrations/` numbered `0001_*.sql`, applied by a tiny Python runner. We do not need Alembic in v0.2; revisit when schema changes get hairy.

Implement `just db-init` (currently a stub in `cli/main.py`) so it applies all migrations idempotently. Add `just db-reset` that drops and recreates the schema for development.

Test it: write `tests/integration/test_storage.py` that uses `testcontainers` or a Postgres fixture to spin up a throwaway DB, applies the migrations, round-trips a AcmeRetail profile and a plan through the repos, and reads them back. Mark this test as integration so it doesn't run in `just test` by default; add `just test-integration` to run it.

### 2b. Plan agent

Add `src/proj_clarion/agents/planner.py`. Same shape as `research.py` (TypedDict state, async functions, a `run_plan` entrypoint). The phases:

1. `analyze_profile` — read the CompanyProfile, decide which audience the plan defaults to (use the profile's signals to pick `business`, `technical`, or `pivot`), pick which 4-7 business processes to model from `business_entity_candidates` plus the `channels`.
2. `model_processes` — for each chosen process, produce a `BusinessProcessModel` with steps, KPIs, service-mapping, and 2-3 failure modes. The service IDs you produce here will be referenced later by `kg_nodes` — so pick stable, snake_case names like `svc-checkout`, `svc-wms-bridge`, `svc-pos`.
3. `build_kg` — produce nodes and edges. Two tiers: business entities at the top (region → channel → store), technical resources below (cluster → namespace → service → database/queue/external). Cross-tier edges (`runs_on`, `serves`) connect the two. The AcmeRetail example in the design doc (Store NA-1 → pos-svc → wms-bridge → <ERP-vendor> ERP) is the pattern.
4. `script_incident` — produce one `IncidentScript` matching the design doc's AcmeRetail scenario: a WMS-bridge degradation at T+4min that backs up an order-block queue, affects 3 channels, recovers at T+11min. Make it parameterizable so other companies get sensible defaults.
5. `propose_dashboards_and_alerts` — produce `DashboardSpec` and `AlertSpec` lists. At minimum one Business Health dashboard, one Technical Health dashboard, one Pivot dashboard, and one alert per failure mode in the incident script. Don't generate the actual Grafana JSON — that's v0.4. Just the spec.
6. `propose_assistant_tools` — produce 3-5 `AssistantTool` entries. Examples for AcmeRetail: `store_health_today`, `region_sales_vs_forecast`, `channel_health`, `service_dependencies_for_business_entity`. Each is a SQL view we'll create against the Postgres tables in v0.3.

The orchestration uses LangGraph and is OpenLIT-instrumented like `research.py`. Every LLM call gets a meaningful span name (`plan.analyze_profile`, `plan.model_processes`, etc.) so the meta-observability story lands.

The agent's output is a complete `DemoPlan` that validates against the schema. Persist it via `PlanRepo.upsert` with `review_state="draft"`.

### 2c. SE review CLI

Add three commands to `cli/main.py`:

- `proj-clarion plan run <profile_path>` — runs the planner, prints a summary of what was produced, saves to DB and to `data/plans/<plan_id>.json`.
- `proj-clarion plan show <plan_id>` — renders the plan as a rich tree: business processes, KG node count by type, incident timeline, dashboard specs. Don't dump the whole JSON; show the *shape* in a way an SE can grok in 30 seconds.
- `proj-clarion plan approve <plan_id>` — moves `review_state` from `draft` → `approved_for_provision`. Requires `--note "..."` justifying the approval; that note gets appended to a `plan_audit_log` table you'll add to the schema.

Add corresponding `just plan`, `just plan-show ID`, `just plan-approve ID` recipes.

### 2d. Tests

- `tests/unit/test_planner.py` — mock the Anthropic client (use `respx` against the SDK's HTTP layer or a thin fake `Anthropic` client). Feed it the AcmeRetail fixture profile. Assert: produces a `DemoPlan` that passes schema validation, every KG edge resolves to a node, every dashboard spec references a valid datasource.
- `tests/integration/test_plan_pipeline.py` — full pipeline: profile → plan → DB persist → DB read → schema re-validates. Uses real Postgres via testcontainers.

### 2e. Document

Update `README.md` with the v0.2 commands. Update `docs/design.md` only if a real change happened — not for cosmetic things.

## What I want from you when v0.2 is done

A clean PR (or a single commit if you're working on main) that:

1. Stage 1 verification results: did v0.1 work as shipped? What did you have to fix?
2. The v0.2 implementation per the spec above
3. A `data/plans/acme-retail-example.json` showing what the planner produces for AcmeRetail
4. A short `CHANGELOG.md` entry for v0.2
5. All tests passing (`just check && just test-integration`)
6. A note on anything you'd push back on or do differently

## Things that are easy to get wrong

- **Don't make the Plan agent reference fields by index.** Use stable IDs (`process_id`, `node_id`, `step_id`) so when the plan is regenerated or partially edited, references survive.
- **Don't let the LLM invent KG node IDs that don't appear in `nodes`.** Validate referential integrity before the plan is written. There's already a `KnowledgeGraph.validate_referential_integrity()` method — call it.
- **Don't forget the audit trail.** Approval decisions need a record of who, when, and why.
- **Don't make `plan show` dump 800 lines of JSON.** SEs will not read that. The output should fit on one screen and say "12 KG nodes, 18 edges, 4 dashboards, 6 alerts, incident at T+4min" with click-to-expand details.
- **Don't break the existing schema tests.** They are the canary.
- **Don't add new dependencies without asking.** `pyproject.toml` was chosen carefully. If you genuinely need something new, add it with a one-line justification in your commit message.

## When to stop and ask

- If a schema change feels necessary
- If you find a real safety hole in the research-only posture
- If the LLM's outputs are consistently low-quality even with prompt iteration (we may need to switch to structured-output / tool-use mode)
- If a test fails in a way that suggests the design is wrong, not the implementation

Otherwise, keep moving. The AcmeRetail opp is open. Speed matters but not at the cost of trust in the output.

Good luck. The bar is "an SE on the AcmeRetail account would look at the generated DemoPlan and say 'yes, that's the demo I want to run.'"
