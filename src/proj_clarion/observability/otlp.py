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
    `CLARION_ASSERTS_SITE`. Used for Asserts entity scoping; the default
    matches `clarion_environment()` ("dev") so the Asserts KG scope and
    OTel deployment.environment stay in sync out of the box.

  - `clarion_environment()` — read `CLARION_ENVIRONMENT` (default "dev").
    This is the Clarion *deployment* stage (dev → staging → prod). Setting
    `CLARION_ENVIRONMENT=prod` should typically be matched by
    `CLARION_ASSERTS_ENV=prod` so KG entities, traces, metrics all roll up
    under the same value.

Add new shared helpers here rather than inlining them per-site.
"""

from __future__ import annotations

import os
import re

from opentelemetry.sdk.resources import Resource

# Default values for asserts.* attributes when env isn't set. Match the
# defaults in compose.yaml's environment block and .env.example.
#
# `_DEFAULT_ENV` and `_DEFAULT_DEPLOYMENT_ENVIRONMENT` are deliberately
# the same string so out of the box, KG entities and OTel signals
# share one env value. Promote both together by setting CLARION_ENVIRONMENT
# and CLARION_ASSERTS_ENV to "prod".
_DEFAULT_ENV = "dev"
_DEFAULT_SITE = "demo"
_DEFAULT_DEPLOYMENT_ENVIRONMENT = "dev"


def clarion_env() -> str:
    """asserts.env value — customer-scoped (e.g. carhartt-prod)."""
    return os.environ.get("CLARION_ASSERTS_ENV", _DEFAULT_ENV)


def clarion_site() -> str:
    """asserts.site value."""
    return os.environ.get("CLARION_ASSERTS_SITE", _DEFAULT_SITE)


def clarion_environment() -> str:
    """deployment.environment value for Clarion itself — `dev`, `staging`,
    `prod`. Distinct from `clarion_env()` (asserts scoping per customer).
    Defaults to `dev` until the deployment is promoted."""
    return os.environ.get("CLARION_ENVIRONMENT", _DEFAULT_DEPLOYMENT_ENVIRONMENT)


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
    deployment_env = clarion_environment()

    attrs: dict[str, str] = {
        "service.name":           service_name,
        "service.namespace":      "proj-clarion",
        "service.version":        service_version,
        # deployment.environment is Clarion's own stage (dev / staging / prod),
        # NOT the per-customer asserts scope. Splitting these is required by
        # OTel semantic conventions and lets a single shared Tempo separate
        # dev-from-prod traffic regardless of customer.
        "deployment.environment": deployment_env,
        "asserts.env":            env_value,
        "asserts.site":           site_value,
    }
    if plan_id is not None:
        attrs["clarion.plan_id"] = plan_id
    if customer is not None:
        attrs["clarion.customer"] = customer

    # k8s.node.name is the first tier of Grafana Cloud Application
    # Observability's host-identity match. Without one of (k8s.node.name |
    # host.name+cloud.provider | grafana.host.id) App O11y can't meter
    # host-hours and emits its "no host telemetry" warning. We synthesize a
    # stable per-customer node name so the demo's synthetic K8s fleet
    # registers as a host. NOTE: this is what makes App O11y start metering
    # host-hours. `extra` can override (e.g. per-node fan-out later).
    host_slug = re.sub(r"[^a-z0-9-]+", "-", (customer or "demo").lower()).strip("-") or "demo"
    attrs["k8s.node.name"] = f"clarion-{host_slug}-node-0"

    if extra:
        attrs.update(extra)

    return Resource.create(attrs)


__all__ = [
    "clarion_env",
    "clarion_environment",
    "clarion_resource",
    "clarion_site",
    "otlp_endpoint",
    "otlp_logs_endpoint",
    "otlp_metrics_endpoint",
    "otlp_traces_endpoint",
    "using_alloy_hop",
]
