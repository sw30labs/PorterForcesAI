# ADR 018: uv/hatchling toolchain, strict typing, and exact pins on fragile integrations

- Status: Accepted, retrospective, inferred 2026-10-30
- Date: 2026-10-30 (recorded retrospectively; the decision itself dates from the evidence below)
- Decision owners: not recorded in the repository (retrospective record)

## Context

The code depends on internal attributes of `ddgs` engines and on Deep Agents'
harness and middleware APIs. Release evidence includes strict mypy and ruff.

## Decision

- `pyproject.toml` uses `hatchling.build`, `requires-python = ">=3.12"`, and a
  console script `porter-forces`; `uv.lock` is committed.
- `ddgs==9.15.0` is pinned exactly; `deepagents>=0.7.13,<1` and
  `langgraph>=1.2.11,<2` are bounded.
- `[tool.mypy] strict = true`; ruff is the linter.
- `scripts/setup_and_run.sh` runs `uv sync --extra dev` and `npm ci`, refuses
  to start if another process holds the writer lock or the API port, and
  supervises both processes.
- `tests/test_docs_references.py` (added by Nightshift, `e5a1219`) fails if
  README or QUICKSTART references a missing `examples/`, `scripts/`, or
  `docs/technical/` path.

## Alternatives considered

### Loose version ranges for ddgs

Rejected (inferred). The status-aware adapter checks engine members and raises
an incompatibility error, which implies upgrades are expected to break it.

## Consequences

### Positive consequences

- Reproducible installs; integration drift fails loudly.

### Costs and limitations

- Exact pins need deliberate upgrades and re-qualification.
- No CI workflow exists in the repository, so these checks run only when
  someone runs them locally (observed: no `.github/` directory).

## Verification

- `uv run pytest`, `uv run ruff check .`, `uv run mypy src`.

## Evidence

- `pyproject.toml`, `uv.lock`, `Makefile`, `scripts/setup_and_run.sh`
- `tests/test_docs_references.py`
- Commits: `cffecb9`, `45057cf`, `6d7e1ad`, `e5a1219`, `5ae5893`, `6b805ba`
