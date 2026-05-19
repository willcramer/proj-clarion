/**
 * Dashboard, the SE's landing page.
 *
 * Layout (per the v1 design canvas, `clarion-redesign.html`):
 *
 *   Welcome eyebrow + display H1, "Build a *live demo*." (gradient text)
 *   Hero grid (2-col)
 *     ├ HeroBuildCard, URL + preset chips → /new
 *     └ LiveDemoCard, active emitter sessions, Extend/Stop
 *   OrphanCleanup, auto-hidden when nothing's orphaned
 *   4 KpiCards, Plans · Profiles · Events · KG nodes
 *   DrilldownPanel, Plans-by-state or KG breakdown
 *   Demo library, card grid of researched companies (drill into a plan)
 *
 * Active pipeline state lives in the topbar (PipelineStatusPill), not
 * as a separate strip on this page. Demo-session state lives in the
 * LiveDemoCard (right column of the hero grid). They surface different
 * things: builds (research → publish) vs emitters (data-flowing-to-Cloud).
 *
 * The v2 "Recent builds table + Pagination" variant of this page was
 * tried and reverted — the Demo library is a much more useful landing
 * surface for an SE coming back between calls.
 */
import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import {
  ScrollText, ClipboardList, Network, Database, AlertCircle, ChevronRight,
  Sparkles, Trash2, ExternalLink, Loader2, AlertTriangle,
} from "lucide-react";

import {
  getDashboardSummary, listPlans, listProfiles, listOrphanFolders,
  deleteOrphanFolder,
  type OrphanFolder, type PlanSummary, type ProfileSummary,
} from "@/lib/api";
import { Badge, reviewStateTone } from "@/components/Badge";
import { Button } from "@/components/Button";
import { Card } from "@/components/Card";
import { DrilldownPanel } from "@/components/DrilldownPanel";
import { HeroBuildCard, type BuildPreset } from "@/components/HeroBuildCard";
import { KpiCard } from "@/components/KpiCard";
import { LiveDemoCard } from "@/components/LiveDemoCard";
import { cn } from "@/lib/cn";
import { usePipeline } from "@/lib/PipelineContext";

export function DashboardPage() {
  const pipeline = usePipeline();
  const summary = useQuery({ queryKey: ["dashboard"], queryFn: getDashboardSummary });
  const navigate = useNavigate();

  const s = summary.data;

  const [drilldown, setDrilldown] = useState<null | "plans-by-state" | "kg">(null);
  const toggle = (k: "plans-by-state" | "kg") =>
    setDrilldown((cur) => (cur === k ? null : k));

  /** Start a pipeline directly from the dashboard hero, no /new bounce.
   *  The PipelineContext attaches its own SSE stream, so once `start` resolves
   *  we navigate to /new which auto-renders the live PipelineRunView because
   *  the context now has a non-idle pipeline. */
  async function handleBuild(url: string, preset: BuildPreset) {
    const volume_per_day =
      preset === "smoke"  ?    500 :
      preset === "demo"   ?  2_500 :
      preset === "stress" ? 25_000 :
      /* auto */ undefined;
    try {
      await pipeline.start({ url, days: 1, volume_per_day });
      navigate("/new");
    } catch (e) {
      // Most common failure is the setup gate (missing tokens) or a 503
      // from the orchestrator. Surface synchronously so the SE knows
      // their click didn't get lost; toast system isn't wired into the
      // dashboard yet so a window.alert is the pragmatic fallback.
      const msg = e instanceof Error ? e.message : String(e);
      window.alert(`Couldn't start build:\n\n${msg}`);
    }
  }

  return (
    <div className="space-y-6">
      {/* Page title block, eyebrow + display-text H1 per v1 design.
          The gradient `live demo` span uses the accent→signal gradient
          to give the page a single "marquee" moment without competing
          with the hero card below. */}
      <div>
        <div className="text-[11px] font-mono uppercase tracking-[0.08em] text-[var(--color-text-faint)]">
          Welcome back
        </div>
        <h1 className="mt-2 text-[32px] font-medium tracking-tight leading-tight text-[var(--color-text)]">
          Build a <span className="h1-display">live demo</span>.
        </h1>
      </div>

      {/* Hero grid, HeroBuildCard (1.4fr) + LiveDemoCard (1fr).
          Collapses to single column below ~lg. */}
      <div className="grid gap-5 lg:grid-cols-[1.4fr_1fr]">
        <HeroBuildCard onBuild={handleBuild} />
        <LiveDemoCard />
      </div>

      {/* Orphan cleanup, self-hides when there's nothing to clean up,
          so this slot is invisible most of the time. Placed above the
          KPI strip so when it DOES surface, the SE sees it before
          continuing the demo. */}
      <OrphanCleanup />

      {/* 4-tile status strip per v1 design: Plans → Profiles → Events
          → KG nodes. Plans and KG nodes remain interactive (drilldown
          for state breakdown + node/edge details respectively). */}
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
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
          icon={ScrollText}
          label="Profiles"
          value={fmtNum(s?.profiles_total)}
          tone="info"
          hint="researched companies"
        />
        <KpiCard
          icon={Database}
          label="Events"
          value={fmtNum(s?.business_events_total)}
          tone="success"
          hint="business events stored"
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
      </div>

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

      <DemoLibrary />
    </div>
  );
}

// ──────────────────────────────────────────────────────────────────
// DemoLibrary, the homepage's "what's available to demo" surface.
//
// Each card represents a CompanyProfile the SE has researched. Status
// is derived from the profile's plans, "Ready to demo" if any plan
// is provisioned, "In review" if approved-for-provision, "Drafted"
// if just researched, "Researching" if the pipeline is still in flight.
// ──────────────────────────────────────────────────────────────────

function DemoLibrary() {
  const navigate = useNavigate();
  const profiles = useQuery({
    queryKey: ["profiles"],
    queryFn: listProfiles,
    refetchInterval: 15_000,
  });
  const plansAll = useQuery({
    queryKey: ["plans"],
    queryFn: () => listPlans(),
    refetchInterval: 15_000,
  });

  // Group plans by profile so per-card we can derive a status badge
  // without re-iterating in every render. Cheap; lists are bounded.
  const plansByProfile = useMemo(() => {
    const m = new Map<string, PlanSummary[]>();
    for (const p of plansAll.data ?? []) {
      const arr = m.get(p.source_profile_id) ?? [];
      arr.push(p);
      m.set(p.source_profile_id, arr);
    }
    return m;
  }, [plansAll.data]);

  const allProfiles = profiles.data ?? [];
  // Sort by most recent activity, created_at on profile is the only
  // reliable timestamp without N+1 fetches; good enough for dashboard.
  const sorted = useMemo(
    () => [...allProfiles].sort(
      (a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime(),
    ),
    [allProfiles],
  );
  const visible = sorted.slice(0, 6);

  return (
    <section aria-label="Demo library">
      <div className="flex items-baseline justify-between mb-3">
        <div>
          <div className="flex items-center gap-3">
            <h2 className="text-lg font-medium">Demo library</h2>
            <span className="text-xs text-[var(--color-text-faint)] font-mono tabular-nums">
              {allProfiles.length} researched
            </span>
          </div>
          <p className="text-xs text-[var(--color-text-muted)] mt-0.5">
            Every company you&rsquo;ve researched, with its current demo readiness. Pick one to open the plan.
          </p>
        </div>
        {allProfiles.length > visible.length && (
          <button
            onClick={() => navigate("/profiles")}
            className="text-xs text-[var(--color-text-muted)] hover:text-[var(--color-text)] flex items-center gap-1"
          >
            View all <ChevronRight size={12} />
          </button>
        )}
      </div>

      {profiles.isLoading ? (
        <Card><div className="p-8 text-center text-[var(--color-text-faint)]">Loading…</div></Card>
      ) : allProfiles.length === 0 ? (
        <Card>
          <EmptyState
            icon={Sparkles}
            title="No demos yet"
            hint="Type a URL in the hero card above to research a company. Its profile lands here when research completes."
          />
        </Card>
      ) : (
        <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3">
          {visible.map((p) => (
            <DemoCard
              key={p.profile_id}
              profile={p}
              plans={plansByProfile.get(p.profile_id) ?? []}
              onClick={() => {
                if (p.pending && p.pipeline_id) {
                  navigate(`/pipelines/${p.pipeline_id}`);
                } else {
                  navigate(`/profiles/${p.profile_id}`);
                }
              }}
            />
          ))}
        </div>
      )}
    </section>
  );
}

// ──────────────────────────────────────────────────────────────────
// DemoCard, one card per researched company
// ──────────────────────────────────────────────────────────────────

function DemoCard({
  profile, plans, onClick,
}: {
  profile: ProfileSummary;
  plans: PlanSummary[];
  onClick: () => void;
}) {
  const status = deriveDemoStatus(profile, plans);
  const host = hostOf(profile.primary_url);
  const initial = (profile.company_name || host || "?").trim()[0]?.toUpperCase() ?? "?";

  // Stats row, cheap signals scanned at a glance. We surface the most
  // SE-meaningful: pain signals (what hurts), tech (what they run), and
  // plan count (demo variants prepared).
  const stats: { label: string; value: number | string }[] = [
    { label: "Pain",  value: profile.pain_signal_count },
    { label: "Tech",  value: profile.tech_signal_count },
    { label: "Plans", value: plans.length },
  ];

  return (
    <button
      type="button"
      onClick={onClick}
      className={cn(
        "text-left rounded-xl border p-4 transition-all group",
        "bg-[var(--color-canvas-elev1)] border-[var(--color-border)]",
        "hover:border-[color:var(--color-accent-border)] hover:bg-[var(--color-canvas-elev2)]/60",
        "focus-visible:border-[color:var(--color-accent-border)]",
      )}
    >
      <div className="flex items-start gap-3">
        {/* Initial bubble, gives each card a memorable visual handle
            without needing per-company logos. Tone tracks readiness. */}
        <span
          aria-hidden="true"
          className={cn(
            "inline-flex items-center justify-center w-10 h-10 rounded-lg shrink-0",
            "font-semibold text-sm",
            status.tone === "ready"      && "bg-[var(--color-success-bg)] text-[var(--color-success)] border border-[color:var(--color-success)]/30",
            status.tone === "in-review"  && "bg-[var(--color-accent-bg)] text-[var(--color-accent)] border border-[color:var(--color-accent-border)]",
            status.tone === "draft"      && "bg-[var(--color-canvas-elev2)] text-[var(--color-text-muted)] border border-[var(--color-border)]",
            status.tone === "researching" && "bg-[var(--color-info-bg)] text-[var(--color-info)] border border-[color:var(--color-info)]/30",
          )}
        >
          {status.tone === "researching"
            ? <Loader2 size={16} className="animate-spin" />
            : initial}
        </span>
        <div className="min-w-0 flex-1">
          <div className="text-sm font-medium text-[var(--color-text)] truncate">
            {profile.company_name ?? host ?? "Untitled profile"}
          </div>
          <div className="text-[11px] text-[var(--color-text-faint)] font-mono truncate mt-0.5">
            {host}
          </div>
        </div>
        <ChevronRight
          size={14}
          aria-hidden="true"
          className="shrink-0 text-[var(--color-text-faint)] opacity-0 group-hover:opacity-100 transition-opacity"
        />
      </div>

      {/* Status pill + stats */}
      <div className="mt-3 flex items-center gap-2 flex-wrap">
        <span
          className={cn(
            "inline-flex items-center gap-1 h-6 px-2 rounded-full text-[10px] font-mono uppercase tracking-wider border",
            status.tone === "ready"      && "border-[color:var(--color-success)]/40 bg-[var(--color-success-bg)] text-[var(--color-success)]",
            status.tone === "in-review"  && "border-[color:var(--color-accent-border)] bg-[var(--color-accent-bg)] text-[var(--color-accent)]",
            status.tone === "draft"      && "border-[var(--color-border)] bg-[var(--color-canvas)]/40 text-[var(--color-text-muted)]",
            status.tone === "researching" && "border-[color:var(--color-info)]/40 bg-[var(--color-info-bg)] text-[var(--color-info)]",
          )}
        >
          {status.label}
        </span>
        <span className="text-[11px] text-[var(--color-text-faint)] tabular-nums">
          {formatRelative(profile.created_at)}
        </span>
      </div>

      <div className="mt-3 pt-3 border-t border-[var(--color-border)] grid grid-cols-3 gap-2 text-center">
        {stats.map((s) => (
          <div key={s.label}>
            <div className="text-base font-medium tabular-nums text-[var(--color-text)]">
              {s.value}
            </div>
            <div className="text-[10px] uppercase tracking-wider text-[var(--color-text-faint)] mt-0.5">
              {s.label}
            </div>
          </div>
        ))}
      </div>
    </button>
  );
}

/** Derive a single demo-readiness status from a profile + its plans.
 *  Priority (most-actionable first):
 *    - "researching", pipeline still running, profile not finalized
 *    - "ready", at least one plan is provisioned (live in Cloud)
 *    - "in-review", at least one plan approved or awaiting review
 *    - "draft", profile exists, no usable plan yet
 */
function deriveDemoStatus(
  p: ProfileSummary,
  plans: PlanSummary[],
): { tone: "ready" | "in-review" | "draft" | "researching"; label: string } {
  if (p.pending) return { tone: "researching", label: "Researching" };
  const states = new Set(plans.map((pl) => pl.review_state));
  if (states.has("provisioned"))            return { tone: "ready", label: "Ready to demo" };
  if (states.has("approved_for_provision")) return { tone: "ready", label: "Approved" };
  if (states.has("se_reviewed"))            return { tone: "in-review", label: "In review" };
  if (plans.length > 0)                     return { tone: "in-review", label: "Drafted plan" };
  return { tone: "draft", label: "Just researched" };
}

function hostOf(url: string | null): string {
  if (!url) return "—";
  try { return new URL(url).host.replace(/^www\./, ""); }
  catch { return url; }
}

// ──────────────────────────────────────────────────────────────────
// OrphanCleanup, unchanged from v1, only shows when there's
// something orphaned. Kept above the KPI strip so it gets seen.
// ──────────────────────────────────────────────────────────────────

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
            but their plan is no longer in the DB. Most likely from a delete that didn&rsquo;t
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
            <span className="text-[var(--color-text-muted)]">&middot; plan {orphan.plan_id.slice(0, 8)}</span>
          )}
          <span className="text-[var(--color-warning)]">&middot; {orphan.reason}</span>
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

// ──────────────────────────────────────────────────────────────────
// Misc helpers
// ──────────────────────────────────────────────────────────────────

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
