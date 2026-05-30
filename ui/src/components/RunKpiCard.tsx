/**
 * RunKpiCard, the per-run tile for the Runs list page.
 *
 * Same visual language as ProfileKpiCard / PlanKpiCard / PipelineKpiCard.
 * Bound to RunSummary data — a run is a single CLI subprocess invoked
 * by the API, so the data shape is simpler than pipelines (no events,
 * no phases, just kind / plan / line count / status).
 */
import { Activity, ChevronRight, Loader2 } from "lucide-react";

import { type RunSummary } from "@/lib/api";
import { cn } from "@/lib/cn";

export type RunCardTone = "ready" | "researching" | "failed" | "draft";

function runStatusTone(run: RunSummary): RunCardTone {
  if (!run.finished) return "researching";
  if (run.return_code === 0) return "ready";
  return "failed";
}

const TONE_TO_CARD_ACCENT: Record<RunCardTone, { solid: string; soft: string }> = {
  ready:       { solid: "var(--color-accent)",     soft: "var(--color-accent-bg)" },
  researching: { solid: "var(--color-info)",       soft: "var(--color-info-bg)" },
  failed:      { solid: "var(--color-danger)",     soft: "var(--color-danger-bg)" },
  draft:       { solid: "var(--color-text-muted)", soft: "rgba(180, 200, 230, 0.05)" },
};

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

function statusLabel(run: RunSummary): string {
  if (!run.finished) return "running";
  if (run.return_code === 0) return "done";
  return `exit ${run.return_code}`;
}

export interface RunKpiCardProps {
  run: RunSummary;
  onClick: () => void;
  selected?: boolean;
  compact?: boolean;
}

export function RunKpiCard({ run, onClick, selected, compact = false }: RunKpiCardProps) {
  const tone = runStatusTone(run);
  const accent = TONE_TO_CARD_ACCENT[tone];
  const isRunning = !run.finished;

  return (
    <button
      type="button"
      onClick={onClick}
      style={{
        "--card-accent": accent.solid,
        "--card-accent-bg": accent.soft,
      } as React.CSSProperties}
      className={cn(
        "group relative isolate text-left rounded-xl border overflow-hidden",
        "bg-[var(--color-canvas-elev1)] border-[var(--color-border)]",
        "flex flex-col shadow-[var(--shadow-sm)]",
        compact ? "p-4 min-h-[112px]" : "p-5 min-h-[164px]",
        "transition-[transform,box-shadow,border-color] duration-150 ease-out",
        "hover:-translate-y-px hover:border-[var(--color-border-strong)] hover:shadow-[var(--shadow-md)]",
        "focus-visible:outline-none focus-visible:border-[var(--card-accent)] focus-visible:ring-2 focus-visible:ring-[var(--card-accent)]/40",
        "cursor-pointer",
        selected && "border-[var(--card-accent)] ring-1 ring-[var(--card-accent)]/40",
      )}
    >
      {!compact && (
        <span
          aria-hidden="true"
          className="pointer-events-none absolute inset-0 -z-10"
          style={{
            background:
              "radial-gradient(120% 80% at 100% 0%, var(--card-accent-bg) 0%, transparent 55%)",
          }}
        />
      )}

      <div className={cn("flex items-start", compact ? "gap-2.5" : "gap-3")}>
        <span
          aria-hidden="true"
          className={cn(
            "inline-flex items-center justify-center rounded-lg shrink-0",
            compact ? "w-8 h-8" : "w-10 h-10",
            "bg-[var(--card-accent-bg)] text-[var(--card-accent)]",
            "border border-[color:var(--card-accent)]/30",
          )}
        >
          {isRunning ? (
            <Loader2 size={compact ? 13 : 16} className="animate-spin" />
          ) : (
            <Activity size={compact ? 14 : 16} />
          )}
        </span>
        <div className="min-w-0 flex-1">
          <div
            className={cn(
              "font-semibold tracking-tight text-[var(--color-text)] truncate leading-tight",
              compact ? "text-[14px]" : "text-[17px]",
            )}
          >
            {run.kind}
          </div>
          <div
            className={cn(
              "text-[var(--color-text-faint)] font-mono truncate",
              compact ? "text-[10px] mt-0.5" : "text-[11px] mt-1",
            )}
          >
            plan {run.plan_id.slice(0, 8)}
          </div>
        </div>
        <span
          className={cn(
            "inline-flex items-center rounded-full shrink-0",
            "font-mono uppercase tracking-wider border",
            compact ? "h-[18px] px-1.5 text-[9px]" : "h-5 px-2 text-[10px]",
            "border-[color:var(--card-accent)]/40 bg-[var(--card-accent-bg)] text-[var(--card-accent)]",
          )}
        >
          {statusLabel(run)}
        </span>
      </div>

      <div className={cn(
        "text-[var(--color-text-muted)] tabular-nums",
        compact ? "mt-3 text-[12px]" : "mt-5 text-sm",
      )}>
        <span className="text-[var(--color-text)] font-medium">
          {run.line_count.toLocaleString()}
        </span>
        <span className="text-[var(--color-text-faint)] ml-1">log lines</span>
      </div>

      <div
        className={cn(
          "mt-auto border-t border-[var(--color-border)] flex items-center justify-between gap-3",
          compact ? "pt-2.5 mt-3" : "pt-4",
        )}
      >
        <span
          className={cn(
            "text-[var(--color-text-faint)] tabular-nums",
            compact ? "text-[10px]" : "text-[11px]",
          )}
        >
          Started {formatRelativeTime(run.started_at)}
        </span>
        <span
          className={cn(
            "font-medium inline-flex items-center gap-1",
            compact ? "text-[10px]" : "text-[11px]",
            "text-[var(--card-accent)]",
            selected ? "opacity-100" : "opacity-0 -translate-x-1 group-hover:opacity-100 group-hover:translate-x-0",
            "transition-[opacity,transform] duration-150 ease-out",
          )}
        >
          {selected ? "Viewing" : "View log"}
          <ChevronRight size={compact ? 11 : 12} />
        </span>
      </div>

      <span
        aria-hidden="true"
        className="pointer-events-none absolute inset-x-0 bottom-0 h-[2px] opacity-90"
        style={{
          background:
            "linear-gradient(90deg, var(--card-accent), transparent 70%)",
        }}
      />
    </button>
  );
}
