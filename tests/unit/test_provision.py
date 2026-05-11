"""Provisioning unit tests — `build_assets` is pure, so most assertions don't
need any infrastructure. We mock the Grafana HTTP client to verify the push
flow calls the right endpoints without touching the network.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from proj_clarion.provision.alerts import _parse_predicate, build_alert_rule
from proj_clarion.provision.assembler import (
    build_assets,
    push_assets,
    save_assets_to_disk,
)
from proj_clarion.provision.dashboards import build_dashboard
from proj_clarion.provision.folders import folder_uid_for_plan
from proj_clarion.schemas import DemoPlan

FIXTURES = Path(__file__).parent.parent / "fixtures"
EXAMPLE_PLAN = (
    Path(__file__).parent.parent.parent / "data" / "plans" / "acme_retail-example.json"
)


@pytest.fixture
def acme_retail_plan() -> DemoPlan:
    if not EXAMPLE_PLAN.exists():
        pytest.skip("data/plans/acme_retail-example.json not present (run `just plan`)")
    return DemoPlan.model_validate_json(EXAMPLE_PLAN.read_text())


def test_predicate_parser_handles_common_shapes() -> None:
    assert _parse_predicate("> 0.05") == ("gt", 0.05)
    assert _parse_predicate(">= 100") == ("gt", 100.0)
    assert _parse_predicate("< 0.99") == ("lt", 0.99)
    assert _parse_predicate("nonsense") == ("gt", 0.0)


def test_folder_uid_is_deterministic_and_short() -> None:
    pid = "11111111-2222-3333-4444-555555555555"
    uid = folder_uid_for_plan(pid)
    assert uid == folder_uid_for_plan(pid)
    assert uid.startswith("clarion-")
    assert len(uid) <= 40


def test_build_assets_produces_one_dashboard_per_spec(acme_retail_plan: DemoPlan) -> None:
    assets = build_assets(acme_retail_plan)
    assert len(assets.dashboards) == len(acme_retail_plan.dashboard_specs)
    assert len(assets.alert_rules) == len(acme_retail_plan.alert_specs)
    assert assets.folder_uid.startswith("clarion-")


def test_dashboard_panels_reference_correct_datasources(acme_retail_plan: DemoPlan) -> None:
    assets = build_assets(acme_retail_plan)
    valid_types = {
        "grafana-postgresql-datasource",  # canonical Postgres plugin id
        "prometheus", "loki", "tempo",
    }
    for dash in assets.dashboards:
        # Every panel must have a datasource ref
        for panel in dash["panels"]:
            assert "datasource" in panel
            assert panel["datasource"]["type"] in valid_types
            # And every panel target points at SOME datasource
            for tgt in panel["targets"]:
                assert "datasource" in tgt


def test_dashboard_has_constant_plan_id_template(acme_retail_plan: DemoPlan) -> None:
    assets = build_assets(acme_retail_plan)
    for dash in assets.dashboards:
        templ = dash["templating"]["list"]
        assert any(v["name"] == "plan_id" for v in templ)


def test_alert_rules_preserve_plan_id_in_labels(acme_retail_plan: DemoPlan) -> None:
    assets = build_assets(acme_retail_plan)
    plan_short = str(acme_retail_plan.plan_id)[:8]
    for rule in assets.alert_rules:
        assert rule["labels"]["plan_id"] == plan_short
        assert rule["labels"]["managed_by"] == "proj-clarion"
        assert rule["folderUID"] == assets.folder_uid


def test_alert_rules_use_correct_datasource_uid(acme_retail_plan: DemoPlan) -> None:
    """Each alert's first query (refId=A) must reference the spec's
    datasource_kind."""
    assets = build_assets(acme_retail_plan)
    by_uid = {rule["uid"]: rule for rule in assets.alert_rules}
    for spec in acme_retail_plan.alert_specs:
        rule = by_uid[spec.alert_id]
        a_node = rule["data"][0]
        ds_type = a_node["model"]["datasource"]["type"]
        assert ds_type == spec.datasource_kind


def test_save_assets_to_disk_writes_expected_files(
    acme_retail_plan: DemoPlan, tmp_path: Path
) -> None:
    assets = build_assets(acme_retail_plan)
    out = save_assets_to_disk(assets, tmp_path)
    assert (out / "folder.json").exists()
    assert sum(1 for _ in (out / "dashboards").glob("*.json")) == len(assets.dashboards)
    assert sum(1 for _ in (out / "alerts").glob("*.json")) == len(assets.alert_rules)


def test_push_assets_calls_correct_endpoints(acme_retail_plan: DemoPlan) -> None:
    """End-to-end push flow against a mock client: folder GET → POST,
    dashboards POST, alerts GET → POST/PUT."""
    assets = build_assets(acme_retail_plan)

    client = MagicMock()
    client.get.return_value = None  # nothing exists yet
    client.post.return_value = {}
    client.put.return_value = {}

    counts = push_assets(client, assets)
    assert counts["folders"] == 1
    assert counts["dashboards"] == len(assets.dashboards)
    assert counts["alert_rules"] == len(assets.alert_rules)
    assert counts["alerts_failed"] == 0

    posted_paths = [c.args[0] for c in client.post.call_args_list]
    assert any(p.startswith("/api/folders") for p in posted_paths)
    assert any(p == "/api/dashboards/db" for p in posted_paths)
    assert any(p.startswith("/api/v1/provisioning/alert-rules") for p in posted_paths)


def test_push_assets_uses_put_for_existing_alert_rules(acme_retail_plan: DemoPlan) -> None:
    assets = build_assets(acme_retail_plan)
    client = MagicMock()
    # Folder exists; every alert rule exists too
    def fake_get(path: str, allow_404: bool = False):
        if path.startswith("/api/folders/"):
            return {"uid": assets.folder_uid}
        if path.startswith("/api/v1/provisioning/alert-rules/"):
            return {"uid": "exists"}
        return None
    client.get.side_effect = fake_get
    client.post.return_value = {}
    client.put.return_value = {}

    counts = push_assets(client, assets)
    assert counts["alert_rules"] == len(assets.alert_rules)
    # No POST to alert-rules — all updates went through PUT
    assert all(
        not c.args[0].startswith("/api/v1/provisioning/alert-rules")
        for c in client.post.call_args_list
    )
