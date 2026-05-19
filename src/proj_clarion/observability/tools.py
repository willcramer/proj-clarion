"""Agent tool-call instrumentation.

Provides one public helper: the `track_tool_call` context manager.
Use it around every call to an external system (HTTP API, Postgres,
KG read/write, filesystem, shell). It produces three artefacts in
lockstep:

1. **OTel span** named `execute_tool` carrying the Gen AI semantic-
   convention attrs the Grafana Cloud AI Obs **Tools** view reads:
   `gen_ai.tool.name`, `gen_ai.agent.name`, `gen_ai.provider.name`.
2. **`agent_tool_calls` row** in Postgres — the durable audit record
   read by Grafana panels and queryable via SQL.
3. **Scope check** via `policy.check_tool_scope` — records a `high`
   violation row if the agent used a tool outside its allow-list.
   Non-blocking: the call still runs (we audit, we don't enforce —
   that decision belongs to a human reviewer).

Usage pattern:

    with track_tool_call(
        agent_name="research_agent",
        tool_name="web_fetch",
        provider_name="http",              # for the Tools view
        target_system="data.sec.gov",      # finer-grained internal label
        action="GET",
        input_summary=url,
    ) as result:
        text = await fetch_one(url)
        result["output"] = f"{len(text)} chars"

Pipeline_id + llm_call_id are auto-pulled from `llm_client`'s
ContextVars when present, so call sites don't have to thread them
manually. Pass explicit values to override for SE-facing tools that
don't run inside a pipeline subprocess.
"""

from __future__ import annotations

import contextlib
import time
from typing import Any, Iterator

import structlog

_logger = structlog.get_logger()


@contextlib.contextmanager
def track_tool_call(
    *,
    agent_name: str,
    tool_name: str,
    provider_name: str = "internal",
    target_system: str | None = None,
    action: str | None = None,
    input_summary: str | None = None,
    pipeline_id: str | None = None,
    llm_call_id: str | None = None,
) -> Iterator[dict[str, Any]]:
    """Run-and-record a tool invocation.

    Yields a dict the caller writes its output summary into:

        with track_tool_call(...) as r:
            value = do_the_thing()
            r["output"] = "n rows" or similar

    On context exit, records duration, success/failure, and the
    summary into agent_tool_calls + emits the matching span.

    If `pipeline_id` / `llm_call_id` are None, we read the current
    pipeline-phase ContextVars from `llm_client` so callers inside a
    pipeline phase get the right linkage for free."""
    # Lazy import — keeps this module safe to import in test contexts
    # without the full pipeline context infrastructure.
    phase: str | None = None
    try:
        from proj_clarion.observability.llm_client import (
            current_phase as _current_phase,
            current_pipeline_id as _current_pid,
        )
        pipeline_id = pipeline_id or _current_pid() or None
        phase = _current_phase() or None
    except Exception:  # noqa: BLE001
        pass

    # Scope check — non-blocking, records a violation row if the
    # agent used a tool outside its allow-list.
    try:
        from proj_clarion.observability.policy import check_tool_scope
        check_tool_scope(
            agent_name=agent_name, tool_name=tool_name,
            pipeline_id=pipeline_id, llm_call_id=llm_call_id,
        )
    except Exception:  # noqa: BLE001
        pass

    # Open the span. Lazy OTel import for the same offline-test reason.
    span_cm = _maybe_span(
        tool_name, agent_name, provider_name,
        target_system, action, pipeline_id, phase,
    )
    started = time.monotonic()
    result_holder: dict[str, Any] = {}
    success = True
    error_msg: str | None = None

    # Sigil tool-execution ingest — separate from the OTel span. The
    # Grafana Cloud AI-Obs **Tools** page reads from Sigil's
    # tool-execution channel (NOT from Tempo spans), so without this
    # the page stays empty even when execute_tool spans are flowing.
    sigil_recorder = _maybe_start_sigil_tool(
        tool_name=tool_name,
        agent_name=agent_name,
        provider_name=provider_name,
        action=action,
        pipeline_id=pipeline_id,
        llm_call_id=llm_call_id,
    )

    with span_cm as span:
        try:
            yield result_holder
        except Exception as exc:
            success = False
            error_msg = f"{type(exc).__name__}: {exc}"
            if span is not None:
                span.set_attribute("clarion.tool.success", False)
                span.set_attribute("clarion.tool.error_type", type(exc).__name__)
                span.record_exception(exc)
            if sigil_recorder is not None:
                try:
                    sigil_recorder.set_exec_error(exc)
                except Exception:  # noqa: BLE001
                    pass
            raise
        finally:
            duration_ms = int((time.monotonic() - started) * 1000)
            if span is not None:
                span.set_attribute("clarion.tool.duration_ms", duration_ms)
                if success:
                    span.set_attribute("clarion.tool.success", True)
            _finalise_sigil_tool(
                sigil_recorder,
                tool_name=tool_name,
                arguments=input_summary,
                output=result_holder.get("output"),
                success=success,
                error_msg=error_msg,
            )
            _persist_tool_call(
                agent_name=agent_name,
                tool_name=tool_name,
                target_system=target_system,
                action=action,
                input_summary=input_summary,
                output_summary=result_holder.get("output"),
                success=success,
                error_msg=error_msg,
                duration_ms=duration_ms,
                pipeline_id=pipeline_id,
                llm_call_id=llm_call_id,
            )


# ─── Internals ──────────────────────────────────────────────────────


@contextlib.contextmanager
def _maybe_span(
    tool_name: str,
    agent_name: str,
    provider_name: str,
    target_system: str | None,
    action: str | None,
    pipeline_id: str | None,
    phase: str | None,
) -> Iterator[Any]:
    """Open an OTel span if OTel is installed, else yield None.

    Span is named `execute_tool` (the literal name the Grafana Cloud
    AI-Obs Tools page filters on) and carries the gen_ai.tool.* +
    gen_ai.agent.name + gen_ai.provider.name attrs Grafana reads.
    """
    try:
        from opentelemetry import trace
        tracer = trace.get_tracer("proj-clarion.tools")
    except ImportError:
        yield None
        return
    with tracer.start_as_current_span("execute_tool") as span:
        # Gen AI semantic-convention attrs Grafana AI Obs Tools view reads.
        span.set_attribute("gen_ai.tool.name", tool_name)
        span.set_attribute("gen_ai.agent.name", agent_name)
        span.set_attribute("gen_ai.provider.name", provider_name)

        # Clarion-internal mirror attrs — kept for back-compat with
        # existing dashboards / SQL queries that pre-date the gen_ai naming.
        span.set_attribute("clarion.tool.name", tool_name)
        span.set_attribute("clarion.agent.name", agent_name)
        if target_system:
            span.set_attribute("clarion.tool.target_system", target_system)
        if action:
            span.set_attribute("clarion.tool.action", action)
        if pipeline_id:
            span.set_attribute("clarion.pipeline.id", pipeline_id)
        if phase:
            span.set_attribute("clarion.pipeline.phase", phase)
        yield span


def _persist_tool_call(
    *,
    agent_name: str,
    tool_name: str,
    target_system: str | None,
    action: str | None,
    input_summary: str | None,
    output_summary: str | None,
    success: bool,
    error_msg: str | None,
    duration_ms: int | None,
    pipeline_id: str | None,
    llm_call_id: str | None,
) -> None:
    """Write one row to agent_tool_calls. Best-effort — DB hiccup must
    never break the wrapped tool call. The OTel span already carries the
    same data so observability isn't lost on persist failure."""
    try:
        from proj_clarion.storage import AgentToolCallRepo, session_scope
        with session_scope() as s:
            AgentToolCallRepo().record(
                s,
                agent_name=agent_name,
                tool_name=tool_name,
                target_system=target_system,
                action=action,
                input_summary=input_summary,
                output_summary=output_summary,
                success=success,
                error_msg=error_msg,
                duration_ms=duration_ms,
                pipeline_id=pipeline_id,
                llm_call_id=llm_call_id,
            )
    except Exception as exc:  # noqa: BLE001
        _logger.debug(
            "tool.persist.skip",
            tool=tool_name,
            error=str(exc)[:200],
        )


# ─── Sigil tool-execution ingest ─────────────────────────────────────
#
# Grafana Sigil's AI-Obs **Tools** page reads from Sigil's tool-execution
# channel — distinct from OTel spans in Tempo. Without these calls the
# Tools page stays empty even when execute_tool spans are flowing.
# Mirrors the sigil_helper pattern used for Generation records.


def _maybe_start_sigil_tool(
    *,
    tool_name: str,
    agent_name: str,
    provider_name: str,
    action: str | None,
    pipeline_id: str | None,
    llm_call_id: str | None,
) -> Any:
    """Open a Sigil ToolExecutionRecorder, or return None when Sigil isn't
    configured / the SDK is unavailable. Best-effort: any failure here
    falls back to OTel-only instrumentation."""
    try:
        from proj_clarion.observability import get_sigil_client
        sigil = get_sigil_client()
        if sigil is None:
            return None
        from sigil_sdk import ToolExecutionStart
    except Exception:  # noqa: BLE001
        return None

    try:
        start = ToolExecutionStart(
            tool_name=tool_name,
            tool_call_id=llm_call_id or "",
            tool_type=action or "",
            agent_name=agent_name,
            request_provider=provider_name,
            # Pipeline id doubles as a conversation key so a single
            # build's tools group in the Sigil UI alongside its LLM
            # calls (Generation conversation_id uses the same value).
            conversation_id=pipeline_id or "",
        )
        return sigil.start_tool_execution(start)
    except Exception as exc:  # noqa: BLE001
        _logger.debug("sigil.tool.start.skip", tool=tool_name, error=str(exc)[:200])
        return None


def _finalise_sigil_tool(
    recorder: Any,
    *,
    tool_name: str,
    arguments: str | None,
    output: str | None,
    success: bool,
    error_msg: str | None,
) -> None:
    """Close out a Sigil ToolExecutionRecorder. Errors raised during
    recorder.set_result / end() are swallowed — the wrapped tool call
    already succeeded (or failed cleanly) from the caller's view."""
    if recorder is None:
        return
    try:
        from sigil_sdk import ToolExecutionEnd, ToolResult
        result = ToolResult(
            tool_call_id="",
            name=tool_name,
            content=(output or "") if success else (error_msg or ""),
            is_error=not success,
        )
        recorder.set_result(ToolExecutionEnd(
            arguments=arguments or "",
            result=result,
        ))
    except Exception as exc:  # noqa: BLE001
        _logger.debug("sigil.tool.result.skip", tool=tool_name, error=str(exc)[:200])
    try:
        recorder.end()
        err = recorder.err()
        if err is not None:
            _logger.debug("sigil.tool.recorder.err", tool=tool_name, error=str(err)[:200])
    except Exception as exc:  # noqa: BLE001
        _logger.debug("sigil.tool.end.skip", tool=tool_name, error=str(exc)[:200])
