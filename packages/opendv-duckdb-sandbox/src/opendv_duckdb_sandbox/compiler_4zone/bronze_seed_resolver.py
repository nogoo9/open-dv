"""Bronze Data Loading, Payload Formatting, and Business Key Token Resolution."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from opendv_core.models.canonical_ir import CanonicalIR, IRColumnDef, IRHub
from opendv_duckdb_sandbox.compiler_4zone.hashing import _normalize_duckdb_type
from opendv_duckdb_sandbox.compiler_4zone.schema_utils import (
    _effective_hub_bkcc_column_expr,
    _effective_hub_bkcc_literal,
    _effective_hub_sm_bkcc_literal,
    _hub_seed_bk_values,
    _satellite_seed_sql_literal,
)
from opendv_duckdb_sandbox.compiler_4zone.staging_registry import (
    _table_lookup_keys,
    resolve_staging_bkcc_literal,
)


def _load_bronze_table_rows(
    bronze_tables: Mapping[str, list[dict[str, Any]] | Any] | None,
) -> dict[str, list[dict[str, Any]]]:
    """Normalize caller-provided `bronze_tables` into `{normalized_name: list[dict]}`."""
    if not bronze_tables:
        return {}
    normalized: dict[str, list[dict[str, Any]]] = {}
    for raw_name, content in bronze_tables.items():
        keys = _table_lookup_keys(str(raw_name))
        rows: list[dict[str, Any]] = []
        if isinstance(content, list):
            rows = [dict(r) for r in content if isinstance(r, dict)]
        elif hasattr(content, "adapter") or isinstance(content, (str, Path)):
            from opendv_sources import SourceSpec, get_source_registry

            if hasattr(content, "adapter"):
                spec = content.model_copy(deep=True)
                ws = (
                    Path(spec.uri).parent.resolve()
                    if spec.uri and Path(spec.uri).is_file()
                    else Path.cwd()
                )
            else:
                p = Path(content)
                if not p.is_file():
                    continue
                spec = SourceSpec(
                    adapter="file",
                    uri=str(p.resolve()),
                )
                ws = p.parent.resolve()
            import duckdb

            con = duckdb.connect(":memory:")
            try:
                adapter = get_source_registry().get_tabular_adapter(spec.adapter, workspace_root=ws)
                adapter.register(spec, con, "_compiler_bronze_table")
                cur = con.execute('SELECT * FROM "_compiler_bronze_table"')
                cols = [d[0] for d in cur.description]
                rows = [dict(zip(cols, r, strict=False)) for r in cur.fetchall()]
            finally:
                con.close()
        if rows:
            for k in keys:
                normalized[k] = rows
            raw_s = str(raw_name)
            short_k = raw_s.split(".")[-1]
            normalized[raw_s] = rows
            normalized[raw_s.lower()] = rows
            normalized.setdefault(short_k, rows)
            normalized.setdefault(f"bronze.{short_k}", rows)
            normalized.setdefault(f"stg_{short_k}", rows)
    return normalized


def _filter_hub_rows_for_bkcc(
    hub: IRHub,
    rows: list[list[str]],
    bkcc_lit: str | None,
) -> list[list[str]]:
    """Filter `rows` for `hub` to those matching `bkcc_lit` when `hub` has an `is_bkcc` column."""
    bkcc_idx = next((i for i, bk in enumerate(hub.business_keys) if bk.is_bkcc), None)
    if bkcc_idx is None or not bkcc_lit:
        return rows
    target = bkcc_lit.strip().upper()
    matched = [r for r in rows if len(r) > bkcc_idx and r[bkcc_idx].strip().upper() == target]
    return matched or rows


def _format_bronze_payload_sql_literal(
    col: IRColumnDef,
    row: dict[str, Any] | None,
    row_idx: int,
) -> str:
    """Format a column value from a `loaded_bronze` row as a SQL literal, or fall back to seed."""
    if row is None or col.name not in row or row[col.name] is None:
        return _satellite_seed_sql_literal(col, row_idx)
    s_val = str(row[col.name]).strip()
    if not s_val:
        return _satellite_seed_sql_literal(col, row_idx)

    col_lower = col.name.lower()
    if col_lower in {
        "prompt_template_sha256",
        "extraction_confidence",
        "confidence_score",
        "human_review_status",
        "routing_action",
    }:
        return _satellite_seed_sql_literal(col, row_idx)

    norm_type = _normalize_duckdb_type(col.data_type)
    if norm_type == "BOOLEAN":
        return "TRUE" if s_val.lower() in {"true", "1", "t", "yes", "y"} else "FALSE"
    if norm_type in {"INTEGER", "BIGINT"}:
        try:
            return str(int(float(s_val)))
        except ValueError:
            return _satellite_seed_sql_literal(col, row_idx)
    if norm_type.startswith(("DECIMAL", "NUMERIC", "DOUBLE")):
        try:
            return f"{float(s_val):.2f}"
        except ValueError:
            return _satellite_seed_sql_literal(col, row_idx)
    if norm_type == "DATE":
        if len(s_val) >= 10 and s_val[4:5] == "-":
            esc_d = s_val[:10].replace("'", "''")
            return f"DATE '{esc_d}'"
        return _satellite_seed_sql_literal(col, row_idx)
    if norm_type == "TIMESTAMP":
        if len(s_val) >= 10 and s_val[4:5] == "-":
            esc_ts = s_val.replace("'", "''")
            return f"TIMESTAMP '{esc_ts}'"
        return _satellite_seed_sql_literal(col, row_idx)
    esc = s_val.replace("'", "''")
    return f"'{esc}'"


def _resolve_hub_bk_rows_from_bronze(
    hub: IRHub,
    loaded_bronze: dict[str, list[dict[str, Any]]],
    *,
    ir_obj: CanonicalIR | None = None,
    bkcc_registry: dict[tuple[str, str, str], str | None] | None = None,
) -> list[list[str]]:
    """Resolve distinct business key token tuples for `hub` from `loaded_bronze` or defaults."""
    distinct_sm_bkccs: list[str | None] = []
    for sm in hub.source_mappings:
        eff_lit = _effective_hub_sm_bkcc_literal(hub, sm)
        if eff_lit not in distinct_sm_bkccs:
            distinct_sm_bkccs.append(eff_lit)
    primary_bkcc = distinct_sm_bkccs[0] if distinct_sm_bkccs else None
    secondary_bkcc = distinct_sm_bkccs[1] if len(distinct_sm_bkccs) > 1 else primary_bkcc
    default_r1 = _hub_seed_bk_values(hub, 1, bkcc_override=primary_bkcc)
    default_r2 = _hub_seed_bk_values(hub, 2, bkcc_override=secondary_bkcc)
    if not loaded_bronze:
        if len(distinct_sm_bkccs) > 1:
            extra_rows: list[list[str]] = [default_r1, default_r2]
            for extra_bkcc in distinct_sm_bkccs:
                r_a = _hub_seed_bk_values(hub, 1, bkcc_override=extra_bkcc)
                r_b = _hub_seed_bk_values(hub, 2, bkcc_override=extra_bkcc)
                if r_a not in extra_rows:
                    extra_rows.append(r_a)
                if r_b not in extra_rows:
                    extra_rows.append(r_b)
            return extra_rows
        if any(bk.name in {"portco_natural_key", "lei", "lei_code"} for bk in hub.business_keys):
            return [default_r1]
        return [default_r1, default_r2]

    extracted: list[list[str]] = []
    seen_tuples: set[tuple[str, ...]] = set()
    hub_bkcc_col_expr = _effective_hub_bkcc_column_expr(hub)
    hub_bkcc_lit = _effective_hub_bkcc_literal(hub)

    for sm in hub.source_mappings:
        stg_tbl = sm.staging_table
        sm_rows: list[dict[str, Any]] = []
        lookup_candidates = list(_table_lookup_keys(stg_tbl))
        if getattr(sm, "bronze_table", None):
            for k in _table_lookup_keys(str(sm.bronze_table)):
                if k not in lookup_candidates:
                    lookup_candidates.append(k)
        for extra_k in (
            f"bronze.{stg_tbl.removeprefix('stg_')}",
            hub.name,
            f"src_{hub.name}",
        ):
            if extra_k not in lookup_candidates:
                lookup_candidates.append(extra_k)
        for key in lookup_candidates:
            if key in loaded_bronze:
                sm_rows = loaded_bronze[key]
                break
        if not sm_rows:
            continue

        sm_bkcc_col = getattr(sm, "bkcc_column_expr", None) or hub_bkcc_col_expr
        excluded_bkcc_names = {"bkcc"}
        if sm_bkcc_col:
            excluded_bkcc_names.add(sm_bkcc_col.lower())
        raw_src_bk_cols = list(sm.bk_source_columns)
        src_bk_cols = [
            c for c in raw_src_bk_cols if c.lower() not in excluded_bkcc_names
        ] or raw_src_bk_cols
        primary_src_col = src_bk_cols[0] if src_bk_cols else "*"
        if ir_obj is not None and not sm_bkcc_col:
            scoped_bkcc = resolve_staging_bkcc_literal(
                ir_obj,
                getattr(sm, "bronze_table", None) or sm.staging_table,
                primary_src_col,
                hub.name,
                registry=bkcc_registry,
            )
        else:
            scoped_bkcc = None

        for row in sm_rows:
            tokens: list[str] = []
            valid_row = True
            for bk in hub.business_keys:
                if bk.is_bkcc:
                    if sm_bkcc_col and sm_bkcc_col in row and row[sm_bkcc_col] is not None:
                        val = str(row[sm_bkcc_col]).strip()
                    elif scoped_bkcc is not None:
                        val = scoped_bkcc
                    elif hub_bkcc_lit is not None:
                        val = hub_bkcc_lit
                    else:
                        val = (
                            str(row.get("bkcc", "")).strip()
                            if "bkcc" in row
                            else getattr(sm, "bkcc_literal", None) or "GLOBAL"
                        )
                    tokens.append(val.strip().upper())
                else:
                    target_col = None
                    for c_cand in src_bk_cols:
                        if c_cand in row and row[c_cand] is not None:
                            target_col = c_cand
                            break
                    if target_col is None:
                        for c_cand in (bk.name, f"{hub.name}_id", "id", "code"):
                            if c_cand in row and row[c_cand] is not None:
                                target_col = c_cand
                                break
                    if target_col is None:
                        valid_row = False
                        break
                    raw_val = row[target_col]
                    if raw_val is None or str(raw_val).strip() in {
                        "",
                        "-1",
                        "-2",
                        "-1:^^",
                        "-2:^^",
                    }:
                        valid_row = False
                        break
                    tokens.append(str(raw_val).strip())
            if valid_row and len(tokens) == len(hub.business_keys):
                tup = tuple(tokens)
                if tup not in seen_tuples:
                    seen_tuples.add(tup)
                    extracted.append(tokens)

    if len(extracted) >= 2:
        return extracted
    if len(extracted) == 1:
        return [extracted[0], default_r2]
    return [default_r1, default_r2]


__all__ = [
    "_filter_hub_rows_for_bkcc",
    "_format_bronze_payload_sql_literal",
    "_load_bronze_table_rows",
    "_resolve_hub_bk_rows_from_bronze",
]
