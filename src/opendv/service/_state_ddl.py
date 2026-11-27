"""SQLite WAL DDL, connection helpers, and JSONL run stream append/tailing for state_store."""

from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Final

from pydantic import ValidationError

from opendv.models.errors import ErrorCode, OpenDVError
from opendv.models.events import RunEventLogPage, RunStreamEvent

VALID_SOURCE_KINDS: Final[frozenset[str]] = frozenset(
    {
        "sqlite",
        "duckdb",
        "parquet",
        "csv",
        "xlsx",
        "databricks_uc",
        "pdf",
        "markdown",
        "docx",
        "domain_note",
    }
)

VALID_SOURCE_STATUSES: Final[frozenset[str]] = frozenset(
    {
        "READY_OFFLINE",
        "NEEDS_LOCAL_SECRET_BINDING",
        "METADATA_ONLY_BINARY_ABSENT",
    }
)

VALID_REV_KINDS: Final[frozenset[str]] = frozenset(
    {
        "ontology_version",
        "wave_ir",
        "evolution_lock",
        "horizon_assumption",
        "concept_memory",
        "staged_candidate",
        "ontology_proposal",
        "vault_waiver",
        "ontology_waiver",
    }
)

MUTABLE_REV_KINDS: Final[frozenset[str]] = frozenset(
    {
        "concept_memory",
        "staged_candidate",
        "ontology_proposal",
    }
)

VALID_RECORD_KINDS: Final[frozenset[str]] = frozenset(
    {
        "audit_log",
        "mcp_receipt",
        "ocr_receipt",
        "grounding_receipt",
        "run_checkpoint",
        "run_state",
        "attempt_snapshot",
        "control_signal",
    }
)

SCHEMA_DDL_STATEMENTS: Final[tuple[str, ...]] = (
    """
    CREATE TABLE IF NOT EXISTS projects (
        project_id TEXT PRIMARY KEY,
        domain_namespace TEXT NOT NULL,
        active_ontology_rev_id TEXT,
        active_wave_id TEXT,
        settings_snapshot_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS sources_and_tables (
        source_id TEXT NOT NULL,
        project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
        kind TEXT NOT NULL,
        uri_or_filename TEXT NOT NULL,
        binary_sha256 TEXT,
        connector_config_json TEXT NOT NULL,
        status TEXT NOT NULL,
        profile_json TEXT NOT NULL,
        document_map_json TEXT,
        tables_index_json TEXT NOT NULL,
        approved_scan_pages_json TEXT NOT NULL DEFAULT '[]',
        attached_at TEXT NOT NULL,
        PRIMARY KEY (project_id, source_id)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_sources_project_status
    ON sources_and_tables(project_id, status)
    """,
    """
    CREATE TABLE IF NOT EXISTS revisions (
        revision_id TEXT NOT NULL,
        project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
        rev_kind TEXT NOT NULL,
        rev_key TEXT NOT NULL,
        parent_rev_id TEXT,
        content_sha256 TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        metadata_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        PRIMARY KEY (project_id, revision_id),
        UNIQUE(project_id, rev_kind, rev_key)
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS events_and_receipts (
        seq_id INTEGER PRIMARY KEY AUTOINCREMENT,
        project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
        record_kind TEXT NOT NULL,
        run_id TEXT,
        payload_json TEXT NOT NULL,
        content_sha256 TEXT NOT NULL,
        created_at TEXT NOT NULL
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_events_proj_kind_run
    ON events_and_receipts(project_id, record_kind, run_id)
    """,
    """
    CREATE TABLE IF NOT EXISTS leases (
        lease_key TEXT PRIMARY KEY,
        project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
        holder_run_id TEXT NOT NULL,
        holder_pid INTEGER NOT NULL,
        process_start_ts INTEGER NOT NULL,
        heartbeat_at TEXT NOT NULL,
        expires_at TEXT NOT NULL
    )
    """,
)


def open_wal_connection(db_path: Path, *, ensure_wal: bool = False) -> sqlite3.Connection:
    """Open a sqlite3 connection with WAL, NORMAL sync, busy_timeout=5000, and foreign_keys=ON."""
    conn = sqlite3.connect(
        os.fspath(db_path),
        timeout=5.0,
        isolation_level=None,
        check_same_thread=False,
    )
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute("PRAGMA foreign_keys=ON")
        if ensure_wal:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
    except sqlite3.Error as exc:
        conn.close()
        raise OpenDVError(
            f"Failed to configure SQLite WAL connection at '{db_path}': {exc}",
            error_code=ErrorCode.INTERNAL_SERVER_ERROR,
        ) from exc
    return conn


@contextmanager
def begin_immediate(db_path: Path) -> Iterator[sqlite3.Connection]:
    """Open connection, acquire BEGIN IMMEDIATE write transaction, commit or rollback cleanly."""
    conn = open_wal_connection(db_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        yield conn
        conn.execute("COMMIT")
    except OpenDVError:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    except sqlite3.Error as exc:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise OpenDVError(
            f"SQLite transaction error on '{db_path.name}': {exc}",
            error_code=ErrorCode.INTERNAL_SERVER_ERROR,
        ) from exc
    finally:
        conn.close()


def append_jsonl_run_event(log_path: Path, event: RunStreamEvent) -> None:
    """Append a single RunStreamEvent frame as JSONL and fsync."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    line = event.model_dump_json() + "\n"
    try:
        with log_path.open("a", encoding="utf-8", newline="\n") as fh:
            fh.write(line)
            fh.flush()
            os.fsync(fh.fileno())
    except OSError as exc:
        raise OpenDVError(
            f"Failed to append RunStreamEvent to '{log_path.name}': {exc}",
            error_code=ErrorCode.INTERNAL_SERVER_ERROR,
        ) from exc


def tail_jsonl_run_events(
    log_path: Path,
    *,
    since_seq: int = 0,
    limit: int | None = None,
) -> RunEventLogPage:
    """Tail JSONL run events after since_seq, ignoring any incomplete trailing frame."""
    if not log_path.is_file():
        return RunEventLogPage(events=[], next_since_seq=None)
    if limit is not None and limit <= 0:
        raise OpenDVError(
            f"limit must be >= 1, got {limit}.",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        )

    try:
        raw_bytes = log_path.read_bytes()
    except OSError as exc:
        raise OpenDVError(
            f"Failed to read run event log '{log_path.name}': {exc}",
            error_code=ErrorCode.INTERNAL_SERVER_ERROR,
        ) from exc

    events: list[RunStreamEvent] = []
    raw_lines = raw_bytes.splitlines(keepends=True)
    for idx, raw_line in enumerate(raw_lines):
        is_last = idx == len(raw_lines) - 1
        if not raw_line.strip():
            continue
        is_incomplete_trailing = is_last and not raw_line.endswith(b"\n")
        try:
            line_text = raw_line.decode("utf-8").strip()
            ev = RunStreamEvent.model_validate_json(line_text)
        except (UnicodeDecodeError, ValidationError, json.JSONDecodeError, ValueError) as exc:
            if is_incomplete_trailing:
                break
            raise OpenDVError(
                f"Corrupt RunStreamEvent line {idx + 1} in '{log_path.name}': {exc}",
                error_code=ErrorCode.INTERNAL_SERVER_ERROR,
            ) from exc
        if ev.seq > since_seq:
            events.append(ev)

    events.sort(key=lambda e: e.seq)
    if limit is not None and len(events) > limit:
        page_events = events[:limit]
        return RunEventLogPage(events=page_events, next_since_seq=page_events[-1].seq)
    return RunEventLogPage(events=events, next_since_seq=None)


def _table_pk_columns(conn: sqlite3.Connection, table_name: str) -> list[str]:
    rows = conn.execute(f"PRAGMA table_info({table_name})").fetchall()
    pk_rows = [r for r in rows if int(r["pk"]) > 0]
    pk_rows.sort(key=lambda r: int(r["pk"]))
    return [str(r["name"]) for r in pk_rows]


def migrate_per_project_primary_keys(db_path: Path) -> None:
    """Migrate legacy single-column PKs on child tables to composite (project_id, *)."""
    conn = open_wal_connection(db_path)
    try:
        src_pk = _table_pk_columns(conn, "sources_and_tables")
        rev_pk = _table_pk_columns(conn, "revisions")
        need_src = src_pk == ["source_id"]
        need_rev = rev_pk == ["revision_id"]
        if not need_src and not need_rev:
            return
        conn.execute("PRAGMA foreign_keys=OFF")
        conn.execute("BEGIN IMMEDIATE")
        if need_src:
            conn.execute(
                """
                CREATE TABLE sources_and_tables_v2 (
                    source_id TEXT NOT NULL,
                    project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
                    kind TEXT NOT NULL,
                    uri_or_filename TEXT NOT NULL,
                    binary_sha256 TEXT,
                    connector_config_json TEXT NOT NULL,
                    status TEXT NOT NULL,
                    profile_json TEXT NOT NULL,
                    document_map_json TEXT,
                    tables_index_json TEXT NOT NULL,
                    approved_scan_pages_json TEXT NOT NULL DEFAULT '[]',
                    attached_at TEXT NOT NULL,
                    PRIMARY KEY (project_id, source_id)
                )
                """
            )
            conn.execute(
                """
                INSERT INTO sources_and_tables_v2 (
                    source_id, project_id, kind, uri_or_filename, binary_sha256,
                    connector_config_json, status, profile_json, document_map_json,
                    tables_index_json, approved_scan_pages_json, attached_at
                )
                SELECT
                    source_id, project_id, kind, uri_or_filename, binary_sha256,
                    connector_config_json, status, profile_json, document_map_json,
                    tables_index_json, approved_scan_pages_json, attached_at
                FROM sources_and_tables
                """
            )
            conn.execute("DROP TABLE sources_and_tables")
            conn.execute("ALTER TABLE sources_and_tables_v2 RENAME TO sources_and_tables")
            conn.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_sources_project_status
                ON sources_and_tables(project_id, status)
                """
            )
        if need_rev:
            conn.execute(
                """
                CREATE TABLE revisions_v2 (
                    revision_id TEXT NOT NULL,
                    project_id TEXT NOT NULL REFERENCES projects(project_id) ON DELETE CASCADE,
                    rev_kind TEXT NOT NULL,
                    rev_key TEXT NOT NULL,
                    parent_rev_id TEXT,
                    content_sha256 TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    PRIMARY KEY (project_id, revision_id),
                    UNIQUE(project_id, rev_kind, rev_key)
                )
                """
            )
            conn.execute(
                """
                INSERT INTO revisions_v2 (
                    revision_id, project_id, rev_kind, rev_key, parent_rev_id,
                    content_sha256, payload_json, metadata_json, created_at
                )
                SELECT
                    revision_id, project_id, rev_kind, rev_key, parent_rev_id,
                    content_sha256, payload_json, metadata_json, created_at
                FROM revisions
                """
            )
            conn.execute("DROP TABLE revisions")
            conn.execute("ALTER TABLE revisions_v2 RENAME TO revisions")
        conn.execute("COMMIT")
    except sqlite3.Error as exc:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise OpenDVError(
            f"Failed to migrate per-project primary keys in '{db_path.name}': {exc}",
            error_code=ErrorCode.INTERNAL_SERVER_ERROR,
        ) from exc
    finally:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.close()


__all__ = [
    "MUTABLE_REV_KINDS",
    "SCHEMA_DDL_STATEMENTS",
    "VALID_RECORD_KINDS",
    "VALID_REV_KINDS",
    "VALID_SOURCE_KINDS",
    "VALID_SOURCE_STATUSES",
    "append_jsonl_run_event",
    "begin_immediate",
    "migrate_per_project_primary_keys",
    "open_wal_connection",
    "tail_jsonl_run_events",
]
