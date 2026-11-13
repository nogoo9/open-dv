"""Ontology services for Open-DV (Decision D9, ONT-01..05).

Provides deterministic coverage analysis, delta validation, application, diffing,
hashing, sample data redaction, plugin registry, policy engine, and pipeline extension hooks.
"""

from __future__ import annotations

from opendv_service.ontology.applier import (
    apply,
    apply_ontology_delta,
    compute_next_semver,
    parse_semver,
)
from opendv_service.ontology.coverage import compute_ontology_coverage
from opendv_service.ontology.diff import ontology_diff
from opendv_service.ontology.hashing import canonicalize_graph_a_dict, compute_ontology_sha256
from opendv_service.ontology.hooks import (
    BootstrapContext,
    GateViolationContext,
    PipelineExtensionHooks,
    UnresolvedMentionsContext,
    WaveProfilingContext,
    get_pipeline_extension_hooks,
)
from opendv_service.ontology.matching.verifier import (
    get_default_grounding_verifier,
    is_allowed_path_a_predicate,
)
from opendv_service.ontology.policy import OntologyPolicy, OntologyPolicyMode
from opendv_service.ontology.rebase import (
    RebaseCheckResult,
    RebaseConflict,
    check_rebase_conflicts,
)
from opendv_service.ontology.redaction import (
    is_mnpi_key,
    is_pii_key,
    redact_sample_data,
    redact_sample_value,
)
from opendv_service.ontology.registry import (
    ONTOLOGY_RECOMMENDER_ENTRY_POINT_GROUP,
    OntologyRecommenderRegistry,
    get_ontology_recommender_registry,
    set_default_ontology_recommender_registry,
)
from opendv_service.ontology.resolution import (
    OntologyResolutionResult,
    resolve_ontology_hold,
)
from opendv_service.ontology.validator import (
    ConcurrentModificationError,
    ContractViolationError,
    GovernanceValidationResult,
    GovernanceValidator,
    HierarchicalCycleError,
    InvalidGroundingWaiverError,
    OntologyGovernanceError,
    ProvisionalTokenError,
    SemVerCalculusError,
    StructuralValidationResult,
    StructuralValidator,
    UngroundedConceptError,
    build_deployment_provider_from_contract,
    is_concept_mutated,
    validate_ontology_delta,
)

__all__ = [
    "ONTOLOGY_RECOMMENDER_ENTRY_POINT_GROUP",
    "BootstrapContext",
    "ConcurrentModificationError",
    "ContractViolationError",
    "GateViolationContext",
    "GovernanceValidationResult",
    "GovernanceValidator",
    "HierarchicalCycleError",
    "InvalidGroundingWaiverError",
    "OntologyGovernanceError",
    "OntologyPolicy",
    "OntologyPolicyMode",
    "OntologyRecommenderRegistry",
    "OntologyResolutionResult",
    "PipelineExtensionHooks",
    "ProvisionalTokenError",
    "RebaseCheckResult",
    "RebaseConflict",
    "SemVerCalculusError",
    "StructuralValidationResult",
    "StructuralValidator",
    "UngroundedConceptError",
    "UnresolvedMentionsContext",
    "WaveProfilingContext",
    "apply",
    "apply_ontology_delta",
    "build_deployment_provider_from_contract",
    "canonicalize_graph_a_dict",
    "check_rebase_conflicts",
    "compute_next_semver",
    "compute_ontology_coverage",
    "compute_ontology_sha256",
    "get_default_grounding_verifier",
    "get_ontology_recommender_registry",
    "get_pipeline_extension_hooks",
    "is_allowed_path_a_predicate",
    "is_concept_mutated",
    "is_mnpi_key",
    "is_pii_key",
    "ontology_diff",
    "parse_semver",
    "redact_sample_data",
    "redact_sample_value",
    "resolve_ontology_hold",
    "set_default_ontology_recommender_registry",
    "validate_ontology_delta",
]
