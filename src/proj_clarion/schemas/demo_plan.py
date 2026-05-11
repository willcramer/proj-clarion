"""DemoPlan — the Plan agent's output. SE review gate sits on this."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from proj_clarion.schemas.incident_script import IncidentScript
from proj_clarion.schemas.knowledge_graph import KnowledgeGraph


class TargetAudience(str, Enum):
    BUSINESS = "business"
    TECHNICAL = "technical"
    PIVOT = "pivot"


class ReviewState(str, Enum):
    DRAFT = "draft"
    SE_REVIEWED = "se_reviewed"
    APPROVED_FOR_PROVISION = "approved_for_provision"
    PROVISIONED = "provisioned"
    TORN_DOWN = "torn_down"


class BusinessStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    step_id: str = Field(..., pattern=r"^step-[a-z0-9-]+$")
    name: str
    kpi: str = Field(..., description="The single KPI that measures success at this step")
    services_implementing: list[str] = Field(
        default_factory=list,
        description="KGNode ids of services that implement this step",
    )


class FailureMode(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    description: str
    affects_steps: list[str] = Field(default_factory=list)


class BusinessProcessModel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    process_id: str = Field(..., pattern=r"^proc-[a-z0-9-]+$")
    name: str
    description: str
    business_steps: list[BusinessStep]
    kpis: list[str] = Field(default_factory=list)
    failure_modes: list[FailureMode] = Field(default_factory=list)


class InfrastructureBlueprint(BaseModel):
    """What we'd provision if/when v0.5 lands. For v0.1 this informs generated telemetry shape."""

    model_config = ConfigDict(extra="forbid")

    cluster_count: int = Field(1, ge=0, le=10)
    namespaces: list[str] = Field(default_factory=list)
    services: list[str] = Field(default_factory=list)
    databases: list[str] = Field(default_factory=list)
    queues: list[str] = Field(default_factory=list)
    external_dependencies: list[str] = Field(default_factory=list)
    agentic_workloads: list[str] = Field(default_factory=list)


class DataBlueprint(BaseModel):
    """Tells the generator what to produce."""

    model_config = ConfigDict(extra="forbid")

    historical_window_days: int = Field(14, ge=1, le=90)
    live_tail_minutes: int = Field(30, ge=5, le=120)
    business_event_volume_per_day: int = Field(..., ge=100)
    diurnal_pattern: Literal["retail_us", "retail_global", "saas_b2b", "ecommerce_us", "flat"]
    weekly_pattern: Literal["weekend_heavy", "weekday_heavy", "flat"]
    store_count: int = Field(0, ge=0)
    region_count: int = Field(0, ge=0)
    channel_count: int = Field(0, ge=0)


class DashboardSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    dashboard_id: str = Field(..., pattern=r"^dash-[a-z0-9-]+$")
    title: str
    audience: TargetAudience
    primary_panels: list[str] = Field(default_factory=list)
    folder: str = "Proj Clarion"


class AlertSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    alert_id: str = Field(..., pattern=r"^alrt-[a-z0-9-]+$")
    title: str
    business_subject_line: str = Field(
        ..., description="The headline a CFO would read. Money first."
    )
    technical_subject_line: str = Field(
        ..., description="The headline an SRE would read. Service first."
    )
    datasource_kind: Literal["postgres", "prometheus", "loki"]
    query: str
    threshold_predicate: str = Field(..., description="e.g. '> 0.05' or 'rate > 100'")
    severity: Literal["critical", "high", "medium", "low"]
    routes_to: list[str] = Field(default_factory=list)


class AssistantTool(BaseModel):
    """Named SQL views Grafana Assistant is allowed to use."""

    model_config = ConfigDict(extra="forbid")

    tool_name: str = Field(..., pattern=r"^[a-z][a-z0-9_]+$")
    description: str
    sql: str
    sample_questions: list[str] = Field(default_factory=list)


class CostEnvelope(BaseModel):
    model_config = ConfigDict(extra="forbid")

    estimated_usd_per_demo: float = Field(..., ge=0)
    ttl_hours: int = Field(8, ge=1, le=168)
    hard_ceiling_usd: float = Field(..., gt=0)


class Branding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer_facing: bool = Field(False, description="If False, mask customer name as 'Demo Co.'")
    display_name: str = Field("Demo Co.")
    banner_text: str = Field(
        "Illustrative demo. Built from public information. "
        "Synthetic data; no instrumentation of customer systems."
    )
    enable_logo: bool = False


class DemoPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_id: UUID
    schema_version: Literal["0.1.0"] = "0.1.0"
    created_at: datetime
    source_profile_id: str
    target_audience: TargetAudience
    narrative: str = Field(..., description="One paragraph the SE will rehearse")

    business_process_models: list[BusinessProcessModel]
    infrastructure_blueprint: InfrastructureBlueprint
    data_blueprint: DataBlueprint
    incident_script: IncidentScript
    knowledge_graph: KnowledgeGraph

    dashboard_specs: list[DashboardSpec]
    alert_specs: list[AlertSpec]
    assistant_tools: list[AssistantTool]

    cost_envelope: CostEnvelope
    branding: Branding = Field(default_factory=Branding)
    review_state: ReviewState = ReviewState.DRAFT
