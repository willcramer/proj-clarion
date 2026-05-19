import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Sparkles, Globe, ChevronRight, CheckCircle2, AlertCircle, Loader2,
  ExternalLink, Square, Rocket, Clock, Activity, Bug, RefreshCw, Info,
  History, MinusCircle, ArrowLeft, FileSearch,
  ScrollText, ClipboardList, X,
} from "lucide-react";

import {
  type PipelinePhase, PIPELINE_PHASES, cancelPipeline,
  listPipelines, type PipelineSummary,
} from "@/lib/api";
import { usePipeline, type PhaseState } from "@/lib/PipelineContext";
import { Card } from "@/components/Card";
import { Button } from "@/components/Button";
import { Badge } from "@/components/Badge";
import { CrumbChip } from "@/components/CrumbChip";
import { LogView } from "@/components/LogView";
import { Pagination } from "@/components/Pagination";
// PipelineStepper exists but the live view now uses the .journey-steps
// inline buttons (below) for richer per-phase metadata. The stepper is
// kept around for any future surface (history view, plan-detail mini
// stepper) that wants the compact pill-style layout.
import { useToasts } from "@/components/Toast";
import { cn } from "@/lib/cn";
import { computeMetrics, diagnose, formatDuration } from "@/lib/diagnose";

// Numbered prefix mirrors PIPELINE_PHASES order in api/pipeline.py, keeps
// the step sequence obvious in the UI ("re-run from step 4" reads better
// than "re-run from generate"). Indexes are 1-based for human readability.
const PHASE_LABELS: Record<PipelinePhase, string> = {
  "research":   "1 · Research",
  "plan":       "2 · Plan",
  "approve":    "3 · Approve",
  "generate":   "4 · Generate events",
  "provision":  "5 · Provision dashboards",
  "kg-publish": "6 · KG publish",
};

const PHASE_HINTS: Record<PipelinePhase, string> = {
  "research":   "Read the company URL, build a CompanyProfile.",
  "plan":       "Vertical-aware demo plan: KG, processes, dashboards, alerts.",
  "approve":    "Mark plan ready for provisioning (audited).",
  "generate":   "Diurnal-shaped events into Postgres + traces to Tempo.",
  "provision":  "Push dashboards, alert rules, and the Postgres datasource.",
  "kg-publish": "Push KG model rules; start the entity emitter.",
};

/**
 * Build page, formerly the "Build a demo" form. The form moved to the
 * Dashboard's HeroBuildCard, so this page now renders one of two views:
 *
 *   1. **Pipeline live view**, if PipelineContext has an active build,
 *      show its phase progress, log, and controls (PipelineRunView).
 *      Same component as before; how users land here is via
 *      `/pipelines/:id` (loads into context, redirects to /new).
 *
 *   2. **Build history list**, if no pipeline is loaded, show the full
 *      list of past builds. Click a row → open its live view. Mirrors
 *      the "Plans" / "Profiles" page pattern: list → detail.
 *
 * Starting a new build lives on the Dashboard hero. Trying to do it
 * here twice was the consolidation problem this page now solves.
 */
export function NewDemoPage() {
  const navigate = useNavigate();
  const pipeline = usePipeline();

  async function stop() {
    if (pipeline.pipelineId) await cancelPipeline(pipeline.pipelineId).catch(() => {});
  }

  // ─── If a pipeline is in flight (or finished), show the live view ───
  if (pipeline.status !== "idle" && pipeline.pipelineId) {
    return (
      <PipelineRunView
        navigate={navigate}
        onStop={stop}
        onReset={() => pipeline.reset()}
        onReRunSame={async () => {
          // Smart resume: pick up at the first non-done phase using the
          // existing profile_id/plan_id artifacts. Never re-pays the LLM
          // cost for phases that already succeeded.
          const id = pipeline.pipelineId;
          if (!id) {
            window.alert("No pipeline_id in scope, try refreshing the page (Cmd+Shift+R).");
            return;
          }
          // Guard against stale HMR where smartResume isn't on the context yet.
          if (typeof pipeline.smartResume !== "function") {
            window.alert(
              "Re-run is unavailable in this browser session. "
              + "Hard-refresh the page (Cmd+Shift+R / Ctrl+Shift+R) to load the latest UI.",
            );
            return;
          }
          try {
            const newId = await pipeline.smartResume(id);
            if (newId) return;
            // All phases done already → fall back to a fresh full build.
            const u = pipeline.url;
            const c = pipeline.company ?? undefined;
            const d = pipeline.days;
            pipeline.reset();
            if (u) {
              await pipeline.start({ url: u, company: c, days: d });
            } else {
              window.alert("All phases were already done; nothing to resume from.");
            }
          } catch (err) {
            // Surface API/network failures instead of silently swallowing
            // them. Most common cause is a 400 from /run-from-phase when
            // a required input is missing on the source pipeline.
            const msg = err instanceof Error ? err.message : String(err);
            console.error("smartResume failed:", err);
            window.alert(`Re-run failed:\n\n${msg}`);
          }
        }}
      />
    );
  }

  // ─── Otherwise: build history list ────────────────────────────────
  return <BuildHistoryView />;
}

// ──────────────────────────────────────────────────────────────────
// BuildHistoryView, the /new idle render. Full paginated list of
// every pipeline build the API knows about. Same row pattern as the
// Dashboard's "Recent builds" section, row click navigates to
// /pipelines/:id which loads the build into PipelineContext and
// forwards back here (where the active-pipeline branch renders
// PipelineRunView).
//
// Mirrors the Plans/Profiles pages: list → detail.
// ──────────────────────────────────────────────────────────────────

// Total phases the orchestrator runs. Mirrors PIPELINE_PHASES length;
// hard-coded here because the table column wants a stable denominator.
const TOTAL_PHASES = 6;

// Human label per phase, matches PIPELINE_PHASES order. Reused for the
// "Generate" / "KG publish" callout next to the phase progress bar.
const PHASE_LABEL: Record<PipelinePhase, string> = {
  "research":   "Research",
  "plan":       "Plan",
  "approve":    "Approve",
  "generate":   "Generate",
  "provision":  "Provision",
  "kg-publish": "KG publish",
};

function BuildHistoryView() {
  const navigate = useNavigate();
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(25);
  const [filter, setFilter] = useState("");
  const [stateFilter, setStateFilter] = useState<"all" | "running" | "done" | "failed">("all");

  const list = useQuery({
    queryKey: ["pipelines"],
    queryFn: listPipelines,
    // Faster refresh when builds are running so the row badges feel live.
    refetchInterval: (q) => {
      const data = q.state.data;
      const anyRunning = (data ?? []).some((p) => p.status === "running");
      return anyRunning ? 3_000 : 10_000;
    },
  });

  const all = list.data ?? [];
  const kpis = useMemo(() => computeBuildKpis(all), [all]);

  // Apply filters BEFORE pagination so the page numbers reflect the
  // filtered set, not the raw list.
  const filtered = useMemo(() => {
    const q = filter.trim().toLowerCase();
    return all.filter((b) => {
      if (stateFilter !== "all" && b.status !== stateFilter) return false;
      if (!q) return true;
      const host = safeHost(b.url);
      return (
        b.url.toLowerCase().includes(q) ||
        host.toLowerCase().includes(q) ||
        (b.company ?? "").toLowerCase().includes(q) ||
        b.pipeline_id.toLowerCase().includes(q)
      );
    });
  }, [all, filter, stateFilter]);
  const total = filtered.length;
  const paged = useMemo(
    () => filtered.slice((page - 1) * pageSize, page * pageSize),
    [filtered, page, pageSize],
  );

  // Reset page to 1 whenever filters narrow the result below current
  // page's range, so the table doesn't show "empty page 4 of 1".
  useMemo(() => { if ((page - 1) * pageSize >= total && total > 0) setPage(1); }, [page, pageSize, total]);

  return (
    <div className="space-y-6">
      <header>
        <div className="text-[11px] font-mono uppercase tracking-[0.08em] text-[var(--color-text-faint)]">
          Builds
        </div>
        <h1 className="mt-2 text-[26px] font-semibold tracking-tight leading-tight text-[var(--color-text)]">
          Every run, all in one place.
        </h1>
        <p className="text-[var(--color-text-muted)] mt-1 text-sm max-w-2xl">
          Phase, duration, status. Click any row to open its live view.{" "}
          <span className="text-[var(--color-text-faint)]">
            Start a new build from the dashboard&rsquo;s hero card.
          </span>
        </p>
      </header>

      {/* KPI strip, derived from the loaded pipelines list. Cheap; bounded
          server-side at 200 rows so the math here is O(n) at worst. */}
      <BuildKpiStrip kpis={kpis} />

      <Card className="overflow-hidden">
        {/* Card head with title, count, and filter controls (search +
            state). Matches CDD pipelines mockup. */}
        <div className="flex items-center gap-3 px-4 py-3 border-b border-[var(--color-border)] flex-wrap">
          <h2 className="text-sm font-medium text-[var(--color-text)]">All builds</h2>
          <span className="text-[11px] font-mono text-[var(--color-text-faint)]">
            {all.length.toLocaleString()} total
            {total !== all.length && (
              <span className="ml-1">({total.toLocaleString()} match)</span>
            )}
          </span>
          <div className="ml-auto flex items-center gap-2 flex-wrap">
            <div
              className={cn(
                "flex items-center gap-1.5 h-8 px-2.5 rounded-md",
                "bg-[var(--color-canvas-elev2)]/60 border border-[var(--color-border)]",
                "focus-within:border-[color:var(--color-accent-border)] transition-colors",
                "min-w-[200px]",
              )}
            >
              <FileSearch size={12} className="text-[var(--color-text-faint)] shrink-0" />
              <input
                type="text"
                value={filter}
                onChange={(e) => setFilter(e.target.value)}
                placeholder="Filter by company or host"
                aria-label="Filter builds"
                className={cn(
                  "flex-1 bg-transparent outline-none border-0 text-xs",
                  "text-[var(--color-text)] placeholder:text-[var(--color-text-faint)]",
                  "min-w-0",
                )}
              />
              {filter && (
                <button
                  type="button"
                  onClick={() => setFilter("")}
                  aria-label="Clear filter"
                  className="text-[var(--color-text-faint)] hover:text-[var(--color-text)]"
                >
                  ×
                </button>
              )}
            </div>
            <select
              value={stateFilter}
              onChange={(e) => setStateFilter(e.target.value as typeof stateFilter)}
              aria-label="Filter by state"
              className={cn(
                "h-8 pl-2 pr-1 rounded-md text-xs font-mono",
                "bg-[var(--color-canvas-elev2)]/60 border border-[var(--color-border)]",
                "hover:border-[var(--color-border-strong)] focus-visible:border-[color:var(--color-accent-border)]",
                "transition-colors",
              )}
            >
              <option value="all">All states</option>
              <option value="running">Running</option>
              <option value="done">Done</option>
              <option value="failed">Failed</option>
            </select>
          </div>
        </div>

        {list.isLoading ? (
          <div className="p-8 text-center text-[var(--color-text-faint)]">Loading&hellip;</div>
        ) : all.length === 0 ? (
          <div className="flex flex-col items-center justify-center py-16 text-center">
            <History size={28} className="text-[var(--color-text-faint)] mb-3" />
            <div className="text-sm font-medium">No builds yet</div>
            <div className="text-xs text-[var(--color-text-muted)] mt-1 max-w-sm">
              Start one from the dashboard&rsquo;s &ldquo;What are we showing today?&rdquo; card.
            </div>
          </div>
        ) : total === 0 ? (
          <div className="py-12 text-center text-sm text-[var(--color-text-muted)]">
            No builds match &ldquo;{filter}&rdquo;{stateFilter !== "all" ? ` in state ${stateFilter}` : ""}.
          </div>
        ) : (
          <>
            <table className="w-full text-sm">
              <thead className="text-[10px] text-[var(--color-text-faint)] uppercase tracking-wider font-mono border-b border-[var(--color-border)]">
                <tr>
                  <th className="text-left  font-medium px-4 py-2.5">Run</th>
                  <th className="text-left  font-medium px-4 py-2.5">Target</th>
                  <th className="text-left  font-medium px-4 py-2.5">State</th>
                  <th className="text-left  font-medium px-4 py-2.5">Phase</th>
                  <th className="text-right font-medium px-4 py-2.5">Duration</th>
                  <th className="text-right font-medium px-4 py-2.5">Started</th>
                </tr>
              </thead>
              <tbody>
                {paged.map((b) => (
                  <BuildHistoryRow
                    key={b.pipeline_id}
                    build={b}
                    onClick={() => navigate(`/pipelines/${b.pipeline_id}`)}
                  />
                ))}
              </tbody>
            </table>
            <Pagination
              page={page}
              pageSize={pageSize}
              total={total}
              onPageChange={setPage}
              onPageSizeChange={(n) => { setPageSize(n); setPage(1); }}
            />
          </>
        )}
      </Card>
    </div>
  );
}

// ──────────────────────────────────────────────────────────────────
// BuildKpiStrip: 4 tiles per the CDD pipelines mockup
// ──────────────────────────────────────────────────────────────────

interface BuildKpis {
  runningNow: number;
  avgRunningDurationMs: number | null;
  todayTotal: number;
  todaySuccess: number;
  todayFailed: number;
  last7Success: number | null;
  last7Total: number;
  p50DurationMs: number | null;
  p95DurationMs: number | null;
}

function computeBuildKpis(rows: PipelineSummary[]): BuildKpis {
  const now = Date.now();
  const dayAgo = now - 24 * 3600 * 1000;
  const weekAgo = now - 7 * 24 * 3600 * 1000;

  const durMs = (b: PipelineSummary): number | null => {
    if (!b.started_at) return null;
    const end = b.finished_at ? new Date(b.finished_at).getTime() : now;
    return Math.max(0, end - new Date(b.started_at).getTime());
  };

  const running = rows.filter((b) => b.status === "running");
  const runningDurations = running.map(durMs).filter((d): d is number => d !== null);
  const avgRunningDurationMs = runningDurations.length === 0
    ? null
    : runningDurations.reduce((a, b) => a + b, 0) / runningDurations.length;

  const today = rows.filter((b) => new Date(b.started_at).getTime() >= dayAgo);
  const todaySuccess = today.filter((b) => b.status === "done").length;
  const todayFailed  = today.filter((b) => b.status === "failed" || b.status === "cancelled").length;

  const lastWeek = rows.filter((b) =>
    new Date(b.started_at).getTime() >= weekAgo
    && (b.status === "done" || b.status === "failed" || b.status === "cancelled"),
  );
  const last7Total = lastWeek.length;
  const last7Success = last7Total === 0
    ? null
    : lastWeek.filter((b) => b.status === "done").length / last7Total;

  // Percentiles over finished builds only.
  const finished = rows.filter((b) => b.status === "done" && b.finished_at);
  const finishedDurs = finished
    .map(durMs)
    .filter((d): d is number => d !== null)
    .sort((a, b) => a - b);
  const pct = (p: number): number | null => {
    if (finishedDurs.length === 0) return null;
    const idx = Math.min(finishedDurs.length - 1, Math.floor(p * finishedDurs.length));
    return finishedDurs[idx];
  };

  return {
    runningNow: running.length,
    avgRunningDurationMs,
    todayTotal: today.length,
    todaySuccess,
    todayFailed,
    last7Success,
    last7Total,
    p50DurationMs: pct(0.5),
    p95DurationMs: pct(0.95),
  };
}

function BuildKpiStrip({ kpis }: { kpis: BuildKpis }) {
  const successPct = kpis.last7Success === null ? null : Math.round(kpis.last7Success * 100);
  return (
    <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
      <KpiTile
        label="Running now"
        value={kpis.runningNow.toLocaleString()}
        hint={kpis.avgRunningDurationMs !== null
          ? `avg ${formatDuration(kpis.avgRunningDurationMs)}`
          : "none in flight"}
        tone={kpis.runningNow > 0 ? "live" : "neutral"}
      />
      <KpiTile
        label="Today"
        value={kpis.todayTotal.toLocaleString()}
        hint={kpis.todayTotal > 0
          ? `${kpis.todaySuccess} success · ${kpis.todayFailed} failed`
          : "no builds today"}
        tone="neutral"
      />
      <KpiTile
        label="7-day success"
        value={successPct === null ? "," : `${successPct}%`}
        hint={kpis.last7Total > 0 ? `across ${kpis.last7Total} finished` : "no finished runs"}
        tone={successPct === null ? "neutral" : successPct >= 80 ? "live" : successPct >= 50 ? "warn" : "danger"}
      />
      <KpiTile
        label="P50 duration"
        value={kpis.p50DurationMs !== null ? formatDuration(kpis.p50DurationMs) : ","}
        hint={kpis.p95DurationMs !== null ? `P95 ${formatDuration(kpis.p95DurationMs)}` : "no data yet"}
        tone="neutral"
      />
    </div>
  );
}

function KpiTile({
  label, value, hint, tone,
}: {
  label: string;
  value: string;
  hint: string;
  tone: "live" | "neutral" | "warn" | "danger";
}) {
  const valueClass =
    tone === "live"   ? "text-[var(--color-live)]"
  : tone === "warn"   ? "text-[var(--color-warning)]"
  : tone === "danger" ? "text-[var(--color-danger)]"
  : "text-[var(--color-text)]";
  return (
    <div className="rounded-xl border border-[var(--color-border)] bg-[var(--color-canvas-elev1)] p-4">
      <div className="text-[10px] font-mono uppercase tracking-wider text-[var(--color-text-faint)]">
        {label}
      </div>
      <div className={cn("text-[26px] font-semibold tabular-nums mt-1 leading-none", valueClass)}>
        {value}
      </div>
      <div className="text-[11px] text-[var(--color-text-muted)] mt-2">{hint}</div>
    </div>
  );
}

// ──────────────────────────────────────────────────────────────────
// BuildHistoryRow: includes the inline phase progress bar
// ──────────────────────────────────────────────────────────────────

function BuildHistoryRow({
  build, onClick,
}: { build: PipelineSummary; onClick: () => void }) {
  const flag =
    build.status === "running"   ? "live"   :
    build.status === "failed"    ? "danger" :
    build.status === "cancelled" ? "warn"   :
    "muted";
  const tone =
    build.status === "running"   ? "info"    :
    build.status === "done"      ? "success" :
    build.status === "failed"    ? "danger"  :
    "warning";
  const host = safeHost(build.url);
  const dur = (() => {
    if (!build.started_at) return null;
    const end = build.finished_at ? new Date(build.finished_at).getTime() : Date.now();
    return end - new Date(build.started_at).getTime();
  })();

  // Phase progress derives directly from the server-side rollup.
  // For "done" rows the bar fills 6/6; for running, phases_done is the
  // already-completed count and current_phase names the in-flight one;
  // for failed, the bar stops at phases_done and renders red.
  const phasesDone = build.phases_done ?? 0;
  const progressTo = build.status === "done" ? TOTAL_PHASES : phasesDone;
  const phaseLabel = build.status === "running"
    ? (build.current_phase ? PHASE_LABEL[build.current_phase as PipelinePhase] : "running")
    : build.status === "done"
      ? "KG publish"
      : build.status === "failed" && build.current_phase
        ? `failed at ${PHASE_LABEL[build.current_phase as PipelinePhase]}`
        : build.status;
  const barFill =
    build.status === "failed"   ? "bg-[var(--color-danger)]"
  : build.status === "running"  ? "bg-[var(--color-accent)]"
  : build.status === "done"     ? "bg-[var(--color-live)]"
  : "bg-[var(--color-text-faint)]";

  return (
    <tr
      onClick={onClick}
      className="border-b border-[var(--color-border)] last:border-0 hover:bg-white/[0.02] cursor-pointer transition-colors"
    >
      <td className="px-4 py-3 font-mono text-xs whitespace-nowrap">
        <span className={cn("row-flag", flag)} aria-hidden="true" />
        {build.pipeline_id.slice(0, 8)}
      </td>
      <td className="px-4 py-3 text-[var(--color-text)]">
        {host}
        {build.company && (
          <span className="ml-2 font-mono text-[11px] text-[var(--color-text-faint)]">
            {build.company}
          </span>
        )}
      </td>
      <td className="px-4 py-3">
        <Badge tone={tone}>{build.status}</Badge>
      </td>
      <td className="px-4 py-3">
        <div className="flex items-center gap-2.5">
          <span className="font-mono text-[11px] text-[var(--color-text-faint)] tabular-nums whitespace-nowrap">
            {progressTo}/{TOTAL_PHASES}
          </span>
          <div
            aria-hidden="true"
            className="w-[60px] h-[4px] rounded-full bg-[var(--color-canvas-elev2)] overflow-hidden shrink-0"
          >
            <div
              className={cn("h-full transition-all", barFill)}
              style={{ width: `${(progressTo / TOTAL_PHASES) * 100}%` }}
            />
          </div>
          <span className="text-xs text-[var(--color-text-muted)] truncate">
            {phaseLabel}
          </span>
        </div>
      </td>
      <td className="px-4 py-3 text-right tabular-nums font-mono text-xs text-[var(--color-text-muted)]">
        {formatDuration(dur)}
      </td>
      <td className="px-4 py-3 text-right text-xs text-[var(--color-text-faint)] tabular-nums">
        {build.started_at ? formatRelativeTime(build.started_at) : ","}
      </td>
    </tr>
  );
}

function safeHost(url: string): string {
  if (!url) return "";
  try { return new URL(url).host.replace(/^www\./, ""); }
  catch { return url; }
}

function formatRelativeTime(iso: string): string {
  const d = new Date(iso);
  const diffMs = Date.now() - d.getTime();
  const sec = Math.round(diffMs / 1000);
  if (sec < 60) return `${sec}s ago`;
  const min = Math.round(sec / 60);
  if (min < 60) return `${min}m ago`;
  const hr = Math.round(min / 60);
  if (hr < 24) return `${hr}h ago`;
  const day = Math.round(hr / 24);
  return `${day}d ago`;
}

/** Last 5 builds the API process has seen. Each row deep-links into
 *  /pipelines?p=<id> for full per-event drill-in (logs, phase rollup,
 *  diagnosis). The Re-run button kicks off a fresh build with the same
 *  params, useful when the previous one failed late (e.g. kg-publish)
 *  and you want a clean retry without retyping the URL.
 *
 *  Pipeline history lives in-memory on the API process today, so this
 *  list resets on API restart. Persisting to Postgres is on the
 *  backend backlog. */
function RecentBuilds({ onReRun }: { onReRun: (s: PipelineSummary) => void }) {
  const list = useQuery({
    queryKey: ["pipelines"],
    queryFn: listPipelines,
    // Refresh faster (3s) when builds are running so the running-count
    // and per-row status badges feel live as users queue more builds.
    refetchInterval: (q) => {
      const data = q.state.data;
      const anyRunning = (data ?? []).some((p) => p.status === "running");
      return anyRunning ? 3_000 : 10_000;
    },
  });
  const allBuilds = list.data ?? [];
  const running = allBuilds.filter((b) => b.status === "running");
  // Recently-finished builds, but always show at least 5 total rows so
  // the section feels populated even when there are no in-flight builds.
  const finished = allBuilds.filter((b) => b.status !== "running")
    .slice(0, Math.max(5 - running.length, 3));

  if (list.isLoading) return null;
  if (allBuilds.length === 0) {
    return (
      <Card className="p-5 max-w-2xl">
        <div className="flex items-center gap-2 text-sm text-[var(--color-text-muted)]">
          <History size={14} />
          <span>No builds yet on this API process.</span>
        </div>
      </Card>
    );
  }

  return (
    <Card className="p-5 max-w-2xl">
      <div className="flex items-center justify-between mb-3">
        <div className="flex items-center gap-2 text-sm font-medium">
          <History size={14} className="text-[var(--color-text-muted)]" />
          <span>Recent builds</span>
          {running.length > 0 && (
            <span
              className="ml-1 px-1.5 py-0.5 rounded text-[10px] font-mono bg-[var(--color-info)]/20 text-[var(--color-info)] inline-flex items-center gap-1"
              title="Builds currently running"
            >
              <Loader2 size={9} className="animate-spin" />
              {running.length} running
            </span>
          )}
        </div>
        <Link
          to="/pipelines"
          className="text-xs text-[var(--color-text-muted)] hover:text-[var(--color-text)] flex items-center gap-1"
        >
          All <ChevronRight size={12} />
        </Link>
      </div>
      {/* Running builds get their own visually-distinct group on top,        *  with a faint accent border so the SE eye lands there first
       *  when they're juggling multiple in-flight pipelines. */}
      {running.length > 0 && (
        <div className="mb-3 pb-3 border-b border-[var(--color-border)] space-y-1">
          <div className="text-[10px] uppercase tracking-wider text-[var(--color-text-faint)] mb-1.5">
            In flight
          </div>
          {running.map((s) => (
            <RecentBuildRow key={s.pipeline_id} s={s} onReRun={() => onReRun(s)} />
          ))}
        </div>
      )}
      <div className="space-y-1">
        {finished.map((s) => (
          <RecentBuildRow key={s.pipeline_id} s={s} onReRun={() => onReRun(s)} />
        ))}
      </div>
    </Card>
  );
}

function RecentBuildRow({ s, onReRun }: { s: PipelineSummary; onReRun: () => void }) {
  const pipeline = usePipeline();
  const qc = useQueryClient();
  const [cancelling, setCancelling] = useState(false);
  const dur = useMemo(() => {
    if (!s.started_at) return null;
    const end = s.finished_at ? new Date(s.finished_at).getTime() : Date.now();
    return end - new Date(s.started_at).getTime();
  }, [s.started_at, s.finished_at]);

  const StatusIcon =
    s.status === "running" ? Loader2 :
    s.status === "done"    ? CheckCircle2 :
    AlertCircle;
  const statusClass =
    s.status === "running" ? "text-[var(--color-info)] animate-spin" :
    s.status === "done"    ? "text-[var(--color-success)]" :
    "text-[var(--color-danger)]";

  // Just show the host for compactness; full URL is in the title.
  let host = s.url;
  try { host = new URL(s.url).host.replace(/^www\./, ""); } catch { /* keep raw */ }

  /** Cancel from the list, without navigating into the live view first.
   *  Saves clicks during demos when an SE realises a wrong URL is queued
   *  behind two other builds. The optimistic update is just "show
   *  cancelling…" until the next 3s refetch resolves the row to
   *  status=cancelled. */
  async function cancel() {
    if (!window.confirm(`Cancel build for ${host}? In-flight phases will stop.`)) return;
    setCancelling(true);
    try {
      await cancelPipeline(s.pipeline_id);
    } catch (e) {
      window.alert(`Couldn't cancel: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      // Refresh the list so the row flips to cancelled without waiting
      // for the next refetchInterval tick.
      qc.invalidateQueries({ queryKey: ["pipelines"] });
      setCancelling(false);
    }
  }

  // Click → snapshot-replay this pipeline into the live view. Avoids a
  // round-trip through /pipelines just to inspect a past build.
  return (
    <div className="flex items-center gap-3 px-2 py-2 rounded-md hover:bg-white/[0.03] group">
      <StatusIcon size={14} className={cn("shrink-0", statusClass)} />
      <button
        onClick={() => void pipeline.loadPipeline(s.pipeline_id)}
        className="flex-1 min-w-0 flex items-center gap-2 text-left"
        title={`${s.url} · ${s.pipeline_id}`}
      >
        <span className="text-sm truncate">{host}</span>
        {s.company && (
          <span className="text-xs text-[var(--color-text-faint)] truncate">{s.company}</span>
        )}
      </button>
      <span className="text-xs text-[var(--color-text-faint)] tabular-nums shrink-0">
        {s.days}d · {formatDuration(dur)}
      </span>
      {s.status === "running" ? (
        // In-flight: surface a destructive × so the SE can stop the
        // build without first opening the live view. Visible on the
        // row (not gated by hover) because the running state is
        // already attention-grabbing; the × must be reachable in one
        // click during a live demo.
        <button
          onClick={cancel}
          disabled={cancelling}
          title="Cancel build"
          aria-label={`Cancel build for ${host}`}
          className={cn(
            "p-1.5 rounded transition-colors",
            "text-[var(--color-danger)] hover:bg-[var(--color-danger-bg)]",
            "disabled:opacity-50 disabled:cursor-not-allowed",
          )}
        >
          {cancelling ? <Loader2 size={12} className="animate-spin" /> : <X size={12} />}
        </button>
      ) : (
        <button
          onClick={onReRun}
          title="Re-run with same URL/days"
          className="opacity-0 group-hover:opacity-100 transition-opacity p-1.5 rounded hover:bg-white/[0.05] text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
        >
          <RefreshCw size={12} />
        </button>
      )}
    </div>
  );
}

function PipelineRunView({
  navigate, onStop, onReset, onReRunSame,
}: {
  navigate: ReturnType<typeof useNavigate>;
  onStop: () => void;
  onReset: () => void;
  onReRunSame: () => void;
}) {
  const p = usePipeline();

  // Recompute every 1s while running so duration tickers are live.
  const [tick, setTick] = useState(0);
  useEffect(() => {
    if (p.status !== "running") return;
    const id = setInterval(() => setTick((t) => t + 1), 1000);
    return () => clearInterval(id);
  }, [p.status]);

  const metrics = useMemo(
    () => computeMetrics(p.phases, p.startedAt ?? null, p.finishedAt ?? null),
    // tick intentional, re-render the live ticker
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [p.phases, p.startedAt, p.finishedAt, tick],
  );

  const dx = useMemo(() => {
    if (p.status !== "failed") return null;
    const phaseLogs = Object.fromEntries(
      Object.entries(p.phases).map(([k, v]) => [k, v.logs]),
    ) as Record<PipelinePhase, string[]>;
    return diagnose(p.error, phaseLogs, metrics.phaseFailed);
  }, [p.status, p.error, p.phases, metrics.phaseFailed]);

  // Click a phase row to lock the right-side log panel to that phase.
  // Null means "auto", show whichever phase is currently active (or
  // the last one to emit anything). Set state per phase row click.
  const [focusPhase, setFocusPhase] = useState<PipelinePhase | null>(null);

  // Completion toast. We track the previous status so we only fire the
  // toast on the running→done / running→failed transitions, not on
  // every re-render once the pipeline has finished. Uses a ref instead
  // of state because we don't need the value to drive any UI; we just
  // need to compare-and-swap.
  const toasts = useToasts();
  const prevStatus = useRef(p.status);
  useEffect(() => {
    if (prevStatus.current === "running" && p.status === "done") {
      toasts.push({
        tone: "success",
        title: "Build complete",
        body: p.url ? <span className="font-mono">{p.url}</span> : undefined,
        action: p.planId
          ? { label: "View plan →", onClick: () => navigate(`/plans/${p.planId}`) }
          : undefined,
      });
    } else if (prevStatus.current === "running" && p.status === "failed") {
      toasts.push({
        tone: "danger",
        title: "Build failed",
        body: p.error ?? "Open the diagnosis card below for the suggested fix.",
        duration: 0,  // sticky, failures shouldn't disappear
      });
    }
    prevStatus.current = p.status;
  }, [p.status, p.url, p.planId, p.error, toasts, navigate]);

  // Auto-focus the currently-running phase (or the most recently
  // active one if everything's terminal) when the user hasn't clicked
  // to lock a different one. The `focusPhase` state stays null while
  // we're auto-tracking; clicking a column flips it explicit and
  // sticks there until the user clicks again to unlock.
  const autoActive = useMemo<PipelinePhase>(() => {
    // First running phase, otherwise the last done/failed one,
    // otherwise the first pending one.
    const running = PIPELINE_PHASES.find((ph) => p.phases[ph].status === "running");
    if (running) return running;
    const failed = PIPELINE_PHASES.find((ph) => p.phases[ph].status === "failed");
    if (failed) return failed;
    // last done
    let lastDone: PipelinePhase | null = null;
    for (const ph of PIPELINE_PHASES) {
      if (p.phases[ph].status === "done") lastDone = ph;
    }
    return lastDone ?? PIPELINE_PHASES[0];
  }, [p.phases]);
  const activePhase: PipelinePhase = focusPhase ?? autoActive;
  const activeState = p.phases[activePhase];
  const activeMetric = metrics.phases.find((m) => m.phase === activePhase);

  // Aggregated meta for the head strip. computeMetrics already does
  // the line + error rollup so just read from there instead of doing
  // it twice.
  const phasesDoneCount = useMemo(
    () => PIPELINE_PHASES.filter((ph) => p.phases[ph].status === "done").length,
    [p.phases],
  );

  const eyebrow =
    p.status === "running" ? "Building demo"
  : p.status === "done"    ? "Demo is live"
  : p.status === "failed"  ? "Build failed"
  : p.status === "cancelled" ? "Build cancelled"
  : "Pipeline";

  // Per-phase resume handler. Called from the side panel's "Re-run
  // from here" button. Reuses the same startFromPhase wiring that the
  // old PhaseRow had, so behavior is unchanged.
  function disabledReasonFor(phase: PipelinePhase): string | undefined {
    if (phase === "plan" && !p.profileId) return "research never produced a profile";
    if (["approve", "generate", "provision", "kg-publish"].includes(phase) && !p.planId) {
      return "plan was never produced";
    }
    return undefined;
  }
  async function resumeFromPhase(phase: PipelinePhase) {
    if (typeof p.startFromPhase !== "function") {
      window.alert(
        "Per-phase re-run is unavailable in this browser session. "
        + "Hard-refresh the page (Cmd+Shift+R) to load the latest UI.",
      );
      return;
    }
    try {
      await p.startFromPhase({
        phase,
        url: p.url,
        company: p.company ?? undefined,
        days: p.days,
        profile_id: p.profileId ?? undefined,
        plan_id: p.planId ?? undefined,
        parent_pipeline_id: p.pipelineId ?? undefined,
      });
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      console.error(`re-run from ${phase} failed:`, err);
      window.alert(`Re-run from ${phase} failed:\n\n${msg}`);
    }
  }

  const host = (() => {
    if (!p.url) return "";
    try { return new URL(p.url).host.replace(/^www\./, ""); }
    catch { return p.url; }
  })();

  return (
    <div className="space-y-4">
      {/* Back to builds, drops the user back to the builds list. The
          pipeline keeps running server-side; this is just navigation. */}
      <button
        type="button"
        onClick={onReset}
        className="text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text)] inline-flex items-center gap-1"
        title="Back to the builds list. This pipeline keeps running server-side."
      >
        <ArrowLeft size={14} /> Back to builds
      </button>

      <div className="flex items-center gap-4 flex-wrap">
        <div className="min-w-0">
          <div className="text-[11px] font-mono uppercase tracking-[0.08em] text-[var(--color-text-faint)]">
            {eyebrow}
          </div>
          <h1 className="mt-1 text-[24px] font-semibold tracking-tight leading-tight text-[var(--color-text)] truncate">
            {host || "Pipeline"}
            {p.pipelineId && (
              <span className="ml-2 font-mono text-[14px] text-[var(--color-text-faint)] font-normal">
                {p.pipelineId.slice(0, 8)}{p.days ? ` · ${p.days}d` : ""}
              </span>
            )}
          </h1>
          {/* Crumb chips: links to the source profile + landed plan when
              available. Lets the SE jump out of the build view into
              the upstream profile (to extend research) or the
              downstream plan (to review what got generated). The chip
              styling makes the buttons obvious; the underline-on-hover
              version this replaced read as faint metadata. */}
          {(p.profileId || p.planId) && (
            <div className="mt-3 flex items-center gap-2 flex-wrap">
              {p.profileId && (
                <CrumbChip
                  to={`/profiles/${p.profileId}`}
                  label="profile"
                  value={p.profileId}
                  icon={ScrollText}
                  title="Open the company profile this build researched"
                />
              )}
              {p.planId && (
                <CrumbChip
                  to={`/plans/${p.planId}`}
                  label="plan"
                  value={p.planId.slice(0, 8)}
                  icon={ClipboardList}
                  title="Open the demo plan this build produced"
                />
              )}
            </div>
          )}
        </div>
        <div className="ml-auto flex items-center gap-2">
          {p.status === "running" && (
            <Button size="sm" variant="danger" onClick={onStop}>
              <Square size={12} /> Stop
            </Button>
          )}
          {(p.status === "done" || p.status === "failed" || p.status === "cancelled") && (
            <>
              <Button
                size="sm"
                variant="primary"
                onClick={onReRunSame}
                title="Smart resume: skip phases that already succeeded, reuse existing profile/plan."
              >
                <RefreshCw size={12} /> Re-run
              </Button>
              <Button
                size="sm"
                variant="ghost"
                onClick={() => {
                  const ok = window.confirm(
                    "Force restart will run the entire pipeline from scratch:\n\n"
                    + "  • A NEW profile_id (research agent runs again)\n"
                    + "  • A NEW plan_id (planner agent makes 8+ LLM calls)\n"
                    + "  • Generate, Provision, KG-publish all re-run\n\n"
                    + "Cost: ~5-10 minutes + several dollars in LLM tokens.\n\n"
                    + "If you only want to retry a failed phase, click Re-run instead.\n\n"
                    + "Continue with Force restart?",
                  );
                  if (!ok) return;
                  const u = p.url;
                  const c = p.company ?? undefined;
                  const d = p.days;
                  p.reset();
                  if (u) void p.start({ url: u, company: c, days: d });
                }}
                title="Discard this pipeline and run from research again."
                className="!text-[var(--color-warning)] hover:!bg-[var(--color-warning)]/10"
              >
                <AlertCircle size={12} /> Force restart
              </Button>
              <Button
                size="sm"
                variant="secondary"
                onClick={onReset}
                title="Back to the builds list."
              >
                <Sparkles size={12} /> New build
              </Button>
            </>
          )}
        </div>
      </div>

      {/* Single horizontal "journey" card. Replaces the old MetricsStrip +
          PipelineStepper + 2-col PhaseRow/PhaseDetail layout. */}
      <div className="journey">
        <div className="journey-head">
          <div className="journey-target">
            <Rocket size={14} className="text-[var(--color-accent)]" />
            <span className="text-[var(--color-text-faint)]">target</span>
            <span>{host || p.url}</span>
          </div>
          <div className="journey-meta">
            <div>
              <span className="text-[var(--color-text-faint)]">elapsed</span>
              <b>{formatDuration(metrics.totalDurationMs)}</b>
            </div>
            <div>
              <span className="text-[var(--color-text-faint)]">phases</span>
              <b>{phasesDoneCount}<span className="text-[var(--color-text-faint)]">/{PIPELINE_PHASES.length}</span></b>
            </div>
            <div>
              <span className="text-[var(--color-text-faint)]">log lines</span>
              <b>{metrics.totalLogLines.toLocaleString()}</b>
            </div>
            <div>
              <span className="text-[var(--color-text-faint)]">events</span>
              <b className={metrics.totalErrors > 0 ? "!text-[var(--color-danger)]" : ""}>
                {metrics.totalErrors} err
              </b>
            </div>
          </div>
        </div>

        <div className="journey-steps">
          {PIPELINE_PHASES.map((phase, i) => {
            const state = p.phases[phase];
            const isFocus = activePhase === phase;
            const m = metrics.phases.find((x) => x.phase === phase);
            const num = String(i + 1).padStart(2, "0");
            const label = PHASE_LABELS[phase].replace(/^\d+\s·\s/, "");
            const stateClass =
              state.status === "done"    ? "done"
            : state.status === "running" ? "running"
            : state.status === "failed"  ? "failed"
            : state.status === "skipped" ? "skipped"
            : "pending";
            const Icon =
              state.status === "done"    ? CheckCircle2
            : state.status === "running" ? Loader2
            : state.status === "failed"  ? AlertCircle
            : state.status === "skipped" ? MinusCircle
            : MinusCircle;
            const durationLabel = m?.durationMs != null
              ? formatDuration(m.durationMs)
              : state.status === "skipped" ? "skipped"
              : state.status === "pending" ? "queued"
              : "—";
            const linesLabel = state.logs.length > 0
              ? `${state.logs.length} lines`
              : state.status === "running" ? "streaming…"
              : state.status === "pending" ? "—"
              : "";
            // Per-phase × is meaningful only while a phase is mid-run.
            // The orchestrator runs phases sequentially, so cancelling
            // the running phase ends the build. Same outcome as the
            // build-level Stop button in the header — this is just a
            // closer-to-eye affordance for when the SE is watching the
            // journey panel itself.
            const cancellable = state.status === "running";
            return (
              <div key={phase} className="relative">
                <button
                  type="button"
                  onClick={() => setFocusPhase(focusPhase === phase ? null : phase)}
                  aria-pressed={isFocus}
                  className={cn("journey-step w-full", stateClass, isFocus && "is-active")}
                >
                  <div className="journey-step-head">
                    <div className="journey-step-icon">
                      <Icon
                        size={14}
                        className={state.status === "running" ? "animate-spin" : ""}
                      />
                    </div>
                    <div className="min-w-0">
                      <div className="journey-step-no">{num}</div>
                      <h3 className="journey-step-name truncate">{label}</h3>
                    </div>
                  </div>
                  <div className="journey-step-meta">
                    <span>{durationLabel}</span>
                    {linesLabel && <span>{linesLabel}</span>}
                  </div>
                  <div className="journey-step-bar"><i /></div>
                </button>
                {cancellable && (
                  <button
                    type="button"
                    onClick={(e) => { e.stopPropagation(); onStop(); }}
                    aria-label={`Cancel ${label} (stops the build)`}
                    title="Cancel build"
                    className={cn(
                      "absolute top-2 right-2 w-6 h-6 rounded-full",
                      "flex items-center justify-center",
                      "bg-[var(--color-danger-bg)] text-[var(--color-danger)]",
                      "border border-[color:var(--color-danger)]/40",
                      "hover:bg-[var(--color-danger)] hover:text-white",
                      "transition-colors shadow-sm z-10",
                    )}
                  >
                    <X size={12} strokeWidth={2.5} aria-hidden="true" />
                  </button>
                )}
              </div>
            );
          })}
        </div>

        {/* Detail row: phase side panel + live log. The log pane just
            renders the focused phase's raw lines. */}
        <div className="journey-detail">
          <div className="journey-side">
            <div className="flex items-center justify-between gap-2">
              <h3>
                <span className="text-[var(--color-text-faint)] font-mono mr-2">
                  {String(PIPELINE_PHASES.indexOf(activePhase) + 1).padStart(2, "0")}
                </span>
                {PHASE_LABELS[activePhase].replace(/^\d+\s·\s/, "")}
              </h3>
              <PhaseStatusBadge status={activeState.status} />
            </div>
            <p className="desc">
              {activeState.message || PHASE_HINTS[activePhase]}
            </p>
            <div className="journey-stats">
              <div>
                <div className="l">Elapsed</div>
                <div className="v">{formatDuration(activeMetric?.durationMs ?? null)}</div>
              </div>
              <div>
                <div className="l">Log lines</div>
                <div className="v tabular-nums">{activeState.logs.length.toLocaleString()}</div>
              </div>
              <div>
                <div className="l">Status</div>
                <div className="v capitalize">{activeState.status}</div>
              </div>
              <div>
                <div className="l">Artifact</div>
                <div className="v font-mono text-[12px] truncate">
                  {activeState.artifact ? activeState.artifact.slice(0, 10) : "—"}
                </div>
              </div>
            </div>
            {(activeState.status === "failed" || activeState.status === "done") && (
              <div className="flex items-center gap-2 mt-1">
                <Button
                  size="sm"
                  variant="secondary"
                  onClick={() => void resumeFromPhase(activePhase)}
                  disabled={!!disabledReasonFor(activePhase)}
                  title={disabledReasonFor(activePhase) ?? `Re-run starting from ${PHASE_LABELS[activePhase]}.`}
                >
                  <RefreshCw size={12} /> Re-run from here
                </Button>
              </div>
            )}
          </div>
          <div className="journey-log">
            {activeState.logs.length === 0 ? (
              <div className="text-[var(--color-text-faint)] italic">
                {activeState.status === "pending"
                  ? `${PHASE_LABELS[activePhase].replace(/^\d+\s·\s/, "")} hasn't started yet.`
                  : activeState.status === "skipped"
                    ? `${PHASE_LABELS[activePhase].replace(/^\d+\s·\s/, "")} was skipped.`
                    : "No log output yet."}
              </div>
            ) : (
              activeState.logs.map((line, idx) => (
                <LogLine key={idx} line={line} />
              ))
            )}
          </div>
        </div>
      </div>

      {p.error && dx && (
        <DiagnosisCard
          error={p.error}
          dx={dx}
          onReRunSame={onReRunSame}
          onReset={onReset}
        />
      )}
      {p.error && !dx && (
        <Card className="p-4 border-[var(--color-danger)]/30 bg-[var(--color-danger)]/5">
          <div className="flex items-start gap-2 text-[var(--color-danger)]">
            <AlertCircle size={16} className="shrink-0 mt-0.5" />
            <div className="text-sm">
              <div className="font-medium">Pipeline failed</div>
              <div className="text-[var(--color-text-muted)] mt-1">{p.error}</div>
            </div>
          </div>
        </Card>
      )}

      {p.status === "done" && Object.keys(p.links).length > 0 && (
        <Card className="p-5 border-[var(--color-success)]/30 bg-[var(--color-success)]/5">
          <div className="flex items-start gap-3 mb-3">
            <CheckCircle2 className="text-[var(--color-success)]" size={20} />
            <div>
              <div className="font-medium">Demo is live</div>
              <div className="text-xs text-[var(--color-text-muted)] mt-0.5">
                Plan {p.planId?.slice(0, 8)} from profile {p.profileId}.
                {" "}<button onClick={() => navigate(`/plans/${p.planId}`)} className="underline hover:text-[var(--color-accent)]">View plan in Clarion →</button>
              </div>
            </div>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
            {Object.entries(p.links).map(([label, url]) => (
              <a
                key={label}
                href={url}
                target="_blank"
                rel="noreferrer"
                className="flex items-center justify-between gap-2 px-3 py-2 rounded-md border border-[var(--color-border)] bg-white/[0.02] hover:bg-white/[0.05] hover:border-[var(--color-border-strong)] text-sm transition-all"
              >
                <span>{label}</span>
                <ExternalLink size={12} className="text-[var(--color-text-muted)]" />
              </a>
            ))}
          </div>
        </Card>
      )}
    </div>
  );
}

// ──────────────────────────────────────────────────────────────────
// Side-panel + log helpers for the journey card.
// ──────────────────────────────────────────────────────────────────

function PhaseStatusBadge({ status }: { status: PhaseState["status"] }) {
  const tone =
    status === "running" ? "accent"
  : status === "done"    ? "success"
  : status === "failed"  ? "danger"
  : status === "skipped" ? "neutral"
  : "neutral";
  const icon =
    status === "running" ? <Loader2 size={10} className="animate-spin" />
  : status === "done"    ? <CheckCircle2 size={10} />
  : status === "failed"  ? <AlertCircle size={10} />
  : status === "skipped" ? <MinusCircle size={10} />
  : <Clock size={10} />;
  return (
    <Badge tone={tone}>
      {icon}
      <span className="ml-0.5">{status}</span>
    </Badge>
  );
}

/** Render one log line with subtle level-coloring. Matches the CDD
 *  `.journey-log .line` treatment: faint timestamp prefix, colored
 *  `info` / `ok` / `warn` / `err` token, then the rest in muted text. */
function LogLine({ line }: { line: string }) {
  // Strip ANSI escape codes that some upstream phases dump into the
  // log stream; they're meaningless once we're styling via CSS classes.
  // eslint-disable-next-line no-control-regex
  const clean = line.replace(/\x1b\[[0-9;]*m/g, "");

  // Best-effort parse: "HH:MM:SS LEVEL  rest" or just "LEVEL  rest".
  // Anything we can't match falls through as plain text.
  const m = clean.match(/^(\d{1,2}:\d{2}:\d{2}(?:\.\d+)?)?\s*(info|ok|warn|warning|error|err|debug|trace)?\s*(.*)$/i);
  const ts = m?.[1] ?? null;
  const rawLevel = m?.[2]?.toLowerCase() ?? null;
  const rest = m?.[3] ?? clean;

  let lvlClass: string | null = null;
  let lvlLabel: string | null = null;
  if (rawLevel) {
    if (rawLevel === "info" || rawLevel === "debug" || rawLevel === "trace") {
      lvlClass = "lvl-info"; lvlLabel = rawLevel;
    } else if (rawLevel === "ok") {
      lvlClass = "lvl-ok"; lvlLabel = "ok";
    } else if (rawLevel === "warn" || rawLevel === "warning") {
      lvlClass = "lvl-warn"; lvlLabel = "warn";
    } else if (rawLevel === "error" || rawLevel === "err") {
      lvlClass = "lvl-err"; lvlLabel = "err";
    }
  } else if (/error|traceback|exception|fail/i.test(rest)) {
    // Fallback: any line that mentions an error/traceback gets the
    // red tint so failures stand out even without an explicit level.
    lvlClass = "lvl-err"; lvlLabel = "err";
  }

  return (
    <div className="line">
      {ts && <span className="ts">{ts}</span>}
      {lvlLabel && <span className={lvlClass!}>{lvlLabel}</span>}
      <span>{rest}</span>
    </div>
  );
}

function PhaseRow({
  phase, state, metric, isLast,
  onResume, resumeDisabledReason, showResume, resumeIsRetest,
  focused, onClick,
}: {
  phase: PipelinePhase;
  state: PhaseState;
  metric: { durationMs: number | null; logLineCount: number; errorCount: number } | undefined;
  isLast: boolean;
  /** Always-visible button (when terminal) that starts a new pipeline from
   *  this phase with the prior pipeline as parent. The parent decides
   *  whether the inputs that phase needs are available. */
  onResume?: () => void | Promise<void>;
  /** When a resume isn't possible (e.g. no plan_id available), show a
   *  disabled button with this reason in the title. */
  resumeDisabledReason?: string;
  /** Whether to show the resume button slot at all (pipeline terminal). */
  showResume?: boolean;
  /** True when this is a successful phase being re-run for testing/iteration
   *  rather than retrying after a failure. Reword + soften the styling so
   *  it's visually distinct from the failure-recovery affordance. */
  resumeIsRetest?: boolean;
  /** True when the right-side log panel is locked to this phase. */
  focused?: boolean;
  /** Click anywhere on the row → toggle "lock log panel to this phase". */
  onClick?: () => void;
}) {
  const Icon =
    state.status === "running" ? Loader2 :
    state.status === "done" ? CheckCircle2 :
    state.status === "failed" ? AlertCircle :
    state.status === "skipped" ? MinusCircle :
    ChevronRight;
  const iconClass =
    state.status === "running" ? "text-[var(--color-info)] animate-spin" :
    state.status === "done" ? "text-[var(--color-success)]" :
    state.status === "failed" ? "text-[var(--color-danger)]" :
    state.status === "skipped" ? "text-[var(--color-text-faint)] opacity-50" :
    "text-[var(--color-text-faint)]";

  return (
    <div
      onClick={onClick}
      className={cn(
        "flex items-start gap-3 px-2 py-2.5 group rounded-md transition-colors",
        !isLast && "border-b border-[var(--color-border)]",
        onClick && "cursor-pointer",
        // Skipped phases (resume-from-later builds) get muted so they
        // visually fade into the background. They're not errors, they
        // just didn't run this time.
        state.status === "skipped" && "opacity-55",
        focused
          ? "bg-[var(--color-accent)]/8 ring-1 ring-[var(--color-accent)]/30"
          : onClick && "hover:bg-white/[0.02]",
      )}
    >
      <Icon size={14} className={cn("mt-0.5 shrink-0", iconClass)} />
      <div className="flex-1 min-w-0">
        <div className="flex items-center gap-2">
          <span className="text-sm font-medium">{PHASE_LABELS[phase]}</span>
          {focused && (
            <span className="text-[10px] font-mono text-[var(--color-accent)]/80">
              ·  inspecting
            </span>
          )}
          {state.artifact && (
            <span className="font-mono text-[10px] text-[var(--color-text-faint)] truncate">
              {state.artifact.length > 12 ? state.artifact.slice(0, 8) + "…" : state.artifact}
            </span>
          )}
          {state.status === "done" && <Badge tone="success">done</Badge>}
          {state.status === "running" && <Badge tone="info">running</Badge>}
          {state.status === "failed" && <Badge tone="danger">failed</Badge>}
          {state.status === "skipped" && <Badge tone="neutral">skipped</Badge>}
        </div>
        <div className="text-xs text-[var(--color-text-muted)] mt-0.5">
          {state.message || PHASE_HINTS[phase]}
        </div>
        {metric && state.status !== "pending" && (
          <div className="flex items-center gap-3 mt-1.5 text-[10px] text-[var(--color-text-faint)] font-mono">
            <span className="inline-flex items-center gap-1"><Clock size={10} />{formatDuration(metric.durationMs)}</span>
            <span className="inline-flex items-center gap-1"><Activity size={10} />{metric.logLineCount} lines</span>
            {metric.errorCount > 0 && (
              <span className="inline-flex items-center gap-1 text-[var(--color-warning)]">
                <Bug size={10} />{metric.errorCount} err
              </span>
            )}
          </div>
        )}
      </div>
      {showResume && (
        <button
          onClick={(e) => {
            // Don't toggle the row's inspect-focus when clicking the
            // resume button itself.
            e.stopPropagation();
            void onResume?.();
          }}
          disabled={!onResume}
          title={
            !onResume
              ? resumeDisabledReason ?? "Inputs for this phase aren't available"
              : resumeIsRetest
              ? `Re-test ${PHASE_LABELS[phase]} only, useful when iterating on the code that affects this phase. Reuses profile + plan; new pipeline_id linked to this one as parent.`
              : `Re-run from ${PHASE_LABELS[phase]} (skips earlier phases, reuses profile/plan from this build)`
          }
          className={cn(
            "shrink-0 self-center transition-colors",
            "px-2 h-7 rounded-md border text-xs flex items-center gap-1.5",
            !onResume
              ? "border-[var(--color-border)]/40 bg-transparent text-[var(--color-text-faint)] cursor-not-allowed opacity-50"
              : resumeIsRetest
              // Successful-phase re-test → softer / hover-visible only.
              // Don't compete visually with the failure-recovery flavor.
              ? "border-[var(--color-border)]/40 bg-white/[0.01] opacity-0 group-hover:opacity-100 hover:bg-white/[0.05] hover:border-[var(--color-border)] text-[var(--color-text-faint)] hover:text-[var(--color-text-muted)]"
              // Failure-recovery → always-visible, accent-tinted.
              : "border-[var(--color-border)] bg-white/[0.02] hover:bg-[var(--color-accent)]/10 hover:border-[var(--color-accent)]/40 text-[var(--color-text-muted)] hover:text-[var(--color-text)]",
          )}
        >
          <RefreshCw size={11} />
          <span>{resumeIsRetest ? "Re-test" : "Re-run"}</span>
        </button>
      )}
    </div>
  );
}

/** Top-of-page key metrics. While running, total ticks live; on terminal
 *  status, freezes at the final values. Kept compact, meant for a
 *  glance, not deep analysis. */
function MetricsStrip({
  metrics, status,
}: {
  metrics: ReturnType<typeof computeMetrics>;
  status: ReturnType<typeof usePipeline>["status"];
}) {
  const phasesDone = metrics.phases.filter((m) => m.status === "done").length;
  const phasesTotal = metrics.phases.length;
  return (
    <Card className="p-4">
      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm">
        <Stat
          icon={Clock}
          label="Total"
          value={formatDuration(metrics.totalDurationMs)}
          tone={status === "running" ? "info" : status === "done" ? "success" : status === "failed" ? "danger" : "neutral"}
        />
        <Stat
          icon={CheckCircle2}
          label="Phases done"
          value={`${phasesDone}/${phasesTotal}`}
          tone={phasesDone === phasesTotal ? "success" : "neutral"}
        />
        <Stat
          icon={Activity}
          label="Log lines"
          value={metrics.totalLogLines.toLocaleString()}
          tone="neutral"
        />
        <Stat
          icon={Bug}
          label="Error-shaped lines"
          value={metrics.totalErrors.toLocaleString()}
          tone={metrics.totalErrors > 0 ? "danger" : "neutral"}
        />
      </div>
    </Card>
  );
}

function Stat({
  icon: Icon, label, value, tone,
}: {
  icon: typeof Clock;
  label: string;
  value: string;
  tone: "neutral" | "info" | "success" | "danger";
}) {
  const toneClass = {
    neutral: "text-[var(--color-text)]",
    info:    "text-[var(--color-info)]",
    success: "text-[var(--color-success)]",
    danger:  "text-[var(--color-danger)]",
  }[tone];
  return (
    <div className="flex items-center gap-3">
      <Icon size={14} className="text-[var(--color-text-faint)]" />
      <div>
        <div className="text-[10px] uppercase tracking-wider text-[var(--color-text-faint)]">
          {label}
        </div>
        <div className={cn("text-base font-semibold tabular-nums", toneClass)}>
          {value}
        </div>
      </div>
    </div>
  );
}

/** Pattern-matched diagnosis with concrete next steps. The `dx` payload
 *  comes from `diagnose()` in lib/diagnose.ts. Each branch there should
 *  surface a kind+summary+suggested-fix that's specific enough to act on. */
function DiagnosisCard({
  error, dx, onReRunSame, onReset,
}: {
  error: string;
  dx: NonNullable<ReturnType<typeof diagnose>>;
  onReRunSame: () => void;
  onReset: () => void;
}) {
  const tone =
    dx.severity === "warning"
      ? "border-[var(--color-warning)]/40 bg-[var(--color-warning)]/5"
      : "border-[var(--color-danger)]/40 bg-[var(--color-danger)]/5";
  const iconClass =
    dx.severity === "warning" ? "text-[var(--color-warning)]" : "text-[var(--color-danger)]";

  return (
    <Card className={cn("p-5", tone)}>
      <div className="flex items-start gap-3">
        <Bug size={18} className={cn("shrink-0 mt-0.5", iconClass)} />
        <div className="flex-1 space-y-3 min-w-0">
          <div>
            <div className="flex items-center gap-2 mb-1">
              <span className="text-sm font-medium">{dx.summary}</span>
              <Badge tone={dx.severity === "warning" ? "warning" : "danger"}>{dx.kind}</Badge>
            </div>
            <div className="text-xs text-[var(--color-text-muted)] flex items-start gap-1">
              <Info size={11} className="shrink-0 mt-0.5" />
              <span>{dx.suggested}</span>
            </div>
          </div>

          <details className="text-xs">
            <summary className="cursor-pointer text-[var(--color-text-faint)] hover:text-[var(--color-text-muted)]">
              Full error message
            </summary>
            <pre className="mt-2 p-2 bg-black/40 rounded font-mono text-[var(--color-text-muted)] whitespace-pre-wrap break-all max-h-32 overflow-auto">
              {error}
            </pre>
          </details>

          <div className="flex items-center gap-2 pt-1">
            {dx.retryable && (
              <Button size="sm" variant="primary" onClick={onReRunSame}>
                <RefreshCw size={12} /> Re-run with same URL
              </Button>
            )}
            <Button size="sm" variant="secondary" onClick={onReset}>
              Edit + retry
            </Button>
          </div>
        </div>
      </div>
    </Card>
  );
}

function PhaseDetail({
  phases, focusPhase, onClearFocus,
}: {
  phases: Record<PipelinePhase, PhaseState>;
  /** When non-null, lock the panel to this phase regardless of which
   *  one is currently active. Set by clicking a phase row. Lets the
   *  user scroll back through any phase's history mid-run. */
  focusPhase?: PipelinePhase | null;
  onClearFocus?: () => void;
}) {
  const order = [...PIPELINE_PHASES].reverse();
  const autoFocus =
    order.find((p) => phases[p].status === "running")
    ?? order.find((p) => phases[p].status === "failed")
    ?? order.find((p) => phases[p].status === "done")
    ?? "research";
  // Manual focus wins; otherwise track whichever phase has activity.
  const focus = focusPhase ?? autoFocus;
  const state = phases[focus];
  const isManual = focusPhase !== null && focusPhase !== undefined;

  return (
    <Card className="flex flex-col h-[560px] overflow-hidden">
      <div className="px-4 py-3 border-b border-[var(--color-border)]">
        <div className="flex items-center justify-between">
          <div className="text-sm font-medium flex items-center gap-2">
            {PHASE_LABELS[focus]}
            {isManual && (
              <button
                onClick={onClearFocus}
                title="Unlock, let the panel auto-track the active phase"
                className="text-[10px] font-mono text-[var(--color-accent)]/70 hover:text-[var(--color-accent)] uppercase tracking-wider"
              >
                locked · clear
              </button>
            )}
          </div>
          <div className="text-xs text-[var(--color-text-faint)]">{state.logs.length} lines</div>
        </div>
        <div className="text-xs text-[var(--color-text-muted)] mt-0.5">{PHASE_HINTS[focus]}</div>
      </div>
      <LogView
        lines={state.logs}
        emptyText={
          isManual
            ? `${PHASE_LABELS[focus]} hasn't produced any output yet.`
            : "Waiting for output…"
        }
        // Card height is fixed at 560 + ~80 for the header → body fills.
        maxHeight="100%"
        className="flex-1"
      />
    </Card>
  );
}
