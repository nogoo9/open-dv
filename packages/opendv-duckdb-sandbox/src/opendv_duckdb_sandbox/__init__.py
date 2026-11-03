"""Open-DV Graph B Profiler, 4-Zone SQL Compiler, and DuckDB 4-Batch Shadow Sandbox (`WP-05`).

Exports:
- `graph_b_profiler`: `GraphBProfiler`, `ColumnProfileMetrics`, `SchemaDriftKind`,
  `SchemaDriftEvent`, `SchemaDriftReport`
- `ir_lowerer`: `lower_contract_to_canonical_ir`, `compute_topological_load_waves`,
  `topological_load_dag`
- `compiler_4zone`: `Compiled4ZoneBundle`, `compile_canonical_ir_to_4zone_sql`,
  `build_algorithm_a5_sha256_sql`, `build_length_prefixed_token_sql`,
  `build_query_d10_sql`, `build_query_d10b_sql`
- `adversarial_batches`: `AdversarialBatchResult`, `FourBatchExecutionReport`,
  `run_batch_1_baseline_and_ghost_completeness`, `run_batch_2_replay_idempotency`,
  `run_batch_3_delta_and_boundary_shift`,
  `run_batch_4_cross_source_and_bitemporal_restatement`,
  `execute_4batch_adversarial_suite`
- `dynamic_checks`: `DynamicCheckResult`, `DynamicCheckSuiteReport`,
  `check_dv_dyn_001_*` through `check_dv_dyn_015_*`, `evaluate_all_dynamic_checks`
- `pit_query_engine`: `PitQueryEngine`, `QueryD10Row`, `QueryD10bRow`,
  `BiTemporalValuationProof`, `execute_query_d10`, `execute_query_d10b`,
  `ensure_pit_snapshot_for_cutoff`
- `sandbox_engine`: `DuckDbShadowSandbox`, `SandboxVerificationReport`
"""

from __future__ import annotations

from opendv_duckdb_sandbox.adversarial_batches import (
    AdversarialBatchResult,
    FourBatchExecutionReport,
    execute_4batch_adversarial_suite,
    run_batch_1_baseline_and_ghost_completeness,
    run_batch_2_replay_idempotency,
    run_batch_3_delta_and_boundary_shift,
    run_batch_4_cross_source_and_bitemporal_restatement,
)
from opendv_duckdb_sandbox.compiler_4zone import (
    Compiled4ZoneBundle,
    build_algorithm_a5_sha256_sql,
    build_length_prefixed_token_sql,
    build_query_d10_sql,
    build_query_d10b_sql,
    build_staging_bkcc_registry,
    compile_canonical_ir_to_4zone_sql,
    resolve_staging_bkcc_literal,
)
from opendv_duckdb_sandbox.dynamic_checks import (
    DynamicCheckResult,
    DynamicCheckSuiteReport,
    check_dv_dyn_001_pk_uniqueness,
    check_dv_dyn_002_referential_integrity,
    check_dv_dyn_003_replay_idempotency,
    check_dv_dyn_004_true_change_capture,
    check_dv_dyn_005_boundary_shift_collision,
    check_dv_dyn_006_bkcc_collision_and_sal,
    check_dv_dyn_007_ghost_and_zero_key_completeness,
    check_dv_dyn_008_topological_load_order,
    check_dv_dyn_009_bitemporal_late_arrival,
    check_dv_dyn_010_pit_table_parity,
    check_dv_dyn_011_driving_key_effectivity,
    check_dv_dyn_012_unstructured_provenance,
    check_dv_dyn_013_tier2_confidence_governance,
    check_dv_dyn_014_additive_schema_evolution,
    check_dv_dyn_015_insert_only_audit_invariant,
    evaluate_all_dynamic_checks,
)
from opendv_duckdb_sandbox.graph_b_profiler import (
    ColumnProfileMetrics,
    GraphBProfiler,
    SchemaDriftEvent,
    SchemaDriftKind,
    SchemaDriftReport,
)
from opendv_duckdb_sandbox.ir_lowerer import (
    compute_topological_load_waves,
    lower_contract_to_canonical_ir,
    topological_load_dag,
)
from opendv_duckdb_sandbox.pit_query_engine import (
    BiTemporalValuationProof,
    PitQueryEngine,
    QueryD10bRow,
    QueryD10Row,
    ensure_pit_snapshot_for_cutoff,
    execute_query_d10,
    execute_query_d10b,
)
from opendv_duckdb_sandbox.sandbox_engine import (
    DuckDbShadowSandbox,
    SandboxVerificationReport,
)

__version__ = "0.1.0"

__all__ = [
    "AdversarialBatchResult",
    "BiTemporalValuationProof",
    "ColumnProfileMetrics",
    "Compiled4ZoneBundle",
    "DuckDbShadowSandbox",
    "DynamicCheckResult",
    "DynamicCheckSuiteReport",
    "FourBatchExecutionReport",
    "GraphBProfiler",
    "PitQueryEngine",
    "QueryD10Row",
    "QueryD10bRow",
    "SandboxVerificationReport",
    "SchemaDriftEvent",
    "SchemaDriftKind",
    "SchemaDriftReport",
    "__version__",
    "build_algorithm_a5_sha256_sql",
    "build_length_prefixed_token_sql",
    "build_query_d10_sql",
    "build_query_d10b_sql",
    "build_staging_bkcc_registry",
    "check_dv_dyn_001_pk_uniqueness",
    "check_dv_dyn_002_referential_integrity",
    "check_dv_dyn_003_replay_idempotency",
    "check_dv_dyn_004_true_change_capture",
    "check_dv_dyn_005_boundary_shift_collision",
    "check_dv_dyn_006_bkcc_collision_and_sal",
    "check_dv_dyn_007_ghost_and_zero_key_completeness",
    "check_dv_dyn_008_topological_load_order",
    "check_dv_dyn_009_bitemporal_late_arrival",
    "check_dv_dyn_010_pit_table_parity",
    "check_dv_dyn_011_driving_key_effectivity",
    "check_dv_dyn_012_unstructured_provenance",
    "check_dv_dyn_013_tier2_confidence_governance",
    "check_dv_dyn_014_additive_schema_evolution",
    "check_dv_dyn_015_insert_only_audit_invariant",
    "compile_canonical_ir_to_4zone_sql",
    "compute_topological_load_waves",
    "ensure_pit_snapshot_for_cutoff",
    "evaluate_all_dynamic_checks",
    "execute_4batch_adversarial_suite",
    "execute_query_d10",
    "execute_query_d10b",
    "lower_contract_to_canonical_ir",
    "resolve_staging_bkcc_literal",
    "run_batch_1_baseline_and_ghost_completeness",
    "run_batch_2_replay_idempotency",
    "run_batch_3_delta_and_boundary_shift",
    "run_batch_4_cross_source_and_bitemporal_restatement",
    "topological_load_dag",
]
