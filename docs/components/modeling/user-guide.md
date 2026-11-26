# Open-DV Modeling Engine User Guide (`opendv.modeling`)

> **Design Specification:** [`docs/designs/dv2-modeling-engine-and-governance.md`](../../designs/dv2-modeling-engine-and-governance.md) · **CLI Reference:** [`docs/16-cli-reference.md`](../../16-cli-reference.md)

The `opendv.modeling` package provides two complementary Data Vault 2.0 modeling workflows backed by the same Layer-1 `VaultSchemaIR v2.0` contracts, `L1` deterministic codemods, `V1–V6` structural/runtime gates (including `:memory:` DuckDB `schema_proof` and `real_data_proof`), and tiered `V6b` anti-pattern checks:

- **Mode 1 (`opendv model {spec, profile, verify}`):** Zero-internal-LLM deterministic verification for external coding agents and CI/CD pipelines. External agents profile sources, interview users on surfaced `P1–P4` ambiguities, author `DraftVaultIR`, and verify deterministically to `CERTIFIED_FAST` (exit `0`) or receive structured `RemediationHint`s on exit `5`.
- **Mode 2 (`opendv model run` / `opendv vault build`):** Managed 3-Loop CEGAR synthesis (`@ship-vault` with deterministic sharding, `L1`, `V1–V6b`, single-pass `V7` Semantic Critic, and `V8` Horizon Critic with bounded `S6-06` `STEER` / `"revert"`), producing `CERTIFIED` schemas either statelessly (`opendv model run`) or inside a project's `project.sqlite` (`opendv vault build`).

---

## 1. Mode 1 vs. Mode 2 Workflows

| Capability | Mode 1 (`opendv model verify`) | Mode 2 (`opendv model run` / `opendv vault build`) |
| :--- | :--- | :--- |
| **Internal LLM Calls** | `0` (external agent authors `DraftVaultIR`) | `@ship-vault`, `V7` Semantic Critic, `V8` Horizon Critic |
| **State Footprint** | Zero `.opendv/` writes (pure in-memory) | `model run`: zero `.opendv/` writes; `vault build`: persisted in `project.sqlite` |
| **Survey Discovery** | Stateless via `opendv model profile` (`P1–P4`) | `opendv model profile` or Mode-2 Hold (`clarify_mode=ask_on_ambiguity`) |
| **Governance Inputs** | `ModelingPreferences` + `persisted_waivers` | `ModelingPreferences` + `dv2-modeling-guide.md` (`<= 2,500` tok) + `business_context_hints` (`<= 1,500` tok) |
| **Terminal Success** | `status="CERTIFIED_FAST"` (`next_action="READY_FOR_DOWNSTREAM"`) | `status="CERTIFIED"` (`"CERTIFIED_FAST"` under `fast`/`symbolic_only`) |

### Mode 1 Quick Commands (Stateless, Zero LLM Keys)

```bash
# 1. Inspect schemas, constraints, modeling guide, and preferences
uv run opendv --json model spec --part all

# 2. Profile sources and paginate deterministic P1-P4 survey questions
uv run opendv --json model profile \
  --sources-file tests/fixtures/business_vault_hints/manifest.json \
  --max-questions 3 \
  --questions-offset 0 \
  --out profile_bundle.json

# 3. Validate DraftVaultIR schemas & PII tokenization (--dry-run), then verify in DuckDB
uv run opendv --json model verify \
  --input req.json \
  --draft-ir draft.json \
  --profile profile_bundle.json \
  --dry-run

uv run opendv --json model verify \
  --input req.json \
  --draft-ir draft.json \
  --profile profile_bundle.json \
  --out verify_result.json
```

### Mode 2 Quick Commands (Stateless `model run` vs. Managed `vault build`)

```bash
# Stateless Mode 2 with workspace guide override and ProfileBundle
uv run opendv --json model run \
  --input req.json \
  --profile profile_bundle.json \
  --guide .opendv/agents/dv2-modeling-guide.md \
  --out run_result.json

# Managed Project Mode 2
uv run opendv --json vault build --project proj-am --policy balanced --wait
```

---

## 2. Flat `ModelingPreferences`, Pre-Cert Waivers & `S6-06` `STEER` / `"revert"`

### Governance Precedence Hierarchy
1. **Immutable `V1–V6` Invariants & `v7_floor`:** Always enforced; cannot be relaxed by preferences or hints.
2. **Tier-1 `ModelingPreferences`:** Deterministic switches configured in `req.json` or persisted per project via `opendv settings project-set`.
3. **Tier-2 `dv2-modeling-guide.md` (`<= 2,500` tokens) & Mode-2 `business_context_hints` (`<= 1,500` tokens):** Untrusted domain-exception guidance fenced inside `<untrusted_business_context>` for Mode-2 synthesis and critics (prohibited in Mode-1 `model verify`).

### Configuring `ModelingPreferences` (`opendv settings project-set`)

`ModelingPreferences` exposes eight flat fields:
- `multi_source_hub_strategy`: `"unified_canonical_when_same_arity"` (default) | `"companion_hub_with_sal"`
- `low_cardinality_strategy`: `"auto_ref_table"` (default, promotes `<= 20` distinct values to `Z0` `ref_*`) | `"keep_in_satellite"`
- `link_cardinality_strategy`: `"prefer_standard_or_mas"` (default) | `"allow_transactional_links"`
- `esat_on_mutable_links`: `"auto_when_turnover_known"` (default) | `"always_on_driving_hub"` | `"off"`
- `surrogate_key_strategy`: `"reject_when_natural_exists"` (default) | `"allow_with_bkcc_note"`
- `disabled_v6b_blocks`: `list[str]` of valid `V6b` rule IDs (must remain empty under `strict` policy)
- `clarify_mode`: `"auto"` (default) | `"ask_on_ambiguity"`
- `max_survey_questions`: `int | None` (default `5`, `>= 1`; `None` returns all candidates in Mode-2 Hold discovery)

Update project preferences, Mode-2 business hints, and Step 2/3 proving capture limits via `opendv settings project-set`:

```bash
uv run opendv --json settings project-set \
  --project proj-am \
  --pref multi_source_hub_strategy=unified_canonical_when_same_arity \
  --pref esat_on_mutable_links=always_on_driving_hub \
  --pref clarify_mode=ask_on_ambiguity \
  --hints-file domain_hints.md \
  --proving-sample-rows 200 \
  --real-row-proving
```

### Pre-Certification Waivers (`vault waive`) & Hold Inspection (`vault status`)

Submit Wave-1 `V6b` anti-pattern waivers **before** certification (even when `active_wave_id` is unset); persisted waivers hydrate into `ir.justifications` prior to `L1` and `V1–V6b` gates:

```bash
# Record a pre-certification waiver
uv run opendv --json vault waive \
  --project proj-am \
  --rule DV-AP-003 \
  --entity hub_legacy_item \
  --rationale "Upstream legacy ERP exposes only integer surrogate key with no natural code."

# Inspect current vault status, waived rules, and open Holds only
uv run opendv --json vault status --project proj-am --holds-only
```

### `S6-06` Horizon `STEER` and `"revert"` Recovery

When Gate `V8` (`Horizon Critic`) pauses at `HOLD_CHECKPOINTED`, the pre-steer certified snapshot is preserved (`pre_steer_fallback_ref` in managed runs; `pre_steer_fallback_draft_ir` / `pre_steer_fallback_certified_ir` in stateless runs):

- **`steer`:** Clears `skip_synthesis`, resets `best = None`, applies the steward's guidance (`--assumptions-json`, `--notes`, `--added-fields`), and executes a bounded repair (`<= 1` post-`STEER` `V8` re-score pass).
- **`revert`:** If post-`STEER` synthesis does not improve or exhausts `K_max`, `"revert"` restores the saved pre-steer certified snapshot directly without launching another synthesis pass.
- **`accept`:** Accepts the boundary and finalizes certification.

```bash
# Steer a held V8 Horizon checkpoint
uv run opendv --json vault horizon \
  --project proj-am \
  --run run-001 \
  --action steer \
  --notes "Split settlement allocations into mas_trade_settlement_leg with leg_seq CDK." \
  --added-fields allocation_leg_seq,settled_amt

# Revert directly to the pre-steer certified snapshot if steer fails to converge
uv run opendv --json vault horizon \
  --project proj-am \
  --run run-001 \
  --action revert
```

---

## 3. Querying Excluded `mas_*` and `sat_*_pii` Satellites

To protect privacy and preserve 1:1 Hub grain, `information_mart.vw_<hub>_current` automatically excludes:
- **`sat_*_pii` (`sat_kind == "PII"`):** Personal/sensitive attributes stay isolated in `raw_vault.sat_*_pii`.
- **`mas_*` (`sat_kind == "MAS"`):** Multi-active Satellites have 1:N concurrent rows per `hk_<hub>` keyed by `(hk_<hub>, load_dts, <cdk...>)` and cannot collapse to one row per Hub key.

When every Satellite on a Hub is `PII` or `MAS`, `information_mart.vw_<hub>_current` falls back to projecting `hk_<hub>` plus the Hub's canonical business key columns (`hub_canonical_bk_cols`). Downstream consumers query `mas_*` and `sat_*_pii` directly using `RANK()` / `ROW_NUMBER()` while filtering out Dual Zero/Ghost keys (`0x00..00` / `0xFF..FF`):

### Template A: Latest Multi-Active Rows per `(hk_<hub>, cdk)` (`mas_*`)

Use `RANK()` over `PARTITION BY hk_<hub>` when retrieving the latest active snapshot batch of child rows (preserving all sibling rows loaded in the newest `load_dts` batch), or `ROW_NUMBER()` over `PARTITION BY hk_<hub>, <cdk>` when retrieving the latest state per individual child dependent key:

```sql
-- Latest active multi-active batch per Hub key (preserves all concurrent CDK rows at max load_dts)
WITH ranked_mas AS (
    SELECT
        m.hk_customer,
        m.phone_type,
        m.phone_number,
        m.is_primary,
        m.load_dts,
        RANK() OVER (
            PARTITION BY m.hk_customer
            ORDER BY m.load_dts DESC
        ) AS _batch_rk
    FROM raw_vault.mas_customer_phone AS m
    WHERE m.hk_customer <> REPEAT('0', 64)
      AND m.hk_customer <> REPEAT('f', 64)
)
SELECT
    h.customer_id,
    r.phone_type,
    r.phone_number,
    r.is_primary,
    r.load_dts
FROM raw_vault.hub_customer AS h
INNER JOIN ranked_mas AS r
    ON r.hk_customer = h.hk_customer
WHERE r._batch_rk = 1
  AND h.hk_customer <> REPEAT('0', 64)
  AND h.hk_customer <> REPEAT('f', 64);
```

### Template B: Authorized Current PII Lookup (`sat_*_pii`)

Restrict `sat_*_pii` access to authorized roles/views and resolve the latest non-zero state per `hk_<hub>` with `ROW_NUMBER()`:

```sql
WITH current_pii AS (
    SELECT
        p.hk_customer,
        p.email,
        p.load_dts,
        ROW_NUMBER() OVER (
            PARTITION BY p.hk_customer
            ORDER BY p.load_dts DESC
        ) AS _rn
    FROM raw_vault.sat_customer_erp_pii AS p
    WHERE p.hk_customer <> REPEAT('0', 64)
      AND p.hk_customer <> REPEAT('f', 64)
)
SELECT
    v.hk_customer,
    v.cust_id,
    v.legal_name,
    v.credit_tier,
    p.email
FROM information_mart.vw_customer_current AS v
LEFT JOIN current_pii AS p
    ON p.hk_customer = v.hk_customer
   AND p._rn = 1;
```

---

## 4. `survivorship_priority` vs. `companion_hub_with_sal` Survivorship

- **Unified Canonical Hub (`multi_source_hub_strategy="unified_canonical_when_same_arity"`):** Compatible same-arity sources share one Hub (e.g., `hub_customer`). Setting `survivorship_priority` on the Hub (e.g., `["erp", "crm"]` or `["src_1:table_a", "src_1:table_b"]`) orders its non-`PII`, non-`MAS` Satellites in `information_mart.vw_<hub>_current`, generating type-safe `COALESCE(NULLIF(TRIM(sat_erp.col), ''), NULLIF(TRIM(sat_crm.col), ''))` expressions (`DV-STR-017` validates compatible column types across distinct sources, while `sat_*_ext_vN` extensions sort ahead of their base Satellite by descending `companion_epoch`).
- **Companion Hubs with Same-As Link (`multi_source_hub_strategy="companion_hub_with_sal"`):** When sources use incompatible keyspaces, differing BK arities, or locked Wave-1 Hubs, each source receives its own companion Hub connected via `business_vault.sal_<entity>` (`hk_master`, `hk_duplicate`) plus optional `esat_sal_*`. **`survivorship_priority` applies solely to unified same-Hub Satellites**—disjoint companion Hubs have separate `hk_*` keys, so downstream queries join `vw_<master_hub>_current` and `vw_<companion_hub>_current` through `business_vault.sal_<entity>` and apply explicit `COALESCE(master.col, duplicate.col)` survivorship in the mart query.

---

## 5. Worked Walkthrough: 6-Table Business Vault Hints & Survey

The fixture directory `tests/fixtures/business_vault_hints/` (`erp_customers.csv`, `crm_customers.csv`, `crm_account_mgr.csv`, `funds.csv`, `accounts.csv`, `trades.csv`, `manifest.json`) demonstrates zero-hints discovery through DuckDB-verified compilation:

1. **Stage A — Zero-Hints Survey (`opendv model profile`):**
   - `erp_customers.csv` (`cust_id=C-100, C-200`) holds authoritative `legal_name='Acme Capital'`, `credit_tier='AAA'`, `billing_region=NULL`, `email=NULL` on `C-100` (and `email='ops@beacon.io'` on `C-200`).
   - `crm_customers.csv` holds `legal_name='Acme Cap'`, `credit_tier='A'`, `billing_region='US-EAST'`, `email='billing@acme.io'` on `C-100` (and stale `'old@beacon.io'` on `C-200`).
   - `crm_account_mgr.csv` assigns `C-100` to `REP-1` (`2026-01-01`) and reassigns `REP-2` (`2026-02-01`).
   - `trades.csv -> accounts.csv -> funds.csv` forms a 2-hop chain (`T-900 -> A-10 -> F-01`).

   Running paginated discovery with `--max-questions 3`:

   ```bash
   uv run opendv --json model profile \
     --sources-file tests/fixtures/business_vault_hints/manifest.json \
     --max-questions 3 \
     --questions-offset 0 \
     --out profile_bundle.json
   ```

   Returns `total_candidates=5`, `returned_count=3`, `offset=0`, `remaining_count=2`:
   - **P1:** Customer survivorship conflict across `erp_customers` and `crm_customers` (`credit_tier`, `legal_name`, `billing_region`, plus masked PII `email` showing `<NULL>` vs `<NON_NULL>` without echoing raw addresses).
   - **P2:** 2-hop analytical chain `trades -> accounts -> funds` (`brg_*` candidate).
   - **P3:** `crm_account_mgr` assignment timeline (`esat_*` driving-key interval candidate).

   Fetch the remaining two lower-priority questions (`trades` immutability and `credit_tier` reference-table check) via `--questions-offset 3` (or fetch all remaining with `--max-questions 0`):

   ```bash
   uv run opendv --json model profile \
     --sources-file tests/fixtures/business_vault_hints/manifest.json \
     --max-questions 0 \
     --questions-offset 3 \
     --out profile_bundle_rest.json
   ```

2. **Stage B — Lowered `DraftVaultIR` & DuckDB Verification (`opendv model verify`):**
   Encode the interview decisions into `draft.json` and `req.json`:
   - **`hub_customer`** with `survivorship_priority: ["erp", "crm"]` (`sat_customer_erp` and `sat_customer_crm` unified; `email` isolated in `sat_customer_erp_pii` / `sat_customer_crm_pii`).
   - **`brg_trade_account_fund`** (`DraftBridgeSpec` with `root_hub="hub_trade"` and 2 `BridgeHopSpec` hops across `lnk_trade_account` and `lnk_account_fund`).
   - **`esat_customer_rep_crm`** (`sat_kind="ESAT"`, `zone_id="Z2"`, `driving_keys=["hk_customer"]`) paired with derived `Z3` view `business_vault.vw_esat_customer_rep_crm_intervals`.
   - **`t_lnk_trade_account`** (`link_kind="TRANSACTIONAL"`) preserving immutable trade executions.

   ```bash
   uv run opendv --json model verify \
     --input req.json \
     --draft-ir draft.json \
     --profile profile_bundle.json \
     --out verify_result.json
   ```

   Verification exits `0` (`status="CERTIFIED_FAST"`, `final_energy_e == 0`). Compiling and loading in DuckDB yields:
   - `information_mart.vw_customer_current`: `C-100` resolves `legal_name='Acme Capital'`, `credit_tier='AAA'`, and `billing_region='US-EAST'` (via CRM fallback where ERP is `NULL`), while `email` remains excluded in `sat_customer_*_pii`.
   - `business_vault.brg_trade_account_fund`: resolves `(T-900, A-10, F-01)` via zero-key-guarded `LEFT JOIN`s.
   - `business_vault.vw_esat_customer_rep_crm_intervals`: closes `REP-1` at `effective_to='2026-02-01'` and marks `REP-2` `is_current=TRUE`.
   - `raw_vault.t_lnk_trade_account`: remains strictly immutable.
