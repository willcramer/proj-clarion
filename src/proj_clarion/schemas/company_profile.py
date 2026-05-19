"""CompanyProfile — output of the Research agent.

Hard rule: every claim has a citation or is explicitly marked synthesized.
The `provenance` and `synthesized_flags` arrays are not optional.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, field_validator

CitationId = Annotated[
    str,
    Field(
        pattern=r"^src-\d{3,}$",
        description="Reference to a Provenance entry, e.g. 'src-001'",
    ),
]


class OwnershipType(str, Enum):
    PUBLIC = "public"
    PRIVATE = "private"
    FAMILY_OWNED = "family_owned"
    EMPLOYEE_OWNED = "employee_owned"
    NONPROFIT = "nonprofit"
    GOVERNMENT = "government"
    UNKNOWN = "unknown"


class BusinessModelArchetype(str, Enum):
    """Coarse-grained business model. Drives demo defaults."""

    B2C_RETAIL = "b2c_retail"
    B2C_DIGITAL = "b2c_digital"
    B2B_SAAS = "b2b_saas"
    B2B_DIRECT = "b2b_direct"
    MARKETPLACE_MULTI_SIDED = "marketplace_multi_sided"
    MANUFACTURING = "manufacturing"
    LOGISTICS = "logistics"
    FINANCIAL_SERVICES = "financial_services"
    HEALTHCARE = "healthcare"
    MEDIA_CONTENT = "media_content"
    OMNICHANNEL_RETAIL = "omnichannel_retail"
    OTHER = "other"


class Confidence(str, Enum):
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class OrgArchetype(str, Enum):
    """Observability-shaped organisational archetype.

    Distinct from `BusinessModelArchetype` (which says "what kind of business" —
    SaaS / retail / etc.). This says "what entity hierarchy does the KG use to
    represent the company in a way that maps to dashboards, SLOs, and alerts."

    The catalog is intentionally narrow: 8 named shapes + `GENERIC`. Each shape
    pins down a top-down hierarchy where every level corresponds to a place a
    business KPI lives. We deliberately exclude leaf granularity (individual
    SKUs, individual clinicians, individual users) because those don't have
    durable dashboards — the levels above them do.

    `GENERIC` is the explicit fallback when the research agent can't classify
    with confidence. The UI surfaces a warning chip when GENERIC is chosen so
    the SE knows the demo is using a default entity model rather than a
    vertical-tuned one."""

    RETAIL                 = "retail"                  # Company -> Brand -> Region -> StoreClass -> ProductCategory
    B2B_INDUSTRIAL         = "b2b_industrial"          # Company -> BusinessUnit -> DealerTier -> Territory -> ProductFamily
    HEALTHCARE_PROVIDER    = "healthcare_provider"     # Company -> Facility -> Department -> ServiceLine
    HEALTHCARE_PAYER       = "healthcare_payer"        # Company -> PlanType -> MemberCohort -> ProviderNetwork
    SAAS                   = "saas"                    # Company -> PlanTier -> Region -> WorkspaceClass
    FINANCIAL_SERVICES     = "financial_services"      # Company -> CustomerSegment -> ProductType -> Channel
    MEDIA                  = "media"                   # Company -> Property -> AudienceSegment -> DistributionChannel
    LOGISTICS              = "logistics"               # Company -> Hub -> RouteClass -> CustomerSegment -> ShipmentClass
    GENERIC                = "generic"                 # Company -> BusinessUnit -> ProductLine -> CustomerSegment


class OrganizationalModel(BaseModel):
    """How the company's KG should be shaped for business observability.

    This is the bridge between the Research output and the Plan agent: the
    planner reads `archetype` and picks entity types from the matching catalog
    rather than force-fitting retail conventions (brand / business_unit /
    product_line) onto every customer.

    `primary_entity_type` is always 'Company' OR 'Account' — never 'Brand'.
    The customer's organisation itself sits at the top of the hierarchy; what
    varies underneath is the archetype-specific node types.

    `fallback_used` is True when the agent picked `GENERIC` because no named
    archetype was a confident fit. The Profile UI shows a small warning chip
    in that case so the SE knows the demo is shaped by defaults, not vertical
    knowledge."""

    model_config = ConfigDict(extra="forbid")

    archetype: OrgArchetype
    archetype_confidence: Confidence
    primary_entity_type: Literal["Company", "Account"] = "Company"
    rationale: str = Field(
        ..., max_length=600,
        description="One paragraph: why this archetype fits the company. Cite "
                    "specific evidence from the sources (e.g. 'dealer network', "
                    "'hospital system', 'multi-tenant SaaS dashboard').",
    )
    fallback_used: bool = Field(
        default=False,
        description="True iff archetype==GENERIC was chosen as the explicit "
                    "fallback. Surfaces a 'using defaults' warning in the UI.",
    )
    citations: list[CitationId] = Field(default_factory=list)


class CompanyIdentity(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., description="Common name, e.g. 'AcmeRetail'")
    legal_name: str | None = Field(None, description="Legal entity name if known")
    headquarters_city: str | None = None
    headquarters_country: str | None = None
    founded_year: int | None = Field(None, ge=1500, le=2100)
    ownership_type: OwnershipType = OwnershipType.UNKNOWN
    employee_count_estimate: int | None = Field(None, ge=0)
    primary_url: HttpUrl
    citations: list[CitationId] = Field(default_factory=list)


class IndustryTaxonomy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    primary_industry: str = Field(..., description="e.g. 'Apparel & Workwear'")
    sub_industries: list[str] = Field(default_factory=list)
    business_model: BusinessModelArchetype
    citations: list[CitationId] = Field(default_factory=list)


class RevenueSignals(BaseModel):
    model_config = ConfigDict(extra="forbid")

    annual_revenue_usd: float | None = Field(None, ge=0)
    revenue_year: int | None = Field(None, ge=1900, le=2100)
    growth_direction: Literal["growing", "flat", "declining", "unknown"] = "unknown"
    disclosed_segments: list[str] = Field(
        default_factory=list,
        description="Reported segments, e.g. 'Wholesale', 'D2C web', 'Retail'",
    )
    citations: list[CitationId] = Field(default_factory=list)


class Channel(BaseModel):
    model_config = ConfigDict(extra="forbid")

    channel_id: str = Field(..., pattern=r"^[a-z0-9_]+$")
    name: str
    channel_type: Literal[
        "d2c_web",
        "d2c_mobile_app",
        "d2c_retail_store",
        "b2b_direct",
        "wholesale",
        "marketplace",
        "white_label",
        "partner_endcap",
        "store_within_store",
        "other",
    ]
    description: str
    notable_partners: list[str] = Field(default_factory=list)
    citations: list[CitationId] = Field(default_factory=list)


class GeographicFootprint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    countries: list[str] = Field(default_factory=list)
    regions: list[str] = Field(default_factory=list)
    languages: list[str] = Field(default_factory=list)
    currencies: list[str] = Field(default_factory=list)
    flagship_locations: list[str] = Field(default_factory=list)
    data_center_regions: list[str] = Field(default_factory=list)
    citations: list[CitationId] = Field(default_factory=list)


class TechStackSignal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    component_type: Literal[
        "erp",
        "ecommerce_platform",
        "cms",
        "cdp",
        "cloud_provider",
        "k8s_distribution",
        "cdn",
        "database",
        "messaging",
        "data_warehouse",
        "observability",
        "apm",
        "logging",
        "siem",
        "ci_cd",
        "feature_flagging",
        "auth_idp",
        "payment_processor",
        "search",
        "vector_db",
        "llm_provider",
        "agent_framework",
        "ml_platform",
        "other",
    ]
    vendor_or_product: str = Field(..., description="e.g. '<ERP-vendor> S/4-class', 'Microsoft Azure'")
    confidence: Confidence
    citations: list[CitationId] = Field(default_factory=list)
    notes: str | None = None


class AgenticSignal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    workload_type: Literal[
        "search_recommendation",
        "virtual_assistant",
        "fit_sizing_assistant",
        "computer_vision",
        "demand_forecasting",
        "fraud_detection",
        "support_copilot",
        "code_assistant",
        "marketing_personalization",
        "agentic_orchestration",
        "other",
    ]
    description: str
    status: Literal["live", "announced", "likely_near_term"]
    citations: list[CitationId] = Field(default_factory=list)


class StrategicPriority(BaseModel):
    model_config = ConfigDict(extra="forbid")

    priority: str = Field(..., description="One-sentence summary")
    source_type: Literal["earnings_call", "press_release", "leadership_interview", "blog", "other"]
    timeframe: Literal["recent", "current_year", "multi_year", "historical"]
    citations: list[CitationId] = Field(default_factory=list)


class IncumbentObservability(BaseModel):
    model_config = ConfigDict(extra="forbid")

    vendor: str
    scope: str = Field(..., description="What they use it for, e.g. 'APM for <ERP-vendor>'")
    confidence: Confidence
    citations: list[CitationId] = Field(default_factory=list)


class PainSignal(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pain: str = Field(..., description="One-sentence pain")
    evidence_quote: str | None = Field(
        None,
        max_length=500,
        description="Short paraphrase or quote from public material; keep brief",
    )
    severity: Literal["high", "medium", "low"]
    relevance_to_observability: Literal["direct", "adjacent", "tangential"]
    citations: list[CitationId] = Field(default_factory=list)


class BusinessEntityCandidate(BaseModel):
    """A candidate top-level business entity for the knowledge graph."""

    model_config = ConfigDict(extra="forbid")

    entity_type: Literal[
        "store",
        "region",
        "channel",
        "product_line",
        "fulfillment_center",
        "business_unit",
        "brand",
        "partner_program",
        "other",
    ]
    name: str
    description: str | None = None
    citations: list[CitationId] = Field(default_factory=list)


class Provenance(BaseModel):
    """Every external source touched by the research agent."""

    model_config = ConfigDict(extra="forbid")

    citation_id: str = Field(..., pattern=r"^src-\d{3,}$")
    url: HttpUrl
    title: str | None = None
    fetched_at: datetime
    content_type: Literal["html", "pdf", "json", "text", "image", "other"] = "html"
    summary: str | None = Field(None, max_length=500)
    grounded_claims: list[str] = Field(
        default_factory=list,
        description="Short labels for the claims this source supports",
    )


class SynthesizedFlag(BaseModel):
    """A claim the LLM produced without a source. Must be reviewed."""

    model_config = ConfigDict(extra="forbid")

    claim: str
    field_path: str = Field(
        ..., description="Dotted path to the unsourced claim, e.g. 'tech_stack_signals[3]'"
    )
    rationale: str = Field(..., description="Why the model produced this without evidence")


class CompanyProfile(BaseModel):
    """Complete output of the Research phase. Read-only after generation."""

    model_config = ConfigDict(extra="forbid")

    profile_id: str = Field(..., pattern=r"^prof-[a-z0-9-]+$")
    schema_version: Literal["0.1.0"] = "0.1.0"
    generated_at: datetime
    research_duration_seconds: float = Field(..., ge=0)
    research_model: str = Field(..., description="LLM model id used, e.g. 'claude-opus-4-7'")

    company: CompanyIdentity
    industry_taxonomy: IndustryTaxonomy
    revenue_signals: RevenueSignals
    # Optional for back-compat: existing v0.1.0 profiles in the DB don't have
    # this field. The plan agent treats `None` as "fall back to generic
    # entity catalog". New research runs always populate it.
    organizational_model: OrganizationalModel | None = None
    channels: list[Channel] = Field(default_factory=list)
    geographic_footprint: GeographicFootprint
    tech_stack_signals: list[TechStackSignal] = Field(default_factory=list)
    agentic_signals: list[AgenticSignal] = Field(default_factory=list)
    recent_strategic_priorities: list[StrategicPriority] = Field(default_factory=list)
    incumbent_observability: list[IncumbentObservability] = Field(default_factory=list)
    pain_signals: list[PainSignal] = Field(default_factory=list)
    business_entity_candidates: list[BusinessEntityCandidate] = Field(default_factory=list)

    provenance: list[Provenance] = Field(default_factory=list)
    synthesized_flags: list[SynthesizedFlag] = Field(default_factory=list)

    @field_validator("provenance")
    @classmethod
    def provenance_ids_unique(cls, v: list[Provenance]) -> list[Provenance]:
        seen = set()
        for p in v:
            if p.citation_id in seen:
                raise ValueError(f"duplicate citation_id: {p.citation_id}")
            seen.add(p.citation_id)
        return v
