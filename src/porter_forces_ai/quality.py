"""Deterministic evidence and publication gates."""

from __future__ import annotations

import hashlib
import json
from collections import Counter, defaultdict
from collections.abc import Sequence
from enum import StrEnum

from pydantic import Field, ValidationError

from porter_forces_ai.domain import (
    ApprovalRecord,
    ApprovalRole,
    Assumption,
    BoardBrief,
    Claim,
    ClaimEvidenceLink,
    ClaimKind,
    ContractModel,
    EvidenceItem,
    EvidenceOrigin,
    EvidenceStance,
    SourceClass,
)

REQUIRED_APPROVAL_ROLES: frozenset[ApprovalRole] = frozenset(
    {
        ApprovalRole.STRATEGY,
        ApprovalRole.FINANCE,
        ApprovalRole.TECHNOLOGY,
        ApprovalRole.RISK,
    }
)
MIN_USABLE_SUPPORT_CONTRIBUTION = 0.2


class FindingSeverity(StrEnum):
    ERROR = "error"
    WARNING = "warning"
    INFO = "info"


class QualityFinding(ContractModel):
    code: str = Field(pattern=r"^[A-Z][A-Z0-9_]+$")
    severity: FindingSeverity
    message: str = Field(min_length=3, max_length=2_000)
    artifact_ids: list[str] = Field(default_factory=list)


class QualityReport(ContractModel):
    findings: list[QualityFinding]
    draft_valid: bool
    publishable: bool


SOURCE_AUTHORITY: dict[SourceClass, float] = {
    SourceClass.REGULATOR: 1.00,
    SourceClass.LEGISLATION: 1.00,
    SourceClass.OFFICIAL_STATISTICS: 0.95,
    SourceClass.AUDITED_FILING: 0.95,
    SourceClass.ACADEMIC: 0.85,
    SourceClass.COMPANY_DISCLOSURE: 0.80,
    SourceClass.INTERNAL_DOCUMENT: 0.80,
    SourceClass.INDUSTRY_RESEARCH: 0.70,
    SourceClass.REPUTABLE_MEDIA: 0.65,
    SourceClass.USER_ASSERTION: 0.50,
    SourceClass.VENDOR: 0.45,
    SourceClass.SEARCH_SNIPPET: 0.10,
    SourceClass.MODEL_PRIOR: 0.10,
    SourceClass.CALCULATOR: 1.00,
}


def evidence_strength(item: EvidenceItem) -> float:
    """Return a transparent triage score; never interpret it as truth probability."""

    authority = SOURCE_AUTHORITY[item.source_class]
    score = authority * item.quality_score * item.freshness_score * item.applicability_score
    return round(max(0.0, min(1.0, score)), 4)


def claim_support_score(
    claim: Claim,
    evidence: list[EvidenceItem],
    links: list[ClaimEvidenceLink],
) -> float:
    """Prioritize review using source strength, entailment, contradiction, and diversity."""

    evidence_by_id = {item.evidence_id: item for item in evidence}
    relevant = [link for link in links if link.claim_id == claim.claim_id]
    support: list[float] = []
    contradiction: list[float] = []
    publishers: set[str] = set()
    for link in relevant:
        item = evidence_by_id.get(link.evidence_id)
        if item is None:
            continue
        contribution = evidence_strength(item) * link.entailment_score
        if link.stance == EvidenceStance.SUPPORTS:
            support.append(contribution)
            publishers.add((item.publisher or item.evidence_id).casefold())
        elif link.stance == EvidenceStance.CONTRADICTS:
            contradiction.append(contribution)

    if not support:
        return 0.0
    # Diminishing returns: the best source matters most; corroboration helps.
    support.sort(reverse=True)
    positive = support[0] + sum(support[1:3]) * 0.25
    diversity_multiplier = 1.0 if len(publishers) >= 2 else 0.75
    negative = min(0.6, sum(contradiction) * 0.35)
    return round(max(0.0, min(1.0, positive * diversity_multiplier - negative)), 4)


def brief_fingerprint(brief: BoardBrief) -> str:
    """Bind an approval to the exact canonical board brief that was reviewed."""

    canonical = json.dumps(
        brief.model_dump(mode="json"),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def evaluate_brief(
    brief: BoardBrief,
    evidence: list[EvidenceItem],
    canonical_claims: list[Claim],
    assumptions: list[Assumption],
    links: list[ClaimEvidenceLink],
    approvals: Sequence[ApprovalRecord] = (),
) -> QualityReport:
    """Check draft integrity separately from approval-bound publication authority."""

    findings: list[QualityFinding] = []
    try:
        # validate_assignment does not observe in-place list mutation. Rebuild
        # every gate input so mutated or deserialized artifacts fail closed.
        brief = BoardBrief.model_validate(brief.model_dump(mode="python"))
        evidence = [
            EvidenceItem.model_validate(item.model_dump(mode="python"))
            for item in evidence
        ]
        canonical_claims = [
            Claim.model_validate(item.model_dump(mode="python"))
            for item in canonical_claims
        ]
        assumptions = [
            Assumption.model_validate(item.model_dump(mode="python"))
            for item in assumptions
        ]
        links = [
            ClaimEvidenceLink.model_validate(item.model_dump(mode="python"))
            for item in links
        ]
        approvals = [
            ApprovalRecord.model_validate(item.model_dump(mode="python"))
            for item in approvals
        ]
    except (AttributeError, TypeError, ValidationError):
        return QualityReport(
            findings=[
                QualityFinding(
                    code="MALFORMED_GATE_INPUT",
                    severity=FindingSeverity.ERROR,
                    message=(
                        "A quality-gate input no longer satisfies its canonical contract."
                    ),
                )
            ],
            draft_valid=False,
            publishable=False,
        )

    evidence_by_id = {item.evidence_id: item for item in evidence}
    assumption_by_id = {item.assumption_id: item for item in assumptions}
    assumption_ids = set(assumption_by_id)
    claim_by_id = {claim.claim_id: claim for claim in brief.material_claims}
    canonical_claim_by_id = {claim.claim_id: claim for claim in canonical_claims}

    for claim in brief.material_claims:
        canonical = canonical_claim_by_id.get(claim.claim_id)
        if canonical is None:
            findings.append(
                QualityFinding(
                    code="CLAIM_ABSENT_FROM_LEDGER",
                    severity=FindingSeverity.ERROR,
                    message="A board claim is absent from the canonical evidence ledger.",
                    artifact_ids=[claim.claim_id],
                )
            )
        elif claim.model_dump(mode="json") != canonical.model_dump(mode="json"):
            findings.append(
                QualityFinding(
                    code="CLAIM_LEDGER_MISMATCH",
                    severity=FindingSeverity.ERROR,
                    message=(
                        "A board claim differs from the canonical claim with the same id."
                    ),
                    artifact_ids=[claim.claim_id],
                )
            )

    board_points = [
        brief.recommendation,
        *brief.why_now,
        *brief.no_action_case,
        *brief.largest_uncertainties,
        brief.smallest_sensible_commitment,
        brief.dissenting_view,
    ]
    for option in brief.options:
        missing_option_claims = sorted(set(option.claim_ids) - claim_by_id.keys())
        missing_option_assumptions = sorted(set(option.assumption_ids) - assumption_ids)
        if missing_option_claims or missing_option_assumptions:
            findings.append(
                QualityFinding(
                    code="OPTION_BASIS_MISSING",
                    severity=FindingSeverity.ERROR,
                    message="A strategic option has unresolved claim or assumption references.",
                    artifact_ids=[
                        option.name,
                        *missing_option_claims,
                        *missing_option_assumptions,
                    ],
                )
            )
        option_claims = [
            claim_by_id[item_id] for item_id in option.claim_ids if item_id in claim_by_id
        ]
        if (
            option_claims
            and all(claim.kind == ClaimKind.MODEL_PRIOR for claim in option_claims)
            and not option.assumption_ids
        ):
            findings.append(
                QualityFinding(
                    code="MODEL_PRIOR_OPTION_BASIS",
                    severity=FindingSeverity.ERROR,
                    message="A strategic option cannot be based only on model priors.",
                    artifact_ids=[option.name],
                )
            )
    for point in board_points:
        missing_point_claims = sorted(set(point.claim_ids) - claim_by_id.keys())
        missing_point_assumptions = sorted(set(point.assumption_ids) - assumption_ids)
        if missing_point_claims or missing_point_assumptions:
            findings.append(
                QualityFinding(
                    code="BOARD_POINT_BASIS_MISSING",
                    severity=FindingSeverity.ERROR,
                    message="A board-visible point has unresolved claim or assumption references.",
                    artifact_ids=[
                        *missing_point_claims,
                        *missing_point_assumptions,
                    ],
                )
            )
        point_claims = [
            claim_by_id[item_id] for item_id in point.claim_ids if item_id in claim_by_id
        ]
        if (
            point_claims
            and all(claim.kind == ClaimKind.MODEL_PRIOR for claim in point_claims)
            and not point.assumption_ids
        ):
            findings.append(
                QualityFinding(
                    code="MODEL_PRIOR_BOARD_BASIS",
                    severity=FindingSeverity.ERROR,
                    message="A board-visible point cannot be based only on model priors.",
                    artifact_ids=point.claim_ids,
                )
            )

    for label, identifiers in (
        ("evidence", [item.evidence_id for item in evidence]),
        ("claim", [item.claim_id for item in brief.material_claims]),
        ("ledger_claim", [item.claim_id for item in canonical_claims]),
        ("assumption", [item.assumption_id for item in assumptions]),
    ):
        duplicates = sorted(key for key, count in Counter(identifiers).items() if count > 1)
        if duplicates:
            findings.append(
                QualityFinding(
                    code=f"DUPLICATE_{label.upper()}_ID",
                    severity=FindingSeverity.ERROR,
                    message=f"Duplicate {label} identifiers make provenance ambiguous.",
                    artifact_ids=duplicates,
                )
            )

    links_by_claim: dict[str, list[ClaimEvidenceLink]] = defaultdict(list)
    for link in links:
        links_by_claim[link.claim_id].append(link)
        if link.claim_id not in canonical_claim_by_id:
            findings.append(
                QualityFinding(
                    code="DANGLING_CLAIM_LINK",
                    severity=FindingSeverity.ERROR,
                    message="An evidence link refers to a claim absent from the ledger.",
                    artifact_ids=[link.claim_id, link.evidence_id],
                )
            )
        if link.evidence_id not in evidence_by_id:
            findings.append(
                QualityFinding(
                    code="DANGLING_EVIDENCE_LINK",
                    severity=FindingSeverity.ERROR,
                    message="An evidence link refers to evidence absent from the ledger.",
                    artifact_ids=[link.claim_id, link.evidence_id],
                )
            )

    for claim in brief.material_claims:
        missing_evidence = sorted(set(claim.evidence_ids) - evidence_by_id.keys())
        missing_assumptions = sorted(set(claim.assumption_ids) - assumption_ids)
        if missing_evidence:
            findings.append(
                QualityFinding(
                    code="MISSING_EVIDENCE",
                    severity=FindingSeverity.ERROR,
                    message="A material claim refers to evidence absent from the ledger.",
                    artifact_ids=[claim.claim_id, *missing_evidence],
                )
            )
        if missing_assumptions:
            findings.append(
                QualityFinding(
                    code="MISSING_ASSUMPTION",
                    severity=FindingSeverity.ERROR,
                    message="A material claim refers to assumptions absent from the ledger.",
                    artifact_ids=[claim.claim_id, *missing_assumptions],
                )
            )
        linked_ids = {link.evidence_id for link in links_by_claim[claim.claim_id]}
        if set(claim.evidence_ids) != linked_ids:
            findings.append(
                QualityFinding(
                    code="CLAIM_LINK_MISMATCH",
                    severity=FindingSeverity.ERROR,
                    message="Claim evidence_ids and explicit claim-evidence links disagree.",
                    artifact_ids=[claim.claim_id],
                )
            )

        if (
            claim.material
            and claim.kind in {ClaimKind.FACT, ClaimKind.INFERENCE}
            and claim.evidence_ids
        ):
            has_entailed_support = any(
                link.stance == EvidenceStance.SUPPORTS
                and link.entailment_score >= 0.6
                and link.evidence_id in evidence_by_id
                and link.evidence_id in claim.evidence_ids
                and evidence_strength(evidence_by_id[link.evidence_id])
                * link.entailment_score
                >= MIN_USABLE_SUPPORT_CONTRIBUTION
                for link in links_by_claim[claim.claim_id]
            )
            if not has_entailed_support:
                findings.append(
                    QualityFinding(
                        code="INSUFFICIENT_ENTAILED_SUPPORT",
                        severity=FindingSeverity.ERROR,
                        message=(
                            "A material factual or inferential claim requires at least one "
                            "supporting link with entailment of 0.6 or greater and a usable "
                            "source-strength contribution of 0.2 or greater; this is an "
                            "evidence-utility gate, not a truth probability."
                        ),
                        artifact_ids=[claim.claim_id],
                    )
                )

        if claim.kind == ClaimKind.FACT:
            cited = [
                evidence_by_id[item_id]
                for item_id in claim.evidence_ids
                if item_id in evidence_by_id
            ]
            if cited and all(item.origin == EvidenceOrigin.MODEL_PRIOR for item in cited):
                findings.append(
                    QualityFinding(
                        code="MODEL_PRIOR_AS_FACT",
                        severity=FindingSeverity.ERROR,
                        message="Model memory cannot substantiate a material factual claim.",
                        artifact_ids=[claim.claim_id],
                    )
                )
            if cited:
                publishers = {(item.publisher or item.evidence_id).casefold() for item in cited}
                if len(publishers) < 2:
                    findings.append(
                        QualityFinding(
                            code="SINGLE_SOURCE_MATERIAL_FACT",
                            severity=FindingSeverity.WARNING,
                            message="A material factual claim has no independent corroboration.",
                            artifact_ids=[claim.claim_id],
                        )
                    )

    for assessment in brief.force_assessments:
        referenced = set(assessment.evidence_for_claim_ids) | set(
            assessment.evidence_against_claim_ids
        )
        referenced.update(
            claim_id for driver in assessment.drivers for claim_id in driver.claim_ids
        )
        missing = sorted(referenced - claim_by_id.keys())
        if missing:
            findings.append(
                QualityFinding(
                    code="FORCE_CLAIM_MISSING",
                    severity=FindingSeverity.ERROR,
                    message=f"{assessment.force.value} refers to absent claims.",
                    artifact_ids=missing,
                )
            )
        if not assessment.evidence_against_claim_ids:
            findings.append(
                QualityFinding(
                    code="NO_COUNTEREVIDENCE",
                    severity=FindingSeverity.WARNING,
                    message=f"{assessment.force.value} contains no explicit counterevidence.",
                )
            )

    referenced_assumption_ids = {
        assumption_id
        for claim in brief.material_claims
        for assumption_id in claim.assumption_ids
    }
    referenced_assumption_ids.update(
        assumption_id for point in board_points for assumption_id in point.assumption_ids
    )
    referenced_assumption_ids.update(
        assumption_id for option in brief.options for assumption_id in option.assumption_ids
    )
    for assumption_id in sorted(referenced_assumption_ids):
        assumption = assumption_by_id.get(assumption_id)
        if assumption is None:
            continue
        if assumption.status == "rejected":
            findings.append(
                QualityFinding(
                    code="REJECTED_ASSUMPTION_REFERENCED",
                    severity=FindingSeverity.ERROR,
                    message="The brief relies on an assumption that has been rejected.",
                    artifact_ids=[assumption_id],
                )
            )
        elif assumption.material and assumption.status == "unvalidated":
            findings.append(
                QualityFinding(
                    code="MATERIAL_ASSUMPTION_UNVALIDATED",
                    severity=FindingSeverity.WARNING,
                    message="The brief relies on a material assumption that remains unvalidated.",
                    artifact_ids=[assumption_id],
                )
            )

    errors = [item for item in findings if item.severity == FindingSeverity.ERROR]
    draft_valid = not errors

    fingerprint = brief_fingerprint(brief)
    current_approvals = [item for item in approvals if item.brief_sha256 == fingerprint]
    stale_approvals = [item for item in approvals if item.brief_sha256 != fingerprint]
    approved_roles = {
        item.role for item in current_approvals if item.decision == "approve"
    }
    rejected_roles = {
        item.role for item in current_approvals if item.decision == "reject"
    }
    missing_roles = REQUIRED_APPROVAL_ROLES - approved_roles

    if stale_approvals:
        findings.append(
            QualityFinding(
                code="STALE_APPROVAL",
                severity=FindingSeverity.WARNING,
                message="An approval applies to a different revision of the board brief.",
                artifact_ids=sorted(
                    f"{item.role.value}:{item.reviewer}" for item in stale_approvals
                ),
            )
        )
    if rejected_roles:
        findings.append(
            QualityFinding(
                code="CURRENT_BRIEF_REJECTED",
                severity=FindingSeverity.WARNING,
                message="At least one required reviewer rejected the current board brief.",
                artifact_ids=sorted(role.value for role in rejected_roles),
            )
        )
    if missing_roles:
        findings.append(
            QualityFinding(
                code="REQUIRED_APPROVAL_MISSING",
                severity=FindingSeverity.WARNING,
                message="The current brief lacks one or more required role approvals.",
                artifact_ids=sorted(role.value for role in missing_roles),
            )
        )

    approvals_valid = not rejected_roles and not missing_roles
    return QualityReport(
        findings=findings,
        draft_valid=draft_valid,
        publishable=draft_valid and approvals_valid,
    )
