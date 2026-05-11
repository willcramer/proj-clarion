/**
 * API client for the Clarion FastAPI backend.
 *
 * Vite's dev server proxies /api/* to http://127.0.0.1:8765 (see
 * vite.config.ts), so the same path works in dev and against a deployed
 * setup. No fetch base-URL juggling.
 */

const BASE = "/api";

/**
 * Custom error class thrown when the backend reports `setup_required`
 * (503 + `X-Clarion-Setup: required`). The SetupGate component catches
 * this and force-navigates to /setup. Distinct error class lets us
 * differentiate "we need creds" from "the API actually failed".
 */
export class SetupRequiredError extends Error {
  constructor() {
    super("Clarion setup is required — visit /setup");
    this.name = "SetupRequiredError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      Accept: "application/json",
      ...(init?.headers ?? {}),
    },
  });
  // Special-case the setup-gate response. We check the header (not just
  // the status code) so a coincidental 503 from a downstream service
  // doesn't kick the user to the setup wizard.
  if (res.status === 503 && res.headers.get("X-Clarion-Setup") === "required") {
    throw new SetupRequiredError();
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body?.detail ?? detail;
    } catch {
      /* body wasn't JSON; keep statusText */
    }
    throw new Error(`API ${res.status}: ${detail}`);
  }
  // 204 / empty body
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

// ─── Health / env ──────────────────────────────────────────────────

export interface EnvStatus {
  otlp_endpoint: string | null;
  asserts_env: string;
  asserts_site: string;
  mode: "alloy" | "cloud-direct" | "unset";
  cloud_auth_present: boolean;
  anthropic_key_present: boolean;
  sigil_endpoint: string | null;
}
export const getEnv = () => request<EnvStatus>("/env");

// ─── Dashboard ──────────────────────────────────────────────────────

export interface DashboardSummary {
  profiles_total: number;
  plans_total: number;
  plans_by_state: Record<string, number>;
  kg_nodes_total: number;
  kg_edges_total: number;
  business_events_total: number;
  last_event_at: string | null;
}
export const getDashboardSummary = () =>
  request<DashboardSummary>("/dashboard/summary");

// ─── Profiles ──────────────────────────────────────────────────────

export interface ProfileSummary {
  profile_id: string;
  company_name: string | null;
  primary_url: string;
  created_at: string;
  pain_signal_count: number;
  tech_signal_count: number;
  synthesized_flag_count: number;
  // Placeholder rows for in-flight pipelines whose research hasn't yet
  // produced a profile. Click → /pipelines?p=<pipeline_id>.
  pending?: boolean;
  pipeline_id?: string | null;
  pipeline_status?: string | null;
}
export const listProfiles = () => request<ProfileSummary[]>("/profiles");
export const getProfile = (id: string) =>
  request<unknown>(`/profiles/${encodeURIComponent(id)}`);

// ─── Plans ──────────────────────────────────────────────────────────

export interface PlanSummary {
  plan_id: string;
  plan_id_short: string;
  source_profile_id: string;
  review_state: string;
  updated_at: string;
  process_count: number;
  kg_node_count: number;
  kg_edge_count: number;
  alert_count: number;
  dashboard_count: number;
  // Placeholder rows for in-flight pipelines that have a profile but
  // haven't produced a plan yet (review_state="planning"). The UI
  // shows a "Planning..." card linking to /pipelines?p=<pipeline_id>.
  pending?: boolean;
  pipeline_id?: string | null;
  pipeline_status?: string | null;
}
export const listPlans = (state?: string) =>
  request<PlanSummary[]>(`/plans${state ? `?state=${encodeURIComponent(state)}` : ""}`);
export const getPlan = (id: string) =>
  request<unknown>(`/plans/${encodeURIComponent(id)}`);

export interface AuditEntry {
  timestamp: string;
  actor: string;
  action: string;
  from_state: string | null;
  to_state: string | null;
  note: string | null;
}
export const getPlanAudit = (id: string) =>
  request<AuditEntry[]>(`/plans/${encodeURIComponent(id)}/audit`);

export const approvePlan = (id: string, note: string, actor?: string) =>
  request<{ plan_id: string; from_state: string; to_state: string }>(
    `/plans/${encodeURIComponent(id)}/approve`,
    { method: "POST", body: JSON.stringify({ note, actor }) },
  );

export interface CloudCleanupResult {
  ran?: boolean;
  ok?: boolean;
  plan_id?: string;
  stdout_tail?: string[];
  stderr_tail?: string[];
}
export const deletePlan = (id: string, cleanupCloud = false) =>
  request<{
    deleted: boolean;
    plan_id: string;
    cascaded: { business_events: number; kg_nodes: number; kg_edges: number };
    json_file_removed: boolean;
    cloud_cleanup: CloudCleanupResult | null;
  }>(
    `/plans/${encodeURIComponent(id)}${cleanupCloud ? "?cleanup_cloud=true" : ""}`,
    { method: "DELETE" },
  );

export const deleteProfile = (id: string, cleanupCloud = false) =>
  request<{
    deleted: boolean;
    profile_id: string;
    cascaded_plans: number;
    cascaded_plan_ids: string[];
    json_file_removed: boolean;
    cloud_cleanup_per_plan: CloudCleanupResult[] | null;
  }>(
    `/profiles/${encodeURIComponent(id)}${cleanupCloud ? "?cleanup_cloud=true" : ""}`,
    { method: "DELETE" },
  );

export const replacePlanJson = (id: string, payload: unknown) =>
  request<unknown>(
    `/plans/${encodeURIComponent(id)}/json`,
    { method: "PUT", body: JSON.stringify(payload) },
  );

export interface HealthCheck {
  name: string;
  status: "pass" | "fail" | "warn" | "skip";
  detail: string;
  fix: string | null;
}
export interface HealthReport {
  plan_id: string | null;
  customer: string | null;
  passed: boolean;
  counts: { pass: number; fail: number; warn: number; skip: number };
  summary: string;
  checks: HealthCheck[];
}
export const getPlanHealth = (id: string) =>
  request<HealthReport>(`/plans/${encodeURIComponent(id)}/health`);

// ─── Orphan cleanup ────────────────────────────────────────────────

export interface OrphanFolder {
  uid: string;
  title: string;
  url: string;
  plan_id: string | null;
  reason: string;
}
export const listOrphanFolders = () =>
  request<OrphanFolder[]>("/orphans/folders");
export const deleteOrphanFolder = (uid: string) =>
  request<{ deleted: boolean; uid: string }>(
    `/orphans/folders/${encodeURIComponent(uid)}`,
    { method: "DELETE" },
  );

// ─── Runs ──────────────────────────────────────────────────────────

export type RunKind = "generate" | "provision" | "kg-publish" | "live-tail";

export interface RunSummary {
  run_id: string;
  kind: string;
  plan_id: string;
  started_at: string;
  finished: boolean;
  return_code: number | null;
  line_count: number;
}

export const listRuns = () => request<RunSummary[]>("/runs");
export const startRun = (kind: RunKind, plan_id: string, extra_args: string[] = []) =>
  request<RunSummary>("/runs", {
    method: "POST",
    body: JSON.stringify({ kind, plan_id, extra_args }),
  });
export const cancelRun = (run_id: string) =>
  request<{ cancelled: boolean }>(`/runs/${run_id}/cancel`, { method: "POST" });

/** SSE stream of log lines for a run.
 *  Yields `{type: "log", line}` for each line, `{type: "exit", code}` at the end.
 */
export function streamRun(
  run_id: string,
  onEvent: (e: { type: "log"; line: string } | { type: "exit"; code: number }) => void,
): () => void {
  const es = new EventSource(`${BASE}/runs/${encodeURIComponent(run_id)}/stream`);
  es.addEventListener("log", (e) => onEvent({ type: "log", line: (e as MessageEvent).data }));
  es.addEventListener("exit", (e) => {
    const code = parseInt((e as MessageEvent).data, 10) || 0;
    onEvent({ type: "exit", code });
    es.close();
  });
  es.addEventListener("error", () => {
    es.close();
  });
  return () => es.close();
}

// ─── End-to-end demo pipeline ──────────────────────────────────────

/** A single event from the pipeline orchestrator's SSE stream. */
export type PipelineEvent =
  | { event: "pipeline"; status: "started"; url: string; company: string | null; days: number; started_at: string; starting_phase?: PipelinePhase | null; profile_id?: string | null; plan_id?: string | null }
  | { event: "phase";    phase: PipelinePhase; status: "started"; message?: string }
  | { event: "phase";    phase: PipelinePhase; status: "done"; profile_id?: string; profile_path?: string; plan_id?: string; message?: string }
  | { event: "phase";    phase: PipelinePhase; status: "failed"; error: string }
  | { event: "phase";    phase: PipelinePhase; status: "skipped"; message?: string }
  | { event: "log";      phase: PipelinePhase; line: string }
  | { event: "links";    plan_id: string; profile_id: string; [key: string]: unknown }
  | { event: "pipeline"; status: "done"; finished_at: string; plan_id: string; profile_id: string }
  | { event: "pipeline"; status: "failed"; error: string; profile_id?: string | null; plan_id?: string | null }
  | { event: "pipeline"; status: "cancelled"; finished_at: string };

export type PipelinePhase = "research" | "plan" | "approve" | "generate" | "provision" | "kg-publish";

export const PIPELINE_PHASES: PipelinePhase[] = [
  "research", "plan", "approve", "generate", "provision", "kg-publish",
];

export interface PipelineSummary {
  pipeline_id: string;
  url: string;
  company: string | null;
  days: number;
  started_at: string;
  finished_at: string | null;
  status: "running" | "done" | "failed" | "cancelled";
  error: string | null;
  event_count: number;
  // Resume metadata — null when this build started from research.
  trigger?: "full" | "phase";
  starting_phase?: PipelinePhase | null;
  parent_pipeline_id?: string | null;
  // Linked artifacts produced by this build (set as phases complete).
  profile_id?: string | null;
  plan_id?: string | null;
}

export interface PipelinePhaseRow {
  phase: PipelinePhase;
  status: "pending" | "running" | "done" | "failed" | "skipped";
  started_at: string | null;
  finished_at: string | null;
  error: string | null;
  artifact: { profile_id?: string; plan_id?: string } | null;
}

/** Start a pipeline server-side. Returns its ID immediately — orchestrator
 *  runs as a background task on the API process. Use streamPipeline(id)
 *  to follow it (works even if the user navigates away and comes back).
 *
 *  `volume_per_day` overrides the planner's auto-derived event volume
 *  (default auto-scales 1.5K-5K/day by channel count). 500 = quick
 *  smoke build, 25K+ = stress test. Range [100, 100000]. */
export async function createPipeline(
  body: { url: string; company?: string; days?: number; volume_per_day?: number },
): Promise<PipelineSummary> {
  return request<PipelineSummary>("/pipelines/run", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

/** Resume a build from a specific phase. The new pipeline_id is fresh
 *  but `parent_pipeline_id` links back to the failed origin. Caller must
 *  supply whatever inputs the chosen phase needs:
 *    - 'research'                          → url + company
 *    - 'plan'                              → profile_id (or profile_path)
 *    - 'approve' | 'generate' | 'provision' | 'kg-publish'
 *                                          → plan_id */
export async function runPipelineFromPhase(body: {
  phase: PipelinePhase;
  url?: string;
  company?: string;
  days?: number;
  profile_id?: string;
  profile_path?: string;
  plan_id?: string;
  parent_pipeline_id?: string;
  volume_per_day?: number;
}): Promise<PipelineSummary> {
  return request<PipelineSummary>("/pipelines/run-from-phase", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

export const listPipelines = () => request<PipelineSummary[]>("/pipelines");
export const getPipeline = (id: string) => request<PipelineSummary>(`/pipelines/${id}`);
export const getPipelinePhases = (id: string) =>
  request<PipelinePhaseRow[]>(`/pipelines/${id}/phases`);
export const getPipelineEvents = (id: string) =>
  request<{ pipeline_id: string; status: string | null; events: PipelineEvent[] }>(
    `/pipelines/${id}/events`,
  );
export const cancelPipeline = (id: string) =>
  request<{ cancelled: boolean }>(`/pipelines/${id}/cancel`, { method: "POST" });

/** Connect to a pipeline's SSE stream. Replays from the beginning, then
 *  tails. Returns a function to close the connection.
 *
 *  Survives tab switches, page navigations, even browser reloads — events
 *  are buffered server-side, so reconnecting picks up from event 0. */
export function streamPipeline(
  pipeline_id: string,
  onEvent: (e: PipelineEvent) => void,
  onError?: (msg: string) => void,
): () => void {
  const es = new EventSource(`${BASE}/pipelines/${pipeline_id}/stream`);

  // Once we've seen a terminal pipeline event we MUST close the stream
  // ourselves. Otherwise: the server's stream_pipeline returns after
  // sending pipeline:done|failed|cancelled, the connection drops, and
  // browser EventSource silently auto-reconnects. The new connection
  // hits the same endpoint which replays every event from seq 0 — and
  // the client appends them all to its log buffer again. That's how
  // a 1700-line failed pipeline turned into 3888 lines five seconds
  // later. `terminated` is a one-way latch: once flipped, no further
  // events are forwarded, even if a stale reconnect somehow slips
  // through (e.g. browser cache vs. actual close timing).
  let terminated = false;
  const TERMINAL_STATUSES = new Set(["done", "failed", "cancelled"]);

  const handleEvent = (name: string) => (e: Event) => {
    if (terminated) return;
    try {
      const payload = JSON.parse((e as MessageEvent).data);
      const ev = { event: name, ...payload } as PipelineEvent;
      onEvent(ev);
      if (name === "pipeline" && TERMINAL_STATUSES.has(payload.status)) {
        terminated = true;
        es.close();
      }
    } catch (err) {
      onError?.(`bad event payload: ${err}`);
    }
  };
  es.addEventListener("pipeline", handleEvent("pipeline"));
  es.addEventListener("phase",    handleEvent("phase"));
  es.addEventListener("log",      handleEvent("log"));
  es.addEventListener("links",    handleEvent("links"));
  es.addEventListener("error", () => {
    if (terminated) return;
    // EventSource auto-retries on transient errors; only flag if it never recovers.
    if (es.readyState === EventSource.CLOSED) onError?.("stream closed");
  });
  return () => {
    terminated = true;
    es.close();
  };
}

// ─── Agent chat (research extend / plan refine) ────────────────────

export interface ChatMessage {
  role: "user" | "assistant";
  content: string;
}

/** Stream an agent reply token-by-token. Returns the AbortController so the
 *  caller can stop the stream mid-flight. */
export async function streamAgent(
  endpoint: "research/extend" | "plan/refine",
  context_id: string,
  history: ChatMessage[],
  onDelta: (s: string) => void,
  onDone: () => void,
  onError: (e: string) => void,
): Promise<AbortController> {
  const controller = new AbortController();
  // SSE over POST: the EventSource API only supports GET, so we read the
  // streaming response body manually with fetch + ReadableStream.
  const res = await fetch(`${BASE}/agents/${endpoint}`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify({ context_id, history }),
    signal: controller.signal,
  });
  if (!res.ok || !res.body) {
    onError(`agent stream failed: HTTP ${res.status}`);
    return controller;
  }

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffered = "";

  (async () => {
    try {
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffered += decoder.decode(value, { stream: true });
        // SSE frames are separated by blank lines. Each frame is a set of
        // `event: ...` / `data: ...` lines.
        let idx;
        while ((idx = buffered.indexOf("\n\n")) >= 0) {
          const frame = buffered.slice(0, idx);
          buffered = buffered.slice(idx + 2);
          let event = "message";
          let data = "";
          for (const line of frame.split("\n")) {
            if (line.startsWith("event: ")) event = line.slice(7).trim();
            else if (line.startsWith("data: ")) data += line.slice(6);
          }
          if (event === "delta") onDelta(data);
          else if (event === "done") {
            onDone();
            return;
          } else if (event === "error") {
            onError(data);
            return;
          }
        }
      }
      onDone();
    } catch (e) {
      if ((e as Error).name !== "AbortError") onError(String(e));
    }
  })();

  return controller;
}
