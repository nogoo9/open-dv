"""Stateless symbolic verification endpoints (`/api/v1/verify`)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from opendv_contracts import VerifyRequest, VerifyResponse
from opendv_platform_api.deps import get_opendv_service
from opendv_service import OpenDVService

router = APIRouter(prefix="/verify", tags=["verify"])


@router.post("", response_model=VerifyResponse)
async def verify_pipeline(
    req: VerifyRequest,
    service: Annotated[OpenDVService, Depends(get_opendv_service)],
) -> VerifyResponse:
    """Execute stateless 6-Gate symbolic verification across contract, Graph A,
    and Graph B inputs.
    """
    return service.verify_pipeline(req)
