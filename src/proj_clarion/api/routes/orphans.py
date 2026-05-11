"""Cloud-side orphan cleanup.

Surfaces `clarion-*` Grafana folders whose plan is no longer in the
DB, so an SE can clean them up after a plan was deleted without the
`cleanup_cloud=true` flag (or via the v0.7 API before the flag existed).

Endpoints:
- GET    /api/orphans/folders         — list current orphans
- DELETE /api/orphans/folders/{uid}   — delete one folder + its
                                         dashboards/alerts (cascade)
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import text

from proj_clarion.provision.client import GrafanaAuthError, GrafanaClient
from proj_clarion.provision.folders import (
    CLARION_FOLDER_UID_PREFIX,
    delete_folder_by_uid,
    find_orphan_folders,
)
from proj_clarion.storage import session_scope

router = APIRouter(prefix="/api/orphans", tags=["orphans"])


class OrphanFolder(BaseModel):
    uid: str
    title: str
    url: str
    plan_id: str | None
    reason: str


@router.get("/folders", response_model=list[OrphanFolder])
def list_orphans() -> list[OrphanFolder]:
    """Cross-reference Cloud folders with the DB plan list, return the
    deltas. Returns an empty list when everything is in sync."""
    with session_scope() as s:
        rows = s.execute(text("SELECT plan_id::text FROM demo_plans")).fetchall()
    known = {r[0] for r in rows}

    try:
        with GrafanaClient() as client:
            raw = find_orphan_folders(client, known)
    except GrafanaAuthError as exc:
        raise HTTPException(status_code=502, detail=f"Grafana auth: {exc}") from exc

    return [
        OrphanFolder(
            uid=f.get("uid", ""),
            title=f.get("title", ""),
            url=f.get("url", ""),
            plan_id=f.get("plan_id"),
            reason=f.get("reason", ""),
        )
        for f in raw
    ]


@router.delete("/folders/{uid}")
def delete_orphan(uid: str) -> dict[str, object]:
    """Delete one folder by UID. Refuses non-`clarion-*` UIDs as a
    guardrail — this endpoint is purpose-built for Clarion cleanup, not
    a generic folder-delete proxy."""
    if not uid.startswith(CLARION_FOLDER_UID_PREFIX):
        raise HTTPException(
            status_code=400,
            detail=f"Refusing to delete non-Clarion folder {uid!r}; "
                   f"this endpoint only handles {CLARION_FOLDER_UID_PREFIX}* UIDs.",
        )
    try:
        with GrafanaClient() as client:
            delete_folder_by_uid(client, uid)
    except GrafanaAuthError as exc:
        raise HTTPException(status_code=502, detail=f"Grafana auth: {exc}") from exc

    return {"deleted": True, "uid": uid}
