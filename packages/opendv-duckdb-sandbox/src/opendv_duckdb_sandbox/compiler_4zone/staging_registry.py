"""Staging View BKCC Namespace Registry and Scoped Literal Resolution."""

from __future__ import annotations

from opendv_core.models.canonical_ir import CanonicalIR
from opendv_duckdb_sandbox.compiler_4zone.schema_utils import (
    _effective_hub_bkcc_literal,
    _effective_hub_sm_bkcc_literal,
)


def _table_lookup_keys(table_name: str) -> list[str]:
    """Generate normalized lookup candidate keys for a staging or bronze table name."""
    raw = table_name.strip()
    if not raw:
        return []
    short = raw.split(".")[-1]
    keys = [raw, short]
    if "." in raw:
        schema, tbl = raw.split(".", 1)
        clean_schema = schema.removeprefix("bronze_").removeprefix("src_").removeprefix("stg_")
        if clean_schema:
            keys.extend(
                [
                    f"{clean_schema}_{tbl}",
                    f"stg_{clean_schema}_{tbl}",
                    f"src_{clean_schema}_{tbl}",
                    tbl,
                ]
            )
    for prefix in ("stg_", "src_"):
        if short.startswith(prefix):
            unprefixed = short[len(prefix) :]
            keys.extend([unprefixed, f"stg_{unprefixed}", f"src_{unprefixed}"])
            if "_" in unprefixed:
                sub_unprefixed = unprefixed.split("_", 1)[1]
                keys.extend([sub_unprefixed, f"stg_{sub_unprefixed}", f"src_{sub_unprefixed}"])
    seen: list[str] = []
    for k in keys:
        if k and k not in seen:
            seen.append(k)
    return seen


def build_staging_bkcc_registry(
    ir_obj: CanonicalIR,
) -> dict[tuple[str, str, str], str | None]:
    """Build a lookup registry mapping `(table_key, column_name, target_hub)` -> `bkcc_literal`."""
    registry: dict[tuple[str, str, str], str | None] = {}
    for hub in ir_obj.hubs:
        hub_bkcc_lit = _effective_hub_bkcc_literal(hub)
        non_bkcc_hub_cols = [bk.name.lower() for bk in hub.business_keys if not bk.is_bkcc]
        for sm in hub.source_mappings:
            eff_lit = _effective_hub_sm_bkcc_literal(hub, sm) or hub_bkcc_lit
            col_map = getattr(sm, "bk_column_bkcc_map", None) or {}
            src_cols = [
                c.lower() for c in getattr(sm, "bk_source_columns", []) if c.lower() != "bkcc"
            ]
            tbl_keys = _table_lookup_keys(getattr(sm, "staging_table", ""))
            bronze_tbl = getattr(sm, "bronze_table", None)
            if bronze_tbl:
                for k in _table_lookup_keys(str(bronze_tbl)):
                    if k not in tbl_keys:
                        tbl_keys.append(k)
            for t_key in tbl_keys:
                registry.setdefault((t_key, "*", hub.name), eff_lit)
                for c_name in src_cols:
                    registry[(t_key, c_name, hub.name)] = eff_lit
                for c_name in non_bkcc_hub_cols:
                    registry.setdefault((t_key, c_name, hub.name), eff_lit)
                for c_name, c_lit in col_map.items():
                    norm_lit = str(c_lit).strip().upper() if c_lit else eff_lit
                    registry[(t_key, str(c_name).lower(), hub.name)] = norm_lit

    for link in ir_obj.links:
        for sm_dict in link.source_mappings:
            if not isinstance(sm_dict, dict):
                continue
            stg_tbl = str(sm_dict.get("staging_table", ""))
            brz_tbl = str(sm_dict.get("bronze_table", ""))
            fk_src_map = sm_dict.get("fk_source_columns") or {}
            fk_bkcc_map = sm_dict.get("fk_bkcc_literals") or {}
            tbl_keys = _table_lookup_keys(stg_tbl)
            for k in _table_lookup_keys(brz_tbl):
                if k not in tbl_keys:
                    tbl_keys.append(k)
            for ref in link.hub_references:
                fk_col = ref.fk_column_name
                if fk_col in fk_bkcc_map:
                    fk_lit = fk_bkcc_map[fk_col]
                    norm_fk_lit = str(fk_lit).strip().upper() if fk_lit else None
                    src_col = str(fk_src_map.get(fk_col, fk_col)).lower()
                    for t_key in tbl_keys:
                        registry[(t_key, fk_col.lower(), ref.target_hub)] = norm_fk_lit
                        registry[(t_key, src_col, ref.target_hub)] = norm_fk_lit

    return registry


def resolve_staging_bkcc_literal(
    ir_obj: CanonicalIR,
    bronze_or_staging_table: str,
    column_name: str,
    target_hub: str,
    *,
    role_name: str | None = None,
    registry: dict[tuple[str, str, str], str | None] | None = None,
) -> str | None:
    """Resolve the scoped `bkcc_literal` for `(bronze_table, column_name, target_hub)`."""
    reg = registry if registry is not None else build_staging_bkcc_registry(ir_obj)
    col_low = column_name.strip().lower()
    tbl_keys = _table_lookup_keys(bronze_or_staging_table)

    # 1. Exact (table, column, target_hub) match
    for t_key in tbl_keys:
        if (t_key, col_low, target_hub) in reg:
            return reg[(t_key, col_low, target_hub)]

    # 2. Table-scoped wildcard match on target_hub when column is '*' or unambiguous
    if col_low == "*":
        for t_key in tbl_keys:
            if (t_key, "*", target_hub) in reg:
                return reg[(t_key, "*", target_hub)]

    hub_obj = next((h for h in ir_obj.hubs if h.name == target_hub), None)
    if hub_obj is None:
        return None

    # 3. Match against target_hub's source_mappings by column name or stripped role prefix
    stripped_col = col_low
    for prefix in ("master_", "duplicate_", "primary_", "secondary_", "parent_", "child_", "fk_"):
        if stripped_col.startswith(prefix) and len(stripped_col) > len(prefix):
            stripped_col = stripped_col[len(prefix) :]
            break

    if col_low != "*":
        for sm in hub_obj.source_mappings:
            src_cols = [c.lower() for c in sm.bk_source_columns if c.lower() != "bkcc"]
            if col_low in src_cols or stripped_col in src_cols:
                return _effective_hub_sm_bkcc_literal(hub_obj, sm)
            if any(
                c.endswith(f"_{stripped_col}") or stripped_col.endswith(f"_{c}") for c in src_cols
            ):
                return _effective_hub_sm_bkcc_literal(hub_obj, sm)

    # 4. Role-based disambiguation for Same-As / Hierarchical links on multi-source Hubs
    if role_name:
        r_low = role_name.strip().lower()
        if (
            r_low in {"duplicate", "secondary", "legacy", "alias"}
            and len(hub_obj.source_mappings) > 1
        ):
            for sm in reversed(hub_obj.source_mappings):
                if getattr(sm, "key_authority_tier", None) == "TIER_3_LOCAL_SCOPED":
                    return _effective_hub_sm_bkcc_literal(hub_obj, sm)
            return _effective_hub_sm_bkcc_literal(hub_obj, hub_obj.source_mappings[-1])
        if r_low in {"master", "primary", "canonical", "golden"}:
            for sm in hub_obj.source_mappings:
                if getattr(sm, "key_authority_tier", None) in {
                    "TIER_1_GLOBAL_STANDARD",
                    "TIER_2_ENTERPRISE_SOR",
                }:
                    return _effective_hub_sm_bkcc_literal(hub_obj, sm)

    # 5. Table wildcard fallback or primary source_mapping fallback
    for t_key in tbl_keys:
        if (t_key, "*", target_hub) in reg:
            return reg[(t_key, "*", target_hub)]

    if hub_obj.source_mappings:
        return _effective_hub_sm_bkcc_literal(hub_obj, hub_obj.source_mappings[0])
    return None


__all__ = [
    "_table_lookup_keys",
    "build_staging_bkcc_registry",
    "resolve_staging_bkcc_literal",
]
