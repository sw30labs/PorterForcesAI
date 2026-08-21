from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage
from typer.testing import CliRunner

from porter_forces_ai.cli import _CanaryResponse, _run_model_canaries, app
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


def test_help_exposes_runtime_commands() -> None:
    result = runner.invoke(app, ["--help"])

    assert result.exit_code == 0
    for command in ("demo", "analyze", "run", "runs", "show", "serve", "doctor"):
        assert command in result.output
