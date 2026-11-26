"""Authoritative 5-table SQLite WAL ProjectStateManager and Layer-2b agent port adapters."""

from __future__ import annotations

import json
import shutil
import sqlite3
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ValidationError

from opendv.agents.ports import ControlSignalReader, RunCheckpointStore, RunEventSink
from opendv.models.errors import ErrorCode, OpenDVError
from opendv.models.events import (
    ControlSignalRecord,
    RunCheckpointRecord,
    RunEventLogPage,
    RunStreamEvent,
)
from opendv.models.project import (
    ProjectSettingsSnapshot,
    ProjectState,
    ProjectSummary,
    RevisionMetadata,
    validate_domain_namespace,
    validate_project_id,
)
from opendv.models.sources import (
    ConnectorConfigPayload,
    DocumentMap,
    SourceProfile,
    SourceStatus,
    SourceSummary,
    TableIndexEntry,
)
from opendv.models.wire_dtos_vault import AttemptSnapshotSpec
from opendv.service._state_ddl import (
    SCHEMA_DDL_STATEMENTS,
    append_jsonl_run_event,
    begin_immediate,
    migrate_per_project_primary_keys,
    open_wal_connection,
    tail_jsonl_run_events,
)
from opendv.service._state_leases import (
    append_event_row,
    claim_lease_row,
    is_lease_row_live,
    list_event_rows,
    poll_control_signal_rows,
    release_lease_row,
    renew_lease_row,
)
from opendv.service._state_records import (
    delete_source_row,
    get_revision_by_key_row,
    get_revision_row,
    get_source_row,
    list_revision_rows,
    list_source_rows,
    require_project_row,
    save_revision_row,
    upsert_source_row,
    utc_now,
)
from opendv.service.os_platform import check_wal_filesystem_safe, resolve_opendv_home


class _UnsetType:
    pass


_UNSET = _UnsetType()


def _parse_settings_snapshot(raw_json: str) -> ProjectSettingsSnapshot:
    try:
        return ProjectSettingsSnapshot.model_validate_json(raw_json)
    except ValidationError as exc:
        raise OpenDVError(
            f"Corrupt project settings_snapshot_json: {exc}",
            error_code=ErrorCode.INTERNAL_SERVER_ERROR,
        ) from exc


def _build_project_state(conn: sqlite3.Connection, row: sqlite3.Row) -> ProjectState:
    pid = str(row["project_id"])
    src_rows = conn.execute(
        "SELECT profile_json, tables_index_json FROM sources_and_tables WHERE project_id = ?",
        (pid,),
    ).fetchall()
    table_count = 0
    for srow in src_rows:
        prof = json.loads(str(srow["profile_json"]))
        t_idx = json.loads(str(srow["tables_index_json"]))
        prof_count = int(prof.get("table_count", 0)) if isinstance(prof, dict) else 0
        idx_count = len(t_idx) if isinstance(t_idx, list) else 0
        table_count += max(prof_count, idx_count)

    lease_row = conn.execute(
        "SELECT * FROM leases WHERE lease_key = ? AND project_id = ?",
        (f"active_job_lease:{pid}", pid),
    ).fetchone()
    has_active = is_lease_row_live(lease_row, utc_now()) if lease_row is not None else False

    return ProjectState(
        project_id=pid,
        domain_namespace=str(row["domain_namespace"]),
        active_ontology_rev_id=(
            str(row["active_ontology_rev_id"])
            if row["active_ontology_rev_id"] is not None
            else None
        ),
        active_wave_id=str(row["active_wave_id"]) if row["active_wave_id"] is not None else None,
        settings_snapshot=_parse_settings_snapshot(str(row["settings_snapshot_json"])),
        source_count=len(src_rows),
        table_count=table_count,
        has_active_lease=has_active,
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
    )


class ProjectStateManager:
    """Authoritative 5-table SQLite WAL state manager + JSONL run stream persistence."""

    def __init__(self, workspace_root: Path) -> None:
        self.workspace_root: Path = Path(workspace_root)
        self.opendv_home: Path = resolve_opendv_home(self.workspace_root)
        self.db_path: Path = self.opendv_home / "project.sqlite"

    def project_dir(self, project_id: str) -> Path:
        return self.opendv_home / "projects" / project_id

    def runs_dir(self, project_id: str) -> Path:
        return self.project_dir(project_id) / "runs"

    def run_log_path(self, project_id: str, run_id: str) -> Path:
        return self.runs_dir(project_id) / f"{run_id}.jsonl"

    def sources_dir(self, project_id: str) -> Path:
        return self.project_dir(project_id) / "sources"

    def targets_dir(self, project_id: str) -> Path:
        return self.project_dir(project_id) / "targets"

    def initialize(self) -> None:
        """Verify WAL mount safety and initialize project.sqlite 5-table WAL schema."""
        check_wal_filesystem_safe(self.opendv_home)
        self.opendv_home.mkdir(parents=True, exist_ok=True)
        conn = open_wal_connection(self.db_path, ensure_wal=True)
        try:
            conn.execute("BEGIN IMMEDIATE")
            for stmt in SCHEMA_DDL_STATEMENTS:
                conn.execute(stmt)
            conn.execute("COMMIT")
        except sqlite3.Error as exc:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise OpenDVError(
                f"Failed to initialize SQLite schema at '{self.db_path}': {exc}",
                error_code=ErrorCode.INTERNAL_SERVER_ERROR,
            ) from exc
        finally:
            conn.close()
        migrate_per_project_primary_keys(self.db_path)

    def create_project(
        self,
        project_id: str,
        domain_namespace: str,
        settings_snapshot: ProjectSettingsSnapshot,
    ) -> ProjectState:
        """Create a new project row and project directories inside BEGIN IMMEDIATE."""
        try:
            validate_project_id(project_id)
            validate_domain_namespace(domain_namespace)
        except ValueError as exc:
            raise OpenDVError(str(exc), error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT) from exc

        now_iso = utc_now().isoformat()
        with begin_immediate(self.db_path) as conn:
            existing = conn.execute(
                "SELECT 1 FROM projects WHERE project_id = ?",
                (project_id,),
            ).fetchone()
            if existing is not None:
                raise OpenDVError(
                    f"Project '{project_id}' already exists.",
                    error_code=ErrorCode.PROJECT_ID_CONFLICT,
                )
            conn.execute(
                """
                INSERT INTO projects (
                    project_id, domain_namespace, active_ontology_rev_id, active_wave_id,
                    settings_snapshot_json, created_at, updated_at
                ) VALUES (?, ?, NULL, NULL, ?, ?, ?)
                """,
                (
                    project_id,
                    domain_namespace,
                    settings_snapshot.model_dump_json(),
                    now_iso,
                    now_iso,
                ),
            )
            self.runs_dir(project_id).mkdir(parents=True, exist_ok=True)
            self.sources_dir(project_id).mkdir(parents=True, exist_ok=True)
            self.targets_dir(project_id).mkdir(parents=True, exist_ok=True)
            row = require_project_row(conn, project_id)
            return _build_project_state(conn, row)

    def get_project(self, project_id: str) -> ProjectState:
        """Fetch full ProjectState projection or raise RESOURCE_NOT_FOUND."""
        conn = open_wal_connection(self.db_path)
        try:
            row = require_project_row(conn, project_id)
            return _build_project_state(conn, row)
        finally:
            conn.close()

    def list_projects(self) -> list[ProjectSummary]:
        """List all projects ordered by most recently updated."""
        if not self.db_path.is_file():
            return []
        conn = open_wal_connection(self.db_path)
        try:
            rows = conn.execute(
                "SELECT * FROM projects ORDER BY updated_at DESC, project_id ASC"
            ).fetchall()
            return [
                ProjectSummary(
                    project_id=str(r["project_id"]),
                    domain_namespace=str(r["domain_namespace"]),
                    active_ontology_rev_id=(
                        str(r["active_ontology_rev_id"])
                        if r["active_ontology_rev_id"] is not None
                        else None
                    ),
                    active_wave_id=(
                        str(r["active_wave_id"]) if r["active_wave_id"] is not None else None
                    ),
                    updated_at=str(r["updated_at"]),
                )
                for r in rows
            ]
        finally:
            conn.close()

    def delete_project(self, project_id: str) -> None:
        """Delete a project (cascading all 4 child tables) and remove project directory."""
        with begin_immediate(self.db_path) as conn:
            cur = conn.execute("DELETE FROM projects WHERE project_id = ?", (project_id,))
            if cur.rowcount == 0:
                raise OpenDVError(
                    f"Project '{project_id}' not found.",
                    error_code=ErrorCode.RESOURCE_NOT_FOUND,
                )
        proj_path = self.project_dir(project_id)
        if proj_path.exists():
            shutil.rmtree(proj_path, ignore_errors=True)

    def update_project_pointers(
        self,
        project_id: str,
        *,
        active_ontology_rev_id: str | _UnsetType | None = _UNSET,
        active_wave_id: str | _UnsetType | None = _UNSET,
        settings_snapshot: ProjectSettingsSnapshot | None = None,
        require_no_active_lease: bool = False,
        snapshot_mutator: (
            Callable[[ProjectSettingsSnapshot], ProjectSettingsSnapshot] | None
        ) = None,
    ) -> ProjectState:
        """Atomically update active ontology/wave pointers or project settings snapshot."""
        if settings_snapshot is not None and snapshot_mutator is not None:
            raise OpenDVError(
                "Cannot supply both settings_snapshot and snapshot_mutator.",
                error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
            )
        now_iso = utc_now().isoformat()
        with begin_immediate(self.db_path) as conn:
            row = require_project_row(conn, project_id)
            if require_no_active_lease:
                lease_row = conn.execute(
                    "SELECT * FROM leases WHERE lease_key = ? AND project_id = ?",
                    (f"active_job_lease:{project_id}", project_id),
                ).fetchone()
                if lease_row is not None and is_lease_row_live(lease_row, utc_now()):
                    raise OpenDVError(
                        f"Project '{project_id}' has an active job lease.",
                        error_code=ErrorCode.PROJECT_JOB_ACTIVE,
                    )
            next_ont = (
                row["active_ontology_rev_id"]
                if isinstance(active_ontology_rev_id, _UnsetType)
                else active_ontology_rev_id
            )
            next_wave = (
                row["active_wave_id"]
                if isinstance(active_wave_id, _UnsetType)
                else active_wave_id
            )
            if snapshot_mutator is not None:
                current_snap = _parse_settings_snapshot(str(row["settings_snapshot_json"]))
                next_settings = snapshot_mutator(current_snap).model_dump_json()
            elif settings_snapshot is not None:
                next_settings = settings_snapshot.model_dump_json()
            else:
                next_settings = str(row["settings_snapshot_json"])
            conn.execute(
                """
                UPDATE projects
                SET active_ontology_rev_id = ?, active_wave_id = ?,
                    settings_snapshot_json = ?, updated_at = ?
                WHERE project_id = ?
                """,
                (next_ont, next_wave, next_settings, now_iso, project_id),
            )
            updated = require_project_row(conn, project_id)
            return _build_project_state(conn, updated)

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
        return upsert_source_row(
            self.db_path,
            project_id,
            source_id=source_id,
            kind=kind,
            uri_or_filename=uri_or_filename,
            binary_sha256=binary_sha256,
            connector_config=connector_config,
            status=status,
            profile=profile,
            document_map=document_map,
            tables_index=tables_index,
            approved_scan_pages=approved_scan_pages,
        )

    def get_source_record(self, project_id: str, source_id: str) -> dict[str, Any]:
        return get_source_row(self.db_path, project_id, source_id)

    def list_sources(self, project_id: str) -> list[SourceSummary]:
        return list_source_rows(self.db_path, project_id)

    def delete_source(self, project_id: str, source_id: str) -> None:
        delete_source_row(self.db_path, project_id, source_id)

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
        return save_revision_row(
            self.db_path,
            project_id,
            revision_id=revision_id,
            rev_kind=rev_kind,
            rev_key=rev_key,
            parent_rev_id=parent_rev_id,
            payload=payload,
            metadata=metadata,
        )

    def get_revision(self, project_id: str, revision_id: str) -> dict[str, Any]:
        return get_revision_row(self.db_path, project_id, revision_id)

    def get_revision_by_key(
        self,
        project_id: str,
        rev_kind: str,
        rev_key: str,
    ) -> dict[str, Any] | None:
        return get_revision_by_key_row(self.db_path, project_id, rev_kind, rev_key)

    def list_revisions(
        self,
        project_id: str,
        *,
        rev_kind: str | None = None,
    ) -> list[dict[str, Any]]:
        return list_revision_rows(self.db_path, project_id, rev_kind=rev_kind)

    def append_event_or_receipt(
        self,
        project_id: str,
        *,
        record_kind: str,
        run_id: str | None,
        payload: BaseModel,
    ) -> int:
        return append_event_row(
            self.db_path,
            project_id,
            record_kind=record_kind,
            run_id=run_id,
            payload=payload,
        )

    def list_events_or_receipts(
        self,
        project_id: str,
        *,
        record_kind: str | None = None,
        run_id: str | None = None,
    ) -> list[dict[str, Any]]:
        return list_event_rows(
            self.db_path,
            project_id,
            record_kind=record_kind,
            run_id=run_id,
        )

    def poll_control_signals(
        self,
        project_id: str,
        run_id: str,
        *,
        after_seq: int = 0,
    ) -> list[ControlSignalRecord]:
        return poll_control_signal_rows(self.db_path, project_id, run_id, after_seq=after_seq)

    def claim_lease(
        self,
        project_id: str,
        *,
        lease_key: str,
        holder_run_id: str,
        ttl_seconds: int = 45,
    ) -> None:
        claim_lease_row(
            self.db_path,
            project_id,
            lease_key=lease_key,
            holder_run_id=holder_run_id,
            ttl_seconds=ttl_seconds,
        )

    def renew_lease(
        self,
        project_id: str,
        *,
        lease_key: str,
        holder_run_id: str,
        ttl_seconds: int = 45,
    ) -> None:
        renew_lease_row(
            self.db_path,
            project_id,
            lease_key=lease_key,
            holder_run_id=holder_run_id,
            ttl_seconds=ttl_seconds,
        )

    def release_lease(
        self,
        project_id: str,
        *,
        lease_key: str,
        holder_run_id: str | None = None,
    ) -> None:
        release_lease_row(
            self.db_path,
            project_id,
            lease_key=lease_key,
            holder_run_id=holder_run_id,
        )

    def append_run_stream_event(self, project_id: str, event: RunStreamEvent) -> None:
        append_jsonl_run_event(self.run_log_path(project_id, event.run_id), event)

    def tail_run_stream_events(
        self,
        project_id: str,
        run_id: str,
        *,
        since_seq: int = 0,
        limit: int | None = None,
    ) -> RunEventLogPage:
        return tail_jsonl_run_events(
            self.run_log_path(project_id, run_id),
            since_seq=since_seq,
            limit=limit,
        )


class ServiceRunEventSink(RunEventSink):
    """Concrete Layer-2b RunEventSink backed by ProjectStateManager JSONL logs."""

    def __init__(self, state_manager: ProjectStateManager, project_id: str) -> None:
        self._state_manager = state_manager
        self._project_id = project_id
        self._seq_lock = threading.Lock()
        self._last_seq_by_run: dict[str, int] = {}

    def _current_seq_locked(self, run_id: str) -> int:
        if run_id not in self._last_seq_by_run:
            page = self._state_manager.tail_run_stream_events(
                self._project_id, run_id, since_seq=0
            )
            self._last_seq_by_run[run_id] = max((ev.seq for ev in page.events), default=0)
        return self._last_seq_by_run[run_id]

    def current_seq(self, run_id: str) -> int:
        """Return the highest persisted sequence number for run_id."""
        with self._seq_lock:
            return self._current_seq_locked(run_id)

    def emit(self, event: RunStreamEvent) -> None:
        with self._seq_lock:
            next_seq = self._current_seq_locked(event.run_id) + 1
            normalized = (
                event if event.seq == next_seq else event.model_copy(update={"seq": next_seq})
            )
            self._state_manager.append_run_stream_event(self._project_id, normalized)
            self._last_seq_by_run[event.run_id] = next_seq


class ServiceControlSignalReader(ControlSignalReader):
    """Concrete Layer-2b ControlSignalReader querying SQLite events_and_receipts."""

    def __init__(self, state_manager: ProjectStateManager, project_id: str) -> None:
        self._state_manager = state_manager
        self._project_id = project_id

    def poll_signals(self, run_id: str, *, after_seq: int = 0) -> list[ControlSignalRecord]:
        return self._state_manager.poll_control_signals(
            self._project_id,
            run_id,
            after_seq=after_seq,
        )


class ServiceRunCheckpointStore(RunCheckpointStore):
    """Concrete Layer-2b RunCheckpointStore backed by ProjectStateManager SQLite WAL."""

    def __init__(self, state_manager: ProjectStateManager, project_id: str) -> None:
        self._state_manager = state_manager
        self._project_id = project_id

    def save_checkpoint(self, record: RunCheckpointRecord) -> None:
        self._state_manager.append_event_or_receipt(
            self._project_id,
            record_kind="run_checkpoint",
            run_id=record.run_id,
            payload=record,
        )

    def load_checkpoint(self, run_id: str) -> RunCheckpointRecord | None:
        rows = self._state_manager.list_events_or_receipts(
            self._project_id,
            record_kind="run_checkpoint",
            run_id=run_id,
        )
        if not rows:
            return None
        return RunCheckpointRecord.model_validate(rows[-1]["payload"])

    def save_attempt_snapshot(self, run_id: str, snapshot: AttemptSnapshotSpec) -> None:
        self._state_manager.append_event_or_receipt(
            self._project_id,
            record_kind="attempt_snapshot",
            run_id=run_id,
            payload=snapshot,
        )

    def release_active_lease(self, run_id: str) -> None:
        self._state_manager.release_lease(
            self._project_id,
            lease_key=f"active_job_lease:{self._project_id}",
            holder_run_id=run_id,
        )


__all__ = [
    "ProjectStateManager",
    "ServiceControlSignalReader",
    "ServiceRunCheckpointStore",
    "ServiceRunEventSink",
]
