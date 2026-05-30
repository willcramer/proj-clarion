import { Command } from "cmdk";
import { useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import {
  ScrollText,
  ClipboardList,
  Activity,
  LayoutDashboard,
  ChevronRight,
} from "lucide-react";
import { listPlans, listProfiles, listRuns } from "@/lib/api";

interface PaletteProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

export function CommandPalette({ open, onOpenChange }: PaletteProps) {
  const navigate = useNavigate();
  const profiles = useQuery({ queryKey: ["profiles"], queryFn: listProfiles, enabled: open });
  const plans    = useQuery({ queryKey: ["plans"],    queryFn: () => listPlans(), enabled: open });
  const runs     = useQuery({ queryKey: ["runs"],     queryFn: listRuns, enabled: open });

  function go(path: string) {
    navigate(path);
    onOpenChange(false);
  }

  if (!open) return null;

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-label="Command palette"
      // Backdrop: opaque enough on both themes to give a clear contrast
      // baseline behind the modal. The black/60 is the same on light + dark
      // (the modal contents define their own theme-aware surface below).
      className="fixed inset-0 z-50 flex items-start justify-center pt-32 bg-black/60 backdrop-blur-sm"
      onClick={() => onOpenChange(false)}
    >
      <Command
        label="Clarion command palette"
        // Solid `canvas-elev2` (raised-modal token) + strong border so the
        // dialog has predictable AA contrast in both themes. No `.glass`
        // semi-transparency here — that was making placeholder + hint text
        // float at 3.6:1 contrast on the underlying backdrop.
        className={
          "w-[640px] max-w-[90vw] rounded-xl shadow-2xl overflow-hidden " +
          "bg-[var(--color-canvas-elev2)] border border-[var(--color-border-strong)] " +
          "text-[var(--color-text)]"
        }
        onClick={(e) => e.stopPropagation()}
      >
        <Command.Input
          placeholder="Jump to a profile, plan, run…"
          // Placeholder uses `text-muted` (#9aa3b2 dark / #5a6478 light) for
          // ~7:1 contrast on the elev2 surface — passes WCAG AAA. The
          // previous `text-faint` (#6b7385) was ~3.6:1, failing AA.
          // Focus ring tightens to the accent border on both themes.
          className={
            "w-full h-12 px-4 bg-transparent border-0 outline-none " +
            "border-b border-[var(--color-border)] " +
            "text-[var(--color-text)] " +
            "placeholder:text-[var(--color-text-muted)] " +
            "focus:border-[var(--color-accent-border)]"
          }
          autoFocus
        />
        <Command.List className="max-h-[420px] overflow-y-auto p-2">
          <Command.Empty className="text-[var(--color-text-muted)] text-sm py-8 text-center">
            No matches.
          </Command.Empty>

          <Command.Group heading="Pages" className="cmdk-group">
            <PaletteItem icon={LayoutDashboard} onSelect={() => go("/")}>Dashboard</PaletteItem>
            <PaletteItem icon={ScrollText} onSelect={() => go("/profiles")}>Profiles</PaletteItem>
            <PaletteItem icon={ClipboardList} onSelect={() => go("/plans")}>Plans</PaletteItem>
            <PaletteItem icon={Activity} onSelect={() => go("/runs")}>Runs</PaletteItem>
          </Command.Group>

          {plans.data && plans.data.length > 0 && (
            <Command.Group heading="Plans" className="cmdk-group">
              {plans.data.map((p) => (
                <PaletteItem
                  key={p.plan_id}
                  icon={ClipboardList}
                  onSelect={() => go(`/plans/${p.plan_id}`)}
                  hint={p.review_state}
                >
                  {p.source_profile_id}, {p.plan_id_short}
                </PaletteItem>
              ))}
            </Command.Group>
          )}

          {profiles.data && profiles.data.length > 0 && (
            <Command.Group heading="Profiles" className="cmdk-group">
              {profiles.data.map((p) => (
                <PaletteItem
                  key={p.profile_id}
                  icon={ScrollText}
                  onSelect={() => go(`/profiles/${p.profile_id}`)}
                  hint={p.company_name ?? ""}
                >
                  {p.profile_id}
                </PaletteItem>
              ))}
            </Command.Group>
          )}

          {runs.data && runs.data.length > 0 && (
            <Command.Group heading="Recent runs" className="cmdk-group">
              {runs.data.slice(0, 8).map((r) => (
                <PaletteItem
                  key={r.run_id}
                  icon={Activity}
                  onSelect={() => go(`/runs?run=${r.run_id}`)}
                  hint={r.finished ? `exit ${r.return_code}` : "running"}
                >
                  {r.kind}, {r.plan_id.slice(0, 8)}
                </PaletteItem>
              ))}
            </Command.Group>
          )}
        </Command.List>
      </Command>
      <style>{`
        /* Group heading: was text-faint (3.6:1) → text-muted (~7:1).
           Slight size bump + tighter padding for scan readability. */
        .cmdk-group [cmdk-group-heading] {
          font-size: 11px;
          font-weight: 500;
          color: var(--color-text-muted);
          text-transform: uppercase;
          letter-spacing: 0.08em;
          padding: 10px 10px 4px;
        }
      `}</style>
    </div>
  );
}

function PaletteItem({
  icon: Icon,
  children,
  hint,
  onSelect,
}: {
  icon: typeof LayoutDashboard;
  children: React.ReactNode;
  hint?: string;
  onSelect: () => void;
}) {
  return (
    <Command.Item
      onSelect={onSelect}
      // Selected state uses the accent-bg/border tokens so it has
      // proper visual weight in BOTH themes (the previous
      // `bg-white/[0.06]` was invisible in light mode). The accent
      // ring + text shift gives ~5:1 contrast on the selected row.
      className={
        "flex items-center gap-3 px-3 h-10 rounded-md text-sm cursor-pointer " +
        "text-[var(--color-text)] " +
        "data-[selected=true]:bg-[var(--color-accent-bg)] " +
        "data-[selected=true]:text-[var(--color-accent)] " +
        "data-[selected=true]:ring-1 data-[selected=true]:ring-[color:var(--color-accent-border)] " +
        "transition-colors"
      }
    >
      {/* Icon was already at text-muted (~7:1); bump shrink-0 so long
          labels truncate cleanly without squashing the glyph. */}
      <Icon size={14} className="text-[var(--color-text-muted)] shrink-0" />
      <span className="flex-1 truncate">{children}</span>
      {hint && (
        // Hint was text-faint (3.6:1, AA fail). Bumped to text-muted
        // for ~7:1 contrast — visible in both light + dark.
        <span className="text-xs text-[var(--color-text-muted)] truncate shrink-0 ml-2">
          {hint}
        </span>
      )}
      {/* ChevronRight: dropped the opacity-60 multiplier; muted token
          alone gives ~7:1. Previous `text-faint opacity-60` was ~2.1:1,
          well below WCAG 3:1 minimum for non-text UI. */}
      <ChevronRight
        size={14}
        aria-hidden="true"
        className="text-[var(--color-text-muted)] shrink-0"
      />
    </Command.Item>
  );
}
