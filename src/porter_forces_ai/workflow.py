"""Deterministic LangGraph control plane.

The graph owns ordering, fan-out/fan-in, state, and bounded repair. A runtime
owns model prompts and source promotion. This makes the workflow testable with a
fake runtime and prevents a Deep Agent from quietly changing the business
process.
"""

from __future__ import annotations

import operator
from typing import Annotated, Any, Protocol, TypedDict

from langgraph.types import Overwrite, Send

from porter_forces_ai.domain import (
    FORCE_ORDER,
    BoardBrief,
    ChallengeReport,
    DecisionFrame,
    DecisionRequest,
    EvidenceLedger,
    ForceAssessment,
    ForceName,
    PublicResearchAssignment,
    ResearchBundle,
)
from porter_forces_ai.egress import EgressPolicy, EgressViolation
from porter_forces_ai.quality import QualityReport, evaluate_brief


class DecisionScopeError(RuntimeError):
    """The decision contract is too ambiguous to begin public research."""


class ForceResearchState(TypedDict):
    assignment: PublicResearchAssignment
    egress_policy: EgressPolicy


class ForceAssessmentState(TypedDict):
    request: DecisionRequest
    decision_frame: DecisionFrame
    ledger: EvidenceLedger
    force: ForceName


class AnalysisState(TypedDict, total=False):
    request: DecisionRequest
    decision_frame: DecisionFrame
    research_bundles: Annotated[list[ResearchBundle], operator.add]
    ledger: EvidenceLedger
    force_assessments: Annotated[list[ForceAssessment], operator.add]
    board_brief: BoardBrief
    challenge_report: ChallengeReport
    quality_report: QualityReport
    repair_count: int


class AdvisorRuntime(Protocol):
    """Application services invoked by deterministic graph nodes."""

    def frame_decision(self, request: DecisionRequest) -> DecisionFrame: ...

    def research_force(
        self,
        assignment: PublicResearchAssignment,
        egress_policy: EgressPolicy,
    ) -> ResearchBundle: ...

    def build_evidence_ledger(
        self,
        request: DecisionRequest,
        frame: DecisionFrame,
        bundles: list[ResearchBundle],
    ) -> EvidenceLedger: ...

    def assess_force(
        self,
        request: DecisionRequest,
        frame: DecisionFrame,
        ledger: EvidenceLedger,
        force: ForceName,
    ) -> ForceAssessment: ...

    def compose_board_brief(
        self,
        request: DecisionRequest,
        frame: DecisionFrame,
        ledger: EvidenceLedger,
        assessments: list[ForceAssessment],
    ) -> BoardBrief: ...

    def challenge(
        self,
        request: DecisionRequest,
        frame: DecisionFrame,
        ledger: EvidenceLedger,
        brief: BoardBrief,
    ) -> ChallengeReport: ...

    def revise_board_brief(
        self,
        brief: BoardBrief,
        challenge: ChallengeReport,
        quality: QualityReport | None,
    ) -> BoardBrief: ...


def build_workflow(
    runtime: AdvisorRuntime,
    *,
    checkpointer: Any | None = None,
    max_quality_repairs: int = 1,
) -> Any:
    """Compile the outer graph; callers supply persistence appropriate to the environment."""

    from langgraph.graph import END, START, StateGraph

    def frame_decision(state: AnalysisState) -> dict[str, Any]:
        return {
            "decision_frame": runtime.frame_decision(state["request"]),
            # A checkpoint thread may be reused for a fresh request. Reducer-backed
            # fan-in state must be replaced, not appended to the previous run.
            "research_bundles": Overwrite(value=[]),
            "force_assessments": Overwrite(value=[]),
            "repair_count": 0,
        }

    def confirm_scope(state: AnalysisState) -> dict[str, Any]:
        frame = state["decision_frame"]
        if not frame.ready_for_research:
            questions = "; ".join(frame.clarification_questions) or "no questions supplied"
            raise DecisionScopeError(f"decision contract is not research-ready: {questions}")
        request = state["request"]
        public_context_policy = EgressPolicy(
            forbidden_terms=tuple(request.restricted_terms),
            max_query_chars=4_000,
        )
        try:
            public_context_policy.validate(request.public_research_context)
        except EgressViolation as exc:
            raise DecisionScopeError(
                "public_research_context failed the outbound-data policy"
            ) from exc
        return {}

    def dispatch_research(state: AnalysisState) -> list[Send]:
        return [
            Send(
                "research_force",
                {
                    "assignment": PublicResearchAssignment(
                        force=force,
                        archetype=state["request"].archetype,
                        analysis_mode=state["request"].analysis_mode,
                        public_context=state["request"].public_research_context,
                        time_horizon_months=state["request"].time_horizon_months,
                        evidence_cutoff=state["request"].evidence_cutoff,
                    ),
                    # Kept out of the assignment so the model never learns the
                    # confidential strings it is forbidden to emit.
                    "egress_policy": EgressPolicy(
                        forbidden_terms=tuple(state["request"].restricted_terms)
                    ),
                },
            )
            for force in FORCE_ORDER
        ]

    def research_force(state: ForceResearchState) -> dict[str, Any]:
        assignment = state["assignment"]
        bundle = runtime.research_force(assignment, state["egress_policy"])
        if bundle.force != assignment.force:
            raise ValueError("research worker returned the wrong force")
        return {"research_bundles": [bundle]}

    def build_evidence(state: AnalysisState) -> dict[str, Any]:
        bundles = state.get("research_bundles", [])
        if len(bundles) != len(FORCE_ORDER) or {
            bundle.force for bundle in bundles
        } != set(FORCE_ORDER):
            raise ValueError("research fan-in must contain each force exactly once")
        ordered_bundles = sorted(
            bundles,
            key=lambda item: FORCE_ORDER.index(item.force),
        )
        ledger = runtime.build_evidence_ledger(
            state["request"],
            state["decision_frame"],
            ordered_bundles,
        )
        return {"ledger": ledger}

    def dispatch_assessments(state: AnalysisState) -> list[Send]:
        return [
            Send(
                "assess_force",
                {
                    "request": state["request"],
                    "decision_frame": state["decision_frame"],
                    "ledger": state["ledger"],
                    "force": force,
                },
            )
            for force in FORCE_ORDER
        ]

    def assess_force(state: ForceAssessmentState) -> dict[str, Any]:
        assessment = runtime.assess_force(
            state["request"],
            state["decision_frame"],
            state["ledger"],
            state["force"],
        )
        if assessment.force != state["force"]:
            raise ValueError("force analyst returned the wrong force")
        return {"force_assessments": [assessment]}

    def compose(state: AnalysisState) -> dict[str, Any]:
        assessments = state.get("force_assessments", [])
        if len(assessments) != len(FORCE_ORDER) or {
            assessment.force for assessment in assessments
        } != set(FORCE_ORDER):
            raise ValueError("assessment fan-in must contain each force exactly once")
        ordered = sorted(assessments, key=lambda item: FORCE_ORDER.index(item.force))
        return {
            "board_brief": runtime.compose_board_brief(
                state["request"],
                state["decision_frame"],
                state["ledger"],
                ordered,
            )
        }

    def challenge(state: AnalysisState) -> dict[str, Any]:
        return {
            "challenge_report": runtime.challenge(
                state["request"],
                state["decision_frame"],
                state["ledger"],
                state["board_brief"],
            )
        }

    def incorporate_challenge(state: AnalysisState) -> dict[str, Any]:
        return {
            "board_brief": runtime.revise_board_brief(
                state["board_brief"],
                state["challenge_report"],
                None,
            )
        }

    def quality_gate(state: AnalysisState) -> dict[str, Any]:
        ledger = state["ledger"]
        report = evaluate_brief(
            state["board_brief"],
            ledger.evidence,
            ledger.claims,
            state["decision_frame"].assumptions,
            ledger.links,
        )
        return {"quality_report": report}

    def route_after_quality(state: AnalysisState) -> str:
        if state["quality_report"].draft_valid:
            return "done"
        if state.get("repair_count", 0) < max_quality_repairs:
            return "repair"
        return "done"

    def repair(state: AnalysisState) -> dict[str, Any]:
        return {
            "board_brief": runtime.revise_board_brief(
                state["board_brief"],
                state["challenge_report"],
                state["quality_report"],
            ),
            "repair_count": state.get("repair_count", 0) + 1,
        }

    builder = StateGraph(AnalysisState)
    builder.add_node("frame_decision", frame_decision)
    builder.add_node("confirm_scope", confirm_scope)
    builder.add_node("research_force", research_force)
    builder.add_node("build_evidence", build_evidence)
    builder.add_node("assess_force", assess_force)
    builder.add_node("compose", compose)
    builder.add_node("challenge", challenge)
    builder.add_node("incorporate_challenge", incorporate_challenge)
    builder.add_node("quality_gate", quality_gate)
    builder.add_node("repair", repair)

    builder.add_edge(START, "frame_decision")
    builder.add_edge("frame_decision", "confirm_scope")
    builder.add_conditional_edges("confirm_scope", dispatch_research, ["research_force"])
    builder.add_edge("research_force", "build_evidence")
    builder.add_conditional_edges("build_evidence", dispatch_assessments, ["assess_force"])
    builder.add_edge("assess_force", "compose")
    builder.add_edge("compose", "challenge")
    builder.add_edge("challenge", "incorporate_challenge")
    builder.add_edge("incorporate_challenge", "quality_gate")
    builder.add_conditional_edges(
        "quality_gate",
        route_after_quality,
        {"repair": "repair", "done": END},
    )
    builder.add_edge("repair", "quality_gate")
    return builder.compile(checkpointer=checkpointer)
