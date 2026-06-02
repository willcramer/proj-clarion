"""Telemetry token/scope preflight.

Answers one question for the SE setting up `.env`: *will every signal Clarion
emits actually reach my Grafana Cloud tenant?* For each signal it checks the
required env vars are present and well-shaped, names the exact Access-Policy
scope the token must carry, and — when `probe=True` — does a best-effort AUTH
probe (reusing the setup validators) so an authenticated-but-wrong token is
distinguished from a missing one. It NEVER writes to the tenant (no test
ingest), per the chosen depth.

Used by:
  - GET /api/setup/telemetry-preflight   (UI / wizard surface)
  - init_telemetry()                     (one structured WARNING per gap at boot)
  - `clarion doctor`                     (CLI table for .env setup)
"""

from __future__ import annotations

import os
from dataclasses import asdict, dataclass
from typing import Mapping


@dataclass(frozen=True)
class TelemetryCheck:
    signal: str            # metrics | logs | traces | profiles | sigil | grafana_cloud_token | anthropic
    ok: bool
    severity: str          # "ok" | "warn" | "error"
    detail: str            # human-readable status
    required_scope: str | None = None
    # Exact "do this" string when not ok (empty when ok).
    remediation: str = ""

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def _truthy(v: str | None) -> bool:
    return (v or "").strip().lower() in ("1", "true", "yes", "on")


def _has(env: Mapping[str, str], key: str) -> bool:
    return bool((env.get(key) or "").strip())


def telemetry_preflight(
    env: Mapping[str, str] | None = None, *, probe: bool = False,
) -> list[TelemetryCheck]:
    """Return one check per signal. `probe=True` adds a network AUTH probe
    for the Grafana Cloud token (no tenant writes)."""
    env = env if env is not None else os.environ
    checks: list[TelemetryCheck] = []

    # ── OTLP path → traces / metrics / logs ────────────────────────────
    otlp_endpoint = (env.get("OTEL_EXPORTER_OTLP_ENDPOINT") or "").strip()
    via_alloy = "localhost" in otlp_endpoint or "127.0.0.1" in otlp_endpoint
    otlp_headers = (env.get("OTEL_EXPORTER_OTLP_HEADERS") or "").strip()
    cloud_otlp_auth = (env.get("GRAFANA_CLOUD_OTLP_AUTH") or "").strip()

    if not otlp_endpoint:
        for sig in ("metrics", "logs", "traces"):
            checks.append(TelemetryCheck(
                signal=sig, ok=False, severity="error",
                detail="OTLP path not configured — this signal will not ship to Cloud.",
                required_scope=f"{sig}:write",
                remediation=(
                    "Set OTEL_EXPORTER_OTLP_ENDPOINT (Cloud → 'Send OpenTelemetry data', "
                    "or http://localhost:4318 for the Alloy hop) and the auth for it, "
                    f"with a token carrying {sig}:write."
                ),
            ))
    else:
        # Endpoint is set; auth lives either on Alloy (Mode A) or in the
        # OTLP headers (Mode B). Flag a direct-to-Cloud endpoint with no auth.
        auth_ok = via_alloy or bool(otlp_headers) or bool(cloud_otlp_auth)
        if via_alloy and not cloud_otlp_auth:
            # Alloy holds the creds — can't see them here, just remind.
            note = ("Routing through local Alloy — the Cloud token lives in Alloy "
                    "(GRAFANA_CLOUD_OTLP_AUTH / clarion-obs .env). ")
        else:
            note = ""
        for sig in ("metrics", "logs", "traces"):
            if auth_ok:
                checks.append(TelemetryCheck(
                    signal=sig, ok=True, severity="ok",
                    detail=f"OTLP configured. {note}Token must carry {sig}:write.".strip(),
                    required_scope=f"{sig}:write",
                ))
            else:
                checks.append(TelemetryCheck(
                    signal=sig, ok=False, severity="error",
                    detail="OTLP endpoint set but no auth — Cloud will 401 this signal.",
                    required_scope=f"{sig}:write",
                    remediation=(
                        "Direct-to-Cloud (Mode B): set OTEL_EXPORTER_OTLP_HEADERS="
                        "'Authorization=Basic <base64(stack_id:token)>' with a token carrying "
                        f"{sig}:write. Or via Alloy (Mode A): set GRAFANA_CLOUD_OTLP_AUTH."
                    ),
                ))

    # ── Profiles → Pyroscope (opt-in) ──────────────────────────────────
    if _truthy(env.get("PYROSCOPE_ENABLED")):
        checks.append(TelemetryCheck(
            signal="profiles", ok=True, severity="ok",
            detail=("Profiling enabled → clarion-obs collector forwards to Cloud Profiles. "
                    "That collector's token must carry profiles:write."),
            required_scope="profiles:write",
        ))
    else:
        checks.append(TelemetryCheck(
            signal="profiles", ok=True, severity="ok",
            detail="Profiling off (PYROSCOPE_ENABLED unset) — optional, no token needed.",
            required_scope="profiles:write",
        ))

    # ── Sigil → AI Observability generations ───────────────────────────
    sigil_endpoint = _has(env, "SIGIL_ENDPOINT")
    sigil_tenant = _has(env, "SIGIL_AUTH_TENANT_ID")
    sigil_token = _has(env, "SIGIL_AUTH_TOKEN")
    if sigil_endpoint and sigil_tenant and sigil_token:
        checks.append(TelemetryCheck(
            signal="sigil", ok=True, severity="ok",
            detail="Sigil configured. SIGIL_AUTH_TOKEN must carry sigil:write.",
            required_scope="sigil:write",
        ))
    else:
        missing = [k for k, present in (
            ("SIGIL_ENDPOINT", sigil_endpoint),
            ("SIGIL_AUTH_TENANT_ID", sigil_tenant),
            ("SIGIL_AUTH_TOKEN", sigil_token),
        ) if not present]
        checks.append(TelemetryCheck(
            signal="sigil", ok=False, severity="error",
            detail=("Sigil NOT fully configured — gen_ai spans reach Tempo, but the AI "
                    "Observability app's Conversations / Generations / evals stay EMPTY."),
            required_scope="sigil:write",
            remediation=(
                f"Set {', '.join(missing)} (Cloud → AI Observability → Configuration). "
                "SIGIL_AUTH_TENANT_ID routes data into your tenant; SIGIL_AUTH_TOKEN needs "
                "the sigil:write scope."
            ),
        ))

    # ── Anthropic (the agent can't run at all without it) ──────────────
    if _has(env, "ANTHROPIC_API_KEY"):
        checks.append(TelemetryCheck(
            signal="anthropic", ok=True, severity="ok",
            detail="ANTHROPIC_API_KEY set.",
        ))
    else:
        checks.append(TelemetryCheck(
            signal="anthropic", ok=False, severity="error",
            detail="ANTHROPIC_API_KEY unset — research/plan agents (and their gen_ai spans) can't run.",
            remediation="Set ANTHROPIC_API_KEY=sk-ant-… from the Anthropic console.",
        ))

    # ── Grafana Cloud control-plane token (provisioning) ───────────────
    gc_token = (env.get("GRAFANA_CLOUD_API_TOKEN") or "").strip()
    gc_stack = (env.get("GRAFANA_CLOUD_STACK_URL") or "").strip()
    if not gc_token or not gc_stack:
        miss = [k for k, p in (("GRAFANA_CLOUD_STACK_URL", gc_stack), ("GRAFANA_CLOUD_API_TOKEN", gc_token)) if not p]
        checks.append(TelemetryCheck(
            signal="grafana_cloud_token", ok=False, severity="error",
            detail="Grafana Cloud not configured — provisioning (dashboards/alerts/KG) can't run.",
            remediation=f"Set {', '.join(miss)} from your Cloud stack settings + Access Policies.",
        ))
    elif probe:
        # Reuse the live auth probe — confirms the token authenticates.
        try:
            from proj_clarion.api.setup.validators import _validate_grafana_cloud_token
            res = _validate_grafana_cloud_token(gc_token, all_values={"GRAFANA_CLOUD_STACK_URL": gc_stack})
            checks.append(TelemetryCheck(
                signal="grafana_cloud_token", ok=res.valid,
                severity="ok" if res.valid else "error",
                detail=res.reason or ("Token authenticates." if res.valid else "Token rejected."),
                remediation="" if res.valid else (res.hint or "Generate a fresh access-policy token."),
            ))
        except Exception as exc:  # noqa: BLE001 — preflight must never crash the caller
            checks.append(TelemetryCheck(
                signal="grafana_cloud_token", ok=True, severity="warn",
                detail=f"Token present; live probe skipped ({exc.__class__.__name__}).",
            ))
    else:
        checks.append(TelemetryCheck(
            signal="grafana_cloud_token", ok=True, severity="ok",
            detail="Grafana Cloud token present (auth not probed).",
        ))

    return checks


def missing_pieces(checks: list[TelemetryCheck]) -> list[TelemetryCheck]:
    """Just the non-ok checks (what the SE still needs to fix)."""
    return [c for c in checks if not c.ok]
