# Data-flow diagrams

Status: normative local design  
Last reviewed: 2026-08-21

These diagrams describe what crosses each trust boundary. They intentionally
separate search discovery, page capture, evidence promotion, and publication.

## Data classes

| Class                     | Examples                                                            | Permitted destinations                                               |
| ------------------------- | ------------------------------------------------------------------- | -------------------------------------------------------------------- |
| Restricted internal       | Internal facts, client names, positions, controls, restricted terms | Local UI, API, service, database, approved local oMLX only           |
| Approved public context   | Sanitized market, geography, product, and time horizon              | Local services, oMLX, DuckDuckGo query planner                       |
| Public untrusted          | Search results, snippets, captured page text                        | Discovery/capture stores and bounded model context; never executable |
| Derived decision material | Claims, assessments, options, economics, brief                      | Local stores and reviewers; publish only after gates                 |
| Security-sensitive        | API keys, configuration, approval identities                        | Local process/configuration; never artifacts or public queries       |

## Level 0: system context

```mermaid
flowchart LR
    USER[Technical adviser or reviewer]
    SYS((PorterForcesAI local system))
    MODEL[oMLX local model server]
    DDG[DuckDuckGo]
    SOURCES[Public source hosts]
    STORE[(Local SQLite and artifacts)]

    USER -->|question, public context, internal context, economics, approvals| SYS
    SYS -->|run state, evidence, analysis, board brief| USER
    SYS <-->|structured prompts and responses| MODEL
    SYS -->|sanitized public queries only| DDG
    DDG -->|untrusted discovery metadata| SYS
    SYS -->|GET registered public URL| SOURCES
    SOURCES -->|bounded untrusted response| SYS
    SYS <--> STORE
```

Prohibited flows are as important as the arrows shown: internal context,
restricted terms, model prompts, approval identities, and secrets must not be
sent to DuckDuckGo or public source hosts.

## Level 1: analysis and verification

```mermaid
flowchart TB
    U[Analyst]
    UI[1.0 Local UI or CLI]
    API[2.0 Local API]
    ACQ[3.0 Durable acquisition phase]
    FRAME[3.1 Decision framing]
    RESEARCH[3.2 Five-force discovery and capture]
    CALC[4.0 Economics calculator]
    RALPH[5.0 Ralph supervisor]
    INNER[5.1 Fresh analysis StateGraph]
    EVAL[5.2 Deterministic evaluator]
    CHECK[5.3 Per-attempt checkpoint]
    RENDER[6.0 Artifact renderer]
    REVIEW[7.0 Publication gate]
    D1[(D1 Run repository)]
    D2[(D2 Evidence snapshot)]
    D3[(D3 Artifacts)]

    U -->|request and owned inputs| UI
    UI -->|validated JSON| API
    API --> ACQ
    ACQ --> FRAME
    FRAME --> RESEARCH
    RESEARCH -->|queries, hits, captures| D1
    RESEARCH -->|promoted captured evidence| D2
    ACQ --> CALC
    CALC -->|owned NPV, ROI, payback, delay results| RALPH
    D2 -->|frozen evidence and snapshot ID| RALPH
    RALPH -->|objective, fresh thread, gap directives| INNER
    D2 -->|cached research and captured evidence| INNER
    CALC -->|immutable calculator results| INNER
    INNER -->|candidate brief, ledger, challenge| EVAL
    EVAL -->|criterion outcomes and remediation| RALPH
    RALPH --> CHECK
    CHECK -->|attempt, goals, Ralph state| D1
    CHECK -->|retry only after checkpoint| RALPH
    RALPH -->|accepted candidate| RENDER
    RENDER --> D3
    RENDER -->|immutable public payloads and result| D1
    D1 -->|immutable board artifact and exact fingerprint| REVIEW
    U -->|role decision for exact fingerprint| REVIEW
    REVIEW -->|immutable approval| D1
    REVIEW -->|publishable state or missing roles| API
    API --> UI
```

Acquisition and finance calculation finish before candidate synthesis. A Ralph
retry reuses the cached research bundles, frozen captures, and exact calculator
results; it does not silently search again. The evaluator receives the candidate
but does not invoke the generator to decide success. Every completed attempt is
persisted before another retry begins. Those checkpoints provide audit and
restart diagnosis, not resumption of a partly completed model call. The status
`achieved_draft` means deterministic draft criteria passed. `publishable`
additionally requires all current content-bound human approvals.

## Level 2: public research and evidence promotion

```mermaid
flowchart LR
    CTX[Approved public research context]
    PLAN[Hypothesis and query planner]
    EGRESS{Outbound egress policy}
    SEARCH[DuckDuckGo adapter]
    DDG[DuckDuckGo]
    REG[(Executed-query and hit ledger)]
    SELECT[Force-balanced candidate selection<br/>with publisher diversity]
    FETCH{Safe capture boundary}
    HOST[Registered public host]
    CAP[(Content-addressed capture)]
    CLASSIFY[Policy-owned source class, scores,<br/>and excerpt selection]
    EVID[(EvidenceItem ledger)]
    LINK[Claim-evidence linker]
    CLAIM[(Canonical claims)]

    CTX --> PLAN
    PLAN -->|query plus rationale| EGRESS
    EGRESS -- reject --> PLAN
    EGRESS -- approved query --> SEARCH
    SEARCH -->|query| DDG
    DDG -->|titles, URLs, snippets| SEARCH
    SEARCH -->|exact query and ordered hits| REG
    REG --> SELECT
    SELECT -->|ledger-minted source ID| FETCH
    FETCH -->|validate and pin public DNS on every hop; bounded GET| HOST
    HOST -->|untrusted HTML or text| FETCH
    FETCH -->|hashes, clean text, final URL| CAP
    CAP --> CLASSIFY
    CLASSIFY -->|captured excerpt and source metadata| EVID
    EVID -->|exact quote locator + lexical screen| LINK
    LINK --> CLAIM
```

Trust-state rules:

1. A `SearchHit` is discovery metadata only. Its snippet cannot support a
   board-visible material claim.
2. Capture accepts a ledger-minted `source_id`, not an arbitrary model-supplied
   URL.
3. DNS is checked before each request and redirect, and the approved address set
   is pinned into the connection path. Private, loopback, link-local, reserved,
   multicast, and non-global addresses are rejected.
4. Redirect count, media types, time, response bytes, and extracted characters
   are bounded. Only HTML, XHTML, and plain text are accepted; PDF is rejected.
   HTTPS downgrade is rejected by default.
5. Captured text remains untrusted external content. Promotion records the
   capture hash, class, publisher, excerpt, scores, applicability, and retrieval
   time.
6. A claim must cite canonical evidence IDs and have matching explicit link
   records. Each link's supporting quote must occur exactly in the excerpt, and
   fact/inference links pass a lexical-alignment screen. Capture and alignment
   prove neither semantic entailment nor truth.
7. Candidate capture is application-owned, round-robin across the five force
   sets, and biased toward publisher diversity. Source class and conservative
   scores are policy-owned rather than accepted from the model.

## Level 2: model capability boundary

```mermaid
flowchart TB
    ASSIGN[PublicResearchAssignment]
    AGENT[Bounded Deep Agent]
    MODEL[oMLX chat model]
    TOOL[search_public_web]
    LIMITS[Model and tool call limits]
    GUARD[Tool-call allowlist middleware]
    DURABLE[(Force-scoped query and hit record)]
    RESULT[ResearchBundle schema]
    RECONCILE[Recorded-query and URL reconciliation]

    ASSIGN --> AGENT
    AGENT <--> MODEL
    AGENT --> LIMITS
    MODEL -->|requested tool call| GUARD
    GUARD -->|allowed| TOOL
    GUARD -->|guessed or hidden tool| REJECT[Fail closed]
    TOOL -->|successful search callback| DURABLE
    DURABLE -->|persist before tool result returns| AGENT
    AGENT --> RESULT
    RESULT --> RECONCILE
```

The research worker's model-visible capability set is the application-owned
search tool and the structured `ResearchBundle` response. Host filesystem,
shell, general-purpose subagents, unrestricted fetching, and final board
recommendation authority are outside that boundary.

The worker contract asks for one parallel batch. Middleware executes at most
five searches per force and returns explicit limit errors for excess calls so
the model can still synthesize its typed bundle. Each successful execution is
assigned force-scoped sequence lineage and committed synchronously before its
result reaches the model. Concurrent records are later sorted by that lineage;
a bundle/schema failure cannot erase the searches that already completed.

## Level 2: human publication flow

```mermaid
sequenceDiagram
    participant S as Analysis service
    participant Q as Quality gate
    participant R as Reviewer
    participant DB as SQLite repository

    S->>Q: BoardBrief plus canonical ledger
    Q-->>S: draft_valid and findings
    S->>DB: Store brief and SHA-256 fingerprint
    S-->>R: Display exact revision and evidence
    R->>S: role, reviewer, approve or reject, fingerprint
    S->>DB: Append immutable approval record
    S->>Q: Re-evaluate with all approvals
    Q-->>S: publishable only if draft valid, all roles approve, no current rejection
    Note over S,Q: Any brief change creates a new fingerprint and makes prior approvals stale
```

## Failure flows

```mermaid
flowchart TD
    OP[Operation] --> F{Failure type}
    F -->|ambiguous scope| HUMAN[Fail closed; clarify and start a new run]
    F -->|egress violation| DENY[Reject without network call]
    F -->|filtered DuckDuckGo no-results| RELAX[One same-provider unfiltered retry]
    F -->|unfiltered no-results| EMPTY[Successful empty discovery set]
    F -->|timeout, rate limit, or provider error| SEARCHFAIL[Fail closed as search unavailable]
    F -->|unsafe source| QUAR[Reject source and record gap]
    F -->|model schema failure| CONTRACT[Runtime contract error]
    F -->|quality defect| REPAIR[One local repair then Ralph evaluation]
    F -->|same Ralph gap repeats| STALL[Blocked by stall limit]
    F -->|missing publication approval| PAUSE[Human required]
    F -->|reviewer rejection| BLOCKED[Blocked; new revision required]
    F -->|process interrupted| RECOVER[Startup marks run failed and closes open attempt]
    F -->|missing finance input| DISCLOSE[Disclose missing input; never invent it]
```

There is no permitted failure route from unavailable evidence to "use model
memory as fact." Model priors remain labeled priors or evidence gaps. There is
also no general pause/resume API in the local release: startup recovery fails an
interrupted run closed and directs the operator to create a new run.
