# 17 — Testing Scenarios and Datasets

> **Part of the [`open-dv` Reference Guide Suite](DV2_AGENT_REFERENCE_GUIDE.md)**  
> **Domain:** End-to-End Verification Scenarios (`S1`–`S10`), Adversarial Test Datasets, and Reproducible Execution Instructions

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
