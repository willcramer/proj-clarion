"""Synthetic technical-tier expansion of a plan's KG.

Expands a planner-generated KG into a fuller, demo-ready topology:
- If the plan has fewer than `min_stores` stores, synthesise more (with
  plausible names/regions for the company's vertical).
- Each Store gets its OWN dedicated edge cluster (mirrors how a real
  multi-location retailer deploys per-store edge infra), in addition to the
  central cluster the planner produced.
- Each store cluster gets: 1-2 namespaces, 2-3 nodes (built-in `Node`
  entity type), 4-6 pods (built-in `Pod`), 1 LoadBalancer (built-in,
  picks up Asserts LB icon), 1 Database (built-in), 1 Topic (built-in,
  Kafka stream).
- Synthesised entities use built-in TYPE NAMES so they inherit the Asserts
  icon registry (e.g. Pod → pod icon, Database → cylinder, LoadBalancer →
  routing icon). The definedBy query is still ours; we just borrow the
  type name for visual identity.

The expansion is deterministic: same plan_id → same shape, no surprises
between re-runs.
"""

from __future__ import annotations

import random
from typing import Any

from proj_clarion.schemas import (
    DemoPlan,
    EdgeType,
    KGEdge,
    KGNode,
    KnowledgeGraph,
    NodeType,
)


def _seeded_rng(plan_id: str, salt: str = "") -> random.Random:
    seed_bytes = (plan_id + "|" + salt).encode()
    return random.Random(int.from_bytes(seed_bytes[:8].ljust(8, b"\x00"), "little"))


# ============================================================
# Plausible names for synthesised stores per region (retail-leaning;
# vertical-specific naming lands in Stage 3 with planner prompts)
# ============================================================

_PLAUSIBLE_STORE_BY_REGION = {
    "region-na": [
        ("store-na-6",  "Store NA-6"),
        ("store-na-2",    "Store NA-2"),
        ("store-na-3",   "Store NA-3"),
        ("store-na-4",       "Store NA-4"),
        ("store-na-5",        "Store NA-5"),
    ],
    "region-emea": [
        ("store-emea-1",    "Store EMEA-1"),
        ("store-emea-2",    "Store EMEA-2"),
        ("store-emea-3",     "Store EMEA-3"),
    ],
    "region-apac": [
        ("store-apac-1",     "Store APAC-1"),
        ("store-apac-2", "Store APAC-2"),
    ],
}


# NOTE: the previous `_RETAIL_BUSINESS_MODELS` allow-list has been
# removed. The synthesizer now AUGMENTS existing entity types in the
# plan rather than introducing new ones based on Python-side vertical
# inference. If the plan has stores, the synthesizer adds more (up to
# `min_stores`); if it has zero stores, the synthesizer adds zero.
# Same principle: the planner LLM is canonical — Python doesn't override.


def _synthesize_extra_stores(
    plan: DemoPlan,
    rng: random.Random,
    min_stores: int,
) -> tuple[list[KGNode], list[KGEdge]]:
    """If the plan has fewer than `min_stores` Stores, fabricate more so the
    demo shows multi-location operation. Each new Store is anchored to one of
    the plan's existing Regions and to a Channel that makes sense for it
    (defaults to the first retail channel found, falls back to first Channel).

    Augment-only policy: this synthesizer never INTRODUCES stores to a
    plan that doesn't already have them. If the planner LLM (informed by
    the company's vertical) decided to model stores, we top them up to
    `min_stores` so the demo shows multi-location operation. If the
    planner decided NOT to model stores (airline, manufacturer, SaaS),
    we leave the KG alone — the plan is canonical, this synthesizer
    only augments.
    """
    existing_stores = [
        n for n in plan.knowledge_graph.nodes
        if n.business_subtype == "store"
    ]
    if not existing_stores:
        # Plan didn't model stores — don't fabricate them. The planner
        # already picked vertical-appropriate entity types via
        # business_entity_candidates + business_subtype guidance.
        return [], []
    needed = max(0, min_stores - len(existing_stores))
    if needed <= 0:
        return [], []

    regions = [
        n for n in plan.knowledge_graph.nodes if n.business_subtype == "region"
    ]
    channels = [
        n for n in plan.knowledge_graph.nodes if n.business_subtype == "channel"
    ]
    # Prefer a retail-shaped channel (D2C web/retail) for the synthetic stores
    primary_channel = next(
        (c for c in channels if any(t in c.node_id for t in ("retail", "d2c"))),
        channels[0] if channels else None,
    )

    new_nodes: list[KGNode] = []
    new_edges: list[KGEdge] = []
    edge_seq = 9000  # starts well past the planner's edge ids

    # Distribute new stores across regions round-robin
    placed_per_region: dict[str, int] = {}
    for i in range(needed):
        if not regions:
            break
        region = regions[i % len(regions)]
        idx_in_region = placed_per_region.get(region.node_id, 0)
        candidates = _PLAUSIBLE_STORE_BY_REGION.get(
            region.node_id,
            [(f"store-synth-{i+1}", f"Synth Store {i+1}")],
        )
        if idx_in_region >= len(candidates):
            continue  # ran out of plausible names for this region
        store_id, label = candidates[idx_in_region]
        placed_per_region[region.node_id] = idx_in_region + 1

        # Skip if a real store already has this id (rare but possible)
        if any(s.node_id == store_id for s in existing_stores):
            continue

        new_nodes.append(KGNode(
            node_id=store_id,
            node_type=NodeType.BUSINESS_ENTITY,
            business_subtype="store",
            label=label,
            attributes={
                "synthetic": True,
                "source": "expand_with_synthetic_infra",
            },
        ))
        # Region CONTAINS Store
        new_edges.append(KGEdge(
            edge_id=f"edge-{edge_seq:04d}",
            edge_type=EdgeType.CONTAINS,
            from_node_id=region.node_id, to_node_id=store_id,
        ))
        edge_seq += 1
        # Channel CONTAINS Store
        if primary_channel:
            new_edges.append(KGEdge(
                edge_id=f"edge-{edge_seq:04d}",
                edge_type=EdgeType.CONTAINS,
                from_node_id=primary_channel.node_id, to_node_id=store_id,
            ))
            edge_seq += 1

    return new_nodes, new_edges


# ============================================================
# Per-store cluster topology
# ============================================================

def _build_store_cluster(
    store: KGNode,
    rng: random.Random,
    edge_seq: list[int],  # mutable counter
) -> tuple[list[KGNode], list[KGEdge]]:
    """Generate a per-store edge cluster + namespaces, nodes, pods,
    load balancer, database, and topic. All entities use built-in
    Asserts type names so they inherit nice icons.
    """
    nodes: list[KGNode] = []
    edges: list[KGEdge] = []
    # Use the store's full id (with `store-` prefix preserved) as the
    # slug for synthesized resources. Stripping the prefix is shorter but
    # collides when a planner-emitted entity happens to share the bare
    # slug — e.g. planner emits `db-0` while a `store-0` also synthesizes
    # `db-0`, producing two KGNodes with the same id and corrupted graph
    # validation. The slug is only used in node ids, not in user-visible
    # labels (those still derive from `store.label`), so the readability
    # cost is invisible to the SE.
    store_slug = store.node_id

    def _next_edge() -> str:
        edge_seq[0] += 1
        return f"edge-{edge_seq[0]:04d}"

    # Per-store cluster (built-in KubeCluster type).
    cluster_id = f"cluster-{store_slug}"
    nodes.append(KGNode(
        node_id=cluster_id,
        node_type=NodeType.TECHNICAL_RESOURCE,
        technical_subtype="cluster",
        label=f"{store.label} cluster",
        attributes={
            "store_id": store.node_id,
            "kind": "kubecluster",
        },
    ))
    edges.append(KGEdge(
        edge_id=_next_edge(),
        edge_type=EdgeType.RUNS_ON,
        from_node_id=store.node_id, to_node_id=cluster_id,
    ))

    # 2 namespaces per store cluster
    for ns_purpose in ("commerce", "ops"):
        ns_id = f"ns-{store_slug}-{ns_purpose}"
        nodes.append(KGNode(
            node_id=ns_id,
            node_type=NodeType.TECHNICAL_RESOURCE,
            technical_subtype="namespace",
            label=ns_purpose,
            attributes={
                "cluster_id": cluster_id,
                "store_id": store.node_id,
            },
        ))
        edges.append(KGEdge(
            edge_id=_next_edge(),
            edge_type=EdgeType.CONTAINS,
            from_node_id=cluster_id, to_node_id=ns_id,
        ))

    # 2-3 k8s nodes per store cluster (built-in `Node` entity)
    n_nodes = rng.randint(2, 3)
    for i in range(n_nodes):
        node_id = f"node-{store_slug}-{i+1}"
        nodes.append(KGNode(
            node_id=node_id,
            node_type=NodeType.TECHNICAL_RESOURCE,
            technical_subtype="deployment",
            label=f"{store.label} node-{i+1}",
            attributes={
                "kind": "kubenode",
                "cluster_id": cluster_id,
                "store_id": store.node_id,
                "instance_type": rng.choice(["m6i.xlarge", "c6i.xlarge"]),
                "node_index": i + 1,
            },
        ))
        edges.append(KGEdge(
            edge_id=_next_edge(),
            edge_type=EdgeType.CONTAINS,
            from_node_id=cluster_id, to_node_id=node_id,
        ))

    # Edge services + 1-2 pods each — small per-store service portfolio
    edge_services = ["pos-edge", "inventory-cache", "customer-display"]
    for svc_name in edge_services:
        svc_id = f"svc-{store_slug}-{svc_name}"
        nodes.append(KGNode(
            node_id=svc_id,
            node_type=NodeType.TECHNICAL_RESOURCE,
            technical_subtype="service",
            label=svc_name,
            attributes={
                "cluster_id": cluster_id,
                "store_id": store.node_id,
                "namespace_id": f"ns-{store_slug}-commerce",
                "edge_service": True,
            },
        ))
        edges.append(KGEdge(
            edge_id=_next_edge(),
            edge_type=EdgeType.RUNS_ON,
            from_node_id=svc_id, to_node_id=cluster_id,
        ))
        # 1-2 pods per edge service
        n_pods = rng.randint(1, 2)
        for i in range(n_pods):
            pod_id = f"pod-{store_slug}-{svc_name}-{i}"
            nodes.append(KGNode(
                node_id=pod_id,
                node_type=NodeType.TECHNICAL_RESOURCE,
                technical_subtype="deployment",
                label=f"{svc_name}-{i}",
                attributes={
                    "kind": "pod",
                    "service_id": svc_id,
                    "namespace_id": f"ns-{store_slug}-commerce",
                    "cluster_id": cluster_id,
                    "store_id": store.node_id,
                    "image": f"clarion/{svc_name}:edge",
                    "replicas_total": n_pods,
                    "replica_index": i,
                },
            ))
            edges.append(KGEdge(
                edge_id=_next_edge(),
                edge_type=EdgeType.CONTAINS,
                from_node_id=svc_id, to_node_id=pod_id,
            ))

    # 1 LoadBalancer per store (built-in `LoadBalancer` icon)
    lb_id = f"lb-{store_slug}"
    nodes.append(KGNode(
        node_id=lb_id,
        node_type=NodeType.TECHNICAL_RESOURCE,
        technical_subtype="external_dependency",
        label=f"{store.label} ALB",
        attributes={
            "kind": "loadbalancer",
            "cluster_id": cluster_id,
            "store_id": store.node_id,
        },
    ))
    edges.append(KGEdge(
        edge_id=_next_edge(),
        edge_type=EdgeType.CONTAINS,
        from_node_id=store.node_id, to_node_id=lb_id,
    ))

    # 1 Database per store (built-in `Database` icon)
    db_id = f"db-{store_slug}"
    nodes.append(KGNode(
        node_id=db_id,
        node_type=NodeType.TECHNICAL_RESOURCE,
        technical_subtype="database",
        label=f"{store.label} local DB",
        attributes={
            "kind": "database",
            "engine": "postgres",
            "store_id": store.node_id,
            "cluster_id": cluster_id,
        },
    ))
    edges.append(KGEdge(
        edge_id=_next_edge(),
        edge_type=EdgeType.CONTAINS,
        from_node_id=store.node_id, to_node_id=db_id,
    ))

    # 1 Kafka Topic per store (built-in `Topic` icon)
    topic_id = f"topic-{store_slug}-events"
    nodes.append(KGNode(
        node_id=topic_id,
        node_type=NodeType.TECHNICAL_RESOURCE,
        technical_subtype="queue",
        label=f"{store_slug}-events",
        attributes={
            "kind": "topic",
            "store_id": store.node_id,
            "broker": "kafka",
        },
    ))
    edges.append(KGEdge(
        edge_id=_next_edge(),
        edge_type=EdgeType.CONTAINS,
        from_node_id=store.node_id, to_node_id=topic_id,
    ))

    return nodes, edges


# ============================================================
# Public entrypoint
# ============================================================

# ============================================================
# Customer-wide K8s quota
# ============================================================
#
# Per the v0.7+ requirement, EVERY customer's expanded KG must contain
# enough KubeCluster / KubeNode / Pod entities for the Asserts entity
# graph to look populated regardless of vertical. Specifically:
#
#   * ≥ MIN_CLUSTERS_PER_CUSTOMER clusters  (default 5)
#   * exactly NODES_PER_CLUSTER nodes per cluster (default 3)
#   * each node hosts [PODS_PER_NODE_MIN, PODS_PER_NODE_MAX] pods
#     (round-robin distributed by `_attach_pod_to_node` at emit time)
#
# Cluster anchors are picked from the plan's existing physical-ish
# business entities (store → fulfillment_center → business_unit → region
# → brand). If the plan doesn't have enough physical anchors we synthesise
# `region`-subtype anchors named after cloud regions ("us-east-1" etc.)
# — schema-valid, vertically agnostic, and reads naturally as "cluster
# runs in <region>".

MIN_CLUSTERS_PER_CUSTOMER = 5
NODES_PER_CLUSTER         = 3
PODS_PER_NODE_MIN         = 2
PODS_PER_NODE_MAX         = 5

# Cloud platform variety per cluster. Names match the Grafana Cloud
# Asserts built-in `Cloud` entity-type values (AWS, Azure, GCP, etc.) so
# we get the right icon for free. We deliberately don't suffix the
# K8s-distribution (no `-eks`, `-aks`, `-gke`) — the cluster's
# distribution is a separate concept; the Cloud tier just answers "which
# provider hosts this".
#
# Each cloud is paired with one of its real region IDs so the
# `Cloud → CloudRegion → KubeCluster` chain reflects real IT topology
# (Cloud first, then a region inside that cloud). Real region names
# (us-east-1, eastus, europe-west1, etc.) so the entity graph reads as a
# customer's actual deployment, not generic placeholders. CloudRegion is
# kept distinct from the business `Region` (region-emea, region-americas)
# — those are SALES regions, not cloud-region availability zones.
#
# 5 clusters per customer × 5 entries here ⇒ each customer naturally
# spans 5 distinct cloud-region pairs. Deterministic per plan_id (offset
# from `_assign_cloud_providers`).
_CLOUD_PROVIDERS: tuple[tuple[str, str], ...] = (
    # (cloud, cloud_region)
    ("AWS",       "us-east-1"),
    ("Azure",     "eastus"),
    ("GCP",       "europe-west1"),
    ("OpenShift", "rh-na-east"),
    ("Rancher",   "rancher-edge-1"),
)

# Priority order for choosing where a cluster runs. Most-physical first.
# `channel`, `product_line`, `partner_program` are intentionally absent —
# they're abstract groupings, not deployment targets.
_PHYSICAL_ANCHOR_PRIORITY = (
    "store",
    "fulfillment_center",
    "business_unit",
    "region",
    "brand",
)

# Cloud-style fallback names for synth anchors. Used when the plan
# doesn't have enough physical entities to seat 5 clusters. Names are
# universally recognisable and stay schema-compliant by being typed as
# `region` (the schema's only physical-location subtype that fits a
# generic DC/region).
_FALLBACK_REGION_ANCHORS = (
    ("region-cloud-us-east-1",      "US-East-1"),
    ("region-cloud-us-west-2",      "US-West-2"),
    ("region-cloud-eu-west-1",      "EU-West-1"),
    ("region-cloud-ap-southeast-1", "AP-Southeast-1"),
    ("region-cloud-ap-northeast-1", "AP-Northeast-1"),
)


def _pick_cluster_anchors(
    plan: DemoPlan,
    kg_nodes: list[KGNode],
    *,
    n_needed: int,
    used_anchor_ids: set[str],
) -> tuple[list[KGNode], list[KGNode]]:
    """Return (`anchors`, `synth_anchors_to_add`) — anchors are KGNodes
    to seat clusters at, with `synth_anchors_to_add` being any new region
    nodes that should be appended to the KG before building clusters.

    `used_anchor_ids` is the set of existing-anchor ids already hosting a
    cluster (e.g. stores that got per-store clusters earlier). They're
    skipped so we don't double-build a cluster on the same store.
    """
    anchors: list[KGNode] = []
    synth: list[KGNode] = []

    by_subtype: dict[str, list[KGNode]] = {}
    for n in kg_nodes:
        if n.business_subtype:
            by_subtype.setdefault(n.business_subtype, []).append(n)

    # Walk priority list; pick first n_needed anchors not already cluster-hosting
    for st in _PHYSICAL_ANCHOR_PRIORITY:
        for n in by_subtype.get(st, []):
            if len(anchors) >= n_needed:
                break
            if n.node_id in used_anchor_ids:
                continue
            anchors.append(n)
            used_anchor_ids.add(n.node_id)
        if len(anchors) >= n_needed:
            break

    # Still short → synthesise generic cloud-region anchors. We keep
    # the list bounded by _FALLBACK_REGION_ANCHORS so a malformed plan
    # can't loop forever.
    for sid, label in _FALLBACK_REGION_ANCHORS:
        if len(anchors) >= n_needed:
            break
        if sid in used_anchor_ids:
            continue
        new_region = KGNode(
            node_id=sid,
            node_type=NodeType.BUSINESS_ENTITY,
            business_subtype="region",
            label=label,
            attributes={
                "synthetic": True,
                "source":    "kube_quota_anchor",
                "kind":      "cloud_region",
            },
        )
        synth.append(new_region)
        anchors.append(new_region)
        used_anchor_ids.add(sid)

    return anchors, synth


def _build_cluster_at_anchor(
    anchor: KGNode,
    *,
    customer: str,
    edge_seq: list[int],
) -> tuple[list[KGNode], list[KGEdge]]:
    """Stand up one KubeCluster + NODES_PER_CLUSTER kube nodes anchored
    at `anchor`. Pods are filled in later by `_ensure_pod_quota_per_cluster`.

    The anchor → cluster relationship is `RUNS_ON` (same predicate the
    existing per-store cluster uses), so model rules don't need to learn a
    new edge type.
    """
    nodes: list[KGNode] = []
    edges: list[KGEdge] = []

    def _next_edge() -> str:
        edge_seq[0] += 1
        return f"edge-{edge_seq[0]:04d}"

    # Use the anchor's FULL node_id for the cluster slug. Stripping the
    # subtype prefix is more readable but causes collisions: a Region
    # `region-0` and a Store `store-0` both strip to `0`, producing two
    # `cluster-0` entries with merged pod populations. The mom-and-pop
    # stress fixture exposed this as 16 pods/node (round-robin saw the
    # collided cluster's combined pods + a too-small node pool).
    #
    # The cluster's `label` keeps a clean human-readable form
    # (`<anchor.label> cluster`); only the id pays the readability cost.
    anchor_slug = anchor.node_id
    cluster_id = f"cluster-{anchor_slug}"

    # CRITICAL: write `cluster_id` back onto the anchor's own attributes so
    # the emitter labels the anchor's `clarion_entity_info` series with
    # `clarion_kube_cluster=<cluster_id>`. Without this label, the
    # cross-tier PROPERTY_MATCH `<BusinessUnit|Region|Brand>.cluster ↔
    # KubeCluster.name` finds no join keys, and the business tier renders
    # as a disconnected blob in the Asserts entity graph (the bug the
    # Sentinel plan exposed: 5 K8s clusters connected to each other, but
    # the Brand/BU/Region/PartnerProgram nodes floating with no edges to
    # the tech tier — Clarion's #1 goal violated).
    if "cluster_id" not in anchor.attributes:
        anchor.attributes["cluster_id"] = cluster_id

    nodes.append(KGNode(
        node_id=cluster_id,
        node_type=NodeType.TECHNICAL_RESOURCE,
        technical_subtype="cluster",
        label=f"{anchor.label} cluster",
        attributes={
            "kind":               "kubecluster",
            "anchor_id":          anchor.node_id,
            "anchor_subtype":     anchor.business_subtype or "",
            "synthetic":          True,
            "source":             "kube_quota",
        },
    ))
    # anchor RUNS_ON cluster — matches existing store→cluster shape so
    # `_compute_store_cluster_map` and built-in entity rules pick this up.
    edges.append(KGEdge(
        edge_id=_next_edge(),
        edge_type=EdgeType.RUNS_ON,
        from_node_id=anchor.node_id, to_node_id=cluster_id,
    ))

    # 3 kube nodes — emitted as built-in `Node` entities. The
    # `clarion_kube_cluster` label on each lets `Node HOSTS Pod` join
    # via PROPERTY_MATCH on `node` ↔ `clarion_node_id`.
    for i in range(NODES_PER_CLUSTER):
        node_id = f"node-{anchor_slug}-{i+1}"
        nodes.append(KGNode(
            node_id=node_id,
            node_type=NodeType.TECHNICAL_RESOURCE,
            technical_subtype="deployment",
            label=f"{anchor.label} node-{i+1}",
            attributes={
                "kind":          "kubenode",
                "cluster_id":    cluster_id,
                "anchor_id":     anchor.node_id,
                "instance_type": "m6i.xlarge",
                "node_index":    i + 1,
                "synthetic":     True,
                "source":        "kube_quota",
            },
        ))
        edges.append(KGEdge(
            edge_id=_next_edge(),
            edge_type=EdgeType.CONTAINS,
            from_node_id=cluster_id, to_node_id=node_id,
        ))

    return nodes, edges


def _ensure_pod_quota_per_cluster(
    new_nodes: list[KGNode],
    new_edges: list[KGEdge],
    rng: random.Random,
    edge_seq: list[int],
) -> None:
    """For every cluster in the KG, top up the pod count so the
    `_attach_pod_to_node` round-robin distribution lands in
    [PODS_PER_NODE_MIN, PODS_PER_NODE_MAX] pods per node.

    Pods are bound to whichever services already run on that cluster
    (cluster_id match). If the cluster has no services attached (a synth
    DC anchor), we round-robin across ALL plan services so each cluster
    still produces realistic Service↔Pod relationships in the entity
    graph — same service shows up in multiple regions, like a real
    multi-region deployment.
    """
    clusters = [n for n in new_nodes if n.technical_subtype == "cluster"]
    services = [n for n in new_nodes if n.technical_subtype == "service"]
    namespaces = [n for n in new_nodes if n.technical_subtype == "namespace"]

    services_by_cluster: dict[str, list[KGNode]] = {}
    for s in services:
        cid = s.attributes.get("cluster_id") or ""
        if cid:
            services_by_cluster.setdefault(cid, []).append(s)

    pods_by_cluster: dict[str, int] = {}
    for n in new_nodes:
        if n.attributes.get("kind") == "pod":
            cid = n.attributes.get("cluster_id") or ""
            if cid:
                pods_by_cluster[cid] = pods_by_cluster.get(cid, 0) + 1

    nodes_per_cluster_map: dict[str, list[KGNode]] = {}
    for n in new_nodes:
        if n.attributes.get("kind") in ("kubenode", "node"):
            cid = n.attributes.get("cluster_id") or ""
            if cid:
                nodes_per_cluster_map.setdefault(cid, []).append(n)

    namespace_for_cluster: dict[str, str] = {}
    for ns in namespaces:
        cid = ns.attributes.get("cluster_id") or ""
        if cid and cid not in namespace_for_cluster:
            namespace_for_cluster[cid] = ns.node_id

    def _next_edge() -> str:
        edge_seq[0] += 1
        return f"edge-{edge_seq[0]:04d}"

    for cluster in clusters:
        cluster_id = cluster.node_id
        kube_nodes = nodes_per_cluster_map.get(cluster_id, [])
        if not kube_nodes:
            # Cluster with no kube nodes — `_ensure_node_quota_per_cluster`
            # should already have fixed this, but skip defensively.
            continue

        # Target pods so per-node round-robin lands in [MIN, MAX]
        target_min = PODS_PER_NODE_MIN * len(kube_nodes)
        target_max = PODS_PER_NODE_MAX * len(kube_nodes)
        target = rng.randint(target_min, target_max)
        existing = pods_by_cluster.get(cluster_id, 0)
        n_needed = max(0, target - existing)
        if n_needed == 0:
            continue

        # Service binding pool: prefer cluster-local services; fall back
        # to plan-wide services so synth-anchor clusters still bind to
        # something meaningful. If the plan has zero services (very thin),
        # bind to a placeholder service id so observation labels stay
        # well-formed.
        bind_pool: list[KGNode] = list(services_by_cluster.get(cluster_id, []))
        if not bind_pool:
            bind_pool = list(services)
        if not bind_pool:
            bind_pool = []

        anchor_id = cluster.attributes.get("anchor_id", "")
        anchor_slug = (
            cluster_id.removeprefix("cluster-") or cluster_id
        )
        ns_id = namespace_for_cluster.get(cluster_id, "")

        for i in range(n_needed):
            if bind_pool:
                svc = bind_pool[i % len(bind_pool)]
                svc_id = svc.node_id
                svc_slug = svc_id.removeprefix("svc-")
                pod_ns = svc.attributes.get("namespace_id", "") or ns_id
            else:
                # No services at all — extremely rare, but keep observation
                # labels populated so model rules still fire.
                svc_id = ""
                svc_slug = "default"
                pod_ns = ns_id

            pod_id = f"pod-{anchor_slug}-{svc_slug}-q{existing + i}"
            new_nodes.append(KGNode(
                node_id=pod_id,
                node_type=NodeType.TECHNICAL_RESOURCE,
                technical_subtype="deployment",
                label=f"{svc_slug}-{existing + i}",
                attributes={
                    "kind":          "pod",
                    "service_id":    svc_id,
                    "namespace_id": pod_ns or "",
                    "cluster_id":    cluster_id,
                    "anchor_id":     anchor_id,
                    "image":         f"clarion/{svc_slug}:1.0.0",
                    "synthetic":     True,
                    "source":        "kube_quota",
                },
            ))
            # Service CONTAINS Pod — fires the Service↔Pod KG join when
            # the pod's `clarion_service_id` label matches the service's
            # `clarion_service_id`. The edge is also documentation-quality
            # for the in-Postgres KG.
            if svc_id:
                new_edges.append(KGEdge(
                    edge_id=_next_edge(),
                    edge_type=EdgeType.CONTAINS,
                    from_node_id=svc_id, to_node_id=pod_id,
                ))
            # Pod RUNS_ON Cluster — completes the cluster-membership graph.
            new_edges.append(KGEdge(
                edge_id=_next_edge(),
                edge_type=EdgeType.RUNS_ON,
                from_node_id=pod_id, to_node_id=cluster_id,
            ))


def _assign_cloud_providers(
    new_nodes: list[KGNode],
    plan: DemoPlan,
) -> None:
    """Stamp every cluster with `cloud` and `cloud_region` attributes,
    rotating through `_CLOUD_PROVIDERS` so each customer's 5 clusters
    span 5 distinct (Cloud, CloudRegion) pairs.

    The rotation is deterministic per plan_id, sorted by cluster id, so
    repeat runs of the same plan produce the same assignments and the
    Asserts entity graph's Cloud/CloudRegion tiers don't shuffle on
    every re-emit.

    Pre-existing `cloud`/`cloud_region` attributes are respected (so a
    planner that explicitly modeled a single-cloud customer doesn't get
    overridden — research is canonical, this is just a sensible default
    when the planner left these unset).
    """
    clusters = sorted(
        [n for n in new_nodes if n.technical_subtype == "cluster"],
        key=lambda c: c.node_id,
    )
    # Stable starting offset per plan so different customers don't all
    # assign AWS to their first cluster (would dull the demo variety
    # when running multiple customers side-by-side).
    rng = _seeded_rng(str(plan.plan_id), salt="cloud_provider")
    offset = rng.randrange(len(_CLOUD_PROVIDERS))
    for i, cluster in enumerate(clusters):
        cloud, region = _CLOUD_PROVIDERS[(i + offset) % len(_CLOUD_PROVIDERS)]
        if "cloud" not in cluster.attributes:
            cluster.attributes["cloud"] = cloud
        if "cloud_region" not in cluster.attributes:
            cluster.attributes["cloud_region"] = region


def _ensure_service_database_topology(
    new_nodes: list[KGNode],
    new_edges: list[KGEdge],
    rng: random.Random,
    edge_seq: list[int],
) -> None:
    """Make sure every service has at least one `depends_on` edge into a
    Database, so the `Service USES Database` model relation has join
    keys for every Service entity in the graph.

    Without this the planner's service→database `depends_on` coverage is
    sparse — typically 0-3 such edges across 50 services, leaving most
    services disconnected from the data tier in the Asserts entity
    graph (cylinder DBs floating with no Service edges incoming).

    Approach (kept simple and realistic):
    - Find all existing Database nodes (by `kind=database` or
      technical_subtype=database).
    - For every Service that doesn't already have a depends_on edge to
      some Database, add one — round-robined across the database pool
      so DBs share load realistically rather than every service hitting
      one mega-DB.

    No new Databases are synthesized here. If the plan has zero DBs we
    bail (Service USES Database has nothing to point at).
    """
    services = [n for n in new_nodes if n.technical_subtype == "service"]
    databases = [
        n for n in new_nodes
        if n.technical_subtype == "database" or n.attributes.get("kind") == "database"
    ]
    if not services or not databases:
        return

    # Existing service→DB depends_on edges
    existing_pairs: set[tuple[str, str]] = set()
    services_with_db: set[str] = set()
    db_ids = {d.node_id for d in databases}
    for e in new_edges:
        if e.edge_type != EdgeType.DEPENDS_ON:
            continue
        if e.to_node_id in db_ids:
            existing_pairs.add((e.from_node_id, e.to_node_id))
            services_with_db.add(e.from_node_id)

    def _next_edge() -> str:
        edge_seq[0] += 1
        return f"edge-{edge_seq[0]:04d}"

    # Round-robin services without DB across the DB pool. Sort services
    # by id so the assignment is stable across re-runs.
    services_sorted = sorted(services, key=lambda s: s.node_id)
    db_count = len(databases)
    for i, svc in enumerate(services_sorted):
        if svc.node_id in services_with_db:
            continue
        # Pick a db with affinity for the service name where possible —
        # e.g. svc-search → db-search, svc-billing → db-billing — falling
        # back to round-robin. Reads more "researched" than random pairs.
        svc_slug = svc.node_id.removeprefix("svc-").lower()
        affinity_db = next(
            (d for d in databases
             if any(tok in d.node_id.lower()
                    for tok in svc_slug.split("-")[-2:] if len(tok) > 3)),
            None,
        )
        db = affinity_db or databases[i % db_count]
        if (svc.node_id, db.node_id) in existing_pairs:
            continue
        new_edges.append(KGEdge(
            edge_id=_next_edge(),
            edge_type=EdgeType.DEPENDS_ON,
            from_node_id=svc.node_id, to_node_id=db.node_id,
        ))
        existing_pairs.add((svc.node_id, db.node_id))


def _ensure_node_quota_per_cluster(
    new_nodes: list[KGNode],
    new_edges: list[KGEdge],
    edge_seq: list[int],
) -> None:
    """Top up every cluster's kube-node count to NODES_PER_CLUSTER. Idempotent
    when a cluster already has ≥3 nodes (we never trim, only add).
    """
    clusters = [n for n in new_nodes if n.technical_subtype == "cluster"]
    nodes_per_cluster_map: dict[str, list[KGNode]] = {}
    for n in new_nodes:
        if n.attributes.get("kind") in ("kubenode", "node"):
            cid = n.attributes.get("cluster_id") or ""
            if cid:
                nodes_per_cluster_map.setdefault(cid, []).append(n)

    def _next_edge() -> str:
        edge_seq[0] += 1
        return f"edge-{edge_seq[0]:04d}"

    for cluster in clusters:
        cid = cluster.node_id
        existing = nodes_per_cluster_map.get(cid, [])
        n_needed = max(0, NODES_PER_CLUSTER - len(existing))
        if n_needed == 0:
            continue
        anchor_slug = cid.removeprefix("cluster-") or cid
        start_idx = len(existing) + 1
        for i in range(n_needed):
            node_id = f"node-{anchor_slug}-{start_idx + i}"
            new_nodes.append(KGNode(
                node_id=node_id,
                node_type=NodeType.TECHNICAL_RESOURCE,
                technical_subtype="deployment",
                label=f"{cluster.label} node-{start_idx + i}",
                attributes={
                    "kind":          "kubenode",
                    "cluster_id":    cid,
                    "instance_type": "m6i.xlarge",
                    "node_index":    start_idx + i,
                    "synthetic":     True,
                    "source":        "kube_quota",
                },
            ))
            new_edges.append(KGEdge(
                edge_id=_next_edge(),
                edge_type=EdgeType.CONTAINS,
                from_node_id=cid, to_node_id=node_id,
            ))


def expand_with_synthetic_infra(
    plan: DemoPlan,
    *,
    pods_per_service: tuple[int, int] = (1, 2),
    nodes_per_cluster: tuple[int, int] = (3, 6),
    vms_per_cluster: tuple[int, int] = (1, 2),
    min_stores: int = 4,
) -> KnowledgeGraph:
    """Return a NEW expanded KG: original + synthetic stores (if needed) +
    per-store edge clusters + central-cluster pods/VMs from the original plan.
    """
    original = plan.knowledge_graph
    rng = _seeded_rng(str(plan.plan_id), salt="kg_expand")
    edge_seq = [9000]

    new_nodes: list[KGNode] = list(original.nodes)
    new_edges: list[KGEdge] = list(original.edges)

    # ── 1. Synthesize additional stores up to min_stores ──
    extra_store_nodes, extra_store_edges = _synthesize_extra_stores(
        plan, rng, min_stores
    )
    new_nodes.extend(extra_store_nodes)
    new_edges.extend(extra_store_edges)

    # ── 2. Per-store edge clusters (for ALL stores, original + synthesized) ──
    all_stores = [n for n in new_nodes if n.business_subtype == "store"]
    for store in all_stores:
        store_nodes, store_edges = _build_store_cluster(store, rng, edge_seq)
        new_nodes.extend(store_nodes)
        new_edges.extend(store_edges)

    # ── 3. Central-cluster service metadata + node provisioning ──
    # Pre-quota era we ALSO spawned 1-2 pods per central service here.
    # That's been removed: with 50+ services on the central cluster, that
    # produced 50-100 pods on one cluster and any pods-per-node cap had
    # no chance. The quota step (`_ensure_pod_quota_per_cluster`) is now
    # the sole pod creator and round-robins services across all 5+
    # clusters, giving realistic multi-region service deployments while
    # respecting the [PODS_PER_NODE_MIN, PODS_PER_NODE_MAX] bound.
    central_clusters = [n for n in original.nodes if n.technical_subtype == "cluster"]
    services = [n for n in original.nodes if n.technical_subtype == "service"]
    namespaces = [n for n in original.nodes if n.technical_subtype == "namespace"]
    ns_ids = {n.node_id for n in namespaces}
    service_to_ns: dict[str, str] = {}
    for e in original.edges:
        if e.from_node_id in ns_ids and any(s.node_id == e.to_node_id for s in services):
            service_to_ns[e.to_node_id] = e.from_node_id
    fallback_ns = namespaces[0].node_id if namespaces else None
    central_cluster_id = central_clusters[0].node_id if central_clusters else None

    # Write resolved namespace_id + cluster_id back onto each central service
    # node's attributes. Without this, red_emitter's per-service Resource has
    # nothing to read for `service.namespace` / `kube.cluster`, so target_info
    # falls back to namespace="default". The quota step uses `service.namespace_id`
    # to label the pods it creates so each pod inherits the right namespace.
    for svc in services:
        ns_id = service_to_ns.get(svc.node_id, fallback_ns)
        if ns_id and "namespace_id" not in svc.attributes:
            svc.attributes["namespace_id"] = ns_id
        if central_cluster_id and "cluster_id" not in svc.attributes:
            svc.attributes["cluster_id"] = central_cluster_id

    # Central cluster k8s nodes (built-in Node)
    for cluster in central_clusters:
        n_nodes = rng.randint(*nodes_per_cluster)
        for i in range(n_nodes):
            edge_seq[0] += 1
            node_id = f"node-{cluster.node_id.removeprefix('cluster-')}-{i+1}"
            new_nodes.append(KGNode(
                node_id=node_id,
                node_type=NodeType.TECHNICAL_RESOURCE,
                technical_subtype="deployment",
                label=f"{cluster.label} node-{i+1}",
                attributes={
                    "kind": "kubenode",
                    "cluster_id": cluster.node_id,
                    "instance_type": rng.choice(["m6i.xlarge", "m6i.2xlarge", "c6i.xlarge"]),
                    "node_index": i + 1,
                },
            ))
            new_edges.append(KGEdge(
                edge_id=f"edge-{edge_seq[0]:04d}",
                edge_type=EdgeType.CONTAINS,
                from_node_id=cluster.node_id, to_node_id=node_id,
            ))

    # Central VMs (kept; per-store assignment still applies for visualisation)
    stores_for_vm = all_stores[:]
    for cluster in central_clusters:
        n_vms = rng.randint(*vms_per_cluster)
        purposes = rng.sample(
            ["bastion", "monitoring", "ci-runner", "backup", "log-aggregator"],
            k=min(n_vms, 5),
        )
        for i, purpose in enumerate(purposes):
            edge_seq[0] += 1
            vm_id = f"vm-{cluster.node_id.removeprefix('cluster-')}-{purpose}"
            assigned_store = stores_for_vm[i % len(stores_for_vm)] if stores_for_vm else None
            new_nodes.append(KGNode(
                node_id=vm_id,
                node_type=NodeType.TECHNICAL_RESOURCE,
                technical_subtype="external_dependency",
                label=f"{purpose.title()} VM",
                attributes={
                    "kind": "vm",
                    "cluster_id": cluster.node_id,
                    "store_id": assigned_store.node_id if assigned_store else "",
                    "purpose": purpose,
                    "instance_type": rng.choice(["t3.medium", "t3.large"]),
                },
            ))
            new_edges.append(KGEdge(
                edge_id=f"edge-{edge_seq[0]:04d}",
                edge_type=EdgeType.INTEGRATES_WITH,
                from_node_id=cluster.node_id, to_node_id=vm_id,
            ))
            if assigned_store is not None:
                edge_seq[0] += 1
                new_edges.append(KGEdge(
                    edge_id=f"edge-{edge_seq[0]:04d}",
                    edge_type=EdgeType.CONTAINS,
                    from_node_id=assigned_store.node_id, to_node_id=vm_id,
                ))

    # ── 4. Customer-wide K8s quota (the v0.7+ "must-have" topology) ──
    # Up to this point the cluster count is whatever the planner produced
    # (typically 1) plus 1-per-store from the per-store synth above. Many
    # verticals have no stores so they bottom out at 1 cluster — too thin
    # to populate the entity graph. Top up to MIN_CLUSTERS_PER_CUSTOMER,
    # anchoring the new clusters at the most-physical existing entity in
    # the plan (store → FC → BU → region → brand). If the plan doesn't
    # have enough physical anchors, we synth `region`-typed cloud-region
    # entities ("us-east-1" etc.) — schema-valid and reads naturally as
    # "cluster runs in <region>" for SaaS / airline / financial verticals.
    existing_clusters = [n for n in new_nodes if n.technical_subtype == "cluster"]
    n_clusters_needed = max(0, MIN_CLUSTERS_PER_CUSTOMER - len(existing_clusters))
    if n_clusters_needed > 0:
        # Don't double-anchor: any entity already hosting a cluster is excluded.
        used_anchor_ids: set[str] = set()
        for c in existing_clusters:
            for e in new_edges:
                if e.to_node_id == c.node_id and e.edge_type == EdgeType.RUNS_ON:
                    used_anchor_ids.add(e.from_node_id)
            anchor_id = c.attributes.get("anchor_id") or c.attributes.get("store_id")
            if anchor_id:
                used_anchor_ids.add(anchor_id)

        anchors, synth_anchors = _pick_cluster_anchors(
            plan,
            new_nodes,
            n_needed=n_clusters_needed,
            used_anchor_ids=used_anchor_ids,
        )
        # Append any synth anchors first so the cluster build can find them
        new_nodes.extend(synth_anchors)
        # Synth cloud-region anchors: hang them off the customer's existing
        # region tree if there's a sensible parent. None today, so they live
        # at the top level (parent-less business entities are fine — the
        # entity graph just shows them as roots).
        customer_slug = (plan.source_profile_id or "").removeprefix("prof-").strip("-").lower()
        for anchor in anchors:
            cluster_nodes, cluster_edges = _build_cluster_at_anchor(
                anchor,
                customer=customer_slug or "clarion",
                edge_seq=edge_seq,
            )
            new_nodes.extend(cluster_nodes)
            new_edges.extend(cluster_edges)

    # ── 5. Normalize node count per cluster (≥ NODES_PER_CLUSTER) ──
    # The planner's central cluster + per-store clusters may have produced
    # fewer than 3 nodes (the per-store builder uses randint(2,3) → can be
    # 2). Top each one up so the round-robin pod-to-node assignment doesn't
    # produce nodes with <2 pods.
    _ensure_node_quota_per_cluster(new_nodes, new_edges, edge_seq)

    # ── 6. Normalize pod count per cluster (in [3*MIN, 3*MAX] = [6, 15]) ──
    # Pods are bound to existing services (service_id label on the pod is
    # what `Service METRICS Pod` matches on) so we get realistic Service↔Pod
    # joins in the entity graph regardless of vertical. `_attach_pod_to_node`
    # in the emitter will round-robin distribute them across the cluster's
    # 3 nodes, landing each node in [PODS_PER_NODE_MIN, PODS_PER_NODE_MAX].
    rng_quota = _seeded_rng(str(plan.plan_id), salt="kube_quota")
    _ensure_pod_quota_per_cluster(new_nodes, new_edges, rng_quota, edge_seq)

    # ── 7. Cloud platform variety on top of K8s ──
    # Stamp each cluster with a cloud_provider so the entity graph has a
    # CloudProvider → KubeCluster tier that mirrors how a real customer's
    # multi-cloud topology looks. With 5 clusters and 5 providers in
    # rotation, each customer naturally hits AWS, Azure, GCP, OpenShift,
    # and Rancher (deterministic per plan_id).
    _assign_cloud_providers(new_nodes, plan)

    # ── 8. Top up service→database depends_on coverage ──
    # The Service USES Database model rule needs at least one
    # `clarion_service_database_affinity` series per (service, database)
    # pair. Most plans only have a handful of explicit depends_on edges
    # from the planner; this step makes sure every Service is connected
    # to at least one Database, with affinity-based pairing where
    # possible (svc-search → db-search, svc-billing → db-billing) and
    # round-robin otherwise.
    rng_dbtopo = _seeded_rng(str(plan.plan_id), salt="service_db_topology")
    _ensure_service_database_topology(new_nodes, new_edges, rng_dbtopo, edge_seq)

    return KnowledgeGraph(nodes=new_nodes, edges=new_edges)


def expansion_summary(original: KnowledgeGraph, expanded: KnowledgeGraph) -> dict[str, Any]:
    """Diagnostic helper for the CLI."""
    new_count = len(expanded.nodes) - len(original.nodes)
    by_kind: dict[str, int] = {}
    for n in expanded.nodes:
        kind = n.attributes.get("kind") or n.business_subtype or n.technical_subtype or "?"
        by_kind[kind] = by_kind.get(kind, 0) + 1
    return {
        "original_nodes": len(original.nodes),
        "expanded_nodes": len(expanded.nodes),
        "synth_nodes_added": new_count,
        "by_kind": by_kind,
        "synth_edges_added": len(expanded.edges) - len(original.edges),
    }
