"""External-source enrichment for the Research agent.

Why this package exists: the v0.1 research agent had one move — open the
prospect's URL and synthesize. That left empty fields on every company
whose marketing pages don't explicitly state "we use Kubernetes". The
v0.2 enrichment hits 5 free public sources in parallel (SEC EDGAR for
public co's, Greenhouse + Lever job boards for active-initiative signal,
GitHub org for tech-stack ground truth, Wikidata for structured metadata)
and runs a cheap haiku extraction on each before handing the synthesis
to opus.

Net effect: fewer empty fields, no retries, ~$0.01 added per research
call (well within the cost-positive package the PR 2 prompt-caching
savings cover).

Public surface:
    gather_external_signals(target_url, company_name=None) -> list[ExternalSourceSummary]
"""

from proj_clarion.agents.external_sources.orchestrator import (
    ALL_SOURCE_TYPES,
    ExternalSourceSummary,
    gather_external_signals,
)

__all__ = [
    "ALL_SOURCE_TYPES",
    "ExternalSourceSummary",
    "gather_external_signals",
]
