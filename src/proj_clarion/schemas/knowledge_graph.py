"""KnowledgeGraph schema — two-tier graph stored in Postgres as kg_nodes and kg_edges."""

from __future__ import annotations

from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class NodeType(str, Enum):
    BUSINESS_ENTITY = "business_entity"
    TECHNICAL_RESOURCE = "technical_resource"
    AGENTIC_RESOURCE = "agentic_resource"


class EdgeType(str, Enum):
    RUNS_ON = "runs_on"
    DEPENDS_ON = "depends_on"
    INTEGRATES_WITH = "integrates_with"
    SERVES = "serves"
    CONTAINS = "contains"


class LiveStateBinding(BaseModel):
    """A query template Grafana evaluates at render time to color the node."""

    model_config = ConfigDict(extra="forbid")

    datasource_kind: Literal["postgres", "prometheus", "loki", "tempo"]
    query_template: str = Field(
        ...,
        description=(
            "SQL or PromQL/LogQL/TraceQL with {{node_id}} placeholders. "
            "Returns a single numeric value or status string."
        ),
    )
    healthy_when: str = Field(
        ...,
        description="Predicate, e.g. 'value < 0.05' or \"status == 'OK'\"",
    )


class KGNode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    node_id: str = Field(..., pattern=r"^[a-z0-9][a-z0-9_-]*$")
    node_type: NodeType
    business_subtype: Literal[
        "store",
        "region",
        "channel",
        "product_line",
        "fulfillment_center",
        "business_unit",
        "brand",
        "partner_program",
    ] | None = None
    technical_subtype: Literal[
        "cluster",
        "namespace",
        "service",
        "deployment",
        "database",
        "queue",
        "external_dependency",
    ] | None = None
    agentic_subtype: Literal[
        "agent",
        "tool",
        "model",
        "vector_index",
    ] | None = None

    label: str = Field(..., description="Human-readable name shown in the UI")
    attributes: dict[str, Any] = Field(default_factory=dict)
    live_state_binding: LiveStateBinding | None = None


class KGEdge(BaseModel):
    model_config = ConfigDict(extra="forbid")

    edge_id: str = Field(..., pattern=r"^edge-[a-z0-9-]+$")
    edge_type: EdgeType
    from_node_id: str
    to_node_id: str
    attributes: dict[str, Any] = Field(default_factory=dict)


class KnowledgeGraph(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nodes: list[KGNode] = Field(default_factory=list)
    edges: list[KGEdge] = Field(default_factory=list)

    def validate_referential_integrity(self) -> list[str]:
        """Return a list of validation errors. Empty list = healthy graph."""
        errors: list[str] = []
        node_ids = {n.node_id for n in self.nodes}
        for e in self.edges:
            if e.from_node_id not in node_ids:
                errors.append(f"edge {e.edge_id}: from_node_id '{e.from_node_id}' not found")
            if e.to_node_id not in node_ids:
                errors.append(f"edge {e.edge_id}: to_node_id '{e.to_node_id}' not found")
        return errors
