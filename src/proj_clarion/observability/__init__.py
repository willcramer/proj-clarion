"""Observability bootstrap.

Proj Clarion is itself an agentic AI system. We instrument it three ways:

1. **OpenTelemetry traces and metrics** to Grafana Cloud via OTLP/HTTP. Every
   LLM call and every planner phase ends up as a span; the gen_ai.* metrics
   land in Mimir.
2. **OpenLIT** auto-instruments common AI libraries (httpx, requests, OpenAI,
   Anthropic) and emits gen_ai.* spans+metrics under our shared providers.
3. **Grafana Sigil** receives a normalized Generation record per LLM call via
   our `sigil_helper` wrapper, with `parent_generation_ids` set so multi-phase
   pipelines (the Plan agent's six phases) form a dependency DAG.

The Sigil SDK requires the application to own the OTel TracerProvider and
MeterProvider — we set both up here, before initializing OpenLIT or Sigil.

Call `init_telemetry()` once at process start. Idempotent.
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache
from typing import Any

import structlog

_logger = structlog.get_logger()

# Module-level Sigil client handle — None when Sigil isn't configured.
_sigil_client: object | None = None


@lru_cache(maxsize=1)
def init_telemetry() -> bool:
    """Initialize OpenTelemetry tracing/metrics + Sigil. Returns True if OTLP wired.

    Registers an atexit hook to flush + shutdown both the OTel
    TracerProvider/MeterProvider and the Sigil client. Without this,
    short-lived CLI subprocesses (research, plan, kg-publish) exit
    before the BatchSpanProcessor's next export tick fires and any
    queued Sigil tool-execution / generation records are dropped —
    which is the most common cause of an empty AI-Obs Tools page.
    """
    try:
        from opentelemetry import metrics, trace
        from opentelemetry.sdk.metrics import MeterProvider
        from opentelemetry.sdk.metrics.export import (
            ConsoleMetricExporter,
            PeriodicExportingMetricReader,
        )
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import (
            BatchSpanProcessor,
            ConsoleSpanExporter,
        )

        from proj_clarion.observability.otlp import (
            clarion_resource,
            otlp_endpoint,
            otlp_metrics_endpoint,
            otlp_traces_endpoint,
        )
    except ImportError:
        _logger.info("otel.skip", reason="opentelemetry packages not installed")
        return False

    # Canonical Clarion Resource — shared across init_telemetry,
    # EntityEmitter, LiveTailLogEmitter via observability.otlp.
    resource = clarion_resource(service_name="proj-clarion")
    has_otlp = otlp_endpoint() is not None

    # --- Tracer ---
    tracer_provider = TracerProvider(resource=resource)
    if has_otlp:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        tracer_provider.add_span_processor(
            BatchSpanProcessor(OTLPSpanExporter(endpoint=otlp_traces_endpoint()))
        )
        backend = "otlp"
    else:
        tracer_provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
        backend = "console"

    # Stamp gen_ai.conversation.id on every span started while an assistant
    # conversation context is active (see llm_client._conversation_id_var).
    # Grafana AI-Obs groups its Conversations view by this attribute on the
    # generation spans — and there are two per call (our gen_ai.chat wrapper
    # and OpenLIT's auto-instrumented span), so a processor is the only way
    # to reliably tag both.
    try:
        from opentelemetry.sdk.trace import SpanProcessor

        from proj_clarion.observability.llm_client import _conversation_id_var

        class _ConversationStamp(SpanProcessor):
            def on_start(self, span: Any, parent_context: Any = None) -> None:
                cid = _conversation_id_var.get()
                if cid:
                    span.set_attribute("gen_ai.conversation.id", cid)

            def on_end(self, span: Any) -> None:
                pass

            def shutdown(self) -> None:
                pass

            def force_flush(self, timeout_millis: int = 30000) -> bool:
                return True

        tracer_provider.add_span_processor(_ConversationStamp())
    except Exception as exc:  # noqa: BLE001 — instrumentation must never break boot
        _logger.warning("otel.conversation_stamp.skip", error=str(exc))

    trace.set_tracer_provider(tracer_provider)

    # --- Meter ---
    metric_exporter_disabled = os.getenv("OTEL_METRICS_EXPORTER", "").strip() == "none"
    if has_otlp and not metric_exporter_disabled:
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter

        metric_reader = PeriodicExportingMetricReader(
            OTLPMetricExporter(endpoint=otlp_metrics_endpoint()),
            export_interval_millis=int(os.getenv("OTEL_METRIC_EXPORT_INTERVAL_MILLIS", "60000")),
        )
        meter_provider = MeterProvider(resource=resource, metric_readers=[metric_reader])
    elif metric_exporter_disabled:
        meter_provider = MeterProvider(resource=resource)
    else:
        meter_provider = MeterProvider(
            resource=resource,
            metric_readers=[PeriodicExportingMetricReader(ConsoleMetricExporter())],
        )
    metrics.set_meter_provider(meter_provider)

    # --- Process / runtime metrics ---
    # Emits process.runtime.* (CPU, memory, GC, threads) for THIS process so
    # the Clarion app shows up as runtime/infrastructure in Asserts (under
    # env=clarion) alongside its service + DB entities. Host-level system.*
    # is left to the node_exporter Alloy collector to avoid double-counting.
    try:
        from opentelemetry.instrumentation.system_metrics import (
            SystemMetricsInstrumentor,
        )

        SystemMetricsInstrumentor(config={
            "process.runtime.memory": ["rss", "vms"],
            "process.runtime.cpu.time": ["user", "system"],
            "process.runtime.gc_count": None,
            "process.runtime.thread_count": None,
            "process.runtime.cpu.utilization": None,
            "process.runtime.context_switches": ["involuntary", "voluntary"],
        }).instrument()
        _logger.info("otel.init.system_metrics")
    except Exception as exc:  # noqa: BLE001 — instrumentation must never break boot
        _logger.info("otel.init.system_metrics.skip", error=str(exc)[:200])

    # --- Logs (OTLP → Loki) ---
    # The app runs on the host (not a container), so Docker log discovery
    # can't see it — instead its structlog records flow through the stdlib
    # root logger (see configure_logging) into this OTel LoggingHandler →
    # OTLP → Loki, auto-correlated with the active trace. This is what makes
    # the "zero Loki streams" gap go away for the app itself.
    logger_provider = None
    if has_otlp:
        try:
            from opentelemetry._logs import set_logger_provider
            from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
            from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
            from opentelemetry.sdk._logs.export import BatchLogRecordProcessor

            from proj_clarion.observability.otlp import otlp_logs_endpoint

            logger_provider = LoggerProvider(resource=resource)
            logger_provider.add_log_record_processor(
                BatchLogRecordProcessor(OTLPLogExporter(endpoint=otlp_logs_endpoint()))
            )
            set_logger_provider(logger_provider)
            logging.getLogger().addHandler(
                LoggingHandler(level=logging.NOTSET, logger_provider=logger_provider)
            )
            _logger.info("otel.init.logs")
        except Exception as exc:  # noqa: BLE001 — never break boot on logs export
            _logger.info("otel.init.logs.skip", error=str(exc)[:200])

    # --- OpenLIT (auto-instrumentation; layered on top of our providers) ---
    try:
        import openlit

        openlit.init(application_name="proj-clarion")
        _logger.info("otel.init.openlit", backend=backend)
    except ImportError:
        _logger.info("otel.init", backend=backend, openlit="not_installed")

    # --- Sigil ---
    _init_sigil()

    # --- Telemetry preflight (presence/scope; no network) ---
    # Surface, at boot, exactly which signals WON'T reach Cloud and the exact
    # var+scope each needs — so a half-configured .env is loud, not silent.
    try:
        from proj_clarion.observability.preflight import missing_pieces, telemetry_preflight

        for chk in missing_pieces(telemetry_preflight(probe=False)):
            _logger.warning(
                "telemetry.preflight.gap",
                signal=chk.signal,
                required_scope=chk.required_scope,
                fix=chk.remediation,
            )
    except Exception as exc:  # noqa: BLE001 — preflight must never break boot
        _logger.debug("telemetry.preflight.skip", error=str(exc)[:200])

    # --- Exit flush ---
    # CLI subprocesses (research, plan, kg-publish) finish in seconds-to-
    # minutes. Without an atexit hook the BatchSpanProcessor's queued spans
    # and the Sigil client's queued generations / tool executions die with
    # the interpreter. Register here so every process that touched
    # init_telemetry flushes before exit.
    import atexit

    def _flush_all() -> None:
        # OTel: force-flush the tracer + meter providers we just set up.
        try:
            tracer_provider.force_flush(timeout_millis=5000)
        except Exception as exc:  # noqa: BLE001
            _logger.debug("otel.tracer.flush.skip", error=str(exc)[:200])
        try:
            tracer_provider.shutdown()
        except Exception as exc:  # noqa: BLE001
            _logger.debug("otel.tracer.shutdown.skip", error=str(exc)[:200])
        try:
            meter_provider.force_flush(timeout_millis=5000)
        except Exception as exc:  # noqa: BLE001
            _logger.debug("otel.meter.flush.skip", error=str(exc)[:200])
        try:
            meter_provider.shutdown()
        except Exception as exc:  # noqa: BLE001
            _logger.debug("otel.meter.shutdown.skip", error=str(exc)[:200])
        # Logs: flush the queued OTLP log records before the interpreter dies.
        if logger_provider is not None:
            try:
                logger_provider.force_flush(timeout_millis=5000)
                logger_provider.shutdown()
            except Exception as exc:  # noqa: BLE001
                _logger.debug("otel.logs.shutdown.skip", error=str(exc)[:200])
        # Sigil: flush queued records, then close.
        shutdown_sigil()

    atexit.register(_flush_all)

    return backend == "otlp"


def _ensure_ssl_cert_file() -> None:
    """The Sigil SDK uses stdlib urllib for ingest, which honours SSL_CERT_FILE
    but does NOT pick up certifi's bundle automatically on macOS brew Python.
    Without this, every Sigil export fails with CERTIFICATE_VERIFY_FAILED.
    """
    if os.environ.get("SSL_CERT_FILE"):
        return
    try:
        import certifi

        os.environ["SSL_CERT_FILE"] = certifi.where()
    except ImportError:
        pass


def _init_sigil() -> None:
    """Initialize the Sigil client when SIGIL_ENDPOINT is set. Idempotent."""
    global _sigil_client
    if _sigil_client is not None:
        return

    endpoint = os.getenv("SIGIL_ENDPOINT", "").strip()
    if not endpoint:
        _logger.info("sigil.skip", reason="SIGIL_ENDPOINT not set")
        return

    try:
        from sigil_sdk import Client
    except ImportError:
        _logger.warning("sigil.skip", reason="sigil-sdk not installed")
        return

    _ensure_ssl_cert_file()

    try:
        # Client(ClientConfig()) reads SIGIL_* env vars by default.
        _sigil_client = Client()
        _logger.info("sigil.init.ok", endpoint=endpoint,
                     tenant=os.getenv("SIGIL_AUTH_TENANT_ID", ""))
    except Exception as exc:  # noqa: BLE001
        _logger.warning("sigil.init.failed", error=str(exc))
        _sigil_client = None


def get_sigil_client() -> object | None:
    """Return the process-wide Sigil client, or None if not configured."""
    return _sigil_client


def shutdown_sigil() -> None:
    """Flush + close the Sigil client. Safe at process exit.

    Flushes pending Generation + ToolExecution records first; the SDK's
    `shutdown()` call also flushes but may swallow individual export
    failures, so we call `flush()` explicitly to surface them in logs."""
    global _sigil_client
    if _sigil_client is None:
        return
    try:
        if hasattr(_sigil_client, "flush"):
            _sigil_client.flush()  # type: ignore[attr-defined]
    except Exception as exc:  # noqa: BLE001
        _logger.warning("sigil.flush.failed", error=str(exc))
    try:
        _sigil_client.shutdown()  # type: ignore[attr-defined]
    except Exception as exc:  # noqa: BLE001
        _logger.warning("sigil.shutdown.failed", error=str(exc))
    finally:
        _sigil_client = None


def _add_trace_context(_logger: Any, _method: str, event_dict: dict[str, Any]) -> dict[str, Any]:
    """structlog processor: stamp the active trace_id/span_id onto every log
    line so logs correlate with traces in Grafana (Logs ↔ Traces, RCA). The
    OTel LoggingHandler also attaches trace context to OTLP log records; this
    keeps the stdout JSON correlated too."""
    try:
        from opentelemetry import trace as _t

        ctx = _t.get_current_span().get_span_context()
        if ctx.is_valid:
            event_dict["trace_id"] = format(ctx.trace_id, "032x")
            event_dict["span_id"] = format(ctx.span_id, "016x")
    except Exception:  # noqa: BLE001
        pass
    return event_dict


def configure_logging() -> None:
    """structlog routed through stdlib logging, so app logs flow to BOTH the
    console AND the OTel LoggingHandler (→ OTLP → Loki, trace-correlated;
    handler is attached by init_telemetry). JSON in prod, console locally."""
    level = os.getenv("LOG_LEVEL", "INFO").upper()
    use_json = os.getenv("LOG_FORMAT", "console") == "json"

    # Processors shared by structlog-native and stdlib-foreign records.
    shared: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        _add_trace_context,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    structlog.configure(
        processors=[*shared, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    renderer: Any = (
        structlog.processors.JSONRenderer() if use_json
        else structlog.dev.ConsoleRenderer(colors=True)
    )
    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[structlog.stdlib.ProcessorFormatter.remove_processors_meta, renderer],
        foreign_pre_chain=shared,
    )

    # Reset the root logger to one console handler with our formatter.
    # init_telemetry() adds the OTel LoggingHandler alongside it.
    root = logging.getLogger()
    for h in list(root.handlers):
        root.removeHandler(h)
    console = logging.StreamHandler()
    console.setFormatter(formatter)
    root.addHandler(console)
    root.setLevel(getattr(logging, level, logging.INFO))


def init_profiling() -> None:
    """Opt-in continuous profiling (Pyroscope). OFF unless PYROSCOPE_ENABLED
    is truthy — profiling adds CPU overhead, so it's a deliberate switch.

    Pushes pprof to the local clarion-obs Alloy receiver
    (PYROSCOPE_SERVER_ADDRESS, default http://localhost:4040), which forwards
    to Grafana Cloud Profiles. Tagged env=clarion so it lines up with the
    app's traces/metrics/logs in the RCA workbench."""
    if os.getenv("PYROSCOPE_ENABLED", "").strip().lower() not in ("1", "true", "yes"):
        return
    try:
        import pyroscope

        pyroscope.configure(
            application_name="proj-clarion",
            server_address=os.getenv("PYROSCOPE_SERVER_ADDRESS", "http://localhost:4040"),
            tags={
                "env":     os.getenv("CLARION_ASSERTS_ENV", "clarion"),
                "service": "proj-clarion",
            },
        )
        _logger.info("otel.init.pyroscope")
    except Exception as exc:  # noqa: BLE001 — profiling must never break boot
        _logger.info("otel.init.pyroscope.skip", error=str(exc)[:200])
