"""Application service coordinating acquisition, Ralph verification, and artifacts."""

from __future__ import annotations

import hashlib
import json
import threading
from collections.abc import Mapping
from contextlib import suppress
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
    ForceName,
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
from porter_forces_ai.egress import EgressPolicy, EgressViolation
from porter_forces_ai.evaluation import (
    AnalysisGoalEvaluator,
    criteria_for,
    evidence_snapshot_id,
)
from porter_forces_ai.quality import QualityReport, brief_fingerprint, evaluate_brief
from porter_forces_ai.ralph import (
    AttemptContext,
    CompletionTarget,
    CriterionOutcome,
    RalphState,
    RalphStatus,
    RalphSupervisor,
)
from porter_forces_ai.renderers import render_board_memo, write_run_artifacts
from porter_forces_ai.repository import SQLiteRunRepository
from porter_forces_ai.runtime import DeterministicDemoRuntime, OmlxAdvisorRuntime
from porter_forces_ai.settings import Settings
from porter_forces_ai.source_capture import (
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
    approvals: list[ApprovalRecord] = Field(default_factory=list)
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

        runtime: DeterministicDemoRuntime | OmlxAdvisorRuntime
        acquisition_attempt_id: str | None = None
        try:
            self._set_status(result, ApplicationRunStatus.ACQUIRING_EVIDENCE)
            acquisition_attempt_id = f"ACQ-{uuid4().hex}"
            self.repository.start_attempt(
                run_id,
                1,
                attempt_id=acquisition_attempt_id,
                status="acquiring_evidence",
                started_at=datetime.now(UTC),
            )
            if submission.mode is AnalysisMode.DEMO:
                runtime = DeterministicDemoRuntime(run_id)
                frame, bundles, evidence = self._prepare_demo(runtime, submission.request)
            else:
                runtime, frame, bundles, evidence = (
                    self._prepare_live(
                        run_id,
                        submission.request,
                        result.warnings,
                        acquisition_attempt_id=acquisition_attempt_id,
                    )
                )
            snapshot_id = evidence_snapshot_id(evidence)
            result.evidence_snapshot_id = snapshot_id
            self.repository.finish_attempt(
                acquisition_attempt_id,
                status="evidence_frozen",
                completed_at=datetime.now(UTC),
            )

            # Finance-owned arithmetic is completed before synthesis so the
            # recommendation and Ralph acceptance criteria see the same numbers.
            result.scenario_economics = [
                calculate_scenario(item) for item in submission.scenario_economics
            ]
            result.cost_of_delay = (
                calculate_cost_of_delay(submission.cost_of_delay)
                if submission.cost_of_delay is not None
                else None
            )

            graph = build_workflow(runtime, max_quality_repairs=1)
            evaluator = AnalysisGoalEvaluator(
                expected_evidence_snapshot_id=snapshot_id,
                request=submission.request,
                scenario_economics=result.scenario_economics,
                cost_of_delay=result.cost_of_delay,
            )

            def build_attempt_input(
                objective: Mapping[str, Any],
                context: AttemptContext,
            ) -> Mapping[str, Any]:
                if isinstance(runtime, OmlxAdvisorRuntime):
                    runtime.set_gap_directives(context.directives)
                return {
                    "request": DecisionRequest.model_validate(objective["request"]),
                    "research_bundles": [],
                    "force_assessments": [],
                    "scenario_economics": result.scenario_economics,
                    "cost_of_delay": result.cost_of_delay,
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
                criteria=criteria_for(
                    submission.target,
                    include_economics=bool(
                        result.scenario_economics or result.cost_of_delay is not None
                    ),
                ),
                evidence_snapshot_id=snapshot_id,
                target=submission.target,
                max_attempts=submission.max_attempts or self.settings.ralph_max_attempts,
                max_budget_units=(
                    submission.max_budget_units or self.settings.ralph_max_budget_units
                ),
                stall_limit=submission.stall_limit or self.settings.ralph_stall_limit,
            )
            self._set_status(result, ApplicationRunStatus.VERIFYING)
            ralph_state = initial
            while ralph_state.status is RalphStatus.RUNNING:
                previous_attempt_count = len(ralph_state.attempts)
                ralph_state = supervisor.step(ralph_state)
                result.ralph_state = ralph_state
                if len(ralph_state.attempts) > previous_attempt_count:
                    self._persist_ralph_checkpoint(
                        result,
                        ralph_state,
                        ralph_state.attempts[-1],
                        database_attempt_number=len(ralph_state.attempts) + 1,
                    )
                self._cache(result)
            result.ralph_state = ralph_state
            if ralph_state.latest_output is None:
                raise AnalysisServiceError(
                    ralph_state.terminal_reason or "Ralph produced no analysis artifact"
                )
            self._hydrate_result(result, ralph_state.latest_output)
            result.status = _application_status(ralph_state.status)
            result.completed_at = datetime.now(UTC)
            self._persist_success(
                result,
                frame=frame,
                bundles=bundles,
            )
            self.repository.set_run_status(run_id, result.status.value)
            self._cache(result)
            return result
        except Exception as exc:
            # Preserve the initiating failure. Any open phase is closed so a
            # restart never projects it as still running.
            with suppress(Exception):
                self.repository.finish_open_attempts(
                    run_id,
                    status="failed",
                    completed_at=datetime.now(UTC),
                )
            result.status = ApplicationRunStatus.FAILED
            result.completed_at = datetime.now(UTC)
            result.error = f"{type(exc).__name__}: {exc}"
            self.repository.set_run_status(run_id, result.status.value)
            self.repository.put_artifact(
                run_id,
                artifact_type="failure",
                payload={
                    "error": result.error,
                    "completed_at": result.completed_at,
                    "warnings": result.warnings,
                    "evidence_snapshot_id": result.evidence_snapshot_id,
                },
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
            record = self.repository.get_run(run_id)
            if record is None:
                return None
            try:
                submission = AnalysisSubmission.model_validate(record.request)
            except ValueError:
                return None
            failure = next(
                (item for item in artifacts if item.artifact_type == "failure"),
                None,
            )
            checkpoint = next(
                (item for item in artifacts if item.artifact_type == "ralph_checkpoint"),
                None,
            )
            try:
                status = ApplicationRunStatus(record.status)
            except ValueError:
                status = ApplicationRunStatus.FAILED
            completed_at: datetime | None = None
            error: str | None = None
            warnings: list[str] = []
            restored_snapshot_id: str | None = None
            if failure is not None and isinstance(failure.payload, Mapping):
                error_value = failure.payload.get("error")
                error = str(error_value) if error_value is not None else None
                completed_value = failure.payload.get("completed_at")
                if isinstance(completed_value, str):
                    completed_at = datetime.fromisoformat(completed_value)
                warning_value = failure.payload.get("warnings", [])
                if isinstance(warning_value, list):
                    warnings = [str(item) for item in warning_value]
                snapshot_value = failure.payload.get("evidence_snapshot_id")
                if isinstance(snapshot_value, str):
                    restored_snapshot_id = snapshot_value
            restored = AnalysisResult(
                run_id=run_id,
                mode=submission.mode,
                status=status,
                model_id=(
                    "deterministic-demo"
                    if submission.mode is AnalysisMode.DEMO
                    else self.settings.llm_model
                ),
                started_at=record.created_at,
                completed_at=completed_at,
                request=submission.request,
                evidence_snapshot_id=restored_snapshot_id,
                error=error,
                warnings=warnings,
                ralph_state=(
                    RalphState.model_validate(checkpoint.payload)
                    if checkpoint is not None
                    else None
                ),
            )
            self._cache(restored)
            return restored
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

    def recover_interrupted_runs(self) -> int:
        """Fail closed for nonterminal work left behind by a prior process."""

        nonterminal = {
            ApplicationRunStatus.CREATED.value,
            ApplicationRunStatus.ACQUIRING_EVIDENCE.value,
            ApplicationRunStatus.VERIFYING.value,
        }
        recovered = 0
        for record in self.repository.list_runs(limit=1_000):
            if record.status not in nonterminal:
                continue
            completed_at = datetime.now(UTC)
            message = (
                "InterruptedRunError: the previous local process ended before this run "
                "reached a terminal Ralph state; start a new run from the persisted request"
            )
            self.repository.set_run_status(
                record.run_id,
                ApplicationRunStatus.FAILED.value,
                updated_at=completed_at,
            )
            self.repository.finish_open_attempts(
                record.run_id,
                status="interrupted",
                completed_at=completed_at,
            )
            self.repository.put_artifact(
                record.run_id,
                artifact_type="failure",
                payload={"error": message, "completed_at": completed_at},
            )
            with self._lock:
                self._results.pop(record.run_id, None)
            recovered += 1
        return recovered

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
        result.approvals = list(approvals)
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
        current_rejected = any(
            item.decision is ApprovalDecision.REJECT
            and item.brief_sha256 == approval.brief_sha256
            for item in approvals
        )
        machine_gaps = []
        if result.ralph_state is not None and result.ralph_state.latest_report is not None:
            machine_gaps = [
                item
                for item in result.ralph_state.latest_report.evaluations
                if item.criterion_id != "G-human-approval"
                and item.outcome is not CriterionOutcome.PASS
            ]
        if machine_gaps:
            result.status = ApplicationRunStatus.BLOCKED
            ralph_status = RalphStatus.BLOCKED
            goal_status = "fail"
            reason = "machine acceptance criteria remain open; approvals cannot override them"
        elif current_rejected:
            result.status = ApplicationRunStatus.BLOCKED
            ralph_status = RalphStatus.BLOCKED
            goal_status = "fail"
            reason = (
                "a required reviewer rejected the exact current brief; a new content "
                "revision is required before publication"
            )
        elif result.quality_report.publishable:
            result.status = ApplicationRunStatus.PUBLISHABLE
            ralph_status = RalphStatus.PUBLISHABLE
            goal_status = "pass"
            reason = (
                "all required machine criteria and exact-content human approvals passed"
            )
        else:
            result.status = ApplicationRunStatus.HUMAN_REQUIRED
            ralph_status = RalphStatus.HUMAN_REQUIRED
            goal_status = "human_required"
            reason = "one or more exact-content publication approvals remain outstanding"
        if result.ralph_state is not None:
            state_payload = result.ralph_state.model_dump(mode="python")
            state_payload.update(status=ralph_status, terminal_reason=reason)
            result.ralph_state = RalphState.model_validate(state_payload)
        self.repository.save_goal(
            run_id,
            criterion_key="G-human-approval",
            description=(
                "Strategy, finance, technology, and risk approve the exact brief content."
            ),
            required=True,
            status=goal_status,
            score=1 if goal_status == "pass" else 0,
            evidence={
                "brief_sha256": approval.brief_sha256,
                "decisions": [item.model_dump(mode="json") for item in approvals],
                "reason": reason,
            },
        )
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
        *,
        acquisition_attempt_id: str,
    ) -> tuple[
        OmlxAdvisorRuntime,
        DecisionFrame,
        list[ResearchBundle],
        list[EvidenceItem],
    ]:
        try:
            EgressPolicy(
                forbidden_terms=tuple(request.restricted_terms),
                max_query_chars=4_000,
            ).validate(request.public_research_context)
        except EgressViolation as exc:
            raise DecisionScopeError(
                "public_research_context failed the outbound-data policy"
            ) from exc
        if (
            request.evidence_cutoff is not None
            and request.evidence_cutoff < datetime.now(UTC).date()
        ):
            raise DecisionScopeError(
                "historical evidence_cutoff cannot be honored by live-web capture without "
                "a dated archive; use a current/future cutoff or supply archived evidence"
            )
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
        self.repository.put_artifact(
            run_id,
            artifact_type="decision_frame_checkpoint",
            payload=frame,
            attempt_id=acquisition_attempt_id,
        )
        ledger = DiscoveryLedger()
        registered_by_force: dict[ForceName, list[str]] = {
            force: [] for force in FORCE_ORDER
        }
        source_force: dict[str, ForceName] = {}
        persisted_query_ids: set[str] = set()

        def persist_new_searches() -> None:
            for query, hits in runtime.recorded_search_executions:
                if query.query_id in persisted_query_ids:
                    continue
                if query.force is None:
                    raise AnalysisServiceError(
                        "executed search is missing its Porter-force lineage"
                    )
                execution, registered = ledger.record_execution(
                    query,
                    hits,
                    provider="duckduckgo",
                )
                self.repository.record_search_execution(
                    run_id,
                    acquisition_attempt_id,
                    execution,
                    registered,
                )
                persisted_query_ids.add(query.query_id)
                for hit in registered:
                    registered_by_force[query.force].append(hit.source_id)
                    source_force[hit.source_id] = query.force

        bundles: list[ResearchBundle] = []
        for force in FORCE_ORDER:
            bundle = runtime.research_force(
                _assignment(request, force),
                _egress_policy(request),
            )
            bundles.append(bundle)
            self.repository.put_artifact(
                run_id,
                artifact_type="research_bundle_checkpoint",
                payload=bundle,
                attempt_id=acquisition_attempt_id,
            )
            # Commit each completed force's exact query/hit lineage before
            # another model call begins.
            persist_new_searches()

        # Capture selection is application-owned and round-robin balanced. The
        # generating model cannot consume the global source budget by nominating
        # only convenient hits for one force.
        source_ids = _balanced_source_ids(
            registered_by_force,
            limit=self.settings.capture_max_sources,
        )
        if not source_ids:
            raise AnalysisServiceError(
                "research returned no registered public source candidates"
            )

        evidence: list[EvidenceItem] = []
        capture_policy = CapturePolicy(
            max_content_bytes=self.settings.capture_max_bytes,
            timeout_seconds=self.settings.capture_timeout_seconds,
        )
        with SafeSourceCapture(ledger, policy=capture_policy) as capture_service:
            captured_forces: set[ForceName] = set()
            for source_id in source_ids:
                try:
                    capture = capture_service.capture(source_id)
                except SourceCaptureError as exc:
                    warnings.append(f"Capture {source_id} skipped: {type(exc).__name__}: {exc}")
                    continue
                captured_forces.add(source_force[source_id])
                self.repository.record_capture(capture)
                source_class = _conservative_source_class(capture.final_url)
                quality_score, freshness_score, applicability_score = (
                    _owned_source_scores(source_class)
                )
                evidence.append(
                    promote_capture_to_evidence(
                        capture,
                        source_class=source_class,
                        excerpt=capture.extracted_text[:10_000],
                        quality_score=quality_score,
                        freshness_score=freshness_score,
                        applicability_score=applicability_score,
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
        missing_force_capture = set(FORCE_ORDER) - captured_forces
        if missing_force_capture:
            missing = ", ".join(sorted(force.value for force in missing_force_capture))
            raise AnalysisServiceError(
                "captured-evidence coverage is incomplete for Porter forces: " + missing
            )
        runtime.freeze_captured_evidence(evidence)
        return runtime, frame, bundles, evidence

    def _persist_ralph_checkpoint(
        self,
        result: AnalysisResult,
        state: RalphState,
        manifest: Any,
        *,
        database_attempt_number: int,
    ) -> None:
        """Durably record every completed Ralph gate before another retry starts."""

        self.repository.start_attempt(
            result.run_id,
            database_attempt_number,
            attempt_id=manifest.attempt_id,
            started_at=manifest.started_at,
        )
        attempt_status = (
            "retry_required"
            if state.status is RalphStatus.RUNNING
            else state.status.value
        )
        self.repository.finish_attempt(
            manifest.attempt_id,
            status=attempt_status,
            goal_report=manifest.report,
            completed_at=manifest.completed_at,
        )
        criteria = {item.criterion_id: item for item in state.criteria}
        for evaluation in manifest.report.evaluations:
            criterion = criteria[evaluation.criterion_id]
            self.repository.save_goal(
                result.run_id,
                criterion_key=criterion.criterion_id,
                description=criterion.description,
                required=criterion.required,
                status=evaluation.outcome.value,
                score=1 if evaluation.outcome.value == "pass" else 0,
                evidence=evaluation,
                updated_at=manifest.completed_at,
            )
        self.repository.put_artifact(
            result.run_id,
            artifact_type="ralph_checkpoint",
            payload=state,
            attempt_id=manifest.attempt_id,
            created_at=manifest.completed_at,
        )

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
    ) -> None:
        if result.ralph_state is None or result.board_brief is None:
            raise AnalysisServiceError("cannot persist an incomplete Ralph result")
        if result.evidence_ledger is None or result.quality_report is None:
            raise AnalysisServiceError("cannot persist an incomplete analysis result")
        attempts = result.ralph_state.attempts
        latest_attempt_id = attempts[-1].attempt_id if attempts else None

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
        evidence_register = Path(result.artifact_paths["evidence_register"]).read_text(
            encoding="utf-8"
        )
        register_artifact = self.repository.put_artifact(
            result.run_id,
            artifact_type="evidence_register_csv",
            payload=evidence_register,
            attempt_id=latest_attempt_id,
        )
        result.artifact_ids["evidence_register"] = register_artifact.artifact_id
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
    scheme = urlsplit(url).scheme.casefold()
    regulator_domains = (
        "bis.org",
        "ecb.europa.eu",
        "bankofengland.co.uk",
        "europa.eu",
        "finra.org",
    )
    is_regulator_host = host.endswith(".gov") or any(
        host == domain or host.endswith(f".{domain}") for domain in regulator_domains
    )
    # Authority is never inferred from a hostname over cleartext HTTP.
    if scheme == "https" and is_regulator_host:
        return SourceClass.REGULATOR
    if scheme == "https" and (
        host.endswith(".edu")
        or any(
            host == domain or host.endswith(f".{domain}")
            for domain in ("arxiv.org", "doi.org")
        )
    ):
        return SourceClass.ACADEMIC
    # Unknown public pages receive the least-authoritative promotable class.
    # The quality gate can still reject them when strength or entailment is weak.
    return SourceClass.VENDOR


def _owned_source_scores(source_class: SourceClass) -> tuple[float, float, float]:
    """Conservative metadata scores owned by policy, never supplied by the model.

    Publication date and organization-specific applicability are not extracted
    by the MVP HTML capture boundary, so their components remain deliberately
    modest. Unknown pages consequently cannot clear the material-fact gate by
    themselves.
    """

    quality = {
        SourceClass.REGULATOR: 0.9,
        SourceClass.ACADEMIC: 0.8,
    }.get(source_class, 0.55)
    return quality, 0.5, 0.6


def _balanced_source_ids(
    by_force: Mapping[ForceName, list[str]],
    *,
    limit: int,
) -> list[str]:
    """Select registered hits round-robin so no force monopolizes capture."""

    selected: list[str] = []
    offsets = {force: 0 for force in FORCE_ORDER}
    while len(selected) < limit:
        progressed = False
        for force in FORCE_ORDER:
            candidates = by_force.get(force, [])
            offset = offsets[force]
            if offset >= len(candidates):
                continue
            selected.append(candidates[offset])
            offsets[force] = offset + 1
            progressed = True
            if len(selected) >= limit:
                break
        if not progressed:
            break
    return selected


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
