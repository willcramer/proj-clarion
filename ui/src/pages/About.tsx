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
import { useEffect, useLayoutEffect, useRef, useState, type CSSProperties } from "react";
import {
  Boxes, Database, Activity, Sparkles, ClipboardList,
  Network, Rocket, GitBranch, Eye, BookOpen,
  Globe, Layers, Zap, Cpu, Gauge, LayoutGrid, Bell, FlaskConical, Maximize2, X,
  type LucideIcon,
} from "lucide-react";
import type { IconType } from "react-icons";
import {
  SiReact, SiFastapi, SiAnthropic, SiClaude, SiPostgresql,
  SiOpentelemetry, SiGrafana,
} from "react-icons/si";
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
// 2. Architecture diagram — faithful port of the Claude-design runtime
//    architecture export. Node cards (HTML, absolutely positioned) are
//    layered over an SVG edge/zone canvas; the whole stage scales to fit
//    its container (width AND a height cap, so it fits one viewport).
//    "Expand" opens a fullscreen view. Generic left-square icons are
//    lucide; the inline brand mark beside each name is a Simple Icons
//    logo (react-icons), rendered in a flex row so Tailwind's
//    `svg{display:block}` preflight can't drop it onto its own line.
//    All colours are theme tokens so it re-themes light/dark.
// ──────────────────────────────────────────────────────────────────

function ArchitectureDiagramCard() {
  const [expanded, setExpanded] = useState(false);

  useEffect(() => {
    if (!expanded) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setExpanded(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [expanded]);

  return (
    <Card className="p-6">
      <div className="flex items-start justify-between gap-3">
        <SectionHeader icon={Network} title="Runtime architecture" />
        <button
          type="button"
          onClick={() => setExpanded(true)}
          className="inline-flex shrink-0 items-center gap-1.5 rounded-md border border-[var(--color-border)] bg-[var(--color-canvas-elev1)] px-2.5 py-1 text-xs font-medium text-[var(--color-text-muted)] transition-colors hover:border-[var(--color-accent-border)] hover:bg-[var(--color-accent-bg)] hover:text-[var(--color-accent)]"
        >
          <Maximize2 size={12} /> Expand
        </button>
      </div>
      <p className="text-xs text-[var(--color-text-muted)] mt-2 max-w-3xl leading-relaxed">
        FastAPI backend, React 19 UI, Postgres for persistence. Three external
        cloud-shaped destinations: <strong>Anthropic</strong> for LLM calls,{" "}
        <strong>Grafana Cloud</strong> as the observability target, and{" "}
        <strong>Grafana Sigil</strong> for AI-observability of the LLM calls
        themselves.
      </p>
      <ArchLegend />
      <RuntimeArchitecture maxHeight={440} />

      {expanded && (
        <div
          className="fixed inset-0 z-[100] flex flex-col bg-[var(--color-canvas)]"
          onClick={() => setExpanded(false)}
        >
          <div className="flex items-center justify-between border-b border-[var(--color-border)] px-5 py-3">
            <span className="text-sm font-medium text-[var(--color-text)]">
              Runtime architecture
            </span>
            <button
              type="button"
              onClick={() => setExpanded(false)}
              className="inline-flex items-center gap-1.5 rounded-md border border-[var(--color-border)] bg-[var(--color-canvas-elev1)] px-2.5 py-1 text-xs font-medium text-[var(--color-text)] transition-colors hover:border-[var(--color-accent-border)] hover:bg-[var(--color-accent-bg)] hover:text-[var(--color-accent)]"
            >
              <X size={13} /> Close
            </button>
          </div>
          <div className="flex flex-1 flex-col justify-center overflow-auto px-6 pb-6" onClick={(e) => e.stopPropagation()}>
            <RuntimeArchitecture maxHeight={typeof window !== "undefined" ? window.innerHeight - 116 : 760} />
          </div>
        </div>
      )}
    </Card>
  );
}

const ARCH_W = 1340;
const ARCH_H = 950;

const ARCH_C = {
  info: "var(--color-info)",
  accent: "var(--color-accent)",
  live: "var(--color-live)",
  amber: "var(--color-warning)",
  grafana: "var(--color-grafana)",
} as const;

type ArchNode = {
  x: number; y: number; w: number; h: number; c: string;
  Icon: LucideIcon; t: string; s: string; brand?: IconType; big?: boolean;
};

const ARCH_NODES: ArchNode[] = [
  { x: 60,   y: 110, w: 210, h: 78, c: ARCH_C.live,    Icon: Globe,        t: "SE / Customer",     s: "browser" },
  { x: 330,  y: 110, w: 230, h: 78, c: ARCH_C.info,    Icon: Layers,       t: "React + Vite",      s: "React 19 · Tailwind 4",          brand: SiReact },
  { x: 330,  y: 265, w: 250, h: 78, c: ARCH_C.accent,  Icon: Sparkles,     t: "Clarion Assistant", s: "agentic chat · tools",           brand: SiClaude },
  { x: 620,  y: 265, w: 250, h: 78, c: ARCH_C.info,    Icon: Zap,          t: "FastAPI",           s: "Python 3.11 · SSE",              brand: SiFastapi },
  { x: 330,  y: 440, w: 250, h: 78, c: ARCH_C.accent,  Icon: Activity,     t: "Entity emitter",    s: "OTel SDK · OpenLIT",             brand: SiOpentelemetry },
  { x: 620,  y: 440, w: 250, h: 78, c: ARCH_C.accent,  Icon: Database,     t: "PostgreSQL 16",     s: "plans · pipelines · KG",     brand: SiPostgresql },
  { x: 1010, y: 110, w: 280, h: 78, c: ARCH_C.amber,   Icon: Cpu,          t: "Anthropic SDK",     s: "opus-4 · haiku-4",               brand: SiAnthropic },
  { x: 1010, y: 265, w: 280, h: 78, c: ARCH_C.info,    Icon: Globe,        t: "External sources",  s: "EDGAR · GitHub · Wikidata" },
  { x: 60,   y: 620, w: 230, h: 82, c: ARCH_C.grafana, Icon: Gauge,        t: "Grafana Cloud",     s: "Mimir · Loki · Tempo",       brand: SiGrafana },
  { x: 300,  y: 620, w: 230, h: 82, c: ARCH_C.grafana, Icon: Network,      t: "Asserts",           s: "knowledge graph",                     brand: SiGrafana },
  { x: 540,  y: 620, w: 230, h: 82, c: ARCH_C.grafana, Icon: LayoutGrid,   t: "Dashboards",        s: "per plan",                            brand: SiGrafana },
  { x: 780,  y: 620, w: 230, h: 82, c: ARCH_C.grafana, Icon: Bell,         t: "Alerts",            s: "per plan",                            brand: SiGrafana },
  { x: 1020, y: 620, w: 270, h: 82, c: ARCH_C.grafana, Icon: Eye,          t: "Sigil — AI Obs.", s: "generations · trace · cost", brand: SiGrafana },
  { x: 380,  y: 820, w: 580, h: 96, c: ARCH_C.accent,  Icon: FlaskConical, t: "Live demo session", s: "customer sees real telemetry in Grafana Cloud", big: true },
];

type ArchEdge = { pts: [number, number][]; style: "solid" | "dash"; label?: string; at?: [number, number] };

const ARCH_EDGES: ArchEdge[] = [
  { pts: [[270,149],[330,149]], style: "dash" },
  { pts: [[468,188],[468,226],[720,226],[720,265]], style: "dash" },
  { pts: [[399,188],[399,242],[455,242],[455,265]], style: "solid", label: "chat", at: [399,253] },
  { pts: [[542,265],[542,212],[1150,212],[1150,188]], style: "solid", label: "LLM", at: [860,212] },
  { pts: [[870,300],[940,300],[940,149],[1010,149]], style: "dash", label: "research · plan", at: [940,237] },
  { pts: [[870,325],[960,325],[960,304],[1010,304]], style: "dash" },
  { pts: [[745,343],[745,440]], style: "solid", label: "persist", at: [745,392] },
  { pts: [[620,479],[580,479]], style: "dash", label: "read plan", at: [600,479] },
  { pts: [[430,518],[430,575],[175,575],[175,620]], style: "solid", label: "telemetry", at: [300,575] },
  { pts: [[700,518],[700,560],[415,560],[415,620]], style: "dash" },
  { pts: [[745,518],[745,588],[655,588],[655,620]], style: "dash", label: "provision", at: [700,601] },
  { pts: [[790,518],[790,572],[895,572],[895,620]], style: "dash" },
  { pts: [[1290,149],[1316,149],[1316,661],[1290,661]], style: "solid", label: "spans · cost", at: [1316,405] },
  { pts: [[175,702],[175,762],[500,762],[500,820]], style: "solid" },
  { pts: [[895,702],[895,772],[845,772],[845,820]], style: "solid" },
];

type ArchZone = { x: number; y: number; w: number; h: number; label: string; lx: number; ly: number; tint: string };

const ARCH_ZONES: ArchZone[] = [
  { x: 300, y: 88,  w: 600,  h: 478, label: "Clarion · app", lx: 314,  ly: 92,  tint: "var(--color-info)" },
  { x: 986, y: 86,  w: 322,  h: 262, label: "External",          lx: 1000, ly: 92,  tint: "var(--color-warning)" },
  { x: 40,  y: 596, w: 1268, h: 130, label: "Grafana Cloud",     lx: 54,   ly: 602, tint: "var(--color-grafana)" },
];

const ARCH_BANDS: { t: string; x: number; y: number }[] = [
  { t: "CLIENT", x: 60,  y: 90 },
  { t: "OUTPUT", x: 380, y: 800 },
];

/** Rounded orthogonal path through waypoints (quadratic corners). */
function archPath(pts: [number, number][], r = 15): string {
  if (pts.length < 2) return "";
  const P = pts.map((p) => ({ x: p[0], y: p[1] }));
  let d = `M ${P[0].x} ${P[0].y}`;
  for (let i = 1; i < P.length - 1; i++) {
    const p0 = P[i - 1], p1 = P[i], p2 = P[i + 1];
    const v1 = { x: p1.x - p0.x, y: p1.y - p0.y }, l1 = Math.hypot(v1.x, v1.y) || 1;
    const v2 = { x: p2.x - p1.x, y: p2.y - p1.y }, l2 = Math.hypot(v2.x, v2.y) || 1;
    const rr = Math.min(r, l1 / 2, l2 / 2);
    const a = { x: p1.x - (v1.x / l1) * rr, y: p1.y - (v1.y / l1) * rr };
    const b = { x: p1.x + (v2.x / l2) * rr, y: p1.y + (v2.y / l2) * rr };
    d += ` L ${a.x} ${a.y} Q ${p1.x} ${p1.y} ${b.x} ${b.y}`;
  }
  const last = P[P.length - 1];
  d += ` L ${last.x} ${last.y}`;
  return d;
}

function ArchLegend() {
  const swatch = (v: string): CSSProperties => ({
    width: 11, height: 11, borderRadius: 3, display: "inline-block",
    background: `color-mix(in srgb, ${v} 60%, transparent)`,
  });
  return (
    <div className="mt-4 flex flex-wrap items-center gap-x-5 gap-y-2 text-xs text-[var(--color-text-muted)]">
      <span className="inline-flex items-center gap-2">
        <svg width="34" height="10"><line x1="2" y1="5" x2="32" y2="5" stroke="var(--color-accent)" strokeWidth="2.5" /></svg>
        Data &amp; telemetry flow
      </span>
      <span className="inline-flex items-center gap-2">
        <svg width="34" height="10"><line x1="2" y1="5" x2="32" y2="5" stroke="var(--color-text-faint)" strokeWidth="2.5" strokeDasharray="5 4" /></svg>
        Request / read
      </span>
      <span className="inline-flex items-center gap-2"><span style={swatch("var(--color-info)")} /> App tier</span>
      <span className="inline-flex items-center gap-2"><span style={swatch("var(--color-accent)")} /> Clarion services</span>
      <span className="inline-flex items-center gap-2"><span style={swatch("var(--color-grafana)")} /> Grafana Cloud</span>
    </div>
  );
}

/** Node cards layered over the SVG edge/zone canvas, scaled to fit both
 *  the container width and an optional height cap. Labels render last so
 *  they sit on top of node cards (edge chips occlude borders cleanly). */
function RuntimeArchitecture({ maxHeight }: { maxHeight?: number }) {
  const scalerRef = useRef<HTMLDivElement>(null);
  const stageRef = useRef<HTMLDivElement>(null);

  useLayoutEffect(() => {
    const scaler = scalerRef.current;
    const stage = stageRef.current;
    if (!scaler || !stage) return;
    const fit = () => {
      const availW = scaler.clientWidth;
      let s = availW / ARCH_W;
      if (maxHeight) s = Math.min(s, maxHeight / ARCH_H);
      s = Math.min(s, 1);
      const usedW = ARCH_W * s;
      stage.style.transform = `scale(${s})`;
      stage.style.left = `${Math.max(0, (availW - usedW) / 2)}px`;
      scaler.style.height = `${ARCH_H * s}px`;
    };
    fit();
    const ro = new ResizeObserver(fit);
    ro.observe(scaler);
    return () => ro.disconnect();
  }, [maxHeight]);

  const zoneLabel = (lx: number, ly: number): CSSProperties => ({
    position: "absolute", left: lx, top: ly, lineHeight: 1,
    fontFamily: "var(--font-mono)", fontSize: 11, letterSpacing: "0.14em",
    textTransform: "uppercase", color: "var(--color-text-faint)",
    pointerEvents: "none",
  });

  return (
    <div ref={scalerRef} className="mt-5" style={{ position: "relative" }}>
      <div
        ref={stageRef}
        style={{ position: "absolute", top: 0, left: 0, width: ARCH_W, height: ARCH_H, transformOrigin: "top left" }}
      >
        {/* edge + zone canvas */}
        <svg width={ARCH_W} height={ARCH_H} style={{ position: "absolute", inset: 0, overflow: "visible" }}>
          <defs>
            <marker id="ah-a" markerWidth="9" markerHeight="9" refX="7" refY="4.5" orient="auto">
              <path d="M1 1 L8 4.5 L1 8 Z" fill="var(--color-accent)" />
            </marker>
            <marker id="ah-f" markerWidth="9" markerHeight="9" refX="7" refY="4.5" orient="auto">
              <path d="M1 1 L8 4.5 L1 8 Z" fill="var(--color-text-faint)" />
            </marker>
          </defs>
          {ARCH_ZONES.map((z) => (
            <rect
              key={z.label} x={z.x} y={z.y} width={z.w} height={z.h} rx={18}
              fill={`color-mix(in srgb, ${z.tint} 4%, transparent)`}
              stroke={`color-mix(in srgb, ${z.tint} 26%, transparent)`}
              strokeWidth={1} strokeDasharray="2 6"
            />
          ))}
          {ARCH_EDGES.map((e) => (
            <path
              key={e.pts.flat().join("_") + e.style} d={archPath(e.pts)} fill="none"
              stroke={e.style === "solid" ? "var(--color-accent)" : "var(--color-text-faint)"}
              strokeWidth={e.style === "solid" ? 2.4 : 1.8}
              strokeLinecap="round" strokeLinejoin="round"
              strokeDasharray={e.style === "dash" ? "5 5" : undefined}
              markerEnd={e.style === "solid" ? "url(#ah-a)" : "url(#ah-f)"}
              opacity={e.style === "solid" ? 1 : 0.85}
            />
          ))}
        </svg>

        {/* node cards */}
        {ARCH_NODES.map((n) => {
          const NodeIcon = n.Icon;
          const Brand = n.brand;
          return (
            <div
              key={n.t}
              style={{
                position: "absolute", left: n.x, top: n.y, width: n.w, height: n.h,
                boxSizing: "border-box", display: "flex", alignItems: "center", gap: 12,
                padding: "0 16px", borderRadius: 13,
                background: n.big
                  ? "color-mix(in srgb, var(--color-accent) 7%, var(--color-canvas-elev1))"
                  : "var(--color-canvas-elev1)",
                border: "1px solid var(--color-border)",
                borderLeft: `${n.big ? 4 : 3}px solid ${n.c}`,
                boxShadow: "var(--shadow-md)",
              }}
            >
              <span
                style={{
                  width: 30, height: 30, borderRadius: 8, display: "grid", placeItems: "center",
                  color: n.c, background: `color-mix(in srgb, ${n.c} 15%, transparent)`, flex: "none",
                }}
              >
                <NodeIcon size={17} strokeWidth={2} />
              </span>
              <div style={{ minWidth: 0 }}>
                <div
                  style={{
                    display: "flex", alignItems: "center", gap: 7, whiteSpace: "nowrap",
                    fontSize: n.big ? 16 : 14.5, fontWeight: 600, letterSpacing: "-0.01em",
                    color: "var(--color-text)",
                  }}
                >
                  {Brand && <Brand size={14} style={{ flex: "none", opacity: 0.9 }} />}
                  <span>{n.t}</span>
                </div>
                <div
                  style={{
                    fontFamily: "var(--font-mono)", fontSize: 11.5, color: "var(--color-text-faint)",
                    marginTop: 3, whiteSpace: "nowrap",
                  }}
                >
                  {n.s}
                </div>
              </div>
            </div>
          );
        })}

        {/* zone + band labels (on top of cards) */}
        {ARCH_ZONES.map((z) => (
          <div key={`zl-${z.label}`} style={zoneLabel(z.lx, z.ly)}>{z.label}</div>
        ))}
        {ARCH_BANDS.map((b) => (
          <div key={b.t} style={zoneLabel(b.x, b.y)}>{b.t}</div>
        ))}

        {/* edge labels (chips, on top so they occlude node borders cleanly) */}
        {ARCH_EDGES.filter((e) => e.label && e.at).map((e) => (
          <div
            key={`el-${e.label}`}
            style={{
              position: "absolute", left: e.at![0], top: e.at![1],
              transform: "translate(-50%,-50%)", pointerEvents: "none",
              fontFamily: "var(--font-mono)", fontSize: 10.5,
              color: e.style === "solid" ? "var(--color-accent)" : "var(--color-text-muted)",
              background: "var(--color-canvas)", padding: "2px 7px", borderRadius: 6,
              border: `1px solid ${e.style === "solid" ? "var(--color-accent-border)" : "var(--color-border)"}`,
              whiteSpace: "nowrap",
            }}
          >
            {e.label}
          </div>
        ))}
      </div>
    </div>
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
