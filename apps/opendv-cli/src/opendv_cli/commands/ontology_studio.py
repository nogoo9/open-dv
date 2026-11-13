"""`opendv ontology studio` command handlers (Track 5C / Task 5C.1 / Issue #219).

In-process local executor for the Ontology Construction Studio subcommand groups
operating directly against `opendv-service` with zero network overhead.
Adheres strictly to AST isolation: zero imports from `opendv_platform_api` or web frameworks.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import hmac
import json
import os
import re
import secrets
import uuid
import zipfile
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml
from pydantic import TypeAdapter, ValidationError

from opendv_contracts.dtos.clarification import ClarificationAnswerItem
from opendv_contracts.dtos.ontology import (
    BaseOntologyDeltaOp,
    OntologyDelta,
    OntologyOperation,
    StewardWaiverRecord,
    is_destructive_delta,
)
from opendv_contracts.errors import ErrorCode, OpenDVError
from opendv_contracts.jcs import dumps_jcs
from opendv_contracts.spi.deployment import DeploymentStateProvider
from opendv_core.genesis import create_genesis_ontology
from opendv_core.models.graph_a import GraphAOntology
from opendv_core.ontology_hashing import compute_ontology_sha256
from opendv_service.construction.clarification_harness import EMPTY_ANSWERS_SHA256
from opendv_service.export.rdf_exporter import export_ontology
from opendv_service.ontology.applier import (
    apply as apply_delta,
)
from opendv_service.ontology.applier import (
    compute_next_semver,
    parse_semver,
)
from opendv_service.ontology.matching.verifier import get_default_grounding_verifier
from opendv_service.ontology.rebase import check_rebase_conflicts
from opendv_service.ontology.validator import (
    GovernanceValidator,
    OntologyGovernanceError,
    SemVerCalculusError,
    build_workspace_deployment_provider,
)
from opendv_service.studio.atomic_commit import (
    AtomicCommitService,
    CommitValidationError,
    ConcurrentModificationError,
    ForbiddenAdminRequiredError,
    HistoricalContractRationaleRequiredError,
    HistoricalContractWarningUnacknowledgedError,
    InvalidPreconditionHeaderError,
    LineageRootMismatchError,
    NoOpPromotionError,
    PromotionRationaleRequiredError,
    _load_grandfathered_concept_uris,
    _LocalWaiverVerifier,
    collect_touched_concept_uris,
    parse_semver_tuple,
    verify_ont_evo_002_post_state_grounding,
)
from opendv_service.studio.bundle import (
    AdminRoleRequiredError,
    ArchiveSecurityViolationError,
    ConfidentialSourceExportUnacknowledgedError,
    ExportRationaleRequiredError,
    compute_effective_session_taint,
    export_bundle,
    import_bundle,
)
from opendv_service.studio.job_runners import (
    OCRUnavailableError,
    OfficeConversionUnavailableError,
    UnsupportedFormatError,
    run_clarify_session,
    run_construct_session,
    run_parse_session_sources,
)
from opendv_service.studio.locks import (
    reentrant_session_lock,
)
from opendv_service.studio.session_manager import (
    CannotDeletePromotedSessionError,
    InvalidSessionIdentifierError,
    SessionAlreadyExistsError,
    SessionNotFoundError,
    StudioSessionManager,
    append_signed_audit_record,
    sanitize_domain_namespace,
    set_owner_only_permissions,
    validate_session_id,
)
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
_VERSION_PATTERN: re.Pattern[str] = re.compile(
    r"^(?:v)?[0-9]+\.[0-9]+\.[0-9]+(?:-[a-zA-Z0-9_.-]+)?$"
)
_OP_TYPE_ADAPTER: TypeAdapter[OntologyOperation] = TypeAdapter(OntologyOperation)

_ADMIN_ROLES: frozenset[str] = frozenset({"ADMIN", "DATA_STEWARD", "RESTRICTED_PII_CUSTODIAN"})
_EDITOR_ROLES: frozenset[str] = frozenset({"EDITOR", "STEWARD"})
_AUTOMATED_ROLES: frozenset[str] = frozenset({"AUTOMATED", "INTERNAL_MODELER"})
_VIEWER_ROLES: frozenset[str] = frozenset({"VIEWER", "PUBLIC", "AUDITOR_FINANCE"})


def _resolve_local_workspace_root(executor: LocalExecutor | None = None) -> Path:
    """Resolve active local workspace directory from executor, env, or cwd."""
    return Path(
        (executor.workspace_root if executor else None)
        or os.environ.get("OPENDV_WORKSPACE_ROOT")
        or Path.cwd()
    ).resolve()


def _get_studio_root(executor: LocalExecutor | None = None) -> Path:
    """Resolve active studio CAS root path."""
    env_root = os.environ.get("OPENDV_STUDIO_ROOT")
    if env_root:
        return Path(env_root).resolve()
    base_ws = _resolve_local_workspace_root(executor)
    return (base_ws / DEFAULT_STUDIO_DIR).resolve()


def _get_session_manager(executor: LocalExecutor | None = None) -> StudioSessionManager:
    """Resolve StudioSessionManager for local execution."""
    root = _get_studio_root(executor)
    return StudioSessionManager(
        studio_root=root,
        workspace_root=_resolve_local_workspace_root(executor),
    )


def _err(code: int, error_code: str, detail: str) -> tuple[int, dict[str, str]]:
    """Construct standard error envelope tuple."""
    return code, {
        "status": "ERROR",
        "error_code": error_code,
        "detail": detail,
    }


def _err_from_opendv_error(exc: OpenDVError) -> tuple[int, dict[str, Any]]:
    """Convert a typed OpenDVError into a standard CLI `(exit_code, payload)` tuple."""
    code = int(getattr(exc, "exit_code", 1) or 1)
    err_code_raw = getattr(exc, "error_code", ErrorCode.INTERNAL_ERROR)
    err_code = err_code_raw.value if hasattr(err_code_raw, "value") else str(err_code_raw)
    payload: dict[str, Any] = {
        "status": "ERROR",
        "error_code": err_code,
        "detail": str(exc),
    }
    if getattr(exc, "details", None):
        payload["details"] = exc.details
    return code, payload


def resolve_studio_principal(
    args: argparse.Namespace,
) -> tuple[int, dict[str, str]] | tuple[str, str]:
    """Resolve `(principal, role_tier)` from CLI `--role` / `OPENDV_ROLE` or standalone default.

    Precedence (P12a::MAJ-01, P13::MAJ-04):
    1. Explicit CLI `--role` (`args._explicit_role`).
    2. Non-empty `OPENDV_ROLE` environment variable (never shadowed by argparse defaults).
    3. Direct `args.role` when set to something other than the default `'INTERNAL_MODELER'`
       (and `_explicit_role` was not explicitly `False`).
    4. Local workstation default `("human:local_admin", "admin")`.
    """
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
    elif (
        explicit_flag is not False
        and raw_role is not None
        and str(raw_role).strip()
        and str(raw_role).strip().upper() != "INTERNAL_MODELER"
    ):
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


def _derive_session_status(sdir: Path, meta: Any) -> str:
    """Return `'PROMOTED'` when session has active promotion metadata, else `meta.status`."""
    if getattr(meta, "status", "") == "ACTIVE" and (
        (sdir / "promotion.json").is_file() or bool(getattr(meta, "promoted_versions", None))
    ):
        return "PROMOTED"
    return str(getattr(meta, "status", "ACTIVE"))


def _is_reparse_or_symlink(path: Path) -> bool:
    """Return True when `path` is a symlink or Windows NTFS junction/reparse point."""
    with contextlib.suppress(OSError):
        if path.is_symlink():
            return True
        is_junc = getattr(path, "is_junction", None)
        if callable(is_junc) and bool(is_junc()):
            return True
    return False


def _resolve_configured_workspace_roots(base_ws: Path) -> list[Path] | None:
    """Return explicitly configured `OPENDV_WORKSPACE_ROOTS` paths, or `None` if unset."""
    env_roots = os.environ.get("OPENDV_WORKSPACE_ROOTS", "").strip()
    if not env_roots:
        return None
    roots: list[Path] = []
    for item in env_roots.split(os.pathsep):
        cleaned = item.strip()
        if cleaned:
            p = Path(cleaned)
            roots.append(p.resolve() if p.is_absolute() else (base_ws / p).resolve())
    return roots or None


def _validate_out_path(
    raw_out: str, executor: LocalExecutor | None = None
) -> tuple[int, dict[str, str]] | Path:
    """Enforce symlink/reparse rejection and workspace containment on `--out` paths."""
    raw_str = str(raw_out)
    if not raw_str or not raw_str.strip():
        return _err(2, "PATH_CONTAINMENT_ERROR", "Output path cannot be empty.")
    if "\x00" in raw_str or any(ord(ch) < 32 or ord(ch) == 127 for ch in raw_str):
        return _err(2, "PATH_CONTAINMENT_ERROR", "Output path contains invalid control bytes.")

    base_ws = (executor.workspace_root if executor else Path.cwd()).resolve()
    base_real = Path(os.path.realpath(str(base_ws)))
    raw_p = Path(raw_str)
    candidate_unres = raw_p if raw_p.is_absolute() else (base_ws / raw_p)

    # Reject symlinks/reparse points on target and non-base parent components
    base_ancestors = {base_ws, *base_ws.parents, base_real, *base_real.parents}
    for anc in (candidate_unres, *candidate_unres.parents):
        if anc in base_ancestors:
            break
        if _is_reparse_or_symlink(anc):
            return _err(
                2,
                "PATH_CONTAINMENT_ERROR",
                f"Output path '{raw_out}' traverses a symlink or reparse point.",
            )

    if _is_reparse_or_symlink(candidate_unres):
        return _err(
            2,
            "PATH_CONTAINMENT_ERROR",
            f"Output path '{raw_out}' cannot target a symlink or reparse point.",
        )

    configured_roots = _resolve_configured_workspace_roots(base_ws)
    try:
        out_p = Path(os.path.realpath(str(candidate_unres)))
    except (OSError, ValueError) as exc:
        return _err(2, "PATH_CONTAINMENT_ERROR", f"Invalid output path '{raw_out}': {exc}")

    # Relative `--out` must never escape `base_ws` via `..`
    if not raw_p.is_absolute() and not out_p.is_relative_to(base_real):
        return _err(
            2,
            "PATH_CONTAINMENT_ERROR",
            f"Relative output path '{raw_out}' escapes workspace root.",
        )

    # Explicit `..` in absolute paths must remain inside allowed roots (or `base_ws`)
    if ".." in raw_p.parts:
        allowed_for_dotdot = configured_roots if configured_roots is not None else [base_real]
        if not any(
            out_p.is_relative_to(Path(os.path.realpath(str(r)))) for r in allowed_for_dotdot
        ):
            return _err(
                2,
                "PATH_CONTAINMENT_ERROR",
                f"Path traversal '..' in output path '{raw_out}' escapes allowed workspace roots.",
            )

    try:
        if configured_roots is not None:
            return validate_workspace_containment(
                candidate_unres,
                allowed_roots=configured_roots,
                is_remote_request=False,
            )
        return validate_workspace_containment(
            candidate_unres,
            allowed_roots=[out_p.parent],
            is_remote_request=False,
        )
    except OpenDVError as exc:
        return _err(2, "PATH_CONTAINMENT_ERROR", str(exc))


def _atomic_write_text(target_path: Path, text: str) -> None:
    """Atomically write UTF-8 text via temp file + `os.replace`."""
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


def _validate_session(sid: str | None) -> tuple[int, Any] | None:
    """Validate session identifier presence and structure."""
    if not sid:
        return _err(2, "SYNTAX_INVALID_ARGUMENT", "Missing required --session flag")
    try:
        validate_session_id(sid)
    except InvalidSessionIdentifierError as exc:
        return _err(2, "INVALID_SESSION_IDENTIFIER", str(exc))
    return None


def _resolve_proposal_path(sdir: Path, proposal_id: str | None) -> tuple[int, Any] | Path:
    """Validate proposal ID and return resolved file path strictly under proposals dir."""
    if not proposal_id or not _PROPOSAL_ID_PATTERN.match(str(proposal_id)):
        return _err(2, "SYNTAX_INVALID_ARGUMENT", f"Invalid proposal identifier: '{proposal_id}'")
    props_dir = (sdir / "proposals").resolve()
    target_file = (props_dir / f"{proposal_id}.json").resolve()
    if not target_file.is_relative_to(props_dir):
        return _err(
            2,
            "INVALID_WORKSPACE_PATH",
            f"Path traversal detected in proposal identifier: '{proposal_id}'",
        )
    return target_file


def _atomic_write_json(target_path: Path, data: Any, indent: int = 2) -> None:
    """Atomically write JSON data with 0600 permissions using temp-file and replace."""
    target_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = target_path.parent / f".{target_path.name}.{secrets.token_hex(4)}.tmp"
    tmp_path.write_text(json.dumps(data, indent=indent), encoding="utf-8")
    set_owner_only_permissions(tmp_path)
    os.replace(tmp_path, target_path)
    set_owner_only_permissions(target_path)


def _require_session(session_mgr: StudioSessionManager, sid: str) -> tuple[int, Any] | Any:
    """Retrieve session metadata or return exit 3 error tuple."""
    try:
        return session_mgr.get_session(sid)
    except SessionNotFoundError:
        return _err(3, "SESSION_NOT_FOUND", f"Session '{sid}' not found")


def _require_session_context(
    args: argparse.Namespace, executor: LocalExecutor | None = None
) -> tuple[int, dict[str, str]] | tuple[StudioSessionManager, Any, Path, str]:
    """Validate session ID and return `(session_mgr, meta, sdir, sid)` or error tuple."""
    sid = getattr(args, "session", None)
    err = _validate_session(sid)
    if err:
        return err
    session_mgr = _get_session_manager(executor)
    try:
        meta = session_mgr.get_session(sid)
        sdir = session_mgr.get_session_dir(sid)
        return session_mgr, meta, sdir, str(sid)
    except SessionNotFoundError:
        return _err(3, "SESSION_NOT_FOUND", f"Session '{sid}' not found")


def _normalize_external_mapping(m: dict[str, Any] | Any) -> Any:
    """Normalize legacy or heterogeneous external mapping dicts into ExternalMapping schema."""
    if not isinstance(m, dict):
        return m
    norm = dict(m)
    scheme = (
        norm.get("scheme")
        or norm.pop("source_system", None)
        or norm.pop("source_standard", None)
        or norm.pop("standard_name", None)
    )
    if scheme:
        norm["scheme"] = str(scheme)
    target = (
        norm.get("target_uri")
        or norm.pop("standard_uri", None)
        or norm.pop("external_id", None)
        or norm.pop("external_term", None)
    )
    if target:
        norm["target_uri"] = str(target)

    conf = norm.get("confidence")
    if conf is None:
        conf = norm.pop("confidence_score", None)
    if conf is None:
        conf = norm.pop("mapping_confidence", None)
    if conf is not None:
        with contextlib.suppress(ValueError, TypeError):
            norm["confidence"] = float(conf)

    pred = norm.get("predicate") or norm.pop("mapping_type", None)
    if pred:
        norm["predicate"] = str(pred)
    return norm


def _normalize_delta_operation(op: dict[str, Any] | Any) -> OntologyOperation:
    """Instantiate typed `OntologyOperation` from JSON dictionary."""
    if isinstance(op, BaseOntologyDeltaOp):
        return _OP_TYPE_ADAPTER.validate_python(op)
    if not isinstance(op, dict):
        return _OP_TYPE_ADAPTER.validate_python(op)
    op_copy = dict(op)
    raw_type = (
        op_copy.pop("op_type", None) or op_copy.pop("op", None) or op_copy.pop("operation", None)
    )
    if not raw_type:
        raise ValueError("Delta operation missing required 'op_type'")

    op_copy.pop("op", None)
    op_copy.pop("operation", None)

    op_type = str(raw_type)
    if op_type.endswith("Op") and len(op_type) > 2:
        op_type = op_type[:-2]
    op_type = re.sub(r"(?<!^)(?=[A-Z])", "_", op_type).lower()

    if not op_copy.get("op_id"):
        op_bytes = json.dumps(op_copy, sort_keys=True).encode("utf-8")
        op_copy["op_id"] = f"op-{hashlib.sha256(op_bytes).hexdigest()[:12]}"

    if "external_mappings" in op_copy and isinstance(op_copy["external_mappings"], list):
        op_copy["external_mappings"] = [
            _normalize_external_mapping(m) for m in op_copy["external_mappings"]
        ]

    if op_type == "add_concept":
        op_copy["op_type"] = "add_concept"
        if "concept" in op_copy and isinstance(op_copy["concept"], dict):
            c_dict = dict(op_copy.pop("concept"))
            if "external_mappings" in c_dict and isinstance(c_dict["external_mappings"], list):
                c_dict["external_mappings"] = [
                    _normalize_external_mapping(m) for m in c_dict["external_mappings"]
                ]
            norm: dict[str, Any] = {
                "op_type": "add_concept",
                "op_id": op_copy["op_id"],
            }
            norm.update(c_dict)
            norm.update(op_copy)
            norm["op_type"] = "add_concept"
            return _OP_TYPE_ADAPTER.validate_python(norm)
        return _OP_TYPE_ADAPTER.validate_python(op_copy)

    op_copy["op_type"] = op_type
    return _OP_TYPE_ADAPTER.validate_python(op_copy)


def _build_workspace_deployment_provider(
    executor: LocalExecutor | None,
) -> DeploymentStateProvider | None:
    """Recursively inspect active workspace for deployed Data Vault contracts (ONT-EVO-001)."""
    ws_root = _resolve_local_workspace_root(executor)
    try:
        return build_workspace_deployment_provider(ws_root)
    except OntologyGovernanceError as exc:
        if getattr(exc, "error_code", None) in (
            ErrorCode.INVALID_WORKSPACE_ONTOLOGY,
            "INVALID_WORKSPACE_ONTOLOGY",
        ):
            raise StudioLocalError(
                str(exc),
                error_code=ErrorCode.INVALID_WORKSPACE_ONTOLOGY,
                exit_code=4,
            ) from exc
        raise


def _build_governance_validator(
    executor: LocalExecutor | None,
    sec_mgr: WorkspaceSecurityManager,
    *,
    verify_grounding: bool = True,
) -> GovernanceValidator:
    """Build service-layer `GovernanceValidator` enforcing ONT-EVO-001 & ONT-EVO-002."""
    dep_prov = _build_workspace_deployment_provider(executor)
    grounding_ver = get_default_grounding_verifier() if verify_grounding else None
    return GovernanceValidator(
        deployment_provider=dep_prov,
        grounding_verifier=grounding_ver,
        waiver_verifier=_LocalWaiverVerifier(sec_mgr),
        strict_verifier_required=verify_grounding,
    )


def _load_and_verify_waiver_credentials(
    args: argparse.Namespace,
    executor: LocalExecutor | None,
    sec_mgr: WorkspaceSecurityManager,
    *,
    role_tier: str,
) -> tuple[int, Any] | None:
    """Verify admin role, optional `--authorized-role`, and cryptographic waiver key.

    Precedence (P13::MAJ-01, MAJ-03, P12::MAJ-04):
    1. Authenticated role MUST be `admin` -> exit 7 `FORBIDDEN_ADMIN_REQUIRED` (before rationale).
    2. If `--authorized-role` is passed, it MUST bind to `admin` -> exit 7.
    3. If `--waiver-key-file` is passed and missing -> exit 3 `FILE_NOT_FOUND` (not 403/7!).
    4. Waiver key (`--waiver-key-file` or `OPENDV_WAIVER_KEY`) MUST match workspace K_waiver
       -> exit 7 `FORBIDDEN_ADMIN_REQUIRED`.
    """
    if role_tier != "admin":
        return _err(
            7,
            "FORBIDDEN_ADMIN_REQUIRED",
            "Administrative grounding waivers strictly require the 'admin' role.",
        )

    raw_auth_role = getattr(args, "authorized_role", None)
    if (
        raw_auth_role is not None
        and str(raw_auth_role).strip()
        and str(raw_auth_role).strip().upper() not in _ADMIN_ROLES
    ):
        return _err(
            7,
            "FORBIDDEN_ADMIN_REQUIRED",
            f"Waiver --authorized-role '{raw_auth_role}' is not authorized; 'admin' required.",
        )

    waiver_key = ""
    waiver_key_file = getattr(args, "waiver_key_file", None)
    waiver_key_env = os.environ.get("OPENDV_WAIVER_KEY")
    if waiver_key_file:
        key_path = Path(waiver_key_file)
        if not key_path.is_absolute() and executor:
            key_path = (executor.workspace_root / key_path).resolve()
        if not key_path.is_file():
            return _err(
                3,
                "FILE_NOT_FOUND",
                f"Waiver key file not found: {waiver_key_file}",
            )
        try:
            waiver_key = key_path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            return _err(
                2,
                "SYNTAX_INVALID_ARGUMENT",
                f"Cannot read waiver key file '{waiver_key_file}': {exc}",
            )
    elif waiver_key_env:
        waiver_key = waiver_key_env.strip()

    if not waiver_key:
        return _err(
            7,
            "FORBIDDEN_ADMIN_REQUIRED",
            "Administrative waivers strictly require authenticated waiver credentials.",
        )

    rationale = getattr(args, "waiver_rationale", None) or getattr(
        args, "grounding_rationale", None
    )
    if not rationale or len(str(rationale).strip()) < 10:
        return _err(
            4,
            "INVALID_GROUNDING_WAIVER",
            "Low-confidence grounding waiver requires a rationale of at least 10 characters.",
        )

    expected_waiver_bytes = sec_mgr.get_waiver_key()
    expected_waiver_hex = expected_waiver_bytes.hex().lower()
    key_clean = waiver_key.strip().lower()

    try:
        key_bytes = bytes.fromhex(key_clean)
    except ValueError:
        key_bytes = waiver_key.strip().encode("utf-8")

    is_valid_key = hmac.compare_digest(key_clean, expected_waiver_hex) or hmac.compare_digest(
        key_bytes, expected_waiver_bytes
    )
    if not is_valid_key:
        return _err(7, "FORBIDDEN_ADMIN_REQUIRED", "Invalid steward waiver credentials.")
    return None


def _build_signed_waiver_record(
    *,
    args: argparse.Namespace,
    delta_obj: OntologyDelta,
    base_ontology: GraphAOntology,
    base_sha256: str,
    proposal_id: str | None,
    principal: str,
    role_tier: str,
    executor: LocalExecutor | None,
    sec_mgr: WorkspaceSecurityManager,
) -> tuple[int, Any] | StewardWaiverRecord | None:
    """Construct and HMAC-sign a `StewardWaiverRecord` when `--low-confidence-waiver` is set."""
    if not bool(getattr(args, "low_confidence_waiver", False)):
        return None

    cred_err = _load_and_verify_waiver_credentials(args, executor, sec_mgr, role_tier=role_tier)
    if cred_err is not None:
        return cred_err

    waiver_rationale = getattr(args, "waiver_rationale", None) or getattr(
        args, "grounding_rationale", None
    )
    if not waiver_rationale or len(waiver_rationale.strip()) < 10:
        return _err(
            4,
            "INVALID_GROUNDING_WAIVER",
            "Administrative grounding waiver requires a rationale of at least 10 characters.",
        )

    touched_uris = collect_touched_concept_uris(base_ontology, delta_obj)
    specified_scope = getattr(args, "ungrounded_concept", None) or []
    if specified_scope:
        target_concept_uris = sorted({str(u) for u in specified_scope if str(u).strip()})
    elif touched_uris:
        target_concept_uris = sorted(touched_uris)
    else:
        target_concept_uris = ["urn:ontology:entity:exempted_concept"]

    if proposal_id:
        try:
            p_uuid = uuid.UUID(str(proposal_id))
            prop_uuid = (
                p_uuid
                if p_uuid.version == 4
                else uuid.UUID(
                    bytes=hashlib.sha256(str(proposal_id).encode("utf-8")).digest()[:16],
                    version=4,
                )
            )
        except (ValueError, TypeError):
            prop_uuid = uuid.UUID(
                bytes=hashlib.sha256(str(proposal_id).encode("utf-8")).digest()[:16],
                version=4,
            )
    else:
        prop_uuid = uuid.uuid4()

    auth_by = str(getattr(args, "authorized_by", None) or principal)
    waiver_draft = StewardWaiverRecord(
        waiver_id=uuid.uuid4(),
        proposal_id=prop_uuid,
        proposal_delta_sha256=delta_obj.canonical_sha256(),
        target_concept_uris=target_concept_uris,
        base_sha256=base_sha256,
        rationale=waiver_rationale.strip(),
        authorized_by=auth_by,
        authorized_role="admin",
        signature_or_token="pending",
        created_at=datetime.now(UTC),
    )
    sig = sec_mgr.sign_waiver(waiver_draft.signing_payload().encode("utf-8"))
    return waiver_draft.model_copy(update={"signature_or_token": sig})


def _load_session_version_ontology(
    sdir: Path,
    version_str: str | None,
    *,
    domain_namespace: str = "general",
    enterprise_owner: str = "Enterprise Architecture",
) -> GraphAOntology | None:
    """Load `GraphAOntology` for a session version (including `0.0.0` genesis)."""
    clean_ver = (version_str or "0.0.0").lstrip("v")
    vdir = sdir / "versions" / f"v{clean_ver}"
    for fname in ("ontology.json", "ontology.yaml"):
        ont_f = vdir / fname
        if ont_f.is_file():
            raw_text = ont_f.read_text(encoding="utf-8")
            if ont_f.suffix == ".json":
                return GraphAOntology.model_validate_json(raw_text)
            return GraphAOntology.model_validate(yaml.safe_load(raw_text))
    if clean_ver == "0.0.0":
        return create_genesis_ontology(
            domain_namespace=domain_namespace,
            enterprise_owner=enterprise_owner,
        )
    return None


def _extract_proposal_operations(prop_data: dict[str, Any]) -> list[Any]:
    """Extract raw operations from flat `operations` or nested `delta.operations`."""
    if isinstance(prop_data.get("operations"), list):
        return list(prop_data["operations"])
    delta_block = prop_data.get("delta")
    if isinstance(delta_block, dict) and isinstance(delta_block.get("operations"), list):
        return list(delta_block["operations"])
    return []


def _extract_proposal_base_sha256(prop_data: dict[str, Any]) -> str:
    """Extract base SHA-256 from flat or `OntologyProposal` nested fields."""
    for key in ("base_sha256", "base_ontology_sha256"):
        val = prop_data.get(key)
        if isinstance(val, str):
            return val
    delta_block = prop_data.get("delta")
    if isinstance(delta_block, dict) and isinstance(delta_block.get("parent_sha256"), str):
        return str(delta_block["parent_sha256"])
    return ""


def _extract_proposal_proposer(prop_data: dict[str, Any]) -> str:
    """Extract proposer attribution from flat `proposer` or `OntologyProposal.proposed_by`."""
    return str(prop_data.get("proposer") or prop_data.get("proposed_by") or "unknown")


# ---------------------------------------------------------------------------
# Command Handlers
# ---------------------------------------------------------------------------


def handle_init(args: argparse.Namespace, executor: LocalExecutor | None = None) -> tuple[int, Any]:
    """1. `opendv ontology studio init [--force] [--show-token]`."""
    auth_res = _resolve_studio_principal(args)
    if isinstance(auth_res[0], int):
        return auth_res  # type: ignore[return-value]
    _, role_tier = auth_res  # type: ignore[misc]
    if role_tier != "admin":
        return _err(
            7,
            "FORBIDDEN_ADMIN_REQUIRED",
            "Initializing or rotating local Studio secrets requires the 'admin' role.",
        )

    studio_root = _get_studio_root(executor)
    secret_file = studio_root / ".odv_workspace_secret"
    token_file = studio_root / ".local_token"
    force = bool(getattr(args, "force", False))

    if secret_file.exists() and not force:
        return _err(
            6,
            "STUDIO_ALREADY_INITIALIZED",
            "Studio workspace already initialized. Use --force to rotate.",
        )

    studio_root.mkdir(parents=True, exist_ok=True)
    set_owner_only_permissions(studio_root)

    if force:
        with contextlib.suppress(OSError):
            if secret_file.exists():
                secret_file.unlink()

    WorkspaceSecurityManager(studio_root=studio_root)

    token = secrets.token_hex(32)
    tmp_token = studio_root / f".token.{uuid.uuid4().hex[:8]}.tmp"
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_BINARY", 0)
    fd = os.open(str(tmp_token), flags, 0o600)
    try:
        os.write(fd, token.encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
    set_owner_only_permissions(tmp_token)
    os.replace(tmp_token, token_file)
    set_owner_only_permissions(token_file)

    res_payload: dict[str, Any] = {
        "status": "initialized",
        "token_path": str(token_file),
        "principal": "human:local_admin",
        "studio_root": str(studio_root),
    }
    if getattr(args, "show_token", False):
        res_payload["local_token"] = token
    return 0, res_payload


def handle_create(
    args: argparse.Namespace, executor: LocalExecutor | None = None
) -> tuple[int, Any]:
    """2. `opendv ontology studio create`."""
    auth_res = _resolve_studio_principal(args)
    if isinstance(auth_res[0], int):
        return auth_res  # type: ignore[return-value]
    _principal, role_tier = auth_res  # type: ignore[misc]
    if role_tier == "viewer":
        return _err(
            7,
            "FORBIDDEN_ADMIN_REQUIRED",
            "Read-only viewer role is not permitted to create Studio sessions.",
        )

    sid = getattr(args, "session", None)
    if sid:
        err = _validate_session(sid)
        if err:
            return err

    raw_ns = getattr(args, "domain_namespace", None)
    if not raw_ns or (
        getattr(args, "_explicit_domain_namespace", None) is not True and raw_ns == "default"
    ):
        domain_ns = "general"
    else:
        domain_ns = str(raw_ns)

    raw_owner = getattr(args, "enterprise_owner", None)
    if not raw_owner or (
        getattr(args, "_explicit_enterprise_owner", None) is not True and raw_owner == "Enterprise"
    ):
        owner = "Enterprise Architecture"
    else:
        owner = str(raw_owner)

    from_ws = getattr(args, "from_workspace", None)
    initial_ontology: GraphAOntology | None = None

    if from_ws:
        ws_path = Path(from_ws)
        if not ws_path.is_absolute():
            base = executor.workspace_root if executor else Path.cwd()
            ws_path = (base / ws_path).resolve()
        if not ws_path.exists():
            return _err(2, "INVALID_WORKSPACE_PATH", f"Workspace directory not found: {from_ws}")

        found_yaml: Path | None = None
        if ws_path.is_file():
            found_yaml = ws_path
        else:
            ns_slug = sanitize_domain_namespace(domain_ns)
            for candidate in [
                ws_path / f"{ns_slug}_graph_a_ontology.yaml",
                ws_path / "graph_a_ontology.yaml",
                ws_path / "ontology.yaml",
                ws_path / "pe_graph_a_ontology.yaml",
                *sorted(ws_path.glob("*_graph_a_ontology.yaml")),
            ]:
                if candidate.is_file():
                    found_yaml = candidate
                    break

        if not found_yaml:
            return _err(
                4,
                "INVALID_WORKSPACE_ONTOLOGY",
                f"No Graph A ontology contract found in '{from_ws}' "
                "(expected graph_a_ontology.yaml or *_graph_a_ontology.yaml)",
            )

        try:
            raw_ont = yaml.safe_load(found_yaml.read_text(encoding="utf-8"))
            initial_ontology = GraphAOntology.model_validate(raw_ont)
        except (yaml.YAMLError, ValidationError, ValueError, OSError) as err_parse:
            return _err(
                4,
                "INVALID_WORKSPACE_ONTOLOGY",
                f"Failed to parse ontology in workspace: {err_parse}",
            )

    session_mgr = _get_session_manager(executor)

    try:
        meta = session_mgr.create_session(
            session_id=sid,
            initial_ontology=initial_ontology,
            domain_namespace=domain_ns,
            enterprise_owner=owner,
        )
    except SessionAlreadyExistsError as err_exists:
        return _err(6, "SESSION_ALREADY_EXISTS", str(err_exists))
    except InvalidSessionIdentifierError as err_id:
        return _err(2, "INVALID_SESSION_IDENTIFIER", str(err_id))

    sdir = session_mgr.get_session_dir(meta.session_id)
    return 0, {
        "session_id": meta.session_id,
        "domain_namespace": meta.domain_namespace,
        "enterprise_owner": meta.enterprise_owner,
        "status": _derive_session_status(sdir, meta),
        "active_version": meta.head_version,
        "head_sha256": meta.head_sha256,
        "created_at": meta.created_at,
        "updated_at": meta.updated_at,
    }


def handle_delete(
    args: argparse.Namespace, executor: LocalExecutor | None = None
) -> tuple[int, Any]:
    """3. `opendv ontology studio delete -s <session> [--force]`."""
    auth_res = _resolve_studio_principal(args)
    if isinstance(auth_res[0], int):
        return auth_res  # type: ignore[return-value]
    _, role_tier = auth_res  # type: ignore[misc]
    if role_tier != "admin":
        return _err(
            7,
            "FORBIDDEN_ADMIN_REQUIRED",
            "Deleting Studio sessions requires the 'admin' role.",
        )

    sid = getattr(args, "session", None)
    err = _validate_session(sid)
    if err:
        return err

    force = bool(getattr(args, "force", False))
    session_mgr = _get_session_manager(executor)
    try:
        session_mgr.delete_session(str(sid), force=force)
    except SessionNotFoundError:
        return _err(3, "SESSION_NOT_FOUND", f"Session '{sid}' not found")
    except CannotDeletePromotedSessionError as exc:
        return _err(6, "CANNOT_DELETE_PROMOTED_SESSION", str(exc))
    except OpenDVError as exc:
        return _err_from_opendv_error(exc)

    return 0, {
        "session_id": str(sid),
        "status": "DELETED",
        "deleted": True,
    }


def handle_list(args: argparse.Namespace, executor: LocalExecutor | None = None) -> tuple[int, Any]:
    """4. `opendv ontology studio list`."""
    auth_res = _resolve_studio_principal(args)
    if isinstance(auth_res[0], int):
        return auth_res  # type: ignore[return-value]

    session_mgr = _get_session_manager(executor)
    sessions = session_mgr.list_sessions()
    enriched = [
        (s, _derive_session_status(session_mgr.get_session_dir(s.session_id), s)) for s in sessions
    ]
    status_filter = getattr(args, "status", None)
    if status_filter:
        target_st = str(status_filter).upper()
        enriched = [
            (s, eff_st)
            for s, eff_st in enriched
            if eff_st.upper() == target_st or s.status.upper() == target_st
        ]

    offset = getattr(args, "offset", 0) or 0
    limit = getattr(args, "limit", 50) or 50
    paged = enriched[offset : offset + limit]

    return 0, [
        {
            "session_id": s.session_id,
            "domain_namespace": s.domain_namespace,
            "enterprise_owner": s.enterprise_owner,
            "status": eff_st,
            "active_version": s.head_version,
            "created_at": s.created_at,
            "updated_at": s.updated_at,
        }
        for s, eff_st in paged
    ]


def handle_status(
    args: argparse.Namespace, executor: LocalExecutor | None = None
) -> tuple[int, Any]:
    """5. `opendv ontology studio status -s <session>`."""
    auth_res = _resolve_studio_principal(args)
    if isinstance(auth_res[0], int):
        return auth_res  # type: ignore[return-value]

    ctx = _require_session_context(args, executor)
    if isinstance(ctx[0], int):
        return ctx  # type: ignore[return-value]
    _, meta, sdir, _ = ctx  # type: ignore[misc]

    return 0, {
        "session_id": meta.session_id,
        "status": _derive_session_status(sdir, meta),
        "active_version": meta.head_version,
        "head_sha256": meta.head_sha256,
        "taint_classification": meta.taint_classification,
        "active_jobs": 0,
    }


def handle_upload(
    args: argparse.Namespace, executor: LocalExecutor | None = None
) -> tuple[int, Any]:
    """6. `opendv ontology studio upload -s <session> -f <file>` (P12::MAJ-03 sticky taint)."""
    auth_res = _resolve_studio_principal(args)
    if isinstance(auth_res[0], int):
        return auth_res  # type: ignore[return-value]
    _, role_tier = auth_res  # type: ignore[misc]
    if role_tier == "viewer":
        return _err(
            7,
            "FORBIDDEN_ADMIN_REQUIRED",
            "Read-only viewer role is not permitted to upload sources.",
        )

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

    requested_do_not_send = bool(
        getattr(args, "do_not_send_to_agent", False) or getattr(args, "confidential", False)
    )
    raw_description = getattr(args, "description", None)

    lock_file = sdir / ".session.lock"
    with reentrant_session_lock(lock_file, session_id=sid):
        cas = session_mgr.get_cas(sid)
        cas.put(content)
        manifest_file = sdir / "inputs" / "manifest.json"
        manifest: dict[str, Any] = {"session_id": sid, "files": [], "sources": []}
        if manifest_file.exists():
            try:
                manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as err_man:
                return _err(
                    1, "CORRUPT_SESSION_STATE", f"Failed to parse inputs manifest.json: {err_man}"
                )

        existing = manifest.get("files", []) or manifest.get("sources", [])
        prev_entry = next(
            (
                item
                for item in existing
                if isinstance(item, dict)
                and (item.get("sha256") == sha256_hex or item.get("source_sha256") == sha256_hex)
            ),
            None,
        )

        if (
            role_tier == "automated"
            and isinstance(prev_entry, dict)
            and not prev_entry.get("deleted")
            and str(prev_entry.get("status", "")).upper() != "DELETED"
            and raw_description is not None
            and raw_description != prev_entry.get("description")
        ):
            return _err(
                7,
                "AGENT_METADATA_OVERWRITE_FORBIDDEN",
                "Automated actors cannot overwrite existing human-authored source metadata.",
            )

        # Sticky taint: re-uploading without --confidential MUST NEVER downgrade prior taint!
        prev_taint = bool(
            prev_entry
            and (
                prev_entry.get("do_not_send_to_agent") is True
                or str(prev_entry.get("taint_classification", "")).upper()
                in ("CONFIDENTIAL", "TAINTED", "DO_NOT_SEND")
            )
        )
        effective_do_not_send = prev_taint or requested_do_not_send
        effective_description = (
            raw_description
            if raw_description is not None
            else (prev_entry.get("description") if isinstance(prev_entry, dict) else None)
        )

        prev_sid = str(prev_entry.get("source_id") or "") if isinstance(prev_entry, dict) else ""
        source_id = prev_sid if prev_sid.startswith("src-") else f"src-{sha256_hex[:16]}"
        now_iso = datetime.now(UTC).isoformat()

        files = [
            f
            for f in existing
            if isinstance(f, dict)
            and f.get("sha256") != sha256_hex
            and f.get("source_sha256") != sha256_hex
        ]
        source_entry: dict[str, Any] = {
            "source_id": source_id,
            "source_sha256": sha256_hex,
            "file_name": fpath.name,
            "filename": fpath.name,
            "sha256": sha256_hex,
            "size_bytes": len(content),
            "byte_count": len(content),
            "mime_type": "application/octet-stream",
            "do_not_send_to_agent": effective_do_not_send,
            "taint_classification": "CONFIDENTIAL" if effective_do_not_send else "CLEAN",
            "uploaded_at": now_iso,
        }
        if effective_description is not None:
            source_entry["description"] = str(effective_description)

        files.append(source_entry)
        manifest["files"] = files
        manifest["sources"] = files
        manifest["total_bytes"] = sum(f.get("size_bytes", 0) for f in files)
        _atomic_write_json(manifest_file, manifest)

        # Synchronize taint_index.json & session.json sticky taint state
        taint_index_path = sdir / "taint_index.json"
        tainted_ids = sorted(
            {
                str(f.get("source_id") or f.get("sha256"))
                for f in files
                if f.get("do_not_send_to_agent") is True
                or str(f.get("taint_classification", "")).upper()
                in ("CONFIDENTIAL", "TAINTED", "DO_NOT_SEND")
            }
        )
        tainted_shas = sorted(
            {
                str(f.get("sha256") or f.get("source_sha256"))
                for f in files
                if f.get("do_not_send_to_agent") is True
                or str(f.get("taint_classification", "")).upper()
                in ("CONFIDENTIAL", "TAINTED", "DO_NOT_SEND")
            }
        )
        taint_index_data: dict[str, Any] = {
            "session_id": sid,
            "is_tainted": bool(tainted_ids),
            "tainted_sources": tainted_ids,
            "tainted_source_ids": tainted_ids,
            "tainted_sha256s": tainted_shas,
            "updated_at": now_iso,
        }
        _atomic_write_json(taint_index_path, taint_index_data)

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


def handle_parse(
    args: argparse.Namespace, executor: LocalExecutor | None = None
) -> tuple[int, Any]:
    """7. `opendv ontology studio parse -s <session>` via `StudioJobRunners`."""
    auth_res = _resolve_studio_principal(args)
    if isinstance(auth_res[0], int):
        return auth_res  # type: ignore[return-value]
    _, role_tier = auth_res  # type: ignore[misc]
    if role_tier == "viewer":
        return _err(
            7,
            "FORBIDDEN_ADMIN_REQUIRED",
            "Read-only viewer role is not permitted to trigger parse jobs.",
        )

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

    ocr_mode = str(getattr(args, "ocr_mode", None) or "auto")
    try:
        res = run_parse_session_sources(
            session_mgr,
            sid,
            source_ids=collected_ids or None,
            ocr_mode=ocr_mode,
            actor_role=role_tier,
        )
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


def handle_clarify(
    args: argparse.Namespace, executor: LocalExecutor | None = None
) -> tuple[int, Any]:
    """8. `opendv ontology studio clarify -s <session>` via `StudioJobRunners`."""
    auth_res = _resolve_studio_principal(args)
    if isinstance(auth_res[0], int):
        return auth_res  # type: ignore[return-value]
    _, role_tier = auth_res  # type: ignore[misc]
    if role_tier == "viewer":
        return _err(
            7,
            "FORBIDDEN_ADMIN_REQUIRED",
            "Read-only viewer role is not permitted to run clarification.",
        )
    if role_tier == "automated":
        return _err(
            7,
            "AGENT_OPERATION_FORBIDDEN",
            "Automated agent role is forbidden from dispatching clarification jobs.",
        )

    ctx = _require_session_context(args, executor)
    if isinstance(ctx[0], int):
        return ctx  # type: ignore[return-value]
    session_mgr, _, sdir, sid = ctx  # type: ignore[misc]

    mode = str(getattr(args, "mode", None) or "auto").lower()
    if mode == "agent":
        return _err(
            1,
            "AGENT_UNAVAILABLE",
            "Agent-assisted clarification harness is unavailable in local standalone engine; "
            "use --mode deterministic or --mode auto.",
        )

    max_q_raw = getattr(args, "max_questions", None)
    max_q = int(max_q_raw) if max_q_raw is not None else 10
    focus = getattr(args, "focus_concept", None)
    cand_terms = [str(focus)] if focus else None

    try:
        res = run_clarify_session(
            session_mgr,
            sid,
            candidate_terms=cand_terms,
            max_questions=max_q,
            actor_role=role_tier,
        )
    except OpenDVError as exc:
        return _err_from_opendv_error(exc)

    q_dicts: list[dict[str, Any]] = []
    q_file = sdir / "clarifications" / "questions.json"
    if q_file.is_file():
        with contextlib.suppress(json.JSONDecodeError, OSError):
            loaded_q = json.loads(q_file.read_text(encoding="utf-8"))
            if isinstance(loaded_q, list):
                q_dicts = loaded_q
    res["questions"] = q_dicts
    return 0, res


def _normalize_answer_item_dict(raw_item: dict[str, Any]) -> ClarificationAnswerItem:
    """Normalize CLI/legacy answer item keys into strict `ClarificationAnswerItem` DTO."""
    qid = str(raw_item.get("question_id") or "").strip()
    raw_opts = raw_item.get("selected_options")
    if raw_opts is None:
        raw_opts = raw_item.get("selected_option_ids")
    if raw_opts is None and raw_item.get("selected_option_id") is not None:
        raw_opts = [str(raw_item["selected_option_id"])]
    if isinstance(raw_opts, str):
        selected_opts = [s.strip() for s in raw_opts.split(",") if s.strip()]
    elif isinstance(raw_opts, list):
        selected_opts = [str(s).strip() for s in raw_opts if str(s).strip()]
    else:
        selected_opts = []

    raw_text = (
        raw_item.get("user_text")
        if raw_item.get("user_text") is not None
        else (
            raw_item.get("free_text")
            if raw_item.get("free_text") is not None
            else raw_item.get("text")
        )
    )
    user_text = str(raw_text).strip() if raw_text is not None and str(raw_text).strip() else None

    return ClarificationAnswerItem(
        question_id=qid,
        selected_options=selected_opts,
        user_text=user_text,
    )


def handle_answer(
    args: argparse.Namespace, executor: LocalExecutor | None = None
) -> tuple[int, Any]:
    """9. `opendv ontology studio answer -s <session>` (canonical list + JCS OCC)."""
    auth_res = _resolve_studio_principal(args)
    if isinstance(auth_res[0], int):
        return auth_res  # type: ignore[return-value]
    principal, role_tier = auth_res  # type: ignore[misc]
    if role_tier == "automated":
        return _err(
            7,
            "AGENT_OPERATION_FORBIDDEN",
            "Automated actors are forbidden from submitting human clarification answers.",
        )
    if role_tier == "viewer":
        return _err(
            7,
            "FORBIDDEN_ADMIN_REQUIRED",
            "Read-only viewer role is not permitted to submit clarification answers.",
        )

    ctx = _require_session_context(args, executor)
    if isinstance(ctx[0], int):
        return ctx  # type: ignore[return-value]
    session_mgr, _, sdir, sid = ctx  # type: ignore[misc]

    ans_file = getattr(args, "file", None)
    qid_flag = getattr(args, "question_id", None) or getattr(args, "question", None)
    opt_flag = getattr(args, "option", None) or getattr(args, "option_id", None)
    text_flag = getattr(args, "text", None) or getattr(args, "free_text", None)
    exp_sha_flag = getattr(args, "expected_answers_sha256", None)

    incoming_raw: list[dict[str, Any]] = []
    file_exp_sha: str | None = None

    if ans_file:
        fpath = Path(ans_file)
        if not fpath.is_absolute() and executor:
            fpath = (executor.workspace_root / fpath).resolve()
        if not fpath.is_file():
            return _err(3, "FILE_NOT_FOUND", f"Answers file not found: {ans_file}")
        try:
            loaded_answers = json.loads(fpath.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            return _err(2, "SYNTAX_INVALID_ARGUMENT", f"Malformed answers JSON: {exc}")

        if isinstance(loaded_answers, dict):
            if isinstance(loaded_answers.get("expected_answers_sha256"), str):
                file_exp_sha = str(loaded_answers["expected_answers_sha256"])
            if isinstance(loaded_answers.get("answers"), list):
                for item in loaded_answers["answers"]:
                    if isinstance(item, dict):
                        incoming_raw.append(item)
            elif loaded_answers.get("question_id"):
                incoming_raw.append(loaded_answers)
            else:
                for k, v in loaded_answers.items():
                    if k == "expected_answers_sha256":
                        continue
                    if isinstance(v, dict):
                        d = dict(v)
                        d.setdefault("question_id", str(k))
                        incoming_raw.append(d)
        elif isinstance(loaded_answers, list):
            for item in loaded_answers:
                if isinstance(item, dict):
                    incoming_raw.append(item)
        else:
            return _err(
                2, "SYNTAX_INVALID_ARGUMENT", "Answers file must contain a JSON list or object"
            )

    if qid_flag:
        if opt_flag is None and text_flag is None:
            return _err(
                2,
                "SYNTAX_INVALID_ARGUMENT",
                "Clarification answer must include at least one --option or non-empty --text.",
            )
        flag_item: dict[str, Any] = {"question_id": str(qid_flag)}
        if opt_flag is not None:
            flag_item["selected_options"] = (
                [opt_flag] if isinstance(opt_flag, str) else list(opt_flag)
            )
        if text_flag is not None:
            flag_item["user_text"] = str(text_flag)
        incoming_raw.append(flag_item)

    if not incoming_raw:
        return _err(
            2,
            "SYNTAX_INVALID_ARGUMENT",
            "Provide either --file or --question-id with --option/--text for studio answer.",
        )

    try:
        incoming_items = [_normalize_answer_item_dict(item) for item in incoming_raw]
    except (ValidationError, ValueError, TypeError) as exc:
        return _err(2, "SYNTAX_INVALID_ARGUMENT", f"Invalid clarification answer item: {exc}")

    seen_qids: set[str] = set()
    for item in incoming_items:
        if not item.question_id:
            return _err(
                2,
                "SYNTAX_INVALID_ARGUMENT",
                "Clarification answer item is missing question_id.",
            )
        if item.question_id in seen_qids:
            return _err(
                2,
                "SYNTAX_INVALID_ARGUMENT",
                f"Duplicate question_id '{item.question_id}' in clarification answers.",
            )
        seen_qids.add(item.question_id)
        if not item.selected_options and (
            item.user_text is None or not str(item.user_text).strip()
        ):
            return _err(
                2,
                "SYNTAX_INVALID_ARGUMENT",
                "Clarification answer must include at least one --option or non-empty --text.",
            )

    expected_sha = (exp_sha_flag or file_exp_sha or "").strip().strip('"').lower() or None

    target_ans = sdir / "clarifications" / "answers.json"
    lock_file = sdir / ".session.lock"

    with reentrant_session_lock(lock_file, session_id=sid):
        target_ans.parent.mkdir(parents=True, exist_ok=True)
        q_file = sdir / "clarifications" / "questions.json"
        if q_file.is_file():
            try:
                raw_q = json.loads(q_file.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as exc:
                return _err(
                    1,
                    "CORRUPT_SESSION_RECORD",
                    f"Corrupt questions.json in session '{sid}': {exc}",
                )
            if isinstance(raw_q, dict) and isinstance(raw_q.get("questions"), list):
                q_list = raw_q["questions"]
            elif isinstance(raw_q, list):
                q_list = raw_q
            else:
                return _err(
                    1,
                    "CORRUPT_SESSION_RECORD",
                    f"Corrupt questions.json in session '{sid}': "
                    "expected list or questions wrapper.",
                )

            if q_list:
                q_map: dict[str, dict[str, Any]] = {}
                for q_obj in q_list:
                    if not isinstance(q_obj, dict) or not q_obj.get("question_id"):
                        return _err(
                            1,
                            "CORRUPT_SESSION_RECORD",
                            f"Corrupt question entry in questions.json for session '{sid}'.",
                        )
                    q_map[str(q_obj["question_id"])] = q_obj

                for item in incoming_items:
                    if item.question_id not in q_map:
                        return _err(
                            3,
                            "RESOURCE_NOT_FOUND",
                            f"Clarification question '{item.question_id}' "
                            f"does not exist in session '{sid}'.",
                        )
                    q_def = q_map[item.question_id]
                    if item.user_text and q_def.get("allow_custom_text") is False:
                        return _err(
                            2,
                            "SYNTAX_INVALID_ARGUMENT",
                            f"Question '{item.question_id}' does not permit "
                            "custom free-text answers.",
                        )
                    valid_opts: set[str] = set()
                    raw_q_opts = q_def.get("options")
                    if isinstance(raw_q_opts, list):
                        for opt_entry in raw_q_opts:
                            if isinstance(opt_entry, str) and opt_entry.strip():
                                valid_opts.add(opt_entry.strip())
                            elif isinstance(opt_entry, dict):
                                for opt_key in ("option_id", "id", "value", "label"):
                                    opt_val = opt_entry.get(opt_key)
                                    if isinstance(opt_val, str) and opt_val.strip():
                                        valid_opts.add(opt_val.strip())
                    if valid_opts:
                        for sel in item.selected_options:
                            if sel not in valid_opts:
                                return _err(
                                    2,
                                    "SYNTAX_INVALID_ARGUMENT",
                                    f"Invalid option '{sel}' for question '{item.question_id}'.",
                                )

        cas = session_mgr.get_cas(sid)
        existing_map: dict[str, ClarificationAnswerItem] = {}
        if target_ans.is_file():
            try:
                raw_existing = json.loads(target_ans.read_text(encoding="utf-8"))
                if isinstance(raw_existing, list):
                    for item in raw_existing:
                        if isinstance(item, dict):
                            norm_existing = _normalize_answer_item_dict(item)
                            existing_map[norm_existing.question_id] = norm_existing
                elif isinstance(raw_existing, dict):
                    for k, item in raw_existing.items():
                        if isinstance(item, dict):
                            d = dict(item)
                            d.setdefault("question_id", str(k))
                            norm_existing = _normalize_answer_item_dict(d)
                            existing_map[norm_existing.question_id] = norm_existing
            except (json.JSONDecodeError, OSError, ValidationError, ValueError) as exc:
                return _err(
                    1,
                    "CORRUPT_SESSION_RECORD",
                    f"Corrupt answers record in session '{sid}': {exc}",
                )

        sorted_existing = sorted(existing_map.values(), key=lambda x: x.question_id)
        if sorted_existing:
            existing_jcs = dumps_jcs(
                [a.model_dump(mode="json", exclude_none=True) for a in sorted_existing]
            ).encode("utf-8")
            current_sha = hashlib.sha256(existing_jcs).hexdigest()
        else:
            current_sha = EMPTY_ANSWERS_SHA256

        if expected_sha is not None and expected_sha != current_sha:
            return _err(
                5,
                "ONT_EVO_CONCURRENT_MODIFICATION",
                f"Clarification answers OCC mismatch: expected '{expected_sha}', "
                f"current is '{current_sha}'.",
            )

        for item in incoming_items:
            existing_map[item.question_id] = item

        merged_sorted = sorted(existing_map.values(), key=lambda x: x.question_id)
        answers_payload = [a.model_dump(mode="json", exclude_none=True) for a in merged_sorted]
        jcs_bytes = dumps_jcs(answers_payload).encode("utf-8")
        answers_sha = cas.put(jcs_bytes)
        _atomic_write_json(target_ans, answers_payload)
        append_signed_audit_record(
            session_dir=sdir,
            action="CLARIFICATION_ANSWERS_SAVED",
            actor=principal,
            details={"answered_count": len(answers_payload), "answers_sha256": answers_sha},
            security_manager=session_mgr.security_manager,
        )

    return 0, {
        "session_id": sid,
        "status": "ACCEPTED",
        "saved_count": len(incoming_items),
        "answers_recorded": len(answers_payload),
        "answers_sha256": answers_sha,
    }


def handle_construct(
    args: argparse.Namespace, executor: LocalExecutor | None = None
) -> tuple[int, Any]:
    """10. `opendv ontology studio construct -s <session>` via `StudioJobRunners`."""
    auth_res = _resolve_studio_principal(args)
    if isinstance(auth_res[0], int):
        return auth_res  # type: ignore[return-value]
    principal, role_tier = auth_res  # type: ignore[misc]
    if role_tier == "viewer":
        return _err(
            7,
            "FORBIDDEN_ADMIN_REQUIRED",
            "Read-only viewer role is not permitted to construct ontology proposals.",
        )

    auto_commit = bool(getattr(args, "auto_commit", False))
    if auto_commit and role_tier == "automated":
        return _err(
            7,
            "AGENT_PROPOSAL_ACCEPT_FORBIDDEN",
            "Automated agent role is forbidden from committing ontology deltas directly.",
        )

    ctx = _require_session_context(args, executor)
    if isinstance(ctx[0], int):
        return ctx  # type: ignore[return-value]
    session_mgr, _, sdir, sid = ctx  # type: ignore[misc]

    mode = str(getattr(args, "mode", "auto") or "auto").lower()
    base_ver = getattr(args, "base_version", None)
    base_sha = getattr(args, "base_sha256", None)

    if base_ver and not _VERSION_PATTERN.match(str(base_ver)):
        return _err(2, "SYNTAX_INVALID_ARGUMENT", f"Invalid --base-version format: '{base_ver}'")

    if auto_commit and (mode != "deterministic" or not base_ver or not base_sha):
        return _err(
            2,
            "MISSING_AUTO_COMMIT_PRECONDITION",
            "--auto-commit requires --mode deterministic, --base-version, and --base-sha256",
        )

    if base_ver:
        clean_ver = str(base_ver).lstrip("v")
        vdir = (sdir / "versions" / f"v{clean_ver}").resolve()
        if not vdir.is_relative_to(sdir.resolve()):
            return _err(
                2, "INVALID_WORKSPACE_PATH", f"Base version path traversal detected: '{base_ver}'"
            )
        if clean_ver != "0.0.0" and not (
            (vdir / "ontology.json").is_file() or (vdir / "ontology.yaml").is_file()
        ):
            return _err(
                2,
                "SYNTAX_INVALID_ARGUMENT",
                f"Specified --base-version does not exist: '{base_ver}'",
            )

    sec_mgr = session_mgr.security_manager

    try:
        res = run_construct_session(
            session_mgr,
            sid,
            actor=principal if auto_commit else "system:deterministic",
            actor_role=role_tier,
            mode=mode,
            auto_commit=auto_commit,
            base_version=base_ver,
            base_sha256=base_sha,
            if_match=base_sha if auto_commit else None,
            security_manager=sec_mgr,
        )
    except ConcurrentModificationError as err_occ:
        return _err(5, "ONT_EVO_CONCURRENT_MODIFICATION", str(err_occ))
    except InvalidPreconditionHeaderError as err_hdr:
        return _err(2, "INVALID_PRECONDITION_HEADER", str(err_hdr))
    except OpenDVError as exc:
        return _err_from_opendv_error(exc)

    return 0, res


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
    base_ver_arg = getattr(args, "base_version", None)
    base_sha_arg = getattr(args, "base_sha256", None)
    meta = None
    sdir: Path | None = None
    base_ontology: GraphAOntology | None = None
    grandfathered: set[str] = set()

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
            if base_path.suffix.lower() == ".json":
                base_ontology = GraphAOntology.model_validate_json(raw_text)
            else:
                base_ontology = GraphAOntology.model_validate(yaml.safe_load(raw_text))
            base_ver = base_ontology.ontology_version
            expected_parent_sha = compute_ontology_sha256(base_ontology)
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

    raw_ops = delta_dict.get("operations", [])
    try:
        norm_ops = [_normalize_delta_operation(op) for op in raw_ops]
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
            f"target_version '{req_tgt}' does not match required semver bump "
            f"'{computed_target_ver}'."
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

    studio_root = _get_studio_root(executor)
    sec_mgr = WorkspaceSecurityManager(studio_root=studio_root)

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

    error_code_out = "ONT_EVO_CONTRACT_VIOLATION"
    exit_code_out = 4
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

    # Check waiver role & credentials BEFORE rationale / delta apply (MAJ-03, P12::MAJ-04)
    studio_root = _get_studio_root(executor)
    sec_mgr = WorkspaceSecurityManager(studio_root=studio_root)
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
    try:
        target_version = compute_next_semver(meta.head_version, temp_delta, base_ontology=base_ont)
    except (
        ValueError,
        TypeError,
        KeyError,
        SemVerCalculusError,
        ValidationError,
        AttributeError,
    ) as exc:
        return _err(4, "ONT_EVO_CONTRACT_VIOLATION", f"SemVer bump computation failure: {exc}")

    user_target = delta_dict.get("target_version") or delta_dict.get("target_ontology_version")
    if (
        user_target
        and isinstance(user_target, str)
        and user_target.lstrip("v") != target_version.lstrip("v")
    ):
        return _err(
            4,
            "ONT_EVO_CONTRACT_VIOLATION",
            f"User target_version '{user_target}' does not match required semver bump "
            f"'{target_version}'.",
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

    waiver_res = _build_signed_waiver_record(
        args=args,
        delta_obj=delta_obj,
        base_ontology=base_ont,
        base_sha256=meta.head_sha256,
        proposal_id=None,
        principal=principal,
        role_tier=role_tier,
        executor=executor,
        sec_mgr=sec_mgr,
    )
    if isinstance(waiver_res, tuple):
        return waiver_res
    waiver_record = waiver_res
    waivers_list = [waiver_record] if waiver_record is not None else []

    eval_dict = (
        {"waiver": waiver_record.model_dump(mode="json", exclude_none=True)}
        if waiver_record
        else None
    )
    try:
        gv = _build_governance_validator(executor, sec_mgr, verify_grounding=True)
        commit_svc = AtomicCommitService(
            session_manager=session_mgr,
            security_manager=sec_mgr,
            governance_validator=gv,
        )
        res = commit_svc.commit_delta(
            session_id=sid,
            delta=delta_obj,
            author=principal,
            actor_role=role_tier,
            if_match=clean_base_sha if clean_base_sha else meta.head_sha256,
            waivers=waivers_list,
            governance_validator=gv,
            evaluation=eval_dict,
        )
    except ForbiddenAdminRequiredError as err_adm:
        return _err(7, "FORBIDDEN_ADMIN_REQUIRED", str(err_adm))
    except ConcurrentModificationError as err_occ:
        return _err(5, "ONT_EVO_CONCURRENT_MODIFICATION", str(err_occ))
    except OpenDVError as exc:
        return _err_from_opendv_error(exc)
    except (
        RuntimeError,
        ValueError,
        TypeError,
        KeyError,
        ValidationError,
        OSError,
    ) as err_commit:
        return _err(4, "ONT_EVO_CONTRACT_VIOLATION", str(err_commit))

    bump_type = delta_obj.highest_semver_class(base_ont)

    return 0, {
        "session_id": sid,
        "target_version": res.version.lstrip("v"),
        "target_sha256": res.target_sha256,
        "parent_sha256": res.parent_sha256,
        "delta_id": delta_obj.delta_id,
        "bump_type": bump_type,
        "applied_operations_count": len(norm_ops),
    }


def handle_proposal_list(
    args: argparse.Namespace, executor: LocalExecutor | None = None
) -> tuple[int, Any]:
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


def handle_proposal_show(
    args: argparse.Namespace, executor: LocalExecutor | None = None
) -> tuple[int, Any]:
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
    pfile = prop_res

    if not pfile.is_file():
        return _err(3, "PROPOSAL_NOT_FOUND", f"Proposal '{pid}' not found")

    try:
        return 0, json.loads(pfile.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return _err(1, "CORRUPT_SESSION_RECORD", f"Failed to read proposal JSON: {exc}")


def handle_proposal_accept(
    args: argparse.Namespace, executor: LocalExecutor | None = None
) -> tuple[int, Any]:
    """15. `opendv ontology studio proposal accept` (RBAC, self-acceptance, waiver, governance)."""
    auth_res = _resolve_studio_principal(args)
    if isinstance(auth_res[0], int):
        return auth_res  # type: ignore[return-value]
    principal, role_tier = auth_res  # type: ignore[misc]
    if role_tier == "viewer":
        return _err(
            7,
            "FORBIDDEN_ADMIN_REQUIRED",
            "Read-only viewer role is not permitted to accept proposals.",
        )
    if role_tier == "automated":
        return _err(
            7,
            "AGENT_PROPOSAL_ACCEPT_FORBIDDEN",
            "Automated agent role is forbidden from accepting ontology proposals.",
        )

    allow_self = bool(getattr(args, "allow_self_accepted_proposals", False))
    if allow_self and role_tier != "admin":
        return _err(
            7,
            "FORBIDDEN_ADMIN_REQUIRED",
            "--allow-self-accepted-proposals requires the 'admin' role.",
        )

    studio_root = _get_studio_root(executor)
    sec_mgr = WorkspaceSecurityManager(studio_root=studio_root)
    if bool(getattr(args, "low_confidence_waiver", False)):
        cred_err = _load_and_verify_waiver_credentials(args, executor, sec_mgr, role_tier=role_tier)
        if cred_err is not None:
            return cred_err

    pid = getattr(args, "proposal_id", None) or getattr(args, "proposal", None)
    base_sha = getattr(args, "base_sha256", None)
    if not pid or base_sha is None:
        return _err(2, "SYNTAX_INVALID_ARGUMENT", "Missing --proposal-id or --base-sha256")

    ctx = _require_session_context(args, executor)
    if isinstance(ctx[0], int):
        return ctx  # type: ignore[return-value]
    session_mgr, meta, sdir, sid = ctx  # type: ignore[misc]

    prop_res = _resolve_proposal_path(sdir, pid)
    if isinstance(prop_res, tuple):
        return prop_res
    pfile = prop_res

    # Inspect proposal under session lock for existence, status, self-acceptance, and OCC
    lock_file = sdir / ".session.lock"
    with reentrant_session_lock(lock_file, session_id=sid):
        meta = session_mgr.get_session(sid)
        if not pfile.is_file():
            return _err(3, "PROPOSAL_NOT_FOUND", f"Proposal '{pid}' not found")

        try:
            prop_data = json.loads(pfile.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            return _err(1, "CORRUPT_SESSION_RECORD", f"Failed to read proposal JSON: {exc}")

        if prop_data.get("status") in ("ACCEPTED", "REJECTED"):
            return _err(
                6,
                "PROPOSAL_ALREADY_RESOLVED",
                f"Proposal '{pid}' has already been resolved ({prop_data.get('status')}).",
            )

        proposer = _extract_proposal_proposer(prop_data)
        if proposer == principal and not allow_self:
            return _err(
                7,
                "PROPOSAL_SELF_ACCEPTANCE_FORBIDDEN",
                f"Principal '{principal}' cannot accept their own proposal '{pid}' without "
                "--allow-self-accepted-proposals (admin only).",
            )

        clean_base_sha = str(base_sha).strip('"')
        is_genesis_occ = meta.head_version in ("0.0.0", "", None) and clean_base_sha == ""
        if clean_base_sha != meta.head_sha256 and not is_genesis_occ:
            return _err(
                5,
                "ONT_EVO_CONCURRENT_MODIFICATION",
                f"Base SHA-256 mismatch: expected {meta.head_sha256}, got {base_sha}",
            )

        prop_base = _extract_proposal_base_sha256(prop_data)
        is_genesis = meta.head_version in ("0.0.0", "", None) and (
            prop_base == "" or clean_base_sha == ""
        )
        if prop_base != meta.head_sha256 and not is_genesis:
            return _err(
                5,
                "ONT_EVO_CONCURRENT_MODIFICATION",
                f"Proposal base SHA ('{prop_base}') is stale; rebase onto head "
                f"'{meta.head_sha256}' is required before acceptance.",
            )

        raw_ops = _extract_proposal_operations(prop_data)
        try:
            norm_ops = [_normalize_delta_operation(op) for op in raw_ops]
        except (ValidationError, ValueError, TypeError, KeyError) as exc:
            return _err(4, "ONT_EVO_CONTRACT_VIOLATION", f"Invalid proposal operations: {exc}")

        if len(norm_ops) == 0:
            prop_data["status"] = "ACCEPTED"
            prop_data["accepted_at"] = datetime.now(UTC).isoformat()
            prop_data["accepted_by"] = principal
            prop_data["commit_sha256"] = meta.head_sha256
            _atomic_write_json(pfile, prop_data)
            append_signed_audit_record(
                session_dir=sdir,
                action="PROPOSAL_ACCEPTED_NOOP",
                actor=principal,
                details={
                    "proposal_id": str(pid),
                    "base_version": meta.head_version,
                    "base_sha256": meta.head_sha256,
                },
                security_manager=sec_mgr,
            )
            return 0, {
                "session_id": sid,
                "target_version": meta.head_version.lstrip("v"),
                "target_sha256": meta.head_sha256,
                "parent_sha256": meta.head_sha256,
                "delta_id": pid,
                "proposal_id": pid,
                "bump_type": "NONE",
                "applied_operations_count": 0,
            }

        base_ont = _load_session_version_ontology(
            sdir,
            meta.head_version,
            domain_namespace=meta.domain_namespace,
            enterprise_owner=meta.enterprise_owner,
        )
        if base_ont is None:
            return _err(1, "CORRUPT_SESSION_STATE", f"Missing head ontology '{meta.head_version}'")

        temp_delta = OntologyDelta.model_construct(
            delta_id=f"delta-{uuid.uuid4().hex[:8]}",
            parent_sha256=meta.head_sha256,
            base_ontology_version=meta.head_version,
            target_ontology_version=None,
            operations=norm_ops,
        )
        try:
            next_ver = compute_next_semver(meta.head_version, temp_delta, base_ontology=base_ont)
        except (
            ValueError,
            TypeError,
            KeyError,
            SemVerCalculusError,
            ValidationError,
            AttributeError,
        ) as exc:
            return _err(4, "ONT_EVO_CONTRACT_VIOLATION", f"SemVer bump computation failure: {exc}")

        try:
            delta_obj = OntologyDelta(
                delta_id=pid,
                parent_sha256=meta.head_sha256,
                base_version=meta.head_version,
                target_version=next_ver,
                operations=norm_ops,
                author=principal,
                rationale=prop_data.get("rationale")
                or getattr(args, "waiver_rationale", None)
                or getattr(args, "grounding_rationale", None)
                or "Accepted via opendv ontology studio proposal accept",
            )
        except (ValidationError, ValueError, TypeError) as exc:
            return _err(
                4, "ONT_EVO_CONTRACT_VIOLATION", f"Proposal delta construction failure: {exc}"
            )

        is_major = (
            parse_semver_tuple(next_ver)[0] > parse_semver_tuple(meta.head_version)[0]
            and meta.head_version != "0.0.0"
        )
        if (is_destructive_delta(delta_obj, base_ont) or is_major) and role_tier != "admin":
            return _err(
                7,
                "FORBIDDEN_ADMIN_REQUIRED",
                "Destructive (MAJOR SemVer) proposal acceptance requires the 'admin' role.",
            )

        waiver_res = _build_signed_waiver_record(
            args=args,
            delta_obj=delta_obj,
            base_ontology=base_ont,
            base_sha256=meta.head_sha256,
            proposal_id=str(pid),
            principal=principal,
            role_tier=role_tier,
            executor=executor,
            sec_mgr=sec_mgr,
        )
        if isinstance(waiver_res, tuple):
            return waiver_res
        waiver_record = waiver_res
        waivers_list = [waiver_record] if waiver_record is not None else []

        eval_dict = (
            {"waiver": waiver_record.model_dump(mode="json", exclude_none=True)}
            if waiver_record
            else None
        )
        try:
            gv = _build_governance_validator(executor, sec_mgr, verify_grounding=True)
            commit_svc = AtomicCommitService(
                session_manager=session_mgr,
                security_manager=sec_mgr,
                governance_validator=gv,
            )
            res = commit_svc.commit_delta(
                session_id=sid,
                delta=delta_obj,
                proposal_id=str(pid),
                author=principal,
                actor_role=role_tier,
                if_match=meta.head_sha256,
                waivers=waivers_list,
                governance_validator=gv,
                evaluation=eval_dict,
            )
        except ForbiddenAdminRequiredError as err_adm:
            return _err(7, "FORBIDDEN_ADMIN_REQUIRED", str(err_adm))
        except ConcurrentModificationError as err_occ:
            if "already been resolved" in str(err_occ):
                return _err(6, "PROPOSAL_ALREADY_RESOLVED", str(err_occ))
            return _err(5, "ONT_EVO_CONCURRENT_MODIFICATION", str(err_occ))
        except OpenDVError as exc:
            return _err_from_opendv_error(exc)
        except (
            RuntimeError,
            ValueError,
            TypeError,
            KeyError,
            ValidationError,
            OSError,
        ) as err_commit:
            return _err(4, "ONT_EVO_CONTRACT_VIOLATION", str(err_commit))

        prop_data["status"] = "ACCEPTED"
        prop_data["accepted_at"] = datetime.now(UTC).isoformat()
        prop_data["accepted_by"] = principal
        prop_data["commit_sha256"] = res.target_sha256
        _atomic_write_json(pfile, prop_data)

        bump_type = delta_obj.highest_semver_class(base_ont)

        return 0, {
            "session_id": sid,
            "target_version": res.version.lstrip("v"),
            "target_sha256": res.target_sha256,
            "parent_sha256": res.parent_sha256,
            "delta_id": pid,
            "proposal_id": pid,
            "bump_type": bump_type,
            "applied_operations_count": len(norm_ops),
        }


def handle_proposal_reject(
    args: argparse.Namespace, executor: LocalExecutor | None = None
) -> tuple[int, Any]:
    """16. `opendv ontology studio proposal reject`."""
    auth_res = _resolve_studio_principal(args)
    if isinstance(auth_res[0], int):
        return auth_res  # type: ignore[return-value]
    principal, role_tier = auth_res  # type: ignore[misc]
    if role_tier == "automated":
        return _err(
            7,
            "AGENT_OPERATION_FORBIDDEN",
            "Automated agent role is forbidden from rejecting proposals.",
        )
    if role_tier == "viewer":
        return _err(
            7,
            "FORBIDDEN_ADMIN_REQUIRED",
            f"Role '{role_tier}' is not authorized to reject proposals.",
        )

    pid = getattr(args, "proposal_id", None) or getattr(args, "proposal", None)
    ctx = _require_session_context(args, executor)
    if isinstance(ctx[0], int):
        return ctx  # type: ignore[return-value]
    _, _, sdir, sid = ctx  # type: ignore[misc]

    prop_res = _resolve_proposal_path(sdir, pid)
    if isinstance(prop_res, tuple):
        return prop_res
    pfile = prop_res

    lock_file = sdir / ".session.lock"
    with reentrant_session_lock(lock_file, session_id=sid):
        if not pfile.is_file():
            return _err(3, "PROPOSAL_NOT_FOUND", f"Proposal '{pid}' not found")

        try:
            prop_data = json.loads(pfile.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            return _err(1, "CORRUPT_SESSION_RECORD", f"Failed to read proposal JSON: {exc}")

        if prop_data.get("status") in ("ACCEPTED", "REJECTED"):
            return _err(
                6,
                "PROPOSAL_ALREADY_RESOLVED",
                f"Proposal '{pid}' has already been resolved ({prop_data.get('status')}).",
            )

        reason = getattr(args, "reason", None) or "Rejected by steward"
        prop_data["status"] = "REJECTED"
        prop_data["rejected_by"] = principal
        prop_data["rejection_reason"] = reason
        _atomic_write_json(pfile, prop_data)

    return 0, {
        "proposal_id": pid,
        "status": "REJECTED",
        "reason": reason,
    }


def _load_intervening_deltas_strict(
    sdir: Path,
    prop_base_ver_str: str,
    head_ver_str: str,
) -> tuple[int, dict[str, str]] | list[OntologyDelta]:
    """Load all committed `OntologyDelta` records strictly in `(prop_base_ver, head_ver]`."""
    versions_dir = sdir / "versions"
    if not versions_dir.is_dir():
        return _err(1, "CORRUPT_SESSION_RECORD", f"Missing versions directory in '{sdir.name}'")

    try:
        base_tuple = parse_semver(prop_base_ver_str.lstrip("v") or "0.0.0")
        head_tuple = parse_semver(head_ver_str.lstrip("v") or "0.0.0")
    except (SemVerCalculusError, ValueError) as exc:
        return _err(1, "CORRUPT_SESSION_STATE", f"Invalid version identifier during rebase: {exc}")

    matched: list[tuple[tuple[int, int, int], Path]] = []
    for child in versions_dir.iterdir():
        if not child.is_dir() or not child.name.startswith("v"):
            continue
        ver_raw = child.name[1:]
        try:
            ver_t = parse_semver(ver_raw)
        except (SemVerCalculusError, ValueError):
            continue
        if base_tuple < ver_t <= head_tuple:
            matched.append((ver_t, child))

    matched.sort(key=lambda item: item[0])
    deltas: list[OntologyDelta] = []
    for ver_t, vdir in matched:
        delta_file = vdir / "delta.json"
        if not delta_file.is_file():
            return _err(
                1,
                "CORRUPT_SESSION_RECORD",
                f"Missing intervening delta.json for version v{ver_t[0]}.{ver_t[1]}.{ver_t[2]}",
            )
        try:
            raw_d = json.loads(delta_file.read_text(encoding="utf-8"))
            norm_ops = [_normalize_delta_operation(op) for op in raw_d.get("operations", [])]
            raw_d["operations"] = norm_ops
            deltas.append(OntologyDelta.model_validate(raw_d))
        except (OSError, json.JSONDecodeError, ValidationError, ValueError, TypeError) as exc:
            return _err(
                1,
                "CORRUPT_SESSION_RECORD",
                f"Corrupt intervening delta.json in {vdir.name}: {exc}",
            )
    return deltas


def handle_proposal_rebase(
    args: argparse.Namespace, executor: LocalExecutor | None = None
) -> tuple[int, Any]:
    """17. `opendv ontology studio proposal rebase` via 24-op `check_rebase_conflicts`."""
    auth_res = _resolve_studio_principal(args)
    if isinstance(auth_res[0], int):
        return auth_res  # type: ignore[return-value]
    _, role_tier = auth_res  # type: ignore[misc]
    if role_tier == "automated":
        return _err(
            7,
            "AGENT_OPERATION_FORBIDDEN",
            "Automated agent role is forbidden from rebasing proposals.",
        )
    if role_tier == "viewer":
        return _err(
            7,
            "FORBIDDEN_ADMIN_REQUIRED",
            f"Role '{role_tier}' is not authorized to rebase proposals.",
        )

    pid = getattr(args, "proposal_id", None) or getattr(args, "proposal", None)
    if not pid:
        return _err(2, "SYNTAX_INVALID_ARGUMENT", "Missing required --proposal-id")

    ctx = _require_session_context(args, executor)
    if isinstance(ctx[0], int):
        return ctx  # type: ignore[return-value]
    session_mgr, meta, sdir, sid = ctx  # type: ignore[misc]

    prop_res = _resolve_proposal_path(sdir, pid)
    if isinstance(prop_res, tuple):
        return prop_res
    pfile = prop_res

    lock_file = sdir / ".session.lock"
    with reentrant_session_lock(lock_file, session_id=sid):
        meta = session_mgr.get_session(sid)
        if not pfile.is_file():
            return _err(3, "PROPOSAL_NOT_FOUND", f"Proposal '{pid}' not found")

        try:
            prop_data = json.loads(pfile.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            return _err(1, "CORRUPT_SESSION_RECORD", f"Failed to read proposal JSON: {exc}")

        if prop_data.get("status") in ("ACCEPTED", "REJECTED"):
            return _err(
                6,
                "PROPOSAL_ALREADY_RESOLVED",
                f"Cannot rebase proposal '{pid}': status is {prop_data.get('status')}",
            )

        target_sha_arg = getattr(args, "base_sha256", None) or getattr(args, "head_sha256", None)
        if target_sha_arg is not None:
            clean_sha = str(target_sha_arg).strip('"')
            if clean_sha != meta.head_sha256:
                return _err(
                    5,
                    "ONT_EVO_CONCURRENT_MODIFICATION",
                    f"Base SHA mismatch: expected {meta.head_sha256}, got {clean_sha}",
                )

        raw_ops = _extract_proposal_operations(prop_data)
        try:
            ops = [_normalize_delta_operation(op) for op in raw_ops]
        except (ValidationError, ValueError, TypeError, KeyError) as exc:
            return _err(4, "ONT_EVO_CONTRACT_VIOLATION", f"Invalid proposal operations: {exc}")

        # Fail closed when head ontology artifact is missing or corrupt (P12::MAJ-05)
        head_ver = (meta.head_version or "0.0.0").lstrip("v")
        head_vdir = sdir / "versions" / f"v{head_ver}"
        head_ont_file = head_vdir / "ontology.json"
        if not head_ont_file.is_file():
            head_ont_file = head_vdir / "ontology.yaml"
        if head_ver != "0.0.0" and not head_ont_file.is_file():
            return _err(
                1,
                "CORRUPT_SESSION_RECORD",
                f"Head ontology artifact missing for version 'v{head_ver}'; refusing blind rebase.",
            )

        try:
            head_ontology = _load_session_version_ontology(
                sdir,
                meta.head_version,
                domain_namespace=meta.domain_namespace,
                enterprise_owner=meta.enterprise_owner,
            )
        except (OSError, json.JSONDecodeError, yaml.YAMLError, ValidationError, ValueError) as exc:
            return _err(
                1,
                "CORRUPT_SESSION_RECORD",
                f"Corrupt head ontology artifact for version 'v{head_ver}': {exc}",
            )
        if head_ontology is None:
            return _err(
                1,
                "CORRUPT_SESSION_RECORD",
                f"Head ontology artifact could not be loaded for version 'v{head_ver}'.",
            )

        prop_base_ver = str(
            prop_data.get("base_version")
            or (prop_data.get("delta") or {}).get("base_ontology_version")
            or "0.0.0"
        ).lstrip("v")
        try:
            prop_base_ontology = _load_session_version_ontology(
                sdir,
                prop_base_ver,
                domain_namespace=meta.domain_namespace,
                enterprise_owner=meta.enterprise_owner,
            )
        except (OSError, json.JSONDecodeError, yaml.YAMLError, ValidationError, ValueError) as exc:
            return _err(
                1,
                "CORRUPT_SESSION_RECORD",
                f"Corrupt proposal base ontology 'v{prop_base_ver}': {exc}",
            )
        if prop_base_ontology is None:
            return _err(
                1,
                "CORRUPT_SESSION_RECORD",
                f"Missing proposal base ontology artifact 'v{prop_base_ver}'.",
            )

        deltas_res = _load_intervening_deltas_strict(sdir, prop_base_ver, head_ver)
        if isinstance(deltas_res, tuple):
            return deltas_res
        intervening_deltas = deltas_res

        rebase_res = check_rebase_conflicts(
            ops,
            base_ontology=prop_base_ontology,
            head_ontology=head_ontology,
            intervening_deltas=intervening_deltas,
        )
        if rebase_res.has_conflicts:
            conflict_dicts = [c.to_dict() for c in rebase_res.conflicts]
            first_msg = conflict_dicts[0].get("reason", "24-op conflict detected")
            return 6, {
                "status": "ERROR",
                "error_code": "PROPOSAL_STALE_REBASE_REQUIRED",
                "detail": f"Rebase blocked by {len(conflict_dicts)} conflict(s): {first_msg}",
                "conflicts": conflict_dicts,
            }

        temp_delta = OntologyDelta.model_construct(
            delta_id=f"delta-{uuid.uuid4().hex[:8]}",
            parent_sha256=meta.head_sha256,
            base_ontology_version=meta.head_version or "0.0.0",
            target_ontology_version=None,
            operations=ops,
        )
        try:
            t_ver = compute_next_semver(
                meta.head_version or "0.0.0", temp_delta, base_ontology=head_ontology
            )
        except (
            ValueError,
            TypeError,
            KeyError,
            SemVerCalculusError,
            ValidationError,
            AttributeError,
        ) as exc:
            return _err(4, "ONT_EVO_CONTRACT_VIOLATION", f"SemVer bump computation failure: {exc}")

        try:
            rebased_delta = OntologyDelta(
                delta_id=str(
                    (prop_data.get("delta") or {}).get("delta_id")
                    or f"delta-{uuid.uuid4().hex[:12]}"
                ),
                parent_sha256=meta.head_sha256,
                base_version=meta.head_version,
                target_version=t_ver,
                operations=ops,
                author=_extract_proposal_proposer(prop_data),
            )
            apply_delta(head_ontology, rebased_delta, validate_structural=True)
            dep_prov = _build_workspace_deployment_provider(executor)
            if dep_prov is not None:
                GovernanceValidator(
                    deployment_provider=dep_prov,
                    strict_verifier_required=False,
                ).validate_deployed_contracts(head_ontology, rebased_delta)
        except StudioLocalError as exc:
            return _err_from_opendv_error(exc)
        except (ValidationError, ValueError, TypeError, SemVerCalculusError, OpenDVError) as exc:
            return _err(
                4,
                "ONT_EVO_CONTRACT_VIOLATION",
                f"Rebase post-state structural validation failed against head: {exc}",
            )

        prop_data["base_sha256"] = meta.head_sha256
        prop_data["base_ontology_sha256"] = meta.head_sha256
        prop_data["base_version"] = meta.head_version
        if isinstance(prop_data.get("delta"), dict):
            prop_data["delta"] = rebased_delta.model_dump(mode="json", exclude_none=True)
        prop_data["rebased_at"] = datetime.now(UTC).isoformat()
        prop_data["status"] = "PENDING"
        _atomic_write_json(pfile, prop_data)

    return 0, {
        "proposal_id": pid,
        "status": "REBASED",
        "base_sha256": meta.head_sha256,
        "base_version": meta.head_version,
    }


def handle_export(
    args: argparse.Namespace, executor: LocalExecutor | None = None
) -> tuple[int, Any]:
    """18. `opendv ontology studio export -s <session>` (never reads global `args.format`!)."""
    auth_res = _resolve_studio_principal(args)
    if isinstance(auth_res[0], int):
        return auth_res  # type: ignore[return-value]

    ctx = _require_session_context(args, executor)
    if isinstance(ctx[0], int):
        return ctx  # type: ignore[return-value]
    _, meta, sdir, sid = ctx  # type: ignore[misc]

    raw_ver = getattr(args, "version", None)
    if raw_ver:
        if not _VERSION_PATTERN.match(str(raw_ver)):
            return _err(2, "SYNTAX_INVALID_ARGUMENT", f"Invalid version identifier '{raw_ver}'.")
        ver_tag = str(raw_ver) if str(raw_ver).startswith("v") else f"v{raw_ver}"
        vdir = (sdir / "versions" / ver_tag).resolve()
    else:
        vdir = (sdir / "versions" / f"v{meta.head_version.lstrip('v')}").resolve()

    if not vdir.is_relative_to(sdir.resolve()):
        return _err(
            2, "INVALID_WORKSPACE_PATH", f"Version path escapes session directory: '{raw_ver}'"
        )

    ont_file = vdir / "ontology.json"
    if not ont_file.is_file():
        ont_file = vdir / "ontology.yaml"

    if not ont_file.is_file():
        return _err(
            3, "VERSION_NOT_FOUND", f"Ontology artifact not found for version '{vdir.name}'"
        )

    try:
        if ont_file.suffix == ".json":
            ont = GraphAOntology.model_validate_json(ont_file.read_text(encoding="utf-8"))
        else:
            ont = GraphAOntology.model_validate(
                yaml.safe_load(ont_file.read_text(encoding="utf-8"))
            )
    except (ValidationError, yaml.YAMLError, OSError, ValueError) as exc:
        return _err(1, "CORRUPT_SESSION_STATE", f"Failed to load ontology artifact: {exc}")

    # CRIT-03 parity: strictly use `export_format` (never global `--format` default `toon`)
    fmt = str(getattr(args, "export_format", None) or "yaml").lower()
    if fmt not in (
        "yaml",
        "yml",
        "json",
        "ttl",
        "turtle",
        "rdf",
        "owl",
        "jsonld",
        "json-ld",
        "nt",
        "ntriples",
        "xml",
    ):
        return _err(
            2,
            "SYNTAX_INVALID_ARGUMENT",
            f"Unsupported export format '{fmt}'. Supported: yaml, json, turtle, jsonld.",
        )

    out_file = getattr(args, "out", None)
    resolved_out_p: Path | None = None
    if out_file is not None:
        out_res = _validate_out_path(str(out_file), executor)
        if isinstance(out_res, tuple):
            return out_res
        resolved_out_p = out_res

    try:
        if fmt in ("ttl", "turtle", "rdf", "owl", "jsonld", "json-ld", "nt", "ntriples", "xml"):
            res = export_ontology(ont, export_format=fmt)
            exported_text = res[0] if isinstance(res, tuple) else str(res)
        elif fmt == "json":
            exported_text = json.dumps(ont.model_dump(mode="json", exclude_none=True), indent=2)
        else:
            exported_text = yaml.safe_dump(ont.model_dump(mode="json", exclude_none=True))
    except (RuntimeError, ValueError, TypeError, OSError) as exc:
        return _err(1, "EXECUTION_FAILURE", f"Ontology serialization failed: {exc}")

    if resolved_out_p is not None:
        try:
            _atomic_write_text(resolved_out_p, exported_text)
        except OSError as exc:
            return _err(1, "EXECUTION_FAILURE", f"Failed to write export output: {exc}")

    content_sha = hashlib.sha256(exported_text.encode("utf-8")).hexdigest()
    res_payload: dict[str, Any] = {
        "session_id": sid,
        "version": vdir.name,
        "format": fmt,
        "export_format": fmt,
        "sha256": content_sha,
        "content": exported_text,
    }
    if resolved_out_p is not None:
        res_payload["out_path"] = str(resolved_out_p)
    return 0, res_payload


def _resolve_workspace_target_contract(target_ws: Path, domain_namespace: str) -> Path:
    """Resolve existing workspace Graph A contract or greenfield `{ns}_graph_a_ontology.yaml`."""
    if target_ws.suffix.lower() in (".yaml", ".yml", ".json"):
        return target_ws

    ns_slug = sanitize_domain_namespace(domain_namespace or "general")
    namespaced = target_ws / f"{ns_slug}_graph_a_ontology.yaml"
    candidates: list[Path] = [namespaced]
    if ns_slug in ("private_equity", "pe"):
        candidates.append(target_ws / "pe_graph_a_ontology.yaml")
    candidates.extend(
        [
            target_ws / "graph_a_ontology.yaml",
            target_ws / "ontology.yaml",
        ]
    )
    for cand in candidates:
        if cand.is_file():
            return cand
    return namespaced


def handle_promote(
    args: argparse.Namespace, executor: LocalExecutor | None = None
) -> tuple[int, Any]:
    """19. `opendv ontology studio promote` via `AtomicCommitService.promote_to_workspace`."""
    auth_res = _resolve_studio_principal(args)
    if isinstance(auth_res[0], int):
        return auth_res  # type: ignore[return-value]
    principal, role_tier = auth_res  # type: ignore[misc]
    if role_tier != "admin":
        return _err(
            7,
            "FORBIDDEN_ADMIN_REQUIRED",
            "Promoting session ontology to workspace requires the 'admin' role.",
        )

    rationale = getattr(args, "rationale", None)
    if not rationale or len(rationale.strip()) < 10:
        return _err(
            1,
            "PROMOTION_RATIONALE_REQUIRED",
            "Promotion rationale of at least 10 characters is required.",
        )

    ack_hist = bool(getattr(args, "acknowledge_historical_contract_warning", False))
    hist_rationale = getattr(args, "historical_contract_rationale", None)
    if ack_hist and (not hist_rationale or len(str(hist_rationale).strip()) < 10):
        return _err(
            1,
            "HISTORICAL_CONTRACT_RATIONALE_REQUIRED",
            "historical_contract_rationale of at least 10 characters is required when "
            "acknowledge_historical_contract_warning=True.",
        )

    ws_sha = getattr(args, "workspace_sha256", None)
    head_sha = getattr(args, "head_sha256", None)
    if not ws_sha or not head_sha:
        return _err(2, "SYNTAX_INVALID_ARGUMENT", "Missing --workspace-sha256 or --head-sha256")

    ctx = _require_session_context(args, executor)
    if isinstance(ctx[0], int):
        return ctx  # type: ignore[return-value]
    session_mgr, meta, _, sid = ctx  # type: ignore[misc]

    base_ws = (executor.workspace_root if executor else Path.cwd()).resolve()
    configured_roots = _resolve_configured_workspace_roots(base_ws)
    allowed_roots = configured_roots if configured_roots is not None else [base_ws]

    ws_arg = getattr(args, "workspace", None)
    if ws_arg:
        raw_ws_path = Path(ws_arg)
        candidate_ws = raw_ws_path if raw_ws_path.is_absolute() else (base_ws / raw_ws_path)
        try:
            target_ws = validate_workspace_containment(
                candidate_ws,
                allowed_roots=allowed_roots,
                is_remote_request=False,
            )
        except OpenDVError as exc:
            return _err(2, "INVALID_WORKSPACE_PATH", str(exc))
    else:
        target_ws = base_ws

    if target_ws.suffix.lower() not in (".yaml", ".yml", ".json"):
        target_ws.mkdir(parents=True, exist_ok=True)
    target_contract = _resolve_workspace_target_contract(target_ws, meta.domain_namespace)

    # Strict greenfield workspace SHA-256 check (`"0" * 64` required when no contract exists)
    clean_ws_sha = str(ws_sha).strip().strip('"').lower()
    if not target_contract.is_file() and clean_ws_sha != "0" * 64:
        return _err(
            5,
            "ONT_EVO_CONCURRENT_MODIFICATION",
            f"Greenfield workspace promotion requires --workspace-sha256={'0' * 64}, "
            f"got '{ws_sha}'.",
        )

    ack_hist = bool(getattr(args, "acknowledge_historical_contract_warning", False))
    commit_svc = AtomicCommitService(
        session_manager=session_mgr,
        security_manager=session_mgr.security_manager,
    )
    try:
        res = commit_svc.promote_to_workspace(
            session_id=sid,
            target_workspace=target_contract,
            expected_workspace_sha256=clean_ws_sha,
            expected_head_sha256=str(head_sha).strip().strip('"'),
            rationale=rationale.strip(),
            actor=principal,
            actor_role=role_tier,
            acknowledge_historical_contract_warning=ack_hist,
            historical_contract_rationale=getattr(args, "historical_contract_rationale", None),
            allowed_workspace_roots=allowed_roots,
            is_remote_request=False,
            enforce_lineage=(not ack_hist),
            allow_noop=False,
        )
    except ForbiddenAdminRequiredError as exc:
        return _err(7, "FORBIDDEN_ADMIN_REQUIRED", str(exc))
    except PromotionRationaleRequiredError as exc:
        return _err(1, "PROMOTION_RATIONALE_REQUIRED", str(exc))
    except HistoricalContractWarningUnacknowledgedError as exc:
        return _err(4, "HISTORICAL_CONTRACT_WARNING_UNACKNOWLEDGED", str(exc))
    except HistoricalContractRationaleRequiredError as exc:
        return _err(1, "HISTORICAL_CONTRACT_RATIONALE_REQUIRED", str(exc))
    except LineageRootMismatchError as exc:
        return _err(5, "LINEAGE_ROOT_MISMATCH", str(exc))
    except ConcurrentModificationError as exc:
        return _err(5, "ONT_EVO_CONCURRENT_MODIFICATION", str(exc))
    except NoOpPromotionError as exc:
        return _err(1, "NO_OP_DELTA", str(exc))
    except CommitValidationError as exc:
        return _err_from_opendv_error(exc)
    except OpenDVError as exc:
        return _err_from_opendv_error(exc)

    return 0, {
        "session_id": res.session_id,
        "promoted_version": res.promoted_version,
        "head_sha256": res.head_sha256,
        "workspace_sha256": res.workspace_sha256,
        "pre_workspace_sha256": res.pre_workspace_sha256,
        "promoted_at": res.promoted_at,
        "target_contract_path": str(res.target_contract_path),
    }


def handle_bundle_export(
    args: argparse.Namespace, executor: LocalExecutor | None = None
) -> tuple[int, Any]:
    """20. `opendv ontology studio bundle export`."""
    auth_res = _resolve_studio_principal(args)
    if isinstance(auth_res[0], int):
        return auth_res  # type: ignore[return-value]
    principal, role_tier = auth_res  # type: ignore[misc]
    if role_tier == "automated":
        return _err(
            7,
            "AGENT_OPERATION_FORBIDDEN",
            "Automated agents are not permitted to export Studio session bundles.",
        )
    if role_tier == "viewer":
        return _err(
            7,
            "FORBIDDEN_ADMIN_REQUIRED",
            "Read-only viewer role is not permitted to export Studio session bundles.",
        )

    ctx = _require_session_context(args, executor)
    if isinstance(ctx[0], int):
        return ctx  # type: ignore[return-value]
    session_mgr, _, sdir, sid = ctx  # type: ignore[misc]

    out_zip = getattr(args, "out", None) or f"{sid}.odvstudio.zip"
    out_res = _validate_out_path(str(out_zip), executor)
    if isinstance(out_res, tuple):
        return out_res
    out_p = out_res

    ack_confidential = bool(getattr(args, "acknowledge_confidential_source_export", False))
    export_rationale = getattr(args, "export_rationale", None)

    try:
        res_path = export_bundle(
            session_dir=sdir,
            output_zip_path=out_p,
            security_manager=session_mgr.security_manager,
            acknowledge_confidential=ack_confidential,
            export_rationale=export_rationale,
            admin=(role_tier == "admin"),
            actor=principal,
            overwrite=True,
            allow_cli_output_path=(executor is None or getattr(executor, "is_local", True)),
        )
    except AdminRoleRequiredError as exc:
        return _err(7, "FORBIDDEN_ADMIN_REQUIRED", str(exc))
    except ConfidentialSourceExportUnacknowledgedError as exc:
        return _err(4, "CONFIDENTIAL_SOURCE_EXPORT_UNACKNOWLEDGED", str(exc))
    except ExportRationaleRequiredError as exc:
        return _err(1, "EXPORT_RATIONALE_REQUIRED", str(exc))
    except ArchiveSecurityViolationError as exc:
        return _err_from_opendv_error(exc)
    except OpenDVError as exc:
        return _err_from_opendv_error(exc)
    except (OSError, ValueError, RuntimeError, KeyError) as exc:
        return _err(1, "EXECUTION_FAILURE", str(exc))

    return 0, {
        "session_id": sid,
        "bundle_path": str(res_path),
        "size_bytes": res_path.stat().st_size,
    }


def handle_bundle_import(
    args: argparse.Namespace, executor: LocalExecutor | None = None
) -> tuple[int, Any]:
    """21. `opendv ontology studio bundle import -f <in_zip>`."""
    auth_res = _resolve_studio_principal(args)
    if isinstance(auth_res[0], int):
        return auth_res  # type: ignore[return-value]
    principal, role_tier = auth_res  # type: ignore[misc]
    if role_tier == "automated":
        return _err(
            7,
            "AGENT_OPERATION_FORBIDDEN",
            "Automated agents are not permitted to import Studio bundles.",
        )
    if role_tier == "viewer":
        return _err(
            7,
            "FORBIDDEN_ADMIN_REQUIRED",
            f"Role '{role_tier}' is not authorized to import Studio bundles.",
        )

    ack_unverified = bool(getattr(args, "acknowledge_unverified_import_attribution", False))
    force = bool(getattr(args, "force", False))
    if (ack_unverified or force) and role_tier != "admin":
        return _err(
            7,
            "FORBIDDEN_ADMIN_REQUIRED",
            "--acknowledge-unverified-import-attribution and --force require the 'admin' role.",
        )

    in_zip = getattr(args, "file", None)
    if not in_zip:
        return _err(2, "SYNTAX_INVALID_ARGUMENT", "Missing required --file flag")

    in_p = Path(in_zip)
    if not in_p.is_absolute() and executor:
        in_p = (executor.workspace_root / in_p).resolve()
    if not in_p.is_file():
        return _err(3, "FILE_NOT_FOUND", f"File not found: {in_zip}")

    studio_root = _get_studio_root(executor)
    sec_mgr = WorkspaceSecurityManager(studio_root=studio_root)
    new_sid = getattr(args, "new_session_id", None)
    if new_sid:
        err_new = _validate_session(new_sid)
        if err_new:
            return err_new

    ack_rationale = getattr(args, "acknowledgement_rationale", None)
    try:
        res = import_bundle(
            zip_path=in_p,
            target_dir=studio_root,
            security_manager=sec_mgr,
            new_session_id=new_sid,
            acknowledge_unverified_import_attribution=ack_unverified,
            acknowledgement_rationale=ack_rationale,
            imported_by=principal,
            force=force,
            actor_role=role_tier,
        )
    except SessionAlreadyExistsError as exc:
        return _err(6, "SESSION_ALREADY_EXISTS", str(exc))
    except ArchiveSecurityViolationError as exc:
        return _err_from_opendv_error(exc)
    except OpenDVError as exc:
        return _err_from_opendv_error(exc)
    except zipfile.BadZipFile as exc:
        return _err(4, "ONT_EVO_CONTRACT_VIOLATION", f"Corrupt bundle zip archive: {exc}")
    except FileExistsError as exc:
        return _err(6, "SESSION_ALREADY_EXISTS", str(exc))
    except (ValueError, KeyError, OSError, json.JSONDecodeError, RuntimeError) as exc:
        return _err(4, "IMPORT_GROUNDING_VIOLATION", str(exc))

    return 0, {
        "session_id": res.session_id,
        "status": "IMPORTED",
        "version": res.head_version,
        "head_sha256": res.head_sha256,
    }


def handle_bundle_clone(
    args: argparse.Namespace, executor: LocalExecutor | None = None
) -> tuple[int, Any]:
    """22. `opendv ontology studio bundle clone`."""
    auth_res = _resolve_studio_principal(args)
    if isinstance(auth_res[0], int):
        return auth_res  # type: ignore[return-value]
    principal, role_tier = auth_res  # type: ignore[misc]
    if role_tier == "automated":
        return _err(
            7,
            "AGENT_OPERATION_FORBIDDEN",
            "Automated agents are not permitted to clone Studio sessions.",
        )
    if role_tier == "viewer":
        return _err(
            7,
            "FORBIDDEN_ADMIN_REQUIRED",
            "Read-only viewer role is not permitted to clone Studio sessions.",
        )

    sid = getattr(args, "session", None)
    err = _validate_session(sid)
    if err:
        return err

    new_sid = getattr(args, "new_session_id", None) or f"s-{secrets.token_hex(8)}"
    err_new = _validate_session(new_sid)
    if err_new:
        return err_new

    session_mgr = _get_session_manager(executor)
    res_sess = _require_session(session_mgr, str(sid))
    if isinstance(res_sess, tuple):
        return res_sess

    sdir = session_mgr.get_session_dir(str(sid))
    if bool(compute_effective_session_taint(sdir)) and role_tier != "admin":
        return _err(
            7,
            "FORBIDDEN_ADMIN_REQUIRED",
            "Cloning a session containing confidential/tainted sources requires the 'admin' role.",
        )

    try:
        res = session_mgr.clone_session(
            str(sid),
            new_sid,
            actor=principal,
            actor_role=role_tier,
        )
    except (FileExistsError, SessionAlreadyExistsError):
        return _err(6, "SESSION_ALREADY_EXISTS", f"Session '{new_sid}' already exists")
    except OpenDVError as exc:
        return _err_from_opendv_error(exc)
    except (OSError, RuntimeError, ValueError) as exc:
        return _err(1, "INTERNAL_CLONE_ERROR", f"Failed to clone session: {exc}")

    return 0, {
        "source_session_id": sid,
        "new_session_id": res.session_id,
        "session_id": res.session_id,
        "status": "CLONED",
        "head_version": res.head_version,
        "head_sha256": res.head_sha256,
    }


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

_STUDIO_DISPATCH = {
    "init": handle_init,
    "create": handle_create,
    "delete": handle_delete,
    "list": handle_list,
    "status": handle_status,
    "upload": handle_upload,
    "parse": handle_parse,
    "clarify": handle_clarify,
    "answer": handle_answer,
    "construct": handle_construct,
    ("delta", "validate"): handle_delta_validate,
    ("delta", "apply"): handle_delta_apply,
    ("proposal", "list"): handle_proposal_list,
    ("proposal", "show"): handle_proposal_show,
    ("proposal", "accept"): handle_proposal_accept,
    ("proposal", "reject"): handle_proposal_reject,
    ("proposal", "rebase"): handle_proposal_rebase,
    "export": handle_export,
    "promote": handle_promote,
    ("bundle", "export"): handle_bundle_export,
    ("bundle", "import"): handle_bundle_import,
    ("bundle", "clone"): handle_bundle_clone,
}


def run_ontology_studio_command(
    args: argparse.Namespace,
    *,
    executor: LocalExecutor | None = None,
) -> tuple[int, Any]:
    """Top-level entrypoint for `opendv ontology studio <cmd>` in-process execution."""
    action = getattr(args, "studio_action", None)
    if not action:
        return _err(
            2,
            "SYNTAX_INVALID_ARGUMENT",
            "Missing studio action (e.g. init, create, delete, list, status, ...)",
        )

    if action in ("delta", "proposal", "bundle"):
        sub_action = getattr(args, f"studio_{action}_action", None)
        if not sub_action:
            return _err(2, "SYNTAX_INVALID_ARGUMENT", f"Missing {action} subcommand")
        handler = _STUDIO_DISPATCH.get((action, sub_action))
    else:
        handler = _STUDIO_DISPATCH.get(action)

    if not handler:
        return _err(2, "SYNTAX_INVALID_ARGUMENT", f"Unknown studio subcommand '{action}'")

    return handler(args, executor=executor)


def register_studio_subparsers(parser: argparse.ArgumentParser) -> None:
    """No-op hook retained for backwards compatibility.

    All `ontology studio` subcommands (including 3rd/4th-level `delta`, `proposal`,
    and `bundle` leaf commands) are now generated directly from `cli-spec.json` by
    `opendv_cli_common.parser_builder.build_argument_parser` (`P13::MAJ-02`).
    """
    _ = parser


__all__ = [
    "StudioLocalError",
    "handle_answer",
    "handle_bundle_clone",
    "handle_bundle_export",
    "handle_bundle_import",
    "handle_clarify",
    "handle_construct",
    "handle_create",
    "handle_delete",
    "handle_delta_apply",
    "handle_delta_validate",
    "handle_export",
    "handle_init",
    "handle_list",
    "handle_parse",
    "handle_promote",
    "handle_proposal_accept",
    "handle_proposal_list",
    "handle_proposal_rebase",
    "handle_proposal_reject",
    "handle_proposal_show",
    "handle_status",
    "handle_upload",
    "register_studio_subparsers",
    "resolve_studio_principal",
    "run_ontology_studio_command",
]
