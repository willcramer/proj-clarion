"""RED metrics emitter — gives services real telemetry so KG entities have insights.

Without this, the KG entities are just dots: no latency, no error rate, no
request rate, so the entity processor has nothing to color them by. With
this, every Service in the plan's KG gets cumulative counters that look
realistic to Grafana/Mimir.

Emits (all observable, cumulative, exported every 30s alongside the entity gauges):
- `http_requests_total{service, namespace, status_class, method, customer}`
- `http_request_duration_seconds_sum{service, namespace, customer}`
- `http_request_duration_seconds_count{service, namespace, customer}`
- `clarion_business_revenue_total{store_id, channel_id, region_id, customer}`
- `clarion_business_orders_total{store_id, channel_id, region_id, customer}`

The values grow cumulatively: total = baseline_per_second * elapsed_seconds
since emitter start, with diurnal/weekly weighting. Deterministic per plan_id.
"""

from __future__ import annotations

import os
import random
import time
from typing import Any

from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.metrics import Meter, Observation
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics._internal.instrument import (
    Counter,
    Histogram,
    ObservableCounter,
    ObservableGauge,
    ObservableUpDownCounter,
    UpDownCounter,
)
from opentelemetry.sdk.metrics.export import (
    AggregationTemporality,
    PeriodicExportingMetricReader,
)
from opentelemetry.sdk.resources import Resource

from proj_clarion.generator.diurnal import composite_weight
from proj_clarion.schemas import EdgeType, KGNode, KnowledgeGraph, NodeType


_PREFER_CUMULATIVE = {
    Counter:                AggregationTemporality.CUMULATIVE,
    UpDownCounter:          AggregationTemporality.CUMULATIVE,
    Histogram:              AggregationTemporality.CUMULATIVE,
    ObservableCounter:      AggregationTemporality.CUMULATIVE,
    ObservableUpDownCounter: AggregationTemporality.CUMULATIVE,
    ObservableGauge:        AggregationTemporality.CUMULATIVE,
}


# ============================================================
# Per-service baselines (deterministic from plan_id + service)
# ============================================================

def _baseline_rps(rng: random.Random, service_id: str) -> float:
    """Requests-per-second baseline. Most services 5-30 rps; a few hot ones higher."""
    name = service_id.lower()
    if any(w in name for w in ("checkout", "payment", "cart", "auth")):
        return rng.uniform(40, 90)  # hot path
    if any(w in name for w in ("cdn", "edge", "gateway")):
        return rng.uniform(80, 200)  # very hot
    if any(w in name for w in ("internal", "background", "worker", "scheduler")):
        return rng.uniform(0.5, 3)  # slow batch
    return rng.uniform(5, 30)  # mid


def _baseline_error_rate(service_id: str) -> float:
    """Background error rate."""
    name = service_id.lower()
    if any(w in name for w in ("integration", "bridge", "edi", "carrier")):
        return 0.020  # external integrations are flaky
    if any(w in name for w in ("payment", "auth")):
        return 0.012
    return 0.005


def _baseline_latency_seconds(rng: random.Random, service_id: str) -> float:
    name = service_id.lower()
    if any(w in name for w in ("integration", "bridge", "external", "carrier", "edi")):
        return rng.uniform(0.4, 1.2)  # slow
    if any(w in name for w in ("payment", "checkout")):
        return rng.uniform(0.15, 0.4)
    return rng.uniform(0.03, 0.12)


# ============================================================
# Emitter
# ============================================================

class RedEmitter:
    """Builds the RED instruments and registers callbacks on a shared meter.

    Designed to be installed by EntityEmitter so both share the same Resource +
    MeterProvider — one process, one OTLP pipeline.
    """

    def __init__(
        self,
        kg: KnowledgeGraph,
        plan_id: str,
        customer: str,
        *,
        env: str = "",
        site: str = "",
        diurnal_pattern: str = "retail_us",
        weekly_pattern: str = "weekend_heavy",
    ) -> None:
        self._kg = kg
        self._plan_id = plan_id
        self._customer = customer
        # `env` / `site` get stamped onto every observation (see
        # `_common_attrs`) — Mimir promotes asserts.env from the Resource
        # but NOT asserts.site, so without observation-level fallback the
        # affinity metrics (clarion_service_database_affinity, etc.) lack
        # scope, and METRICS relations like Service USES Database render
        # zero edges in the entity graph.
        self._env = env
        self._site = site
        self._diurnal = diurnal_pattern
        self._weekly = weekly_pattern
        self._started_at = time.time()

        # Pre-compute per-service baselines (RNG seeded by plan_id+service)
        rng = random.Random(hash((plan_id, "red")) & 0xFFFFFFFF)
        self._services = [n for n in kg.nodes if n.technical_subtype == "service"]
        self._service_state: dict[str, dict[str, float]] = {}
        for svc in self._services:
            sid = svc.node_id
            ns = self._namespace_for(svc)
            self._service_state[sid] = {
                "rps":           _baseline_rps(rng, sid),
                "err_rate":      _baseline_error_rate(sid),
                "latency_avg":   _baseline_latency_seconds(rng, sid),
                "namespace":     ns,
            }

        # Business-entity emitters — generalized across ALL leaf-level
        # business subtypes so non-retail verticals also produce revenue
        # and order observations. Previously this loop only matched
        # store/fulfillment_center, so airlines / healthcare / SaaS /
        # manufacturing emitted ZERO business metrics, and every
        # downstream dashboard panel rendered empty.
        #
        # Each emitter records:
        #   - `entity_id`     — the KG node_id ("nyc-flagship", "passenger-mainline")
        #   - `entity_kind`   — the business_subtype ("store", "business_unit", ...)
        #   - parent IDs (channel / region) for cross-tabulations
        #   - revenue_per_sec / orders_per_sec random baselines
        #
        # At observation time, _emit_revenue / _emit_orders writes BOTH a
        # long-form `clarion_<subtype>_id` label (which the model rules
        # consume to materialize entities) AND a short alias `<subtype>`
        # (which dashboard queries group by). Long form drives KG
        # entity placement; short form drives chart legibility.
        _LEAF_BUSINESS_SUBTYPES = {
            "store", "fulfillment_center", "business_unit",
            "brand", "partner_program", "product_line",
        }
        self._business_emitters: list[dict[str, Any]] = []
        rng_biz = random.Random(hash((plan_id, "biz")) & 0xFFFFFFFF)
        for n in kg.nodes:
            if n.business_subtype not in _LEAF_BUSINESS_SUBTYPES:
                continue
            parents = n.attributes.get("_clarion_parents") or {}
            self._business_emitters.append({
                "entity_id":   n.node_id,
                "entity_kind": n.business_subtype,
                "channel_id":  parents.get("channel", ""),
                "region_id":   parents.get("region", ""),
                "revenue_per_sec":  rng_biz.uniform(0.3, 2.0),
                "orders_per_sec":   rng_biz.uniform(0.05, 0.4),
            })

        # Channel→Service affinity edges from KG `serves` edges. Each pair
        # gets one observation on `clarion_channel_service_affinity`, which is
        # the co-labeled series the SERVES model rule needs (per Grafana
        # Assistant: METRICS join requires both labels on the same series).
        # We strip the svc- prefix on `service` because the built-in Service
        # entity is keyed off the unprefixed value.
        nodes_by_id = {n.node_id: n for n in kg.nodes}
        self._channel_service_pairs: list[dict[str, str]] = []
        for e in kg.edges:
            if e.edge_type.value != EdgeType.SERVES.value:
                continue
            start = nodes_by_id.get(e.from_node_id)
            end = nodes_by_id.get(e.to_node_id)
            if start is None or end is None:
                continue
            if start.business_subtype != "channel" or end.technical_subtype != "service":
                continue
            ns = end.attributes.get("namespace_id", "").removeprefix("ns-") or "default"
            self._channel_service_pairs.append({
                "clarion_channel_id":  start.node_id,
                "clarion_service_id":  end.node_id,
                "service":             end.node_id.removeprefix("svc-"),
                "namespace":           ns,
            })

        # Service→Database affinity. The planner emits `depends_on` edges
        # from services to backing databases (commerce-checkout → db-orders,
        # inventory → db-inventory, etc). Without a co-labeled metric, the
        # custom-model `Service USES Database` relation has nothing to join
        # on, and the user sees floating DBs in the KG visualization.
        # Mirror of the channel-service affinity pattern above.
        self._service_db_pairs: list[dict[str, str]] = []
        for e in kg.edges:
            if e.edge_type.value != EdgeType.DEPENDS_ON.value:
                continue
            src = nodes_by_id.get(e.from_node_id)
            dst = nodes_by_id.get(e.to_node_id)
            if src is None or dst is None:
                continue
            if src.technical_subtype != "service":
                continue
            # Database can be either a true `database` subtype or any node
            # whose `kind` attribute says so (expand.py uses both shapes).
            is_db = (
                dst.technical_subtype == "database"
                or dst.attributes.get("kind") == "database"
            )
            if not is_db:
                continue
            ns = src.attributes.get("namespace_id", "").removeprefix("ns-") or "default"
            self._service_db_pairs.append({
                "service":              src.node_id.removeprefix("svc-"),
                "namespace":            ns,
                "clarion_service_id":   src.node_id,
                "clarion_database_id":  dst.node_id,
            })

        # k8s node entities — emit `kube_node_info` so the custom KubeCluster
        # entity rule fires (it queries `kube_node_info{cluster!=""}`). The
        # `kind` attribute may be either "node" (legacy) or "kubenode" (after
        # expand.py refactor) — both represent k8s worker nodes.
        self._kube_nodes: list[dict[str, str]] = []
        for n in kg.nodes:
            if n.attributes.get("kind") not in ("node", "kubenode"):
                continue
            cluster = n.attributes.get("cluster_id", "")
            if not cluster:
                continue
            self._kube_nodes.append({
                "node":    n.node_id,
                "cluster": cluster,
            })

    @staticmethod
    def _namespace_for(svc: KGNode) -> str:
        return svc.attributes.get("namespace_id", "").removeprefix("ns-") or "default"

    def install(self, meter: Meter, base_resource: Resource | None = None) -> None:
        """Register RED instruments + callbacks.

        Per-service metrics (http_requests_total, latency) are emitted from
        per-service MeterProviders so each service has its own `target_info`
        series, materialising 53 distinct built-in Service entities. The
        shared `meter` keeps cross-service stuff: business revenue/orders,
        channel-service affinity, kube_node_info.

        `base_resource` is the shared emitter resource we clone per service
        (overriding service.name + service.namespace).
        """
        # ----- Per-service providers (53 of them) -----
        self._service_providers: list[MeterProvider] = []
        if base_resource is None:
            # Fallback: only emit on the shared meter (legacy behavior, won't
            # produce per-service Service entities)
            self._install_per_service_metrics_on_shared(meter)
        else:
            base_attrs = dict(base_resource.attributes)
            for sid, st in self._service_state.items():
                svc_unprefixed = sid.removeprefix("svc-")
                svc_attrs = {
                    **base_attrs,
                    # Override identity for this service's metrics
                    "service.name":      svc_unprefixed,
                    "service.namespace": st["namespace"],
                }
                svc_resource = Resource.create(svc_attrs)
                reader = PeriodicExportingMetricReader(
                    OTLPMetricExporter(preferred_temporality=_PREFER_CUMULATIVE),
                    export_interval_millis=30_000,
                )
                provider = MeterProvider(resource=svc_resource, metric_readers=[reader])
                svc_meter = provider.get_meter("proj-clarion.kg_publish.red")
                # bind the service id into closures so each meter only emits its own
                self._install_one_service_metrics(svc_meter, sid)
                self._service_providers.append(provider)

        # ----- Shared metrics on the shared meter -----
        # Note: OTLP→Prometheus translation appends `unit` into the metric name,
        # so we encode the unit in the name and leave `unit=` blank to keep the
        # series queryable as `clarion_business_revenue_total{...}`.
        meter.create_observable_counter(
            "clarion_business_revenue_usd_total",
            description="Cumulative revenue (USD) per store/channel/region",
            callbacks=[self._emit_revenue],
        )
        meter.create_observable_counter(
            "clarion_business_orders_total",
            description="Cumulative order count per store/channel/region",
            callbacks=[self._emit_orders],
        )
        # Co-labeled join metric: one series per (channel, service) pair so the
        # Channel SERVES Service METRICS join in model rules fires.
        meter.create_observable_gauge(
            "clarion_channel_service_affinity",
            description="Presence gauge for each Channel-Service pair from the plan KG",
            callbacks=[self._emit_channel_service_affinity],
        )
        # Same shape, for Service USES Database. Driven by `depends_on`
        # edges in the planner output (commerce-checkout → db-orders, etc).
        meter.create_observable_gauge(
            "clarion_service_database_affinity",
            description="Presence gauge for each Service-Database depends_on pair from the plan KG",
            callbacks=[self._emit_service_database_affinity],
        )
        # Customer-level KPIs (top of the hierarchy)
        meter.create_observable_counter(
            "clarion_customer_revenue_usd_total",
            description="Customer-wide revenue (sum across all stores/channels)",
            callbacks=[self._emit_customer_revenue],
        )
        meter.create_observable_counter(
            "clarion_customer_orders_total",
            description="Customer-wide order count",
            callbacks=[self._emit_customer_orders],
        )
        meter.create_observable_gauge(
            "clarion_customer_health_score",
            description="Aggregate health score 0-100 for the customer",
            callbacks=[self._emit_customer_health],
        )
        meter.create_observable_gauge(
            "clarion_customer_active_stores",
            description="Number of currently active stores for the customer",
            callbacks=[self._emit_customer_active_stores],
        )
        # Materialise built-in KubeCluster entities by emitting kube_node_info
        # for each synthetic k8s node (the built-in rule queries this metric).
        meter.create_observable_gauge(
            "kube_node_info",
            description="Synthetic kube_node_info so built-in KubeCluster entity fires",
            callbacks=[self._emit_kube_node_info],
        )

    # ----- callbacks (cumulative, computed from elapsed time) -----

    def _elapsed_weighted_seconds(self) -> float:
        """Elapsed seconds since emitter start, scaled by current diurnal weight.

        Cumulative growth uses *unweighted* elapsed for monotonicity — but
        we apply a current-weight multiplier so the rate during peak hours
        looks higher than off-hours when graphed. Simplification: we grow
        linearly here; PromQL `rate()` over a recent window naturally shows
        the hourly variation.
        """
        return time.time() - self._started_at

    def _install_one_service_metrics(self, meter: Meter, service_id: str) -> None:
        """Install RED instruments scoped to ONE service. Closures capture
        service_id so the callbacks only emit data for that service.
        """
        meter.create_observable_counter(
            "http_requests_total",
            unit="{request}",
            description="HTTP request count per status class",
            callbacks=[lambda opts, sid=service_id: self._emit_http_for(sid)],
        )
        meter.create_observable_counter(
            "http_request_duration_seconds_sum",
            unit="s",
            description="Cumulative request duration sum",
            callbacks=[lambda opts, sid=service_id: self._emit_latency_sum_for(sid)],
        )
        meter.create_observable_counter(
            "http_request_duration_seconds_count",
            unit="{request}",
            description="Cumulative request count for the duration metric",
            callbacks=[lambda opts, sid=service_id: self._emit_latency_count_for(sid)],
        )

    def _install_per_service_metrics_on_shared(self, meter: Meter) -> None:
        """Legacy fallback: emit all services' metrics from the shared meter.
        Used only if no base_resource is supplied to install(). Won't create
        per-service Service entities.
        """
        meter.create_observable_counter(
            "http_requests_total",
            unit="{request}",
            description="Cumulative HTTP request count per service per status class",
            callbacks=[self._emit_http_requests],
        )
        meter.create_observable_counter(
            "http_request_duration_seconds_sum",
            unit="s",
            description="Cumulative request duration sum per service",
            callbacks=[self._emit_latency_sum],
        )
        meter.create_observable_counter(
            "http_request_duration_seconds_count",
            unit="{request}",
            description="Cumulative request count for the duration metric",
            callbacks=[self._emit_latency_count],
        )

    def _emit_http_for(self, sid: str) -> list[Observation]:
        """Per-service callback: emit only this service's http_requests_total."""
        from datetime import UTC, datetime
        st = self._service_state[sid]
        elapsed = self._elapsed_weighted_seconds()
        weight = composite_weight(self._diurnal, self._weekly, datetime.now(UTC))
        total_reqs = st["rps"] * weight * elapsed
        err_total = total_reqs * st["err_rate"]
        ok_total = total_reqs - err_total
        common = self._common_attrs()
        # Note: NO `service` attr here — service.name on the Resource carries it
        # (becomes service_name label in Mimir).
        return [
            Observation(value=int(ok_total), attributes={
                **common, "method": "GET", "status_class": "2xx",
            }),
            Observation(value=int(err_total * 0.7), attributes={
                **common, "method": "GET", "status_class": "5xx",
            }),
            Observation(value=int(err_total * 0.3), attributes={
                **common, "method": "GET", "status_class": "4xx",
            }),
        ]

    def _emit_latency_sum_for(self, sid: str) -> list[Observation]:
        from datetime import UTC, datetime
        st = self._service_state[sid]
        elapsed = self._elapsed_weighted_seconds()
        weight = composite_weight(self._diurnal, self._weekly, datetime.now(UTC))
        total_reqs = st["rps"] * weight * elapsed
        return [Observation(
            value=total_reqs * st["latency_avg"],
            attributes=self._common_attrs(),
        )]

    def _emit_latency_count_for(self, sid: str) -> list[Observation]:
        from datetime import UTC, datetime
        st = self._service_state[sid]
        elapsed = self._elapsed_weighted_seconds()
        weight = composite_weight(self._diurnal, self._weekly, datetime.now(UTC))
        total_reqs = st["rps"] * weight * elapsed
        return [Observation(
            value=int(total_reqs),
            attributes=self._common_attrs(),
        )]

    def shutdown(self) -> None:
        """Tear down per-service providers."""
        for p in getattr(self, "_service_providers", []):
            try:
                p.shutdown()
            except Exception:  # noqa: BLE001
                pass

    def _common_attrs(self) -> dict[str, str]:
        """Per-metric attrs we put on every observation.

        History:
        - v0.6.0 set asserts_env, asserts_site, clarion_customer, clarion_plan_id here.
        - v0.6.4 emptied this entirely after a `prod;prod` doubling
          regression — Asserts relabel rules concatenated when both
          Resource and observation set `asserts.env=prod`.
        - v0.7.x: re-added ONLY `clarion_customer`. We trusted Mimir's
          OTLP→Prom translation to promote `asserts.env`/`asserts.site`
          from the Resource to series labels.
        - 2026-05 (this fix): GS investigation of the Sentinel scope
          confirmed Mimir promotes `asserts.env` but NOT `asserts.site`
          for non-`target_info` series — the Pod/affinity metrics
          materialised without `asserts_site`, so cross-tier relations
          (Service↔Pod, Service↔Database) couldn't fire across the
          (env, site) scope mismatch with the Service entity (built
          from `target_info`, which DOES carry both).
          Fix: stamp both as observation attrs. The historical doubling
          only triggers when Resource and observation values DIFFER;
          here we source both from the same `clarion_resource()` inputs
          so values match and Mimir doesn't `;`-merge.
        """
        attrs: dict[str, str] = {"clarion_customer": self._customer}
        if self._env:
            attrs["asserts_env"] = self._env
        if self._site:
            attrs["asserts_site"] = self._site
        return attrs

    def _emit_http_requests(self, _options: Any) -> list[Observation]:
        from datetime import UTC, datetime
        elapsed = self._elapsed_weighted_seconds()
        weight = composite_weight(self._diurnal, self._weekly, datetime.now(UTC))
        observations: list[Observation] = []
        for sid, st in self._service_state.items():
            total_reqs = st["rps"] * weight * elapsed
            err_total = total_reqs * st["err_rate"]
            ok_total = total_reqs - err_total
            base_attrs = {
                **self._common_attrs(),
                "service":          sid.removeprefix("svc-"),
                "namespace":        st["namespace"],
                "method":           "GET",
            }
            observations.append(Observation(
                value=int(ok_total),
                attributes={**base_attrs, "status_class": "2xx"},
            ))
            observations.append(Observation(
                value=int(err_total * 0.7),
                attributes={**base_attrs, "status_class": "5xx"},
            ))
            observations.append(Observation(
                value=int(err_total * 0.3),
                attributes={**base_attrs, "status_class": "4xx"},
            ))
        return observations

    def _emit_latency_sum(self, _options: Any) -> list[Observation]:
        from datetime import UTC, datetime
        elapsed = self._elapsed_weighted_seconds()
        weight = composite_weight(self._diurnal, self._weekly, datetime.now(UTC))
        common = self._common_attrs()
        observations: list[Observation] = []
        for sid, st in self._service_state.items():
            total_reqs = st["rps"] * weight * elapsed
            total_seconds = total_reqs * st["latency_avg"]
            observations.append(Observation(
                value=total_seconds,
                attributes={
                    **common,
                    "service":   sid.removeprefix("svc-"),
                    "namespace": st["namespace"],
                },
            ))
        return observations

    def _emit_latency_count(self, _options: Any) -> list[Observation]:
        from datetime import UTC, datetime
        elapsed = self._elapsed_weighted_seconds()
        weight = composite_weight(self._diurnal, self._weekly, datetime.now(UTC))
        common = self._common_attrs()
        observations: list[Observation] = []
        for sid, st in self._service_state.items():
            total_reqs = st["rps"] * weight * elapsed
            observations.append(Observation(
                value=int(total_reqs),
                attributes={
                    **common,
                    "service":   sid.removeprefix("svc-"),
                    "namespace": st["namespace"],
                },
            ))
        return observations

    def _entity_labels(self, b: dict[str, Any]) -> dict[str, str]:
        """Build the per-entity label dict for revenue/orders observations.

        Writes BOTH:
          - `clarion_<entity_kind>_id` (long form) — model rules use this
            to materialize Asserts entities (the entity type's `name`
            label points here)
          - `<entity_kind>` (short form) — dashboard PromQL queries group
            by these because their legends + titles read cleanly without
            the prefix
        Plus the parent channel/region pair on both forms so cross-
        tabulations (Revenue by Region, Revenue Trend by Channel) work
        regardless of which leaf subtype the entity is.
        """
        kind = b["entity_kind"]
        eid = b["entity_id"]
        ch = b["channel_id"]
        rg = b["region_id"]
        return {
            f"clarion_{kind}_id": eid,
            kind:                  eid,
            "clarion_channel_id":  ch,
            "channel":             ch,
            "clarion_region_id":   rg,
            "region":              rg,
            "clarion_entity_kind": kind,
        }

    def _emit_revenue(self, _options: Any) -> list[Observation]:
        from datetime import UTC, datetime
        elapsed = self._elapsed_weighted_seconds()
        weight = composite_weight(self._diurnal, self._weekly, datetime.now(UTC))
        common = self._common_attrs()
        return [
            Observation(
                value=b["revenue_per_sec"] * weight * elapsed,
                attributes={**common, **self._entity_labels(b)},
            )
            for b in self._business_emitters
        ]

    def _emit_channel_service_affinity(self, _options: Any) -> list[Observation]:
        common = self._common_attrs()
        return [
            Observation(value=1, attributes={**common, **pair})
            for pair in self._channel_service_pairs
        ]

    def _emit_service_database_affinity(self, _options: Any) -> list[Observation]:
        """One observation per (service, database) `depends_on` pair the
        planner produced. Drives the model's `Service USES Database`
        relation; without this, central databases (db-orders, db-inventory,
        etc.) appear floating in the KG view."""
        common = self._common_attrs()
        return [
            Observation(value=1, attributes={**common, **pair})
            for pair in self._service_db_pairs
        ]

    # ----- Customer-level KPIs -----

    def _emit_customer_revenue(self, _options: Any) -> list[Observation]:
        from datetime import UTC, datetime
        elapsed = self._elapsed_weighted_seconds()
        weight = composite_weight(self._diurnal, self._weekly, datetime.now(UTC))
        total_per_sec = sum(b["revenue_per_sec"] for b in self._business_emitters)
        return [Observation(
            value=total_per_sec * weight * elapsed,
            attributes=self._common_attrs(),
        )]

    def _emit_customer_orders(self, _options: Any) -> list[Observation]:
        from datetime import UTC, datetime
        elapsed = self._elapsed_weighted_seconds()
        weight = composite_weight(self._diurnal, self._weekly, datetime.now(UTC))
        total_per_sec = sum(b["orders_per_sec"] for b in self._business_emitters)
        return [Observation(
            value=int(total_per_sec * weight * elapsed),
            attributes=self._common_attrs(),
        )]

    def _emit_customer_health(self, _options: Any) -> list[Observation]:
        """Synthetic health score 0-100. Wobbles slowly around 92, occasional dips."""
        import math, time as _t
        # Base 92, slow sine wave for variety, deterministic per plan
        t = _t.time()
        score = 92 + 4 * math.sin(t / 600) - 2 * math.sin(t / 137)
        return [Observation(
            value=round(max(0, min(100, score)), 2),
            attributes=self._common_attrs(),
        )]

    def _emit_customer_active_stores(self, _options: Any) -> list[Observation]:
        """Count of active business entities reporting telemetry. Metric
        name kept as `_active_stores` for backward compatibility with the
        acme_retail-7c reference dashboard, but the value now reflects ALL
        leaf business entities (stores for retail, business_units for
        non-retail). The command-center dashboard uses
        `count(count by(<primary>) (clarion_entity_info{...}))` for
        non-retail verticals where this name reads weird; this gauge is
        still useful as a single-tile retail KPI."""
        return [Observation(
            value=len(self._business_emitters),
            attributes=self._common_attrs(),
        )]

    def _emit_kube_node_info(self, _options: Any) -> list[Observation]:
        common = self._common_attrs()
        return [
            Observation(value=1, attributes={**common, **kn})
            for kn in self._kube_nodes
        ]

    def _emit_orders(self, _options: Any) -> list[Observation]:
        from datetime import UTC, datetime
        elapsed = self._elapsed_weighted_seconds()
        weight = composite_weight(self._diurnal, self._weekly, datetime.now(UTC))
        common = self._common_attrs()
        return [
            Observation(
                value=int(b["orders_per_sec"] * weight * elapsed),
                attributes={**common, **self._entity_labels(b)},
            )
            for b in self._business_emitters
        ]
