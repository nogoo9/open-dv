"""Open-DV Shared Headless Application Core Service & Pipeline Orchestrator."""

from __future__ import annotations

from opendv_agent_harness import AgenticPipelineReport
from opendv_contracts import (
    CompileRequest,
    CompileResponse,
    PipelineEvent,
    PipelineRunHandle,
    ProfileRequest,
    ProfileResponse,
    SandboxQueryResponse,
    VerifyRequest,
    VerifyResponse,
)
from opendv_service.errors import (
    CasIntegrityError,
    ConflictError,
    ErrorCode,
    InputValidationError,
    NotFoundError,
    OpenDVError,
    PathContainmentError,
    PayloadTooLargeError,
    PipelineExecutionError,
    ProviderUnavailableError,
    RolePermissionError,
    UnsupportedFormatError,
)
from opendv_service.models import (
    ArtifactMetadataResponse,
    ArtifactUploadRequest,
    CaptainHoldDecisionRequest,
    CaptainHoldDecisionResponse,
    CreateProjectRequest,
    CreateRunRequest,
    GateScorecardSummary,
    GateVerdictStatus,
    HoldDecisionRequest,
    HoldDecisionResponse,
    PipelineRunRequest,
    PipelineValidateRequest,
    ProjectResponse,
    ProjectRunDTO,
    RunStatus,
    SandboxExecutionResponse,
    SandboxQueryRequest,
)
from opendv_service.rbac import (
    ROLE_CLEARANCE_LEVELS,
    RbacRole,
    normalize_rbac_role,
    redact_event_for_role,
    resolve_cleared_tiers,
)
from opendv_service.seams import (
    PipelineRunner,
    get_default_pipeline_runner,
    set_pipeline_runner_override,
)
from opendv_service.service import OpenDVService
from opendv_service.state import PlatformStateContainer, StateStore, store_cas_artifact
from opendv_service.use_cases.compile import compile_pipeline
from opendv_service.use_cases.profile import profile_pipeline
from opendv_service.use_cases.sandbox import execute_sandbox_query
from opendv_service.use_cases.verify import (
    build_gate_scorecard_from_axi_result,
    verify_pipeline,
)

__version__ = "0.1.0"

__all__ = [
    "ROLE_CLEARANCE_LEVELS",
    "AgenticPipelineReport",
    "ArtifactMetadataResponse",
    "ArtifactUploadRequest",
    "CaptainHoldDecisionRequest",
    "CaptainHoldDecisionResponse",
    "CasIntegrityError",
    "CompileRequest",
    "CompileResponse",
    "ConflictError",
    "CreateProjectRequest",
    "CreateRunRequest",
    "ErrorCode",
    "GateScorecardSummary",
    "GateVerdictStatus",
    "HoldDecisionRequest",
    "HoldDecisionResponse",
    "InputValidationError",
    "NotFoundError",
    "OpenDVError",
    "OpenDVService",
    "PathContainmentError",
    "PayloadTooLargeError",
    "PipelineEvent",
    "PipelineExecutionError",
    "PipelineRunHandle",
    "PipelineRunRequest",
    "PipelineRunner",
    "PipelineValidateRequest",
    "PlatformStateContainer",
    "ProfileRequest",
    "ProfileResponse",
    "ProjectResponse",
    "ProjectRunDTO",
    "ProviderUnavailableError",
    "RbacRole",
    "RolePermissionError",
    "RunStatus",
    "SandboxExecutionResponse",
    "SandboxQueryRequest",
    "SandboxQueryResponse",
    "StateStore",
    "UnsupportedFormatError",
    "VerifyRequest",
    "VerifyResponse",
    "__version__",
    "build_gate_scorecard_from_axi_result",
    "compile_pipeline",
    "execute_sandbox_query",
    "get_default_pipeline_runner",
    "normalize_rbac_role",
    "profile_pipeline",
    "redact_event_for_role",
    "resolve_cleared_tiers",
    "set_pipeline_runner_override",
    "store_cas_artifact",
    "verify_pipeline",
]
