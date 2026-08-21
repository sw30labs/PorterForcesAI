from pathlib import Path

from porter_forces_ai.domain import (
    ApprovalDecision,
    ApprovalRole,
    DecisionRequest,
    OrganizationArchetype,
)
from porter_forces_ai.ralph import CompletionTarget
from porter_forces_ai.repository import SQLiteRunRepository
from porter_forces_ai.service import (
    AnalysisMode,
    AnalysisService,
    AnalysisSubmission,
    ApplicationRunStatus,
)
from porter_forces_ai.settings import Settings


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
        assert counts["attempts"] == 1
        assert counts["goals"] == 4
        assert counts["artifacts"] == 3
    finally:
        service.repository.close()


def test_publishable_request_pauses_then_four_exact_approvals_publish(tmp_path: Path) -> None:
    service = _service(tmp_path)
    try:
        result = service.analyze(_submission(target=CompletionTarget.PUBLISHABLE))
        assert result.status is ApplicationRunStatus.HUMAN_REQUIRED

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
    finally:
        service.repository.close()

