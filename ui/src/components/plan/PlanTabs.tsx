/**
 * PlanTabs — controlled tab strip for the plan-detail body. Owns no
 * state of its own; the parent decides which tab is active and which
 * panel to render. Replaces an earlier anchor-scroll prototype, the
 * single-panel-at-a-time model keeps the page short and lets each
 * section pull its data lazily if needed.
 *
 * Visual: 2px var(--accent) bottom border on the active tab; muted
 * text on inactive; right-aligned mono "approved by" stamp when the
 * plan has it.
 */
import { cn } from "@/lib/cn";

export interface PlanTab {
  id: string;
  label: string;
  count?: number;
}

export const DEFAULT_PLAN_TAB_IDS = [
  "processes",
  "dashboards",
  "telemetry",
  "incident",
  "audit",
] as const;
export type PlanTabId = typeof DEFAULT_PLAN_TAB_IDS[number];

export function PlanTabs({
  tabs,
  activeId,
  onChange,
  approvedBy,
}: {
  tabs: PlanTab[];
  activeId: string;
  onChange: (id: string) => void;
  approvedBy?: { name: string; at: string } | null;
}) {
  return (
    <div
      className={cn(
        "sticky top-16 z-20",
        // Slight blur backdrop so the tab strip reads through content
        // scrolling underneath without a hard background change.
        "backdrop-blur bg-[var(--color-canvas)]/85",
        "border-b border-[var(--color-border)]",
        "-mx-4 sm:-mx-6 px-4 sm:px-6",
      )}
    >
      <div className="flex items-end gap-1 flex-wrap">
        {tabs.map((t) => {
          const active = t.id === activeId;
          return (
            <button
              key={t.id}
              type="button"
              onClick={() => onChange(t.id)}
              role="tab"
              aria-selected={active}
              aria-current={active ? "page" : undefined}
              className={cn(
                "px-3 h-9 text-xs font-medium transition-colors inline-flex items-center gap-2",
                "border-b-2",
                active
                  ? "text-[var(--color-text)] border-[var(--color-accent)]"
                  : "text-[var(--color-text-muted)] hover:text-[var(--color-text)] border-transparent",
              )}
            >
              {t.label}
              {typeof t.count === "number" && (
                <span
                  className={cn(
                    "font-mono text-[10px] tabular-nums px-1 rounded",
                    active
                      ? "text-[var(--color-accent)] bg-[var(--color-accent-bg)]"
                      : "text-[var(--color-text-faint)] bg-[var(--color-canvas-elev2)]/60",
                  )}
                >
                  {t.count}
                </span>
              )}
            </button>
          );
        })}
        {approvedBy && (
          <span className="ml-auto py-2 text-[11px] font-mono text-[var(--color-text-faint)] truncate">
            approved by <span className="text-[var(--color-text-muted)]">{approvedBy.name}</span>
            <span className="mx-1">·</span>
            {approvedBy.at}
          </span>
        )}
      </div>
    </div>
  );
}
