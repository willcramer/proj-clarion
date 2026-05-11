/**
 * Typed client for /api/setup/* — the first-run wizard endpoints.
 *
 * These calls use raw `fetch` (not the shared `request` helper) because
 * we explicitly DON'T want the SetupGate's 503-redirect to kick in
 * here — the wizard itself is the destination of that redirect, so it
 * needs to stay reachable even when the gate is closed.
 */

const BASE = "/api/setup";

export type Group = "anthropic" | "grafana_cloud" | "sigil" | "pdc" | "advanced";

export interface SetupKeyMeta {
  key: string;
  group: Group;
  required: boolean;
  label: string;
  description: string;
  placeholder: string;
  help_url: string;
  secret: boolean;
  /** Validator name; null = no live validation (format check only). */
  validator: string | null;
}

export interface SetupSchema {
  keys: SetupKeyMeta[];
  required_keys: string[];
}

export interface GroupRollup {
  required: number;
  present: number;
  missing: string[];
}

export interface SetupStatus {
  ready: boolean;
  missing: string[];
  present: string[];
  groups: Record<string, GroupRollup>;
}

export interface ParseResult {
  parsed: Record<string, string>;
  known: string[];
  unknown: string[];
  ignored: string[];
}

export interface ValidateResult {
  valid: boolean;
  reason: string;
  hint: string;
}

export interface SaveResult {
  ok: boolean;
  ready: boolean;
  changed: string[];
  env_path: string;
  backup_path: string | null;
}

/**
 * Identity returned by /api/setup/identity. Any field can be null when
 * the corresponding service isn't configured / reachable yet — the
 * UserMenu falls back through them in priority order.
 */
export interface Identity {
  stack_url: string;
  org_slug: string;
  org_name: string | null;
  user_name: string | null;
  user_email: string | null;
  anthropic_model: string;
  setup_complete: boolean;
  env_path: string;
}

export interface SignOutResult {
  ok: boolean;
  ready: boolean;
  cleared: string[];
  env_path: string;
  backup_path: string | null;
}

async function json<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${BASE}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      Accept: "application/json",
      ...(init?.headers ?? {}),
    },
  });
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = body?.detail?.message ?? body?.detail ?? detail;
      // Save endpoint returns a structured failures list — surface it
      // so the UI can highlight per-field errors instead of a wall of text.
      if (body?.detail?.failures) {
        const err = new Error(detail) as Error & { failures?: unknown };
        err.failures = body.detail.failures;
        throw err;
      }
    } catch (e) {
      if (e instanceof Error && (e as Error & { failures?: unknown }).failures) throw e;
      /* fall through */
    }
    throw new Error(`Setup API ${res.status}: ${detail}`);
  }
  return (await res.json()) as T;
}

export const setupApi = {
  status:   ()                                                          => json<SetupStatus>("/status"),
  schema:   ()                                                          => json<SetupSchema>("/schema"),
  identity: ()                                                          => json<Identity>("/identity"),
  parse:    (text: string)                                              => json<ParseResult>("/parse", { method: "POST", body: JSON.stringify({ text }) }),
  validate: (key: string, value: string, all_values: Record<string, string> = {}) =>
              json<ValidateResult>("/validate", { method: "POST", body: JSON.stringify({ key, value, all_values }) }),
  save:     (values: Record<string, string>, merge: boolean = true) =>
              json<SaveResult>("/save", { method: "POST", body: JSON.stringify({ values, merge }) }),
  signout:  ()                                                          => json<SignOutResult>("/signout", { method: "POST", body: JSON.stringify({ confirm: true }) }),
};
