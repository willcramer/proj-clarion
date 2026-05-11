"""Idempotent folder management + cleanup helpers.

Each plan gets its own Grafana folder so an SE can find or delete the
generated assets without trawling. Folder naming: `Proj Clarion / <plan_id_8>`.
Folders in Grafana are addressed by UID; we derive a deterministic UID from
the plan_id so re-runs target the same folder.

The list/delete/orphan helpers below let multiple call sites share one
implementation: the per-plan delete path used by `provision clear`, the
delete-by-uid path used after a plan has already been removed from the
DB, and the orphan finder used by the cleanup UI.
"""

from __future__ import annotations

import re
from typing import Any
from uuid import UUID

from proj_clarion.provision.client import GrafanaClient

CLARION_FOLDER_UID_PREFIX = "clarion-"
# Folder UID is `clarion-<32 hex chars (plan_id minus dashes)>`. The regex
# pulls the hex back so we can reconstruct the plan_id and look it up.
_FOLDER_UID_RE = re.compile(r"^clarion-([0-9a-fA-F]{32})$")


def folder_uid_for_plan(plan_id: str | UUID) -> str:
    """Stable UID derived from plan_id; max 40 chars per Grafana's rules."""
    pid = str(plan_id).replace("-", "")[:32]
    return f"clarion-{pid}"[:40]


def folder_title_for_plan(
    plan_id: str | UUID,
    prefix: str = "Proj Clarion",
    customer: str | None = None,
) -> str:
    """Folder title shown in Grafana Cloud's left-nav. Includes customer
    slug when supplied so an SE scrolling through `/dashboards/browse`
    sees `Proj Clarion / AcmeRetail / 5ac44b56` instead of an opaque hash.
    Falls back to `Proj Clarion / 5ac44b56` when no customer is known."""
    short = str(plan_id)[:8]
    if customer:
        # Light cleanup: lowercase, drop the prof- prefix if someone
        # passed the raw profile_id by accident, collapse whitespace.
        slug = customer.strip().lower()
        if slug.startswith("prof-"):
            slug = slug[len("prof-"):]
        slug = "-".join(slug.split())
        if slug:
            return f"{prefix} / {slug} / {short}"
    return f"{prefix} / {short}"


def plan_id_from_folder_uid(uid: str) -> str | None:
    """Reverse of folder_uid_for_plan: pull the canonical UUID-with-dashes
    plan_id back out of a folder UID. Returns None for non-clarion UIDs.
    """
    m = _FOLDER_UID_RE.match(uid)
    if not m:
        return None
    h = m.group(1).lower()
    return f"{h[0:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"


def ensure_folder(
    client: GrafanaClient,
    plan_id: str | UUID,
    *,
    prefix: str = "Proj Clarion",
    customer: str | None = None,
) -> dict[str, Any]:
    """Create the folder if missing, otherwise return the existing one.
    Updates the title in-place when an existing folder doesn't yet carry
    the customer slug (so older folders rename when their plan is re-pushed).
    """
    uid = folder_uid_for_plan(plan_id)
    desired_title = folder_title_for_plan(plan_id, prefix=prefix, customer=customer)
    existing = client.get(f"/api/folders/{uid}", allow_404=True)
    if existing:
        if existing.get("title") != desired_title:
            client.put(f"/api/folders/{uid}", {
                "title": desired_title,
                "version": existing.get("version", 0),
            })
            existing["title"] = desired_title
        return existing
    return client.post(
        "/api/folders",
        {
            "uid": uid,
            "title": desired_title,
        },
    )


def list_clarion_folders(client: GrafanaClient) -> list[dict[str, Any]]:
    """Return every Grafana folder whose UID starts with `clarion-`.

    Each item has at least {uid, title, url}; Grafana's /api/folders
    endpoint returns more, but we don't promise more than that.
    """
    all_folders: list[dict[str, Any]] = client.get("/api/folders") or []
    return [f for f in all_folders
            if isinstance(f, dict) and str(f.get("uid", "")).startswith(CLARION_FOLDER_UID_PREFIX)]


def delete_folder_by_uid(client: GrafanaClient, uid: str) -> None:
    """Delete a Grafana folder + everything it contains (dashboards + alert
    rules) via `?forceDeleteRules=true`. Idempotent: a 404 is a no-op."""
    client.delete(f"/api/folders/{uid}?forceDeleteRules=true")


def find_orphan_folders(
    client: GrafanaClient,
    known_plan_ids: set[str],
) -> list[dict[str, Any]]:
    """Return clarion-* folders whose plan_id is NOT in `known_plan_ids`.

    Caller passes the set of currently-known plans (typically from
    `PlanRepo.list`). Anything in Cloud whose UID doesn't map back to
    one of those is "orphan" — left over from a deleted plan, or from
    a `provision push` that wasn't followed by a DB write (rare).
    """
    out: list[dict[str, Any]] = []
    for f in list_clarion_folders(client):
        uid = f.get("uid", "")
        plan_id = plan_id_from_folder_uid(uid)
        if plan_id is None:
            # uid prefix matched but format unexpected — call it orphan
            # so the user sees it and decides
            out.append({**f, "plan_id": None, "reason": "uid did not parse"})
            continue
        if plan_id not in known_plan_ids:
            out.append({**f, "plan_id": plan_id, "reason": "plan not in DB"})
    return out
