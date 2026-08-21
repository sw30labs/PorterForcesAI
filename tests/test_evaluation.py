from porter_forces_ai.domain import DecisionRequest, OrganizationArchetype
from porter_forces_ai.evaluation import (
    AnalysisGoalEvaluator,
    criteria_for,
    evidence_snapshot_id,
)
from porter_forces_ai.ralph import AttemptContext, CompletionTarget, CriterionOutcome
from porter_forces_ai.runtime import DeterministicDemoRuntime
from porter_forces_ai.workflow import build_workflow


def _output() -> dict[str, object]:
    request = DecisionRequest(
        question="Should a global bank test a controlled AI workflow now or wait?",
        archetype=OrganizationArchetype.GLOBAL_BANK,
        public_research_context=(
            "Public evidence about controlled AI adoption in regulated global banking."
        ),
    )
    return build_workflow(DeterministicDemoRuntime()).invoke(
        {"request": request, "research_bundles": [], "force_assessments": []}
    )


def _context() -> AttemptContext:
    return AttemptContext(
        run_id="ralph-test",
        attempt_number=1,
        attempt_id="attempt-test",
        thread_id="thread-test",
        evidence_snapshot_id="snapshot-test",
    )


def test_deterministic_evaluator_accepts_complete_demo_draft() -> None:
    output = _output()
    snapshot = evidence_snapshot_id(output["ledger"].evidence)  # type: ignore[union-attr]
    evaluator = AnalysisGoalEvaluator(expected_evidence_snapshot_id=snapshot)

    report = evaluator(criteria_for(CompletionTarget.DRAFT), output, _context())

    assert all(item.outcome is CriterionOutcome.PASS for item in report.evaluations)


def test_evaluator_rejects_a_changed_evidence_snapshot() -> None:
    output = _output()
    evaluator = AnalysisGoalEvaluator(
        expected_evidence_snapshot_id="evidence-" + "0" * 64
    )

    report = evaluator(criteria_for(CompletionTarget.DRAFT), output, _context())
    result = {item.criterion_id: item for item in report.evaluations}

    assert result["G-evidence-integrity"].outcome is CriterionOutcome.FAIL
    assert "frozen evidence" in result["G-evidence-integrity"].explanation


def test_publishable_target_pauses_for_content_bound_human_approvals() -> None:
    output = _output()
    snapshot = evidence_snapshot_id(output["ledger"].evidence)  # type: ignore[union-attr]
    evaluator = AnalysisGoalEvaluator(expected_evidence_snapshot_id=snapshot)

    report = evaluator(criteria_for(CompletionTarget.PUBLISHABLE), output, _context())
    result = {item.criterion_id: item for item in report.evaluations}

    assert result["G-human-approval"].outcome is CriterionOutcome.HUMAN_REQUIRED

