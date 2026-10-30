# ADR 012: Offline deterministic demo runtime with labelled synthetic evidence

- Status: Accepted, retrospective, inferred 2026-10-30
- Date: 2026-10-30 (recorded retrospectively; the decision itself dates from the evidence below)
- Decision owners: not recorded in the repository (retrospective record)

## Context

Live runs need oMLX, DuckDuckGo, and public hosts, none of which are
deterministic. The operations guide tells operators to use demo mode first, and
the release validation records demo mode as the release-verified profile.

## Decision

`DeterministicDemoRuntime` implements `AdvisorRuntime` with fixed
`E-demo-*`/`C-demo-*` artifacts. Evidence is `user_provided` /
`user_assertion`, published by "PorterForcesAI demo fixture", and every text
says it is synthetic. It includes a contrary claim so the counterevidence
criterion is exercised. `porter-forces demo` and the UI's "Current public
evidence" toggle select it. Unit tests never call oMLX, DuckDuckGo, DNS, or
public hosts.

## Alternatives considered

### Recorded live fixtures

Not chosen (inferred); recorded public content would look like real research.

## Consequences

### Positive consequences

- The full graph, gates, Ralph loop, renderers, store, API, and UI are testable
  offline.

### Costs and limitations

- Demo output must never be shared as research; this relies on labels and
  operator discipline.
- Demo success says nothing about model qualification.

## Verification

- `tests/test_runtime.py`, `tests/test_cli.py`, and the demo row in
  `docs/technical/release-validation.md`.

## Evidence

- `src/porter_forces_ai/runtime.py` (`DeterministicDemoRuntime`)
- `src/porter_forces_ai/cli.py` (`demo`)
- `docs/technical/operations.md` (Deterministic demo)
- Commit: `7a5b4bf`
