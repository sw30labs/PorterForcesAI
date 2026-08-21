"""Local SQLite persistence for Ralph runs and immutable research provenance.

The repository is intentionally local-only: it accepts a filesystem path (or
``:memory:`` for tests), enables WAL and foreign keys, applies numbered schema
migrations, and exposes bounded transaction methods. Executed queries, search
hits, captures, artifacts, and approvals are append-only at the database layer.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from pathlib import Path
from threading import RLock
from uuid import uuid4

from pydantic import BaseModel

from porter_forces_ai.adapters.duckduckgo import canonicalize_public_url
from porter_forces_ai.domain import ApprovalRecord
from porter_forces_ai.source_capture import (
    UNTRUSTED_EXTERNAL_CONTENT,
    CapturedSource,
    ExecutedQueryRecord,
    RegisteredSearchHit,
)


class RepositoryError(RuntimeError):
    """Base persistence error."""


class RepositoryClosedError(RepositoryError):
    """A caller used a repository after closing it."""


class RepositoryConflictError(RepositoryError):
    """An insert would violate an immutable identifier or uniqueness rule."""


class RepositoryIntegrityError(RepositoryError):
    """Persisted relationships or content hashes do not reconcile."""


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _utc_iso(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamps must be timezone-aware")
    return value.astimezone(UTC).isoformat(timespec="microseconds")


def _parse_datetime(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def _json_default(value: object) -> object:
    if isinstance(value, datetime):
        return _utc_iso(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"{type(value).__name__} is not JSON serializable")


def _canonical_json(value: object) -> str:
    payload = value.model_dump(mode="json") if isinstance(value, BaseModel) else value
    return json.dumps(
        payload,
        default=_json_default,
        ensure_ascii=False,
        allow_nan=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _json_hash(payload_json: str) -> str:
    return hashlib.sha256(payload_json.encode("utf-8")).hexdigest()


def _is_sha256(value: str) -> bool:
    return len(value) == 64 and all(character in "0123456789abcdef" for character in value)


def _require_text(value: str, label: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError(f"{label} must not be blank")
    return normalized


def _local_database_path(path: str | os.PathLike[str]) -> str:
    raw = os.fspath(path)
    if raw == ":memory:":
        return raw
    if not raw or "\x00" in raw:
        raise ValueError("database path is invalid")
    if raw.casefold().startswith("file:") or "://" in raw:
        raise ValueError("database must use a local filesystem path, not a URI")
    resolved = Path(raw).expanduser().resolve(strict=False)
    if resolved.exists() and resolved.is_dir():
        raise ValueError("database path points to a directory")
    resolved.parent.mkdir(parents=True, exist_ok=True)
    return str(resolved)


@dataclass(frozen=True, slots=True)
class RunRecord:
    run_id: str
    status: str
    request: object
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class StoredArtifact:
    artifact_id: str
    run_id: str
    attempt_id: str | None
    artifact_type: str
    schema_version: int
    content_sha256: str
    payload: object
    created_at: datetime


@dataclass(frozen=True, slots=True)
class GoalRecord:
    goal_id: str
    run_id: str
    criterion_key: str
    description: str
    required: bool
    status: str
    score: float | None
    evidence: object
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class RepositoryHealth:
    database_path: str
    schema_version: int
    journal_mode: str
    foreign_keys_enabled: bool
    index_names: tuple[str, ...]


_MIGRATIONS: tuple[tuple[int, str, str], ...] = (
    (
        1,
        "core_run_store",
        """
        CREATE TABLE IF NOT EXISTS runs (
            run_id TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            request_json TEXT NOT NULL CHECK (json_valid(request_json)),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS attempts (
            attempt_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE RESTRICT,
            attempt_number INTEGER NOT NULL CHECK (attempt_number > 0),
            status TEXT NOT NULL,
            started_at TEXT NOT NULL,
            completed_at TEXT,
            goal_report_json TEXT CHECK (
                goal_report_json IS NULL OR json_valid(goal_report_json)
            ),
            UNIQUE (run_id, attempt_number),
            UNIQUE (run_id, attempt_id)
        );

        CREATE TABLE IF NOT EXISTS goals (
            goal_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE RESTRICT,
            criterion_key TEXT NOT NULL,
            description TEXT NOT NULL,
            required INTEGER NOT NULL CHECK (required IN (0, 1)),
            status TEXT NOT NULL,
            score REAL CHECK (score IS NULL OR (score >= 0 AND score <= 1)),
            evidence_json TEXT NOT NULL CHECK (json_valid(evidence_json)),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE (run_id, criterion_key)
        );

        CREATE TABLE IF NOT EXISTS queries (
            execution_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE RESTRICT,
            attempt_id TEXT NOT NULL,
            query_id TEXT NOT NULL,
            force TEXT,
            query_text TEXT NOT NULL,
            rationale TEXT NOT NULL,
            preferred_source_classes_json TEXT NOT NULL
                CHECK (json_valid(preferred_source_classes_json)),
            recency TEXT,
            provider TEXT NOT NULL,
            executed_at TEXT NOT NULL,
            query_sha256 TEXT NOT NULL CHECK (length(query_sha256) = 64),
            FOREIGN KEY (run_id, attempt_id)
                REFERENCES attempts(run_id, attempt_id) ON DELETE RESTRICT
        );

        CREATE TABLE IF NOT EXISTS hits (
            source_id TEXT PRIMARY KEY,
            execution_id TEXT NOT NULL
                REFERENCES queries(execution_id) ON DELETE RESTRICT,
            query_id TEXT NOT NULL,
            rank INTEGER NOT NULL CHECK (rank > 0),
            title TEXT NOT NULL,
            url TEXT NOT NULL,
            snippet TEXT NOT NULL,
            provider TEXT NOT NULL,
            retrieved_at TEXT NOT NULL,
            search_hit_sha256 TEXT NOT NULL CHECK (length(search_hit_sha256) = 64),
            UNIQUE (execution_id, rank),
            UNIQUE (execution_id, url)
        );

        CREATE TABLE IF NOT EXISTS captures (
            capture_id TEXT PRIMARY KEY,
            source_id TEXT NOT NULL REFERENCES hits(source_id) ON DELETE RESTRICT,
            execution_id TEXT NOT NULL,
            query_id TEXT NOT NULL,
            discovered_url TEXT NOT NULL,
            final_url TEXT NOT NULL,
            title TEXT NOT NULL,
            publisher TEXT NOT NULL,
            retrieved_at TEXT NOT NULL,
            media_type TEXT NOT NULL,
            byte_length INTEGER NOT NULL CHECK (byte_length >= 0),
            content_sha256 TEXT NOT NULL CHECK (length(content_sha256) = 64),
            extracted_text_sha256 TEXT NOT NULL
                CHECK (length(extracted_text_sha256) = 64),
            extracted_text TEXT NOT NULL,
            text_truncated INTEGER NOT NULL CHECK (text_truncated IN (0, 1)),
            trust_classification TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS artifacts (
            artifact_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE RESTRICT,
            attempt_id TEXT,
            artifact_type TEXT NOT NULL,
            schema_version INTEGER NOT NULL CHECK (schema_version > 0),
            content_sha256 TEXT NOT NULL CHECK (length(content_sha256) = 64),
            payload_json TEXT NOT NULL CHECK (json_valid(payload_json)),
            created_at TEXT NOT NULL,
            FOREIGN KEY (run_id, attempt_id)
                REFERENCES attempts(run_id, attempt_id) ON DELETE RESTRICT
        );

        CREATE TABLE IF NOT EXISTS approvals (
            approval_id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL REFERENCES runs(run_id) ON DELETE RESTRICT,
            artifact_id TEXT NOT NULL REFERENCES artifacts(artifact_id) ON DELETE RESTRICT,
            role TEXT NOT NULL,
            reviewer TEXT NOT NULL,
            reviewed_at TEXT NOT NULL,
            decision TEXT NOT NULL,
            brief_sha256 TEXT NOT NULL CHECK (length(brief_sha256) = 64),
            approval_json TEXT NOT NULL CHECK (json_valid(approval_json))
        );
        """,
    ),
    (
        2,
        "audit_indexes_and_immutability",
        """
        CREATE INDEX IF NOT EXISTS idx_runs_status_updated
            ON runs(status, updated_at DESC);
        CREATE INDEX IF NOT EXISTS idx_attempts_run_number
            ON attempts(run_id, attempt_number DESC);
        CREATE INDEX IF NOT EXISTS idx_goals_run_status
            ON goals(run_id, required, status);
        CREATE INDEX IF NOT EXISTS idx_queries_attempt_executed
            ON queries(attempt_id, executed_at);
        CREATE INDEX IF NOT EXISTS idx_hits_execution_rank
            ON hits(execution_id, rank);
        CREATE INDEX IF NOT EXISTS idx_hits_url
            ON hits(url);
        CREATE INDEX IF NOT EXISTS idx_captures_source_retrieved
            ON captures(source_id, retrieved_at DESC);
        CREATE INDEX IF NOT EXISTS idx_artifacts_run_type_created
            ON artifacts(run_id, artifact_type, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_approvals_artifact_role_decision
            ON approvals(artifact_id, role, decision);

        CREATE TRIGGER IF NOT EXISTS immutable_queries_update
        BEFORE UPDATE ON queries BEGIN
            SELECT RAISE(ABORT, 'executed queries are immutable');
        END;
        CREATE TRIGGER IF NOT EXISTS immutable_queries_delete
        BEFORE DELETE ON queries BEGIN
            SELECT RAISE(ABORT, 'executed queries are immutable');
        END;
        CREATE TRIGGER IF NOT EXISTS immutable_hits_update
        BEFORE UPDATE ON hits BEGIN
            SELECT RAISE(ABORT, 'search hits are immutable');
        END;
        CREATE TRIGGER IF NOT EXISTS immutable_hits_delete
        BEFORE DELETE ON hits BEGIN
            SELECT RAISE(ABORT, 'search hits are immutable');
        END;
        CREATE TRIGGER IF NOT EXISTS immutable_captures_update
        BEFORE UPDATE ON captures BEGIN
            SELECT RAISE(ABORT, 'captures are immutable');
        END;
        CREATE TRIGGER IF NOT EXISTS immutable_captures_delete
        BEFORE DELETE ON captures BEGIN
            SELECT RAISE(ABORT, 'captures are immutable');
        END;
        CREATE TRIGGER IF NOT EXISTS immutable_artifacts_update
        BEFORE UPDATE ON artifacts BEGIN
            SELECT RAISE(ABORT, 'artifacts are immutable');
        END;
        CREATE TRIGGER IF NOT EXISTS immutable_artifacts_delete
        BEFORE DELETE ON artifacts BEGIN
            SELECT RAISE(ABORT, 'artifacts are immutable');
        END;
        CREATE TRIGGER IF NOT EXISTS immutable_approvals_update
        BEFORE UPDATE ON approvals BEGIN
            SELECT RAISE(ABORT, 'approvals are immutable');
        END;
        CREATE TRIGGER IF NOT EXISTS immutable_approvals_delete
        BEFORE DELETE ON approvals BEGIN
            SELECT RAISE(ABORT, 'approvals are immutable');
        END;
        """,
    ),
)


class SQLiteRunRepository:
    """Transactional local run store with append-only audit artifacts."""

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self._path = _local_database_path(path)
        self._lock = RLock()
        self._closed = False
        self._connection = sqlite3.connect(
            self._path,
            timeout=5.0,
            isolation_level=None,
            check_same_thread=False,
        )
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys = ON")
        self._connection.execute("PRAGMA busy_timeout = 5000")
        self._connection.execute("PRAGMA synchronous = NORMAL")
        self._connection.execute("PRAGMA journal_mode = WAL")
        self._migrate()

    @property
    def path(self) -> str:
        return self._path

    def _ensure_open(self) -> None:
        if self._closed:
            raise RepositoryClosedError("repository is closed")

    def _migrate(self) -> None:
        self._connection.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version INTEGER PRIMARY KEY,
                name TEXT NOT NULL,
                applied_at TEXT NOT NULL DEFAULT (
                    strftime('%Y-%m-%dT%H:%M:%f+00:00', 'now')
                )
            )
            """
        )
        applied = {
            int(row["version"])
            for row in self._connection.execute(
                "SELECT version FROM schema_migrations"
            ).fetchall()
        }
        latest_supported = max(version for version, _name, _sql in _MIGRATIONS)
        if applied and max(applied) > latest_supported:
            raise RepositoryIntegrityError(
                "database schema is newer than this application supports"
            )
        for version, name, sql in _MIGRATIONS:
            if version in applied:
                continue
            escaped_name = name.replace("'", "''")
            script = (
                "BEGIN IMMEDIATE;\n"
                f"{sql}\n"
                "INSERT INTO schema_migrations(version, name) "
                f"VALUES ({version}, '{escaped_name}');\n"
                f"PRAGMA user_version = {version};\n"
                "COMMIT;"
            )
            try:
                self._connection.executescript(script)
            except sqlite3.Error:
                if self._connection.in_transaction:
                    self._connection.rollback()
                raise
        applied_version = int(
            self._connection.execute(
                "SELECT coalesce(max(version), 0) FROM schema_migrations"
            ).fetchone()[0]
        )
        self._connection.execute(f"PRAGMA user_version = {applied_version}")

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Open an immediate transaction, using savepoints when nested."""

        self._ensure_open()
        with self._lock:
            nested = self._connection.in_transaction
            savepoint = f"sp_{uuid4().hex}"
            if nested:
                self._connection.execute(f"SAVEPOINT {savepoint}")
            else:
                self._connection.execute("BEGIN IMMEDIATE")
            try:
                yield self._connection
            except BaseException:
                if nested:
                    self._connection.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                    self._connection.execute(f"RELEASE SAVEPOINT {savepoint}")
                else:
                    self._connection.rollback()
                raise
            else:
                if nested:
                    self._connection.execute(f"RELEASE SAVEPOINT {savepoint}")
                else:
                    self._connection.commit()

    def create_run(
        self,
        request: object,
        *,
        run_id: str | None = None,
        status: str = "created",
        created_at: datetime | None = None,
    ) -> str:
        run_id = _require_text(run_id or f"RUN-{uuid4().hex}", "run_id")
        status = _require_text(status, "status")
        timestamp = _utc_iso(created_at or _utc_now())
        request_json = _canonical_json(request)
        try:
            with self.transaction() as connection:
                connection.execute(
                    """
                    INSERT INTO runs(run_id, status, request_json, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (run_id, status, request_json, timestamp, timestamp),
                )
        except sqlite3.IntegrityError as exc:
            raise RepositoryConflictError("run_id already exists") from exc
        return run_id

    def set_run_status(
        self,
        run_id: str,
        status: str,
        *,
        updated_at: datetime | None = None,
    ) -> None:
        status = _require_text(status, "status")
        with self.transaction() as connection:
            cursor = connection.execute(
                "UPDATE runs SET status = ?, updated_at = ? WHERE run_id = ?",
                (status, _utc_iso(updated_at or _utc_now()), run_id),
            )
            if cursor.rowcount != 1:
                raise RepositoryIntegrityError("run_id does not exist")

    def get_run(self, run_id: str) -> RunRecord | None:
        self._ensure_open()
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM runs WHERE run_id = ?", (run_id,)
            ).fetchone()
        return self._run_from_row(row) if row is not None else None

    def list_runs(
        self,
        *,
        limit: int = 100,
        status: str | None = None,
    ) -> tuple[RunRecord, ...]:
        if not 1 <= limit <= 1_000:
            raise ValueError("limit must be between 1 and 1000")
        self._ensure_open()
        with self._lock:
            if status is None:
                rows = self._connection.execute(
                    """
                    SELECT * FROM runs
                    ORDER BY created_at DESC, run_id DESC LIMIT ?
                    """,
                    (limit,),
                ).fetchall()
            else:
                rows = self._connection.execute(
                    """
                    SELECT * FROM runs WHERE status = ?
                    ORDER BY created_at DESC, run_id DESC LIMIT ?
                    """,
                    (_require_text(status, "status"), limit),
                ).fetchall()
        return tuple(self._run_from_row(row) for row in rows)

    @staticmethod
    def _run_from_row(row: sqlite3.Row) -> RunRecord:
        return RunRecord(
            run_id=str(row["run_id"]),
            status=str(row["status"]),
            request=json.loads(str(row["request_json"])),
            created_at=_parse_datetime(str(row["created_at"])),
            updated_at=_parse_datetime(str(row["updated_at"])),
        )

    def start_attempt(
        self,
        run_id: str,
        attempt_number: int,
        *,
        attempt_id: str | None = None,
        status: str = "running",
        started_at: datetime | None = None,
    ) -> str:
        if attempt_number < 1:
            raise ValueError("attempt_number must be positive")
        attempt_id = _require_text(
            attempt_id or f"ATT-{uuid4().hex}", "attempt_id"
        )
        try:
            with self.transaction() as connection:
                connection.execute(
                    """
                    INSERT INTO attempts(
                        attempt_id, run_id, attempt_number, status, started_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        attempt_id,
                        run_id,
                        attempt_number,
                        _require_text(status, "status"),
                        _utc_iso(started_at or _utc_now()),
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise RepositoryConflictError(
                "attempt identifier or run attempt number already exists"
            ) from exc
        return attempt_id

    def finish_attempt(
        self,
        attempt_id: str,
        *,
        status: str,
        goal_report: object | None = None,
        completed_at: datetime | None = None,
    ) -> None:
        report_json = _canonical_json(goal_report) if goal_report is not None else None
        with self.transaction() as connection:
            cursor = connection.execute(
                """
                UPDATE attempts
                SET status = ?, completed_at = ?, goal_report_json = ?
                WHERE attempt_id = ? AND completed_at IS NULL
                """,
                (
                    _require_text(status, "status"),
                    _utc_iso(completed_at or _utc_now()),
                    report_json,
                    attempt_id,
                ),
            )
            if cursor.rowcount != 1:
                raise RepositoryIntegrityError(
                    "attempt_id does not exist or the attempt is already complete"
                )

    def save_goal(
        self,
        run_id: str,
        *,
        criterion_key: str,
        description: str,
        required: bool,
        status: str,
        score: float | None = None,
        evidence: object = (),
        updated_at: datetime | None = None,
    ) -> str:
        if score is not None and not 0 <= score <= 1:
            raise ValueError("goal score must be between zero and one")
        criterion_key = _require_text(criterion_key, "criterion_key")
        description = _require_text(description, "description")
        status = _require_text(status, "status")
        timestamp = _utc_iso(updated_at or _utc_now())
        evidence_json = _canonical_json(evidence)
        with self.transaction() as connection:
            existing = connection.execute(
                "SELECT goal_id, created_at FROM goals WHERE run_id = ? AND criterion_key = ?",
                (run_id, criterion_key),
            ).fetchone()
            if existing is None:
                goal_id = f"GOAL-{uuid4().hex}"
                try:
                    connection.execute(
                        """
                        INSERT INTO goals(
                            goal_id, run_id, criterion_key, description, required,
                            status, score, evidence_json, created_at, updated_at
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            goal_id,
                            run_id,
                            criterion_key,
                            description,
                            int(required),
                            status,
                            score,
                            evidence_json,
                            timestamp,
                            timestamp,
                        ),
                    )
                except sqlite3.IntegrityError as exc:
                    raise RepositoryIntegrityError("run_id does not exist") from exc
            else:
                goal_id = str(existing["goal_id"])
                connection.execute(
                    """
                    UPDATE goals SET description = ?, required = ?, status = ?,
                        score = ?, evidence_json = ?, updated_at = ?
                    WHERE goal_id = ?
                    """,
                    (
                        description,
                        int(required),
                        status,
                        score,
                        evidence_json,
                        timestamp,
                        goal_id,
                    ),
                )
        return goal_id

    def list_goals(self, run_id: str) -> tuple[GoalRecord, ...]:
        self._ensure_open()
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT * FROM goals WHERE run_id = ?
                ORDER BY required DESC, criterion_key
                """,
                (run_id,),
            ).fetchall()
        return tuple(
            GoalRecord(
                goal_id=str(row["goal_id"]),
                run_id=str(row["run_id"]),
                criterion_key=str(row["criterion_key"]),
                description=str(row["description"]),
                required=bool(row["required"]),
                status=str(row["status"]),
                score=float(row["score"]) if row["score"] is not None else None,
                evidence=json.loads(str(row["evidence_json"])),
                created_at=_parse_datetime(str(row["created_at"])),
                updated_at=_parse_datetime(str(row["updated_at"])),
            )
            for row in rows
        )

    def record_search_execution(
        self,
        run_id: str,
        attempt_id: str,
        execution: ExecutedQueryRecord,
        hits: Sequence[RegisteredSearchHit],
    ) -> None:
        if any(hit.execution_id != execution.execution_id for hit in hits):
            raise RepositoryIntegrityError("a hit belongs to a different query execution")
        if any(hit.query_id != execution.query_id for hit in hits):
            raise RepositoryIntegrityError("a hit belongs to a different query identifier")
        if len({hit.rank for hit in hits}) != len(hits):
            raise RepositoryIntegrityError("hit ranks must be unique")
        if len({hit.source_id for hit in hits}) != len(hits):
            raise RepositoryIntegrityError("source identifiers must be unique")
        query_payload = {
            "query_id": execution.query_id,
            "force": execution.force,
            "query": execution.query_text,
            "rationale": execution.rationale,
            "preferred_source_classes": list(execution.preferred_source_classes),
            "recency": execution.recency,
        }
        if _json_hash(_canonical_json(query_payload)) != execution.query_sha256:
            raise RepositoryIntegrityError("executed-query hash does not verify")
        for hit in hits:
            if hit.provider != execution.provider:
                raise RepositoryIntegrityError(
                    "a hit provider differs from its query execution"
                )
            retrieved_at = _parse_datetime(_utc_iso(hit.retrieved_at))
            hit_payload = {
                "execution_id": hit.execution_id,
                "query_id": hit.query_id,
                "rank": hit.rank,
                "title": hit.title,
                "url": hit.url,
                "snippet": hit.snippet,
                "provider": hit.provider,
                "retrieved_at": retrieved_at.isoformat(),
            }
            if _json_hash(_canonical_json(hit_payload)) != hit.search_hit_sha256:
                raise RepositoryIntegrityError("search-hit hash does not verify")
            source_basis = f"{hit.execution_id}:{hit.rank}:{hit.url}".encode()
            expected_source_id = f"S-{hashlib.sha256(source_basis).hexdigest()[:32]}"
            if hit.source_id != expected_source_id:
                raise RepositoryIntegrityError(
                    "source_id was not minted from the registered search hit"
                )
        try:
            with self.transaction() as connection:
                connection.execute(
                    """
                    INSERT INTO queries(
                        execution_id, run_id, attempt_id, query_id, force,
                        query_text, rationale, preferred_source_classes_json,
                        recency, provider, executed_at, query_sha256
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        execution.execution_id,
                        run_id,
                        attempt_id,
                        execution.query_id,
                        execution.force,
                        execution.query_text,
                        execution.rationale,
                        _canonical_json(execution.preferred_source_classes),
                        execution.recency,
                        execution.provider,
                        _utc_iso(execution.executed_at),
                        execution.query_sha256,
                    ),
                )
                connection.executemany(
                    """
                    INSERT INTO hits(
                        source_id, execution_id, query_id, rank, title, url,
                        snippet, provider, retrieved_at, search_hit_sha256
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            hit.source_id,
                            hit.execution_id,
                            hit.query_id,
                            hit.rank,
                            hit.title,
                            hit.url,
                            hit.snippet,
                            hit.provider,
                            _utc_iso(hit.retrieved_at),
                            hit.search_hit_sha256,
                        )
                        for hit in hits
                    ],
                )
        except sqlite3.IntegrityError as exc:
            raise RepositoryConflictError(
                "search execution conflicts with persisted provenance"
            ) from exc

    def list_search_hits(self, execution_id: str) -> tuple[RegisteredSearchHit, ...]:
        self._ensure_open()
        with self._lock:
            rows = self._connection.execute(
                "SELECT * FROM hits WHERE execution_id = ? ORDER BY rank",
                (execution_id,),
            ).fetchall()
        return tuple(
            RegisteredSearchHit(
                source_id=str(row["source_id"]),
                execution_id=str(row["execution_id"]),
                query_id=str(row["query_id"]),
                rank=int(row["rank"]),
                title=str(row["title"]),
                url=str(row["url"]),
                snippet=str(row["snippet"]),
                provider=str(row["provider"]),
                retrieved_at=_parse_datetime(str(row["retrieved_at"])),
                search_hit_sha256=str(row["search_hit_sha256"]),
            )
            for row in rows
        )

    def record_capture(self, capture: CapturedSource) -> None:
        if not _is_sha256(capture.content_sha256):
            raise RepositoryIntegrityError("capture content hash is not a SHA-256 digest")
        extracted_hash = hashlib.sha256(capture.extracted_text.encode("utf-8")).hexdigest()
        if extracted_hash != capture.extracted_text_sha256:
            raise RepositoryIntegrityError("capture extracted-text hash does not verify")
        capture_basis = (
            f"{capture.source_id}:{capture.final_url}:{capture.content_sha256}".encode()
        )
        expected_capture_id = f"CAP-{hashlib.sha256(capture_basis).hexdigest()[:32]}"
        if capture.capture_id != expected_capture_id:
            raise RepositoryIntegrityError("capture_id does not match captured provenance")
        if capture.trust_classification != UNTRUSTED_EXTERNAL_CONTENT:
            raise RepositoryIntegrityError("captured public content must remain untrusted")
        try:
            discovered_url = canonicalize_public_url(capture.discovered_url)
            final_url = canonicalize_public_url(capture.final_url)
        except ValueError as exc:
            raise RepositoryIntegrityError("capture contains an unsafe URL") from exc
        if discovered_url != capture.discovered_url:
            raise RepositoryIntegrityError("capture discovered URL is not canonical")
        if final_url != capture.final_url:
            raise RepositoryIntegrityError("capture final URL is not canonical")
        try:
            with self.transaction() as connection:
                hit = connection.execute(
                    """
                    SELECT execution_id, query_id, url FROM hits WHERE source_id = ?
                    """,
                    (capture.source_id,),
                ).fetchone()
                if hit is None:
                    raise RepositoryIntegrityError(
                        "capture source_id is not a registered search hit"
                    )
                if (
                    str(hit["execution_id"]) != capture.execution_id
                    or str(hit["query_id"]) != capture.query_id
                    or str(hit["url"]) != capture.discovered_url
                ):
                    raise RepositoryIntegrityError(
                        "capture provenance does not match its registered search hit"
                    )
                connection.execute(
                    """
                    INSERT INTO captures(
                        capture_id, source_id, execution_id, query_id,
                        discovered_url, final_url, title, publisher, retrieved_at,
                        media_type, byte_length, content_sha256,
                        extracted_text_sha256, extracted_text, text_truncated,
                        trust_classification
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        capture.capture_id,
                        capture.source_id,
                        capture.execution_id,
                        capture.query_id,
                        capture.discovered_url,
                        capture.final_url,
                        capture.title,
                        capture.publisher,
                        _utc_iso(capture.retrieved_at),
                        capture.media_type,
                        capture.byte_length,
                        capture.content_sha256,
                        capture.extracted_text_sha256,
                        capture.extracted_text,
                        int(capture.text_truncated),
                        capture.trust_classification,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise RepositoryConflictError("capture_id already exists") from exc

    def get_capture(self, capture_id: str) -> CapturedSource | None:
        self._ensure_open()
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM captures WHERE capture_id = ?", (capture_id,)
            ).fetchone()
        if row is None:
            return None
        return CapturedSource(
            capture_id=str(row["capture_id"]),
            source_id=str(row["source_id"]),
            execution_id=str(row["execution_id"]),
            query_id=str(row["query_id"]),
            discovered_url=str(row["discovered_url"]),
            final_url=str(row["final_url"]),
            title=str(row["title"]),
            publisher=str(row["publisher"]),
            retrieved_at=_parse_datetime(str(row["retrieved_at"])),
            media_type=str(row["media_type"]),
            byte_length=int(row["byte_length"]),
            content_sha256=str(row["content_sha256"]),
            extracted_text_sha256=str(row["extracted_text_sha256"]),
            extracted_text=str(row["extracted_text"]),
            text_truncated=bool(row["text_truncated"]),
            trust_classification=str(row["trust_classification"]),
        )

    def put_artifact(
        self,
        run_id: str,
        *,
        artifact_type: str,
        payload: object,
        attempt_id: str | None = None,
        schema_version: int = 1,
        artifact_id: str | None = None,
        created_at: datetime | None = None,
    ) -> StoredArtifact:
        if schema_version < 1:
            raise ValueError("schema_version must be positive")
        artifact_id = _require_text(
            artifact_id or f"ART-{uuid4().hex}", "artifact_id"
        )
        artifact_type = _require_text(artifact_type, "artifact_type")
        payload_json = _canonical_json(payload)
        content_hash = _json_hash(payload_json)
        timestamp = created_at or _utc_now()
        try:
            with self.transaction() as connection:
                connection.execute(
                    """
                    INSERT INTO artifacts(
                        artifact_id, run_id, attempt_id, artifact_type,
                        schema_version, content_sha256, payload_json, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        artifact_id,
                        run_id,
                        attempt_id,
                        artifact_type,
                        schema_version,
                        content_hash,
                        payload_json,
                        _utc_iso(timestamp),
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise RepositoryConflictError(
                "artifact identifier conflicts or parent run/attempt is absent"
            ) from exc
        return StoredArtifact(
            artifact_id=artifact_id,
            run_id=run_id,
            attempt_id=attempt_id,
            artifact_type=artifact_type,
            schema_version=schema_version,
            content_sha256=content_hash,
            payload=json.loads(payload_json),
            created_at=timestamp.astimezone(UTC),
        )

    def get_artifact(self, artifact_id: str) -> StoredArtifact | None:
        self._ensure_open()
        with self._lock:
            row = self._connection.execute(
                "SELECT * FROM artifacts WHERE artifact_id = ?", (artifact_id,)
            ).fetchone()
        if row is None:
            return None
        return self._artifact_from_row(row)

    def list_artifacts(
        self,
        run_id: str,
        *,
        artifact_type: str | None = None,
    ) -> tuple[StoredArtifact, ...]:
        self._ensure_open()
        with self._lock:
            if artifact_type is None:
                rows = self._connection.execute(
                    """
                    SELECT * FROM artifacts WHERE run_id = ?
                    ORDER BY created_at DESC, artifact_id DESC
                    """,
                    (run_id,),
                ).fetchall()
            else:
                rows = self._connection.execute(
                    """
                    SELECT * FROM artifacts
                    WHERE run_id = ? AND artifact_type = ?
                    ORDER BY created_at DESC, artifact_id DESC
                    """,
                    (run_id, _require_text(artifact_type, "artifact_type")),
                ).fetchall()
        return tuple(self._artifact_from_row(row) for row in rows)

    @staticmethod
    def _artifact_from_row(row: sqlite3.Row) -> StoredArtifact:
        payload_json = str(row["payload_json"])
        stored_hash = str(row["content_sha256"])
        if _json_hash(payload_json) != stored_hash:
            raise RepositoryIntegrityError("artifact content hash does not verify")
        return StoredArtifact(
            artifact_id=str(row["artifact_id"]),
            run_id=str(row["run_id"]),
            attempt_id=str(row["attempt_id"]) if row["attempt_id"] else None,
            artifact_type=str(row["artifact_type"]),
            schema_version=int(row["schema_version"]),
            content_sha256=stored_hash,
            payload=json.loads(payload_json),
            created_at=_parse_datetime(str(row["created_at"])),
        )

    def add_approval(
        self,
        run_id: str,
        artifact_id: str,
        approval: ApprovalRecord,
        *,
        approval_id: str | None = None,
    ) -> str:
        approval_id = _require_text(
            approval_id or f"APR-{uuid4().hex}", "approval_id"
        )
        approval_json = _canonical_json(approval)
        try:
            with self.transaction() as connection:
                artifact = connection.execute(
                    """
                    SELECT run_id, content_sha256 FROM artifacts WHERE artifact_id = ?
                    """,
                    (artifact_id,),
                ).fetchone()
                if artifact is None or str(artifact["run_id"]) != run_id:
                    raise RepositoryIntegrityError(
                        "approval artifact is absent from the specified run"
                    )
                if str(artifact["content_sha256"]) != approval.brief_sha256:
                    raise RepositoryIntegrityError(
                        "approval fingerprint does not match the immutable artifact"
                    )
                connection.execute(
                    """
                    INSERT INTO approvals(
                        approval_id, run_id, artifact_id, role, reviewer,
                        reviewed_at, decision, brief_sha256, approval_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        approval_id,
                        run_id,
                        artifact_id,
                        approval.role.value,
                        approval.reviewer,
                        _utc_iso(approval.reviewed_at),
                        approval.decision.value,
                        approval.brief_sha256,
                        approval_json,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise RepositoryConflictError("approval_id already exists") from exc
        return approval_id

    def list_approvals(self, artifact_id: str) -> tuple[ApprovalRecord, ...]:
        self._ensure_open()
        with self._lock:
            rows = self._connection.execute(
                """
                SELECT approval_json FROM approvals
                WHERE artifact_id = ? ORDER BY reviewed_at, approval_id
                """,
                (artifact_id,),
            ).fetchall()
        return tuple(
            ApprovalRecord.model_validate_json(str(row["approval_json"])) for row in rows
        )

    def health(self) -> RepositoryHealth:
        self._ensure_open()
        with self._lock:
            version = int(self._connection.execute("PRAGMA user_version").fetchone()[0])
            journal_mode = str(
                self._connection.execute("PRAGMA journal_mode").fetchone()[0]
            )
            foreign_keys = bool(
                self._connection.execute("PRAGMA foreign_keys").fetchone()[0]
            )
            indexes = tuple(
                str(row["name"])
                for row in self._connection.execute(
                    """
                    SELECT name FROM sqlite_master
                    WHERE type = 'index' AND name LIKE 'idx_%'
                    ORDER BY name
                    """
                ).fetchall()
            )
        return RepositoryHealth(
            database_path=self._path,
            schema_version=version,
            journal_mode=journal_mode,
            foreign_keys_enabled=foreign_keys,
            index_names=indexes,
        )

    def table_counts(self) -> Mapping[str, int]:
        """Return fixed-table counts for diagnostics without accepting SQL names."""

        self._ensure_open()
        tables = (
            "runs",
            "attempts",
            "goals",
            "queries",
            "hits",
            "captures",
            "artifacts",
            "approvals",
        )
        with self._lock:
            return {
                table: int(
                    self._connection.execute(f"SELECT count(*) FROM {table}").fetchone()[
                        0
                    ]
                )
                for table in tables
            }

    def optimize(self) -> None:
        self._ensure_open()
        with self._lock:
            self._connection.execute("PRAGMA optimize")

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._connection.execute("PRAGMA optimize")
            self._connection.close()
            self._closed = True

    def __enter__(self) -> SQLiteRunRepository:
        return self

    def __exit__(self, *_args: object) -> None:
        self.close()
