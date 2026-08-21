"""Application service coordinating acquisition, Ralph verification, and artifacts."""

from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Mapping
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from uuid import uuid4

from pydantic import Field

from porter_forces_ai.adapters.duckduckgo import DuckDuckGoSearchProvider
from porter_forces_ai.adapters.omlx import create_chat_model
from porter_forces_ai.domain import (
    FORCE_ORDER,
    ApprovalDecision,
    ApprovalRecord,
    ApprovalRole,
    BoardBrief,
    ChallengeReport,
    ContractModel,
    DecisionFrame,
    DecisionRequest,
    EvidenceItem,
    EvidenceLedger,
    ForceAssessment,
    PublicResearchAssignment,
    ResearchBundle,
    SourceClass,
)
from porter_forces_ai.economics import (
    CostOfDelayInputs,
    CostOfDelayResult,
    ScenarioEconomicsInputs,
    ScenarioEconomicsResult,
    calculate_cost_of_delay,
    calculate_scenario,
)
from porter_forces_ai.egress import EgressPolicy
from porter_forces_ai.evaluation import (
    AnalysisGoalEvaluator,
    criteria_for,
    evidence_snapshot_id,
)
from porter_forces_ai.quality import QualityReport, brief_fingerprint, evaluate_brief
from porter_forces_ai.ralph import (
    AttemptContext,
    CompletionTarget,
    RalphState,
    RalphStatus,
    RalphSupervisor,
)
from porter_forces_ai.renderers import render_board_memo, write_run_artifacts
from porter_forces_ai.repository import SQLiteRunRepository
from porter_forces_ai.runtime import DeterministicDemoRuntime, OmlxAdvisorRuntime
from porter_forces_ai.settings import Settings
from porter_forces_ai.source_capture import (
    CapturedSource,
    CapturePolicy,
    DiscoveryLedger,
    SafeSourceCapture,
    SourceCaptureError,
    promote_capture_to_evidence,
)
from porter_forces_ai.workflow import DecisionScopeError, build_workflow


class AnalysisMode(StrEnum):
    DEMO = "demo"
    LIVE = "live"


class ApplicationRunStatus(StrEnum):
    CREATED = "created"
    ACQUIRING_EVIDENCE = "acquiring_evidence"
    VERIFYING = "verifying"
    ACHIEVED_DRAFT = "achieved_draft"
    HUMAN_REQUIRED = "human_required"
    PUBLISHABLE = "publishable"
    BLOCKED = "blocked"
    FAILED = "failed"


class AnalysisSubmission(ContractModel):
    request: DecisionRequest
    mode: AnalysisMode = AnalysisMode.DEMO
    target: CompletionTarget = CompletionTarget.DRAFT
    scenario_economics: list[ScenarioEconomicsInputs] = Field(default_factory=list)
    cost_of_delay: CostOfDelayInputs | None = None
    max_attempts: int | None = Field(default=None, ge=1, le=10)
    max_budget_units: int | None = Field(default=None, ge=1, le=100)
    stall_limit: int | None = Field(default=None, ge=2, le=10)


class AnalysisResult(ContractModel):
    run_id: str
    mode: AnalysisMode
    status: ApplicationRunStatus
    model_id: str
    started_at: datetime
    completed_at: datetime | None = None
    request: DecisionRequest
    evidence_snapshot_id: str | None = None
    decision_frame: DecisionFrame | None = None
    research_bundles: list[ResearchBundle] = Field(default_factory=list)
    evidence_ledger: EvidenceLedger | None = None
    force_assessments: list[ForceAssessment] = Field(default_factory=list)
    board_brief: BoardBrief | None = None
    challenge_report: ChallengeReport | None = None
    quality_report: QualityReport | None = None
    ralph_state: RalphState | None = None
    scenario_economics: list[ScenarioEconomicsResult] = Field(default_factory=list)
    cost_of_delay: CostOfDelayResult | None = None
    artifact_paths: dict[str, str] = Field(default_factory=dict)
    artifact_ids: dict[str, str] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
    error: str | None = None


class AnalysisServiceError(RuntimeError):
    """A complete application run could not be produced."""


class AnalysisService:
    """Synchronous core used by the CLI and the local background-job API."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        repository: SQLiteRunRepository | None = None,
    ) -> None:
        self.settings = settings or Settings()
        self.repository = repository or SQLiteRunRepository(self.settings.database_path)
        self._owns_repository = repository is None
        self._results: dict[str, AnalysisResult] = {}
        self._lock = threading.RLock()

    def close(self) -> None:
        if self._owns_repository:
            self.repository.close()

    def __enter__(self) -> AnalysisService:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()

    def analyze(
        self,
        submission: AnalysisSubmission,
        *,
        run_id: str | None = None,
    ) -> AnalysisResult:
        """Run acquisition once and Ralph-supervised synthesis to a terminal state."""

        run_id = run_id or self.new_run_id()
        started_at = datetime.now(UTC)
        result = AnalysisResult(
            run_id=run_id,
            mode=submission.mode,
            status=ApplicationRunStatus.CREATED,
            model_id=(
                "deterministic-demo"
                if submission.mode is AnalysisMode.DEMO
                else self.settings.llm_model
            ),
            started_at=started_at,
            request=submission.request,
        )
        self.repository.create_run(submission, run_id=run_id, status=result.status.value)
        self._cache(result)

        captures: list[CapturedSource] = []
        discovery_ledger: DiscoveryLedger | None = None
        runtime: DeterministicDemoRuntime | OmlxAdvisorRuntime
        try:
            self._set_status(result, ApplicationRunStatus.ACQUIRING_EVIDENCE)
            if submission.mode is AnalysisMode.DEMO:
                runtime = DeterministicDemoRuntime(run_id)
                frame, bundles, evidence = self._prepare_demo(runtime, submission.request)
            else:
                runtime, frame, bundles, evidence, captures, discovery_ledger = (
                    self._prepare_live(run_id, submission.request, result.warnings)
                )
            snapshot_id = evidence_snapshot_id(evidence)
            result.evidence_snapshot_id = snapshot_id

            graph = build_workflow(runtime, max_quality_repairs=1)
            evaluator = AnalysisGoalEvaluator(
                expected_evidence_snapshot_id=snapshot_id,
            )

            def build_attempt_input(
                objective: Mapping[str, Any],
                context: AttemptContext,
            ) -> Mapping[str, Any]:
                if isinstance(runtime, OmlxAdvisorRuntime):
                    runtime.set_gap_directives(context.directives)
                return {
                    "request": objective["request"],
                    "research_bundles": [],
                    "force_assessments": [],
                }

            supervisor = RalphSupervisor(
                graph,
                evaluator,
                input_builder=build_attempt_input,
                base_config={
                    "max_concurrency": self.settings.analysis_max_concurrency,
                    "recursion_limit": 50,
                },
            )
            initial = supervisor.new_state(
                objective={"request": submission.request},
                criteria=criteria_for(submission.target),
                evidence_snapshot_id=snapshot_id,
                target=submission.target,
                max_attempts=submission.max_attempts or self.settings.ralph_max_attempts,
                max_budget_units=(
                    submission.max_budget_units or self.settings.ralph_max_budget_units
                ),
                stall_limit=submission.stall_limit or self.settings.ralph_stall_limit,
            )
            self._set_status(result, ApplicationRunStatus.VERIFYING)
            ralph_state = supervisor.run(initial)
            result.ralph_state = ralph_state
            if ralph_state.latest_output is None:
                raise AnalysisServiceError(
                    ralph_state.terminal_reason or "Ralph produced no analysis artifact"
                )
            self._hydrate_result(result, ralph_state.latest_output)
            result.scenario_economics = [
                calculate_scenario(item) for item in submission.scenario_economics
            ]
            result.cost_of_delay = (
                calculate_cost_of_delay(submission.cost_of_delay)
                if submission.cost_of_delay is not None
                else None
            )
            result.status = _application_status(ralph_state.status)
            result.completed_at = datetime.now(UTC)
            self._persist_success(
                result,
                frame=frame,
                bundles=bundles,
                captures=captures,
                discovery_ledger=discovery_ledger,
            )
            self.repository.set_run_status(run_id, result.status.value)
            self._cache(result)
            return result
        except Exception as exc:
            result.status = ApplicationRunStatus.FAILED
            result.completed_at = datetime.now(UTC)
            result.error = f"{type(exc).__name__}: {exc}"
            self.repository.set_run_status(run_id, result.status.value)
            self.repository.put_artifact(
                run_id,
                artifact_type="failure",
                payload={"error": result.error, "completed_at": result.completed_at},
            )
            self._cache(result)
            raise AnalysisServiceError(result.error) from exc

    def get_result(self, run_id: str) -> AnalysisResult | None:
        with self._lock:
            cached = self._results.get(run_id)
        if cached is not None:
            return cached
        artifacts = self.repository.list_artifacts(run_id)
        artifact = next(
            (
                item
                for item in artifacts
                if item.artifact_type
                in {"analysis_result_revision", "analysis_result"}
            ),
            None,
        )
        if artifact is None:
            return None
        loaded = AnalysisResult.model_validate(artifact.payload)
        by_type = {item.artifact_type: item.artifact_id for item in reversed(artifacts)}
        loaded.artifact_ids.update(
            {
                "board_brief": by_type.get("board_brief", ""),
                "board_memo": by_type.get("board_memo_markdown", ""),
                "analysis_result": artifact.artifact_id,
            }
        )
        loaded.artifact_ids = {
            key: value for key, value in loaded.artifact_ids.items() if value
        }
        self._cache(loaded)
        return loaded

    def list_results(self) -> list[AnalysisResult]:
        for record in self.repository.list_runs(limit=200):
            self.get_result(record.run_id)
        with self._lock:
            return sorted(
                self._results.values(),
                key=lambda item: item.started_at,
                reverse=True,
            )

    def record_approval(
        self,
        run_id: str,
        *,
        role: ApprovalRole,
        reviewer: str,
        decision: ApprovalDecision,
    ) -> AnalysisResult:
        """Append one exact-content decision and recompute publication status."""

        result = self.get_result(run_id)
        if result is None or result.board_brief is None or result.evidence_ledger is None:
            raise AnalysisServiceError("run is absent or has no completed board brief")
        artifact_id = result.artifact_ids.get("board_brief")
        if artifact_id is None:
            raise AnalysisServiceError("run has no immutable board-brief artifact")
        approval = ApprovalRecord(
            role=role,
            reviewer=reviewer,
            reviewed_at=datetime.now(UTC),
            decision=decision,
            brief_sha256=brief_fingerprint(result.board_brief),
        )
        self.repository.add_approval(run_id, artifact_id, approval)
        approvals = self.repository.list_approvals(artifact_id)
        if result.decision_frame is None:
            raise AnalysisServiceError("run has no decision frame")
        result.quality_report = evaluate_brief(
            result.board_brief,
            result.evidence_ledger.evidence,
            result.evidence_ledger.claims,
            result.decision_frame.assumptions,
            result.evidence_ledger.links,
            approvals,
        )
        if result.quality_report.publishable:
            result.status = ApplicationRunStatus.PUBLISHABLE
        else:
            result.status = ApplicationRunStatus.HUMAN_REQUIRED
        self.repository.put_artifact(
            run_id,
            artifact_type="approval_quality_report",
            payload=result.quality_report,
        )
        self.repository.set_run_status(run_id, result.status.value)
        revision = self.repository.put_artifact(
            run_id,
            artifact_type="analysis_result_revision",
            payload=result,
        )
        result.artifact_ids["analysis_result"] = revision.artifact_id
        self._cache(result)
        return result

    def _prepare_demo(
        self,
        runtime: DeterministicDemoRuntime,
        request: DecisionRequest,
    ) -> tuple[DecisionFrame, list[ResearchBundle], list[EvidenceItem]]:
        frame = runtime.frame_decision(request)
        bundles = [
            runtime.research_force(_assignment(request, force), _egress_policy(request))
            for force in FORCE_ORDER
        ]
        ledger = runtime.build_evidence_ledger(request, frame, bundles)
        return frame, bundles, ledger.evidence

    def _prepare_live(
        self,
        run_id: str,
        request: DecisionRequest,
        warnings: list[str],
    ) -> tuple[
        OmlxAdvisorRuntime,
        DecisionFrame,
        list[ResearchBundle],
        list[EvidenceItem],
        list[CapturedSource],
        DiscoveryLedger,
    ]:
        model = create_chat_model(self.settings)
        search = DuckDuckGoSearchProvider(
            policy=_egress_policy(request),
            region=self.settings.search_region,
            timeout_seconds=self.settings.search_timeout_seconds,
            max_results=self.settings.search_max_results,
        )
        runtime = OmlxAdvisorRuntime(model=model, search=search, run_id=run_id)
        frame = runtime.frame_decision(request)
        if not frame.ready_for_research:
            questions = "; ".join(frame.clarification_questions)
            raise DecisionScopeError(
                "the live decision contract requires clarification: " + questions
            )
        bundles = [
            runtime.research_force(_assignment(request, force), _egress_policy(request))
            for force in FORCE_ORDER
        ]

        ledger = DiscoveryLedger()
        registered_by_url: dict[str, str] = {}
        for query, hits in runtime.recorded_search_executions:
            _execution, registered = ledger.record_execution(
                query,
                hits,
                provider="duckduckgo",
            )
            for hit in registered:
                registered_by_url.setdefault(hit.url, hit.source_id)

        nominated_urls = {
            candidate.url
            for bundle in bundles
            for candidate in bundle.source_candidates
        }
        source_ids = [
            source_id
            for url, source_id in registered_by_url.items()
            if url in nominated_urls
        ][: self.settings.capture_max_sources]
        if not source_ids:
            raise AnalysisServiceError(
                "research returned no source candidate reconciled to an executed search"
            )

        captures: list[CapturedSource] = []
        evidence: list[EvidenceItem] = []
        capture_policy = CapturePolicy(
            max_content_bytes=self.settings.capture_max_bytes,
            timeout_seconds=self.settings.capture_timeout_seconds,
        )
        with SafeSourceCapture(ledger, policy=capture_policy) as capture_service:
            for source_id in source_ids:
                try:
                    capture = capture_service.capture(source_id)
                except SourceCaptureError as exc:
                    warnings.append(f"Capture {source_id} skipped: {type(exc).__name__}: {exc}")
                    continue
                captures.append(capture)
                evidence.append(
                    promote_capture_to_evidence(
                        capture,
                        source_class=_conservative_source_class(capture.final_url),
                        excerpt=capture.extracted_text[:10_000],
                        quality_score=0.85,
                        freshness_score=0.85,
                        applicability_score=0.85,
                        geographies=request.geographies,
                        applicable_entities=(
                            [request.organization_name]
                            if request.organization_name
                            else []
                        ),
                    )
                )
        if not evidence:
            raise AnalysisServiceError(
                "none of the registered public sources could be safely captured"
            )
        runtime.freeze_captured_evidence(evidence)
        return runtime, frame, bundles, evidence, captures, ledger

    def _hydrate_result(self, result: AnalysisResult, output: Mapping[str, Any]) -> None:
        result.decision_frame = DecisionFrame.model_validate(output["decision_frame"])
        result.research_bundles = [
            ResearchBundle.model_validate(item) for item in output["research_bundles"]
        ]
        result.evidence_ledger = EvidenceLedger.model_validate(output["ledger"])
        result.force_assessments = [
            ForceAssessment.model_validate(item) for item in output["force_assessments"]
        ]
        result.board_brief = BoardBrief.model_validate(output["board_brief"])
        result.challenge_report = ChallengeReport.model_validate(output["challenge_report"])
        result.quality_report = QualityReport.model_validate(output["quality_report"])

    def _persist_success(
        self,
        result: AnalysisResult,
        *,
        frame: DecisionFrame,
        bundles: list[ResearchBundle],
        captures: list[CapturedSource],
        discovery_ledger: DiscoveryLedger | None,
    ) -> None:
        if result.ralph_state is None or result.board_brief is None:
            raise AnalysisServiceError("cannot persist an incomplete Ralph result")
        if result.evidence_ledger is None or result.quality_report is None:
            raise AnalysisServiceError("cannot persist an incomplete analysis result")
        attempts = result.ralph_state.attempts
        for manifest in attempts:
            self.repository.start_attempt(
                result.run_id,
                manifest.attempt_number,
                attempt_id=manifest.attempt_id,
            )
            self.repository.finish_attempt(
                manifest.attempt_id,
                status=(
                    result.status.value
                    if manifest is attempts[-1]
                    else RalphStatus.RUNNING.value
                ),
                goal_report=manifest.report,
                completed_at=manifest.completed_at,
            )
        first_attempt_id = attempts[0].attempt_id if attempts else None
        latest_attempt_id = attempts[-1].attempt_id if attempts else None
        if discovery_ledger is not None and first_attempt_id is not None:
            hits_by_execution: dict[str, list[Any]] = {}
            for hit in discovery_ledger.hits:
                hits_by_execution.setdefault(hit.execution_id, []).append(hit)
            for execution in discovery_ledger.executions:
                self.repository.record_search_execution(
                    result.run_id,
                    first_attempt_id,
                    execution,
                    hits_by_execution.get(execution.execution_id, []),
                )
            for capture in captures:
                self.repository.record_capture(capture)
        latest_report = result.ralph_state.latest_report
        if latest_report is not None:
            criteria = {item.criterion_id: item for item in result.ralph_state.criteria}
            for evaluation in latest_report.evaluations:
                criterion = criteria[evaluation.criterion_id]
                self.repository.save_goal(
                    result.run_id,
                    criterion_key=criterion.criterion_id,
                    description=criterion.description,
                    required=criterion.required,
                    status=evaluation.outcome.value,
                    score=1 if evaluation.outcome.value == "pass" else 0,
                    evidence=evaluation,
                )

        board_artifact = self.repository.put_artifact(
            result.run_id,
            artifact_type="board_brief",
            payload=result.board_brief,
            attempt_id=latest_attempt_id,
        )
        result.artifact_ids["board_brief"] = board_artifact.artifact_id
        memo = render_board_memo(
            brief=result.board_brief,
            frame=result.decision_frame or frame,
            ledger=result.evidence_ledger,
            quality=result.quality_report,
            mode=result.mode.value,
            scenario_economics=result.scenario_economics,
            cost_of_delay=result.cost_of_delay,
            ralph_summary={
                "status": result.ralph_state.status.value,
                "attempt_count": len(result.ralph_state.attempts),
            },
        )
        output_dir = self.settings.artifacts_dir / result.run_id
        result.artifact_paths = write_run_artifacts(
            output_dir,
            memo=memo,
            audit_payload={
                "result": result,
                "decision_frame": frame,
                "research_bundles": bundles,
            },
            ledger=result.evidence_ledger,
        )
        memo_artifact = self.repository.put_artifact(
            result.run_id,
            artifact_type="board_memo_markdown",
            payload=memo,
            attempt_id=latest_attempt_id,
        )
        result.artifact_ids["board_memo"] = memo_artifact.artifact_id
        result_artifact = self.repository.put_artifact(
            result.run_id,
            artifact_type="analysis_result",
            payload=result,
            attempt_id=latest_attempt_id,
        )
        result.artifact_ids["analysis_result"] = result_artifact.artifact_id

    def _cache(self, result: AnalysisResult) -> None:
        with self._lock:
            self._results[result.run_id] = result

    def _set_status(self, result: AnalysisResult, status: ApplicationRunStatus) -> None:
        result.status = status
        self.repository.set_run_status(result.run_id, status.value)
        self._cache(result)

    @staticmethod
    def new_run_id() -> str:
        timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
        return f"RUN-{timestamp}-{uuid4().hex[:8]}"


def _assignment(request: DecisionRequest, force: Any) -> PublicResearchAssignment:
    return PublicResearchAssignment(
        force=force,
        archetype=request.archetype,
        analysis_mode=request.analysis_mode,
        public_context=request.public_research_context,
        time_horizon_months=request.time_horizon_months,
        evidence_cutoff=request.evidence_cutoff,
    )


def _egress_policy(request: DecisionRequest) -> EgressPolicy:
    return EgressPolicy(forbidden_terms=tuple(request.restricted_terms))


def _conservative_source_class(url: str) -> SourceClass:
    host = (urlsplit(url).hostname or "").casefold()
    regulator_markers = (
        ".gov",
        "bis.org",
        "ecb.europa.eu",
        "bankofengland.co.uk",
        "europa.eu",
        "finra.org",
    )
    if host.endswith(regulator_markers):
        return SourceClass.REGULATOR
    if host.endswith(".edu") or host in {"arxiv.org", "doi.org"}:
        return SourceClass.ACADEMIC
    # Unknown public pages receive the least-authoritative promotable class.
    # The quality gate can still reject them when strength or entailment is weak.
    return SourceClass.VENDOR


def _application_status(status: RalphStatus) -> ApplicationRunStatus:
    mapping = {
        RalphStatus.ACHIEVED_DRAFT: ApplicationRunStatus.ACHIEVED_DRAFT,
        RalphStatus.HUMAN_REQUIRED: ApplicationRunStatus.HUMAN_REQUIRED,
        RalphStatus.PUBLISHABLE: ApplicationRunStatus.PUBLISHABLE,
        RalphStatus.BLOCKED: ApplicationRunStatus.BLOCKED,
        RalphStatus.RUNNING: ApplicationRunStatus.VERIFYING,
    }
    return mapping[status]


def result_etag(result: AnalysisResult) -> str:
    """Return a stable HTTP cache validator for a serialized run result."""

    payload = json.dumps(
        result.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def artifact_path_is_within(root: Path, path: str) -> bool:
    """Check that an artifact path resolves below its configured run root."""

    try:
        Path(path).resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True
