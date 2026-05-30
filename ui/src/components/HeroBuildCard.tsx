/**
 * HeroBuildCard, the Dashboard's prominent "start a build" surface.
 *
 * Visual spec (claude_design_assets/clarion-redesign-v2/dashboard.md):
 *   - Full-width, ~280px tall, --radius-lg, soft radial halo of
 *     --color-accent-bg in the top-right corner (CSS gradient, no image).
 *   - Eyebrow mono "NEW BUILD" → 32px headline → URL input + Build button
 *     → 4 preset chips (Smoke / Demo / Auto / Stress).
 *   - Active preset: --color-accent-bg + --color-accent-border.
 *
 * Behavior (PR 2 scope):
 *   - Owns the URL + preset selection locally.
 *   - On submit, calls the parent's `onBuild(url, preset)`. The Dashboard
 *     wires that to a `/new?prefill_url=...&prefill_preset=...` deep-link
 *     so the canonical start-pipeline flow stays in NewDemo.
 *   - A later PR will reuse this card inside NewDemo itself and call
 *     `usePipeline().start()` directly. For now this is a navigation
 *     affordance, not a start-pipeline trigger.
 *
 * The form is plain HTML, no react-hook-form, no schema validation.
 * The validation that matters (URL reachable, planner can profile it)
 * lives in the orchestrator on the server side, where it has to be
 * anyway. Client-side we just require a non-empty input.
 */
import { Globe, Sparkles } from "lucide-react";
import { useState, type FormEvent } from "react";

import { Button } from "@/components/Button";
import { cn } from "@/lib/cn";

export type BuildPreset = "smoke" | "demo" | "auto" | "stress";

export type HeroBuildCardProps = {
  /** Optional URL to pre-fill in the input (no protocol, just host/path). */
  defaultUrl?: string;
  /** Defaults to "demo", the most common SE choice. */
  defaultPreset?: BuildPreset;
  /** Called when the user submits the form. Caller decides whether this
   *  navigates to /new with prefill params, or starts a pipeline directly. */
  onBuild: (url: string, preset: BuildPreset) => void;
};

// `eta` shows the expected wall-clock cost of the preset; rendered as
// a small mono caption BELOW each chip per the v2 mockup. Hard-coded
// (not computed from real history), the SE only needs a rough sense
// of "fast vs slow" before clicking Build, not a real estimate.
const PRESETS: {
  value: BuildPreset;
  title: string;
  sub: string;
  eta: string;
  blurb: string;
}[] = [
  { value: "smoke",  title: "Smoke",  sub: "500/day",  eta: "2m",     blurb: "Fastest iteration. ~2-3 min build." },
  { value: "demo",   title: "Demo",   sub: "2.5K/day", eta: "6m",     blurb: "Default walk-through volume." },
  { value: "auto",   title: "Auto",   sub: "scaled",   eta: "varies", blurb: "Let the planner auto-scale." },
  { value: "stress", title: "Stress", sub: "25K/day",  eta: "12m",    blurb: "Pressure-test ingest. Burns quota." },
];

export function HeroBuildCard({
  defaultUrl = "",
  defaultPreset = "demo",
  onBuild,
}: HeroBuildCardProps) {
  const [url, setUrl] = useState(defaultUrl);
  const [preset, setPreset] = useState<BuildPreset>(defaultPreset);

  function handleSubmit(e: FormEvent) {
    e.preventDefault();
    const trimmed = url.trim();
    if (!trimmed) return;
    onBuild(trimmed, preset);
  }

  // The halo lives in the card's ::before via inline style so the
  // gradient stop honors the active theme accent (it reads
  // --color-accent-bg directly, which the [data-theme="light"] override
  // re-points at the deeper light-theme teal automatically).
  const haloStyle: React.CSSProperties = {
    background:
      "radial-gradient(140% 100% at 100% 0%, var(--color-accent-bg), transparent 60%), var(--color-canvas-elev1)",
  };

  return (
    <section
      aria-label="Start a new demo build"
      className="relative overflow-hidden rounded-2xl border border-[var(--color-border)] p-7"
      style={haloStyle}
    >
      {/* Eyebrow, small horizontal teal mark + mono caps label.
          The mark is a "title-bar tick" the v2 mockup uses on every
          section eyebrow for a consistent rhythm. */}
      <div className="flex items-center gap-2 text-[11px] font-medium font-mono uppercase tracking-[0.16em] text-[var(--color-accent)]">
        <span
          aria-hidden="true"
          className="inline-block w-5 h-px bg-[var(--color-accent)]"
        />
        New build
      </div>
      <h1 className="mt-3 text-[32px] font-semibold tracking-tight leading-tight text-[var(--color-text)]">
        What are we showing today?
      </h1>

      <form onSubmit={handleSubmit} className="mt-5">
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
          <span
            aria-hidden="true"
            className="px-1 font-mono text-sm text-[var(--color-text-faint)] select-none"
          >
            https://
          </span>
          <input
            type="text"
            inputMode="url"
            autoComplete="off"
            spellCheck={false}
            placeholder="grafana.com"
            value={url}
            onChange={(e) => setUrl(e.target.value)}
            aria-label="Prospect URL"
            className={cn(
              "flex-1 bg-transparent outline-none border-0",
              "font-mono text-base text-[var(--color-text)]",
              "placeholder:text-[var(--color-text-faint)]",
              "py-2 min-w-0",
            )}
          />
          <Button
            type="submit"
            variant="primary"
            disabled={!url.trim()}
            className="h-11 px-5"
          >
            <Sparkles size={14} aria-hidden="true" />
            Build
          </Button>
        </div>
      </form>

      <div
        role="radiogroup"
        aria-label="Volume preset"
        className="mt-4 flex flex-wrap gap-2"
      >
        {PRESETS.map((p) => {
          const active = preset === p.value;
          return (
            <div key={p.value} className="flex flex-col items-start">
              <button
                type="button"
                role="radio"
                aria-checked={active}
                onClick={() => setPreset(p.value)}
                title={p.blurb}
                className={cn(
                  "flex items-center gap-2 px-3 h-9 rounded-md border text-xs transition-colors",
                  active
                    ? "bg-[var(--color-accent-bg)] border-[color:var(--color-accent-border)] text-[var(--color-accent)]"
                    : "bg-[var(--color-canvas-elev2)]/40 border-[var(--color-border)] text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:border-[var(--color-border-strong)]",
                )}
              >
                <span className="font-medium">{p.title}</span>
                <span
                  className={cn(
                    "font-mono text-[10px]",
                    active ? "opacity-80" : "text-[var(--color-text-faint)]",
                  )}
                >
                  {p.sub}
                </span>
                {/* Trailing dot per mockup, visual rhythm marker that
                    separates the chip's label from the duration below. */}
                <span
                  aria-hidden="true"
                  className={cn(
                    "ml-0.5 font-mono text-[10px]",
                    active ? "text-[var(--color-accent)]" : "text-[var(--color-text-faint)]",
                  )}
                >
                  ·
                </span>
              </button>
              {/* Wall-clock estimate for the preset. Active chip's ETA
                  is teal, same visual cue as the chip itself. */}
              <span
                className={cn(
                  "mt-1 ml-3 font-mono text-[10px] tabular-nums",
                  active ? "text-[var(--color-accent)]" : "text-[var(--color-text-faint)]",
                )}
                aria-hidden="true"
              >
                {p.eta}
              </span>
            </div>
          );
        })}
      </div>
    </section>
  );
}
