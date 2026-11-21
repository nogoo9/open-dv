"""Deterministic ontology delta validation and evolution lock facade (ONT-EVO-001..005).

Decomposed into <5,000-token submodules (`validator_models`, `validator_structural`,
`validator_deployment`, `validator_governance`) while preserving 100% of exports.
"""

from __future__ import annotations

from opendv_contracts.dtos.ontology import (
    ALLOWED_PATH_A_PREDICATES,
    EXISTING_CONCEPT_MUTATING_OP_TYPES,
    PATH_A_GROUNDING_THRESHOLD,
)
from opendv_service.ontology.validator_deployment import (
    _extract_deployed_contract_mappings,
    build_deployment_provider_from_contract,
    build_workspace_deployment_provider,
)
from opendv_service.ontology.validator_governance import (
    PATH_B_GROUNDING_ORIGINS,
    GovernanceValidator,
    is_path_b_empirically_grounded,
    validate_ontology_delta,
)
from opendv_service.ontology.validator_models import (
    DEFAULT_GENESIS_SHA256,
    ConcurrentModificationError,
    ContractViolationError,
    GovernanceValidationResult,
    HierarchicalCycleError,
    InvalidGroundingWaiverError,
    OntologyContractViolationError,
    OntologyGovernanceError,
    ProvisionalTokenError,
    SemVerCalculusError,
    StructuralValidationResult,
    UngroundedConceptError,
    _is_concept_deployed,
    _resolve_base_relationships,
    is_concept_mutated,
)
from opendv_service.ontology.validator_structural import StructuralValidator

__all__ = [
    "ALLOWED_PATH_A_PREDICATES",
    "DEFAULT_GENESIS_SHA256",
    "EXISTING_CONCEPT_MUTATING_OP_TYPES",
    "PATH_A_GROUNDING_THRESHOLD",
    "PATH_B_GROUNDING_ORIGINS",
    "ConcurrentModificationError",
    "ContractViolationError",
    "GovernanceValidationResult",
    "GovernanceValidator",
    "HierarchicalCycleError",
    "InvalidGroundingWaiverError",
    "OntologyContractViolationError",
    "OntologyGovernanceError",
    "ProvisionalTokenError",
    "SemVerCalculusError",
    "StructuralValidationResult",
    "StructuralValidator",
    "UngroundedConceptError",
    "_extract_deployed_contract_mappings",
    "_is_concept_deployed",
    "_resolve_base_relationships",
    "build_deployment_provider_from_contract",
    "build_workspace_deployment_provider",
    "is_concept_mutated",
    "is_path_b_empirically_grounded",
    "validate_ontology_delta",
]
