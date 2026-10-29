# 02 — Data Vault 2.0 Schema Stability Rules, Invariants, Evolution Mechanics, and Anti-Patterns

> **Part of the [`open-dv` Reference Guide Suite](DV2_AGENT_REFERENCE_GUIDE.md)**  
> **Domain:** Fundamental Stability Invariants (`INV-01`–`INV-06`), Source Schema Drift Playbooks (`EVO-01`–`EVO-08`), Key & Bi-Temporal Rules (`KEY-01`–`KEY-05`, `TMP-01`–`TMP-04`), Early-Arriving Fact Stubbing, Anti-Pattern Catalog (`AP-01`–`AP-15`), and Pragmatic Cold-Start ($N=1$) to Multi-Source ($N+1$) Evolution  
> **Companion Specifications:**
> - [01 — Core Architecture, Entity Specifications, and Methodology Standards](01-core-modelling-spec.md)
> - [03 — Automated 6-Gate Verification, `dv-firstmate` Swarm, and Agentic Architecture](03-verification-and-agentic-architecture.md)
> - [04 — Unstructured & Multi-Modal Data Vault 2.0 Integration](04-unstructured-and-multimodal-dv2.md)
> - [05 — ELM Calculus, Graph B Profiling Math, 3-Pass CoT Prompt, and Reference Algorithms (`A.1`–`A.9` & `D.10`)](05-elm-calculus-and-reference-algorithms.md)
> - [Full HTML Reference Guide (`docs/references/No-Mistake-Agentic-Data-Vault-2.0-Complete-Design-and-Implementation-Guide.html`)](references/No-Mistake-Agentic-Data-Vault-2.0-Complete-Design-and-Implementation-Guide.html)
> - [Canonical IR JSON Schema (`schemas/dv2-canonical-ir.schema.json`)](../schemas/dv2-canonical-ir.schema.json) & [No-Mistake Contract V5 Schema (`schemas/no-mistake-dv2-contract-v5.schema.json`)](../schemas/no-mistake-dv2-contract-v5.schema.json)
> - [Annotated Multi-Source Model Contract (`examples/dv_model_contract.yaml`)](../examples/dv_model_contract.yaml)

---

## Executive Summary

To enable an autonomous agentic system (`open-dv`) to design, evolve, and verify Data Vault 2.0 (DV 2.0) schemas without breaking downstream consumers or corrupting historical audit trails, schema stability cannot rely on informal conventions. It must be expressed as **formal, machine-checkable invariants**, **deterministic key/temporal algorithms**, **additive-only schema evolution playbooks**, and **static/runtime anti-pattern lint gates**.

This specification establishes:
1. **Fundamental Stability Invariants (`INV-01` through `INV-06`)** governing Raw Vault insert-only immutability, virtual end-dating, Hub semantic grain invariance, Link Unit-of-Work grain invariance, strict triad separation of concerns, and idempotent delta suppression.
2. **Non-Breaking Schema Evolution Playbooks (`EVO-01` through `EVO-08` & `EVO-01a`)** mapping every operational source schema drift event to an additive Raw Vault evolution action and a Ghost-Record-backed Business Vault consumer shield.
3. **Key Canonicalization & Bi-Temporal Rules (`KEY-01`–`KEY-05`, `TMP-01`–`TMP-04`)** including mathematical proofs of delimiter-shift and `CONCAT_WS` NULL-skipping hash collisions, injective length-prefixed encoding, dual-timeline bi-temporal point-in-time queries, and early-arriving fact Hub stubbing.
4. **Comprehensive Anti-Pattern Catalog (`AP-01` through `AP-15`)** with structural signatures, failure modes, and automated AST/DDL verification gates.

---

## 1. Fundamental Stability Invariants

### 1.1 Formal Invariant Table (`INV-01` to `INV-06`)

| Invariant ID | Name | Formal Specification | Agent Verification Check |
| :--- | :--- | :--- | :--- |
| **`INV-01`** | **Raw Vault Insert-Only Immutability** | Every table in the Raw Vault (`hub_*`, `lnk_*`, `sat_*`, `esat_*`, `mas_*`, `sts_*`, `rts_*`) is strictly append-only (`INSERT` only). `UPDATE`, `DELETE`, `TRUNCATE`, and `MERGE ... WHEN MATCHED THEN UPDATE/DELETE` statements are forbidden on Raw Vault targets. | AST check on all generated DML pipelines: reject any statement targeting a Raw Vault relation that contains `UPDATE`, `DELETE`, `TRUNCATE`, or `OVERWRITE`. |
| **`INV-02`** | **Virtual End-Dating Over Physical Mutation** | Raw Vault Satellites must never store a physically mutated `load_end_dts` or `is_current` column updated on subsequent loads. Effective intervals `[effective_from_dts, effective_to_dts)` are derived virtually via `LEAD(load_dts) OVER (PARTITION BY hk_parent ORDER BY load_dts ASC)` or pre-joined in disposable Business Vault PIT tables. | DDL check on `sat_*` / `esat_*`: forbid mutable `load_end_dts`, `valid_to`, or `current_flag` columns in Raw Vault tables. |
| **`INV-03`** | **Hub Semantic Grain Invariance** | Once a Hub `hub_<concept>` is deployed, its semantic grain and business key column list `(bkcc, bk_col_1, ..., bk_col_n)` are immutable. Altering the business key columns or semantics of an existing Hub is forbidden. | Schema diff check: `hub_*` business key column names, count, order, and canonicalization expression must match the baseline registry exactly. |
| **`INV-04`** | **Link Unit-of-Work Grain Invariance** | A Link `lnk_<rel>` represents a fixed N-ary intersection of parent Hubs `(hk_hub_1, ..., hk_hub_k)` (plus optional degenerate Dependent Child keys for transactional/multi-active grain). Adding, removing, or changing the participating Hub foreign keys of an existing Link is forbidden. | Schema diff check: `lnk_*` foreign key column set is frozen after creation. Any change in arity or participating Hubs requires a new Link table. |
| **`INV-05`** | **Strict Triad Separation of Concerns** | **(a) Hubs** store *only* identity (`hk`, `bkcc`, `bk_*`, `load_dts`, `rec_src`). **(b) Links** store *only* relationship keys (`hk_lnk`, `hk_hub_*`, optional degenerate child key, `load_dts`, `rec_src`). **(c) Satellites** store *only* descriptive state and temporal context attached to *exactly one* parent Hub or Link (`hk_parent`, `load_dts`, `hash_diff`, `rec_src`, payload). | Structural linter: (a) No descriptive payload or FKs in Hubs; (b) No mutable state, start/end dates, or non-key payload in standard Links; (c) Every Satellite has exactly 1 parent FK (`hk_hub` or `hk_lnk`) and 0 FKs to other Satellites or Hubs. |
| **`INV-06`** | **Idempotent Delta Suppression** | A standard Satellite inserts a new row for `hk_parent` at `load_dts` if and only if no prior row exists for `hk_parent` or the incoming `hash_diff` differs from the most recent `hash_diff` for `hk_parent`. | Pipeline SQL check: every `sat_*` load query must include a latest-state `hash_diff` anti-join (`WHERE stg.hash_diff != current_sat.hash_diff OR current_sat.hk IS NULL`). |

---

### 1.2 Insert-Only Immutability & Virtual End-Dating Mechanics

In legacy Data Vault 1.0 and Kimball SCD Type 2 implementations, arriving state changes triggered two operations: inserting the new row and running an `UPDATE` to set `load_end_dts` on the previous row. In modern cloud columnar data warehouses (Snowflake, BigQuery, Databricks Delta, Iceberg, DuckDB) and distributed streaming architectures, physical `UPDATE`s on Satellites cause:
1. **Micro-partition / Parquet file rewrite amplification** (rewriting gigabytes of historical storage to flip one timestamp).
2. **Read/write concurrency lock contention** and non-idempotent failure recovery if a batch fails mid-update.
3. **Destruction of strict immutability** required for regulatory audit compliance.

#### Canonical Raw Satellite DDL & Virtual End-Dating View
```sql
-- Raw Vault Satellite: strictly insert-only, no physical load_end_dts column
CREATE TABLE sat_customer_core (
    hk_customer     CHAR(64)       NOT NULL,
    load_dts        TIMESTAMP_NTZ  NOT NULL,
    hash_diff       CHAR(64)       NOT NULL,
    rec_src         VARCHAR(128)   NOT NULL,
    customer_tier   VARCHAR(32),
    credit_limit    NUMERIC(18, 2),
    CONSTRAINT pk_sat_customer_core PRIMARY KEY (hk_customer, load_dts),
    CONSTRAINT fk_sat_customer_hub  FOREIGN KEY (hk_customer) REFERENCES hub_customer(hk_customer)
);

-- Virtualized End-Dating View (Half-Open Interval: [effective_from_dts, effective_to_dts))
CREATE VIEW v_sat_customer_core_effective AS
SELECT
    hk_customer,
    load_dts AS effective_from_dts,
    COALESCE(
        LEAD(load_dts) OVER (
            PARTITION BY hk_customer
            ORDER BY load_dts ASC
        ),
        CAST('9999-12-31T23:59:59.999999Z' AS TIMESTAMP_NTZ)
    ) AS effective_to_dts,
    CASE
        WHEN LEAD(load_dts) OVER (
            PARTITION BY hk_customer
            ORDER BY load_dts ASC
        ) IS NULL THEN TRUE
        ELSE FALSE
    END AS is_current,
    hash_diff,
    rec_src,
    customer_tier,
    credit_limit
FROM sat_customer_core;
```

> [!IMPORTANT]
> **Half-Open Interval Rule (`[from, to)`):** Notice that `effective_to_dts` is set to the exact `load_dts` of the *next* record (without subtracting 1 millisecond or 1 microsecond). Downstream point-in-time queries MUST evaluate `as_of_dts >= effective_from_dts AND as_of_dts < effective_to_dts`. Subtracting arbitrary time units (`- INTERVAL '1 second'`) is brittle across database engines with differing timestamp precisions (millisecond vs. microsecond vs. nanosecond) and creates temporal gaps or overlaps.

#### Handling Source Hard Deletes Without `DELETE` or `UPDATE`
When a source system physically deletes a record (detected via CDC tombstone or full-snapshot anti-join), the Raw Vault never deletes or updates the Hub, Link, or descriptive Satellite. Instead, it appends a record to a **Record Tracking Satellite (`rts_*`)** or **Status Tracking Satellite (`sts_*`)**:
```sql
CREATE TABLE sts_customer_status (
    hk_customer     CHAR(64)       NOT NULL,
    load_dts        TIMESTAMP_NTZ  NOT NULL,
    rec_src         VARCHAR(128)   NOT NULL,
    status_code     CHAR(1)        NOT NULL, -- 'I' = Inserted, 'D' = Deleted, 'U' = Re-appeared
    is_deleted      BOOLEAN        NOT NULL, -- TRUE when tombstone/missing in full snapshot
    PRIMARY KEY (hk_customer, load_dts)
);
```

---

### 1.3 Grain Invariance & How Grain Changes Are Handled Safely

#### Case A: Hub Semantic Grain Shift
Suppose `hub_account` was initially modeled with business key `account_number`. Two years later, a corporate restructuring makes `account_number` non-unique unless paired with `legal_entity_code`, or splits "Billing Account" from "Customer Organization".
- **Why in-place mutation fails:** Recomputing `hk_account = SHA256(legal_entity_code || account_number)` changes the primary key of every historical row, breaking all historical Satellites and Links referencing the old `hk_account`. Leaving old hashes while hashing new rows with the new composite formula causes silent entity duplication (the same real-world account gets two different `hk_account` values before and after the cutover date).
- **Non-Breaking Resolution Playbook:**
  1. **If the change is a scoping disambiguation** where legacy keys belong to a known default scope (e.g., `legal_entity_code = 'LEGACY_US'`), the `bkcc` (Business Key Collision Code) pattern is used if already present; if `bkcc` was omitted, introduce a new Hub `hub_legal_account` with explicit `(bkcc, bk_account_number)` or map legacy keys via a **Same-As Link (`sal_account`)**.
  2. **If the semantic concept changed** (e.g., `hub_customer` was actually storing `site_id`), create the new Hub `hub_site` at the true grain, preserve `hub_customer` intact, and link them via a **Link (`lnk_customer_site`)** + Business Vault semantic view.

#### Case B: Link Arity / Grain Shift (Unit of Work Change)
Suppose `lnk_order_line` initially linked `(hk_order, hk_product)`. Later, a single order line can be split across multiple fulfillment warehouses (`hk_warehouse`), changing the relationship grain to `(hk_order, hk_product, hk_warehouse)`.
- **Why in-place mutation fails (`INV-04`):** `hk_lnk` is the hash of all participating parent keys. Adding `hk_warehouse` to `lnk_order_line` means `hk_lnk` can no longer be computed deterministically for historical rows where `hk_warehouse` was unknown, or requires mutating existing primary keys referenced by `sat_lnk_order_line`.
- **Non-Breaking Resolution Playbook:**
  1. Freeze or continue populating `lnk_order_line` at the `(hk_order, hk_product)` grain if that binary relationship still holds meaning.
  2. Create a **new Link** `lnk_order_product_fulfillment` with keys `(hk_order, hk_product, hk_warehouse)` (and its own `sat_lnk_order_product_fulfillment`).
  3. Expose a unified Business Vault Bridge or View that unions or left-joins the pre-cutover `lnk_order_line` (resolved to the Zero Key `hk_warehouse = '0000...0000'`) with the post-cutover `lnk_order_product_fulfillment`.

---

### 1.4 Strict Separation of Identity, Relationship, and State (Plus Structural Specializations)

To keep the triad (`Hub`, `Link`, `Satellite`) invariant under all source patterns, Data Vault 2.0 defines three specialized structural constructs that obey `INV-01` through `INV-05`:

1. **Dependent Child Keys (Degenerate Keys in Links / Multi-Active Satellites):**
   - When a relationship or descriptive child entity has no independent business existence outside its parent (e.g., `order_line_number` `1, 2, 3` within an `order_id`), `order_line_number` **must never become a Hub** (`hub_order_line_number` containing `'1'`, `'2'`, `'3'` is a classic anti-pattern `AP-15`).
   - Instead, `order_line_number` is carried as a **Dependent Child Key (`order_line_no` / `cdk`)** inside `lnk_order_item` (participating in `hk_lnk_order_item` and the Link unique constraint) or as the sub-key in a **Multi-Active Satellite (`mas_*`)** whose primary key is `(hk_parent, load_dts, cdk_child_key)`.
2. **Non-Historized Links (Transactional Links, `t_lnk_*` / `nh_lnk_*`):**
   - Immutable high-velocity atomic events (e.g., IoT telemetry pings, point-of-sale ledger postings) that **never change state after arrival** can be stored in a Non-Historized Link (`t_lnk_*`) carrying the participating Hub keys, a degenerate `transaction_id`, `event_dts`, `load_dts`, `rec_src`, and immutable event measures. Once inserted, a `t_lnk_*` row is never updated or superseded.
3. **Reference Tables (`ref_*` + `sat_ref_*`):**
   - Lookup codes (ISO currency codes, country codes) avoid Hub/Link overhead by residing in historized Reference Satellites (`ref_code`, `load_dts`, `hash_diff`, `description`) joined by natural code rather than hash keys.

---

## 2. Non-Breaking Schema Evolution Rules

### 2.1 Source Schema Drift Decision Matrix (`EVO-01` to `EVO-08`)

When an upstream operational source drifts, the `open-dv` agent must **never execute destructive DDL** (`DROP COLUMN`, incompatible `ALTER COLUMN TYPE`, `RENAME TABLE`, or table truncation/re-hashing) on Raw Vault tables. Every source drift event maps deterministically to an additive evolution action:

| Rule ID | Source Schema Drift Event | Raw Vault Evolution Action | Business Vault / Consumer Shielding |
| :--- | :--- | :--- | :--- |
| **`EVO-01`** | **Added Column(s)** (New attributes added to source table) | **Option A (Preferred for stability):** Create a new Satellite `sat_<entity>_<domain_ext>` attached to the same Hub/Link, OR create versioned Satellite `sat_<entity>_v2`.<br>**Option B (In-place additive):** `ALTER TABLE sat_<entity> ADD COLUMN <col> NULL` **ONLY IF** the `hash_diff` transition rule (`EVO-01a` below) is enforced to prevent a full-table false delta reload. | Extend PIT table with `(sat_ext_hk, sat_ext_ldts)` initialized to Ghost Record, and expose new column in semantic view `bv_<entity>`. |
| **`EVO-02`** | **Dropped Column(s)** (Source stops sending an attribute) | **Never `DROP COLUMN` in Raw Vault** (violates historical auditability).<br>**Action:** Either (a) map missing source column to `NULL` in staging (noting this triggers one state transition row where the dropped attribute becomes `NULL`), OR (b) freeze `sat_<entity>_v1` and route subsequent loads to `sat_<entity>_v2` (which omits the dropped column). | Semantic view `bv_<entity>` projects the column from `v1` for historical dates and `NULL` after `v1` freeze date. |
| **`EVO-03`** | **Renamed Column** (Same semantics & grain, new source field name) | **Zero DDL change in Raw Vault.** Map `stg.new_col_name AS legacy_col_name` in the Staging layer. Because `hash_diff` hashes column *values* in fixed alphabetical target-column order, `hash_diff` remains 100% invariant. | Downstream views can project both `legacy_col_name` (deprecated alias) and `new_col_name`. |
| **`EVO-04`** | **Widened Data Type** (e.g., `VARCHAR(50) -> VARCHAR(200)`, `INT -> BIGINT`, `NUMERIC(10,2) -> NUMERIC(18,2)`) | Execute metadata-only `ALTER TABLE sat_<entity> ALTER COLUMN <col> SET DATA TYPE <wider_type>`. Because canonical `hash_diff` formatting (`KEY-01`) trims strings and normalizes numeric scale, existing `hash_diff` values remain identical. | Transparent to PIT and semantic views; widen view column casts if explicit. |
| **`EVO-05`** | **Incompatible Data Type Change** (e.g., `VARCHAR('AA+')` rating code changed to `INTEGER(820)` credit score) | **Never overwrite or cast-in-place.** Either add a new column `rating_score_int` alongside `rating_code_str` in a new Satellite `sat_<entity>_credit_v2` (deprecating `v1`), or split into a dedicated Satellite. | PIT table joins both `v1` and `v2` via Ghost Records; Business Vault view computes a harmonized column (`CASE WHEN v2.hk != GHOST THEN ... ELSE ... END`). |
| **`EVO-06`** | **Rate-of-Change Divergence** (A newly volatile column causes frequent duplicate storage of wide static columns) | **Satellite Splitting:** Split `sat_<entity>` into `sat_<entity>_static` (low frequency) and `sat_<entity>_volatile` (high frequency). Keep legacy `sat_<entity>` frozen for historical audit or backfill the split Satellites insert-only from `sat_<entity>`. | PIT table tracks `(sat_static_ldts, sat_volatile_ldts)` independently, eliminating storage bloat while presenting a single unified row to consumers. |
| **`EVO-07`** | **PII / Security Reclassification** (Columns reclassified as GDPR/CCPA restricted or confidential) | **Security Satellite Split:** Isolate sensitive attributes into `sat_<entity>_pii` with separate encryption keys / RBAC masking policies and independent crypto-shredding / right-to-be-forgotten lifecycle. | Non-privileged consumers query `bv_<entity>_public` (joining only non-PII Satellites); privileged consumers query `bv_<entity>_full`. |
| **`EVO-08`** | **Split / Merged Source Tables or New Source System** | **Passive Integration:** Load new source BKs into existing `hub_<concept>` (with distinct `bkcc` if key namespaces can collide) and attach source-specific `sat_<concept>_<new_src>`.<br>**Active Integration:** Reconcile cross-source BKs via `sal_<concept>` + `esat_sal_<concept>`. | Business Vault **Mastered / Golden Entity View** traverses `sal_<concept>` to roll up all Satellites to the surviving master Hub key. |

#### Deep Dive (`EVO-01a`): Why Naive `ALTER TABLE ADD COLUMN` Causes False Delta Storms & How to Prevent It
If `sat_customer` has columns `(tier, credit_limit)` and an engineer runs `ALTER TABLE sat_customer ADD COLUMN loyalty_points INT` and immediately adds `COALESCE(CAST(loyalty_points AS VARCHAR), '^^')` to the staging `hash_diff` formula:
- Even for customers whose `loyalty_points` is `NULL` (unchanged!), the new `hash_diff` hashes `'STANDARD||5000.00||^^'`, whereas the stored historical `hash_diff` in `sat_customer` hashed `'STANDARD||5000.00'`.
- Consequently, on the very next batch run, **100% of active entities in the Hub will fail the `stg.hash_diff = sat.hash_diff` equality check and insert a redundant duplicate row** into `sat_customer`!
- **Prevention Rule (`EVO-01a`):**
  1. **Preferred:** Put new attributes into a new Satellite (`sat_customer_loyalty` or `sat_customer_v2`). Existing `sat_customer` `hash_diff` is untouched; zero false delta rows are generated.
  2. **Alternative (if in-place `ADD COLUMN` is mandated):** Only include the newly added column in the `hash_diff` input string when its value is `NOT NULL` (or append a versioned delta record only for rows where `loyalty_points IS NOT NULL`), OR create `sat_customer_v2` at cutover timestamp $T_{\text{cutover}}$.

---

### 2.2 Satellite Versioning, Ghost Records, and PIT/Bridge Consumer Protection

When `sat_customer_credit_v1` is deprecated at $T_{\text{cutover}}$ and replaced by `sat_customer_credit_v2`, downstream analytical queries, BI dashboards, and ML feature pipelines must not break or require rewriting. This is achieved via a 3-layer insulation architecture:

```text
  [sat_customer_credit_v1] (Frozen at T_cutover; contains Ghost Record 0000...0)
             \
              +---> [pit_customer_credit] (Business Vault Helper: EQUI-JOIN spine)
             /              |
  [sat_customer_credit_v2]  +---> [bv_customer_credit] (Stable Consumer Contract View)
  (Active >= T_cutover;
   contains Ghost Record)
```

#### Ghost Records vs. Zero Keys (Strict Distinction)
- **Zero Key (`hk = '0000...0000'` or `'-1'` / `'FFFFFFFF...FFFF'`):** Inserted into **Hubs** (and referenced by **Links**) when an incoming foreign business key in a source transaction is `NULL` (`-1` = Unknown) or explicitly inapplicable (`-2` = Not Applicable). It ensures every Link FK has a valid parent Hub row so `INNER JOIN`s between Links and Hubs never drop rows.
- **Ghost Record (`hk = '0000...0000'`, `load_dts = '1900-01-01T00:00:00Z'`):** Inserted into **every Satellite** (`sat_*`) with default/unknown attribute values (`'UNKNOWN'`, `0`, `NULL`). When a Point-in-Time (PIT) table is built for a Hub key `hk_customer` at snapshot timestamp `as_of_dts`, if a particular Satellite (`sat_customer_credit_v2` before $T_{\text{cutover}}$) has no active record yet for that customer, the PIT table populates `sat_v2_hk = '0000...0000'` and `sat_v2_ldts = '1900-01-01T00:00:00Z'`.
- **Why this guarantees stability:** Consumers joining the PIT table to 5 or 10 Satellites can use pure `INNER JOIN`s (`EQUI-JOIN`s) on `(hk, load_dts)` rather than slow, error-prone `LEFT OUTER JOIN` cascades, even when Satellites are added, split, or deprecated across time!

#### Concrete SQL Pattern: PIT Continuity & Unified Consumer View Across `v1` and `v2`

```sql
-- 1. Ghost record seeded into every Satellite upon table creation
INSERT INTO sat_customer_credit_v1 (hk_customer, load_dts, hash_diff, rec_src, rating_code)
VALUES (REPEAT('0', 64), '1900-01-01T00:00:00Z', REPEAT('0', 64), 'SYSTEM.GHOST', 'UNKNOWN');

INSERT INTO sat_customer_credit_v2 (hk_customer, load_dts, hash_diff, rec_src, rating_score, risk_band)
VALUES (REPEAT('0', 64), '1900-01-01T00:00:00Z', REPEAT('0', 64), 'SYSTEM.GHOST', -1, 'UNKNOWN');

-- 2. PIT Table population mapping missing timelines to the Ghost Record
INSERT INTO pit_customer_credit (
    hk_pit_customer, hk_customer, as_of_dts,
    sat_v1_hk, sat_v1_ldts,
    sat_v2_hk, sat_v2_ldts
)
SELECT
    SHA256(CONCAT(h.hk_customer, '||', CAST(:as_of_dts AS VARCHAR))) AS hk_pit_customer,
    h.hk_customer,
    :as_of_dts AS as_of_dts,
    COALESCE(v1.hk_customer, REPEAT('0', 64))          AS sat_v1_hk,
    COALESCE(v1.max_ldts,    '1900-01-01T00:00:00Z')   AS sat_v1_ldts,
    COALESCE(v2.hk_customer, REPEAT('0', 64))          AS sat_v2_hk,
    COALESCE(v2.max_ldts,    '1900-01-01T00:00:00Z')   AS sat_v2_ldts
FROM hub_customer h
LEFT JOIN (
    SELECT hk_customer, MAX(load_dts) AS max_ldts
    FROM sat_customer_credit_v1
    WHERE load_dts <= :as_of_dts AND hk_customer != REPEAT('0', 64)
    GROUP BY hk_customer
) v1 ON h.hk_customer = v1.hk_customer
LEFT JOIN (
    SELECT hk_customer, MAX(load_dts) AS max_ldts
    FROM sat_customer_credit_v2
    WHERE load_dts <= :as_of_dts AND hk_customer != REPEAT('0', 64)
    GROUP BY hk_customer
) v2 ON h.hk_customer = v2.hk_customer;

-- 3. Stable Business Vault View shielding downstream consumers from v1 -> v2 split
CREATE VIEW bv_customer_credit AS
SELECT
    p.hk_customer,
    p.as_of_dts,
    s1.rating_code AS legacy_rating_code,
    s2.rating_score,
    s2.risk_band,
    CASE
        WHEN p.sat_v2_hk != REPEAT('0', 64) THEN s2.risk_band
        ELSE s1.rating_code
    END AS unified_credit_indicator
FROM pit_customer_credit p
INNER JOIN sat_customer_credit_v1 s1
    ON p.sat_v1_hk = s1.hk_customer AND p.sat_v1_ldts = s1.load_dts
INNER JOIN sat_customer_credit_v2 s2
    ON p.sat_v2_hk = s2.hk_customer AND p.sat_v2_ldts = s2.load_dts;
```

---

## 3. Key & Temporal Stability Edge Cases

### 3.1 Formal Key & Temporal Rules (`KEY-01` to `KEY-05`, `TMP-01` to `TMP-04`)

| Rule ID | Domain | Formal Specification |
| :--- | :--- | :--- |
| **`KEY-01`** | **Business Key String Canonicalization** | Before hashing or storing in a Hub/Link, every Business Key component must be cast to UTF-8 string, Unicode-normalized (NFC), stripped of leading and trailing whitespace (`TRIM`), and uppercased (`UPPER`) unless the domain explicitly marks the key as case-sensitive (e.g., Base64 IDs). |
| **`KEY-02`** | **Asymmetric NULL & Empty-String Handling** | **(a) Empty strings (`''`)** after `TRIM` must be coerced to `NULL` (`NULLIF(TRIM(col), '')`).<br>**(b) Hub Hash Key:** If *all* BK columns (excluding `bkcc`) are `NULL`, the row must resolve to the Zero Key (`REPEAT('0', 64)` with BK `'-1'`) or be rejected to an error staging queue—never hash `bkcc` alone into a real Hub key. If *some* columns of a composite BK are `NULL`, substitute the explicit null token `'-1:^^'` (or `'^^'` in escaped mode) for the `NULL` positions.<br>**(c) Satellite `hash_diff`:** Every `NULL` attribute *must* be substituted with the explicit null token `'-1:^^'` (or `'^^'`) so transitions `VALUE -> NULL` and `NULL -> VALUE` produce a distinct digest. |
| **`KEY-03`** | **Collision-Free Composite Concatenation** | Naive delimiter concatenation (`a || '||' || b`) and raw `CONCAT_WS('||', a, b)` on nullable columns are **forbidden** for composite keys and `hash_diff` payloads. Composite inputs must use **Length-Prefixed Canonical Encoding** (`<byte_len>:<val>\|\|<byte_len>:<val>`) or **Escaped Delimiter Encoding** (`REPLACE(val, '\', '\\')`, `REPLACE(val, '||', '\||')`, `REPLACE(val, '^^', '\^^')`) with explicit positional null tokens prior to concatenation. |
| **`KEY-04`** | **Business Key Collision Code (`bkcc`) vs. `rec_src`** | Every Hub integrating keys from potentially colliding namespaces must include `bkcc` in both the Hub unique business key constraint `UNIQUE(bkcc, bk_1, ..., bk_n)` and the Hub Hash Key input `HASH(bkcc, bk_1, ..., bk_n)`. Conversely, `rec_src` is technical audit metadata and **must never** be hashed into `hk_hub` or `hk_lnk`. |
| **`KEY-05`** | **Deterministic Link Key Ordering** | For standard distinct-entity Links (`lnk_a_b`), parent Hub keys are hashed in the fixed DDL column order `(hk_hub_1, ..., hk_hub_k)`. For symmetric self-referencing Links (e.g., `lnk_person_marriage`), canonical sort ordering `LEAST(hk_1, hk_2) || GREATEST(hk_1, hk_2)` is only used if the relationship is strictly undirected; for directed self-links (`sal_customer`, `hal_org_hierarchy`), role-qualified columns (`hk_customer_master`, `hk_customer_duplicate`) preserve directionality. |
| **`TMP-01`** | **Multi-Timeline Separation** | Every temporal timestamp must be classified into one of three distinct columns:<br>1. `load_dts`: Immutable system assertion time when the row entered the Data Vault.<br>2. `applied_dts` (or `effective_dts`): Source/business validity timestamp when the state took effect in reality.<br>3. `event_dts`: Immutable business event occurrence timestamp on transactional links/satellites. |
| **`TMP-02`** | **Non-Spoofing of `load_dts`** | Late-arriving records must **never** back-date `load_dts` to match `applied_dts`. `load_dts` must always equal the actual batch/stream ingestion timestamp so point-in-time audit reproducibility ("what did the warehouse report on date X?") remains intact. |
| **`TMP-03`** | **Hub-First Referential Integrity & Stubbing** | No Link or Satellite record may be inserted unless its referenced `hk_hub` (or `hk_lnk`) exists in the parent table. When an early-arriving fact/link stream contains a business key not yet present in `hub_<concept>`, the ingestion pipeline must automatically insert a **stub Hub row** (`rec_src = '<stream_name> (STUB)'`) in the same load wave. |
| **`TMP-04`** | **Bi-Temporal Out-of-Order Satellite Grain** | When a source system can emit out-of-order state updates for the same entity (or multiple historical states within one batch), the Satellite primary key must be `(hk_parent, load_dts, applied_dts)` (or include a source sequence number), and `hash_diff` deduplication must compare against the temporally adjacent state on the `applied_dts` timeline. |

---

### 3.2 Proof of Hash Collisions in Naive Concatenation & The Canonical Hashing Formula

Two critical vulnerabilities exist in naive SQL hashing templates:

1. **Delimiter Shift Collision (`AP-08a`):**
   - Record 1: `col_a = 'ANNE||O'`, `col_b = 'BRIEN'` $\rightarrow$ `'ANNE||O' || '||' || 'BRIEN'` = `'ANNE||O||BRIEN'`
   - Record 2: `col_a = 'ANNE'`, `col_b = 'O||BRIEN'` $\rightarrow$ `'ANNE' || '||' || 'O||BRIEN'` = `'ANNE||O||BRIEN'`
   - Both produce SHA-256 `3B724334553C2566...`, merging two distinct business entities or suppressing a legitimate Satellite attribute change!
2. **`CONCAT_WS` NULL-Skipping Positional Shift Collision (`AP-08b`):**
   - Many SQL developers use `CONCAT_WS('||', col_1, col_2, col_3)` directly on nullable columns for `hash_diff`. Because standard SQL `CONCAT_WS` silently ignores `NULL` arguments:
   - State 1: `(col_1 = 'VAL_X', col_2 = NULL, col_3 = 'VAL_Y')` $\rightarrow$ `CONCAT_WS` outputs `'VAL_X||VAL_Y'`
   - State 2: `(col_1 = 'VAL_X', col_2 = 'VAL_Y', col_3 = NULL)` $\rightarrow$ `CONCAT_WS` outputs `'VAL_X||VAL_Y'`
   - When an entity transitions from State 1 to State 2, `hash_diff` is identical (`equal=True`), and **the Satellite silently drops the update!**

#### The `open-dv` Length-Prefixed Canonical Hash Encoding Specification
To eliminate both delimiter shift collisions and positional NULL shifts across all SQL engines, every composite Business Key and `hash_diff` expression generated by `open-dv` can wrap each column `c_i` in a **Length-Prefixed Null-Safe Macro** (or the equivalent **Delimiter-Escaped Null-Safe Macro** in [Section 4.2 of `01-core-modelling-spec.md`](01-core-modelling-spec.md#42-the-6-stage-pre-hash-canonicalization-pipeline)):

$$\text{enc}(c_i) = \begin{cases} \texttt{'-1:\^\^'} & \text{if } \text{NULLIF}(\text{TRIM}(\text{CAST}(c_i \text{ AS VARCHAR})), \texttt{''}) \text{ IS NULL} \\ \text{OCTET\_LENGTH}(\text{norm}(c_i)) \mathbin{\Vert} \texttt{':'} \mathbin{\Vert} \text{norm}(c_i) & \text{otherwise} \end{cases}$$

where $\text{norm}(c_i) = \text{UPPER}(\text{TRIM}(\text{CAST}(c_i \text{ AS VARCHAR})))$ for Hub/Link business keys (and case-preserving $\text{TRIM}(\text{CAST}(c_i \text{ AS VARCHAR}))$ for case-sensitive Satellite payload columns in `hash_diff`).

```sql
-- Portable SQL macro generated by open-dv for a 2-part Hub Business Key (bkcc, customer_id)
CASE
    WHEN NULLIF(TRIM(CAST(src.customer_id AS VARCHAR)), '') IS NULL
    THEN REPEAT('0', 64) -- Zero Key for missing business key
    ELSE UPPER(ENCODE(SHA256(
        CONCAT(
            CAST(OCTET_LENGTH(UPPER(TRIM(CAST(src.bkcc AS VARCHAR)))) AS VARCHAR), ':', UPPER(TRIM(CAST(src.bkcc AS VARCHAR))),
            '||',
            CAST(OCTET_LENGTH(UPPER(TRIM(CAST(src.customer_id AS VARCHAR)))) AS VARCHAR), ':', UPPER(TRIM(CAST(src.customer_id AS VARCHAR)))
        )
    ), 'hex'))
END AS hk_customer
```

With this encoding:
- `('ANNE||O', 'BRIEN')` encodes to `'7:ANNE||O||5:BRIEN'`
- `('ANNE', 'O||BRIEN')` encodes to `'4:ANNE||8:O||BRIEN'`
- `('VAL_X', NULL, 'VAL_Y')` encodes to `'5:VAL_X||-1:^^||5:VAL_Y'`
- `('VAL_X', 'VAL_Y', NULL)` encodes to `'5:VAL_X||5:VAL_Y||-1:^^'`
Mathematically, this encoding is **injective (one-to-one)** over all tuples of nullable strings: two distinct tuples can never produce the same pre-hash string.

---

### 3.3 Business Key Collision Codes (`bkcc`), Passive vs. Active Integration, and Same-As Links

When multiple source systems feed a single business concept (e.g., `hub_customer` fed by `SAP_ERP`, `SALESFORCE_CRM`, and `STRIPE_BILLING`), two opposite hazards arise:
1. **Key Collision Hazard (Homonyms):** `SAP_ERP` Customer `10042` is "Acme Corp", while `STRIPE_BILLING` Customer `10042` is "Jane Doe". If both load into `hub_customer` without a scope discriminator, they hash to the same `hk_customer` and corrupt each other's Satellites and Links.
2. **Siloed Hub Hazard (Synonyms):** Creating separate Hubs (`hub_sap_customer`, `hub_sfdc_customer`, `hub_stripe_customer`) destroys enterprise integration and forces downstream re-architecture whenever a new source system is added.

#### The Two-Stage Stability Pattern (`KEY-04` + `EVO-08`)
1. **Stage 1 — Passive Integration via `bkcc`:**
   - Assign each distinct key namespace a **Business Key Collision Code (`bkcc`)** (e.g., `'ERP_GLOBAL'`, `'CRM_NA'`, `'BILLING_STRIPE'`). Note: If two source tables share the exact same master key namespace (e.g., two microservices both using the enterprise UUID), they share the **same `bkcc`** (`'ENT_UUID'`) so they passively integrate in the Hub automatically!
   - Include `bkcc` in `hub_customer(hk_customer, bkcc, customer_id, load_dts, rec_src)`.
2. **Stage 2 — Active Integration via Same-As Link (`sal_customer`) + Effectivity Satellite:**
   - When Master Data Management (MDM) or deterministic identity resolution matches `'BILLING_STRIPE:10042'` (`hk_customer_duplicate`) to `'ERP_GLOBAL:90088'` (`hk_customer_master`), **never update or delete the Hub keys**.
   - Insert the pair `(hk_customer_master, hk_customer_duplicate)` into `sal_customer`, and track when the merge/unmerge is active in `esat_sal_customer` (with `hk_customer_duplicate` as the **Driving Key**, since each duplicate maps to one active master at any point in time).

---

### 3.4 Bi-Temporal & Multi-Timeline Stability (`load_dts` vs. `applied_dts` vs. `event_dts`)

Suppose a contract `CTR-900` undergoes three events:
1. **Jan 1:** Contract signed for `$1,000` (`applied_dts = 2026-01-01`, arrives on time `load_dts = 2026-01-01`).
2. **Jan 20:** Contract amended to `$2,500` (`applied_dts = 2026-01-20`, arrives on time `load_dts = 2026-01-20`).
3. **Jan 25 (Out-of-Order / Late-Arriving):** An interim amendment that actually took effect on **Jan 10** for `$1,800` (`applied_dts = 2026-01-10`) arrives late in the warehouse on **Jan 25** (`load_dts = 2026-01-25`).

#### Why Single-Timeline (`load_dts`-only) or Back-Dating Fails
- If the Satellite only tracks `load_dts` and inserts the late-arriving Jan 10 amendment (`$1,800`) with `load_dts = 2026-01-25`, a standard `LEAD(load_dts)` query thinks `$1,800` became the current state on Jan 25, **overriding the newer Jan 20 (`$2,500`) amendment!**
- Conversely, if the pipeline back-dates `load_dts = 2026-01-10` on Jan 25, any regulatory financial report run on Jan 22 for the Jan 15 state can no longer be reproduced (violating `TMP-02`).

#### The Bi-Temporal Satellite & Dual-Timeline Reconstruction SQL
By storing both `load_dts` (when the Vault knew it) and `applied_dts` (when it was effective in business reality) with primary key `(hk_contract, load_dts, applied_dts)`:

```sql
-- Bi-Temporal Point-in-Time Query parameterized by:
--   :knowledge_cutoff_dts (System Load Time — "What did we know as of this timestamp?")
--   :effective_as_of_dts  (Business Applied Time — "What was valid in the business at this timestamp?")
WITH known_states AS (
    SELECT
        hk_contract,
        contract_status,
        contract_amount,
        applied_dts,
        load_dts,
        ROW_NUMBER() OVER (
            PARTITION BY hk_contract
            ORDER BY applied_dts DESC, load_dts DESC
        ) AS rn
    FROM sat_contract_bitemporal
    WHERE load_dts    <= :knowledge_cutoff_dts
      AND applied_dts <= :effective_as_of_dts
)
SELECT hk_contract, contract_status, contract_amount, applied_dts, load_dts
FROM known_states
WHERE rn = 1;
```

- Querying `(knowledge = Jan 22, effective = Jan 15)` returns **`$1,000`** (reproducing the exact report generated before the late arrival).
- Querying `(knowledge = Jan 26, effective = Jan 15)` returns **`$1,800`** (reflecting the corrected historical state on Jan 15 now that the Jan 10 amendment has arrived).
- Querying `(knowledge = Jan 26, effective = Jan 22)` returns **`$2,500`** (correctly recognizing that the Jan 20 amendment still supersedes the late-arriving Jan 10 amendment!).

---

### 3.5 Early-Arriving Facts / Late-Arriving Hub Keys (Automated Stubbing Pattern)

When a high-velocity transactional stream (e.g., `ORDERS.STREAM`) arrives referencing `customer_id = 'C-9999'` (`bkcc = 'EU_WEST'`) before the nightly batch from `CRM.CUSTOMERS` has loaded `'C-9999'` into `hub_customer`:
1. **Do NOT hold/drop the order** in a staging suspense table (causes data loss and incomplete revenue reporting).
2. **Do NOT load `lnk_order_customer` without a parent `hub_customer` row** (violates `TMP-03` referential integrity).
3. **Execute Automated Hub Stubbing:** Extract all distinct `(bkcc, customer_id)` pairs from `stg_orders_stream` and run a standard idempotent Hub insert into `hub_customer` with `rec_src = 'ORDERS.STREAM (STUB)'` **prior to** or **in parallel with** the `lnk_order_customer` insert:

```sql
-- Step 1: Stub missing Hub keys from the incoming fact staging table
INSERT INTO hub_customer (hk_customer, bkcc, customer_id, load_dts, rec_src)
SELECT DISTINCT
    stg.hk_customer,
    stg.bkcc_customer,
    stg.customer_id,
    stg.load_dts,
    CONCAT(stg.rec_src, ' (STUB)') AS rec_src
FROM stg_orders_stream stg
WHERE NOT EXISTS (
    SELECT 1 FROM hub_customer h WHERE h.hk_customer = stg.hk_customer
);

-- Step 2: Load Link safely with 100% referential integrity
INSERT INTO lnk_order_customer (hk_lnk_order_customer, hk_order, hk_customer, load_dts, rec_src)
SELECT DISTINCT
    stg.hk_lnk_order_customer,
    stg.hk_order,
    stg.hk_customer,
    stg.load_dts,
    stg.rec_src
FROM stg_orders_stream stg
WHERE NOT EXISTS (
    SELECT 1 FROM lnk_order_customer l WHERE l.hk_lnk_order_customer = stg.hk_lnk_order_customer
);
```
When `CRM.CUSTOMERS` arrives hours later, `hk_customer` already exists in `hub_customer` (so the Hub load is a no-op), and `sat_customer_core` inserts the descriptive attributes seamlessly.

---

## 4. Catalog of Schema Anti-Patterns & Failure Modes

The following 15 concrete anti-patterns represent the primary failure modes encountered in Data Vault 2.0 implementations. The `open-dv` agentic verification engine implements each **Automated Agent Verification Gate** as a hard pre-deployment check.

### 4.1 Comprehensive Anti-Pattern Catalog (`AP-01` to `AP-15`)

| ID | Anti-Pattern Name | Structural / SQL Signature | Runtime & Historical Failure Mode | Preventing Rule | Automated Agent Verification Gate (AST / DDL Check) |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **`AP-01`** | **Direct Raw Vault Mutation (`UPDATE` / `DELETE`)** | `UPDATE sat_* ...`, `DELETE FROM hub_* ...`, or `MERGE` with `WHEN MATCHED THEN UPDATE` on Raw Vault tables. | Destroys regulatory auditability, triggers cloud micro-partition rewrite amplification, and makes failed batch recovery non-idempotent. | `INV-01` | Parse all DML ASTs targeting Raw Vault schema: fail if statement type $\notin \{\texttt{INSERT}, \texttt{SELECT}\}$. |
| **`AP-02`** | **Physical `load_end_dts` Mutation Races** | Storing a physical `load_end_dts` or `is_current` column in `sat_*` and updating the prior row when a new row arrives. | Causes deadlocks/race conditions in concurrent/streaming loads, doubles I/O write cost, and corrupts intervals when out-of-order data arrives. | `INV-02` | Reject `sat_*` / `esat_*` DDL containing `load_end_dts`, `valid_to`, or `is_current`; enforce virtual `LEAD(load_dts)` views or PIT tables. |
| **`AP-03`** | **Satellite-to-Satellite Joins or Foreign Keys** | Joining `sat_a` directly to `sat_b` without anchoring on the parent `hub`/`lnk` or `pit` spine, or defining a FK from `sat_b` to `sat_a`. | Produces Cartesian fan-outs across asynchronous `load_dts` timelines and creates brittle coupling when either Satellite evolves or splits. | `INV-05` | AST join graph check: every `sat_*` join must bind `sat.hk_parent = spine.hk_parent` (and `sat.load_dts = pit.sat_ldts` when PIT-assisted). |
| **`AP-04`** | **Parent Hub Keys or Business Keys in Satellites** | Adding `hk_customer`, `hk_product`, or raw `customer_id` columns inside a Link Satellite (`sat_lnk_order_item`) or Hub Satellite. | Denormalizes identity into descriptive storage; if a Link is refactored or keys are remapped, redundant Hub keys in Link Satellites cause schema lock-in. | `INV-05` | DDL check on `sat_*`: allow *only* one parent hash key (`hk_<parent_hub_or_link>`), metadata columns (`load_dts`, `applied_dts`, `hash_diff`, `rec_src`), optional `cdk_*` (in `mas_*`), and descriptive attributes. |
| **`AP-05`** | **Overloaded / Mutable Links (State or Dates in Links)** | Adding `start_date`, `end_date`, `status`, or `amount` columns directly into a standard `lnk_*` table. | Turns a timeless structural intersection into a mutable/temporal table; breaks when the same two entities re-establish a relationship later (e.g., `ENG -> AI -> ENG`). | `INV-04`, `INV-05` | DDL check on `lnk_*`: forbid any non-key payload or effective date columns (except explicitly designated `t_lnk_*` / `nh_lnk_*` immutable transaction links). |
| **`AP-06`** | **Effectivity Satellite Without Explicit Driving Key** | Creating `esat_<link>` and partitioning `LEAD(load_dts)` by `hk_lnk` instead of the designated **Driving Key** (`driving_hk_<hub>`). | Partitioning by `hk_lnk` never closes out an old relationship when the driving entity switches to a *different* driven entity (because the new relationship has a different `hk_lnk`!). | `INV-05` | Require explicit `driving_keys: [hk_<hub>]` metadata on every `esat_*` model; verify window partition in effectivity view uses `PARTITION BY driving_hk_<hub>`. |
| **`AP-07`** | **Single-Active Effectivity Satellite on M:N / 1:N Relationship** | Attaching a standard single-driver `esat_*` to a Link where one driving key has *multiple simultaneous active* driven keys in the same load batch. | Causes primary key violations or erroneously end-dates sibling active relationships loaded at the same timestamp. | `INV-04`, `INV-06` | Require either a Multi-Active Effectivity Satellite (with set-hash diff over the full active set per driver) or a Dependent Child Key when multiplicity $> 1$. |
| **`AP-08`** | **Unescaped Delimiter or `CONCAT_WS` NULL-Skipping Hash Collisions** | Computing `hk` or `hash_diff` via `SHA256(a || '||' || b)` or `SHA256(CONCAT_WS('||', a, b, c))` on unescaped/nullable columns. | Merges distinct business keys (`('A||B','C') == ('A','B||C')`) or silently drops Satellite updates when `NULL` shifts position (`('X',NULL,'Y') == ('X','Y',NULL)`). | `KEY-02`, `KEY-03` | Enforce use of the canonical length-prefixed (`<len>:<val>`) or delimiter-escaped (`\||`, `\^^`) hashing macro with explicit `COALESCE(..., '^^')`. |
| **`AP-09`** | **Hashing `rec_src` or `load_dts` into Hub/Link Hash Keys** | Including `rec_src` or `load_dts` in the `SHA256(...)` input of `hk_hub` or `hk_lnk`. | Destroys passive integration completely: the exact same business key arriving from two tables or at two times produces different Hub keys. | `KEY-04` | AST check on `hk_*` generation expressions: fail if `rec_src` or `load_dts` appears in the hash input list. |
| **`AP-10`** | **Retroactive `hash_diff` Re-Computation on In-Place `ADD COLUMN`** | Adding a column to an existing `sat_*` and immediately appending `COALESCE(new_col, '^^')` to the staging `hash_diff` formula for all rows. | Changes the `hash_diff` of every existing entity even when `new_col IS NULL`, triggering a 100% false-positive delta reload across the entire Satellite. | `EVO-01`, `EVO-01a` | Block in-place `sat_*` `ADD COLUMN` unless accompanied by a versioned `hash_diff` gate or redirect new columns to a new Satellite (`sat_*_ext` / `sat_*_v2`). |
| **`AP-11`** | **Source-System-Specific Hubs (`hub_sap_cust`, `hub_sfdc_cust`)** | Modelling one Hub per source system table instead of one Hub per core business concept. | Replicates source silos inside the Raw Vault; adding a new source system requires rewiring every downstream Link, Bridge, and Mart. | `EVO-08`, `KEY-04` | Semantic model check: flag Hub names containing source system prefixes (`sap_`, `sfdc_`, `crm_`, `erp_`) and require shared Hub + `bkcc`. |
| **`AP-12`** | **In-Place Hub/Link Grain Mutation** | Adding or removing business key columns in an existing `hub_*` or foreign key columns in an existing `lnk_*`. | Invalidates all historical hash keys (`hk_*`), orphaning existing Satellites or mixing two incompatible grains in one table. | `INV-03`, `INV-04` | Schema registry lock: reject any migration that alters `bk_*` columns on an existing Hub or `hk_*` FK columns on an existing Link. |
| **`AP-13`** | **Orphan Link / Satellite Loads (Bypassing Early-Arriving Stubs)** | Loading `lnk_*` or `sat_*` directly from staging without verifying or stubbing missing keys in `hub_*`. | Creates dangling foreign keys (`hk_hub` present in Link/Satellite but missing in Hub), causing inner joins on Hubs to silently drop facts. | `TMP-03` | DAG dependency & SQL check: every `lnk_*` / `sat_*` load must either depend on a stub-hub step from the same staging source or pass an FK integrity test. |
| **`AP-14`** | **Back-Dating `load_dts` to `applied_dts` for Late-Arriving Data** | Overwriting `load_dts` with the business `event_dts` or `applied_dts` when ingesting late or historical backfill payloads. | Falsifies the system audit record ("what did we know when?") and can collide with existing `(hk, load_dts)` primary keys. | `TMP-01`, `TMP-02` | Enforce `load_dts = CURRENT_BATCH_TIMESTAMP` at ingestion; store business validity strictly in `applied_dts`. |
| **`AP-15`** | **Modeling Low-Cardinality Codes or Dependent Line Numbers as Hubs** | Creating `hub_order_line_number` (`'1'`, `'2'`) or `hub_boolean_flag` / `hub_status_code`. | Creates massive hot-spot "super-hubs" and meaningless Links where millions of unrelated transactions link to Hub key `'1'`. | `INV-05` | Cardinality & semantic check: dependent child numbers must be `cdk_*` / degenerate keys in Links/Multi-Active Satellites; lookup codes must be Reference tables (`ref_*`). |

---

### 4.2 Deep Dive on Critical Failure Modes

#### Failure Mode `AP-06`: Why Partitioning an Effectivity Satellite by `hk_lnk` Fails
Consider an employee `E-100` who starts in department `ENG` at $T_1$ (`hk_lnk_1 = HASH(E-100, ENG)`), transfers to `AI` at $T_2$ (`hk_lnk_2 = HASH(E-100, AI)`), and transfers back to `ENG` at $T_3$ (`hk_lnk_1 = HASH(E-100, ENG)`).
- If an engineer writes the Effectivity Satellite view with `LEAD(load_dts) OVER (PARTITION BY hk_lnk_emp_dept ORDER BY load_dts)`:
  - `hk_lnk_2` (`E-100 -> AI`) only has **one** row (at $T_2$). Partitioning by `hk_lnk_emp_dept` sees `LEAD(load_dts) IS NULL` for `hk_lnk_2`, so **`E-100 -> AI` remains open forever (`9999-12-31`)** even after `E-100` transfers back to `ENG` at $T_3$!
- **Correct Pattern:**
  - The window function MUST partition by the **Driving Key** (`PARTITION BY e.driving_hk_employee ORDER BY e.load_dts ASC`). Because all three transitions (`T1: ENG`, `T2: AI`, `T3: ENG`) share the same driving key `driving_hk_employee = HASH(E-100)`, `T2` cleanly closes `T1` (`to = 2026-03-01`), and `T3` cleanly closes `T2` (`to = 2026-06-01`) while opening the new `ENG` interval (`2026-06-01` to `9999-12-31`).

---

## 5. Empirical Verification Evidence

All stability invariants, delimiter/NULL collision proofs, Driving Key Effectivity Satellite timelines, bi-temporal out-of-order queries, Ghost-Record PIT joins Across `v1`/`v2` Satellites, and Early-Arriving Fact stubbing patterns were empirically executed and verified on SQLite 3.45 / Python 3.12:

```text
=== TEST 1: Insert-Only Immutability & Virtual End-Dating ===
  Virtual window: ('2026-01-01T00:00:00Z', '2026-02-15T12:00:00Z', 0, 'STANDARD', 5000)
  Virtual window: ('2026-02-15T12:00:00Z', '2026-04-01T08:30:00Z', 0, 'GOLD', 15000)
  Virtual window: ('2026-04-01T08:30:00Z', '9999-12-31T23:59:59.999999Z', 1, 'PLATINUM', 50000)
  Blocked mutation as expected: INV-01 VIOLATION: UPDATE prohibited on Raw Vault Satellite

=== TEST 2: Hash Key & HashDiff Canonicalization & Collision Prevention ===
  Naive concat collision: ('ANNE||O', 'BRIEN') vs ('ANNE', 'O||BRIEN') -> equal=True (3B724334553C2566...)
  Naive CONCAT_WS NULL-skipping collision: ('VAL_X', None, 'VAL_Y') vs ('VAL_X', 'VAL_Y', None) -> equal=True
  Canonical length-prefixed ('ANNE||O', 'BRIEN') vs ('ANNE', 'O||BRIEN') -> equal=False
  Canonical length-prefixed ('VAL_X', None, 'VAL_Y') vs ('VAL_X', 'VAL_Y', None) -> equal=False

=== TEST 3: Effectivity Satellite with Driving Key Resolution ===
  Driver timeline: dept=ENG, from=2026-01-01T00:00:00Z, to=2026-03-01T00:00:00Z
  Driver timeline: dept=AI, from=2026-03-01T00:00:00Z, to=2026-06-01T00:00:00Z
  Driver timeline: dept=ENG, from=2026-06-01T00:00:00Z, to=9999-12-31T23:59:59.999999Z

=== TEST 4: Bi-Temporal Out-of-Order Ingestion & PIT Satellite Version Evolution ===
  Bitemporal (known=Jan22, effective=Jan15): ('ACTIVE', 1000, '2026-01-01T00:00:00Z', '2026-01-01T00:05:00Z')
  Bitemporal (known=Jan26, effective=Jan15): ('INTERIM_AMEND', 1800, '2026-01-10T00:00:00Z', '2026-01-25T00:05:00Z')
  Bitemporal (known=Jan26, effective=Jan22): ('AMENDED', 2500, '2026-01-20T00:00:00Z', '2026-01-20T00:05:00Z')

=== TEST 5: Non-Breaking Schema Evolution, Ghost Records & PIT Continuity ===
  PIT EQUI-JOIN snapshot: ('2026-02-01T00:00:00Z', 'AA+', -1, 'UNKNOWN', 'AA+')
  PIT EQUI-JOIN snapshot: ('2026-04-01T00:00:00Z', 'AA+', 820, 'LOW_RISK', 'LOW_RISK')

=== TEST 6: Early-Arriving Fact Stubbing & BKCC + Same-As Link Resolution ===
  Early-Arriving fact + Same-As resolution: ('F9057B0189AD6BE478D91F734BC11EA07C3F4D03B69FE26FA7D7F11F3E8BED3D', 'EU_WEST:C-9999', 'US_EAST:C-1001')

ALL EMPIRICAL VERIFICATION CHECKS PASSED.
```

---

## 6. Pragmatic Cold-Start ($N=1$) Architecture & Continuous Multi-Source ($N \to N+1$) Evolution

A central failure mode in enterprise Data Vault implementations is **Day-1 Over-Engineering**: either freezing progress because the first operational source ($N=1$) lacks idealized enterprise keys, or fragmenting a small table into 6 micro-satellites on Day 1 "just in case." Conversely, under-engineering irreversible Hub/Link invariants on Day 1 forces destructive warehouse rebuilds when Source $N=2$ arrives.

To solve this rigorously, `open-dv` enforces the **Asymmetric Reversibility Principle**.

### 6.1 The Asymmetric Reversibility Matrix

Not all Data Vault modeling choices carry symmetric refactoring costs. The agentic system strictly separates **Irreversible Structural Anchor Decisions** (which must be locked on Day 1 even for $N=1$) from **Reversible Storage/Performance Optimizations** (which are deferred via YAGNI guards until empirical thresholds are met):

| Architectural Decision | Reversibility Class | Cost to Change Later ($N \to N+1$) | Day-1 ($N=1$) Agent Rule |
| :--- | :--- | :--- | :--- |
| **Hub Semantic Naming (`hub_customer` vs `hub_sap_kna1`)** | **IRREVERSIBLE** (High Cost) | Requires dropping/re-pointing all downstream Links, Satellites, PITs, and Marts. | **STRICT ON DAY 1**: Always map to the Enterprise Core Business Concept (CBC) from Graph A, never the source table name. |
| **Hash Key Canonicalization & `BKCC` Namespace Policy** | **IRREVERSIBLE** (High Cost) | Any change to hash formatting invalidates 100% of historical `_hk` joins across the warehouse. | **STRICT ON DAY 1**: Enforce the 6-stage canonicalization pipeline and the 3-Tier `BKCC` Authority Protocol (`GLOBAL` for Tier 1/2, `<SOURCE_SYS>` for Tier 3). |
| **Link Mandatory vs. Optional FK Decomposition** | **IRREVERSIBLE** (High Cost) | Packing nullable optional FKs into an $N$-ary Link mutates `hk_lnk` whenever an optional participant becomes known later. | **STRICT ON DAY 1**: Enforce the **Nullable-FK Link Splitter Rule** ($U_{nn} \ge 0.95$ stays in mandatory kernel Link; nullable FKs with $U_{nn} < 0.95$ or null rate $> 5\%$ split into binary optional Links). |
| **Raw Vault Insert-Only Immutability (`INV-01`)** | **IRREVERSIBLE** (High Cost) | In-place `UPDATE`s permanently destroy historical auditability. | **STRICT ON DAY 1**: Zero `UPDATE`/`DELETE` statements permitted in Raw Vault from Day 1. |
| **Rate-of-Change Satellite Splitting (`sat_core` vs `sat_fast`/`sat_slow`)** | **REVERSIBLE** (Low Cost) | Adding a new Satellite and updating a disposable PIT/View requires zero historical Hub/Link migration. | **YAGNI ON DAY 1 (Gate 4 Cost Guard)**: Keep a single source-aligned Satellite unless empirical write-amplification exceeds $W_{\text{amp}} \ge 50\text{ MB/day}$, $\text{cols} \ge 10$, and $\Delta\log_{10}(\lambda) \ge 2.0$. |
| **Cross-Source Entity Deduplication (`sal_*`)** | **REVERSIBLE / ADDITIVE** | Deploying a Business Vault `sal_*` + Mart resolution view is 100% additive. | **DEFER AT $N=1$**: Do not pre-build empty `sal_*` tables at $N=1$; generate them automatically when $N \ge 2$ introduces overlapping entities. |
| **PIT & Bridge Materialization (`pit_*`, `brg_*`)** | **REVERSIBLE / DISPOSABLE** | PITs and Bridges hold zero unique historical truth and can be dropped/rebuilt at will. | **DEFER AT $N=1$**: Use virtualized views until Satellite join fan-out ($\ge 3$ Satellites) or Mart query SLA requires materialization. |

---

### 6.2 The 3-Tier Business Key Authority Protocol & Nullable-FK Link Splitter

#### 6.2.1 3-Tier Business Key Authority Protocol
When profiling an operational source at $N=1$, an ideal natural key is not always present in every table. Rather than hallucinating a fake natural key or silently using an un-namespaced sequence integer, the agent classifies every Hub Business Key into one of three formal tiers (enforced by Gate 1 / **Algorithm A.4**):

| Authority Tier | Definition & Examples | Empirical Graph B Signature | Hash Key (`hk`) Prefix Rule | Cross-Source Integration Behavior ($N \to N+1$) |
| :--- | :--- | :--- | :--- | :--- |
| **`TIER_1_GLOBAL_STANDARD`** | Universally standardized external identifier (ISO-3166, LEI, ISBN, VIN, IBAN, UUIDv4). | $U(C) = 1.0$, matches standard regex pattern, high entropy $H(C)$. | `BKCC = 'GLOBAL'` | **Automatic Passive Integration**: Any future source emitting the same Tier-1 key hashes to the exact same `hk_*` row in the Hub. |
| **`TIER_2_ENTERPRISE_SOR`** | Enterprise System-of-Record natural key or MDM Golden Key (`customer_number = 'CUST-00491'`, `tax_id`, `employee_badge_no`). | $U(C) = 1.0$, business-formatted alphanumeric string or enterprise master key. | `BKCC = 'GLOBAL'` (or domain namespace) | **Automatic Passive Integration**: Secondary systems referencing the enterprise natural key automatically join on `hk_*` in the shared Hub. |
| **`TIER_3_LOCAL_SCOPED`** | Source-local sequence/auto-increment surrogate key (`user_id = 48192`) used **only when no Tier-1 or Tier-2 key exists** in the source. | Monotonic integer sequence, low semantic portability across systems. | **`BKCC = '<SOURCE_SYS>'`** (Mandatory collision prefix, e.g. `' billing_app_v1'`) | **Collision-Proof Isolation + `sal_*` Resolution**: Prevents `SAP#100` from colliding with `SFDC#100`; cross-system equivalence is bridged in the Business Vault via a **Same-As Link (`sal_*`)**. |

#### 6.2.2 The Nullable-FK Link Splitter Rule
In operational 3NF tables (e.g., an `orders` table with `customer_id NOT NULL`, `product_id NOT NULL`, `promo_code_id NULL`, `sales_rep_id NULL`), packing all four foreign keys into a single 4-way Link `lnk_order_cust_prod_promo_rep` creates a severe stability hazard:
- At order creation ($T_1$), `sales_rep_id IS NULL` (mapped to Zero Key `-1`), producing `hk_lnk_1 = HASH(order_1, cust_1, prod_1, -1, -1)`.
- At $T_2$, a sales rep is assigned (`sales_rep_id = 'REP-9'`), producing a **completely different Link hash key** `hk_lnk_2 = HASH(order_1, cust_1, prod_1, -1, 'REP-9')` and duplicating the core order relationship!

**Mandatory Rule (Gate 1 Check)**:
1. **Mandatory Kernel Link**: Only foreign keys with non-null ratio $U_{nn}(C) = 1 - N_{\text{null}}(C) \ge 0.95$ (mandatory participants at the birth of the unit of work) belong in the primary $N$-ary Link (`lnk_order_customer_product`).
2. **Binary Optional Links**: Any foreign key with $N_{\text{null}}(C) > 0.05$ (`promo_code_id`, `sales_rep_id`) **must be split into a separate binary Link** (`lnk_order_promotion`, `lnk_order_sales_rep`) that loads rows only when the optional FK is non-null (plus an Effectivity Satellite `esat_order_sales_rep` if the assignment can change over time).

---

### 6.3 Gate 4 YAGNI Write-Amplification Cost Guard for Satellite Splitting

Textbook Data Vault advice states: *"Split Satellites by rate of change."* However, when an LLM applies this rule blindly to a 7-column table with 1,000 rows, it creates 4 micro-satellites (`sat_customer_static`, `sat_customer_daily`, `sat_customer_hourly`, `sat_customer_monthly`), quadrupling ETL tasks and requiring a 4-way PIT join for negligible storage savings.

To enforce engineering pragmatism, **Gate 4 (`Algorithm A.1` in [`05-elm-calculus-and-reference-algorithms.md`](05-elm-calculus-and-reference-algorithms.md))** permits splitting a single-source Satellite by rate-of-change **if and only if ALL THREE empirical thresholds hold simultaneously**:

1. **Column Width Threshold**: Total descriptive payload columns in the source table $\ge 10$.
2. **Frequency Divergence Threshold**: The log-ratio between the fastest-changing attribute cluster $\lambda_{\text{fast}}$ and slowest-changing attribute cluster $\lambda_{\text{slow}}$ satisfies:
   $$\Delta \log_{10}(\lambda) = \log_{10}\left(\frac{\max_c \lambda(c)}{\min_{c: \lambda(c)>0} \lambda(c)}\right) \ge 2.0 \quad (\text{i.e., } \ge 100\times \text{ update rate difference})$$
3. **Daily Write-Amplification Byte Threshold**: Keeping the slow/wide columns inside the fast-updating Satellite would waste at least **50 MB/day** of redundant columnar storage:
   $$W_{\text{amp}} = N_{\text{entities}} \cdot \lambda_{\text{fast}} \cdot \sum_{c \in C_{\text{slow}}} \text{ AvgBytes}(c) \ge 50\text{ MB/day}$$

- **PII / GDPR Exception**: Columns classified as `PII` or regulated under GDPR right-to-erasure are **always** split into an isolated `sat_<entity>_pii` regardless of $W_{\text{amp}}$, enabling targeted crypto-shredding without touching non-PII history.
- **Compiler Action**: If a proposed rate-of-change split fails Gate 4 (and is not a PII split), the `axi-dv` compiler executes `auto-fix: merge_satellites` to consolidate them into a single clean `sat_<cbc>_<source>` table.

---

### 6.4 The 6-Scenario Continuous Evolution Lifecycle ($N \to N+1$ & $S_k(t) \to S_k(t+1)$)

The combination of **Cold-Start Pragmatism ($N=1$)** and **Additive Evolution ($N \to N+1$)** guarantees that **zero existing Raw Vault tables are ever dropped, truncated, or destructively altered** across the entire lifecycle of the enterprise data warehouse:

| Scenario | Trigger Event | Raw Vault Action (100% Additive) | Business Vault / Mart Shield | Existing Tables Modified? |
| :--- | :--- | :--- | :--- | :--- |
| **Scenario 1: Shared Global/SOR Key ($N \to N+1$)** | Source $N+1$ (`CRM_SFDC`) arrives with the same Tier-1/2 key (`tax_id`) already in `hub_customer`. | Reuse `hub_customer` (`BKCC='GLOBAL'`); deploy new source-aligned `sat_customer_sfdc`. | Update `pit_customer` to include `sat_customer_sfdc` (coalescing pre-SFDC dates to Ghost Record `0x00..00`). | **0 existing tables modified.** |
| **Scenario 2: Disjoint Local Key ($N \to N+1$)** | Source $N+1$ (`LEGACY_ERP`) arrives with a Tier-3 local key (`cust_seq_id`) that differs from `hub_customer`. | Reuse `hub_customer` with `BKCC='LEGACY_ERP'`; deploy `sat_customer_legacy_erp`. | Deploy Business Vault **Same-As Link (`sal_customer`)** mapping `(hk_customer_master, hk_customer_duplicate)`. | **0 existing tables modified.** |
| **Scenario 3: New Relationship / Grain Discovery ($N \to N+1$)** | Source $N+1$ (`WMS`) introduces a new `hub_warehouse` linked to existing `hub_order` and `hub_product`. | Create `hub_warehouse`, `lnk_shipment_fulfillment`, and `sat_shipment_wms`. | Optional new `brg_order_fulfillment` Bridge table for supply-chain Marts. | **0 existing tables modified.** |
| **Scenario 4: Late-Arriving Rate-of-Change Split** | Source 1 volume grows $100\times$ so $W_{\text{amp}}$ crosses the $50\text{ MB/day}$ Gate 4 threshold. | Deploy new `sat_customer_sfdc_fast` for high-frequency attributes; freeze those columns in `sat_customer_sfdc_v1` or supersede with `sat_customer_sfdc_v2`. | Repoint `pit_customer` and `dim_customer` view `COALESCE(v2.col, v1.col)` transparently. | **0 existing tables modified.** |
| **Scenario 5: Intra-Source Schema Drift ($S_k(t) \to S_k(t+1)$)** | Operational team modifies Source $S_k$ in-place (`ADD COLUMN`, `DROP COLUMN`, `RENAME`, widen/semantic type change). | Handled automatically by `bin/fm-schema-watch.sh` + **Algorithm A.9a** (`codemod_intra_source_schema_drift`): additive columns route to `sat_<cbc>_<src>_ext_v2`; dropped columns inject `CAST(NULL AS <type>) AS <col>` in `stg_*`; breaking semantic type changes route to `sat_<cbc>_<src>_v2`. | Mart views preserve column signatures via `COALESCE(v2.col, v1.col)`. | **0 existing tables modified.** |
| **Scenario 6: Unstructured Document / Multimodal Source ($N \to N+1$)** | PDF contracts, call transcripts, or clinical notes arrive referencing `hub_customer`. | Deploy `hub_document`, `sat_document_blob_meta`, `lnk_document_<cbc>`, and `mas_document_<cbc>_extraction` (via **Algorithm A.9b**, see [`04-unstructured-and-multimodal-dv2.md`](04-unstructured-and-multimodal-dv2.md)). | Join structured + unstructured context via Point-in-Time / GraphRAG view (**Query D.10**). | **0 existing tables modified.** |

