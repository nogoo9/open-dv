"""Local duckdb_local & parquet materializer and AST-hardened read-only query guard.

Strictly Layer 2a: imports only opendv.models, duckdb, sqlglot, openpyxl, csv, sqlite3,
pyarrow, and sibling storage modules. Zero opendv.sources imports.
"""

from __future__ import annotations

import csv
import sqlite3
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb
import pyarrow.parquet as pq
import sqlglot

from opendv.models.errors import ErrorCode, OpenDVError
from opendv.models.project import EvolutionLockSpec, TargetManifest
from opendv.models.sources import SourceKind
from opendv.models.vault_ir_certified import CertifiedVaultIR
from opendv.models.wire_dtos_vault import MigrationApplyResp, SqlQueryResult
from opendv.storage._eval_runtime import arrow_table_to_pylist_utc
from opendv.storage._readonly_sql_guard import validate_readonly_query_sql
from opendv.storage.emitters.parquet import ParquetEmitter
from opendv.storage.eval_gate import TargetEvalGate
from opendv.storage.prover.edge_batches import _insert_rows
from opendv.storage.sql_compiler.compiler import SqlGlotVaultCompiler
from opendv.storage.sql_compiler.ddl_builders import qualify_name


def _json_safe_cell(val: Any) -> str | int | float | bool | None:
    if val is None or isinstance(val, (str, int, float, bool)):
        return val
    if isinstance(val, (bytes, bytearray, memoryview)):
        return bytes(val).hex()
    if isinstance(val, Decimal):
        return float(val)
    if isinstance(val, (datetime, date)):
        return val.isoformat()
    return str(val)


def execute_readonly_query(
    db_path: Path,
    sql: str,
    *,
    limit: int = 1000,
    timeout_seconds: float = 15.0,
    memory_limit: str = "256MB",
) -> SqlQueryResult:
    """Execute a validated read-only query against a DuckDB database file."""
    validate_readonly_query_sql(sql)
    if not db_path.exists():
        raise OpenDVError(
            f"DuckDB database not found: {db_path}",
            error_code=ErrorCode.RESOURCE_NOT_FOUND,
        )

    conn = duckdb.connect(
        str(db_path),
        read_only=True,
        config={
            "memory_limit": memory_limit,
            "threads": 2,
            "enable_external_access": False,
            "lock_configuration": True,
        },
    )
    timer = threading.Timer(timeout_seconds, conn.interrupt)
    timer.daemon = True
    timer.start()
    try:
        inner = sql.strip()
        rel = conn.sql(inner)
        cols = list(rel.columns)
        aliases = [f"_c{i}" for i in range(len(cols))]
        proj = [
            (
                f"STRFTIME({a} AT TIME ZONE 'UTC', '%Y-%m-%dT%H:%M:%S.%fZ')"
                if (dt_u := str(dt).upper()) in ("TIMESTAMP WITH TIME ZONE", "TIMESTAMPTZ")
                else (f"CAST({a} AS VARCHAR)" if "TIME ZONE" in dt_u else a)
            )
            for a, dt in zip(aliases, rel.dtypes, strict=True)
        ]
        wrapped = (
            f"SELECT {', '.join(proj)} FROM ({inner}\n) AS _opendv_q({', '.join(aliases)}) "
            f"LIMIT {int(limit)}"
        )
        raw_rows = conn.execute(wrapped).fetchall()
        safe_rows = [[_json_safe_cell(cell) for cell in row] for row in raw_rows]
        return SqlQueryResult(columns=cols, rows=safe_rows, row_count=len(safe_rows))
    except duckdb.Error as exc:
        raise OpenDVError(
            f"DuckDB query execution failed: {exc}",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        ) from exc
    finally:
        timer.cancel()
        conn.close()


@dataclass(frozen=True, slots=True)
class SourceAttachment:
    """Physical source attachment descriptor for local DuckDB/Parquet materialization."""

    source_id: str
    kind: SourceKind
    path: Path
    table_name: str | None = None


def _read_real_source_tables(
    attachments: Sequence[SourceAttachment],
    *,
    metadata_only: bool,
) -> dict[str, list[dict[str, Any]]]:
    """Load strictly real attached rows from sqlite/duckdb/parquet/csv/xlsx."""
    rows_by_table_id: dict[str, list[dict[str, Any]]] = {}
    if metadata_only:
        return rows_by_table_id

    for att in attachments:
        if not att.path.exists():
            raise OpenDVError(
                f"Source binary absent for '{att.source_id}' at {att.path}",
                error_code=ErrorCode.SOURCE_BINARY_ABSENT,
            )
        if att.kind == "sqlite":
            sconn = sqlite3.connect(str(att.path))
            try:
                q_tbls = (
                    "SELECT name FROM sqlite_master "
                    "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                )
                tbls = [r[0] for r in sconn.execute(q_tbls).fetchall()]
                for tname in tbls:
                    cur = sconn.execute(f'SELECT * FROM "{tname}"')
                    cols = [d[0] for d in (cur.description or ())]
                    rows_by_table_id[f"{att.source_id}:{tname}"] = [
                        dict(zip(cols, row, strict=True)) for row in cur.fetchall()
                    ]
            finally:
                sconn.close()
        elif att.kind == "duckdb":
            dconn = duckdb.connect(str(att.path), read_only=True)
            try:
                tbls = [
                    r[0]
                    for r in dconn.execute(
                        "SELECT table_name FROM information_schema.tables WHERE table_schema='main'"
                    ).fetchall()
                ]
                for tname in tbls:
                    atbl = dconn.sql(f'SELECT * FROM "{tname}"').to_arrow_table()
                    rows_by_table_id[f"{att.source_id}:{tname}"] = arrow_table_to_pylist_utc(atbl)
            finally:
                dconn.close()
        elif att.kind == "parquet":
            tname = att.table_name or att.path.stem
            ptbl = pq.read_table(att.path)
            rows_by_table_id[f"{att.source_id}:{tname}"] = arrow_table_to_pylist_utc(ptbl)
        elif att.kind == "csv":
            tname = att.table_name or att.path.stem
            with att.path.open("r", encoding="utf-8", newline="") as fh:
                reader = csv.DictReader(fh)
                rows_by_table_id[f"{att.source_id}:{tname}"] = [
                    {k: (None if v == "" else v) for k, v in r.items()} for r in reader
                ]
        elif att.kind == "xlsx":
            import openpyxl  # type: ignore[import-untyped]

            wb = openpyxl.load_workbook(att.path, read_only=True, data_only=True)
            try:
                for sheet in wb.sheetnames:
                    ws = wb[sheet]
                    it = ws.iter_rows(values_only=True)
                    headers = [str(h) for h in next(it, ())]
                    if headers:
                        rows_by_table_id[f"{att.source_id}:{sheet}"] = [
                            dict(zip(headers, row, strict=True)) for row in it
                        ]
            finally:
                wb.close()
    return rows_by_table_id


def materialize_duckdb_local(
    ir: CertifiedVaultIR,
    output_dir: Path,
    attachments: Sequence[SourceAttachment] = (),
    *,
    prior_ir: CertifiedVaultIR | None = None,
    locks: EvolutionLockSpec | None = None,
    metadata_only: bool = False,
    regenerate: bool = False,
) -> MigrationApplyResp:
    """Compile, validate via TargetEvalGate, and materialize duckdb_local with real rows."""
    db_path = output_dir / f"{ir.project_id}.duckdb"
    if regenerate and output_dir.exists():
        for child in list(output_dir.iterdir()):
            if child.is_dir():
                for sub in sorted(child.rglob("*"), reverse=True):
                    sub.unlink() if sub.is_file() else sub.rmdir()
                child.rmdir()
            else:
                child.unlink()
    output_dir.mkdir(parents=True, exist_ok=True)

    compiler = SqlGlotVaultCompiler("duckdb_local")
    bundle = compiler.compile(
        ir,
        prior_ir=prior_ir,
        locks=locks,
        output_dir=output_dir,
        verification_tier_override="RUNTIME_VERIFIED",
    )

    gate = TargetEvalGate()
    gate.verify_sql_ast("duckdb_local", bundle.full_sql, ir=ir)

    source_rows = _read_real_source_tables(attachments, metadata_only=metadata_only)
    if not regenerate and prior_ir is None and not db_path.exists():
        mig_sqls: Sequence[str] = ()
    elif prior_ir is not None and db_path.exists():
        prior_bundle = compiler.compile(prior_ir, locks=locks)
        mig_sqls = (prior_bundle.ddl_sql, bundle.migration_sql)
    else:
        mig_sqls = ()

    gate.verify_duckdb_runtime(
        ir,
        bundle.ddl_sql,
        bundle.dml_sql,
        migration_sqls=mig_sqls,
        source_rows_by_table=source_rows,
        physical_aliases=bundle.physical_aliases,
    )

    db_exists_prior = db_path.exists()
    conn = duckdb.connect(
        str(db_path),
        config={
            "memory_limit": "512MB",
            "threads": 2,
            "enable_external_access": False,
            "lock_configuration": True,
        },
    )
    rows_loaded = 0
    try:
        exec_ddl = (
            bundle.migration_sql
            if (prior_ir is not None and not regenerate and db_exists_prior)
            else bundle.ddl_sql
        )
        for stmt in sqlglot.parse(exec_ddl, read="duckdb"):
            if stmt is not None:
                conn.execute(stmt.sql(dialect="duckdb"))

        if not metadata_only:
            for b in ir.bronze_tables:
                rows = source_rows.get(b.source_table_id, ())
                if rows:
                    b_full = qualify_name("Z0", b.table_name, bundle.physical_aliases)
                    # Avoid duplicating identical Z0 rows on wave re-run if table already populated
                    existing_cnt_r = conn.execute(f"SELECT COUNT(*) FROM {b_full}").fetchone()
                    if existing_cnt_r and int(existing_cnt_r[0]) > 0 and prior_ir is not None:
                        continue
                    _insert_rows(conn, b_full, [c.name for c in b.columns], rows)
                    rows_loaded += len(rows)

        for stmt in sqlglot.parse(bundle.dml_sql, read="duckdb"):
            if stmt is not None:
                conn.execute(stmt.sql(dialect="duckdb"))
    finally:
        conn.close()

    manifest = TargetManifest(
        target_id="duckdb_local",
        last_compiled_wave=ir.wave_number,
        last_materialized_wave=ir.wave_number,
        verification_tier="RUNTIME_VERIFIED",
        certification_status="CERTIFIED",
        physical_aliases=bundle.physical_aliases,
        eval_receipts=["EVAL-AST-01", "EVAL-RUN-01", "EVAL-RUN-02", "EVAL-RUN-03", "EVAL-RUN-04"],
        generated_files=sorted([*bundle.files.keys(), db_path.name]),
    )
    (output_dir / "target_manifest.json").write_text(
        manifest.model_dump_json(indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    return MigrationApplyResp(
        target_id="duckdb_local",
        wave_number=ir.wave_number,
        rows_loaded=rows_loaded,
        verified=True,
    )


def materialize_parquet_local(
    ir: CertifiedVaultIR,
    output_dir: Path,
    attachments: Sequence[SourceAttachment] = (),
    *,
    locks: EvolutionLockSpec | None = None,
    metadata_only: bool = False,
    regenerate: bool = False,
) -> MigrationApplyResp:
    """Materialize ZSTD Parquet target with Zero/Ghost + real attached rows and EVAL-FMT-02."""
    if regenerate and output_dir.exists():
        for sub in sorted(output_dir.rglob("*"), reverse=True):
            sub.unlink() if sub.is_file() else sub.rmdir()
        output_dir.mkdir(parents=True, exist_ok=True)

    source_rows = _read_real_source_tables(attachments, metadata_only=metadata_only)
    rows_by_entity: dict[str, list[dict[str, Any]]] = {}
    rows_loaded = 0
    if not metadata_only:
        for b in ir.bronze_tables:
            rlist = source_rows.get(b.source_table_id, [])
            if rlist:
                rows_by_entity[b.table_name] = rlist
                rows_loaded += len(rlist)

    emitter = ParquetEmitter()
    emitter.emit(
        ir,
        output_dir,
        locks=locks,
        rows_by_entity=rows_by_entity,
        materialized_wave=ir.wave_number,
    )
    TargetEvalGate().verify_format_bundle("parquet", output_dir, ir)
    return MigrationApplyResp(
        target_id="parquet",
        wave_number=ir.wave_number,
        rows_loaded=rows_loaded,
        verified=True,
    )


__all__ = [
    "SourceAttachment",
    "execute_readonly_query",
    "materialize_duckdb_local",
    "materialize_parquet_local",
    "validate_readonly_query_sql",
]
