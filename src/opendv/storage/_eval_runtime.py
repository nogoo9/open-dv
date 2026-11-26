"""Non-circular DuckDB runtime verification against pure-Python ir_kernel (`EVAL-RUN-01..04`).

Strictly Layer 2a: imports opendv.models, duckdb, sqlglot, and sibling storage modules.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any

import duckdb
import pyarrow as pa
import sqlglot

from opendv.models.diagnostics import KeyTierEnum
from opendv.models.ir_kernel import (
    ZERO_KEY_ERROR,
    ZERO_KEY_UNKNOWN,
    evaluate_predicate,
    execute_hash_recipe,
)
from opendv.models.vault_ir import hub_canonical_bk_cols
from opendv.models.vault_ir_certified import CertifiedVaultIR
from opendv.storage.prover.edge_batches import _insert_rows
from opendv.storage.sql_compiler.ddl_builders import (
    ZONE_SCHEMAS,
    planned_exclusion_sets,
    qualify_name,
    resolve_staging_hd_col_map,
)


def _strip_arrow_tz_type(dtype: pa.DataType) -> pa.DataType:
    if pa.types.is_timestamp(dtype) and dtype.tz is not None:
        return pa.timestamp(dtype.unit)
    if pa.types.is_list(dtype):
        vf = dtype.value_field
        return pa.list_(vf.with_type(_strip_arrow_tz_type(vf.type)))
    if pa.types.is_large_list(dtype):
        vf = dtype.value_field
        return pa.large_list(vf.with_type(_strip_arrow_tz_type(vf.type)))
    if pa.types.is_fixed_size_list(dtype):
        vf = dtype.value_field
        return pa.list_(vf.with_type(_strip_arrow_tz_type(vf.type)), dtype.list_size)
    if pa.types.is_map(dtype):
        kf, itf = dtype.key_field, dtype.item_field
        return pa.map_(
            kf.with_type(_strip_arrow_tz_type(kf.type)),
            itf.with_type(_strip_arrow_tz_type(itf.type)),
        )
    if pa.types.is_struct(dtype):
        return pa.struct([f.with_type(_strip_arrow_tz_type(f.type)) for f in dtype])
    return dtype


def _make_arrow_cell_normalizer(dtype: pa.DataType) -> Callable[[Any], Any] | None:
    if pa.types.is_timestamp(dtype) and dtype.tz is not None:
        return lambda v: (
            None if v is None else (v.replace(tzinfo=UTC) if isinstance(v, datetime) else v)
        )
    if (
        pa.types.is_list(dtype)
        or pa.types.is_large_list(dtype)
        or pa.types.is_fixed_size_list(dtype)
    ):
        child = _make_arrow_cell_normalizer(dtype.value_type)
        if child is None:
            return None
        return lambda v: None if v is None else [child(item) for item in v]
    if pa.types.is_map(dtype):
        k_fn = _make_arrow_cell_normalizer(dtype.key_type)
        v_fn = _make_arrow_cell_normalizer(dtype.item_type)
        return lambda v: (
            None
            if v is None
            else {
                (k_fn(k) if k_fn else k): (v_fn(val) if v_fn else val)
                for k, val in (v.items() if isinstance(v, dict) else v)
            }
        )
    if pa.types.is_struct(dtype):
        child_fns = {
            f.name: fn
            for f in dtype
            if (fn := _make_arrow_cell_normalizer(f.type)) is not None
        }
        if not child_fns:
            return None
        return lambda v: (
            None
            if v is None
            else {
                k: (child_fns[k](val) if k in child_fns else val) for k, val in v.items()
            }
        )
    return None


def arrow_table_to_pylist_utc(tbl: pa.Table) -> list[dict[str, Any]]:
    """Convert Arrow table to Python dicts in UTC without invoking zoneinfo/pytz."""
    orig_schema = tbl.schema
    stripped_fields = [f.with_type(_strip_arrow_tz_type(f.type)) for f in orig_schema]
    needs_cast = any(
        nf.type != of.type for nf, of in zip(stripped_fields, orig_schema, strict=True)
    )
    norm_tbl = tbl.cast(pa.schema(stripped_fields)) if needs_cast else tbl
    col_norms = {
        f.name: fn
        for f in orig_schema
        if (fn := _make_arrow_cell_normalizer(f.type)) is not None
    }
    rows: list[dict[str, Any]] = norm_tbl.to_pylist()
    if not col_norms:
        return rows
    return [
        {k: (col_norms[k](v) if k in col_norms else v) for k, v in row.items()} for row in rows
    ]


def _safe_select_dicts(
    conn: duckdb.DuckDBPyConnection,
    sql: str,
) -> list[dict[str, Any]]:
    return arrow_table_to_pylist_utc(conn.sql(sql).to_arrow_table())


def run_eval_runtime_checks(
    ir: CertifiedVaultIR,
    ddl_sql: str,
    dml_sql: str,
    *,
    migration_sqls: Sequence[str] = (),
    source_rows_by_table: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
    physical_aliases: Mapping[str, str] | None = None,
) -> list[str]:
    """Run EVAL-RUN-01..04 in isolated :memory: DuckDB and return defect strings."""
    errors: list[str] = []
    aliases: dict[str, str] = dict(physical_aliases or {})
    hd_col_map = resolve_staging_hd_col_map(ir)
    planned_hubs, excluded_links, excluded_sats = planned_exclusion_sets(ir)

    conn = duckdb.connect(
        ":memory:",
        config={
            "memory_limit": "512MB",
            "threads": 2,
            "enable_external_access": False,
            "lock_configuration": True,
        },
    )
    try:
        try:
            for stmt in sqlglot.parse(ddl_sql, read="duckdb"):
                if stmt is not None:
                    conn.execute(stmt.sql(dialect="duckdb"))

            if source_rows_by_table:
                for b in ir.bronze_tables:
                    rows = source_rows_by_table.get(b.source_table_id, ())
                    if rows:
                        _insert_rows(
                            conn,
                            qualify_name("Z0", b.table_name, aliases),
                            [c.name for c in b.columns],
                            rows,
                        )

            for stmt in sqlglot.parse(dml_sql, read="duckdb"):
                if stmt is not None:
                    conn.execute(stmt.sql(dialect="duckdb"))
        except duckdb.Error as exc:
            return [f"EVAL-RUN execution error: {exc}"]

        # EVAL-RUN-01: compare Z1 hk_* and hash_diff digests against ir_kernel
        bronze_by_tbl = {b.source_table_id: b for b in ir.bronze_tables}
        hub_tier_by_name: dict[str, KeyTierEnum] = {
            h.hub_name: h.key_tier for h in ir.hubs if not h.is_planned_concept
        }
        hub_binding_tiers: dict[tuple[str, str], KeyTierEnum] = {
            (h.hub_name, sb.table_id): (sb.key_tier or h.key_tier)
            for h in ir.hubs
            if not h.is_planned_concept
            for sb in h.source_bindings
        }
        sats_by_tbl: dict[str, list[Any]] = {}
        for s in ir.satellites:
            if s.sat_name not in excluded_sats and s.hash_recipe is not None:
                sats_by_tbl.setdefault(s.source_table_id, []).append(s)

        for stg in ir.staging_views:
            bronze = bronze_by_tbl.get(stg.source_table_id)
            if bronze is None:
                continue
            col_types = {c.name: c.logical_type for c in bronze.columns}
            stg_tier_by_col: dict[str, KeyTierEnum] = {}
            for h in ir.hubs:
                if h.is_planned_concept:
                    continue
                if h.hash_recipe is not None:
                    stg_tier_by_col[h.hash_recipe.target_col] = h.key_tier
                for sb in h.source_bindings:
                    if sb.table_id == stg.source_table_id and sb.hash_recipe is not None:
                        stg_tier_by_col[sb.hash_recipe.target_col] = sb.key_tier or h.key_tier
            for lnk in ir.links:
                if lnk.link_name in excluded_links:
                    continue
                for sb in lnk.source_bindings:
                    if sb.table_id != stg.source_table_id:
                        continue
                    for p in lnk.participants:
                        if p.hub_name in hub_tier_by_name:
                            fk_target = sb.col_mappings.get(p.fk_col_name, p.fk_col_name)
                            target_tbl = p.target_hub_binding_table_id or sb.table_id
                            eff_p_tier: KeyTierEnum = (
                                p.key_tier
                                or hub_binding_tiers.get((p.hub_name, target_tbl))
                                or sb.key_tier
                                or hub_tier_by_name[p.hub_name]
                            )
                            if fk_target not in stg_tier_by_col:
                                stg_tier_by_col[fk_target] = eff_p_tier
                    if sb.hash_recipe is not None and sb.key_tier is not None:
                        stg_tier_by_col[sb.hash_recipe.target_col] = sb.key_tier

            try:
                b_rows = _safe_select_dicts(
                    conn,
                    f"SELECT * FROM {qualify_name('Z0', bronze.table_name, aliases)}",
                )
                s_rows = _safe_select_dicts(
                    conn, f"SELECT * FROM {qualify_name('Z1', stg.view_name, aliases)}"
                )
            except duckdb.Error as exc:
                errors.append(f"EVAL-RUN-01 query error on {stg.view_name}: {exc}")
                continue
            if len(b_rows) != len(s_rows):
                errors.append(f"EVAL-RUN-01 row count mismatch on {stg.view_name}")
                continue

            sat_list = sats_by_tbl.get(stg.source_table_id, [])
            sat_idx = 0
            for recipe in stg.hash_recipes:
                if recipe.recipe_kind == "SAT_HASH_DIFF" and sat_idx < len(sat_list):
                    proj_col = hd_col_map.get(
                        (stg.view_name, sat_list[sat_idx].sat_name), recipe.target_col
                    )
                    sat_idx += 1
                else:
                    proj_col = recipe.target_col
                tier: KeyTierEnum = stg_tier_by_col.get(recipe.target_col, "TIER_2_ENTERPRISE_SOR")
                for b_row, s_row in zip(b_rows, s_rows, strict=True):
                    expected = execute_hash_recipe(
                        recipe, b_row, key_tier=tier, column_types=col_types
                    )
                    raw_val = s_row.get(proj_col)
                    actual = bytes(raw_val).hex().lower() if raw_val is not None else ""
                    if actual != expected:
                        errors.append(
                            f"EVAL-RUN-01 digest mismatch {stg.view_name}.{proj_col}: "
                            f"{actual} != {expected}"
                        )
                        break

        try:
            # EVAL-RUN-02: Zero/Ghost presence, canonical Hub BK columns, and filter parity
            for h in ir.hubs:
                if h.hub_name in planned_hubs:
                    continue
                hf = qualify_name(h.zone_id, h.hub_name, aliases)
                hk = h.columns[0].name
                hub_col_names = {c.name for c in h.columns}
                missing_bks = [c for c in hub_canonical_bk_cols(h) if c not in hub_col_names]
                if missing_bks:
                    errors.append(
                        f"EVAL-RUN-02 missing canonical BK columns {missing_bks} on {h.hub_name}"
                    )
                z_rows = conn.execute(
                    f"SELECT LOWER(HEX({hk})) FROM {hf} WHERE LOWER(HEX({hk})) IN (?, ?)",
                    [ZERO_KEY_UNKNOWN, ZERO_KEY_ERROR],
                ).fetchall()
                if len({r[0] for r in z_rows}) < 2:
                    errors.append(f"EVAL-RUN-02 missing -1/-2 seed rows on {h.hub_name}")

            for lk in ir.links:
                if lk.link_name in excluded_links:
                    continue
                lf = qualify_name(lk.zone_id, lk.link_name, aliases)
                lhk = lk.columns[0].name
                for sb in lk.source_bindings:
                    sem = sb.load_semantics or lk.load_semantics
                    if sem and sem.filter_ast is not None and source_rows_by_table:
                        raw_src = source_rows_by_table.get(sb.table_id, ())
                        expected_pass = sum(
                            1 for r in raw_src if evaluate_predicate(sem.filter_ast, r)
                        )
                        cnt_r = conn.execute(
                            f"SELECT COUNT(*) FROM {lf} WHERE LOWER(HEX({lhk})) NOT IN (?, ?)",
                            [ZERO_KEY_UNKNOWN, ZERO_KEY_ERROR],
                        ).fetchone()
                        actual_cnt = int(cnt_r[0]) if cnt_r else 0
                        if (
                            actual_cnt != expected_pass
                            and lk.link_kind != "UNSTRUCTURED_EXTRACTION_LINK"
                        ):
                            errors.append(
                                f"EVAL-RUN-02 filter parity on {lk.link_name}: "
                                f"{actual_cnt} != {expected_pass}"
                            )

            # EVAL-RUN-03: replay idempotency
            before_counts: dict[str, int] = {}
            for h in ir.hubs:
                if h.hub_name not in planned_hubs:
                    hf = qualify_name(h.zone_id, h.hub_name, aliases)
                    r = conn.execute(f"SELECT COUNT(*) FROM {hf}").fetchone()
                    before_counts[h.hub_name] = int(r[0]) if r else 0
            for stmt in sqlglot.parse(dml_sql, read="duckdb"):
                if stmt is not None:
                    conn.execute(stmt.sql(dialect="duckdb"))
            for h in ir.hubs:
                if h.hub_name not in planned_hubs:
                    hf = qualify_name(h.zone_id, h.hub_name, aliases)
                    r = conn.execute(f"SELECT COUNT(*) FROM {hf}").fetchone()
                    if (int(r[0]) if r else 0) != before_counts[h.hub_name]:
                        errors.append(f"EVAL-RUN-03 replay non-idempotent on {h.hub_name}")
        except duckdb.Error as exc:
            errors.append(f"EVAL-RUN query error: {exc}")

        # EVAL-RUN-04: V001..V00N == full_schema_wN.sql information_schema.columns parity
        if migration_sqls:
            mig_conn = duckdb.connect(
                ":memory:",
                config={
                    "memory_limit": "512MB",
                    "threads": 2,
                    "enable_external_access": False,
                    "lock_configuration": True,
                },
            )
            try:
                for msql in migration_sqls:
                    for stmt in sqlglot.parse(msql, read="duckdb"):
                        if stmt is not None:
                            mig_conn.execute(stmt.sql(dialect="duckdb"))
                info_q = (
                    "SELECT table_schema, table_name, column_name, data_type, "
                    "is_nullable, ordinal_position FROM information_schema.columns "
                    f"WHERE table_schema IN {tuple(ZONE_SCHEMAS.values())!r} "
                    "ORDER BY table_schema, table_name, ordinal_position"
                )
                full_cols = conn.execute(info_q).fetchall()
                mig_cols = mig_conn.execute(info_q).fetchall()
                if full_cols != mig_cols:
                    errors.append(
                        f"EVAL-RUN-04 schema parity mismatch: "
                        f"{len(mig_cols)} migrated cols vs {len(full_cols)} full cols"
                    )
            finally:
                mig_conn.close()
    finally:
        conn.close()

    return errors


__all__ = ["run_eval_runtime_checks"]
