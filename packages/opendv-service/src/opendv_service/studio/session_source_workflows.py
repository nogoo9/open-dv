"""Shared Studio source upload, clarification answers, and starter pack workflows."""

from __future__ import annotations

import contextlib
import hashlib
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from opendv_contracts.dtos.clarification import ClarificationAnswerItem, ClarificationQuestion
from opendv_contracts.dtos.starter_packs import AssetManagementPackId
from opendv_contracts.jcs import dumps_jcs_bytes
from opendv_core.ontology_hashing import compute_ontology_sha256
from opendv_service.construction.clarification_harness import EMPTY_ANSWERS_SHA256
from opendv_service.ontology.applier import parse_semver
from opendv_service.studio.delta_normalizer import StudioWorkflowError
from opendv_service.studio.locks import reentrant_session_lock
from opendv_service.studio.session_manager import (
    SessionMetadata,
    StudioSessionManager,
    _atomic_write_session_json,
    append_signed_audit_record,
    reconcile_or_verify_audit_ledger,
    record_tainted_source_index,
)
from opendv_service.studio.workspace_security import replace_with_retry, set_owner_only_permissions
from opendv_sources.asset_management import AssetManagementStarterPackRegistry


def persist_uploaded_source_sync(
    *,
    session_mgr: StudioSessionManager,
    session_id: str,
    tmp_blob_path: Path,
    size_bytes: int,
    filename: str,
    mime_type: str,
    do_not_send_to_agent: bool,
    description: str | None,
    principal_id: str,
    is_automated: bool,
    ocr_mode: str = "auto",
) -> dict[str, Any]:
    """Persist streamed source blob into CAS, manifest, taint index, and session metadata."""
    sdir = session_mgr.get_session_dir(session_id)
    raw_bytes = tmp_blob_path.read_bytes()
    source_sha256 = hashlib.sha256(raw_bytes).hexdigest().lower()
    source_id = f"src-{source_sha256[:16]}"
    with reentrant_session_lock(sdir / ".session.lock", session_id=sdir.name):
        mfile = sdir / "inputs" / "manifest.json"
        if mfile.is_file():
            try:
                mdata = json.loads(mfile.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise StudioWorkflowError(
                    "CORRUPT_SESSION_RECORD",
                    f"Corrupt manifest file in session '{session_id}': {exc}",
                    status_code=500,
                    exit_code=1,
                ) from exc
        else:
            mdata = {"files": []}

        files = mdata.get("files", [])
        existing = next((f for f in files if f.get("source_id") == source_id), None)
        if existing:
            if (
                is_automated
                and not existing.get("tombstoned", False)
                and description is not None
                and existing.get("description")
                and existing.get("description") != description
            ):
                raise StudioWorkflowError(
                    "AGENT_METADATA_OVERWRITE_FORBIDDEN",
                    "Automated actors cannot overwrite existing human-authored source metadata.",
                    status_code=403,
                    exit_code=7,
                )
            existing.update(
                {
                    "tombstoned": False,
                    "deleted_at": None,
                    "filename": filename,
                    "size_bytes": size_bytes,
                    "mime_type": mime_type,
                    "ocr_mode": ocr_mode,
                    "updated_at": datetime.now(UTC).isoformat(),
                }
            )
            if do_not_send_to_agent or existing.get("do_not_send_to_agent"):
                existing["do_not_send_to_agent"] = True
            if description is not None:
                existing["description"] = description
            effective_do_not_send = bool(existing.get("do_not_send_to_agent", False))
        else:
            existing = {
                "source_id": source_id,
                "filename": filename,
                "sha256": source_sha256,
                "size_bytes": size_bytes,
                "mime_type": mime_type,
                "ocr_mode": ocr_mode,
                "do_not_send_to_agent": bool(do_not_send_to_agent),
                "description": description,
                "uploaded_at": datetime.now(UTC).isoformat(),
                "tombstoned": False,
            }
            files.append(existing)
            effective_do_not_send = bool(do_not_send_to_agent)

        mdata["files"] = files

        # Verify audit ledger fail-closed before mutating CAS, manifest, taint, or session.json.
        meta = session_mgr.get_session(session_id)
        reconcile_or_verify_audit_ledger(sdir, meta, session_mgr.security_manager, mutate=True)

        taint_idx_path = sdir / "taint_index.json"
        session_json_path = sdir / "session.json"
        prev_manifest = mfile.read_bytes() if mfile.is_file() else None
        prev_taint = taint_idx_path.read_bytes() if taint_idx_path.is_file() else None
        prev_session = session_json_path.read_bytes() if session_json_path.is_file() else None

        cas = session_mgr.get_cas(session_id)
        had_blob = cas.has(source_sha256)
        try:
            cas.put(raw_bytes)
            mfile.parent.mkdir(parents=True, exist_ok=True)
            tmp_mfile = sdir / "inputs" / f".manifest.{uuid.uuid4().hex[:8]}.tmp"
            tmp_mfile.write_text(json.dumps(mdata, indent=2), encoding="utf-8")
            replace_with_retry(tmp_mfile, mfile)
            set_owner_only_permissions(mfile)

            if effective_do_not_send:
                record_tainted_source_index(
                    sdir, taint_idx_path, source_id, source_sha256, filename
                )

                if meta.taint_classification != "TAINTED":
                    _atomic_write_session_json(
                        sdir,
                        meta.model_copy(
                            update={
                                "taint_classification": "TAINTED",
                                "updated_at": datetime.now(UTC).isoformat(),
                            }
                        ),
                    )
                append_signed_audit_record(
                    session_dir=sdir,
                    action="CONFIDENTIAL_SOURCE_REGISTERED",
                    actor=principal_id,
                    details={
                        "source_id": source_id,
                        "source_sha256": source_sha256,
                        "do_not_send_to_agent": True,
                    },
                    security_manager=session_mgr.security_manager,
                )
        except Exception:
            snapshots = (
                (mfile, prev_manifest),
                (taint_idx_path, prev_taint),
                (session_json_path, prev_session),
            )
            for path, snap in snapshots:
                if snap is None:
                    with contextlib.suppress(OSError):
                        path.unlink()
                else:
                    tmp_rb = path.parent / f".{path.name}.{uuid.uuid4().hex[:8]}.rb"
                    tmp_rb.write_bytes(snap)
                    replace_with_retry(tmp_rb, path)
                    set_owner_only_permissions(path)
            if not had_blob:
                with contextlib.suppress(OSError):
                    cas.delete(source_sha256)
            raise

    return dict(
        zip(
            (
                "source_id",
                "source_sha256",
                "file_name",
                "filename",
                "size_bytes",
                "byte_count",
                "mime_type",
                "do_not_send_to_agent",
                "ocr_mode",
            ),
            (
                source_id,
                source_sha256,
                filename,
                filename,
                size_bytes,
                size_bytes,
                mime_type,
                effective_do_not_send,
                ocr_mode,
            ),
            strict=True,
        )
    )


def submit_clarification_answers_workflow(
    *,
    session_mgr: StudioSessionManager,
    sdir: Path,
    session_id: str,
    raw_answers: list[Any] | dict[str, Any],
    expected_answers_sha256: str | None,
    principal_id: str,
) -> tuple[int, str]:
    """Persist human clarification answers with mandatory OCC expected_answers_sha256 check."""
    answers_file = sdir / "clarifications" / "answers.json"
    with reentrant_session_lock(sdir / ".session.lock", session_id=sdir.name):
        answers_file.parent.mkdir(parents=True, exist_ok=True)
        current_answers: list[dict[str, Any]] = []
        if answers_file.is_file():
            try:
                raw = json.loads(answers_file.read_text(encoding="utf-8"))
                if isinstance(raw, list) and all(isinstance(x, dict) for x in raw):
                    current_answers = raw
                else:
                    raise StudioWorkflowError(
                        "CORRUPT_SESSION_RECORD",
                        f"Corrupt answers file in session '{session_id}': expected list.",
                        status_code=500,
                        exit_code=1,
                    )
            except (OSError, json.JSONDecodeError) as exc:
                raise StudioWorkflowError(
                    "CORRUPT_SESSION_RECORD",
                    f"Corrupt answers file in session '{session_id}': {exc}",
                    status_code=500,
                    exit_code=1,
                ) from exc

        if not current_answers:
            current_sha256 = EMPTY_ANSWERS_SHA256
        else:
            clean_cur = [{k: v for k, v in a.items() if v is not None} for a in current_answers]
            current_sha256 = hashlib.sha256(
                dumps_jcs_bytes(sorted(clean_cur, key=lambda a: a.get("question_id", "")))
            ).hexdigest()

        if expected_answers_sha256 is None:
            raise StudioWorkflowError(
                "MISSING_AUTO_COMMIT_PRECONDITION",
                "Submitting clarification answers requires expected_answers_sha256 precondition.",
                status_code=400,
                exit_code=2,
            )
        if expected_answers_sha256 != current_sha256:
            raise StudioWorkflowError(
                "ONT_EVO_CONCURRENT_MODIFICATION",
                f"OCC conflict on answers: expected '{expected_answers_sha256}', "
                f"current stored answers digest is '{current_sha256}'.",
                status_code=412,
                exit_code=5,
            )

        ans_items: list[ClarificationAnswerItem] = []
        try:
            if isinstance(raw_answers, dict):
                for qid, val in raw_answers.items():
                    if isinstance(val, dict):
                        vc = dict(val)
                        if "custom_text" in vc:
                            ct = vc.pop("custom_text")
                            vc.setdefault("user_text", ct)
                        ans_items.append(ClarificationAnswerItem(question_id=qid, **vc))
                    elif isinstance(val, list):
                        ans_items.append(
                            ClarificationAnswerItem(question_id=qid, selected_options=val)
                        )
                    else:
                        ans_items.append(
                            ClarificationAnswerItem(question_id=qid, user_text=str(val))
                        )
            else:
                for raw_item in raw_answers:
                    if isinstance(raw_item, ClarificationAnswerItem):
                        ans_items.append(raw_item)
                    elif isinstance(raw_item, dict):
                        ic = dict(raw_item)
                        if "custom_text" in ic:
                            ct = ic.pop("custom_text")
                            ic.setdefault("user_text", ct)
                        ans_items.append(ClarificationAnswerItem.model_validate(ic))
                    else:
                        ans_items.append(ClarificationAnswerItem.model_validate(raw_item))
        except (ValidationError, TypeError, ValueError) as exc:
            raise StudioWorkflowError(
                "SYNTAX_INVALID_ARGUMENT",
                f"Invalid clarification answer item format: {exc}",
                status_code=400,
                exit_code=2,
            ) from exc

        if not ans_items:
            raise StudioWorkflowError(
                "SYNTAX_INVALID_ARGUMENT",
                "Clarification answers submission must include at least one answer.",
                status_code=400,
                exit_code=2,
            )

        seen_qids: set[str] = set()
        for ans in ans_items:
            if ans.question_id in seen_qids:
                raise StudioWorkflowError(
                    "SYNTAX_INVALID_ARGUMENT",
                    f"Duplicate question_id '{ans.question_id}' in clarification answers.",
                    status_code=400,
                    exit_code=2,
                )
            seen_qids.add(ans.question_id)

        qfile = sdir / "clarifications" / "questions.json"
        if qfile.is_file():
            try:
                raw_q = json.loads(qfile.read_text(encoding="utf-8"))
                if not isinstance(raw_q, list):
                    raise StudioWorkflowError(
                        "CORRUPT_SESSION_RECORD",
                        f"Corrupt questions.json in session '{session_id}'.",
                        status_code=500,
                        exit_code=1,
                    )
                loaded_q = [ClarificationQuestion.model_validate(q) for q in raw_q]
            except StudioWorkflowError:
                raise
            except (OSError, json.JSONDecodeError, ValidationError, ValueError, TypeError) as exc:
                raise StudioWorkflowError(
                    "CORRUPT_SESSION_RECORD",
                    f"Corrupt questions.json in session '{session_id}': {exc}",
                    status_code=500,
                    exit_code=1,
                ) from exc
            if loaded_q:
                q_by_id = {q.question_id: q for q in loaded_q}
                for ans in ans_items:
                    if ans.question_id not in q_by_id:
                        raise StudioWorkflowError(
                            "RESOURCE_NOT_FOUND",
                            f"Clarification question '{ans.question_id}' not found.",
                            status_code=404,
                            exit_code=3,
                        )
                    q_def = q_by_id[ans.question_id]
                    if (
                        not q_def.allow_custom_text
                        and ans.user_text is not None
                        and ans.user_text.strip()
                    ):
                        raise StudioWorkflowError(
                            "SYNTAX_INVALID_ARGUMENT",
                            f"Question '{ans.question_id}' does not permit custom text answers.",
                            status_code=400,
                            exit_code=2,
                        )
                    valid_opts = {o.option_id for o in q_def.options} | {
                        o.label for o in q_def.options
                    }
                    for sel in ans.selected_options:
                        if sel not in valid_opts:
                            raise StudioWorkflowError(
                                "SYNTAX_INVALID_ARGUMENT",
                                f"Invalid option '{sel}' for question '{ans.question_id}'.",
                                status_code=400,
                                exit_code=2,
                            )

        answers_map = {
            a.get("question_id"): {k: v for k, v in a.items() if v is not None}
            for a in current_answers
            if a.get("question_id")
        }
        for ans in ans_items:
            answers_map[ans.question_id] = ans.model_dump(mode="json", exclude_none=True)
        reconcile_or_verify_audit_ledger(
            sdir, session_mgr.get_session(session_id), session_mgr.security_manager, mutate=True
        )
        merged_answers = sorted(answers_map.values(), key=lambda a: a.get("question_id", ""))
        new_sha256 = session_mgr.get_cas(session_id).put(dumps_jcs_bytes(merged_answers))

        tmp_answers = sdir / "clarifications" / f".answers.{uuid.uuid4().hex[:8]}.tmp"
        tmp_answers.write_text(json.dumps(merged_answers, indent=2), encoding="utf-8")
        replace_with_retry(tmp_answers, answers_file)
        set_owner_only_permissions(answers_file)
        append_signed_audit_record(
            session_dir=sdir,
            action="CLARIFICATION_ANSWERS_SAVED",
            actor=principal_id,
            details={"saved_count": len(ans_items), "answers_sha256": new_sha256},
            security_manager=session_mgr.security_manager,
        )
    return len(ans_items), new_sha256


def apply_starter_pack_workflow(
    *,
    session_mgr: StudioSessionManager,
    meta: SessionMetadata,
    sdir: Path,
    pack_ids: list[AssetManagementPackId],
    base_version: str | None,
    domain_namespace: str | None,
    expected_sha: str | None,
    principal_id: str,
) -> tuple[str, str, int, int]:
    """Compose and commit Asset Management starter packs onto session head."""
    if expected_sha is not None and expected_sha != meta.head_sha256:
        raise StudioWorkflowError(
            "ONT_EVO_CONCURRENT_MODIFICATION",
            f"Expected SHA ('{expected_sha}') does not match head SHA ('{meta.head_sha256}').",
            status_code=412,
            exit_code=5,
        )

    pack_ontology = AssetManagementStarterPackRegistry.compose_packs(
        pack_ids,
        version=base_version or meta.head_version,
        domain_namespace=domain_namespace or meta.domain_namespace,
    )
    with reentrant_session_lock(sdir / ".session.lock", session_id=sdir.name):
        reconcile_or_verify_audit_ledger(sdir, meta, session_mgr.security_manager, mutate=True)
        b_maj, b_min, _ = parse_semver(meta.head_version)
        next_ver = (
            ("1.0.0" if pack_ontology.canonical_business_concepts else f"0.{b_min + 1}.0")
            if b_maj == 0
            else f"{b_maj}.{b_min + 1}.0"
        )
        pack_ontology.ontology_version = next_ver
        pack_ontology.parent_sha256 = meta.head_sha256
        new_sha = compute_ontology_sha256(pack_ontology)
        v_dir = sdir / "versions" / f"v{next_ver}"
        v_dir.mkdir(parents=True, exist_ok=True)
        v_ont_json = v_dir / "ontology.json"
        v_ont_json.write_text(pack_ontology.model_dump_json(indent=2), encoding="utf-8")
        set_owner_only_permissions(v_ont_json)
        _atomic_write_session_json(
            sdir,
            meta.model_copy(
                update={
                    "head_version": next_ver,
                    "head_sha256": new_sha,
                    "updated_at": datetime.now(UTC).isoformat(),
                }
            ),
        )
        append_signed_audit_record(
            session_dir=sdir,
            action="STARTER_PACK_APPLIED",
            actor=principal_id,
            details={
                "pack_ids": [p.value if hasattr(p, "value") else str(p) for p in pack_ids],
                "new_version": next_ver,
                "new_sha256": new_sha,
            },
            security_manager=session_mgr.security_manager,
        )
    return (
        next_ver,
        new_sha,
        len(pack_ontology.canonical_business_concepts),
        len(pack_ontology.canonical_relationships),
    )
