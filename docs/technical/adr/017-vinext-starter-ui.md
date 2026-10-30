# ADR 017: Analyst console built on the vinext/Sites/Cloudflare starter template

- Status: Accepted, retrospective, inferred 2026-10-30
- Date: 2026-10-30 (recorded retrospectively; the decision itself dates from the evidence below)
- Decision owners: not recorded in the repository (retrospective record)

## Context

ADR 004 chose a separate vinext console. This record covers how it was built
and what template scaffolding came with it.

## Decision

- `ui/` uses vinext 1.0.0-beta.2, React 19.2, Vite 8, Tailwind 4,
  `@openai/sites-vite-plugin`, and `@cloudflare/vite-plugin` with Wrangler.
- `vite.config.ts` pins the dev server to `127.0.0.1` (vinext ignored
  `--host`; `04ce97c`) and proxies `/api` to `PFA_UI_API_PROXY`
  (default `http://127.0.0.1:8765`).
- `ui/.openai/hosting.json` sets `d1` and `r2` to `null`, so no Cloudflare
  bindings are configured; `db/schema.ts` is empty and `db/index.ts`,
  `worker/index.ts`, and `app/chatgpt-auth.ts` remain from the starter.
  No application file imports `chatgpt-auth.ts` or `getDb`.
- Until a Python result loads, the console shows an "illustrative workspace"
  banner and never simulates success.

## Alternatives considered

### Plain Vite/React SPA

Not recorded. Matching the "Contingency Atlas style" is the reason ADR 004
gives.

## Consequences

### Positive consequences

- Same stack as Pedro's other consoles.

### Costs and limitations

- Dormant cloud and ChatGPT-auth scaffolding adds dependencies and attack
  surface, and may confuse readers about the deployment model (A-009).
- `playwright` is a runtime dependency and `drizzle-orm` is unused today.

## Verification

- `npm run lint`, `npm exec tsc -- --noEmit`, `npm test` (build plus Node
  tests); 8 UI tests passed on 2026-08-21.

## Evidence

- `ui/package.json`, `ui/vite.config.ts`, `ui/.openai/hosting.json`
- `ui/worker/index.ts`, `ui/db/index.ts`, `ui/db/schema.ts`, `ui/app/chatgpt-auth.ts`
- `ui/README.md`
- Commits: `f7865ff`, `e0b8ac6`, `04ce97c`
