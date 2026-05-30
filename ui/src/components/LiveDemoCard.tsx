/**
 * LiveDemoCard, the Dashboard's "Active demo" surface.
 *
 * Renders the right-column card from the v1 design canvas
 * (`clarion-redesign.html` line ~1175). Sits to the right of the
 * HeroBuildCard on the dashboard. Three states:
 *
 *   - **No active session**, neutral card, "Nothing live" copy, deep
 *     link to Plans for "Start a demo." This is the most common state.
 *   - **One active session**, the headline state. Title (host or
 *     company), session-id crumb, "Live" badge, the green telemetry
 *     strip with countdown, Extend / Stop actions.
 *   - **N > 1 active sessions**, surfaces the freshest, with a small
 *     "+N more" badge linking to the Plans page so the SE can find
 *     the other sessions.
 *
 * The card OWNS its own data fetching (5s poll of /api/demo/sessions)
 * because the dashboard page is otherwise pretty static and we don't
 * want every other widget to re-render every 5s. React Query handles
 * the stale-while-revalidate.
 *
 * Stop is destructive but it's a non-permanent (server-side SIGTERM,
 * row stays in `demo_sessions` history). No confirmation modal,  * the cost of an accidental click is "start it again" which is one
 * click and ~10s.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { ChevronRight, Plus, Square, Loader2 } from "lucide-react";

import { Button } from "@/components/Button";
import {
  listDemoSessions, stopDemoSession, extendDemoSession,
  type DemoSession,
} from "@/lib/api";
import { cn } from "@/lib/cn";

export function LiveDemoCard() {
  const qc = useQueryClient();
  const sessions = useQuery({
    queryKey: ["demo-sessions"],
    queryFn: listDemoSessions,
    refetchInterval: 5_000,
    refetchOnWindowFocus: true,
    retry: 1,
  });

  const stopMut = useMutation({
    mutationFn: (planId: string) => stopDemoSession(planId),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["demo-sessions"] }),
  });
  const extendMut = useMutation({
    mutationFn: (planId: string) => extendDemoSession(planId, 1),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["demo-sessions"] }),
  });

  const rows = sessions.data ?? [];
  const headline = rows[0];
  const moreCount = Math.max(0, rows.length - 1);

  return (
    <aside
      aria-label="Active demo sessions"
      className="rounded-2xl border border-[var(--color-border)] bg-[var(--color-canvas-elev1)] p-6 flex flex-col gap-4"
    >
      <header className="flex items-start justify-between gap-3">
        {/* When a session is active the whole headline becomes a link
            to the plan detail page so the SE can drill into the page
            where the demo's actually running (and where Start/Stop +
            telemetry panels live). The clickable region covers title
            + subtitle so the click target is forgiving. When nothing's
            live, the same block is a non-interactive empty state. */}
        {headline ? (
          <Link
            to={`/plans/${headline.plan_id}`}
            className="group min-w-0 flex-1 -m-1 p-1 rounded-md hover:bg-white/[0.02] transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-accent)]/40"
            title={`Open plan ${headline.plan_id.slice(0, 8)} — where this demo is running`}
          >
            <p className="text-sm font-medium text-[var(--color-text)] inline-flex items-center gap-1.5">
              Active demo
              <ChevronRight
                size={12}
                aria-hidden="true"
                className="text-[var(--color-text-faint)] opacity-0 -translate-x-0.5 group-hover:opacity-100 group-hover:translate-x-0 transition-[opacity,transform] duration-150"
              />
            </p>
            <p className="text-xs text-[var(--color-text-muted)] truncate mt-1 group-hover:text-[var(--color-text)] transition-colors">
              {friendlyTitle(headline)} · {headline.plan_id.slice(0, 6)}
            </p>
          </Link>
        ) : (
          <div className="min-w-0 flex-1">
            <p className="text-sm font-medium text-[var(--color-text)]">No active demo</p>
            <p className="text-xs text-[var(--color-text-muted)] truncate mt-1">
              Start one from a plan to begin streaming live telemetry.
            </p>
          </div>
        )}
        {headline && (
          <span className="inline-flex items-center gap-1.5 px-2 h-6 rounded-md font-mono text-[10px] uppercase tracking-wider text-[var(--color-live)] bg-[var(--color-live-bg)] border border-[color:var(--color-live)]/30 shrink-0">
            <span
              className="inline-block w-1.5 h-1.5 rounded-full"
              style={{ background: "var(--color-live)" }}
              aria-hidden="true"
            />
            Live
          </span>
        )}
      </header>

      {headline ? (
        <LiveStrip session={headline} />
      ) : (
        <EmptyStrip loading={sessions.isLoading} error={sessions.error} />
      )}

      <footer className="flex items-center gap-2 mt-auto">
        {headline ? (
          <>
            <Button
              size="sm"
              variant="ghost"
              onClick={() => extendMut.mutate(headline.plan_id)}
              disabled={extendMut.isPending}
            >
              {extendMut.isPending ? <Loader2 size={12} className="animate-spin" /> : <Plus size={12} />}
              Extend +1h
            </Button>
            <Button
              size="sm"
              variant="danger"
              onClick={() => stopMut.mutate(headline.plan_id)}
              disabled={stopMut.isPending}
              className="ml-auto"
            >
              {stopMut.isPending ? <Loader2 size={12} className="animate-spin" /> : <Square size={12} />}
              Stop demo
            </Button>
          </>
        ) : (
          <Link
            to="/plans"
            className="ml-auto text-xs text-[var(--color-accent)] hover:underline"
          >
            Open Plans →
          </Link>
        )}
        {moreCount > 0 && (
          <Link
            to="/plans"
            className="ml-2 px-1.5 py-0.5 rounded bg-[var(--color-accent-bg)] text-[var(--color-accent)] text-[10px] font-mono"
            title={`${moreCount} additional demo${moreCount === 1 ? "" : "s"} running`}
          >
            +{moreCount} more
          </Link>
        )}
      </footer>
    </aside>
  );
}

// ──────────────────────────────────────────────────────────────────
// LiveStrip, the green "Telemetry flowing" row inside the card
// ──────────────────────────────────────────────────────────────────

function LiveStrip({ session }: { session: DemoSession }) {
  const stopsIn = formatRemainingTime(session.seconds_until_expiry);
  const liveFor = session.started_at
    ? formatElapsed(session.started_at)
    : "just now";
  const isStarting = session.health === "starting";
  const isStale = session.health === "stale";

  return (
    <div
      className={cn(
        "flex items-center gap-3 rounded-[10px] px-4 py-3.5 border",
        isStale
          ? "bg-[var(--color-warning-bg)] border-[color:var(--color-warning)]/30"
          : "bg-[var(--color-live-bg)] border-[color:var(--color-live)]/30",
      )}
    >
      <span
        aria-hidden="true"
        className="live-dot relative inline-flex h-2 w-2 rounded-full shrink-0"
      />
      <div className="min-w-0 flex-1">
        <div
          className={cn(
            "text-sm font-semibold",
            isStale
              ? "text-[var(--color-warning)]"
              : "text-[var(--color-live)]",
          )}
        >
          {isStarting
            ? "Starting…"
            : isStale
              ? "Heartbeat stale"
              : "Telemetry flowing"}
        </div>
        <div className="text-[11px] text-[var(--color-text-muted)] font-mono mt-0.5">
          live for {liveFor}
        </div>
      </div>
      <span className="font-mono text-xs text-[var(--color-text-muted)] tabular-nums shrink-0">
        stops in {stopsIn}
      </span>
    </div>
  );
}

function EmptyStrip({
  loading, error,
}: { loading: boolean; error: unknown }) {
  let message = "Nothing live right now.";
  if (loading) message = "Checking sessions…";
  else if (error) message = "Couldn't reach /api/demo/sessions.";

  return (
    <div className="flex items-center gap-3 rounded-[10px] px-4 py-3.5 border border-dashed border-[var(--color-border)] bg-[var(--color-canvas)]/60">
      <span
        aria-hidden="true"
        className="inline-block w-2 h-2 rounded-full bg-[var(--color-text-faint)]"
      />
      <div className="text-xs text-[var(--color-text-muted)]">{message}</div>
    </div>
  );
}

// ──────────────────────────────────────────────────────────────────
// Helpers
// ──────────────────────────────────────────────────────────────────

function friendlyTitle(s: DemoSession): string {
  if (s.company) return s.company;
  if (s.url) {
    try { return new URL(s.url).host; }
    catch { return s.url; }
  }
  return "Unknown plan";
}

function formatRemainingTime(seconds: number): string {
  const secs = Math.max(0, Math.round(seconds));
  if (secs < 60) return `${secs}s`;
  const min = Math.floor(secs / 60);
  if (min < 60) return `${min}m`;
  const hr = Math.floor(min / 60);
  const remMin = min % 60;
  return `${hr}h ${remMin.toString().padStart(2, "0")}m`;
}

function formatElapsed(startedAtIso: string): string {
  const start = new Date(startedAtIso).getTime();
  const sec = Math.max(0, Math.round((Date.now() - start) / 1000));
  if (sec < 60) return `${sec}s`;
  const min = Math.floor(sec / 60);
  if (min < 60) return `${min}m`;
  const hr = Math.floor(min / 60);
  const remMin = min % 60;
  return `${hr}h ${remMin.toString().padStart(2, "0")}m`;
}
