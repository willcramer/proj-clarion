"""KG → call graph helpers.

Each business event flows through a sequence of services per the plan's KG.
Given a process step that names `services_implementing` (a list of KG node_ids
for services), we walk the KG's `depends_on` and `integrates_with` edges to
build the downstream call chain. That chain becomes the trace shape: each
service is a child span of the previous one.

We also collect the business-entity context (region/channel/store) by walking
`serves` and `contains` edges upward from the same services.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable

from proj_clarion.schemas import EdgeType, KGEdge, KGNode, KnowledgeGraph, NodeType


def _index_edges(kg: KnowledgeGraph) -> dict[tuple[str, str], list[KGEdge]]:
    """Index edges by (from_node_id, edge_type) for fast lookup."""
    idx: dict[tuple[str, str], list[KGEdge]] = defaultdict(list)
    for e in kg.edges:
        idx[(e.from_node_id, e.edge_type.value)].append(e)
    return idx


def service_chain_for_step(
    kg: KnowledgeGraph,
    services_implementing: Iterable[str],
    *,
    max_depth: int = 6,
) -> list[str]:
    """Return the ordered service-call chain starting from the entry services.

    Walks `depends_on` and `integrates_with` edges in BFS order, collecting
    unique service / database / queue / external_dependency nodes. Truncates
    at `max_depth` total hops so a span tree stays readable.
    """
    edge_idx = _index_edges(kg)
    nodes_by_id = {n.node_id: n for n in kg.nodes}

    chain: list[str] = []
    visited: set[str] = set()
    queue: list[str] = [s for s in services_implementing if s in nodes_by_id]
    while queue and len(chain) < max_depth:
        current = queue.pop(0)
        if current in visited:
            continue
        visited.add(current)
        chain.append(current)
        for edge_type in (EdgeType.DEPENDS_ON.value, EdgeType.INTEGRATES_WITH.value):
            for edge in edge_idx.get((current, edge_type), []):
                if edge.to_node_id not in visited:
                    queue.append(edge.to_node_id)
    return chain


def business_context_for_services(
    kg: KnowledgeGraph,
    services: Iterable[str],
) -> list[str]:
    """Find the business_entity nodes that `serve` (point to) any of the given
    services. Returns up to 3 representative entity node_ids per service —
    typically a region, a channel, and a store/center.
    """
    serves_by_to: dict[str, list[str]] = defaultdict(list)
    for e in kg.edges:
        if e.edge_type.value == EdgeType.SERVES.value:
            serves_by_to[e.to_node_id].append(e.from_node_id)

    nodes_by_id = {n.node_id: n for n in kg.nodes}
    out: list[str] = []
    seen: set[str] = set()
    for svc in services:
        for entity_id in serves_by_to.get(svc, []):
            if entity_id in seen or entity_id not in nodes_by_id:
                continue
            node = nodes_by_id[entity_id]
            if node.node_type == NodeType.BUSINESS_ENTITY:
                out.append(entity_id)
                seen.add(entity_id)
    return out


def select_entities_by_subtype(
    kg: KnowledgeGraph, subtypes: Iterable[str]
) -> dict[str, list[KGNode]]:
    """Group business_entity nodes by business_subtype. Useful for picking
    a (region, channel, store) tuple per generated event.
    """
    out: dict[str, list[KGNode]] = defaultdict(list)
    target = set(subtypes)
    for n in kg.nodes:
        if n.node_type == NodeType.BUSINESS_ENTITY and n.business_subtype in target:
            out[n.business_subtype].append(n)
    return out
