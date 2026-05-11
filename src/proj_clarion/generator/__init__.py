"""Generator — turns a DemoPlan into synthetic data.

For v0.3 the deliverables are:
- `business_events` rows in Postgres for the plan's full historical window,
  deterministically seeded from `plan_id` so re-runs reproduce.
- OTel traces emitted to Cloud Tempo, one trace per event, with backdated
  timestamps so the dashboard renders 14 days of "history".

Metrics and logs land in v0.5 alongside Alloy + the live tail. The generated
events table is dashboard-first: the v0.4 dashboards query Postgres directly
for business KPIs, so business signals work without a metrics pipeline.
"""

from proj_clarion.generator.events import generate_events_for_plan
from proj_clarion.generator.incident import apply_incident_script
from proj_clarion.generator.telemetry import emit_traces_for_events
from proj_clarion.generator.topology import service_chain_for_step

__all__ = [
    "apply_incident_script",
    "emit_traces_for_events",
    "generate_events_for_plan",
    "service_chain_for_step",
]
