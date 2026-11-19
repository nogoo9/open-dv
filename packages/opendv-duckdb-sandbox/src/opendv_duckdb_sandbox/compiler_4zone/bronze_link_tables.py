"""Bronze Source Table DDL and Row Seed Compiler for Canonical IR Links."""

from __future__ import annotations

from typing import Any

from opendv_core.models.canonical_ir import CanonicalIR, IRHub, IRLink
from opendv_duckdb_sandbox.compiler_4zone.bronze_seed_resolver import _filter_hub_rows_for_bkcc
from opendv_duckdb_sandbox.compiler_4zone.hashing import _sql_lit, _validate_ident
from opendv_duckdb_sandbox.compiler_4zone.schema_utils import _effective_hub_bkcc_column_expr
from opendv_duckdb_sandbox.compiler_4zone.staging_registry import (
    _table_lookup_keys,
    resolve_staging_bkcc_literal,
)


def _format_table_and_insert(
    table_name: str, col_defs: list[str], insert_rows: list[str]
) -> str:
    cols = "\n".join(col_defs)
    vals = ",\n".join(insert_rows)
    return (
        f"CREATE OR REPLACE TABLE bronze.{table_name} (\n{cols}\n);\n"
        f"INSERT INTO bronze.{table_name} VALUES\n{vals};"
    )


def compile_bronze_link_tables_sql(
    ir_obj: CanonicalIR,
    *,
    hub_by_name: dict[str, IRHub],
    hub_bk_rows: dict[str, list[list[str]]],
    loaded_bronze: dict[str, list[dict[str, Any]]],
    bkcc_registry: Any,
    unstructured_links_by_name: dict[str, IRLink],
) -> list[str]:
    """Compile Bronze `src_<link>` source tables and seed/loaded rows."""
    parts: list[str] = []
    for link in ir_obj.links:
        tbl = _validate_ident(link.name)
        sm0_dict = link.source_mappings[0] if link.source_mappings else {}
        rec_src = (
            str(sm0_dict.get("rec_src_value", "SYSTEM_SEED"))
            if isinstance(sm0_dict, dict)
            else "SYSTEM_SEED"
        )
        link_brz_tbl = (
            str(sm0_dict.get("bronze_table", "") or "")
            if isinstance(sm0_dict, dict)
            else ""
        )
        link_stg_tbl = (
            str(sm0_dict.get("staging_table", f"stg_{tbl}"))
            if isinstance(sm0_dict, dict)
            else f"stg_{tbl}"
        )
        fk_src_map = sm0_dict.get("fk_source_columns") or {} if isinstance(sm0_dict, dict) else {}
        has_where_filter = bool(
            isinstance(sm0_dict, dict) and sm0_dict.get("where_filter_condition")
        )
        link_bronze_rows: list[dict[str, Any]] | None = None
        if has_where_filter:
            for cand_tbl in (link_brz_tbl, link_stg_tbl):
                if not cand_tbl:
                    continue
                for lk_key in _table_lookup_keys(cand_tbl):
                    if lk_key in loaded_bronze:
                        link_bronze_rows = loaded_bronze[lk_key]
                        break
                if link_bronze_rows is not None:
                    break

        col_defs: list[str] = []
        seen_link_cols: set[str] = set()
        link_col_specs: list[tuple[str, str, str | None, bool, str, str | None]] = []
        raw_fk_specs: list[tuple[str, str]] = []
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
            scoped_rows = _filter_hub_rows_for_bkcc(t_hub, hub_bk_rows[t_hub.name], ref_bkcc_lit)
            h_r1 = scoped_rows[0]
            h_r2 = scoped_rows[1] if len(scoped_rows) > 1 else scoped_rows[0]
            r1_bks = h_r1 if (occ == 0 or scoped_rows != hub_bk_rows[t_hub.name]) else h_r2
            r2_bks = h_r2
            t_bkcc_col_expr = _effective_hub_bkcc_column_expr(t_hub)
            for bk, val1, val2 in zip(t_hub.business_keys, r1_bks, r2_bks, strict=True):
                raw_col = _validate_ident(f"{fk_col}__{bk.name}")
                col_defs.append(f"    {raw_col} VARCHAR,")
                seen_link_cols.add(raw_col)
                link_col_specs.append(
                    (raw_col, fk_src_col, ref_bkcc_lit, bool(bk.is_bkcc), bk.name, t_bkcc_col_expr)
                )
                esc1 = ((ref_bkcc_lit or val1) if bk.is_bkcc else val1).replace("'", "''")
                esc2 = ((ref_bkcc_lit or val2) if bk.is_bkcc else val2).replace("'", "''")
                row1_tokens.append(f"'{esc1}'")
                if ref_idx == 0:
                    row2_tokens.append(f"'{esc2}'")
                    row3_tokens.append(f"'{esc1}'")
                    row4_tokens.append(f"'{esc2}'")
                else:
                    row2_tokens.append(f"'{esc2}'" if bk.is_bkcc else "NULL")
                    row3_tokens.append(f"'{esc1}'" if bk.is_bkcc else "'-1'")
                    row4_tokens.append(f"'{esc2}'" if bk.is_bkcc else "'-2'")
            if t_bkcc_col_expr and t_bkcc_col_expr not in {bk.name for bk in t_hub.business_keys}:
                t_bkcc_idx = next(
                    (idx for idx, bk in enumerate(t_hub.business_keys) if bk.is_bkcc), 0
                )
                extra_fk_bkcc_col = _validate_ident(f"{fk_col}__{t_bkcc_col_expr}")
                col_defs.append(f"    {extra_fk_bkcc_col} VARCHAR,")
                seen_link_cols.add(extra_fk_bkcc_col)
                link_col_specs.append(
                    (
                        extra_fk_bkcc_col,
                        fk_src_col,
                        ref_bkcc_lit,
                        True,
                        t_bkcc_col_expr,
                        t_bkcc_col_expr,
                    )
                )
                v1_bkcc = r1_bks[t_bkcc_idx].replace("'", "''")
                v2_bkcc = r2_bks[t_bkcc_idx].replace("'", "''")
                row1_tokens.append(f"'{v1_bkcc}'")
                row2_tokens.append(f"'{v2_bkcc}'")
                row3_tokens.append(f"'{v1_bkcc}'")
                row4_tokens.append(f"'{v2_bkcc}'")
            if has_where_filter or link_bronze_rows is not None:
                clean_src_fk = _validate_ident(fk_src_col)
                if clean_src_fk not in seen_link_cols:
                    seen_link_cols.add(clean_src_fk)
                    col_defs.append(f"    {clean_src_fk} VARCHAR,")
                    raw_fk_specs.append((clean_src_fk, fk_src_col))
                    first_non_bkcc = next(
                        (
                            (val1, val2)
                            for bk, val1, val2 in zip(
                                t_hub.business_keys, r1_bks, r2_bks, strict=True
                            )
                            if not bk.is_bkcc
                        ),
                        (r1_bks[0], r2_bks[0]),
                    )
                    esc_fk1 = first_non_bkcc[0].replace("'", "''")
                    esc_fk2 = first_non_bkcc[1].replace("'", "''")
                    row1_tokens.append(f"'{esc_fk1}'")
                    if ref_idx == 0:
                        row2_tokens.append(f"'{esc_fk2}'")
                        row3_tokens.append(f"'{esc_fk1}'")
                        row4_tokens.append(f"'{esc_fk2}'")
                    else:
                        row2_tokens.append("NULL")
                        row3_tokens.append("'-1'")
                        row4_tokens.append("'-2'")

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

        l_ts = "TIMESTAMP '2026-10-01 06:00:00'"
        esc_rec = _sql_lit(rec_src)
        if link_bronze_rows:
            link_insert_rows: list[str] = []
            for row_dict in link_bronze_rows:
                ci = {str(k).lower(): v for k, v in row_dict.items()}
                vals: list[str] = []
                for (
                    _raw_col,
                    fk_src_col,
                    ref_bkcc_lit,
                    is_bkcc,
                    bk_name,
                    bkcc_expr,
                ) in link_col_specs:
                    if is_bkcc:
                        raw_v = (
                            ref_bkcc_lit
                            or (bkcc_expr and ci.get(bkcc_expr.lower()))
                            or ci.get(bk_name.lower())
                            or ci.get("bkcc")
                            or "GLOBAL"
                        )
                    else:
                        raw_v = ci.get(fk_src_col.lower())
                        if raw_v is None:
                            raw_v = ci.get(bk_name.lower())
                    if raw_v is None or str(raw_v).strip() == "":
                        vals.append("NULL")
                    else:
                        vals.append(f"'{_sql_lit(str(raw_v))}'")
                for _clean_src_fk, fk_src_col in raw_fk_specs:
                    raw_v = ci.get(fk_src_col.lower())
                    if raw_v is None or str(raw_v).strip() == "":
                        vals.append("NULL")
                    else:
                        vals.append(f"'{_sql_lit(str(raw_v))}'")
                r_dts = ci.get("load_dts") or "2026-10-01 06:00:00"
                r_src = ci.get("rec_src") or rec_src
                extra_st = ""
                if link.name in unstructured_links_by_name:
                    st_val = str(ci.get("steward_gate_status") or "STEWARD_HOLD")
                    extra_st = f", '{_sql_lit(st_val)}'"
                link_insert_rows.append(
                    f"    ({', '.join(vals)}, TIMESTAMP '{_sql_lit(str(r_dts))}', "
                    f"'{_sql_lit(str(r_src))}'{extra_st})"
                )
        else:
            link_insert_rows = [
                f"    ({', '.join(row1_tokens)}, {l_ts}, '{esc_rec}'{steward_r1})",
                f"    ({', '.join(row2_tokens)}, {l_ts}, '{esc_rec}'{steward_r2})",
                f"    ({', '.join(row3_tokens)}, {l_ts}, '{esc_rec}'{steward_r3})",
                f"    ({', '.join(row4_tokens)}, {l_ts}, '{esc_rec}'{steward_r4})",
            ]
        parts.append(_format_table_and_insert(f"src_{tbl}", col_defs, link_insert_rows))
    return parts


__all__ = ["compile_bronze_link_tables_sql"]
