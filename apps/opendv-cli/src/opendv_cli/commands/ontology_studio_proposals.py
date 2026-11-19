"""Ontology Studio CLI proposal list, inspect, accept, reject, and staged-candidate handlers."""

from __future__ import annotations

import argparse
import contextlib
import json
import uuid
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from opendv_cli.commands.ontology_studio_common import (
    MIN_WAIVER_RATIONALE_LENGTH,
    _atomic_write_json,
    _build_signed_waiver_record,
    _err,
    _err_from_opendv_error,
    _extract_proposal_base_sha256,
    _extract_proposal_operations,
    _get_studio_root,
    _load_and_verify_waiver_credentials,
    _load_session_version_ontology,
    _normalize_delta_operation,
    _require_session_context,
    _resolve_proposal_path,
    _resolve_studio_principal,
)
from opendv_cli.commands.ontology_studio_deltas import commit_validated_delta_cli
from opendv_contracts.dtos.ontology import OntologyDelta, is_destructive_delta
from opendv_contracts.dtos.promotion import PromoteCandidateRequest
from opendv_contracts.errors import OpenDVError
from opendv_service.ontology.applier import compute_next_semver
from opendv_service.ontology.validator import SemVerCalculusError
from opendv_service.studio.atomic_commit import parse_semver_tuple
from opendv_service.studio.locks import reentrant_session_lock
from opendv_service.studio.promoter import StagedCandidatePromoter
from opendv_service.studio.session_manager import append_signed_audit_record
from opendv_service.studio.workspace_security import WorkspaceSecurityManager

if TYPE_CHECKING:
    from opendv_cli.executor import LocalExecutor


def read_pending_proposal_record(pfile: Any, pid: str, *, conflict_prefix: str = "Proposal") -> tuple[int, Any] | dict[str, Any]:
    """Load proposal JSON and reject if missing or already resolved."""
    if not pfile.is_file():
        return _err(3, "PROPOSAL_NOT_FOUND", f"Proposal '{pid}' not found")
    try:
        prop_data = json.loads(pfile.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return _err(1, "CORRUPT_SESSION_RECORD", f"Failed to read proposal JSON: {exc}")
    if prop_data.get("status") in ("ACCEPTED", "REJECTED"):
        return _err(6, "PROPOSAL_ALREADY_RESOLVED", f"{conflict_prefix} '{pid}' has already been resolved ({prop_data.get('status')}).")
    return prop_data


def _require_proposal_context(args: argparse.Namespace, executor: LocalExecutor | None, pid: str) -> tuple[int, Any] | tuple[Any, Any, Any, str, Any]:
    ctx = _require_session_context(args, executor)
    if isinstance(ctx[0], int):
        return ctx  # type: ignore[return-value]
    session_mgr, meta, sdir, sid = ctx  # type: ignore[misc]
    prop_res = _resolve_proposal_path(sdir, pid)
    if isinstance(prop_res, tuple):
        return prop_res
    return session_mgr, meta, sdir, sid, prop_res


def handle_proposal_list(args: argparse.Namespace, executor: LocalExecutor | None = None) -> tuple[int, Any]:
    """13. `opendv ontology studio proposal list -s <session>`."""
    auth_res = _resolve_studio_principal(args)
    if isinstance(auth_res[0], int):
        return auth_res  # type: ignore[return-value]
    ctx = _require_session_context(args, executor)
    if isinstance(ctx[0], int):
        return ctx  # type: ignore[return-value]
    _, _, sdir, _ = ctx  # type: ignore[misc]
    props_dir = sdir / "proposals"
    results: list[dict[str, Any]] = []
    if props_dir.exists():
        for pfile in sorted(props_dir.glob("*.json")):
            with contextlib.suppress(json.JSONDecodeError, OSError):
                results.append(json.loads(pfile.read_text(encoding="utf-8")))
    return 0, results


def handle_proposal_show(args: argparse.Namespace, executor: LocalExecutor | None = None) -> tuple[int, Any]:
    """14. `opendv ontology studio proposal show -s <session> -p <proposal_id>`."""
    auth_res = _resolve_studio_principal(args)
    if isinstance(auth_res[0], int):
        return auth_res  # type: ignore[return-value]
    pid = getattr(args, "proposal_id", None) or getattr(args, "proposal", None)
    ctx = _require_session_context(args, executor)
    if isinstance(ctx[0], int):
        return ctx  # type: ignore[return-value]
    _, _, sdir, _ = ctx  # type: ignore[misc]
    prop_res = _resolve_proposal_path(sdir, pid)
    if isinstance(prop_res, tuple):
        return prop_res
    if not prop_res.is_file():
        return _err(3, "PROPOSAL_NOT_FOUND", f"Proposal '{pid}' not found")
    try:
        return 0, json.loads(prop_res.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return _err(1, "CORRUPT_SESSION_RECORD", f"Failed to read proposal JSON: {exc}")


def handle_proposal_accept(args: argparse.Namespace, executor: LocalExecutor | None = None) -> tuple[int, Any]:
    """15. `opendv ontology studio proposal accept` (RBAC, self-acceptance, waiver, governance)."""
    auth_res = _resolve_studio_principal(args)
    if isinstance(auth_res[0], int):
        return auth_res  # type: ignore[return-value]
    principal, role_tier = auth_res  # type: ignore[misc]
    if role_tier == "viewer":
        return _err(7, "FORBIDDEN_ADMIN_REQUIRED", "Read-only viewer role is not permitted to accept proposals.")
    if role_tier == "automated":
        return _err(7, "AGENT_PROPOSAL_ACCEPT_FORBIDDEN", "Automated agent role is forbidden from accepting ontology proposals.")
    if bool(getattr(args, "allow_self_accepted_proposals", False)) and role_tier != "admin":
        return _err(7, "FORBIDDEN_ADMIN_REQUIRED", "--allow-self-accepted-proposals requires the 'admin' role.")
    sec_mgr = WorkspaceSecurityManager(studio_root=_get_studio_root(executor))
    if bool(getattr(args, "low_confidence_waiver", False)):
        cred_err = _load_and_verify_waiver_credentials(args, executor, sec_mgr, role_tier=role_tier)
        if cred_err is not None:
            return cred_err
    pid, base_sha = getattr(args, "proposal_id", None) or getattr(args, "proposal", None), getattr(args, "base_sha256", None)
    if not pid or base_sha is None:
        return _err(2, "SYNTAX_INVALID_ARGUMENT", "Missing --proposal-id or --base-sha256")
    pctx = _require_proposal_context(args, executor, str(pid))
    if isinstance(pctx[0], int):
        return pctx  # type: ignore[return-value]
    session_mgr, meta, sdir, sid, pfile = pctx  # type: ignore[misc]
    with reentrant_session_lock(sdir / ".session.lock", session_id=sid):
        meta = session_mgr.get_session(sid)
        prop_or_err = read_pending_proposal_record(pfile, str(pid))
        if isinstance(prop_or_err, tuple):
            return prop_or_err
        prop_data = prop_or_err
        clean_base_sha = str(base_sha).strip('"')
        is_genesis_occ = meta.head_version in ("0.0.0", "", None) and clean_base_sha == ""
        if clean_base_sha != meta.head_sha256 and not is_genesis_occ:
            return _err(5, "ONT_EVO_CONCURRENT_MODIFICATION", f"Base SHA-256 mismatch: expected {meta.head_sha256}, got {base_sha}")
        prop_base = _extract_proposal_base_sha256(prop_data)
        is_genesis = meta.head_version in ("0.0.0", "", None) and (prop_base == "" or clean_base_sha == "")
        if prop_base != meta.head_sha256 and not is_genesis:
            return _err(5, "ONT_EVO_CONCURRENT_MODIFICATION", f"Proposal base SHA ('{prop_base}') is stale; rebase onto head '{meta.head_sha256}' is required before acceptance.")
        try:
            norm_ops = [_normalize_delta_operation(op) for op in _extract_proposal_operations(prop_data)]
        except (ValidationError, ValueError, TypeError, KeyError) as exc:
            return _err(4, "ONT_EVO_CONTRACT_VIOLATION", f"Invalid proposal operations: {exc}")
        if len(norm_ops) == 0:
            prop_data.update({"status": "ACCEPTED", "accepted_at": datetime.now(UTC).isoformat(), "accepted_by": principal, "commit_sha256": meta.head_sha256})
            _atomic_write_json(pfile, prop_data)
            append_signed_audit_record(session_dir=sdir, action="PROPOSAL_ACCEPTED_NOOP", actor=principal, details={"proposal_id": str(pid), "base_version": meta.head_version, "base_sha256": meta.head_sha256}, security_manager=sec_mgr)
            return 0, {"session_id": sid, "target_version": meta.head_version.lstrip("v"), "target_sha256": meta.head_sha256, "parent_sha256": meta.head_sha256, "delta_id": pid, "proposal_id": pid, "bump_type": "NONE", "applied_operations_count": 0}
        base_ont = _load_session_version_ontology(sdir, meta.head_version, domain_namespace=meta.domain_namespace, enterprise_owner=meta.enterprise_owner)
        if base_ont is None:
            return _err(1, "CORRUPT_SESSION_STATE", f"Missing head ontology '{meta.head_version}'")
        temp_delta = OntologyDelta.model_construct(delta_id=f"delta-{uuid.uuid4().hex[:8]}", parent_sha256=meta.head_sha256, base_ontology_version=meta.head_version, target_ontology_version=None, operations=norm_ops)
        try:
            next_ver = compute_next_semver(meta.head_version, temp_delta, base_ontology=base_ont)
        except (ValueError, TypeError, KeyError, SemVerCalculusError, ValidationError, AttributeError) as exc:
            return _err(4, "ONT_EVO_CONTRACT_VIOLATION", f"SemVer bump computation failure: {exc}")
        try:
            delta_obj = OntologyDelta(delta_id=pid, parent_sha256=meta.head_sha256, base_version=meta.head_version, target_version=next_ver, operations=norm_ops, author=principal, rationale=prop_data.get("rationale") or getattr(args, "waiver_rationale", None) or getattr(args, "grounding_rationale", None) or "Accepted via opendv ontology studio proposal accept")
        except (ValidationError, ValueError, TypeError) as exc:
            return _err(4, "ONT_EVO_CONTRACT_VIOLATION", f"Proposal delta construction failure: {exc}")
        is_major = parse_semver_tuple(next_ver)[0] > parse_semver_tuple(meta.head_version)[0] and meta.head_version != "0.0.0"
        if (is_destructive_delta(delta_obj, base_ont) or is_major) and role_tier != "admin":
            return _err(7, "FORBIDDEN_ADMIN_REQUIRED", "Destructive (MAJOR SemVer) proposal acceptance requires the 'admin' role.")
        waiver_res = _build_signed_waiver_record(args=args, delta_obj=delta_obj, base_ontology=base_ont, base_sha256=meta.head_sha256, proposal_id=str(pid), principal=principal, role_tier=role_tier, executor=executor, sec_mgr=sec_mgr)
        if isinstance(waiver_res, tuple):
            return waiver_res
        res = commit_validated_delta_cli(session_mgr=session_mgr, sec_mgr=sec_mgr, executor=executor, sid=sid, delta_obj=delta_obj, principal=principal, role_tier=role_tier, if_match=meta.head_sha256, waiver_record=waiver_res, proposal_id=str(pid))
        if isinstance(res, tuple):
            return res
        prop_data.update({"status": "ACCEPTED", "accepted_at": datetime.now(UTC).isoformat(), "accepted_by": principal, "commit_sha256": res.target_sha256})
        _atomic_write_json(pfile, prop_data)
        return 0, {"session_id": sid, "target_version": res.version.lstrip("v"), "target_sha256": res.target_sha256, "parent_sha256": res.parent_sha256, "delta_id": pid, "proposal_id": pid, "bump_type": delta_obj.highest_semver_class(base_ont), "applied_operations_count": len(norm_ops)}


def handle_proposal_reject(args: argparse.Namespace, executor: LocalExecutor | None = None) -> tuple[int, Any]:
    """16. `opendv ontology studio proposal reject`."""
    auth_res = _resolve_studio_principal(args)
    if isinstance(auth_res[0], int):
        return auth_res  # type: ignore[return-value]
    principal, role_tier = auth_res  # type: ignore[misc]
    if role_tier == "automated":
        return _err(7, "AGENT_OPERATION_FORBIDDEN", "Automated agent role is forbidden from rejecting proposals.")
    if role_tier == "viewer":
        return _err(7, "FORBIDDEN_ADMIN_REQUIRED", f"Role '{role_tier}' is not authorized to reject proposals.")
    pid = getattr(args, "proposal_id", None) or getattr(args, "proposal", None)
    ctx = _require_session_context(args, executor)
    if isinstance(ctx[0], int):
        return ctx  # type: ignore[return-value]
    _, _, sdir, sid = ctx  # type: ignore[misc]
    prop_res = _resolve_proposal_path(sdir, pid)
    if isinstance(prop_res, tuple):
        return prop_res
    with reentrant_session_lock(sdir / ".session.lock", session_id=sid):
        if not prop_res.is_file():
            return _err(3, "PROPOSAL_NOT_FOUND", f"Proposal '{pid}' not found")
        try:
            prop_data = json.loads(prop_res.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            return _err(1, "CORRUPT_SESSION_RECORD", f"Failed to read proposal JSON: {exc}")
        if prop_data.get("status") in ("ACCEPTED", "REJECTED"):
            return _err(6, "PROPOSAL_ALREADY_RESOLVED", f"Proposal '{pid}' has already been resolved ({prop_data.get('status')}).")
        reason = getattr(args, "reason", None) or "Rejected by steward"
        prop_data.update({"status": "REJECTED", "rejected_by": principal, "rejection_reason": reason})
        _atomic_write_json(prop_res, prop_data)
    return 0, {"proposal_id": pid, "status": "REJECTED", "reason": reason}


def handle_staged_list(args: argparse.Namespace, executor: LocalExecutor | None = None) -> tuple[int, Any]:
    """List held-back candidate concepts."""
    auth_res = _resolve_studio_principal(args)
    if isinstance(auth_res[0], int):
        return auth_res  # type: ignore[return-value]
    ctx = _require_session_context(args, executor)
    if isinstance(ctx[0], int):
        return ctx  # type: ignore[return-value]
    session_mgr, _meta, _sdir, sid = ctx  # type: ignore[misc]
    try:
        candidates = StagedCandidatePromoter(_sdir, session_mgr).list_staged_candidates()
        return 0, {"session_id": sid, "total_count": len(candidates), "staged_candidates": [c.model_dump(mode="json") for c in candidates]}
    except OpenDVError as exc:
        return _err_from_opendv_error(exc)


def handle_staged_promote(args: argparse.Namespace, executor: LocalExecutor | None = None) -> tuple[int, Any]:
    """Promote a held-back candidate concept via Path A or Path B."""
    auth_res = _resolve_studio_principal(args)
    if isinstance(auth_res[0], int):
        return auth_res  # type: ignore[return-value]
    principal, role_tier = auth_res  # type: ignore[misc]
    ctx = _require_session_context(args, executor)
    if isinstance(ctx[0], int):
        return ctx  # type: ignore[return-value]
    session_mgr, _meta, _sdir, _ = ctx  # type: ignore[misc]
    candidate_id = getattr(args, "candidate_id", None) or getattr(args, "candidate", None)
    if not candidate_id:
        return _err(2, "SYNTAX_INVALID_ARGUMENT", "Missing required --candidate-id flag")
    synonyms_str, waiver, rationale = getattr(args, "synonyms", None), bool(getattr(args, "waiver", False)), getattr(args, "rationale", None)
    if waiver and role_tier != "admin":
        return _err(7, "FORBIDDEN_ADMIN_REQUIRED", "Admin role required for Path B waiver promotion.")
    if waiver and (not rationale or len(rationale.strip()) < MIN_WAIVER_RATIONALE_LENGTH):
        return _err(4, "INVALID_GROUNDING_WAIVER", f"Waiver rationale must be at least {MIN_WAIVER_RATIONALE_LENGTH} characters.")
    req = PromoteCandidateRequest(path="path_b" if waiver else "path_a", synonyms=[s.strip() for s in synonyms_str.split(",")] if (synonyms_str and not waiver) else None, waiver_rationale=rationale if waiver else None, waiver=waiver)
    actor_name = principal if isinstance(principal, str) else getattr(principal, "principal", "human:steward")
    principal_obj = type("StudioPrincipal", (), {"principal": actor_name, "role": role_tier, "is_admin": role_tier == "admin", "auth_method": "cli"})()
    try:
        return 0, StagedCandidatePromoter(_sdir, session_mgr).promote_candidate(candidate_id, req, principal_obj).model_dump(mode="json")
    except OpenDVError as exc:
        return _err_from_opendv_error(exc)
