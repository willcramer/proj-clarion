"""Dashboard summary endpoint — aggregate counts the UI's stat cards read."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import text

from proj_clarion.storage import session_scope

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


class DashboardSummary(BaseModel):
    profiles_total: int
    plans_total: int
    plans_by_state: dict[str, int]
    kg_nodes_total: int
    kg_edges_total: int
    business_events_total: int
    last_event_at: datetime | None


@router.get("/summary", response_model=DashboardSummary)
def summary() -> DashboardSummary:
    """One round-trip aggregate. Cheap reads against the existing tables."""
    with session_scope() as s:
        profiles_total = s.execute(
            text("SELECT COUNT(*) FROM company_profiles")
        ).scalar_one()
        plans_total = s.execute(text("SELECT COUNT(*) FROM demo_plans")).scalar_one()
        states = s.execute(text(
            "SELECT review_state, COUNT(*) FROM demo_plans GROUP BY review_state"
        )).fetchall()
        plans_by_state = {row[0]: int(row[1]) for row in states}
        kg_nodes = s.execute(text("SELECT COUNT(*) FROM kg_nodes")).scalar_one()
        kg_edges = s.execute(text("SELECT COUNT(*) FROM kg_edges")).scalar_one()
        events_total = s.execute(text("SELECT COUNT(*) FROM business_events")).scalar_one()
        last_event = s.execute(
            text("SELECT MAX(ts) FROM business_events")
        ).scalar_one()

    return DashboardSummary(
        profiles_total=int(profiles_total),
        plans_total=int(plans_total),
        plans_by_state=plans_by_state,
        kg_nodes_total=int(kg_nodes),
        kg_edges_total=int(kg_edges),
        business_events_total=int(events_total),
        last_event_at=last_event,
    )
