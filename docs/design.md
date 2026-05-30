# Grafana Proj Clarion — Design Document

**Working title:** Proj Clarion (Grafana App plugin for SE-driven business observability demos)
**Status:** v0.1 — design phase, pre-implementation
**Audience:** Engineering, Product, GTM leadership
**Last updated:** 2026-05-07

---

## 1. Purpose

A Grafana App plugin that lets a Solutions Engineer turn a prospect's name and a few inputs into a live, ephemeral, customer-shaped demo environment in Grafana Cloud — complete with real Kubernetes infrastructure, business-domain entities, dashboards for both technical and business audiences, alerts, and a knowledge graph that ties business outcomes to underlying systems.

The differentiator against incumbents (Dynatrace, Datadog, AppDynamics, Splunk) is not feature parity on APM. It is the unification of business KPIs and technical signals in a single pane that a non-technical buyer can navigate via Grafana Assistant.

## 2. Non-goals

- **Self-service for prospects.** v1 is SE-driven; prospects do not log in and run the factory themselves.
- **Instrumenting real customer systems.** The factory only reads public information about the prospect. It never sends a packet to their production infrastructure, never installs anything on their side, never probes their domains beyond what a reader of public web pages would do.
- **Replacing manual SE work.** The factory accelerates and standardizes demo prep; it does not remove the SE's judgment from the loop.
- **Production telemetry pipeline.** Generated data is illustrative. Prospects are not encouraged to wire their real systems into the demo stack.

## 3. Locked decisions

| Area | Decision |
|------|----------|
| Packaging | Grafana App plugin |
| Deployment model | Centrally hosted orchestration service, single trust boundary |
| Cloud provider | AWS first, Azure second |
| Backend stack | Python (AI brain), Go (plugin backend), React (plugin UI), Terraform (IaC) |
| LLM strategy | Multi-model router (showcases AI observability), Claude as primary |
| Data behavior | Hybrid — pre-baked history with live last 30 minutes |
| Knowledge graph rendering | Grafana Node Graph + BigQuery for v1; design open for custom panel later |
| Test fixture | Fictional Northwind Provisions for tests, AcmeRetail as real-world validation |
| Research scope | Public sources only, allowlisted domains, every claim cited or flagged |
| Production boundary | Zero contact with prospect production systems; persistent disclaimer banner |

## 4. System architecture overview

Five logical components with thin contracts between them.

1. **Plugin (Grafana App).** React UI for the SE; Go backend that holds a token to call orchestration. No cloud credentials.
2. **Orchestration service (Python, hosted centrally).** LangGraph-based agentic pipeline. Holds cloud credentials, drives the four phases — Research, Plan, Provision, Reap.
3. **Ephemeral cloud landscape.** Per-demo Kubernetes cluster (EKS first) with workloads that mirror the prospect's stack at signal-shape level. Tagged for TTL teardown.
4. **Data plane.** Two sinks. BigQuery for business entities and events (Grafana datasource). Grafana Cloud LGTMP (Loki, Grafana, Tempo, Mimir, Pyroscope) plus AI Observability for telemetry.
5. **Demo surface.** Generated dashboards (business view, technical view, pivot view), alert rules, knowledge graph panel, Assistant tool definitions.
6. **Clarion Assistant.** A single, app-wide agentic chat (it replaced the per-page Refine/Extend panels). It runs the same Anthropic SDK + tool loop as the pipeline — start/re-run a build, extend a profile, approve a plan, start/stop a demo, cancel a running build — with an approval gate before any build kicks off (toggleable to hands-free). It is instrumented exactly like the build phases: an `assistant.conversation` span groups each turn, the LLM rounds nest as `gen_ai.chat` spans (`agent_name=clarion.assistant`), and every tool runs under an `execute_tool {name}` span, with the same per-call cost rows landing in `llm_calls`. So a chat session reads like a build trace in Tempo / AI Observability.

## 5. The four schemas (structural depth)

These are the contracts that hold the pipeline together. Designed as JSON; expressed here at the structural level. Field-by-field schemas come with the code.

### 5.1 CompanyProfile

The output of the Research phase. Every claim has a citation or is explicitly marked synthesized.

Top-level structure:

- `company` — name, legal name, headquarters, founding year, ownership type
- `industry_taxonomy` — primary industry, sub-industry, business model archetype (B2C retail, B2B marketplace, SaaS, manufacturing, etc.)
- `revenue_signals` — public revenue figures, growth direction, any disclosed segments
- `channels` — list of go-to-market channels (D2C web, D2C retail stores, B2B direct, wholesale, marketplaces, white-label)
- `geographic_footprint` — countries, regions, languages, currencies, named flagship locations
- `tech_stack_signals` — list of inferred technical components, each with confidence and source. Examples: ERP system, e-commerce platform, cloud provider, CDN, observability incumbents
- `agentic_signals` — current and likely-near-term AI workloads (recommendation engines, virtual assistants, computer vision, demand forecasting)
- `recent_strategic_priorities` — pulled from earnings calls, press releases, leadership interviews
- `incumbent_observability` — list of existing observability vendors, with citations
- `pain_signals` — explicit or strongly-implied pain points from public material
- `business_entity_candidates` — list of candidate top-level business entities for the knowledge graph (stores, regions, business units, product lines, fulfillment centers)
- `provenance` — array of every source URL touched, with timestamp, content type, and the specific claims it grounds
- `synthesized_flags` — array of any claim the LLM produced without a source, with rationale

### 5.2 DemoPlan

The output of the Plan phase. This is the SE review gate — nothing downstream runs until an SE approves the plan.

Top-level structure:

- `plan_id` — UUID; ties everything in this demo together (BigQuery dataset name, K8s namespace, Grafana folder, alert group)
- `target_audience` — business, technical, or pivot
- `narrative` — one paragraph the SE will rehearse; the demo's "why this matters"
- `business_process_model` — the 4-7 core flows with KPIs (defined in 5.3 below)
- `infrastructure_blueprint` — what we'll provision: cluster size, namespaces, services, databases, queues, external API mocks, agentic workloads
- `data_blueprint` — what we'll generate: business event volumes, time horizon, diurnal pattern, entity counts (e.g., 12 stores, 4 regions, 3 channels)
- `incident_script` — deterministic timeline of the demo's "story arc" (defined in 5.5 below)
- `dashboard_spec` — list of dashboards to generate with their primary panels
- `alert_spec` — list of alert rules with thresholds tied to the incident script
- `knowledge_graph_spec` — node and edge definitions (defined in 5.4 below)
- `assistant_tools` — list of named SQL views and metric queries that Grafana Assistant will be allowed to use
- `cost_envelope` — estimated cost, TTL, hard ceiling
- `branding` — `customer_facing` toggle, banner text, logo handling
- `review_state` — draft, se_reviewed, approved_for_provision, provisioned, torn_down

### 5.3 BusinessProcessModel (nested in DemoPlan)

The bridge from "this company exists" to "here's what we'll show happening."

For each process:

- `process_id` and `name` — e.g., "order_to_cash_d2c", "store_replenishment", "wholesale_partner_sync"
- `description` — one sentence
- `business_steps` — ordered list of business-meaningful stages (Browse, Add to cart, Checkout, Fulfill, Settle), each with the KPI that measures success
- `service_mapping` — for each business step, the technical services that implement it
- `kpis` — measurable outcomes: conversion rate, time-to-fulfill, GMV, error budgets
- `failure_modes` — the realistic ways this process breaks; the incident script will pull from this list

### 5.4 KnowledgeGraph (nested in DemoPlan)

Two-tier graph — business entities and technical resources — with cross-tier edges.

Node types:

- `business_entity` — store, region, channel, product line, distribution center, business unit
- `technical_resource` — cluster, namespace, service, deployment, database, queue, external dependency
- `agentic_resource` — agent, tool, model, vector index

Each node carries: id, type, label, attributes (free-form key-value), live-state-binding (a query template that Grafana evaluates at render time to color the node by health).

Edge types:

- `runs_on` — business entity → technical resource (Store NA-1 Store runs on pos-svc in eks-retail-east)
- `depends_on` — service → service (pos-svc depends on erp-vendor-bridge)
- `integrates_with` — service → external dep (erp-vendor-bridge integrates with erp-vendor-erp)
- `serves` — technical resource → business entity (the inverse of runs_on, for queries the other direction)
- `contains` — region contains store; cluster contains namespace

The graph is stored as two tables in BigQuery (`kg_nodes`, `kg_edges`). The Grafana Node Graph panel reads these via SQL. Future custom panel work can read the same tables.

### 5.5 IncidentScript (nested in DemoPlan)

Deterministic timeline so demos are reproducible.

- `script_id` — UUID
- `total_duration_minutes` — typically 10-15
- `arming_mode` — `historical_replay` (zoom to a past window) or `live_armed` (fires N minutes after demo start)
- `events` — ordered list, each with offset, target service or business entity, type (latency_spike, error_burst, throughput_drop, queue_back_pressure, dependency_unavailable, agent_hallucination, token_cost_spike), magnitude, recovery_offset, expected_alert_id
- `narrator_cues` — for each event, a one-line note for the SE: "this is where you click into the trace"

## 6. AcmeRetail validation: two demo workflows

Both demos use the same underlying environment, the same incident, and the same data. They differ only in the dashboard the SE opens first and the order in which they ask Grafana Assistant questions. This is intentional — it shows that one observability platform serves both audiences from one source of truth.

### 6.1 Shared scenario

It is a Saturday morning. A weekend marketing push is driving above-normal traffic to D2C web and to physical stores. The factory has provisioned an EKS cluster in us-east-1 representing North American operations, with workloads that mirror AcmeRetail's published architecture at signal-shape level: a fake <ERP-vendor> ERP bridge, a fake Hybris-style commerce service, a fake order management service, a fake POS service per store, a fake WMS bridge, a fake recommendation agent, and a fake fit-assistant agent.

12 store entities are modeled, three of which represent stores-within-stores at named partner retailers. Two channels (D2C web, D2B AcmeRetail Company Gear) are active. Two regions are configured (US, EU) with the EU region quiet at this hour.

At T+4 minutes into the demo, the WMS bridge in the <HQ-city> metro fulfillment region begins timing out on inventory-availability checks. The <ERP-vendor> order-block queue starts backing up. Three Tractor Supply endcap syncs miss their windows. Two <HQ-city>-area physical store POS terminals slow down because they share the WMS bridge. D2C web at the customer-facing layer looks healthy but checkout-completion rates quietly drop because the cart's stock-validation step is hitting the same bridge.

At T+11 minutes the bridge recovers; queue drains; everything returns to baseline.

### 6.2 Business-buyer workflow (10-12 minutes)

Audience: a VP of Retail, a CFO, or similar. Cares about revenue, channels, regional performance.

1. **(0:00) Open the Proj Clarion app.** SE shows the AcmeRetail configuration and the freshly built dashboard set with a one-line: "we built this in 18 minutes from public information about your company."
2. **(0:30) Open the Business Health view.** Headline tiles: GMV today, GMV vs forecast, channel mix, top regions, top stores. Everything is green; revenue is running 8% above forecast.
3. **(1:30) Open the knowledge graph.** Show AcmeRetail as the root, expand to channels, expand North America to regions, expand <HQ-city> Metro to its stores. Click into "Store NA-1" and read the panel: today's revenue, conversion, customer count, plus a small badge showing technical health.
4. **(3:00) Ask Grafana Assistant.** Type: "Are any of my channels having a bad morning?" Assistant replies in plain English — D2C web is healthy, but checkout-completion is trending down over the last 20 minutes; three Tractor Supply endcaps have stopped reporting; two <HQ-city> stores are slow. Click the link Assistant provides.
5. **(5:00) Land on the Channel Pivot dashboard.** Shows the revenue-at-risk number ($230K projected over the next hour if it persists), broken down by channel. Business view, no Kubernetes language.
6. **(6:30) Click the "what's underneath" toggle.** The same dashboard now also shows the technical service that ties these channels together: the WMS bridge. The SE narrates: "for a CFO, this is where the conversation usually ends — you have a number, you have an owner."
7. **(8:30) Close with the alert.** Show the alert rule that fired at T+6 with a business-impact subject line: "$230K at risk: WMS bridge degraded, affecting 3 channels, 5 retail surfaces." Show the Slack/PagerDuty preview.
8. **(10:00) Final beat.** Open the AI Observability panel briefly: "by the way, your fit-assistant agent? We're tracking it the same way. As you go agentic, the same dashboard speaks both languages."

### 6.3 Technical-buyer workflow (10-12 minutes)

Audience: an SRE lead, a Platform Engineering director, an Observability architect. Cares about MTTR, signal correlation, breadth of coverage, openness.

1. **(0:00) Open the Technical Health view.** SLO grid: web latency p95, order-API success rate, queue depths, pod restarts. Most green; WMS bridge SLO burning.
2. **(1:00) Drill into the WMS bridge SLO.** Show error budget burn rate, timeline of the burn.
3. **(2:00) Pivot to traces.** TraceQL query for slow inventory checks. Show the slow span and the downstream timeout. Note the trace ID is also in the order-block queue records.
4. **(4:00) Logs in Loki.** Filter to the WMS bridge pod; show structured error logs. Note the linked trace IDs from the previous step.
5. **(5:30) Profiles in Pyroscope.** Show CPU profile from the bridge during the incident; identify the slow XML parser frame as the proximate cause.
6. **(7:00) Show the alert that fired and its routing.** Same alert from the business demo, but here we show the runbook link, the on-call routing, the labels that drove the routing decision.
7. **(8:30) AI Observability detour.** Show `gen_ai.*` spans from the recommendation agent. Tokens per request, time-to-first-token by model, an example trace with prompt and completion. Note that this works because everything is OTel; no proprietary agent.
8. **(10:00) Close on openness.** Show the Alloy config — that's all that's running on the cluster collecting data. No proprietary agent. Same OTel collectors that work with their existing stack.

### 6.4 What's deliberately not in either flow

- **No claim that AcmeRetail's real systems look like this.** Every panel carries the disclaimer.
- **No mention of AcmeRetail's incumbents.** The demo doesn't bash Dynatrace; it shows what's possible. SE addresses competitive questions in conversation.
- **No agentic content that could read as production guidance.** The fit-assistant and recommendation agent are clearly synthetic.
- **No live links into AcmeRetail assets.** Logos, brand colors, and copy are toggled off by default and only enabled with explicit opt-in by the SE for that session.

## 7. Phasing

A v0 to v1 path that gets to a usable Proj Clarion without trying to do everything at once.

| Phase | Scope | Exit criteria |
|-------|-------|---------------|
| v0.1 — Schemas frozen | This document approved; Northwind and AcmeRetail CompanyProfile examples written by hand to validate the schema | Stakeholder sign-off; no schema changes for two weeks |
| v0.2 — Research agent | Python service that produces a CompanyProfile from a URL with full citation audit; no provisioning yet | Generates a AcmeRetail profile that an SE recognizes as accurate |
| v0.3 — Plan agent | Produces a DemoPlan from a CompanyProfile; SE review UI in plugin | An SE can approve a plan and see it stored |
| v0.4 — Generators only | Synthetic data into BigQuery and OTel stream into Grafana Cloud, no real K8s yet | AcmeRetail business dashboard renders from generated data |
| v0.5 — Ephemeral K8s | Terraform module provisions EKS, deploys workload Helm charts, Alloy collectors forward to Cloud | AcmeRetail technical dashboard renders from real cluster signals |
| v0.6 — Knowledge graph | KG schema lands in BigQuery; Node Graph panel works; Assistant tool definitions for KG | A VP-flavored question gets a KG-aware answer |
| v0.7 — Incident scripting | IncidentScript runs deterministically; alerts fire on cue | Both AcmeRetail demos run end-to-end on the dot |
| v1.0 — Polish and harden | Cost guardrails, audit logging, plugin packaging, SE training material | An SE who has not seen the tool can run the AcmeRetail demo from a 10-minute walkthrough |

## 8. Open questions

These do not block schema sign-off but need answers before v0.4 — v0.5.

1. Plugin distribution. Public Grafana plugin catalog, or private Grafana Cloud distribution to specific stacks only? This affects the security review and the legal review.
2. Cloud account ownership. Will the central orchestration account be an existing Grafana Labs account, a dedicated proj-clarion account, or a new sub-account? Budget, IAM, and audit-trail implications differ.
3. Branding policy. Do we run real-customer demos with the customer's name in the title bar by default, or do we default to "Demo Co." and require an SE override? My recommendation: default to "Demo Co.", require SE override with a logged justification.
4. Data retention. How long do BigQuery datasets and Grafana folders persist after the demo? Suggested default: 7 days, then auto-delete. Override available.
5. Repeatability. Can the same demo be re-run later for the same prospect? If so, do we keep the CompanyProfile and re-derive a fresh DemoPlan, or do we snapshot the entire state? Recommendation: snapshot.

## 9. What I am asking for

Sign-off on:

- The five locked decisions (section 3) and the non-goals (section 2).
- The four schemas at structural level (section 5).
- The two AcmeRetail demo workflows (section 6) — narratively, before any code.
- The phasing plan (section 7) as an ordering, not a calendar.

Once those are signed off, the next deliverable is a detailed schema specification with field-level definitions and a runnable CompanyProfile validator, followed by the Research agent.
