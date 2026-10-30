# ADR 019: License the project under Apache-2.0

- Status: Accepted, retrospective, inferred 2026-10-30
- Date: 2026-10-30 (recorded retrospectively; the decision itself dates from the evidence below)
- Decision owners: not recorded in the repository (retrospective record)

## Context

The repository was unlicensed until 2026-10-08.

## Decision

Add `LICENSE` with the Apache License 2.0 (`8ca5edb`, "chore: license under
Apache-2.0").

## Alternatives considered

None recorded. Why Apache-2.0 rather than MIT or another license is not stated
(A-010).

## Consequences

### Positive consequences

- Explicit reuse terms, including a patent grant.

### Costs and limitations

- `ui/package.json` does not declare a license field; `pyproject.toml` does
  (`license = {text = "Apache-2.0"}`).

## Verification

- `LICENSE` exists at the repository root.

## Evidence

- `LICENSE`, `pyproject.toml`
- Commit: `8ca5edb`
