"""Databricks Pushdown Profiler.

Generates and executes a single batched remote aggregate query in the source
Databricks SQL warehouse avoiding full data egress, and streams bounded Arrow
batches for DuckDB shadow sandbox execution.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

import pyarrow as pa

from opendv_sources.config.defaults import get_source_defaults
from opendv_sources.models.descriptors import ColumnDescriptor, TabularProfileSummary
from opendv_sources.models.specs import SamplingPolicy, TableRef

_IDENTIFIER_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# Candidate BKCC regex patterns for Key Authority Tiers
DEFAULT_CANDIDATE_PATTERNS: dict[str, str] = {
    "isin": r"^[A-Z]{2}[A-Z0-9]{9}[0-9]$",
    "cusip": r"^[0-9A-Z]{9}$",
    "lei": r"^[A-Z0-9]{18}[0-9]{2}$",
    "cik": r"^[0-9]{1,10}$",
    "sedol": r"^[0-9B-DF-HJ-NP-TV-Z]{6}[0-9]$",
    "ticker": r"^[A-Z]{1,5}$",
}

_TEXT_TYPE_NAMES: set[str] = {
    "string",
    "varchar",
    "char",
    "text",
}


def _validate_safe_identifier(name: str) -> str:
    """Validate identifier against strict safe identifier rules to prevent SQL injection."""
    clean = name.strip('`" ')
    if not _IDENTIFIER_PATTERN.match(clean):
        raise ValueError(f"Unsafe SQL identifier: {name!r}")
    return clean


def _build_qualified_table_name(table: TableRef) -> str:
    """Construct backtick-quoted 3-level or 2-level qualified table reference."""
    parts: list[str] = []
    if table.namespace:
        if table.namespace.catalog:
            parts.append(f"`{_validate_safe_identifier(table.namespace.catalog)}`")
        if table.namespace.schema_name:
            parts.append(f"`{_validate_safe_identifier(table.namespace.schema_name)}`")
    parts.append(f"`{_validate_safe_identifier(table.table_name)}`")
    return ".".join(parts)


def _is_text_type(physical_type: str) -> bool:
    """Check whether a physical type represents string/text data."""
    clean_type = physical_type.lower().strip()
    return any(clean_type.startswith(t) for t in _TEXT_TYPE_NAMES)


class DatabricksPushdownProfiler:
    """Pushdown profiling engine executing remote batched aggregates in Databricks."""

    def __init__(
        self,
        candidate_patterns: dict[str, str] | None = None,
    ) -> None:
        self.candidate_patterns = (
            dict(candidate_patterns)
            if candidate_patterns is not None
            else DEFAULT_CANDIDATE_PATTERNS
        )

    def generate_pushdown_query(
        self,
        table: TableRef,
        columns: list[ColumnDescriptor],
    ) -> str:
        """Generate a single batched aggregate profiling query for all columns."""
        qualified_table = _build_qualified_table_name(table)
        select_expressions: list[str] = ["COUNT(*) AS __total_rows__"]

        for col in columns:
            safe_col = _validate_safe_identifier(col.name)
            col_ref = f"`{safe_col}`"
            select_expressions.append(
                f"APPROX_COUNT_DISTINCT({col_ref}) AS `{safe_col}__approx_distinct`"
            )
            select_expressions.append(f"COUNT_IF({col_ref} IS NULL) AS `{safe_col}__null_count`")

            if _is_text_type(col.physical_type):
                cast_expr = f"CAST({col_ref} AS STRING)"
                select_expressions.append(f"MIN(LENGTH({cast_expr})) AS `{safe_col}__min_len`")
                select_expressions.append(f"MAX(LENGTH({cast_expr})) AS `{safe_col}__max_len`")

                for pat_name, pat_regex in self.candidate_patterns.items():
                    escaped_regex = pat_regex.replace("'", "''")
                    pred = f"{col_ref} IS NOT NULL AND REGEXP_LIKE({cast_expr}, '{escaped_regex}')"
                    alias = f"`{safe_col}__regex_{pat_name}`"
                    expr = f"AVG(CASE WHEN {pred} THEN 1.0 ELSE 0.0 END) AS {alias}"
                    select_expressions.append(expr)

        joined_selects = ",\n    ".join(select_expressions)
        return f"SELECT\n    {joined_selects}\nFROM {qualified_table}"

    def profile_table(
        self,
        cursor: Any,
        table: TableRef,
        columns: list[ColumnDescriptor],
    ) -> TabularProfileSummary:
        """Execute batched pushdown query and parse results into TabularProfileSummary."""
        query_sql = self.generate_pushdown_query(table, columns)
        cursor.execute(query_sql)
        row = cursor.fetchone()

        total_row_count = 0
        column_stats: dict[str, dict[str, Any]] = {}

        for col in columns:
            column_stats[col.name] = {
                "approx_distinct_count": 0,
                "null_count": 0,
                "null_ratio": 0.0,
                "min_length": None,
                "max_length": None,
                "regex_match_ratios": {},
            }

        if row is not None and cursor.description:
            col_names = [desc[0].strip("`") for desc in cursor.description]
            row_dict = dict(zip(col_names, row, strict=False))
            total_row_count = int(row_dict.get("__total_rows__") or 0)

            for col in columns:
                c_name = col.name
                stats = column_stats[c_name]
                approx_distinct = row_dict.get(f"{c_name}__approx_distinct")
                null_cnt = row_dict.get(f"{c_name}__null_count")

                stats["approx_distinct_count"] = (
                    int(approx_distinct) if approx_distinct is not None else 0
                )
                stats["null_count"] = int(null_cnt) if null_cnt is not None else 0
                stats["null_ratio"] = (
                    float(stats["null_count"]) / float(total_row_count)
                    if total_row_count > 0
                    else 0.0
                )

                if _is_text_type(col.physical_type):
                    min_len = row_dict.get(f"{c_name}__min_len")
                    max_len = row_dict.get(f"{c_name}__max_len")
                    stats["min_length"] = int(min_len) if min_len is not None else None
                    stats["max_length"] = int(max_len) if max_len is not None else None

                    regex_ratios: dict[str, float] = {}
                    for pat_name in self.candidate_patterns:
                        key = f"{c_name}__regex_{pat_name}"
                        val = row_dict.get(key)
                        if val is not None:
                            regex_ratios[pat_name] = round(float(val), 4)
                    stats["regex_match_ratios"] = regex_ratios

        return TabularProfileSummary(
            table_name=table.table_name,
            namespace=table.namespace,
            total_row_count=total_row_count,
            column_stats=column_stats,
            profile_timestamp=datetime.now(UTC).isoformat(),
            profiling_strategy_used="PUSHDOWN_SQL",
        )

    def fetch_arrow_sample(
        self,
        cursor: Any,
        table: TableRef,
        policy: SamplingPolicy,
        version_as_of: int | None = None,
    ) -> pa.Table:
        """Stream a bounded Arrow sample from Databricks SQL for DuckDB sandbox registration."""
        qualified_table = _build_qualified_table_name(table)
        version_clause = f" VERSION AS OF {int(version_as_of)}" if version_as_of is not None else ""
        max_rows = max(1, policy.max_sandbox_rows)

        if policy.strategy == "TABLESAMPLE_BERNOULLI":
            # Bernoulli sampling
            sample_pct = get_source_defaults().bernoulli_sample_percentage
            query_sql = (
                f"SELECT * FROM {qualified_table}{version_clause} "
                f"TABLESAMPLE ({sample_pct} PERCENT) LIMIT {max_rows}"
            )
        else:
            query_sql = f"SELECT * FROM {qualified_table}{version_clause} LIMIT {max_rows}"

        cursor.execute(query_sql)

        # Use fetchmany_arrow or fetchall_arrow depending on interface
        if hasattr(cursor, "fetchmany_arrow"):
            table_arrow = cursor.fetchmany_arrow(max_rows)
        elif hasattr(cursor, "fetchall_arrow"):
            table_arrow = cursor.fetchall_arrow()
        else:
            # Fallback to DB-API fetchall and construct pyarrow table
            rows = cursor.fetchall()
            col_names = [d[0] for d in cursor.description] if cursor.description else []
            table_arrow = pa.Table.from_arrays(
                [pa.array([r[i] for r in rows]) for i in range(len(col_names))],
                names=col_names,
            )

        if len(table_arrow) > max_rows:
            table_arrow = table_arrow.slice(0, max_rows)

        return table_arrow


__all__ = [
    "DEFAULT_CANDIDATE_PATTERNS",
    "DatabricksPushdownProfiler",
]
