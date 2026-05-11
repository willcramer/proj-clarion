"""Per-event OTel trace emission.

Each `BusinessEvent` becomes one trace:
- root span: the business event (e.g., `d2c_checkout.add_to_cart`)
- one child span per service in `service_chain`, parented in order
- spans are stamped with `ts` from the event so historical events backdate
  correctly in Tempo (which accepts arbitrary past timestamps)

Metrics and logs are deliberately NOT emitted from here — Mimir's OTLP
gateway rejects historical metrics, and the v0.4 dashboards query Postgres
for business KPIs anyway. Live metrics land in v0.5 with Alloy.

The implementation goes through the OTel SDK's tracer, which we rely on to
have been initialised by `observability.init_telemetry()`. To control
service.name per child span we attach a `service.name` attribute (Tempo
honours that for span filtering) rather than swapping resources mid-trace.
"""

from __future__ import annotations

from collections.abc import Iterable

from opentelemetry import trace
from opentelemetry.trace import (
    Link,
    SpanContext,
    SpanKind,
    Status,
    StatusCode,
    TraceFlags,
    set_span_in_context,
)
from opentelemetry.trace.span import NonRecordingSpan

from proj_clarion.generator.events import BusinessEvent

_tracer = trace.get_tracer("proj-clarion.generator")


def _ts_to_nanos(dt) -> int:
    return int(dt.timestamp() * 1_000_000_000)


def _trace_id_int(hex_id: str) -> int:
    return int(hex_id, 16)


def _generate_span_id(rng_seed: int, suffix: int) -> int:
    """Stable 8-byte span id from the trace seed + suffix."""
    return ((rng_seed * 0x9E3779B97F4A7C15) ^ (suffix * 0x100000001B3)) & ((1 << 64) - 1) or 1


def emit_traces_for_events(
    events: Iterable[BusinessEvent],
    *,
    plan_id: str,
    flush_every: int = 200,
) -> int:
    """Emit one trace per event. Returns the number of traces emitted.

    NOTE: We rely on the global TracerProvider being a real OTel SDK
    TracerProvider (not the no-op one). `observability.init_telemetry()`
    sets that up.
    """
    provider = trace.get_tracer_provider()
    if not hasattr(provider, "force_flush"):
        # No-op provider — nothing to do.
        return 0

    count = 0
    for ev in events:
        _emit_one(ev, plan_id=plan_id)
        count += 1
        if count % flush_every == 0:
            provider.force_flush(timeout_millis=10_000)  # type: ignore[attr-defined]
    if count:
        provider.force_flush(timeout_millis=30_000)  # type: ignore[attr-defined]
    return count


def _emit_one(ev: BusinessEvent, *, plan_id: str) -> None:
    """Build and end a synthetic trace for one event."""
    trace_id_int = _trace_id_int(ev.trace_id)
    seed = trace_id_int & ((1 << 64) - 1)
    start_ns = _ts_to_nanos(ev.ts)
    total_dur_ns = max(1, ev.duration_ms) * 1_000_000

    # Distribute total duration across [root + N children] proportionally.
    chain = ev.service_chain or ["proj-clarion-event"]
    n_children = len(chain)
    # Budget root at 10%, leave 90% spread across children, weighted toward tail
    child_budget_ns = int(total_dur_ns * 0.9)
    weights = [1.0 + (i * 0.6) for i in range(n_children)]
    weight_sum = sum(weights)
    child_durations = [int(child_budget_ns * (w / weight_sum)) for w in weights]
    root_duration_ns = total_dur_ns - sum(child_durations)

    # ROOT SPAN — represents the business event
    root_span_id = _generate_span_id(seed, 1)
    root_ctx = SpanContext(
        trace_id=trace_id_int,
        span_id=root_span_id,
        is_remote=False,
        trace_flags=TraceFlags(TraceFlags.SAMPLED),
    )
    parent_ctx = trace.set_span_in_context(NonRecordingSpan(root_ctx))

    root = _tracer.start_span(
        name=ev.event_type,
        context=trace.set_span_in_context(NonRecordingSpan(SpanContext(
            trace_id=trace_id_int,
            span_id=0,
            is_remote=False,
            trace_flags=TraceFlags(TraceFlags.SAMPLED),
        ))),
        kind=SpanKind.SERVER,
        start_time=start_ns,
        attributes={
            "clarion.plan_id": plan_id,
            "clarion.process_id": ev.process_id,
            "clarion.step_id": ev.step_id,
            "clarion.trace_id": ev.trace_id,
            "clarion.business_entity_ids": ev.business_entity_ids,
            "service.name": "proj-clarion-event",
        },
    )
    # Force the trace_id/span_id we computed (otherwise SDK assigns its own)
    root._context = root_ctx  # type: ignore[attr-defined]

    if ev.error:
        root.set_status(Status(StatusCode.ERROR, "event reported error"))
        root.set_attribute("error", True)

    cursor = start_ns + root_duration_ns

    # CHILD SPANS — one per service in the chain
    parent_id = root_span_id
    parent_ns_carried = trace.set_span_in_context(NonRecordingSpan(root_ctx))
    for i, svc in enumerate(chain):
        child_span_id = _generate_span_id(seed, i + 2)
        child_ctx = SpanContext(
            trace_id=trace_id_int,
            span_id=child_span_id,
            is_remote=False,
            trace_flags=TraceFlags(TraceFlags.SAMPLED),
        )
        # Use the parent's context so the SDK records parent_span_id correctly
        parent_ctx_obj = SpanContext(
            trace_id=trace_id_int,
            span_id=parent_id,
            is_remote=False,
            trace_flags=TraceFlags(TraceFlags.SAMPLED),
        )
        child = _tracer.start_span(
            name=f"{svc}.handle",
            context=trace.set_span_in_context(NonRecordingSpan(parent_ctx_obj)),
            kind=SpanKind.CLIENT if i + 1 < len(chain) else SpanKind.INTERNAL,
            start_time=cursor,
            attributes={
                "service.name": svc,
                "clarion.plan_id": plan_id,
                "clarion.process_id": ev.process_id,
            },
        )
        child._context = child_ctx  # type: ignore[attr-defined]
        if ev.error and i + 1 == len(chain):
            child.set_status(Status(StatusCode.ERROR, "downstream error"))
            child.set_attribute("error", True)
        cursor += child_durations[i]
        child.end(end_time=cursor)
        parent_id = child_span_id

    root.end(end_time=start_ns + total_dur_ns)
