"""Developer-facing CLI for capability checks and, later, analysis runs."""

from __future__ import annotations

import json
from typing import Literal

import typer
from pydantic import BaseModel

from porter_forces_ai.adapters.omlx import (
    OmlxConfigurationError,
    create_chat_model,
    list_models,
)
from porter_forces_ai.settings import Settings

app = typer.Typer(no_args_is_help=True, help="Porter Five Forces development foundation")


class _CanaryResponse(BaseModel):
    status: Literal["ok"]


@app.callback()
def main() -> None:
    """Porter Five Forces development foundation."""


def _run_model_canaries(settings: Settings) -> dict[str, bool]:
    from langchain_core.tools import tool

    model = create_chat_model(settings)

    @tool
    def capability_probe(value: str) -> str:
        """Return the supplied capability-canary value."""

        return value

    tool_result = model.bind_tools(
        [capability_probe],
        tool_choice="capability_probe",
    ).invoke("Call capability_probe once with value='ok'.")
    tool_calls = getattr(tool_result, "tool_calls", [])
    tool_ok = False
    if len(tool_calls) == 1:
        call = tool_calls[0]
        arguments = call.get("args")
        tool_ok = (
            call.get("name") == "capability_probe"
            and arguments == {"value": "ok"}
            and capability_probe.invoke(arguments) == "ok"
        )

    structured = model.with_structured_output(
        _CanaryResponse,
        method="json_schema",
    ).invoke("Return status ok and no other information.")
    structured_ok = isinstance(structured, _CanaryResponse) and structured.status == "ok"
    return {"tool_calling": tool_ok, "structured_output": structured_ok}


@app.command()
def doctor(
    live_canary: bool = typer.Option(
        False,
        "--live-canary",
        help="Also make two small model calls to verify tools and JSON schema.",
    ),
    as_json: bool = typer.Option(False, "--json", help="Emit machine-readable output."),
) -> None:
    """Check Python configuration and the local oMLX model inventory."""

    settings = Settings()
    result: dict[str, object] = {
        "endpoint": settings.llm_base_url,
        "configured_model": settings.llm_model or None,
        "models": [],
        "selected_model_available": False,
        "canaries": None,
    }
    try:
        models = list_models(settings)
        result["models"] = models
        result["selected_model_available"] = bool(
            settings.llm_model and settings.llm_model in models
        )
        if live_canary:
            if not result["selected_model_available"]:
                raise OmlxConfigurationError(
                    "configured model is absent from the authenticated /v1/models response"
                )
            try:
                result["canaries"] = _run_model_canaries(settings)
            except Exception as exc:
                raise OmlxConfigurationError(
                    "selected model failed the tool-calling or JSON-schema canary"
                ) from exc
    except OmlxConfigurationError as exc:
        if as_json:
            typer.echo(json.dumps({**result, "ok": False, "error": str(exc)}, indent=2))
        else:
            typer.echo(f"FAILED: {exc}", err=True)
        raise typer.Exit(code=1) from exc

    ok = bool(result["selected_model_available"])
    if live_canary:
        canaries = result["canaries"]
        ok = ok and isinstance(canaries, dict) and all(bool(value) for value in canaries.values())
    result["ok"] = ok
    if as_json:
        typer.echo(json.dumps(result, indent=2))
    else:
        typer.echo(f"oMLX endpoint: {result['endpoint']}")
        typer.echo(f"Models visible: {len(result['models'])}")  # type: ignore[arg-type]
        typer.echo(f"Configured model available: {result['selected_model_available']}")
        if live_canary:
            typer.echo(f"Canaries: {result['canaries']}")
    if not ok:
        raise typer.Exit(code=1)


if __name__ == "__main__":
    app()
