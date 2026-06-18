import { Link, NavLink, Outlet, useLocation, useNavigate } from "react-router-dom";
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
  Wand2,
  ChevronDown,
  FileText,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { cn } from "@/lib/cn";
import { listPipelines, type PipelineSummary } from "@/lib/api";
import { CommandPalette } from "@/components/CommandPalette";
import { ClarionAssistant } from "@/components/ClarionAssistant";
import { ClarionMark } from "@/components/icons/ClarionIcons";
import { UserMenu } from "@/components/UserMenu";
import { useAssistant } from "@/lib/AssistantContext";

// Primary nav: the surfaces an SE hits during a live demo. The home
// page (Dashboard) is reachable via the brand tile on the left, so a
// dedicated "Dashboard" item here would be redundant. We keep the nav
// focused on the secondary surfaces the SE drills into.
const NAV = [
  { to: "/profiles", label: "Company Profiles", icon: ScrollText    },
  { to: "/plans",    label: "Demo Plans",       icon: ClipboardList },
  { to: "/new",      label: "Demo Builds",      icon: Sparkles      },
];

// Secondary nav, surfaced via UserMenu so they remain one click away
// without crowding the primary top bar. Mobile drawer also lists these
// so phones don't lose access to them.
//
// "Pipelines" used to live here but was dropped: the "Build" primary
// nav now goes to /new which is the builds list (consolidation per
// CDD). /pipelines stays as a backward-compat route in App.tsx.
// The leadership one-pager is internal-only and git-ignored (see App.tsx).
// Only surface the nav entry when the file is actually present, so a clean
// checkout without it doesn't show a dead link.
const hasOnePager = Object.keys(import.meta.glob("../pages/OnePager.tsx")).length > 0;

export const SECONDARY_NAV = [
  ...(hasOnePager ? [{ to: "/one-pager", label: "One-pager", icon: FileText }] : []),
  { to: "/runs",  label: "Runs",  icon: Activity },
  { to: "/audit", label: "Activity Log", icon: History  },
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
              <ClarionMark size={18} />
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

/** Topbar live cluster — shows what's building right now.
 *  One running build → a single live pill (host · phase n/6) linking to
 *  its view. Multiple → a "N builds" pill that opens a dropdown listing
 *  each one (host · phase · spinner), so parallel builds are legible at
 *  a glance and one click away. Hidden when nothing is running. */
function PipelineLiveCluster() {
  const navigate = useNavigate();
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement | null>(null);
  const all = useQuery({
    queryKey: ["pipelines"],
    queryFn: listPipelines,
    refetchInterval: 5_000,
  });
  const running = (all.data ?? []).filter((x) => x.status === "running");

  useEffect(() => {
    function h(e: MouseEvent) { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false); }
    document.addEventListener("mousedown", h);
    return () => document.removeEventListener("mousedown", h);
  }, []);

  if (running.length === 0) return null;

  function go(b: PipelineSummary) {
    setOpen(false);
    navigate(`/pipelines/${b.pipeline_id}`);
  }

  // Single build → a compact live pill straight to its view.
  if (running.length === 1) {
    const b = running[0];
    return (
      <button
        type="button"
        onClick={() => go(b)}
        title="Open the live build"
        className="flex items-center gap-2 px-2.5 h-7 rounded-full border text-xs transition-colors border-[color:var(--color-accent-border)] text-[var(--color-accent)] bg-[var(--color-accent-bg)] hover:bg-[color:var(--color-accent-bg)]/80"
      >
        <Loader2 size={12} className="animate-spin" />
        <span className="font-medium max-w-[140px] truncate">{buildHost(b)}</span>
        <span className="font-mono text-[10px] opacity-80">{buildPhase(b)}</span>
      </button>
    );
  }

  // Multiple builds → a count pill that opens a status dropdown.
  return (
    <div ref={ref} className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-expanded={open}
        title="Builds in progress"
        className="flex items-center gap-2 px-2.5 h-7 rounded-full border text-xs transition-colors border-[color:var(--color-accent-border)] text-[var(--color-accent)] bg-[var(--color-accent-bg)] hover:bg-[color:var(--color-accent-bg)]/80"
      >
        <Loader2 size={12} className="animate-spin" />
        <span className="font-medium">{running.length} builds</span>
        <ChevronDown size={12} className={cn("transition-transform", open && "rotate-180")} />
      </button>
      {open && (
        <div className="absolute right-0 top-9 z-50 w-72 rounded-xl border border-[var(--color-border-strong)] bg-[var(--color-canvas-elev2)] p-1 shadow-[var(--shadow-lg)]">
          <div className="px-3 py-2 text-[10px] font-mono uppercase tracking-[0.08em] text-[var(--color-text-faint)]">
            {running.length} builds in progress
          </div>
          {running.map((b) => (
            <button
              key={b.pipeline_id}
              type="button"
              onClick={() => go(b)}
              className="hover-wash flex w-full items-center gap-2.5 rounded-md px-3 py-2 text-left"
            >
              <Loader2 size={13} className="shrink-0 animate-spin text-[var(--color-accent)]" />
              <div className="min-w-0 flex-1">
                <div className="text-[13px] font-medium text-[var(--color-text)] truncate">{buildHost(b)}</div>
                <div className="font-mono text-[10px] text-[var(--color-text-faint)]">{buildPhase(b)}</div>
              </div>
              <Activity size={13} className="shrink-0 text-[var(--color-text-faint)]" />
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function buildHost(b: PipelineSummary): string {
  if (b.company) return b.company;
  try { return new URL(b.url).host.replace(/^www\./, ""); }
  catch { return b.url; }
}
function buildPhase(b: PipelineSummary): string {
  const done = b.phases_done ?? 0;
  return `${b.current_phase ?? "running"} · ${done}/6`;
}

function TopBar({
  onOpenPalette, onOpenMobileNav,
}: {
  onOpenPalette: () => void;
  onOpenMobileNav: () => void;
}) {
  const location = useLocation();
  const assistant = useAssistant();

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
        {/* Brand cluster — accent-tinted tile wraps the Clarion mark, with
            the wordmark to its right. */}
        <Link
          to="/"
          className="flex items-center gap-3 rounded-md -mx-1 px-1 py-1 hover:bg-white/[0.03] transition-colors"
          aria-label="Proj-Clarion home"
        >
          <span
            className="brand-tile inline-flex items-center justify-center w-9 h-9 rounded-[10px]"
            aria-hidden="true"
          >
            <ClarionMark size={22} />
          </span>
          <span className="text-[16px] font-semibold tracking-tight text-[var(--color-text)]">
            Proj-Clarion
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
            <Wand2
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
