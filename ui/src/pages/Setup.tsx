/**
 * Setup page, the first-run wizard.
 *
 * Three input surfaces on one page:
 *
 *  1. **Form**, one card per group (Anthropic / Grafana Cloud /
 *     Sigil / PDC). Each field has label, help link, masked/visible
 *     toggle for secrets, per-field "Test" button that hits
 *     /api/setup/validate.
 *
 *  2. **Paste / upload**, sidebar with a drag-drop zone (accepts .env,
 *     .json, .txt) and a textarea for free-form paste. Calls
 *     /api/setup/parse and bulk-fills the form fields. Useful for users
 *     who already have a .env from another machine.
 *
 *  3. **Save & launch**, bottom-anchored button. Disabled until every
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
  Upload, KeyRound, Check, Lock,
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
  sigil:         "Sigil, AI observability",
  pdc:           "PDC, private datasource",
  advanced:      "Advanced",
};

const GROUP_SUBTITLES: Record<Group, string> = {
  anthropic:     "Required, Clarion uses Claude for research + planning.",
  grafana_cloud: "Required, your Grafana Cloud stack hosts dashboards, alerts, and KG.",
  sigil:         "Optional, enables trace + token visibility on the LLM calls.",
  pdc:           "Optional, only needed for private-datasource Postgres.",
  advanced:      "Less commonly changed.",
};

// Step → groups mapping. Four-step wizard: required first, optional
// observability + datasource extras in middle, dedicated Review step
// at the end so Save & launch isn't competing with form fields.
//
// Why these groupings:
//   - PDC sits with Grafana Cloud because PDC is a Cloud-side concept
//     (private datasource bridge into your stack). They're co-configured
//     in practice.
//   - Sigil gets its own step labelled "AI Observability" so the SE has
//     a clear "set this up for trace/token visibility on the LLM calls"
//     moment, separate from the Grafana plumbing.
//   - Advanced lives on Review so power users can poke at it last
//     without crowding the primary path.
type WizardStep = 1 | 2 | 3 | 4;
const STEP_GROUPS: Record<WizardStep, Group[]> = {
  1: ["anthropic"],
  2: ["grafana_cloud", "pdc"],
  3: ["sigil"],
  4: ["advanced"],  // Review surface; Advanced is the only optional last-mile group.
};
const STEP_TITLES: Record<WizardStep, string> = {
  1: "Anthropic",
  2: "Grafana Cloud",
  3: "AI Observability",
  4: "Review & launch",
};
const STEP_LEADS: Record<WizardStep, string> = {
  1: "Clarion uses Claude for the research + planner LLM calls. Drop in your Anthropic API key.",
  2: "Where does your demo live? Add the Grafana Cloud stack URL, a service-account token, and PDC if you're bridging into a private datasource.",
  3: "Optional, enables trace + token visibility on the LLM calls via Sigil. Skip if you don't need observability on the agent itself.",
  4: "Everything's in place. Tweak Advanced overrides if you need to, then save to `.env`.",
};

/** Lowest step that still has at least one required key unset. */
function firstIncompleteStep(
  schema: SetupSchema,
  values: Record<string, string>,
  status: SetupStatus,
): WizardStep {
  function complete(step: WizardStep): boolean {
    for (const g of STEP_GROUPS[step]) {
      const required = schema.keys.filter((k) => k.group === g && k.required);
      if (required.some(
        (k) => !((values[k.key] || "").trim() || status.present.includes(k.key)),
      )) return false;
    }
    return true;
  }
  if (!complete(1)) return 1;
  if (!complete(2)) return 2;
  if (!complete(3)) return 3;
  return 4;
}

export function SetupPage({
  status, onComplete, mode = "first-run",
}: {
  status: SetupStatus;
  onComplete: () => void;
  /**
   * "first-run", gated by SetupGate before the app loads. Shows the
   *                full centered hero ("Let's get you set up.") so the
   *                page reads as a welcoming wizard.
   * "settings", reached via UserMenu → Settings while signed in.
   *                Drops the hero and runs inside the regular Layout
   *                chrome so it looks like any other in-app page.
   */
  mode?: "first-run" | "settings";
}) {
  const [schema, setSchema] = useState<SetupSchema | null>(null);
  const [values, setValues] = useState<Record<string, string>>({});
  const [validation, setValidation] = useState<Record<string, ValidateResult>>({});
  const [validating, setValidating] = useState<Record<string, boolean>>({});
  const [reveal, setReveal] = useState<Record<string, boolean>>({});
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [parsedHint, setParsedHint] = useState<string | null>(null);
  const [pasteText, setPasteText] = useState("");
  // `currentStep` is the active step the body renders. Set lazily once
  // the schema lands (we need it to compute "first incomplete"). Users
  // can navigate freely via the rail; Continue advances to the next
  // step but only when the current step's required keys are filled.
  const [currentStep, setCurrentStep] = useState<WizardStep | null>(null);

  // Load the schema once on mount. The form structure is data-driven so
  // adding a new env key on the backend automatically surfaces it here.
  useEffect(() => {
    setupApi.schema()
      .then(setSchema)
      .catch((e) => setSaveError(`Couldn't load setup schema: ${e.message}`));
  }, []);

  // Once schema lands, pick the first incomplete step as the starting
  // point. Done only once; after that the user drives navigation via
  // Back/Continue and rail clicks.
  useEffect(() => {
    if (schema && currentStep === null) {
      setCurrentStep(firstIncompleteStep(schema, values, status));
    }
  }, [schema, currentStep, values, status]);

  // Field-level helpers
  function setValue(key: string, v: string) {
    setValues((prev) => ({ ...prev, [key]: v }));
    // Clear any prior validation result when the user edits, they
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

  // Required-completeness check. A required key is "ready" if EITHER
  // the user has typed a value OR the key is already on disk (status.present).
  // In settings mode the user typically hasn't typed anything, they're
  // tweaking optional fields. Without checking status.present we'd
  // mis-report "fill required fields" on a fully-configured stack.
  const requiredReady = useMemo(() => {
    if (!schema) return false;
    for (const k of schema.keys) {
      if (!k.required) continue;
      const typed = (values[k.key] || "").trim();
      const onDisk = status.present.includes(k.key);
      if (!typed && !onDisk) return false;
    }
    return true;
  }, [schema, values, status]);

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
      // don't blindly set, let the user decide whether to add them.
      const nextValues = (() => {
        const next: Record<string, string> = { ...values };
        for (const k of r.known) next[k] = r.parsed[k];
        return next;
      })();
      setValues(nextValues);
      // Clear prior validations on the just-filled fields
      setValidation((prev) => {
        const next = { ...prev };
        for (const k of r.known) delete next[k];
        return next;
      });
      // Always render both halves of the count, historically we hid the
      // "0 fields filled" line when knownCount was 0, which masked the
      // bug case where a user uploaded a file that had ONLY non-schema
      // keys (e.g. a post-signout `.env`). Both numbers go in the hint so
      // "15 ignored, 0 filled" makes the problem visible.
      const fieldsLabel = `${knownCount} field${knownCount === 1 ? "" : "s"} filled`;
      const unknownLabel = `${unknownCount} unknown key${unknownCount === 1 ? "" : "s"} ignored`;
      setParsedHint(`${fieldsLabel} · ${unknownLabel}`);

      // Auto-advance: if the upload filled enough that the user can now
      // skip steps, jump them ahead to the first incomplete one. Most
      // commonly that's step 3 (Review & launch) for a complete .env.
      if (schema && knownCount > 0) {
        const next = firstIncompleteStep(schema, nextValues, status);
        setCurrentStep(next);
      }
    } catch (e) {
      setParsedHint(`Parse failed: ${e instanceof Error ? e.message : String(e)}`);
    }
  }

  /** Required keys in the current step are all filled (or already on disk). */
  function isStepComplete(step: WizardStep): boolean {
    if (!schema) return false;
    for (const g of STEP_GROUPS[step]) {
      const required = schema.keys.filter((k) => k.group === g && k.required);
      if (required.some(
        (k) => !((values[k.key] || "").trim() || status.present.includes(k.key)),
      )) return false;
    }
    return true;
  }

  if (!schema) {
    return (
      <SetupShell mode={mode}>
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

  const railSteps = computeRailSteps(schema, values, status);
  const step: WizardStep = currentStep ?? firstIncompleteStep(schema, values, status);
  // Only render groups that belong to the active step.
  const visibleGroups = STEP_GROUPS[step];
  const stepEntries = Array.from(byGroup.entries()).filter(
    ([g]) => visibleGroups.includes(g),
  );
  const stepReady = isStepComplete(step);

  // Step 4 (Review) gets a different layout: single column, dedicated
  // review summary + a prominent Save & launch banner with tinted bg.
  // Form-mixed two-column grid would bury the primary action.
  const isReviewStep = step === 4;

  return (
    <SetupShell mode={mode}>
      <div className="setup-card" data-mode={mode}>
        <SetupRail
          steps={railSteps}
          currentStep={step}
          onJump={setCurrentStep}
          mode={mode}
        />
        <div
          className={cn(
            "p-6 md:p-8",
            isReviewStep
              ? "flex flex-col gap-6"
              : "grid grid-cols-1 lg:grid-cols-[1fr_360px] gap-8",
          )}
        >
        {/* Main column: step content. */}
        <div>
          <div className="mb-5">
            <div className="text-[11px] font-mono uppercase tracking-[0.06em] text-[var(--color-text-faint)]">
              Step {step} of 4
            </div>
            <h2 className="text-[24px] font-semibold tracking-tight leading-tight text-[var(--color-text)] mt-1">
              {STEP_TITLES[step]}
            </h2>
            <p className="text-sm text-[var(--color-text-muted)] mt-2 max-w-xl">
              {STEP_LEADS[step]}
            </p>
          </div>

          {/* Field groups. When the step has a single group (step 1 +
              step 2), the step title already names it, so we skip the
              per-group header and render the fields directly. Step 3
              has multiple optional groups (Sigil / PDC / Advanced); we
              break those up with a slim divider + group name. */}
          <div className="space-y-6">
          {stepEntries.map(([group, keys], groupIdx) => {
            const groupKeyNames = keys.map((k) => k.key);
            const present = groupKeyNames.filter(
              (k) => (values[k] || status.present.includes(k)),
            ).length;
            const required = keys.filter((k) => k.required).length;
            const missing = keys.filter(
              (k) => k.required && !(values[k.key] || "").trim() && !status.present.includes(k.key),
            ).length;
            const showHeader = stepEntries.length > 1;

            return (
              <div key={group}>
                {showHeader && (
                  <div
                    className={cn(
                      "flex items-baseline gap-3 mb-3 pb-2",
                      groupIdx > 0 && "pt-3 border-t border-[var(--color-border)]",
                    )}
                  >
                    <div className="flex-1 min-w-0">
                      <div className="text-sm font-medium text-[var(--color-text)]">
                        {GROUP_TITLES[group]}
                      </div>
                      <div className="text-xs text-[var(--color-text-muted)] mt-0.5">
                        {GROUP_SUBTITLES[group]}
                      </div>
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
                      <span className="text-[var(--color-text-faint)] tabular-nums">
                        {present}/{keys.length}
                      </span>
                    </div>
                  </div>
                )}
                <div className="space-y-3">
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
              </div>
            );
          })}
          </div>

          {/* Step nav. Affordances follow the step's actual reachable
              actions, no disabled-Back on step 1, no Continue on the
              final step. Save & launch lives in the right-column box
              on step 3 and is the only primary action there. */}
          <div className="flex items-center gap-2 mt-6 pt-5 border-t border-[var(--color-border)]">
            {step > 1 && (
              <button
                type="button"
                onClick={() => setCurrentStep((step - 1) as WizardStep)}
                className={cn(
                  "h-9 px-3 rounded-md text-sm border border-[var(--color-border)]",
                  "text-[var(--color-text-muted)] hover:text-[var(--color-text)]",
                  "hover:border-[var(--color-border-strong)] transition-colors",
                )}
              >
                &larr; Back
              </button>
            )}
            {step < 4 && (
              <button
                type="button"
                onClick={() => setCurrentStep((step + 1) as WizardStep)}
                disabled={!stepReady}
                className={cn(
                  "ml-auto h-9 px-4 rounded-md text-sm font-medium",
                  "bg-[var(--color-accent)] text-[var(--color-on-accent)]",
                  "hover:bg-[var(--color-accent-hover)] transition-colors",
                  "disabled:opacity-40 disabled:cursor-not-allowed",
                )}
                title={stepReady ? undefined : "Fill in the required fields to continue."}
              >
                Continue &rarr;
              </button>
            )}
          </div>
        </div>

        {/* Right column: paste / upload, only on steps 1-3 where it's
            still useful. Step 4's prominent Save & launch panel renders
            below the grid in a single-column flow instead. */}
        {!isReviewStep && (
          <aside className="space-y-4">
            <PasteUpload
              text={pasteText}
              onTextChange={setPasteText}
              onParse={handleParse}
              parsedHint={parsedHint}
            />
          </aside>
        )}

        {/* Step-4 launch panel, its own visually-distinct section so the
            Save & launch CTA isn't buried inside a column of form fields.
            Accent-tinted background gives it the "this is the action"
            read at a glance. */}
        {isReviewStep && (
          <LaunchPanel
            schema={schema}
            values={values}
            status={status}
            saving={saving}
            ready={requiredReady}
            error={saveError}
            onSave={save}
            mode={mode}
          />
        )}
        </div>{/* /body */}
      </div>{/* /.setup-card */}
    </SetupShell>
  );
}

// ──────────────────────────────────────────────────────────────────
// LaunchPanel, step-4 "Save & launch" banner. Renders a config recap
// (per-group status) on the left, the big primary CTA + safety copy
// on the right. Accent-tinted bg to separate it visually from the
// form-mixed steps above.
// ──────────────────────────────────────────────────────────────────

function LaunchPanel({
  schema, values, status, saving, ready, error, onSave, mode,
}: {
  schema: SetupSchema;
  values: Record<string, string>;
  status: SetupStatus;
  saving: boolean;
  ready: boolean;
  error: string | null;
  onSave: () => void;
  /** "first-run" = fresh install, copy reads as "Save & launch".
   *  "settings"  = signed-in update, copy reads as "Update tokens". */
  mode: "first-run" | "settings";
}) {
  // Only the FORM values count as edits in settings mode, if nothing
  // was typed, there's nothing to save (everything's already on disk).
  // First-run always lets you save (writes the freshly-typed values).
  const hasEdits = Object.values(values).some((v) => v.trim() !== "");
  const isSettings = mode === "settings";
  function fieldIsSet(key: string): boolean {
    return !!((values[key] || "").trim() || status.present.includes(key));
  }
  function groupRollup(group: Group): {
    set: number; total: number; required: number; requiredSet: number;
  } {
    const keys = schema.keys.filter((k) => k.group === group);
    const required = keys.filter((k) => k.required);
    return {
      set:         keys.filter((k) => fieldIsSet(k.key)).length,
      total:       keys.length,
      required:    required.length,
      requiredSet: required.filter((k) => fieldIsSet(k.key)).length,
    };
  }

  // Per-group rollup, in wizard order. Status chips are rendered as
  // pills below the headline so the SE sees the entire config state
  // in one horizontal scan.
  const rollup: { group: Group; title: string }[] = [
    { group: "anthropic",     title: "Anthropic"        },
    { group: "grafana_cloud", title: "Grafana Cloud"    },
    { group: "pdc",           title: "PDC"              },
    { group: "sigil",         title: "AI Observability" },
  ];

  // Copy adapts to context. Fresh install is a "launch" moment; an
  // already-signed-in user updating a token is a "save these edits"
  // moment. Don't reuse launch language when there's nothing to launch.
  const headline = saving
    ? (isSettings ? "Updating .env…" : "Saving to .env…")
    : !ready
      ? "Almost there, fill required fields"
      : isSettings
        ? (hasEdits ? "Ready to update" : "No edits to save")
        : "Ready to launch";

  const ctaIdle =
    isSettings ? "Update tokens" :
    "Save & launch Clarion";
  const ctaBusy = isSettings ? "Updating…" : "Saving…";
  const ctaBlocked = "Fill required fields first";

  // Disable the CTA in settings mode when there's nothing to save,   // hitting "Update" with an empty form would write zero changes and
  // confuse the user about whether their click did anything.
  const ctaDisabled =
    !ready || saving || (isSettings && !hasEdits);

  return (
    <section
      aria-label="Save and launch"
      className={cn(
        "relative overflow-hidden rounded-xl border p-6 md:p-7",
        "border-[color:var(--color-accent-border)] bg-[var(--color-accent-bg)]",
      )}
    >
      {/* Soft accent halo, top-right, same visual language as the
          Dashboard HeroBuildCard so the SE recognizes this as the
          "primary action" surface of the page. */}
      <div
        aria-hidden="true"
        className="pointer-events-none absolute top-0 right-0 w-[280px] h-[280px]"
        style={{
          background:
            "radial-gradient(circle, color-mix(in srgb, var(--color-accent) 16%, transparent), transparent 70%)",
          filter: "blur(40px)",
        }}
      />

      <div className="relative grid gap-6 md:grid-cols-[1fr_auto] items-end">
        {/* Headline + description */}
        <div>
          <div className="flex items-center gap-2.5 text-[11px] font-mono uppercase tracking-[0.16em] text-[var(--color-accent)]">
            <KeyRound size={13} />
            <span>{isSettings ? "Update tokens" : "Save & launch"}</span>
          </div>
          <h3 className="mt-2 text-[22px] font-semibold tracking-tight leading-tight text-[var(--color-text)]">
            {headline}
          </h3>
          <p className="text-sm text-[var(--color-text-muted)] mt-2 max-w-xl">
            {isSettings ? (
              <>
                Writes your edits to <code className="font-mono text-[var(--color-text)]">.env</code> on this machine.
                Fields you didn&rsquo;t change keep their existing values. We back up the previous file to
                {" "}<code className="font-mono text-[var(--color-text)]">.env.bak</code> before writing.
              </>
            ) : (
              <>
                Writes your tokens to <code className="font-mono text-[var(--color-text)]">.env</code> on this machine.
                The previous file is backed up to <code className="font-mono text-[var(--color-text)]">.env.bak</code> so you can roll back if anything goes wrong.
              </>
            )}
          </p>
        </div>

        {/* Primary CTA, anchored to the right on wide viewports, full
            width below md so it doesn't get cramped on smaller screens. */}
        <button
          type="button"
          onClick={onSave}
          disabled={ctaDisabled}
          title={
            saving        ? ctaBusy :
            !ready        ? ctaBlocked :
            isSettings && !hasEdits ? "Type a new value in any field to enable update." :
            undefined
          }
          className={cn(
            "h-11 px-6 rounded-md text-sm font-medium whitespace-nowrap transition-colors",
            !ctaDisabled
              ? "bg-[var(--color-accent)] text-[var(--color-on-accent)] hover:bg-[var(--color-accent-hover)] shadow-md"
              : "bg-[var(--color-canvas-elev2)] text-[var(--color-text-faint)] cursor-not-allowed border border-[var(--color-border)]",
          )}
        >
          {saving ? (
            <span className="inline-flex items-center gap-2">
              <Loader2 size={14} className="animate-spin" /> {ctaBusy}
            </span>
          ) : !ready ? (
            ctaBlocked
          ) : isSettings && !hasEdits ? (
            "No edits"
          ) : (
            <span className="inline-flex items-center gap-2">
              <KeyRound size={14} /> {ctaIdle}
            </span>
          )}
        </button>
      </div>

      {/* Status pill row, horizontal, scans in one glance. Each pill
          shows: dot · group name · count. Tone-colored to mirror the
          rail at the top of the wizard. */}
      <div className="relative mt-5 flex flex-wrap gap-2">
        {rollup.map(({ group, title }) => {
          const { set, total, required, requiredSet } = groupRollup(group);
          if (total === 0) return null;
          const missing = required > 0 && requiredSet < required;
          const empty = set === 0;
          const tone: "set" | "skipped" | "missing" =
            missing ? "missing" : empty ? "skipped" : "set";
          return (
            <span
              key={group}
              className={cn(
                "inline-flex items-center gap-2 h-7 pl-2 pr-2.5 rounded-full text-xs border",
                tone === "set" &&
                  "border-[color:var(--color-success)]/40 bg-[var(--color-success-bg)] text-[var(--color-success)]",
                tone === "skipped" &&
                  "border-[var(--color-border)] bg-[var(--color-canvas-elev2)]/60 text-[var(--color-text-muted)]",
                tone === "missing" &&
                  "border-[color:var(--color-danger)]/40 bg-[var(--color-danger-bg)] text-[var(--color-danger)]",
              )}
            >
              <span
                aria-hidden="true"
                className={cn(
                  "inline-flex items-center justify-center w-4 h-4 rounded-full",
                  tone === "set"     && "bg-[var(--color-success)] text-[var(--color-canvas)]",
                  tone === "missing" && "bg-[var(--color-danger)]  text-[var(--color-canvas)]",
                  tone === "skipped" && "bg-[var(--color-canvas)] text-[var(--color-text-faint)] border border-[var(--color-border)]",
                )}
              >
                {tone === "set" ? <Check size={10} /> :
                 tone === "missing" ? <span className="font-mono text-[10px] leading-none">!</span> :
                 <span className="font-mono text-[10px] leading-none">&middot;</span>}
              </span>
              <span className="font-medium text-[var(--color-text)]">{title}</span>
              <span className="font-mono text-[10px] tabular-nums opacity-80">
                {tone === "skipped" ? "skipped" : `${set}/${total}`}
              </span>
            </span>
          );
        })}
      </div>

      {error && (
        <div className="relative mt-4 text-xs text-[var(--color-danger)] flex items-start gap-1.5">
          <AlertCircle size={12} className="shrink-0 mt-0.5" />
          <span>{error}</span>
        </div>
      )}
    </section>
  );
}

// ──────────────────────────────────────────────────────────────────
// Page shell, centered v1-design wizard layout. Logo + brand text
// on top, big gradient H1, subtitle, then the setup-card with rail.
// Reused by SetupGate's loading/error states for layout consistency.
// ──────────────────────────────────────────────────────────────────

function SetupShell({
  children, mode,
}: {
  children: React.ReactNode;
  mode: "first-run" | "settings";
}) {
  if (mode === "settings") {
    // In-app settings, sits inside the Layout's main area, so we
    // drop the centered min-screen hero and use a regular page header
    // matching the Dashboard / DemoHistory style.
    return (
      <div className="space-y-6">
        <header>
          <div className="text-[11px] font-mono uppercase tracking-[0.08em] text-[var(--color-text-faint)]">
            Settings
          </div>
          <h1 className="mt-2 text-[28px] font-semibold tracking-tight leading-tight text-[var(--color-text)]">
            Tokens &amp; credentials
          </h1>
          <p className="text-[var(--color-text-muted)] mt-1 text-sm max-w-2xl">
            Rotate API keys, re-upload an <code className="font-mono">.env</code>, or fix any
            field that&rsquo;s gone stale. Changes are written to{" "}
            <code className="font-mono">.env</code> on this machine on Save.
          </p>
        </header>
        {children}
      </div>
    );
  }
  // First-run wizard, centered hero block before the gated app loads.
  return (
    <div className="setup-wrap">
      <div className="w-full max-w-[880px]">
        <header className="text-center mb-8">
          <div className="inline-flex items-center gap-2.5 text-sm text-[var(--color-text-muted)] mb-3.5">
            <span className="setup-mark" aria-hidden="true">
              <Logo size={14} monochrome />
            </span>
            <span className="font-medium text-[var(--color-text)]">Proj-Clarion</span>
          </div>
          <h1 className="text-[36px] leading-[1.1] tracking-[-0.025em] font-semibold m-0 text-[var(--color-text)]">
            Let&rsquo;s get you <span className="h1-display">set up</span>.
          </h1>
          <p className="text-[var(--color-text-muted)] text-[15px] m-0 mt-2 mx-auto max-w-[520px]">
            Three quick steps. We&rsquo;ll verify each credential as you go, nothing&rsquo;s
            persisted to the browser.
          </p>
        </header>
        {children}
      </div>
    </div>
  );
}

// ──────────────────────────────────────────────────────────────────
// SetupRail, v1's 3-step progress rail across the top of the card.
// Derives state from the schema + filled values, no extra source of
// truth. Step labels match the v1 design canvas.
// ──────────────────────────────────────────────────────────────────

type StepStatus = "pending" | "active" | "done";

function SetupRail({
  steps, currentStep, onJump, mode,
}: {
  steps: { label: string; sub: string; status: StepStatus }[];
  currentStep: WizardStep;
  onJump: (step: WizardStep) => void;
  /** First-run locks forward navigation past the first incomplete step
   *  so a user can't skip required fields. Settings mode lets the user
   *  jump freely, they're tweaking, not learning the flow. */
  mode: "first-run" | "settings";
}) {
  // In first-run, the max reachable step is the first non-done one
  // (the user can be on it but not past it). Anything beyond is
  // locked until the gating step's required fields are filled.
  let maxReachable: WizardStep = 4;
  if (mode === "first-run") {
    const firstActiveIdx = steps.findIndex((s) => s.status !== "done");
    maxReachable = (firstActiveIdx === -1 ? 4 : firstActiveIdx + 1) as WizardStep;
  }

  return (
    <div className="setup-rail" role="tablist" aria-label="Setup steps">
      {steps.map((s, i) => {
        const stepIndex = (i + 1) as WizardStep;
        const isCurrent = stepIndex === currentStep;
        const locked = mode === "first-run" && stepIndex > maxReachable;
        return (
          <button
            type="button"
            key={s.label}
            role="tab"
            aria-selected={isCurrent}
            aria-disabled={locked}
            onClick={() => { if (!locked) onJump(stepIndex); }}
            title={
              locked
                ? "Complete the previous step's required fields first."
                : undefined
            }
            className={cn(
              "setup-rail-item text-left",
              s.status === "done" && "done",
              isCurrent           && "active",
              locked              && "locked",
            )}
          >
            <div className="n">
              {s.status === "done" ? (
                <Check size={12} aria-hidden="true" />
              ) : locked ? (
                <Lock size={11} aria-hidden="true" />
              ) : (
                stepIndex
              )}
            </div>
            <div className="lbl-grp">
              <span className="lbl">{s.label}</span>
              <span className="sub">{s.sub}</span>
            </div>
          </button>
        );
      })}
    </div>
  );
}

/** Compute the rail's 4-step state from the schema + current values.
 *  Step 1 = Anthropic done.
 *  Step 2 = Grafana Cloud (+ optional PDC), required for hosting demos.
 *  Step 3 = AI Observability (Sigil), optional, always "active" once
 *           you can reach it.
 *  Step 4 = Review & launch, terminal step, no fields beyond Advanced.
 *           Sigil presence is informational only, not gating.
 */
function computeRailSteps(
  schema: SetupSchema,
  values: Record<string, string>,
  status: SetupStatus,
): { label: string; sub: string; status: StepStatus }[] {
  function groupComplete(group: Group): boolean {
    const required = schema.keys.filter((k) => k.group === group && k.required);
    if (required.length === 0) return true;
    return required.every(
      (k) => ((values[k.key] || "").trim() || status.present.includes(k.key)),
    );
  }
  function groupHasAnyValue(group: Group): boolean {
    return schema.keys.filter((k) => k.group === group).some(
      (k) => ((values[k.key] || "").trim() || status.present.includes(k.key)),
    );
  }
  const s1 = groupComplete("anthropic");
  const s2 = groupComplete("grafana_cloud");
  const sigilSet = groupHasAnyValue("sigil");
  return [
    {
      label: "Anthropic",
      sub: s1 ? "API key verified" : "API key required",
      status: s1 ? "done" : "active",
    },
    {
      label: "Grafana Cloud",
      sub: s2 ? "Stack & token verified" : "Stack & service token",
      status: s2 ? "done" : s1 ? "active" : "pending",
    },
    {
      label: "AI Observability",
      sub: sigilSet ? "Sigil configured" : "Optional · Sigil",
      status: sigilSet ? "done" : (s1 && s2 ? "active" : "pending"),
    },
    {
      label: "Review & launch",
      sub: "Save tokens to .env",
      status: s1 && s2 ? "active" : "pending",
    },
  ];
}

// ──────────────────────────────────────────────────────────────────
// FieldRow, one row per env key, with masked input, test button,
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
  // Password-UI pattern for fields the user has already configured.
  // The backend deliberately doesn't expose stored secret VALUES (only
  // their `present` keys), so when the user is "viewing" a stored field
  // we render a fixed masked placeholder. The Eye toggle still works
  // on values the user IS typing, so they can confirm a freshly-pasted
  // token before saving.
  const storedDisplay = alreadyPresent && !value;
  return (
    <div>
      <div className="flex items-baseline justify-between gap-2 mb-1">
        <label
          htmlFor={`f-${meta.key}`}
          className="text-xs font-medium text-[var(--color-text)] inline-flex items-center gap-2"
        >
          <span>{meta.label}</span>
          {meta.required && <span className="text-[var(--color-danger)] -ml-1">*</span>}
          {alreadyPresent && (
            <span
              className={cn(
                "inline-flex items-center gap-1 px-1.5 h-[18px] rounded-full",
                "text-[10px] font-mono uppercase tracking-wider",
                "bg-[var(--color-success-bg)] text-[var(--color-success)] border border-[color:var(--color-success)]/30",
              )}
              title="This value is stored in .env on this machine."
            >
              <Check size={9} aria-hidden="true" />
              Stored
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
          {/* Single input element. When the field has a stored value and
              the user hasn't typed anything yet, we show a Lock prefix
              icon + a "•••••••• click to replace" placeholder so it
              reads as a password-UI "set, tap to change" affordance. */}
          {storedDisplay && (
            <Lock
              size={12}
              aria-hidden="true"
              className="absolute left-2.5 top-1/2 -translate-y-1/2 text-[var(--color-text-faint)]"
            />
          )}
          <input
            id={`f-${meta.key}`}
            type={meta.secret && !reveal ? "password" : "text"}
            value={value}
            onChange={(e) => onChange(e.target.value)}
            placeholder={
              storedDisplay
                ? meta.secret
                  ? "•••••••••••••••• stored, type to replace"
                  : "Stored, type to replace"
                : meta.placeholder
            }
            autoComplete="off"
            spellCheck={false}
            className={cn(
              "w-full bg-[var(--color-canvas)] border rounded-md py-1.5 text-sm font-mono",
              "outline-none transition-colors",
              validation?.valid === false
                ? "border-[var(--color-danger)]"
                : validation?.valid === true
                ? "border-[var(--color-success)]"
                : storedDisplay
                ? "border-[color:var(--color-success)]/25"
                : "border-[var(--color-border)] focus:border-[var(--color-border-strong)]",
              storedDisplay ? "pl-7" : "pl-2.5",
              meta.secret ? "pr-8" : "pr-2.5",
            )}
          />
          {meta.secret && (
            <button
              type="button"
              onClick={onReveal}
              aria-label={reveal ? "Hide value" : "Show value"}
              disabled={storedDisplay}
              title={
                storedDisplay
                  ? "Stored value isn't readable from the server, type a new one to confirm."
                  : reveal ? "Hide" : "Show"
              }
              className={cn(
                "absolute right-1.5 top-1/2 -translate-y-1/2 p-1",
                "text-[var(--color-text-faint)] hover:text-[var(--color-text)]",
                "disabled:opacity-30 disabled:cursor-not-allowed",
              )}
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
              {validation.reason}{validation.hint ? `, ${validation.hint}` : ""}
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
// Paste / upload sidebar, drag-drop + textarea, parse via API
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
