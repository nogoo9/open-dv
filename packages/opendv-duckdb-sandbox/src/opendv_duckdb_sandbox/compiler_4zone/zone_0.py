"""Zone 0: Target Schemas, Bronze Source Seeds, Vault DDL & Ghost/Zero Records."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from opendv_core.models.canonical_ir import CanonicalIR
from opendv_duckdb_sandbox.compiler_4zone.bronze_tables import compile_generic_bronze_tables_sql
from opendv_duckdb_sandbox.compiler_4zone.ddl import (
    _compile_generic_hub_ddl_and_zero_keys,
    _compile_generic_link_ddl_and_zero_keys,
    _compile_generic_reference_ddl_and_seed,
    _compile_generic_satellite_ddl_and_ghost,
)


def _compile_generic_zone_0_sql(
    ir_obj: CanonicalIR,
    *,
    include_batch4_restatement_in_seed: bool = False,
    bronze_tables: Mapping[str, list[dict[str, Any]] | Any] | None = None,
) -> str:
    """Compile Zone 0 schemas, Bronze source tables, Vault DDL, Zero Keys, and Ghosts."""
    parts = [
        "-- =============================================================================",
        "-- ZONE 0: TARGET SCHEMAS, BRONZE SOURCE SEEDS, VAULT DDL & GHOST/ZERO RECORDS",
        "-- =============================================================================",
        "CREATE SCHEMA IF NOT EXISTS bronze;",
        "CREATE SCHEMA IF NOT EXISTS staging;",
        "CREATE SCHEMA IF NOT EXISTS raw_vault;",
        "CREATE SCHEMA IF NOT EXISTS business_vault;",
        "CREATE SCHEMA IF NOT EXISTS information_mart;",
    ]

    bronze_parts = compile_generic_bronze_tables_sql(
        ir_obj,
        include_batch4_restatement_in_seed=include_batch4_restatement_in_seed,
        bronze_tables=bronze_tables,
    )
    parts.extend(bronze_parts)

    # Target Vault DDL + Dual Zero Keys + Ghost Records
    for hub in ir_obj.hubs:
        parts.append(_compile_generic_hub_ddl_and_zero_keys(hub))
    for ref_tbl in ir_obj.reference_tables or []:
        parts.append(_compile_generic_reference_ddl_and_seed(ref_tbl))
    for link in ir_obj.links:
        parts.append(_compile_generic_link_ddl_and_zero_keys(link))
    for sat in ir_obj.satellites:
        parts.append(_compile_generic_satellite_ddl_and_ghost(sat))

    return "\n\n".join(parts)


__all__ = ["_compile_generic_zone_0_sql"]
