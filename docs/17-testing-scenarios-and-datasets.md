# Open-DV Testing Scenarios and Datasets (`docs/17-testing-scenarios-and-datasets.md`)

> Normative catalog of end-to-end and unit testing scenarios, datasets, and verification commands mapped to `open-dv` Functional Requirements (`FR-DOM-*`, `FR-VER-*`, `FR-INT-*`), Scenarios (`S1`–`S10`), and Unmatched Cases (`UF-1`–`UF-5`).

---

## Issue #32 — Scenario S8 (Unstructured Document Ingestion), Unmatched Case UF-5

### 1. Scenario & Governance Context
- **GitHub Issue:** [#32 — `feat(compiler_4zone): enforce steward approval filter on unstructured extraction links in Zone 2 DML`](https://github.com/nogoo9/open-dv/issues/32)
- **Audit ID & Severity:** `BUG-03` (`HIGH`)
- **Functional Requirements:** `FR-DOM-05` (Multimodal Unstructured Document Grounding & Steward Resolution), `FR-VER-04` (Adversarial Dynamic DuckDB Verification & Replay Idempotency)
- **Mapped Scenario & Case:** `Scenario S8` (Unstructured Document Ingestion), Unmatched Case `UF-5` (Ambiguous / Low-Confidence `Tier-U3` Entity Match)
- **Enforced Rules & Invariants:** `Rule U4` (3-Tier Entity Resolution Confidence Margin Routing), `Tier-U3` (`confidence < 0.85` or `top2_margin < 0.15`), `DV-AST-007` (Steward-Approved Gating on Unstructured Links), `DV-DYN-013` (Tier-2 Confidence Threshold & Human Review Governance)

### 2. Test Dataset Description
1. **Private Equity Unstructured VDR Dataset (`examples/private_equity/pe_canonical_ir.json` + `unstructured_vdr.src_llm_extractions`):**
   - **Approved Baseline Records:**
     - `EXT-SL-001` (`SIDE_LETTER_TERMS`, `LP-101`, `extraction_confidence = 0.9720`, `tier2_token_jaccard = 0.9410`, `human_review_status = 'AUTO_ACCEPTED_TIER_2'`) $\to$ normalized to `steward_gate_status = 'APPROVED'` in `staging.stg_unstructured_vdr_llm_extractions`.
     - `EXT-IC-002` (`PORTCO_VALUATION_RESTATEMENT`, `529900T8BM49AURSDO55`, `extraction_confidence = 0.9140`, `tier2_token_jaccard = 0.7200`, `human_review_status = 'STEWARD_VERIFIED'`) $\to$ normalized to `steward_gate_status = 'APPROVED'` in `staging.stg_unstructured_vdr_llm_extractions`.
   - **Injected Adversarial `Tier-U3` (`UF-5`) Steward-Hold Records:**
     - `EXT-SL-HOLD-003` (`SIDE_LETTER_TERMS`, `LP-102`, `extraction_confidence = 0.7200 < 0.85`, `human_review_status = 'STEWARD_HOLD'`) $\to$ normalized to `steward_gate_status = 'STEWARD_HOLD'` and blocked from `raw_vault.lnk_doc_lp_side_letter` and `business_vault.sat_lp_side_letter_llm_ext`.
     - `EXT-IC-HOLD-004` (`PORTCO_VALUATION_RESTATEMENT`, `529900T8BM49AURSDO55`, `extraction_confidence = 0.6900 < 0.85`, `human_review_status = 'STEWARD_HOLD'`) $\to$ blocked from `raw_vault.lnk_doc_portco_memo` until updated to `'STEWARD_VERIFIED'`, and flagged by `DV-DYN-013` if promoted while `extraction_confidence < 0.85`.
2. **Generic `CanonicalIR` Unstructured Dataset (`bronze.src_lnk_document_portfolio_company` / `bronze.src_sat_lnk_doc_portco_ai_provenance`):**
   - Contains 1 baseline `'APPROVED'` seed row and 1 baseline `'STEWARD_HOLD'` seed row across unstructured extraction links and attached link-satellites, plus 4 adversarial rows spanning `'STEWARD_HOLD'`, `'PENDING'`, `'REJECTED'`, and `'APPROVED'` governance states. Only `'APPROVED'` rows are promoted into `raw_vault.lnk_document_*` and `raw_vault.sat_*`.

### 3. Running the Unit and End-to-End Tests

```bash
# 1. Run the E2E Scenario S8 / Case UF-5 test (real DuckDB + real CanonicalIR + real SQLGlot AST)
uv run pytest tests/test_multimodal_and_duckdb_e2e.py -k test_issue_32_scenario_s8_uf5_unstructured_link_steward_gate_e2e -vv

# 2. Run the 4-Zone Compiler & DV-DYN-013 unit test in opendv-duckdb-sandbox
uv run pytest packages/opendv-duckdb-sandbox/tests/test_compiler_and_4batch_sandbox.py -k test_compiler_4zone_enforces_steward_gate_status_on_unstructured_links -vv

# 3. Run the Gate 6 SQLGlot AST Linter (DV-AST-007) unit tests in opendv-axi-verifier
uv run pytest packages/opendv-axi-verifier/tests/test_codemods_and_static_gates.py -k "test_alg_a6_dv_ast_007_unstructured_link_steward_approval_filter or test_issue_32_alg_a6_dv_ast_007_requires_steward_gate" -vv

# 4. Run CLI compile & 4-Batch Adversarial Shadow Sandbox verification
uv run opendv compile --local \
  --contract examples/private_equity/dv_pe_model_contract.yaml \
  --canonical-ir examples/private_equity/pe_canonical_ir.json

uv run opendv sandbox --local \
  --contract examples/private_equity/dv_pe_model_contract.yaml \
  --canonical-ir examples/private_equity/pe_canonical_ir.json \
  --query-type ADVERSARIAL_BATCHES_1_TO_4 \
  --cutoff "2026-11-15 23:59:59"
```
