"""Generic Bronze Source Tables and Deterministic Seed Generators."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from opendv_core.constants import GLOBAL_BKCC, ZERO_KEY_UNKNOWN
from opendv_core.hashing import compute_dv2_length_prefixed_sha256
from opendv_core.models.canonical_ir import CanonicalIR, IRHub
from opendv_duckdb_sandbox.compiler_4zone.bronze_link_tables import (
    compile_bronze_link_tables_sql,
)
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
    _bitemporal_restatement_sql_literal,
    _effective_hub_bkcc_column_expr,
    _effective_hub_bkcc_literal,
    _effective_hub_sm_bkcc_literal,
    _find_bitemporal_satellite,
    _satellite_all_data_columns,
)
from opendv_duckdb_sandbox.compiler_4zone.staging_registry import (
    _table_lookup_keys,
    build_staging_bkcc_registry,
    resolve_staging_bkcc_literal,
)


def _compute_ref_fk_hash(
    t_hub: IRHub,
    chosen_row: list[str],
    ref_bkcc_lit: str | None,
) -> str:
    """Compute the Algorithm A.5 parent Hub HK matching Zone 1 `stg_<link>` views."""
    t_has_bkcc_col = any(bool(bk.is_bkcc) for bk in t_hub.business_keys)
    eff_bkcc_lit = ref_bkcc_lit or _effective_hub_bkcc_literal(t_hub)
    if eff_bkcc_lit is not None or not t_has_bkcc_col:
        non_bkcc_vals = [
            v for bk, v in zip(t_hub.business_keys, chosen_row, strict=True) if not bk.is_bkcc
        ] or chosen_row
        return compute_dv2_length_prefixed_sha256(
            non_bkcc_vals,
            is_business_key=True,
            bkcc_prefix=eff_bkcc_lit or GLOBAL_BKCC,
        )
    return compute_dv2_length_prefixed_sha256(chosen_row, is_business_key=True)


def _format_aliased_payload_literal(
    col: Any,
    phys_name: str,
    src_row: dict[str, Any] | None,
    row_idx: int,
) -> str:
    if src_row is not None:
        if phys_name in src_row and src_row[phys_name] is not None:
            target = col if phys_name == col.name else col.model_copy(update={"name": phys_name})
            return _format_bronze_payload_sql_literal(target, src_row, row_idx)
        if col.name in src_row and src_row[col.name] is not None:
            return _format_bronze_payload_sql_literal(col, src_row, row_idx)
    return _format_bronze_payload_sql_literal(col, src_row, row_idx)


def _make_table_and_insert(table_name: str, col_defs: list[str], insert_rows: list[str]) -> str:
    cols = "\n".join(col_defs)
    vals = ",\n".join(insert_rows)
    return (
        f"CREATE OR REPLACE TABLE bronze.{table_name} (\n{cols}\n);\n"
        f"INSERT INTO bronze.{table_name} VALUES\n{vals};"
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
            hub, loaded_bronze, ir_obj=ir_obj, bkcc_registry=bkcc_registry
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
        seen_hub_bks: set[str] = set()
        bk_defs = []
        for bk in hub.business_keys:
            clean_bk = _validate_ident(bk.name)
            if clean_bk not in seen_hub_bks:
                seen_hub_bks.add(clean_bk)
                bk_defs.append(f"    {clean_bk} VARCHAR,")
        extra_bkcc_col = (
            _validate_ident(bkcc_col_expr)
            if bkcc_col_expr and bkcc_col_expr not in seen_hub_bks
            else None
        )
        if extra_bkcc_col:
            seen_hub_bks.add(extra_bkcc_col)
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
            _make_table_and_insert(
                f"src_{tbl}",
                [*bk_defs, "    load_dts TIMESTAMP,", "    rec_src VARCHAR"],
                hub_insert_rows,
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
                _make_table_and_insert(
                    raw_short,
                    [*col_defs, "    load_dts TIMESTAMP,", "    rec_src VARCHAR"],
                    comp_insert_rows,
                )
            )

    unstructured_links_by_name = {
        lnk.name: lnk for lnk in ir_obj.links if lnk.requires_steward_approval_gate
    }

    # 2. Bronze source tables & seed rows for each Link
    parts.extend(
        compile_bronze_link_tables_sql(
            ir_obj,
            hub_by_name=hub_by_name,
            hub_bk_rows=hub_bk_rows,
            loaded_bronze=loaded_bronze,
            bkcc_registry=bkcc_registry,
            unstructured_links_by_name=unstructured_links_by_name,
        )
    )

    # 3. Bronze source tables & seed rows for each Satellite
    for sat in ir_obj.satellites:
        tbl = _validate_ident(sat.name)
        rec_src = sat.source_mapping.rec_src_value
        has_applied = sat.applied_dts_column is not None
        soft_deprecated = set(getattr(sat.source_mapping, "soft_deprecated_columns", None) or ())
        aliases = dict(getattr(sat.source_mapping, "column_aliases", None) or {})
        data_cols = [c for c in _satellite_all_data_columns(sat) if c.name not in soft_deprecated]
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
            ref_bkcc_lits: list[str | None] = []
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
                ref_bkcc_lits.append(ref_bkcc_lit)
            is_sal_link = (
                p_lnk.name.startswith("sal_") or p_lnk.link_type == "SAME_AS"
            ) and len(p_lnk.hub_references) == 2
            n_rows = len(sat_source_rows) if sat_source_rows else 2
            for row_idx in range(n_rows):
                fk_vals: list[str] = []
                target_counts: dict[str, int] = {}
                for ref_idx, ref in enumerate(p_lnk.hub_references):
                    t_hub = hub_by_name[ref.target_hub]
                    occ = target_counts.get(t_hub.name, 0)
                    target_counts[t_hub.name] = occ + 1
                    c_rows = ref_hub_rows[ref_idx]
                    if is_sal_link and not sat_source_rows and row_idx == 1 and ref_idx > 0:
                        # Match bronze_link_tables row 2 (h_r2, ZERO_KEY_UNKNOWN) so Zone 2 SAL
                        # inverse-pair filtering keeps row 2 in esat_sal_*.
                        fk_hash = ZERO_KEY_UNKNOWN
                    else:
                        pick_idx = (row_idx + occ) % len(c_rows) if c_rows else 0
                        chosen_row = c_rows[pick_idx] if c_rows else [f"FALLBACK-{row_idx}"]
                        fk_hash = _compute_ref_fk_hash(t_hub, chosen_row, ref_bkcc_lits[ref_idx])
                    fk_vals.append(f"'{fk_hash}'")
                if is_sal_link and len(fk_vals) == 2 and fk_vals[0] == fk_vals[1]:
                    fk_vals[1] = f"'{ZERO_KEY_UNKNOWN}'"
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

        seen_sat_col_names: set[str] = set()
        clean_col_defs: list[str] = []
        for def_line in col_defs:
            col_part = def_line.strip().split()[0]
            if col_part not in seen_sat_col_names:
                seen_sat_col_names.add(col_part)
                clean_col_defs.append(def_line)

        deduped_data_cols: list[tuple[Any, str]] = []
        for col in data_cols:
            phys_name = _validate_ident(aliases.get(col.name, col.name))
            if phys_name not in seen_sat_col_names:
                seen_sat_col_names.add(phys_name)
                deduped_data_cols.append((col, phys_name))

        for idx_c, (col, phys_name) in enumerate(deduped_data_cols):
            c_type = _normalize_duckdb_type(col.data_type)
            comma = "," if (idx_c < len(deduped_data_cols) - 1 or add_steward_col) else ""
            clean_col_defs.append(f"    {phys_name} {c_type}{comma}")

        if add_steward_col and "steward_gate_status" not in seen_sat_col_names:
            seen_sat_col_names.add("steward_gate_status")
            clean_col_defs.append("    steward_gate_status VARCHAR DEFAULT 'APPROVED'")

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
                _format_aliased_payload_literal(c, phys_name, src_row, idx_r + 1)
                for c, phys_name in deduped_data_cols
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
                _bitemporal_restatement_sql_literal(sat, c, len(parent_val_rows) + 1)
                for c, _phys in deduped_data_cols
            ]
            r3_pay_sql = f", {', '.join(r3_payload)}" if r3_payload else ""
            steward_lit_r3 = ", 'APPROVED'" if add_steward_col else ""
            insert_rows.append(
                f"    ({', '.join(parent_val_rows[0])}, TIMESTAMP '2026-11-15 09:15:00', "
                f"{app_lit}'{rec_src}'{r3_pay_sql}{steward_lit_r3})"
            )

        parts.append(_make_table_and_insert(f"src_{tbl}", clean_col_defs, insert_rows))

    return parts


__all__ = ["compile_generic_bronze_tables_sql"]
