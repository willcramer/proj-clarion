"""Deterministic business-event generator.

Produces a stream of `BusinessEvent`s for the plan's historical window.
Volume is derived from `data_blueprint.business_event_volume_per_day` and
spread across the day using the diurnal + weekly patterns. Events are
distributed across the plan's business processes proportionally.

Determinism: the RNG is seeded from `plan_id` + the day offset, so the same
plan always produces the same events. Useful for re-runs and golden testing.
"""

from __future__ import annotations

import random
import uuid as _uuid
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from proj_clarion.generator.diurnal import per_minute_rate
from proj_clarion.generator.topology import (
    business_context_for_services,
    select_entities_by_subtype,
    service_chain_for_step,
)
from proj_clarion.schemas import (
    BusinessProcessModel,
    BusinessStep,
    DemoPlan,
    KGNode,
)


@dataclass
class BusinessEvent:
    """One generated event ready to insert into business_events.

    Mirrors the table schema; the `payload` blob carries process-specific
    fields (order_id, amount, etc.) that downstream queries can pivot on.
    """

    ts: datetime
    event_type: str
    business_entity_ids: list[str]
    payload: dict[str, Any]
    trace_id: str
    # Not persisted directly but used by telemetry.py to shape spans:
    process_id: str = ""
    step_id: str = ""
    service_chain: list[str] = field(default_factory=list)
    error: bool = False
    duration_ms: int = 0


def _seeded_rng(plan_id: str, salt: str = "") -> random.Random:
    """Plan-scoped RNG so re-runs produce identical streams."""
    seed_bytes = (plan_id + "|" + salt).encode()
    seed_int = int.from_bytes(seed_bytes[:8].ljust(8, b"\x00"), "little")
    return random.Random(seed_int)


def _new_trace_id(rng: random.Random) -> str:
    """16-byte hex, OTel trace-id shape."""
    return f"{rng.getrandbits(128):032x}"


def _payload_for_step(
    process: BusinessProcessModel,
    step: BusinessStep,
    rng: random.Random,
) -> dict[str, Any]:
    """Process-aware payload. Heuristics by name keep things readable in
    demos without forcing every process to declare its own payload schema.
    """
    name = step.name.lower()
    payload: dict[str, Any] = {
        "process": process.name,
        "step": step.name,
        "kpi": step.kpi,
    }
    if any(w in name for w in ("checkout", "payment", "order", "purchase", "sale")):
        payload["order_id"] = f"ord-{_uuid.UUID(int=rng.getrandbits(128)).hex[:12]}"
        payload["amount_usd"] = round(rng.uniform(15, 480), 2)
        payload["customer_id"] = f"cus-{rng.randrange(10_000, 999_999)}"
    elif any(w in name for w in ("ship", "fulfill", "release", "pick")):
        payload["shipment_id"] = f"shp-{rng.randrange(100_000, 9_999_999)}"
        payload["units"] = rng.randint(1, 6)
    elif any(w in name for w in ("return", "refund")):
        payload["return_id"] = f"ret-{rng.randrange(10_000, 999_999)}"
        payload["amount_usd"] = round(rng.uniform(15, 240), 2)
    elif any(w in name for w in ("auth", "login", "session")):
        payload["session_id"] = f"sess-{rng.randrange(100_000, 9_999_999)}"
    return payload


def _pick_business_entities(
    rng: random.Random,
    plan: DemoPlan,
    services: list[str],
    by_subtype: dict[str, list[KGNode]],
) -> list[str]:
    """Sample 1-3 business entities for an event: prefer ones served by the
    chosen services; back-fill with random region/channel/store from the KG.
    """
    chain = business_context_for_services(plan.knowledge_graph, services)
    out: list[str] = list(chain[:3])
    for subtype in ("region", "channel", "store"):
        candidates = by_subtype.get(subtype, [])
        if candidates and not any(
            n.business_subtype == subtype and n.node_id in out
            for n in plan.knowledge_graph.nodes if n.node_id in out
        ):
            out.append(rng.choice(candidates).node_id)
    return out[:3] or [n.node_id for n in plan.knowledge_graph.nodes
                       if n.node_type.value == "business_entity"][:1]


def _baseline_error_rate(step_name: str) -> float:
    """Background error rate for a step; incident events spike this further."""
    name = step_name.lower()
    if any(w in name for w in ("payment", "checkout", "auth")):
        return 0.012
    if any(w in name for w in ("integration", "bridge", "translate")):
        return 0.018
    return 0.006


def _baseline_latency_ms(rng: random.Random, step_name: str) -> int:
    name = step_name.lower()
    if any(w in name for w in ("integration", "bridge", "external", "carrier", "edi")):
        # Long-tail external dependency
        return int(rng.lognormvariate(6.4, 0.5))  # ~600ms median
    if any(w in name for w in ("payment", "auth", "checkout")):
        return int(rng.lognormvariate(5.6, 0.4))  # ~270ms median
    return int(rng.lognormvariate(4.5, 0.5))  # ~90ms median


def generate_events_for_plan(
    plan: DemoPlan,
    *,
    days: int | None = None,
    end_at: datetime | None = None,
) -> Iterator[BusinessEvent]:
    """Yield events for the plan's historical window.

    Args:
        plan: a validated DemoPlan
        days: overrides `data_blueprint.historical_window_days` if set —
              useful for cheap dev runs.
        end_at: defaults to now (UTC).
    """
    blueprint = plan.data_blueprint
    span_days = days if days is not None else blueprint.historical_window_days
    end_at = (end_at or datetime.now(UTC)).replace(second=0, microsecond=0)
    start_at = end_at - timedelta(days=span_days)

    plan_id = str(plan.plan_id)
    rng = _seeded_rng(plan_id, salt="events")

    # Cache once
    by_subtype = select_entities_by_subtype(
        plan.knowledge_graph, ("region", "channel", "store", "fulfillment_center")
    )

    # Build a flat (process, step) list so we can sample uniformly per minute.
    process_steps: list[tuple[BusinessProcessModel, BusinessStep, list[str]]] = []
    for proc in plan.business_process_models:
        for step in proc.business_steps:
            chain = service_chain_for_step(plan.knowledge_graph, step.services_implementing)
            process_steps.append((proc, step, chain))
    if not process_steps:
        return  # nothing to emit

    daily_volume = blueprint.business_event_volume_per_day
    minute = start_at
    while minute < end_at:
        rate = per_minute_rate(blueprint.diurnal_pattern, blueprint.weekly_pattern,
                               daily_volume, minute)
        # Sample a Poisson-ish count: fractional rate handled by Bernoulli + floor
        count = int(rate)
        if rng.random() < (rate - count):
            count += 1
        for _ in range(count):
            proc, step, chain = rng.choice(process_steps)
            payload = _payload_for_step(proc, step, rng)
            entities = _pick_business_entities(rng, plan, chain, by_subtype)
            base_error_rate = _baseline_error_rate(step.name)
            error = rng.random() < base_error_rate
            duration = _baseline_latency_ms(rng, step.name)
            if error:
                duration = int(duration * rng.uniform(2.0, 5.0))
                payload["error"] = True
            yield BusinessEvent(
                ts=minute + timedelta(seconds=rng.randrange(0, 60)),
                event_type=f"{proc.process_id}.{step.step_id}",
                business_entity_ids=entities,
                payload=payload,
                trace_id=_new_trace_id(rng),
                process_id=proc.process_id,
                step_id=step.step_id,
                service_chain=chain,
                error=error,
                duration_ms=duration,
            )
        minute += timedelta(minutes=1)


# ============================================================
# Persistence
# ============================================================

def persist_events(session: Any, plan_id: str, events: Iterator[BusinessEvent],
                   batch_size: int = 1000) -> int:
    """Bulk-insert events into business_events. Returns total rows written."""
    from sqlalchemy import bindparam, text
    from sqlalchemy.dialects.postgresql import JSONB

    insert_sql = text("""
        INSERT INTO business_events
            (plan_id, ts, event_type, business_entity_ids, payload, trace_id)
        VALUES
            (:plan_id, :ts, :event_type, :business_entity_ids, :payload, :trace_id)
    """).bindparams(bindparam("payload", type_=JSONB))

    total = 0
    batch: list[dict[str, Any]] = []
    for ev in events:
        batch.append({
            "plan_id": plan_id,
            "ts": ev.ts,
            "event_type": ev.event_type,
            "business_entity_ids": ev.business_entity_ids,
            "payload": ev.payload,
            "trace_id": ev.trace_id,
        })
        if len(batch) >= batch_size:
            session.execute(insert_sql, batch)
            total += len(batch)
            batch.clear()
    if batch:
        session.execute(insert_sql, batch)
        total += len(batch)
    return total
