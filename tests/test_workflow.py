from datetime import UTC, datetime
from decimal import Decimal

from langgraph.checkpoint.memory import InMemorySaver

from porter_forces_ai.domain import (
    FORCE_ORDER,
    BoardBrief,
    BoardPoint,
    CausalHypothesis,
    ChallengeReport,
    Claim,
    ClaimEvidenceLink,
    ClaimKind,
    DecisionFrame,
    DecisionRequest,
    EvidenceItem,
    EvidenceLedger,
    EvidenceOrigin,
    EvidenceStance,
    ForceAssessment,
    ForceDriver,
    ForceName,
    Horizon,
    OrganizationArchetype,
    PublicResearchAssignment,
    ResearchBundle,
    ResearchQuery,
    SourceCandidate,
    SourceClass,
    StrategicOption,
    Trend,
)
from porter_forces_ai.economics import (
    CostOfDelayResult,
    RangeEstimate,
    ScenarioEconomicsResult,
)
from porter_forces_ai.egress import EgressPolicy, EgressViolation
from porter_forces_ai.quality import QualityReport
from porter_forces_ai.workflow import build_workflow


class FakeRuntime:
    def __init__(self) -> None:
        self.researched: list[ForceName] = []
        self.research_assignments: list[PublicResearchAssignment] = []
        self.assessed: list[ForceName] = []
        self.composed_economics: list[ScenarioEconomicsResult] = []
        self.composed_cost_of_delay: CostOfDelayResult | None = None
        self.challenged_economics: list[ScenarioEconomicsResult] = []

    def frame_decision(self, request: DecisionRequest) -> DecisionFrame:
        return DecisionFrame(
            decision_statement=request.question,
            industry_boundary="Global-bank KYC operations in the US and EU",
            baseline="Current analyst-led KYC investigation process",
            options=["No action", "Controlled deployment", "Scaled platform"],
            success_measures=["Realized cost per completed investigation"],
            ready_for_research=True,
        )

    def research_force(
        self,
        assignment: PublicResearchAssignment,
        egress_policy: EgressPolicy,
    ) -> ResearchBundle:
        force = assignment.force
        if egress_policy.forbidden_terms:
            try:
                egress_policy.validate("Project Cedar competitor response")
            except EgressViolation:
                pass
            else:
                raise AssertionError("run-confidential term reached the outbound boundary")
        self.researched.append(force)
        self.research_assignments.append(assignment)
        suffix = force.value
        return ResearchBundle(
            force=force,
            hypotheses=[
                CausalHypothesis(
                    hypothesis_id=f"H-{suffix}",
                    force=force,
                    driver="Test driver",
                    force_effect="Changes structural pressure",
                    economic_mechanism="Changes unit economics",
                    organization_exposure="Affects the focal organization",
                    observable_signals=["Test signal"],
                    falsification_condition="Signal moves in the opposite direction",
                )
            ],
            queries=[
                ResearchQuery(
                    query_id=f"Q-{suffix}",
                    force=force,
                    query=f"public evidence {suffix}",
                    rationale="Test graph fan-out",
                )
            ],
            source_candidates=[
                SourceCandidate(
                    url=f"https://example.com/{suffix}",
                    title="Test source",
                    why_relevant="Supports the test mechanism",
                    query_id=f"Q-{suffix}",
                )
            ],
        )

    def build_evidence_ledger(
        self,
        request: DecisionRequest,
        frame: DecisionFrame,
        bundles: list[ResearchBundle],
    ) -> EvidenceLedger:
        evidence: list[EvidenceItem] = []
        claims: list[Claim] = []
        links: list[ClaimEvidenceLink] = []
        for force in FORCE_ORDER:
            suffix = force.value
            evidence_id = f"E-{suffix}"
            claim_id = f"C-{suffix}"
            evidence.append(
                EvidenceItem(
                    evidence_id=evidence_id,
                    origin=EvidenceOrigin.USER_PROVIDED,
                    source_class=SourceClass.USER_ASSERTION,
                    title="Offline test fixture",
                    publisher="Test owner",
                    excerpt="A traceable fixture statement.",
                    quality_score=0.8,
                    freshness_score=1,
                    applicability_score=1,
                )
            )
            claims.append(
                Claim(
                    claim_id=claim_id,
                    statement=(
                        f"A traceable fixture statement supports the test inference for {suffix}"
                    ),
                    kind=ClaimKind.INFERENCE,
                    evidence_ids=[evidence_id],
                    confidence=0.6,
                )
            )
            links.append(
                ClaimEvidenceLink(
                    claim_id=claim_id,
                    evidence_id=evidence_id,
                    stance=EvidenceStance.SUPPORTS,
                    supporting_quote="A traceable fixture statement.",
                    entailment_score=1,
                    rationale="Fixture explicitly supports its test claim",
                )
            )
        return EvidenceLedger(evidence=evidence, claims=claims, links=links)

    def assess_force(
        self,
        request: DecisionRequest,
        frame: DecisionFrame,
        ledger: EvidenceLedger,
        force: ForceName,
    ) -> ForceAssessment:
        self.assessed.append(force)
        claim_id = f"C-{force.value}"
        return ForceAssessment(
            force=force,
            pressure_score=3,
            trend=Trend.STABLE,
            primary_horizon=Horizon.MEDIUM,
            confidence=0.6,
            drivers=[
                ForceDriver(
                    name="Test driver",
                    mechanism="Test mechanism",
                    pressure_score=3,
                    weight=1,
                    claim_ids=[claim_id],
                )
            ],
            evidence_for_claim_ids=[claim_id],
            organization_exposures=["Test exposure"],
            strategic_implications=["Test implication"],
            leading_indicators=["Test indicator"],
            conditions_that_change_conclusion=["Contrary test evidence"],
        )

    def compose_board_brief(
        self,
        request: DecisionRequest,
        frame: DecisionFrame,
        ledger: EvidenceLedger,
        assessments: list[ForceAssessment],
        scenario_economics: list[ScenarioEconomicsResult],
        cost_of_delay: CostOfDelayResult | None,
    ) -> BoardBrief:
        self.composed_economics = scenario_economics
        self.composed_cost_of_delay = cost_of_delay

        def option(name: str) -> StrategicOption:
            return StrategicOption(
                name=name,
                description=f"{name} description",
                external_necessity=3,
                capability_fit=3,
                economic_attractiveness=3,
                control_acceptability=3,
                reversibility=3,
                time_to_learning_months=6,
                claim_ids=[ledger.claims[0].claim_id],
                stop_conditions=[] if name == "No action" else ["Control test fails"],
                acceleration_conditions=(
                    [] if name == "No action" else ["Measured benefit clears the hurdle"]
                ),
            )

        first_claim = ledger.claims[0].claim_id
        return BoardBrief(
            run_id="run-test",
            as_of=datetime.now(UTC),
            decision_requested=frame.decision_statement,
            recommendation=BoardPoint(
                text="Use a controlled, stage-gated deployment.",
                claim_ids=[first_claim],
            ),
            why_now=[
                BoardPoint(
                    text="Create evidence through a reversible decision",
                    claim_ids=[first_claim],
                )
            ],
            no_action_case=[
                BoardPoint(
                    text="The baseline remains a valid option with measurable costs",
                    claim_ids=[first_claim],
                )
            ],
            largest_uncertainties=[
                BoardPoint(text="Benefit realization", claim_ids=[first_claim])
            ],
            smallest_sensible_commitment=BoardPoint(
                text="One controlled workflow",
                claim_ids=[first_claim],
            ),
            options=[option("No action"), option("Controlled deployment"), option("Scale")],
            force_assessments=assessments,
            material_claims=ledger.claims,
            board_questions=["Which result would justify scaling?"],
            dissenting_view=BoardPoint(
                text="Waiting may preserve option value.",
                claim_ids=[first_claim],
            ),
        )

    def challenge(
        self,
        request: DecisionRequest,
        frame: DecisionFrame,
        ledger: EvidenceLedger,
        brief: BoardBrief,
        scenario_economics: list[ScenarioEconomicsResult],
        cost_of_delay: CostOfDelayResult | None,
    ) -> ChallengeReport:
        self.challenged_economics = scenario_economics
        assert cost_of_delay is self.composed_cost_of_delay
        return ChallengeReport(
            strongest_counterargument="The evidence may justify waiting.",
            premortem=["Benefits never become booked savings"],
            invalidation_conditions=["Controls cost more than expected value"],
        )

    def revise_board_brief(
        self,
        brief: BoardBrief,
        challenge: ChallengeReport,
        quality: QualityReport | None,
    ) -> BoardBrief:
        return brief.model_copy(
            update={
                "dissenting_view": brief.dissenting_view.model_copy(
                    update={"text": challenge.strongest_counterargument}
                )
            }
        )


def test_graph_dispatches_and_reduces_exactly_five_forces() -> None:
    runtime = FakeRuntime()
    graph = build_workflow(runtime)
    request = DecisionRequest(
        question="Should a global bank deploy AI for KYC investigations?",
        archetype=OrganizationArchetype.GLOBAL_BANK,
        public_research_context=(
            "Public evidence about AI-enabled KYC operations at large regulated banks."
        ),
        restricted_terms=["Project Cedar"],
        internal_context={"program": "Project Cedar", "benefit_target": "$420 million"},
    )
    scenario = ScenarioEconomicsResult(
        scenario_name="Controlled deployment",
        currency="USD",
        npv=RangeEstimate(
            low=Decimal("-10"),
            base=Decimal("5"),
            high=Decimal("20"),
            unit="USD",
            basis_ids=["A-finance"],
        ),
        undiscounted_roi=None,
        base_discounted_payback_month=None,
        formulas=["calculator-owned test formula"],
    )
    delay = CostOfDelayResult(
        currency="USD",
        period_months=18,
        cost_of_delay=RangeEstimate(
            low=Decimal("1"),
            base=Decimal("2"),
            high=Decimal("3"),
            unit="USD",
            basis_ids=["A-finance"],
        ),
        formula="calculator-owned delay formula",
    )
    result = graph.invoke(
        {
            "request": request,
            "research_bundles": [],
            "force_assessments": [],
            "scenario_economics": [scenario],
            "cost_of_delay": delay,
        },
        {"configurable": {"thread_id": "test-run"}, "max_concurrency": 2},
    )

    assert set(runtime.researched) == set(FORCE_ORDER)
    assert set(runtime.assessed) == set(FORCE_ORDER)
    assert len(result["research_bundles"]) == 5
    assert len(result["force_assessments"]) == 5
    assert runtime.composed_economics == runtime.challenged_economics == [scenario]
    assert runtime.composed_cost_of_delay == delay
    assert result["scenario_economics"] == [scenario]
    assert result["cost_of_delay"] == delay
    assert result["quality_report"].draft_valid is True
    assert result["quality_report"].publishable is False
    assert all(
        set(assignment.model_dump())
        == {
            "force",
            "archetype",
            "analysis_mode",
            "public_context",
            "time_horizon_months",
            "evidence_cutoff",
        }
        for assignment in runtime.research_assignments
    )
    assert all(
        "Project Cedar" not in assignment.model_dump_json()
        and "$420 million" not in assignment.model_dump_json()
        for assignment in runtime.research_assignments
    )


def test_reusing_checkpoint_thread_resets_fan_in_state() -> None:
    runtime = FakeRuntime()
    graph = build_workflow(runtime, checkpointer=InMemorySaver())
    request = DecisionRequest(
        question="Should a global bank deploy AI for KYC investigations?",
        archetype=OrganizationArchetype.GLOBAL_BANK,
        public_research_context=(
            "Public evidence about AI-enabled KYC operations at large regulated banks."
        ),
    )
    config = {"configurable": {"thread_id": "reused-thread"}, "max_concurrency": 2}

    first = graph.invoke({"request": request}, config)
    second = graph.invoke({"request": request}, config)

    assert len(first["research_bundles"]) == len(FORCE_ORDER)
    assert len(second["research_bundles"]) == len(FORCE_ORDER)
    assert len(second["force_assessments"]) == len(FORCE_ORDER)
