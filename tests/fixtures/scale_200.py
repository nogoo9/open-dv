"""Deterministic 220-table / 11-cluster scale benchmark generator (`DS-ERP-REAL` / `UAT-S3-01`)."""

from __future__ import annotations

import random
import sqlite3
from pathlib import Path

from opendv.models.sources import ColumnProfile, TableProfile

SCALE_DOMAINS: tuple[str, ...] = (
    "orders",
    "executions",
    "custody",
    "settlement",
    "positions",
    "valuations",
    "treasury",
    "compliance",
    "stewardship",
    "collateral",
    "billing",
)


def build_scale_200_table_profiles(
    source_id: str = "scale_200",
    *,
    table_count: int = 220,
    cluster_count: int = 11,
    shuffle_seed: int | None = None,
) -> list[TableProfile]:
    """Synthesize 220 TableProfiles partitioned cleanly into 11 domain clusters (20/cluster)."""
    per_cluster = table_count // cluster_count
    remainder = table_count % cluster_count
    tables: list[TableProfile] = []

    for c_idx in range(cluster_count):
        domain = SCALE_DOMAINS[c_idx % len(SCALE_DOMAINS)]
        count = per_cluster + (1 if c_idx < remainder else 0)
        anchor_name = f"d{c_idx:02d}_{domain}_tbl_000"
        shared_cols = [f"{domain}_key_{k}" for k in range(6)]

        for t_idx in range(count):
            t_name = f"d{c_idx:02d}_{domain}_tbl_{t_idx:03d}"
            pk_col = f"{domain}_id"
            cols = [
                ColumnProfile(
                    col_name=pk_col,
                    sql_type="VARCHAR",
                    nullable=False,
                    is_pk=True,
                    pk_ord=1,
                    fk_ref=f"{anchor_name}.{pk_col}" if t_idx > 0 else None,
                    null_ratio=0.0,
                    distinct_count_est=100,
                    pii_tags=[],
                    sample_values=["ID-1", "ID-2"],
                )
            ]
            for s_col in shared_cols:
                cols.append(
                    ColumnProfile(
                        col_name=s_col,
                        sql_type="VARCHAR",
                        nullable=True,
                        is_pk=False,
                        pk_ord=None,
                        fk_ref=None,
                        null_ratio=0.0,
                        distinct_count_est=25,
                        pii_tags=[],
                        sample_values=["VAL-1"],
                    )
                )
            cols.append(
                ColumnProfile(
                    col_name="amount_dec",
                    sql_type="DECIMAL(18,4)",
                    nullable=True,
                    is_pk=False,
                    pk_ord=None,
                    fk_ref=None,
                    null_ratio=0.0,
                    distinct_count_est=50,
                    pii_tags=[],
                    sample_values=["100.0000"],
                )
            )
            tables.append(
                TableProfile(
                    table_id=f"{source_id}:{t_name}",
                    table_name=t_name,
                    row_count_est=100,
                    columns=cols,
                )
            )

    if shuffle_seed is not None:
        rng = random.Random(shuffle_seed)
        rng.shuffle(tables)
    return tables


def build_scale_200_sqlite(db_path: Path) -> Path:
    """Materialize the 220-table catalog into a SQLite database at db_path."""
    profiles = build_scale_200_table_profiles()
    db_path.unlink(missing_ok=True)
    conn = sqlite3.connect(str(db_path))
    try:
        for tbl in profiles:
            col_defs = []
            for col in tbl.columns:
                if col.is_pk:
                    col_defs.append(f'"{col.col_name}" TEXT PRIMARY KEY')
                else:
                    col_defs.append(f'"{col.col_name}" TEXT')
            conn.execute(f'CREATE TABLE "{tbl.table_name}" ({", ".join(col_defs)})')
        conn.commit()
    finally:
        conn.close()
    return db_path


__all__ = [
    "SCALE_DOMAINS",
    "build_scale_200_sqlite",
    "build_scale_200_table_profiles",
]
