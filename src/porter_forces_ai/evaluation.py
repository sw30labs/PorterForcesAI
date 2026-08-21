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
    ChallengeReport,
    DecisionFrame,
    DecisionRequest,
    EvidenceItem,
    EvidenceLedger,
    EvidenceStance,
)
from porter_forces_ai.economics import CostOfDelayResult, ScenarioEconomicsResult
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
    GoalCriterion(
        criterion_id="G-audience-analogy",
        description=(
            "Every requested board audience has a typed analogy with correspondences and limits."
        ),
        verification_method="Match typed analogies to the request's audience roles.",
    ),
    GoalCriterion(
        criterion_id="G-request-fidelity",
        description="The frame and brief answer the user's exact decision and market boundary.",
        verification_method="Compare normalized request text with typed decision artifacts.",
    ),
    GoalCriterion(
        criterion_id="G-counterevidence",
        description=(
            "The challenge and dissent expose a traceable basis explicitly labeled contrary."
        ),
        verification_method=(
            "Validate disconfirming IDs, stance, and board-visible dissent lineage."
        ),
    ),
)

ECONOMICS_CRITERION = GoalCriterion(
    criterion_id="G-economics-coherence",
    description=(
        "Recommendations and challenge are coherent with immutable finance-owned calculations."
    ),
    verification_method=(
        "Verify the calculated snapshot, scenario-option mapping, explicit economics treatment, "
        "and conservative handling of wholly negative cases."
    ),
)

PUBLICATION_CRITERION = GoalCriterion(
    criterion_id="G-human-approval",
    description="Strategy, finance, technology, and risk approve the exact brief content.",
    verification_method="Verify immutable content-bound approvals for every required role.",
    retryable=False,
)


def criteria_for(
    target: CompletionTarget,
    *,
    include_economics: bool = False,
) -> tuple[GoalCriterion, ...]:
    """Return explicit criteria for the requested machine or human completion level."""

    machine_criteria = (
        (*DRAFT_CRITERIA, ECONOMICS_CRITERION)
        if include_economics
        else DRAFT_CRITERIA
    )
    if target is CompletionTarget.PUBLISHABLE:
        return (*machine_criteria, PUBLICATION_CRITERION)
    return machine_criteria


class AnalysisGoalEvaluator:
    """Evaluate graph artifacts without asking the generating model if it is done."""

    def __init__(
        self,
        *,
        expected_evidence_snapshot_id: str,
        request: DecisionRequest,
        scenario_economics: Sequence[ScenarioEconomicsResult] = (),
        cost_of_delay: CostOfDelayResult | None = None,
        approvals: Sequence[ApprovalRecord] = (),
    ) -> None:
        self.expected_evidence_snapshot_id = expected_evidence_snapshot_id
        self.request = DecisionRequest.model_validate(request.model_dump(mode="python"))
        self.scenario_economics = tuple(
            ScenarioEconomicsResult.model_validate(item.model_dump(mode="python"))
            for item in scenario_economics
        )
        self.cost_of_delay = (
            CostOfDelayResult.model_validate(cost_of_delay.model_dump(mode="python"))
            if cost_of_delay is not None
            else None
        )
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
            if criterion_id == "G-audience-analogy":
                return self._audience_analogy(output)
            if criterion_id == "G-request-fidelity":
                return self._request_fidelity(output)
            if criterion_id == "G-counterevidence":
                return self._counterevidence(output)
            if criterion_id == "G-economics-coherence":
                return self._economics_coherence(output)
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

    def _audience_analogy(self, output: Mapping[str, Any]) -> CriterionEvaluation:
        brief = self._model(output, "board_brief", BoardBrief)
        requested = set(self.request.audience)
        covered = {analogy.audience for analogy in brief.analogies}
        missing = requested - covered
        if missing:
            labels = ", ".join(sorted(item.value for item in missing))
            return self._fail(
                "G-audience-analogy",
                "The brief lacks a bounded analogy for requested audiences: " + labels,
                (
                    "Add one faithful analogy per missing audience and state where each "
                    "analogy breaks."
                ),
                marker="missing-audiences:" + labels,
            )
        if any(
            self._normalize(analogy.unfamiliar_concept)
            == self._normalize(analogy.familiar_mechanism)
            for analogy in brief.analogies
        ):
            return self._fail(
                "G-audience-analogy",
                "An analogy merely repeats the unfamiliar concept instead of translating it.",
                "Use a distinct mechanism already familiar to the named board audience.",
                marker="tautological-analogy",
            )
        return self._pass(
            "G-audience-analogy",
            "Every requested audience has a typed analogy with a stated breaking point.",
        )

    def _request_fidelity(self, output: Mapping[str, Any]) -> CriterionEvaluation:
        frame = self._model(output, "decision_frame", DecisionFrame)
        brief = self._model(output, "board_brief", BoardBrief)
        expected_question = self._normalize(self.request.question)
        if self._normalize(frame.decision_statement) != expected_question:
            return self._fail(
                "G-request-fidelity",
                "The decision frame changed the user's decision question.",
                "Copy the user question verbatim; put interpretation in the bounded frame fields.",
                marker="decision-frame-drift",
            )
        if self._normalize(brief.decision_requested) != expected_question:
            return self._fail(
                "G-request-fidelity",
                "The board brief answers a different decision from the one requested.",
                "Copy the user question verbatim into decision_requested.",
                marker="board-question-drift",
            )
        if self.request.industry_arena and (
            self._normalize(frame.industry_boundary)
            != self._normalize(self.request.industry_arena)
        ):
            return self._fail(
                "G-request-fidelity",
                "The decision frame changed the user-owned market boundary.",
                (
                    "Preserve the supplied industry_arena exactly; clarify rather than silently "
                    "widen it."
                ),
                marker="market-boundary-drift",
            )
        return self._pass(
            "G-request-fidelity",
            "The frame and brief preserve the exact decision and supplied market boundary.",
        )

    def _counterevidence(self, output: Mapping[str, Any]) -> CriterionEvaluation:
        ledger = self._model(output, "ledger", EvidenceLedger)
        brief = self._model(output, "board_brief", BoardBrief)
        challenge = self._model(output, "challenge_report", ChallengeReport)
        claim_ids = {claim.claim_id for claim in ledger.claims}
        nominated = set(challenge.disconfirming_claim_ids)
        if not nominated:
            return self._fail(
                "G-counterevidence",
                "The independent challenge names no disconfirming claim.",
                "Cite a canonical contrary claim or a claim with captured contradictory evidence.",
                marker="missing-disconfirming-id",
            )
        unknown = nominated - claim_ids
        if unknown:
            return self._fail(
                "G-counterevidence",
                "The challenge cites non-canonical disconfirming claim IDs.",
                "Use only canonical claim IDs from the frozen evidence ledger.",
                marker="unknown-disconfirming:" + ":".join(sorted(unknown)),
            )
        contrary = {
            claim.claim_id
            for claim in ledger.claims
            if claim.stance is EvidenceStance.CONTRADICTS
        }
        contrary.update(
            link.claim_id
            for link in ledger.links
            if link.stance is EvidenceStance.CONTRADICTS
        )
        if not nominated & contrary:
            return self._fail(
                "G-counterevidence",
                "Claims labeled disconfirming have no contrary stance in the ledger.",
                "Do not relabel supporting evidence; capture or state a genuine contrary basis.",
                marker="false-counterevidence-label",
            )
        visible_basis = set(brief.dissenting_view.claim_ids)
        if not visible_basis & nominated:
            return self._fail(
                "G-counterevidence",
                "The board-visible dissent is disconnected from the independent contrary basis.",
                "Cite at least one nominated disconfirming claim in the dissenting view.",
                marker="dissent-lineage-missing",
            )
        return self._pass(
            "G-counterevidence",
            (
                "The challenge and board-visible dissent share a canonical basis with explicit "
                "contrary stance."
            ),
        )

    def _economics_coherence(self, output: Mapping[str, Any]) -> CriterionEvaluation:
        if not self.scenario_economics and self.cost_of_delay is None:
            return self._fail(
                "G-economics-coherence",
                "The economics criterion was requested without finance-owned calculations.",
                "Remove the conditional criterion or supply calculator-owned results.",
                marker="missing-owned-economics",
            )
        brief = self._model(output, "board_brief", BoardBrief)
        challenge = self._model(output, "challenge_report", ChallengeReport)
        actual_scenarios = tuple(
            ScenarioEconomicsResult.model_validate(
                item.model_dump(mode="python") if isinstance(item, BaseModel) else item
            )
            for item in output.get("scenario_economics", ())
        )
        raw_delay = output.get("cost_of_delay")
        actual_delay = (
            CostOfDelayResult.model_validate(
                raw_delay.model_dump(mode="python")
                if isinstance(raw_delay, BaseModel)
                else raw_delay
            )
            if raw_delay is not None
            else None
        )
        if actual_scenarios != self.scenario_economics or actual_delay != self.cost_of_delay:
            return self._fail(
                "G-economics-coherence",
                "The graph output changed or omitted the finance-owned calculation snapshot.",
                "Use the immutable calculator results; never ask the model to recompute them.",
                marker="economics-snapshot-drift",
            )

        option_names = {self._normalize(option.name) for option in brief.options}
        missing_scenarios = [
            item.scenario_name
            for item in self.scenario_economics
            if self._normalize(item.scenario_name) not in option_names
        ]
        if missing_scenarios:
            return self._fail(
                "G-economics-coherence",
                "Calculated scenarios are not mapped to board options: "
                + ", ".join(missing_scenarios),
                "Use each finance-owned scenario name verbatim as its corresponding option name.",
                marker="unmapped-scenarios:" + ":".join(sorted(missing_scenarios)),
            )

        brief_text = self._brief_text(brief)
        challenge_text = " ".join(
            [
                challenge.strongest_counterargument,
                *challenge.premortem,
                *challenge.invalidation_conditions,
                *challenge.required_changes,
            ]
        ).casefold()
        economics_terms = ("npv", "roi", "payback", "finance-owned", "economics")
        if self.scenario_economics and not any(term in brief_text for term in economics_terms):
            return self._fail(
                "G-economics-coherence",
                "The recommendation does not acknowledge the supplied scenario economics.",
                "Explain the finance-owned NPV, ROI, or payback implication without recalculating.",
                marker="economics-absent-from-brief",
            )
        if self.cost_of_delay is not None and "cost of delay" not in brief_text:
            return self._fail(
                "G-economics-coherence",
                "The no-action comparison omits the calculated cost of delay.",
                "State the owner-calculated cost-of-delay implication and the benefit of waiting.",
                marker="cost-of-delay-absent",
            )
        if not any(term in challenge_text for term in (*economics_terms, "cost of delay")):
            return self._fail(
                "G-economics-coherence",
                "The independent challenge does not stress-test the owned economics.",
                "Challenge NPV, ROI, payback, cost-of-delay, or their owner-supplied assumptions.",
                marker="economics-absent-from-challenge",
            )

        wholly_negative = bool(self.scenario_economics) and all(
            item.npv.high < 0 for item in self.scenario_economics
        )
        if wholly_negative:
            recommendation = brief.recommendation.text.casefold()
            conservative_markers = (
                "do not scale",
                "no action",
                "defer",
                "decline",
                "rescope",
                "re-scope",
                "wait",
            )
            if "negative" not in recommendation or not any(
                marker in recommendation for marker in conservative_markers
            ):
                return self._fail(
                    "G-economics-coherence",
                    "The recommendation obscures a wholly negative owned NPV range.",
                    "Say the range is negative and do not recommend scaling under that case.",
                    marker="negative-economics-obscured",
                )
            action_options = [
                option for option in brief.options if not self._is_no_action(option.name)
            ]
            if any(option.economic_attractiveness > 2 for option in action_options):
                return self._fail(
                    "G-economics-coherence",
                    (
                        "An action option is labeled economically attractive despite negative "
                        "upside NPV."
                    ),
                    "Score action economics at 2 or below until finance-owned inputs change.",
                    marker="negative-economics-score-conflict",
                )
        return self._pass(
            "G-economics-coherence",
            "The recommendation and challenge preserve and coherently use owned calculations.",
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
    def _normalize(value: str) -> str:
        return " ".join(value.casefold().split())

    @classmethod
    def _brief_text(cls, brief: BoardBrief) -> str:
        return " ".join(
            [
                brief.recommendation.text,
                *(item.text for item in brief.why_now),
                *(item.text for item in brief.no_action_case),
                *(item.text for item in brief.largest_uncertainties),
                brief.smallest_sensible_commitment.text,
                *(option.description for option in brief.options),
                *(item for option in brief.options for item in option.conditions_required),
                brief.dissenting_view.text,
            ]
        ).casefold()

    @staticmethod
    def _is_no_action(name: str) -> bool:
        normalized = name.casefold()
        return any(
            marker in normalized
            for marker in ("no action", "do nothing", "current course", "status quo")
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
