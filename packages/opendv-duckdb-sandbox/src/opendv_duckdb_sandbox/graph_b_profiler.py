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

import re
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

import duckdb
from pydantic import BaseModel, ConfigDict, Field

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
    CandidateRoleLiteral,
    GraphBProfile,
    ProfiledBronzeTable,
    ProfiledColumn,
    ProfiledSourceSystem,
    YagniTelemetry,
)

_SAFE_SQL_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*(\.[A-Za-z_][A-Za-z0-9_]*)?$")


def _quote_identifier(identifier: str) -> str:
    """Validate and double-quote a SQL table or column identifier (optionally schema-qualified)."""
    if not _SAFE_SQL_IDENTIFIER_RE.match(identifier):
        msg = f"Unsafe SQL identifier rejected: {identifier!r}"
        raise ValueError(msg)
    parts = identifier.split(".")
    return ".".join(f'"{part}"' for part in parts)


class SchemaDriftKind(StrEnum):
    """Taxonomy of source schema drift transitions $S_k(t) \\to S_k(t+1)$ (`docs/05` §3.9)."""

    ADD_COLUMN = "ADD_COLUMN"
    DROP_COLUMN = "DROP_COLUMN"
    RENAME_COLUMN = "RENAME_COLUMN"
    TYPE_WIDENING = "TYPE_WIDENING"
    SEMANTIC_TYPE_CHANGE = "SEMANTIC_TYPE_CHANGE"


class SchemaDriftEvent(BaseModel):
    """Single detected schema drift transition between $S_k(t)$ and $S_k(t+1)$."""

    model_config = ConfigDict(extra="forbid")

    drift_kind: SchemaDriftKind
    source_table: str
    column_name: str
    previous_column_name: str | None = None
    previous_type: str | None = None
    new_type: str | None = None
    jaccard_value_overlap: float | None = Field(default=None, ge=0.0, le=1.0)
    recommended_action: str


class SchemaDriftReport(BaseModel):
    """Aggregate report of all $S_k(t) \\to S_k(t+1)$ schema drift events for a table."""

    model_config = ConfigDict(extra="forbid")

    source_table: str
    has_drift: bool
    events: list[SchemaDriftEvent] = Field(default_factory=list)


class ForeignKeyInclusionEdge(BaseModel):
    """Empirical Jaccard and asymmetric containment edge between child and parent columns."""

    model_config = ConfigDict(extra="allow")

    edge_id: str
    child_table: str
    child_column: str
    parent_table: str
    parent_column: str
    asymmetric_inclusion_i_c_in_p: float = Field(ge=0.0, le=1.0)
    jaccard_similarity: float = Field(ge=0.0, le=1.0)
    jaccard_inclusion: float = Field(ge=0.0, le=1.0)
    null_fk_rate: float = Field(ge=0.0, le=1.0)
    orphan_value_count: int = Field(ge=0)
    verdict: str
    target_link: str
    zero_key_policy: str


class ColumnProfileMetrics(BaseModel):
    """Empirical Graph B metrics for a single relational/Parquet/CSV column."""

    model_config = ConfigDict(extra="allow")

    column_name: str
    physical_type: str
    row_count: int = Field(ge=0)
    distinct_count: int = Field(ge=0)
    distinct_non_null_count: int = Field(ge=0)
    null_count: int = Field(ge=0)
    uniqueness_ratio: float = Field(ge=0.0, le=1.0)
    non_null_ratio: float = Field(ge=0.0, le=1.0)
    u_total: float = Field(ge=0.0, le=1.0)
    u_non_null: float = Field(ge=0.0, le=1.0)
    null_rate: float = Field(ge=0.0, le=1.0)
    shannon_entropy_bits: float = Field(ge=0.0)
    shannon_entropy_norm: float = Field(ge=0.0, le=1.0)
    jaccard_inclusion: float = Field(default=0.0, ge=0.0, le=1.0)
    cdc_velocity_hz: float = Field(default=0.0, ge=0.0)
    write_amp_mb_per_day: float = Field(default=0.0, ge=0.0)
    update_frequency_lambda_per_day: float = Field(default=0.0, ge=0.0)
    avg_column_width_bytes: int = Field(default=8, ge=1)
    sample_values: list[Any] = Field(default_factory=list)
    bk_candidate_verdict: str | None = None
    recommended_dv2_role: str | None = None
    enum_demotion_verdict: str | None = None
    rationale: str | None = None

    def to_profiled_column(self) -> ProfiledColumn:
        """Convert into an `opendv_core.models.graph_b.ProfiledColumn` model."""
        role_map: dict[str, CandidateRoleLiteral] = {
            "HUB_BUSINESS_KEY": "TIER_1_BUSINESS_KEY",
            "LINK_FOREIGN_KEY": "FOREIGN_KEY_REF",
            "SYSTEM_METADATA": "TECHNICAL_METADATA",
            "SURROGATE_SEQUENCE_DROP": "SYSTEM_SURROGATE_DROPPED",
            "SATELLITE_DESCRIPTIVE_ATTRIBUTE": "DESCRIPTIVE_PAYLOAD",
        }
        cand_role: CandidateRoleLiteral = role_map.get(
            self.recommended_dv2_role or "",
            "DESCRIPTIVE_PAYLOAD",
        )
        return ProfiledColumn(
            column_name=self.column_name,
            data_type=self.physical_type,
            u_total=self.u_total,
            u_non_null=self.u_non_null,
            null_rate=self.null_rate,
            shannon_entropy_bits=self.shannon_entropy_bits,
            drift_rate_lambda=self.update_frequency_lambda_per_day,
            inclusion_in_parent=self.jaccard_inclusion if self.jaccard_inclusion > 0 else None,
            candidate_role=cand_role,
        )


def _infer_column_role(
    column_name: str,
    u_total: float,
    u_non_null: float,
    null_rate: float,
    shannon_entropy_norm: float,
    distinct_non_null_count: int,
    jaccard_inclusion: float,
) -> tuple[str, str, str | None]:
    """Infer a candidate DV 2.0 column role and BK verdict from empirical Graph B metrics."""
    lower_name = column_name.lower()
    if lower_name in {"load_dts", "rec_src", "record_source", "extract_dts"}:
        return ("REJECTED_SYSTEM_METADATA", "SYSTEM_METADATA", None)
    if u_total >= 0.99 and null_rate == 0.0 and shannon_entropy_norm >= 0.85:
        if lower_name.endswith("_seq_id") or lower_name == "row_id":
            return ("REJECTED_SURROGATE_SEQUENCE", "SURROGATE_SEQUENCE_DROP", None)
        return ("PROMOTED_HUB_BK", "HUB_BUSINESS_KEY", None)
    if jaccard_inclusion >= 0.85 or lower_name.endswith(("_id", "_code", "_lei")):
        if u_non_null >= 0.95 and null_rate == 0.0:
            return ("PROMOTED_HUB_BK", "HUB_BUSINESS_KEY", None)
        return ("REJECTED_FK_DEPENDENCY", "LINK_FOREIGN_KEY", None)
    if distinct_non_null_count <= 12 and shannon_entropy_norm < 0.55:
        return (
            "REJECTED_LOW_CARDINALITY_ENUM",
            "SATELLITE_DESCRIPTIVE_ATTRIBUTE",
            "DEMOTE_TO_SATELLITE_OR_REF",
        )
    return ("REJECTED_DESCRIPTIVE_ATTRIBUTE", "SATELLITE_DESCRIPTIVE_ATTRIBUTE", None)


def _normalized_base_type(type_str: str) -> tuple[str, tuple[int, ...]]:
    """Parse a SQL type string into its base family and numeric precision/length tuple."""
    cleaned = type_str.strip().upper()
    match = re.match(r"^([A-Z0-9_ ]+?)(?:\(([\d,\s]+)\))?$", cleaned)
    if not match:
        return cleaned, ()
    base = match.group(1).strip()
    params_raw = match.group(2)
    params = (
        tuple(int(part.strip()) for part in params_raw.split(",") if part.strip().isdigit())
        if params_raw
        else ()
    )
    return base, params


_WIDENING_FAMILIES: dict[str, int] = {
    "TINYINT": 1,
    "SMALLINT": 2,
    "INT": 3,
    "INTEGER": 3,
    "INT4": 3,
    "BIGINT": 4,
    "INT8": 4,
    "HUGEINT": 5,
    "FLOAT": 6,
    "REAL": 6,
    "DOUBLE": 7,
}

_STRING_FAMILIES = {"CHAR", "CHARACTER", "VARCHAR", "TEXT", "STRING"}
_DECIMAL_FAMILIES = {"DECIMAL", "NUMERIC"}


def _classify_type_transition(old_type: str, new_type: str) -> SchemaDriftKind | None:
    """Classify a column type change as None (identical), TYPE_WIDENING, or SEMANTIC_TYPE_CHANGE."""
    if old_type.strip().upper() == new_type.strip().upper():
        return None
    old_base, old_params = _normalized_base_type(old_type)
    new_base, new_params = _normalized_base_type(new_type)

    if old_base in _STRING_FAMILIES and new_base in _STRING_FAMILIES:
        if not new_params:
            return SchemaDriftKind.TYPE_WIDENING
        if old_params and new_params and new_params[0] >= old_params[0]:
            return SchemaDriftKind.TYPE_WIDENING
        return SchemaDriftKind.SEMANTIC_TYPE_CHANGE

    if old_base in _DECIMAL_FAMILIES and new_base in _DECIMAL_FAMILIES:
        if len(old_params) >= 2 and len(new_params) >= 2:
            if new_params[0] >= old_params[0] and new_params[1] >= old_params[1]:
                return SchemaDriftKind.TYPE_WIDENING
        elif not old_params or not new_params:
            return SchemaDriftKind.TYPE_WIDENING
        return SchemaDriftKind.SEMANTIC_TYPE_CHANGE

    if old_base in _WIDENING_FAMILIES and new_base in _WIDENING_FAMILIES:
        if _WIDENING_FAMILIES[new_base] > _WIDENING_FAMILIES[old_base]:
            return SchemaDriftKind.TYPE_WIDENING
        return SchemaDriftKind.SEMANTIC_TYPE_CHANGE

    return SchemaDriftKind.SEMANTIC_TYPE_CHANGE


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

    def __exit__(self, *_exc: object) -> None:
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
            quoted_col = _quote_identifier(col_name)

            raw_values = [
                r[0]
                for r in self.conn.execute(f"SELECT {quoted_col} FROM {quoted_table}").fetchall()
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

    def profile_parquet(
        self,
        parquet_path: str | Path,
        *,
        view_name: str = "temp_profiled_parquet",
        **kwargs: Any,
    ) -> list[ColumnProfileMetrics]:
        """Register a Parquet file in DuckDB and compute Graph B metrics for all columns."""
        resolved = str(Path(parquet_path).resolve()).replace("'", "''")
        quoted_view = _quote_identifier(view_name)
        self.conn.execute(
            f"CREATE OR REPLACE TEMP VIEW {quoted_view} AS SELECT * FROM read_parquet('{resolved}')"
        )
        return self.profile_table_columns(view_name, **kwargs)

    def profile_csv(
        self,
        csv_path: str | Path,
        *,
        view_name: str = "temp_profiled_csv",
        **kwargs: Any,
    ) -> list[ColumnProfileMetrics]:
        """Register a CSV file in DuckDB and compute Graph B metrics for all columns."""
        resolved = str(Path(csv_path).resolve()).replace("'", "''")
        quoted_view = _quote_identifier(view_name)
        self.conn.execute(
            f"CREATE OR REPLACE TEMP VIEW {quoted_view} "
            f"AS SELECT * FROM read_csv_auto('{resolved}')"
        )
        return self.profile_table_columns(view_name, **kwargs)

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
        q_child_col = _quote_identifier(child_column)
        q_parent_tbl = _quote_identifier(parent_table)
        q_parent_col = _quote_identifier(parent_column)

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
        q_t0 = _quote_identifier(table_t0)
        q_t1 = _quote_identifier(table_t1)

        cols_t0 = {str(r[0]): str(r[1]) for r in self.conn.execute(f"DESCRIBE {q_t0}").fetchall()}
        cols_t1 = {str(r[0]): str(r[1]) for r in self.conn.execute(f"DESCRIBE {q_t1}").fetchall()}

        removed_candidates = [c for c in cols_t0 if c not in cols_t1]
        added_candidates = [c for c in cols_t1 if c not in cols_t0]

        events: list[SchemaDriftEvent] = []
        matched_removed: set[str] = set()
        matched_added: set[str] = set()

        # Check for RENAME_COLUMN via value-set Jaccard similarity >= threshold
        for add_col in added_candidates:
            q_add = _quote_identifier(add_col)
            add_vals = {
                str(r[0])
                for r in self.conn.execute(
                    f"SELECT {q_add} FROM {q_t1} WHERE {q_add} IS NOT NULL"
                ).fetchall()
            }
            if not add_vals:
                continue
            best_old: str | None = None
            best_jaccard = 0.0
            for rem_col in removed_candidates:
                if rem_col in matched_removed:
                    continue
                q_rem = _quote_identifier(rem_col)
                rem_vals = {
                    str(r[0])
                    for r in self.conn.execute(
                        f"SELECT {q_rem} FROM {q_t0} WHERE {q_rem} IS NOT NULL"
                    ).fetchall()
                }
                jaccard = compute_jaccard_similarity(rem_vals, add_vals)
                if jaccard >= rename_jaccard_threshold and jaccard > best_jaccard:
                    best_jaccard = jaccard
                    best_old = rem_col
            if best_old is not None:
                matched_removed.add(best_old)
                matched_added.add(add_col)
                events.append(
                    SchemaDriftEvent(
                        drift_kind=SchemaDriftKind.RENAME_COLUMN,
                        source_table=table_t1,
                        column_name=add_col,
                        previous_column_name=best_old,
                        previous_type=cols_t0[best_old],
                        new_type=cols_t1[add_col],
                        jaccard_value_overlap=round(best_jaccard, 4),
                        recommended_action=(
                            f"Aliasstaging column {add_col!r} -> {best_old!r} in Zone 1 staging "
                            "view to preserve downstream Satellite hash_diff continuity."
                        ),
                    )
                )

        # Remaining added columns -> ADD_COLUMN
        for add_col in added_candidates:
            if add_col in matched_added:
                continue
            events.append(
                SchemaDriftEvent(
                    drift_kind=SchemaDriftKind.ADD_COLUMN,
                    source_table=table_t1,
                    column_name=add_col,
                    new_type=cols_t1[add_col],
                    recommended_action=(
                        f"Isolate {add_col!r} ({cols_t1[add_col]}) into an additive extension "
                        "Satellite (Algorithm A.9a) so existing Satellite hash_diff history "
                        "remains immutable."
                    ),
                )
            )

        # Remaining removed columns -> DROP_COLUMN
        for rem_col in removed_candidates:
            if rem_col in matched_removed:
                continue
            events.append(
                SchemaDriftEvent(
                    drift_kind=SchemaDriftKind.DROP_COLUMN,
                    source_table=table_t1,
                    column_name=rem_col,
                    previous_type=cols_t0[rem_col],
                    recommended_action=(
                        f"Project CAST(NULL AS {cols_t0[rem_col]}) AS {rem_col} in Zone 1 "
                        "staging view; never DROP physical columns from historical Satellites."
                    ),
                )
            )

        # Shared columns -> check TYPE_WIDENING or SEMANTIC_TYPE_CHANGE
        for shared_col in cols_t0:
            if shared_col not in cols_t1:
                continue
            transition = _classify_type_transition(cols_t0[shared_col], cols_t1[shared_col])
            if transition == SchemaDriftKind.TYPE_WIDENING:
                events.append(
                    SchemaDriftEvent(
                        drift_kind=SchemaDriftKind.TYPE_WIDENING,
                        source_table=table_t1,
                        column_name=shared_col,
                        previous_type=cols_t0[shared_col],
                        new_type=cols_t1[shared_col],
                        recommended_action=(
                            f"Apply non-destructive ALTER COLUMN {shared_col} SET DATA TYPE "
                            f"{cols_t1[shared_col]} in Satellite; length-prefixed hash_diff "
                            "tokenization remains invariant."
                        ),
                    )
                )
            elif transition == SchemaDriftKind.SEMANTIC_TYPE_CHANGE:
                events.append(
                    SchemaDriftEvent(
                        drift_kind=SchemaDriftKind.SEMANTIC_TYPE_CHANGE,
                        source_table=table_t1,
                        column_name=shared_col,
                        previous_type=cols_t0[shared_col],
                        new_type=cols_t1[shared_col],
                        recommended_action=(
                            f"Escalate incompatible type change on {shared_col!r} "
                            f"({cols_t0[shared_col]} -> {cols_t1[shared_col]}) to a new versioned "
                            "Satellite column."
                        ),
                    )
                )

        return SchemaDriftReport(
            source_table=table_t1,
            has_drift=len(events) > 0,
            events=events,
        )
