"""Loopback-only FastAPI surface for the local advisory workspace."""

from __future__ import annotations

import re
import threading
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from datetime import date
from typing import Any

from fastapi import FastAPI, HTTPException, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import Response as ContentResponse
from pydantic import BaseModel, ConfigDict, Field, model_validator

from porter_forces_ai.domain import (
    ApprovalDecision,
    ApprovalRole,
    BoardAudience,
    DecisionRequest,
    OrganizationArchetype,
)
from porter_forces_ai.economics import CostOfDelayInputs, ScenarioEconomicsInputs
from porter_forces_ai.ralph import CompletionTarget
from porter_forces_ai.service import (
    AnalysisMode,
    AnalysisResult,
    AnalysisService,
    AnalysisServiceError,
    AnalysisSubmission,
    ApplicationRunStatus,
    result_etag,
)
from porter_forces_ai.settings import Settings


class _ApiModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ResearchPolicy(_ApiModel):
    web: bool = False


class AnalysisCreateRequest(_ApiModel):
    """Accept both the canonical API contract and the dashboard's compact form."""

    request: DecisionRequest | None = None
    mode: AnalysisMode | None = None
    target: CompletionTarget = CompletionTarget.DRAFT
    question: str | None = Field(default=None, max_length=4_000)
    organization_type: str | None = Field(default=None, max_length=100)
    horizon: str | int | None = None
    market_boundary: str | None = Field(default=None, max_length=500)
    internal_context: str | None = Field(default=None, max_length=10_000)
    board_objection: str | None = Field(default=None, max_length=2_000)
    public_research_context: str | None = Field(default=None, max_length=4_000)
    evidence_cutoff: date | None = None
    research_policy: ResearchPolicy = Field(default_factory=ResearchPolicy)
    scenario_economics: list[ScenarioEconomicsInputs] = Field(default_factory=list)
    cost_of_delay: CostOfDelayInputs | None = None
    max_attempts: int | None = Field(default=None, ge=1, le=10)
    max_budget_units: int | None = Field(default=None, ge=1, le=100)
    stall_limit: int | None = Field(default=None, ge=2, le=10)

    @model_validator(mode="after")
    def require_request_or_question(self) -> AnalysisCreateRequest:
        if self.request is None and not (self.question or "").strip():
            raise ValueError("request or question is required")
        return self

    def submission(self) -> AnalysisSubmission:
        web_enabled = self.research_policy.web
        mode = self.mode or (AnalysisMode.LIVE if web_enabled else AnalysisMode.DEMO)
        if self.request is not None:
            decision_request = self.request
        else:
            question = (self.question or "").strip()
            boundary = (self.market_boundary or "Global regulated financial services").strip()
            archetype = _archetype(self.organization_type or "global bank")
            horizon = _months(self.horizon)
            if mode is AnalysisMode.LIVE and not self.public_research_context:
                raise ValueError(
                    "compact live requests require an explicitly sanitized "
                    "public_research_context"
                )
            public_context = self.public_research_context or (
                "Synthetic offline demonstration context for Porter Five Forces; no public "
                "network request is made in demo mode."
            )
            local_context: dict[str, str] = {}
            if self.internal_context:
                local_context["advisor_context"] = self.internal_context
            if self.board_objection:
                local_context["board_objection"] = self.board_objection
            decision_request = DecisionRequest(
                question=question,
                archetype=archetype,
                industry_arena=boundary,
                time_horizon_months=horizon,
                audience=[BoardAudience.FULL_BOARD],
                public_research_context=public_context,
                internal_context=local_context,
                evidence_cutoff=self.evidence_cutoff,
            )
        return AnalysisSubmission(
            request=decision_request,
            mode=mode,
            target=self.target,
            scenario_economics=self.scenario_economics,
            cost_of_delay=self.cost_of_delay,
            max_attempts=self.max_attempts,
            max_budget_units=self.max_budget_units,
            stall_limit=self.stall_limit,
        )


class ApprovalRequest(_ApiModel):
    role: ApprovalRole
    reviewer: str | None = Field(default=None, max_length=300)
    decision: str

    def normalized_decision(self) -> ApprovalDecision:
        if self.decision.casefold() in {"approve", "approved"}:
            return ApprovalDecision.APPROVE
        if self.decision.casefold() in {"reject", "rejected", "return", "returned"}:
            return ApprovalDecision.REJECT
        raise ValueError("decision must be approve or reject")


class SettingsUpdate(_ApiModel):
    endpoint: str | None = None
    model: str | None = None
    searchRegion: str | None = None
    maxSources: int | str | None = None


def create_app(
    settings: Settings | None = None,
    *,
    service: AnalysisService | None = None,
) -> FastAPI:
    """Create the local API; dependency injection keeps integration tests isolated."""

    app_settings = settings or (service.settings if service is not None else Settings())
    owns_service = service is None
    active_service = service or AnalysisService(app_settings)
    active_service.recover_interrupted_runs()
    jobs: dict[str, dict[str, Any]] = {}
    jobs_lock = threading.RLock()
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="porter-analysis")

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> Any:
        yield
        # Do not close SQLite beneath an in-flight analysis. Queued work is
        # cancelled; the current job drains and persists its terminal state.
        executor.shutdown(wait=True, cancel_futures=True)
        if owns_service:
            active_service.close()

    app = FastAPI(
        title="PorterForcesAI local API",
        version="0.1.0",
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        redoc_url=None,
        lifespan=lifespan,
    )
    app.state.service = active_service
    app.state.jobs = jobs
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=["127.0.0.1", "localhost", "[::1]", "testserver"],
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[app_settings.ui_origin.rstrip("/")],
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "OPTIONS"],
        allow_headers=["Content-Type", "Accept", "If-None-Match"],
    )

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        repository_health = active_service.repository.health()
        return {
            "ok": True,
            "service": "porter-forces-ai",
            "model": active_service.settings.llm_model,
            "model_endpoint": active_service.settings.llm_base_url,
            "database": {
                "schema_version": repository_health.schema_version,
                "journal_mode": repository_health.journal_mode,
                "foreign_keys": repository_health.foreign_keys_enabled,
            },
            "local_only": True,
        }

    @app.get("/api/dashboard")
    @app.get("/api/workspace")
    def dashboard() -> dict[str, Any]:
        results = active_service.list_results()
        active = _run_view(results[0]) if results else None
        return {
            "active_run": active,
            "run_count": len(results),
            "database_counts": dict(active_service.repository.table_counts()),
            "model": active_service.settings.llm_model,
        }

    @app.post("/api/analyses", status_code=status.HTTP_202_ACCEPTED)
    def create_analysis(payload: AnalysisCreateRequest) -> dict[str, Any]:
        try:
            submission = payload.submission()
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        run_id = active_service.new_run_id()
        with jobs_lock:
            jobs[run_id] = {
                "run_id": run_id,
                "status": "queued",
                "progress": 1,
                "error": None,
            }

        def execute() -> None:
            with jobs_lock:
                jobs[run_id].update(status="running", progress=3)
            try:
                completed = active_service.analyze(submission, run_id=run_id)
            except AnalysisServiceError as exc:
                with jobs_lock:
                    jobs[run_id].update(status="failed", progress=100, error=str(exc))
                return
            with jobs_lock:
                jobs[run_id].update(
                    status=completed.status.value,
                    progress=100,
                    error=completed.error,
                )

        executor.submit(execute)
        return {
            "run_id": run_id,
            "analysis_id": run_id,
            "id": run_id,
            "status": "running",
            "progress": 1,
            "current_phase": "Queued",
        }

    @app.get("/api/runs")
    def list_runs(limit: int = 50) -> dict[str, Any]:
        if not 1 <= limit <= 200:
            raise HTTPException(status_code=422, detail="limit must be between 1 and 200")
        cached = active_service.list_results()[:limit]
        cached_ids = {item.run_id for item in cached}
        persisted = [
            {
                "run_id": item.run_id,
                "id": item.run_id,
                "status": item.status,
                "created_at": item.created_at.isoformat(),
                "updated_at": item.updated_at.isoformat(),
            }
            for item in active_service.repository.list_runs(limit=limit)
            if item.run_id not in cached_ids
        ]
        return {
            "runs": [*[_run_view(item) for item in cached], *persisted][:limit],
            "count": len(cached) + len(persisted),
        }

    @app.get("/api/runs/{run_id}")
    def get_run(run_id: str, request: Request, response: Response) -> dict[str, Any]:
        result = active_service.get_result(run_id)
        if result is not None:
            etag = result_etag(result)
            if request.headers.get("if-none-match", "").strip('"') == etag:
                response.status_code = status.HTTP_304_NOT_MODIFIED
                return {}
            response.headers["ETag"] = f'"{etag}"'
            return _run_view(result, include_details=True)
        with jobs_lock:
            job = jobs.get(run_id)
        if job is not None:
            return {
                "id": run_id,
                "run_id": run_id,
                "analysis_id": run_id,
                "status": "running" if job["status"] in {"queued", "running"} else job["status"],
                "progress": job["progress"],
                "current_phase": "Queued" if job["status"] == "queued" else "Starting",
                "iteration": 0,
                "quality_score": 0,
                "error": job["error"],
            }
        persisted = active_service.repository.get_run(run_id)
        if persisted is None:
            raise HTTPException(status_code=404, detail="run not found")
        return {
            "id": persisted.run_id,
            "run_id": persisted.run_id,
            "analysis_id": persisted.run_id,
            "status": persisted.status,
            "progress": 100,
            "current_phase": "Persisted",
            "iteration": 0,
            "quality_score": 0,
            "created_at": persisted.created_at.isoformat(),
            "updated_at": persisted.updated_at.isoformat(),
        }

    @app.get("/api/runs/{run_id}/artifacts")
    def list_artifacts(run_id: str) -> dict[str, Any]:
        if active_service.repository.get_run(run_id) is None:
            raise HTTPException(status_code=404, detail="run not found")
        rows = active_service.repository.list_artifacts(run_id)
        public_types = {
            "board_memo": "board_memo_markdown",
            "evidence_register": "evidence_register_csv",
        }
        available_types = {item.artifact_type for item in rows}
        return {
            "run_id": run_id,
            "files": {
                name: f"/api/runs/{run_id}/artifacts/{name}"
                for name, artifact_type in public_types.items()
                if artifact_type in available_types
            },
            "artifacts": [
                {
                    "artifact_id": item.artifact_id,
                    "type": item.artifact_type,
                    "sha256": item.content_sha256,
                    "schema_version": item.schema_version,
                    "created_at": item.created_at.isoformat(),
                }
                for item in rows
            ],
        }

    @app.get("/api/runs/{run_id}/artifacts/{artifact_name}")
    def download_artifact(run_id: str, artifact_name: str) -> ContentResponse:
        public_types = {
            "board_memo": (
                "board_memo_markdown",
                "text/markdown; charset=utf-8",
                "board-brief.md",
            ),
            "evidence_register": (
                "evidence_register_csv",
                "text/csv; charset=utf-8",
                "evidence-register.csv",
            ),
        }
        contract = public_types.get(artifact_name)
        if contract is None:
            raise HTTPException(status_code=404, detail="artifact file not found")
        artifact_type, media_type, filename = contract
        artifact = next(
            iter(
                active_service.repository.list_artifacts(
                    run_id,
                    artifact_type=artifact_type,
                )
            ),
            None,
        )
        if artifact is None or not isinstance(artifact.payload, str):
            raise HTTPException(status_code=404, detail="artifact file not found")
        return ContentResponse(
            content=artifact.payload,
            media_type=media_type,
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    def approve(run_id: str, payload: ApprovalRequest) -> dict[str, Any]:
        try:
            updated = active_service.record_approval(
                run_id,
                role=payload.role,
                reviewer=payload.reviewer or f"{payload.role.value} local reviewer",
                decision=payload.normalized_decision(),
            )
        except (AnalysisServiceError, ValueError) as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return _run_view(updated, include_details=True)

    app.post("/api/runs/{run_id}/approvals")(approve)
    app.post("/api/analyses/{run_id}/approvals", include_in_schema=False)(approve)

    @app.get("/api/settings")
    def get_settings() -> dict[str, Any]:
        return _safe_settings(active_service.settings)

    @app.put("/api/settings")
    def put_settings(payload: SettingsUpdate) -> dict[str, Any]:
        updates: dict[str, Any] = {}
        if payload.endpoint:
            updates["llm_base_url"] = payload.endpoint
        if payload.model:
            updates["llm_model"] = payload.model
        if payload.searchRegion:
            updates["search_region"] = payload.searchRegion
        if payload.maxSources is not None:
            try:
                updates["capture_max_sources"] = int(payload.maxSources)
            except (TypeError, ValueError) as exc:
                raise HTTPException(
                    status_code=422,
                    detail="maxSources must be an integer",
                ) from exc
        try:
            candidate = active_service.settings.model_dump(mode="python")
            candidate.update(updates)
            active_service.settings = Settings.model_validate(candidate)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return _safe_settings(active_service.settings)

    return app


def _archetype(value: str) -> OrganizationArchetype:
    normalized = value.casefold()
    if "quant" in normalized or "trading" in normalized or "hedge" in normalized:
        return OrganizationArchetype.QUANT_TRADING
    if "insur" in normalized:
        return OrganizationArchetype.INSURER
    return OrganizationArchetype.GLOBAL_BANK


def _months(value: str | int | None) -> int:
    if isinstance(value, int):
        return max(1, min(value, 120))
    match = re.search(r"\d+", value or "")
    return max(1, min(int(match.group()) if match else 36, 120))


def _run_view(result: AnalysisResult, *, include_details: bool = False) -> dict[str, Any]:
    status_map = {
        ApplicationRunStatus.CREATED: ("running", 2, "Created"),
        ApplicationRunStatus.ACQUIRING_EVIDENCE: ("running", 32, "Evidence acquisition"),
        ApplicationRunStatus.VERIFYING: ("running", 76, "Ralph verification"),
        ApplicationRunStatus.ACHIEVED_DRAFT: ("complete", 100, "Draft achieved"),
        ApplicationRunStatus.HUMAN_REQUIRED: (
            "awaiting_approval",
            99,
            "Human approval",
        ),
        ApplicationRunStatus.PUBLISHABLE: ("complete", 100, "Publication unlocked"),
        ApplicationRunStatus.BLOCKED: ("blocked", 100, "Ralph blocked"),
        ApplicationRunStatus.FAILED: ("failed", 100, "Failed"),
    }
    ui_status, progress, phase = status_map[result.status]
    attempts = result.ralph_state.attempts if result.ralph_state else ()
    latest = result.ralph_state.latest_report if result.ralph_state else None
    quality_score = 0.0
    if latest and latest.evaluations:
        passed = sum(
            (
                result.human_approval.outcome.value == "pass"
                if item.criterion_id == "G-human-approval"
                and result.human_approval is not None
                else item.outcome.value == "pass"
            )
            for item in latest.evaluations
        )
        quality_score = passed / len(latest.evaluations)
    payload: dict[str, Any] = {
        "id": result.run_id,
        "run_id": result.run_id,
        "analysis_id": result.run_id,
        "status": ui_status,
        "application_status": result.status.value,
        "progress": progress,
        "current_phase": phase,
        "iteration": len(attempts),
        "max_iterations": result.ralph_state.max_attempts if result.ralph_state else 0,
        "quality_score": quality_score,
        "started_at": result.started_at.isoformat(),
        "completed_at": result.completed_at.isoformat() if result.completed_at else None,
        "model": result.model_id,
        "error": result.error,
    }
    if include_details:
        details = result.model_dump(mode="json")
        secrets = [*result.request.restricted_terms]
        secrets.extend(result.request.internal_context.keys())
        secrets.extend(result.request.internal_context.values())
        details["artifact_paths"] = {
            name: f"/api/runs/{result.run_id}/artifacts/{name}"
            for name in ("board_memo", "evidence_register")
            if name in result.artifact_ids
        }
        payload["details"] = _redact_api_value(details, secrets=secrets)
    return payload


def _safe_settings(settings: Settings) -> dict[str, Any]:
    return {
        "endpoint": settings.llm_base_url,
        "model": settings.llm_model,
        "searchRegion": settings.search_region,
        "maxSources": settings.capture_max_sources,
        "apiHost": settings.api_host,
        "apiPort": settings.api_port,
    }


def _redact_api_value(value: Any, *, secrets: list[str]) -> Any:
    """Remove local-only request data from every nested API projection."""

    patterns = [
        re.compile(re.escape(secret), re.IGNORECASE)
        for secret in secrets
        if len(secret.strip()) >= 3
    ]

    def redact_text(text: str) -> str:
        for pattern in patterns:
            text = pattern.sub("[REDACTED LOCAL TERM]", text)
        return text

    def walk(item: Any) -> Any:
        if isinstance(item, Mapping):
            cleaned: dict[str, Any] = {}
            for raw_key, nested in item.items():
                key = redact_text(str(raw_key))
                if str(raw_key) == "internal_context":
                    cleaned[key] = {"redacted": "[REDACTED LOCAL CONTEXT]"}
                elif str(raw_key) == "restricted_terms":
                    cleaned[key] = []
                else:
                    cleaned[key] = walk(nested)
            return cleaned
        if isinstance(item, list):
            return [walk(nested) for nested in item]
        if isinstance(item, str):
            return redact_text(item)
        return item

    return walk(value)
