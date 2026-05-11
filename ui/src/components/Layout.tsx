import { Link, NavLink, Outlet, useLocation } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import {
  LayoutDashboard,
  ScrollText,
  ClipboardList,
  Activity,
  Search,
  Sparkles,
  Loader2,
  CheckCircle2,
  AlertCircle,
  Menu,
  X,
} from "lucide-react";
import { useEffect, useState } from "react";

import { cn } from "@/lib/cn";
import { getEnv, listPipelines } from "@/lib/api";
import { CommandPalette } from "@/components/CommandPalette";
import { Logo } from "@/components/Logo";
import { UserMenu } from "@/components/UserMenu";
import { usePipeline, activePhase, phaseProgress } from "@/lib/PipelineContext";

const NAV = [
  { to: "/",          label: "Dashboard", icon: LayoutDashboard },
  { to: "/new",       label: "Build",     icon: Sparkles        },
  { to: "/profiles",  label: "Profiles",  icon: ScrollText      },
  { to: "/plans",     label: "Plans",     icon: ClipboardList   },
  { to: "/pipelines", label: "Pipelines", icon: Activity        },
  { to: "/runs",      label: "Runs",      icon: Activity        },
];

export function Layout() {
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const location = useLocation();

  // ⌘K / Ctrl+K to open the command palette + Esc to close mobile nav.
  // Single keydown handler so we don't pile up listeners — the cost of
  // two checks per keypress is negligible vs. registering two handlers.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === "k") {
        e.preventDefault();
        setPaletteOpen((v) => !v);
      } else if (e.key === "Escape" && mobileNavOpen) {
        setMobileNavOpen(false);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [mobileNavOpen]);

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
          <Link to="/" className="flex items-center gap-2 font-semibold">
            <Logo size={20} />
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
        </nav>
      </aside>
    </div>
  );
}

/** Persistent indicator shown in the top nav whenever a pipeline is alive.
 *  Click jumps back to /new. Now polls the global pipelines list so a
 *  user who's queued multiple builds (start one, click Back, fill the
 *  form, start another) sees a count of all the running ones — not just
 *  whichever the local PipelineContext is currently following.
 */
function PipelineIndicator() {
  const p = usePipeline();
  // Cheap poll — bounded list size, only used to count running builds.
  const all = useQuery({
    queryKey: ["pipelines"],
    queryFn: listPipelines,
    refetchInterval: 5_000,
  });
  const runningCount = (all.data ?? []).filter((x) => x.status === "running").length;

  // Three rendering branches:
  //   1. PipelineContext is following one → show its phase progress
  //   2. No follow but there ARE running builds → show running count
  //   3. Nothing in flight at all → render nothing
  const followsActive = p.status !== "idle" && !!p.pipelineId;
  if (!followsActive && runningCount === 0) return null;

  if (!followsActive) {
    // Multiple builds running but we're not actively following any — show
    // a generic "N builds running" pill that links to /pipelines history.
    return (
      <Link
        to="/pipelines"
        title={`${runningCount} pipeline${runningCount === 1 ? "" : "s"} running — click to view list`}
        className="flex items-center gap-2 px-2.5 h-7 rounded-md border text-xs transition-all border-[var(--color-info)]/40 text-[var(--color-info)] bg-[var(--color-info)]/10"
      >
        <Loader2 size={12} className="text-[var(--color-info)] animate-spin" />
        <span className="font-medium">{runningCount} build{runningCount === 1 ? "" : "s"} running</span>
      </Link>
    );
  }

  const { done, total } = phaseProgress(p.phases);
  const ap = activePhase(p.phases);

  let Icon = Loader2;
  let iconClass = "text-[var(--color-info)] animate-spin";
  let label = ap ?? "running";
  let tone = "border-[var(--color-info)]/40 text-[var(--color-info)] bg-[var(--color-info)]/10";
  if (p.status === "done") {
    Icon = CheckCircle2; iconClass = "text-[var(--color-success)]";
    label = "done"; tone = "border-[var(--color-success)]/40 text-[var(--color-success)] bg-[var(--color-success)]/10";
  } else if (p.status === "failed" || p.status === "cancelled") {
    Icon = AlertCircle; iconClass = "text-[var(--color-danger)]";
    label = p.status; tone = "border-[var(--color-danger)]/40 text-[var(--color-danger)] bg-[var(--color-danger)]/10";
  }

  // "+N" badge when there are MORE running builds than just the one
  // we're following — so users who queued additional builds know they
  // need to switch context to see them.
  const otherRunning = Math.max(0, runningCount - (p.status === "running" ? 1 : 0));

  return (
    <Link
      to="/new"
      title={`Pipeline ${p.pipelineId} · ${p.url}${otherRunning > 0 ? ` (and ${otherRunning} more running)` : ""}`}
      className={cn(
        "flex items-center gap-2 px-2.5 h-7 rounded-md border text-xs transition-all",
        tone,
      )}
    >
      <Icon size={12} className={iconClass} />
      <span className="font-medium">{label}</span>
      <span className="opacity-60">· {done}/{total}</span>
      {otherRunning > 0 && (
        <span
          className="ml-1 px-1.5 rounded bg-[var(--color-info)]/30 text-[var(--color-info)] text-[10px] font-mono"
          title={`${otherRunning} additional build${otherRunning === 1 ? "" : "s"} running — switch context via Recent Builds`}
        >
          +{otherRunning}
        </span>
      )}
    </Link>
  );
}

function TopBar({
  onOpenPalette, onOpenMobileNav,
}: {
  onOpenPalette: () => void;
  onOpenMobileNav: () => void;
}) {
  const location = useLocation();
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
      <div className="max-w-[1400px] mx-auto w-full px-4 sm:px-6 h-14 flex items-center gap-4 lg:gap-6">
        {/* Hamburger — only on mobile. Opens the slide-in drawer. */}
        <button
          type="button"
          onClick={onOpenMobileNav}
          aria-label="Open navigation menu"
          className="md:hidden -ml-2 p-2 rounded text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:bg-white/[0.04]"
        >
          <Menu size={18} aria-hidden="true" />
        </button>
        {/* Brand — clickable to home. The logo glyph is decorative
            because the text "Proj-Clarion" already conveys the brand;
            screen readers don't need to announce it twice. */}
        <Link
          to="/"
          className="flex items-center gap-2.5 font-semibold tracking-tight rounded-md -mx-1 px-1 py-1 hover:bg-white/[0.03] transition-colors"
          aria-label="Proj-Clarion home"
        >
          <Logo size={22} />
          <span className="text-[15px]">Proj-Clarion</span>
          <span className="hidden sm:inline text-[var(--color-text-faint)] font-normal text-[11px] ml-1 px-1.5 py-0.5 rounded border border-[var(--color-border)]">
            SE Console
          </span>
        </Link>
        {/* Primary nav — hidden on mobile (replaced by overflow menu in
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
          <PipelineIndicator />
          <span className="hidden lg:inline">
            mode <span className={cn("font-mono", modeColor)}>{mode}</span>
          </span>
          <button
            onClick={onOpenPalette}
            aria-label="Open command palette"
            aria-keyshortcuts="Meta+K Control+K"
            className="flex items-center gap-2 h-8 px-3 rounded-md border border-[var(--color-border)] bg-[var(--color-canvas-elev1)] hover:bg-[var(--color-canvas-elev2)] hover:border-[var(--color-border-strong)] transition-all"
          >
            <Search size={12} aria-hidden="true" />
            <span className="hidden sm:inline text-[var(--color-text-muted)]">Search…</span>
            <kbd className="hidden sm:inline-flex items-center gap-0.5 text-[10px] font-mono text-[var(--color-text-faint)] ml-2">
              <span>⌘</span><span>K</span>
            </kbd>
          </button>
          {/* User menu — identity, theme toggle, Settings, Sign out.
              Lives at the rightmost edge of the TopBar so it's always
              in the same spot regardless of viewport width. */}
          <UserMenu />
        </div>
      </div>
    </header>
  );
}
