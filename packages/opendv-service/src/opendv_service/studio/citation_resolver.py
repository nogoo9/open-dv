"""Batch citation resolver for Studio EvidenceRef drawer previews (FR-CIT-02)."""

from __future__ import annotations

import contextlib
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from opendv_contracts.dtos.ontology import EvidenceRef
from opendv_service.studio.connector_ingestion import get_session_evidence_tables
from opendv_service.studio.job_runners_evidence import load_active_session_evidence
from opendv_service.studio.session_manager import StudioSessionManager
from opendv_sources.fibo.loader import get_default_fibo_loader


def _load_manifest_by_label_and_id(session_dir: Path) -> tuple[dict[str, str], dict[str, str]]:
    mpath = session_dir / "inputs" / "manifest.json"
    by_label: dict[str, str] = {}
    by_id: dict[str, str] = {}
    if not mpath.is_file():
        return by_label, by_id
    with contextlib.suppress(OSError, json.JSONDecodeError):
        mdata = json.loads(mpath.read_text(encoding="utf-8"))
        for entry in mdata.get("files", []):
            if entry.get("tombstoned") or entry.get("deleted"):
                continue
            sha = str(entry.get("sha256") or "").lower()
            if not sha:
                continue
            fname = str(entry.get("filename") or "")
            sid = str(entry.get("source_id") or "")
            if fname:
                by_label[fname] = sha
            if sid:
                by_id[sid] = sha
    return by_label, by_id


def resolve_session_citations(
    session_mgr: StudioSessionManager,
    session_id: str,
    citations: Sequence[EvidenceRef | dict[str, Any]],
) -> dict[str, Any]:
    """Resolve `document_span`, `column_profile`, and `standard_reference` citations."""
    session_dir = session_mgr.get_session_dir(session_id)
    cas = session_mgr.get_cas(session_id)
    chunks, _ = load_active_session_evidence(session_dir)
    tables_resp = get_session_evidence_tables(session_mgr, session_id)
    tables_list: list[dict[str, Any]] = tables_resp.get("tables", [])
    by_label, by_id = _load_manifest_by_label_and_id(session_dir)
    chunk_map = {c.chunk_id: c for c in chunks}

    fibo_loader = None
    with contextlib.suppress(Exception):
        fibo_loader = get_default_fibo_loader()

    resolved: list[dict[str, Any]] = []
    for raw_ref in citations:
        ref = raw_ref if isinstance(raw_ref, EvidenceRef) else EvidenceRef.model_validate(raw_ref)
        item: dict[str, Any] = {
            "evidence_type": ref.evidence_type,
            "citation": ref.model_dump(mode="json", exclude_none=True),
            "highlighted_span": None,
            "table_profile": None,
            "fibo_definition": None,
        }

        if ref.evidence_type == "document_span":
            span_text: str | None = None
            target_sha = (ref.doc_sha256 or "").lower() or by_label.get(ref.source_label or "", "")
            if not target_sha and ref.chunk_id and ref.chunk_id in chunk_map:
                chk = chunk_map[ref.chunk_id]
                target_sha = (chk.source_sha256 or "").lower() or by_id.get(chk.document_id, "")
            if target_sha and ref.char_start is not None and ref.char_end is not None:
                with contextlib.suppress(Exception):
                    raw_text = (
                        cas.get(target_sha).decode("utf-8", errors="replace").replace("\r\n", "\n")
                    )
                    if ref.snippet and raw_text[ref.char_start :].startswith(ref.snippet):
                        span_text = ref.snippet
                    elif 0 <= ref.char_start <= ref.char_end <= len(raw_text):
                        span_text = raw_text[ref.char_start : ref.char_end]
            if not span_text and ref.chunk_id and ref.chunk_id in chunk_map:
                span_text = chunk_map[ref.chunk_id].text
            if not span_text and ref.snippet:
                span_text = ref.snippet
            item["highlighted_span"] = span_text

        elif ref.evidence_type == "column_profile":
            matched_tbl = None
            for tbl in tables_list:
                if ref.table_id and tbl["table_id"] == ref.table_id:
                    matched_tbl = tbl
                    break
                if ref.bronze_table and (
                    tbl["table_name"] == ref.bronze_table
                    or tbl["table_name"].endswith(ref.bronze_table)
                ):
                    matched_tbl = tbl
                    break
            if matched_tbl is not None:
                col_prof = None
                if ref.column_name:
                    col_prof = next(
                        (
                            c
                            for c in matched_tbl.get("columns", [])
                            if c["column_name"] == ref.column_name
                        ),
                        None,
                    )
                item["table_profile"] = {
                    "table_id": matched_tbl["table_id"],
                    "table_name": matched_tbl["table_name"],
                    "column_name": ref.column_name,
                    "column_profile": col_prof,
                    "sample_rows": matched_tbl.get("sample_rows", [])[:5],
                }

        elif ref.evidence_type == "standard_reference":
            target_uri = (ref.fibo_uri or ref.reference_uri or "").strip()
            if target_uri and fibo_loader is not None:
                with contextlib.suppress(Exception):
                    hit = fibo_loader.lookup_by_uri(target_uri)
                    if hit is not None:
                        item["fibo_definition"] = {
                            "uri": hit.get("uri") or target_uri,
                            "label": hit.get("label") or "",
                            "definition": hit.get("definition") or "",
                            "module": hit.get("module") or "",
                        }

        resolved.append(item)

    return {"session_id": session_id, "resolved": resolved}


__all__ = ["resolve_session_citations"]
