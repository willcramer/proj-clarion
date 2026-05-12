/**
 * Toast, non-blocking notifications anchored to the bottom-right of the
 * viewport. Used for build-completed / build-failed / "we used X URL"
 * announcements that don't need a modal.
 *
 * Design constraints:
 * - **Non-blocking**, no focus trap, no overlay; the user keeps doing
 *   what they were doing.
 * - **Live region**, wrapped in `aria-live="polite"` so screen readers
 *   announce the toast when it appears, but don't interrupt urgent
 *   reading (we'd use `assertive` only for failures).
 * - **Auto-dismiss with manual override**, `duration` defaults to 6s;
 *   pass `0` for sticky toasts (failure paths typically). The X button
 *   is always present.
 * - **Stackable**, multiple toasts stack vertically with newest at
 *   the bottom. `useToasts()` hook owns the stack; consumers call
 *   `push(toast)` and `dismiss(id)`.
 */
import { CheckCircle2, AlertCircle, AlertTriangle, Info, X } from "lucide-react";
import {
  createContext, useCallback, useContext, useEffect, useState,
  type ReactNode,
} from "react";

import { cn } from "@/lib/cn";

export type ToastTone = "success" | "danger" | "warning" | "info";

export type Toast = {
  id: string;
  tone: ToastTone;
  title: string;
  body?: ReactNode;
  /** ms before auto-dismiss; 0 = sticky. Default 6000. */
  duration?: number;
  /** Optional CTA: rendered as a small button on the right of the body. */
  action?: { label: string; onClick: () => void };
};

type ToastContextValue = {
  push: (t: Omit<Toast, "id">) => string;
  dismiss: (id: string) => void;
};

const ToastContext = createContext<ToastContextValue | null>(null);

const ICONS: Record<ToastTone, typeof CheckCircle2> = {
  success: CheckCircle2,
  danger:  AlertCircle,
  warning: AlertTriangle,
  info:    Info,
};

const TONE_TO_ACCENT: Record<ToastTone, string> = {
  success: "border-[var(--color-success)]/40 [--toast-icon:var(--color-success)]",
  danger:  "border-[var(--color-danger)]/50 [--toast-icon:var(--color-danger)]",
  warning: "border-[var(--color-warning)]/40 [--toast-icon:var(--color-warning)]",
  info:    "border-[var(--color-info)]/40 [--toast-icon:var(--color-info)]",
};

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);

  const dismiss = useCallback((id: string) => {
    setToasts((cur) => cur.filter((t) => t.id !== id));
  }, []);

  const push = useCallback(
    (t: Omit<Toast, "id">) => {
      const id = `t-${Date.now()}-${Math.random().toString(36).slice(2, 6)}`;
      setToasts((cur) => [...cur, { ...t, id }]);
      const dur = t.duration ?? 6000;
      if (dur > 0) {
        // Auto-dismiss timer. We let the React state cleanup garbage
        // collect this; no need to clearTimeout on unmount because the
        // dismiss is idempotent (filter on missing id is a no-op).
        setTimeout(() => dismiss(id), dur);
      }
      return id;
    },
    [dismiss],
  );

  return (
    <ToastContext.Provider value={{ push, dismiss }}>
      {children}
      <div
        // `aria-live="polite"` so screen readers announce new toasts
        // without interrupting the user's current focus. Use a separate
        // live region per toast tone for failures? Not yet, `polite`
        // is fine for our cadence (1-2 toasts per build).
        aria-live="polite"
        aria-atomic="false"
        className="fixed bottom-4 right-4 z-50 flex flex-col gap-2 max-w-[calc(100vw-2rem)] sm:max-w-sm pointer-events-none"
      >
        {toasts.map((t) => (
          <ToastCard key={t.id} toast={t} onDismiss={() => dismiss(t.id)} />
        ))}
      </div>
    </ToastContext.Provider>
  );
}

export function useToasts() {
  const ctx = useContext(ToastContext);
  if (!ctx) {
    throw new Error("useToasts must be used inside <ToastProvider>");
  }
  return ctx;
}

function ToastCard({ toast, onDismiss }: { toast: Toast; onDismiss: () => void }) {
  const Icon = ICONS[toast.tone];
  // Slide-in animation. We use a state flag so the initial render
  // mounts at translate-x-full, then flips to 0 in a useEffect, this
  // gives the browser a chance to apply the transition class.
  const [mounted, setMounted] = useState(false);
  useEffect(() => {
    const t = setTimeout(() => setMounted(true), 10);
    return () => clearTimeout(t);
  }, []);

  return (
    <div
      role={toast.tone === "danger" ? "alert" : "status"}
      className={cn(
        "pointer-events-auto rounded-xl border bg-[var(--color-canvas-elev2)] backdrop-blur",
        "shadow-xl transition-all duration-200 ease-out",
        TONE_TO_ACCENT[toast.tone],
        mounted ? "translate-x-0 opacity-100" : "translate-x-full opacity-0",
      )}
    >
      <div className="flex items-start gap-3 p-3">
        <Icon
          size={16}
          aria-hidden="true"
          className="shrink-0 mt-0.5 text-[var(--toast-icon)]"
        />
        <div className="flex-1 min-w-0">
          <div className="text-sm font-medium text-[var(--color-text)]">
            {toast.title}
          </div>
          {toast.body && (
            <div className="text-xs text-[var(--color-text-muted)] mt-1">
              {toast.body}
            </div>
          )}
          {toast.action && (
            <button
              type="button"
              onClick={() => {
                toast.action!.onClick();
                onDismiss();
              }}
              className="mt-2 text-xs font-medium text-[var(--color-accent)] hover:text-[var(--color-accent-hover)]"
            >
              {toast.action.label}
            </button>
          )}
        </div>
        <button
          type="button"
          aria-label="Dismiss notification"
          onClick={onDismiss}
          className="-mr-1 -mt-0.5 p-1 rounded text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:bg-white/[0.04]"
        >
          <X size={14} aria-hidden="true" />
        </button>
      </div>
    </div>
  );
}
