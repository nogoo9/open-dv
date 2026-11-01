"""Deterministic AST and Contract IR Codemods (Algorithms A.1-A.5, A.9a)."""

from __future__ import annotations

from opendv_axi_verifier.codemods.alg_a1_satellite_split import (
    codemod_2_split_satellite_by_cadence_and_classification,
    codemod_split_satellites_3axis,
    escalate_classification,
)
from opendv_axi_verifier.codemods.alg_a2_enum_demotion import (
    codemod_3_demote_enum_hub_to_reference,
    codemod_demote_enum_hubs_to_ref,
)
from opendv_axi_verifier.codemods.alg_a3_link_effectivity import (
    codemod_4_inject_effectivity_satellite,
    codemod_inject_link_effectivity,
)
from opendv_axi_verifier.codemods.alg_a4_cold_start import (
    codemod_1_extract_hub_mutable_attributes,
    codemod_enforce_cold_start_extensibility,
    codemod_extract_hub_mutable_attributes,
)
from opendv_axi_verifier.codemods.alg_a5_sqlglot_hash import (
    DELIMITER,
    NULL_TOKEN,
    ZERO_KEY,
    build_canonical_column_standardization_ast,
    build_canonical_sha256_hash_ast,
    build_length_prefixed_ast,
    codemod_5_rewrite_canonical_sha256_hash_ast,
    codemod_enforce_staging_hashes_sql,
)
from opendv_axi_verifier.codemods.alg_a9a_schema_drift import (
    DriftFinding,
    are_types_compatible,
    codemod_6_additive_schema_drift_evolution,
    codemod_intra_source_schema_drift,
    detect_intra_source_schema_drift,
    is_lossless_type_widening,
    levenshtein_distance,
    levenshtein_similarity,
)

__all__ = [
    "DELIMITER",
    "NULL_TOKEN",
    "ZERO_KEY",
    "DriftFinding",
    "are_types_compatible",
    "build_canonical_column_standardization_ast",
    "build_canonical_sha256_hash_ast",
    "build_length_prefixed_ast",
    "codemod_1_extract_hub_mutable_attributes",
    "codemod_2_split_satellite_by_cadence_and_classification",
    "codemod_3_demote_enum_hub_to_reference",
    "codemod_4_inject_effectivity_satellite",
    "codemod_5_rewrite_canonical_sha256_hash_ast",
    "codemod_6_additive_schema_drift_evolution",
    "codemod_demote_enum_hubs_to_ref",
    "codemod_enforce_cold_start_extensibility",
    "codemod_enforce_staging_hashes_sql",
    "codemod_extract_hub_mutable_attributes",
    "codemod_inject_link_effectivity",
    "codemod_intra_source_schema_drift",
    "codemod_split_satellites_3axis",
    "detect_intra_source_schema_drift",
    "escalate_classification",
    "is_lossless_type_widening",
    "levenshtein_distance",
    "levenshtein_similarity",
]
