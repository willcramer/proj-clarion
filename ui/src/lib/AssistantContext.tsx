/**
 * Global Clarion Assistant context.
 *
 * The assistant is a first-class, app-wide surface — a docked right-side
 * drawer that any page can open, optionally scoped to whatever the SE is
 * looking at (a plan, a profile, a build) and optionally seeded with a
 * starter prompt. The drawer itself (ClarionAssistant) reads this context
 * to know whether it's open, what scope to pin to turns, and whether a
 * page asked it to start a fresh thread.
 *
 * Pages open it via `useAssistant().openAssistant({ scope, seedPrompt })`.
 * The TopBar button + ⌘J toggle it with no scope (route-derived).
 */
import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import type { AssistantContextScope } from "@/lib/api";

interface OpenOptions {
  /** Explicit scope from the opener; merged over the route-derived scope. */
  scope?: AssistantContextScope;
  /** Prefill the compose box (the SE can edit/send or clear it). */
  seedPrompt?: string;
  /** Force a brand-new conversation instead of resuming the last one. */
  newThread?: boolean;
}

interface AssistantContextValue {
  open: boolean;
  /** Explicit scope set by the last opener (null = route-derived only). */
  scope: AssistantContextScope | null;
  /** Compose-box seed requested by a page; cleared once consumed. */
  seedPrompt: string | null;
  /** Increments whenever a page requests a fresh thread — the drawer
   *  watches this to reset its active conversation. */
  newThreadNonce: number;
  openAssistant: (opts?: OpenOptions) => void;
  close: () => void;
  toggle: () => void;
  /** Drawer calls this after it consumes the seed so it doesn't refill. */
  consumeSeed: () => void;
}

const AssistantCtx = createContext<AssistantContextValue | null>(null);

export function AssistantProvider({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState(false);
  const [scope, setScope] = useState<AssistantContextScope | null>(null);
  const [seedPrompt, setSeedPrompt] = useState<string | null>(null);
  const [newThreadNonce, setNewThreadNonce] = useState(0);

  const openAssistant = useCallback((opts?: OpenOptions) => {
    setScope(opts?.scope ?? null);
    if (opts?.seedPrompt !== undefined) setSeedPrompt(opts.seedPrompt);
    if (opts?.newThread) setNewThreadNonce((n) => n + 1);
    setOpen(true);
  }, []);

  const close = useCallback(() => setOpen(false), []);
  const toggle = useCallback(() => setOpen((v) => !v), []);
  const consumeSeed = useCallback(() => setSeedPrompt(null), []);

  const value = useMemo<AssistantContextValue>(
    () => ({ open, scope, seedPrompt, newThreadNonce, openAssistant, close, toggle, consumeSeed }),
    [open, scope, seedPrompt, newThreadNonce, openAssistant, close, toggle, consumeSeed],
  );

  return <AssistantCtx.Provider value={value}>{children}</AssistantCtx.Provider>;
}

export function useAssistant(): AssistantContextValue {
  const ctx = useContext(AssistantCtx);
  if (!ctx) throw new Error("useAssistant must be used within an AssistantProvider");
  return ctx;
}

/** Derive a context scope from the current route. The drawer merges any
 *  explicit scope (from openAssistant) over this so a page-specified
 *  plan_id always wins, but a bare ⌘J still gets useful context. */
export function deriveScopeFromPath(pathname: string): AssistantContextScope {
  const plan = pathname.match(/^\/plans\/([^/]+)/);
  if (plan) return { plan_id: plan[1], route: pathname };
  const profile = pathname.match(/^\/profiles\/([^/]+)/);
  if (profile) return { profile_id: profile[1], route: pathname };
  const pipeline = pathname.match(/^\/pipelines\/([^/]+)/);
  if (pipeline) return { pipeline_id: pipeline[1], route: pathname };
  return { route: pathname };
}
