# Local release validation

Status: release evidence, not a portability guarantee  
Validation date: 2026-08-21  
Profile: macOS local workstation, loopback oMLX, exact model
`Qwen3.8-27B-4bit`

This record separates application verification from external model/profile
qualification. A green deterministic suite proves the repository's contracts;
it cannot make an installed checkpoint honor those contracts.

## Deterministic release matrix

The release was exercised with the offline demo, strict Pydantic contracts,
LangGraph and Ralph routes, bounded Deep Agents fakes, DuckDuckGo boundary
fixtures, source-capture security fixtures, SQLite/restart/concurrency tests,
FastAPI tests, and the production UI build/test suite.

| Check | Result |
| --- | --- |
| `uv run pytest -q` | 189 passed |
| `uv run ruff check .` | Passed |
| `uv run mypy src` | Strict check passed for 21 source files |
| UI ESLint and TypeScript `--noEmit` | Passed |
| vinext production build and Node tests | Build passed; 8 tests passed |
| Shell syntax and Git whitespace checks | Passed |
| Markdown links/fences and Mermaid CLI v11 | All release documents and diagrams passed |
| Isolated CLI demo | `achieved_draft`; five forces, one Ralph attempt, valid draft, all canonical artifacts |
| Fresh loopback API/UI smoke | Health/WAL/FK, IPv4 UI, API proxy, `202` job, polling, memo/register downloads passed |

The in-app browser had no active browser instance in this environment, so an
interactive screenshot/keyboard pass was not claimed. Production build,
server-rendered HTML tests, loopback HTTP rendering, and the UI projection tests
passed; a human visual/accessibility walkthrough remains part of deployment
acceptance.

These checks make no public network call unless a live command is selected.

## Exact Qwen/oMLX qualification result

The configured oMLX inventory contained the exact requested ID
`Qwen3.8-27B-4bit`. The small readiness command passed forced tool calling and a
small strict JSON-schema response in 7.20 seconds. A direct DuckDuckGo probe also
proved the status-aware adapter behavior: an HTTP 202 was rejected as unavailable
and a subsequent ordinary query returned eight discovery hits.

The representative application contract did **not** qualify on this installed
model/server combination:

| Probe | Bound/result | Contract outcome |
| --- | --- | --- |
| Full live submission, default settings | Failed after 185.55 seconds | Wrapped `DecisionFrame` contract failure; frame-only reproduction below |
| Fresh full live submission, 300-second client timeout | Failed after 165.70 seconds | Same wrapper; frame-only reproduction below |
| Exact frame, JSON-schema transport | Returned after 171.34 seconds | Invalid JSON at the first character |
| Exact frame, forced function transport | No response inside 300 seconds | Hard bound ended the client |
| Forced function plus `/no_think` | No response inside 180 seconds | Hard bound ended the client |
| JSON schema plus top-level oMLX `thinking_budget: 0` | Returned after 165.63 seconds | Same invalid JSON |

The two full runs failed during acquisition attempt 1 before a valid decision
frame existed. Their public failure records intentionally retained only the
sanitized wrapper; attributing those wrappers to invalid JSON is an inference
from the identical frame-only reproduction, not a directly persisted exception
chain. Each run persisted one failure artifact and no decision/research
checkpoint, search, hit, capture, evidence snapshot, Ralph attempt, goal, or
quality result. This is the intended fail-closed behavior: malformed model text
was not repaired into trusted state, model memory was not substituted for
evidence, and no silent model fallback occurred.

## Release interpretation

- The repository and deterministic demo profile are release-verified.
- `Qwen3.8-27B-4bit` remains the checked-in live-test **candidate**, as requested,
  but this exact installed oMLX/checkpoint combination is not qualified for a
  live board analysis until it passes the representative contract and one full
  bounded run.
- The intended DeepSeek production ID is also a candidate until the same gates
  pass; configuration never silently switches to it.
- A small protocol canary is necessary but not sufficient. Model qualification
  must include the actual decision-frame schema, bounded five-force research,
  captured-evidence promotion, synthesis/challenge contracts, Ralph evaluation,
  and artifact review.

Re-run qualification after any model, quantization, tokenizer/chat template,
oMLX, LangChain, or prompt change. Start a fresh run and database for each live
acceptance attempt so prior failure evidence remains immutable.
