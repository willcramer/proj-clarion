"""AlertSpec → Grafana managed alert rule.

Targets the v1 provisioning API at `/api/v1/provisioning/alert-rules` —
each rule is a single ProvisionedAlertRule object with one or more `data`
queries, a condition, and a folder + group.

The query body shape changes by datasource:
- prometheus / loki: PromQL/LogQL with `expr` (instant or range) → reduce → math
- postgres: SQL via the postgres datasource plugin → reduce → math
- The `threshold_predicate` from the spec (e.g., "> 0.05") becomes the
  condition expression.

We default `for=2m`, `noDataState=OK`, `execErrState=Error`. Routes go into
labels (e.g., `route="#oncall"`); the user wires routes → contact points in
their Grafana notification policies separately.
"""

from __future__ import annotations

import re
from typing import Any
from uuid import UUID

from proj_clarion.provision.dashboards import _ds_uid
from proj_clarion.schemas import AlertSpec


_PRED_RE = re.compile(r"^\s*(>=|<=|==|!=|>|<)\s*(-?\d+(?:\.\d+)?)\s*$")


def _parse_predicate(pred: str) -> tuple[str, float]:
    """'> 0.05' → ('gt', 0.05)."""
    m = _PRED_RE.match(pred)
    if not m:
        # Default to >0 if we can't parse — surfaces as "alert when expr is non-zero"
        return ("gt", 0.0)
    op_map = {">": "gt", ">=": "gt", "<": "lt", "<=": "lt", "==": "eq", "!=": "ne"}
    return op_map[m.group(1)], float(m.group(2))


def _query_node(ref_id: str, datasource_uid: str, datasource_type: str,
                model: dict[str, Any]) -> dict[str, Any]:
    return {
        "refId": ref_id,
        "datasourceUid": datasource_uid,
        "queryType": model.get("queryType", ""),
        "relativeTimeRange": {"from": 600, "to": 0},
        "model": {**model, "refId": ref_id, "datasource": {
            "type": datasource_type, "uid": datasource_uid,
        }},
    }


def _expression_node(ref_id: str, expression: str, expr_type: str = "math") -> dict[str, Any]:
    return {
        "refId": ref_id,
        "datasourceUid": "__expr__",
        "queryType": "",
        "relativeTimeRange": {"from": 0, "to": 0},
        "model": {
            "refId": ref_id,
            "type": expr_type,
            "expression": expression,
            "datasource": {"type": "__expr__", "uid": "__expr__"},
        },
    }


def build_alert_rule(
    spec: AlertSpec,
    plan_id: str | UUID,
    *,
    folder_uid: str,
    rule_group: str = "proj-clarion",
    interval_seconds: int = 60,
) -> dict[str, Any]:
    """Build a single Grafana managed alert rule from a spec.

    Returns a body suitable for `POST /api/v1/provisioning/alert-rules`.
    """
    plan_id_str = str(plan_id)
    op, threshold = _parse_predicate(spec.threshold_predicate)
    ds_kind = spec.datasource_kind
    ds_uid = _ds_uid(ds_kind)

    if ds_kind == "prometheus":
        query_model = {"expr": spec.query, "instant": True, "range": False}
    elif ds_kind == "loki":
        query_model = {"expr": spec.query, "queryType": "instant"}
    elif ds_kind == "postgres":
        query_model = {
            "format": "table",
            "rawQuery": True,
            "rawSql": spec.query.replace("$plan_id", plan_id_str),
        }
    else:
        query_model = {"expr": spec.query}

    a_node = _query_node("A", ds_uid, ds_kind, query_model)
    # B = reduce A to a single value (last) so the threshold expression has scalar input
    b_node = {
        "refId": "B",
        "datasourceUid": "__expr__",
        "queryType": "",
        "relativeTimeRange": {"from": 0, "to": 0},
        "model": {
            "refId": "B",
            "type": "reduce",
            "reducer": "last",
            "expression": "A",
            "datasource": {"type": "__expr__", "uid": "__expr__"},
        },
    }
    c_node = _expression_node("C", f"$B {{op_to_math(op)}} {threshold}".replace(
        "{op_to_math(op)}", _op_to_math(op)
    ))

    severity_to_label = {"critical": "critical", "high": "high",
                          "medium": "warning", "low": "info"}

    return {
        "uid": spec.alert_id,
        "title": spec.title,
        "ruleGroup": rule_group,
        "folderUID": folder_uid,
        "condition": "C",
        "data": [a_node, b_node, c_node],
        "noDataState": "OK",
        "execErrState": "Error",
        "for": "2m",
        "annotations": {
            "summary": spec.business_subject_line,
            "description": spec.technical_subject_line,
            "clarion_plan_id": plan_id_str,
        },
        "labels": {
            "severity": severity_to_label.get(spec.severity, "warning"),
            "plan_id": plan_id_str[:8],
            "route": ",".join(spec.routes_to) if spec.routes_to else "",
            "managed_by": "proj-clarion",
        },
        "isPaused": False,
        "intervalSeconds": interval_seconds,
    }


def _op_to_math(op: str) -> str:
    return {"gt": ">", "lt": "<", "eq": "==", "ne": "!="}.get(op, ">")
