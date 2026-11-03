# `open-dv` Testing Scenarios & Adversarial Datasets

This document catalogs the non-mocked unit and end-to-end (E2E) verification scenarios, reference datasets, and CLI/pytest execution commands for `open-dv`.

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
