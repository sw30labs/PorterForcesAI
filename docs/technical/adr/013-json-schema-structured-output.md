# ADR 013: JSON-schema structured output at temperature 0, failing closed

- Status: Accepted, retrospective, inferred 2026-10-30
- Date: 2026-10-30 (recorded retrospectively; the decision itself dates from the evidence below)
- Decision owners: not recorded in the repository (retrospective record)

## Context

Local models vary in tool-calling and JSON reliability. The release record
shows that the Qwen checkpoint returned invalid JSON for `DecisionFrame`, and
that the system failed closed instead of repairing it.

## Decision

- `create_chat_model` builds `ChatOpenAI` against the oMLX endpoint with
  `temperature=0`, configured timeout/retries, `use_responses_api=False`, and,
  for loopback hosts, httpx clients with `trust_env=False` and no redirects.
- `OmlxAdvisorRuntime._structured` calls
  `with_structured_output(schema, method="json_schema")`; any exception or
  wrong type becomes `RuntimeContractError` and the run fails.
- `porter-forces doctor --live-canary` checks the exact model ID in `/v1/models`
  plus one forced tool call and one strict JSON-schema reply.

## Alternatives considered

### Function-calling transport

Tried during qualification; no response within 300 seconds
(`docs/technical/release-validation.md`).

### Repairing malformed JSON

Rejected: "malformed model text was not repaired into trusted state".

## Consequences

### Positive consequences

- No partially valid artifact enters the store.
- Corporate proxies never see local prompts.

### Costs and limitations

- Some models cannot run the pipeline at all until they qualify.
- The canary is necessary but not sufficient.

## Verification

- `tests/test_runtime.py` and `tests/test_cli.py`; commit `a9bc152`
  ("test: lock strict oMLX response handling").

## Evidence

- `src/porter_forces_ai/adapters/omlx.py`
- `src/porter_forces_ai/runtime.py` (`_structured`)
- `src/porter_forces_ai/cli.py` (`doctor`, `_run_model_canaries`)
- `docs/technical/release-validation.md`
- Commits: `7a5b4bf`, `a9bc152`, `c5bac50`
