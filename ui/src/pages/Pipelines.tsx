/**
 * Pipeline history, every full demo build the API has seen since
 * its last restart, with duration, phase breakdown, and a drill-in
 * for the per-event log.
 *
 * Pulls from `GET /api/pipelines` and `/api/pipelines/{id}/events`.
 * The runs page (/runs) is for individual CLI subprocesses; this is
 * the higher-level orchestrated view.
 */

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate, useSearchParams } from "react-router-dom";
import { useMemo, useState } from "react";
import {
  Activity, CheckCircle2, AlertCircle, Loader2, Clock, Bug,
  Rewind, ChevronRight, FileSearch, RefreshCw, X,
  ScrollText, ClipboardList,
} from "lucide-react";

import { CrumbChip } from "@/components/CrumbChip";
import {
  listPipelines, listPlans, listProfiles, cancelPipeline,
  type PipelineSummary, type PlanSummary, type ProfileSummary,
  type PipelinePhase, PIPELINE_PHASES,
} from "@/lib/api";
import { Card } from "@/components/Card";
import { Button } from "@/components/Button";
import { Badge } from "@/components/Badge";
import { KpiCard } from "@/components/KpiCard";
import { LogView } from "@/components/LogView";
import { cn } from "@/lib/cn";
import { formatDuration } from "@/lib/diagnose";
import { usePipeline } from "@/lib/PipelineContext";

export function PipelinesPage() {
  const list = useQuery({
    queryKey: ["pipelines"],
    queryFn: listPipelines,
    refetchInterval: 5_000,
  });
  const [params, setParams] = useSearchParams();
  const selected = params.get("p");

  // Aggregate KPIs across the visible pipelines. Cheap, these lists
  // are bounded to a few hundred at most before the API restart wipes
  // them. Recomputing on every render is fine.
  const pipelines = list.data ?? [];
  const kpis = useMemo(() => {
    const running = pipelines.filter((p) => p.status === "running").length;
    const failed = pipelines.filter((p) => p.status === "failed").length;
    const done = pipelines.filter((p) => p.status === "done").length;
    const cancelled = pipelines.filter((p) => p.status === "cancelled").length;
    // Median duration of finished builds, a more stable number than mean
    // for build pipelines where one stuck build can drag the average up.
    const durations = pipelines
      .map((p) => durationMs(p))
      .filter((d): d is number => d !== null && !!d)
      .sort((a, b) => a - b);
    const medianDuration =
      durations.length === 0
        ? null
        : durations[Math.floor(durations.length / 2)];
    return { running, failed, done, cancelled, medianDuration, total: pipelines.length };
  }, [pipelines]);

  // Drilldown, caller picks one of running/failed/done; the list
  // below filters to that subset. `null` = no filter.
  const [statusFilter, setStatusFilter] = useState<null | "running" | "failed" | "done">(null);
  const filteredPipelines = useMemo(
    () =>
      statusFilter
        ? pipelines.filter((p) => p.status === statusFilter)
        : pipelines,
    [pipelines, statusFilter],
  );

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Pipelines</h1>
        <p className="text-[var(--color-text-muted)] mt-1 text-sm max-w-3xl">
          Every full demo build this API process has run, with duration and
          status. Survives tab switches; lost when the API restarts.
        </p>
      </div>

      {/* KPI strip, running / failed / median duration / total. The
          interactive ones filter the list below; click again to clear. */}
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        <KpiCard
          icon={Loader2}
          label="Running"
          value={kpis.running}
          tone={kpis.running > 0 ? "info" : "neutral"}
          onClick={kpis.running > 0 ? () => setStatusFilter(statusFilter === "running" ? null : "running") : undefined}
          selected={statusFilter === "running"}
          hint={statusFilter === "running" ? "filter active" : undefined}
        />
        <KpiCard
          icon={AlertCircle}
          label="Failed"
          value={kpis.failed}
          tone={kpis.failed > 0 ? "danger" : "neutral"}
          onClick={kpis.failed > 0 ? () => setStatusFilter(statusFilter === "failed" ? null : "failed") : undefined}
          selected={statusFilter === "failed"}
          hint={statusFilter === "failed" ? "filter active" : undefined}
        />
        <KpiCard
          icon={CheckCircle2}
          label="Done"
          value={kpis.done}
          tone="success"
          onClick={kpis.done > 0 ? () => setStatusFilter(statusFilter === "done" ? null : "done") : undefined}
          selected={statusFilter === "done"}
          hint={statusFilter === "done" ? "filter active" : undefined}
        />
        <KpiCard
          icon={Clock}
          label="Median duration"
          value={formatDuration(kpis.medianDuration)}
          tone="neutral"
          hint={`${kpis.total} total builds`}
        />
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-[420px_1fr] gap-6">
        <PipelinesList
          pipelines={filteredPipelines}
          loading={list.isLoading}
          selected={selected}
          onSelect={(id) => setParams(id ? { p: id } : {})}
        />
        {selected ? (
          <PipelineEventsPanel pipelineId={selected} />
        ) : (
          <Card className="p-12 text-center text-[var(--color-text-faint)] flex items-center justify-center">
            <div>
              <FileSearch className="mx-auto opacity-40 mb-3" size={28} />
              <div className="text-sm">Pick a pipeline to inspect.</div>
              <div className="text-xs mt-1 max-w-sm mx-auto">
                The Build page is where you start new pipelines; this page is
                history + post-mortem.
              </div>
            </div>
          </Card>
        )}
      </div>
    </div>
  );
}

function PipelinesList({
  pipelines, loading, selected, onSelect,
}: {
  pipelines: PipelineSummary[];
  loading: boolean;
  selected: string | null;
  onSelect: (id: string | null) => void;
}) {
  const qc = useQueryClient();
  /** Inline cancel from the list row — saves the click into the detail
   *  pane just to find the Cancel button. The detail pane still has
   *  the canonical Cancel button for users that landed there directly.
   *  Confirm prompt because this is destructive + visible-on-list. */
  async function cancelRow(pipeline_id: string, host: string, e: React.MouseEvent) {
    e.stopPropagation();
    if (!window.confirm(`Cancel build for ${host}? In-flight phases will stop.`)) return;
    try {
      await cancelPipeline(pipeline_id);
    } catch (err) {
      window.alert(`Couldn't cancel: ${err instanceof Error ? err.message : String(err)}`);
    } finally {
      qc.invalidateQueries({ queryKey: ["pipelines"] });
    }
  }

  return (
    <Card className="overflow-hidden">
      {loading ? (
        <div className="p-8 text-center text-[var(--color-text-faint)]">Loading…</div>
      ) : pipelines.length === 0 ? (
        <div className="p-8 text-center text-[var(--color-text-muted)] text-sm">
          No pipelines in this API process.
        </div>
      ) : (
        <ul className="divide-y divide-[var(--color-border)]">
          {pipelines.map((p) => {
            let host = p.url;
            try { host = new URL(p.url).host.replace(/^www\./, ""); } catch { /* keep raw */ }
            return (
              <li
                key={p.pipeline_id}
                onClick={() => onSelect(p.pipeline_id)}
                className={cn(
                  "px-4 py-3 cursor-pointer transition-colors",
                  selected === p.pipeline_id ? "bg-white/[0.05]" : "hover:bg-white/[0.02]",
                )}
              >
                <div className="flex items-center justify-between gap-2 mb-1">
                  <span className="font-mono text-xs">{p.pipeline_id.slice(0, 8)}</span>
                  <div className="flex items-center gap-1.5">
                    <PipelineStatusBadge status={p.status} />
                    {p.status === "running" && (
                      <button
                        onClick={(e) => cancelRow(p.pipeline_id, host, e)}
                        title="Cancel build"
                        aria-label={`Cancel build ${p.pipeline_id.slice(0, 8)}`}
                        className={cn(
                          "p-1 rounded transition-colors",
                          "text-[var(--color-danger)] hover:bg-[var(--color-danger-bg)]",
                        )}
                      >
                        <X size={12} />
                      </button>
                    )}
                  </div>
                </div>
                <div className="text-sm truncate">{p.url}</div>
                <div className="text-xs text-[var(--color-text-faint)] mt-0.5 flex items-center gap-3">
                  <span className="inline-flex items-center gap-1">
                    <Clock size={10} />
                    {formatDuration(durationMs(p))}
                  </span>
                  <span className="inline-flex items-center gap-1">
                    <Activity size={10} />
                    {p.event_count}
                  </span>
                  <span>{new Date(p.started_at).toLocaleTimeString()}</span>
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </Card>
  );
}

function PipelineStatusBadge({ status }: { status: string }) {
  if (status === "running") {
    return <Badge tone="info"><Loader2 size={10} className="animate-spin" /> running</Badge>;
  }
  if (status === "done") {
    return <Badge tone="success"><CheckCircle2 size={10} /> done</Badge>;
  }
  if (status === "failed") {
    return <Badge tone="danger"><AlertCircle size={10} /> failed</Badge>;
  }
  if (status === "cancelled") {
    return <Badge tone="warning"><Rewind size={10} /> cancelled</Badge>;
  }
  return <Badge>{status}</Badge>;
}

function durationMs(p: PipelineSummary): number | null {
  const start = new Date(p.started_at).getTime();
  const end = p.finished_at ? new Date(p.finished_at).getTime() : Date.now();
  return Number.isFinite(start) ? end - start : null;
}

interface PipelineEventsResponse {
  pipeline_id: string;
  status: string;
  events: Array<Record<string, unknown>>;
}

function PipelineEventsPanel({ pipelineId }: { pipelineId: string }) {
  const navigate = useNavigate();
  const qc = useQueryClient();
  const events = useQuery({
    queryKey: ["pipeline-events", pipelineId],
    queryFn: async () => {
      const res = await fetch(`/api/pipelines/${pipelineId}/events`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return (await res.json()) as PipelineEventsResponse;
    },
    refetchInterval: 5_000,
  });
  const summary = useQuery({
    queryKey: ["pipeline-summary", pipelineId],
    queryFn: async () => {
      const res = await fetch(`/api/pipelines/${pipelineId}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      return (await res.json()) as PipelineSummary;
    },
    refetchInterval: 5_000,
  });

  const cancelMut = useMutation({
    mutationFn: () => cancelPipeline(pipelineId),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["pipelines"] });
      qc.invalidateQueries({ queryKey: ["pipeline-summary", pipelineId] });
    },
  });

  // Compute per-phase rollups from the buffered events. We don't have
  // server timestamps yet (a v0.7 backend follow-up) so durations here
  // are based on event ordering, not wall-clock, but counts are exact.
  const phaseRollup = useMemo(() => {
    const empty: Record<PipelinePhase, { logs: number; errors: number; status: string }> =
      Object.fromEntries(
        PIPELINE_PHASES.map((p) => [p, { logs: 0, errors: 0, status: "pending" }]),
      ) as Record<PipelinePhase, { logs: number; errors: number; status: string }>;
    if (!events.data) return empty;
    for (const ev of events.data.events) {
      const t = ev.event as string;
      const phase = ev.phase as PipelinePhase | undefined;
      if (!phase || !(phase in empty)) continue;
      if (t === "log") {
        empty[phase].logs += 1;
        const line = String(ev.line ?? "");
        if (/error|exception|traceback|fail/i.test(line)) empty[phase].errors += 1;
      } else if (t === "phase") {
        if (ev.status === "started") empty[phase].status = "running";
        else if (ev.status === "done") empty[phase].status = "done";
        else if (ev.status === "failed") empty[phase].status = "failed";
      }
    }
    return empty;
  }, [events.data]);

  // Scroll handling lives inside <LogView> now, it auto-tails when the
  // user is near the bottom and pauses when they scroll up. We keep the
  // filter state here.
  const [filter, setFilter] = useState("");

  const filteredLogLines = useMemo(() => {
    if (!events.data) return [];
    const wanted = filter.toLowerCase();
    const out: string[] = [];
    for (const ev of events.data.events) {
      const phase = (ev.phase as string) || ", ";
      const line = (ev.line as string) ?? null;
      const t = ev.event as string;
      if (line == null && t === "log") continue;

      const formatted =
        t === "log"
          ? `[${phase}] ${line}`
          : t === "phase"
          ? `[${phase}] -- phase ${ev.status} ${ev.message ? "· " + ev.message : ""}`
          : t === "pipeline"
          ? `--- pipeline ${ev.status} ${ev.error ? "· " + ev.error : ""}`
          : t === "links"
          ? `--- links ${JSON.stringify(ev)}`
          : "";
      if (!formatted) continue;
      if (wanted && !formatted.toLowerCase().includes(wanted)) continue;
      out.push(formatted);
    }
    return out;
  }, [events.data, filter]);

  const s = summary.data;

  return (
    <Card className="flex flex-col h-[760px]">
      <div className="px-5 py-3 border-b border-[var(--color-border)] flex items-center justify-between gap-3">
        <div className="min-w-0">
          <div className="font-medium text-sm truncate">{s?.url ?? pipelineId}</div>
          <div className="text-xs text-[var(--color-text-faint)] flex items-center gap-3 mt-0.5">
            <span className="font-mono">{pipelineId.slice(0, 12)}</span>
            {s && (
              <>
                <PipelineStatusBadge status={s.status} />
                <span className="inline-flex items-center gap-1">
                  <Clock size={10} />
                  {formatDuration(durationMs(s))}
                </span>
                <span className="inline-flex items-center gap-1">
                  <Activity size={10} />
                  {s.event_count} events
                </span>
              </>
            )}
          </div>
          {/* Linked-resources strip. Surfaces this build's profile + plan
              when present; falls back to the most-recent profile + plan
              for the same URL host when this build itself didn't produce
              them (e.g. it failed before research finished, or was
              cancelled). The fallback is the answer to the recurring
              "a lot of builds have no profile or plan attached"
              complaint — even early-failed builds now point at SOMETHING
              the SE can drill into for the same prospect. */}
          {s && <LinkedResources summary={s} />}
        </div>
        <div className="flex items-center gap-2">
          {s?.status === "running" && (
            <Button size="sm" variant="danger" onClick={() => cancelMut.mutate()}>
              Cancel
            </Button>
          )}
          <OpenInBuildButton pipelineId={pipelineId} />
          {s?.parent_pipeline_id && (
            <Button
              size="sm"
              variant="ghost"
              onClick={() => {
                if (s.parent_pipeline_id) {
                  // Drill from a resume run back to its parent.
                  const params = new URLSearchParams();
                  params.set("p", s.parent_pipeline_id);
                  window.location.search = params.toString();
                }
              }}
              title={`Resumed from pipeline ${s.parent_pipeline_id}`}
            >
              ← Parent
            </Button>
          )}
        </div>
      </div>

      {/* Phase rollup row */}
      <div className="px-5 py-3 border-b border-[var(--color-border)] grid grid-cols-3 lg:grid-cols-6 gap-2">
        {PIPELINE_PHASES.map((p) => {
          const r = phaseRollup[p];
          const tone =
            r.status === "done" ? "text-[var(--color-success)]" :
            r.status === "failed" ? "text-[var(--color-danger)]" :
            r.status === "running" ? "text-[var(--color-info)]" :
            "text-[var(--color-text-faint)]";
          return (
            <div key={p} className="text-xs">
              <div className={cn("font-medium", tone)}>{p}</div>
              <div className="text-[10px] text-[var(--color-text-faint)] font-mono">
                {r.logs}L
                {r.errors > 0 && (
                  <span className="text-[var(--color-warning)] ml-2">
                    <Bug size={10} className="inline" /> {r.errors}
                  </span>
                )}
              </div>
            </div>
          );
        })}
      </div>

      {/* Filter + log */}
      <div className="px-5 py-2 border-b border-[var(--color-border)] flex items-center gap-2">
        <FileSearch size={12} className="text-[var(--color-text-faint)]" />
        <input
          value={filter}
          onChange={(e) => setFilter(e.target.value)}
          placeholder="Filter log… (substring, case-insensitive)"
          className="flex-1 bg-transparent text-xs outline-none placeholder:text-[var(--color-text-faint)]"
        />
        {filter && (
          <button
            onClick={() => setFilter("")}
            className="text-[10px] text-[var(--color-text-faint)] hover:text-[var(--color-text)]"
          >
            clear
          </button>
        )}
        <span className="text-[10px] text-[var(--color-text-faint)] font-mono">
          {filteredLogLines.length}/{events.data?.events.length ?? 0} lines
        </span>
      </div>

      <LogView
        lines={events.isLoading ? [] : filteredLogLines}
        emptyText={
          events.isLoading
            ? "Loading…"
            : filter
            ? "No lines match the filter."
            : "No events."
        }
        maxHeight="100%"
        className="flex-1"
      />
    </Card>
  );
}

// ChevronRight isn't used directly here, keep import to avoid forgetting we
// might add a "deeper drill" view later. Stub usage to silence lint.
void ChevronRight;


/** Loads the chosen pipeline into the live PipelineRunView so users
 *  see the full phase rollup + per-phase Resume buttons that the
 *  Build page already renders. Past pipelines (terminal) are
 *  snapshot-replayed; running ones tail live. */
function OpenInBuildButton({ pipelineId }: { pipelineId: string }) {
  const navigate = useNavigate();
  const pipeline = usePipeline();
  return (
    <Button
      size="sm"
      onClick={async () => {
        await pipeline.loadPipeline(pipelineId);
        navigate("/new");
      }}
      title="Open in the Build page (full phase view + Resume buttons)"
    >
      <RefreshCw size={12} /> Open in Build
    </Button>
  );
}


/** Render a Profile + Plan crumb chip row for a pipeline.
 *
 *  Three resolution modes per slot:
 *    1. Pipeline has its own profile_id / plan_id → render directly.
 *    2. Pipeline's own is null but a profile (or plan) exists for the
 *       same URL host → render the latest, plus a small "from latest run
 *       for {host}" note so the SE knows this is a fallback link.
 *    3. Nothing matches → render a faint "no profile yet for {host}" hint.
 *
 *  The fallback path is what fixes the recurring "build detail has no
 *  profile / plan" complaint: builds that failed mid-research, or were
 *  cancelled, never produced their own profile_id, but for prospects we've
 *  researched before there's still a profile to point at. */
function LinkedResources({ summary }: { summary: PipelineSummary }) {
  const host = useMemo(() => {
    try { return new URL(summary.url).hostname.replace(/^www\./, "").toLowerCase(); }
    catch { return ""; }
  }, [summary.url]);

  // Fetch only when we need a fallback — i.e. when the pipeline's own
  // profile_id or plan_id is missing. React Query handles caching across
  // selections so the SE flipping between rows doesn't re-fetch each time.
  const needsFallback = !summary.profile_id || !summary.plan_id;
  const profiles = useQuery({
    queryKey: ["profiles"],
    queryFn: listProfiles,
    enabled: needsFallback,
  });
  const profilesForHost = useMemo(() => {
    if (!host || !profiles.data) return [] as ProfileSummary[];
    return profiles.data.filter((p) => {
      try { return new URL(p.primary_url).hostname.replace(/^www\./, "").toLowerCase() === host; }
      catch { return false; }
    });
  }, [host, profiles.data]);

  // Latest profile for the URL host (already newest-first from the API).
  const latestProfileId = profilesForHost[0]?.profile_id ?? null;
  const displayedProfileId = summary.profile_id ?? latestProfileId;
  const profileIsFallback = !summary.profile_id && !!latestProfileId;

  const plans = useQuery({
    queryKey: ["plans-by-profile", displayedProfileId],
    queryFn: () => listPlans({ source_profile_id: displayedProfileId ?? "" }),
    enabled: needsFallback && !!displayedProfileId,
  });
  const latestPlanId = (plans.data ?? []).filter((p) => !p.pending)[0]?.plan_id ?? null;
  const displayedPlanId = summary.plan_id ?? latestPlanId;
  const planIsFallback = !summary.plan_id && !!latestPlanId;

  return (
    <div className="mt-2.5 flex items-center gap-2 flex-wrap">
      {displayedProfileId ? (
        <CrumbChip
          to={`/profiles/${displayedProfileId}`}
          label={profileIsFallback ? "latest profile" : "profile"}
          value={displayedProfileId}
          icon={ScrollText}
          title={
            profileIsFallback
              ? `This build didn't produce its own profile — showing the most recent profile for ${host}.`
              : "Open the profile this build researched"
          }
        />
      ) : host && profiles.isFetched ? (
        <span className="inline-flex items-center gap-1.5 h-7 px-2.5 rounded-md font-mono text-[10px] uppercase tracking-[0.06em] bg-[var(--color-canvas-elev1)] border border-dashed border-[var(--color-border)] text-[var(--color-text-faint)]">
          no profile yet for {host}
        </span>
      ) : null}
      {displayedPlanId ? (
        <CrumbChip
          to={`/plans/${displayedPlanId}`}
          label={planIsFallback ? "latest plan" : "plan"}
          value={displayedPlanId.slice(0, 8)}
          icon={ClipboardList}
          title={
            planIsFallback
              ? `This build didn't produce its own plan — showing the most recent plan for ${host}.`
              : "Open the demo plan this build produced"
          }
        />
      ) : displayedProfileId && plans.isFetched ? (
        <span className="inline-flex items-center gap-1.5 h-7 px-2.5 rounded-md font-mono text-[10px] uppercase tracking-[0.06em] bg-[var(--color-canvas-elev1)] border border-dashed border-[var(--color-border)] text-[var(--color-text-faint)]">
          no plan yet for this profile
        </span>
      ) : null}
    </div>
  );
}
