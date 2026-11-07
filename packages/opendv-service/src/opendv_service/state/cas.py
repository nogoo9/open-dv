"""CAS artifact storage and retrieval operations for opendv-service."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import TYPE_CHECKING

from opendv_contracts.errors import ErrorCode
from opendv_service.errors import OpenDVError, PayloadTooLargeError
from opendv_service.models import (
    ArtifactMetadataResponse,
    ArtifactUploadRequest,
)
from opendv_service.settings import DEFAULT_MAX_INLINE_BYTES
from opendv_telemetry import ArtifactCorruptedError, ContentAddressableStore

if TYPE_CHECKING:
    from opendv_service.state.base import BaseStateContainer


def store_cas_artifact(
    artifacts_store: dict[str, bytes] | Path,
    content: str | bytes,
) -> str:
    """Store an artifact in the CAS and return its 64-char uppercase SHA-256."""
    raw = content.encode("utf-8") if isinstance(content, str) else content
    sha256_hex = hashlib.sha256(raw).hexdigest().upper()
    if isinstance(artifacts_store, dict):
        artifacts_store[sha256_hex] = raw
    else:
        cas = ContentAddressableStore(artifacts_store)
        cas.put(raw)
    return sha256_hex


class ArtifactStateMixin:
    """Artifact upload, CAS persistence, and hash addressing mixin."""

    def store_artifact(
        self: BaseStateContainer,
        req: ArtifactUploadRequest,
    ) -> ArtifactMetadataResponse:
        """Persist a JSON or text artifact in the CAS and return its SHA-256 descriptor."""
        if isinstance(req.content, str):
            raw_bytes = req.content.encode("utf-8")
        else:
            raw_bytes = json.dumps(req.content, sort_keys=True).encode("utf-8")
        max_limit = getattr(
            getattr(self, "settings", None),
            "max_inline_bytes",
            DEFAULT_MAX_INLINE_BYTES,
        )
        if len(raw_bytes) > max_limit:
            raise PayloadTooLargeError(
                f"Artifact size ({len(raw_bytes)} bytes) exceeds limit ({max_limit} bytes)"
            )
        digest = store_cas_artifact(self.cas_root, raw_bytes)
        meta = ArtifactMetadataResponse(
            sha256=digest.upper(),
            byte_length=len(raw_bytes),
            media_type=req.media_type,
            artifact_name=req.artifact_name,
            content=req.content,
        )
        self.artifacts[digest.lower()] = meta
        self.artifacts[digest.upper()] = meta
        return meta

    def get_artifact(
        self: BaseStateContainer,
        sha256: str,
    ) -> ArtifactMetadataResponse:
        """Retrieve an artifact from the CAS by its 64-hex SHA-256 digest."""
        clean = sha256.strip()
        if not re.match(r"^[0-9A-Fa-f]{64}$", clean):
            raise ValueError(f"Invalid SHA-256 hex digest: {sha256!r}")
        if clean in self.artifacts:
            return self.artifacts[clean]
        if clean.upper() in self.artifacts:
            return self.artifacts[clean.upper()]
        if clean.lower() in self.artifacts:
            return self.artifacts[clean.lower()]
        if not self.cas.has(clean.upper()):
            raise KeyError(f"CAS artifact not found for sha256={sha256}")
        try:
            raw_bytes = self.cas.get_bytes(clean.upper())
        except ArtifactCorruptedError as exc:
            raise OpenDVError(str(exc), error_code=ErrorCode.CAS_INTEGRITY_FAILURE) from exc
        try:
            parsed = json.loads(raw_bytes.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            parsed = raw_bytes.decode("utf-8", errors="replace")
        meta = ArtifactMetadataResponse(
            sha256=clean.upper(),
            byte_length=len(raw_bytes),
            media_type="application/json",
            artifact_name=None,
            content=parsed,
        )
        self.artifacts[clean.upper()] = meta
        return meta
