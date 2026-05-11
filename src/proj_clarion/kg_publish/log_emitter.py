"""OTLP log emitter — per-service LoggerProvider so logs land as
distinct Loki streams keyed by `service_name=svc-X`.

Why per-service: Grafana Cloud Loki only indexes a small set of stream
labels by default (`service_name`, `service_namespace`,
`deployment_environment`). Everything else lands as "structured metadata"
that's visible per-line but NOT queryable in a stream selector. Without a
per-service `service.name` resource attribute, all our logs would land in
ONE stream and Service-entity Log tabs (which query
`{service_name=...}`) would fail.

We keep ONE provider per service rather than one per log record because
OTel SDK doesn't allow per-record resource overrides cleanly. 53 providers
sharing the same OTLP exporter pipeline is fine at this scale.

Structured metadata still carries everything else (clarion_customer,
clarion_store_id, etc.) so a custom Loki config matching Store/Region/
Channel entities can filter on those.
"""

from __future__ import annotations

import logging as _logging
import os
import random
import threading
import time
from typing import Any

import structlog
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.sdk.resources import Resource

from proj_clarion.schemas import KGNode, KnowledgeGraph

_log = structlog.get_logger()


_INFO_TEMPLATES = [
    "request handled status=200 path=/{p} latency_ms={l}",
    "cache hit key={key} ttl_remaining_ms={l}",
    "downstream call to {svc} returned in {l}ms",
    "background task completed records={n}",
    "connection established remote={svc} pool_size={n}",
]
_WARN_TEMPLATES = [
    "slow query path=/{p} latency_ms={l}",
    "retry attempt={n} for upstream {svc}",
    "rate limit approaching client={key} bucket=80%",
    "circuit breaker half-open svc={svc}",
]
_ERROR_TEMPLATES = [
    "request failed status=500 path=/{p} reason={reason}",
    "downstream {svc} timeout after {l}ms",
    "deserialization error key={key} reason={reason}",
    "auth failure client={key} reason={reason}",
]
_REASONS = ["timeout", "connection_reset", "validation_error",
            "missing_field", "permission_denied", "schema_mismatch"]
_PATHS = ["api/v1/orders", "api/v1/cart", "internal/healthz",
          "api/v1/checkout", "api/v1/inventory", "graphql"]


class LogEmitter:
    """One LoggerProvider per service so each lands in its own Loki stream."""

    def __init__(
        self,
        kg: KnowledgeGraph,
        plan_id: str,
        customer: str,
        *,
        lines_per_service_per_tick: int = 3,
        interval_seconds: int = 30,
    ) -> None:
        self._kg = kg
        self._plan_id = plan_id
        self._customer = customer
        self._n_per = lines_per_service_per_tick
        self._interval = interval_seconds
        self._stop = False
        self._rng = random.Random(hash((plan_id, "logs")) & 0xFFFFFFFF)

        # Walk serves edges for parent context
        nodes_by_id = {n.node_id: n for n in kg.nodes}
        serves_to: dict[str, list[KGNode]] = {}
        for e in kg.edges:
            if e.edge_type.value != "serves":
                continue
            src = nodes_by_id.get(e.from_node_id)
            if src and src.business_subtype:
                serves_to.setdefault(e.to_node_id, []).append(src)

        # Per-service state: provider, logger, structured metadata to attach
        services = [n for n in kg.nodes if n.technical_subtype == "service"]
        self._svc_state: list[dict[str, Any]] = []
        for svc in services:
            ns = svc.attributes.get("namespace_id", "").removeprefix("ns-") or "default"
            parents = serves_to.get(svc.node_id, [])
            channel = next((p.node_id for p in parents
                            if p.business_subtype == "channel"), "")
            store = next((p.node_id for p in parents
                          if p.business_subtype in ("store", "fulfillment_center")), "")
            region = next((p.node_id for p in parents
                           if p.business_subtype == "region"), "")
            self._svc_state.append({
                "service":           svc.node_id.removeprefix("svc-"),
                "service_id":        svc.node_id,
                "namespace":         ns,
                "channel_id":        channel,
                "store_id":          store,
                "region_id":         region,
                "cluster":           svc.attributes.get("cluster_id", "")
                                      or "cluster-prod",
                # populated by install():
                "logger":            None,  # type: ignore[dict-item]
                "provider":          None,  # type: ignore[dict-item]
            })

    def install(self, base_resource: Resource) -> None:
        """Stand up one LoggerProvider per service. Resource per provider has
        the service-scoped service.name so Loki indexes a stream per service.
        """
        os.environ.setdefault(
            "OTEL_EXPORTER_OTLP_LOGS_TEMPORALITY_PREFERENCE", "cumulative"
        )
        # Pull non-service-overridable attrs from base — this carries the
        # customer-scoped `asserts.env` (= customer slug) and `asserts.site`
        # already set by EntityEmitter via clarion_resource(). DO NOT pull
        # those values from env vars here: the base is the source of truth,
        # and shadowing them with `clarion_env()` re-collapses every demo
        # back into env=prod, which defeats the whole "filter by customer
        # in the entity graph" goal.
        base_attrs = dict(base_resource.attributes)
        for st in self._svc_state:
            attrs = {
                **base_attrs,
                "service.name":      st["service_id"],
                "service.namespace": st["namespace"],
            }
            res = Resource.create(attrs)
            provider = LoggerProvider(resource=res)
            provider.add_log_record_processor(
                BatchLogRecordProcessor(OTLPLogExporter())
            )
            handler = LoggingHandler(level=_logging.INFO, logger_provider=provider)
            logger = _logging.getLogger(f"clarion.synth_logs.{st['service']}")
            logger.setLevel(_logging.DEBUG)
            logger.addHandler(handler)
            logger.propagate = False
            st["logger"] = logger
            st["provider"] = provider

        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()
        _log.info(
            "kg_log_emitter.start",
            services=len(self._svc_state),
            per_tick=self._n_per * len(self._svc_state),
            providers=len(self._svc_state),
        )

    def _loop(self) -> None:
        while not self._stop:
            self._emit_tick()
            for _ in range(self._interval):
                if self._stop:
                    return
                time.sleep(1)

    def _emit_tick(self) -> None:
        rng = self._rng
        for st in self._svc_state:
            for _ in range(self._n_per):
                roll = rng.random()
                if roll < 0.80:
                    tmpl, severity_name = rng.choice(_INFO_TEMPLATES), "INFO"
                    level = _logging.INFO
                elif roll < 0.95:
                    tmpl, severity_name = rng.choice(_WARN_TEMPLATES), "WARNING"
                    level = _logging.WARNING
                else:
                    tmpl, severity_name = rng.choice(_ERROR_TEMPLATES), "ERROR"
                    level = _logging.ERROR
                msg = tmpl.format(
                    p=rng.choice(_PATHS),
                    l=rng.randrange(5, 800),
                    n=rng.randrange(1, 50),
                    key=f"k_{rng.randrange(1000, 9999)}",
                    svc=rng.choice(self._svc_state)["service"],
                    reason=rng.choice(_REASONS),
                )
                # Structured metadata — per-record attrs (visible per line,
                # filterable via Loki | clarion_store_id="..." pipe syntax).
                # Do NOT set asserts_env/asserts_site here: they already live
                # on the Resource (see install() above) and Mimir/Loki merge
                # values from both sources with `;` separators when keys
                # overlap, putting these records in scope `(prod, demo;demo)`
                # instead of `(prod, demo)` — which breaks every relation
                # join in Cloud KG.
                extra = {
                    "clarion_customer":     self._customer,
                    "clarion_plan_id":      self._plan_id,
                    "clarion_service_id":   st["service_id"],
                    "clarion_store_id":     st["store_id"],
                    "clarion_channel_id":   st["channel_id"],
                    "clarion_region_id":    st["region_id"],
                    "clarion_kube_cluster": st["cluster"],
                    "service":              st["service"],
                    "namespace":            st["namespace"],
                }
                extra = {k: v for k, v in extra.items() if v}
                st["logger"].log(level, msg, extra=extra)

    def stop(self) -> None:
        self._stop = True
        for st in self._svc_state:
            provider = st.get("provider")
            if provider is not None:
                try:
                    provider.shutdown()
                except Exception as exc:  # noqa: BLE001
                    _log.warning("kg_log_emitter.shutdown.failed",
                                 service=st["service"], error=str(exc))
        _log.info("kg_log_emitter.stop")
