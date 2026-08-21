from decimal import Decimal
from typing import Any

import pytest

from porter_forces_ai.domain import (
    FORCE_ORDER,
    DecisionFrame,
    DecisionRequest,
    EvidenceOrigin,
    ForceName,
    OrganizationArchetype,
    ResearchQuery,
    SearchHit,
)
from porter_forces_ai.economics import RangeEstimate, ScenarioEconomicsResult
from porter_forces_ai.runtime import (
    DeterministicDemoRuntime,
    OmlxAdvisorRuntime,
    RecordingSearchProvider,
    RuntimeContractError,
)
from porter_forces_ai.workflow import build_workflow


def _request() -> DecisionRequest:
    return DecisionRequest(
        question=(
            "Should a global bank authorize a controlled generative-AI workflow now, "
            "and what happens if it waits eighteen months?"
        ),
        archetype=OrganizationArchetype.GLOBAL_BANK,
        organization_name="Illustrative Global Bank",
        industry_arena="US and EU regulated banking knowledge workflows",
        geographies=["United States", "European Union"],
        public_research_context=(
            "Public information about generative-AI adoption, suppliers, customer behavior, "
            "substitution, entrants, and competitive investment in regulated banking."
        ),
    )


def test_demo_runtime_exercises_complete_graph_and_passes_draft_gate() -> None:
    graph = build_workflow(DeterministicDemoRuntime())

    result = graph.invoke(
        {"request": _request(), "research_bundles": [], "force_assessments": []},
        {"configurable": {"thread_id": "demo-test"}, "max_concurrency": 2},
    )

    assert result["quality_report"].draft_valid is True
    assert result["quality_report"].publishable is False
    assert {item.force for item in result["force_assessments"]} == set(FORCE_ORDER)
    assert all(
        item.origin == EvidenceOrigin.USER_PROVIDED
        and "demo" in (item.notes or "").casefold()
        for item in result["ledger"].evidence
    )
    brief = result["board_brief"]
    assert "controlled" in brief.recommendation.text.casefold()
    assert "synthetic" in brief.dissenting_view.text.casefold()
    assert brief.analogies[0].where_it_breaks


def test_demo_runtime_is_deterministic_except_for_timestamp() -> None:
    request = _request()
    first = build_workflow(DeterministicDemoRuntime("run-deterministic")).invoke(
        {"request": request, "research_bundles": [], "force_assessments": []}
    )
    second = build_workflow(DeterministicDemoRuntime("run-deterministic")).invoke(
        {"request": request, "research_bundles": [], "force_assessments": []}
    )
    first_brief = first["board_brief"].model_copy(update={"as_of": second["board_brief"].as_of})

    assert first_brief == second["board_brief"]


def test_demo_runtime_declines_scale_when_owned_economics_are_wholly_negative() -> None:
    negative = ScenarioEconomicsResult(
        scenario_name="Controlled workflow deployment",
        currency="USD",
        npv=RangeEstimate(
            low=Decimal("-50000000"),
            base=Decimal("-30000000"),
            high=Decimal("-10000000"),
            unit="USD",
            basis_ids=["FINANCE-OWNED-NEGATIVE-CASE"],
        ),
        undiscounted_roi=RangeEstimate(
            low=Decimal("-0.8"),
            base=Decimal("-0.5"),
            high=Decimal("-0.2"),
            unit="ratio",
            basis_ids=["FINANCE-OWNED-NEGATIVE-CASE"],
        ),
        base_discounted_payback_month=None,
        formulas=["Finance-owned deterministic test fixture"],
    )
    result = build_workflow(DeterministicDemoRuntime()).invoke(
        {
            "request": _request(),
            "research_bundles": [],
            "force_assessments": [],
            "scenario_economics": [negative],
            "cost_of_delay": None,
        }
    )

    brief = result["board_brief"]
    assert "wholly negative" in brief.recommendation.text.casefold()
    assert "do not scale" in brief.recommendation.text.casefold()
    assert "no production deployment" in brief.smallest_sensible_commitment.text.casefold()
    assert all(
        option.economic_attractiveness <= 2
        for option in brief.options
        if "no action" not in option.name.casefold()
    )


def test_omlx_compose_and_challenge_prompts_receive_owned_economics() -> None:
    negative = ScenarioEconomicsResult(
        scenario_name="Controlled workflow deployment",
        currency="USD",
        npv=RangeEstimate(
            low=Decimal("-50"),
            base=Decimal("-30"),
            high=Decimal("-10"),
            unit="USD",
            basis_ids=["FINANCE-OWNED-PROMPT-CASE"],
        ),
        undiscounted_roi=None,
        base_discounted_payback_month=None,
        formulas=["Finance-owned deterministic test fixture"],
    )
    graph_result = build_workflow(DeterministicDemoRuntime()).invoke(
        {
            "request": _request(),
            "research_bundles": [],
            "force_assessments": [],
            "scenario_economics": [negative],
            "cost_of_delay": None,
        }
    )

    class CapturingRuntime(OmlxAdvisorRuntime):
        def __init__(self) -> None:
            super().__init__(
                model=object(),
                search=object(),  # type: ignore[arg-type]
                run_id="run-demo-ai-adoption",
            )
            self.responses = [
                graph_result["board_brief"],
                graph_result["challenge_report"],
            ]
            self.prompts: list[tuple[str, str]] = []

        def _structured(self, schema: object, system: str, payload: str) -> object:
            del schema
            self.prompts.append((system, payload))
            return self.responses.pop(0)

    runtime = CapturingRuntime()
    brief = runtime.compose_board_brief(
        _request(),
        graph_result["decision_frame"],
        graph_result["ledger"],
        graph_result["force_assessments"],
        [negative],
        None,
    )
    runtime.challenge(
        _request(),
        graph_result["decision_frame"],
        graph_result["ledger"],
        brief,
        [negative],
        None,
    )

    compose_text = " ".join(runtime.prompts[0])
    challenge_text = " ".join(runtime.prompts[1])
    assert "Every supplied scenario has negative NPV" in compose_text
    assert '"scenario_name": "Controlled workflow deployment"' in compose_text
    assert "finance-owned NPV, ROI, payback" in challenge_text


def test_omlx_runtime_uses_strict_schema_transport_and_preserves_failure_cause() -> None:
    frame = DeterministicDemoRuntime().frame_decision(_request())
    calls: list[tuple[type[Any], str, list[dict[str, str]]]] = []

    class Invoker:
        def invoke(self, messages: list[dict[str, str]]) -> object:
            calls.append((DecisionFrame, "json_schema", messages))
            return frame

    class Model:
        def with_structured_output(
            self,
            schema: type[Any],
            method: str,
        ) -> Invoker:
            assert schema is DecisionFrame
            assert method == "json_schema"
            return Invoker()

    runtime = OmlxAdvisorRuntime(
        model=Model(),
        search=object(),  # type: ignore[arg-type]
        run_id="run-structured-contract",
    )

    result = runtime.frame_decision(_request())

    assert result.decision_statement == _request().question
    assert len(calls) == 1
    assert [message["role"] for message in calls[0][2]] == ["system", "user"]

    class FailingInvoker:
        def invoke(self, messages: list[dict[str, str]]) -> object:
            del messages
            raise ValueError("invalid structured response")

    class FailingModel:
        def with_structured_output(
            self,
            schema: type[Any],
            method: str,
        ) -> FailingInvoker:
            del schema, method
            return FailingInvoker()

    failing = OmlxAdvisorRuntime(
        model=FailingModel(),
        search=object(),  # type: ignore[arg-type]
        run_id="run-failed-structured-contract",
    )
    with pytest.raises(RuntimeContractError, match="failed to produce DecisionFrame") as exc:
        failing.frame_decision(_request())
    assert isinstance(exc.value.__cause__, ValueError)


def test_recording_search_stamps_force_lineage_before_durability_callback() -> None:
    class Search:
        def search(self, request: ResearchQuery | str) -> list[SearchHit]:
            assert isinstance(request, ResearchQuery)
            return [
                SearchHit(
                    query_id=request.query_id,
                    title="Observed result",
                    url="https://example.org/report",
                )
            ]

        async def asearch(self, request: ResearchQuery | str) -> list[SearchHit]:
            return self.search(request)

    persisted: list[tuple[ResearchQuery, tuple[SearchHit, ...]]] = []
    recorder = RecordingSearchProvider(
        Search(),
        force=ForceName.RIVALRY,
        on_execution=lambda query, hits: persisted.append((query, hits)),
    )

    returned = recorder.search(
        ResearchQuery(
            query_id="Q-agent-0001",
            query="banking AI competition",
            rationale="Find public rivalry evidence",
        )
    )

    assert len(persisted) == 1
    query, hits = persisted[0]
    assert query.query_id == "Q-competitive_rivalry-0001"
    assert query.force is ForceName.RIVALRY
    assert hits[0].query_id == query.query_id
    assert returned == list(hits)
    assert recorder.executions == persisted
