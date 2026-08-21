# Porter Forces AI console

Local board-advisory workspace built with React, TypeScript, vinext, and the
OpenAI Sites Vite plugin. Until a completed Python analysis is loaded, the
console shows a prominent **illustrative workspace** banner over its walkthrough
fixture. A fixture is never presented as live analysis, and submission,
approval, or settings failures never trigger simulated success.

## Run locally

Requires Node.js `>=22.13.0`.

```bash
npm install
npm run dev
```

The vinext development server proxies `/api/*` to
`PFA_UI_API_PROXY` (default `http://127.0.0.1:8765`). Start the FastAPI service
before the UI to commission or approve runs. Without it, the clearly labelled
illustrative walkthrough remains navigable but cannot be submitted as work.

## API surface

- `GET /api/health`
- `GET /api/dashboard`
- `GET /api/runs`
- `GET /api/runs/{run_id}`
- `GET /api/runs/{run_id}/artifacts`
- `GET /api/settings`
- `POST /api/analyses`
- `POST /api/runs/{run_id}/approvals`
- `PUT /api/settings`

The analysis intake keeps confidential `internal_context` separate from the
sanitized `public_research_context` that may be used for DuckDuckGo discovery.
It sends an explicit draft/publishable target and evidence cutoff. Optional
Finance-owned scenario ranges are blank by default and use the canonical
deterministic economics contract; the UI never invents missing financial data.

Completed results hydrate the force assessments, evidence ledger, economics,
board brief, quality findings, artifact links, and exact-content approvals from
`GET /api/runs/{run_id}`. The run-history selector reloads persisted records.
Only endpoint, model, search region, and capture limit appear under Settings,
because those are the fields the API actually accepts and enforces.

## Verify

```bash
npm run lint
npm exec tsc -- --noEmit
npm test
```

The UI is local-first. oMLX is expected to expose an OpenAI-compatible endpoint
to the Python service; browser code does not call the model server directly.
