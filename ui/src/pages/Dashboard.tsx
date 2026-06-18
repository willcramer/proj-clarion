/**
 * Dashboard — the SE's landing surface (homepage redesign).
 *
 * Idle-first, agentic, calm. Structure (per the clarion-icons v2 handoff):
 *   Hero      — greeting + agent dot · gradient headline "What should we
 *               demo today?" · the command bar (one input that takes a URL
 *               OR a description, a preset selector, Build demo) · a faint
 *               pipeline hint · quiet suggestion chips.
 *   KPI band  — 4 thin gradient cards (Plans / Profiles / Events / KG nodes).
 *   Continue  — recent accounts to resume (hairline list, no card boxes).
 *   Needs you — the attention queue (approve / verify / retry).
 *   Live dock — a docked pill at the bottom, only while a demo is running.
 *
 * The command bar is the hero of the app: a URL starts a build directly;
 * a free-text description hands off to the assistant, which infers the
 * best-fit company and builds it. OrphanCleanup is preserved (self-hides
 * when there's nothing to clean) since the homepage is its only home.
 */
import { useEffect, useMemo, useRef, useState, type ComponentType, type CSSProperties } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import {
  ClipboardList, ScrollText, Database, Hammer, ChevronRight, ChevronDown,
  ArrowRight, Sparkles, Trash2, ExternalLink, Loader2, AlertTriangle,
  CheckCircle2, AlertCircle, Square, Wand2, SlidersHorizontal, Check,
} from "lucide-react";

import {
  getDashboardSummary, listPlans, listProfiles, listPipelines,
  listDemoSessions, listOrphanFolders, deleteOrphanFolder, stopDemoSession,
  RESEARCH_SOURCES,
  type DashboardSummary, type OrphanFolder, type PlanSummary,
  type ProfileSummary, type PipelineSummary, type DemoSession, type ResearchSource,
} from "@/lib/api";
import { Button } from "@/components/Button";
import { Card } from "@/components/Card";
import { LiveInCloud } from "@/components/icons/ClarionIcons";
import { deriveDemoStatus, hostOf } from "@/components/ProfileKpiCard";
import { usePipeline } from "@/lib/PipelineContext";
import { useAssistant } from "@/lib/AssistantContext";
import { cn } from "@/lib/cn";

// ─── Build presets (volume per day; days fixed at 1 from the hero) ──

type BuildPreset = "smoke" | "demo" | "auto" | "stress";
const PRESETS: { id: BuildPreset; label: string; sub: string; volume?: number }[] = [
  { id: "smoke",  label: "Smoke",  sub: "500 / day",  volume: 500 },
  { id: "demo",   label: "Demo",   sub: "2.5K / day", volume: 2_500 },
  { id: "auto",   label: "Auto",   sub: "scaled" },
  { id: "stress", label: "Stress", sub: "25K / day",  volume: 25_000 },
];

function isUrlLike(text: string): boolean {
  if (/^https?:\/\//i.test(text)) return true;
  // A bare host: a dot-separated token with a TLD and no spaces.
  return /\.[a-z]{2,}(\/|$)/i.test(text) && !/\s/.test(text);
}

/** Canonical host for dedup: lowercased, scheme/www/path stripped. Works
 *  on a full URL or a bare host typed into the command bar. */
function normalizeHost(s: string): string {
  return s.trim().toLowerCase()
    .replace(/^https?:\/\//, "")
    .replace(/^www\./, "")
    .split("/")[0];
}

function greeting(): string {
  const h = new Date().getHours();
  if (h < 12) return "Good morning";
  if (h < 18) return "Good afternoon";
  return "Good evening";
}

// ─── Page ───────────────────────────────────────────────────────────

export function DashboardPage() {
  const summary = useQuery({ queryKey: ["dashboard"], queryFn: getDashboardSummary });
  const profiles = useQuery({ queryKey: ["profiles"], queryFn: listProfiles, refetchInterval: 15_000 });
  const plans = useQuery({ queryKey: ["plans"], queryFn: () => listPlans(), refetchInterval: 15_000 });
  const pipelines = useQuery({ queryKey: ["pipelines"], queryFn: listPipelines, refetchInterval: 10_000 });

  return (
    <div className="relative">
      <HeroAurora />

      {/* Hero — raised above the KPI/workspace bands (z-1) so the preset
          dropdown overlays them, but kept BELOW the sticky top bar (z-30)
          so its menus/dropdowns are never covered by the Build button. */}
      <section className="relative z-10 mx-auto max-w-[840px] px-2 pt-4 sm:pt-10 text-center">
        <div className="inline-flex items-center gap-2 text-sm text-[var(--color-text-muted)] mb-3.5">
          <span className="active-session-dot inline-block w-[7px] h-[7px] rounded-full bg-[var(--color-accent)]" />
          {greeting()}.
        </div>
        <h1 className="h1-display text-[34px] sm:text-[46px] font-semibold tracking-[-0.035em] leading-[1.05]">
          What should we demo today?
        </h1>
        <div className="mt-7">
          <CommandBar />
        </div>
        <Suggestions
          profiles={profiles.data ?? []}
          plans={plans.data ?? []}
        />
      </section>

      {/* Overview — workspace footprint at a glance. */}
      <section className="relative z-[1] mx-auto max-w-[1080px] mt-14 pt-9 border-t border-[var(--color-border)]">
        <SectionLabel>Overview</SectionLabel>
        <KpiBand summary={summary.data} buildsTotal={pipelines.data?.length} />
      </section>

      {/* Orphan cleanup — self-hides when nothing's orphaned. */}
      <div className="mx-auto max-w-[1080px] mt-6">
        <OrphanCleanup />
      </div>

      {/* Workspace — recent accounts to resume + the action queue, split
          by a hairline divider on wide screens. */}
      <section className="relative z-[1] mx-auto max-w-[1080px] mt-12 pt-9 border-t border-[var(--color-border)]">
        <div className="grid gap-x-12 gap-y-10 lg:grid-cols-[1.5fr_1fr]">
          <ContinueList profiles={profiles.data ?? []} plans={plans.data ?? []} loading={profiles.isLoading} />
          <div className="lg:border-l lg:border-[var(--color-border)] lg:pl-12">
            <NeedsYou plans={plans.data ?? []} profiles={profiles.data ?? []} pipelines={pipelines.data ?? []} />
          </div>
        </div>
      </section>

      <div className="h-16" />
      <LiveDock />
    </div>
  );
}

// ─── Hero aurora (subtle premium glow behind the hero) ──────────────

function HeroAurora() {
  return (
    <div aria-hidden="true" className="pointer-events-none absolute inset-x-0 top-[-80px] h-[420px] overflow-hidden">
      <div
        className="absolute left-1/2 top-[-160px] h-[420px] w-[900px] -translate-x-1/2"
        style={{ background: "radial-gradient(closest-side, color-mix(in srgb, var(--color-accent) 22%, transparent), transparent 70%)" }}
      />
      <div
        className="absolute left-[8%] top-[-40px] h-[320px] w-[420px]"
        style={{ background: "radial-gradient(closest-side, color-mix(in srgb, var(--color-signal) 20%, transparent), transparent 70%)" }}
      />
      <div
        className="absolute inset-0"
        style={{
          backgroundImage:
            "linear-gradient(var(--color-border) 1px, transparent 1px), linear-gradient(90deg, var(--color-border) 1px, transparent 1px)",
          backgroundSize: "48px 48px",
          maskImage: "radial-gradient(820px 380px at 50% 0%, #000, transparent 75%)",
          WebkitMaskImage: "radial-gradient(820px 380px at 50% 0%, #000, transparent 75%)",
          opacity: 0.4,
        }}
      />
    </div>
  );
}

// ─── Command bar ────────────────────────────────────────────────────

function CommandBar() {
  const pipeline = usePipeline();
  const assistant = useAssistant();
  const navigate = useNavigate();
  const profilesQ = useQuery({ queryKey: ["profiles"], queryFn: listProfiles });
  const [text, setText] = useState("");
  const [preset, setPreset] = useState<BuildPreset>("demo");
  const [focus, setFocus] = useState(false);
  const [busy, setBusy] = useState(false);
  // When the typed URL already has a profile, we surface a guard instead
  // of blindly researching the same company again.
  const [dup, setDup] = useState<ProfileSummary | null>(null);
  // Advanced customization: by default "Build demo" runs the OOTB pipeline.
  // Expanding Advanced lets the SE turn individual research sources off and
  // attach discovery notes before kicking the build off.
  const [advancedOpen, setAdvancedOpen] = useState(false);
  const [disabledSources, setDisabledSources] = useState<Set<ResearchSource>>(new Set());
  const [notes, setNotes] = useState("");
  const presetMeta = PRESETS.find((p) => p.id === preset)!;
  const customized = disabledSources.size > 0 || notes.trim().length > 0;

  function toggleSource(key: ResearchSource) {
    setDisabledSources((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }

  function existingProfileFor(input: string): ProfileSummary | null {
    const host = normalizeHost(input);
    if (!host) return null;
    return (profilesQ.data ?? []).find(
      (p) => !p.pending && normalizeHost(p.primary_url) === host,
    ) ?? null;
  }

  async function run(opts?: { force?: boolean }) {
    const t = text.trim();
    if (!t || busy) return;
    if (isUrlLike(t)) {
      // Guard: don't re-research a company we already have a profile for.
      // Surface the existing one; let the SE open it, build from it (no
      // duplicate research), or override and build fresh.
      if (!opts?.force) {
        const existing = existingProfileFor(t);
        if (existing) { setDup(existing); return; }
      }
      setDup(null);
      // URL → start the build directly (fast path). Stops at the plan:
      // nothing is written to Grafana Cloud until the SE reviews and
      // approves the plan, then provisions explicitly.
      setBusy(true);
      try {
        await pipeline.start({
          url: t, days: 1, volume_per_day: presetMeta.volume,
          stop_after_phase: "plan", allow_duplicate: !!opts?.force,
          // Advanced customizations — only sent when the SE changed them, so
          // the default OOTB request shape is unchanged.
          disabled_sources: disabledSources.size ? Array.from(disabledSources) : undefined,
          notes: notes.trim() || undefined,
        });
        navigate("/new");
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e);
        window.alert(`Couldn't start build:\n\n${msg}`);
      } finally {
        setBusy(false);
      }
    } else {
      // Free-text description → hand to the assistant, which infers the
      // best-fit company URL and starts the build itself. autoSend = it
      // just goes (no manual kick-off, no approval pause); the SE watches
      // it work in the drawer.
      assistant.openAssistant({
        newThread: true,
        scope: { route: "/" },
        seedPrompt: `Build a ${presetMeta.label.toLowerCase()} demo for this customer: ${t}. Figure out the best-fit company and its URL, then start the build.`,
        autoSend: true,
      });
      setText("");
    }
  }

  return (
    <div>
      <div
        className={cn(
          "flex items-center gap-2.5 h-[64px] pl-5 pr-2.5 rounded-2xl bg-[var(--color-canvas-elev1)] transition-shadow",
          focus
            ? "border border-[var(--color-accent-border)] shadow-[0_0_0_4px_var(--color-accent-bg),var(--shadow-lg)]"
            : "border border-[var(--color-border-strong)] shadow-[var(--shadow-md)]",
        )}
      >
        <input
          value={text}
          onChange={(e) => { setText(e.target.value); if (dup) setDup(null); }}
          onFocus={() => setFocus(true)}
          onBlur={() => setFocus(false)}
          onKeyDown={(e) => {
            if (e.key === "Enter" && (e.metaKey || e.ctrlKey || !e.shiftKey)) {
              e.preventDefault();
              void run();
            }
          }}
          placeholder="Paste a company URL, or describe the customer…"
          className="flex-1 min-w-0 bg-transparent border-none outline-none text-[15px] sm:text-[16px] font-mono text-[var(--color-text)] placeholder:text-[var(--color-text-faint)]"
        />
        <PresetSelect preset={preset} onChange={setPreset} />
        <Button
          variant="primary"
          onClick={() => void run()}
          disabled={busy || !text.trim()}
          className="h-11 rounded-xl px-4 shrink-0"
        >
          {busy ? <Loader2 size={16} className="animate-spin" /> : <Wand2 size={15} />}
          Build demo
          {!busy && <ArrowRight size={15} />}
        </Button>
      </div>

      {/* Duplicate-profile guard — we already researched this company, so
          don't blindly start a second build. Open the existing profile,
          build from it (skips re-research), or override and build fresh. */}
      {dup && (
        <div className="mt-2.5 flex flex-wrap items-center gap-x-3 gap-y-2 rounded-xl border border-[color:var(--color-warning)]/40 bg-[var(--color-warning-bg)] px-3.5 py-2.5 text-left">
          <AlertTriangle size={15} className="shrink-0 text-[var(--color-warning)]" />
          <span className="text-[13px] text-[var(--color-text)]">
            A profile for{" "}
            <span className="font-medium">{dup.company_name ?? normalizeHost(dup.primary_url)}</span>{" "}
            already exists. Build from it instead of researching again?
          </span>
          <div className="ml-auto flex items-center gap-2">
            <Button size="sm" variant="primary" onClick={() => navigate(`/profiles/${dup.profile_id}`)}>
              Open profile <ArrowRight size={13} />
            </Button>
            <Button size="sm" variant="ghost" onClick={() => void run({ force: true })}>
              Build new anyway
            </Button>
          </div>
        </div>
      )}

      <div className="flex items-center justify-between gap-3 mt-2.5 px-1 flex-wrap">
        <PipelineHint />
        <div className="flex items-center gap-3 shrink-0">
          <button
            type="button"
            onClick={() => setAdvancedOpen((v) => !v)}
            aria-expanded={advancedOpen}
            className={cn(
              "inline-flex items-center gap-1.5 text-[12px] rounded-md px-1.5 py-1 transition-colors",
              advancedOpen || customized
                ? "text-[var(--color-accent)]"
                : "text-[var(--color-text-faint)] hover:text-[var(--color-text-muted)]",
            )}
            title="Customize which research sources run and attach discovery notes"
          >
            <SlidersHorizontal size={13} aria-hidden="true" />
            Advanced
            {customized && (
              <span className="font-mono text-[10px]">
                · {RESEARCH_SOURCES.length - disabledSources.size}/{RESEARCH_SOURCES.length} sources
                {notes.trim() ? " · notes" : ""}
              </span>
            )}
            <ChevronDown
              size={13}
              aria-hidden="true"
              className={cn("transition-transform", advancedOpen && "rotate-180")}
            />
          </button>
          <span className="font-mono text-[11px] text-[var(--color-text-faint)]">⌘↵ to run</span>
        </div>
      </div>

      {/* Advanced — customize the research before building. Collapsed by
          default so the OOTB "Build demo" path stays a single action. */}
      {advancedOpen && (
        <div className="mt-2.5 rounded-xl border border-[var(--color-border)] bg-[var(--color-canvas-elev1)] px-4 py-3.5 space-y-3.5 text-left">
          <div>
            <div className="text-[11px] font-mono uppercase tracking-wider text-[var(--color-text-muted)] mb-2">
              Research sources
            </div>
            <div className="grid grid-cols-2 sm:grid-cols-3 gap-1.5">
              {RESEARCH_SOURCES.map((s) => {
                const on = !disabledSources.has(s.key);
                return (
                  <button
                    key={s.key}
                    type="button"
                    role="switch"
                    aria-checked={on}
                    onClick={() => toggleSource(s.key)}
                    title={s.hint}
                    className={cn(
                      "flex items-center gap-2 px-2.5 py-2 rounded-md border text-left transition-colors",
                      on
                        ? "bg-[var(--color-accent-bg)] border-[color:var(--color-accent-border)] text-[var(--color-text)]"
                        : "bg-transparent border-[var(--color-border)] text-[var(--color-text-faint)] hover:border-[var(--color-border-strong)]",
                    )}
                  >
                    <span
                      aria-hidden="true"
                      className={cn(
                        "inline-flex items-center justify-center w-4 h-4 rounded-[5px] shrink-0 border",
                        on
                          ? "bg-[var(--color-accent)] border-[var(--color-accent)] text-[var(--color-on-accent)]"
                          : "border-[var(--color-border-strong)] text-transparent",
                      )}
                    >
                      <Check size={11} />
                    </span>
                    <span className="text-[13px] font-medium leading-tight">{s.label}</span>
                  </button>
                );
              })}
            </div>
            <div className="text-[11px] text-[var(--color-text-faint)] mt-1.5">
              Turn off sources that don&rsquo;t apply (e.g. SEC for a private company).
              A source is also skipped when no matching handle is found.
            </div>
          </div>

          <div>
            <label
              htmlFor="hero-notes"
              className="text-[11px] font-mono uppercase tracking-wider text-[var(--color-text-muted)] mb-1.5 block"
            >
              Discovery / meeting notes
              <span className="ml-2 normal-case text-[10px] font-sans text-[var(--color-text-faint)] tracking-normal">
                optional · folded into research as a trusted source
              </span>
            </label>
            <textarea
              id="hero-notes"
              value={notes}
              onChange={(e) => setNotes(e.target.value)}
              rows={3}
              placeholder="Paste notes from a discovery call — priorities, stack, pain points."
              className={cn(
                "w-full rounded-[10px] resize-y min-h-[72px]",
                "bg-[var(--color-canvas)] border border-[var(--color-border-strong)]",
                "focus:border-[color:var(--color-accent-border)] outline-none transition-colors",
                "text-sm text-[var(--color-text)] placeholder:text-[var(--color-text-faint)] p-2.5",
              )}
            />
          </div>
        </div>
      )}
    </div>
  );
}

function PresetSelect({ preset, onChange }: { preset: BuildPreset; onChange: (p: BuildPreset) => void }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement | null>(null);
  const meta = PRESETS.find((p) => p.id === preset)!;
  useEffect(() => {
    function h(e: MouseEvent) { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false); }
    document.addEventListener("mousedown", h);
    return () => document.removeEventListener("mousedown", h);
  }, []);
  return (
    <div ref={ref} className="relative shrink-0">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="flex items-center gap-2 h-9 px-3 rounded-[10px] border border-[var(--color-border)] text-[12.5px] text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:border-[var(--color-border-strong)] transition-colors"
      >
        <Sparkles size={13} className="text-[var(--color-text-faint)]" />
        <span className="hidden sm:inline">{meta.label} · 1 day</span>
        <span className="sm:hidden">{meta.label}</span>
        <ChevronDown size={13} />
      </button>
      {open && (
        <div className="absolute right-0 top-11 z-50 w-44 rounded-xl border border-[var(--color-border-strong)] bg-[var(--color-canvas-elev2)] p-1 shadow-[var(--shadow-lg)]">
          {PRESETS.map((p) => (
            <button
              key={p.id}
              type="button"
              onClick={() => { onChange(p.id); setOpen(false); }}
              className={cn(
                "flex w-full items-center justify-between gap-3 rounded-md px-2.5 py-2 text-left text-[13px] transition-colors",
                p.id === preset ? "bg-[var(--color-accent-bg)] text-[var(--color-accent)]" : "text-[var(--color-text-muted)] hover:bg-white/[0.04] hover:text-[var(--color-text)]",
              )}
            >
              <span className="font-medium">{p.label}</span>
              <span className="font-mono text-[10px] text-[var(--color-text-faint)]">{p.sub}</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function PipelineHint() {
  // The build now stops at the plan — nothing reaches Grafana Cloud until
  // the SE reviews + approves it, so the hint shows the build (Research →
  // Plan) and then the explicit approve → provision → live tail.
  return (
    <div className="flex items-center gap-1.5 flex-wrap font-mono text-[11px]">
      <span style={{ color: "color-mix(in srgb, var(--color-accent) 80%, var(--color-text-muted))" }}>Research</span>
      <span className="text-[10px] text-[var(--color-accent)] opacity-60">→</span>
      <span style={{ color: "color-mix(in srgb, var(--color-accent) 80%, var(--color-text-muted))" }}>Plan</span>
      <span className="ml-1.5 text-[var(--color-text-faint)]">then review &amp; approve to provision → go live</span>
      <span className="ml-1.5 font-semibold text-[var(--color-accent)]">≈ 2 min</span>
    </div>
  );
}

// ─── Suggestion chips (functional, derived from real data) ──────────

function Suggestions({ profiles, plans }: { profiles: ProfileSummary[]; plans: PlanSummary[] }) {
  const navigate = useNavigate();
  const newest = [...profiles].filter((p) => !p.pending).sort(
    (a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime(),
  )[0];
  const draft = plans.find((p) => !p.pending && (p.review_state === "draft" || p.review_state === "se_reviewed"));

  const chips: { icon: ComponentType<{ size?: number; className?: string }>; verb: string; obj: string; onClick: () => void }[] = [];
  if (newest) {
    chips.push({
      icon: ArrowRight, verb: "Resume", obj: newest.company_name ?? hostOf(newest.primary_url),
      onClick: () => navigate(`/profiles/${newest.profile_id}`),
    });
  }
  chips.push({ icon: ScrollText, verb: "Browse", obj: "demo library", onClick: () => navigate("/profiles") });
  if (draft) {
    chips.push({
      icon: CheckCircle2, verb: "Approve", obj: draft.plan_id_short,
      onClick: () => navigate(`/plans/${draft.plan_id}`),
    });
  }

  if (chips.length === 0) return <div className="mt-5" />;

  return (
    <div className="flex flex-wrap justify-center gap-2.5 mt-5">
      {chips.map((c, i) => (
        <button
          key={i}
          type="button"
          onClick={c.onClick}
          className="hover-wash inline-flex cursor-pointer items-center gap-2 h-[34px] px-3.5 rounded-full border border-[var(--color-border)] text-[12.5px] text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:border-[color:var(--color-accent-border)]"
        >
          <c.icon size={13} className="text-[var(--color-text-faint)]" />
          <b className="font-semibold text-[var(--color-text)]">{c.verb}</b>
          <span className="font-mono text-[11.5px] opacity-85 truncate max-w-[160px]">{c.obj}</span>
        </button>
      ))}
    </div>
  );
}

// ─── KPI band — 4 thin gradient cards ───────────────────────────────

function KpiBand({ summary, buildsTotal }: { summary?: DashboardSummary; buildsTotal?: number }) {
  const navigate = useNavigate();
  const cards: {
    label: string; value: string; tone: string; icon: ComponentType<{ size?: number; className?: string }>;
    spark: number[]; to: string;
  }[] = [
    { label: "Company Profiles", value: fmtNum(summary?.profiles_total),      tone: "var(--color-info)",   icon: ScrollText,    spark: [2, 3, 5, 4, 6, 7, 8],  to: "/profiles" },
    { label: "Demo Plans",       value: fmtNum(summary?.plans_total),         tone: "var(--color-accent)", icon: ClipboardList, spark: [4, 6, 5, 8, 7, 9, 12], to: "/plans" },
    { label: "Demo Builds",      value: fmtNum(buildsTotal),                  tone: "var(--color-signal)", icon: Hammer,        spark: [2, 4, 3, 5, 4, 6, 7],  to: "/new" },
    { label: "Activity Log",     value: fmtNum(summary?.business_events_total), tone: "var(--color-accent)", icon: Database,    spark: [5, 7, 6, 9, 8, 11, 10, 13], to: "/audit" },
  ];
  return (
    <div className="grid grid-cols-2 lg:grid-cols-4 gap-3.5">
      {cards.map((k) => (
        <button
          key={k.label}
          type="button"
          onClick={() => navigate(k.to)}
          title={`View ${k.label.toLowerCase()}`}
          className="group relative isolate overflow-hidden text-left rounded-2xl px-4 py-3.5 cursor-pointer transition-[transform,box-shadow] duration-150 hover:-translate-y-0.5 hover:shadow-[var(--shadow-md)] focus-visible:outline-none focus-visible:-translate-y-0.5"
          style={{
            // tone exposed as a var so the hover ring can reference it
            "--tone": k.tone,
            background: `linear-gradient(135deg, var(--color-canvas-elev1), color-mix(in srgb, ${k.tone} 13%, var(--color-canvas-elev1)))`,
            border: `1px solid color-mix(in srgb, ${k.tone} 20%, var(--color-border))`,
          } as CSSProperties}
        >
          {/* hover ring + watery accent wash — clear "this is clickable" cue */}
          <span
            aria-hidden="true"
            className="pointer-events-none absolute inset-0 rounded-2xl opacity-0 transition-opacity duration-150 group-hover:opacity-100 group-focus-visible:opacity-100"
            style={{
              boxShadow: "inset 0 0 0 1.5px color-mix(in srgb, var(--tone) 55%, transparent)",
              background: "color-mix(in srgb, var(--tone) 9%, transparent)",
            }}
          />
          <div className="flex items-center justify-between">
            <span className="text-[11px] font-mono uppercase tracking-[0.08em]" style={{ color: k.tone, opacity: 0.92 }}>
              {k.label}
            </span>
            <span
              className="grid place-items-center w-[26px] h-[26px] rounded-lg transition-transform duration-150 group-hover:scale-105"
              style={{ color: k.tone, background: `color-mix(in srgb, ${k.tone} 16%, transparent)` }}
            >
              <k.icon size={14} />
            </span>
          </div>
          <div className="flex items-end justify-between mt-2 gap-2">
            <span className="text-[27px] font-semibold tracking-[-0.02em] tabular-nums">{k.value}</span>
            <MiniSpark data={k.spark} color={k.tone} />
          </div>
        </button>
      ))}
    </div>
  );
}

function MiniSpark({ data, color }: { data: number[]; color: string }) {
  const w = 64, h = 24;
  const mx = Math.max(...data), mn = Math.min(...data), rng = mx - mn || 1;
  const pts: [number, number][] = data.map((v, i) => [
    (i / (data.length - 1)) * w,
    h - ((v - mn) / rng) * (h - 5) - 2.5,
  ]);
  return (
    <svg width={w} height={h} fill="none" aria-hidden="true" className="shrink-0 overflow-visible">
      <path d={smoothPath(pts)} stroke={color} strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" opacity={0.95} />
    </svg>
  );
}

/** Smooth (Catmull-Rom → cubic bézier) path through points, so sparklines
 *  read as flowing trend lines rather than jagged polylines. */
function smoothPath(pts: [number, number][]): string {
  if (pts.length < 2) return pts.length ? `M${pts[0][0]},${pts[0][1]}` : "";
  const t = 0.2; // smoothing tension
  const out = [`M${pts[0][0].toFixed(1)},${pts[0][1].toFixed(1)}`];
  for (let i = 0; i < pts.length - 1; i++) {
    const p0 = pts[i - 1] ?? pts[i];
    const p1 = pts[i];
    const p2 = pts[i + 1];
    const p3 = pts[i + 2] ?? p2;
    const c1x = p1[0] + (p2[0] - p0[0]) * t;
    const c1y = p1[1] + (p2[1] - p0[1]) * t;
    const c2x = p2[0] - (p3[0] - p1[0]) * t;
    const c2y = p2[1] - (p3[1] - p1[1]) * t;
    out.push(`C${c1x.toFixed(1)},${c1y.toFixed(1)} ${c2x.toFixed(1)},${c2y.toFixed(1)} ${p2[0].toFixed(1)},${p2[1].toFixed(1)}`);
  }
  return out.join(" ");
}

// ─── Continue — recent accounts to resume ───────────────────────────

const TONE_BG: Record<string, string> = {
  ready: "var(--color-accent)", "in-review": "var(--color-warning)",
  draft: "var(--color-text-muted)", researching: "var(--color-info)",
};

function ContinueList({
  profiles, plans, loading,
}: { profiles: ProfileSummary[]; plans: PlanSummary[]; loading: boolean }) {
  const navigate = useNavigate();
  const plansByProfile = useMemo(() => {
    const m = new Map<string, PlanSummary[]>();
    for (const p of plans) {
      const arr = m.get(p.source_profile_id) ?? [];
      arr.push(p);
      m.set(p.source_profile_id, arr);
    }
    return m;
  }, [plans]);

  const rows = useMemo(
    () => [...profiles]
      .sort((a, b) => new Date(b.created_at).getTime() - new Date(a.created_at).getTime())
      .slice(0, 5),
    [profiles],
  );

  return (
    <div>
      <div className="mb-1.5 flex items-baseline justify-between">
        <span className="text-[11px] font-mono uppercase tracking-[0.08em] text-[var(--color-accent)]">Continue</span>
        {rows.length > 0 && (
          <span className="font-mono text-[11px] tabular-nums text-[var(--color-text-faint)]">{rows.length}</span>
        )}
      </div>
      {loading ? (
        <div className="py-6 text-sm text-[var(--color-text-faint)]">Loading…</div>
      ) : rows.length === 0 ? (
        <div className="py-6 text-sm text-[var(--color-text-faint)]">
          No researched companies yet. Start one from the command bar above.
        </div>
      ) : (
        <div>
          {rows.map((p) => {
            const status = deriveDemoStatus(p, plansByProfile.get(p.profile_id) ?? []);
            const name = p.company_name ?? hostOf(p.primary_url);
            const initial = (name || "?").trim()[0]?.toUpperCase() ?? "?";
            return (
              <button
                key={p.profile_id}
                type="button"
                onClick={() => {
                  if (p.pending && p.pipeline_id) navigate(`/pipelines/${p.pipeline_id}`);
                  else navigate(`/profiles/${p.profile_id}`);
                }}
                className="hover-wash group flex w-full cursor-pointer items-center gap-3.5 px-3 py-3.5 -mx-1 border-b border-[var(--color-border)] text-left rounded-lg"
              >
                <span
                  className="grid place-items-center w-[34px] h-[34px] shrink-0 rounded-[9px] text-[13px] font-bold transition-transform duration-150 group-hover:scale-105"
                  style={{ color: TONE_BG[status.tone], background: `color-mix(in srgb, ${TONE_BG[status.tone]} 14%, transparent)` }}
                >
                  {p.pending ? <Loader2 size={14} className="animate-spin" /> : initial}
                </span>
                <div className="min-w-0 flex-1">
                  <div className="text-[14px] font-semibold text-[var(--color-text)] truncate transition-colors group-hover:text-[var(--color-accent)]">{name}</div>
                  <div className="text-[12.5px] text-[var(--color-text-muted)] truncate mt-0.5">
                    {status.label} · <span className="font-mono">{hostOf(p.primary_url)}</span>
                  </div>
                </div>
                <span className="font-mono text-[11px] text-[var(--color-text-faint)] shrink-0">{relTime(p.created_at)}</span>
                <ChevronRight size={15} className="shrink-0 text-[var(--color-text-faint)] transition-transform duration-150 group-hover:translate-x-0.5 group-hover:text-[var(--color-text-muted)]" />
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}

// ─── Needs you — the attention queue ────────────────────────────────

interface NeedRow {
  icon: ComponentType<{ size?: number; className?: string; style?: CSSProperties }>;
  tone: string;
  lead: string;
  rest: string;
  onClick: () => void;
}

function NeedsYou({
  plans, profiles, pipelines,
}: { plans: PlanSummary[]; profiles: ProfileSummary[]; pipelines: PipelineSummary[] }) {
  const navigate = useNavigate();
  const rows: NeedRow[] = [];

  for (const p of plans) {
    if (p.pending) continue;
    if (p.review_state === "draft" || p.review_state === "se_reviewed") {
      rows.push({
        icon: CheckCircle2, tone: "var(--color-accent)",
        lead: "Approve", rest: `${p.plan_id_short} · ${p.review_state.replace(/_/g, " ")}`,
        onClick: () => navigate(`/plans/${p.plan_id}`),
      });
    }
  }
  for (const pr of profiles) {
    if (!pr.pending && pr.synthesized_flag_count > 0) {
      rows.push({
        icon: AlertTriangle, tone: "var(--color-warning)",
        lead: "Verify", rest: `${pr.company_name ?? hostOf(pr.primary_url)} · ${pr.synthesized_flag_count} synthetic`,
        onClick: () => navigate(`/profiles/${pr.profile_id}`),
      });
    }
  }
  for (const pl of pipelines) {
    if (pl.status === "failed") {
      rows.push({
        icon: AlertCircle, tone: "var(--color-danger)",
        lead: "Retry", rest: `${hostOf(pl.url)} build failed`,
        onClick: () => navigate(`/pipelines/${pl.pipeline_id}`),
      });
    }
  }

  const shown = rows.slice(0, 6);

  return (
    <div>
      <div className="mb-1.5 flex items-baseline justify-between">
        <span className="text-[11px] font-mono uppercase tracking-[0.08em] text-[var(--color-accent)]">Action items</span>
        {shown.length > 0 && (
          <span className="font-mono text-[11px] tabular-nums text-[var(--color-text-faint)]">{rows.length}</span>
        )}
      </div>
      {shown.length === 0 ? (
        <div className="py-6 text-sm text-[var(--color-text-faint)]">No outstanding items — your workspace is up to date.</div>
      ) : (
        <div>
          {shown.map((r, i) => (
            <button
              key={i}
              type="button"
              onClick={r.onClick}
              className="hover-wash group flex w-full cursor-pointer items-center gap-3 px-3 py-3.5 -mx-1 border-b border-[var(--color-border)] text-left rounded-lg"
            >
              <span
                className="grid place-items-center w-[30px] h-[30px] shrink-0 rounded-lg transition-transform duration-150 group-hover:scale-105"
                style={{ color: r.tone, background: `color-mix(in srgb, ${r.tone} 13%, transparent)` }}
              >
                <r.icon size={15} />
              </span>
              <div className="min-w-0 flex-1 text-[13.5px]">
                <b className="font-semibold" style={{ color: r.tone }}>{r.lead}</b>
                <span className="text-[var(--color-text-muted)]"> · {r.rest}</span>
              </div>
              <ArrowRight size={14} className="shrink-0 text-[var(--color-text-faint)] transition-transform duration-150 group-hover:translate-x-0.5 group-hover:text-[var(--color-text-muted)]" />
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

// ─── Live dock — only when a demo is running ────────────────────────

function LiveDock() {
  const qc = useQueryClient();
  const navigate = useNavigate();
  const sessions = useQuery({
    queryKey: ["demo-sessions"],
    queryFn: listDemoSessions,
    refetchInterval: 5_000,
  });
  const [stopping, setStopping] = useState(false);
  const live = (sessions.data ?? []).find(
    (s: DemoSession) => s.status === "live" || s.status === "starting",
  );
  if (!live) return null;

  const host = live.company ?? hostOf(live.url);
  const expiry = fmtDurationShort(live.seconds_until_expiry);

  async function stop() {
    if (!live) return;
    setStopping(true);
    try {
      await stopDemoSession(live.plan_id);
      qc.invalidateQueries({ queryKey: ["demo-sessions"] });
    } finally {
      setStopping(false);
    }
  }

  return (
    <div className="fixed bottom-5 left-1/2 z-40 -translate-x-1/2 flex items-center gap-3.5 rounded-2xl border border-[var(--color-border-strong)] bg-[var(--color-canvas-elev2)] py-2.5 pl-4 pr-3 shadow-[var(--shadow-lg)]">
      <LiveInCloud size={18} />
      <div className="leading-tight">
        <div className="text-[13px] font-semibold">
          {host}{" "}
          <span className="font-mono font-normal text-[var(--color-grafana)]">
            {live.health === "starting" ? "starting…" : "live"}
          </span>
        </div>
        <div className="font-mono text-[11px] text-[var(--color-text-faint)]">auto-stop {expiry}</div>
      </div>
      <div className="flex items-center gap-2 ml-1">
        <Button size="sm" variant="secondary" onClick={() => navigate(`/plans/${live.plan_id}`)}>
          Open <ArrowRight size={13} />
        </Button>
        <Button size="sm" variant="danger" onClick={() => void stop()} disabled={stopping}>
          {stopping ? <Loader2 size={11} className="animate-spin" /> : <Square size={10} />} Stop
        </Button>
      </div>
    </div>
  );
}

// ─── Orphan cleanup (preserved — self-hides when empty) ─────────────

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
    mutationFn: (uid: string) => { setBusyUid(uid); return deleteOrphanFolder(uid); },
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["orphans"] }); setBusyUid(null); setError(null); },
    onError: (e: Error) => { setError(e.message); setBusyUid(null); },
  });

  const items = orphans.data ?? [];
  if (orphans.isLoading || (orphans.isFetched && items.length === 0)) return null;

  async function deleteAll() {
    setError(null);
    for (const o of items) await deleteMut.mutateAsync(o.uid).catch(() => {});
  }

  return (
    <Card className="p-5 border-[var(--color-warning)]/30 bg-[var(--color-warning)]/5">
      <div className="flex items-start gap-3 mb-3">
        <AlertTriangle className="text-[var(--color-warning)] shrink-0 mt-0.5" size={18} />
        <div className="flex-1">
          <h2 className="text-sm font-medium">Orphan Grafana folders ({items.length})</h2>
          <p className="text-xs text-[var(--color-text-muted)] mt-1 max-w-2xl">
            These <code className="font-mono">clarion-*</code> folders exist in your stack
            but their plan is no longer in the DB. Deleting cascades the folder + its
            dashboards + its alert rules.
          </p>
        </div>
        <Button size="sm" variant="danger" onClick={() => void deleteAll()} disabled={deleteMut.isPending}>
          {deleteMut.isPending ? <Loader2 size={12} className="animate-spin" /> : <Trash2 size={12} />}
          Delete all
        </Button>
      </div>
      {error && <div className="text-xs text-[var(--color-danger)] mb-2">{error}</div>}
      <div className="space-y-1">
        {items.map((o) => (
          <OrphanRow key={o.uid} orphan={o} busy={busyUid === o.uid} onDelete={() => deleteMut.mutate(o.uid)} />
        ))}
      </div>
    </Card>
  );
}

function OrphanRow({ orphan, busy, onDelete }: { orphan: OrphanFolder; busy: boolean; onDelete: () => void }) {
  return (
    <div className="flex items-center gap-3 px-2 py-2 text-sm border-b border-[var(--color-border)] last:border-0">
      <div className="flex-1 min-w-0">
        <div className="font-medium truncate">{orphan.title}</div>
        <div className="text-xs text-[var(--color-text-faint)] flex items-center gap-2 mt-0.5">
          <span className="font-mono truncate">{orphan.uid}</span>
          {orphan.plan_id && <span className="text-[var(--color-text-muted)]">&middot; plan {orphan.plan_id.slice(0, 8)}</span>}
          <span className="text-[var(--color-warning)]">&middot; {orphan.reason}</span>
        </div>
      </div>
      {orphan.url && (
        <a href={orphan.url} target="_blank" rel="noreferrer" className="text-xs text-[var(--color-text-muted)] hover:text-[var(--color-text)] inline-flex items-center gap-1">
          view <ExternalLink size={10} />
        </a>
      )}
      <Button size="sm" variant="danger" onClick={onDelete} disabled={busy}>
        {busy ? <Loader2 size={12} className="animate-spin" /> : <Trash2 size={12} />} Delete
      </Button>
    </div>
  );
}

// ─── Helpers ────────────────────────────────────────────────────────

function SectionLabel({ children }: { children: string }) {
  return (
    <div className="mb-4 text-[11px] font-mono uppercase tracking-[0.12em] text-[var(--color-text-faint)]">
      {children}
    </div>
  );
}

function fmtNum(n: number | undefined): string {
  if (n === undefined) return "…";
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2).replace(/\.?0+$/, "")}M`;
  if (n >= 10_000) return `${(n / 1_000).toFixed(1).replace(/\.0$/, "")}K`;
  return n.toLocaleString();
}

function relTime(iso: string): string {
  const s = Math.round((Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60) return `${s}s ago`;
  const m = Math.round(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.round(h / 24)}d ago`;
}

function fmtDurationShort(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) { const rs = s % 60; return rs ? `${m}m ${rs}s` : `${m}m`; }
  const h = Math.floor(m / 60), rm = m % 60;
  return rm ? `${h}h ${rm}m` : `${h}h`;
}
