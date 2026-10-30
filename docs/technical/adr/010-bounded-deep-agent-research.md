# ADR 010: Bounded Deep Agents worker with a two-phase search protocol

- Status: Accepted, retrospective, inferred 2026-10-30
- Date: 2026-10-30 (recorded retrospectively; the decision itself dates from the evidence below)
- Decision owners: not recorded in the repository (retrospective record)

## Context

Each Porter force needs hypothesis-led public discovery. Deep Agents ships
filesystem, shell, and general-purpose subagent tools by default, and a local
model can loop on searches. Several commits on 2026-08-21 tightened this
boundary (`4da5427`, `d2cdedd`, `575f599`).

## Decision

- `_register_restricted_harness` registers a Deep Agents harness profile for the
  exact `openai:<model id>` that excludes `ls`, `read_file`, `write_file`,
  `edit_file`, `delete`, `glob`, `grep`, `execute`, and `task`, and disables the
  general-purpose subagent. Model IDs containing `:` are rejected.
- The only executable tool is `search_public_web`; `subagents=[]`,
  `backend=StateBackend()`, and `response_format=ResearchBundle`.
- Middleware stack: `ModelCallLimitMiddleware(run_limit=8, exit_behavior="error")`;
  `ToolCallLimitMiddleware(search_public_web, run_limit=5, exit_behavior="continue")`
  so excess parallel calls get explicit limit errors but synthesis still runs;
  a middleware that removes the search tool after a successful search so
  `ResearchBundle` becomes mandatory; and a boundary middleware that rejects
  unknown tool names, synthesis before search, and a second search after the batch.
- Search results are returned to the model labelled
  `untrusted_search_discovery_metadata`.

## Alternatives considered

### Unrestricted Deep Agent defaults

Rejected; the security document lists tool escalation as a threat.

### Plain LangChain tool loop without Deep Agents

Not documented. Why Deep Agents was chosen over a simpler loop is not recorded
(A-004).

## Consequences

### Positive consequences

- Research cost and behaviour are bounded per force.
- A guessed hidden tool name fails the run instead of executing.

### Costs and limitations

- Coupled to Deep Agents' harness-profile and middleware APIs, pinned to
  `deepagents>=0.7.13,<1` (`6d7e1ad`); upgrades need contract re-tests.
- One batch can under-research a force; gaps are recorded instead of broadened.

## Verification

- `tests/test_research_agent.py` (8 tests).

## Evidence

- `src/porter_forces_ai/research_agent.py`
- `pyproject.toml` (`deepagents>=0.7.13,<1`)
- Commits: `cffecb9`, `4da5427`, `d2cdedd`, `575f599`, `6d7e1ad`
