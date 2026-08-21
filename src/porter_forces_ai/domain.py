"""Canonical, provider-neutral contracts for a strategy-analysis run.

The LLM is allowed to propose instances of these models. It is not allowed to
change their semantics. Deterministic validators and quality gates consume the
same contracts, which keeps orchestration, model choice, and rendering loosely
coupled.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from enum import StrEnum
from ipaddress import ip_address
from typing import Annotated
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class ContractModel(BaseModel):
    """Strict base class for persisted workflow artifacts."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        validate_assignment=True,
    )


class OrganizationArchetype(StrEnum):
    GLOBAL_BANK = "global_bank"
    QUANT_TRADING = "quant_trading"
    INSURER = "insurer"


class AnalysisMode(StrEnum):
    INDUSTRY_ATTRACTIVENESS = "industry_attractiveness"
    STRATEGIC_INITIATIVE = "strategic_initiative"
    COMPETITIVE_RESPONSE = "competitive_response"


class BoardAudience(StrEnum):
    FULL_BOARD = "full_board"
    CHAIR = "chair"
    CEO = "ceo"
    CFO = "cfo"
    CRO = "cro"
    CIO = "cio"
    COO = "coo"
    BUSINESS_EXECUTIVE = "business_executive"
    AUDIT_OR_RISK_COMMITTEE = "audit_or_risk_committee"


class ForceName(StrEnum):
    NEW_ENTRANTS = "threat_of_new_entrants"
    SUPPLIER_POWER = "bargaining_power_of_suppliers"
    BUYER_POWER = "bargaining_power_of_buyers"
    SUBSTITUTES = "threat_of_substitutes"
    RIVALRY = "competitive_rivalry"


FORCE_ORDER: tuple[ForceName, ...] = (
    ForceName.NEW_ENTRANTS,
    ForceName.SUPPLIER_POWER,
    ForceName.BUYER_POWER,
    ForceName.SUBSTITUTES,
    ForceName.RIVALRY,
)


class Trend(StrEnum):
    DECREASING = "decreasing"
    STABLE = "stable"
    INCREASING = "increasing"
    UNCERTAIN = "uncertain"


class Horizon(StrEnum):
    NEAR = "near_0_12_months"
    MEDIUM = "medium_12_36_months"
    LONG = "long_36_plus_months"


class ClaimKind(StrEnum):
    FACT = "fact"
    INFERENCE = "inference"
    ASSUMPTION = "assumption"
    CALCULATION = "calculation"
    RECOMMENDATION = "recommendation"
    MODEL_PRIOR = "model_prior"


class EvidenceOrigin(StrEnum):
    PUBLIC_WEB = "public_web"
    USER_PROVIDED = "user_provided"
    INTERNAL_DOCUMENT = "internal_document"
    MODEL_PRIOR = "model_prior"
    CALCULATION = "calculation"


class EvidenceStance(StrEnum):
    SUPPORTS = "supports"
    CONTRADICTS = "contradicts"
    CONTEXT = "context"


class SourceClass(StrEnum):
    REGULATOR = "regulator"
    LEGISLATION = "legislation"
    OFFICIAL_STATISTICS = "official_statistics"
    AUDITED_FILING = "audited_filing"
    COMPANY_DISCLOSURE = "company_disclosure"
    ACADEMIC = "academic"
    INDUSTRY_RESEARCH = "industry_research"
    REPUTABLE_MEDIA = "reputable_media"
    VENDOR = "vendor"
    SEARCH_SNIPPET = "search_snippet"
    INTERNAL_DOCUMENT = "internal_document"
    USER_ASSERTION = "user_assertion"
    MODEL_PRIOR = "model_prior"
    CALCULATOR = "calculator"


class ApprovalRole(StrEnum):
    STRATEGY = "strategy"
    FINANCE = "finance"
    TECHNOLOGY = "technology"
    RISK = "risk"


class ApprovalDecision(StrEnum):
    APPROVE = "approve"
    REJECT = "reject"


_PUBLIC_SOURCE_CLASSES = frozenset(
    {
        SourceClass.REGULATOR,
        SourceClass.LEGISLATION,
        SourceClass.OFFICIAL_STATISTICS,
        SourceClass.AUDITED_FILING,
        SourceClass.COMPANY_DISCLOSURE,
        SourceClass.ACADEMIC,
        SourceClass.INDUSTRY_RESEARCH,
        SourceClass.REPUTABLE_MEDIA,
        SourceClass.VENDOR,
    }
)

_SOURCE_CLASSES_BY_ORIGIN: dict[EvidenceOrigin, frozenset[SourceClass]] = {
    EvidenceOrigin.PUBLIC_WEB: _PUBLIC_SOURCE_CLASSES,
    EvidenceOrigin.USER_PROVIDED: frozenset({SourceClass.USER_ASSERTION}),
    EvidenceOrigin.INTERNAL_DOCUMENT: frozenset({SourceClass.INTERNAL_DOCUMENT}),
    EvidenceOrigin.MODEL_PRIOR: frozenset({SourceClass.MODEL_PRIOR}),
    EvidenceOrigin.CALCULATION: frozenset({SourceClass.CALCULATOR}),
}

_ListItem = Annotated[str, Field(min_length=2, max_length=2_000)]


def _validate_external_http_url(value: str) -> str:
    """Reject malformed and obviously local URLs at artifact boundaries."""

    candidate = value.strip()
    try:
        parsed = urlsplit(candidate)
        port = parsed.port
    except ValueError as exc:
        raise ValueError("URL is malformed") from exc
    if parsed.scheme.casefold() not in {"http", "https"} or not parsed.hostname:
        raise ValueError("URL must be an absolute HTTP(S) URL")
    if parsed.username or parsed.password:
        raise ValueError("URL must not contain credentials")
    host = parsed.hostname.rstrip(".").casefold()
    if host == "localhost" or host.endswith((".localhost", ".local", ".internal")):
        raise ValueError("URL must not target a local host")
    if (host.replace(".", "").isdigit() or host.startswith("0x")) and ":" not in host:
        try:
            address = ip_address(int(host, 0)) if "." not in host else ip_address(host)
        except ValueError as exc:
            raise ValueError("URL must not use a non-standard numeric host") from exc
    else:
        try:
            address = ip_address(host)
        except ValueError:
            address = None
    if address is not None and not address.is_global:
        raise ValueError("URL must not target a private or non-global IP address")
    if port is not None and not 1 <= port <= 65_535:
        raise ValueError("URL port is invalid")
    return candidate


class DecisionRequest(ContractModel):
    """User input before the system turns a topic into a decision contract."""

    question: Annotated[str, Field(min_length=12, max_length=4_000)]
    archetype: OrganizationArchetype
    analysis_mode: AnalysisMode = AnalysisMode.STRATEGIC_INITIATIVE
    organization_name: str | None = Field(default=None, max_length=200)
    industry_arena: str | None = Field(
        default=None,
        description="Product/customer/geography market boundary, if already known.",
        max_length=500,
    )
    geographies: list[str] = Field(default_factory=list, max_length=20)
    time_horizon_months: int = Field(default=36, ge=1, le=120)
    audience: list[BoardAudience] = Field(
        default_factory=lambda: [BoardAudience.FULL_BOARD],
        min_length=1,
    )
    constraints: list[str] = Field(default_factory=list, max_length=30)
    public_research_context: Annotated[
        str,
        Field(
            min_length=12,
            max_length=4_000,
            description="Explicitly approved, sanitized context for public search.",
        ),
    ]

    restricted_terms: list[Annotated[str, Field(min_length=4, max_length=300)]] = Field(
        default_factory=list,
        max_length=100,
        description="Local-only terms that the outbound-query guard must reject.",
    )
    internal_context: dict[str, str] = Field(
        default_factory=dict,
        description="Confidential local-only facts. Never copy these into public search queries.",
    )
    evidence_cutoff: date | None = None


class Assumption(ContractModel):
    assumption_id: str = Field(pattern=r"^A-[A-Za-z0-9_-]+$")
    statement: str = Field(min_length=3, max_length=2_000)
    owner: str | None = Field(default=None, max_length=200)
    material: bool = True
    validation_method: str | None = Field(default=None, max_length=1_000)
    status: str = Field(default="unvalidated", pattern=r"^(unvalidated|validated|rejected)$")


class DecisionFrame(ContractModel):
    """The decision contract that must be usable before research starts."""

    decision_statement: str = Field(min_length=12, max_length=2_000)
    decision_owner: str | None = Field(default=None, max_length=200)
    industry_boundary: str = Field(min_length=8, max_length=2_000)
    baseline: str = Field(min_length=3, max_length=2_000)
    options: list[_ListItem] = Field(min_length=3, max_length=12)
    success_measures: list[_ListItem] = Field(min_length=1, max_length=20)
    assumptions: list[Assumption] = Field(default_factory=list, max_length=50)
    clarification_questions: list[_ListItem] = Field(default_factory=list, max_length=12)
    reversible_elements: list[_ListItem] = Field(default_factory=list)
    irreversible_elements: list[_ListItem] = Field(default_factory=list)
    ready_for_research: bool = False

    @model_validator(mode="after")
    def require_status_quo_option(self) -> DecisionFrame:
        if len({item.casefold() for item in self.options}) != len(self.options):
            raise ValueError("options must be unique")
        joined = " ".join(self.options).lower()
        if not any(term in joined for term in ("no action", "do nothing", "current course")):
            raise ValueError("options must include an explicit no-action/current-course baseline")
        if self.ready_for_research and self.clarification_questions:
            raise ValueError(
                "a research-ready decision frame cannot retain clarification questions"
            )
        return self


class PublicResearchAssignment(ContractModel):
    """The complete payload allowed to cross into a public-research worker."""

    force: ForceName
    archetype: OrganizationArchetype
    analysis_mode: AnalysisMode
    public_context: Annotated[str, Field(min_length=12, max_length=4_000)]
    time_horizon_months: int = Field(ge=1, le=120)
    evidence_cutoff: date | None = None


class ResearchQuery(ContractModel):
    query_id: str = Field(pattern=r"^Q-[A-Za-z0-9_-]+$")
    force: ForceName | None = None
    query: str = Field(min_length=3, max_length=300)
    rationale: str = Field(min_length=3, max_length=1_000)
    preferred_source_classes: list[SourceClass] = Field(default_factory=list)
    recency: str | None = Field(default=None, pattern=r"^(d|w|m|y)$")


class SearchHit(ContractModel):
    """Discovery metadata. A hit is never sufficient support for a material claim."""

    query_id: str | None = None
    title: str = Field(min_length=1, max_length=1_000)
    url: str = Field(min_length=8, max_length=4_000)
    snippet: str = Field(default="", max_length=4_000)
    provider: str = "duckduckgo"
    retrieved_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        return _validate_external_http_url(value)


class EvidenceItem(ContractModel):
    """A captured source passage or explicitly labeled non-public basis."""

    evidence_id: str = Field(pattern=r"^E-[A-Za-z0-9_-]+$")
    origin: EvidenceOrigin
    source_class: SourceClass
    title: str = Field(min_length=1, max_length=1_000)
    publisher: str | None = Field(default=None, max_length=500)
    source_url: str | None = Field(default=None, max_length=4_000)
    published_at: date | None = None
    retrieved_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    excerpt: str = Field(min_length=1, max_length=12_000)
    content_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    geographies: list[str] = Field(default_factory=list)
    applicable_entities: list[str] = Field(default_factory=list)
    applicable_period: str | None = Field(default=None, max_length=300)
    quality_score: float = Field(ge=0, le=1)
    freshness_score: float = Field(ge=0, le=1)
    applicability_score: float = Field(ge=0, le=1)
    notes: str | None = Field(default=None, max_length=2_000)

    @field_validator("source_url")
    @classmethod
    def validate_source_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return _validate_external_http_url(value)

    @model_validator(mode="after")
    def require_consistent_provenance(self) -> EvidenceItem:
        allowed_classes = _SOURCE_CLASSES_BY_ORIGIN[self.origin]
        if self.source_class not in allowed_classes:
            raise ValueError(
                f"{self.source_class.value} is incompatible with {self.origin.value} origin"
            )
        if self.origin == EvidenceOrigin.PUBLIC_WEB:
            if not self.publisher:
                raise ValueError("public web evidence requires publisher")
            if not self.source_url:
                raise ValueError("public web evidence requires source_url")
            if not self.content_sha256:
                raise ValueError("public web evidence requires a captured-content hash")
        return self


class Claim(ContractModel):
    claim_id: str = Field(pattern=r"^C-[A-Za-z0-9_-]+$")
    statement: str = Field(min_length=3, max_length=4_000)
    kind: ClaimKind
    material: bool = True
    evidence_ids: list[str] = Field(default_factory=list)
    assumption_ids: list[str] = Field(default_factory=list)
    stance: EvidenceStance = EvidenceStance.SUPPORTS
    confidence: float = Field(ge=0, le=1)
    reasoning: str | None = Field(
        default=None,
        description="Concise inference rationale, never hidden chain of thought.",
        max_length=2_000,
    )

    @model_validator(mode="after")
    def require_traceable_basis(self) -> Claim:
        if self.material and self.kind == ClaimKind.FACT and not self.evidence_ids:
            raise ValueError("a material factual claim requires evidence_ids")
        if self.material and self.kind == ClaimKind.ASSUMPTION and not self.assumption_ids:
            raise ValueError("a material assumption requires assumption_ids")
        derived_kinds = {
            ClaimKind.INFERENCE,
            ClaimKind.CALCULATION,
            ClaimKind.RECOMMENDATION,
        }
        if (
            self.material
            and self.kind in derived_kinds
            and not self.evidence_ids
            and not self.assumption_ids
        ):
            raise ValueError("a material derived claim requires evidence_ids or assumption_ids")
        if self.kind == ClaimKind.MODEL_PRIOR and self.evidence_ids:
            raise ValueError("model priors must not masquerade as externally evidenced claims")
        return self


class ClaimEvidenceLink(ContractModel):
    """Explicit relationship between one claim and one captured source."""

    claim_id: str = Field(pattern=r"^C-[A-Za-z0-9_-]+$")
    evidence_id: str = Field(pattern=r"^E-[A-Za-z0-9_-]+$")
    stance: EvidenceStance
    entailment_score: float = Field(
        ge=0,
        le=1,
        description="Verifier score; it is a triage signal, not proof of truth.",
    )
    rationale: str = Field(min_length=3, max_length=1_000)


class CausalHypothesis(ContractModel):
    hypothesis_id: str = Field(pattern=r"^H-[A-Za-z0-9_-]+$")
    force: ForceName
    driver: str = Field(min_length=3, max_length=1_000)
    force_effect: str = Field(min_length=3, max_length=1_000)
    economic_mechanism: str = Field(min_length=3, max_length=1_000)
    organization_exposure: str = Field(min_length=3, max_length=1_000)
    observable_signals: list[str] = Field(min_length=1, max_length=12)
    falsification_condition: str = Field(min_length=3, max_length=1_000)


class SourceCandidate(ContractModel):
    """A source discovered by research but not yet promoted into evidence."""

    url: str = Field(min_length=8, max_length=4_000)
    title: str = Field(min_length=1, max_length=1_000)
    why_relevant: str = Field(min_length=3, max_length=2_000)
    query_id: str | None = None

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        return _validate_external_http_url(value)


class ResearchBundle(ContractModel):
    """Structured response produced by one bounded Deep Agent worker."""

    force: ForceName
    hypotheses: list[CausalHypothesis] = Field(min_length=1, max_length=20)
    queries: list[ResearchQuery] = Field(min_length=1, max_length=30)
    source_candidates: list[SourceCandidate] = Field(default_factory=list, max_length=100)
    model_priors: list[str] = Field(default_factory=list, max_length=30)
    evidence_gaps: list[str] = Field(default_factory=list, max_length=30)

    @model_validator(mode="after")
    def require_force_and_identifier_consistency(self) -> ResearchBundle:
        if any(item.force != self.force for item in self.hypotheses):
            raise ValueError("every hypothesis must match the bundle force")
        if any(item.force is not None and item.force != self.force for item in self.queries):
            raise ValueError("every force-specific query must match the bundle force")
        hypothesis_ids = [item.hypothesis_id for item in self.hypotheses]
        query_ids = [item.query_id for item in self.queries]
        if len(set(hypothesis_ids)) != len(hypothesis_ids):
            raise ValueError("hypothesis identifiers must be unique within a bundle")
        if len(set(query_ids)) != len(query_ids):
            raise ValueError("query identifiers must be unique within a bundle")
        return self


class EvidenceLedger(ContractModel):
    evidence: list[EvidenceItem]
    claims: list[Claim]
    links: list[ClaimEvidenceLink]


class ChallengeReport(ContractModel):
    strongest_counterargument: str = Field(min_length=3, max_length=4_000)
    disconfirming_claim_ids: list[str] = Field(default_factory=list)
    dominant_assumption_ids: list[str] = Field(default_factory=list)
    premortem: list[str] = Field(min_length=1, max_length=20)
    invalidation_conditions: list[str] = Field(min_length=1, max_length=20)
    required_changes: list[str] = Field(default_factory=list, max_length=20)


class ForceDriver(ContractModel):
    name: str = Field(min_length=2, max_length=500)
    mechanism: str = Field(min_length=3, max_length=2_000)
    pressure_score: float = Field(ge=1, le=5)
    weight: float = Field(gt=0, le=1)
    claim_ids: list[str] = Field(min_length=1)


class ForceAssessment(ContractModel):
    force: ForceName
    pressure_score: float = Field(ge=1, le=5)
    trend: Trend
    primary_horizon: Horizon
    confidence: float = Field(ge=0, le=1)
    drivers: list[ForceDriver] = Field(min_length=1, max_length=20)
    evidence_for_claim_ids: list[str] = Field(default_factory=list)
    evidence_against_claim_ids: list[str] = Field(default_factory=list)
    organization_exposures: list[str] = Field(min_length=1)
    strategic_implications: list[str] = Field(min_length=1)
    leading_indicators: list[str] = Field(min_length=1)
    conditions_that_change_conclusion: list[str] = Field(min_length=1)

    @model_validator(mode="after")
    def require_normalized_driver_math(self) -> ForceAssessment:
        driver_names = [driver.name.casefold() for driver in self.drivers]
        if len(set(driver_names)) != len(driver_names):
            raise ValueError("force driver names must be unique")
        weight_total = sum(driver.weight for driver in self.drivers)
        if abs(weight_total - 1.0) > 1e-6:
            raise ValueError("force driver weights must sum to 1")
        weighted_pressure = sum(
            driver.pressure_score * driver.weight for driver in self.drivers
        )
        if abs(self.pressure_score - weighted_pressure) > 0.05:
            raise ValueError(
                "force pressure_score must match the weighted driver score within 0.05"
            )
        return self


class StrategicOption(ContractModel):
    name: str = Field(min_length=2, max_length=300)
    description: str = Field(min_length=3, max_length=2_000)
    external_necessity: float = Field(ge=1, le=5)
    capability_fit: float = Field(ge=1, le=5)
    economic_attractiveness: float = Field(ge=1, le=5)
    control_acceptability: float = Field(ge=1, le=5)
    reversibility: float = Field(ge=1, le=5)
    time_to_learning_months: int = Field(ge=0, le=120)
    claim_ids: list[str] = Field(default_factory=list)
    assumption_ids: list[str] = Field(default_factory=list)
    conditions_required: list[str] = Field(default_factory=list)
    stop_conditions: list[str] = Field(default_factory=list)
    acceleration_conditions: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def require_traceable_basis(self) -> StrategicOption:
        if not self.claim_ids and not self.assumption_ids:
            raise ValueError("a strategic option requires claim_ids or assumption_ids")
        normalized_name = self.name.casefold()
        is_no_action = any(
            term in normalized_name
            for term in ("no action", "do nothing", "current course", "status quo")
        )
        if not is_no_action and (
            not self.stop_conditions or not self.acceleration_conditions
        ):
            raise ValueError(
                "an action option requires explicit stop_conditions and "
                "acceleration_conditions"
            )
        return self


class Analogy(ContractModel):
    unfamiliar_concept: str = Field(min_length=2, max_length=1_000)
    familiar_mechanism: str = Field(min_length=2, max_length=1_000)
    audience: BoardAudience
    correspondences: list[str] = Field(min_length=1, max_length=10)
    decision_implication: str = Field(min_length=3, max_length=1_000)
    where_it_breaks: str = Field(min_length=3, max_length=1_000)


class BoardPoint(ContractModel):
    """One board-visible statement and the claim/assumption ids behind it."""

    text: str = Field(min_length=3, max_length=2_000)
    claim_ids: list[str] = Field(default_factory=list)
    assumption_ids: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def require_traceable_basis(self) -> BoardPoint:
        if not self.claim_ids and not self.assumption_ids:
            raise ValueError("a board point requires claim_ids or assumption_ids")
        return self


class BoardBrief(ContractModel):
    """Structured content from which memo, slides, and Q&A are rendered."""

    run_id: str = Field(min_length=6, max_length=200)
    as_of: datetime
    decision_requested: str = Field(min_length=3, max_length=2_000)
    recommendation: BoardPoint
    why_now: list[BoardPoint] = Field(min_length=1, max_length=10)
    no_action_case: list[BoardPoint] = Field(min_length=1, max_length=10)
    largest_uncertainties: list[BoardPoint] = Field(min_length=1, max_length=10)
    smallest_sensible_commitment: BoardPoint
    options: list[StrategicOption] = Field(min_length=3, max_length=12)
    force_assessments: list[ForceAssessment] = Field(min_length=5, max_length=5)
    material_claims: list[Claim] = Field(default_factory=list)
    analogies: list[Analogy] = Field(default_factory=list, max_length=10)
    board_questions: list[str] = Field(min_length=1, max_length=30)
    dissenting_view: BoardPoint

    @model_validator(mode="after")
    def require_each_force_once(self) -> BoardBrief:
        forces = [assessment.force for assessment in self.force_assessments]
        if set(forces) != set(FORCE_ORDER) or len(set(forces)) != len(FORCE_ORDER):
            raise ValueError("force_assessments must contain each Porter force exactly once")
        if any(not claim.material for claim in self.material_claims):
            raise ValueError("material_claims cannot contain claims labeled non-material")
        option_names = [option.name.casefold() for option in self.options]
        if len(set(option_names)) != len(option_names):
            raise ValueError("board options must have unique names")
        if not any(
            any(
                term in name
                for term in ("no action", "do nothing", "current course", "status quo")
            )
            for name in option_names
        ):
            raise ValueError("board options must include an explicit no-action option")
        return self


class ApprovalRecord(ContractModel):
    """Immutable, content-bound human decision kept outside the generated brief."""

    model_config = ConfigDict(
        extra="forbid",
        str_strip_whitespace=True,
        validate_assignment=True,
        frozen=True,
    )

    role: ApprovalRole
    reviewer: str = Field(min_length=3, max_length=300)
    reviewed_at: datetime
    decision: ApprovalDecision
    brief_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
