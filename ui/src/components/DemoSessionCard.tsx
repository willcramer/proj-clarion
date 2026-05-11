/**
 * DemoSessionCard — Phase-1 "live telemetry" control surface.
 *
 * What it does (and doesn't):
 *  - Starts and stops the KG-entities emitter for one plan, on demand,
 *    via /api/demo/{start,stop}.
 *  - Polls /api/demo/status every 5s while a session is active so the
 *    "Live · 18s ago" badge reflects reality.
 *  - Auto-stops after a 2h cap server-side (configurable via slider on
 *    Start). User can Extend +1h.
 *  - Doesn't (yet): toggle business-event live-tail, dashboard warmup,
 *    Slack/email notify. Those are Phase 2.
 *
 * State machine:
 *
 *   idle ──[Start]──▶ starting ──(first heartbeat)──▶ live
 *                                                        │
 *                                ┌───────[Stop]──────────┤
 *                                │                       │
 *                                ▼                       │
 *                             stopped ◄──[expired]───────┘
 *
 * The UI doesn't directly differentiate `expired` vs `stopped` —
 * both render as "no active session" with a "Start new session"
 * button. The historical row is in `demo_sessions` if anyone wants
 * to query it.
 */
import {
  Play, Square, Circle, AlertCircle, RefreshCw, Clock, Plus,
} from "lucide-react";
import { useEffect, useState } from "react";

import { Card } from "@/components/Card";
import { Button } from "@/components/Button";
import { cn } from "@/lib/cn";

// ─── Types mirror /api/demo response shapes ───────────────────────

interface DemoStatusActive {
  active: true;
  plan_id: string;
  session_id: number;
  pid: number | null;
  status: "starting" | "live" | "stopped" | "expired" | "crashed";
  started_at: string;
  expires_at: string;
  last_heartbeat_at: string | null;
  seconds_since_heartbeat: number | null;
  seconds_until_expiry: number;
  health: "starting" | "live" | "stale";
}
interface DemoStatusInactive {
  active: false;
  plan_id: string;
}
type DemoStatus = DemoStatusActive | DemoStatusInactive;

// ─── API client (lightweight; co-located so we don't bloat api.ts) ─

async function demoApi<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api/demo${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", Accept: "application/json", ...(init?.headers ?? {}) },
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(body?.detail?.message ?? body?.detail ?? `HTTP ${res.status}`);
  }
  return res.json() as Promise<T>;
}
const demoApiCalls = {
  status: (planId: string) => demoApi<DemoStatus>(`/status?plan_id=${encodeURIComponent(planId)}`),
  start:  (planId: string, hours: number) =>
    demoApi<{ ok: boolean; session_id: number; pid: number; expires_at: string }>(
      "/start", { method: "POST", body: JSON.stringify({ plan_id: planId, duration_hours: hours }) },
    ),
  stop:   (planId: string) =>
    demoApi<{ ok: boolean; stopped: boolean; pid: number | null }>(
      "/stop", { method: "POST", body: JSON.stringify({ plan_id: planId }) },
    ),
  extend: (planId: string, hours: number) =>
    demoApi<{ ok: boolean; expires_at: string }>(
      "/extend", { method: "POST", body: JSON.stringify({ plan_id: planId, additional_hours: hours }) },
    ),
};

// ─── Component ────────────────────────────────────────────────────

export function DemoSessionCard({ planId }: { planId: string }) {
  const [status, setStatus] = useState<DemoStatus | null>(null);
  const [busy, setBusy] = useState<"start" | "stop" | "extend" | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [durationHours, setDurationHours] = useState(2);

  async function refresh() {
    try {
      setStatus(await demoApiCalls.status(planId));
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  }

  // Initial + reactive poll. When a session is active we poll every 5s
  // so the heartbeat freshness stays accurate; when idle we poll every
  // 30s (handles the case where someone else started a session via API).
  useEffect(() => {
    void refresh();
    const interval = setInterval(() => { void refresh(); },
      status?.active ? 5_000 : 30_000);
    return () => clearInterval(interval);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [planId, status?.active]);

  async function onStart() {
    setBusy("start"); setErr(null);
    try {
      await demoApiCalls.start(planId, durationHours);
      // Poll faster right after start so the "starting → live" transition
      // shows up snappily.
      void refresh();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(null);
    }
  }
  async function onStop() {
    setBusy("stop"); setErr(null);
    try { await demoApiCalls.stop(planId); void refresh(); }
    catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(null); }
  }
  async function onExtend() {
    setBusy("extend"); setErr(null);
    try { await demoApiCalls.extend(planId, 1); void refresh(); }
    catch (e) { setErr(e instanceof Error ? e.message : String(e)); }
    finally { setBusy(null); }
  }

  if (status === null) {
    return (
      <Card className="p-5">
        <div className="text-sm text-[var(--color-text-muted)]">
          Loading demo status…
        </div>
      </Card>
    );
  }

  return (
    <Card className="p-5 space-y-3">
      <header className="flex items-start justify-between gap-3">
        <div>
          <h3 className="text-sm font-medium text-[var(--color-text)] flex items-center gap-2">
            <Play size={14} className="text-[var(--color-accent)]" />
            Live demo telemetry
          </h3>
          <p className="text-xs text-[var(--color-text-muted)] mt-0.5">
            KG entities only appear in Grafana Cloud while telemetry is actively
            flowing. Use this to spin the emitter up right before a demo,
            then stop it when you're done.
          </p>
        </div>
      </header>

      {status.active ? (
        <ActiveView status={status} busy={busy} onStop={onStop} onExtend={onExtend} />
      ) : (
        <IdleView
          durationHours={durationHours}
          onDurationChange={setDurationHours}
          busy={busy}
          onStart={onStart}
        />
      )}

      {err && (
        <div className="text-xs text-[var(--color-danger)] flex items-start gap-1.5 pt-1 border-t border-[var(--color-border)]">
          <AlertCircle size={12} className="shrink-0 mt-0.5" />
          <span>{err}</span>
        </div>
      )}
    </Card>
  );
}

// ─── Idle (no active session) ─────────────────────────────────────

function IdleView({
  durationHours, onDurationChange, busy, onStart,
}: {
  durationHours: number;
  onDurationChange: (h: number) => void;
  busy: "start" | "stop" | "extend" | null;
  onStart: () => void;
}) {
  return (
    <div className="space-y-3">
      <div className="flex items-center gap-2 text-xs text-[var(--color-text-muted)]">
        <Circle size={10} className="text-[var(--color-text-faint)]" />
        <span>No active session. Telemetry is not flowing to Cloud.</span>
      </div>
      <div className="flex items-end gap-3">
        <div className="flex-1">
          <label
            htmlFor="demo-duration"
            className="block text-[10px] uppercase tracking-wider text-[var(--color-text-faint)] mb-1"
          >
            Auto-stop after
          </label>
          <div className="flex items-center gap-2">
            {[1, 2, 4, 8].map((h) => (
              <button
                key={h}
                type="button"
                onClick={() => onDurationChange(h)}
                aria-pressed={durationHours === h}
                className={cn(
                  "px-3 py-1.5 rounded-md text-xs font-medium transition-colors border",
                  durationHours === h
                    ? "border-[var(--color-accent-border)] bg-[var(--color-accent-bg)] text-[var(--color-accent)]"
                    : "border-[var(--color-border)] text-[var(--color-text-muted)] hover:border-[var(--color-border-strong)]",
                )}
              >
                {h}h
              </button>
            ))}
          </div>
        </div>
        <Button
          size="md"
          variant="primary"
          onClick={onStart}
          disabled={busy !== null}
        >
          {busy === "start" ? <RefreshCw size={12} className="animate-spin" /> : <Play size={12} />}
          {busy === "start" ? "Starting…" : "Start demo"}
        </Button>
      </div>
    </div>
  );
}

// ─── Active (session running) ─────────────────────────────────────

function ActiveView({
  status, busy, onStop, onExtend,
}: {
  status: DemoStatusActive;
  busy: "start" | "stop" | "extend" | null;
  onStop: () => void;
  onExtend: () => void;
}) {
  // Compose human-friendly heartbeat + expiry strings client-side. The
  // raw seconds-since-heartbeat is the source of truth; we just format.
  const hbLabel =
    status.health === "starting"
      ? "Spawning emitter… first push in ~30s"
      : status.seconds_since_heartbeat === null
      ? "—"
      : status.seconds_since_heartbeat < 60
      ? `Live · ${Math.round(status.seconds_since_heartbeat)}s ago`
      : `Live · ${Math.round(status.seconds_since_heartbeat / 60)}m ago`;

  const expiryLabel = formatDurationShort(status.seconds_until_expiry);
  const expiryUrgent = status.seconds_until_expiry < 5 * 60; // <5m

  return (
    <div className="space-y-3">
      <div className="flex items-center justify-between gap-3 px-3 py-2 rounded-lg bg-[var(--color-success-bg)]/40 border border-[var(--color-success)]/30">
        <div className="flex items-center gap-2 min-w-0">
          <span className={cn(
            "w-2 h-2 rounded-full",
            status.health === "live" ? "bg-[var(--color-success)] animate-pulse"
            : status.health === "starting" ? "bg-[var(--color-warning)] animate-pulse"
            : "bg-[var(--color-danger)]",
          )} />
          <span className={cn(
            "text-sm font-medium",
            status.health === "live" ? "text-[var(--color-success)]"
            : status.health === "starting" ? "text-[var(--color-warning)]"
            : "text-[var(--color-danger)]",
          )}>
            {hbLabel}
          </span>
        </div>
        <div className="text-xs text-[var(--color-text-muted)] flex items-center gap-1.5">
          <Clock size={11} />
          <span>
            Auto-stop in{" "}
            <span className={cn(
              "font-mono",
              expiryUrgent && "text-[var(--color-warning)] font-medium",
            )}>
              {expiryLabel}
            </span>
          </span>
        </div>
      </div>

      <div className="flex items-center gap-2">
        <Button
          size="sm"
          variant="secondary"
          onClick={onExtend}
          disabled={busy !== null}
          title="Push the auto-stop deadline back by 1 hour"
        >
          {busy === "extend" ? <RefreshCw size={12} className="animate-spin" /> : <Plus size={12} />}
          Extend +1h
        </Button>
        <Button
          size="sm"
          variant="danger"
          onClick={onStop}
          disabled={busy !== null}
          className="ml-auto"
        >
          {busy === "stop" ? <RefreshCw size={12} className="animate-spin" /> : <Square size={12} />}
          {busy === "stop" ? "Stopping…" : "Stop demo"}
        </Button>
      </div>

      <div className="text-[10px] font-mono text-[var(--color-text-faint)]">
        session #{status.session_id} · pid {status.pid ?? "—"} · started {new Date(status.started_at).toLocaleTimeString()}
      </div>
    </div>
  );
}

function formatDurationShort(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return `${s}s`;
  const m = Math.floor(s / 60);
  if (m < 60) {
    const rs = s % 60;
    return rs ? `${m}m ${rs}s` : `${m}m`;
  }
  const h = Math.floor(m / 60);
  const rm = m % 60;
  return rm ? `${h}h ${rm}m` : `${h}h`;
}
