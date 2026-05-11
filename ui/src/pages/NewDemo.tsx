import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import {
  Sparkles, Globe, ChevronRight, CheckCircle2, AlertCircle, Loader2,
  ExternalLink, Square, Rocket, Clock, Activity, Bug, RefreshCw, Info,
  History, MinusCircle, ArrowLeft,
} from "lucide-react";

import {
  type PipelinePhase, PIPELINE_PHASES, cancelPipeline,
  listPipelines, type PipelineSummary,
} from "@/lib/api";
import { usePipeline, type PhaseState } from "@/lib/PipelineContext";
import { Card } from "@/components/Card";
import { Button } from "@/components/Button";
import { Badge } from "@/components/Badge";
import { LogView } from "@/components/LogView";
import { PipelineStepper } from "@/components/PipelineStepper";
import { useToasts } from "@/components/Toast";
import { cn } from "@/lib/cn";
import { computeMetrics, diagnose, formatDuration } from "@/lib/diagnose";

// Numbered prefix mirrors PIPELINE_PHASES order in api/pipeline.py — keeps
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

export function NewDemoPage() {
  const navigate = useNavigate();
  const pipeline = usePipeline();
  // Pre-fill from query params when the user got here via "Add profile"
  // on /profiles or any other deep-link. We read once on mount; if you
  // edit the URL field after, navigating back doesn't clobber your edit.
  const [searchParams, setSearchParams] = useSearchParams();
  const prefillUrl = searchParams.get("prefill_url") ?? "";
  const prefillCompany = searchParams.get("prefill_company") ?? "";

  // Form state is page-local — only the running pipeline state is global.
  const [url, setUrl] = useState(prefillUrl);
  const [company, setCompany] = useState(prefillCompany);
  const [days, setDays] = useState(1);

  // Once we've consumed the prefill params, strip them from the URL bar
  // so a refresh doesn't re-prefill stale values and the bar stays clean.
  useEffect(() => {
    if (prefillUrl || prefillCompany) {
      const next = new URLSearchParams(searchParams);
      next.delete("prefill_url");
      next.delete("prefill_company");
      setSearchParams(next, { replace: true });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  // Volume preset: keep this small by default so a fresh build never
  // accidentally fills the SE's Cloud quota. Values map to the
  // volume_per_day override the planner accepts:
  //   "auto"   = let the planner auto-scale (None on the wire)
  //   "smoke"  = 500/day  → finishes in a couple minutes, dashboards still readable
  //   "demo"   = 2500/day → default for a real walk-through
  //   "stress" = 25000/day → stress-test the generator + Cloud ingest
  const [volumePreset, setVolumePreset] = useState<"smoke" | "demo" | "auto" | "stress">("demo");
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  function volumeForPreset(preset: typeof volumePreset): number | undefined {
    switch (preset) {
      case "smoke":  return 500;
      case "demo":   return 2_500;
      case "stress": return 25_000;
      case "auto":   return undefined;  // omit → planner auto-scales
    }
  }

  async function start() {
    if (!url.trim() || submitting) return;
    setSubmitting(true);
    setSubmitError(null);
    try {
      await pipeline.start({
        url: url.trim(),
        company: company.trim() || undefined,
        days,
        volume_per_day: volumeForPreset(volumePreset),
      });
    } catch (e) {
      setSubmitError(String(e));
    } finally {
      setSubmitting(false);
    }
  }

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
            window.alert("No pipeline_id in scope — try refreshing the page (Cmd+Shift+R).");
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

  // ─── Otherwise: form ────────────────────────────────────────────
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight flex items-center gap-2">
          <Sparkles className="text-[var(--color-accent)]" size={20} />
          Build a demo
        </h1>
        <p className="text-[var(--color-text-muted)] mt-1 text-sm max-w-2xl">
          One URL, one button. The pipeline does the rest: research → plan → auto-approve → generate
          events → provision dashboards → push KG. End state: a live demo in your Grafana stack.
        </p>
        <p className="text-[var(--color-text-faint)] text-xs mt-2 max-w-2xl">
          Safe to navigate away or close the tab — the build runs server-side and you'll pick it
          back up here when you return.
        </p>
      </div>

      <Card className="p-6 max-w-2xl">
        <div className="space-y-4">
          <div>
            <label className="text-xs uppercase tracking-wider text-[var(--color-text-muted)] block mb-1">
              Company URL
            </label>
            <div className="relative">
              <Globe size={14} className="absolute left-3 top-1/2 -translate-y-1/2 text-[var(--color-text-faint)]" />
              <input
                type="url"
                value={url}
                onChange={(e) => setUrl(e.target.value)}
                placeholder="https://www.acme-retail.com"
                className="w-full pl-9 pr-3 h-10 rounded-md bg-white/[0.02] border border-[var(--color-border)] text-sm focus:border-[var(--color-accent)] focus:outline-none"
                autoFocus
              />
            </div>
            <p className="text-xs text-[var(--color-text-faint)] mt-1">
              Must be in <code className="font-mono">RESEARCH_ALLOWED_HOSTS</code> in <code className="font-mono">.env</code>.
            </p>
          </div>

          <div className="grid grid-cols-2 gap-4">
            <div>
              <label className="text-xs uppercase tracking-wider text-[var(--color-text-muted)] block mb-1">
                Company hint (optional)
              </label>
              <input
                value={company}
                onChange={(e) => setCompany(e.target.value)}
                placeholder="e.g. AcmeRetail Inc."
                className="w-full px-3 h-10 rounded-md bg-white/[0.02] border border-[var(--color-border)] text-sm focus:border-[var(--color-accent)] focus:outline-none"
              />
            </div>
            <div>
              <label className="text-xs uppercase tracking-wider text-[var(--color-text-muted)] block mb-1">
                Days of events
              </label>
              <input
                type="number"
                min={1}
                max={14}
                value={days}
                onChange={(e) => setDays(parseInt(e.target.value, 10) || 1)}
                className="w-full px-3 h-10 rounded-md bg-white/[0.02] border border-[var(--color-border)] text-sm focus:border-[var(--color-accent)] focus:outline-none"
              />
            </div>
          </div>

          <div>
            <label className="text-xs uppercase tracking-wider text-[var(--color-text-muted)] block mb-1.5">
              Build size
            </label>
            <div className="grid grid-cols-4 gap-2">
              {(["smoke", "demo", "auto", "stress"] as const).map((preset) => {
                const labels: Record<typeof preset, { title: string; sub: string }> = {
                  smoke:  { title: "Smoke",  sub: "500/day" },
                  demo:   { title: "Demo",   sub: "2.5K/day" },
                  auto:   { title: "Auto",   sub: "scaled" },
                  stress: { title: "Stress", sub: "25K/day" },
                };
                const { title, sub } = labels[preset];
                const active = volumePreset === preset;
                return (
                  <button
                    key={preset}
                    type="button"
                    onClick={() => setVolumePreset(preset)}
                    className={cn(
                      "h-12 px-2 rounded-md border text-left transition-all",
                      active
                        ? "bg-[var(--color-accent)]/10 border-[var(--color-accent)]/40 text-[var(--color-text)]"
                        : "bg-white/[0.02] border-[var(--color-border)] text-[var(--color-text-muted)] hover:bg-white/[0.04] hover:text-[var(--color-text)]",
                    )}
                  >
                    <div className="text-xs font-medium">{title}</div>
                    <div className="text-[10px] opacity-70 font-mono">{sub}</div>
                  </button>
                );
              })}
            </div>
            <p className="text-xs text-[var(--color-text-faint)] mt-1">
              {volumePreset === "smoke" && "~500 events/day. Finishes in ~2-3 min, dashboards still readable. Use when iterating on planner/KG fixes."}
              {volumePreset === "demo"  && "~2.5K events/day. Default. Realistic-looking demo, finishes in ~5-8 min."}
              {volumePreset === "auto"  && "Planner auto-scales by channel count (1.5K-5K/day). Best when you don't know the company shape."}
              {volumePreset === "stress" && "~25K events/day. Pressure-tests the generator + Cloud ingest. Will burn quota — use sparingly."}
            </p>
          </div>

          {submitError && (
            <div className="text-xs text-[var(--color-danger)] flex items-center gap-1">
              <AlertCircle size={12} /> {submitError}
            </div>
          )}

          <Button
            variant="primary"
            size="lg"
            disabled={!url.trim() || submitting}
            onClick={() => void start()}
            className="w-full"
          >
            {submitting ? <Loader2 size={16} className="animate-spin" /> : <Rocket size={16} />}
            Build the demo end-to-end
          </Button>
        </div>
      </Card>

      <RecentBuilds
        onReRun={async (s) => {
          // Smart resume: skip phases that already succeeded in the
          // source build. If everything was done, fall back to fresh.
          if (typeof pipeline.smartResume !== "function") {
            window.alert(
              "Re-run is unavailable in this browser session. "
              + "Hard-refresh the page (Cmd+Shift+R) to load the latest UI.",
            );
            return;
          }
          try {
            const newId = await pipeline.smartResume(s.pipeline_id);
            if (newId) return;
            await pipeline.start({
              url: s.url,
              company: s.company ?? undefined,
              days: s.days,
            });
          } catch (err) {
            const msg = err instanceof Error ? err.message : String(err);
            console.error("smartResume failed:", err);
            window.alert(`Re-run failed:\n\n${msg}`);
          }
        }}
      />
    </div>
  );
}

/** Last 5 builds the API process has seen. Each row deep-links into
 *  /pipelines?p=<id> for full per-event drill-in (logs, phase rollup,
 *  diagnosis). The Re-run button kicks off a fresh build with the same
 *  params — useful when the previous one failed late (e.g. kg-publish)
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
      {/* Running builds get their own visually-distinct group on top —
       *  with a faint accent border so the SE eye lands there first
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
      {s.status !== "running" && (
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
    // tick intentional — re-render the live ticker
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
  // Null means "auto" — show whichever phase is currently active (or
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
        duration: 0,  // sticky — failures shouldn't disappear
      });
    }
    prevStatus.current = p.status;
  }, [p.status, p.url, p.planId, p.error, toasts, navigate]);

  return (
    <div className="space-y-4">
      {/* Back link — drops the user into the form view (which has the
       *  build form on top + the Recent Builds list below). The pipeline
       *  itself keeps running server-side; this is just navigation, not
       *  cancel. Mirrors the same affordance on /profiles/<id>, /plans/<id>. */}
      <button
        type="button"
        onClick={onReset}
        className="text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text)] inline-flex items-center gap-1 -mb-2"
        title="Go back to the build form + recent builds list. This pipeline keeps running server-side."
      >
        <ArrowLeft size={14} /> Back to builds
      </button>
      <div className="flex items-center justify-between">
        <div className="text-sm">
          <span className="text-[var(--color-text-muted)]">Building from</span>{" "}
          <span className="font-mono">{p.url}</span>
          {p.company && <span className="text-[var(--color-text-faint)]"> · {p.company}</span>}
          <span className="text-[var(--color-text-faint)]"> · {p.days}d</span>
          {p.pipelineId && (
            <span className="text-[var(--color-text-faint)] font-mono ml-3">
              pipeline {p.pipelineId.slice(0, 8)}
            </span>
          )}
        </div>
        <div className="flex items-center gap-2">
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
                title="Smart resume: skip phases that already succeeded, reuse existing profile/plan, only re-run from the first non-done phase. Cheap and fast."
              >
                <RefreshCw size={12} /> Re-run
              </Button>
              <Button
                size="sm"
                variant="ghost"
                onClick={() => {
                  // Force restart = discard everything, run from research.
                  // Distinct from Re-run because it re-pays the LLM cost
                  // on research + plan even if those succeeded. Use only
                  // when the existing profile/plan are bad and you want
                  // them regenerated. Confirmation is mandatory.
                  const ok = window.confirm(
                    "Force restart will run the entire pipeline from scratch:\n\n"
                    + "  • A NEW profile_id (research agent runs again)\n"
                    + "  • A NEW plan_id (planner agent makes 8+ LLM calls)\n"
                    + "  • Generate, Provision, KG-publish all re-run\n\n"
                    + "Cost: ~5-10 minutes + several dollars in LLM tokens.\n\n"
                    + "If you only want to retry a failed phase, click Re-run instead "
                    + "— that smart-resumes from where this build broke and reuses "
                    + "the existing profile + plan.\n\n"
                    + "Continue with Force restart?",
                  );
                  if (!ok) return;
                  const u = p.url;
                  const c = p.company ?? undefined;
                  const d = p.days;
                  p.reset();
                  if (u) void p.start({ url: u, company: c, days: d });
                }}
                title="Discard this pipeline and run from research again with the same URL. Pays the full LLM cost — use Re-run for cheap resume."
                className="!text-[var(--color-warning)] hover:!bg-[var(--color-warning)]/10"
              >
                <AlertCircle size={12} /> Force restart
              </Button>
              <Button
                size="sm"
                variant="secondary"
                onClick={onReset}
                title="Go to the build form to start a fresh build for a different URL — this pipeline keeps running server-side and you can find it again under /pipelines"
              >
                <Sparkles size={12} /> New build
              </Button>
            </>
          )}
        </div>
      </div>

      <MetricsStrip metrics={metrics} status={p.status} />

      {/* Visual stepper — at-a-glance phase progress with per-step
          duration / error counts. Click a step to focus its log panel.
          The detailed PhaseRow grid below stays for the resume/re-run
          actions and the artifact links. */}
      <Card className="p-4">
        <PipelineStepper
          phases={p.phases}
          metrics={metrics.phases}
          focusedPhase={focusPhase}
          onStepClick={(phase) =>
            setFocusPhase(focusPhase === phase ? null : phase)
          }
        />
      </Card>

      <div className="grid grid-cols-1 lg:grid-cols-[420px_1fr] gap-6">
        <Card className="p-4">
          <div className="space-y-1">
            {PIPELINE_PHASES.map((phase, i) => {
              // Re-run button visibility:
              //   - failed phase   → "Re-run" (primary affordance — fix path)
              //   - done phase     → "Re-run" too (testing iteration —
              //                       e.g. push updated KG model rules
              //                       without redoing research+plan)
              //   - pending/running/skipped → no button (nothing to redo)
              // Both flavors call the same backend (`startFromPhase`); the
              // only difference is wording so the user knows what they're
              // doing.
              const phaseStatus = p.phases[phase].status;
              const isFailed = phaseStatus === "failed";
              const isDone = phaseStatus === "done";
              const showResume = isFailed || isDone;
              let canResume = false;
              let disabledReason: string | undefined;
              if (showResume) {
                if (phase === "plan" && !p.profileId) {
                  disabledReason = "research never produced a profile";
                } else if (
                  ["approve", "generate", "provision", "kg-publish"].includes(phase) &&
                  !p.planId
                ) {
                  disabledReason = "plan was never produced";
                } else {
                  canResume = true;
                }
              }
              // Inside PipelineRunView the context is destructured as
              // `p`, not `pipeline`. Earlier I wrote `pipeline.*` here and
              // it threw `ReferenceError: pipeline is not defined` on
              // every per-phase Re-run click — that's why "click does
              // nothing": the async callback rejected immediately.
              const onResume = canResume
                ? async () => {
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
                : undefined;
              return (
                <PhaseRow
                  key={phase}
                  phase={phase}
                  state={p.phases[phase]}
                  metric={metrics.phases.find((m) => m.phase === phase)}
                  isLast={i === PIPELINE_PHASES.length - 1}
                  onResume={onResume}
                  resumeDisabledReason={disabledReason}
                  showResume={showResume}
                  resumeIsRetest={isDone}
                  focused={focusPhase === phase}
                  onClick={() => setFocusPhase(
                    focusPhase === phase ? null : phase,
                  )}
                />
              );
            })}
          </div>
        </Card>

        <PhaseDetail phases={p.phases} focusPhase={focusPhase} onClearFocus={() => setFocusPhase(null)} />
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
        // visually fade into the background. They're not errors — they
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
              ? `Re-test ${PHASE_LABELS[phase]} only — useful when iterating on the code that affects this phase. Reuses profile + plan; new pipeline_id linked to this one as parent.`
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
 *  status, freezes at the final values. Kept compact — meant for a
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
                title="Unlock — let the panel auto-track the active phase"
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
