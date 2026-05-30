/**
 * About / Architecture page.
 *
 * Purpose: a single page an SE can flip to during a customer demo to
 * explain what Proj Clarion IS, how it's built, and — most importantly
 * for AI-observability demos — exactly which signals it emits and where
 * they land in Grafana Cloud.
 *
 * The page is composed of six sections, each rendered as its own card:
 *
 *   1. Project pitch         — one-paragraph "what does this do"
 *   2. Architecture diagram  — SVG block-and-arrow of the runtime components
 *   3. Pipeline phases       — horizontal timeline of the six-phase build
 *   4. Data model            — table of the Postgres tables we own
 *   5. Observability stack   — what's instrumented + where it lands
 *   6. AI-obs demo flow      — regulated-buyer walkthrough of "show me what
 *                              Grafana sees when a Claude-SDK app runs"
 *
 * Lives at /about (also linked from the UserMenu and the mobile nav drawer).
 */
import {
  Boxes, Database, Activity, Sparkles, ClipboardList,
  Network, Rocket, GitBranch, Eye, BookOpen,
} from "lucide-react";
import { Link } from "react-router-dom";

import { Card } from "@/components/Card";
import { Badge } from "@/components/Badge";
import { cn } from "@/lib/cn";

export function AboutPage() {
  return (
    <div className="space-y-6">
      <PageHeader />
      <ProjectPitchCard />
      <ArchitectureDiagramCard />
      <PipelinePhasesCard />
      <DataModelCard />
      <ObservabilityStackCard />
      <DemoFlowCard />
    </div>
  );
}

// ──────────────────────────────────────────────────────────────────
// Header — matches the Plan + Profile detail-page rhythm.
// ──────────────────────────────────────────────────────────────────

function PageHeader() {
  return (
    <div>
      <div className="text-[11px] font-mono uppercase tracking-[0.08em] text-[var(--color-text-faint)]">
        About this project
      </div>
      <h1 className="mt-1 text-[32px] font-medium tracking-tight leading-tight">
        Proj Clarion <span className="h1-display">architecture</span>.
      </h1>
      <p className="mt-3 text-[var(--color-text-muted)] text-[15px] leading-relaxed max-w-3xl">
        A vertical-aware demo-data generator for Grafana Cloud business
        observability. Type a prospect URL → research → plan → generate →
        provision dashboards + alerts → emit live telemetry into your stack.
        Every LLM call is instrumented for AI observability via Grafana
        Sigil + OpenTelemetry.
      </p>
      <div className="mt-4 flex flex-wrap gap-2">
        <Link
          to="/docs/ai-obs"
          className="inline-flex items-center gap-1.5 rounded-md border border-[var(--color-border)] bg-[var(--color-canvas-elev1)] px-3 py-1.5 text-xs font-medium hover:bg-[var(--color-accent-bg)] hover:border-[var(--color-accent-border)] transition-colors"
        >
          <BookOpen size={12} />
          Instrument your own Claude app
        </Link>
      </div>
    </div>
  );
}

// ──────────────────────────────────────────────────────────────────
// 1. Project pitch
// ──────────────────────────────────────────────────────────────────

function ProjectPitchCard() {
  const pillars: { icon: typeof Boxes; label: string; body: string }[] = [
    {
      icon: Sparkles,
      label: "Research-driven",
      body: "Anthropic Claude reads the prospect's URL + SEC 10-K + job boards + GitHub + Wikidata, then synthesises a vertical-tuned company profile.",
    },
    {
      icon: ClipboardList,
      label: "Plan-driven",
      body: "An archetype-aware planner produces business processes, dashboards, alerts, and an incident script tailored to the company's actual shape.",
    },
    {
      icon: Activity,
      label: "Live telemetry",
      body: "Once provisioned, an entity emitter pushes synthetic-but-realistic OTel metrics, logs, and traces into Mimir/Loki/Tempo for live demos.",
    },
  ];
  return (
    <Card className="p-6">
      <SectionHeader icon={Boxes} title="What this is" />
      <p className="text-[var(--color-text-muted)] text-sm leading-relaxed mt-3 max-w-3xl">
        Proj Clarion takes a prospect URL and produces an end-to-end
        Grafana Cloud business observability demo — researched, planned,
        and live in the SE&rsquo;s stack within minutes. The whole pipeline
        is itself an agentic AI system, which makes it a clean reference
        target for demoing <strong>AI observability of 3rd-party Claude
        SDK applications</strong>.
      </p>
      <div className="grid grid-cols-1 md:grid-cols-3 gap-3 mt-5">
        {pillars.map((p) => (
          <div
            key={p.label}
            className="rounded-lg border border-[var(--color-border)] bg-[var(--color-canvas-elev1)] p-4"
          >
            <div className="flex items-center gap-2 mb-2">
              <p.icon size={14} className="text-[var(--color-accent)]" />
              <span className="text-xs font-mono uppercase tracking-wider text-[var(--color-text-muted)]">
                {p.label}
              </span>
            </div>
            <p className="text-xs text-[var(--color-text)] leading-relaxed">{p.body}</p>
          </div>
        ))}
      </div>
    </Card>
  );
}

// ──────────────────────────────────────────────────────────────────
// 2. Architecture diagram — SVG block-and-arrow
// ──────────────────────────────────────────────────────────────────

function ArchitectureDiagramCard() {
  return (
    <Card className="p-6">
      <SectionHeader icon={Network} title="Runtime architecture" />
      <p className="text-xs text-[var(--color-text-muted)] mt-2 max-w-3xl">
        FastAPI backend, React 19 UI, Postgres for persistence. Three external
        cloud-shaped destinations: <strong>Anthropic</strong> for LLM calls,
        <strong> Grafana Cloud</strong> as the obs target, <strong>Grafana
        Sigil</strong> for AI-observability of the LLM calls themselves.
      </p>
      <div className="mt-5 overflow-x-auto">
        <ArchitectureSVG />
      </div>
    </Card>
  );
}

/** SVG block diagram — no external lib, all SCSS-token colours so it
 *  re-themes light/dark automatically. Wide enough that on mobile it
 *  scrolls horizontally rather than cramping. */
function ArchitectureSVG() {
  return (
    <svg
      viewBox="0 0 980 460"
      className="w-full h-auto"
      style={{ minWidth: 880 }}
      aria-label="Proj Clarion architecture block diagram"
    >
      <defs>
        <marker
          id="arrow"
          viewBox="0 0 10 10"
          refX="9"
          refY="5"
          markerWidth="6"
          markerHeight="6"
          orient="auto-start-reverse"
        >
          <path d="M0,0 L10,5 L0,10 z" fill="var(--color-text-faint)" />
        </marker>
        <marker
          id="arrow-accent"
          viewBox="0 0 10 10"
          refX="9"
          refY="5"
          markerWidth="6"
          markerHeight="6"
          orient="auto-start-reverse"
        >
          <path d="M0,0 L10,5 L0,10 z" fill="var(--color-accent)" />
        </marker>
      </defs>

      {/* SE-actor */}
      <Block x={20}  y={30}  w={120} h={56} label="SE / Customer" sub="browser" tone="accent" />

      {/* UI tier */}
      <Block x={180} y={30}  w={160} h={56} label="React 19 + Vite UI" sub="tokens · tabs · stepper" tone="info" />

      {/* API tier */}
      <Block x={380} y={30}  w={180} h={56} label="FastAPI orchestrator" sub="/api/* · SSE · session_scope" tone="info" />

      {/* External agents row (LLM / Web / SEC etc.) */}
      <Block x={620} y={20}  w={170} h={48} label="Anthropic SDK" sub="opus + haiku · prompt caching" tone="grafana" />
      <Block x={620} y={78}  w={170} h={48} label="External sources" sub="EDGAR / GH / Greenhouse / Wikidata" tone="signal" />

      {/* Storage tier */}
      <Block x={380} y={140} w={180} h={56} label="Postgres" sub="profiles · plans · pipelines · KG" tone="success" />

      {/* Emitter */}
      <Block x={620} y={140} w={170} h={56} label="Entity emitter" sub="OTel metrics · logs · traces" tone="live" />

      {/* Clarion Assistant — the global agentic chat front-door. Same
          Anthropic SDK + tool loop as the pipeline; emits its own
          conversation / turn / tool spans to Grafana Cloud. */}
      <Block x={180} y={150} w={170} h={56} label="Clarion Assistant" sub="agentic chat · tools · approval" tone="accent" />

      {/* Grafana Cloud row */}
      <Block x={20}  y={260} w={150} h={56} label="Grafana Cloud" sub="Mimir · Loki · Tempo" tone="grafana" />
      <Block x={200} y={260} w={150} h={56} label="Grafana Asserts" sub="Knowledge graph" tone="grafana" />
      <Block x={380} y={260} w={150} h={56} label="Grafana dashboards" sub="Provisioned per plan" tone="grafana" />
      <Block x={560} y={260} w={150} h={56} label="Grafana alerts" sub="Provisioned per plan" tone="grafana" />

      {/* Sigil */}
      <Block x={740} y={260} w={210} h={56} label="Grafana Sigil (AI Obs)" sub="Generations · trace tree · cost" tone="accent" />

      {/* Demo session */}
      <Block x={380} y={380} w={400} h={56} label="Live demo session" sub="customer sees real telemetry flowing into Grafana Cloud" tone="live" />

      {/* Arrows */}
      <Arrow from={[140, 58]} to={[180, 58]} />
      <Arrow from={[340, 58]} to={[380, 58]} />
      <Arrow from={[560, 58]} to={[620, 44]} />
      <Arrow from={[560, 58]} to={[620, 102]} />
      <Arrow from={[470, 86]} to={[470, 140]} accent />
      <Arrow from={[560, 168]} to={[620, 168]} />
      <Arrow from={[705, 196]} to={[95, 260]} accent />
      <Arrow from={[705, 196]} to={[275, 260]} accent />
      <Arrow from={[470, 196]} to={[455, 260]} />
      <Arrow from={[470, 196]} to={[635, 260]} />
      <Arrow from={[705, 68]} to={[845, 260]} accent />
      <Arrow from={[95, 316]} to={[490, 380]} accent />
      <Arrow from={[635, 316]} to={[580, 380]} accent />

      {/* Clarion Assistant wiring: UI opens it, it calls Claude + tools,
          persists to Postgres, and ships conversation/tool spans to Tempo. */}
      <Arrow from={[260, 86]}  to={[260, 150]} accent />
      <Arrow from={[350, 162]} to={[620, 46]}  accent />
      <Arrow from={[350, 178]} to={[380, 178]} />
      <Arrow from={[200, 206]} to={[95, 260]}  accent />

      {/* Section labels — light grey, just visual orientation */}
      <text x={20}  y={20} fontSize="10" fill="var(--color-text-faint)" fontFamily="var(--font-mono)" letterSpacing="0.08em">
        CLIENT
      </text>
      <text x={380} y={130} fontSize="10" fill="var(--color-text-faint)" fontFamily="var(--font-mono)" letterSpacing="0.08em">
        SERVER + STORAGE
      </text>
      <text x={20}  y={250} fontSize="10" fill="var(--color-text-faint)" fontFamily="var(--font-mono)" letterSpacing="0.08em">
        GRAFANA CLOUD
      </text>
      <text x={380} y={370} fontSize="10" fill="var(--color-text-faint)" fontFamily="var(--font-mono)" letterSpacing="0.08em">
        OUTPUT
      </text>
    </svg>
  );
}

type Tone = "accent" | "info" | "success" | "live" | "signal" | "grafana";

const TONE_COLOURS: Record<Tone, { fill: string; stroke: string; text: string }> = {
  accent:  { fill: "var(--color-accent-bg)",   stroke: "var(--color-accent-border)",       text: "var(--color-accent)"  },
  info:    { fill: "var(--color-info-bg)",     stroke: "var(--color-info)",                text: "var(--color-info)"    },
  success: { fill: "var(--color-success-bg)",  stroke: "var(--color-success)",             text: "var(--color-success)" },
  live:    { fill: "var(--color-live-bg)",     stroke: "var(--color-live)",                text: "var(--color-live)"    },
  signal:  { fill: "var(--color-signal-bg)",   stroke: "var(--color-signal)",              text: "var(--color-signal)"  },
  grafana: { fill: "var(--color-grafana-bg)",  stroke: "var(--color-grafana-border)",      text: "var(--color-grafana)" },
};

function Block({
  x, y, w, h, label, sub, tone,
}: {
  x: number; y: number; w: number; h: number;
  label: string; sub?: string; tone: Tone;
}) {
  const c = TONE_COLOURS[tone];
  return (
    <g>
      <rect
        x={x} y={y} width={w} height={h} rx={10}
        fill={c.fill} stroke={c.stroke} strokeWidth={1}
      />
      <text
        x={x + w / 2} y={y + (sub ? 22 : h / 2 + 4)}
        textAnchor="middle"
        fontSize="12.5"
        fontWeight="500"
        fill={c.text}
      >
        {label}
      </text>
      {sub && (
        <text
          x={x + w / 2} y={y + 40}
          textAnchor="middle"
          fontSize="10"
          fontFamily="var(--font-mono)"
          fill="var(--color-text-muted)"
        >
          {sub}
        </text>
      )}
    </g>
  );
}

function Arrow({
  from, to, accent,
}: { from: [number, number]; to: [number, number]; accent?: boolean }) {
  return (
    <line
      x1={from[0]} y1={from[1]} x2={to[0]} y2={to[1]}
      stroke={accent ? "var(--color-accent)" : "var(--color-text-faint)"}
      strokeWidth={accent ? 1.5 : 1}
      strokeDasharray={accent ? undefined : "4 3"}
      markerEnd={accent ? "url(#arrow-accent)" : "url(#arrow)"}
      opacity={accent ? 0.8 : 0.5}
    />
  );
}

// ──────────────────────────────────────────────────────────────────
// 3. Pipeline phases timeline
// ──────────────────────────────────────────────────────────────────

const PHASES: { id: string; title: string; sub: string; agent?: string }[] = [
  { id: "1", title: "Research",   sub: "Read URL + 5 external sources; synthesise CompanyProfile",         agent: "Claude opus + 5× haiku" },
  { id: "2", title: "Plan",       sub: "Pick archetype; build KG + dashboards + alerts + incident",       agent: "Claude opus" },
  { id: "3", title: "Approve",    sub: "SE reviews; state transitions audited per plan + per profile" },
  { id: "4", title: "Generate",   sub: "Emitter spins up; synthetic OTel data flowing to Grafana Cloud" },
  { id: "5", title: "Provision",  sub: "Push dashboards + alert rules to Grafana Cloud via API" },
  { id: "6", title: "KG publish", sub: "Push knowledge graph nodes + edges to Grafana Asserts" },
];

function PipelinePhasesCard() {
  return (
    <Card className="p-6">
      <SectionHeader icon={GitBranch} title="Pipeline phases" />
      <p className="text-xs text-[var(--color-text-muted)] mt-2">
        Six sequential phases. Each phase emits SSE events; the orchestrator
        wraps the whole pipeline in a parent OTel span so each build shows up
        as a single trace in Grafana Cloud Tempo with one child span per phase.
      </p>
      <ol className="mt-5 grid grid-cols-1 md:grid-cols-3 lg:grid-cols-6 gap-2">
        {PHASES.map((p) => (
          <li
            key={p.id}
            className="rounded-lg border border-[var(--color-border)] bg-[var(--color-canvas-elev1)] p-3 flex flex-col"
          >
            <div className="text-[10px] font-mono text-[var(--color-text-faint)]">{p.id}</div>
            <div className="text-sm font-medium mt-1">{p.title}</div>
            <div className="text-[11px] text-[var(--color-text-muted)] mt-1 leading-relaxed flex-1">
              {p.sub}
            </div>
            {p.agent && (
              <div className="mt-2 inline-flex items-center gap-1 text-[10px] font-mono text-[var(--color-accent)]">
                <Sparkles size={9} /> {p.agent}
              </div>
            )}
          </li>
        ))}
      </ol>
    </Card>
  );
}

// ──────────────────────────────────────────────────────────────────
// 4. Data model
// ──────────────────────────────────────────────────────────────────

const TABLES: { name: string; purpose: string; group: "core" | "build" | "audit" | "kg" | "demo" | "aiobs" }[] = [
  { name: "company_profiles",      purpose: "Researched CompanyProfile JSON (stable per company)",            group: "core"  },
  { name: "demo_plans",            purpose: "DemoPlan JSON (KG + dashboards + alerts + incident script)",     group: "core"  },
  { name: "pipelines",             purpose: "One row per build run; status + linked profile/plan",            group: "build" },
  { name: "pipeline_phases",       purpose: "Denormalised phase rollup (status/started/finished per phase)",  group: "build" },
  { name: "pipeline_events",       purpose: "Append-only SSE event log; source of truth for replays",          group: "build" },
  { name: "kg_nodes",              purpose: "Knowledge-graph nodes (Company → BU → … by archetype)",          group: "kg"    },
  { name: "kg_edges",              purpose: "Edges between KG nodes (ownership, dependency, lineage)",        group: "kg"    },
  { name: "business_events",       purpose: "Generated synthetic events the emitter ships to Loki/Tempo",     group: "kg"    },
  { name: "plan_audit_log",        purpose: "Every plan state transition (draft → approved → provisioned)",   group: "audit" },
  { name: "profile_audit_log",     purpose: "Profile extends + claim accept/dismiss decisions",                group: "audit" },
  { name: "demo_sessions",         purpose: "Live emitter sessions (heartbeat, expires_at, status)",          group: "demo"  },
  { name: "llm_calls",             purpose: "One row per Anthropic call — tokens, cost, cache savings, TTFT, phase, is_stream",     group: "aiobs" },
  { name: "llm_evals",             purpose: "Structural evals per phase (source_count, kg_node_count, no_hallucinated_services, …)", group: "aiobs" },
  { name: "agent_tool_calls",      purpose: "Per-tool-call audit (web_search · db_read · kg_write · api_call · …) with target_system + action + duration + success",   group: "aiobs" },
  { name: "agent_policy_violations", purpose: "Guardrail trips: cost_spike · output_too_long · high_attempt_count · prompt_injection · unexpected_tool",                  group: "aiobs" },
  { name: "system_health",         purpose: "60s heartbeat per external dep (postgres · anthropic · grafana_cloud · serper); 7-day inline retention sweep",              group: "aiobs" },
];

function DataModelCard() {
  return (
    <Card className="p-6">
      <SectionHeader icon={Database} title="Data model · Postgres" />
      <p className="text-xs text-[var(--color-text-muted)] mt-2 max-w-3xl">
        Raw-SQL migrations via a small in-house runner — no Alembic. All
        artefacts are persisted as JSONB columns; SQL is intentionally narrow
        per repository class to keep the surface auditable. Every &nbsp;
        <code className="font-mono">apply_migrations()</code> run is
        idempotent (every CREATE uses <code className="font-mono">IF NOT EXISTS</code>).
      </p>
      <div className="overflow-x-auto mt-4">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left border-b border-[var(--color-border)]">
              <th className="py-2 pr-3 font-mono text-[10px] uppercase tracking-wider text-[var(--color-text-faint)]">
                Table
              </th>
              <th className="py-2 pr-3 font-mono text-[10px] uppercase tracking-wider text-[var(--color-text-faint)]">
                Group
              </th>
              <th className="py-2 font-mono text-[10px] uppercase tracking-wider text-[var(--color-text-faint)]">
                Purpose
              </th>
            </tr>
          </thead>
          <tbody>
            {TABLES.map((t) => (
              <tr key={t.name} className="border-b border-[var(--color-border)] last:border-0">
                <td className="py-2 pr-3 font-mono text-xs text-[var(--color-text)]">{t.name}</td>
                <td className="py-2 pr-3">
                  <Badge tone={tableTone(t.group)}>{t.group}</Badge>
                </td>
                <td className="py-2 text-xs text-[var(--color-text-muted)]">{t.purpose}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

function tableTone(g: string): "accent" | "info" | "success" | "warning" | "neutral" {
  return (
    g === "core"  ? "accent"
  : g === "build" ? "info"
  : g === "kg"    ? "success"
  : g === "audit" ? "warning"
  : g === "aiobs" ? "accent"
  : "neutral"
  );
}

// ──────────────────────────────────────────────────────────────────
// 5. Observability stack
// ──────────────────────────────────────────────────────────────────

const OBS_LAYERS: {
  layer: string;
  emits: string;
  exporter: string;
  lands_in: string;
  status: "wired" | "partial" | "todo";
}[] = [
  {
    layer:   "llm_client wrapper (custom)",
    emits:   "gen_ai.chat spans with Gen AI semantic-convention attrs + clarion.pipeline.phase / prompt.template / cost / cache_savings / context_utilization. As of v0.8.0 every call streams internally so TTFT (gen_ai.ttft_ms + clarion.llm.ttft_ms) is captured on every span, not just SE-facing endpoints.",
    exporter: "OTLP/HTTP to Grafana Cloud",
    lands_in: "Tempo",
    status:  "wired",
  },
  {
    layer:   "OpenLIT auto-instrumentation",
    emits:   "gen_ai.* spans + token/latency metrics (transport-level)",
    exporter: "OTLP/HTTP to Grafana Cloud",
    lands_in: "Tempo + Mimir",
    status:  "wired",
  },
  {
    layer:   "Cost persistence (llm_calls)",
    emits:   "Per-call DB row: tokens · cost_usd · cache_savings_usd · ttft_ms · error_type · stop_reason",
    exporter: "Postgres (queryable via the API)",
    lands_in: "llm_calls table",
    status:  "wired",
  },
  {
    layer:   "Structural evals (llm_evals)",
    emits:   "Per-phase eval rows + clarion.eval span events (source_count_ge_3, kg_node_count_ge_5, no_hallucinated_services, …)",
    exporter: "Postgres + span events",
    lands_in: "llm_evals table + Tempo",
    status:  "wired",
  },
  {
    layer:   "Tool spans (execute_tool)",
    emits:   "gen_ai.tool.name + gen_ai.agent.name + gen_ai.provider.name on every external call (web_fetch, edgar_fetch, github_org_fetch, greenhouse_fetch, lever_fetch, wikidata_fetch, dashboard_provision, alert_provision, kg_model_rules_push, kg_prom_rules_push, kg_entity_emitter_start)",
    exporter: "OTLP/HTTP to Grafana Cloud + agent_tool_calls row in Postgres",
    lands_in: "Tempo + AI Obs Tools view + agent_tool_calls table",
    status:  "wired",
  },
  {
    layer:   "Clarion Assistant spans",
    emits:   "assistant.conversation span groups each chat turn; the LLM rounds nest as gen_ai.chat spans (agent_name=clarion.assistant); every tool runs under an execute_tool {name} span carrying gen_ai.tool.name + gen_ai.tool.call.id + clarion.assistant.tool.mutating / .is_error / .declined. Same per-call cost rows in llm_calls as the build phases, so a chat session reads exactly like a build trace.",
    exporter: "OTLP/HTTP to Grafana Cloud + llm_calls row in Postgres",
    lands_in: "Tempo + AI Obs + llm_calls table",
    status:  "wired",
  },
  {
    layer:   "Policy detectors (agent_policy_violations)",
    emits:   "Auto-detected guardrail trips fired from llm_client._persist_call: cost_spike (>$0.50), output_too_long (>8K tokens), high_attempt_count (>3 retries), prompt_injection (pattern scan), unexpected_tool (allow-list). Each violation writes a postgres row AND emits a policy_violation span event so it surfaces in the AI-obs trace tree even if the DB is degraded.",
    exporter: "Postgres + span events",
    lands_in: "agent_policy_violations + Tempo",
    status:  "wired",
  },
  {
    layer:   "Tool-call audit (agent_tool_calls)",
    emits:   "track_tool_call context manager emits a tool.<name> span + writes one row per external-system call (web_search · db_read · kg_write · api_call · …) with target_system + action + duration_ms + success. Auto-pulls pipeline_id from llm_client ContextVars.",
    exporter: "Postgres + span",
    lands_in: "agent_tool_calls + Tempo",
    status:  "partial",
  },
  {
    layer:   "System health heartbeat (system_health)",
    emits:   "Lifespan asyncio task probes postgres / anthropic / grafana_cloud / serper every 60s. status=degraded when latency > 5000ms, status=down on exception. 7-day inline retention sweep on each tick — no separate cron. Surfaced via /api/health/services for dashboards + UI chips.",
    exporter: "Postgres",
    lands_in: "system_health table",
    status:  "wired",
  },
  {
    layer:   "Grafana Sigil",
    emits:   "Generation records (prompt, completion, parents, tags) with parent_generation_ids forming the planner DAG",
    exporter: "sigil-sdk via llm_client",
    lands_in: "Grafana Sigil (AI Obs)",
    status:  "wired",
  },
  {
    layer:   "Phase context propagation",
    emits:   "CLARION_PIPELINE_ID + CLARION_PIPELINE_PHASE env vars → ContextVar → stamped on every span",
    exporter: "subprocess env inheritance",
    lands_in: "every gen_ai span",
    status:  "wired",
  },
  {
    layer:   "deployment.environment + asserts.env",
    emits:   "Both Resource attrs default to `dev` (CLARION_ENVIRONMENT / CLARION_ASSERTS_ENV). Promote to `prod` together. KG entities, traces, metrics all roll up under the same env value.",
    exporter: "Resource on TracerProvider / MeterProvider + clarion_entity_info labels",
    lands_in: "Tempo + Mimir + Asserts KG (env scope)",
    status:  "wired",
  },
  {
    layer:   "App metrics (custom)",
    emits:   "clarion.pipeline.duration · clarion.archetype.classified · http.* / db.*",
    exporter: "OTLP/HTTP",
    lands_in: "Mimir",
    status:  "todo",
  },
  {
    layer:   "FastAPI + SQLAlchemy auto-instrument",
    emits:   "HTTP-server spans + db.* spans",
    exporter: "OTLP/HTTP",
    lands_in: "Tempo + Mimir",
    status:  "todo",
  },
  {
    layer:   "Structured logs → Loki",
    emits:   "structlog JSON (currently stdout)",
    exporter: "Alloy/Promtail not yet wired",
    lands_in: "Local terminal (Loki when configured)",
    status:  "partial",
  },
  {
    layer:   "Live demo emitter",
    emits:   "Customer-facing OTel metrics/logs/traces (synthetic business data) — NOT app observability",
    exporter: "OTLP/HTTP to SE's stack",
    lands_in: "SE's Grafana Cloud tenant",
    status:  "wired",
  },
];

function ObservabilityStackCard() {
  return (
    <Card className="p-6">
      <SectionHeader icon={Eye} title="Observability stack" />
      <p className="text-xs text-[var(--color-text-muted)] mt-2 max-w-3xl">
        Three signals flow out of Proj Clarion. Two are about <em>the demo</em>{" "}
        (the emitter pushing synthetic data into the SE&rsquo;s Grafana Cloud).
        The interesting one for AI-obs demos is <em>about Proj Clarion itself</em>{" "}
        — every Claude SDK call that drives the agent is observed via the{" "}
        <code className="font-mono text-[11px]">llm_client</code> wrapper, OpenLIT,
        and Sigil, all sharing the same TracerProvider and{" "}
        <code className="font-mono text-[11px]">deployment.environment</code>{" "}
        resource attribute.
      </p>
      <div className="overflow-x-auto mt-4">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left border-b border-[var(--color-border)]">
              <Th>Layer</Th>
              <Th>Emits</Th>
              <Th>Exporter</Th>
              <Th>Lands in</Th>
              <Th>Status</Th>
            </tr>
          </thead>
          <tbody>
            {OBS_LAYERS.map((o) => (
              <tr key={o.layer} className="border-b border-[var(--color-border)] last:border-0 align-top">
                <td className="py-2 pr-3 text-xs font-medium text-[var(--color-text)] whitespace-nowrap">{o.layer}</td>
                <td className="py-2 pr-3 text-xs text-[var(--color-text-muted)] leading-relaxed">{o.emits}</td>
                <td className="py-2 pr-3 text-xs font-mono text-[var(--color-text-faint)]">{o.exporter}</td>
                <td className="py-2 pr-3 text-xs text-[var(--color-text-muted)]">{o.lands_in}</td>
                <td className="py-2 pr-3">
                  <Badge
                    tone={
                      o.status === "wired"    ? "success" :
                      o.status === "partial"  ? "warning" :
                      "neutral"
                    }
                  >
                    {o.status}
                  </Badge>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Card>
  );
}

function Th({ children }: { children: React.ReactNode }) {
  return (
    <th className="py-2 pr-3 font-mono text-[10px] uppercase tracking-wider text-[var(--color-text-faint)]">
      {children}
    </th>
  );
}

// ──────────────────────────────────────────────────────────────────
// 6. AI-obs demo flow
// ──────────────────────────────────────────────────────────────────

const DEMO_STEPS: { num: string; title: string; body: string; lands: string }[] = [
  {
    num:   "01",
    title: "Trigger a build",
    body:  "From the hero card on the home page, type any prospect URL and click Build. This kicks off the 6-phase pipeline.",
    lands: "Backend SSE → live PipelineRunView",
  },
  {
    num:   "02",
    title: "Watch Grafana Sigil",
    body:  "Each Anthropic call lands in Sigil as a Generation. The research phase fans out to 1× opus + 5× haiku calls; switch to the AI Observability view in Grafana Cloud to see the parent-child DAG.",
    lands: "Grafana Cloud → AI Observability → Generations",
  },
  {
    num:   "03",
    title: "Cost + latency + TTFT in Mimir + Postgres",
    body:  "Every call carries `clarion.llm.cost_usd`, `clarion.llm.cache_savings_usd`, and `clarion.llm.ttft_ms` on the span and a matching row in `llm_calls`. Build a Cost-per-Build panel: `SELECT SUM(cost_usd) FROM llm_calls WHERE pipeline_id = $pid GROUP BY phase`. TTFT lights up the latency-per-model panel even without streaming endpoints because v0.8.0 streams every call internally.",
    lands: "Grafana Cloud → Tempo · Postgres → llm_calls",
  },
  {
    num:   "04",
    title: "Trace tree in Tempo",
    body:  "Each call is a `gen_ai.chat {model}` span tagged with `clarion.pipeline.phase` + `clarion.prompt.template`. Filter to a single build by `clarion.pipeline.id` and drill into the longest phase to see which template dominated. Policy violations attach as `policy_violation` span events on the same trace so guardrail trips are one click away from the cost outliers that caused them.",
    lands: "Grafana Cloud → Explore → Tempo",
  },
  {
    num:   "05",
    title: "Guardrails + dependency health",
    body:  "`agent_policy_violations` rolls up auto-detected cost_spike / output_too_long / prompt_injection / unexpected_tool trips per build — show the SQL panel filtered to `resolved = FALSE`. `system_health` shows the last 7 days of postgres / anthropic / grafana_cloud uptime so the customer can see the November-outage-style story would have been caught here.",
    lands: "Grafana Cloud panels reading agent_policy_violations + system_health via Postgres datasource",
  },
  {
    num:   "06",
    title: "Live demo telemetry",
    body:  "Approve the plan to fire the emitter. Synthetic business events (orders, payments, dealer-network throughput) start flowing into the SE's stack. This is the OUTPUT of the demo, not its observability.",
    lands: "Customer's Grafana Cloud tenant",
  },
];

function DemoFlowCard() {
  return (
    <Card className="p-6 border-[color:var(--color-accent-border)] bg-[var(--color-accent-bg)]/30">
      <SectionHeader icon={Rocket} title="Demo walkthrough — AI observability for 3rd-party Claude SDK" />
      <p className="text-xs text-[var(--color-text-muted)] mt-2 max-w-3xl">
        How to use Proj Clarion in an AI-observability demo: every step
        below produces signal in Grafana Cloud that the customer can see
        live. The three pillars (AWS Agentcore · Azure Copilot Agents ·{" "}
        <em>3rd-party Claude SDK</em>) are independent — this app
        demonstrates the third, alongside whatever native AWS/Azure surfaces
        the customer wants to show.
      </p>
      <ol className="mt-5 space-y-3">
        {DEMO_STEPS.map((s) => (
          <li
            key={s.num}
            className="flex items-start gap-4 p-3 rounded-lg bg-[var(--color-canvas-elev1)] border border-[var(--color-border)]"
          >
            <span className="font-mono text-2xl tabular-nums text-[var(--color-accent)] shrink-0">
              {s.num}
            </span>
            <div className="min-w-0 flex-1">
              <div className="text-sm font-medium text-[var(--color-text)]">{s.title}</div>
              <p className="text-xs text-[var(--color-text-muted)] mt-1 leading-relaxed">{s.body}</p>
              <div className="text-[10px] font-mono uppercase tracking-wider text-[var(--color-text-faint)] mt-2">
                lands → <span className="text-[var(--color-accent)]">{s.lands}</span>
              </div>
            </div>
          </li>
        ))}
      </ol>
    </Card>
  );
}

// ──────────────────────────────────────────────────────────────────
// Shared section header
// ──────────────────────────────────────────────────────────────────

function SectionHeader({
  icon: Icon, title,
}: {
  icon: typeof Boxes; title: string;
}) {
  return (
    <div className={cn("flex items-center gap-2")}>
      <Icon size={16} className="text-[var(--color-accent)]" />
      <h2 className="text-base font-medium tracking-tight m-0">{title}</h2>
    </div>
  );
}
