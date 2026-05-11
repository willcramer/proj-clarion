import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate, useParams, Link } from "react-router-dom";
import { useState, type ReactNode } from "react";
import {
  ArrowLeft, MessageCircle, Send, Loader2, Trash2, Sparkles, Activity,
} from "lucide-react";

import {
  listProfiles, getProfile, streamAgent, deleteProfile, listPipelines,
  type ChatMessage,
} from "@/lib/api";
import { usePipeline } from "@/lib/PipelineContext";
import { Card } from "@/components/Card";
import { Badge } from "@/components/Badge";
import { Button } from "@/components/Button";
import { ConfirmDialog } from "@/components/ConfirmDialog";
import { cn } from "@/lib/cn";

// Refetch every 5s while looking at the list so in-flight pipeline
// placeholders keep their spinner alive and disappear when research
// lands. Cheap query — DB list of pipelines + profiles.
const PROFILES_REFETCH_MS = 5_000;

// ─── List page ────────────────────────────────────────────────────

export function ProfilesListPage() {
  const profiles = useQuery({
    queryKey: ["profiles"],
    queryFn: listProfiles,
    refetchInterval: PROFILES_REFETCH_MS,
  });
  const navigate = useNavigate();
  const pipeline = usePipeline();

  function addProfile() {
    // Don't auto-start the build. Capture the URL via prompt and drop
    // the user into the build form on /new with that URL pre-filled —
    // they pick the volume preset (smoke / demo / auto / stress) and
    // days from the form before clicking Build. Auto-starting bypassed
    // those knobs and produced a default-sized build whether the user
    // wanted it or not.
    const url = window.prompt(
      "Enter the company URL to research (must be in RESEARCH_ALLOWED_HOSTS).\n"
      + "\n"
      + "You'll be taken to the build form to pick volume size + days "
      + "before the build actually starts.",
      "",
    );
    if (!url) return;
    const trimmed = url.trim();
    if (!trimmed) return;
    navigate(`/new?prefill_url=${encodeURIComponent(trimmed)}`);
  }

  return (
    <div className="space-y-6">
      <div className="flex items-start justify-between gap-3 flex-wrap">
        <div>
          <h1 className="text-2xl font-semibold tracking-tight">Profiles</h1>
          <p className="text-[var(--color-text-muted)] mt-1 text-sm">
            CompanyProfiles produced by the research agent. Click into one to extend it.
          </p>
        </div>
        <Button
          variant="primary"
          size="sm"
          onClick={() => void addProfile()}
          title="Research a new company URL — the resulting CompanyProfile lands here when research completes"
        >
          <Sparkles size={12} /> Add profile
        </Button>
      </div>

      <Card>
        {profiles.isLoading ? (
          <div className="p-8 text-center text-[var(--color-text-faint)]">Loading…</div>
        ) : (profiles.data ?? []).length === 0 ? (
          <div className="p-8 text-center text-[var(--color-text-muted)]">
            No profiles yet. Run <code className="font-mono text-xs">just research &lt;url&gt;</code>.
          </div>
        ) : (
          <table className="w-full text-sm">
            <thead className="text-xs text-[var(--color-text-faint)] uppercase tracking-wider border-b border-[var(--color-border)]">
              <tr>
                <th className="text-left font-medium px-4 py-3">Profile</th>
                <th className="text-left font-medium px-4 py-3">Company</th>
                <th className="text-left font-medium px-4 py-3">Source</th>
                <th className="text-right font-medium px-4 py-3">Pain signals</th>
                <th className="text-right font-medium px-4 py-3">Tech signals</th>
                <th className="text-right font-medium px-4 py-3">Synth flags</th>
              </tr>
            </thead>
            <tbody>
              {(profiles.data ?? []).map((p) => {
                // Pending rows are in-flight pipelines; click → /pipelines
                // detail rather than /profiles/<pending-id> which 404s.
                const onRowClick = p.pending && p.pipeline_id
                  ? () => navigate(`/pipelines?p=${p.pipeline_id}`)
                  : () => navigate(`/profiles/${p.profile_id}`);
                return (
                  <tr
                    key={p.profile_id}
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
                          researching…
                        </span>
                      ) : (
                        p.profile_id
                      )}
                    </td>
                    <td className="px-4 py-3">{p.company_name ?? <span className="text-[var(--color-text-faint)]">—</span>}</td>
                    <td className="px-4 py-3 text-xs text-[var(--color-text-muted)] truncate max-w-[280px]">
                      {p.primary_url}
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
        )}
      </Card>
    </div>
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
  // CompanyProfile (Pydantic) — typed `unknown` on the wire so we narrow.
  function profileUrl(): string | undefined {
    const data = profile.data as { company?: { primary_url?: string } } | undefined;
    return data?.company?.primary_url;
  }

  async function buildFromProfile() {
    // Start a NEW build that skips research (we already have this profile)
    // and goes straight to plan. Smart resume's not the right tool — that's
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
      window.alert("This profile doesn't have a primary URL — can't start a build.");
      return;
    }
    setBuilding(true);
    try {
      await pipeline.startFromPhase({
        phase: "plan",
        url,
        profile_id: profileId,
        // No parent_pipeline_id — this is a fresh build, not a resume.
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

  return (
    <div className="space-y-6">
      <div className="flex items-center justify-between gap-2 flex-wrap">
        <Link to="/profiles" className="text-sm text-[var(--color-text-muted)] hover:text-[var(--color-text)] inline-flex items-center gap-1">
          <ArrowLeft size={14} /> Profiles
        </Link>
        <div className="flex items-center gap-2">
          {latestPipeline && (
            <Button
              variant="secondary"
              size="sm"
              onClick={() => void viewLatestBuild()}
              title={`Open the latest build for this profile (${latestPipeline.pipeline_id} · ${latestPipeline.status})`}
            >
              <Activity size={12} /> View latest build
              <span className="ml-1 text-[10px] text-[var(--color-text-faint)]">
                {latestPipeline.status}
              </span>
            </Button>
          )}
          <Button
            variant="primary"
            size="sm"
            onClick={() => void buildFromProfile()}
            disabled={building || !profile.data}
            title="Start a new pipeline that skips research (uses this profile) and goes straight to plan → … → kg-publish"
          >
            {building ? (
              <Loader2 size={12} className="animate-spin" />
            ) : (
              <Sparkles size={12} />
            )}
            Build demo from this profile
          </Button>
          <Button variant="danger" size="sm" onClick={() => setConfirmDelete(true)}>
            <Trash2 size={12} /> Delete profile
          </Button>
        </div>
      </div>

      {deleteError && (
        <Card className="p-3 text-xs text-[var(--color-danger)] border-[var(--color-danger)]/30 bg-[var(--color-danger)]/5">
          {deleteError}
        </Card>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <div className="space-y-4">
          {profile.isLoading ? (
            <Card className="p-5"><div className="text-[var(--color-text-faint)] text-sm">Loading…</div></Card>
          ) : (
            <ProfileSummaryView
              profileId={profileId}
              data={profile.data as ProfileShape | undefined}
            />
          )}
        </div>

        <AgentChatPanel
          contextId={profileId}
          endpoint="research/extend"
          title="Extend research"
          subtitle="Ask the agent for more depth on this company. Read-only — suggestions don't auto-save."
        />
      </div>

      <ConfirmDialog
        open={confirmDelete}
        title="Delete this profile?"
        body={
          <div className="space-y-2">
            <p>Removes the profile from Postgres. <strong>Cascades to every plan
              that references it</strong> — those plans, their KG, events, and audit
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
  company?: {
    name?: string;
    primary_url?: string;
    description?: string;
    headquarters?: string;
    founded_year?: number;
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

function ProfileSummaryView({
  profileId, data,
}: { profileId: string; data: ProfileShape | undefined }) {
  const [showRaw, setShowRaw] = useState(false);
  if (!data) {
    return (
      <Card className="p-5">
        <div className="text-[var(--color-text-faint)] text-sm">Profile not found.</div>
      </Card>
    );
  }
  const co = data.company ?? {};
  const tax = data.industry_taxonomy ?? {};
  const geo = data.geographic_footprint ?? {};
  return (
    <div className="space-y-4">
      {/* Hero — name, domain, classification */}
      <Card className="p-5">
        <div className="flex items-start justify-between gap-4">
          <div className="min-w-0">
            <h1 className="text-xl font-semibold tracking-tight truncate">
              {co.name ?? "(unnamed company)"}
            </h1>
            {co.primary_url && (
              <a
                href={co.primary_url}
                target="_blank"
                rel="noreferrer"
                className="text-sm text-[var(--color-accent)] hover:underline inline-block mt-0.5"
              >
                {co.primary_url}
              </a>
            )}
            <div className="font-mono text-[10px] text-[var(--color-text-faint)] mt-1.5">
              {profileId}
            </div>
          </div>
          {tax.business_model && (
            <Badge tone="accent">{tax.business_model}</Badge>
          )}
        </div>
        {co.description && (
          <p className="text-sm text-[var(--color-text-muted)] mt-3 leading-relaxed">
            {co.description}
          </p>
        )}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3 mt-4 pt-4 border-t border-[var(--color-border)] text-xs">
          <Field label="Industry" value={tax.primary_industry} />
          <Field label="HQ" value={co.headquarters} />
          <Field label="Founded" value={co.founded_year?.toString()} />
          <Field
            label="Footprint"
            value={(geo.countries ?? []).slice(0, 3).join(", ") || undefined}
            extra={(geo.countries?.length ?? 0) > 3 ? `+${geo.countries!.length - 3}` : undefined}
          />
        </div>
      </Card>

      {/* Channels */}
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

      {/* Business entities (regions, business units, brands, etc.) */}
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

      {/* Strategic priorities */}
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

      {/* Pain signals — color-coded by severity */}
      {data.pain_signals && data.pain_signals.length > 0 && (
        <SectionCard title="Pain signals" count={data.pain_signals.length}>
          <ul className="space-y-2.5">
            {data.pain_signals.map((p, i) => (
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
      )}

      {/* Tech stack signals — group by confidence */}
      {data.tech_stack_signals && data.tech_stack_signals.length > 0 && (
        <SectionCard title="Tech stack" count={data.tech_stack_signals.length}>
          <ul className="grid grid-cols-1 md:grid-cols-2 gap-x-4 gap-y-2 text-sm">
            {data.tech_stack_signals.map((t, i) => (
              <li key={i} className="flex items-center gap-2">
                <Badge
                  tone={t.confidence === "high" ? "success" : t.confidence === "medium" ? "info" : "neutral"}
                >
                  {t.component_type}
                </Badge>
                <span className="font-medium truncate">{t.vendor_or_product}</span>
              </li>
            ))}
          </ul>
        </SectionCard>
      )}

      {/* Synthesized flags — what the agent guessed without sources. SE
       *  should review these before approving a plan based on the profile. */}
      {data.synthesized_flags && data.synthesized_flags.length > 0 && (
        <SectionCard
          title="Synthesized claims (review)"
          count={data.synthesized_flags.length}
          tone="warning"
        >
          <ul className="space-y-2 text-sm">
            {data.synthesized_flags.map((f, i) => (
              <li key={i} className="text-xs">
                <div className="font-mono text-[var(--color-warning)]">{f.field_path}</div>
                <div className="text-[var(--color-text-muted)] mt-0.5">{f.claim}</div>
                {f.rationale && (
                  <div className="text-[var(--color-text-faint)] italic mt-0.5">
                    {f.rationale}
                  </div>
                )}
              </li>
            ))}
          </ul>
        </SectionCard>
      )}

      {/* Collapsed raw JSON (debug / schema view). Click to expand. */}
      <Card className="p-0 overflow-hidden">
        <button
          type="button"
          onClick={() => setShowRaw(v => !v)}
          className="w-full px-5 py-3 flex items-center justify-between text-xs text-[var(--color-text-muted)] hover:bg-white/[0.02] transition-colors"
        >
          <span className="uppercase tracking-wider">
            Raw JSON (machine-readable schema)
          </span>
          <span>{showRaw ? "▾" : "▸"}</span>
        </button>
        {showRaw && (
          <pre className="text-xs font-mono leading-relaxed text-[var(--color-text-muted)] overflow-auto max-h-[500px] p-4 bg-black/30 border-t border-[var(--color-border)]">
            {JSON.stringify(data, null, 2)}
          </pre>
        )}
      </Card>
    </div>
  );
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


// ─── Reusable streaming chat panel ─────────────────────────────────

export function AgentChatPanel({
  contextId,
  endpoint,
  title,
  subtitle,
}: {
  contextId: string;
  endpoint: "research/extend" | "plan/refine";
  title: string;
  subtitle: string;
}) {
  const [history, setHistory] = useState<ChatMessage[]>([]);
  const [draft, setDraft] = useState("");
  const [streaming, setStreaming] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function send() {
    const trimmed = draft.trim();
    if (!trimmed || streaming) return;
    setError(null);
    const next: ChatMessage[] = [...history, { role: "user", content: trimmed }];
    setHistory([...next, { role: "assistant", content: "" }]);
    setDraft("");
    setStreaming(true);
    try {
      await streamAgent(
        endpoint,
        contextId,
        next,
        (delta) => setHistory((h) => {
          const out = h.slice();
          const last = out[out.length - 1];
          if (last && last.role === "assistant") {
            out[out.length - 1] = { ...last, content: last.content + delta };
          }
          return out;
        }),
        () => setStreaming(false),
        (err) => { setError(err); setStreaming(false); },
      );
    } catch (e) {
      setError(String(e));
      setStreaming(false);
    }
  }

  return (
    <Card className="p-5 flex flex-col h-[680px]">
      <div className="flex items-center gap-2 mb-1">
        <MessageCircle size={14} className="text-[var(--color-accent)]" />
        <h2 className="text-sm font-medium">{title}</h2>
      </div>
      <p className="text-xs text-[var(--color-text-faint)] mb-4">{subtitle}</p>

      <div className="flex-1 overflow-y-auto space-y-3 pr-2 scroll-smooth">
        {history.length === 0 && (
          <div className="text-[var(--color-text-faint)] text-sm py-4 italic">
            Try something like: <span className="font-mono">"What channels are missing from this profile?"</span>
          </div>
        )}
        {history.map((m, i) => (
          <div
            key={i}
            className={cn(
              "rounded-md px-3 py-2 text-sm whitespace-pre-wrap",
              m.role === "user"
                ? "bg-[var(--color-accent-bg)] border border-[var(--color-accent-border)] text-[var(--color-text)]"
                : "bg-white/[0.02] border border-[var(--color-border)] text-[var(--color-text)]",
            )}
          >
            <div className="text-[10px] uppercase tracking-wider mb-1 text-[var(--color-text-faint)]">
              {m.role}
            </div>
            {m.content || (streaming && i === history.length - 1 ? <span className="inline-flex"><Loader2 size={12} className="animate-spin" /></span> : <span className="text-[var(--color-text-faint)]">…</span>)}
          </div>
        ))}
        {error && (
          <div className="rounded-md px-3 py-2 text-sm border border-[var(--color-danger)]/30 bg-[var(--color-danger)]/10 text-[var(--color-danger)]">
            {error}
          </div>
        )}
      </div>

      <div className="flex gap-2 pt-3 border-t border-[var(--color-border)]">
        <textarea
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
              e.preventDefault();
              void send();
            }
          }}
          placeholder="Type a question for the agent…  (⌘↵ to send)"
          rows={2}
          className="flex-1 resize-none rounded-md bg-white/[0.02] border border-[var(--color-border)] px-3 py-2 text-sm placeholder:text-[var(--color-text-faint)] focus:border-[var(--color-accent)] focus:outline-none"
          disabled={streaming}
        />
        <Button onClick={() => void send()} disabled={streaming || !draft.trim()} variant="primary">
          {streaming ? <Loader2 size={14} className="animate-spin" /> : <><Send size={14} /> Send</>}
        </Button>
      </div>
    </Card>
  );
}
