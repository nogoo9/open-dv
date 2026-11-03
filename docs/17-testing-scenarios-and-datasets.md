# `open-dv` Testing Scenarios, Datasets & Verification Ledger (`docs/17-testing-scenarios-and-datasets.md`)

> Normative catalog of end-to-end and unit test datasets, scenarios (`S1`–`S10`), unmatched ontology field routing cases (`UF-1`–`UF-5`), and zero-mock verification commands.

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
