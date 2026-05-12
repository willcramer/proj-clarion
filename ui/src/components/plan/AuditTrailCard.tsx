/**
 * AuditTrailCard — section 6a.
 *
 * Feed of {who, what, when, icon, tone} entries from the existing
 * plan_audit_log. Uses the GlobalAuditEntry-shaped rows the page
 * already fetches via getPlanAudit().
 */
import { Check, Cloud, Pencil, Rocket, Stethoscope, AlertCircle } from "lucide-react";

import { Card } from "@/components/Card";
import { cn } from "@/lib/cn";

export interface AuditFeedEntry {
  timestamp: string;
  actor: string;
  action: string;
  from_state: string | null;
  to_state: string | null;
  note: string | null;
}

export function AuditTrailCard({
  entries,
}: {
  entries: AuditFeedEntry[];
}) {
  const visible = entries.slice(-12).reverse();

  if (entries.length === 0) {
    return (
      <Card className="p-8 text-center text-sm text-[var(--color-text-muted)]">
        No audit entries yet.
      </Card>
    );
  }

  return (
    <Card className="p-5">
      <div className="flex items-center justify-between gap-3 mb-3">
        <h3 className="text-sm font-medium text-[var(--color-text)] m-0">
          Audit trail
        </h3>
        <span className="font-mono text-[11px] text-[var(--color-text-faint)] tabular-nums">
          {entries.length} total
        </span>
      </div>
      <ul className="space-y-3">
        {visible.map((e, i) => (
          <AuditRow key={`${e.timestamp}-${i}`} entry={e} />
        ))}
      </ul>
    </Card>
  );
}

function AuditRow({ entry }: { entry: AuditFeedEntry }) {
  const { Icon, tone } = iconFor(entry.action);
  return (
    <li className="flex items-start gap-3">
      <span
        aria-hidden="true"
        className={cn(
          "inline-flex items-center justify-center w-[26px] h-[26px] rounded-full shrink-0 border",
          toneClass(tone),
        )}
      >
        <Icon size={12} />
      </span>
      <div className="flex-1 min-w-0">
        <div className="text-sm text-[var(--color-text)]">
          <span className="text-[var(--color-text-muted)]">{entry.actor}</span>
          <span className="text-[var(--color-text-faint)]"> · </span>
          <span className="font-mono text-[12px]">{entry.action}</span>
          {entry.from_state && entry.to_state && (
            <span className="ml-1 font-mono text-[11px] text-[var(--color-text-faint)]">
              ({entry.from_state} → <span className="text-[var(--color-accent)]">{entry.to_state}</span>)
            </span>
          )}
        </div>
        {entry.note && (
          <div className="text-[12px] text-[var(--color-text-muted)] truncate">
            {entry.note}
          </div>
        )}
        <div className="text-[11px] font-mono text-[var(--color-text-faint)] tabular-nums mt-0.5">
          {formatRelative(entry.timestamp)}
        </div>
      </div>
    </li>
  );
}

type Tone = "accent" | "live" | "warn" | "default";

function iconFor(action: string): { Icon: typeof Check; tone: Tone } {
  const a = action.toLowerCase();
  if (a.includes("approve"))      return { Icon: Check,       tone: "live"    };
  if (a.includes("provision"))    return { Icon: Cloud,       tone: "accent"  };
  if (a.includes("kg"))           return { Icon: Cloud,       tone: "accent"  };
  if (a.includes("edit"))         return { Icon: Pencil,      tone: "default" };
  if (a.includes("demo"))         return { Icon: Rocket,      tone: "accent"  };
  if (a.includes("health"))       return { Icon: Stethoscope, tone: "default" };
  if (a.includes("fail") || a.includes("error")) return { Icon: AlertCircle, tone: "warn" };
  return { Icon: Pencil, tone: "default" };
}

function toneClass(t: Tone): string {
  switch (t) {
    case "accent":  return "bg-[var(--color-accent-bg)] text-[var(--color-accent)] border-[color:var(--color-accent-border)]";
    case "live":    return "bg-[var(--color-live-bg)] text-[var(--color-live)] border-[color:var(--color-live)]/30";
    case "warn":    return "bg-[var(--color-warning-bg)] text-[var(--color-warning)] border-[color:var(--color-warning)]/30";
    case "default": return "bg-[var(--color-canvas-elev2)] text-[var(--color-text-muted)] border-[var(--color-border)]";
  }
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
