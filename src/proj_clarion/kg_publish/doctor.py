"""KG validation framework — `proj-clarion kg doctor`.

The KG agent (planner + kg_publish) ships a lot of state to Grafana Cloud:
metrics, model rules, recording rules, custom entities, relations. Every
one of those has invariants the v0.6/v0.7 model depends on, and we've
hit several silent regressions in the last week (asserts_env doubling,
clarion_customer dropped from kube_node_info, KubeCluster entities not
materializing, services-DBs disconnected).

This module is the post-emit check that catches them. Every check
expresses one invariant the model needs to be true; together they
verify the data pipeline is healthy without you having to eyeball the
KG visualization.

Each check returns:
  - status: pass / fail / warn / skip
  - detail: what was observed
  - fix:    what to do about it (when actionable)

Usage:
    from proj_clarion.kg_publish.doctor import run_doctor
    report = run_doctor(plan_id="...", customer="acme_retail")
    print(report.summary)
    for check in report.checks:
        print(check.name, check.status, check.detail)

Calls Mimir + Cloud KG via `gcx` (which is already authed in the user's
session), so the doctor is callable from the CLI, the API, or a future
auto-run after kg-publish.
"""

from __future__ import annotations

import json
import re
import subprocess
from dataclasses import dataclass, field
from typing import Any, Literal

from proj_clarion.schemas import KnowledgeGraph, NodeType
from proj_clarion.storage import PlanRepo, session_scope


# ─── Result types ────────────────────────────────────────────────────


Status = Literal["pass", "fail", "warn", "skip"]


@dataclass
class Check:
    """One invariant verification result. `fix` is a one-line hint when
    the failure is actionable; None when the next step requires
    investigation."""
    name: str
    status: Status
    detail: str
    fix: str | None = None


@dataclass
class Report:
    plan_id: str | None
    customer: str | None
    checks: list[Check] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not any(c.status == "fail" for c in self.checks)

    @property
    def counts(self) -> dict[str, int]:
        return {
            s: sum(1 for c in self.checks if c.status == s)
            for s in ("pass", "fail", "warn", "skip")
        }

    @property
    def summary(self) -> str:
        c = self.counts
        return f"{c['pass']} pass · {c['warn']} warn · {c['fail']} fail · {c['skip']} skip"


# ─── gcx primitives ─────────────────────────────────────────────────


def _gcx_metrics_query(query: str) -> dict[str, Any] | None:
    """Run a PromQL instant query through gcx. Returns None on any
    failure (auth, network, parse) — callers decide whether the check
    skips or fails."""
    try:
        proc = subprocess.run(
            ["gcx", "metrics", "query", query, "--agent"],
            capture_output=True, text=True, timeout=20,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    out = proc.stdout
    # gcx prints a "hint" preamble before the JSON; strip it
    if out.lstrip().startswith("hint:"):
        nl = out.find("\n")
        if nl >= 0:
            out = out[nl + 1:]
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return None


def _gcx_kg_entities(entity_type: str) -> list[dict[str, Any]] | None:
    """List Cloud KG entities of a given type. Returns None on failure."""
    try:
        proc = subprocess.run(
            ["gcx", "kg", "entities", "list", "--type", entity_type, "--agent"],
            capture_output=True, text=True, timeout=20,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    out = proc.stdout
    if out.lstrip().startswith("hint:"):
        nl = out.find("\n")
        if nl >= 0:
            out = out[nl + 1:]
    try:
        d = json.loads(out)
        if isinstance(d, list):
            return d
        return d.get("items", d.get("entities", [])) if isinstance(d, dict) else None
    except json.JSONDecodeError:
        return None


def _series_count(query_result: dict[str, Any] | None) -> int:
    """Pull the result count from an instant-query response."""
    if not query_result:
        return 0
    return len(query_result.get("data", {}).get("result", []))


def _series_results(query_result: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not query_result:
        return []
    return query_result.get("data", {}).get("result", [])


def _has_doubled_value(query_result: dict[str, Any] | None, label: str) -> bool:
    """Returns True if any result has `;` in the named label value
    (the signature of the asserts.env vs deployment.environment
    relabel-concatenation regression)."""
    for r in _series_results(query_result):
        v = r.get("metric", {}).get(label, "")
        if ";" in v:
            return True
    return False


# ─── Check implementations ───────────────────────────────────────────


def _check_mimir_reachable() -> Check:
    """Smoke: can we even talk to Mimir via gcx?"""
    r = _gcx_metrics_query("vector(1)")
    if r is None:
        return Check(
            "Mimir reachable via gcx",
            "fail",
            "Cannot run any other check — gcx returned no data. "
            "Check that gcx is logged in and the user has metrics:read.",
            fix="`gcx auth login` and re-run.",
        )
    return Check("Mimir reachable via gcx", "pass", "instant query returned 200")


def _check_kube_node_info_present(customer: str) -> Check:
    name = "kube_node_info series exist for customer"
    r = _gcx_metrics_query(
        f'count by (cluster) (kube_node_info{{clarion_customer="{customer}"}})'
    )
    n = _series_count(r)
    if n == 0:
        return Check(
            name, "fail",
            f"No kube_node_info series found for clarion_customer={customer!r}.",
            fix="Restart the kg-publish emitter; verify red_emitter is reaching Cloud.",
        )
    return Check(name, "pass", f"{n} distinct clusters reporting")


def _check_kube_node_info_customer_label(customer: str) -> Check:
    """If there's any kube_node_info series WITHOUT clarion_customer,
    KubeCluster entities won't be customer-scoped. This is the v0.6.4
    regression that hid clusters from the Customer Entities saved search."""
    name = "kube_node_info carries clarion_customer"
    # Use absent() trick: count() of empty-customer series
    r = _gcx_metrics_query('count(kube_node_info unless on() kube_node_info{clarion_customer!=""})')
    n = _series_count(r)
    if n > 0:
        return Check(
            name, "warn",
            f"{n} kube_node_info series without clarion_customer label — old/stale series.",
            fix="They'll age out of retention. New emitter runs carry the label correctly.",
        )
    return Check(name, "pass", "every series has the customer label")


def _check_no_doubled_scope(customer: str) -> Check:
    """asserts_env or asserts_site containing `;` is the doubling
    regression — Asserts treats those as a different scope, splitting
    your entities and breaking every relation."""
    name = "no doubled asserts_env / asserts_site values"
    queries = [
        ('clarion_entity_info', f'clarion_entity_info{{clarion_customer="{customer}"}}'),
        ('kube_node_info',      f'kube_node_info{{clarion_customer="{customer}"}}'),
        ('target_info',         f'target_info{{clarion_customer="{customer}"}}'),
    ]
    bad: list[str] = []
    for label_name, q in queries:
        r = _gcx_metrics_query(q)
        if _has_doubled_value(r, "asserts_env") or _has_doubled_value(r, "asserts_site"):
            bad.append(label_name)
    if bad:
        return Check(
            name, "fail",
            f"found `;`-doubled scope on: {', '.join(bad)}",
            fix="Don't set asserts.env or asserts.site as observation attrs — "
                "they belong on the Resource only. Asserts' relabel rule "
                "concatenates with deployment.environment otherwise.",
        )
    return Check(name, "pass", "all queried metrics have single-valued scope")


def _check_pods_have_node_label(customer: str) -> Check:
    """v0.6.5 added `node` label to Pod observations so the built-in
    Node HOSTS Pod relation has a join key."""
    name = "Pod observations carry `node` label"
    r = _gcx_metrics_query(
        f'count by (clarion_pod_id) (clarion_entity_info{{'
        f'clarion_customer="{customer}", clarion_entity_kind="pod", node!=""}})'
    )
    with_node = _series_count(r)
    r_total = _gcx_metrics_query(
        f'count by (clarion_pod_id) (clarion_entity_info{{'
        f'clarion_customer="{customer}", clarion_entity_kind="pod"}})'
    )
    total = _series_count(r_total)
    if total == 0:
        return Check(name, "skip", "no Pod observations to check")
    if with_node < total:
        return Check(
            name, "fail",
            f"{total - with_node} of {total} pods missing the `node` label.",
            fix="Verify _attach_pod_to_node ran in EntityEmitter.__init__ "
                "and the pod has a cluster_id with kubenodes available.",
        )
    return Check(name, "pass", f"all {total} pods carry node")


def _check_stores_fan_out(customer: str) -> Check:
    """v0.6.1 fan-out: each Store should emit one observation per service
    in its per-store cluster. <2 series per store ⇒ HOSTS relation won't fire."""
    name = "Stores fan out across services in their cluster"
    r = _gcx_metrics_query(
        f'count by (clarion_store_id) (clarion_entity_info{{'
        f'clarion_customer="{customer}", clarion_entity_kind="store"}})'
    )
    results = _series_results(r)
    if not results:
        return Check(name, "skip", "no Store entities found")
    issues: list[str] = []
    for row in results:
        sid = row["metric"].get("clarion_store_id", "?")
        n = int(float(row["value"][1]))
        if n < 2:
            issues.append(f"{sid}={n}")
    if issues:
        return Check(
            name, "warn",
            f"some stores emit < 2 observations: {', '.join(issues[:5])}"
            + ("..." if len(issues) > 5 else ""),
            fix="Likely those stores have no per-store cluster (planner-original FCs hit this). "
                "HOSTS relation won't fire for them. v0.7 carryover.",
        )
    return Check(name, "pass", f"every store has ≥2 observations (fan-out working)")


def _check_service_db_affinity(customer: str) -> Check:
    """The custom Service USES Database relation needs at least one
    pair to materialize, otherwise the model rule renders a no-op
    relation and DBs visually float."""
    name = "Service→Database affinity metric is flowing"
    r = _gcx_metrics_query(
        f'count by (service) (clarion_service_database_affinity{{clarion_customer="{customer}"}})'
    )
    n = _series_count(r)
    if n == 0:
        return Check(
            name, "fail",
            "0 service-database pairs in clarion_service_database_affinity",
            fix="Verify red_emitter._service_db_pairs is populated from "
                "depends_on edges, and the gauge callback is registered.",
        )
    return Check(name, "pass", f"{n} distinct services declare DB dependencies")


def _check_channel_service_affinity(customer: str) -> Check:
    name = "Channel→Service affinity metric is flowing"
    r = _gcx_metrics_query(
        f'count by (clarion_channel_id) (clarion_channel_service_affinity{{clarion_customer="{customer}"}})'
    )
    n = _series_count(r)
    if n == 0:
        return Check(
            name, "warn",
            "0 channel-service pairs — not necessarily wrong, but the "
            "Channel SERVES Service relation will be empty.",
            fix="Verify the planner emits SERVES edges from Channel nodes to Service nodes.",
        )
    return Check(name, "pass", f"{n} channels declare service relationships")


def _check_kg_count_matches_entities(plan_id: str, customer: str) -> Check:
    """Cross-check: number of Stores/Channels/Regions in the plan KG
    should match the count of the corresponding entity series in Mimir.
    Catches "emitter started but isn't actually flowing" silently.

    The expected count comes from the EXPANDED KG (post-`expand_with_
    synthetic_infra`) — that's what the EntityEmitter actually publishes.
    Reading `plan.knowledge_graph` directly compares pre-expansion to
    post-expansion and reports phantom mismatches like
    `store expected=0 actual=1` for industrial verticals (ITC, etc.)
    where the planner emits zero stores but the synthesizer fabricates
    a couple to keep the demo interesting.
    """
    from proj_clarion.kg_publish.expand import expand_with_synthetic_infra

    name = "Plan KG entity counts match Mimir series"
    with session_scope() as s:
        plan = PlanRepo().get(s, plan_id)
    if plan is None:
        return Check(name, "skip", f"plan {plan_id[:8]} not in DB")
    kg: KnowledgeGraph = expand_with_synthetic_infra(plan)

    expected = {
        "store":              sum(1 for n in kg.nodes if n.business_subtype == "store"),
        "channel":            sum(1 for n in kg.nodes if n.business_subtype == "channel"),
        "region":             sum(1 for n in kg.nodes if n.business_subtype == "region"),
        "fulfillment_center": sum(1 for n in kg.nodes if n.business_subtype == "fulfillment_center"),
    }
    actual: dict[str, int] = {}
    for kind in expected:
        r = _gcx_metrics_query(
            f'count by (clarion_{kind}_id) (clarion_entity_info{{'
            f'clarion_customer="{customer}", clarion_entity_kind="{kind}"}})'
        )
        actual[kind] = _series_count(r)

    # Asymmetric severity: actual < expected is a real bug (entities not
    # being emitted by the live emitter); actual > expected is almost
    # always transient (stale series from a prior emitter process still
    # within Mimir's 5-min retention window after kg-publish was re-run).
    # Treating both as `fail` cried wolf every time the user iterated.
    missing = [
        f"{kind} expected={e} actual={actual[kind]}"
        for kind, e in expected.items()
        if actual[kind] < e
    ]
    excess = [
        f"{kind} expected={e} actual={actual[kind]}"
        for kind, e in expected.items()
        if actual[kind] > e
    ]
    if missing:
        # Hard failure: the live emitter isn't covering some entities.
        return Check(
            name, "fail",
            "; ".join(missing) + (
                " (also excess: " + "; ".join(excess) + ")" if excess else ""
            ),
            fix="Either the emitter isn't running, isn't reaching Cloud, or some "
                "entities aren't being emitted. Check the kg-publish run log.",
        )
    if excess:
        # Soft signal: probably stale series from a previous build/emitter
        # still in Mimir's retention window. Will age out within ~5 min.
        return Check(
            name, "warn",
            "; ".join(excess),
            fix="Stale series from a previous emitter haven't aged out yet "
                "(Mimir keeps series ~5 min past last sample). Re-run the check "
                "in a few minutes; if it persists, a duplicate emitter process "
                "may be running for this plan — check `ps aux | grep kg.publish`.",
        )
    return Check(name, "pass",
                 f"all kinds match: {', '.join(f'{k}={v}' for k,v in expected.items())}")


def _check_kubecluster_entities_materialize(customer: str) -> Check:
    """KubeCluster materialisation in Cloud KG. With the v0.7.x model fix
    (added KubeCluster type to clarion-business-model.yaml), entity count
    should match distinct cluster values in kube_node_info."""
    name = "KubeCluster entities materialised in Cloud KG"
    r = _gcx_metrics_query(
        f'count by (cluster) (kube_node_info{{clarion_customer="{customer}"}})'
    )
    expected_clusters = _series_count(r)
    if expected_clusters == 0:
        return Check(name, "skip", "no kube_node_info series — emitter not running yet")
    entities = _gcx_kg_entities("KubeCluster")
    if entities is None:
        return Check(name, "skip", "could not list KubeCluster entities via gcx")
    # Filter to the customer if entities carry it
    relevant = [
        e for e in entities
        if (e.get("properties") or {}).get("customer") == customer
        or e.get("name", "").startswith("cluster-")  # fall back to name pattern
    ]
    if len(relevant) < expected_clusters:
        return Check(
            name, "fail",
            f"expected ≥{expected_clusters} clusters, found {len(relevant)}",
            fix="The model rule must define a KubeCluster type (the built-in alone "
                "doesn't match our kube_node_info shape). Verify "
                "clarion-business-model-1a7a1fad in Cloud has the KubeCluster type.",
        )
    return Check(name, "pass", f"{len(relevant)} KubeCluster entities materialised")


def _check_label_pattern_clean(customer: str) -> Check:
    """Generic: customer label values shouldn't contain unexpected punctuation
    (catches leakage of things like 'acme_retail;' or 'acme_retail-test\n')."""
    name = "clarion_customer values are well-formed"
    r = _gcx_metrics_query(
        'count by (clarion_customer) (clarion_entity_info{clarion_customer!=""})'
    )
    bad: list[str] = []
    for row in _series_results(r):
        v = row["metric"].get("clarion_customer", "")
        if not re.fullmatch(r"[a-z0-9-]+", v):
            bad.append(v)
    if bad:
        return Check(
            name, "warn",
            f"non-canonical customer values seen: {', '.join(bad[:5])}",
            fix="Customers should be lowercase, alphanumeric, hyphenated. "
                "Mixed values usually mean stale series from old test runs.",
        )
    # Also flag if the queried customer isn't even present
    customers = {row["metric"].get("clarion_customer") for row in _series_results(r)}
    if customer not in customers:
        return Check(
            name, "warn",
            f"customer {customer!r} not present in clarion_entity_info — "
            f"only saw: {sorted(customers)}",
            fix="Either the emitter for this customer isn't running, or stale "
                "series are masking the current run.",
        )
    return Check(name, "pass", f"all customer labels canonical; {customer!r} present")


# ─── Entry point ─────────────────────────────────────────────────────


def run_doctor(plan_id: str | None, customer: str | None = None) -> Report:
    """Execute every check that's applicable given what's available
    (DB plan, customer slug, gcx auth). Returns a Report with one Check
    per invariant.

    Designed to be safe to call multiple times — every probe is read-only.
    """
    # Resolve customer from plan if not given
    if customer is None and plan_id is not None:
        with session_scope() as s:
            plan = PlanRepo().get(s, plan_id)
        if plan is not None:
            # Match _slug_for_plan in emitter.py
            pid = plan.source_profile_id
            customer = (
                pid.removeprefix("prof-")
                if pid.startswith("prof-")
                else pid
            ).lower()

    report = Report(plan_id=plan_id, customer=customer)

    # First check: gcx working at all. If this fails, every downstream
    # check would skip with confusing messages.
    smoke = _check_mimir_reachable()
    report.checks.append(smoke)
    if smoke.status == "fail":
        return report

    if customer is None:
        report.checks.append(Check(
            "customer slug resolved",
            "fail",
            "no customer slug — supply --customer or pass a valid plan_id",
        ))
        return report

    report.checks.append(_check_kube_node_info_present(customer))
    report.checks.append(_check_kube_node_info_customer_label(customer))
    report.checks.append(_check_no_doubled_scope(customer))
    report.checks.append(_check_pods_have_node_label(customer))
    report.checks.append(_check_stores_fan_out(customer))
    report.checks.append(_check_service_db_affinity(customer))
    report.checks.append(_check_channel_service_affinity(customer))
    report.checks.append(_check_label_pattern_clean(customer))
    report.checks.append(_check_kubecluster_entities_materialize(customer))

    if plan_id:
        report.checks.append(_check_kg_count_matches_entities(plan_id, customer))

    return report
