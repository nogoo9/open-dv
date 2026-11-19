"""Helper compilers for Zone 1 Hub, Link, and Staging Alias Views."""

from __future__ import annotations

from typing import Any

import sqlglot

from opendv_core.constants import GLOBAL_BKCC, ZERO_KEY_ERROR_TOKEN, ZERO_KEY_UNKNOWN_TOKEN
from opendv_core.models.canonical_ir import CanonicalIR, IRHub, IRLink
from opendv_duckdb_sandbox.compiler_4zone.hashing import (
    _sql_lit,
    _validate_ident,
    build_algorithm_a5_sha256_sql,
)
from opendv_duckdb_sandbox.compiler_4zone.schema_utils import (
    _effective_hub_bkcc_column_expr,
    _effective_hub_bkcc_literal,
)
from opendv_duckdb_sandbox.compiler_4zone.staging_registry import resolve_staging_bkcc_literal
from opendv_duckdb_sandbox.sql_guard import validate_safe_sql


def validate_where_filter_condition(where_cond: str) -> str:
    """Validate that a contract ``where_filter_condition`` is a safe SQL predicate."""
    cleaned = str(where_cond or "").strip()
    if not cleaned:
        return ""
    probe_sql = f"SELECT 1 FROM bronze.src_probe WHERE {cleaned}"
    validate_safe_sql(probe_sql)
    parsed = sqlglot.parse_one(probe_sql, read="duckdb")
    if parsed.args.get("where") is None:
        raise ValueError(f"Invalid where_filter_condition predicate: {where_cond!r}")
    return cleaned


def compile_hub_zone_1_view(
    ir_obj: CanonicalIR,
    hub: IRHub,
    *,
    bkcc_registry: Any,
    created_views: set[str],
    alias_targets: list[tuple[str, str]],
    stg_to_hub_mappings: dict[str, list[tuple[IRHub, Any]]],
) -> str:
    """Compile a single Hub's `staging.stg_<hub>` view SQL."""
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
        return (
            f"CREATE OR REPLACE VIEW staging.{view_name} AS\n"
            f"SELECT\n"
            f"    {hk_expr} AS {hk_col},\n"
            f"    {bk_csv},\n"
            f"    load_dts,\n"
            f"    rec_src\n"
            f"FROM bronze.src_{tbl};"
        )

    if len(distinct_sm_bkccs) <= 1:
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
        return (
            f"CREATE OR REPLACE VIEW staging.{view_name} AS\n"
            f"SELECT\n"
            f"    {hk_expr} AS {hk_col},\n"
            f"    {bk_csv},\n"
            f"    load_dts,\n"
            f"    rec_src\n"
            f"FROM bronze.src_{tbl};"
        )

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
    return (
        f"CREATE OR REPLACE VIEW staging.{view_name} AS\n"
        + "\nUNION ALL\n".join(union_branches)
        + ";"
    )


def compile_link_zone_1_view(
    ir_obj: CanonicalIR,
    link: IRLink,
    *,
    hub_by_name: dict[str, IRHub],
    bkcc_registry: Any,
    created_views: set[str],
    alias_targets: list[tuple[str, str]],
    view_body_by_name: dict[str, str],
) -> str:
    """Compile a single Link's `staging.stg_<link>` view SQL, including sparse FK filters."""
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
    raw_where = (
        str(sm0_dict.get("where_filter_condition") or "").strip()
        if isinstance(sm0_dict, dict)
        else ""
    )
    safe_where = validate_where_filter_condition(raw_where) if raw_where else ""

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
    where_sql = f"\n    WHERE {safe_where}" if safe_where else ""
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
            f"    FROM bronze.src_{tbl}{where_sql}\n"
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
            f"    FROM bronze.src_{tbl}{where_sql}\n"
            f")\n"
            f"SELECT\n"
            f"    {link_hk_expr} AS {hk_col},\n"
            f"    {fk_csv},\n"
            f"    load_dts,\n"
            f"    rec_src\n"
            f"FROM fk_resolved;"
        )

    view_body_by_name[view_name] = link_body
    if is_unstructured_link:
        alias_targets.append(("stg_unstructured_vdr_llm_extractions", view_name))
    for sm_dict in link.source_mappings:
        if "staging_table" in sm_dict:
            alias_targets.append((_validate_ident(str(sm_dict["staging_table"])), view_name))
    return f"CREATE OR REPLACE VIEW staging.{view_name} AS\n{link_body}"


def compile_alias_zone_1_views(
    ir_obj: CanonicalIR,
    *,
    bkcc_registry: Any,
    created_views: set[str],
    alias_targets: list[tuple[str, str]],
    stg_to_hub_mappings: dict[str, list[tuple[IRHub, Any]]],
    view_body_by_name: dict[str, str],
) -> list[str]:
    """Compile staging alias views that redirect source-named staging views."""
    parts: list[str] = []
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
    return parts
