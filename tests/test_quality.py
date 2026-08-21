from datetime import UTC, datetime

import pytest

from porter_forces_ai.domain import (
    FORCE_ORDER,
    ApprovalRecord,
    ApprovalRole,
    Assumption,
    BoardBrief,
    BoardPoint,
    Claim,
    ClaimEvidenceLink,
    ClaimKind,
    EvidenceItem,
    EvidenceOrigin,
    EvidenceStance,
    ForceAssessment,
    ForceDriver,
    Horizon,
    SourceClass,
    StrategicOption,
    Trend,
)
from porter_forces_ai.quality import brief_fingerprint, evaluate_brief


def _evidence(*, quality_score: float = 0.8) -> EvidenceItem:
    return EvidenceItem(
        evidence_id="E-primary",
        origin=EvidenceOrigin.USER_PROVIDED,
        source_class=SourceClass.USER_ASSERTION,
        title="Controlled fixture",
        publisher="Finance owner",
        excerpt="The controlled fixture directly supports the material claim.",
        quality_score=quality_score,
        freshness_score=1,
        applicability_score=1,
    )


def _claim(*, assumption_ids: list[str] | None = None) -> Claim:
    return Claim(
        claim_id="C-primary",
        statement="A controlled deployment creates decision-relevant evidence.",
        kind=ClaimKind.INFERENCE,
        evidence_ids=["E-primary"],
        assumption_ids=assumption_ids or [],
        confidence=0.7,
    )


def _link(
    *,
    stance: EvidenceStance = EvidenceStance.SUPPORTS,
    entailment_score: float = 0.9,
) -> ClaimEvidenceLink:
    return ClaimEvidenceLink(
        claim_id="C-primary",
        evidence_id="E-primary",
        stance=stance,
        entailment_score=entailment_score,
        rationale="The fixture directly addresses the claim.",
    )


def _brief(claim: Claim, *, assumption_ids: list[str] | None = None) -> BoardBrief:
    basis = {"claim_ids": [claim.claim_id], "assumption_ids": assumption_ids or []}
    assessments = [
        ForceAssessment(
            force=force,
            pressure_score=3,
            trend=Trend.STABLE,
            primary_horizon=Horizon.MEDIUM,
            confidence=0.7,
            drivers=[
                ForceDriver(
                    name="Observed pressure",
                    mechanism="The pressure changes industry economics.",
                    pressure_score=3,
                    weight=1,
                    claim_ids=[claim.claim_id],
                )
            ],
            evidence_for_claim_ids=[claim.claim_id],
            evidence_against_claim_ids=[claim.claim_id],
            organization_exposures=["Existing operating model"],
            strategic_implications=["Preserve option value"],
            leading_indicators=["Measured unit economics"],
            conditions_that_change_conclusion=["The measured benefit is absent"],
        )
        for force in FORCE_ORDER
    ]

    def point(text: str) -> BoardPoint:
        return BoardPoint(text=text, **basis)

    def option(name: str) -> StrategicOption:
        return StrategicOption(
            name=name,
            description=f"{name} with explicit stage gates.",
            external_necessity=3,
            capability_fit=3,
            economic_attractiveness=3,
            control_acceptability=3,
            reversibility=3,
            time_to_learning_months=6,
            stop_conditions=[] if name == "No action" else ["Control test fails"],
            acceleration_conditions=(
                [] if name == "No action" else ["Measured benefit clears the hurdle"]
            ),
            **basis,
        )

    return BoardBrief(
        run_id="run-quality",
        as_of=datetime(2026, 8, 20, tzinfo=UTC),
        decision_requested="Choose the smallest sensible AI commitment.",
        recommendation=point("Use a controlled and reversible deployment."),
        why_now=[point("The deployment can create decision-relevant evidence now.")],
        no_action_case=[point("No action preserves cash but delays learning.")],
        largest_uncertainties=[point("Realized benefit remains uncertain.")],
        smallest_sensible_commitment=point("Test one bounded workflow."),
        options=[option("No action"), option("Controlled deployment"), option("Scale")],
        force_assessments=assessments,
        material_claims=[claim],
        board_questions=["Which result would justify scaling?"],
        dissenting_view=point("Waiting may preserve option value."),
    )


def _approvals(brief: BoardBrief) -> list[ApprovalRecord]:
    digest = brief_fingerprint(brief)
    return [
        ApprovalRecord(
            role=role,
            reviewer=f"{role.value} reviewer",
            reviewed_at=datetime(2026, 8, 20, 12, tzinfo=UTC),
            decision="approve",
            brief_sha256=digest,
        )
        for role in ApprovalRole
    ]


def test_all_required_approvals_publish_the_exact_valid_brief() -> None:
    evidence = _evidence()
    claim = _claim()
    brief = _brief(claim)

    draft = evaluate_brief(brief, [evidence], [claim], [], [_link()])
    approved = evaluate_brief(
        brief,
        [evidence],
        [claim],
        [],
        [_link()],
        _approvals(brief),
    )

    assert draft.draft_valid is True
    assert draft.publishable is False
    assert approved.draft_valid is True
    assert approved.publishable is True


def test_approval_is_invalidated_by_any_brief_change() -> None:
    evidence = _evidence()
    claim = _claim()
    brief = _brief(claim)
    approvals = _approvals(brief)
    revised = brief.model_copy(
        update={"decision_requested": "Choose a different AI commitment."}
    )

    report = evaluate_brief(revised, [evidence], [claim], [], [_link()], approvals)

    assert brief_fingerprint(brief) != brief_fingerprint(revised)
    assert report.draft_valid is True
    assert report.publishable is False
    assert {item.code for item in report.findings} >= {
        "STALE_APPROVAL",
        "REQUIRED_APPROVAL_MISSING",
    }


def test_in_place_contract_mutation_fails_closed() -> None:
    evidence = _evidence()
    claim = _claim()
    brief = _brief(claim)
    brief.force_assessments.clear()

    report = evaluate_brief(
        brief,
        [evidence],
        [claim],
        [],
        [_link()],
        _approvals(brief),
    )

    assert report.draft_valid is False
    assert report.publishable is False
    assert {item.code for item in report.findings} == {"MALFORMED_GATE_INPUT"}


def test_non_material_claim_cannot_be_used_as_board_visible_basis() -> None:
    evidence = _evidence()
    claim = _claim()
    brief = _brief(claim)
    non_material = claim.model_copy(
        update={"material": False, "evidence_ids": []},
    )
    unsafe_copy = brief.model_copy(update={"material_claims": [non_material]})

    report = evaluate_brief(
        unsafe_copy,
        [evidence],
        [claim],
        [],
        [],
        _approvals(unsafe_copy),
    )

    assert report.draft_valid is False
    assert report.publishable is False
    assert {item.code for item in report.findings} == {"MALFORMED_GATE_INPUT"}


def test_board_claim_cannot_rewrite_canonical_ledger_claim_with_same_id() -> None:
    evidence = _evidence()
    canonical_claim = _claim()
    brief = _brief(canonical_claim)
    rewritten_claim = canonical_claim.model_copy(
        update={"statement": "Approve an unrelated billion-dollar commitment immediately."}
    )
    rewritten_brief = brief.model_copy(update={"material_claims": [rewritten_claim]})

    report = evaluate_brief(
        rewritten_brief,
        [evidence],
        [canonical_claim],
        [],
        [_link()],
        _approvals(rewritten_brief),
    )

    assert report.draft_valid is False
    assert report.publishable is False
    assert "CLAIM_LEDGER_MISMATCH" in {item.code for item in report.findings}


def test_rejection_blocks_publication_even_if_the_same_role_also_approved() -> None:
    evidence = _evidence()
    claim = _claim()
    brief = _brief(claim)
    approvals = _approvals(brief)
    approvals.append(
        ApprovalRecord(
            role=ApprovalRole.RISK,
            reviewer="Chief risk reviewer",
            reviewed_at=datetime(2026, 8, 20, 13, tzinfo=UTC),
            decision="reject",
            brief_sha256=brief_fingerprint(brief),
        )
    )

    report = evaluate_brief(brief, [evidence], [claim], [], [_link()], approvals)

    assert report.draft_valid is True
    assert report.publishable is False
    assert "CURRENT_BRIEF_REJECTED" in {item.code for item in report.findings}


@pytest.mark.parametrize(
    ("stance", "entailment_score"),
    [
        (EvidenceStance.CONTEXT, 1.0),
        (EvidenceStance.CONTRADICTS, 1.0),
        (EvidenceStance.SUPPORTS, 0.59),
    ],
)
def test_context_contradiction_or_weak_entailment_cannot_support_material_claim(
    stance: EvidenceStance,
    entailment_score: float,
) -> None:
    evidence = _evidence()
    claim = _claim()
    brief = _brief(claim)

    report = evaluate_brief(
        brief,
        [evidence],
        [claim],
        [],
        [_link(stance=stance, entailment_score=entailment_score)],
        _approvals(brief),
    )

    assert report.draft_valid is False
    assert report.publishable is False
    assert "INSUFFICIENT_ENTAILED_SUPPORT" in {item.code for item in report.findings}


def test_zero_strength_evidence_cannot_validate_or_publish_material_claim() -> None:
    evidence = _evidence(quality_score=0)
    claim = _claim()
    brief = _brief(claim)

    report = evaluate_brief(
        brief,
        [evidence],
        [claim],
        [],
        [_link(entailment_score=1)],
        _approvals(brief),
    )

    assert report.draft_valid is False
    assert report.publishable is False
    assert "INSUFFICIENT_ENTAILED_SUPPORT" in {item.code for item in report.findings}


@pytest.mark.parametrize(
    ("status", "expected_code", "draft_valid"),
    [
        ("rejected", "REJECTED_ASSUMPTION_REFERENCED", False),
        ("unvalidated", "MATERIAL_ASSUMPTION_UNVALIDATED", True),
    ],
)
def test_referenced_assumption_status_is_enforced(
    status: str,
    expected_code: str,
    draft_valid: bool,
) -> None:
    assumption = Assumption(
        assumption_id="A-benefit",
        statement="Benefits can be converted into booked savings.",
        material=True,
        status=status,
    )
    claim = _claim(assumption_ids=[assumption.assumption_id])
    brief = _brief(claim, assumption_ids=[assumption.assumption_id])

    report = evaluate_brief(
        brief,
        [_evidence()],
        [claim],
        [assumption],
        [_link()],
        _approvals(brief),
    )

    assert report.draft_valid is draft_valid
    assert expected_code in {item.code for item in report.findings}
    assert report.publishable is draft_valid
