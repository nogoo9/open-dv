"""Open-DV V2 Layer-2a storage package: 11-target compilers, AST verifier, DuckDB prover."""

from __future__ import annotations

from opendv.storage.dialects import (
    ALL_TARGET_IDS,
    DIALECT_PROFILES,
    SQL_TARGET_IDS,
    SqlDialectProfile,
    TargetId,
    assign_physical_aliases,
    get_dialect_profile,
    lower_logical_type,
    truncate_identifier_utf8,
)
from opendv.storage.emitters import DbtEmitter, IcebergEmitter, ParquetEmitter
from opendv.storage.eval_gate import EmitterDefectReporter, TargetEvalGate
from opendv.storage.local_runner import (
    SourceAttachment,
    execute_readonly_query,
    materialize_duckdb_local,
    materialize_parquet_local,
    validate_readonly_query_sql,
)
from opendv.storage.prover import DuckDbProverAdapter
from opendv.storage.sql_ast_verifier import SqlAstVerifier
from opendv.storage.sql_compiler import CompiledSqlBundle, SqlGlotVaultCompiler, WaveMigrator

__all__ = [
    "ALL_TARGET_IDS",
    "DIALECT_PROFILES",
    "SQL_TARGET_IDS",
    "CompiledSqlBundle",
    "DbtEmitter",
    "DuckDbProverAdapter",
    "EmitterDefectReporter",
    "IcebergEmitter",
    "ParquetEmitter",
    "SourceAttachment",
    "SqlAstVerifier",
    "SqlDialectProfile",
    "SqlGlotVaultCompiler",
    "TargetEvalGate",
    "TargetId",
    "WaveMigrator",
    "assign_physical_aliases",
    "execute_readonly_query",
    "get_dialect_profile",
    "lower_logical_type",
    "materialize_duckdb_local",
    "materialize_parquet_local",
    "truncate_identifier_utf8",
    "validate_readonly_query_sql",
]
