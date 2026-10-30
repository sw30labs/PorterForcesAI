# ADR 008: Deterministic Decimal economics owned by finance inputs

- Status: Accepted, retrospective, inferred 2026-10-30
- Date: 2026-10-30 (recorded retrospectively; the decision itself dates from the evidence below)
- Decision owners: not recorded in the repository (retrospective record)

## Context

Board briefs discuss NPV, ROI, payback, and the cost of waiting. The
`economics.py` docstring states that the language model may explain these
results but never performs or silently changes the arithmetic, and the
operations guide says missing finance inputs mean ROI is omitted, not invented.

## Decision

- Inputs are `RangeEstimate` low/base/high values in `Decimal`, ordered, with a
  unit and at least one evidence or assumption `basis_id`, plus an optional
  owner.
- `calculate_scenario` computes conservative/base/upside NPV (annual
  discounting), undiscounted ROI, and base-case discounted payback (monthly
  discounting). Conservative pairs low benefit with high cost and upside pairs
  high benefit with low cost.
- ROI is `None` with a warning when any scenario has zero modeled cost; a base
  realization rate of exactly 1 produces a finance-review warning.
- `calculate_cost_of_delay` sums foregone benefit, competitive erosion,
  accumulated technical/control debt, and lost learning advantage, minus
  savings from waiting.
- The Ralph evaluator adds `G-economics-coherence` only when calculations
  exist, rejects any change to the calculated snapshot, requires scenario names
  to map verbatim to option names, and, when every upside NPV is negative,
  requires a conservative recommendation and action options scored at 2 or below.

## Alternatives considered

### Let the model estimate economics

Rejected (inferred from the module docstring and `docs/technical/ralph-loop.md`:
"the model may not invent ROI").

### Floating-point arithmetic

Rejected. `docs/technical/testing.md` requires Decimal values for financial
arithmetic.

## Consequences

### Positive consequences

- Every number is reproducible by a finance owner and traceable to basis IDs.
- A negative case cannot be framed positively without failing Ralph.

### Costs and limitations

- Two discounting conventions coexist (annual for NPV, monthly for payback);
  this is documented only in code and could surprise a finance reviewer
  (see A-002).
- Keyword checks for "npv", "roi", "cost of delay", and similar terms are a
  lexical proxy for coherence.

## Verification

- `tests/test_economics.py` (4 tests, including zero-cost baseline and
  benefit of waiting).
- `tests/test_evaluation.py` economics cases.

## Evidence

- `src/porter_forces_ai/economics.py`
- `src/porter_forces_ai/evaluation.py` (`ECONOMICS_CRITERION`, `_economics_coherence`)
- Commits: `cffecb9`, `8d48a6a` (expanded definition of done)
