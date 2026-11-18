"""Content-Addressable Store (CAS) binary blob endpoints (`/api/v1/cas`)."""

from __future__ import annotations

import hashlib
import re
from typing import Annotated, Any, Final

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status

from opendv_contracts import RbacRole
from opendv_platform_api.deps import get_opendv_service, get_rbac_role
from opendv_service import OpenDVService

router = APIRouter(prefix="/cas", tags=["cas"])

HEX_64_RE: Final[re.Pattern[str]] = re.compile(r"^[0-9A-Fa-f]{64}$")
MAX_CAS_UPLOAD_BYTES: Final[int] = 100 * 1024 * 1024  # 100 MiB


@router.post("/blobs", status_code=status.HTTP_201_CREATED)
async def upload_cas_blob(
    request: Request,
    service: Annotated[OpenDVService, Depends(get_opendv_service)],
    _role: Annotated[RbacRole, Depends(get_rbac_role)],
    x_cas_sha256: Annotated[str | None, Header(alias="X-CAS-SHA256")] = None,
) -> dict[str, Any]:
    """Store an immutable binary blob into the Content-Addressable Store.

    Validates optional X-CAS-SHA256 header, enforces max upload size, computes
    SHA-256 digest, and persists into the CAS store.
    """
    if _role == RbacRole.PUBLIC:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: unauthenticated public role not authorized to upload CAS blobs",
        )

    if x_cas_sha256 is not None:
        clean_header = x_cas_sha256.strip()
        if not HEX_64_RE.match(clean_header):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Invalid X-CAS-SHA256 header: '{x_cas_sha256}' "
                    "is not a 64-character hex digest."
                ),
            )

    body = await request.body()
    if len(body) > MAX_CAS_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=(
                f"Uploaded blob size ({len(body)} bytes) "
                f"exceeds maximum limit ({MAX_CAS_UPLOAD_BYTES} bytes)."
            ),
        )

    actual_digest = hashlib.sha256(body).hexdigest().lower()
    if x_cas_sha256 is not None and x_cas_sha256.strip().lower() != actual_digest:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Cryptographic digest mismatch: header declared '{x_cas_sha256.strip().lower()}' "
                f"but computed body digest is '{actual_digest}'."
            ),
        )

    cas_store = (
        getattr(service, "cas_store", None)
        or getattr(service.state, "cas_store", None)
        or getattr(service.state, "cas", None)
    )
    if cas_store is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Content-Addressable Store is not initialized on service.",
        )

    cas_store.put(body)

    return {
        "sha256": actual_digest,
        "size_bytes": len(body),
        "uri": f"cas://{actual_digest}",
    }


@router.get("/blobs/{sha256}")
async def get_cas_blob(
    sha256: str,
    service: Annotated[OpenDVService, Depends(get_opendv_service)],
    _role: Annotated[RbacRole, Depends(get_rbac_role)],
) -> Response:
    """Retrieve raw immutable binary blob by its 64-character hex digest."""
    clean_hash = sha256.strip()
    if not HEX_64_RE.match(clean_hash):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid SHA-256 digest: '{sha256}'.",
        )

    cas_store = (
        getattr(service, "cas_store", None)
        or getattr(service.state, "cas_store", None)
        or getattr(service.state, "cas", None)
    )
    if cas_store is None:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Content-Addressable Store is not initialized on service.",
        )

    if not cas_store.has(clean_hash):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"CAS blob '{clean_hash}' not found.",
        )

    raw_bytes = cas_store.get_bytes(clean_hash)
    return Response(content=raw_bytes, media_type="application/octet-stream")


__all__ = ["MAX_CAS_UPLOAD_BYTES", "router"]
