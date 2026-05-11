"""Grafana Assistant dashboard-refinement integration.

After Clarion provisions its command-center dashboard, this module asks
Grafana Assistant (via `gcx assistant prompt`) for vertical-specific
panel improvements. The Assistant has access to the user's stack, can
query metrics, and knows current Grafana Cloud best practices — so its
suggestions are grounded in *what's actually emitted* rather than what
the planner LLM thought might exist.

Two integration shapes:
  - `refine_dashboard(plan_id, customer)` — synchronous call, returns
    suggestions as structured data
  - CLI: `python -m proj_clarion.cli.main provision refine <plan_id>` —
    runs the call + appends an audit entry with the suggestions

The Assistant call uses `--no-stream --json` so we get a single JSON
object back (instead of NDJSON event stream). Failures are non-fatal —
suggestions are nice-to-have, never block the build.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from typing import Any

import structlog

from proj_clarion.schemas import DemoPlan

_logger = structlog.get_logger()


@dataclass
class RefinementSuggestion:
    """One Assistant-proposed dashboard panel improvement."""
    title: str
    viz: str
    promql: str
    rationale: str


@dataclass
class RefinementResult:
    """Outcome of the Assistant call. `suggestions` is empty when the
    call failed or returned unparseable output — `error` carries the
    reason in that case."""
    plan_id: str
    customer: str
    business_model: str
    suggestions: list[RefinementSuggestion]
    raw_response: str
    error: str | None


def _build_prompt(plan: DemoPlan, customer: str, business_model: str) -> str:
    """Compose the message we send to GS. Concise — every additional
    token costs Assistant credits and risks longer replies that won't
    parse cleanly. Vertical context is the key input; everything else
    GS can introspect from the stack."""
    company = plan.knowledge_graph.nodes[0].label if plan.knowledge_graph.nodes else customer
    services = [n.node_id for n in plan.knowledge_graph.nodes
                if n.technical_subtype == "service"][:8]
    return (
        f"I'm refining a Grafana Cloud business-command-center dashboard "
        f"for a {business_model} company called {customer}. "
        f"\n\n"
        f"Currently emitted metrics for this customer "
        f'(filter by `clarion_customer=\"{customer}\"`):\n'
        f"  - `clarion_business_revenue_usd_total` (labels: channel, region, "
        f"and a vertical-specific primary axis like `business_unit`/`store`)\n"
        f"  - `clarion_business_orders_total` (same labels)\n"
        f"  - `clarion_customer_health_score` (gauge, 0-100)\n"
        f"  - RED metrics per service: `service_calls_total{{status,service}}`, "
        f"`service_call_duration_seconds_bucket`\n"
        f"\n"
        f"Sample services in this plan: {', '.join(services[:5]) or '(none)'}\n"
        f"\n"
        f"Suggest 3-5 specific dashboard panel improvements that would be "
        f"most insightful for a {business_model} customer. For each: a "
        f"concise panel title, viz type, PromQL query, and one-line rationale. "
        f"Return ONLY this JSON shape, no markdown fences, no prose:\n"
        f'{{"suggestions": [{{"title": "...", "viz": "stat|gauge|barchart|'
        f'piechart|timeseries", "promql": "...", "rationale": "..."}}]}}'
    )


def _extract_json_object(blob: str) -> dict[str, Any] | None:
    """Pull the first valid JSON object out of GS's response.

    GS sometimes wraps its reply in prose (despite being told not to).
    We scan for the first `{` and try parsing progressively-larger spans
    until one succeeds. Fall back to None when nothing parses — caller
    treats that as a soft failure."""
    if not blob:
        return None
    start = blob.find("{")
    if start < 0:
        return None
    # Try parsing the whole tail first (most common), then progressively
    # shorter spans until we find a balanced object.
    for end in range(len(blob), start, -1):
        try:
            obj = json.loads(blob[start:end])
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            continue
    return None


def refine_dashboard(
    plan: DemoPlan, *, customer: str, business_model: str,
    timeout_seconds: int = 60,
) -> RefinementResult:
    """Ask GS for vertical-specific dashboard improvements. Synchronous.
    Always returns a `RefinementResult` — never raises — so callers can
    treat the call as best-effort enrichment."""
    plan_id = str(plan.plan_id)
    prompt = _build_prompt(plan, customer, business_model)

    try:
        proc = subprocess.run(
            [
                "gcx", "assistant", "prompt", prompt,
                "--no-stream", "--json",
                "--timeout", str(timeout_seconds),
            ],
            capture_output=True, text=True, timeout=timeout_seconds + 10,
        )
    except (subprocess.TimeoutExpired, FileNotFoundError) as exc:
        msg = f"gcx assistant call failed: {exc}"
        _logger.warning("gs_refine.subprocess_failed", error=msg)
        return RefinementResult(plan_id, customer, business_model, [], "", msg)

    if proc.returncode != 0:
        # gcx prints a structured error on stdout in agent mode; surface it
        err = (proc.stdout or proc.stderr or "").strip()[:500]
        _logger.warning(
            "gs_refine.gcx_nonzero",
            returncode=proc.returncode, error_tail=err,
        )
        return RefinementResult(plan_id, customer, business_model, [], err, err)

    raw = proc.stdout or ""
    # In `--no-stream --json` mode gcx emits a single A2A task envelope.
    # The Assistant's actual reply lives in `result.message.parts[0].text`
    # or similar — extract carefully because the schema can vary.
    envelope = _extract_json_object(raw)
    text_reply = ""
    if isinstance(envelope, dict):
        # Common A2A response shapes seen in practice:
        text_reply = (
            (envelope.get("result") or {}).get("message", {}).get("parts", [{}])[0].get("text", "")
            if isinstance(envelope.get("result"), dict) else ""
        ) or envelope.get("response", "") or envelope.get("text", "") or ""
    if not text_reply:
        # Fall back to scanning the whole blob for an inner suggestions object
        text_reply = raw

    parsed = _extract_json_object(text_reply)
    if not parsed or "suggestions" not in parsed:
        msg = "GS reply did not contain a parseable suggestions object"
        _logger.warning("gs_refine.parse_failed",
                        raw_tail=raw[:300], reply_tail=text_reply[:300])
        return RefinementResult(
            plan_id, customer, business_model, [],
            text_reply[:1000], msg,
        )

    suggestions: list[RefinementSuggestion] = []
    for s in parsed.get("suggestions", []):
        if not isinstance(s, dict):
            continue
        try:
            suggestions.append(RefinementSuggestion(
                title=str(s.get("title", "")).strip()[:120],
                viz=str(s.get("viz", "timeseries")).strip()[:30],
                promql=str(s.get("promql", "")).strip()[:1000],
                rationale=str(s.get("rationale", "")).strip()[:500],
            ))
        except Exception:  # noqa: BLE001
            continue

    _logger.info(
        "gs_refine.ok",
        plan_id=plan_id, customer=customer, business_model=business_model,
        suggestion_count=len(suggestions),
    )
    return RefinementResult(
        plan_id, customer, business_model,
        suggestions, text_reply[:2000], None,
    )


def format_audit_note(result: RefinementResult) -> str:
    """Render the refinement result as an audit-note string.
    Audit notes are plaintext + URLs; the UI's LinkifiedNote renders
    URLs as clickable but otherwise shows the text as-is. Multi-line
    bulleted format reads cleanly in the audit panel."""
    if result.error and not result.suggestions:
        return f"GS dashboard review skipped: {result.error}"
    lines = [
        f"GS dashboard review: {len(result.suggestions)} panel suggestion"
        f"{'' if len(result.suggestions) == 1 else 's'} for {result.customer} "
        f"({result.business_model}).",
    ]
    for i, s in enumerate(result.suggestions, 1):
        lines.append(f"  {i}. {s.title} [{s.viz}] — {s.rationale}")
        lines.append(f"      promql: {s.promql}")
    return "\n".join(lines)
