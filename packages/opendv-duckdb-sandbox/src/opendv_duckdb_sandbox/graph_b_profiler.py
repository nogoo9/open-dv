"""Empirical Source Profiler (Graph B) and Schema Drift Detector for DuckDB.

Profiles relational tables, Parquet files, and CSV feeds inside an ephemeral DuckDB
instance to compute the empirical Graph B topology signals required by the Open-DV
synthesis pipeline (`docs/03` §2, `docs/05` §1-§2):
- `uniqueness_ratio` (`u_total = distinct_total / N`)
- `non_null_ratio` (`u_non_null = 1 - null_rate`, with `u_nn = distinct_non_null / non_null_rows`)
- `shannon_entropy_norm` (`H_norm(C) in [0, 1]`)
- `jaccard_inclusion` (`I(A subseteq B) = |V(A) cap V(B)| / |V(A)|`)
- `cdc_velocity_hz` (`v(c) = delta_mutations / delta_t_seconds`)
- `write_amp_mb_per_day` (daily satellite write amplification in MB/day)

Also detects $S_k(t) \\to S_k(t+1)$ schema drift across source snapshots (`ADD_COLUMN`,
`DROP_COLUMN`, `RENAME_COLUMN`, `TYPE_WIDENING`, `SEMANTIC_TYPE_CHANGE`).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import duckdb

from opendv_core.elm_calculus import (
    compute_asymmetric_containment,
    compute_cdc_velocity,
    compute_jaccard_inclusion,
    compute_jaccard_similarity,
    compute_non_null_ratio,
    compute_normalized_shannon_entropy,
    compute_null_rate,
    compute_shannon_entropy,
    compute_u_c,
    compute_u_nn,
    compute_uniqueness_ratio,
    compute_write_amplification_mb_per_day,
    evaluate_yagni_satellite_split,
)
from opendv_core.models.graph_b import (
    GraphBProfile,
    ProfiledBronzeTable,
    ProfiledSourceSystem,
    YagniTelemetry,
)
from opendv_duckdb_sandbox.graph_b_drift import (
    SchemaDriftEvent,
    SchemaDriftKind,
    SchemaDriftReport,
)
from opendv_duckdb_sandbox.graph_b_drift import (
    _classify_type_transition as _classify_type_transition,
)
from opendv_duckdb_sandbox.graph_b_drift import (
    _normalized_base_type as _normalized_base_type,
)
from opendv_duckdb_sandbox.graph_b_drift import (
    detect_schema_drift as _detect_schema_drift_impl,
)
from opendv_duckdb_sandbox.graph_b_models import (
    ColumnProfileMetrics,
    ForeignKeyInclusionEdge,
    _infer_column_role,
)

_FORBIDDEN_SQL_IDENT_TOKENS = (";", "--", "/*", "*/", "'", '"', "`", "\\")
_SAFE_IDENT_PUNCT = frozenset("_ -/()%#&+:,€£¥")


def _is_safe_ident_part(part: str, *, allow_dot: bool) -> bool:
    return bool(part.strip()) and all(
        ch.isalnum() or ch in _SAFE_IDENT_PUNCT or (allow_dot and ch == ".") for ch in part
    )


def _quote_identifier(identifier: str, *, allow_qualified: bool = True) -> str:
    """Validate and double-quote a SQL table or column identifier (optionally schema-qualified)."""
    if (
        not identifier
        or not identifier.strip()
        or any(tok in identifier for tok in _FORBIDDEN_SQL_IDENT_TOKENS)
    ):
        msg = "Unsafe SQL identifier rejected: identifier contains invalid characters"
        raise ValueError(msg)
    if allow_qualified:
        parts = identifier.split(".")
        if len(parts) > 2 or any(not _is_safe_ident_part(p, allow_dot=False) for p in parts):
            msg = "Unsafe SQL identifier rejected: identifier contains invalid characters"
            raise ValueError(msg)
        return ".".join(f'"{part}"' for part in parts)
    if not _is_safe_ident_part(identifier, allow_dot=True):
        msg = "Unsafe SQL identifier rejected: identifier contains invalid characters"
        raise ValueError(msg)
    return f'"{identifier}"'


class GraphBProfiler:
    """Profiles DuckDB tables, Parquet files, and CSV feeds into Graph B profiles."""

    def __init__(self, conn: duckdb.DuckDBPyConnection | None = None) -> None:
        self._owns_conn = conn is None
        self.conn: duckdb.DuckDBPyConnection = (
            conn if conn is not None else duckdb.connect(":memory:")
        )

    def close(self) -> None:
        """Close the underlying DuckDB connection if owned by this profiler."""
        if self._owns_conn:
            self.conn.close()

    def __enter__(self) -> GraphBProfiler:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def profile_table_columns(
        self,
        table_name: str,
        *,
        delta_mutations_by_column: dict[str, int] | None = None,
        window_seconds: float = 86400.0,
        parent_key_values_by_column: dict[str, set[Any]] | None = None,
        hash_overhead_bytes: int = 88,
    ) -> list[ColumnProfileMetrics]:
        """Compute empirical Graph B metrics for every column in `table_name`."""
        quoted_table = _quote_identifier(table_name)
        describe_rows = self.conn.execute(f"DESCRIBE {quoted_table}").fetchall()
        total_rows_row = self.conn.execute(f"SELECT COUNT(*) FROM {quoted_table}").fetchone()
        total_rows = int(total_rows_row[0]) if total_rows_row is not None else 0

        delta_map = delta_mutations_by_column or {}
        parent_map = parent_key_values_by_column or {}

        metrics_list: list[ColumnProfileMetrics] = []
        for col_row in describe_rows:
            col_name = str(col_row[0])
            col_type = str(col_row[1])
            quoted_col = _quote_identifier(col_name, allow_qualified=False)

            col_type_upper = col_type.upper()
            select_col = (
                f"CAST({quoted_col} AS VARCHAR)"
                if "WITH TIME ZONE" in col_type_upper or "TIMESTAMPTZ" in col_type_upper
                else quoted_col
            )
            raw_values = [
                r[0]
                for r in self.conn.execute(f"SELECT {select_col} FROM {quoted_table}").fetchall()
            ]
            non_null_values = [v for v in raw_values if v is not None]
            null_count = total_rows - len(non_null_values)

            distinct_non_null = len({str(v) for v in non_null_values})
            distinct_total = distinct_non_null + (1 if null_count > 0 else 0)

            u_total = round(compute_u_c(raw_values), 4)
            u_nn = round(compute_u_nn(raw_values), 4)
            uniqueness_ratio = round(compute_uniqueness_ratio(raw_values, over_non_null=False), 4)
            null_rate = round(compute_null_rate(raw_values), 4)
            non_null_ratio = round(compute_non_null_ratio(raw_values), 4)

            entropy_bits = round(compute_shannon_entropy(raw_values), 4)
            entropy_norm = round(compute_normalized_shannon_entropy(raw_values), 4)

            jaccard_inc = 0.0
            if col_name in parent_map:
                jaccard_inc = round(
                    compute_jaccard_inclusion(non_null_values, parent_map[col_name]),
                    4,
                )

            mutations = delta_map.get(col_name, 0)
            delta_t_days = max(window_seconds / 86400.0, 1e-6)
            lambda_per_day = round(
                compute_cdc_velocity(
                    cdc_update_count=mutations,
                    entity_count=max(total_rows, 1),
                    delta_t_days=delta_t_days,
                ),
                4,
            )
            cdc_vel_hz = round(
                float(mutations) / max(window_seconds, 1.0) if window_seconds > 0 else 0.0,
                8,
            )

            avg_width = (
                max(
                    1,
                    round(
                        sum(len(str(v).encode("utf-8")) for v in non_null_values)
                        / len(non_null_values)
                    ),
                )
                if non_null_values
                else 8
            )
            write_amp_mb = round(
                compute_write_amplification_mb_per_day(
                    daily_rows_changed=mutations,
                    slow_payload_width_bytes=avg_width + hash_overhead_bytes,
                    high_freq_change_probability=min(1.0, lambda_per_day),
                ),
                6,
            )

            sample_vals: list[Any] = []
            seen_samples: set[str] = set()
            for val in non_null_values:
                s_val = str(val)
                if s_val not in seen_samples:
                    seen_samples.add(s_val)
                    sample_vals.append(
                        val if isinstance(val, (int, float, bool, str)) else str(val)
                    )
                if len(sample_vals) >= 3:
                    break

            bk_verdict, dv2_role, enum_verdict = _infer_column_role(
                column_name=col_name,
                u_total=u_total,
                u_non_null=u_nn,
                null_rate=null_rate,
                shannon_entropy_norm=entropy_norm,
                distinct_non_null_count=distinct_non_null,
                jaccard_inclusion=jaccard_inc,
            )

            metrics_list.append(
                ColumnProfileMetrics(
                    column_name=col_name,
                    physical_type=col_type,
                    row_count=total_rows,
                    distinct_count=distinct_total,
                    distinct_non_null_count=distinct_non_null,
                    null_count=null_count,
                    uniqueness_ratio=uniqueness_ratio,
                    non_null_ratio=non_null_ratio,
                    u_total=u_total,
                    u_non_null=u_nn,
                    null_rate=null_rate,
                    shannon_entropy_bits=entropy_bits,
                    shannon_entropy_norm=entropy_norm,
                    jaccard_inclusion=jaccard_inc,
                    cdc_velocity_hz=cdc_vel_hz,
                    write_amp_mb_per_day=write_amp_mb,
                    update_frequency_lambda_per_day=lambda_per_day,
                    avg_column_width_bytes=avg_width,
                    sample_values=sample_vals,
                    bk_candidate_verdict=bk_verdict,
                    recommended_dv2_role=dv2_role,
                    enum_demotion_verdict=enum_verdict,
                    rationale=(
                        f"Profiled {total_rows} rows: u_total={u_total:.4f}, "
                        f"u_nn={u_nn:.4f}, H_norm={entropy_norm:.4f}"
                    ),
                )
            )

        return metrics_list

    def profile_relation(
        self,
        relation_name: str,
        *,
        delta_mutations_by_column: dict[str, int] | None = None,
        window_seconds: float = 86400.0,
        parent_key_values_by_column: dict[str, set[Any]] | None = None,
        hash_overhead_bytes: int = 88,
    ) -> list[ColumnProfileMetrics]:
        """Compute empirical Graph B metrics for columns in the registered relation (SRC-02)."""
        return self.profile_table_columns(
            relation_name,
            delta_mutations_by_column=delta_mutations_by_column,
            window_seconds=window_seconds,
            parent_key_values_by_column=parent_key_values_by_column,
            hash_overhead_bytes=hash_overhead_bytes,
        )

    def compute_table_inclusion_edge(
        self,
        *,
        edge_id: str,
        child_table: str,
        child_column: str,
        parent_table: str,
        parent_column: str,
        target_link: str = "lnk_inferred_relationship",
    ) -> ForeignKeyInclusionEdge:
        """Compute empirical Jaccard & asymmetric inclusion metrics between two DuckDB columns."""
        q_child_tbl = _quote_identifier(child_table)
        q_child_col = _quote_identifier(child_column, allow_qualified=False)
        q_parent_tbl = _quote_identifier(parent_table)
        q_parent_col = _quote_identifier(parent_column, allow_qualified=False)

        child_rows = [
            r[0] for r in self.conn.execute(f"SELECT {q_child_col} FROM {q_child_tbl}").fetchall()
        ]
        parent_rows = [
            r[0] for r in self.conn.execute(f"SELECT {q_parent_col} FROM {q_parent_tbl}").fetchall()
        ]

        child_non_null = [v for v in child_rows if v is not None]
        child_nulls = len(child_rows) - len(child_non_null)
        child_set = {str(v) for v in child_non_null}
        parent_set = {str(v) for v in parent_rows if v is not None}

        asym_containment = round(compute_asymmetric_containment(child_set, parent_set), 4)
        jaccard_sim = round(compute_jaccard_similarity(child_set, parent_set), 4)
        jaccard_inc = round(compute_jaccard_inclusion(child_set, parent_set), 4)
        orphan_count = sum(1 for v in child_non_null if str(v) not in parent_set)
        null_fk_rate = round(compute_null_rate(child_rows), 4)

        return ForeignKeyInclusionEdge(
            edge_id=edge_id,
            child_table=child_table,
            child_column=child_column,
            parent_table=parent_table,
            parent_column=parent_column,
            asymmetric_inclusion_i_c_in_p=asym_containment,
            jaccard_similarity=jaccard_sim,
            null_fk_rate=null_fk_rate,
            orphan_value_count=orphan_count,
            verdict="PROMOTED_LINK_FK" if asym_containment >= 0.80 else "CANDIDATE_LINK_FK",
            target_link=target_link,
            zero_key_policy=(
                "MAP_NULL_TO_0x00_UNKNOWN" if child_nulls > 0 else "STRICT_INNER_JOIN_READY"
            ),
            jaccard_inclusion=jaccard_inc,
        )

    def build_graph_b_profile(
        self,
        *,
        domain: str,
        source_system_id: str,
        bkcc_namespace: str,
        table_names: list[str],
        source_description: str = "Empirical DuckDB source profile",
        profile_timestamp_utc: str = "2026-10-15T14:22:00Z",
        delta_mutations_by_table: dict[str, dict[str, int]] | None = None,
        window_seconds: float = 86400.0,
    ) -> GraphBProfile:
        """Profile a set of DuckDB tables and assemble a structured `GraphBProfile`."""
        bronze_tables: list[ProfiledBronzeTable] = []
        delta_tables = delta_mutations_by_table or {}

        for tbl_name in table_names:
            quoted_tbl = _quote_identifier(tbl_name)
            row_count_res = self.conn.execute(f"SELECT COUNT(*) FROM {quoted_tbl}").fetchone()
            row_count = int(row_count_res[0]) if row_count_res is not None else 0
            col_metrics = self.profile_table_columns(
                tbl_name,
                delta_mutations_by_column=delta_tables.get(tbl_name),
                window_seconds=window_seconds,
            )
            profiled_cols = [m.to_profiled_column() for m in col_metrics]
            static_cols = [
                m.column_name for m in col_metrics if m.update_frequency_lambda_per_day <= 0.05
            ]
            volatile_cols = [
                m.column_name for m in col_metrics if m.update_frequency_lambda_per_day > 0.05
            ]
            high_freq_width = max(
                sum(
                    m.avg_column_width_bytes for m in col_metrics if m.column_name in volatile_cols
                ),
                16,
            )
            max_volatile_lambda = max(
                (
                    max(
                        m.update_frequency_lambda_per_day
                        for m in col_metrics
                        if m.column_name in volatile_cols
                    )
                    if volatile_cols
                    else 0.01
                ),
                0.01,
            )
            static_width = max(
                sum(m.avg_column_width_bytes for m in col_metrics if m.column_name in static_cols),
                16,
            )
            daily_delta = sum((delta_tables.get(tbl_name) or {}).values())
            write_amp_mb = round(
                compute_write_amplification_mb_per_day(
                    daily_rows_changed=max(daily_delta, row_count),
                    slow_payload_width_bytes=static_width,
                    high_freq_change_probability=min(1.0, max_volatile_lambda),
                ),
                4,
            )
            should_split = evaluate_yagni_satellite_split(write_amp_mb)
            yagni = YagniTelemetry(
                general_column_count=len(static_cols),
                high_freq_column_count=len(volatile_cols),
                high_freq_row_width_bytes=high_freq_width,
                high_freq_change_probability=min(1.0, round(max_volatile_lambda, 4)),
                write_amplification_mb_per_day=write_amp_mb,
                threshold_mb_per_day=1.0,
                gate4_verdict="SPLIT_JUSTIFIED" if should_split else "KEEP_UNSPLIT_YAGNI",
            )
            bronze_tables.append(
                ProfiledBronzeTable(
                    bronze_table=tbl_name,
                    row_count=row_count,
                    daily_delta_rows=sum((delta_tables.get(tbl_name) or {}).values()),
                    yagni_telemetry=yagni,
                    columns=profiled_cols,
                )
            )

        return GraphBProfile(
            profile_version="1.0.0",
            domain_namespace=domain.lower(),
            profiled_at_utc=datetime.fromisoformat(profile_timestamp_utc.replace("Z", "+00:00"))
            if profile_timestamp_utc
            else datetime.now(UTC),
            source_systems=[
                ProfiledSourceSystem(
                    source_system_code=source_system_id.upper(),
                    description=source_description,
                    tables=bronze_tables,
                )
            ],
        )

    def detect_schema_drift(
        self,
        table_t0: str,
        table_t1: str,
        *,
        rename_jaccard_threshold: float = 0.90,
    ) -> SchemaDriftReport:
        """Detect $S_k(t) \\to S_k(t+1)$ schema drift between two DuckDB tables/views."""
        return _detect_schema_drift_impl(
            self.conn,
            table_t0,
            table_t1,
            quote_identifier=_quote_identifier,
            rename_jaccard_threshold=rename_jaccard_threshold,
        )


def profile_relation(
    conn: duckdb.DuckDBPyConnection,
    relation_name: str,
    *,
    delta_mutations_by_column: dict[str, int] | None = None,
    window_seconds: float = 86400.0,
    parent_key_values_by_column: dict[str, set[Any]] | None = None,
    hash_overhead_bytes: int = 88,
) -> list[ColumnProfileMetrics]:
    """Compute empirical Graph B metrics for columns in the registered relation (SRC-02)."""
    profiler = GraphBProfiler(conn)
    return profiler.profile_relation(
        relation_name,
        delta_mutations_by_column=delta_mutations_by_column,
        window_seconds=window_seconds,
        parent_key_values_by_column=parent_key_values_by_column,
        hash_overhead_bytes=hash_overhead_bytes,
    )


__all__ = [
    "ColumnProfileMetrics",
    "ForeignKeyInclusionEdge",
    "GraphBProfiler",
    "SchemaDriftEvent",
    "SchemaDriftKind",
    "SchemaDriftReport",
    "profile_relation",
]
