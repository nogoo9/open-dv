"""4-Zone Deterministic DuckDB SQL Compiler (`CanonicalIR` -> Zones 0-4 SQL).

Modular package architecture conforming to User Directive R9 (<400 LOC per file):
- `bundle.py`: Compiled4ZoneBundle model
- `hashing.py`: Algorithm A.5 length-prefixed SHA-256 and type normalization
- `schema_utils.py`: IR inspection and seed literal synthesis
- `ddl.py`: DDL, dual zero keys, ghost records, and additive evolution
- `staging_registry.py`: Staging view BKCC namespace registry
- `bronze_tables.py`: Bronze source staging tables and seed data
- `zone_0.py`: Target schemas, bronze seeds, and vault DDL
- `zone_1.py`: Hashed staging views
- `zone_2.py`: Idempotent insert-only Raw Vault loads
- `zone_3.py`: Business Vault PIT tables and calendar
- `zone_4.py`: Information Mart views and bi-temporal PIT queries
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from opendv_core.models.canonical_ir import CanonicalIR
from opendv_duckdb_sandbox.compiler_4zone.bundle import Compiled4ZoneBundle
from opendv_duckdb_sandbox.compiler_4zone.ddl import (
    _compile_generic_hub_ddl_and_zero_keys,
    _compile_generic_link_ddl_and_zero_keys,
    _compile_generic_reference_ddl_and_seed,
    _compile_generic_satellite_ddl_and_ghost,
    compile_zone_0_migration_sql,
)
from opendv_duckdb_sandbox.compiler_4zone.hashing import (
    _normalize_duckdb_type,
    _sql_lit,
    _validate_ident,
    build_algorithm_a5_sha256_sql,
    build_length_prefixed_token_sql,
)
from opendv_duckdb_sandbox.compiler_4zone.schema_utils import (
    _effective_hub_bkcc_column_expr,
    _effective_hub_bkcc_literal,
    _effective_hub_sm_bkcc_literal,
    _effective_pit_tables,
    _find_bitemporal_satellite,
    _hub_seed_bk_values,
    _hub_slug,
    _link_schema,
    _qualify_steward_predicate,
    _satellite_all_data_columns,
    _satellite_child_key_names,
    _satellite_schema,
    _satellite_seed_sql_literal,
)
from opendv_duckdb_sandbox.compiler_4zone.staging_registry import (
    _table_lookup_keys,
    build_staging_bkcc_registry,
    resolve_staging_bkcc_literal,
)
from opendv_duckdb_sandbox.compiler_4zone.zone_0 import _compile_generic_zone_0_sql
from opendv_duckdb_sandbox.compiler_4zone.zone_1 import _compile_generic_zone_1_staging_views
from opendv_duckdb_sandbox.compiler_4zone.zone_2 import _compile_generic_zone_2_raw_vault_dml
from opendv_duckdb_sandbox.compiler_4zone.zone_3 import (
    _build_generic_pit_view_sql,
    _compile_generic_zone_3_business_vault_sql,
)
from opendv_duckdb_sandbox.compiler_4zone.zone_4 import (
    _compile_generic_zone_4_information_mart_views,
    build_query_d10_sql,
    build_query_d10b_sql,
)
from opendv_duckdb_sandbox.ir_lowerer import topological_load_dag


def compile_canonical_ir_to_4zone_sql(
    ir: CanonicalIR | dict[str, Any],
    *,
    include_batch4_restatement_in_seed: bool = False,
    bronze_tables: Mapping[str, list[dict[str, Any]] | Any] | None = None,
    baseline_ir: CanonicalIR | dict[str, Any] | None = None,
) -> Compiled4ZoneBundle:
    """Compile a `CanonicalIR` model into an executable `Compiled4ZoneBundle`.

    Synthesizes the complete schema/DDL/Ghost/Zero-Key/Staging/Load/PIT/Mart bundle
    directly and generically from the IR.
    """
    ir_obj = CanonicalIR.model_validate(ir) if isinstance(ir, dict) else ir
    load_order = topological_load_dag(ir_obj)

    zone_0_sql = _compile_generic_zone_0_sql(
        ir_obj,
        include_batch4_restatement_in_seed=include_batch4_restatement_in_seed,
        bronze_tables=bronze_tables,
    )
    zone_0_migration_sql = compile_zone_0_migration_sql(ir_obj, baseline_ir=baseline_ir)
    zone_1_sql = _compile_generic_zone_1_staging_views(ir_obj)
    zone_2_sql = _compile_generic_zone_2_raw_vault_dml(ir_obj)
    zone_3_sql = _compile_generic_zone_3_business_vault_sql(ir_obj)
    zone_4_d10, zone_4_d10b = _compile_generic_zone_4_information_mart_views(ir_obj)

    return Compiled4ZoneBundle(
        model_name=ir_obj.model_name,
        ir_version=ir_obj.ir_version,
        topological_load_order=load_order,
        zone_0_sql=zone_0_sql,
        zone_0_migration_sql=zone_0_migration_sql,
        zone_1_sql=zone_1_sql,
        zone_2_sql=zone_2_sql,
        zone_3_sql=zone_3_sql,
        zone_4_query_d10_sql=zone_4_d10,
        zone_4_query_d10b_sql=zone_4_d10b,
    )


__all__ = [
    "Compiled4ZoneBundle",
    "_build_generic_pit_view_sql",
    "_compile_generic_hub_ddl_and_zero_keys",
    "_compile_generic_link_ddl_and_zero_keys",
    "_compile_generic_reference_ddl_and_seed",
    "_compile_generic_satellite_ddl_and_ghost",
    "_compile_generic_zone_0_sql",
    "_compile_generic_zone_1_staging_views",
    "_compile_generic_zone_2_raw_vault_dml",
    "_compile_generic_zone_3_business_vault_sql",
    "_compile_generic_zone_4_information_mart_views",
    "_effective_hub_bkcc_column_expr",
    "_effective_hub_bkcc_literal",
    "_effective_hub_sm_bkcc_literal",
    "_effective_pit_tables",
    "_find_bitemporal_satellite",
    "_hub_seed_bk_values",
    "_hub_slug",
    "_link_schema",
    "_normalize_duckdb_type",
    "_qualify_steward_predicate",
    "_satellite_all_data_columns",
    "_satellite_child_key_names",
    "_satellite_schema",
    "_satellite_seed_sql_literal",
    "_sql_lit",
    "_table_lookup_keys",
    "_validate_ident",
    "build_algorithm_a5_sha256_sql",
    "build_length_prefixed_token_sql",
    "build_query_d10_sql",
    "build_query_d10b_sql",
    "build_staging_bkcc_registry",
    "compile_canonical_ir_to_4zone_sql",
    "compile_zone_0_migration_sql",
    "resolve_staging_bkcc_literal",
]
