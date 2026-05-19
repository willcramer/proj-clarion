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
from proj_clarion.storage import (
    PipelineRepo, ProfileAuditRepo, ProfileRepo, session_scope,
)

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


class ProfileAuditEntry(BaseModel):
    audit_id:   int
    timestamp:  datetime
    profile_id: str
    actor:      str
    prompt:     str
    summary:    str
    additions:  dict[str, int]
    applied:    bool
    # Only populated by the global /audit feed, never by per-profile.
    url:        str | None = None
    company:    str | None = None


class ProfileAuditResponse(BaseModel):
    entries: list[ProfileAuditEntry]
    total:   int
    limit:   int
    offset:  int


# IMPORTANT: lives BEFORE `/{profile_id}` so FastAPI doesn't match
# "audit" as a profile_id. Same trick we use for /api/plans/audit.
@router.get("/audit", response_model=ProfileAuditResponse)
def global_profile_audit(limit: int = 100, offset: int = 0) -> ProfileAuditResponse:
    """Every profile extend across the whole stack, newest first. Used
    by the global /audit page to render a "Profile changes" section
    next to plan_audit_log and demo_sessions."""
    if limit < 1: limit = 1
    if limit > 500: limit = 500
    if offset < 0: offset = 0
    with session_scope() as s:
        repo = ProfileAuditRepo()
        rows = repo.list_all(s, limit=limit, offset=offset)
        total = repo.count_all(s)
    return ProfileAuditResponse(
        entries=[
            ProfileAuditEntry(
                audit_id=r["audit_id"], timestamp=r["created_at"],
                profile_id=r["profile_id"], actor=r["actor"],
                prompt=r["prompt"], summary=r["summary"],
                additions=r["additions"], applied=r["applied"],
                url=r.get("url"), company=r.get("company"),
            )
            for r in rows
        ],
        total=total, limit=limit, offset=offset,
    )


@router.get("/{profile_id}/audit", response_model=list[ProfileAuditEntry])
def per_profile_audit(profile_id: str) -> list[ProfileAuditEntry]:
    """All extends for one profile, newest first. Powers the chat panel's
    initial render on the Profile detail page."""
    with session_scope() as s:
        # 404 if the profile itself is gone; profile_audit_log has FK
        # ON DELETE CASCADE so a deleted profile has no history anyway.
        profile = ProfileRepo().get(s, profile_id)
        if profile is None:
            raise HTTPException(status_code=404, detail=f"profile {profile_id} not found")
        rows = ProfileAuditRepo().history(s, profile_id, limit=200)
    return [
        ProfileAuditEntry(
            audit_id=r["audit_id"], timestamp=r["created_at"],
            profile_id=r["profile_id"], actor=r["actor"],
            prompt=r["prompt"], summary=r["summary"],
            additions=r["additions"], applied=r["applied"],
        )
        for r in rows
    ]


@router.get("/{profile_id}", response_model=CompanyProfile)
def get_profile(profile_id: str) -> CompanyProfile:
    """Full profile for the detail page."""
    with session_scope() as s:
        profile = ProfileRepo().get(s, profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail=f"profile {profile_id} not found")
    return profile


# ─── Accept / dismiss a synthesized claim ─────────────────────────
#
# "Synthesized claims" are entries the research agent flagged because
# it had to fill a field without a citation. The SE reviews them on
# the Profile detail page (Claims tab). Two actions:
#
#   - accept: SE has verified the claim is correct. Remove it from
#     synthesized_flags; the value stays in the profile JSON.
#   - dismiss: same removal, but the audit row records intent so we
#     can distinguish "yes good" from "I just don't want to see it".
#
# Both are additive-to-audit, no destructive change to the profile
# values themselves (the value was already in the profile; the flag
# was just a "review this" marker).

class ClaimDecisionRequest(BaseModel):
    field_path: str
    # "accept" today; "dismiss" reserved for future ("I read it but
    # don't want to mark it as verified") — same removal mechanics.
    decision: str = "accept"


class ClaimDecisionResponse(BaseModel):
    remaining: int
    profile: CompanyProfile


@router.post("/{profile_id}/claims/accept", response_model=ClaimDecisionResponse)
def accept_claim(profile_id: str, body: ClaimDecisionRequest) -> ClaimDecisionResponse:
    """Remove a single synthesized_flags entry by field_path. The
    underlying profile value stays put; the flag is what gets cleared
    so the Claims tab count drops. Records an audit row so the action
    surfaces on the global audit page."""
    if body.decision not in ("accept", "dismiss"):
        raise HTTPException(
            status_code=400,
            detail=f"decision must be 'accept' or 'dismiss', got {body.decision!r}",
        )
    with session_scope() as s:
        profile = ProfileRepo().get(s, profile_id)
        if profile is None:
            raise HTTPException(status_code=404, detail=f"profile {profile_id} not found")

        # Pydantic List[SynthesizedFlag] → filter out the matching path.
        # If there are duplicates with the same path (shouldn't happen
        # but isn't enforced), this drops them all.
        before = list(profile.synthesized_flags or [])
        kept    = [f for f in before if f.field_path != body.field_path]
        removed = [f for f in before if f.field_path == body.field_path]
        if not removed:
            raise HTTPException(
                status_code=404,
                detail=f"no synthesized claim with field_path={body.field_path!r}",
            )

        # Write back via Pydantic so the schema validates on save.
        merged = profile.model_dump(mode="json")
        merged["synthesized_flags"] = [f.model_dump(mode="json") for f in kept]
        updated = CompanyProfile.model_validate(merged)
        ProfileRepo().upsert(s, updated)

        # Audit the decision so the global audit page can show it.
        # We slot it into the same profile_audit_log table as extends;
        # `additions={}` keeps the schema simple, the prompt + summary
        # carry the semantics.
        ProfileAuditRepo().record(
            s, profile_id,
            prompt=f"{body.decision} claim: {body.field_path}",
            summary=(
                f"{body.decision.capitalize()}ed synthesized claim "
                f"{body.field_path!r}. Value remains in profile; "
                f"flag removed from review list."
            ),
            additions={f"synthesized_flags.{body.decision}ed": len(removed)},
            applied=True,
        )

    return ClaimDecisionResponse(remaining=len(kept), profile=updated)


# ─── Extend research ──────────────────────────────────────────────
#
# "Build runs → SE reviews → if profile's missing detail, SE asks the
# assistant to extend." The /api/agents/research/extend endpoint is
# read-only (gives narrative advice). This one actually MUTATES.
#
# Approach: ask Anthropic to return a structured JSON additions
# object scoped to the list fields on CompanyProfile, validate each
# item against its sub-schema, then upsert the merged profile.
# Additions only — no deletes/edits from this surface.

class ExtendRequest(BaseModel):
    prompt: str


class ExtendResponse(BaseModel):
    summary: str
    additions: dict[str, int]
    profile: CompanyProfile


# The list-typed fields on CompanyProfile the SE typically asks us to
# extend. Each maps to the sub-schema name we use in the system prompt.
_EXTENDABLE_LIST_FIELDS: dict[str, str] = {
    "channels":                      "Channel",
    "tech_stack_signals":            "TechStackSignal",
    "agentic_signals":               "AgenticSignal",
    "pain_signals":                  "PainSignal",
    "business_entity_candidates":    "BusinessEntityCandidate",
    "recent_strategic_priorities":   "StrategicPriority",
    "incumbent_observability":       "IncumbentObservability",
}


_EXTEND_SYSTEM_HEADER = """You are extending an existing CompanyProfile JSON for a Grafana
Solutions Engineer. The user describes what's missing or wrong; you
respond with ADDITIVE changes only (never deletions, never edits to
existing items). The SE wants verifiable extensions, not speculation.

Return a single JSON object, no preamble, no markdown fences:

{
  "summary": "<one short sentence on what you added>",
  "additions": {
    "<field>": [<item>, ...]
  }
}

Only include `additions` keys you actually have additions for. Items
must match the field's sub-schema EXACTLY (field names, required
fields, enum values). Don't invent fields the schema doesn't allow.
The schemas are spelled out below.

If you can't extend based on the request (irrelevant ask, no public
evidence, etc.), return:
{"summary": "No additions made because <one-line reason>", "additions": {}}

Don't repeat items that already exist in the current profile (match
by name/key when possible). Cite sources in any field that has a
source_url slot; use "synthesized - needs verification" when you
can't cite.
"""


def _build_extend_system(profile: "CompanyProfile") -> str:
    """Compose the system prompt: header + per-field JSON schemas +
    current profile state. Schemas come from Pydantic so the agent
    sees the EXACT field names + types + enums the validator will
    accept, avoiding "extra_forbidden" / "field required" 502s. */"""
    import json
    from proj_clarion.schemas.company_profile import (
        Channel, TechStackSignal, AgenticSignal, PainSignal,
        BusinessEntityCandidate, StrategicPriority, IncumbentObservability,
    )
    schemas = {
        "channels":                    Channel.model_json_schema(),
        "tech_stack_signals":          TechStackSignal.model_json_schema(),
        "agentic_signals":             AgenticSignal.model_json_schema(),
        "pain_signals":                PainSignal.model_json_schema(),
        "business_entity_candidates":  BusinessEntityCandidate.model_json_schema(),
        "recent_strategic_priorities": StrategicPriority.model_json_schema(),
        "incumbent_observability":     IncumbentObservability.model_json_schema(),
    }
    schema_block = "\n\n=== Field sub-schemas ===\n" + json.dumps(schemas, indent=2)
    profile_block = (
        "\n\n=== Current CompanyProfile ===\n" + profile.model_dump_json(indent=2)
    )
    return _EXTEND_SYSTEM_HEADER + schema_block + profile_block


@router.post("/{profile_id}/extend", response_model=ExtendResponse)
def extend_profile(profile_id: str, body: ExtendRequest) -> ExtendResponse:
    """Apply an SE-driven, agent-produced extension to a CompanyProfile.

    Loads the profile, asks Anthropic for a JSON additions object,
    validates each item against the field's sub-schema, merges into
    the profile (additions only, no edits), and saves. Returns the
    extended profile + a per-field count of what was added.
    """
    import json
    import os
    from anthropic import Anthropic

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise HTTPException(
            status_code=503,
            detail="ANTHROPIC_API_KEY not set; profile extend unavailable",
        )

    prompt = body.prompt.strip()
    if not prompt:
        raise HTTPException(status_code=400, detail="prompt is required")

    with session_scope() as s:
        profile = ProfileRepo().get(s, profile_id)
    if profile is None:
        raise HTTPException(status_code=404, detail=f"profile {profile_id} not found")

    system = _build_extend_system(profile)

    client = Anthropic(api_key=api_key)
    model = os.getenv("ANTHROPIC_MODEL", "claude-opus-4-7")
    from proj_clarion.observability.llm_client import call_anthropic
    try:
        # System block cached: the extend system prompt embeds every
        # extendable field's Pydantic JSON schema (multi-KB). Repeat
        # extends on the same profile within 5 min read those tokens
        # at ~10% input cost.
        msg, _gen_id = call_anthropic(
            client,
            {
                "model": model,
                "max_tokens": 4096,
                "system": [{
                    "type": "text",
                    "text": system,
                    "cache_control": {"type": "ephemeral"},
                }],
                "messages": [{"role": "user", "content": prompt}],
            },
            agent_name="clarion.profile.extend",
            prompt_template="profile.extend",
        )
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"agent call failed: {exc}") from exc

    # Anthropic returns content as a list of blocks; pull the text.
    text = "".join(
        getattr(b, "text", "")
        for b in (msg.content or [])
        if getattr(b, "type", "") == "text"
    ).strip()
    if not text:
        raise HTTPException(status_code=502, detail="agent returned no text")

    # Be lenient: strip ```json fences if the model wrapped its output.
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:].lstrip()

    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"agent returned non-JSON: {exc}; first 200 chars: {text[:200]!r}",
        ) from exc

    summary = str(payload.get("summary") or "Profile extended.")
    raw_additions = payload.get("additions") or {}
    if not isinstance(raw_additions, dict):
        raise HTTPException(status_code=502, detail="agent additions must be an object")

    # Merge each known list field via Pydantic validation: build a
    # candidate CompanyProfile JSON with the new items appended, then
    # round-trip through model_validate so any malformed items raise
    # cleanly. This also exercises field-level validators (provenance
    # uniqueness, etc.) so we never persist a profile that wouldn't
    # round-trip through the schema.
    merged = profile.model_dump(mode="json")
    counts: dict[str, int] = {}
    for field, _ in _EXTENDABLE_LIST_FIELDS.items():
        new_items = raw_additions.get(field)
        if not new_items:
            continue
        if not isinstance(new_items, list):
            raise HTTPException(
                status_code=502,
                detail=f"additions.{field} must be a list",
            )
        merged.setdefault(field, [])
        merged[field].extend(new_items)
        counts[field] = len(new_items)

    if not counts:
        # Nothing landed. Still write the audit row so "I asked but
        # nothing changed" stays visible; applied=False signals the
        # no-op so the UI can render it differently.
        with session_scope() as s:
            ProfileAuditRepo().record(
                s, profile_id,
                prompt=prompt, summary=summary,
                additions={}, applied=False,
            )
        return ExtendResponse(summary=summary, additions={}, profile=profile)

    # Validate the merged blob against the schema so we surface
    # validation errors clearly to the UI instead of corrupting state.
    try:
        extended = CompanyProfile.model_validate(merged)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(
            status_code=502,
            detail=f"agent produced invalid additions: {exc}",
        ) from exc

    with session_scope() as s:
        ProfileRepo().upsert(s, extended)
        ProfileAuditRepo().record(
            s, profile_id,
            prompt=prompt, summary=summary,
            additions=counts, applied=True,
        )

    return ExtendResponse(summary=summary, additions=counts, profile=extended)


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
