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
      className="fixed inset-0 z-50 flex items-start justify-center pt-32 bg-black/50 backdrop-blur-sm"
      onClick={() => onOpenChange(false)}
    >
      <Command
        label="Clarion command palette"
        className="glass w-[640px] max-w-[90vw] rounded-xl shadow-2xl overflow-hidden"
        onClick={(e) => e.stopPropagation()}
      >
        <Command.Input
          placeholder="Jump to a profile, plan, run…"
          className="w-full h-12 px-4 bg-transparent border-0 border-b border-[var(--color-border)] text-[var(--color-text)] placeholder:text-[var(--color-text-faint)] outline-none"
          autoFocus
        />
        <Command.List className="max-h-[420px] overflow-y-auto p-2">
          <Command.Empty className="text-[var(--color-text-faint)] text-sm py-8 text-center">
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
                  {p.source_profile_id} — {p.plan_id_short}
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
                  {r.kind} — {r.plan_id.slice(0, 8)}
                </PaletteItem>
              ))}
            </Command.Group>
          )}
        </Command.List>
      </Command>
      <style>{`
        .cmdk-group [cmdk-group-heading] {
          font-size: 11px;
          color: var(--color-text-faint);
          text-transform: uppercase;
          letter-spacing: 0.06em;
          padding: 8px 8px 4px;
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
      className="flex items-center gap-3 px-3 h-10 rounded-md text-sm cursor-pointer text-[var(--color-text)] data-[selected=true]:bg-white/[0.06]"
    >
      <Icon size={14} className="text-[var(--color-text-muted)]" />
      <span className="flex-1 truncate">{children}</span>
      {hint && (
        <span className="text-xs text-[var(--color-text-faint)] truncate">{hint}</span>
      )}
      <ChevronRight size={14} className="text-[var(--color-text-faint)] opacity-60" />
    </Command.Item>
  );
}
