"""Health and platform capability endpoints (`/api/v1/health`, `/api/v1/platform/capabilities`)."""

from __future__ import annotations

from typing import Annotated, Any

import duckdb
from fastapi import APIRouter, Depends

from opendv_platform_api.deps import (
    WORKBENCH_VIEW_TITLES,
    PlatformStateContainer,
    RbacRole,
    get_platform_state,
)

router = APIRouter(tags=["health"])


@router.get("/health")
async def get_health(
    state: Annotated[PlatformStateContainer, Depends(get_platform_state)],
) -> dict[str, Any]:
    """Liveness and readiness probe for the Open-DV Platform API."""
    schema_files = sorted(state.schema_registry.schemas_dir.glob("*.json"))
    return {
        "status": "ok",
        "service": "opendv-platform-api",
        "version": "0.1.0",
        "api_version": "v1",
        "contract_version": "6.0",
        "duckdb_version": duckdb.__version__,
        "schemas_loaded": len(schema_files),
        "registered_projects": len(state.projects),
        "registered_runs": len(state.runs),
    }


@router.get("/platform/capabilities")
async def get_platform_capabilities() -> dict[str, Any]:
    """Return supported agent harnesses, 5-Tier RBAC roles, WB-00..WB-11 views, and gates."""
    harnesses = [
        "antigravity",
        "claude",
        "claude-agent",
        "deterministic-stub",
    ]
    roles = [r.value for r in RbacRole]
    gates = ["G0", "G1", "G2", "G3", "G4", "G5"]
    return {
        "api_version": "v1",
        "contract_version": "6.0",
        "canonical_ir_version": "1.0.0",
        "supported_harnesses": harnesses,
        "harness_providers": harnesses,
        "supported_rbac_roles": roles,
        "rbac_roles": roles,
        "security_classifications": [
            "PUBLIC",
            "INTERNAL",
            "CONFIDENTIAL",
            "MNPI",
            "RESTRICTED_PII",
        ],
        "workbench_views": [f"WB-{i:02d}" for i in range(12)],
        "workbench_view_titles": list(WORKBENCH_VIEW_TITLES.values()),
        "verification_gates": gates,
        "gates": gates,
    }
