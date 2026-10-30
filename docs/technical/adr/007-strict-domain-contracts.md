# ADR 007: Strict Pydantic domain contracts as the model boundary

- Status: Accepted, retrospective, inferred 2026-10-30
- Date: 2026-10-30 (recorded retrospectively; the decision itself dates from the evidence below)
- Decision owners: not recorded in the repository (retrospective record)

## Context

The model proposes decision frames, research bundles, claims, force assessments,
briefs, and challenges. The documentation repeatedly says the model may propose
artifacts but may not change their semantics (`src/porter_forces_ai/domain.py`
module docstring). Free-form model text would make the quality gate, Ralph
evaluator, renderers, and SQLite store depend on prose.

## Decision

Every persisted workflow artifact derives from `ContractModel`
(`extra="forbid"`, `str_strip_whitespace=True`, `validate_assignment=True`).
Contracts carry their own invariants rather than relying on prompts:

- `DecisionFrame` needs at least three unique options, one of which is an
  explicit no-action/current-course baseline, and a research-ready frame may not
  keep clarification questions.
- `Claim` rejects a material fact without evidence IDs, a material derived claim
  without evidence or assumption IDs, and a `model_prior` claim that cites
  evidence.
- `EvidenceItem` ties `source_class` to `origin`, and public-web evidence needs
  a publisher, a URL, and a captured-content SHA-256.
- `ForceAssessment` driver weights must sum to 1 and the pressure score must
  match the weighted driver score within 0.05.
- `BoardBrief` must contain each Porter force exactly once and a no-action option.
- `ApprovalRecord` is frozen.

## Alternatives considered

### Validate model prose with prompts only

Rejected (inferred). Prompts cannot be tested deterministically; the testing
strategy requires that "invalid identities, URLs, options, weights, provenance,
ranges, and approvals fail closed" (`docs/technical/testing.md`).

### Loose dictionaries between nodes

Rejected (inferred). The workflow, evaluator, and repository all re-validate the
same models, which only works with one canonical schema.

## Consequences

### Positive consequences

- Malformed model output fails at the boundary instead of reaching the brief.
- The same contracts drive JSON-schema structured output, the quality gate,
  storage, and rendering.

### Costs and limitations

- A model that cannot satisfy the full schema cannot run at all; the
  2026-08-21 Qwen qualification failed on `DecisionFrame`
  (`docs/technical/release-validation.md`).
- No-action detection is keyword based and English only: the frame accepts
  "no action", "do nothing", or "current course", while brief checks also
  accept "status quo".

## Verification

- `tests/test_domain.py` (11 tests).
- `quality.evaluate_brief` re-validates every input and returns
  `MALFORMED_GATE_INPUT` on contract drift.

## Evidence

- `src/porter_forces_ai/domain.py`
- `src/porter_forces_ai/quality.py` (re-validation before gating)
- Commits: `cffecb9` (foundation), `7a5b4bf` (runtime)
