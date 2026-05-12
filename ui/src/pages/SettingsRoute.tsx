/**
 * SettingsRoute, the `/setup` route reachable from inside the app
 * (via UserMenu → Settings), as opposed to the first-run gate.
 *
 * Reuses <SetupPage> wholesale; the only differences from the gated
 * version are:
 *   - We fetch the current status ourselves (the gate normally passes
 *     it in via prop).
 *   - On save, instead of unmounting and showing the app, we just
 *     refetch status and stay on the page, the UserMenu's identity
 *     query will refresh on next mount.
 *
 * Routing-wise this lives inside the Layout so the TopBar stays visible
 * (the UserMenu is the entry point, so it should remain reachable).
 */
import { useEffect, useState } from "react";

import { setupApi, type SetupStatus } from "@/lib/setup-api";
import { SetupPage } from "@/pages/Setup";

export function SettingsRoute() {
  const [status, setStatus] = useState<SetupStatus | null>(null);

  async function refresh() {
    try {
      setStatus(await setupApi.status());
    } catch (e) {
      // If the API is unreachable we don't want a blank screen; render
      // the SetupPage with an empty status so the form is at least
      // visible. The user will see error toasts when they try to save.
      console.error("setup status failed:", e);
      setStatus({ ready: false, missing: [], present: [], groups: {} });
    }
  }

  useEffect(() => { void refresh(); }, []);

  if (status === null) {
    return (
      <div className="py-8 text-center text-sm text-[var(--color-text-muted)]">
        Loading settings…
      </div>
    );
  }
  return <SetupPage status={status} onComplete={refresh} mode="settings" />;
}
