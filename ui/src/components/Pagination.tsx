/**
 * Pagination, table footer control. Generic across every list page that
 * grows past a single screen (Plans, Pipelines, Profiles, Runs).
 *
 * Layout (left → right):
 *   - "1–10 of 28" range label (uses tabular-nums so the digit columns
 *     don't jitter as the page advances)
 *   - page-size select ("10 / 25 / 50 per page")
 *   - prev button · numbered page buttons (with `…` for long lists) · next
 *
 * Numeric window: we always show first + last, plus 1-2 neighbors of the
 * current page, with `…` filling the gaps. This keeps the footer narrow
 * even on a 200-page table. Window logic is in `pageWindow()` so it's
 * exhaustively testable without rendering.
 *
 * No library, Tailwind + cn() only. Tokens for surfaces / borders so the
 * footer tracks the rest of the app.
 */
import { ChevronLeft, ChevronRight } from "lucide-react";

import { cn } from "@/lib/cn";

export type PaginationProps = {
  /** 1-indexed current page. Caller owns state. */
  page: number;
  /** Items per page. */
  pageSize: number;
  /** Total items across all pages, used to compute "x–y of N". */
  total: number;
  onPageChange: (next: number) => void;
  onPageSizeChange?: (next: number) => void;
  /** Defaults to [10, 25, 50]. Pass [] to hide the size select. */
  pageSizes?: number[];
};

/** Returns the numbered-button window for the given state.
 *  Always includes first + last; uses `null` to mark a `…` gap. */
export function pageWindow(page: number, totalPages: number): (number | null)[] {
  // Trivial case, show all pages, no gap logic needed.
  if (totalPages <= 7) {
    return Array.from({ length: totalPages }, (_, i) => i + 1);
  }
  const out: (number | null)[] = [1];
  // Always show 1; show `…` if there's a gap to (page - 1).
  if (page > 4) out.push(null);
  // Window around the active page.
  const start = Math.max(2, page - 1);
  const end = Math.min(totalPages - 1, page + 1);
  for (let p = start; p <= end; p++) out.push(p);
  // Right `…` if there's a gap to the last page.
  if (page < totalPages - 3) out.push(null);
  out.push(totalPages);
  return out;
}

export function Pagination({
  page,
  pageSize,
  total,
  onPageChange,
  onPageSizeChange,
  pageSizes = [10, 25, 50],
}: PaginationProps) {
  const totalPages = Math.max(1, Math.ceil(total / pageSize));
  const clampedPage = Math.min(Math.max(1, page), totalPages);
  const from = total === 0 ? 0 : (clampedPage - 1) * pageSize + 1;
  const to = Math.min(total, clampedPage * pageSize);
  const window = pageWindow(clampedPage, totalPages);

  return (
    <div
      role="navigation"
      aria-label="Pagination"
      className="flex flex-wrap items-center justify-between gap-3 px-3 py-2.5 border-t border-[var(--color-border)]"
    >
      {/* Left: range label + page-size select */}
      <div className="flex items-center gap-3 text-xs text-[var(--color-text-muted)]">
        <span className="tabular-nums">
          {from}&ndash;{to} of {total.toLocaleString()}
        </span>
        {pageSizes.length > 0 && onPageSizeChange && (
          <label className="flex items-center gap-1.5">
            <span className="sr-only">Rows per page</span>
            <select
              value={pageSize}
              onChange={(e) => onPageSizeChange(Number(e.target.value))}
              className={cn(
                "h-7 pl-2 pr-1 rounded-md text-xs font-mono",
                "bg-[var(--color-canvas-elev1)] border border-[var(--color-border)]",
                "hover:border-[var(--color-border-strong)] focus-visible:border-[color:var(--color-accent-border)]",
                "transition-colors",
              )}
            >
              {pageSizes.map((n) => (
                <option key={n} value={n}>{n} / page</option>
              ))}
            </select>
          </label>
        )}
      </div>

      {/* Right: prev / numbered / next */}
      <div className="flex items-center gap-1">
        <PageButton
          ariaLabel="Previous page"
          disabled={clampedPage <= 1}
          onClick={() => onPageChange(clampedPage - 1)}
        >
          <ChevronLeft size={14} aria-hidden="true" />
        </PageButton>

        {window.map((p, i) =>
          p === null ? (
            <span
              key={`gap-${i}`}
              aria-hidden="true"
              className="px-1 text-[var(--color-text-faint)] select-none"
            >
              &hellip;
            </span>
          ) : (
            <PageButton
              key={p}
              ariaLabel={`Page ${p}`}
              ariaCurrent={p === clampedPage}
              active={p === clampedPage}
              onClick={() => onPageChange(p)}
            >
              {p}
            </PageButton>
          )
        )}

        <PageButton
          ariaLabel="Next page"
          disabled={clampedPage >= totalPages}
          onClick={() => onPageChange(clampedPage + 1)}
        >
          <ChevronRight size={14} aria-hidden="true" />
        </PageButton>
      </div>
    </div>
  );
}

function PageButton({
  children,
  active = false,
  disabled = false,
  ariaLabel,
  ariaCurrent,
  onClick,
}: {
  children: React.ReactNode;
  active?: boolean;
  disabled?: boolean;
  ariaLabel: string;
  ariaCurrent?: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      aria-label={ariaLabel}
      aria-current={ariaCurrent ? "page" : undefined}
      className={cn(
        "min-w-[28px] h-7 px-2 inline-flex items-center justify-center rounded-md",
        "text-xs font-mono transition-colors",
        "disabled:opacity-40 disabled:cursor-not-allowed",
        active
          ? "bg-[var(--color-accent-bg)] text-[var(--color-accent)] border border-[color:var(--color-accent-border)]"
          : "border border-transparent text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:bg-white/[0.04]",
      )}
    >
      {children}
    </button>
  );
}
