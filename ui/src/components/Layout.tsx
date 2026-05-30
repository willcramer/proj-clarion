import { Link, NavLink, Outlet, useLocation } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import {
  ScrollText,
  ClipboardList,
  Activity,
  History,
  Search,
  Sparkles,
  Loader2,
  Menu,
  X,
  Boxes,
  BookOpen,
  Bot,
} from "lucide-react";
import { useEffect, useState } from "react";

import { cn } from "@/lib/cn";
import { getEnv, listPipelines } from "@/lib/api";
import { CommandPalette } from "@/components/CommandPalette";
import { ClarionAssistant } from "@/components/ClarionAssistant";
import { Logo } from "@/components/Logo";
import { PipelineStatusPill } from "@/components/PipelineStatusPill";
import { UserMenu } from "@/components/UserMenu";
import { usePipeline } from "@/lib/PipelineContext";
import { useAssistant } from "@/lib/AssistantContext";

// Primary nav: the surfaces an SE hits during a live demo. The home
// page (Dashboard) is reachable via the brand tile on the left, so a
// dedicated "Dashboard" item here would be redundant. We keep the nav
// focused on the secondary surfaces the SE drills into.
const NAV = [
  { to: "/new",      label: "Builds",   icon: Sparkles      },
  { to: "/profiles", label: "Profiles", icon: ScrollText    },
  { to: "/plans",    label: "Plans",    icon: ClipboardList },
];

// Secondary nav, surfaced via UserMenu so they remain one click away
// without crowding the primary top bar. Mobile drawer also lists these
// so phones don't lose access to them.
//
// "Pipelines" used to live here but was dropped: the "Build" primary
// nav now goes to /new which is the builds list (consolidation per
// CDD). /pipelines stays as a backward-compat route in App.tsx.
export const SECONDARY_NAV = [
  { to: "/runs",  label: "Runs",  icon: Activity },
  { to: "/audit", label: "Audit", icon: History  },
  // /about explains the project + arch + observability surfaces; useful
  // in customer demos as a "what is this thing" landing surface.
  { to: "/about", label: "About", icon: Boxes    },
  // /docs/ai-obs is a six-step recipe for instrumenting any Claude SDK
  // app — copy-pasteable code blocks linked from the About page hero.
  { to: "/docs/ai-obs", label: "Docs", icon: BookOpen },
];

export function Layout() {
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const location = useLocation();
  const assistant = useAssistant();

  // ⌘K / Ctrl+K to open the command palette, ⌘J / Ctrl+J to toggle the
  // Clarion assistant drawer, + Esc to close mobile nav. Single keydown
  // handler so we don't pile up listeners, the cost of a few checks per
  // keypress is negligible vs. registering separate handlers.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setPaletteOpen((v) => !v);
      } else if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "j") {
        e.preventDefault();
        assistant.toggle();
      } else if (e.key === "Escape" && mobileNavOpen) {
        setMobileNavOpen(false);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [mobileNavOpen, assistant]);

  // Close the mobile drawer on route change. Without this, navigating
  // via the drawer leaves it open over the new page.
  useEffect(() => { setMobileNavOpen(false); }, [location.pathname]);

  return (
    <div className="min-h-screen flex flex-col">
      <TopBar
        onOpenPalette={() => setPaletteOpen(true)}
        onOpenMobileNav={() => setMobileNavOpen(true)}
      />
      {/* `id="main-content"` is the skip-link target from `index.html`.
          Keyboard / screen-reader users can jump past the persistent
          TopBar with one tab + Enter. */}
      <main
        id="main-content"
        className="flex-1 max-w-[1400px] mx-auto w-full px-4 sm:px-6 py-6 sm:py-8"
      >
        <Outlet />
      </main>
      <CommandPalette open={paletteOpen} onOpenChange={setPaletteOpen} />
      <MobileNavDrawer open={mobileNavOpen} onClose={() => setMobileNavOpen(false)} />
      {/* Global Clarion assistant, a docked right-side drawer that any
          page can open (scoped to a plan/profile/pipeline) and that the
          TopBar button + ⌘J toggle. Renders here so it overlays every
          route uniformly. */}
      <ClarionAssistant />
    </div>
  );
}

// ─── Mobile nav drawer ─────────────────────────────────────────────
//
// Slide-in drawer that mirrors the primary nav for screens narrower
// than `md`. We reuse the same NAV array as the desktop bar so any
// future addition stays in sync. Clicking a link auto-closes (handled
// in Layout's location effect). Click the backdrop or press Esc to
// close without navigating.

function MobileNavDrawer({
  open, onClose,
}: { open: boolean; onClose: () => void }) {
  // Lock body scroll while open so the drawer doesn't drag the page.
  useEffect(() => {
    if (!open) return;
    const prev = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => { document.body.style.overflow = prev; };
  }, [open]);

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Primary navigation"
      aria-hidden={!open}
      className={cn(
        "fixed inset-0 z-40 md:hidden",
        open ? "pointer-events-auto" : "pointer-events-none",
      )}
    >
      {/* Backdrop */}
      <button
        type="button"
        aria-label="Close navigation"
        onClick={onClose}
        tabIndex={open ? 0 : -1}
        className={cn(
          "absolute inset-0 bg-black/60 backdrop-blur-sm transition-opacity",
          open ? "opacity-100" : "opacity-0",
        )}
      />
      {/* Panel */}
      <aside
        className={cn(
          "absolute top-0 left-0 h-full w-72 max-w-[85vw] bg-[var(--color-canvas-elev1)] border-r border-[var(--color-border)]",
          "transition-transform duration-200 ease-out",
          open ? "translate-x-0" : "-translate-x-full",
        )}
      >
        <div className="flex items-center justify-between p-4 border-b border-[var(--color-border)]">
          <Link to="/" className="flex items-center gap-2.5 font-semibold">
            <span className="brand-tile inline-flex items-center justify-center w-8 h-8 rounded-[8px]" aria-hidden="true">
              <Logo size={18} />
            </span>
            <span>Proj-Clarion</span>
          </Link>
          <button
            type="button"
            onClick={onClose}
            aria-label="Close navigation"
            className="p-1.5 rounded text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:bg-white/[0.04]"
          >
            <X size={16} />
          </button>
        </div>
        <nav aria-label="Primary mobile" className="p-2">
          {NAV.map(({ to, label, icon: Icon }) => (
            <NavLink
              key={to}
              to={to}
              end={to === "/"}
              className={({ isActive }) =>
                cn(
                  "flex items-center gap-3 px-3 py-2.5 rounded-md text-sm transition-colors",
                  isActive
                    ? "bg-white/[0.06] text-[var(--color-text)]"
                    : "text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:bg-white/[0.03]",
                )
              }
            >
              <Icon size={16} aria-hidden="true" />
              <span>{label}</span>
            </NavLink>
          ))}
          {/* Secondary nav, phones can't fall back to the UserMenu
              overflow as easily as desktops, so we always list them
              under a faint divider. */}
          <div className="my-2 mx-3 border-t border-[var(--color-border)]" />
          {SECONDARY_NAV.map(({ to, label, icon: Icon }) => (
            <NavLink
              key={to}
              to={to}
              className={({ isActive }) =>
                cn(
                  "flex items-center gap-3 px-3 py-2.5 rounded-md text-sm transition-colors",
                  isActive
                    ? "bg-white/[0.06] text-[var(--color-text)]"
                    : "text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:bg-white/[0.03]",
                )
              }
            >
              <Icon size={16} aria-hidden="true" />
              <span>{label}</span>
            </NavLink>
          ))}
        </nav>
      </aside>
    </div>
  );
}

/** Topbar live cluster, the headline `PipelineStatusPill` for the
 *  followed pipeline, plus a small "+N more" sibling when other builds
 *  are running in parallel (SE queued multiple) so they're discoverable
 *  without polling /pipelines manually.
 *
 *  Layered structure on purpose: the pill is the prominent "what's
 *  happening right now" cue; the +N is a compact pointer to the list
 *  view. When there's no followed pipeline at all but builds ARE running
 *  (the SE just refreshed and the context hasn't latched yet), we fall
 *  back to a single "N builds running" link to /pipelines.
 */
function PipelineLiveCluster() {
  const p = usePipeline();
  // Cheap poll, bounded list size, only used to count running builds.
  const all = useQuery({
    queryKey: ["pipelines"],
    queryFn: listPipelines,
    refetchInterval: 5_000,
  });
  const runningCount = (all.data ?? []).filter((x) => x.status === "running").length;

  const followsActive = p.status !== "idle" && !!p.pipelineId;
  if (!followsActive && runningCount === 0) return null;

  if (!followsActive) {
    // Multiple builds running but we're not actively following any, show
    // a generic "N builds running" pill that links to /pipelines history.
    return (
      <Link
        to="/pipelines"
        title={`${runningCount} pipeline${runningCount === 1 ? "" : "s"} running, click to view list`}
        className="flex items-center gap-2 px-2.5 h-7 rounded-full border text-xs transition-colors border-[color:var(--color-info)]/40 text-[var(--color-info)] bg-[var(--color-info-bg)]"
      >
        <Loader2 size={12} className="animate-spin" />
        <span className="font-medium">{runningCount} build{runningCount === 1 ? "" : "s"} running</span>
      </Link>
    );
  }

  // Followed pipeline → the new v2 pill. "+N more" sibling appears only
  // when there are MORE running builds than just the one we're following.
  const otherRunning = Math.max(0, runningCount - (p.status === "running" ? 1 : 0));
  return (
    <div className="flex items-center gap-1.5">
      <PipelineStatusPill />
      {otherRunning > 0 && (
        <Link
          to="/pipelines"
          className="px-1.5 h-5 inline-flex items-center rounded-full bg-[var(--color-info-bg)] text-[var(--color-info)] text-[10px] font-mono"
          title={`${otherRunning} additional build${otherRunning === 1 ? "" : "s"} running, open Pipelines to switch context`}
        >
          +{otherRunning}
        </Link>
      )}
    </div>
  );
}

function TopBar({
  onOpenPalette, onOpenMobileNav,
}: {
  onOpenPalette: () => void;
  onOpenMobileNav: () => void;
}) {
  const location = useLocation();
  const assistant = useAssistant();
  const env = useQuery({ queryKey: ["env"], queryFn: getEnv, refetchInterval: 30_000 });
  const mode = env.data?.mode ?? "…";
  const modeColor =
    mode === "alloy" ? "text-[var(--color-accent)]" :
    mode === "cloud-direct" ? "text-[var(--color-info)]" :
    "text-[var(--color-text-faint)]";

  return (
    <header
      role="banner"
      className="sticky top-0 z-30 border-b border-[var(--color-border)] backdrop-blur bg-[var(--color-canvas)]/75"
    >
      <div className="max-w-[1400px] mx-auto w-full px-4 sm:px-6 h-16 flex items-center gap-4 lg:gap-6">
        {/* Hamburger, only on mobile. Opens the slide-in drawer. */}
        <button
          type="button"
          onClick={onOpenMobileNav}
          aria-label="Open navigation menu"
          className="md:hidden -ml-2 p-2 rounded text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:bg-white/[0.04]"
        >
          <Menu size={18} aria-hidden="true" />
        </button>
        {/* Brand cluster, accent-tinted tile wraps the Logo, wordmark
            sits to its right, then a small 2-line mono "SE / CONSOLE"
            pill beside the wordmark (per v2 mockup). The pill is a
            typographic mark, not a link, purely identifying this as
            the SE-facing surface. */}
        <Link
          to="/"
          className="flex items-center gap-3 rounded-md -mx-1 px-1 py-1 hover:bg-white/[0.03] transition-colors"
          aria-label="Proj-Clarion home"
        >
          <span
            className="brand-tile inline-flex items-center justify-center w-9 h-9 rounded-[10px]"
            aria-hidden="true"
          >
            <Logo size={22} />
          </span>
          <span className="text-[16px] font-semibold tracking-tight text-[var(--color-text)]">
            Proj-Clarion
          </span>
          <span className="brand-tag hidden sm:inline-flex" aria-hidden="true">
            SE Console
          </span>
        </Link>
        {/* Primary nav, hidden on mobile (replaced by overflow menu in
            future iteration; for now the command palette ⌘K covers it). */}
        <nav
          aria-label="Primary"
          className="hidden md:flex items-center gap-1 ml-2"
        >
          {NAV.map(({ to, label, icon: Icon }) => {
            const active = (to === "/" ? location.pathname === "/" : location.pathname.startsWith(to));
            return (
              <NavLink
                key={to}
                to={to}
                aria-current={active ? "page" : undefined}
                className={cn(
                  "h-8 px-3 rounded-md text-sm flex items-center gap-2 transition-colors",
                  active
                    ? "bg-white/[0.06] text-[var(--color-text)]"
                    : "text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:bg-white/[0.03]",
                )}
              >
                <Icon size={14} aria-hidden="true" />
                <span>{label}</span>
              </NavLink>
            );
          })}
        </nav>
        <div className="ml-auto flex items-center gap-2 sm:gap-3 text-xs text-[var(--color-text-muted)]">
          <PipelineLiveCluster />
          {/* Mode chip, inline label paired with a mono value, mirroring
              how Grafana panels render unit/value pairs. The accent
              token cascades through the value so an `alloy`-mode Clarion
              reads as "primary" even at a glance. */}
          <span
            className="hidden lg:inline-flex items-center gap-1.5 h-7 px-2 rounded-md border border-[var(--color-border)] bg-[var(--color-canvas-elev1)]/60"
            title={`Build mode: ${mode}`}
          >
            <span className="text-[10px] uppercase tracking-wider text-[var(--color-text-faint)]">mode</span>
            <span className={cn("font-mono text-[11px]", modeColor)}>{mode}</span>
          </span>
          <button
            onClick={onOpenPalette}
            aria-label="Open command palette"
            aria-keyshortcuts="Meta+K Control+K"
            className={cn(
              "flex items-center gap-2 h-8 pl-2.5 pr-1.5 rounded-md",
              "border border-[var(--color-border)] bg-[var(--color-canvas-elev1)]/70",
              "hover:bg-[var(--color-canvas-elev2)] hover:border-[var(--color-border-strong)]",
              "focus-visible:border-[color:var(--color-accent-border)] transition-colors",
            )}
          >
            <Search size={12} aria-hidden="true" className="text-[var(--color-text-faint)]" />
            <span className="hidden sm:inline text-[var(--color-text-muted)]">Search…</span>
            <kbd
              className={cn(
                "hidden sm:inline-flex items-center gap-0.5 ml-2 px-1.5 py-0.5 rounded",
                "text-[10px] font-mono text-[var(--color-text-faint)]",
                "border border-[var(--color-border)] bg-[var(--color-canvas)]/60",
              )}
            >
              <span>⌘</span><span>K</span>
            </kbd>
          </button>
          {/* Clarion assistant, the agentic chat that can run builds,
              re-run phases, extend profiles, approve plans and drive
              demos. First-class peer to the command palette (⌘J vs ⌘K).
              Accent-tinted when open so the SE knows the drawer is live. */}
          <button
            onClick={() => assistant.toggle()}
            aria-label="Open Clarion assistant"
            aria-keyshortcuts="Meta+J Control+J"
            aria-pressed={assistant.open}
            className={cn(
              "flex items-center gap-2 h-8 pl-2.5 pr-1.5 rounded-md transition-colors",
              assistant.open
                ? "border border-[color:var(--color-accent-border)] bg-[var(--color-accent-bg)] text-[var(--color-accent)]"
                : "border border-[var(--color-border)] bg-[var(--color-canvas-elev1)]/70 text-[var(--color-text-muted)] hover:bg-[var(--color-canvas-elev2)] hover:border-[var(--color-border-strong)] focus-visible:border-[color:var(--color-accent-border)]",
            )}
          >
            <Bot
              size={14}
              aria-hidden="true"
              className={assistant.open ? "text-[var(--color-accent)]" : "text-[var(--color-text-faint)]"}
            />
            <span className="hidden sm:inline">Assistant</span>
            <kbd
              className={cn(
                "hidden sm:inline-flex items-center gap-0.5 ml-1 px-1.5 py-0.5 rounded",
                "text-[10px] font-mono",
                assistant.open
                  ? "border border-[color:var(--color-accent-border)]/50 text-[var(--color-accent)]"
                  : "border border-[var(--color-border)] bg-[var(--color-canvas)]/60 text-[var(--color-text-faint)]",
              )}
            >
              <span>⌘</span><span>J</span>
            </kbd>
          </button>
          {/* User menu, identity, theme toggle, Settings, Sign out.
              Lives at the rightmost edge of the TopBar so it's always
              in the same spot regardless of viewport width. */}
          <UserMenu />
        </div>
      </div>
    </header>
  );
}
