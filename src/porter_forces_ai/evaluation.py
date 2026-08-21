"""Independent deterministic acceptance criteria for the Ralph supervisor."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import Any

from pydantic import BaseModel

from porter_forces_ai.domain import (
    FORCE_ORDER,
    ApprovalRecord,
    BoardBrief,
    DecisionFrame,
    EvidenceItem,
    EvidenceLedger,
)
from porter_forces_ai.quality import evaluate_brief
from porter_forces_ai.ralph import (
    AttemptContext,
    CompletionTarget,
    CriterionEvaluation,
    CriterionOutcome,
    GoalCriterion,
    GoalReport,
)


def evidence_snapshot_id(evidence: Sequence[EvidenceItem]) -> str:
    """Return the immutable identity of an ordered, canonical evidence universe."""

    rows = sorted(
        (item.model_dump(mode="json") for item in evidence),
        key=lambda item: str(item["evidence_id"]),
    )
    canonical = json.dumps(
        rows,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return f"evidence-{hashlib.sha256(canonical).hexdigest()}"


DRAFT_CRITERIA: tuple[GoalCriterion, ...] = (
    GoalCriterion(
        criterion_id="G-decision-contract",
        description="The decision contract is complete enough to support research.",
        verification_method="Validate the typed frame and its research-ready state.",
    ),
    GoalCriterion(
        criterion_id="G-five-forces",
        description="Every Porter force has one complete, mathematically valid assessment.",
        verification_method="Validate force identity, count, drivers, and normalized weights.",
    ),
    GoalCriterion(
        criterion_id="G-evidence-integrity",
        description="Material board claims pass provenance and captured-evidence gates.",
        verification_method="Run the deterministic quality gate on the immutable snapshot.",
    ),
    GoalCriterion(
        criterion_id="G-board-decision",
        description="The brief answers action, no-action, uncertainty, and commitment questions.",
        verification_method="Validate mandatory BoardBrief decision fields and option coverage.",
    ),
)

PUBLICATION_CRITERION = GoalCriterion(
    criterion_id="G-human-approval",
    description="Strategy, finance, technology, and risk approve the exact brief content.",
    verification_method="Verify immutable content-bound approvals for every required role.",
    retryable=False,
)


def criteria_for(target: CompletionTarget) -> tuple[GoalCriterion, ...]:
    """Return explicit criteria for the requested machine or human completion level."""

    if target is CompletionTarget.PUBLISHABLE:
        return (*DRAFT_CRITERIA, PUBLICATION_CRITERION)
    return DRAFT_CRITERIA


class AnalysisGoalEvaluator:
    """Evaluate graph artifacts without asking the generating model if it is done."""

    def __init__(
        self,
        *,
        expected_evidence_snapshot_id: str,
        approvals: Sequence[ApprovalRecord] = (),
    ) -> None:
        self.expected_evidence_snapshot_id = expected_evidence_snapshot_id
        self.approvals = tuple(approvals)

    def __call__(
        self,
        criteria: tuple[GoalCriterion, ...],
        output: Mapping[str, Any],
        context: AttemptContext,
    ) -> GoalReport:
        evaluations = tuple(
            self._evaluate(criterion.criterion_id, output)
            for criterion in criteria
        )
        passed = sum(item.outcome is CriterionOutcome.PASS for item in evaluations)
        return GoalReport(
            attempt_id=context.attempt_id,
            evaluations=evaluations,
            summary=f"Deterministic acceptance: {passed}/{len(evaluations)} criteria passed.",
            budget_units_used=1,
        )

    def _evaluate(
        self,
        criterion_id: str,
        output: Mapping[str, Any],
    ) -> CriterionEvaluation:
        try:
            if criterion_id == "G-decision-contract":
                return self._decision_contract(output)
            if criterion_id == "G-five-forces":
                return self._five_forces(output)
            if criterion_id == "G-evidence-integrity":
                return self._evidence_integrity(output)
            if criterion_id == "G-board-decision":
                return self._board_decision(output)
            if criterion_id == "G-human-approval":
                return self._human_approval(output)
        except (KeyError, TypeError, ValueError) as exc:
            return self._fail(
                criterion_id,
                f"Artifact validation failed: {exc}",
                "Regenerate the malformed or missing typed artifact.",
                marker=f"malformed:{type(exc).__name__}",
            )
        return self._fail(
            criterion_id,
            "The evaluator does not recognize this criterion.",
            "Use an application-owned acceptance criterion.",
            marker="unknown-criterion",
        )

    def _decision_contract(self, output: Mapping[str, Any]) -> CriterionEvaluation:
        frame = self._model(output, "decision_frame", DecisionFrame)
        if not frame.ready_for_research or frame.clarification_questions:
            return self._fail(
                "G-decision-contract",
                "The decision contract still requires clarification.",
                "Resolve the clarification questions and retain an explicit current-course option.",
                marker="not-research-ready",
            )
        return self._pass("G-decision-contract", "The decision contract is research-ready.")

    def _five_forces(self, output: Mapping[str, Any]) -> CriterionEvaluation:
        brief = self._model(output, "board_brief", BoardBrief)
        forces = [item.force for item in brief.force_assessments]
        if len(forces) != 5 or set(forces) != set(FORCE_ORDER):
            return self._fail(
                "G-five-forces",
                "The brief does not contain exactly one valid assessment per force.",
                "Regenerate only missing or duplicate force assessments.",
                marker=":".join(sorted(item.value for item in forces)),
            )
        return self._pass("G-five-forces", "All five typed force assessments are present.")

    def _evidence_integrity(self, output: Mapping[str, Any]) -> CriterionEvaluation:
        frame = self._model(output, "decision_frame", DecisionFrame)
        ledger = self._model(output, "ledger", EvidenceLedger)
        brief = self._model(output, "board_brief", BoardBrief)
        actual_snapshot_id = evidence_snapshot_id(ledger.evidence)
        if actual_snapshot_id != self.expected_evidence_snapshot_id:
            return self._fail(
                "G-evidence-integrity",
                "The attempt changed the frozen evidence universe.",
                "Retry using the original captured evidence snapshot without searching again.",
                marker=actual_snapshot_id,
            )
        report = evaluate_brief(
            brief,
            ledger.evidence,
            ledger.claims,
            frame.assumptions,
            ledger.links,
            self.approvals,
        )
        if not report.draft_valid:
            codes = sorted(item.code for item in report.findings if item.severity == "error")
            return self._fail(
                "G-evidence-integrity",
                "The evidence and claim gates failed: " + ", ".join(codes),
                "Repair only the cited claim, link, or board-basis gaps; do not change evidence.",
                marker="quality:" + ":".join(codes),
            )
        return self._pass(
            "G-evidence-integrity",
            "The brief passes deterministic provenance and evidence-utility gates.",
        )

    def _board_decision(self, output: Mapping[str, Any]) -> CriterionEvaluation:
        brief = self._model(output, "board_brief", BoardBrief)
        option_names = " ".join(item.name.casefold() for item in brief.options)
        has_no_action = any(
            token in option_names
            for token in ("no action", "current course", "status quo", "do nothing")
        )
        if not has_no_action or not brief.no_action_case or not brief.smallest_sensible_commitment:
            return self._fail(
                "G-board-decision",
                "The brief omits a mandatory decision comparison.",
                "State no-action consequences and the smallest reversible commitment explicitly.",
                marker="missing-decision-comparison",
            )
        return self._pass(
            "G-board-decision",
            "The brief makes action, no-action, uncertainty, and commitment explicit.",
        )

    def _human_approval(self, output: Mapping[str, Any]) -> CriterionEvaluation:
        frame = self._model(output, "decision_frame", DecisionFrame)
        ledger = self._model(output, "ledger", EvidenceLedger)
        brief = self._model(output, "board_brief", BoardBrief)
        report = evaluate_brief(
            brief,
            ledger.evidence,
            ledger.claims,
            frame.assumptions,
            ledger.links,
            self.approvals,
        )
        if report.publishable:
            return self._pass(
                "G-human-approval",
                "All required roles approved the exact current brief fingerprint.",
            )
        return CriterionEvaluation(
            criterion_id="G-human-approval",
            outcome=CriterionOutcome.HUMAN_REQUIRED,
            explanation=(
                "The draft is valid but content-bound publication approvals are incomplete."
            ),
            remediation=(
                "Obtain strategy, finance, technology, and risk decisions for the exact brief hash."
            ),
            progress_marker="approvals-incomplete",
        )

    @staticmethod
    def _model(
        output: Mapping[str, Any],
        key: str,
        schema: type[BaseModel],
    ) -> Any:
        value = output[key]
        return schema.model_validate(
            value.model_dump(mode="python") if isinstance(value, BaseModel) else value
        )

    @staticmethod
    def _pass(criterion_id: str, explanation: str) -> CriterionEvaluation:
        return CriterionEvaluation(
            criterion_id=criterion_id,
            outcome=CriterionOutcome.PASS,
            explanation=explanation,
        )

    @staticmethod
    def _fail(
        criterion_id: str,
        explanation: str,
        remediation: str,
        *,
        marker: str,
    ) -> CriterionEvaluation:
        return CriterionEvaluation(
            criterion_id=criterion_id,
            outcome=CriterionOutcome.FAIL,
            explanation=explanation,
            remediation=remediation,
            progress_marker=marker[:500],
        )
