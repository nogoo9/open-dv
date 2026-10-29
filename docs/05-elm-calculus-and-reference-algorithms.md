# 05 — Ensemble Logical Modeling (ELM) Calculus, Mathematical Formulations, and Production Reference Algorithms (`A.1`–`A.9`)

> **Part of the [`open-dv` Reference Guide Suite](DV2_AGENT_REFERENCE_GUIDE.md)**  
> **Domain:** Mathematical Formulations for Graph B & Gate 4 Cost Guard, ELM Workshop Calculus, Three-Pass Chain-of-Thought State Machine, Production System Prompt Template, and Complete Production Python Reference Implementations (`Algorithms A.1`–`A.9` & `D.10`)  
> **Companion Specifications:**
> - [01 — Core Architecture, Entity Specifications, and Methodology Standards](01-core-modelling-spec.md)
> - [02 — Schema Stability, Cold-Start Extensibility, and Evolution Mechanics](02-schema-stability-and-evolution.md)
> - [03 — Automated Verification Gates and `dv-firstmate` Swarm Architecture](03-verification-and-agentic-architecture.md)
> - [04 — Unstructured Data & Multi-Modal Integration in Data Vault 2.0](04-unstructured-and-multimodal-dv2.md)
> - [Constrained Decoding Contract Schema (`schemas/no-mistake-dv2-contract-v5.schema.json`)](../schemas/no-mistake-dv2-contract-v5.schema.json)
> - [Annotated Contract Example (`examples/dv_model_contract.yaml`)](../examples/dv_model_contract.yaml)
> - [HTML Reference Guide (`docs/references/No-Mistake-Agentic-Data-Vault-2.0-Complete-Design-and-Implementation-Guide.html`)](references/No-Mistake-Agentic-Data-Vault-2.0-Complete-Design-and-Implementation-Guide.html)

---

## 1. Formal Mathematical Formulations for Graph B (Bronze Empirical Profile) & Gate 4 Cost Guard

Before any LLM modelling agent is invoked, `scout-source` deterministically computes **Graph B (The Bronze Empirical Profile Graph)** via DuckDB or Spark profiling over Bronze Parquet/Iceberg tables and Change Data Capture (CDC) logs:

1. **Uniqueness Ratio $U(c)$ & Non-Null Ratio $U_{nn}(c)$:**
   $$U(c) = \frac{|\text{DISTINCT}(c)|}{N_{\text{non-null}}(c)}, \qquad U_{nn}(c) = \frac{N_{\text{non-null}}(c)}{N_{\text{total}}}$$

2. **Cross-Table Jaccard Similarity $J(c_1, c_2)$ & Asymmetric Containment $C(c_1, c_2)$:**
   Computed via MinHash and HyperLogLog sketches across all candidate key columns in the Bronze catalog:
   $$J(c_1, c_2) = \frac{|V(c_1) \cap V(c_2)|}{|V(c_1) \cup V(c_2)|}, \qquad C(c_1, c_2) = \frac{|V(c_1) \cap V(c_2)|}{\min(|V(c_1)|, |V(c_2)|)}$$

3. **Surrogate vs. Natural Key Heuristic $S_{\text{surr}}(c)$:**
   Combines sequential monotonic step detection ($\Delta x_i = 1$) and Shannon character-class entropy to classify columns into:
   - `MONOTONIC_INT`
   - `RANDOM_UUID_V4`
   - `GLOBAL_STANDARD_CODE`
   - `STRUCTURED_NATURAL_CODE`
   - `FREE_TEXT`

4. **CDC Temporal Volatility $\lambda(c)$ & Log-Volatility $v(c)$:**
   Using Bronze CDC logs (`_cdc_op = 'U'`), measures the empirical rate of value change per entity key per day and transforms into Laplace-smoothed log space ($\epsilon = 10^{-6}$):
   $$\lambda(c) = \frac{\text{Count}(\text{CDC Updates where } c_t \neq c_{t-1})}{N_{\text{entities}} \times \Delta t_{\text{days}}}, \qquad v(c) = \log_{10}(\lambda(c) + 10^{-6})$$

5. **Key Mutability Rate $\mu_{BK}(c)$ & Foreign Key Swap Rate $\mu_{FK}(c)$:**
   Measures how frequently a candidate business key or foreign key target changes while the underlying record identifier remains constant. Whenever $\mu_{FK}(c) > 0$, Algorithm A.3 automatically designates the driving Hub key and injects a Link Effectivity Satellite (`esat_*`).

6. **Gate 4 Daily Storage Write-Amplification Cost Function $W_{\text{amp}}$ (bytes/day):**
   Given payload columns $C$ partitioned by catalog sensitivity map $\sigma$ into $C_{\text{sensitive}} = \{c \in C \mid \sigma(c) \in \{\texttt{PII\_HIGH}, \texttt{PCI}\}\}$ and $C_{\text{general}} = C \setminus C_{\text{sensitive}}$, 3-tier volatility splitting (`_static` for $\lambda \le 10^{-3}$, `_core` for $10^{-3} < \lambda \le 5 \times 10^{-2}$, `_high_freq` for $\lambda > 5 \times 10^{-2}$) executes **if and only if** $|C_{\text{general}}| \ge 10$, $\max_{c} v(c) - \min_{c} v(c) \ge 2.0$, and:
   $$W_{\text{amp}} = N_{\text{entities}} \times \left(\max_{c \in C_{\text{general}}} \lambda(c)\right) \times \sum_{c \in C_{\text{general}},\, \lambda(c) \le 0.05} \text{avg\_width\_bytes}(c) \ge \tau_{\text{bytes/day}} \quad (\tau_{\text{bytes/day}} = 50\text{ MB/day})$$

---

## 2. Ensemble Logical Modeling (ELM) Calculus & Production System Prompt

### 2.1 Mapping the Four ELM Workshop Pillars to Deterministic Agent Rules

| ELM Workshop Artifact | Traditional Human Workshop Question | Deterministic Agent Rule & Empirical Signal |
| :--- | :--- | :--- |
| **1. Core Business Concept (CBC) Discovery** | *"Is this a real thing the business tracks independently, or just a property/state?"* | **The Independent Existence Test:** A candidate concept qualifies as a Hub iff: (a) it matches an `urn:ontology:entity:*` node, (b) it possesses a natural identifier, (c) its distinct cardinality exceeds enumeration bounds ($\text{DISTINCT count} > 250$) OR grows with business volume, and (d) it has physical columns in the current Bronze scope (**Zero-Phantom-Entity Rule**). |
| **2. The 5-W Event Canvas (NBRs)** | *"Who, What, Where, When, and How does this business event occur as an atomic unit of work?"* | **The Mandatory Kernel vs. Optional Context Rule:** For any Bronze event table, bind all mandatory participating Hubs ($U_{nn} \ge 0.95$) into a single N-ary Link kernel. Separate sparse/optional foreign keys ($U_{nn} < 0.95$) into dedicated binary contextual Links so future sources without those optional attributes integrate cleanly. |
| **3. Exhaustive Column Triage** | *"Where does every source attribute live in the ensemble?"* | **Zero-Orphan Ledger Invariant:** Every column in the input Bronze tables must be explicitly classified in `column_triage_ledger`. Surrogate primary keys superseded by natural business keys must be marked `SYSTEM_SURROGATE_DROPPED`. |
| **4. Context Partitioning** | *"What describes the Hub/Link at a point in time?"* | **Source-Aligned Context:** Group all `DESCRIPTIVE_PAYLOAD` columns by their parent Hub/Link and single Bronze table. Delegate PII isolation and cost-guarded rate-of-change splitting to the deterministic Gate 4 Codemod (`Algorithm A.1`). |

### 2.2 The Three-Pass Chain-of-Thought State Machine

Before emitting `dv_model_contract.yaml` ([`schemas/no-mistake-dv2-contract-v5.schema.json`](../schemas/no-mistake-dv2-contract-v5.schema.json)), the modelling agent executes a structured three-pass reasoning trace:
- **Pass 1 — Column-by-Column Triage, Surrogate Elimination & 3-Tier Authority Assignment:** Iterate through every Bronze table. Discard `MONOTONIC_INT` and `RANDOM_UUID_V4` surrogates (`_id`) whenever a natural key exists. Classify every Business Key into `TIER_1_GLOBAL_STANDARD`, `TIER_2_ENTERPRISE_SOR`, or `TIER_3_LOCAL_SCOPED` (enforcing `require_src_qualifier: true` on Tier 3).
- **Pass 2 — Multi-Source Ensemble Consolidation & Kernel/Optional Link Synthesis:** Group all `BUSINESS_KEY` columns mapping to the same `urn:ontology:entity:*` URI into unified Hubs. For event tables, assemble mandatory 5-W participants ($U_{nn} \ge 0.95$) into the core N-ary Link and split sparse foreign keys ($U_{nn} < 0.95$) into binary contextual Links.
- **Pass 3 — Backwards-Compatibility & Ambiguity Escalation Check:** Verify that no existing deployed Hub or Link hash key order is mutated. If any entity lacks an ontology URI or exhibits 0% cross-source overlap with no shared natural key, populate `unmapped_escalations`.

### 2.3 Production System Prompt Template

```text
You are a Principal Data Vault 2.0 & Ensemble Logical Modeling (ELM) Compiler Agent.
Your sole responsibility is to transform the provided [BUSINESS_ONTOLOGY_GRAPH], [BRONZE_EMPIRICAL_PROFILE_GRAPH], and optional [EXISTING_DEPLOYED_CONTRACT] into a strictly valid `NoMistakeDataVault2ContractV5` specification.

### HARD ARCHITECTURAL INVARIANTS (VIOLATION TRIGGERS COMPILER REJECTION)
1. 100% COLUMN ACCOUNTABILITY: Every column present in [BRONZE_EMPIRICAL_PROFILE_GRAPH] MUST appear exactly once in `column_triage_ledger`. Do NOT invent phantom Hubs or Links for ontology concepts that have no columns in the current Bronze tables.
2. ANTI-SILO INTEGRATION: Never create source-specific Hubs (e.g., `hub_sfdc_account`). If multiple Bronze tables map to the same `urn:ontology:entity:*` URI, consolidate them as multiple `business_key_sources` inside a single canonical Hub.
3. 3-TIER BUSINESS KEY AUTHORITY (COLD-START EXTENSIBILITY):
   - TIER_1_GLOBAL_STANDARD: Column matches an ontology global regex (UEN, Tax ID, ISIN, LEI, IBAN). Set `require_src_qualifier: false` and `bkcc_prefix: null`.
   - TIER_2_ENTERPRISE_SOR: Source system is listed in the ontology `authoritative_sor` for this entity. Set `require_src_qualifier: false` and `bkcc_prefix: null`.
   - TIER_3_LOCAL_SCOPED: Local/departmental key with no global standard pattern and non-authoritative source. You MUST set `require_src_qualifier: true` and `bkcc_prefix: "<record_source_code>"` to prevent cross-system key collisions when future sources are onboarded.
4. SURROGATE KEY REJECTION: Never select a column with `pattern_class: MONOTONIC_INT` or `RANDOM_UUID_V4` as a Hub Business Key if a Tier-1 or Tier-2 natural key exists in the table. Mark dropped surrogates as `SYSTEM_SURROGATE_DROPPED`.
5. 5-W EVENT CANVAS & NULLABLE-FK LINK SPLIT:
   - Bind all MANDATORY participating Hubs (`non_null_ratio >= 0.95`) of an event table into a single N-ary Link to preserve the atomic unit of work.
   - Do NOT include sparse/optional foreign keys (`non_null_ratio < 0.95`) in the core N-ary Link hash. Classify them as `OPTIONAL_FOREIGN_KEY_REF` and model them as separate binary Links with `where_filter_condition: "<fk_col> IS NOT NULL"`.
6. ENUMERATION DEMOTION: Any concept with `taxonomy_class: ENUMERATION` or static distinct count <= 250 with zero key growth MUST be modeled in `reference_tables` (`ref_`) or as a descriptive satellite payload—NEVER as a Hub.
7. ADDITIVE EVOLUTION LOCK: If [EXISTING_DEPLOYED_CONTRACT] is provided, you may ONLY add new `business_key_sources`, new Links, or new Satellites. Never alter the `key_columns` order or `hub_references` of an already deployed Hub or Link.
8. NO GUESSING ON AMBIGUITY: If a Bronze table represents a core entity that has no matching URI in [BUSINESS_ONTOLOGY_GRAPH], classify its columns as `ESCALATED_AMBIGUOUS` and emit an entry in `unmapped_escalations`.
```

---

## 3. Complete Production Reference Implementations (Algorithms A.1–A.9 & Multimodal PIT SQL)

In a No-Mistake architecture, mechanical, statistical, and evolutionary refactoring is never delegated back to the LLM. Below are the complete, unshortened production Python implementations for Algorithms `A.1` through `A.9` and the canonical Multimodal Point-in-Time (`D.10`) SQL query. The Graph B profiler (Section 1), the Gate 3 `networkx` topology verifier, the Gate 5 4-Batch DuckDB sandbox, and the Ghost Record seeder have no reference implementation here; they are specified in [`03-verification-and-agentic-architecture.md`](03-verification-and-agentic-architecture.md).

### 3.1 Algorithm A.1: 3-Axis Satellite Partitioning Codemod with Cold-Start Write-Amplification Guard

```python
from __future__ import annotations
import math
from copy import deepcopy
from typing import Any, Dict, List

SENSITIVE_TAGS = {"PII_HIGH", "PCI"}
CLASSIFICATION_RANK = {"PUBLIC": 0, "INTERNAL": 1, "CONFIDENTIAL": 2, "PII_HIGH": 3, "PCI": 4}

def escalate_classification(declared: str, column_tags: List[str]) -> str:
    """Returns the most restrictive of the declared and column-level tags; never downgrades."""
    return max([declared, *column_tags], key=lambda tag: CLASSIFICATION_RANK.get(tag, 1))

def codemod_split_satellites_3axis(
    contract: Dict[str, Any],
    bronze_profile: Dict[str, Dict[str, Dict[str, Any]]],
    min_cols_for_volatility_split: int = 10,
    log_spread_threshold: float = 2.0,  # 10^2 = 100x rate-of-change spread
    write_amp_threshold_bytes_per_day: float = 50 * 1024 * 1024,  # 50 MB/day YAGNI guard
    epsilon: float = 1e-6,
) -> Dict[str, Any]:
    """
    Deterministically splits Satellites across:
    1. Governance/PII isolation (_pii) -> Always enforced, even at Cold Start (N=1).
    2. Empirical CDC log-volatility tiers (_static, _core, _high_freq) -> Enforced only
       when column width, log-volatility spread, AND daily byte write-amplification
       exceed the Cold-Start YAGNI thresholds.
    Synchronizes column_triage_ledger in-place.
    """
    mutated = deepcopy(contract)
    new_satellites: List[Dict[str, Any]] = []
    ledger_remap: Dict[tuple[str, str], str] = {}

    for sat in mutated.get("satellites", []):
        if sat["satellite_type"] != "STANDARD_SATELLITE":
            new_satellites.append(sat)
            continue

        b_table = sat["source_bronze_table"]
        base_name = sat["name"]
        payload: List[str] = sat.get("payload_columns", [])
        table_profile = bronze_profile.get(b_table, {})
        declared_class = sat.get("security_classification", "INTERNAL")
        declared_tier = sat.get("volatility_tier", "UNCLASSIFIED")
        col_tags = {
            col: table_profile.get(col, {}).get("security_classification", "INTERNAL")
            for col in payload
        }

        # A Satellite already named *_pii is the sensitive partition itself: keep it whole.
        if base_name.endswith("_pii"):
            pii_sat = deepcopy(sat)
            pii_sat["security_classification"] = escalate_classification(
                declared_class, list(col_tags.values())
            )
            pii_sat["payload_columns"] = sorted(payload)
            new_satellites.append(pii_sat)
            for c in payload:
                ledger_remap[(b_table, c)] = base_name
            continue

        # Step 1: Partition PII / PCI columns from General payload (Always active)
        sensitive_cols: List[str] = []
        general_cols: List[str] = []

        for col in sorted(payload):
            if col_tags[col] in SENSITIVE_TAGS:
                sensitive_cols.append(col)
            else:
                general_cols.append(col)

        if sensitive_cols:
            pii_sat_name = f"{base_name}_pii"
            pii_sat = deepcopy(sat)
            pii_sat["name"] = pii_sat_name
            pii_sat["security_classification"] = escalate_classification(
                declared_class, [col_tags[c] for c in sensitive_cols]
            )
            pii_sat["volatility_tier"] = declared_tier
            pii_sat["payload_columns"] = sorted(sensitive_cols)
            new_satellites.append(pii_sat)
            for c in sensitive_cols:
                ledger_remap[(b_table, c)] = pii_sat_name

        if not general_cols:
            continue

        general_class = escalate_classification(declared_class, [col_tags[c] for c in general_cols])

        # Step 2: Evaluate log10 CDC volatility spread & Daily Write-Amplification Cost
        log_vols: Dict[str, float] = {}
        raw_vols: Dict[str, float] = {}
        col_widths: Dict[str, float] = {}
        entity_count = 1

        for col in general_cols:
            c_prof = table_profile.get(col, {})
            lam = float(c_prof.get("cdc_lambda_per_day", 0.0))
            width = float(c_prof.get("avg_width_bytes", 32.0))
            entity_count = max(entity_count, int(c_prof.get("table_row_count", 1000)))
            raw_vols[col] = lam
            log_vols[col] = math.log10(lam + epsilon)
            col_widths[col] = width

        v_spread = max(log_vols.values()) - min(log_vols.values()) if len(general_cols) > 1 else 0.0
        max_lambda = max(raw_vols.values()) if raw_vols else 0.0
        slow_bytes = sum(col_widths[c] for c in general_cols if raw_vols[c] <= 5e-2)
        daily_write_amp_bytes = entity_count * max_lambda * slow_bytes

        # Cold-Start YAGNI Guard: Keep a single Satellite unless all 3 thresholds are breached
        should_split_volatility = (
            len(general_cols) >= min_cols_for_volatility_split
            and v_spread >= log_spread_threshold
            and daily_write_amp_bytes >= write_amp_threshold_bytes_per_day
        )

        if not should_split_volatility:
            clean_sat = deepcopy(sat)
            clean_sat["name"] = base_name
            clean_sat["security_classification"] = general_class
            clean_sat["volatility_tier"] = declared_tier
            clean_sat["payload_columns"] = sorted(general_cols)
            new_satellites.append(clean_sat)
            for c in general_cols:
                ledger_remap[(b_table, c)] = base_name
            continue

        # Step 3: Partition into STATIC (<= 1e-3), STANDARD (1e-3 .. 5e-2), HIGH_FREQ (> 5e-2)
        tiers: Dict[str, List[str]] = {"STATIC": [], "STANDARD": [], "HIGH_FREQ": []}
        for col in general_cols:
            lam = raw_vols[col]
            if lam <= 1e-3:
                tiers["STATIC"].append(col)
            elif lam <= 5e-2:
                tiers["STANDARD"].append(col)
            else:
                tiers["HIGH_FREQ"].append(col)

        suffix_map = {"STATIC": "static", "STANDARD": "core", "HIGH_FREQ": "high_freq"}

        for tier_key, cols in tiers.items():
            if not cols:
                continue
            tier_sat_name = f"{base_name}_{suffix_map[tier_key]}"
            tier_sat = deepcopy(sat)
            tier_sat["name"] = tier_sat_name
            tier_sat["security_classification"] = general_class
            tier_sat["volatility_tier"] = tier_key
            tier_sat["payload_columns"] = sorted(cols)
            new_satellites.append(tier_sat)
            for c in cols:
                ledger_remap[(b_table, c)] = tier_sat_name

    mutated["satellites"] = new_satellites

    # Step 4: Synchronize the Column Triage Ledger
    for entry in mutated.get("column_triage_ledger", []):
        key = (entry["bronze_table"], entry["column_name"])
        if key in ledger_remap and entry["elm_classification"] == "DESCRIPTIVE_PAYLOAD":
            entry["target_dv_entity"] = ledger_remap[key]

    return mutated
```

---

### 3.2 Algorithm A.2: Low-Cardinality Hub-to-Reference Demotion & Graph Rewiring Codemod

```python
from copy import deepcopy
from typing import Any, Dict, List

def codemod_demote_enum_hubs_to_ref(
    contract: Dict[str, Any],
    ontology_graph: Dict[str, Dict[str, Any]],
    bronze_profile: Dict[str, Dict[str, Dict[str, Any]]],
    max_enum_cardinality: int = 250,
) -> Dict[str, Any]:
    """
    Identifies Hubs that represent static enumerations, converts them into
    Reference Tables (ref_*), and rewires all connected Links and Satellites.
    """
    mutated = deepcopy(contract)
    demoted_hubs: Dict[str, str] = {}  # hub_name -> ref_name
    retained_hubs: List[Dict[str, Any]] = []

    for hub in mutated.get("hubs", []):
        uri = hub["ontology_concept_uri"]
        concept_meta = ontology_graph.get(uri, {})
        is_enum_concept = concept_meta.get("taxonomy_class") == "ENUMERATION"

        max_card = 0
        max_growth = 0.0
        for src in hub["business_key_sources"]:
            b_table = src["bronze_table"]
            for k_col in src["key_columns"]:
                col_prof = bronze_profile.get(b_table, {}).get(k_col, {})
                max_card = max(max_card, int(col_prof.get("distinct_count", 999999)))
                max_growth = max(max_growth, float(col_prof.get("key_growth_30d", 1.0)))

        if is_enum_concept or (max_card <= max_enum_cardinality and max_growth == 0.0):
            concept_slug = hub["name"].replace("hub_", "", 1)
            ref_name = f"ref_{concept_slug}"
            demoted_hubs[hub["name"]] = ref_name

            desc_cols: List[str] = []
            for sat in mutated.get("satellites", []):
                if sat["parent_entity"] == hub["name"]:
                    desc_cols.extend(sat.get("payload_columns", []))

            primary_src = hub["business_key_sources"][0]
            enum_uri = uri.replace(":entity:", ":enum:")
            mutated.setdefault("reference_tables", []).append({
                "name": ref_name,
                "ontology_concept_uri": enum_uri,
                "source_bronze_table": primary_src["bronze_table"],
                "code_column": primary_src["key_columns"][0],
                "description_columns": sorted(set(desc_cols)),
            })
        else:
            retained_hubs.append(hub)

    if not demoted_hubs:
        return mutated

    mutated["hubs"] = retained_hubs
    mutated["satellites"] = [
        sat for sat in mutated.get("satellites", [])
        if sat["parent_entity"] not in demoted_hubs
    ]

    retained_links: List[Dict[str, Any]] = []
    folded_satellite_cols: List[tuple[str, str, List[str]]] = []

    for lnk in mutated.get("links", []):
        real_refs = [r for r in lnk["hub_references"] if r["target_hub"] not in demoted_hubs]
        demoted_refs = [r for r in lnk["hub_references"] if r["target_hub"] in demoted_hubs]

        if not demoted_refs:
            retained_links.append(lnk)
            continue

        if len(real_refs) >= 2:
            lnk["hub_references"] = real_refs
            degen = list(lnk.get("degenerate_columns", []))
            for d_ref in demoted_refs:
                degen.extend(d_ref["source_fk_columns"])
            lnk["degenerate_columns"] = sorted(set(degen))
            retained_links.append(lnk)
        elif len(real_refs) == 1:
            real_hub = real_refs[0]["target_hub"]
            fk_cols: List[str] = []
            for d_ref in demoted_refs:
                fk_cols.extend(d_ref["source_fk_columns"])
            folded_satellite_cols.append((real_hub, lnk["source_bronze_table"], fk_cols))

    mutated["links"] = retained_links

    for real_hub, b_table, fk_cols in folded_satellite_cols:
        target_sat = next(
            (
                s for s in mutated["satellites"]
                if s["parent_entity"] == real_hub and s["source_bronze_table"] == b_table
            ),
            None,
        )
        if target_sat is not None:
            merged_payload = set(target_sat.get("payload_columns", [])) | set(fk_cols)
            target_sat["payload_columns"] = sorted(merged_payload)

    for entry in mutated.get("column_triage_ledger", []):
        if entry["target_dv_entity"] in demoted_hubs:
            entry["elm_classification"] = "REFERENCE_CODE"
            entry["target_dv_entity"] = demoted_hubs[entry["target_dv_entity"]]

    return mutated
```

---

### 3.3 Algorithm A.3: Link Effectivity Satellite (`esat_`) & Driving Key Auto-Injection Codemod

```python
from copy import deepcopy
from typing import Any, Dict

def codemod_inject_link_effectivity(
    contract: Dict[str, Any],
    bronze_link_cdc_profile: Dict[str, Dict[str, float]],
) -> Dict[str, Any]:
    """
    Inspects empirical CDC relationship turnover rates for all STANDARD_LINK entities.
    Automatically sets driving_key_hub and synthesizes an EFFECTIVITY_SATELLITE (esat_*)
    whenever relationship target swapping is detected in Bronze CDC logs.
    """
    mutated = deepcopy(contract)
    existing_eff_parents = {
        sat["parent_entity"]
        for sat in mutated.get("satellites", [])
        if sat["satellite_type"] == "EFFECTIVITY_SATELLITE"
    }

    for lnk in mutated.get("links", []):
        if lnk["link_type"] != "STANDARD_LINK":
            continue

        b_table = lnk["source_bronze_table"]
        turnover_map = bronze_link_cdc_profile.get(b_table, {})

        best_hub = None
        best_fk_col = None
        max_swap_rate = 0.0

        for ref in lnk.get("hub_references", []):
            fk_col = ref["source_fk_columns"][0]
            swap_rate = float(turnover_map.get(fk_col, 0.0))
            if swap_rate > max_swap_rate:
                max_swap_rate = swap_rate
                best_hub = ref["target_hub"]
                best_fk_col = fk_col

        if max_swap_rate > 0.0 and best_hub is not None:
            lnk["driving_key_hub"] = best_hub

            if lnk["name"] not in existing_eff_parents:
                link_slug = lnk["name"].replace("lnk_", "", 1)
                eff_sat_name = f"esat_{link_slug}"
                mutated.setdefault("satellites", []).append({
                    "name": eff_sat_name,
                    "satellite_type": "EFFECTIVITY_SATELLITE",
                    "parent_entity": lnk["name"],
                    "source_bronze_table": b_table,
                    "driving_key_column": best_fk_col,
                    "security_classification": "INTERNAL",
                    "volatility_tier": "STANDARD",
                    "payload_columns": ["START_DTS", "END_DTS", "IS_CURRENT_FLAG"],
                })
                existing_eff_parents.add(lnk["name"])

    return mutated
```

---

### 3.4 Algorithm A.4: Cold-Start 3-Tier BK Authority (`BKCC`) & Nullable-FK Link Splitter Codemod

```python
import re
from copy import deepcopy
from typing import Any, Dict, List

def codemod_enforce_cold_start_extensibility(
    contract: Dict[str, Any],
    ontology_graph: Dict[str, Dict[str, Any]],
    bronze_profile: Dict[str, Dict[str, Dict[str, Any]]],
    mandatory_fk_non_null_threshold: float = 0.95,
) -> Dict[str, Any]:
    """
    Enforces Cold-Start (N=1) extensibility invariants:
    1. 3-Tier Business Key Authority & BKCC collision prefixing on Hub keys.
    2. Splitting sparse/nullable foreign keys (U_nn < 0.95) out of N-ary Links
       into dedicated binary contextual Links.
    """
    mutated = deepcopy(contract)

    # Part 1: Enforce 3-Tier Business Key Authority & BKCC on Hubs
    for hub in mutated.get("hubs", []):
        uri = hub["ontology_concept_uri"]
        concept_meta = ontology_graph.get(uri, {})
        auth_sors = set(concept_meta.get("authoritative_sor", []))

        for src in hub.get("business_key_sources", []):
            b_table = src["bronze_table"]
            rec_src = src["record_source_code"]
            key_cols = src["key_columns"]

            is_tier1_global = all(
                bronze_profile.get(b_table, {}).get(c, {}).get("pattern_class") == "GLOBAL_STANDARD_CODE"
                for c in key_cols
            )
            is_tier2_sor = rec_src in auth_sors

            if is_tier1_global:
                src["key_authority_tier"] = "TIER_1_GLOBAL_STANDARD"
                src["require_src_qualifier"] = False
                src["bkcc_prefix"] = None
            elif is_tier2_sor:
                src["key_authority_tier"] = "TIER_2_ENTERPRISE_SOR"
                src["require_src_qualifier"] = False
                src["bkcc_prefix"] = None
            else:
                src["key_authority_tier"] = "TIER_3_LOCAL_SCOPED"
                src["require_src_qualifier"] = True
                src["bkcc_prefix"] = rec_src

    # Part 2: Split sparse/nullable FKs out of N-ary Links (N >= 3)
    rewired_links: List[Dict[str, Any]] = []
    ledger_fk_remap: Dict[tuple[str, str], str] = {}

    for lnk in mutated.get("links", []):
        refs = lnk.get("hub_references", [])
        if len(refs) < 3 or lnk["link_type"] not in {"STANDARD_LINK", "NON_HISTORIZED_LINK"}:
            rewired_links.append(lnk)
            continue

        b_table = lnk["source_bronze_table"]
        table_prof = bronze_profile.get(b_table, {})

        mandatory_refs: List[Dict[str, Any]] = []
        optional_refs: List[Dict[str, Any]] = []

        for ref in refs:
            min_nn_ratio = min(
                float(table_prof.get(fk_col, {}).get("non_null_ratio", 1.0))
                for fk_col in ref["source_fk_columns"]
            )
            if min_nn_ratio >= mandatory_fk_non_null_threshold:
                mandatory_refs.append(ref)
            else:
                optional_refs.append(ref)

        if len(mandatory_refs) >= 2 and optional_refs:
            core_lnk = deepcopy(lnk)
            core_lnk["hub_references"] = mandatory_refs
            rewired_links.append(core_lnk)

            anchor_ref = mandatory_refs[0]
            base_slug = re.sub(r"^(lnk|t_lnk|nh_lnk)_", "", lnk["name"])

            for opt_ref in optional_refs:
                role_slug = opt_ref["role_alias"].lower()
                opt_lnk_name = f"lnk_{base_slug}_{role_slug}"
                not_null_clauses = [f"{col} IS NOT NULL" for col in opt_ref["source_fk_columns"]]

                opt_lnk = {
                    "name": opt_lnk_name,
                    "link_type": "STANDARD_LINK",
                    "ontology_concept_uri": lnk["ontology_concept_uri"],
                    "source_bronze_table": b_table,
                    "driving_key_hub": anchor_ref["target_hub"],
                    "where_filter_condition": " AND ".join(not_null_clauses),
                    "degenerate_columns": [],
                    "hub_references": [anchor_ref, opt_ref],
                }
                rewired_links.append(opt_lnk)
                for fk_col in opt_ref["source_fk_columns"]:
                    ledger_fk_remap[(b_table, fk_col)] = opt_lnk_name
        else:
            rewired_links.append(lnk)

    mutated["links"] = rewired_links

    for entry in mutated.get("column_triage_ledger", []):
        key = (entry["bronze_table"], entry["column_name"])
        if key in ledger_fk_remap:
            entry["elm_classification"] = "OPTIONAL_FOREIGN_KEY_REF"
            entry["target_dv_entity"] = ledger_fk_remap[key]

    return mutated
```

---

### 3.5 Algorithm A.5: SQLGlot Physical AST Codemod for Canonical `hk_*` (with `BKCC`) & Alphabetical `hash_diff` Rewriting

```python
from functools import reduce
from typing import Dict, List, Optional, Tuple
import sqlglot
from sqlglot import exp

NULL_TOKEN = "-1:^^"
ZERO_KEY = "0" * 64
DELIMITER = "||"

def build_canonical_column_standardization_ast(
    col_name: str,
    is_business_key: bool = True,
) -> exp.Expression:
    """
    Constructs the normalized (NULL-preserving) value of a column:
    - Business Keys: NULLIF(UPPER(TRIM(CAST(col AS VARCHAR))), '')
    - Satellite Payload: NULLIF(TRIM(CAST(col AS VARCHAR)), '')
    """
    cast_expr = exp.Cast(
        this=exp.column(col_name),
        to=exp.DataType.build("VARCHAR"),
    )
    trim_expr = exp.Trim(this=cast_expr)
    core_str = exp.Upper(this=trim_expr) if is_business_key else trim_expr
    return exp.Nullif(this=core_str, expression=exp.Literal.string(""))

def build_length_prefixed_ast(norm: exp.Expression) -> exp.Expression:
    """
    Length-Prefixed Canonical Encoding (KEY-02 / KEY-03):
    CASE WHEN norm IS NULL THEN '-1:^^' ELSE CAST(OCTET_LENGTH(norm) AS VARCHAR) || ':' || norm END
    OCTET_LENGTH is the UTF-8 byte length (Snowflake / ANSI); DuckDB spells it STRLEN(varchar).
    """
    byte_len = exp.Cast(
        this=exp.Anonymous(this="OCTET_LENGTH", expressions=[norm.copy()]),
        to=exp.DataType.build("VARCHAR"),
    )
    encoded = exp.DPipe(
        this=exp.DPipe(this=byte_len, expression=exp.Literal.string(":")),
        expression=norm.copy(),
    )
    return exp.Case(
        ifs=[exp.If(this=exp.Is(this=norm.copy(), expression=exp.Null()), true=exp.Literal.string(NULL_TOKEN))],
        default=encoded,
    )

def build_canonical_sha256_hash_ast(
    columns: List[str],
    alias_name: str,
    is_business_key: bool = True,
    sort_alphabetically: bool = False,
    bkcc_prefix: Optional[str] = None,
) -> exp.Alias:
    """
    Builds UPPER(SHA2(enc(c_1) || '||' || ... || enc(c_n), 256)) with enc() = length-prefixed encoding.
    - If bkcc_prefix is set (Tier-3 local key), prepends the encoded BKCC literal.
    - For Business Keys (is_business_key=True), preserves semantic composite key order and
      resolves to the Zero Key REPEAT('0', 64) when every BK column (excluding bkcc) is NULL.
    - For Satellite hash_diff (sort_alphabetically=True), enforces strict lexicographical order.
    """
    ordered_cols = sorted(columns) if sort_alphabetically else list(columns)
    normalized = [
        build_canonical_column_standardization_ast(c, is_business_key=is_business_key)
        for c in ordered_cols
    ]
    encoded_args: List[exp.Expression] = []

    if is_business_key and bkcc_prefix:
        bkcc = bkcc_prefix.strip().upper()
        encoded_args.append(exp.Literal.string(f"{len(bkcc.encode('utf-8'))}:{bkcc}"))

    encoded_args.extend(build_length_prefixed_ast(n) for n in normalized)

    concatenated = reduce(
        lambda acc, arg: exp.DPipe(
            this=exp.DPipe(this=acc, expression=exp.Literal.string(DELIMITER)),
            expression=arg,
        ),
        encoded_args[1:],
        encoded_args[0],
    )
    digest = exp.Upper(this=exp.SHA2(this=concatenated, length=exp.Literal.number(256)))

    if is_business_key:
        all_null = reduce(
            lambda acc, cond: exp.And(this=acc, expression=cond),
            [exp.Is(this=n.copy(), expression=exp.Null()) for n in normalized],
        )
        digest = exp.Case(
            ifs=[exp.If(this=all_null, true=exp.Literal.string(ZERO_KEY))],
            default=digest,
        )
    return exp.alias_(digest, alias_name)

def codemod_enforce_staging_hashes_sql(
    raw_sql: str,
    hub_keys: Dict[str, Tuple[List[str], Optional[str]]],
    satellite_hashdiffs: Dict[str, List[str]],
    dialect: str = "snowflake",
) -> str:
    """
    Parses a Staging SQL model via SQLGlot, removes any existing non-compliant hk_*
    or hash_diff projections, and injects canonical AST expressions.
    hub_keys maps: hk_alias -> (list_of_key_columns, optional_bkcc_prefix)
    """
    parsed = sqlglot.parse_one(raw_sql, read=dialect)
    select_node = parsed.find(exp.Select)
    if select_node is None:
        raise ValueError("Staging SQL model must contain a valid SELECT expression.")

    target_aliases = set(hub_keys.keys()) | set(satellite_hashdiffs.keys())

    cleaned_projections = [
        proj for proj in select_node.expressions
        if proj.alias_or_name not in target_aliases
    ]

    for hk_alias, (bk_cols, bkcc_prefix) in hub_keys.items():
        cleaned_projections.append(
            build_canonical_sha256_hash_ast(
                columns=bk_cols,
                alias_name=hk_alias,
                is_business_key=True,
                sort_alphabetically=False,
                bkcc_prefix=bkcc_prefix,
            )
        )

    for hd_alias, payload_cols in satellite_hashdiffs.items():
        cleaned_projections.append(
            build_canonical_sha256_hash_ast(
                columns=payload_cols,
                alias_name=hd_alias,
                is_business_key=False,
                sort_alphabetically=True,
                bkcc_prefix=None,
            )
        )

    select_node.set("expressions", cleaned_projections)
    return parsed.sql(dialect=dialect, pretty=True)
```

---

### 3.6 Algorithm A.6: SQLGlot Static AST Invariant Verifier (Entity Signatures, Insert-Only & Raw Vault Purity)

```python
from typing import List
import sqlglot
from sqlglot import exp

HUB_PREFIXES = ("hub_", "bhub_")
LINK_PREFIXES = ("lnk_", "t_lnk_", "nh_lnk_", "sal_", "hal_", "x_lnk_")
SAT_PREFIXES = ("sat_", "bsat_", "mas_", "ma_sat_", "esat_", "sts_", "rts_")
HASH_DIFF_SAT_PREFIXES = ("sat_", "bsat_", "mas_", "ma_sat_")
RAW_VAULT_PREFIXES = ("hub_", "lnk_", "t_lnk_", "nh_lnk_", "sal_", "hal_", "sat_", "mas_", "ma_sat_", "esat_", "sts_", "rts_")
LINK_KEY_PREFIXES = tuple(f"hk_{p}" for p in LINK_PREFIXES)

def verify_physical_dv2_sql_ast(
    model_name: str,
    sql_text: str,
    dialect: str = "snowflake",
) -> List[str]:
    """
    Parses compiled Data Vault 2.0 SQL via SQLGlot and enforces physical AST invariants
    against the canonical IR column names (hk_*, hk_lnk_*, load_dts, rec_src, hash_diff):
    1. Insert-Only guarantee (no UPDATE, DELETE, TRUNCATE, DROP).
    2. Entity column signature contracts for Hubs, Links, and Satellites.
    3. Raw Vault purity (no GROUP BY or HAVING inside Raw Vault models).
    """
    violations: List[str] = []
    parsed = sqlglot.parse_one(sql_text, read=dialect)

    # 1. Insert-Only Guarantee
    forbidden_mutations = (exp.Delete, exp.Update, exp.TruncateTable, exp.Drop)
    if parsed.find(*forbidden_mutations):
        violations.append(
            f"AST_ERROR [{model_name}]: Contains destructive DML/DDL node. Raw Vault is strictly Insert-Only."
        )

    select_node = parsed.find(exp.Select)
    if select_node is None:
        violations.append(f"AST_ERROR [{model_name}]: Missing valid SELECT projection.")
        return violations

    projected_cols = {proj.alias_or_name.lower() for proj in select_node.expressions}
    hash_diff_cols = [c for c in projected_cols if c.startswith("hash_diff")]

    # 2. Mandatory Audit Columns Check (PIT / Bridge carry no rec_src)
    audit_cols = ("load_dts",) if model_name.startswith(("pit_", "brg_")) else ("load_dts", "rec_src")
    for audit_col in audit_cols:
        if audit_col not in projected_cols:
            violations.append(f"AST_ERROR [{model_name}]: Missing mandatory audit column '{audit_col}'.")

    # 3. Archetype-Specific Structural Checks
    if model_name.startswith(HUB_PREFIXES):
        hk_cols = sorted(c for c in projected_cols if c.startswith("hk_"))
        if len(hk_cols) != 1:
            violations.append(f"AST_ERROR [{model_name}]: Hub must project exactly 1 hk_* column; found {hk_cols}.")
        if hash_diff_cols:
            violations.append(f"AST_ERROR [{model_name}]: Hubs cannot contain hash_diff columns.")

    elif model_name.startswith(LINK_PREFIXES):
        lk_cols = sorted(c for c in projected_cols if c.startswith(LINK_KEY_PREFIXES))
        hk_refs = sorted(c for c in projected_cols if c.startswith("hk_") and c not in lk_cols)
        if len(lk_cols) != 1:
            violations.append(f"AST_ERROR [{model_name}]: Link must project 1 Link Hash Key (hk_lnk_*); found {lk_cols}.")
        if len(hk_refs) < 2:
            violations.append(f"AST_ERROR [{model_name}]: Link must reference >= 2 Hub Hash Keys; found {hk_refs}.")

    elif model_name.startswith(SAT_PREFIXES) and not model_name.startswith("sat_ref_"):
        parent_keys = sorted(c for c in projected_cols if c.startswith("hk_"))
        if len(parent_keys) != 1:
            violations.append(
                f"AST_ERROR [{model_name}]: Satellite must reference exactly 1 parent hk_*; found {parent_keys}."
            )
        if model_name.startswith(HASH_DIFF_SAT_PREFIXES) and not hash_diff_cols:
            violations.append(f"AST_ERROR [{model_name}]: Standard Satellite must project a hash_diff column.")

    # 4. Raw Vault Purity Check (No Aggregations in Raw Vault)
    if model_name.startswith(RAW_VAULT_PREFIXES):
        if parsed.find(exp.Group) or parsed.find(exp.Having):
            violations.append(
                f"AST_ERROR [{model_name}]: Aggregations (GROUP BY / HAVING) are forbidden in Raw Vault models."
            )

    return violations
```

---

### 3.7 Algorithm A.7: Additive Evolution Backwards-Compatibility CI Gate ($N \to N+1$ Hash-Signature Lock)

```python
from typing import Any, Dict, List

def verify_additive_evolution_invariants(
    deployed_contract: Dict[str, Any],
    candidate_contract: Dict[str, Any],
) -> List[str]:
    """
    Verifies that transitioning from deployed_contract (Stage N) to candidate_contract (Stage N+1)
    is 100% additive and never mutates existing Hub or Link hash signatures.
    """
    errors: List[str] = []

    # 1. Verify Hub backwards compatibility
    cand_hubs = {h["name"]: h for h in candidate_contract.get("hubs", [])}
    for dep_hub in deployed_contract.get("hubs", []):
        h_name = dep_hub["name"]
        if h_name not in cand_hubs:
            errors.append(
                f"EVOLUTION_ERROR [HUB_DELETED]: Deployed Hub '{h_name}' was removed in candidate contract."
            )
            continue

        cand_hub = cand_hubs[h_name]
        if cand_hub["ontology_concept_uri"] != dep_hub["ontology_concept_uri"]:
            errors.append(
                f"EVOLUTION_ERROR [HUB_CONCEPT_DRIFT]: Hub '{h_name}' changed ontology URI from "
                f"'{dep_hub['ontology_concept_uri']}' to '{cand_hub['ontology_concept_uri']}'."
            )

        cand_sources = {
            (s["bronze_table"], s["record_source_code"]): s
            for s in cand_hub.get("business_key_sources", [])
        }
        for dep_src in dep_hub.get("business_key_sources", []):
            src_key = (dep_src["bronze_table"], dep_src["record_source_code"])
            if src_key not in cand_sources:
                errors.append(
                    f"EVOLUTION_ERROR [HUB_SOURCE_REMOVED]: Source {src_key} removed from Hub '{h_name}'."
                )
                continue

            cand_src = cand_sources[src_key]
            if cand_src["key_columns"] != dep_src["key_columns"]:
                errors.append(
                    f"EVOLUTION_ERROR [BK_SIGNATURE_MUTATED]: Hub '{h_name}' source {src_key} "
                    f"altered key_columns from {dep_src['key_columns']} to {cand_src['key_columns']}."
                )
            if cand_src.get("bkcc_prefix") != dep_src.get("bkcc_prefix"):
                errors.append(
                    f"EVOLUTION_ERROR [BKCC_MUTATED]: Hub '{h_name}' source {src_key} "
                    f"altered bkcc_prefix from '{dep_src.get('bkcc_prefix')}' to '{cand_src.get('bkcc_prefix')}'."
                )

    # 2. Verify Link backwards compatibility (composite FK order & arity lock)
    cand_links = {l["name"]: l for l in candidate_contract.get("links", [])}
    for dep_lnk in deployed_contract.get("links", []):
        l_name = dep_lnk["name"]
        if l_name not in cand_links:
            errors.append(
                f"EVOLUTION_ERROR [LINK_DELETED]: Deployed Link '{l_name}' was removed in candidate contract."
            )
            continue

        cand_lnk = cand_links[l_name]
        dep_sig = [
            (r["target_hub"], r["role_alias"], tuple(r["source_fk_columns"]))
            for r in dep_lnk.get("hub_references", [])
        ]
        cand_sig = [
            (r["target_hub"], r["role_alias"], tuple(r["source_fk_columns"]))
            for r in cand_lnk.get("hub_references", [])
        ]
        if dep_sig != cand_sig:
            errors.append(
                f"EVOLUTION_ERROR [LINK_ARITY_OR_ORDER_MUTATED]: Deployed Link '{l_name}' "
                f"changed hub_references signature from {dep_sig} to {cand_sig}. "
                f"Create a new Link instead of mutating an existing Link's composite hash."
            )

    return errors
```

---

### 3.8 Algorithm A.8: Master `axi-dv` Verification & Codemod Orchestrator Loop

```python
from typing import Any, Dict, Optional, Tuple

def run_axi_dv_verification_pipeline(
    candidate_contract: Dict[str, Any],
    ontology_graph: Dict[str, Dict[str, Any]],
    bronze_profile: Dict[str, Dict[str, Dict[str, Any]]],
    bronze_link_cdc_profile: Dict[str, Dict[str, float]],
    deployed_contract: Optional[Dict[str, Any]] = None,
) -> Tuple[str, Dict[str, Any], list[str]]:
    """
    Executes the complete No-Mistake Data Vault 2.0 verification and codemod loop.
    Returns:
      status: "PASS" | "REJECT_WITH_TRACE" | "ASK_USER"
      transformed_contract: Codemodded contract dictionary
      diagnostics: Structured compiler error traces or human escalation prompts
    """
    diagnostics: list[str] = []

    # 0. Check for explicit human architect escalations emitted by the agent
    if candidate_contract.get("unmapped_escalations"):
        for esc in candidate_contract["unmapped_escalations"]:
            diagnostics.append(
                f"ASK_USER [{esc['escalation_reason']}] on {esc['bronze_table']} "
                f"({esc['columns']}): {esc['architect_question']}"
            )
        return "ASK_USER", candidate_contract, diagnostics

    # 1. Execute Deterministic Graph Codemods (`auto-fix` stage)
    contract = codemod_demote_enum_hubs_to_ref(
        candidate_contract, ontology_graph, bronze_profile
    )
    contract = codemod_enforce_cold_start_extensibility(
        contract, ontology_graph, bronze_profile
    )
    contract = codemod_inject_link_effectivity(
        contract, bronze_link_cdc_profile
    )
    contract = codemod_split_satellites_3axis(
        contract, bronze_profile
    )

    # 2. Gate 0 Assertion: Additive Evolution Lock (if upgrading an existing Vault N -> N+1)
    if deployed_contract is not None:
        diagnostics.extend(
            verify_additive_evolution_invariants(deployed_contract, contract)
        )

    # 3. Gate 1 Assertion: Anti-Silo Ontology Uniqueness & Zero-Phantom-Entity Check
    seen_concepts: Dict[str, str] = {}
    active_entities = {
        entry["target_dv_entity"]
        for entry in contract.get("column_triage_ledger", [])
    }

    for hub in contract.get("hubs", []):
        uri = hub["ontology_concept_uri"]
        if uri in seen_concepts:
            diagnostics.append(
                f"SEMANTIC_ERROR [Gate 1 - ANTI_SILO_VIOLATION]: Both '{seen_concepts[uri]}' "
                f"and '{hub['name']}' bind to '{uri}'. Consolidate into a single canonical Hub."
            )
        seen_concepts[uri] = hub["name"]

        if hub["name"] not in active_entities:
            diagnostics.append(
                f"SEMANTIC_ERROR [Gate 1 - PHANTOM_HUB_REJECTED]: Hub '{hub['name']}' has no "
                f"mapped physical columns in column_triage_ledger."
            )

    # 4. Gate 2 Assertion: Surrogate Key Ban when Natural Key Exists
    for hub in contract.get("hubs", []):
        for src in hub["business_key_sources"]:
            b_table = src["bronze_table"]
            for k_col in src["key_columns"]:
                prof = bronze_profile.get(b_table, {}).get(k_col, {})
                pat = prof.get("pattern_class", "UNKNOWN")
                if pat in {"MONOTONIC_INT", "RANDOM_UUID_V4"} and not src.get("system_scoped_bk_justification"):
                    diagnostics.append(
                        f"SEMANTIC_ERROR [Gate 2 - SURROGATE_KEY_REJECTED]: Hub '{hub['name']}' "
                        f"uses column '{b_table}.{k_col}' classified as '{pat}' without justification. "
                        f"Select a natural business key or provide system_scoped_bk_justification."
                    )

    if diagnostics:
        return "REJECT_WITH_TRACE", contract, diagnostics

    return "PASS", contract, ["ALL_GATES_GREEN: Contract verified and cold-start codemods applied."]
```

---

### 3.9 Algorithm A.9: Zero-Rehash Intra-Source Schema Drift Codemod (`A.9a`) & Gate 5 Unstructured Verifier (`A.9b`)

```python
from typing import Any, Dict, List

def codemod_intra_source_schema_drift(
    deployed_contract: Dict[str, Any],
    candidate_contract: Dict[str, Any],
    schema_epoch_tag: str = "v2",
) -> Dict[str, Any]:
    """
    Algorithm A.9a: Zero-Rehash Intra-Source Schema Drift Codemod.
    When an already-deployed Satellite gains new descriptive columns from an existing
    Bronze table, automatically isolates the newly added columns into an additive
    companion Satellite (sat_<entity>_<src>_ext_<epoch>) so historical HASHDIFF digests
    in the primary Satellite remain 100% immutable.
    """
    deployed_sats = {s["name"]: s for s in deployed_contract.get("satellites", [])}
    updated_satellites: List[Dict[str, Any]] = []

    for sat in candidate_contract.get("satellites", []):
        s_name = sat["name"]
        if s_name not in deployed_sats:
            updated_satellites.append(sat)
            continue

        dep_sat = deployed_sats[s_name]
        dep_cols = set(dep_sat.get("payload_columns", []))
        cand_cols = set(sat.get("payload_columns", []))

        added_cols = sorted(cand_cols - dep_cols)
        removed_cols = sorted(dep_cols - cand_cols)

        # Preserve original payload columns on the deployed Satellite (soft-tombstoning removed cols in stg_)
        sat["payload_columns"] = sorted(dep_cols)
        if removed_cols:
            sat["soft_deprecated_columns"] = removed_cols
        updated_satellites.append(sat)

        # Spin off newly added columns into an additive companion Satellite
        if added_cols:
            updated_satellites.append({
                "name": f"{s_name}_ext_{schema_epoch_tag}",
                "satellite_type": sat["satellite_type"],
                "parent_entity": sat["parent_entity"],
                "source_bronze_table": sat["source_bronze_table"],
                "security_classification": sat.get("security_classification", "INTERNAL"),
                "volatility_tier": "ADDITIVE_DRIFT_COMPANION",
                "payload_columns": added_cols,
            })

    candidate_contract["satellites"] = updated_satellites
    return candidate_contract

def verify_gate5_unstructured_invariants(
    contract: Dict[str, Any],
    parsed_doc_samples: Dict[str, str],
) -> List[str]:
    """
    Algorithm A.9b: Gate 5 Verifier for Unstructured Data Vault 2.0 Integration.
    Enforces binary/vector isolation, provenance satellites on AI links, and exact
    character-span substring verification on LLM-extracted Business Vault attributes.
    """
    errors: List[str] = []
    sats_by_parent: Dict[str, List[Dict[str, Any]]] = {}

    for sat in contract.get("satellites", []):
        sats_by_parent.setdefault(sat["parent_entity"], []).append(sat)
        if sat.get("satellite_type") == "STANDARD_SATELLITE":
            for col in sat.get("payload_columns", []):
                if any(k in col.lower() for k in ("raw_blob", "embedding_vec", "binary_bytes")):
                    errors.append(
                        f"GATE5_ERROR [BLOB_OR_VEC_IN_RAW_SAT]: Satellite '{sat['name']}' "
                        f"includes '{col}'. Isolate binary files in Object Storage and "
                        f"embeddings in a dedicated VECTOR_SATELLITE."
                    )

    for lnk in contract.get("links", []):
        if lnk.get("link_type") == "UNSTRUCTURED_EXTRACTION_LINK":
            attached = sats_by_parent.get(lnk["name"], [])
            req_cols = {"extractor_model_id", "confidence_score", "evidence_quote"}
            if not any(
                s.get("satellite_type") == "AI_PROVENANCE_SATELLITE"
                and req_cols.issubset(set(s.get("payload_columns", [])))
                for s in attached
            ):
                errors.append(
                    f"GATE5_ERROR [MISSING_PROVENANCE_SAT]: Unstructured Link '{lnk['name']}' "
                    f"must attach an AI_PROVENANCE_SATELLITE containing {sorted(req_cols)}."
                )

    for sample in contract.get("sample_extractions", []):
        doc_id = sample["doc_id"]
        quote = sample.get("evidence_quote", "")
        start, end = sample.get("char_start", 0), sample.get("char_end", 0)
        body = parsed_doc_samples.get(doc_id, "")
        if not quote or body[start:end] != quote:
            errors.append(
                f"GATE5_ERROR [SPAN_HALLUCINATION]: Extracted attribute '{sample.get('attribute')}' "
                f"in document '{doc_id}' failed exact substring verification at [{start}:{end}]."
            )

    return errors
```

---

### 3.10 Canonical Multimodal Point-in-Time (PIT) + GraphRAG Retrieval SQL Query (`D.10`)

```sql
-- Canonical Multimodal Point-in-Time (PIT) + GraphRAG Retrieval Query
SELECT
    h_cust.COMPANY_UEN,
    s_erp.CREDIT_LIMIT_SGD,
    s_cov.COVENANT_MAX_LEVERAGE_RATIO,
    s_cov.EVIDENCE_QUOTE          AS COVENANT_PDF_QUOTE,
    s_man.OBJECT_STORAGE_URI      AS SOURCE_PDF_URI,
    s_vec.VECTOR_ARRAY            AS CHUNK_EMBEDDING
FROM raw_vault.hub_customer h_cust
JOIN biz_vault.pit_customer p_cust
  ON h_cust.HK_CUSTOMER = p_cust.HK_CUSTOMER
 AND p_cust.AS_OF_DTS = TIMESTAMP '2026-10-29 00:00:00'
JOIN raw_vault.sat_customer_erp s_erp
  ON p_cust.HK_CUSTOMER = s_erp.HK_CUSTOMER
 AND p_cust.SAT_ERP_LOAD_DTS = s_erp.LOAD_DTS
JOIN biz_vault.lnk_document_entity_mention l_mention
  ON h_cust.HK_CUSTOMER = l_mention.HK_CUSTOMER
JOIN biz_vault.sat_lnk_doc_entity_ai_provenance s_prov
  ON l_mention.HK_LNK_DOC_ENTITY = s_prov.HK_LNK_DOC_ENTITY
 AND s_prov.CONFIDENCE_SCORE >= 0.85
 AND s_prov.LOAD_DTS <= p_cust.AS_OF_DTS
JOIN raw_vault.sat_document_manifest s_man
  ON l_mention.HK_DOCUMENT = s_man.HK_DOCUMENT
LEFT JOIN biz_vault.sat_contract_doc_extracted_bv s_cov
  ON l_mention.HK_DOCUMENT = s_cov.SOURCE_HK_DOCUMENT
 AND s_cov.LOAD_DTS <= p_cust.AS_OF_DTS
LEFT JOIN biz_vault.sat_doc_chunk_vec_text_emb_004 s_vec
  ON l_mention.HK_DOC_CHUNK = s_vec.HK_DOC_CHUNK;
```
