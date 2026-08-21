# SQLite entity-relationship design

Status: implemented schema v2  
Last reviewed: 2026-08-20

`SQLiteRunRepository` is the local system of record for run coordination and
audit provenance. The schema is deliberately compact: strongly typed domain
artifacts remain canonical JSON, while columns needed for identity, lineage,
integrity, and operational queries are normalized.

## Entity relationship diagram

```mermaid
erDiagram
    RUNS ||--o{ ATTEMPTS : contains
    RUNS ||--o{ GOALS : evaluates
    RUNS ||--o{ QUERIES : executes
    ATTEMPTS ||--o{ QUERIES : emits
    QUERIES ||--o{ HITS : discovers
    HITS ||--o{ CAPTURES : captured_as
    RUNS ||--o{ ARTIFACTS : produces
    ATTEMPTS o|--o{ ARTIFACTS : may_produce
    RUNS ||--o{ APPROVALS : governs
    ARTIFACTS ||--o{ APPROVALS : reviewed_as

    RUNS {
        text run_id PK
        text status
        text request_json
        text created_at
        text updated_at
    }

    ATTEMPTS {
        text attempt_id PK
        text run_id FK
        integer attempt_number UK
        text status
        text started_at
        text completed_at
        text goal_report_json
    }

    GOALS {
        text goal_id PK
        text run_id FK
        text criterion_key UK
        text description
        integer required
        text status
        real score
        text evidence_json
        text created_at
        text updated_at
    }

    QUERIES {
        text execution_id PK
        text run_id FK
        text attempt_id FK
        text query_id
        text force
        text query_text
        text rationale
        text preferred_source_classes_json
        text recency
        text provider
        text executed_at
        text query_sha256
    }

    HITS {
        text source_id PK
        text execution_id FK
        text query_id
        integer rank UK
        text title
        text url UK
        text snippet
        text provider
        text retrieved_at
        text search_hit_sha256
    }

    CAPTURES {
        text capture_id PK
        text source_id FK
        text execution_id
        text query_id
        text discovered_url
        text final_url
        text title
        text publisher
        text retrieved_at
        text media_type
        integer byte_length
        text content_sha256
        text extracted_text_sha256
        text extracted_text
        integer text_truncated
        text trust_classification
    }

    ARTIFACTS {
        text artifact_id PK
        text run_id FK
        text attempt_id FK
        text artifact_type
        integer schema_version
        text content_sha256
        text payload_json
        text created_at
    }

    APPROVALS {
        text approval_id PK
        text run_id FK
        text artifact_id FK
        text role
        text reviewer
        text reviewed_at
        text decision
        text brief_sha256
        text approval_json
    }
```

`SCHEMA_MIGRATIONS` is omitted from the diagram because it has no domain
relationship. It records `version`, `name`, and `applied_at`; SQLite
`user_version` mirrors the latest applied migration.

## Relationship semantics

| Relationship            | Cardinality and rule                                                                                                                      |
| ----------------------- | ----------------------------------------------------------------------------------------------------------------------------------------- |
| Run to attempt          | A run has zero or more attempts; `(run_id, attempt_number)` and `(run_id, attempt_id)` are unique.                                        |
| Run to goal             | A run has one row per criterion key; goal status is an updatable projection of evaluation history.                                        |
| Attempt to query        | Every executed query belongs to an existing attempt in the same run through a composite foreign key.                                      |
| Query to hit            | Hits are ordered by rank; rank and canonical URL are unique within one execution.                                                         |
| Hit to capture          | Every capture starts from a registered hit. Multiple time-separated captures are representable, although capture IDs are content-derived. |
| Run/attempt to artifact | An artifact always belongs to a run and may be associated with one attempt in that same run.                                              |
| Artifact to approval    | Every approval names an immutable artifact and must carry its exact content SHA-256.                                                      |

All foreign-key deletes use `RESTRICT`. Provenance cannot be erased by deleting a
parent through ordinary repository operations.

## Immutability model

```mermaid
flowchart LR
    MUTABLE[Mutable coordination projections]
    APPEND[Append-only audit records]

    MUTABLE --> R[RUNS status and updated_at]
    MUTABLE --> A[ATTEMPTS completion status and report]
    MUTABLE --> G[GOALS status, score, and evidence]

    APPEND --> Q[QUERIES]
    APPEND --> H[HITS]
    APPEND --> C[CAPTURES]
    APPEND --> T[ARTIFACTS]
    APPEND --> P[APPROVALS]
```

Schema v2 installs `BEFORE UPDATE` and `BEFORE DELETE` triggers for queries,
hits, captures, artifacts, and approvals. The repository inserts these records
inside bounded transactions. Attempts and goals are intentionally mutable
coordination rows; the immutable attempt manifest and rendered audit sidecar
retain the complete Ralph history.

## Content and lineage integrity

| Record     | Integrity mechanism                                                                                                                                                                          |
| ---------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Query      | SHA-256 of the canonical `ResearchQuery`: ID, force, text, rationale, preferred source classes, and recency. Provider and execution time are stored immutably but are not part of this hash. |
| Search hit | SHA-256 of execution identity, rank, canonical URL, metadata, provider, and retrieval time                                                                                                   |
| Capture    | Registered `source_id` match; discovered URL match; raw-content and extracted-text SHA-256 hashes                                                                                            |
| Artifact   | SHA-256 of canonical JSON payload; recomputed on read                                                                                                                                        |
| Approval   | Foreign key to artifact plus `brief_sha256 == artifact.content_sha256` check in repository service                                                                                           |

The database hashes are tamper-evidence within the local application boundary,
not cryptographic signatures or remote attestation. A workstation administrator
can replace the database and application together; stronger non-repudiation
would require signed manifests and external immutable storage.

## SQLite configuration

On open, the repository applies:

- `PRAGMA foreign_keys = ON`;
- `PRAGMA journal_mode = WAL`;
- `PRAGMA synchronous = NORMAL`;
- `PRAGMA busy_timeout = 5000`;
- numbered, transactional migrations;
- `PRAGMA optimize` during explicit optimization and clean close.

`:memory:` is supported for tests. All other paths are resolved as local
filesystem paths; SQLite URI syntax and network-like URLs are rejected.

## Indexes

Schema v2 creates operational indexes for:

- run status and latest update;
- attempt number by run;
- required goal status by run;
- query execution time by attempt;
- hit rank by query and URL lookup;
- captures by source and retrieval time;
- artifacts by run, type, and creation time;
- approvals by artifact, role, and decision.

## Canonical domain data versus storage rows

The normalized schema does not duplicate every Pydantic field. Canonical request,
goal evidence, attempt report, artifact, and approval payloads are stored as
validated JSON with `json_valid` checks. The application must deserialize them
back through their Pydantic contracts before using them at a gate. SQLite JSON
validity alone is not domain validation.

`CAPTURES.extracted_text` is deliberately retained for evidence review. It is
untrusted public content and must never be executed, interpreted as HTML in the
UI, or included in outbound queries without a new egress review.

## Backup and recovery implications

WAL databases must be backed up with the SQLite backup API or the `sqlite3`
`.backup` command while the service is stopped or quiescent. Copying only the
main database file while a WAL is active can omit committed pages. Artifacts
written outside SQLite must be backed up with the database and correlated by
run and content hash.
