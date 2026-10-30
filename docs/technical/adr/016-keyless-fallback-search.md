# ADR 016: Keyless fallback search engines as an unwired, opt-in chain

- Status: Accepted, retrospective, inferred 2026-10-30
- Date: 2026-10-30 (recorded retrospectively; the decision itself dates from the evidence below)
- Decision owners: not recorded in the repository (retrospective record)

## Context

DuckDuckGo rate limits and challenge pages end live runs with a research gap
(ADR 002). On 2026-09-03 a nightly change (`9542565`) added
`adapters/fallback_search.py` and its tests. ADR 002, `docs/technical/security.md`,
and the operations guide all say there is no hidden or silent provider
fallback.

## Decision

- `KeylessEngineSearchProvider` wraps one keyless `ddgs` engine. Defaults are
  Brave then Bing; Mojeek is registered but not default. The code comment
  records that on 2026-08-25, against ddgs 9.15.0, Mojeek served a captcha and
  Yahoo, Startpage, Google, and Wikipedia parsed to zero rows; Yandex was
  excluded for a US/EU regulated-banking profile.
- A fallback engine is trusted only when it returns at least one parsable
  result; empty, non-200, blank, or unparsable responses raise
  `SearchUnavailableError`.
- `FallbackSearchProvider` advances only on unavailability. A verified empty
  result from the primary ends the chain. Handoffs sleep about one second with
  jitter. When every provider fails, the error names each one.
- Hits carry their real `provider` name, and `max_results` caps them.
- As of `8ca5edb`, no module in `src/` or `ui/` imports `fallback_search`; the
  live service still uses only `DuckDuckGoSearchProvider`.

## Alternatives considered

### Silent automatic fallback in the live path

Not adopted as of `8ca5edb`; it would contradict ADR 002 and the security
model.

### Modelling each engine's block markup

Rejected in the module docstring: guessing challenge markup could read a block
as "no results".

## Consequences

### Positive consequences

- Coverage can be extended later without weakening the "no results" contract.
- Results stay attributable to their real engine.

### Costs and limitations

- Unused code that conflicts with the documented "no fallback" stance until a
  decision is made either to wire it in behind an explicit setting (and update
  ADR 002 and the security docs) or to remove it (A-008).
- Depends on internal `ddgs` engine attributes, which the exact
  `ddgs==9.15.0` pin protects.

## Verification

- `tests/test_fallback_search.py` (10 tests).

## Evidence

- `src/porter_forces_ai/adapters/fallback_search.py`
- `tests/test_fallback_search.py`
- `pyproject.toml` (`ddgs==9.15.0`)
- Commit: `9542565` ("feat: wire max_results cap ... (night/2026-09-03)")
