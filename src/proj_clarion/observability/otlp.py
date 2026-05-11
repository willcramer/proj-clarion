"""Shared OTel/OTLP bootstrap.

Three sites in the codebase build OTel providers (`observability.init_telemetry`,
`kg_publish.emitter.EntityEmitter`, `livetail.emitter.LiveTailLogEmitter`).
They all need the same Resource attribute conventions and the same OTLP
endpoint discovery rules. Before this module existed, each site copied the
shape and they drifted independently — three places to update if a label
convention changed.

This module is the single source of truth:

  - `clarion_resource(...)` — build a Resource with the canonical
    `service.*`, `deployment.environment`, `asserts.*`, and `clarion.*`
    attributes any Clarion provider should carry.

  - `otlp_endpoint()` / `otlp_logs_endpoint()` / `otlp_metrics_endpoint()` /
    `otlp_traces_endpoint()` — read `OTEL_EXPORTER_OTLP_ENDPOINT` and
    return the per-signal endpoint with the right path suffix. Returns
    None when unset (callers fall back to console exporters or skip).

  - `clarion_env()` / `clarion_site()` — read `CLARION_ASSERTS_ENV` /
    `CLARION_ASSERTS_SITE` with the v0.5 defaults ("prod" / "demo").

Add new shared helpers here rather than inlining them per-site.
"""

from __future__ import annotations

import os

from opentelemetry.sdk.resources import Resource

# Default values for asserts.* attributes when env isn't set. Match the
# defaults in compose.yaml's environment block and .env.example.
_DEFAULT_ENV = "prod"
_DEFAULT_SITE = "demo"


def clarion_env() -> str:
    """asserts.env / deployment.environment value."""
    return os.environ.get("CLARION_ASSERTS_ENV", _DEFAULT_ENV)


def clarion_site() -> str:
    """asserts.site value."""
    return os.environ.get("CLARION_ASSERTS_SITE", _DEFAULT_SITE)


def otlp_endpoint() -> str | None:
    """Base OTLP endpoint (no /v1/* suffix). None if unset."""
    raw = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip()
    return raw.rstrip("/") if raw else None


def otlp_logs_endpoint() -> str | None:
    """Per-signal logs endpoint. None if base is unset."""
    base = otlp_endpoint()
    return f"{base}/v1/logs" if base else None


def otlp_metrics_endpoint() -> str | None:
    """Per-signal metrics endpoint. None if base is unset."""
    base = otlp_endpoint()
    return f"{base}/v1/metrics" if base else None


def otlp_traces_endpoint() -> str | None:
    """Per-signal traces endpoint. None if base is unset."""
    base = otlp_endpoint()
    return f"{base}/v1/traces" if base else None


def using_alloy_hop() -> bool:
    """Best-effort guess: are we routing through a local Alloy?

    Returns True when OTEL_EXPORTER_OTLP_ENDPOINT points at localhost or
    127.0.0.1 — matches the Mode-A convention in .env.example.
    """
    base = otlp_endpoint() or ""
    return "localhost" in base or "127.0.0.1" in base


def clarion_resource(
    *,
    service_name: str,
    service_version: str = "0.5.0",
    plan_id: str | None = None,
    customer: str | None = None,
    env: str | None = None,
    site: str | None = None,
    extra: dict[str, str] | None = None,
) -> Resource:
    """Build the canonical Resource for a Clarion OTel provider.

    Attributes set:
      - service.name / service.namespace / service.version
      - deployment.environment / asserts.env / asserts.site
      - clarion.plan_id  (if plan_id given)
      - clarion.customer (if customer given)
      - any keys from `extra`, which override anything above

    `env` and `site` override the env-var defaults when supplied — used
    by the emitter CLIs to pin asserts.env to the customer slug so the
    Asserts entity-graph "env" filter naturally separates customers
    (e.g. env=bluesky_airlines, env=acme_retail) instead of every demo
    living in `env=prod` together.

    Adopt this from `EntityEmitter`, `LiveTailLogEmitter`, and
    `init_telemetry()` — every Clarion provider's Resource must come through
    here, not be hand-rolled.
    """
    env_value = env if env is not None else clarion_env()
    site_value = site if site is not None else clarion_site()

    attrs: dict[str, str] = {
        "service.name":           service_name,
        "service.namespace":      "proj-clarion",
        "service.version":        service_version,
        "deployment.environment": env_value,
        "asserts.env":            env_value,
        "asserts.site":           site_value,
    }
    if plan_id is not None:
        attrs["clarion.plan_id"] = plan_id
    if customer is not None:
        attrs["clarion.customer"] = customer
    if extra:
        attrs.update(extra)

    return Resource.create(attrs)


__all__ = [
    "clarion_env",
    "clarion_resource",
    "clarion_site",
    "otlp_endpoint",
    "otlp_logs_endpoint",
    "otlp_metrics_endpoint",
    "otlp_traces_endpoint",
    "using_alloy_hop",
]
