"""End-to-end integration test: profile → planner (mocked LLM) → DB persist
→ DB read → schema re-validates.

The planner LLM calls are still mocked (we don't burn tokens in CI), but
everything below the LLM is real: SQLAlchemy, psycopg, Postgres, and the
schema/integrity checks the brief calls out.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import patch

import pytest
from sqlalchemy import Engine

from proj_clarion.agents import planner
from proj_clarion.schemas import CompanyProfile, DemoPlan
from proj_clarion.storage import (
    AuditRepo,
    KGRepo,
    PlanRepo,
    ProfileRepo,
    apply_migrations,
    session_scope,
)

from tests.unit.test_planner import (  # reuse the canned LLM responses
    _ANALYZE_RESPONSE,
    _BUILD_KG_RESPONSE,
    _DASHBOARDS_RESPONSE,
    _INCIDENT_RESPONSE,
    _TOOLS_RESPONSE,
    _FakeClient,
    _process_id,
    _process_response,
)

FIXTURES = Path(__file__).parent.parent / "fixtures"

pytestmark = pytest.mark.integration


@pytest.fixture()
def acme_retail_profile() -> CompanyProfile:
    raw = json.loads((FIXTURES / "acme_retail_profile.json").read_text())
    return CompanyProfile.model_validate(raw)


def _build_fake_client() -> _FakeClient:
    queue = [
        _ANALYZE_RESPONSE,
        _process_response("D2C Web Checkout",
                          ["svc-checkout", "svc-fulfillment"], _process_id("d2c-checkout")),
        _process_response("Retail POS",
                          ["svc-pos", "svc-wms-bridge"], _process_id("retail-pos")),
        _process_response("Wholesale Order Routing",
                          ["svc-wms-bridge", "svc-fulfillment"], _process_id("wholesale-orders")),
        _process_response("Order Fulfillment",
                          ["svc-fulfillment", "svc-wms-bridge"], _process_id("fulfillment")),
        _BUILD_KG_RESPONSE,
        _INCIDENT_RESPONSE,
        _DASHBOARDS_RESPONSE,
        _TOOLS_RESPONSE,
    ]
    return _FakeClient(queue)


def test_full_pipeline_profile_to_plan_to_db_to_back(
    engine: Engine, acme_retail_profile: CompanyProfile
) -> None:
    apply_migrations(engine)

    fake = _build_fake_client()
    with patch.object(planner, "_client", lambda: fake):
        state = asyncio.run(planner.run_plan(acme_retail_profile))

    plan = state.get("plan")
    assert plan is not None, f"errors: {state.get('errors')}"
    assert isinstance(plan, DemoPlan)

    # Persist
    with session_scope() as s:
        ProfileRepo().upsert(s, acme_retail_profile)
        PlanRepo().upsert(s, plan)
        KGRepo().replace(s, plan.plan_id, plan.knowledge_graph)
        AuditRepo().record(
            s, plan.plan_id, actor="test", action="created",
            to_state=plan.review_state.value,
            note="full pipeline test",
        )

    # Read back independently
    with session_scope() as s:
        plan_back = PlanRepo().get(s, plan.plan_id)
        kg_back = KGRepo().graph_for_plan(s, plan.plan_id)
        history = AuditRepo().history(s, plan.plan_id)

    assert plan_back is not None
    # Re-validate the round-tripped plan against the schema
    revalidated = DemoPlan.model_validate(json.loads(plan_back.model_dump_json()))
    assert revalidated.plan_id == plan.plan_id
    assert revalidated.source_profile_id == acme_retail_profile.profile_id
    assert len(revalidated.business_process_models) == len(plan.business_process_models)

    # KG round-trip preserves the graph; integrity holds
    assert len(kg_back.nodes) == len(plan.knowledge_graph.nodes)
    assert len(kg_back.edges) == len(plan.knowledge_graph.edges)
    assert kg_back.validate_referential_integrity() == []

    # Audit captured the creation
    assert len(history) == 1
    assert history[0][2] == "created"
