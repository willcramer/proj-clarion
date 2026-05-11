"""Lock in the per-entity label invariants the v0.6 KG model depends on.

Source of truth: `infra/grafana/clarion-business-model.yaml`. If you change
the model in Grafana and don't update this test, every relation that depends
on a missing label will silently produce zero edges in Cloud KG. These tests
fail loudly when an emitter change drops a required label.

Each test asserts ONE specific PROPERTY_MATCH or query requirement from
the model.
"""

from __future__ import annotations

import pytest

from proj_clarion.kg_publish.emitter import (
    _attach_hierarchy,
    _attach_pod_to_node,
    _compute_store_cluster_map,
    _observation_attrs,
)
from proj_clarion.schemas import (
    EdgeType,
    KGEdge,
    KGNode,
    KnowledgeGraph,
    NodeType,
)


# ============================================================
# Fixture: one of every entity kind, wired up the way expand.py does
# ============================================================

@pytest.fixture()
def kg() -> KnowledgeGraph:
    """Hand-built KG covering every kind the model has rules for, in the same
    shape `expand_with_synthetic_infra` produces (Store has its own cluster
    with edge services).
    """
    return KnowledgeGraph(
        nodes=[
            # Business hierarchy
            KGNode(node_id="region-na", node_type=NodeType.BUSINESS_ENTITY,
                   business_subtype="region", label="North America"),
            KGNode(node_id="channel-d2c", node_type=NodeType.BUSINESS_ENTITY,
                   business_subtype="channel", label="D2C Web"),
            KGNode(node_id="store-portland", node_type=NodeType.BUSINESS_ENTITY,
                   business_subtype="store", label="Store NA-6"),
            KGNode(node_id="fc-hq-city", node_type=NodeType.BUSINESS_ENTITY,
                   business_subtype="fulfillment_center", label="<HQ-city> FC"),

            # Per-store cluster (built-in KubeCluster type)
            KGNode(node_id="cluster-portland", node_type=NodeType.TECHNICAL_RESOURCE,
                   technical_subtype="cluster", label="Portland cluster",
                   attributes={"store_id": "store-portland", "kind": "kubecluster"}),
            # Per-FC cluster
            KGNode(node_id="cluster-hq-city", node_type=NodeType.TECHNICAL_RESOURCE,
                   technical_subtype="cluster", label="<HQ-city> cluster",
                   attributes={"store_id": "fc-hq-city", "kind": "kubecluster"}),

            # Namespaces
            KGNode(node_id="ns-portland-commerce", node_type=NodeType.TECHNICAL_RESOURCE,
                   technical_subtype="namespace", label="commerce",
                   attributes={"cluster_id": "cluster-portland", "store_id": "store-portland"}),

            # Edge services in the Portland cluster
            KGNode(node_id="svc-portland-pos-edge", node_type=NodeType.TECHNICAL_RESOURCE,
                   technical_subtype="service", label="pos-edge",
                   attributes={"cluster_id": "cluster-portland",
                               "store_id": "store-portland",
                               "namespace_id": "ns-portland-commerce"}),
            KGNode(node_id="svc-portland-inventory-cache", node_type=NodeType.TECHNICAL_RESOURCE,
                   technical_subtype="service", label="inventory-cache",
                   attributes={"cluster_id": "cluster-portland",
                               "store_id": "store-portland",
                               "namespace_id": "ns-portland-commerce"}),

            # A pod (built-in Pod entity)
            KGNode(node_id="pod-portland-pos-edge-0", node_type=NodeType.TECHNICAL_RESOURCE,
                   technical_subtype="deployment", label="pos-edge-0",
                   attributes={"kind": "pod",
                               "service_id": "svc-portland-pos-edge",
                               "namespace_id": "ns-portland-commerce",
                               "cluster_id": "cluster-portland",
                               "store_id": "store-portland"}),

            # K8s node (built-in Node entity)
            KGNode(node_id="node-portland-1", node_type=NodeType.TECHNICAL_RESOURCE,
                   technical_subtype="deployment", label="Portland node-1",
                   attributes={"kind": "kubenode", "cluster_id": "cluster-portland"}),

            # Custom infra under a store
            KGNode(node_id="lb-portland", node_type=NodeType.TECHNICAL_RESOURCE,
                   technical_subtype="external_dependency", label="Portland ALB",
                   attributes={"kind": "loadbalancer",
                               "store_id": "store-portland",
                               "cluster_id": "cluster-portland"}),
            KGNode(node_id="db-portland", node_type=NodeType.TECHNICAL_RESOURCE,
                   technical_subtype="database", label="Portland DB",
                   attributes={"kind": "database",
                               "store_id": "store-portland",
                               "cluster_id": "cluster-portland"}),
            KGNode(node_id="topic-portland-events", node_type=NodeType.TECHNICAL_RESOURCE,
                   technical_subtype="queue", label="portland-events",
                   attributes={"kind": "topic",
                               "store_id": "store-portland"}),
            KGNode(node_id="vm-monitoring", node_type=NodeType.TECHNICAL_RESOURCE,
                   technical_subtype="external_dependency", label="Monitoring VM",
                   attributes={"kind": "vm",
                               "cluster_id": "cluster-portland",
                               "store_id": "store-portland"}),
        ],
        edges=[
            # Business hierarchy
            KGEdge(edge_id="edge-1", edge_type=EdgeType.CONTAINS,
                   from_node_id="region-na", to_node_id="store-portland"),
            KGEdge(edge_id="edge-2", edge_type=EdgeType.CONTAINS,
                   from_node_id="channel-d2c", to_node_id="store-portland"),
            KGEdge(edge_id="edge-3", edge_type=EdgeType.CONTAINS,
                   from_node_id="region-na", to_node_id="fc-hq-city"),
            # Cluster ownership
            KGEdge(edge_id="edge-4", edge_type=EdgeType.RUNS_ON,
                   from_node_id="store-portland", to_node_id="cluster-portland"),
        ],
    )


def _attrs_for(kg: KnowledgeGraph, node_id: str) -> dict[str, str]:
    """Convenience: run hierarchy + pod-to-node walk, then return the
    observation dict for one node. Mirrors the order EntityEmitter does
    its precompute in __init__.
    """
    _attach_hierarchy(kg)
    _attach_pod_to_node(kg)
    node = next(n for n in kg.nodes if n.node_id == node_id)
    return _observation_attrs(node, customer="testco")


def _all_observations(kg: KnowledgeGraph) -> list[dict[str, str]]:
    """Mirror what `EntityEmitter._emit_all` does, without the OTel SDK
    plumbing. Returns the list of attribute dicts that would land in Mimir.

    The shape is:
      - Non-store/FC entities: one observation per node (current behavior).
      - Store/FC entities: one observation per service in the per-store
        cluster, with `clarion_kube_cluster` populated from the cluster
        lookup. Falls back to a single observation when no cluster exists.

    Keeping this mirror here (instead of instantiating EntityEmitter) lets
    the test stay independent of DemoPlan schema churn.
    """
    _attach_hierarchy(kg)
    _attach_pod_to_node(kg)
    store_map = _compute_store_cluster_map(kg)
    out: list[dict[str, str]] = []
    for node in kg.nodes:
        base = _observation_attrs(node, customer="testco")
        if node.business_subtype not in ("store", "fulfillment_center"):
            out.append(base)
            continue
        info = store_map.get(node.node_id, {"cluster_id": None, "services": []})
        if info["cluster_id"]:
            base["clarion_kube_cluster"] = info["cluster_id"]
        if not info["services"]:
            out.append(base)
        else:
            for s in info["services"]:
                attrs = dict(base)
                attrs["service"]   = s["service"]
                attrs["namespace"] = s["namespace"]
                out.append(attrs)
    return out


# ============================================================
# Account / Region / Channel — the simple business hierarchy
# ============================================================

class TestBusinessHierarchy:
    def test_region_carries_customer_for_account_to_region_relation(
        self, kg: KnowledgeGraph,
    ) -> None:
        """Account.name == Region.customer for the CONTAINS relation."""
        a = _attrs_for(kg, "region-na")
        assert a["clarion_region_id"] == "region-na"
        assert a["clarion_customer"] == "testco"

    def test_channel_carries_customer_for_account_to_channel_relation(
        self, kg: KnowledgeGraph,
    ) -> None:
        a = _attrs_for(kg, "channel-d2c")
        assert a["clarion_channel_id"] == "channel-d2c"
        assert a["clarion_customer"] == "testco"


# ============================================================
# Store — most label-dense entity in the model
# ============================================================

class TestStoreLabels:
    def test_store_observation_carries_region_and_channel_ids(
        self, kg: KnowledgeGraph,
    ) -> None:
        """Region→Store and Channel→Store CONTAINS relations both join on
        these label values. Without them the business hierarchy breaks."""
        a = _attrs_for(kg, "store-portland")
        assert a["clarion_store_id"] == "store-portland"
        assert a["clarion_region_id"] == "region-na"
        assert a["clarion_channel_id"] == "channel-d2c"

    def test_store_observations_set_clarion_kube_cluster(
        self, kg: KnowledgeGraph,
    ) -> None:
        """Store→KubeCluster RUNS_ON joins on Store.cluster == KubeCluster.name.
        Cluster id must come from the precomputed map (store-id back-reference
        on the cluster node), not from store.attributes (which planner-output
        and synth stores don't have)."""
        observations = _all_observations(kg)
        # Filter on entity_kind so we don't pick up infra entities that also
        # carry clarion_store_id (pods, LBs, etc.)
        store_obs = [
            o for o in observations
            if o.get("clarion_entity_kind") == "store"
            and o.get("clarion_store_id") == "store-portland"
        ]
        assert store_obs, "Store entity must emit at least one observation"
        for o in store_obs:
            assert o["clarion_kube_cluster"] == "cluster-portland", (
                "Store missing clarion_kube_cluster — Store→KubeCluster RUNS_ON breaks"
            )

    def test_store_observations_fan_out_across_services(
        self, kg: KnowledgeGraph,
    ) -> None:
        """Store→Service HOSTS PROPERTY_MATCH joins on
        [Store.service, Store.namespace] == [Service.name, Service.namespace].
        For multi-valued matching to work, each Store must be present in
        series carrying every (service, namespace) it hosts.
        """
        observations = _all_observations(kg)
        store_obs = [
            o for o in observations
            if o.get("clarion_entity_kind") == "store"
            and o.get("clarion_store_id") == "store-portland"
        ]
        # Two services in cluster-portland → two store observations
        assert len(store_obs) == 2, (
            f"Expected 2 store observations (one per service), got {len(store_obs)}"
        )
        services = {(o["service"], o["namespace"]) for o in store_obs}
        assert services == {
            ("portland-pos-edge",         "portland-commerce"),
            ("portland-inventory-cache",  "portland-commerce"),
        }


class TestFulfillmentCenterLabels:
    def test_fc_observations_set_clarion_kube_cluster(
        self, kg: KnowledgeGraph,
    ) -> None:
        """Same reliability requirement as Store, via the same lookup path."""
        observations = _all_observations(kg)
        fc_obs = [
            o for o in observations
            if o.get("clarion_entity_kind") == "fulfillment_center"
            and o.get("clarion_fulfillment_center_id") == "fc-hq-city"
        ]
        assert fc_obs, "FulfillmentCenter entity must emit at least one observation"
        for o in fc_obs:
            assert o["clarion_kube_cluster"] == "cluster-hq-city"

    def test_fc_with_no_services_still_emitted_as_single_observation(
        self, kg: KnowledgeGraph,
    ) -> None:
        """An FC whose cluster has no services should still produce one Store
        entity (so Account→FC and Region→FC relations work even when HOSTS
        won't fire)."""
        observations = _all_observations(kg)
        fc_obs = [
            o for o in observations
            if o.get("clarion_entity_kind") == "fulfillment_center"
            and o.get("clarion_fulfillment_center_id") == "fc-hq-city"
        ]
        assert len(fc_obs) == 1
        # service/namespace are absent (no fan-out, no HOSTS)
        assert "service" not in fc_obs[0] or fc_obs[0]["service"] == ""


# ============================================================
# Pod / VM / Node / LB / DB / Topic — infra entities
# ============================================================

class TestInfraEntityLabels:
    def test_pod_carries_store_for_store_to_pod_contains_relation(
        self, kg: KnowledgeGraph,
    ) -> None:
        a = _attrs_for(kg, "pod-portland-pos-edge-0")
        assert a["clarion_pod_id"] == "pod-portland-pos-edge-0"
        assert a["clarion_store_id"] == "store-portland", (
            "Pod missing clarion_store_id — Store→Pod CONTAINS breaks"
        )
        assert a["service"] == "portland-pos-edge"
        assert a["namespace"] == "portland-commerce"
        assert a["clarion_kube_cluster"] == "cluster-portland"

    def test_pod_carries_node_for_built_in_node_hosts_pod_relation(
        self, kg: KnowledgeGraph,
    ) -> None:
        """The built-in `Node HOSTS Pod` relation does PROPERTY_MATCH on
        Node.name == Pod.node. Every Pod must carry a `node` label
        pointing at one of the kubenodes in its cluster, or the
        relation has no join key.
        """
        a = _attrs_for(kg, "pod-portland-pos-edge-0")
        assert "node" in a, (
            "Pod missing `node` label — built-in Node HOSTS Pod won't fire"
        )
        # In the fixture, cluster-portland has exactly one kubenode
        assert a["node"] == "node-portland-1"

    def test_vm_carries_cluster_and_store_for_runs_on_and_contains(
        self, kg: KnowledgeGraph,
    ) -> None:
        a = _attrs_for(kg, "vm-monitoring")
        assert a["clarion_vm_id"] == "vm-monitoring"
        # VM→KubeCluster RUNS_ON
        assert a["clarion_kube_cluster"] == "cluster-portland"
        # Store→VM CONTAINS
        assert a["clarion_store_id"] == "store-portland"

    def test_node_carries_cluster_for_kubecluster_to_node_relation(
        self, kg: KnowledgeGraph,
    ) -> None:
        a = _attrs_for(kg, "node-portland-1")
        assert a["clarion_node_id"] == "node-portland-1"
        assert a["clarion_kube_cluster"] == "cluster-portland"

    def test_loadbalancer_database_topic_carry_store(
        self, kg: KnowledgeGraph,
    ) -> None:
        """All three need clarion_store_id for Store→{LB,DB,Topic} CONTAINS."""
        for node_id, label_key in (
            ("lb-portland",            "clarion_loadbalancer_id"),
            ("db-portland",            "clarion_database_id"),
            ("topic-portland-events",  "clarion_topic_id"),
        ):
            a = _attrs_for(kg, node_id)
            assert a[label_key] == node_id
            assert a["clarion_store_id"] == "store-portland", (
                f"{node_id} missing clarion_store_id"
            )


# ============================================================
# _compute_store_cluster_map covers a few edge cases worth pinning
# ============================================================

class TestStoreClusterMap:
    def test_store_with_no_cluster_returns_empty_services(self) -> None:
        """A Store without a per-store cluster (e.g. a planner-original store
        the expansion didn't reach yet) must not crash; just return empty."""
        kg = KnowledgeGraph(
            nodes=[
                KGNode(node_id="store-orphan", node_type=NodeType.BUSINESS_ENTITY,
                       business_subtype="store", label="Orphan"),
            ],
            edges=[],
        )
        m = _compute_store_cluster_map(kg)
        assert m["store-orphan"] == {"cluster_id": None, "services": []}

    def test_namespace_id_prefix_stripped_in_service_fanout(self) -> None:
        """The service `namespace` label must be the unprefixed name (e.g.
        `commerce`), not the KG node id (`ns-…-commerce`), because
        target_info-derived Service entities use the unprefixed value."""
        kg = KnowledgeGraph(
            nodes=[
                KGNode(node_id="store-x", node_type=NodeType.BUSINESS_ENTITY,
                       business_subtype="store", label="X"),
                KGNode(node_id="cluster-x", node_type=NodeType.TECHNICAL_RESOURCE,
                       technical_subtype="cluster", label="X cluster",
                       attributes={"store_id": "store-x", "kind": "kubecluster"}),
                KGNode(node_id="svc-x-foo", node_type=NodeType.TECHNICAL_RESOURCE,
                       technical_subtype="service", label="foo",
                       attributes={"cluster_id": "cluster-x",
                                   "namespace_id": "ns-x-commerce"}),
            ],
            edges=[],
        )
        m = _compute_store_cluster_map(kg)
        assert m["store-x"]["services"] == [
            {"service": "x-foo", "namespace": "x-commerce"},
        ]


