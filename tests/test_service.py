from decimal import Decimal
from pathlib import Path

import pytest

from porter_forces_ai.domain import (
    FORCE_ORDER,
    ApprovalDecision,
    ApprovalRole,
    DecisionRequest,
    ForceName,
    OrganizationArchetype,
)
from porter_forces_ai.economics import RangeEstimate, ScenarioEconomicsInputs
from porter_forces_ai.ralph import CompletionTarget
from porter_forces_ai.repository import SQLiteRunRepository
from porter_forces_ai.service import (
    AnalysisMode,
    AnalysisService,
    AnalysisServiceError,
    AnalysisSubmission,
    ApplicationRunStatus,
    _balanced_source_ids,
    _conservative_source_class,
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
        assert counts["attempts"] == 2  # acquisition plus one Ralph attempt
        assert counts["goals"] >= 7
        assert counts["artifacts"] == 5  # checkpoint plus four immutable outputs
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
        assert result.ralph_state is not None
        assert result.ralph_state.status.value == "publishable"
        human_goal = next(
            item
            for item in service.repository.list_goals(result.run_id)
            if item.criterion_key == "G-human-approval"
        )
        assert human_goal.status == "pass"
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
