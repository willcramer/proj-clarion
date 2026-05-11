"""Unit tests for the shared OTel/OTLP bootstrap helpers.

These helpers are the single source of truth for Resource shape and
endpoint discovery used by init_telemetry, EntityEmitter, and
LiveTailLogEmitter. Drift here would silently break label conventions
across three sites, so the surface deserves coverage.
"""

from __future__ import annotations

import pytest

from proj_clarion.observability.otlp import (
    clarion_env,
    clarion_resource,
    clarion_site,
    otlp_endpoint,
    otlp_logs_endpoint,
    otlp_metrics_endpoint,
    otlp_traces_endpoint,
    using_alloy_hop,
)


class TestEndpointHelpers:
    def test_endpoints_return_none_when_unset(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
        assert otlp_endpoint() is None
        assert otlp_logs_endpoint() is None
        assert otlp_metrics_endpoint() is None
        assert otlp_traces_endpoint() is None

    def test_endpoints_strip_trailing_slash(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318/")
        assert otlp_endpoint() == "http://localhost:4318"
        assert otlp_logs_endpoint() == "http://localhost:4318/v1/logs"
        assert otlp_metrics_endpoint() == "http://localhost:4318/v1/metrics"
        assert otlp_traces_endpoint() == "http://localhost:4318/v1/traces"

    def test_using_alloy_hop_detects_localhost(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318")
        assert using_alloy_hop() is True
        monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://127.0.0.1:4318")
        assert using_alloy_hop() is True
        monkeypatch.setenv(
            "OTEL_EXPORTER_OTLP_ENDPOINT",
            "https://otlp-gateway-prod-<region>.grafana.net/otlp",
        )
        assert using_alloy_hop() is False
        monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
        assert using_alloy_hop() is False


class TestAssertsDefaults:
    def test_clarion_env_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("CLARION_ASSERTS_ENV", raising=False)
        assert clarion_env() == "prod"

    def test_clarion_env_override(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("CLARION_ASSERTS_ENV", "staging")
        assert clarion_env() == "staging"

    def test_clarion_site_default(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("CLARION_ASSERTS_SITE", raising=False)
        assert clarion_site() == "demo"


class TestClarionResource:
    def test_minimal_resource_has_canonical_attributes(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.delenv("CLARION_ASSERTS_ENV", raising=False)
        monkeypatch.delenv("CLARION_ASSERTS_SITE", raising=False)

        r = clarion_resource(service_name="test-svc")
        attrs = dict(r.attributes)

        # Canonical service.* triple
        assert attrs["service.name"] == "test-svc"
        assert attrs["service.namespace"] == "proj-clarion"
        assert attrs["service.version"] == "0.5.0"

        # asserts.* + deployment.environment all share clarion_env()
        assert attrs["asserts.env"] == "prod"
        assert attrs["asserts.site"] == "demo"
        assert attrs["deployment.environment"] == "prod"

        # plan_id / customer omitted when not provided — keeps cardinality
        # low for emitters that don't care
        assert "clarion.plan_id" not in attrs
        assert "clarion.customer" not in attrs

    def test_resource_includes_plan_and_customer_when_given(self) -> None:
        r = clarion_resource(
            service_name="test-svc",
            plan_id="abc-123",
            customer="acme_retail",
        )
        attrs = dict(r.attributes)
        assert attrs["clarion.plan_id"] == "abc-123"
        assert attrs["clarion.customer"] == "acme_retail"

    def test_extra_overrides_canonical_attrs(self) -> None:
        """Extra dict can override the canonical attrs — useful for the
        per-service Resource clones in red_emitter / log_emitter."""
        r = clarion_resource(
            service_name="default-name",
            extra={"service.name": "overridden", "custom.key": "v"},
        )
        attrs = dict(r.attributes)
        assert attrs["service.name"] == "overridden"
        assert attrs["custom.key"] == "v"

    def test_env_overrides_propagate_to_resource(
        self, monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setenv("CLARION_ASSERTS_ENV", "staging")
        monkeypatch.setenv("CLARION_ASSERTS_SITE", "perf")
        r = clarion_resource(service_name="test")
        attrs = dict(r.attributes)
        assert attrs["asserts.env"] == "staging"
        assert attrs["asserts.site"] == "perf"
        assert attrs["deployment.environment"] == "staging"
