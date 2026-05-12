/**
 * DashboardsAlertsCard — section 3.
 *
 * Stacked list of 4–6 dashboards + alerts the plan will provision.
 * Each row has a 32px square icon tile (tone tracks severity), a
 * title + mono subtitle, and a right-side Badge.
 *
 * Footer: ghost button "View all N dashboards & M alerts" that
 * navigates to the Grafana stack URL once the plan is provisioned
 * (otherwise stays inert).
 */
import { useState } from "react";
import { LayoutDashboard, Bell, ChevronDown, ChevronRight } from "lucide-react";

import { Badge, type BadgeTone } from "@/components/Badge";
import { Card } from "@/components/Card";
import { cn } from "@/lib/cn";
import type { DashboardItem } from "@/lib/plan-derivations";

export function DashboardsAlertsCard({
  items,
  onViewAll,
  showAllInitially = false,
}: {
  items: DashboardItem[];
  onViewAll?: () => void;
  /** When true, render the full list instead of the 6-row preview.
   *  Used when the card is the primary content on its tab. */
  showAllInitially?: boolean;
}) {
  const dashboards = items.filter((i) => i.kind === "dashboard");
  const alerts = items.filter((i) => i.kind === "alert");
  const [expandedAll, setExpandedAll] = useState(showAllInitially);
  const visible = expandedAll ? items : items.slice(0, 6);

  if (items.length === 0) {
    return (
      <Card className="p-8 text-center text-sm text-[var(--color-text-muted)]">
        No dashboards or alerts captured yet.
      </Card>
    );
  }

  return (
    <Card className="overflow-hidden">
      <div className="px-5 py-4 border-b border-[var(--color-border)] flex items-center justify-between gap-3">
        <h3 className="text-sm font-medium text-[var(--color-text)] m-0">
          Dashboards &amp; alerts
        </h3>
        <span className="font-mono text-[11px] text-[var(--color-text-faint)] tabular-nums">
          {dashboards.length} dashboards · {alerts.length} alerts
        </span>
      </div>
      <ul className="divide-y divide-[var(--color-border)]">
        {visible.map((item) => (
          <DashboardsAlertsRow key={`${item.kind}-${item.id}`} item={item} />
        ))}
      </ul>
      {items.length > visible.length && (
        <div className="border-t border-[var(--color-border)]">
          <button
            type="button"
            onClick={() => {
              if (onViewAll) onViewAll();
              else setExpandedAll(true);
            }}
            className={cn(
              "w-full px-5 py-3 text-xs font-medium flex items-center justify-center gap-1",
              "text-[var(--color-text-muted)] hover:text-[var(--color-text)]",
              "transition-colors",
            )}
          >
            View all {dashboards.length} dashboards &amp; {alerts.length} alerts
            <ChevronRight size={12} />
          </button>
        </div>
      )}
    </Card>
  );
}

function DashboardsAlertsRow({ item }: { item: DashboardItem }) {
  const Icon = item.kind === "dashboard" ? LayoutDashboard : Bell;
  const [open, setOpen] = useState(false);
  return (
    <li>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className={cn(
          "w-full text-left flex items-center gap-3 px-5 py-3",
          "transition-colors hover:bg-white/[0.02]",
          open && "bg-white/[0.02]",
        )}
        aria-expanded={open}
      >
        <span
          aria-hidden="true"
          className={cn(
            "inline-flex items-center justify-center w-8 h-8 rounded-md shrink-0 border",
            tileClass(item.severity),
          )}
        >
          <Icon size={15} />
        </span>
        <div className="flex-1 min-w-0">
          <div className="text-sm font-medium text-[var(--color-text)] truncate">
            {item.title}
          </div>
          <div className="text-[11px] font-mono text-[var(--color-text-faint)] truncate">
            {item.sub}
          </div>
        </div>
        <Badge tone={severityTone(item.severity)}>{severityLabel(item.severity)}</Badge>
        <ChevronDown
          size={14}
          aria-hidden="true"
          className={cn(
            "shrink-0 text-[var(--color-text-faint)] transition-transform",
            open && "rotate-180",
          )}
        />
      </button>
      {open && (
        <div className="px-5 pb-4 pl-[68px] text-xs text-[var(--color-text-muted)] space-y-1">
          <div>
            <span className="text-[var(--color-text-faint)]">type</span>{" "}
            <span className="font-mono">{item.kind}</span>
          </div>
          <div>
            <span className="text-[var(--color-text-faint)]">id</span>{" "}
            <span className="font-mono text-[var(--color-text)]">{item.id}</span>
          </div>
          <div>
            <span className="text-[var(--color-text-faint)]">
              {item.kind === "dashboard" ? "audience" : "severity"}
            </span>{" "}
            <span className="font-mono">{item.sub || "—"}</span>
          </div>
          <div className="text-[11px] text-[var(--color-text-faint)] italic pt-1">
            Cloud link appears here once the plan is provisioned.
          </div>
        </div>
      )}
    </li>
  );
}

function tileClass(s: DashboardItem["severity"]): string {
  switch (s) {
    case "hero":
      return "bg-[var(--color-accent-bg)] text-[var(--color-accent)] border-[color:var(--color-accent-border)]";
    case "slo":
      return "bg-[var(--color-warning-bg)] text-[var(--color-warning)] border-[color:var(--color-warning)]/30";
    case "p1":
      return "bg-[var(--color-danger-bg)] text-[var(--color-danger)] border-[color:var(--color-danger)]/30";
    case "p2":
      return "bg-[var(--color-info-bg)] text-[var(--color-info)] border-[color:var(--color-info)]/30";
    case "info":
      return "bg-[var(--color-canvas-elev2)] text-[var(--color-text-muted)] border-[var(--color-border)]";
  }
}

function severityTone(s: DashboardItem["severity"]): BadgeTone {
  switch (s) {
    case "hero": return "accent";
    case "slo":  return "warning";
    case "p1":   return "danger";
    case "p2":   return "info";
    case "info": return "neutral";
  }
}

function severityLabel(s: DashboardItem["severity"]): string {
  return s.toUpperCase();
}
