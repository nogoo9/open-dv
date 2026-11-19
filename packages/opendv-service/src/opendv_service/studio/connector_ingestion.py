"""Multi-connector Studio ingestion and rich table/chunk evidence previews."""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections import Counter
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

import duckdb

from opendv_contracts.dtos.ingestion import TableStructure
from opendv_contracts.dtos.sources import NamespaceSpecDTO, SourceInspectRequestDTO
from opendv_contracts.errors import ErrorCode, OpenDVError
from opendv_service.studio.connector_ingestion_helpers import (
    SAMPLE_ROW_CAP,
    build_databricks_table_entry,
    build_table_entry,
    extract_markdown_table_data,
    infer_cas_extension,
    load_duckdb_table_from_rows,
    materialize_cas_tempfile,
    read_xlsx_rows,
    resolve_cas_connector_bytes,
    resolve_connector_local_path,
    strip_file_uri,
    validate_connector_payload,
)
from opendv_service.studio.job_runners_evidence import (
    _atomic_write_bytes,
    load_active_session_evidence,
)
from opendv_service.studio.locks import reentrant_session_lock
from opendv_service.studio.session_manager import StudioSessionManager
from opendv_service.use_cases.sources_inspect import inspect_source
from opendv_sources.adapters.filesystem import LocalFilesystemSourceAdapter
from opendv_sources.exceptions import SourceLoadError
from opendv_sources.models.specs import SourceSpec

SUPPORTED_CONNECTOR_FORMATS: Final[tuple[str, ...]] = (
    "sqlite",
    "duckdb",
    "csv",
    "tsv",
    "parquet",
    "xlsx",
    "jsonl",
    "databricks",
    "uc",
)
_TERM_RE: Final[re.Pattern[str]] = re.compile(r"\b[A-Za-z][A-Za-z0-9_]{2,}\b")


def _persist_connector_evidence(
    *,
    session_mgr: StudioSessionManager,
    session_id: str,
    source_id: str,
    source_filename: str,
    source_sha256: str,
    size_bytes: int,
    mime_type: str,
    adapter: str,
    parsed_tables: list[TableStructure],
    principal: str,
) -> dict[str, Any]:
    session_dir = session_mgr.get_session_dir(session_id)
    with reentrant_session_lock(session_dir / ".session.lock", session_id=session_id):
        manifest_path = session_dir / "inputs" / "manifest.json"
        mdata = (
            json.loads(manifest_path.read_text(encoding="utf-8"))
            if manifest_path.is_file()
            else {"files": []}
        )
        files = [f for f in mdata.get("files", []) if f.get("source_id") != source_id]
        files.append(
            {
                "source_id": source_id,
                "filename": source_filename,
                "sha256": source_sha256,
                "size_bytes": size_bytes,
                "mime_type": mime_type,
                "connector_adapter": adapter,
                "tombstoned": False,
                "uploaded_by": principal,
                "uploaded_at": datetime.now(UTC).isoformat(),
            }
        )
        mdata["files"] = files
        _atomic_write_bytes(manifest_path, json.dumps(mdata, indent=2).encode("utf-8"))

        _, existing_tables = load_active_session_evidence(session_dir)
        new_ids = {t.table_id for t in parsed_tables}
        merged = [t for t in existing_tables if t.table_id not in new_ids] + parsed_tables
        payload = (
            "\n".join(json.dumps(t.model_dump(mode="json", exclude_none=True)) for t in merged)
            + "\n"
        ).encode("utf-8")
        _atomic_write_bytes(session_dir / "evidence" / "tables.jsonl", payload)

    return {
        "session_id": session_id,
        "source_id": source_id,
        "adapter": adapter,
        "table_count": len(parsed_tables),
        "tables": [t.table_id for t in parsed_tables],
    }


def _profile_local_source_tables(
    *,
    session_mgr: StudioSessionManager,
    local_path: Path,
    adapter: str,
    ext: str,
    source_id: str,
    source_sha256: str,
    request_payload: dict[str, Any],
    platform_cas: Any | None = None,
    allowed_inspect_roots: list[Path] | None = None,
) -> list[TableStructure]:
    inspect_adapter = (
        ext if adapter == "file" and ext in {"csv", "tsv", "parquet", "jsonl"} else adapter
    )
    if inspect_adapter in {"sqlite", "duckdb", "csv", "tsv", "parquet", "jsonl"}:
        req = SourceInspectRequestDTO(
            adapter=inspect_adapter,
            uri=str(local_path),
            credentials_ref=request_payload.get("credentials_ref"),
            options=dict(request_payload.get("options") or {}),
        )
        try:
            inspect_source(
                req,
                cas_store=platform_cas,
                workspace_root=getattr(session_mgr, "workspace_root", None),
                allowed_roots=allowed_inspect_roots,
            )
        except (sqlite3.DatabaseError, duckdb.Error, SourceLoadError) as exc:
            raise OpenDVError(
                f"Corrupt or unreadable {inspect_adapter} source: {exc}",
                error_code=ErrorCode.CORRUPT_SOURCE_BLOB,
            ) from exc

    raw_req_uri = str(request_payload.get("uri") or "").strip()
    src_name = str(request_payload.get("source_name") or "").strip()
    tname_hint = (
        src_name
        if raw_req_uri.startswith("cas://") and src_name
        else (local_path.name if raw_req_uri.startswith("cas://") else (raw_req_uri or local_path.name))
    )
    default_tname = Path(strip_file_uri(tname_hint)).stem or local_path.stem

    parsed_tables: list[TableStructure] = []
    mem_conn = duckdb.connect(":memory:")
    try:
        try:
            if adapter == "sqlite" or ext in {"sqlite", "db", "sqlite3"}:
                sconn = sqlite3.connect(f"file:{local_path}?mode=ro", uri=True)
                try:
                    t_rows = sconn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
                    ).fetchall()
                    for (tname,) in t_rows:
                        qt = f'"{tname.replace(chr(34), chr(34) * 2)}"'
                        tinfo = sconn.execute(f"PRAGMA table_info({qt})").fetchall()
                        pk_cols = {str(r[1]) for r in tinfo if int(r[5] or 0) > 0}
                        fk_list = sconn.execute(f"PRAGMA foreign_key_list({qt})").fetchall()
                        fk_map = {str(r[3]): {"table": str(r[2]), "column": str(r[4])} for r in fk_list}
                        headers = [str(r[1]) for r in tinfo]
                        data_rows = sconn.execute(f"SELECT * FROM {qt} LIMIT 5000").fetchall()
                        load_duckdb_table_from_rows(mem_conn, tname, headers, data_rows)
                        parsed_tables.append(
                            build_table_entry(
                                conn=mem_conn,
                                table_name=tname,
                                pk_cols=pk_cols,
                                fk_map=fk_map,
                                source_id=source_id,
                                source_sha256=source_sha256,
                            )
                        )
                finally:
                    sconn.close()
            elif adapter == "duckdb" or ext == "duckdb":
                dconn = duckdb.connect(str(local_path), read_only=True)
                try:
                    t_rows = dconn.execute(
                        "SELECT table_name FROM information_schema.tables "
                        "WHERE table_schema = 'main' ORDER BY table_name"
                    ).fetchall()
                    for (tname,) in t_rows:
                        qt = f'"{tname.replace(chr(34), chr(34) * 2)}"'
                        cols = [str(r[0]) for r in dconn.execute(f"DESCRIBE {qt}").fetchall()]
                        data_rows = dconn.execute(f"SELECT * FROM {qt} LIMIT 5000").fetchall()
                        load_duckdb_table_from_rows(mem_conn, tname, cols, data_rows)
                        parsed_tables.append(
                            build_table_entry(
                                conn=mem_conn,
                                table_name=tname,
                                pk_cols=set(),
                                fk_map={},
                                source_id=source_id,
                                source_sha256=source_sha256,
                            )
                        )
                finally:
                    dconn.close()
            elif ext == "xlsx":
                headers, rows = read_xlsx_rows(local_path)
                tname = default_tname
                load_duckdb_table_from_rows(mem_conn, tname, headers, rows)
                parsed_tables.append(
                    build_table_entry(
                        conn=mem_conn,
                        table_name=tname,
                        pk_cols=set(),
                        fk_map={},
                        source_id=source_id,
                        source_sha256=source_sha256,
                    )
                )
            elif ext in {"csv", "tsv", "parquet", "jsonl"}:
                tname = default_tname
                fs_adapter = LocalFilesystemSourceAdapter(
                    workspace_root=getattr(session_mgr, "workspace_root", None),
                    allowed_roots=[local_path.parent],
                )
                fs_adapter.register(
                    SourceSpec(adapter="file", uri=str(local_path), format=ext),
                    mem_conn,
                    tname,
                )
                parsed_tables.append(
                    build_table_entry(
                        conn=mem_conn,
                        table_name=tname,
                        pk_cols=set(),
                        fk_map={},
                        source_id=source_id,
                        source_sha256=source_sha256,
                    )
                )
            else:
                raise OpenDVError(
                    f"Unsupported connector format '{ext or adapter}'. Supported: {SUPPORTED_CONNECTOR_FORMATS}",
                    error_code=ErrorCode.UNSUPPORTED_FORMAT,
                )
        except (sqlite3.DatabaseError, duckdb.Error, SourceLoadError) as exc:
            raise OpenDVError(
                f"Corrupt or unreadable {inspect_adapter} source: {exc}",
                error_code=ErrorCode.CORRUPT_SOURCE_BLOB,
            ) from exc
    finally:
        mem_conn.close()
    return parsed_tables


def _attach_databricks_connector(
    *,
    session_mgr: StudioSessionManager,
    session_id: str,
    adapter: str,
    raw_uri: str,
    request_payload: dict[str, Any],
    principal: str,
    allowed_hosts: Sequence[str] | None = None,
) -> dict[str, Any]:
    options = dict(request_payload.get("options") or {})
    if request_payload.get("table") and "table" not in options:
        options["table"] = request_payload["table"]
    ns_dto: NamespaceSpecDTO | None = None
    raw_ns = request_payload.get("namespace")
    if isinstance(raw_ns, dict):
        ns_dto = NamespaceSpecDTO.model_validate(raw_ns)
    elif isinstance(raw_ns, str) and raw_ns.strip():
        parts = [p.strip() for p in raw_ns.strip().split(".", 1)]
        ns_dto = NamespaceSpecDTO(
            catalog=parts[0] if len(parts) == 2 else None,
            schema_name=parts[-1],
        )
    req = SourceInspectRequestDTO(
        adapter=adapter,
        uri=raw_uri or "databricks://catalog",
        namespace=ns_dto,
        credentials_ref=request_payload.get("credentials_ref"),
        options=options,
    )
    eff_allowed_hosts = list(allowed_hosts) if allowed_hosts is not None else None
    inspect_resp = inspect_source(
        req,
        workspace_root=getattr(session_mgr, "workspace_root", None),
        allowed_hosts=eff_allowed_hosts,
    )
    canonical_bytes = json.dumps(inspect_resp.model_dump(mode="json"), sort_keys=True).encode("utf-8")
    source_sha256 = hashlib.sha256(canonical_bytes).hexdigest().lower()
    session_mgr.get_cas(session_id).put(canonical_bytes)
    source_id = f"src-{source_sha256[:16]}"
    parsed_tables = [
        build_databricks_table_entry(
            tbl.model_dump(mode="json") if hasattr(tbl, "model_dump") else dict(tbl),
            source_id=source_id,
            source_sha256=source_sha256,
        )
        for tbl in inspect_resp.tables
    ]
    source_filename = str(request_payload.get("source_name") or raw_uri or "databricks_uc")
    return _persist_connector_evidence(
        session_mgr=session_mgr,
        session_id=session_id,
        source_id=source_id,
        source_filename=source_filename,
        source_sha256=source_sha256,
        size_bytes=len(canonical_bytes),
        mime_type="application/x-databricks-uc",
        adapter=adapter,
        parsed_tables=parsed_tables,
        principal=principal,
    )


def attach_connector_source(
    *,
    session_mgr: StudioSessionManager,
    session_id: str,
    request_payload: dict[str, Any],
    principal: str = "human:local_user",
    platform_cas: Any | None = None,
    allowed_hosts: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Inspect, profile with GraphBProfiler, and persist tabular connector evidence."""
    validate_connector_payload(request_payload)
    adapter = str(request_payload.get("adapter") or "file").strip().lower()
    raw_uri = str(request_payload.get("uri") or request_payload.get("path") or "").strip()

    if adapter in {"databricks", "uc"}:
        return _attach_databricks_connector(
            session_mgr=session_mgr,
            session_id=session_id,
            adapter=adapter,
            raw_uri=raw_uri,
            request_payload=request_payload,
            principal=principal,
            allowed_hosts=allowed_hosts,
        )

    raw_cas_hash = str(request_payload.get("cas_hash") or "").strip()
    if not raw_cas_hash and raw_uri.startswith("cas://"):
        raw_cas_hash = raw_uri.removeprefix("cas://").split("/")[0].split("?")[0].strip()

    if raw_cas_hash:
        raw_bytes, source_sha256 = resolve_cas_connector_bytes(
            cas_hash=raw_cas_hash,
            session_mgr=session_mgr,
            session_id=session_id,
            platform_cas=platform_cas,
        )
        session_mgr.get_cas(session_id).put(raw_bytes)
        source_id = f"src-{source_sha256[:16]}"
        hint_name = str(
            request_payload.get("source_name")
            or ("" if raw_uri.startswith("cas://") else raw_uri)
            or source_sha256
        )
        ext = infer_cas_extension(raw_bytes, hint_name, adapter)
        source_filename = Path(strip_file_uri(hint_name)).name or f"{source_id}.{ext or 'bin'}"
        with materialize_cas_tempfile(raw_bytes, ext) as staged_path:
            parsed_tables = _profile_local_source_tables(
                session_mgr=session_mgr,
                local_path=staged_path,
                adapter=adapter,
                ext=ext,
                source_id=source_id,
                source_sha256=source_sha256,
                request_payload=request_payload,
                platform_cas=platform_cas,
                allowed_inspect_roots=[staged_path.parent],
            )
    else:
        if not raw_uri:
            raise OpenDVError(
                "Connector 'uri' is required.", error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT
            )
        local_path = resolve_connector_local_path(session_mgr, raw_uri)
        raw_bytes = local_path.read_bytes()
        source_sha256 = hashlib.sha256(raw_bytes).hexdigest().lower()
        session_mgr.get_cas(session_id).put(raw_bytes)
        source_id = f"src-{source_sha256[:16]}"
        ext = local_path.suffix.lower().lstrip(".")
        source_filename = local_path.name
        parsed_tables = _profile_local_source_tables(
            session_mgr=session_mgr,
            local_path=local_path,
            adapter=adapter,
            ext=ext,
            source_id=source_id,
            source_sha256=source_sha256,
            request_payload=request_payload,
            platform_cas=platform_cas,
            allowed_inspect_roots=None,
        )

    return _persist_connector_evidence(
        session_mgr=session_mgr,
        session_id=session_id,
        source_id=source_id,
        source_filename=source_filename,
        source_sha256=source_sha256,
        size_bytes=len(raw_bytes),
        mime_type=f"application/x-{ext or adapter}",
        adapter=adapter,
        parsed_tables=parsed_tables,
        principal=principal,
    )


def get_session_evidence_tables(
    session_mgr: StudioSessionManager, session_id: str
) -> dict[str, Any]:
    """Return enriched table profiles (sample_rows <= 50, PK/FK, entropy, FIBO gap hints)."""
    session_dir = session_mgr.get_session_dir(session_id)
    _, tables = load_active_session_evidence(session_dir)
    out: list[dict[str, Any]] = []
    for t in tables:
        meta = dict(t.metadata or {})
        cols = list(meta.get("columns") or [])
        if not cols and t.headers:
            cols = [
                {
                    "column_name": h,
                    "sql_type": "VARCHAR",
                    "is_primary_key": False,
                    "surrogate_hint": False,
                    "foreign_key": None,
                    "null_rate": 0.0,
                    "shannon_entropy": 0.0,
                }
                for h in t.headers
            ]
        tname = str(meta.get("table_name") or t.caption or t.table_id)
        out.append(
            {
                "table_id": t.table_id,
                "table_name": tname,
                "headers": list(t.headers),
                "sample_rows": list(t.rows[:SAMPLE_ROW_CAP]),
                "columns": cols,
                "fibo_gap_hints": list(meta.get("fibo_gap_hints") or []),
            }
        )
    return {"session_id": session_id, "tables": out}


def get_session_evidence_chunks(
    session_mgr: StudioSessionManager, session_id: str
) -> dict[str, Any]:
    """Return parsed evidence chunks with char spans, confidence, table_data, and term counts."""
    session_dir = session_mgr.get_session_dir(session_id)
    chunks, tables = load_active_session_evidence(session_dir)
    tbl_by_id = {
        t.table_id: {"headers": t.headers, "rows": t.rows[:SAMPLE_ROW_CAP]} for t in tables
    }
    term_counter: Counter[str] = Counter()
    out: list[dict[str, Any]] = []
    for c in chunks:
        for tok in _TERM_RE.findall(c.text or ""):
            term_counter[tok.lower()] += 1
        table_data = tbl_by_id.get(c.table_id) if c.table_id else None
        is_tbl = bool(c.is_table)
        if not is_tbl and "|" in (c.text or "") and "---" in (c.text or ""):
            extracted = extract_markdown_table_data(c.text or "")
            if extracted is not None:
                is_tbl = True
                table_data = extracted
        out.append(
            {
                "chunk_id": c.chunk_id,
                "document_id": c.document_id,
                "page_number": c.page_number or 1,
                "start_char": c.start_char,
                "end_char": c.end_char,
                "text": c.text,
                "confidence": c.confidence,
                "is_table": is_tbl,
                "table_id": c.table_id,
                "table_data": table_data,
            }
        )
    top_terms = dict(term_counter.most_common(100))
    return {"session_id": session_id, "chunks": out, "observed_term_counts": top_terms}


__all__ = [
    "SAMPLE_ROW_CAP",
    "SUPPORTED_CONNECTOR_FORMATS",
    "attach_connector_source",
    "get_session_evidence_chunks",
    "get_session_evidence_tables",
]
