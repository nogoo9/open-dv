"""Content-Addressable Store (CAS) artifact endpoints (`/api/v1/artifacts`)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, status

from opendv_platform_api.deps import get_opendv_service
from opendv_service import ArtifactMetadataResponse, ArtifactUploadRequest, OpenDVService

router = APIRouter(prefix="/artifacts", tags=["artifacts"])


@router.post("", response_model=ArtifactMetadataResponse, status_code=status.HTTP_201_CREATED)
async def upload_artifact(
    req: ArtifactUploadRequest,
    service: Annotated[OpenDVService, Depends(get_opendv_service)],
) -> ArtifactMetadataResponse:
    """Store an immutable artifact in the Content-Addressable Store and return its SHA-256."""
    return service.store_artifact(req)


@router.get("/{sha256}", response_model=ArtifactMetadataResponse)
async def get_artifact(
    sha256: str,
    service: Annotated[OpenDVService, Depends(get_opendv_service)],
) -> ArtifactMetadataResponse:
    """Fetch an immutable CAS artifact by its 64-character lowercase hex SHA-256 digest."""
    return service.get_artifact(sha256)
