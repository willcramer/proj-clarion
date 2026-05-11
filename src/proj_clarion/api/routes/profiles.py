"""CompanyProfile read + delete endpoints + light summary list for the UI.

The list endpoint also surfaces in-flight pipelines as placeholder
"researching" rows so the page isn't empty during the 1-2 minutes
between hitting Build and the research phase landing a profile in
Postgres. Placeholders carry `pending=true` and a `pipeline_id` so the
UI can deep-link to the build page; once research lands, the real
profile appears in the list and the placeholder drops out (because the
pipelines query filters on `profile_id IS NULL`)."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from proj_clarion.schemas import CompanyProfile
from proj_clarion.storage import PipelineRepo, ProfileRepo, session_scope

PROJECT_ROOT = Path(__file__).resolve().parents[4]
PROFILES_DIR = PROJECT_ROOT / "data" / "profiles"

router = APIRouter(prefix="/api/profiles", tags=["profiles"])


class ProfileSummary(BaseModel):
    """Lean shape for the list page — avoids shipping every CompanyProfile to the client."""

    profile_id: str
    company_name: str | None
    primary_url: str
    created_at: datetime
    pain_signal_count: int
    tech_signal_count: int
    synthesized_flag_count: int
    # Placeholder rows for in-flight builds (no profile yet) carry
    # pending=True + pipeline_id; the UI shows a "Researching..." card
    # and links to /pipelines?p=<id>.
    pending: bool = False
    pipeline_id: str | None = None
    pipeline_status: str | None = None


@router.get("", response_model=list[ProfileSummary])
def list_profiles(limit: int = 50) -> list[ProfileSummary]:
    """Newest first. Returns: in-flight pipelines (no profile yet) +
    completed profiles. Placeholders sort to the top because their
    started_at is now-ish and they're explicitly marked `pending`."""
    out: list[ProfileSummary] = []
    with session_scope() as s:
        # In-flight builds whose research hasn't yet produced a profile.
        pipe_repo = PipelineRepo()
        for p in pipe_repo.list(s, limit=20, status="running"):
            if p.get("profile_id"):
                continue  # research already landed; real profile shows below
            out.append(ProfileSummary(
                profile_id=f"pending-{p['pipeline_id']}",
                company_name=p.get("company"),
                primary_url=p.get("url") or "",
                created_at=p["started_at"],
                pain_signal_count=0,
                tech_signal_count=0,
                synthesized_flag_count=0,
                pending=True,
                pipeline_id=p["pipeline_id"],
                pipeline_status=p["status"],
            ))

        # Completed profiles from company_profiles.
        repo = ProfileRepo()
        for pid, created_at, url in repo.list(s, limit=limit):
            profile = repo.get(s, pid)
            if profile is None:
                continue
            out.append(ProfileSummary(
                profile_id=pid,
                company_name=profile.company.name if profile.company else None,
                primary_url=url,
                created_at=created_at,
                pain_signal_count=len(profile.pain_signals or []),
                tech_signal_count=len(profile.tech_stack_signals or []),
                synthesized_flag_count=len(profile.synthesized_flags or []),
            ))
    return out


@router.get("/{profile_id}", response_model=CompanyProfile)
def get_profile(profile_id: str) -> CompanyProfile:
    """Full profile for the detail page."""
    with session_scope() as s:
        profile = ProfileRepo().get(s, profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail=f"profile {profile_id} not found")
    return profile


@router.delete("/{profile_id}")
def delete_profile(profile_id: str, cleanup_cloud: bool = False) -> dict[str, object]:
    """Drop the profile from Postgres. Cascades to every plan that uses
    it (and through them: kg_nodes/edges, business_events, audit log).

    With `?cleanup_cloud=true`, runs `proj-clarion provision clear` for
    each cascaded plan BEFORE the DB delete, so dashboards + alert rules
    are removed from Grafana Cloud. Per-plan results returned in
    `cloud_cleanup_per_plan`. Mimir/Loki/Tempo series and KG entity
    records aren't deleted — they age out naturally.
    """
    import subprocess
    from pathlib import Path

    PROJECT_ROOT = Path(__file__).resolve().parents[4]

    # Snapshot the cascade scope BEFORE delete so we know what plans to clean
    with session_scope() as s:
        from sqlalchemy import text
        plan_ids = [
            str(r[0]) for r in s.execute(
                text("SELECT plan_id FROM demo_plans WHERE source_profile_id = :pid"),
                {"pid": profile_id},
            ).fetchall()
        ]

    cloud_results: list[dict[str, object]] = []
    if cleanup_cloud:
        for pid in plan_ids:
            proc = subprocess.run(
                ["uv", "run", "python", "-m", "proj_clarion.cli.main",
                 "provision", "clear", pid, "--yes"],
                cwd=str(PROJECT_ROOT),
                capture_output=True, text=True, timeout=30,
            )
            cloud_results.append({
                "plan_id":      pid,
                "ok":           proc.returncode == 0,
                "stdout_tail":  proc.stdout.strip().splitlines()[-3:] if proc.stdout else [],
                "stderr_tail":  proc.stderr.strip().splitlines()[-3:] if proc.stderr else [],
            })

    with session_scope() as s:
        deleted = ProfileRepo().delete(s, profile_id)

    if not deleted:
        raise HTTPException(status_code=404, detail=f"profile {profile_id} not found")

    on_disk = PROFILES_DIR / f"{profile_id}.json"
    file_removed = False
    try:
        if on_disk.exists():
            on_disk.unlink()
            file_removed = True
    except OSError:
        pass

    return {
        "deleted": True,
        "profile_id": profile_id,
        "cascaded_plans": len(plan_ids),
        "cascaded_plan_ids": plan_ids,
        "json_file_removed": file_removed,
        "cloud_cleanup_per_plan": cloud_results if cleanup_cloud else None,
    }
