from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from threading import Event, Thread
from urllib.parse import urlsplit

import pytest

from porter_forces_ai.domain import (
    FORCE_ORDER,
    ApprovalDecision,
    ApprovalRole,
    DecisionRequest,
    ForceName,
    OrganizationArchetype,
    ResearchQuery,
    SearchHit,
)
from porter_forces_ai.economics import RangeEstimate, ScenarioEconomicsInputs
from porter_forces_ai.ralph import (
    CompletionTarget,
    CriterionOutcome,
    RalphStatus,
)
from porter_forces_ai.repository import (
    RepositoryWriterLeaseConflictError,
    SQLiteRunRepository,
)
from porter_forces_ai.service import (
    AnalysisMode,
    AnalysisResult,
    AnalysisService,
    AnalysisServiceError,
    AnalysisSubmission,
    ApplicationRunStatus,
    _balanced_source_ids,
    _capture_source_candidates,
    _conservative_source_class,
)
from porter_forces_ai.settings import Settings
from porter_forces_ai.source_capture import (
    CapturedSource,
    DiscoveryLedger,
    SourceFetchError,
    UnsupportedSourceTypeError,
)


def _submission(*, target: CompletionTarget = CompletionTarget.DRAFT) -> AnalysisSubmission:
    return AnalysisSubmission(
        request=DecisionRequest(
            question=(
                "Should a global bank authorize one controlled AI workflow now, "
                "and what happens if it waits?"
            ),
            archetype=OrganizationArchetype.GLOBAL_BANK,
            organization_name="Illustrative Bank",
            public_research_context=(
                "Public evidence about generative AI adoption in regulated global banks."
            ),
        ),
        mode=AnalysisMode.DEMO,
        target=target,
    )


def _service(tmp_path: Path) -> AnalysisService:
    settings = Settings(
        _env_file=None,
        database_path=tmp_path / "runs.db",
        artifacts_dir=tmp_path / "artifacts",
    )
    return AnalysisService(
        settings,
        repository=SQLiteRunRepository(settings.database_path),
    )


def test_demo_service_runs_ralph_persists_and_renders(tmp_path: Path) -> None:
    service = _service(tmp_path)
    try:
        result = service.analyze(_submission())

        assert result.status is ApplicationRunStatus.ACHIEVED_DRAFT
        assert result.ralph_state is not None
        assert len(result.ralph_state.attempts) == 1
        assert result.quality_report is not None
        assert result.quality_report.draft_valid is True
        assert set(result.artifact_paths) == {
            "board_memo",
            "audit_sidecar",
            "evidence_register",
        }
        assert all(Path(path).exists() for path in result.artifact_paths.values())
        counts = service.repository.table_counts()
        assert counts["runs"] == 1
        assert counts["attempts"] == 2  # acquisition plus one Ralph attempt
        assert counts["goals"] >= 7
        assert counts["artifacts"] == 5  # checkpoint plus four immutable outputs
    finally:
        service.repository.close()


def test_analysis_service_enforces_and_releases_one_writer_per_database(
    tmp_path: Path,
) -> None:
    settings = Settings(
        _env_file=None,
        database_path=tmp_path / "single-writer.db",
        artifacts_dir=tmp_path / "artifacts",
    )
    first = AnalysisService(settings)
    inspection = SQLiteRunRepository(settings.database_path, read_only=True)
    try:
        # A direct repository remains available for read-only inspection while
        # the service owns the advisory writer lease.
        assert inspection.list_runs() == ()
        with pytest.raises(
            RepositoryWriterLeaseConflictError,
            match="writer lease",
        ):
            AnalysisService(settings)
    finally:
        inspection.close()
        first.close()

    replacement = AnalysisService(settings)
    replacement.close()


def test_repository_close_releases_service_writer_lease(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        database_path=tmp_path / "repository-close.db",
        artifacts_dir=tmp_path / "artifacts",
    )
    repository = SQLiteRunRepository(settings.database_path)
    first = AnalysisService(settings, repository=repository)
    repository.close()

    replacement = AnalysisService(settings)
    replacement.close()
    first.close()


def test_read_only_service_hydrates_but_rejects_every_write_path(tmp_path: Path) -> None:
    settings = Settings(
        _env_file=None,
        database_path=tmp_path / "read-only-service.db",
        artifacts_dir=tmp_path / "artifacts",
    )
    writer = AnalysisService(settings)
    try:
        completed = writer.analyze(_submission())
        reader = AnalysisService(settings, read_only=True)
        try:
            hydrated = reader.get_result(completed.run_id)
            assert hydrated is not None
            assert hydrated.status is ApplicationRunStatus.ACHIEVED_DRAFT
            with pytest.raises(AnalysisServiceError, match="read-only"):
                reader.analyze(_submission())
            with pytest.raises(AnalysisServiceError, match="read-only"):
                reader.recover_interrupted_runs()
            with pytest.raises(AnalysisServiceError, match="read-only"):
                reader.record_approval(
                    completed.run_id,
                    role=ApprovalRole.RISK,
                    reviewer="Read-only reviewer",
                    decision=ApprovalDecision.APPROVE,
                )
        finally:
            reader.close()
    finally:
        writer.close()


def test_read_only_service_rejects_a_writable_injected_repository(
    tmp_path: Path,
) -> None:
    settings = Settings(
        _env_file=None,
        database_path=tmp_path / "unsafe-read-projection.db",
        artifacts_dir=tmp_path / "artifacts",
    )
    repository = SQLiteRunRepository(settings.database_path)
    try:
        with pytest.raises(AnalysisServiceError, match="read-only repository"):
            AnalysisService(settings, repository=repository, read_only=True)
    finally:
        repository.close()


def test_writer_lease_conflicts_across_processes_and_releases_on_exit(
    tmp_path: Path,
) -> None:
    settings = Settings(
        _env_file=None,
        database_path=tmp_path / "subprocess-writer.db",
        artifacts_dir=tmp_path / "artifacts",
    )
    script = """
import sys
from pathlib import Path
from porter_forces_ai.repository import RepositoryWriterLeaseConflictError
from porter_forces_ai.service import AnalysisService
from porter_forces_ai.settings import Settings

settings = Settings(
    _env_file=None,
    database_path=Path(sys.argv[1]),
    artifacts_dir=Path(sys.argv[2]),
)
try:
    service = AnalysisService(settings)
except RepositoryWriterLeaseConflictError:
    raise SystemExit(23)
raise SystemExit(0)
"""
    environment = os.environ.copy()
    first = AnalysisService(settings)
    try:
        conflict = subprocess.run(
            [
                sys.executable,
                "-c",
                script,
                str(settings.database_path),
                str(settings.artifacts_dir),
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
            env=environment,
        )
        assert conflict.returncode == 23, conflict.stderr
    finally:
        first.close()

    acquired_then_exited = subprocess.run(
        [
            sys.executable,
            "-c",
            script,
            str(settings.database_path),
            str(settings.artifacts_dir),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=10,
        env=environment,
    )
    assert acquired_then_exited.returncode == 0, acquired_then_exited.stderr
    # The subprocess deliberately omitted close(); POSIX process teardown and
    # the registered release callback must leave no stale lease.
    replacement = AnalysisService(settings)
    replacement.close()


def test_recovery_processes_more_than_one_thousand_nonterminal_runs(
    tmp_path: Path,
) -> None:
    settings = Settings(
        _env_file=None,
        database_path=tmp_path / "large-recovery.db",
        artifacts_dir=tmp_path / "artifacts",
    )
    service = AnalysisService(settings)
    nonterminal = ("created", "acquiring_evidence", "verifying")
    try:
        with service.repository.transaction():
            for index in range(1_005):
                service.repository.create_run(
                    {"ordinal": index},
                    run_id=f"RUN-interrupted-{index:04d}",
                    status=nonterminal[index % len(nonterminal)],
                )
            service.repository.create_run(
                {"terminal": True},
                run_id="RUN-terminal-history",
                status="achieved_draft",
            )

        assert service.recover_interrupted_runs() == 1_005
        assert service.repository.list_runs_with_statuses(nonterminal) == ()
        assert service.repository.get_run("RUN-terminal-history").status == (
            "achieved_draft"
        )
        assert service.repository.table_counts()["artifacts"] == 1_005
    finally:
        service.close()


def test_publishable_request_pauses_then_four_exact_approvals_publish(tmp_path: Path) -> None:
    service = _service(tmp_path)
    try:
        result = service.analyze(_submission(target=CompletionTarget.PUBLISHABLE))
        assert result.status is ApplicationRunStatus.HUMAN_REQUIRED
        initial_memos = service.repository.list_artifacts(
            result.run_id,
            artifact_type="board_memo_markdown",
        )
        assert len(initial_memos) == 1
        assert "Publication approvals: **not complete**" in initial_memos[0].payload
        assert "Ralph verification: **human_required**" in initial_memos[0].payload

        for role in ApprovalRole:
            result = service.record_approval(
                result.run_id,
                role=role,
                reviewer=f"{role.value} accountable reviewer",
                decision=ApprovalDecision.APPROVE,
            )

        assert result.status is ApplicationRunStatus.PUBLISHABLE
        assert result.quality_report is not None
        assert result.quality_report.publishable is True
        assert result.ralph_state is not None
        assert result.ralph_state.status.value == "publishable"
        assert result.human_approval is not None
        assert result.human_approval.outcome is CriterionOutcome.PASS
        assert result.human_approval.attempt_outcome is CriterionOutcome.HUMAN_REQUIRED
        assert result.human_approval.missing_roles == []
        memo_revisions = service.repository.list_artifacts(
            result.run_id,
            artifact_type="board_memo_markdown",
        )
        assert len(memo_revisions) == 5
        assert len({item.content_sha256 for item in memo_revisions}) == 5
        assert memo_revisions[0].artifact_id == result.artifact_ids["board_memo"]
        assert "Publication approvals: **complete**" in memo_revisions[0].payload
        assert "Post-human approval gate: **pass**" in memo_revisions[0].payload
        assert "Ralph verification: **publishable**" in memo_revisions[0].payload
        assert "Publication approvals: **not complete**" in memo_revisions[-1].payload
        assert "Ralph verification: **human_required**" in memo_revisions[-1].payload
        local_memo = Path(result.artifact_paths["board_memo"]).read_text(encoding="utf-8")
        assert local_memo == memo_revisions[0].payload
        human_goal = next(
            item
            for item in service.repository.list_goals(result.run_id)
            if item.criterion_key == "G-human-approval"
        )
        assert human_goal.status == "pass"
    finally:
        service.repository.close()


def test_publishable_human_projection_and_latest_memo_survive_restart(
    tmp_path: Path,
) -> None:
    first = _service(tmp_path)
    result = first.analyze(_submission(target=CompletionTarget.PUBLISHABLE))
    for role in ApprovalRole:
        result = first.record_approval(
            result.run_id,
            role=role,
            reviewer=f"{role.value} accountable reviewer",
            decision=ApprovalDecision.APPROVE,
        )
    run_id = result.run_id
    newest_memo_id = result.artifact_ids["board_memo"]
    first.repository.close()

    second = _service(tmp_path)
    try:
        restored = second.get_result(run_id)
        assert restored is not None
        assert restored.status is ApplicationRunStatus.PUBLISHABLE
        assert restored.quality_report is not None
        assert restored.quality_report.publishable is True
        assert restored.ralph_state is not None
        assert restored.ralph_state.status is RalphStatus.PUBLISHABLE
        # Attempt-time results are immutable; the explicit projection records
        # the later human transition without rewriting that history.
        latest_human_attempt = next(
            item
            for item in restored.ralph_state.latest_report.evaluations
            if item.criterion_id == "G-human-approval"
        )
        assert latest_human_attempt.outcome is CriterionOutcome.HUMAN_REQUIRED
        assert restored.human_approval is not None
        assert restored.human_approval.outcome is CriterionOutcome.PASS
        assert restored.human_approval.attempt_outcome is CriterionOutcome.HUMAN_REQUIRED
        assert len(restored.approvals) == 4
        assert restored.artifact_ids["board_memo"] == newest_memo_id
        latest_memo = second.repository.list_artifacts(
            run_id,
            artifact_type="board_memo_markdown",
        )[0]
        assert latest_memo.artifact_id == newest_memo_id
        assert "Publication approvals: **complete**" in latest_memo.payload
        human_goal = next(
            item
            for item in second.repository.list_goals(run_id)
            if item.criterion_key == "G-human-approval"
        )
        assert human_goal.status == "pass"
        assert human_goal.evidence["human_gate"]["outcome"] == "pass"
    finally:
        second.repository.close()


def test_concurrent_approvals_cannot_publish_an_older_cache_revision(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    try:
        initial = service.analyze(_submission(target=CompletionTarget.PUBLISHABLE))
        first_waiting = Event()
        second_cached = Event()
        original_cache = service._cache

        def ordered_cache(candidate: AnalysisResult) -> None:
            approval_count = len(candidate.approvals)
            if approval_count == 1:
                first_waiting.set()
                second_cached.wait(timeout=0.25)
            original_cache(candidate)
            if approval_count == 2:
                second_cached.set()

        service._cache = ordered_cache  # type: ignore[method-assign]
        errors: list[BaseException] = []

        def approve(role: ApprovalRole) -> None:
            try:
                service.record_approval(
                    initial.run_id,
                    role=role,
                    reviewer=f"{role.value} accountable reviewer",
                    decision=ApprovalDecision.APPROVE,
                )
            except BaseException as exc:  # pragma: no cover - surfaced below
                errors.append(exc)

        first_thread = Thread(target=approve, args=(ApprovalRole.STRATEGY,))
        first_thread.start()
        assert first_waiting.wait(timeout=2)
        second_thread = Thread(target=approve, args=(ApprovalRole.FINANCE,))
        second_thread.start()
        first_thread.join(timeout=3)
        second_thread.join(timeout=3)

        assert not first_thread.is_alive()
        assert not second_thread.is_alive()
        assert errors == []
        cached = service.get_result(initial.run_id)
        assert cached is not None
        assert len(cached.approvals) == 2
        board_artifact_id = cached.artifact_ids["board_brief"]
        assert len(service.repository.list_approvals(board_artifact_id)) == 2
        assert len(
            service.repository.list_artifacts(
                initial.run_id,
                artifact_type="analysis_result_revision",
            )
        ) == 2
    finally:
        service.repository.close()


def test_exact_content_rejection_blocks_and_cannot_be_approved_away(
    tmp_path: Path,
) -> None:
    service = _service(tmp_path)
    try:
        result = service.analyze(_submission(target=CompletionTarget.PUBLISHABLE))
        result = service.record_approval(
            result.run_id,
            role=ApprovalRole.RISK,
            reviewer="Accountable CRO",
            decision=ApprovalDecision.REJECT,
        )
        assert result.status is ApplicationRunStatus.BLOCKED
        assert result.ralph_state is not None
        assert result.ralph_state.status.value == "blocked"

        for role in ApprovalRole:
            result = service.record_approval(
                result.run_id,
                role=role,
                reviewer=f"{role.value} second reviewer",
                decision=ApprovalDecision.APPROVE,
            )
        assert result.status is ApplicationRunStatus.BLOCKED
        human_goal = next(
            item
            for item in service.repository.list_goals(result.run_id)
            if item.criterion_key == "G-human-approval"
        )
        assert human_goal.status == "fail"
    finally:
        service.repository.close()


def test_finance_owned_negative_upside_changes_verified_recommendation(
    tmp_path: Path,
) -> None:
    def money(value: str, basis: str) -> RangeEstimate:
        return RangeEstimate(
            low=Decimal(value),
            base=Decimal(value),
            high=Decimal(value),
            unit="USD",
            basis_ids=[basis],
            owner="Finance",
        )
    ratio = RangeEstimate(
        low=Decimal("0"),
        base=Decimal("0"),
        high=Decimal("0"),
        unit="ratio",
        basis_ids=["A-zero-benefit"],
        owner="Finance",
    )
    scenario = ScenarioEconomicsInputs(
        scenario_name="Controlled workflow deployment",
        horizon_years=3,
        discount_rate=Decimal("0.1"),
        upfront_cost=money("1000000000", "A-upfront"),
        annual_gross_benefit=money("0", "A-zero-benefit"),
        benefit_realization_rate=ratio,
        annual_run_cost=money("100000000", "A-run"),
        annual_control_cost=money("100000000", "A-control"),
        annual_expected_loss=money("100000000", "A-loss"),
    )
    submission = _submission().model_copy(update={"scenario_economics": [scenario]})
    service = _service(tmp_path)
    try:
        result = service.analyze(submission)
        assert result.status is ApplicationRunStatus.ACHIEVED_DRAFT
        assert result.scenario_economics[0].npv.high < 0
        assert result.board_brief is not None
        recommendation = result.board_brief.recommendation.text.casefold()
        assert "negative" in recommendation
        assert "do not scale" in recommendation
        economics_goal = next(
            item
            for item in service.repository.list_goals(result.run_id)
            if item.criterion_key == "G-economics-coherence"
        )
        assert economics_goal.status == "pass"
    finally:
        service.repository.close()


def test_capture_selection_is_force_balanced_and_authority_uses_domain_boundaries() -> None:
    candidates = {
        force: [f"{force.value}-1", f"{force.value}-2"] for force in FORCE_ORDER
    }
    selected = _balanced_source_ids(candidates, limit=7)

    assert selected[:5] == [f"{force.value}-1" for force in FORCE_ORDER]
    assert selected[5:] == [
        f"{ForceName.NEW_ENTRANTS.value}-2",
        f"{ForceName.SUPPLIER_POWER.value}-2",
    ]
    assert (
        _conservative_source_class("https://research.bis.org/report")
        .value
        == "regulator"
    )
    assert _conservative_source_class("https://evilbis.org/report").value == "vendor"
    assert _conservative_source_class("http://bis.org/report").value == "vendor"

    diverse = _balanced_source_ids(
        {
            force: [f"{force.value}-same", f"{force.value}-unique"]
            for force in FORCE_ORDER
        },
        limit=5,
        diversity_keys={
            **{f"{force.value}-same": "same.example" for force in FORCE_ORDER},
            **{
                f"{force.value}-unique": f"{force.value}.example"
                for force in FORCE_ORDER
            },
        },
    )
    assert diverse[0].endswith("-same")
    assert all(item.endswith("-unique") for item in diverse[1:])


def test_capture_scheduler_backfills_uncovered_force_within_hard_attempt_cap() -> None:
    candidates = {
        force: [f"{force.value}-{index}" for index in range(1, 6)]
        for force in FORCE_ORDER
    }
    backfilled_force = FORCE_ORDER[0]
    failed = set(candidates[backfilled_force][:3])
    attempted: list[tuple[ForceName, str]] = []
    covered: set[ForceName] = set()

    def attempt(force: ForceName, source_id: str) -> bool:
        attempted.append((force, source_id))
        if source_id in failed:
            return False
        covered.add(force)
        return True

    selected = _capture_source_candidates(
        candidates,
        limit=15,
        diversity_keys=None,
        attempt=attempt,
    )

    assert len(selected) == 15
    assert len(set(selected)) == 15
    assert selected == tuple(source_id for _, source_id in attempted)
    assert set(FORCE_ORDER) == covered
    assert selected[:8] == (
        candidates[backfilled_force][0],
        *(candidates[force][0] for force in FORCE_ORDER[1:]),
        *candidates[backfilled_force][1:4],
    )


def test_capture_scheduler_fails_before_fetch_when_a_force_has_no_candidates() -> None:
    missing_force = ForceName.RIVALRY
    candidates = {
        force: ([] if force is missing_force else [f"{force.value}-1"])
        for force in FORCE_ORDER
    }
    attempted: list[str] = []

    with pytest.raises(AnalysisServiceError, match=missing_force.value):
        _capture_source_candidates(
            candidates,
            limit=5,
            diversity_keys=None,
            attempt=lambda _force, source_id: not attempted.append(source_id),
        )

    assert attempted == []


def test_capture_scheduler_failure_consumes_budget_without_overrun() -> None:
    candidates = {
        force: [f"{force.value}-1", f"{force.value}-2"] for force in FORCE_ORDER
    }
    failed_source = candidates[FORCE_ORDER[0]][0]
    attempted: list[str] = []

    def attempt(_force: ForceName, source_id: str) -> bool:
        attempted.append(source_id)
        return source_id != failed_source

    with pytest.raises(AnalysisServiceError, match=FORCE_ORDER[0].value):
        _capture_source_candidates(
            candidates,
            limit=5,
            diversity_keys=None,
            attempt=attempt,
        )

    assert len(attempted) == 5
    assert len(set(attempted)) == 5
    assert candidates[FORCE_ORDER[0]][1] not in attempted


def test_capture_scheduler_retries_missing_forces_round_robin_and_preserves_diversity() -> None:
    candidates = {
        force: [f"{force.value}-same", f"{force.value}-unique"]
        for force in FORCE_ORDER
    }
    diversity_keys = {
        **{f"{force.value}-same": "same.example" for force in FORCE_ORDER},
        **{
            f"{force.value}-unique": f"{force.value}.example"
            for force in FORCE_ORDER
        },
    }
    first_two_forces = set(FORCE_ORDER[:2])
    attempted: list[tuple[ForceName, str]] = []
    attempts_by_force: dict[ForceName, int] = {}

    def attempt(force: ForceName, source_id: str) -> bool:
        attempted.append((force, source_id))
        attempts_by_force[force] = attempts_by_force.get(force, 0) + 1
        return not (force in first_two_forces and attempts_by_force[force] == 1)

    selected = _capture_source_candidates(
        candidates,
        limit=7,
        diversity_keys=diversity_keys,
        attempt=attempt,
    )

    assert selected[:5] == (
        candidates[FORCE_ORDER[0]][0],
        *(candidates[force][1] for force in FORCE_ORDER[1:]),
    )
    assert selected[5:] == (
        candidates[FORCE_ORDER[0]][1],
        candidates[FORCE_ORDER[1]][0],
    )
    assert len({source_id for _, source_id in attempted}) == 7


def test_live_capture_integration_backfills_and_persists_successes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service = _service(tmp_path)
    submission = _submission().model_copy(update={"mode": AnalysisMode.LIVE})
    run_id = "RUN-capture-backfill"
    attempt_id = "ACQ-capture-backfill"
    ledger = DiscoveryLedger()
    registered_by_force: dict[ForceName, list[str]] = {}
    source_order: dict[str, tuple[str, int]] = {}
    diversity_keys: dict[str, str] = {}
    observed_at = datetime(2026, 8, 21, tzinfo=UTC)

    service.repository.create_run(
        submission,
        run_id=run_id,
        status=ApplicationRunStatus.ACQUIRING_EVIDENCE.value,
    )
    service.repository.start_attempt(run_id, 1, attempt_id=attempt_id)
    for force_index, force in enumerate(FORCE_ORDER, start=1):
        query = ResearchQuery(
            query_id=f"Q-{force.value}-0001",
            force=force,
            query=f"public evidence for {force.value}",
            rationale="Exercise coverage-first capture scheduling",
        )
        execution, hits = ledger.record_execution(
            query,
            [
                SearchHit(
                    query_id=query.query_id,
                    title=f"{force.value} source {rank}",
                    url=f"https://source-{force_index}-{rank}.example/report",
                    provider="duckduckgo",
                    retrieved_at=observed_at,
                )
                for rank in range(1, 6)
            ],
            provider="duckduckgo",
            executed_at=observed_at,
        )
        service.repository.record_search_execution(run_id, attempt_id, execution, hits)
        registered_by_force[force] = [hit.source_id for hit in hits]
        for hit in hits:
            source_order[hit.source_id] = (query.query_id, hit.rank)
            diversity_keys[hit.source_id] = urlsplit(hit.url).hostname or hit.url

    backfilled_force = FORCE_ORDER[0]
    failed_ids = set(registered_by_force[backfilled_force][:3])
    attempts: list[str] = []

    class FakeSafeSourceCapture:
        def __init__(
            self,
            candidate_ledger: DiscoveryLedger,
            *,
            policy: object,
        ) -> None:
            assert candidate_ledger is ledger
            assert policy is not None

        def __enter__(self) -> FakeSafeSourceCapture:
            return self

        def __exit__(self, *_args: object) -> None:
            return None

        def capture(self, source_id: str) -> CapturedSource:
            attempts.append(source_id)
            if source_id in failed_ids:
                if source_id == registered_by_force[backfilled_force][0]:
                    raise UnsupportedSourceTypeError("scripted PDF response")
                raise SourceFetchError("scripted HTTP status 403")
            hit = ledger.require_hit(source_id)
            extracted_text = (
                f"Captured public evidence from {hit.title} about competitive conditions."
            )
            content = extracted_text.encode("utf-8")
            content_hash = hashlib.sha256(content).hexdigest()
            capture_basis = f"{source_id}:{hit.url}:{content_hash}".encode()
            return CapturedSource(
                capture_id=(
                    f"CAP-{hashlib.sha256(capture_basis).hexdigest()[:32]}"
                ),
                source_id=source_id,
                execution_id=hit.execution_id,
                query_id=hit.query_id,
                discovered_url=hit.url,
                final_url=hit.url,
                title=hit.title,
                publisher=urlsplit(hit.url).hostname or "unknown publisher",
                retrieved_at=observed_at,
                media_type="text/plain",
                byte_length=len(content),
                content_sha256=content_hash,
                extracted_text_sha256=hashlib.sha256(content).hexdigest(),
                extracted_text=extracted_text,
                text_truncated=False,
            )

    monkeypatch.setattr(
        "porter_forces_ai.service.SafeSourceCapture",
        FakeSafeSourceCapture,
    )
    warnings: list[str] = []
    try:
        evidence = service._capture_live_candidates(
            submission.request,
            warnings,
            ledger=ledger,
            registered_by_force=registered_by_force,
            source_order=source_order,
            source_diversity_key=diversity_keys,
        )

        assert len(attempts) == service.settings.capture_max_sources
        assert len(set(attempts)) == len(attempts)
        assert registered_by_force[backfilled_force][3] in attempts
        assert len(warnings) == 3
        assert len(evidence) == len(attempts) - len(failed_ids)
        assert service.repository.table_counts()["captures"] == len(evidence)
        captured_hosts = {urlsplit(item.source_url or "").hostname for item in evidence}
        assert all(
            any(f"source-{force_index}-" in (host or "") for host in captured_hosts)
            for force_index in range(1, len(FORCE_ORDER) + 1)
        )
    finally:
        service.repository.close()


def test_live_public_context_is_egress_checked_before_model_creation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    created = False

    def forbidden_model(_settings: Settings) -> object:
        nonlocal created
        created = True
        return object()

    monkeypatch.setattr("porter_forces_ai.service.create_chat_model", forbidden_model)
    base = _submission()
    unsafe_request = base.request.model_copy(
        update={
            "public_research_context": (
                "Public banking research about Project Cedar deployment plans."
            ),
            "restricted_terms": ["Project Cedar"],
        }
    )
    submission = base.model_copy(
        update={"mode": AnalysisMode.LIVE, "request": unsafe_request}
    )
    service = _service(tmp_path)
    try:
        with pytest.raises(AnalysisServiceError, match="outbound-data policy"):
            service.analyze(submission)
        assert created is False
        assert service.repository.table_counts()["attempts"] == 1
    finally:
        service.repository.close()
