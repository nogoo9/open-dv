# 14 — End-to-End Consistency Review, Scenarios Audit, and Data Vault 2.0 Invariant Analysis

> **Target Repository:** `open-dv` (`main` branch, commit `b2f7f0f`)  
> **Audited Subsystems:**
> - `opendv-agent-harness` (8-stage pipeline, `AgyCliHarnessAdapter`, `ClaudeCliHarnessAdapter`, `StewardHoldManager`)
> - `opendv-axi-verifier` (6-gate verification pipeline, `alg_a1`–`alg_a9b` codemods, `@toon_v1` codec)
> - `opendv-duckdb-sandbox` (`compiler_4zone.py`, `dynamic_checks.py`, `adversarial_batches.py`, `pit_query_engine.py`)
> - `opendv-core` (`constants.py`, `hashing.py`, `models/canonical_ir.py`, `models/contract_v5.py`)
> - Platform CLI, API & Web Workbench (`opendv-cli`, `opendv-platform-api`, `opendv-web-workbench`)

---

## 1. Executive Summary

Open-DV implements an advanced **neuro-symbolic framework** for automated Data Vault 2.0 (DV 2.0) design and verification. Unlike naive LLM code generators, Open-DV strictly enforces an **Intermediate Representation (IR)-First Architecture**:
1. LLM agents (`VaultFirstMate`, `ScoutSource`, `ScoutUnstructured`, `ShipVault`) operate purely on high-level contracts (`NoMistakeDataVault2ContractV5` / `CanonicalIR`). Generative models are **cryptographically barred from authoring physical SQL directly**.
2. Deterministic symbolic compilers (`compiler_4zone.py`) transpile verified contracts into 4-zone idempotent DuckDB SQL.
3. Multi-gate static verifiers (`axi-dv`) and in-memory sandboxes (`duckdb`) validate bipartite graph topology, cryptographic hashing, grain uniqueness, and bi-temporal point-in-time reconstruction before any code is promoted to production.

This audit evaluates the system across **10 complex data input scenarios**, examines the routing of **unmatched fields against ontologies**, pinpoints actual codebase behavior against expected DV 2.0 standards, and outlines concrete bug fixes and enhancements for GitHub issue tracking.

---

## 2. Unmatched Fields & Ontology Discrepancy Routing Matrix

When live source data contains fields that cannot be matched to the declared Graph A ontology, Open-DV classifies the discrepancy at **Gate 0 (`STAGE_01_SCOUT_SOURCE`)** using `GraphBProfiler` and diffs them via **Algorithm A.9a**. The pipeline routes the unmatched field according to four primary cases and one unstructured multimodal case:

| Unmatched Field Case | Scenario Category | Stage & Detection Mechanism | Rule / Gate Triggered | Pipeline Resolution Action |
| :--- | :--- | :--- | :--- | :--- |
| **1. New / Unmapped Descriptive Column on a Known Entity** | **Scenario S2 (Branch A: Schema Drift)** | `STAGE_01` (Algorithm A.9a) $\to$ `STAGE_03` (`@ship-vault` Pass 1 Triage) | `EVO-01` (Added Column) & Algorithm A.7 Locks (`02-schema-stability-and-evolution.md`) | Classifies the field as `PAYLOAD_ATTRIBUTE` and isolates it into a new versioned Satellite (`sat_<entity>_v2`) or additive extension Satellite (`sat_<entity>_<ext>`) without mutating existing Hub/Link grains (`INV-03`, `INV-04`). |
| **2. Renamed Source Field (Semantic Match, Name Mismatch)** | **Scenario S2 (Branch A: Schema Drift)** | `STAGE_01` (GraphBProfiler stats/FD match) $\to$ `STAGE_04` (`@ship-vault` Pass 2) | `EVO-03` (Renamed Column) (`02-schema-stability-and-evolution.md`) | Maps `stg.unmatched_col AS ontology_col` in Zone 1 Staging views so Raw Vault DDL and `hash_diff` remain 100% invariant. |
| **3. System / Operational Noise Field (No Ontology Counterpart)** | **Scenario S2 $\to$ Scenario S1** | `STAGE_03_SHIP_VAULT_PASS1` (`01-core-modelling-spec.md`) | Pass 1 Column Triage (`SYSTEM_SURROGATE`) | Classifies the unmatched column as `SYSTEM_SURROGATE` (e.g., CDC watermarks, ETL batch IDs) and excludes it from business Hubs and Satellite `hash_diff` calculations. |
| **4. Unmatchable Business Key / Orphan Foreign Key (No Target Hub in Ontology)** | **Scenario S2 (Branch C) $\to$ Scenario S3 (Self-Healing / Halt)** | `STAGE_01` (Gate 0), `STAGE_05` (Gate 1 `opendv-axi-verifier`), or `STAGE_06` (Gate 5) | `DV-STR` / `DV-COL` or `DV-DYN-004` / `006` (`03-verification-and-agentic-architecture.md`) | Emits a `@toon_v1` diagnostic trace for `@ship-vault` to either spawn a new Hub/Same-As Link (`EVO-08`) or re-triage keys ($k=2$ retries); if strict ontology locking blocks new Hubs, halts with `FAILED_STATIC_GATES` and escalates to `WB-07` in `opendv-web-workbench`. |
| **5. Unmatched Field / Entity Extracted from Unstructured Docs** | **Scenario S4 (Steward Captain-Hold Governance)** | `STAGE_02_SCOUT_UNSTRUCTURED` (Algorithm A.9b 3-Tier Resolution) | Tier-U3 $\to$ Captain-Hold U1 or U4 (`04-unstructured-and-multimodal-dv2.md`) | Fails Tier-U1 (exact) and Tier-U2 (fuzzy) matching and triggers U1 (Unresolved Entity Identity) or U4 (Attribute State Ambiguity), pausing the record in Web Workbench view `WB-03` (`opendv-platform-api`) for human Data Steward approval or quarantine. |

---

## 3. Evaluation of the 10 Data Input Scenarios

### Scenario S1: Pure Cold Start ($N=1 \to N+1$)
- **Data Input Profile:** Clean single-source feed (e.g. `wave0_cold_start.yaml` with CRM customers & orders). No prior contract or physical vault exists.
- **Observed Codebase Behavior:**
  - `pipeline.py` executes Stage 01 profiling (`GraphBProfiler`).
  - Stage 04 synthesizes initial candidate contract `NoMistakeDataVault2ContractV5`.
  - Algorithm A.4 (`alg_a4_cold_start.py`) applies 3-Tier BKCC Authority Protocol. Tier-1 (`LEI`, `ISIN`) sets `bkcc_prefix=None`; Tier-3 sets `bkcc_prefix=<rec_src>`.
  - Gate 1 validates contract grain and concept URIs; Gate 2 rejects surrogate keys (`SURROGATE_KEY_REJECTED`); Gate 3 verifies bipartite DAG.
  - `compiler_4zone.py` generates Zone 0 DDL, Zone 1 staging views, Zone 2 raw vault DML, Zone 3 business vault, and Zone 4 PIT queries.
- **DV 2.0 Consistency Critique & Gaps:**
  - *Discrepancy:* In `constants.py`, `GLOBAL_BKCC = "GLOBAL"`. In PE reference models, Tier-1 keys use `'6:GLOBAL||...'`, whereas generic IR leaves `bkcc_prefix=None`, hashing `'6:LP-101'` without `'GLOBAL'`. This creates cross-model hash incompatibility.
  - *Isolated Hubs:* Gate 3 allows isolated Hubs without Links unless `check_isolated_hubs=True` is explicitly activated.

### Scenario S2: Multi-Source Entity Convergence
- **Data Input Profile:** Multiple sources feeding the same Hub with different BK formats/namespaces (e.g., Investran `LP-101` vs Legacy GP `LP-101`).
- **Observed Codebase Behavior:**
  - In Raw Vault, `hub_limited_partner` records distinct hash keys isolated by BKCC (`GLOBAL` vs `LEGACY_GP_ADMIN`).
  - Gate 1 enforces `ANTI_SILO_VIOLATION` preventing duplicate Hubs binding to the same ontology concept URI.
  - In Business Vault, `sal_limited_partner` (`hk_lp_master`, `hk_lp_duplicate`) converges identities, while `esat_sal_limited_partner` tracks validity periods.
- **DV 2.0 Consistency Critique & Gaps:**
  - *Staging BKCC derivation:* In `compiler_4zone.py`, BKCC literal is scoped at the table level rather than the `(table, column, target_hub)` tuple level, risking misapplication if one table feeds multiple Hubs with differing authorities.
  - *SAL Directionality:* Gate 3 lacks canonical sorting on `(hk_master, hk_duplicate)` to mathematically prevent reflexive `(A, A)` or duplicate inverted pairs `(B, A)`.

### Scenario S3: Completely Unmapped Feed / Absent Ontology Concept
- **Data Input Profile:** Source feed containing tables or entities completely absent from Graph A ontology.
- **Observed Codebase Behavior:**
  - `pipeline.py` (`_resolve_hub_and_uri`) assigns fallback provisional URI `urn:ontology:provisional:entity:<slug>`.
  - Stage 05 Gate 1 strictly fails `DV-G4-001` (`Hub references unknown ontology_concept_uri`).
  - Emits `@toon_v1` trace, attempts self-healing, and transitions to `PAUSED_HOLD` (`ASK-USER`), generating a durable `StewardHoldRecord` in `hold_manager.py`.
- **DV 2.0 Consistency Critique & Gaps:**
  - If columns are marked `OMITTED_INTENTIONALLY` in column triage, they are excluded from Zone 1 & Zone 2 SQL. If Bronze staging is transient, raw audit bytes for omitted columns are lost unless landing stores raw JSON.

### Scenario S4: Upstream Schema Drift
- **Data Input Profile:** Wave 2 schema drift (`wave2_schema_drift.yaml`): new columns added (`kyc_aml_risk_tier`), column types widened, columns soft-deprecated.
- **Observed Codebase Behavior:**
  - Algorithm A.9a detects drift and applies non-breaking codemods:
    - New columns $\to$ Additive companion satellite (`sat_lp_core_ext`).
    - Soft-deprecated columns $\to$ Projected as `CAST(NULL AS <type>)` in staging views.
    - Renamed columns $\to$ Aliased in staging via Levenshtein distance ($\ge 0.85$).
  - Algorithm A.7 Evolution Lock verifies invariants (`DV-EVO-001` through `DV-EVO-008`).
- **DV 2.0 Consistency Critique & Gaps:**
  - *Physical DDL Execution Gap:* Zone 0 DDL emits `CREATE TABLE IF NOT EXISTS`. If an in-place column addition is declared, `compiler_4zone.py` generates no `ALTER TABLE ADD COLUMN` DDL, causing Zone 2 `INSERT` statements to fail on existing tables.

### Scenario S5: Nullable Foreign Key / 0..1 Relationship
- **Data Input Profile:** Optional foreign keys in relationships (e.g. fund investment without co-investment PortCo, or order without promo code).
- **Observed Codebase Behavior:**
  - Algorithm A.4 (`alg_a4_cold_start.py` lines 288–374) splits $N$-ary Links ($\text{arity} \ge 3$) when non-null ratio $U_{nn} < 0.95$ into a base Link and a filtered companion Link (`lnk_*_co_invest` with `WHERE <fk> IS NOT NULL`).
  - Gate 2 (`DV-G2-001`) rejects unsplit sparse FKs on $N$-ary Links.
- **DV 2.0 Consistency Critique & Gaps:**
  - **CRITICAL BOUNDARY GAP:** Algorithm A.4 line 294 explicitly checks `if len(refs) < 3: continue`. Binary links with sparse foreign keys ($U_{nn} < 0.95$) are **not** split or filtered. Staging assigns `'-1:^^'`, inserting rows linking parent to Zero Key (`0x00..00`), creating phantom synthetic relationship clusters.

### Scenario S6: Retroactive / Out-of-Order Event Arrival
- **Data Input Profile:** Late-arriving valuation restatements (e.g. Q3 valuation restated on Nov 15 with earlier effective date).
- **Observed Codebase Behavior:**
  - Satellites record both `applied_dts` (business effective date) and `load_dts` (system ingestion date).
  - PIT query engine (`pit_query_engine.py`) evaluates `load_dts <= as_of_dts`, successfully reconstructing historical reality at Oct 15 vs Nov 15 (Adversarial Batch 4 passes).
- **DV 2.0 Consistency Critique & Gaps:**
  - In generic `compiler_4zone.py` Zone 2 DML, satellite delta detection partitions by `hk` and orders strictly by `load_dts DESC`. A late-arriving restatement with an older `load_dts` is dropped by `src.load_dts > cur.load_dts`, even if `applied_dts` is novel.

### Scenario S7: Malformed / Adversarial Raw Data
- **Data Input Profile:** Duplicate BKs in micro-batch, whitespace/null BKs, SQL injection payloads (`' OR 1=1; --`).
- **Observed Codebase Behavior:**
  - Zone 2 DML groups by BK columns (`GROUP BY src.{hk}, {src_bk_csv}`) and uses `WHERE NOT EXISTS`, deduplicating micro-batches.
  - Length-prefixed SHA-256 (`<len>:<val>`) prevents delimiter injection collisions (`['AB', 'C']` vs `['A', 'BC']`).
  - Identifier regex `^[A-Za-z_][A-Za-z0-9_]*$` and SQLGlot AST prevent dynamic SQL injection.
- **DV 2.0 Consistency Critique & Gaps:**
  - **CRITICAL BUG: DuckDB `STRLEN()` vs. UTF-8 `OCTET_LENGTH()`:**
    In `compiler_4zone.py` line 112:
    `CAST(STRLEN({nullif_expr}) AS VARCHAR) || ':' || {nullif_expr}`
    DuckDB's `STRLEN()` returns Unicode character count, whereas Python `len(s.encode('utf-8'))` in `hashing.py` returns UTF-8 byte length. For multi-byte characters (e.g. `'ä'`), DuckDB produces `'20:...'` while Python produces `'21:...'`, causing **cryptographic hash mismatch between Python and DuckDB**!
  - **Case Divergence:** Python `hashing.py` produces uppercase hex (`.hexdigest().upper()`), whereas DuckDB SQL produces lowercase hex (`LOWER(SHA256(...))`).

### Scenario S8: Unstructured Document Ingestion
- **Data Input Profile:** PDF side letters, investment memos, LLM extracted entities and terms.
- **Observed Codebase Behavior:**
  - Documents identified by SHA-256 content digest in `hub_unstructured_document`.
  - Algorithm A.9b enforces Rule U1 (no blobs/vectors in raw satellites), Rule U2 (character span quote exactness), Rule U3 (AI provenance satellite recording model ID and confidence).
  - 3-Tier ER routing routes low-confidence or ambiguous entities to `WB-03` Captain-Hold.
- **DV 2.0 Consistency Critique & Gaps:**
  - Zone 2 DML in `compiler_4zone.py` does not include `WHERE steward_gate_status = 'APPROVED'`. If compiled outside the pipeline harness, unapproved Tier-U3 links could be loaded into Raw Vault.

### Scenario S9: Multi-Tenant / Multi-Unit Collisions
- **Data Input Profile:** Same natural key (e.g. account `1001`) originating from different tenants or business units.
- **Observed Codebase Behavior:**
  - BKCC namespaces prefix keys during Algorithm A.5 hashing (`<bkcc_len>:<bkcc>||<bk_len>:<bk>`), isolating hash keys in Hubs.
- **DV 2.0 Consistency Critique & Gaps:**
  - `compiler_4zone.py` only accepts static `bkcc_literal`. If a single multi-tenant feed contains rows with a dynamic `tenant_id` column, the compiler cannot prefix hashes with that column unless modeled manually as a composite business key.

### Scenario S10: Ghost Record & Unknown Value Handling
- **Data Input Profile:** Missing parent keys in links, outer joins in analytical queries.
- **Observed Codebase Behavior:**
  - Hubs and Links seeded with Dual Zero Keys: `'0'*64` (Unknown, token `-1`) and `'f'*64` (Error, token `-2`).
  - Satellites seeded with Ghost Record (`'0'*64`, `1900-01-01 00:00:00`, `SYSTEM_GHOST`).
  - `DV-DYN-007` dynamic check validates ghost completeness. PIT table queries use pure `INNER JOIN`s without row loss.
- **DV 2.0 Consistency Critique & Gaps:**
  - Staging SQL in `compiler_4zone.py` only branches on `all_null_pred` to `REPEAT('0', 64)`. It does not automatically generate a branch for `ZERO_KEY_ERROR` (`'f'*64`) for inapplicable keys, defaulting them to null tokens instead.

---

## 4. Priority Remediation Issues & Action Plan

| Issue ID | Severity | Subsystem & File Location | Description & Root Cause | Proposed Fix | GitHub Issue |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **BUG-01** | **CRITICAL** | `opendv-duckdb-sandbox/compiler_4zone.py` (lines 112, 750, 787, 852, 1013) | `STRLEN()` in DuckDB counts characters, while Python `hashing.py` counts UTF-8 bytes. Diverges on multi-byte characters. | Replace `STRLEN(...)` with `OCTET_LENGTH(...)` across `compiler_4zone.py`. | [#30](https://github.com/nogoo9/open-dv/issues/30) |
| **BUG-02** | **HIGH** | `opendv-axi-verifier/codemods/alg_a4_cold_start.py` (line 294) | `if len(refs) < 3: continue` skips binary links with sparse foreign keys ($U_{nn} < 0.95$), generating synthetic zero-key relationships. | Extend Algorithm A.4 and Gate 2 to binary links: filter `WHERE fk IS NOT NULL` or demote FK to satellite attribute. | [#31](https://github.com/nogoo9/open-dv/issues/31) |
| **BUG-03** | **HIGH** | `opendv-duckdb-sandbox/compiler_4zone.py` (Zone 2 templates) | Zone 2 DML lacks approval filter for unstructured extraction links. | Inject `WHERE steward_gate_status = 'APPROVED'` into Zone 2 DML for unstructured links. | [#32](https://github.com/nogoo9/open-dv/issues/32) |
| **BUG-04** | **MEDIUM** | `opendv-axi-verifier/verifiers/alg_a9b_unstructured.py` (line 107) | Windows CRLF line endings cause 1-character-per-line shifts, spuriously triggering `SPAN_HALLUCINATION`. | Explicitly normalize newlines (`replace('\r\n', '\n')`) prior to span verification. | [#33](https://github.com/nogoo9/open-dv/issues/33) |
| **BUG-05** | **MEDIUM** | `opendv-duckdb-sandbox/compiler_4zone.py` (Zone 2) | Satellite delta detection uses strict `src.load_dts > cur.load_dts`, rejecting late-arriving restatements with older load timestamps. | Window against adjacent state in business effective time (`applied_dts`) rather than strictly by `load_dts`. | [#34](https://github.com/nogoo9/open-dv/issues/34) |
| **BUG-06** | **MEDIUM** | `opendv-core/hashing.py` & `compiler_4zone.py` | Hash hex casing divergence: `.upper()` in Python vs `LOWER(...)` in DuckDB. | Harmonize all SHA-256 digests to lowercase hex strings across Python, IR, and SQL. | Tracked |
| **BUG-07** | **MEDIUM** | `opendv-duckdb-sandbox/compiler_4zone.py` (Zone 0) | Missing `ALTER TABLE ADD COLUMN` migration DDL for non-breaking in-place schema evolution. | Emit idempotent `ALTER TABLE <sat> ADD COLUMN IF NOT EXISTS ...` in `zone_0_migration_sql`. | Tracked |
| **BUG-08** | **MEDIUM** | `opendv-duckdb-sandbox/compiler_4zone.py` (Zone 1) | BKCC literal is scoped per table rather than `(table, column, target_hub)`. | Scope staging BKCC mapping per `(table, column, target_hub)` tuple. | Tracked |

