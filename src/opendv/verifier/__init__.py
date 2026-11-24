"""Public Layer-2a exports for Open-DV V2 zero-SQL codemods and symbolic verifier."""

from __future__ import annotations

from opendv.verifier._l1_drift_and_bucket_a import (
    apply_alg_a9a_schema_drift,
    column_name_similarity,
    is_lossless_type_widening,
    map_sql_to_portable_type,
)
from opendv.verifier._v4_unstructured import (
    compute_canonical_text_sha256,
    normalize_page_text,
)
from opendv.verifier.l1_codemods import (
    L1CodemodOptions,
    apply_bucket_a_operators,
    inject_unresolved_triage,
    merge_draft_shards,
    promote_draft_to_certified,
    run_l1_codemods,
)
from opendv.verifier.ports import (
    BucketAExecutionResult,
    IRRuntimeProver,
    VerifierContext,
    VerifierReport,
)
from opendv.verifier.v1_structure import verify_v1_structure
from opendv.verifier.v2_5_gates import (
    build_evolution_lock_from_certified,
    run_v1_to_v6_gates,
    verify_v2_hash_and_bkcc,
    verify_v3_sentinels_and_temporal,
    verify_v4_unstructured,
    verify_v5_evolution,
)
from opendv.verifier.v6b_antipatterns import (
    evaluate_v6b_accounting,
    is_diagnostic_waived,
    lint_ontology_antipatterns,
    lint_v6b_antipatterns,
    lint_vault_antipatterns,
)

__all__ = [
    "BucketAExecutionResult",
    "IRRuntimeProver",
    "L1CodemodOptions",
    "VerifierContext",
    "VerifierReport",
    "apply_alg_a9a_schema_drift",
    "apply_bucket_a_operators",
    "build_evolution_lock_from_certified",
    "column_name_similarity",
    "compute_canonical_text_sha256",
    "evaluate_v6b_accounting",
    "inject_unresolved_triage",
    "is_diagnostic_waived",
    "is_lossless_type_widening",
    "lint_ontology_antipatterns",
    "lint_v6b_antipatterns",
    "lint_vault_antipatterns",
    "map_sql_to_portable_type",
    "merge_draft_shards",
    "normalize_page_text",
    "promote_draft_to_certified",
    "run_l1_codemods",
    "run_v1_to_v6_gates",
    "verify_v1_structure",
    "verify_v2_hash_and_bkcc",
    "verify_v3_sentinels_and_temporal",
    "verify_v4_unstructured",
    "verify_v5_evolution",
]
