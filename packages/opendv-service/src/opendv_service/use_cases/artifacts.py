"""Artifacts use case: Content-addressable storage operations."""

from __future__ import annotations

from opendv_service.models import (
    ArtifactMetadataResponse,
    ArtifactUploadRequest,
)
from opendv_service.state import StateStore


def store_artifact(
    state: StateStore,
    request: ArtifactUploadRequest,
) -> ArtifactMetadataResponse:
    """Store an artifact payload in the Content-Addressable Store (CAS)."""
    return state.store_artifact(request)


def get_artifact(state: StateStore, sha256: str) -> ArtifactMetadataResponse:
    """Retrieve an artifact by its 64-char SHA-256 digest from CAS or cache."""
    return state.get_artifact(sha256)


__all__ = ["get_artifact", "store_artifact"]
