"""11-target SQL dialect matrix and deterministic 13-byte-reserved UTF-8 alias truncation.

Strictly Layer 2a: imports only opendv.models, _dialect_types, and Python stdlib.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Literal

from opendv.models.project import DbtSqlDialect, EvolutionLockSpec
from opendv.models.vault_ir_certified import CertifiedVaultIR
from opendv.storage._dialect_types import (
    ALL_TARGET_IDS,
    SQL_TARGET_IDS,
    SqlTargetId,
    TargetId,
    lower_logical_type,
    resolve_target_id,
)

ViewReplaceMode = Literal["CREATE_OR_REPLACE", "CREATE_OR_ALTER", "DROP_AND_CREATE"]
LoadPattern = Literal[
    "INSERT_NOT_EXISTS",
    "MERGE_DELTA",
    "MERGE_WHEN_NOT_MATCHED",
    "MERGE_HOLDLOCK",
    "ON_CONFLICT_DO_NOTHING",
    "LEFT_ANTI_JOIN",
    "CLICKHOUSE_ARGMAX_ANTI_JOIN",
    "FORMAT_BUNDLE",
]


@dataclass(frozen=True, slots=True)
class SqlDialectProfile:
    """Canonical dialect metadata and capabilities for each of the 11 Open-DV V2 targets."""

    target_id: TargetId
    sqlglot_dialect: DbtSqlDialect | None
    verification_tier: Literal["RUNTIME_VERIFIED", "SYNTAX_AND_CONTRACT_CHECKED"]
    max_ident_bytes: int
    supports_alter_widen: bool
    view_replace_mode: ViewReplaceMode
    z3_rebuild_mode: ViewReplaceMode
    load_pattern: LoadPattern
    supports_schemas: bool = True


DIALECT_PROFILES: dict[TargetId, SqlDialectProfile] = {
    "duckdb_local": SqlDialectProfile(
        target_id="duckdb_local",
        sqlglot_dialect="duckdb",
        verification_tier="RUNTIME_VERIFIED",
        max_ident_bytes=255,
        supports_alter_widen=True,
        view_replace_mode="CREATE_OR_REPLACE",
        z3_rebuild_mode="CREATE_OR_REPLACE",
        load_pattern="INSERT_NOT_EXISTS",
    ),
    "databricks_uc": SqlDialectProfile(
        target_id="databricks_uc",
        sqlglot_dialect="databricks",
        verification_tier="SYNTAX_AND_CONTRACT_CHECKED",
        max_ident_bytes=255,
        supports_alter_widen=True,
        view_replace_mode="CREATE_OR_REPLACE",
        z3_rebuild_mode="CREATE_OR_REPLACE",
        load_pattern="MERGE_DELTA",
    ),
    "snowflake": SqlDialectProfile(
        target_id="snowflake",
        sqlglot_dialect="snowflake",
        verification_tier="SYNTAX_AND_CONTRACT_CHECKED",
        max_ident_bytes=255,
        supports_alter_widen=True,
        view_replace_mode="CREATE_OR_REPLACE",
        z3_rebuild_mode="CREATE_OR_REPLACE",
        load_pattern="MERGE_WHEN_NOT_MATCHED",
    ),
    "postgres": SqlDialectProfile(
        target_id="postgres",
        sqlglot_dialect="postgres",
        verification_tier="SYNTAX_AND_CONTRACT_CHECKED",
        max_ident_bytes=63,
        supports_alter_widen=True,
        view_replace_mode="DROP_AND_CREATE",
        z3_rebuild_mode="DROP_AND_CREATE",
        load_pattern="ON_CONFLICT_DO_NOTHING",
    ),
    "bigquery": SqlDialectProfile(
        target_id="bigquery",
        sqlglot_dialect="bigquery",
        verification_tier="SYNTAX_AND_CONTRACT_CHECKED",
        max_ident_bytes=300,
        supports_alter_widen=True,
        view_replace_mode="CREATE_OR_REPLACE",
        z3_rebuild_mode="CREATE_OR_REPLACE",
        load_pattern="MERGE_WHEN_NOT_MATCHED",
    ),
    "sqlserver_fabric": SqlDialectProfile(
        target_id="sqlserver_fabric",
        sqlglot_dialect="tsql",
        verification_tier="SYNTAX_AND_CONTRACT_CHECKED",
        max_ident_bytes=128,
        supports_alter_widen=True,
        view_replace_mode="CREATE_OR_ALTER",
        z3_rebuild_mode="DROP_AND_CREATE",
        load_pattern="MERGE_HOLDLOCK",
    ),
    "redshift": SqlDialectProfile(
        target_id="redshift",
        sqlglot_dialect="redshift",
        verification_tier="SYNTAX_AND_CONTRACT_CHECKED",
        max_ident_bytes=127,
        supports_alter_widen=True,
        view_replace_mode="DROP_AND_CREATE",
        z3_rebuild_mode="DROP_AND_CREATE",
        load_pattern="LEFT_ANTI_JOIN",
    ),
    "clickhouse": SqlDialectProfile(
        target_id="clickhouse",
        sqlglot_dialect="clickhouse",
        verification_tier="SYNTAX_AND_CONTRACT_CHECKED",
        max_ident_bytes=255,
        supports_alter_widen=False,
        view_replace_mode="CREATE_OR_REPLACE",
        z3_rebuild_mode="CREATE_OR_REPLACE",
        load_pattern="CLICKHOUSE_ARGMAX_ANTI_JOIN",
    ),
    "iceberg": SqlDialectProfile(
        target_id="iceberg",
        sqlglot_dialect=None,
        verification_tier="SYNTAX_AND_CONTRACT_CHECKED",
        max_ident_bytes=255,
        supports_alter_widen=True,
        view_replace_mode="CREATE_OR_REPLACE",
        z3_rebuild_mode="CREATE_OR_REPLACE",
        load_pattern="FORMAT_BUNDLE",
    ),
    "parquet": SqlDialectProfile(
        target_id="parquet",
        sqlglot_dialect=None,
        verification_tier="RUNTIME_VERIFIED",
        max_ident_bytes=255,
        supports_alter_widen=False,
        view_replace_mode="CREATE_OR_REPLACE",
        z3_rebuild_mode="CREATE_OR_REPLACE",
        load_pattern="FORMAT_BUNDLE",
    ),
    "dbt": SqlDialectProfile(
        target_id="dbt",
        sqlglot_dialect=None,
        verification_tier="SYNTAX_AND_CONTRACT_CHECKED",
        max_ident_bytes=255,
        supports_alter_widen=True,
        view_replace_mode="CREATE_OR_REPLACE",
        z3_rebuild_mode="CREATE_OR_REPLACE",
        load_pattern="FORMAT_BUNDLE",
    ),
}


def get_dialect_profile(target_or_dialect: str) -> SqlDialectProfile:
    """Return the SqlDialectProfile for a TargetId or DbtSqlDialect."""
    return DIALECT_PROFILES[resolve_target_id(target_or_dialect)]


def truncate_identifier_utf8(
    logical_name: str,
    max_ident_bytes: int,
    used_aliases: set[str],
    *,
    locked_aliases: dict[str, str] | None = None,
) -> str:
    """Truncate logical_name deterministically at UTF-8 char boundary reserving 13 bytes."""
    if locked_aliases and logical_name in locked_aliases:
        existing = locked_aliases[logical_name]
        used_aliases.add(existing)
        return existing

    raw = logical_name.encode("utf-8")
    if len(raw) <= max_ident_bytes and logical_name not in used_aliases:
        used_aliases.add(logical_name)
        return logical_name

    if max_ident_bytes < 14:
        raise ValueError(f"max_ident_bytes ({max_ident_bytes}) must be >= 14")

    digest8 = hashlib.sha256(raw).hexdigest()[:8]
    prefix_budget = max_ident_bytes - 13
    prefix = raw[:prefix_budget].decode("utf-8", errors="ignore")
    base = f"{prefix}_{digest8}"
    if base not in used_aliases:
        used_aliases.add(base)
        return base

    for idx in range(1, 100):
        cand = f"{base}_{idx:02d}"
        if len(cand.encode("utf-8")) <= max_ident_bytes and cand not in used_aliases:
            used_aliases.add(cand)
            return cand
    raise ValueError(f"Identifier collision limit exceeded for {logical_name!r}")


def assign_physical_aliases(
    ir: CertifiedVaultIR,
    target_or_dialect: str,
    *,
    locks: EvolutionLockSpec | None = None,
) -> dict[str, str]:
    """Deterministically assign and lock physical aliases in (wave, zone, entity) order."""
    profile = get_dialect_profile(target_or_dialect)
    max_bytes = profile.max_ident_bytes
    locked_map = dict(locks.physical_aliases) if locks else {}
    used: set[str] = set(locked_map.values())
    aliases: dict[str, str] = dict(locked_map)

    entities: list[tuple[int, str, str]] = [
        *((ir.wave_number, b.zone_id, b.table_name) for b in ir.bronze_tables),
        *((ir.wave_number, r.zone_id, r.ref_name) for r in ir.reference_tables),
        *((ir.wave_number, s.zone_id, s.view_name) for s in ir.staging_views),
        *((ir.wave_number, h.zone_id, h.hub_name) for h in ir.hubs if not h.is_planned_concept),
        *((ir.wave_number, lk.zone_id, lk.link_name) for lk in ir.links),
        *((ir.wave_number, sat.zone_id, sat.sat_name) for sat in ir.satellites),
        *((ir.wave_number, pb.zone_id, pb.object_name) for pb in ir.pit_bridges),
        *((ir.wave_number, m.zone_id, m.view_name) for m in ir.information_marts),
    ]
    entities.sort(key=lambda item: (item[0], item[1], item[2]))
    for _, _, logical_name in entities:
        if "." in logical_name:
            schema_part, obj_part = logical_name.split(".", 1)
            s_alias = truncate_identifier_utf8(schema_part, max_bytes, used, locked_aliases=aliases)
            o_alias = truncate_identifier_utf8(obj_part, max_bytes, used, locked_aliases=aliases)
            aliases[schema_part] = s_alias
            aliases[obj_part] = o_alias
            aliases[logical_name] = f"{s_alias}.{o_alias}"
        else:
            aliases[logical_name] = truncate_identifier_utf8(
                logical_name, max_bytes, used, locked_aliases=aliases
            )
    return aliases


__all__ = [
    "ALL_TARGET_IDS",
    "DIALECT_PROFILES",
    "SQL_TARGET_IDS",
    "LoadPattern",
    "SqlDialectProfile",
    "SqlTargetId",
    "TargetId",
    "ViewReplaceMode",
    "assign_physical_aliases",
    "get_dialect_profile",
    "lower_logical_type",
    "resolve_target_id",
    "truncate_identifier_utf8",
]
