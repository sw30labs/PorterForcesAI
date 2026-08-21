from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Iterator
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest

from porter_forces_ai.domain import (
    ApprovalDecision,
    ApprovalRecord,
    ApprovalRole,
    ForceName,
    ResearchQuery,
    SearchHit,
)
from porter_forces_ai.repository import (
    RepositoryClosedError,
    RepositoryConflictError,
    RepositoryIntegrityError,
    SQLiteRunRepository,
)
from porter_forces_ai.source_capture import (
    CapturedSource,
    DiscoveryLedger,
    ExecutedQueryRecord,
    RegisteredSearchHit,
)


def _discovery() -> tuple[
    DiscoveryLedger,
    ExecutedQueryRecord,
    tuple[RegisteredSearchHit, ...],
]:
    ledger = DiscoveryLedger()
    execution, hits = ledger.record_execution(
        ResearchQuery(
            query_id="Q-repo-1",
            force=ForceName.NEW_ENTRANTS,
            query="digital bank licensing statistics",
            rationale="Measure barriers to entry",
        ),
        [
            SearchHit(
                query_id="Q-repo-1",
                title="Licensing report",
                url="https://regulator.example/report",
                snippet="Discovery only",
                provider="duckduckgo",
                retrieved_at=datetime(2026, 8, 20, 5, tzinfo=UTC),
            ),
            SearchHit(
                query_id="Q-repo-1",
                title="Industry statistics",
                url="https://statistics.example/data",
                snippet="Discovery only",
                provider="duckduckgo",
                retrieved_at=datetime(2026, 8, 20, 5, tzinfo=UTC),
            ),
        ],
        provider="duckduckgo",
        executed_at=datetime(2026, 8, 20, 5, tzinfo=UTC),
    )
    return ledger, execution, hits


def _captured(hit: RegisteredSearchHit) -> CapturedSource:
    source_id = hit.source_id
    content = "A captured public regulatory statement."
    content_hash = hashlib.sha256(content.encode()).hexdigest()
    capture_basis = f"{source_id}:{hit.url}:{content_hash}".encode()
    return CapturedSource(
        capture_id=f"CAP-{hashlib.sha256(capture_basis).hexdigest()[:32]}",
        source_id=source_id,
        execution_id=hit.execution_id,
        query_id=hit.query_id,
        discovered_url=hit.url,
        final_url=hit.url,
        title=hit.title,
        publisher="regulator.example",
        retrieved_at=datetime(2026, 8, 20, 6, tzinfo=UTC),
        media_type="text/html",
        byte_length=len(content.encode()),
        content_sha256=content_hash,
        extracted_text_sha256=content_hash,
        extracted_text=content,
        text_truncated=False,
    )


@pytest.fixture
def repository(tmp_path: Path) -> Iterator[SQLiteRunRepository]:
    with SQLiteRunRepository(tmp_path / "state" / "runs.sqlite3") as value:
        yield value


def test_repository_initializes_wal_foreign_keys_migrations_and_indexes(
    repository: SQLiteRunRepository,
) -> None:
    health = repository.health()
    assert Path(health.database_path).is_absolute()
    assert health.schema_version == 2
    assert health.journal_mode == "wal"
    assert health.foreign_keys_enabled is True
    assert {
        "idx_attempts_run_number",
        "idx_goals_run_status",
        "idx_queries_attempt_executed",
        "idx_hits_execution_rank",
        "idx_captures_source_retrieved",
        "idx_artifacts_run_type_created",
        "idx_approvals_artifact_role_decision",
    }.issubset(health.index_names)


def test_run_attempt_and_goal_lifecycle(repository: SQLiteRunRepository) -> None:
    run_id = repository.create_run(
        {"question": "Should the bank adopt controlled agentic AI?"},
        run_id="RUN-lifecycle",
    )
    attempt_id = repository.start_attempt(run_id, 1, attempt_id="ATT-lifecycle")
    goal_id = repository.save_goal(
        run_id,
        criterion_key="five_forces_complete",
        description="Each Porter force appears exactly once",
        required=True,
        status="pending",
        evidence=[],
    )
    updated_goal_id = repository.save_goal(
        run_id,
        criterion_key="five_forces_complete",
        description="Each Porter force appears exactly once",
        required=True,
        status="achieved",
        score=1.0,
        evidence=["ART-brief"],
    )
    repository.finish_attempt(
        attempt_id,
        status="achieved_draft",
        goal_report={"all_required": True},
    )
    with pytest.raises(RepositoryIntegrityError, match="already complete"):
        repository.finish_attempt(
            attempt_id,
            status="rewritten",
            goal_report={"all_required": False},
        )
    repository.set_run_status(run_id, "human_required")

    goals = repository.list_goals(run_id)
    assert updated_goal_id == goal_id
    assert len(goals) == 1
    assert goals[0].status == "achieved"
    assert goals[0].score == 1.0
    assert goals[0].evidence == ["ART-brief"]
    assert repository.table_counts()["attempts"] == 1


def test_get_and_list_runs_return_typed_filtered_records(
    repository: SQLiteRunRepository,
) -> None:
    first_time = datetime(2026, 8, 20, 1, tzinfo=UTC)
    second_time = datetime(2026, 8, 20, 2, tzinfo=UTC)
    repository.create_run(
        {"question": "First"},
        run_id="RUN-first-read",
        status="running",
        created_at=first_time,
    )
    repository.create_run(
        {"question": "Second"},
        run_id="RUN-second-read",
        status="human_required",
        created_at=second_time,
    )

    restored = repository.get_run("RUN-first-read")
    assert restored is not None
    assert restored.request == {"question": "First"}
    assert restored.status == "running"
    assert repository.get_run("RUN-absent") is None
    assert [item.run_id for item in repository.list_runs()] == [
        "RUN-second-read",
        "RUN-first-read",
    ]
    assert [
        item.run_id for item in repository.list_runs(status="human_required")
    ] == ["RUN-second-read"]
    assert len(repository.list_runs(limit=1)) == 1
    with pytest.raises(ValueError, match="limit"):
        repository.list_runs(limit=0)


def test_parent_foreign_keys_and_attempt_run_pairing_are_enforced(
    repository: SQLiteRunRepository,
) -> None:
    with pytest.raises(RepositoryConflictError):
        repository.start_attempt("RUN-absent", 1)

    first_run = repository.create_run({}, run_id="RUN-first")
    second_run = repository.create_run({}, run_id="RUN-second")
    second_attempt = repository.start_attempt(second_run, 1)
    _, execution, hits = _discovery()
    with pytest.raises(RepositoryConflictError):
        repository.record_search_execution(first_run, second_attempt, execution, hits)

    with pytest.raises(RepositoryConflictError):
        repository.put_artifact(
            first_run,
            attempt_id=second_attempt,
            artifact_type="board_brief",
            payload={},
        )


def test_search_execution_hits_and_capture_round_trip_are_immutable(
    repository: SQLiteRunRepository,
) -> None:
    run_id = repository.create_run({}, run_id="RUN-research")
    attempt_id = repository.start_attempt(run_id, 1, attempt_id="ATT-research")
    _, execution, hits = _discovery()
    repository.record_search_execution(run_id, attempt_id, execution, hits)

    restored_hits = repository.list_search_hits(execution.execution_id)
    assert restored_hits == hits
    capture = _captured(hits[0])
    repository.record_capture(capture)
    assert repository.get_capture(capture.capture_id) == capture
    assert repository.table_counts()["queries"] == 1
    assert repository.table_counts()["hits"] == 2
    assert repository.table_counts()["captures"] == 1

    with pytest.raises(
        sqlite3.IntegrityError, match="immutable"
    ), repository.transaction() as connection:
        connection.execute(
            "UPDATE queries SET query_text = 'rewritten' WHERE execution_id = ?",
            (execution.execution_id,),
        )
    with pytest.raises(
        sqlite3.IntegrityError, match="immutable"
    ), repository.transaction() as connection:
        connection.execute(
            "DELETE FROM hits WHERE source_id = ?",
            (hits[0].source_id,),
        )


def test_capture_requires_matching_registered_hit_provenance(
    repository: SQLiteRunRepository,
) -> None:
    run_id = repository.create_run({}, run_id="RUN-capture")
    attempt_id = repository.start_attempt(run_id, 1)
    _, execution, hits = _discovery()
    repository.record_search_execution(run_id, attempt_id, execution, hits)
    capture = _captured(hits[0])

    absent_source_id = "S-not-registered"
    absent_basis = (
        f"{absent_source_id}:{capture.final_url}:{capture.content_sha256}".encode()
    )
    absent_capture = replace(
        capture,
        source_id=absent_source_id,
        capture_id=f"CAP-{hashlib.sha256(absent_basis).hexdigest()[:32]}",
    )
    with pytest.raises(RepositoryIntegrityError, match="registered search hit"):
        repository.record_capture(absent_capture)
    with pytest.raises(RepositoryIntegrityError, match="does not match"):
        repository.record_capture(replace(capture, query_id="Q-rewritten"))
    with pytest.raises(RepositoryIntegrityError, match="extracted-text hash"):
        repository.record_capture(replace(capture, extracted_text="rewritten"))
    with pytest.raises(RepositoryIntegrityError, match="capture_id"):
        repository.record_capture(replace(capture, capture_id="CAP-forged"))
    invalid_content_hash = "z" * 64
    invalid_basis = (
        f"{capture.source_id}:{capture.final_url}:{invalid_content_hash}".encode()
    )
    with pytest.raises(RepositoryIntegrityError, match="SHA-256"):
        repository.record_capture(
            replace(
                capture,
                content_sha256=invalid_content_hash,
                capture_id=f"CAP-{hashlib.sha256(invalid_basis).hexdigest()[:32]}",
            )
        )


def test_search_insert_is_atomic_on_conflict(repository: SQLiteRunRepository) -> None:
    run_id = repository.create_run({}, run_id="RUN-atomic-search")
    attempt_id = repository.start_attempt(run_id, 1)
    _, execution, hits = _discovery()
    duplicate_source_hits = (hits[0], replace(hits[1], source_id=hits[0].source_id))

    with pytest.raises(RepositoryIntegrityError, match="source identifiers"):
        repository.record_search_execution(
            run_id,
            attempt_id,
            execution,
            duplicate_source_hits,
        )
    counts = repository.table_counts()
    assert counts["queries"] == 0
    assert counts["hits"] == 0


def test_search_record_hashes_and_ledger_minted_source_ids_are_verified(
    repository: SQLiteRunRepository,
) -> None:
    run_id = repository.create_run({}, run_id="RUN-hash-verification")
    attempt_id = repository.start_attempt(run_id, 1)
    _, execution, hits = _discovery()

    with pytest.raises(RepositoryIntegrityError, match="executed-query hash"):
        repository.record_search_execution(
            run_id,
            attempt_id,
            replace(execution, query_text="rewritten"),
            hits,
        )
    with pytest.raises(RepositoryIntegrityError, match="search-hit hash"):
        repository.record_search_execution(
            run_id,
            attempt_id,
            execution,
            (replace(hits[0], title="rewritten"),),
        )
    with pytest.raises(RepositoryIntegrityError, match="not minted"):
        forged_hit = replace(
            hits[0],
            source_id="S-forged",
            search_hit_sha256=hits[0].search_hit_sha256,
        )
        repository.record_search_execution(
            run_id,
            attempt_id,
            execution,
            (forged_hit,),
        )


def test_transaction_rolls_back_and_nested_savepoint_isolated(
    repository: SQLiteRunRepository,
) -> None:
    with pytest.raises(RuntimeError, match="abort outer"), repository.transaction():
        repository.create_run({}, run_id="RUN-rolled-back")
        raise RuntimeError("abort outer")
    assert repository.table_counts()["runs"] == 0

    with repository.transaction():
        repository.create_run({}, run_id="RUN-outer")
        with pytest.raises(RuntimeError, match="abort inner"), repository.transaction():
            repository.create_run({}, run_id="RUN-inner")
            raise RuntimeError("abort inner")
    assert repository.table_counts()["runs"] == 1


def test_json_artifacts_are_canonical_hashed_and_content_bound_to_approvals(
    repository: SQLiteRunRepository,
) -> None:
    run_id = repository.create_run({}, run_id="RUN-artifacts")
    first = repository.put_artifact(
        run_id,
        artifact_id="ART-first",
        artifact_type="board_brief",
        payload={"b": 2, "a": 1},
    )
    second = repository.put_artifact(
        run_id,
        artifact_id="ART-second",
        artifact_type="board_brief",
        payload={"a": 1, "b": 2},
    )
    assert first.content_sha256 == second.content_sha256
    assert repository.get_artifact(first.artifact_id) == first
    assert repository.list_artifacts(run_id) == (second, first)
    assert repository.list_artifacts(run_id, artifact_type="board_brief") == (
        second,
        first,
    )
    assert repository.list_artifacts(run_id, artifact_type="other") == ()

    approval = ApprovalRecord(
        role=ApprovalRole.RISK,
        reviewer="Risk Reviewer",
        reviewed_at=datetime(2026, 8, 20, tzinfo=UTC),
        decision=ApprovalDecision.APPROVE,
        brief_sha256=first.content_sha256,
    )
    approval_id = repository.add_approval(
        run_id, first.artifact_id, approval, approval_id="APR-risk"
    )
    assert approval_id == "APR-risk"
    assert repository.list_approvals(first.artifact_id) == (approval,)

    stale_approval = approval.model_copy(update={"brief_sha256": "f" * 64})
    with pytest.raises(RepositoryIntegrityError, match="fingerprint"):
        repository.add_approval(run_id, first.artifact_id, stale_approval)


def test_artifact_and_approval_rows_are_append_only(
    repository: SQLiteRunRepository,
) -> None:
    run_id = repository.create_run({}, run_id="RUN-immutable-artifact")
    artifact = repository.put_artifact(
        run_id,
        artifact_id="ART-immutable",
        artifact_type="board_brief",
        payload={"decision": "controlled pilot"},
    )
    approval = ApprovalRecord(
        role=ApprovalRole.STRATEGY,
        reviewer="Strategy Reviewer",
        reviewed_at=datetime(2026, 8, 20, tzinfo=UTC),
        decision=ApprovalDecision.APPROVE,
        brief_sha256=artifact.content_sha256,
    )
    repository.add_approval(run_id, artifact.artifact_id, approval)

    with pytest.raises(
        sqlite3.IntegrityError, match="immutable"
    ), repository.transaction() as connection:
        connection.execute(
            "UPDATE artifacts SET artifact_type = 'changed' WHERE artifact_id = ?",
            (artifact.artifact_id,),
        )
    with pytest.raises(
        sqlite3.IntegrityError, match="immutable"
    ), repository.transaction() as connection:
        connection.execute(
            "DELETE FROM approvals WHERE artifact_id = ?", (artifact.artifact_id,)
        )


def test_database_reopens_without_reapplying_migrations(tmp_path: Path) -> None:
    path = tmp_path / "persistent.sqlite3"
    first = SQLiteRunRepository(path)
    first.create_run({"question": "Persistent?"}, run_id="RUN-persistent")
    first.close()

    with SQLiteRunRepository(path) as reopened:
        assert reopened.health().schema_version == 2
        assert reopened.table_counts()["runs"] == 1
        reopened.optimize()


@pytest.mark.parametrize(
    "path",
    [
        "file:/tmp/run.db?mode=rwc",
        "https://database.example/run.db",
        "sqlite:///tmp/run.db",
    ],
)
def test_repository_rejects_uri_and_nonlocal_database_endpoints(path: str) -> None:
    with pytest.raises(ValueError, match="local filesystem"):
        SQLiteRunRepository(path)


def test_repository_rejects_calls_after_close(tmp_path: Path) -> None:
    repository = SQLiteRunRepository(tmp_path / "closed.sqlite3")
    repository.close()
    repository.close()
    with pytest.raises(RepositoryClosedError):
        repository.health()
