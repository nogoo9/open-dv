"""Evidence loading, ingestion builder, and atomic write helpers for Studio job runners."""

from __future__ import annotations

import contextlib
import json
import os
import re
import secrets
from pathlib import Path
from typing import Any, Final

from pydantic import ValidationError

from opendv_contracts.dtos.ingestion import EvidenceChunk, TableStructure, TaintClassification
from opendv_contracts.errors import ErrorCode, OpenDVError
from opendv_service.studio.job_runners_privacy import (  # noqa: F401
    _iter_manifest_entries_deduped,
    _load_source_privacy_and_lifecycle_state,
    _max_taint,
    _normalize_taint_value,
    _prepare_evidence_for_agent,
    _read_session_json_dict_fail_closed,
)
from opendv_service.studio.workspace_security import (
    replace_with_retry,
    set_owner_only_permissions,
)
from opendv_sources.adapters import (
    DelimitedTabularAdapter,
    LibreOfficeDocumentAdapter,
    LiteParseDocumentAdapter,
    MarkdownDocumentAdapter,
    MarkupDocumentAdapter,
    PyArrowTabularAdapter,
    StructuredDataDocumentAdapter,
)
from opendv_sources.ingestion import PlainTextDocumentAdapter
from opendv_sources.ingestion.service import IngestionService

_VALID_OCR_MODES: Final[frozenset[str]] = frozenset({"auto", "force", "off"})
_VALID_CONSTRUCT_MODES: Final[frozenset[str]] = frozenset({"auto", "deterministic", "agent"})
_STRICT_CORE_SEMVER_RE: Final[re.Pattern[str]] = re.compile(r"\A\d+\.\d+\.\d+\Z")


def build_studio_ingestion_service(ocr_mode: str = "auto") -> IngestionService:
    """Build a full-capability Studio `IngestionService` covering PDF, Office, and tabular files."""
    clean_ocr = (ocr_mode or "auto").strip().lower()
    if clean_ocr not in _VALID_OCR_MODES:
        raise OpenDVError(
            f"Invalid ocr_mode '{ocr_mode}'; expected one of {sorted(_VALID_OCR_MODES)}.",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        )
    return IngestionService(
        adapters=[
            LiteParseDocumentAdapter(ocr_mode=clean_ocr),
            LibreOfficeDocumentAdapter(ocr_mode=clean_ocr),
            MarkdownDocumentAdapter(),
            DelimitedTabularAdapter(),
            PyArrowTabularAdapter(),
            StructuredDataDocumentAdapter(),
            MarkupDocumentAdapter(),
            PlainTextDocumentAdapter(),
        ],
        default_ocr_mode=clean_ocr,
    )


class AgentProposalAcceptForbiddenError(OpenDVError, PermissionError):
    """Raised when an agent or automated role attempts governed auto-commit (403 / exit 7)."""

    error_code = ErrorCode.AGENT_PROPOSAL_ACCEPT_FORBIDDEN

    def __init__(
        self,
        message: str = "Agent and automated principals are forbidden from auto-commit.",
    ) -> None:
        PermissionError.__init__(self, message)
        OpenDVError.__init__(self, message, error_code=ErrorCode.AGENT_PROPOSAL_ACCEPT_FORBIDDEN)
        self.status_code = 403
        self.http_status = 403
        self.exit_code = 7


def load_active_session_evidence(
    session_dir: Path | str,
) -> tuple[list[EvidenceChunk], list[TableStructure]]:
    """Load active (non-tombstoned) evidence chunks and tables with sticky privacy metadata."""
    sdir = Path(session_dir).resolve()
    state = _load_source_privacy_and_lifecycle_state(sdir)
    active_ids: set[str] = state["active_ids"]
    active_hashes: set[str] = state["active_hashes"]
    active_sha_by_id: dict[str, str] = state["active_sha_by_id"]
    tombstoned_ids: set[str] = state["tombstoned_ids"]
    tombstoned_hashes: set[str] = state["tombstoned_hashes"]
    do_not_send_ids: set[str] = state["do_not_send_ids"]
    do_not_send_shas: set[str] = state["do_not_send_shas"]
    taint_by_id: dict[str, TaintClassification] = state["taint_by_id"]
    taint_by_sha: dict[str, TaintClassification] = state["taint_by_sha"]
    known_manifest_ids = active_ids | tombstoned_ids

    chunks: list[EvidenceChunk] = []
    chunks_file = sdir / "evidence" / "chunks.jsonl"
    if chunks_file.is_file():
        for raw_line in chunks_file.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            try:
                raw_obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(raw_obj, dict):
                continue
            meta = (
                dict(raw_obj.get("metadata")) if isinstance(raw_obj.get("metadata"), dict) else {}
            )
            if raw_obj.get("tombstoned") or meta.get("tombstoned"):
                continue

            c_sid = str(raw_obj.get("source_id") or meta.get("source_id") or "")
            if not c_sid:
                doc_id = str(raw_obj.get("document_id") or "")
                if doc_id and doc_id in known_manifest_ids:
                    c_sid = doc_id

            c_sha = str(
                raw_obj.get("source_sha256")
                or raw_obj.get("blob_sha256")
                or raw_obj.get("sha256")
                or meta.get("source_sha256")
                or ""
            ).lower()

            if c_sid:
                if c_sid in tombstoned_ids:
                    continue
                if c_sha and c_sha in tombstoned_hashes and c_sha != active_sha_by_id.get(c_sid):
                    continue
            elif c_sha and c_sha in tombstoned_hashes and c_sha not in active_hashes:
                continue

            is_dns = bool(
                meta.get("do_not_send_to_agent") is True
                or raw_obj.get("do_not_send_to_agent") is True
                or (c_sid and c_sid in do_not_send_ids)
                or (c_sha and c_sha in do_not_send_shas)
            )
            raw_c_taint = (
                raw_obj.get("taint") or raw_obj.get("taint_classification") or meta.get("taint")
            )
            eff_c_taint = (
                _max_taint(
                    raw_c_taint,
                    taint_by_id.get(c_sid) if c_sid else None,
                    taint_by_sha.get(c_sha) if c_sha else None,
                    TaintClassification.CONFIDENTIAL if is_dns else None,
                )
                or TaintClassification.INTERNAL
            )
            if is_dns:
                meta["do_not_send_to_agent"] = True

            cleaned = {
                k: v
                for k, v in raw_obj.items()
                if k
                not in (
                    "tombstoned",
                    "source_id",
                    "blob_sha256",
                    "taint_classification",
                    "do_not_send_to_agent",
                )
            }
            cleaned["taint"] = eff_c_taint.value
            cleaned["metadata"] = meta
            if c_sha and "source_sha256" not in cleaned:
                cleaned["source_sha256"] = c_sha
            with contextlib.suppress(ValidationError, ValueError, TypeError):
                chunks.append(EvidenceChunk.model_validate(cleaned))

    tables: list[TableStructure] = []
    tables_file = sdir / "evidence" / "tables.jsonl"
    if tables_file.is_file():
        for raw_line in tables_file.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line:
                continue
            try:
                raw_obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not isinstance(raw_obj, dict):
                continue
            meta = (
                dict(raw_obj.get("metadata")) if isinstance(raw_obj.get("metadata"), dict) else {}
            )
            if raw_obj.get("tombstoned") or meta.get("tombstoned"):
                continue

            t_sid = str(raw_obj.get("source_id") or meta.get("source_id") or "")
            t_sha = str(
                raw_obj.get("source_sha256")
                or raw_obj.get("sha256")
                or meta.get("source_sha256")
                or ""
            ).lower()

            if t_sid:
                if t_sid in tombstoned_ids:
                    continue
                if t_sha and t_sha in tombstoned_hashes and t_sha != active_sha_by_id.get(t_sid):
                    continue
            elif t_sha and t_sha in tombstoned_hashes and t_sha not in active_hashes:
                continue

            is_dns = bool(
                meta.get("do_not_send_to_agent") is True
                or raw_obj.get("do_not_send_to_agent") is True
                or (t_sid and t_sid in do_not_send_ids)
                or (t_sha and t_sha in do_not_send_shas)
            )
            raw_t_taint = (
                raw_obj.get("taint") or raw_obj.get("taint_classification") or meta.get("taint")
            )
            eff_t_taint = _max_taint(
                raw_t_taint,
                taint_by_id.get(t_sid) if t_sid else None,
                taint_by_sha.get(t_sha) if t_sha else None,
                TaintClassification.CONFIDENTIAL if is_dns else None,
            )
            if is_dns:
                meta["do_not_send_to_agent"] = True

            cleaned = {
                k: v
                for k, v in raw_obj.items()
                if k
                not in (
                    "tombstoned",
                    "source_id",
                    "taint_classification",
                    "do_not_send_to_agent",
                )
            }
            if eff_t_taint is not None:
                cleaned["taint"] = eff_t_taint.value
            cleaned["metadata"] = meta
            with contextlib.suppress(ValidationError, ValueError, TypeError):
                tables.append(TableStructure.model_validate(cleaned))

    return chunks, tables


def load_binding_clarification_answers(session_dir: Path | str) -> dict[str, Any]:
    """Extract binding steward clarification answers as a `dict` for construction pipelines."""
    sdir = Path(session_dir).resolve()
    answers_file = sdir / "clarifications" / "answers.json"
    if not answers_file.is_file():
        return {}

    raw_answers = json.loads(answers_file.read_text(encoding="utf-8"))
    if isinstance(raw_answers, dict) and "answers" not in raw_answers:
        return dict(raw_answers)

    questions_file = sdir / "clarifications" / "questions.json"
    if not questions_file.is_file():
        return {}

    from opendv_service.construction.clarification_harness import ClarificationHarness

    harness = ClarificationHarness(session_dir=sdir)
    constraints = harness.get_binding_constraints()
    return constraints.model_dump(mode="json")


def _atomic_write_bytes(target_path: Path, payload: bytes) -> None:
    """Atomically write `payload` to `target_path` with fsync, owner-only permissions, and retry."""
    target_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = target_path.parent / f".{target_path.name}.{secrets.token_hex(4)}.tmp"
    fd = os.open(
        str(tmp_path),
        os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0),
        0o600,
    )
    try:
        os.write(fd, payload)
        os.fsync(fd)
    finally:
        os.close(fd)
    set_owner_only_permissions(tmp_path)
    replace_with_retry(tmp_path, target_path)
    set_owner_only_permissions(target_path)
