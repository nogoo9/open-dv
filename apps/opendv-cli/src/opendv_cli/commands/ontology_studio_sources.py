"""Ontology Studio CLI source ingestion, text notes, parse, and connector attachment handlers."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from opendv_cli.commands.ontology_studio_common import (
    _atomic_write_json,
    _err,
    _err_from_opendv_error,
    _err_from_workflow_error,
    _require_session_context,
    _resolve_studio_principal,
)
from opendv_contracts.dtos.cas import TextSourceUploadRequest
from opendv_contracts.errors import OpenDVError
from opendv_service.studio.bundle import compute_effective_session_taint
from opendv_service.studio.delta_normalizer import StudioWorkflowError
from opendv_service.studio.job_runners import run_parse_session_sources
from opendv_service.studio.locks import reentrant_session_lock
from opendv_service.studio.wizard_workflows import attach_connector_workflow
from opendv_sources.exceptions import (
    OCRUnavailableError,
    OfficeConversionUnavailableError,
    UnsupportedFormatError,
)

if TYPE_CHECKING:
    from opendv_cli.executor import LocalExecutor


def handle_upload(args: argparse.Namespace, executor: LocalExecutor | None = None) -> tuple[int, Any]:
    """6. `opendv ontology studio upload -s <session> -f <file>` (sticky taint)."""
    auth_res = _resolve_studio_principal(args)
    if isinstance(auth_res[0], int):
        return auth_res  # type: ignore[return-value]
    _, role_tier = auth_res  # type: ignore[misc]
    if role_tier == "viewer":
        return _err(7, "FORBIDDEN_ADMIN_REQUIRED", "Read-only viewer role is not permitted to upload sources.")
    fpath_raw = getattr(args, "file", None)
    if not fpath_raw:
        return _err(2, "SYNTAX_INVALID_ARGUMENT", "Missing required --file argument")
    ctx = _require_session_context(args, executor)
    if isinstance(ctx[0], int):
        return ctx  # type: ignore[return-value]
    session_mgr, _, sdir, sid = ctx  # type: ignore[misc]
    fpath = Path(fpath_raw)
    if not fpath.is_absolute() and executor:
        fpath = (executor.workspace_root / fpath).resolve()
    if not fpath.is_file():
        return _err(3, "FILE_NOT_FOUND", f"File not found: {fpath_raw}")
    try:
        content = fpath.read_bytes()
    except OSError as exc:
        return _err(2, "SYNTAX_INVALID_ARGUMENT", f"Failed to read file: {exc}")
    if not content:
        return _err(4, "CORRUPT_SOURCE_BLOB", f"Source file '{fpath.name}' is empty or corrupt.")
    sha256_hex = hashlib.sha256(content).hexdigest()
    requested_do_not_send = bool(getattr(args, "do_not_send_to_agent", False) or getattr(args, "confidential", False))
    raw_description = getattr(args, "description", None)
    with reentrant_session_lock(sdir / ".session.lock", session_id=sid):
        session_mgr.get_cas(sid).put(content)
        manifest_file = sdir / "inputs" / "manifest.json"
        manifest: dict[str, Any] = {"session_id": sid, "files": [], "sources": []}
        if manifest_file.exists():
            try:
                manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as err_man:
                return _err(1, "CORRUPT_SESSION_STATE", f"Failed to parse inputs manifest.json: {err_man}")
        existing = manifest.get("files", []) or manifest.get("sources", [])
        prev_entry = next((item for item in existing if isinstance(item, dict) and (item.get("sha256") == sha256_hex or item.get("source_sha256") == sha256_hex)), None)
        if role_tier == "automated" and isinstance(prev_entry, dict) and not prev_entry.get("deleted") and str(prev_entry.get("status", "")).upper() != "DELETED" and raw_description is not None and raw_description != prev_entry.get("description"):
            return _err(7, "AGENT_METADATA_OVERWRITE_FORBIDDEN", "Automated actors cannot overwrite existing human-authored source metadata.")
        prev_taint = bool(prev_entry and (prev_entry.get("do_not_send_to_agent") is True or str(prev_entry.get("taint_classification", "")).upper() in ("CONFIDENTIAL", "TAINTED", "DO_NOT_SEND")))
        effective_do_not_send = prev_taint or requested_do_not_send
        effective_description = raw_description if raw_description is not None else (prev_entry.get("description") if isinstance(prev_entry, dict) else None)
        prev_sid = str(prev_entry.get("source_id") or "") if isinstance(prev_entry, dict) else ""
        source_id = prev_sid if prev_sid.startswith("src-") else f"src-{sha256_hex[:16]}"
        now_iso = datetime.now(UTC).isoformat()
        files = [f for f in existing if isinstance(f, dict) and f.get("sha256") != sha256_hex and f.get("source_sha256") != sha256_hex]
        source_entry: dict[str, Any] = {"source_id": source_id, "source_sha256": sha256_hex, "file_name": fpath.name, "filename": fpath.name, "sha256": sha256_hex, "size_bytes": len(content), "byte_count": len(content), "mime_type": "application/octet-stream", "do_not_send_to_agent": effective_do_not_send, "taint_classification": "CONFIDENTIAL" if effective_do_not_send else "CLEAN", "uploaded_at": now_iso}
        if effective_description is not None:
            source_entry["description"] = str(effective_description)
        files.append(source_entry)
        manifest["files"], manifest["sources"], manifest["total_bytes"] = files, files, sum(f.get("size_bytes", 0) for f in files)
        _atomic_write_json(manifest_file, manifest)
        tainted_ids = sorted({str(f.get("source_id") or f.get("sha256")) for f in files if f.get("do_not_send_to_agent") is True or str(f.get("taint_classification", "")).upper() in ("CONFIDENTIAL", "TAINTED", "DO_NOT_SEND")})
        tainted_shas = sorted({str(f.get("sha256") or f.get("source_sha256")) for f in files if f.get("do_not_send_to_agent") is True or str(f.get("taint_classification", "")).upper() in ("CONFIDENTIAL", "TAINTED", "DO_NOT_SEND")})
        _atomic_write_json(sdir / "taint_index.json", {"session_id": sid, "is_tainted": bool(tainted_ids), "tainted_sources": tainted_ids, "tainted_source_ids": tainted_ids, "tainted_sha256s": tainted_shas, "updated_at": now_iso})
        is_session_tainted = bool(compute_effective_session_taint(sdir)) or bool(tainted_ids)
        session_json_path = sdir / "session.json"
        if session_json_path.is_file():
            try:
                sess_dict = json.loads(session_json_path.read_text(encoding="utf-8"))
                if sess_dict.get("taint_classification") == "TAINTED" or is_session_tainted:
                    sess_dict["taint_classification"] = "TAINTED"
                sess_dict["updated_at"] = now_iso
                _atomic_write_json(session_json_path, sess_dict)
            except (json.JSONDecodeError, OSError):
                pass
    return 0, source_entry


def handle_text_add(args: argparse.Namespace, executor: LocalExecutor | None = None) -> tuple[int, Any]:
    """Ingest free-form natural language text notes into session CAS storage."""
    auth_res = _resolve_studio_principal(args)
    if isinstance(auth_res[0], int):
        return auth_res  # type: ignore[return-value]
    principal, role_tier = auth_res  # type: ignore[misc]
    if role_tier == "viewer":
        return _err(7, "FORBIDDEN_ADMIN_REQUIRED", "Read-only viewer cannot add text sources.")
    ctx = _require_session_context(args, executor)
    if isinstance(ctx[0], int):
        return ctx  # type: ignore[return-value]
    session_mgr, _meta, _sdir, sid = ctx  # type: ignore[misc]
    text, file_path = getattr(args, "text", None), getattr(args, "file", None)
    source_name = getattr(args, "source_name", "domain_notes.txt") or "domain_notes.txt"
    if text is None and file_path is None:
        return _err(2, "SYNTAX_INVALID_ARGUMENT", "Either --text or --file must be provided.")
    if file_path:
        p = Path(file_path)
        if not p.is_file():
            return _err(2, "SYNTAX_INVALID_ARGUMENT", f"File '{file_path}' does not exist.")
        text = p.read_text(encoding="utf-8")
    req = TextSourceUploadRequest(text=text or "", source_name=source_name, do_not_send_to_agent=False)
    actor_name = principal if isinstance(principal, str) else getattr(principal, "principal", "human:steward")
    try:
        return 0, session_mgr.add_text_source(sid, text=req.text, source_name=req.source_name, principal=actor_name)
    except OpenDVError as exc:
        return _err_from_opendv_error(exc)


def handle_parse(args: argparse.Namespace, executor: LocalExecutor | None = None) -> tuple[int, Any]:
    """7. `opendv ontology studio parse -s <session>` via `StudioJobRunners`."""
    auth_res = _resolve_studio_principal(args)
    if isinstance(auth_res[0], int):
        return auth_res  # type: ignore[return-value]
    _, role_tier = auth_res  # type: ignore[misc]
    if role_tier == "viewer":
        return _err(7, "FORBIDDEN_ADMIN_REQUIRED", "Read-only viewer role is not permitted to trigger parse jobs.")
    ctx = _require_session_context(args, executor)
    if isinstance(ctx[0], int):
        return ctx  # type: ignore[return-value]
    session_mgr, _, _, sid = ctx  # type: ignore[misc]
    collected_ids: list[str] = []
    single_id = getattr(args, "source_id", None)
    if single_id:
        collected_ids.extend([s.strip() for s in str(single_id).split(",") if s.strip()])
    multi_ids = getattr(args, "source_ids", None)
    if isinstance(multi_ids, list):
        for item in multi_ids:
            collected_ids.extend([s.strip() for s in str(item).split(",") if s.strip()])
    elif isinstance(multi_ids, str) and multi_ids.strip():
        collected_ids.extend([s.strip() for s in multi_ids.split(",") if s.strip()])
    try:
        res = run_parse_session_sources(session_mgr, sid, source_ids=collected_ids or None, ocr_mode=str(getattr(args, "ocr_mode", None) or "auto"), actor_role=role_tier)
    except OCRUnavailableError as exc:
        return _err(1, "OCR_UNAVAILABLE", str(exc))
    except OfficeConversionUnavailableError as exc:
        return _err(1, "OFFICE_CONVERSION_UNAVAILABLE", str(exc))
    except UnsupportedFormatError as exc:
        return _err(2, "UNSUPPORTED_FORMAT", str(exc))
    except OpenDVError as exc:
        return _err_from_opendv_error(exc)
    res["stage"] = "PARSED"
    return 0, res


def handle_attach_source(args: argparse.Namespace, executor: LocalExecutor | None = None) -> tuple[int, Any]:
    """`opendv ontology studio attach-source` connector profiling handler."""
    auth_res = _resolve_studio_principal(args)
    if isinstance(auth_res[0], int):
        return auth_res  # type: ignore[return-value]
    principal, role_tier = auth_res  # type: ignore[misc]
    if role_tier == "viewer":
        return _err(7, "FORBIDDEN_ADMIN_REQUIRED", "Read-only viewer role cannot attach connector sources.")
    ctx = _require_session_context(args, executor)
    if isinstance(ctx[0], int):
        return ctx  # type: ignore[return-value]
    session_mgr, _, _, sid = ctx  # type: ignore[misc]
    raw_uri = getattr(args, "uri", None)
    raw_adapter = getattr(args, "adapter", None)
    if not raw_uri or not raw_adapter:
        return _err(40, "SYNTAX_INVALID_ARGUMENT", "Missing required --adapter or --uri flag.")
    payload: dict[str, Any] = {
        "adapter": str(raw_adapter),
        "uri": str(raw_uri),
        "table": getattr(args, "table", None),
        "namespace": getattr(args, "namespace", None),
        "credentials_ref": getattr(args, "credentials_ref", None),
        "description": getattr(args, "description", None),
        "do_not_send_to_agent": bool(getattr(args, "do_not_send_to_agent", False)),
    }
    try:
        return 0, attach_connector_workflow(session_mgr=session_mgr, session_id=sid, payload=payload, principal_id=principal)
    except StudioWorkflowError as exc:
        return _err_from_workflow_error(exc, wizard_mode=True)
    except OpenDVError as exc:
        return _err_from_opendv_error(exc, wizard_mode=True)
