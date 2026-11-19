"""Shared Studio ontology export, promotion, feedback, bundle, and clone workflows."""

from __future__ import annotations

import contextlib
import json
import os
import re
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from opendv_contracts.errors import OpenDVError
from opendv_core.models.graph_a import GraphAOntology
from opendv_service.export.rdf_exporter import export_ontology
from opendv_service.studio.atomic_commit import AtomicCommitService, ConcurrentModificationError
from opendv_service.studio.bundle import (
    ArchiveSecurityViolationError,
    ImportGroundingRationaleRequiredError,
    ImportGroundingViolationError,
    compute_effective_session_taint,
    export_bundle,
    import_bundle,
)
from opendv_service.studio.delta_normalizer import (
    StudioWorkflowError,
    parse_strong_etag_or_error,
    raise_from_opendv_error,
    validate_version_syntax,
)
from opendv_service.studio.locks import reentrant_session_lock
from opendv_service.studio.session_manager import (
    InvalidSessionIdentifierError,
    SessionAlreadyExistsError,
    SessionMetadata,
    SessionNotFoundError,
    StudioSessionManager,
    TamperEvidentChainCorruptError,
    append_signed_audit_record,
)
from opendv_service.studio.workspace_security import (
    set_owner_only_permissions,
    validate_workspace_containment,
)


def derive_session_status(sdir: Path, meta: SessionMetadata) -> str:
    """Return 'PROMOTED' when session has active promotion metadata, else meta.status."""
    if meta.status == "ACTIVE" and ((sdir / "promotion.json").is_file() or meta.promoted_versions):
        return "PROMOTED"
    return meta.status


def export_session_ontology_workflow(
    *,
    meta: SessionMetadata,
    sdir: Path,
    export_format: str = "yaml",
    version: str | None = None,
) -> tuple[str, str, str, str]:
    """Export active or historical ontology snapshot returning `(content, media_type, clean_ver, safe_ext)`."""
    if version:
        validate_version_syntax(version)
    target_version = version or meta.head_version
    clean_ver = target_version.lstrip("v")
    validate_version_syntax(clean_ver)
    versions_dir = (sdir / "versions").resolve()
    vdir = (versions_dir / f"v{clean_ver}").resolve()
    if not vdir.is_relative_to(versions_dir):
        raise StudioWorkflowError("INVALID_WORKSPACE_PATH", f"Path traversal detected in version identifier '{target_version}'.", status_code=400, exit_code=2)
    if not vdir.is_dir():
        raise StudioWorkflowError("VERSION_NOT_FOUND", f"Version '{target_version}' not found in session.", status_code=404, exit_code=3)

    ont_file = vdir / "ontology.json" if (vdir / "ontology.json").is_file() else vdir / "ontology.yaml"
    if not ont_file.is_file():
        raise StudioWorkflowError("VERSION_NOT_FOUND", f"Ontology artifact missing for v{clean_ver}.", status_code=404, exit_code=3)
    try:
        raw_text = ont_file.read_text(encoding="utf-8")
        ont_obj = GraphAOntology.model_validate_json(raw_text) if ont_file.suffix == ".json" else GraphAOntology.model_validate(yaml.safe_load(raw_text))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, yaml.YAMLError, ValidationError, TypeError, ValueError) as exc:
        raise StudioWorkflowError("CORRUPT_SESSION_RECORD", f"Corrupt ontology artifact for version 'v{clean_ver}': {exc}", status_code=500, exit_code=1) from exc

    clean_format = export_format.strip()
    try:
        content, media_type = export_ontology(ont_obj, export_format=clean_format)
    except OpenDVError as exc:
        raise raise_from_opendv_error(exc) from exc
    except ValueError as exc:
        raise StudioWorkflowError("UNSUPPORTED_FORMAT", str(exc), status_code=415, exit_code=2) from exc
    safe_ext = re.sub(r"[^a-z0-9_-]", "", clean_format.lower()) or "txt"
    return content, media_type, clean_ver, safe_ext


def resolve_target_promotion_contract_path(target_ws: Path, domain_namespace: str) -> Path:
    if target_ws.suffix.lower() in (".yaml", ".yml", ".json"):
        return target_ws
    default_name = f"{domain_namespace}_graph_a_ontology.yaml"
    for cand_name in (default_name, "graph_a_ontology.yaml", "graph_a.yaml", "ontology.yaml", "ontology.json"):
        cand = target_ws / cand_name
        if cand.is_file():
            return cand
    return target_ws / default_name


def promote_session_workflow(
    *,
    session_mgr: StudioSessionManager,
    meta: SessionMetadata,
    allowed_roots: list[Path],
    if_match: str | None,
    head_sha256_body: str | None,
    workspace_if_match: str | None,
    workspace_sha256_body: str | None,
    rationale: str,
    workspace_arg: str | None,
    acknowledge_historical: bool,
    historical_rationale: str | None,
    principal_id: str,
    principal_role: str,
    is_remote: bool,
) -> dict[str, Any]:
    if not if_match:
        raise StudioWorkflowError("MISSING_PRECONDITION_HEADER", "Promoting a session requires an 'If-Match' header anchoring session head SHA-256.", status_code=400, exit_code=2)
    clean_if_match = parse_strong_etag_or_error(if_match, header_name="If-Match")
    if head_sha256_body is not None:
        clean_body_head = parse_strong_etag_or_error(head_sha256_body, header_name="head_sha256")
        if clean_if_match != clean_body_head:
            raise StudioWorkflowError("PRECONDITION_BASE_SHA_MISMATCH", f"If-Match header ('{clean_if_match}') does not match body head_sha256 ('{clean_body_head}').", status_code=412, exit_code=5)

    clean_ws_hdr = parse_strong_etag_or_error(workspace_if_match, header_name="X-OpenDV-Workspace-If-Match") if workspace_if_match is not None else None
    clean_ws_body = parse_strong_etag_or_error(workspace_sha256_body, header_name="workspace_sha256") if workspace_sha256_body is not None else None
    if clean_ws_hdr is None and clean_ws_body is None:
        raise StudioWorkflowError("MISSING_PRECONDITION_HEADER", "Promoting a session requires 'X-OpenDV-Workspace-If-Match' or 'workspace_sha256' precondition.", status_code=400, exit_code=2)
    if clean_ws_hdr is not None and clean_ws_body is not None and clean_ws_hdr != clean_ws_body:
        raise StudioWorkflowError("PRECONDITION_BASE_SHA_MISMATCH", f"X-OpenDV-Workspace-If-Match ('{clean_ws_hdr}') does not match body workspace_sha256 ('{clean_ws_body}').", status_code=412, exit_code=5)
    expected_ws_sha = clean_ws_hdr or clean_ws_body or ("0" * 64)

    if not rationale or len(rationale.strip()) < 10:
        raise StudioWorkflowError("PROMOTION_RATIONALE_REQUIRED", "Promotion rationale must be at least 10 characters.", status_code=422, exit_code=1)
    if workspace_arg and is_remote:
        raise StudioWorkflowError("REMOTE_SERVER_PATH_FORBIDDEN", "Server-local 'workspace' paths are forbidden in remote_platform mode.", status_code=403, exit_code=7)

    ws_base = allowed_roots[0]
    candidate_ws = (Path(workspace_arg) if Path(workspace_arg).is_absolute() else (ws_base / workspace_arg)) if workspace_arg else (session_mgr.workspace_root or ws_base)
    try:
        validated_ws = validate_workspace_containment(candidate_ws, allowed_roots=allowed_roots, is_remote_request=False)
    except OpenDVError as exc:
        raise raise_from_opendv_error(exc) from exc

    target_contract_path = resolve_target_promotion_contract_path(validated_ws, meta.domain_namespace)
    atomic_svc = AtomicCommitService(session_manager=session_mgr)
    try:
        promo_res = atomic_svc.promote_to_workspace(
            session_id=meta.session_id,
            target_workspace=target_contract_path,
            expected_workspace_sha256=expected_ws_sha,
            expected_head_sha256=clean_if_match,
            if_match=clean_if_match,
            rationale=rationale,
            actor=principal_id,
            actor_role=principal_role,
            acknowledge_historical_contract_warning=acknowledge_historical,
            historical_contract_rationale=historical_rationale,
            allowed_workspace_roots=allowed_roots,
            is_remote_request=False,
            enforce_lineage=(not acknowledge_historical),
            allow_noop=False,
        )
    except ConcurrentModificationError as exc:
        raise StudioWorkflowError("ONT_EVO_CONCURRENT_MODIFICATION", str(exc), status_code=412, exit_code=5) from exc
    except OpenDVError as exc:
        raise raise_from_opendv_error(exc) from exc

    try:
        rel_path = str(promo_res.target_contract_path.relative_to(session_mgr.workspace_root.resolve()))
    except ValueError:
        rel_path = promo_res.target_contract_path.name
    return {
        "session_id": meta.session_id,
        "promoted_version": promo_res.promoted_version.lstrip("v"),
        "head_sha256": promo_res.head_sha256,
        "workspace_sha256": promo_res.workspace_sha256,
        "promoted_at": promo_res.promoted_at,
        "target_contract_path": rel_path,
    }


def submit_feedback_workflow(
    *,
    session_mgr: StudioSessionManager,
    meta: SessionMetadata,
    sdir: Path,
    rating: str | int,
    comment: str | None,
    concept_uri: str | None,
    proposal_id: str | None,
    principal_id: str,
    principal_role: str,
    is_automated: bool,
) -> dict[str, Any]:
    if is_automated:
        raise StudioWorkflowError("AGENT_OPERATION_FORBIDDEN", "Automated principals are forbidden from submitting human review feedback.", status_code=403, exit_code=7)
    feedback_id = f"fbk-{uuid.uuid4().hex[:12]}"
    now_iso = datetime.now(UTC).isoformat()
    record = {
        "feedback_id": feedback_id,
        "timestamp": now_iso,
        "actor": principal_id,
        "role": principal_role,
        "rating": rating,
        "comment": comment,
        "concept_uri": concept_uri,
        "proposal_id": proposal_id,
        "active_version": meta.head_version,
        "head_sha256": meta.head_sha256,
    }
    with reentrant_session_lock(sdir / ".session.lock", session_id=sdir.name):
        fb_file = sdir / "feedback.jsonl"
        fd = os.open(str(fb_file), os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_BINARY", 0), 0o600)
        try:
            os.write(fd, (json.dumps(record, sort_keys=True) + "\n").encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)
        set_owner_only_permissions(fb_file)
        append_signed_audit_record(session_dir=sdir, action="FEEDBACK_SUBMITTED", actor=principal_id, details={"feedback_id": feedback_id, "rating": rating}, security_manager=session_mgr.security_manager)
    return {"status": "recorded", "feedback_id": feedback_id, "session_id": meta.session_id, "active_version": meta.head_version, "head_sha256": meta.head_sha256, "timestamp": now_iso}


def export_bundle_workflow(
    *,
    session_mgr: StudioSessionManager,
    sdir: Path,
    acknowledge_confidential: bool,
    export_rationale: str | None,
    principal_id: str,
    is_admin: bool,
) -> bytes:
    is_tainted = compute_effective_session_taint(sdir)
    if is_tainted and not is_admin:
        raise StudioWorkflowError("FORBIDDEN_ADMIN_REQUIRED", "Exporting sessions with confidential/tainted sources requires administrator role.", status_code=403, exit_code=7)
    if is_tainted and not acknowledge_confidential:
        raise StudioWorkflowError("CONFIDENTIAL_SOURCE_EXPORT_UNACKNOWLEDGED", "Session contains confidential sources. Pass acknowledge_confidential_source_export=true.", status_code=422, exit_code=4)
    if is_tainted and (not export_rationale or len(export_rationale.strip()) < 10):
        raise StudioWorkflowError("EXPORT_RATIONALE_REQUIRED", "Exporting a tainted session requires export_rationale of at least 10 characters.", status_code=400, exit_code=1)

    tmp_dir = session_mgr.studio_root / ".scratch"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp_out = tmp_dir / f".bundle_export_{uuid.uuid4().hex[:8]}.zip"
    try:
        export_bundle(sdir, tmp_out, admin=is_admin, acknowledge_confidential=acknowledge_confidential, export_rationale=export_rationale, security_manager=session_mgr.security_manager, actor=principal_id)
        return tmp_out.read_bytes()
    except OpenDVError as exc:
        raise raise_from_opendv_error(exc) from exc
    finally:
        with contextlib.suppress(OSError):
            if tmp_out.exists():
                tmp_out.unlink()


def import_bundle_sync(
    *,
    tmp_in: Path,
    session_mgr: StudioSessionManager,
    principal_id: str,
    principal_role: str,
    new_session_id: str | None,
    acknowledge_unverified_import_attribution: bool,
    acknowledgement_rationale: str | None,
    force: bool,
) -> dict[str, Any]:
    """Run synchronous bundle verification and atomic import."""
    try:
        meta = import_bundle(
            tmp_in,
            session_mgr.studio_root,
            security_manager=session_mgr.security_manager,
            new_session_id=new_session_id,
            acknowledge_unverified_import_attribution=acknowledge_unverified_import_attribution,
            acknowledgement_rationale=acknowledgement_rationale,
            imported_by=principal_id,
            actor_role=principal_role,
            force=force,
        )
    except ImportGroundingRationaleRequiredError as exc:
        raise StudioWorkflowError("IMPORT_GROUNDING_RATIONALE_REQUIRED", str(exc), status_code=400, exit_code=4) from exc
    except ImportGroundingViolationError as exc:
        raise StudioWorkflowError("IMPORT_GROUNDING_VIOLATION", str(exc), status_code=422, exit_code=4) from exc
    except (SessionAlreadyExistsError, FileExistsError) as exc:
        raise StudioWorkflowError("SESSION_ALREADY_EXISTS", str(exc), status_code=409, exit_code=6) from exc
    except InvalidSessionIdentifierError as exc:
        raise StudioWorkflowError("INVALID_SESSION_IDENTIFIER", str(exc), status_code=400, exit_code=2) from exc
    except ArchiveSecurityViolationError as exc:
        raise raise_from_opendv_error(exc) from exc
    except OpenDVError as exc:
        raise raise_from_opendv_error(exc) from exc
    except Exception as exc:
        raise StudioWorkflowError("EXECUTION_FAILURE", str(exc), status_code=500, exit_code=1) from exc

    sdir = session_mgr.get_session_dir(meta.session_id)
    from opendv_service.studio.session_workflows import build_session_summary_payload

    return build_session_summary_payload(sdir, meta)


def clone_session_workflow(
    *,
    session_mgr: StudioSessionManager,
    session_id: str,
    new_session_id: str | None,
    principal_id: str,
    principal_role: str,
    is_admin: bool,
    is_automated: bool,
) -> dict[str, Any]:
    _ = principal_role
    requested_new_sid = new_session_id or f"clone-{session_id[:51].rstrip('-_')}-{uuid.uuid4().hex[:6]}"
    role_tier = "admin" if is_admin else ("automated" if is_automated else "editor")
    try:
        meta = session_mgr.clone_session(source_session_id=session_id, new_session_id=requested_new_sid, actor=principal_id, actor_role=role_tier)
    except InvalidSessionIdentifierError as exc:
        raise StudioWorkflowError("SYNTAX_INVALID_ARGUMENT", f"Invalid destination session identifier '{requested_new_sid}': {exc}", status_code=400, exit_code=2) from exc
    except SessionAlreadyExistsError as exc:
        raise StudioWorkflowError("SESSION_ALREADY_EXISTS", f"Session '{requested_new_sid}' already exists.", status_code=409, exit_code=6) from exc
    except TamperEvidentChainCorruptError as exc:
        raise StudioWorkflowError("TAMPER_EVIDENT_CHAIN_CORRUPT", str(exc), status_code=422, exit_code=4) from exc
    except SessionNotFoundError as exc:
        raise StudioWorkflowError("TAMPER_EVIDENT_CHAIN_CORRUPT", f"Audit ledger or session metadata missing for session '{session_id}': {exc}", status_code=422, exit_code=4) from exc
    except OpenDVError as exc:
        raise raise_from_opendv_error(exc) from exc
    except Exception as exc:
        raise StudioWorkflowError("INTERNAL_CLONE_ERROR", f"Failed to clone session '{session_id}': {exc}", status_code=500, exit_code=1) from exc

    new_dir = session_mgr.get_session_dir(meta.session_id)
    return {
        "session_id": meta.session_id,
        "domain_namespace": meta.domain_namespace,
        "enterprise_owner": meta.enterprise_owner,
        "status": derive_session_status(new_dir, meta),
        "active_version": meta.head_version,
        "head_sha256": meta.head_sha256,
        "created_at": meta.created_at,
        "updated_at": meta.updated_at,
        "wizard_schema_version": int(getattr(meta, "wizard_schema_version", None) or 1),
    }
