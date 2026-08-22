# What is PorterForcesAI?

> A plain-language, code-grounded walkthrough. See `README.md` for install and command reference.

## One sentence

PorterForcesAI is a **local, evidence-led decision engine** that turns a strategic question into a board-ready go/no-go recommendation — and its whole point is *refusing to say "yes" blindly*. It applies Porter's Five Forces as a disciplined, audit-logged, fail-closed workflow. It is decision support, not financial or legal advice.

## The thesis

A typical multi-agent system that produces five polished paragraphs and a heatmap is "easy to demonstrate and dangerous to trust": it hides the market boundary, repeats the same mechanism across forces, **invents economic precision**, and gives the board no real decision.

So this is not a report generator — it is a **decision system** with four independent lenses layered over the five forces:

1. **External necessity** — how the five forces change the profit pool and the institution's bargaining position.
2. **Capability fit** — whether *this* institution can actually exploit the opportunity.
3. **Economic attractiveness** — deterministic NPV / ROI / payback / cost-of-delay from finance-owned inputs.
4. **Control acceptability** — operational, regulatory, model, concentration, cyber, reputational exposure.

It earns trust by being willing to output: *proceed / a smaller reversible commitment / wait / stop / give me evidence first.*

## How the pipeline actually runs

```
Question + confidential context + owned economics
        │
   ┌────▼────┐   fail closed if the boundary is too vague
   │ Frame    │  → Decision Contract (requires an explicit no-action option)
   │ Contract │
   └────┬────┘
        │ fan out to exactly FIVE Porter forces
   ┌────▼──────────────────────────────────────────┐
   │ Hypothesis-led Deep Agent research (bounded)   │  one search batch, ≤5 searches per force
   │  → DuckDuckGo discovery → capture → evidence   │  egress gate, DNS-pinned capture
   └────┬──────────────────────────────────────────┘
        │ frozen, immutable evidence snapshot
   ┌────▼─────────┐   LLM never does the arithmetic
   │ Deterministic│
   │ economics    │   NPV / ROI / payback / cost-of-delay
   └────┬─────────┘
        │
   ┌────▼──────────────┐   independent, does not grade its own homework
   │ Ralph supervisor  │   fresh threads · typed gap directives · attempt/
   │ loop              │   budget/stall limits → achieved_draft /
   └────┬──────────────┘   human_required / publishable / blocked
        │
   ┌────▼──────────────┐   verbatim quote · lexical alignment · no-model-memory-as-fact
   │ Deterministic     │
   │ quality gates     │
   └────┬──────────────┘
        │
   ┌────▼──────────────┐   Strategy + Finance + Technology + Risk must
   │ Human approval    │   approve the exact SHA-256 brief; any edit
   │ gate (publishable)│   invalidates prior approval
   └───────────────────┘
```

### The pieces (from the code)

- **Domain contracts** (`domain.py`) — every artifact (`DecisionFrame`, `ResearchBundle`, `EvidenceItem`, `Claim`, `BoardBrief`, …) is a strict Pydantic model. Validators enforce hard rules: every force assessed exactly once, options must include a no-action case, weights sum to 1, claims cannot cite model memory as fact.
- **Bounded research** (`research_agent.py`, `adapters/omlx.py`) — one Deep Agent per Porter force. Model-visible tools are narrowed to *only* `search_public_web` + a typed `ResearchBundle`. Hidden filesystem/shell tools are masked per-model; guessed calls are rejected. `ModelCallLimitMiddleware` (≤8) and `ToolCallLimitMiddleware` (≤5) cap work; excess calls get an explicit limit error so the model can still return its bundle.
- **Egress + capture** (`egress.py`, `source_capture.py`, `adapters/duckduckgo.py`) — `EgressPolicy` blocks outbound searches containing emails, IBANs, SSNs, credentials, or confidential labels, so secret context cannot leak to DuckDuckGo. Search snippets are *discovery metadata, never evidence*. DuckDuckGo is status-aware: emptiness accepted only on HTTP 200 + explicit no-results marker; 403s / challenges / layout changes **fail closed**. The capture service is DNS-pinned (validates the entire address set, rejects mixed public/private — SSRF-safe) and hashes every downloaded page.
- **Deterministic economics** (`economics.py`) — finance-supplied low/base/high ranges feed exact NPV, ROI, discounted payback, and cost-of-delay. The LLM never performs arithmetic; missing values stay missing; a wholly-negative range can't be papered over.
- **Ralph** (`ralph.py`) — the centerpiece. An analysis graph cannot declare its own goal complete. A separate supervisor runs fresh LangGraph threads, evaluates against explicit criteria, and decides status. It issues typed gap directives for retries, then **stops on attempt / budget / stall limits**.
- **Quality gates** (`quality.py`) — verbatim quote locator must appear in the captured excerpt; lexical alignment screen (explicitly *not* entailment or truth); single-source material facts flagged.
- **Approval** (`service.py`) — publication requires all four of Strategy / Finance / Technology / Risk approving the exact `SHA-256` fingerprint of the brief. Any material edit invalidates prior approval.
- **Persistence** (`repository.py`) — SQLite WAL with append-only provenance (queries, hits, captures, artifacts, approvals) and a POSIX advisory writer lease (`.writer.lock`) so only one writable service runs against the DB. Startup **fails closed on interrupted runs** rather than pretending a partial model call resumed.
- **Surfaces** (`web.py` FastAPI, `cli.py` Typer, `ui/` Next.js/Vinext) — all loopback-only by design.

## Targets & mode

- **Orgs:** global banks, quantitative trading / investment firms, insurers.
- **Demo mode** — fully offline, labeled synthetic evidence; exercises the real graph and gates with no model or network.
- **Live mode** — local oMLX (OpenAI-compatible) + DuckDuckGo. Production is meant for a locally-served DeepSeek profile; the checked-in live-test candidate is Qwen. Model choice **never** bypasses capture, evidence, Ralph, or approval gates.

## Where to go next

- `README.md` — install, quick start, and full command reference.
- `docs/PROJECT_BLUEPRINT.md` — the product thesis and design intent.
- `docs/technical/` — architecture, Ralph loop, security model, API, data-flow, and ADRs.
