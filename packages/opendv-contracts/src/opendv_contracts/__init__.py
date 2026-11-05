"""Open-DV Contracts Package.

Language-neutral Pydantic DTOs, Error Taxonomy, RFC 8785 JCS, and CLI Spec.
"""

# ruff: noqa: F401
from __future__ import annotations

from opendv_contracts import dtos, errors
from opendv_contracts.dtos import (
    ArtifactMetadataResponse,
    ArtifactUploadRequest,
    BaseContractModel,
    CompileRequest,
    CompileResponse,
    ContractVersion,
    CreateProjectRequest,
    EventType,
    GateRuleEvaluation,
    GateScorecardSummary,
    GateVerdict,
    HoldAction,
    HoldDecisionRequest,
    HoldDecisionResponse,
    HoldOptionCandidate,
    HoldRequest,
    HoldResponse,
    HoldStatus,
    PipelineEvent,
    PipelineRunHandle,
    PipelineRunRequest,
    PipelineValidateRequest,
    ProfileRequest,
    ProfileResponse,
    ProjectResponse,
    RbacRole,
    RenderedOutput,
    RuleEvaluationDTO,
    RunStatus,
    SandboxBatchResult,
    SandboxQueryRequest,
    SandboxQueryResponse,
    SandboxQueryType,
    VerifyRequest,
    VerifyResponse,
)
from opendv_contracts.errors import (
    ERROR_MAP,
    ERROR_MAPPINGS,
    ErrorCode,
    ErrorDetail,
    ErrorMappingEntry,
    HoldNotFoundError,
    InputValidationError,
    NotFoundError,
    OpenDVError,
    ProblemDetail,
    create_problem_detail,
    get_error_entry,
    get_error_mapping,
)
from opendv_contracts.jcs import (
    CanonicalizationError,
    FloatDomainError,
    IntegerDomainError,
    dumps_jcs,
    dumps_jcs_bytes,
    loads_jcs,
)
from opendv_contracts.spec import (
    load_cli_spec,
    load_cli_spec_schema,
    validate_cli_spec,
)
from opendv_contracts.version import CONTRACT_VERSION, SPEC_VERSION

__version__ = CONTRACT_VERSION

__all__ = [
    "CONTRACT_VERSION",
    "SPEC_VERSION",
    "__version__",
    *errors.__all__,
    # JCS
    "CanonicalizationError",
    "FloatDomainError",
    "IntegerDomainError",
    "dumps_jcs",
    "dumps_jcs_bytes",
    "loads_jcs",
    # Spec
    "load_cli_spec",
    "load_cli_spec_schema",
    "validate_cli_spec",
    *dtos.__all__,
]
