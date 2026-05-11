import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate, useParams, Link } from "react-router-dom";
import { useState } from "react";
import {
  ArrowLeft, Check, AlertCircle, Play, Trash2, MessageCircle, Code2,
  Stethoscope, CheckCircle2, XCircle, AlertTriangle, MinusCircle, Loader2,
  Sparkles, Activity, ChevronDown, ChevronRight,
} from "lucide-react";

import {
  listPlans, getPlan, getPlanAudit, approvePlan,
  startRun, deletePlan, replacePlanJson, getPlanHealth, listPipelines,
  type RunKind, type HealthReport,
} from "@/lib/api";
import { usePipeline } from "@/lib/PipelineContext";
import { Card } from "@/components/Card";
import { Badge, reviewStateTone } from "@/components/Badge";
import { Button } from "@/components/Button";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { Tabs } from "@/components/Tabs";
import { JsonEditor } from "@/components/JsonEditor";
import { DemoSessionCard } from "@/components/DemoSessionCard";
import { AgentChatPanel } from "@/pages/Profiles";
import { cn } from "@/lib/cn";

// ─── List ──────────────────────────────────────────────────────────

export function PlansListPage() {
  // Refetch every 5s so in-flight pipeline placeholders ("Planning...")
  // stay alive and disappear when the planner agent lands a plan in DB.
  const plans = useQuery({
    queryKey: ["plans"],
    queryFn: () => listPlans(),
    refetchInterval: 5_000,
  });
  const navigate = useNavigate();

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-semibold tracking-tight">Plans</h1>
        <p className="text-[var(--color-text-muted)] mt-1 text-sm">
          DemoPlans for SE review. Click in to inspect, approve, or refine.
        </p>
      </div>
      <Card>
        {plans.isLoading ? (
          <div className="p-8 text-center text-[var(--color-text-faint)]">Loading…</div>
        ) : (plans.data ?? []).length === 0 ? (
          <div className="p-8 text-center text-[var(--color-text-muted)]">No plans yet.</div>
        ) : (
          <table className="w-full text-sm">
            <thead className="text-xs text-[var(--color-text-faint)] uppercase tracking-wider border-b border-[var(--color-border)]">
              <tr>
                <th className="text-left font-medium px-4 py-3">Plan</th>
                <th className="text-left font-medium px-4 py-3">Profile</th>
                <th className="text-left font-medium px-4 py-3">State</th>
                <th className="text-right font-medium px-4 py-3">Proc</th>
                <th className="text-right font-medium px-4 py-3">KG nodes</th>
                <th className="text-right font-medium px-4 py-3">Alerts</th>
                <th className="text-right font-medium px-4 py-3">Dashboards</th>
                <th className="text-right font-medium px-4 py-3">Updated</th>
              </tr>
            </thead>
            <tbody>
              {(plans.data ?? []).map((p) => {
                const onRowClick = p.pending && p.pipeline_id
                  ? () => navigate(`/pipelines?p=${p.pipeline_id}`)
                  : () => navigate(`/plans/${p.plan_id}`);
                return (
                  <tr
                    key={p.plan_id}
                    onClick={onRowClick}
                    className={cn(
                      "border-b border-[var(--color-border)] last:border-0 cursor-pointer transition-colors",
                      p.pending
                        ? "bg-[var(--color-info)]/5 hover:bg-[var(--color-info)]/10"
                        : "hover:bg-white/[0.02]",
                    )}
                  >
                    <td className="px-4 py-3 font-mono text-xs">
                      {p.pending ? (
                        <span className="inline-flex items-center gap-1.5 text-[var(--color-info)]">
                          <Loader2 size={11} className="animate-spin" />
                          planning…
                        </span>
                      ) : (
                        p.plan_id_short
                      )}
                    </td>
                    <td className="px-4 py-3 font-mono text-xs">{p.source_profile_id}</td>
                    <td className="px-4 py-3">
                      <Badge tone={p.pending ? "info" : reviewStateTone(p.review_state)}>
                        {p.review_state}
                      </Badge>
                    </td>
                    <td className="px-4 py-3 text-right tabular-nums">
                      {p.pending ? <span className="text-[var(--color-text-faint)]">—</span> : p.process_count}
                    </td>
                    <td className="px-4 py-3 text-right tabular-nums">
                      {p.pending ? <span className="text-[var(--color-text-faint)]">—</span> : p.kg_node_count}
                    </td>
                    <td className="px-4 py-3 text-right tabular-nums">
                      {p.pending ? <span className="text-[var(--color-text-faint)]">—</span> : p.alert_count}
                    </td>
                    <td className="px-4 py-3 text-right tabular-nums">
                      {p.pending ? <span className="text-[var(--color-text-faint)]">—</span> : p.dashboard_count}
                    </td>
                    <td className="px-4 py-3 text-right text-xs text-[var(--color-text-muted)]">
                      {new Date(p.updated_at).toLocaleString()}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </Card>
    </div>
  );
}

// ─── Detail ────────────────────────────────────────────────────────

interface KGNode {
  node_id: string;
  node_type: "business_entity" | "technical_resource" | "agentic_resource";
  business_subtype?: string | null;
  technical_subtype?: string | null;
  agentic_subtype?: string | null;
  label: string;
}

interface PlanDoc {
  plan_id: string;
  source_profile_id: string;
  review_state: string;
  narrative: string;
  business_process_models: Array<{ process_id: string; name: string; business_steps: unknown[]; failure_modes: unknown[] }>;
  knowledge_graph: { nodes: KGNode[]; edges: unknown[] };
  incident_script: { title: string; total_duration_minutes: number; arming_mode: string; events: Array<{ event_type: string; target_id: string; offset_seconds: number }> };
  dashboard_specs: Array<{ dashboard_id: string; title: string; audience: string }>;
  alert_specs: Array<{ alert_id: string; severity: string; business_subject_line: string }>;
  assistant_tools: Array<{ tool_name: string; description: string }>;
}

export function PlanDetailPage() {
  const { planId = "" } = useParams<{ planId: string }>();
  const plan = useQuery({
    queryKey: ["plan", planId],
    queryFn: () => getPlan(planId) as Promise<PlanDoc>,
    enabled: !!planId,
  });
  const audit = useQuery({
    queryKey: ["plan-audit", planId],
    queryFn: () => getPlanAudit(planId),
    enabled: !!planId,
  });

  return (
    <div className="space-y-6">
      <Link to="/plans" className="text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text)] inline-flex items-center gap-1">
        <ArrowLeft size={14} /> Plans
      </Link>

      {plan.isLoading ? (
        <div className="text-[var(--color-text-faint)]">Loading…</div>
      ) : !plan.data ? (
        <div className="text-[var(--color-danger)]">Plan not found.</div>
      ) : (
        <div className="grid grid-cols-1 xl:grid-cols-2 gap-6">
          <div className="space-y-6">
            <PlanHeader plan={plan.data} />
            {/* Demo session controls — only meaningful once the plan is
                approved (= the KG rules were pushed to Cloud). Hiding for
                draft/in-review plans avoids confusing users who haven't
                done the initial kg-publish yet (Cloud entities don't
                materialize from a single emitter alone — the rule push
                in `kg-publish` is what defines them). */}
            {plan.data.review_state === "approved_for_provision" && (
              <DemoSessionCard planId={plan.data.plan_id} />
            )}
            <EntityTypesPanel plan={plan.data} />
            <PlanTree plan={plan.data} />
            <PlanActions planId={plan.data.plan_id} state={plan.data.review_state} />
            <HealthPanel planId={plan.data.plan_id} />
            <AuditPanel rows={audit.data ?? []} />
          </div>
          <RefineColumn planId={plan.data.plan_id} planJson={plan.data} />
        </div>
      )}
    </div>
  );
}

/** Right column on Plan detail: tabs between agent chat (narrative
 *  refinement) and a JSON editor (direct schema-validated edits).
 *
 *  Before this component existed, the right column was a single
 *  AgentChatPanel — useful for "what should I change" but read-only.
 *  Now SEs can either talk to the agent OR open the editor and apply
 *  changes themselves. */
function RefineColumn({
  planId, planJson,
}: { planId: string; planJson: unknown }) {
  const qc = useQueryClient();
  const [tab, setTab] = useState<"chat" | "json">("chat");
  const [saveErr, setSaveErr] = useState<string | null>(null);

  const saveMut = useMutation({
    mutationFn: (parsed: unknown) => replacePlanJson(planId, parsed),
    onSuccess: () => {
      setSaveErr(null);
      // Re-fetch the plan + audit so the tree view reflects the edit
      qc.invalidateQueries({ queryKey: ["plan", planId] });
      qc.invalidateQueries({ queryKey: ["plans"] });
      qc.invalidateQueries({ queryKey: ["plan-audit", planId] });
    },
    onError: (e: Error) => setSaveErr(e.message),
  });

  return (
    <div className="space-y-3">
      <Tabs
        active={tab}
        onChange={(t) => setTab(t as "chat" | "json")}
        tabs={[
          { id: "chat", label: <span className="inline-flex items-center gap-1.5"><MessageCircle size={12} /> Refine via chat</span>, hint: "ask the planner" },
          { id: "json", label: <span className="inline-flex items-center gap-1.5"><Code2 size={12} /> Edit JSON</span>, hint: "schema-validated save" },
        ]}
      />
      {tab === "chat" ? (
        <AgentChatPanel
          contextId={planId}
          endpoint="plan/refine"
          title="Refine plan"
          subtitle="Ask the planner to reconsider a process, alert, or incident. Suggestions are narrative — switch to the JSON tab to apply changes."
        />
      ) : (
        <JsonEditor
          value={planJson}
          onSave={async (parsed) => { await saveMut.mutateAsync(parsed); }}
          busy={saveMut.isPending}
          error={saveErr}
        />
      )}
    </div>
  );
}

function PlanHeader({ plan }: { plan: PlanDoc }) {
  return (
    <Card className="p-5">
      <div className="flex items-start justify-between gap-4">
        <div className="flex-1">
          <div className="flex items-center gap-2 mb-2">
            <Badge tone={reviewStateTone(plan.review_state)}>{plan.review_state}</Badge>
            <span className="font-mono text-xs text-[var(--color-text-muted)]">{plan.plan_id.slice(0, 8)}</span>
          </div>
          <div className="text-sm text-[var(--color-text-muted)] mb-1">{plan.source_profile_id}</div>
          <p className="text-sm leading-relaxed">{plan.narrative}</p>
        </div>
      </div>
    </Card>
  );
}

/** At-a-glance breakdown of every entity type the plan models, grouped
 *  by tier (business / technical / agentic). Most useful right after a
 *  plan lands so the SE can verify the planner picked vertical-fit
 *  subtypes (no "store" on BlueSky Airlines etc.) without diving into
 *  the raw JSON. */
function EntityTypesPanel({ plan }: { plan: PlanDoc }) {
  const nodes = plan.knowledge_graph.nodes ?? [];
  if (nodes.length === 0) return null;

  // Group node labels by (tier, subtype). Tier comes from node_type.
  // Examples: subtype=region, store, channel, business_unit, ... (business)
  //           service, cluster, namespace, database, queue, ... (technical)
  const groups: Record<string, Record<string, string[]>> = {
    "Business entities": {},
    "Technical resources": {},
    "Agentic resources": {},
  };
  const TIER_LABEL: Record<string, string> = {
    business_entity: "Business entities",
    technical_resource: "Technical resources",
    agentic_resource: "Agentic resources",
  };
  for (const n of nodes) {
    const tier = TIER_LABEL[n.node_type] ?? "Other";
    const subtype = n.business_subtype ?? n.technical_subtype ?? n.agentic_subtype ?? "(untyped)";
    if (!groups[tier]) groups[tier] = {};
    if (!groups[tier][subtype]) groups[tier][subtype] = [];
    groups[tier][subtype].push(n.label);
  }

  // Hide tiers that have no entries.
  const tiers = (Object.keys(groups) as string[]).filter(t => Object.keys(groups[t]).length > 0);

  return (
    <Card className="p-5">
      <div className="flex items-center justify-between mb-3">
        <h2 className="text-sm font-medium text-[var(--color-text-muted)] uppercase tracking-wider">
          Entity types
        </h2>
        <div className="text-xs text-[var(--color-text-faint)] font-mono">
          {nodes.length} total nodes
        </div>
      </div>
      <div className="space-y-4">
        {tiers.map(tier => {
          const subtypes = groups[tier];
          const subtypeKeys = Object.keys(subtypes).sort();
          return (
            <div key={tier}>
              <div className="text-xs text-[var(--color-text-faint)] uppercase tracking-wider mb-2">
                {tier}
              </div>
              <div className="flex flex-wrap gap-1.5">
                {subtypeKeys.map(st => {
                  const labels = subtypes[st];
                  return (
                    <div
                      key={st}
                      className="group relative inline-flex items-center gap-1.5 px-2 py-1 rounded-md border border-[var(--color-border)] bg-white/[0.02] hover:bg-white/[0.05] hover:border-[var(--color-border-strong)] transition-colors text-xs"
                      title={labels.slice(0, 12).join(", ") + (labels.length > 12 ? ` … +${labels.length - 12} more` : "")}
                    >
                      <span className="font-medium">{st}</span>
                      <span className="text-[var(--color-text-faint)] font-mono tabular-nums">
                        {labels.length}
                      </span>
                    </div>
                  );
                })}
              </div>
            </div>
          );
        })}
      </div>
      <p className="text-xs text-[var(--color-text-faint)] mt-3 leading-relaxed">
        Hover any chip to see the names. Subtype values come from the
        plan's knowledge graph; for non-retail verticals you should NOT
        see <code className="font-mono">store</code> or{" "}
        <code className="font-mono">fulfillment_center</code>.
      </p>
    </Card>
  );
}


function PlanTree({ plan }: { plan: PlanDoc }) {
  return (
    <Card className="p-5">
      <h2 className="text-sm font-medium text-[var(--color-text-muted)] uppercase tracking-wider mb-3">
        Composition
      </h2>
      <div className="grid grid-cols-2 gap-3 text-sm">
        <Stat label="Processes" value={plan.business_process_models.length} />
        <Stat label="KG nodes" value={plan.knowledge_graph.nodes.length} />
        <Stat label="KG edges" value={plan.knowledge_graph.edges.length} />
        <Stat label="Dashboards" value={plan.dashboard_specs.length} />
        <Stat label="Alerts" value={plan.alert_specs.length} />
        <Stat label="Assistant tools" value={plan.assistant_tools.length} />
      </div>
      <div className="mt-5 space-y-2">
        <div className="text-xs uppercase tracking-wider text-[var(--color-text-faint)]">Incident script</div>
        <div className="text-sm">{plan.incident_script.title} · {plan.incident_script.total_duration_minutes}m · arming={plan.incident_script.arming_mode}</div>
        <div className="space-y-1 mt-2">
          {plan.incident_script.events.map((e, i) => (
            <div key={i} className="text-xs font-mono text-[var(--color-text-muted)]">
              T+{Math.round(e.offset_seconds / 60)}m  {e.event_type}  → {e.target_id}
            </div>
          ))}
        </div>
      </div>
    </Card>
  );
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div className="flex items-baseline justify-between border-b border-[var(--color-border)] pb-1">
      <span className="text-[var(--color-text-muted)]">{label}</span>
      <span className="tabular-nums font-medium">{value}</span>
    </div>
  );
}

// ─── Actions: approve + run ─────────────────────────────────────────

function PlanActions({ planId, state }: { planId: string; state: string }) {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const pipeline = usePipeline();
  const [note, setNote] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [building, setBuilding] = useState(false);

  // Find pipelines previously associated with this plan so we can offer
  // "View latest build" when one exists. Refetched on a slow cadence —
  // the relevant pipeline IDs only change when this user kicks off a
  // new build, which they'd notice anyway.
  const pipelinesQ = useQuery({
    queryKey: ["pipelines"],
    queryFn: listPipelines,
    refetchInterval: 15_000,
  });
  const planPipelines = (pipelinesQ.data ?? []).filter(
    (p) => p.plan_id === planId,
  );
  const latestPipeline = planPipelines[0]; // newest-first ordering

  const approveMut = useMutation({
    mutationFn: () => approvePlan(planId, note),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["plan", planId] });
      qc.invalidateQueries({ queryKey: ["plans"] });
      qc.invalidateQueries({ queryKey: ["plan-audit", planId] });
      setNote("");
      setError(null);
    },
    onError: (e: Error) => setError(e.message),
  });

  const runMut = useMutation({
    mutationFn: (kind: RunKind) => startRun(kind, planId),
    onSuccess: (run) => navigate(`/runs?run=${run.run_id}`),
    onError: (e: Error) => setError(e.message),
  });

  const deleteMut = useMutation({
    mutationFn: (cleanupCloud: boolean) => deletePlan(planId, cleanupCloud),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["plans"] });
      qc.invalidateQueries({ queryKey: ["dashboard"] });
      navigate("/plans");
    },
    onError: (e: Error) => setError(e.message),
  });

  // Start a new pipeline using THIS plan as the input. Skips research
  // AND plan because the plan is already in DB and approved/ready —
  // start at `approve` so the orchestrator records the audit transition,
  // then runs generate → provision → kg-publish.
  async function newBuildFromPlan() {
    if (typeof pipeline.startFromPhase !== "function") {
      window.alert(
        "Pipeline context isn't ready (likely a stale tab). Hard-refresh and try again.",
      );
      return;
    }
    // Inherit URL/company from the latest pipeline if we have one.
    // Otherwise fall back to a placeholder; the build still runs because
    // research is skipped — the URL is just metadata for the pipelines row.
    const url = latestPipeline?.url
      ?? `plan://${planId.slice(0, 8)}`;
    const company = latestPipeline?.company ?? undefined;
    setBuilding(true);
    try {
      await pipeline.startFromPhase({
        phase: "approve",
        url,
        company,
        plan_id: planId,
        // No parent_pipeline_id — this is a fresh build, not a resume.
      });
      navigate("/new");
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      setError(`Couldn't start build: ${msg}`);
    } finally {
      setBuilding(false);
    }
  }

  async function viewLatestBuild() {
    if (!latestPipeline) return;
    if (typeof pipeline.loadPipeline === "function") {
      await pipeline.loadPipeline(latestPipeline.pipeline_id);
      navigate("/new");
    } else {
      navigate(`/pipelines?p=${latestPipeline.pipeline_id}`);
    }
  }

  // Pick the right primary CTA copy based on plan state + build history.
  // Reasoning:
  //   - draft + no build  → user must approve first; build CTA secondary
  //   - approved/provisioned + has build → "View latest build" is the
  //     natural primary; "New build with this plan" is one click away
  //   - approved + no build → "Build with this plan" is THE call to action
  //   - any state, has build → also surface "New build" so a fresh run
  //     against this plan is one click (e.g. after editing the plan JSON)
  const hasBuild = !!latestPipeline;
  const planReadyForBuild = state !== "draft";
  const newBuildLabel = hasBuild ? "New build with this plan" : "Build with this plan";

  return (
    <Card className="p-5 space-y-4">
      <h2 className="text-sm font-medium text-[var(--color-text-muted)] uppercase tracking-wider">
        Actions
      </h2>

      {state === "draft" && (
        <div className="space-y-2">
          <label className="text-xs text-[var(--color-text-muted)]">Approval note (required)</label>
          <textarea
            value={note}
            onChange={(e) => setNote(e.target.value)}
            placeholder="Why this plan is approved (audit log)…"
            rows={2}
            className="w-full resize-none rounded-md bg-white/[0.02] border border-[var(--color-border)] px-3 py-2 text-sm placeholder:text-[var(--color-text-faint)] focus:border-[var(--color-accent)] focus:outline-none"
          />
          <Button
            variant="primary"
            disabled={!note.trim() || approveMut.isPending}
            onClick={() => approveMut.mutate()}
          >
            <Check size={14} /> Approve for provision
          </Button>
        </div>
      )}

      {/* Primary build CTAs — the modern path. View previous build OR
       *  start a new one from this plan. Granular phase runs are tucked
       *  into the Advanced disclosure below. */}
      <div className="flex flex-wrap gap-2">
        {hasBuild && (
          <Button
            size="sm"
            variant="secondary"
            onClick={() => void viewLatestBuild()}
            title={`Open the latest build for this plan (${latestPipeline.pipeline_id} · ${latestPipeline.status})`}
          >
            <Activity size={12} /> View latest build
            <span className="ml-1 text-[10px] text-[var(--color-text-faint)]">
              {latestPipeline.status}
            </span>
          </Button>
        )}
        <Button
          size="sm"
          variant={hasBuild ? "secondary" : "primary"}
          disabled={!planReadyForBuild || building}
          onClick={() => void newBuildFromPlan()}
          title={
            planReadyForBuild
              ? "Start a new pipeline that skips research AND plan (uses this plan) — runs approve → generate → provision → kg-publish."
              : "Approve the plan first, then this becomes available."
          }
        >
          {building ? <Loader2 size={12} className="animate-spin" /> : <Sparkles size={12} />}
          {newBuildLabel}
        </Button>
      </div>

      {/* Advanced: legacy granular phase runs. Useful for one-off
       *  re-runs of an individual CLI command (debugging, partial
       *  reprovisioning) but not the primary path. */}
      <div className="border-t border-[var(--color-border)] pt-3">
        <button
          type="button"
          onClick={() => setShowAdvanced((v) => !v)}
          className="text-xs text-[var(--color-text-muted)] hover:text-[var(--color-text)] inline-flex items-center gap-1"
        >
          {showAdvanced ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
          Advanced — run individual CLI phases
        </button>
        {showAdvanced && (
          <div className="mt-3 flex flex-wrap gap-2">
            <Button size="sm" variant="ghost" onClick={() => runMut.mutate("generate")} disabled={runMut.isPending}>
              <Play size={12} /> Generate events
            </Button>
            <Button size="sm" variant="ghost" onClick={() => runMut.mutate("provision")} disabled={runMut.isPending}>
              <Play size={12} /> Provision
            </Button>
            <Button size="sm" variant="ghost" onClick={() => runMut.mutate("kg-publish")} disabled={runMut.isPending}>
              <Play size={12} /> KG publish
            </Button>
            <Button size="sm" variant="ghost" onClick={() => runMut.mutate("live-tail")} disabled={runMut.isPending}>
              <Play size={12} /> Live-tail
            </Button>
          </div>
        )}
      </div>

      {error && (
        <div className="text-xs text-[var(--color-danger)] flex items-center gap-1">
          <AlertCircle size={12} /> {error}
        </div>
      )}

      <div className="pt-3 border-t border-[var(--color-border)]">
        <Button variant="danger" size="sm" onClick={() => setConfirmDelete(true)}>
          <Trash2 size={12} /> Delete plan
        </Button>
      </div>

      <ConfirmDialog
        open={confirmDelete}
        title="Delete this plan?"
        body={
          <div className="space-y-2">
            <p>Removes the plan and everything it cascades to in Postgres
              (KG nodes/edges, generated events, audit history). The on-disk
              JSON is removed too.</p>
            <p className="text-xs text-[var(--color-text-faint)]">
              Mimir/Loki/Tempo time-series for this plan stay (~30d retention).
              Cloud KG entity records fade as the emitter stops feeding them.
            </p>
          </div>
        }
        extras={[{
          id: "cleanup_cloud",
          label: "Also remove dashboards + alerts from Grafana Cloud",
          hint: <>Runs <code className="font-mono">provision clear</code> against the plan's folder in your stack.</>,
          defaultChecked: true,
        }]}
        confirmLabel="Yes, delete plan"
        onConfirm={(toggles) => {
          setConfirmDelete(false);
          deleteMut.mutate(!!toggles.cleanup_cloud);
        }}
        onCancel={() => setConfirmDelete(false)}
      />
    </Card>
  );
}

/** Post-emit validation panel. Click to run; reports per-check status
 *  inline with a "fix" hint. The agent isn't trusted by default — this
 *  is the verification step that gates "is the data really good". */
function HealthPanel({ planId }: { planId: string }) {
  const [report, setReport] = useState<HealthReport | null>(null);
  const [error, setError] = useState<string | null>(null);

  const runMut = useMutation({
    mutationFn: () => getPlanHealth(planId),
    onSuccess: (r) => { setReport(r); setError(null); },
    onError: (e: Error) => setError(e.message),
  });

  return (
    <Card className="p-5 space-y-3">
      <div className="flex items-center justify-between">
        <div>
          <h2 className="text-sm font-medium text-[var(--color-text-muted)] uppercase tracking-wider flex items-center gap-2">
            <Stethoscope size={14} /> KG health
          </h2>
          <p className="text-xs text-[var(--color-text-faint)] mt-1">
            Validates this plan's metrics + entities in Cloud against every
            invariant we know about (asserts scope, customer label, Pod node,
            Store fan-out, affinity metrics, entity-count parity).
          </p>
        </div>
        <Button size="sm" onClick={() => runMut.mutate()} disabled={runMut.isPending}>
          {runMut.isPending ? "Running…" : "Run check"}
        </Button>
      </div>

      {error && (
        <div className="text-xs text-[var(--color-danger)]">{error}</div>
      )}

      {report && (
        <div>
          <div className="flex items-center gap-3 text-sm mb-2">
            <span className={cn(
              "font-medium",
              report.passed ? "text-[var(--color-success)]" : "text-[var(--color-danger)]",
            )}>
              {report.passed ? "All critical checks pass" : "Failures detected"}
            </span>
            <span className="text-[var(--color-text-faint)]">{report.summary}</span>
          </div>
          <div className="space-y-1.5">
            {report.checks.map((c, i) => {
              const Icon = (
                c.status === "pass" ? CheckCircle2 :
                c.status === "fail" ? XCircle :
                c.status === "warn" ? AlertTriangle :
                MinusCircle
              );
              const tone = (
                c.status === "pass" ? "text-[var(--color-success)]" :
                c.status === "fail" ? "text-[var(--color-danger)]" :
                c.status === "warn" ? "text-[var(--color-warning)]" :
                "text-[var(--color-text-faint)]"
              );
              return (
                <div key={i} className="flex items-start gap-2 text-xs py-1 border-b border-[var(--color-border)] last:border-0">
                  <Icon size={14} className={cn("shrink-0 mt-0.5", tone)} />
                  <div className="flex-1 min-w-0">
                    <div className={cn("font-medium", tone)}>{c.name}</div>
                    <div className="text-[var(--color-text-muted)] mt-0.5">{c.detail}</div>
                    {c.fix && (
                      <div className="text-[var(--color-text-faint)] mt-0.5 italic">
                        fix: {c.fix}
                      </div>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </Card>
  );
}

/** Inline-link any URLs found in audit note text so an SE can click
 *  through to the folder/dashboard/KG entity that was created. The
 *  pipeline phase audit entries embed Grafana Cloud URLs in their note
 *  bodies for exactly this purpose. */
function LinkifiedNote({ text }: { text: string }) {
  // Conservative URL detector — matches http(s) URLs ending at whitespace
  // or sentence punctuation. Won't break on URLs with query strings.
  const parts = text.split(/(\bhttps?:\/\/[^\s)]+)/g);
  return (
    <>
      {parts.map((part, i) => {
        if (/^https?:\/\//.test(part)) {
          // Trim trailing punctuation that's almost certainly sentence-end,
          // not URL-meaningful (e.g. "https://x.com/foo." → strip the dot).
          const trimmed = part.replace(/[.,;:!?]+$/, "");
          const trailing = part.slice(trimmed.length);
          return (
            <span key={i}>
              <a
                href={trimmed}
                target="_blank"
                rel="noreferrer"
                className="text-[var(--color-accent)] hover:underline break-all"
              >
                {trimmed}
              </a>
              {trailing}
            </span>
          );
        }
        return <span key={i}>{part}</span>;
      })}
    </>
  );
}

function AuditPanel({ rows }: { rows: Array<{ timestamp: string; actor: string; action: string; from_state: string | null; to_state: string | null; note: string | null }> }) {
  if (rows.length === 0) return null;
  return (
    <Card className="p-5">
      <h2 className="text-sm font-medium text-[var(--color-text-muted)] uppercase tracking-wider mb-3">
        Audit history
      </h2>
      <div className="space-y-3">
        {rows.map((r, i) => {
          // Cloud-asset audit actions get a small accent dot so they're
          // distinguishable from plan-state transitions at a glance.
          const isCloudCreate = r.action.startsWith("cloud.");
          return (
            <div key={i} className="flex items-start gap-3 text-xs">
              <div className="text-[var(--color-text-faint)] font-mono shrink-0 w-32">
                {new Date(r.timestamp).toLocaleString()}
              </div>
              <div
                className={cn(
                  "shrink-0 w-1.5 h-1.5 rounded-full mt-1.5",
                  isCloudCreate
                    ? "bg-[var(--color-accent)]"
                    : "bg-[var(--color-text-faint)]/40",
                )}
              />
              <div className="flex-1 min-w-0">
                <div>
                  <span className="text-[var(--color-text-muted)]">{r.actor}</span>{" "}
                  <span className={isCloudCreate ? "font-medium" : ""}>{r.action}</span>{" "}
                  {r.from_state && r.to_state && (
                    <span className="text-[var(--color-text-faint)] font-mono">{r.from_state} → {r.to_state}</span>
                  )}
                </div>
                {r.note && (
                  <div className="text-[var(--color-text-muted)] mt-1 leading-relaxed">
                    <LinkifiedNote text={r.note} />
                  </div>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </Card>
  );
}
