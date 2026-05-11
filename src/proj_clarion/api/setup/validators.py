"""Per-key live validators.

Each validator takes a candidate value and returns a `ValidationResult`
that the UI surfaces as a green / red badge. Validators should:

- Be CHEAP. Anthropic = one models-list call, Grafana = one /api/health
  hit. No multi-second waits.
- Be SAFE. The candidate value is provided by the user via the setup
  form, but we still never log it. Wrap exceptions and surface a short
  reason, not a full stack trace.
- Time out. Default 8 seconds — enough for cold DNS + TLS, short enough
  that a hung endpoint doesn't freeze the UI.

If a validator name in `schema.py` isn't registered here, the setup
endpoint falls back to a pure format check (non-empty + length sanity).
"""

from __future__ import annotations

import base64
import re
from dataclasses import dataclass
from typing import Callable

import httpx

VALIDATION_TIMEOUT_SECONDS = 8.0


@dataclass(frozen=True)
class ValidationResult:
    valid: bool
    reason: str = ""
    # Optional hint string shown to the user when invalid (e.g. "did you
    # paste the access-policy ID instead of the token?").
    hint: str = ""


# ──────────────────────────────────────────────────────────────────
# Anthropic — `sk-ant-...`
# ──────────────────────────────────────────────────────────────────


def _validate_anthropic(value: str) -> ValidationResult:
    """Hit Anthropic's models endpoint with the candidate key — the
    cheapest auth check (no inference, no token cost). 401 → invalid;
    200 → valid; anything else → network-flavoured reason."""
    if not value.startswith("sk-ant-"):
        return ValidationResult(
            valid=False,
            reason="Key doesn't start with `sk-ant-`.",
            hint="Anthropic console keys always start with `sk-ant-api03-` or similar.",
        )
    try:
        r = httpx.get(
            "https://api.anthropic.com/v1/models",
            headers={
                "x-api-key":         value,
                "anthropic-version": "2023-06-01",
            },
            timeout=VALIDATION_TIMEOUT_SECONDS,
        )
    except httpx.TimeoutException:
        return ValidationResult(valid=False, reason="Anthropic API timed out.")
    except httpx.RequestError as exc:
        return ValidationResult(valid=False, reason=f"Network error: {exc.__class__.__name__}")
    if r.status_code == 200:
        return ValidationResult(valid=True)
    if r.status_code in (401, 403):
        return ValidationResult(
            valid=False,
            reason="Anthropic rejected the key (401/403).",
            hint="Double-check you copied the full key from the console.",
        )
    return ValidationResult(valid=False, reason=f"Unexpected status {r.status_code}.")


# ──────────────────────────────────────────────────────────────────
# Grafana Cloud stack URL — should be a real Grafana stack
# ──────────────────────────────────────────────────────────────────


def _validate_grafana_stack_url(value: str) -> ValidationResult:
    """`GET <stack>/api/health` — public endpoint that confirms the URL
    points at a real Grafana instance. No auth needed."""
    url = value.strip().rstrip("/")
    if not url.startswith(("http://", "https://")):
        return ValidationResult(
            valid=False,
            reason="URL must start with https:// (or http:// for local).",
        )
    try:
        r = httpx.get(
            f"{url}/api/health",
            timeout=VALIDATION_TIMEOUT_SECONDS,
            follow_redirects=True,
        )
    except httpx.TimeoutException:
        return ValidationResult(valid=False, reason="Stack URL timed out.")
    except httpx.RequestError as exc:
        return ValidationResult(
            valid=False,
            reason=f"Couldn't reach the stack ({exc.__class__.__name__}).",
            hint="If this is a private stack, the API needs network access to it.",
        )
    if r.status_code == 200 and "database" in r.text.lower():
        return ValidationResult(valid=True)
    return ValidationResult(
        valid=False,
        reason=f"Stack /api/health returned {r.status_code} — is the URL right?",
    )


# ──────────────────────────────────────────────────────────────────
# Grafana Cloud API token — `glc_...`
# ──────────────────────────────────────────────────────────────────


def _validate_grafana_cloud_token(value: str, *, all_values: dict[str, str]) -> ValidationResult:
    """Validate a Grafana Cloud token against the SURFACE it's meant for.

    Grafana Cloud has two API surfaces and tokens for one don't work on
    the other:

      1. **Control-plane** — `https://grafana.com/api/*`. Used by `gcx`
         to push dashboards / alerts / KG rules / datasources across the
         org. Tokens are issued as access policies on grafana.com → are
         `glc_...` shaped. Most Clarion workflows go through here.

      2. **Per-stack** — `https://<stack>.grafana.net/api/*`. Used for
         in-stack admin (users, folders read with `users:read` scope).
         Tokens are service accounts created INSIDE the stack →
         `glsa_...` shaped. Rare for Clarion's flow.

    Strategy: try BOTH. If either returns 2xx the token is good. If
    control-plane returns 200 but per-stack returns 401, that's the
    common Clarion shape (Cloud-API token) → return valid with a
    matter-of-fact hint about which surface it's for. If neither
    surface accepts the token → genuine 401.
    """
    stack = (all_values.get("GRAFANA_CLOUD_STACK_URL") or "").strip().rstrip("/")
    if not stack:
        return ValidationResult(
            valid=False,
            reason="Set the Grafana stack URL first; this token is checked against it.",
        )
    if not value or len(value) < 16:
        return ValidationResult(valid=False, reason="Token too short to be a real Grafana Cloud token.")

    headers = {"Authorization": f"Bearer {value}"}
    control_plane_status: int | None = None
    per_stack_status: int | None = None
    per_stack_403 = False

    try:
        # 1) Control-plane surface — the canonical home for Clarion's
        # `gcx` operations. `/api/instances` lists stacks visible to the
        # token; it's cheap and any access policy with any read scope
        # can hit it.
        r = httpx.get(
            "https://grafana.com/api/instances",
            headers=headers,
            timeout=VALIDATION_TIMEOUT_SECONDS,
            follow_redirects=True,
        )
        control_plane_status = r.status_code

        # 2) Per-stack surface. Try `/api/folders` (broadest), fall back
        # to `/api/user`. 403 anywhere counts as "auth ok, scope narrow".
        for path in ("/api/folders", "/api/user"):
            r = httpx.get(
                f"{stack}{path}",
                headers=headers,
                timeout=VALIDATION_TIMEOUT_SECONDS,
                follow_redirects=True,
            )
            per_stack_status = r.status_code
            if 200 <= r.status_code < 300:
                break
            if r.status_code == 403:
                per_stack_403 = True
            elif r.status_code != 401:
                # Some other transient status; stop probing per-stack.
                break
    except httpx.TimeoutException:
        return ValidationResult(valid=False, reason="Stack timed out validating the token.")
    except httpx.RequestError as exc:
        return ValidationResult(valid=False, reason=f"Network error: {exc.__class__.__name__}")

    cp_ok = control_plane_status is not None and 200 <= control_plane_status < 300
    ps_ok = per_stack_status is not None and 200 <= per_stack_status < 300

    if cp_ok and ps_ok:
        return ValidationResult(valid=True, reason="Token works on both Cloud API and per-stack API.")
    if cp_ok:
        return ValidationResult(
            valid=True,
            reason="Token accepted (Grafana.com Cloud API).",
            hint="This is a control-plane token — perfect for Clarion's dashboard/alert/KG-rule pushes via gcx. The per-stack API rejects it, which is expected for this token type.",
        )
    if ps_ok:
        return ValidationResult(
            valid=True,
            reason="Token accepted (per-stack API).",
            hint="This is a per-stack service-account token. Works for in-stack admin; if you also use `gcx` for cross-org operations you may want to ALSO supply a Cloud API token.",
        )
    if per_stack_403:
        return ValidationResult(
            valid=True,
            reason="Token accepted (limited read scope).",
            hint="Token authenticates but can't read /api/folders. That's fine for OTLP push — scope errors will surface at push time if any.",
        )
    # Both surfaces returned 401 (or near-401).
    return ValidationResult(
        valid=False,
        reason=(
            f"Rejected by both surfaces "
            f"(grafana.com: {control_plane_status}, stack: {per_stack_status})."
        ),
        hint="Token may be revoked or malformed. Generate a fresh access-policy token at "
             "grafana.com → Access Policies, with at least `metrics:write` / `dashboards:write` scopes.",
    )


# ──────────────────────────────────────────────────────────────────
# Generic format check — fallback when no live validator is registered
# ──────────────────────────────────────────────────────────────────


_BASIC_AUTH_RE = re.compile(r"^Basic\s+[A-Za-z0-9+/]+={0,2}$")


def _validate_format(key: str, value: str) -> ValidationResult:
    """Sanity-check the shape of an env value without hitting the network.

    Special cases a few well-known formats (Basic auth, URLs); falls back
    to "non-empty" for everything else. The point isn't to be strict — it's
    to catch the obvious typo ('Basic ' missing → user pasted raw token)."""
    val = value.strip()
    if not val:
        return ValidationResult(valid=False, reason="Empty value.")
    # OTLP auth header
    if "OTLP_AUTH" in key:
        if not _BASIC_AUTH_RE.match(val):
            return ValidationResult(
                valid=False,
                reason="Expected `Basic <base64(...)>`.",
                hint="Copy the full Authorization header from the Cloud OTLP page.",
            )
        # Sanity check that it decodes to `id:token`-ish
        try:
            decoded = base64.b64decode(val[len("Basic "):].encode()).decode("utf-8")
            if ":" not in decoded:
                return ValidationResult(
                    valid=False,
                    reason="Decoded value isn't `instance_id:token`.",
                )
        except Exception:  # noqa: BLE001 — broad on purpose, any decode failure is invalid
            return ValidationResult(valid=False, reason="Couldn't base64-decode the value.")
        return ValidationResult(valid=True)
    # URL-shaped keys
    if key.endswith(("URL", "ENDPOINT")):
        if not val.startswith(("http://", "https://")):
            return ValidationResult(valid=False, reason="Must start with http:// or https://")
    return ValidationResult(valid=True)


# ──────────────────────────────────────────────────────────────────
# Registry — `schema.SetupKey.validator` looks up here
# ──────────────────────────────────────────────────────────────────


_REGISTRY: dict[str, Callable[..., ValidationResult]] = {
    "anthropic_api_key":   _validate_anthropic,
    "grafana_stack_url":   _validate_grafana_stack_url,
    "grafana_cloud_token": _validate_grafana_cloud_token,
}


def validate(
    key: str,
    value: str,
    *,
    all_values: dict[str, str] | None = None,
    validator_name: str | None = None,
) -> ValidationResult:
    """Dispatch to the right validator. Falls back to format check.

    `all_values` is the full candidate map from the form — some
    validators (e.g. Grafana token) need a sibling value to do their job
    (the stack URL). Pass it when available; otherwise leave None.
    """
    if validator_name:
        impl = _REGISTRY.get(validator_name)
        if impl is not None:
            # Some validators need all_values, some just value.
            try:
                if validator_name == "grafana_cloud_token":
                    return impl(value, all_values=all_values or {})
                return impl(value)
            except Exception as exc:  # noqa: BLE001 — never crash the route
                return ValidationResult(valid=False, reason=f"Validator error: {exc.__class__.__name__}")
    return _validate_format(key, value)
