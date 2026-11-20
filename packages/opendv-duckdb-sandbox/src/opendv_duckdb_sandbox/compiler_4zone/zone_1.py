"""Zone 1: Hashed Staging Views with Algorithm A.5 Length-Prefixed SHA-256."""

from __future__ import annotations

from typing import Any

from opendv_core.constants import GLOBAL_BKCC
from opendv_core.models.canonical_ir import CanonicalIR, IRHub, IRLink, IRSatellite
from opendv_duckdb_sandbox.compiler_4zone.hashing import (
    _normalize_duckdb_type,
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
from opendv_duckdb_sandbox.compiler_4zone.zone_1_helpers import (
    compile_alias_zone_1_views,
    compile_hub_zone_1_view,
    compile_link_zone_1_view,
)


def _compile_satellite_parent_hk_expr(
    ir_obj: CanonicalIR,
    sat: IRSatellite,
    *,
    hub_by_name: dict[str, IRHub] | None = None,
    link_by_name: dict[str, IRLink] | None = None,
    bkcc_registry: Any = None,
) -> tuple[list[str], str]:
    """Return `(bronze_parent_cols, parent_hk_sql_expr)` for a Satellite's staging view."""
    hubs = hub_by_name if hub_by_name is not None else {h.name: h for h in ir_obj.hubs}
    links = link_by_name if link_by_name is not None else {lnk.name: lnk for lnk in ir_obj.links}
    reg = bkcc_registry if bkcc_registry is not None else build_staging_bkcc_registry(ir_obj)

    if sat.parent_entity in hubs:
        p_hub = hubs[sat.parent_entity]
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
            return p_cols, build_algorithm_a5_sha256_sql(
                non_bkcc_p_cols,
                is_business_key=True,
                bkcc_column_expr=p_bkcc_cand,
                key_columns=non_bkcc_p_cols,
            )
        sat_bkcc_lit = resolve_staging_bkcc_literal(
            ir_obj,
            sat.source_mapping.staging_table,
            "*",
            p_hub.name,
            registry=reg,
        )
        eff_sat_bkcc_lit = sat_bkcc_lit or p_bkcc_lit
        if eff_sat_bkcc_lit is None and p_has_bkcc_col:
            return p_cols, build_algorithm_a5_sha256_sql(
                p_cols,
                is_business_key=True,
                key_columns=non_bkcc_p_cols,
            )
        return p_cols, build_algorithm_a5_sha256_sql(
            non_bkcc_p_cols,
            bkcc_literal=eff_sat_bkcc_lit or GLOBAL_BKCC,
            is_business_key=True,
            key_columns=non_bkcc_p_cols,
        )

    p_lnk = links[sat.parent_entity]
    p_cols = [
        f"parent_fk__{_validate_ident(ref.fk_column_name)}" for ref in p_lnk.hub_references
    ]
    all_p_err_pred = " AND ".join(f"{c} = REPEAT('f', 64)" for c in p_cols)
    all_p_unk_pred = " AND ".join(f"{c} = REPEAT('0', 64)" for c in p_cols)
    return p_cols, build_algorithm_a5_sha256_sql(
        p_cols,
        is_business_key=True,
        error_predicate=all_p_err_pred,
        unknown_predicate=all_p_unk_pred,
    )


def _compile_satellite_zone_1_view(
    ir_obj: CanonicalIR,
    sat: IRSatellite,
    *,
    hub_by_name: dict[str, IRHub],
    link_by_name: dict[str, IRLink],
    bkcc_registry: Any,
    created_views: set[str],
    alias_targets: list[tuple[str, str]],
    view_body_by_name: dict[str, str],
) -> str:
    """Compile a single Satellite's `staging.stg_<sat>` view SQL."""
    tbl = _validate_ident(sat.name)
    view_name = f"stg_{tbl}"
    created_views.add(view_name)
    parent_hk = _validate_ident(sat.parent_hk_column.name)
    has_applied = sat.applied_dts_column is not None
    data_cols = _satellite_all_data_columns(sat)
    _, parent_hk_expr = _compile_satellite_parent_hk_expr(
        ir_obj,
        sat,
        hub_by_name=hub_by_name,
        link_by_name=link_by_name,
        bkcc_registry=bkcc_registry,
    )

    soft_deprecated = set(getattr(sat.source_mapping, "soft_deprecated_columns", None) or ())
    aliases = dict(getattr(sat.source_mapping, "column_aliases", None) or {})

    hash_inputs: list[str] = []
    payload_projections: list[str] = []
    use_explicit_projections = bool(soft_deprecated or aliases)
    for col_def in data_cols:
        clean_col = _validate_ident(col_def.name)
        if col_def.name in soft_deprecated:
            duck_type = _normalize_duckdb_type(col_def.data_type)
            null_expr = f"CAST(NULL AS {duck_type})"
            hash_inputs.append(null_expr)
            payload_projections.append(f"{null_expr} AS {clean_col}")
        else:
            src_col = _validate_ident(aliases.get(col_def.name, col_def.name))
            hash_inputs.append(src_col)
            payload_projections.append(
                f"{src_col} AS {clean_col}" if use_explicit_projections else src_col
            )

    hash_diff_expr = build_algorithm_a5_sha256_sql(hash_inputs, is_business_key=False)
    payload_csv = (",\n    " if use_explicit_projections else ", ").join(payload_projections)
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
    alias_targets.append((_validate_ident(sat.source_mapping.staging_table), view_name))
    return f"CREATE OR REPLACE VIEW staging.{view_name} AS\n{sat_body}"


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
        parts.append(
            compile_hub_zone_1_view(
                ir_obj,
                hub,
                bkcc_registry=bkcc_registry,
                created_views=created_views,
                alias_targets=alias_targets,
                stg_to_hub_mappings=stg_to_hub_mappings,
            )
        )

    for link in ir_obj.links:
        parts.append(
            compile_link_zone_1_view(
                ir_obj,
                link,
                hub_by_name=hub_by_name,
                bkcc_registry=bkcc_registry,
                created_views=created_views,
                alias_targets=alias_targets,
                view_body_by_name=view_body_by_name,
            )
        )

    for sat in ir_obj.satellites:
        parts.append(
            _compile_satellite_zone_1_view(
                ir_obj,
                sat,
                hub_by_name=hub_by_name,
                link_by_name=link_by_name,
                bkcc_registry=bkcc_registry,
                created_views=created_views,
                alias_targets=alias_targets,
                view_body_by_name=view_body_by_name,
            )
        )

    parts.extend(
        compile_alias_zone_1_views(
            ir_obj,
            bkcc_registry=bkcc_registry,
            created_views=created_views,
            alias_targets=alias_targets,
            stg_to_hub_mappings=stg_to_hub_mappings,
            view_body_by_name=view_body_by_name,
        )
    )
    return "\n\n".join(parts)


__all__ = ["_compile_generic_zone_1_staging_views", "_compile_satellite_parent_hk_expr"]
