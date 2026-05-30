/**
 * AddProfileModal, first-class replacement for the native window.prompt
 * that used to handle "Add profile" on the Profiles page.
 *
 * Visual treatment mirrors the Dashboard HeroBuildCard (mono `https://`
 * prefix on the URL input, preset chips with eta hints) but in a
 * contained modal, so the action surface reads as a focused intent
 * rather than a fresh page.
 *
 * Behavior:
 *   - Type a URL + pick a preset → click "Research".
 *   - We call `pipeline.start(...)` directly (no /new bounce) and
 *     close the modal; the parent typically navigates to /new where
 *     PipelineRunView renders the live build.
 *   - Esc or backdrop click cancels.
 *   - First field gets autofocus on open.
 *
 * The parent is responsible for navigating after `onSubmitted` fires,  * keeps this component agnostic of routing decisions.
 */
import { useEffect, useRef, useState, type FormEvent } from "react";
import { Check, Globe, X, Loader2, FileSearch, Rocket } from "lucide-react";

import { Button } from "@/components/Button";
import { cn } from "@/lib/cn";
import { usePipeline } from "@/lib/PipelineContext";

export type AddProfilePreset = "smoke" | "demo" | "auto" | "stress";

const PRESETS: {
  value: AddProfilePreset;
  title: string;
  sub: string;
  eta: string;
  blurb: string;
}[] = [
  { value: "smoke",  title: "Smoke",  sub: "500/day",  eta: "2m",     blurb: "Fastest iteration. ~2-3 min build." },
  { value: "demo",   title: "Demo",   sub: "2.5K/day", eta: "6m",     blurb: "Default walk-through volume." },
  { value: "auto",   title: "Auto",   sub: "scaled",   eta: "varies", blurb: "Let the planner auto-scale." },
  { value: "stress", title: "Stress", sub: "25K/day",  eta: "12m",    blurb: "Pressure-tests ingest. Burns quota." },
];

function volumeForPreset(p: AddProfilePreset): number | undefined {
  switch (p) {
    case "smoke":  return    500;
    case "demo":   return  2_500;
    case "stress": return 25_000;
    case "auto":   return undefined;
  }
}

/** Which "intent" the modal will submit with: stop after research
 *  (profile-only) or run the whole pipeline through KG publish. */
type SubmitIntent = "research_only" | "full_build";

/** Best-effort guess: given what the user typed, return a probable URL.
 *
 *   "grafana"            -> { url: "https://grafana.com",           source: "guess" }
 *   "Grafana Labs"       -> { url: "https://grafanalabs.com",       source: "guess" }
 *   "grafana.com"        -> { url: "https://grafana.com",           source: "typed" }
 *   "https://acme.com/x" -> { url: "https://acme.com/x",             source: "typed" }
 *
 * The `source` field tells the UI whether to show a "we guessed this,
 * looks right?" confirmation chip (`guess`) or just trust the user
 * (`typed`). Returns null when there isn't enough to work with.
 */
function resolveCompanyInput(raw: string): { url: string; source: "typed" | "guess" } | null {
  const trimmed = raw.trim();
  if (!trimmed) return null;
  // Looks like a URL? Dot, slash, or scheme.
  if (/[./]/.test(trimmed) || /^https?:\/\//i.test(trimmed)) {
    const url = /^https?:\/\//i.test(trimmed) ? trimmed : `https://${trimmed}`;
    return { url, source: "typed" };
  }
  // Otherwise it's a company name. Slugify (lowercase alphanumerics
  // only) and try `.com`. Keeps things predictable; no surprise lookups
  // against a third-party search API.
  const slug = trimmed.toLowerCase().replace(/[^a-z0-9]/g, "");
  if (!slug) return null;
  return { url: `https://${slug}.com`, source: "guess" };
}

export function AddProfileModal({
  open, onClose, onSubmitted,
}: {
  open: boolean;
  onClose: () => void;
  /** Called once a build has been queued. Parent typically navigates
   *  to /new so the PipelineRunView renders the live tail. */
  onSubmitted: (pipelineId: string) => void;
}) {
  const pipeline = usePipeline();
  const [url, setUrl] = useState("");
  const [preset, setPreset] = useState<AddProfilePreset>("demo");
  const [submitting, setSubmitting] = useState<SubmitIntent | null>(null);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  // Esc to close. Reset state when the modal opens.
  useEffect(() => {
    if (!open) return;
    setUrl("");
    setPreset("demo");
    setError(null);
    setSubmitting(null);
    // Autofocus URL field on next tick (after the modal animates in).
    requestAnimationFrame(() => inputRef.current?.focus());
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  const resolved = resolveCompanyInput(url);
  const canSubmit = resolved !== null;

  /** Submit with one of the two intents. Research-only stops after the
   *  research phase (no plan, no Cloud-side provisioning); full-build
   *  runs the whole research-to-kg-publish flow. */
  async function submit(intent: SubmitIntent) {
    if (!resolved || submitting) return;
    setSubmitting(intent);
    setError(null);
    try {
      const newId = await pipeline.start({
        url: resolved.url,
        days: 1,
        volume_per_day: intent === "full_build" ? volumeForPreset(preset) : undefined,
        stop_after_phase: intent === "research_only" ? "research" : undefined,
      });
      onSubmitted(newId);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
      setSubmitting(null);
    }
  }

  function onFormSubmit(e: FormEvent) {
    e.preventDefault();
    // Enter on the form defaults to the full-build action; most users
    // hit Enter expecting "do the thing" rather than a research-only
    // tease. The dedicated "Just add profile" button is still one click.
    void submit("full_build");
  }

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="add-profile-title"
      className="fixed inset-0 z-50 flex items-start justify-center p-4 pt-[10vh] bg-black/60 backdrop-blur-sm"
      onClick={onClose}
    >
      <div
        onClick={(e) => e.stopPropagation()}
        className={cn(
          "relative w-full max-w-[560px] rounded-2xl overflow-hidden",
          "border border-[var(--color-border)] bg-[var(--color-canvas-elev1)]",
          "shadow-2xl",
        )}
        style={{
          background:
            "radial-gradient(120% 80% at 100% 0%, var(--color-accent-bg), transparent 60%), var(--color-canvas-elev1)",
        }}
      >
        <header className="flex items-start justify-between gap-4 px-6 pt-5 pb-4">
          <div>
            <div className="flex items-center gap-2 text-[11px] font-mono uppercase tracking-[0.16em] text-[var(--color-accent)]">
              <span aria-hidden="true" className="inline-block w-5 h-px bg-[var(--color-accent)]" />
              New profile
            </div>
            <h2
              id="add-profile-title"
              className="mt-2 text-[22px] font-semibold tracking-tight leading-tight text-[var(--color-text)]"
            >
              Research a company
            </h2>
            <p className="text-sm text-[var(--color-text-muted)] mt-1">
              Type a name or URL. We&rsquo;ll figure out the rest and add it to your library.
            </p>
          </div>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close"
            className="p-1.5 -mt-1 -mr-2 rounded-md text-[var(--color-text-faint)] hover:text-[var(--color-text)] hover:bg-white/[0.05]"
          >
            <X size={16} aria-hidden="true" />
          </button>
        </header>

        <form onSubmit={onFormSubmit} className="px-6 pb-5 space-y-4">
          <div>
            <label
              htmlFor="add-profile-input"
              className="text-[11px] font-mono uppercase tracking-wider text-[var(--color-text-muted)] mb-1.5 block"
            >
              Company name or URL
            </label>
            <div
              className={cn(
                "flex items-center gap-2 p-1.5 rounded-[10px]",
                "bg-[var(--color-canvas)] border border-[var(--color-border-strong)]",
                "focus-within:border-[color:var(--color-accent-border)] transition-colors",
              )}
            >
              <Globe
                size={16}
                aria-hidden="true"
                className="ml-2 mr-0.5 text-[var(--color-text-faint)] shrink-0"
              />
              <input
                ref={inputRef}
                id="add-profile-input"
                type="text"
                autoComplete="off"
                spellCheck={false}
                placeholder="Grafana  or  grafana.com"
                value={url}
                onChange={(e) => setUrl(e.target.value)}
                aria-label="Company name or URL"
                className={cn(
                  "flex-1 bg-transparent outline-none border-0",
                  "text-base text-[var(--color-text)]",
                  "placeholder:text-[var(--color-text-faint)]",
                  "py-2 min-w-0",
                )}
              />
            </div>
            {/* Resolved URL preview. We always show the canonical URL
                we're about to research so the user can confirm before
                committing. For typed-URL inputs the chip just echoes
                what they typed (with the https:// prefix normalized);
                for name inputs it shows the guessed `.com` so the user
                can override if their company uses a different TLD. */}
            <div className="mt-2 min-h-[24px] flex items-center gap-2">
              {resolved ? (
                <span
                  className={cn(
                    "inline-flex items-center gap-2 h-7 pl-2 pr-3 rounded-full text-xs",
                    "bg-[var(--color-accent-bg)] border border-[color:var(--color-accent-border)]",
                    "text-[var(--color-accent)]",
                  )}
                >
                  <span
                    aria-hidden="true"
                    className="inline-flex items-center justify-center w-4 h-4 rounded-full bg-[var(--color-accent)] text-[var(--color-on-accent)]"
                  >
                    <Check size={10} />
                  </span>
                  <span>
                    {resolved.source === "guess" ? "We'll research" : "Researching"}
                  </span>
                  <span className="font-mono text-[var(--color-text)]">
                    {resolved.url}
                  </span>
                </span>
              ) : (
                <span className="text-[11px] text-[var(--color-text-faint)]">
                  Type a name (Grafana), a domain (grafana.com), or a full URL.
                </span>
              )}
            </div>
            <div className="text-[11px] text-[var(--color-text-faint)] mt-1.5">
              If <code className="font-mono">RESEARCH_ALLOWED_HOSTS</code> is set
              in <code className="font-mono">.env</code>, the host must match
              one of its patterns.
            </div>
          </div>

          <div>
            <div className="text-[11px] font-mono uppercase tracking-wider text-[var(--color-text-muted)] mb-1.5">
              Build size
              <span className="ml-2 normal-case text-[10px] font-sans text-[var(--color-text-faint)] tracking-normal">
                applies only to &ldquo;Research &amp; build&rdquo;
              </span>
            </div>
            <div
              role="radiogroup"
              aria-label="Volume preset"
              className="grid grid-cols-4 gap-2"
            >
              {PRESETS.map((p) => {
                const active = preset === p.value;
                return (
                  <button
                    key={p.value}
                    type="button"
                    role="radio"
                    aria-checked={active}
                    onClick={() => setPreset(p.value)}
                    title={p.blurb}
                    className={cn(
                      "px-2 py-2 rounded-md border text-left transition-colors",
                      active
                        ? "bg-[var(--color-accent-bg)] border-[color:var(--color-accent-border)] text-[var(--color-accent)]"
                        : "bg-[var(--color-canvas-elev2)]/40 border-[var(--color-border)] text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:border-[var(--color-border-strong)]",
                    )}
                  >
                    <div className="text-xs font-medium">{p.title}</div>
                    <div
                      className={cn(
                        "text-[10px] font-mono mt-0.5",
                        active ? "opacity-80" : "text-[var(--color-text-faint)]",
                      )}
                    >
                      {p.sub} · {p.eta}
                    </div>
                  </button>
                );
              })}
            </div>
          </div>

          {error && (
            <div className="text-xs text-[var(--color-danger)] flex items-start gap-1.5 px-1">
              <span aria-hidden="true">!</span>
              <span>{error}</span>
            </div>
          )}

          {/* Two intents. "Just add profile" runs research and stops,               gives the SE a CompanyProfile in the library without
              spinning up dashboards / KG / Cloud entities. "Research
              & build" runs the whole flow end-to-end. */}
          <div className="pt-2 grid grid-cols-1 sm:grid-cols-[1fr_1fr] gap-2">
            <Button
              type="button"
              variant="secondary"
              onClick={() => void submit("research_only")}
              disabled={!canSubmit || submitting !== null}
              className="h-11 justify-center"
              title="Run only the research phase. Stores a CompanyProfile and stops. No plan, no Cloud-side provisioning."
            >
              {submitting === "research_only" ? (
                <>
                  <Loader2 size={14} className="animate-spin" /> Researching&hellip;
                </>
              ) : (
                <>
                  <FileSearch size={14} /> Just add profile
                </>
              )}
            </Button>
            <Button
              type="submit"
              variant="primary"
              disabled={!canSubmit || submitting !== null}
              className="h-11 justify-center"
              title="Research, plan, provision, and KG publish. Full end-to-end build using the selected build size."
            >
              {submitting === "full_build" ? (
                <>
                  <Loader2 size={14} className="animate-spin" /> Starting&hellip;
                </>
              ) : (
                <>
                  <Rocket size={14} /> Research &amp; build
                </>
              )}
            </Button>
          </div>

          <div className="flex justify-center pt-1">
            <button
              type="button"
              onClick={onClose}
              disabled={submitting !== null}
              className="text-xs text-[var(--color-text-faint)] hover:text-[var(--color-text-muted)] disabled:opacity-40"
            >
              Cancel
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}
