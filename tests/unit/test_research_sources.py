"""Unit tests for the per-source research toggles (UI/CLI/API feature).

Covers the source-key single-source-of-truth and the disabled→enabled
translation. The empty-set short-circuit in gather_external_signals is
verified offline (it returns before any network discovery)."""

from __future__ import annotations

import pytest

from proj_clarion.agents.external_sources import ALL_SOURCE_TYPES, gather_external_signals
from proj_clarion.agents.external_sources.constants import (
    RESEARCH_SOURCE_KEYS,
    RESEARCH_SOURCE_LABELS,
)
from proj_clarion.cli.main import _resolve_enabled_sources


def test_source_keys_single_source_of_truth() -> None:
    # ALL_SOURCE_TYPES re-exports the canonical constant, and every key has
    # a display label for the UI/CLI.
    assert tuple(ALL_SOURCE_TYPES) == RESEARCH_SOURCE_KEYS
    assert set(RESEARCH_SOURCE_LABELS) == set(RESEARCH_SOURCE_KEYS)


def test_api_sourcename_literal_in_sync() -> None:
    # The API request model duplicates the keys as a Literal (pydantic needs
    # a static type); guard against drift from the canonical list.
    from typing import get_args

    from proj_clarion.api.routes.pipelines import SourceName

    assert set(get_args(SourceName)) == set(RESEARCH_SOURCE_KEYS)


def test_resolve_enabled_sources_none_when_no_flags() -> None:
    # No --disable-source flags → None, i.e. "all sources" (historical default).
    assert _resolve_enabled_sources(()) is None


def test_resolve_enabled_sources_subtracts_disabled() -> None:
    enabled = _resolve_enabled_sources(("edgar_10k", "wikidata"))
    assert enabled == set(RESEARCH_SOURCE_KEYS) - {"edgar_10k", "wikidata"}


def test_resolve_enabled_sources_all_disabled_is_empty_set() -> None:
    # Disabling every source yields an empty set (NOT None) — distinct from
    # the "all enabled" default, so the agent skips external enrichment.
    enabled = _resolve_enabled_sources(tuple(RESEARCH_SOURCE_KEYS))
    assert enabled == set()


@pytest.mark.asyncio
async def test_gather_external_signals_empty_set_short_circuits() -> None:
    # An empty enabled set means "no external enrichment" and must return
    # without attempting any network discovery.
    out = await gather_external_signals("https://example.com", enabled_sources=set())
    assert out == []
