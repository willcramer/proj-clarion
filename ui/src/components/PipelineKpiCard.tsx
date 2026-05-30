/**
 * PipelineKpiCard, the per-build tile for the Pipelines list page.
 *
 * Mirrors the visual language of ProfileKpiCard + PlanKpiCard (accent
 * CSS var, smooth hover, reveal CTA, compact + full variants). Bound
 * to PipelineSummary data with its own tone mapping for build status
 * (running / done / failed / cancelled).
 *
 * Headline = host (parsed from `url`), since the pipeline_id is opaque
 * but the URL the build was started on is the human-readable identity.
 */
import { Activity, ChevronRight, Clock, Loader2 } from "lucide-react";

import { type PipelineSummary } from "@/lib/api";
import { cn } from "@/lib/cn";
import { formatDuration } from "@/lib/diagnose";

export type PipelineCardTone = "ready" | "in-review" | "draft" | "researching" | "failed";

function pipelineStatusTone(status: string): PipelineCardTone {
  switch (status) {
    case "running":   return "researching";
    case "done":      return "ready";
    case "failed":    return "failed";
    case "cancelled": return "in-review";
    default:          return "draft";
  }
}

const TONE_TO_CARD_ACCENT: Record<PipelineCardTone, { solid: string; soft: string }> = {
  ready:       { solid: "var(--color-accent)",     soft: "var(--color-accent-bg)" },
  "in-review": { solid: "var(--color-warning)",    soft: "var(--color-warning-bg)" },
  draft:       { solid: "var(--color-text-muted)", soft: "rgba(180, 200, 230, 0.05)" },
  researching: { solid: "var(--color-info)",       soft: "var(--color-info-bg)" },
  // Failed gets its own tone — the only place in the card system where
  // we use danger-red. Builds that fail need to pop in a scroll-by glance.
  failed:      { solid: "var(--color-danger)",     soft: "var(--color-danger-bg)" },
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

function hostOf(url: string): string {
  try { return new URL(url).host.replace(/^www\./, ""); }
  catch { return url; }
}

function durationMs(p: PipelineSummary): number | null {
  const start = new Date(p.started_at).getTime();
  const end = p.finished_at ? new Date(p.finished_at).getTime() : Date.now();
  return Number.isFinite(start) ? end - start : null;
}

export interface PipelineKpiCardProps {
  pipeline: PipelineSummary;
  onClick: () => void;
  selected?: boolean;
  compact?: boolean;
}

export function PipelineKpiCard({ pipeline, onClick, selected, compact = false }: PipelineKpiCardProps) {
  const tone = pipelineStatusTone(pipeline.status);
  const accent = TONE_TO_CARD_ACCENT[tone];
  const host = hostOf(pipeline.url);
  const isRunning = pipeline.status === "running";

  const stats = [
    { label: "Events",   value: pipeline.event_count, hint: "events recorded by the orchestrator" },
    { label: "Duration", value: formatDuration(durationMs(pipeline)) ?? "—", hint: "wall-clock time" },
  ];

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
        // Selected variant — when this card's pipeline is the one whose
        // events panel is open below. Stronger border + ring so the
        // "you're inspecting this one" signal is unmissable.
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

      {/* Top row — status icon bubble + host + status pill */}
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
            {host}
          </div>
          <div
            className={cn(
              "text-[var(--color-text-faint)] font-mono truncate",
              compact ? "text-[10px] mt-0.5" : "text-[11px] mt-1",
            )}
          >
            {pipeline.pipeline_id.slice(0, 8)}
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
          {pipeline.status}
        </span>
      </div>

      {/* Stats — 2 stats (events + duration) for pipelines. */}
      {compact ? (
        <div className="mt-3 text-[12px] text-[var(--color-text-muted)] tabular-nums truncate flex items-center gap-3">
          <span className="inline-flex items-center gap-1">
            <Activity size={11} className="text-[var(--color-text-faint)]" />
            <span className="text-[var(--color-text)] font-medium">{pipeline.event_count}</span>
            <span className="text-[var(--color-text-faint)]">events</span>
          </span>
          <span className="text-[var(--color-text-faint)]">·</span>
          <span className="inline-flex items-center gap-1">
            <Clock size={11} className="text-[var(--color-text-faint)]" />
            <span className="text-[var(--color-text)] font-medium">{formatDuration(durationMs(pipeline)) ?? "—"}</span>
          </span>
        </div>
      ) : (
        <div className="mt-5 grid grid-cols-2 gap-3">
          {stats.map((s) => (
            <div key={s.label} title={s.hint}>
              <div className="text-[20px] font-semibold tabular-nums tracking-tight text-[var(--color-text)] leading-none">
                {s.value}
              </div>
              <div className="text-[10px] uppercase tracking-[0.08em] text-[var(--color-text-faint)] mt-1.5">
                {s.label}
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Footer */}
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
          Started {formatRelativeTime(pipeline.started_at)}
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
          {selected ? "Inspecting" : "Inspect"}
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
