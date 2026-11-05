"""Zone 1: Hashed Staging Views with Algorithm A.5 Length-Prefixed SHA-256."""

from __future__ import annotations

from typing import Any

from opendv_core.constants import GLOBAL_BKCC, ZERO_KEY_ERROR_TOKEN, ZERO_KEY_UNKNOWN_TOKEN
from opendv_core.models.canonical_ir import CanonicalIR, IRHub
from opendv_duckdb_sandbox.compiler_4zone.hashing import (
    _sql_lit,
    _validate_ident,
    build_algorithm_a5_sha256_sql,
)
from opendv_duckdb_sandbox.compiler_4zone.schema_utils import (
    _effective_hub_bkcc_column_expr,
    _effective_hub_bkcc_literal,
    _satellite_all_data_columns,
)
from opendv_duckdb_sandbox.compiler_4zone.staging_registry import (
    build_staging_bkcc_registry,
    resolve_staging_bkcc_literal,
)


def _compile_generic_zone_1_staging_views(ir_obj: CanonicalIR) -> str:
    """Compile Zone 1 Hashed Staging Views (`staging.stg_*`) with Algorithm A.5 SHA-256."""
    hub_by_name = {h.name: h for h in ir_obj.hubs}
    link_by_name = {lnk.name: lnk for lnk in ir_obj.links}
    bkcc_registry = build_staging_bkcc_registry(ir_obj)
    parts = [
        "-- =============================================================================",
        "-- ZONE 1: HASHED STAGING VIEWS (ALGORITHM A.5 LENGTH-PREFIXED SHA-256)",
        "-- =============================================================================",
    ]
    created_views: set[str] = set()
    alias_targets: list[tuple[str, str]] = []
    stg_to_hub_mappings: dict[str, list[tuple[IRHub, Any]]] = {}
    view_body_by_name: dict[str, str] = {}

    for hub in ir_obj.hubs:
        tbl = _validate_ident(hub.name)
        view_name = f"stg_{tbl}"
        created_views.add(view_name)
        hk_col = _validate_ident(hub.hk_column.name)
        bk_cols = [_validate_ident(bk.name) for bk in hub.business_keys]
        non_bkcc_cols = [
            _validate_ident(bk.name) for bk in hub.business_keys if not bk.is_bkcc
        ] or bk_cols
        bkcc_col_expr = _effective_hub_bkcc_column_expr(hub)
        bkcc_lit = _effective_hub_bkcc_literal(hub)

        distinct_sm_bkccs: list[str | None] = []
        for sm in hub.source_mappings:
            stg_alias = _validate_ident(sm.staging_table)
            stg_to_hub_mappings.setdefault(stg_alias, []).append((hub, sm))
            alias_targets.append((stg_alias, view_name))
            primary_src_col = next(
                (c for c in sm.bk_source_columns if c.lower() != "bkcc"),
                non_bkcc_cols[0] if non_bkcc_cols else "*",
            )
            sm_bkcc = resolve_staging_bkcc_literal(
                ir_obj,
                getattr(sm, "bronze_table", None) or sm.staging_table,
                primary_src_col,
                hub.name,
                registry=bkcc_registry,
            )
            if sm_bkcc not in distinct_sm_bkccs:
                distinct_sm_bkccs.append(sm_bkcc)

        if bkcc_col_expr:
            hk_expr = build_algorithm_a5_sha256_sql(
                non_bkcc_cols,
                is_business_key=True,
                bkcc_column_expr=_validate_ident(bkcc_col_expr),
                key_columns=non_bkcc_cols,
            )
            bk_csv = ", ".join(bk_cols)
            parts.append(
                f"CREATE OR REPLACE VIEW staging.{view_name} AS\n"
                f"SELECT\n"
                f"    {hk_expr} AS {hk_col},\n"
                f"    {bk_csv},\n"
                f"    load_dts,\n"
                f"    rec_src\n"
                f"FROM bronze.src_{tbl};"
            )
        elif len(distinct_sm_bkccs) <= 1:
            has_bkcc_col = any(bool(bk.is_bkcc) for bk in hub.business_keys)
            scoped_bkcc = distinct_sm_bkccs[0] if distinct_sm_bkccs else bkcc_lit
            if scoped_bkcc is None and not has_bkcc_col:
                scoped_bkcc = GLOBAL_BKCC
            if scoped_bkcc is None and has_bkcc_col:
                hk_expr = build_algorithm_a5_sha256_sql(
                    bk_cols,
                    is_business_key=True,
                    key_columns=non_bkcc_cols,
                )
            else:
                hk_expr = build_algorithm_a5_sha256_sql(
                    non_bkcc_cols,
                    bkcc_literal=scoped_bkcc,
                    is_business_key=True,
                    key_columns=non_bkcc_cols,
                )
            bk_select_exprs = [
                f"'{_sql_lit(scoped_bkcc)}' AS {_validate_ident(bk.name)}"
                if bk.is_bkcc and scoped_bkcc is not None
                else _validate_ident(bk.name)
                for bk in hub.business_keys
            ]
            bk_csv = ", ".join(bk_select_exprs)
            parts.append(
                f"CREATE OR REPLACE VIEW staging.{view_name} AS\n"
                f"SELECT\n"
                f"    {hk_expr} AS {hk_col},\n"
                f"    {bk_csv},\n"
                f"    load_dts,\n"
                f"    rec_src\n"
                f"FROM bronze.src_{tbl};"
            )
        else:
            union_branches: list[str] = []
            for sm_bkcc in distinct_sm_bkccs:
                eff_sm_bkcc = sm_bkcc or bkcc_lit or GLOBAL_BKCC
                hk_expr = build_algorithm_a5_sha256_sql(
                    non_bkcc_cols,
                    bkcc_literal=eff_sm_bkcc,
                    is_business_key=True,
                    key_columns=non_bkcc_cols,
                )
                bk_select_exprs = [
                    f"'{_sql_lit(eff_sm_bkcc)}' AS {_validate_ident(bk.name)}"
                    if bk.is_bkcc and eff_sm_bkcc is not None
                    else _validate_ident(bk.name)
                    for bk in hub.business_keys
                ]
                bk_csv = ", ".join(bk_select_exprs)
                where_clause = (
                    f"\nWHERE UPPER(TRIM(CAST(bkcc AS VARCHAR))) = '{_sql_lit(eff_sm_bkcc)}'"
                    if eff_sm_bkcc is not None and any(bk.is_bkcc for bk in hub.business_keys)
                    else ""
                )
                union_branches.append(
                    f"SELECT\n"
                    f"    {hk_expr} AS {hk_col},\n"
                    f"    {bk_csv},\n"
                    f"    load_dts,\n"
                    f"    rec_src\n"
                    f"FROM bronze.src_{tbl}{where_clause}"
                )
            parts.append(
                f"CREATE OR REPLACE VIEW staging.{view_name} AS\n"
                + "\nUNION ALL\n".join(union_branches)
                + ";"
            )

    for link in ir_obj.links:
        tbl = _validate_ident(link.name)
        view_name = f"stg_{tbl}"
        created_views.add(view_name)
        hk_col = _validate_ident(link.hk_column.name)
        sm0_dict = link.source_mappings[0] if link.source_mappings else {}
        link_stg_tbl = (
            str(sm0_dict.get("staging_table", f"stg_{tbl}"))
            if isinstance(sm0_dict, dict)
            else f"stg_{tbl}"
        )
        fk_src_map = sm0_dict.get("fk_source_columns") or {} if isinstance(sm0_dict, dict) else {}
        fk_cols: list[str] = []
        fk_cte_exprs: list[str] = []
        for ref in link.hub_references:
            fk_col = _validate_ident(ref.fk_column_name)
            fk_cols.append(fk_col)
            t_hub = hub_by_name[ref.target_hub]
            fk_src_col = str(fk_src_map.get(ref.fk_column_name, ref.fk_column_name))
            ref_bkcc_lit = resolve_staging_bkcc_literal(
                ir_obj,
                link_stg_tbl,
                fk_src_col,
                ref.target_hub,
                role_name=ref.role_name,
                registry=bkcc_registry,
            )
            raw_bk_cols = [_validate_ident(f"{fk_col}__{bk.name}") for bk in t_hub.business_keys]
            non_bkcc_cols = [
                _validate_ident(f"{fk_col}__{bk.name}")
                for bk in t_hub.business_keys
                if not bk.is_bkcc
            ] or raw_bk_cols
            error_pred = " OR ".join(
                f"TRIM(CAST({c} AS VARCHAR)) = '{ZERO_KEY_ERROR_TOKEN}'" for c in non_bkcc_cols
            )
            unknown_pred = " OR ".join(
                f"(NULLIF(TRIM(CAST({c} AS VARCHAR)), '') IS NULL "
                f"OR TRIM(CAST({c} AS VARCHAR)) = '{ZERO_KEY_UNKNOWN_TOKEN}')"
                for c in non_bkcc_cols
            )
            t_bkcc_col_expr = _effective_hub_bkcc_column_expr(t_hub)
            t_bkcc_lit = _effective_hub_bkcc_literal(t_hub)
            t_has_bkcc_col = any(bool(bk.is_bkcc) for bk in t_hub.business_keys)
            eff_fk_bkcc_lit = ref_bkcc_lit or t_bkcc_lit
            if t_bkcc_col_expr:
                bkcc_cand = (
                    _validate_ident(f"{fk_col}__{t_bkcc_col_expr}")
                    if f"{fk_col}__{t_bkcc_col_expr}" in raw_bk_cols
                    else _validate_ident(f"{fk_col}__bkcc")
                )
                raw_hash = build_algorithm_a5_sha256_sql(
                    non_bkcc_cols,
                    is_business_key=True,
                    bkcc_column_expr=bkcc_cand,
                    key_columns=non_bkcc_cols,
                )
            elif eff_fk_bkcc_lit is None and t_has_bkcc_col:
                raw_hash = build_algorithm_a5_sha256_sql(
                    raw_bk_cols,
                    is_business_key=True,
                    key_columns=non_bkcc_cols,
                )
            else:
                raw_hash = build_algorithm_a5_sha256_sql(
                    non_bkcc_cols,
                    bkcc_literal=eff_fk_bkcc_lit or GLOBAL_BKCC,
                    is_business_key=True,
                    key_columns=non_bkcc_cols,
                )
            fk_cte_exprs.append(
                f"        COALESCE(\n"
                f"            CASE\n"
                f"                WHEN {error_pred} THEN REPEAT('f', 64)\n"
                f"                WHEN {unknown_pred} THEN REPEAT('0', 64)\n"
                f"                ELSE {raw_hash}\n"
                f"            END,\n"
                f"            REPEAT('0', 64)\n"
                f"        ) AS {fk_col}"
            )
        all_fk_err_pred = " AND ".join(f"{c} = REPEAT('f', 64)" for c in fk_cols)
        all_fk_unk_pred = " AND ".join(f"{c} = REPEAT('0', 64)" for c in fk_cols)
        link_hk_expr = build_algorithm_a5_sha256_sql(
            fk_cols,
            is_business_key=True,
            error_predicate=all_fk_err_pred,
            unknown_predicate=all_fk_unk_pred,
        )
        fk_csv = ", ".join(fk_cols)
        fk_cte_body = ",\n".join(fk_cte_exprs)
        is_unstructured_link = link.requires_steward_approval_gate
        if is_unstructured_link:
            link_body = (
                f"WITH fk_resolved AS (\n"
                f"    SELECT\n"
                f"{fk_cte_body},\n"
                f"        load_dts,\n"
                f"        rec_src,\n"
                f"        CASE\n"
                f"            WHEN UPPER(TRIM(COALESCE(steward_gate_status, ''))) IN (\n"
                f"                'APPROVED',\n"
                f"                'STEWARD_VERIFIED',\n"
                f"                'AUTO_ACCEPTED_TIER_1',\n"
                f"                'AUTO_ACCEPTED_TIER_2'\n"
                f"            ) THEN 'APPROVED'\n"
                f"            ELSE COALESCE(\n"
                f"                NULLIF(TRIM(steward_gate_status), ''), 'STEWARD_HOLD'\n"
                f"            )\n"
                f"        END AS steward_gate_status\n"
                f"    FROM bronze.src_{tbl}\n"
                f")\n"
                f"SELECT\n"
                f"    {link_hk_expr} AS {hk_col},\n"
                f"    {fk_csv},\n"
                f"    load_dts,\n"
                f"    rec_src,\n"
                f"    steward_gate_status\n"
                f"FROM fk_resolved;"
            )
        else:
            link_body = (
                f"WITH fk_resolved AS (\n"
                f"    SELECT\n"
                f"{fk_cte_body},\n"
                f"        load_dts,\n"
                f"        rec_src\n"
                f"    FROM bronze.src_{tbl}\n"
                f")\n"
                f"SELECT\n"
                f"    {link_hk_expr} AS {hk_col},\n"
                f"    {fk_csv},\n"
                f"    load_dts,\n"
                f"    rec_src\n"
                f"FROM fk_resolved;"
            )
        view_body_by_name[view_name] = link_body
        parts.append(f"CREATE OR REPLACE VIEW staging.{view_name} AS\n{link_body}")
        if is_unstructured_link:
            alias_targets.append(("stg_unstructured_vdr_llm_extractions", view_name))
        for sm_dict in link.source_mappings:
            if "staging_table" in sm_dict:
                alias_targets.append((_validate_ident(str(sm_dict["staging_table"])), view_name))

    for sat in ir_obj.satellites:
        tbl = _validate_ident(sat.name)
        view_name = f"stg_{tbl}"
        created_views.add(view_name)
        parent_hk = _validate_ident(sat.parent_hk_column.name)
        has_applied = sat.applied_dts_column is not None
        data_cols = _satellite_all_data_columns(sat)

        if sat.parent_entity in hub_by_name:
            p_hub = hub_by_name[sat.parent_entity]
            p_cols = [f"parent_bk__{_validate_ident(bk.name)}" for bk in p_hub.business_keys]
            non_bkcc_p_cols = [
                f"parent_bk__{_validate_ident(bk.name)}"
                for bk in p_hub.business_keys
                if not bk.is_bkcc
            ] or p_cols
            p_bkcc_col_expr = _effective_hub_bkcc_column_expr(p_hub)
            p_bkcc_lit = _effective_hub_bkcc_literal(p_hub)
            p_has_bkcc_col = any(bool(bk.is_bkcc) for bk in p_hub.business_keys)
            if p_bkcc_col_expr:
                p_bkcc_cand = (
                    f"parent_bk__{_validate_ident(p_bkcc_col_expr)}"
                    if f"parent_bk__{_validate_ident(p_bkcc_col_expr)}" in p_cols
                    else "parent_bk__bkcc"
                )
                parent_hk_expr = build_algorithm_a5_sha256_sql(
                    non_bkcc_p_cols,
                    is_business_key=True,
                    bkcc_column_expr=p_bkcc_cand,
                    key_columns=non_bkcc_p_cols,
                )
            else:
                sat_bkcc_lit = resolve_staging_bkcc_literal(
                    ir_obj,
                    sat.source_mapping.staging_table,
                    "*",
                    p_hub.name,
                    registry=bkcc_registry,
                )
                eff_sat_bkcc_lit = sat_bkcc_lit or p_bkcc_lit
                if eff_sat_bkcc_lit is None and p_has_bkcc_col:
                    parent_hk_expr = build_algorithm_a5_sha256_sql(
                        p_cols,
                        is_business_key=True,
                        key_columns=non_bkcc_p_cols,
                    )
                else:
                    parent_hk_expr = build_algorithm_a5_sha256_sql(
                        non_bkcc_p_cols,
                        bkcc_literal=eff_sat_bkcc_lit or GLOBAL_BKCC,
                        is_business_key=True,
                        key_columns=non_bkcc_p_cols,
                    )
        else:
            p_lnk = link_by_name[sat.parent_entity]
            p_cols = [
                f"parent_fk__{_validate_ident(ref.fk_column_name)}" for ref in p_lnk.hub_references
            ]
            all_p_err_pred = " AND ".join(f"{c} = REPEAT('f', 64)" for c in p_cols)
            all_p_unk_pred = " AND ".join(f"{c} = REPEAT('0', 64)" for c in p_cols)
            parent_hk_expr = build_algorithm_a5_sha256_sql(
                p_cols,
                is_business_key=True,
                error_predicate=all_p_err_pred,
                unknown_predicate=all_p_unk_pred,
            )

        payload_names = [_validate_ident(c.name) for c in data_cols]
        hash_diff_expr = build_algorithm_a5_sha256_sql(payload_names, is_business_key=False)
        payload_csv = ", ".join(payload_names)
        applied_sel = "    applied_dts,\n" if has_applied else ""
        payload_sel = f",\n    {payload_csv}" if payload_csv else ""
        is_unstructured_link_sat = (
            sat.parent_entity in link_by_name
            and link_by_name[sat.parent_entity].requires_steward_approval_gate
        )
        has_steward_col = any(c.name.lower() == "steward_gate_status" for c in data_cols)
        steward_sel = (
            ",\n    CASE\n"
            "        WHEN UPPER(TRIM(COALESCE(steward_gate_status, ''))) IN (\n"
            "            'APPROVED',\n"
            "            'STEWARD_VERIFIED',\n"
            "            'AUTO_ACCEPTED_TIER_1',\n"
            "            'AUTO_ACCEPTED_TIER_2'\n"
            "        ) THEN 'APPROVED'\n"
            "        ELSE COALESCE(\n"
            "            NULLIF(TRIM(steward_gate_status), ''), 'STEWARD_HOLD'\n"
            "        )\n"
            "    END AS steward_gate_status"
            if (is_unstructured_link_sat and not has_steward_col)
            else ""
        )
        sat_body = (
            f"SELECT\n"
            f"    {parent_hk_expr} AS {parent_hk},\n"
            f"    load_dts,\n"
            f"{applied_sel}"
            f"    rec_src,\n"
            f"    {hash_diff_expr} AS hash_diff"
            f"{payload_sel}"
            f"{steward_sel}\n"
            f"FROM bronze.src_{tbl};"
        )
        view_body_by_name[view_name] = sat_body
        parts.append(f"CREATE OR REPLACE VIEW staging.{view_name} AS\n{sat_body}")
        alias_targets.append((_validate_ident(sat.source_mapping.staging_table), view_name))

    for alias_name, target_view in alias_targets:
        if alias_name in created_views:
            continue
        created_views.add(alias_name)
        hub_pairs = stg_to_hub_mappings.get(alias_name, [])
        if len(hub_pairs) > 1:
            raw_short = _validate_ident(alias_name.removeprefix("stg_").split(".")[-1])
            select_exprs: list[str] = []
            seen_cols: set[str] = set()
            for hub, sm in hub_pairs:
                hk_col = _validate_ident(hub.hk_column.name)
                src_non_bkcc_cols = [
                    _validate_ident(c) for c in sm.bk_source_columns if c.lower() != "bkcc"
                ]
                primary_col = src_non_bkcc_cols[0] if src_non_bkcc_cols else "*"
                sm_bkcc = resolve_staging_bkcc_literal(
                    ir_obj,
                    getattr(sm, "bronze_table", None) or sm.staging_table,
                    primary_col,
                    hub.name,
                    registry=bkcc_registry,
                )
                hk_expr = build_algorithm_a5_sha256_sql(
                    src_non_bkcc_cols,
                    bkcc_literal=sm_bkcc,
                    is_business_key=True,
                )
                if hk_col not in seen_cols:
                    seen_cols.add(hk_col)
                    select_exprs.append(f"    {hk_expr} AS {hk_col}")
                if sm_bkcc is not None:
                    bkcc_alias = f"bkcc__{_validate_ident(hub.name)}"
                    if bkcc_alias not in seen_cols:
                        seen_cols.add(bkcc_alias)
                        select_exprs.append(f"    '{_sql_lit(sm_bkcc)}' AS {bkcc_alias}")
                for c_ident in src_non_bkcc_cols:
                    if c_ident not in seen_cols:
                        seen_cols.add(c_ident)
                        select_exprs.append(f"    {c_ident}")
            select_exprs.extend(["    load_dts", "    rec_src"])
            parts.append(
                f"CREATE OR REPLACE VIEW staging.{alias_name} AS\n"
                f"SELECT\n" + ",\n".join(select_exprs) + f"\nFROM bronze.{raw_short};"
            )
        elif len(hub_pairs) == 1:
            only_hub, sm = hub_pairs[0]
            tbl = _validate_ident(only_hub.name)
            hk_col = _validate_ident(only_hub.hk_column.name)
            non_bkcc_cols = [
                _validate_ident(bk.name) for bk in only_hub.business_keys if not bk.is_bkcc
            ]
            primary_col = next(
                (c for c in sm.bk_source_columns if c.lower() != "bkcc"),
                non_bkcc_cols[0] if non_bkcc_cols else "*",
            )
            sm_bkcc = resolve_staging_bkcc_literal(
                ir_obj,
                getattr(sm, "bronze_table", None) or sm.staging_table,
                primary_col,
                only_hub.name,
                registry=bkcc_registry,
            )
            hk_expr = build_algorithm_a5_sha256_sql(
                non_bkcc_cols,
                bkcc_literal=sm_bkcc,
                is_business_key=True,
            )
            bk_select_exprs = [
                f"'{_sql_lit(sm_bkcc)}' AS {_validate_ident(bk.name)}"
                if bk.is_bkcc and sm_bkcc is not None
                else _validate_ident(bk.name)
                for bk in only_hub.business_keys
            ]
            where_clause = (
                f"\nWHERE UPPER(TRIM(CAST(bkcc AS VARCHAR))) = '{_sql_lit(sm_bkcc)}'"
                if sm_bkcc is not None
                and len(only_hub.source_mappings) > 1
                and any(bk.is_bkcc for bk in only_hub.business_keys)
                else ""
            )
            parts.append(
                f"CREATE OR REPLACE VIEW staging.{alias_name} AS\n"
                f"SELECT\n"
                f"    {hk_expr} AS {hk_col},\n"
                f"    {', '.join(bk_select_exprs)},\n"
                f"    load_dts,\n"
                f"    rec_src\n"
                f"FROM bronze.src_{tbl}{where_clause};"
            )
        else:
            body_sql = view_body_by_name.get(
                target_view,
                f"SELECT load_dts, rec_src FROM staging.{target_view};",
            )
            parts.append(f"CREATE OR REPLACE VIEW staging.{alias_name} AS\n{body_sql}")

    return "\n\n".join(parts)


__all__ = ["_compile_generic_zone_1_staging_views"]
