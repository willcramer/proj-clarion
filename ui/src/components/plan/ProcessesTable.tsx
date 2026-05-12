/**
 * ProcessesTable — section 2 of the plan-detail expansion.
 *
 * One row per `business_process_model`. Columns: Process / Tier /
 * Latency SLO / Error SLO / Signals / Health. Paginates 6/page.
 *
 * Rows are clickable: clicking expands the row to show the raw
 * business_steps and failure_modes from the plan JSON. Click again
 * to collapse. Multiple rows can be open at once.
 *
 * Health is "not-instrumented" until the backend ships per-process
 * telemetry. Those rows render at opacity-60 per the spec.
 */
import { useMemo, useState } from "react";
import { ChevronDown, ChevronRight } from "lucide-react";

import { Badge, type BadgeTone } from "@/components/Badge";
import { Card } from "@/components/Card";
import { Pagination } from "@/components/Pagination";
import { cn } from "@/lib/cn";
import {
  extractLabel,
  type ProcessRow, type ProcessTier,
} from "@/lib/plan-derivations";

export function ProcessesTable({ rows }: { rows: ProcessRow[] }) {
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(6);
  // Expanded process ids. Using a Set lets us toggle individual rows
  // open without juggling N booleans in component state.
  const [expanded, setExpanded] = useState<Set<string>>(new Set());

  const paged = useMemo(
    () => rows.slice((page - 1) * pageSize, page * pageSize),
    [rows, page, pageSize],
  );

  if (rows.length === 0) {
    return (
      <Card className="p-8 text-center text-sm text-[var(--color-text-muted)]">
        No processes captured yet. Re-run research to extract them.
      </Card>
    );
  }

  function toggle(id: string) {
    setExpanded((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  return (
    <Card className="overflow-hidden">
      <table className="w-full text-sm">
        <thead className="text-[10px] text-[var(--color-text-faint)] uppercase tracking-wider font-mono border-b border-[var(--color-border)]">
          <tr>
            <th className="text-left  font-medium px-2 py-2.5 w-6" aria-label="Expand"></th>
            <th className="text-left  font-medium px-4 py-2.5">Process</th>
            <th className="text-left  font-medium px-4 py-2.5">Tier</th>
            <th className="text-left  font-medium px-4 py-2.5">Latency SLO</th>
            <th className="text-left  font-medium px-4 py-2.5">Error SLO</th>
            <th className="text-right font-medium px-4 py-2.5">Signals</th>
            <th className="text-right font-medium px-4 py-2.5">Health</th>
          </tr>
        </thead>
        <tbody>
          {paged.map((p) => (
            <ProcessRowView
              key={p.id}
              row={p}
              open={expanded.has(p.id)}
              onToggle={() => toggle(p.id)}
            />
          ))}
        </tbody>
      </table>
      <Pagination
        page={page}
        pageSize={pageSize}
        total={rows.length}
        onPageChange={setPage}
        onPageSizeChange={(n) => { setPageSize(n); setPage(1); }}
        pageSizes={[6, 12, 24]}
      />
    </Card>
  );
}

function ProcessRowView({
  row, open, onToggle,
}: { row: ProcessRow; open: boolean; onToggle: () => void }) {
  const notInstrumented = row.health === "not-instrumented";
  return (
    <>
      <tr
        onClick={onToggle}
        className={cn(
          "border-b border-[var(--color-border)] last:border-0 cursor-pointer transition-colors",
          "hover:bg-white/[0.02]",
          notInstrumented && "opacity-60",
          open && "bg-white/[0.02]",
        )}
      >
        <td className="px-2 py-3 text-[var(--color-text-faint)]">
          {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        </td>
        <td className="px-4 py-3">
          <div className="font-medium text-[var(--color-text)]">{row.name}</div>
          <div className="text-[11px] text-[var(--color-text-faint)] truncate max-w-[420px]">
            {row.description}
          </div>
        </td>
        <td className="px-4 py-3">
          <Badge tone={tierTone(row.tier)}>{row.tier}</Badge>
        </td>
        <td className="px-4 py-3 font-mono text-xs text-[var(--color-text-muted)]">
          {row.latencySlo ?? "—"}
        </td>
        <td className="px-4 py-3 font-mono text-xs text-[var(--color-text-muted)]">
          {row.errorSlo ?? "—"}
        </td>
        <td className="px-4 py-3 text-right font-mono text-xs tabular-nums text-[var(--color-text-muted)]">
          {row.signalCount}
        </td>
        <td className="px-4 py-3 text-right">
          <Badge tone={healthTone(row.health)}>{healthLabel(row.health)}</Badge>
        </td>
      </tr>
      {open && (
        <tr className="border-b border-[var(--color-border)] last:border-0">
          <td colSpan={7} className="px-6 pt-2 pb-5 bg-[var(--color-canvas)]/40">
            <div className="grid gap-5 md:grid-cols-2">
              <DetailList
                title="Business steps"
                items={row.steps}
                empty="No steps captured."
              />
              <DetailList
                title="Failure modes"
                items={row.failureModes}
                empty="No failure modes captured."
                ordered={false}
              />
            </div>
            <div className="mt-3 text-[11px] font-mono text-[var(--color-text-faint)]">
              process_id <span className="text-[var(--color-text-muted)]">{row.id}</span>
            </div>
          </td>
        </tr>
      )}
    </>
  );
}

function DetailList({
  title, items, empty, ordered = true,
}: {
  title: string;
  items: unknown[];
  empty: string;
  ordered?: boolean;
}) {
  return (
    <div>
      <div className="text-[10px] font-mono uppercase tracking-wider text-[var(--color-text-faint)] mb-2">
        {title} <span className="text-[var(--color-text-muted)]">· {items.length}</span>
      </div>
      {items.length === 0 ? (
        <div className="text-xs text-[var(--color-text-faint)] italic">{empty}</div>
      ) : (
        <ol className="space-y-1.5 list-none m-0 p-0">
          {items.slice(0, 10).map((item, i) => {
            const { primary, secondary } = extractLabel(item);
            return (
              <li key={i} className="flex items-start gap-2 text-xs">
                {ordered && (
                  <span className="font-mono text-[10px] text-[var(--color-text-faint)] tabular-nums shrink-0 w-5 mt-0.5">
                    {i + 1}.
                  </span>
                )}
                {!ordered && (
                  <span
                    aria-hidden="true"
                    className="inline-block w-1.5 h-1.5 rounded-full bg-[var(--color-warning)] mt-1.5 shrink-0"
                  />
                )}
                <div className="min-w-0 flex-1">
                  <div className="text-[var(--color-text)]">{primary}</div>
                  {secondary && (
                    <div className="text-[var(--color-text-faint)] mt-0.5">{secondary}</div>
                  )}
                </div>
              </li>
            );
          })}
          {items.length > 10 && (
            <li className="text-[11px] text-[var(--color-text-faint)] italic">
              + {items.length - 10} more
            </li>
          )}
        </ol>
      )}
    </div>
  );
}

function tierTone(tier: ProcessTier): BadgeTone {
  return tier === "revenue" ? "accent" : "neutral";
}
function healthTone(h: ProcessRow["health"]): BadgeTone {
  switch (h) {
    case "healthy":          return "success";
    case "warn":             return "warning";
    case "danger":           return "danger";
    case "not-instrumented": return "neutral";
  }
}
function healthLabel(h: ProcessRow["health"]): string {
  return h === "not-instrumented" ? "not instrumented" : h;
}
