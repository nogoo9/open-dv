"""Shared Studio proposal list, create, accept, and reject workflows."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from opendv_contracts.dtos.ontology import (
    DESTRUCTIVE_OP_TYPES,
    OntologyDelta,
    is_destructive_delta,
)
from opendv_contracts.errors import OpenDVError
from opendv_core.genesis import GENESIS_VERSION
from opendv_service.studio.atomic_commit import (
    AtomicCommitService,
    ConcurrentModificationError,
    _load_grandfathered_concept_uris,
)
from opendv_service.studio.delta_normalizer import (
    StudioWorkflowError,
    compute_expected_semver_for_ops,
    load_session_version_ontology,
    normalize_delta_operation_wire,
    parse_strong_etag_or_error,
    raise_from_opendv_error,
)
from opendv_service.studio.locks import reentrant_session_lock
from opendv_service.studio.proposal_helpers import (
    compute_stored_proposal_delta_sha256,
    ensure_session_version_exists,
    normalize_proposal_record,
    read_proposal_json_safe,
    resolve_proposal_base_version_and_sha,
    resolve_proposal_file_safe,
    write_proposal_json_atomic,
)
from opendv_service.studio.proposal_rebase_workflows import (
    collect_intervening_deltas,
    rebase_session_proposal_workflow,
    refine_session_proposal_workflow,
)
from opendv_service.studio.session_manager import (
    SessionMetadata,
    StudioSessionManager,
    append_signed_audit_record,
)
from opendv_service.studio.waiver_workflows import build_and_sign_commit_waivers

logger = logging.getLogger(__name__)


def list_session_proposals(sdir: Path, session_id: str) -> list[dict[str, Any]]:
    proposals_dir = sdir / "proposals"
    if not proposals_dir.is_dir():
        return []
    records: list[dict[str, Any]] = []
    for pfile in sorted(proposals_dir.glob("*.json")):
        try:
            raw_data = json.loads(pfile.read_text(encoding="utf-8"))
            if isinstance(raw_data, dict):
                records.append(normalize_proposal_record(raw_data, session_id=session_id, fallback_proposal_id=pfile.stem))
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            logger.debug("Skipping unreadable proposal file %s: %s", pfile, exc)
    return records


def get_session_proposal_detail(sdir: Path, session_id: str, proposal_id: str) -> dict[str, Any]:
    prop_file = resolve_proposal_file_safe(sdir, proposal_id)
    raw_data = read_proposal_json_safe(prop_file, proposal_id)
    return normalize_proposal_record(raw_data, session_id=session_id, fallback_proposal_id=proposal_id)


def create_session_proposal_workflow(
    *,
    meta: SessionMetadata,
    sdir: Path,
    proposal_id: str | None,
    base_version_req: str | None,
    base_sha256_req: str | None,
    operations: list[dict[str, Any]],
    rationale: str | None,
    principal_id: str,
    is_automated: bool,
) -> dict[str, Any]:
    if proposal_id:
        resolve_proposal_file_safe(sdir, proposal_id)
        eff_pid = proposal_id
    else:
        eff_pid = f"prop-{uuid.uuid4().hex[:12]}"

    norm_ops = [normalize_delta_operation_wire(op) for op in operations]
    if is_automated and any(isinstance(op, dict) and op.get("op_type") in DESTRUCTIVE_OP_TYPES for op in norm_ops):
        raise StudioWorkflowError("AGENT_DESTRUCTIVE_OP_FORBIDDEN", "Automated principals are strictly forbidden from submitting destructive operations.", status_code=403, exit_code=7)

    base_version, version_sha256, base_ont = resolve_proposal_base_version_and_sha(sdir, meta, base_version_req, base_sha256_req)
    try:
        t_ver = compute_expected_semver_for_ops(base_version, version_sha256, norm_ops, base_ont, meta.domain_namespace)
        candidate_delta = OntologyDelta(
            delta_id=f"delta-{uuid.uuid4().hex[:12]}",
            parent_sha256=version_sha256,
            base_version=base_version,
            target_version=t_ver,
            domain_namespace=meta.domain_namespace,
            operations=norm_ops,
            author=principal_id,
            rationale=rationale,
        )
    except OpenDVError as exc:
        raise raise_from_opendv_error(exc) from exc
    except (ValidationError, ValueError, TypeError) as exc:
        raise StudioWorkflowError("ONT_EVO_CONTRACT_VIOLATION", f"Invalid proposal operations: {exc}", status_code=422, exit_code=4) from exc

    if is_automated and is_destructive_delta(candidate_delta, base_ont):
        raise StudioWorkflowError("AGENT_DESTRUCTIVE_OP_FORBIDDEN", "Automated principals are strictly forbidden from submitting destructive operations.", status_code=403, exit_code=7)

    record = {
        "proposal_id": eff_pid,
        "session_id": meta.session_id,
        "base_version": base_version,
        "base_sha256": version_sha256,
        "status": "PENDING",
        "operations": norm_ops,
        "proposer": principal_id,
        "created_at": datetime.now(UTC).isoformat(),
        "rationale": rationale,
        "delta": candidate_delta.model_dump(mode="json", exclude_none=True),
    }
    with reentrant_session_lock(sdir / ".session.lock", session_id=sdir.name):
        prop_file = resolve_proposal_file_safe(sdir, eff_pid)
        if prop_file.exists():
            raise StudioWorkflowError("PROPOSAL_ALREADY_RESOLVED", f"Proposal '{eff_pid}' already exists in session '{meta.session_id}'.", status_code=409, exit_code=6)
        write_proposal_json_atomic(prop_file, record)
    return record


def accept_session_proposal_workflow(
    *,
    session_mgr: StudioSessionManager,
    meta: SessionMetadata,
    sdir: Path,
    proposal_id: str,
    if_match: str | None,
    base_sha256_req: str | None,
    low_confidence_waiver: bool,
    self_acceptance_override: bool,
    grounding_rationale: str | None,
    explicit_ungrounded: list[str] | str | None,
    principal_id: str,
    principal_role: str,
    is_admin: bool,
    is_automated: bool,
) -> dict[str, Any]:
    clean_if_match_header = parse_strong_etag_or_error(if_match) if if_match is not None else None
    if is_automated:
        raise StudioWorkflowError("AGENT_PROPOSAL_ACCEPT_FORBIDDEN", "Automated agent identities are strictly forbidden from accepting proposals.", status_code=403, exit_code=7)
    if principal_role == "viewer":
        raise StudioWorkflowError("SERVER_OPERATION_FORBIDDEN", "Read-only 'viewer' role is forbidden from accepting proposals.", status_code=403, exit_code=7)
    if (low_confidence_waiver or self_acceptance_override) and not is_admin:
        raise StudioWorkflowError("FORBIDDEN_ADMIN_REQUIRED", "Destructive revisions, grounding waivers, and self-acceptance overrides require admin role.", status_code=403, exit_code=7)

    prop_file = resolve_proposal_file_safe(sdir, proposal_id)
    raw_prop = read_proposal_json_safe(prop_file, proposal_id)
    prop_data = normalize_proposal_record(raw_prop, session_id=meta.session_id, fallback_proposal_id=proposal_id)
    if prop_data.get("status") in ("ACCEPTED", "REJECTED"):
        raise StudioWorkflowError("PROPOSAL_ALREADY_RESOLVED", f"Proposal '{proposal_id}' has already been resolved ({prop_data.get('status')}).", status_code=409, exit_code=6)

    ops = [normalize_delta_operation_wire(op) for op in prop_data.get("operations", [])]
    base_ontology = load_session_version_ontology(sdir, meta, meta.head_version)
    try:
        next_ver = compute_expected_semver_for_ops(meta.head_version, meta.head_sha256, ops, base_ontology, meta.domain_namespace)
        delta = OntologyDelta(
            delta_id=f"delta-{uuid.uuid4().hex[:12]}",
            parent_sha256=meta.head_sha256,
            base_version=meta.head_version,
            target_version=next_ver,
            domain_namespace=meta.domain_namespace,
            operations=ops,
            author=principal_id,
            rationale=prop_data.get("rationale") or grounding_rationale,
        )
    except OpenDVError as exc:
        raise raise_from_opendv_error(exc) from exc
    except (ValidationError, ValueError, TypeError) as exc:
        raise StudioWorkflowError("ONT_EVO_CONTRACT_VIOLATION", f"Proposal delta construction failure: {exc}", status_code=422, exit_code=4) from exc

    if (is_destructive_delta(delta, base_ontology) or low_confidence_waiver or self_acceptance_override) and not is_admin:
        raise StudioWorkflowError("FORBIDDEN_ADMIN_REQUIRED", "Destructive revisions, grounding waivers, and self-acceptance overrides require admin role.", status_code=403, exit_code=7)

    submitted_sha = compute_stored_proposal_delta_sha256(sdir, meta, raw_prop, proposal_id)
    signed_waivers = build_and_sign_commit_waivers(
        sdir=sdir,
        session_mgr=session_mgr,
        base_ontology=base_ontology,
        delta=delta,
        principal_id=principal_id,
        low_confidence_waiver=low_confidence_waiver,
        grounding_rationale=grounding_rationale,
        explicit_ungrounded=explicit_ungrounded,
        proposal_id=proposal_id,
        submitted_delta_sha256=submitted_sha,
    )

    clean_body_sha = parse_strong_etag_or_error(base_sha256_req, header_name="base_sha256") if base_sha256_req else None
    if clean_if_match_header is None and clean_body_sha is None:
        raise StudioWorkflowError("MISSING_PRECONDITION_HEADER", "Accepting a proposal requires an 'If-Match' header anchoring session head SHA-256.", status_code=400, exit_code=2)
    if clean_if_match_header is not None and clean_body_sha is not None and clean_if_match_header != clean_body_sha:
        raise StudioWorkflowError("PRECONDITION_BASE_SHA_MISMATCH", f"If-Match header ('{clean_if_match_header}') does not match body base_sha256 ('{clean_body_sha}').", status_code=412, exit_code=5)
    clean_if_match = clean_if_match_header or clean_body_sha
    if clean_if_match != meta.head_sha256:
        raise StudioWorkflowError("ONT_EVO_CONCURRENT_MODIFICATION", f"If-Match ('{clean_if_match}') does not match session head SHA-256 ('{meta.head_sha256}').", status_code=412, exit_code=5)

    prop_base = prop_data.get("base_sha256") or ""
    if prop_base != meta.head_sha256 and not (prop_base == "" and meta.head_version == GENESIS_VERSION):
        raise StudioWorkflowError("PROPOSAL_STALE_REBASE_REQUIRED", f"Proposal base SHA-256 ('{prop_base}') does not match current session head ('{meta.head_sha256}'). Proposal rebase is required.", status_code=409, exit_code=6)

    atomic_svc = AtomicCommitService(session_manager=session_mgr)
    eval_dict = {"waivers": [w.model_dump(mode="json") for w in signed_waivers]} if signed_waivers else None
    try:
        commit_res = atomic_svc.commit_delta(
            session_id=sdir.name,
            delta=delta,
            proposal_id=proposal_id,
            author=principal_id,
            actor_role=principal_role,
            if_match=clean_if_match,
            evaluation=eval_dict,
            waivers=signed_waivers or None,
            grandfathered_concept_uris=_load_grandfathered_concept_uris(sdir),
        )
    except ConcurrentModificationError as exc:
        if "already been resolved" in str(exc):
            raise StudioWorkflowError("PROPOSAL_ALREADY_RESOLVED", f"Proposal '{proposal_id}' has already been resolved.", status_code=409, exit_code=6) from exc
        raise StudioWorkflowError("ONT_EVO_CONCURRENT_MODIFICATION", str(exc), status_code=412, exit_code=5) from exc
    except OpenDVError as exc:
        raise raise_from_opendv_error(exc) from exc

    return {
        "session_id": meta.session_id,
        "target_version": commit_res.version.lstrip("v"),
        "target_sha256": commit_res.target_sha256,
        "parent_sha256": meta.head_sha256,
        "delta_id": delta.delta_id,
        "bump_type": delta.highest_semver_class(base_ontology),
        "applied_operations_count": len(delta.operations),
    }


def reject_session_proposal_workflow(
    *,
    session_mgr: StudioSessionManager,
    sdir: Path,
    session_id: str,
    proposal_id: str,
    reason: str | None,
    principal_id: str,
    is_automated: bool,
) -> dict[str, Any]:
    if is_automated:
        raise StudioWorkflowError("AGENT_OPERATION_FORBIDDEN", "Automated agent principals cannot reject proposals.", status_code=403, exit_code=7)
    prop_file = resolve_proposal_file_safe(sdir, proposal_id)
    if not prop_file.is_file():
        raise StudioWorkflowError("PROPOSAL_NOT_FOUND", f"Proposal '{proposal_id}' not found.", status_code=404, exit_code=3)
    with reentrant_session_lock(sdir / ".session.lock", session_id=sdir.name):
        prop_data = normalize_proposal_record(read_proposal_json_safe(prop_file, proposal_id), session_id=session_id, fallback_proposal_id=proposal_id)
        if prop_data.get("status") in ("ACCEPTED", "REJECTED", "SUPERSEDED"):
            raise StudioWorkflowError("PROPOSAL_ALREADY_RESOLVED", f"Proposal '{proposal_id}' has already been resolved ({prop_data.get('status')}).", status_code=409, exit_code=6)
        eff_reason = reason or "Rejected by steward"
        prop_data.update({"status": "REJECTED", "reject_reason": eff_reason, "resolved_at": datetime.now(UTC).isoformat()})
        write_proposal_json_atomic(prop_file, prop_data)
        append_signed_audit_record(
            session_dir=sdir,
            action="PROPOSAL_REJECTED",
            actor=principal_id,
            details={"proposal_id": proposal_id, "reason": eff_reason},
            security_manager=session_mgr.security_manager,
        )
    return prop_data


__all__ = [
    "accept_session_proposal_workflow",
    "collect_intervening_deltas",
    "compute_stored_proposal_delta_sha256",
    "create_session_proposal_workflow",
    "ensure_session_version_exists",
    "get_session_proposal_detail",
    "list_session_proposals",
    "normalize_proposal_record",
    "rebase_session_proposal_workflow",
    "refine_session_proposal_workflow",
    "reject_session_proposal_workflow",
    "resolve_proposal_base_version_and_sha",
    "resolve_proposal_file_safe",
]
