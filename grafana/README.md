# Grafana dashboards

This folder holds the Grafana Cloud dashboards Clarion ships alongside the
pipeline. They're tracked as JSON (in the
[Dashboard.v2](https://grafana.com/docs/grafana/latest/dashboards/) schema) so
the dashboard-as-code story matches the rest of the project.

## `clarion-dashboard.json` — Clarion · Dev-to-Prod KPIs

Five tabs that tell the dev→prod readiness story over a single shared OTLP
stream:

| Tab | What it answers |
|---|---|
| **Overview** | "Is everything OK right now?" — 6 cross-cutting KPIs + 2 trends |
| **Pipelines** | "Are builds running?" — 24h success rate, MTTR, phase success matrix, top errors |
| **AI & LLM** | "What is the AI doing?" — cost, cache hits, TTFT, phase mix, cost by prompt template |
| **Guardrails & Health** | "Is it safe and reliable?" — system health, policy violations, API latency, multi-cloud coverage |
| **Adoption** | "Is anyone using it?" — SE engagement, claim accept/dismiss, profile reuse |

The dashboard reads from the Postgres tables Clarion writes (`llm_calls`,
`llm_evals`, `agent_tool_calls`, `agent_policy_violations`, `system_health`,
`pipelines`, `pipeline_phases`, `demo_plans`, etc.) — same datasource the
pipeline already requires.

## Importing into your stack

The JSON references a Postgres datasource via a placeholder UID:

```
"name": "YOUR_POSTGRES_DATASOURCE_UID"
```

Two ways to wire it up:

### Option A — search-and-replace before import

Replace the placeholder with your own Postgres datasource UID, then upload via
the Grafana UI (**Dashboards → Import → Upload JSON file**) or via the API.

```bash
# 1. Find your Postgres datasource UID
gcx resources get datasources --json | jq '.[] | select(.type=="postgres") | {name, uid}'

# 2. Substitute and push (gcx supports the Dashboard.v2 schema directly)
sed "s/YOUR_POSTGRES_DATASOURCE_UID/<your-uid>/g" clarion-dashboard.json > /tmp/clarion-dashboard-local.json
mkdir -p /tmp/dash-push/resources/dashboards.v2.dashboard.grafana.app
cp /tmp/clarion-dashboard-local.json /tmp/dash-push/resources/dashboards.v2.dashboard.grafana.app/clarion-dashboard.json
gcx resources push --path /tmp/dash-push
```

### Option B — let Grafana prompt

If you import via the UI without substituting, Grafana will prompt you to
remap the missing datasource on first load. Less repeatable than option A,
but fine for a one-off.

## Working on the dashboard

The dashboard structure is in
[`ui/src/pages/About.tsx`](../ui/src/pages/About.tsx) (the page that explains
what each tab does to a customer). The instrumentation that feeds it lives in
[`src/proj_clarion/observability/`](../src/proj_clarion/observability/) —
`llm_client.py`, `policy.py`, `tools.py`, `health.py`.

To pull live edits back into this file:

```bash
gcx resources pull dashboards/<your-dashboard-uid>
cp resources/dashboards.v2.dashboard.grafana.app/<uid>.json grafana/clarion-dashboard.json
# Strip your tenant's UID before committing:
sed -i '' "s/<your-uid>/YOUR_POSTGRES_DATASOURCE_UID/g" grafana/clarion-dashboard.json
```
