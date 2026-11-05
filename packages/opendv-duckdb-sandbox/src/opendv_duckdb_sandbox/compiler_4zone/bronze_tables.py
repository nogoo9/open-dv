"""Generic Bronze Source Tables and Deterministic Seed Generators."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from opendv_core.hashing import compute_dv2_length_prefixed_sha256
from opendv_core.models.canonical_ir import CanonicalIR, IRHub
from opendv_duckdb_sandbox.compiler_4zone.bronze_seed_resolver import (
    _filter_hub_rows_for_bkcc,
    _format_bronze_payload_sql_literal,
    _load_bronze_table_rows,
    _resolve_hub_bk_rows_from_bronze,
)
from opendv_duckdb_sandbox.compiler_4zone.hashing import (
    _normalize_duckdb_type,
    _sql_lit,
    _validate_ident,
)
from opendv_duckdb_sandbox.compiler_4zone.schema_utils import (
    _effective_hub_bkcc_column_expr,
    _effective_hub_sm_bkcc_literal,
    _find_bitemporal_satellite,
    _satellite_all_data_columns,
    _satellite_seed_sql_literal,
)
from opendv_duckdb_sandbox.compiler_4zone.staging_registry import (
    _table_lookup_keys,
    build_staging_bkcc_registry,
    resolve_staging_bkcc_literal,
)


def compile_generic_bronze_tables_sql(
    ir_obj: CanonicalIR,
    *,
    include_batch4_restatement_in_seed: bool = False,
    bronze_tables: Mapping[str, list[dict[str, Any]] | Any] | None = None,
) -> list[str]:
    """Compile Bronze staging source tables and seed data for Hubs, Links, and Satellites."""
    hub_by_name = {h.name: h for h in ir_obj.hubs}
    link_by_name = {lnk.name: lnk for lnk in ir_obj.links}
    bitemp_sat = _find_bitemporal_satellite(ir_obj)
    bitemp_sat_name = bitemp_sat.name if bitemp_sat else None
    loaded_bronze = _load_bronze_table_rows(bronze_tables)
    bkcc_registry = build_staging_bkcc_registry(ir_obj)
    hub_bk_rows: dict[str, list[list[str]]] = {
        hub.name: _resolve_hub_bk_rows_from_bronze(
            hub,
            loaded_bronze,
            ir_obj=ir_obj,
            bkcc_registry=bkcc_registry,
        )
        for hub in ir_obj.hubs
    }

    parts: list[str] = []

    # 1. Bronze source tables & seed rows for each Hub
    raw_bronze_hub_pairs: dict[str, list[tuple[IRHub, Any]]] = {}
    for hub in ir_obj.hubs:
        tbl = _validate_ident(hub.name)
        default_rec_src = (
            hub.source_mappings[0].rec_src_value if hub.source_mappings else "SYSTEM_SEED"
        )
        bkcc_to_rec_src: dict[str, str] = {}
        for sm in hub.source_mappings:
            eff_lit = _effective_hub_sm_bkcc_literal(hub, sm)
            if eff_lit:
                bkcc_to_rec_src.setdefault(eff_lit.upper(), sm.rec_src_value)
            raw_short = _validate_ident(sm.staging_table.removeprefix("stg_").split(".")[-1])
            if raw_short != f"src_{tbl}":
                raw_bronze_hub_pairs.setdefault(raw_short, []).append((hub, sm))

        bkcc_col_expr = _effective_hub_bkcc_column_expr(hub)
        bk_names = {bk.name for bk in hub.business_keys}
        extra_bkcc_col = (
            _validate_ident(bkcc_col_expr)
            if bkcc_col_expr and bkcc_col_expr not in bk_names
            else None
        )
        bk_defs = [f"    {_validate_ident(bk.name)} VARCHAR," for bk in hub.business_keys]
        if extra_bkcc_col:
            bk_defs.append(f"    {extra_bkcc_col} VARCHAR,")
        bk_rows = hub_bk_rows[hub.name]
        bkcc_idx = next((i for i, bk in enumerate(hub.business_keys) if bk.is_bkcc), None)
        hub_insert_rows = []
        for row_bks in bk_rows:
            row_rec_src = default_rec_src
            if bkcc_idx is not None and len(row_bks) > bkcc_idx:
                row_rec_src = bkcc_to_rec_src.get(row_bks[bkcc_idx].upper(), default_rec_src)
            row_vals = list(row_bks)
            if extra_bkcc_col:
                eff_idx = bkcc_idx if bkcc_idx is not None else 0
                row_vals.append(row_bks[eff_idx] if eff_idx < len(row_bks) else "GLOBAL")
            lit_vals = ", ".join(
                chr(39) + v.replace(chr(39), chr(39) * 2) + chr(39) for v in row_vals
            )
            hub_insert_rows.append(
                f"    ({lit_vals}, TIMESTAMP '2026-10-01 06:00:00', '{_sql_lit(row_rec_src)}')"
            )
        parts.append(
            "\n".join(
                [
                    f"CREATE OR REPLACE TABLE bronze.src_{tbl} (",
                    *bk_defs,
                    "    load_dts TIMESTAMP,",
                    "    rec_src VARCHAR",
                    ");",
                    f"INSERT INTO bronze.src_{tbl} VALUES",
                    ",\n".join(hub_insert_rows) + ";",
                ]
            )
        )

    for raw_short, pairs in raw_bronze_hub_pairs.items():
        if len(pairs) == 1:
            only_hub, _ = pairs[0]
            parts.append(
                f"CREATE OR REPLACE VIEW bronze.{raw_short} AS "
                f"SELECT * FROM bronze.src_{_validate_ident(only_hub.name)};"
            )
        else:
            comp_cols: list[str] = []
            for _h, sm in pairs:
                for c in sm.bk_source_columns:
                    if c.lower() != "bkcc":
                        c_clean = _validate_ident(c)
                        if c_clean not in comp_cols:
                            comp_cols.append(c_clean)
            col_defs = [f"    {c} VARCHAR," for c in comp_cols]
            comp_insert_rows: list[str] = []
            row_count = max(
                (len(hub_bk_rows[h.name]) for h, _ in pairs),
                default=2,
            )
            comp_rec_src = pairs[0][1].rec_src_value if pairs else "SYSTEM_SEED"
            for r_idx in range(min(max(row_count, 2), 4)):
                comp_row_vals: list[str] = []
                for c_ident in comp_cols:
                    val_found = f"'VAL-{r_idx + 1:03d}'"
                    for h, sm in pairs:
                        src_non_bkcc = [
                            _validate_ident(col_n)
                            for col_n in sm.bk_source_columns
                            if col_n.lower() != "bkcc"
                        ]
                        if c_ident in src_non_bkcc:
                            pos = src_non_bkcc.index(c_ident)
                            scoped_lit = _effective_hub_sm_bkcc_literal(h, sm)
                            h_cand = _filter_hub_rows_for_bkcc(h, hub_bk_rows[h.name], scoped_lit)
                            chosen_row = h_cand[r_idx % len(h_cand)]
                            non_bkcc_vals = [
                                v
                                for bk, v in zip(h.business_keys, chosen_row, strict=True)
                                if not bk.is_bkcc
                            ]
                            if pos < len(non_bkcc_vals):
                                esc_v = non_bkcc_vals[pos].replace("'", "''")
                                val_found = f"'{esc_v}'"
                            break
                    comp_row_vals.append(val_found)
                comp_insert_rows.append(
                    f"    ({', '.join(comp_row_vals)}, TIMESTAMP '2026-10-01 06:00:00', "
                    f"'{_sql_lit(comp_rec_src)}')"
                )
            parts.append(
                "\n".join(
                    [
                        f"CREATE OR REPLACE TABLE bronze.{raw_short} (",
                        *col_defs,
                        "    load_dts TIMESTAMP,",
                        "    rec_src VARCHAR",
                        ");",
                        f"INSERT INTO bronze.{raw_short} VALUES",
                        ",\n".join(comp_insert_rows) + ";",
                    ]
                )
            )

    unstructured_links_by_name = {
        lnk.name: lnk for lnk in ir_obj.links if lnk.requires_steward_approval_gate
    }

    # 2. Bronze source tables & seed rows for each Link
    for link in ir_obj.links:
        tbl = _validate_ident(link.name)
        sm0_dict = link.source_mappings[0] if link.source_mappings else {}
        rec_src = (
            str(sm0_dict.get("rec_src_value", "SYSTEM_SEED"))
            if isinstance(sm0_dict, dict)
            else "SYSTEM_SEED"
        )
        link_stg_tbl = (
            str(sm0_dict.get("staging_table", f"stg_{tbl}"))
            if isinstance(sm0_dict, dict)
            else f"stg_{tbl}"
        )
        fk_src_map = sm0_dict.get("fk_source_columns") or {} if isinstance(sm0_dict, dict) else {}
        col_defs = []
        row1_tokens: list[str] = []
        row2_tokens: list[str] = []
        row3_tokens: list[str] = []
        row4_tokens: list[str] = []
        target_counts: dict[str, int] = {}
        for ref_idx, ref in enumerate(link.hub_references):
            fk_col = _validate_ident(ref.fk_column_name)
            t_hub = hub_by_name[ref.target_hub]
            occ = target_counts.get(t_hub.name, 0)
            target_counts[t_hub.name] = occ + 1
            fk_src_col = str(fk_src_map.get(ref.fk_column_name, ref.fk_column_name))
            ref_bkcc_lit = resolve_staging_bkcc_literal(
                ir_obj,
                link_stg_tbl,
                fk_src_col,
                ref.target_hub,
                role_name=ref.role_name,
                registry=bkcc_registry,
            )
            scoped_rows = _filter_hub_rows_for_bkcc(
                t_hub,
                hub_bk_rows[t_hub.name],
                ref_bkcc_lit,
            )
            h_r1 = scoped_rows[0]
            h_r2 = scoped_rows[1] if len(scoped_rows) > 1 else scoped_rows[0]
            r1_bks = h_r1 if (occ == 0 or scoped_rows != hub_bk_rows[t_hub.name]) else h_r2
            r2_bks = h_r2
            for bk, val1, val2 in zip(t_hub.business_keys, r1_bks, r2_bks, strict=True):
                raw_col = _validate_ident(f"{fk_col}__{bk.name}")
                col_defs.append(f"    {raw_col} VARCHAR,")
                eff_v1 = (ref_bkcc_lit or val1) if bk.is_bkcc else val1
                eff_v2 = (ref_bkcc_lit or val2) if bk.is_bkcc else val2
                esc1 = eff_v1.replace("'", "''")
                esc2 = eff_v2.replace("'", "''")
                row1_tokens.append(f"'{esc1}'")
                if ref_idx == 0:
                    row2_tokens.append(f"'{esc2}'")
                    row3_tokens.append(f"'{esc1}'")
                    row4_tokens.append(f"'{esc2}'")
                else:
                    row2_tokens.append(f"'{esc2}'" if bk.is_bkcc else "NULL")
                    row3_tokens.append(f"'{esc1}'" if bk.is_bkcc else "'-1'")
                    row4_tokens.append(f"'{esc2}'" if bk.is_bkcc else "'-2'")
            t_bkcc_col_expr = _effective_hub_bkcc_column_expr(t_hub)
            if t_bkcc_col_expr and t_bkcc_col_expr not in {bk.name for bk in t_hub.business_keys}:
                t_bkcc_idx = next(
                    (idx for idx, bk in enumerate(t_hub.business_keys) if bk.is_bkcc), 0
                )
                extra_fk_bkcc_col = _validate_ident(f"{fk_col}__{t_bkcc_col_expr}")
                col_defs.append(f"    {extra_fk_bkcc_col} VARCHAR,")
                v1_bkcc = r1_bks[t_bkcc_idx].replace("'", "''")
                v2_bkcc = r2_bks[t_bkcc_idx].replace("'", "''")
                row1_tokens.append(f"'{v1_bkcc}'")
                row2_tokens.append(f"'{v2_bkcc}'")
                row3_tokens.append(f"'{v1_bkcc}'")
                row4_tokens.append(f"'{v2_bkcc}'")

        col_defs.append("    load_dts TIMESTAMP,")
        if link.name in unstructured_links_by_name:
            col_defs.append("    rec_src VARCHAR,")
            col_defs.append("    steward_gate_status VARCHAR")
            steward_r1 = ", 'APPROVED'"
            steward_r2 = ", 'STEWARD_HOLD'"
            steward_r3 = ", 'STEWARD_HOLD'"
            steward_r4 = ", 'STEWARD_HOLD'"
        else:
            col_defs.append("    rec_src VARCHAR")
            steward_r1 = steward_r2 = steward_r3 = steward_r4 = ""

        parts.append(
            "\n".join(
                [
                    f"CREATE OR REPLACE TABLE bronze.src_{tbl} (",
                    *col_defs,
                    ");",
                    f"INSERT INTO bronze.src_{tbl} VALUES",
                    f"    ({', '.join(row1_tokens)}, TIMESTAMP '2026-10-01 06:00:00', "
                    f"'{_sql_lit(rec_src)}'{steward_r1}),",
                    f"    ({', '.join(row2_tokens)}, TIMESTAMP '2026-10-01 06:00:00', "
                    f"'{_sql_lit(rec_src)}'{steward_r2}),",
                    f"    ({', '.join(row3_tokens)}, TIMESTAMP '2026-10-01 06:00:00', "
                    f"'{_sql_lit(rec_src)}'{steward_r3}),",
                    f"    ({', '.join(row4_tokens)}, TIMESTAMP '2026-10-01 06:00:00', "
                    f"'{_sql_lit(rec_src)}'{steward_r4});",
                ]
            )
        )

    # 3. Bronze source tables & seed rows for each Satellite
    for sat in ir_obj.satellites:
        tbl = _validate_ident(sat.name)
        rec_src = sat.source_mapping.rec_src_value
        has_applied = sat.applied_dts_column is not None
        data_cols = _satellite_all_data_columns(sat)
        col_defs = []
        parent_val_rows: list[list[str]] = []

        sat_source_rows: list[dict[str, Any]] = []
        for sat_key_cand in _table_lookup_keys(sat.source_mapping.staging_table):
            if sat_key_cand in loaded_bronze:
                sat_source_rows = loaded_bronze[sat_key_cand]
                break

        if sat.parent_entity in hub_by_name:
            p_hub = hub_by_name[sat.parent_entity]
            for bk in p_hub.business_keys:
                c_ident = _validate_ident(f"parent_bk__{bk.name}")
                col_defs.append(f"    {c_ident} VARCHAR,")
            sat_sm_stg = sat.source_mapping.staging_table
            sat_bkcc_lit = resolve_staging_bkcc_literal(
                ir_obj,
                sat_sm_stg,
                "*",
                p_hub.name,
                registry=bkcc_registry,
            )
            scoped_hub_rows = _filter_hub_rows_for_bkcc(
                p_hub,
                hub_bk_rows[p_hub.name],
                sat_bkcc_lit,
            )
            for row_bks in scoped_hub_rows:
                p_vals = [f"'{_sql_lit(v)}'" for v in row_bks]
                parent_val_rows.append(p_vals)
            if not sat_source_rows:
                for sm in p_hub.source_mappings:
                    for key in _table_lookup_keys(sm.staging_table):
                        if key in loaded_bronze:
                            sat_source_rows = loaded_bronze[key]
                            break
                    if sat_source_rows:
                        break
        else:
            p_lnk = link_by_name[sat.parent_entity]
            for ref in p_lnk.hub_references:
                c_ident = _validate_ident(f"parent_fk__{ref.fk_column_name}")
                col_defs.append(f"    {c_ident} VARCHAR,")
            ref_hub_rows: list[list[list[str]]] = []
            for ref in p_lnk.hub_references:
                t_hub = hub_by_name[ref.target_hub]
                sm0_dict = p_lnk.source_mappings[0] if p_lnk.source_mappings else {}
                link_stg_tbl = (
                    str(sm0_dict.get("staging_table", f"stg_{_validate_ident(p_lnk.name)}"))
                    if isinstance(sm0_dict, dict)
                    else f"stg_{_validate_ident(p_lnk.name)}"
                )
                fk_src_map = (
                    sm0_dict.get("fk_source_columns") or {} if isinstance(sm0_dict, dict) else {}
                )
                fk_src_col = str(fk_src_map.get(ref.fk_column_name, ref.fk_column_name))
                ref_bkcc_lit = resolve_staging_bkcc_literal(
                    ir_obj,
                    link_stg_tbl,
                    fk_src_col,
                    ref.target_hub,
                    role_name=ref.role_name,
                    registry=bkcc_registry,
                )
                t_cand_rows = _filter_hub_rows_for_bkcc(
                    t_hub, hub_bk_rows[t_hub.name], ref_bkcc_lit
                )
                ref_hub_rows.append(t_cand_rows)
            n_rows = len(sat_source_rows) if sat_source_rows else 2
            for row_idx in range(n_rows):
                fk_vals: list[str] = []
                for ref_idx, ref in enumerate(p_lnk.hub_references):
                    t_hub = hub_by_name[ref.target_hub]
                    c_rows = ref_hub_rows[ref_idx]
                    chosen_row = (
                        c_rows[row_idx % len(c_rows)] if c_rows else [f"FALLBACK-{row_idx}"]
                    )
                    fk_hash = compute_dv2_length_prefixed_sha256(
                        chosen_row,
                        is_business_key=True,
                    )
                    fk_vals.append(f"'{fk_hash}'")
                parent_val_rows.append(fk_vals)

        col_defs.append("    load_dts TIMESTAMP,")
        if has_applied:
            col_defs.append("    applied_dts TIMESTAMP,")
        is_unstructured_link_sat = (
            sat.parent_entity in link_by_name
            and link_by_name[sat.parent_entity].requires_steward_approval_gate
        )
        has_steward_col = any(c.name.lower() == "steward_gate_status" for c in data_cols)
        add_steward_col = is_unstructured_link_sat and not has_steward_col
        col_defs.append("    rec_src VARCHAR" + ("," if (data_cols or add_steward_col) else ""))

        for idx_c, col in enumerate(data_cols):
            c_name = _validate_ident(col.name)
            c_type = _normalize_duckdb_type(col.data_type)
            comma = "," if (idx_c < len(data_cols) - 1 or add_steward_col) else ""
            col_defs.append(f"    {c_name} {c_type}{comma}")

        if add_steward_col:
            col_defs.append("    steward_gate_status VARCHAR DEFAULT 'APPROVED'")

        app_lit = "TIMESTAMP '2026-09-30 00:00:00', " if has_applied else ""
        insert_rows: list[str] = []
        for idx_r, p_vals in enumerate(parent_val_rows):
            src_row = sat_source_rows[idx_r] if idx_r < len(sat_source_rows) else None
            row_load_dts = (
                str(src_row.get("load_dts") or "2026-10-01 06:00:00").replace("'", "''")
                if src_row is not None
                else "2026-10-01 06:00:00"
            )
            row_app_dts = (
                str(src_row.get("applied_dts") or "2026-09-30 00:00:00").replace("'", "''")
                if src_row is not None
                else "2026-09-30 00:00:00"
            )
            row_app_lit = f"TIMESTAMP '{row_app_dts}', " if has_applied else ""
            r_payload = [
                _format_bronze_payload_sql_literal(c, src_row, idx_r + 1) for c in data_cols
            ]
            r_pay_sql = f", {', '.join(r_payload)}" if r_payload else ""
            steward_lit = ""
            if add_steward_col:
                steward_val = "STEWARD_HOLD" if idx_r == 1 else "APPROVED"
                steward_lit = f", '{steward_val}'"
            insert_rows.append(
                f"    ({', '.join(p_vals)}, TIMESTAMP '{row_load_dts}', "
                f"{row_app_lit}'{rec_src}'{r_pay_sql}{steward_lit})"
            )

        if include_batch4_restatement_in_seed and sat.name == bitemp_sat_name and parent_val_rows:
            r3_payload = [
                _satellite_seed_sql_literal(c, len(parent_val_rows) + 1) for c in data_cols
            ]
            r3_pay_sql = f", {', '.join(r3_payload)}" if r3_payload else ""
            steward_lit_r3 = ", 'APPROVED'" if add_steward_col else ""
            insert_rows.append(
                f"    ({', '.join(parent_val_rows[0])}, TIMESTAMP '2026-11-15 09:15:00', "
                f"{app_lit}'{rec_src}'{r3_pay_sql}{steward_lit_r3})"
            )

        parts.append(
            "\n".join(
                [
                    f"CREATE OR REPLACE TABLE bronze.src_{tbl} (",
                    *col_defs,
                    ");",
                    f"INSERT INTO bronze.src_{tbl} VALUES",
                    ",\n".join(insert_rows) + ";",
                ]
            )
        )

    return parts


__all__ = ["compile_generic_bronze_tables_sql"]
