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
