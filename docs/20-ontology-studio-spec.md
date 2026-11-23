# 20 — Open-DV Ontology Construction Studio: Functional Specification & Interface Reference

> [!WARNING]
> **OPEN-DV V2 CLEAN-SHEET — SUPERSEDED / HISTORICAL (2026-11-21):** V1 Studio spec (`--mode deterministic` Journey 3, `/parse`, `.odvstudio.zip`, `promote`) is superseded by clean-sheet V2 Project-first API/CLI and `docs/31`. See [`docs/designs/SITEMAP.md`](designs/SITEMAP.md).

**Status:** Normative Functional Specification (Updated for Unified Phases 1–4)  
**Target Repository:** `open-dv` (`packages/opendv-contracts`, `packages/opendv-sources`, `packages/opendv-agent-harness`, `packages/opendv-service`, `apps/opendv-platform-api`, `apps/opendv-cli`, `apps/opendvctl`, `apps/opendv-web-workbench`)  
**Interface Dialects:** OpenAPI 3.1.0 (`schemas/v1/openapi.json` & `schemas/openapi-v1.json`), CLI Specification v1 (`schemas/v1/cli-spec.json`), W3C Server-Sent Events, RFC 8785 JCS, RFC 9457 Problem Details  
**Normative Cross-References:** [`DESIGN.md`](../DESIGN.md), [`docs/19-ontology-studio-architecture.md`](19-ontology-studio-architecture.md), [`docs/27-unified-ontology-pipeline-requirements-and-uat.md`](27-unified-ontology-pipeline-requirements-and-uat.md), [`docs/28-unified-ontology-pipeline-design-and-plan.md`](28-unified-ontology-pipeline-design-and-plan.md), `ADR-001` through `ADR-016`

---

## 1. Functional Requirements & Personas

### 1.1 Target System Personas & Open-Access Loopback Model (`D18`, `D28`)
Under Phase 1 loopback zero-auth open access (`D18`, `FR-AUTH-01..04`), local loopback callers on `127.0.0.1` / `::1` require zero login tokens or CSRF headers and resolve directly to `StudioPrincipal(subject="human:local_user", role="admin")` (`RbacRole.PRIVILEGED_CUSTODIAN`). For CLI/platform auditability, the core role catalog spans `viewer`, `editor`, `admin`, and `automated` (`INTERNAL_MODELER`):

| Persona | Role Clearance | Primary Objectives | Interface Surfaces | Constraints |
|---|---|---|---|---|
| **Viewer** | `viewer` | Read-only inspection (`list`, `status`, `proposal list/show`, `export`, `logs`, `run dv-schema`). | CLI / Workbench | Read-only; cannot mutate state. |
| **Data Steward** | `DATA_STEWARD` / `editor` | Ingests sources/connectors, inspects Step 3 evidence, runs Agent Discovery (`D28`), answers clarifications, cherry-picks proposals, applies additive deltas. | 4-Tab Web Workbench (`/` Unified Wizard, `/dv-schema`) | Additive modeling only; Path A/B grounding (`FR-AGT-07`) enforced. |
| **Enterprise Architect** | `RESTRICTED_PII_CUSTODIAN` / `admin` | Default local loopback identity (`human:local_user`); governs workspaces, resolves Step 6 Steward/Budget Holds (`D14`, `D21`), executes steward revisions, authorizes promotion. | Loopback Web Workbench, `opendv`, `opendvctl` | Full administrative elevation; accountable for audit trail. |
| **Automated Modeler / CI** | `INTERNAL_MODELER` / `automated` | Headless CLI CI/CD execution (`--mode deterministic` lives solely in CLI/API, never in UI `D28`). | Headless CLI / CI runner | Destructive ops and manual proposal accept restricted; tagged `mode=deterministic` in logs. |

### 1.2 Functional Requirements (FR-1 through FR-10 & Phase 1–4 Extensions)

| Requirement ID | Name | Summary Description | Applicable Personas | Normative Exit Codes |
|---|---|---|---|---|
| **FR-1** | Multi-Format Ingestion & Connectors | Ingest files (PDF, DOCX, MD, CSV, Parquet, JSON), inline domain notes, and structured connectors (`SQLite`, `DuckDB`, `Databricks UC`, tabular files via `sources_inspect` + `GraphBProfiler`, `P1`). | All personas | 0 (OK), 2 (Syntax), 4 (Corrupt), 1 (OCR/Office missing) |
| **FR-2** | Reference Alignment & Empirical Grounding | Align concepts via FIBO BM25 (`>= 0.6500` Path A) or Path B empirical `EvidenceRef` citations (`FR-AGT-07`, `D3`, `D4` zero mention-count floor). | All personas | 0 (OK), 4 (Grounding violation) |
| **FR-3** | Agent-First Synthesis (`422` Gate) & CLI CI Mode | Web UI is 100% Agent-only (`D28`); `mode="agent"` fails closed with `422 AGENT_NOT_CONFIGURED` if unconfigured; single-pass `@term-scout -> @domain-clarifier -> @ontology-architect` (`D1`), `HarnessBudget` cost meter + `BudgetHold` (`D21`), and CLI-only `--mode deterministic` for CI (`FR-CLI-07`). | All personas | 0 (OK), 10 (`422`), 20 (`409`), 1 (Agent down) |
| **FR-4** | Dual FIBO Q&A & Citation Drawer | Structured `@domain-clarifier` Q&A (`Q1..Qn`) with live cascade (`D1`, `D5`) + interactive `Ask FIBO & Sources` (`POST .../fibo-qa`, `D2`) + batch `CitationInspectorDrawer` (`4c`). | Steward, Architect | 0 (OK), 3 (Question not found) |
| **FR-5** | Additive Delta Management & Opt-In `MAJOR` | Calculate SemVer per 24-Op Calculus; isolate breaking ops into unchecked-by-default `MAJOR · Opt-In` (`D7`); optional Step 5 `VisualEditorDrawer` (`D15`) + `Export Ontology Only` (`D9`). | Steward, Architect | 0 (OK), 4 (Envelope/contract violation), 5 (OCC) |
| **FR-6** | Steward Revisions, Rebase & Concurrency | Apply destructive mutations under admin privilege; rebase `STALE` proposals; enforce one active job per session (`409 SESSION_JOB_ACTIVE`, `D27`). | Architect | 0 (OK), 20 (`409` active job), 5 (Conflict) |
| **FR-7** | Claude `DESIGN.md` 4-Tab Workbench & Explorer | Unified 4-tab shell (`Ontology & Pipeline`, `DV Schema Explorer`, `Run Explorer`, `Settings`) with 6-Step Wizard, 4-View `/dv-schema` (`D11` `validate_safe_sql`, `5-Zone Z0–Z4` + `information_mart`), `SessionLogsInspector` (`07`), and `<768px` view-only responsiveness (`D26`). | Steward, Architect | N/A (Web UI) |
| **FR-8** | Dual-CLI Platform Parity (`D13`) | Maintain bit-for-bit JCS operational parity across `opendv`, `opendvctl`, and REST, including 4 new subcommands (`run dv-schema`, `ontology studio attach-source`, `ontology studio fibo-qa`, `ontology studio logs`). | All personas | Mapping via `error-codes.json` (`0, 1..7, 10, 20, 30, 40`) |
| **FR-9** | Workspace Promotion & Step 6 Hold Resolution | Atomically promote head ontologies (`graph_a.lock`), auto-bind multi-wave `deployed_contract` (`D12`), resolve `StewardHoldModal` inline in Step 6 (`D14`), and `↺ Sync Drift` (`D10`). | Architect | 0 (OK), 1 (Rationale missing), 2 (Path), 5 (OCC), 30 (`PAUSED_HOLD`) |
| **FR-10** | Secure Session Bundles & Log Retention (`D24`) | Export/import `.odvstudio.zip` archives with streaming ZIP bomb guards, delta replay, and session-lifetime `50 MB` `.gz`/`.jsonl` prompt/LLM logs (`D24`). | Steward, Architect | 0 (OK), 4 (Archive violation / replay mismatch) |

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
- **AC-6 (Security Perimeter & Loopback Guard Enforcement)**: Non-loopback `--host` without `--allow-remote-unauthenticated` aborts with exit code `40` (`D18`); `OPENDV_WORKSPACE_ROOTS` escapes and ZIP bomb/symlinks reject with exit codes 2 and 4 respectively; `validate_safe_sql()` enforces read-only `SELECT`/`UNION` SQL (`D11`).
- **AC-7 (Deployed Table Contract Guard)**: Destructive operations targeting deployed Data Vault tables are blocked with HTTP 422 and exit code 4.
- **AC-8 (Schema Synchronization)**: `error-codes.json`, `baseline-digests.json`, `openapi.json`, and `cli-spec.json` pass validation with zero undocumented tokens.

---

## 3. REST API Specification (`/api/v1/ontology/studio` & `/api/v1/runs/{run_id}/dv-schema`)

All Studio endpoints are rooted at `/api/v1/ontology/studio` (decomposed into `< 5,000`-token routers `ontology_studio_{sessions,evidence,citations,proposals,logs,exports,blueprint}.py` plus `routers/dv_schema_explorer.py`, `D16`). Under Phase 1 loopback open access (`D18`, `FR-AUTH-01..04`), loopback requests require zero tokens, cookies, or custom CSRF headers, mapping automatically to `StudioPrincipal(subject="human:local_user", role="admin")`. `POST /init` and `/auth/*` are preserved solely as benign `200 OK` compatibility shims. To prevent DNS rebinding, the server strips `X-Forwarded-*` headers and enforces loopback `Host`/`Origin` validation (`403 CSRF_ORIGIN_DENIED`, exit 7), while refusing non-loopback `--host` bindings with **exit code `40`** unless `--allow-remote-unauthenticated` is passed.

**Strict Path Safety & Containment**: All path and query parameters are validated against closed patterns before path construction:
- `session_id`, `proposal_id`, `job_id`, `question_id`: `^[a-z0-9_-]{1,64}$` (`session_id` excludes reserved names `bundle`, `init`, `sessions`, `health`, `auth`, `admin`).
- `source_id`: `^[a-f0-9]{64}$` (SHA-256 hex digest).
- `version`: `^v?[0-9]+\.[0-9]+\.[0-9]+$` (SemVer tuple).
Every path within `<session_dir>` is verified via `try: os.path.commonpath([os.path.normcase(os.path.realpath(session_dir)), os.path.normcase(os.path.realpath(resolved_path))]) == os.path.normcase(os.path.realpath(session_dir)) except ValueError: False`. Traversal (`..`, slashes) aborts with `400 SYNTAX_INVALID_ARGUMENT` (exit 2).

### 3.1 Endpoint Inventory & Parameter Matrix

```
Base URIs: /api/v1/ontology/studio  |  /api/v1/runs/{run_id}/dv-schema
```

#### 1. `POST /init` (Compatibility Shim)
- **Summary**: Benign `200 OK` loopback compatibility shim (`human:local_user`, `admin`). Legacy token-minting and `409` re-init barriers were retired in Phase 1 (`D18`).
- **Responses**: 200 `InitStudioResponse` | 403 `CSRF_ORIGIN_DENIED` (cross-site origin)

#### 2. `GET /sessions`
- **Summary**: List Studio Sessions (`?wizard_only=true` filters to `wizard_schema_version` sessions + returns `legacy_session_count` per `D25`).
- **Parameters**: `limit` (int=50), `offset` (int=0), `status` (string, optional), `wizard_only` (bool, optional)
- **Responses**: 200 `StudioSessionListDTO` / `list[StudioSessionSummaryDTO]`

#### 3. `POST /sessions` & `POST /sessions/import-ontology`
- **Summary**: Create Studio Session (`wizard_schema_version` recorded on new sessions `D25`) or import `.yaml`/`.json`/`.ttl` baseline ontology (`POST /sessions/import-ontology`) with RFC 8785 JCS SHA-256 verification.
- **Request Body**: `CreateSessionRequest` / `multipart/form-data` (`file`, `domain_namespace`, `enterprise_owner`)
- **Responses**: 201 `StudioSessionDTO` | 400 `INVALID_WORKSPACE_PATH`, `REMOTE_SERVER_PATH_FORBIDDEN`, `CONFLICTING_WORKSPACE_SEED_PARAMS` | 409 `SESSION_ALREADY_EXISTS`

#### 4. `GET /sessions/{session_id}`
- **Summary**: Get Studio Session Details
- **Path Parameters**: `session_id` (`^[a-z0-9_-]{1,64}$`, excluding reserved names `bundle`, `init`, `sessions`, `health`, `auth`, `admin`)
- **Responses**: 200 `StudioSessionDetailDTO` | 400 `INVALID_SESSION_IDENTIFIER` | 404 `SESSION_NOT_FOUND`

#### 5. `DELETE /sessions/{session_id}`
- **Summary**: Delete Studio Session (also removes session-lifetime logs per `D24`)
- **Path Parameters**: `session_id` (string); **Query Parameters**: `force` (boolean, default: false)
- **Responses**: 204 No Content | 400 `SYNTAX_INVALID_ARGUMENT` | 404 `SESSION_NOT_FOUND` | 422 `CANNOT_DELETE_PROMOTED_SESSION`

#### 6. `POST /sessions/{session_id}/sources`, `/sources/connectors` & `/sources/text`
- **Summary**: Attach tabular connectors (`SQLite`, `DuckDB`, `Databricks UC`, `CSV`, `Parquet`, `XLSX`, `JSONL` via `sources_inspect` + `GraphBProfiler`, `P1`), upload multipart documents (`ocr`, `do_not_send_to_agent`), or add inline domain notes (`/sources/text`, CRLF-normalized UTF-8 spans).
- **Responses**: 201 `SourceUploadResponse` / `ConnectorAttachResponse` | 400 `INVALID_SESSION_IDENTIFIER`, `SYNTAX_INVALID_ARGUMENT` | 404 `SESSION_NOT_FOUND` | 413 `PAYLOAD_TOO_LARGE` | 422 `CORRUPT_SOURCE_BLOB`

#### 7. `DELETE /sessions/{session_id}/sources/{source_id}`
- **Summary**: Remove Source Document (Tombstone in Manifest)
- **Path Parameters**: `session_id` (string), `source_id` (string)
- **Responses**: 204 No Content | 404 `SOURCE_NOT_FOUND` | 422 `SOURCE_REFERENCED_BY_COMMITTED_VERSION`, `SOURCE_REFERENCED_BY_PENDING_PROPOSAL`

#### 8. `POST /sessions/{session_id}/parse` & `GET /sessions/{session_id}/evidence/{tables,chunks}`
- **Summary**: Trigger asynchronous layout parsing (`POST .../parse`) and inspect Step 3a structured schema profiles + `<= 50` sample rows (`GET .../evidence/tables`) or Step 3b parsed chunks + embedded `table_data` (`GET .../evidence/chunks`).
- **Responses**: 202 `JobHandleDTO` / 200 `EvidenceTablesDTO` / 200 `EvidenceChunksDTO` | 404 `SESSION_NOT_FOUND`, `SOURCE_NOT_FOUND` | 409 `SESSION_JOB_ACTIVE` | 501 `OCR_UNAVAILABLE`, `OFFICE_CONVERSION_UNAVAILABLE`

#### 9. `POST /sessions/{session_id}/clarify` & `POST .../citations/resolve` & `GET /fibo/lookup` & `POST .../fibo-qa`
- **Summary**: Generate clarification questions (`/clarify`), batch-resolve Step 4c document spans + table profiles + `fibo-core` definitions (`/citations/resolve`), query side-by-side FIBO entries (`GET /fibo/lookup`), and run live-agent cited `Ask FIBO & Sources` Q&A (`POST .../fibo-qa`, `D2`).
- **Responses**: 200 / 202 | 404 `SESSION_NOT_FOUND` | 409 `SESSION_JOB_ACTIVE` | 422 `AGENT_NOT_CONFIGURED` | 503 `AGENT_UNAVAILABLE`

#### 10. `POST /sessions/{session_id}/answers`
- **Summary**: Submit Clarification Answers (cascades live in Step 4b `D1`, `D5`)
- **Path Parameters**: `session_id` (string)
- **Request Body**: `ClarificationAnswerRequest`
- **Responses**: 200 `ClarificationAnswerResponse` (`{ saved_count, answers_sha256 }`) | 400 `SYNTAX_INVALID_ARGUMENT` | 404 `SESSION_NOT_FOUND`, `QUESTION_NOT_FOUND`

#### 11. `POST /sessions/{session_id}/construct`
- **Summary**: Trigger Ontology Construction Job (`StudioAgentRunner` single pass `@term-scout -> @domain-clarifier -> @ontology-architect`, `D1`, `D21`). Defaults to `mode="agent"` + `stage_output_policy="strict"`.
- **Path Parameters**: `session_id` (string); **Headers**: `If-Match: "<head_sha256>"` (if CLI auto-commit)
- **Responses**: 202 `JobHandleDTO` | 400 `MISSING_AUTO_COMMIT_PRECONDITION` | 404 `SESSION_NOT_FOUND` | 409 `SESSION_JOB_ACTIVE` (`D27`), `LOCK_TIMEOUT` | 412 `ONT_EVO_CONCURRENT_MODIFICATION` | 422 `AGENT_NOT_CONFIGURED` (`FR-AGT-01`) | 503 `AGENT_UNAVAILABLE`

#### 12. `POST /sessions/{session_id}/delta`
- **Summary**: Apply Direct Delta Operations (Additive or Steward Revision)
- **Path Parameters**: `session_id` (string); **Headers**: `If-Match: "<head_sha256>"` (Required)
- **Responses**: 200 `DeltaCommitResponse` | 400 `SYNTAX_INVALID_ARGUMENT`, `VALIDATION_AMBIGUOUS_TARGET` | 403 `AGENT_OPERATION_FORBIDDEN`, `FORBIDDEN_ADMIN_REQUIRED` | 404 `SESSION_NOT_FOUND` | 409 `LOCK_TIMEOUT`, `JOB_COMMIT_IN_PROGRESS` | 412 `ONT_EVO_CONCURRENT_MODIFICATION` | 422 `ONT_EVO_CONTRACT_VIOLATION`, `ONT_EVO_ENVELOPE_VIOLATION`, `ONT_EVO_UNGROUNDED_CONCEPT`, `ONT_EVO_GROUNDING_VIOLATION`, `HIERARCHICAL_CYCLE_DETECTED`, `PHYSICAL_TABLE_NAME_COLLISION`, `DUPLICATE_SYNONYM`, `DUPLICATE_ATTRIBUTE_NAME`

#### 13–15. `GET /sessions/{session_id}/proposals`, `GET .../proposals/{proposal_id}`, `POST .../proposals`
- **Summary**: List, inspect, or submit candidate proposals (including `MAJOR · Opt-In` flagging `D7` and `EvidenceRef` citations `FR-CIT-01`).
- **Responses**: 200 / 201 | 400 `SYNTAX_INVALID_ARGUMENT` | 403 `AGENT_DESTRUCTIVE_OP_FORBIDDEN` | 404 `SESSION_NOT_FOUND`, `PROPOSAL_NOT_FOUND` | 409 `LOCK_TIMEOUT` | 422 `ONT_EVO_CONTRACT_VIOLATION`, `ONT_EVO_ENVELOPE_VIOLATION`

#### 16. `POST /sessions/{session_id}/proposals/{proposal_id}/accept`
- **Summary**: Accept Pending Proposal (or Cherry-Picked Subset `D5`, `D7`) & Commit Version Snapshot
- **Headers**: `If-Match: "<head_sha256>"` (Required)
- **Responses**: 200 `DeltaCommitResponse` | 400 `SYNTAX_INVALID_ARGUMENT` | 403 `AGENT_PROPOSAL_ACCEPT_FORBIDDEN` | 404 `SESSION_NOT_FOUND`, `PROPOSAL_NOT_FOUND` | 409 `PROPOSAL_ALREADY_RESOLVED`, `LOCK_TIMEOUT` | 412 `ONT_EVO_CONCURRENT_MODIFICATION` | 422 `ONT_EVO_UNGROUNDED_CONCEPT`, `ONT_EVO_GROUNDING_VIOLATION`, `HIERARCHICAL_CYCLE_DETECTED`, `ONT_EVO_CONTRACT_VIOLATION`

#### 17–18. `POST .../proposals/{proposal_id}/reject` & `POST .../proposals/{proposal_id}/rebase`
- **Summary**: Reject pending proposal or rebase `STALE` proposal across concurrent head commits (`If-Match: "<head_sha256>"`).
- **Responses**: 200 `ProposalRecord` | 400 `SYNTAX_INVALID_ARGUMENT` | 404 `SESSION_NOT_FOUND`, `PROPOSAL_NOT_FOUND` | 409 `PROPOSAL_ALREADY_RESOLVED`, `LOCK_TIMEOUT` | 412 `ONT_EVO_CONCURRENT_MODIFICATION` | 422 `HIERARCHICAL_CYCLE_DETECTED`, `ONT_EVO_CONTRACT_VIOLATION`

#### 19–20. `GET /sessions/{session_id}/jobs/{job_id}` & `GET .../jobs/{job_id}/events`
- **Summary**: Poll job status or stream W3C SSE (`text/event-stream`, `Last-Event-ID`).
- **Responses**: 200 `JobStatusDTO` / `text/event-stream` | 400 `SYNTAX_INVALID_ARGUMENT` | 404 `JOB_NOT_FOUND`

#### 21. `GET /sessions/{session_id}/logs`, `GET .../logs/stream` & `GET .../logs/export` (`FR-LOG-02`, `D24`)
- **Summary**: Query merged monotonic session & linked pipeline logs (`wizard_step`, `stage_id` accepting `STAGE_00..07` or Step 6 pills `00..06`, `persona`, `source_id`, `category`), subscribe to live W3C SSE `EventSource` (`text/event-stream`, `Last-Event-ID`, `15s` keepalives, subscribe-before-replay), or download `.jsonl` session log bundle (`50 MB` `.gz` rotation).
- **Responses**: 200 `SessionLogsResponse` / `text/event-stream` / `application/x-ndjson` | 400 `SYNTAX_INVALID_ARGUMENT` | 404 `SESSION_NOT_FOUND`

#### 22. `GET /sessions/{session_id}/export` & `GET /sessions/{session_id}/export-blueprint`
- **Summary**: Export committed ontology (`yaml` / `json` / `turtle` / `jsonld`, `D9` Export Ontology Only) or `VaultBlueprint` (`export-blueprint`).
- **Responses**: 200 stream | 400 `UNSUPPORTED_FORMAT` | 404 `VERSION_NOT_FOUND`, `SESSION_NOT_FOUND`

#### 23. `POST /sessions/{session_id}/promote`
- **Summary**: Promote Head Ontology to Workspace Data Vault Pipeline (`graph_a.lock`, dual OCC).
- **Responses**: 200 `PromoteResponse` | 400 `PROMOTION_RATIONALE_REQUIRED`, `INVALID_WORKSPACE_PATH`, `SYNTAX_INVALID_ARGUMENT` | 404 `SESSION_NOT_FOUND` | 409 `LOCK_TIMEOUT` | 412 `LINEAGE_ROOT_MISMATCH`, `ONT_EVO_CONCURRENT_MODIFICATION` | 422 `ONT_EVO_CONTRACT_VIOLATION`, `ONT_EVO_UNGROUNDED_CONCEPT`, `ONT_EVO_GROUNDING_VIOLATION`

#### 24–26. `POST .../bundle`, `POST /sessions/bundle/import`, `POST .../bundle/clone`
- **Summary**: Export/import/clone portable `.odvstudio.zip` archives (including session-lifetime prompt/LLM logs per `D24`, streaming ZIP bomb guards, and delta replay).
- **Responses**: 200 / 201 | 400 / 404 / 409 / 422 `ARCHIVE_SECURITY_VIOLATION`, `IMPORT_DELTA_REPLAY_HASH_MISMATCH`

#### 27. `GET /runs/{run_id}/dv-schema`, `GET .../tables/{table}/rows` & `POST .../query` (`FR-DVE-01..07`, `D11`)
- **Summary**: Project `CanonicalIR` + `NoMistakeDataVault2ContractV5` + scorecard + optional `?compare=<base_run_id>` multi-wave diff (`P3`); read paginated rows from persisted/rehydrated DuckDB sandbox (`.../tables/{table}/rows`); and execute read-only SQL guarded by `validate_safe_sql()` allowlist + `enable_external_access=False` (`POST .../query`, `D11`).
- **Responses**: 200 `DvSchemaProjectionDTO` / `DvTableRowsDTO` / `DvSqlQueryResultDTO` | 400 `INVALID_REQUEST` | 404 `RUN_NOT_FOUND`

#### 28. `POST /auth/launch-nonce` & `POST /auth/token` (Compatibility Shims)
- **Summary**: Benign `200 OK` loopback compatibility shims preserved for legacy external scripts (`D18`). Loopback callers operate zero-token with no nonce exchange required.

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
The operational CLI subcommands in `schemas/v1/cli-spec.json` map directly to corresponding OpenAPI operations in `schemas/v1/openapi.json` (and `schemas/openapi-v1.json`) with identical RFC 8785 JCS outputs across `opendv`, `opendvctl`, and direct REST (`FR-CLI-05`, `D13`):

| CLI Command | HTTP Route | Role / Guard | Canonical Exit Codes |
|---|---|---|---|
| `serve` | Platform Server (`--host`, `--port`, `--open`, `--allow-remote-unauthenticated`) | Loopback zero-auth (`D18`) | 0, 40 (non-loopback without flag) |
| `run dv-schema` (`D13`) | `GET /runs/{id}/dv-schema`, `GET .../rows`, `POST .../query` | Safe read-only SQL (`D11`) | 0, 2, 3, 40 |
| `init` | `POST /init` (benign `200 OK` shim) | `loopback/owner` | 0 |
| `create` | `POST /sessions` | `editor` | 0, 1, 2, 6 |
| `list` | `GET /sessions` (`D25` `wizard_schema_version`) | `viewer` | 0 |
| `status` | `GET /sessions/{id}` | `viewer` | 0, 2, 3 |
| `upload` | `POST /sessions/{id}/sources` | `editor` | 0, 2, 3, 4 |
| `attach-source` (`D13`) | `POST /sessions/{id}/sources/connectors` | `editor` (`P1` secrets guard) | 0, 2, 3, 4 |
| `parse` | `POST /sessions/{id}/parse` | `editor` (`409` if job active) | 0, 1, 3, 20 |
| `clarify` | `POST /sessions/{id}/clarify` | `editor` (`422` if unconfigured) | 0, 1, 3, 10, 20 |
| `answer` | `POST /sessions/{id}/answers` | `editor` | 0, 2, 3 |
| `fibo-qa` (`D13`) | `POST /sessions/{id}/fibo-qa` | `viewer` (`D2` cited Q&A) | 0, 1, 2, 3, 10 |
| `construct` | `POST /sessions/{id}/construct` | `editor` (`422` / `409` gates) | 0, 1, 2, 3, 4, 5, 6, 10, 20 |
| `logs` (`D13`) | `GET /sessions/{id}/logs`, `.../logs/stream` (`--follow`) | `viewer` (`STAGE_00..07` or `00..06`) | 0, 2, 3 |
| `delta apply` | `POST /sessions/{id}/delta` | `editor` / `admin`* | 0, 1, 2, 3, 4, 5, 6 |
| `proposal list` | `GET /sessions/{id}/proposals` | `viewer` | 0, 3 |
| `proposal show` | `GET /sessions/{id}/proposals/{pid}` | `viewer` | 0, 3 |
| `proposal accept` | `POST /sessions/{id}/proposals/{pid}/accept` | `editor` / `admin`* | 0, 2, 3, 4, 5, 6 |
| `proposal reject` | `POST /sessions/{id}/proposals/{pid}/reject` | `editor` | 0, 2, 3, 6 |
| `proposal rebase` | `POST /sessions/{id}/proposals/{pid}/rebase` | `editor` | 0, 2, 3, 4, 5, 6 |
| `export` | `GET /sessions/{id}/export` | `viewer` | 0, 2, 3 |
| `promote` | `POST /sessions/{id}/promote` | `admin` | 0, 1, 2, 3, 4, 5, 6 |
| `bundle export` | `POST /sessions/{id}/bundle` | `editor` / `admin`* | 0, 1, 3, 4 |
| `bundle import` | `POST /sessions/bundle/import` | `editor` / `admin`* | 0, 1, 2, 3, 4, 6 |
| `bundle clone` | `POST /sessions/{id}/bundle/clone` | `editor` | 0, 2, 3, 6 |

*\* Note: `delta apply` requires `admin` for destructive steward revisions; `bundle export` and `bundle import` require `admin` when acknowledging confidential export or unverified import attribution. On loopback, requests execute as `human:local_user` (`admin`) (`D18`).*


### 5.2 Canonical Exit Code Registry (0..7 & Platform Guards `10`, `20`, `30`, `40`)
All commands in both CLIs map their termination status deterministically through `schemas/v1/error-codes.json`:

| Exit Code | Classification | Trigger Conditions | Corresponding HTTP Statuses |
|---|---|---|---|
| **0** | Success | Command completed successfully; output emitted. | `200 OK`, `201 Created`, `202 Accepted`, `204 No Content` |
| **1** | Capability / System Error | Missing required rationale ($\ge 10$ chars), zero-op delta, uninstalled system capability (OCR, LibreOffice, Agent LLM), or internal crash. | `500 Internal Server Error`, `501 Not Implemented`, `503 Service Unavailable`, `400 Bad Request` |
| **2** | Syntax / Precondition Error | Invalid CLI flag, missing mandatory arguments, URI format violation, malformed offset ordering (start >= end), payload too large, or workspace boundary violation. | `400 Bad Request`, `413 Payload Too Large`, `415 Unsupported Media Type` |
| **3** | Resource Not Found | Specified session ID, run ID, version string, proposal ID, source ID, or question ID does not exist. | `404 Not Found` |
| **4** | Domain & Governance Violation | ONT-EVO-001 deployed table conflict, ONT-EVO-002 grounding violation, archive security violation, or delta replay mismatch. | `422 Unprocessable Content` |
| **5** | Concurrency Conflict (OCC) | Session head SHA-256 or workspace SHA-256 mismatch during commit, accept, rebase, or promotion. | `412 Precondition Failed` |
| **6** | Lock Timeout / In Progress | Advisory file lock acquisition timed out (30s) or operating on an already resolved proposal. | `409 Conflict` |
| **7** | Origin / RBAC Denied | Cross-site DNS-rebinding `Origin` denied or restricted actor attempting privileged mutation. | `403 Forbidden` |
| **10** | Agent Not Configured (`FR-AGT-01`) | `mode="agent"` + `stage_output_policy="strict"` invoked without a ready real LLM provider (`AGENT_NOT_CONFIGURED`). | `422 Unprocessable Content` |
| **20** | Session Job Active (`D27`) | Concurrent job started in a session that already has an active job/run (`SESSION_JOB_ACTIVE`). | `409 Conflict` |
| **30** | Steward / Budget Hold (`D14`, `D21`) | Pipeline paused in `PAUSED_HOLD` awaiting inline Steward Hold or `BudgetHold` resolution. | `202 Accepted` (`PAUSED_HOLD`) |
| **40** | Remote Guard Refusal (`D18`) / Wizard Validation | `opendv serve` / `opendv-platform-api` bound to non-loopback `--host` without `--allow-remote-unauthenticated`, or `INVALID_REQUEST` (incl. unsafe SQL) / `SYNTAX_INVALID_ARGUMENT` in `D13` wizard/`dv-schema` commands. | Startup Abort / `400 Bad Request` |

### 5.3 Complete Command Inventory (`opendv ontology studio` & `D13` Commands)

#### 1. `init` (Compatibility Shim)
- **Synopsis**: `opendv ontology studio init [--force]`
- **Semantics**: Retained as a benign `200 OK` compatibility shim (`D18`). Loopback open access requires zero initialization or tokens.

#### 2. `create`
- **Synopsis**: `opendv ontology studio create [-s <id>] [-n <ns>] [-o <owner>] [--from-workspace <ws>] [-d <desc>]`
- **Key Constraints**: `-s` regex `^[a-z0-9_-]{1,64}$`. Records `wizard_schema_version` (`D25`); `--from-workspace` loads contract and anchors baseline SHA.
- **Exit Codes**: 0 (OK), 1 (`CONFLICTING_WORKSPACE_SEED_PARAMS`), 2 (`INVALID_WORKSPACE_PATH`), 6 (`SESSION_ALREADY_EXISTS`)

#### 3. `list`
- **Synopsis**: `opendv ontology studio list [--limit <int>] [--offset <int>] [--status <str>]`
- **Exit Codes**: 0 (OK)

#### 4. `status`
- **Synopsis**: `opendv ontology studio status -s <session_id>`
- **Exit Codes**: 0 (OK), 2 (`INVALID_SESSION_IDENTIFIER`), 3 (`SESSION_NOT_FOUND`)

#### 5. `upload` & `attach-source` (`D13`)
- **Synopsis**:
  - `opendv ontology studio upload -s <session_id> -f <file> [--do-not-send-to-agent] [--description <str>]`
  - `opendv ontology studio attach-source -s <session_id> --adapter <sqlite|duckdb|databricks|csv|parquet|xlsx|jsonl|filesystem> --uri <uri_or_path> [--table <name>] [--namespace <ns>] [--credentials-ref <env:...|secret:...>] [--description <str>] [--do-not-send-to-agent]`
- **Key Constraints**: Rejects plaintext secrets (`_FORBIDDEN_SECRET_KEYS`, requiring `env:`/`secret:` refs); `--do-not-send-to-agent` sets taint in CAS so zero bytes leave the host (`FR-AGT-04`).
- **Exit Codes**: 0 (OK), 2 (`INVALID_SESSION_IDENTIFIER`, `PAYLOAD_TOO_LARGE`, `SYNTAX_INVALID_ARGUMENT`), 3 (`SESSION_NOT_FOUND`), 4 (`CORRUPT_SOURCE_BLOB`)

#### 6. `parse`
- **Synopsis**: `opendv ontology studio parse -s <session_id> [--source-ids <id1,id2...>] [--ocr-mode <auto|force|off>] [--wait] [--stream]`
- **Exit Codes**: 0 (OK), 1 (`OCR_UNAVAILABLE`, `OFFICE_CONVERSION_UNAVAILABLE`), 2 (`SYNTAX_INVALID_ARGUMENT`), 3 (`SESSION_NOT_FOUND`), 20 (`SESSION_JOB_ACTIVE`)

#### 7. `clarify` & `fibo-qa` (`D13`)
- **Synopsis**:
  - `opendv ontology studio clarify -s <session_id> [--max-questions <int>] [--wait] [--stream]`
  - `opendv ontology studio fibo-qa -s <session_id> --question <text> [--source-id <id>] [--concept-uri <uri>]`
- **Exit Codes**: 0 (OK), 1 (`AGENT_UNAVAILABLE`), 2 (`SYNTAX_INVALID_ARGUMENT`), 3 (`SESSION_NOT_FOUND`), 10 (`AGENT_NOT_CONFIGURED`), 20 (`SESSION_JOB_ACTIVE`)

#### 8. `answer`
- **Synopsis**: `opendv ontology studio answer -s <session_id> -q <question_id> [-o <opt>...] [-t <text>]`
- **Exit Codes**: 0 (OK), 2 (`SYNTAX_INVALID_ARGUMENT`), 3 (`QUESTION_NOT_FOUND`)

#### 9. `construct`
- **Synopsis**: `opendv ontology studio construct -s <session_id> [--mode <auto|agent|deterministic>] [--auto-commit] [--base-version <v>] [--base-sha256 <s>] [--wait] [--stream]`
- **Semantics**: UI is strictly Agent-only (`D28`); explicit `--mode deterministic` exists solely in CLI/API for offline CI benchmarks (`FR-CLI-07`). Default `mode="agent"` fails closed with exit `10` (`422 AGENT_NOT_CONFIGURED`) if no real LLM provider is ready, and `exit 20` (`409 SESSION_JOB_ACTIVE`) if a job is already active in the session (`D27`).
- **Exit Codes**: 0 (OK), 1 (`AGENT_UNAVAILABLE`), 2 (`MISSING_AUTO_COMMIT_PRECONDITION`), 3 (`SESSION_NOT_FOUND`), 4 (`PHYSICAL_TABLE_NAME_COLLISION`, `ONT_EVO_CONTRACT_VIOLATION`), 5 (`ONT_EVO_CONCURRENT_MODIFICATION`), 6 (`LOCK_TIMEOUT`), 10 (`AGENT_NOT_CONFIGURED`), 20 (`SESSION_JOB_ACTIVE`)

#### 10. `logs` (`D13`, `FR-LOG-04`)
- **Synopsis**: `opendv ontology studio logs [--session <id> | --session-id <id>] [--stage <STAGE_00..07|00..06>] [--step <int>] [--category <str>] [--persona <str>] [--follow]` (global `--json`)
- **Semantics**: Queries merged session & linked pipeline logs (`STAGE_00..07` or Step 6 pill aliases `00..06`), streaming `/logs/stream` live via W3C SSE when `--follow` is supplied.
- **Exit Codes**: 0 (OK), 2 (`SYNTAX_INVALID_ARGUMENT`), 3 (`SESSION_NOT_FOUND`)

#### 11. `run dv-schema` (`D13`, `D11`)
- **Synopsis**: `opendv run dv-schema --run-id <id> [--compare <base_run_id>] [--table <name>] [--sql <select_sql>] [--limit <int>] [--offset <int>]`
- **Semantics**: Inspects DV 2.0 schema graph projection, multi-wave diff, persisted DuckDB table rows, or executes bounded read-only `SELECT`/`WITH` SQL validated via `validate_safe_sql()`.
- **Exit Codes**: 0 (OK), 2 (`SYNTAX_INVALID_ARGUMENT` missing `--run-id`), 3 (`RUN_NOT_FOUND`), 40 (`INVALID_REQUEST`, `SYNTAX_INVALID_ARGUMENT`)

#### 12. `delta apply`
- **Synopsis**: `opendv ontology studio delta apply -s <session_id> -f <delta_file> --base-sha256 <s> [--rationale <str>] [--grounding-rationale <str>]`
- **Exit Codes**: 0 (OK), 1 (Missing rationale), 2 (Syntax), 3 (`SESSION_NOT_FOUND`), 4 (`ONT_EVO_CONTRACT_VIOLATION`, `ONT_EVO_ENVELOPE_VIOLATION`, `ONT_EVO_UNGROUNDED_CONCEPT`, `ONT_EVO_GROUNDING_VIOLATION`, `HIERARCHICAL_CYCLE_DETECTED`, `PHYSICAL_TABLE_NAME_COLLISION`, `DUPLICATE_SYNONYM`, `DUPLICATE_ATTRIBUTE_NAME`), 5 (`ONT_EVO_CONCURRENT_MODIFICATION`), 6 (`LOCK_TIMEOUT`, `JOB_COMMIT_IN_PROGRESS`), 7 (`FORBIDDEN_ADMIN_REQUIRED`, `AGENT_OPERATION_FORBIDDEN`)

#### 13. `proposal list / show / accept / reject / rebase`
- **Synopsis**:
  - `opendv ontology studio proposal list -s <session_id>`
  - `opendv ontology studio proposal show -s <session_id> -p <proposal_id>`
  - `opendv ontology studio proposal accept -s <session_id> -p <proposal_id> --base-sha256 <s> [--grounding-rationale <str>]`
  - `opendv ontology studio proposal reject -s <session_id> -p <proposal_id> [-r <reason>]`
  - `opendv ontology studio proposal rebase -s <session_id> -p <proposal_id> --base-sha256 <s>`
- **Exit Codes**: 0 (OK), 2 (Syntax), 3 (Not found), 4 (`ONT_EVO_UNGROUNDED_CONCEPT`, `ONT_EVO_GROUNDING_VIOLATION`, cycle), 5 (`ONT_EVO_CONCURRENT_MODIFICATION`), 6 (`PROPOSAL_ALREADY_RESOLVED`), 7 (`AGENT_PROPOSAL_ACCEPT_FORBIDDEN`)

#### 14. `export`
- **Synopsis**: `opendv ontology studio export -s <session_id> [-e <yaml|json|turtle|jsonld>] [-v <version>] [-o <out_file>]`
- **Exit Codes**: 0 (OK), 2 (`UNSUPPORTED_FORMAT`), 3 (`VERSION_NOT_FOUND`)

#### 15. `promote`
- **Synopsis**: `opendv ontology studio promote -s <session_id> [-w <workspace>] --workspace-sha256 <s> --head-sha256 <s> -r <rationale> [--acknowledge-historical-contract-warning]`
- **Key Constraints**: Rationale must be $\ge 10$ characters. OCC requires exact match of workspace and head SHA.
- **Exit Codes**: 0 (OK), 1 (`PROMOTION_RATIONALE_REQUIRED`), 2 (`INVALID_WORKSPACE_PATH`), 3 (`SESSION_NOT_FOUND`), 4 (`ONT_EVO_CONTRACT_VIOLATION`, `ONT_EVO_UNGROUNDED_CONCEPT`, `ONT_EVO_GROUNDING_VIOLATION`), 5 (`LINEAGE_ROOT_MISMATCH`, `ONT_EVO_CONCURRENT_MODIFICATION`), 6 (`LOCK_TIMEOUT`), 7 (`FORBIDDEN_ADMIN_REQUIRED`)

#### 16. `bundle export / import / clone`
- **Synopsis**:
  - `opendv ontology studio bundle export -s <session_id> -o <out_zip> [--acknowledge-confidential-source-export] [--export-rationale <str>]`
  - `opendv ontology studio bundle import -f <in_zip> [--acknowledge-unverified-import-attribution] [--acknowledgement-rationale <str>]`
  - `opendv ontology studio bundle clone -s <session_id> [--new-session-id <id>] [-l <label>]`
- **Exit Codes**: 0 (OK), 1 (`EXPORT_RATIONALE_REQUIRED`, `IMPORT_GROUNDING_RATIONALE_REQUIRED`), 2 (`INVALID_SESSION_IDENTIFIER`, `SYNTAX_INVALID_ARGUMENT`), 3 (`SESSION_NOT_FOUND`), 4 (`ARCHIVE_SECURITY_VIOLATION`, `IMPORT_DELTA_REPLAY_HASH_MISMATCH`, `IMPORT_GROUNDING_VIOLATION`), 6 (`SESSION_ALREADY_EXISTS`, `LOCK_TIMEOUT`), 7 (`FORBIDDEN_ADMIN_REQUIRED`)

---

## 6. Web Workbench Studio UI Specification (`DESIGN.md` 4-Tab Shell)

### 6.1 Architecture, Open-Access Launch & Routing (`D18`, `D19`, `D26`, `D28`)
The unified Web Workbench in `apps/opendv-web-workbench` implements the warm editorial [`DESIGN.md`](../DESIGN.md) token system (`#faf9f5` cream canvas, `#efe9de` cream cards, `#cc785c` coral CTAs, `#181715` dark obsidian chrome, weight-`400` `Instrument Serif` + `DM Sans` + `JetBrains Mono`) and a `54px` 4-tab topbar:
- **4 Top-Level Navigation Tabs (`ExplorerTopBar.tsx`, `url-router.ts`)**:
  1. **`Ontology & Pipeline`** (`/`, `/ontology`, `/builder` $\to$ `OntologyWizardShell.tsx`, replacing the retired standalone legacy screens `D19`)
  2. **`DV Schema Explorer`** (`/dv-schema` $\to$ `DvSchemaExplorerView.tsx`)
  3. **`Run Explorer`** (`/explorer`, `/stages/00..07` $\to$ `PipelineOverviewView` / `StageDrillDownView`)
  4. **`Settings`** (`/settings` $\to$ `PlatformSettingsView` with shared `<AgentHarnessConfigCard />`)
- **Top-Bar Live Telemetry**: `● Agent Ready` / warning pill, live **cost meter** pill (cumulative tokens & USD, `D21`), **`Logs & Traces (<count>)`** button opening `SessionLogsInspector`, and dark session digest pill (`pill-dark`).
- **Loopback Zero-Auth & Remote Banner (`D18`)**: `opendv serve --open` opens `http://127.0.0.1:8420/` directly with zero login tokens or `#token=` URL fragments; non-loopback `--host` is refused with exit code `40` unless `--allow-remote-unauthenticated` is passed (surfacing a persistent coral `RemoteUnauthenticatedBanner`).
- **Agent-Only UI (`D28`) & New-Schema Sessions (`D25`)**: Neither the Wizard nor `Settings` exposes a deterministic mode toggle. `GET /sessions?wizard_only=true` lists only `wizard_schema_version` sessions plus `legacy_session_count` rendered as a single CLI export notice line.
- **Desktop-First Responsive Behavior (`D26`)**: Full editing density at `1440×920` and `1024×768`; below `768px` (verified at `390×844`) the layout stacks cleanly with zero horizontal overflow (`scrollWidth <= clientWidth`), collapsible navigation/stepper, and **view-only** graph & SQL runner modes.
- **State Management (`D16`)**: Zustand `studio-store` decomposed into `< 5,000`-token domain slices (`session-slice.ts`, `sources-preview-slice.ts`, `agent-discovery-slice.ts`, `graph-editor-slice.ts`, `pipeline-execution-slice.ts`, `session-logs-slice.ts`, etc.).

### 6.2 Unified 6-Step Wizard (`src/ontology/wizard/`)

| Step | Component & Screen (`01..06`) | Key Capabilities & Contracts |
|---|---|---|
| **Step 1** | `Step1ScopeAndBaseline.tsx` (`01`) | Construct New (Blank `v0.0.0`, Workspace Seed, 6 Asset-Management Starter Packs / `all_domains`) or Evolve Existing (new-schema `wizard_schema_version` sessions or `.yaml`/`.json`/`.ttl` import via `/sessions/import-ontology`, `D25`). |
| **Step 2** | `Step2SourcesAndNotes.tsx` (`02`) | Structured Connectors (`SQLite`, `DuckDB`, `Databricks UC`, `CSV`/`Parquet`/`XLSX`/`JSONL` via `/sources/connectors`, `P1`), Multipart Upload (`ocr=auto|force|off`, `do_not_send_to_agent`), and Inline Domain Notes (`/sources/text`). |
| **Step 3** | `Step3PreviewEvidence.tsx` (`03a`, `03b`) | **3a**: Inferred SQL types, PK/FK keys, surrogate hints, null%/distinctness stats, `<= 50` local sample rows (`/evidence/tables`, `D6`). **3b**: Parsed chunks, `[start_char, end_char)` spans, embedded `table_data` (`/evidence/chunks`). Includes **`Fast-Forward to Step 6`** (`D8`). |
| **Step 4** | `Step4AgentDiscovery.tsx` (`04a`, `04b`, `04c`) | **4a**: Inline `<AgentHarnessConfigCard />` (`P2`) when unconfigured (`422 AGENT_NOT_CONFIGURED`). **4b**: Single-pass `@term-scout -> @domain-clarifier -> @ontology-architect` (`D1`), Beyond-FIBO + `broadMatch` (`D3`), `1x · Semantic` doc rules (`D4`), unchecked `MAJOR · Opt-In` (`D7`), cascading checkboxes & inline edits (`D5`), `Q1..Qn` live cascade, and **`Ask FIBO`** (`AskFiboPanel`, `D2`). **4c**: `CitationInspectorDrawer` (`716×920` slide-over resolving spans, table stats, and `fibo-core` definitions). |
| **Step 5** | `Step5DiffGraphExport.tsx` (`05`) | Visual diff (`+FIBO`, `+Custom`, `~Enriched`), warm dot-grid `elkjs` + `@xyflow/react` DAG (3-tier LOD), optional **`VisualEditorDrawer`** (`+ Add / Edit Concept`, `D15`), **`Export Ontology Only`** (`.yaml`/`.json`/`.ttl`, `D9`), and atomic CAS commit. |
| **Step 6** | `Step6PipelineBuildAndRun.tsx` (`06`) | Auto-bound `deployed_contract` + Baseline Picker (`D12`), `DuckDB` / `Databricks Delta` dialect switch, live **7-pill (`00..06`) console** (projected over internal `STAGE_00..07`, [`docs/28 §4.4`](28-unified-ontology-pipeline-design-and-plan.md)), **5-Zone (`Z0–Z4`)** SQL preview (`bronze`, `staging`, `raw_vault`, `business_vault`, `information_mart`), inline **`StewardHoldModal`** (`D14`) & **`BudgetHoldCard`** (`D21`), **`↺ Sync Drift`** to Step 4 (`D10`), and **`Explore Generated DV 2.0 Schema ➔`**. |

### 6.3 4-View DV Schema Explorer (`/dv-schema`, `06b`, `06c`, `D11`)
Rendered by `DvSchemaExplorerView.tsx` (`src/explorer/dv-schema/`) backed by `GET /api/v1/runs/{run_id}/dv-schema`:
1. **View 1 — Interactive DV 2.0 Graph (`DvBipartiteGraphTab.tsx`, `06b`)**: Bipartite Hub–Link–Satellite–PIT–Bridge DAG (`elkjs` + `@xyflow/react`) with Zone/PII/Delta filter pills, live search, and `DvTableLineageInspector` showing physical columns, `BKCC` tier, `Algorithm A.5` SQL expression, Gate `0–6` badges, and per-table DDL/DML `<dialog>`.
2. **View 2 — 100% Triage & Wave Diff (`DvTriageAndWaveDiffTab.tsx`, `06c`)**: Filterable `100%` column triage ledger (`DV-G4-002`) with CSV export plus multi-wave diff (`W1 -> W2 -> W3`: `EVO-01`, `EVO-02`, `EVO-03`, `EVO-07`).
3. **View 3 — Vault Rows & Safe SQL (`DvVaultRowsAndSqlQueryTab.tsx`, `D11`)**: Browses persisted DuckDB rows (`/tables/{table}/rows`) and runs bounded read-only SQL queries (`/query`) guarded by `validate_safe_sql()` allowlist (`SELECT`/`UNION` only, zero file/metadata scans). View-only below `768px` (`D26`).
4. **View 4 — 5-Zone SQL (`Z0–Z4`) & IR (`DvContractIrTab.tsx`)**: Per-zone (`Z0–Z4`, `information_mart`) `DuckDB` & `Databricks Delta` SQL bundles plus `final_contract.yaml` & `final_canonical_ir.json`.

### 6.4 Live Session & Per-Stage Logs Inspector (`SessionLogsInspector`, `07`, `D24`)
Opened globally via **`Logs & Traces (<count>)`** or contextually via **`View Stage Logs ↗`** / Step 6 console pills (`00..06` mapped to `STAGE_00..07`):
- Subscribes to `GET /api/v1/ontology/studio/sessions/{id}/logs/stream` over W3C SSE **`EventSource`** (`Last-Event-ID` gapless replay, `● Follow Live Tail` / `⏸ Pause Auto-Scroll`), displays live token & USD **cost meter** (`D21`), and filters by `wizard_step`, `stage_id`, `persona`, and `category`.
- Renders 4 deep tabs (`Masked Prompt & Context`, `Streaming / Structured LLM Response`, `Tool Calls & @toon_v1 Traces`, `Raw JSON Event`) and supports `.jsonl` log download (`50 MB` `.gz` rotation retained for the session lifetime and bundled in `.odvstudio.zip`, `D24`).
- Enforces single active job per session (`409 SESSION_JOB_ACTIVE`, `D27`), disabling start triggers with `Run in progress — view live logs` while keeping Cancel enabled.

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
