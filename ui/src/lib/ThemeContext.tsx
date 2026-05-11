/**
 * Theme system.
 *
 * Three modes:
 *   - `light`   — explicit; user picked light
 *   - `dark`    — explicit; user picked dark
 *   - `system`  — follow OS `prefers-color-scheme`; flips automatically
 *                 when the OS preference changes
 *
 * Storage: `localStorage["clarion.theme"]` holds the user's PREFERENCE
 * (light/dark/system), not the RESOLVED value. The resolved value is
 * always written to `<html data-theme=...>` as either "light" or
 * "dark" — the CSS in index.css branches on that attribute.
 *
 * The boot script in `index.html` sets `data-theme` synchronously
 * before React paints, so refresh / cold-load never flashes the wrong
 * theme. This Provider hooks up runtime changes (the toggle in
 * UserMenu) and OS-pref change events for `system` mode.
 */
import {
  createContext, useCallback, useContext, useEffect, useMemo, useState,
  type ReactNode,
} from "react";

export type ThemeMode = "light" | "dark" | "system";
export type ResolvedTheme = "light" | "dark";

const STORAGE_KEY = "clarion.theme";

function resolveSystem(): ResolvedTheme {
  return window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

function readStored(): ThemeMode {
  try {
    const v = localStorage.getItem(STORAGE_KEY);
    if (v === "light" || v === "dark" || v === "system") return v;
  } catch {
    /* localStorage unavailable in private mode — fall through */
  }
  return "system";
}

function writeStored(mode: ThemeMode) {
  try { localStorage.setItem(STORAGE_KEY, mode); } catch { /* private mode */ }
}

function applyTheme(mode: ThemeMode): ResolvedTheme {
  const resolved: ResolvedTheme = mode === "system" ? resolveSystem() : mode;
  document.documentElement.setAttribute("data-theme", resolved);
  return resolved;
}

interface ThemeContextValue {
  /** What the user picked. */
  mode: ThemeMode;
  /** What's actually applied right now (system resolves to light or dark). */
  resolved: ResolvedTheme;
  /** Change the preference; persists to localStorage + applies immediately. */
  setMode: (m: ThemeMode) => void;
}

const ThemeContext = createContext<ThemeContextValue | null>(null);

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [mode, setModeState] = useState<ThemeMode>(() => readStored());
  // `resolved` is derived from mode + (when mode='system') the OS pref.
  // We keep it in state so the React tree re-renders when the OS pref
  // changes mid-session (rare but real — laptop lid close, screen-share
  // mode, etc.).
  const [resolved, setResolved] = useState<ResolvedTheme>(() =>
    mode === "system" ? resolveSystem() : mode,
  );

  // Re-apply whenever mode changes. The boot script already set the
  // attribute on first paint; this handles every subsequent change.
  useEffect(() => {
    const r = applyTheme(mode);
    setResolved(r);
  }, [mode]);

  // When mode === "system", listen for OS preference changes and re-apply.
  // The listener is added once on mount and only matters when mode is
  // "system"; we still install it always (cheaper than conditional
  // subscribe) and check `mode` inside the handler.
  useEffect(() => {
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    function onChange() {
      // Read mode from localStorage at fire time so we don't capture a
      // stale value via React closure.
      if (readStored() === "system") {
        const r = applyTheme("system");
        setResolved(r);
      }
    }
    // Safari < 14 needs the old addListener; everyone else uses addEventListener.
    if ("addEventListener" in mq) mq.addEventListener("change", onChange);
    else (mq as unknown as { addListener: (cb: () => void) => void }).addListener(onChange);
    return () => {
      if ("removeEventListener" in mq) mq.removeEventListener("change", onChange);
      else (mq as unknown as { removeListener: (cb: () => void) => void }).removeListener(onChange);
    };
  }, []);

  const setMode = useCallback((m: ThemeMode) => {
    writeStored(m);
    setModeState(m);
  }, []);

  const value = useMemo<ThemeContextValue>(
    () => ({ mode, resolved, setMode }),
    [mode, resolved, setMode],
  );

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme(): ThemeContextValue {
  const ctx = useContext(ThemeContext);
  if (!ctx) throw new Error("useTheme must be used inside <ThemeProvider>");
  return ctx;
}
