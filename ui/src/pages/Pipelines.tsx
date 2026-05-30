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
  type PipelineSummary, type ProfileSummary,
  type PipelinePhase, PIPELINE_PHASES,
} from "@/lib/api";
import { Card } from "@/components/Card";
import { Button } from "@/components/Button";
import { Badge } from "@/components/Badge";
import { LogView } from "@/components/LogView";
import { Pagination } from "@/components/Pagination";
import { PipelineKpiCard } from "@/components/PipelineKpiCard";
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

  const pipelines = list.data ?? [];

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Builds</h1>
        <p className="text-[var(--color-text-muted)] mt-1 text-sm max-w-3xl">
          Every full demo build this API process has run, with duration and
          status. Survives tab switches; lost when the API restarts.{" "}
          <span className="text-[var(--color-text-faint)] tabular-nums">
            {pipelines.length} total
          </span>
        </p>
      </div>

      <PipelinesHybridList
        pipelines={pipelines}
        loading={list.isLoading}
        selected={selected}
        onSelect={(id) => setParams(id ? { p: id } : {})}
        filterActive={false}
      />

      {selected && (
        <section aria-label="Pipeline inspection">
          <div className="flex items-baseline justify-between mb-3">
            <h2 className="text-sm font-medium uppercase tracking-wider text-[var(--color-text-muted)]">
              Inspecting
              <span className="ml-2 font-mono text-[10px] tracking-normal normal-case text-[var(--color-text-faint)]">
                {selected.slice(0, 8)}
              </span>
            </h2>
            <button
              type="button"
              onClick={() => setParams({})}
              className="text-[11px] text-[var(--color-text-muted)] hover:text-[var(--color-text)] inline-flex items-center gap-1"
            >
              <X size={12} /> Close
            </button>
          </div>
          <PipelineEventsPanel pipelineId={selected} />
        </section>
      )}
    </div>
  );
}

// Hybrid list — KPI strip stays above (unchanged), and the previous
// 420px master-detail "list on the left, events on the right" becomes
// a full-width cards-on-top + paginated-table-below layout. Events
// drill-down renders inline below the table when something is
// selected. Pattern matches Profiles + Plans.
function PipelinesHybridList({
  pipelines, loading, selected, onSelect, filterActive,
}: {
  pipelines: PipelineSummary[];
  loading: boolean;
  selected: string | null;
  onSelect: (id: string | null) => void;
  filterActive: boolean;
}) {
  const qc = useQueryClient();
  // Inline cancel — same UX the v1 list had, lifted into the table row.
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

  const HIGHLIGHTS_LIMIT = 6;
  const highlights = pipelines.slice(0, HIGHLIGHTS_LIMIT);
  const showTable = pipelines.length > HIGHLIGHTS_LIMIT;
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);
  const totalPages = Math.max(1, Math.ceil(pipelines.length / pageSize));
  const safePage = Math.min(page, totalPages);
  const pageRows = pipelines.slice((safePage - 1) * pageSize, safePage * pageSize);

  if (loading) {
    return (
      <Card>
        <div className="p-8 text-center text-[var(--color-text-faint)]">Loading…</div>
      </Card>
    );
  }
  if (pipelines.length === 0) {
    return (
      <Card>
        <div className="p-12 text-center text-[var(--color-text-muted)] text-sm">
          {filterActive
            ? "No pipelines match the current status filter."
            : "No pipelines in this API process."}
        </div>
      </Card>
    );
  }

  return (
    <>
      {/* Highlights */}
      <section aria-label="Recent pipelines">
        <div className="flex items-baseline justify-between mb-3">
          <h2 className="text-sm font-medium uppercase tracking-wider text-[var(--color-text-muted)]">
            Recent
          </h2>
          <span className="text-[11px] text-[var(--color-text-faint)] font-mono tabular-nums">
            {highlights.length} of {pipelines.length}
          </span>
        </div>
        <div className="grid gap-2.5 sm:grid-cols-2 lg:grid-cols-3">
          {highlights.map((p) => (
            <PipelineKpiCard
              key={p.pipeline_id}
              pipeline={p}
              compact
              selected={selected === p.pipeline_id}
              onClick={() => onSelect(p.pipeline_id)}
            />
          ))}
        </div>
      </section>

      {/* Paginated table */}
      {showTable && (
        <section aria-label="All pipelines">
          <div className="flex items-baseline justify-between mb-3">
            <h2 className="text-sm font-medium uppercase tracking-wider text-[var(--color-text-muted)]">
              All builds
            </h2>
            <span className="text-[11px] text-[var(--color-text-faint)] font-mono tabular-nums">
              {pipelines.length} total
            </span>
          </div>
          <Card className="p-0 overflow-hidden">
            <table className="w-full text-sm">
              <thead className="text-xs text-[var(--color-text-faint)] uppercase tracking-wider border-b border-[var(--color-border)]">
                <tr>
                  <th className="text-left font-medium px-4 py-3">Build</th>
                  <th className="text-left font-medium px-4 py-3">Host</th>
                  <th className="text-left font-medium px-4 py-3">Status</th>
                  <th className="text-right font-medium px-4 py-3">Duration</th>
                  <th className="text-right font-medium px-4 py-3">Events</th>
                  <th className="text-right font-medium px-4 py-3">Started</th>
                  <th className="w-10"></th>
                </tr>
              </thead>
              <tbody>
                {pageRows.map((p) => {
                  let host = p.url;
                  try { host = new URL(p.url).host.replace(/^www\./, ""); } catch { /* keep raw */ }
                  const isSelected = selected === p.pipeline_id;
                  return (
                    <tr
                      key={p.pipeline_id}
                      onClick={() => onSelect(p.pipeline_id)}
                      className={cn(
                        "border-b border-[var(--color-border)] last:border-0 cursor-pointer transition-colors",
                        isSelected
                          ? "bg-[var(--color-accent-bg)]/40"
                          : "hover:bg-white/[0.02]",
                      )}
                    >
                      <td className="px-4 py-3 font-mono text-xs">
                        {p.pipeline_id.slice(0, 8)}
                      </td>
                      <td className="px-4 py-3 text-xs text-[var(--color-text-muted)] truncate max-w-[280px]">
                        {host}
                      </td>
                      <td className="px-4 py-3">
                        <PipelineStatusBadge status={p.status} />
                      </td>
                      <td className="px-4 py-3 text-right tabular-nums text-xs">
                        {formatDuration(durationMs(p)) ?? "—"}
                      </td>
                      <td className="px-4 py-3 text-right tabular-nums text-xs">
                        {p.event_count}
                      </td>
                      <td className="px-4 py-3 text-right text-xs text-[var(--color-text-muted)]">
                        {new Date(p.started_at).toLocaleString()}
                      </td>
                      <td className="px-2 py-3 text-right">
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
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            <Pagination
              page={safePage}
              pageSize={pageSize}
              total={pipelines.length}
              onPageChange={setPage}
              onPageSizeChange={(n) => { setPageSize(n); setPage(1); }}
            />
          </Card>
        </section>
      )}
    </>
  );
}

// PipelinesList removed — its responsibilities moved into the
// PipelinesHybridList above (cards on top + paginated table below,
// inline cancel button preserved on the table row's right edge).

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
