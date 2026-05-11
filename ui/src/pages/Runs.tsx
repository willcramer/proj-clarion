import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router-dom";
import { useEffect, useState } from "react";
import { Activity, Square, AlertCircle, CheckCircle2, Loader2 } from "lucide-react";

import { listRuns, streamRun, cancelRun, type RunSummary } from "@/lib/api";
import { Card } from "@/components/Card";
import { Badge } from "@/components/Badge";
import { Button } from "@/components/Button";
import { LogView } from "@/components/LogView";
import { cn } from "@/lib/cn";

export function RunsPage() {
  const runs = useQuery({ queryKey: ["runs"], queryFn: listRuns, refetchInterval: 3_000 });
  const [params, setParams] = useSearchParams();
  const selected = params.get("run");

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Runs</h1>
        <p className="text-[var(--color-text-muted)] mt-1 text-sm max-w-3xl">
          A <strong>run</strong> is a single CLI subprocess invoked from this UI:
          one of <code className="font-mono">generate</code>, <code className="font-mono">provision</code>,
          {" "}<code className="font-mono">kg-publish</code>, or <code className="font-mono">live-tail</code>.
          Each one shells out to <code className="font-mono">proj-clarion</code> just like
          you'd run from a terminal — same arguments, same logs. This page is where you
          watch what's happening, cancel a long-running one, or check the exit status.
        </p>
        <p className="text-[var(--color-text-faint)] text-xs mt-2 max-w-3xl">
          Compare to <strong>pipelines</strong> (the Build page): a pipeline chains
          multiple phases together (research → plan → generate → provision → kg-publish)
          and is tracked as one entity. Runs are the lower-level primitive.
          When you click "Generate events" on a Plan detail page, that creates a run.
          The Build page creates a pipeline that internally creates several runs.
        </p>
      </div>
      <div className="grid grid-cols-1 lg:grid-cols-[380px_1fr] gap-6">
        <RunsList
          runs={runs.data ?? []}
          selected={selected}
          onSelect={(id) => setParams(id ? { run: id } : {})}
        />
        {selected ? (
          <RunDetail
            runId={selected}
            run={(runs.data ?? []).find((r) => r.run_id === selected)}
          />
        ) : (
          <Card className="p-12 text-center text-[var(--color-text-faint)] flex items-center justify-center">
            <div>
              <Activity className="mx-auto opacity-40 mb-3" size={28} />
              <div className="text-sm">Select a run to view its log.</div>
              <div className="text-xs mt-1 max-w-sm mx-auto">
                Start a new run from a plan's actions panel, or via ⌘K → "generate".
              </div>
            </div>
          </Card>
        )}
      </div>
    </div>
  );
}

function RunsList({
  runs, selected, onSelect,
}: {
  runs: RunSummary[]; selected: string | null; onSelect: (id: string | null) => void;
}) {
  return (
    <Card className="overflow-hidden">
      {runs.length === 0 ? (
        <div className="p-8 text-center text-[var(--color-text-muted)] text-sm">No runs in this session.</div>
      ) : (
        <ul className="divide-y divide-[var(--color-border)]">
          {runs.map((r) => (
            <li
              key={r.run_id}
              onClick={() => onSelect(r.run_id)}
              className={cn(
                "px-4 py-3 cursor-pointer transition-colors",
                selected === r.run_id ? "bg-white/[0.05]" : "hover:bg-white/[0.02]",
              )}
            >
              <div className="flex items-center justify-between gap-2 mb-1">
                <span className="font-medium text-sm">{r.kind}</span>
                <RunStatus run={r} />
              </div>
              <div className="text-xs text-[var(--color-text-muted)] font-mono">{r.plan_id.slice(0, 8)}</div>
              <div className="text-xs text-[var(--color-text-faint)] mt-0.5">
                {new Date(r.started_at).toLocaleString()} · {r.line_count} lines
              </div>
            </li>
          ))}
        </ul>
      )}
    </Card>
  );
}

function RunStatus({ run }: { run: RunSummary }) {
  if (!run.finished) return <Badge tone="info"><Loader2 size={10} className="animate-spin" /> running</Badge>;
  if (run.return_code === 0) return <Badge tone="success"><CheckCircle2 size={10} /> done</Badge>;
  return <Badge tone="danger"><AlertCircle size={10} /> exit {run.return_code}</Badge>;
}

function RunDetail({ runId, run }: { runId: string; run?: RunSummary }) {
  const [lines, setLines] = useState<string[]>([]);
  const [exitCode, setExitCode] = useState<number | null>(null);

  useEffect(() => {
    setLines([]);
    setExitCode(null);
    const stop = streamRun(runId, (e) => {
      if (e.type === "log") setLines((prev) => [...prev, e.line]);
      else setExitCode(e.code);
    });
    return stop;
  }, [runId]);

  // Auto-scroll handled by LogView's tail-aware scroll behaviour.

  const finished = exitCode !== null || run?.finished === true;

  return (
    <Card className="flex flex-col h-[700px]">
      <div className="px-5 py-3 border-b border-[var(--color-border)] flex items-center justify-between gap-3">
        <div>
          <div className="font-medium text-sm">{run?.kind ?? "run"} · <span className="font-mono text-xs">{run?.plan_id.slice(0, 8)}</span></div>
          <div className="text-xs text-[var(--color-text-faint)]">{runId}</div>
        </div>
        <div className="flex items-center gap-2">
          {run && <RunStatus run={run} />}
          {!finished && (
            <Button size="sm" variant="danger" onClick={() => void cancelRun(runId)}>
              <Square size={12} /> Cancel
            </Button>
          )}
        </div>
      </div>
      <LogView
        lines={lines}
        emptyText="Waiting for output…"
        maxHeight="100%"
        className="flex-1"
      />
      {finished && exitCode !== null && (
        <div className={cn(
          "px-5 py-2 text-xs border-t border-[var(--color-border)]",
          exitCode === 0 ? "text-[var(--color-success)]" : "text-[var(--color-danger)]",
        )}>
          exit {exitCode}
        </div>
      )}
    </Card>
  );
}
