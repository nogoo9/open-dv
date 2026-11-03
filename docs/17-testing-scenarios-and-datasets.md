# `open-dv` Testing Scenarios, Datasets & Verification Ledger (`docs/17-testing-scenarios-and-datasets.md`)

> Normative catalog of end-to-end and unit test datasets, scenarios (`S1`–`S10`), unmatched ontology field routing cases (`UF-1`–`UF-5`), canonical test vectors, and zero-mock verification commands.

---

## Repository Testing Scenarios & Datasets Catalog

| Scenario / Suite | Dataset & Fixture Path | Governing Requirements & Invariants | Primary Test Suite & CLI Entrypoint |
| :--- | :--- | :--- | :--- |
| **S1: Day-1 Cold Start ($N=1$)** | [`examples/pipeline_samples/global_commerce_vault/wave0_cold_start.yaml`](../examples/pipeline_samples/global_commerce_vault/wave0_cold_start.yaml) (`sources/crm_customers_w0.csv`, `sources/erp_orders_w0.csv`) | `FR-DOM-01`, `INV-01..06`, `KEY-01..05`, `DV-G4-002` (100% Column Triage), `Algorithms A.1`, `A.4`, `A.5` | `uv run opendv pipeline validate --input examples/pipeline_samples/global_commerce_vault/wave0_cold_start.yaml --harness-config examples/pipeline_samples/global_commerce_vault/harness_config_agy.yaml --json` |
| **S2: Multi-Source Entity Convergence & Composite-Feed Scoped BKCC (Issue [#39](https://github.com/nogoo9/open-dv/issues/39))** | [`examples/pipeline_samples/scenario_s2_issue39_bkcc/`](../examples/pipeline_samples/scenario_s2_issue39_bkcc/) (`src_composite_deals.csv`, `src_global_issuers.csv`, `src_local_companies.csv`, `src_company_convergence.csv`, `dv_s2_issue39_contract.yaml`) & [`examples/pipeline_samples/global_commerce_vault/wave1_incremental_billing.yaml`](../examples/pipeline_samples/global_commerce_vault/wave1_incremental_billing.yaml) | `FR-DOM-02`, `FR-INT-01`, `KEY-04`, `ANTI_SILO_VIOLATION`, `EVO-08`, `Algorithm A.4` & `A.5` (`BUG-08`) | `uv run pytest packages/opendv-duckdb-sandbox/tests/test_compiler_and_4batch_sandbox.py -k "test_issue_39" -v` |
| **S3: Unmapped Feed / Absent Ontology Concept** | `packages/opendv-axi-verifier/tests/test_codemods_and_static_gates.py` & `packages/opendv-agent-harness/tests/test_hold_manager_and_pipeline.py` | `FR-DOM-04`, `DV-G4-001` (Ontology Firewall), `ASK-USER` / `STEWARD_HOLD` | `uv run pytest packages/opendv-axi-verifier/tests/ packages/opendv-agent-harness/tests/ -v` |
| **S4: Upstream Schema Drift ($S_k(t) \to S_k(t+1)$)** | [`examples/pipeline_samples/global_commerce_vault/wave2_schema_drift.yaml`](../examples/pipeline_samples/global_commerce_vault/wave2_schema_drift.yaml) (`sources/crm_customers_w2_drifted.csv`) | `FR-DOM-03`, `EVO-01..08`, `DV-EVO-001..008`, `Algorithms A.7`, `A.9a` | `uv run pytest packages/opendv-axi-verifier/tests/test_codemods_and_static_gates.py -k "drift or evolution" -v` |
| **S5: Nullable Foreign Key / $0..1$ Relationship ($U_{\text{nn}} < 0.95$)** | [`examples/private_equity/`](../examples/private_equity/) (`lnk_capital_call_co_invest`) & `packages/opendv-axi-verifier/tests/test_codemods_and_static_gates.py` | `FR-DOM-01`, `DV-G2-001`, `Algorithm A.4` (Sparse FK Companion Link Split) | `uv run pytest packages/opendv-axi-verifier/tests/test_codemods_and_static_gates.py -k "a4 or gate2" -v` |
| **S6: Retroactive / Out-of-Order Bi-Temporal Arrival** | [`examples/private_equity/dv_pe_model_contract.yaml`](../examples/private_equity/dv_pe_model_contract.yaml) & [`examples/private_equity/pe_canonical_ir.json`](../examples/private_equity/pe_canonical_ir.json) (Adversarial Batch 4 Q3 valuation restatement) | `FR-DOM-03`, `FR-VER-02`, `TMP-01..04`, `DV-DYN-009`, `Queries D.10` & `D.10b` | `uv run opendv sandbox --local --contract examples/private_equity/dv_pe_model_contract.yaml --canonical-ir examples/private_equity/pe_canonical_ir.json --query-type ADVERSARIAL_BATCHES_1_TO_4` |
| **S7: Malformed / Adversarial Raw Data & Hash Parity** | `packages/opendv-core/tests/test_core_models_and_hashing.py` & `packages/opendv-duckdb-sandbox/tests/test_compiler_and_4batch_sandbox.py` (Batches 1–3) | `FR-VER-01`, `Algorithm A.5` (6-Stage Length-Prefixed SHA-256), `Algorithm A.6` (`DV-AST-001..006`) | `uv run pytest packages/opendv-core/tests/ packages/opendv-duckdb-sandbox/tests/ -v` |
| **S8: Unstructured Document Ingestion & Span Grounding** | [`examples/pipeline_samples/global_commerce_vault/wave3_unstructured_msa.yaml`](../examples/pipeline_samples/global_commerce_vault/wave3_unstructured_msa.yaml) (`sources/enterprise_msa_addendum_2026.txt`) | `FR-DOM-05`, `Rules U1–U4`, `Tier-U1..U3`, `Algorithm A.9b` (`SPAN_HALLUCINATION`) | `uv run pytest packages/opendv-axi-verifier/tests/test_codemods_and_static_gates.py -k "unstructured or a9b" -v` |
| **S9: Multi-Tenant / Multi-Unit Keyspace Collisions** | [`examples/dv_model_contract.yaml`](../examples/dv_model_contract.yaml), [`examples/private_equity/`](../examples/private_equity/) (`GLOBAL` vs `LEGACY_GP_ADMIN`), & [`examples/pipeline_samples/scenario_s2_issue39_bkcc/`](../examples/pipeline_samples/scenario_s2_issue39_bkcc/) | `FR-DOM-02`, `KEY-04` (3-Tier `BKCC` Namespace Isolation), `Algorithm A.4` & `A.5` | `uv run opendv verify --local --contract examples/private_equity/dv_pe_model_contract.yaml --graph-a examples/private_equity/pe_graph_a_ontology.yaml --graph-b examples/private_equity/pe_graph_b_profile.json` |
| **S10: Ghost Record & Dual Zero-Key Completeness** | [`examples/private_equity/`](../examples/private_equity/) & [`examples/pipeline_samples/scenario_s2_issue39_bkcc/`](../examples/pipeline_samples/scenario_s2_issue39_bkcc/) (Adversarial Batch 1 & Batch 2) | `FR-DOM-01`, `FR-VER-02`, `KEY-02b`, `DV-DYN-003` (Replay Idempotency), `DV-DYN-007` (Zero/Ghost Seed) | `uv run pytest packages/opendv-duckdb-sandbox/tests/test_duckdb_sandbox_suite.py -v` |

---

## Issue #39 — Scenario S2 (Multi-Source Entity Convergence & Scoped BKCC)

- **GitHub Issue**: [#39 — `fix(compiler_4zone): scope staging BKCC mappings per (table, column, target_hub) tuple`](https://github.com/nogoo9/open-dv/issues/39)
- **Audit ID & Severity**: `BUG-08` (`MEDIUM`)
- **Governing Functional Requirements**: `FR-DOM-02` (Multi-Source Entity Convergence & Cross-Source Integration), `FR-INT-01` (Deterministic Canonical IR & 4-Zone SQL Compilation)
- **DV 2.0 Invariants & Algorithms**: `KEY-04` (Cross-System Collision Prevention), `Algorithm A.4` (3-Tier BKCC Authority), `Algorithm A.5` (6-Stage Length-Prefixed Canonical Pre-Hash Serialization), `Algorithm A.6` (`DV-AST-001`–`006` SQLGlot AST Linter)

### 1. Scenario & Architectural Context

Scenario S2 exercises both **multi-source entity convergence ($N=1 \to N+1$)** and **composite upstream feeds containing mixed-authority business keys**:

1. **Composite Raw Feed (`dealcloud.src_composite_deals`)**:
   - `lei_code` maps to `hub_company` under **`TIER_1_GLOBAL_STANDARD`** (`bkcc_prefix: 'GLOBAL'`).
   - `internal_deal_id` maps to `hub_deal` under **`TIER_3_LOCAL_SCOPED`** (`bkcc_prefix: 'DEALCLOUD_LOCAL'`, `require_src_qualifier: true`).
   - Both columns also participate in `lnk_deal_company` (`hk_deal`, `hk_company`) from the same composite feed.
2. **Passive Global Convergence (`bloomberg.src_global_issuers`)**:
   - `lei_code` maps to `hub_company` under **`TIER_1_GLOBAL_STANDARD`**, passively converging with `dealcloud.src_composite_deals.lei_code` onto identical `hk_company` hash keys without creating duplicate Hub entries.
3. **Isolated Local Convergence + Same-As Link (`legacy_crm.src_local_companies` + `mdm.src_company_convergence`)**:
   - `legacy_co_num` maps to `hub_company` under **`TIER_3_LOCAL_SCOPED`** (`bkcc_prefix: 'LEGACY_CRM_LOCAL'`), isolating local CRM numbers from global LEIs.
   - `mdm.src_company_convergence` feeds `sal_company` (`SAME_AS_LINK`), where `master_lei_code` (`role_alias: 'MASTER'`) resolves to `(mdm.src_company_convergence, master_lei_code, hub_company) -> 'GLOBAL'` and `duplicate_legacy_co_num` (`role_alias: 'DUPLICATE'`) resolves to `(mdm.src_company_convergence, duplicate_legacy_co_num, hub_company) -> 'LEGACY_CRM_LOCAL'`.

### 2. Test Dataset Layout (`examples/pipeline_samples/scenario_s2_issue39_bkcc/`)

| File Path | Role | Key Columns & Authority Tiers |
| :--- | :--- | :--- |
| `data/src_composite_deals.csv` | Composite DealCloud feed (2 rows) | `lei_code` (`TIER_1_GLOBAL_STANDARD` $\to$ `hub_company`), `internal_deal_id` (`TIER_3_LOCAL_SCOPED`, `DEALCLOUD_LOCAL` $\to$ `hub_deal`), `company_legal_name`, `deal_stage` |
| `data/src_global_issuers.csv` | Bloomberg global issuer feed (2 rows) | `lei_code` (`TIER_1_GLOBAL_STANDARD` $\to$ `hub_company`), `credit_rating_code` |
| `data/src_local_companies.csv` | Legacy CRM local company feed (2 rows) | `legacy_co_num` (`TIER_3_LOCAL_SCOPED`, `LEGACY_CRM_LOCAL` $\to$ `hub_company`), `local_company_name` |
| `data/src_company_convergence.csv` | MDM Same-As crosswalk feed (2 rows) | `master_lei_code` (`GLOBAL` $\to$ `sal_company.hk_company_master`), `duplicate_legacy_co_num` (`LEGACY_CRM_LOCAL` $\to$ `sal_company.hk_company_duplicate`), `confidence_score` |
| `graph_a_ontology.yaml` | Domain Ontology (`Graph A`) | `urn:ontology:entity:company` (`hub_company`), `urn:ontology:entity:deal` (`hub_deal`), `lnk_deal_company`, `sal_company` |
| `graph_b_profile.json` | Empirical Source Profile (`Graph B`) | Column uniqueness, non-null, Shannon entropy, and candidate role metrics for all 4 tables |
| `dv_s2_issue39_contract.yaml` | `NoMistakeDataVault2ContractV5` Contract | 2 Hubs, 2 Links (`STANDARD_LINK` + `SAME_AS_LINK`), 5 Satellites, 11-entry `column_triage_ledger` |

### 3. Running the Unit & End-to-End Tests

```bash
# 1. Run dedicated non-mocked unit tests for Issue #39
uv run pytest packages/opendv-duckdb-sandbox/tests/test_compiler_and_4batch_sandbox.py \
  -k "test_issue_39" -v

# 2. Run 6-Gate static + dynamic verification via the opendv CLI
uv run opendv verify --local \
  --contract examples/pipeline_samples/scenario_s2_issue39_bkcc/dv_s2_issue39_contract.yaml \
  --graph-a examples/pipeline_samples/scenario_s2_issue39_bkcc/graph_a_ontology.yaml \
  --graph-b examples/pipeline_samples/scenario_s2_issue39_bkcc/graph_b_profile.json

# 3. Compile to Canonical IR & 4-Zone SQL and run the 4-Batch Adversarial DuckDB Sandbox
uv run opendv compile --local \
  --contract examples/pipeline_samples/scenario_s2_issue39_bkcc/dv_s2_issue39_contract.yaml

uv run opendv sandbox --local \
  --contract examples/pipeline_samples/scenario_s2_issue39_bkcc/dv_s2_issue39_contract.yaml \
  --query-type ADVERSARIAL_BATCHES_1_TO_4 \
  --cutoff "2026-11-15 23:59:59"
```

---

## Issue #37 — Scenario S7 (Cross-Engine Hash Parity)

### 1. Scenario Overview & Governing Requirements
- **GitHub Issue:** [#37 — `fix(core,compiler_4zone): harmonize SHA-256 hash hex output to lowercase across Python and DuckDB`](https://github.com/nogoo9/open-dv/issues/37) (`BUG-06`, Medium)
- **Governing Functional Requirements:** `FR-VER-01` (6-Gate Symbolic & Dynamic Verification Pipeline), `FR-INT-01` (Dual-Mode CLI & Headless REST/SSE Server Parity)
- **Mapped Scenario:** `S7` — Cross-Engine Hash Parity (`docs/00-mission-vision-and-functional-requirements.md` §5.2, §7; `docs/14-end-to-end-consistency-and-scenarios-audit.md` §3.1)
- **Enforced DV 2.0 Rules & Invariants:** `Algorithm A.5` (6-Stage Length-Prefixed Canonical SHA-256 Hashing), `DV-COL-005` (`CHAR(64)` SHA-256 hex digest typing), `DV-AST-002` (length-prefixed `||` concatenation), `DV-AST-003` (positional null token `'-1:^^'`), `DV-DYN-003` (orphan-free Link-to-Hub joins), `DV-DYN-005` (boundary-shift collision immunity), `DV-DYN-007` (cross-source `BKCC` isolation)

### 2. Test Dataset & Vectors
The Scenario S7 verification dataset exercises both the Private Equity reference contract (`examples/private_equity/dv_pe_model_contract.yaml`) and adversarial boundary/encoding vectors across Python (`opendv_core.hashing`), SQLGlot AST synthesis (`opendv_axi_verifier.codemods.alg_a5_sqlglot_hash`), and DuckDB 4-Zone SQL (`opendv_duckdb_sandbox.compiler_4zone`):

| Vector Category | Input Tokens / Columns | `is_business_key` | `bkcc_prefix` | Canonical Pre-Hash String (Stage 4) | Canonical Lowercase SHA-256 Digest (Stage 5, `[0-9a-f]{64}`) |
| :--- | :--- | :---: | :---: | :--- | :--- |
| **Tier-1/2 Hub BK (`CalPERS`)** | `['LP-101']` | `True` | `'GLOBAL'` | `'6:GLOBAL\|\|6:LP-101'` | `5d15eb10c8d7621d3167849c41c5f9cd9244fd79fb720cb17104d49e4b820ae4` |
| **Tier-3 Hub BK (`Bavarian Pension`)** | `['LP-101']` | `True` | `'LEGACY_GP_ADMIN'` | `'15:LEGACY_GP_ADMIN\|\|6:LP-101'` | `6b7c7180b62164f31f14eebcd16cc5fd2b0ce58143de66a4b67521113ec5d8b9` |
| **Tier-2 Hub BK (`Apex Buyout Fund IV`)** | `['APEX_BO_IV']` | `True` | `'GLOBAL'` | `'6:GLOBAL\|\|10:APEX_BO_IV'` | `94a60fcda278c5aec284c595413935793d5cec113c511f20690046061d388759` |
| **Boundary-Shift Pair A** | `['AB', 'C']` | `False` | `None` | `'2:AB\|\|1:C'` | Evaluated identically in Python, SQLGlot AST, and DuckDB (`!= Pair B`) |
| **Boundary-Shift Pair B** | `['A', 'BC']` | `False` | `None` | `'1:A\|\|2:BC'` | Evaluated identically in Python, SQLGlot AST, and DuckDB (`!= Pair A`) |
| **Embedded Delimiter Pair** | `['ANNE\|\|O', 'BRIEN']` vs `['ANNE', 'O\|\|BRIEN']` | `False` | `None` | `'7:ANNE\|\|O\|\|5:BRIEN'` vs `'4:ANNE\|\|8:O\|\|BRIEN'` | Disjoint lowercase `[0-9a-f]{64}` digests across all 3 engines |
| **Positional `NULL` Pair** | `['VAL_X', None, 'VAL_Y']` vs `['VAL_X', 'VAL_Y', None]` | `False` | `None` | `'5:VAL_X\|\|-1:^^\|\|5:VAL_Y'` vs `'5:VAL_X\|\|5:VAL_Y\|\|-1:^^'` | Disjoint lowercase `[0-9a-f]{64}` digests across all 3 engines |
| **Multi-Byte UTF-8 Payload** | `['München', '株式会社']` | `False` | `None` | `'8:München\|\|12:株式会社'` | UTF-8 byte length (`OCTET_LENGTH` / `STRLEN`) matches Python `len(s.encode('utf-8'))` |
| **Mandatory FK `NULL` / `'-1'` Zero Key** | `[None]` or `['-1']` | `True` | `'GLOBAL'` | *(Short-circuits to `ZERO_KEY_UNKNOWN`)* | `0000000000000000000000000000000000000000000000000000000000000000` |
| **Optional Inapplicable / `'-2'` Zero Key** | `[None]` (`is_optional_inapplicable=True`) or `['-2']` | `True` | `'GLOBAL'` | *(Short-circuits to `ZERO_KEY_ERROR`)* | `ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff` |

### 3. Running the Unit and End-to-End Tests
```bash
# 1. Run unit tests for Algorithm A.5 lowercase hex digests and Zero-Key parity in opendv-core
uv run --all-packages pytest packages/opendv-core/tests/test_core_models_and_hashing.py -v

# 2. Run SQLGlot AST hash recipe and codemod parity tests in opendv-axi-verifier
uv run --all-packages pytest packages/opendv-axi-verifier/tests/test_codemods_and_static_gates.py -k test_alg_a5_and_a6_sqlglot_hash_recipe_and_linter -v

# 3. Run Scenario S7 End-to-End Cross-Engine Hash Parity test in :memory: DuckDB
uv run --all-packages pytest packages/opendv-duckdb-sandbox/tests/test_compiler_and_4batch_sandbox.py -k test_issue37_scenario_s7_cross_engine_hash_parity_e2e -v

# 4. Run CLI E2E compile & 4-Batch Adversarial Shadow Sandbox on the Private Equity contract
uv run opendv compile --local \
  --contract examples/private_equity/dv_pe_model_contract.yaml \
  --canonical-ir examples/private_equity/pe_canonical_ir.json

uv run opendv sandbox --local \
  --contract examples/private_equity/dv_pe_model_contract.yaml \
  --canonical-ir examples/private_equity/pe_canonical_ir.json \
  --query-type ADVERSARIAL_BATCHES_1_TO_4 \
  --cutoff "2026-11-15 23:59:59"
```

---

## Issue #33 — Scenario S8 (Unstructured Document Ingestion), Unmatched Case UF-5

### 1. Scenario & Requirement Summary
- **GitHub Issue**: [#33 — `fix(alg_a9b): normalize CRLF line endings to prevent spurious span hallucination errors in Windows environments`](https://github.com/nogoo9/open-dv/issues/33)
- **Audit ID & Severity**: `BUG-04` (`MEDIUM`)
- **Governing Functional Requirement**: `FR-DOM-05` (*Unstructured & Multimodal Document Ingestion & 3-Tier ER Routing*)
- **Mapped Scenario & Unmatched Case**: Scenario `S8` (*Unstructured Document Ingestion*), Unmatched Case `UF-5` (*Unstructured document lacks `evidence_quote` or span offset `[char_start, char_end]` mismatch*)
- **DV 2.0 Invariant / Rule**: `Rule U2` (*Span-Grounded Provenance*), `Algorithm A.9b` (`SPAN_HALLUCINATION`)

### 2. Test Datasets & Fixtures
1. **Private Equity Unstructured PDF Corpus (CRLF-Encoded)**:
   - Contract: `examples/private_equity/dv_pe_model_contract.yaml`
   - Ontology (Graph A): `examples/private_equity/pe_graph_a_ontology.yaml`
   - Bronze Profile (Graph B): `examples/private_equity/pe_graph_b_profile.json`
   - Unstructured Documents (17 character-offset spans across 2 documents converted to Windows `\r\n` and legacy `\r` line endings):
     - `calpers_side_letter_fund_iv_2026.pdf` (1,215 LF chars / 1,226 CRLF chars; 9 extracted spans across `Tier-U1`, `Tier-U2`, and `Tier-U3`)
     - `ic_q3_valuation_nordic_cloud.pdf` (1,331 LF chars / 1,346 CRLF chars; 8 extracted spans across `Tier-U1`, `Tier-U2`, and `Tier-U3`)
2. **Global Commerce Vault Wave 3 Unstructured MSA Addendum (CRLF-Encoded on Disk + Multi-Line CRLF Quotes)**:
   - Pipeline Input: `examples/pipeline_samples/global_commerce_vault/pipeline_input.yaml` (Wave 0 + Wave 3)
   - Unstructured Document: `examples/pipeline_samples/global_commerce_vault/sources/wave3_unstructured_msa/enterprise_msa_addendum_2026.txt` written with Windows CRLF (`\r\n`) bytes on disk and multi-line CRLF `verbatim_quote` candidates (`ext_msa_counterparty_crlf_u1` at `Tier-U1` and `ext_msa_guarantor_crlf_u3` at `Tier-U3` `STEWARD_HOLD`).

### 3. Automated Tests (Zero Mocks)
- **Unit Tests (`packages/opendv-axi-verifier/tests/test_codemods_and_static_gates.py` & `tests/test_verification_gates_and_codemods.py`)**:
  - `test_issue_33_alg_a9b_crlf_and_cr_line_ending_normalization`: Verifies that `evaluate_unstructured_er_routing`, `verify_gate5_unstructured_invariants`, and `run_axi_dv_verification_pipeline` normalize `\r\n` and `\r` line endings on document bodies and multi-line quotes without false `SPAN_HALLUCINATION` errors.
  - `test_issue_33_gate5_deduplicates_span_errors_across_decisions`: Verifies that genuine fabricated quotes or mismatched offsets on CRLF documents still trigger `UNSTRUCTURED_VIOLATION [Gate 5 - SPAN_HALLUCINATION]` without duplicate error inflation across `decisions`.
  - `test_issue_33_doc04_and_doc05_reference_alg_a9b_crlf_normalization`: Verifies that the executable reference `verify_gate5_unstructured_invariants` blocks in `docs/04-unstructured-and-multimodal-dv2.md` and `docs/05-elm-calculus-and-reference-algorithms.md` normalize `\r\n` and `\r` line endings.
- **End-to-End Tests**:
  - `tests/test_multimodal_and_duckdb_e2e.py::test_issue_33_crlf_unstructured_document_ingestion_and_4zone_duckdb_e2e`: Executes all 17 PDF spans on CRLF document bodies, runs 6-Gate verification (`PASS`), compiles 4-Zone DuckDB SQL, runs the 4-Batch Adversarial Shadow Sandbox (`DV-DYN-001`–`015`), and verifies bi-temporal PIT non-retroactivity in `:memory:` DuckDB.
  - `packages/opendv-agent-harness/tests/test_agentic_pipeline.py::test_issue_33_wave3_crlf_unstructured_document_pipeline_e2e`: Executes `validate_pipeline_inputs`, `_handle_extract_pdf_spans`, and `run_agentic_vault_pipeline` across Wave 0 and Wave 3 with an on-disk CRLF MSA document and multi-line CRLF `verbatim_quote`, completing Stage 05 (Gate 5) and Stage 06 (4-Batch DuckDB Sandbox).

### 4. Execution Commands
```bash
# 1. Run Issue #33 unit tests in opendv-axi-verifier and docs/04-05 reference suite
uv run pytest -v packages/opendv-axi-verifier/tests/test_codemods_and_static_gates.py tests/test_verification_gates_and_codemods.py -k "test_issue_33"

# 2. Run Issue #33 6-Gate + 4-Zone DuckDB Sandbox E2E test
uv run pytest -v tests/test_multimodal_and_duckdb_e2e.py -k "test_issue_33"

# 3. Run Issue #33 Wave 0 -> Wave 3 Agentic Pipeline E2E test
uv run --all-packages pytest -v packages/opendv-agent-harness/tests/test_agentic_pipeline.py -k "test_issue_33"
```

---

## Issue #44 — Scenario S7 (Intra-Batch Deduplication)

- **GitHub Issue:** [#44 — `fix(axi-verifier): whitelist idempotent micro-batch deduplication aggregations in DV-AST-006`](https://github.com/nogoo9/open-dv/issues/44)
- **Audit ID & Severity:** `GAP-13` (`MEDIUM`)
- **Governing Functional Requirement:** `FR-VER-01` (Static Structural, Cryptographic, Grain & Evolution Verification — AST Anti-Pattern Linting)
- **Mapped Scenario:** Scenario S7 (Malformed / Adversarial Raw Data — Intra-Batch Deduplication)
- **Enforced Invariants & Rules:** `Algorithm A.6`, `DV-AST-006` / `DV-G6-005` (Raw Vault Aggregation Rule), `DV-DYN-001` (Hub & Link Primary Key Uniqueness)

### 1. Scenario & Dataset Description

When an operational source feed delivers multiple records with the **exact same natural business key** (e.g., `customer_code = 'CUST-S7-DUP-777'` or `order_number = 'ORD-S7-DUP-999'`) within a single micro-batch across different ingestion timestamps (`load_dts`) or record sources (`rec_src`), Zone 2 Raw Vault Hub and Link load DML (`compiler_4zone.py`) deduplicates the batch using:

```sql
INSERT INTO raw_vault.hub_customer (hk_customer, bkcc, customer_code, load_dts, rec_src)
SELECT
    src.hk_customer,
    src.bkcc,
    src.customer_code,
    MIN(src.load_dts) AS load_dts,
    ANY_VALUE(src.rec_src) AS rec_src
FROM staging.stg_hub_customer src
WHERE src.hk_customer NOT IN (
    '0000000000000000000000000000000000000000000000000000000000000000',
    'ffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffffff'
)
  AND NOT EXISTS (
      SELECT 1 FROM raw_vault.hub_customer h
      WHERE h.hk_customer = src.hk_customer
  )
GROUP BY src.hk_customer, src.bkcc, src.customer_code;
```

The Scenario S7 test dataset injects:
1. **3 duplicate Hub Customer records** for `('CRM_SFDC', 'CUST-S7-DUP-777')` arriving in the same micro-batch at `2026-10-01 05:30:00`, `2026-10-01 05:00:00` (earliest), and `2026-10-01 05:45:00`.
2. **2 duplicate Hub Sales Order records** for `'ORD-S7-DUP-999'` arriving at `2026-10-01 05:20:00` and `2026-10-01 05:05:00` (earliest).
3. **2 duplicate Link Customer-Order records** connecting `'CUST-S7-DUP-777'` and `'ORD-S7-DUP-999'` at `2026-10-01 05:25:00` and `2026-10-01 05:10:00` (earliest).

### 2. Expected Verification Outcomes

1. **Gate 6 Static SQLGlot AST Verification (`Algorithm A.6` / `DV-AST-006`):**
   - **Whitelisted (`PASS`):** Hub and Link `INSERT INTO ... SELECT ... GROUP BY <hk>, <bk_or_fk_cols>` statements that aggregate only `MIN(load_dts)` and `ANY_VALUE(rec_src)` (or `MIN(rec_src)`) with no `HAVING` clause.
   - **Rejected (`DV-AST-006 / DV-G6-005`):** Any Raw Vault query containing `HAVING`, any `GROUP BY` on a Satellite (`sat_*`, `mas_*`, `esat_*`), any `GROUP BY` that includes `load_dts` or `rec_src` as grouping keys, or any business metric / non-first-seen aggregation (`SUM`, `AVG`, `COUNT`, `MAX(load_dts)`).
2. **Gate 5 Dynamic DuckDB Shadow Sandbox (`DV-DYN-001`–`015`):**
   - `raw_vault.hub_customer` loads **1 row** for `'CUST-S7-DUP-777'` with `load_dts = '2026-10-01 05:00:00'`.
   - `raw_vault.hub_sales_order` loads **1 row** for `'ORD-S7-DUP-999'` with `load_dts = '2026-10-01 05:05:00'`.
   - `raw_vault.lnk_customer_order` loads **1 row** for the relationship with `load_dts = '2026-10-01 05:10:00'`.
   - All 15 dynamic invariants (`DV-DYN-001`..`015`) pass with zero primary key collisions and zero orphans.

### 3. How to Run the Unit & End-to-End Tests

```bash
# 1. Run the Algorithm A.6 / DV-AST-006 static AST unit tests
uv run pytest packages/opendv-axi-verifier/tests/test_codemods_and_static_gates.py \
  -k "alg_a6" -vv

uv run pytest tests/test_verification_gates_and_codemods.py \
  -k "algorithm_a6" -vv

# 2. Run the Scenario S7 End-to-End (Contract -> CanonicalIR -> 4-Zone SQL -> Gate 6 AST -> DuckDB Sandbox) test
uv run pytest packages/opendv-duckdb-sandbox/tests/test_duckdb_sandbox_suite.py \
  -k "test_scenario_s7_intrabatch_deduplication_ast_and_duckdb_e2e" -vv
```

---

## Issue #43 — Scenario S10 (Ghost Record & Zero Key Handling)

### 1. Overview & Governing Requirements
- **GitHub Issue**: [`#43`](https://github.com/nogoo9/open-dv/issues/43) (`GAP-12`, Severity: `MEDIUM`)
- **Functional Requirements**: `FR-DOM-01` (Automated Relational Source Discovery, Column Triage & Raw Vault Synthesis), `FR-VER-02` (Dynamic DuckDB Adversarial Shadow Sandbox & PIT Audit)
- **DV 2.0 Rules & Invariants**: `KEY-02b` (Pre-Hash Null & Sentinel Substitution), `DV-DYN-002` (Parent-Child Referential Integrity), `DV-DYN-007` (Ghost & Dual Zero-Key `INNER JOIN` Completeness)

### 2. Scenario & Test Dataset Specification
Scenario `S10` verifies that the 4-Zone DuckDB SQL compiler (`compiler_4zone.py`) implements a deterministic **3-way conditional hash branch** in `build_algorithm_a5_sha256_sql` and all Zone 1 staging views (`staging.stg_*`):
1. **Error / Inapplicable Zero Key (`ZERO_KEY_ERROR = 'f' * 64`)**: Triggered when a business key or foreign key token equals `'-2'` (`ZERO_KEY_ERROR_TOKEN`) or matches an explicit `error_predicate`.
2. **Unknown / Missing Zero Key (`ZERO_KEY_UNKNOWN = '0' * 64`)**: Triggered when a business key or foreign key token is `NULL`, empty/whitespace (`''`), equals `'-1'` (`ZERO_KEY_UNKNOWN_TOKEN`), or matches an explicit `unknown_predicate` (excluding constant `bkcc` namespace columns).
3. **Valid Business Key Digest**: Computed via Algorithm A.5 length-prefixed SHA-256 (`<octet_len>:<val>` with `||` delimiter and `BKCC` prefix).

#### Test Datasets Exercised
- **Private Equity Reference Vault (`dealcloud.src_portfolio_companies`)**:
  - `deal_id = 'PC-DEAL-01'`, `lei_code = '529900T8BM49AURSDO55'` $\to$ valid SHA-256 `hk_portco`
  - `deal_id = 'PC-NULL-FK'`, `lei_code = NULL` $\to$ `hk_portco = REPEAT('0', 64)` (`ZERO_KEY_UNKNOWN`)
  - `deal_id = 'PC-ERR-FK'`, `lei_code = '-2'` $\to$ `hk_portco = REPEAT('f', 64)` (`ZERO_KEY_ERROR`)
- **Generic Canonical IR Vault (`global_commerce_vault` / `bronze.src_hub_customer` & `bronze.src_lnk_customer_order`)**:
  - Hub & Satellite rows with `('GLOBAL', 'CUST-S10-101')`, `('GLOBAL', NULL)`, `('GLOBAL', '-1')`, and `('GLOBAL', '-2')`
  - Link rows with valid FK, `NULL` FK, `'-1'` FK, and `'-2'` FK

### 3. How to Run the Unit & End-to-End Tests

```bash
# 1. Run the unit tests for 3-way Zero-Key SQL branching in build_algorithm_a5_sha256_sql
uv run pytest packages/opendv-duckdb-sandbox/tests/test_compiler_and_4batch_sandbox.py \
  -k "test_algorithm_a5_sql_helper" -v

# 2. Run the Scenario S10 end-to-end DuckDB sandbox test (non-mocked)
uv run pytest packages/opendv-duckdb-sandbox/tests/test_duckdb_sandbox_suite.py \
  -k "test_scenario_s10_dual_zero_key_3way_branching_e2e" -v

# 3. Run the CLI end-to-end 4-Batch Adversarial DuckDB Shadow Sandbox verification
uv run opendv sandbox --local \
  --contract examples/private_equity/dv_pe_model_contract.yaml \
  --canonical-ir examples/private_equity/pe_canonical_ir.json \
  --query-type ADVERSARIAL_BATCHES_1_TO_4 \
  --cutoff "2026-11-15 23:59:59" \
  --json
```

---

## Issue #38 — Scenario S4 (Upstream Schema Drift), Unmatched Case UF-1

### 1. Scenario Overview & Architectural Traceability
- **GitHub Issue**: [#38 — `feat(compiler_4zone): generate zone_0_migration_sql with idempotent ALTER TABLE ADD COLUMN statements`](https://github.com/nogoo9/open-dv/issues/38)
- **Audit Finding**: `BUG-07` (`MEDIUM`)
- **Governing Functional Requirements**: `FR-DOM-03` (Intra-Source Schema Drift $S_k(t) \to S_k(t+1)$), `FR-INT-01` (4-Zone Physical SQL Compilation)
- **Mapped Scenario & Case**: Scenario `S4` (Upstream Schema Drift), Unmatched Case `UF-1` (Source field not in Graph A ontology, added in-place to an existing Satellite)
- **DV 2.0 Invariants & Rules**: `INV-01` (Insert-Only Raw Vault History), `EVO-01` / `EVO-01a` (Additive Satellite Evolution), `DV-EVO-006` (`SAT_HASHDIFF_INVALIDATED`), `DV-DYN-014` (Additive Schema Evolution Non-Destructiveness), `Algorithm A.6` (`DV-AST-001`), `Algorithm A.7` (Evolution Lock)

### 2. Test Dataset & Multi-Wave Evolution Setup
This scenario exercises an existing Raw Vault Satellite (`raw_vault.sat_customer_core` attached to `raw_vault.hub_customer`, sourced from `crm_salesforce.crm_customers`) evolving across two waves in the same physical DuckDB database:

1. **Wave 0 (`COLD_START_N1` at `load_dts = '2026-10-01 06:00:00'`)**:
   - **Source Feed**: `examples/pipeline_samples/global_commerce_vault/sources/wave0_cold_start/crm_customers.csv`
   - **Business Key**: `customer_tax_id` (`TIER_1_GLOBAL_STANDARD`, `GLOBAL` BKCC)
   - **Initial `sat_customer_core` Payload Columns**: `legal_name`, `country_iso2`, `account_segment`, `credit_limit_usd`
   - **Sample Seed Rows**:
     - `('TAX-US-900101', 'Apex Industrial Holdings LLC', 'US', 'ENTERPRISE', 1250000.00)`
     - `('TAX-DE-800202', 'Rheinland Precision GmbH', 'DE', 'ENTERPRISE', 980000.00)`

2. **Wave 2 (`MULTI_SOURCE_EXPANSION` / `SCHEMA_DRIFT` at `load_dts = '2026-11-01 06:00:00'`)**:
   - **Source Feed**: `examples/pipeline_samples/global_commerce_vault/sources/wave2_schema_drift/crm_customers_v2.csv`
   - **In-Place Added Columns (`UF-1`)**: `loyalty_tier_code` (`VARCHAR`), `annual_spend_band` (`VARCHAR`)
   - **Migration Policy**: `hash_diff_migration_policy: "NULL_EQUIVALENT_PADDING"`
   - **Sample Drifted Rows**:
     - `('TAX-US-900101', 'Apex Industrial Holdings LLC', 'US', 'ENTERPRISE', 1250000.00, 'PLATINUM', 'BAND_1M_5M')`
     - `('TAX-DE-800202', 'Rheinland Precision GmbH', 'DE', 'ENTERPRISE', 980000.00, 'GOLD', 'BAND_500K_1M')`

3. **Compiled `zone_0_migration_sql` Output**:
   ```sql
   -- =============================================================================
   -- ZONE 0 MIGRATION: IDEMPOTENT ADDITIVE SATELLITE SCHEMA EVOLUTION (EVO-01)
   -- =============================================================================
   ALTER TABLE raw_vault.sat_customer_core ADD COLUMN IF NOT EXISTS loyalty_tier_code VARCHAR;
   ALTER TABLE raw_vault.sat_customer_core ADD COLUMN IF NOT EXISTS annual_spend_band VARCHAR;
   ```

### 3. Running the Unit and End-to-End (E2E) Tests

#### 3.1 Run the Dedicated Unit & Integration Tests (No Mocks)
```bash
uv run pytest packages/opendv-duckdb-sandbox/tests/test_compiler_and_4batch_sandbox.py \
  -k "test_issue_38" -v
```

#### 3.2 Run the Standalone E2E Multi-Wave DuckDB Verification
```bash
uv run python -c '
import copy
import duckdb
from opendv_axi_verifier import verify_additive_evolution_invariants, verify_physical_dv2_sql_ast
from opendv_duckdb_sandbox import (
    check_dv_dyn_014_additive_schema_evolution,
    compile_canonical_ir_to_4zone_sql,
    lower_contract_to_canonical_ir,
)

wave0_contract = {
    "contract_version": "5.0",
    "domain_namespace": "global_commerce",
    "lifecycle_stage": "COLD_START_N1",
    "column_triage_ledger": [
        {"bronze_table": "crm_salesforce.crm_customers", "column_name": "customer_tax_id", "elm_classification": "BUSINESS_KEY", "target_dv_entity": "hub_customer"},
        {"bronze_table": "crm_salesforce.crm_customers", "column_name": "legal_name", "elm_classification": "DESCRIPTIVE_PAYLOAD", "target_dv_entity": "sat_customer_core"},
        {"bronze_table": "crm_salesforce.crm_customers", "column_name": "country_iso2", "elm_classification": "DESCRIPTIVE_PAYLOAD", "target_dv_entity": "sat_customer_core"},
        {"bronze_table": "crm_salesforce.crm_customers", "column_name": "account_segment", "elm_classification": "DESCRIPTIVE_PAYLOAD", "target_dv_entity": "sat_customer_core"},
        {"bronze_table": "crm_salesforce.crm_customers", "column_name": "credit_limit_usd", "elm_classification": "DESCRIPTIVE_PAYLOAD", "target_dv_entity": "sat_customer_core"},
    ],
    "hubs": [{"name": "hub_customer", "ontology_concept_uri": "urn:ontology:entity:customer", "business_key_sources": [{"bronze_table": "crm_salesforce.crm_customers", "key_columns": ["customer_tax_id"], "record_source_code": "CRM_SALESFORCE", "key_authority_tier": "TIER_1_GLOBAL_STANDARD", "require_src_qualifier": False}]}],
    "links": [],
    "satellites": [{"name": "sat_customer_core", "satellite_type": "STANDARD_SATELLITE", "parent_entity": "hub_customer", "source_bronze_table": "crm_salesforce.crm_customers", "volatility_tier": "STANDARD", "security_classification": "INTERNAL", "payload_columns": ["legal_name", "country_iso2", "account_segment", "credit_limit_usd"]}],
    "reference_tables": [],
    "unmapped_escalations": [],
}
wave0_rows = [
    {"customer_tax_id": "TAX-US-900101", "legal_name": "Apex Industrial Holdings LLC", "country_iso2": "US", "account_segment": "ENTERPRISE", "credit_limit_usd": 1250000.00, "load_dts": "2026-10-01 06:00:00"},
    {"customer_tax_id": "TAX-DE-800202", "legal_name": "Rheinland Precision GmbH", "country_iso2": "DE", "account_segment": "ENTERPRISE", "credit_limit_usd": 980000.00, "load_dts": "2026-10-01 06:00:00"},
]
ir_w0 = lower_contract_to_canonical_ir(wave0_contract)
bundle_w0 = compile_canonical_ir_to_4zone_sql(ir_w0, bronze_tables={"crm_salesforce.crm_customers": wave0_rows})

conn = duckdb.connect(":memory:")
conn.execute(bundle_w0.zones_0_to_3_sql)

wave2_contract = copy.deepcopy(wave0_contract)
wave2_contract["lifecycle_stage"] = "MULTI_SOURCE_EXPANSION"
wave2_contract["column_triage_ledger"].extend([
    {"bronze_table": "crm_salesforce.crm_customers", "column_name": "loyalty_tier_code", "elm_classification": "DESCRIPTIVE_PAYLOAD", "target_dv_entity": "sat_customer_core"},
    {"bronze_table": "crm_salesforce.crm_customers", "column_name": "annual_spend_band", "elm_classification": "DESCRIPTIVE_PAYLOAD", "target_dv_entity": "sat_customer_core"},
])
wave2_contract["satellites"][0]["hash_diff_migration_policy"] = "NULL_EQUIVALENT_PADDING"
wave2_contract["satellites"][0]["payload_columns"].extend(["loyalty_tier_code", "annual_spend_band"])
ir_w2 = lower_contract_to_canonical_ir(wave2_contract)
wave2_rows = [
    {"customer_tax_id": "TAX-US-900101", "legal_name": "Apex Industrial Holdings LLC", "country_iso2": "US", "account_segment": "ENTERPRISE", "credit_limit_usd": 1250000.00, "loyalty_tier_code": "PLATINUM", "annual_spend_band": "BAND_1M_5M", "load_dts": "2026-11-01 06:00:00"},
    {"customer_tax_id": "TAX-DE-800202", "legal_name": "Rheinland Precision GmbH", "country_iso2": "DE", "account_segment": "ENTERPRISE", "credit_limit_usd": 980000.00, "loyalty_tier_code": "GOLD", "annual_spend_band": "BAND_500K_1M", "load_dts": "2026-11-01 06:00:00"},
]
bundle_w2 = compile_canonical_ir_to_4zone_sql(ir_w2, bronze_tables={"crm_salesforce.crm_customers": wave2_rows}, baseline_ir=ir_w0)
conn.execute(bundle_w2.zones_0_to_3_sql)
assert check_dv_dyn_014_additive_schema_evolution(conn, historical_hash_mutations=0, ir=ir_w2).passed is True
print("Wave 0 + Wave 2 rows:", conn.execute("SELECT COUNT(*) FROM raw_vault.sat_customer_core WHERE rec_src <> '\''SYSTEM_GHOST'\''").fetchone()[0])
'
```

---

## Issue #31 — Scenario S5 (Nullable Foreign Key / 0..1 Relationship)

### 1. Scenario Overview & Governing Requirements
- **GitHub Issue**: [`#31`](https://github.com/nogoo9/open-dv/issues/31) (`BUG-02`, **HIGH**)
- **Governing Functional Requirement**: `FR-DOM-01` (§5.1.5 — Nullable-FK Link Splitting & Sparse Binary Link Filtering)
- **DV 2.0 Rules & Invariants**: `Algorithm A.4` (`codemod_enforce_cold_start_extensibility`), `DV-G2-001` / `DV-G2-001b` ($U_{\text{nn}} < 0.95$ Sparse FK Guard), `KEY-05` (Zero-Key Sentinel Discipline)
- **Architectural Property Verified**:
  When a binary Link ($\text{arity} = 2$) references an optional foreign key with empirical non-null ratio $U_{\text{nn}} < 0.95$ (or $\text{null\_rate} > 0.05$), `open-dv` must:
  1. Reject unfiltered binary Links in Gate 2 (`verify_gate1_contract_and_grain`) with `SEMANTIC_ERROR [Gate 2 - DV-G2-001]` (`DV-G2-001b`) when codemods are disabled.
  2. Automatically attach `where_filter_condition = "<fk_col> IS NOT NULL"` in-place on the binary Link and promote its `column_triage_ledger` entry from `FOREIGN_KEY_REF` to `OPTIONAL_FOREIGN_KEY_REF` in `Algorithm A.4` (`codemod_enforce_cold_start_extensibility`), preventing phantom `ZERO_KEY_UNKNOWN` (`'0'*64`) rows from clustering in the Raw Vault Link table.

### 2. Test Dataset & Fixture Layout
Located under `examples/pipeline_samples/global_commerce_vault/scenarios/s5_sparse_binary_link/`:
- **`crm_customers.csv`**: 2 enterprise customers (`TAX-US-900101`, `TAX-DE-800202`) with $U_{\text{nn}} = 1.0$, $\text{null\_rate} = 0.0$.
- **`erp_orders_optional_customer.csv`**: 4 sales orders (`ORD-2026-1001`..`1004`) where `customer_tax_id` is populated on 2 confirmed/shipped orders and `NULL` on 2 draft/quote orders ($\text{null\_rate} = 0.50$, effective non-null ratio $1.0 - \text{null\_rate} = 0.50 < 0.95$).
- **`s5_pipeline_input.yaml`**: Single-wave (`COLD_START`) pipeline input onboarding `crm_salesforce.crm_customers` (`hub_customer`) and `erp_netsuite.erp_orders` (`hub_order`) with binary relationship `lnk_order_customer`.

### 3. Running the Unit & End-to-End Tests

```bash
# 1. Run the non-mocked unit test (GraphBProfiler + Gate 2 DV-G2-001b + Algorithm A.4 + DuckDB SQL)
uv run pytest packages/opendv-axi-verifier/tests/test_codemods_and_static_gates.py \
  -k test_issue_31_alg_a4_and_gate2_sparse_binary_link -vv

# 2. Run the end-to-end 8-stage pipeline test for Scenario S5 optional binary links
uv run pytest packages/opendv-agent-harness/tests/test_agentic_pipeline.py \
  -k test_optional_only_foreign_key_link_synthesis -vv

# 3. Validate the Scenario S5 pipeline input specification via CLI
uv run opendv pipeline validate \
  --input examples/pipeline_samples/global_commerce_vault/scenarios/s5_sparse_binary_link/s5_pipeline_input.yaml \
  --harness-config examples/pipeline_samples/global_commerce_vault/harness_config_agy.yaml \
  --json
```

---

## Issue #41 — Scenario S2 (Multi-Source Entity Convergence)

- **GitHub Issue**: [`#41` — `feat(axi-verifier): add Invariant DV-STR-015 to enforce canonical edge ordering on Same-As Links`](https://github.com/nogoo9/open-dv/issues/41)
- **Audit Gap ID & Severity**: `GAP-10` (`MEDIUM`)
- **Governing Functional Requirements**: `FR-DOM-02` (Wave-by-Wave Multi-Source Entity Convergence), `FR-VER-01` (6-Gate Neuro-Symbolic Verification)
- **DV 2.0 Invariants Verified**: `DV-STR-004`, `DV-STR-015` (Canonical Same-As Link Ordering), `DV-DYN-006` (Multi-Source BK Convergence & SAL Resolution), `DV-DYN-007` (Ghost & Zero-Key Completeness)

### Scenario Overview & Dataset
Scenario S2 models multi-source entity convergence onto a shared Hub (`hub_customer` / `hub_limited_partner`) across sources with distinct 3-Tier `BKCC` authority levels:
1. **Enterprise System of Record (`erp.src_sap_customers` / `investran.src_lp_investors`)**: `TIER_2_ENTERPRISE_SOR` (`require_src_qualifier: false`, `bkcc_prefix: null`).
2. **Local Scoped Source (`crm.src_sfdc_accounts` / `legacy_gp.src_legacy_lp_roster`)**: `TIER_3_LOCAL_SCOPED` (`require_src_qualifier: true`, `bkcc_prefix: "CRM_SALESFORCE"` / `"LEGACY_GP_ADMIN"`).
3. **MDM Steward Crosswalk (`mdm.src_customer_steward_crosswalk` / `mdm.src_lp_steward_crosswalk`)**: Feeds `sal_customer` / `sal_limited_partner` (`link_type: "SAME_AS_LINK"`), mapping `MASTER` (`index 0`, Tier-2) $\to$ `DUPLICATE_ALIAS` (`index 1`, Tier-3).

Adversarial mutations tested against this scenario:
- **Static Contract Mutations (Gate 3 `DV-STR-015`)**:
  - Inverted role ordering (`[DUPLICATE_ALIAS_LP, MASTER_LP]`).
  - Inverted 3-Tier `BKCC` authority hierarchy (`TIER_3_LOCAL_SCOPED` mapped as `MASTER_LP` over `TIER_2_ENTERPRISE_SOR` as `DUPLICATE_ALIAS_LP`).
  - Reflexive self-loops `(A, A)` in `source_fk_columns`, `where_filter_condition`, or `edge_pairs`.
  - Duplicate inverted `(B, A)` pairs within `edge_pairs` or across a second `sal_*` link on the same Hub.
- **Dynamic Runtime Mutations (Zone 2 DML & Gate 5 `DV-DYN-006`)**:
  - Bronze crosswalk rows containing reflexive self-loops `('CUST-100', 'CUST-100')` and inverted duplicate pairs `('CUST-100', 'CUST-200')` + `('CUST-200', 'CUST-100')`.
  - Direct injection of bypassed `(A, A)` and `(B, A)` rows into `business_vault.sal_customer` to verify `check_dv_dyn_006_bkcc_collision_and_sal` fails with `failing_rows >= 2`.

### How to Run the Unit & End-to-End Tests

```bash
# 1. Run the Gate 3 static invariant unit test for DV-STR-015 (no mocks)
uv run pytest -v packages/opendv-axi-verifier/tests/test_codemods_and_static_gates.py \
  -k "test_gate3_dv_str_015_canonical_sal_edge_ordering"

# 2. Run the Scenario S2 end-to-end 4-Zone SQL compiler & DuckDB Shadow Sandbox test (no mocks)
uv run pytest -v packages/opendv-duckdb-sandbox/tests/test_compiler_and_4batch_sandbox.py \
  -k "test_scenario_s2_sal_canonical_ordering_and_dv_dyn_006_enforcement"

# 3. Run standalone CLI 6-Gate verification and 4-Batch DuckDB Shadow Sandbox on the PE reference model
uv run opendv verify --local \
  --contract examples/private_equity/dv_pe_model_contract.yaml \
  --graph-a examples/private_equity/pe_graph_a_ontology.yaml \
  --graph-b examples/private_equity/pe_graph_b_profile.json

uv run opendv sandbox --local \
  --contract examples/private_equity/dv_pe_model_contract.yaml \
  --canonical-ir examples/private_equity/pe_canonical_ir.json \
  --query-type ADVERSARIAL_BATCHES_1_TO_4 \
  --cutoff "2026-11-15 23:59:59"
```

---

## Issue #34 — Scenario S6 (Retroactive / Out-of-Order Event Arrival)

### Overview & Architectural Invariants Verified
- **GitHub Issue:** [#34](https://github.com/nogoo9/open-dv/issues/34) (`BUG-05` — MEDIUM)
- **Governing Requirements:** `FR-DOM-03`, `FR-VER-02`
- **DV 2.0 Rules & Dynamic Invariants:** `TMP-01`..`TMP-04`, `DV-DML-003` (*Bi-Temporal Delta Detection*), `DV-DYN-003` (*Replay Idempotency*), `DV-DYN-009` (*Bi-Temporal Late-Arrival Correctness*)

This scenario verifies that for bi-temporal Satellites carrying a business effective timestamp (`applied_dts` / `effective_from_dt`), Zone 2 delta detection evaluates state transitions relative to the **adjacent state on the business effective timeline** (`PARTITION BY hk, ... ORDER BY applied_dts ASC, load_dts ASC`) rather than comparing against the single row with `MAX(load_dts)` or requiring `src.load_dts > cur.load_dts`.

### Dataset & Fixture Layout
Located under [`examples/scenarios/issue_34_bitemporal_s6/`](../examples/scenarios/issue_34_bitemporal_s6/):
- **`bitemporal_contract.yaml`**: Declarative `NoMistakeDataVault2ContractV5` model defining `hub_customer` and bi-temporal `sat_customer_crm` (`legal_name`, `credit_score_live`, `effective_from_dt`).
- **`bitemporal_events.csv`**: 8-row multi-timeline event stream covering:
  1. **Wave 1 (`load_dts = 2026-10-01`, `applied_dts = 2026-09-30`):** Baseline Q3 states for `CUSTOMER-001` (`AA`) and `CUSTOMER-002` (`A`).
  2. **Wave 2 (`load_dts = 2026-10-15`, `applied_dts = 2026-12-31`):** On-time Q4 upgrade for `CUSTOMER-001` (`AAA`).
  3. **Wave 3 (`load_dts = 2026-10-20`, `applied_dts = 2026-09-30`):** Late-arriving unchanged duplicate of Q3 (`AA`) — **must be suppressed (`+0` rows)** despite `2026-12-31` having a different `hash_diff` at a later `applied_dts`.
  4. **Wave 4 (`load_dts = 2026-11-01`, `applied_dts = 2026-09-30`):** Retroactive Q3 restatement to `AAA` — **must be inserted (`+1` row)** despite matching Q4's `hash_diff`.
  5. **Wave 5 (`load_dts = 2026-09-15`, `applied_dts = 2026-06-30`):** Back-filled Q2 historical record (`BBB`) with `src.load_dts < max(cur.load_dts)` — **must be inserted (`+1` row)**.
  6. **Wave 6 (`load_dts = 2026-10-25`, `applied_dts = 2026-10-31`):** Out-of-order intermediate effective date with payload `AAA` matching the restated `2026-09-30` state — **must be suppressed (`+0` rows)**.
  7. **Wave 7 (`load_dts = 2026-10-26`, `applied_dts = 2026-10-31`):** Out-of-order intermediate effective date with mutated payload `AA+` — **must be inserted (`+1` row)**.

### Running the Unit & End-to-End Verification Tests

```bash
# 1. Run the non-mocked Issue #34 unit & bi-temporal PIT reconstruction tests
uv run pytest packages/opendv-duckdb-sandbox/tests/test_compiler_and_4batch_sandbox.py -k "issue_34" -v

# 2. Compile the Issue #34 contract to Canonical IR & 4-Zone DuckDB SQL via CLI
uv run opendv compile --local \
  --contract examples/scenarios/issue_34_bitemporal_s6/bitemporal_contract.yaml

# 3. Execute the 4-Batch Adversarial DuckDB Shadow Sandbox & 15 Runtime Invariants (DV-DYN-001..015)
uv run opendv sandbox --local \
  --contract examples/scenarios/issue_34_bitemporal_s6/bitemporal_contract.yaml \
  --query-type ADVERSARIAL_BATCHES_1_TO_4 \
  --cutoff "2026-11-15 23:59:59"
```

---

## Issue #30 — Scenario S7 (Malformed / Adversarial Raw Data & Multi-byte UTF-8)

### 1. Overview & Governing Invariants
- **GitHub Issue:** [#30](https://github.com/nogoo9/open-dv/issues/30) (`BUG-01` — **CRITICAL**)
- **Governing Functional Requirements:** `FR-VER-01` (Cross-Runtime Cryptographic Parity), `FR-INT-01` (4-Zone DuckDB SQL Compilation)
- **Data Vault 2.0 Rules & Algorithms:** `Algorithm A.5` (6-Stage Length-Prefixed Canonical SHA-256 Hashing), `DV-HASH-004` (Injective UTF-8 Byte-Length Prefixing), `Algorithm A.6` (`DV-AST-002` / `DV-G6-001`), `DV-DYN-005`
- **Core Verification Objective:** Prove that all Hub hash keys (`hk_*`), Link hash keys (`hk_lnk_*`), Satellite change digests (`hash_diff`), and PIT keys (`hk_pit_*`) compiled into DuckDB SQL compute token length prefixes in **UTF-8 bytes (`OCTET_LENGTH(ENCODE(...))`)** rather than Unicode code points (`STRLEN` / `LENGTH`), producing bit-for-bit identical pre-hash strings and 64-character hex SHA-256 digests across Python (`opendv-core`), TypeScript (`@opendv/protocol-ts`), and DuckDB SQL (`opendv-duckdb-sandbox`).

### 2. Scenario S7 Dataset & Contract Artifacts
All Scenario S7 fixtures reside under [`examples/scenarios/s7_multibyte_utf8/`](../examples/scenarios/s7_multibyte_utf8/):

| Artifact Path | Role | Adversarial / Multi-Byte UTF-8 Coverage |
| :--- | :--- | :--- |
| `examples/scenarios/s7_multibyte_utf8/crm_customers.csv` | Bronze Customer Master (`crm_salesforce.crm_customers`) | 2-byte Nordic/German Latin (`'Nordisk Suverän Fond'` = 20 chars / 21 UTF-8 bytes; `'Rheinland Präzision München GmbH'`), 3-byte Euro/Yen & CJK (`'SOVEREIGN_€'`, `'日本国年金積立金管理運用独立行政法人'` = 18 chars / 54 UTF-8 bytes), 4-byte Emoji (`'🏦 Sovereign Wealth Fund'` = 23 chars / 25 UTF-8 bytes). |
| `examples/scenarios/s7_multibyte_utf8/erp_orders.csv` | Bronze Commercial Orders (`erp_netsuite.erp_orders`) | Multi-byte UTF-8 business keys (`'ORD-2026-Å01'`), foreign keys (`'TAX-SE-ÅÄÖ-102'`, `'TAX-NO-🏦-404'`), and embedded delimiter/section-sign payloads (`'Tranche A\|\|B — Nordisk Suverän Fond §4.2'`). |
| `examples/scenarios/s7_multibyte_utf8/s7_multibyte_utf8_contract.yaml` | Declarative DV 2.0 Contract (`NoMistakeDataVault2ContractV5`) | 100% `column_triage_ledger` coverage mapping `crm_customers` and `erp_orders` into `hub_customer`, `hub_order`, `lnk_order_customer`, `sat_customer_core`, and `sat_order_core`. |

#### Normative Multi-Byte UTF-8 Reference Vectors (`DV-HASH-004`)
| Input String | Unicode Chars (`LENGTH`) | UTF-8 Bytes (`OCTET_LENGTH(ENCODE(...))`) | Canonical Payload Pre-Hash (`is_business_key=False`) | Expected Lowercase SHA-256 Digest |
| :--- | :---: | :---: | :--- | :--- |
| `'Nordisk Suverän Fond'` | `20` | `21` | `'21:Nordisk Suverän Fond'` | `8f158a694b28984db2ddd2d99d3fa4e67a624791dc3e1d7e27df363a49cd1ff7` |
| `'München €25M'` | `12` | `15` | `'15:München €25M'` | `e8cdf47ea5d7fc86222aea1232609e57f960e5e5d03c6c8c041e9dac72e34f57` |
| `'日本国年金積立金管理運用独立行政法人'` | `18` | `54` | `'54:日本国年金積立金管理運用独立行政法人'` | `b537f8a15b4fd2f74743b6b2245607c346e89f22d321934a67872854e02807d1` |
| `'🏦 Sovereign Fund'` | `16` | `19` | `'19:🏦 Sovereign Fund'` | `49828efad954c7a21d6ce5723459a90d2727b6a08d7ec02db3604c84cdcf2ae0` |

### 3. Running the Unit & End-to-End Tests

```bash
# 1. Run the Issue #30 unit and E2E sandbox regression tests (no mocks)
uv run pytest \
  packages/opendv-duckdb-sandbox/tests/test_compiler_and_4batch_sandbox.py \
  packages/opendv-axi-verifier/tests/test_codemods_and_static_gates.py \
  -k "issue_30" -v

# 2. Profile the Scenario S7 multi-byte UTF-8 Bronze CSVs via GraphBProfiler
uv run opendv profile --local \
  --source-csv examples/scenarios/s7_multibyte_utf8/crm_customers.csv \
  --source-system crm_salesforce \
  --table-name crm_customers \
  --json

# 3. Run 6-Gate static verification on the Scenario S7 contract
uv run opendv verify --local \
  --contract examples/scenarios/s7_multibyte_utf8/s7_multibyte_utf8_contract.yaml \
  --graph-a examples/pipeline_samples/global_commerce_vault/ontology/commerce_graph_a_ontology.yaml

# 4. Compile Scenario S7 to Canonical IR & 4-Zone DuckDB SQL and execute the 4-Batch Adversarial Sandbox
uv run opendv compile --local \
  --contract examples/scenarios/s7_multibyte_utf8/s7_multibyte_utf8_contract.yaml

uv run opendv sandbox --local \
  --contract examples/scenarios/s7_multibyte_utf8/s7_multibyte_utf8_contract.yaml \
  --query-type ADVERSARIAL_BATCHES_1_TO_4 \
  --cutoff "2026-11-15 23:59:59"
```

---

## Issue #42 — Scenario S9 (Multi-Tenant / Multi-Unit Collisions)

### 1. Scenario Overview & Governing Requirements
- **GitHub Issue**: [#42 — `feat(compiler_4zone): support dynamic bkcc_column_expr for multi-tenant table hashing`](https://github.com/nogoo9/open-dv/issues/42)
- **Audit ID**: `GAP-11` (`MEDIUM`)
- **Functional Requirements**: `FR-DOM-02` (Multi-Source & Multi-Tenant Integration), `FR-INT-01` (Canonical IR & 4-Zone SQL Compilation)
- **DV 2.0 Rules & Invariants**: `KEY-04` (3-Tier Business Key Authority & BKCC Namespace Isolation), `Algorithm A.5` (6-Stage Length-Prefixed Canonical SHA-256 with Dynamic Tenant Namespace), `KEY-02(b)` (Zero-Key Unknown Sentinel `0x00..00`), `DV-COL-012` (Gate 1 Tier-3 BKCC Enforcement), `DV-EVO-003` / `DV-EVO-007` (Gate 4 BKCC Evolution Lock)

In shared single-table multi-tenant architectures, multiple tenants or business units reside in the same physical Bronze table (`bronze_saas.tenant_accounts`, `bronze_saas.tenant_subscriptions`) partitioned by a row-level discriminator column (`tenant_id` or `org_code`). Local natural keys (`account_id = 'ACCT-1001'`) collide across tenants (`TENANT_US` vs. `TENANT_EU`). Declaring `bkcc_column_expr: "tenant_id"` on the Hub's `BusinessKeySource` lowers into `CanonicalIR` (`IRHub`, `IRHashExpression`, `IRHubSourceMapping`) and compiles Zone 1 staging views that prefix `_hk` with the dynamic length-prefixed column expression (`9:TENANT_US||9:ACCT-1001` vs. `9:TENANT_EU||9:ACCT-1001`) while preserving `KEY-02(b)` Zero-Key resolution (`REPEAT('0', 64)`) when `account_id IS NULL`.

### 2. Test Dataset
- **`tenant_accounts.csv` (`bronze_saas.tenant_accounts`)**:
  ```csv
  tenant_id,account_id,account_name,plan_tier
  TENANT_US,ACCT-1001,Acme US Holdings,ENTERPRISE
  TENANT_EU,ACCT-1001,Acme EU GmbH,GROWTH
  TENANT_US,ACCT-1002,Beta US Corp,STARTER
  ```
- **`tenant_subscriptions.csv` (`bronze_saas.tenant_subscriptions`)**:
  ```csv
  tenant_id,account_id,subscription_id
  TENANT_US,ACCT-1001,SUB-9001
  TENANT_EU,ACCT-1001,SUB-9002
  ```
- **Expected Hub Keys (`raw_vault.hub_tenant_account`)**:
  - `('TENANT_EU', 'ACCT-1001')` $\to$ `2530e2e2959ce91a2582cdcc0369df5367ba21659d14078ea37902e23af18c4b` (`SHA256('9:TENANT_EU||9:ACCT-1001')`)
  - `('TENANT_US', 'ACCT-1001')` $\to$ `05732713befe836172388e84bd17a65b7fc1cd080a775cb9f9b2aafb48f407a5` (`SHA256('9:TENANT_US||9:ACCT-1001')`)
  - `('TENANT_US', 'ACCT-1002')` $\to$ `aa878e0129a5d13e21718834b48473337925ac1db45214194155e0c275b14e8d` (`SHA256('9:TENANT_US||9:ACCT-1002')`)
  - `('TENANT_US', NULL)` $\to$ `0000000000000000000000000000000000000000000000000000000000000000` (`ZERO_KEY_UNKNOWN`)

### 3. Test Scenarios Covered
- **`test_contract_v5_and_canonical_ir_accept_bkcc_column_expr`** (`packages/opendv-core/tests/test_core_models_and_hashing.py`): Validates `bkcc_column_expr` and `bkcc_literal` across Pydantic v2 models (`BusinessKeySource`, `IRHashExpression`, `IRHubSourceMapping`, `IRHub`) and Draft 2020-12 JSON Schemas (`schemas/no-mistake-dv2-contract-v5.schema.json`, `schemas/dv2-canonical-ir.schema.json`).
- **`test_build_algorithm_a5_sha256_sql_dynamic_bkcc_column_expr_and_zero_key`** (`packages/opendv-duckdb-sandbox/tests/test_compiler_and_4batch_sandbox.py`): Verifies `build_algorithm_a5_sha256_sql` and `build_canonical_sha256_hash_ast` in `:memory:` DuckDB for dynamic `bkcc_column_expr` prefixing, input column deduplication, single-column fallback, `UPPER(TRIM(...))` normalization, and `KEY-02(b)` Zero-Key resolution (`REPEAT('0', 64)`) when business key columns are `NULL` or whitespace.
- **`test_scenario_s9_multitenant_bkcc_column_expr_e2e_4zone_and_sandbox`** (`packages/opendv-duckdb-sandbox/tests/test_compiler_and_4batch_sandbox.py`): End-to-end verification of `tenant_accounts.csv` and `tenant_subscriptions.csv` through `validate_contract_v5`, Gate 1 `verify_gate1_contract_and_grain` (including whitespace-only `bkcc_column_expr` rejection under `DV-COL-012`), Gate 4 `verify_evolution_lock_alg_a7` (`BKCC_MUTATED`), SQLGlot AST verification of `codemod_enforce_staging_hashes_sql`, `lower_contract_to_canonical_ir`, `compile_canonical_ir_to_4zone_sql`, and `DuckDbShadowSandbox.run_full_verification` (4 Adversarial Batches + `DV-DYN-001..015`).

### 4. Running the Unit and E2E Tests
```bash
# Run the Issue #42 / Scenario S9 unit & E2E tests in opendv-duckdb-sandbox and opendv-core
uv run --all-packages pytest \
  packages/opendv-duckdb-sandbox/tests/test_compiler_and_4batch_sandbox.py \
  packages/opendv-core/tests/test_core_models_and_hashing.py \
  -k "bkcc_column_expr or scenario_s9" -v

# Run the full Python & TypeScript conformance suites
uv run --all-packages pytest -v
bun test
```

---

## Issue #40 — Scenario S1 (Cold Start), Scenario S2 (Multi-Source Convergence)

### 1. Scenario Overview & Governing Requirements
- **GitHub Issue**: [#40 — `feat(axi-verifier,core): add Invariant DV-COL-013 to standardize Tier-1 global key prefixing`](https://github.com/nogoo9/open-dv/issues/40)
- **Audit Gap ID**: `GAP-09` (`MEDIUM`)
- **Governing Functional Requirements**: `FR-DOM-01` (Cold-Start $N=1$ Single-Source Onboarding), `FR-DOM-02` (Periodic Multi-Source Onboarding $N \to N+1$), `FR-VER-01` (Deterministic 6-Gate Symbolic Verification & 4-Batch Adversarial DuckDB Sandbox)
- **DV 2.0 Rules & Invariants**: `KEY-04` (3-Tier Business Key Authority), `DV-COL-013` (Tier-1 Global BKCC Invariant), `EVO-01` & `EVO-03` (Grain & Hash Key Immutability Across Waves)
- **Mapped Scenarios**:
  - **Scenario S1 (`Cold Start`, $N=1$):** Onboards single-source Tier-1 Global Standard feeds (`bronze_crm.accounts` with ACRA `company_uen` and `bronze_erp.invoices` with `invoice_global_id`). Verifies that Gate 1 (`gate1_contract_grain.py`) and Gate 3 (`gate3_networkx_dag.py`) reject any `TIER_1_GLOBAL_STANDARD` source lacking `require_src_qualifier=false` and `bkcc_prefix='GLOBAL'` with `DV-COL-013`, that `Algorithm A.4` (`alg_a4_cold_start.py`) normalizes Tier-1 sources to `bkcc_prefix='GLOBAL'`, and that `CanonicalIR` and 4-Zone DuckDB SQL hash `'202601234K'` as `'6:GLOBAL||10:202601234K'` (`452e5f90e20e894cf6fb9ab6dd799752b60915669d7ddba0ed2ff283774992e8`).
  - **Scenario S2 (`Multi-Source Convergence`, $N \to N+1$):** Expands `hub_customer` with a second Tier-1 source (`bronze_erp.invoices.tax_reg_no`, `bkcc_prefix='GLOBAL'`) and a colliding Tier-3 local-scoped source (`bronze_legacy.tickets.legacy_client_no`, `bkcc_prefix='LEGACY_TICKET_DB'`). Verifies that Tier-1 customer `'202601234K'` passively converges on the exact same Hub hash key (`452e5f90...`) with zero re-hashing storm (`EVO-01`), while Tier-3 key `'1001'` is isolated under `'16:LEGACY_TICKET_DB||4:1001'` (`28f087bb678034b531ff6991fdd613f6793d0a66ef6b5ece88bd5945d5f8488f`).

### 2. Test Dataset Specification
The E2E test suite (`tests/test_issue40_tier1_global_bkcc_e2e.py`) materializes three real CSV feeds and profiles them via `GraphBProfiler`:

| Wave | Qualified Bronze Table | Record Source | Authority Tier | BK Column | Sample Values | Expected Canonical Pre-Hash (`Algorithm A.5`) | Expected SHA-256 Hub Hash Key (`hk_*`) |
| :---: | :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Wave 0 (`S1`)** | `bronze_crm.accounts` | `SFDC_CRM` | `TIER_1_GLOBAL_STANDARD` | `company_uen` | `202601234K` | `6:GLOBAL\|\|10:202601234K` | `452e5f90e20e894cf6fb9ab6dd799752b60915669d7ddba0ed2ff283774992e8` |
| **Wave 0 (`S1`)** | `bronze_erp.invoices` | `SAP_ERP` | `TIER_1_GLOBAL_STANDARD` | `invoice_global_id` | `INV-SG-2026-0001` | `6:GLOBAL\|\|16:INV-SG-2026-0001` | `0b26bb8471756644be558a60b03ca26bc7f3f84168dd04e3b7711ef73fef4895` |
| **Wave 1 (`S2`)** | `bronze_erp.invoices` | `SAP_ERP` | `TIER_1_GLOBAL_STANDARD` | `tax_reg_no` | `202601234K` | `6:GLOBAL\|\|10:202601234K` | `452e5f90e20e894cf6fb9ab6dd799752b60915669d7ddba0ed2ff283774992e8` *(passive convergence)* |
| **Wave 1 (`S2`)** | `bronze_legacy.tickets` | `LEGACY_TICKET_DB` | `TIER_3_LOCAL_SCOPED` | `legacy_client_no` | `1001` | `16:LEGACY_TICKET_DB\|\|4:1001` | `28f087bb678034b531ff6991fdd613f6793d0a66ef6b5ece88bd5945d5f8488f` *(namespace isolated)* |

### 3. Running the Unit and End-to-End Tests
Execute the following commands from the repository root (zero LLM credentials or external database services required):

```bash
# 1. Run the dedicated Issue #40 End-to-End (S1 -> S2) test suite
uv run pytest tests/test_issue40_tier1_global_bkcc_e2e.py -v

# 2. Run the unit regression tests across opendv-core, opendv-axi-verifier, and opendv-duckdb-sandbox
uv run pytest \
  packages/opendv-core/tests/test_core_models_and_hashing.py \
  packages/opendv-axi-verifier/tests/test_codemods_and_static_gates.py \
  packages/opendv-duckdb-sandbox/tests/test_duckdb_sandbox_suite.py \
  -k "tier1 or dv_col_013" -v

# 3. Run the full repository conformance test suite
uv run pytest -v
```
