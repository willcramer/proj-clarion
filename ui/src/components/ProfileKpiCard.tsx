/**
 * ProfileKpiCard, the shared "one card per researched company" tile.
 *
 * Visual language: per-card `--card-accent` / `--card-accent-bg` CSS
 * custom properties drive a corner radial-gradient glow, the initial
 * bubble, the status pill, the hover CTA, and a bottom 2px accent bar.
 * Smooth 150ms hover: translateY(-1px) + shadow ramp + border-strong
 * + reveal of a tone-matched CTA. Pattern adapted from the
 * Opportunity Overview KPI tiles.
 *
 * Used by:
 *   - Dashboard's DemoLibrary section (top-6 most recent profiles)
 *   - Profiles list page (full grid of every researched company)
 *
 * Both surfaces want the same affordances: status-tone tells you
 * readiness at a glance, stats strip surfaces numbers that have
 * variance (no degenerate "1 plan" hero), hover reveals the next
 * action without a click.
 */
import { ChevronRight, Loader2 } from "lucide-react";

import { type PlanSummary, type ProfileSummary } from "@/lib/api";
import { cn } from "@/lib/cn";

export type DemoStatusTone = "ready" | "in-review" | "draft" | "researching";

export interface DemoStatus {
  tone: DemoStatusTone;
  label: string;
}

/** Derive a single demo-readiness status from a profile + its plans.
 *  Priority (most-actionable first):
 *    - "researching", pipeline still running, profile not finalized
 *    - "ready", at least one plan is provisioned (live in Cloud)
 *    - "in-review", at least one plan approved or awaiting review
 *    - "draft", profile exists, no usable plan yet
 */
export function deriveDemoStatus(
  p: ProfileSummary,
  plans: PlanSummary[],
): DemoStatus {
  if (p.pending) return { tone: "researching", label: "Researching" };
  const states = new Set(plans.map((pl) => pl.review_state));
  if (states.has("provisioned"))            return { tone: "ready",     label: "Ready to demo" };
  if (states.has("approved_for_provision")) return { tone: "ready",     label: "Approved" };
  if (states.has("se_reviewed"))            return { tone: "in-review", label: "In review" };
  if (plans.length > 0)                     return { tone: "in-review", label: "Drafted plan" };
  return { tone: "draft", label: "Just researched" };
}

/** Tone palette — each state on a distinct hue so a grid of cards
 *  never collapses into one colour. Ready = teal (Clarion brand accent),
 *  in-review = amber (needs-attention warmth), researching = info blue,
 *  draft = neutral muted. */
const TONE_TO_CARD_ACCENT: Record<
  DemoStatusTone,
  { solid: string; soft: string }
> = {
  ready:       { solid: "var(--color-accent)",     soft: "var(--color-accent-bg)" },
  "in-review": { solid: "var(--color-warning)",    soft: "var(--color-warning-bg)" },
  draft:       { solid: "var(--color-text-muted)", soft: "rgba(180, 200, 230, 0.05)" },
  researching: { solid: "var(--color-info)",       soft: "var(--color-info-bg)" },
};

export function hostOf(url: string | null | undefined): string {
  if (!url) return "—";
  try { return new URL(url).host.replace(/^www\./, ""); }
  catch { return url; }
}

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

export interface ProfileKpiCardProps {
  profile: ProfileSummary;
  plans: PlanSummary[];
  onClick: () => void;
  /** Compact variant for high-density list pages (e.g. /profiles).
   *  Shrinks the stat block from a 3-column grid of 20px tabular numbers
   *  to a single inline line of 12px text, drops the corner gradient,
   *  and tightens padding. Same visual language, ~70% less ink per
   *  card so a 25-card grid stops feeling crammed. */
  compact?: boolean;
}

export function ProfileKpiCard({ profile, plans, onClick, compact = false }: ProfileKpiCardProps) {
  const status = deriveDemoStatus(profile, plans);
  const host = hostOf(profile.primary_url);
  const initial =
    (profile.company_name || host || "?").trim()[0]?.toUpperCase() ?? "?";
  const isInflight = status.tone === "researching";

  const accent = TONE_TO_CARD_ACCENT[status.tone];
  const cta =
    isInflight              ? "View build" :
    status.tone === "ready" ? "Open plan" :
    plans.length > 0        ? "Open profile" :
                              "Refine";

  // Adaptive stats — pick numbers that actually differ across cards at
  // this profile's state. With a plan attached we show demo richness
  // (KG nodes, processes) alongside pain. Without a plan yet, fall back
  // to research-only signals.
  const primaryPlan = plans[0];
  const stats: { label: string; value: number; hint?: string }[] = primaryPlan
    ? [
        { label: "Pain",      value: profile.pain_signal_count, hint: "pain signals captured" },
        { label: "Processes", value: primaryPlan.process_count, hint: "business processes covered" },
        { label: "KG nodes",  value: primaryPlan.kg_node_count, hint: "entities in the knowledge graph" },
      ]
    : [
        { label: "Pain",    value: profile.pain_signal_count,      hint: "pain signals captured" },
        { label: "Tech",    value: profile.tech_signal_count,      hint: "tech signals captured" },
        { label: "Pending", value: profile.synthesized_flag_count, hint: "AI claims to review" },
      ];

  return (
    <button
      type="button"
      onClick={onClick}
      // React types reject CSS custom properties on `style`; the cast
      // is the standard workaround. Children resolve these via
      // `var(--card-accent)` in arbitrary-value classes.
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
      {/* Corner accent glow — radial top-right, fades out. Suppressed
          in compact mode where 20+ overlapping gradients turn into
          visual noise; the bottom bar carries the tone cue alone. */}
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

      {/* Top row — initial bubble + company name + status pill */}
      <div className={cn("flex items-start", compact ? "gap-2.5" : "gap-3")}>
        <span
          aria-hidden="true"
          className={cn(
            "inline-flex items-center justify-center rounded-lg shrink-0",
            "font-semibold",
            compact ? "w-8 h-8 text-xs" : "w-10 h-10 text-sm",
            "bg-[var(--card-accent-bg)] text-[var(--card-accent)]",
            "border border-[color:var(--card-accent)]/30",
          )}
        >
          {isInflight
            ? <Loader2 size={compact ? 13 : 16} className="animate-spin" />
            : initial}
        </span>
        <div className="min-w-0 flex-1">
          <div
            className={cn(
              "font-semibold tracking-tight text-[var(--color-text)] truncate leading-tight",
              compact ? "text-[14px]" : "text-[17px]",
            )}
          >
            {profile.company_name ?? host ?? "Untitled profile"}
          </div>
          <div
            className={cn(
              "text-[var(--color-text-faint)] font-mono truncate",
              compact ? "text-[10px] mt-0.5" : "text-[11px] mt-1",
            )}
          >
            {host}
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
          {status.label}
        </span>
      </div>

      {/* Stats — adaptive shape. Full mode = 3-col tabular grid of 20px
          numbers (good at 6 cards). Compact mode = single inline line
          of small text (much quieter at 25+ cards). */}
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
              <ProfileKpiStat
                key={s.label}
                label={s.label}
                value={s.value}
                title={s.hint}
              />
            ))}
          </div>
        )
      )}

      {/* Footer — relative time + reveal-on-hover CTA. */}
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
          {isInflight ? "Researching…" : `Researched ${formatRelative(profile.created_at)}`}
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

      {/* Bottom accent bar — keeps a grid reading as a coordinated set. */}
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

function ProfileKpiStat({
  label, value, title,
}: { label: string; value: number; title?: string }) {
  return (
    <div title={title}>
      <div className="text-[20px] font-semibold tabular-nums tracking-tight text-[var(--color-text)] leading-none">
        {value.toLocaleString()}
      </div>
      <div className="text-[10px] uppercase tracking-[0.08em] text-[var(--color-text-faint)] mt-1.5">
        {label}
      </div>
    </div>
  );
}
