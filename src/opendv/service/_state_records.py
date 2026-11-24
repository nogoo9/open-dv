"""SQLite record CRUD helpers for sources and revisions."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from pydantic import BaseModel, TypeAdapter

from opendv.models.errors import ErrorCode, OpenDVError
from opendv.models.json_hasher import sha256_jcs
from opendv.models.project import RevisionMetadata
from opendv.models.sources import (
    ConnectorConfigPayload,
    DocumentMap,
    SourceProfile,
    SourceStatus,
    SourceSummary,
    TableIndexEntry,
)
from opendv.service._state_ddl import (
    MUTABLE_REV_KINDS,
    VALID_REV_KINDS,
    VALID_SOURCE_KINDS,
    VALID_SOURCE_STATUSES,
    begin_immediate,
    open_wal_connection,
)

_TABLE_INDEX_LIST_ADAPTER = TypeAdapter(list[TableIndexEntry])
_INT_LIST_ADAPTER = TypeAdapter(list[int])


def utc_now() -> datetime:
    """Return current UTC datetime."""
    return datetime.now(tz=UTC)


def require_project_row(conn: sqlite3.Connection, project_id: str) -> sqlite3.Row:
    """Fetch project row or raise RESOURCE_NOT_FOUND."""
    row: sqlite3.Row | None = conn.execute(
        "SELECT * FROM projects WHERE project_id = ?",
        (project_id,),
    ).fetchone()
    if row is None:
        raise OpenDVError(
            f"Project '{project_id}' not found.",
            error_code=ErrorCode.RESOURCE_NOT_FOUND,
        )
    return row


def upsert_source_row(
    db_path: Path,
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
    if kind not in VALID_SOURCE_KINDS:
        raise OpenDVError(
            f"Invalid source kind '{kind}'.",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        )
    if status not in VALID_SOURCE_STATUSES:
        raise OpenDVError(
            f"Invalid source status '{status}'.",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        )

    now_iso = utc_now().isoformat()
    pages_list = list(approved_scan_pages or [])
    tables_json = _TABLE_INDEX_LIST_ADAPTER.dump_json(tables_index).decode("utf-8")
    pages_json = _INT_LIST_ADAPTER.dump_json(pages_list).decode("utf-8")
    doc_map_json = document_map.model_dump_json() if document_map is not None else None

    with begin_immediate(db_path) as conn:
        require_project_row(conn, project_id)
        existing = conn.execute(
            "SELECT attached_at FROM sources_and_tables WHERE project_id = ? AND source_id = ?",
            (project_id, source_id),
        ).fetchone()
        attached_at = str(existing["attached_at"]) if existing is not None else now_iso
        conn.execute(
            """
            INSERT INTO sources_and_tables (
                source_id, project_id, kind, uri_or_filename, binary_sha256,
                connector_config_json, status, profile_json, document_map_json,
                tables_index_json, approved_scan_pages_json, attached_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(project_id, source_id) DO UPDATE SET
                kind = excluded.kind,
                uri_or_filename = excluded.uri_or_filename,
                binary_sha256 = excluded.binary_sha256,
                connector_config_json = excluded.connector_config_json,
                status = excluded.status,
                profile_json = excluded.profile_json,
                document_map_json = excluded.document_map_json,
                tables_index_json = excluded.tables_index_json,
                approved_scan_pages_json = excluded.approved_scan_pages_json
            """,
            (
                source_id,
                project_id,
                kind,
                uri_or_filename,
                binary_sha256,
                connector_config.model_dump_json(),
                status,
                profile.model_dump_json(),
                doc_map_json,
                tables_json,
                pages_json,
                attached_at,
            ),
        )
        conn.execute(
            "UPDATE projects SET updated_at = ? WHERE project_id = ?",
            (now_iso, project_id),
        )

    return SourceSummary(
        source_id=source_id,
        kind=kind,
        uri_or_filename=uri_or_filename,
        status=status,
        table_count=max(profile.table_count, len(tables_index)),
        page_count=document_map.page_count if document_map is not None else None,
        attached_at=attached_at,
        locked_in_wave=None,
        connector_config=connector_config,
    )


def get_source_row(db_path: Path, project_id: str, source_id: str) -> dict[str, Any]:
    conn = open_wal_connection(db_path)
    try:
        require_project_row(conn, project_id)
        row = conn.execute(
            "SELECT * FROM sources_and_tables WHERE project_id = ? AND source_id = ?",
            (project_id, source_id),
        ).fetchone()
        if row is None:
            raise OpenDVError(
                f"Source '{source_id}' not found in project '{project_id}'.",
                error_code=ErrorCode.RESOURCE_NOT_FOUND,
            )
        return {
            "source_id": str(row["source_id"]),
            "project_id": str(row["project_id"]),
            "kind": str(row["kind"]),
            "uri_or_filename": str(row["uri_or_filename"]),
            "binary_sha256": (
                str(row["binary_sha256"]) if row["binary_sha256"] is not None else None
            ),
            "connector_config": ConnectorConfigPayload.model_validate_json(
                str(row["connector_config_json"])
            ),
            "status": str(row["status"]),
            "profile": SourceProfile.model_validate_json(str(row["profile_json"])),
            "document_map": (
                DocumentMap.model_validate_json(str(row["document_map_json"]))
                if row["document_map_json"] is not None
                else None
            ),
            "tables_index": _TABLE_INDEX_LIST_ADAPTER.validate_json(
                str(row["tables_index_json"])
            ),
            "approved_scan_pages": _INT_LIST_ADAPTER.validate_json(
                str(row["approved_scan_pages_json"])
            ),
            "attached_at": str(row["attached_at"]),
        }
    finally:
        conn.close()


def list_source_rows(db_path: Path, project_id: str) -> list[SourceSummary]:
    conn = open_wal_connection(db_path)
    try:
        require_project_row(conn, project_id)
        rows = conn.execute(
            """
            SELECT * FROM sources_and_tables
            WHERE project_id = ?
            ORDER BY attached_at ASC, source_id ASC
            """,
            (project_id,),
        ).fetchall()
        summaries: list[SourceSummary] = []
        for r in rows:
            prof = SourceProfile.model_validate_json(str(r["profile_json"]))
            t_idx = _TABLE_INDEX_LIST_ADAPTER.validate_json(str(r["tables_index_json"]))
            doc_map = (
                DocumentMap.model_validate_json(str(r["document_map_json"]))
                if r["document_map_json"] is not None
                else None
            )
            status_val = str(r["status"])
            if status_val not in VALID_SOURCE_STATUSES:
                raise OpenDVError(
                    f"Corrupt source status '{status_val}' in database.",
                    error_code=ErrorCode.INTERNAL_SERVER_ERROR,
                )
            summaries.append(
                SourceSummary(
                    source_id=str(r["source_id"]),
                    kind=str(r["kind"]),
                    uri_or_filename=str(r["uri_or_filename"]),
                    status=cast(SourceStatus, status_val),
                    table_count=max(prof.table_count, len(t_idx)),
                    page_count=doc_map.page_count if doc_map is not None else None,
                    attached_at=str(r["attached_at"]),
                    locked_in_wave=None,
                    connector_config=ConnectorConfigPayload.model_validate_json(
                        str(r["connector_config_json"])
                    ),
                )
            )
        return summaries
    finally:
        conn.close()


def delete_source_row(db_path: Path, project_id: str, source_id: str) -> None:
    with begin_immediate(db_path) as conn:
        require_project_row(conn, project_id)
        cur = conn.execute(
            "DELETE FROM sources_and_tables WHERE project_id = ? AND source_id = ?",
            (project_id, source_id),
        )
        if cur.rowcount == 0:
            raise OpenDVError(
                f"Source '{source_id}' not found in project '{project_id}'.",
                error_code=ErrorCode.RESOURCE_NOT_FOUND,
            )
        conn.execute(
            "UPDATE projects SET updated_at = ? WHERE project_id = ?",
            (utc_now().isoformat(), project_id),
        )


def _row_to_revision_dict(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "revision_id": str(row["revision_id"]),
        "project_id": str(row["project_id"]),
        "rev_kind": str(row["rev_kind"]),
        "rev_key": str(row["rev_key"]),
        "parent_rev_id": (
            str(row["parent_rev_id"]) if row["parent_rev_id"] is not None else None
        ),
        "content_sha256": str(row["content_sha256"]),
        "payload": json.loads(str(row["payload_json"])),
        "metadata": RevisionMetadata.model_validate_json(str(row["metadata_json"])),
        "created_at": str(row["created_at"]),
    }


def save_revision_row(
    db_path: Path,
    project_id: str,
    *,
    revision_id: str,
    rev_kind: str,
    rev_key: str,
    parent_rev_id: str | None,
    payload: BaseModel,
    metadata: RevisionMetadata,
) -> str:
    if rev_kind not in VALID_REV_KINDS:
        raise OpenDVError(
            f"Invalid revision kind '{rev_kind}'.",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        )
    if not rev_key.strip() or not revision_id.strip():
        raise OpenDVError(
            "revision_id and rev_key must be non-empty.",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        )

    content_sha256 = sha256_jcs(payload)
    payload_json = payload.model_dump_json()
    metadata_json = metadata.model_dump_json()
    now_iso = utc_now().isoformat()

    with begin_immediate(db_path) as conn:
        require_project_row(conn, project_id)
        existing = conn.execute(
            """
            SELECT revision_id FROM revisions
            WHERE project_id = ? AND rev_kind = ? AND rev_key = ?
            """,
            (project_id, rev_kind, rev_key),
        ).fetchone()
        if existing is not None:
            if rev_kind not in MUTABLE_REV_KINDS:
                raise OpenDVError(
                    f"Revision ({rev_kind}, {rev_key}) already exists in '{project_id}'.",
                    error_code=ErrorCode.REVISION_CONFLICT,
                )
            conn.execute(
                """
                UPDATE revisions
                SET parent_rev_id = ?, content_sha256 = ?, payload_json = ?,
                    metadata_json = ?, created_at = ?
                WHERE project_id = ? AND rev_kind = ? AND rev_key = ?
                """,
                (
                    parent_rev_id,
                    content_sha256,
                    payload_json,
                    metadata_json,
                    now_iso,
                    project_id,
                    rev_kind,
                    rev_key,
                ),
            )
        else:
            id_conflict = conn.execute(
                "SELECT rev_kind, rev_key FROM revisions WHERE project_id = ? AND revision_id = ?",
                (project_id, revision_id),
            ).fetchone()
            if id_conflict is not None:
                raise OpenDVError(
                    f"Revision ID '{revision_id}' already exists in project '{project_id}' "
                    f"for ({id_conflict['rev_kind']}, {id_conflict['rev_key']}).",
                    error_code=ErrorCode.REVISION_CONFLICT,
                )
            conn.execute(
                """
                INSERT INTO revisions (
                    revision_id, project_id, rev_kind, rev_key, parent_rev_id,
                    content_sha256, payload_json, metadata_json, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    revision_id,
                    project_id,
                    rev_kind,
                    rev_key,
                    parent_rev_id,
                    content_sha256,
                    payload_json,
                    metadata_json,
                    now_iso,
                ),
            )
        conn.execute(
            "UPDATE projects SET updated_at = ? WHERE project_id = ?",
            (now_iso, project_id),
        )
    return content_sha256


def get_revision_row(db_path: Path, project_id: str, revision_id: str) -> dict[str, Any]:
    conn = open_wal_connection(db_path)
    try:
        require_project_row(conn, project_id)
        row = conn.execute(
            "SELECT * FROM revisions WHERE project_id = ? AND revision_id = ?",
            (project_id, revision_id),
        ).fetchone()
        if row is None:
            raise OpenDVError(
                f"Revision '{revision_id}' not found in project '{project_id}'.",
                error_code=ErrorCode.RESOURCE_NOT_FOUND,
            )
        return _row_to_revision_dict(row)
    finally:
        conn.close()


def get_revision_by_key_row(
    db_path: Path,
    project_id: str,
    rev_kind: str,
    rev_key: str,
) -> dict[str, Any] | None:
    conn = open_wal_connection(db_path)
    try:
        require_project_row(conn, project_id)
        row = conn.execute(
            "SELECT * FROM revisions WHERE project_id = ? AND rev_kind = ? AND rev_key = ?",
            (project_id, rev_kind, rev_key),
        ).fetchone()
        return _row_to_revision_dict(row) if row is not None else None
    finally:
        conn.close()


def list_revision_rows(
    db_path: Path,
    project_id: str,
    *,
    rev_kind: str | None = None,
) -> list[dict[str, Any]]:
    if rev_kind is not None and rev_kind not in VALID_REV_KINDS:
        raise OpenDVError(
            f"Invalid revision kind '{rev_kind}'.",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        )
    conn = open_wal_connection(db_path)
    try:
        require_project_row(conn, project_id)
        if rev_kind is not None:
            rows = conn.execute(
                """
                SELECT * FROM revisions
                WHERE project_id = ? AND rev_kind = ?
                ORDER BY created_at ASC, revision_id ASC
                """,
                (project_id, rev_kind),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT * FROM revisions
                WHERE project_id = ?
                ORDER BY created_at ASC, revision_id ASC
                """,
                (project_id,),
            ).fetchall()
        return [_row_to_revision_dict(r) for r in rows]
    finally:
        conn.close()


__all__ = [
    "delete_source_row",
    "get_revision_by_key_row",
    "get_revision_row",
    "get_source_row",
    "list_revision_rows",
    "list_source_rows",
    "require_project_row",
    "save_revision_row",
    "upsert_source_row",
    "utc_now",
]
