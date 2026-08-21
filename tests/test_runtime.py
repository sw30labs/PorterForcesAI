from porter_forces_ai.domain import (
    FORCE_ORDER,
    DecisionRequest,
    EvidenceOrigin,
    OrganizationArchetype,
)
from porter_forces_ai.runtime import DeterministicDemoRuntime
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

