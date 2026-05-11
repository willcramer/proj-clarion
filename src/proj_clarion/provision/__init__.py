"""Provision — push DashboardSpec / AlertSpec to Grafana Cloud.

For v0.4 we generate the resources locally (always) and only push to Cloud
when an explicit `--push` is set. The dry-run default writes JSON to disk
under `data/generated/<plan_id>/` so an SE can inspect what would land
before committing.

Public surface:
- `build_assets(plan)` → `ProvisionAssets` with the dashboards + alerts
  already serialised to dict form
- `push_assets(client, assets, plan_id)` → push everything to Cloud
- `GrafanaClient` — minimal HTTP client around the Grafana stack URL
"""

from proj_clarion.provision.alerts import build_alert_rule
from proj_clarion.provision.assembler import ProvisionAssets, build_assets, push_assets
from proj_clarion.provision.client import GrafanaClient
from proj_clarion.provision.dashboards import build_dashboard
from proj_clarion.provision.folders import ensure_folder

__all__ = [
    "GrafanaClient",
    "ProvisionAssets",
    "build_alert_rule",
    "build_assets",
    "build_dashboard",
    "ensure_folder",
    "push_assets",
]
