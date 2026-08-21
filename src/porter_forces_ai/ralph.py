"""Bounded, evidence-stable Ralph supervision for an analysis graph.

The analysis graph produces an artifact; it never decides that its own goal is
complete.  A separate deterministic evaluator assesses explicit acceptance
criteria.  Failed criteria become typed directives for a fresh attempt, while
human decisions, exhausted budgets, and repeated gaps stop the loop.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import Enum, StrEnum
from typing import Any, Protocol, TypedDict, cast
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class _FrozenModel(BaseModel):
    """Strict immutable contract used for persisted supervisor records."""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        str_strip_whitespace=True,
    )


class RalphStatus(StrEnum):
    """Terminal and non-terminal states of the supervisor."""

    RUNNING = "running"
    ACHIEVED_DRAFT = "achieved_draft"
    HUMAN_REQUIRED = "human_required"
    PUBLISHABLE = "publishable"
    BLOCKED = "blocked"


class CompletionTarget(StrEnum):
    """The highest status that a successful machine evaluation may produce."""

    DRAFT = "draft"
    PUBLISHABLE = "publishable"


class CriterionOutcome(StrEnum):
    """Result of independently evaluating one acceptance criterion."""

    PASS = "pass"
    FAIL = "fail"
    HUMAN_REQUIRED = "human_required"


class GoalCriterion(_FrozenModel):
    """One explicit, independently testable definition-of-done condition."""

    criterion_id: str = Field(pattern=r"^G-[A-Za-z0-9_-]+$", max_length=100)
    description: str = Field(min_length=8, max_length=2_000)
    verification_method: str = Field(min_length=3, max_length=2_000)
    required: bool = True
    retryable: bool = True


class CriterionEvaluation(_FrozenModel):
    """Evaluator-owned result; it is not accepted from the analysis output."""

    criterion_id: str = Field(pattern=r"^G-[A-Za-z0-9_-]+$", max_length=100)
    outcome: CriterionOutcome
    explanation: str = Field(min_length=3, max_length=4_000)
    remediation: str | None = Field(default=None, max_length=2_000)
    progress_marker: str | None = Field(
        default=None,
        max_length=500,
        description="Stable machine-readable marker for meaningful progress on this gap.",
    )

    @model_validator(mode="after")
    def require_action_for_open_gap(self) -> CriterionEvaluation:
        if self.outcome is not CriterionOutcome.PASS and not self.remediation:
            raise ValueError("failed and human-required evaluations need remediation")
        return self


class GapDirective(_FrozenModel):
    """A gap-specific instruction supplied to the next fresh attempt."""

    criterion_id: str = Field(pattern=r"^G-[A-Za-z0-9_-]+$", max_length=100)
    instruction: str = Field(min_length=3, max_length=2_000)
    progress_marker: str | None = Field(default=None, max_length=500)


class GoalReport(_FrozenModel):
    """Complete deterministic evaluation of one attempt."""

    attempt_id: str = Field(min_length=3, max_length=200)
    evaluations: tuple[CriterionEvaluation, ...] = Field(min_length=1)
    summary: str = Field(min_length=3, max_length=4_000)
    budget_units_used: int = Field(default=1, ge=1, le=1_000_000)

    @model_validator(mode="after")
    def reject_duplicate_results(self) -> GoalReport:
        ids = [item.criterion_id for item in self.evaluations]
        if len(ids) != len(set(ids)):
            raise ValueError("each criterion may be evaluated only once")
        return self

    def open_gap_fingerprint(self) -> str | None:
        """Hash stable gap identities, outcomes, and explicit progress markers."""

        gaps = sorted(
            (
                evaluation.criterion_id,
                evaluation.outcome.value,
                evaluation.progress_marker or "",
            )
            for evaluation in self.evaluations
            if evaluation.outcome is not CriterionOutcome.PASS
        )
        if not gaps:
            return None
        return _sha256(gaps)


class AttemptManifest(_FrozenModel):
    """Append-only audit record for one fresh analysis execution."""

    attempt_number: int = Field(ge=1)
    attempt_id: str = Field(min_length=3, max_length=200)
    thread_id: str = Field(min_length=3, max_length=200)
    evidence_snapshot_id: str = Field(min_length=3, max_length=300)
    started_at: datetime
    completed_at: datetime
    input_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    output_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    directives_applied: tuple[GapDirective, ...] = ()
    report: GoalReport
    gap_fingerprint: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")

    @field_validator("started_at", "completed_at")
    @classmethod
    def require_aware_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("attempt timestamps must be timezone-aware")
        return value

    @model_validator(mode="after")
    def validate_manifest(self) -> AttemptManifest:
        if self.completed_at < self.started_at:
            raise ValueError("attempt completion cannot precede its start")
        if self.report.attempt_id != self.attempt_id:
            raise ValueError("goal report must belong to this attempt")
        if self.gap_fingerprint != self.report.open_gap_fingerprint():
            raise ValueError("manifest gap fingerprint does not match its report")
        return self


class RalphState(_FrozenModel):
    """Persistent state for a complete bounded Ralph run."""

    run_id: str = Field(min_length=3, max_length=200)
    objective: dict[str, Any]
    criteria: tuple[GoalCriterion, ...] = Field(min_length=1)
    evidence_snapshot_id: str = Field(min_length=3, max_length=300)
    target: CompletionTarget = CompletionTarget.DRAFT
    status: RalphStatus = RalphStatus.RUNNING
    max_attempts: int = Field(default=4, ge=1, le=100)
    max_budget_units: int = Field(default=12, ge=1, le=1_000_000)
    consumed_budget_units: int = Field(default=0, ge=0)
    stall_limit: int = Field(
        default=2,
        ge=2,
        le=20,
        description="Consecutive occurrences of one gap fingerprint before blocking.",
    )
    consecutive_gap_count: int = Field(default=0, ge=0)
    attempts: tuple[AttemptManifest, ...] = ()
    pending_directives: tuple[GapDirective, ...] = ()
    latest_output: dict[str, Any] | None = None
    terminal_reason: str | None = Field(default=None, max_length=2_000)

    @model_validator(mode="after")
    def validate_persisted_state(self) -> RalphState:
        criterion_ids = [criterion.criterion_id for criterion in self.criteria]
        if len(criterion_ids) != len(set(criterion_ids)):
            raise ValueError("criterion IDs must be unique")
        if not any(criterion.required for criterion in self.criteria):
            raise ValueError("at least one criterion must be required")
        _sha256(self.objective)

        attempt_ids = [attempt.attempt_id for attempt in self.attempts]
        thread_ids = [attempt.thread_id for attempt in self.attempts]
        if len(attempt_ids) != len(set(attempt_ids)):
            raise ValueError("attempt IDs must be unique")
        if len(thread_ids) != len(set(thread_ids)):
            raise ValueError("thread IDs must be fresh for every attempt")
        for expected_number, attempt in enumerate(self.attempts, start=1):
            if attempt.attempt_number != expected_number:
                raise ValueError("attempt manifests must be contiguous and ordered")
            if attempt.evidence_snapshot_id != self.evidence_snapshot_id:
                raise ValueError("the evidence snapshot cannot change between attempts")
        used = sum(attempt.report.budget_units_used for attempt in self.attempts)
        if used != self.consumed_budget_units:
            raise ValueError("consumed budget must reconcile to attempt reports")
        if self.status is not RalphStatus.RUNNING and not self.terminal_reason:
            raise ValueError("terminal and paused states require a reason")
        if (
            self.status in {RalphStatus.ACHIEVED_DRAFT, RalphStatus.PUBLISHABLE}
            and self.latest_output is None
        ):
            raise ValueError("successful states must retain their accepted output")
        return self

    @property
    def latest_report(self) -> GoalReport | None:
        """Return the most recent evaluator report, if an attempt has run."""

        return self.attempts[-1].report if self.attempts else None


class AttemptContext(_FrozenModel):
    """Attempt-scoped metadata available to input builders and evaluators."""

    run_id: str
    attempt_number: int = Field(ge=1)
    attempt_id: str
    thread_id: str
    evidence_snapshot_id: str
    directives: tuple[GapDirective, ...] = ()


class AnalysisGraph(Protocol):
    """Structural protocol implemented by a compiled LangGraph graph."""

    def invoke(
        self,
        input: Mapping[str, Any],
        config: Mapping[str, Any] | None = None,
    ) -> Mapping[str, Any]: ...


class AnalysisCallable(Protocol):
    """Callable alternative for tests and non-LangGraph analysis engines."""

    def __call__(
        self,
        input: Mapping[str, Any],
        config: Mapping[str, Any],
    ) -> Mapping[str, Any]: ...


class GoalEvaluator(Protocol):
    """Independent evaluator; analysis output cannot declare its own success."""

    def __call__(
        self,
        criteria: tuple[GoalCriterion, ...],
        output: Mapping[str, Any],
        context: AttemptContext,
    ) -> GoalReport: ...


class AttemptInputBuilder(Protocol):
    """Creates graph input, optionally applying prior gap directives."""

    def __call__(
        self,
        objective: Mapping[str, Any],
        context: AttemptContext,
    ) -> Mapping[str, Any]: ...


class IdFactory(Protocol):
    """Injectable identity source used to make offline tests deterministic."""

    def __call__(self, prefix: str) -> str: ...


class _MetaGraphState(TypedDict):
    ralph_state: RalphState


AnalysisTarget = AnalysisGraph | AnalysisCallable
Clock = Callable[[], datetime]


def _default_id_factory(prefix: str) -> str:
    return f"{prefix}-{uuid4()}"


def _default_input_builder(
    objective: Mapping[str, Any],
    context: AttemptContext,
) -> Mapping[str, Any]:
    del context
    return dict(objective)


def _jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("naive datetimes cannot be persisted canonically")
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Mapping):
        if not all(isinstance(key, str) for key in value):
            raise TypeError("persistent mappings must use string keys")
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if isinstance(value, (set, frozenset)):
        converted = [_jsonable(item) for item in value]
        return sorted(converted, key=lambda item: json.dumps(item, sort_keys=True))
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"value of type {type(value).__name__} is not persistable")


def _sha256(value: Any) -> str:
    encoded = json.dumps(
        _jsonable(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
        allow_nan=False,
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


class RalphSupervisor:
    """Execute and independently verify bounded fresh analysis attempts."""

    def __init__(
        self,
        analysis: AnalysisTarget,
        evaluator: GoalEvaluator,
        *,
        input_builder: AttemptInputBuilder = _default_input_builder,
        id_factory: IdFactory = _default_id_factory,
        clock: Clock = lambda: datetime.now(UTC),
        base_config: Mapping[str, Any] | None = None,
    ) -> None:
        self._analysis = analysis
        self._evaluator = evaluator
        self._input_builder = input_builder
        self._id_factory = id_factory
        self._clock = clock
        self._base_config = dict(base_config or {})

    def new_state(
        self,
        *,
        objective: Mapping[str, Any],
        criteria: tuple[GoalCriterion, ...],
        evidence_snapshot_id: str,
        target: CompletionTarget = CompletionTarget.DRAFT,
        max_attempts: int = 4,
        max_budget_units: int = 12,
        stall_limit: int = 2,
    ) -> RalphState:
        """Create a validated run whose evidence universe is fixed by ID."""

        return RalphState(
            run_id=self._id_factory("ralph"),
            objective=dict(objective),
            criteria=criteria,
            evidence_snapshot_id=evidence_snapshot_id,
            target=target,
            max_attempts=max_attempts,
            max_budget_units=max_budget_units,
            stall_limit=stall_limit,
        )

    def run(self, state: RalphState) -> RalphState:
        """Run synchronously until success, pause, or a deterministic stop."""

        current = state
        while current.status is RalphStatus.RUNNING:
            current = self.step(current)
        return current

    def step(self, state: RalphState) -> RalphState:
        """Run exactly one fresh attempt and make one supervisor decision."""

        if state.status is not RalphStatus.RUNNING:
            return state
        if len(state.attempts) >= state.max_attempts:
            return self._updated(
                state,
                status=RalphStatus.BLOCKED,
                terminal_reason="maximum attempts exhausted before a new attempt",
            )
        if state.consumed_budget_units >= state.max_budget_units:
            return self._updated(
                state,
                status=RalphStatus.BLOCKED,
                terminal_reason="budget exhausted before a new attempt",
            )

        context = AttemptContext(
            run_id=state.run_id,
            attempt_number=len(state.attempts) + 1,
            attempt_id=self._id_factory("attempt"),
            thread_id=self._id_factory("thread"),
            evidence_snapshot_id=state.evidence_snapshot_id,
            directives=state.pending_directives,
        )
        started_at = self._aware_now()
        attempt_input = dict(self._input_builder(state.objective, context))
        output = self._invoke(attempt_input, self._attempt_config(context))
        report = self._evaluator(state.criteria, output, context)
        self._validate_report(state.criteria, context, report)
        completed_at = self._aware_now()

        directives = self._directives(report)
        fingerprint = report.open_gap_fingerprint()
        previous_fingerprint = (
            state.attempts[-1].gap_fingerprint if state.attempts else None
        )
        consecutive_gaps = (
            state.consecutive_gap_count + 1
            if fingerprint is not None and fingerprint == previous_fingerprint
            else (1 if fingerprint is not None else 0)
        )
        manifest = AttemptManifest(
            attempt_number=context.attempt_number,
            attempt_id=context.attempt_id,
            thread_id=context.thread_id,
            evidence_snapshot_id=context.evidence_snapshot_id,
            started_at=started_at,
            completed_at=completed_at,
            input_sha256=_sha256(attempt_input),
            output_sha256=_sha256(output),
            directives_applied=context.directives,
            report=report,
            gap_fingerprint=fingerprint,
        )
        attempts = (*state.attempts, manifest)
        consumed = state.consumed_budget_units + report.budget_units_used

        status, reason = self._route(
            state=state,
            report=report,
            attempt_count=len(attempts),
            consumed_budget=consumed,
            consecutive_gaps=consecutive_gaps,
        )
        return self._updated(
            state,
            attempts=attempts,
            consumed_budget_units=consumed,
            consecutive_gap_count=consecutive_gaps,
            pending_directives=directives if status is RalphStatus.RUNNING else (),
            latest_output=dict(output),
            status=status,
            terminal_reason=reason,
        )

    def build_meta_graph(self, *, checkpointer: Any | None = None) -> Any:
        """Compile a LangGraph StateGraph that loops over the same ``step`` contract."""

        from langgraph.graph import END, START, StateGraph

        def execute_attempt(graph_state: _MetaGraphState) -> dict[str, Any]:
            return {"ralph_state": self.step(graph_state["ralph_state"])}

        def route(graph_state: _MetaGraphState) -> str:
            if graph_state["ralph_state"].status is RalphStatus.RUNNING:
                return "retry"
            return "done"

        builder = StateGraph(_MetaGraphState)
        # LangGraph's callable overload is narrower than its runtime support for
        # TypedDict update mappings; the cast is isolated at that library seam.
        builder.add_node("attempt", cast(Any, execute_attempt))
        builder.add_edge(START, "attempt")
        builder.add_conditional_edges(
            "attempt",
            route,
            {"retry": "attempt", "done": END},
        )
        return builder.compile(checkpointer=checkpointer)

    def _invoke(
        self,
        attempt_input: Mapping[str, Any],
        config: Mapping[str, Any],
    ) -> dict[str, Any]:
        invoke = getattr(self._analysis, "invoke", None)
        if callable(invoke):
            raw_output = invoke(attempt_input, config)
        else:
            callable_analysis = cast(AnalysisCallable, self._analysis)
            raw_output = callable_analysis(attempt_input, config)
        if not isinstance(raw_output, Mapping):
            raise TypeError("analysis graph must return a mapping")
        output = dict(raw_output)
        _sha256(output)
        return output

    def _attempt_config(self, context: AttemptContext) -> dict[str, Any]:
        config = dict(self._base_config)
        configurable = dict(cast(Mapping[str, Any], config.get("configurable", {})))
        metadata = dict(cast(Mapping[str, Any], config.get("metadata", {})))
        configurable["thread_id"] = context.thread_id
        metadata["ralph"] = context.model_dump(mode="json")
        config["configurable"] = configurable
        config["metadata"] = metadata
        return config

    def _route(
        self,
        *,
        state: RalphState,
        report: GoalReport,
        attempt_count: int,
        consumed_budget: int,
        consecutive_gaps: int,
    ) -> tuple[RalphStatus, str | None]:
        criterion_by_id = {item.criterion_id: item for item in state.criteria}
        required = [
            evaluation
            for evaluation in report.evaluations
            if criterion_by_id[evaluation.criterion_id].required
        ]
        if any(item.outcome is CriterionOutcome.HUMAN_REQUIRED for item in required):
            return RalphStatus.HUMAN_REQUIRED, "one or more criteria require human judgment"
        if all(item.outcome is CriterionOutcome.PASS for item in required):
            if state.target is CompletionTarget.PUBLISHABLE:
                return RalphStatus.PUBLISHABLE, "all required publishability criteria passed"
            return RalphStatus.ACHIEVED_DRAFT, "all required draft criteria passed"

        failed = [item for item in required if item.outcome is CriterionOutcome.FAIL]
        if any(not criterion_by_id[item.criterion_id].retryable for item in failed):
            return RalphStatus.BLOCKED, "a required non-retryable criterion failed"
        if attempt_count >= state.max_attempts:
            return RalphStatus.BLOCKED, "maximum attempts exhausted with open criteria"
        if consumed_budget >= state.max_budget_units:
            return RalphStatus.BLOCKED, "budget exhausted with open criteria"
        if consecutive_gaps >= state.stall_limit:
            return RalphStatus.BLOCKED, "gap fingerprint repeated without measurable progress"
        return RalphStatus.RUNNING, None

    @staticmethod
    def _directives(report: GoalReport) -> tuple[GapDirective, ...]:
        return tuple(
            GapDirective(
                criterion_id=evaluation.criterion_id,
                instruction=cast(str, evaluation.remediation),
                progress_marker=evaluation.progress_marker,
            )
            for evaluation in report.evaluations
            if evaluation.outcome is CriterionOutcome.FAIL
        )

    @staticmethod
    def _validate_report(
        criteria: tuple[GoalCriterion, ...],
        context: AttemptContext,
        report: GoalReport,
    ) -> None:
        if report.attempt_id != context.attempt_id:
            raise ValueError("evaluator report belongs to a different attempt")
        expected = {criterion.criterion_id for criterion in criteria}
        actual = {evaluation.criterion_id for evaluation in report.evaluations}
        if actual != expected:
            missing = sorted(expected - actual)
            unexpected = sorted(actual - expected)
            raise ValueError(
                f"evaluator must report every criterion exactly once; "
                f"missing={missing}, unexpected={unexpected}"
            )

    def _aware_now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("Ralph clock must return timezone-aware datetimes")
        return value

    @staticmethod
    def _updated(state: RalphState, **updates: Any) -> RalphState:
        payload = state.model_dump(mode="python")
        payload.update(updates)
        return RalphState.model_validate(payload)


def run_ralph(
    analysis: AnalysisTarget,
    evaluator: GoalEvaluator,
    *,
    objective: Mapping[str, Any],
    criteria: tuple[GoalCriterion, ...],
    evidence_snapshot_id: str,
    target: CompletionTarget = CompletionTarget.DRAFT,
    max_attempts: int = 4,
    max_budget_units: int = 12,
    stall_limit: int = 2,
    input_builder: AttemptInputBuilder = _default_input_builder,
) -> RalphState:
    """Convenience synchronous entry point for callers that do not need checkpoints."""

    supervisor = RalphSupervisor(
        analysis,
        evaluator,
        input_builder=input_builder,
    )
    initial = supervisor.new_state(
        objective=objective,
        criteria=criteria,
        evidence_snapshot_id=evidence_snapshot_id,
        target=target,
        max_attempts=max_attempts,
        max_budget_units=max_budget_units,
        stall_limit=stall_limit,
    )
    return supervisor.run(initial)
