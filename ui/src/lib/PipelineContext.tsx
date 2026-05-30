/**
 * Global pipeline state, survives page navigations.
 *
 * Why this exists: the SE-builder pipeline runs ~10-15 minutes. Holding
 * state in NewDemo.tsx's useState meant a tab switch / route change
 * unmounted the component and lost everything visible (the work kept
 * going server-side, but the user couldn't see it).
 *
 * This Provider:
 *   - Holds the active pipeline ID + accumulated events at app scope.
 *   - Owns the SSE connection, opens it once, keeps it open across
 *     route changes.
 *   - On app load, calls /api/pipelines and resumes following any
 *     pipeline that's still running. So even closing the browser and
 *     reopening it lands you back on the in-flight build.
 *
 * Pipeline ID is also persisted to localStorage so a hard reload picks
 * up where you left off, even if the API doesn't list it as the most-
 * recent (e.g. someone else started one in another window).
 */

import {
  createContext, useCallback, useContext, useEffect, useRef, useState,
} from "react";
import type { ReactNode } from "react";

import {
  createPipeline, getPipeline, getPipelineEvents, getPipelinePhases, listPipelines,
  runPipelineFromPhase, streamPipeline,
  type PipelineEvent, type PipelinePhase, type PipelineSummary,
  PIPELINE_PHASES,
} from "@/lib/api";

export type PhaseStatus = "pending" | "running" | "done" | "failed" | "skipped";

export interface PhaseState {
  status: PhaseStatus;
  message: string;
  logs: string[];
  artifact?: string;
  error?: string;
  /** Wall-clock when we first saw `phase:started` for this phase, or
   *  when the first event arrived if we missed `started` (e.g.
   *  reconnecting mid-stream). Used only for client-side metrics,    *  the server still owns canonical timing. */
  startedAt?: number;
  /** Wall-clock when we saw `phase:done` or `phase:failed`. */
  finishedAt?: number;
}

const INITIAL_PHASES: Record<PipelinePhase, PhaseState> = {
  "research":   { status: "pending", message: "", logs: [] },
  "plan":       { status: "pending", message: "", logs: [] },
  "approve":    { status: "pending", message: "", logs: [] },
  "generate":   { status: "pending", message: "", logs: [] },
  "provision":  { status: "pending", message: "", logs: [] },
  "kg-publish": { status: "pending", message: "", logs: [] },
};

export interface PipelineUiState {
  /** Server-assigned ID, or null if no pipeline is being followed. */
  pipelineId: string | null;
  /** Mirrors the server-side .status. */
  status: "idle" | "running" | "done" | "failed" | "cancelled";
  url: string;
  company: string | null;
  days: number;
  phases: Record<PipelinePhase, PhaseState>;
  links: Record<string, string>;
  planId: string | null;
  profileId: string | null;
  error: string | null;
  /** Wall-clock when we first saw `pipeline:started` (or when the
   *  earliest event arrived during a mid-stream reconnect). */
  startedAt?: number;
  /** Wall-clock when the pipeline reached a terminal status. */
  finishedAt?: number;
}

const INITIAL: PipelineUiState = {
  pipelineId: null,
  status: "idle",
  url: "",
  company: null,
  days: 1,
  phases: INITIAL_PHASES,
  links: {},
  planId: null,
  profileId: null,
  error: null,
};

/** When a pipeline reaches a terminal state, any phase still showing
 *  "running" is a lie (the orchestrator may have crashed mid-phase
 *  without emitting phase:failed). Force-flip those to `s` and stamp a
 *  finishedAt so the per-row spinners stop and durations stay sane. */
function forceTerminatePhases(
  phases: Record<PipelinePhase, PhaseState>, s: PhaseStatus, now: number,
): Record<PipelinePhase, PhaseState> {
  const out = { ...phases };
  for (const p of Object.keys(out) as PipelinePhase[]) {
    if (out[p].status === "running") {
      out[p] = { ...out[p], status: s, finishedAt: out[p].finishedAt ?? now };
    }
  }
  return out;
}

/** Wall-clock for an event: the server-stamped `ts` (injected by the
 *  repo on replay) so reloading mid-build keeps real timings, falling
 *  back to `now` for any event without one (shouldn't happen post-fix). */
function evTs(ev: PipelineEvent, now: number): number {
  const t = (ev as { ts?: string }).ts;
  if (!t) return now;
  const ms = new Date(t).getTime();
  return Number.isNaN(ms) ? now : ms;
}

interface PipelineContextValue extends PipelineUiState {
  /** Start a new pipeline. Returns the pipeline_id.
   *  `stop_after_phase` cuts the build short after that phase completes
   *  successfully, pass "research" for a profile-only research run. */
  start: (body: {
    url: string;
    company?: string;
    days?: number;
    volume_per_day?: number;
    stop_after_phase?: PipelinePhase;
  }) => Promise<string>;
  /** Resume from a specific phase. New pipeline_id; parent linkage on the server. */
  startFromPhase: (body: {
    phase: PipelinePhase;
    url?: string;
    company?: string;
    days?: number;
    profile_id?: string;
    profile_path?: string;
    plan_id?: string;
    parent_pipeline_id?: string;
    volume_per_day?: number;
  }) => Promise<string>;
  /** Load any past pipeline (running, done, failed) into the live view
   *  by snapshot-replaying its events from the server. If the pipeline
   *  is still running, switches to live tail after the replay. */
  loadPipeline: (pipelineId: string) => Promise<void>;
  /** Smart resume: look at a pipeline's phase rollup, find the first
   *  non-`done` phase, and start a new pipeline from there with the
   *  profile_id/plan_id artifacts inherited from the source.
   *
   *  This is what the top-level Re-run button SHOULD do, never redo
   *  a successful research/plan because the user is paying the LLM
   *  cost both times. If everything was done already, falls back to
   *  null (caller can choose: run fresh or no-op). */
  smartResume: (pipelineId: string) => Promise<string | null>;
  /** Reconcile the followed pipeline against the server's status — flips
   *  a stuck "running" view to its real terminal state (used after a
   *  cancel, or when a stream dies without a terminal event). */
  reconcile: (pipelineId: string) => Promise<void>;
  /** Reset the UI back to the form. Doesn't cancel the server-side pipeline. */
  reset: () => void;
}

const Ctx = createContext<PipelineContextValue | null>(null);

const STORAGE_KEY = "clarion.activePipelineId";

export function PipelineProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<PipelineUiState>(INITIAL);
  const closeRef = useRef<(() => void) | null>(null);

  /** Apply one event from the SSE stream onto the local UI state. */
  const applyEvent = useCallback((ev: PipelineEvent) => {
    setState((prev) => {
      const now = Date.now();
      // Prefer the server-stamped event time so replaying the log on a
      // refresh reconstructs the REAL elapsed, not "now". (Without this
      // the duration ticker resets to 0 on every reload.)
      const at = evTs(ev, now);
      if (ev.event === "pipeline") {
        if (ev.status === "started") {
          return {
            ...prev,
            status: "running",
            url: ev.url,
            company: ev.company,
            days: ev.days,
            startedAt: prev.startedAt ?? at,
          };
        }
        const forceTerminate = (s: PhaseStatus) => forceTerminatePhases(prev.phases, s, at);

        if (ev.status === "done") {
          return {
            ...prev, status: "done",
            phases: forceTerminate("done"),
            finishedAt: prev.finishedAt ?? at,
          };
        }
        if (ev.status === "failed") {
          return {
            ...prev, status: "failed", error: ev.error,
            phases: forceTerminate("failed"),
            finishedAt: prev.finishedAt ?? at,
          };
        }
        if (ev.status === "cancelled") {
          return {
            ...prev, status: "cancelled",
            phases: forceTerminate("failed"),
            finishedAt: prev.finishedAt ?? at,
          };
        }
      }
      if (ev.event === "phase") {
        // Map server `phase:*` event statuses to UI PhaseStatus.
        // `skipped` is its own state, without an explicit branch it
        // falls through to "failed" and the row renders as red error,
        // which is wrong for "this phase didn't run because we resumed
        // from a later one."
        const ps: PhaseStatus =
          ev.status === "started" ? "running" :
          ev.status === "done"    ? "done" :
          ev.status === "skipped" ? "skipped" : "failed";
        const cur = prev.phases[ev.phase];
        const phases = {
          ...prev.phases,
          [ev.phase]: {
            ...cur,
            status: ps,
            message: ("message" in ev && ev.message) ? ev.message : cur.message,
            artifact:
              ("profile_id" in ev && ev.profile_id) ? ev.profile_id :
              ("plan_id" in ev && ev.plan_id)       ? ev.plan_id :
              cur.artifact,
            error: ev.status === "failed" ? ev.error : cur.error,
            startedAt: ev.status === "started" ? (cur.startedAt ?? at) : cur.startedAt,
            finishedAt: ev.status !== "started" ? (cur.finishedAt ?? at) : cur.finishedAt,
          },
        };
        const next: Partial<PipelineUiState> = { phases };
        if (ev.status === "done" && "profile_id" in ev && ev.profile_id) next.profileId = ev.profile_id;
        if (ev.status === "done" && "plan_id"    in ev && ev.plan_id)    next.planId    = ev.plan_id;
        return { ...prev, ...next };
      }
      if (ev.event === "log") {
        const cur = prev.phases[ev.phase];
        return {
          ...prev,
          phases: {
            ...prev.phases,
            [ev.phase]: { ...cur, logs: [...cur.logs, ev.line] },
          },
        };
      }
      if (ev.event === "links") {
        const { event: _e, plan_id, profile_id, ...rest } = ev as Record<string, string>;
        void _e; void plan_id; void profile_id;
        return { ...prev, links: rest as Record<string, string> };
      }
      return prev;
    });
  }, []);

  /** Connect to a pipeline's SSE stream. Replays from event 0; safe to
   *  call after any reset. Closes any prior connection first.
   *
   *  `seed` lets resume-on-mount preload canonical timestamps from the
   *  /api/pipelines summary so the duration ticker shows real elapsed
   *  time after a refresh, not the moment we re-attached. Without this
   *  the SSE replay re-stamps `pipeline:started` with Date.now(), which
   *  silently zeroes the timer. */
  /** The SSE stream can close WITHOUT delivering a terminal pipeline
   *  event — the orchestrator process died, the network dropped, or the
   *  build was cancelled out-of-band (e.g. from the assistant). Without
   *  this we'd stay stuck on "running" forever, showing a Stop button
   *  that calls cancel on a dead pipeline and never updates. Reconcile
   *  against the server's canonical status and synthesize the matching
   *  terminal transition so the UI converges. */
  const reconcileTerminal = useCallback(async (pipelineId: string) => {
    const summary = await getPipeline(pipelineId).catch(() => null);
    const status = summary?.status;
    if (status !== "done" && status !== "failed" && status !== "cancelled") return;
    setState((prev) => {
      // Only act if we're still following THIS pipeline as running.
      if (prev.pipelineId !== pipelineId || prev.status !== "running") return prev;
      const now = Date.now();
      const phaseTone: PhaseStatus = status === "done" ? "done" : "failed";
      return {
        ...prev,
        status,
        error: status === "failed"
          ? (prev.error ?? "Build failed (stream closed before the error detail arrived).")
          : prev.error,
        phases: forceTerminatePhases(prev.phases, phaseTone, now),
        finishedAt: prev.finishedAt
          ?? (summary?.finished_at ? new Date(summary.finished_at).getTime() : now),
      };
    });
  }, []);

  const follow = useCallback((pipelineId: string, seed?: Partial<PipelineUiState>) => {
    closeRef.current?.();
    setState((prev) => ({
      ...INITIAL, pipelineId, status: "running", url: prev.url, ...seed,
    }));
    localStorage.setItem(STORAGE_KEY, pipelineId);
    closeRef.current = streamPipeline(pipelineId, applyEvent, () => {
      // EventSource closed. If a terminal event already landed this is a
      // no-op; otherwise reconcile against the server so we don't get
      // stuck showing "running" with a dead Stop button.
      closeRef.current = null;
      void reconcileTerminal(pipelineId);
    });
  }, [applyEvent, reconcileTerminal]);

  /** App-mount: resume an in-flight pipeline if there is one. We check
   *  localStorage first (sticky pointer for the user's last build) and
   *  fall back to whatever the server reports as still running. */
  useEffect(() => {
    let cancelled = false;
    (async () => {
      const stored = localStorage.getItem(STORAGE_KEY);
      const list = await listPipelines().catch(() => [] as PipelineSummary[]);
      if (cancelled) return;

      // Prefer the stored pipeline if it's still in the list AND running.
      const pinned = stored ? list.find((p) => p.pipeline_id === stored) : undefined;
      const running = list.find((p) => p.status === "running");
      const target = (pinned && pinned.status === "running") ? pinned : running;

      // Server-canonical timestamps so a refresh mid-build doesn't reset
      // the duration ticker.
      const seedFrom = (s: PipelineSummary): Partial<PipelineUiState> => ({
        startedAt: s.started_at ? new Date(s.started_at).getTime() : undefined,
        finishedAt: s.finished_at ? new Date(s.finished_at).getTime() : undefined,
      });

      if (target) {
        follow(target.pipeline_id, seedFrom(target));
      } else if (pinned) {
        // Pipeline finished while we were away, still want to show its
        // final state. Fetch the events snapshot and replay them all.
        const snap = await fetch(`/api/pipelines/${pinned.pipeline_id}/events`)
          .then((r) => r.json())
          .catch(() => null);
        if (cancelled || !snap) return;
        setState((prev) => ({
          ...INITIAL,
          pipelineId: pinned.pipeline_id,
          status: "running",
          url: prev.url,
          ...seedFrom(pinned),
        }));
        for (const ev of (snap.events as PipelineEvent[])) applyEvent(ev);
      }
    })();
    return () => {
      cancelled = true;
      closeRef.current?.();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const start = useCallback(async (body: {
    url: string;
    company?: string;
    days?: number;
    volume_per_day?: number;
    stop_after_phase?: PipelinePhase;
  }) => {
    const summary = await createPipeline(body);
    follow(summary.pipeline_id, {
      startedAt: summary.started_at ? new Date(summary.started_at).getTime() : undefined,
    });
    return summary.pipeline_id;
  }, [follow]);

  const startFromPhase = useCallback(async (body: {
    phase: PipelinePhase;
    url?: string;
    company?: string;
    days?: number;
    profile_id?: string;
    profile_path?: string;
    plan_id?: string;
    parent_pipeline_id?: string;
  }) => {
    const summary = await runPipelineFromPhase(body);
    follow(summary.pipeline_id, {
      startedAt: summary.started_at ? new Date(summary.started_at).getTime() : undefined,
    });
    return summary.pipeline_id;
  }, [follow]);

  /** Snapshot-replay any past pipeline. If still running, switches to
   *  live tail afterwards (handled by `follow` for running pipelines).
   *  For terminal pipelines we replay the events ourselves so the UI
   *  shows the full history without opening a doomed SSE stream. */
  const loadPipeline = useCallback(async (pipelineId: string) => {
    closeRef.current?.();
    closeRef.current = null;
    const summary = await getPipeline(pipelineId).catch(() => null);
    if (!summary) return;
    if (summary.status === "running") {
      // Live: SSE replays from event 0, so we're done here.
      follow(pipelineId, {
        startedAt: summary.started_at ? new Date(summary.started_at).getTime() : undefined,
      });
      return;
    }
    // Terminal: fetch the full event log, prime state with canonical
    // start/finish times, then replay.
    const snap = await getPipelineEvents(pipelineId).catch(() => null);
    setState(() => ({
      ...INITIAL,
      pipelineId,
      status: "running", // gets terminal-flipped by the replayed pipeline:done/failed event
      url: summary.url,
      company: summary.company,
      days: summary.days,
      startedAt: summary.started_at ? new Date(summary.started_at).getTime() : undefined,
      finishedAt: summary.finished_at ? new Date(summary.finished_at).getTime() : undefined,
    }));
    localStorage.setItem(STORAGE_KEY, pipelineId);
    if (snap) {
      for (const ev of snap.events as PipelineEvent[]) applyEvent(ev);
    }
  }, [applyEvent, follow]);

  /** Find the first non-done phase in a pipeline's rollup and resume
   *  from there. Inherits profile_id, plan_id, url, company, days from
   *  the source pipeline so the user never re-pays for completed work.
   *
   *  The order of preference for "what's the resume phase":
   *    1. Phase with status=failed (the one that broke)
   *    2. Phase with status=running (orphaned mid-run; least common but possible)
   *    3. First phase with status=pending (everything before it succeeded)
   *  If all phases are done, returns null and the caller decides
   *  whether to start a fresh build. */
  const smartResume = useCallback(async (pipelineId: string): Promise<string | null> => {
    const [summary, phases] = await Promise.all([
      getPipeline(pipelineId).catch(() => null),
      getPipelinePhases(pipelineId).catch(() => [] as Awaited<ReturnType<typeof getPipelinePhases>>),
    ]);
    if (!summary) return null;

    // Pull the artifacts the source pipeline produced. Some phases may
    // have failed before persisting their artifact, server returned
    // null in that case and startFromPhase will validate.
    const profileFromPhases = phases.find((p) => p.artifact?.profile_id)?.artifact?.profile_id;
    const planFromPhases    = phases.find((p) => p.artifact?.plan_id)?.artifact?.plan_id;
    const profile_id = summary.profile_id ?? profileFromPhases ?? undefined;
    const plan_id    = summary.plan_id    ?? planFromPhases    ?? undefined;

    // Find resume phase. Prefer failed; fall back to running; fall back
    // to first non-done. If none, all phases are done already.
    const failed  = phases.find((p) => p.status === "failed");
    const running = phases.find((p) => p.status === "running");
    const pending = PIPELINE_PHASES.find((ph) => {
      const row = phases.find((p) => p.phase === ph);
      return !row || (row.status !== "done" && row.status !== "skipped");
    });
    const resumePhase: PipelinePhase | undefined =
      (failed?.phase as PipelinePhase | undefined)
      ?? (running?.phase as PipelinePhase | undefined)
      ?? pending;
    if (!resumePhase) return null;

    const newId = await startFromPhase({
      phase: resumePhase,
      url: summary.url,
      company: summary.company ?? undefined,
      days: summary.days,
      profile_id,
      plan_id,
      parent_pipeline_id: pipelineId,
    });
    return newId;
  }, [startFromPhase]);

  const reset = useCallback(() => {
    closeRef.current?.();
    closeRef.current = null;
    localStorage.removeItem(STORAGE_KEY);
    setState(INITIAL);
  }, []);

  return (
    <Ctx.Provider value={{ ...state, start, startFromPhase, loadPipeline, smartResume, reconcile: reconcileTerminal, reset }}>
      {children}
    </Ctx.Provider>
  );
}

export function usePipeline(): PipelineContextValue {
  const ctx = useContext(Ctx);
  if (!ctx) throw new Error("usePipeline must be used within a PipelineProvider");
  return ctx;
}

/** Helpers used by the indicator + page. */
export function activePhase(phases: Record<PipelinePhase, PhaseState>): PipelinePhase | null {
  return PIPELINE_PHASES.find((p) => phases[p].status === "running") ?? null;
}

export function phaseProgress(phases: Record<PipelinePhase, PhaseState>): { done: number; total: number } {
  const done = PIPELINE_PHASES.filter((p) => phases[p].status === "done").length;
  return { done, total: PIPELINE_PHASES.length };
}
