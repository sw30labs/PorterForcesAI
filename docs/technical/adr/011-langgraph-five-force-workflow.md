# ADR 011: Deterministic LangGraph workflow with five-force fan-out and one repair

- Status: Accepted, retrospective, inferred 2026-10-30
- Date: 2026-10-30 (recorded retrospectively; the decision itself dates from the evidence below)
- Decision owners: not recorded in the repository (retrospective record)

## Context

`workflow.py` states that the graph owns ordering, fan-out/fan-in, state, and
bounded repair, while a runtime owns prompts and source promotion, so the
workflow is testable with a fake runtime and a Deep Agent cannot change the
business process.

## Decision

- An `AdvisorRuntime` protocol defines `frame_decision`, `research_force`,
  `build_evidence_ledger`, `assess_force`, `compose_board_brief`, `challenge`,
  and `revise_board_brief`.
- Graph: `frame_decision -> confirm_scope -> Send x5 research_force ->
  build_evidence -> Send x5 assess_force -> compose -> challenge ->
  incorporate_challenge -> quality_gate -> (repair -> quality_gate) | END`.
- Fan-in checks that each force appears exactly once and sorts results by
  `FORCE_ORDER`; a worker returning the wrong force fails.
- `frame_decision` resets reducer-backed lists with `Overwrite` so a reused
  checkpoint thread does not append to an earlier run.
- `max_quality_repairs` defaults to 1; after that the graph ends and Ralph's
  evaluator decides the outcome.

## Alternatives considered

### Let an agent plan the whole analysis

Rejected (inferred from the module docstring).

### Unbounded repair loop inside the graph

Rejected; retries belong to Ralph with budgets and stall detection (ADR 001).

## Consequences

### Positive consequences

- Demo and live modes share one graph (see ADR 012).
- Deterministic ordering keeps hashes and lineage stable.

### Costs and limitations

- Exactly five forces are hard-wired; other frameworks would need a new graph.

## Verification

- `tests/test_workflow.py` (2 tests) and runtime/service integration tests.

## Evidence

- `src/porter_forces_ai/workflow.py`
- Commits: `cffecb9`, `7a5b4bf`
