"""Health and platform capability endpoints (`/api/v1/health`, `/api/v1/platform/capabilities`)."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends

from opendv_platform_api.deps import get_opendv_service
from opendv_service import OpenDVService

WORKBENCH_CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; "
    "font-src 'self' data:; "
    "connect-src 'self'; "
    "frame-ancestors 'none'; "
    "base-uri 'self'"
)

WORKBENCH_INDEX_HTML = """<!doctype html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Open-DV Platform Workbench</title>
  </head>
  <body>
    <div id="root" data-opendv-shell="fallback">
      <h1>Open-DV Platform Workbench</h1>
      <p>Unified Data Vault 2.0 Explorer, Pipeline Builder &amp; Harness Settings</p>
    </div>
  </body>
</html>
"""

router = APIRouter(tags=["health"])


@router.get("/health")
async def get_health(
    service: Annotated[OpenDVService, Depends(get_opendv_service)],
) -> dict[str, Any]:
    """Liveness and readiness probe for the Open-DV Platform API."""
    return service.get_health_status()


@router.get("/platform/capabilities")
async def get_platform_capabilities(
    service: Annotated[OpenDVService, Depends(get_opendv_service)],
) -> dict[str, Any]:
    """Return supported agent harnesses, 5-Tier RBAC roles, Explorer views,
    parity endpoints, and gates.
    """
    return service.get_platform_capabilities()
