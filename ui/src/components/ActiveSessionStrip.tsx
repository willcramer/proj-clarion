/**
 * ActiveSessionStrip, full-width 60px row that surfaces "a build is
 * running right now" on any page that has space for it (initially the
 * Dashboard, between the Hero and the KPI tiles).
 *
 * Reads `PipelineContext` directly (no props) and returns `null` when no
 * pipeline is being followed. That null-return makes it a drop-in: place
 * it in the page tree and it self-shows / self-hides without parent
 * boilerplate.
 *
 * Visual treatment (from v2 dashboard.md):
 *   - Outline: --color-accent-border · Fill: --color-accent-bg
 *   - 60px tall, --radius
 *   - Pulsing teal dot (`.active-session-dot` keyframes in index.css)
 *   - Left:  hostname + active phase name (large)
 *           pipeline-id-short · n/6 · started-relative (small)
 *   - Right: "Continue →" link to /pipelines/:id
 *
 * This is the running-build companion to PipelineStatusPill in the
 * topbar, the pill is always-on-screen, the strip is hero-area.
 */
import { Link } from "react-router-dom";

import {
  usePipeline, activePhase, phaseProgress,
} from "@/lib/PipelineContext";
import type { PipelinePhase } from "@/lib/api";

/** Map phase codes to full English labels the v2 mockup uses for the
 *  ActiveSessionStrip ("Provisioning dashboards" instead of "provision").
 *  Reads as a present-continuous status the SE can drop into a customer
 *  conversation without translating to engineering language. */
const PHASE_LABELS: Record<PipelinePhase, string> = {
  "research":   "Researching prospect",
  "plan":       "Planning entities",
  "approve":    "Reviewing plan",
  "generate":   "Generating data",
  "provision":  "Provisioning dashboards",
  "kg-publish": "Publishing knowledge graph",
};

export function ActiveSessionStrip() {
  const p = usePipeline();
  // Only render for *live* pipelines. Done / failed / cancelled belong
  // in history surfaces, not "active session."
  if (p.status !== "running" || !p.pipelineId) return null;

  const apCode = activePhase(p.phases);
  const apLabel = apCode ? PHASE_LABELS[apCode] : "Running";
  const { done, total } = phaseProgress(p.phases);
  // Phase count shown in the subtitle is the 1-indexed CURRENT phase,
  // not the count of completed phases. So "phase 5 of 6" means we're
  // working on the 5th, i.e. (done + 1) clamped to total.
  const phaseNumber = Math.min(total, done + 1);
  const host = safeHost(p.url);
  const startedRel = p.startedAt ? formatRelative(p.startedAt) : "just now";
  const idShort = p.pipelineId.slice(0, 6);

  return (
    <Link
      to={`/pipelines/${p.pipelineId}`}
      className="active-session group flex items-center gap-4 px-5 h-[60px] rounded-[10px] transition-colors"
      style={{
        border: "1px solid var(--color-accent-border)",
        background: "var(--color-accent-bg)",
      }}
    >
      <span
        aria-hidden="true"
        className="active-session-dot relative inline-flex h-2 w-2 rounded-full shrink-0"
        style={{ background: "var(--color-accent)" }}
      />

      <div className="flex-1 min-w-0">
        <div className="text-sm truncate">
          <span className="font-medium text-[var(--color-text)]">
            {host}
          </span>
          {/* Em-dash separator + full English phase name, per v2 mockup. */}
          <span className="mx-1.5 text-[var(--color-text-faint)]" aria-hidden="true">
,           </span>
          <span className="text-[var(--color-text)]">{apLabel}</span>
        </div>
        <div className="text-[11px] text-[var(--color-text-muted)] mt-0.5 truncate">
          pipeline{" "}
          <span className="font-mono text-[var(--color-text)]">{idShort}</span>
          <span aria-hidden="true"> · </span>
          phase <span className="tabular-nums">{phaseNumber}</span> of{" "}
          <span className="tabular-nums">{total}</span>
          <span aria-hidden="true"> · </span>
          started {startedRel}
        </div>
      </div>

      <span className="hidden sm:inline-flex items-center gap-1 text-xs font-medium text-[var(--color-accent)] group-hover:translate-x-0.5 transition-transform">
        Continue
        <span aria-hidden="true">→</span>
      </span>
    </Link>
  );
}

function safeHost(url: string): string {
  if (!url) return "unknown host";
  try {
    return new URL(url).host;
  } catch {
    // PipelineContext.url is whatever the user typed, may not be a full
    // URL during the early "started" event. Fall back to the raw string.
    return url;
  }
}

function formatRelative(timestampMs: number): string {
  const diffSec = Math.max(0, Math.round((Date.now() - timestampMs) / 1000));
  if (diffSec < 60) return `${diffSec}s ago`;
  const min = Math.round(diffSec / 60);
  if (min < 60) return `${min}m ago`;
  const hr = Math.round(min / 60);
  return `${hr}h ago`;
}
