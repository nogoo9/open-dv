"""Gates 2–6 Verification & Deterministic AST Codemods Suite (`tests/test_verification_gates_and_codemods.py`).

Validates:
1. Algorithm A.1 (ELM-to-DV2 Folding & Grain Check):
   - Positive verification on Private Equity Graph A (`pe_graph_a_ontology.yaml`) + Graph B (`pe_graph_b_profile.json`).
   - Negative detection of Trap 1 (mutable `deal_stage` placed in Hub -> `DV-G2-002`)
     and Trap 2 (2-way Link for 3-way co-investment grain -> `DV-G2-001` / `AP-06`).
2. Gate 3 (NetworkX MultiDiGraph Topology Verifier):
   - Positive verification on Private Equity model (both the 6-Hub, 4-Link, 9-Satellite core PE benchmark
     and the physical `pe_canonical_ir.json` / `dv_pe_model_contract.yaml` topology).
   - Negative detection of direct Hub-to-Hub FK (`DV-G3-001`), Link with `< 2` Hubs (`DV-G3-002`),
     Satellite with `!= 1` parent (`DV-G3-003`), Link-to-Link reference without feature flag (`DV-G3-004`),
     and directed cycle (`DV-G3-005`).
3. Algorithm A.4 (YAGNI & 100% Column Triage Completeness):
   - Verify 67/67 Graph B column accountability in `dv_pe_model_contract.yaml` (`column_triage_ledger`) and `pe_canonical_ir.json`.
   - Detect phantom unmapped Hub (`DV-G4-001`), unaccounted source column (`DV-G4-002`),
     and mixed-cadence/mixed-classification Satellite requiring split (`SPLIT_REQUIRED` / `DV-G4-003` / Trap 3).
4. Algorithm A.6 (`sqlglot` AST Linter for Staging & Vault SQL):
   - Positive verification on valid Staging/Hub/Link/Satellite `INSERT` SQL (and `docs/07` DuckDB SQL).
   - Negative detection of nondeterministic `NOW()`/`CURRENT_TIMESTAMP`/`RANDOM()` (`DV-G6-001`),
     physical `DELETE`/`UPDATE`/`TRUNCATE` (`DV-G6-002`), `SELECT *` (`DV-G6-003`),
     missing `HASH_DIFF` comparison on Satellite load (`DV-G6-004`), and Raw Vault `GROUP BY`/`HAVING`
     aggregations (`DV-G6-005`).
5. Algorithms A.1–A.8 Reference Codemods & Closed-Loop Self-Healing Pipeline (`docs/05`):
   - Codemod 1 (extract mutable attribute from Hub into Satellite) and Codemod 2 (split Satellite by cadence
     and `data_classification`), verifying idempotence (`f(f(x)) == f(x)`) and post-codemod Gate 2 / Gate 4 pass.
   - Direct execution of `docs/05` reference functions (`codemod_split_satellites_3axis`,
     `codemod_demote_enum_hubs_to_ref`, `codemod_inject_link_effectivity`,
     `codemod_enforce_cold_start_extensibility`, `codemod_enforce_staging_hashes_sql`,
     `verify_physical_dv2_sql_ast`, `verify_additive_evolution_invariants`,
     `run_axi_dv_verification_pipeline`).
"""

from __future__ import annotations

import copy
import pathlib
from typing import Any

import networkx as nx
import pytest
import sqlglot
from sqlglot import exp

REPO_ROOT = pathlib.Path(__file__).resolve().parents[1]
PE_DIR = REPO_ROOT / "examples" / "private_equity"
GRAPH_A_PATH = PE_DIR / "pe_graph_a_ontology.yaml"
GRAPH_B_PATH = PE_DIR / "pe_graph_b_profile.json"
PE_CONTRACT_PATH = PE_DIR / "dv_pe_model_contract.yaml"
PE_IR_PATH = PE_DIR / "pe_canonical_ir.json"


# ============================================================================
# Reference Verifiers & Codemods (Algorithms A.1, Gate 3, A.4, A.6, A.7, A.8)
# ============================================================================


def verify_algorithm_a1_elm_folding_and_grain(
    graph_a: dict[str, Any],
    graph_b: dict[str, Any],
    contract: dict[str, Any],
) -> list[str]:
    """Algorithm A.1: Verify ELM-to-DV2 folding and Link grain alignment across Graph A, Graph B, and Contract.

    Detects:
      - `DV-G2-002` (Trap 1): Mutable attribute (`MUTABLE_STATUS` / `DESCRIPTIVE_PAYLOAD`) placed inside a Hub.
      - `DV-G2-001` / `AP-06` (Trap 2): Link grain mismatch where a 3-way co-investment relationship
        (`co_invest_portco_lei` / `LP_COMMITMENT_CO_INVEST`) is collapsed into a 2-way Link or missing its Hub reference.
    """
    violations: list[str] = []

    # Build lookup of source column metadata from Graph B
    col_profile_map: dict[tuple[str, str], dict[str, Any]] = {}
    for ss in graph_b.get("source_systems", []):
        for tbl in ss.get("tables", []):
            b_table = tbl["bronze_table"]
            for col in tbl.get("columns", []):
                col_profile_map[(b_table, col["column_name"])] = col

    # 1. Check Hubs for mutable attributes (Trap 1 -> DV-G2-002)
    IMMUTABLE_BK_ROLES = {
        "TIER_1_BUSINESS_KEY",
        "TIER_2_BUSINESS_KEY",
        "TIER_3_LOCAL_SCOPED_KEY",
        "BUSINESS_KEY",
    }
    for hub in contract.get("hubs", []):
        hub_name = hub["name"]
        # Explicit mutable columns leaked into Hub definition
        if hub.get("mutable_columns") or hub.get("extra_columns") or hub.get("descriptive_attributes"):
            violations.append(
                f"DV-G2-002:Trap 1 mutable attribute leaked into Hub '{hub_name}'"
            )
        for bk_src in hub.get("business_key_sources", []):
            b_table = bk_src["bronze_table"]
            for k_col in bk_src.get("key_columns", []):
                col_meta = col_profile_map.get((b_table, k_col), {})
                role = col_meta.get("candidate_role") or col_meta.get("column_role")
                drift = col_meta.get("drift_rate_lambda", 0.0)
                if role not in IMMUTABLE_BK_ROLES or drift > 0.0:
                    violations.append(
                        f"DV-G2-002:Hub '{hub_name}' key column '{b_table}.{k_col}' has mutable role '{role}' (drift={drift})"
                    )

    # Also inspect column_triage_ledger for mutable columns routed to a Hub
    hub_names = {h["name"] for h in contract.get("hubs", [])}
    for entry in contract.get("column_triage_ledger", []):
        if entry.get("target_dv_entity") in hub_names and entry.get("elm_classification") != "BUSINESS_KEY":
            violations.append(
                f"DV-G2-002:Column '{entry['bronze_table']}.{entry['column_name']}' with role '{entry['elm_classification']}' routed to Hub '{entry['target_dv_entity']}'"
            )

    # 2. Check Link grain alignment against Graph A relationships & Graph B optional FKs (Trap 2 -> DV-G2-001 / AP-06)
    rel_by_uri = {
        r["relationship_uri"]: r for r in graph_a.get("canonical_relationships", [])
    }
    links_by_name = {lnk["name"]: lnk for lnk in contract.get("links", [])}

    for lnk in contract.get("links", []):
        lnk_name = lnk["name"]
        rel_uri = lnk.get("ontology_concept_uri")
        hub_refs = lnk.get("hub_references", [])
        if rel_uri in rel_by_uri:
            expected_endpoints = rel_by_uri[rel_uri]["endpoints"]
            actual_endpoints = [r["target_hub"] for r in hub_refs]
            if len(actual_endpoints) < len(expected_endpoints):
                violations.append(
                    f"DV-G2-001:AP-06:Link '{lnk_name}' has {len(actual_endpoints)}-way grain {actual_endpoints}, expected {len(expected_endpoints)}-way grain {expected_endpoints}"
                )

    # Check that every OPTIONAL_FOREIGN_KEY_REF in Graph B (e.g. co_invest_portco_lei) is bound to a >=3-way Link
    for (b_table, col_name), col_meta in col_profile_map.items():
        if col_meta.get("candidate_role") == "OPTIONAL_FOREIGN_KEY_REF":
            ledger_matches = [
                e
                for e in contract.get("column_triage_ledger", [])
                if e["bronze_table"] == b_table and e["column_name"] == col_name
            ]
            if not ledger_matches:
                violations.append(
                    f"DV-G2-001:AP-06:Optional FK '{b_table}.{col_name}' missing from column_triage_ledger"
                )
                continue
            target_entity = ledger_matches[0]["target_dv_entity"]
            target_lnk = links_by_name.get(target_entity)
            if not target_lnk or len(target_lnk.get("hub_references", [])) < 3:
                violations.append(
                    f"DV-G2-001:AP-06:Co-investment optional FK '{b_table}.{col_name}' routed to '{target_entity}' without 3-way Link grain"
                )

    return violations


def verify_gate3_networkx_topology(
    model: dict[str, Any],
    *,
    allow_link_to_link: bool = False,
) -> tuple[nx.MultiDiGraph, list[str]]:
    """Gate 3: Construct a `networkx.MultiDiGraph` of the Data Vault topology and verify structural axioms.

    Detects:
      - `DV-G3-001`: Direct Hub-to-Hub foreign key reference (`out_degree(hub) > 0`).
      - `DV-G3-002`: Link with `< 2` parent Hub references.
      - `DV-G3-003`: Satellite with `!= 1` parent table (0 parents, `>= 2` parents, or unknown parent).
      - `DV-G3-004`: Link-to-Link reference when `allow_link_to_link=False`.
      - `DV-G3-005`: Directed cycle in the Data Vault schema graph.
    """
    G = nx.MultiDiGraph()
    violations: list[str] = []

    hub_names: set[str] = set()
    link_names: set[str] = set()
    sat_names: set[str] = set()

    for hub in model.get("hubs", []):
        h_name = hub["name"]
        hub_names.add(h_name)
        G.add_node(h_name, node_type="HUB")

    for lnk in model.get("links", []):
        l_name = lnk["name"]
        link_names.add(l_name)
        G.add_node(l_name, node_type="LINK")

    for sat in model.get("satellites", []):
        s_name = sat["name"]
        sat_names.add(s_name)
        G.add_node(s_name, node_type="SATELLITE")

    # 1. Inspect Hub edges (DV-G3-001: Hub -> Hub or outbound Hub FK)
    for hub in model.get("hubs", []):
        h_name = hub["name"]
        for fk_target in hub.get("parent_hubs", []) + hub.get("foreign_keys", []):
            G.add_edge(h_name, fk_target, edge_type="HUB_FK")
            violations.append(
                f"DV-G3-001:Direct Hub-to-Hub FK from '{h_name}' to '{fk_target}' is forbidden"
            )

    # 2. Inspect Link edges (DV-G3-002: < 2 Hubs; DV-G3-004: Link-to-Link reference)
    for lnk in model.get("links", []):
        l_name = lnk["name"]
        raw_refs = lnk.get("hub_references") if "hub_references" in lnk else lnk.get("parent_hubs", [])
        targets: list[str] = []
        for ref in raw_refs:
            t_name = ref["target_hub"] if isinstance(ref, dict) and "target_hub" in ref else (
                ref["hub_name"] if isinstance(ref, dict) and "hub_name" in ref else str(ref)
            )
            targets.append(t_name)
            G.add_edge(l_name, t_name, edge_type="LINK_PARENT")

        for l2l_target in lnk.get("link_references", []):
            targets.append(l2l_target)
            G.add_edge(l_name, l2l_target, edge_type="LINK_TO_LINK")
            if not allow_link_to_link:
                violations.append(
                    f"DV-G3-004:Link '{l_name}' references Link '{l2l_target}' without allow_link_to_link feature flag"
                )

        hub_targets = [t for t in targets if t in hub_names]
        non_hub_targets = [t for t in targets if t in link_names]
        if non_hub_targets and not allow_link_to_link:
            for nht in non_hub_targets:
                violations.append(
                    f"DV-G3-004:Link '{l_name}' references Link '{nht}' without allow_link_to_link feature flag"
                )

        if len(hub_targets) < 2 and not (allow_link_to_link and len(targets) >= 2):
            violations.append(
                f"DV-G3-002:Link '{l_name}' references only {len(hub_targets)} Hub(s); minimum 2 required"
            )

    # 3. Inspect Satellite edges (DV-G3-003: != 1 parent)
    valid_parents = hub_names | link_names
    for sat in model.get("satellites", []):
        s_name = sat["name"]
        primary_parent = sat.get("parent_entity") if "parent_entity" in sat else sat.get("parent_table")
        parents: list[str] = []
        if isinstance(primary_parent, list):
            parents.extend(primary_parent)
        elif isinstance(primary_parent, str) and primary_parent.strip():
            parents.append(primary_parent.strip())
        parents.extend(sat.get("additional_parents", []))

        for p in parents:
            G.add_edge(s_name, p, edge_type="SAT_PARENT")

        if len(parents) != 1 or parents[0] not in valid_parents:
            violations.append(
                f"DV-G3-003:Satellite '{s_name}' must attach to exactly 1 valid Hub or Link parent (found {parents})"
            )

    # 4. Inspect Directed Cycles (DV-G3-005)
    if not nx.is_directed_acyclic_graph(G):
        cycles = list(nx.simple_cycles(G))
        violations.append(f"DV-G3-005:Directed cycle(s) detected in Vault topology: {cycles}")

    return G, violations


def verify_algorithm_a4_yagni_and_column_triage(
    graph_b: dict[str, Any],
    contract: dict[str, Any],
    *,
    write_amp_threshold_mb_per_day: float = 50.0,
) -> list[str]:
    """Algorithm A.4: Verify 100% Graph B column triage accountability, YAGNI zero-source Hub pruning,
    and Satellite security/cadence homogeneity.

    Detects:
      - `DV-G4-001`: Phantom Hub in contract with 0 mapped source columns in Graph B / triage ledger.
      - `DV-G4-002`: Unaccounted Graph B source column missing from `column_triage_ledger`.
      - `SPLIT_REQUIRED` (`DV-G4-003` / Trap 3): Satellite mixing `PII_HIGH` with non-PII columns, or mixing
        high-frequency (`drift_rate_lambda >= 0.5`) and static (`drift_rate_lambda <= 0.05`) columns when
        daily write amplification exceeds `write_amp_threshold_mb_per_day` (50.0 MB/day).
    """
    violations: list[str] = []

    # Build full Graph B column inventory
    graph_b_cols: dict[tuple[str, str], dict[str, Any]] = {}
    table_telemetry: dict[str, dict[str, Any]] = {}
    for ss in graph_b.get("source_systems", []):
        for tbl in ss.get("tables", []):
            b_table = tbl["bronze_table"]
            table_telemetry[b_table] = tbl.get("yagni_telemetry", {})
            for col in tbl.get("columns", []):
                graph_b_cols[(b_table, col["column_name"])] = col

    # 1. Check 100% column triage completeness (DV-G4-002)
    ledger_entries = contract.get("column_triage_ledger", [])
    ledger_col_set = {(e["bronze_table"], e["column_name"]) for e in ledger_entries}
    missing_cols = sorted(set(graph_b_cols.keys()) - ledger_col_set)
    for b_table, col_name in missing_cols:
        violations.append(
            f"DV-G4-002:Unaccounted Graph B source column '{b_table}.{col_name}' missing from column_triage_ledger"
        )

    # 2. Check for Phantom Unmapped Hubs (DV-G4-001)
    ledger_targets = {e["target_dv_entity"] for e in ledger_entries}
    for hub in contract.get("hubs", []):
        h_name = hub["name"]
        bk_sources = hub.get("business_key_sources", [])
        has_valid_graph_b_col = any(
            (src.get("bronze_table"), col) in graph_b_cols
            for src in bk_sources
            for col in src.get("key_columns", [])
        )
        if not bk_sources or h_name not in ledger_targets or not has_valid_graph_b_col:
            violations.append(
                f"DV-G4-001:Phantom Hub '{h_name}' has no mapped source columns in Graph B"
            )

    # 3. Check Satellites for mixed classification or unguarded mixed cadence (SPLIT_REQUIRED / DV-G4-003)
    for sat in contract.get("satellites", []):
        s_name = sat["name"]
        b_table = sat.get("source_bronze_table", "")
        payload_cols = sat.get("payload_columns", [])

        sec_tags: set[str] = set()
        lambdas: list[float] = []
        for col_name in payload_cols:
            col_meta = graph_b_cols.get((b_table, col_name), {})
            sec_tag = col_meta.get("security_tag", sat.get("security_classification", "INTERNAL"))
            sec_tags.add("PII_HIGH" if sec_tag == "PII_HIGH" else "NON_PII")
            if "drift_rate_lambda" in col_meta:
                lambdas.append(float(col_meta["drift_rate_lambda"]))

        if "PII_HIGH" in sec_tags and "NON_PII" in sec_tags:
            violations.append(
                f"SPLIT_REQUIRED:DV-G4-003:Satellite '{s_name}' mixes PII_HIGH and non-PII columns {payload_cols}"
            )

        if lambdas and (max(lambdas) - min(lambdas) >= 0.5):
            telemetry = table_telemetry.get(b_table, {})
            write_amp = float(
                sat.get(
                    "write_amplification_mb_per_day",
                    telemetry.get("write_amplification_mb_per_day", 0.0),
                )
            )
            if write_amp >= write_amp_threshold_mb_per_day:
                violations.append(
                    f"SPLIT_REQUIRED:DV-G4-003:Satellite '{s_name}' mixes high/low cadence columns with write_amp={write_amp:.1f} MB/day >= {write_amp_threshold_mb_per_day:.1f} MB/day"
                )

    return violations


def verify_algorithm_a6_sqlglot_ast_linter(
    sql_text: str,
    *,
    dialect: str = "duckdb",
) -> list[str]:
    """Algorithm A.6: Parse Staging & Raw Vault SQL into a `sqlglot` AST and enforce DV 2.0 invariants.

    Detects:
      - `DV-G6-001`: Nondeterministic runtime functions (`NOW()`, `CURRENT_TIMESTAMP`, `RANDOM()`, `UUID()`).
      - `DV-G6-002`: Destructive physical mutations (`DELETE`, `UPDATE`, `TRUNCATE`, `DROP`, `ALTER`, `MERGE ... UPDATE/DELETE`).
      - `DV-G6-003`: Wildcard `SELECT *` projection.
      - `DV-G6-004`: Satellite `INSERT INTO ... sat_*` load missing `hash_diff` change comparison in `WHERE`.
      - `DV-G6-005`: Raw Vault (`hub_*`, `lnk_*`, `sat_*`) load containing `GROUP BY` or `HAVING` aggregations.
    """
    violations: list[str] = []
    statements = sqlglot.parse(sql_text, read=dialect)

    FORBIDDEN_DML_TYPES = tuple(
        t
        for t in (
            getattr(exp, "Update", None),
            getattr(exp, "Delete", None),
            getattr(exp, "TruncateTable", None),
            getattr(exp, "Drop", None),
            getattr(exp, "Alter", None),
        )
        if t is not None
    )

    NONDETERMINISTIC_EXP_TYPES = tuple(
        t
        for t in (
            getattr(exp, "CurrentTimestamp", None),
            getattr(exp, "CurrentDate", None),
            getattr(exp, "CurrentTime", None),
            getattr(exp, "Rand", None),
            getattr(exp, "Uuid", None),
        )
        if t is not None
    )
    NONDETERMINISTIC_FN_NAMES = {
        "NOW",
        "CURRENT_TIMESTAMP",
        "SYSDATE",
        "GETDATE",
        "RANDOM",
        "RAND",
        "UUID",
        "GEN_RANDOM_UUID",
    }

    for stmt in statements:
        if stmt is None:
            continue

        # 1. Check destructive DML (DV-G6-002)
        if isinstance(stmt, FORBIDDEN_DML_TYPES) or any(
            isinstance(node, FORBIDDEN_DML_TYPES) for node in stmt.walk()
        ):
            violations.append(f"DV-G6-002:Forbidden destructive statement '{stmt.key.upper()}'")

        if isinstance(stmt, exp.Merge):
            stmt_sql_upper = stmt.sql(dialect=dialect).upper()
            if "WHEN MATCHED" in stmt_sql_upper and ("UPDATE" in stmt_sql_upper or "DELETE" in stmt_sql_upper):
                violations.append("DV-G6-002:Forbidden MERGE WHEN MATCHED UPDATE/DELETE")

        # 2. Check nondeterministic functions (DV-G6-001)
        for node in stmt.walk():
            if isinstance(node, NONDETERMINISTIC_EXP_TYPES):
                violations.append(f"DV-G6-001:Nondeterministic expression '{node.sql(dialect=dialect)}'")
            elif isinstance(node, exp.Anonymous) and node.name.upper() in NONDETERMINISTIC_FN_NAMES:
                violations.append(f"DV-G6-001:Nondeterministic function '{node.name.upper()}()'")
            elif isinstance(node, exp.Func) and node.sql_name().upper() in NONDETERMINISTIC_FN_NAMES:
                violations.append(f"DV-G6-001:Nondeterministic function '{node.sql_name().upper()}()'")
            elif isinstance(node, exp.Column) and node.name.upper() == "CURRENT_TIMESTAMP" and not node.table:
                violations.append("DV-G6-001:Nondeterministic token 'CURRENT_TIMESTAMP'")

        # 3. Check SELECT * wildcard projections (DV-G6-003)
        for select_node in stmt.find_all(exp.Select):
            for proj in select_node.expressions:
                if isinstance(proj, exp.Star) or (
                    isinstance(proj, exp.Column) and isinstance(proj.this, exp.Star)
                ):
                    violations.append("DV-G6-003:Wildcard 'SELECT *' is forbidden in Staging and Vault SQL")

        # 4. Check Satellite INSERT ... SELECT has hash_diff change comparison (DV-G6-004)
        #    and Raw Vault purity against GROUP BY / HAVING aggregations (DV-G6-005)
        if isinstance(stmt, exp.Insert):
            target_table_node = stmt.this.find(exp.Table) if stmt.this else None
            target_name = target_table_node.name.lower() if target_table_node else ""
            is_raw_vault_target = target_name.startswith(("hub_", "lnk_", "sat_", "sal_", "nh_lnk_"))
            is_sat_target = target_name.startswith(("sat_", "bsat_", "mas_", "ma_sat_"))
            select_query = stmt.expression
            if is_raw_vault_target and isinstance(select_query, exp.Select):
                if select_query.args.get("group") is not None or select_query.args.get("having") is not None:
                    violations.append(
                        f"DV-G6-005:Raw Vault load into '{target_name}' contains forbidden GROUP BY / HAVING aggregation"
                    )
            if is_sat_target and isinstance(select_query, exp.Select):
                # Exempt static 1-row SYSTEM_GHOST seed inserts that have no FROM table
                has_from_clause = select_query.args.get("from") is not None or select_query.args.get("from_") is not None
                if has_from_clause:
                    where_node = select_query.args.get("where")
                    where_sql = where_node.sql(dialect=dialect).lower() if where_node else ""
                    if "hash_diff" not in where_sql:
                        violations.append(
                            f"DV-G6-004:Satellite load into '{target_name}' is missing HASH_DIFF comparison in WHERE clause"
                        )

    return violations


def codemod_1_extract_hub_mutable_attributes(
    contract: dict[str, Any],
    graph_b: dict[str, Any],
) -> dict[str, Any]:
    """Algorithm A.7 / Codemod 1: Deterministically extract mutable attributes from Hubs into attached Satellites.

    Idempotent transformation: running Codemod 1 twice yields an identical contract dict.
    """
    repaired = copy.deepcopy(contract)

    col_profile_map: dict[tuple[str, str], dict[str, Any]] = {}
    for ss in graph_b.get("source_systems", []):
        for tbl in ss.get("tables", []):
            for col in tbl.get("columns", []):
                col_profile_map[(tbl["bronze_table"], col["column_name"])] = col

    IMMUTABLE_BK_ROLES = {
        "TIER_1_BUSINESS_KEY",
        "TIER_2_BUSINESS_KEY",
        "TIER_3_LOCAL_SCOPED_KEY",
        "BUSINESS_KEY",
    }

    for hub in repaired.get("hubs", []):
        hub_name = hub["name"]
        extracted_cols: list[tuple[str, str]] = []

        # Extract any explicit mutable_columns / extra_columns
        for field in ("mutable_columns", "descriptive_attributes", "extra_columns"):
            if field in hub:
                items = hub.pop(field)
                default_table = (
                    hub["business_key_sources"][0]["bronze_table"]
                    if hub.get("business_key_sources")
                    else ""
                )
                for item in items:
                    col_name = item["name"] if isinstance(item, dict) else str(item)
                    extracted_cols.append((default_table, col_name))

        # Inspect business_key_sources for non-BK or mutable columns
        for bk_src in hub.get("business_key_sources", []):
            b_table = bk_src["bronze_table"]
            clean_keys: list[str] = []
            for k_col in bk_src.get("key_columns", []):
                col_meta = col_profile_map.get((b_table, k_col), {})
                role = col_meta.get("candidate_role") or col_meta.get("column_role", "MUTABLE_STATUS")
                drift = col_meta.get("drift_rate_lambda", 0.0)
                if role in IMMUTABLE_BK_ROLES and drift == 0.0:
                    clean_keys.append(k_col)
                else:
                    extracted_cols.append((b_table, k_col))
            bk_src["key_columns"] = clean_keys

        # Route extracted mutable columns to the primary Satellite attached to this Hub
        if extracted_cols:
            target_sat = next(
                (
                    s
                    for s in repaired.get("satellites", [])
                    if s.get("parent_entity") == hub_name
                    and s.get("security_classification") in {"PUBLIC", "INTERNAL"}
                ),
                None,
            )
            if target_sat is None:
                b_table = extracted_cols[0][0]
                target_sat = {
                    "name": f"sat_{hub_name.removeprefix('hub_')}_core",
                    "satellite_type": "STANDARD_SATELLITE",
                    "parent_entity": hub_name,
                    "source_bronze_table": b_table,
                    "security_classification": "INTERNAL",
                    "volatility_tier": "STANDARD",
                    "payload_columns": [],
                }
                repaired.setdefault("satellites", []).append(target_sat)

            existing_payload = list(target_sat.get("payload_columns", []))
            for b_table, col_name in extracted_cols:
                if col_name not in existing_payload:
                    existing_payload.append(col_name)
                # Update column_triage_ledger
                for entry in repaired.get("column_triage_ledger", []):
                    if entry.get("column_name") == col_name and entry.get("target_dv_entity") == hub_name:
                        entry["elm_classification"] = "DESCRIPTIVE_PAYLOAD"
                        entry["target_dv_entity"] = target_sat["name"]

            target_sat["payload_columns"] = sorted(existing_payload)

    return repaired


def codemod_2_split_satellite_by_cadence_and_classification(
    contract: dict[str, Any],
    graph_b: dict[str, Any],
    *,
    write_amp_threshold_mb_per_day: float = 50.0,
) -> dict[str, Any]:
    """Algorithm A.8 / Codemod 2: Deterministically split Satellites by `security_classification` (`PII_HIGH`)
    and by cadence (`HIGH_FREQUENCY` vs `STATIC` when write amplification >= 50 MB/day).

    Idempotent transformation: running Codemod 2 twice yields an identical contract dict.
    """
    repaired = copy.deepcopy(contract)

    col_profile_map: dict[tuple[str, str], dict[str, Any]] = {}
    table_telemetry: dict[str, dict[str, Any]] = {}
    for ss in graph_b.get("source_systems", []):
        for tbl in ss.get("tables", []):
            b_table = tbl["bronze_table"]
            table_telemetry[b_table] = tbl.get("yagni_telemetry", {})
            for col in tbl.get("columns", []):
                col_profile_map[(b_table, col["column_name"])] = col

    new_satellites: list[dict[str, Any]] = []
    ledger_updates: dict[tuple[str, str], str] = {}

    for sat in repaired.get("satellites", []):
        s_name = sat["name"]
        b_table = sat.get("source_bronze_table", "")
        payload_cols = list(sat.get("payload_columns", []))

        pii_cols: list[str] = []
        non_pii_cols: list[str] = []
        for col_name in payload_cols:
            col_meta = col_profile_map.get((b_table, col_name), {})
            sec_tag = col_meta.get("security_tag", sat.get("security_classification", "INTERNAL"))
            if sec_tag == "PII_HIGH":
                pii_cols.append(col_name)
            else:
                non_pii_cols.append(col_name)

        # Case 1: Mixed PII_HIGH + non-PII -> split into core + _pii satellite
        if pii_cols and non_pii_cols:
            sat["payload_columns"] = sorted(non_pii_cols)
            if sat.get("security_classification") == "PII_HIGH":
                sat["security_classification"] = "INTERNAL"

            base_stem = s_name.removesuffix("_investran").removesuffix("_core")
            pii_sat_name = f"{base_stem}_pii" if not base_stem.endswith("_pii") else s_name
            pii_sat = {
                "name": pii_sat_name,
                "satellite_type": sat.get("satellite_type", "STANDARD_SATELLITE"),
                "parent_entity": sat["parent_entity"],
                "source_bronze_table": b_table,
                "security_classification": "PII_HIGH",
                "volatility_tier": sat.get("volatility_tier", "STATIC"),
                "payload_columns": sorted(pii_cols),
            }
            new_satellites.append(sat)
            new_satellites.append(pii_sat)
            for c in pii_cols:
                ledger_updates[(b_table, c)] = pii_sat_name
            for c in non_pii_cols:
                ledger_updates[(b_table, c)] = s_name
            continue

        # Case 2: Mixed cadence exceeding Gate 4 write-amplification threshold
        telemetry = table_telemetry.get(b_table, {})
        write_amp = float(
            sat.get(
                "write_amplification_mb_per_day",
                telemetry.get("write_amplification_mb_per_day", 0.0),
            )
        )
        fast_cols: list[str] = []
        slow_cols: list[str] = []
        for col_name in payload_cols:
            col_meta = col_profile_map.get((b_table, col_name), {})
            lam = float(col_meta.get("drift_rate_lambda", 0.0))
            if lam >= 0.5:
                fast_cols.append(col_name)
            else:
                slow_cols.append(col_name)

        if fast_cols and slow_cols and write_amp >= write_amp_threshold_mb_per_day:
            sat["payload_columns"] = sorted(slow_cols)
            sat["volatility_tier"] = "STATIC"
            sat.pop("write_amplification_mb_per_day", None)

            fast_sat_name = f"{s_name}_fast"
            fast_sat = {
                "name": fast_sat_name,
                "satellite_type": sat.get("satellite_type", "STANDARD_SATELLITE"),
                "parent_entity": sat["parent_entity"],
                "source_bronze_table": b_table,
                "security_classification": sat.get("security_classification", "INTERNAL"),
                "volatility_tier": "HIGH_FREQUENCY",
                "payload_columns": sorted(fast_cols),
            }
            new_satellites.append(sat)
            new_satellites.append(fast_sat)
            for c in fast_cols:
                ledger_updates[(b_table, c)] = fast_sat_name
            for c in slow_cols:
                ledger_updates[(b_table, c)] = s_name
            continue

        sat["payload_columns"] = sorted(payload_cols)
        new_satellites.append(sat)

    repaired["satellites"] = new_satellites
    for entry in repaired.get("column_triage_ledger", []):
        key = (entry["bronze_table"], entry["column_name"])
        if key in ledger_updates:
            entry["target_dv_entity"] = ledger_updates[key]

    return repaired


# ============================================================================
# 1. Algorithm A.1 Tests (ELM-to-DV2 Folding & Grain Check)
# ============================================================================


def test_algorithm_a1_positive_pe_graph_a_and_b_folding(
    graph_a: dict[str, Any],
    graph_b: dict[str, Any],
    pe_contract: dict[str, Any],
) -> None:
    """Algorithm A.1 must pass with 0 violations on the canonical Private Equity model."""
    violations = verify_algorithm_a1_elm_folding_and_grain(graph_a, graph_b, pe_contract)
    assert violations == []


def test_algorithm_a1_negative_trap1_mutable_deal_stage_in_hub(
    graph_a: dict[str, Any],
    graph_b: dict[str, Any],
    pe_contract: dict[str, Any],
) -> None:
    """Trap 1: Placing mutable `deal_stage` in `hub_portfolio_company` must trigger `DV-G2-002`."""
    corrupted_b = copy.deepcopy(graph_b)
    corrupted_contract = copy.deepcopy(pe_contract)

    # Add deal_stage (MUTABLE_STATUS) to dealcloud.src_portfolio_companies and place in hub_portfolio_company
    portco_table = corrupted_b["source_systems"][1]["tables"][0]
    portco_table["columns"].append(
        {
            "column_name": "deal_stage",
            "data_type": "VARCHAR(32)",
            "drift_rate_lambda": 0.35,
            "candidate_role": "MUTABLE_STATUS",
        }
    )
    corrupted_contract["hubs"][2]["business_key_sources"][0]["key_columns"].append("deal_stage")
    corrupted_contract["column_triage_ledger"].append(
        {
            "bronze_table": "dealcloud.src_portfolio_companies",
            "column_name": "deal_stage",
            "elm_classification": "MUTABLE_STATUS",
            "target_dv_entity": "hub_portfolio_company",
        }
    )

    violations = verify_algorithm_a1_elm_folding_and_grain(
        graph_a, corrupted_b, corrupted_contract
    )
    assert any("DV-G2-002" in v and "deal_stage" in v for v in violations)


def test_algorithm_a1_negative_trap2_two_way_link_for_three_way_co_invest_grain(
    graph_a: dict[str, Any],
    graph_b: dict[str, Any],
    pe_contract: dict[str, Any],
) -> None:
    """Trap 2: Modeling 3-way co-investment grain with a 2-way Link must trigger `DV-G2-001` / `AP-06`."""
    corrupted_contract = copy.deepcopy(pe_contract)
    # Collapse lnk_lp_commitment_co_invest from 3 hubs down to 2 hubs (dropping hub_portfolio_company)
    co_invest_lnk = next(
        l for l in corrupted_contract["links"] if l["name"] == "lnk_lp_commitment_co_invest"
    )
    co_invest_lnk["hub_references"] = co_invest_lnk["hub_references"][:2]

    violations = verify_algorithm_a1_elm_folding_and_grain(
        graph_a, graph_b, corrupted_contract
    )
    assert any("DV-G2-001" in v and "AP-06" in v for v in violations)


# ============================================================================
# 2. Gate 3 Tests (NetworkX MultiDiGraph Topology Verifier)
# ============================================================================


def test_gate3_positive_pe_canonical_ir_and_6h_4l_9s_benchmark(
    pe_contract: dict[str, Any],
    pe_ir: dict[str, Any],
) -> None:
    """Gate 3 must pass on both the 6-Hub/4-Link/9-Satellite benchmark and `pe_canonical_ir.json`."""
    # Verify physical PE Contract & Canonical IR
    g_contract, v_contract = verify_gate3_networkx_topology(pe_contract)
    assert v_contract == []
    assert nx.is_directed_acyclic_graph(g_contract)

    g_ir, v_ir = verify_gate3_networkx_topology(pe_ir)
    assert v_ir == []
    assert nx.is_directed_acyclic_graph(g_ir)
    assert len(pe_ir["hubs"]) == 4
    assert len(pe_ir["links"]) == 5
    assert len(pe_ir["satellites"]) == 11

    # Verify the 6-Hub, 4-Link, 9-Satellite Private Equity domain topology benchmark
    pe_6h_4l_9s_model = {
        "hubs": [
            {"name": "hub_fund"},
            {"name": "hub_limited_partner"},
            {"name": "hub_portfolio_company"},
            {"name": "hub_gp_entity"},
            {"name": "hub_investment_vehicle"},
            {"name": "hub_document"},
        ],
        "links": [
            {
                "name": "lnk_lp_commitment",
                "hub_references": [{"target_hub": "hub_fund"}, {"target_hub": "hub_limited_partner"}],
            },
            {
                "name": "lnk_lp_commitment_co_invest",
                "hub_references": [
                    {"target_hub": "hub_fund"},
                    {"target_hub": "hub_limited_partner"},
                    {"target_hub": "hub_portfolio_company"},
                ],
            },
            {
                "name": "lnk_deal_investment",
                "hub_references": [
                    {"target_hub": "hub_fund"},
                    {"target_hub": "hub_investment_vehicle"},
                    {"target_hub": "hub_portfolio_company"},
                ],
            },
            {
                "name": "lnk_document_lp_commitment",
                "hub_references": [
                    {"target_hub": "hub_document"},
                    {"target_hub": "hub_fund"},
                    {"target_hub": "hub_limited_partner"},
                ],
            },
        ],
        "satellites": [
            {"name": "sat_fund_investran", "parent_entity": "hub_fund"},
            {"name": "sat_fund_investran_esg", "parent_entity": "hub_fund"},
            {"name": "sat_limited_partner_investran", "parent_entity": "hub_limited_partner"},
            {"name": "sat_limited_partner_pii", "parent_entity": "hub_limited_partner"},
            {"name": "sat_limited_partner_legacy", "parent_entity": "hub_limited_partner"},
            {"name": "sat_lp_commitment_investran", "parent_entity": "lnk_lp_commitment"},
            {"name": "sat_portfolio_company_dealcloud", "parent_entity": "hub_portfolio_company"},
            {"name": "sat_portco_valuation_mnpi", "parent_entity": "hub_portfolio_company"},
            {"name": "sat_document_manifest", "parent_entity": "hub_document"},
        ],
    }
    g_bench, v_bench = verify_gate3_networkx_topology(pe_6h_4l_9s_model)
    assert v_bench == []
    assert len(pe_6h_4l_9s_model["hubs"]) == 6
    assert len(pe_6h_4l_9s_model["links"]) == 4
    assert len(pe_6h_4l_9s_model["satellites"]) == 9
    assert g_bench.number_of_nodes() == 19


@pytest.mark.parametrize(
    ("mutation_name", "mutator", "expected_code"),
    [
        (
            "direct_hub_to_hub_fk",
            lambda m: m["hubs"][0].update({"parent_hubs": ["hub_limited_partner"]}),
            "DV-G3-001",
        ),
        (
            "link_with_fewer_than_two_hubs",
            lambda m: m["links"][0].update({"hub_references": [{"target_hub": "hub_fund"}]}),
            "DV-G3-002",
        ),
        (
            "satellite_with_zero_parents",
            lambda m: m["satellites"][0].update({"parent_entity": ""}),
            "DV-G3-003",
        ),
        (
            "satellite_with_two_parents",
            lambda m: m["satellites"][0].update({"additional_parents": ["hub_limited_partner"]}),
            "DV-G3-003",
        ),
        (
            "link_to_link_without_feature_flag",
            lambda m: m["links"][0].update({"link_references": ["lnk_lp_commitment_co_invest"]}),
            "DV-G3-004",
        ),
        (
            "directed_cycle_in_topology",
            lambda m: (
                m["links"][0].update({"link_references": ["lnk_lp_commitment_co_invest"]}),
                m["links"][1].update({"link_references": ["lnk_lp_commitment"]}),
            ),
            "DV-G3-005",
        ),
    ],
)
def test_gate3_negative_topology_violations(
    pe_contract: dict[str, Any],
    mutation_name: str,
    mutator: Any,
    expected_code: str,
) -> None:
    """Gate 3 must detect DV-G3-001 through DV-G3-005 on adversarial MultiDiGraph mutations."""
    mutated = copy.deepcopy(pe_contract)
    mutator(mutated)
    _, violations = verify_gate3_networkx_topology(mutated, allow_link_to_link=False)
    assert any(expected_code in v for v in violations), (
        f"Expected {expected_code} for {mutation_name}, got {violations}"
    )


def test_gate3_link_to_link_allowed_only_with_feature_flag(pe_contract: dict[str, Any]) -> None:
    """When `allow_link_to_link=True` is set and acyclic, a Link-to-Link edge passes DV-G3-004."""
    mutated = copy.deepcopy(pe_contract)
    mutated["links"][0]["link_references"] = ["lnk_lp_commitment_co_invest"]
    _, v_disabled = verify_gate3_networkx_topology(mutated, allow_link_to_link=False)
    _, v_enabled = verify_gate3_networkx_topology(mutated, allow_link_to_link=True)
    assert any("DV-G3-004" in v for v in v_disabled)
    assert v_enabled == []


# ============================================================================
# 3. Algorithm A.4 Tests (YAGNI & 100% Column Triage Completeness)
# ============================================================================


def test_algorithm_a4_positive_67_of_67_column_triage_completeness(
    graph_b: dict[str, Any],
    pe_contract: dict[str, Any],
) -> None:
    """Verify all 67/67 Graph B source columns are accounted for in `dv_pe_model_contract.yaml`."""
    total_graph_b_cols = sum(
        len(tbl["columns"])
        for ss in graph_b["source_systems"]
        for tbl in ss.get("tables", [])
    )
    assert total_graph_b_cols == 67
    assert len(pe_contract["column_triage_ledger"]) == 67

    violations = verify_algorithm_a4_yagni_and_column_triage(graph_b, pe_contract)
    assert violations == []


def test_algorithm_a4_negative_phantom_unmapped_hub_triggers_dv_g4_001(
    graph_b: dict[str, Any],
    pe_contract: dict[str, Any],
) -> None:
    """Adding an unmapped ontology Hub (`hub_custodian_bank`) with 0 Graph B columns triggers `DV-G4-001`."""
    mutated = copy.deepcopy(pe_contract)
    mutated["hubs"].append(
        {
            "name": "hub_custodian_bank",
            "ontology_concept_uri": "urn:ontology:entity:custodian_bank",
            "business_key_sources": [],
        }
    )
    violations = verify_algorithm_a4_yagni_and_column_triage(graph_b, mutated)
    assert any("DV-G4-001" in v and "hub_custodian_bank" in v for v in violations)


def test_algorithm_a4_negative_unaccounted_source_column_triggers_dv_g4_002(
    graph_b: dict[str, Any],
    pe_contract: dict[str, Any],
) -> None:
    """Dropping a Graph B column from `column_triage_ledger` triggers `DV-G4-002`."""
    mutated = copy.deepcopy(pe_contract)
    removed_entry = mutated["column_triage_ledger"].pop()
    violations = verify_algorithm_a4_yagni_and_column_triage(graph_b, mutated)
    assert any(
        "DV-G4-002" in v and removed_entry["column_name"] in v for v in violations
    )


def test_algorithm_a4_negative_mixed_pii_and_mixed_cadence_satellite_triggers_split_required(
    graph_b: dict[str, Any],
    pe_contract: dict[str, Any],
) -> None:
    """Trap 3: Mixing PII_HIGH with INTERNAL columns or high-write-amp cadence triggers `SPLIT_REQUIRED`."""
    # Case A: Merge PII columns (`wire_iban`, `tax_id`) into `sat_limited_partner_investran`
    mutated_pii = copy.deepcopy(pe_contract)
    mutated_pii["satellites"] = [
        s for s in mutated_pii["satellites"] if s["name"] != "sat_limited_partner_pii"
    ]
    lp_sat = next(
        s for s in mutated_pii["satellites"] if s["name"] == "sat_limited_partner_investran"
    )
    lp_sat["payload_columns"].extend(["wire_iban", "tax_id"])

    v_pii = verify_algorithm_a4_yagni_and_column_triage(graph_b, mutated_pii)
    assert any("SPLIT_REQUIRED" in v and "sat_limited_partner_investran" in v for v in v_pii)

    # Case B: High write-amplification (125.0 MB/day >= 50.0 MB/day) on mixed-cadence `sat_lp_commitment_investran`
    mutated_cadence = copy.deepcopy(pe_contract)
    comm_sat = next(
        s for s in mutated_cadence["satellites"] if s["name"] == "sat_lp_commitment_investran"
    )
    comm_sat["write_amplification_mb_per_day"] = 125.0
    v_cadence = verify_algorithm_a4_yagni_and_column_triage(graph_b, mutated_cadence)
    assert any("SPLIT_REQUIRED" in v and "sat_lp_commitment_investran" in v for v in v_cadence)


# ============================================================================
# 4. Algorithm A.6 Tests (`sqlglot` AST Linter for Staging & Vault SQL)
# ============================================================================


def test_algorithm_a6_positive_staging_hub_and_satellite_sql() -> None:
    """Valid deterministic Staging, Hub, and Satellite SQL must pass Algorithm A.6 with 0 violations."""
    valid_sql = """
    CREATE VIEW staging.stg_investran_pe_funds AS
    SELECT
        staging.dv_bk(['GLOBAL', fund_code]) AS hk_fund,
        staging.dv_pl([deprecated_fax_no, fund_name, isin, CAST(mgmt_fee_bps AS VARCHAR), CAST(target_size_usd AS VARCHAR), CAST(vintage_year AS VARCHAR)]) AS hash_diff_sat_fund_investran,
        UPPER(TRIM(fund_code)) AS fund_code,
        TRIM(fund_name) AS fund_name,
        batch_load_dts AS load_dts,
        'INVESTRAN_FUND_ADMIN' AS rec_src
    FROM bronze.src_pe_funds;

    INSERT INTO raw_vault.hub_fund (hk_fund, bkcc, fund_code, load_dts, rec_src)
    SELECT stg.hk_fund, 'GLOBAL', stg.fund_code, stg.load_dts, stg.rec_src
    FROM staging.stg_investran_pe_funds stg
    WHERE NOT EXISTS (
        SELECT 1 FROM raw_vault.hub_fund h WHERE h.hk_fund = stg.hk_fund
    );

    INSERT INTO raw_vault.sat_fund_investran (hk_fund, load_dts, hash_diff, fund_name, rec_src)
    SELECT stg.hk_fund, stg.load_dts, stg.hash_diff_sat_fund_investran, stg.fund_name, stg.rec_src
    FROM staging.stg_investran_pe_funds stg
    LEFT JOIN (
        SELECT hk_fund, hash_diff
        FROM raw_vault.sat_fund_investran
        QUALIFY ROW_NUMBER() OVER (PARTITION BY hk_fund ORDER BY load_dts DESC) = 1
    ) curr ON curr.hk_fund = stg.hk_fund
    WHERE COALESCE(curr.hash_diff, '') <> stg.hash_diff_sat_fund_investran;
    """
    violations = verify_algorithm_a6_sqlglot_ast_linter(valid_sql)
    assert violations == []


@pytest.mark.parametrize(
    ("bad_sql", "expected_code"),
    [
        (
            "INSERT INTO raw_vault.hub_fund (hk_fund, load_dts, rec_src) SELECT hk_fund, NOW(), rec_src FROM staging.stg_funds;",
            "DV-G6-001",
        ),
        (
            "INSERT INTO raw_vault.hub_fund (hk_fund, load_dts, rec_src) SELECT hk_fund, CURRENT_TIMESTAMP, rec_src FROM staging.stg_funds;",
            "DV-G6-001",
        ),
        (
            "INSERT INTO raw_vault.hub_fund (hk_fund, load_dts, rec_src) SELECT RANDOM(), load_dts, rec_src FROM staging.stg_funds;",
            "DV-G6-001",
        ),
        (
            "UPDATE raw_vault.sat_fund_investran SET load_end_dts = '2026-10-31' WHERE hk_fund = 'abc';",
            "DV-G6-002",
        ),
        (
            "DELETE FROM raw_vault.hub_limited_partner WHERE bkcc = 'LEGACY_GP_ADMIN';",
            "DV-G6-002",
        ),
        (
            "TRUNCATE TABLE raw_vault.sat_lp_commitment_investran;",
            "DV-G6-002",
        ),
        (
            "CREATE VIEW staging.stg_bad AS SELECT * FROM bronze.src_pe_funds;",
            "DV-G6-003",
        ),
        (
            """
            INSERT INTO raw_vault.sat_fund_investran (hk_fund, load_dts, hash_diff, fund_name, rec_src)
            SELECT stg.hk_fund, stg.load_dts, stg.hash_diff, stg.fund_name, stg.rec_src
            FROM staging.stg_investran_pe_funds stg;
            """,
            "DV-G6-004",
        ),
        (
            """
            INSERT INTO raw_vault.hub_fund (hk_fund, bkcc, fund_code, load_dts, rec_src)
            SELECT stg.hk_fund, 'GLOBAL', stg.fund_code, MAX(stg.load_dts), stg.rec_src
            FROM staging.stg_investran_pe_funds stg
            GROUP BY stg.hk_fund, stg.fund_code, stg.rec_src;
            """,
            "DV-G6-005",
        ),
    ],
    ids=[
        "nondeterministic_NOW",
        "nondeterministic_CURRENT_TIMESTAMP",
        "nondeterministic_RANDOM",
        "destructive_UPDATE",
        "destructive_DELETE",
        "destructive_TRUNCATE",
        "wildcard_SELECT_STAR",
        "satellite_missing_HASH_DIFF_comparison",
        "raw_vault_GROUP_BY_aggregation",
    ],
)
def test_algorithm_a6_negative_sql_ast_violations(bad_sql: str, expected_code: str) -> None:
    """Algorithm A.6 must detect DV-G6-001 through DV-G6-005."""
    violations = verify_algorithm_a6_sqlglot_ast_linter(bad_sql)
    assert any(expected_code in v for v in violations), (
        f"Expected {expected_code}, got {violations}"
    )


# ============================================================================
# 5. Algorithms A.1–A.8 Tests (Deterministic Codemods + docs/05 Reference Suite)
# ============================================================================


def test_codemod_1_extract_mutable_from_hub_idempotence_and_gate2_pass(
    graph_a: dict[str, Any],
    graph_b: dict[str, Any],
    pe_contract: dict[str, Any],
) -> None:
    """Codemod 1 must extract mutable `deal_stage` from `hub_portfolio_company` into
    `sat_portfolio_company_dealcloud`, pass Gate 2 (`Algorithm A.1`), and be strictly idempotent.
    """
    corrupted_b = copy.deepcopy(graph_b)
    corrupted_contract = copy.deepcopy(pe_contract)

    corrupted_b["source_systems"][1]["tables"][0]["columns"].append(
        {
            "column_name": "deal_stage",
            "data_type": "VARCHAR(32)",
            "drift_rate_lambda": 0.35,
            "candidate_role": "MUTABLE_STATUS",
        }
    )
    corrupted_contract["hubs"][2]["business_key_sources"][0]["key_columns"].append("deal_stage")
    corrupted_contract["column_triage_ledger"].append(
        {
            "bronze_table": "dealcloud.src_portfolio_companies",
            "column_name": "deal_stage",
            "elm_classification": "MUTABLE_STATUS",
            "target_dv_entity": "hub_portfolio_company",
        }
    )

    # Pre-codemod: Gate 2 fails with DV-G2-002
    pre_violations = verify_algorithm_a1_elm_folding_and_grain(
        graph_a, corrupted_b, corrupted_contract
    )
    assert any("DV-G2-002" in v for v in pre_violations)

    # Run Codemod 1 once
    repaired_once = codemod_1_extract_hub_mutable_attributes(corrupted_contract, corrupted_b)
    # Run Codemod 1 twice (idempotence check)
    repaired_twice = codemod_1_extract_hub_mutable_attributes(repaired_once, corrupted_b)
    assert repaired_once == repaired_twice

    # Verify deal_stage was removed from Hub and placed into sat_portfolio_company_dealcloud
    portco_hub = next(h for h in repaired_once["hubs"] if h["name"] == "hub_portfolio_company")
    assert "deal_stage" not in portco_hub["business_key_sources"][0]["key_columns"]
    portco_sat = next(
        s for s in repaired_once["satellites"] if s["name"] == "sat_portfolio_company_dealcloud"
    )
    assert "deal_stage" in portco_sat["payload_columns"]
    assert portco_sat["payload_columns"] == sorted(portco_sat["payload_columns"])

    # Post-codemod: Gate 2 and Gate 4 pass with 0 violations
    assert verify_algorithm_a1_elm_folding_and_grain(graph_a, corrupted_b, repaired_once) == []
    assert verify_algorithm_a4_yagni_and_column_triage(corrupted_b, repaired_once) == []


def test_codemod_2_split_satellite_by_classification_and_cadence_idempotence_and_gate4_pass(
    graph_b: dict[str, Any],
    pe_contract: dict[str, Any],
) -> None:
    """Codemod 2 must split mixed PII/non-PII and high-write-amp Satellites, pass Gate 4,
    and be strictly idempotent (`f(f(ir)) == f(ir)`).
    """
    corrupted_contract = copy.deepcopy(pe_contract)
    # Merge sat_limited_partner_pii into sat_limited_partner_investran
    corrupted_contract["satellites"] = [
        s for s in corrupted_contract["satellites"] if s["name"] != "sat_limited_partner_pii"
    ]
    lp_sat = next(
        s for s in corrupted_contract["satellites"] if s["name"] == "sat_limited_partner_investran"
    )
    lp_sat["payload_columns"].extend(["wire_iban", "tax_id"])
    for entry in corrupted_contract["column_triage_ledger"]:
        if entry["target_dv_entity"] == "sat_limited_partner_pii":
            entry["target_dv_entity"] = "sat_limited_partner_investran"

    # Pre-codemod: Gate 4 fails with SPLIT_REQUIRED
    pre_violations = verify_algorithm_a4_yagni_and_column_triage(graph_b, corrupted_contract)
    assert any("SPLIT_REQUIRED" in v for v in pre_violations)

    # Run Codemod 2 once
    repaired_once = codemod_2_split_satellite_by_cadence_and_classification(
        corrupted_contract, graph_b
    )
    # Run Codemod 2 twice (idempotence check)
    repaired_twice = codemod_2_split_satellite_by_cadence_and_classification(
        repaired_once, graph_b
    )
    assert repaired_once == repaired_twice

    # Verify sat_limited_partner_pii was split out with sorted PII columns
    sat_by_name = {s["name"]: s for s in repaired_once["satellites"]}
    assert "sat_limited_partner_pii" in sat_by_name
    assert sat_by_name["sat_limited_partner_pii"]["security_classification"] == "PII_HIGH"
    assert sat_by_name["sat_limited_partner_pii"]["payload_columns"] == ["tax_id", "wire_iban"]
    assert "wire_iban" not in sat_by_name["sat_limited_partner_investran"]["payload_columns"]

    # Post-codemod: Gate 3 and Gate 4 pass with 0 violations
    _, g3_violations = verify_gate3_networkx_topology(repaired_once)
    assert g3_violations == []
    assert verify_algorithm_a4_yagni_and_column_triage(graph_b, repaired_once) == []


def test_doc05_reference_algorithms_a1_through_a8_direct_execution(
    doc05_algos: dict[str, Any],
    pe_contract: dict[str, Any],
) -> None:
    """Directly execute the compiled reference Python algorithms A.1–A.9a from `docs/05`."""
    # 1. Algorithm A.1: codemod_split_satellites_3axis
    split_fn = doc05_algos["codemod_split_satellites_3axis"]
    sample_contract = {
        "hubs": [{"name": "hub_lp"}],
        "links": [],
        "satellites": [
            {
                "name": "sat_lp_combined",
                "satellite_type": "STANDARD_SATELLITE",
                "parent_entity": "hub_lp",
                "source_bronze_table": "investran.src_limited_partners",
                "payload_columns": ["lp_name", "tax_id"],
            }
        ],
    }
    bronze_profile = {
        "investran.src_limited_partners": {
            "lp_name": {
                "cdc_lambda_per_day": 0.01,
                "security_classification": "INTERNAL",
                "avg_width_bytes": 64,
            },
            "tax_id": {
                "cdc_lambda_per_day": 0.01,
                "security_classification": "PII_HIGH",
                "avg_width_bytes": 32,
            },
        }
    }
    split_res = split_fn(copy.deepcopy(sample_contract), bronze_profile)
    assert len(split_res["satellites"]) == 2

    # 2. Algorithm A.2: codemod_demote_enum_hubs_to_ref
    demote_fn = doc05_algos["codemod_demote_enum_hubs_to_ref"]
    enum_contract = {
        "hubs": [
            {
                "name": "hub_fund",
                "ontology_concept_uri": "urn:ontology:entity:fund",
                "business_key_sources": [
                    {
                        "bronze_table": "investran.src_pe_funds",
                        "record_source_code": "INVESTRAN",
                        "key_columns": ["fund_code"],
                    }
                ],
            },
            {
                "name": "hub_currency",
                "ontology_concept_uri": "urn:ontology:entity:iso_currency",
                "business_key_sources": [
                    {
                        "bronze_table": "investran.src_currencies",
                        "record_source_code": "INVESTRAN",
                        "key_columns": ["iso_currency_code"],
                    }
                ],
            },
        ],
        "links": [
            {
                "name": "lnk_fund_currency",
                "source_bronze_table": "investran.src_pe_funds",
                "hub_references": [
                    {"target_hub": "hub_fund", "role_alias": "fund", "source_fk_columns": ["fund_code"]},
                    {"target_hub": "hub_currency", "role_alias": "currency", "source_fk_columns": ["iso_currency_code"]},
                ],
            }
        ],
        "satellites": [
            {
                "name": "sat_currency_desc",
                "satellite_type": "STANDARD_SATELLITE",
                "parent_entity": "hub_currency",
                "source_bronze_table": "investran.src_currencies",
                "payload_columns": ["currency_name"],
            }
        ],
        "reference_tables": [],
    }
    demoted_res = demote_fn(
        copy.deepcopy(enum_contract),
        ontology_graph={
            "urn:ontology:entity:fund": {"taxonomy_class": "CORE_ENTITY"},
            "urn:ontology:entity:iso_currency": {"taxonomy_class": "ENUMERATION"},
        },
        bronze_profile={
            "investran.src_pe_funds": {"fund_code": {"distinct_count": 500, "key_growth_30d": 0.1}},
            "investran.src_currencies": {"iso_currency_code": {"distinct_count": 8, "key_growth_30d": 0.0}},
        },
    )
    assert [h["name"] for h in demoted_res["hubs"]] == ["hub_fund"]
    assert demoted_res["reference_tables"][0]["name"] == "ref_currency"
    assert demoted_res["links"] == []

    # 3. Algorithm A.3: codemod_inject_link_effectivity
    eff_fn = doc05_algos["codemod_inject_link_effectivity"]
    lnk_contract = {
        "hubs": [{"name": "hub_lp"}, {"name": "hub_fund"}],
        "links": [
            {
                "name": "lnk_lp_advisor",
                "link_type": "STANDARD_LINK",
                "source_bronze_table": "investran.src_lp_advisor",
                "hub_references": [
                    {"target_hub": "hub_lp", "role_alias": "lp", "source_fk_columns": ["lp_id"]},
                    {"target_hub": "hub_fund", "role_alias": "fund", "source_fk_columns": ["fund_code"]},
                ],
            }
        ],
        "satellites": [],
    }
    eff_res = eff_fn(
        copy.deepcopy(lnk_contract),
        bronze_link_cdc_profile={
            "investran.src_lp_advisor": {
                "lp_id": 0.12,
                "fund_code": 0.0,
            }
        },
    )
    assert any(s["name"] == "esat_lp_advisor" for s in eff_res["satellites"])

    # 4. Algorithm A.4: codemod_enforce_cold_start_extensibility
    cold_fn = doc05_algos["codemod_enforce_cold_start_extensibility"]
    cold_contract = {
        "hubs": [
            {
                "name": "hub_lp",
                "ontology_concept_uri": "urn:ontology:entity:limited_partner",
                "business_key_sources": [
                    {
                        "bronze_table": "investran.src_limited_partners",
                        "record_source_code": "INVESTRAN",
                        "key_columns": ["lp_id"],
                    }
                ],
            }
        ],
        "links": [
            {
                "name": "lnk_lp_commitment",
                "link_type": "STANDARD_LINK",
                "ontology_concept_uri": "urn:ontology:relationship:lp_commitment",
                "source_bronze_table": "investran.src_lp_commitments",
                "hub_references": [
                    {"target_hub": "hub_fund", "role_alias": "fund", "source_fk_columns": ["fund_code"]},
                    {"target_hub": "hub_lp", "role_alias": "lp", "source_fk_columns": ["lp_id"]},
                    {"target_hub": "hub_portfolio_company", "role_alias": "co_invest", "source_fk_columns": ["co_invest_portco_id"]},
                ],
            }
        ],
        "satellites": [],
    }
    cold_res = cold_fn(
        copy.deepcopy(cold_contract),
        ontology_graph={
            "urn:ontology:entity:limited_partner": {"authoritative_sor": []}
        },
        bronze_profile={
            "investran.src_limited_partners": {"lp_id": {"pattern_class": "MONOTONIC_INT"}},
            "investran.src_lp_commitments": {
                "fund_code": {"non_null_ratio": 1.0},
                "lp_id": {"non_null_ratio": 1.0},
                "co_invest_portco_id": {"non_null_ratio": 0.285},
            },
        },
    )
    assert cold_res["hubs"][0]["business_key_sources"][0]["bkcc_prefix"] == "INVESTRAN"
    assert len(cold_res["links"]) >= 2

    # 5. Algorithm A.5: codemod_enforce_staging_hashes_sql
    stg_fn = doc05_algos["codemod_enforce_staging_hashes_sql"]
    rewritten_sql = stg_fn(
        "SELECT fund_code, fund_name, batch_load_dts AS load_dts, 'INVESTRAN' AS rec_src FROM bronze.src_pe_funds",
        hub_keys={"hk_fund": (["fund_code"], "GLOBAL")},
        satellite_hashdiffs={"hash_diff_sat_fund": ["fund_name", "fund_code"]},
        dialect="snowflake",
    )
    assert "SHA2(" in rewritten_sql.upper()
    assert "OCTET_LENGTH(" in rewritten_sql.upper()

    # 6. Algorithm A.6: verify_physical_dv2_sql_ast
    ast_fn = doc05_algos["verify_physical_dv2_sql_ast"]
    ast_errors = ast_fn(
        "hub_fund",
        "SELECT hk_fund, bkcc, fund_code, load_dts, rec_src FROM staging.stg_fund",
        dialect="snowflake",
    )
    assert ast_errors == []

    # 7. Algorithm A.7: verify_additive_evolution_invariants
    evo_fn = doc05_algos["verify_additive_evolution_invariants"]
    old_c = copy.deepcopy(pe_contract)
    new_c_valid = copy.deepcopy(pe_contract)
    new_c_valid["satellites"].append(
        {
            "name": "sat_fund_extra",
            "satellite_type": "STANDARD_SATELLITE",
            "parent_entity": "hub_fund",
            "source_bronze_table": "investran.src_pe_funds",
            "security_classification": "INTERNAL",
            "volatility_tier": "STATIC",
            "payload_columns": ["fund_name"],
        }
    )
    assert evo_fn(old_c, new_c_valid) == []

    # 8. Algorithm A.8: run_axi_dv_verification_pipeline
    pipeline_fn = doc05_algos["run_axi_dv_verification_pipeline"]
    clean_candidate = copy.deepcopy(pe_contract)
    clean_candidate["unmapped_escalations"] = []
    status, transformed, diags = pipeline_fn(
        candidate_contract=clean_candidate,
        ontology_graph={
            h["ontology_concept_uri"]: {
                "taxonomy_class": "CORE_ENTITY",
                "authoritative_sor": ["INVESTRAN_FUND_ADMIN", "DEALCLOUD_CRM"],
            }
            for h in pe_contract["hubs"]
        },
        bronze_profile={},
        bronze_link_cdc_profile={},
        deployed_contract=None,
    )
    assert status == "PASS", f"Expected PASS, got {status}: {diags}"
    assert transformed is not None
    assert any("ALL_GATES_GREEN" in d for d in diags)

    # 9. Algorithm A.9a (schema drift codemod): codemod_intra_source_schema_drift
    drift_fn = doc05_algos["codemod_intra_source_schema_drift"]
    cand_with_new_col = copy.deepcopy(pe_contract)
    cand_with_new_col["satellites"][0]["payload_columns"].append("sfdr_sustainability_tier")
    drift_res = drift_fn(copy.deepcopy(pe_contract), cand_with_new_col, schema_epoch_tag="v2")
    assert any(s["name"].endswith("_ext_v2") for s in drift_res["satellites"])

