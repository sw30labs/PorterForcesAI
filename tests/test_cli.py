from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage
from typer.testing import CliRunner

from porter_forces_ai.cli import _CanaryResponse, _run_model_canaries, app
from porter_forces_ai.domain import DecisionRequest, OrganizationArchetype
from porter_forces_ai.service import AnalysisMode, AnalysisService, AnalysisSubmission
from porter_forces_ai.settings import Settings

runner = CliRunner()


class StructuredInvoker:
    def invoke(self, prompt: str) -> _CanaryResponse:
        return _CanaryResponse(status="ok")


class CanaryModel:
    def __init__(self, tool_calls: list[dict[str, Any]]) -> None:
        self.tool_calls = tool_calls

    def bind_tools(self, tools: list[Any], tool_choice: str) -> CanaryModel:
        return self

    def invoke(self, prompt: str) -> AIMessage:
        return AIMessage(content="", tool_calls=self.tool_calls)

    def with_structured_output(self, schema: type[Any], method: str) -> StructuredInvoker:
        return StructuredInvoker()


def test_model_canary_requires_exact_tool_name_and_arguments(monkeypatch: Any) -> None:
    valid = CanaryModel(
        [
            {
                "name": "capability_probe",
                "args": {"value": "ok"},
                "id": "call-1",
                "type": "tool_call",
            }
        ]
    )
    monkeypatch.setattr("porter_forces_ai.cli.create_chat_model", lambda settings: valid)
    assert _run_model_canaries(Settings()) == {
        "tool_calling": True,
        "structured_output": True,
    }

    wrong = CanaryModel(
        [
            {
                "name": "different_tool",
                "args": {"value": "ok"},
                "id": "call-2",
                "type": "tool_call",
            }
        ]
    )
    monkeypatch.setattr("porter_forces_ai.cli.create_chat_model", lambda settings: wrong)
    assert _run_model_canaries(Settings())["tool_calling"] is False


def test_run_command_executes_canonical_demo_file(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    request_path = Path(__file__).parents[1] / "examples" / "global-bank-ai-adoption.demo.json"
    monkeypatch.setenv("PFA_DATABASE_PATH", str(tmp_path / "cli.db"))
    monkeypatch.setenv("PFA_ARTIFACTS_DIR", str(tmp_path / "artifacts"))

    result = runner.invoke(app, ["run", str(request_path), "--json"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["status"] == "achieved_draft"
    assert payload["ralph_state"]["status"] == "achieved_draft"
    assert payload["scenario_economics"][0]["base_discounted_payback_month"] is not None
    assert Path(payload["artifact_paths"]["board_memo"]).exists()


def test_show_and_runs_remain_read_only_while_api_writer_is_alive(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    monkeypatch.setenv("PFA_DATABASE_PATH", str(tmp_path / "live-writer.db"))
    monkeypatch.setenv("PFA_ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    writer = AnalysisService(Settings())
    try:
        completed = writer.analyze(
            AnalysisSubmission(
                mode=AnalysisMode.DEMO,
                request=DecisionRequest(
                    question="Should the board authorize one bounded AI workflow?",
                    archetype=OrganizationArchetype.GLOBAL_BANK,
                    public_research_context=(
                        "Synthetic offline context for a read-only CLI projection."
                    ),
                ),
            )
        )

        shown = runner.invoke(app, ["show", completed.run_id, "--json"])
        assert shown.exit_code == 0, shown.output
        assert json.loads(shown.output)["run_id"] == completed.run_id

        listed = runner.invoke(app, ["runs", "--json"])
        assert listed.exit_code == 0, listed.output
        assert json.loads(listed.output)[0]["run_id"] == completed.run_id
    finally:
        writer.close()


def test_show_and_runs_do_not_create_an_uninitialized_database(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    database_path = tmp_path / "missing" / "not-created.db"
    monkeypatch.setenv("PFA_DATABASE_PATH", str(database_path))

    listed = runner.invoke(app, ["runs"])
    assert listed.exit_code == 0
    assert listed.output == "No persisted runs.\n"

    shown = runner.invoke(app, ["show", "RUN-missing"])
    assert shown.exit_code == 1
    assert "run has no completed analysis result" in shown.output
    assert not database_path.exists()


def test_analysis_cli_reports_writer_lease_conflict_without_traceback(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    monkeypatch.setenv("PFA_DATABASE_PATH", str(tmp_path / "owned.db"))
    writer = AnalysisService(Settings())
    try:
        result = runner.invoke(app, ["demo"])
    finally:
        writer.close()

    assert result.exit_code == 1
    assert "FAILED:" in result.output
    assert "already owns the writer lease" in result.output
    assert result.exception is not None
    assert "Traceback" not in result.output


def test_serve_reports_writer_lease_conflict_without_traceback(
    tmp_path: Path,
    monkeypatch: Any,
) -> None:
    monkeypatch.setenv("PFA_DATABASE_PATH", str(tmp_path / "owned-serve.db"))
    monkeypatch.setenv("PFA_API_PORT", "8765")

    def load_factory(*_args: Any, **_kwargs: Any) -> None:
        from porter_forces_ai.web import create_app

        create_app()

    monkeypatch.setattr("uvicorn.run", load_factory)
    writer = AnalysisService(Settings())
    try:
        result = runner.invoke(app, ["serve"])
    finally:
        writer.close()

    assert result.exit_code == 1
    assert "FAILED:" in result.output
    assert "writer lease" in result.output
    assert result.exception is not None
    assert "Traceback" not in result.output


def test_help_exposes_runtime_commands() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    for command in ("demo", "analyze", "run", "runs", "show", "serve", "doctor"):
        assert command in result.output
