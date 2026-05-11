import { useEffect, useMemo, useState } from "react";
import { AlertCircle, Save, RotateCcw, Loader2, CheckCircle2 } from "lucide-react";
import { Card } from "@/components/Card";
import { Button } from "@/components/Button";
import { cn } from "@/lib/cn";

/**
 * Plain textarea-based JSON editor. Parse-validates on every keystroke
 * (so the Save button only enables on valid JSON), but the server-side
 * also validates against the Pydantic schema and rejects malformed
 * structures with a 400.
 *
 * Why textarea + line-counter and not Monaco: keeps the UI bundle
 * small and avoids dependency drift. If we end up needing semantic
 * highlighting / autocomplete, Monaco's an easy swap-in for this
 * component without touching call sites.
 */
export function JsonEditor({
  value, onSave, busy, error,
}: {
  /** Initial value. We deep-clone via JSON.stringify to seed the
   *  editor; the parent owns the source-of-truth and re-renders this
   *  with a new `value` after Save lands. */
  value: unknown;
  onSave: (parsed: unknown) => Promise<void>;
  busy?: boolean;
  /** Server-side error from the last save attempt, e.g. schema validation. */
  error?: string | null;
}) {
  const initial = useMemo(() => JSON.stringify(value, null, 2), [value]);
  const [text, setText] = useState(initial);
  const [savedOnce, setSavedOnce] = useState(false);

  // Re-seed the textarea when `value` changes (e.g. after a successful
  // save the parent passes us back the canonical, server-validated JSON).
  useEffect(() => { setText(initial); }, [initial]);

  // Try to parse on every change so we know if Save is allowed.
  const { parsed, parseError } = useMemo(() => {
    try {
      return { parsed: JSON.parse(text), parseError: null as string | null };
    } catch (e) {
      return { parsed: null, parseError: (e as Error).message };
    }
  }, [text]);

  const dirty = text !== initial;

  async function save() {
    if (parsed === null || !dirty || busy) return;
    await onSave(parsed);
    setSavedOnce(true);
    // Auto-hide the "saved" pip after a moment
    setTimeout(() => setSavedOnce(false), 2_000);
  }

  function reset() {
    setText(initial);
  }

  return (
    <Card className="flex flex-col h-[680px]">
      <div className="px-4 py-3 border-b border-[var(--color-border)] flex items-center justify-between gap-3">
        <div>
          <div className="text-sm font-medium">Edit plan JSON</div>
          <div className="text-xs text-[var(--color-text-faint)] mt-0.5">
            Validated against the DemoPlan schema on save. The plan_id
            and source_profile_id are pinned by the server; everything
            else is editable.
          </div>
        </div>
        <div className="flex items-center gap-2">
          {savedOnce && (
            <span className="flex items-center gap-1 text-xs text-[var(--color-success)]">
              <CheckCircle2 size={12} /> saved
            </span>
          )}
          <Button size="sm" variant="ghost" disabled={!dirty || busy} onClick={reset}>
            <RotateCcw size={12} /> Reset
          </Button>
          <Button
            size="sm"
            variant="primary"
            disabled={!dirty || !!parseError || busy}
            onClick={() => void save()}
          >
            {busy ? <Loader2 size={12} className="animate-spin" /> : <Save size={12} />}
            Save
          </Button>
        </div>
      </div>

      {(parseError || error) && (
        <div className="px-4 py-2 text-xs flex items-center gap-2 border-b border-[var(--color-border)] bg-[var(--color-danger)]/10 text-[var(--color-danger)]">
          <AlertCircle size={12} className="shrink-0" />
          <span className="truncate">{error || `parse: ${parseError}`}</span>
        </div>
      )}

      <textarea
        spellCheck={false}
        value={text}
        onChange={(e) => setText(e.target.value)}
        className={cn(
          "flex-1 resize-none bg-black/40 p-4 font-mono text-xs leading-relaxed",
          "outline-none whitespace-pre",
          "text-[var(--color-text)]",
        )}
        style={{ tabSize: 2 }}
      />
    </Card>
  );
}
