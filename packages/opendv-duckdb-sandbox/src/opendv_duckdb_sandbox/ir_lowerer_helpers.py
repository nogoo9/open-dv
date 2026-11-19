"""Canonical IR Lowerer type-inference, BKCC resolution, and Hub lowering helpers."""

from __future__ import annotations

import re
from typing import Any

from opendv_core.constants import GLOBAL_BKCC
from opendv_core.hashing import resolve_canonical_bkcc_prefix
from opendv_core.models.canonical_ir import (
    IRBusinessKeyColumn,
    IRColumnDef,
    IRHashColumn,
    IRHashExpression,
    IRHub,
    IRHubSourceMapping,
)
from opendv_core.models.contract_v5 import ContractHubSpec
from opendv_core.models.graph_b import GraphBProfile


def _sanitize_model_name(domain_namespace: str) -> str:
    """Normalize a domain namespace into a schema-valid `^[a-z][a-z0-9_]*$` model name."""
    cleaned = re.sub(r"[^a-z0-9_]+", "_", domain_namespace.lower()).strip("_")
    if not cleaned or not cleaned[0].isalpha():
        cleaned = f"dv_{cleaned}" if cleaned else "dv_model"
    return cleaned if cleaned.endswith("_vault") else f"{cleaned}_vault"


def _staging_table_name(bronze_table: str) -> str:
    """Derive a canonical Zone 1 staging view name from a qualified Bronze table name."""
    parts = bronze_table.split(".")
    leaf = parts[-1].lower()
    for prefix in ("src_", "raw_", "bronze_"):
        if leaf.startswith(prefix) and len(leaf) > len(prefix):
            leaf = leaf[len(prefix) :]
            break
    if len(parts) >= 2:
        src_prefix = parts[0].lower().removeprefix("bronze_")
        if not leaf.startswith(src_prefix):
            return f"stg_{src_prefix}_{leaf}"
    return f"stg_{leaf}"


def _infer_sql_data_type(column_name: str, profile_type_map: dict[str, str] | None = None) -> str:
    """Infer a canonical SQL data type for a column from Graph B or naming conventions."""
    if profile_type_map and column_name in profile_type_map:
        return profile_type_map[column_name]
    lower = column_name.lower()
    if lower.endswith(("_usd", "_sgd", "_eur", "_amount", "_amt", "_nav", "_ebitda", "_ev")):
        return "DECIMAL(18,2)"
    if lower.endswith(("_pct", "_rate", "_multiple", "_ratio", "_score", "_confidence")):
        return "DECIMAL(10,4)"
    if lower.endswith(("_date", "_dt")):
        return "DATE"
    if lower.endswith(("_ts", "_dts", "_timestamp")):
        return "TIMESTAMP_NTZ(9)"
    if lower.startswith(("is_", "has_")) or lower.endswith("_flag"):
        return "BOOLEAN"
    if lower.endswith(("_year", "_bps", "_count", "_index", "_page", "_seq")):
        return "INTEGER"
    if lower.endswith("_sha256"):
        return "CHAR(64)"
    if lower.endswith(("_quote", "_excerpt", "_text", "_uri")):
        return "VARCHAR(2000)"
    return "VARCHAR(255)"


def _extract_profile_column_types(graph_b: GraphBProfile | None) -> dict[str, str]:
    """Build a lookup map of `column_name -> data_type` from a `GraphBProfile`."""
    if graph_b is None:
        return {}
    type_map: dict[str, str] = {}
    for system in graph_b.source_systems:
        for table in system.tables:
            for col in table.columns:
                type_map[col.column_name] = col.data_type
    return type_map


def _derive_hub_hk_name(hub_name: str) -> str:
    """Derive a canonical `hk_<concept>` column name from a Hub table name."""
    slug = hub_name
    for prefix in ("bhub_", "hub_"):
        if slug.startswith(prefix):
            slug = slug[len(prefix) :]
            break
    abbreviations = {
        "limited_partner": "lp",
        "portfolio_company": "portco",
        "capital_account": "cap_acct",
        "unstructured_document": "doc",
    }
    return f"hk_{abbreviations.get(slug, slug)}"


def _resolve_source_bkcc_literal(
    src: Any,
    *,
    verified_global: bool = False,
) -> str | None:
    """Resolve the effective BKCC literal for a Hub `BusinessKeySource`."""
    _ = verified_global
    raw_pfx = str(getattr(src, "bkcc_prefix", None) or "").strip().upper()
    if raw_pfx and raw_pfx not in {"NONE", "SOURCE_SYSTEM_PREFIX_REQUIRED"}:
        return raw_pfx
    if getattr(src, "key_authority_tier", "") == "TIER_3_LOCAL_SCOPED" or getattr(
        src, "require_src_qualifier", False
    ):
        return str(getattr(src, "record_source_code", "GLOBAL")).strip().upper() or "GLOBAL"
    return GLOBAL_BKCC


def _resolve_link_ref_bkcc_literal(
    source_bronze_table: str,
    fk_src_col: str,
    target_hub: str,
    *,
    role_alias: str,
    scoped_bkcc_map: dict[tuple[str, str, str], str | None],
    hub_source_specs: dict[str, list[tuple[list[str], str, str | None]]],
) -> str | None:
    """Resolve the BKCC literal for a `(bronze_table, fk_column_name, target_hub)` tuple."""
    tbl_candidates = (
        source_bronze_table.lower(),
        _staging_table_name(source_bronze_table).lower(),
        source_bronze_table.split(".")[-1].lower(),
    )
    col_lower = fk_src_col.lower()
    for tbl_key in tbl_candidates:
        if (tbl_key, col_lower, target_hub) in scoped_bkcc_map:
            return scoped_bkcc_map[(tbl_key, col_lower, target_hub)]

    specs = hub_source_specs.get(target_hub, [])
    if not specs:
        return None

    for tbl_key in tbl_candidates:
        if (tbl_key, "*", target_hub) in scoped_bkcc_map:
            return scoped_bkcc_map[(tbl_key, "*", target_hub)]

    stripped_col = col_lower
    for prefix in ("master_", "duplicate_", "primary_", "secondary_", "parent_", "child_", "fk_"):
        if stripped_col.startswith(prefix) and len(stripped_col) > len(prefix):
            stripped_col = stripped_col[len(prefix) :]
            break

    for key_cols, _tier, bkcc_lit in specs:
        low_keys = [k.lower() for k in key_cols]
        if col_lower in low_keys or stripped_col in low_keys:
            return bkcc_lit
        if any(k.endswith(f"_{stripped_col}") or stripped_col.endswith(f"_{k}") for k in low_keys):
            return bkcc_lit

    role_low = role_alias.lower()
    if role_low in {"duplicate", "secondary", "legacy", "alias"} and len(specs) > 1:
        for _key_cols, tier, bkcc_lit in reversed(specs):
            if tier == "TIER_3_LOCAL_SCOPED":
                return bkcc_lit
        return specs[-1][2]
    if role_low in {"master", "primary", "canonical", "golden"}:
        for _key_cols, tier, bkcc_lit in specs:
            if tier in {"TIER_1_GLOBAL_STANDARD", "TIER_2_ENTERPRISE_SOR"}:
                return bkcc_lit

    return specs[0][2]


def _lower_hub(
    contract_hub: ContractHubSpec,
    profile_types: dict[str, str],
) -> tuple[
    IRHub,
    dict[str, str],
    dict[tuple[str, str, str], str | None],
    list[tuple[list[str], str, str | None]],
]:
    """Lower a `ContractHubSpec` into an `IRHub` and return scoped BKCC metadata."""
    hk_name = _derive_hub_hk_name(contract_hub.name)
    verified_global = all(
        src.key_authority_tier == "TIER_1_GLOBAL_STANDARD" and not src.require_src_qualifier
        for src in contract_hub.business_key_sources
    )

    primary_key_cols = contract_hub.business_key_sources[0].key_columns
    first_src = contract_hub.business_key_sources[0]
    hub_bkcc_col_expr = next(
        (
            src.bkcc_column_expr.strip()
            for src in contract_hub.business_key_sources
            if src.bkcc_column_expr and src.bkcc_column_expr.strip()
        ),
        None,
    )
    hub_bkcc_literal = (
        first_src.bkcc_prefix.strip()
        if (
            not hub_bkcc_col_expr
            and len(contract_hub.business_key_sources) == 1
            and first_src.bkcc_prefix
            and first_src.bkcc_prefix.strip()
        )
        else None
    )
    excluded_hub_bkcc = {"bkcc", *([hub_bkcc_col_expr] if hub_bkcc_col_expr else [])}
    filtered_primary_key_cols = list(
        dict.fromkeys(c for c in primary_key_cols if c.strip() not in excluded_hub_bkcc)
    ) or list(dict.fromkeys(primary_key_cols))
    business_keys: list[IRBusinessKeyColumn] = [
        IRBusinessKeyColumn(
            name="bkcc",
            data_type="VARCHAR(64)",
            nullable=False,
            is_bkcc=True,
            is_tenant_id=True if hub_bkcc_col_expr else None,
        )
    ]
    seen_bks = {"bkcc"}
    for key_col in filtered_primary_key_cols:
        if key_col not in seen_bks:
            seen_bks.add(key_col)
            business_keys.append(
                IRBusinessKeyColumn(
                    name=key_col,
                    data_type=_infer_sql_data_type(key_col, profile_types),
                    nullable=False,
                )
            )

    source_mappings: list[IRHubSourceMapping] = []
    bronze_to_rec_src: dict[str, str] = {}
    scoped_bkcc_map: dict[tuple[str, str, str], str | None] = {}
    hub_specs: list[tuple[list[str], str, str | None]] = []

    for src in contract_hub.business_key_sources:
        bronze_to_rec_src.setdefault(src.bronze_table, src.record_source_code)
        src_bkcc_col = (
            src.bkcc_column_expr.strip()
            if src.bkcc_column_expr and src.bkcc_column_expr.strip()
            else None
        )
        bkcc_lit = (
            _resolve_source_bkcc_literal(src, verified_global=verified_global)
            if not src_bkcc_col
            else None
        )
        resolved_bkcc = (
            resolve_canonical_bkcc_prefix(
                bkcc_prefix=bkcc_lit or src.bkcc_prefix,
                key_authority_tier=src.key_authority_tier,
                require_src_qualifier=src.require_src_qualifier,
            )
            if not src_bkcc_col
            else (src.bkcc_prefix.strip() if src.bkcc_prefix and src.bkcc_prefix.strip() else None)
        )
        stg_name = _staging_table_name(src.bronze_table)
        bk_prefix_col = src_bkcc_col or "bkcc"
        non_bkcc_key_cols = [
            c for c in src.key_columns if c.strip() not in {bk_prefix_col, "bkcc"}
        ] or list(src.key_columns)
        bk_src_cols = [bk_prefix_col, *non_bkcc_key_cols]
        col_bkcc_map = {col: bkcc_lit for col in src.key_columns}

        for tbl_alias in (
            src.bronze_table.lower(),
            stg_name.lower(),
            src.bronze_table.split(".")[-1].lower(),
        ):
            scoped_bkcc_map.setdefault((tbl_alias, "*", contract_hub.name), bkcc_lit)
            for col in src.key_columns:
                scoped_bkcc_map[(tbl_alias, col.lower(), contract_hub.name)] = bkcc_lit
            for pk_col in primary_key_cols:
                scoped_bkcc_map.setdefault((tbl_alias, pk_col.lower(), contract_hub.name), bkcc_lit)

        hub_specs.append((list(src.key_columns), src.key_authority_tier, bkcc_lit))
        source_mappings.append(
            IRHubSourceMapping(
                staging_table=stg_name,
                bk_source_columns=bk_src_cols,
                bkcc_prefix=resolved_bkcc or (GLOBAL_BKCC if not src_bkcc_col else None),
                rec_src_value=src.record_source_code,
                bronze_table=src.bronze_table,
                key_authority_tier=src.key_authority_tier,
                bkcc_literal=bkcc_lit,
                bkcc_column_expr=src_bkcc_col,
                column_bkcc_map=col_bkcc_map,
            )
        )

    concept_label = contract_hub.ontology_concept_uri.rsplit(":", 1)[-1].replace("_", " ").title()
    ir_hub = IRHub(
        name=contract_hub.name,
        business_concept=concept_label,
        verified_global_master_key=verified_global,
        bkcc_literal=hub_bkcc_literal,
        bkcc_column_expr=hub_bkcc_col_expr,
        hk_column=IRHashColumn(
            name=hk_name,
            data_type="BINARY(32)",
            nullable=False,
            hash_expression=IRHashExpression(
                algorithm="SHA256",
                input_columns=[bk.name for bk in business_keys],
                normalization="UPPER_TRIM",
                bkcc_literal=hub_bkcc_literal,
                bkcc_column_expr=hub_bkcc_col_expr,
            ),
        ),
        business_keys=business_keys,
        load_dts_column=IRColumnDef(name="load_dts", data_type="TIMESTAMP_NTZ(9)", nullable=False),
        rec_src_column=IRColumnDef(name="rec_src", data_type="VARCHAR(255)", nullable=False),
        source_mappings=source_mappings,
    )
    return ir_hub, bronze_to_rec_src, scoped_bkcc_map, hub_specs


__all__ = [
    "_derive_hub_hk_name",
    "_extract_profile_column_types",
    "_infer_sql_data_type",
    "_lower_hub",
    "_resolve_link_ref_bkcc_literal",
    "_resolve_source_bkcc_literal",
    "_sanitize_model_name",
    "_staging_table_name",
]
