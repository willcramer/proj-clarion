/**
 * KpiCard — the foundational stat tile for the Dashboard, the build
 * runner, and the pipeline detail page.
 *
 * Three things make it useful beyond a vanilla number-with-label:
 *
 * 1. **Tone-aware** — the value's colour reflects state (success / warning
 *    / danger / accent / muted). The whole card subtly tints to match
 *    when `tone` is non-neutral, so an "Errors: 2" tile reads as a
 *    danger signal at a glance, not just text the eye has to parse.
 *
 * 2. **Drill-downable** — pass `onClick` and the card becomes a button
 *    with a hover state, focus ring, and an aria-controlled relationship
 *    to a `<DrilldownPanel>`. The pattern: caller renders the card +
 *    panel side-by-side, threads a single `selected` boolean and an
 *    `onToggle` to flip both. Cards with `onClick` get an arrow glyph
 *    on hover to telegraph "more here".
 *
 * 3. **Trend-aware** — `delta` (number) renders as a +/- pill; `trend`
 *    (sparkline data) renders as a tiny inline sparkline. Either or
 *    both. Both default to undefined (omitted), so the card is dense
 *    when you don't need them.
 *
 * The card is responsive: sizes down to one column on mobile, two on
 * sm:, the calling grid decides further.
 */
import { ChevronRight } from "lucide-react";
import { type ComponentType, type ReactNode } from "react";

import { cn } from "@/lib/cn";

export type KpiTone =
  | "neutral"
  | "accent"
  | "success"
  | "warning"
  | "danger"
  | "info"
  | "grafana";

export type KpiCardProps = {
  /** Lucide icon component (or any 14×14-friendly SVG component). */
  icon?: ComponentType<{ size?: number; className?: string }>;
  /** Top-line label, e.g. "Errors", "Tokens used", "Active builds". */
  label: string;
  /** The big number / value. Pre-formatted by the caller. */
  value: ReactNode;
  /** Smaller helper text under the value, e.g. "across 6 phases". */
  hint?: string;
  /** Drives both the value's colour and a faint card-edge tint. */
  tone?: KpiTone;
  /** Numeric delta vs. previous period; renders +N / -N pill. */
  delta?: number;
  /** Suffix attached to delta (e.g. "%", "tokens"). */
  deltaUnit?: string;
  /** Optional inline sparkline data (caller-provided values, normalized). */
  trend?: number[];
  /** Click handler. Presence makes the card a button with focus ring +
   *  hover state + chevron affordance. */
  onClick?: () => void;
  /** When true, marks this card as the currently-expanded drilldown.
   *  Caller-controlled state. Sets `aria-expanded` and ties to
   *  `aria-controls` via `controlsId`. */
  selected?: boolean;
  /** ID of the panel this card opens, for `aria-controls`. */
  controlsId?: string;
  /** Render smaller (used in dense Dashboard grids). */
  compact?: boolean;
};

const TONE_TO_VALUE_CLASS: Record<KpiTone, string> = {
  neutral: "text-[var(--color-text)]",
  accent:  "text-[var(--color-accent)]",
  success: "text-[var(--color-success)]",
  warning: "text-[var(--color-warning)]",
  danger:  "text-[var(--color-danger)]",
  info:    "text-[var(--color-info)]",
  grafana: "text-[var(--color-grafana)]",
};

const TONE_TO_BG_CLASS: Record<KpiTone, string> = {
  neutral: "",
  accent:  "before:bg-[var(--color-accent-bg)]",
  success: "before:bg-[var(--color-success-bg)]",
  warning: "before:bg-[var(--color-warning-bg)]",
  danger:  "before:bg-[var(--color-danger-bg)]",
  info:    "before:bg-[var(--color-info-bg)]",
  grafana: "before:bg-[var(--color-grafana-bg)]",
};

export function KpiCard({
  icon: Icon,
  label,
  value,
  hint,
  tone = "neutral",
  delta,
  deltaUnit = "",
  trend,
  onClick,
  selected,
  controlsId,
  compact = false,
}: KpiCardProps) {
  const interactive = !!onClick;
  const Wrapper = interactive ? "button" : "div";
  const interactiveProps = interactive
    ? {
        type: "button" as const,
        onClick,
        "aria-expanded": selected ?? undefined,
        "aria-controls": controlsId,
      }
    : {};

  return (
    <Wrapper
      {...interactiveProps}
      className={cn(
        // Base card — glass-ish surface, faint border, subtle tint via
        // ::before pseudo-element so the tint sits below content but
        // above the border.
        "relative isolate text-left rounded-xl border transition-all",
        "before:absolute before:inset-0 before:rounded-xl before:opacity-0 before:transition-opacity before:-z-10",
        TONE_TO_BG_CLASS[tone],
        "bg-[var(--color-canvas-elev1)] border-[var(--color-border)]",
        compact ? "p-3" : "p-4",
        // Interactive states
        interactive && "cursor-pointer hover:border-[var(--color-border-strong)] hover:before:opacity-100 group",
        selected && "border-[var(--color-accent-border)] before:opacity-100 ring-1 ring-[var(--color-accent-border)]",
      )}
    >
      <div className="flex items-start justify-between gap-2">
        <div className="flex items-center gap-2 min-w-0">
          {Icon && (
            <Icon
              size={compact ? 12 : 14}
              className={cn(
                "shrink-0",
                tone === "neutral"
                  ? "text-[var(--color-text-faint)]"
                  : TONE_TO_VALUE_CLASS[tone],
              )}
            />
          )}
          <span
            className={cn(
              "uppercase tracking-wider truncate",
              compact ? "text-[10px]" : "text-[11px]",
              "text-[var(--color-text-muted)]",
            )}
          >
            {label}
          </span>
        </div>
        {interactive && (
          <ChevronRight
            size={14}
            aria-hidden="true"
            className={cn(
              "shrink-0 text-[var(--color-text-faint)] transition-transform",
              "opacity-0 group-hover:opacity-100",
              selected && "opacity-100 rotate-90",
            )}
          />
        )}
      </div>

      <div
        className={cn(
          "mt-2 font-semibold tabular-nums",
          compact ? "text-xl" : "text-2xl",
          TONE_TO_VALUE_CLASS[tone],
        )}
      >
        {value}
      </div>

      {(hint || delta !== undefined || trend) && (
        <div className="mt-1.5 flex items-center justify-between gap-2 text-[11px]">
          {hint && (
            <span className="text-[var(--color-text-faint)] truncate">
              {hint}
            </span>
          )}
          <div className="ml-auto flex items-center gap-2">
            {trend && trend.length > 1 && <Sparkline data={trend} tone={tone} />}
            {delta !== undefined && (
              <DeltaPill delta={delta} unit={deltaUnit} />
            )}
          </div>
        </div>
      )}
    </Wrapper>
  );
}

// ─── DeltaPill — colour-coded change indicator ─────────────────────

function DeltaPill({ delta, unit }: { delta: number; unit: string }) {
  if (delta === 0) {
    return (
      <span className="px-1.5 py-0.5 rounded text-[10px] font-mono text-[var(--color-text-faint)] bg-white/[0.04]">
        ±0{unit}
      </span>
    );
  }
  const positive = delta > 0;
  return (
    <span
      className={cn(
        "px-1.5 py-0.5 rounded text-[10px] font-mono",
        positive
          ? "bg-[var(--color-success-bg)] text-[var(--color-success)]"
          : "bg-[var(--color-danger-bg)] text-[var(--color-danger)]",
      )}
    >
      {positive ? "+" : ""}
      {delta}
      {unit}
    </span>
  );
}

// ─── Sparkline — minimal SVG, no library ───────────────────────────

function Sparkline({ data, tone }: { data: number[]; tone: KpiTone }) {
  // Normalize to 0..1 for the viewBox. If all values are equal, draw
  // a flat line at the midpoint instead of dividing by zero.
  const min = Math.min(...data);
  const max = Math.max(...data);
  const range = max - min || 1;
  const W = 60;
  const H = 14;
  const step = W / (data.length - 1);

  const points = data
    .map((v, i) => {
      const x = i * step;
      const y = H - ((v - min) / range) * H;
      return `${x.toFixed(2)},${y.toFixed(2)}`;
    })
    .join(" ");

  const stroke =
    tone === "neutral"
      ? "var(--color-text-muted)"
      : `var(--color-${tone === "grafana" ? "grafana" : tone})`;

  return (
    <svg
      viewBox={`0 0 ${W} ${H}`}
      width={W}
      height={H}
      aria-hidden="true"
      className="shrink-0"
    >
      <polyline
        fill="none"
        stroke={stroke}
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
        points={points}
      />
    </svg>
  );
}
