/**
 * SampleDataSourcesCard — section 6b.
 *
 * Pill-row list showing each data stream the generator emits when a
 * demo runs (orders, inventory, traces, logs, metrics) with a mono
 * volume on the right.
 *
 * Today the planner doesn't enumerate per-plan sample streams, so
 * `deriveSampleSources` returns a fixed set with illustrative volumes.
 * Swap to real per-plan data when the backend ships it.
 */
import { ChevronRight, Database } from "lucide-react";

import { Card } from "@/components/Card";
import { cn } from "@/lib/cn";
import type { SampleDataSource } from "@/lib/plan-derivations";

export function SampleDataSourcesCard({
  sources,
  onViewSpec,
}: {
  sources: SampleDataSource[];
  onViewSpec?: () => void;
}) {
  return (
    <Card className="overflow-hidden">
      <div className="px-5 py-4 border-b border-[var(--color-border)] flex items-center justify-between gap-3">
        <h3 className="text-sm font-medium text-[var(--color-text)] m-0">
          Sample data sources
        </h3>
        <span className="font-mono text-[11px] text-[var(--color-text-faint)] tabular-nums">
          {sources.length} streams
        </span>
      </div>
      <ul className="divide-y divide-[var(--color-border)]">
        {sources.map((s) => (
          <li key={s.id} className="flex items-center gap-3 px-5 py-3">
            <span
              aria-hidden="true"
              className="inline-flex items-center justify-center w-7 h-7 rounded-md bg-[var(--color-canvas-elev2)] text-[var(--color-text-faint)] border border-[var(--color-border)] shrink-0"
            >
              <Database size={13} />
            </span>
            <div className="flex-1 min-w-0">
              <div className="text-sm text-[var(--color-text)]">{s.label}</div>
              <div className="text-[11px] font-mono text-[var(--color-text-faint)] truncate">
                {s.sub}
              </div>
            </div>
            <span className="font-mono text-[11px] text-[var(--color-text-muted)] tabular-nums whitespace-nowrap">
              {s.volume}
            </span>
          </li>
        ))}
      </ul>
      <div className="border-t border-[var(--color-border)]">
        <button
          type="button"
          onClick={onViewSpec}
          disabled={!onViewSpec}
          className={cn(
            "w-full px-5 py-3 text-xs font-medium flex items-center justify-center gap-1",
            "text-[var(--color-text-muted)] hover:text-[var(--color-text)]",
            "transition-colors",
            "disabled:opacity-50 disabled:cursor-not-allowed",
          )}
        >
          View generation spec
          <ChevronRight size={12} />
        </button>
      </div>
    </Card>
  );
}
