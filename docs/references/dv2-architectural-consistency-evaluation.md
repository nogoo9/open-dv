# Open-DV Consistency Review: Data Vault 2.0 Architectural Evaluation

**Author:** DV 2.0 Consistency Specialist, Open-DV Consistency Review Team  
**Review Target:** Open-DV Multi-Agent System (`opendv-agent-harness`, `opendv-axi-verifier`, `opendv-duckdb-sandbox`, `opendv-core`, and Documentation Specifications)  
**Standard of Reference:** Dan Linstedt's Data Vault 2.0 Standard, Enterprise Data Warehousing (EDW) Principles, and Open-DV Core Principles (Zero-Hallucination Semantic Grounding, Deterministic Dynamic Verification, Idempotent 4-Zone SQL, and Human Steward Governance).

---

## Executive Summary

Open-DV implements an advanced neuro-symbolic framework for autonomous Data Vault 2.0 design and verification. It isolates LLM agents (`VaultFirstMate`, `ScoutSource`, `ScoutUnstructured`, `ShipVault`) behind a formal intermediate representation (`CanonicalIR` / `NoMistakeDataVault2ContractV5`), ensuring that generative models never emit physical DDL or DML directly. Instead, a deterministic symbolic compiler transpiles verified contracts into idempotent 4-zone SQL, while static verification gates (`axi-dv`) and an in-memory database sandbox (`duckdb`) validate mathematical graph topology, cryptographic hashing, grain uniqueness, and bi-temporal point-in-time reconstruction.

Across the 10 evaluated enterprise scenarios, Open-DV adheres closely to Data Vault 2.0 principles—often surpassing conventional tooling by automating 3-axis satellite splitting, 3-tier business key authority enforcement, and character-exact unstructured document grounding. However, this deep consistency audit identified several subtle architectural divergences, boundary gaps, and concrete implementation discrepancies between Python contracts and generated DuckDB SQL (notably string-length vs. octet-length hashing, hash casing alignment, binary link zero-key propagation, and unapproved unstructured link loading).

Below is the exhaustive scenario-by-scenario evaluation, critique, and actionable remediation blueprint.

---

## Subsystem Architecture Overview & Gate Alignment

| Subsystem | Scope & Role | DV 2.0 & Enterprise DW Alignment | Key Code Files |
| :--- | :--- | :--- | :--- |
| **`opendv-agent-harness`** | Hexagonal agent runtime, 8-stage pipeline orchestrator (`STAGE_00`–`STAGE_07`), persona definitions, and `StewardHoldManager` (`ASK-USER`). | Enforces human-in-the-loop governance for low-confidence or high-risk architectural decisions. Strict agent tool isolation. | `pipeline.py`, `models.py`, `hold_manager.py`, `personas/` |
| **`opendv-axi-verifier`** | 6-Gate verification engine and deterministic codemods (`A.1`–`A.5`, `A.7`, `A.9a/b`). | Enforces structural bipartite DAG invariants, column contracts, grain statements, anti-silo uniqueness, and additive evolution locks. | `alg_a8_orchestrator.py`, `gate1_contract_grain.py`, `gate3_networkx_dag.py`, `alg_a7_evolution_lock.py` |
| **`opendv-duckdb-sandbox`** | Deterministic 4-zone SQL compiler, 15 runtime checks (`DV-DYN-001`–`015`), 4 adversarial batches, and bi-temporal PIT query engine. | Guarantees runtime idempotency, zero hash collisions, referential integrity without foreign key constraints, and dual-timeline time travel. | `compiler_4zone.py`, `dynamic_checks.py`, `adversarial_batches.py`, `pit_query_engine.py` |
| **`opendv-core`** | Domain models (`CanonicalIR`, `contract_v5`), Algorithm A.5 length-prefixed SHA-256 hashing, constants, and schema registry. | Mathematical foundation of injective hashing, Zero Keys (`'0'*64`, `'f'*64`), and Ghost Records (`SYSTEM_GHOST`). | `constants.py`, `hashing.py`, `models/` |

---

## Deep Evaluation of the 10 Scenarios

### Scenario S1: Pure Cold Start ($N=1 \to N+1$)

#### 1. Mathematically & Architecturally Expected Behavior (DV 2.0 Standard)
Under pure Data Vault 2.0 and enterprise warehousing principles:
- **Concept Grounding Over Table Clones:** Even when bootstrapping with a single source feed ($N=1$), Hubs must be modeled strictly around enterprise business concepts, never reflecting physical 3NF tables or application surrogate keys (e.g., auto-increment integers).
- **Proactive Namespace Isolation (BKCC):** In an $N=1$ cold start, business keys that are local to the initial source must be qualified with a Business Key Collision Code (BKCC) or source prefix before hashing. This ensures that when source $N+1$ arrives with identical numeric or string sequences (e.g., Customer ID `1001`), their Hub hash keys ($hk$) do not catastrophically collide. Global standard codes (LEI, ISIN, IBAN) are exempt from source prefixes.
- **M:N Extensibility:** All relationships discovered in the $N=1$ source must be instantiated as Links with an $M:N$ physical grain, even if the source 3NF schema enforces a 1:1 or 1:$N$ relationship, preventing destructive DDL redesign when source $N+1$ introduces fan-outs.
- **Satellite Orthogonal Decomposition:** Attributes must be split into Satellites along natural axes (e.g., rate of change, data privacy classification) from Day 1 rather than creating a monolithic satellite.

#### 2. Open-DV Architectural Critique
- **Where Open-DV Excels:**
  - Open-DV enforces **Dual-Graph Grounding** (`Graph A` ontology + `Graph B` statistical profiling). An agent cannot hallucinate a Hub without binding it to a canonical concept URI in `Graph A`.
  - **Algorithm A.4 (`alg_a4_cold_start.py`)** explicitly enforces a 3-Tier BKCC Authority Protocol:
    - Tier-1: Global Standards (`LEI`, `ISIN`) $\to$ `require_src_qualifier=False`, `bkcc_prefix=None`.
    - Tier-2: Enterprise System of Record (SoR) $\to$ `require_src_qualifier=False`.
    - Tier-3: Local Scoped Keys $\to$ `require_src_qualifier=True`, `bkcc_prefix=<rec_src>`.
  - Gate 2 (`gate1_contract_grain.py` line 237) automatically catches monotonic integers or UUIDv4 sequences used as Hub keys without a steward justification (`SURROGATE_KEY_REJECTED`).
- **Ambiguities and Gaps:**
  - In `alg_a4_cold_start.py`, if a key is classified as Tier-1, `bkcc_prefix` is set to `None`. However, in `opendv-core/constants.py`, `GLOBAL_BKCC = "GLOBAL"`. In the Private Equity reference model (`compiler_4zone.py`), Tier-1 entities (like `hub_limited_partner` with `LP-101`) use `'6:GLOBAL||6:LP-101'`. If a generic IR leaves `bkcc_prefix=None` for a Tier-1 key, `compute_dv2_prehash_string` omits the prefix entirely (`'6:LP-101'`), producing an incompatible hash key compared to models expecting `'6:GLOBAL||...'`.
  - Cold-start Hubs generated without at least one connected Link are allowed through Gate 3 unless `check_isolated_hubs=True` is explicitly passed. In pure DV 2.0, an isolated Hub without a Link or Satellite is considered an incomplete unit of work.

#### 3. Concrete Recommendations & Invariants
- **Add Invariant `DV-COL-013` (Deterministic Tier-1 BKCC Default):** If a business key is Tier-1 Global Standard, standardize whether it is hashed with literal `'GLOBAL'` or with no prefix across both Python `hashing.py` and DuckDB `compiler_4zone.py`. Eliminate the discrepancy where some models inject `GLOBAL` and others omit it.
- **Enforce Gate 3 Isolated Hub Check by Default:** Modify `alg_a8_orchestrator.py` to default `check_isolated_hubs=True` in production runs. Require every cold-start Hub to participate in at least one Link or have a documented justification in `contract.unmapped_escalations`.

---

### Scenario S2: Multi-Source Entity Convergence

#### 1. Mathematically & Architecturally Expected Behavior (DV 2.0 Standard)
- **Passive Integration in Raw Vault:** When two operational sources ($S_1$ and $S_2$) reference the same real-world business entity using identical enterprise business keys (e.g., both use ISO LEI `5493006MHB84DD0ZWV18`), both feeds load directly into the **same Raw Hub** (`hub_portfolio_company`) producing the identical hash key ($hk$).
- **Collision Code Isolation:** When $S_1$ and $S_2$ use internal IDs that may collide in value but represent different entities (or when identity equality is unproven), distinct BKCCs (e.g., `INVESTRAN` vs. `LEGACY_GP`) must be hashed into the key, yielding distinct Hub rows.
- **Active Integration in Business Vault (Same-As Links):** Identity convergence across disparate business keys is strictly prohibited in the Raw Data Vault (which must remain an immutable audit log). It must be executed in the **Business Data Vault** via **Same-As Links (`sal_<entity>`)**, recording `(hk_master, hk_duplicate, load_dts, rec_src)`.
- **Effectivity & Supercedence:** A Business Vault Effectivity Satellite (`esat_sal_*`) must track the valid lifetime of the match, allowing merges to be rolled back or adjusted without rewriting raw history.

#### 2. Open-DV Architectural Critique
- **Where Open-DV Excels:**
  - Open-DV's anti-silo validation in Gate 1 (`gate1_contract_grain.py` lines 157–167) detects duplicate Hub definitions binding to the same ontology concept URI (`ANTI_SILO_VIOLATION`), preventing silo sprawl and forcing integration into a single canonical Hub.
  - The implementation in `compiler_4zone.py` and `pit_query_engine.py` demonstrates a reference-grade Business Vault pattern: `sal_limited_partner` reconciles `hk_lp_master` (Investran `GLOBAL||LP-104`) with `hk_lp_duplicate` (Legacy GP `LEGACY_GP_ADMIN||LP-101`), while `esat_sal_limited_partner` tracks the effectivity interval.
- **Ambiguities and Gaps:**
  - *Staging BKCC Generation in Multi-Source Hub Feeds:* In `compiler_4zone.py`, `_compile_generic_zone_1_staging_views_sql` generates staging views per bronze table. When a bronze table maps to a Hub, the BKCC literal is extracted from the contract. However, if a single bronze table feeds multiple Hubs with differing BKCC authorities (e.g., one Tier-1 column and one Tier-3 column), the staging compiler's current parameterization can assign a uniform BKCC across all hash calculations in that staging view.
  - *Same-As Link Topological Directionality:* In `gate3_networkx_dag.py`, `DV-STR-004` verifies role names on self-referencing Links. However, it does not mathematically enforce asymmetry or canonical sorting between `hk_master` and `hk_duplicate`. In a distributed or multi-agent setting, Agent A might map `(A, B)` while Agent B maps `(B, A)`, creating cyclical or duplicate SAL records.

#### 3. Concrete Recommendations & Invariants
- **Add Invariant `DV-STR-015` (Canonical SAL Edge Ordering):** Enforce that in `sal_<entity>`, the master and duplicate references are strictly ordered (or derived from an authoritative hierarchy) such that `hk_master != hk_duplicate`, and reflexive loops `(A, A)` are rejected at compile time.
- **Staging View Column-Scoped BKCC Mapping:** Refactor `compiler_4zone.py` to bind BKCC literals strictly at the `(bronze_table, column_name, target_hub)` tuple level rather than the table level.

---

### Scenario S3: Completely Unmapped Feed / Absent Ontology Concept

#### 1. Mathematically & Architecturally Expected Behavior (DV 2.0 Standard)
- **100% Auditability vs. Controlled Modeling:** Enterprise Data Vault 2.0 requires capturing all relevant enterprise data without loss. However, it explicitly prohibits generating ungrounded tables that have no identified business owner or natural business key.
- **Landing / Quarantine Holding Pattern:** If an upstream feed arrives with no corresponding concept in the enterprise data ontology, the data must be retained in an immutable Landing/Bronze zone or a raw event store. It should **not** be forced into arbitrary Hubs or Links.
- **Human Steward Governance:** In automated modeling systems, unmapped structures must trigger a formal escalation workflow to data stewards or enterprise architects to determine whether to extend the conceptual model or classify the data as transient/disposable.

#### 2. Open-DV Architectural Critique
- **Where Open-DV Excels:**
  - Open-DV enforces an **Ontology Firewall** at Gate 1 (`gate1_contract_grain.py` lines 168–173). Any entity referencing an unknown `ontology_concept_uri` is rejected (`DV-G4-001`).
  - The `hold_manager.py` subsystem provides a durable `StewardHoldManager` that records `ASK-USER` holds (`StewardHoldRecord`) in `state/holds/<id>.json`. The orchestrator (`alg_a8_orchestrator.py` lines 202–210) extracts `contract.unmapped_escalations` and routes questions directly to human architects.
  - Gate 4 (`DV-G4-002`) requires **100% Column Triage Ledger Coverage**. No bronze column can be silently ignored; it must either be mapped or explicitly marked `OMITTED_INTENTIONALLY` with a documented rationale ($\ge 15$ characters).
- **Ambiguities and Gaps:**
  - When a feed contains completely unmapped columns marked `OMITTED_INTENTIONALLY`, Open-DV discards them from Zone 1 and Zone 2 SQL generation. While this protects the Raw Vault from schema pollution, if the raw Bronze layer is transient (e.g., ephemeral DuckDB memory or temporary staging tables), raw fidelity is lost. Standard DV 2.0 recommends attaching a generic Record Tracking Satellite (`rts_*`) or retaining raw payloads in a raw landing table with load timestamps.

#### 3. Concrete Recommendations & Invariants
- **Add Invariant `DV-LND-001` (Raw Staging Audit Guarantee):** Ensure that unmapped bronze columns are preserved in a durable landing table or raw JSON payload column (`raw_payload_json`) with metadata (`load_dts`, `rec_src`, `source_row_id`) before the pipeline marks them as omitted from Raw Vault.
- **Formal Hold Invariant in Gate 1:** If `unmapped_escalations` is present in the candidate contract, Gate 1 must return verdict `ASK_USER` and halt compilation until a signed steward decision record (`status='APPROVED'`) is present in the `StewardHoldManager`.

---

### Scenario S4: Upstream Schema Drift

#### 1. Mathematically & Architecturally Expected Behavior (DV 2.0 Standard)
- **Immutability of Historical Satellites:** Data Vault 2.0 Satellites are append-only. Under no circumstances may an existing Satellite table undergo destructive modification (`DROP COLUMN`, destructive type narrowing, or in-place re-hashing).
- **Additive Evolution (Companion Satellites):** When upstream systems add attributes:
  - *Standard Approach:* The new attributes are isolated in an **Additive Companion Satellite** (`sat_<entity>_ext` or `sat_<entity>_<subdomain>`). This preserves the historical `hash_diff` calculation on the original Satellite.
  - *Alternative Approach:* If columns are added in-place to an existing Satellite, they must be added as `NULLABLE`, and historical rows must retain their original `hash_diff`.
- **Soft Deprecation on Dropped Columns:** When an upstream column is removed, the EDW Satellite column is retained; staging projects `CAST(NULL AS <type>) AS <col>`, preserving the query interface for historic periods.
- **Lossless Type Widening:** Widening (e.g., `VARCHAR(50) \to VARCHAR(255)` or `INT \to BIGINT`) must not alter the string formatting of existing values, ensuring `hash_diff` continuity.

#### 2. Open-DV Architectural Critique
- **Where Open-DV Excels:**
  - Open-DV's **Algorithm A.9a (`alg_a9a_schema_drift.py`)** and **Algorithm A.7 (`alg_a7_evolution_lock.py`)** represent one of the most sophisticated schema evolution engines in modern data engineering:
    - `ADDITIVE`: Isolates new columns into additive companion satellites (`sat_<base>_ext_<epoch>`), preventing hash invalidation (`DV-EVO-006`).
    - `SOFT_DEPRECATED`: Projects `CAST(NULL AS <type>)` in staging, marking columns in `soft_deprecated_columns` (`DV-EVO-004`).
    - `RENAMED`: Employs Levenshtein distance ($\ge 0.85$ similarity) to alias renamed columns in staging with zero Vault DDL mutation.
    - `TYPE_WIDENED`: Verifies type compatibility via `is_lossless_type_widening`.
    - `KEY_DRIFT`: Immediately flags business key drift as breaking, triggering `ASK-USER` (`DV-EVO-003`).
- **Ambiguities and Gaps:**
  - *Physical DDL Execution Gap for In-Place Evolution:* In `compiler_4zone.py`, Zone 0 DDL statements are generated as `CREATE TABLE IF NOT EXISTS`. If an architect declares an in-place column addition with an explicit `hash_diff_migration_policy`, `compiler_4zone.py` does not emit `ALTER TABLE <sat> ADD COLUMN <col> <type>`. In an existing physical database, re-running Zone 0 will not modify the table schema, causing subsequent Zone 2 `INSERT` statements to fail with "column not found".

#### 3. Concrete Recommendations & Invariants
- **Add Migration DDL Generator to `compiler_4zone.py`:** When compiling a candidate contract against a deployed contract (`contract_v_prev`), the compiler must emit a dedicated `zone_0_migration_sql` script containing idempotent `ALTER TABLE <sat> ADD COLUMN IF NOT EXISTS <col> <type>` statements for non-breaking additions.
- **Add Invariant `DV-EVO-009` (HashDiff Compatibility Check):** Verify that if a companion satellite is created for drifted attributes, its `hash_diff` computation includes only the new attributes, and its parent foreign key references the exact same Hub hash key.

---

### Scenario S5: Nullable Foreign Key / 0..1 Relationship

#### 1. Mathematically & Architecturally Expected Behavior (DV 2.0 Standard)
- **Links Represent Realized Relationships:** In Dan Linstedt's Data Vault 2.0, a Link represents an association that actually occurred in the business domain.
- **The Null-FK / Zero-Key Pitfall:** If an operational transaction has an optional foreign key (e.g., 70% of fund commitments have no co-investment portfolio company), loading an optional FK into a 3-way Link by substituting a Zero Key (`0x00..00`) creates a catastrophic synthetic clustering effect: all transactions with no co-investment collapse into the same artificial relationship instance.
- **Decomposition Pattern:** The mathematically correct Data Vault 2.0 architecture requires **decomposing the relationship**:
  - The mandatory association is modeled in a base Link (`lnk_lp_commitment` connecting LP and Fund).
  - The optional association is modeled in a separate, filtered Link (`lnk_lp_commitment_co_invest` connecting LP, Fund, and PortCo, or LP and PortCo) loaded with `WHERE co_invest_fk IS NOT NULL`.

#### 2. Open-DV Architectural Critique
- **Where Open-DV Excels:**
  - Open-DV features the **Algorithm A.4 Nullable-FK Link Arity Splitter (`U_nn >= 0.95`)** (`alg_a4_cold_start.py` lines 288–374).
  - When an $N$-ary Link ($\text{arity} \ge 3$) contains an optional foreign key whose non-null ratio $U_{nn} < 0.95$, the codemod automatically splits the Link:
    1. Keeps mandatory references in the base Link.
    2. Generates a dedicated companion Link (e.g., `lnk_lp_commitment_co_invest`) with `where_filter_condition = '<fk> IS NOT NULL'`.
  - Gate 2 (`gate1_contract_grain.py` lines 265–284, `DV-G2-001`) rejects any contract containing unsplit sparse foreign keys on $N$-ary Links.
- **Ambiguities and Gaps:**
  - *Binary Link Boundary Gap:* In `alg_a4_cold_start.py` line 294:

```python
for lnk in candidate_links:
    refs = lnk.get("hub_references", [])
    if len(refs) < 3 or lnk.get("where_filter_condition"):
        new_links.append(lnk)
        continue
```

    Algorithm A.4 **only inspects Links with arity $\ge 3$**. If a source table has a binary relationship where the foreign key is optional (e.g., an Order with an optional Promotion ID, where $U_{nn} = 0.20$), Algorithm A.4 does **not** split or filter the Link.
    In staging, if `promotion_id` is null, it receives the null token `'-1:^^'`, which hashes to `ZERO_KEY_UNKNOWN` (`'0'*64`). The binary Link `lnk_order_promotion` will insert rows linking `hk_order` to `hk_promotion = '0'*64`. While this maintains referential equi-joins, it violates the DV 2.0 principle that Links should not instantiate non-events.

#### 3. Concrete Recommendations & Invariants
- **Extend Algorithm A.4 and Gate 2 to Binary Links (`DV-G2-001b`):**
  If a binary Link ($\text{arity} = 2$) references a foreign key with $U_{nn} < 0.95$, enforce one of two explicit architectural choices:
  1. Inject an automatic `where_filter_condition = '<fk> IS NOT NULL'` onto the Link so that no Link record is created when the relationship is absent.
  2. Model the optional key as a descriptive attribute in the parent Hub's Satellite (Foreign Key in Satellite pattern), omitting the Link entirely.

---

### Scenario S6: Retroactive / Out-of-Order Event Arrival

#### 1. Mathematically & Architecturally Expected Behavior (DV 2.0 Standard)
- **Bi-Temporal Orthogonality:**
  - **System Timeline (`load_dts`):** Immutable physical insertion timestamp. Monotonically increasing. Identifies the batch when the EDW became aware of the record.
  - **Business Timeline (`applied_date` / `effective_from_dt`):** The real-world occurrence date. Events can arrive hours, weeks, or years out of order.
- **Non-Destructive Restatement:** When a retroactive restatement arrives (e.g., a Q3 portfolio company valuation restated upward on November 15), the old record is **never** updated or overwritten. A new row is appended with the current `load_dts` and the retroactive `effective_from_dt`.
- **Point-in-Time (PIT) Query Engine:** The PIT engine must support bi-temporal as-of querying across two independent axes:
  - "What did we believe the state was on October 15?" ($\text{load\_dts} \le \text{2026-10-15}$)
  - "What is the restated true state for October 15 known as of today?" ($\text{load\_dts} \le \text{now()}$, $\text{effective\_from\_dt} \le \text{2026-10-15}$).

#### 2. Open-DV Architectural Critique
- **Where Open-DV Excels:**
  - Open-DV's bi-temporal architecture is demonstrated in `pit_query_engine.py` and validated by **Adversarial Batch 4** (`adversarial_batches.py`).
  - In `Query D.10b`, Open-DV proves that:
    - At `assertion_cutoff_ts = '2026-10-15 12:00:00'`, the valuation query yields $\$216.28\text{M}$ EV ($12.80\times$ multiple).
    - At `assertion_cutoff_ts = '2026-11-15 23:59:59'`, the restatement is absorbed without physical updates, returning $\$245.00\text{M}$ EV ($14.50\times$ multiple).
  - PIT tables employ logarithmic snapshot calendar spines (`information_mart.snap_logarithmic_calendar`), cutting snapshot storage by over $90\%$ while retaining audit precision.
- **Ambiguities and Gaps:**
  - *Satellite Delta Detection vs. Retroactive Arrival:* In `compiler_4zone.py`, Zone 2 Satellite loading utilizes an anti-join to prevent inserting redundant records:
    ```sql
    LEFT JOIN (
        SELECT hk, hash_diff, load_dts,
               ROW_NUMBER() OVER (PARTITION BY hk ORDER BY load_dts DESC) as rn
        FROM raw_vault.sat_*
    ) cur ON cur.hk = src.hk AND cur.rn = 1
    WHERE cur.hk IS NULL OR cur.hash_diff <> src.hash_diff
    ```
    Notice that the window function orders strictly by `load_dts DESC`. If an out-of-order record arrives for an effective date in the past, its `hash_diff` is compared against the *most recently loaded* record rather than the record adjacent in *effective business time*! If the current state differs from the retroactive record, the record will insert properly. However, if a duplicate of an older state arrives late, comparing it against the current head state will trigger a false insert.

#### 3. Concrete Recommendations & Invariants
- **Add Invariant `DV-DML-003` (Bi-Temporal Delta Detection):** For Satellites tracking business time (`applied_date` / `effective_from_dt`), compile delta detection using a bi-temporal window:
  ```sql
  QUALIFY ROW_NUMBER() OVER (PARTITION BY hk, effective_from_dt ORDER BY load_dts DESC) = 1
  ```
  This ensures idempotency on the business timeline as well as the system timeline.

---

### Scenario S7: Malformed / Adversarial Raw Data

#### 1. Mathematically & Architecturally Expected Behavior (DV 2.0 Standard)
- **Injective Hashing:** The pre-hash serialization function $f(v_1, v_2, \dots, v_k)$ must be mathematically injective: $f(A) = f(B) \iff A = B$. Delimiters within data values (e.g., `'A||B'` and `'C'`) must not collide with token separations (`'A'` and `'B||C'`).
- **SQL Injection Defense:** All table names, column identifiers, and dynamic SQL expressions must be strictly sanitized against injection vulnerabilities.
- **Intra-Batch Deduplication:** Staging and loading queries must handle duplicate identical rows arriving in the same micro-batch without violating primary key constraints.

#### 2. Open-DV Architectural Critique
- **Where Open-DV Excels:**
  - **Algorithm A.5 Length-Prefixed Hashing:** Open-DV prefixes each token with its byte length (`<len>:<val>`) and separates with `||`. This mathematically eliminates delimiter collision attacks (`tests/test_hashing_and_keys.py`).
  - **AST Linter & Identifier Validation:** `compiler_4zone.py` and `alg_a6_sqlglot_ast.py` enforce strict regex validation (`^[A-Za-z_][A-Za-z0-9_]*$`) on all SQL identifiers and ban non-deterministic functions (`NOW()`, `RANDOM()`) and wildcard queries (`SELECT *`).
  - **Adversarial Sandbox Testing:** `adversarial_batches.py` runs progressive adversarial batches (Batches 1–4) specifically injecting boundary shifts and duplicate batches to verify zero drift.
- **Ambiguities and Gaps:**
  - **RESOLVED (`BUG-01` / [#30](https://github.com/nogoo9/open-dv/issues/30)): DuckDB `STRLEN()` vs. UTF-8 `OCTET_LENGTH(ENCODE(...))`:**
    In `compiler_4zone.py` `build_length_prefixed_token_sql`:

```python
def build_length_prefixed_token_sql(col_expr: str, *, is_business_key: bool = True) -> str:
    null_token = "-1:^^" if is_business_key else "^^"
    nullif_expr = f"NULLIF(TRIM(CAST({col_expr} AS VARCHAR)), '')"
    return (
        f"CASE WHEN {nullif_expr} IS NULL THEN '{null_token}' "
        f"ELSE CAST(OCTET_LENGTH(ENCODE({nullif_expr})) AS VARCHAR) || ':' || {nullif_expr} END"
    )
```

    DuckDB's `STRLEN()` function computes the **character count** (Unicode codepoints), whereas Python's `len(s.encode('utf-8'))` in `hashing.py` and DuckDB's `OCTET_LENGTH(ENCODE(...))` compute the **UTF-8 byte length** (`'Nordisk Suverän Fond'` $\to$ `'21:Nordisk Suverän Fond'`).
  - **Case Inconsistency:** DuckDB `compiler_4zone.py` computes `LOWER(SHA256(...))` (lowercase hex `[0-9a-f]{64}`), while Python `hashing.py` produces uppercase hex (`.hexdigest().upper()`).

#### 3. Concrete Recommendations & Invariants
- **Resolved in `compiler_4zone.py` (`STRLEN` $\to$ `OCTET_LENGTH(ENCODE(...))`, [#30](https://github.com/nogoo9/open-dv/issues/30)):** Replaced all instances of `STRLEN(...)` with `OCTET_LENGTH(ENCODE(...))` across `compiler_4zone.py`, `pit_query_engine.py`, `adversarial_batches.py`, and `dynamic_checks.py`, and restricted `DV-AST-002` in `alg_a6_sqlglot_ast.py` to `OCTET_LENGTH`/`BYTE_LENGTH`.
- **Normalize Hash Hex Case Across All Subsystems:** Standardize whether SHA-256 digests are stored in lowercase or uppercase across Python `hashing.py`, `constants.py`, and `compiler_4zone.py`. Lowercase is recommended for SQL engine alignment.
- **Enforced Invariant `DV-HASH-004`:** Dynamically verifies that multi-byte test vectors (e.g., `'Nordisk Suverän Fond'`) produce the exact same pre-hash string (`'21:Nordisk Suverän Fond'`) and hash digest in DuckDB SQL as in Python `hashing.py`.

---

### Scenario S8: Unstructured Document Ingestion

#### 1. Mathematically & Architecturally Expected Behavior (DV 2.0 Standard)
- **Document Identity in Hubs:** Unstructured files (PDFs, images) are identified by an immutable content digest (e.g., SHA-256 of the raw file content) stored as the business key in a dedicated Hub (`hub_document`).
- **Separation of Blobs and Context (Rule U1):** Raw binary blobs and high-dimensional embedding vectors must not be placed in relational Satellites. Blobs reside in object storage / CAS; vector embeddings reside in vector indexes.
- **Unstructured Extraction Links & AI Provenance Satellites (Rules U2–U3):** Extracted facts must link `hub_document` to participating business Hubs via an Unstructured Extraction Link. An attached AI Provenance Satellite must record the extractor model ID, execution timestamp, confidence score, and exact character-span citation.
- **Entity Resolution Gating:** Probabilistic entity resolution (matching an extracted string like "CalPERS" to an established Hub key) must have a threshold-based safety mechanism. High-confidence matches auto-bind; ambiguous matches must be quarantined or escalated to a human steward.

#### 2. Open-DV Architectural Critique
- **Where Open-DV Excels:**
  - Open-DV's multimodal specification (`04-unstructured-and-multimodal-dv2.md` and `alg_a9b_unstructured.py`) is exemplary:
    - `BLOB_OR_VEC_IN_RAW_SAT` (Rule U1): Prohibits binary and vector types in raw satellites.
    - `SPAN_HALLUCINATION` (Rule U2): Verifies that every extracted text quote matches `doc_text[start:end]` exactly.
    - `MISSING_PROVENANCE_SAT` (Rule U3): Demands full AI extraction provenance.
    - 3-Tier ER Confidence Margin Routing (Rule U4): Categorizes extractions into `Tier-U1` (deterministic), `Tier-U2` ($\text{conf} \ge 0.85$, $\text{margin} \ge 0.15$), and `Tier-U3` (ambiguous $\to$ `ASK-USER` hold).
- **Ambiguities and Gaps:**
  - *Zone 2 Load DML Gating Gap:* In `alg_a9b_unstructured.py`, Tier-U3 documents route to `ASK-USER`. However, in `compiler_4zone.py`, the generated Zone 2 DML for unstructured extraction links (e.g., `lnk_doc_lp_side_letter`) loads directly from `stg_*` without filtering for approved status (`WHERE steward_gate_status = 'APPROVED'`). If compiled and executed directly without the pipeline harness intercepting the hold, unapproved links could be loaded into Raw Vault tables.

#### 3. Concrete Recommendations & Invariants
- **Add Invariant `DV-AST-007` (Steward-Approved Gating on Unstructured Links):** Ensure that generated Zone 2 SQL for any `UNSTRUCTURED_EXTRACTION_LINK` enforces an inner join or where clause on `steward_gate_status = 'APPROVED'`, guaranteeing that records held in `Tier-U3` are never physically loaded into the Raw Vault until resolved.

---

### Scenario S9: Multi-Tenant / Multi-Unit Collisions

#### 1. Mathematically & Architecturally Expected Behavior (DV 2.0 Standard)
- **Tenant Key Disambiguation:** In multi-tenant environments (or multi-operating unit conglomerates), business keys from different tenants (e.g., Tenant A Account `#101` and Tenant B Account `#101`) represent distinct entities and must never share a Hub hash key.
- **Hash Formulation:** The tenant identifier must either be incorporated into the composite natural business key or injected as the BKCC namespace in Stage 4 of Algorithm A.5:
  $$hk = \text{SHA256}(\text{len}(\text{tenant\_id}) : \text{tenant\_id} \mathbin{\Vert} \text{len}(\text{account\_id}) : \text{account\_id})$$
- **Information Mart Isolation:** While Raw Data Vault stores multi-tenant entities in shared Hubs/Satellites differentiated by hash keys and tenant attributes, Information Delivery marts must apply row-level security (RLS) or tenant-filtered views.

#### 2. Open-DV Architectural Critique
- **Where Open-DV Excels:**
  - Open-DV's BKCC architecture (`hashing.py`, `alg_a4_cold_start.py`) treats tenant/system prefixes as mandatory for local-scoped keys. In the Private Equity scenario, `LEGACY_GP_ADMIN` vs. `GLOBAL` proves that identical LP identifiers (`LP-101`) hash to completely different Hub keys (`6B7C71...` vs. `5D15EB...`), guaranteeing zero cross-tenant collision.
- **Ambiguities and Gaps:**
  - *Dynamic Tenant Column Limitation:* In `compiler_4zone.py`, `build_algorithm_a5_sha256_sql` accepts a static `bkcc_literal: str | None`. If a single multi-tenant source table contains rows from multiple tenants with a dynamic `tenant_id` column (rather than a static tenant code per table), `compiler_4zone.py` cannot dynamically prefix the hash calculation with that column unless the user manually models `tenant_id` as part of a composite business key.

#### 3. Concrete Recommendations & Invariants
- **Add Dynamic Tenant Column Support to `compiler_4zone.py`:** Update `build_algorithm_a5_sha256_sql` to accept either a static `bkcc_literal` or a dynamic `bkcc_column_expr` (e.g., `TRIM(CAST(tenant_id AS VARCHAR))`), automatically prepending the tenant identifier as the leading token in the length-prefixed hashing pipeline.

---

### Scenario S10: Ghost Record & Unknown Value Handling

#### 1. Mathematically & Architecturally Expected Behavior (DV 2.0 Standard)
- **Zero Keys in Hubs & Links:**
  - **Unknown / Missing Mandatory Foreign Key (`-1` token):** Represented by 32 zero bytes / 64 `'0'` hex characters (`0x0000...0000`).
  - **Inapplicable / Optional / Error Key (`-2` token):** Represented by 32 `0xFF` bytes / 64 `'f'` hex characters (`0xFFFF...FFFF`).
  - Stored with `rec_src = 'SYSTEM_ZERO_KEY'` and `load_dts = '1900-01-01 00:00:00'`.
- **Ghost Records in Satellites:**
  - Every Satellite must be seeded with exactly one Ghost Record at deployment time:
    - $hk = \text{ZERO\_KEY\_UNKNOWN}$ (`'0'*64`)
    - $\text{load\_dts} = \text{'1900-01-01 00:00:00'}$
    - $\text{rec\_src} = \text{'SYSTEM\_GHOST'}$
    - Text columns = `'UNKNOWN'`, numeric columns = $0$ or $\text{NULL}$.
- **PIT Equi-Join Completeness:** When a Hub key has no active state in Satellite $S_i$ at snapshot time `as_of_dts`, the PIT table records `hk_sat_i = ZERO_KEY_UNKNOWN` and `load_dts = '1900-01-01 00:00:00'`. This allows Information Marts to execute fast **`INNER JOIN`s** across all Satellites without dropping rows or resorting to costly `LEFT JOIN`s.

#### 2. Open-DV Architectural Critique
- **Where Open-DV Excels:**
  - Open-DV's implementation of Zero Keys and Ghost Records is comprehensive.
  - In `opendv-core/constants.py`:
    `ZERO_KEY_UNKNOWN = '0'*64`, `ZERO_KEY_ERROR = 'f'*64`, `GHOST_DTS = '1900-01-01 00:00:00'`, `GHOST_REC_SRC = 'SYSTEM_GHOST'`.
  - In `compiler_4zone.py` Zone 0, every Hub, Link, and Satellite receives automated DDL and seed queries generating both Zero Keys and the Satellite Ghost Record.
  - In `dynamic_checks.py`, rule **`DV-DYN-007`** verifies that every Hub and Link contains both Zero Keys and every Satellite contains the Ghost Record.
  - Adversarial Batch 1 (`adversarial_batches.py`) proves referential integrity with zero orphan records (`DV-DYN-002`).
- **Ambiguities and Gaps:**
  - *Distinction Between `-1` and `-2` in SQL Staging:* In `compiler_4zone.py`, `build_algorithm_a5_sha256_sql` evaluates:
    ```sql
    CASE WHEN {all_null_pred} THEN REPEAT('0', 64) ELSE {sha_expr} END
    ```
    This automatically maps all-null business keys to `ZERO_KEY_UNKNOWN` (`'0'*64`). However, it does not provide an automated SQL branch for `ZERO_KEY_ERROR` (`'f'*64`). If an optional foreign key fails domain parsing or represents an inapplicable state, the staging view maps it to the null token unless bespoke SQL expressions are authored.

#### 3. Concrete Recommendations & Invariants
- **Enhance Staging SQL Zero-Key Expression in `compiler_4zone.py`:**
  Update `build_algorithm_a5_sha256_sql` to generate a 3-way conditional for foreign keys:
  ```sql
  CASE 
      WHEN {col} = '-1' OR {col} IS NULL THEN REPEAT('0', 64)
      WHEN {col} = '-2' THEN REPEAT('f', 64)
      ELSE LOWER(SHA256(...))
  END
  ```
  This fully standardizes `-1` (Unknown) and `-2` (Inapplicable/Error) in both Python `resolve_fk_hash_key_with_zero_keys` and compiled SQL views.

---

## Synthesis: Summary of Discovered Discrepancies & Recommended Fixes

| Discrepancy / Gap | Subsystem | Severity | DV 2.0 Impact | Remediation Action | GitHub Issue |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **`STRLEN` vs `OCTET_LENGTH`** | `opendv-duckdb-sandbox/compiler_4zone.py`, `pit_query_engine.py`, `alg_a6_sqlglot_ast.py` | **Critical** *(Resolved)* | Multi-byte UTF-8 data produces divergent hash digests between DuckDB SQL and Python / test vectors. | Replaced `STRLEN(...)` with `OCTET_LENGTH(ENCODE(...))` and restricted `DV-AST-002` to `OCTET_LENGTH`/`BYTE_LENGTH` ([`docs/17`](../17-testing-scenarios-and-datasets.md)). | [#30](https://github.com/nogoo9/open-dv/issues/30) |
| **Binary Link Nullable FK Split** | `opendv-axi-verifier/codemods/alg_a4_cold_start.py` | **High** | Algorithm A.4 only splits Links with arity $\ge 3$. Binary Links with sparse FKs insert Zero-Key relationships. | Extend Algorithm A.4 to filter or demote sparse binary Links ($U_{nn} < 0.95$). | [#31](https://github.com/nogoo9/open-dv/issues/31) |
| **Unstructured Gating in DML** | `opendv-duckdb-sandbox/compiler_4zone.py` | **High** | Unstructured extraction links in Zone 2 lack explicit `steward_gate_status = 'APPROVED'` filter. | Inject approval check into compiled Zone 2 DML for all unstructured links. | [#32](https://github.com/nogoo9/open-dv/issues/32) |
| **Windows CRLF Span Hallucination** | `opendv-axi-verifier/verifiers/alg_a9b_unstructured.py` | **Medium** | CRLF line endings shift character indices by 1 byte per newline, falsely triggering `SPAN_HALLUCINATION`. | Normalize newlines (`replace('\r\n', '\n')`) prior to span verification. | [#33](https://github.com/nogoo9/open-dv/issues/33) |
| **Retroactive Delta Comparison** | `opendv-duckdb-sandbox/compiler_4zone.py` | **Medium** | Satellite delta comparison partitions by `hk` and orders by `load_dts DESC`, ignoring `applied_date`. | Order by `applied_date` or partition by `(hk, applied_date)` for bi-temporal delta detection. | [#34](https://github.com/nogoo9/open-dv/issues/34) |
| **Hash Hex Casing Discrepancy** | `opendv-core/hashing.py` vs `compiler_4zone.py` | **Medium** | Python generates uppercase hex (`.upper()`); DuckDB generates lowercase hex (`LOWER(...)`). | Standardize on lowercase hex across all packages. | [#37](https://github.com/nogoo9/open-dv/issues/37) |
| **Zone 0 Migration DDL Omission** | `opendv-duckdb-sandbox/compiler_4zone.py` | **Medium** | In-place Satellite column additions are not physically applied to existing database tables (`CREATE TABLE IF NOT EXISTS`). | Add `zone_0_migration_sql` with idempotent `ALTER TABLE ADD COLUMN` statements. | [#38](https://github.com/nogoo9/open-dv/issues/38) |
| **Staging BKCC Scoping** | `opendv-duckdb-sandbox/compiler_4zone.py` | **Medium** | BKCC literal is scoped per table rather than per `(table, column, target_hub)` tuple. | Scope staging BKCC mapping per `(table, column, target_hub)` tuple. | [#39](https://github.com/nogoo9/open-dv/issues/39) |
| **Tier-1 Global Prefix Invariant** | `opendv-axi-verifier` & `opendv-core` | **Medium** | Mismatch between literal `'GLOBAL'` prefix in PE model vs empty prefix in generic IR. | Add Invariant `DV-COL-013` to standardize Tier-1 global key prefixing. | [#40](https://github.com/nogoo9/open-dv/issues/40) |
| **Same-As-Link Canonical Ordering** | `opendv-axi-verifier/gate3_networkx_dag.py` | **Medium** | Lack of canonical edge sorting on Same-As Links (`sal_*`) risks reflexive loops and duplicate pairs. | Add Invariant `DV-STR-015` to enforce canonical edge ordering. | [#41](https://github.com/nogoo9/open-dv/issues/41) |
| **Dynamic Multi-Tenant Column BKCC** | `opendv-duckdb-sandbox/compiler_4zone.py` | **Medium** | `bkcc_literal` is static string only; dynamic tenant column expression not natively supported. | Permit dynamic `bkcc_column_expr` in staging view compiler. | [#42](https://github.com/nogoo9/open-dv/issues/42) |
| **Error Zero Key Staging Routing** | `opendv-duckdb-sandbox/compiler_4zone.py` | **Medium** | Error Zero Key sentinel `'f'*64` is pre-seeded but never emitted by Zone 1 staging SQL. | Implement 3-way staging branch for Unknown (`-1`) vs Error (`-2`) Zero Keys. | [#43](https://github.com/nogoo9/open-dv/issues/43) |
| **AST Linter Aggregation Ban Whitelist** | `opendv-axi-verifier/alg_a6_sqlglot_ast.py` | **Medium** | Rule `DV-AST-006` bans all `GROUP BY` in Raw Vault, flagging Hub micro-batch deduplication SQL. | Whitelist idempotent micro-batch deduplication aggregations in `DV-AST-006`. | [#44](https://github.com/nogoo9/open-dv/issues/44) |

---

## Conclusion & Architectural Sign-Off

Open-DV represents an exceptionally well-engineered, mathematically grounded implementation of Data Vault 2.0. Its neuro-symbolic separation—ensuring LLMs propose declarative contracts while deterministic verifiers and compilers enforce structural and cryptographic purity—is the gold standard for AI-assisted data engineering.

By remediating the highlighted discrepancies (principally adopting `OCTET_LENGTH` in DuckDB, harmonizing hash casing, extending nullable-FK guards to binary links, and enforcing steward approval in Zone 2 extraction DML), Open-DV will achieve an unassailable level of enterprise robustness, mathematical consistency, and full compliance with Dan Linstedt's Data Vault 2.0 standard.
