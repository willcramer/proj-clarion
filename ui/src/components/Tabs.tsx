import type { ReactNode } from "react";
import { cn } from "@/lib/cn";

/**
 * Minimal tabs primitive. Controlled — parent owns active state. We
 * keep this simple instead of pulling shadcn/Radix because we have
 * exactly one place that uses it.
 */
export function Tabs({
  tabs, active, onChange, className,
}: {
  tabs: { id: string; label: ReactNode; hint?: ReactNode }[];
  active: string;
  onChange: (id: string) => void;
  className?: string;
}) {
  return (
    <div className={cn("flex items-end gap-1 border-b border-[var(--color-border)]", className)}>
      {tabs.map((t) => (
        <button
          key={t.id}
          onClick={() => onChange(t.id)}
          className={cn(
            "px-4 h-10 text-sm font-medium transition-colors relative -mb-px border-b-2",
            active === t.id
              ? "text-[var(--color-text)] border-[var(--color-accent)]"
              : "text-[var(--color-text-muted)] hover:text-[var(--color-text)] border-transparent",
          )}
        >
          {t.label}
          {t.hint && (
            <span className="ml-2 text-[10px] text-[var(--color-text-faint)]">{t.hint}</span>
          )}
        </button>
      ))}
    </div>
  );
}
