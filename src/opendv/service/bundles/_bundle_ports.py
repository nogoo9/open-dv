"""Structural Protocol ports and atomic file helpers for M2B .opendv.zip bundles."""

from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel

from opendv.models.json_hasher import sha256_jcs
from opendv.models.project import (
    ProjectSettingsSnapshot,
    ProjectState,
    ProjectSummary,
    RevisionMetadata,
    SecretPrecedence,
)
from opendv.models.sources import (
    ConnectorConfigPayload,
    DocumentMap,
    SourceProfile,
    SourceStatus,
    SourceSummary,
    TableIndexEntry,
)

_RETRYABLE_WIN_ERRORS = frozenset({5, 32})


@runtime_checkable
class BundleStateStorePort(Protocol):
    """Structural Protocol matching M2A ProjectStateManager methods used by bundles/."""

    def get_project(self, project_id: str) -> ProjectState:
        """Return full ProjectState or raise RESOURCE_NOT_FOUND."""
        ...

    def list_projects(self) -> list[ProjectSummary]:
        """Return all persisted ProjectSummary rows."""
        ...

    def create_project(
        self,
        project_id: str,
        domain_namespace: str,
        settings_snapshot: ProjectSettingsSnapshot,
    ) -> ProjectState:
        """Create a new project record."""
        ...

    def delete_project(self, project_id: str) -> None:
        """Delete a project and cascade all child tables."""
        ...

    def update_project_pointers(
        self,
        project_id: str,
        *,
        active_ontology_rev_id: str | None = ...,
        active_wave_id: str | None = ...,
        settings_snapshot: ProjectSettingsSnapshot | None = ...,
    ) -> ProjectState:
        """Update active ontology/wave pointers or settings snapshot."""
        ...

    def upsert_source(
        self,
        project_id: str,
        *,
        source_id: str,
        kind: str,
        uri_or_filename: str,
        binary_sha256: str | None,
        connector_config: ConnectorConfigPayload,
        status: SourceStatus,
        profile: SourceProfile,
        document_map: DocumentMap | None,
        tables_index: list[TableIndexEntry],
        approved_scan_pages: list[int] | None = None,
    ) -> SourceSummary:
        """Insert or update a source record."""
        ...

    def get_source_record(self, project_id: str, source_id: str) -> dict[str, Any]:
        """Return full source record fields including profile, document_map, and binary_sha256."""
        ...

    def list_sources(self, project_id: str) -> list[SourceSummary]:
        """Return summaries of all attached sources for project_id."""
        ...

    def save_revision(
        self,
        project_id: str,
        *,
        revision_id: str,
        rev_kind: str,
        rev_key: str,
        parent_rev_id: str | None,
        payload: BaseModel,
        metadata: RevisionMetadata,
    ) -> str:
        """Persist a revision payload and return its RFC 8785 sha256_jcs digest."""
        ...

    def list_revisions(
        self, project_id: str, *, rev_kind: str | None = None
    ) -> list[dict[str, Any]]:
        """List revision records for project_id."""
        ...

    def append_event_or_receipt(
        self,
        project_id: str,
        *,
        record_kind: str,
        run_id: str | None,
        payload: BaseModel,
    ) -> int:
        """Append an event or receipt record and return seq_id."""
        ...

    def list_events_or_receipts(
        self,
        project_id: str,
        *,
        record_kind: str | None = None,
        run_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """List events/receipts for project_id."""
        ...


@runtime_checkable
class BundleSecretStorePort(Protocol):
    """Structural Protocol matching M2A KeyringSecretStore.get_secret."""

    def get_secret(
        self,
        key: str,
        *,
        project_precedence: SecretPrecedence | None = None,
        cli_env_first: bool = False,
    ) -> str | None:
        """Resolve a secret by key without mutating workspace files."""
        ...


def replace_with_retry(src: Path, dst: Path, *, max_attempts: int = 8) -> None:
    """Atomically replace dst with src, retrying transient Windows sharing locks."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    delay = 0.02
    for attempt in range(max_attempts):
        try:
            os.replace(src, dst)
            return
        except PermissionError as exc:
            winerr = getattr(exc, "winerror", None)
            if os.name == "nt" and winerr in _RETRYABLE_WIN_ERRORS and attempt + 1 < max_attempts:
                time.sleep(delay)
                delay = min(delay * 2, 0.4)
                continue
            raise


def atomic_write_bytes(target: Path, data: bytes) -> None:
    """Write bytes to a sibling temporary file and atomically replace target."""
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=".tmp-bundle-", dir=str(target.parent))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        replace_with_retry(tmp_path, target)
    except Exception:
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
        raise


def coerce_payload_dict(raw: Any) -> dict[str, Any]:
    """Convert BaseModel, JSON string, or mapping into a plain dict."""
    if isinstance(raw, BaseModel):
        return raw.model_dump(mode="json")
    if isinstance(raw, str):
        loaded = json.loads(raw)
        return dict(loaded) if isinstance(loaded, dict) else {}
    if isinstance(raw, dict):
        return dict(raw)
    return {}


def make_revision_id(rev_kind: str, rev_key: str, payload: BaseModel) -> str:
    """Compute deterministic revision_id from kind, key, and RFC 8785 payload hash."""
    digest = sha256_jcs(payload)
    return f"{rev_kind}:{rev_key}:{digest[:12]}"


__all__ = [
    "BundleSecretStorePort",
    "BundleStateStorePort",
    "atomic_write_bytes",
    "coerce_payload_dict",
    "make_revision_id",
    "replace_with_retry",
]
