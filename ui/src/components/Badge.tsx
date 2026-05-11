import type { ReactNode } from "react";
import { cn } from "@/lib/cn";

type Tone = "neutral" | "info" | "success" | "warning" | "danger" | "accent";

const TONES: Record<Tone, string> = {
  neutral: "bg-white/[0.06] text-[var(--color-text-muted)] border-[var(--color-border)]",
  info:    "bg-[var(--color-info)]/12 text-[var(--color-info)] border-[var(--color-info)]/30",
  success: "bg-[var(--color-success)]/12 text-[var(--color-success)] border-[var(--color-success)]/30",
  warning: "bg-[var(--color-warning)]/12 text-[var(--color-warning)] border-[var(--color-warning)]/30",
  danger:  "bg-[var(--color-danger)]/12 text-[var(--color-danger)] border-[var(--color-danger)]/30",
  accent:  "bg-[var(--color-accent-bg)] text-[var(--color-accent)] border-[var(--color-accent-border)]",
};

export function Badge({
  children,
  tone = "neutral",
  className,
}: {
  children: ReactNode;
  tone?: Tone;
  className?: string;
}) {
  return (
    <span
      className={cn(
        "inline-flex items-center gap-1 rounded-md border px-2 py-0.5 text-xs font-medium",
        TONES[tone],
        className,
      )}
    >
      {children}
    </span>
  );
}

/** Map a plan review_state to a tone. Single source of truth for the UI's
 *  visual language around plan lifecycle. */
export function reviewStateTone(state: string): Tone {
  switch (state) {
    case "draft":                  return "neutral";
    case "se_reviewed":            return "info";
    case "approved_for_provision": return "accent";
    case "provisioned":            return "success";
    case "torn_down":              return "warning";
    default:                       return "neutral";
  }
}
