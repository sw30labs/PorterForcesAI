from decimal import Decimal

from porter_forces_ai.domain import (
    BoardAudience,
    DecisionRequest,
    EvidenceStance,
    OrganizationArchetype,
)
from porter_forces_ai.economics import (
    CostOfDelayResult,
    RangeEstimate,
    ScenarioEconomicsResult,
)
from porter_forces_ai.evaluation import (
    AnalysisGoalEvaluator,
    criteria_for,
    evidence_snapshot_id,
)
from porter_forces_ai.ralph import (
    AttemptContext,
    CompletionTarget,
    CriterionOutcome,
    GoalReport,
)
from porter_forces_ai.runtime import DeterministicDemoRuntime
from porter_forces_ai.workflow import build_workflow


def _request(*, audiences: list[BoardAudience] | None = None) -> DecisionRequest:
    return DecisionRequest(
        question="Should a global bank test a controlled AI workflow now or wait?",
        archetype=OrganizationArchetype.GLOBAL_BANK,
        industry_arena="US regulated-bank knowledge workflows",
        audience=audiences or [BoardAudience.FULL_BOARD],
        public_research_context=(
            "Public evidence about controlled AI adoption in regulated global banking."
        ),
    )


def _range(low: str, base: str, high: str, *, unit: str = "USD") -> RangeEstimate:
    return RangeEstimate(
        low=Decimal(low),
        base=Decimal(base),
        high=Decimal(high),
        unit=unit,
        basis_ids=["FINANCE-OWNED-TEST"],
    )


def _scenario(
    *,
    low: str = "-10",
    base: str = "5",
    high: str = "20",
) -> ScenarioEconomicsResult:
    return ScenarioEconomicsResult(
        scenario_name="Controlled workflow deployment",
        currency="USD",
        npv=_range(low, base, high),
        undiscounted_roi=_range("-0.2", "0.1", "0.4", unit="ratio"),
        base_discounted_payback_month=24 if Decimal(base) >= 0 else None,
        formulas=["calculator-owned NPV formula"],
    )


def _delay() -> CostOfDelayResult:
    return CostOfDelayResult(
        currency="USD",
        period_months=18,
        cost_of_delay=_range("2", "8", "15"),
        formula="calculator-owned cost-of-delay formula",
    )


def _output(
    request: DecisionRequest | None = None,
    *,
    scenarios: list[ScenarioEconomicsResult] | None = None,
    delay: CostOfDelayResult | None = None,
) -> dict[str, object]:
    active_request = request or _request()
    return build_workflow(DeterministicDemoRuntime()).invoke(
        {
            "request": active_request,
            "research_bundles": [],
            "force_assessments": [],
            "scenario_economics": scenarios or [],
            "cost_of_delay": delay,
        }
    )


def _context() -> AttemptContext:
    return AttemptContext(
        run_id="ralph-test",
        attempt_number=1,
        attempt_id="attempt-test",
        thread_id="thread-test",
        evidence_snapshot_id="snapshot-test",
    )


def _results(report: GoalReport) -> dict[str, object]:
    return {item.criterion_id: item for item in report.evaluations}


def test_deterministic_evaluator_accepts_complete_demo_draft() -> None:
    request = _request(
        audiences=[BoardAudience.FULL_BOARD, BoardAudience.CFO, BoardAudience.CRO]
    )
    output = _output(request)
    snapshot = evidence_snapshot_id(output["ledger"].evidence)  # type: ignore[union-attr]
    evaluator = AnalysisGoalEvaluator(
        expected_evidence_snapshot_id=snapshot,
        request=request,
    )

    report = evaluator(criteria_for(CompletionTarget.DRAFT), output, _context())

    assert all(item.outcome is CriterionOutcome.PASS for item in report.evaluations)


def test_evaluator_rejects_a_changed_evidence_snapshot() -> None:
    request = _request()
    output = _output(request)
    evaluator = AnalysisGoalEvaluator(
        expected_evidence_snapshot_id="evidence-" + "0" * 64,
        request=request,
    )

    report = evaluator(criteria_for(CompletionTarget.DRAFT), output, _context())
    result = _results(report)

    assert result["G-evidence-integrity"].outcome is CriterionOutcome.FAIL  # type: ignore[attr-defined]
    assert "frozen evidence" in result["G-evidence-integrity"].explanation  # type: ignore[attr-defined]


def test_publishable_target_pauses_for_content_bound_human_approvals() -> None:
    request = _request()
    output = _output(request)
    snapshot = evidence_snapshot_id(output["ledger"].evidence)  # type: ignore[union-attr]
    evaluator = AnalysisGoalEvaluator(
        expected_evidence_snapshot_id=snapshot,
        request=request,
    )

    report = evaluator(criteria_for(CompletionTarget.PUBLISHABLE), output, _context())
    result = _results(report)

    assert result["G-human-approval"].outcome is CriterionOutcome.HUMAN_REQUIRED  # type: ignore[attr-defined]


def test_audience_analogy_criterion_requires_every_requested_role() -> None:
    request = _request(audiences=[BoardAudience.FULL_BOARD, BoardAudience.CFO])
    output = _output(request)
    output["board_brief"] = output["board_brief"].model_copy(  # type: ignore[union-attr]
        update={"analogies": output["board_brief"].analogies[:1]}  # type: ignore[union-attr]
    )
    snapshot = evidence_snapshot_id(output["ledger"].evidence)  # type: ignore[union-attr]
    evaluator = AnalysisGoalEvaluator(
        expected_evidence_snapshot_id=snapshot,
        request=request,
    )

    result = _results(
        evaluator(criteria_for(CompletionTarget.DRAFT), output, _context())
    )

    assert result["G-audience-analogy"].outcome is CriterionOutcome.FAIL  # type: ignore[attr-defined]
    assert "cfo" in result["G-audience-analogy"].explanation  # type: ignore[attr-defined]


def test_request_fidelity_rejects_question_or_market_boundary_drift() -> None:
    request = _request()
    output = _output(request)
    output["decision_frame"] = output["decision_frame"].model_copy(  # type: ignore[union-attr]
        update={"industry_boundary": "Worldwide retail banking"}
    )
    snapshot = evidence_snapshot_id(output["ledger"].evidence)  # type: ignore[union-attr]
    evaluator = AnalysisGoalEvaluator(
        expected_evidence_snapshot_id=snapshot,
        request=request,
    )

    result = _results(
        evaluator(criteria_for(CompletionTarget.DRAFT), output, _context())
    )

    assert result["G-request-fidelity"].outcome is CriterionOutcome.FAIL  # type: ignore[attr-defined]
    assert "market boundary" in result["G-request-fidelity"].explanation  # type: ignore[attr-defined]


def test_counterevidence_must_be_truthfully_labeled_and_visible_in_dissent() -> None:
    request = _request()
    output = _output(request)
    challenge = output["challenge_report"]
    supporting_id = next(
        claim.claim_id
        for claim in output["ledger"].claims  # type: ignore[union-attr]
        if claim.stance is EvidenceStance.SUPPORTS
    )
    output["challenge_report"] = challenge.model_copy(  # type: ignore[union-attr]
        update={"disconfirming_claim_ids": [supporting_id]}
    )
    snapshot = evidence_snapshot_id(output["ledger"].evidence)  # type: ignore[union-attr]
    evaluator = AnalysisGoalEvaluator(
        expected_evidence_snapshot_id=snapshot,
        request=request,
    )

    result = _results(
        evaluator(criteria_for(CompletionTarget.DRAFT), output, _context())
    )

    assert result["G-counterevidence"].outcome is CriterionOutcome.FAIL  # type: ignore[attr-defined]
    assert "no contrary stance" in result["G-counterevidence"].explanation  # type: ignore[attr-defined]


def test_economics_criterion_is_conditional_and_preserves_owned_snapshot() -> None:
    without_economics = {
        item.criterion_id for item in criteria_for(CompletionTarget.DRAFT)
    }
    with_economics = {
        item.criterion_id
        for item in criteria_for(CompletionTarget.DRAFT, include_economics=True)
    }

    assert "G-economics-coherence" not in without_economics
    assert "G-economics-coherence" in with_economics

    request = _request()
    scenario = _scenario()
    delay = _delay()
    output = _output(request, scenarios=[scenario], delay=delay)
    snapshot = evidence_snapshot_id(output["ledger"].evidence)  # type: ignore[union-attr]
    evaluator = AnalysisGoalEvaluator(
        expected_evidence_snapshot_id=snapshot,
        request=request,
        scenario_economics=[scenario],
        cost_of_delay=delay,
    )

    result = _results(
        evaluator(
            criteria_for(CompletionTarget.DRAFT, include_economics=True),
            output,
            _context(),
        )
    )

    assert result["G-economics-coherence"].outcome is CriterionOutcome.PASS  # type: ignore[attr-defined]


def test_wholly_negative_owned_economics_cannot_be_presented_as_attractive() -> None:
    request = _request()
    negative = _scenario(low="-40", base="-25", high="-10")
    output = _output(request, scenarios=[negative])
    snapshot = evidence_snapshot_id(output["ledger"].evidence)  # type: ignore[union-attr]
    evaluator = AnalysisGoalEvaluator(
        expected_evidence_snapshot_id=snapshot,
        request=request,
        scenario_economics=[negative],
    )

    result = _results(
        evaluator(
            criteria_for(CompletionTarget.DRAFT, include_economics=True),
            output,
            _context(),
        )
    )
    brief = output["board_brief"]

    assert result["G-economics-coherence"].outcome is CriterionOutcome.PASS  # type: ignore[attr-defined]
    assert "negative" in brief.recommendation.text.casefold()  # type: ignore[union-attr]
    assert "do not scale" in brief.recommendation.text.casefold()  # type: ignore[union-attr]
    assert all(
        option.economic_attractiveness <= 2
        for option in brief.options  # type: ignore[union-attr]
        if "no action" not in option.name.casefold()
    )


def test_economics_snapshot_drift_fails_closed() -> None:
    request = _request()
    scenario = _scenario()
    output = _output(request, scenarios=[scenario])
    output["scenario_economics"] = [_scenario(low="-100", base="-50", high="-20")]
    snapshot = evidence_snapshot_id(output["ledger"].evidence)  # type: ignore[union-attr]
    evaluator = AnalysisGoalEvaluator(
        expected_evidence_snapshot_id=snapshot,
        request=request,
        scenario_economics=[scenario],
    )

    result = _results(
        evaluator(
            criteria_for(CompletionTarget.DRAFT, include_economics=True),
            output,
            _context(),
        )
    )

    assert result["G-economics-coherence"].outcome is CriterionOutcome.FAIL  # type: ignore[attr-defined]
    assert "changed or omitted" in result["G-economics-coherence"].explanation  # type: ignore[attr-defined]
