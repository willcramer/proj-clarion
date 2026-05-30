"""Unified Anthropic client wrapper with Gen AI OTel semantic conventions,
cost tracking, and pipeline-phase context propagation.

Every Anthropic call in proj-clarion goes through this module — both
non-streaming (planner, research, profiles.extend) and streaming
(agents.extend/refine). It is the single point at which we:

- Open an OTel span named `gen_ai.chat {model}` carrying the spec'd
  `gen_ai.*` attributes (model, tokens, finish_reason, cached_tokens)
  AND Clarion-specific enrichment (pipeline phase, prompt template,
  prompt version, cost, context utilization).
- For streaming calls, record TTFT (time to first text delta).
- Compute per-call cost from MODEL_PRICES.

OpenLIT auto-instrumentation (initialised in observability/__init__.py)
ALSO emits gen_ai.* spans around each call. The duplication is intentional
and cheap — OpenLIT's span is a transport-level view, ours is an
agent-level view that carries the Clarion context attrs OpenLIT can't see.
The trace ID joins them so neither view is lost.

# Pipeline-phase propagation

Phases run as CLI subprocesses (see api/pipeline.py). The orchestrator
injects `CLARION_PIPELINE_ID` and `CLARION_PIPELINE_PHASE` into the
subprocess env. This module reads them once at import time as the
default for the ContextVars. Code inside a phase can override per-call
with the `pipeline_context()` context manager (used for finer-grained
prompt-template tracking inside a phase, e.g. plan's six sub-calls).
"""

from __future__ import annotations

import contextlib
import contextvars
import os
import time
import uuid
from collections.abc import Iterator
from typing import Any

import structlog

from proj_clarion.observability import get_sigil_client

_logger = structlog.get_logger()


# ─── Pricing tables ──────────────────────────────────────────────────
#
# Per-million-token USD prices. Update from anthropic.com/pricing.
# The lookup is best-effort — unknown model names log a warning and
# get $0 cost rather than raising, so a new model can ship before
# the table is updated without breaking the pipeline.
MODEL_PRICES: dict[str, dict[str, float]] = {
    "claude-opus-4-7": {
        "input":       15.00 / 1_000_000,
        "output":      75.00 / 1_000_000,
        "cache_write": 18.75 / 1_000_000,
        "cache_read":   1.50 / 1_000_000,
    },
    "claude-haiku-4-5": {
        "input":        1.00 / 1_000_000,
        "output":       5.00 / 1_000_000,
        "cache_write":  1.25 / 1_000_000,
        "cache_read":   0.10 / 1_000_000,
    },
    # Legacy entries kept for users still pinning ANTHROPIC_MODEL to a 3.x id
    "claude-3-5-sonnet-20241022": {
        "input":       3.00 / 1_000_000,
        "output":     15.00 / 1_000_000,
        "cache_write": 3.75 / 1_000_000,
        "cache_read":  0.30 / 1_000_000,
    },
    "claude-3-haiku-20240307": {
        "input":       0.25 / 1_000_000,
        "output":      1.25 / 1_000_000,
        "cache_write": 0.30 / 1_000_000,
        "cache_read":  0.03 / 1_000_000,
    },
}

# 200k is the floor for every shipping Claude model as of 2026; updated
# entries can override.
MODEL_CONTEXT_LIMITS: dict[str, int] = {
    "claude-opus-4-7":              200_000,
    "claude-haiku-4-5":             200_000,
    "claude-3-5-sonnet-20241022":   200_000,
    "claude-3-haiku-20240307":      200_000,
}


# ─── Pipeline context (ContextVars + env-var defaults) ───────────────
#
# Subprocesses inherit the parent orchestrator's phase via env vars.
# The ContextVar default is read at import time so any module that
# imports llm_client picks up the right phase without explicit setup.
_pipeline_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "clarion_pipeline_id", default=os.getenv("CLARION_PIPELINE_ID", ""),
)
_pipeline_phase_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "clarion_pipeline_phase", default=os.getenv("CLARION_PIPELINE_PHASE", ""),
)
_prompt_template_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "clarion_prompt_template", default="",
)
_prompt_version_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "clarion_prompt_version", default=os.getenv("CLARION_PROMPT_VERSION", ""),
)
# Assistant conversation id. When set, the ConversationSpanProcessor stamps
# gen_ai.conversation.id on every span started inside the block — which is
# how Grafana AI-Obs groups generation spans (ours AND OpenLIT's) into a
# conversation in the Conversations view.
_conversation_id_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "clarion_conversation_id", default=os.getenv("CLARION_CONVERSATION_ID", ""),
)


@contextlib.contextmanager
def pipeline_context(
    *,
    pipeline_id: str | None = None,
    phase: str | None = None,
    prompt_template: str | None = None,
    prompt_version: str | None = None,
    conversation_id: str | None = None,
) -> Iterator[None]:
    """Set Clarion pipeline context for the duration of the `with` block.

    Any LLM calls made inside the block will tag their span with these
    attributes. Nested calls override outer ones; nothing leaks out.
    """
    tokens: list[Any] = []
    if pipeline_id is not None:
        tokens.append(_pipeline_id_var.set(pipeline_id))
    if phase is not None:
        tokens.append(_pipeline_phase_var.set(phase))
    if prompt_template is not None:
        tokens.append(_prompt_template_var.set(prompt_template))
    if prompt_version is not None:
        tokens.append(_prompt_version_var.set(prompt_version))
    if conversation_id is not None:
        tokens.append(_conversation_id_var.set(conversation_id))
    try:
        yield
    finally:
        for tok in reversed(tokens):
            try:
                tok.var.reset(tok)
            except ValueError:
                pass


def current_phase() -> str:
    return _pipeline_phase_var.get()


def current_pipeline_id() -> str:
    return _pipeline_id_var.get()


# ─── Cost computation ────────────────────────────────────────────────


def compute_cost(
    model: str,
    *,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
) -> tuple[float, float]:
    """Return (total_cost_usd, cache_savings_usd) for a single call.

    `cache_savings_usd` is the delta vs. paying full input price for
    cached tokens — a positive value means caching helped.
    """
    prices = MODEL_PRICES.get(model)
    if prices is None:
        _logger.warning("llm.cost.unknown_model", model=model)
        return 0.0, 0.0
    input_cost      = input_tokens       * prices["input"]
    output_cost     = output_tokens      * prices["output"]
    cache_read_cost = cache_read_tokens  * prices["cache_read"]
    cache_write_cost = cache_write_tokens * prices["cache_write"]
    total = input_cost + output_cost + cache_read_cost + cache_write_cost
    cache_savings = cache_read_tokens * (prices["input"] - prices["cache_read"])
    return round(total, 6), round(cache_savings, 6)


# ─── Span helpers ────────────────────────────────────────────────────


def _get_tracer() -> Any:
    """Return the OTel tracer, or None if OTel isn't installed. Lazy import
    so this module is safe to import in test environments without OTel."""
    try:
        from opentelemetry import trace
    except ImportError:
        return None
    return trace.get_tracer("proj-clarion.llm")


def _stamp_request_attrs(span: Any, model: str, request: dict[str, Any]) -> None:
    span.set_attribute("gen_ai.system", "anthropic")
    span.set_attribute("gen_ai.operation.name", "chat")
    span.set_attribute("gen_ai.request.model", model)
    max_tokens = request.get("max_tokens")
    if max_tokens is not None:
        span.set_attribute("gen_ai.request.max_tokens", int(max_tokens))
    # Deployment stage as a span attr too — Resource attrs are already
    # set by clarion_resource(), but span attrs are what some TraceQL
    # filters expect (`span.deployment.environment`). Cheap, defensive.
    try:
        from proj_clarion.observability.otlp import clarion_environment
        span.set_attribute("deployment.environment", clarion_environment())
    except Exception:  # noqa: BLE001
        pass
    # Clarion context — read fresh from ContextVars on every call so
    # nested pipeline_context() blocks take effect.
    phase = _pipeline_phase_var.get()
    if phase:
        span.set_attribute("clarion.pipeline.phase", phase)
    pid = _pipeline_id_var.get()
    if pid:
        span.set_attribute("clarion.pipeline.id", pid)
    template = _prompt_template_var.get()
    if template:
        span.set_attribute("clarion.prompt.template", template)
    version = _prompt_version_var.get()
    if version:
        span.set_attribute("clarion.prompt.version", version)


def _stamp_usage_attrs(
    span: Any,
    model: str,
    *,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int,
    cache_write_tokens: int,
    stop_reason: str | None,
) -> tuple[float, float]:
    span.set_attribute("gen_ai.usage.input_tokens", int(input_tokens))
    span.set_attribute("gen_ai.usage.output_tokens", int(output_tokens))
    if cache_read_tokens:
        span.set_attribute("gen_ai.usage.cached_input_tokens", int(cache_read_tokens))
    if cache_write_tokens:
        span.set_attribute("gen_ai.usage.cache_write_input_tokens", int(cache_write_tokens))
    if stop_reason:
        span.set_attribute("gen_ai.response.finish_reason", stop_reason)
    total_cost, cache_savings = compute_cost(
        model,
        input_tokens=input_tokens, output_tokens=output_tokens,
        cache_read_tokens=cache_read_tokens, cache_write_tokens=cache_write_tokens,
    )
    span.set_attribute("clarion.llm.cost_usd", total_cost)
    if cache_savings > 0:
        span.set_attribute("clarion.llm.cache_savings_usd", cache_savings)
    # Context window utilization — total tokens vs. model limit.
    limit = MODEL_CONTEXT_LIMITS.get(model, 200_000)
    util_pct = round((input_tokens + output_tokens) / limit * 100, 1)
    span.set_attribute("clarion.context.utilization_pct", util_pct)
    return total_cost, cache_savings


def _extract_usage(response: Any) -> tuple[int, int, int, int, str | None]:
    """Pull (input, output, cache_read, cache_write, stop_reason) out of an
    Anthropic response object. Returns zeros + None when fields are absent
    (e.g. mocked response in tests)."""
    usage = getattr(response, "usage", None)
    if usage is None:
        return 0, 0, 0, 0, getattr(response, "stop_reason", None)
    return (
        int(getattr(usage, "input_tokens", 0) or 0),
        int(getattr(usage, "output_tokens", 0) or 0),
        int(getattr(usage, "cache_read_input_tokens", 0) or 0),
        int(getattr(usage, "cache_creation_input_tokens", 0) or 0),
        getattr(response, "stop_reason", None),
    )


# ─── Public API: non-streaming ───────────────────────────────────────


def call_anthropic(
    anthropic_client: Any,
    request: dict[str, Any],
    *,
    agent_name: str,
    prompt_template: str | None = None,
    prompt_version: str | None = None,
    parent_generation_ids: list[str] | None = None,
    conversation_id: str = "",
    tags: dict[str, str] | None = None,
) -> tuple[Any, str]:
    """Invoke an Anthropic chat completion with full instrumentation.
    Returns (response, sigil_generation_id) — same shape callers had with
    the older non-streaming impl.

    Internally we now use `anthropic_client.messages.stream()` so we can
    record TTFT (time to first text token) on every call. The final
    response is reassembled via `stream.get_final_message()`; cost,
    token-usage, cache-read/write counts, and stop_reason are extracted
    identically to before. From the call-site's perspective nothing
    changed except the new `ttft_ms` value flowing to spans + llm_calls.

    `prompt_template` / `prompt_version` override the ContextVar values
    for this call only — useful when one phase makes several calls with
    different templates (e.g. planner's analyze/model_processes/build_kg).

    Sigil integration: the sigil-sdk-anthropic helper assumes a
    non-streaming `messages.create` response, so we feed it a synthesized
    one shaped from the final reassembled message. Trace ID joins our
    span to OpenLIT's auto-instrumented stream span; no caller-facing
    change there either.
    """
    model = str(request.get("model", "unknown"))
    tracer = _get_tracer()

    if tracer is None:
        # OTel not installed — preserve the simple non-streaming path so
        # tests + offline environments aren't forced through streaming.
        return _invoke_with_sigil_nonstream(
            anthropic_client, request,
            agent_name=agent_name,
            parent_generation_ids=parent_generation_ids,
            conversation_id=conversation_id,
            tags=tags,
        )

    call_id = new_call_id()
    with tracer.start_as_current_span(f"gen_ai.chat {model}") as span:
        with pipeline_context(
            prompt_template=prompt_template, prompt_version=prompt_version,
        ):
            _stamp_request_attrs(span, model, request)
            span.set_attribute("clarion.llm.call_id", call_id)
            # Even though we're streaming internally, callers see the
            # same final-response shape — keep gen_ai.request.streaming
            # truthful since the wire transport IS streaming.
            span.set_attribute("gen_ai.request.streaming", True)
            attempt = 1
            span.set_attribute("gen_ai.request.attempt", attempt)
            started = time.monotonic()
            first_token_ms: int | None = None
            try:
                with anthropic_client.messages.stream(**request) as stream:
                    # Drive iteration ourselves so we can timestamp the
                    # first text delta. We don't yield anything to the
                    # caller — they get the assembled message at the end.
                    for chunk in stream.text_stream:
                        if first_token_ms is None and chunk:
                            first_token_ms = int((time.monotonic() - started) * 1000)
                            span.set_attribute("gen_ai.ttft_ms", first_token_ms)
                            span.set_attribute("clarion.llm.ttft_ms", first_token_ms)
                    response = stream.get_final_message()
            except Exception as exc:
                err_type = _classify_error(exc)
                span.set_attribute("gen_ai.error.type", err_type)
                span.record_exception(exc)
                _persist_call(
                    call_id=call_id, model=model, agent_name=agent_name,
                    sigil_generation_id=None,
                    input_tokens=0, output_tokens=0,
                    cache_read_tokens=0, cache_write_tokens=0,
                    stop_reason=None, cost_usd=0.0, cache_savings_usd=0.0,
                    ttft_ms=first_token_ms, attempt=attempt, error_type=err_type,
                    prompt_template_override=prompt_template,
                    prompt_version_override=prompt_version,
                    is_stream=True,
                )
                raise

            # Inform Sigil with a synthesized non-stream view so Generation
            # records keep tracking parent chains across the multi-phase DAG.
            gen_id = _report_to_sigil(
                response=response, request=request,
                agent_name=agent_name,
                parent_generation_ids=parent_generation_ids,
                conversation_id=conversation_id,
                tags=tags,
            )

            inp, out, cache_r, cache_w, stop = _extract_usage(response)
            cost, savings = _stamp_usage_attrs(
                span, model,
                input_tokens=inp, output_tokens=out,
                cache_read_tokens=cache_r, cache_write_tokens=cache_w,
                stop_reason=stop,
            )
            _persist_call(
                call_id=call_id, model=model, agent_name=agent_name,
                sigil_generation_id=gen_id or None,
                input_tokens=inp, output_tokens=out,
                cache_read_tokens=cache_r, cache_write_tokens=cache_w,
                stop_reason=stop, cost_usd=cost, cache_savings_usd=savings,
                ttft_ms=first_token_ms, attempt=attempt, error_type=None,
                prompt_template_override=prompt_template,
                prompt_version_override=prompt_version,
                is_stream=True,
            )
            return response, gen_id


def _invoke_with_sigil_nonstream(
    anthropic_client: Any,
    request: dict[str, Any],
    *,
    agent_name: str,
    parent_generation_ids: list[str] | None,
    conversation_id: str,
    tags: dict[str, str] | None,
) -> tuple[Any, str]:
    """Fallback path for environments without OTel installed: delegates to
    sigil_helper's non-streaming wrapper. Kept around so test runs that
    monkeypatch the Anthropic client (and don't expect streaming)
    continue to work."""
    from proj_clarion.observability.sigil_helper import call_anthropic as _sigil_call
    return _sigil_call(
        anthropic_client, request,
        agent_name=agent_name,
        parent_generation_ids=parent_generation_ids,
        conversation_id=conversation_id,
        tags=tags,
    )


def _report_to_sigil(
    *,
    response: Any,
    request: dict[str, Any],
    agent_name: str,
    parent_generation_ids: list[str] | None,
    conversation_id: str,
    tags: dict[str, str] | None,
) -> str:
    """Record a Sigil Generation for a stream-completed call.

    sigil-sdk-anthropic is shaped for non-streaming `messages.create` —
    it doesn't have a streaming receive path. We synthesise the same
    record by running the helper's mappers against the reassembled
    final message. If Sigil isn't configured, returns the empty string
    (matches the existing call_anthropic contract)."""
    sigil = get_sigil_client()
    if sigil is None:
        return ""
    try:
        from sigil_sdk import GenerationMode
        from sigil_sdk_anthropic import AnthropicOptions
        from sigil_sdk_anthropic.provider import (
            _messages_from_request_response,
            _start_payload,
        )

        options = AnthropicOptions(
            agent_name=agent_name,
            agent_version="0.1.0",
            conversation_id=conversation_id,
            tags=tags or {},
        )
        start = _start_payload(request, options, GenerationMode.SYNC)
        generation_id = f"gen-{uuid.uuid4().hex}"
        start.id = generation_id
        if parent_generation_ids:
            start.parent_generation_ids = list(parent_generation_ids)

        recorder = sigil.start_generation(start)
        try:
            recorder.set_result(_messages_from_request_response(request, response, options))
        except Exception as exc:  # noqa: BLE001
            recorder.set_call_error(exc)
            recorder.end()
            return generation_id
        recorder.end()
        if recorder.err() is not None:
            _logger.warning("sigil.report.failed", error=str(recorder.err()))
        return generation_id
    except Exception as exc:  # noqa: BLE001
        # Don't let Sigil reporting break the LLM call path.
        _logger.debug("sigil.report.skip", error=str(exc)[:200])
        return ""


# ─── Public API: streaming (with TTFT) ───────────────────────────────


@contextlib.contextmanager
def stream_anthropic(
    anthropic_client: Any,
    request: dict[str, Any],
    *,
    agent_name: str,
    prompt_template: str | None = None,
    prompt_version: str | None = None,
    conversation_id: str = "",
) -> Iterator[Any]:
    """Wrap `anthropic_client.messages.stream(**request)` with span + TTFT.

    Yields the Anthropic stream object, augmented with a `text_stream`
    iterator that records first-byte timing. Sigil is intentionally not
    invoked on streaming calls — sigil_helper doesn't support streaming,
    and the SE-facing extend/refine flows are exploratory rather than
    pipeline artefacts.

    Usage:
        with stream_anthropic(client, request, agent_name="...") as stream:
            for chunk in stream.text_stream:
                yield chunk
    """
    model = str(request.get("model", "unknown"))
    tracer = _get_tracer()

    # No OTel installed — pass through.
    if tracer is None:
        with anthropic_client.messages.stream(**request) as stream:
            yield stream
        return

    call_id = new_call_id()
    with tracer.start_as_current_span(f"gen_ai.chat {model}") as span:
        with pipeline_context(
            prompt_template=prompt_template, prompt_version=prompt_version,
            conversation_id=conversation_id or None,
        ):
            _stamp_request_attrs(span, model, request)
            span.set_attribute("gen_ai.request.streaming", True)
            span.set_attribute("clarion.llm.call_id", call_id)
            # Grafana AI-Obs groups the Conversations view by this attr on
            # the generation span. Set it here for our span; the
            # ConversationSpanProcessor covers OpenLIT's child span too.
            if conversation_id:
                span.set_attribute("gen_ai.conversation.id", conversation_id)
            started = time.monotonic()
            first_token_ms: int | None = None
            first_token_seen = False
            persisted = False

            try:
                with anthropic_client.messages.stream(**request) as stream:
                    original_stream = stream.text_stream

                    def _timed_text_stream() -> Iterator[str]:
                        nonlocal first_token_seen, first_token_ms
                        for chunk in original_stream:
                            if not first_token_seen and chunk:
                                first_token_ms = int((time.monotonic() - started) * 1000)
                                span.set_attribute("gen_ai.ttft_ms", first_token_ms)
                                first_token_seen = True
                            yield chunk

                    # Monkey-patch so callers can keep iterating
                    # `stream.text_stream` without code change.
                    stream.text_stream = _timed_text_stream()  # type: ignore[assignment]
                    yield stream
                    # After consumer exits, get_final_message() returns
                    # the assembled message with usage.
                    try:
                        final = stream.get_final_message()
                        inp, out, cache_r, cache_w, stop = _extract_usage(final)
                        cost, savings = _stamp_usage_attrs(
                            span, model,
                            input_tokens=inp, output_tokens=out,
                            cache_read_tokens=cache_r, cache_write_tokens=cache_w,
                            stop_reason=stop,
                        )
                        _persist_call(
                            call_id=call_id, model=model, agent_name=agent_name,
                            sigil_generation_id=None,
                            input_tokens=inp, output_tokens=out,
                            cache_read_tokens=cache_r, cache_write_tokens=cache_w,
                            stop_reason=stop, cost_usd=cost, cache_savings_usd=savings,
                            ttft_ms=first_token_ms, attempt=1, error_type=None,
                            prompt_template_override=prompt_template,
                            prompt_version_override=prompt_version,
                            is_stream=True,
                        )
                        persisted = True
                    except Exception as exc:  # noqa: BLE001
                        # Some Anthropic SDK versions don't expose final
                        # message after iter completes — that's OK, the
                        # span already has request-side attrs.
                        _logger.debug("llm.stream.final_unavailable", error=str(exc))
                # Still emit a row even when the final message wasn't
                # available — captures TTFT and the fact the call happened.
                if not persisted:
                    _persist_call(
                        call_id=call_id, model=model, agent_name=agent_name,
                        sigil_generation_id=None,
                        input_tokens=0, output_tokens=0,
                        cache_read_tokens=0, cache_write_tokens=0,
                        stop_reason=None, cost_usd=0.0, cache_savings_usd=0.0,
                        ttft_ms=first_token_ms, attempt=1, error_type=None,
                        prompt_template_override=prompt_template,
                        prompt_version_override=prompt_version,
                    )
            except Exception as exc:
                err_type = _classify_error(exc)
                span.set_attribute("gen_ai.error.type", err_type)
                span.record_exception(exc)
                if not persisted:
                    _persist_call(
                        call_id=call_id, model=model, agent_name=agent_name,
                        sigil_generation_id=None,
                        input_tokens=0, output_tokens=0,
                        cache_read_tokens=0, cache_write_tokens=0,
                        stop_reason=None, cost_usd=0.0, cache_savings_usd=0.0,
                        ttft_ms=first_token_ms, attempt=1, error_type=err_type,
                        prompt_template_override=prompt_template,
                        prompt_version_override=prompt_version,
                        is_stream=True,
                    )
                raise


def _persist_call(
    *,
    call_id: str,
    model: str,
    agent_name: str,
    sigil_generation_id: str | None,
    input_tokens: int,
    output_tokens: int,
    cache_read_tokens: int,
    cache_write_tokens: int,
    stop_reason: str | None,
    cost_usd: float,
    cache_savings_usd: float,
    ttft_ms: int | None,
    attempt: int,
    error_type: str | None,
    prompt_template_override: str | None,
    prompt_version_override: str | None,
    is_stream: bool = False,
) -> None:
    """Write one row to `llm_calls`. Best-effort: a DB failure here must
    not break the LLM call path. The migration may not have been applied
    in dev environments, so we swallow OperationalError too.

    Reads `pipeline_id` / `phase` from ContextVars (env-derived) so
    subprocess phases inherit them without explicit plumbing.

    After persisting, runs `check_llm_call_anomalies()` which surfaces
    cost spikes / runaway-output / excessive-retry rows into
    `agent_policy_violations`. The detector function silently no-ops
    when the migration hasn't been applied yet, so this is safe to call
    even in environments running on older schemas."""
    try:
        from proj_clarion.storage import LlmCallRepo, session_scope

        phase = _pipeline_phase_var.get() or None
        pipeline_id = _pipeline_id_var.get() or None
        template = prompt_template_override or _prompt_template_var.get() or None
        version = prompt_version_override or _prompt_version_var.get() or None

        with session_scope() as s:
            LlmCallRepo().record(
                s,
                call_id=call_id,
                pipeline_id=pipeline_id,
                phase=phase,
                prompt_template=template,
                prompt_version=version,
                model=model,
                agent_name=agent_name,
                sigil_generation_id=sigil_generation_id,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                cache_read_tokens=cache_read_tokens,
                cache_write_tokens=cache_write_tokens,
                stop_reason=stop_reason,
                cost_usd=cost_usd,
                cache_savings_usd=cache_savings_usd,
                ttft_ms=ttft_ms,
                attempt=attempt,
                error_type=error_type,
                is_stream=is_stream,
            )
    except Exception as exc:  # noqa: BLE001
        # Migration not yet applied, DB unreachable, etc. — log and move on.
        # The OTel span already carries the same data.
        _logger.debug("llm.persist.skip", call_id=call_id, error=str(exc)[:200])
        return

    # Policy-violation auto-detection. Lives behind a separate try so a
    # missing 0008 migration doesn't take out the call persist.
    try:
        from proj_clarion.observability.policy import check_llm_call_anomalies
        check_llm_call_anomalies(
            pipeline_id=_pipeline_id_var.get() or None,
            llm_call_id=call_id,
            agent_name=agent_name,
            cost_usd=cost_usd,
            output_tokens=output_tokens,
            attempt=attempt,
        )
    except Exception as exc:  # noqa: BLE001
        _logger.debug("policy.check.skip", call_id=call_id, error=str(exc)[:200])


def _classify_error(exc: Exception) -> str:
    """Coarse error bucket for the gen_ai.error.type attr. We deliberately
    match on class name + message rather than importing the Anthropic
    exception hierarchy — the SDK reshuffles it occasionally."""
    name = type(exc).__name__.lower()
    msg = str(exc).lower()
    if "ratelimit" in name or "rate_limit" in msg or "429" in msg:
        return "rate_limit"
    if "timeout" in name or "timeout" in msg:
        return "timeout"
    if "context" in msg and "length" in msg:
        return "context_length"
    if "auth" in name or "401" in msg or "403" in msg:
        return "auth"
    if "server" in name or "500" in msg or "503" in msg:
        return "server_error"
    return "unknown"


# ─── Misc helpers ────────────────────────────────────────────────────


def new_call_id() -> str:
    """Fresh opaque id for cross-referencing a span with a DB row in
    llm_calls. Same shape as Sigil's `gen-<hex>` so they sort together."""
    return f"call-{uuid.uuid4().hex}"
