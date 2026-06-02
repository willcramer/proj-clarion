"""Setup wizard routes.

All four endpoints live under `/api/setup/*` and are excluded from the
"setup-required" middleware (otherwise they'd gate themselves out and
the user could never reach the setup form).

  GET  /api/setup/status     — what's present, what's missing (no values
                               echoed back, only key names)
  POST /api/setup/parse      — parse free-form text into a candidate
                               map. In-memory only; nothing persisted.
  POST /api/setup/validate   — test a single candidate value against the
                               live service. Never logs the value.
  POST /api/setup/save       — write `.env` atomically and refresh
                               `os.environ` so the gate flips without a
                               server restart.
  GET  /api/setup/schema     — exposes the SETUP_KEYS metadata to the UI
                               (labels, help URLs, validators). Lets the
                               UI render the form from a single source
                               of truth.
"""

from __future__ import annotations

import os
from typing import Any

import httpx
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from proj_clarion.api.setup import (
    REQUIRED_KEYS,
    SETUP_KEYS,
    check_status,
    refresh_environment,
    save_env,
)
from proj_clarion.api.setup.parsers import parse_user_input
from proj_clarion.api.setup.persistence import env_path
from proj_clarion.api.setup.schema import SETUP_KEYS_BY_KEY
from proj_clarion.api.setup.validators import validate as live_validate

router = APIRouter(prefix="/api/setup", tags=["setup"])


# ──────────────────────────────────────────────────────────────────
# Request / response models
# ──────────────────────────────────────────────────────────────────


class ParseRequest(BaseModel):
    text: str = Field(..., description="Raw user input — env/JSON/yaml-ish")


class ValidateRequest(BaseModel):
    key: str = Field(..., description="Env var name to validate")
    value: str = Field(..., description="Candidate value")
    # `all_values` lets validators that depend on a sibling field (e.g.
    # Grafana token needs the stack URL) do their job. UI sends the
    # full form-state snapshot.
    all_values: dict[str, str] = Field(default_factory=dict)


class SaveRequest(BaseModel):
    values: dict[str, str] = Field(..., description="Full set of KEY→VALUE pairs to persist")
    merge: bool = Field(
        default=True,
        description="When true, merge onto existing .env. When false, replace it.",
    )


# ──────────────────────────────────────────────────────────────────
# Routes
# ──────────────────────────────────────────────────────────────────


@router.get("/status")
def get_status() -> dict[str, Any]:
    """Snapshot of which required/optional keys are set. Never echoes
    values — only key names. Safe to call without auth (this endpoint
    is, by definition, what gates auth)."""
    return check_status()


@router.get("/telemetry-preflight")
def get_telemetry_preflight(probe: bool = False) -> dict[str, Any]:
    """Per-signal readiness for shipping ALL telemetry to a Grafana Cloud
    tenant (metrics/logs/traces/profiles/sigil). For each: is it configured,
    the exact scope its token needs, and a precise remediation when not.
    `?probe=true` adds a live AUTH probe of the Cloud token (no tenant
    writes). Never echoes token values."""
    from proj_clarion.observability.preflight import telemetry_preflight

    checks = telemetry_preflight(probe=probe)
    return {
        "ok": all(c.ok for c in checks),
        "checks": [c.as_dict() for c in checks],
    }


@router.get("/schema")
def get_schema() -> dict[str, Any]:
    """Expose the SETUP_KEYS metadata so the UI can render the form from
    a single source of truth. Read-only; no values, just labels + help."""
    return {
        "keys": [
            {
                "key":         k.key,
                "group":       k.group,
                "required":    k.required,
                "label":       k.label,
                "description": k.description,
                "placeholder": k.placeholder,
                "help_url":    k.help_url,
                "secret":      k.secret,
                # We expose the validator NAME (not the function); the UI
                # uses it to decide whether to show a "Test" button.
                "validator":   k.validator,
            }
            for k in SETUP_KEYS
        ],
        "required_keys": sorted(REQUIRED_KEYS),
    }


@router.post("/parse")
def post_parse(body: ParseRequest) -> dict[str, Any]:
    """Parse a blob of text (uploaded file contents OR pasted into the
    textarea) into a candidate map. Server-side parsing only — the UI
    never has to know the format details.

    Returns:
      {
        "parsed":     {KEY: VALUE, ...},   # known + custom keys
        "known":      [KEY, ...],          # subset that match SETUP_KEYS
        "unknown":    [KEY, ...],          # parsed but not in schema
        "ignored":    [],                  # reserved for future "rejected" rule
      }

    UI uses `known` to auto-fill the form; `unknown` is surfaced as a
    "we found these but they're not in our schema, save anyway?" prompt.
    """
    parsed = parse_user_input(body.text)
    known   = sorted(k for k in parsed if k in SETUP_KEYS_BY_KEY)
    unknown = sorted(k for k in parsed if k not in SETUP_KEYS_BY_KEY)
    return {
        "parsed":  parsed,
        "known":   known,
        "unknown": unknown,
        "ignored": [],
    }


@router.post("/validate")
def post_validate(body: ValidateRequest) -> dict[str, Any]:
    """Validate a single candidate. Returns `{valid, reason, hint}`.
    Never echoes the value; the response carries only outcome + a short
    diagnostic string the UI shows under the field."""
    setup_key = SETUP_KEYS_BY_KEY.get(body.key)
    validator_name = setup_key.validator if setup_key else None
    result = live_validate(
        body.key,
        body.value,
        all_values=body.all_values,
        validator_name=validator_name,
    )
    return {
        "valid":  result.valid,
        "reason": result.reason,
        "hint":   result.hint,
    }


@router.get("/identity")
def get_identity() -> dict[str, Any]:
    """Who's "signed in" — derived from current env + a single Grafana
    Cloud round-trip.

    Returns whatever can be discovered without exposing tokens:
      - `stack_url`        — from GRAFANA_CLOUD_STACK_URL
      - `org_name`         — from `<stack>/api/org`
      - `org_slug`         — derived from stack subdomain
      - `user_name`        — from `<stack>/api/user` (might be the
                              service-account name when token is a SA)
      - `user_email`       — from `<stack>/api/user`
      - `anthropic_model`  — model id (defaults to claude-opus-4-7)
      - `setup_complete`   — convenience mirror of /status's `ready`

    Tolerant of partial setup: if the Cloud token isn't set yet, returns
    only what's locally inferable so the UserMenu can still render a
    "Not signed in yet" state without throwing.
    """
    stack_url = (os.environ.get("GRAFANA_CLOUD_STACK_URL") or "").strip().rstrip("/")
    api_token = (os.environ.get("GRAFANA_CLOUD_API_TOKEN") or "").strip()
    model     = (os.environ.get("ANTHROPIC_MODEL") or "claude-opus-4-7").strip()

    # Subdomain slug — handy when /api/org fails. `https://mystack.grafana.net`
    # → `mystack`. Falls back to "" if URL is malformed.
    org_slug = ""
    if stack_url.startswith(("http://", "https://")):
        host = stack_url.split("://", 1)[1].split("/", 1)[0]
        if host.endswith(".grafana.net"):
            org_slug = host[: -len(".grafana.net")]

    out: dict[str, Any] = {
        "stack_url":       stack_url,
        "org_slug":        org_slug,
        "org_name":        None,
        "user_name":       None,
        "user_email":      None,
        "anthropic_model": model,
        "setup_complete":  bool(check_status()["ready"]),
        "env_path":        str(env_path()),
    }

    # If we have both URL + token, fetch live identity. Failures are
    # non-fatal — we still return what we know.
    if stack_url and api_token:
        try:
            headers = {"Authorization": f"Bearer {api_token}"}
            org = httpx.get(f"{stack_url}/api/org", headers=headers,
                            timeout=5.0, follow_redirects=True)
            if org.status_code == 200:
                data = org.json()
                out["org_name"] = data.get("name")
            user = httpx.get(f"{stack_url}/api/user", headers=headers,
                             timeout=5.0, follow_redirects=True)
            if user.status_code == 200:
                data = user.json()
                out["user_name"]  = data.get("name") or data.get("login")
                out["user_email"] = data.get("email")
        except httpx.RequestError:
            # Network/timeout — leave the live fields as None. The slug
            # and stack URL are still useful for the UserMenu badge.
            pass

    return out


@router.post("/save")
def post_save(body: SaveRequest) -> dict[str, Any]:
    """Persist the candidate map to `.env` and refresh os.environ.

    Strategy:
      1. Re-validate all REQUIRED keys server-side. The UI should have
         already validated them, but we don't trust the UI to be the
         enforcement point — anyone with the API URL could call this.
      2. Atomic write via persistence.save_env() (tmp + rename + backup).
      3. Reload .env into os.environ so the running API picks up the
         changes without a uvicorn restart.
      4. Return `{ok, ready, changed: [keys], backup_path}`.

    On any required-key failure, returns 400 with the list of failures
    so the UI can highlight which fields are still bad.
    """
    failures: list[dict[str, str]] = []
    for key in REQUIRED_KEYS:
        val = (body.values.get(key) or "").strip()
        if not val:
            failures.append({"key": key, "reason": "missing"})
            continue
        setup_key = SETUP_KEYS_BY_KEY.get(key)
        validator_name = setup_key.validator if setup_key else None
        result = live_validate(
            key, val,
            all_values=body.values,
            validator_name=validator_name,
        )
        if not result.valid:
            failures.append({"key": key, "reason": result.reason or "invalid"})

    if failures:
        raise HTTPException(
            status_code=400,
            detail={
                "message": "required keys missing or invalid",
                "failures": failures,
            },
        )

    path, backup = save_env(body.values, merge=body.merge)
    changed = list(refresh_environment())
    status = check_status()
    return {
        "ok":           True,
        "ready":        bool(status["ready"]),
        "changed":      sorted(changed),
        "env_path":     str(path),
        "backup_path":  str(backup) if backup else None,
    }


class SignOutRequest(BaseModel):
    """Optional confirmation token. Reserved for future use — today the
    UI confirms via a modal before calling this. Field kept so the
    contract is stable if we tighten the flow later."""

    confirm: bool = True


@router.post("/signout")
def post_signout(body: SignOutRequest) -> dict[str, Any]:
    """Wipe every Clarion-managed env key.

    Strategy: pass an empty value for every key the setup schema knows
    about — `save_env` interprets empty as "delete this key" — and then
    refresh os.environ so the gate flips back to setup-required without
    a server restart.

    Custom (non-schema) keys in `.env` are preserved. We don't touch
    Postgres creds, `RESEARCH_ALLOWED_HOSTS`, or anything else the user
    might have added manually — sign-out only clears identity + tokens.
    """
    if not body.confirm:
        raise HTTPException(status_code=400, detail="Sign-out not confirmed.")
    wipe = {k: "" for k in SETUP_KEYS_BY_KEY}
    path, backup = save_env(wipe, merge=True)
    changed = list(refresh_environment())
    return {
        "ok":          True,
        "ready":       False,
        "cleared":     sorted(changed),
        "env_path":    str(path),
        "backup_path": str(backup) if backup else None,
    }
