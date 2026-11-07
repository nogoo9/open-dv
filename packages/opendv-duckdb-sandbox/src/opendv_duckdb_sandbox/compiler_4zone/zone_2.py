"""Zone 2: Idempotent Insert-Only Raw Vault and Business Vault Loads."""

from __future__ import annotations

from opendv_core.constants import GHOST_REC_SRC, ZERO_KEY_ERROR, ZERO_KEY_UNKNOWN
from opendv_core.models.canonical_ir import CanonicalIR
from opendv_duckdb_sandbox.compiler_4zone.hashing import _validate_ident
from opendv_duckdb_sandbox.compiler_4zone.schema_utils import (
    _link_schema,
    _qualify_steward_predicate,
    _satellite_all_data_columns,
    _satellite_child_key_names,
    _satellite_schema,
)


def _compile_generic_zone_2_raw_vault_dml(ir_obj: CanonicalIR) -> str:
    """Compile Zone 2 idempotent INSERT-only Raw/Business Vault load statements."""
    parts = [
        "-- =============================================================================",
        "-- ZONE 2: IDEMPOTENT INSERT-ONLY RAW & BUSINESS VAULT LOADS",
        "-- =============================================================================",
    ]

    for hub in ir_obj.hubs:
        tbl = _validate_ident(hub.name)
        hk = _validate_ident(hub.hk_column.name)
        bk_cols = [_validate_ident(bk.name) for bk in hub.business_keys]
        bk_csv = ", ".join(bk_cols)
        src_bk_csv = ", ".join(f"src.{c}" for c in bk_cols)
        parts.append(
            f"INSERT INTO raw_vault.{tbl} ({hk}, {bk_csv}, load_dts, rec_src)\n"
            f"SELECT\n"
            f"    src.{hk},\n"
            f"    {src_bk_csv},\n"
            f"    MIN(src.load_dts) AS load_dts,\n"
            f"    ANY_VALUE(src.rec_src) AS rec_src\n"
            f"FROM staging.stg_{tbl} src\n"
            f"WHERE src.{hk} NOT IN ('{ZERO_KEY_UNKNOWN}', '{ZERO_KEY_ERROR}')\n"
            f"  AND NOT EXISTS (\n"
            f"      SELECT 1 FROM raw_vault.{tbl} h\n"
            f"      WHERE h.{hk} = src.{hk}\n"
            f"  )\n"
            f"GROUP BY src.{hk}, {src_bk_csv};"
        )

    unstructured_links_by_name = {
        lnk.name: lnk for lnk in ir_obj.links if lnk.requires_steward_approval_gate
    }
    for link in ir_obj.links:
        schema = _link_schema(link)
        tbl = _validate_ident(link.name)
        hk = _validate_ident(link.hk_column.name)
        fk_cols = [_validate_ident(ref.fk_column_name) for ref in link.hub_references]
        fk_csv = ", ".join(fk_cols)
        src_fk_csv = ", ".join(f"src.{c}" for c in fk_cols)
        steward_filter = (
            f"  AND {_qualify_steward_predicate(link.steward_filter_predicate)}\n"
            if link.name in unstructured_links_by_name
            else ""
        )
        is_sal_link = (link.name.startswith("sal_") or link.link_type == "SAME_AS") and len(
            fk_cols
        ) == 2
        sal_where_clause = ""
        if is_sal_link:
            m_fk, d_fk = fk_cols[0], fk_cols[1]
            sal_where_clause = (
                f"  AND src.{m_fk} <> src.{d_fk}\n"
                f"  AND NOT EXISTS (\n"
                f"      SELECT 1 FROM {schema}.{tbl} l_inv\n"
                f"      WHERE l_inv.{m_fk} = src.{d_fk}\n"
                f"        AND l_inv.{d_fk} = src.{m_fk}\n"
                f"  )\n"
                f"  AND NOT EXISTS (\n"
                f"      SELECT 1 FROM staging.stg_{tbl} src_inv\n"
                f"      WHERE src_inv.{m_fk} = src.{d_fk}\n"
                f"        AND src_inv.{d_fk} = src.{m_fk}\n"
                f"        AND src_inv.{m_fk} <> src_inv.{d_fk}\n"
                f"        AND (\n"
                f"            src_inv.load_dts < src.load_dts\n"
                f"            OR (\n"
                f"                src_inv.load_dts = src.load_dts\n"
                f"                AND src_inv.{m_fk} < src.{m_fk}\n"
                f"            )\n"
                f"        )\n"
                f"  )\n"
            )
        parts.append(
            f"INSERT INTO {schema}.{tbl} ({hk}, {fk_csv}, load_dts, rec_src)\n"
            f"SELECT\n"
            f"    src.{hk},\n"
            f"    {src_fk_csv},\n"
            f"    MIN(src.load_dts) AS load_dts,\n"
            f"    ANY_VALUE(src.rec_src) AS rec_src\n"
            f"FROM staging.stg_{tbl} src\n"
            f"WHERE src.{hk} NOT IN ('{ZERO_KEY_UNKNOWN}', '{ZERO_KEY_ERROR}')\n"
            f"{steward_filter}"
            f"{sal_where_clause}"
            f"  AND NOT EXISTS (\n"
            f"      SELECT 1 FROM {schema}.{tbl} l\n"
            f"      WHERE l.{hk} = src.{hk}\n"
            f"  )\n"
            f"GROUP BY src.{hk}, {src_fk_csv};"
        )

    for sat in ir_obj.satellites:
        schema = _satellite_schema(sat)
        tbl = _validate_ident(sat.name)
        parent_hk = _validate_ident(sat.parent_hk_column.name)
        has_applied = sat.applied_dts_column is not None
        child_keys = [_validate_ident(k) for k in _satellite_child_key_names(sat) if k != parent_hk]
        data_cols = [c for c in _satellite_all_data_columns(sat) if c.name not in set(child_keys)]
        col_names = [*child_keys, *(c.name for c in data_cols)]
        col_tail = ", " + ", ".join(_validate_ident(c) for c in col_names) if col_names else ""
        sel_tail = (
            ",\n    " + ", ".join(f"src.{_validate_ident(c)}" for c in col_names)
            if col_names
            else ""
        )
        app_col = "applied_dts, " if has_applied else ""
        src_app_col = "src.applied_dts, " if has_applied else ""

        is_link_sat = any(lnk.name == sat.parent_entity for lnk in ir_obj.links)
        parent_link_guard = ""
        sat_steward_filter = ""
        if is_link_sat:
            p_lnk = next(lnk for lnk in ir_obj.links if lnk.name == sat.parent_entity)
            p_schema = _link_schema(p_lnk)
            p_tbl = _validate_ident(p_lnk.name)
            p_hk = _validate_ident(p_lnk.hk_column.name)
            parent_link_guard = (
                f"  AND EXISTS (\n"
                f"      SELECT 1 FROM {p_schema}.{p_tbl} pl\n"
                f"      WHERE pl.{p_hk} = src.{parent_hk}\n"
                f"  )\n"
            )
            if p_lnk.name in unstructured_links_by_name:
                sat_steward_filter = (
                    f"  AND {_qualify_steward_predicate(p_lnk.steward_filter_predicate)}\n"
                )

        part_cols = [parent_hk, *child_keys]
        part_csv = ", ".join(part_cols)
        join_on = " AND ".join(f"cur.{c} = src.{c}" for c in part_cols)

        sat_cols = [parent_hk, "load_dts", "applied_dts", "rec_src", "hash_diff", *col_names]
        comb_csv = ",\n        ".join(f"combined.{c}" for c in [*sat_cols, "is_new"])

        if has_applied:
            parts.append(
                f"INSERT INTO {schema}.{tbl} (\n"
                f"    {parent_hk}, load_dts, {app_col}rec_src, hash_diff{col_tail}\n"
                f")\n"
                f"SELECT\n"
                f"    cand.{parent_hk},\n"
                f"    cand.load_dts,\n"
                f"    cand.applied_dts,\n"
                f"    cand.rec_src,\n"
                f"    cand.hash_diff{col_tail}\n"
                f"FROM (\n"
                f"    SELECT\n"
                f"        {comb_csv},\n"
                f"        LAG(combined.hash_diff) OVER (\n"
                f"            PARTITION BY {part_csv}\n"
                f"            ORDER BY combined.applied_dts ASC, combined.load_dts ASC, combined.is_new ASC\n"
                f"        ) AS prev_hash_diff\n"
                f"    FROM (\n"
                f"        SELECT\n"
                f"            {parent_hk},\n"
                f"            load_dts,\n"
                f"            applied_dts,\n"
                f"            rec_src,\n"
                f"            hash_diff{col_tail},\n"
                f"            0 AS is_new\n"
                f"        FROM {schema}.{tbl}\n"
                f"        WHERE rec_src <> '{GHOST_REC_SRC}'\n"
                f"        UNION ALL\n"
                f"        SELECT\n"
                f"            {parent_hk},\n"
                f"            load_dts,\n"
                f"            applied_dts,\n"
                f"            rec_src,\n"
                f"            hash_diff{col_tail},\n"
                f"            1 AS is_new\n"
                f"        FROM staging.stg_{tbl} src\n"
                f"        WHERE src.{parent_hk} NOT IN ('{ZERO_KEY_UNKNOWN}', '{ZERO_KEY_ERROR}')\n"
                f"        {parent_link_guard}"
                f"        {sat_steward_filter}"
                f"    ) combined\n"
                f") cand\n"
                f"WHERE cand.is_new = 1\n"
                f"  AND NOT EXISTS (\n"
                f"      SELECT 1 FROM {schema}.{tbl} ex\n"
                f"      WHERE ex.{parent_hk} = cand.{parent_hk}\n"
                f"        AND ex.load_dts = cand.load_dts\n"
                f"  )\n"
                f"  AND (\n"
                f"      cand.prev_hash_diff IS NULL\n"
                f"      OR cand.prev_hash_diff <> cand.hash_diff\n"
                f"  );"
            )
        else:
            parts.append(
                f"INSERT INTO {schema}.{tbl} (\n"
                f"    {parent_hk}, load_dts, {app_col}rec_src, hash_diff{col_tail}\n"
                f")\n"
                f"SELECT\n"
                f"    src.{parent_hk},\n"
                f"    src.load_dts,\n"
                f"    {src_app_col}src.rec_src,\n"
                f"    src.hash_diff{sel_tail}\n"
                f"FROM staging.stg_{tbl} src\n"
                f"LEFT JOIN (\n"
                f"    SELECT {part_csv}, load_dts, hash_diff\n"
                f"    FROM {schema}.{tbl}\n"
                f"    WHERE rec_src <> '{GHOST_REC_SRC}'\n"
                f"    QUALIFY ROW_NUMBER() OVER (\n"
                f"        PARTITION BY {part_csv} ORDER BY load_dts DESC\n"
                f"    ) = 1\n"
                f") cur ON {join_on}\n"
                f"WHERE src.{parent_hk} NOT IN ('{ZERO_KEY_UNKNOWN}', '{ZERO_KEY_ERROR}')\n"
                f"{parent_link_guard}"
                f"{sat_steward_filter}"
                f"  AND (\n"
                f"      cur.{parent_hk} IS NULL\n"
                f"      OR (src.load_dts > cur.load_dts AND cur.hash_diff <> src.hash_diff)\n"
                f"  );"
            )

    return "\n\n".join(parts)


__all__ = ["_compile_generic_zone_2_raw_vault_dml"]
