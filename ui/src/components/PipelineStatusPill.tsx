/**
 * PipelineStatusPill — top-bar live indicator for the actively-followed
 * pipeline.
 *
 * Reads PipelineContext directly (no props) and renders:
 *   - nothing, if no pipeline is running
 *   - a teal pill with a pulsing dot, the active phase label, and an N/total
 *     progress fraction (e.g. "research · 4/6")
 *   - a terminal-state pill (success/danger) if the pipeline just finished
 *     and we haven't navigated away yet
 *
 * The legacy multi-pipeline "+N more running" counter logic lives in
 * `Layout.tsx`'s `PipelineIndicator`; this pill is intentionally narrower:
 * one followed pipeline, one click → `/pipelines/:currentId` to dive in.
 *
 * Why a separate component (vs leaving everything inline in Layout.tsx):
 * the v2 topbar wants a distinct "live signal" visual treatment — pulsing
 * accent dot, accent-bg surface, smaller height — and Layout.tsx was
 * already 340+ lines. Splitting keeps both files readable and lets the
 * pill be reused later (e.g. on a future split-pane "two-pipelines"
 * dashboard).
 */
import { Link } from "react-router-dom";
import { CheckCircle2, AlertCircle } from "lucide-react";

import { cn } from "@/lib/cn";
import { usePipeline, activePhase, phaseProgress } from "@/lib/PipelineContext";

export function PipelineStatusPill() {
  const p = usePipeline();

  // Don't render at all when the context is idle — the topbar's right
  // cluster collapses cleanly so we don't end up with phantom whitespace.
  if (p.status === "idle" || !p.pipelineId) return null;

  const { done, total } = phaseProgress(p.phases);
  const ap = activePhase(p.phases);

  // Visual treatment per terminal state. Default (running) is the v2
  // "live signal" — accent dot pulses, accent-bg surface.
  let tone =
    "border-[color:var(--color-accent-border)] bg-[var(--color-accent-bg)] text-[var(--color-accent)] hover:bg-[color:var(--color-accent-bg)]/80";
  let label = ap ?? "running";
  let dot = (
    <span className="relative flex h-2 w-2">
      <span className="absolute inset-0 rounded-full bg-[var(--color-accent)] opacity-70 animate-ping" />
      <span className="relative inline-flex h-2 w-2 rounded-full bg-[var(--color-accent)]" />
    </span>
  );

  if (p.status === "done") {
    tone =
      "border-[color:var(--color-success)]/40 bg-[var(--color-success-bg)] text-[var(--color-success)]";
    label = "done";
    dot = <CheckCircle2 size={12} className="text-[var(--color-success)]" />;
  } else if (p.status === "failed" || p.status === "cancelled") {
    tone =
      "border-[color:var(--color-danger)]/40 bg-[var(--color-danger-bg)] text-[var(--color-danger)]";
    label = p.status;
    dot = <AlertCircle size={12} className="text-[var(--color-danger)]" />;
  }

  return (
    <Link
      to={`/pipelines/${p.pipelineId}`}
      title={`Pipeline ${p.pipelineId} · ${p.url}`}
      className={cn(
        "flex items-center gap-2 px-2.5 h-7 rounded-full border text-xs font-medium transition-colors",
        tone,
      )}
    >
      {dot}
      <span>{label}</span>
      <span className="opacity-70 font-mono text-[11px]">· {done}/{total}</span>
    </Link>
  );
}
