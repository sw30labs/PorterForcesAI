# Local operations runbook

Status: local workstation profile  
Last reviewed: 2026-08-20

This runbook operates PorterForcesAI as a local analyst toolkit. It deliberately
does not describe internet deployment.

## Prerequisites

- macOS or Linux workstation appropriate for the selected oMLX model;
- Python 3.12;
- `uv`;
- Node.js 22.13 or newer and npm;
- a separately installed oMLX server for live mode;
- approved public internet access for DuckDuckGo and source capture.

Demo mode needs neither oMLX nor public network access.

## Initial setup

```bash
git clone <repository-url> PorterForcesAI
cd PorterForcesAI
uv sync --extra dev
cp .env.example .env
cd ui
npm ci
cd ..
```

Keep `.env` local. The standard test profile is:

```dotenv
PFA_LLM_MODEL=Qwen3.8-27B-4bit
PFA_LLM_BASE_URL=http://127.0.0.1:8000/v1
PFA_LLM_API_KEY=test
PFA_ALLOW_REMOTE_MODEL_ENDPOINT=false
```

Use the real local oMLX key if the server requires one. The value `test` is only
a development placeholder. Production uses an explicitly qualified DeepSeek
model ID from that server's `/v1/models` inventory; never assume a friendly name
or silently fall back to Qwen.

## Start sequence

```mermaid
sequenceDiagram
    participant O as Operator
    participant M as oMLX
    participant A as FastAPI
    participant U as vinext UI
    participant B as Browser

    O->>M: Start approved model server on loopback
    O->>O: Run model inventory and live canary
    O->>A: Start local Python API
    A->>A: Apply SQLite migrations and health checks
    O->>U: Start local UI
    O->>B: Open emitted loopback URL
    B->>A: GET /api/health
```

For a complete local launch, use the repository launcher:

```bash
./setup_and_run.sh
```

For separate development processes:

```bash
uv run porter-forces serve
```

```bash
cd ui
npm run dev
```

Do not bind the standard profile to `0.0.0.0`. Use the exact UI/API ports printed
by the processes; UI development proxies or calls only the local `/api` origin.

## Readiness checks

### Offline readiness

```bash
uv run pytest
uv run ruff check .
uv run mypy src
cd ui && npm run lint && npm test
```

### oMLX readiness

```bash
PFA_LLM_MODEL=Qwen3.8-27B-4bit uv run porter-forces doctor --live-canary
```

The command must show the selected model in inventory and both tool-calling and
structured-output canaries as true. Do not proceed with a live run if either
canary fails. Demo mode remains available.

### Service readiness

```bash
curl --fail --silent http://127.0.0.1:8765/api/health
```

Confirm database `schema_version`, `journal_mode=wal`, and
`foreign_keys_enabled=true`. A basic health request does not prove oMLX answer
quality or public-network availability.

## Running analyses

### Deterministic demo

Use demo mode first. It exercises the real graph, quality gates, Ralph loop,
renderers, repository, API, and UI with visibly labeled synthetic evidence.

```bash
uv run porter-forces demo
```

Or create it from the New Analysis view with mode **Demo**. Demo output must not
be shared as current market research.

### Live analysis

Before submission:

1. bound the market, geography, and horizon;
2. provide an explicit, sanitized `public_research_context`;
3. put confidential facts only in `internal_context`;
4. add distinctive sensitive strings to `restricted_terms`;
5. supply finance-owned range inputs or accept that ROI will be omitted;
6. select a draft or publication Ralph target deliberately.

```bash
uv run porter-forces analyze examples/global_bank_ai_adoption.json
```

Review exact outbound queries, captured sources, evidence gaps, and quality
findings before relying on the brief. Search failure or uncaptured pages must
appear as an explicit gap, never as model-memory evidence.

## Run-state response

```mermaid
flowchart TD
    S[Run status] --> R{Value}
    R -->|created or running| WAIT[Poll and observe attempts]
    R -->|achieved_draft| REVIEW[Review brief, evidence, economics, and dissent]
    R -->|human_required| HUMAN[Supply owned input or exact content-bound approvals]
    R -->|publishable| EXPORT[Export only through approved process]
    R -->|blocked| FIX[Read terminal reason and open criterion gaps]
    R -->|failed| DIAG[Use request ID and sanitized local logs]
```

Do not restart blindly after `blocked`. If the evidence universe changes, create
a new run. If the same snapshot remains valid and the gap is generative, a new
bounded attempt may be appropriate only within configured limits.

## Human review

At `human_required`, reviewers examine the exact board artifact and its hash.
Strategy, Finance, Technology, and Risk each approve or reject. A brief change
invalidates all previous approvals for publication, although old records remain
in the audit trail.

Finance must own economic assumptions. Risk/legal approval is not delegated to
the model or inferred from a high quality score.

## Artifacts and storage

Each completed run emits:

- `board-brief.md`;
- `audit-sidecar.json`;
- `evidence.csv`.

The database stores run/attempt status and immutable provenance records. Runtime
database and artifact directories are local data, not source code; keep them out
of Git and apply the organization's retention and classification policy.

The defaults are `data/porter-forces.db` and `runs/<run-id>/`. Override them
with `PFA_DATABASE_PATH` and `PFA_ARTIFACTS_DIR` before startup when the
workstation policy requires an encrypted or access-controlled location.

### Backup

Stop or quiesce the API, then use SQLite's backup operation rather than copying
only the main file while WAL is active:

```bash
sqlite3 path/to/porter-forces-ai.db ".backup 'path/to/backup.db'"
```

Back up the artifact directory in the same recovery set. Verify artifact hashes
after restore. A backup may contain confidential internal context and captured
public text; protect it accordingly.

### Recovery

1. preserve the failed database, WAL, logs, and artifacts read-only;
2. restore the database and artifacts together;
3. start the API on loopback;
4. verify repository health and table counts;
5. read a sample artifact so its hash is recomputed;
6. run deterministic tests and a demo before resuming live analyses.

Never repair audit history with direct SQL. Add a migration or explicit recovery
record and retain the original evidence.

## Monitoring

For the local profile, monitor:

- API health and startup migration result;
- run status, duration, and terminal reasons;
- Ralph attempt count, budget units, gap fingerprints, and stalls;
- oMLX model ID, latency, schema/tool failures, and context exhaustion;
- DuckDuckGo errors/rate limits and query counts;
- capture success/rejection by reason, response bytes, and truncation;
- quality finding codes and missing approvals;
- database size/WAL growth and artifact disk usage.

Logs should use run, attempt, execution, source, capture, artifact, and request IDs.
They must not log API keys, full prompts, internal context, restricted terms,
source bodies, or entire briefs by default.

## Troubleshooting

### Configured model is absent

- Confirm oMLX is running on the configured loopback URL.
- Confirm the API key.
- Run `porter-forces doctor --json` and copy the exact inventory ID into
  `PFA_LLM_MODEL`.
- Do not substitute another model automatically.

### Tool or JSON-schema canary fails

- Confirm the exact chat template/model combination supports tool calls and
  strict structured output.
- Inspect oMLX server logs without exposing prompt content.
- Reduce model concurrency and retry the explicit canary.
- Keep live analysis disabled until qualified.

### DuckDuckGo is unavailable

- Confirm approved public egress and DNS.
- Respect rate limits; do not switch providers silently.
- Use demo mode or end the live run with a visible research gap.

### Source capture rejects a page

- Read the typed rejection: unregistered ID, unsafe DNS, redirect limit,
  downgrade, media type, size, timeout, or empty text.
- Do not bypass capture or paste a search snippet into evidence.
- Nominate another public source through a newly recorded search execution.

### Ralph blocks

- Inspect the last `GoalReport`, directives, terminal reason, and gap fingerprint.
- Repeated identical fingerprints indicate no measurable progress; changing
  prose alone is not progress.
- Missing human input is handled as `human_required`, not repeated generation.
- Refreshing evidence creates a new run and snapshot.

### Database reports lock/busy

- Ensure only the intended API process writes the local database.
- Allow the configured five-second busy timeout.
- Stop duplicate processes, checkpoint/backup correctly, and restart.
- Do not delete `-wal` or `-shm` files from a running database.

### UI loads but has no data

- Call `/api/health` directly.
- Verify the UI points to the emitted local API origin.
- Check browser network failures, local Host/Origin policy, and API logs.
- Demo data may be a design fixture; distinguish it visually from repository
  runs.

## Shutdown

Stop accepting new runs, let current bounded work reach a checkpoint or terminal
state, stop the UI, stop FastAPI cleanly so SQLite optimizes/closes, and then stop
oMLX. Preserve blocked and human-required state; do not delete incomplete runs.

## Production-readiness gate

The local profile is not automatically production-ready. Before use in a formal
board process, require:

- exact DeepSeek model and prompt/template qualification;
- model-risk, security, privacy, records, and legal review;
- approved source and egress policy;
- role-based reviewer identity rather than free-text local names;
- signed/exported audit manifests if non-repudiation is required;
- backup, retention, recovery, and incident-response exercises;
- concurrency and unified-memory capacity tests;
- outcome monitoring against realized decisions;
- an explicit statement of human accountability and limitations in every
  published artifact.
