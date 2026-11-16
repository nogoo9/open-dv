"""Inspection routines and schema extraction for Databricks Unity Catalog tables."""

from __future__ import annotations

import hashlib
import logging
from typing import Any

from opendv_sources.exceptions import SourceError, SourceLoadError, SourceNotFoundError
from opendv_sources.models.descriptors import (
    ColumnDescriptor,
    ForeignKeyHint,
    TableSchemaDescriptor,
)
from opendv_sources.models.specs import NamespaceRef

_logger = logging.getLogger(__name__)


def inspect_databricks_table(
    cursor: Any,
    safe_cat: str,
    safe_sch: str,
    safe_tbl: str,
    version_as_of: int | None = None,
) -> TableSchemaDescriptor:
    """Inspect Databricks Unity Catalog Delta table columns, constraints, and metadata."""
    col_query = (
        f"SELECT column_name, ordinal_position, data_type, is_nullable, comment "
        f"FROM `{safe_cat}`.information_schema.columns "
        f"WHERE table_catalog = '{safe_cat}' AND table_schema = '{safe_sch}' "
        f"AND table_name = '{safe_tbl}' ORDER BY ordinal_position"
    )
    try:
        cursor.execute(col_query)
        col_rows = cursor.fetchall()
    except Exception as err:
        raise SourceLoadError(
            f"Failed to inspect columns for `{safe_cat}`.`{safe_sch}`.`{safe_tbl}`: {err}"
        ) from err

    if not col_rows:
        raise SourceNotFoundError(
            f"Table '{safe_tbl}' not found in Databricks Unity Catalog `{safe_cat}`.`{safe_sch}`"
        )

    pk_query = (
        f"SELECT DISTINCT k.column_name, k.ordinal_position "
        f"FROM `{safe_cat}`.information_schema.table_constraints c "
        f"JOIN `{safe_cat}`.information_schema.key_column_usage k "
        f"  ON c.constraint_name = k.constraint_name AND c.table_name = k.table_name "
        f"WHERE c.table_catalog = '{safe_cat}' AND c.table_schema = '{safe_sch}' "
        f"  AND c.table_name = '{safe_tbl}' AND c.constraint_type = 'PRIMARY KEY' "
        f"ORDER BY k.ordinal_position"
    )
    try:
        cursor.execute(pk_query)
        pk_rows = cursor.fetchall()
    except SourceError:
        raise
    except Exception as exc:  # noqa: BLE001
        _logger.debug(
            "Failed to query Primary Keys for %s.%s.%s: %s",
            safe_cat,
            safe_sch,
            safe_tbl,
            exc,
        )
        pk_rows = []
    pk_columns = list(dict.fromkeys(str(r[0]) for r in pk_rows))
    pk_ordinals = {str(r[0]): int(r[1]) for r in pk_rows}

    fk_query = (
        f"SELECT DISTINCT "
        f"  c.constraint_name, "
        f"  k.column_name AS source_column, "
        f"  k.ordinal_position AS source_ordinal, "
        f"  rk.table_catalog AS target_catalog, "
        f"  rk.table_schema AS target_schema, "
        f"  rk.table_name AS target_table, "
        f"  rk.column_name AS target_column, "
        f"  rk.ordinal_position AS target_ordinal "
        f"FROM `{safe_cat}`.information_schema.table_constraints c "
        f"JOIN `{safe_cat}`.information_schema.key_column_usage k "
        f"  ON c.constraint_name = k.constraint_name AND c.table_name = k.table_name "
        f"LEFT JOIN `{safe_cat}`.information_schema.referential_constraints r "
        f"  ON c.constraint_name = r.constraint_name "
        f"LEFT JOIN `{safe_cat}`.information_schema.key_column_usage rk "
        f"  ON r.unique_constraint_name = rk.constraint_name "
        f"  AND k.ordinal_position = rk.ordinal_position "
        f"WHERE c.table_catalog = '{safe_cat}' AND c.table_schema = '{safe_sch}' "
        f"  AND c.table_name = '{safe_tbl}' AND c.constraint_type = 'FOREIGN KEY' "
        f"ORDER BY c.constraint_name, k.ordinal_position"
    )
    try:
        cursor.execute(fk_query)
        fk_rows = cursor.fetchall()
    except SourceError:
        raise
    except Exception as exc:  # noqa: BLE001
        _logger.debug("Failed full Foreign Key query, trying simplified: %s", exc)
        try:
            cursor.execute(
                f"SELECT DISTINCT c.constraint_name, k.column_name "
                f"FROM `{safe_cat}`.information_schema.table_constraints c "
                f"JOIN `{safe_cat}`.information_schema.key_column_usage k "
                f"  ON c.constraint_name = k.constraint_name AND c.table_name = k.table_name "
                f"WHERE c.table_catalog = '{safe_cat}' AND c.table_schema = '{safe_sch}' "
                f"  AND c.table_name = '{safe_tbl}' AND c.constraint_type = 'FOREIGN KEY'"
            )
            simple_rows = cursor.fetchall()
            fk_rows = [(r[0], r[1], 1, None, None, None, None, None) for r in simple_rows]
        except SourceError:
            raise
        except Exception as exc_simple:  # noqa: BLE001
            _logger.debug("Failed simplified Foreign Key query: %s", exc_simple)
            fk_rows = []

    foreign_keys: list[ForeignKeyHint] = []
    fk_by_col: dict[str, ForeignKeyHint] = {}
    from collections import defaultdict

    fk_groups: dict[str, dict[str, Any]] = defaultdict(
        lambda: {
            "target_catalog": None,
            "target_schema": None,
            "target_table": None,
            "source_columns": [],
            "target_columns": [],
        }
    )
    for row in fk_rows:
        c_name = str(row[0])
        s_col = str(row[1])
        s_ord = int(row[2]) if row[2] is not None else 1
        t_cat = str(row[3]) if row[3] is not None else None
        t_sch = str(row[4]) if row[4] is not None else None
        t_tbl = str(row[5]) if row[5] is not None else None
        t_col = str(row[6]) if row[6] is not None else None
        t_ord = int(row[7]) if row[7] is not None else s_ord

        grp = fk_groups[c_name]
        if (s_ord, s_col) not in grp["source_columns"]:
            grp["source_columns"].append((s_ord, s_col))
        if t_col and (t_ord, t_col) not in grp["target_columns"]:
            grp["target_columns"].append((t_ord, t_col))
        if t_tbl and not grp["target_table"]:
            grp["target_table"] = t_tbl
        if t_cat and not grp["target_catalog"]:
            grp["target_catalog"] = t_cat
        if t_sch and not grp["target_schema"]:
            grp["target_schema"] = t_sch

    for c_name, grp in fk_groups.items():
        grp["source_columns"].sort(key=lambda x: x[0])
        grp["target_columns"].sort(key=lambda x: x[0])
        src_cols = [c for _, c in grp["source_columns"]]
        tgt_cols = [c for _, c in grp["target_columns"]]

        tgt_tbl = grp["target_table"]
        if not tgt_tbl:
            tgt_tbl = c_name.replace(f"fk_{safe_tbl}_", "")
            for col in src_cols:
                if tgt_tbl.startswith(f"{col}_"):
                    tgt_tbl = tgt_tbl[len(f"{col}_") :]
        if not tgt_cols:
            tgt_cols = list(src_cols)

        primary_src = src_cols[0] if src_cols else ""
        primary_tgt = tgt_cols[0] if tgt_cols else primary_src

        hint = ForeignKeyHint(
            target_table=tgt_tbl,
            target_column=primary_tgt,
            source_column=primary_src,
            source_columns=src_cols,
            target_columns=tgt_cols,
            constraint_name=c_name,
            target_namespace=(
                NamespaceRef(catalog=grp["target_catalog"], schema_name=grp["target_schema"])
                if grp["target_schema"]
                else None
            ),
        )
        foreign_keys.append(hint)
        for col_n in src_cols:
            fk_by_col[col_n] = hint

    tag_query = (
        f"SELECT column_name, tag_name, tag_value "
        f"FROM `{safe_cat}`.information_schema.column_tags "
        f"WHERE catalog_name = '{safe_cat}' AND schema_name = '{safe_sch}' "
        f"AND table_name = '{safe_tbl}'"
    )
    column_tags: dict[str, dict[str, str]] = {}
    try:
        cursor.execute(tag_query)
        for t_col, t_k, t_v in cursor.fetchall():
            col_k = str(t_col)
            if col_k not in column_tags:
                column_tags[col_k] = {}
            column_tags[col_k][str(t_k)] = str(t_v)
    except SourceError:
        raise
    except Exception as exc:  # noqa: BLE001
        _logger.debug(
            "Failed to query column tags for %s.%s.%s: %s",
            safe_cat,
            safe_sch,
            safe_tbl,
            exc,
        )

    columns: list[ColumnDescriptor] = []
    for c_row in col_rows:
        c_name = str(c_row[0])
        c_type = str(c_row[2])
        c_null = str(c_row[3]).upper() in ("YES", "TRUE", "1")
        c_comment = c_row[4]
        columns.append(
            ColumnDescriptor(
                name=c_name,
                physical_type=c_type,
                nullable=c_null,
                is_primary_key=c_name in pk_columns,
                primary_key_ordinal=pk_ordinals.get(c_name),
                foreign_key_ref=fk_by_col.get(c_name),
                description=c_comment,
                governance_tags=column_tags.get(c_name, {}),
            )
        )

    table_version = 1
    num_rows_estimate = None

    # 1. Run DESCRIBE DETAIL first (extracts statistics: numrecords and baseline version)
    try:
        cursor.execute(f"DESCRIBE DETAIL `{safe_cat}`.`{safe_sch}`.`{safe_tbl}`")
        detail_row = cursor.fetchone()
        if detail_row and cursor.description:
            col_keys = [d[0].lower() for d in cursor.description]
            detail_dict = dict(zip(col_keys, detail_row, strict=False))
            if "version" in detail_dict and detail_dict["version"] is not None:
                table_version = int(detail_dict["version"])
            if "numrecords" in detail_dict and detail_dict["numrecords"] is not None:
                num_rows_estimate = int(detail_dict["numrecords"])
            elif "num_records" in detail_dict and detail_dict["num_records"] is not None:
                num_rows_estimate = int(detail_dict["num_records"])
    except SourceError:
        raise
    except Exception as exc:  # noqa: BLE001
        _logger.debug(
            "Failed to query Delta detail for %s.%s.%s: %s",
            safe_cat,
            safe_sch,
            safe_tbl,
            exc,
        )

    # 2. Run DESCRIBE HISTORY second (refines or confirms latest version)
    try:
        cursor.execute(f"DESCRIBE HISTORY `{safe_cat}`.`{safe_sch}`.`{safe_tbl}` LIMIT 1")
        hist_row = cursor.fetchone()
        if hist_row and hist_row[0] is not None:
            table_version = int(hist_row[0])
        elif hist_row and cursor.description:
            col_keys = [d[0].lower() for d in cursor.description]
            if "version" in col_keys:
                v_idx = col_keys.index("version")
                if hist_row[v_idx] is not None:
                    table_version = int(hist_row[v_idx])
    except SourceError:
        raise
    except Exception as exc:  # noqa: BLE001
        _logger.debug(
            "Failed to query Delta history for %s.%s.%s: %s",
            safe_cat,
            safe_sch,
            safe_tbl,
            exc,
        )

    if version_as_of is not None:
        table_version = int(version_as_of)

    fp_components = [
        safe_cat,
        safe_sch,
        safe_tbl,
        f"v:{table_version}",
        ",".join(f"{c.name}:{c.physical_type}:{c.nullable}" for c in columns),
    ]
    fingerprint = hashlib.sha256(":".join(fp_components).encode("utf-8")).hexdigest()

    return TableSchemaDescriptor(
        table_name=safe_tbl,
        namespace=NamespaceRef(catalog=safe_cat, schema_name=safe_sch),
        columns=columns,
        primary_key_columns=pk_columns,
        foreign_keys=foreign_keys,
        row_count_estimate=num_rows_estimate,
        fingerprint=fingerprint,
        comment=f"Databricks Delta table {safe_cat}.{safe_sch}.{safe_tbl} v{table_version}",
    )


__all__ = ["inspect_databricks_table"]
