"""Ontology Studio CLI proposal rebase and refine handlers."""

from __future__ import annotations

import argparse
import contextlib
import json
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml
from pydantic import ValidationError

from opendv_cli.commands.ontology_studio_common import (
    StudioLocalError,
    _atomic_write_json,
    _build_workspace_deployment_provider,
    _err,
    _err_from_opendv_error,
    _extract_proposal_operations,
    _extract_proposal_proposer,
    _load_session_version_ontology,
    _normalize_delta_operation,
    _require_session_context,
    _resolve_proposal_path,
    _resolve_studio_principal,
)
from opendv_cli.commands.ontology_studio_proposals import (
    _require_proposal_context,
    read_pending_proposal_record,
)
from opendv_contracts.dtos.ontology import OntologyDelta
from opendv_contracts.errors import OpenDVError
from opendv_service.ontology.applier import apply as apply_delta
from opendv_service.ontology.applier import compute_next_semver, parse_semver
from opendv_service.ontology.rebase import check_rebase_conflicts
from opendv_service.ontology.validator import GovernanceValidator, SemVerCalculusError
from opendv_service.studio.job_runners import run_construct_session
from opendv_service.studio.locks import reentrant_session_lock

if TYPE_CHECKING:
    from opendv_cli.executor import LocalExecutor


def _load_intervening_deltas_strict(sdir: Path, prop_base_ver_str: str, head_ver_str: str) -> tuple[int, dict[str, str]] | list[OntologyDelta]:
    versions_dir = sdir / "versions"
    if not versions_dir.is_dir():
        return _err(1, "CORRUPT_SESSION_RECORD", f"Missing versions directory in '{sdir.name}'")
    try:
        base_tuple, head_tuple = parse_semver(prop_base_ver_str.lstrip("v") or "0.0.0"), parse_semver(head_ver_str.lstrip("v") or "0.0.0")
    except (SemVerCalculusError, ValueError) as exc:
        return _err(1, "CORRUPT_SESSION_STATE", f"Invalid version identifier during rebase: {exc}")
    matched: list[tuple[tuple[int, int, int], Path]] = []
    for child in versions_dir.iterdir():
        if child.is_dir() and child.name.startswith("v"):
            with contextlib.suppress(SemVerCalculusError, ValueError):
                ver_t = parse_semver(child.name[1:])
                if base_tuple < ver_t <= head_tuple:
                    matched.append((ver_t, child))
    matched.sort(key=lambda item: item[0])
    deltas: list[OntologyDelta] = []
    for ver_t, vdir in matched:
        delta_file = vdir / "delta.json"
        if not delta_file.is_file():
            return _err(1, "CORRUPT_SESSION_RECORD", f"Missing intervening delta.json for version v{ver_t[0]}.{ver_t[1]}.{ver_t[2]}")
        try:
            raw_d = json.loads(delta_file.read_text(encoding="utf-8"))
            raw_d["operations"] = [_normalize_delta_operation(op) for op in raw_d.get("operations", [])]
            deltas.append(OntologyDelta.model_validate(raw_d))
        except (OSError, json.JSONDecodeError, ValidationError, ValueError, TypeError) as exc:
            return _err(1, "CORRUPT_SESSION_RECORD", f"Corrupt intervening delta.json in {vdir.name}: {exc}")
    return deltas


def handle_proposal_rebase(args: argparse.Namespace, executor: LocalExecutor | None = None) -> tuple[int, Any]:
    """17. `opendv ontology studio proposal rebase` via 24-op `check_rebase_conflicts`."""
    auth_res = _resolve_studio_principal(args)
    if isinstance(auth_res[0], int):
        return auth_res  # type: ignore[return-value]
    _, role_tier = auth_res  # type: ignore[misc]
    if role_tier == "automated":
        return _err(7, "AGENT_OPERATION_FORBIDDEN", "Automated agent role is forbidden from rebasing proposals.")
    if role_tier == "viewer":
        return _err(7, "FORBIDDEN_ADMIN_REQUIRED", f"Role '{role_tier}' is not authorized to rebase proposals.")
    pid = getattr(args, "proposal_id", None) or getattr(args, "proposal", None)
    if not pid:
        return _err(2, "SYNTAX_INVALID_ARGUMENT", "Missing required --proposal-id")
    pctx = _require_proposal_context(args, executor, str(pid))
    if isinstance(pctx[0], int):
        return pctx  # type: ignore[return-value]
    session_mgr, meta, sdir, sid, pfile = pctx  # type: ignore[misc]
    with reentrant_session_lock(sdir / ".session.lock", session_id=sid):
        meta = session_mgr.get_session(sid)
        prop_or_err = read_pending_proposal_record(pfile, str(pid), conflict_prefix="Cannot rebase proposal")
        if isinstance(prop_or_err, tuple):
            return prop_or_err
        prop_data = prop_or_err
        target_sha_arg = getattr(args, "base_sha256", None) or getattr(args, "head_sha256", None)
        if target_sha_arg is not None and str(target_sha_arg).strip('"') != meta.head_sha256:
            return _err(5, "ONT_EVO_CONCURRENT_MODIFICATION", f"Base SHA mismatch: expected {meta.head_sha256}, got {str(target_sha_arg).strip('\"')}")
        try:
            ops = [_normalize_delta_operation(op) for op in _extract_proposal_operations(prop_data)]
        except (ValidationError, ValueError, TypeError, KeyError) as exc:
            return _err(4, "ONT_EVO_CONTRACT_VIOLATION", f"Invalid proposal operations: {exc}")
        head_ver = (meta.head_version or "0.0.0").lstrip("v")
        head_vdir = sdir / "versions" / f"v{head_ver}"
        if head_ver != "0.0.0" and not (head_vdir / "ontology.json").is_file() and not (head_vdir / "ontology.yaml").is_file():
            return _err(1, "CORRUPT_SESSION_RECORD", f"Head ontology artifact missing for version 'v{head_ver}'; refusing blind rebase.")
        try:
            head_ontology = _load_session_version_ontology(sdir, meta.head_version, domain_namespace=meta.domain_namespace, enterprise_owner=meta.enterprise_owner)
        except (OSError, json.JSONDecodeError, yaml.YAMLError, ValidationError, ValueError) as exc:
            return _err(1, "CORRUPT_SESSION_RECORD", f"Corrupt head ontology artifact for version 'v{head_ver}': {exc}")
        if head_ontology is None:
            return _err(1, "CORRUPT_SESSION_RECORD", f"Head ontology artifact could not be loaded for version 'v{head_ver}'.")
        prop_base_ver = str(prop_data.get("base_version") or (prop_data.get("delta") or {}).get("base_ontology_version") or "0.0.0").lstrip("v")
        try:
            prop_base_ontology = _load_session_version_ontology(sdir, prop_base_ver, domain_namespace=meta.domain_namespace, enterprise_owner=meta.enterprise_owner)
        except (OSError, json.JSONDecodeError, yaml.YAMLError, ValidationError, ValueError) as exc:
            return _err(1, "CORRUPT_SESSION_RECORD", f"Corrupt proposal base ontology 'v{prop_base_ver}': {exc}")
        if prop_base_ontology is None:
            return _err(1, "CORRUPT_SESSION_RECORD", f"Missing proposal base ontology artifact 'v{prop_base_ver}'.")
        deltas_res = _load_intervening_deltas_strict(sdir, prop_base_ver, head_ver)
        if isinstance(deltas_res, tuple):
            return deltas_res
        rebase_res = check_rebase_conflicts(ops, base_ontology=prop_base_ontology, head_ontology=head_ontology, intervening_deltas=deltas_res)
        if rebase_res.has_conflicts:
            conflict_dicts = [c.to_dict() for c in rebase_res.conflicts]
            return 6, {"status": "ERROR", "error_code": "PROPOSAL_STALE_REBASE_REQUIRED", "detail": f"Rebase blocked by {len(conflict_dicts)} conflict(s): {conflict_dicts[0].get('reason', '24-op conflict detected')}", "conflicts": conflict_dicts}
        temp_delta = OntologyDelta.model_construct(delta_id=f"delta-{uuid.uuid4().hex[:8]}", parent_sha256=meta.head_sha256, base_ontology_version=meta.head_version or "0.0.0", target_ontology_version=None, operations=ops)
        try:
            t_ver = compute_next_semver(meta.head_version or "0.0.0", temp_delta, base_ontology=head_ontology)
            rebased_delta = OntologyDelta(delta_id=str((prop_data.get("delta") or {}).get("delta_id") or f"delta-{uuid.uuid4().hex[:12]}"), parent_sha256=meta.head_sha256, base_version=meta.head_version, target_version=t_ver, operations=ops, author=_extract_proposal_proposer(prop_data))
            apply_delta(head_ontology, rebased_delta, validate_structural=True)
            dep_prov = _build_workspace_deployment_provider(executor)
            if dep_prov is not None:
                GovernanceValidator(deployment_provider=dep_prov, strict_verifier_required=False).validate_deployed_contracts(head_ontology, rebased_delta)
        except StudioLocalError as exc:
            return _err_from_opendv_error(exc)
        except (ValidationError, ValueError, TypeError, KeyError, SemVerCalculusError, OpenDVError, AttributeError) as exc:
            return _err(4, "ONT_EVO_CONTRACT_VIOLATION", f"Rebase validation failed against head: {exc}")
        prop_data.update({"base_sha256": meta.head_sha256, "base_ontology_sha256": meta.head_sha256, "base_version": meta.head_version, "rebased_at": datetime.now(UTC).isoformat(), "status": "PENDING"})
        if isinstance(prop_data.get("delta"), dict):
            prop_data["delta"] = rebased_delta.model_dump(mode="json", exclude_none=True)
        _atomic_write_json(pfile, prop_data)
    return 0, {"proposal_id": pid, "status": "REBASED", "base_sha256": meta.head_sha256, "base_version": meta.head_version}


def handle_proposal_refine(args: argparse.Namespace, executor: LocalExecutor | None = None) -> tuple[int, Any]:
    """Iteratively refine a pending proposal using natural-language steering instructions."""
    auth_res = _resolve_studio_principal(args)
    if isinstance(auth_res[0], int):
        return auth_res  # type: ignore[return-value]
    principal, role_tier = auth_res  # type: ignore[misc]
    if role_tier == "viewer":
        return _err(7, "FORBIDDEN_ADMIN_REQUIRED", "Read-only viewer role is not permitted to refine proposals.")
    ctx = _require_session_context(args, executor)
    if isinstance(ctx[0], int):
        return ctx  # type: ignore[return-value]
    session_mgr, _meta, sdir, sid = ctx  # type: ignore[misc]
    pid, steering = getattr(args, "proposal_id", None) or getattr(args, "proposal", None), getattr(args, "steering_prompt", None)
    if not pid:
        return _err(2, "SYNTAX_INVALID_ARGUMENT", "Missing required --proposal-id flag")
    if not steering:
        return _err(2, "SYNTAX_INVALID_ARGUMENT", "Missing required --steering-prompt flag")
    prop_res = _resolve_proposal_path(sdir, pid)
    if isinstance(prop_res, tuple):
        return prop_res
    if not prop_res.is_file():
        return _err(3, "PROPOSAL_NOT_FOUND", f"Proposal '{pid}' not found")
    try:
        pred_dict = json.loads(prop_res.read_text(encoding="utf-8"))
        if pred_dict.get("status") in ("ACCEPTED", "REJECTED", "SUPERSEDED"):
            return _err(1, "PROPOSAL_ALREADY_RESOLVED", f"Cannot refine proposal '{pid}' because its status is '{pred_dict.get('status')}'. Only PENDING proposals can be refined.")
        res = run_construct_session(session_mgr, sid, actor=principal, actor_role=role_tier, mode=str(getattr(args, "mode", "deterministic") or "deterministic").lower(), auto_commit=False, security_manager=session_mgr.security_manager, steering_prompt=steering)
        new_pid = res.get("proposal_id") if isinstance(res, dict) else getattr(res, "proposal_id", None)
        if isinstance(res, dict) and new_pid:
            res["replaces_proposal_id"] = pid
            new_pfile = sdir / "proposals" / f"{new_pid}.json"
            if new_pfile.is_file():
                _atomic_write_json(new_pfile, res)
        pred_dict.update({"status": "SUPERSEDED", "superseded_by": new_pid})
        _atomic_write_json(prop_res, pred_dict)
        return 0, res if isinstance(res, dict) else res.model_dump(mode="json")
    except OpenDVError as exc:
        return _err_from_opendv_error(exc)
