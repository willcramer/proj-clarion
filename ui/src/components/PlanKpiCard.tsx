/**
 * PlanKpiCard, the per-plan tile for the Plans list page.
 *
 * Mirrors ProfileKpiCard's visual language (per-card `--card-accent`
 * driving bubble + status pill + bottom bar, smooth hover lift, reveal
 * CTA, compact + full variants) but bound to PlanSummary data:
 *
 *   - Headline = plan_id_short (mono, since plan IDs aren't names)
 *   - Sub-line = source_profile_id (mono, smaller)
 *   - Stats   = Processes · KG nodes · Alerts (always present on a
 *               PlanSummary, all have real variance, so no adaptive
 *               shape like Profile needs)
 *   - Tone    = derived from review_state via PLAN_STATE_TO_TONE
 *
 * Bubble holds a ClipboardList icon rather than a letter — plan IDs
 * aren't memorable enough for an "initial" to mean anything.
 */
import { ChevronRight, ClipboardList, Loader2 } from "lucide-react";

import { type PlanSummary } from "@/lib/api";
import { cn } from "@/lib/cn";

export type PlanCardTone = "ready" | "in-review" | "draft" | "researching";

/** Map a plan review_state to the card-accent tone. Keeps the visual
 *  language identical to ProfileKpiCard so users learn one palette,
 *  not two. */
export function planStateTone(state: string, pending?: boolean): PlanCardTone {
  if (pending) return "researching";
  switch (state) {
    case "provisioned":
    case "approved_for_provision":
      return "ready";
    case "se_reviewed":
      return "in-review";
    case "planning":
      return "researching";
    case "draft":
    case "torn_down":
    case "archived":
    default:
      return "draft";
  }
}

const TONE_TO_CARD_ACCENT: Record<PlanCardTone, { solid: string; soft: string }> = {
  ready:       { solid: "var(--color-accent)",     soft: "var(--color-accent-bg)" },
  "in-review": { solid: "var(--color-warning)",    soft: "var(--color-warning-bg)" },
  draft:       { solid: "var(--color-text-muted)", soft: "rgba(180, 200, 230, 0.05)" },
  researching: { solid: "var(--color-info)",       soft: "var(--color-info-bg)" },
};

function formatRelative(iso: string): string {
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

export interface PlanKpiCardProps {
  plan: PlanSummary;
  onClick: () => void;
  /** Compact variant for high-density list pages. Single inline stat
   *  line instead of a 3-col tabular grid; no corner gradient. */
  compact?: boolean;
}

export function PlanKpiCard({ plan, onClick, compact = false }: PlanKpiCardProps) {
  const isInflight = !!plan.pending;
  const tone = planStateTone(plan.review_state, plan.pending);
  const accent = TONE_TO_CARD_ACCENT[tone];

  // Status label — readable version of the review_state enum, plus
  // a special "Planning" for in-flight rows since the underlying
  // state column might be empty.
  const statusLabel = isInflight
    ? "Planning"
    : plan.review_state.replace(/_/g, " ");

  const cta = isInflight ? "View build" : "Open plan";

  const stats = [
    { label: "Proc",       value: plan.process_count,   hint: "business processes covered" },
    { label: "KG nodes",   value: plan.kg_node_count,   hint: "entities in the knowledge graph" },
    { label: "Dashboards", value: plan.dashboard_count, hint: "dashboards the plan deploys" },
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

      {/* Top row — icon bubble + plan id + status pill */}
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
          {isInflight ? (
            <Loader2 size={compact ? 13 : 16} className="animate-spin" />
          ) : (
            <ClipboardList size={compact ? 14 : 16} />
          )}
        </span>
        <div className="min-w-0 flex-1">
          <div
            className={cn(
              "font-semibold tracking-tight text-[var(--color-text)] truncate leading-tight font-mono",
              compact ? "text-[13px]" : "text-[15px]",
            )}
          >
            {isInflight ? "Planning…" : plan.plan_id_short}
          </div>
          <div
            className={cn(
              "text-[var(--color-text-faint)] font-mono truncate",
              compact ? "text-[10px] mt-0.5" : "text-[11px] mt-1",
            )}
          >
            from {plan.source_profile_id}
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
          {statusLabel}
        </span>
      </div>

      {/* Stats — adaptive shape, same as ProfileKpiCard. */}
      {!isInflight && (
        compact ? (
          <div
            className="mt-3 text-[12px] text-[var(--color-text-muted)] tabular-nums truncate"
            title={stats.map((s) => `${s.value} ${s.label.toLowerCase()}`).join(" · ")}
          >
            {stats.map((s, i) => (
              <span key={s.label}>
                <span className="text-[var(--color-text)] font-medium">
                  {s.value.toLocaleString()}
                </span>
                <span className="text-[var(--color-text-faint)] ml-1">
                  {s.label.toLowerCase()}
                </span>
                {i < stats.length - 1 && (
                  <span className="text-[var(--color-text-faint)] mx-1.5">·</span>
                )}
              </span>
            ))}
          </div>
        ) : (
          <div className="mt-5 grid grid-cols-3 gap-3">
            {stats.map((s) => (
              <div key={s.label} title={s.hint}>
                <div className="text-[20px] font-semibold tabular-nums tracking-tight text-[var(--color-text)] leading-none">
                  {s.value.toLocaleString()}
                </div>
                <div className="text-[10px] uppercase tracking-[0.08em] text-[var(--color-text-faint)] mt-1.5">
                  {s.label}
                </div>
              </div>
            ))}
          </div>
        )
      )}

      {/* Footer — relative time + reveal CTA */}
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
          {isInflight ? "Planning…" : `Updated ${formatRelative(plan.updated_at)}`}
        </span>
        <span
          className={cn(
            "font-medium inline-flex items-center gap-1",
            compact ? "text-[10px]" : "text-[11px]",
            "text-[var(--card-accent)]",
            "opacity-0 -translate-x-1",
            "group-hover:opacity-100 group-hover:translate-x-0",
            "transition-[opacity,transform] duration-150 ease-out",
          )}
        >
          {cta}
          <ChevronRight size={compact ? 11 : 12} />
        </span>
      </div>

      {/* Bottom accent bar */}
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
