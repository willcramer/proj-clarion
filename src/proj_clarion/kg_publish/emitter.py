"""OTLP entity emitter.

Architecture (post-refactor):
- ONE MeterProvider with one Resource (service.name=proj-clarion-kg-emitter).
  This means we emit ONE Service entity for the emitter itself — not one per
  KG entity. (The previous version emitted one Resource per entity, which
  polluted the Service entity type with hundreds of fake services.)
- ONE observable gauge `clarion_entity_info`. Per cycle we emit N
  Observations (one per KG entity), each carrying that entity's identity
  as Observation attributes. Mimir sees N unique series of
  `clarion_entity_info{...}` — exactly what the model rules' `definedBy`
  queries select on.
- Every observation carries `clarion_customer` so users can filter their
  Cloud KG to "this demo only" with a single label match.

Lifecycle: `EntityEmitter(plan, kg).start()` registers the provider and
schedules the export loop. `.stop()` flushes and shuts down. `run_forever()`
keeps the process alive until SIGINT.
"""

from __future__ import annotations

import os
import re
import signal
import time
from dataclasses import dataclass
from typing import Any
from uuid import UUID

import structlog
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.metrics import Observation
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

from proj_clarion.kg_publish.log_emitter import LogEmitter
from proj_clarion.kg_publish.red_emitter import RedEmitter
from proj_clarion.observability.otlp import clarion_resource
from proj_clarion.schemas import DemoPlan, KGNode, KnowledgeGraph, NodeType

_logger = structlog.get_logger()


_PREFER_CUMULATIVE = {
    Counter:                AggregationTemporality.CUMULATIVE,
    UpDownCounter:          AggregationTemporality.CUMULATIVE,
    Histogram:              AggregationTemporality.CUMULATIVE,
    ObservableCounter:      AggregationTemporality.CUMULATIVE,
    ObservableUpDownCounter: AggregationTemporality.CUMULATIVE,
    ObservableGauge:        AggregationTemporality.CUMULATIVE,
}


def _slug(name: str) -> str:
    """company name → URL/label-safe slug. 'AcmeRetail, Inc.' → 'acme_retail-inc'."""
    s = name.lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s or "unknown"


def _compute_store_cluster_map(
    kg: KnowledgeGraph,
) -> dict[str, dict[str, Any]]:
    """For each Store / FulfillmentCenter, look up its per-store cluster and
    the services running in that cluster.

    Returns:
        { store_node_id: { "cluster_id": str|None, "services": [{"service": str, "namespace": str}, ...] } }

    The Store→Service HOSTS PROPERTY_MATCH relation in the v0.6 KG model
    requires each Store's `clarion_entity_info` series to carry the
    `service` and `namespace` labels of every service it hosts. The
    `expand.py` pipeline builds one cluster per Store with its `store_id`
    attribute pointing back to the Store; we use that to discover what
    services to fan out across.

    Falls back to empty service list if no per-store cluster found —
    the Store entity is still emitted so it exists in Mimir; it just
    won't generate any HOSTS edges.
    """
    by_store_cluster: dict[str, str] = {}
    services_in_cluster: dict[str, list[dict[str, str]]] = {}

    for n in kg.nodes:
        if n.technical_subtype != "cluster":
            continue
        owner = n.attributes.get("store_id")
        if owner:
            # First match wins if a store somehow has two clusters
            by_store_cluster.setdefault(owner, n.node_id)
        services_in_cluster.setdefault(n.node_id, [])

    for n in kg.nodes:
        if n.technical_subtype != "service":
            continue
        cluster_id = n.attributes.get("cluster_id")
        if not cluster_id or cluster_id not in services_in_cluster:
            continue
        ns = (n.attributes.get("namespace_id", "") or "").removeprefix("ns-") or "default"
        services_in_cluster[cluster_id].append({
            "service":   n.node_id.removeprefix("svc-"),
            "namespace": ns,
        })

    out: dict[str, dict[str, Any]] = {}
    for store in kg.nodes:
        if store.business_subtype not in ("store", "fulfillment_center"):
            continue
        cluster_id = by_store_cluster.get(store.node_id)
        out[store.node_id] = {
            "cluster_id": cluster_id,
            "services":   services_in_cluster.get(cluster_id, []) if cluster_id else [],
        }
    return out


def _attach_pod_to_node(kg: KnowledgeGraph) -> None:
    """Assign every Pod to a deterministic Node within its cluster.

    Real K8s schedulers spread pods across nodes by various strategies;
    for demo purposes a stable sorted round-robin is convincing enough
    and survives re-runs (same plan_id → same pod placement).

    Writes the resolved node id to `pod.attributes["assigned_node_id"]`
    so `_observation_attrs` can read it without needing a separate map.
    Without this, the Custom Pod entity has no `node` label, and the
    built-in `Node HOSTS Pod` PROPERTY_MATCH relation has no join key.
    """
    nodes_in_cluster: dict[str, list[str]] = {}
    for n in kg.nodes:
        if n.attributes.get("kind") not in ("kubenode", "node"):
            continue
        cluster = n.attributes.get("cluster_id")
        if cluster:
            nodes_in_cluster.setdefault(cluster, []).append(n.node_id)
    # Sort so iteration order is stable across runs.
    for c in nodes_in_cluster:
        nodes_in_cluster[c].sort()

    # Pods, sorted by id so round-robin is stable across runs even if the
    # KG nodes list comes through in a different order.
    pods_by_cluster: dict[str, list[KGNode]] = {}
    for n in kg.nodes:
        if n.attributes.get("kind") != "pod":
            continue
        cluster = n.attributes.get("cluster_id")
        if not cluster:
            continue
        pods_by_cluster.setdefault(cluster, []).append(n)
    for c in pods_by_cluster:
        pods_by_cluster[c].sort(key=lambda p: p.node_id)

    for cluster, pods in pods_by_cluster.items():
        nodes = nodes_in_cluster.get(cluster, [])
        if not nodes:
            continue
        for i, pod in enumerate(pods):
            pod.attributes["assigned_node_id"] = nodes[i % len(nodes)]


def _attach_hierarchy(kg: KnowledgeGraph) -> None:
    """For each Store / FulfillmentCenter / Kiosk, walk `contains` edges
    upward to find region_id and channel_id and stash them under
    `attributes['_clarion_parents']` for the observation builder.
    """
    by_id = {n.node_id: n for n in kg.nodes}
    parents_of: dict[str, list[str]] = {}
    for e in kg.edges:
        if e.edge_type.value != "contains":
            continue
        parents_of.setdefault(e.to_node_id, []).append(e.from_node_id)

    def walk_up(nid: str, want_subtype: str, depth: int = 0) -> str | None:
        if depth > 6:
            return None
        for pid in parents_of.get(nid, []):
            p = by_id.get(pid)
            if p and p.business_subtype == want_subtype:
                return p.node_id
            if p:
                hit = walk_up(pid, want_subtype, depth + 1)
                if hit:
                    return hit
        return None

    for n in kg.nodes:
        if n.business_subtype not in ("store", "fulfillment_center", "kiosk"):
            continue
        parents: dict[str, str] = {}
        for want in ("region", "channel"):
            found = walk_up(n.node_id, want)
            if found:
                parents[want] = found
        if parents:
            existing = n.attributes.get("_clarion_parents", {})
            existing.update(parents)
            n.attributes["_clarion_parents"] = existing


def _entity_kind(node: KGNode) -> str:
    if node.attributes.get("kind"):
        return node.attributes["kind"]
    if node.business_subtype:
        return node.business_subtype
    if node.technical_subtype:
        return node.technical_subtype
    if node.agentic_subtype:
        return node.agentic_subtype
    return "unknown"


def _observation_attrs(
    node: KGNode,
    customer: str,
    *,
    env: str = "",
    site: str = "",
) -> dict[str, str]:
    """Per-entity attributes that go on the `clarion_entity_info` observation.

    These become Prometheus labels in Mimir (with dots/colons normalized to
    underscores). The model rules' `definedBy` queries select on these.

    NOTE on asserts_env / asserts_site (updated 2026-05):
    Cloud Mimir promotes `asserts.env` from the Resource to a label, but
    NOT `asserts.site` — confirmed via Grafana Assistant inspection of
    the Sentinel customer scope:
      `target_info`            had `asserts_env=sentinel, asserts_site=demo`
      `clarion_entity_info`    had `asserts_env=sentinel` only — no site.

    Different `(env, site)` scope between Pod (env=sentinel, site=∅) and
    Service (env=sentinel, site=demo) blocked every cross-tier join. We
    therefore set BOTH as observation attrs here, sourced from the same
    values the EntityEmitter passed to `clarion_resource()`. Same values
    on Resource and observation ⇒ Mimir doesn't `;`-merge (the historical
    "doubling" bug only fires when values differ).
    """
    attrs: dict[str, str] = {
        "clarion_customer":     customer,
        "clarion_entity_kind":  _entity_kind(node),
        "clarion_label":        node.label,
    }
    if env:
        attrs["asserts_env"] = env
    if site:
        attrs["asserts_site"] = site

    # Per-subtype identity
    if node.node_type == NodeType.BUSINESS_ENTITY and node.business_subtype:
        attrs[f"clarion_{node.business_subtype}_id"] = node.node_id
        # Stores/FCs surface their parent IDs so model-rule joins can fire
        for parent_kind, parent_id in (node.attributes.get("_clarion_parents") or {}).items():
            attrs[f"clarion_{parent_kind}_id"] = parent_id
    elif node.node_type == NodeType.TECHNICAL_RESOURCE:
        kind = node.attributes.get("kind", node.technical_subtype)
        if kind == "pod":
            attrs["clarion_pod_id"] = node.node_id
            attrs["clarion_service_id"] = node.attributes.get("service_id", "")
            svc_id = node.attributes.get("service_id", "")
            attrs["service"] = svc_id.removeprefix("svc-") if svc_id else ""
            attrs["namespace"] = (
                node.attributes.get("namespace_id", "") or ""
            ).removeprefix("ns-")
            attrs["clarion_kube_cluster"] = node.attributes.get("cluster_id", "") or ""
            store_id = node.attributes.get("store_id", "")
            if store_id:
                attrs["clarion_store_id"] = store_id
            # `node` label is the join key for the built-in `Node HOSTS Pod`
            # relation (PROPERTY_MATCH on Node.name == Pod.node). Set from
            # `_attach_pod_to_node`'s precomputed assignment.
            assigned = node.attributes.get("assigned_node_id", "")
            if assigned:
                attrs["node"] = assigned
        elif kind == "vm":
            attrs["clarion_vm_id"] = node.node_id
            store_id = node.attributes.get("store_id", "")
            if store_id:
                attrs["clarion_store_id"] = store_id
        elif kind == "kubenode":
            # k8s worker node — built-in `Node` entity type
            attrs["clarion_node_id"] = node.node_id
            attrs["clarion_kube_cluster"] = node.attributes.get("cluster_id", "") or ""
        elif kind == "loadbalancer":
            attrs["clarion_loadbalancer_id"] = node.node_id
            store_id = node.attributes.get("store_id", "")
            if store_id:
                attrs["clarion_store_id"] = store_id
        elif kind == "database":
            attrs["clarion_database_id"] = node.node_id
            store_id = node.attributes.get("store_id", "")
            if store_id:
                attrs["clarion_store_id"] = store_id
        elif kind == "topic":
            attrs["clarion_topic_id"] = node.node_id
            store_id = node.attributes.get("store_id", "")
            if store_id:
                attrs["clarion_store_id"] = store_id
        elif node.technical_subtype == "service":
            attrs["clarion_service_id"] = node.node_id
            attrs["service"] = node.node_id.removeprefix("svc-")
            attrs["clarion_kube_cluster"] = node.attributes.get("cluster_id", "") or ""
            store_id = node.attributes.get("store_id", "")
            if store_id:
                attrs["clarion_store_id"] = store_id
        elif node.technical_subtype == "cluster":
            attrs["clarion_kube_cluster"] = node.node_id
            store_id = node.attributes.get("store_id", "")
            if store_id:
                attrs["clarion_store_id"] = store_id
            # `clarion_cloud` and `clarion_cloud_region` let the Cloud
            # and CloudRegion entities (model rules sourced from these
            # labels) PROPERTY_MATCH-join their KubeClusters via the
            # `Cloud HOSTS CloudRegion HOSTS KubeCluster` chain — real
            # IT-arch topology where the cloud comes first, then the
            # region inside it. Set by `expand._assign_cloud_providers`.
            cloud = node.attributes.get("cloud", "")
            if cloud:
                attrs["clarion_cloud"] = cloud
            cloud_region = node.attributes.get("cloud_region", "")
            if cloud_region:
                attrs["clarion_cloud_region"] = cloud_region

    # Cluster context for tech-tier entities. Stores/FCs get their cluster
    # set from the precomputed map in _emit_all (more reliable than
    # node.attributes); the line below is a fallback for legacy planner
    # output that might already carry cluster_id directly on the Store.
    cluster_id = node.attributes.get("cluster_id")
    if cluster_id:
        attrs["clarion_kube_cluster"] = cluster_id
    if node.business_subtype in ("store", "fulfillment_center"):
        attrs["clarion_kube_cluster"] = (
            node.attributes.get("cluster_id")
            or node.attributes.get("cluster")
            or ""
        )

    # Drop empties (Mimir doesn't index empty labels)
    return {k: v for k, v in attrs.items() if v}


# ============================================================
# Emitter
# ============================================================

class EntityEmitter:
    """Single MeterProvider, single observable gauge, N observations per cycle.

    The single-Resource shape avoids polluting Grafana KG's Service entity
    type with one fake Service per emitted entity.
    """

    def __init__(
        self,
        plan: DemoPlan,
        kg: KnowledgeGraph,
        *,
        customer: str | None = None,
        env: str | None = None,
        site: str = "demo",
        export_interval_seconds: int = 30,
        emit_red: bool = True,
    ) -> None:
        self._plan_id = str(plan.plan_id)
        self._kg = kg
        self._customer = customer or _slug_for_plan(plan)
        # Default `asserts.env` to the customer slug so the Asserts
        # entity-graph "env" filter naturally separates demos. Each
        # customer's entities live in their own scope rather than every
        # demo collapsing into env=prod together. CLI `--env <value>`
        # overrides this for unusual cases (multi-environment demos).
        self._env = env or self._customer
        self._site = site
        self._export_interval_ms = export_interval_seconds * 1000
        self._provider: MeterProvider | None = None
        self._stopping = False
        # Walk hierarchy NOW so RedEmitter sees the parent IDs on stores/FCs
        _attach_hierarchy(kg)
        # Pin every Pod to a Node in its cluster so the built-in
        # `Node HOSTS Pod` relation can PROPERTY_MATCH-join on
        # Pod.node == Node.name.
        _attach_pod_to_node(kg)
        # Precompute Store/FC → cluster + service-list mapping. Used by
        # _emit_all to fan out the entity gauge across services so the
        # v0.6 Store→Service HOSTS PROPERTY_MATCH relation can fire.
        self._store_cluster_map = _compute_store_cluster_map(kg)
        self._red = RedEmitter(
            kg, self._plan_id, self._customer,
            # Pass env/site through so RedEmitter stamps them on the
            # affinity / business metrics — required for METRICS-based
            # relations (Service USES Database, Channel SERVES Service)
            # to land in the same scope as Service entities (which carry
            # asserts_site=demo from target_info). See `_common_attrs`
            # docstring for the GS-confirmed scope-mismatch bug.
            env=self._env, site=self._site,
            diurnal_pattern=plan.data_blueprint.diurnal_pattern,
            weekly_pattern=plan.data_blueprint.weekly_pattern,
        ) if emit_red else None
        self._log_emitter: LogEmitter | None = (
            LogEmitter(kg, self._plan_id, self._customer)
            if emit_red else None
        )

    def start(self) -> None:
        os.environ.setdefault(
            "OTEL_EXPORTER_OTLP_METRICS_TEMPORALITY_PREFERENCE", "cumulative"
        )
        # _attach_hierarchy already ran in __init__

        # ONE Resource: identifies the emitter itself, NOT the entities.
        # Service entity processor will see this as a single Service (the
        # emitter), not one Service per emitted entity.
        resource = clarion_resource(
            service_name="proj-clarion-kg-emitter",
            plan_id=self._plan_id,
            customer=self._customer,
            env=self._env,
            site=self._site,
        )

        reader = PeriodicExportingMetricReader(
            OTLPMetricExporter(preferred_temporality=_PREFER_CUMULATIVE),
            export_interval_millis=self._export_interval_ms,
        )
        self._provider = MeterProvider(resource=resource, metric_readers=[reader])
        meter = self._provider.get_meter("proj-clarion.kg_publish")

        # ONE observable gauge — emits N observations per cycle, one per entity.
        meter.create_observable_gauge(
            "clarion_entity_info",
            callbacks=[self._emit_all],
            description="Presence gauge for Clarion KG entities; one observation per entity",
        )

        # RED metrics so KG entities have insights to render
        if self._red is not None:
            self._red.install(meter, base_resource=resource)

        # Synthetic logs per service (separate provider, same Resource) so
        # the Logs tab on every entity has data to surface
        if self._log_emitter is not None:
            self._log_emitter.install(resource)

        _logger.info(
            "kg_emitter.start",
            entity_count=len(self._kg.nodes),
            services=sum(1 for n in self._kg.nodes if n.technical_subtype == "service"),
            red_metrics=self._red is not None,
            log_emitter=self._log_emitter is not None,
            plan_id=self._plan_id,
            customer=self._customer,
        )

    def _heartbeat(self) -> None:
        """Best-effort heartbeat write into demo_sessions.

        Runs once per emit cycle (i.e. every 30s by default). Wrapped in
        a broad try/except because:

        - If there's no active demo_sessions row for this plan_id, the
          UPDATE matches zero rows — that's normal and not an error
          (e.g. the emitter was started via the old kg-publish CLI flow,
          not via `/api/demo/start`).
        - If the DB is briefly unreachable (Postgres restart, network
          blip), we DO NOT want telemetry emission to fail. The heartbeat
          is observability for the UI, not a load-bearing dependency of
          the OTLP push.

        Telemetry-flow lesson learned: do NOT raise here. Worst case is
        the UI shows "stale" briefly until the next cycle's heartbeat
        succeeds; best case is the OTLP push to Cloud continues unaffected.
        """
        try:
            # Local import — keeps the emitter importable in contexts
            # where Postgres isn't reachable (tests, dry-run, etc.).
            from proj_clarion.storage import DemoSessionRepo, session_scope
            with session_scope() as s:
                DemoSessionRepo().heartbeat(s, self._plan_id)
        except Exception:  # noqa: BLE001 — see docstring
            pass

    def _emit_all(self, _options: Any) -> list[Observation]:
        """One Observation per KG entity, except Stores/FCs which fan out
        across the services in their per-store cluster.

        The fan-out is what populates `service` and `namespace` on Store
        entity series — required by the Store→Service HOSTS PROPERTY_MATCH
        relation in the v0.6 KG model. Without it, every Store would have
        a single series with empty service/namespace and the relation
        would never fire.
        """
        # Heartbeat is best-effort and runs once per cycle — see
        # `_heartbeat` for why this can't throw.
        self._heartbeat()

        out: list[Observation] = []
        for node in self._kg.nodes:
            if node.business_subtype in ("store", "fulfillment_center"):
                out.extend(self._observations_for_store(node))
            else:
                out.append(Observation(
                    value=1,
                    attributes=_observation_attrs(
                        node, self._customer,
                        env=self._env, site=self._site,
                    ),
                ))
        return out

    def _observations_for_store(self, node: KGNode) -> list[Observation]:
        """Fan out a Store/FC entity across the services in its cluster.

        - Always returns at least one observation, so the Store entity
          itself exists in Mimir even when no per-store cluster is found.
        - When a cluster is found, sets `clarion_kube_cluster` to the
          cluster's id (Store→KubeCluster RUNS_ON relation key).
        - When services are found in that cluster, emits one observation
          per (service, namespace) pair so the Store entity inherits
          multi-valued service/namespace properties (HOSTS relation key).
        """
        info = self._store_cluster_map.get(node.node_id, {"cluster_id": None, "services": []})
        base = _observation_attrs(
            node, self._customer,
            env=self._env, site=self._site,
        )
        if info["cluster_id"]:
            base["clarion_kube_cluster"] = info["cluster_id"]

        services = info["services"]
        if not services:
            # No per-store cluster discovered — emit a single observation
            # so the Store entity is still defined; service/namespace are
            # absent so HOSTS relation simply won't fire for it.
            return [Observation(value=1, attributes=base)]

        out: list[Observation] = []
        for s in services:
            attrs = dict(base)
            attrs["service"]   = s["service"]
            attrs["namespace"] = s["namespace"]
            out.append(Observation(value=1, attributes=attrs))
        return out

    def stop(self) -> None:
        if self._stopping:
            return
        self._stopping = True
        if self._log_emitter is not None:
            try:
                self._log_emitter.stop()
            except Exception as exc:  # noqa: BLE001
                _logger.warning("kg_emitter.log_shutdown.failed", error=str(exc))
        if self._red is not None:
            try:
                self._red.shutdown()
            except Exception as exc:  # noqa: BLE001
                _logger.warning("kg_emitter.red_shutdown.failed", error=str(exc))
        if self._provider is not None:
            try:
                self._provider.shutdown()
            except Exception as exc:  # noqa: BLE001
                _logger.warning("kg_emitter.shutdown.failed", error=str(exc))
        _logger.info("kg_emitter.stop", customer=self._customer)

    def run_forever(self) -> None:
        installed: list[tuple[int, Any]] = []

        def _handler(_sig: int, _frame: Any) -> None:
            self.stop()

        for sig in (signal.SIGINT, signal.SIGTERM):
            installed.append((sig, signal.signal(sig, _handler)))
        try:
            while not self._stopping:
                time.sleep(1)
        finally:
            for sig, prev in installed:
                signal.signal(sig, prev)


def _slug_for_plan(plan: DemoPlan) -> str:
    """Derive a customer slug from the source profile id (e.g. 'prof-acme_retail' → 'acme_retail').
    Use the source profile id, not the company name, because at this point the planner only
    knows the profile id; the CLI can override with --customer to set a friendlier value.
    """
    pid = plan.source_profile_id
    return _slug(pid.removeprefix("prof-")) if pid.startswith("prof-") else _slug(pid)
