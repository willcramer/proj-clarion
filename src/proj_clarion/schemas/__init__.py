"""Pydantic schemas — the contracts between pipeline stages.

These are the four artifacts from the design doc, expressed as runnable
Python types. The JSON serialization of any of these models is the
on-disk format.

- CompanyProfile: output of the Research agent
- DemoPlan: output of the Plan agent (contains the others below)
- BusinessProcessModel, KnowledgeGraph, IncidentScript: nested in DemoPlan
"""

from proj_clarion.schemas.company_profile import (
    AgenticSignal,
    Channel,
    CompanyProfile,
    GeographicFootprint,
    IncumbentObservability,
    OrgArchetype,
    OrganizationalModel,
    PainSignal,
    Provenance,
    RevenueSignals,
    StrategicPriority,
    SynthesizedFlag,
    TechStackSignal,
)
from proj_clarion.schemas.demo_plan import (
    AlertSpec,
    AssistantTool,
    BusinessProcessModel,
    BusinessStep,
    CostEnvelope,
    DashboardSpec,
    DataBlueprint,
    DemoPlan,
    InfrastructureBlueprint,
    ReviewState,
    TargetAudience,
)
from proj_clarion.schemas.incident_script import (
    EventType,
    IncidentEvent,
    IncidentScript,
)
from proj_clarion.schemas.knowledge_graph import (
    EdgeType,
    KGEdge,
    KGNode,
    KnowledgeGraph,
    NodeType,
)

__all__ = [
    "AgenticSignal",
    "AlertSpec",
    "AssistantTool",
    "BusinessProcessModel",
    "BusinessStep",
    "Channel",
    "CompanyProfile",
    "CostEnvelope",
    "DashboardSpec",
    "DataBlueprint",
    "DemoPlan",
    "EdgeType",
    "EventType",
    "GeographicFootprint",
    "IncidentEvent",
    "IncidentScript",
    "IncumbentObservability",
    "InfrastructureBlueprint",
    "KGEdge",
    "KGNode",
    "KnowledgeGraph",
    "NodeType",
    "OrgArchetype",
    "OrganizationalModel",
    "PainSignal",
    "Provenance",
    "ReviewState",
    "RevenueSignals",
    "StrategicPriority",
    "SynthesizedFlag",
    "TargetAudience",
    "TechStackSignal",
]
