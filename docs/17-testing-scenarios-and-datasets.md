# `open-dv` Testing Scenarios, Datasets & Verification Ledger (`docs/17-testing-scenarios-and-datasets.md`)

> Normative catalog of end-to-end and unit test datasets, scenarios (`S1`–`S10`), unmatched ontology field routing cases (`UF-1`–`UF-5`), canonical test vectors, and zero-mock verification commands.

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
