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

import structlog

_logger = structlog.get_logger()

# Module-level Sigil client handle — None when Sigil isn't configured.
_sigil_client: object | None = None


@lru_cache(maxsize=1)
def init_telemetry() -> bool:
    """Initialize OpenTelemetry tracing/metrics + Sigil. Returns True if OTLP wired."""
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

    # --- OpenLIT (auto-instrumentation; layered on top of our providers) ---
    try:
        import openlit

        openlit.init(application_name="proj-clarion")
        _logger.info("otel.init.openlit", backend=backend)
    except ImportError:
        _logger.info("otel.init", backend=backend, openlit="not_installed")

    # --- Sigil ---
    _init_sigil()

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
    """Flush + close the Sigil client. Safe at process exit."""
    global _sigil_client
    if _sigil_client is None:
        return
    try:
        _sigil_client.shutdown()  # type: ignore[attr-defined]
    except Exception as exc:  # noqa: BLE001
        _logger.warning("sigil.shutdown.failed", error=str(exc))
    finally:
        _sigil_client = None


def configure_logging() -> None:
    """Plain structlog config — JSON logs in production, console-friendly locally."""
    level = os.getenv("LOG_LEVEL", "INFO").upper()
    logging.basicConfig(level=level, format="%(message)s")

    use_json = os.getenv("LOG_FORMAT", "console") == "json"
    processors = [
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.add_log_level,
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]
    processors.append(
        structlog.processors.JSONRenderer() if use_json else structlog.dev.ConsoleRenderer()
    )
    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(getattr(logging, level)),
        cache_logger_on_first_use=True,
    )
