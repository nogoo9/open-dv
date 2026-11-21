"""Ontology Studio CLI delta validate and apply handlers."""

from __future__ import annotations

import argparse
import json
import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml
from pydantic import ValidationError

from opendv_cli.commands.ontology_studio_commit_helpers import (
    commit_delta_with_waiver_cli,
    compute_next_semver_or_err,
)
from opendv_cli.commands.ontology_studio_common import (
    _build_governance_validator,
    _build_signed_waiver_record,
    _err,
    _get_session_manager,
    _get_studio_root,
    _load_and_verify_waiver_credentials,
    _load_session_version_ontology,
    _normalize_delta_operation,
    _require_session_context,
    _resolve_studio_principal,
    _validate_session,
)
from opendv_contracts.dtos.ontology import OntologyDelta, StewardWaiverRecord, is_destructive_delta
from opendv_contracts.errors import OpenDVError
from opendv_core.models.graph_a import GraphAOntology
from opendv_core.ontology_hashing import compute_ontology_sha256
from opendv_service.ontology.applier import apply as apply_delta
from opendv_service.ontology.applier import compute_next_semver
from opendv_service.ontology.validator import OntologyGovernanceError, SemVerCalculusError
from opendv_service.studio.atomic_commit import (
    _load_grandfathered_concept_uris,
    parse_semver_tuple,
    verify_ont_evo_002_post_state_grounding,
)
from opendv_service.studio.session_manager import SessionNotFoundError
from opendv_service.studio.workspace_security import WorkspaceSecurityManager

if TYPE_CHECKING:
    from opendv_cli.executor import LocalExecutor


def handle_delta_validate(
    args: argparse.Namespace, executor: LocalExecutor | None = None
) -> tuple[int, Any]:
    """11. `opendv ontology studio delta validate` via `GovernanceValidator`."""
    auth_res = _resolve_studio_principal(args)
    if isinstance(auth_res[0], int):
        return auth_res  # type: ignore[return-value]
    principal, role_tier = auth_res  # type: ignore[misc]
    fpath_raw = getattr(args, "file", None) or getattr(args, "delta", None)
    if not fpath_raw:
        return _err(2, "SYNTAX_INVALID_ARGUMENT", "Missing required --file flag")
    fpath = Path(fpath_raw)
    if not fpath.is_absolute() and executor:
        fpath = (executor.workspace_root / fpath).resolve()
    if not fpath.is_file():
        return _err(3, "FILE_NOT_FOUND", f"File not found: {fpath_raw}")
    try:
        raw_delta_text = fpath.read_text(encoding="utf-8")
        delta_dict = (
            json.loads(raw_delta_text)
            if fpath.suffix.lower() == ".json"
            else yaml.safe_load(raw_delta_text)
        )
        if not isinstance(delta_dict, dict):
            return _err(2, "SYNTAX_INVALID_ARGUMENT", "Delta file must contain a JSON/YAML object")
    except (json.JSONDecodeError, yaml.YAMLError, OSError) as err_json:
        return _err(2, "SYNTAX_INVALID_ARGUMENT", f"Malformed delta file: {err_json}")
    sid = getattr(args, "session", None)
    base_file_arg = getattr(args, "base", None) or getattr(args, "base_file", None)
    base_ver_arg, base_sha_arg = (
        getattr(args, "base_version", None),
        getattr(args, "base_sha256", None),
    )
    meta, sdir, base_ontology, grandfathered = None, None, None, set()
    if sid:
        err = _validate_session(sid)
        if err:
            return err
        session_mgr = _get_session_manager(executor)
        try:
            meta = session_mgr.get_session(sid)
            sdir = session_mgr.get_session_dir(sid)
            grandfathered = set(_load_grandfathered_concept_uris(sdir))
        except SessionNotFoundError:
            return _err(3, "SESSION_NOT_FOUND", f"Session '{sid}' not found")
    elif not base_file_arg:
        return _err(
            2,
            "SYNTAX_INVALID_ARGUMENT",
            "Either --session or --base must be provided for delta validation.",
        )
    if base_file_arg:
        base_path = Path(base_file_arg)
        if not base_path.is_absolute() and executor:
            base_path = (executor.workspace_root / base_path).resolve()
        if not base_path.is_file():
            return _err(3, "FILE_NOT_FOUND", f"Base ontology file not found: '{base_file_arg}'")
        try:
            raw_text = base_path.read_text(encoding="utf-8")
            base_ontology = (
                GraphAOntology.model_validate_json(raw_text)
                if base_path.suffix.lower() == ".json"
                else GraphAOntology.model_validate(yaml.safe_load(raw_text))
            )
            base_ver, expected_parent_sha = (
                base_ontology.ontology_version,
                compute_ontology_sha256(base_ontology),
            )
        except (OSError, yaml.YAMLError, ValidationError, ValueError, TypeError) as exc:
            return _err(
                2,
                "SYNTAX_INVALID_ARGUMENT",
                f"Failed to parse base ontology file '{base_file_arg}': {exc}",
            )
    elif base_ver_arg and sdir and meta:
        base_ver = str(base_ver_arg).lstrip("v")
        base_ontology = _load_session_version_ontology(
            sdir,
            base_ver,
            domain_namespace=meta.domain_namespace,
            enterprise_owner=meta.enterprise_owner,
        )
        if base_ontology is None:
            return _err(3, "VERSION_NOT_FOUND", f"Base version artifact missing: '{base_ver_arg}'")
        expected_parent_sha = compute_ontology_sha256(base_ontology)
    elif sdir and meta:
        base_ver = meta.head_version
        base_ontology = _load_session_version_ontology(
            sdir,
            base_ver,
            domain_namespace=meta.domain_namespace,
            enterprise_owner=meta.enterprise_owner,
        )
        if base_ontology is None:
            return _err(1, "CORRUPT_SESSION_STATE", f"Head ontology artifact missing: '{base_ver}'")
        expected_parent_sha = meta.head_sha256
    else:
        return _err(
            2,
            "SYNTAX_INVALID_ARGUMENT",
            "Either --session or --base must be provided for delta validation.",
        )
    if base_sha_arg:
        clean_arg_sha = str(base_sha_arg).strip().strip('"').lower()
        if clean_arg_sha and clean_arg_sha != expected_parent_sha:
            return _err(
                5,
                "ONT_EVO_CONCURRENT_MODIFICATION",
                f"Base SHA mismatch: expected {expected_parent_sha}, got {clean_arg_sha}",
            )
    try:
        norm_ops = [_normalize_delta_operation(op) for op in delta_dict.get("operations", [])]
    except (ValidationError, ValueError, TypeError, KeyError) as exc:
        return 4, {
            "valid": False,
            "status": "ERROR",
            "error_code": "ONT_EVO_CONTRACT_VIOLATION",
            "detail": f"Malformed operation in delta: {exc}",
            "violations": [str(exc)],
            "diagnostics": [],
        }
    violations: list[str] = []
    parent_given = str(delta_dict.get("parent_sha256") or "").strip()
    if base_ver == "0.0.0":
        if parent_given not in ("", expected_parent_sha):
            violations.append(
                f"Genesis delta parent_sha256 '{parent_given}' must be empty or match genesis SHA."
            )
    elif parent_given and parent_given != expected_parent_sha:
        violations.append(
            f"parent_sha256 '{parent_given}' does not match expected base SHA "
            f"'{expected_parent_sha}' for base version '{base_ver}'"
        )
    temp_delta = OntologyDelta.model_construct(
        delta_id=delta_dict.get("delta_id", f"delta-{uuid.uuid4().hex[:8]}"),
        parent_sha256=expected_parent_sha,
        base_ontology_version=base_ver,
        target_ontology_version=None,
        operations=norm_ops,
    )
    computed_target_ver: str | None = None
    if norm_ops:
        try:
            computed_target_ver = compute_next_semver(
                base_version=base_ver, delta=temp_delta, base_ontology=base_ontology
            )
        except (ValueError, TypeError, KeyError, SemVerCalculusError, ValidationError) as exc:
            violations.append(f"SemVer bump computation failure: {exc}")
    req_tgt = delta_dict.get("target_version") or delta_dict.get("target_ontology_version")
    if (
        req_tgt
        and computed_target_ver
        and str(req_tgt).lstrip("v") != computed_target_ver.lstrip("v")
    ):
        violations.append(
            f"target_version '{req_tgt}' does not match required "
            f"semver bump '{computed_target_ver}'."
        )
    effective_tgt = str(req_tgt).lstrip("v") if req_tgt else computed_target_ver
    delta_obj: OntologyDelta | None = None
    try:
        delta_obj = OntologyDelta(
            delta_id=delta_dict.get("delta_id", f"delta-{uuid.uuid4().hex[:8]}"),
            parent_sha256=expected_parent_sha,
            base_version=base_ver,
            target_version=effective_tgt,
            operations=norm_ops,
            author=delta_dict.get("author", "validator"),
            rationale=delta_dict.get("rationale"),
        )
    except (ValidationError, ValueError, TypeError) as exc:
        violations.append(f"Delta structural validation failure: {exc}")
    sec_mgr = WorkspaceSecurityManager(studio_root=_get_studio_root(executor))
    waiver_rec: StewardWaiverRecord | None = None
    if delta_obj is not None and bool(getattr(args, "low_confidence_waiver", False)):
        w_res = _build_signed_waiver_record(
            args=args,
            delta_obj=delta_obj,
            base_ontology=base_ontology,
            base_sha256=expected_parent_sha,
            proposal_id=None,
            principal=principal,
            role_tier=role_tier,
            executor=executor,
            sec_mgr=sec_mgr,
        )
        if isinstance(w_res, tuple):
            return w_res
        waiver_rec = w_res
    error_code_out, exit_code_out = "ONT_EVO_CONTRACT_VIOLATION", 4
    enforce_grounding = (
        bool(sid)
        or bool(getattr(args, "verify_grounding", False))
        or bool(getattr(args, "low_confidence_waiver", False))
    )
    if not violations and delta_obj is not None and norm_ops:
        try:
            target_ontology = apply_delta(base_ontology, delta_obj, validate_structural=True)
            gv = _build_governance_validator(executor, sec_mgr, verify_grounding=enforce_grounding)
            waivers_list = [waiver_rec] if waiver_rec is not None else []
            gv.validate_deployed_contracts(base_ontology, delta_obj)
            if enforce_grounding:
                gv.validate(
                    base_ontology,
                    delta_obj,
                    target_ontology,
                    waivers=waivers_list,
                    grandfathered_concept_uris=grandfathered,
                )
                verify_ont_evo_002_post_state_grounding(
                    base_ontology,
                    delta_obj,
                    target_ontology,
                    waivers=waivers_list,
                    security_manager=sec_mgr,
                    grounding_verifier=gv.grounding_verifier,
                )
        except OntologyGovernanceError as gov_err:
            raw_c = getattr(gov_err, "error_code", "ONT_EVO_CONTRACT_VIOLATION")
            error_code_out = raw_c.value if hasattr(raw_c, "value") else str(raw_c)
            violations.append(str(gov_err))
        except (SemVerCalculusError, ValidationError, ValueError, OpenDVError) as apply_err:
            if isinstance(apply_err, OpenDVError):
                raw_c = getattr(apply_err, "error_code", "ONT_EVO_CONTRACT_VIOLATION")
                error_code_out = raw_c.value if hasattr(raw_c, "value") else str(raw_c)
                exit_code_out = int(getattr(apply_err, "exit_code", 4) or 4)
            violations.append(str(apply_err))
    if violations:
        return exit_code_out, {
            "valid": False,
            "status": "ERROR",
            "error_code": error_code_out,
            "detail": violations[0],
            "violations": violations,
            "diagnostics": [],
        }
    return 0, {
        "valid": True,
        "error_code": None,
        "violations": [],
        "diagnostics": [
            f"Validation complete: {len(norm_ops)} operations evaluated, 0 violations detected."
        ],
    }


def handle_delta_apply(
    args: argparse.Namespace, executor: LocalExecutor | None = None
) -> tuple[int, Any]:
    """12. `opendv ontology studio delta apply` via `AtomicCommitService`."""
    auth_res = _resolve_studio_principal(args)
    if isinstance(auth_res[0], int):
        return auth_res  # type: ignore[return-value]
    principal, role_tier = auth_res  # type: ignore[misc]
    if role_tier == "automated":
        return _err(
            7,
            "AGENT_OPERATION_FORBIDDEN",
            "Automated agent role is forbidden from applying ontology deltas directly.",
        )
    if role_tier == "viewer":
        return _err(
            7,
            "FORBIDDEN_ADMIN_REQUIRED",
            f"Role '{role_tier}' is not authorized to apply ontology deltas directly.",
        )
    sec_mgr = WorkspaceSecurityManager(studio_root=_get_studio_root(executor))
    if bool(getattr(args, "low_confidence_waiver", False)):
        cred_err = _load_and_verify_waiver_credentials(args, executor, sec_mgr, role_tier=role_tier)
        if cred_err is not None:
            return cred_err
    fpath_raw = getattr(args, "file", None) or getattr(args, "delta", None)
    if not fpath_raw:
        return _err(2, "SYNTAX_INVALID_ARGUMENT", "Missing required --file flag")
    fpath = Path(fpath_raw)
    if not fpath.is_absolute() and executor:
        fpath = (executor.workspace_root / fpath).resolve()
    if not fpath.is_file():
        return _err(3, "FILE_NOT_FOUND", f"File not found: {fpath_raw}")
    try:
        delta_dict = json.loads(fpath.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as err_json:
        return _err(2, "SYNTAX_INVALID_ARGUMENT", f"Malformed delta JSON: {err_json}")
    ctx = _require_session_context(args, executor)
    if isinstance(ctx[0], int):
        return ctx  # type: ignore[return-value]
    session_mgr, meta, sdir, sid = ctx  # type: ignore[misc]
    base_ver_arg = (
        getattr(args, "base_version", None)
        or delta_dict.get("base_version")
        or delta_dict.get("base_ontology_version")
    )
    if base_ver_arg and str(base_ver_arg).lstrip("v") != meta.head_version.lstrip("v"):
        return _err(
            4,
            "ONT_EVO_ENVELOPE_VIOLATION",
            f"Base version '{base_ver_arg}' does not match session head '{meta.head_version}'.",
        )
    base_sha = getattr(args, "base_sha256", None) or delta_dict.get("parent_sha256")
    is_genesis = meta.head_version in ("0.0.0", "", None)
    clean_base_sha = base_sha.strip('"') if isinstance(base_sha, str) else ""
    if not clean_base_sha and not is_genesis:
        return _err(
            2,
            "MISSING_PRECONDITION_HEADER",
            "Missing mandatory --base-sha256 precondition for delta apply.",
        )
    if clean_base_sha and clean_base_sha != meta.head_sha256:
        return _err(
            5,
            "ONT_EVO_CONCURRENT_MODIFICATION",
            f"Base SHA mismatch: expected {meta.head_sha256}, got {clean_base_sha}",
        )
    raw_ops = delta_dict.get("operations", [])
    if not raw_ops:
        return _err(1, "NO_OP_DELTA", "Cannot apply delta with zero operations.")
    try:
        norm_ops = [_normalize_delta_operation(op) for op in raw_ops]
    except (ValidationError, ValueError, TypeError, KeyError) as exc:
        return _err(4, "ONT_EVO_CONTRACT_VIOLATION", f"Invalid delta operation: {exc}")
    base_ont = _load_session_version_ontology(
        sdir,
        meta.head_version,
        domain_namespace=meta.domain_namespace,
        enterprise_owner=meta.enterprise_owner,
    )
    if base_ont is None:
        return _err(
            1, "CORRUPT_SESSION_STATE", f"Missing head ontology artifact '{meta.head_version}'"
        )
    temp_delta = OntologyDelta.model_construct(
        delta_id=f"delta-{uuid.uuid4().hex[:8]}",
        parent_sha256=meta.head_sha256,
        base_ontology_version=meta.head_version,
        target_ontology_version=None,
        operations=norm_ops,
    )
    target_ver_res = compute_next_semver_or_err(meta.head_version, temp_delta, base_ont)
    if isinstance(target_ver_res, tuple):
        return target_ver_res
    target_version = target_ver_res
    user_target = delta_dict.get("target_version") or delta_dict.get("target_ontology_version")
    if (
        user_target
        and isinstance(user_target, str)
        and user_target.lstrip("v") != target_version.lstrip("v")
    ):
        return _err(
            4,
            "ONT_EVO_CONTRACT_VIOLATION",
            f"User target_version '{user_target}' does not match required "
            f"semver bump '{target_version}'.",
        )
    try:
        delta_obj = OntologyDelta(
            delta_id=delta_dict.get("delta_id", f"delta-{uuid.uuid4().hex[:12]}"),
            parent_sha256=meta.head_sha256,
            base_version=meta.head_version,
            target_version=target_version,
            operations=norm_ops,
            author=principal,
            rationale=getattr(args, "rationale", None)
            or delta_dict.get("rationale")
            or "Direct delta apply",
        )
    except (ValidationError, ValueError, TypeError) as exc:
        return _err(4, "ONT_EVO_CONTRACT_VIOLATION", f"Delta construction failure: {exc}")
    is_major = (
        parse_semver_tuple(target_version)[0] > parse_semver_tuple(meta.head_version)[0]
        and meta.head_version != "0.0.0"
    )
    if (is_destructive_delta(delta_obj, base_ont) or is_major) and role_tier != "admin":
        return _err(
            7,
            "FORBIDDEN_ADMIN_REQUIRED",
            "Destructive (MAJOR SemVer) ontology revisions require the 'admin' role.",
        )
    eff_match = clean_base_sha if clean_base_sha else meta.head_sha256
    res = commit_delta_with_waiver_cli(
        args=args, session_mgr=session_mgr, sec_mgr=sec_mgr, executor=executor,
        sid=sid, delta_obj=delta_obj, base_ontology=base_ont,
        base_sha256=meta.head_sha256, principal=principal, role_tier=role_tier,
        if_match=eff_match, proposal_id=None,
    )
    if isinstance(res, tuple):
        return res
    return 0, {
        "session_id": sid,
        "target_version": res.version.lstrip("v"),
        "target_sha256": res.target_sha256,
        "parent_sha256": res.parent_sha256,
        "delta_id": delta_obj.delta_id,
        "bump_type": delta_obj.highest_semver_class(base_ont),
        "applied_operations_count": len(norm_ops),
    }
