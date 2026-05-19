"""Structural evals for pipeline-produced artefacts.

These run after a phase finishes and produces its artefact (research →
CompanyProfile, plan → DemoPlan). They check the *shape* of what came
back, not the *correctness* of the content. Shape regressions are what
break downstream phases (a plan with 0 KG nodes can't materialise as a
demo); content drift is what the SE eyeballs.

Each check is recorded two ways:
1. As a row in `llm_evals` (durable, queryable in postgres).
2. As an OTel span event named `clarion.eval` on the current span
   (joinable with the gen_ai.* spans that produced the artefact).

A failed eval is *not* a hard error — the artefact still ships, the
eval just shows up red in the dashboard. Hard errors live at the
pydantic-validation layer.

# Adding a new eval

Define a `(name, passed, score, details)` tuple inside the relevant
runner, and the rest takes care of itself. Keep evals fast (these run
synchronously inside the pipeline) and side-effect-free.
"""

from __future__ import annotations

from typing import Any

import structlog

from proj_clarion.observability.llm_client import current_phase, current_pipeline_id

_logger = structlog.get_logger()


# ─── Eval runners ────────────────────────────────────────────────────


def run_research_evals(
    profile: Any,
    *,
    model: str | None = None,
    prompt_version: str | None = None,
) -> list[dict[str, Any]]:
    """Evals for the research phase output (CompanyProfile).

    Returns the list of results (also persists each one). `profile` is
    a pydantic CompanyProfile; we access fields by attribute.
    """
    results: list[dict[str, Any]] = []

    source_count = len(getattr(profile, "provenance", []) or [])
    results.append(_record(
        phase="research", eval_name="source_count_ge_3",
        passed=source_count >= 3, score=float(source_count),
        model=model, prompt_version=prompt_version,
        details={"sources": source_count},
    ))

    # Required-fields completeness — exercise the same fields the plan
    # phase relies on. Pydantic already validates structure; this
    # catches "validated but empty" cases.
    required_lists = {
        "channels":                 len(getattr(profile, "channels", []) or []),
        "tech_stack_signals":       len(getattr(profile, "tech_stack_signals", []) or []),
        "pain_signals":             len(getattr(profile, "pain_signals", []) or []),
        "recent_strategic_priorities": len(getattr(profile, "recent_strategic_priorities", []) or []),
    }
    empty_lists = [k for k, v in required_lists.items() if v == 0]
    results.append(_record(
        phase="research", eval_name="profile_json_complete",
        passed=not empty_lists,
        score=float(len(required_lists) - len(empty_lists)),
        model=model, prompt_version=prompt_version,
        details={"counts": required_lists, "empty_fields": empty_lists},
    ))

    # Organizational model is optional in the schema but its absence
    # makes the plan phase fall back to a generic catalog — worth
    # flagging so the SE knows whether the research is fully fit.
    org_present = getattr(profile, "organizational_model", None) is not None
    results.append(_record(
        phase="research", eval_name="organizational_model_present",
        passed=org_present, score=1.0 if org_present else 0.0,
        model=model, prompt_version=prompt_version,
        details={},
    ))

    return results


def run_plan_evals(
    plan: Any,
    *,
    model: str | None = None,
    prompt_version: str | None = None,
) -> list[dict[str, Any]]:
    """Evals for the plan phase output (DemoPlan)."""
    results: list[dict[str, Any]] = []

    # Plan got through pydantic validation if we're here, so this is a
    # tautology — but recording it keeps the dashboard's "did the schema
    # check pass" row populated, and gives us somewhere to attach
    # validation-error counts later if we widen the parse to be lenient.
    results.append(_record(
        phase="plan", eval_name="plan_json_schema_valid",
        passed=True, score=1.0,
        model=model, prompt_version=prompt_version,
        details={},
    ))

    kg = getattr(plan, "knowledge_graph", None)
    nodes = list(getattr(kg, "nodes", []) or [])
    edges = list(getattr(kg, "edges", []) or [])

    results.append(_record(
        phase="plan", eval_name="kg_node_count_ge_5",
        passed=len(nodes) >= 5, score=float(len(nodes)),
        model=model, prompt_version=prompt_version,
        details={"node_count": len(nodes)},
    ))
    results.append(_record(
        phase="plan", eval_name="kg_edge_count_ge_5",
        passed=len(edges) >= 5, score=float(len(edges)),
        model=model, prompt_version=prompt_version,
        details={"edge_count": len(edges)},
    ))

    dashboards = list(getattr(plan, "dashboard_specs", []) or [])
    alerts     = list(getattr(plan, "alert_specs", []) or [])
    results.append(_record(
        phase="plan", eval_name="has_dashboards",
        passed=len(dashboards) >= 1, score=float(len(dashboards)),
        model=model, prompt_version=prompt_version,
        details={"dashboard_count": len(dashboards)},
    ))
    results.append(_record(
        phase="plan", eval_name="has_alerts",
        passed=len(alerts) >= 1, score=float(len(alerts)),
        model=model, prompt_version=prompt_version,
        details={"alert_count": len(alerts)},
    ))

    incident = getattr(plan, "incident_script", None)
    events = list(getattr(incident, "events", []) or [])
    results.append(_record(
        phase="plan", eval_name="incident_has_events",
        passed=len(events) >= 1, score=float(len(events)),
        model=model, prompt_version=prompt_version,
        details={"event_count": len(events)},
    ))

    # Cross-reference: each business process's services_implementing
    # should resolve to a KG node. Catches LLM hallucinating service
    # names that don't exist anywhere else in the plan.
    service_node_ids: set[str] = {
        getattr(n, "node_id", "") for n in nodes
        if getattr(n, "technical_subtype", None) == "service"
    }
    referenced: set[str] = set()
    for bp in getattr(plan, "business_process_models", []) or []:
        for step in getattr(bp, "business_steps", []) or []:
            for svc in getattr(step, "services_implementing", []) or []:
                referenced.add(svc)
    missing = sorted(referenced - service_node_ids - {""})
    results.append(_record(
        phase="plan", eval_name="no_hallucinated_services",
        passed=not missing, score=float(len(referenced) - len(missing)),
        model=model, prompt_version=prompt_version,
        details={
            "referenced": sorted(referenced),
            "missing":    missing,
        },
    ))

    return results


# ─── Internals ───────────────────────────────────────────────────────


def _record(
    *,
    phase: str,
    eval_name: str,
    passed: bool,
    score: float | None,
    model: str | None,
    prompt_version: str | None,
    details: dict[str, Any],
) -> dict[str, Any]:
    """Persist one eval result and emit a span event. Returns the
    result dict (for testability + caller logging)."""
    pipeline_id = current_pipeline_id() or None

    # 1. DB row — best-effort.
    try:
        from proj_clarion.storage import LlmEvalRepo, session_scope
        with session_scope() as s:
            LlmEvalRepo().record(
                s,
                phase=phase,
                eval_name=eval_name,
                passed=passed,
                pipeline_id=pipeline_id,
                score=score,
                model=model,
                prompt_version=prompt_version,
                details=details,
            )
    except Exception as exc:  # noqa: BLE001
        _logger.debug("eval.persist.skip", eval_name=eval_name, error=str(exc)[:200])

    # 2. Span event on the currently-active span, if any.
    try:
        from opentelemetry import trace
        span = trace.get_current_span()
        if span and span.is_recording():
            span.add_event("clarion.eval", attributes={
                "eval.name":   eval_name,
                "eval.passed": bool(passed),
                "eval.score":  float(score) if score is not None else 0.0,
                "eval.phase":  phase,
            })
    except Exception:  # noqa: BLE001 — OTel optional in tests
        pass

    result = {
        "phase":      phase,
        "eval_name":  eval_name,
        "passed":     bool(passed),
        "score":      score,
        "details":    details,
    }
    _logger.info(
        "eval.recorded",
        phase=phase, eval=eval_name, passed=passed, score=score,
    )
    return result


# Phase awareness — used when a caller doesn't know which phase it's in.
def phase_from_context() -> str:
    return current_phase()
