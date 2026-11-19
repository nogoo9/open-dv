"""Shared helpers, principal resolution, path guards, and waiver signing for Studio CLI."""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import re
import secrets
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from opendv_contracts.dtos.ontology import OntologyDelta, StewardWaiverRecord
from opendv_contracts.errors import ErrorCode, OpenDVError
from opendv_contracts.spi.deployment import DeploymentStateProvider
from opendv_core.genesis import create_genesis_ontology
from opendv_core.models.graph_a import GraphAOntology
from opendv_service.ontology.matching.verifier import get_default_grounding_verifier
from opendv_service.ontology.validator import (
    GovernanceValidator,
    OntologyGovernanceError,
    build_workspace_deployment_provider,
)
from opendv_service.studio.atomic_commit import (
    _LocalWaiverVerifier,
    collect_touched_concept_uris,
)
from opendv_service.studio.delta_normalizer import (
    StudioWorkflowError,
    normalize_delta_operation_typed,
    normalize_external_mapping,
)
from opendv_service.studio.session_manager import (
    SessionMetadata,
    SessionNotFoundError,
    StudioSessionManager,
    validate_session_id,
)
from opendv_service.studio.session_workflows import derive_session_status
from opendv_service.studio.workspace_security import (
    DEFAULT_STUDIO_DIR,
    WorkspaceSecurityManager,
    validate_workspace_containment,
)

if TYPE_CHECKING:
    from opendv_cli.executor import LocalExecutor


class StudioLocalError(OpenDVError):
    """Typed local Studio execution error mapped to canonical `ErrorCode`."""

    def __init__(
        self,
        message: str,
        *,
        error_code: ErrorCode = ErrorCode.INTERNAL_ERROR,
        exit_code: int | None = None,
    ) -> None:
        super().__init__(message, error_code=error_code)
        if exit_code is not None:
            self.exit_code = exit_code


_PROPOSAL_ID_PATTERN: re.Pattern[str] = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
_VERSION_PATTERN: re.Pattern[str] = re.compile(r"^(?:v)?[0-9]+\.[0-9]+\.[0-9]+(?:-[a-zA-Z0-9_.-]+)?$")
_ADMIN_ROLES: frozenset[str] = frozenset({"ADMIN", "DATA_STEWARD", "RESTRICTED_PII_CUSTODIAN"})
_EDITOR_ROLES: frozenset[str] = frozenset({"EDITOR", "STEWARD"})
_AUTOMATED_ROLES: frozenset[str] = frozenset({"AUTOMATED", "INTERNAL_MODELER"})
_VIEWER_ROLES: frozenset[str] = frozenset({"VIEWER", "PUBLIC", "AUDITOR_FINANCE"})
MIN_WAIVER_RATIONALE_LENGTH: int = 10
MIN_PROMOTION_RATIONALE_LENGTH: int = 10
_normalize_external_mapping = normalize_external_mapping
_normalize_delta_operation = normalize_delta_operation_typed
_derive_session_status = derive_session_status


def _resolve_local_workspace_root(executor: LocalExecutor | None = None) -> Path:
    return Path((executor.workspace_root if executor else None) or os.environ.get("OPENDV_WORKSPACE_ROOT") or Path.cwd()).resolve()


def _get_studio_root(executor: LocalExecutor | None = None) -> Path:
    env_root = os.environ.get("OPENDV_STUDIO_ROOT")
    if env_root:
        return Path(env_root).resolve()
    return (_resolve_local_workspace_root(executor) / DEFAULT_STUDIO_DIR).resolve()


def _get_session_manager(executor: LocalExecutor | None = None) -> StudioSessionManager:
    return StudioSessionManager(studio_root=_get_studio_root(executor), workspace_root=_resolve_local_workspace_root(executor))


def _err(code: int, error_code: str, detail: str) -> tuple[int, dict[str, str]]:
    return code, {"status": "ERROR", "error_code": error_code, "detail": detail}


def wizard_exit_code_for_error(status_code: int | None, error_code: str, default_exit: int = 1) -> int:
    """Map Phase 2 wizard / dv-schema error status/codes to CLI exit codes 10/20/30/40."""
    code_up = str(error_code or "").upper()
    if status_code == 422 or code_up == "AGENT_NOT_CONFIGURED":
        return 10
    if status_code == 409 or code_up == "SESSION_JOB_ACTIVE":
        return 20
    if "PAUSED" in code_up:
        return 30
    if status_code == 400 or code_up in {
        "INVALID_REQUEST", "SYNTAX_INVALID_ARGUMENT", "CREDENTIALS_REF_INVALID",
        "PLAINTEXT_CREDENTIAL_FORBIDDEN", "INVALID_SESSION_IDENTIFIER", "UNSAFE_SQL_QUERY",
    }:
        return 40
    return default_exit


def _err_from_opendv_error(exc: OpenDVError, *, wizard_mode: bool = False) -> tuple[int, dict[str, Any]]:
    code = int(getattr(exc, "exit_code", 1) or 1)
    err_code_raw = getattr(exc, "error_code", ErrorCode.INTERNAL_ERROR)
    err_code = err_code_raw.value if hasattr(err_code_raw, "value") else str(err_code_raw)
    if wizard_mode:
        code = wizard_exit_code_for_error(getattr(exc, "http_status", None), err_code, code)
    payload: dict[str, Any] = {"status": "ERROR", "error_code": err_code, "detail": str(exc)}
    if getattr(exc, "details", None):
        payload["details"] = exc.details
    return code, payload


def _err_from_workflow_error(exc: StudioWorkflowError, *, wizard_mode: bool = False) -> tuple[int, dict[str, Any]]:
    http_st = getattr(exc, "http_status", None)
    err_code = getattr(exc, "code", None) or (exc.error_code.value if hasattr(exc.error_code, "value") else str(exc.error_code))
    code = wizard_exit_code_for_error(http_st, err_code, exc.exit_code) if wizard_mode else exc.exit_code
    payload: dict[str, Any] = {"status": "ERROR", "error_code": err_code, "detail": str(exc)}
    if getattr(exc, "details", None):
        payload.update(exc.details)
    return code, payload


def resolve_studio_principal(args: argparse.Namespace) -> tuple[int, dict[str, str]] | tuple[str, str]:
    """Resolve `(principal, role_tier)` from CLI `--role` / `OPENDV_ROLE` or standalone default."""
    explicit_flag = getattr(args, "_explicit_role", None)
    env_role = (os.environ.get("OPENDV_ROLE") or "").strip()
    raw_role = getattr(args, "role", None)
    role_token: str | None = None
    if isinstance(explicit_flag, str) and explicit_flag.strip():
        role_token = explicit_flag.strip()
    elif explicit_flag is True and raw_role is not None and str(raw_role).strip():
        role_token = str(raw_role).strip()
    elif env_role:
        role_token = env_role
    elif explicit_flag is not False and raw_role is not None and str(raw_role).strip() and str(raw_role).strip().upper() != "INTERNAL_MODELER":
        role_token = str(raw_role).strip()
    if not role_token:
        return "human:local_admin", "admin"
    upper = role_token.upper()
    if upper in _ADMIN_ROLES:
        return "human:local_admin", "admin"
    if upper in _EDITOR_ROLES:
        return "human:local_editor", "editor"
    if upper in _AUTOMATED_ROLES:
        return "agent:internal_modeler", "automated"
    if role_token.lower().startswith("agent:") and len(role_token) > 6:
        return role_token, "automated"
    if upper in _VIEWER_ROLES:
        return "human:local_viewer", "viewer"
    return _err(2, "SYNTAX_INVALID_ARGUMENT", f"Unsupported --role value: '{role_token}'")


_resolve_studio_principal = resolve_studio_principal


def _is_reparse_or_symlink(p: Path) -> bool:
    try:
        if p.is_symlink():
            return True
        if os.name == "nt" and p.exists():
            st = os.lstat(p)
            return bool(getattr(st, "st_file_attributes", 0) & 0x0400)
    except OSError:
        return True
    return False


def _resolve_configured_workspace_roots(base_ws: Path) -> list[Path] | None:
    env_roots = os.environ.get("OPENDV_ALLOWED_WORKSPACE_ROOTS") or os.environ.get("OPENDV_WORKSPACE_ROOTS")
    if not env_roots:
        return None
    roots: list[Path] = []
    for raw_item in env_roots.split(os.pathsep):
        raw_clean = raw_item.strip()
        if not raw_clean:
            continue
        p = Path(raw_clean)
        roots.append((p if p.is_absolute() else (base_ws / p)).resolve())
    return roots or None


def _validate_out_path(raw_out: str, executor: LocalExecutor | None = None) -> tuple[int, dict[str, str]] | Path:
    raw_str = str(raw_out)
    if not raw_str or not raw_str.strip():
        return _err(2, "PATH_CONTAINMENT_ERROR", "Output path cannot be empty.")
    if "\x00" in raw_str or any(ord(ch) < 32 or ord(ch) == 127 for ch in raw_str):
        return _err(2, "PATH_CONTAINMENT_ERROR", "Output path contains invalid control bytes.")
    base_ws = (executor.workspace_root if executor else Path.cwd()).resolve()
    base_real = Path(os.path.realpath(str(base_ws)))
    raw_p = Path(raw_str)
    candidate_unres = raw_p if raw_p.is_absolute() else (base_ws / raw_p)
    base_ancestors = {base_ws, *base_ws.parents, base_real, *base_real.parents}
    for anc in (candidate_unres, *candidate_unres.parents):
        if anc in base_ancestors:
            break
        if _is_reparse_or_symlink(anc):
            return _err(2, "PATH_CONTAINMENT_ERROR", f"Output path '{raw_out}' traverses a symlink or reparse point.")
    if _is_reparse_or_symlink(candidate_unres):
        return _err(2, "PATH_CONTAINMENT_ERROR", f"Output path '{raw_out}' cannot target a symlink or reparse point.")
    configured_roots = _resolve_configured_workspace_roots(base_ws)
    try:
        out_p = Path(os.path.realpath(str(candidate_unres)))
    except (OSError, ValueError) as exc:
        return _err(2, "PATH_CONTAINMENT_ERROR", f"Invalid output path '{raw_out}': {exc}")
    if not raw_p.is_absolute() and not out_p.is_relative_to(base_real):
        return _err(2, "PATH_CONTAINMENT_ERROR", f"Relative output path '{raw_out}' escapes workspace root.")
    if ".." in raw_p.parts:
        allowed_for_dotdot = configured_roots if configured_roots is not None else [base_real]
        if not any(out_p.is_relative_to(Path(os.path.realpath(str(r)))) for r in allowed_for_dotdot):
            return _err(2, "PATH_CONTAINMENT_ERROR", f"Path traversal '..' in output path '{raw_out}' escapes allowed workspace roots.")
    try:
        return validate_workspace_containment(candidate_unres, allowed_roots=configured_roots if configured_roots is not None else [out_p.parent], is_remote_request=False)
    except OpenDVError as exc:
        return _err(2, "PATH_CONTAINMENT_ERROR", str(exc))


def _atomic_write_text(target_path: Path, text: str) -> None:
    target_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = target_path.parent / f".{target_path.name}.{secrets.token_hex(4)}.tmp"
    try:
        tmp_path.write_text(text, encoding="utf-8")
        os.replace(tmp_path, target_path)
    except BaseException:
        with contextlib.suppress(OSError):
            if tmp_path.exists():
                tmp_path.unlink()
        raise


def _atomic_write_json(target_path: Path, payload: Any) -> None:
    _atomic_write_text(target_path, json.dumps(payload, indent=2, default=str))


def _validate_session(sid: str | None) -> tuple[int, Any] | None:
    if not sid:
        return _err(2, "SYNTAX_INVALID_ARGUMENT", "Missing required --session flag")
    try:
        validate_session_id(sid)
    except OpenDVError as exc:
        return _err(2, "INVALID_SESSION_IDENTIFIER", str(exc))
    return None


def _resolve_proposal_path(sdir: Path, pid: str | None) -> tuple[int, Any] | Path:
    if not pid or not _PROPOSAL_ID_PATTERN.match(str(pid)):
        return _err(2, "SYNTAX_INVALID_ARGUMENT", f"Invalid or missing proposal_id '{pid}'.")
    pdir = (sdir / "proposals").resolve()
    cand = (pdir / f"{pid}.json").resolve()
    if not cand.is_relative_to(pdir):
        return _err(2, "SYNTAX_INVALID_ARGUMENT", f"Proposal path traversal rejected: '{pid}'")
    return cand


def _require_session(session_mgr: StudioSessionManager, sid: str) -> tuple[int, Any] | SessionMetadata:
    try:
        return session_mgr.get_session(sid)
    except SessionNotFoundError:
        return _err(3, "SESSION_NOT_FOUND", f"Session '{sid}' not found")


def _require_session_context(args: argparse.Namespace, executor: LocalExecutor | None) -> tuple[int, Any] | tuple[StudioSessionManager, SessionMetadata, Path, str]:
    sid = getattr(args, "session", None) or getattr(args, "session_id", None)
    err = _validate_session(sid)
    if err:
        return err
    session_mgr = _get_session_manager(executor)
    meta_or_err = _require_session(session_mgr, str(sid))
    if isinstance(meta_or_err, tuple):
        return meta_or_err
    return session_mgr, meta_or_err, session_mgr.get_session_dir(str(sid)), str(sid)


def _build_workspace_deployment_provider(executor: LocalExecutor | None) -> DeploymentStateProvider | None:
    ws_root = _resolve_local_workspace_root(executor)
    try:
        return build_workspace_deployment_provider(ws_root)
    except OntologyGovernanceError as exc:
        if getattr(exc, "error_code", None) in (ErrorCode.INVALID_WORKSPACE_ONTOLOGY, "INVALID_WORKSPACE_ONTOLOGY"):
            raise StudioLocalError(str(exc), error_code=ErrorCode.INVALID_WORKSPACE_ONTOLOGY, exit_code=4) from exc
        raise


def _build_governance_validator(executor: LocalExecutor | None, sec_mgr: WorkspaceSecurityManager, *, verify_grounding: bool = True) -> GovernanceValidator:
    dep_prov = _build_workspace_deployment_provider(executor)
    return GovernanceValidator(deployment_provider=dep_prov, grounding_verifier=get_default_grounding_verifier() if verify_grounding else None, waiver_verifier=_LocalWaiverVerifier(sec_mgr), strict_verifier_required=verify_grounding)


def _load_and_verify_waiver_credentials(
    args: argparse.Namespace,
    executor: LocalExecutor | None,
    sec_mgr: WorkspaceSecurityManager,
    *,
    role_tier: str,
) -> tuple[int, Any] | None:
    """Verify admin role, `--authorized-role`, and rationale length (no K_waiver secret)."""
    _ = (executor, sec_mgr)
    if role_tier != "admin":
        return _err(7, "FORBIDDEN_ADMIN_REQUIRED", "Administrative grounding waivers strictly require the 'admin' role.")
    raw_auth_role = getattr(args, "authorized_role", None)
    if raw_auth_role is not None and str(raw_auth_role).strip() and str(raw_auth_role).strip().upper() not in _ADMIN_ROLES:
        return _err(7, "FORBIDDEN_ADMIN_REQUIRED", f"Waiver --authorized-role '{raw_auth_role}' is not authorized; 'admin' required.")
    rationale = getattr(args, "waiver_rationale", None) or getattr(args, "grounding_rationale", None)
    if not rationale or len(str(rationale).strip()) < 10:
        return _err(4, "INVALID_GROUNDING_WAIVER", "Low-confidence grounding waiver requires a rationale of at least 10 characters.")
    return None


def _build_signed_waiver_record(
    *, args: argparse.Namespace, delta_obj: OntologyDelta, base_ontology: GraphAOntology, base_sha256: str,
    proposal_id: str | None, principal: str, role_tier: str, executor: LocalExecutor | None, sec_mgr: WorkspaceSecurityManager,
) -> tuple[int, Any] | StewardWaiverRecord | None:
    if not bool(getattr(args, "low_confidence_waiver", False)):
        return None
    cred_err = _load_and_verify_waiver_credentials(args, executor, sec_mgr, role_tier=role_tier)
    if cred_err is not None:
        return cred_err
    waiver_rationale = getattr(args, "waiver_rationale", None) or getattr(args, "grounding_rationale", None)
    if not waiver_rationale or len(waiver_rationale.strip()) < 10:
        return _err(4, "INVALID_GROUNDING_WAIVER", "Administrative grounding waiver requires a rationale of at least 10 characters.")
    touched_uris = collect_touched_concept_uris(base_ontology, delta_obj)
    specified_scope = getattr(args, "ungrounded_concept", None) or []
    target_concept_uris = sorted({str(u) for u in specified_scope if str(u).strip()}) if specified_scope else (sorted(touched_uris) if touched_uris else ["urn:ontology:entity:exempted_concept"])
    if proposal_id:
        try:
            p_uuid = uuid.UUID(str(proposal_id))
            prop_uuid = p_uuid if p_uuid.version == 4 else uuid.UUID(bytes=hashlib.sha256(str(proposal_id).encode("utf-8")).digest()[:16], version=4)
        except (ValueError, TypeError):
            prop_uuid = uuid.UUID(bytes=hashlib.sha256(str(proposal_id).encode("utf-8")).digest()[:16], version=4)
    else:
        prop_uuid = uuid.uuid4()
    waiver_draft = StewardWaiverRecord(
        waiver_id=uuid.uuid4(), proposal_id=prop_uuid, proposal_delta_sha256=delta_obj.canonical_sha256(),
        target_concept_uris=target_concept_uris, base_sha256=base_sha256, rationale=waiver_rationale.strip(),
        authorized_by=str(getattr(args, "authorized_by", None) or principal), authorized_role="admin",
        signature_or_token="pending", created_at=datetime.now(UTC),
    )
    sig = sec_mgr.sign_waiver(waiver_draft.signing_payload().encode("utf-8"))
    return waiver_draft.model_copy(update={"signature_or_token": sig})


def _load_session_version_ontology(sdir: Path, version_str: str | None, *, domain_namespace: str = "general", enterprise_owner: str = "Enterprise Architecture") -> GraphAOntology | None:
    clean_ver = (version_str or "0.0.0").lstrip("v")
    vdir = sdir / "versions" / f"v{clean_ver}"
    for fname in ("ontology.json", "ontology.yaml"):
        ont_f = vdir / fname
        if ont_f.is_file():
            raw_text = ont_f.read_text(encoding="utf-8")
            return GraphAOntology.model_validate_json(raw_text) if ont_f.suffix == ".json" else GraphAOntology.model_validate(yaml.safe_load(raw_text))
    if clean_ver == "0.0.0":
        return create_genesis_ontology(domain_namespace=domain_namespace, enterprise_owner=enterprise_owner)
    return None


def _extract_proposal_operations(prop_data: dict[str, Any]) -> list[Any]:
    if isinstance(prop_data.get("operations"), list):
        return list(prop_data["operations"])
    delta_block = prop_data.get("delta")
    return list(delta_block["operations"]) if isinstance(delta_block, dict) and isinstance(delta_block.get("operations"), list) else []


def _extract_proposal_base_sha256(prop_data: dict[str, Any]) -> str:
    for key in ("base_sha256", "base_ontology_sha256"):
        if isinstance(prop_data.get(key), str):
            return str(prop_data[key])
    delta_block = prop_data.get("delta")
    return str(delta_block["parent_sha256"]) if isinstance(delta_block, dict) and isinstance(delta_block.get("parent_sha256"), str) else ""


def _extract_proposal_proposer(prop_data: dict[str, Any]) -> str:
    return str(prop_data.get("proposer") or prop_data.get("proposed_by") or "unknown")
