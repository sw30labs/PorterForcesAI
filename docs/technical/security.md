# Security and trust model

Status: local single-user threat model  
Last reviewed: 2026-08-21

The most important security property is not "the model is local." It is that
confidential inputs, public egress, untrusted content, model capabilities,
evidence promotion, and publication authority are separated and auditable.

## Scope and assumptions

This model assumes:

- one trusted operator on a managed workstation;
- UI and FastAPI bind to loopback, and the default oMLX endpoint is loopback;
- SQLite, generated artifacts, and `.env` are local plaintext files protected by
  the workstation account and storage controls;
- the operating-system account and repository are trusted;
- public egress is limited to DuckDuckGo and registered HTTP(S) source hosts;
- no untrusted user has local shell or filesystem access;
- publication still occurs through an accountable human process.

It does not cover public hosting, multi-tenancy, hostile local administrators,
malware on the workstation, or a compromised model server. Those deployments
require a new threat model.

## Trust zones

```mermaid
flowchart LR
    subgraph Z1[Zone 1: trusted local interaction]
        USER[Adviser and reviewers]
        UI[Browser UI]
    end

    subgraph Z2[Zone 2: trusted local application]
        API[FastAPI]
        GRAPH[LangGraph and Ralph]
        POLICY[Egress, capture, and quality policies]
        DB[(SQLite and artifacts)]
    end

    subgraph Z3[Zone 3: approved local model]
        OMLX[oMLX server]
    end

    subgraph Z4[Zone 4: untrusted public network]
        DDG[DuckDuckGo]
        WEB[Public pages]
    end

    USER --> UI
    UI -->|validated local JSON| API
    API --> GRAPH
    GRAPH --> POLICY
    GRAPH <-->|confidential prompts and structured output| OMLX
    POLICY -->|sanitized queries| DDG
    DDG -->|untrusted metadata| POLICY
    POLICY -->|registered bounded GET| WEB
    WEB -->|untrusted bytes| POLICY
    POLICY <--> DB
```

Crossing from Zone 2 to Zone 4 is the principal confidentiality boundary.
Crossing from Zone 4 back to Zone 2 is the principal content-integrity boundary.

## Security objectives

| Objective              | Required property                                                                                                             |
| ---------------------- | ----------------------------------------------------------------------------------------------------------------------------- |
| Prompt confidentiality | Internal context goes only to approved local components and oMLX.                                                             |
| Outbound minimization  | Only sanitized public query text leaves the workstation.                                                                      |
| SSRF resistance        | Model output cannot fetch arbitrary hosts; each target and redirect is validated and bounded.                                 |
| Capability confinement | Research agents cannot use host filesystem, shell, general subagents, or unrestricted URL fetch.                              |
| Provenance integrity   | Queries, hits, captures, artifacts, and approvals retain identities and SHA-256 hashes.                                       |
| Content safety         | Public content is labeled untrusted, normalized to text, never rendered as executable HTML, and never granted tool authority. |
| Decision integrity     | Material board points resolve to canonical claims and evidence/assumption IDs.                                                |
| Publication authority  | Four accountable roles approve the exact current brief fingerprint.                                                           |
| Availability bounds    | Tool/model calls, redirects, bytes, characters, attempts, budgets, and stalls have hard limits.                               |

## Implemented controls

### Confidentiality and egress

- `DecisionRequest` separates `public_research_context` from
  `internal_context`.
- `restricted_terms` stay outside the model-visible
  `PublicResearchAssignment`; the egress policy rejects matching queries.
- Public queries have length bounds. Search explicitly selects the DuckDuckGo
  backend and has no hidden provider fallback.
- The adapter retains the installed DuckDuckGo engine's HTTP status instead of
  relying on DDGS's ambiguous no-results exception. Only an HTTP 200 page with a
  recognized no-results DOM class is empty discovery. A recency-filtered verified
  empty result gets one unfiltered retry on that same backend. Unrecognized
  layouts, challenges, non-200 responses, blank/malformed pages, timeouts, rate
  limits, and ambiguous sentinels fail closed; response bodies are not included
  in adapter errors.
- Remote model endpoints are rejected unless
  `PFA_ALLOW_REMOTE_MODEL_ENDPOINT=true`; non-loopback remote endpoints also
  require HTTPS.
- Local oMLX HTTP clients ignore environment proxies, preventing accidental
  forwarding of prompts through corporate proxy configuration.

The query guard is deterministic string policy, not a data-loss-prevention
system. Paraphrases, encoded values, or facts not listed as restricted can still
leak. Operators must sanitize `public_research_context` and review outbound
policy for sensitive deployments.

### Agent capability boundary

- The research worker receives a force-specific public assignment rather than
  the full internal decision request.
- The model-visible tool set is limited to `search_public_web` and the
  `ResearchBundle` structured response.
- Deep Agents filesystem/shell scaffolding is excluded for the exact model
  profile; the general-purpose subagent is disabled.
- Middleware rejects guessed or hidden tool names.
- Each force worker is instructed to issue one parallel batch. The hard tool
  limit executes at most five searches and returns explicit errors for excess
  calls while still allowing typed bundle synthesis; model calls are also
  bounded.
- Search candidates are reconciled to URLs actually observed by the recording
  provider.
- The recording boundary stamps force-scoped sequence IDs and synchronously
  persists every successful query/hit execution before returning the tool result
  to the model. Concurrent records are deterministically sorted by lineage, and
  partial discovery remains durable if later bundle generation fails.

These controls constrain consequences; they do not make model behavior trusted.

### URL and source capture

- Source capture accepts a ledger-minted `source_id`, not an arbitrary URL.
- URLs reject credentials, local names, private/non-global IP literals, malformed
  ports, fragments, and tracking parameters.
- The resolver checks every address before the initial request and every manual
  redirect; private, loopback, link-local, reserved, multicast, and unspecified
  ranges are rejected.
- The owned HTTP transport resolves again at connection time, revalidates the
  complete answer set, and connects to a validated IP literal while retaining
  the original HTTP Host and TLS server name. This removes the independent
  resolver lookup that would otherwise permit DNS rebinding between validation
  and connection.
- Automatic redirects and environment proxies are disabled.
- Redirect count, timeout, content type, declared/observed response bytes, and
  extracted text length are bounded.
- HTTPS-to-HTTP redirect downgrade is disabled by default.
- Only HTML, XHTML, and plain text are accepted. Scripts, styles, templates,
  SVG, canvas, `noscript`, control characters, and markup are removed during
  plain-text extraction. PDF and other document formats are rejected.
- Raw response bytes and extracted text receive separate SHA-256 hashes. The
  raw bytes are not retained by the default store.

IP pinning prevents application-level DNS time-of-check/time-of-use rebinding,
but it is not a substitute for network egress enforcement. High-assurance
deployments should still isolate the fetcher and deny private, local, and
metadata-service destinations at the operating-system or network layer.

### Evidence and prompt-injection containment

Search snippets never become `EvidenceItem` instances. The application selects
capture candidates coverage-first across the five force-specific hit sets,
rather than trusting the model to spend the global source budget. A zero-hit
force fails before fetch. Each unique fetch, including a policy or network
failure, consumes one slot from the fixed cap; retries target uncovered forces
round-robin, then remaining slots fund balanced publisher-diverse enrichment.
Captured content remains classified as `untrusted_external_content`; promotion
requires source class, publisher, captured hash, excerpt, applicability, and
quality values. Source classification and the conservative
quality/freshness/applicability scores are policy-owned; the model does not
supply them.

Claims and explicit claim-evidence links must reconcile. Each link includes a
`supporting_quote` that must occur exactly in the captured excerpt. Material fact
and inference links also pass a conservative lexical-alignment screen. These
checks detect absent, fabricated, and plainly unrelated locators. They are not
an independent semantic-entailment model, publisher authentication, or proof of
truth; a lexically similar quote can still be misleading or misapplied.

Public text can contain prompt-injection instructions. Normalizing HTML to text
removes executable markup but not malicious language. The structured model
prompts tell the model to treat it as evidence data, and model tool capability is
narrow; deterministic validators then constrain IDs and publication. This
reduces impact but does not prove semantic immunity. High-risk runs require
human source review, source allowlists, and/or an isolated evidence extraction
model with no tools.

### Persistence and approval integrity

- SQLite foreign keys, JSON validity constraints, unique keys, and transactional
  migrations are enabled.
- Queries, hits, captures, artifacts, and approvals are append-only through
  database triggers.
- Artifact hashes are recomputed on read.
- Repository insertion verifies an approval's run, artifact, and exact SHA-256.
- Quality evaluation ignores stale approvals and blocks publication when any
  required role is missing or a current reviewer rejected.
- Each approval transaction appends the decision plus quality/memo/result
  revisions, then updates the current goal projection and status. Attempt-time
  Ralph output remains immutable and is shown separately from the current human
  gate.
- During live acquisition the decision frame is checkpointed first. Each
  successful search execution/hit set is committed before its tool result
  returns; the completed force bundle is checkpointed afterward; successful
  captures are appended as they complete. Each completed Ralph step stores its
  manifest, goal rows, and an immutable checkpoint before the next retry.
- A supported writer acquires both an in-process registration and a mode-`0600`
  POSIX advisory lock before migrations, recovery, or writes. This prevents a
  second cooperating process from declaring active work interrupted. Read-only
  inspection uses SQLite `mode=ro`/`query_only` and does not take the lease.

SHA-256 records are tamper-evident only relative to the local database and
application. They are not digital signatures. An administrator able to replace
both can forge history.

The writer lease is cooperative, not an authorization boundary: a hostile or
direct SQLite process can ignore it. Operating-system account isolation and file
permissions remain required. The retained `.writer.lock` sidecar is not secret
and its presence alone says nothing about current ownership.

The application does not encrypt the database, artifact files, WAL/SHM
sidecars, backups, or `.env`. These may contain confidential context, captured
source text, derived/model output, and reviewer identities. Full-disk or
volume encryption, restrictive permissions, encrypted backups, retention, and
secure deletion are required deployment controls.

## Local API and browser controls

The implemented local adapter and launcher provide:

- a settings-enforced loopback API bind;
- a local `Host` allowlist and narrow CORS for the configured UI origin;
- strict API and nested domain request schemas;
- recursive redaction of internal-context values and restricted terms from run
  details, including nested Ralph state and hydrated results after restart;
- allowlisted logical downloads for only the board memo and evidence register,
  served from hash-verified immutable SQLite payloads rather than mutable paths;
- safe React text rendering of public excerpts; and
- settings responses that omit model API keys and remote-endpoint permission.

The Pydantic request contracts impose field and cardinality limits, but the
local adapter does not yet impose one global HTTP body-size limit. Before any
wider or higher-assurance deployment, add that limit, `Cache-Control: no-store`
for sensitive responses, authenticated reviewer identities, structured log
redaction tests, and a restrictive browser Content Security Policy.

Loopback is not authentication. A malicious website can attempt localhost CSRF,
and DNS rebinding can target local services. Host/origin validation and
non-idempotent JSON POST semantics are required even in the local profile. If
the service binds beyond loopback, add authenticated identities, role-based
authorization, CSRF protection, TLS, rate limiting, and audit-grade session
records before use.

## Threat analysis

| Threat                      | Example                                        | Controls                                                                                     | Residual action                                           |
| --------------------------- | ---------------------------------------------- | -------------------------------------------------------------------------------------------- | --------------------------------------------------------- |
| Confidential query leakage  | Internal client or project name reaches search | Public/internal split, restricted-term egress guard                                          | Operator review and enterprise DLP integration            |
| Prompt injection            | Captured page tells model to ignore policy     | Text-only capture, no fetch tool in evidence stage, schemas, ID reconciliation, human review | Source allowlist and isolated extractor for high-risk use |
| SSRF                        | Redirect targets metadata service or localhost | Ledger IDs, per-hop URL validation, connection-time DNS validation and IP pinning, no proxy  | Network sandbox and OS/network egress denial              |
| Hallucinated citation       | Model invents or edits URL/evidence ID         | Provider reconciliation, capture, exact quote locator, lexical screen, canonical link gate   | Human semantic and source validation                      |
| Tool escalation             | Model guesses shell or filesystem tool         | Exact profile exclusion and tool-call middleware                                             | Contract tests on every dependency/model upgrade          |
| Model endpoint exfiltration | Operator configures remote HTTP model          | Remote disabled by default; remote requires explicit flag and HTTPS                          | Formal third-party/model data review                      |
| Artifact tampering          | Brief changed after review                     | Canonical hashes and immutable artifact/approval records                                     | External signing for non-repudiation                      |
| Stale approval              | Old approval reused for revised brief          | Exact brief fingerprint; stale approvals ignored                                             | Reviewer UI must show revision and hash                   |
| Resource exhaustion         | Infinite agents, giant pages, repeated retries | Tool, byte, redirect, attempt, budget, and stall bounds                                      | OS/process quotas and concurrency limits                  |
| Cross-site local request    | Hostile webpage calls local API                | Loopback, Host/Origin allowlist, narrow CORS, JSON POST                                      | Add local auth token if browser threat increases          |
| Unsafe HTML display         | Evidence page contains script                  | Plain-text extraction and React text rendering                                               | Content Security Policy and no raw HTML APIs              |
| Local data disclosure       | Database or sidecar copied from workstation     | Git ignores runtime stores; operator-controlled local paths                                  | Disk/backup encryption, permissions, retention, deletion  |

## Secrets and configuration

- Keep `.env` out of Git. Commit only `.env.example` with blank or placeholder
  values.
- A local development API key such as `test` is not a production secret and
  must not be reused outside the workstation.
- Restrict database, artifact, and environment-file permissions to the operator.
- Use workstation full-disk/volume encryption and encrypt every backup that
  contains the database, WAL/SHM files, artifact directory, or `.env`.
- Do not put secrets in `public_research_context`, run names, filenames, CLI
  arguments visible in process listings, or screenshots.
- Rotate a real oMLX API key after suspected disclosure and re-run the model
  canary.

## Security validation checklist

- Egress tests prove restricted terms are rejected before the provider call.
- Agent contract tests prove only the allowed tool schema is exposed and guessed
  calls fail.
- URL tests cover private IPv4/IPv6, numeric hosts, local suffixes, redirects,
  downgrade, invalid media types, large/chunked bodies, and proxy bypass.
- Evidence tests cover force-balanced capture selection, policy-owned source
  scores, exact quote locators, lexical misalignment, and the explicit limits of
  those checks.
- Repository tests cover foreign-key failures, immutable triggers, hash
  verification, cross-run artifacts, and approval mismatch.
- API tests cover Host/Origin checks, recursive response redaction, schema field
  sizes, invalid paths, immutable downloads, recovery, and approval conflicts.
- UI tests verify excerpts are text, not HTML.
- Dependency and model upgrades rerun tool-call and structured-output canaries.

## What a passing run does not prove

A passing quality gate and Ralph goal report prove that the configured contracts
were enforced over a named evidence snapshot. They do not prove source truth,
future market outcomes, regulatory compliance, control effectiveness, or
fitness for a real capital decision. Those remain accountable human judgments.
