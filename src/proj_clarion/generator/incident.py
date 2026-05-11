"""Apply an IncidentScript to a generated event stream.

For each event in the stream we check whether it falls inside any incident
window and, if so, apply the magnitude-shaped distortion the script asks for.
The distortion changes BusinessEvent fields in-place:

- latency_spike / queue_back_pressure / dependency_unavailable on a service:
    if the event's service_chain contains the target service, multiply
    duration_ms by `magnitude` and lift error rate.
- error_burst on a service:
    same membership check, force error=True for ~`magnitude * 5%` of events.
- business_kpi_drop on a business_entity:
    if the event touches the target entity, drop the event probabilistically
    so the downstream KPI shows the dip.
- throughput_drop on a service:
    drop ~ (1 - 1/magnitude) of events that touch the service.
- agent_hallucination / token_cost_spike / others:
    annotate but don't reshape (stub for v0.5+ when agent telemetry lands).

The incident is anchored at `incident_anchor` (defaults to demo `now`), so the
T+4min event maps to anchor + 4min in absolute time. Anchor near the END of
the historical window so the incident shows up at the right edge of dashboards.
"""

from __future__ import annotations

import random
from collections.abc import Iterator
from datetime import datetime, timedelta

from proj_clarion.generator.events import BusinessEvent
from proj_clarion.schemas import EventType, IncidentEvent, IncidentScript


def _event_window(
    ev: IncidentEvent, anchor: datetime
) -> tuple[datetime, datetime]:
    return (
        anchor + timedelta(seconds=ev.offset_seconds),
        anchor + timedelta(seconds=ev.recovery_offset_seconds),
    )


def _affects(ev: IncidentEvent, be: BusinessEvent) -> bool:
    if ev.target_kind == "service":
        return ev.target_id in be.service_chain
    if ev.target_kind == "business_entity":
        return ev.target_id in be.business_entity_ids
    if ev.target_kind == "agent":
        # Agent-level distortion not modeled in v0.3 events; let the trace
        # emitter (v0.5+) annotate gen_ai spans instead.
        return False
    return False


def apply_incident_script(
    events: Iterator[BusinessEvent],
    script: IncidentScript,
    *,
    anchor: datetime,
    rng_seed: str = "incident",
) -> Iterator[BusinessEvent]:
    """Yield events with incident effects applied. Some events may be dropped
    entirely (throughput_drop / business_kpi_drop) — caller treats this as the
    expected dip in volume.
    """
    rng = random.Random(hash(rng_seed) & 0xFFFFFFFF)
    windows = [(_event_window(e, anchor), e) for e in script.events]

    for be in events:
        keep = True
        for (start, end), inc in windows:
            if not (start <= be.ts < end):
                continue
            if not _affects(inc, be):
                continue
            be.payload.setdefault("incident", []).append({
                "event_id": inc.event_id,
                "type": inc.event_type.value,
                "magnitude": inc.magnitude,
            })
            if inc.event_type in (
                EventType.LATENCY_SPIKE,
                EventType.QUEUE_BACK_PRESSURE,
                EventType.DEPENDENCY_UNAVAILABLE,
            ):
                be.duration_ms = int(be.duration_ms * inc.magnitude)
                if rng.random() < min(0.4, 0.05 * inc.magnitude):
                    be.error = True
                    be.payload["error"] = True
            elif inc.event_type == EventType.ERROR_BURST:
                if rng.random() < min(0.6, 0.05 * inc.magnitude):
                    be.error = True
                    be.payload["error"] = True
            elif inc.event_type in (EventType.THROUGHPUT_DROP, EventType.BUSINESS_KPI_DROP):
                # Drop a fraction of events to make the KPI dip visible.
                drop_prob = min(0.85, 1.0 - (1.0 / max(1.05, inc.magnitude)))
                if rng.random() < drop_prob:
                    keep = False
                    break
        if keep:
            yield be
