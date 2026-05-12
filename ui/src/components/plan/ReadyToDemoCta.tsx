/**
 * ReadyToDemoCta — section 7 / footer CTA.
 *
 * Full-width accent-tinted card that duplicates the top-right "Start
 * demo" affordance. Keeps the primary action reachable after a long
 * scroll without forcing the user to scroll back up. Disabled (with
 * informative copy) when the plan isn't approved yet.
 */
import { FileDown, Play, Rocket } from "lucide-react";

import { Button } from "@/components/Button";
import { cn } from "@/lib/cn";

export function ReadyToDemoCta({
  ready, onStartDemo, onExportPlan,
}: {
  /** False until the plan reaches `approved_for_provision` or
   *  `provisioned`. When false the Start button stays disabled with
   *  a tooltip; we still render the card so users see the destination. */
  ready: boolean;
  onStartDemo: () => void;
  onExportPlan: () => void;
}) {
  return (
    <div
      className={cn(
        "rounded-[14px] border p-5 flex items-center gap-4 flex-wrap",
        "border-[color:var(--color-accent-border)] bg-[var(--color-accent-bg)]",
      )}
    >
      <span
        aria-hidden="true"
        className="inline-flex items-center justify-center w-10 h-10 rounded-md bg-[var(--color-accent)] text-[var(--color-on-accent)] shrink-0"
      >
        <Rocket size={18} />
      </span>
      <div className="min-w-0 flex-1">
        <div className="text-base font-medium text-[var(--color-text)]">
          {ready ? "Ready to demo" : "Approve to demo"}
        </div>
        <div className="text-xs text-[var(--color-text-muted)] mt-0.5">
          {ready
            ? "Spin up the entity emitter and run the incident script live in your stack."
            : "Once the plan is approved for provision the demo controls light up here."}
        </div>
      </div>
      <div className="flex items-center gap-2 flex-wrap">
        <Button
          variant="secondary"
          size="md"
          onClick={onExportPlan}
          title="Download the plan_json as a file."
        >
          <FileDown size={14} /> Export plan
        </Button>
        <Button
          variant="primary"
          size="lg"
          onClick={onStartDemo}
          disabled={!ready}
          title={ready
            ? "Open the live demo session controls."
            : "Plan must be approved before starting a demo."}
        >
          <Play size={14} /> Start demo
        </Button>
      </div>
    </div>
  );
}
