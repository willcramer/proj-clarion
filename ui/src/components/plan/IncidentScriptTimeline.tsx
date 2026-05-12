/**
 * IncidentScriptTimeline — section 5.
 *
 * Horizontal rail of N stops (one per `incident_script.events[]`).
 * Each stop is a 28px circular node with a tone-coloured ring, linked
 * by a 2px progress line. The connector behind completed stops uses
 * a gradient accent→warn→danger so a long script reads as escalation.
 *
 * Below 768px the rail collapses to a vertical stack (CSS grid auto
 * column count > 1 only at md+).
 */
import { useState } from "react";

import { Card } from "@/components/Card";
import { cn } from "@/lib/cn";
import type { IncidentStop } from "@/lib/plan-derivations";

export function IncidentScriptTimeline({
  stops, totalMinutes,
}: {
  stops: IncidentStop[];
  totalMinutes: number;
}) {
  // The selected stop index. Defaults to 0 so the panel below always
  // has something to render the moment the user lands on this tab.
  const [selected, setSelected] = useState(0);

  if (stops.length === 0) {
    return (
      <Card className="p-8 text-center text-sm text-[var(--color-text-muted)]">
        No incident script in this plan.
      </Card>
    );
  }

  const active = stops[Math.min(selected, stops.length - 1)];

  return (
    <Card className="p-5 space-y-5">
      <div className="flex items-center justify-between gap-3">
        <h3 className="text-sm font-medium text-[var(--color-text)] m-0">
          Incident script
        </h3>
        <span className="font-mono text-[11px] text-[var(--color-text-faint)] tabular-nums">
          {stops.length} stops · {totalMinutes}m total
        </span>
      </div>

      {/* Horizontal rail on md+, vertical stack below. */}
      <div
        role="tablist"
        aria-label="Incident script stops"
        className={cn(
          "grid gap-4 md:gap-2",
          "md:grid-cols-[repeat(var(--stops),1fr)]",
        )}
        style={{ "--stops": stops.length } as React.CSSProperties}
      >
        {stops.map((stop, i) => (
          <TimelineStop
            key={i}
            stop={stop}
            index={i}
            isFirst={i === 0}
            isLast={i === stops.length - 1}
            isActive={i === selected}
            onSelect={() => setSelected(i)}
          />
        ))}
      </div>

      {/* Detail panel under the rail. Updates instantly when a stop
          is clicked; matches the row-expand pattern in ProcessesTable
          (interactive instead of static mockup). */}
      <div
        role="tabpanel"
        aria-label={`Stop ${selected + 1} of ${stops.length}`}
        className="rounded-md border border-[var(--color-border)] bg-[var(--color-canvas)] p-4"
      >
        <div className="flex items-baseline gap-3 flex-wrap">
          <span className="font-mono text-[11px] uppercase tracking-wider text-[var(--color-text-faint)]">
            Stop {selected + 1} / {stops.length}
          </span>
          <span
            className={cn(
              "inline-flex items-center gap-1.5 h-5 px-2 rounded-full text-[10px] font-mono uppercase tracking-wider border",
              chipClass(active.tone),
            )}
          >
            <span
              aria-hidden="true"
              className={cn("inline-block w-1.5 h-1.5 rounded-full", dotClass(active.tone))}
            />
            {active.tone === "default" ? "step" : active.tone}
          </span>
          <span className="font-mono text-[11px] text-[var(--color-text-muted)] tabular-nums ml-auto">
            {active.t}
          </span>
        </div>
        <div className="mt-2 text-sm font-medium text-[var(--color-text)]">
          {active.title}
        </div>
        <div className="text-xs text-[var(--color-text-muted)] mt-1 font-mono">
          target <span className="text-[var(--color-text)]">{active.sub}</span>
        </div>
      </div>
    </Card>
  );
}

function TimelineStop({
  stop, index, isFirst, isLast, isActive, onSelect,
}: {
  stop: IncidentStop;
  index: number;
  isFirst: boolean;
  isLast: boolean;
  isActive: boolean;
  onSelect: () => void;
}) {
  return (
    <button
      type="button"
      role="tab"
      aria-selected={isActive}
      onClick={onSelect}
      className={cn(
        "relative pt-7 md:pt-9 px-1 text-left rounded-md",
        "transition-colors",
        "hover:bg-[var(--color-canvas-elev2)]/40",
        "focus-visible:bg-[var(--color-canvas-elev2)]/40",
      )}
    >
      {/* Connector line. Hidden on the first stop's left and the last
          stop's right so the rail terminates cleanly. */}
      {!isFirst && (
        <span
          aria-hidden="true"
          className="absolute top-[14px] md:top-[18px] left-0 right-1/2 h-px"
          style={{
            background:
              "linear-gradient(90deg, var(--color-accent), var(--color-warning) 50%, var(--color-danger))",
            opacity: 0.55,
          }}
        />
      )}
      {!isLast && (
        <span
          aria-hidden="true"
          className="absolute top-[14px] md:top-[18px] left-1/2 right-0 h-px"
          style={{
            background:
              "linear-gradient(90deg, var(--color-accent), var(--color-warning) 50%, var(--color-danger))",
            opacity: 0.55,
          }}
        />
      )}
      {/* Node. Active stop gets a filled center + accent halo. */}
      <span
        className={cn(
          "absolute top-0 left-1/2 -translate-x-1/2",
          "w-7 h-7 md:w-9 md:h-9 rounded-full border-2",
          "bg-[var(--color-canvas-elev1)]",
          ringClass(stop.tone),
          "flex items-center justify-center",
          "transition-shadow",
          isActive && "shadow-[0_0_0_4px_var(--color-accent-bg)]",
        )}
        aria-hidden="true"
      >
        {isActive && (
          <span
            className="w-2.5 h-2.5 md:w-3 md:h-3 rounded-full bg-[var(--color-accent)]"
          />
        )}
        {!isActive && (
          <span className="font-mono text-[10px] text-[var(--color-text-faint)] tabular-nums">
            {String(index + 1).padStart(2, "0")}
          </span>
        )}
      </span>
      {/* Label */}
      <div className="text-center md:text-left">
        <div className="font-mono text-[10px] uppercase tracking-wider text-[var(--color-text-faint)]">
          {stop.t}
        </div>
        <div className={cn(
          "text-sm font-medium mt-0.5 truncate",
          isActive ? "text-[var(--color-accent)]" : "text-[var(--color-text)]",
        )}>
          {stop.title}
        </div>
        <div className="text-[11px] font-mono text-[var(--color-text-muted)] truncate">
          {stop.sub}
        </div>
      </div>
    </button>
  );
}

function ringClass(tone: IncidentStop["tone"]): string {
  switch (tone) {
    case "accent":  return "border-[color:var(--color-accent)]";
    case "warn":    return "border-[color:var(--color-warning)]";
    case "live":    return "border-[color:var(--color-live)]";
    case "default": return "border-[color:var(--color-border-strong)]";
  }
}

function chipClass(tone: IncidentStop["tone"]): string {
  switch (tone) {
    case "accent":  return "border-[color:var(--color-accent-border)] bg-[var(--color-accent-bg)] text-[var(--color-accent)]";
    case "warn":    return "border-[color:var(--color-warning)]/40 bg-[var(--color-warning-bg)] text-[var(--color-warning)]";
    case "live":    return "border-[color:var(--color-live)]/40 bg-[var(--color-live-bg)] text-[var(--color-live)]";
    case "default": return "border-[var(--color-border)] bg-[var(--color-canvas-elev2)]/60 text-[var(--color-text-muted)]";
  }
}

function dotClass(tone: IncidentStop["tone"]): string {
  switch (tone) {
    case "accent":  return "bg-[var(--color-accent)]";
    case "warn":    return "bg-[var(--color-warning)]";
    case "live":    return "bg-[var(--color-live)]";
    case "default": return "bg-[var(--color-text-faint)]";
  }
}
