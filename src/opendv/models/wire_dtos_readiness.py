"""Wire DTOs for Harness Readiness, Doctor Diagnostics, and Trust Revocation."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, field_validator

from opendv.models.diagnostics import StrictModel
from opendv.models.errors import ErrorRemediation
from opendv.models.policy import CegarPolicy
from opendv.models.project import HarnessProvider, SecretPrecedence


class ModelOption(StrictModel):
    """Offline default model suggestion exposed to Web UI and CLI."""

    id: str
    label: str
    source: Literal["default"] = "default"


class ProviderReadinessRecord(StrictModel):
    """Per-provider readiness snapshot merging Tier-1 prerequisites, cache, and active-run state."""

    provider: HarnessProvider
    status: Literal["MISSING_PREREQ", "CONFIGURED_UNVERIFIED", "VERIFIED", "AUTH_FAILED"]
    credential_source: Literal["cli_login", "store", "os_env", "none"]
    binary_path: str | None = None
    sdk_installed: bool | None = None
    missing_prereqs: list[str] = Field(default_factory=list)
    available_models: list[ModelOption] = Field(default_factory=list)
    last_verified_at: str | None = None
    last_auth_error_at: str | None = None
    last_auth_error_summary: str | None = None
    active_run: bool = False
    active_run_id: str | None = None
    remediation: ErrorRemediation | None = None


class ReadinessResponse(StrictModel):
    """Response payload for GET /api/v1/agents/readiness."""

    providers: list[ProviderReadinessRecord]


class ReadinessTestReq(StrictModel):
    """Request payload for POST /api/v1/agents/readiness/test."""

    provider: HarnessProvider


class RevokeTrustedWorkspaceReq(StrictModel):
    """Request payload for POST /api/v1/settings/trusted-workspaces/revoke."""

    workspace_root: str

    @field_validator("workspace_root")
    @classmethod
    def _check_workspace_root(cls, v: str) -> str:
        if any(ord(ch) < 32 or ord(ch) == 127 for ch in v):
            raise ValueError("workspace_root must not contain control characters")
        cleaned = v.strip()
        if not cleaned:
            raise ValueError("workspace_root must not be empty or whitespace")
        if cleaned.replace("\\", "/").startswith("//"):
            raise ValueError("workspace_root must not be a UNC or device path")
        return cleaned


class DoctorResponse(StrictModel):
    """Unified diagnostic payload returned by GET /api/v1/system/doctor and `opendv doctor`."""

    status: Literal["ok"] = "ok"
    version: str
    workspace_root: str
    harness_provider: HarnessProvider
    cegar_policy: CegarPolicy
    secret_precedence: SecretPrecedence
    agents: dict[str, Any] | None = None
    mcp_servers: list[dict[str, Any]] | None = None
    readiness: ReadinessResponse
    remediation: list[ErrorRemediation] = Field(default_factory=list)


__all__ = [
    "DoctorResponse",
    "ModelOption",
    "ProviderReadinessRecord",
    "ReadinessResponse",
    "ReadinessTestReq",
    "RevokeTrustedWorkspaceReq",
]
