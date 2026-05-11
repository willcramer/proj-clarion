"""Thin wrapper around `anthropic_client.messages.create` that emits a Sigil
Generation when a Sigil client is configured, and falls back to a direct
provider call otherwise (so unit tests stay offline).

The official `sigil_sdk_anthropic.messages.create()` helper does not expose
`parent_generation_ids` via `AnthropicOptions`, but multi-agent dependency
tracking is exactly what we need for the Plan agent's six-phase DAG. So we
build the GenerationStart ourselves (re-using the helper's request/response
mappers) and inject parents + capture the resulting generation_id for chaining.
"""

from __future__ import annotations

import uuid
from typing import Any

from proj_clarion.observability import get_sigil_client


def call_anthropic(
    anthropic_client: Any,
    request: dict[str, Any],
    *,
    agent_name: str,
    parent_generation_ids: list[str] | None = None,
    conversation_id: str = "",
    tags: dict[str, str] | None = None,
) -> tuple[Any, str]:
    """Invoke `anthropic_client.messages.create(**request)` with Sigil tracking.

    Returns (anthropic_response, generation_id). The generation_id is the empty
    string when Sigil is not configured — callers chaining downstream phases
    should treat the empty string as "no parent to attach".
    """
    sigil = get_sigil_client()

    def _provider_call() -> Any:
        return anthropic_client.messages.create(**request)

    if sigil is None:
        return _provider_call(), ""

    return _call_with_sigil(
        sigil, anthropic_client, request, _provider_call,
        agent_name=agent_name,
        parent_generation_ids=parent_generation_ids or [],
        conversation_id=conversation_id,
        tags=tags or {},
    )


def _call_with_sigil(
    sigil: Any,
    anthropic_client: Any,
    request: dict[str, Any],
    provider_call: Any,
    *,
    agent_name: str,
    parent_generation_ids: list[str],
    conversation_id: str,
    tags: dict[str, str],
) -> tuple[Any, str]:
    """Sigil-instrumented path. Builds the GenerationStart from the Anthropic
    helper's mappers, injects parent IDs + a pre-generated generation_id,
    runs the provider, and reports the mapped result back to Sigil.
    """
    from sigil_sdk import GenerationMode
    from sigil_sdk_anthropic import AnthropicOptions
    # The provider helper re-exports the internal mappers we need
    from sigil_sdk_anthropic.provider import (
        _messages_from_request_response,
        _start_payload,
    )

    options = AnthropicOptions(
        agent_name=agent_name,
        agent_version="0.1.0",
        conversation_id=conversation_id,
        tags=tags,
    )
    start = _start_payload(request, options, GenerationMode.SYNC)

    # Pin a generation_id so we can return it for downstream chaining.
    generation_id = f"gen-{uuid.uuid4().hex}"
    start.id = generation_id
    if parent_generation_ids:
        start.parent_generation_ids = list(parent_generation_ids)

    recorder = sigil.start_generation(start)
    try:
        response = provider_call()
        recorder.set_result(_messages_from_request_response(request, response, options))
    except Exception as exc:
        recorder.set_call_error(exc)
        recorder.end()
        raise
    recorder.end()
    if recorder.err() is not None:
        # SDK validation/enqueue errors are otherwise silent — surface them
        raise recorder.err()
    return response, generation_id
