"""Developer-facing CLI for capability checks and, later, analysis runs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Literal

import typer
from pydantic import BaseModel, ValidationError

from porter_forces_ai.adapters.omlx import (
    OmlxConfigurationError,
    create_chat_model,
    list_models,
)
from porter_forces_ai.settings import Settings

app = typer.Typer(
    no_args_is_help=True,
    help="Evidence-led Porter Five Forces and board decision intelligence",
)


class _CanaryResponse(BaseModel):
    status: Literal["ok"]


@app.callback()
def main() -> None:
    """Porter Five Forces board decision intelligence."""


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


@app.command()
def demo(
    question: str = typer.Option(
        (
            "Should a global bank authorize one controlled generative-AI workflow now, "
            "and what happens if it waits eighteen months?"
        ),
        "--question",
        "-q",
        help="Board decision to analyze using labeled synthetic evidence.",
    ),
    archetype: str = typer.Option(
        "global_bank",
        help="global_bank, quant_trading, or insurer",
    ),
    target: str = typer.Option("draft", help="draft or publishable"),
    as_json: bool = typer.Option(False, "--json", help="Emit the complete result as JSON."),
) -> None:
    """Run the complete graph offline with an explicitly synthetic evidence fixture."""

    from porter_forces_ai.domain import DecisionRequest, OrganizationArchetype
    from porter_forces_ai.ralph import CompletionTarget
    from porter_forces_ai.repository import RepositoryError
    from porter_forces_ai.service import (
        AnalysisMode,
        AnalysisService,
        AnalysisServiceError,
        AnalysisSubmission,
    )

    try:
        request = DecisionRequest(
            question=question,
            archetype=OrganizationArchetype(archetype),
            public_research_context=(
                "Synthetic offline demonstration of Porter Five Forces for a regulated "
                "financial-services technology decision."
            ),
        )
        submission = AnalysisSubmission(
            request=request,
            mode=AnalysisMode.DEMO,
            target=CompletionTarget(target),
        )
        with AnalysisService(Settings()) as service:
            result = service.analyze(submission)
    except (ValueError, ValidationError, RepositoryError, AnalysisServiceError) as exc:
        typer.echo(f"FAILED: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    _emit_result(result, as_json=as_json)


@app.command()
def analyze(
    request_file: Annotated[
        Path,
        typer.Argument(
            exists=True,
            dir_okay=False,
            readable=True,
            help="Canonical AnalysisSubmission JSON file.",
        ),
    ],
    as_json: bool = typer.Option(False, "--json", help="Emit the complete result as JSON."),
) -> None:
    """Run live local-oMLX analysis with DuckDuckGo and captured public evidence."""

    from porter_forces_ai.repository import RepositoryError
    from porter_forces_ai.service import (
        AnalysisMode,
        AnalysisService,
        AnalysisServiceError,
        AnalysisSubmission,
    )

    try:
        payload = json.loads(request_file.read_text(encoding="utf-8"))
        submission = AnalysisSubmission.model_validate(payload).model_copy(
            update={"mode": AnalysisMode.LIVE}
        )
        with AnalysisService(Settings()) as service:
            result = service.analyze(submission)
    except (
        OSError,
        json.JSONDecodeError,
        ValidationError,
        RepositoryError,
        AnalysisServiceError,
    ) as exc:
        typer.echo(f"FAILED: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    _emit_result(result, as_json=as_json)


@app.command("run")
def run_submission(
    request_file: Annotated[
        Path,
        typer.Argument(
            exists=True,
            dir_okay=False,
            readable=True,
            help="Canonical AnalysisSubmission JSON file; its mode is respected.",
        ),
    ],
    as_json: bool = typer.Option(False, "--json", help="Emit the complete result as JSON."),
) -> None:
    """Run a canonical submission in its declared demo or live mode."""

    from porter_forces_ai.repository import RepositoryError
    from porter_forces_ai.service import AnalysisService, AnalysisServiceError, AnalysisSubmission

    try:
        submission = AnalysisSubmission.model_validate_json(
            request_file.read_text(encoding="utf-8")
        )
        with AnalysisService(Settings()) as service:
            result = service.analyze(submission)
    except (OSError, ValidationError, RepositoryError, AnalysisServiceError) as exc:
        typer.echo(f"FAILED: {exc}", err=True)
        raise typer.Exit(code=1) from exc
    _emit_result(result, as_json=as_json)


@app.command()
def runs(
    limit: int = typer.Option(20, min=1, max=200),
    as_json: bool = typer.Option(False, "--json", help="Emit machine-readable output."),
) -> None:
    """List persisted local analysis runs."""

    from porter_forces_ai.repository import SQLiteRunRepository

    settings = Settings()
    if settings.database_path.expanduser().is_file():
        with SQLiteRunRepository(settings.database_path, read_only=True) as repository:
            rows = repository.list_runs(limit=limit)
    else:
        rows = ()
    if as_json:
        typer.echo(
            json.dumps(
                [
                    {
                        "run_id": item.run_id,
                        "status": item.status,
                        "created_at": item.created_at.isoformat(),
                        "updated_at": item.updated_at.isoformat(),
                    }
                    for item in rows
                ],
                indent=2,
            )
        )
        return
    if not rows:
        typer.echo("No persisted runs.")
        return
    for item in rows:
        typer.echo(f"{item.run_id}  {item.status:<20}  {item.updated_at.isoformat()}")


@app.command()
def show(
    run_id: str = typer.Argument(..., help="Persisted run identifier."),
    as_json: bool = typer.Option(False, "--json", help="Emit the complete result as JSON."),
) -> None:
    """Show a completed run, hydrating its immutable result artifact."""

    from porter_forces_ai.service import AnalysisService

    settings = Settings()
    if settings.database_path.expanduser().is_file():
        with AnalysisService(settings, read_only=True) as service:
            result = service.get_result(run_id)
    else:
        result = None
    if result is None:
        typer.echo("FAILED: run has no completed analysis result", err=True)
        raise typer.Exit(code=1)
    _emit_result(result, as_json=as_json)


@app.command()
def serve(
    reload: bool = typer.Option(False, help="Reload Python code during local development."),
) -> None:
    """Serve the loopback-only API used by the local dashboard."""

    import uvicorn

    settings = Settings()
    uvicorn.run(
        "porter_forces_ai.web:create_app",
        factory=True,
        host=settings.api_host,
        port=settings.api_port,
        reload=reload,
    )


def _emit_result(result: object, *, as_json: bool) -> None:
    from porter_forces_ai.service import AnalysisResult

    validated = AnalysisResult.model_validate(result)
    if as_json:
        typer.echo(validated.model_dump_json(indent=2))
        return
    typer.echo(f"Run: {validated.run_id}")
    typer.echo(f"Status: {validated.status.value}")
    if validated.ralph_state is not None:
        typer.echo(f"Ralph attempts: {len(validated.ralph_state.attempts)}")
    if validated.quality_report is not None:
        typer.echo(f"Draft valid: {validated.quality_report.draft_valid}")
        typer.echo(f"Publishable: {validated.quality_report.publishable}")
    for name, path in validated.artifact_paths.items():
        typer.echo(f"{name}: {path}")


if __name__ == "__main__":
    app()
