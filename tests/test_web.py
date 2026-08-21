from __future__ import annotations

import json
import time
from pathlib import Path

from fastapi.testclient import TestClient

from porter_forces_ai.domain import (
    ApprovalRole,
    DecisionRequest,
    OrganizationArchetype,
)
from porter_forces_ai.repository import SQLiteRunRepository
from porter_forces_ai.service import AnalysisMode, AnalysisService, AnalysisSubmission
from porter_forces_ai.settings import Settings
from porter_forces_ai.web import create_app


def _client(tmp_path: Path) -> tuple[TestClient, AnalysisService]:
    settings = Settings(
        _env_file=None,
        database_path=tmp_path / "api.db",
        artifacts_dir=tmp_path / "artifacts",
    )
    service = AnalysisService(
        settings,
        repository=SQLiteRunRepository(settings.database_path),
    )
    return TestClient(create_app(settings, service=service)), service


def _request() -> dict[str, object]:
    return {
        "mode": "demo",
        "target": "publishable",
        "request": {
            "question": (
                "Should a global bank authorize one controlled AI workflow now, "
                "and what happens if it waits?"
            ),
            "archetype": "global_bank",
            "organization_name": "Illustrative Bank",
            "public_research_context": (
                "Public evidence about AI adoption in regulated global banking workflows."
            ),
            "internal_context": {"confidential_program": "Project Cedar"},
        },
    }


def _wait_for_status(client: TestClient, run_id: str, expected: str) -> dict[str, object]:
    deadline = time.monotonic() + 5
    last: dict[str, object] = {}
    while time.monotonic() < deadline:
        response = client.get(f"/api/runs/{run_id}")
        assert response.status_code == 200
        last = response.json()
        if last.get("application_status") == expected:
            return last
        time.sleep(0.01)
    raise AssertionError(f"run did not reach {expected}; last payload={last}")


def test_local_api_runs_demo_redacts_context_and_serves_artifacts(tmp_path: Path) -> None:
    client, service = _client(tmp_path)
    try:
        with client:
            assert client.get("/api/health").json()["local_only"] is True
            created = client.post("/api/analyses", json=_request())
            assert created.status_code == 202
            run_id = created.json()["run_id"]

            completed = _wait_for_status(client, run_id, "human_required")
            details = completed["details"]
            assert details["request"]["internal_context"] == {
                "redacted": "[REDACTED LOCAL CONTEXT]"
            }
            serialized = json.dumps(completed).casefold()
            assert "project cedar" not in serialized
            assert "confidential_program" not in serialized
            artifacts = client.get(f"/api/runs/{run_id}/artifacts")
            assert artifacts.status_code == 200
            assert len(artifacts.json()["artifacts"]) == 5
            assert set(artifacts.json()["files"]) == {
                "board_memo",
                "evidence_register",
            }
            memo = client.get(f"/api/runs/{run_id}/artifacts/board_memo")
            assert memo.status_code == 200
            assert "Board decision brief" in memo.text
            assert (
                client.get(f"/api/runs/{run_id}/artifacts/audit_sidecar").status_code
                == 404
            )
    finally:
        service.repository.close()


def test_approval_endpoint_unlocks_exact_content_and_settings_hide_key(tmp_path: Path) -> None:
    client, service = _client(tmp_path)
    try:
        with client:
            run_id = client.post("/api/analyses", json=_request()).json()["run_id"]
            _wait_for_status(client, run_id, "human_required")

            for role in ApprovalRole:
                response = client.post(
                    f"/api/runs/{run_id}/approvals",
                    json={
                        "role": role.value,
                        "reviewer": f"{role.value} accountable reviewer",
                        "decision": "approve",
                    },
                )
                assert response.status_code == 200
            final = client.get(f"/api/runs/{run_id}").json()
            assert final["application_status"] == "publishable"
            assert len(final["details"]["approvals"]) == 4
            assert final["quality_score"] == 1.0
            assert final["details"]["human_approval"]["outcome"] == "pass"
            published_memo = client.get(f"/api/runs/{run_id}/artifacts/board_memo")
            assert published_memo.status_code == 200
            assert "Publication approvals: **complete**" in published_memo.text
            assert "Post-human approval gate: **pass**" in published_memo.text
            assert "Ralph verification: **publishable**" in published_memo.text

            settings = client.get("/api/settings").json()
            assert settings["model"] == "Qwen3.8-27B-4bit"
            assert "api_key" not in settings
            assert "llm_api_key" not in settings
            rejected_ignored_setting = client.put(
                "/api/settings",
                json={"temperature": 0.7},
            )
            assert rejected_ignored_setting.status_code == 422
    finally:
        service.repository.close()


def test_dashboard_compact_form_defaults_to_demo_without_web_policy(tmp_path: Path) -> None:
    client, service = _client(tmp_path)
    try:
        with client:
            response = client.post(
                "/api/analyses",
                json={
                    "question": "Should an insurer test a controlled AI claims workflow now?",
                    "organization_type": "Insurance",
                    "horizon": "24 months",
                    "market_boundary": "US commercial insurance claims",
                    "internal_context": "Confidential claims baseline",
                    "research_policy": {"web": False},
                },
            )
            assert response.status_code == 202
            run_id = response.json()["run_id"]
            _wait_for_status(client, run_id, "achieved_draft")
            assert client.get("/api/dashboard").json()["run_count"] == 1
    finally:
        service.repository.close()


def test_compact_live_form_requires_explicit_sanitized_public_context(tmp_path: Path) -> None:
    client, service = _client(tmp_path)
    try:
        with client:
            response = client.post(
                "/api/analyses",
                json={
                    "question": "Should Project Cedar be accelerated?",
                    "organization_type": "Global bank",
                    "internal_context": "Project Cedar is confidential",
                    "research_policy": {"web": True},
                },
            )
            assert response.status_code == 422
            assert "sanitized" in response.json()["detail"]
            assert service.repository.table_counts()["runs"] == 0
    finally:
        service.repository.close()


def test_completed_result_and_download_manifest_survive_service_restart(
    tmp_path: Path,
) -> None:
    client, first_service = _client(tmp_path)
    with client:
        payload = _request()
        payload["target"] = "draft"
        run_id = client.post("/api/analyses", json=payload).json()["run_id"]
        _wait_for_status(client, run_id, "achieved_draft")
    first_service.repository.close()

    settings = Settings(
        _env_file=None,
        database_path=tmp_path / "api.db",
        artifacts_dir=tmp_path / "artifacts",
    )
    second_service = AnalysisService(
        settings,
        repository=SQLiteRunRepository(settings.database_path),
    )
    second_client = TestClient(create_app(settings, service=second_service))
    try:
        with second_client:
            restored = second_client.get(f"/api/runs/{run_id}")
            assert restored.status_code == 200
            assert restored.json()["application_status"] == "achieved_draft"
            manifest = second_client.get(f"/api/runs/{run_id}/artifacts").json()
            assert manifest["files"]["board_memo"].startswith("/api/runs/")
            assert str(tmp_path) not in str(manifest)
            assert second_client.get(manifest["files"]["board_memo"]).status_code == 200
    finally:
        second_service.repository.close()


def test_public_download_comes_from_immutable_store_not_mutable_file(
    tmp_path: Path,
) -> None:
    client, service = _client(tmp_path)
    try:
        with client:
            payload = _request()
            payload["target"] = "draft"
            run_id = client.post("/api/analyses", json=payload).json()["run_id"]
            _wait_for_status(client, run_id, "achieved_draft")
            result = service.get_result(run_id)
            assert result is not None
            Path(result.artifact_paths["board_memo"]).write_text(
                "TAMPERED BOARD INSTRUCTION",
                encoding="utf-8",
            )

            downloaded = client.get(f"/api/runs/{run_id}/artifacts/board_memo")
            assert downloaded.status_code == 200
            assert "Board decision brief" in downloaded.text
            assert "TAMPERED" not in downloaded.text
    finally:
        service.repository.close()


def test_startup_marks_orphaned_nonterminal_run_interrupted(tmp_path: Path) -> None:
    _, service = _client(tmp_path)
    # create_app already ran its startup recovery, so create an orphan exactly as
    # a prior process would leave it and then construct a replacement app.
    submission = service.repository.get_run("missing")
    assert submission is None
    request = AnalysisSubmission(
        mode=AnalysisMode.DEMO,
        request=DecisionRequest(
            question="Should the board test one bounded workflow before scaling?",
            archetype=OrganizationArchetype.GLOBAL_BANK,
            public_research_context="Synthetic offline context for interrupted-run recovery.",
        ),
    )
    run_id = service.repository.create_run(
        request,
        run_id="RUN-interrupted-test",
        status="verifying",
    )
    service.repository.start_attempt(run_id, 1, attempt_id="ATT-interrupted")
    service.repository.close()

    settings = Settings(
        _env_file=None,
        database_path=tmp_path / "api.db",
        artifacts_dir=tmp_path / "artifacts",
    )
    replacement = AnalysisService(
        settings,
        repository=SQLiteRunRepository(settings.database_path),
    )
    replacement_client = TestClient(create_app(settings, service=replacement))
    try:
        with replacement_client:
            restored = replacement_client.get(f"/api/runs/{run_id}")
            assert restored.status_code == 200
            body = restored.json()
            assert body["application_status"] == "failed"
            assert "InterruptedRunError" in body["error"]
    finally:
        replacement.repository.close()
