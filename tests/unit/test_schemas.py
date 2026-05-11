"""Schema validation tests.

These prove the schemas are coherent and that we can round-trip a real example.
The AcmeRetail fixture is hand-curated — it represents what a successful Research
agent run should produce. If it stops validating, the schema changed in a way
that needs review.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from proj_clarion.schemas import (
    CompanyProfile,
    KGEdge,
    KGNode,
    KnowledgeGraph,
    NodeType,
)

FIXTURES = Path(__file__).parent.parent / "fixtures"


class TestCompanyProfile:
    def test_acme_retail_fixture_validates(self) -> None:
        raw = json.loads((FIXTURES / "acme_retail_profile.json").read_text())
        profile = CompanyProfile.model_validate(raw)
        assert profile.company.name == "AcmeRetail"
        assert profile.industry_taxonomy.business_model.value == "omnichannel_retail"

    def test_round_trip_is_lossless(self) -> None:
        raw = json.loads((FIXTURES / "acme_retail_profile.json").read_text())
        profile = CompanyProfile.model_validate(raw)
        regenerated = json.loads(profile.model_dump_json())
        for key in ("profile_id", "schema_version"):
            assert regenerated[key] == raw[key]
        assert len(regenerated["channels"]) == len(raw["channels"])
        assert len(regenerated["provenance"]) == len(raw["provenance"])

    def test_every_citation_resolves(self) -> None:
        raw = json.loads((FIXTURES / "acme_retail_profile.json").read_text())
        profile = CompanyProfile.model_validate(raw)
        valid_ids = {p.citation_id for p in profile.provenance}

        def check(label: str, citations: list[str]) -> None:
            for c in citations:
                assert c in valid_ids, f"{label}: dangling citation {c}"

        check("company", profile.company.citations)
        check("industry", profile.industry_taxonomy.citations)
        check("revenue", profile.revenue_signals.citations)
        for ch in profile.channels:
            check(f"channel:{ch.channel_id}", ch.citations)
        for ts in profile.tech_stack_signals:
            check(f"tech:{ts.vendor_or_product}", ts.citations)
        for ps in profile.pain_signals:
            check(f"pain:{ps.pain[:30]}", ps.citations)

    def test_duplicate_provenance_id_rejected(self) -> None:
        raw = json.loads((FIXTURES / "acme_retail_profile.json").read_text())
        raw["provenance"].append(raw["provenance"][0])
        with pytest.raises(ValidationError, match="duplicate citation_id"):
            CompanyProfile.model_validate(raw)

    def test_extra_field_rejected(self) -> None:
        raw = json.loads((FIXTURES / "acme_retail_profile.json").read_text())
        raw["something_unexpected"] = "boo"
        with pytest.raises(ValidationError):
            CompanyProfile.model_validate(raw)


class TestKnowledgeGraph:
    def test_referential_integrity_passes_for_consistent_graph(self) -> None:
        kg = KnowledgeGraph(
            nodes=[
                KGNode(node_id="store-hq-city", node_type=NodeType.BUSINESS_ENTITY,
                       business_subtype="store", label="Store NA-1"),
                KGNode(node_id="svc-pos", node_type=NodeType.TECHNICAL_RESOURCE,
                       technical_subtype="service", label="pos-svc"),
            ],
            edges=[
                KGEdge(edge_id="edge-001", edge_type="runs_on",  # type: ignore[arg-type]
                       from_node_id="store-hq-city", to_node_id="svc-pos"),
            ],
        )
        assert kg.validate_referential_integrity() == []

    def test_dangling_edge_detected(self) -> None:
        kg = KnowledgeGraph(
            nodes=[
                KGNode(node_id="store-hq-city", node_type=NodeType.BUSINESS_ENTITY,
                       business_subtype="store", label="Store NA-1"),
            ],
            edges=[
                KGEdge(edge_id="edge-001", edge_type="runs_on",  # type: ignore[arg-type]
                       from_node_id="store-hq-city", to_node_id="svc-missing"),
            ],
        )
        errors = kg.validate_referential_integrity()
        assert len(errors) == 1
        assert "svc-missing" in errors[0]
