"""Policy / guardrail detectors.

Three surfaces:

1. `check_llm_call_anomalies()` — called from `llm_client._persist_call`
   immediately after a successful or failed LLM call lands in postgres.
   Detects cost spikes ($/call > threshold), runaway output
   (output_tokens > threshold), and excessive retries (attempt > 3).

2. `check_prompt_injection()` — called BEFORE passing externally-sourced
   text (scraped pages, profile-extend prompts) into an agent. Scans for
   known injection patterns and records a `critical` violation when one
   matches. The caller can ignore the boolean return; the violation
   record + span event are the durable artefact.

3. `check_tool_scope()` — called from the `track_tool_call` context
   manager. Records a `high` violation when an agent uses a tool that
   isn't in its allow-set (AGENT_TOOL_ALLOWLIST).

All three are no-throw: a missing migration, DB hiccup, or unavailable
OTel still leave the LLM/tool call path working. The OTel span event
and the postgres row are both written best-effort; at least one of the
two will land in every reasonable failure mode.

The thresholds are deliberately conservative for the v1 demo — regulated
buyers' governance ask is "show the architecture", not "tune the thresholds".
Adjust in `THRESHOLDS` once we have real production load data.
"""

from __future__ import annotations

import json
from typing import Any

import structlog

_logger = structlog.get_logger()


# ─── Allow-lists + thresholds ───────────────────────────────────────


# Tool allow-list per agent. Used by `check_tool_scope`. An empty entry
# (or an agent absent from the map) means "no enforcement — every tool
# permitted". New agents start unscoped until we know what they need.
AGENT_TOOL_ALLOWLIST: dict[str, set[str]] = {
    "research_agent": {
        "web_search", "web_fetch",
        "edgar_fetch", "github_org_fetch",
        "greenhouse_fetch", "lever_fetch", "wikidata_fetch",
        "db_read", "kg_read", "kg_write",
    },
    "plan_agent":          {"db_read", "db_write", "kg_read", "kg_write"},
    "provision_agent":     {"api_call", "dashboard_provision", "alert_provision",
                            "db_read", "db_write"},
    "kg_publish_agent":    {"kg_model_rules_push", "kg_prom_rules_push",
                            "kg_entity_emitter_start", "kg_write", "api_call"},
}


# Detection thresholds. Tuned for the v1 demo — most real plan calls
# clock $0.10-0.15, so $0.50 is a real outlier. Output > 8K is a
# runaway generation; we cap max_tokens at 8192 on the planner so a
# call that returns 8K is almost certainly cut off.
THRESHOLDS = {
    "cost_spike_usd":     0.50,
    "output_token_limit": 8_000,
    "max_attempts":       3,
}


# Conservative injection-pattern list. Matched case-insensitively as a
# substring scan — no regex, no fuzzy matching. False positives are
# cheaper than false negatives at the critical severity level since
# the SE reviewing the audit page can quickly mark a row resolved.
_PROMPT_INJECTION_PATTERNS = [
    "ignore previous instructions",
    "ignore prior instructions",
    "disregard all prior",
    "ignore your system prompt",
    "you are now",
    "new persona",
    "act as if you have no restrictions",
    "do anything now",
    "jailbreak",
    "DAN",
]


# ─── Public detector API ────────────────────────────────────────────


def check_llm_call_anomalies(
    *,
    pipeline_id: str | None,
    llm_call_id: str,
    agent_name: str,
    cost_usd: float,
    output_tokens: int,
    attempt: int,
) -> None:
    """Run the three LLM-call detectors. Called from llm_client._persist_call.

    Silently no-ops if the migration isn't applied yet (or the DB is
    unreachable) so the LLM call path stays unbreakable."""
    if cost_usd > THRESHOLDS["cost_spike_usd"]:
        _log_violation(
            pipeline_id=pipeline_id, llm_call_id=llm_call_id,
            agent_name=agent_name,
            violation_type="cost_spike", severity="medium",
            details={
                "cost_usd": float(cost_usd),
                "threshold_usd": THRESHOLDS["cost_spike_usd"],
            },
        )
    if output_tokens > THRESHOLDS["output_token_limit"]:
        _log_violation(
            pipeline_id=pipeline_id, llm_call_id=llm_call_id,
            agent_name=agent_name,
            violation_type="output_too_long", severity="medium",
            details={
                "output_tokens": int(output_tokens),
                "threshold": THRESHOLDS["output_token_limit"],
            },
        )
    if attempt > THRESHOLDS["max_attempts"]:
        _log_violation(
            pipeline_id=pipeline_id, llm_call_id=llm_call_id,
            agent_name=agent_name,
            violation_type="high_attempt_count", severity="low",
            details={
                "attempt": int(attempt),
                "threshold": THRESHOLDS["max_attempts"],
            },
        )


def check_prompt_injection(
    text: str,
    *,
    agent_name: str,
    pipeline_id: str | None = None,
    llm_call_id: str | None = None,
) -> bool:
    """Scan `text` for known injection patterns. Returns True if a
    pattern matched (caller may choose to short-circuit). Either way,
    a `critical` violation row + span event get written."""
    if not text:
        return False
    lowered = text.lower()
    for pattern in _PROMPT_INJECTION_PATTERNS:
        if pattern.lower() in lowered:
            # Truncate the excerpt so we don't dump scraped HTML into
            # the audit row. 300 chars is enough context.
            i = lowered.find(pattern.lower())
            start = max(0, i - 60)
            end = min(len(text), i + len(pattern) + 60)
            excerpt = text[start:end]
            _log_violation(
                pipeline_id=pipeline_id, llm_call_id=llm_call_id,
                agent_name=agent_name,
                violation_type="prompt_injection", severity="critical",
                details={"pattern_matched": pattern, "excerpt": excerpt[:300]},
            )
            return True
    return False


def check_tool_scope(
    *,
    agent_name: str,
    tool_name: str,
    pipeline_id: str | None = None,
    llm_call_id: str | None = None,
) -> bool:
    """Check that the agent is allowed to use this tool. Returns False
    when out-of-scope (and records a `high` violation). The
    `track_tool_call` context manager calls this BEFORE yielding so
    the offending invocation still runs (we audit, we don't block —
    that decision belongs to a human-in-the-loop). Returns True for
    agents without an allow-list configured."""
    allowed = AGENT_TOOL_ALLOWLIST.get(agent_name)
    if not allowed:
        return True
    if tool_name in allowed:
        return True
    _log_violation(
        pipeline_id=pipeline_id, llm_call_id=llm_call_id,
        agent_name=agent_name,
        violation_type="unexpected_tool", severity="high",
        details={
            "tool_called": tool_name,
            "allowed_tools": sorted(allowed),
        },
    )
    return False


# ─── Internal: write + emit span event ──────────────────────────────


def _log_violation(
    *,
    pipeline_id: str | None,
    llm_call_id: str | None,
    agent_name: str,
    violation_type: str,
    severity: str,
    details: dict[str, Any],
) -> None:
    """Persist + emit. Two best-effort writes — neither is allowed to
    raise. Both surfaces (postgres row + Tempo span event) carry the
    same data so a Grafana alert can fire even if one of the two
    sinks is degraded."""
    # 1. Postgres row (durable).
    try:
        from proj_clarion.storage import PolicyViolationRepo, session_scope
        with session_scope() as s:
            PolicyViolationRepo().record(
                s,
                agent_name=agent_name,
                violation_type=violation_type,
                severity=severity,
                pipeline_id=pipeline_id,
                llm_call_id=llm_call_id,
                details=details,
            )
    except Exception as exc:  # noqa: BLE001
        _logger.debug(
            "policy.persist.skip",
            violation_type=violation_type,
            error=str(exc)[:200],
        )

    # 2. Tempo span event (best-effort; surfaces in AI-obs trace tree).
    try:
        from opentelemetry import trace
        span = trace.get_current_span()
        # Only set on a recording span — outside a request there's a
        # NonRecordingSpan that swallows events silently anyway, but
        # the explicit check makes the intent obvious.
        if span and span.is_recording():
            span.add_event("policy_violation", {
                "clarion.violation.type": violation_type,
                "clarion.violation.severity": severity,
                "clarion.agent.name": agent_name,
                "clarion.violation.details_json": json.dumps(details)[:500],
            })
    except Exception:  # noqa: BLE001
        pass

    # 3. Structured log so a tail of stdout shows the same thing.
    _logger.warning(
        "policy.violation",
        agent=agent_name,
        type=violation_type,
        severity=severity,
        pipeline_id=pipeline_id,
        details=details,
    )
