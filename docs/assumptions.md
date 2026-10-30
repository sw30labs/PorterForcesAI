# Assumptions register

Assumptions made while writing the retrospective ADRs (007-019) on 2026-10-30
from `8ca5edb`. Each one needs confirming or refuting by the project owner.

| ID | Assumption | Why it was needed | Evidence / source | How to verify | Confidence | Status |
| --- | --- | --- | --- | --- | --- | --- |
| A-001 | Strict contracts were chosen so that output from weak local models fails closed instead of being repaired. | ADR 007 rationale is inferred. | `domain.py`, `docs/technical/release-validation.md` | Ask the author; check whether a repair layer was ever considered. | medium | open |
| A-002 | Using annual discounting for NPV and monthly discounting for payback is intentional, not an oversight. | ADR 008 describes both conventions. | `economics.py` (`calculate_scenario`, `_discounted_payback_month`) | Ask a finance owner or the author; add a test or doc note stating the convention. | low | open |
| A-003 | The long-number egress pattern is meant to block account and card numbers, accepting some false positives on numeric queries. | ADR 009 consequences. | `egress.py` (`long_account_or_card_number`) | Run queries containing years or statistics through `EgressPolicy.validate`. | medium | open |
| A-004 | Deep Agents was chosen over a plain tool loop for its middleware and harness profile hooks. | ADR 010 has no recorded alternative. | `research_agent.py`, `cffecb9` | Ask the author. | low | open |
| A-005 | The regulator/academic allowlist is a deliberately short MVP list, expected to grow. | ADR 014. | `service.py` (`_conservative_source_class`) | Ask the author; check the roadmap in `PROJECT_BLUEPRINT.md`. | low | open |
| A-006 | The gate thresholds (entailment 0.6, contribution 0.2, alignment 0.2) are uncalibrated heuristics. | ADR 015; no calibration data in the repo. | `quality.py` | Look for calibration notes outside the repo or run a labelled evaluation set. | medium | open |
| A-007 | `entailment_score` on claim-evidence links is supplied by the model, not computed independently. | ADR 015 consequence depends on it. | `domain.py` (`ClaimEvidenceLink`), `runtime.py` | Trace where links are built in `OmlxAdvisorRuntime` and the demo runtime. | medium | open |
| A-008 | The keyless fallback chain (`9542565`) is meant to stay opt-in, and wiring it into the live path would need ADR 002 to be superseded. | ADR 016; the module is unused in `src/`. | `adapters/fallback_search.py`, `docs/technical/adr/002-evidence-snapshot-boundary.md` | Ask the author whether to wire it behind a setting or remove it. | medium | open |
| A-009 | The D1/Drizzle/Worker/ChatGPT-auth files in `ui/` are leftovers from the vinext starter template and are not planned for use. | ADR 017. | `ui/.openai/hosting.json`, `ui/db/schema.ts`, `ui/app/chatgpt-auth.ts` | Ask the author; check whether any hosted deployment is planned. | medium | open |
| A-010 | Apache-2.0 was chosen to publish the project as open source with a patent grant; no rationale is recorded. | ADR 019. | `LICENSE`, `8ca5edb` | Ask the author. | low | open |
| A-011 | Commit `9542565` was produced by an automated nightly agent run even though it carries Nic's identity. | The commit subject ends with `(night/2026-09-03)`; authorship matters for review of ADR 016. | `git log 9542565` | Check Nightshift run logs for 2026-09-03. | medium | open |
| A-012 | `analysis_max_concurrency = 1` reflects local unified-memory and model-server limits rather than a correctness requirement. | Context for ADRs 010 and 011. | `settings.py`, `web.py` (`ThreadPoolExecutor(max_workers=1)`) | Ask the author; test two concurrent runs against oMLX. | low | open |
| A-013 | Temperature 0 was set for reproducibility of structured output. | ADR 013 rationale is inferred. | `adapters/omlx.py` | Ask the author. | medium | open |

## Review coverage

The documentation and the core modules (domain, quality, economics, egress, evaluation,
workflow, research agent, settings, adapters) were read in full. `runtime.py`, `service.py`,
`repository.py`, `source_capture.py`, `web.py`, `renderers.py`, and the second half of
`ralph.py` were reviewed structurally and searched for specific behaviour. Tests and the UI
were reviewed structurally rather than line by line.
