# ADR 015: Transparent evidence-utility scoring with fixed gate thresholds

- Status: Accepted, retrospective, inferred 2026-10-30
- Date: 2026-10-30 (recorded retrospectively; the decision itself dates from the evidence below)
- Decision owners: not recorded in the repository (retrospective record)

## Context

ADR 002 decides that only captured content becomes evidence. This record covers
the scoring and thresholds the quality gate applies to that evidence.

## Decision

- `evidence_strength = authority x quality x freshness x applicability`, with a
  fixed `SOURCE_AUTHORITY` table (regulator/legislation 1.0 down to search
  snippet and model prior 0.1).
- A material fact or inference needs at least one supporting link with
  `entailment_score >= 0.6` and strength x entailment `>= 0.2`
  (`MIN_USABLE_SUPPORT_CONTRIBUTION`).
- Each link's `supporting_quote` must occur in the excerpt after case and
  whitespace normalization, and supporting quotes for material claims need a
  token-overlap alignment of at least 0.2 (`SUPPORT_QUOTE_LOW_ALIGNMENT`).
- Board points and options based only on model priors are errors; single-source
  material facts, missing counterevidence per force, and unvalidated material
  assumptions are warnings.
- Messages say these are utility screens, not truth probabilities.

## Alternatives considered

### Semantic-entailment model as the gate

Not adopted; the documents describe it as a future high-risk control
(isolated extractor) and keep human review for meaning.

## Consequences

### Positive consequences

- Every gate decision is reproducible and explainable.

### Costs and limitations

- Thresholds (0.6, 0.2, 0.2) have no recorded calibration (A-006).
- `entailment_score` is model-supplied on the link, so the 0.6 threshold
  depends on the model's self-report (inferred from `ClaimEvidenceLink`; A-007).

## Verification

- `tests/test_quality.py` (11 tests) and the evidence-security matrix in
  `docs/technical/testing.md`.

## Evidence

- `src/porter_forces_ai/quality.py`
- `src/porter_forces_ai/domain.py` (`ClaimEvidenceLink`)
- Commits: `cffecb9`, `8d48a6a`
