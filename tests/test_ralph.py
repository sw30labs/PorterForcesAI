from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from pydantic import ValidationError

from porter_forces_ai.ralph import (
    AttemptContext,
    CompletionTarget,
    CriterionEvaluation,
    CriterionOutcome,
    GoalCriterion,
    GoalReport,
    RalphState,
    RalphStatus,
    RalphSupervisor,
)

CRITERIA = (
    GoalCriterion(
        criterion_id="G-evidence",
        description="Every material claim has usable supporting evidence.",
        verification_method="Run the deterministic evidence quality gate.",
    ),
    GoalCriterion(
        criterion_id="G-options",
        description="The board brief compares the mandatory strategic options.",
        verification_method="Inspect canonical option identifiers.",
    ),
)


class SequentialIds:
    def __init__(self) -> None:
        self.number = 0

    def __call__(self, prefix: str) -> str:
        self.number += 1
        return f"{prefix}-{self.number}"


class SequentialClock:
    def __init__(self) -> None:
        self.value = datetime(2026, 8, 20, tzinfo=UTC)

    def __call__(self) -> datetime:
        result = self.value
        self.value += timedelta(seconds=1)
        return result


class RecordingAnalysis:
    def __init__(self) -> None:
        self.calls: list[tuple[Mapping[str, Any], Mapping[str, Any]]] = []

    def invoke(
        self,
        input: Mapping[str, Any],
        config: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any]:
        assert config is not None
        self.calls.append((input, config))
        return {"draft": f"version-{len(self.calls)}", "self_declared_done": True}


class SequenceEvaluator:
    def __init__(self, outcomes: list[tuple[CriterionOutcome, CriterionOutcome]]) -> None:
        self.outcomes = outcomes
        self.calls = 0

    def __call__(
        self,
        criteria: tuple[GoalCriterion, ...],
        output: Mapping[str, Any],
        context: AttemptContext,
    ) -> GoalReport:
        del criteria, output
        pair = self.outcomes[self.calls]
        self.calls += 1
        evaluations = tuple(
            CriterionEvaluation(
                criterion_id=criterion_id,
                outcome=outcome,
                explanation="Deterministic fixture result",
                remediation=(
                    None
                    if outcome is CriterionOutcome.PASS
                    else f"Resolve {criterion_id} using verified inputs"
                ),
                progress_marker=("gap-v1" if outcome is CriterionOutcome.FAIL else None),
            )
            for criterion_id, outcome in zip(
                ("G-evidence", "G-options"),
                pair,
                strict=True,
            )
        )
        return GoalReport(
            attempt_id=context.attempt_id,
            evaluations=evaluations,
            summary="Offline fixture evaluation",
        )


def make_supervisor(
    analysis: RecordingAnalysis,
    evaluator: SequenceEvaluator,
    *,
    input_builder: Any = None,
) -> RalphSupervisor:
    kwargs: dict[str, Any] = {
        "id_factory": SequentialIds(),
        "clock": SequentialClock(),
    }
    if input_builder is not None:
        kwargs["input_builder"] = input_builder
    return RalphSupervisor(analysis, evaluator, **kwargs)


def initial_state(
    supervisor: RalphSupervisor,
    *,
    target: CompletionTarget = CompletionTarget.DRAFT,
    max_attempts: int = 4,
    max_budget_units: int = 12,
    stall_limit: int = 2,
) -> RalphState:
    return supervisor.new_state(
        objective={"request": "Should the bank adopt AI for KYC?"},
        criteria=CRITERIA,
        evidence_snapshot_id="snapshot-fixed-001",
        target=target,
        max_attempts=max_attempts,
        max_budget_units=max_budget_units,
        stall_limit=stall_limit,
    )


def test_success_comes_from_evaluator_not_analysis_output() -> None:
    analysis = RecordingAnalysis()
    evaluator = SequenceEvaluator([(CriterionOutcome.PASS, CriterionOutcome.PASS)])
    supervisor = make_supervisor(analysis, evaluator)

    result = supervisor.run(initial_state(supervisor))

    assert result.status is RalphStatus.ACHIEVED_DRAFT
    assert result.latest_output == {"draft": "version-1", "self_declared_done": True}
    assert len(result.attempts) == 1
    assert result.terminal_reason == "all required draft criteria passed"


def test_publishable_is_an_explicit_target() -> None:
    analysis = RecordingAnalysis()
    evaluator = SequenceEvaluator([(CriterionOutcome.PASS, CriterionOutcome.PASS)])
    supervisor = make_supervisor(analysis, evaluator)

    result = supervisor.run(
        initial_state(supervisor, target=CompletionTarget.PUBLISHABLE)
    )

    assert result.status is RalphStatus.PUBLISHABLE


def test_gap_directed_retry_uses_new_attempt_and_thread_then_succeeds() -> None:
    analysis = RecordingAnalysis()
    evaluator = SequenceEvaluator(
        [
            (CriterionOutcome.FAIL, CriterionOutcome.PASS),
            (CriterionOutcome.PASS, CriterionOutcome.PASS),
        ]
    )
    seen_contexts: list[AttemptContext] = []

    def build_input(
        objective: Mapping[str, Any],
        context: AttemptContext,
    ) -> Mapping[str, Any]:
        seen_contexts.append(context)
        return {
            **objective,
            "repair": [directive.model_dump() for directive in context.directives],
        }

    supervisor = make_supervisor(analysis, evaluator, input_builder=build_input)

    result = supervisor.run(initial_state(supervisor, stall_limit=3))

    assert result.status is RalphStatus.ACHIEVED_DRAFT
    assert len(result.attempts) == 2
    assert result.attempts[0].attempt_id != result.attempts[1].attempt_id
    assert result.attempts[0].thread_id != result.attempts[1].thread_id
    assert {
        attempt.evidence_snapshot_id for attempt in result.attempts
    } == {"snapshot-fixed-001"}
    assert seen_contexts[0].directives == ()
    assert seen_contexts[1].directives[0].criterion_id == "G-evidence"
    assert analysis.calls[0][1]["configurable"]["thread_id"] != (
        analysis.calls[1][1]["configurable"]["thread_id"]
    )


def test_human_required_pauses_without_retrying() -> None:
    analysis = RecordingAnalysis()
    evaluator = SequenceEvaluator(
        [(CriterionOutcome.HUMAN_REQUIRED, CriterionOutcome.PASS)]
    )
    supervisor = make_supervisor(analysis, evaluator)

    result = supervisor.run(initial_state(supervisor))

    assert result.status is RalphStatus.HUMAN_REQUIRED
    assert len(result.attempts) == 1
    assert "human judgment" in (result.terminal_reason or "")
    assert supervisor.run(result) is result


def test_machine_failure_retries_before_human_pause() -> None:
    analysis = RecordingAnalysis()
    evaluator = SequenceEvaluator(
        [
            (CriterionOutcome.FAIL, CriterionOutcome.HUMAN_REQUIRED),
            (CriterionOutcome.PASS, CriterionOutcome.HUMAN_REQUIRED),
        ]
    )
    supervisor = make_supervisor(analysis, evaluator)

    result = supervisor.run(initial_state(supervisor, stall_limit=3))

    assert result.status is RalphStatus.HUMAN_REQUIRED
    assert len(result.attempts) == 2
    assert len(analysis.calls) == 2
    assert result.attempts[1].directives_applied[0].criterion_id == "G-evidence"
    assert "human judgment" in (result.terminal_reason or "")


def test_mixed_machine_failure_and_human_requirement_obeys_attempt_bound() -> None:
    analysis = RecordingAnalysis()
    evaluator = SequenceEvaluator(
        [
            (CriterionOutcome.FAIL, CriterionOutcome.HUMAN_REQUIRED),
            (CriterionOutcome.FAIL, CriterionOutcome.HUMAN_REQUIRED),
        ]
    )
    supervisor = make_supervisor(analysis, evaluator)

    result = supervisor.run(
        initial_state(supervisor, max_attempts=2, stall_limit=3)
    )

    assert result.status is RalphStatus.BLOCKED
    assert len(result.attempts) == 2
    assert len(analysis.calls) == 2
    assert "maximum attempts" in (result.terminal_reason or "")


def test_max_attempts_blocks_open_goal() -> None:
    analysis = RecordingAnalysis()
    evaluator = SequenceEvaluator(
        [
            (CriterionOutcome.FAIL, CriterionOutcome.PASS),
            (CriterionOutcome.FAIL, CriterionOutcome.PASS),
        ]
    )
    supervisor = make_supervisor(analysis, evaluator)

    result = supervisor.run(
        initial_state(supervisor, max_attempts=2, stall_limit=3)
    )

    assert result.status is RalphStatus.BLOCKED
    assert len(result.attempts) == 2
    assert "maximum attempts" in (result.terminal_reason or "")


def test_repeated_gap_fingerprint_blocks_stalled_loop() -> None:
    analysis = RecordingAnalysis()
    evaluator = SequenceEvaluator(
        [
            (CriterionOutcome.FAIL, CriterionOutcome.PASS),
            (CriterionOutcome.FAIL, CriterionOutcome.PASS),
        ]
    )
    supervisor = make_supervisor(analysis, evaluator)

    result = supervisor.run(initial_state(supervisor, max_attempts=5, stall_limit=2))

    assert result.status is RalphStatus.BLOCKED
    assert len(result.attempts) == 2
    assert result.consecutive_gap_count == 2
    assert "fingerprint repeated" in (result.terminal_reason or "")


def test_budget_exhaustion_blocks_retry() -> None:
    analysis = RecordingAnalysis()
    evaluator = SequenceEvaluator([(CriterionOutcome.FAIL, CriterionOutcome.PASS)])
    supervisor = make_supervisor(analysis, evaluator)

    result = supervisor.run(initial_state(supervisor, max_budget_units=1))

    assert result.status is RalphStatus.BLOCKED
    assert result.consumed_budget_units == 1
    assert "budget exhausted" in (result.terminal_reason or "")


def test_evidence_snapshot_cannot_change_across_manifests() -> None:
    analysis = RecordingAnalysis()
    evaluator = SequenceEvaluator([(CriterionOutcome.PASS, CriterionOutcome.PASS)])
    supervisor = make_supervisor(analysis, evaluator)
    result = supervisor.run(initial_state(supervisor))
    payload = result.model_dump(mode="python")
    payload["evidence_snapshot_id"] = "snapshot-substituted"

    with pytest.raises(ValidationError, match="evidence snapshot cannot change"):
        RalphState.model_validate(payload)


def test_langgraph_meta_graph_uses_same_supervision_contract() -> None:
    analysis = RecordingAnalysis()
    evaluator = SequenceEvaluator(
        [
            (CriterionOutcome.FAIL, CriterionOutcome.PASS),
            (CriterionOutcome.PASS, CriterionOutcome.PASS),
        ]
    )
    supervisor = make_supervisor(analysis, evaluator)
    graph = supervisor.build_meta_graph()

    result = graph.invoke(
        {"ralph_state": initial_state(supervisor, stall_limit=3)},
        {"recursion_limit": 10},
    )

    assert result["ralph_state"].status is RalphStatus.ACHIEVED_DRAFT
    assert len(result["ralph_state"].attempts) == 2


def test_incomplete_evaluator_report_is_rejected() -> None:
    analysis = RecordingAnalysis()

    def incomplete_evaluator(
        criteria: tuple[GoalCriterion, ...],
        output: Mapping[str, Any],
        context: AttemptContext,
    ) -> GoalReport:
        del criteria, output
        return GoalReport(
            attempt_id=context.attempt_id,
            evaluations=(
                CriterionEvaluation(
                    criterion_id="G-evidence",
                    outcome=CriterionOutcome.PASS,
                    explanation="Only one criterion was evaluated",
                ),
            ),
            summary="Incomplete on purpose",
        )

    supervisor = RalphSupervisor(
        analysis,
        incomplete_evaluator,
        id_factory=SequentialIds(),
        clock=SequentialClock(),
    )

    with pytest.raises(ValueError, match="every criterion exactly once"):
        supervisor.run(initial_state(supervisor))
