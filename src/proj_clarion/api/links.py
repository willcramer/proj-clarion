"""Build Grafana Cloud links for a finished plan.

Knows about:
- The dashboards folder UID convention (`clarion-<32hex>` from `provision/folders.py`)
- The Asserts entity catalog and Knowledge Graph paths
- The user's stack URL from `GRAFANA_CLOUD_STACK_URL` in `.env` (or
  derives one from `OTEL_EXPORTER_OTLP_ENDPOINT` as a fallback)

Returns a dict of {label → URL}. The UI renders these as buttons after
a successful pipeline run.
"""

from __future__ import annotations

import os
import re

from proj_clarion.provision.folders import folder_uid_for_plan


def _stack_url() -> str:
    """Best-effort discovery of the user's Grafana stack URL."""
    explicit = os.environ.get("GRAFANA_CLOUD_STACK_URL", "").strip()
    if explicit:
        return explicit.rstrip("/")
    # OTLP endpoint is e.g. https://otlp-gateway-prod-<region>.grafana.net/otlp
    # The stack URL we want is e.g. https://<your-stack>.grafana.net — derive
    # from GRAFANA_URL if present, otherwise leave blank and let the UI just
    # show the raw paths.
    grafana_url = os.environ.get("GRAFANA_URL", "").strip()
    if grafana_url:
        return grafana_url.rstrip("/")
    return ""


def build_grafana_links(plan_id: str) -> dict[str, str]:
    """Return {label: url} for the post-pipeline summary card.

    Always returns relative paths under the stack URL when the URL is
    available; if the stack URL isn't configured, returns absolute paths
    starting with `/` so the user can prefix them manually.
    """
    base = _stack_url()
    folder_uid = folder_uid_for_plan(plan_id)

    paths = {
        "Dashboards (this plan's folder)": f"/dashboards/f/{folder_uid}",
        "Knowledge Graph (entity catalog)": "/a/grafana-asserts-app/entities?definitionId=1001",
        "Asserts overview": "/a/grafana-asserts-app",
        "Alerts (this plan)": f"/alerting/list?queryString=plan_id%3D{plan_id}",
        "Explore (Tempo, this plan)": (
            "/explore?left="
            + _explore_state(
                "grafanacloud-traces",
                f'{{ resource.service.namespace="proj-clarion" && '
                f'resource.clarion.plan_id="{plan_id}" }}',
            )
        ),
    }
    return {label: (base + path) if base else path for label, path in paths.items()}


def _explore_state(datasource_uid: str, query: str) -> str:
    """Encode a Grafana Explore left-pane state. Loose schema; the only
    thing that really matters is the datasource and the query, both of
    which Grafana gracefully handles when other fields are missing."""
    import json
    import urllib.parse
    state = {
        "range": {"from": "now-1h", "to": "now"},
        "datasource": datasource_uid,
        "queries": [{"refId": "A", "datasource": {"type": "tempo", "uid": datasource_uid}, "query": query, "queryType": "traceql"}],
    }
    return urllib.parse.quote(json.dumps(state))


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
