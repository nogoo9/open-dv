"""Headless Studio job runners for parse, clarify, and construct workflows.

Implements Task 5B.2 / Issue #216 and resolves P11::MAJ-01..03, 565f7024::CRIT-01,
565f7024::CRIT-04, and 565f7024::CRIT-07 inside pure `opendv-service` (with zero
FastAPI, Starlette, httpx, or app-layer imports):
- `build_studio_ingestion_service`: registers all concrete document & tabular adapters
  (`LiteParseDocumentAdapter`, `LibreOfficeDocumentAdapter`, `MarkdownDocumentAdapter`,
  `DelimitedTabularAdapter`, `PyArrowTabularAdapter`, `StructuredDataDocumentAdapter`,
  `MarkupDocumentAdapter`, `PlainTextDocumentAdapter`) with configurable `ocr_mode`.
- `run_parse_session_sources`: validates requested `source_ids` against non-tombstoned
  manifest entries (`SOURCE_NOT_FOUND` 404/3), propagates `OCRUnavailableError` (501/1)
  and `OfficeConversionUnavailableError` (501/1), deduplicates & atomically persists
  `evidence/chunks.jsonl` and `evidence/tables.jsonl` under L5, and flushes SSE under L3
  strictly AFTER releasing L5.
- `run_clarify_session`: loads non-tombstoned evidence chunks & tables, generates
  polysemy clarification questions via `ClarificationHarness`, and flushes SSE outside L5.
- `run_construct_session`: enforces exact base ontology SHA-256 OCC (`P11::MAJ-01`),
  extracts binding steward clarification constraints via `ClarificationHarness`
  (`565f7024::CRIT-07`), handles 0-op `NOOP_CONSTRUCT` deltas with signed audit records,
  and routes `auto_commit=True` through `AtomicCommitService.commit_delta(..., waivers=[])`
  (`P11::MAJ-02` / `565f7024::CRIT-04`).
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import secrets
import stat
import time
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Final

import yaml
from pydantic import ValidationError

from opendv_agent_harness.privacy import Pseudonymizer
from opendv_contracts.dtos.ingestion import (
    EvidenceChunk,
    TableStructure,
    TaintClassification,
)
from opendv_contracts.dtos.ontology import (
    PATH_A_GROUNDING_THRESHOLD,
    OntologyDelta,
    OntologyProposal,
)
from opendv_contracts.errors import ErrorCode, OpenDVError
from opendv_core.models.graph_a import GraphAOntology
from opendv_core.ontology_hashing import compute_ontology_sha256
from opendv_service.ontology.applier import compute_next_semver
from opendv_service.studio.atomic_commit import (
    AtomicCommitService,
    ConcurrentModificationError,
    ForbiddenAdminRequiredError,
    SourceNotFoundError,
    parse_strong_etag,
)
from opendv_service.studio.locks import reentrant_session_lock
from opendv_service.studio.session_manager import (
    StudioSessionManager,
    append_signed_audit_record,
)
from opendv_service.studio.sse_journal import SSEJournalWriter
from opendv_service.studio.workspace_security import (
    WorkspaceSecurityManager,
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
from opendv_sources.exceptions import (
    OCRUnavailableError,
    OfficeConversionUnavailableError,
    UnsupportedFormatError,
)
from opendv_sources.ingestion import PlainTextDocumentAdapter
from opendv_sources.ingestion.service import IngestionService

if TYPE_CHECKING:
    from opendv_service.construction.deterministic_pipeline import (
        DeterministicConstructionPipeline,
    )

logger = logging.getLogger(__name__)

_VALID_OCR_MODES: Final[frozenset[str]] = frozenset({"auto", "force", "off"})
_VALID_CONSTRUCT_MODES: Final[frozenset[str]] = frozenset({"auto", "deterministic", "agent"})
_STRICT_CORE_SEMVER_RE: Final[re.Pattern[str]] = re.compile(r"\A\d+\.\d+\.\d+\Z")
_TAINT_RANK: Final[dict[TaintClassification, int]] = {
    TaintClassification.PUBLIC: 0,
    TaintClassification.INTERNAL: 1,
    TaintClassification.CONFIDENTIAL: 2,
    TaintClassification.RESTRICTED: 3,
}


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


def _normalize_taint_value(
    raw_taint: TaintClassification | str | None,
    *,
    default: TaintClassification | None = None,
) -> TaintClassification | None:
    """Normalize Studio manifest taint labels ('CLEAN' or 'TAINTED') to TaintClassification."""
    if isinstance(raw_taint, TaintClassification):
        return raw_taint
    if isinstance(raw_taint, str):
        cleaned = raw_taint.strip()
        if not cleaned:
            return default
        upper = cleaned.upper()
        if upper == "CLEAN":
            return TaintClassification.PUBLIC
        if upper in ("TAINTED", "DO_NOT_SEND"):
            return TaintClassification.CONFIDENTIAL
        with contextlib.suppress(ValueError):
            return TaintClassification(cleaned.lower())
    return default


def _max_taint(*labels: TaintClassification | str | None) -> TaintClassification | None:
    """Return the monotonic maximum TaintClassification across all provided labels."""
    best: TaintClassification | None = None
    for lbl in labels:
        norm = _normalize_taint_value(lbl)
        if norm is None:
            continue
        if best is None or _TAINT_RANK[norm] > _TAINT_RANK[best]:
            best = norm
    return best


def _iter_manifest_entries_deduped(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    """Return deduplicated entries across `manifest['files']` and `manifest['sources']`."""
    entries: list[dict[str, Any]] = []
    seen_keys: set[tuple[str, str, bool]] = set()
    for bucket_name in ("files", "sources"):
        bucket = manifest.get(bucket_name)
        items: list[Any] = []
        if isinstance(bucket, dict):
            for k, v in bucket.items():
                if isinstance(v, dict):
                    items.append({**v, "source_id": v.get("source_id") or v.get("id") or k})
        elif isinstance(bucket, list):
            items = bucket
        for item in items:
            if isinstance(item, dict):
                sid = str(item.get("source_id") or item.get("id") or "")
                sha = str(
                    item.get("sha256")
                    or item.get("blob_sha256")
                    or item.get("content_sha256")
                    or item.get("source_sha256")
                    or ""
                ).lower()
                is_tomb = bool(item.get("tombstoned") or item.get("deleted"))
                key = (sid, sha, is_tomb)
                if key not in seen_keys:
                    seen_keys.add(key)
                    entries.append(item)
    return entries


def _read_session_json_dict_fail_closed(file_path: Path, label: str) -> dict[str, Any] | None:
    """Read a session JSON dictionary with Win32 atomic-replace retry; fail closed on corruption."""
    try:
        st = os.lstat(file_path)
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise OpenDVError(
            f"Unable to stat {label} '{file_path.name}'; refusing to fail open on privacy state: "
            f"{exc}",
            error_code=ErrorCode.CORRUPT_SESSION_RECORD,
        ) from exc

    is_reparse = bool(getattr(st, "st_file_attributes", 0) & 0x0400)
    if stat.S_ISLNK(st.st_mode) or is_reparse or file_path.is_symlink():
        raise OpenDVError(
            f"Session {label} '{file_path.name}' must not be a symlink or reparse point.",
            error_code=ErrorCode.INVALID_WORKSPACE_PATH,
        )
    if not stat.S_ISREG(st.st_mode):
        return None

    last_err: Exception | None = None
    for attempt in range(6):
        try:
            raw_text = file_path.read_text(encoding="utf-8")
            parsed = json.loads(raw_text)
            if not isinstance(parsed, dict):
                raise OpenDVError(
                    f"Session {label} '{file_path.name}' must contain a JSON object; "
                    "refusing to fail open on privacy/tombstone state.",
                    error_code=ErrorCode.CORRUPT_SESSION_RECORD,
                )
            return parsed
        except FileNotFoundError:
            return None
        except PermissionError as exc:
            last_err = exc
            if attempt < 5:
                time.sleep(0.01 * (2**attempt))
                continue
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            last_err = exc
            if isinstance(exc, OSError) and attempt < 5:
                time.sleep(0.01 * (2**attempt))
                continue
            break

    raise OpenDVError(
        f"Failed to read or parse session {label} '{file_path.name}'; "
        f"refusing to fail open on privacy/tombstone state: {last_err}",
        error_code=ErrorCode.CORRUPT_SESSION_RECORD,
    ) from last_err


def _load_source_privacy_and_lifecycle_state(session_dir: Path) -> dict[str, Any]:
    """Read `inputs/manifest.json` and `taint_index.json` for sticky privacy and tombstones."""
    active_ids: set[str] = set()
    active_hashes: set[str] = set()
    active_sha_by_id: dict[str, str] = {}
    tombstoned_ids: set[str] = set()
    tombstoned_hashes: set[str] = set()
    do_not_send_ids: set[str] = set()
    do_not_send_shas: set[str] = set()
    taint_by_id: dict[str, TaintClassification] = {}
    taint_by_sha: dict[str, TaintClassification] = {}

    def _merge_taint(
        store: dict[str, TaintClassification],
        key: str,
        val: TaintClassification | None,
    ) -> None:
        if not key or val is None:
            return
        merged = _max_taint(store.get(key), val)
        if merged is not None:
            store[key] = merged

    manifest_file = session_dir / "inputs" / "manifest.json"
    m_data = _read_session_json_dict_fail_closed(manifest_file, "inputs manifest")
    if m_data is not None:
        for entry in _iter_manifest_entries_deduped(m_data):
            sid = str(entry.get("source_id") or entry.get("id") or "")
            entry_shas = [
                str(entry.get(k)).lower()
                for k in ("sha256", "blob_sha256", "content_sha256", "source_sha256")
                if isinstance(entry.get(k), str) and len(str(entry.get(k))) == 64
            ]
            if entry.get("tombstoned") or entry.get("deleted"):
                if sid:
                    tombstoned_ids.add(sid)
                tombstoned_hashes.update(entry_shas)
            else:
                if sid:
                    active_ids.add(sid)
                    if entry_shas:
                        active_sha_by_id[sid] = entry_shas[0]
                active_hashes.update(entry_shas)

            raw_t = entry.get("taint") or entry.get("taint_classification")
            norm_t = _normalize_taint_value(raw_t)
            dns = bool(entry.get("do_not_send_to_agent") is True)
            if str(raw_t or "").strip().upper() == "DO_NOT_SEND":
                dns = True
            if dns:
                norm_t = _max_taint(norm_t, TaintClassification.CONFIDENTIAL)
                if sid:
                    do_not_send_ids.add(sid)
                do_not_send_shas.update(entry_shas)
            if sid:
                _merge_taint(taint_by_id, sid, norm_t)
            for h in entry_shas:
                _merge_taint(taint_by_sha, h, norm_t)

    # An active re-registration of the same source_id supersedes an older tombstone record
    tombstoned_ids -= active_ids

    taint_idx_file = session_dir / "taint_index.json"
    t_data = _read_session_json_dict_fail_closed(taint_idx_file, "taint index")
    if t_data is not None:
        for list_key in (
            "tainted_source_ids",
            "tainted_sources",
            "confidential_sources",
            "do_not_send_to_agent",
            "do_not_send_source_ids",
            "do_not_send_sources",
        ):
            coll = t_data.get(list_key)
            if isinstance(coll, list):
                for item in coll:
                    if isinstance(item, str) and item.strip():
                        clean_s = item.strip()
                        if len(clean_s) == 64 and all(
                            ch in "0123456789abcdefABCDEF" for ch in clean_s
                        ):
                            do_not_send_shas.add(clean_s.lower())
                            _merge_taint(
                                taint_by_sha,
                                clean_s.lower(),
                                TaintClassification.CONFIDENTIAL,
                            )
                        else:
                            do_not_send_ids.add(clean_s)
                            _merge_taint(
                                taint_by_id,
                                clean_s,
                                TaintClassification.CONFIDENTIAL,
                            )
                    elif isinstance(item, dict):
                        i_sid = str(item.get("source_id") or item.get("id") or "").strip()
                        i_sha = (
                            str(item.get("sha256") or item.get("source_sha256") or "")
                            .strip()
                            .lower()
                        )
                        i_dns = item.get("do_not_send_to_agent", True) is not False
                        i_taint = _normalize_taint_value(
                            item.get("taint") or item.get("taint_classification"),
                            default=TaintClassification.CONFIDENTIAL,
                        )
                        if i_dns:
                            i_taint = _max_taint(i_taint, TaintClassification.CONFIDENTIAL)
                            if i_sid:
                                do_not_send_ids.add(i_sid)
                            if len(i_sha) == 64:
                                do_not_send_shas.add(i_sha)
                        if i_sid:
                            _merge_taint(taint_by_id, i_sid, i_taint)
                        if len(i_sha) == 64:
                            _merge_taint(taint_by_sha, i_sha, i_taint)

        sha_list = t_data.get("tainted_sha256s")
        if isinstance(sha_list, list):
            for s_val in sha_list:
                if isinstance(s_val, str) and len(s_val.strip()) == 64:
                    clean_h = s_val.strip().lower()
                    do_not_send_shas.add(clean_h)
                    _merge_taint(taint_by_sha, clean_h, TaintClassification.CONFIDENTIAL)

        sources_map = t_data.get("sources")
        if isinstance(sources_map, dict):
            for k, v in sources_map.items():
                if isinstance(v, dict):
                    v_dns = bool(v.get("do_not_send_to_agent") is True)
                    v_taint = _normalize_taint_value(
                        v.get("taint") or v.get("taint_classification")
                    )
                    if v_dns:
                        v_taint = _max_taint(v_taint, TaintClassification.CONFIDENTIAL)
                        do_not_send_ids.add(str(k))
                    _merge_taint(taint_by_id, str(k), v_taint)

    return {
        "active_ids": active_ids,
        "active_hashes": active_hashes,
        "active_sha_by_id": active_sha_by_id,
        "tombstoned_ids": tombstoned_ids,
        "tombstoned_hashes": tombstoned_hashes,
        "do_not_send_ids": do_not_send_ids,
        "do_not_send_shas": do_not_send_shas,
        "taint_by_id": taint_by_id,
        "taint_by_sha": taint_by_sha,
    }


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
    """Extract binding steward clarification answers as a `dict` for construction pipelines.

    Supports both the normative `list[ClarificationAnswerItem]` format persisted by
    `ClarificationHarness.save_answers()` (extracting `preferred_mappings`,
    `custom_guidance`, and `selected_option_ids` via `get_binding_constraints()`) and
    direct dictionary test fixtures (`{"preferred_mappings": {...}}`).
    """
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

        try:
            raw_bytes = cas.get(source_sha)
            res = svc.ingest_bytes(
                data=raw_bytes,
                filename=filename,
                mime_type=mime_type,
                taint=source_taint,
                ocr_mode=ocr_mode,
            )
            if source_id:
                parsed_source_ids.add(source_id)
            for c in res.chunks:
                stamped_meta = dict(c.metadata or {})
                if source_id:
                    stamped_meta["source_id"] = source_id
                if source_sha:
                    stamped_meta["source_sha256"] = source_sha
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
            # Missing host capability (HTTP 501 / exit 1) or unsupported media type (HTTP 415)
            # MUST fail closed rather than silently succeeding with 0 chunks (565f7024::CRIT-01).
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

    # Persist deduplicated chunks & tables atomically under L5 session lock
    if parsed_source_ids or parsed_chunks or parsed_tables:
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

    # Flush SSE journal under L3 strictly AFTER releasing L5 session lock!
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


_PII_EMAIL_RE: Final[re.Pattern[str]] = re.compile(
    r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"
)


def _prepare_evidence_for_agent(
    chunks: Sequence[EvidenceChunk],
    tables: Sequence[TableStructure],
    security_manager: WorkspaceSecurityManager,
    sensitive_terms: Sequence[str] | None = None,
) -> tuple[list[EvidenceChunk], list[TableStructure]]:
    """Filter `do_not_send_to_agent` items and HMAC-pseudonymize non-public evidence for agents."""
    pseudonymizer = Pseudonymizer(security_manager.get_pseudo_key())
    caller_terms = [str(t).strip() for t in (sensitive_terms or ()) if str(t).strip()]
    safe_chunks: list[EvidenceChunk] = []
    for chunk in chunks:
        c_meta = dict(chunk.metadata or {})
        if c_meta.get("do_not_send_to_agent") is True:
            continue
        eff_c_taint = _max_taint(chunk.taint, c_meta.get("taint")) or TaintClassification.INTERNAL
        if eff_c_taint != TaintClassification.PUBLIC:
            eff_c_terms: list[str] = list(caller_terms)
            for email_hit in _PII_EMAIL_RE.findall(chunk.text or ""):
                if email_hit not in eff_c_terms:
                    eff_c_terms.append(email_hit)
            p_chunk = pseudonymizer.pseudonymize_chunk(chunk, sensitive_terms=eff_c_terms)
            p_meta = dict(p_chunk.metadata or {})
            p_meta["pseudonymized"] = True
            p_chunk = p_chunk.model_copy(update={"metadata": p_meta})
            safe_chunks.append(p_chunk)
        else:
            safe_chunks.append(chunk)

    safe_tables: list[TableStructure] = []
    for tbl in tables:
        t_meta = dict(tbl.metadata or {})
        if t_meta.get("do_not_send_to_agent") is True:
            continue
        eff_taint = _max_taint(tbl.taint, t_meta.get("taint")) or TaintClassification.INTERNAL
        if eff_taint != TaintClassification.PUBLIC:
            eff_terms: list[str] = list(caller_terms)
            tbl_blob = "\n".join(
                [
                    tbl.table_id or "",
                    *tbl.headers,
                    *(str(c) for r in tbl.rows for c in r),
                    tbl.caption or "",
                    tbl.raw_html_or_md or "",
                ]
            )
            for email_hit in _PII_EMAIL_RE.findall(tbl_blob):
                if email_hit not in eff_terms:
                    eff_terms.append(email_hit)
            safe_tables.append(pseudonymizer.pseudonymize_table(tbl, sensitive_terms=eff_terms))
        else:
            safe_tables.append(tbl)

    return safe_chunks, safe_tables


def run_clarify_session(
    session_manager: StudioSessionManager,
    session_id: str,
    *,
    job_id: str = "clarify",
    mode: str = "auto",
    max_questions: int = 5,
    candidate_terms: Sequence[str | dict[str, Any]] | None = None,
    actor_role: str = "admin",
    writer: SSEJournalWriter | None = None,
    is_cancelled: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Generate polysemy clarification questions from active session evidence."""
    if actor_role == "viewer":
        raise ForbiddenAdminRequiredError(
            "Viewer role is not permitted to generate clarifications."
        )

    session_dir = session_manager.get_session_dir(session_id)
    chunks, tables = load_active_session_evidence(session_dir)
    if (mode or "").strip().lower() == "agent":
        sec_mgr = getattr(session_manager, "security_manager", None) or WorkspaceSecurityManager(
            studio_root=session_dir.parent
        )
        chunks, tables = _prepare_evidence_for_agent(chunks, tables, sec_mgr)

    if (not chunks and not tables and not candidate_terms) or (
        is_cancelled is not None and is_cancelled()
    ):
        return {"questions_count": 0, "question_ids": []}

    from opendv_service.construction.clarification_harness import ClarificationHarness

    harness = ClarificationHarness(session_dir=session_dir)
    questions = harness.generate_questions(
        candidate_terms=candidate_terms,
        evidence_chunks=chunks,
        tables=tables,
        max_questions=max_questions,
    )

    if writer is not None:
        for q in questions:
            writer.buffer_event(
                "clarification_generated",
                {
                    "job_id": job_id,
                    "question_id": q.question_id,
                    "focus_concept": q.focus_concept,
                },
            )
        writer.flush()

    return {
        "questions_count": len(questions),
        "question_ids": [q.question_id for q in questions],
    }


def _normalize_and_validate_version_dir(
    raw_version: str,
    session_dir: Path,
    session_id: str,
) -> tuple[str, Path]:
    """Validate strict `X.Y.Z` SemVer and assert containment within `session_dir/versions`."""
    cleaned = raw_version.strip() if isinstance(raw_version, str) else ""
    if cleaned.startswith("v") and not cleaned.startswith("vv"):
        cleaned = cleaned[1:]
    if not _STRICT_CORE_SEMVER_RE.fullmatch(cleaned):
        err = OpenDVError(
            f"Malformed or unknown version '{raw_version}' in session '{session_id}'.",
            error_code=ErrorCode.VERSION_NOT_FOUND,
        )
        err.status_code = err.http_status
        raise err

    versions_root = (session_dir / "versions").resolve()
    vdir = (session_dir / "versions" / f"v{cleaned}").resolve()
    if not vdir.is_relative_to(versions_root):
        err = OpenDVError(
            f"Version path traversal rejected for 'v{cleaned}' in session '{session_id}'.",
            error_code=ErrorCode.VERSION_NOT_FOUND,
        )
        err.status_code = err.http_status
        raise err
    return cleaned, vdir


def _load_session_version_ontology(
    session_dir: Path,
    session_id: str,
    active_version: str,
    head_version: str,
) -> GraphAOntology:
    """Load `versions/v{active_version}/ontology.{json,yaml}` or raise typed OpenDVError."""
    clean_active, vdir = _normalize_and_validate_version_dir(
        active_version, session_dir, session_id
    )
    ont_json = vdir / "ontology.json"
    ont_yaml = vdir / "ontology.yaml"
    if ont_json.is_file():
        return GraphAOntology.model_validate_json(ont_json.read_text(encoding="utf-8"))
    if ont_yaml.is_file():
        return GraphAOntology.model_validate(yaml.safe_load(ont_yaml.read_text(encoding="utf-8")))

    clean_head = head_version.strip()
    if clean_head.startswith("v") and not clean_head.startswith("vv"):
        clean_head = clean_head[1:]
    if clean_active != clean_head:
        err = OpenDVError(
            f"Requested base version 'v{clean_active}' does not exist in session '{session_id}'.",
            error_code=ErrorCode.VERSION_NOT_FOUND,
        )
        err.status_code = err.http_status
        raise err
    err = OpenDVError(
        f"Base ontology artifact missing for head version 'v{clean_active}' "
        f"in session '{session_id}'.",
        error_code=ErrorCode.CORRUPT_SESSION_RECORD,
    )
    err.status_code = err.http_status
    raise err


def run_construct_session(
    session_manager: StudioSessionManager,
    session_id: str,
    *,
    base_version: str | None = None,
    base_sha256: str | None = None,
    job_id: str = "construct",
    auto_commit: bool = False,
    fibo_modules: Sequence[str] | None = None,
    mode: str = "deterministic",
    if_match: str | None = None,
    actor: str = "human:local_admin",
    actor_role: str = "admin",
    min_confidence: float = PATH_A_GROUNDING_THRESHOLD,
    require_admin_for_auto_commit: bool = False,
    writer: SSEJournalWriter | None = None,
    is_cancelled: Callable[[], bool] | None = None,
    on_stage: Callable[[str], None] | None = None,
    pipeline: DeterministicConstructionPipeline | None = None,
    agent_runner: Callable[..., OntologyProposal] | None = None,
    security_manager: WorkspaceSecurityManager | None = None,
) -> dict[str, Any]:
    """Execute ontology construction producing a persisted proposal or governed version commit."""
    if actor_role == "viewer":
        raise ForbiddenAdminRequiredError("Viewer role is not permitted to construct proposals.")

    clean_mode = (mode or "deterministic").strip().lower()
    if clean_mode not in _VALID_CONSTRUCT_MODES:
        allowed_modes = sorted(_VALID_CONSTRUCT_MODES)
        raise OpenDVError(
            f"Unsupported construction mode '{mode}'; expected one of {allowed_modes}.",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        )

    if auto_commit:
        if actor.startswith("agent:") or actor_role == "automated":
            raise AgentProposalAcceptForbiddenError(
                "Agent and automated principals are forbidden from auto-commit construction."
            )
        if clean_mode == "agent":
            raise OpenDVError(
                "Auto-commit construction is only permitted in deterministic mode; "
                "agent-generated proposals require human review.",
                error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
            )
        if require_admin_for_auto_commit and actor_role != "admin":
            raise ForbiddenAdminRequiredError(
                "Administrator role is required for auto-commit construction."
            )

    session_dir = session_manager.get_session_dir(session_id)
    session_meta = session_manager.get_session(session_id)
    raw_target_version = session_meta.head_version if base_version is None else base_version
    active_version, _ = _normalize_and_validate_version_dir(
        raw_target_version, session_dir, session_id
    )
    sec_mgr = (
        security_manager
        or getattr(session_manager, "security_manager", None)
        or WorkspaceSecurityManager(studio_root=session_dir.parent)
    )

    # 1. Load base ontology and enforce strict OCC against canonical_base_sha (P11::MAJ-01)
    base_ontology = _load_session_version_ontology(
        session_dir=session_dir,
        session_id=session_id,
        active_version=active_version,
        head_version=session_meta.head_version,
    )
    canonical_base_sha = compute_ontology_sha256(base_ontology)

    clean_base_sha = parse_strong_etag(base_sha256) if base_sha256 else None
    if clean_base_sha is not None and clean_base_sha != canonical_base_sha:
        raise ConcurrentModificationError(
            f"base_sha256 '{clean_base_sha}' does not match loaded base ontology "
            f"'v{active_version}' digest '{canonical_base_sha}'."
        )

    clean_if_match = parse_strong_etag(if_match) if if_match else None
    if clean_if_match is not None and clean_if_match != canonical_base_sha:
        raise ConcurrentModificationError(
            f"If-Match precondition '{clean_if_match}' does not match loaded base ontology "
            f"'v{active_version}' digest '{canonical_base_sha}'."
        )

    clean_auto_precond: str | None = None
    if auto_commit:
        clean_auto_precond = clean_if_match or clean_base_sha
        if not clean_auto_precond:
            raise OpenDVError(
                "Auto-commit construction requires an explicit If-Match header or base_sha256 "
                "precondition anchoring the expected head ontology SHA-256.",
                error_code=ErrorCode.MISSING_AUTO_COMMIT_PRECONDITION,
            )
        if canonical_base_sha != session_meta.head_sha256:
            raise ConcurrentModificationError(
                f"Auto-commit requires constructing against active head "
                f"('{session_meta.head_sha256}'), got base digest '{canonical_base_sha}'."
            )
        if clean_auto_precond != session_meta.head_sha256:
            raise ConcurrentModificationError(
                f"Auto-commit precondition '{clean_auto_precond}' does not match active session "
                f"head digest '{session_meta.head_sha256}'."
            )

    # 2. Load active (non-tombstoned) evidence & binding clarification constraints (CRIT-07)
    chunks, tables = load_active_session_evidence(session_dir)
    answers_dict = load_binding_clarification_answers(session_dir)

    # 3. Execute construction pipeline
    used_agent_runner = clean_mode == "agent" or (
        clean_mode == "auto" and not auto_commit and agent_runner is not None
    )
    resolved_engine_mode = "agent" if used_agent_runner else "deterministic"
    if used_agent_runner:
        if agent_runner is None:
            raise OpenDVError(
                "Agent construction mode requested, but no LLM provider or agent runner is "
                "configured; use mode='deterministic' or configure an agent provider.",
                error_code=ErrorCode.AGENT_UNAVAILABLE,
            )
        agent_chunks, agent_tables = _prepare_evidence_for_agent(chunks, tables, sec_mgr)
        proposal = agent_runner(
            base_ontology=base_ontology,
            evidence_chunks=agent_chunks,
            tables=agent_tables,
            clarification_answers=answers_dict,
            session_dir=session_dir,
            fibo_modules=list(fibo_modules or []),
        )
    else:
        from opendv_service.construction.deterministic_pipeline import (
            DeterministicConstructionPipeline,
        )
        from opendv_service.construction.models import ConstructOptions

        det_pipeline = pipeline or DeterministicConstructionPipeline()
        opts = ConstructOptions(
            mode="deterministic",
            auto_commit=False,
            session_id=session_id,
            session_dir=session_dir,
            min_confidence=min_confidence,
            actor=actor,
            principal=actor,
            if_match=None,
        )
        proposal = det_pipeline.construct_proposal(
            base_ontology=base_ontology,
            evidence_chunks=chunks,
            tables=tables,
            clarification_answers=answers_dict,
            options=opts,
        )

    # Realign delta to canonical_base_sha and base_ontology_version via full model_validate
    proposal_ops = list(proposal.delta.operations)
    raw_delta_dict = proposal.delta.model_dump(mode="json")
    for alias_key in ("base_sha256", "base_version", "target_version"):
        raw_delta_dict.pop(alias_key, None)
    raw_delta_dict["parent_sha256"] = canonical_base_sha
    raw_delta_dict["base_ontology_version"] = active_version
    if proposal_ops:
        probe_delta = proposal.delta.model_copy(update={"target_ontology_version": None})
        probe_delta.__pydantic_fields_set__.discard("target_ontology_version")
        expected_semver = compute_next_semver(active_version, probe_delta, base_ontology)
        raw_delta_dict["target_ontology_version"] = expected_semver
    else:
        raw_delta_dict.pop("target_ontology_version", None)

    delta = OntologyDelta.model_validate(raw_delta_dict)
    assert delta.base_sha256 == canonical_base_sha
    proposal = proposal.model_copy(
        update={
            "delta": delta,
            "base_ontology_sha256": canonical_base_sha,
        }
    )

    # Emit FIBO grounding telemetry outside L5
    if writer is not None:
        for op in proposal_ops:
            ext_mappings = getattr(op, "external_mappings", None)
            if ext_mappings:
                for m in ext_mappings:
                    writer.buffer_event(
                        "fibo_grounding",
                        {
                            "job_id": job_id,
                            "concept_uri": getattr(op, "concept_uri", ""),
                            "matched_fibo_uri": m.target_uri,
                            "score": m.confidence,
                        },
                    )
        writer.flush()

    if is_cancelled is not None and is_cancelled():
        return {
            "proposal_id": proposal.proposal_id,
            "status": "cancelled",
            "message": "Job cancelled before commit.",
        }

    # Persist canonical Studio proposal shape to proposals/<proposal_id>.json atomically under L5
    proposals_dir = session_dir / "proposals"
    prop_file = proposals_dir / f"{proposal.proposal_id}.json"
    lock_file = session_dir / ".session.lock"
    delta_json = delta.model_dump(mode="json", exclude_none=True)
    staged_candidates = list(
        getattr(proposal, "staged_candidates", None)
        or (delta.metadata or {}).get("staged_candidates", [])
    )
    created_at_str = proposal.created_at_utc or datetime.now(UTC).isoformat()
    clean_job_id = (job_id or "").strip()
    if clean_job_id:
        proposer_identity = f"{resolved_engine_mode}:{clean_job_id}"
    elif actor.startswith(("system:", "agent:", "deterministic:")):
        proposer_identity = actor
    else:
        proposer_identity = f"system:{resolved_engine_mode}"

    proposal_record: dict[str, Any] = {
        **proposal.model_dump(mode="json", exclude_none=True),
        "proposal_id": proposal.proposal_id,
        "session_id": session_id,
        "status": "PENDING",
        "base_version": active_version,
        "base_sha256": canonical_base_sha,
        "proposer": proposer_identity,
        "proposer_actor": resolved_engine_mode,
        "triggered_by": actor,
        "created_at": created_at_str,
        "operations": list(delta_json.get("operations", [])),
        "staged_candidates": staged_candidates,
        "rationale": proposal.rationale,
        "confidence_score": proposal.confidence_score,
        "diagnostics": list(proposal.diagnostics or []),
        "delta": delta_json,
    }
    with reentrant_session_lock(lock_file, session_id=session_id):
        prop_payload = json.dumps(proposal_record, indent=2).encode("utf-8")
        _atomic_write_bytes(prop_file, prop_payload)

    if not auto_commit:
        return {
            "proposal_id": proposal.proposal_id,
            "operations_count": len(proposal_ops),
        }

    # 4. Governed Auto-Commit Path (P11::MAJ-02 / 565f7024::CRIT-04)
    assert clean_auto_precond is not None
    if is_cancelled is not None and is_cancelled():
        return {
            "proposal_id": proposal.proposal_id,
            "status": "cancelled",
            "message": "Job cancelled before commit.",
        }

    if on_stage is not None:
        on_stage("committing")

    # Handle 0-op deterministic construct cleanly via signed NOOP_CONSTRUCT audit record
    if len(proposal_ops) == 0:
        with reentrant_session_lock(lock_file, session_id=session_id):
            fresh_meta = session_manager.get_session(session_id)
            if fresh_meta.head_sha256 != clean_auto_precond:
                raise ConcurrentModificationError(
                    f"Session head moved concurrently during construct: expected "
                    f"'{clean_auto_precond}', got '{fresh_meta.head_sha256}'."
                )
            audit_rec = append_signed_audit_record(
                session_dir=session_dir,
                action="NOOP_CONSTRUCT",
                actor=actor,
                details={
                    "job_id": job_id,
                    "proposal_id": proposal.proposal_id,
                    "base_version": f"v{active_version}",
                    "base_sha256": canonical_base_sha,
                },
                security_manager=sec_mgr,
            )
            audit_sha = (
                str(audit_rec["audit_sha256"]) if isinstance(audit_rec, dict) else str(audit_rec)
            )
        if writer is not None:
            writer.buffer_event(
                "noop_construct",
                {
                    "job_id": job_id,
                    "proposal_id": proposal.proposal_id,
                    "head_version": fresh_meta.head_version,
                    "head_sha256": fresh_meta.head_sha256,
                },
            )
            writer.flush()
        return {
            "proposal_id": proposal.proposal_id,
            "status": "NOOP_CONSTRUCT",
            "auto_committed": False,
            "operations_count": 0,
            "target_version": fresh_meta.head_version,
            "target_sha256": fresh_meta.head_sha256,
            "audit_head_sha256": audit_sha,
        }

    atomic_svc = AtomicCommitService(
        session_manager=session_manager,
        security_manager=sec_mgr,
    )
    commit_res = atomic_svc.commit_delta(
        session_id=session_id,
        delta=delta,
        proposal_id=proposal.proposal_id,
        author=actor,
        if_match=f'"{clean_auto_precond}"',
        actor_role=actor_role,
        waivers=[],
    )

    # Flush SSE journal under L3 strictly AFTER releasing L5 session lock!
    if writer is not None:
        writer.buffer_event(
            "version_committed",
            {
                "job_id": job_id,
                "target_version": commit_res.version,
                "target_sha256": commit_res.target_sha256,
            },
        )
        writer.flush()

    return {
        "proposal_id": proposal.proposal_id,
        "auto_committed": True,
        "operations_count": len(proposal_ops),
        "target_version": commit_res.version,
        "target_sha256": commit_res.target_sha256,
        "audit_head_sha256": commit_res.audit_head_sha256,
    }


class StudioJobRunners:
    """Object-oriented facade bundling headless Studio parse, clarify, and construct runners."""

    def __init__(
        self,
        session_manager: StudioSessionManager,
        security_manager: WorkspaceSecurityManager | None = None,
        ingestion_service: IngestionService | None = None,
        pipeline: DeterministicConstructionPipeline | None = None,
        agent_runner: Callable[..., OntologyProposal] | None = None,
    ) -> None:
        self.session_manager = session_manager
        self.security_manager = security_manager
        self.ingestion_service = ingestion_service
        self.pipeline = pipeline
        self.agent_runner = agent_runner

    def run_parse(
        self,
        session_id: str,
        *,
        job_id: str = "parse",
        source_ids: Sequence[str] | None = None,
        ocr_mode: str = "auto",
        actor_role: str = "admin",
        writer: SSEJournalWriter | None = None,
        is_cancelled: Callable[[], bool] | None = None,
        on_progress: Callable[[str, int], None] | None = None,
        fail_fast_on_source_error: bool = False,
    ) -> dict[str, Any]:
        """Delegate to `run_parse_session_sources`."""
        svc = (
            self.ingestion_service
            if self.ingestion_service is not None and ocr_mode == "auto"
            else None
        )
        return run_parse_session_sources(
            session_manager=self.session_manager,
            session_id=session_id,
            job_id=job_id,
            source_ids=source_ids,
            ocr_mode=ocr_mode,
            actor_role=actor_role,
            writer=writer,
            is_cancelled=is_cancelled,
            on_progress=on_progress,
            ingestion_service=svc,
            fail_fast_on_source_error=fail_fast_on_source_error,
        )

    def run_clarify(
        self,
        session_id: str,
        *,
        job_id: str = "clarify",
        mode: str = "auto",
        max_questions: int = 5,
        candidate_terms: Sequence[str | dict[str, Any]] | None = None,
        actor_role: str = "admin",
        writer: SSEJournalWriter | None = None,
        is_cancelled: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        """Delegate to `run_clarify_session`."""
        return run_clarify_session(
            session_manager=self.session_manager,
            session_id=session_id,
            job_id=job_id,
            mode=mode,
            max_questions=max_questions,
            candidate_terms=candidate_terms,
            actor_role=actor_role,
            writer=writer,
            is_cancelled=is_cancelled,
        )

    def run_construct(
        self,
        session_id: str,
        *,
        base_version: str | None = None,
        base_sha256: str | None = None,
        job_id: str = "construct",
        auto_commit: bool = False,
        fibo_modules: Sequence[str] | None = None,
        mode: str = "deterministic",
        if_match: str | None = None,
        actor: str = "human:local_admin",
        actor_role: str = "admin",
        min_confidence: float = PATH_A_GROUNDING_THRESHOLD,
        require_admin_for_auto_commit: bool = False,
        writer: SSEJournalWriter | None = None,
        is_cancelled: Callable[[], bool] | None = None,
        on_stage: Callable[[str], None] | None = None,
    ) -> dict[str, Any]:
        """Delegate to `run_construct_session`."""
        return run_construct_session(
            session_manager=self.session_manager,
            session_id=session_id,
            base_version=base_version,
            base_sha256=base_sha256,
            job_id=job_id,
            auto_commit=auto_commit,
            fibo_modules=fibo_modules,
            mode=mode,
            if_match=if_match,
            actor=actor,
            actor_role=actor_role,
            min_confidence=min_confidence,
            require_admin_for_auto_commit=require_admin_for_auto_commit,
            writer=writer,
            is_cancelled=is_cancelled,
            on_stage=on_stage,
            pipeline=self.pipeline,
            agent_runner=self.agent_runner,
            security_manager=self.security_manager,
        )


__all__ = [
    "AgentProposalAcceptForbiddenError",
    "StudioJobRunners",
    "build_studio_ingestion_service",
    "load_active_session_evidence",
    "load_binding_clarification_answers",
    "run_clarify_session",
    "run_construct_session",
    "run_parse_session_sources",
]
