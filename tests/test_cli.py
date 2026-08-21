from __future__ import annotations

from typing import Any

from langchain_core.messages import AIMessage

from porter_forces_ai.cli import _CanaryResponse, _run_model_canaries
from porter_forces_ai.settings import Settings


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
