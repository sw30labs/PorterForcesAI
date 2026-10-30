# ADR 009: Deterministic outbound egress policy and public/internal context split

- Status: Accepted, retrospective, inferred 2026-10-30
- Date: 2026-10-30 (recorded retrospectively; the decision itself dates from the evidence below)
- Decision owners: not recorded in the repository (retrospective record)

## Context

The tool is aimed at regulated financial-services decisions where internal
facts must not reach a public search engine. `docs/technical/security.md`
calls the Zone 2 to Zone 4 crossing the principal confidentiality boundary.

## Decision

- `DecisionRequest` separates `public_research_context` (sanitized, may be used
  for search) from `internal_context` (local only) and lists `restricted_terms`.
- `EgressPolicy.validate` NFKC-normalizes the query, rejects control characters,
  empty and over-length queries (240 characters by default), any restricted
  term of 4+ characters, and regex classes: email, IBAN, SSN, long
  account/card numbers, credential-like strings, labelled confidential data,
  and JWTs. Errors never echo the matched value.
- The workflow's `confirm_scope` node runs the policy over the whole
  `public_research_context` before any research starts.
- Research workers receive a `PublicResearchAssignment` without the restricted
  terms; the policy carrying them travels separately so the model never sees
  the strings it must not emit.
- Every search provider validates the query again before calling out.

## Alternatives considered

### Enterprise DLP integration

Not adopted for the local profile; the security document lists DLP as a
residual action rather than a current control.

### Trust the prompt to keep internal facts private

Rejected. The design keeps restricted terms out of the model-visible payload.

## Consequences

### Positive consequences

- Obvious leaks (named terms, identifiers, secrets) fail before the network.
- Rejection messages are safe to log.

### Costs and limitations

- It is string policy, not DLP: paraphrases and unlisted facts can still leak,
  as `docs/technical/security.md` states.
- The long-number pattern can reject legitimate numeric queries (inferred, A-003).

## Verification

- `tests/test_egress.py` (3 tests, including non-echo of confidential context).
- Workflow and service tests covering scope failure.

## Evidence

- `src/porter_forces_ai/egress.py`
- `src/porter_forces_ai/workflow.py` (`confirm_scope`, `dispatch_research`)
- `src/porter_forces_ai/domain.py` (`DecisionRequest`, `PublicResearchAssignment`)
- Commit: `cffecb9`
