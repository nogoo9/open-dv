"""Content-Addressable Store (CAS) artifact endpoints (`/api/v1/artifacts`)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from opendv_platform_api.deps import (
    ArtifactMetadataResponse,
    ArtifactUploadRequest,
    PlatformStateContainer,
    get_platform_state,
)

router = APIRouter(prefix="/artifacts", tags=["artifacts"])


@router.post("", response_model=ArtifactMetadataResponse, status_code=status.HTTP_201_CREATED)
async def upload_artifact(
    req: ArtifactUploadRequest,
    state: Annotated[PlatformStateContainer, Depends(get_platform_state)],
) -> ArtifactMetadataResponse:
    """Store an immutable artifact in the Content-Addressable Store and return its SHA-256."""
    return state.store_artifact(req)


@router.get("/{sha256}", response_model=ArtifactMetadataResponse)
async def get_artifact(
    sha256: str,
    state: Annotated[PlatformStateContainer, Depends(get_platform_state)],
) -> ArtifactMetadataResponse:
    """Fetch an immutable CAS artifact by its 64-character lowercase hex SHA-256 digest."""
    try:
        return state.get_artifact(sha256)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(exc),
        ) from exc
