/**
 * ClarionAssistant — the single, app-wide agent surface.
 *
 * A docked right-side drawer (⌘J or the TopBar button) that streams the
 * Clarion Assistant's replies, renders the tools it calls as inline
 * chips, and persists every conversation. It's route-aware: opening it
 * on /plans/<id> pins that plan as context so "rebuild this" / "extend
 * the research and re-plan" Just Work. It is the ONE chat window — the
 * old per-page Refine and Extend panels open into this instead.
 *
 * The agent can DO things (start/re-run builds, extend profiles, approve
 * plans, start/stop demos), so build actions surface as "View build →"
 * links right in the transcript.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Sparkles, X, Plus, History as HistoryIcon, Send, Square,
  Loader2, Check, AlertCircle, Wrench, Hammer, Trash2, ArrowUpRight,
  Database, MessageSquare, Target, ChevronDown, ShieldCheck, Zap, Play,
} from "lucide-react";

import { cn } from "@/lib/cn";
import {
  archiveConversation, getConversation, listConversations, streamClarionChat,
  resumeClarionChat, listPlans, listProfiles,
  type AssistantContextScope, type AssistantToolCall, type AssistantToolResult,
  type AssistantToolResultDetail, type AssistantTurn, type ClarionChatHandlers,
} from "@/lib/api";
import { deriveScopeFromPath, useAssistant } from "@/lib/AssistantContext";

// ── Tool presentation ──────────────────────────────────────────────

const TOOL_LABEL: Record<string, string> = {
  run_build:          "Start build",
  run_pipeline_phase: "Run build phase",
  extend_profile:     "Extend profile",
  approve_plan:       "Approve plan",
  start_demo:         "Start live demo",
  stop_demo:          "Stop live demo",
  cancel_build:       "Cancel build",
  list_profiles:      "List profiles",
  get_profile:        "Read profile",
  list_plans:         "List plans",
  get_plan:           "Read plan",
  list_pipelines:     "List builds",
  get_pipeline:       "Read build",
  list_demo_sessions: "List live demos",
  get_audit_log:      "Read audit log",
};

function toolLabel(name: string): string {
  return TOOL_LABEL[name] ?? name.replace(/_/g, " ");
}

/** A one-line summary of the most relevant tool input for the chip. */
function toolInputHint(call: AssistantToolCall): string | null {
  const i = call.input ?? {};
  if (call.name === "run_build") return (i.url as string) ?? null;
  if (call.name === "run_pipeline_phase")
    return [i.phase, i.plan_id ? `plan ${String(i.plan_id).slice(0, 8)}` : null].filter(Boolean).join(" · ");
  if (call.name === "extend_profile") return (i.prompt as string) ?? (i.profile_id as string) ?? null;
  if (i.plan_id) return `plan ${String(i.plan_id).slice(0, 8)}`;
  if (i.profile_id) return String(i.profile_id);
  const first = Object.values(i).find((v) => typeof v === "string");
  return (first as string) ?? null;
}

/** Pull a render-ready detail object out of a tool result, whether it's
 *  a live SSE event (has `.detail`) or a persisted turn (has `.content`
 *  JSON string). */
function resultDetail(r?: AssistantToolResult): AssistantToolResultDetail | null {
  if (!r) return null;
  if (r.detail && Object.keys(r.detail).length > 0) return r.detail;
  if (r.content) {
    try {
      const o = JSON.parse(r.content);
      if (o && typeof o === "object") return o as AssistantToolResultDetail;
    } catch { /* not JSON (e.g. plain string result) */ }
  }
  return null;
}

// ── Small helpers ──────────────────────────────────────────────────

function relTime(iso: string | null): string {
  if (!iso) return "";
  const s = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return `${Math.floor(s / 86400)}d ago`;
}

/** Render assistant text, turning internal paths into router links. */
function LinkifiedText({ text }: { text: string }) {
  const parts = text.split(/(\/(?:pipelines|plans|profiles)\/[A-Za-z0-9-]+)/g);
  return (
    <span className="whitespace-pre-wrap break-words">
      {parts.map((p, i) =>
        /^\/(pipelines|plans|profiles)\//.test(p) ? (
          <Link
            key={i}
            to={p}
            className="text-[var(--color-accent)] underline underline-offset-2 hover:opacity-80"
          >
            {p}
          </Link>
        ) : (
          <span key={i}>{p}</span>
        ),
      )}
    </span>
  );
}

function scopeLabel(scope: AssistantContextScope): string | null {
  if (scope.plan_id) return `plan ${scope.plan_id.slice(0, 8)}`;
  if (scope.profile_id) return scope.profile_id;
  if (scope.pipeline_id) return `build ${scope.pipeline_id.slice(0, 8)}`;
  return null;
}

const EXAMPLE_PROMPTS = [
  "Build a demo for https://grafana.com",
  "Refine this plan: add a data pipeline service and re-plan",
  "Which of my plans aren't provisioned yet?",
  "Start the live demo for this plan",
];

// localStorage key for the approval-mode preference.
const AUTO_APPROVE_KEY = "clarion.autoApprove";

// Build-kicking tools that pause for approval — must match the backend's
// NEEDS_APPROVAL_TOOL_NAMES. When the last turn is an assistant turn with
// one of these unanswered, the conversation is paused awaiting approval.
const BUILD_TOOL_NAMES = new Set(["run_build", "run_pipeline_phase"]);

/** Human one-liner for the approval card (mirrors the server's). */
function approvalMessage(call: AssistantToolCall): string {
  const i = call.input ?? {};
  if (call.name === "run_build") return `Start a full build for ${(i.url as string) || "this company"}?`;
  if (call.name === "run_pipeline_phase") return `Run the '${(i.phase as string) || "selected"}' build phase?`;
  return `Run ${call.name}?`;
}

// ── Live (in-flight) turn shape ────────────────────────────────────

interface LiveTurn {
  text: string;
  calls: AssistantToolCall[];
  results: Record<string, AssistantToolResult>;
}

// ── Component ──────────────────────────────────────────────────────

export function ClarionAssistant() {
  const ctx = useAssistant();
  const location = useLocation();
  const qc = useQueryClient();

  const [convId, setConvId] = useState<number | null>(null);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [showPicker, setShowPicker] = useState(false);
  const [pendingUser, setPendingUser] = useState<string | null>(null);
  const [live, setLive] = useState<LiveTurn | null>(null);
  // A scope the SE explicitly picked from the in-drawer dropdown. Wins
  // over the route/opener scope so they can re-aim the assistant without
  // navigating. `null` = follow the page; `{}` = explicit "no scope".
  const [scopeOverride, setScopeOverride] = useState<AssistantContextScope | null>(null);
  // Approval mode. false (default) = pause for an explicit Approve before
  // the assistant kicks off a build; true = run builds hands-free.
  // Persisted so the SE's preference survives reloads.
  const [autoApprove, setAutoApprove] = useState<boolean>(() => {
    try { return localStorage.getItem(AUTO_APPROVE_KEY) === "1"; } catch { return false; }
  });
  function toggleAutoApprove() {
    setAutoApprove((v) => {
      const next = !v;
      try { localStorage.setItem(AUTO_APPROVE_KEY, next ? "1" : "0"); } catch { /* private mode */ }
      return next;
    });
  }

  const abortRef = useRef<AbortController | null>(null);
  const scrollRef = useRef<HTMLDivElement | null>(null);
  const taRef = useRef<HTMLTextAreaElement | null>(null);
  const firstNonceRef = useRef(ctx.newThreadNonce);

  // Route- + opener-derived scope (the page's intent).
  const pageScope = useMemo<AssistantContextScope>(
    () => ({ ...deriveScopeFromPath(location.pathname), ...(ctx.scope ?? {}) }),
    [location.pathname, ctx.scope],
  );
  // The override wins when set; otherwise follow the page.
  const effectiveScope = scopeOverride ?? pageScope;
  const sLabel = scopeLabel(effectiveScope);

  // When a page explicitly opens the assistant with a scope, that's a
  // fresh intent — drop any manual override so the page wins again.
  useEffect(() => {
    if (ctx.scope) setScopeOverride(null);
  }, [ctx.scope]);

  // Conversation list for the picker (only while open).
  const convList = useQuery({
    queryKey: ["assistant-conversations"],
    queryFn: () => listConversations({ status: "active", limit: 50 }),
    enabled: ctx.open,
  });

  // Active conversation turns. Disabled while streaming so a focus
  // refetch can't duplicate the optimistic user turn mid-flight.
  const conv = useQuery({
    queryKey: ["assistant-conversation", convId],
    queryFn: () => getConversation(convId as number),
    enabled: ctx.open && convId != null && !busy,
  });

  // Plans + profiles power the context-switcher dropdown. Shared cache
  // keys with the rest of the app, so finish()'s invalidations refresh
  // these too.
  const plansQ = useQuery({ queryKey: ["plans"], queryFn: () => listPlans(), enabled: ctx.open });
  const profilesQ = useQuery({ queryKey: ["profiles"], queryFn: listProfiles, enabled: ctx.open });

  // profile_id → human label, for naming both profiles and the plans
  // built from them in the dropdown.
  const profileLabel = useMemo(() => {
    const m: Record<string, string> = {};
    for (const p of profilesQ.data ?? []) {
      m[p.profile_id] = p.company_name || p.primary_url || p.profile_id;
    }
    return m;
  }, [profilesQ.data]);

  const planOptions = useMemo(
    () => (plansQ.data ?? [])
      .filter((p) => !p.pending)
      .slice(0, 50)
      .map((p) => ({
        id: p.plan_id,
        label: `${p.plan_id_short || p.plan_id.slice(0, 8)}${
          profileLabel[p.source_profile_id] ? ` · ${profileLabel[p.source_profile_id]}` : ""
        }`,
      })),
    [plansQ.data, profileLabel],
  );
  const profileOptions = useMemo(
    () => (profilesQ.data ?? [])
      .filter((p) => !p.pending)
      .slice(0, 50)
      .map((p) => ({ id: p.profile_id, label: p.company_name || p.primary_url || p.profile_id })),
    [profilesQ.data],
  );

  // Encode the current effective scope as the <select> value.
  const scopeSelectValue =
    effectiveScope.plan_id ? `plan:${effectiveScope.plan_id}` :
    effectiveScope.profile_id ? `profile:${effectiveScope.profile_id}` :
    "";

  function onScopeSelect(v: string) {
    if (!v) { setScopeOverride({}); return; }            // explicit "no scope"
    const sep = v.indexOf(":");
    const kind = v.slice(0, sep);
    const id = v.slice(sep + 1);
    if (kind === "plan") setScopeOverride({ plan_id: id });
    else if (kind === "profile") setScopeOverride({ profile_id: id });
  }

  // Page asked for a fresh thread → reset. (Declared BEFORE the seed
  // effect so a same-call seed wins the input.)
  useEffect(() => {
    if (ctx.newThreadNonce === firstNonceRef.current) return;
    firstNonceRef.current = ctx.newThreadNonce;
    abortRef.current?.abort();
    setConvId(null);
    setLive(null);
    setPendingUser(null);
    setBusy(false);
    setErr(null);
    setInput("");
  }, [ctx.newThreadNonce]);

  // Consume a seed prompt from a page opener.
  useEffect(() => {
    if (ctx.seedPrompt != null) {
      setInput(ctx.seedPrompt);
      ctx.consumeSeed();
      // focus the textarea so the SE can hit send (or edit)
      queueMicrotask(() => taRef.current?.focus());
    }
  }, [ctx.seedPrompt, ctx]);

  // Esc closes the drawer.
  useEffect(() => {
    if (!ctx.open) return;
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape" && !showPicker) ctx.close();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [ctx.open, ctx, showPicker]);

  // Auto-scroll to the latest message.
  const turns = conv.data?.turns ?? [];
  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [turns.length, live?.text, live?.calls.length, pendingUser, ctx.open]);

  // Map tool_use_id → result, from the conversation's tool turns.
  const resultsByToolId = useMemo(() => {
    const m: Record<string, AssistantToolResult> = {};
    for (const t of turns) {
      for (const r of t.tool_results ?? []) m[r.tool_use_id] = r;
    }
    return m;
  }, [turns]);

  // A build paused awaiting approval. Derived from the persisted turns
  // (the last turn is an assistant turn carrying an unanswered build
  // tool_call) so it survives a page reload — no separate state needed.
  // Hidden while a stream is in flight.
  const pendingBuild = useMemo<AssistantToolCall | null>(() => {
    if (busy || live) return null;
    const last = turns[turns.length - 1];
    if (last?.role === "assistant" && last.tool_calls) {
      return last.tool_calls.find((tc) => BUILD_TOOL_NAMES.has(tc.name)) ?? null;
    }
    return null;
  }, [turns, busy, live]);

  async function finish(id: number) {
    try {
      // Force the authoritative turns into cache BEFORE we drop the
      // optimistic ones, so the transcript doesn't flicker empty.
      await qc.fetchQuery({
        queryKey: ["assistant-conversation", id],
        queryFn: () => getConversation(id),
      });
    } catch { /* ignore; the enabled query will retry */ }
    setConvId(id);
    setLive(null);
    setPendingUser(null);
    setBusy(false);
    // The agent may have mutated state — refresh the surfaces that show it.
    qc.invalidateQueries({ queryKey: ["assistant-conversations"] });
    qc.invalidateQueries({ queryKey: ["pipelines"] });
    qc.invalidateQueries({ queryKey: ["plans"] });
    qc.invalidateQueries({ queryKey: ["profiles"] });
    qc.invalidateQueries({ queryKey: ["dashboard"] });
    qc.invalidateQueries({ queryKey: ["demo-sessions"] });
  }

  // Shared streaming callbacks for both a fresh send and an approval resume.
  function streamHandlers(): ClarionChatHandlers {
    return {
      onDelta: (c) => setLive((l) => (l ? { ...l, text: l.text + c } : l)),
      onToolCall: (call) =>
        setLive((l) => (l ? { ...l, calls: [...l.calls, call] } : l)),
      onToolResult: (r) =>
        setLive((l) => (l ? { ...l, results: { ...l.results, [r.tool_use_id]: r } } : l)),
      // finish() refetches the conversation; a paused build surfaces as the
      // approval card derived from the persisted turns (see pendingBuild).
      onDone: ({ conversation_id }) => { void finish(conversation_id); },
      onError: (m) => { setErr(m); setBusy(false); setLive(null); },
    };
  }

  async function send(text?: string) {
    const msg = (text ?? input).trim();
    if (!msg || busy || pendingBuild) return;
    setErr(null);
    setInput("");
    setShowPicker(false);
    setPendingUser(msg);
    setLive({ text: "", calls: [], results: {} });
    setBusy(true);
    abortRef.current = await streamClarionChat(
      {
        message: msg,
        conversation_id: convId ?? undefined,
        context_scope: effectiveScope,
        auto_approve: autoApprove,
      },
      streamHandlers(),
    );
  }

  // Resolve a build paused awaiting approval — run it (approve) or decline
  // it (reject), then stream the continuation.
  async function resolveApproval(decision: "approve" | "reject") {
    if (convId == null || busy) return;
    setErr(null);
    setLive({ text: "", calls: [], results: {} });
    setBusy(true);
    abortRef.current = await resumeClarionChat(
      convId, { decision, auto_approve: autoApprove }, streamHandlers(),
    );
  }

  function stop() {
    abortRef.current?.abort();
    setBusy(false);
    // Keep what streamed so far; the server already persisted partial turns.
    if (convId != null) qc.invalidateQueries({ queryKey: ["assistant-conversation", convId] });
  }

  function newThread() {
    abortRef.current?.abort();
    setConvId(null);
    setLive(null);
    setPendingUser(null);
    setBusy(false);
    setErr(null);
    setInput("");
    setShowPicker(false);
    queueMicrotask(() => taRef.current?.focus());
  }

  async function openConversation(id: number) {
    abortRef.current?.abort();
    setBusy(false);
    setLive(null);
    setPendingUser(null);
    setConvId(id);
    setShowPicker(false);
  }

  async function archive(id: number, e: React.MouseEvent) {
    e.stopPropagation();
    await archiveConversation(id);
    qc.invalidateQueries({ queryKey: ["assistant-conversations"] });
    if (id === convId) newThread();
  }

  const hasMessages = turns.length > 0 || pendingUser != null || live != null;

  return (
    <>
      {/* Mobile-only backdrop. Desktop keeps the page interactive. */}
      <div
        aria-hidden="true"
        onClick={ctx.close}
        className={cn(
          "fixed inset-0 z-40 bg-black/40 backdrop-blur-sm transition-opacity sm:hidden",
          ctx.open ? "opacity-100" : "opacity-0 pointer-events-none",
        )}
      />
      <aside
        role="complementary"
        aria-label="Clarion Assistant"
        aria-hidden={!ctx.open}
        className={cn(
          "fixed top-0 right-0 z-50 h-full w-full sm:w-[440px] max-w-full flex flex-col",
          "bg-[var(--color-canvas-elev1)] border-l border-[var(--color-border)] shadow-2xl",
          "transition-transform duration-200 ease-out",
          ctx.open ? "translate-x-0" : "translate-x-full",
        )}
      >
        {/* Header */}
        <header className="flex items-center gap-2 px-4 h-14 border-b border-[var(--color-border)] shrink-0">
          <span className="inline-flex items-center justify-center w-7 h-7 rounded-lg bg-[var(--color-accent-bg)] text-[var(--color-accent)]">
            <Sparkles size={15} />
          </span>
          <div className="flex-1 min-w-0">
            <div className="text-sm font-semibold text-[var(--color-text)] leading-tight">
              Clarion Assistant
            </div>
            {sLabel && (
              <div className="text-[11px] text-[var(--color-text-muted)] font-mono truncate">
                context: {sLabel}
              </div>
            )}
          </div>
          <button
            type="button"
            onClick={newThread}
            title="New conversation"
            className="p-1.5 rounded-md text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:bg-white/[0.05]"
          >
            <Plus size={16} />
          </button>
          <button
            type="button"
            onClick={() => setShowPicker((v) => !v)}
            title="Conversation history"
            className={cn(
              "p-1.5 rounded-md hover:bg-white/[0.05]",
              showPicker ? "text-[var(--color-accent)]" : "text-[var(--color-text-muted)] hover:text-[var(--color-text)]",
            )}
          >
            <HistoryIcon size={16} />
          </button>
          <button
            type="button"
            onClick={ctx.close}
            title="Close (Esc)"
            className="p-1.5 rounded-md text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:bg-white/[0.05]"
          >
            <X size={16} />
          </button>
        </header>

        {/* Context switcher — re-aim the assistant at any plan/profile
            without leaving the page. Follows the current page until the
            SE picks something here. */}
        <div className="flex items-center gap-2 px-4 py-2 border-b border-[var(--color-border)] bg-[var(--color-canvas)]/40 shrink-0">
          <Target size={13} className="text-[var(--color-text-faint)] shrink-0" />
          <label htmlFor="clarion-scope" className="text-[11px] uppercase tracking-wider text-[var(--color-text-faint)] shrink-0">
            Context
          </label>
          <div className="relative flex-1 min-w-0">
            <select
              id="clarion-scope"
              value={scopeSelectValue}
              onChange={(e) => onScopeSelect(e.target.value)}
              className={cn(
                "w-full appearance-none bg-transparent text-xs text-[var(--color-text)] truncate",
                "rounded-md border border-[var(--color-border)] pl-2 pr-7 py-1.5",
                "hover:border-[var(--color-border-strong)] focus:border-[var(--color-accent-border)] outline-none",
              )}
            >
              <option value="">Global — no specific scope</option>
              {planOptions.length > 0 && (
                <optgroup label="Plans">
                  {planOptions.map((o) => (
                    <option key={o.id} value={`plan:${o.id}`}>Plan {o.label}</option>
                  ))}
                </optgroup>
              )}
              {profileOptions.length > 0 && (
                <optgroup label="Profiles">
                  {profileOptions.map((o) => (
                    <option key={o.id} value={`profile:${o.id}`}>{o.label}</option>
                  ))}
                </optgroup>
              )}
            </select>
            <ChevronDown
              size={13}
              className="pointer-events-none absolute right-2 top-1/2 -translate-y-1/2 text-[var(--color-text-faint)]"
            />
          </div>
          {scopeOverride && (
            <button
              type="button"
              onClick={() => setScopeOverride(null)}
              title="Follow the current page again"
              className="text-[10px] text-[var(--color-text-muted)] hover:text-[var(--color-text)] shrink-0"
            >
              reset
            </button>
          )}
        </div>

        {/* Conversation picker overlay */}
        {showPicker && (
          <div className="border-b border-[var(--color-border)] bg-[var(--color-canvas-elev2)] max-h-[50%] overflow-y-auto">
            <div className="px-4 py-2 text-[11px] uppercase tracking-wider text-[var(--color-text-faint)]">
              Conversations
            </div>
            {(convList.data ?? []).length === 0 ? (
              <div className="px-4 py-3 text-sm text-[var(--color-text-muted)]">
                No conversations yet.
              </div>
            ) : (
              (convList.data ?? []).map((c) => (
                <button
                  key={c.conversation_id}
                  type="button"
                  onClick={() => void openConversation(c.conversation_id)}
                  className={cn(
                    "group w-full text-left px-4 py-2 flex items-center gap-2 hover:bg-white/[0.04]",
                    c.conversation_id === convId && "bg-[var(--color-accent-bg)]",
                  )}
                >
                  <MessageSquare size={13} className="text-[var(--color-text-faint)] shrink-0" />
                  <span className="flex-1 min-w-0 truncate text-sm text-[var(--color-text)]">
                    {c.title ?? `Conversation ${c.conversation_id}`}
                  </span>
                  <span className="text-[11px] text-[var(--color-text-faint)] shrink-0">
                    {relTime(c.last_message_at ?? c.created_at)}
                  </span>
                  <span
                    role="button"
                    tabIndex={0}
                    onClick={(e) => void archive(c.conversation_id, e)}
                    title="Archive"
                    className="opacity-0 group-hover:opacity-100 p-1 rounded text-[var(--color-text-faint)] hover:text-[var(--color-danger)]"
                  >
                    <Trash2 size={13} />
                  </span>
                </button>
              ))
            )}
          </div>
        )}

        {/* Messages */}
        <div ref={scrollRef} className="flex-1 overflow-y-auto px-4 py-4 space-y-4">
          {!hasMessages ? (
            <EmptyState
              scopeLabel={sLabel}
              autoSend={autoApprove}
              onPick={(p) => {
                // Auto-run = "just go": fire the suggestion immediately.
                // Ask-before-builds = load it into the composer to edit first.
                if (autoApprove) {
                  void send(p);
                } else {
                  setInput(p);
                  queueMicrotask(() => taRef.current?.focus());
                }
              }}
            />
          ) : (
            <>
              {turns.map((t) => (
                <TurnView key={t.turn_id} turn={t} resultsByToolId={resultsByToolId} />
              ))}
              {pendingUser != null && <UserBubble text={pendingUser} />}
              {live != null && <LiveBubble live={live} />}
            </>
          )}
          {pendingBuild && (
            <ApprovalCard
              call={pendingBuild}
              onApprove={() => void resolveApproval("approve")}
              onReject={() => void resolveApproval("reject")}
            />
          )}
          {err && (
            <div className="flex items-start gap-2 text-xs text-[var(--color-danger)] bg-[var(--color-danger-bg)] rounded-md px-3 py-2">
              <AlertCircle size={13} className="mt-0.5 shrink-0" /> <span>{err}</span>
            </div>
          )}
        </div>

        {/* Composer */}
        <div className="border-t border-[var(--color-border)] p-3 shrink-0">
          <div className="relative rounded-lg border border-[var(--color-border)] bg-[var(--color-canvas)] focus-within:border-[var(--color-accent-border)] transition-colors">
            <textarea
              ref={taRef}
              value={input}
              onChange={(e) => setInput(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  void send();
                }
              }}
              rows={2}
              disabled={!!pendingBuild}
              placeholder={
                pendingBuild
                  ? "Approve or cancel the pending build above first…"
                  : "Ask Clarion to build, refine, or explain anything…"
              }
              className="w-full resize-none bg-transparent px-3 py-2.5 pr-12 text-sm text-[var(--color-text)] placeholder:text-[var(--color-text-faint)] outline-none disabled:opacity-50"
            />
            <div className="absolute right-2 bottom-2">
              {busy ? (
                <button
                  type="button"
                  onClick={stop}
                  title="Stop"
                  className="inline-flex items-center justify-center w-8 h-8 rounded-md bg-[var(--color-canvas-elev2)] text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
                >
                  <Square size={13} className="fill-current" />
                </button>
              ) : (
                <button
                  type="button"
                  onClick={() => void send()}
                  disabled={!input.trim() || !!pendingBuild}
                  title="Send (Enter)"
                  className="inline-flex items-center justify-center w-8 h-8 rounded-md bg-[var(--color-accent)] text-[var(--color-on-accent,#06231f)] disabled:opacity-40 disabled:cursor-not-allowed hover:opacity-90"
                >
                  <Send size={14} />
                </button>
              )}
            </div>
          </div>
          <div className="mt-1.5 flex items-center justify-between text-[10px] text-[var(--color-text-faint)] px-1">
            <span>Enter to send · Shift+Enter for newline</span>
            {/* Approval-mode toggle — gates whether the assistant pauses
                before kicking off a build. */}
            <button
              type="button"
              onClick={toggleAutoApprove}
              title={
                autoApprove
                  ? "Builds run automatically. Click to require approval first."
                  : "Builds pause for your approval. Click to let them run automatically."
              }
              className={cn(
                "inline-flex items-center gap-1 rounded px-1.5 py-0.5 transition-colors",
                autoApprove
                  ? "text-[var(--color-warning)] hover:bg-white/[0.05]"
                  : "text-[var(--color-accent)] hover:bg-white/[0.05]",
              )}
            >
              {autoApprove
                ? (<><Zap size={10} /> Auto-run builds</>)
                : (<><ShieldCheck size={10} /> Ask before builds</>)}
            </button>
          </div>
        </div>
      </aside>
    </>
  );
}

// ── Sub-views ──────────────────────────────────────────────────────

function EmptyState({
  scopeLabel, onPick, autoSend,
}: { scopeLabel: string | null; onPick: (p: string) => void; autoSend: boolean }) {
  return (
    <div className="h-full flex flex-col items-center justify-center text-center px-4 py-8">
      <span className="inline-flex items-center justify-center w-12 h-12 rounded-2xl bg-[var(--color-accent-bg)] text-[var(--color-accent)] mb-3">
        <Sparkles size={22} />
      </span>
      <h3 className="text-sm font-semibold text-[var(--color-text)] m-0">
        How can I help?
      </h3>
      <p className="text-xs text-[var(--color-text-muted)] mt-1 max-w-[300px]">
        I can build a demo from a URL, refine an existing plan, provision to
        Grafana Cloud, and start the live telemetry — just ask.
        {scopeLabel && (
          <> I'm scoped to <span className="font-mono text-[var(--color-text)]">{scopeLabel}</span>.</>
        )}
      </p>
      <div className="mt-4 w-full space-y-2">
        {EXAMPLE_PROMPTS.map((p) => (
          <button
            key={p}
            type="button"
            onClick={() => onPick(p)}
            title={autoSend
              ? "Auto-run is on — clicking sends this immediately"
              : "Click to load into the composer — edit, then send"}
            className="w-full text-left text-xs px-3 py-2 rounded-md border border-[var(--color-border)] bg-[var(--color-canvas-elev2)] text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:border-[var(--color-border-strong)] transition-colors"
          >
            {p}
          </button>
        ))}
      </div>
      <p className="mt-2 text-[10px] text-[var(--color-text-faint)]">
        {autoSend
          ? "Auto-run is on — clicking a suggestion sends it right away."
          : "Suggestions load into the composer so you can edit before sending."}
      </p>
    </div>
  );
}

/** Inline card shown when the assistant wants to kick off a build and
 *  approval mode is on. Approve runs it; Cancel declines and lets the
 *  assistant ask what to change. */
function ApprovalCard({
  call, onApprove, onReject,
}: { call: AssistantToolCall; onApprove: () => void; onReject: () => void }) {
  const i = call.input ?? {};
  const detail = [
    call.name,
    typeof i.url === "string" ? i.url : null,
    typeof i.phase === "string" ? i.phase : null,
    typeof i.plan_id === "string" ? `plan ${(i.plan_id as string).slice(0, 8)}` : null,
  ].filter(Boolean).join(" · ");
  return (
    <div className="rounded-xl border border-[color:var(--color-warning)]/40 bg-[var(--color-warning-bg)] p-3 space-y-2">
      <div className="flex items-center gap-2 text-xs font-medium text-[var(--color-text)]">
        <ShieldCheck size={14} className="text-[var(--color-warning)]" />
        Approval needed before this build runs
      </div>
      <p className="text-sm text-[var(--color-text)] m-0">{approvalMessage(call)}</p>
      <div className="text-[11px] font-mono text-[var(--color-text-muted)] truncate">{detail}</div>
      <div className="flex items-center gap-2 pt-1">
        <button
          type="button"
          onClick={onApprove}
          className="inline-flex items-center gap-1.5 h-8 px-3 rounded-md text-xs font-medium bg-[var(--color-accent)] text-[var(--color-on-accent,#06231f)] hover:opacity-90"
        >
          <Play size={13} /> Approve &amp; run
        </button>
        <button
          type="button"
          onClick={onReject}
          className="inline-flex items-center gap-1.5 h-8 px-3 rounded-md text-xs border border-[var(--color-border)] text-[var(--color-text-muted)] hover:text-[var(--color-text)] hover:border-[var(--color-border-strong)]"
        >
          <X size={13} /> Cancel
        </button>
      </div>
    </div>
  );
}

function UserBubble({ text }: { text: string }) {
  return (
    <div className="flex justify-end">
      <div className="max-w-[85%] rounded-2xl rounded-br-sm bg-[var(--color-accent-bg)] text-[var(--color-text)] px-3.5 py-2 text-sm whitespace-pre-wrap break-words">
        {text}
      </div>
    </div>
  );
}

function AssistantText({ text }: { text: string }) {
  if (!text) return null;
  return (
    <div className="text-sm text-[var(--color-text)] leading-relaxed">
      <LinkifiedText text={text} />
    </div>
  );
}

/** One persisted turn → bubble(s). Tool turns are folded into the
 *  preceding assistant's chips, so they render nothing on their own. */
function TurnView({
  turn, resultsByToolId,
}: { turn: AssistantTurn; resultsByToolId: Record<string, AssistantToolResult> }) {
  if (turn.role === "user") return <UserBubble text={turn.content} />;
  if (turn.role === "tool") return null;
  // assistant
  const calls = turn.tool_calls ?? [];
  if (!turn.content && calls.length === 0) return null;
  return (
    <div className="space-y-2">
      <AssistantText text={turn.content} />
      {calls.map((c) => (
        <ToolChip
          key={c.tool_use_id}
          call={c}
          result={resultsByToolId[c.tool_use_id]}
          // A persisted build tool with no result means the conversation
          // is paused awaiting approval — show that, not a spinner.
          awaitingApproval={!resultsByToolId[c.tool_use_id] && BUILD_TOOL_NAMES.has(c.name)}
        />
      ))}
    </div>
  );
}

/** The in-flight assistant turn (streaming text + live tool chips). */
function LiveBubble({ live }: { live: LiveTurn }) {
  const noOutput = !live.text && live.calls.length === 0;
  return (
    <div className="space-y-2">
      <AssistantText text={live.text} />
      {live.calls.map((c) => (
        <ToolChip key={c.tool_use_id} call={c} result={live.results[c.tool_use_id]} />
      ))}
      {noOutput && (
        <div className="inline-flex items-center gap-2 text-xs text-[var(--color-text-muted)]">
          <Loader2 size={13} className="animate-spin" /> Thinking…
        </div>
      )}
    </div>
  );
}

/** A tool invocation rendered as a compact chip with live status and,
 *  for build actions, a "View build →" link. */
function ToolChip({
  call, result, awaitingApproval = false,
}: { call: AssistantToolCall; result?: AssistantToolResult; awaitingApproval?: boolean }) {
  const detail = resultDetail(result);
  const pending = !result;
  const isError = result?.is_error ?? false;
  const hint = toolInputHint(call);
  const watch = detail?.watch_url;

  const Icon = call.mutating ? Hammer : call.name.startsWith("get_") || call.name.startsWith("list_") ? Database : Wrench;

  return (
    <div
      className={cn(
        "rounded-lg border px-3 py-2 text-xs",
        call.mutating
          ? "border-[color:var(--color-accent-border)] bg-[var(--color-accent-bg)]/40"
          : "border-[var(--color-border)] bg-[var(--color-canvas-elev2)]",
      )}
    >
      <div className="flex items-center gap-2">
        <Icon size={13} className={cn("shrink-0", call.mutating ? "text-[var(--color-accent)]" : "text-[var(--color-text-muted)]")} />
        <span className="font-medium text-[var(--color-text)]">{toolLabel(call.name)}</span>
        {call.mutating && (
          <span className="text-[9px] uppercase tracking-wide px-1 py-0.5 rounded bg-[var(--color-accent)]/15 text-[var(--color-accent)]">
            action
          </span>
        )}
        <span className="ml-auto shrink-0">
          {awaitingApproval ? (
            <span className="inline-flex items-center gap-1 text-[10px] text-[var(--color-warning)]">
              <ShieldCheck size={11} /> awaiting approval
            </span>
          ) : pending ? (
            <Loader2 size={12} className="animate-spin text-[var(--color-text-faint)]" />
          ) : isError ? (
            <AlertCircle size={12} className="text-[var(--color-danger)]" />
          ) : (
            <Check size={12} className="text-[var(--color-success)]" />
          )}
        </span>
      </div>
      {hint && (
        <div className="mt-1 text-[var(--color-text-muted)] truncate font-mono">{hint}</div>
      )}
      {detail?.message && !isError && (
        <div className="mt-1 text-[var(--color-text-muted)]">{detail.message}</div>
      )}
      {isError && result?.summary && (
        <div className="mt-1 text-[var(--color-danger)]">{result.summary}</div>
      )}
      {watch && (
        <Link
          to={watch}
          className="mt-1.5 inline-flex items-center gap-1 text-[var(--color-accent)] font-medium hover:opacity-80"
        >
          View build <ArrowUpRight size={12} />
        </Link>
      )}
    </div>
  );
}
