import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Sparkles, CheckCircle2, AlertCircle, Loader2,
  ExternalLink, Square, Rocket, Clock, Bug, RefreshCw, Info,
  MinusCircle, ArrowLeft, FileSearch,
  ScrollText, ClipboardList, X, Trash2, Wand2,
} from "lucide-react";

import {
  type PipelinePhase, PIPELINE_PHASES, cancelPipeline, deletePipeline, prunePipelines,
  listPipelines, type PipelineSummary,
} from "@/lib/api";
import { usePipeline, type PhaseState } from "@/lib/PipelineContext";
import { useAssistant } from "@/lib/AssistantContext";
import { Card } from "@/components/Card";
import { Button } from "@/components/Button";
import { Badge } from "@/components/Badge";
import { CrumbChip } from "@/components/CrumbChip";
import { PageHeader } from "@/components/PageHeader";
import { ClarionGlyph, ClarionEmptyArt, type ClarionGlyphName } from "@/components/icons/ClarionIcons";
import { Pagination } from "@/components/Pagination";
import { PipelineKpiCard } from "@/components/PipelineKpiCard";
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
    const id = pipeline.pipelineId;
    if (!id) return;
    // Request cancellation (the server also flips the DB row to cancelled
    // so a wedged task can't keep us spinning), then reconcile so the view
    // converges immediately instead of waiting on the stream to notice.
    await cancelPipeline(id).catch(() => {});
    await pipeline.reconcile(id);
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
  const qc = useQueryClient();
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(25);
  const [filter, setFilter] = useState("");
  const [stateFilter, setStateFilter] = useState<"all" | "running" | "done" | "failed">("all");
  const [pruning, setPruning] = useState(false);

  async function clearFailed() {
    if (pruning) return;
    if (!window.confirm("Remove all failed and cancelled builds from the list?\n\nThis only deletes the build records — plans/profiles and Cloud resources are untouched.")) return;
    setPruning(true);
    try {
      await prunePipelines();
      qc.invalidateQueries({ queryKey: ["pipelines"] });
    } catch (e) {
      window.alert(`Couldn't clear builds: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setPruning(false);
    }
  }

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

  // Highlights — top 6 newest builds, NOT subject to the search/state
  // filter (those narrow the All-builds table below). Newest-first by
  // started_at; the server may already return in this order but we
  // sort defensively so a future API change can't silently invert it.
  const highlights = useMemo(
    () => [...all].sort(
      (a, b) => new Date(b.started_at).getTime() - new Date(a.started_at).getTime(),
    ).slice(0, 6),
    [all],
  );

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
  // Clamp the active page so narrowing filters can't strand the table on
  // an empty "page 4 of 1". Derived during render (not synced via an
  // effect), so it stays consistent with the slice below.
  const pageCount = Math.max(1, Math.ceil(total / pageSize));
  const currentPage = Math.min(page, pageCount);
  const paged = useMemo(
    () => filtered.slice((currentPage - 1) * pageSize, currentPage * pageSize),
    [filtered, currentPage, pageSize],
  );

  return (
    <div className="space-y-6">
      <PageHeader
        eyebrow="Demo Builds"
        title="Every build, live or done."
        lede="Phase, duration, status. Click any row to open its live view. Start a new build from the dashboard."
      />

      {list.isLoading ? (
        <Card>
          <div className="p-8 text-center text-[var(--color-text-faint)]">Loading…</div>
        </Card>
      ) : all.length === 0 ? (
        <Card>
          <div className="flex flex-col items-center justify-center py-16 text-center">
            <ClarionEmptyArt name="builds" className="mb-4 text-[var(--color-text-muted)]" />
            <div className="text-sm font-medium">No builds yet</div>
            <div className="text-xs text-[var(--color-text-muted)] mt-1 max-w-sm">
              Start one from the dashboard&rsquo;s &ldquo;What are we showing today?&rdquo; card.
            </div>
          </div>
        </Card>
      ) : (
        <>
          {/* Recent — top 6 newest builds as compact KPI cards.
              Same shape as /profiles, /plans, /runs. */}
          <section aria-label="Recent builds">
            <div className="flex items-baseline justify-between mb-3">
              <h2 className="text-sm font-medium uppercase tracking-wider text-[var(--color-text-muted)]">
                Recent
              </h2>
              <span className="text-[11px] text-[var(--color-text-faint)] font-mono tabular-nums">
                {highlights.length} of {all.length}
              </span>
            </div>
            <div className="grid gap-2.5 sm:grid-cols-2 lg:grid-cols-3">
              {highlights.map((b) => (
                <PipelineKpiCard
                  key={b.pipeline_id}
                  pipeline={b}
                  compact
                  onClick={() => navigate(`/pipelines/${b.pipeline_id}`)}
                />
              ))}
            </div>
          </section>

          {/* All builds — paginated table with search + state filter.
              Filter chrome lives inline at the top of the table card so
              an SE with 50+ builds can narrow quickly. */}
          {all.length > 6 && (
            <section aria-label="All builds">
              <div className="flex items-baseline justify-between mb-3 gap-3 flex-wrap">
                <h2 className="text-sm font-medium uppercase tracking-wider text-[var(--color-text-muted)]">
                  All builds
                </h2>
                <span className="text-[11px] text-[var(--color-text-faint)] font-mono tabular-nums">
                  {all.length.toLocaleString()} total
                  {total !== all.length && (
                    <span className="ml-1">({total.toLocaleString()} match)</span>
                  )}
                </span>
              </div>
              <Card className="p-0 overflow-hidden">
                {/* Filter row — kept INSIDE the table card so it sits right
                    above the data it filters. */}
                <div className="flex items-center gap-2 px-4 py-2.5 border-b border-[var(--color-border)] flex-wrap">
                  <div
                    className={cn(
                      "flex items-center gap-1.5 h-8 px-2.5 rounded-md",
                      "bg-[var(--color-canvas-elev2)]/60 border border-[var(--color-border)]",
                      "focus-within:border-[color:var(--color-accent-border)] transition-colors",
                      "min-w-[220px]",
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
                  {all.some((b) => b.status === "failed" || b.status === "cancelled") && (
                    <button
                      type="button"
                      onClick={() => void clearFailed()}
                      disabled={pruning}
                      title="Remove all failed + cancelled builds from the list (records only)"
                      className="hover-wash ml-auto inline-flex h-8 items-center gap-1.5 rounded-md border border-[var(--color-border)] px-2.5 text-xs text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:border-[color:var(--color-danger)]/40 disabled:opacity-50"
                    >
                      {pruning ? <Loader2 size={12} className="animate-spin" /> : <Trash2 size={12} />}
                      Clear failed
                    </button>
                  )}
                </div>

                {total === 0 ? (
                  <div className="py-12 text-center text-sm text-[var(--color-text-muted)]">
                    No builds match &ldquo;{filter}&rdquo;
                    {stateFilter !== "all" ? ` in state ${stateFilter}` : ""}.
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
                          <th className="px-2 py-2.5"><span className="sr-only">Actions</span></th>
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
                      page={currentPage}
                      pageSize={pageSize}
                      total={total}
                      onPageChange={setPage}
                      onPageSizeChange={(n) => { setPageSize(n); setPage(1); }}
                    />
                  </>
                )}
              </Card>
            </section>
          )}
        </>
      )}
    </div>
  );
}

// BuildKpiStrip / computeBuildKpis / KpiTile removed — the /builds
// page now matches /profiles + /plans + /runs (no top-of-page strip;
// status visible per-card). Old aggregate metrics surface lived above
// the table; replaced by the per-card status pill on the Recent grid.

// ──────────────────────────────────────────────────────────────────
// BuildHistoryRow: includes the inline phase progress bar
// ──────────────────────────────────────────────────────────────────

function BuildHistoryRow({
  build, onClick,
}: { build: PipelineSummary; onClick: () => void }) {
  const qc = useQueryClient();
  const [deleting, setDeleting] = useState(false);

  async function onDelete(e: React.MouseEvent) {
    e.stopPropagation();
    const ok = window.confirm(
      `Delete build ${build.pipeline_id.slice(0, 8)}?\n\n` +
      "This cancels it if it's still running and removes the build record. " +
      "The plan/profile it produced (and any Grafana Cloud resources) are NOT " +
      "deleted — use the plan's Delete (with Cloud cleanup) for those.",
    );
    if (!ok) return;
    setDeleting(true);
    try {
      await deletePipeline(build.pipeline_id);
      qc.invalidateQueries({ queryKey: ["pipelines"] });
    } catch (err) {
      window.alert(`Couldn't delete build: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      setDeleting(false);
    }
  }

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
    // Intentional wall-clock read: a running build's elapsed time depends
    // on "now", and this row re-renders on the list's polling refetch.
    // eslint-disable-next-line react-hooks/purity
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
      <td className="px-2 py-3 text-right">
        <button
          type="button"
          onClick={onDelete}
          disabled={deleting}
          title="Delete this build"
          aria-label="Delete this build"
          className="grid place-items-center w-7 h-7 rounded-md text-[var(--color-text-faint)] transition-colors hover:bg-[var(--color-danger-bg)] hover:text-[var(--color-danger)] disabled:opacity-50"
        >
          {deleting ? <Loader2 size={13} className="animate-spin" /> : <Trash2 size={13} />}
        </button>
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

function PipelineRunView({
  navigate, onStop, onReset, onReRunSame,
}: {
  navigate: ReturnType<typeof useNavigate>;
  onStop: () => void;
  onReset: () => void;
  onReRunSame: () => void;
}) {
  const p = usePipeline();
  const assistant = useAssistant();
  const [approving, setApproving] = useState(false);

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

  // A build stops at the plan by default (nothing provisioned). Only call
  // it "live" when kg-publish actually ran; otherwise it landed a plan to
  // review.
  const wentLive = p.phases["kg-publish"].status === "done";
  const landedPlan = p.phases["plan"].status === "done";
  const eyebrow =
    p.status === "running" ? "Building demo"
  : p.status === "done"    ? (wentLive ? "Demo is live" : landedPlan ? "Plan ready to review" : "Build complete")
  : p.status === "failed"  ? "Build failed"
  : p.status === "cancelled" ? "Build cancelled"
  : "Pipeline";
  // Build landed a reviewable plan and stopped before provisioning.
  const awaitingReview = p.status === "done" && !!p.planId && !wentLive;

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

  // One-click approve → provision, IN PLACE. The "approve" pipeline phase
  // records the approval in-process (audited, no LLM/assistant call), then
  // this SAME build continues through generate → provision → kg-publish.
  // No second build row — it just moves through the next phases.
  async function approveAndGoLive() {
    if (!p.planId || !p.pipelineId || approving) return;
    if (typeof p.continueInPlace !== "function") {
      window.alert(
        "Approve is unavailable in this browser session. "
        + "Hard-refresh the page (Cmd+Shift+R) to load the latest UI.",
      );
      return;
    }
    setApproving(true);
    try {
      await p.continueInPlace(p.pipelineId, { startingPhase: "approve" });
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      console.error("approve & go live failed:", err);
      window.alert(`Couldn't approve and provision:\n\n${msg}`);
    } finally {
      setApproving(false);
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

      <PageHeader
        eyebrow={eyebrow}
        title={
          <>
            {host || "Pipeline"}
            {p.pipelineId && (
              <span className="ml-2 font-mono text-[14px] text-[var(--color-text-faint)] font-normal">
                {p.pipelineId.slice(0, 8)}{p.days ? ` · ${p.days}d` : ""}
              </span>
            )}
          </>
        }
        actions={
          <>
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
                    // Stops at the plan — provisioning stays gated on approval.
                    if (u) void p.start({ url: u, company: c, days: d, stop_after_phase: "plan" });
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
          </>
        }
      />

      {/* Crumb chips: links to the source profile + landed plan when
          available — jump out to the upstream profile or downstream plan. */}
      {(p.profileId || p.planId) && (
        <div className="flex items-center gap-2 flex-wrap">
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

      {/* Plan ready → the explicit review/approve gate. The build stops here
          (nothing in Grafana Cloud yet); the SE reviews the plan, refines it
          with the assistant if needed, then approves to provision + go live. */}
      {awaitingReview && p.planId && (
        <Card className="p-5 border-[color:var(--color-accent-border)] bg-[var(--color-accent-bg)]/40">
          <div className="flex items-start gap-3 flex-wrap">
            <span className="grid place-items-center w-9 h-9 rounded-lg bg-[var(--color-accent-bg)] text-[var(--color-accent)] border border-[var(--color-accent-border)] shrink-0">
              <CheckCircle2 size={18} />
            </span>
            <div className="flex-1 min-w-0">
              <h3 className="m-0 text-sm font-medium text-[var(--color-text)]">Plan ready to review</h3>
              <p className="mt-1 text-[13px] leading-relaxed text-[var(--color-text-muted)] max-w-2xl">
                Research and planning are done — <strong className="text-[var(--color-text)]">nothing has been written to
                Grafana Cloud</strong> yet. Approve to provision and go live, or refine the plan with the assistant first.
              </p>
              <div className="mt-3 flex flex-wrap items-center gap-2">
                <Button size="sm" variant="primary" onClick={approveAndGoLive} disabled={approving}>
                  {approving
                    ? <><Loader2 size={12} className="animate-spin" /> Provisioning…</>
                    : <><CheckCircle2 size={12} /> Approve &amp; go live</>}
                </Button>
                <Button
                  size="sm"
                  variant="secondary"
                  disabled={approving}
                  onClick={() => assistant.openAssistant({ scope: { plan_id: p.planId ?? undefined }, seedPrompt: "Refine this plan before I approve it: " })}
                >
                  <Wand2 size={12} /> Refine with assistant
                </Button>
                <button
                  type="button"
                  onClick={() => navigate(`/plans/${p.planId}`)}
                  className="ml-1 inline-flex items-center gap-1 text-[13px] text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
                >
                  <ClipboardList size={12} /> Review plan first
                </button>
              </div>
            </div>
          </div>
        </Card>
      )}

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
            // The column carries its PHASE glyph for identity; the
            // .journey-step.{done|running|failed} CSS tints it by status and
            // the bar shows progress. Running swaps in the self-animating
            // StateRunning spinner as the "active" cue.
            const glyphName: ClarionGlyphName = state.status === "running" ? "state-running" : phase;
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
                      <ClarionGlyph name={glyphName} size={15} />
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
