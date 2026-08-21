from datetime import date

import pytest
from pydantic import ValidationError

from porter_forces_ai.domain import (
    CausalHypothesis,
    Claim,
    ClaimKind,
    DecisionFrame,
    EvidenceItem,
    EvidenceOrigin,
    ForceAssessment,
    ForceDriver,
    ForceName,
    Horizon,
    ResearchBundle,
    ResearchQuery,
    SearchHit,
    SourceClass,
    StrategicOption,
    Trend,
)


def test_decision_frame_requires_no_action_baseline() -> None:
    with pytest.raises(ValidationError, match="no-action"):
        DecisionFrame(
            decision_statement="Should the bank deploy AI for KYC investigations?",
            industry_boundary="US and EU corporate banking KYC operations",
            baseline="Manual analyst-led investigation",
            options=["Targeted pilot", "Enterprise rollout", "Vendor partnership"],
            success_measures=["Realized cost per completed case"],
        )


def test_material_fact_requires_evidence() -> None:
    with pytest.raises(ValidationError, match="requires evidence_ids"):
        Claim(
            claim_id="C-missing",
            statement="A current material assertion",
            kind=ClaimKind.FACT,
            confidence=0.9,
        )


def test_public_web_evidence_requires_capture_hash() -> None:
    with pytest.raises(ValidationError, match="captured-content hash"):
        EvidenceItem(
            evidence_id="E-regulator",
            origin=EvidenceOrigin.PUBLIC_WEB,
            source_class=SourceClass.REGULATOR,
            title="Regulatory publication",
            publisher="Example regulator",
            source_url="https://example.org/publication",
            published_at=date(2026, 1, 2),
            excerpt="A captured statement from the publication.",
            quality_score=1,
            freshness_score=1,
            applicability_score=1,
        )


def test_search_snippet_cannot_be_promoted_to_evidence() -> None:
    with pytest.raises(ValidationError, match="incompatible"):
        EvidenceItem(
            evidence_id="E-snippet",
            origin=EvidenceOrigin.PUBLIC_WEB,
            source_class=SourceClass.SEARCH_SNIPPET,
            title="Search result",
            source_url="https://example.org/result",
            excerpt="Unverified result text.",
            content_sha256="a" * 64,
            quality_score=0.1,
            freshness_score=0.5,
            applicability_score=0.5,
        )


def test_origin_cannot_claim_a_stronger_source_class() -> None:
    with pytest.raises(ValidationError, match="incompatible"):
        EvidenceItem(
            evidence_id="E-user-regulator",
            origin=EvidenceOrigin.USER_PROVIDED,
            source_class=SourceClass.REGULATOR,
            title="User assertion mislabeled as regulator evidence",
            publisher="Test owner",
            excerpt="A statement supplied by a user.",
            quality_score=1,
            freshness_score=1,
            applicability_score=1,
        )


@pytest.mark.parametrize(
    "url",
    [
        "not-a-url",
        "file:///etc/passwd",
        "http://localhost/admin",
        "http://127.0.0.1/private",
        "https://user:password@example.org/report",
    ],
)
def test_discovery_urls_must_be_external_http_urls(url: str) -> None:
    with pytest.raises(ValidationError):
        SearchHit(title="Unsafe result", url=url)


def test_ready_decision_frame_rejects_blank_options_and_open_questions() -> None:
    with pytest.raises(ValidationError):
        DecisionFrame(
            decision_statement="Should the bank deploy AI for KYC investigations?",
            industry_boundary="US and EU corporate banking KYC operations",
            baseline="Manual analyst-led investigation",
            options=["No action", "", "Enterprise rollout"],
            success_measures=["Realized cost per completed case"],
            clarification_questions=["Which legal entities are in scope?"],
            ready_for_research=True,
        )


def test_ready_decision_frame_cannot_retain_clarification_questions() -> None:
    with pytest.raises(ValidationError, match="cannot retain clarification"):
        DecisionFrame(
            decision_statement="Should the bank deploy AI for KYC investigations?",
            industry_boundary="US and EU corporate banking KYC operations",
            baseline="Manual analyst-led investigation",
            options=["No action", "Controlled pilot", "Enterprise rollout"],
            success_measures=["Realized cost per completed case"],
            clarification_questions=["Which legal entities are in scope?"],
            ready_for_research=True,
        )


def test_research_bundle_cannot_mix_forces() -> None:
    with pytest.raises(ValidationError, match="hypothesis must match"):
        ResearchBundle(
            force=ForceName.RIVALRY,
            hypotheses=[
                CausalHypothesis(
                    hypothesis_id="H-mixed",
                    force=ForceName.BUYER_POWER,
                    driver="Buyer behavior",
                    force_effect="Changes buyer pressure",
                    economic_mechanism="Changes realized pricing",
                    organization_exposure="Affects product margins",
                    observable_signals=["Price realization"],
                    falsification_condition="Pricing remains unchanged",
                )
            ],
            queries=[
                ResearchQuery(
                    query_id="Q-mixed",
                    force=ForceName.RIVALRY,
                    query="public bank rivalry evidence",
                    rationale="Test force consistency",
                )
            ],
        )


def test_force_assessment_requires_normalized_consistent_driver_math() -> None:
    with pytest.raises(ValidationError, match="weights must sum to 1"):
        ForceAssessment(
            force=ForceName.RIVALRY,
            pressure_score=3,
            trend=Trend.STABLE,
            primary_horizon=Horizon.MEDIUM,
            confidence=0.6,
            drivers=[
                ForceDriver(
                    name="Competitor investment",
                    mechanism="Changes service differentiation",
                    pressure_score=4,
                    weight=0.8,
                    claim_ids=["C-1"],
                ),
                ForceDriver(
                    name="Price competition",
                    mechanism="Compresses unit margins",
                    pressure_score=2,
                    weight=0.8,
                    claim_ids=["C-2"],
                ),
            ],
            organization_exposures=["Operating margin"],
            strategic_implications=["Preserve option value"],
            leading_indicators=["Competitor releases"],
            conditions_that_change_conclusion=["Investment slows"],
        )


def test_action_option_requires_stop_and_acceleration_gates() -> None:
    with pytest.raises(ValidationError, match="action option requires"):
        StrategicOption(
            name="Controlled deployment",
            description="A stage-gated deployment",
            external_necessity=3,
            capability_fit=3,
            economic_attractiveness=3,
            control_acceptability=3,
            reversibility=4,
            time_to_learning_months=6,
            claim_ids=["C-1"],
        )
