/**
 * DrilldownPanel, the disclosure surface that opens beneath a KpiCard
 * when the user clicks it.
 *
 * Pattern:
 *   <KpiCard onClick={() => toggle("errors")} selected={open === "errors"}
 *            controlsId="errors-panel" .../>
 *   <DrilldownPanel id="errors-panel" open={open === "errors"}
 *                   title="2 errors in this build">
 *     {errors.map(e => <ErrorRow ... />)}
 *   </DrilldownPanel>
 *
 * Why a panel and not a modal:
 * - Errors / token breakdowns are *contextual*, you want to scan them
 *   alongside the card you clicked, not in a centred dialog that
 *   obscures the rest of the page.
 * - Inline disclosure also keeps the keyboard story simple: tab order
 *   stays linear, no focus-trap needed.
 *
 * Animation: a max-height + opacity transition (200ms) avoids the jank
 * of `display: none ↔ block` while staying GPU-cheap. `prefers-reduced-
 * motion` is respected via the global rule in `index.css`.
 */
import { X } from "lucide-react";
import { type ReactNode } from "react";

import { cn } from "@/lib/cn";

export type DrilldownPanelProps = {
  /** Must match the `controlsId` of the KpiCard that opens it. */
  id: string;
  /** Open / closed state, caller-controlled. */
  open: boolean;
  /** Caller's close handler, wired to the X button + Esc keypress. */
  onClose?: () => void;
  /** Title shown at the top of the panel. */
  title: string;
  /** Optional subtitle for context. */
  subtitle?: string;
  /** When true, render an empty-state instead of the children if
   *  children would be empty. Caller can pass any ReactNode. */
  emptyState?: ReactNode;
  /** The drilldown rows, usually a list of errors, token breakdowns,
   *  per-phase durations, etc. */
  children?: ReactNode;
};

export function DrilldownPanel({
  id,
  open,
  onClose,
  title,
  subtitle,
  emptyState,
  children,
}: DrilldownPanelProps) {
  // We always render the markup so screen readers can find it via
  // `aria-controls`, but visually collapse to zero-height when closed.
  // `aria-hidden` flips so keyboard tab order skips the closed panel.
  return (
    <div
      id={id}
      role="region"
      aria-label={title}
      aria-hidden={!open}
      className={cn(
        "overflow-hidden transition-all duration-200 ease-out",
        open ? "max-h-[600px] opacity-100 mt-3" : "max-h-0 opacity-0 mt-0",
      )}
    >
      <div className="rounded-xl border border-[var(--color-accent-border)] bg-[var(--color-canvas-elev1)] shadow-lg">
        <header className="flex items-start justify-between gap-3 px-4 py-3 border-b border-[var(--color-border)]">
          <div className="min-w-0">
            <h3 className="text-sm font-medium text-[var(--color-text)] truncate">
              {title}
            </h3>
            {subtitle && (
              <p className="text-xs text-[var(--color-text-muted)] mt-0.5 truncate">
                {subtitle}
              </p>
            )}
          </div>
          {onClose && (
            <button
              type="button"
              onClick={onClose}
              aria-label="Close drilldown"
              className="-mr-1 -mt-1 p-1.5 rounded-md text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:bg-white/[0.04] transition-colors"
            >
              <X size={14} aria-hidden="true" />
            </button>
          )}
        </header>
        <div className="px-4 py-3 max-h-[480px] overflow-y-auto">
          {children ?? emptyState ?? (
            <div className="py-6 text-center text-sm text-[var(--color-text-faint)]">
              No items.
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
