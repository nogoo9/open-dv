# 17 — Testing Scenarios and Datasets

This document catalogs the end-to-end verification scenarios, reference datasets, canonical test vectors, and execution commands for `open-dv` conformance and issue regression suites.

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
