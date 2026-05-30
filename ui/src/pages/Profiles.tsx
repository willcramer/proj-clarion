import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate, useParams, Link } from "react-router-dom";
import { useMemo, useState, type ReactNode } from "react";
import {
  ArrowLeft, Loader2, Trash2, Sparkles, Activity,
  ChevronRight, ClipboardList, Globe, Check, Bot,
} from "lucide-react";

import {
  listProfiles, getProfile, deleteProfile, listPipelines,
  listPlans, acceptProfileClaim,
  type PlanSummary,
} from "@/lib/api";
import { usePipeline } from "@/lib/PipelineContext";
import { AddProfileModal } from "@/components/AddProfileModal";
import { Card } from "@/components/Card";
import { Badge, reviewStateTone } from "@/components/Badge";
import { Button } from "@/components/Button";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { CrumbChip } from "@/components/CrumbChip";
import { DemoSessionCard } from "@/components/DemoSessionCard";
import { PlanTabs } from "@/components/plan/PlanTabs";
import { Pagination } from "@/components/Pagination";
import { ProfileKpiCard, deriveDemoStatus } from "@/components/ProfileKpiCard";
import { useAssistant } from "@/lib/AssistantContext";
import { cn } from "@/lib/cn";

// Refetch every 5s while looking at the list so in-flight pipeline
// placeholders keep their spinner alive and disappear when research
// lands. Cheap query, DB list of pipelines + profiles.
const PROFILES_REFETCH_MS = 5_000;

// ─── List page ────────────────────────────────────────────────────

export function ProfilesListPage() {
  const profiles = useQuery({
    queryKey: ["profiles"],
    queryFn: listProfiles,
    refetchInterval: PROFILES_REFETCH_MS,
  });
  // Plans drive each card's status tone + stats (Processes / KG nodes
  // when present, else Pain / Tech / Pending fall back). Same shape the
  // Dashboard's DemoLibrary uses; we group client-side to avoid an
  // N+1 fetch per card.
  const plansAll = useQuery({
    queryKey: ["plans"],
    queryFn: () => listPlans(),
    refetchInterval: 15_000,
  });
  const plansByProfile = useMemo(() => {
    const m = new Map<string, PlanSummary[]>();
    for (const p of plansAll.data ?? []) {
      const arr = m.get(p.source_profile_id) ?? [];
      arr.push(p);
      m.set(p.source_profile_id, arr);
    }
    return m;
  }, [plansAll.data]);

  const navigate = useNavigate();
  const pipeline = usePipeline();
  void pipeline;
  // First-class modal replaces the old window.prompt, picks up URL +
  // volume preset and starts the build directly (no /new bounce).
  const [addOpen, setAddOpen] = useState(false);

  // Newest-first ordering matches the SE mental model: "what I just
  // researched should be at the top, easy to find."
  const ordered = useMemo(
    () => [...(profiles.data ?? [])].sort(
      (a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime(),
    ),
    [profiles.data],
  );

  // Hybrid layout: top 6 newest as compact KPI cards (the "recent
  // highlights" surface), then the full list as a paginated table
  // (the canonical searchable surface). Cards give visual rhythm at
  // a glance; the table gives sortable column data + pagination for
  // when the library grows past a screen.
  const HIGHLIGHTS_LIMIT = 6;
  const highlights = ordered.slice(0, HIGHLIGHTS_LIMIT);
  const showTable = ordered.length > HIGHLIGHTS_LIMIT;

  // Pagination state for the table. Defaulting to 10 rows/page matches
  // the rest of the app; users can dial up to 50.
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);
  // Clamp page when row count changes (e.g. a delete drops us past the
  // last page). Without this you can see an empty table page.
  const totalPages = Math.max(1, Math.ceil(ordered.length / pageSize));
  const safePage = Math.min(page, totalPages);
  const pageRows = ordered.slice((safePage - 1) * pageSize, safePage * pageSize);

  function navigateToProfile(p: typeof ordered[number]) {
    if (p.pending && p.pipeline_id) {
      navigate(`/pipelines?p=${p.pipeline_id}`);
    } else {
      navigate(`/profiles/${p.profile_id}`);
    }
  }

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between gap-3 flex-wrap">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Profiles</h1>
          <p className="text-[var(--color-text-muted)] mt-1 text-sm">
            CompanyProfiles produced by the research agent.{" "}
            <span className="text-[var(--color-text-faint)] tabular-nums">
              {ordered.length} total
            </span>
          </p>
        </div>
        <Button
          variant="primary"
          size="sm"
          onClick={() => setAddOpen(true)}
          title="Research a new company URL, the resulting CompanyProfile lands here when research completes"
        >
          <Sparkles size={12} /> Add profile
        </Button>
      </div>

      <AddProfileModal
        open={addOpen}
        onClose={() => setAddOpen(false)}
        onSubmitted={() => {
          setAddOpen(false);
          // PipelineContext is now non-idle; /new auto-renders the
          // live PipelineRunView for the just-started build.
          navigate("/new");
        }}
      />

      {profiles.isLoading ? (
        <Card>
          <div className="p-8 text-center text-[var(--color-text-faint)]">Loading…</div>
        </Card>
      ) : ordered.length === 0 ? (
        <Card>
          <div className="p-12 text-center">
            <Sparkles size={28} className="text-[var(--color-text-faint)] mx-auto mb-3" />
            <div className="text-sm font-medium">No profiles yet</div>
            <div className="text-xs text-[var(--color-text-muted)] mt-1 max-w-sm mx-auto">
              Use <span className="font-mono text-[var(--color-accent)]">Add profile</span> above, or run{" "}
              <code className="font-mono text-xs">just research &lt;url&gt;</code> from the terminal.
            </div>
          </div>
        </Card>
      ) : (
        <>
          {/* Highlights — top 6 newest as compact KPI cards. */}
          <section aria-label="Recent profiles">
            <div className="flex items-baseline justify-between mb-3">
              <h2 className="text-sm font-medium uppercase tracking-wider text-[var(--color-text-muted)]">
                Recent
              </h2>
              <span className="text-[11px] text-[var(--color-text-faint)] font-mono tabular-nums">
                {highlights.length} of {ordered.length}
              </span>
            </div>
            <div className="grid gap-2.5 sm:grid-cols-2 lg:grid-cols-3">
              {highlights.map((p) => (
                <ProfileKpiCard
                  key={p.profile_id}
                  profile={p}
                  plans={plansByProfile.get(p.profile_id) ?? []}
                  compact
                  onClick={() => navigateToProfile(p)}
                />
              ))}
            </div>
          </section>

          {/* Full list — table + pagination. Only renders when there are
              more profiles than the highlights grid surfaces, so small
              libraries don't see a redundant 4-row table. */}
          {showTable && (
            <section aria-label="All profiles">
              <div className="flex items-baseline justify-between mb-3">
                <h2 className="text-sm font-medium uppercase tracking-wider text-[var(--color-text-muted)]">
                  All profiles
                </h2>
                <span className="text-[11px] text-[var(--color-text-faint)] font-mono tabular-nums">
                  {ordered.length} total
                </span>
              </div>
              <Card className="p-0 overflow-hidden">
                <table className="w-full text-sm">
                  <thead className="text-xs text-[var(--color-text-faint)] uppercase tracking-wider border-b border-[var(--color-border)]">
                    <tr>
                      <th className="text-left font-medium px-4 py-3">Company</th>
                      <th className="text-left font-medium px-4 py-3">Source</th>
                      <th className="text-left font-medium px-4 py-3">Status</th>
                      <th className="text-right font-medium px-4 py-3">Pain</th>
                      <th className="text-right font-medium px-4 py-3">Tech</th>
                      <th className="text-right font-medium px-4 py-3">Synth</th>
                    </tr>
                  </thead>
                  <tbody>
                    {pageRows.map((p) => {
                      const status = deriveDemoStatus(p, plansByProfile.get(p.profile_id) ?? []);
                      return (
                        <tr
                          key={p.profile_id}
                          onClick={() => navigateToProfile(p)}
                          className={cn(
                            "border-b border-[var(--color-border)] last:border-0 cursor-pointer transition-colors",
                            p.pending
                              ? "bg-[var(--color-info)]/5 hover:bg-[var(--color-info)]/10"
                              : "hover:bg-white/[0.02]",
                          )}
                        >
                          <td className="px-4 py-3">
                            {p.pending ? (
                              <span className="inline-flex items-center gap-1.5 text-[var(--color-info)]">
                                <Loader2 size={11} className="animate-spin" />
                                researching…
                              </span>
                            ) : (
                              <span className="font-medium">
                                {p.company_name ?? (
                                  <span className="text-[var(--color-text-faint)]">—</span>
                                )}
                              </span>
                            )}
                          </td>
                          <td className="px-4 py-3 text-xs text-[var(--color-text-muted)] truncate max-w-[280px] font-mono">
                            {hostOfUrl(p.primary_url)}
                          </td>
                          <td className="px-4 py-3">
                            <StatusChip status={status} />
                          </td>
                          <td className="px-4 py-3 text-right tabular-nums">
                            {p.pending ? <span className="text-[var(--color-text-faint)]">—</span> : p.pain_signal_count}
                          </td>
                          <td className="px-4 py-3 text-right tabular-nums">
                            {p.pending ? <span className="text-[var(--color-text-faint)]">—</span> : p.tech_signal_count}
                          </td>
                          <td className="px-4 py-3 text-right tabular-nums">
                            {p.pending ? (
                              <span className="text-[var(--color-text-faint)]">—</span>
                            ) : p.synthesized_flag_count > 0 ? (
                              <Badge tone="warning">{p.synthesized_flag_count}</Badge>
                            ) : (
                              <span className="text-[var(--color-text-faint)]">0</span>
                            )}
                          </td>
                        </tr>
                      );
                    })}
                  </tbody>
                </table>
                <Pagination
                  page={safePage}
                  pageSize={pageSize}
                  total={ordered.length}
                  onPageChange={setPage}
                  onPageSizeChange={(n) => { setPageSize(n); setPage(1); }}
                />
              </Card>
            </section>
          )}
        </>
      )}
    </div>
  );
}

// Inline status chip for table rows. Uses the same tone palette as
// ProfileKpiCard but renders as a flat pill (no card chrome) since
// the table doesn't have per-row accent. Tone strings stay in sync
// with deriveDemoStatus.
function StatusChip({ status }: { status: { tone: string; label: string } }) {
  const cls = {
    ready:       "border-[color:var(--color-accent)]/40 bg-[var(--color-accent-bg)] text-[var(--color-accent)]",
    "in-review": "border-[color:var(--color-warning)]/40 bg-[var(--color-warning-bg)] text-[var(--color-warning)]",
    draft:       "border-[var(--color-border)] bg-[var(--color-canvas-elev2)] text-[var(--color-text-muted)]",
    researching: "border-[color:var(--color-info)]/40 bg-[var(--color-info-bg)] text-[var(--color-info)]",
  }[status.tone] ?? "border-[var(--color-border)] text-[var(--color-text-muted)]";
  return (
    <span
      className={cn(
        "inline-flex items-center h-5 px-2 rounded-full text-[10px] font-mono uppercase tracking-wider border",
        cls,
      )}
    >
      {status.label}
    </span>
  );
}

// ─── Detail page with research-extension chat ────────────────────

export function ProfileDetailPage() {
  const { profileId = "" } = useParams<{ profileId: string }>();
  const navigate = useNavigate();
  const qc = useQueryClient();
  const pipeline = usePipeline();
  const profile = useQuery({
    queryKey: ["profile", profileId],
    queryFn: () => getProfile(profileId),
    enabled: !!profileId,
  });
  // All pipelines, filtered client-side for this profile. Cheap because
  // pipelines list is bounded (~200 rows). Newest-first ordering already
  // matches the API; .find() picks the most recent build for this profile.
  const pipelinesQ = useQuery({
    queryKey: ["pipelines"],
    queryFn: listPipelines,
    refetchInterval: 10_000,
  });
  const profilePipelines = (pipelinesQ.data ?? []).filter(
    (p) => p.profile_id === profileId,
  );
  const latestPipeline = profilePipelines[0];

  // Plans for this profile — drives the header's "View plan" affordance
  // so the SE can jump straight from a profile into the plan it produced.
  // Two-level fanout: one plan -> direct link; many plans -> filtered list.
  const profilePlansQ = useQuery({
    queryKey: ["plans-by-profile", profileId],
    queryFn: () => listPlans({ source_profile_id: profileId }),
    enabled: !!profileId,
    refetchInterval: 30_000,
  });
  const profilePlans = (profilePlansQ.data ?? []).filter((p) => !p.pending);

  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [building, setBuilding] = useState(false);

  const deleteMut = useMutation({
    mutationFn: (cleanupCloud: boolean) => deleteProfile(profileId, cleanupCloud),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["profiles"] });
      qc.invalidateQueries({ queryKey: ["plans"] });
      qc.invalidateQueries({ queryKey: ["dashboard"] });
      navigate("/profiles");
    },
    onError: (e: Error) => setDeleteError(e.message),
  });

  // Pull the source URL out of the profile JSON so a "Build from profile"
  // call can attach it to the new pipeline row. The shape is
  // CompanyProfile (Pydantic), typed `unknown` on the wire so we narrow.
  function profileUrl(): string | undefined {
    const data = profile.data as { company?: { primary_url?: string } } | undefined;
    return data?.company?.primary_url;
  }

  async function buildFromProfile() {
    // Start a NEW build that skips research (we already have this profile)
    // and goes straight to plan. Smart resume's not the right tool, that's
    // for resuming an existing pipeline. Here the user wants a fresh
    // pipeline pinned to this profile.
    if (typeof pipeline.startFromPhase !== "function") {
      window.alert(
        "Pipeline context isn't ready (likely a stale tab). Hard-refresh and try again.",
      );
      return;
    }
    const url = profileUrl();
    if (!url) {
      window.alert("This profile doesn't have a primary URL, can't start a build.");
      return;
    }
    setBuilding(true);
    try {
      await pipeline.startFromPhase({
        phase: "plan",
        url,
        profile_id: profileId,
        // No parent_pipeline_id, this is a fresh build, not a resume.
      });
      navigate("/new");
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      window.alert(`Couldn't start build:\n\n${msg}`);
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

  const data = profile.data as ProfileShape | undefined;

  return (
    <div className="space-y-6">
      {/* Back link, own row, matches the Plan detail page rhythm. */}
      <Link to="/profiles" className="text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text)] inline-flex items-center gap-1">
        <ArrowLeft size={14} /> Profiles
      </Link>

      {deleteError && (
        <Card className="p-3 text-xs text-[var(--color-danger)] border-[var(--color-danger)]/30 bg-[var(--color-danger)]/5">
          {deleteError}
        </Card>
      )}

      {profile.isLoading ? (
        <div className="text-[var(--color-text-faint)]">Loading…</div>
      ) : !data ? (
        <div className="text-[var(--color-danger)]">Profile not found.</div>
      ) : (
        <ProfileDetailBody
          profileId={profileId}
          data={data}
          profilePlans={profilePlans}
          latestPipeline={latestPipeline}
          building={building}
          onBuildFromProfile={() => void buildFromProfile()}
          onViewLatestBuild={() => void viewLatestBuild()}
          onDelete={() => setConfirmDelete(true)}
        />
      )}

      <ConfirmDialog
        open={confirmDelete}
        title="Delete this profile?"
        body={
          <div className="space-y-2">
            <p>Removes the profile from Postgres. <strong>Cascades to every plan
              that references it</strong>, those plans, their KG, events, and audit
              history are deleted too.</p>
            <p className="text-xs text-[var(--color-text-faint)]">
              Mimir/Loki/Tempo time-series for the cascaded plans stay (~30d retention).
              Cloud KG entity records fade as their emitters stop.
            </p>
          </div>
        }
        extras={[{
          id: "cleanup_cloud",
          label: "Also remove dashboards + alerts from Grafana Cloud (every cascaded plan)",
          hint: <>Runs <code className="font-mono">provision clear</code> for each plan before the DB delete.</>,
          defaultChecked: true,
        }]}
        confirmLabel="Yes, delete profile + all its plans"
        onConfirm={(toggles) => {
          setConfirmDelete(false);
          deleteMut.mutate(!!toggles.cleanup_cloud);
        }}
        onCancel={() => setConfirmDelete(false)}
      />
    </div>
  );
}

// ─── Human-readable profile view ───────────────────────────────────

/** Loose typing for the CompanyProfile JSON we get from the API. We don't
 *  want a schema dependency on the front end (the Pydantic source-of-truth
 *  evolves often) so we narrow only the fields we actually render and let
 *  the rest pass through into the collapsible JSON view. */
interface ProfileShape {
  profile_id?: string;
  fetched_at?: string;
  /** Mirrors `CompanyIdentity` in `schemas/company_profile.py`. Keep this
   *  interface in sync — the v1 version of this type had a phantom
   *  `headquarters` field that doesn't exist on the schema (real fields
   *  are `headquarters_city` + `headquarters_country`) and a phantom
   *  `description` field. Snapshot/Header rendering silently fell back
   *  to empty for those because nothing on the wire ever matched. */
  company?: {
    name?: string;
    legal_name?: string;
    primary_url?: string;
    headquarters_city?: string;
    headquarters_country?: string;
    founded_year?: number;
    ownership_type?: string;
    employee_count_estimate?: number;
  };
  industry_taxonomy?: {
    primary_industry?: string;
    business_model?: string;
    sub_industries?: string[];
  };
  geographic_footprint?: {
    countries?: string[];
    regions?: string[];
    flagship_locations?: string[];
  };
  channels?: Array<{
    channel_id?: string;
    channel_type?: string;
    name?: string;
    description?: string;
    citations?: string[];
  }>;
  business_entity_candidates?: Array<{
    entity_type?: string;
    name?: string;
    description?: string;
    citations?: string[];
  }>;
  recent_strategic_priorities?: Array<{
    priority?: string;
    citations?: string[];
  }>;
  pain_signals?: Array<{
    pain?: string;
    severity?: string;
    citations?: string[];
  }>;
  tech_stack_signals?: Array<{
    component_type?: string;
    vendor_or_product?: string;
    confidence?: string;
    citations?: string[];
  }>;
  synthesized_flags?: Array<{
    field_path?: string;
    claim?: string;
    rationale?: string;
  }>;
}

// ─── ProfileDetailBody, the v2 page layout ─────────────────────────
//
// Mirrors PlanDetailBody (Plans.tsx): header (eyebrow + h1 + narrative
// + crumb chips + action buttons), 2-col hero (Snapshot + Stats), and
// a sticky PlanTabs strip with one tab panel rendered at a time.
//
// Replaces the v1 "single long scroll" ProfileSummaryView. Tabs:
//   overview · pain · tech · claims (if any) · related · extend · raw
// ───────────────────────────────────────────────────────────────────

type ProfileTabId =
  | "overview" | "pain" | "tech" | "claims"
  | "related"  | "extend" | "raw";

function ProfileDetailBody({
  profileId, data, profilePlans, latestPipeline,
  building, onBuildFromProfile, onViewLatestBuild, onDelete,
}: {
  profileId: string;
  data: ProfileShape;
  profilePlans: Array<{ plan_id: string; plan_id_short: string; review_state: string }>;
  latestPipeline: { pipeline_id: string; status: string } | undefined;
  building: boolean;
  onBuildFromProfile: () => void;
  onViewLatestBuild: () => void;
  onDelete: () => void;
}) {
  const navigate = useNavigate();
  const [activeTab, setActiveTab] = useState<ProfileTabId>("overview");

  // Counts that drive both the hero stats card and the tab pill labels.
  const channelCount  = data.channels?.length ?? 0;
  const entityCount   = data.business_entity_candidates?.length ?? 0;
  const priorityCount = data.recent_strategic_priorities?.length ?? 0;
  const painCount     = data.pain_signals?.length ?? 0;
  const techCount     = data.tech_stack_signals?.length ?? 0;
  const claimsCount   = data.synthesized_flags?.length ?? 0;

  // Tab strip. Claims tab only surfaces when there's something to review
  // (the warning-tone tab pill is loud; suppress it on clean profiles).
  const tabs = useMemo(() => {
    const overviewCount = channelCount + entityCount + priorityCount;
    const list: { id: ProfileTabId; label: string; count?: number }[] = [
      { id: "overview", label: "Overview",       count: overviewCount },
      { id: "pain",     label: "Pain signals",   count: painCount },
      { id: "tech",     label: "Tech stack",     count: techCount },
    ];
    if (claimsCount > 0) {
      list.push({ id: "claims", label: "Claims to review", count: claimsCount });
    }
    list.push(
      { id: "related", label: "Related" },
      { id: "extend",  label: "Extend research" },
      { id: "raw",     label: "Raw JSON" },
    );
    return list;
  }, [channelCount, entityCount, priorityCount, painCount, techCount, claimsCount]);

  return (
    <div className="space-y-6">
      <ProfileHeader
        profileId={profileId}
        data={data}
        profilePlans={profilePlans}
        latestPipeline={latestPipeline}
        building={building}
        onBuildFromProfile={onBuildFromProfile}
        onViewLatestBuild={onViewLatestBuild}
        onDelete={onDelete}
        onJumpToPlan={(planId) => navigate(`/plans/${planId}`)}
        onJumpToPlans={() => navigate(`/plans?profile=${encodeURIComponent(profileId)}`)}
      />

      {/* Hero grid: Company snapshot (1.4fr) + right column with
          DemoSessionCard + Profile stats (1fr). Matches PlanDetailBody
          shape so the SE sees the same demo-control surface whether
          they're on a profile or a plan. The DemoSessionCard binds to
          the most recent plan from this profile — most profiles have
          one plan; when there are multiple, a small annotation marks
          which one the controls are wired to. */}
      <div className="grid gap-5 xl:grid-cols-[1.4fr_1fr] items-start">
        <CompanySnapshotPanel data={data} />
        <div className="space-y-5">
          {profilePlans.length > 0 && (
            <div>
              {profilePlans.length > 1 && (
                <div className="mb-2 text-[10px] font-mono uppercase tracking-wider text-[var(--color-text-faint)]">
                  Demo controls · plan{" "}
                  <Link
                    to={`/plans/${profilePlans[0].plan_id}`}
                    className="text-[var(--color-text-muted)] hover:text-[var(--color-accent)] underline-offset-2 hover:underline"
                  >
                    {profilePlans[0].plan_id_short}
                  </Link>
                  <span className="ml-1 normal-case tracking-normal">
                    (most recent of {profilePlans.length})
                  </span>
                </div>
              )}
              <DemoSessionCard planId={profilePlans[0].plan_id} />
            </div>
          )}
          <ProfileContentsStats
            channels={channelCount}
            entities={entityCount}
            pain={painCount}
            tech={techCount}
          />
        </div>
      </div>

      <PlanTabs
        tabs={tabs}
        activeId={activeTab}
        onChange={(id) => setActiveTab(id as ProfileTabId)}
      />

      <div role="tabpanel" aria-label={activeTab}>
        {activeTab === "overview" && <OverviewTab data={data} />}
        {activeTab === "pain"     && <PainSignalsTab data={data} />}
        {activeTab === "tech"     && <TechStackTab data={data} />}
        {activeTab === "claims"   && <ClaimsTab profileId={profileId} data={data} />}
        {activeTab === "related"  && <RelatedProfileContent profileId={profileId} />}
        {activeTab === "extend"   && (
          <ExtendWithAssistantPanel profileId={profileId} />
        )}
        {activeTab === "raw"      && <RawJsonTab data={data} />}
      </div>
    </div>
  );
}

// ─── ProfileHeader, title block + action bar ───────────────────────
//
// Same shape as PlanHeader: eyebrow caps + 32px h1 + narrative + crumb
// chips on the left, action buttons on the right. The crumb chips are
// the same CrumbChip primitive used on the Plan + Pipeline pages so
// the "navigate between resources" affordance reads consistently.

function ProfileHeader({
  profileId, data, profilePlans, latestPipeline,
  building, onBuildFromProfile, onViewLatestBuild, onDelete,
  onJumpToPlan, onJumpToPlans,
}: {
  profileId: string;
  data: ProfileShape;
  profilePlans: Array<{ plan_id: string; plan_id_short: string; review_state: string }>;
  latestPipeline: { pipeline_id: string; status: string } | undefined;
  building: boolean;
  onBuildFromProfile: () => void;
  onViewLatestBuild: () => void;
  onDelete: () => void;
  onJumpToPlan: (planId: string) => void;
  onJumpToPlans: () => void;
}) {
  const assistant = useAssistant();
  const co  = data.company ?? {};
  const tax = data.industry_taxonomy ?? {};
  const companyName = co.name ?? "Unnamed company";

  // The schema has no `company.description` field, so the v1 description
  // line was always empty. Synthesize a one-liner from the structured
  // identity fields instead: "<industry> · <ownership> · founded <year>
  // · ~<headcount> employees". Whichever fields are present render;
  // missing ones drop out.
  const blurbParts: string[] = [];
  if (tax.primary_industry) blurbParts.push(tax.primary_industry);
  if (co.ownership_type)    blurbParts.push(co.ownership_type);
  if (co.founded_year)      blurbParts.push(`founded ${co.founded_year}`);
  if (co.employee_count_estimate) {
    blurbParts.push(`~${formatHeadcount(co.employee_count_estimate)} employees`);
  }
  const blurb = blurbParts.join(" · ");

  return (
    <div className="flex items-start gap-6 flex-wrap">
      <div className="flex-1 min-w-[280px]">
        <div className="text-[11px] font-mono uppercase tracking-[0.08em] text-[var(--color-text-faint)]">
          Profile
          <span className="ml-2 text-[var(--color-text-faint)]">
            {profileId}
          </span>
        </div>
        <h1 className="mt-1 text-[32px] font-medium tracking-tight leading-tight text-[var(--color-text)]">
          {companyName}.
        </h1>
        {blurb && (
          <p className="mt-3 text-[var(--color-text-muted)] text-[15px] leading-relaxed max-w-2xl">
            {blurb}
            {co.legal_name && co.legal_name !== co.name && (
              <span className="text-[var(--color-text-faint)]"> · legal name {co.legal_name}</span>
            )}
          </p>
        )}

        {/* Crumb chips: source URL (external) + primary industry +
            business model. Each chip reads as a real "click me"
            button; the URL one opens the company in a new tab, the
            others stay decorative for now (no by-industry view yet). */}
        <div className="mt-4 flex items-center gap-2 flex-wrap">
          {co.primary_url && (
            <CrumbChip
              to={co.primary_url}
              label="website"
              value={hostOfUrl(co.primary_url)}
              icon={Globe}
              external
              title="Open the company website in a new tab"
            />
          )}
          {tax.primary_industry && (
            <span
              className="inline-flex items-center gap-1.5 h-7 px-2.5 rounded-md font-mono bg-[var(--color-canvas-elev1)] border border-[var(--color-border)]"
              title="Primary industry classification (research agent)"
            >
              <span className="uppercase tracking-[0.06em] text-[10px] text-[var(--color-text-faint)]">
                industry
              </span>
              <span className="text-[11px] text-[var(--color-text)]">
                {tax.primary_industry}
              </span>
            </span>
          )}
          {tax.business_model && (
            <Badge tone="accent">{tax.business_model}</Badge>
          )}
        </div>
      </div>

      {/* Action bar, right-aligned. Wraps under the title on narrow
          widths via the parent's `flex-wrap`. */}
      <div className="flex items-center gap-2 flex-wrap">
        {profilePlans.length === 1 && (
          <Button
            variant="secondary"
            size="sm"
            onClick={() => onJumpToPlan(profilePlans[0].plan_id)}
            title={`Open plan ${profilePlans[0].plan_id_short} (${profilePlans[0].review_state})`}
          >
            <ClipboardList size={12} /> View plan
            <span className="ml-1 text-[10px] text-[var(--color-text-faint)]">
              {profilePlans[0].review_state.replace(/_/g, " ")}
            </span>
          </Button>
        )}
        {profilePlans.length > 1 && (
          <Button
            variant="secondary"
            size="sm"
            onClick={onJumpToPlans}
            title={`This profile has ${profilePlans.length} plans. Open the Plans list filtered to this profile.`}
          >
            <ClipboardList size={12} /> View plans
            <span className="ml-1 text-[10px] text-[var(--color-text-faint)] font-mono tabular-nums">
              {profilePlans.length}
            </span>
          </Button>
        )}
        {latestPipeline && (
          <Button
            variant="secondary"
            size="sm"
            onClick={onViewLatestBuild}
            title={`Open the latest build for this profile (${latestPipeline.pipeline_id} · ${latestPipeline.status})`}
          >
            <Activity size={12} /> View latest build
            <span className="ml-1 text-[10px] text-[var(--color-text-faint)]">
              {latestPipeline.status}
            </span>
          </Button>
        )}
        {/* Entry into the Clarion assistant, scoped to this profile.
            The assistant extends the profile's research, refines plans,
            runs builds, and drives demos — the conversational path that
            replaced the old in-page "Extend research" chat. */}
        <Button
          variant="secondary"
          size="sm"
          onClick={() => assistant.openAssistant({ scope: { profile_id: profileId } })}
          title="Open the Clarion assistant scoped to this profile (⌘J)."
        >
          <Bot size={12} /> Refine with assistant
        </Button>
        <Button
          variant="primary"
          size="sm"
          onClick={onBuildFromProfile}
          disabled={building}
          title="Start a new pipeline that skips research (uses this profile) and goes straight to plan → … → kg-publish"
        >
          {building ? (
            <Loader2 size={12} className="animate-spin" />
          ) : (
            <Sparkles size={12} />
          )}
          Build demo
        </Button>
        <Button variant="danger" size="sm" onClick={onDelete}>
          <Trash2 size={12} /> Delete
        </Button>
      </div>
    </div>
  );
}

// ─── Company snapshot, hero LEFT card ──────────────────────────────
//
// Mirrors KnowledgeGraphPanel's role on the Plan page: the dominant
// "what is this thing" card on the left half of the hero grid. Field
// grid + countries footer; HQ / Founded / Footprint / Headcount sit
// where the entity-type chips do on the plan page.

function CompanySnapshotPanel({ data }: { data: ProfileShape }) {
  const co  = data.company ?? {};
  const tax = data.industry_taxonomy ?? {};
  const geo = data.geographic_footprint ?? {};
  const countries = geo.countries ?? [];
  const regions   = geo.regions ?? [];

  // Compose the HQ cell from city + country. Either field may be
  // present alone (e.g. global HQ where only "United States" is known)
  // so render whichever we have.
  const hqParts: string[] = [];
  if (co.headquarters_city)    hqParts.push(co.headquarters_city);
  if (co.headquarters_country) hqParts.push(co.headquarters_country);
  const hq = hqParts.join(", ") || undefined;

  return (
    <Card className="p-5">
      <div className="flex items-center justify-between gap-3 mb-4">
        <h3 className="text-sm font-medium text-[var(--color-text)] m-0">
          Company snapshot
        </h3>
        <span className="font-mono text-[11px] text-[var(--color-text-faint)] tabular-nums">
          {countries.length} countries
          {regions.length > 0 && ` · ${regions.length} regions`}
        </span>
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-xs">
        <Field label="Industry" value={tax.primary_industry} />
        <Field label="HQ"       value={hq} />
        <Field label="Founded"  value={co.founded_year?.toString()} />
        <Field
          label="Footprint"
          value={countries.slice(0, 3).join(", ") || undefined}
          extra={countries.length > 3 ? `+${countries.length - 3}` : undefined}
        />
      </div>

      {/* Second row, only when one of these is populated. Keeps the
          snapshot focused on the four hero fields up top, with optional
          enrichment below. */}
      {(co.ownership_type || co.employee_count_estimate) && (
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4 text-xs mt-4 pt-4 border-t border-[var(--color-border)]">
          <Field label="Ownership" value={co.ownership_type} />
          <Field
            label="Headcount"
            value={co.employee_count_estimate ? formatHeadcount(co.employee_count_estimate) : undefined}
            extra={co.employee_count_estimate ? "approx." : undefined}
          />
          <Field label="Business model" value={tax.business_model} />
        </div>
      )}

      {/* Sub-industries chip row, only when present. Keeps the panel
          dense without forcing a tab navigation to find them. */}
      {tax.sub_industries && tax.sub_industries.length > 0 && (
        <div className="mt-4 pt-4 border-t border-[var(--color-border)]">
          <div className="text-[10px] uppercase tracking-wider text-[var(--color-text-faint)] mb-2">
            Sub-industries
          </div>
          <div className="flex flex-wrap gap-1.5">
            {tax.sub_industries.map((s, i) => (
              <span
                key={i}
                className="inline-flex items-center px-2 h-6 rounded-md text-[11px] font-mono bg-[var(--color-canvas-elev2)]/60 border border-[var(--color-border)] text-[var(--color-text-muted)]"
              >
                {s}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* Flagship locations footer, mirrors the KG legend's role on
          the Plan page (small inline pills under the field grid). */}
      {geo.flagship_locations && geo.flagship_locations.length > 0 && (
        <div className="flex flex-wrap gap-3 mt-4 text-[11px] text-[var(--color-text-muted)]">
          {geo.flagship_locations.slice(0, 4).map((loc, i) => (
            <span key={i} className="inline-flex items-center gap-1.5">
              <i className="inline-block w-1.5 h-1.5 rounded-full bg-[var(--color-accent)]" />
              {loc}
            </span>
          ))}
        </div>
      )}
    </Card>
  );
}

// ─── Profile contents stats, hero RIGHT card ───────────────────────
//
// Mirrors PlanContentsStats: a 4-cell counts grid. The four numbers
// the SE eyes look for to decide "is this profile rich enough to
// build a plan from yet". Channels / Entities / Pain / Tech.

function ProfileContentsStats({
  channels, entities, pain, tech,
}: {
  channels: number; entities: number; pain: number; tech: number;
}) {
  const stats: { label: string; value: number }[] = [
    { label: "Channels",     value: channels },
    { label: "Entities",     value: entities },
    { label: "Pain signals", value: pain     },
    { label: "Tech stack",   value: tech     },
  ];
  return (
    <Card className="p-5">
      <h3 className="text-sm font-medium text-[var(--color-text)] m-0 mb-3">
        Profile contents
      </h3>
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        {stats.map((s) => (
          <div key={s.label}>
            <div className="text-[10px] font-mono uppercase tracking-[0.06em] text-[var(--color-text-faint)]">
              {s.label}
            </div>
            <div className="mt-1 text-[22px] font-medium tabular-nums text-[var(--color-text)]">
              {s.value.toLocaleString()}
            </div>
          </div>
        ))}
      </div>
    </Card>
  );
}

// ─── Tab bodies ────────────────────────────────────────────────────
//
// Each body is a tiny composition of SectionCards; the heavy lifting
// (data shaping, badges) stayed inline since we're not reusing these
// outside the Profile page.

function OverviewTab({ data }: { data: ProfileShape }) {
  return (
    <div className="space-y-4">
      {data.channels && data.channels.length > 0 && (
        <SectionCard title="Channels" count={data.channels.length}>
          <ul className="space-y-2.5">
            {data.channels.map((ch, i) => (
              <li key={i} className="text-sm">
                <div className="flex items-center gap-2">
                  <span className="font-medium">{ch.name ?? ch.channel_id ?? "—"}</span>
                  {ch.channel_type && (
                    <Badge tone="neutral">{ch.channel_type}</Badge>
                  )}
                </div>
                {ch.description && (
                  <div className="text-xs text-[var(--color-text-muted)] mt-0.5 leading-relaxed">
                    {ch.description}
                  </div>
                )}
              </li>
            ))}
          </ul>
        </SectionCard>
      )}

      {data.business_entity_candidates && data.business_entity_candidates.length > 0 && (
        <SectionCard title="Business entities" count={data.business_entity_candidates.length}>
          <ul className="grid grid-cols-1 md:grid-cols-2 gap-x-4 gap-y-2 text-sm">
            {data.business_entity_candidates.map((e, i) => (
              <li key={i}>
                <div className="flex items-center gap-2">
                  <span className="font-medium truncate">{e.name ?? "—"}</span>
                  {e.entity_type && (
                    <Badge tone="neutral">{e.entity_type}</Badge>
                  )}
                </div>
                {e.description && (
                  <div className="text-xs text-[var(--color-text-muted)] mt-0.5 truncate">
                    {e.description}
                  </div>
                )}
              </li>
            ))}
          </ul>
        </SectionCard>
      )}

      {data.recent_strategic_priorities && data.recent_strategic_priorities.length > 0 && (
        <SectionCard title="Strategic priorities" count={data.recent_strategic_priorities.length}>
          <ul className="space-y-2 text-sm">
            {data.recent_strategic_priorities.map((p, i) => (
              <li key={i} className="text-[var(--color-text-muted)] leading-relaxed">
                <span className="text-[var(--color-text-faint)] mr-2">▸</span>
                {p.priority}
              </li>
            ))}
          </ul>
        </SectionCard>
      )}

      {/* Empty-state, so a profile that landed without channels /
          entities / priorities doesn't render a blank tab. */}
      {!(data.channels?.length) &&
       !(data.business_entity_candidates?.length) &&
       !(data.recent_strategic_priorities?.length) && (
        <Card className="p-8 text-center text-[var(--color-text-faint)] text-sm">
          No channels, entities, or strategic priorities on this profile yet.
          Use the <span className="font-mono">Extend research</span> tab to add some.
        </Card>
      )}
    </div>
  );
}

function PainSignalsTab({ data }: { data: ProfileShape }) {
  const rows = data.pain_signals ?? [];
  if (rows.length === 0) {
    return (
      <Card className="p-8 text-center text-[var(--color-text-faint)] text-sm">
        No pain signals on this profile yet.
      </Card>
    );
  }
  return (
    <SectionCard title="Pain signals" count={rows.length}>
      <ul className="space-y-2.5">
        {rows.map((p, i) => (
          <li key={i} className="text-sm flex items-start gap-2">
            <Badge
              tone={
                p.severity === "high" ? "danger"
                : p.severity === "medium" ? "warning"
                : "neutral"
              }
              className="shrink-0 mt-0.5"
            >
              {p.severity ?? "?"}
            </Badge>
            <span className="text-[var(--color-text-muted)] leading-relaxed">
              {p.pain}
            </span>
          </li>
        ))}
      </ul>
    </SectionCard>
  );
}

function TechStackTab({ data }: { data: ProfileShape }) {
  const rows = data.tech_stack_signals ?? [];
  if (rows.length === 0) {
    return (
      <Card className="p-8 text-center text-[var(--color-text-faint)] text-sm">
        No tech stack signals on this profile yet.
      </Card>
    );
  }
  return (
    <SectionCard title="Tech stack" count={rows.length}>
      <ul className="grid grid-cols-1 md:grid-cols-2 gap-x-4 gap-y-2 text-sm">
        {rows.map((t, i) => (
          <li key={i} className="flex items-center gap-2">
            <Badge tone={t.confidence === "high" ? "success" : t.confidence === "medium" ? "info" : "neutral"}>
              {t.component_type}
            </Badge>
            <span className="font-medium truncate">{t.vendor_or_product}</span>
          </li>
        ))}
      </ul>
    </SectionCard>
  );
}

/** Per-claim accept action: drops the entry from `synthesized_flags`
 *  (value stays in the profile). On success we invalidate the profile
 *  + audit + summary queries so the Claims tab pill and the global
 *  Audit page update without a page refresh. */
function ClaimsTab({
  profileId, data,
}: {
  profileId: string;
  data: ProfileShape;
}) {
  const qc = useQueryClient();
  const rows = data.synthesized_flags ?? [];
  // Track which row is currently being accepted so we can disable just
  // its button (not all of them) while the request is in flight.
  const [busyPath, setBusyPath] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const acceptMut = useMutation({
    mutationFn: (field_path: string) => {
      setBusyPath(field_path);
      setError(null);
      return acceptProfileClaim(profileId, field_path, "accept");
    },
    onSuccess: () => {
      // Profile detail re-renders with one fewer flag; audit page picks
      // up the row; profile summary's synthesized_flag_count refreshes.
      qc.invalidateQueries({ queryKey: ["profile", profileId] });
      qc.invalidateQueries({ queryKey: ["profiles"] });
      qc.invalidateQueries({ queryKey: ["profile-audit", profileId] });
      qc.invalidateQueries({ queryKey: ["profile-audit-global"] });
      setBusyPath(null);
    },
    onError: (e: Error) => {
      setError(e.message);
      setBusyPath(null);
    },
  });

  if (rows.length === 0) {
    return (
      <Card className="p-8 text-center text-[var(--color-text-faint)] text-sm">
        No synthesized claims to review.
      </Card>
    );
  }

  return (
    <SectionCard
      title="Synthesized claims (review)"
      count={rows.length}
      tone="warning"
    >
      {error && (
        <div className="mb-3 px-3 py-2 rounded-md text-xs border border-[var(--color-danger)]/30 bg-[var(--color-danger)]/10 text-[var(--color-danger)]">
          {error}
        </div>
      )}
      <ul className="divide-y divide-[var(--color-border)]">
        {rows.map((f, i) => {
          const path = f.field_path ?? "";
          const busy = busyPath === path;
          return (
            <li
              key={`${path}-${i}`}
              className="py-3 first:pt-0 last:pb-0 flex items-start gap-3"
            >
              <div className="flex-1 min-w-0">
                <div className="font-mono text-xs text-[var(--color-warning)]">
                  {f.field_path}
                </div>
                <div className="text-[var(--color-text-muted)] text-sm mt-1">
                  {f.claim}
                </div>
                {f.rationale && (
                  <div className="text-[var(--color-text-faint)] italic text-xs mt-1">
                    {f.rationale}
                  </div>
                )}
              </div>
              <Button
                variant="primary"
                size="sm"
                onClick={() => acceptMut.mutate(path)}
                disabled={busy || !path}
                title="Accept this claim. The value stays in the profile; the review flag is cleared."
                className="shrink-0"
              >
                {busy ? (
                  <Loader2 size={12} className="animate-spin" />
                ) : (
                  <Check size={12} />
                )}
                Accept
              </Button>
            </li>
          );
        })}
      </ul>
    </SectionCard>
  );
}

function RawJsonTab({ data }: { data: ProfileShape }) {
  return (
    <Card className="p-0 overflow-hidden">
      <div className="px-5 py-3 border-b border-[var(--color-border)]">
        <span className="text-xs uppercase tracking-wider text-[var(--color-text-muted)] font-mono">
          Raw JSON · machine-readable schema
        </span>
      </div>
      <pre className="text-xs font-mono leading-relaxed text-[var(--color-text-muted)] overflow-auto max-h-[600px] p-4 bg-black/30">
        {JSON.stringify(data, null, 2)}
      </pre>
    </Card>
  );
}

/** Strip protocol + www from a URL for the website crumb chip, so
 *  the chip reads "hyster.com" not "https://www.hyster.com". */
function hostOfUrl(url: string): string {
  try { return new URL(url).host.replace(/^www\./, ""); }
  catch { return url; }
}

// ─── Cross-links from a profile to its plans + builds ─────────────
//
// One section card with two compact tables: every Plan the planner
// produced from this profile, and every Pipeline the orchestrator
// ran on its behalf. Click anywhere to drill in.

function RelatedProfileContent({ profileId }: { profileId: string }) {
  const plans = useQuery({
    queryKey: ["plans-by-profile", profileId],
    queryFn: () => listPlans({ source_profile_id: profileId }),
    refetchInterval: 30_000,
  });
  const pipelines = useQuery({
    queryKey: ["pipelines"],
    queryFn: listPipelines,
    // Faster refresh when something on this profile is in flight so
    // the row's status badge stays live without a manual refetch.
    refetchInterval: 10_000,
  });

  const navigate = useNavigate();
  const planRows = plans.data ?? [];
  const builds = (pipelines.data ?? []).filter((p) => p.profile_id === profileId).slice(0, 5);

  return (
    <Card className="p-5 space-y-5">
      <div className="flex items-center justify-between gap-2">
        <h2 className="text-sm font-medium text-[var(--color-text)] m-0">Related</h2>
        <span className="font-mono text-[11px] text-[var(--color-text-faint)] tabular-nums">
          {planRows.length} plans · {builds.length} recent builds
        </span>
      </div>

      <div>
        <div className="flex items-center justify-between mb-2">
          <div className="text-[10px] font-mono uppercase tracking-wider text-[var(--color-text-faint)]">
            Plans from this profile
          </div>
          {planRows.length > 0 && (
            <Link
              to={`/plans?profile=${encodeURIComponent(profileId)}`}
              className="text-[11px] text-[var(--color-text-muted)] hover:text-[var(--color-accent)] underline-offset-2 hover:underline"
            >
              See all on Plans →
            </Link>
          )}
        </div>
        {plans.isLoading ? (
          <div className="text-xs text-[var(--color-text-faint)] italic py-2">Loading…</div>
        ) : planRows.length === 0 ? (
          <div className="text-xs text-[var(--color-text-faint)] italic py-2">
            No plans yet. Use <span className="font-mono">Build demo from this profile</span> above.
          </div>
        ) : (
          <ul className="divide-y divide-[var(--color-border)] border border-[var(--color-border)] rounded-md overflow-hidden">
            {planRows.slice(0, 6).map((p) => (
              <li
                key={p.plan_id}
                onClick={() => {
                  // Pending rows are in-flight pipelines, not real
                  // plan_ids; route to the build page instead so the
                  // SE can watch them finish.
                  if (p.pending && p.pipeline_id) {
                    void navigate("/new");
                    return;
                  }
                  navigate(`/plans/${p.plan_id}`);
                }}
                className="flex items-center gap-3 px-3 py-2.5 cursor-pointer hover:bg-white/[0.02] transition-colors"
              >
                <span
                  aria-hidden="true"
                  className={cn(
                    "row-flag",
                    p.pending ? "live" : reviewStateRowFlag(p.review_state),
                  )}
                />
                <div className="flex-1 min-w-0">
                  <div className="text-sm font-medium text-[var(--color-text)] truncate">
                    {p.plan_id_short}
                  </div>
                  <div className="text-[11px] text-[var(--color-text-faint)] truncate">
                    {p.process_count} processes · {p.kg_node_count} KG nodes · {p.alert_count} alerts
                  </div>
                </div>
                <Badge tone={p.pending ? "info" : reviewStateTone(p.review_state)}>
                  {p.review_state}
                </Badge>
                <ChevronRight size={14} className="text-[var(--color-text-faint)] shrink-0" />
              </li>
            ))}
          </ul>
        )}
      </div>

      <div>
        <div className="flex items-center justify-between mb-2">
          <div className="text-[10px] font-mono uppercase tracking-wider text-[var(--color-text-faint)]">
            Recent builds
          </div>
          {builds.length > 0 && (
            <Link
              to="/new"
              className="text-[11px] text-[var(--color-text-muted)] hover:text-[var(--color-accent)] underline-offset-2 hover:underline"
            >
              All builds →
            </Link>
          )}
        </div>
        {pipelines.isLoading ? (
          <div className="text-xs text-[var(--color-text-faint)] italic py-2">Loading…</div>
        ) : builds.length === 0 ? (
          <div className="text-xs text-[var(--color-text-faint)] italic py-2">
            No builds for this profile yet.
          </div>
        ) : (
          <ul className="divide-y divide-[var(--color-border)] border border-[var(--color-border)] rounded-md overflow-hidden">
            {builds.map((b) => (
              <li
                key={b.pipeline_id}
                onClick={() => navigate(`/pipelines/${b.pipeline_id}`)}
                className="flex items-center gap-3 px-3 py-2.5 cursor-pointer hover:bg-white/[0.02] transition-colors"
              >
                <span
                  aria-hidden="true"
                  className={cn(
                    "row-flag",
                    b.status === "running"   ? "live"
                  : b.status === "failed"    ? "danger"
                  : b.status === "cancelled" ? "warn"
                  : "muted",
                  )}
                />
                <div className="flex-1 min-w-0">
                  <div className="text-sm font-medium font-mono text-[var(--color-text)] truncate">
                    {b.pipeline_id.slice(0, 8)}
                  </div>
                  <div className="text-[11px] text-[var(--color-text-faint)] truncate">
                    {b.plan_id
                      ? <>landed plan <span className="font-mono">{b.plan_id.slice(0, 8)}</span></>
                      : "no plan landed yet"}
                  </div>
                </div>
                <Badge
                  tone={
                    b.status === "running"   ? "info"
                  : b.status === "done"      ? "success"
                  : b.status === "failed"    ? "danger"
                  : "warning"
                  }
                >
                  {b.status}
                </Badge>
                <ChevronRight size={14} className="text-[var(--color-text-faint)] shrink-0" />
              </li>
            ))}
          </ul>
        )}
      </div>
    </Card>
  );
}

/** Map plan review_state to the .row-flag color class used on the
 *  Builds + Plans tables, so the relevant-content list reads with the
 *  same visual rhythm as the rest of the app. */
function reviewStateRowFlag(state: string): "live" | "warn" | "muted" | "danger" {
  switch (state) {
    case "provisioned":            return "live";
    case "approved_for_provision": return "live";
    case "se_reviewed":            return "warn";
    case "torn_down":              return "warn";
    case "draft":                  return "muted";
    default:                       return "muted";
  }
}

function Field({
  label, value, extra,
}: { label: string; value?: string; extra?: string }) {
  return (
    <div>
      <div className="text-[10px] uppercase tracking-wider text-[var(--color-text-faint)]">
        {label}
      </div>
      <div className="text-sm mt-0.5">
        {value ?? <span className="text-[var(--color-text-faint)]">—</span>}
        {extra && <span className="text-[var(--color-text-faint)] ml-1">{extra}</span>}
      </div>
    </div>
  );
}

/** Headcount formatter that keeps numbers human at scan-glance:
 *    7900   → "7.9K"
 *    250000 → "250K"
 *    1250000 → "1.3M"
 *  Anything under 1000 stays as-is so small companies don't end up
 *  with a "0.9K" label. */
function formatHeadcount(n: number): string {
  if (n < 1_000) return n.toLocaleString();
  if (n < 1_000_000) {
    const k = n / 1_000;
    return `${k >= 100 ? Math.round(k) : k.toFixed(1).replace(/\.0$/, "")}K`;
  }
  const m = n / 1_000_000;
  return `${m >= 100 ? Math.round(m) : m.toFixed(1).replace(/\.0$/, "")}M`;
}

function SectionCard({
  title, count, tone, children,
}: {
  title: string;
  count?: number;
  tone?: "warning";
  children: ReactNode;
}) {
  return (
    <Card
      className={cn(
        "p-5",
        tone === "warning" && "border-[var(--color-warning)]/30 bg-[var(--color-warning)]/5",
      )}
    >
      <div className="flex items-center gap-2 mb-3">
        <h2 className="text-sm font-medium text-[var(--color-text-muted)] uppercase tracking-wider">
          {title}
        </h2>
        {count !== undefined && (
          <span className="text-xs text-[var(--color-text-faint)] font-mono">
            {count}
          </span>
        )}
      </div>
      {children}
    </Card>
  );
}


/** Entry point into the global Clarion assistant, scoped to this
 *  profile. Replaces the old in-page ExtendProfilePanel chat: extending
 *  a profile's research is now one of the things the assistant does (it
 *  calls the extend_profile tool, then can re-run the plan so the new
 *  entities reach the live demo), so the profile page points at the
 *  assistant rather than carrying a second, divergent chat surface.
 *
 *  Seeding a prompt prefills the assistant's compose box — the SE can
 *  edit it before sending or clear it entirely. */
function ExtendWithAssistantPanel({ profileId }: { profileId: string }) {
  const assistant = useAssistant();

  const EXAMPLES = [
    "Add the industries they support",
    "We're missing their EMEA region channels",
    "Add their main competitors and recent acquisitions",
  ];

  function open(seed?: string) {
    assistant.openAssistant({ scope: { profile_id: profileId }, seedPrompt: seed });
  }

  return (
    <Card className="p-6">
      <div className="flex items-start gap-3">
        <span className="inline-flex items-center justify-center w-9 h-9 rounded-lg bg-[var(--color-accent-bg)] border border-[var(--color-accent-border)] shrink-0">
          <Bot size={18} className="text-[var(--color-accent)]" />
        </span>
        <div className="flex-1 min-w-0">
          <h2 className="text-sm font-medium text-[var(--color-text)] m-0">
            Extend research with the assistant
          </h2>
          <p className="mt-1.5 text-sm text-[var(--color-text-muted)] leading-relaxed max-w-2xl">
            Tell the Clarion assistant what&rsquo;s missing or wrong and it extends
            this profile in place — additive only — then offers to re-run the plan so
            the new entities reach your demo. The same conversation also refines plans,
            runs builds, and drives demos, so the whole loop stays in one place.
          </p>
          <div className="mt-4 flex flex-wrap items-center gap-2">
            <Button variant="primary" size="sm" onClick={() => open()}>
              <Bot size={12} /> Open assistant
            </Button>
            <span className="text-[11px] text-[var(--color-text-faint)]">
              or start from an example:
            </span>
          </div>
          <div className="mt-2 flex flex-wrap gap-2">
            {EXAMPLES.map((ex) => (
              <button
                key={ex}
                type="button"
                onClick={() => open(ex)}
                className="text-left text-xs px-2.5 py-1.5 rounded-md border border-[var(--color-border)] bg-[var(--color-canvas-elev1)] text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:border-[var(--color-border-strong)] transition-colors"
              >
                {ex}
              </button>
            ))}
          </div>
        </div>
      </div>
    </Card>
  );
}
