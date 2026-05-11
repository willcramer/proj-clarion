/**
 * Setup page — the first-run wizard.
 *
 * Three input surfaces on one page:
 *
 *  1. **Form** — one card per group (Anthropic / Grafana Cloud /
 *     Sigil / PDC). Each field has label, help link, masked/visible
 *     toggle for secrets, per-field "Test" button that hits
 *     /api/setup/validate.
 *
 *  2. **Paste / upload** — sidebar with a drag-drop zone (accepts .env,
 *     .json, .txt) and a textarea for free-form paste. Calls
 *     /api/setup/parse and bulk-fills the form fields. Useful for users
 *     who already have a .env from another machine.
 *
 *  3. **Save & launch** — bottom-anchored button. Disabled until every
 *     required field is set. On click, posts the full map to /api/setup/save,
 *     waits for `ready: true`, then triggers a status refetch on the
 *     parent SetupGate which dismounts this page and renders the app.
 *
 * All field values live in React state; nothing persists to localStorage
 * (we don't want secrets sitting in browser storage). The user has to
 * hit Save to write them to disk.
 */
import {
  CheckCircle2, AlertCircle, Eye, EyeOff, ExternalLink, Loader2,
  Upload, KeyRound, Settings2, ChevronDown, ChevronRight,
} from "lucide-react";
import { useEffect, useMemo, useRef, useState, type ChangeEvent, type DragEvent } from "react";

import { Logo } from "@/components/Logo";
import {
  setupApi,
  type Group, type SetupKeyMeta, type SetupSchema, type SetupStatus,
  type ValidateResult,
} from "@/lib/setup-api";
import { cn } from "@/lib/cn";

const GROUP_TITLES: Record<Group, string> = {
  anthropic:     "Anthropic",
  grafana_cloud: "Grafana Cloud",
  sigil:         "Sigil — AI observability",
  pdc:           "PDC — private datasource",
  advanced:      "Advanced",
};

const GROUP_SUBTITLES: Record<Group, string> = {
  anthropic:     "Required — Clarion uses Claude for research + planning.",
  grafana_cloud: "Required — your Grafana Cloud stack hosts dashboards, alerts, and KG.",
  sigil:         "Optional — enables trace + token visibility on the LLM calls.",
  pdc:           "Optional — only needed for private-datasource Postgres.",
  advanced:      "Less commonly changed.",
};

// Initial UI state for the advanced/optional groups: collapsed by default
// to keep the primary flow short. Required groups always open.
const DEFAULT_COLLAPSED: Group[] = ["sigil", "pdc", "advanced"];

export function SetupPage({
  status, onComplete,
}: {
  status: SetupStatus;
  onComplete: () => void;
}) {
  const [schema, setSchema] = useState<SetupSchema | null>(null);
  const [values, setValues] = useState<Record<string, string>>({});
  const [validation, setValidation] = useState<Record<string, ValidateResult>>({});
  const [validating, setValidating] = useState<Record<string, boolean>>({});
  const [reveal, setReveal] = useState<Record<string, boolean>>({});
  const [collapsed, setCollapsed] = useState<Set<Group>>(new Set(DEFAULT_COLLAPSED));
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [parsedHint, setParsedHint] = useState<string | null>(null);
  const [pasteText, setPasteText] = useState("");

  // Load the schema once on mount. The form structure is data-driven so
  // adding a new env key on the backend automatically surfaces it here.
  useEffect(() => {
    setupApi.schema()
      .then(setSchema)
      .catch((e) => setSaveError(`Couldn't load setup schema: ${e.message}`));
  }, []);

  // Field-level helpers
  function setValue(key: string, v: string) {
    setValues((prev) => ({ ...prev, [key]: v }));
    // Clear any prior validation result when the user edits — they
    // need to re-Test before we trust the green check again.
    setValidation((prev) => {
      const next = { ...prev };
      delete next[key];
      return next;
    });
  }

  async function testField(meta: SetupKeyMeta) {
    const value = (values[meta.key] || "").trim();
    if (!value) {
      setValidation((prev) => ({
        ...prev,
        [meta.key]: { valid: false, reason: "No value to test.", hint: "" },
      }));
      return;
    }
    setValidating((prev) => ({ ...prev, [meta.key]: true }));
    try {
      const r = await setupApi.validate(meta.key, value, values);
      setValidation((prev) => ({ ...prev, [meta.key]: r }));
    } catch (e) {
      setValidation((prev) => ({
        ...prev,
        [meta.key]: { valid: false, reason: e instanceof Error ? e.message : "Validation failed", hint: "" },
      }));
    } finally {
      setValidating((prev) => ({ ...prev, [meta.key]: false }));
    }
  }

  // Required-completeness check. Every required field needs a non-empty
  // value AND, if it has a validator, a passing validation. (Format
  // checks are lenient — a validated format-only field counts as OK.)
  const requiredReady = useMemo(() => {
    if (!schema) return false;
    for (const k of schema.keys) {
      if (!k.required) continue;
      const v = (values[k.key] || "").trim();
      if (!v) return false;
      // If we have a live validator and the user never tested, we
      // still allow save — the backend re-validates required keys on
      // its own. This makes the form less annoying when the user knows
      // their keys are right.
    }
    return true;
  }, [schema, values]);

  async function save() {
    setSaving(true);
    setSaveError(null);
    try {
      // Strip empty values so we don't persist a bunch of `KEY=` lines
      // for optional fields the user left blank.
      const trimmed: Record<string, string> = {};
      for (const [k, v] of Object.entries(values)) {
        if (v.trim()) trimmed[k] = v.trim();
      }
      const result = await setupApi.save(trimmed, true);
      if (result.ready) {
        // Tell the parent gate to re-check; it'll see ready=true and
        // dismount this page in favour of the app.
        onComplete();
      } else {
        setSaveError(
          "Saved, but some required keys are still missing. Re-check and try again.",
        );
      }
    } catch (e) {
      // `setup-api.json` attaches a `failures` array on the error when
      // the backend rejected individual fields. Show them inline.
      const err = e as Error & { failures?: { key: string; reason: string }[] };
      if (err.failures) {
        const next: Record<string, ValidateResult> = {};
        for (const f of err.failures) {
          next[f.key] = { valid: false, reason: f.reason, hint: "" };
        }
        setValidation((prev) => ({ ...prev, ...next }));
        setSaveError(err.message);
      } else {
        setSaveError(e instanceof Error ? e.message : String(e));
      }
    } finally {
      setSaving(false);
    }
  }

  async function handleParse(text: string) {
    setParsedHint(null);
    if (!text.trim()) return;
    try {
      const r = await setupApi.parse(text);
      const knownCount = r.known.length;
      const unknownCount = r.unknown.length;
      if (knownCount === 0 && unknownCount === 0) {
        setParsedHint("Couldn't parse any KEY=VALUE pairs from that input.");
        return;
      }
      // Auto-fill only known keys. Custom/unknown keys we surface but
      // don't blindly set — let the user decide whether to add them.
      setValues((prev) => {
        const next = { ...prev };
        for (const k of r.known) next[k] = r.parsed[k];
        return next;
      });
      // Clear prior validations on the just-filled fields
      setValidation((prev) => {
        const next = { ...prev };
        for (const k of r.known) delete next[k];
        return next;
      });
      // Always render both halves of the count — historically we hid the
      // "0 fields filled" line when knownCount was 0, which masked the
      // bug case where a user uploaded a file that had ONLY non-schema
      // keys (e.g. a post-signout `.env`). Both numbers go in the hint so
      // "15 ignored, 0 filled" makes the problem visible.
      const fieldsLabel = `${knownCount} field${knownCount === 1 ? "" : "s"} filled`;
      const unknownLabel = `${unknownCount} unknown key${unknownCount === 1 ? "" : "s"} ignored`;
      setParsedHint(`${fieldsLabel} · ${unknownLabel}`);
    } catch (e) {
      setParsedHint(`Parse failed: ${e instanceof Error ? e.message : String(e)}`);
    }
  }

  if (!schema) {
    return (
      <SetupShell>
        <div className="text-sm text-[var(--color-text-muted)]">Loading setup schema…</div>
      </SetupShell>
    );
  }

  // Group keys for rendering. Iteration order matches the SETUP_KEYS
  // list on the backend (which is curated for "important first").
  const byGroup = new Map<Group, SetupKeyMeta[]>();
  for (const k of schema.keys) {
    const arr = byGroup.get(k.group) ?? [];
    arr.push(k);
    byGroup.set(k.group, arr);
  }

  return (
    <SetupShell>
      <div className="grid grid-cols-1 lg:grid-cols-[1fr_360px] gap-6">
        {/* Left column: per-group forms */}
        <div className="space-y-4">
          {Array.from(byGroup.entries()).map(([group, keys]) => {
            const groupKeyNames = keys.map((k) => k.key);
            const present = groupKeyNames.filter((k) => (values[k] || status.present.includes(k))).length;
            const required = keys.filter((k) => k.required).length;
            const missing = keys.filter(
              (k) => k.required && !(values[k.key] || "").trim() && !status.present.includes(k.key),
            ).length;
            const isCollapsed = collapsed.has(group);

            return (
              <div
                key={group}
                className="rounded-xl border border-[var(--color-border)] bg-[var(--color-canvas-elev1)] overflow-hidden"
              >
                <button
                  type="button"
                  onClick={() => {
                    setCollapsed((cur) => {
                      const next = new Set(cur);
                      if (next.has(group)) next.delete(group);
                      else next.add(group);
                      return next;
                    });
                  }}
                  className="w-full text-left px-4 py-3 flex items-center gap-3 hover:bg-white/[0.03]"
                  aria-expanded={!isCollapsed}
                >
                  {isCollapsed ? <ChevronRight size={14} className="text-[var(--color-text-faint)]" /> : <ChevronDown size={14} className="text-[var(--color-text-faint)]" />}
                  <div className="flex-1 min-w-0">
                    <div className="text-sm font-medium text-[var(--color-text)]">{GROUP_TITLES[group]}</div>
                    <div className="text-xs text-[var(--color-text-muted)] mt-0.5">{GROUP_SUBTITLES[group]}</div>
                  </div>
                  <div className="flex items-center gap-2 text-xs">
                    {required > 0 && (
                      <span
                        className={cn(
                          "px-1.5 py-0.5 rounded font-mono",
                          missing > 0
                            ? "bg-[var(--color-danger-bg)] text-[var(--color-danger)]"
                            : "bg-[var(--color-success-bg)] text-[var(--color-success)]",
                        )}
                      >
                        {missing > 0 ? `${missing} missing` : "ready"}
                      </span>
                    )}
                    <span className="text-[var(--color-text-faint)] tabular-nums">{present}/{keys.length}</span>
                  </div>
                </button>
                {!isCollapsed && (
                  <div className="px-4 pb-4 space-y-3 border-t border-[var(--color-border)] pt-3">
                    {keys.map((k) => (
                      <FieldRow
                        key={k.key}
                        meta={k}
                        value={values[k.key] ?? ""}
                        reveal={!!reveal[k.key]}
                        validation={validation[k.key]}
                        validating={!!validating[k.key]}
                        alreadyPresent={status.present.includes(k.key)}
                        onChange={(v) => setValue(k.key, v)}
                        onReveal={() => setReveal((p) => ({ ...p, [k.key]: !p[k.key] }))}
                        onTest={() => testField(k)}
                      />
                    ))}
                  </div>
                )}
              </div>
            );
          })}
        </div>

        {/* Right column: paste / upload */}
        <aside className="space-y-4">
          <PasteUpload
            text={pasteText}
            onTextChange={setPasteText}
            onParse={handleParse}
            parsedHint={parsedHint}
          />
          <SaveBox
            ready={requiredReady}
            saving={saving}
            error={saveError}
            onSave={save}
          />
        </aside>
      </div>
    </SetupShell>
  );
}

// ──────────────────────────────────────────────────────────────────
// Page shell — neutral background, centered max width, brand mark.
// Reused by SetupGate's loading/error states for layout consistency.
// ──────────────────────────────────────────────────────────────────

function SetupShell({ children }: { children: React.ReactNode }) {
  return (
    <div className="min-h-screen bg-[var(--color-canvas)] py-8 px-4">
      <div className="max-w-6xl mx-auto space-y-6">
        <header className="flex items-center gap-3">
          <Logo size={28} />
          <div>
            <h1 className="text-xl font-medium text-[var(--color-text)]">Clarion setup</h1>
            <p className="text-sm text-[var(--color-text-muted)]">
              Add your Anthropic + Grafana Cloud credentials to launch the app.
              They're stored only in <code className="font-mono">.env</code> on this machine.
            </p>
          </div>
        </header>
        {children}
      </div>
    </div>
  );
}

// ──────────────────────────────────────────────────────────────────
// FieldRow — one row per env key, with masked input, test button,
// validation badge.
// ──────────────────────────────────────────────────────────────────

function FieldRow({
  meta, value, reveal, validation, validating, alreadyPresent,
  onChange, onReveal, onTest,
}: {
  meta: SetupKeyMeta;
  value: string;
  reveal: boolean;
  validation: ValidateResult | undefined;
  validating: boolean;
  alreadyPresent: boolean;
  onChange: (v: string) => void;
  onReveal: () => void;
  onTest: () => void;
}) {
  const showTestButton = meta.validator !== null;
  return (
    <div>
      <div className="flex items-baseline justify-between gap-2 mb-1">
        <label htmlFor={`f-${meta.key}`} className="text-xs font-medium text-[var(--color-text)]">
          {meta.label}
          {meta.required && <span className="text-[var(--color-danger)] ml-0.5">*</span>}
          {alreadyPresent && !value && (
            <span className="ml-2 text-[10px] font-normal text-[var(--color-text-faint)] uppercase tracking-wider">
              already set
            </span>
          )}
        </label>
        {meta.help_url && (
          <a
            href={meta.help_url}
            target="_blank"
            rel="noopener noreferrer"
            className="text-[10px] text-[var(--color-text-faint)] hover:text-[var(--color-accent)] inline-flex items-center gap-0.5"
          >
            where to find <ExternalLink size={10} aria-hidden="true" />
          </a>
        )}
      </div>
      <div className="flex items-stretch gap-2">
        <div className="flex-1 relative">
          <input
            id={`f-${meta.key}`}
            type={meta.secret && !reveal ? "password" : "text"}
            value={value}
            onChange={(e) => onChange(e.target.value)}
            placeholder={alreadyPresent && !value ? "•••••• (leave blank to keep existing)" : meta.placeholder}
            autoComplete="off"
            spellCheck={false}
            className={cn(
              "w-full bg-[var(--color-canvas)] border rounded-md px-2.5 py-1.5 text-sm font-mono",
              "outline-none transition-colors",
              validation?.valid === false
                ? "border-[var(--color-danger)]"
                : validation?.valid === true
                ? "border-[var(--color-success)]"
                : "border-[var(--color-border)] focus:border-[var(--color-border-strong)]",
              meta.secret && "pr-8",
            )}
          />
          {meta.secret && (
            <button
              type="button"
              onClick={onReveal}
              aria-label={reveal ? "Hide value" : "Show value"}
              className="absolute right-1.5 top-1/2 -translate-y-1/2 p-1 text-[var(--color-text-faint)] hover:text-[var(--color-text)]"
            >
              {reveal ? <EyeOff size={12} /> : <Eye size={12} />}
            </button>
          )}
        </div>
        {showTestButton && (
          <button
            type="button"
            onClick={onTest}
            disabled={!value || validating}
            className={cn(
              "px-3 py-1.5 rounded-md text-xs font-medium border whitespace-nowrap",
              "border-[var(--color-border)] text-[var(--color-text-muted)]",
              "hover:border-[var(--color-border-strong)] hover:text-[var(--color-text)]",
              "disabled:opacity-50 disabled:cursor-not-allowed",
            )}
          >
            {validating ? <Loader2 size={12} className="animate-spin inline" /> : "Test"}
          </button>
        )}
      </div>
      <div className="mt-1 min-h-[16px] flex items-center gap-1.5">
        {validation ? (
          validation.valid ? (
            <span className="text-[11px] text-[var(--color-success)] inline-flex items-center gap-1">
              <CheckCircle2 size={10} /> Valid
            </span>
          ) : (
            <span className="text-[11px] text-[var(--color-danger)] inline-flex items-center gap-1">
              <AlertCircle size={10} />
              {validation.reason}{validation.hint ? ` — ${validation.hint}` : ""}
            </span>
          )
        ) : (
          <span className="text-[11px] text-[var(--color-text-faint)]">{meta.description}</span>
        )}
      </div>
    </div>
  );
}

// ──────────────────────────────────────────────────────────────────
// Paste / upload sidebar — drag-drop + textarea, parse via API
// ──────────────────────────────────────────────────────────────────

function PasteUpload({
  text, onTextChange, onParse, parsedHint,
}: {
  text: string;
  onTextChange: (s: string) => void;
  onParse: (s: string) => void;
  parsedHint: string | null;
}) {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const [drag, setDrag] = useState(false);

  function handleFile(file: File) {
    const reader = new FileReader();
    reader.onload = () => {
      const text = String(reader.result || "");
      onTextChange(text);
      onParse(text);
    };
    reader.readAsText(file);
  }

  function onDrop(e: DragEvent<HTMLDivElement>) {
    e.preventDefault();
    setDrag(false);
    const file = e.dataTransfer.files?.[0];
    if (file) handleFile(file);
  }

  function onPick(e: ChangeEvent<HTMLInputElement>) {
    const file = e.target.files?.[0];
    if (file) handleFile(file);
  }

  return (
    <div className="rounded-xl border border-[var(--color-border)] bg-[var(--color-canvas-elev1)] p-4 space-y-3">
      <div className="flex items-center gap-2">
        <Upload size={14} className="text-[var(--color-text-faint)]" />
        <h2 className="text-sm font-medium text-[var(--color-text)]">Bulk import</h2>
      </div>
      <p className="text-xs text-[var(--color-text-muted)]">
        Already have an <code>.env</code> from another machine? Drop it here or paste below
        and we'll fill in the matching fields.
      </p>

      <div
        onDragOver={(e) => { e.preventDefault(); setDrag(true); }}
        onDragLeave={() => setDrag(false)}
        onDrop={onDrop}
        className={cn(
          "rounded-md border-2 border-dashed p-3 text-center transition-colors",
          drag
            ? "border-[var(--color-accent)] bg-[var(--color-accent-bg)]"
            : "border-[var(--color-border)] hover:border-[var(--color-border-strong)]",
        )}
      >
        <input
          ref={fileInputRef}
          type="file"
          accept=".env,.txt,.json,text/plain,application/json"
          onChange={onPick}
          className="hidden"
        />
        <button
          type="button"
          onClick={() => fileInputRef.current?.click()}
          className="text-xs text-[var(--color-text-muted)] hover:text-[var(--color-text)]"
        >
          Drop a file or <span className="text-[var(--color-accent)] underline">click to choose</span>
        </button>
        <div className="text-[10px] text-[var(--color-text-faint)] mt-1">
          .env · shell exports · JSON
        </div>
      </div>

      <div className="relative">
        <textarea
          value={text}
          onChange={(e) => onTextChange(e.target.value)}
          placeholder={`Or paste KEY=VALUE pairs:\n\nANTHROPIC_API_KEY=sk-ant-...\nGRAFANA_CLOUD_STACK_URL=https://...`}
          rows={6}
          spellCheck={false}
          className="w-full bg-[var(--color-canvas)] border border-[var(--color-border)] rounded-md p-2 text-xs font-mono outline-none focus:border-[var(--color-border-strong)] resize-none"
        />
        <div className="flex items-center justify-between mt-1">
          <span className="text-[10px] text-[var(--color-text-faint)]">{parsedHint ?? ""}</span>
          <button
            type="button"
            onClick={() => onParse(text)}
            disabled={!text.trim()}
            className={cn(
              "text-xs px-2.5 py-1 rounded-md border",
              "border-[var(--color-border)] text-[var(--color-text-muted)]",
              "hover:border-[var(--color-border-strong)] hover:text-[var(--color-text)]",
              "disabled:opacity-50 disabled:cursor-not-allowed",
            )}
          >
            Parse
          </button>
        </div>
      </div>
    </div>
  );
}

// ──────────────────────────────────────────────────────────────────
// Save box — ready-state + button + error
// ──────────────────────────────────────────────────────────────────

function SaveBox({
  ready, saving, error, onSave,
}: {
  ready: boolean;
  saving: boolean;
  error: string | null;
  onSave: () => void;
}) {
  return (
    <div className="rounded-xl border border-[var(--color-border)] bg-[var(--color-canvas-elev1)] p-4 space-y-3">
      <div className="flex items-center gap-2">
        <KeyRound size={14} className="text-[var(--color-text-faint)]" />
        <h2 className="text-sm font-medium text-[var(--color-text)]">Save & launch</h2>
      </div>
      <p className="text-xs text-[var(--color-text-muted)]">
        Writes your tokens to <code>.env</code> on this machine. We back up the previous file to
        <code className="ml-1">.env.bak</code> so you can roll back.
      </p>
      <button
        type="button"
        onClick={onSave}
        disabled={!ready || saving}
        className={cn(
          "w-full px-3 py-2 rounded-md text-sm font-medium transition-colors",
          ready && !saving
            ? "bg-[var(--color-accent)] text-[var(--color-on-accent)] hover:bg-[var(--color-accent-hover)]"
            : "bg-[var(--color-canvas-elev2)] text-[var(--color-text-faint)] cursor-not-allowed",
        )}
      >
        {saving ? (
          <span className="inline-flex items-center gap-2">
            <Loader2 size={14} className="animate-spin" /> Saving…
          </span>
        ) : ready ? (
          "Save & launch Clarion"
        ) : (
          "Fill required fields to continue"
        )}
      </button>
      {error && (
        <div className="text-xs text-[var(--color-danger)] flex items-start gap-1.5">
          <AlertCircle size={12} className="shrink-0 mt-0.5" />
          <span>{error}</span>
        </div>
      )}
      <details className="text-xs text-[var(--color-text-faint)]">
        <summary className="cursor-pointer hover:text-[var(--color-text-muted)] inline-flex items-center gap-1">
          <Settings2 size={11} /> Advanced
        </summary>
        <div className="mt-2 space-y-1">
          <div>
            You can also edit <code>.env</code> directly, then refresh this page.
          </div>
          <div>
            Required fields: marked with <span className="text-[var(--color-danger)]">*</span>.
          </div>
        </div>
      </details>
    </div>
  );
}
