"""Gate 1 & Gate 2 Verifier: Contract Schema, Grain Statement (>= 8 words),
100% Column Triage Ledger Coverage, Anti-Silo Ontology Alignment, and Key Discipline.
"""

from __future__ import annotations

from typing import Any

import jsonschema  # type: ignore[import-untyped]

from opendv_core.schema_registry import validate_contract_v5

MIN_GRAIN_WORDS: int = 8


def verify_grain_statement_word_count(
    grain_statement: str | None,
    min_words: int = MIN_GRAIN_WORDS,
) -> bool:
    """Return True if `grain_statement` contains at least `min_words` whitespace-delimited words."""
    if not grain_statement or not isinstance(grain_statement, str):
        return False
    words = [w for w in grain_statement.strip().split() if w]
    return len(words) >= min_words


def _extract_expected_bronze_columns(
    bronze_profile: dict[str, Any] | None,
) -> tuple[set[tuple[str, str]], dict[str, dict[str, dict[str, Any]]], dict[str, dict[str, Any]]]:
    expected_cols: set[tuple[str, str]] = set()
    col_profiles: dict[str, dict[str, dict[str, Any]]] = {}
    table_collisions: dict[str, dict[str, Any]] = {}

    if not bronze_profile or not isinstance(bronze_profile, dict):
        return expected_cols, col_profiles, table_collisions

    if "source_systems" in bronze_profile and isinstance(bronze_profile["source_systems"], list):
        for sys_entry in bronze_profile["source_systems"]:
            if not isinstance(sys_entry, dict):
                continue
            for tbl in sys_entry.get("tables", []):
                if not isinstance(tbl, dict):
                    continue
                b_table = str(tbl.get("bronze_table", ""))
                if not b_table:
                    continue
                if isinstance(tbl.get("collision_diagnostics"), dict):
                    table_collisions[b_table] = dict(tbl["collision_diagnostics"])
                t_map: dict[str, dict[str, Any]] = {}
                for col in tbl.get("columns", []):
                    if isinstance(col, dict) and "column_name" in col:
                        c_name = str(col["column_name"])
                        expected_cols.add((b_table, c_name))
                        t_map[c_name] = dict(col)
                col_profiles[b_table] = t_map
        return expected_cols, col_profiles, table_collisions

    for b_table, raw_tbl in bronze_profile.items():
        if not isinstance(raw_tbl, dict):
            continue
        t_map = {}
        for col_name, metrics in raw_tbl.items():
            if col_name == "yagni_telemetry" or not isinstance(metrics, dict):
                continue
            expected_cols.add((str(b_table), str(col_name)))
            t_map[str(col_name)] = dict(metrics)
        col_profiles[str(b_table)] = t_map

    return expected_cols, col_profiles, table_collisions


def _extract_known_ontology_uris(ontology_graph: dict[str, Any] | None) -> set[str]:
    uris: set[str] = set()
    if not ontology_graph or not isinstance(ontology_graph, dict):
        return uris

    for concept in ontology_graph.get("canonical_business_concepts", []):
        if isinstance(concept, dict) and "concept_uri" in concept:
            uris.add(str(concept["concept_uri"]))
    for rel in ontology_graph.get("canonical_relationships", []):
        if isinstance(rel, dict) and "relationship_uri" in rel:
            uris.add(str(rel["relationship_uri"]))
    for ref_enum in ontology_graph.get("reference_enumerations", []):
        if isinstance(ref_enum, dict) and "enum_uri" in ref_enum:
            uris.add(str(ref_enum["enum_uri"]))
    for k, v in ontology_graph.items():
        if isinstance(v, dict) and str(k).startswith("urn:"):
            uris.add(str(k))
    return uris


def verify_gate1_contract_and_grain(
    contract: dict[str, Any],
    ontology_graph: dict[str, Any] | None = None,
    bronze_profile: dict[str, Any] | None = None,
    *,
    strict_schema: bool = False,
    require_grain_statements: bool = False,
    check_full_ledger_coverage: bool = False,
) -> list[str]:
    """Run Gate 1 & Gate 2 contract, grain statement, column ledger, and key authority checks."""
    diagnostics: list[str] = []

    # 1. JSON Schema Validation (when contract_version in {"5.0", "5.0.0"} or strict_schema=True)
    if strict_schema or str(contract.get("contract_version", "")) in {"5.0", "5.0.0"}:
        try:
            validate_contract_v5(contract)
        except (
            jsonschema.ValidationError,
            jsonschema.SchemaError,
            ValueError,
            TypeError,
            KeyError,
        ) as exc:
            diagnostics.append(f"SCHEMA_ERROR [Gate 1 - DV-G1-001]: {exc}")

    # 2. Grain Statement Word Count (>= 8 words)
    root_grain = contract.get("grain_statement") or contract.get("grain_semantics")
    if root_grain is not None and not verify_grain_statement_word_count(
        str(root_grain), MIN_GRAIN_WORDS
    ):
        diagnostics.append(
            f"SEMANTIC_ERROR [Gate 1 - DV-G1-GRAIN]: Contract root grain_statement "
            f"'{root_grain}' has fewer than {MIN_GRAIN_WORDS} words."
        )

    for section in ("hubs", "links", "satellites"):
        for entity in contract.get(section, []):
            if not isinstance(entity, dict):
                continue
            e_name = str(entity.get("name", "unknown"))
            stmt = entity.get("grain_statement") or entity.get("grain_semantics")
            if stmt is not None:
                if not verify_grain_statement_word_count(str(stmt), MIN_GRAIN_WORDS):
                    diagnostics.append(
                        f"SEMANTIC_ERROR [Gate 1 - DV-G1-GRAIN]: Entity '{e_name}' "
                        f"grain_statement '{stmt}' has fewer than {MIN_GRAIN_WORDS} words."
                    )
            elif require_grain_statements and section in {"hubs", "links"}:
                diagnostics.append(
                    f"SEMANTIC_ERROR [Gate 1 - DV-G1-GRAIN]: Entity '{e_name}' is missing "
                    f"a mandatory grain_statement (>= {MIN_GRAIN_WORDS} words)."
                )

    if isinstance(ontology_graph, dict):
        for rel in ontology_graph.get("canonical_relationships", []):
            if isinstance(rel, dict) and "grain_semantics" in rel:
                rel_stmt = str(rel["grain_semantics"])
                if not verify_grain_statement_word_count(rel_stmt, MIN_GRAIN_WORDS):
                    diagnostics.append(
                        f"SEMANTIC_ERROR [Gate 1 - DV-G1-GRAIN]: Ontology relationship "
                        f"'{rel.get('relationship_uri')}' grain_semantics has fewer than "
                        f"{MIN_GRAIN_WORDS} words."
                    )

    # 3. Anti-Silo Ontology Uniqueness & Zero-Phantom-Entity Check
    ledger = list(contract.get("column_triage_ledger", []))
    active_entities = {
        str(entry.get("target_entity") or entry.get("target_dv_entity") or "")
        for entry in ledger
        if isinstance(entry, dict)
    }
    known_uris = _extract_known_ontology_uris(ontology_graph)

    seen_concepts: dict[str, str] = {}
    for hub in contract.get("hubs", []):
        h_name = str(hub.get("name", ""))
        uri = str(hub.get("ontology_concept_uri", ""))
        if uri in seen_concepts:
            diagnostics.append(
                f"SEMANTIC_ERROR [Gate 1 - ANTI_SILO_VIOLATION]: Both '{seen_concepts[uri]}' "
                f"and '{h_name}' bind to '{uri}'. Consolidate into a single canonical Hub."
            )
        seen_concepts[uri] = h_name

        if known_uris and uri and uri not in known_uris:
            diagnostics.append(
                f"SEMANTIC_ERROR [Gate 1 - DV-G4-001]: Hub '{h_name}' references unknown "
                f"ontology_concept_uri '{uri}'."
            )

        if ledger and h_name not in active_entities:
            diagnostics.append(
                f"SEMANTIC_ERROR [Gate 1 - PHANTOM_HUB_REJECTED]: Hub '{h_name}' has no "
                f"mapped physical columns in column_triage_ledger."
            )

    # 4. 100% Column Triage Ledger Coverage (DV-G4-002)
    expected_cols, col_profiles, table_collisions = _extract_expected_bronze_columns(bronze_profile)
    if check_full_ledger_coverage and expected_cols:
        triaged_cols = {
            (
                str(entry.get("bronze_table") or entry.get("source_table") or ""),
                str(entry.get("column_name", "")),
            )
            for entry in ledger
            if isinstance(entry, dict)
        }
        missing_cols = sorted(expected_cols - triaged_cols)
        if missing_cols:
            formatted = ", ".join(f"{t}.{c}" for t, c in missing_cols[:10])
            diagnostics.append(
                f"SEMANTIC_ERROR [Gate 4 - DV-G4-002]: {len(missing_cols)} physical Bronze "
                f"column(s) missing from column_triage_ledger: {formatted}."
            )

    for entry in ledger:
        if not isinstance(entry, dict):
            continue
        if entry.get("dv_target_role") == "OMITTED_INTENTIONALLY":
            rationale = str(entry.get("disposition_rationale") or "").strip()
            if len(rationale) < 15:
                diagnostics.append(
                    f"SEMANTIC_ERROR [Gate 4 - DV-G4-002]: Column "
                    f"'{entry.get('source_table')}.{entry.get('column_name')}' marked "
                    f"OMITTED_INTENTIONALLY without disposition_rationale (>= 15 chars)."
                )

    # 5. Gate 2 Key Discipline: Surrogate Key Ban, Mutable Hub Key Check, & Tier-3 BKCC Check
    for hub in contract.get("hubs", []):
        h_name = str(hub.get("name", ""))
        hub_just = hub.get("system_scoped_bk_justification")
        for src in hub.get("business_key_sources", []):
            b_table = str(src.get("bronze_table", ""))
            src_just = src.get("system_scoped_bk_justification") or hub_just
            tbl_collision = table_collisions.get(b_table, {})
            src_tier = str(src.get("key_authority_tier") or src.get("bk_authority_tier") or "")

            bkcc_prefix_val = str(src.get("bkcc_prefix") or "").strip()
            bkcc_col_expr_val = str(src.get("bkcc_column_expr") or "").strip()
            if (
                src_tier == "TIER_3_LOCAL_SCOPED"
                or tbl_collision.get("requires_tier3_bkcc_prefix")
                or tbl_collision.get("required_bkcc_prefix")
            ) and (
                not src.get("require_src_qualifier") or not (bkcc_prefix_val or bkcc_col_expr_val)
            ):
                diagnostics.append(
                    f"SEMANTIC_ERROR [Gate 1 - DV-COL-012]: Hub '{h_name}' source "
                    f"'{b_table}' is Tier-3 local-scoped / colliding but lacks "
                    f"require_src_qualifier=true and non-empty bkcc_prefix or bkcc_column_expr."
                )
            elif src_tier == "TIER_1_GLOBAL_STANDARD":
                raw_bkcc = src.get("bkcc_prefix")
                if bool(src.get("require_src_qualifier")) or raw_bkcc != "GLOBAL":
                    diagnostics.append(
                        f"SEMANTIC_ERROR [Gate 1 - DV-COL-013]: Hub '{h_name}' source "
                        f"'{b_table}' is Tier-1 global-standard (TIER_1_GLOBAL_STANDARD) "
                        f"but has require_src_qualifier={src.get('require_src_qualifier')} "
                        f"or non-standard bkcc_prefix={raw_bkcc!r} (expected "
                        f"require_src_qualifier=false and bkcc_prefix='GLOBAL')."
                    )

            for k_col in src.get("key_columns", []):
                prof = col_profiles.get(b_table, {}).get(str(k_col), {})
                pat = str(prof.get("pattern_class", "UNKNOWN"))
                cand_role = str(prof.get("candidate_role", ""))
                if (
                    pat in {"MONOTONIC_INT", "RANDOM_UUID_V4"} or cand_role == "SURROGATE_SEQUENCE"
                ) and not src_just:
                    diagnostics.append(
                        f"SEMANTIC_ERROR [Gate 2 - SURROGATE_KEY_REJECTED]: Hub '{h_name}' "
                        f"uses column '{b_table}.{k_col}' classified as '{pat or cand_role}' "
                        f"without justification. Select a natural business key or provide "
                        f"system_scoped_bk_justification."
                    )
                mut_freq = float(
                    prof.get(
                        "update_frequency_per_row_per_day",
                        prof.get(
                            "mutable_update_freq",
                            prof.get("drift_rate_lambda", prof.get("cdc_lambda_per_day", 0.0)),
                        ),
                    )
                )
                if mut_freq > 0.0 and cand_role in {
                    "SATELLITE_DESCRIPTIVE_ATTRIBUTE",
                    "DESCRIPTIVE_PAYLOAD",
                }:
                    diagnostics.append(
                        f"SEMANTIC_ERROR [Gate 2 - DV-G2-002]: Hub '{h_name}' includes "
                        f"mutable column '{b_table}.{k_col}' (update_freq={mut_freq}) in "
                        f"key_columns."
                    )

    # 6. Gate 2 Nullable Optional FK Check on >= 2-way Links (DV-G2-001 / DV-G2-001b)
    for lnk in contract.get("links", []):
        l_name = str(lnk.get("name", ""))
        refs = list(lnk.get("hub_references", []))
        if len(refs) < 2 or lnk.get("where_filter_condition"):
            continue
        b_table = str(lnk.get("source_bronze_table", ""))
        for ref in refs:
            for fk_col in ref.get("source_fk_columns", []):
                prof = col_profiles.get(b_table, {}).get(str(fk_col), {})
                nn_candidates: list[float] = []
                if prof.get("non_null_ratio") is not None:
                    nn_candidates.append(float(prof["non_null_ratio"]))
                if prof.get("u_non_null") is not None:
                    nn_candidates.append(float(prof["u_non_null"]))
                if prof.get("null_rate") is not None:
                    nn_candidates.append(1.0 - float(prof["null_rate"]))
                non_null = min(nn_candidates) if nn_candidates else None
                if non_null is not None and float(non_null) < 0.95:
                    if len(refs) == 2:
                        diagnostics.append(
                            f"SEMANTIC_ERROR [Gate 2 - DV-G2-001]: Binary Link '{l_name}' "
                            f"includes sparse optional FK '{b_table}.{fk_col}' "
                            f"(U_nn={float(non_null):.3f} < 0.95; DV-G2-001b) without "
                            f"where_filter_condition = '{fk_col} IS NOT NULL'."
                        )
                    else:
                        diagnostics.append(
                            f"SEMANTIC_ERROR [Gate 2 - DV-G2-001]: Link '{l_name}' includes "
                            f"sparse optional FK '{b_table}.{fk_col}' "
                            f"(U_nn={float(non_null):.3f} < 0.95) "
                            f"without splitting into a dedicated filtered Link."
                        )

    return diagnostics
