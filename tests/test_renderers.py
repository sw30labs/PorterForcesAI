import json
from pathlib import Path

from porter_forces_ai.domain import DecisionRequest, OrganizationArchetype
from porter_forces_ai.renderers import render_board_memo, write_run_artifacts
from porter_forces_ai.runtime import DeterministicDemoRuntime
from porter_forces_ai.workflow import build_workflow


def _analysis() -> dict[str, object]:
    request = DecisionRequest(
        question="Should a regulated bank deploy a controlled AI workflow or wait?",
        archetype=OrganizationArchetype.GLOBAL_BANK,
        public_research_context=(
            "Public market information about AI adoption in regulated banking workflows."
        ),
    )
    return build_workflow(DeterministicDemoRuntime()).invoke(
        {"request": request, "research_bundles": [], "force_assessments": []}
    )


def test_board_memo_exposes_decision_no_action_uncertainty_and_demo_warning() -> None:
    analysis = _analysis()

    memo = render_board_memo(
        brief=analysis["board_brief"],  # type: ignore[arg-type]
        frame=analysis["decision_frame"],  # type: ignore[arg-type]
        ledger=analysis["ledger"],  # type: ignore[arg-type]
        quality=analysis["quality_report"],  # type: ignore[arg-type]
        mode="demo",
    )

    assert "Demonstration only" in memo
    assert "What if we do nothing?" in memo
    assert "When will we see ROI?" in memo
    assert "deliberately not invented" in memo
    assert "Where the analogy breaks" in memo


def test_run_artifacts_are_written_with_an_audit_sidecar(tmp_path: Path) -> None:
    analysis = _analysis()
    memo = render_board_memo(
        brief=analysis["board_brief"],  # type: ignore[arg-type]
        frame=analysis["decision_frame"],  # type: ignore[arg-type]
        ledger=analysis["ledger"],  # type: ignore[arg-type]
        quality=analysis["quality_report"],  # type: ignore[arg-type]
        mode="demo",
    )

    paths = write_run_artifacts(
        tmp_path,
        memo=memo,
        audit_payload=analysis,
        ledger=analysis["ledger"],  # type: ignore[arg-type]
    )

    assert set(paths) == {"board_memo", "audit_sidecar", "evidence_register"}
    assert "Board decision brief" in Path(paths["board_memo"]).read_text()
    sidecar = json.loads(Path(paths["audit_sidecar"]).read_text())
    assert sidecar["board_brief"]["run_id"] == "run-demo-ai-adoption"
    assert "evidence_id" in Path(paths["evidence_register"]).read_text()

