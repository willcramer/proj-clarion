/**
 * SetupGate, top-level wrapper that checks setup status and renders
 * the Setup page when the backend isn't ready yet.
 *
 * Flow:
 *   1. On mount, hit `/api/setup/status`.
 *   2. If `ready: true`, render children (the regular app).
 *   3. If `ready: false`, render the Setup page in place of the app.
 *   4. After Setup's "Save & launch" succeeds, refetch status; once
 *      ready, this component unmounts the Setup view and the app loads.
 *
 * We also listen for `SetupRequiredError` thrown by api.ts, that fires
 * if the user has the app open in a tab and the backend's env gets
 * cleared (or they manually deleted .env). In that case we flip the
 * gate state and show the wizard, no full-page reload needed.
 */
import { useEffect, useState, type ReactNode } from "react";

import { setupApi, type SetupStatus } from "@/lib/setup-api";
import { SetupPage } from "@/pages/Setup";

export function SetupGate({ children }: { children: ReactNode }) {
  const [status, setStatus] = useState<SetupStatus | null>(null);
  const [error, setError] = useState<string | null>(null);

  // Initial probe + an "after-save" refetch handler that Setup calls.
  async function refresh() {
    setError(null);
    try {
      const s = await setupApi.status();
      setStatus(s);
    } catch (e) {
      // Network error reaching the API at all, show a help message so
      // the user knows it's not their tokens, it's that the backend is
      // off. Most common cause during dev.
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  useEffect(() => { void refresh(); }, []);

  if (error) {
    return <ApiUnreachable detail={error} onRetry={refresh} />;
  }
  if (status === null) {
    // First-paint state. Brief enough that we don't need a real loader;
    // a neutral panel keeps the screen from flashing.
    return <BootScreen />;
  }
  if (!status.ready) {
    return <SetupPage status={status} onComplete={refresh} />;
  }
  return <>{children}</>;
}

// ──────────────────────────────────────────────────────────────────
// Helper render states
// ──────────────────────────────────────────────────────────────────

function BootScreen() {
  return (
    <div className="min-h-screen flex items-center justify-center bg-[var(--color-canvas)]">
      <div className="text-sm text-[var(--color-text-muted)]">Loading Clarion…</div>
    </div>
  );
}

function ApiUnreachable({ detail, onRetry }: { detail: string; onRetry: () => void }) {
  return (
    <div className="min-h-screen flex items-center justify-center bg-[var(--color-canvas)] p-4">
      <div className="max-w-md rounded-xl border border-[var(--color-danger)]/40 bg-[var(--color-canvas-elev1)] p-6 space-y-3">
        <h1 className="text-lg font-medium text-[var(--color-text)]">Can't reach the Clarion API</h1>
        <p className="text-sm text-[var(--color-text-muted)]">
          The frontend tried to read setup status but the backend didn't respond.
          Make sure it's running on port 8765.
        </p>
        <details className="text-xs">
          <summary className="text-[var(--color-text-faint)] cursor-pointer">Details</summary>
          <pre className="mt-2 p-2 bg-black/40 rounded font-mono overflow-x-auto">{detail}</pre>
        </details>
        <div className="flex gap-2 text-xs">
          <button
            type="button"
            onClick={onRetry}
            className="px-3 py-1.5 rounded-md bg-[var(--color-accent)] text-[var(--color-on-accent)] font-medium hover:bg-[var(--color-accent-hover)]"
          >
            Retry
          </button>
          <a
            href="https://127.0.0.1:8765/docs"
            className="px-3 py-1.5 rounded-md border border-[var(--color-border)] text-[var(--color-text-muted)] hover:border-[var(--color-border-strong)]"
          >
            Open API docs
          </a>
        </div>
        <p className="text-xs text-[var(--color-text-faint)]">
          Quick start:{" "}
          <code className="font-mono text-[var(--color-text-muted)]">just up && just api</code>
        </p>
      </div>
    </div>
  );
}
