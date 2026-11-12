# 20 — Open-DV Ontology Construction Studio: Functional Specification & Interface Reference

**Status:** Normative Functional Specification (Milestone M2 Deliverable)  
**Target Repository:** `open-dv` (`packages/opendv-contracts`, `packages/opendv-sources`, `packages/opendv-agent-harness`, `packages/opendv-service`, `apps/opendv-platform-api`, `apps/opendv-cli`, `apps/opendvctl`, `apps/opendv-web-workbench`)  
**Interface Dialects:** OpenAPI 3.1.0 (`schemas/v1/openapi.json`), CLI Specification v1 (`schemas/v1/cli-spec.json`), W3C Server-Sent Events, RFC 8785 JCS, RFC 9457 Problem Details  
**Normative Cross-References:** `docs/19-ontology-studio-architecture.md`, `master-plan.md`, `ADR-001` through `ADR-016`, `HANDOFF.md`

---

## 1. Functional Requirements & Personas

### 1.1 Target System Personas & Role Hierarchy
The Open-DV Ontology Construction Studio enforces a 4-tier role hierarchy: `viewer` $\subset$ `editor` $\subset$ `admin`, with `automated` (`INTERNAL_MODELER`) as a non-human service identity:

| Persona | Role Clearance | Primary Objectives | Interface Surfaces | Constraints |
|---|---|---|---|---|
| **Viewer** | `viewer` | Read-only inspection (`list`, `status`, `proposal list/show`, `export`). | CLI / Workbench | Read-only; cannot mutate state. |
| **Data Steward** | `DATA_STEWARD` / `editor` | Discovers semantics, answers clarifications, reviews proposals, applies additive deltas. | Web Workbench SPA (`/ontology`), interactive Q&A | Additive modeling only; admin waiver required for low confidence. |
| **Enterprise Architect** | `RESTRICTED_PII_CUSTODIAN` / `admin` | Governs workspaces, grants waivers, executes steward revisions, authorizes promotion. | In-process CLI (`opendv`), `opendvctl`, admin dialogs | Full administrative elevation; accountable for audit trail. |
| **Automated Modeler / CI** | `INTERNAL_MODELER` / `automated` | Headless CI/CD execution, zero-credential deterministic pipelines. Explicit allow-list: `create`, `upload`, `parse`, `construct --mode deterministic --auto-commit`. | Headless CLI / CI runner | Destructive ops, waivers, manual proposal accept, and promotion strictly forbidden. |

### 1.2 Functional Requirements (FR-1 through FR-10)

| Requirement ID | Name | Summary Description | Applicable Personas | Normative Exit Codes |
|---|---|---|---|---|
| **FR-1** | Multi-Format Document Ingestion | Ingest, hash, and index source files (PDF, MD, CSV, Parquet, JSON, Office) into CAS with NFC/LF normalization and codepoint offset extraction. | All personas | 0 (OK), 2 (Syntax), 4 (Corrupt), 1 (OCR/Office missing) |
| **FR-2** | Reference Ontology Alignment | Search, score, and align discovered business concepts against curated FIBO core modules (`FND`, `BE`, `FBC`, `SEC`, `LOAN`, `IND`) using Okapi BM25 ($\ge 0.6500$). | All personas | 0 (OK), 4 (Grounding violation) |
| **FR-3** | Dual-Mode Ontology Construction | Execute construction via Agent-First mode (3-persona LLM) or Deterministic Fallback mode (zero-API-key heuristics + BM25 + holding area). | All personas | 0 (OK), 1 (Agent down), 2 (Precondition mismatch) |
| **FR-4** | Interactive Clarification Q&A | Automatically generate structured domain ambiguity questions with semantic rationale options, persisting human responses in CAS. | Steward | 0 (OK), 3 (Question not found) |
| **FR-5** | Additive Delta Management | Calculate SemVer per 24-Op Calculus & Genesis Invariant (`0.0.0` $\to$ `1.0.0` on initial CBC, then additive bumps) and commit immutable snapshots. | Steward, Architect | 0 (OK), 4 (Envelope/contract violation), 5 (OCC) |
| **FR-6** | Steward Revisions & Rebase | Apply destructive mutations under admin privilege; rebase proposals across concurrent head commits using 24-operation conflict matrix. | Architect | 0 (OK), 7 (Non-admin denied), 5 (Conflict) |
| **FR-7** | Interactive Web Studio Workbench | Decoupled React 19 SPA at `/ontology` with React Flow graph canvas, background `elkjs` layout, Level-of-Detail (LOD), and diff badges. | Steward, Architect | N/A (Web UI) |
| **FR-8** | Dual-CLI Platform Parity | Maintain bit-for-bit operational parity between in-process `opendv` (`LocalExecutor`) and Bun client `opendvctl` (`RemoteExecutor`). | All personas | 0..7 (Strict exit code mapping via error-codes.json) |
| **FR-9** | Workspace Pipeline Promotion | Atomically promote committed session head ontologies into workspace pipelines under containment check, contract resolution, and `graph_a.lock`. | Architect | 0 (OK), 1 (Rationale missing), 2 (Path), 5 (OCC) |
| **FR-10** | Secure Session Bundle Portability | Export CAS sessions to `.odvstudio.zip` archives; import archives with streaming decompression limits, ZIP bomb guards, and delta replay. | Steward, Architect | 0 (OK), 4 (Archive violation / replay mismatch) |

---

## 2. End-to-End User Journeys & Acceptance Criteria

#### 2.1 Journey 1: Greenfield Enterprise Modeling from Regulatory Filings
1. **Context**: Financial data steward creates a new Data Vault model from an SEC 10-K report without an existing workspace model.
2. **Session Creation**: Steward calls `POST /sessions` (`domain_namespace="wealth_mgmt"`, `enterprise_owner="Global Wealth"`), initializing at genesis version `0.0.0` (canonical hash `1916a5c6d501015984b887fd76609b225b72d5d393e5813d8df210b92ab3b4c9` in `baseline-digests.json`). Steward uploads `annual_report_2024.pdf` via `POST .../sources` (normalized into CAS).
3. **Parsing**: `POST .../parse` spawns async parser; SSE emits `progress_update` and `chunk_parsed`. Chunks, tables, and codepoint offset spans are persisted in `evidence/chunks.jsonl`.
4. **Grounding & Clarification**: `POST .../clarify` runs `@domain-clarifier`, detecting ambiguous alignments for *"Fund Advisor"* between `fibo-fbc-fct-fse:InvestmentAdvisor` and `fibo-be-oac-cctl:FundManager` (registered pack examples). Steward selects `InvestmentAdvisor` with rationale: *"Acts as registered investment advisor under SEC regulations."*
5. **Construction**: `POST .../construct` invokes `@ontology-architect`, synthesizing 6 CBCs (`InvestmentAdvisor`, `MutualFund`, etc.), 8 Links, and 24 Satellites with FIBO grounding. Proposal `prop-8821` created (`PENDING`).
6. **Acceptance & Commit**: Steward calls `POST .../proposals/prop-8821/accept` with `If-Match: "1916a5c6..."`. Genesis transition bumps `0.0.0` $\to$ `1.0.0` on adding CBCs, atomically committing `versions/v1.0.0/`.

### 2.2 Journey 2: Evolutionary Workspace Modeling & Promotion
1. **Context**: Enterprise architect evolves deployed private equity ontology in `examples/private_equity/pe_graph_a_ontology.yaml` with LP commitments.
2. **Workspace Seeding & Resolution**: Architect creates session: `opendv ontology studio create -s pe-studio -n private_equity --from-workspace examples/private_equity`. Target contract path resolves to `pe_graph_a_ontology.yaml`. Baseline loads at `1.0.0` (`head_sha256 = 1c100409cbbe12051f7211d2d6d6838670982b9164aaf18b52f566864da90b81`); existing URIs recorded as `grandfathered_concept_uris`.
3. **Additive Delta Ingestion**: Architect uploads `lp_agreement_template.md` and runs deterministic construction. Engine synthesizes `AddConcept("urn:opendv:concept:limited_partner_commitment")`, `AddAttribute("commitment_amount")`, `AddRelationship("lnk_fund_lp_commitment")`, and `AddExternalMapping("fibo-sec-eq:CapitalCommitment", 0.8200)` (registered pack mapping). Because `AddRelationshipOp` introduces new link topology without modifying `fund` hub attributes or definition, and new concept is grounded at 0.8200, delta satisfies ONT-EVO-002. Grandfathered exemption is ended strictly by the authoritative 16 concept-mutating operations (§7.3.3).
4. **Commit & Promotion Protocol**: Architect commits delta $\to$ `v1.1.0` (Minor bump). Promotes to workspace: `opendv ontology studio promote -s pe-studio -w examples/private_equity --workspace-sha256 <cur_sha> --head-sha256 <head_sha> --rationale "Add LP capital commitment semantics"`. Promotion verifies containment, acquires L4 then L5, verifies lineage continuity and ONT-EVO rules, executes two-phase intent commit, updates `session.json`, and releases locks in reverse order per §7.2.

### 2.3 Journey 3: Air-Gapped Zero-Credential CI Pipeline
1. **Context**: Headless CI runner validates ontology invariants without internet access or LLM credentials.
2. **Execution**: Creates session `opendv ontology studio create -s s-ci -n ci_benchmark --from-workspace examples/private_equity`, then runs `construct -s s-ci --base-version 1.0.0 --base-sha256 1c100409cbbe12051f7211d2d6d6838670982b9164aaf18b52f566864da90b81 --mode deterministic --auto-commit`. If no source inputs yield new candidates, construct completes with exit 0 (`NOOP_CONSTRUCT` in audit log). Deterministic auto-commit is strictly limited to additive ops with verified Path A grounding (zero waivers) and OCC verification. Ungrounded candidates and touched grandfathered concepts without $\ge 0.6500$ grounding are held back in `evidence/staged_candidates.json` via `is_concept_mutated`, guaranteeing ONT-EVO-002 compliance. Golden questions pass; terminates in $< 2.5\text{s}$ with exit `0`.

### 2.4 Journey 4: Steward Revision of Undeployed Concept
1. **Context**: Model refactoring renames an undeployed conceptual Hub.
2. **Execution**: Administrator submits `steward_revision.json` containing `RenameConceptOp`: `opendv ontology studio delta apply -s s1 --file steward_revision.json --base-sha256 <cur_sha> --rationale "Refactor conceptual naming per 2026 enterprise standard"`. Platform verifies admin role (`FORBIDDEN_ADMIN_REQUIRED` exit 7 if non-admin), asserts no physical table has been deployed for old slug, applies revision, and triggers Major bump (`v2.0.0`).

### 2.5 Normative Acceptance Criteria (AC-1 through AC-8)
- **AC-1 (Canonical Hash Invariance)**:
  `compute_ontology_sha256` evaluated over `pe_graph_a_ontology.yaml` must yield exactly `1c100409cbbe12051f7211d2d6d6838670982b9164aaf18b52f566864da90b81`. Zero byte drift permitted across platforms. Canonical genesis hash `1916a5c6d501015984b887fd76609b225b72d5d393e5813d8df210b92ab3b4c9` is verified against `baseline-digests.json`.
- **AC-2 (Zero Regression)**: All unit, integration, and compiler tests across `opendv-core`, `opendv-service`, and `opendv-duckdb-sandbox` pass 100% without modification.
- **AC-3 (Dual-CLI Parity)**: `opendv --dump-cli-tree` and `opendvctl --dump-cli-tree` produce canonical JCS command trees matching identically across spec version, commands, options, and error maps.
- **AC-4 (Construction Parity)**: Deterministic Fallback and Agent-First modes output valid `GraphAOntology` structures satisfying ONT-EVO rules.
- **AC-5 (Zero Offset Drift)**: Character offsets in `PageMapEntry` and `EvidenceRef` extracted from identical text are bit-for-bit identical across Windows, Linux, and macOS.
- **AC-6 (Security Perimeter Enforcement)**: Remote `/init`, `OPENDV_WORKSPACE_ROOTS` escapes, or ZIP bomb/symlinks reject with exit codes 7, 2, and 4 respectively.
- **AC-7 (Deployed Table Contract Guard)**: Destructive operations targeting deployed Data Vault tables are blocked with HTTP 422 and exit code 4.
- **AC-8 (Schema Synchronization)**: `error-codes.json`, `baseline-digests.json`, `openapi.json`, and `cli-spec.json` pass validation with zero undocumented tokens.

---

## 3. REST API Specification (`/api/v1/ontology/studio`)

All REST endpoints are rooted at `/api/v1/ontology/studio`. Bootstrap endpoints `POST /init` (loopback only, `local_standalone` mode; initial setup is unauthenticated on loopback, while `force=true` rotation strictly requires Bearer token possession `Authorization: Bearer <local_token>`) and `POST /auth/token` (single-use launch nonce exchange) run pre-authentication. To prevent DNS rebinding and cross-site attacks, bootstrap endpoints reject cross-site requests (`Sec-Fetch-Site: cross-site` or unauthorized foreign Origin) with `403 Forbidden` (`CSRF_ORIGIN_DENIED`, exit 7). Every subsequent state-mutating request MUST be authenticated via Bearer token (`Authorization: Bearer <token>`), mTLS client certificate, or launch-exchanged session cookie with `X-OpenDV-Workbench: 1` CSRF header. In `local_standalone` mode, loopback callers authenticate using `.local_token` or local session cookie.

**Strict Path Safety & Containment**: All path and query parameters are validated against closed patterns before path construction:
- `session_id`, `proposal_id`, `job_id`, `question_id`: `^[a-z0-9_-]{1,64}$` (`session_id` excludes reserved names `bundle`, `init`, `sessions`, `health`, `auth`, `admin`).
- `source_id`: `^[a-f0-9]{64}$` (SHA-256 hex digest).
- `version`: `^v?[0-9]+\.[0-9]+\.[0-9]+$` (SemVer tuple).
Every path within `<session_dir>` is verified via `try: os.path.commonpath([os.path.normcase(os.path.realpath(session_dir)), os.path.normcase(os.path.realpath(resolved_path))]) == os.path.normcase(os.path.realpath(session_dir)) except ValueError: False`. Traversal (`..`, slashes) aborts with `400 SYNTAX_INVALID_ARGUMENT` (exit 2).

### 3.1 Endpoint Inventory & Parameter Matrix

```
Base URI: /api/v1/ontology/studio
```

#### 1. `POST /init`
- **Summary**: Initialize Loopback Studio Workbench (`human:local_admin`). Loopback-only bootstrap; disabled in `remote_platform` mode.
- **Headers & Query**: optional `force` (bool); if `force=true`, requires `Authorization: Bearer <existing_local_token>`.
- **Responses**: 200 `InitStudioResponse` | 400 `ProblemDetail` | 401 `UNAUTHENTICATED` (on unauthenticated `force=true`) | 403 `LOOPBACK_PRIVILEGE_DISABLED_IN_REMOTE_MODE` | 409 `STUDIO_ALREADY_INITIALIZED` | 500 `INSECURE_STUDIO_ROOT_PERMISSIONS`

#### 2. `GET /sessions`
- **Summary**: List Studio Sessions
- **Parameters**: `limit` (int=50), `offset` (int=0), `status` (string, optional)
- **Responses**: 200 `list[StudioSessionSummaryDTO]` | 401 `UNAUTHENTICATED`

#### 3. `POST /sessions`
- **Summary**: Create Studio Session
- **Request Body**: `CreateSessionRequest`
- **Responses**: 201 `StudioSessionDTO` | 400 `INVALID_WORKSPACE_PATH`, `REMOTE_SERVER_PATH_FORBIDDEN`, `CONFLICTING_WORKSPACE_SEED_PARAMS` | 401 `UNAUTHENTICATED` | 403 `AGENT_OPERATION_FORBIDDEN` | 409 `SESSION_ALREADY_EXISTS`

#### 4. `GET /sessions/{session_id}`
- **Summary**: Get Studio Session Details
- **Path Parameters**: `session_id` (`^[a-z0-9_-]{1,64}$`, excluding reserved names `bundle`, `init`, `sessions`, `health`, `auth`, `admin`)
- **Responses**: 200 `StudioSessionDetailDTO` | 400 `INVALID_SESSION_IDENTIFIER` | 401 `UNAUTHENTICATED` | 404 `SESSION_NOT_FOUND`

#### 5. `DELETE /sessions/{session_id}`
- **Summary**: Delete Studio Session (Admin)
- **Path Parameters**: `session_id` (string); **Query Parameters**: `force` (boolean, default: false)
- **Responses**: 204 No Content | 400 `SYNTAX_INVALID_ARGUMENT` | 401 `UNAUTHENTICATED` | 403 `FORBIDDEN_ADMIN_REQUIRED` | 404 `SESSION_NOT_FOUND` | 422 `CANNOT_DELETE_PROMOTED_SESSION`

#### 6. `POST /sessions/{session_id}/sources`
- **Summary**: Upload Source Document to CAS
- **Path Parameters**: `session_id` (string)
- **Request Body**: `multipart/form-data` (`file`, `do_not_send_to_agent`, `description`)
- **Responses**: 201 `SourceUploadResponse` | 400 `INVALID_SESSION_IDENTIFIER` | 401 `UNAUTHENTICATED` | 404 `SESSION_NOT_FOUND` | 413 `PAYLOAD_TOO_LARGE` | 422 `CORRUPT_SOURCE_BLOB`

#### 7. `DELETE /sessions/{session_id}/sources/{source_id}`
- **Summary**: Remove Source Document (Tombstone in Manifest)
- **Path Parameters**: `session_id` (string), `source_id` (string)
- **Responses**: 204 No Content | 401 `UNAUTHENTICATED` | 404 `SOURCE_NOT_FOUND` | 422 `SOURCE_REFERENCED_BY_COMMITTED_VERSION`, `SOURCE_REFERENCED_BY_PENDING_PROPOSAL`

#### 8. `POST /sessions/{session_id}/parse`
- **Summary**: Trigger Asynchronous Ingestion & Layout Parsing Job
- **Path Parameters**: `session_id` (string)
- **Request Body**: `ParseRequest` (`{ source_ids?: list[string], ocr_mode?: "auto"|"force"|"off" }`)
- **Responses**: 202 `JobHandleDTO` | 401 `UNAUTHENTICATED` | 404 `SESSION_NOT_FOUND`, `SOURCE_NOT_FOUND` | 501 `OCR_UNAVAILABLE`, `OFFICE_CONVERSION_UNAVAILABLE` (synchronous 501 when `ocr_mode="force"` and required binary is missing; otherwise emitted as terminal async job event)

#### 9. `POST /sessions/{session_id}/clarify`
- **Summary**: Trigger Clarification Question Generation Job
- **Path Parameters**: `session_id` (string)
- **Request Body**: `ClarifyRequest` (`{ focus_concepts: [...], max_questions: 10 }`)
- **Responses**: 202 `JobHandleDTO` | 401 `UNAUTHENTICATED` | 404 `SESSION_NOT_FOUND` | 503 `AGENT_UNAVAILABLE`

#### 10. `POST /sessions/{session_id}/answers`
- **Summary**: Submit Clarification Answers
- **Path Parameters**: `session_id` (string)
- **Request Body**: `ClarificationAnswerRequest`
- **Responses**: 200 `ClarificationAnswerResponse` (`{ saved_count, answers_sha256 }`) | 400 `SYNTAX_INVALID_ARGUMENT` | 401 `UNAUTHENTICATED` | 404 `SESSION_NOT_FOUND`, `QUESTION_NOT_FOUND`

#### 11. `POST /sessions/{session_id}/construct`
- **Summary**: Trigger Ontology Construction Job
- **Path Parameters**: `session_id` (string); **Headers**: `If-Match: "<head_sha256>"` (if auto-commit)
- **Request Body**: `ConstructRequest`
- **Responses**: 202 `JobHandleDTO` | 400 `MISSING_AUTO_COMMIT_PRECONDITION` | 401 `UNAUTHENTICATED` | 404 `SESSION_NOT_FOUND` | 409 `LOCK_TIMEOUT` | 412 `ONT_EVO_CONCURRENT_MODIFICATION` | 503 `AGENT_UNAVAILABLE`

#### 12. `POST /sessions/{session_id}/delta`
- **Summary**: Apply Direct Delta Operations (Additive or Steward Revision)
- **Path Parameters**: `session_id` (string); **Headers**: `If-Match: "<head_sha256>"` (Required)
- **Request Body**: `OntologyDeltaRequest` / `StewardRevisionRequest` (accepts optional `low_confidence_waiver?: bool`, `grounding_rationale?: string` for admin waivers)
- **Responses**: 200 `DeltaCommitResponse` | 400 `SYNTAX_INVALID_ARGUMENT`, `VALIDATION_AMBIGUOUS_TARGET` | 401 `UNAUTHENTICATED` | 403 `AGENT_OPERATION_FORBIDDEN`, `FORBIDDEN_ADMIN_REQUIRED` | 404 `SESSION_NOT_FOUND` | 409 `LOCK_TIMEOUT`, `JOB_COMMIT_IN_PROGRESS` | 412 `ONT_EVO_CONCURRENT_MODIFICATION` | 422 `ONT_EVO_CONTRACT_VIOLATION`, `ONT_EVO_ENVELOPE_VIOLATION`, `ONT_EVO_UNGROUNDED_CONCEPT`, `ONT_EVO_GROUNDING_VIOLATION`, `INVALID_GROUNDING_WAIVER`, `HIERARCHICAL_CYCLE_DETECTED`, `PHYSICAL_TABLE_NAME_COLLISION`, `DUPLICATE_SYNONYM`, `DUPLICATE_ATTRIBUTE_NAME`

#### 13. `GET /sessions/{session_id}/proposals`
- **Summary**: List Candidate Proposals
- **Path Parameters**: `session_id` (string)
- **Responses**: 200 `list[ProposalRecord]` | 401 `UNAUTHENTICATED` | 404 `SESSION_NOT_FOUND`

#### 14. `GET /sessions/{session_id}/proposals/{proposal_id}`
- **Summary**: Get Candidate Proposal Details (`proposal show`)
- **Path Parameters**: `session_id`, `proposal_id`
- **Responses**: 200 `ProposalRecord` | 401 `UNAUTHENTICATED` | 404 `SESSION_NOT_FOUND`, `PROPOSAL_NOT_FOUND`

#### 15. `POST /sessions/{session_id}/proposals`
- **Summary**: Submit Candidate Proposal
- **Path Parameters**: `session_id` (string)
- **Request Body**: `ProposalCreateRequest`
- **Responses**: 201 `ProposalRecord` | 400 `SYNTAX_INVALID_ARGUMENT` | 401 `UNAUTHENTICATED` | 403 `AGENT_DESTRUCTIVE_OP_FORBIDDEN` | 404 `SESSION_NOT_FOUND` | 409 `LOCK_TIMEOUT` | 422 `ONT_EVO_CONTRACT_VIOLATION`, `ONT_EVO_ENVELOPE_VIOLATION`

#### 16. `POST /sessions/{session_id}/proposals/{proposal_id}/accept`
- **Summary**: Accept Pending Proposal & Commit Version Snapshot
- **Path Parameters**: `session_id`, `proposal_id`; **Headers**: `If-Match: "<head_sha256>"` (Required)
- **Request Body**: `ProposalAcceptRequest` (`{ base_sha256, allow_self_acceptance, low_confidence_waiver, grounding_rationale }`)
- **Authorization & Waivers**: Standard accept requires `editor`. `--low-confidence-waiver` and `--allow-self-accepted-proposals` strictly require `admin` role (`FORBIDDEN_ADMIN_REQUIRED`, exit 7). Automated actors strictly forbidden from accepting proposals (`AGENT_PROPOSAL_ACCEPT_FORBIDDEN`, exit 7).
- **Responses**: 200 `DeltaCommitResponse` | 400 `SYNTAX_INVALID_ARGUMENT` | 401 `UNAUTHENTICATED` | 403 `FORBIDDEN_ADMIN_REQUIRED`, `PROPOSAL_SELF_ACCEPTANCE_FORBIDDEN`, `AGENT_PROPOSAL_ACCEPT_FORBIDDEN` | 404 `SESSION_NOT_FOUND`, `PROPOSAL_NOT_FOUND` | 409 `PROPOSAL_ALREADY_RESOLVED`, `LOCK_TIMEOUT` | 412 `ONT_EVO_CONCURRENT_MODIFICATION` | 422 `ONT_EVO_UNGROUNDED_CONCEPT`, `ONT_EVO_GROUNDING_VIOLATION`, `HIERARCHICAL_CYCLE_DETECTED`, `ONT_EVO_CONTRACT_VIOLATION`

#### 17. `POST /sessions/{session_id}/proposals/{proposal_id}/reject`
- **Summary**: Reject Pending Proposal
- **Path Parameters**: `session_id`, `proposal_id`
- **Request Body**: `ProposalRejectRequest` (`{ reason?: string }`)
- **Responses**: 200 `ProposalRecord` (state: `REJECTED`) | 400 `SYNTAX_INVALID_ARGUMENT` | 401 `UNAUTHENTICATED` | 403 `AGENT_OPERATION_FORBIDDEN` | 404 `SESSION_NOT_FOUND`, `PROPOSAL_NOT_FOUND` | 409 `PROPOSAL_ALREADY_RESOLVED`, `LOCK_TIMEOUT`

#### 18. `POST /sessions/{session_id}/proposals/{proposal_id}/rebase`
- **Summary**: Rebase Pending Proposal Across Concurrent Head Commits
- **Path Parameters**: `session_id`, `proposal_id`; **Headers**: `If-Match: "<head_sha256>"` (Required; supplied via `--base-sha256` on CLI)
- **Request Body**: `ProposalRebaseRequest` (`{ base_sha256: string }`)
- **Responses**: 200 `ProposalRecord` (rebased) | 400 `SYNTAX_INVALID_ARGUMENT` | 401 `UNAUTHENTICATED` | 403 `AGENT_OPERATION_FORBIDDEN` | 404 `SESSION_NOT_FOUND`, `PROPOSAL_NOT_FOUND` | 409 `PROPOSAL_ALREADY_RESOLVED`, `LOCK_TIMEOUT` | 412 `ONT_EVO_CONCURRENT_MODIFICATION` | 422 `HIERARCHICAL_CYCLE_DETECTED`, `ONT_EVO_CONTRACT_VIOLATION`, `DUPLICATE_ATTRIBUTE_NAME`

#### 19. `GET /sessions/{session_id}/jobs/{job_id}`
- **Summary**: Get Background Job Status (Polled by `--wait` and Web Workbench)
- **Path Parameters**: `session_id`, `job_id`
- **Responses**: 200 `JobStatusDTO` | 401 `UNAUTHENTICATED` | 404 `JOB_NOT_FOUND`

#### 20. `GET /sessions/{session_id}/jobs/{job_id}/events`
- **Summary**: Stream Job Execution Events (SSE)
- **Headers**: `Accept: text/event-stream`, `Last-Event-ID: <cursor>` (Non-negative integer)
- **Responses**: 200 `text/event-stream` | 400 `SYNTAX_INVALID_ARGUMENT` (malformed `Last-Event-ID`) | 401 `UNAUTHENTICATED` | 404 `JOB_NOT_FOUND`

#### 21. `GET /sessions/{session_id}/export`
- **Summary**: Export Ontology Representation
- **Query Parameters**: `export_format` (`yaml` / `json` / `turtle` / `jsonld`), `version` (optional)
- **Responses**: 200 stream (`application/yaml`, `application/json`, `text/turtle`, `application/ld+json`) | 400 `UNSUPPORTED_FORMAT` | 401 `UNAUTHENTICATED` | 404 `VERSION_NOT_FOUND`, `SESSION_NOT_FOUND`

#### 22. `POST /sessions/{session_id}/promote`
- **Summary**: Promote Head Ontology to Workspace Data Vault Pipeline (Admin)
- **Path Parameters**: `session_id`; **Headers**: `If-Match: "<head_sha256>"`, `X-OpenDV-Workspace-If-Match: "<ws_sha256>"`
- **Request Body**: `PromoteRequest` (`{ workspace, workspace_sha256, head_sha256, rationale, acknowledge_historical_contract_warning?: boolean }`)
- **OCC Precedence Rule**: Header and body parameters must agree identically; disagreement triggers 400 `SYNTAX_INVALID_ARGUMENT`.
- **Responses**: 200 `PromoteResponse` | 400 `PROMOTION_RATIONALE_REQUIRED`, `INVALID_WORKSPACE_PATH`, `SYNTAX_INVALID_ARGUMENT` | 401 `UNAUTHENTICATED` | 403 `FORBIDDEN_ADMIN_REQUIRED` | 404 `SESSION_NOT_FOUND` | 409 `LOCK_TIMEOUT` | 412 `LINEAGE_ROOT_MISMATCH`, `ONT_EVO_CONCURRENT_MODIFICATION` | 422 `ONT_EVO_CONTRACT_VIOLATION`, `ONT_EVO_UNGROUNDED_CONCEPT`, `ONT_EVO_GROUNDING_VIOLATION`

#### 23. `POST /sessions/{session_id}/bundle`
- **Summary**: Export Portable Session Archive (`.odvstudio.zip`)
- **Path Parameters**: `session_id`
- **Request Body**: `BundleExportRequest` (`{ acknowledge_confidential_source_export, export_rationale }`)
- **Responses**: 200 `application/zip` stream | 400 `EXPORT_RATIONALE_REQUIRED` (missing rationale), `SYNTAX_INVALID_ARGUMENT` | 401 `UNAUTHENTICATED` | 403 `FORBIDDEN_ADMIN_REQUIRED` | 404 `SESSION_NOT_FOUND` | 422 `CONFIDENTIAL_SOURCE_EXPORT_UNACKNOWLEDGED`

#### 24. `POST /sessions/bundle/import`
- **Summary**: Import and Decompress Portable Session Archive (`.odvstudio.zip`)
- **Request Body**: `multipart/form-data` (`file`, `acknowledge_unverified_import_attribution`, `acknowledgement_rationale`)
- **Responses**: 201 `StudioSessionDTO` | 400 `IMPORT_GROUNDING_RATIONALE_REQUIRED`, `SYNTAX_INVALID_ARGUMENT` | 401 `UNAUTHENTICATED` | 403 `FORBIDDEN_ADMIN_REQUIRED` | 409 `SESSION_ALREADY_EXISTS` | 422 `ARCHIVE_SECURITY_VIOLATION`, `IMPORT_DELTA_REPLAY_HASH_MISMATCH`, `IMPORT_GROUNDING_VIOLATION`

#### 25. `POST /sessions/{session_id}/bundle/clone`
- **Summary**: Clone Studio Session Bundle into Isolated Session
- **Path Parameters**: `session_id`
- **Request Body**: `CloneSessionRequest` (`{ session_label?: string, new_session_id?: string }`)
- **Responses**: 201 `StudioSessionDTO` | 400 `INVALID_SESSION_IDENTIFIER`, `SYNTAX_INVALID_ARGUMENT` | 401 `UNAUTHENTICATED` | 404 `SESSION_NOT_FOUND` | 409 `SESSION_ALREADY_EXISTS`, `LOCK_TIMEOUT`

#### 26. `POST /auth/launch-nonce`
- **Summary**: Mint Single-Use Launch Nonce (Loopback / Authenticated)
- **Headers**: `Authorization: Bearer <local_token>`
- **Responses**: 200 `LaunchNonceResponse` | 401 `UNAUTHENTICATED` | 403 `LOOPBACK_PRIVILEGE_DISABLED_IN_REMOTE_MODE`

#### 27. `POST /auth/token`
- **Summary**: Exchange Single-Use Launch Nonce for HttpOnly Session Cookie
- **Request Body**: `NonceExchangeRequest` (`{ nonce: string }`)
- **Responses**: 200 `TokenExchangeResponse` | 400 `SYNTAX_INVALID_ARGUMENT` | 401 `TOKEN_EXPIRED` | 403 `LOOPBACK_PRIVILEGE_DISABLED_IN_REMOTE_MODE`

---

## 4. Asynchronous SSE Event Streaming Specification

### 4.1 Wire Protocol & Framing
Background asynchronous jobs stream diagnostic telemetry and state progress via W3C Server-Sent Events (`text/event-stream`):

```http
HTTP/1.1 200 OK
Content-Type: text/event-stream; Cache-Control: no-cache; Connection: keep-alive; X-Accel-Buffering: no

id: 1
event: job_started
data: {"job_id":"job-8812","session_id":"s1","job_type":"construct"}

: keepalive

id: 2
event: progress_update
data: {"job_id":"job-8812","stage":"fibo_grounding","pct_complete":45}

id: 3
event: job_completed
data: {"job_id":"job-8812","status":"COMPLETED","proposal_id":"prop-9912"}
```

- **Keepalive Ping**: The server transmits an SSE comment `: keepalive\n\n` every 15 seconds to prevent intermediate reverse proxies or cloud load balancers from terminating idle connections.
- **Journal File**: Events are concurrently appended to `<session_dir>/jobs/<job_id>.events.jsonl` under `.journal_<job_id>.lock`.

### 4.2 Reconnection & Cursor Replay (`Last-Event-ID`)
When a client connection drops due to network interruption:
1. The client reconnects, supplying the header `Last-Event-ID: <id>`.
2. The server strictly validates `Last-Event-ID` against non-negative integer pattern `^[0-9]+$`. If malformed or invalid, the server strictly rejects the request with HTTP 400 `SYNTAX_INVALID_ARGUMENT` (exit code 2), guaranteeing deterministic client behavior across all implementations.
3. Under shared lock $\mathbf{L}_3$ (blocking job writer appends), the server: (a) registers the client as a live subscriber with a bounded in-memory queue, and (b) snapshots journal replay records where `event_id > parsed_cursor` into an in-memory buffer (or temp file for $> 10\text{ MB}$).
4. The server releases shared $\mathbf{L}_3$ before transmitting data over the network socket, preventing slow clients from starving the background writer.
5. The server streams buffered replay events in monotonic order, then drains the live queue, dropping any events with `event_id <= last_replayed_id`. If the job was already terminal when the snapshot was taken, the stream closes cleanly after replay.
6. If the job completed while the client was disconnected and the journal exceeds $50.0\text{ MB}$, the file is compressed to `<job_id>.events.jsonl.gz`: compression acquires exclusive journal lock $\mathbf{L}_3$, waiting for active shared snapshot readers to finish, writes the `.gz` sidecar atomically, and unlinks the raw file; subsequent replay requests stream from the compressed archive.

---

## 5. Dual-CLI Reference Specification (`opendv` & `opendvctl`)

### 5.1 Architecture Parity Contract
The CLI interfaces offer identical ergonomics and flag structures:
- `opendv ontology studio <cmd>`: In-process execution via `LocalExecutor`. Directly imports `opendv-service` and operates over local CAS files without network overhead. For background jobs (`parse`, `clarify`, `construct`), `LocalExecutor` executes synchronously to completion (implicit `--wait`) within the host process, preventing orphaned jobs.
- `opendvctl ontology studio <cmd>`: Bun platform client via `RemoteExecutor`. Sends REST and SSE requests to the Platform API service (`--api-url`), returning `202 Accepted` unless `--wait` or `--stream` is passed.
- **Exit Code Derivation Rule**: Both `LocalExecutor` and `RemoteExecutor` derive command exit codes strictly from the RFC 9457 ProblemDetail `code` token mapped through `schemas/v1/error-codes.json`, never from HTTP status codes alone. For instance, HTTP 400 responses map to exit code 1 when the error code is `PROMOTION_RATIONALE_REQUIRED` and to exit code 2 when the error code is `SYNTAX_INVALID_ARGUMENT`.

#### Dual-CLI & REST Capability Parity Matrix
The operational CLI subcommands in `schemas/v1/cli-spec.json` map directly to corresponding OpenAPI operations in `schemas/v1/openapi.json`. Transport and internal endpoints (`POST /auth/launch-nonce`, `POST /auth/token`, internal `POST .../proposals`, `GET .../jobs/{id}`, `GET .../jobs/{id}/events`) are transport/streaming interfaces consumed by Web Workbench, Agent Harness, and CLI `--wait`/`--stream`, while administrative REST deletions (`DELETE /sessions/{id}`, `DELETE .../sources/{id}`) and CAS GC operate via direct platform API:

| CLI Command | HTTP Route | RBAC | Canonical Exit Codes |
|---|---|---|---|
| `init` | `POST /init` | `loopback/owner` | 0, 1, 6, 7 |
| `create` | `POST /sessions` | `editor` | 0, 1, 2, 6, 7 |
| `list` | `GET /sessions` | `viewer` | 0, 7 |
| `status` | `GET /sessions/{id}` | `viewer` | 0, 2, 3, 7 |
| `upload` | `POST /sessions/{id}/sources` | `editor` | 0, 2, 3, 4, 7 |
| `parse` | `POST /sessions/{id}/parse` | `editor` | 0, 1, 3, 7 |
| `clarify` | `POST /sessions/{id}/clarify` | `editor` | 0, 1, 3, 7 |
| `answer` | `POST /sessions/{id}/answers` | `editor` | 0, 2, 3, 7 |
| `construct` | `POST /sessions/{id}/construct` | `editor` | 0, 1, 2, 3, 4, 5, 6, 7 |
| `delta apply` | `POST /sessions/{id}/delta` | `editor` / `admin`* | 0, 1, 2, 3, 4, 5, 6, 7 |
| `proposal list` | `GET /sessions/{id}/proposals` | `viewer` | 0, 3, 7 |
| `proposal show` | `GET /sessions/{id}/proposals/{pid}` | `viewer` | 0, 3, 7 |
| `proposal accept` | `POST /sessions/{id}/proposals/{pid}/accept` | `editor` / `admin`* | 0, 2, 3, 4, 5, 6, 7 |
| `proposal reject` | `POST /sessions/{id}/proposals/{pid}/reject` | `editor` | 0, 2, 3, 6, 7 |
| `proposal rebase` | `POST /sessions/{id}/proposals/{pid}/rebase` | `editor` | 0, 2, 3, 4, 5, 6, 7 |
| `export` | `GET /sessions/{id}/export` | `viewer` | 0, 2, 3, 7 |
| `promote` | `POST /sessions/{id}/promote` | `admin` | 0, 1, 2, 3, 4, 5, 6, 7 |
| `bundle export` | `POST /sessions/{id}/bundle` | `editor` / `admin`* | 0, 1, 3, 4, 7 |
| `bundle import` | `POST /sessions/bundle/import` | `editor` / `admin`* | 0, 1, 2, 3, 4, 6, 7 |
| `bundle clone` | `POST /sessions/{id}/bundle/clone` | `editor` | 0, 2, 3, 6, 7 |

*\* RBAC Rules: `delta apply` requires `admin` for destructive steward revisions. `proposal accept` requires `admin` when invoked with `--low-confidence-waiver` or `--allow-self-accepted-proposals`. `bundle export` and `bundle import` require `admin` when acknowledging confidential export or unverified import attribution.*


### 5.2 Canonical Exit Code Registry (0..7)
All commands in both CLIs map their termination status strictly to one of eight canonical exit codes. `schemas/v1/error-codes.json` is the sole authoritative registry:

| Exit Code | Classification | Trigger Conditions | Corresponding HTTP Statuses |
|---|---|---|---|
| **0** | Success | Command completed successfully; output emitted. | `200 OK`, `201 Created`, `202 Accepted`, `204 No Content` |
| **1** | Capability / System Error | Missing required rationale ($\ge 10$ chars), zero-op delta, uninstalled system capability (OCR, LibreOffice, Agent LLM), or internal crash. | `500 Internal Server Error`, `501 Not Implemented`, `503 Service Unavailable`, `400 Bad Request` |
| **2** | Syntax / Precondition Error | Invalid CLI flag, missing mandatory arguments, URI format violation, malformed offset ordering (start >= end), payload too large, or workspace boundary violation. | `400 Bad Request`, `413 Payload Too Large`, `415 Unsupported Media Type` |
| **3** | Resource Not Found | Specified session ID, version string, proposal ID, source ID, or question ID does not exist. | `404 Not Found` |
| **4** | Domain & Governance Violation | ONT-EVO-001 deployed table conflict, ONT-EVO-002 ungrounded concept, duplicate synonym/attribute, archive security violation, or delta replay hash mismatch. | `422 Unprocessable Content` |
| **5** | Concurrency Conflict (OCC) | Session head SHA-256 or workspace SHA-256 mismatch during commit, accept, rebase, or promotion. | `412 Precondition Failed` |
| **6** | Lock Timeout / In Progress | Advisory file lock acquisition timed out (30s) or operating on an already resolved proposal. | `409 Conflict` |
| **7** | Authentication / RBAC Denied | Missing/invalid token, non-admin invoking destructive operation, agent attempting destructive mutation, or unapproved self-acceptance. | `401 Unauthorized`, `403 Forbidden` |

### 5.3 Complete Command Inventory (`opendv ontology studio`)

#### 1. `init`
- **Synopsis**: `opendv ontology studio init [--force]` (RBAC: `loopback/owner`; `--force` requires `Authorization: Bearer <token>` over REST or local process UID/SID match in CLI)
- **Exit Codes**: 0 (OK), 1 (Permissions failure), 6 (`STUDIO_ALREADY_INITIALIZED`), 7 (`LOOPBACK_PRIVILEGE_DISABLED_IN_REMOTE_MODE`, `UNAUTHENTICATED`)

#### 2. `create`
- **Synopsis**: `opendv ontology studio create [-s <id>] [-n <ns>] [-o <owner>] [--from-workspace <ws>] [-d <desc>]` (RBAC: `editor`)
- **Key Constraints**: `-s` regex `^[a-z0-9_-]{1,64}$`. Seeding via `--from-workspace` loads contract and anchors baseline SHA.
- **Exit Codes**: 0 (OK), 1 (`CONFLICTING_WORKSPACE_SEED_PARAMS`), 2 (`INVALID_WORKSPACE_PATH`), 6 (`SESSION_ALREADY_EXISTS`), 7 (`UNAUTHENTICATED`)

#### 3. `list`
- **Synopsis**: `opendv ontology studio list [--limit <int>] [--offset <int>] [--status <str>]` (RBAC: `viewer`)
- **Exit Codes**: 0 (OK), 7 (`UNAUTHENTICATED`)

#### 4. `status`
- **Synopsis**: `opendv ontology studio status -s <session_id>` (RBAC: `viewer`)
- **Exit Codes**: 0 (OK), 2 (`INVALID_SESSION_IDENTIFIER`), 3 (`SESSION_NOT_FOUND`), 7 (`UNAUTHENTICATED`)

#### 5. `upload`
- **Synopsis**: `opendv ontology studio upload -s <session_id> -f <file> [--confidential] [--description <str>]` (RBAC: `editor`)
- **Key Constraints**: `--confidential` sets `do_not_send_to_agent=True` and taints derived concepts in CAS.
- **Exit Codes**: 0 (OK), 2 (`INVALID_SESSION_IDENTIFIER`, `PAYLOAD_TOO_LARGE`), 3 (`SESSION_NOT_FOUND`), 4 (`CORRUPT_SOURCE_BLOB`), 7 (`UNAUTHENTICATED`)

#### 6. `parse`
- **Synopsis**: `opendv ontology studio parse -s <session_id> [--source-ids <id1,id2...>] [--ocr-mode <auto|force|off>] [--wait] [--stream]` (RBAC: `editor`)
- **Semantics**: Synchronous under `LocalExecutor`; async returning `JobHandleDTO` under `RemoteExecutor` unless `--wait`/`--stream`.
- **Exit Codes**: 0 (OK), 1 (`OCR_UNAVAILABLE`, `OFFICE_CONVERSION_UNAVAILABLE`), 2 (`SYNTAX_INVALID_ARGUMENT`), 3 (`SESSION_NOT_FOUND`), 7 (`UNAUTHENTICATED`)

#### 7. `clarify`
- **Synopsis**: `opendv ontology studio clarify -s <session_id> [--max-questions <int>] [--wait] [--stream]` (RBAC: `editor`)
- **Semantics**: Synchronous under `LocalExecutor`; async under `RemoteExecutor` unless `--wait`/`--stream`.
- **Exit Codes**: 0 (OK), 1 (`AGENT_UNAVAILABLE`), 3 (`SESSION_NOT_FOUND`), 7 (`UNAUTHENTICATED`)

#### 8. `answer`
- **Synopsis**: `opendv ontology studio answer -s <session_id> -q <question_id> [-o <opt>...] [-t <text>]` (RBAC: `editor`)
- **Exit Codes**: 0 (OK), 2 (`SYNTAX_INVALID_ARGUMENT`), 3 (`QUESTION_NOT_FOUND`), 7 (`UNAUTHENTICATED`)

#### 9. `construct`
- **Synopsis**: `opendv ontology studio construct -s <session_id> [--mode <auto|agent|deterministic>] [--auto-commit] [--base-version <v>] [--base-sha256 <s>] [--wait] [--stream]` (RBAC: `editor`)
- **Semantics**: `--auto-commit` is permitted strictly in `--mode deterministic` with `--base-version` and `--base-sha256`; otherwise exit code 2.
- **Exit Codes**: 0 (OK), 1 (`AGENT_UNAVAILABLE`), 2 (`MISSING_AUTO_COMMIT_PRECONDITION`), 3 (`SESSION_NOT_FOUND`), 4 (`PHYSICAL_TABLE_NAME_COLLISION`, `ONT_EVO_CONTRACT_VIOLATION`), 5 (`ONT_EVO_CONCURRENT_MODIFICATION`), 6 (`LOCK_TIMEOUT`), 7 (`UNAUTHENTICATED`)

#### 10. `delta apply`
- **Synopsis**: `opendv ontology studio delta apply -s <session_id> -f <delta_file> --base-sha256 <s> [--rationale <str>] [--low-confidence-waiver] [--grounding-rationale <str>]` (RBAC: `editor` / `admin` for steward revisions or waivers)
- **Exit Codes**: 0 (OK), 1 (Missing rationale), 2 (Syntax), 3 (`SESSION_NOT_FOUND`), 4 (`ONT_EVO_CONTRACT_VIOLATION`, `ONT_EVO_ENVELOPE_VIOLATION`, `ONT_EVO_UNGROUNDED_CONCEPT`, `ONT_EVO_GROUNDING_VIOLATION`, `INVALID_GROUNDING_WAIVER`), 5 (`ONT_EVO_CONCURRENT_MODIFICATION`), 6 (`LOCK_TIMEOUT`, `JOB_COMMIT_IN_PROGRESS`), 7 (`FORBIDDEN_ADMIN_REQUIRED`, `AGENT_OPERATION_FORBIDDEN`)

#### 11. `proposal list / show / accept / reject / rebase`
- **Synopsis**:
  - `opendv ontology studio proposal list -s <session_id>` (RBAC: `viewer`)
  - `opendv ontology studio proposal show -s <session_id> -p <proposal_id>` (RBAC: `viewer`)
  - `opendv ontology studio proposal accept -s <session_id> -p <proposal_id> --base-sha256 <s> [--allow-self-accepted-proposals] [--low-confidence-waiver] [--grounding-rationale <str>]` (RBAC: `editor` / `admin` for waivers/self-accept)
  - `opendv ontology studio proposal reject -s <session_id> -p <proposal_id> [-r <reason>]` (RBAC: `editor`)
  - `opendv ontology studio proposal rebase -s <session_id> -p <proposal_id> --base-sha256 <s>` (RBAC: `editor`)
- **RBAC & Waiver Rules**: Standard proposal accept requires `editor`. Flags `--low-confidence-waiver` and `--allow-self-accepted-proposals` strictly require `admin` role (`FORBIDDEN_ADMIN_REQUIRED`, exit 7); automated actors forbidden from issuing waivers or accepting proposals (`AGENT_PROPOSAL_ACCEPT_FORBIDDEN`, exit 7).
- **Exit Codes**: 0 (OK), 2 (Syntax), 3 (Not found), 4 (`ONT_EVO_UNGROUNDED_CONCEPT`, `ONT_EVO_GROUNDING_VIOLATION`, cycle), 5 (`ONT_EVO_CONCURRENT_MODIFICATION`), 6 (`PROPOSAL_ALREADY_RESOLVED`), 7 (`FORBIDDEN_ADMIN_REQUIRED`, `PROPOSAL_SELF_ACCEPTANCE_FORBIDDEN`, `AGENT_PROPOSAL_ACCEPT_FORBIDDEN`)

#### 12. `export`
- **Synopsis**: `opendv ontology studio export -s <session_id> [-e <yaml|json|turtle|jsonld>] [-v <version>] [-o <out_file>]` (RBAC: `viewer`)
- **Exit Codes**: 0 (OK), 2 (`UNSUPPORTED_FORMAT`), 3 (`VERSION_NOT_FOUND`), 7 (`UNAUTHENTICATED`)

#### 13. `promote`
- **Synopsis**: `opendv ontology studio promote -s <session_id> [-w <workspace>] --workspace-sha256 <s> --head-sha256 <s> -r <rationale> [--acknowledge-historical-contract-warning]` (RBAC: `admin`)
- **Key Constraints**: Rationale must be $\ge 10$ characters. OCC requires exact match of workspace and head SHA.
- **Exit Codes**: 0 (OK), 1 (`PROMOTION_RATIONALE_REQUIRED`), 2 (`INVALID_WORKSPACE_PATH`), 3 (`SESSION_NOT_FOUND`), 4 (`ONT_EVO_CONTRACT_VIOLATION`, `ONT_EVO_UNGROUNDED_CONCEPT`, `ONT_EVO_GROUNDING_VIOLATION`), 5 (`LINEAGE_ROOT_MISMATCH`, `ONT_EVO_CONCURRENT_MODIFICATION`), 6 (`LOCK_TIMEOUT`), 7 (`FORBIDDEN_ADMIN_REQUIRED`)

#### 14. `bundle export / import / clone`
- **Synopsis**:
  - `opendv ontology studio bundle export -s <session_id> -o <out_zip> [--acknowledge-confidential-source-export] [--export-rationale <str>]` (RBAC: `editor` / `admin` with acknowledgment)
  - `opendv ontology studio bundle import -f <in_zip> [--acknowledge-unverified-import-attribution] [--acknowledgement-rationale <str>]` (RBAC: `editor` / `admin` with acknowledgment)
  - `opendv ontology studio bundle clone -s <session_id> [--new-session-id <id>] [-l <label>]` (RBAC: `editor`)
- **RBAC Rules**: `bundle export` and `bundle import` require `admin` when acknowledging confidential export or unverified import attribution (`FORBIDDEN_ADMIN_REQUIRED`, exit 7). `bundle clone` requires `editor`.
- **Exit Codes**: 0 (OK), 1 (`EXPORT_RATIONALE_REQUIRED`, `IMPORT_GROUNDING_RATIONALE_REQUIRED`), 2 (`INVALID_SESSION_IDENTIFIER`, `SYNTAX_INVALID_ARGUMENT`), 3 (`SESSION_NOT_FOUND`), 4 (`ARCHIVE_SECURITY_VIOLATION`, `IMPORT_DELTA_REPLAY_HASH_MISMATCH`, `IMPORT_GROUNDING_VIOLATION`), 6 (`SESSION_ALREADY_EXISTS`, `LOCK_TIMEOUT`), 7 (`FORBIDDEN_ADMIN_REQUIRED`)

---

## 6. Web Workbench Studio UI Specification (`/ontology`)

### 6.1 Architecture & Single-Page Application Mounting
The visual Studio workbench is hosted inside `apps/opendv-web-workbench` at route `/ontology`:
- **Route Isolation**: Lazy-loaded in `src/url-router.ts` to prevent bundling overhead on core pipeline pages.
- **State Management**: Reactive Zustand session store synchronizing REST polling, SSE streaming events, and graph modifications.
- **Authentication**: In `local_standalone` mode, launch nonces are NEVER minted as an unauthenticated side effect of HTTP GET requests to `/ontology` (unauthenticated GET requests redirect to the auth gate and never receive a nonce). Launch nonces are minted strictly through authenticated or out-of-band channels: (1) CLI `init` (`opendv ontology studio init`), which outputs the launch URL with fragment (`http://localhost:8000/ontology#token=<nonce>`) to stdout; (2) at local platform startup (printed to stdout); or (3) via authenticated loopback request `POST /auth/launch-nonce` with `Authorization: Bearer <local_token>`. The 256-bit nonce has 60s TTL in server memory. Upon landing, the SPA exchanges this token via `POST /api/v1/ontology/studio/auth/token` for a secure `HttpOnly` session cookie (`SameSite=Strict`), immediately invalidating the nonce to reject replays. Landing page declares `Referrer-Policy: no-referrer`. In `remote_platform` mode, remote interaction is driven via `opendvctl` with Bearer tokens; browser SSO (OIDC/SAML) is scheduled for Milestone M4.

### 6.2 Component Hierarchy & Layout Structure

The Web Workbench organizes studio tooling into five persistent layout zones:
- **Header Bar**: Session Switcher, Active Version Badge, Elevation Pill, Save Status
- **Left Drawer (280px)**: Session Tree, Sources Tab, Questions Tab, History Tab, Settings
- **Center Canvas**: React Flow canvas with custom nodes, background `elkjs` layout, Level-of-Detail (Macro/Meso/Micro), and visual diff overlay
- **Right Context Drawer**: Node Inspector, Attribute Editor, Reference Mapping Search
- **Bottom Dock**: Diagnostic Console & W3C SSE Event Stream Log


### 6.3 Dedicated Workbench Panels

| Panel | Name | Key Features & Interactions |
|---|---|---|
| **A** | **Sources & Ingestion** | Drag-drop multi-file upload, file table (size, MIME, SHA-256, taint toggle, manifest tombstone), chunk inspector. |
| **B** | **Clarification Q&A** | Card stack of `@domain-clarifier` questions, option buttons with semantic tooltips, free-text rationale, debounce save. |
| **C** | **Graph Explorer Canvas** | React Flow SVG nodes, background `elkjs` layout ($<300\text{ms}$ / 150 nodes), 3-tier LOD zoom. |
| **D** | **Visual Ontology Editor** | Contextual drawer for concepts, definitions, synonyms, parent URIs, FIBO mappings, satellite columns. |
| **E** | **Visual Diff Overlay** | Color-coded version comparison between committed versions or pending proposals (green `+`, red `-`, amber `mod`). |
| **F** | **Quality Scorecard** | Real-time checks: deployed table contracts, grounding $\ge 0.6500$, DAG acyclicity, ratings to `feedback.jsonl`. |
| **G** | **Export & Promotion** | Multi-format export (YAML, JSON, Turtle, JSON-LD), workspace promotion modal with OCC hash verification and rationale $\ge 10$ chars. |

---

## 7. Multi-Format Export, Promotion & Portability Workflows

### 7.1 Multi-Format Semantic Serialization
The Studio exports versioned ontologies to four standardized formats via `opendv_service.ontology.export`:

1. **Data Vault Graph A YAML (`pe_graph_a_ontology.yaml` standard)**:
   Emits human-readable YAML conforming to the Open-DV Graph A schema, including conceptual concepts, relationships, enumerations, and metadata headers.
2. **RFC 8785 Canonical JSON**:
   Emits deterministically sorted, whitespace-normalized JSON matching the exact byte stream used for SHA-256 digest computation.
3. **W3C Turtle (`.ttl`) via `rdflib` 7.x** (declared as direct dependency of `opendv-service`):
   Emits formal RDF triples declaring OWL 2 DL ontologies:
   ```turtle
   @prefix dv2: <https://open-dv.dev/ontology/core#> .
   @prefix fibo: <https://spec.edmcouncil.org/fibo/ontology/FBC/FunctionalEntities/FinancialServicesEntities/> .
   @prefix owl: <http://www.w3.org/2002/07/owl#> .
   @prefix skos: <http://www.w3.org/2004/02/skos/core#> .

   <urn:opendv:concept:advisor> a owl:Class ; dv2:targetHub "hub_advisor" ; skos:exactMatch fibo:InvestmentAdvisor .
   ```
4. **W3C JSON-LD (`.jsonld`)**:
   Emits linked data JSON with `@context` defining vocabulary prefixes, supporting ingestion by external enterprise knowledge graphs.

### 7.2 Atomic Workspace Promotion Protocol
Promoting an ontology head version into a production workspace pipeline executes the following atomic sequence:

1. **Pre-Lock Workspace & Target Contract Containment Check**:
   - Resolve workspace directory via `Path.resolve(strict=False)` and verify containment within `OPENDV_WORKSPACE_ROOTS` using `os.path.normcase`: `try: os.path.commonpath([norm_root, norm_ws]) == norm_root except ValueError: False`. Any `ValueError` (cross-drive) or containment failure terminates with `INVALID_WORKSPACE_PATH` (HTTP 400, exit 2).
   - Resolve target contract filename (`target_contract_path`). It MUST be a single relative path segment matching `^[A-Za-z0-9_.-]+\.ya?ml$`, assert not a symlink (`os.path.islink()`), and verify resolution within workspace via `commonpath`. Traversal (`..`, `/`, `\`) or symlinks terminate with `INVALID_WORKSPACE_PATH` (HTTP 400, exit 2) before touching any lock.
2. **Lock Acquisition & Concurrency Guard**:
   - Acquire exclusive advisory file lock `<workspace>/graph_a.lock` [L4].
   - **L5 Lock Acquisition**: While holding L4, check `<workspace>/.promotion_intent.json`. If a foreign intent exists (`intent.session_id == s_intent != s_curr`), validate `intent.session_id` against `^[a-z0-9_-]{1,64}$` and reserved keywords (`bundle`, `init`, `sessions`, `health`, `auth`, `admin`), then acquire both session L5 locks in deterministic sorted order `min(s_curr, s_intent) -> max(s_curr, s_intent)` to prevent cross-session deadlocks. If no foreign intent exists (or `intent.session_id == s_curr`), acquire `L5(s_curr)` directly.
   - **Re-Verification & Intent Recovery Under Lock**: Re-verify workspace containment, contract filename regex, and symlink checks under the held locks. If `.promotion_intent.json` exists: attempt to parse JSON. If corrupted or unparseable (e.g. crash mid-write), move to `.quarantine/intent_<timestamp>.json`, record audit warning to `audit.jsonl`, and proceed. If valid: validate `intent.target_contract_path` against `^[A-Za-z0-9_.-]+\.ya?ml$`, assert not a symlink, and verify workspace containment before loading; if `compute_ontology_sha256(loaded_target_contract) == intent.head_sha256`, complete step 4c (update `session.json` under L5 recording promotion) and unlink intent; otherwise unlink stale intent without updating `session.json`. Log recovery action to `audit.jsonl` under L5. If foreign session L5 lock was acquired for recovery, release immediately. Continue holding current session L5 through steps 3–4d.
3. **Lineage Continuity & Governance Verification**:
   - Verify `compute_workspace_sha256(workspace, target_contract_path) == expected_workspace_sha256`. For first-time promotion of a greenfield session where no contract exists yet, expected workspace SHA is defined as sentinel `"0"*64` (`0000000000000000000000000000000000000000000000000000000000000000`).
   - Verify `session.active_version.head_sha256 == expected_head_sha256`.
   - Lineage Root Continuity: Re-derive `grandfathered_concept_uris` against the target workspace contract. Verify lineage root continuity between session root and workspace contract (trivially satisfied for greenfield sessions targeting a new contract). On mismatch, abort with `LINEAGE_ROOT_MISMATCH` (HTTP 412, exit code 5).
   - Verify ONT-EVO-001 deployed table compatibility against the authoritative deployment registry.
   - Verify ONT-EVO-002 positive grounding ($\ge 0.6500$ or administrative waiver).
4. **Two-Phase Atomic Commit**:
   - 4a. Atomically write `<workspace>/.promotion_intent.json` via temporary file in workspace (`.{target_contract_path}.intent.tmp`), flush with `fsync`, and atomic `os.replace`, recording `{ session_id, head_sha256, target_contract_path, timestamp }`.
   - 4b. Write canonical Graph A YAML (matching `versions/*/ontology.yaml`) to a secure workspace temporary file: opened on POSIX via `os.open(temp_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_NOFOLLOW", 0), 0o600, dir_fd=ws_fd)` (on Windows, `CreateFileW` with `CREATE_NEW` and assert not a symlink); assert `compute_ontology_sha256(yaml) == head_sha256`; execute atomic replacement via `os.replace` to `<workspace>/<target_contract_path>`.
   - 4c. Under `.session.lock`, update `session.json` recording `promoted_at`, `target_contract_path`, and `workspace_sha256`.
   - 4d. Unlink `<workspace>/.promotion_intent.json`.
5. **Lock Release**:
   - Release locks in reverse order (`.session.lock` [L5] then `graph_a.lock` [L4]).

### 7.3 Portable Studio Bundle Archive Format (`.odvstudio.zip`)
To support offline collaboration, air-gapped handoffs, and audit archiving, the Studio defines the portable `.odvstudio.zip` format:

#### Archive Directory Structure
The archive contains: `bundle.json` (archive manifest and schema version), `session.json` (session state), `taint_index.json` (confidential taint index), `audit.jsonl` (hash-chained audit ledger), `audit.checkpoint.*.json` (historical checkpoint archives, if rotated), `inputs/` (`manifest.json`, `cas/<sha256>`), `evidence/` (`chunks.jsonl`, `tables.jsonl`), `clarifications/` (`questions.json`, `answers.json`), `proposals/` (`<proposal_id>.json`), and `versions/vX.Y.Z/` (`ontology.json`, `delta.json`, `meta.json`). For workspace-seeded sessions, the seed root is serialized under `versions/<seed_version>/ontology.json` with `seed: true` in `meta.json`. Note that `ontology.yaml` is regenerated from canonical JSON on import or at promotion time. Active locks (`*.lock`) and HMAC secret (`.session_secret`) are strictly excluded.

#### Export Limits Pre-Check
Before creating a `.odvstudio.zip` bundle, the exporter scans session contents against import limits: max uncompressed size $\le 250.0\text{ MB}$ and max entries $\le 500$. Exceeding either limit terminates immediately with `ARCHIVE_SECURITY_VIOLATION` (HTTP 422, exit code 4), preventing generation of oversized bundles that import will reject.

#### Zero-Trust Streaming Import Verification
When importing a `.odvstudio.zip` bundle via `POST .../bundle/import` or `opendv ontology studio bundle import`:
1. **Streaming Security Validation**:
   The unpacker enforces strict limits on bytes actually decompressed during streaming: max uncompressed size $\le 250.0\text{ MB}$, max entries $\le 500$, max compression ratio $100:1$ (monitored per-chunk during streaming decompression on all entries $> 1.0\text{ MB}$ to thwart zip bombs before memory exhaustion).
   - Any entry containing relative path traversal (`..`), leading slashes, backslashes, or Windows drive prefixes (`C:`) triggers `ARCHIVE_SECURITY_VIOLATION` (HTTP 422, exit code 4).
   - Any symlink entry (detected via Unix mode bits in ZIP `external_attr` `0o120000`) or device file is rejected unconditionally.
   - Duplicate entry names or case-folding and Unicode NFC canonical collisions on all platforms (Windows, Linux, macOS) trigger immediate abortion.
   - Extraction takes place into a fresh isolated temporary directory before paths are validated a second time.
2. **Delta Replay Verification**:
   The import pipeline executes pure delta replay: starting from genesis `0.0.0` or seed root, it re-applies each version's `delta.json` via `apply(base, delta)`.
   - The resulting computed SHA-256 hash must match `meta.json` `target_sha256` bit-for-bit.
   - Any divergence aborts the import with `IMPORT_DELTA_REPLAY_HASH_MISMATCH` (HTTP 422, exit code 4).
3. **Grounding & Provenance Attestation**:
   The importer verifies all external mappings and waivers. Seed roots and `grandfathered_concept_uris` in imported archives are treated as unverified attribution. If the imported session is promoted to a workspace, the grandfathered concept set is re-derived against the target workspace's existing contract rather than blindly trusting the bundle. Unacknowledged waivers require administrative elevation (`--acknowledge-unverified-import-attribution`); otherwise, the import rejects with `IMPORT_GROUNDING_VIOLATION` (exit 4).
4. **Taint Index & Audit Chain Verification**:
   The importer verifies that `audit.checkpoint.*.json` and `audit.jsonl` entries form a cryptographically unbroken hash chain. Checkpoints cover historical commit anchors and their Merkle root, verifying from genesis forward to `checkpoint_root_sha256` through active `audit.jsonl`. This verifies internal cryptographic integrity and replay consistency, but does not prove external provenance authenticity because `.session_secret` is generated locally and never exported. Every version commit anchor (`meta.json.audit_chain_head_sha256`) must be present in the active chain or covered by a verified checkpoint. Effective taint is computed as $T_{\text{effective}} = T_{\text{manifest}} \cup T_{\text{imported}}$. The session secret (`.session_secret`) is NEVER exported; a fresh secret is generated locally upon import, invalidating imported ephemeral HMAC pseudonyms and prompt caches. Imported session IDs are re-validated against `^[a-z0-9_-]{1,64}$` and reserved route keywords (`bundle`, `init`, `sessions`, `health`, `auth`, `admin`).
