import type { ReactNode } from "react";
import { cn } from "@/lib/cn";

/** Glass card primitive — the canvas-on-canvas surface used everywhere. */
export function Card({
  children,
  className,
  hover,
}: {
  children: ReactNode;
  className?: string;
  hover?: boolean;
}) {
  return (
    <div
      className={cn(
        "glass rounded-xl",
        hover && "transition-all duration-200 hover:border-[var(--color-border-strong)] hover:bg-white/[0.04]",
        className,
      )}
    >
      {children}
    </div>
  );
}
