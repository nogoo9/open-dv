"""Open-DV Headless Platform API (`/api/v1` REST + SSE Server)."""

from __future__ import annotations

from typing import Any

from opendv_platform_api.deps import (
    get_opendv_service,
    get_platform_state,
    get_rbac_role,
)
from opendv_platform_api.export_openapi import export_openapi_to_file, generate_openapi_spec
from opendv_platform_api.main import create_app
from opendv_platform_api.routers.sandbox import SandboxQueryRequest
from opendv_service import (
    ArtifactMetadataResponse,
    ArtifactUploadRequest,
    CaptainHoldDecisionRequest,
    CaptainHoldDecisionResponse,
    CreateProjectRequest,
    CreateRunRequest,
    GateScorecardSummary,
    OpenDVService,
    PipelineRunRequest,
    PipelineValidateRequest,
    PlatformStateContainer,
    ProjectResponse,
    ProjectRunDTO,
    RbacRole,
    RunStatus,
    SandboxExecutionResponse,
    build_gate_scorecard_from_axi_result,
    normalize_rbac_role,
    redact_event_for_role,
    resolve_cleared_tiers,
    store_cas_artifact,
)
from opendv_service.legacy_demo import (
    execute_pe_sandbox_query,
)
from opendv_service.rbac import ROLE_CLEARANCE_LEVELS
from opendv_service.state import resolve_safe_repo_path
from opendv_telemetry import format_sse_event_stream

__version__ = "0.1.0"

__all__ = [
    "ROLE_CLEARANCE_LEVELS",
    "ArtifactMetadataResponse",
    "ArtifactUploadRequest",
    "CaptainHoldDecisionRequest",
    "CaptainHoldDecisionResponse",
    "CreateProjectRequest",
    "CreateRunRequest",
    "GateScorecardSummary",
    "OpenDVService",
    "PipelineRunRequest",
    "PipelineValidateRequest",
    "PlatformStateContainer",
    "ProjectResponse",
    "ProjectRunDTO",
    "RbacRole",
    "RunStatus",
    "SandboxExecutionResponse",
    "SandboxQueryRequest",
    "__version__",
    "app",
    "build_gate_scorecard_from_axi_result",
    "create_app",
    "execute_pe_sandbox_query",
    "export_openapi_to_file",
    "format_sse_event_stream",
    "generate_openapi_spec",
    "get_opendv_service",
    "get_platform_state",
    "get_rbac_role",
    "normalize_rbac_role",
    "redact_event_for_role",
    "resolve_cleared_tiers",
    "resolve_safe_repo_path",
    "store_cas_artifact",
]


def __getattr__(name: str) -> Any:
    if name == "app":
        return create_app()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
