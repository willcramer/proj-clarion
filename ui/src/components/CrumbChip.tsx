/**
 * CrumbChip, a small pill-shaped link used in page-header crumb rows
 * to jump between related resources (profile → plan → pipeline).
 *
 * Why it exists: pre-redesign these were just bare underlined Links
 * in a faint-mono row. The hit target was tiny and looked like meta-
 * data, so SEs missed that the IDs were clickable at all. The chip
 * gives the crumb a real button affordance: bordered surface, chevron
 * glyph, accent-coloured hover state. The information density stays
 * roughly the same, but the "click me" reads at a glance.
 *
 * Used in:
 *   - PlanHeader (`source profile`, `built by`)
 *   - PipelineRunView crumb (`profile`, `plan`)
 *
 * Visually neighbour-aware: drop a row of CrumbChips into any
 * `flex flex-wrap gap-2` container and they stack nicely.
 */
import { ChevronRight, type LucideIcon } from "lucide-react";
import { Link } from "react-router-dom";

import { cn } from "@/lib/cn";

export type CrumbChipProps = {
  /** Router target, or absolute URL when `external`. */
  to: string;
  /** Small uppercase label (e.g. "source profile"). */
  label: string;
  /** Mono value rendered next to the label (e.g. "prof-hyster-001"). */
  value: string;
  /** Optional leading icon (12px). */
  icon?: LucideIcon;
  /** Open in a new tab; uses an <a> instead of router Link. */
  external?: boolean;
  /** Optional native title attr for hover hint. */
  title?: string;
};

export function CrumbChip({
  to, label, value, icon: Icon, external, title,
}: CrumbChipProps) {
  const inner = (
    <>
      {Icon && (
        <Icon
          size={12}
          aria-hidden="true"
          className="shrink-0 text-[var(--color-text-faint)] group-hover:text-[var(--color-accent)] transition-colors"
        />
      )}
      <span className="uppercase tracking-[0.06em] text-[10px] text-[var(--color-text-faint)] group-hover:text-[var(--color-text-muted)] transition-colors">
        {label}
      </span>
      <span className="text-[11px] text-[var(--color-text)] group-hover:text-[var(--color-accent)] transition-colors">
        {value}
      </span>
      <ChevronRight
        size={11}
        aria-hidden="true"
        className="shrink-0 text-[var(--color-text-faint)] group-hover:text-[var(--color-accent)] group-hover:translate-x-0.5 transition-all"
      />
    </>
  );

  const className = cn(
    "group inline-flex items-center gap-1.5 h-7 px-2.5 rounded-md font-mono",
    "bg-[var(--color-canvas-elev1)] border border-[var(--color-border)]",
    "hover:border-[color:var(--color-accent-border)] hover:bg-[var(--color-accent-bg)]",
    "focus-visible:border-[color:var(--color-accent-border)] focus-visible:bg-[var(--color-accent-bg)]",
    "transition-colors",
  );

  if (external) {
    return (
      <a
        href={to}
        target="_blank"
        rel="noreferrer"
        title={title}
        className={className}
      >
        {inner}
      </a>
    );
  }
  return (
    <Link to={to} title={title} className={className}>
      {inner}
    </Link>
  );
}
