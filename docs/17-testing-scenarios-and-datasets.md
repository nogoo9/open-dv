# 17 — Normative Testing Scenarios & Verification Datasets

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
