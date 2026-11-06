"""Compile endpoints (`/api/v1/compile`)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from opendv_contracts import CompileRequest, CompileResponse
from opendv_platform_api.deps import get_opendv_service
from opendv_service import OpenDVService

router = APIRouter(prefix="/compile", tags=["compile"])


@router.post("", response_model=CompileResponse)
async def compile_pipeline(
    req: CompileRequest,
    service: Annotated[OpenDVService, Depends(get_opendv_service)],
) -> CompileResponse:
    """Compile Data Vault 2.0 contract into Canonical IR and 4-Zone SQL."""
    return service.compile_pipeline(req)
