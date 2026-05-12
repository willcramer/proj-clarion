/**
 * plan-derivations: project a real `PlanDoc` (the planner's JSON) into
 * the section-friendly shapes the new plan-detail components expect.
 *
 * The CLAUDE_CODE_PLAN_DETAIL_PROMPT.md spec asks for fields the
 * planner doesn't capture today: per-process SLO strings, telemetry
 * peakQps, etc. Rather than block on backend changes, we derive what
 * we can from the existing schema and surface "not captured" /
 * "not-instrumented" placeholders for the rest. When the backend
 * starts emitting real values, just swap the helpers in this file.
 */

// ─── Output shapes the components consume ─────────────────────────

export type ProcessTier =
  | "revenue" | "logistics" | "inventory" | "customer" | "risk" | "other";

export interface ProcessRow {
  id: string;
  name: string;
  description: string;
  tier: ProcessTier;
  latencySlo: string | null;
  errorSlo: string | null;
  signalCount: number;
  /** "not-instrumented" until we have per-process telemetry on the wire. */
  health: "healthy" | "warn" | "danger" | "not-instrumented";
  /** Raw business steps from the planner JSON. Shape varies (some
   *  vertical templates use {name, description}, others have richer
   *  metadata); the UI extracts what it can. */
  steps: unknown[];
  /** Raw failure modes, same caveat as `steps`. */
  failureModes: unknown[];
}

export interface DashboardItem {
  id: string;
  kind: "dashboard" | "alert";
  title: string;
  sub: string;
  severity: "hero" | "slo" | "p1" | "p2" | "info";
}

export interface TelemetryShape {
  /** Approx events per minute at peak — derived from the planner's
   *  volume_per_day when available, otherwise null. */
  peakQps: number | null;
  /** Baseline rate; null when not measured. */
  troughQps: number | null;
  /** Free-text label e.g. "diurnal, 2 peaks". */
  shape: string;
  /** Normalised 0..1 series of length 24 for the area chart. */
  series: number[];
}

export interface IncidentStop {
  t: string;
  title: string;
  sub: string;
  tone: "accent" | "warn" | "live" | "default";
}

export interface SampleDataSource {
  id: string;
  label: string;
  sub: string;
  volume: string;
}

// ─── Source PlanDoc fragments we read from ────────────────────────
// Kept loose so this module doesn't have to import the full PlanDoc
// type from Plans.tsx (which would create a circular reference once
// Plans.tsx imports back from here).

interface SourcePlanLike {
  plan_id: string;
  business_process_models: Array<{
    process_id: string;
    name: string;
    business_steps: unknown[];
    failure_modes: unknown[];
  }>;
  dashboard_specs: Array<{ dashboard_id: string; title: string; audience: string }>;
  alert_specs: Array<{ alert_id: string; severity: string; business_subject_line: string }>;
  incident_script: {
    title: string;
    total_duration_minutes: number;
    arming_mode: string;
    events: Array<{ event_type: string; target_id: string; offset_seconds: number }>;
  };
  knowledge_graph: { nodes: unknown[]; edges: unknown[] };
}

// ─── Per-section helpers ──────────────────────────────────────────

/** Coarse keyword match to assign a tier to a process. Real verticals
 *  use thousands of process names, so we keep this loose: anything
 *  with revenue/checkout/order keywords lands in "revenue"; anything
 *  shipping/warehouse hits "logistics"; etc. Falls back to "other". */
export function inferProcessTier(name: string): ProcessTier {
  const n = name.toLowerCase();
  if (/order|checkout|payment|revenue|sale|billing|invoice|cart/.test(n)) return "revenue";
  if (/ship|warehouse|fulfil|delivery|logistics|inventory|stock/.test(n)) return "logistics";
  if (/inventory|stock/.test(n)) return "inventory";
  if (/customer|account|signup|login|onboard|support/.test(n)) return "customer";
  if (/fraud|risk|compliance|audit|kyc/.test(n)) return "risk";
  return "other";
}

export function deriveProcesses(plan: SourcePlanLike): ProcessRow[] {
  return plan.business_process_models.map((p) => {
    const stepDesc = describeFromSteps(p.business_steps);
    return {
      id: p.process_id,
      name: p.name,
      description: stepDesc ?? "No description captured.",
      tier: inferProcessTier(p.name),
      latencySlo: null,
      errorSlo: null,
      signalCount: p.failure_modes.length,
      health: "not-instrumented",
      steps: p.business_steps,
      failureModes: p.failure_modes,
    };
  });
}

/** Best-effort label extraction for an arbitrary plan-JSON object.
 *  Used by ProcessesTable's expandable detail rows. Handles common
 *  keys the planner emits (name, label, description, title, mode).
 *  Falls back to a JSON-stringify for objects without a useful key,
 *  or to the raw value if the input is a primitive. */
export function extractLabel(item: unknown): { primary: string; secondary?: string } {
  if (item == null) return { primary: "—" };
  if (typeof item === "string") return { primary: item };
  if (typeof item === "number" || typeof item === "boolean") {
    return { primary: String(item) };
  }
  if (typeof item !== "object") return { primary: String(item) };
  const o = item as Record<string, unknown>;
  // First key that has a usable string value becomes the primary
  // label; the next non-empty string becomes the secondary.
  const candidates = ["name", "label", "title", "step_name", "mode", "id"];
  const subs = ["description", "summary", "detail", "note", "reason"];
  let primary: string | undefined;
  for (const k of candidates) {
    const v = o[k];
    if (typeof v === "string" && v.trim()) { primary = v.trim(); break; }
  }
  let secondary: string | undefined;
  for (const k of subs) {
    const v = o[k];
    if (typeof v === "string" && v.trim()) { secondary = v.trim(); break; }
  }
  if (!primary) {
    // No friendly key — show the first leaf string we find, or fall
    // back to a JSON snippet so the SE at least sees the data shape.
    for (const k of Object.keys(o)) {
      const v = o[k];
      if (typeof v === "string" && v.trim()) {
        primary = `${k}: ${v.trim()}`;
        break;
      }
    }
  }
  return {
    primary: primary ?? JSON.stringify(o).slice(0, 80),
    secondary,
  };
}

/** Pull a one-line description out of business_steps[0] when present.
 *  Planner JSON varies between shapes; we look for common label keys
 *  and fall back to null. */
function describeFromSteps(steps: unknown[]): string | null {
  if (!Array.isArray(steps) || steps.length === 0) return null;
  const first = steps[0] as Record<string, unknown>;
  for (const key of ["description", "name", "label", "step_name", "summary"]) {
    const v = first?.[key];
    if (typeof v === "string" && v.trim()) return v.trim();
  }
  return null;
}

export function deriveDashboardsAndAlerts(plan: SourcePlanLike): DashboardItem[] {
  const dashboards: DashboardItem[] = plan.dashboard_specs.map((d) => ({
    id: d.dashboard_id,
    kind: "dashboard",
    title: d.title,
    sub: d.audience,
    severity:
      d.audience?.toLowerCase().includes("exec")
        ? "hero"
        : d.audience?.toLowerCase().includes("oncall") || d.audience?.toLowerCase().includes("on-call")
          ? "slo"
          : "info",
  }));
  const alerts: DashboardItem[] = plan.alert_specs.map((a) => ({
    id: a.alert_id,
    kind: "alert",
    title: a.business_subject_line,
    sub: a.severity,
    severity:
      a.severity?.toLowerCase().includes("p1") || a.severity?.toLowerCase().includes("crit")
        ? "p1"
        : a.severity?.toLowerCase().includes("p2") || a.severity?.toLowerCase().includes("warn")
          ? "p2"
          : "slo",
  }));
  return [...dashboards, ...alerts];
}

/** Build a 24-point diurnal series. The planner doesn't ship telemetry
 *  shape with the plan today; we generate a deterministic sine-with-
 *  noise curve from the plan_id so the chart is stable across renders
 *  and looks like a realistic diurnal pattern. Replace with a real
 *  shape from the backend when available. */
export function deriveTelemetryShape(plan: SourcePlanLike): TelemetryShape {
  // Deterministic seed from plan_id so the curve doesn't dance on
  // re-render. xfnv1a hash is overkill; sum char codes is fine.
  let seed = 0;
  for (let i = 0; i < plan.plan_id.length; i++) {
    seed = (seed * 31 + plan.plan_id.charCodeAt(i)) >>> 0;
  }
  function rand() {
    seed = (seed * 1664525 + 1013904223) >>> 0;
    return seed / 0xffffffff;
  }
  // Single morning + evening peak, with small noise.
  const series: number[] = [];
  for (let h = 0; h < 24; h++) {
    const morning = Math.exp(-((h - 11) ** 2) / 18);
    const evening = Math.exp(-((h - 20) ** 2) / 12) * 0.9;
    const base = 0.18 + morning * 0.65 + evening * 0.55;
    const noise = (rand() - 0.5) * 0.06;
    series.push(Math.max(0, Math.min(1, base + noise)));
  }
  const peakIdx = series.indexOf(Math.max(...series));
  return {
    peakQps: null,
    troughQps: null,
    shape: `diurnal, peaks ~${peakIdx}h and ~20h`,
    series,
  };
}

/** Map incident_script.events[] into IncidentStop[]. Tones cycle so a
 *  multi-event script looks visually paced; in practice the first stop
 *  is "arming", middle are "fault" / "page", final is "mitigation". */
export function deriveIncidentStops(plan: SourcePlanLike): IncidentStop[] {
  const events = plan.incident_script?.events ?? [];
  return events.map((e, i, arr) => {
    const min = Math.round(e.offset_seconds / 60);
    const isFirst = i === 0;
    const isLast = i === arr.length - 1;
    const tone: IncidentStop["tone"] =
      isFirst ? "accent"
    : isLast  ? "live"
    : /fault|crash|error|degrade|drop|loss/i.test(e.event_type) ? "warn"
    : "default";
    return {
      t: `T+${min}m`,
      title: humaniseEvent(e.event_type),
      sub: e.target_id,
      tone,
    };
  });
}

function humaniseEvent(t: string): string {
  return t
    .replace(/_/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase());
}

/** Five canonical data streams the generator emits when a demo runs.
 *  These are fixed for now (the planner doesn't enumerate per-plan
 *  sample streams). Volumes are illustrative placeholders. */
export function deriveSampleSources(_plan: SourcePlanLike): SampleDataSource[] {
  return [
    { id: "orders",    label: "Orders",          sub: "business_events.orders",    volume: "1.2K/min" },
    { id: "inventory", label: "Inventory ticks", sub: "business_events.inventory", volume: "240/min"  },
    { id: "traces",    label: "Traces",          sub: "tempo.checkout-svc",        volume: "8.4K/min" },
    { id: "logs",      label: "Logs",            sub: "loki.app + nginx",          volume: "22K/min" },
    { id: "metrics",   label: "Metrics",         sub: "prometheus + asserts",      volume: "560/sec"  },
  ];
}
