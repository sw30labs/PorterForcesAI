# ADR 014: Conservative, policy-owned source classification and scores

- Status: Accepted, retrospective, inferred 2026-10-30
- Date: 2026-10-30 (recorded retrospectively; the decision itself dates from the evidence below)
- Decision owners: not recorded in the repository (retrospective record)

## Context

Evidence strength multiplies source authority by quality, freshness, and
applicability scores. If the model supplied these, it could raise weak sources
over the gate. `docs/technical/security.md` says source classification and
scores are policy-owned.

## Decision

`_conservative_source_class` in `service.py` assigns:

- `regulator` only for HTTPS hosts ending `.gov` or on a short list
  (`bis.org`, `ecb.europa.eu`, `bankofengland.co.uk`, `europa.eu`, `finra.org`);
- `academic` only for HTTPS `.edu`, `arxiv.org`, or `doi.org`;
- otherwise `vendor`, the least authoritative promotable class.

`_owned_source_scores` fixes quality at 0.9 (regulator), 0.8 (academic), or
0.55 (other), freshness at 0.5, and applicability at 0.6, because the HTML
capture boundary does not extract publication date or applicability.
Authority over cleartext HTTP is never inferred.

## Alternatives considered

### Model-assigned source class and scores

Rejected; the model could inflate its own evidence.

### Large curated publisher registry

Not adopted (inferred). The allowlist is deliberately short; whether it is
meant to grow is not recorded (A-005).

## Consequences

### Positive consequences

- An unknown page cannot clear the material-fact gate on its own.

### Costs and limitations

- Reputable media, company filings, and statistics offices are all scored as
  `vendor` today, so many legitimate sources are under-weighted.
- The regulator list is US/EU-centric.

## Verification

- Service capture tests (`tests/test_service.py`) and quality tests.

## Evidence

- `src/porter_forces_ai/service.py` (`_conservative_source_class`, `_owned_source_scores`)
- `src/porter_forces_ai/quality.py` (`SOURCE_AUTHORITY`)
- Commits: `573107d`, `8ffb581`
