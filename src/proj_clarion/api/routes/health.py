"""Health + environment status endpoints — UI sidebar reads these."""

from __future__ import annotations

import os

from fastapi import APIRouter

from proj_clarion.observability.otlp import (
    clarion_env,
    clarion_site,
    otlp_endpoint,
    using_alloy_hop,
)

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health")
def health() -> dict[str, str]:
    """Liveness probe."""
    return {"status": "ok", "service": "proj-clarion-api"}


@router.get("/env")
def env_status() -> dict[str, object]:
    """Mirror what `proj-clarion check env` reports, in JSON for the UI banner.

    Read-only summary; deliberately omits secrets like
    `OTEL_EXPORTER_OTLP_HEADERS` and `GRAFANA_CLOUD_OTLP_AUTH`.
    """
    return {
        "otlp_endpoint": otlp_endpoint(),
        "asserts_env":   clarion_env(),
        "asserts_site":  clarion_site(),
        "mode":          "alloy" if using_alloy_hop() else (
            "cloud-direct" if otlp_endpoint() else "unset"
        ),
        "cloud_auth_present": bool(
            os.environ.get("GRAFANA_CLOUD_OTLP_AUTH")
            or os.environ.get("OTEL_EXPORTER_OTLP_HEADERS")
        ),
        "anthropic_key_present": bool(os.environ.get("ANTHROPIC_API_KEY")),
        "sigil_endpoint":        os.environ.get("SIGIL_ENDPOINT", "") or None,
    }
