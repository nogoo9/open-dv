"""Profile endpoints (`/api/v1/profile`)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from opendv_contracts import ProfileRequest, ProfileResponse
from opendv_platform_api.deps import get_opendv_service
from opendv_service import OpenDVService

router = APIRouter(prefix="/profile", tags=["profile"])


@router.post("", response_model=ProfileResponse)
async def profile_source(
    req: ProfileRequest,
    service: Annotated[OpenDVService, Depends(get_opendv_service)],
) -> ProfileResponse:
    """Profile Bronze tabular sources or existing Graph B profile via SourceSpec."""
    return service.profile_pipeline(req)
