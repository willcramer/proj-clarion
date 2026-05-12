/**
 * Failure-pattern matcher.
 *
 * Each known failure mode we've seen in production gets a branch here.
 * Given the pipeline's terminal error message + a window of recent log
 * lines, returns a Diagnosis with:
 *   - kind:     a short identifier so the UI can pick an icon / colour
 *   - summary:  one-sentence human explanation of what broke
 *   - suggested: actionable hint, what to try, where to look, or what
 *               file to edit
 *   - retryable: whether a fresh re-run (same URL) is likely to help
 *   - severity: error / warning, most failures are errors, but some
 *               (e.g. transient DNS) are softer
 *
 * Add new branches at the top of `diagnose()` as we discover them.
 * The default fall-through returns a generic "look at the log" diagnosis
 * so the UI never goes silent.
 *
 * This is intentionally pure & client-side: no backend dependency, no
 * model calls. Just regex + substring checks against the data we
 * already have.
 */

import type { PipelinePhase } from "@/lib/api";
import type { PhaseStatus } from "@/lib/PipelineContext";

export interface Diagnosis {
  kind:
    | "schema-drift"
    | "llm-truncated"
    | "dns-failure"
    | "host-not-allowed"
    | "anthropic-rate-limit"
    | "anthropic-auth"
    | "subprocess-crash"
    | "alloy-down"
    | "loki-rate-limit"
    | "no-data-fetched"
    | "unknown";
  summary: string;
  suggested: string;
  retryable: boolean;
  severity: "error" | "warning";
}

const SEARCHED_LOG_LINES = 60;  // how far back to look from the tail

export function diagnose(
  error: string | null | undefined,
  phaseLogs: Record<PipelinePhase, string[]>,
  failedPhase: PipelinePhase | null,
): Diagnosis | null {
  if (!error && !failedPhase) return null;

  // Pull a focused tail. If we know which phase failed, look at its log
  // first; otherwise fall back to all-phase concat.
  const tail = (failedPhase
    ? phaseLogs[failedPhase] ?? []
    : Object.values(phaseLogs).flat()
  ).slice(-SEARCHED_LOG_LINES).join("\n");
  const blob = `${error ?? ""}\n${tail}`;

  // ─── Order matters: most-specific patterns first ───

  // Orchestrator metadata write hit an FK violation. Tells the user
  // research succeeded but DB linkage failed (e.g. profile JSON written
  // to disk but ProfileRepo.upsert was skipped). The fix lives in the
  // research CLI; the symptom is fatal because the orchestrator's
  // _maybe_link_artifact tried UPDATE pipelines.profile_id with no
  // matching row in company_profiles.
  if (
    /ForeignKeyViolation/i.test(blob) ||
    /violates foreign key constraint .*pipelines_(profile|plan)_id_fkey/i.test(blob)
  ) {
    return {
      kind: "subprocess-crash",
      summary: "Orchestrator hit a foreign-key violation linking pipeline to its profile/plan.",
      suggested:
        "Research wrote the profile to disk but skipped Postgres (or the row was deleted "
        + "between research-done and the FK update). Make sure cli/main.py::research calls "
        + "ProfileRepo().upsert() before exit. After the fix, re-run; the next research "
        + "phase will populate company_profiles eagerly and the FK will hold.",
      retryable: true,
      severity: "error",
    };
  }

  // LLM output got cut off mid-string, most often a max_tokens setting
  // that's too small for the vertical's fan-out. The planner's _llm_json
  // dumps the raw response and surfaces the path; we recognise either
  // the dump-path marker or the raw "Unterminated string" error.
  if (
    /Unterminated string starting at/i.test(blob) ||
    /raw saved to \/tmp\/clarion-llm-failure-/i.test(blob) ||
    /llm_json\.unparseable/i.test(blob)
  ) {
    const dumpMatch = blob.match(/raw saved to (\/tmp\/clarion-llm-failure-[^\s|]+)/);
    const dump = dumpMatch?.[1];
    return {
      kind: "llm-truncated",
      summary: "LLM output was truncated mid-string, its max_tokens for this phase is too small.",
      suggested:
        "Bump the call's `max_tokens` in agents/planner.py for the failing phase "
        + "(propose_assistant_tools, build_kg, propose_dashboards_and_alerts each "
        + "have their own setting). "
        + (dump ? `The full raw response is at ${dump} so you can see the cut-off point.` : "")
        + " Re-running often works on smaller verticals because output length varies.",
      retryable: true,
      severity: "error",
    };
  }

  // Schema-validation drift (most common LLM-output failure)
  if (
    blob.includes("synth.validation_error") ||
    /pydantic_core.*ValidationError/.test(blob) ||
    /Input should be /.test(blob)
  ) {
    const fieldMatch = blob.match(/(\w+(?:\.\w+|\.\d+)+)\s*\n\s*(?:Input should be|String should match|Field required)/);
    const field = fieldMatch?.[1];
    return {
      kind: "schema-drift",
      summary: field
        ? `LLM output failed schema validation on \`${field}\`.`
        : `LLM output failed schema validation.`,
      suggested: "The sanitizer in agents/research.py::_sanitize_research_payload "
        + "should catch most enum-drift cases. If you keep hitting this on the "
        + "same field, add a coercion branch for it. Re-running often works because "
        + "the LLM produces different output each call.",
      retryable: true,
      severity: "error",
    };
  }

  // Host not in the allow-list
  if (/host.*not.*allow|RESEARCH_ALLOWED_HOSTS|fetcher.refused/i.test(blob)) {
    return {
      kind: "host-not-allowed",
      summary: "The research agent refused to fetch the URL, host isn't in `RESEARCH_ALLOWED_HOSTS`.",
      suggested: "Add the company's domain to RESEARCH_ALLOWED_HOSTS in `.env`, "
        + "then restart the API (`lsof -ti:8765 | xargs kill && just api`) and re-run.",
      retryable: false,
      severity: "error",
    };
  }

  // Anthropic auth
  if (/401.*Unauthorized|invalid.*api.?key|ANTHROPIC_API_KEY/i.test(blob)) {
    return {
      kind: "anthropic-auth",
      summary: "Anthropic API rejected the request, usually a bad or missing key.",
      suggested: "Check `ANTHROPIC_API_KEY` in `.env`. Restart the API after editing.",
      retryable: false,
      severity: "error",
    };
  }

  // Anthropic rate limit / overloaded
  if (/anthropic.*rate.?limit|429.*Too Many|api.*overloaded/i.test(blob)) {
    return {
      kind: "anthropic-rate-limit",
      summary: "Anthropic API throttled or overloaded the request.",
      suggested: "Transient. Wait a minute and click Re-run.",
      retryable: true,
      severity: "warning",
    };
  }

  // Loki ingestion rate limit
  if (/ingestion.*rate.*limit.*Loki|429.*ResourceExhausted/i.test(blob)) {
    return {
      kind: "loki-rate-limit",
      summary: "Cloud Loki rejected log writes, your tier's bytes/sec limit was hit.",
      suggested: "Lower the live-tail batch (--batch 50 --interval 5) or reduce "
        + "--days for generate. The rest of the pipeline keeps working; logs "
        + "just don't all land.",
      retryable: true,
      severity: "warning",
    };
  }

  // Alloy down (Mode A only)
  if (/connection.*refused.*4317|connection.*refused.*4318|otel.*export.*failed/i.test(blob)) {
    return {
      kind: "alloy-down",
      summary: "Couldn't reach Alloy on localhost:4317/4318. The container may be down or unhealthy.",
      suggested: "Run `just up-cloud` to bring it back up, then re-run the pipeline.",
      retryable: true,
      severity: "error",
    };
  }

  // DNS / network failure to a fetched URL
  if (/nodename nor servname provided|getaddrinfo|name or service not known/i.test(blob)) {
    return {
      kind: "dns-failure",
      summary: "A source URL didn't resolve. The fetcher logs and continues.",
      suggested: "Usually harmless, research uses other sources. If the *primary* "
        + "company URL itself fails, the run will produce no profile; verify the "
        + "URL is correct and reachable.",
      retryable: true,
      severity: "warning",
    };
  }

  // Subprocess crash with no useful output
  if (/subprocess exited with code (1|2)/.test(blob) && tail.length < 200) {
    return {
      kind: "subprocess-crash",
      summary: "A CLI subprocess exited non-zero without leaving useful logs.",
      suggested: "Check the run's output panel above for stack traces. Common causes: "
        + "missing env var (re-check `.env`), Python import error, or DB connection failure.",
      retryable: true,
      severity: "error",
    };
  }

  // No data fetched at all
  if (/No profile produced|fetched_ok=0|no sources fetched/i.test(blob)) {
    return {
      kind: "no-data-fetched",
      summary: "Research couldn't fetch any usable content from the sources.",
      suggested: "Check the URL is live. The agent's allow-list may be filtering "
        + "all your candidate sources, see RESEARCH_ALLOWED_HOSTS in `.env`.",
      retryable: false,
      severity: "error",
    };
  }

  // Generic subprocess crash
  if (/subprocess exited with code/.test(blob)) {
    return {
      kind: "subprocess-crash",
      summary: "A CLI subprocess in this phase crashed. The error is in the log tail.",
      suggested: "Scroll the phase's log to find the traceback. Most causes are "
        + "schema drift (LLM output), missing env vars, or DB connectivity.",
      retryable: true,
      severity: "error",
    };
  }

  // Fall-through, never go silent on the user
  return {
    kind: "unknown",
    summary: "Pipeline failed but the failure mode isn't in our catalog yet.",
    suggested: "Check the phase log above. If you can isolate the cause, "
      + "add a branch to ui/src/lib/diagnose.ts so the next run gets diagnosed.",
    retryable: true,
    severity: "error",
  };
}

// ─── Lightweight metrics aggregator ─────────────────────────────────

export interface PhaseMetric {
  phase: PipelinePhase;
  status: PhaseStatus;
  durationMs: number | null;
  logLineCount: number;
  errorCount: number;
}

export interface PipelineMetrics {
  totalDurationMs: number | null;
  phases: PhaseMetric[];
  totalLogLines: number;
  totalErrors: number;
  phaseFailed: PipelinePhase | null;
}

export interface PhaseTimingsInput {
  status: PhaseStatus;
  startedAt?: number;
  finishedAt?: number;
  logs: string[];
}

export function computeMetrics(
  phases: Record<PipelinePhase, PhaseTimingsInput>,
  pipelineStartedAt: number | null,
  pipelineFinishedAt: number | null,
): PipelineMetrics {
  const PIPELINE_PHASES: PipelinePhase[] = [
    "research", "plan", "approve", "generate", "provision", "kg-publish",
  ];
  let phaseFailed: PipelinePhase | null = null;
  const phaseRows: PhaseMetric[] = [];
  let totalLogLines = 0;
  let totalErrors = 0;

  for (const p of PIPELINE_PHASES) {
    const ps = phases[p];
    if (!ps) continue;
    const dur =
      ps.startedAt && ps.finishedAt
        ? ps.finishedAt - ps.startedAt
        : ps.startedAt
        ? Date.now() - ps.startedAt
        : null;
    const errs = ps.logs.filter((l) =>
      /error|traceback|exception|fail/i.test(l),
    ).length;
    phaseRows.push({
      phase: p,
      status: ps.status,
      durationMs: dur,
      logLineCount: ps.logs.length,
      errorCount: errs,
    });
    totalLogLines += ps.logs.length;
    totalErrors += errs;
    if (ps.status === "failed" && phaseFailed === null) phaseFailed = p;
  }

  const totalDurationMs =
    pipelineStartedAt && pipelineFinishedAt
      ? pipelineFinishedAt - pipelineStartedAt
      : pipelineStartedAt
      ? Date.now() - pipelineStartedAt
      : null;

  return {
    totalDurationMs,
    phases: phaseRows,
    totalLogLines,
    totalErrors,
    phaseFailed,
  };
}

export function formatDuration(ms: number | null): string {
  if (ms === null) return ", ";
  const s = Math.round(ms / 1000);
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  const rs = s % 60;
  return rs === 0 ? `${m}m` : `${m}m ${rs}s`;
}
