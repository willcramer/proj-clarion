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
 *   4 KpiCards, Plans · Profiles · Events · KG nodes — each links to
 *     its canonical list page (Plans → /plans, etc.)
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
  History, Info, Terminal,
} from "lucide-react";
import { type ComponentType } from "react";

import {
  getDashboardSummary, listPlans, listProfiles, listOrphanFolders,
  deleteOrphanFolder,
  type OrphanFolder, type PlanSummary,
} from "@/lib/api";
import { Button } from "@/components/Button";
import { Card } from "@/components/Card";
import { HeroBuildCard, type BuildPreset } from "@/components/HeroBuildCard";
import { KpiCard } from "@/components/KpiCard";
import { LiveDemoCard } from "@/components/LiveDemoCard";
import { ProfileKpiCard } from "@/components/ProfileKpiCard";
import { usePipeline } from "@/lib/PipelineContext";

export function DashboardPage() {
  const pipeline = usePipeline();
  const summary = useQuery({ queryKey: ["dashboard"], queryFn: getDashboardSummary });
  const navigate = useNavigate();

  const s = summary.data;

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

      {/* 4-tile status strip. Each tile is a link to its canonical
          list page — Plans / Profiles → their list; Events → /audit
          where the demo-session + event history surfaces; KG nodes →
          /plans since KGs live inside plans. v1's drilldown panels
          (Plans by review-state, KG node/edge breakdown) were dropped
          because their value was just setting a URL filter that the
          destination pages already accept directly. */}
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
          onClick={() => navigate("/plans")}
        />
        <KpiCard
          icon={ScrollText}
          label="Profiles"
          value={fmtNum(s?.profiles_total)}
          tone="info"
          hint="researched companies"
          onClick={() => navigate("/profiles")}
        />
        <KpiCard
          icon={Database}
          label="Events"
          value={fmtNum(s?.business_events_total)}
          tone="success"
          hint="business events stored"
          onClick={() => navigate("/audit")}
        />
        <KpiCard
          icon={Network}
          label="KG nodes"
          value={fmtNum(s?.kg_nodes_total)}
          tone="info"
          hint={s ? `${fmtNum(s.kg_edges_total)} edges` : undefined}
          onClick={() => navigate("/plans")}
        />
      </div>

      <DemoLibrary />

      <SectionsGrid />
    </div>
  );
}

// ──────────────────────────────────────────────────────────────────
// SectionsGrid, the "jump to" footer row on the dashboard.
//
// Every top-level route gets a small link card here: Profiles · Plans
// · Builds · Runs · Audit · About. The topbar already has nav links
// for these, but the dashboard is where someone returns between
// sessions and wants a discoverable surface for "where do I look for
// X?". The cards stay quiet — small icon + label + one-line hint +
// reveal chevron — so they don't compete with the Demo library above.
// ──────────────────────────────────────────────────────────────────

interface SectionLink {
  to: string;
  label: string;
  hint: string;
  icon: ComponentType<{ size?: number; className?: string }>;
}

// Topbar nav already has Builds / Profiles / Plans, so the Jump-to
// row only surfaces routes that aren't otherwise visible. Keeps the
// home page from duplicating itself.
const SECTIONS: SectionLink[] = [
  { to: "/runs",  label: "Runs",  hint: "Runner tasks + logs",       icon: Terminal },
  { to: "/audit", label: "Audit", hint: "Plan + profile + demo log", icon: History },
  { to: "/about", label: "About", hint: "Architecture + pipeline",   icon: Info },
];

function SectionsGrid() {
  const navigate = useNavigate();
  return (
    <section aria-label="Jump to section">
      <div className="flex items-baseline justify-between mb-3">
        <h2 className="text-sm font-medium uppercase tracking-wider text-[var(--color-text-muted)]">
          Jump to
        </h2>
      </div>
      <div className="grid gap-2.5 sm:grid-cols-2 md:grid-cols-3">
        {SECTIONS.map(({ to, label, hint, icon: Icon }) => (
          <button
            key={to}
            type="button"
            onClick={() => navigate(to)}
            className={
              // Quieter than KPI tiles: no accent gradient, no bottom
              // bar. Just a clean tile with a smooth hover lift + icon
              // tint shift so each one signals "click me" without
              // shouting.
              "group relative isolate text-left rounded-xl border " +
              "border-[var(--color-border)] bg-[var(--color-canvas-elev1)] " +
              "p-3 flex items-center gap-3 shadow-[var(--shadow-sm)] " +
              "transition-[transform,box-shadow,border-color] duration-150 ease-out " +
              "hover:-translate-y-px hover:border-[var(--color-border-strong)] hover:shadow-[var(--shadow-md)] " +
              "focus-visible:outline-none focus-visible:border-[var(--color-accent-border)] focus-visible:ring-2 focus-visible:ring-[var(--color-accent)]/40 " +
              "cursor-pointer"
            }
          >
            <span
              aria-hidden="true"
              className="inline-flex items-center justify-center w-9 h-9 rounded-lg shrink-0 bg-[var(--color-canvas-elev2)] border border-[var(--color-border)] text-[var(--color-text-muted)] group-hover:text-[var(--color-accent)] group-hover:border-[color:var(--color-accent-border)] group-hover:bg-[var(--color-accent-bg)] transition-colors"
            >
              <Icon size={16} />
            </span>
            <div className="flex-1 min-w-0">
              <div className="text-sm font-medium text-[var(--color-text)] truncate">
                {label}
              </div>
              <div className="text-[11px] text-[var(--color-text-faint)] truncate">
                {hint}
              </div>
            </div>
            <ChevronRight
              size={14}
              aria-hidden="true"
              className="shrink-0 text-[var(--color-text-faint)] opacity-0 -translate-x-1 group-hover:opacity-100 group-hover:translate-x-0 transition-[opacity,transform] duration-150 ease-out"
            />
          </button>
        ))}
      </div>
    </section>
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
            <ProfileKpiCard
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

// The card component itself + its tone palette + status derivation +
// host parsing live in @/components/ProfileKpiCard so the Profiles list
// page can render the same tile. One source of truth for the visual
// language.

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
