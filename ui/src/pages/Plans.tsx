import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate, useParams, useSearchParams, Link } from "react-router-dom";
import { useMemo, useState } from "react";
import {
  ArrowLeft, Check, AlertCircle, Play, Trash2, MessageCircle, Code2,
  Stethoscope, CheckCircle2, XCircle, AlertTriangle, MinusCircle, Loader2,
  Sparkles, Activity, ChevronDown, ChevronRight, FileDown,
  ScrollText, Hammer,
} from "lucide-react";

import {
  listPlans, getPlan, getPlanAudit, approvePlan,
  startRun, deletePlan, replacePlanJson, getPlanHealth, listPipelines,
  type RunKind, type HealthReport,
} from "@/lib/api";
import { usePipeline } from "@/lib/PipelineContext";
import { Card } from "@/components/Card";
import { Badge, reviewStateTone } from "@/components/Badge";
import { CrumbChip } from "@/components/CrumbChip";
import { Button } from "@/components/Button";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { Tabs } from "@/components/Tabs";
import { JsonEditor } from "@/components/JsonEditor";
import { DemoSessionCard } from "@/components/DemoSessionCard";
import { DemoHistorySection } from "@/pages/Audit";
import { AuditTrailCard } from "@/components/plan/AuditTrailCard";
import { DashboardsAlertsCard } from "@/components/plan/DashboardsAlertsCard";
import { IncidentScriptTimeline } from "@/components/plan/IncidentScriptTimeline";
import { PlanTabs, type PlanTabId } from "@/components/plan/PlanTabs";
import { ProcessesTable } from "@/components/plan/ProcessesTable";
import { SampleDataSourcesCard } from "@/components/plan/SampleDataSourcesCard";
import { TelemetryShapeCard } from "@/components/plan/TelemetryShapeCard";
import {
  deriveDashboardsAndAlerts, deriveIncidentStops, deriveProcesses,
  deriveSampleSources, deriveTelemetryShape,
} from "@/lib/plan-derivations";
import { AgentChatPanel } from "@/pages/Profiles";
import { cn } from "@/lib/cn";

// ─── List ──────────────────────────────────────────────────────────

export function PlansListPage() {
  // ?profile=prof-xxx narrows the list to plans from one CompanyProfile.
  // The Profile detail page's "See all on Plans →" link sets this so
  // an SE can keep their context when jumping from one surface to the
  // other. Stripping the param via setSearchParams clears the filter.
  const [searchParams, setSearchParams] = useSearchParams();
  const profileFilter = searchParams.get("profile") ?? undefined;

  const plans = useQuery({
    queryKey: ["plans", profileFilter ?? "all"],
    queryFn: () => listPlans({ source_profile_id: profileFilter }),
    refetchInterval: 5_000,
  });
  const navigate = useNavigate();

  return (
    <div className="space-y-6">
      <header>
        <div className="text-[11px] font-mono uppercase tracking-[0.08em] text-[var(--color-text-faint)]">
          Plans
        </div>
        <h1 className="mt-2 text-[28px] font-semibold tracking-tight leading-tight text-[var(--color-text)]">
          Every plan, ready to review.
        </h1>
        <p className="text-[var(--color-text-muted)] mt-1 text-sm max-w-2xl">
          DemoPlans the planner produced. Click in to inspect, approve, refine, or
          start a live demo.
        </p>
        {profileFilter && (
          <div className="mt-3 inline-flex items-center gap-2 px-2.5 py-1 rounded-md bg-[var(--color-accent-bg)] border border-[color:var(--color-accent-border)] text-xs">
            <span className="text-[var(--color-text-muted)]">filter</span>
            <span className="font-mono text-[var(--color-accent)]">
              profile = {profileFilter}
            </span>
            <button
              type="button"
              onClick={() => {
                const next = new URLSearchParams(searchParams);
                next.delete("profile");
                setSearchParams(next, { replace: true });
              }}
              aria-label="Clear profile filter"
              className="text-[var(--color-text-faint)] hover:text-[var(--color-text)]"
            >
              ×
            </button>
          </div>
        )}
      </header>
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
                    <td className="px-4 py-3 font-mono text-xs text-[var(--color-text-muted)]">
                      {/* Plain text on purpose. Clicking a plan row should
                          land on the plan, full stop; the profile crumb
                          is reachable from the plan detail page when
                          needed. Mixing two click targets in one row
                          made the flow confusing. */}
                      {p.source_profile_id}
                    </td>
                    <td className="px-4 py-3">
                      <Badge tone={p.pending ? "info" : reviewStateTone(p.review_state)}>
                        {p.review_state}
                      </Badge>
                    </td>
                    <td className="px-4 py-3 text-right tabular-nums">
                      {p.pending ? <span className="text-[var(--color-text-faint)]">, </span> : p.process_count}
                    </td>
                    <td className="px-4 py-3 text-right tabular-nums">
                      {p.pending ? <span className="text-[var(--color-text-faint)]">, </span> : p.kg_node_count}
                    </td>
                    <td className="px-4 py-3 text-right tabular-nums">
                      {p.pending ? <span className="text-[var(--color-text-faint)]">, </span> : p.alert_count}
                    </td>
                    <td className="px-4 py-3 text-right tabular-nums">
                      {p.pending ? <span className="text-[var(--color-text-faint)]">, </span> : p.dashboard_count}
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
        <PlanDetailBody plan={plan.data} audit={audit.data ?? []} />
      )}
    </div>
  );
}

// ──────────────────────────────────────────────────────────────────
// PlanDetailBody: header → CDD plan-grid (KG + live demo + stats) →
// sticky tab strip → 5 deep sections → footer CTA → editor row.
// Split out of PlanDetailPage so the body's helpers + memos don't
// have to deal with `plan.isLoading` / null-checking the whole way
// down.
// ──────────────────────────────────────────────────────────────────

function PlanDetailBody({
  plan, audit,
}: {
  plan: PlanDoc;
  audit: Array<{
    timestamp: string;
    actor: string;
    action: string;
    from_state: string | null;
    to_state: string | null;
    note: string | null;
  }>;
}) {
  const processes = useMemo(() => deriveProcesses(plan), [plan]);
  const dashboards = useMemo(() => deriveDashboardsAndAlerts(plan), [plan]);
  const telemetry = useMemo(() => deriveTelemetryShape(plan), [plan]);
  const incidentStops = useMemo(() => deriveIncidentStops(plan), [plan]);
  const sampleSources = useMemo(() => deriveSampleSources(plan), [plan]);
  const [activeTab, setActiveTab] = useState<PlanTabId>("processes");

  // `isApproved` gates the DemoSessionCard in the hero right column.
  // Export + start-demo actions live in PlanHeader's top-right action
  // bar, so the v1 helpers that wired the footer CTA were removed
  // along with ReadyToDemoCta.
  const isApproved =
    plan.review_state === "approved_for_provision"
    || plan.review_state === "provisioned";

  return (
    <div className="space-y-6">
      <PlanHeader plan={plan} />

      {/* CDD plan-detail body: knowledge graph on the left (1.4fr),
          live demo session + plan contents stats on the right (1fr).
          Collapses to single column below xl. `items-start` keeps
          each column at its natural height. */}
      <div className="grid gap-5 xl:grid-cols-[1.4fr_1fr] items-start">
        <KnowledgeGraphPanel plan={plan} />
        <div className="space-y-5">
          {isApproved && (
            <div id="plan-demo-session">
              <DemoSessionCard planId={plan.plan_id} />
            </div>
          )}
          <PlanContentsStats plan={plan} />
        </div>
      </div>

      {/* Controlled tab strip + a single panel at a time. Replaces an
          earlier anchor-scroll prototype that turned the page into a
          long ribbon; now each section's content is swapped in place
          so the page stays compact and the SE can sweep through the
          plan without scroll fatigue. */}
      <PlanTabs
        tabs={[
          { id: "processes",  label: "Processes & SLOs",  count: processes.length },
          { id: "dashboards", label: "Dashboards & alerts", count: dashboards.length },
          { id: "telemetry",  label: "Telemetry shape" },
          { id: "incident",   label: "Incident script", count: incidentStops.length },
          { id: "audit",      label: "Audit & data" },
        ]}
        activeId={activeTab}
        onChange={(id) => setActiveTab(id as PlanTabId)}
      />

      <div role="tabpanel" aria-label={activeTab}>
        {activeTab === "processes" && (
          <ProcessesTable rows={processes} />
        )}
        {activeTab === "dashboards" && (
          <DashboardsAlertsCard items={dashboards} showAllInitially />
        )}
        {activeTab === "telemetry" && (
          <TelemetryShapeCard shape={telemetry} />
        )}
        {activeTab === "incident" && (
          <IncidentScriptTimeline
            stops={incidentStops}
            totalMinutes={plan.incident_script?.total_duration_minutes ?? 0}
          />
        )}
        {activeTab === "audit" && (
          <div className="grid grid-cols-1 xl:grid-cols-2 gap-5 items-start">
            <AuditTrailCard entries={audit} />
            <SampleDataSourcesCard sources={sampleSources} />
          </div>
        )}
      </div>

      {/* Power-user disclosure. Holds the surfaces that aren't
          right-clicked daily but the SE needs: composition (raw plan
          tree), state transitions (approve / tear-down), health
          diagnostics, demo session history, and the refine column
          (chat + JSON editor).
          Audit history was removed from here — it duplicated the
          Audit tab's AuditTrailCard. The footer ReadyToDemoCta was
          removed too — Export plan / Start demo already live in the
          header action bar, so a second copy at the bottom was
          redundant once tabs collapsed the page to one screen. */}
      <details className="group">
        <summary
          className={cn(
            "list-none cursor-pointer select-none",
            "flex items-center gap-2 px-4 py-3 rounded-md border",
            "bg-[var(--color-canvas-elev1)] border-[var(--color-border)]",
            "hover:border-[var(--color-border-strong)] transition-colors",
            "text-sm font-medium text-[var(--color-text)]",
          )}
        >
          <ChevronRight
            size={14}
            className="text-[var(--color-text-faint)] transition-transform group-open:rotate-90"
          />
          <span className="flex-1">Composition, actions, health &amp; refine</span>
          <span className="text-[11px] font-mono text-[var(--color-text-faint)]">power user</span>
        </summary>
        <div className="grid grid-cols-1 xl:grid-cols-2 gap-5 items-start mt-4">
          <div className="space-y-5">
            <PlanTree plan={plan} />
            <PlanActions planId={plan.plan_id} state={plan.review_state} />
            <HealthPanel planId={plan.plan_id} />
            <DemoHistorySection planId={plan.plan_id} />
          </div>
          <RefineColumn planId={plan.plan_id} planJson={plan} />
        </div>
      </details>
    </div>
  );
}


/** Right column on Plan detail: tabs between agent chat (narrative
 *  refinement) and a JSON editor (direct schema-validated edits).
 *
 *  Before this component existed, the right column was a single
 *  AgentChatPanel, useful for "what should I change" but read-only.
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
          subtitle="Ask the planner to reconsider a process, alert, or incident. Suggestions are narrative, switch to the JSON tab to apply changes."
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

/** Human-readable mapping for review_state, used in the page eyebrow.
 *  Same five states the backend emits, written as a phrase the SE
 *  recognises without translation. */
const REVIEW_STATE_LABEL: Record<string, string> = {
  draft:                  "draft",
  se_reviewed:            "in review",
  approved_for_provision: "approved for provision",
  provisioned:            "provisioned",
  torn_down:              "torn down",
};

/** A short, action-oriented title for the H1. Picks a state-relevant
 *  word to apply the accent→signal gradient to, the same "display"
 *  treatment the v1 design canvas uses for the marquee moment on
 *  every page. */
function planTitle(state: string): { lead: string; display: string } {
  switch (state) {
    case "provisioned":            return { lead: "Demo is",      display: "live" };
    case "approved_for_provision": return { lead: "Ready to",      display: "demo" };
    case "se_reviewed":            return { lead: "Plan is in",   display: "review" };
    case "torn_down":              return { lead: "Demo was",      display: "torn down" };
    default:                       return { lead: "Demo plan",     display: "draft" };
  }
}

/** Look up the most recent pipeline whose plan_id matches and link
 *  to it. Lets the SE jump from plan-detail back to the build that
 *  produced it without going through the global Builds list. Returns
 *  null while loading or if no matching pipeline is in the DB.
 *
 *  Renders as a CrumbChip so the link reads as a real "click me"
 *  button, not as faint metadata next to the source-profile crumb. */
function BuiltByChip({ planId }: { planId: string }) {
  const pipelines = useQuery({
    queryKey: ["pipelines"],
    queryFn: listPipelines,
    // Pipelines list is cheap; we just need ONE match. Cached.
  });
  const matches = (pipelines.data ?? []).filter((p) => p.plan_id === planId);
  if (matches.length === 0) return null;
  const m = matches[0];
  return (
    <CrumbChip
      to={`/pipelines/${m.pipeline_id}`}
      label="built by"
      value={m.pipeline_id.slice(0, 8)}
      icon={Hammer}
      title="Open the pipeline that produced this plan"
    />
  );
}

function PlanHeader({ plan }: { plan: PlanDoc }) {
  const t = planTitle(plan.review_state);
  const isApproved =
    plan.review_state === "approved_for_provision"
    || plan.review_state === "provisioned";

  function exportPlan() {
    // Drop the raw plan_json into the browser as a download. Cheap,
    // no API addition needed; the SE can hand the file to a colleague
    // or attach to a Slack thread.
    const blob = new Blob([JSON.stringify(plan, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `${plan.plan_id}.json`;
    a.click();
    URL.revokeObjectURL(url);
  }

  function scrollToDemo() {
    document.getElementById("plan-demo-session")?.scrollIntoView({
      behavior: "smooth", block: "start",
    });
  }

  return (
    <div className="flex items-start gap-6 flex-wrap">
      <div className="flex-1 min-w-[280px]">
        <div className="text-[11px] font-mono uppercase tracking-[0.08em] text-[var(--color-text-faint)]">
          Plan, {REVIEW_STATE_LABEL[plan.review_state] ?? plan.review_state}
          <span className="ml-2 text-[var(--color-text-faint)]">
            {plan.plan_id.slice(0, 8)}
          </span>
        </div>
        <h1 className="mt-1 text-[32px] font-medium tracking-tight leading-tight text-[var(--color-text)]">
          {t.lead} <span className="h1-display">{t.display}</span>.
        </h1>
        <p className="mt-3 text-[var(--color-text-muted)] text-[15px] leading-relaxed max-w-2xl">
          {plan.narrative}
        </p>
        <div className="mt-4 flex items-center gap-2 flex-wrap">
          <CrumbChip
            to={`/profiles/${plan.source_profile_id}`}
            label="source profile"
            value={plan.source_profile_id}
            icon={ScrollText}
            title="Open the profile this plan was generated from"
          />
          <BuiltByChip planId={plan.plan_id} />
        </div>
      </div>
      <div className="flex items-center gap-2 flex-wrap">
        <Button
          variant="secondary"
          size="sm"
          onClick={exportPlan}
          title="Download the plan_json as a file."
        >
          <FileDown size={12} /> Export plan
        </Button>
        {isApproved && (
          <Button
            variant="primary"
            size="sm"
            onClick={scrollToDemo}
            title="Jump to the live demo session controls below."
          >
            <Play size={12} /> Start demo
          </Button>
        )}
      </div>
    </div>
  );
}

/** Knowledge-graph card. CDD header (title + node/edge count) plus the
 *  existing tier-breakdown body. The fancy SVG node-link diagram from
 *  the design canvas is intentionally left out for now; the per-tier
 *  entity-type list still tells the SE what the plan models. */
function KnowledgeGraphPanel({ plan }: { plan: PlanDoc }) {
  const nodes = plan.knowledge_graph.nodes ?? [];
  const edges = plan.knowledge_graph.edges ?? [];
  return (
    <Card className="p-5">
      <div className="flex items-center justify-between gap-3 mb-4">
        <h3 className="text-sm font-medium text-[var(--color-text)] m-0">
          Knowledge graph
        </h3>
        <span className="font-mono text-[11px] text-[var(--color-text-faint)] tabular-nums">
          {nodes.length} nodes · {edges.length} edges
        </span>
      </div>
      <EntityTypesBody plan={plan} />
      {/* Legend mirrors the CDD plan-detail swatch row: business teal,
          agentic blue, technical sky. */}
      <div className="flex flex-wrap gap-3 mt-4 text-[11px] text-[var(--color-text-muted)]">
        <span className="inline-flex items-center gap-1.5">
          <i className="inline-block w-2.5 h-2.5 rounded-full" style={{ background: "var(--color-accent)" }} />
          Business
        </span>
        <span className="inline-flex items-center gap-1.5">
          <i className="inline-block w-2.5 h-2.5 rounded-full" style={{ background: "var(--color-info)" }} />
          Agentic
        </span>
        <span className="inline-flex items-center gap-1.5">
          <i className="inline-block w-2.5 h-2.5 rounded-full" style={{ background: "var(--color-signal)" }} />
          Technical
        </span>
      </div>
    </Card>
  );
}

/** Snapshot of what's IN the plan: the four counts SE eyes look for
 *  before clicking Start demo. Mirrors the CDD plan-detail "Plan
 *  contents" card (Processes / Dashboards / Alerts / Incident script
 *  duration). All numbers come from the plan_json directly. */
function PlanContentsStats({ plan }: { plan: PlanDoc }) {
  const stats: { label: string; value: string; sub?: string }[] = [
    {
      label: "Processes",
      value: plan.business_process_models.length.toLocaleString(),
    },
    {
      label: "Dashboards",
      value: plan.dashboard_specs.length.toLocaleString(),
    },
    {
      label: "Alerts",
      value: plan.alert_specs.length.toLocaleString(),
    },
    {
      label: "Incident script",
      value: plan.incident_script?.total_duration_minutes
        ? plan.incident_script.total_duration_minutes.toLocaleString()
        : "—",
      sub: plan.incident_script?.total_duration_minutes ? "min" : undefined,
    },
  ];
  return (
    <Card className="p-5">
      <h3 className="text-sm font-medium text-[var(--color-text)] m-0 mb-3">Plan contents</h3>
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        {stats.map((s) => (
          <div key={s.label}>
            <div className="text-[10px] font-mono uppercase tracking-[0.06em] text-[var(--color-text-faint)]">
              {s.label}
            </div>
            <div className="mt-1 text-[22px] font-medium tabular-nums text-[var(--color-text)]">
              {s.value}
              {s.sub && <span className="text-[14px] text-[var(--color-text-faint)] ml-1">{s.sub}</span>}
            </div>
          </div>
        ))}
      </div>
    </Card>
  );
}

/** At-a-glance breakdown of every entity type the plan models, grouped
 *  by tier (business / technical / agentic). Most useful right after a
 *  plan lands so the SE can verify the planner picked vertical-fit
 *  subtypes (no "store" on BlueSky Airlines etc.) without diving into
 *  the raw JSON. */
/** Tier-grouped entity-type chip body. Extracted from the old
 *  EntityTypesPanel so KnowledgeGraphPanel can compose it inside its
 *  own card header without nesting Card-in-Card.
 *
 *  Returns null if the plan has no nodes (planner ran but produced
 *  zero entities, rare but possible for early failures). */
function EntityTypesBody({ plan }: { plan: PlanDoc }) {
  const nodes = plan.knowledge_graph.nodes ?? [];
  if (nodes.length === 0) {
    return (
      <div className="text-xs text-[var(--color-text-faint)] italic">
        No entities in this plan yet.
      </div>
    );
  }

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

  const tiers = (Object.keys(groups) as string[]).filter(t => Object.keys(groups[t]).length > 0);

  return (
    <div className="space-y-4">
      {tiers.map(tier => {
        const subtypes = groups[tier];
        const subtypeKeys = Object.keys(subtypes).sort();
        return (
          <div key={tier}>
            <div className="text-[10px] font-mono text-[var(--color-text-faint)] uppercase tracking-wider mb-2">
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
  );
}



function PlanTree({ plan }: { plan: PlanDoc }) {
  return (
    <Card className="p-5">
      <h3 className="text-sm font-medium text-[var(--color-text)] m-0 mb-3">
        Composition
      </h3>
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
  // "View latest build" when one exists. Refetched on a slow cadence,   // the relevant pipeline IDs only change when this user kicks off a
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
  // AND plan because the plan is already in DB and approved/ready,   // start at `approve` so the orchestrator records the audit transition,
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
    // research is skipped, the URL is just metadata for the pipelines row.
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
        // No parent_pipeline_id, this is a fresh build, not a resume.
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
      <h3 className="text-sm font-medium text-[var(--color-text)] m-0">
        Actions
      </h3>

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

      {/* Primary build CTAs, the modern path. View previous build OR
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
              ? "Start a new pipeline that skips research AND plan (uses this plan), runs approve → generate → provision → kg-publish."
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
          Advanced, run individual CLI phases
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
 *  inline with a "fix" hint. The agent isn't trusted by default, this
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
      <div className="flex items-start justify-between gap-4">
        <div className="min-w-0">
          <h3 className="text-sm font-medium text-[var(--color-text)] m-0 flex items-center gap-2">
            <Stethoscope size={14} className="text-[var(--color-text-faint)]" /> KG health
          </h3>
          <p className="text-xs text-[var(--color-text-faint)] mt-1">
            Validates this plan's metrics + entities in Cloud against every
            invariant we know about (asserts scope, customer label, Pod node,
            Store fan-out, affinity metrics, entity-count parity).
          </p>
        </div>
        <Button
          size="sm"
          variant="primary"
          onClick={() => runMut.mutate()}
          disabled={runMut.isPending}
          className="shrink-0 whitespace-nowrap"
        >
          {runMut.isPending ? (
            <>
              <Loader2 size={12} className="animate-spin" /> Running&hellip;
            </>
          ) : (
            <>
              <Stethoscope size={12} /> Run check
            </>
          )}
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

// LinkifiedNote + AuditPanel (v1) were removed. AuditTrailCard in the
// Audit tab is the canonical audit view now; it renders the same row
// shape with better hierarchy. The v1 AuditPanel-only URL linkifier
// went with it — if a future surface needs inline URL rendering, lift
// it back into `lib/` as a shared helper.
