/**
 * PipelineStepper, replaces the old text "3/6 phases" indicator with
 * a horizontal visual stepper that shows, at a glance:
 *
 *   • The numbered ordering (1·Research → 2·Plan → ... → 6·KG publish)
 *   • Per-phase status (pending, running spinner, done check, failed,
 *     skipped). Status drives both the connector dot's colour and the
 *     connector line's tint between this step and the next.
 *   • Per-phase duration on completed steps (or live wall-clock for
 *     the active one), log line count, and error count.
 *   • A vertical-stack fallback for narrow viewports, sm: hidden on
 *     desktop, replaces the horizontal stepper on mobile so the same
 *     info stays visible without horizontal-scroll jank.
 *
 * Click on any step to expose the rerun/resume control via the caller's
 * `onStepClick(phase)` callback. The container exposes an
 * `aria-current="step"` attribute on the active step.
 */
import {
  CheckCircle2, Loader2, Circle, AlertCircle, MinusCircle, X,
  type LucideIcon,
} from "lucide-react";
import { type PhaseState, type PhaseStatus } from "@/lib/PipelineContext";
import { type PipelinePhase, PIPELINE_PHASES } from "@/lib/api";
import { type PhaseMetric } from "@/lib/diagnose";
import { cn } from "@/lib/cn";

const PHASE_LABELS: Record<PipelinePhase, string> = {
  "research":   "Research",
  "plan":       "Plan",
  "approve":    "Approve",
  "generate":   "Generate",
  "provision":  "Provision",
  "kg-publish": "KG publish",
};

const STATUS_TO_ICON: Record<PhaseStatus, LucideIcon> = {
  pending: Circle,
  running: Loader2,
  done:    CheckCircle2,
  failed:  AlertCircle,
  skipped: MinusCircle,
};

const STATUS_TO_TONE: Record<
  PhaseStatus,
  { circle: string; ring: string; line: string; label: string }
> = {
  pending: {
    circle: "bg-[var(--color-canvas-elev2)] text-[var(--color-text-faint)]",
    ring:   "ring-[var(--color-border)]",
    line:   "bg-[var(--color-border)]",
    label:  "text-[var(--color-text-faint)]",
  },
  running: {
    circle: "bg-[var(--color-info-bg)] text-[var(--color-info)]",
    ring:   "ring-[var(--color-info)]",
    line:   "bg-[var(--color-border)]",
    label:  "text-[var(--color-text)]",
  },
  done: {
    circle: "bg-[var(--color-success-bg)] text-[var(--color-success)]",
    ring:   "ring-[var(--color-success)]/40",
    line:   "bg-[var(--color-success)]/40",
    label:  "text-[var(--color-text)]",
  },
  failed: {
    circle: "bg-[var(--color-danger-bg)] text-[var(--color-danger)]",
    ring:   "ring-[var(--color-danger)]/50",
    line:   "bg-[var(--color-border)]",
    label:  "text-[var(--color-danger)]",
  },
  skipped: {
    circle: "bg-[var(--color-canvas-elev2)] text-[var(--color-text-faint)]",
    ring:   "ring-[var(--color-border)]",
    line:   "bg-[var(--color-border)]",
    label:  "text-[var(--color-text-faint)]",
  },
};

export type PipelineStepperProps = {
  /** Phase state map from PipelineContext. */
  phases: Record<PipelinePhase, PhaseState>;
  /** Per-phase computed metrics (duration / errors / log count). */
  metrics?: PhaseMetric[];
  /** Click handler, caller decides whether to scroll to the phase log,
   *  open a rerun modal, or anything else. */
  onStepClick?: (phase: PipelinePhase) => void;
  /** When set, this phase gets the `aria-current="step"` marker. */
  focusedPhase?: PipelinePhase | null;
  /** Optional cancel hook. When provided, a small × button appears on
   *  the currently-running phase. Clicking it asks the caller to stop
   *  the build — phases are sequential so today this is equivalent to
   *  cancelling the whole pipeline. If/when the orchestrator gains true
   *  skip-and-continue semantics we'll branch in the caller. */
  onPhaseCancel?: (phase: PipelinePhase) => void;
};

export function PipelineStepper({
  phases,
  metrics,
  onStepClick,
  focusedPhase,
  onPhaseCancel,
}: PipelineStepperProps) {
  const metricsByPhase = new Map(metrics?.map((m) => [m.phase, m]) ?? []);

  return (
    <div
      role="list"
      aria-label="Pipeline progress"
      // Horizontal stepper on >=md, vertical on <md. Both render the
      // same data; only layout changes, keeps the DOM small enough for
      // server-paint and keeps a single source of truth.
      className="space-y-3"
    >
      {/* Horizontal stepper (desktop) */}
      <ol className="hidden md:flex items-stretch gap-0">
        {PIPELINE_PHASES.map((phase, i) => {
          const last = i === PIPELINE_PHASES.length - 1;
          const ps = phases[phase];
          const m = metricsByPhase.get(phase);
          return (
            <Step
              key={phase}
              phase={phase}
              index={i + 1}
              status={ps.status}
              metric={m}
              focused={focusedPhase === phase}
              onClick={onStepClick}
              onCancel={onPhaseCancel}
              connectorAfter={!last}
            />
          );
        })}
      </ol>

      {/* Vertical stepper (mobile) */}
      <ol className="md:hidden space-y-2">
        {PIPELINE_PHASES.map((phase, i) => {
          const ps = phases[phase];
          const m = metricsByPhase.get(phase);
          return (
            <li key={phase} role="listitem">
              <StepRow
                phase={phase}
                index={i + 1}
                status={ps.status}
                metric={m}
                focused={focusedPhase === phase}
                onClick={onStepClick}
                onCancel={onPhaseCancel}
              />
            </li>
          );
        })}
      </ol>
    </div>
  );
}

// ─── Horizontal step (desktop) ──────────────────────────────────────

function Step({
  phase, index, status, metric, focused, onClick, onCancel, connectorAfter,
}: {
  phase: PipelinePhase;
  index: number;
  status: PhaseStatus;
  metric?: PhaseMetric;
  focused: boolean;
  onClick?: (p: PipelinePhase) => void;
  onCancel?: (p: PipelinePhase) => void;
  connectorAfter: boolean;
}) {
  const Icon = STATUS_TO_ICON[status];
  const tone = STATUS_TO_TONE[status];
  const interactive = !!onClick;
  // Cancel ×: only meaningful for the phase that's actively running.
  // Done/failed/skipped/pending phases hide it. Caller must opt in via
  // `onPhaseCancel` so non-live surfaces (history view, plan detail's
  // small stepper) don't get a destructive control.
  const cancellable = !!onCancel && status === "running";

  // Compose the step button + the trailing connector line as a single
  // <li> so flex stretching distributes them evenly.
  return (
    <li
      role="listitem"
      aria-current={focused ? "step" : undefined}
      className="flex-1 flex items-center min-w-0"
    >
      <div className="relative w-full">
        <button
          type="button"
          disabled={!interactive}
          onClick={() => interactive && onClick?.(phase)}
          className={cn(
            "group flex flex-col items-center gap-1.5 w-full px-2 py-1.5 rounded-lg transition-colors",
            interactive && "hover:bg-white/[0.03] cursor-pointer",
            focused && "bg-white/[0.04]",
          )}
          aria-label={`Phase ${index}: ${PHASE_LABELS[phase]}, ${status}`}
        >
          <span
            className={cn(
              "w-7 h-7 rounded-full flex items-center justify-center ring-2 transition-shadow",
              tone.circle,
              tone.ring,
              focused && "ring-offset-2 ring-offset-[var(--color-canvas)]",
            )}
          >
            <Icon
              size={14}
              aria-hidden="true"
              className={status === "running" ? "animate-spin" : undefined}
            />
          </span>
          <span className={cn("text-[11px] font-medium tracking-wide", tone.label)}>
            <span className="font-mono opacity-60 mr-1">{index}</span>
            {PHASE_LABELS[phase]}
          </span>
          {metric && <StepStats metric={metric} />}
        </button>
        {/* Cancel × overlay, positioned over the upper-right of the
            step circle. Sibling-of-button rather than nested so the
            outer button doesn't swallow the click. Live phase only. */}
        {cancellable && (
          <button
            type="button"
            onClick={(e) => { e.stopPropagation(); onCancel?.(phase); }}
            aria-label={`Cancel ${PHASE_LABELS[phase]} phase (stops the build)`}
            title="Cancel build"
            className={cn(
              "absolute top-0.5 right-[calc(50%-22px)]",
              "w-5 h-5 rounded-full flex items-center justify-center",
              "bg-[var(--color-danger-bg)] text-[var(--color-danger)]",
              "border border-[color:var(--color-danger)]/40",
              "hover:bg-[var(--color-danger)] hover:text-white transition-colors",
              "shadow-sm",
            )}
          >
            <X size={11} strokeWidth={2.5} aria-hidden="true" />
          </button>
        )}
      </div>
      {connectorAfter && (
        <span
          aria-hidden="true"
          className={cn(
            "h-px flex-1 mx-1 transition-colors",
            tone.line,
          )}
        />
      )}
    </li>
  );
}

// ─── Vertical step row (mobile) ─────────────────────────────────────

function StepRow({
  phase, index, status, metric, focused, onClick, onCancel,
}: {
  phase: PipelinePhase;
  index: number;
  status: PhaseStatus;
  metric?: PhaseMetric;
  focused: boolean;
  onClick?: (p: PipelinePhase) => void;
  onCancel?: (p: PipelinePhase) => void;
}) {
  const Icon = STATUS_TO_ICON[status];
  const tone = STATUS_TO_TONE[status];
  const interactive = !!onClick;
  const cancellable = !!onCancel && status === "running";

  return (
    <div className="relative">
      <button
        type="button"
        disabled={!interactive}
        onClick={() => interactive && onClick?.(phase)}
        aria-label={`Phase ${index}: ${PHASE_LABELS[phase]}, ${status}`}
        className={cn(
          "w-full flex items-center gap-3 px-3 py-2 rounded-lg border transition-colors text-left",
          "border-[var(--color-border)] bg-[var(--color-canvas-elev1)]",
          interactive && "hover:border-[var(--color-border-strong)] cursor-pointer",
          focused && "border-[var(--color-accent-border)] bg-[var(--color-accent-bg)]",
          // Reserve right-edge space for the × so the label doesn't crowd it.
          cancellable && "pr-10",
        )}
      >
        <span
          className={cn(
            "w-7 h-7 rounded-full flex items-center justify-center ring-2 shrink-0",
            tone.circle, tone.ring,
          )}
        >
          <Icon
            size={14}
            aria-hidden="true"
            className={status === "running" ? "animate-spin" : undefined}
          />
        </span>
        <div className="min-w-0 flex-1">
          <div className={cn("text-sm font-medium", tone.label)}>
            <span className="font-mono opacity-60 mr-1.5">{index}</span>
            {PHASE_LABELS[phase]}
          </div>
          {metric && (
            <div className="text-[11px] text-[var(--color-text-faint)] mt-0.5">
              <StepStats metric={metric} inline />
            </div>
          )}
        </div>
      </button>
      {cancellable && (
        <button
          type="button"
          onClick={(e) => { e.stopPropagation(); onCancel?.(phase); }}
          aria-label={`Cancel ${PHASE_LABELS[phase]} phase (stops the build)`}
          title="Cancel build"
          className={cn(
            "absolute top-1/2 right-2 -translate-y-1/2",
            "w-6 h-6 rounded-full flex items-center justify-center",
            "bg-[var(--color-danger-bg)] text-[var(--color-danger)]",
            "border border-[color:var(--color-danger)]/40",
            "hover:bg-[var(--color-danger)] hover:text-white transition-colors",
          )}
        >
          <X size={12} strokeWidth={2.5} aria-hidden="true" />
        </button>
      )}
    </div>
  );
}

// ─── Inline mini-stats below the step label ────────────────────────

function StepStats({ metric, inline }: { metric: PhaseMetric; inline?: boolean }) {
  const dur = metric.durationMs;
  const human =
    dur === null
      ? null
      : dur < 1000
      ? `${dur}ms`
      : dur < 60_000
      ? `${(dur / 1000).toFixed(1)}s`
      : `${Math.floor(dur / 60_000)}m ${Math.round((dur % 60_000) / 1000)}s`;

  // Inline (mobile row) renders as a single horizontal line.
  // Stacked (desktop column) puts duration first, error count next.
  return (
    <div
      className={cn(
        "flex items-center gap-2 text-[10px] tabular-nums text-[var(--color-text-faint)]",
        !inline && "justify-center",
      )}
    >
      {human && <span>{human}</span>}
      {metric.errorCount > 0 && (
        <span className="px-1 rounded bg-[var(--color-danger-bg)] text-[var(--color-danger)] font-medium">
          {metric.errorCount} err
        </span>
      )}
      {metric.logLineCount > 0 && (
        <span className="opacity-60">{metric.logLineCount} log</span>
      )}
    </div>
  );
}
