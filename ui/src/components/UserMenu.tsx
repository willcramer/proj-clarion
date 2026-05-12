/**
 * UserMenu, top-right "signed in as" affordance.
 *
 * Surface:
 *   - Avatar circle with initials (from org_slug or user_name)
 *   - Click → dropdown showing identity, theme toggle, Settings, Sign out
 *
 * Identity priority (best signal first):
 *   - user_name + org_name           (live Cloud token, full data)
 *   - org_name + stack_url           (token doesn't expose user)
 *   - org_slug (from stack subdomain) (token rejected, but URL known)
 *   - "Not signed in"                (no stack URL)
 *
 * The menu is keyboard-accessible: tab opens focus, Esc closes,
 * arrow keys not implemented yet (could add later, list is short).
 *
 * Sign-out is destructive (wipes `.env` keys), so it ALWAYS goes through
 * a confirmation modal. The user can dismiss without harm.
 */
import {
  Sun, Moon, Monitor, Settings, LogOut, ChevronDown, ChevronUp,
  ExternalLink, AlertCircle,
} from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";

import { SECONDARY_NAV } from "@/components/Layout";
import { setupApi, type Identity } from "@/lib/setup-api";
import { useTheme, type ThemeMode } from "@/lib/ThemeContext";
import { cn } from "@/lib/cn";

export function UserMenu() {
  const navigate = useNavigate();
  const { mode, resolved, setMode } = useTheme();
  const [identity, setIdentity] = useState<Identity | null>(null);
  const [open, setOpen] = useState(false);
  const [confirmSignOut, setConfirmSignOut] = useState(false);
  const menuRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    // Best-effort identity fetch. Failures land us in the "Not signed in"
    // fallback but never throw, the rest of the app keeps working.
    setupApi.identity().then(setIdentity).catch(() => setIdentity(null));
  }, []);

  // Close on outside click / Esc, standard popover ergonomics.
  useEffect(() => {
    if (!open) return;
    function onPointer(e: PointerEvent) {
      if (menuRef.current && !menuRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("pointerdown", onPointer);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("pointerdown", onPointer);
      document.removeEventListener("keydown", onKey);
    };
  }, [open]);

  const displayName  = identity?.user_name ?? identity?.org_name ?? identity?.org_slug ?? "Not signed in";
  const displaySub   = identity?.user_email
                     ?? (identity?.org_name && identity?.org_slug && identity.org_name !== identity.org_slug ? identity.org_slug : null)
                     ?? (identity?.stack_url || "Setup required");
  const initials     = computeInitials(identity);

  const signedIn = !!identity?.stack_url && identity?.setup_complete;

  async function handleSignOut() {
    try {
      await setupApi.signout();
      // The middleware will start returning 503 on protected routes;
      // a hard reload is the cleanest way to re-trigger the SetupGate.
      window.location.assign("/");
    } catch (e) {
      // Surface, but don't leave the modal open, the user can retry.
      console.error("Sign out failed:", e);
      setConfirmSignOut(false);
    }
  }

  return (
    <>
      <div ref={menuRef} className="relative">
        <button
          type="button"
          onClick={() => setOpen((o) => !o)}
          aria-haspopup="menu"
          aria-expanded={open}
          aria-label={`Account menu, ${displayName}`}
          className={cn(
            "flex items-center gap-2 px-1.5 py-1 rounded-lg",
            "hover:bg-[var(--color-canvas-elev2)] transition-colors",
            open && "bg-[var(--color-canvas-elev2)]",
          )}
        >
          <Avatar initials={initials} signedIn={signedIn} />
          <span className="hidden sm:flex items-center gap-1 text-xs">
            <span className="text-[var(--color-text)] font-medium max-w-[140px] truncate">
              {displayName}
            </span>
            {open
              ? <ChevronUp size={12} className="text-[var(--color-text-faint)]" aria-hidden="true" />
              : <ChevronDown size={12} className="text-[var(--color-text-faint)]" aria-hidden="true" />}
          </span>
        </button>

        {open && (
          <div
            role="menu"
            className={cn(
              "absolute right-0 mt-2 w-72 z-50 rounded-xl",
              "border border-[var(--color-border)] bg-[var(--color-canvas-elev2)]",
              "shadow-xl overflow-hidden",
            )}
          >
            {/* Identity card */}
            <div className="px-3 py-3 border-b border-[var(--color-border)] flex items-start gap-3">
              <Avatar initials={initials} signedIn={signedIn} large />
              <div className="flex-1 min-w-0">
                <div className="text-sm font-medium text-[var(--color-text)] truncate">
                  {displayName}
                </div>
                <div className="text-xs text-[var(--color-text-muted)] truncate">
                  {displaySub}
                </div>
                {identity?.stack_url && identity.org_name && identity.user_name && (
                  <a
                    href={identity.stack_url}
                    target="_blank"
                    rel="noopener noreferrer"
                    className="mt-1 inline-flex items-center gap-1 text-[10px] text-[var(--color-text-faint)] hover:text-[var(--color-accent)]"
                  >
                    Open stack <ExternalLink size={10} aria-hidden="true" />
                  </a>
                )}
              </div>
            </div>

            {/* Theme toggle, segmented control */}
            <div className="px-3 py-2 border-b border-[var(--color-border)]">
              <div className="text-[10px] uppercase tracking-wider text-[var(--color-text-faint)] mb-1.5">
                Theme
              </div>
              <ThemeToggle current={mode} resolved={resolved} onChange={setMode} />
            </div>

            {/* Secondary nav, Pipelines / Runs / future. Removed from
                the primary topbar to keep the look uncluttered; they're
                one click away here. */}
            <div className="py-1 border-b border-[var(--color-border)]">
              {SECONDARY_NAV.map(({ to, label, icon: Icon }) => (
                <MenuItem
                  key={to}
                  icon={Icon}
                  onClick={() => { setOpen(false); navigate(to); }}
                  label={label}
                />
              ))}
            </div>

            {/* Actions */}
            <div className="py-1">
              <MenuItem
                icon={Settings}
                onClick={() => { setOpen(false); navigate("/setup"); }}
                label="Settings"
                sub="Edit tokens · re-upload .env"
              />
              {/* "New build" item removed, the Dashboard hero is the
                  canonical entry point for starting a build now. Keeping
                  it here was redundant. */}
              {signedIn && (
                <MenuItem
                  icon={LogOut}
                  onClick={() => { setOpen(false); setConfirmSignOut(true); }}
                  label="Sign out"
                  sub="Clears Clarion tokens from .env"
                  destructive
                />
              )}
            </div>

            {/* Model badge */}
            {identity?.anthropic_model && (
              <div className="px-3 py-2 border-t border-[var(--color-border)] flex items-center justify-between text-[10px] text-[var(--color-text-faint)]">
                <span>Model</span>
                <span className="font-mono">{identity.anthropic_model}</span>
              </div>
            )}
          </div>
        )}
      </div>

      {/* Sign-out confirmation modal */}
      {confirmSignOut && (
        <SignOutModal
          onCancel={() => setConfirmSignOut(false)}
          onConfirm={handleSignOut}
        />
      )}
    </>
  );
}

// ──────────────────────────────────────────────────────────────────
// Avatar, initials in a circle, dot signals signed-in state
// ──────────────────────────────────────────────────────────────────

function Avatar({
  initials, signedIn, large = false,
}: { initials: string; signedIn: boolean; large?: boolean }) {
  return (
    <span
      aria-hidden="true"
      // `user-menu-avatar` is a CSS hook (defined in `index.css`) that
      // paints a teal→sky gradient surface across the circle. The
      // Tailwind `bg-[...]` class below is a fallback if the rule is
      // ever scoped away, Tailwind wins specificity tie-breakers via
      // source order, but the gradient is loaded earlier in the cascade.
      className={cn(
        "user-menu-avatar",
        "relative inline-flex items-center justify-center font-medium select-none",
        large ? "w-10 h-10 text-sm" : "w-7 h-7 text-[11px]",
        "rounded-full bg-[var(--color-accent-bg)] text-[var(--color-on-accent)]",
        "ring-1 ring-[var(--color-accent-border)]",
      )}
    >
      {initials}
      <span
        className={cn(
          "absolute -bottom-0.5 -right-0.5 w-2 h-2 rounded-full ring-2 ring-[var(--color-canvas)]",
          signedIn ? "bg-[var(--color-success)]" : "bg-[var(--color-text-faint)]",
        )}
      />
    </span>
  );
}

function computeInitials(identity: Identity | null): string {
  if (!identity) return "?";
  const src = identity.user_name || identity.org_name || identity.org_slug || "";
  if (!src) return "?";
  // First letter of first two whitespace-or-hyphen-separated tokens.
  const parts = src.split(/[\s-]+/).filter(Boolean);
  if (parts.length === 0) return "?";
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[1][0]).toUpperCase();
}

// ──────────────────────────────────────────────────────────────────
// Theme toggle, three-up segmented control
// ──────────────────────────────────────────────────────────────────

function ThemeToggle({
  current, resolved, onChange,
}: {
  current: ThemeMode;
  resolved: "light" | "dark";
  onChange: (m: ThemeMode) => void;
}) {
  const options: { value: ThemeMode; icon: typeof Sun; label: string }[] = [
    { value: "light",  icon: Sun,     label: "Light" },
    { value: "dark",   icon: Moon,    label: "Dark" },
    { value: "system", icon: Monitor, label: "System" },
  ];
  return (
    <div
      role="radiogroup"
      aria-label="Color theme"
      className="grid grid-cols-3 gap-1 p-0.5 rounded-md bg-[var(--color-canvas)] border border-[var(--color-border)]"
    >
      {options.map(({ value, icon: Icon, label }) => {
        const active = current === value;
        return (
          <button
            key={value}
            type="button"
            role="radio"
            aria-checked={active}
            onClick={() => onChange(value)}
            title={value === "system" ? `System (now ${resolved})` : label}
            className={cn(
              "flex items-center justify-center gap-1.5 py-1.5 rounded text-[11px] font-medium transition-colors",
              active
                ? "bg-[var(--color-canvas-elev2)] text-[var(--color-text)] shadow-sm"
                : "text-[var(--color-text-muted)] hover:text-[var(--color-text)]",
            )}
          >
            <Icon size={12} aria-hidden="true" />
            {label}
          </button>
        );
      })}
    </div>
  );
}

// ──────────────────────────────────────────────────────────────────
// MenuItem, one row in the dropdown
// ──────────────────────────────────────────────────────────────────

function MenuItem({
  icon: Icon, label, sub, onClick, destructive = false,
}: {
  icon: typeof Settings;
  label: string;
  sub?: string;
  onClick: () => void;
  destructive?: boolean;
}) {
  return (
    <button
      type="button"
      role="menuitem"
      onClick={onClick}
      className={cn(
        "w-full px-3 py-2 flex items-start gap-3 text-left transition-colors",
        "hover:bg-[var(--color-canvas-elev3)]",
        destructive && "text-[var(--color-danger)] hover:bg-[var(--color-danger-bg)]",
      )}
    >
      <Icon
        size={14}
        aria-hidden="true"
        className={cn(
          "shrink-0 mt-0.5",
          destructive ? "text-[var(--color-danger)]" : "text-[var(--color-text-muted)]",
        )}
      />
      <span className="flex-1 min-w-0">
        <span className={cn("block text-sm font-medium", !destructive && "text-[var(--color-text)]")}>
          {label}
        </span>
        {sub && (
          <span className="block text-[11px] text-[var(--color-text-faint)] truncate">{sub}</span>
        )}
      </span>
    </button>
  );
}

// ──────────────────────────────────────────────────────────────────
// SignOutModal, confirmation before clearing .env keys
// ──────────────────────────────────────────────────────────────────

function SignOutModal({
  onCancel, onConfirm,
}: {
  onCancel: () => void;
  onConfirm: () => void;
}) {
  // Trap focus on the modal, Esc / outside click cancels.
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") onCancel();
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [onCancel]);

  return (
    <div
      role="dialog"
      aria-modal="true"
      aria-labelledby="signout-title"
      className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/60 backdrop-blur-sm"
      onClick={(e) => { if (e.target === e.currentTarget) onCancel(); }}
    >
      <div className="max-w-lg w-full rounded-xl border border-[var(--color-danger)]/40 bg-[var(--color-canvas-elev2)] shadow-2xl">
        <div className="p-5 flex items-start gap-3">
          <AlertCircle size={18} className="shrink-0 text-[var(--color-danger)] mt-0.5" aria-hidden="true" />
          <div className="flex-1">
            <h2 id="signout-title" className="text-sm font-medium text-[var(--color-text)]">
              Sign out, this clears your tokens
            </h2>
            <p className="text-xs text-[var(--color-text-muted)] mt-2">
              Clarion will remove these keys from <code>.env</code> on this machine:
            </p>
            <ul className="text-[11px] text-[var(--color-text-muted)] mt-1.5 grid grid-cols-2 gap-x-3 gap-y-0.5 font-mono">
              <li>ANTHROPIC_API_KEY</li>
              <li>GRAFANA_CLOUD_STACK_URL</li>
              <li>GRAFANA_CLOUD_API_TOKEN</li>
              <li>GRAFANA_CLOUD_OTLP_*</li>
              <li>SIGIL_*</li>
              <li>GCLOUD_PDC_*</li>
            </ul>
            <div className="mt-3 p-2 rounded bg-[var(--color-warning-bg)] border border-[var(--color-warning)]/30 text-[11px] text-[var(--color-warning)]">
              <strong>Backup:</strong> the prior <code>.env</code> is saved to{" "}
              <code>.env.bak</code> in the same folder before we wipe. To roll back, copy
              <code> .env.bak</code> back over <code>.env</code> and refresh.
            </div>
            <p className="text-xs text-[var(--color-text-faint)] mt-3">
              Postgres creds, RESEARCH_ALLOWED_HOSTS, OTEL_EXPORTER_* and any custom
              env vars are NOT touched.
            </p>
          </div>
        </div>
        <div className="px-5 py-3 border-t border-[var(--color-border)] flex justify-end gap-2 bg-[var(--color-canvas-elev1)]">
          <button
            type="button"
            onClick={onCancel}
            className="px-3 py-1.5 rounded-md text-xs font-medium border border-[var(--color-border)] text-[var(--color-text-muted)] hover:border-[var(--color-border-strong)] hover:text-[var(--color-text)]"
          >
            Cancel
          </button>
          <button
            type="button"
            onClick={onConfirm}
            autoFocus
            className="px-3 py-1.5 rounded-md text-xs font-medium bg-[var(--color-danger)] text-white hover:opacity-90"
          >
            Sign out
          </button>
        </div>
      </div>
    </div>
  );
}
