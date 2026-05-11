"""Build + push provisioning assets for a plan.

`build_assets(plan)` is pure — it returns a `ProvisionAssets` snapshot of
every resource without touching the network or disk. `push_assets(...)` and
`save_assets_to_disk(...)` are the side-effects.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import structlog

from proj_clarion.provision.alerts import build_alert_rule
from proj_clarion.provision.client import GrafanaClient
from proj_clarion.provision.command_center import build_command_center_dashboard
from proj_clarion.provision.dashboards import build_dashboard, wrap_for_push
from proj_clarion.provision.folders import (
    ensure_folder,
    folder_title_for_plan,
    folder_uid_for_plan,
)
from proj_clarion.schemas import DemoPlan

_logger = structlog.get_logger()


@dataclass
class ProvisionAssets:
    plan_id: str
    folder_uid: str
    folder_title: str
    dashboards: list[dict[str, Any]] = field(default_factory=list)
    alert_rules: list[dict[str, Any]] = field(default_factory=list)


def _customer_slug_from_plan(plan: DemoPlan) -> str | None:
    """Mirror of kg_publish.model_rules._customer_slug_from_plan so the
    folder title and the model-rule file name use the same slug.
    `prof-initech_industrial` → `initech_industrial`. Returns None when
    there's no usable profile_id so callers can fall back."""
    pid = (plan.source_profile_id or "").strip()
    if pid.startswith("prof-"):
        pid = pid[len("prof-"):]
    pid = pid.strip("-").lower()
    return pid or None


def build_assets(
    plan: DemoPlan,
    *,
    customer: str | None = None,
    dashboard_style: str = "command-center",
) -> ProvisionAssets:
    """Pure: build dashboards + alert rules from a plan. No network.

    `customer` overrides the auto-derived slug used for the folder title.
    Falls back to deriving from `plan.source_profile_id` so a CLI run
    without `--customer` still gets a customer-named folder.

    `dashboard_style` picks the dashboard layout:
      - "command-center" (DEFAULT) → ONE dense, web-app-feel dashboard
        per plan: hero KPIs, breakdowns by channel/region, drill by
        store (or channel for non-retail verticals), entity-graph link.
        Mirrors the GS-built `imlcpt6` reference. Vertical-aware.
      - "legacy" → N small dashboards, one per `plan.dashboard_specs`,
        with heuristic panel queries. Kept for backward compatibility
        and for anyone iterating on the planner's per-audience layout.
    """
    plan_id = str(plan.plan_id)
    folder_uid = folder_uid_for_plan(plan_id)
    customer_slug = customer or _customer_slug_from_plan(plan)
    folder_title = folder_title_for_plan(plan_id, customer=customer_slug)

    if dashboard_style == "command-center":
        # Single, dense, vertical-aware dashboard. We deliberately ignore
        # the planner's `dashboard_specs` for the layout — the command
        # center is a fixed template that adapts to vertical, not a
        # per-spec render. The specs still drive alert rule selection
        # below.
        dashboards = [
            build_command_center_dashboard(
                plan, customer=customer_slug, folder_uid=folder_uid,
            )
        ]
    else:
        dashboards = [
            build_dashboard(spec, plan_id, folder_uid=folder_uid)
            for spec in plan.dashboard_specs
        ]
    alert_rules = [
        build_alert_rule(spec, plan_id, folder_uid=folder_uid)
        for spec in plan.alert_specs
    ]
    return ProvisionAssets(
        plan_id=plan_id,
        folder_uid=folder_uid,
        folder_title=folder_title,
        dashboards=dashboards,
        alert_rules=alert_rules,
    )


def save_assets_to_disk(assets: ProvisionAssets, root: Path) -> Path:
    """Write assets under `<root>/<plan_id>/{folder.json,dashboards/...,alerts/...}`.

    Returns the directory we wrote to.
    """
    out_dir = root / assets.plan_id
    (out_dir / "dashboards").mkdir(parents=True, exist_ok=True)
    (out_dir / "alerts").mkdir(parents=True, exist_ok=True)

    (out_dir / "folder.json").write_text(json.dumps({
        "uid": assets.folder_uid,
        "title": assets.folder_title,
    }, indent=2))

    for dash in assets.dashboards:
        (out_dir / "dashboards" / f"{dash['uid']}.json").write_text(
            json.dumps(dash, indent=2)
        )
    for rule in assets.alert_rules:
        (out_dir / "alerts" / f"{rule['uid']}.json").write_text(
            json.dumps(rule, indent=2)
        )
    return out_dir


def push_assets(
    client: GrafanaClient,
    assets: ProvisionAssets,
    *,
    sweep_orphans_against: set[str] | None = None,
) -> dict[str, int]:
    """Push folder + dashboards + alert rules to Grafana Cloud.

    `sweep_orphans_against` is the set of plan_ids the API knows about
    right now. When supplied we delete every clarion-* folder whose
    plan_id isn't in that set BEFORE pushing this plan's assets, so
    Cloud doesn't accumulate dead folders/dashboards/alerts from runs
    whose plans were deleted in Postgres. Pass `None` to skip cleanup
    (e.g. for `provision push` without --cleanup).

    Returns a counts dict for reporting. Raises on the first hard failure
    (auth, server error, etc.).
    """
    counts = {
        "folders": 0, "dashboards": 0, "alert_rules": 0,
        "alerts_failed": 0, "orphans_deleted": 0, "dashboards_pruned": 0,
    }

    # Optional: sweep orphan clarion folders before pushing. Folder
    # delete with forceDeleteRules=true cascades to dashboards + alerts,
    # so this is the single hammer that cleans the lot.
    if sweep_orphans_against is not None:
        from proj_clarion.provision.folders import (
            delete_folder_by_uid,
            find_orphan_folders,
        )
        orphans = find_orphan_folders(client, sweep_orphans_against)
        for f in orphans:
            try:
                delete_folder_by_uid(client, f["uid"])
                counts["orphans_deleted"] += 1
                _logger.info("provision.orphan.deleted",
                             uid=f["uid"], title=f.get("title"))
            except Exception as exc:  # noqa: BLE001
                _logger.warning("provision.orphan.delete_failed",
                                uid=f["uid"], error=str(exc))

    # Folder first — POST creates, PUT updates the title in-place when
    # this plan's existing folder doesn't yet carry the customer slug.
    existing = client.get(f"/api/folders/{assets.folder_uid}", allow_404=True)
    folder_existed = existing is not None
    if not folder_existed:
        client.post("/api/folders", {
            "uid": assets.folder_uid, "title": assets.folder_title,
        })
    elif existing.get("title") != assets.folder_title:
        client.put(f"/api/folders/{assets.folder_uid}", {
            "title": assets.folder_title,
            "version": existing.get("version", 0),
        })
        _logger.info("provision.folder.renamed",
                     uid=assets.folder_uid,
                     from_title=existing.get("title"),
                     to_title=assets.folder_title)
    counts["folders"] = 1

    # Stale dashboard cleanup — when a plan's folder already exists from
    # a prior push, list its dashboards and delete any whose UIDs aren't
    # in this push. Catches the legacy → command-center transition (old
    # per-spec dashboards lingering after the template flip) and any
    # planner re-runs that produce different dashboard_ids. Folder is
    # this plan's own folder, so we only touch dashboards we own.
    if folder_existed:
        new_uids = {d["uid"] for d in assets.dashboards}
        try:
            existing_dashboards = client.get(
                f"/api/search?folderUIDs={assets.folder_uid}&type=dash-db"
            ) or []
            for d in existing_dashboards:
                uid = d.get("uid")
                if uid and uid not in new_uids:
                    client.delete(f"/api/dashboards/uid/{uid}")
                    counts["dashboards_pruned"] = counts.get("dashboards_pruned", 0) + 1
                    _logger.info("provision.dashboard.pruned",
                                 uid=uid, title=d.get("title"))
        except Exception as exc:  # noqa: BLE001 — never fail push on cleanup
            _logger.warning("provision.dashboard.prune_failed",
                            folder_uid=assets.folder_uid, error=str(exc)[:200])

    # Dashboards
    for dash in assets.dashboards:
        body = wrap_for_push(dash, assets.folder_uid)
        client.post("/api/dashboards/db", body)
        counts["dashboards"] += 1

    # Alert rules — provisioning API is idempotent on UID via PUT
    for rule in assets.alert_rules:
        try:
            existing_rule = client.get(
                f"/api/v1/provisioning/alert-rules/{rule['uid']}", allow_404=True
            )
            if existing_rule:
                client.put(f"/api/v1/provisioning/alert-rules/{rule['uid']}", rule)
            else:
                client.post("/api/v1/provisioning/alert-rules", rule)
            counts["alert_rules"] += 1
        except Exception as exc:  # noqa: BLE001
            _logger.warning("alert.push.failed", uid=rule["uid"], error=str(exc))
            counts["alerts_failed"] += 1

    return counts
