"""Storage layer integration test — applies migrations and round-trips models
through the repos against an ephemeral Postgres container.
"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import Engine, text

from proj_clarion.schemas import (
    AlertSpec,
    AssistantTool,
    BusinessProcessModel,
    BusinessStep,
    CompanyProfile,
    CostEnvelope,
    DashboardSpec,
    DataBlueprint,
    DemoPlan,
    EdgeType,
    IncidentEvent,
    IncidentScript,
    InfrastructureBlueprint,
    KGEdge,
    KGNode,
    KnowledgeGraph,
    NodeType,
    ReviewState,
    TargetAudience,
)
from proj_clarion.schemas.demo_plan import FailureMode
from proj_clarion.storage import (
    AuditRepo,
    KGRepo,
    PlanRepo,
    ProfileRepo,
    apply_migrations,
    session_scope,
)

FIXTURES = Path(__file__).parent.parent / "fixtures"

pytestmark = pytest.mark.integration


@pytest.fixture()
def acme_retail_profile() -> CompanyProfile:
    raw = json.loads((FIXTURES / "acme_retail_profile.json").read_text())
    return CompanyProfile.model_validate(raw)


def _minimal_demo_plan(profile_id: str) -> DemoPlan:
    """A small, valid DemoPlan we can use for round-trip testing without LLM calls."""
    kg = KnowledgeGraph(
        nodes=[
            KGNode(node_id="region-na", node_type=NodeType.BUSINESS_ENTITY,
                   business_subtype="region", label="North America"),
            KGNode(node_id="svc-checkout", node_type=NodeType.TECHNICAL_RESOURCE,
                   technical_subtype="service", label="checkout-svc"),
        ],
        edges=[
            KGEdge(edge_id="edge-001", edge_type=EdgeType.SERVES,
                   from_node_id="region-na", to_node_id="svc-checkout"),
        ],
    )
    process = BusinessProcessModel(
        process_id="proc-checkout",
        name="Checkout",
        description="Cart through payment",
        business_steps=[
            BusinessStep(step_id="step-001", name="Add to cart", kpi="Sessions",
                         services_implementing=["svc-checkout"]),
        ],
        kpis=["Conversion"],
        failure_modes=[
            FailureMode(name="Latency spike", description="Slow", affects_steps=["step-001"]),
        ],
    )
    return DemoPlan(
        plan_id=uuid4(),
        created_at=__import__("datetime").datetime.now(__import__("datetime").UTC),
        source_profile_id=profile_id,
        target_audience=TargetAudience.PIVOT,
        narrative="Test plan",
        business_process_models=[process],
        infrastructure_blueprint=InfrastructureBlueprint(services=["svc-checkout"]),
        data_blueprint=DataBlueprint(
            business_event_volume_per_day=1000,
            diurnal_pattern="retail_us",
            weekly_pattern="weekend_heavy",
        ),
        incident_script=IncidentScript(
            script_id="scr-test", title="Test incident",
            total_duration_minutes=15, arming_mode="historical_replay",
            events=[
                IncidentEvent(
                    event_id="evt-001", offset_seconds=240,
                    target_kind="service", target_id="svc-checkout",
                    event_type="latency_spike", magnitude=3.0,
                    recovery_offset_seconds=600,
                    narrator_cue="Click into the trace",
                ),
            ],
        ),
        knowledge_graph=kg,
        dashboard_specs=[
            DashboardSpec(dashboard_id="dash-bh", title="Business Health",
                          audience=TargetAudience.BUSINESS, primary_panels=["Revenue"]),
        ],
        alert_specs=[
            AlertSpec(alert_id="alrt-checkout", title="Checkout latency",
                      business_subject_line="Conversions falling",
                      technical_subject_line="checkout-svc p95 high",
                      datasource_kind="prometheus", query="x", threshold_predicate="> 1",
                      severity="high", routes_to=["#oncall"]),
        ],
        assistant_tools=[
            AssistantTool(tool_name="store_health_today",
                          description="Per-store today",
                          sql="SELECT 1 WHERE plan_id = $1",
                          sample_questions=["How is store X today?"]),
        ],
        cost_envelope=CostEnvelope(estimated_usd_per_demo=2.0, hard_ceiling_usd=10.0),
    )


# ============================================================
# Migrations
# ============================================================

def test_migrations_apply_then_idempotent(engine: Engine) -> None:
    first = apply_migrations(engine)
    assert "0001_initial.sql" in first

    # Tables exist
    with engine.connect() as conn:
        for tbl in ("company_profiles", "demo_plans", "kg_nodes", "kg_edges",
                    "business_events", "plan_audit_log", "_migrations"):
            count = conn.execute(text(
                f"SELECT count(*) FROM information_schema.tables WHERE table_name = '{tbl}'"
            )).scalar()
            assert count == 1, f"missing table {tbl}"

    second = apply_migrations(engine)
    assert second == [], "second apply should be a no-op"


# ============================================================
# Repo round-trips
# ============================================================

def test_profile_round_trip(engine: Engine, acme_retail_profile: CompanyProfile) -> None:
    apply_migrations(engine)
    with session_scope() as s:
        repo = ProfileRepo()
        repo.upsert(s, acme_retail_profile)
        out = repo.get(s, acme_retail_profile.profile_id)
    assert out is not None
    assert out.company.name == acme_retail_profile.company.name
    assert len(out.channels) == len(acme_retail_profile.channels)
    assert len(out.provenance) == len(acme_retail_profile.provenance)


def test_plan_round_trip_persists_kg_and_audit(
    engine: Engine, acme_retail_profile: CompanyProfile
) -> None:
    apply_migrations(engine)
    plan = _minimal_demo_plan(acme_retail_profile.profile_id)

    with session_scope() as s:
        ProfileRepo().upsert(s, acme_retail_profile)
        PlanRepo().upsert(s, plan)
        KGRepo().replace(s, plan.plan_id, plan.knowledge_graph)
        AuditRepo().record(
            s, plan.plan_id, actor="test", action="created",
            to_state="draft", note="round-trip test",
        )

    with session_scope() as s:
        out = PlanRepo().get(s, plan.plan_id)
        kg_back = KGRepo().graph_for_plan(s, plan.plan_id)
        history = AuditRepo().history(s, plan.plan_id)

    assert out is not None
    assert out.source_profile_id == acme_retail_profile.profile_id
    assert out.review_state == ReviewState.DRAFT
    # KG round-trip preserves nodes/edges
    assert len(kg_back.nodes) == len(plan.knowledge_graph.nodes)
    assert len(kg_back.edges) == len(plan.knowledge_graph.edges)
    assert kg_back.validate_referential_integrity() == []
    # Audit recorded
    assert len(history) == 1
    assert history[0][1] == "test"  # actor
    assert history[0][2] == "created"


def test_plan_review_state_transition(
    engine: Engine, acme_retail_profile: CompanyProfile
) -> None:
    apply_migrations(engine)
    plan = _minimal_demo_plan(acme_retail_profile.profile_id)
    with session_scope() as s:
        ProfileRepo().upsert(s, acme_retail_profile)
        PlanRepo().upsert(s, plan)
        KGRepo().replace(s, plan.plan_id, plan.knowledge_graph)

    with session_scope() as s:
        prev = PlanRepo().set_review_state(s, plan.plan_id, ReviewState.APPROVED_FOR_PROVISION)
    assert prev == "draft"

    with session_scope() as s:
        out = PlanRepo().get(s, plan.plan_id)
    assert out is not None
    assert out.review_state == ReviewState.APPROVED_FOR_PROVISION


def test_kg_replace_removes_stale_nodes(
    engine: Engine, acme_retail_profile: CompanyProfile
) -> None:
    apply_migrations(engine)
    plan = _minimal_demo_plan(acme_retail_profile.profile_id)
    with session_scope() as s:
        ProfileRepo().upsert(s, acme_retail_profile)
        PlanRepo().upsert(s, plan)
        KGRepo().replace(s, plan.plan_id, plan.knowledge_graph)

    smaller = KnowledgeGraph(
        nodes=[KGNode(node_id="region-na", node_type=NodeType.BUSINESS_ENTITY,
                      business_subtype="region", label="NA only")],
        edges=[],
    )
    with session_scope() as s:
        KGRepo().replace(s, plan.plan_id, smaller)
        kg_back = KGRepo().graph_for_plan(s, plan.plan_id)
    assert len(kg_back.nodes) == 1
    assert len(kg_back.edges) == 0
