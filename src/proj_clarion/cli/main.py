"""Click-based CLI. Run `proj-clarion --help` for the menu."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

import click
from dotenv import load_dotenv
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from proj_clarion import __version__
from proj_clarion.observability import configure_logging, init_telemetry
from proj_clarion.schemas import CompanyProfile

PROFILES_DIR = Path("data/profiles")
PLANS_DIR = Path("data/plans")
console = Console()


@click.group()
@click.version_option(__version__, prog_name="proj-clarion")
def cli() -> None:
    """Proj Clarion — turn a URL into a Grafana demo."""
    load_dotenv(override=True)
    configure_logging()
    init_telemetry()


@cli.command()
@click.option("--probe/--no-probe", default=True,
              help="Live AUTH-probe the Grafana Cloud token (no tenant writes). Default on.")
def doctor(probe: bool) -> None:
    """Preflight: will every signal reach your Grafana Cloud tenant?

    Checks each telemetry signal (metrics / logs / traces / profiles / sigil),
    names the exact Access-Policy scope its token needs, and tells you exactly
    what to set when something's missing. Run this after editing `.env`."""
    from proj_clarion.observability.preflight import telemetry_preflight

    checks = telemetry_preflight(probe=probe)
    table = Table(title="Telemetry preflight — ship-everything-to-Grafana-Cloud")
    table.add_column("Signal")
    table.add_column("Status")
    table.add_column("Scope")
    table.add_column("Detail / fix")
    for c in checks:
        mark = "[green]✓ ok[/green]" if c.ok else (
            "[yellow]! warn[/yellow]" if c.severity == "warn" else "[red]✗ MISSING[/red]")
        detail = c.detail if c.ok else (c.remediation or c.detail)
        table.add_row(c.signal, mark, c.required_scope or "—", detail)
    console.print(table)
    gaps = [c for c in checks if not c.ok]
    if gaps:
        console.print(Panel.fit(
            f"[red]{len(gaps)} signal(s) won't reach your tenant.[/red] Fix the rows above, "
            "then re-run `clarion doctor`.", border_style="red"))
        sys.exit(1)
    console.print(Panel.fit("[green]All telemetry signals are configured to ship to Cloud.[/green]",
                            border_style="green"))


@cli.command()
@click.argument("url")
@click.option("--company", help="Optional company hint")
@click.option("--out", type=click.Path(path_type=Path), help="Override output path")
def research(url: str, company: str | None, out: Path | None) -> None:
    """Run the Research agent against URL → persist the CompanyProfile.

    Persists in two places:
      - Postgres `company_profiles` row (so /profiles list and the v0.8
        pipelines.profile_id FK both work the moment research finishes)
      - JSON snapshot under data/profiles/ (for inspection + v0.7 tooling
        that still reads from disk; will be deprecated once the planner
        loads from DB)

    Before v0.8 only the JSON was written; the DB row appeared later
    when `plan run` called ProfileRepo().upsert(). That gap meant
    pipeline.profile_id couldn't be set until plan phase, which
    FK-violated when we tried to update it from the research-done event.
    """
    from proj_clarion.agents.research import run_research
    from proj_clarion.storage import ProfileRepo, session_scope

    PROFILES_DIR.mkdir(parents=True, exist_ok=True)

    console.print(Panel.fit(f"[bold]Research:[/bold] {url}", border_style="cyan"))
    state = asyncio.run(run_research(url, company_hint=company))

    if state["errors"]:
        console.print("[yellow]Encountered issues during research:[/yellow]")
        for e in state["errors"]:
            console.print(f"  • {e}")

    if not state["profile"]:
        console.print("[red]No profile produced.[/red]")
        sys.exit(2)

    profile = state["profile"]
    out_path = out or (PROFILES_DIR / f"{profile.profile_id}.json")
    out_path.write_text(profile.model_dump_json(indent=2))
    with session_scope() as s:
        ProfileRepo().upsert(s, profile)
    console.print(
        f"[green]Wrote[/green] {out_path} + persisted to Postgres "
        f"([dim]{profile.profile_id}[/dim])"
    )


@cli.command("research-notes")
@click.argument("notes_path", type=click.Path(exists=True, path_type=Path))
@click.option("--company", help="Company name / hint to anchor the profile")
@click.option("--url", "url", default=None,
              help="Optional primary URL to record on the profile (and to "
                   "fetch from when --also-fetch is set)")
@click.option("--also-fetch", is_flag=True, default=False,
              help="ALSO run the normal web/external research and fold the "
                   "notes in as a trusted source (requires --url). Default: "
                   "notes-only, no web access.")
@click.option("--out", type=click.Path(path_type=Path), help="Override output path")
def research_notes(
    notes_path: Path, company: str | None, url: str | None,
    also_fetch: bool, out: Path | None,
) -> None:
    """Build a CompanyProfile from discovery notes — SKIPS the web research.

    Reads NOTES_PATH (a text/markdown file, e.g. notes from a customer
    discovery call) and synthesizes a CompanyProfile from it via the LLM,
    WITHOUT the SEC/GitHub/jobs/Wikidata investigation. Persisted to Postgres
    + data/profiles/ exactly like `research`, so `plan run <profile>` works
    unchanged.

    Use `--also-fetch --url <site>` when you DO want the deep-dive enrichment
    layered on top of your notes.
    """
    from proj_clarion.agents.research import run_research_from_notes
    from proj_clarion.storage import ProfileRepo, session_scope

    PROFILES_DIR.mkdir(parents=True, exist_ok=True)
    notes_text = notes_path.read_text()
    if not notes_text.strip():
        console.print(f"[red]Notes file is empty:[/red] {notes_path}")
        sys.exit(2)
    if also_fetch and not url:
        console.print("[red]--also-fetch requires --url[/red]")
        sys.exit(2)

    mode = "notes + web research" if also_fetch else "notes-only (no web access)"
    console.print(Panel.fit(
        f"[bold]Research from notes:[/bold] {notes_path.name}\n"
        f"mode: {mode}",
        border_style="cyan",
    ))
    state = asyncio.run(run_research_from_notes(
        notes_text, company_hint=company, target_url=url, also_fetch=also_fetch,
    ))

    if state["errors"]:
        console.print("[yellow]Encountered issues during synthesis:[/yellow]")
        for e in state["errors"]:
            console.print(f"  • {e}")
    if not state["profile"]:
        console.print("[red]No profile produced.[/red]")
        sys.exit(2)

    profile = state["profile"]
    out_path = out or (PROFILES_DIR / f"{profile.profile_id}.json")
    out_path.write_text(profile.model_dump_json(indent=2))
    with session_scope() as s:
        ProfileRepo().upsert(s, profile)
    console.print(
        f"[green]Wrote[/green] {out_path} + persisted to Postgres "
        f"([dim]{profile.profile_id}[/dim])\n"
        f"[dim]Next:[/dim] just plan {out_path}"
    )


@cli.group("profile")
def profile_group() -> None:
    """Inspect generated CompanyProfiles."""


@profile_group.command("show")
@click.option("--path", type=click.Path(exists=True, path_type=Path))
def profile_show(path: Path | None) -> None:
    """Pretty-print the most recent (or named) profile."""
    target = path or _latest_profile()
    if not target:
        console.print("[red]No profiles found.[/red] Run `just research <url>` first.")
        sys.exit(1)
    raw = json.loads(target.read_text())
    profile = CompanyProfile.model_validate(raw)

    console.print(Panel.fit(
        f"[bold]{profile.company.name}[/bold] · {profile.industry_taxonomy.primary_industry}\n"
        f"[dim]{profile.profile_id}[/dim]",
        border_style="green",
    ))

    if profile.channels:
        t = Table(title="Channels", show_header=True, header_style="bold")
        t.add_column("Type"); t.add_column("Name"); t.add_column("Citations")
        for ch in profile.channels:
            t.add_row(ch.channel_type, ch.name, ", ".join(ch.citations))
        console.print(t)

    if profile.tech_stack_signals:
        t = Table(title="Tech stack signals", show_header=True, header_style="bold")
        t.add_column("Component"); t.add_column("Vendor"); t.add_column("Conf"); t.add_column("Cit")
        for s in profile.tech_stack_signals:
            t.add_row(s.component_type, s.vendor_or_product, s.confidence.value,
                      ", ".join(s.citations))
        console.print(t)

    if profile.pain_signals:
        t = Table(title="Pain signals", show_header=True, header_style="bold")
        t.add_column("Severity"); t.add_column("Pain"); t.add_column("Cit")
        for p in profile.pain_signals:
            t.add_row(p.severity, p.pain[:80], ", ".join(p.citations))
        console.print(t)

    if profile.synthesized_flags:
        t = Table(title="Synthesized (unsourced) flags — review these",
                  show_header=True, header_style="bold yellow")
        t.add_column("Field path"); t.add_column("Claim"); t.add_column("Rationale")
        for f in profile.synthesized_flags:
            t.add_row(f.field_path, f.claim[:60], f.rationale[:80])
        console.print(t)

    console.print(f"\n[dim]Provenance: {len(profile.provenance)} sources cited.[/dim]")


@profile_group.command("validate")
def profile_validate() -> None:
    """Validate every profile in data/profiles/ against the schema."""
    files = sorted(PROFILES_DIR.glob("*.json"))
    if not files:
        console.print("[yellow]No profiles to validate.[/yellow]")
        return
    failures = 0
    for f in files:
        try:
            CompanyProfile.model_validate_json(f.read_text())
            console.print(f"[green]OK[/green]   {f.name}")
        except Exception as e:
            failures += 1
            console.print(f"[red]FAIL[/red] {f.name}: {e}")
    if failures:
        sys.exit(1)


@cli.group("db")
def db_group() -> None:
    """Database operations."""


@db_group.command("init")
def db_init() -> None:
    """Apply all pending migrations (idempotent)."""
    from proj_clarion.storage import apply_migrations

    applied = apply_migrations()
    if applied:
        for f in applied:
            console.print(f"[green]applied[/green] {f}")
    else:
        console.print("[dim]No new migrations to apply.[/dim]")


@db_group.command("reset")
@click.confirmation_option(
    prompt="This drops every Proj Clarion table. Continue?",
    help="Skip the confirmation prompt.",
)
def db_reset() -> None:
    """Drop every table and re-apply migrations. DEV ONLY."""
    from proj_clarion.storage import apply_migrations, drop_all

    drop_all()
    console.print("[yellow]dropped[/yellow] all tables")
    applied = apply_migrations()
    for f in applied:
        console.print(f"[green]applied[/green] {f}")


def _latest_profile() -> Path | None:
    if not PROFILES_DIR.exists():
        return None
    files = sorted(PROFILES_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    return files[0] if files else None


# ============================================================
# plan — Plan agent + SE review flow
# ============================================================

@cli.group("plan")
def plan_group() -> None:
    """Plan agent and SE review flow."""


@plan_group.command("run")
@click.argument("profile_path", type=click.Path(exists=True, path_type=Path))
@click.option("--out", type=click.Path(path_type=Path), help="Override output JSON path")
@click.option("--volume-per-day", type=int, default=None,
              help="Override DataBlueprint.business_event_volume_per_day. "
                   "Default auto-scales by channel count, capped at 5K. "
                   "Use 500 for a tiny smoke build, 25000+ to stress-test "
                   "the generator. Clamped to [100, 100000].")
def plan_run(profile_path: Path, out: Path | None, volume_per_day: int | None) -> None:
    """Generate a DemoPlan from a CompanyProfile JSON. Persists to DB and disk."""
    from proj_clarion.agents.planner import run_plan
    from proj_clarion.schemas import CompanyProfile, ReviewState
    from proj_clarion.storage import (
        AuditRepo,
        KGRepo,
        PlanRepo,
        ProfileRepo,
        session_scope,
    )

    PLANS_DIR.mkdir(parents=True, exist_ok=True)
    raw = json.loads(profile_path.read_text())
    profile = CompanyProfile.model_validate(raw)

    console.print(Panel.fit(
        f"[bold]Planning:[/bold] {profile.company.name} "
        f"([dim]{profile.profile_id}[/dim])"
        + (f"\n[dim]volume_per_day override = {volume_per_day:,}[/dim]"
           if volume_per_day is not None else ""),
        border_style="cyan",
    ))

    state = asyncio.run(run_plan(profile, volume_per_day=volume_per_day))

    if state.get("errors"):
        console.print("[yellow]Issues during planning:[/yellow]")
        for e in state["errors"]:
            console.print(f"  • {e}")

    plan = state.get("plan")
    if not plan:
        console.print("[red]No plan produced.[/red]")
        sys.exit(2)

    # Persist: ensure source profile is in DB, then upsert plan + KG, then audit
    with session_scope() as s:
        ProfileRepo().upsert(s, profile)
        PlanRepo().upsert(s, plan)
        KGRepo().replace(s, plan.plan_id, plan.knowledge_graph)
        AuditRepo().record(
            s, plan.plan_id, actor="planner",
            action="created", to_state=ReviewState.DRAFT.value,
            note=f"planned from {profile.profile_id}",
        )

    out_path = out or (PLANS_DIR / f"{plan.plan_id}.json")
    out_path.write_text(plan.model_dump_json(indent=2))

    console.print(f"[green]Wrote[/green] {out_path}")
    console.print(f"[green]Persisted[/green] plan_id={plan.plan_id} (review_state=draft)")


@plan_group.command("show")
@click.argument("plan_id")
def plan_show(plan_id: str) -> None:
    """One-screen tree view of a plan. Pass plan_id (UUID prefix accepted)."""
    from proj_clarion.storage import AuditRepo, PlanRepo, session_scope

    with session_scope() as s:
        full_plan_id = _resolve_plan_id(s, plan_id)
        if not full_plan_id:
            console.print(f"[red]No plan matches[/red] {plan_id!r}")
            sys.exit(1)
        plan = PlanRepo().get(s, full_plan_id)
        history = AuditRepo().history(s, full_plan_id)
    if not plan:
        console.print(f"[red]Plan {full_plan_id} not found[/red]")
        sys.exit(1)

    _render_plan(plan, history)


@plan_group.command("approve")
@click.argument("plan_id")
@click.option("--note", required=True, help="Why this plan is approved (audit log).")
@click.option("--actor", default=None, help="Who is approving. Defaults to $USER.")
def plan_approve(plan_id: str, note: str, actor: str | None) -> None:
    """Move a plan from draft → approved_for_provision. Requires a justification."""
    import os as _os

    from proj_clarion.schemas import ReviewState
    from proj_clarion.storage import AuditRepo, PlanRepo, session_scope

    with session_scope() as s:
        full_plan_id = _resolve_plan_id(s, plan_id)
        if not full_plan_id:
            console.print(f"[red]No plan matches[/red] {plan_id!r}")
            sys.exit(1)
        repo = PlanRepo()
        prev = repo.set_review_state(s, full_plan_id, ReviewState.APPROVED_FOR_PROVISION)
        if prev is None:
            console.print(f"[red]Plan {full_plan_id} not found[/red]")
            sys.exit(1)
        AuditRepo().record(
            s, full_plan_id,
            actor=actor or _os.getenv("USER", "unknown"),
            action="approved",
            from_state=prev,
            to_state=ReviewState.APPROVED_FOR_PROVISION.value,
            note=note,
        )

    console.print(
        f"[green]Approved[/green] {full_plan_id}  "
        f"({prev} → {ReviewState.APPROVED_FOR_PROVISION.value})"
    )


@plan_group.command("list")
@click.option("--limit", default=20, type=int)
def plan_list(limit: int) -> None:
    """List recent plans."""
    from proj_clarion.storage import PlanRepo, session_scope

    with session_scope() as s:
        rows = PlanRepo().list(s, limit=limit)

    if not rows:
        console.print("[dim]No plans yet.[/dim] Run `just plan <profile_path>`.")
        return

    t = Table(title="Plans", show_header=True, header_style="bold")
    t.add_column("plan_id"); t.add_column("updated_at"); t.add_column("source profile")
    t.add_column("review_state")
    for plan_id, ts, spid, state in rows:
        t.add_row(str(plan_id)[:13] + "…", ts.isoformat(timespec="seconds"), spid, state)
    console.print(t)


# ============================================================
# generate — synthetic data generator (v0.3)
# ============================================================

@cli.group("generate")
def generate_group() -> None:
    """Synthetic data generation from a DemoPlan."""


@generate_group.command("run")
@click.argument("plan_id")
@click.option("--days", type=int, default=None,
              help="Override DataBlueprint.historical_window_days for cheap dev runs.")
@click.option("--no-traces", is_flag=True, default=False,
              help="Skip OTel trace emission (faster; only writes Postgres rows).")
@click.option("--anchor-now", is_flag=True, default=False,
              help="Place the incident-script anchor at NOW (default: 30min before NOW).")
def generate_run(plan_id: str, days: int | None, no_traces: bool, anchor_now: bool) -> None:
    """Generate business events + traces for a plan."""
    from datetime import UTC, datetime, timedelta

    from proj_clarion.generator import (
        apply_incident_script,
        emit_traces_for_events,
        generate_events_for_plan,
    )
    from proj_clarion.generator.events import persist_events
    from proj_clarion.storage import PlanRepo, session_scope

    with session_scope() as s:
        full_plan_id = _resolve_plan_id(s, plan_id)
        if not full_plan_id:
            console.print(f"[red]No plan matches[/red] {plan_id!r}")
            sys.exit(1)
        plan = PlanRepo().get(s, full_plan_id)
    if plan is None:
        console.print(f"[red]Plan {full_plan_id} not found[/red]")
        sys.exit(1)

    end_at = datetime.now(UTC).replace(second=0, microsecond=0)
    incident_anchor = end_at if anchor_now else end_at - timedelta(minutes=30)

    console.print(Panel.fit(
        f"[bold]Generating[/bold] for plan {str(plan.plan_id)[:8]}\n"
        f"window:           {days or plan.data_blueprint.historical_window_days} days\n"
        f"event volume/day: {plan.data_blueprint.business_event_volume_per_day:,}\n"
        f"diurnal/weekly:   {plan.data_blueprint.diurnal_pattern} / "
        f"{plan.data_blueprint.weekly_pattern}\n"
        f"incident anchor:  {incident_anchor.isoformat(timespec='seconds')}\n"
        f"traces:           {'OFF' if no_traces else 'ON (sent to Cloud Tempo)'}",
        border_style="cyan",
    ))

    raw_events = generate_events_for_plan(plan, days=days, end_at=end_at)
    shaped = apply_incident_script(raw_events, plan.incident_script, anchor=incident_anchor)
    # Materialize once so we can both persist AND emit traces from the same stream
    materialized = list(shaped)
    console.print(f"[green]Built[/green] {len(materialized):,} events in memory")

    with session_scope() as s:
        # Re-runs are common in dev; clear prior events for this plan to avoid pile-up.
        from sqlalchemy import text as _text
        s.execute(_text("DELETE FROM business_events WHERE plan_id = :pid"),
                  {"pid": str(plan.plan_id)})
        rows = persist_events(s, str(plan.plan_id), iter(materialized))
    console.print(f"[green]Persisted[/green] {rows:,} business_events rows")

    if not no_traces:
        emitted = emit_traces_for_events(materialized, plan_id=str(plan.plan_id))
        console.print(f"[green]Emitted[/green] {emitted:,} traces to Cloud Tempo")

    console.print(
        f"[dim]Verify in Cloud Tempo: "
        f"{{ resource.service.name = \"proj-clarion\" && clarion.plan_id = \"{plan.plan_id}\" }}"
        f"[/dim]"
    )


@generate_group.command("clear")
@click.argument("plan_id")
@click.confirmation_option(prompt="Delete all generated events for this plan?")
def generate_clear(plan_id: str) -> None:
    """Delete every business_events row for a plan. DEV ONLY."""
    from sqlalchemy import text as _text

    from proj_clarion.storage import session_scope

    with session_scope() as s:
        full_plan_id = _resolve_plan_id(s, plan_id)
        if not full_plan_id:
            console.print(f"[red]No plan matches[/red] {plan_id!r}")
            sys.exit(1)
        result = s.execute(_text("DELETE FROM business_events WHERE plan_id = :pid"),
                           {"pid": full_plan_id})
    console.print(f"[yellow]Deleted[/yellow] {result.rowcount} rows for plan {full_plan_id}")


# ============================================================
# kg — publish a plan's KG (+ synthetic infra) to Grafana Cloud KG (v0.5)
# ============================================================

@cli.group("kg")
def kg_group() -> None:
    """Knowledge Graph publication: push entity model rules + emit entity gauges."""


@kg_group.command("preview")
@click.argument("plan_id")
@click.option("--out-dir", type=click.Path(path_type=Path),
              default=Path("data/generated"),
              help="Where to write the YAMLs for inspection (no push).")
def kg_preview(plan_id: str, out_dir: Path) -> None:
    """Generate model-rules + prom-rules YAMLs for a plan; write to disk."""
    from proj_clarion.kg_publish import build_model_rules, build_prom_rules
    from proj_clarion.kg_publish.expand import expand_with_synthetic_infra, expansion_summary
    from proj_clarion.storage import PlanRepo, session_scope

    with session_scope() as s:
        full_plan_id = _resolve_plan_id(s, plan_id)
        if not full_plan_id:
            console.print(f"[red]No plan matches[/red] {plan_id!r}")
            sys.exit(1)
        plan = PlanRepo().get(s, full_plan_id)
    if plan is None:
        console.print(f"[red]Plan {full_plan_id} not found[/red]")
        sys.exit(1)

    expanded = expand_with_synthetic_infra(plan)
    summary = expansion_summary(plan.knowledge_graph, expanded)

    out_dir = out_dir / str(plan.plan_id) / "kg"
    out_dir.mkdir(parents=True, exist_ok=True)

    model_yaml = build_model_rules(plan, kg=expanded)
    prom_yaml = build_prom_rules(plan)
    (out_dir / "model-rules.yaml").write_text(model_yaml)
    (out_dir / "prom-rules.yaml").write_text(prom_yaml)

    by_kind = summary.get("by_kind", {})
    interesting = ("store", "cluster", "kubecluster", "kubenode", "pod", "vm",
                   "loadbalancer", "database", "topic")
    breakdown = "  ".join(
        f"{kind}={by_kind[kind]}" for kind in interesting if kind in by_kind
    )
    console.print(Panel.fit(
        f"[bold]KG preview[/bold] for plan {str(plan.plan_id)[:8]}\n"
        f"original:   {summary['original_nodes']} nodes\n"
        f"+ synth:    {summary['synth_nodes_added']} nodes, "
        f"{summary['synth_edges_added']} edges\n"
        f"expanded:   {summary['expanded_nodes']} nodes total, "
        f"{len(expanded.edges)} edges\n"
        f"breakdown:  {breakdown}\n"
        f"out_dir:    {out_dir}",
        border_style="cyan",
    ))
    console.print(
        f"\n[dim]Next:  just kg-publish {plan_id}  (pushes both YAMLs via gcx + "
        f"starts the entity emitter)[/dim]"
    )


def _sweep_orphan_clarion_prom_rules(*, out_dir: Path, current_plan_id_prefix: str) -> None:
    """List Cloud-side prom-rules; for each clarion-entity-recording-rules-*
    that ISN'T this plan's, overwrite with a tombstone (renamed record so
    the `clarion_entity_info` claim is freed). Without this, every plan
    after the first hits HTTP 422 'duplicate of an existing rule'.

    Failures here are non-fatal — if `gcx kg rules list` itself fails (auth,
    network), we just skip and let the real push surface the error. We
    deliberately don't recurse into any per-rule diagnostics; the goal is
    only to clear OUR plan's path before pushing.
    """
    import json
    import subprocess

    # `gcx kg rules list` doesn't ship a clean parseable mode (its `-o json`
    # output is `null`), but `-vvv --log-http-payload` puts the raw HTTP
    # response body on stderr — which contains a JSON object with the
    # ruleNames array. We grep for the line that starts with `{` and has
    # `"ruleNames":`. Brittle-ish but stable across gcx 0.2.x.
    list_result = subprocess.run(
        ["gcx", "kg", "rules", "list", "-vvv", "--log-http-payload"],
        capture_output=True, text=True, check=False,
    )
    if list_result.returncode != 0:
        console.print(
            "[yellow]Couldn't list existing prom-rules to sweep orphans; "
            "continuing anyway[/yellow]"
        )
        return

    rule_names: list[str] = []
    for line in (list_result.stderr + "\n" + list_result.stdout).splitlines():
        line = line.strip()
        if line.startswith("{") and '"ruleNames"' in line:
            try:
                payload = json.loads(line)
                rule_names = payload.get("ruleNames", [])
                break
            except json.JSONDecodeError:
                continue

    current_name = f"clarion-entity-recording-rules-{current_plan_id_prefix}"
    orphans = [
        n for n in rule_names
        if n.startswith("clarion-entity-recording-rules-") and n != current_name
    ]
    if not orphans:
        return

    console.print(
        f"[cyan]Sweeping[/cyan] {len(orphans)} orphan clarion prom-rule(s) "
        f"so the new push can claim `clarion_entity_info`: {orphans}"
    )
    tomb_dir = out_dir / "tombstones"
    tomb_dir.mkdir(parents=True, exist_ok=True)
    for name in orphans:
        # Replace the file's content with a single no-op recording rule
        # that uses a unique record name. Keeps the file (we can't delete
        # individual rule files via gcx) but releases the global claim on
        # `clarion_entity_info`.
        safe = name.replace("-", "_")
        body = (
            f"name: {name}\n"
            f"groups:\n"
            f"- name: clarion.entity.presence.tombstone-{name}\n"
            f"  interval: 5m\n"
            f"  rules:\n"
            f"  - record: clarion_entity_info_tombstone_{safe}\n"
            f"    expr: vector(0)\n"
        )
        tomb_path = tomb_dir / f"{name}.yaml"
        tomb_path.write_text(body)
        result = subprocess.run(
            ["gcx", "kg", "rules", "create", "-f", str(tomb_path)],
            capture_output=True, text=True, check=False,
        )
        if result.returncode != 0:
            console.print(
                f"[yellow]Tombstone push failed for {name}; "
                f"continuing anyway. Stdout: {result.stdout[:200]}[/yellow]"
            )
        else:
            console.print(f"  [dim]tombstoned[/dim] {name}")


def _run_doctor_after_emit_start(plan_id: str, customer: str | None, wait_seconds: int) -> None:
    """Sleep, then call the doctor in a worker thread. Prints the report
    to stderr so it doesn't interleave with the emitter's structlog
    output going to stdout. Best-effort; doesn't kill the emitter on
    failure, but flags loudly so the user knows."""
    import threading
    import time as _time

    from proj_clarion.kg_publish.doctor import run_doctor

    def _go() -> None:
        _time.sleep(wait_seconds)
        report = run_doctor(plan_id=plan_id, customer=customer)
        # Use a separate console targeting stderr to keep the output
        # visually distinct from the emitter's normal log lines.
        from rich.console import Console as _C
        from rich.table import Table as _T
        ec = _C(stderr=True)
        ec.print()
        ec.print("─── KG health check (auto-run after emit start) ───",
                 style="bold cyan")
        t = _T(show_header=True, header_style="bold")
        t.add_column("check"); t.add_column("status"); t.add_column("detail")
        for c in report.checks:
            tone = {"pass": "green", "fail": "red", "warn": "yellow", "skip": "dim"}[c.status]
            t.add_row(c.name, f"[{tone}]{c.status.upper()}[/{tone}]", c.detail[:80])
        ec.print(t)
        ec.print(f"[bold]{report.summary}[/bold]")
        if not report.passed:
            ec.print(
                "[bold red]Doctor FAILED. The emitter is still running so you "
                "can investigate, but DON'T trust this demo until checks pass. "
                "Run `just kg-doctor` for full output (including fix hints).[/bold red]"
            )

    threading.Thread(target=_go, daemon=True).start()


@kg_group.command("publish")
@click.argument("plan_id")
@click.option("--push-rules/--no-push-rules", default=True,
              help="Push the model-rules + prom-rules to Cloud (default: yes).")
@click.option("--emit/--no-emit", default=True,
              help="Start the entity gauge emitter (default: yes; runs until ctrl-C).")
@click.option("--customer", default=None,
              help="Customer slug to tag every entity with (clarion_customer label). "
                   "Defaults to the source profile slug (e.g. 'acme_retail').")
@click.option("--env", default=None,
              help="asserts_env value to tag entities with. Default: the "
                   "customer slug, so the Asserts entity-graph env filter "
                   "naturally separates demos (env=acme_retail, env=globex_mfg, "
                   "etc.). Also pins deployment.environment to the same "
                   "value so target_info and observation-level metrics "
                   "stay in the same env scope. Pass an explicit value for "
                   "multi-environment demos (--env staging-acme_retail, etc.).")
@click.option("--site", default="demo", help="asserts_site value to tag entities with.")
@click.option("--doctor/--no-doctor", default=True,
              help="Auto-run the KG health check ~75s after starting the emitter "
                   "(one full export cycle + Mimir ingest). Logs failures loudly. "
                   "Use --no-doctor for fast iteration.")
@click.option("--max-entities", type=int, default=None,
              help="Cap the number of entities the emitter materialises in "
                   "Asserts. Tier-priority trim: business entities + clusters + "
                   "nodes are kept; pods are cut first. Useful when a KG with "
                   "100+ pods crowds the entity-graph view for a live demo. "
                   "Defaults to no cap (or CLARION_MAX_ENTITIES env var).")
def kg_publish(plan_id: str, push_rules: bool, emit: bool,
               customer: str | None, env: str | None, site: str, doctor: bool,
               max_entities: int | None) -> None:
    """Push KG model-rules + prom-rules to Cloud; emit entity gauges."""
    import subprocess

    from proj_clarion.kg_publish import (
        EntityEmitter, build_model_rules, build_prom_rules,
    )
    from proj_clarion.kg_publish.expand import expand_with_synthetic_infra
    from proj_clarion.storage import PlanRepo, session_scope

    with session_scope() as s:
        full_plan_id = _resolve_plan_id(s, plan_id)
        if not full_plan_id:
            console.print(f"[red]No plan matches[/red] {plan_id!r}")
            sys.exit(1)
        plan = PlanRepo().get(s, full_plan_id)
    if plan is None:
        console.print(f"[red]Plan {full_plan_id} not found[/red]")
        sys.exit(1)

    expanded = expand_with_synthetic_infra(plan)

    # Always write to disk too, so the user has the artifacts to inspect.
    out_dir = Path("data/generated") / str(plan.plan_id) / "kg"
    out_dir.mkdir(parents=True, exist_ok=True)
    # Pass `customer` so the model-rule file name carries the customer
    # slug (e.g. clarion-business-model-initech_industrial-5ac44b56). When
    # `--customer` was omitted on the CLI, build_model_rules derives the
    # same slug from plan.source_profile_id that EntityEmitter uses, so
    # the file name and the emitted clarion_customer label stay aligned.
    model_yaml = build_model_rules(plan, kg=expanded, customer=customer)
    prom_yaml = build_prom_rules(plan)
    (out_dir / "model-rules.yaml").write_text(model_yaml)
    (out_dir / "prom-rules.yaml").write_text(prom_yaml)
    console.print(f"[green]Wrote[/green] {out_dir}/model-rules.yaml, prom-rules.yaml")

    if push_rules:
        # Sweep orphan clarion-entity-recording-rules-* files BEFORE pushing
        # the new prom-rules. Asserts dedups recording-rule record names
        # globally across all rule files in the tenant. Each plan's
        # prom-rules file claims `clarion_entity_info`; orphans from prior
        # plans hold the same claim and the new push 422s with "duplicate
        # of an existing rule in custom rule file '...'". Tombstoning each
        # orphan (overwriting with a no-op renamed-record body) frees up
        # the claim. Idempotent: if there are no orphans, this is a no-op.
        _sweep_orphan_clarion_prom_rules(
            out_dir=out_dir,
            current_plan_id_prefix=str(plan.plan_id)[:8],
        )

        from proj_clarion.observability.tools import track_tool_call
        for fname, gcx_cmd, tool_name in (
            ("model-rules.yaml", ["gcx", "kg", "model-rules", "create", "-f"], "kg_model_rules_push"),
            ("prom-rules.yaml",  ["gcx", "kg", "rules",       "create", "-f"], "kg_prom_rules_push"),
        ):
            file_path = out_dir / fname
            console.print(f"[cyan]Pushing[/cyan] {fname} via gcx…")
            with track_tool_call(
                agent_name="kg_publish_agent",
                tool_name=tool_name,
                provider_name="grafana_cloud",
                target_system="gcx.kg",
                action=" ".join(gcx_cmd[1:]),
                input_summary=fname,
            ) as _tool:
                result = subprocess.run(
                    [*gcx_cmd, str(file_path)],
                    capture_output=True, check=False,
                )
                if result.returncode != 0:
                    _tool["output"] = f"failed exit={result.returncode}"
                    console.print(
                        f"[red]Push failed[/red] for {fname}\n"
                        f"  stdout: {result.stdout.decode(errors='replace')[:400]}\n"
                        f"  stderr: {result.stderr.decode(errors='replace')[:400]}"
                    )
                    sys.exit(1)
                _tool["output"] = "ok"
            console.print(f"[green]Pushed[/green] {fname}")

    if not emit:
        console.print("[dim]Skipped entity emission. Re-run with --emit to start it.[/dim]")
        return

    # Pre-resolve effective env so the panel + emitter agree.
    # Mirrors EntityEmitter's default: `env or customer_slug`. Keeps
    # asserts.env and deployment.environment aligned to one customer
    # scope so the entity-graph env filter separates demos cleanly.
    effective_customer = (
        customer
        or (plan.source_profile_id or "").removeprefix("prof-").strip("-").lower()
        or "clarion"
    )
    effective_env = env or effective_customer
    console.print(Panel.fit(
        f"[bold]Starting entity emitter[/bold]\n"
        f"entities: {len(expanded.nodes)}\n"
        f"customer={effective_customer}\n"
        f"asserts_env={effective_env}  asserts_site={site}\n"
        f"emits clarion_entity_info every 30s; ctrl-C to stop.",
        border_style="cyan",
    ))
    emitter = EntityEmitter(
        plan, expanded,
        customer=customer, env=env, site=site,
        max_entities=max_entities,
    )
    from proj_clarion.observability.tools import track_tool_call
    with track_tool_call(
        agent_name="kg_publish_agent",
        tool_name="kg_entity_emitter_start",
        provider_name="grafana_cloud",
        target_system="otlp.metrics",
        action="start",
        input_summary=f"entities={len(expanded.nodes)}",
    ) as _tool:
        emitter.start()
        _tool["output"] = "started"

    # Schedule the post-start health check. 75s gives the periodic
    # MetricReader's first export (30s default) plus headroom for
    # Mimir + Asserts to ingest, so the doctor sees fresh series.
    if doctor:
        _run_doctor_after_emit_start(
            plan_id=str(plan.plan_id),
            customer=customer,
            wait_seconds=75,
        )

    try:
        emitter.run_forever()
    finally:
        emitter.stop()


@kg_group.command("doctor")
@click.argument("plan_id", required=False)
@click.option("--customer", default=None,
              help="Customer slug (defaults to derived from plan's profile_id).")
@click.option("--exit-nonzero-on-fail/--no-exit-nonzero-on-fail", default=True,
              help="Exit with code 1 if any check FAILs (good for CI / pipeline gating).")
def kg_doctor(plan_id: str | None, customer: str | None, exit_nonzero_on_fail: bool) -> None:
    """Validate that the KG agent's emissions match the model's invariants.

    Catches the regressions we've actually shipped: doubled asserts_env scope,
    Pods missing the `node` label, Stores not fanning out across services,
    Service→Database affinity not flowing, KubeCluster entities not
    materializing, and KG-vs-Mimir entity-count mismatches.

    Exits non-zero on FAIL so it can gate kg-publish or a CI job.
    """
    from proj_clarion.kg_publish.doctor import run_doctor

    if plan_id:
        with session_scope() as s:
            full_plan_id = _resolve_plan_id(s, plan_id)
            if not full_plan_id:
                console.print(f"[red]No plan matches[/red] {plan_id!r}")
                sys.exit(1)
        plan_id = full_plan_id

    report = run_doctor(plan_id=plan_id, customer=customer)

    t = Table(title="KG health check", show_header=True, header_style="bold")
    t.add_column("check"); t.add_column("status"); t.add_column("detail"); t.add_column("fix")
    for c in report.checks:
        tone = {
            "pass": "green", "fail": "red", "warn": "yellow", "skip": "dim",
        }[c.status]
        t.add_row(
            c.name,
            f"[{tone}]{c.status.upper()}[/{tone}]",
            c.detail,
            c.fix or "—",
        )
    console.print(t)
    console.print(f"\n[bold]{report.summary}[/bold]")
    if report.plan_id:
        console.print(f"[dim]plan: {report.plan_id[:8]}…  customer: {report.customer}[/dim]")

    if not report.passed and exit_nonzero_on_fail:
        sys.exit(1)


@kg_group.command("verify")
@click.argument("plan_id")
@click.option("--type", "entity_type", default=None,
              help="Restrict to a single entity type (Region/Channel/Store/...)")
def kg_verify(plan_id: str, entity_type: str | None) -> None:
    """Query Grafana KG for the plan's entities; report what landed."""
    import json as _json
    import subprocess

    from proj_clarion.storage import session_scope

    with session_scope() as s:
        full_plan_id = _resolve_plan_id(s, plan_id)
        if not full_plan_id:
            console.print(f"[red]No plan matches[/red] {plan_id!r}")
            sys.exit(1)

    types_to_check = [entity_type] if entity_type else [
        "Region", "Channel", "Store", "FulfillmentCenter",
        "ClarionPod", "VM",
    ]
    t = Table(title="KG entities", show_header=True, header_style="bold")
    t.add_column("type"); t.add_column("count"); t.add_column("sample names")
    for et in types_to_check:
        result = subprocess.run(
            ["gcx", "kg", "entities", "list", "--type", et, "-o", "json", "--agent"],
            capture_output=True, check=False,
        )
        if result.returncode != 0:
            t.add_row(et, "?", "(query failed)")
            continue
        try:
            body = _json.loads(result.stdout.decode(errors="replace"))
            items = body.get("items", body) if isinstance(body, dict) else body
        except Exception:
            t.add_row(et, "?", "(unparseable)")
            continue
        if isinstance(items, dict) and "error" in items:
            t.add_row(et, "?", "(none / not found)")
            continue
        names = [str(x.get("name", x.get("entity", x))) for x in items[:3]] if items else []
        t.add_row(et, str(len(items) if isinstance(items, list) else 0),
                  ", ".join(names) or "(none yet)")
    console.print(t)
    console.print(
        f"\n[dim]Open the KG app:  gcx kg open  "
        f"(entities take ~2 min to materialise after first emission)[/dim]"
    )


# ============================================================
# provision — push DashboardSpec/AlertSpec to Grafana Cloud (v0.4)
# ============================================================

@cli.group("provision")
def provision_group() -> None:
    """Provision dashboards and alerts to Grafana Cloud."""


@provision_group.command("run")
@click.argument("plan_id")
@click.option("--push", is_flag=True, default=False,
              help="Actually create resources in Grafana Cloud. Default is dry-run "
                   "(writes JSON under data/generated/<plan_id>/).")
@click.option("--out-dir", type=click.Path(path_type=Path),
              default=Path("data/generated"),
              help="Directory to write dashboard/alert JSON for dry-run inspection.")
@click.option("--customer", default=None,
              help="Customer slug for the folder title (e.g. 'acme_retail'). "
                   "Defaults to the source profile slug — should match what "
                   "kg-publish uses so the folder + KG entity rows align.")
@click.option("--sweep-orphans/--no-sweep-orphans", default=True,
              help="Delete clarion-* folders in Cloud whose plan_id no longer "
                   "exists in Postgres before pushing. Default on; turn off "
                   "with --no-sweep-orphans for read-only inspection runs.")
@click.option(
    "--dashboard-style",
    type=click.Choice(["command-center", "legacy"]),
    default="command-center",
    help="Layout to use. `command-center` is one dense web-app-style "
         "dashboard per plan (vertical-aware). `legacy` produces N small "
         "dashboards from each plan.dashboard_spec — kept for back-compat.",
)
def provision_run(
    plan_id: str, push: bool, out_dir: Path,
    customer: str | None, sweep_orphans: bool, dashboard_style: str,
) -> None:
    """Build dashboards + alert rules for a plan. Default: dry-run to disk."""
    from proj_clarion.provision import build_assets, push_assets
    from proj_clarion.provision.assembler import save_assets_to_disk
    from proj_clarion.provision.client import GrafanaAuthError, GrafanaClient
    from proj_clarion.storage import PlanRepo, session_scope

    with session_scope() as s:
        full_plan_id = _resolve_plan_id(s, plan_id)
        if not full_plan_id:
            console.print(f"[red]No plan matches[/red] {plan_id!r}")
            sys.exit(1)
        plan = PlanRepo().get(s, full_plan_id)
        # Snapshot all known plan_ids for the orphan sweep — anything in
        # Cloud whose UID doesn't map to one of these gets deleted.
        known_plan_ids: set[str] = (
            {str(pid) for pid, *_ in PlanRepo().list(s, limit=500)}
            if push and sweep_orphans else set()
        )
    if plan is None:
        console.print(f"[red]Plan {full_plan_id} not found[/red]")
        sys.exit(1)

    n_dashboards = (
        1 if dashboard_style == "command-center" else len(plan.dashboard_specs)
    )
    console.print(Panel.fit(
        f"[bold]Provisioning[/bold] for plan {str(plan.plan_id)[:8]}\n"
        f"customer:   {customer or '(derived from profile_id)'}\n"
        f"style:      {dashboard_style}\n"
        f"dashboards: {n_dashboards}\n"
        f"alerts:     {len(plan.alert_specs)}\n"
        f"mode:       {'PUSH to Cloud' if push else 'DRY-RUN to disk'}\n"
        f"sweep:      {'on' if (push and sweep_orphans) else 'off'}",
        border_style="cyan" if not push else "yellow",
    ))

    assets = build_assets(plan, customer=customer, dashboard_style=dashboard_style)

    written = save_assets_to_disk(assets, out_dir)
    console.print(f"[green]Wrote[/green] dashboards + alert JSON to {written}")

    if not push:
        console.print(
            "\n[dim]This was a dry run. Inspect the JSON above, then re-run with "
            "--push to send to Grafana Cloud.[/dim]"
        )
        return

    console.print("[cyan]Pushing[/cyan] via gcx (OAuth-authenticated to your tenant)…")
    try:
        with GrafanaClient() as client:
            counts = push_assets(
                client, assets,
                sweep_orphans_against=known_plan_ids if sweep_orphans else None,
            )
    except GrafanaAuthError as exc:
        console.print(f"[red]Auth error:[/red] {exc}")
        console.print(
            "\n[yellow]Likely cause:[/yellow] gcx OAuth session expired. "
            "Run `gcx login` and retry."
        )
        sys.exit(1)

    swept = counts.get("orphans_deleted", 0)
    pruned = counts.get("dashboards_pruned", 0)
    console.print(
        f"[green]Pushed[/green] folder={counts['folders']}  "
        f"dashboards={counts['dashboards']}  "
        f"alert_rules={counts['alert_rules']}"
        + (f"  [dim]pruned {pruned} stale dashboard(s)[/dim]" if pruned else "")
        + (f"  [dim]swept {swept} orphan folder(s)[/dim]" if swept else "")
        + (f"  [yellow]alerts_failed={counts['alerts_failed']}[/yellow]"
           if counts["alerts_failed"] else "")
    )
    console.print(f"[dim]Folder UID: {assets.folder_uid}[/dim]")


@provision_group.command("refine")
@click.argument("plan_id")
@click.option("--no-audit", is_flag=True, default=False,
              help="Print suggestions to stdout but skip writing them to the plan's audit log.")
def provision_refine(plan_id: str, no_audit: bool) -> None:
    """Ask Grafana Assistant for vertical-specific dashboard suggestions.

    Loads the plan, derives its business_model + customer slug, calls
    `gcx assistant prompt` with a structured prompt, parses GS's JSON
    reply, and (by default) appends an audit entry with the suggestions
    so they show up on the plan's detail page next to the existing
    cloud.provisioned / cloud.kg_published entries.

    Best-effort: a GS usage-limit error or unparseable reply doesn't
    return non-zero — the failure is logged + recorded in audit so the
    SE knows what happened. Idempotent: safe to re-run after a successful
    provision push, even multiple times.
    """
    import os
    from proj_clarion.provision.gs_refine import format_audit_note, refine_dashboard
    from proj_clarion.storage import AuditRepo, PlanRepo, ProfileRepo, session_scope

    with session_scope() as s:
        full_plan_id = _resolve_plan_id(s, plan_id)
        if not full_plan_id:
            console.print(f"[red]No plan matches[/red] {plan_id!r}")
            sys.exit(1)
        plan = PlanRepo().get(s, full_plan_id)
        profile = ProfileRepo().get(s, plan.source_profile_id) if plan else None
    if plan is None or profile is None:
        console.print(f"[red]Plan or its source profile not found[/red]")
        sys.exit(1)

    business_model = profile.industry_taxonomy.business_model.value
    customer = profile.profile_id.removeprefix("prof-").lower() or "clarion"

    console.print(Panel.fit(
        f"[bold]Asking Grafana Assistant[/bold] for dashboard suggestions\n"
        f"plan:           {str(plan.plan_id)[:8]}\n"
        f"customer:       {customer}\n"
        f"business_model: {business_model}",
        border_style="cyan",
    ))

    result = refine_dashboard(plan, customer=customer, business_model=business_model)

    note = format_audit_note(result)
    console.print()
    console.print(note)

    if not no_audit:
        actor = os.environ.get("USER", "cli")
        with session_scope() as s:
            AuditRepo().record(
                s, str(plan.plan_id),
                actor=actor, action="cloud.dashboard_refined",
                note=note,
            )
        console.print(f"\n[dim]Recorded as audit entry on plan {str(plan.plan_id)[:8]}.[/dim]")

    if result.error and not result.suggestions:
        # Soft failure — exit non-zero so a CI invocation can spot it,
        # but the audit entry is still there for human review.
        sys.exit(2)


@provision_group.command("clear")
@click.argument("plan_id")
@click.confirmation_option(
    prompt="This will delete the plan's folder + every dashboard and alert in it from Cloud. Continue?"
)
def provision_clear(plan_id: str) -> None:
    """Delete a plan's folder + its dashboards/alerts from Grafana Cloud.
    The plan must still exist in the DB so we can derive the folder UID;
    use `provision clear-folder <uid>` for orphaned post-delete cleanup."""
    from proj_clarion.provision.client import GrafanaAuthError, GrafanaClient
    from proj_clarion.provision.folders import delete_folder_by_uid, folder_uid_for_plan

    with session_scope() as s:
        full_plan_id = _resolve_plan_id(s, plan_id)
        if not full_plan_id:
            console.print(f"[red]No plan matches[/red] {plan_id!r}")
            sys.exit(1)

    folder_uid = folder_uid_for_plan(full_plan_id)
    try:
        with GrafanaClient() as client:
            delete_folder_by_uid(client, folder_uid)
    except GrafanaAuthError as exc:
        console.print(f"[red]Auth error:[/red] {exc}")
        sys.exit(1)
    console.print(f"[yellow]Deleted[/yellow] folder {folder_uid}")


@provision_group.command("clear-folder")
@click.argument("folder_uid")
@click.option("--yes", is_flag=True, default=False,
              help="Skip the interactive confirmation.")
def provision_clear_folder(folder_uid: str, yes: bool) -> None:
    """Delete a Grafana folder by UID. For orphan cleanup after the plan
    has already been removed from the DB — `provision clear <plan_id>`
    can't help once the plan is gone."""
    from proj_clarion.provision.client import GrafanaAuthError, GrafanaClient
    from proj_clarion.provision.folders import delete_folder_by_uid

    if not yes:
        click.confirm(
            f"Delete folder {folder_uid} (cascades to dashboards + alerts)?",
            abort=True,
        )
    try:
        with GrafanaClient() as client:
            delete_folder_by_uid(client, folder_uid)
    except GrafanaAuthError as exc:
        console.print(f"[red]Auth error:[/red] {exc}")
        sys.exit(1)
    console.print(f"[yellow]Deleted[/yellow] folder {folder_uid}")


@provision_group.command("list-orphans")
def provision_list_orphans() -> None:
    """List `clarion-*` folders in Cloud whose plan is missing from the DB.

    Run after a plan delete that didn't include `cleanup_cloud=true`,
    or to spot folders left behind by some manual provisioning path."""
    from proj_clarion.provision.client import GrafanaAuthError, GrafanaClient
    from proj_clarion.provision.folders import find_orphan_folders
    from proj_clarion.storage import session_scope as _session_scope

    with _session_scope() as s:
        from sqlalchemy import text as _text
        rows = s.execute(_text("SELECT plan_id::text FROM demo_plans")).fetchall()
    known = {r[0] for r in rows}

    try:
        with GrafanaClient() as client:
            orphans = find_orphan_folders(client, known)
    except GrafanaAuthError as exc:
        console.print(f"[red]Auth error:[/red] {exc}")
        sys.exit(1)

    if not orphans:
        console.print("[green]No orphan folders found.[/green]")
        return

    t = Table(title="Orphan clarion-* folders", show_header=True, header_style="bold")
    t.add_column("folder uid"); t.add_column("title"); t.add_column("plan_id"); t.add_column("reason")
    for o in orphans:
        t.add_row(
            o.get("uid","-"),
            o.get("title","-"),
            o.get("plan_id") or "(unparseable)",
            o.get("reason","-"),
        )
    console.print(t)
    console.print(
        f"\n[dim]To delete: `proj-clarion provision clear-folder <uid>` "
        f"(or use the Dashboard UI's Orphan cleanup section).[/dim]"
    )


# ============================================================
# check — environment / connectivity diagnostics (v0.5)
# ============================================================

@cli.group("check")
def check_group() -> None:
    """Diagnostics for the local→Cloud telemetry path."""


@check_group.command("env")
@click.option("--alloy-ui", default="http://localhost:12345",
              help="Alloy admin UI base URL (Mode A only).")
@click.option("--timeout", default=3.0, type=float,
              help="Probe timeout in seconds.")
@click.option("--probe", is_flag=True, default=False,
              help="Send a real OTLP log record and report ingest result. "
                   "Verifies auth + ingest in one shot; doesn't catch every "
                   "schema issue, but catches the common silent-misconfig case.")
def check_env(alloy_ui: str, timeout: float, probe: bool) -> None:
    """Verify the OTLP endpoint is reachable; report Mode A vs Mode B.

    Closes the v0.5 carryover gap: nothing else stops the user from setting
    OTEL_EXPORTER_OTLP_ENDPOINT=localhost:4318 with no Alloy running.
    Telemetry would silently hang in the SDK retry queue. This subcommand
    surfaces that misconfig before a long demo run blackholes data.
    """
    import socket
    from urllib.error import URLError
    from urllib.parse import urlparse
    from urllib.request import Request, urlopen

    from proj_clarion.observability.otlp import (
        clarion_env, clarion_site, otlp_endpoint, using_alloy_hop,
    )

    base = otlp_endpoint()
    mode = "A (Alloy hop)" if using_alloy_hop() else (
        "B (direct to Cloud)" if base else "(no OTLP configured)"
    )

    t = Table(show_header=True, header_style="bold")
    t.add_column("check"); t.add_column("status"); t.add_column("detail")

    t.add_row("OTEL endpoint", "[cyan]" + (base or "(unset)") + "[/cyan]", mode)
    t.add_row("CLARION_ASSERTS_ENV",  clarion_env(),  "")
    t.add_row("CLARION_ASSERTS_SITE", clarion_site(), "")

    overall_ok = True

    if not base:
        t.add_row("OTLP probe", "[red]skip[/red]",
                  "Set OTEL_EXPORTER_OTLP_ENDPOINT to enable.")
        console.print(t)
        sys.exit(1)

    # TCP connect to the OTLP receiver. For Cloud (Mode B) this is a TLS port.
    parsed = urlparse(base)
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    host = parsed.hostname or ""
    try:
        with socket.create_connection((host, port), timeout=timeout):
            t.add_row("TCP connect", "[green]ok[/green]", f"{host}:{port}")
    except (OSError, socket.timeout) as exc:
        t.add_row("TCP connect", "[red]fail[/red]", f"{host}:{port}  {exc}")
        overall_ok = False

    if using_alloy_hop():
        # Probe Alloy's `/-/ready` to confirm the agent is actually up. A
        # bare TCP connect to :4318 can succeed even when Alloy itself is
        # not running (e.g. nothing's bound but the port lives in some
        # NAT/state). The /-/ready probe disambiguates.
        ready_url = alloy_ui.rstrip("/") + "/-/ready"
        try:
            req = Request(ready_url, method="GET")
            with urlopen(req, timeout=timeout) as resp:
                if resp.status == 200:
                    t.add_row("Alloy /-/ready", "[green]ok[/green]", ready_url)
                else:
                    t.add_row("Alloy /-/ready", "[yellow]warn[/yellow]",
                              f"{ready_url} → HTTP {resp.status}")
                    overall_ok = False
        except URLError as exc:
            t.add_row("Alloy /-/ready", "[red]fail[/red]",
                      f"{ready_url}  {exc.reason}")
            overall_ok = False

        # Soft-warn if GRAFANA_CLOUD_OTLP_AUTH isn't set (Alloy can't forward)
        if not os.environ.get("GRAFANA_CLOUD_OTLP_AUTH"):
            t.add_row(
                "GRAFANA_CLOUD_OTLP_AUTH", "[yellow]warn[/yellow]",
                "unset — Alloy will receive but can't forward to Cloud",
            )
            overall_ok = False
    else:
        # Mode B — must have OTEL_EXPORTER_OTLP_HEADERS for Cloud to authenticate
        if not os.environ.get("OTEL_EXPORTER_OTLP_HEADERS"):
            t.add_row(
                "OTEL_EXPORTER_OTLP_HEADERS", "[yellow]warn[/yellow]",
                "unset — Cloud will reject unauthenticated OTLP",
            )
            overall_ok = False

    # --probe sends a real OTLP log record. This catches misconfig that
    # the TCP / /-/ready checks above can't see: bad auth header, wrong
    # tenant, expired access policy, OTLP endpoint that accepts the
    # connection but rejects ingest. One throwaway record is cheap.
    if probe:
        ok, detail = _probe_otlp_log(timeout=timeout)
        t.add_row("OTLP probe", "[green]ok[/green]" if ok else "[red]fail[/red]", detail)
        if not ok:
            overall_ok = False

    console.print(t)
    sys.exit(0 if overall_ok else 1)


def _probe_otlp_log(*, timeout: float) -> tuple[bool, str]:
    """Send one OTLP log record to the configured endpoint and report result.

    Uses BatchLogRecordProcessor + force_flush so we get a synchronous
    success/failure verdict instead of fire-and-forget. On success, the
    record is tagged `clarion.probe=true` so an SE can grep for it in
    Cloud Loki to verify it actually landed (not just that the server
    accepted the bytes — Loki rate-limits at ingest, not at OTLP gateway).
    """
    import time as _time

    from opentelemetry._logs import LogRecord, SeverityNumber
    from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
    from opentelemetry.sdk._logs import LoggerProvider
    from opentelemetry.sdk._logs.export import BatchLogRecordProcessor

    from proj_clarion.observability.otlp import (
        clarion_resource, otlp_logs_endpoint,
    )

    endpoint = otlp_logs_endpoint()
    if not endpoint:
        return False, "OTEL_EXPORTER_OTLP_ENDPOINT unset"

    resource = clarion_resource(service_name="proj-clarion-check-env")

    # Wrap the exporter so we can capture the export result code without
    # poking at private SDK internals. force_flush returns True on success
    # but doesn't expose HTTP error detail; this captures it.
    captured: dict[str, object] = {"result": None, "error": None}

    class _CapturingExporter:
        def __init__(self, inner: OTLPLogExporter) -> None:
            self._inner = inner

        def export(self, batch):  # noqa: ANN001 — SDK signature
            try:
                result = self._inner.export(batch)
                captured["result"] = result
                return result
            except Exception as exc:  # noqa: BLE001
                captured["error"] = repr(exc)
                # Return the same enum the inner exporter would have returned;
                # we fish it out via the captured-result comparison below.
                return None

        def shutdown(self) -> None:
            self._inner.shutdown()

        def force_flush(self, timeout_millis: int = 30_000) -> bool:
            return self._inner.force_flush(timeout_millis)

    exporter = _CapturingExporter(OTLPLogExporter(endpoint=endpoint, timeout=int(timeout)))
    provider = LoggerProvider(resource=resource)
    processor = BatchLogRecordProcessor(exporter, max_export_batch_size=1)
    provider.add_log_record_processor(processor)
    logger = provider.get_logger("proj-clarion.check_env")

    record = LogRecord(
        timestamp=_time.time_ns(),
        observed_timestamp=_time.time_ns(),
        severity_text="INFO",
        severity_number=SeverityNumber.INFO,
        body="proj-clarion check env probe",
        attributes={
            "clarion.probe":  True,
            "clarion.source": "check_env",
        },
    )
    logger.emit(record)

    flushed = provider.force_flush(timeout_millis=int(timeout * 1000))
    provider.shutdown()

    if captured["error"]:
        return False, f"{endpoint} → {captured['error']}"
    # The OTLP/HTTP log exporter returns an internal enum
    # (LogRecordExportResult) rather than the public LogExportResult, but
    # both share `.name == "SUCCESS"` for the success case. Comparing on the
    # name decouples us from that internal-vs-public split.
    result = captured["result"]
    if result is not None and getattr(result, "name", "") == "SUCCESS":
        return True, f"{endpoint} accepted 1 record (look for clarion.probe=true in Loki)"
    if not flushed:
        return False, f"{endpoint} flush timed out after {timeout}s"
    return False, f"{endpoint} → exporter returned {result!r}"


# ============================================================
# live-tail — Postgres business_events → OTLP logs (v0.5)
# ============================================================

@cli.group("live-tail")
def livetail_group() -> None:
    """Live-tail business_events as OTLP logs (forwarded by Alloy to Loki)."""


@livetail_group.command("run")
@click.argument("plan_id")
@click.option("--customer", default=None,
              help="clarion_customer label. Defaults to a slug of the plan_id.")
@click.option("--batch", default=500, type=int,
              help="Max rows per poll (default: 500).")
@click.option("--interval", default=1.0, type=float,
              help="Seconds between polls when there's no backlog (default: 1.0).")
@click.option("--from-start", is_flag=True, default=False,
              help="Reset the cursor and re-emit every event for this plan.")
@click.option("--yes", is_flag=True, default=False,
              help="Skip the pre-flight tier-limit confirmation.")
@click.option("--no-preflight", is_flag=True, default=False,
              help="Skip the pre-flight ingest estimate entirely.")
def livetail_run(plan_id: str, customer: str | None,
                 batch: int, interval: float, from_start: bool,
                 yes: bool, no_preflight: bool) -> None:
    """Stream Postgres business_events as OTLP logs until ctrl-C."""
    from proj_clarion.livetail.cursor import Cursor
    from proj_clarion.livetail.preflight import (
        estimate_livetail_rate, format_estimate,
    )
    from proj_clarion.livetail.runner import LiveTailer
    from proj_clarion.storage import session_scope

    with session_scope() as s:
        full_plan_id = _resolve_plan_id(s, plan_id)
        if not full_plan_id:
            console.print(f"[red]No plan matches[/red] {plan_id!r}")
            sys.exit(1)

    if from_start:
        Cursor(full_plan_id).reset()
        console.print(f"[yellow]Reset cursor[/yellow] for {full_plan_id[:8]}")

    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "(unset)")
    console.print(Panel.fit(
        f"[bold]Live-tail[/bold]\n"
        f"plan_id   {full_plan_id[:8]}…\n"
        f"customer  {customer or '(derived from plan_id)'}\n"
        f"endpoint  {endpoint}\n"
        f"batch     {batch}\n"
        f"interval  {interval}s\n"
        f"[dim]Streams business_events as OTLP logs; ctrl-C to stop.[/dim]",
        border_style="cyan",
    ))

    # Pre-flight ingest estimate. If a tier limit is set in the env
    # (CLARION_LOKI_BYTES_PER_SEC), warn before saturating it. v0.5's
    # smoke run hit Loki rate limits because we shipped at ~5MB/s
    # against an 873KB/s tier; this catches that before it happens.
    if not no_preflight:
        cursor_value = 0 if from_start else Cursor(full_plan_id).value
        est = estimate_livetail_rate(
            full_plan_id,
            batch_size=batch,
            poll_interval_seconds=interval,
            cursor_value=cursor_value,
        )
        if est.backlog_rows == 0:
            console.print("[dim]Pre-flight: no backlog, nothing to drain.[/dim]")
        else:
            console.print(Panel.fit(
                format_estimate(est),
                title="Pre-flight ingest estimate",
                border_style="yellow" if est.will_exceed_tier_limit else "dim",
            ))
            if est.will_exceed_tier_limit and not yes:
                if not click.confirm(
                    "Estimate exceeds tier limit. Continue anyway?",
                    default=False,
                ):
                    console.print("[red]Aborted.[/red] Re-run with tuning suggestions above.")
                    sys.exit(1)

    tailer = LiveTailer(
        full_plan_id,
        customer=customer,
        batch_size=batch,
        poll_interval_seconds=interval,
    )
    tailer.run()
    console.print(
        f"[green]Stopped[/green]  emitted={tailer.stats.rows_emitted} "
        f"polls={tailer.stats.polls} last_event_id={tailer.stats.last_event_id}"
    )


@livetail_group.command("status")
@click.argument("plan_id")
def livetail_status(plan_id: str) -> None:
    """Show the cursor state and the lag in rows behind the table tip."""
    from sqlalchemy import text as _text

    from proj_clarion.livetail.cursor import Cursor
    from proj_clarion.storage import session_scope

    with session_scope() as s:
        full_plan_id = _resolve_plan_id(s, plan_id)
        if not full_plan_id:
            console.print(f"[red]No plan matches[/red] {plan_id!r}")
            sys.exit(1)
        tip_row = s.execute(
            _text(
                "SELECT COALESCE(MAX(event_id), 0) FROM business_events "
                "WHERE plan_id = :pid"
            ),
            {"pid": full_plan_id},
        ).fetchone()
        tip = int(tip_row[0]) if tip_row else 0

    cursor = Cursor(full_plan_id)
    behind = max(0, tip - cursor.value)
    console.print(
        f"plan        {full_plan_id[:8]}…\n"
        f"cursor      {cursor.value}\n"
        f"table tip   {tip}\n"
        f"lag         {behind} rows"
    )


def _resolve_plan_id(session: Any, prefix_or_full: str) -> str | None:
    """Allow `just plan-show abc12345` for an unambiguous prefix of the UUID."""
    from sqlalchemy import text as _text

    # Exact match (string compare for UUID column)
    row = session.execute(
        _text("SELECT plan_id FROM demo_plans WHERE plan_id::text = :pid"),
        {"pid": prefix_or_full},
    ).fetchone()
    if row:
        return str(row[0])
    # Prefix match
    rows = session.execute(
        _text("SELECT plan_id FROM demo_plans WHERE plan_id::text LIKE :pat LIMIT 2"),
        {"pat": f"{prefix_or_full}%"},
    ).fetchall()
    if len(rows) == 1:
        return str(rows[0][0])
    return None


def _render_plan(plan: Any, history: list[Any]) -> None:
    """Plan summary that fits on one screen. Not a JSON dump."""
    from rich.tree import Tree

    from proj_clarion.schemas import NodeType

    kg = plan.knowledge_graph
    biz_count = sum(1 for n in kg.nodes if n.node_type == NodeType.BUSINESS_ENTITY)
    tech_count = sum(1 for n in kg.nodes if n.node_type == NodeType.TECHNICAL_RESOURCE)
    agent_count = sum(1 for n in kg.nodes if n.node_type == NodeType.AGENTIC_RESOURCE)

    head = (
        f"[bold]{plan.source_profile_id}[/bold]  →  "
        f"[bold cyan]plan {str(plan.plan_id)[:8]}[/bold cyan]\n"
        f"[dim]created {plan.created_at.isoformat(timespec='seconds')}  "
        f"audience={plan.target_audience.value}  state={plan.review_state.value}[/dim]\n\n"
        f"{plan.narrative}"
    )
    console.print(Panel.fit(head, border_style="green"))

    root = Tree("[bold]DemoPlan[/bold]")

    proc_branch = root.add(
        f"[bold]Processes[/bold]  ({len(plan.business_process_models)})"
    )
    for p in plan.business_process_models:
        n = proc_branch.add(
            f"[cyan]{p.process_id}[/cyan]  {p.name}  "
            f"[dim]({len(p.business_steps)} steps · {len(p.failure_modes)} failure modes)[/dim]"
        )
        for fm in p.failure_modes:
            n.add(f"⚠ [yellow]{fm.name}[/yellow]: {fm.description[:60]}")

    kg_branch = root.add(
        f"[bold]Knowledge graph[/bold]  ("
        f"{len(kg.nodes)} nodes · {len(kg.edges)} edges)"
    )
    kg_branch.add(f"business_entity: {biz_count}")
    kg_branch.add(f"technical_resource: {tech_count}")
    if agent_count:
        kg_branch.add(f"agentic_resource: {agent_count}")

    inc = plan.incident_script
    inc_branch = root.add(
        f"[bold]Incident script[/bold]  [dim]{inc.title}[/dim] "
        f"({inc.total_duration_minutes}m · {inc.arming_mode})"
    )
    for ev in inc.events:
        inc_branch.add(
            f"T+{ev.offset_seconds//60}m  [cyan]{ev.event_type.value}[/cyan]  "
            f"→ [yellow]{ev.target_id}[/yellow]  ×{ev.magnitude:.1f}  "
            f"recovers T+{ev.recovery_offset_seconds//60}m"
        )

    dash_branch = root.add(
        f"[bold]Dashboards[/bold]  ({len(plan.dashboard_specs)})"
    )
    for d in plan.dashboard_specs:
        dash_branch.add(f"[cyan]{d.dashboard_id}[/cyan] {d.title}  [dim]({d.audience.value})[/dim]")

    alert_branch = root.add(
        f"[bold]Alerts[/bold]  ({len(plan.alert_specs)})"
    )
    for a in plan.alert_specs:
        alert_branch.add(
            f"[cyan]{a.alert_id}[/cyan] [{a.severity}] {a.business_subject_line[:60]}"
        )

    tools_branch = root.add(
        f"[bold]Assistant tools[/bold]  ({len(plan.assistant_tools)})"
    )
    for t in plan.assistant_tools:
        tools_branch.add(f"[cyan]{t.tool_name}[/cyan] — {t.description[:70]}")

    console.print(root)

    if history:
        console.print()
        ht = Table(title="Audit history", show_header=True, header_style="bold")
        ht.add_column("when"); ht.add_column("actor"); ht.add_column("action")
        ht.add_column("from"); ht.add_column("to"); ht.add_column("note")
        for ts, actor, action, frm, to, note in history:
            ht.add_row(ts.isoformat(timespec="seconds"), actor, action,
                       frm or "", to or "", (note or "")[:60])
        console.print(ht)


if __name__ == "__main__":
    cli()
