"""OTLP log emitter for live-tailed business events.

Spins up a dedicated LoggerProvider (separate from the global tracer setup
in `observability/`) so the live-tail process can run standalone — no
dependency on init_telemetry, and no risk of clobbering the planner's
TracerProvider when both run side-by-side.

One LogRecord per event row. The body is the event_type (so Loki's
`{service_name="proj-clarion-livetail"} |= "checkout"` works), the
attributes carry plan_id, trace_id, business_entity_ids, and the full
payload as a JSON string for retrieval.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import Any, Mapping

from opentelemetry._logs import LogRecord, SeverityNumber, set_logger_provider
from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
from opentelemetry.sdk._logs import LoggerProvider
from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
from opentelemetry.trace import TraceFlags

from proj_clarion.observability.otlp import clarion_resource, otlp_logs_endpoint


@dataclass(frozen=True)
class EventRow:
    """One row out of business_events, in the shape the emitter wants."""

    event_id: int
    plan_id: str
    ts_unix_nanos: int
    event_type: str
    business_entity_ids: list[str]
    payload: Mapping[str, Any]
    trace_id: str | None


class LiveTailLogEmitter:
    """OTLP log exporter targeted at Alloy (or Cloud direct).

    Lifecycle: `start()` → many `emit_batch(...)` → `shutdown()`. Idempotent.
    """

    def __init__(
        self,
        *,
        plan_id: str,
        customer: str,
        endpoint: str | None = None,
    ) -> None:
        self._plan_id = plan_id
        self._customer = customer
        # Endpoint resolution: explicit override > shared env-driven default
        # (OTEL_EXPORTER_OTLP_ENDPOINT/v1/logs).
        if endpoint:
            self._endpoint: str | None = endpoint.rstrip("/") + "/v1/logs"
        else:
            self._endpoint = otlp_logs_endpoint()
        self._provider: LoggerProvider | None = None
        self._processor: BatchLogRecordProcessor | None = None

    def start(self) -> None:
        if self._provider is not None:
            return
        if not self._endpoint:
            raise RuntimeError(
                "OTEL_EXPORTER_OTLP_ENDPOINT not set; livetail has nowhere to "
                "ship logs. Set it to http://localhost:4318 (Alloy) or to your "
                "Cloud OTLP gateway."
            )

        # Default `asserts.env` to the customer slug so live-tail logs land
        # in the same customer-scoped env scope as the kg-publish entities
        # (`env=bluesky_airlines` etc.) — keeps the Asserts entity-graph filter
        # consistent across all signal types for one demo.
        resource = clarion_resource(
            service_name="proj-clarion-livetail",
            plan_id=self._plan_id,
            customer=self._customer,
            env=self._customer,
        )
        self._provider = LoggerProvider(resource=resource)
        self._processor = BatchLogRecordProcessor(
            OTLPLogExporter(endpoint=self._endpoint),
            max_export_batch_size=512,
            schedule_delay_millis=1_000,
        )
        self._provider.add_log_record_processor(self._processor)
        set_logger_provider(self._provider)

    def emit_batch(self, rows: list[EventRow]) -> None:
        """Emit one LogRecord per row. Caller passes already-fetched rows."""
        if not rows:
            return
        if self._provider is None:
            raise RuntimeError("LiveTailLogEmitter.start() must be called first")
        logger = self._provider.get_logger("proj-clarion.livetail")

        for row in rows:
            # If the event has a trace_id, link the log to the trace by stamping
            # it on the LogRecord — Tempo↔Loki correlation in Grafana Explore
            # picks this up.
            trace_id_int = 0
            span_id_int = 0
            trace_flags = TraceFlags.DEFAULT
            if row.trace_id:
                try:
                    trace_id_int = int(row.trace_id, 16)
                    trace_flags = TraceFlags(TraceFlags.SAMPLED)
                except ValueError:
                    pass

            attributes: dict[str, Any] = {
                "clarion.event_id":            row.event_id,
                "clarion.plan_id":             row.plan_id,
                "clarion.event_type":          row.event_type,
                "clarion.business_entity_ids": row.business_entity_ids,
                "clarion.payload":             json.dumps(row.payload, default=str),
            }
            if row.trace_id:
                attributes["clarion.trace_id"] = row.trace_id

            record = LogRecord(
                timestamp=row.ts_unix_nanos,
                observed_timestamp=time.time_ns(),
                trace_id=trace_id_int,
                span_id=span_id_int,
                trace_flags=trace_flags,
                severity_text="INFO",
                severity_number=SeverityNumber.INFO,
                body=row.event_type,
                attributes=attributes,
            )
            # Resource is owned by the LoggerProvider; the Logger attaches it
            # to each record at export time, so we don't pass it on the record.
            logger.emit(record)

    def shutdown(self) -> None:
        if self._provider is None:
            return
        try:
            self._provider.shutdown()
        finally:
            self._provider = None
            self._processor = None
