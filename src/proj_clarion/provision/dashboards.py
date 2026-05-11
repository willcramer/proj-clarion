"""DashboardSpec → Grafana dashboard JSON.

Each `DashboardSpec` from a DemoPlan turns into one Grafana dashboard. The
spec gives us a title, an audience, and a list of panel titles; we infer
panel queries from the title text using simple keyword heuristics. This is
intentionally heuristic-shaped — the SE will refine the dashboard in v0.6's
review UI; for v0.4 we want enough structure that the SE sees real panels
with real queries against the right datasource.

Datasource selection by audience:
- business → Postgres (queries the local `business_events` table over PDC)
- technical → Tempo (TraceQL against the per-event spans we emitted)
- pivot → mix: top half Postgres (KPI), bottom half Tempo (traces)

Datasources are referenced by `type + uid` rather than name so the same
dashboard works against any user's stack as long as their datasource UIDs
follow Grafana Cloud's defaults (`grafanacloud-postgres`, `grafanacloud-traces`,
`grafanacloud-prom`, `grafanacloud-logs`). Override via env vars if the
user has renamed them.
"""

from __future__ import annotations

import os
from typing import Any
from uuid import UUID

from proj_clarion.schemas import DashboardSpec, TargetAudience


# ============================================================
# Datasource UIDs (overridable per env)
# ============================================================

def _ds_uid(kind: str) -> str:
    """Resolve the datasource UID for a kind. Defaults assume Grafana Cloud's
    out-of-the-box names; override per env if the stack's UIDs differ. The
    Postgres UID has no Cloud-default — it must come from env (the SE's
    PDC-bridged datasource).
    """
    env_key = {
        "postgres": "GRAFANA_DS_POSTGRES_UID",
        "prometheus": "GRAFANA_DS_PROMETHEUS_UID",
        "loki": "GRAFANA_DS_LOKI_UID",
        "tempo": "GRAFANA_DS_TEMPO_UID",
    }.get(kind, "")
    default = {
        "postgres": "",  # SE-provisioned per stack; no Cloud default
        "prometheus": "grafanacloud-prom",
        "loki": "grafanacloud-logs",
        "tempo": "grafanacloud-traces",
    }.get(kind, kind)
    return os.environ.get(env_key, default) if env_key else default


# Plugin IDs as Grafana stores them in `datasource.type` on dashboard panels.
# Postgres in particular: canonical id is `grafana-postgresql-datasource`,
# not `postgres`.
_PLUGIN_TYPE = {
    "postgres": "grafana-postgresql-datasource",
    "prometheus": "prometheus",
    "loki": "loki",
    "tempo": "tempo",
}


def _ds_ref(kind: str) -> dict[str, str]:
    return {"type": _PLUGIN_TYPE.get(kind, kind), "uid": _ds_uid(kind)}


# ============================================================
# Panel builders
# ============================================================

def _grid_pos(row: int, col: int, width: int = 12, height: int = 8) -> dict[str, int]:
    return {"x": col * width, "y": row * height, "w": width, "h": height}


def _postgres_target(plan_id: str, sql: str, ref_id: str = "A") -> dict[str, Any]:
    return {
        "refId": ref_id,
        "datasource": _ds_ref("postgres"),
        "format": "time_series",
        "rawQuery": True,
        "rawSql": sql,
    }


def _tempo_target(traceql: str, ref_id: str = "A") -> dict[str, Any]:
    return {
        "refId": ref_id,
        "datasource": _ds_ref("tempo"),
        "queryType": "traceql",
        "query": traceql,
    }


def _prom_target(promql: str, ref_id: str = "A") -> dict[str, Any]:
    return {
        "refId": ref_id,
        "datasource": _ds_ref("prometheus"),
        "expr": promql,
    }


def _timeseries_panel(
    panel_id: int, title: str, targets: list[dict[str, Any]], pos: dict[str, int],
    *, datasource_kind: str, unit: str | None = None,
) -> dict[str, Any]:
    field_config: dict[str, Any] = {
        "defaults": {"custom": {"drawStyle": "line", "lineWidth": 1, "fillOpacity": 8}},
        "overrides": [],
    }
    if unit:
        field_config["defaults"]["unit"] = unit
    return {
        "id": panel_id,
        "title": title,
        "type": "timeseries",
        "datasource": _ds_ref(datasource_kind),
        "gridPos": pos,
        "targets": targets,
        "fieldConfig": field_config,
        "options": {"legend": {"showLegend": True, "displayMode": "list", "placement": "bottom"},
                    "tooltip": {"mode": "multi"}},
    }


def _stat_panel(
    panel_id: int, title: str, targets: list[dict[str, Any]], pos: dict[str, int],
    *, datasource_kind: str, unit: str | None = None,
) -> dict[str, Any]:
    field_config: dict[str, Any] = {"defaults": {}, "overrides": []}
    if unit:
        field_config["defaults"]["unit"] = unit
    return {
        "id": panel_id,
        "title": title,
        "type": "stat",
        "datasource": _ds_ref(datasource_kind),
        "gridPos": pos,
        "targets": targets,
        "fieldConfig": field_config,
        "options": {"colorMode": "value", "graphMode": "area",
                    "reduceOptions": {"calcs": ["lastNotNull"]}, "textMode": "auto"},
    }


def _table_panel(
    panel_id: int, title: str, targets: list[dict[str, Any]], pos: dict[str, int],
    *, datasource_kind: str,
) -> dict[str, Any]:
    return {
        "id": panel_id,
        "title": title,
        "type": "table",
        "datasource": _ds_ref(datasource_kind),
        "gridPos": pos,
        "targets": targets,
        "fieldConfig": {"defaults": {}, "overrides": []},
        "options": {},
    }


def _trace_search_panel(
    panel_id: int, title: str, plan_id: str, pos: dict[str, int],
    extra_filters: str = "",
) -> dict[str, Any]:
    base = f'resource.service.name = "proj-clarion" && clarion.plan_id = "{plan_id}"'
    traceql = "{ " + base + (f" && {extra_filters}" if extra_filters else "") + " }"
    return {
        "id": panel_id,
        "title": title,
        "type": "table",
        "datasource": _ds_ref("tempo"),
        "gridPos": pos,
        "targets": [_tempo_target(traceql)],
        "fieldConfig": {"defaults": {}, "overrides": []},
        "options": {},
    }


# ============================================================
# Postgres SQL templates against business_events
# ============================================================

def _sql_revenue_trend(plan_id: str) -> str:
    # $__timeGroupAlias bins by time; only sums where amount_usd is set
    return f"""
SELECT
  $__timeGroupAlias(ts, $__interval),
  COALESCE(SUM((payload->>'amount_usd')::float), 0) AS revenue_usd
FROM business_events
WHERE plan_id = '{plan_id}'
  AND $__timeFilter(ts)
  AND payload ? 'amount_usd'
GROUP BY 1
ORDER BY 1
""".strip()


def _sql_event_volume(plan_id: str) -> str:
    return f"""
SELECT
  $__timeGroupAlias(ts, $__interval),
  COUNT(*) AS events
FROM business_events
WHERE plan_id = '{plan_id}'
  AND $__timeFilter(ts)
GROUP BY 1
ORDER BY 1
""".strip()


def _sql_volume_by_process(plan_id: str) -> str:
    return f"""
SELECT
  $__timeGroupAlias(ts, $__interval),
  split_part(event_type, '.', 1) AS process,
  COUNT(*) AS events
FROM business_events
WHERE plan_id = '{plan_id}'
  AND $__timeFilter(ts)
GROUP BY 1, 2
ORDER BY 1
""".strip()


def _sql_error_rate(plan_id: str) -> str:
    return f"""
SELECT
  $__timeGroupAlias(ts, $__interval),
  AVG(CASE WHEN payload ? 'error' THEN 1.0 ELSE 0.0 END) AS error_rate
FROM business_events
WHERE plan_id = '{plan_id}'
  AND $__timeFilter(ts)
GROUP BY 1
ORDER BY 1
""".strip()


def _sql_top_entities(plan_id: str) -> str:
    return f"""
SELECT
  unnest(business_entity_ids) AS entity,
  COUNT(*) AS events
FROM business_events
WHERE plan_id = '{plan_id}'
  AND $__timeFilter(ts)
GROUP BY 1
ORDER BY events DESC
LIMIT 20
""".strip()


def _sql_top_event_types(plan_id: str) -> str:
    return f"""
SELECT
  event_type,
  COUNT(*) AS events
FROM business_events
WHERE plan_id = '{plan_id}'
  AND $__timeFilter(ts)
GROUP BY 1
ORDER BY events DESC
LIMIT 20
""".strip()


# ============================================================
# Panel selection from spec.primary_panels
# ============================================================

def _panel_for_title(
    panel_id: int, title: str, plan_id: str, pos: dict[str, int],
    audience: TargetAudience,
) -> dict[str, Any]:
    """Pick a panel shape + query from a free-text panel title.

    Heuristic-only — the SE refines in v0.6. The intent is "every panel
    points at a real datasource and renders something plausible".
    """
    lower = title.lower()

    # Trace-shaped panels (technical / pivot)
    if any(w in lower for w in ("trace", "span", "explorer", "exemplar")):
        return _trace_search_panel(panel_id, title, plan_id, pos)
    if any(w in lower for w in ("latency", "p95", "p99", "duration")):
        if audience == TargetAudience.TECHNICAL:
            # Try Prom RED-metrics-style panel (works once Alloy's wired up)
            return _timeseries_panel(
                panel_id, title,
                [_prom_target(
                    'histogram_quantile(0.95, sum(rate(traces_spanmetrics_latency_bucket'
                    '{service_name=~"proj-clarion.*"}[$__rate_interval])) by (le, service_name))'
                )],
                pos, datasource_kind="prometheus", unit="ms",
            )
        return _trace_search_panel(panel_id, title, plan_id, pos)

    # Postgres-shaped panels (business / pivot top)
    if any(w in lower for w in ("revenue", "sales", "gmv")):
        return _timeseries_panel(
            panel_id, title, [_postgres_target(plan_id, _sql_revenue_trend(plan_id))], pos,
            datasource_kind="postgres", unit="currencyUSD",
        )
    if any(w in lower for w in ("error", "5xx", "fail")):
        return _timeseries_panel(
            panel_id, title, [_postgres_target(plan_id, _sql_error_rate(plan_id))], pos,
            datasource_kind="postgres", unit="percentunit",
        )
    if any(w in lower for w in ("by channel", "by store", "by region", "by entity",
                                  "channel mix", "store mix", "region mix")):
        return _table_panel(
            panel_id, title, [_postgres_target(plan_id, _sql_top_entities(plan_id))], pos,
            datasource_kind="postgres",
        )
    if any(w in lower for w in ("by process", "by event_type", "process volume")):
        return _timeseries_panel(
            panel_id, title, [_postgres_target(plan_id, _sql_volume_by_process(plan_id))], pos,
            datasource_kind="postgres",
        )
    if any(w in lower for w in ("conversion", "abandonment", "cart")):
        return _timeseries_panel(
            panel_id, title, [_postgres_target(plan_id, _sql_event_volume(plan_id))], pos,
            datasource_kind="postgres", unit="short",
        )
    if any(w in lower for w in ("top", "leaderboard", "ranking")):
        return _table_panel(
            panel_id, title, [_postgres_target(plan_id, _sql_top_event_types(plan_id))], pos,
            datasource_kind="postgres",
        )

    # Fallback — total event volume against Postgres
    return _timeseries_panel(
        panel_id, title, [_postgres_target(plan_id, _sql_event_volume(plan_id))], pos,
        datasource_kind="postgres",
    )


# ============================================================
# Dashboard assembly
# ============================================================

def build_dashboard(
    spec: DashboardSpec,
    plan_id: str | UUID,
    *,
    folder_uid: str | None = None,
) -> dict[str, Any]:
    """Build a Grafana dashboard JSON document for one DashboardSpec.

    The returned dict matches Grafana's dashboard JSON shape used by
    `POST /api/dashboards/db`. Caller wraps it with `{dashboard, folderUid}`.
    """
    plan_id_str = str(plan_id)
    panels: list[dict[str, Any]] = []
    for i, title in enumerate(spec.primary_panels):
        row = i // 2
        col = i % 2
        pos = _grid_pos(row, col)
        panels.append(_panel_for_title(i + 1, title, plan_id_str, pos, spec.audience))

    return {
        "uid": spec.dashboard_id,
        "title": spec.title,
        "tags": ["proj-clarion", f"plan:{plan_id_str[:8]}",
                 f"audience:{spec.audience.value}"],
        "schemaVersion": 39,
        "version": 1,
        "refresh": "30s",
        "time": {"from": "now-1h", "to": "now"},
        "timezone": "browser",
        "panels": panels,
        "templating": {
            "list": [
                {
                    "name": "plan_id",
                    "type": "constant",
                    "current": {"value": plan_id_str, "text": plan_id_str},
                    "hide": 2,
                }
            ]
        },
    }


def wrap_for_push(
    dashboard: dict[str, Any], folder_uid: str, *, message: str = "",
) -> dict[str, Any]:
    """Envelope expected by POST /api/dashboards/db."""
    return {
        "dashboard": dashboard,
        "folderUid": folder_uid,
        "message": message or f"Provisioned by Proj Clarion",
        "overwrite": True,
    }
