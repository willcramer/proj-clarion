import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { useState } from "react";
import {
  ScrollText, ClipboardList, Network, Activity, Database, AlertCircle,
  Trash2, ExternalLink, Loader2, AlertTriangle, Sparkles,
} from "lucide-react";
import {
  getDashboardSummary, listPlans, listOrphanFolders, deleteOrphanFolder,
  type OrphanFolder,
} from "@/lib/api";
import { Card } from "@/components/Card";
import { Badge, reviewStateTone } from "@/components/Badge";
import { Button } from "@/components/Button";
import { KpiCard } from "@/components/KpiCard";
import { DrilldownPanel } from "@/components/DrilldownPanel";
import { cn } from "@/lib/cn";

export function DashboardPage() {
  const summary = useQuery({ queryKey: ["dashboard"], queryFn: getDashboardSummary });
  const plans = useQuery({ queryKey: ["plans"], queryFn: () => listPlans() });
  const navigate = useNavigate();

  const s = summary.data;
  const recent = (plans.data ?? []).slice(0, 6);

  // Drilldown state — null means nothing is open. Single-string state
  // means we open one drilldown at a time, so the page doesn't grow into
  // a wall of expanded panels.
  const [drilldown, setDrilldown] = useState<null | "plans-by-state" | "kg">(null);
  const toggle = (k: "plans-by-state" | "kg") =>
    setDrilldown((cur) => (cur === k ? null : k));

  return (
    <div className="space-y-8">
      <header className="flex items-end justify-between gap-4 flex-wrap">
        <div>
          <h1 className="text-3xl font-semibold tracking-tight">Overview</h1>
          <p className="text-[var(--color-text-muted)] mt-1 text-sm">
            Vertical-aware demo data, end to end. Profiles → plans → live telemetry to your stack.
          </p>
        </div>
        <Button size="sm" variant="primary" onClick={() => navigate("/new")}>
          <Sparkles size={12} /> New build
        </Button>
      </header>

      {/* Top KPI strip — six tiles, two interactive (Plans →
          plans-by-state breakdown, KG → node/edge breakdown). */}
      <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
        <KpiCard
          icon={ScrollText}
          label="Profiles"
          value={fmtNum(s?.profiles_total)}
          tone="info"
          hint="researched companies"
        />
        <KpiCard
          icon={ClipboardList}
          label="Plans"
          value={fmtNum(s?.plans_total)}
          tone="accent"
          hint={
            s && Object.keys(s.plans_by_state).length > 0
              ? `${Object.keys(s.plans_by_state).length} states`
              : undefined
          }
          onClick={
            s && Object.keys(s.plans_by_state).length > 0
              ? () => toggle("plans-by-state")
              : undefined
          }
          selected={drilldown === "plans-by-state"}
          controlsId="dash-drill-plans-by-state"
        />
        <KpiCard
          icon={Network}
          label="KG nodes"
          value={fmtNum(s?.kg_nodes_total)}
          tone="info"
          hint={s ? `${fmtNum(s.kg_edges_total)} edges` : undefined}
          onClick={s ? () => toggle("kg") : undefined}
          selected={drilldown === "kg"}
          controlsId="dash-drill-kg"
        />
        <KpiCard
          icon={Database}
          label="Events"
          value={fmtNum(s?.business_events_total)}
          tone="success"
          hint="business events stored"
        />
        <KpiCard
          icon={Activity}
          label="Last event"
          value={s?.last_event_at ? formatRelative(s.last_event_at) : "—"}
          tone="neutral"
          compact
        />
        <KpiCard
          icon={Network}
          label="KG edges"
          value={fmtNum(s?.kg_edges_total)}
          tone="info"
          compact
        />
      </div>

      {/* Drilldown surfaces — at most one open at a time, slides under
          the strip without disrupting the table below. */}
      <DrilldownPanel
        id="dash-drill-plans-by-state"
        open={drilldown === "plans-by-state"}
        onClose={() => setDrilldown(null)}
        title={`Plans by review state · ${Object.values(s?.plans_by_state ?? {}).reduce((a, b) => a + b, 0)} total`}
        subtitle="Click a state to filter the Plans page"
      >
        <div className="flex flex-wrap gap-2">
          {Object.entries(s?.plans_by_state ?? {})
            .sort((a, b) => b[1] - a[1])
            .map(([state, count]) => (
              <button
                key={state}
                type="button"
                onClick={() => navigate(`/plans?state=${encodeURIComponent(state)}`)}
                className="rounded-md hover:opacity-80 transition-opacity focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent)]"
              >
                <Badge tone={reviewStateTone(state)}>
                  {state}{" "}
                  <span className="ml-1 text-[10px] opacity-70 font-mono">{count}</span>
                </Badge>
              </button>
            ))}
        </div>
      </DrilldownPanel>

      <DrilldownPanel
        id="dash-drill-kg"
        open={drilldown === "kg"}
        onClose={() => setDrilldown(null)}
        title="Knowledge Graph"
        subtitle={s ? `${fmtNum(s.kg_nodes_total)} nodes across ${fmtNum(s.kg_edges_total)} edges` : ""}
      >
        <dl className="grid grid-cols-2 gap-3 text-sm">
          <div>
            <dt className="text-xs text-[var(--color-text-muted)] uppercase tracking-wider">Nodes</dt>
            <dd className="text-xl font-semibold tabular-nums">{fmtNum(s?.kg_nodes_total)}</dd>
          </div>
          <div>
            <dt className="text-xs text-[var(--color-text-muted)] uppercase tracking-wider">Edges</dt>
            <dd className="text-xl font-semibold tabular-nums">{fmtNum(s?.kg_edges_total)}</dd>
          </div>
          <div className="col-span-2 text-xs text-[var(--color-text-faint)] mt-1">
            The KG is the spine that ties the business tier (account, business unit,
            region, brand) to the tech tier (cluster, node, pod, service, database).
            Open a plan to see its full graph in the Plans detail view.
          </div>
        </dl>
      </DrilldownPanel>

      <OrphanCleanup />

      <div>
        <div className="flex items-baseline justify-between mb-3">
          <h2 className="text-lg font-medium">Recent plans</h2>
          <button
            onClick={() => navigate("/plans")}
            className="text-xs text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
          >
            View all →
          </button>
        </div>
        <Card>
          {plans.isLoading ? (
            <div className="p-8 text-center text-[var(--color-text-faint)]">Loading…</div>
          ) : recent.length === 0 ? (
            <EmptyState
              icon={AlertCircle}
              title="No plans yet"
              hint="Run `just research <url>` and then `just plan <profile>` to get started."
            />
          ) : (
            <table className="w-full text-sm">
              <thead className="text-xs text-[var(--color-text-faint)] uppercase tracking-wider border-b border-[var(--color-border)]">
                <tr>
                  <th className="text-left font-medium px-4 py-3">Plan</th>
                  <th className="text-left font-medium px-4 py-3">Profile</th>
                  <th className="text-left font-medium px-4 py-3">State</th>
                  <th className="text-right font-medium px-4 py-3">Processes</th>
                  <th className="text-right font-medium px-4 py-3">KG nodes</th>
                  <th className="text-right font-medium px-4 py-3">Alerts</th>
                  <th className="text-right font-medium px-4 py-3">Updated</th>
                </tr>
              </thead>
              <tbody>
                {recent.map((p) => (
                  <tr
                    key={p.plan_id}
                    onClick={() => navigate(`/plans/${p.plan_id}`)}
                    className="border-b border-[var(--color-border)] last:border-0 hover:bg-white/[0.02] cursor-pointer transition-colors"
                  >
                    <td className="px-4 py-3 font-mono text-xs">{p.plan_id_short}</td>
                    <td className="px-4 py-3">{p.source_profile_id}</td>
                    <td className="px-4 py-3">
                      <Badge tone={reviewStateTone(p.review_state)}>{p.review_state}</Badge>
                    </td>
                    <td className="px-4 py-3 text-right tabular-nums">{p.process_count}</td>
                    <td className="px-4 py-3 text-right tabular-nums">{p.kg_node_count}</td>
                    <td className="px-4 py-3 text-right tabular-nums">{p.alert_count}</td>
                    <td className="px-4 py-3 text-right text-xs text-[var(--color-text-muted)]">
                      {formatRelative(p.updated_at)}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </Card>
      </div>
    </div>
  );
}

/** Cleanup card for `clarion-*` Grafana folders whose plan was deleted from
 *  the DB without `cleanup_cloud=true`. Hidden when nothing's orphaned, so
 *  it isn't permanent UI furniture — only surfaces when there's a problem. */
function OrphanCleanup() {
  const qc = useQueryClient();
  const orphans = useQuery({
    queryKey: ["orphans"],
    queryFn: listOrphanFolders,
    refetchInterval: 30_000,
    retry: 0,
  });
  const [busyUid, setBusyUid] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const deleteMut = useMutation({
    mutationFn: (uid: string) => {
      setBusyUid(uid);
      return deleteOrphanFolder(uid);
    },
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["orphans"] });
      setBusyUid(null);
      setError(null);
    },
    onError: (e: Error) => {
      setError(e.message);
      setBusyUid(null);
    },
  });

  const items = orphans.data ?? [];
  // Don't render anything when everything's clean — keeps the dashboard
  // free of permanent-looking maintenance UI.
  if (orphans.isLoading || (orphans.isFetched && items.length === 0)) return null;

  async function deleteAll() {
    setError(null);
    for (const o of items) {
      await deleteMut.mutateAsync(o.uid).catch(() => {/* keep going */});
    }
  }

  return (
    <Card className="p-5 border-[var(--color-warning)]/30 bg-[var(--color-warning)]/5">
      <div className="flex items-start gap-3 mb-3">
        <AlertTriangle className="text-[var(--color-warning)] shrink-0 mt-0.5" size={18} />
        <div className="flex-1">
          <h2 className="text-sm font-medium">Orphan Grafana folders ({items.length})</h2>
          <p className="text-xs text-[var(--color-text-muted)] mt-1 max-w-2xl">
            These <code className="font-mono">clarion-*</code> folders exist in your stack
            but their plan is no longer in the DB. Most likely from a delete that didn't
            include the Cloud-cleanup checkbox. Deleting cascades the folder + its
            dashboards + its alert rules.
          </p>
        </div>
        <Button size="sm" variant="danger" onClick={() => void deleteAll()} disabled={deleteMut.isPending}>
          {deleteMut.isPending ? <Loader2 size={12} className="animate-spin" /> : <Trash2 size={12} />}
          Delete all
        </Button>
      </div>
      {error && (
        <div className="text-xs text-[var(--color-danger)] mb-2">{error}</div>
      )}
      <div className="space-y-1">
        {items.map((o) => (
          <OrphanRow
            key={o.uid}
            orphan={o}
            busy={busyUid === o.uid}
            onDelete={() => deleteMut.mutate(o.uid)}
          />
        ))}
      </div>
    </Card>
  );
}

function OrphanRow({
  orphan, busy, onDelete,
}: { orphan: OrphanFolder; busy: boolean; onDelete: () => void }) {
  const stackUrl = orphan.url ? `${orphan.url}` : null;
  return (
    <div className="flex items-center gap-3 px-2 py-2 text-sm border-b border-[var(--color-border)] last:border-0">
      <div className="flex-1 min-w-0">
        <div className="font-medium truncate">{orphan.title}</div>
        <div className="text-xs text-[var(--color-text-faint)] flex items-center gap-2 mt-0.5">
          <span className="font-mono truncate">{orphan.uid}</span>
          {orphan.plan_id && (
            <span className="text-[var(--color-text-muted)]">· plan {orphan.plan_id.slice(0, 8)}</span>
          )}
          <span className="text-[var(--color-warning)]">· {orphan.reason}</span>
        </div>
      </div>
      {stackUrl && (
        <a
          href={stackUrl}
          target="_blank"
          rel="noreferrer"
          className="text-xs text-[var(--color-text-muted)] hover:text-[var(--color-text)] inline-flex items-center gap-1"
        >
          view <ExternalLink size={10} />
        </a>
      )}
      <Button size="sm" variant="danger" onClick={onDelete} disabled={busy}>
        {busy ? <Loader2 size={12} className="animate-spin" /> : <Trash2 size={12} />}
        Delete
      </Button>
    </div>
  );
}

function StatCard({
  icon: Icon,
  label,
  value,
  accent,
  small,
}: {
  icon: typeof ScrollText;
  label: string;
  value: number | string | undefined;
  accent: "info" | "accent" | "success" | "neutral";
  small?: boolean;
}) {
  const accentColor = {
    info:    "text-[var(--color-info)]",
    accent:  "text-[var(--color-accent)]",
    success: "text-[var(--color-success)]",
    neutral: "text-[var(--color-text-muted)]",
  }[accent];
  return (
    <Card hover className="p-4">
      <div className="flex items-center justify-between mb-2">
        <span className="text-xs text-[var(--color-text-faint)] uppercase tracking-wider font-medium">
          {label}
        </span>
        <Icon size={14} className={cn("opacity-70", accentColor)} />
      </div>
      <div className={cn(
        "font-semibold tabular-nums tracking-tight",
        small ? "text-base" : "text-2xl",
        accentColor,
      )}>
        {value === undefined ? "…" : typeof value === "number" ? value.toLocaleString() : value}
      </div>
    </Card>
  );
}

function EmptyState({
  icon: Icon, title, hint,
}: {
  icon: typeof AlertCircle; title: string; hint: string;
}) {
  return (
    <div className="flex flex-col items-center justify-center py-12 text-center">
      <Icon size={28} className="text-[var(--color-text-faint)] mb-3" />
      <div className="text-sm font-medium">{title}</div>
      <div className="text-xs text-[var(--color-text-muted)] mt-1 max-w-xs">{hint}</div>
    </div>
  );
}

function fmtNum(n: number | string | undefined): string {
  if (n === undefined) return "…";
  if (typeof n === "string") return n;
  return n.toLocaleString();
}

function formatRelative(iso: string): string {
  const d = new Date(iso);
  const diffMs = Date.now() - d.getTime();
  const sec = Math.round(diffMs / 1000);
  if (sec < 60) return `${sec}s ago`;
  const min = Math.round(sec / 60);
  if (min < 60) return `${min}m ago`;
  const hr = Math.round(min / 60);
  if (hr < 24) return `${hr}h ago`;
  const day = Math.round(hr / 24);
  return `${day}d ago`;
}
