"""Headless Studio source-parsing job runner (`run_parse_session_sources`)."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from opendv_contracts.dtos.ingestion import EvidenceChunk, TableStructure, TaintClassification
from opendv_contracts.errors import OpenDVError
from opendv_service.studio.atomic_commit import ForbiddenAdminRequiredError, SourceNotFoundError
from opendv_service.studio.job_runners_evidence import (
    _atomic_write_bytes,
    _iter_manifest_entries_deduped,
    _load_source_privacy_and_lifecycle_state,
    _max_taint,
    build_studio_ingestion_service,
    load_active_session_evidence,
)
from opendv_service.studio.locks import reentrant_session_lock
from opendv_service.studio.session_manager import StudioSessionManager
from opendv_service.studio.sse_journal import SSEJournalWriter
from opendv_sources.exceptions import (
    OCRUnavailableError,
    OfficeConversionUnavailableError,
    UnsupportedFormatError,
)
from opendv_sources.ingestion.service import IngestionService

logger = logging.getLogger(__name__)


def _should_normalize_crlf(filename: str, mime_type: str | None, entry: dict[str, Any]) -> bool:
    """Return True if raw text bytes should normalize CRLF -> LF before offset slicing."""
    if str(entry.get("kind") or "").lower() in ("inline_note", "text", "markdown", "note"):
        return True
    if mime_type and mime_type.lower().startswith("text/"):
        return True
    suffix = Path(filename).suffix.lower()
    return suffix in {".txt", ".md", ".markdown"}


def _persist_parsed_evidence(
    session_dir: Path,
    session_id: str,
    parsed_source_ids: set[str],
    parsed_chunks: list[EvidenceChunk],
    parsed_tables: list[TableStructure],
) -> None:
    lock_file = session_dir / ".session.lock"
    with reentrant_session_lock(lock_file, session_id=session_id):
        existing_chunks, existing_tables = load_active_session_evidence(session_dir)

        def _chunk_source_id(chk: EvidenceChunk) -> str:
            c_meta = chk.metadata or {}
            return str(c_meta.get("source_id") or chk.document_id or "")

        def _chunk_key(chk: EvidenceChunk) -> tuple[str, str]:
            c_meta = chk.metadata or {}
            src_key = str(c_meta.get("source_id") or chk.document_id or chk.source_sha256 or "")
            return (src_key, chk.chunk_id)

        filtered_existing_chunks = [
            c for c in existing_chunks if _chunk_source_id(c) not in parsed_source_ids
        ]
        if parsed_chunks or len(filtered_existing_chunks) != len(existing_chunks):
            seen_chunk_keys = {_chunk_key(c) for c in parsed_chunks}
            combined_chunks = [
                c for c in filtered_existing_chunks if _chunk_key(c) not in seen_chunk_keys
            ] + parsed_chunks
            chunks_payload = (
                (
                    "\n".join(
                        json.dumps(c.model_dump(mode="json", exclude_none=True))
                        for c in combined_chunks
                    )
                    + "\n"
                )
                if combined_chunks
                else ""
            ).encode("utf-8")
            _atomic_write_bytes(session_dir / "evidence" / "chunks.jsonl", chunks_payload)

        def _table_source_id(tbl: TableStructure) -> str:
            t_meta = tbl.metadata or {}
            return str(t_meta.get("source_id") or "")

        def _table_key(tbl: TableStructure) -> tuple[str, str]:
            t_meta = tbl.metadata or {}
            src_key = str(t_meta.get("source_id") or t_meta.get("source_sha256") or "")
            return (src_key, tbl.table_id)

        filtered_existing_tables = [
            t for t in existing_tables if _table_source_id(t) not in parsed_source_ids
        ]
        if parsed_tables or len(filtered_existing_tables) != len(existing_tables):
            seen_table_keys = {_table_key(t) for t in parsed_tables}
            combined_tables = [
                t for t in filtered_existing_tables if _table_key(t) not in seen_table_keys
            ] + parsed_tables
            tables_payload = (
                (
                    "\n".join(
                        json.dumps(t.model_dump(mode="json", exclude_none=True))
                        for t in combined_tables
                    )
                    + "\n"
                )
                if combined_tables
                else ""
            ).encode("utf-8")
            _atomic_write_bytes(session_dir / "evidence" / "tables.jsonl", tables_payload)


def run_parse_session_sources(
    session_manager: StudioSessionManager,
    session_id: str,
    *,
    job_id: str = "parse",
    source_ids: Sequence[str] | None = None,
    ocr_mode: str = "auto",
    actor_role: str = "admin",
    writer: SSEJournalWriter | None = None,
    is_cancelled: Callable[[], bool] | None = None,
    on_progress: Callable[[str, int], None] | None = None,
    ingestion_service: IngestionService | None = None,
    fail_fast_on_source_error: bool = False,
) -> dict[str, Any]:
    """Parse active CAS sources into `evidence/chunks.jsonl` and `evidence/tables.jsonl`."""
    if actor_role == "viewer":
        raise ForbiddenAdminRequiredError("Viewer role is not permitted to parse session sources.")

    session_dir = session_manager.get_session_dir(session_id)
    manifest_file = session_dir / "inputs" / "manifest.json"
    if not manifest_file.is_file():
        if source_ids:
            raise SourceNotFoundError(
                f"Requested source(s) {list(source_ids)} not found in session '{session_id}'."
            )
        return {"parsed_sources_count": 0, "chunks_count": 0, "tables_count": 0}

    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        manifest = {}

    active_entries = [
        entry
        for entry in _iter_manifest_entries_deduped(manifest)
        if not (entry.get("tombstoned") or entry.get("deleted"))
    ]

    if source_ids is not None and len(source_ids) > 0:
        requested_set = {str(s) for s in source_ids}
        available_by_id = {str(e.get("source_id") or e.get("id") or ""): e for e in active_entries}
        missing = [sid for sid in source_ids if sid not in available_by_id]
        if missing:
            raise SourceNotFoundError(
                f"Source(s) {missing} not found or tombstoned in session '{session_id}'."
            )
        files = [
            e
            for e in active_entries
            if str(e.get("source_id") or e.get("id") or "") in requested_set
        ]
    else:
        files = active_entries

    svc = ingestion_service or build_studio_ingestion_service(ocr_mode=ocr_mode)
    cas = session_manager.get_cas(session_id)
    privacy_state = _load_source_privacy_and_lifecycle_state(session_dir)
    sticky_dns_ids: set[str] = privacy_state["do_not_send_ids"]
    sticky_dns_shas: set[str] = privacy_state["do_not_send_shas"]
    sticky_taint_by_id: dict[str, TaintClassification] = privacy_state["taint_by_id"]
    sticky_taint_by_sha: dict[str, TaintClassification] = privacy_state["taint_by_sha"]

    parsed_chunks: list[EvidenceChunk] = []
    parsed_tables: list[TableStructure] = []
    parsed_source_ids: set[str] = set()
    failed_sources: list[dict[str, str]] = []
    total_files = len(files)

    for idx, entry in enumerate(files, 1):
        if is_cancelled is not None and is_cancelled():
            break

        if on_progress is not None:
            pct = int(10 + (idx / max(1, total_files)) * 80)
            on_progress(f"parsing_file_{idx}_of_{total_files}", pct)

        source_id = str(entry.get("source_id") or entry.get("id") or "")
        source_sha = str(
            entry.get("sha256")
            or entry.get("blob_sha256")
            or entry.get("content_sha256")
            or entry.get("source_sha256")
            or ""
        ).lower()
        filename = str(entry.get("filename") or "unknown.txt")
        mime_type = entry.get("mime_type") if isinstance(entry.get("mime_type"), str) else None
        raw_taint = entry.get("taint") or entry.get("taint_classification")
        is_sticky_dns = bool(
            entry.get("do_not_send_to_agent") is True
            or str(raw_taint or "").strip().upper() == "DO_NOT_SEND"
            or (source_id and source_id in sticky_dns_ids)
            or (source_sha and source_sha in sticky_dns_shas)
        )
        source_taint = _max_taint(
            raw_taint,
            sticky_taint_by_id.get(source_id) if source_id else None,
            sticky_taint_by_sha.get(source_sha) if source_sha else None,
            TaintClassification.CONFIDENTIAL if is_sticky_dns else None,
        )
        eff_ocr_mode = (
            str(entry["ocr_mode"]).strip().lower()
            if ocr_mode == "auto" and isinstance(entry.get("ocr_mode"), str) and entry["ocr_mode"].strip()
            else ocr_mode
        )

        try:
            raw_bytes = cas.get(source_sha)
            if _should_normalize_crlf(filename, mime_type, entry):
                raw_bytes = raw_bytes.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
            res = svc.ingest_bytes(
                data=raw_bytes,
                filename=filename,
                mime_type=mime_type,
                taint=source_taint,
                ocr_mode=eff_ocr_mode,
            )
            if source_id:
                parsed_source_ids.add(source_id)
            scanned_pages = {
                pm.page_number for pm in (res.page_map or []) if getattr(pm, "is_scanned", False)
            }
            tabular_pages = {
                t.page_number
                for t in (res.tables or [])
                if getattr(t, "page_number", None) is not None
            }
            for c in res.chunks:
                stamped_meta = dict(c.metadata or {})
                if source_id:
                    stamped_meta["source_id"] = source_id
                if source_sha:
                    stamped_meta["source_sha256"] = source_sha
                stamped_meta.setdefault("filename", filename)
                if "layout_complexity" not in stamped_meta:
                    page_num = getattr(c, "page_number", None)
                    if page_num in scanned_pages:
                        stamped_meta["layout_complexity"] = "COMPLEX"
                    elif page_num in tabular_pages or "|" in (c.text or ""):
                        stamped_meta["layout_complexity"] = "TABULAR"
                    else:
                        stamped_meta["layout_complexity"] = "SIMPLE"
                is_chunk_dns = bool(
                    is_sticky_dns or stamped_meta.get("do_not_send_to_agent") is True
                )
                eff_chunk_taint = (
                    _max_taint(
                        source_taint,
                        c.taint,
                        stamped_meta.get("taint"),
                        TaintClassification.CONFIDENTIAL if is_chunk_dns else None,
                    )
                    or TaintClassification.INTERNAL
                )
                if is_chunk_dns:
                    stamped_meta["do_not_send_to_agent"] = True
                stamped_chunk = c.model_copy(
                    update={
                        "document_id": source_id or c.document_id,
                        "source_sha256": source_sha or c.source_sha256,
                        "taint": eff_chunk_taint,
                        "metadata": stamped_meta,
                    }
                )
                parsed_chunks.append(stamped_chunk)
                if writer is not None:
                    writer.buffer_event(
                        "chunk_parsed",
                        {
                            "job_id": job_id,
                            "source_id": source_id,
                            "chunk_id": stamped_chunk.chunk_id,
                            "page_number": stamped_chunk.page_number or 1,
                            "byte_length": len(stamped_chunk.text.encode("utf-8")),
                        },
                    )
            if res.tables:
                for t in res.tables:
                    t_meta = dict(t.metadata or {})
                    if source_id:
                        t_meta["source_id"] = source_id
                    if source_sha:
                        t_meta["source_sha256"] = source_sha
                    is_tbl_dns = bool(is_sticky_dns or t_meta.get("do_not_send_to_agent") is True)
                    eff_tbl_taint = _max_taint(
                        source_taint,
                        t.taint,
                        t_meta.get("taint"),
                        TaintClassification.CONFIDENTIAL if is_tbl_dns else None,
                    )
                    if is_tbl_dns:
                        t_meta["do_not_send_to_agent"] = True
                    stamped_table = t.model_copy(
                        update={"taint": eff_tbl_taint, "metadata": t_meta}
                    )
                    parsed_tables.append(stamped_table)
        except (OCRUnavailableError, OfficeConversionUnavailableError, UnsupportedFormatError):
            raise
        except OpenDVError:
            if fail_fast_on_source_error or total_files == 1:
                raise
            logger.warning("Failed to ingest %s (%s)", filename, source_id, exc_info=True)
            failed_sources.append({"source_id": source_id, "filename": filename})
        except (OSError, ValueError, RuntimeError) as exc:
            if fail_fast_on_source_error:
                raise
            logger.warning("Failed to ingest %s: %s", filename, exc)
            failed_sources.append({"source_id": source_id, "filename": filename})

    if parsed_source_ids or parsed_chunks or parsed_tables:
        _persist_parsed_evidence(
            session_dir, session_id, parsed_source_ids, parsed_chunks, parsed_tables
        )

    if writer is not None:
        writer.flush()

    result: dict[str, Any] = {
        "parsed_sources_count": len(files) - len(failed_sources),
        "chunks_count": len(parsed_chunks),
        "tables_count": len(parsed_tables),
    }
    if failed_sources:
        result["failed_sources"] = failed_sources
    return result
