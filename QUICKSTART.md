# Quickstart — calling the PorterForcesAI CLI

> Command reference with runnable examples and payload files. See `README.md` for install and `WHAT-IS-PF-AI.md` for the deep dive.

Prerequisites: Python 3.12, `uv`, Node.js 22+. For **live** analysis only, also an oMLX OpenAI-compatible server (default `http://127.0.0.1:8000/v1`).

```bash
cp .env.example .env        # edit if your oMLX model id / port differs
uv sync --extra dev
npm --prefix ui ci
```

All commands run under `uv run porter-forces …`. Everything is local — no remote endpoints without explicit opt-in.

---

## 1. Check configuration and the local model

```bash
# Inventory models + confirm the configured model is visible (no network/model calls)
uv run porter-forces doctor

# Also make two small model calls: forced tool-call + JSON-schema response
uv run porter-forces doctor --live-canary
```

`--live-canary` is necessary but not sufficient — the included Qwen live-test profile passes the small probes but currently **fails** the representative `DecisionFrame` contract. See `docs/technical/release-validation.md`.

---

## 2. Offline demo (no model, no network — always works)

Runs the entire pipeline with labeled synthetic evidence.

```bash
uv run porter-forces run examples/global-bank-ai-adoption.demo.json
```

Expected:

```
Run: RUN-20260821T220100Z-6ae2f5e4
Status: achieved_draft
Ralph attempts: 1
Draft valid: True
Publishable: False
board_memo: runs/RUN-…/board-brief.md
audit_sidecar: runs/RUN-…/audit-sidecar.json
evidence_register: runs/RUN-…/evidence.csv
```

Output files are written under `runs/<run-id>/`. Because `mode: "demo"` is declared in the file, `run` stays offline regardless of `.env`.

---

## 3. Live analysis (local oMLX + DuckDuckGo)

```bash
uv run porter-forces analyze examples/global-bank-ai-adoption.live.json
```

`analyze` **always forces live mode** (it rewrites the declared mode). For a run that *respects* the mode declared in the file, use `run` instead. The live example uses `"mode": "live"`, `"target": "draft"`.

---

## 4. Inspect past runs

```bash
uv run porter-forces runs            # list persisted runs
uv run porter-forces runs --json     # machine-readable
uv run porter-forces show <run-id>   # hydrate one run's complete result
uv run porter-forces show <run-id> --json
```

---

## 5. Serve the local API + UI

```bash
uv run porter-forces serve           # loopback FastAPI at http://127.0.0.1:8765/api/docs
npm --prefix ui run dev -- --host 127.0.0.1 --port 3000   # decision-room UI at http://127.0.0.1:3000
```

Only one writable service may run against a database. A second writer fails fast with a lease conflict; the `.writer.lock` sidecar is a harmless coordination file and must not be used as a busy-state signal.

---

## The submission payload

A submission is `AnalysisSubmission` JSON: a `request` (`DecisionRequest`) plus optional economics and Ralph limits. Three files ship in `examples/`:

- `examples/global-bank-ai-adoption.demo.json` — demo mode, demo + target, full scenario economics + cost-of-delay.
- `examples/global-bank-ai-adoption.live.json` — live mode, draft target.
- `examples/global_bank_ai_adoption.json` — another demo variant.

### Minimal `DecisionRequest` fields

| Field | Required | Notes |
|---|---|---|
| `question` | yes | ≥12 chars; the board decision |
| `archetype` | yes | `global_bank` · `quant_trading` · `insurer` |
| `analysis_mode` | no (default `strategic_initiative`) | `industry_attractiveness` · `strategic_initiative` · `competitive_response` |
| `organization_name` | no | |
| `industry_arena` | no | product/customer/geography market boundary |
| `geographies` | no | ≤20 entries |
| `time_horizon_months` | no (default 36) | 1–120 |
| `audience` | no (default `[full_board]`) | `full_board` · `chair` · `ceo` · `cfo` · `cro` · `cio` · `coo` · `business_executive` · `audit_or_risk_committee` |
| `constraints` | no | up to 30 |
| `public_research_context` | yes | ≥12 chars; **explicitly approved, sanitized** context for public search |
| `restricted_terms` | no | local-only terms the outbound-query guard rejects |
| `internal_context` | no | confidential local-only facts — **never** copied into public queries |
| `evidence_cutoff` | no | a date; cannot be in the past for live capture |

Top-level submission fields: `request`, `mode` (`demo` · `live`, default `demo`), `target` (`draft` · `publishable`, default `draft`), `scenario_economics[]`, `cost_of_delay`, `max_attempts`, `max_budget_units`, `stall_limit`.

### Key enforcement you'll hit while authoring payloads

- **Options must include an explicit no-action/current-course baseline** (`DecisionFrame` validator).
- **Material fact claims require cited evidence**; **model memory can never masquerade as a fact** (`Claim` validator).
- A **research-ready frame cannot retain clarification questions**.
- **Live compact requests** (no full `request`) require an explicit sanitized `public_research_context`.
- Distinct `scenario_economics`/`cost_of_delay` entries must share the same `currency` and money units; `benefit_realization_rate` uses `unit: "ratio"`.

---

## Minimal payload

```json
{
  "request": {
    "question": "Should a global bank authorize a controlled generative-AI workflow now, and what happens if it waits eighteen months?",
    "archetype": "global_bank",
    "analysis_mode": "strategic_initiative",
    "industry_arena": "US and EU regulated banking knowledge workflows",
    "geographies": ["United States", "European Union"],
    "time_horizon_months": 36,
    "audience": ["full_board", "cfo", "cro"],
    "constraints": ["No customer-facing autonomy", "Exercise provider exit before scale"],
    "public_research_context": "Public evidence about generative-AI adoption, technology suppliers, and competitive investment in regulated global banking.",
    "restricted_terms": ["Project Cedar"],
    "internal_context": { "program": "Project Cedar", "note": "Local-only; not for public search." }
  },
  "mode": "live",
  "target": "draft"
}
```

## Example payload with economics

`examples/global-bank-ai-adoption.demo.json` (also valid with `"mode": "live"`):

```json
{
  "request": {
    "question": "Should a global bank authorize a controlled generative-AI workflow now, and what happens if it waits eighteen months?",
    "archetype": "global_bank",
    "analysis_mode": "strategic_initiative",
    "organization_name": "Illustrative Global Bank",
    "industry_arena": "US and EU regulated banking knowledge workflows",
    "geographies": ["United States", "European Union"],
    "time_horizon_months": 36,
    "audience": ["full_board", "cfo", "cro", "cio"],
    "constraints": ["No customer-facing autonomy", "Exercise provider exit before scale"],
    "public_research_context": "Public evidence about generative-AI adoption, technology suppliers, customer behavior, substitutes, entrants, and competitive investment in regulated global banking.",
    "restricted_terms": ["Project Cedar"],
    "internal_context": { "program": "Project Cedar", "note": "Synthetic example only." }
  },
  "mode": "demo",
  "target": "draft",
  "scenario_economics": [
    {
      "scenario_name": "Controlled workflow deployment",
      "currency": "USD",
      "horizon_years": 3,
      "discount_rate": 0.10,
      "benefit_start_month": 7,
      "upfront_cost": {"low": 20000000, "base": 35000000, "high": 55000000, "unit": "USD", "basis_ids": ["FINANCE-OWNED-UPFRONT"]},
      "annual_gross_benefit": {"low": 35000000, "base": 90000000, "high": 150000000, "unit": "USD", "basis_ids": ["FINANCE-OWNED-BENEFIT"]},
      "benefit_realization_rate": {"low": 0.35, "base": 0.55, "high": 0.75, "unit": "ratio", "basis_ids": ["FINANCE-OWNED-REALIZATION"]},
      "annual_run_cost": {"low": 8000000, "base": 12000000, "high": 18000000, "unit": "USD", "basis_ids": ["TECHNOLOGY-OWNED-RUN-COST"]},
      "annual_control_cost": {"low": 4000000, "base": 7000000, "high": 12000000, "unit": "USD", "basis_ids": ["RISK-OWNED-CONTROL-COST"]},
      "annual_expected_loss": {"low": 1000000, "base": 3000000, "high": 8000000, "unit": "USD", "basis_ids": ["RISK-OWNED-EXPECTED-LOSS"]}
    }
  ],
  "cost_of_delay": {
    "currency": "USD",
    "period_months": 18,
    "foregone_benefit": {"low": 8000000, "base": 16000000, "high": 30000000, "unit": "USD", "basis_ids": ["FINANCE-OWNED-FOREGONE"]},
    "competitive_erosion": {"low": 2000000, "base": 8000000, "high": 20000000, "unit": "USD", "basis_ids": ["STRATEGY-OWNED-EROSION"]},
    "accumulated_technical_and_control_debt": {"low": 1000000, "base": 5000000, "high": 12000000, "unit": "USD", "basis_ids": ["TECHNOLOGY-OWNED-DEBT"]},
    "lost_learning_advantage": {"low": 3000000, "base": 9000000, "high": 22000000, "unit": "USD", "basis_ids": ["STRATEGY-OWNED-LEARNING"]},
    "savings_from_waiting": {"low": 4000000, "base": 8000000, "high": 15000000, "unit": "USD", "basis_ids": ["FINANCE-OWNED-WAIT-SAVINGS"]}
  }
}
```

> The `basis_ids` link every estimate to evidence/assumption IDs so finance can reproduce and challenge each number. The model explains these results but never performs the arithmetic.
