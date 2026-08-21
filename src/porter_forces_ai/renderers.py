"""Deterministic renderers for board-facing and audit-facing artifacts."""

from __future__ import annotations

import csv
import json
from collections.abc import Mapping, Sequence
from io import StringIO
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from porter_forces_ai.domain import BoardBrief, DecisionFrame, EvidenceLedger
from porter_forces_ai.economics import CostOfDelayResult, ScenarioEconomicsResult
from porter_forces_ai.quality import QualityReport, brief_fingerprint


def _table_cell(value: object) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ").strip()


def _points(items: Sequence[Any]) -> str:
    return "\n".join(
        f"- {item.text} `[{', '.join(item.claim_ids + item.assumption_ids)}]`"
        for item in items
    )


def render_board_memo(
    *,
    brief: BoardBrief,
    frame: DecisionFrame,
    ledger: EvidenceLedger,
    quality: QualityReport,
    mode: str,
    scenario_economics: Sequence[ScenarioEconomicsResult] = (),
    cost_of_delay: CostOfDelayResult | None = None,
    ralph_summary: Mapping[str, Any] | None = None,
) -> str:
    """Render an executive memo with traceability visible but unobtrusive."""

    warning = (
        "> **Demonstration only.** This run uses synthetic, user-provided fixtures and must not "
        "be treated as current market research."
        if mode == "demo"
        else "> **Decision support, not advice.** Verify evidence, economics, legal obligations, "
        "and risk acceptance with accountable owners before acting."
    )
    force_rows = "\n".join(
        "| {force} | {score:.1f}/5 | {trend} | {confidence:.0%} | {horizon} |".format(
            force=_table_cell(item.force.value.replace("_", " ").title()),
            score=item.pressure_score,
            trend=_table_cell(item.trend.value.title()),
            confidence=item.confidence,
            horizon=_table_cell(item.primary_horizon.value.replace("_", " ")),
        )
        for item in brief.force_assessments
    )
    option_rows = "\n".join(
        (
            f"| {_table_cell(item.name)} | {item.external_necessity:.1f} | "
            f"{item.capability_fit:.1f} | {item.economic_attractiveness:.1f} | "
            f"{item.control_acceptability:.1f} | {item.reversibility:.1f} | "
            f"{item.time_to_learning_months} |"
        )
        for item in brief.options
    )
    economics_sections: list[str] = []
    for result in scenario_economics:
        roi = (
            f"{result.undiscounted_roi.low:.2f} / {result.undiscounted_roi.base:.2f} / "
            f"{result.undiscounted_roi.high:.2f}"
            if result.undiscounted_roi
            else "Not defined"
        )
        economics_sections.append(
            "\n".join(
                [
                    f"### {_table_cell(result.scenario_name)}",
                    "",
                    f"- NPV low / base / high: {result.currency} "
                    f"{result.npv.low:,.0f} / {result.npv.base:,.0f} / {result.npv.high:,.0f}",
                    f"- Undiscounted ROI low / base / high: {roi}",
                    "- Base discounted payback: "
                    + (
                        f"month {result.base_discounted_payback_month}"
                        if result.base_discounted_payback_month is not None
                        else "not reached inside the modeled horizon"
                    ),
                    *[f"- Warning: {warning}" for warning in result.warnings],
                ]
            )
        )
    if cost_of_delay is not None:
        economics_sections.append(
            "\n".join(
                [
                    "### Cost of delay",
                    "",
                    f"- Period: {cost_of_delay.period_months} months",
                    f"- Low / base / high: {cost_of_delay.currency} "
                    f"{cost_of_delay.cost_of_delay.low:,.0f} / "
                    f"{cost_of_delay.cost_of_delay.base:,.0f} / "
                    f"{cost_of_delay.cost_of_delay.high:,.0f}",
                    f"- Formula: `{cost_of_delay.formula}`",
                ]
            )
        )
    if not economics_sections:
        economics_sections.append(
            "No financial values were supplied. ROI and cost of delay are deliberately not "
            "invented; add finance-owned ranges to calculate them."
        )

    analogy_sections = "\n\n".join(
        "\n".join(
            [
                f"- **{item.unfamiliar_concept}** is like **{item.familiar_mechanism}**.",
                f"  Decision implication: {item.decision_implication}",
                f"  Where the analogy breaks: {item.where_it_breaks}",
            ]
        )
        for item in brief.analogies
    ) or "- No analogy was used."

    sources = []
    for evidence in ledger.evidence:
        source = evidence.publisher or evidence.title
        if evidence.source_url:
            source = f"[{source}]({evidence.source_url})"
        sources.append(
            f"- `{evidence.evidence_id}` {source} — {evidence.title}; "
            f"retrieved {evidence.retrieved_at.date().isoformat()}"
        )
    ralph_line = ""
    if ralph_summary:
        status = ralph_summary.get("status", "unknown")
        attempts = ralph_summary.get("attempt_count", ralph_summary.get("attempts", "unknown"))
        ralph_line = f"- Ralph verification: **{status}** after **{attempts}** attempt(s)\n"
    recommendation_basis = ", ".join(
        brief.recommendation.claim_ids + brief.recommendation.assumption_ids
    )
    commitment_basis = ", ".join(
        brief.smallest_sensible_commitment.claim_ids
        + brief.smallest_sensible_commitment.assumption_ids
    )
    dissent_basis = ", ".join(
        brief.dissenting_view.claim_ids + brief.dissenting_view.assumption_ids
    )

    return f"""# Board decision brief

{warning}

**Run:** `{brief.run_id}`  
**As of:** {brief.as_of.isoformat()}  
**Artifact fingerprint:** `{brief_fingerprint(brief)}`

## Decision requested

{brief.decision_requested}

## Recommendation

{brief.recommendation.text} `[{recommendation_basis}]`

## Why now

{_points(brief.why_now)}

## What if we do nothing?

{_points(brief.no_action_case)}

## Smallest sensible commitment

{brief.smallest_sensible_commitment.text} `[{commitment_basis}]`

## Five Forces signal

| Force | Pressure | Trend | Confidence | Horizon |
|---|---:|---|---:|---|
{force_rows}

## Strategic options

Scores are decision aids (1 low, 5 high), not financial forecasts.

| Option | Necessity | Capability | Economics | Controls | Reversibility | Learning months |
|---|---:|---:|---:|---:|---:|---:|
{option_rows}

## When will we see ROI?

{chr(10).join(economics_sections)}

## Largest uncertainties

{_points(brief.largest_uncertainties)}

## Board-language analogy

{analogy_sections}

## Dissenting view

{brief.dissenting_view.text} `[{dissent_basis}]`

## Questions for management

{chr(10).join(f"- {item}" for item in brief.board_questions)}

## Verification and governance

- Draft quality gate: **{"passed" if quality.draft_valid else "failed"}**
- Publication approvals: **{"complete" if quality.publishable else "not complete"}**
{ralph_line}- Market boundary: {frame.industry_boundary}
- Baseline: {frame.baseline}

## Evidence index

{chr(10).join(sources) if sources else "- No captured evidence."}
"""


def render_evidence_csv(ledger: EvidenceLedger) -> str:
    """Render a compact evidence register suitable for audit review."""

    output = StringIO()
    writer = csv.DictWriter(
        output,
        fieldnames=[
            "evidence_id",
            "origin",
            "source_class",
            "title",
            "publisher",
            "source_url",
            "published_at",
            "retrieved_at",
            "content_sha256",
            "quality_score",
            "freshness_score",
            "applicability_score",
            "excerpt",
            "notes",
        ],
    )
    writer.writeheader()
    for item in ledger.evidence:
        row = item.model_dump(mode="json")
        writer.writerow({key: row.get(key) for key in writer.fieldnames})
    return output.getvalue()


def write_run_artifacts(
    output_dir: Path,
    *,
    memo: str,
    audit_payload: Mapping[str, Any],
    ledger: EvidenceLedger,
) -> dict[str, str]:
    """Atomically write the canonical memo, audit sidecar, and evidence register."""

    output_dir.mkdir(parents=True, exist_ok=True)
    artifacts: dict[str, tuple[str, str]] = {
        "board_memo": ("board-brief.md", memo),
        "audit_sidecar": (
            "audit-sidecar.json",
            json.dumps(audit_payload, ensure_ascii=False, indent=2, default=_json_default),
        ),
        "evidence_register": ("evidence.csv", render_evidence_csv(ledger)),
    }
    paths: dict[str, str] = {}
    for artifact_name, (filename, content) in artifacts.items():
        target = output_dir / filename
        temporary = target.with_suffix(f"{target.suffix}.tmp")
        temporary.write_text(content, encoding="utf-8")
        temporary.replace(target)
        paths[artifact_name] = str(target)
    return paths


def _json_default(value: object) -> object:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    raise TypeError(f"cannot serialize {type(value).__name__}")
