"""Content-Addressable Store (CAS) binary blob endpoints (`/api/v1/cas`)."""

from __future__ import annotations

import contextlib
import hashlib
import re
import tempfile
from pathlib import Path
from typing import Annotated, Any, Final

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status

from opendv_contracts import RbacRole
from opendv_platform_api.deps import get_opendv_service, get_rbac_role
from opendv_service import OpenDVService

router = APIRouter(prefix="/cas", tags=["cas"])

HEX_64_RE: Final[re.Pattern[str]] = re.compile(r"^[0-9A-Fa-f]{64}$")
MAX_CAS_UPLOAD_BYTES: Final[int] = 100 * 1024 * 1024  # 100 MiB
CAS_STREAM_CHUNK_BYTES: Final[int] = 65536  # 64 KiB


@router.post("/blobs", status_code=status.HTTP_201_CREATED)
async def upload_cas_blob(
    request: Request,
    service: Annotated[OpenDVService, Depends(get_opendv_service)],
    _role: Annotated[RbacRole, Depends(get_rbac_role)],
    x_cas_sha256: Annotated[str | None, Header(alias="X-CAS-SHA256")] = None,
) -> dict[str, Any]:
    """Store an immutable binary blob into the Content-Addressable Store.

    Streams request payload in 64 KiB chunks to a temporary file, enforces
    MAX_CAS_UPLOAD_BYTES mid-stream, verifies optional X-CAS-SHA256, and persists via put_file().
    """
    if _role == RbacRole.PUBLIC:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: unauthenticated public role not authorized to upload CAS blobs",
        )

    expected_digest: str | None = None
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
        expected_digest = clean_header.lower()

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

    cas_dir = getattr(cas_store, "base_dir", None)
    if isinstance(cas_dir, Path):
        cas_dir.mkdir(parents=True, exist_ok=True)
    tmp_dir = str(cas_dir) if cas_dir is not None else None

    hasher = hashlib.sha256()
    total_bytes = 0
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(dir=tmp_dir, prefix=".cas_upload_", delete=False) as tmp:
            tmp_path = Path(tmp.name)
            async for chunk in request.stream():
                if not chunk:
                    continue
                for offset in range(0, len(chunk), CAS_STREAM_CHUNK_BYTES):
                    part = chunk[offset : offset + CAS_STREAM_CHUNK_BYTES]
                    total_bytes += len(part)
                    if total_bytes > MAX_CAS_UPLOAD_BYTES:
                        raise HTTPException(
                            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                            detail=(
                                f"Uploaded blob size ({total_bytes} bytes) "
                                f"exceeds maximum limit ({MAX_CAS_UPLOAD_BYTES} bytes)."
                            ),
                        )
                    hasher.update(part)
                    tmp.write(part)
            tmp.flush()

        actual_digest = hasher.hexdigest().lower()
        if expected_digest is not None and expected_digest != actual_digest:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f"Cryptographic digest mismatch: header declared '{expected_digest}' "
                    f"but computed body digest is '{actual_digest}'."
                ),
            )

        if hasattr(cas_store, "put_file"):
            cas_store.put_file(tmp_path)
        else:
            cas_store.put(tmp_path.read_bytes())
    finally:
        if tmp_path is not None:
            with contextlib.suppress(OSError):
                tmp_path.unlink(missing_ok=True)

    return {
        "sha256": actual_digest,
        "size_bytes": total_bytes,
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
