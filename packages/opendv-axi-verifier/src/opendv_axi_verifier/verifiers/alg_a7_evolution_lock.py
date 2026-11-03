"""Algorithm A.7: `N -> N+1` Additive Evolution Lock Verifier (`DV-EVO-001`-`008`).

Compares a candidate contract/IR `V_{N+1}` against the deployed baseline `V_N`
to guarantee strictly additive, backward-compatible Data Vault 2.0 evolution:
  - `DV-EVO-001` (`HASH_CONFIG_MUTATED`): Global `hash_config` immutability
  - `DV-EVO-002` (`HUB_DELETED` / `LINK_DELETED` / `SATELLITE_DELETED`): Zero entity deletion
  - `DV-EVO-003` (`BK_SIGNATURE_MUTATED` / `BKCC_MUTATED` / `LINK_ARITY_OR_ORDER_MUTATED` /
    `HUB_CONCEPT_DRIFT` / `HUB_SOURCE_REMOVED`): Grain & key signature immutability
  - `DV-EVO-004` (`SAT_COLUMN_DROPPED`): Zero physical column deletion on existing entities
  - `DV-EVO-005` (`DESTRUCTIVE_DML_DETECTED`): Insert-only Raw Vault DDL/DML enforcement
  - `DV-EVO-006` (`SAT_HASHDIFF_INVALIDATED`): Satellite `hash_diff` historical stability
"""

from __future__ import annotations

from typing import Any

import sqlglot
from sqlglot import exp


def _entity_key(entity: dict[str, Any]) -> str:
    return str(entity.get("name") or entity.get("entity_id") or "")


def _ref_tuple(ref: dict[str, Any]) -> tuple[str, str]:
    target = str(ref.get("target_hub") or ref.get("hub_id") or "")
    role = str(ref.get("role_alias") or ref.get("role_name") or "")
    return (target, role)


def verify_additive_evolution_invariants(
    contract_v_prev: dict[str, Any],
    contract_v_next: dict[str, Any],
    sql_scripts: list[str] | None = None,
) -> list[str]:
    """Algorithm A.7: Verify `N -> N+1` additive evolution invariants (`DV-EVO-001`-`008`)."""
    violations: list[str] = []

    # 1. DV-EVO-001: Global Hash Recipe Immutability
    prev_hash_cfg = contract_v_prev.get("hash_config")
    next_hash_cfg = contract_v_next.get("hash_config")
    if prev_hash_cfg is not None and next_hash_cfg is not None and prev_hash_cfg != next_hash_cfg:
        violations.append(
            "BREAKING_EVOLUTION [DV-EVO-001 / HASH_CONFIG_MUTATED]: Global hash_config "
            "mutated between schema epochs."
        )

    # 2. Hub Evolution Invariants (DV-EVO-002, DV-EVO-003, DV-EVO-007)
    hubs_prev = {_entity_key(h): h for h in contract_v_prev.get("hubs", []) if _entity_key(h)}
    hubs_next = {_entity_key(h): h for h in contract_v_next.get("hubs", []) if _entity_key(h)}

    for h_name, h_prev in hubs_prev.items():
        if h_name not in hubs_next:
            violations.append(
                f"BREAKING_EVOLUTION [DV-EVO-002 / HUB_DELETED]: Hub '{h_name}' was deleted."
            )
            continue

        h_next = hubs_next[h_name]
        if (
            "ontology_concept_uri" in h_prev
            and "ontology_concept_uri" in h_next
            and h_prev["ontology_concept_uri"] != h_next["ontology_concept_uri"]
        ):
            violations.append(
                f"BREAKING_EVOLUTION [DV-EVO-003 / HUB_CONCEPT_DRIFT]: Hub '{h_name}' "
                f"changed ontology_concept_uri from '{h_prev['ontology_concept_uri']}' "
                f"to '{h_next['ontology_concept_uri']}'."
            )

        if "business_key_columns" in h_prev or "business_key_columns" in h_next:
            prev_bks = [c["column_name"] for c in h_prev.get("business_key_columns", [])]
            next_bks = [c["column_name"] for c in h_next.get("business_key_columns", [])]
            if prev_bks != next_bks:
                violations.append(
                    f"BREAKING_EVOLUTION [DV-EVO-003 / BK_SIGNATURE_MUTATED]: Hub '{h_name}' "
                    f"business_key_columns mutated from {prev_bks} to {next_bks}."
                )

        if (
            "bkcc_value" in h_prev
            and "bkcc_value" in h_next
            and h_prev.get("bkcc_value") != h_next.get("bkcc_value")
        ):
            violations.append(
                f"BREAKING_EVOLUTION [DV-EVO-007 / BKCC_MUTATED]: Hub '{h_name}' "
                f"bkcc_value mutated."
            )

        src_prev = {
            (str(s["bronze_table"]), str(s["record_source_code"])): s
            for s in h_prev.get("business_key_sources", [])
            if "bronze_table" in s and "record_source_code" in s
        }
        src_next = {
            (str(s["bronze_table"]), str(s["record_source_code"])): s
            for s in h_next.get("business_key_sources", [])
            if "bronze_table" in s and "record_source_code" in s
        }

        for src_key, s_old in src_prev.items():
            if src_key not in src_next:
                violations.append(
                    f"BREAKING_EVOLUTION [DV-EVO-003 / HUB_SOURCE_REMOVED]: Hub '{h_name}' "
                    f"dropped source mapping {src_key}."
                )
                continue
            s_new = src_next[src_key]
            if s_old.get("key_columns") != s_new.get("key_columns"):
                violations.append(
                    f"BREAKING_EVOLUTION [DV-EVO-003 / BK_SIGNATURE_MUTATED]: Hub '{h_name}' "
                    f"source {src_key} mutated key_columns from {s_old.get('key_columns')} "
                    f"to {s_new.get('key_columns')}."
                )
            if s_old.get("bkcc_prefix") != s_new.get("bkcc_prefix"):
                violations.append(
                    f"BREAKING_EVOLUTION [DV-EVO-003 / DV-EVO-007 / BKCC_MUTATED]: Hub "
                    f"'{h_name}' source {src_key} mutated bkcc_prefix from "
                    f"'{s_old.get('bkcc_prefix')}' to '{s_new.get('bkcc_prefix')}'."
                )
            if s_old.get("bkcc_column_expr") != s_new.get("bkcc_column_expr"):
                violations.append(
                    f"BREAKING_EVOLUTION [DV-EVO-003 / DV-EVO-007 / BKCC_MUTATED]: Hub "
                    f"'{h_name}' source {src_key} mutated bkcc_column_expr from "
                    f"'{s_old.get('bkcc_column_expr')}' to '{s_new.get('bkcc_column_expr')}'."
                )

    # 3. Link Evolution Invariants (DV-EVO-002, DV-EVO-003, DV-EVO-004)
    links_prev = {
        _entity_key(lnk): lnk for lnk in contract_v_prev.get("links", []) if _entity_key(lnk)
    }
    links_next = {
        _entity_key(lnk): lnk for lnk in contract_v_next.get("links", []) if _entity_key(lnk)
    }

    for l_name, l_prev in links_prev.items():
        if l_name not in links_next:
            violations.append(
                f"BREAKING_EVOLUTION [DV-EVO-002 / LINK_DELETED]: Link '{l_name}' was deleted."
            )
            continue

        l_next = links_next[l_name]
        refs_prev = [_ref_tuple(r) for r in l_prev.get("hub_references", [])]
        refs_next = [_ref_tuple(r) for r in l_next.get("hub_references", [])]
        if refs_prev != refs_next:
            violations.append(
                f"BREAKING_EVOLUTION [DV-EVO-003 / LINK_ARITY_OR_ORDER_MUTATED]: Link "
                f"'{l_name}' changed hub_references from {refs_prev} to {refs_next}. "
                f"Spin off a new Link instead of mutating an existing Link's grain."
            )

        degen_prev = set(l_prev.get("degenerate_columns", []))
        degen_next = set(l_next.get("degenerate_columns", []))
        if not degen_prev.issubset(degen_next):
            violations.append(
                f"BREAKING_EVOLUTION [DV-EVO-004 / LINK_DEGENERATE_DROPPED]: Link '{l_name}' "
                f"dropped degenerate columns {sorted(degen_prev - degen_next)}."
            )

    # 4. Satellite Evolution Invariants (DV-EVO-002, DV-EVO-003, DV-EVO-004, DV-EVO-006)
    sats_prev = {_entity_key(s): s for s in contract_v_prev.get("satellites", []) if _entity_key(s)}
    sats_next = {_entity_key(s): s for s in contract_v_next.get("satellites", []) if _entity_key(s)}

    for s_name, s_prev in sats_prev.items():
        if s_name not in sats_next:
            violations.append(
                f"BREAKING_EVOLUTION [DV-EVO-002 / SATELLITE_DELETED]: Satellite "
                f"'{s_name}' was deleted."
            )
            continue

        s_next = sats_next[s_name]
        if s_prev.get("child_dependent_keys") != s_next.get("child_dependent_keys") or s_prev.get(
            "sub_sequence_column"
        ) != s_next.get("sub_sequence_column"):
            violations.append(
                f"BREAKING_EVOLUTION [DV-EVO-003 / MAS_CDK_MUTATED]: Satellite '{s_name}' "
                f"mutated its multi-active sub-sequence / child dependent keys."
            )

        cols_prev = set(s_prev.get("payload_columns", []))
        cols_next = set(s_next.get("payload_columns", []))
        soft_deprecated = set(s_next.get("soft_deprecated_columns") or [])

        dropped = cols_prev - (cols_next | soft_deprecated)
        if dropped:
            violations.append(
                f"BREAKING_EVOLUTION [DV-EVO-004 / SAT_COLUMN_DROPPED]: Satellite '{s_name}' "
                f"dropped columns {sorted(dropped)} without soft-deprecation. Keep columns "
                f"nullable and project NULL in staging."
            )

        added_in_place = cols_next - cols_prev
        if added_in_place and not s_next.get("hash_diff_migration_policy"):
            violations.append(
                f"BREAKING_EVOLUTION [DV-EVO-006 / SAT_HASHDIFF_INVALIDATED]: Satellite "
                f"'{s_name}' added columns {sorted(added_in_place)} in-place without an "
                f"explicit hash_diff_migration_policy. Isolate new columns in a companion "
                f"Satellite '{s_name}_ext' or declare a hash_diff_migration_policy."
            )

    # 5. DV-EVO-005: Insert-Only Raw Vault DML Enforcement on Migration SQL
    if sql_scripts:
        for script in sql_scripts:
            upper_sql = script.strip().upper()
            if upper_sql.startswith("TRUNCATE "):
                violations.append(
                    "BREAKING_EVOLUTION [DV-EVO-005 / DESTRUCTIVE_DML_DETECTED]: "
                    "TRUNCATE statement forbidden on Raw Vault."
                )
                continue
            try:
                parsed_list = sqlglot.parse(script)
            except Exception as exc:
                violations.append(f"BREAKING_EVOLUTION [DV-EVO-005 / SQL_PARSE_ERR]: {exc}")
                continue
            for ast in parsed_list:
                if ast is None:
                    continue
                if isinstance(ast, (exp.Update, exp.Delete, exp.Drop)):
                    violations.append(
                        f"BREAKING_EVOLUTION [DV-EVO-005 / DESTRUCTIVE_DML_DETECTED]: "
                        f"Forbidden {ast.key.upper()} statement on Raw Vault."
                    )
                elif isinstance(ast, exp.Command) and "TRUNCATE" in ast.sql().upper():
                    violations.append(
                        "BREAKING_EVOLUTION [DV-EVO-005 / DESTRUCTIVE_DML_DETECTED]: "
                        "Forbidden TRUNCATE statement on Raw Vault."
                    )
                elif isinstance(ast, exp.Merge):
                    sql_u = ast.sql().upper()
                    if "WHEN MATCHED" in sql_u and ("UPDATE" in sql_u or "DELETE" in sql_u):
                        violations.append(
                            "BREAKING_EVOLUTION [DV-EVO-005 / DESTRUCTIVE_DML_DETECTED]: "
                            "MERGE with WHEN MATCHED UPDATE/DELETE is forbidden."
                        )

    return violations


verify_evolution_invariants = verify_additive_evolution_invariants
verify_evolution_lock_alg_a7 = verify_additive_evolution_invariants
