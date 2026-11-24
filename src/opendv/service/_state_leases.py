"""SQLite event/receipt persistence, control-signal polling, and PID+TTL lease helpers."""

from __future__ import annotations

import json
import os
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from opendv.models.errors import ErrorCode, OpenDVError
from opendv.models.events import ControlSignalRecord
from opendv.models.json_hasher import sha256_jcs
from opendv.service._state_ddl import (
    VALID_RECORD_KINDS,
    begin_immediate,
    open_wal_connection,
)
from opendv.service._state_records import require_project_row, utc_now
from opendv.service.os_platform import get_process_start_ts, is_process_instance_alive


def is_lease_row_live(row: sqlite3.Row, now: datetime) -> bool:
    """Return True iff lease TTL is unexpired and (holder_pid, process_start_ts) is live."""
    try:
        expires_dt = datetime.fromisoformat(str(row["expires_at"]))
    except ValueError:
        return False
    if expires_dt.tzinfo is None:
        expires_dt = expires_dt.replace(tzinfo=UTC)
    if now >= expires_dt:
        return False
    return is_process_instance_alive(int(row["holder_pid"]), int(row["process_start_ts"]))


def append_event_row(
    db_path: Path,
    project_id: str,
    *,
    record_kind: str,
    run_id: str | None,
    payload: BaseModel,
) -> int:
    if record_kind not in VALID_RECORD_KINDS:
        raise OpenDVError(
            f"Invalid record_kind '{record_kind}'.",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        )
    content_sha256 = sha256_jcs(payload)
    payload_json = payload.model_dump_json()
    now_iso = utc_now().isoformat()

    with begin_immediate(db_path) as conn:
        require_project_row(conn, project_id)
        cur = conn.execute(
            """
            INSERT INTO events_and_receipts (
                project_id, record_kind, run_id, payload_json, content_sha256, created_at
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (project_id, record_kind, run_id, payload_json, content_sha256, now_iso),
        )
        return int(cur.lastrowid or 0)


def list_event_rows(
    db_path: Path,
    project_id: str,
    *,
    record_kind: str | None = None,
    run_id: str | None = None,
) -> list[dict[str, Any]]:
    if record_kind is not None and record_kind not in VALID_RECORD_KINDS:
        raise OpenDVError(
            f"Invalid record_kind '{record_kind}'.",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        )
    clauses = ["project_id = ?"]
    params: list[Any] = [project_id]
    if record_kind is not None:
        clauses.append("record_kind = ?")
        params.append(record_kind)
    if run_id is not None:
        clauses.append("run_id = ?")
        params.append(run_id)
    sql = (
        "SELECT * FROM events_and_receipts WHERE "
        + " AND ".join(clauses)
        + " ORDER BY seq_id ASC"
    )
    conn = open_wal_connection(db_path)
    try:
        require_project_row(conn, project_id)
        rows = conn.execute(sql, params).fetchall()
        return [
            {
                "seq_id": int(r["seq_id"]),
                "project_id": str(r["project_id"]),
                "record_kind": str(r["record_kind"]),
                "run_id": str(r["run_id"]) if r["run_id"] is not None else None,
                "payload": json.loads(str(r["payload_json"])),
                "content_sha256": str(r["content_sha256"]),
                "created_at": str(r["created_at"]),
            }
            for r in rows
        ]
    finally:
        conn.close()


def poll_control_signal_rows(
    db_path: Path,
    project_id: str,
    run_id: str,
    *,
    after_seq: int = 0,
) -> list[ControlSignalRecord]:
    offset = max(0, int(after_seq))
    conn = open_wal_connection(db_path)
    try:
        rows = conn.execute(
            """
            SELECT payload_json FROM events_and_receipts
            WHERE project_id = ? AND record_kind = 'control_signal' AND run_id = ?
            ORDER BY seq_id ASC
            LIMIT -1 OFFSET ?
            """,
            (project_id, run_id, offset),
        ).fetchall()
        return [ControlSignalRecord.model_validate_json(str(r["payload_json"])) for r in rows]
    finally:
        conn.close()


def claim_lease_row(
    db_path: Path,
    project_id: str,
    *,
    lease_key: str,
    holder_run_id: str,
    ttl_seconds: int = 45,
) -> None:
    if ttl_seconds < 1:
        raise OpenDVError(
            f"ttl_seconds must be >= 1, got {ttl_seconds}.",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        )
    now = utc_now()
    now_iso = now.isoformat()
    expires_iso = (now + timedelta(seconds=ttl_seconds)).isoformat()
    pid = os.getpid()
    start_ts = get_process_start_ts(pid)

    with begin_immediate(db_path) as conn:
        require_project_row(conn, project_id)
        row = conn.execute(
            "SELECT * FROM leases WHERE lease_key = ?",
            (lease_key,),
        ).fetchone()
        if (
            row is not None
            and str(row["holder_run_id"]) != holder_run_id
            and is_lease_row_live(row, now)
        ):
            raise OpenDVError(
                f"Lease '{lease_key}' is active for run '{row['holder_run_id']}'.",
                error_code=ErrorCode.PROJECT_JOB_ACTIVE,
            )
        conn.execute(
            """
            INSERT INTO leases (
                lease_key, project_id, holder_run_id, holder_pid,
                process_start_ts, heartbeat_at, expires_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(lease_key) DO UPDATE SET
                project_id = excluded.project_id,
                holder_run_id = excluded.holder_run_id,
                holder_pid = excluded.holder_pid,
                process_start_ts = excluded.process_start_ts,
                heartbeat_at = excluded.heartbeat_at,
                expires_at = excluded.expires_at
            """,
            (lease_key, project_id, holder_run_id, pid, start_ts, now_iso, expires_iso),
        )


def renew_lease_row(
    db_path: Path,
    project_id: str,
    *,
    lease_key: str,
    holder_run_id: str,
    ttl_seconds: int = 45,
) -> None:
    if ttl_seconds < 1:
        raise OpenDVError(
            f"ttl_seconds must be >= 1, got {ttl_seconds}.",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        )
    now = utc_now()
    now_iso = now.isoformat()
    expires_iso = (now + timedelta(seconds=ttl_seconds)).isoformat()

    with begin_immediate(db_path) as conn:
        require_project_row(conn, project_id)
        row = conn.execute(
            "SELECT * FROM leases WHERE lease_key = ? AND project_id = ?",
            (lease_key, project_id),
        ).fetchone()
        if row is None:
            raise OpenDVError(
                f"Lease '{lease_key}' does not exist for project '{project_id}'.",
                error_code=ErrorCode.RESOURCE_NOT_FOUND,
            )
        if str(row["holder_run_id"]) != holder_run_id:
            raise OpenDVError(
                f"Lease '{lease_key}' is held by '{row['holder_run_id']}', not '{holder_run_id}'.",
                error_code=ErrorCode.PROJECT_JOB_ACTIVE,
            )
        conn.execute(
            """
            UPDATE leases
            SET heartbeat_at = ?, expires_at = ?
            WHERE lease_key = ? AND project_id = ?
            """,
            (now_iso, expires_iso, lease_key, project_id),
        )


def release_lease_row(
    db_path: Path,
    project_id: str,
    *,
    lease_key: str,
    holder_run_id: str | None = None,
) -> None:
    with begin_immediate(db_path) as conn:
        if holder_run_id is None:
            conn.execute(
                "DELETE FROM leases WHERE lease_key = ? AND project_id = ?",
                (lease_key, project_id),
            )
        else:
            conn.execute(
                "DELETE FROM leases WHERE lease_key = ? AND project_id = ? AND holder_run_id = ?",
                (lease_key, project_id, holder_run_id),
            )


__all__ = [
    "append_event_row",
    "claim_lease_row",
    "is_lease_row_live",
    "list_event_rows",
    "poll_control_signal_rows",
    "release_lease_row",
    "renew_lease_row",
]
