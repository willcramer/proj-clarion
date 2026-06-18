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
    super("Clarion setup is required, visit /setup");
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

export interface AcceptClaimResult {
  /** Synthesized-flag count after the action. UI uses it to update
   *  the Claims tab pill without an extra refetch. */
  remaining: number;
  /** Full extended profile. Caller can drop it into the cache or
   *  ignore and rely on getProfile invalidation. */
  profile: unknown;
}

/** Accept (or dismiss) a synthesized claim by field_path. The underlying
 *  value stays in the profile; only the "review this" flag is removed.
 *  Server records an audit row so the global Audit page shows the
 *  decision. */
export const acceptProfileClaim = (
  id: string, field_path: string, decision: "accept" | "dismiss" = "accept",
) =>
  request<AcceptClaimResult>(
    `/profiles/${encodeURIComponent(id)}/claims/accept`,
    { method: "POST", body: JSON.stringify({ field_path, decision }) },
  );

// ─── Profile audit log ─────────────────────────────────────────────
//
// Mirrors the global plan-audit shape so AuditPage can render a third
// "Profile changes" section using the same table chrome.

export interface ProfileAuditEntry {
  audit_id: number;
  timestamp: string;
  profile_id: string;
  actor: string;
  prompt: string;
  summary: string;
  additions: Record<string, number>;
  applied: boolean;
  url?: string | null;
  company?: string | null;
}

export interface GlobalProfileAuditResponse {
  entries: ProfileAuditEntry[];
  total: number;
  limit: number;
  offset: number;
}

/** Global cross-profile feed for the /audit page's Profile changes
 *  section. Pagination matches the other audit feeds. */
export const listProfileAudit = (params?: { limit?: number; offset?: number }) => {
  const q = new URLSearchParams();
  if (params?.limit !== undefined)  q.set("limit",  String(params.limit));
  if (params?.offset !== undefined) q.set("offset", String(params.offset));
  const qs = q.toString();
  return request<GlobalProfileAuditResponse>(`/profiles/audit${qs ? "?" + qs : ""}`);
};

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
/** List plans, optionally filtered by review state and/or source
 *  profile. The Profile detail page passes `source_profile_id` to
 *  show "plans built from this profile". String-arg back-compat is
 *  kept so old call sites that passed a bare state name still work. */
export function listPlans(state?: string): Promise<PlanSummary[]>;
export function listPlans(params: {
  state?: string;
  source_profile_id?: string;
}): Promise<PlanSummary[]>;
export function listPlans(
  arg?: string | { state?: string; source_profile_id?: string },
): Promise<PlanSummary[]> {
  const opts = typeof arg === "string" ? { state: arg } : (arg ?? {});
  const q = new URLSearchParams();
  if (opts.state) q.set("state", opts.state);
  if (opts.source_profile_id) q.set("source_profile_id", opts.source_profile_id);
  const qs = q.toString();
  return request<PlanSummary[]>(`/plans${qs ? "?" + qs : ""}`);
}
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

export interface ClearCloudResult {
  plan_id: string;
  cleared: boolean;
  reverted_to: string | null;
  stdout_tail: string[];
  stderr_tail: string[];
}
/** Remove a plan's Grafana Cloud footprint (dashboards folder + alert
 *  rules) but KEEP the Clarion plan. The "tidy my tenant, keep the
 *  plan" action; the plan reverts to not-provisioned so it can be
 *  re-provisioned later. */
export const clearPlanCloud = (id: string) =>
  request<ClearCloudResult>(`/plans/${encodeURIComponent(id)}/cloud/clear`, { method: "POST" });

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

/** External research source types the SE can toggle per build. Mirrors
 *  the server's `SourceName` literal (api/routes/pipelines.py), which is
 *  itself asserted against external_sources.constants.RESEARCH_SOURCE_KEYS. */
export type ResearchSource =
  | "edgar_10k" | "greenhouse_jobs" | "lever_jobs" | "github_org" | "wikidata";

/** Display metadata for the per-source toggles, in render order. */
export const RESEARCH_SOURCES: { key: ResearchSource; label: string; hint: string }[] = [
  { key: "edgar_10k",       label: "SEC EDGAR",  hint: "Latest 10-K — public companies only" },
  { key: "github_org",      label: "GitHub",     hint: "Org repos for tech-stack signal" },
  { key: "greenhouse_jobs", label: "Greenhouse", hint: "Open roles → active initiatives" },
  { key: "lever_jobs",      label: "Lever",      hint: "Open roles → active initiatives" },
  { key: "wikidata",        label: "Wikidata",   hint: "Structured company metadata" },
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
  // Resume metadata, null when this build started from research.
  trigger?: "full" | "phase";
  starting_phase?: PipelinePhase | null;
  parent_pipeline_id?: string | null;
  // Linked artifacts produced by this build (set as phases complete).
  profile_id?: string | null;
  plan_id?: string | null;
  // Phase rollup, server-derived from pipeline_phases. The Builds list
  // renders these as a per-row progress bar (phases_done / 6) plus the
  // active phase name on running rows.
  phases_done?: number;
  current_phase?: PipelinePhase | null;
}

export interface PipelinePhaseRow {
  phase: PipelinePhase;
  status: "pending" | "running" | "done" | "failed" | "skipped";
  started_at: string | null;
  finished_at: string | null;
  error: string | null;
  artifact: { profile_id?: string; plan_id?: string } | null;
}

/** Start a pipeline server-side. Returns its ID immediately, orchestrator
 *  runs as a background task on the API process. Use streamPipeline(id)
 *  to follow it (works even if the user navigates away and comes back).
 *
 *  `volume_per_day` overrides the planner's auto-derived event volume
 *  (default auto-scales 1.5K-5K/day by channel count). 500 = quick
 *  smoke build, 25K+ = stress test. Range [100, 100000]. */
export async function createPipeline(
  body: {
    url: string;
    company?: string;
    days?: number;
    volume_per_day?: number;
    /** Optional cut-off, orchestrator stops after this phase succeeds.
     *  Use "research" for the "Just add profile" flow on Profiles. */
    stop_after_phase?: PipelinePhase;
    /** Override the server-side duplicate-profile guard (the "build new
     *  anyway" path). Omit/false → a 409 if a profile for the host exists. */
    allow_duplicate?: boolean;
    /** External source types to turn OFF for this build. The research agent
     *  fetches every source NOT listed. Omit/[] → all sources enabled. */
    disabled_sources?: ResearchSource[];
    /** Optional discovery/meeting notes folded into research as a trusted
     *  source on top of the web/external investigation. */
    notes?: string;
  },
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
  /** Cut-off phase. UI sets "plan" when building a plan from a profile so
   *  nothing provisions until approval. */
  stop_after_phase?: PipelinePhase;
}): Promise<PipelineSummary> {
  return request<PipelineSummary>("/pipelines/run-from-phase", {
    method: "POST",
    body: JSON.stringify(body),
  });
}

/** Continue an existing build IN PLACE (same pipeline_id) from a phase —
 *  default "approve", which approves the plan then generate → provision →
 *  kg-publish. No second build row; the build just moves through the next
 *  phases. plan_id/profile_id are inherited from the pipeline server-side. */
export async function continuePipeline(
  pipelineId: string,
  body: { starting_phase?: PipelinePhase; plan_id?: string; profile_id?: string } = {},
): Promise<PipelineSummary> {
  return request<PipelineSummary>(`/pipelines/${pipelineId}/continue`, {
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

/** Hard-delete a build: cancels it if running, then removes the row +
 *  its events/phases. Use for a wedged/runaway build that won't stop.
 *  Does NOT delete the produced plan/profile (or their Cloud resources). */
export const deletePipeline = (id: string) =>
  request<{ deleted: boolean }>(`/pipelines/${id}`, { method: "DELETE" });

/** Bulk-remove all failed + cancelled builds (rows + events/phases).
 *  Clears the Builds list of dead/reload-casualty runs; never touches
 *  running or done builds. */
export const prunePipelines = () =>
  request<{ deleted: number }>("/pipelines/prune", { method: "POST" });

// ─── Demo sessions (live emitter) ──────────────────────────────────
//
// One active session per plan. The dashboard's Live demo card lists
// every active session across all plans via /api/demo/sessions; the
// per-plan controls (start/stop/extend) on Plans-detail use the
// existing /api/demo/{status,start,stop,extend} routes.

export interface DemoSession {
  session_id: number;
  plan_id: string;
  pid: number | null;
  status: "starting" | "live" | "stopped" | "expired" | "crashed";
  started_at: string | null;
  expires_at: string;
  last_heartbeat_at: string | null;
  seconds_since_heartbeat: number | null;
  seconds_until_expiry: number;
  health: "starting" | "live" | "stale";
  /** Profile source_url for the plan's company. */
  url: string | null;
  /** Optional friendly company name pulled from the profile JSON. */
  company: string | null;
}

export const listDemoSessions = () =>
  request<{ sessions: DemoSession[] }>("/demo/sessions").then((r) => r.sessions);

export interface DemoHistoryRow {
  session_id: number;
  plan_id: string;
  pid: number | null;
  status: "starting" | "live" | "stopped" | "expired" | "crashed";
  started_at: string | null;
  finished_at: string | null;
  expires_at: string | null;
  /** Server-derived duration. For in-flight rows this is "now - started_at"
   *  at fetch time; for terminal rows it's "finished_at - started_at". */
  seconds_active: number | null;
  url: string | null;
  company: string | null;
  notes: string | null;
}

export interface DemoHistoryResponse {
  history: DemoHistoryRow[];
  total: number;
  limit: number;
  offset: number;
}

/** Audit log of demo sessions, newest first. Pass `plan_id` to scope
 *  to a single plan (used by the per-plan history strip on Plans-detail). */
export const listDemoHistory = (params?: {
  limit?: number;
  offset?: number;
  plan_id?: string;
}) => {
  const q = new URLSearchParams();
  if (params?.limit !== undefined)  q.set("limit",  String(params.limit));
  if (params?.offset !== undefined) q.set("offset", String(params.offset));
  if (params?.plan_id)              q.set("plan_id", params.plan_id);
  const qs = q.toString();
  return request<DemoHistoryResponse>(`/demo/history${qs ? "?" + qs : ""}`);
};

// ─── Plan audit (global) ────────────────────────────────────────────

export interface GlobalAuditEntry {
  timestamp: string;
  plan_id: string | null;
  actor: string;
  action: string;
  from_state: string | null;
  to_state: string | null;
  note: string | null;
  url: string | null;
  company: string | null;
}

export interface GlobalAuditResponse {
  entries: GlobalAuditEntry[];
  total: number;
  limit: number;
  offset: number;
}

export const listPlanAudit = (params?: { limit?: number; offset?: number }) => {
  const q = new URLSearchParams();
  if (params?.limit !== undefined)  q.set("limit",  String(params.limit));
  if (params?.offset !== undefined) q.set("offset", String(params.offset));
  const qs = q.toString();
  return request<GlobalAuditResponse>(`/plans/audit${qs ? "?" + qs : ""}`);
};

export const stopDemoSession = (plan_id: string) =>
  request<{ ok: boolean; stopped: boolean; pid: number | null }>(
    "/demo/stop",
    { method: "POST", body: JSON.stringify({ plan_id }) },
  );

export const extendDemoSession = (plan_id: string, additional_hours = 1) =>
  request<{ ok: boolean; expires_at: string }>(
    "/demo/extend",
    { method: "POST", body: JSON.stringify({ plan_id, additional_hours }) },
  );

/** Connect to a pipeline's SSE stream. Replays from the beginning, then
 *  tails. Returns a function to close the connection.
 *
 *  Survives tab switches, page navigations, even browser reloads, events
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
  // hits the same endpoint which replays every event from seq 0, and
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

// ─── Global Clarion Assistant ──────────────────────────────────────
//
// The single, app-wide agent. Multi-turn tool-use loop server-side;
// the client streams text deltas + tool_call / tool_result events and
// persists everything to assistant_conversations / assistant_turns.

/** Page context pinned to a turn so the agent can resolve "this plan". */
export interface AssistantContextScope {
  plan_id?: string;
  profile_id?: string;
  pipeline_id?: string;
  route?: string;
}

/** A tool the agent invoked (assistant turn) — surfaced as a chip. */
export interface AssistantToolCall {
  tool_use_id: string;
  name: string;
  input: Record<string, unknown>;
  /** True for state-changing tools (run_build, extend_profile, …). */
  mutating?: boolean;
}

/** Selected fields lifted out of a tool result for inline rendering
 *  (e.g. a build's watch link) — present on streaming tool_result
 *  events for mutating tools. */
export interface AssistantToolResultDetail {
  message?: string;
  watch_url?: string;
  pipeline_id?: string;
  plan_id?: string;
  profile_id?: string;
  status?: string;
  phase?: string;
  summary?: string;
}

/** A tool result fed back to the agent (tool turn). */
export interface AssistantToolResult {
  tool_use_id: string;
  /** Full content sent to Claude (present on persisted turns). */
  content?: string;
  summary?: string;
  is_error: boolean;
  detail?: AssistantToolResultDetail;
}

/** One persisted turn. Mirrors the backend AssistantTurnDTO. */
export interface AssistantTurn {
  turn_id: number;
  role: "user" | "assistant" | "tool";
  content: string;
  tool_calls: AssistantToolCall[] | null;
  tool_results: AssistantToolResult[] | null;
  context_scope: AssistantContextScope | null;
  tokens_in: number | null;
  tokens_out: number | null;
  created_at: string;
}

/** Full conversation with its turns. */
export interface AssistantConversation {
  conversation_id: number;
  actor: string;
  title: string | null;
  status: "active" | "archived";
  created_at: string;
  updated_at: string;
  last_message_at: string | null;
  turns: AssistantTurn[];
}

/** Slim shape for the conversation picker. */
export interface AssistantConversationSummary {
  conversation_id: number;
  title: string | null;
  status: "active" | "archived";
  created_at: string;
  last_message_at: string | null;
}

export const listConversations = (
  params?: { status?: "active" | "archived"; limit?: number },
) => {
  const q = new URLSearchParams();
  if (params?.status) q.set("status", params.status);
  if (params?.limit !== undefined) q.set("limit", String(params.limit));
  const qs = q.toString();
  return request<AssistantConversationSummary[]>(
    `/agents/clarion/conversations${qs ? "?" + qs : ""}`,
  );
};

export const getConversation = (id: number) =>
  request<AssistantConversation>(`/agents/clarion/conversations/${id}`);

export const archiveConversation = (id: number) =>
  request<void>(`/agents/clarion/conversations/${id}/archive`, { method: "POST" });

/** A build the assistant wants to run, paused awaiting the SE's approval
 *  (only emitted when auto_approve is off). */
export interface AssistantApprovalRequired {
  conversation_id: number;
  tool_use_id: string;
  name: string;
  input: Record<string, unknown>;
  message: string;
}

/** Callbacks for a streaming assistant turn. */
export interface ClarionChatHandlers {
  onDelta: (chunk: string) => void;
  onToolCall: (call: AssistantToolCall) => void;
  onToolResult: (result: AssistantToolResult) => void;
  /** A build-kicking tool is paused awaiting approval. */
  onApprovalRequired?: (a: AssistantApprovalRequired) => void;
  /** Fires once at the end. `awaiting_approval` is true when the turn
   *  paused for a build approval instead of completing. */
  onDone: (info: {
    conversation_id: number;
    tokens_in: number | null;
    tokens_out: number | null;
    awaiting_approval: boolean;
  }) => void;
  onError: (msg: string) => void;
}

/** Shared SSE frame reader for the assistant's chat + resume streams.
 *  Server emits: delta, tool_call, tool_result, approval_required, done,
 *  error. */
async function consumeClarionStream(res: Response, handlers: ClarionChatHandlers): Promise<void> {
  if (!res.ok || !res.body) {
    handlers.onError(`assistant stream failed: HTTP ${res.status}`);
    return;
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffered = "";
  try {
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buffered += decoder.decode(value, { stream: true }).replace(/\r/g, "");
      let idx;
      while ((idx = buffered.indexOf("\n\n")) >= 0) {
        const frame = buffered.slice(0, idx);
        buffered = buffered.slice(idx + 2);
        let event = "message";
        const dataLines: string[] = [];
        for (const line of frame.split("\n")) {
          if (line.startsWith(":")) continue;
          if (line.startsWith("event:")) {
            event = line.slice(6).trim();
          } else if (line.startsWith("data:")) {
            const rest = line.slice(5);
            dataLines.push(rest.startsWith(" ") ? rest.slice(1) : rest);
          }
        }
        const data = dataLines.join("\n");
        if (event === "delta") {
          handlers.onDelta(data);
        } else if (event === "tool_call") {
          try { handlers.onToolCall(JSON.parse(data)); } catch { /* ignore */ }
        } else if (event === "tool_result") {
          try { handlers.onToolResult(JSON.parse(data)); } catch { /* ignore */ }
        } else if (event === "approval_required") {
          try { handlers.onApprovalRequired?.(JSON.parse(data)); } catch { /* ignore */ }
        } else if (event === "done") {
          try {
            const parsed = JSON.parse(data);
            handlers.onDone({
              conversation_id: parsed.conversation_id,
              tokens_in: parsed.tokens_in ?? null,
              tokens_out: parsed.tokens_out ?? null,
              awaiting_approval: Boolean(parsed.awaiting_approval),
            });
          } catch {
            handlers.onError("malformed done event");
          }
          return;
        } else if (event === "error") {
          handlers.onError(data);
          return;
        }
      }
    }
  } catch (e) {
    if ((e as Error).name !== "AbortError") handlers.onError(String(e));
  }
}

/** Send a message to the global Clarion Assistant and stream the reply.
 *  Returns an AbortController so the caller can cancel mid-stream.
 *
 *  When `conversation_id` is omitted a new thread is created; its id
 *  comes back on the `done` event. `auto_approve=false` (default) makes
 *  the agent pause before running a build (emits `approval_required`). */
export async function streamClarionChat(
  body: {
    message: string;
    conversation_id?: number;
    context_scope?: AssistantContextScope;
    auto_approve?: boolean;
  },
  handlers: ClarionChatHandlers,
): Promise<AbortController> {
  const controller = new AbortController();
  const res = await fetch(`${BASE}/agents/clarion/chat`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify(body),
    signal: controller.signal,
  });
  void consumeClarionStream(res, handlers);
  return controller;
}

/** Resolve a build paused awaiting approval: `approve` runs it, `reject`
 *  declines it. Streams the continuation with the same handlers. */
export async function resumeClarionChat(
  conversationId: number,
  body: { decision: "approve" | "reject"; auto_approve?: boolean },
  handlers: ClarionChatHandlers,
): Promise<AbortController> {
  const controller = new AbortController();
  const res = await fetch(`${BASE}/agents/clarion/conversations/${conversationId}/resume`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Accept: "text/event-stream" },
    body: JSON.stringify(body),
    signal: controller.signal,
  });
  void consumeClarionStream(res, handlers);
  return controller;
}
