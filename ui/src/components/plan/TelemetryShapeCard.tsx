/**
 * TelemetryShapeCard — section 4.
 *
 * Diurnal area chart + 3 KPI callouts (Peak QPS / Trough / Shape).
 *
 * The area sparkline is inlined here so the gradient ID stays scoped
 * to this component and theme tokens drive the fill. The series comes
 * from `deriveTelemetryShape(plan)` and is normalised to 0..1, so the
 * SVG just maps each point onto the viewBox height directly.
 */
import { useId } from "react";

import { Card } from "@/components/Card";
import type { TelemetryShape } from "@/lib/plan-derivations";

export function TelemetryShapeCard({ shape }: { shape: TelemetryShape }) {
  const gradId = useId();
  const W = 600;
  const H = 120;
  const series = shape.series;
  const step = series.length > 1 ? W / (series.length - 1) : 0;
  const pathTop = series
    .map((v, i) => `${i === 0 ? "M" : "L"}${(i * step).toFixed(2)},${(H - v * H).toFixed(2)}`)
    .join(" ");
  // Close to baseline for the filled area.
  const area = `${pathTop} L${W.toFixed(2)},${H} L0,${H} Z`;

  return (
    <Card className="p-5">
      <div className="flex items-center justify-between gap-3 mb-4">
        <h3 className="text-sm font-medium text-[var(--color-text)] m-0">
          Telemetry shape
        </h3>
        <span className="font-mono text-[11px] text-[var(--color-text-faint)] tabular-nums">
          24h projected
        </span>
      </div>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        preserveAspectRatio="none"
        className="w-full h-[120px] block"
        aria-hidden="true"
      >
        <defs>
          <linearGradient id={gradId} x1="0" x2="0" y1="0" y2="1">
            <stop offset="0%"   stopColor="var(--color-accent)" stopOpacity="0.35" />
            <stop offset="100%" stopColor="var(--color-accent)" stopOpacity="0" />
          </linearGradient>
        </defs>
        <path d={area} fill={`url(#${gradId})`} />
        <path
          d={pathTop}
          fill="none"
          stroke="var(--color-accent)"
          strokeWidth="1.5"
          strokeLinejoin="round"
          strokeLinecap="round"
        />
      </svg>
      {/* Hour ticks every 6 hours for context. Cheap to render here
          instead of inside the SVG so they pick up theme colors. */}
      <div className="flex justify-between font-mono text-[10px] text-[var(--color-text-faint)] mt-1">
        {["00", "06", "12", "18", "24"].map((h) => (
          <span key={h}>{h}h</span>
        ))}
      </div>

      <div className="grid grid-cols-3 gap-3 mt-4">
        <Callout
          label="Peak QPS"
          value={shape.peakQps !== null ? shape.peakQps.toLocaleString() : "—"}
          hint={shape.peakQps !== null ? "events/sec at peak" : "not measured yet"}
        />
        <Callout
          label="Trough"
          value={shape.troughQps !== null ? shape.troughQps.toLocaleString() : "—"}
          hint={shape.troughQps !== null ? "events/sec at trough" : "not measured yet"}
        />
        <Callout label="Shape" value={shape.shape} hint="planner projection" />
      </div>
    </Card>
  );
}

function Callout({
  label, value, hint,
}: { label: string; value: string; hint: string }) {
  return (
    <div className="rounded-md border border-[var(--color-border)] bg-[var(--color-canvas)] px-3 py-2.5">
      <div className="text-[10px] font-mono uppercase tracking-wider text-[var(--color-text-faint)]">
        {label}
      </div>
      <div className="mt-0.5 text-sm font-medium text-[var(--color-text)] truncate">
        {value}
      </div>
      <div className="text-[11px] text-[var(--color-text-muted)] truncate">{hint}</div>
    </div>
  );
}
