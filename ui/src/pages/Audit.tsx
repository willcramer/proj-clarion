/**
 * AuditPage, `/audit`.
 *
 * Forensic log surface that bundles every kind of audit data the SE
 * Console produces, each in its own visually-distinct section. Today:
 *
 *   1. Demo sessions, start/stop/expire/crash of the live emitter.
 *   2. Plan changes, review_state transitions and edits on plans.
 *
 * Both sections page independently so a busy demo-session log doesn't
 * push plan-change events off the screen.
 *
 * The same DemoHistorySection is also embedded on Plans-detail with a
 * `planId` prop so the per-plan audit history sits next to the plan
 * itself (where the SE actually starts demos from).
 */
import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { History, GitCommitHorizontal, MessageCircle } from "lucide-react";

import { Badge, type BadgeTone } from "@/components/Badge";
import { Card } from "@/components/Card";
import { Pagination } from "@/components/Pagination";
import {
  listDemoHistory, listPlanAudit, listProfileAudit,
  type DemoHistoryRow, type GlobalAuditEntry, type ProfileAuditEntry,
} from "@/lib/api";
import { cn } from "@/lib/cn";

export function AuditPage() {
  return (
    <div className="space-y-8">
      <header>
        <div className="text-[11px] font-mono uppercase tracking-[0.08em] text-[var(--color-text-faint)]">
          Audit
        </div>
        <h1 className="mt-2 text-[28px] font-semibold tracking-tight leading-tight text-[var(--color-text)]">
          Activity &amp; history
        </h1>
        <p className="text-[var(--color-text-muted)] mt-1 text-sm max-w-2xl">
          Every demo emitter that&rsquo;s spun up on this stack, every plan-state
          transition, and every profile extension. Each section pages independently,
          newest first.
        </p>
      </header>

      <DemoHistorySection />
      <PlanChangesSection />
      <ProfileChangesSection />
    </div>
  );
}

// ──────────────────────────────────────────────────────────────────
// Section 1, Demo sessions
// ──────────────────────────────────────────────────────────────────

/** Demo-session audit. Used standalone on the global Audit page AND
 *  embedded on Plans-detail with `planId` so the SE has the per-plan
 *  demo log next to the start/stop controls. */
export function DemoHistorySection({ planId }: { planId?: string } = {}) {
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);
  const offset = (page - 1) * pageSize;

  const history = useQuery({
    queryKey: ["demo-history", planId ?? "all", offset, pageSize],
    queryFn: () => listDemoHistory({
      limit: pageSize,
      offset,
      plan_id: planId,
    }),
    refetchInterval: 10_000,
    placeholderData: (prev) => prev,
  });

  const rows = history.data?.history ?? [];
  const total = history.data?.total ?? 0;
  const liveOnPage = useMemo(
    () => rows.filter((r) => r.status === "starting" || r.status === "live").length,
    [rows],
  );

  return (
    <section aria-label="Demo sessions audit">
      <SectionHeader
        icon={History}
        title="Demo sessions"
        subtitle="Live emitter start, stop, expire, crash"
        rightHint={
          history.isLoading
            ? "Loading…"
            : `${total.toLocaleString()} total${liveOnPage > 0 ? ` · ${liveOnPage} live on this page` : ""}`
        }
      />
      <Card>
        {history.isLoading ? (
          <div className="p-8 text-center text-[var(--color-text-faint)]">Loading…</div>
        ) : total === 0 ? (
          <EmptySection
            icon={History}
            title="No demo sessions yet"
            hint={planId
              ? "No demos have run for this plan. Start one from the controls above to begin streaming live telemetry."
              : "Sessions appear here once you start a demo from any plan."}
          />
        ) : (
          <>
            <table className="w-full text-sm">
              <thead className="text-[10px] text-[var(--color-text-faint)] uppercase tracking-wider font-mono border-b border-[var(--color-border)]">
                <tr>
                  <th className="text-left font-medium px-4 py-2.5">Session</th>
                  <th className="text-left font-medium px-4 py-2.5">Target</th>
                  <th className="text-left font-medium px-4 py-2.5">Status</th>
                  <th className="text-right font-medium px-4 py-2.5">Started</th>
                  <th className="text-right font-medium px-4 py-2.5">Duration</th>
                  <th className="text-right font-medium px-4 py-2.5">Closed</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <DemoRow key={r.session_id} row={r} hidePlanLink={!!planId} />
                ))}
              </tbody>
            </table>
            <Pagination
              page={page}
              pageSize={pageSize}
              total={total}
              onPageChange={setPage}
              onPageSizeChange={(n) => { setPageSize(n); setPage(1); }}
            />
          </>
        )}
      </Card>
    </section>
  );
}

function DemoRow({ row, hidePlanLink }: { row: DemoHistoryRow; hidePlanLink: boolean }) {
  const navigate = useNavigate();
  const live = row.status === "starting" || row.status === "live";
  const flag = live
    ? "live"
    : row.status === "crashed" || row.status === "expired"
      ? "warn"
      : "muted";
  return (
    <tr
      onClick={hidePlanLink ? undefined : () => navigate(`/plans/${row.plan_id}`)}
      className={cn(
        "border-b border-[var(--color-border)] last:border-0",
        !hidePlanLink && "hover:bg-white/[0.02] cursor-pointer",
        "transition-colors",
      )}
    >
      <td className="px-4 py-3 font-mono text-xs whitespace-nowrap">
        <span className={cn("row-flag", flag)} aria-hidden="true" />
        {String(row.session_id).padStart(4, "0")}
      </td>
      <td className="px-4 py-3 text-[var(--color-text)]">
        {row.company ?? hostOf(row.url)}
        {row.company && row.url && (
          <span className="ml-2 font-mono text-[11px] text-[var(--color-text-faint)]">
            {hostOf(row.url)}
          </span>
        )}
      </td>
      <td className="px-4 py-3">
        <Badge tone={demoStatusToTone(row.status)}>{row.status}</Badge>
      </td>
      <td className="px-4 py-3 text-right text-xs text-[var(--color-text-muted)] tabular-nums">
        {row.started_at ? formatDateTime(row.started_at) : ", "}
      </td>
      <td className="px-4 py-3 text-right font-mono text-xs tabular-nums text-[var(--color-text-muted)]">
        {row.seconds_active != null ? formatDuration(row.seconds_active) : ", "}
      </td>
      <td className="px-4 py-3 text-right text-xs text-[var(--color-text-faint)] tabular-nums">
        {row.finished_at ? formatRelative(row.finished_at) : live ? "still live" : ", "}
      </td>
    </tr>
  );
}

function demoStatusToTone(status: DemoHistoryRow["status"]): BadgeTone {
  switch (status) {
    case "live":     return "success";
    case "starting": return "info";
    case "stopped":  return "neutral";
    case "expired":  return "warning";
    case "crashed":  return "danger";
  }
}

// ──────────────────────────────────────────────────────────────────
// Section 2, Plan changes (review_state transitions and edits)
// ──────────────────────────────────────────────────────────────────

function PlanChangesSection() {
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);
  const offset = (page - 1) * pageSize;

  const audit = useQuery({
    queryKey: ["plan-audit-global", offset, pageSize],
    queryFn: () => listPlanAudit({ limit: pageSize, offset }),
    refetchInterval: 30_000,
    placeholderData: (prev) => prev,
  });

  const rows = audit.data?.entries ?? [];
  const total = audit.data?.total ?? 0;

  return (
    <section aria-label="Plan changes audit">
      <SectionHeader
        icon={GitCommitHorizontal}
        title="Plan changes"
        subtitle="Review state transitions, approvals, edits"
        rightHint={audit.isLoading ? "Loading…" : `${total.toLocaleString()} total`}
      />
      <Card>
        {audit.isLoading ? (
          <div className="p-8 text-center text-[var(--color-text-faint)]">Loading…</div>
        ) : total === 0 ? (
          <EmptySection
            icon={GitCommitHorizontal}
            title="No plan changes recorded"
            hint="Approvals, state transitions, and edits will appear here as you work with plans."
          />
        ) : (
          <>
            <table className="w-full text-sm">
              <thead className="text-[10px] text-[var(--color-text-faint)] uppercase tracking-wider font-mono border-b border-[var(--color-border)]">
                <tr>
                  <th className="text-left font-medium px-4 py-2.5">When</th>
                  <th className="text-left font-medium px-4 py-2.5">Plan</th>
                  <th className="text-left font-medium px-4 py-2.5">Actor</th>
                  <th className="text-left font-medium px-4 py-2.5">Action</th>
                  <th className="text-left font-medium px-4 py-2.5">Transition</th>
                  <th className="text-left font-medium px-4 py-2.5">Note</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r, i) => (
                  <PlanChangeRow key={`${r.timestamp}-${i}`} row={r} />
                ))}
              </tbody>
            </table>
            <Pagination
              page={page}
              pageSize={pageSize}
              total={total}
              onPageChange={setPage}
              onPageSizeChange={(n) => { setPageSize(n); setPage(1); }}
            />
          </>
        )}
      </Card>
    </section>
  );
}

function PlanChangeRow({ row }: { row: GlobalAuditEntry }) {
  const navigate = useNavigate();
  return (
    <tr
      onClick={row.plan_id ? () => navigate(`/plans/${row.plan_id}`) : undefined}
      className={cn(
        "border-b border-[var(--color-border)] last:border-0",
        row.plan_id && "hover:bg-white/[0.02] cursor-pointer",
        "transition-colors",
      )}
    >
      <td className="px-4 py-3 text-xs text-[var(--color-text-muted)] tabular-nums whitespace-nowrap">
        {formatDateTime(row.timestamp)}
      </td>
      <td className="px-4 py-3 text-[var(--color-text)]">
        {row.company ?? hostOf(row.url) ?? <span className="text-[var(--color-text-faint)]">, </span>}
        {row.plan_id && (
          <span className="ml-2 font-mono text-[11px] text-[var(--color-text-faint)]">
            {row.plan_id.slice(0, 8)}
          </span>
        )}
      </td>
      <td className="px-4 py-3 text-xs text-[var(--color-text-muted)] font-mono">
        {row.actor}
      </td>
      <td className="px-4 py-3">
        <span className="font-mono text-[11px] text-[var(--color-text)]">{row.action}</span>
      </td>
      <td className="px-4 py-3 text-xs text-[var(--color-text-muted)] font-mono whitespace-nowrap">
        {row.from_state && row.to_state ? (
          <>
            <span>{row.from_state}</span>
            <span className="mx-1.5 text-[var(--color-text-faint)]">&rarr;</span>
            <span className="text-[var(--color-accent)]">{row.to_state}</span>
          </>
        ) : (
          <span className="text-[var(--color-text-faint)]">, </span>
        )}
      </td>
      <td className="px-4 py-3 text-xs text-[var(--color-text-faint)] max-w-md truncate">
        {row.note ?? <span>, </span>}
      </td>
    </tr>
  );
}

// ──────────────────────────────────────────────────────────────────
// Section header + empty state, shared across both sections
// ──────────────────────────────────────────────────────────────────

// ──────────────────────────────────────────────────────────────────
// Section 3, Profile changes (extend research history)
// ──────────────────────────────────────────────────────────────────

function ProfileChangesSection() {
  const [page, setPage] = useState(1);
  const [pageSize, setPageSize] = useState(10);
  const offset = (page - 1) * pageSize;

  const audit = useQuery({
    queryKey: ["profile-audit-global", offset, pageSize],
    queryFn: () => listProfileAudit({ limit: pageSize, offset }),
    refetchInterval: 30_000,
    placeholderData: (prev) => prev,
  });

  const rows = audit.data?.entries ?? [];
  const total = audit.data?.total ?? 0;

  return (
    <section aria-label="Profile extend audit">
      <SectionHeader
        icon={MessageCircle}
        title="Profile changes"
        subtitle="Extend-research prompts and the additions they produced"
        rightHint={audit.isLoading ? "Loading…" : `${total.toLocaleString()} total`}
      />
      <Card>
        {audit.isLoading ? (
          <div className="p-8 text-center text-[var(--color-text-faint)]">Loading…</div>
        ) : total === 0 ? (
          <EmptySection
            icon={MessageCircle}
            title="No profile extends recorded"
            hint="Use the Extend research chat on any profile to add channels, signals, or entities. Each prompt is logged here."
          />
        ) : (
          <>
            <table className="w-full text-sm">
              <thead className="text-[10px] text-[var(--color-text-faint)] uppercase tracking-wider font-mono border-b border-[var(--color-border)]">
                <tr>
                  <th className="text-left font-medium px-4 py-2.5">When</th>
                  <th className="text-left font-medium px-4 py-2.5">Profile</th>
                  <th className="text-left font-medium px-4 py-2.5">Prompt</th>
                  <th className="text-left font-medium px-4 py-2.5">Summary</th>
                  <th className="text-left font-medium px-4 py-2.5">Additions</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((r) => (
                  <ProfileChangeRow key={r.audit_id} row={r} />
                ))}
              </tbody>
            </table>
            <Pagination
              page={page}
              pageSize={pageSize}
              total={total}
              onPageChange={setPage}
              onPageSizeChange={(n) => { setPageSize(n); setPage(1); }}
            />
          </>
        )}
      </Card>
    </section>
  );
}

function ProfileChangeRow({ row }: { row: ProfileAuditEntry }) {
  const navigate = useNavigate();
  const totalAdds = Object.values(row.additions).reduce((a, b) => a + b, 0);
  return (
    <tr
      onClick={() => navigate(`/profiles/${row.profile_id}`)}
      className={cn(
        "border-b border-[var(--color-border)] last:border-0",
        "hover:bg-white/[0.02] cursor-pointer transition-colors",
        !row.applied && "opacity-70",
      )}
    >
      <td className="px-4 py-3 text-xs text-[var(--color-text-muted)] tabular-nums whitespace-nowrap">
        {formatDateTime(row.timestamp)}
      </td>
      <td className="px-4 py-3 text-[var(--color-text)]">
        {row.company ?? hostOf(row.url ?? null) ?? <span className="text-[var(--color-text-faint)]">—</span>}
        <span className="ml-2 font-mono text-[11px] text-[var(--color-text-faint)]">
          {row.profile_id}
        </span>
      </td>
      <td className="px-4 py-3 text-xs text-[var(--color-text-muted)] max-w-[280px] truncate" title={row.prompt}>
        {row.prompt}
      </td>
      <td className="px-4 py-3 text-xs text-[var(--color-text-muted)] max-w-[320px] truncate" title={row.summary}>
        {row.summary}
      </td>
      <td className="px-4 py-3">
        {!row.applied ? (
          <Badge tone="neutral">no-op</Badge>
        ) : totalAdds === 0 ? (
          <Badge tone="neutral">empty</Badge>
        ) : (
          <div className="flex flex-wrap gap-1.5">
            {Object.entries(row.additions).map(([field, count]) => (
              <span
                key={field}
                className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded font-mono text-[10px] bg-[var(--color-accent-bg)] text-[var(--color-accent)]"
              >
                +{count} {field.replace(/_/g, " ")}
              </span>
            ))}
          </div>
        )}
      </td>
    </tr>
  );
}


function SectionHeader({
  icon: Icon, title, subtitle, rightHint,
}: {
  icon: typeof History;
  title: string;
  subtitle: string;
  rightHint?: string;
}) {
  return (
    <div className="flex items-end justify-between gap-4 flex-wrap mb-3">
      <div className="flex items-center gap-3">
        <span
          className="inline-flex items-center justify-center w-8 h-8 rounded-md text-[var(--color-accent)]"
          style={{
            background: "var(--color-accent-bg)",
            border: "1px solid var(--color-accent-border)",
          }}
          aria-hidden="true"
        >
          <Icon size={15} />
        </span>
        <div>
          <h2 className="text-lg font-medium leading-tight text-[var(--color-text)]">{title}</h2>
          <p className="text-xs text-[var(--color-text-muted)]">{subtitle}</p>
        </div>
      </div>
      {rightHint && (
        <div className="text-xs text-[var(--color-text-muted)] font-mono">{rightHint}</div>
      )}
    </div>
  );
}

function EmptySection({
  icon: Icon, title, hint,
}: {
  icon: typeof History;
  title: string;
  hint: string;
}) {
  return (
    <div className="flex flex-col items-center justify-center py-16 text-center">
      <Icon size={28} className="text-[var(--color-text-faint)] mb-3" />
      <div className="text-sm font-medium">{title}</div>
      <div className="text-xs text-[var(--color-text-muted)] mt-1 max-w-sm">{hint}</div>
    </div>
  );
}

// ──────────────────────────────────────────────────────────────────
// Formatting helpers, shared with the inline DemoRow above
// ──────────────────────────────────────────────────────────────────

function hostOf(url: string | null): string {
  if (!url) return ", ";
  try { return new URL(url).host; } catch { return url; }
}

function formatDuration(seconds: number): string {
  const sec = Math.max(0, Math.round(seconds));
  if (sec < 60) return `${sec}s`;
  const min = Math.floor(sec / 60);
  const remSec = sec % 60;
  if (min < 60) return `${min}m ${remSec.toString().padStart(2, "0")}s`;
  const hr = Math.floor(min / 60);
  const remMin = min % 60;
  return `${hr}h ${remMin.toString().padStart(2, "0")}m`;
}

function formatDateTime(iso: string): string {
  const d = new Date(iso);
  const today = new Date();
  const sameDay =
    d.getFullYear() === today.getFullYear() &&
    d.getMonth() === today.getMonth() &&
    d.getDate() === today.getDate();
  if (sameDay) {
    return d.toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" });
  }
  return d.toLocaleString(undefined, {
    month: "short", day: "numeric",
    hour: "2-digit", minute: "2-digit",
  });
}

function formatRelative(iso: string): string {
  const d = new Date(iso);
  const diffMs = Date.now() - d.getTime();
  const sec = Math.round(diffMs / 1000);
  if (sec < 60) return `${sec}s ago`;
  const min = Math.round(sec / 60);
  if (min < 60) return `${min}m ago`;
  const hr = Math.round(min / 60);
  if (hr < 24) return `${hr}h ago`;
  const day = Math.round(hr / 24);
  return `${day}d ago`;
}
