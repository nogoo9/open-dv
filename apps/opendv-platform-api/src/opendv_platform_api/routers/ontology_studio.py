"""Ontology Construction Studio Platform REST & SSE API Router (Track 5B / Issues #215-#218).

Implements all canonical Studio REST & SSE endpoints conforming to:
- docs/20-ontology-studio-spec.md (§1.1 4-Tier RBAC, §3 REST API, §4 SSE, §7 Export/Promotion)
- docs/19-ontology-studio-architecture.md (§6 CAS, §7 ONT-EVO, §8 Security)
- schemas/v1/openapi.json & schemas/v1/error-codes.json
"""

from __future__ import annotations

import contextlib
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import urllib.parse
import uuid
from datetime import UTC, datetime
from pathlib import Path, PureWindowsPath
from typing import Annotated, Any, Final

import yaml
from fastapi import APIRouter, Depends, Form, Header, Query, Request, Response, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from starlette.concurrency import run_in_threadpool

from opendv_contracts.dtos.clarification import (
    ClarificationAnswerItem,
    ClarificationAnswerResponse,
    ClarificationQuestion,
    ClarifyRequest,
)
from opendv_contracts.dtos.ontology import (
    DESTRUCTIVE_OP_TYPES,
    OntologyDelta,
    OntologyValidationResult,
    StewardWaiverRecord,
    is_destructive_delta,
)
from opendv_contracts.errors import ErrorCode, OpenDVError
from opendv_contracts.jcs import dumps_jcs_bytes
from opendv_core.genesis import GENESIS_VERSION, create_genesis_ontology
from opendv_core.models.graph_a import GraphAOntology
from opendv_core.ontology_hashing import compute_ontology_sha256
from opendv_platform_api.deps import get_opendv_service
from opendv_platform_api.job_supervisor import (
    get_job_supervisor,
    run_clarify_job,
    run_construct_job,
    run_parse_job,
)
from opendv_platform_api.security.csrf import is_loopback_host, verify_csrf
from opendv_platform_api.security.error_catalog import StudioAPIError
from opendv_platform_api.security.studio_auth import (
    SERVER_MODE_REMOTE,
    StudioPrincipal,
    authenticate_studio_request,
    exchange_launch_nonce,
    get_active_session_role,
    get_server_mode,
    initialize_local_token,
    is_loopback_client,
    mint_launch_nonce,
)
from opendv_service.construction.clarification_harness import EMPTY_ANSWERS_SHA256
from opendv_service.export.rdf_exporter import export_ontology
from opendv_service.ontology.applier import compute_next_semver
from opendv_service.ontology.rebase import check_rebase_conflicts
from opendv_service.ontology.validator import (
    GovernanceValidator,
    build_workspace_deployment_provider,
)
from opendv_service.studio.atomic_commit import (
    AtomicCommitService,
    ConcurrentModificationError,
    _load_grandfathered_concept_uris,
    _LocalWaiverVerifier,
    collect_touched_concept_uris,
    delete_source,
    parse_semver_tuple,
    parse_strong_etag,
)
from opendv_service.studio.bundle import (
    ArchiveSecurityViolationError,
    ImportGroundingRationaleRequiredError,
    ImportGroundingViolationError,
    compute_effective_session_taint,
    export_bundle,
    import_bundle,
)
from opendv_service.studio.locks import reentrant_session_lock
from opendv_service.studio.session_manager import (
    InvalidOntologyPayloadError,
    InvalidSessionIdentifierError,
    SessionAlreadyExistsError,
    SessionMetadata,
    SessionNotFoundError,
    StudioSessionManager,
    TamperEvidentChainCorruptError,
    _atomic_write_session_json,
    append_signed_audit_record,
    normalize_version_tag,
    sanitize_domain_namespace,
    validate_session_id,
)
from opendv_service.studio.workspace_security import (
    WINDOWS_RESERVED_DEVICES,
    replace_with_retry,
    set_owner_only_permissions,
    validate_workspace_containment,
)
from opendv_sources.ingestion.layout_evaluator import TesseractOCRRouter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ontology/studio", tags=["ontology-studio"])

UPLOAD_STREAM_CHUNK_BYTES: Final[int] = 64 * 1024  # 64 KiB
MAX_SOURCE_UPLOAD_BYTES: Final[int] = 100 * 1024 * 1024  # 100 MiB
MAX_BUNDLE_IMPORT_BYTES: Final[int] = 500 * 1024 * 1024  # 500 MiB


# ---------------------------------------------------------------------------
# Request & Response Wire Models
# ---------------------------------------------------------------------------


class CreateSessionRequest(BaseModel):
    session_id: str | None = None
    domain_namespace: str = "default"
    enterprise_owner: str = "Enterprise"
    description: str | None = None
    from_workspace: str | None = None


class StudioSessionSummaryDTO(BaseModel):
    session_id: str
    domain_namespace: str
    enterprise_owner: str
    status: str
    active_version: str
    created_at: str
    updated_at: str


class StudioSessionDTO(BaseModel):
    session_id: str
    domain_namespace: str
    enterprise_owner: str
    status: str
    active_version: str
    head_sha256: str
    created_at: str
    updated_at: str


class StudioSessionDetailDTO(BaseModel):
    session_id: str
    domain_namespace: str
    enterprise_owner: str
    status: str
    active_version: str
    head_sha256: str
    created_at: str
    updated_at: str
    sources_count: int
    proposals_count: int
    versions_count: int
    grandfathered_concept_uris: list[str] = Field(default_factory=list)


class StudioSessionStatusDTO(BaseModel):
    session_id: str
    status: str
    active_version: str
    head_sha256: str
    active_jobs: int = 0
    taint_classification: str = "UNTAINTED"


class ManifestDTO(BaseModel):
    session_id: str
    files: list[dict[str, Any]]
    total_bytes: int


class SourceDocumentDTO(BaseModel):
    source_id: str
    source_sha256: str
    file_name: str
    filename: str = ""
    size_bytes: int
    byte_count: int = 0
    mime_type: str
    do_not_send_to_agent: bool
    tombstoned: bool = False


class SourceUploadResponse(BaseModel):
    source_id: str
    source_sha256: str
    file_name: str
    filename: str = ""
    size_bytes: int
    byte_count: int = 0
    mime_type: str
    do_not_send_to_agent: bool


class ParseRequest(BaseModel):
    source_ids: list[str] | None = None
    ocr_mode: str = "auto"


class StudioClarifyRequest(ClarifyRequest):
    base_version: str | None = None


class ConstructRequest(BaseModel):
    base_version: str | None = None
    base_sha256: str | None = None
    auto_commit: bool = False
    fibo_modules: list[str] = Field(
        default_factory=lambda: ["FND", "BE", "FBC", "SEC", "LOAN", "IND"]
    )
    mode: str | None = None


class JobHandleDTO(BaseModel):
    job_id: str
    session_id: str
    job_type: str
    status: str
    created_at: str


class JobStatusDTO(BaseModel):
    job_id: str
    session_id: str
    job_type: str
    status: str
    stage: str
    pct_complete: int
    created_at: str
    finished_at: str | None = None
    error: dict[str, Any] | None = None
    result: dict[str, Any] | None = None


class StudioClarificationAnswerRequest(BaseModel):
    answers: list[ClarificationAnswerItem | dict[str, Any]] | dict[str, Any] = Field(
        default_factory=list
    )
    expected_answers_sha256: str | None = None


class OntologyDeltaRequest(BaseModel):
    delta_id: str | None = None
    parent_sha256: str | None = None
    base_sha256: str | None = None
    domain_namespace: str | None = None
    base_version: str | None = None
    target_version: str | None = None
    operations: list[dict[str, Any]]
    author: str | None = None
    rationale: str | None = None
    low_confidence_waiver: bool = False
    grounding_rationale: str | None = None
    ungrounded_concept: list[str] | str | None = None
    evidence_grounding_file: str | None = None


class DeltaCommitResponse(BaseModel):
    session_id: str
    target_version: str
    target_sha256: str
    parent_sha256: str
    delta_id: str
    bump_type: str
    applied_operations_count: int


class ProposalCreateRequest(BaseModel):
    proposal_id: str | None = None
    base_version: str | None = None
    base_sha256: str | None = None
    operations: list[dict[str, Any]]
    rationale: str | None = None


class ProposalRecordDTO(BaseModel):
    proposal_id: str
    session_id: str
    base_version: str
    base_sha256: str
    status: str
    operations: list[dict[str, Any]]
    proposer: str
    created_at: str
    rationale: str | None = None


class ProposalAcceptRequest(BaseModel):
    base_sha256: str | None = None
    low_confidence_waiver: bool = False
    self_acceptance_override: bool = False
    grounding_rationale: str | None = None
    evidence_grounding_file: str | None = None
    ungrounded_concept: list[str] | str | None = None


class ProposalRebaseRequest(BaseModel):
    base_sha256: str | None = None


class RejectProposalRequest(BaseModel):
    reason: str | None = None


class WaiverSubmissionRequest(BaseModel):
    model_config = ConfigDict(extra="allow")

    waiver_id: str | None = None
    proposal_id: str | None = None
    proposal_delta_sha256: str | None = None
    concept_uri: str | None = None
    target_concept_uris: list[str] | None = None
    concept_uris: list[str] | None = None
    ungrounded_concepts: list[str] | None = None
    base_sha256: str | None = None
    rationale: str = ""
    authorized_by: str | None = None
    steward: str | None = None
    authorized_role: str | None = None
    signature_or_token: str | None = None
    created_at: str | None = None
    expires_at: str | None = None


class FeedbackSubmissionRequest(BaseModel):
    rating: str | int = Field(..., description="Feedback rating ('up', 'down', or numeric score)")
    comment: str | None = Field(default=None, max_length=4000)
    concept_uri: str | None = None
    proposal_id: str | None = None


class FeedbackSubmissionResponse(BaseModel):
    status: str
    feedback_id: str
    session_id: str
    active_version: str
    head_sha256: str
    timestamp: str


class CloneSessionRequest(BaseModel):
    new_session_id: str | None = None


class PromoteRequest(BaseModel):
    workspace_sha256: str | None = None
    head_sha256: str | None = None
    rationale: str = ""
    workspace: str | None = None
    acknowledge_historical_contract_warning: bool = False
    historical_contract_rationale: str | None = None


class PromoteResponse(BaseModel):
    session_id: str
    promoted_version: str
    head_sha256: str
    workspace_sha256: str
    promoted_at: str
    target_contract_path: str


class BundleExportRequest(BaseModel):
    acknowledge_confidential_source_export: bool = False
    export_rationale: str | None = None


class InitStudioRequest(BaseModel):
    domain_namespace: str | None = None
    enterprise_owner: str | None = None
    force: bool = False


class InitStudioResponse(BaseModel):
    status: str
    token_path: str
    principal: str
    role: str | None = None
    local_token: str | None = None
    domain_namespace: str | None = None


class LaunchNonceResponse(BaseModel):
    launch_nonce: str
    nonce: str
    expires_in: int = 60


class TokenExchangeRequest(BaseModel):
    launch_nonce: str | None = None
    nonce: str | None = None


class TokenExchangeResponse(BaseModel):
    status: str
    access_token: str | None = None
    principal: str
    role: str | None = None


# ---------------------------------------------------------------------------
# Dependency Injectors & 4-Tier RBAC Guards
# ---------------------------------------------------------------------------


def get_studio_root(request: Request) -> Path:
    """Resolve Studio CAS storage root directory."""
    service = get_opendv_service(request)
    custom_root = os.environ.get("OPENDV_STUDIO_ROOT")
    if custom_root:
        root = Path(custom_root).resolve()
    else:
        root = (service.workspace_root / ".opendv" / "ontology_studio").resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def _resolve_allowed_workspace_roots(
    session_mgr: StudioSessionManager,
) -> list[Path]:
    """Resolve allowed workspace containment roots from OPENDV_WORKSPACE_ROOTS or default."""
    env_roots = os.environ.get("OPENDV_WORKSPACE_ROOTS")
    if env_roots and env_roots.strip():
        parsed = [Path(p.strip()).resolve() for p in env_roots.split(os.pathsep) if p.strip()]
        if parsed:
            return parsed
    return [(session_mgr.workspace_root or session_mgr.studio_root.parent).resolve()]


def _resolve_deployment_workspace_root(
    request: Request,
    session_mgr: StudioSessionManager,
) -> Path | None:
    """Resolve workspace root for deployed Data Vault contract discovery (ONT-EVO-001)."""
    if os.environ.get("OPENDV_WORKSPACE_ROOTS", "").strip():
        return _resolve_allowed_workspace_roots(session_mgr)[0]
    service = get_opendv_service(request)
    if os.environ.get("OPENDV_STUDIO_ROOT", "").strip() and not getattr(
        service, "_explicit_workspace", False
    ):
        if session_mgr.studio_root.parent.name == ".opendv":
            return session_mgr.studio_root.parent.parent.resolve()
        return session_mgr.studio_root.parent.resolve()
    return service.workspace_root.resolve()


def get_session_manager(
    request: Request,
    studio_root: Annotated[Path, Depends(get_studio_root)],
) -> StudioSessionManager:
    """Create StudioSessionManager configured with active workspace roots."""
    service = get_opendv_service(request)
    mgr = StudioSessionManager(studio_root=studio_root, workspace_root=service.workspace_root)
    mgr.workspace_root = service.workspace_root
    mgr.deployment_workspace_root = _resolve_deployment_workspace_root(request, mgr)
    return mgr


def require_viewer(
    request: Request,
    studio_root: Annotated[Path, Depends(get_studio_root)],
) -> StudioPrincipal:
    """Authenticate read-only / validation caller (viewer, editor, admin, or automated)."""
    verify_csrf(request)
    principal = authenticate_studio_request(request, studio_root, required_role="viewer")
    request.state.studio_principal = principal
    return principal


def require_editor(
    request: Request,
    studio_root: Annotated[Path, Depends(get_studio_root)],
) -> StudioPrincipal:
    """Authenticate human editor or admin caller (rejecting viewer and automated/agent:*)."""
    verify_csrf(request)
    principal = authenticate_studio_request(request, studio_root, required_role="editor")
    request.state.studio_principal = principal
    return principal


def require_editor_or_automated(
    request: Request,
    studio_root: Annotated[Path, Depends(get_studio_root)],
) -> StudioPrincipal:
    """Authenticate human editor/admin or allow-listed automated caller (rejecting viewer)."""
    verify_csrf(request)
    principal = authenticate_studio_request(request, studio_root, required_role="viewer")
    request.state.studio_principal = principal
    if principal.role == "viewer" and not principal.is_automated:
        raise StudioAPIError(
            "SERVER_OPERATION_FORBIDDEN",
            "Read-only 'viewer' role is forbidden from mutating Studio state.",
        )
    return principal


def require_admin(
    request: Request,
    studio_root: Annotated[Path, Depends(get_studio_root)],
) -> StudioPrincipal:
    """Authenticate human administrator caller (rejecting viewer, editor, and automated)."""
    verify_csrf(request)
    principal = authenticate_studio_request(request, studio_root, required_role="admin")
    request.state.studio_principal = principal
    return principal


# Backward-compatible alias for external imports if any
require_principal = require_viewer


# ---------------------------------------------------------------------------
# Internal Helpers (Errors, Strong ETag, SemVer, Waivers, Streaming Upload)
# ---------------------------------------------------------------------------


def _raise_from_opendv_error(exc: OpenDVError) -> StudioAPIError:
    """Convert a domain OpenDVError into a canonical catalog StudioAPIError."""
    raw_code = getattr(exc, "error_code", ErrorCode.INTERNAL_ERROR)
    code_str = raw_code.value if hasattr(raw_code, "value") else str(raw_code)
    details = getattr(exc, "details", None)
    extra = dict(details) if isinstance(details, dict) and details else None
    return StudioAPIError(code_str, str(exc), extensions=extra)


def _get_session_or_404(
    session_mgr: StudioSessionManager,
    session_id: str,
) -> SessionMetadata:
    _validate_session_param(session_id)
    try:
        return session_mgr.get_session(session_id)
    except SessionNotFoundError as exc:
        raise StudioAPIError("SESSION_NOT_FOUND", str(exc)) from exc


def _derive_session_status(sdir: Path, meta: SessionMetadata) -> str:
    """Return 'PROMOTED' when session has active promotion metadata, else meta.status."""
    if meta.status == "ACTIVE" and ((sdir / "promotion.json").is_file() or meta.promoted_versions):
        return "PROMOTED"
    return meta.status


def _parse_strong_etag_or_400(raw_etag: str, header_name: str = "If-Match") -> str:
    """Parse strong 64-hex ETag or raise 400 INVALID_PRECONDITION_HEADER."""
    try:
        return parse_strong_etag(raw_etag)
    except OpenDVError as exc:
        raise StudioAPIError(
            "INVALID_PRECONDITION_HEADER",
            f"Invalid {header_name} precondition '{raw_etag}': {exc}",
        ) from exc


async def _stream_upload_to_tempfile(
    upload: UploadFile,
    scratch_dir: Path,
    *,
    max_bytes: int,
    prefix: str,
    suffix: str,
) -> tuple[Path, int]:
    """Stream multipart UploadFile to disk in 64 KiB chunks enforcing strict byte caps."""
    scratch_dir.mkdir(parents=True, exist_ok=True)
    tmp_path = scratch_dir / f".{prefix}_{secrets.token_hex(8)}{suffix}"
    total_bytes = 0
    try:
        with tmp_path.open("wb") as fp:
            while True:
                chunk = await upload.read(UPLOAD_STREAM_CHUNK_BYTES)
                if not chunk:
                    break
                total_bytes += len(chunk)
                if total_bytes > max_bytes:
                    raise StudioAPIError(
                        "PAYLOAD_TOO_LARGE",
                        f"Uploaded payload exceeds maximum allowed size of {max_bytes} bytes.",
                    )
                fp.write(chunk)
        set_owner_only_permissions(tmp_path)
        return tmp_path, total_bytes
    except BaseException:
        if tmp_path.exists():
            with contextlib.suppress(OSError):
                tmp_path.unlink()
        raise


def _derive_genesis_target_version(operations: list[Any]) -> str:
    """Consistently derive genesis SemVer target based on canonical concepts."""
    has_cbc = any(
        (isinstance(op, dict) and op.get("op_type") in ("add_concept", "AddConceptOp"))
        or getattr(op, "op_type", None) in ("add_concept", "AddConceptOp")
        for op in operations
    )
    return "1.0.0" if has_cbc else "0.1.0"


def _normalize_delta_operation(op: Any) -> Any:
    """Normalize operation dict from agent or wire representation to normative contract format."""
    if isinstance(op, dict):
        op_dict = dict(op)
        op_type = op_dict.get("op_type")
        if isinstance(op_type, str):
            if (
                op_type in ("AddConceptOp", "add_concept")
                and "concept" in op_dict
                and isinstance(op_dict["concept"], dict)
            ):
                c = dict(op_dict.pop("concept"))
                norm = {
                    "op_type": "add_concept",
                    "op_id": op_dict.get("op_id", f"op-{uuid.uuid4().hex[:8]}"),
                }
                norm.update(c)
                norm.update(op_dict)
                norm["op_type"] = "add_concept"
                return norm

            if op_type.endswith("Op") and len(op_type) > 2:
                base = op_type[:-2]
                snake = re.sub(r"(?<!^)(?=[A-Z])", "_", base).lower()
                op_dict["op_type"] = snake
                return op_dict
        return op_dict
    return op


def _load_session_version_ontology(
    sdir: Path,
    meta: SessionMetadata,
    version_str: str | None = None,
) -> GraphAOntology:
    """Load a committed GraphAOntology snapshot or synthesize genesis 0.0.0."""
    ver = (version_str or meta.head_version).lstrip("v")
    if ver == GENESIS_VERSION:
        ont_file = sdir / "versions" / normalize_version_tag(ver) / "ontology.json"
        if ont_file.is_file():
            try:
                return GraphAOntology.model_validate_json(ont_file.read_text(encoding="utf-8"))
            except (
                OSError,
                UnicodeDecodeError,
                json.JSONDecodeError,
                yaml.YAMLError,
                ValidationError,
                TypeError,
                ValueError,
            ) as exc:
                raise StudioAPIError(
                    "CORRUPT_SESSION_RECORD",
                    f"Corrupt ontology artifact for version 'v{ver}': {exc}",
                ) from exc
        return create_genesis_ontology(
            domain_namespace=sanitize_domain_namespace(meta.domain_namespace),
            enterprise_owner=meta.enterprise_owner or "Enterprise Architecture",
        )

    vdir = sdir / "versions" / normalize_version_tag(ver)
    ont_json = vdir / "ontology.json"
    if ont_json.is_file():
        try:
            return GraphAOntology.model_validate_json(ont_json.read_text(encoding="utf-8"))
        except (
            OSError,
            UnicodeDecodeError,
            json.JSONDecodeError,
            yaml.YAMLError,
            ValidationError,
            TypeError,
            ValueError,
        ) as exc:
            raise StudioAPIError(
                "CORRUPT_SESSION_RECORD",
                f"Corrupt ontology artifact for version 'v{ver}': {exc}",
            ) from exc
    ont_yaml = vdir / "ontology.yaml"
    if ont_yaml.is_file():
        try:
            return GraphAOntology.model_validate(
                yaml.safe_load(ont_yaml.read_text(encoding="utf-8"))
            )
        except (
            OSError,
            UnicodeDecodeError,
            json.JSONDecodeError,
            yaml.YAMLError,
            ValidationError,
            TypeError,
            ValueError,
        ) as exc:
            raise StudioAPIError(
                "CORRUPT_SESSION_RECORD",
                f"Corrupt ontology artifact for version 'v{ver}': {exc}",
            ) from exc
    raise StudioAPIError(
        "CORRUPT_SESSION_STATE",
        f"Ontology artifact missing for version 'v{ver}' in session '{meta.session_id}'.",
    )


def _compute_expected_semver_for_ops(
    base_version: str,
    base_sha256: str,
    norm_ops: list[Any],
    base_ontology: GraphAOntology | None = None,
    domain_namespace: str | None = None,
) -> str:
    """Compute exact required target SemVer BEFORE full OntologyDelta instantiation (MAJOR-08)."""
    clean_base = base_version.lstrip("v")
    if clean_base == GENESIS_VERSION:
        return _derive_genesis_target_version(norm_ops)

    # First parse operations using a permissive MAJOR/MINOR seed target so OntologyDelta
    # validates individual OntologyOperation schemas without failing _validate_semver_bump!
    b_maj, b_min, b_pat = parse_semver_tuple(clean_base)
    seed_target = f"{b_maj + 1}.0.0"
    validated_seed = OntologyDelta(
        delta_id=f"delta-probe-{uuid.uuid4().hex[:8]}",
        parent_sha256=base_sha256,
        base_version=clean_base,
        target_version=seed_target,
        domain_namespace=domain_namespace,
        operations=norm_ops,
    )
    if not validated_seed.operations:
        return f"{b_maj}.{b_min}.{b_pat + 1}"

    probe = validated_seed.model_copy(update={"target_ontology_version": None})
    probe.__pydantic_fields_set__.discard("target_ontology_version")
    probe.__pydantic_fields_set__.discard("target_version")
    return compute_next_semver(clean_base, probe, base_ontology)


def _normalize_ungrounded_list(raw: list[str] | str | None) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        return [raw.strip()] if raw.strip() else []
    return [str(item).strip() for item in raw if str(item).strip()]


def _proposal_uuid_from_str(proposal_id: str | None) -> uuid.UUID:
    """Deterministically map a string proposal ID or UUID string to a valid UUIDv4."""
    if proposal_id:
        with contextlib.suppress(ValueError):
            parsed = uuid.UUID(str(proposal_id))
            if parsed.version == 4:
                return parsed
        digest16 = hashlib.sha256(f"opendv:proposal:{proposal_id}".encode()).digest()[:16]
        return uuid.UUID(bytes=digest16, version=4)
    return uuid.uuid4()


def _verify_waiver_key_header_if_present(
    request: Request,
    session_mgr: StudioSessionManager,
) -> None:
    """Verify X-OpenDV-Waiver-Key in constant time whenever supplied or required via env."""
    raw_hdr = request.headers.get("x-opendv-waiver-key")
    env_expected = (os.environ.get("OPENDV_WAIVER_KEY") or "").strip()
    if raw_hdr is None and not env_expected:
        return

    candidate = (raw_hdr or "").strip()
    if candidate.lower().startswith("bearer "):
        candidate = candidate[7:].strip()
    if not candidate:
        raise StudioAPIError(
            "FORBIDDEN_ADMIN_REQUIRED",
            "Administrative waivers strictly require authenticated waiver credentials.",
        )

    if env_expected:
        if not hmac.compare_digest(candidate.encode("utf-8"), env_expected.encode("utf-8")):
            raise StudioAPIError(
                "FORBIDDEN_ADMIN_REQUIRED",
                "Invalid steward waiver credentials.",
            )
        return

    expected_bytes = session_mgr.security_manager.get_waiver_key()
    expected_hex = expected_bytes.hex().lower()
    cand_lower = candidate.lower()
    try:
        cand_bytes = bytes.fromhex(cand_lower)
    except ValueError:
        cand_bytes = candidate.encode("utf-8")

    is_valid = hmac.compare_digest(cand_lower, expected_hex) or hmac.compare_digest(
        cand_bytes, expected_bytes
    )
    if not is_valid:
        raise StudioAPIError(
            "FORBIDDEN_ADMIN_REQUIRED",
            "Invalid steward waiver credentials.",
        )


def _parse_waiver_expires_at(raw_expires_at: Any) -> tuple[bool, datetime | None]:
    """Return `(is_expired, parsed_utc_dt)` for a stored waiver `expires_at` field.

    Returns `(False, None)` when `expires_at` is absent/None/empty.
    Fails closed `(True, None)` when `expires_at` is malformed or `<= now(UTC)`.
    """
    if raw_expires_at is None:
        return False, None
    if isinstance(raw_expires_at, str) and not raw_expires_at.strip():
        return False, None
    if not isinstance(raw_expires_at, str):
        return True, None

    text = raw_expires_at.strip()
    if text.endswith(("Z", "z")):
        text = f"{text[:-1]}+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return True, None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    parsed_utc = parsed.astimezone(UTC)
    if parsed_utc <= datetime.now(UTC):
        return True, None
    return False, parsed_utc


def _has_valid_waiver_authorization(
    request: Request,
    session_mgr: StudioSessionManager,
    principal: StudioPrincipal,
) -> bool:
    """Return True when caller holds admin role or a valid X-OpenDV-Waiver-Key for dry-run."""
    raw_hdr = request.headers.get("x-opendv-waiver-key")
    if not principal.is_admin and raw_hdr is None:
        return False
    try:
        _verify_waiver_key_header_if_present(request, session_mgr)
    except StudioAPIError:
        return False
    return True


def _normalize_proposal_record(
    raw: dict[str, Any],
    session_id: str,
    fallback_proposal_id: str | None = None,
) -> dict[str, Any]:
    """Normalize flat ProposalRecord dicts and nested OntologyProposal dicts seamlessly."""
    delta_obj = raw.get("delta")
    delta_dict = delta_obj if isinstance(delta_obj, dict) else {}

    proposal_id = str(raw.get("proposal_id") or fallback_proposal_id or "")
    raw_base_ver = (
        raw.get("base_version")
        or raw.get("base_ontology_version")
        or delta_dict.get("base_ontology_version")
        or delta_dict.get("base_version")
        or GENESIS_VERSION
    )
    base_version = str(raw_base_ver).lstrip("v") or GENESIS_VERSION

    raw_base_sha = (
        raw.get("base_sha256")
        if raw.get("base_sha256") not in (None, "")
        else (
            raw.get("base_ontology_sha256")
            if raw.get("base_ontology_sha256") not in (None, "")
            else (delta_dict.get("parent_sha256") or delta_dict.get("base_sha256") or "")
        )
    )
    base_sha256 = str(raw_base_sha)

    raw_status = raw.get("status")
    status = (
        str(raw_status).upper() if isinstance(raw_status, str) and raw_status.strip() else "PENDING"
    )

    if "operations" in raw and isinstance(raw.get("operations"), list):
        operations = list(raw["operations"])
    elif isinstance(delta_dict.get("operations"), list):
        operations = list(delta_dict["operations"])
    else:
        operations = []

    proposer = str(
        raw.get("proposer")
        or delta_dict.get("author")
        or raw.get("recommender_id")
        or "agent:unknown"
    )
    created_at = str(
        raw.get("created_at")
        or raw.get("created_at_utc")
        or delta_dict.get("created_at")
        or datetime.now(UTC).isoformat()
    )
    rationale_val = raw.get("rationale")
    if rationale_val is None:
        rationale_val = delta_dict.get("rationale")
    rationale = str(rationale_val) if rationale_val is not None else None

    normalized = dict(raw)
    normalized.update(
        {
            "proposal_id": proposal_id,
            "session_id": session_id,
            "base_version": base_version,
            "base_sha256": base_sha256,
            "status": status,
            "operations": operations,
            "proposer": proposer,
            "created_at": created_at,
            "rationale": rationale,
        }
    )
    return normalized


def _ensure_session_version_exists(sdir: Path, base_version: str | None) -> None:
    """Validate version parameter syntax and confirm `sdir / 'versions' / vX.Y.Z` exists."""
    if base_version is None:
        return
    _validate_version_param(base_version)
    clean_ver = base_version.lstrip("v")
    versions_dir = (sdir / "versions").resolve()
    vdir = (versions_dir / normalize_version_tag(clean_ver)).resolve()
    if not vdir.is_relative_to(versions_dir):
        raise StudioAPIError(
            "INVALID_WORKSPACE_PATH",
            f"Path traversal detected in version identifier '{base_version}'.",
        )
    if not vdir.is_dir():
        raise StudioAPIError(
            "VERSION_NOT_FOUND",
            f"Version '{base_version}' not found in session.",
        )


def _compute_stored_proposal_delta_sha256(
    sdir: Path,
    meta: SessionMetadata,
    raw_prop: dict[str, Any],
    proposal_id: str,
) -> str:
    """Compute canonical RFC 8785 SHA-256 of a stored proposal's effective delta (P10a::MAJ-01)."""
    prop_data = _normalize_proposal_record(
        raw_prop, session_id=meta.session_id, fallback_proposal_id=proposal_id
    )
    raw_ops = prop_data.get("operations", [])
    ops = [_normalize_delta_operation(op) for op in raw_ops]
    prop_base_ver = (
        str(prop_data.get("base_version") or GENESIS_VERSION).lstrip("v") or GENESIS_VERSION
    )
    prop_base_sha = str(prop_data.get("base_sha256") or "")
    base_ont = _load_session_version_ontology(sdir, meta, prop_base_ver)
    eff_parent_sha = prop_base_sha or compute_ontology_sha256(base_ont)

    try:
        raw_delta = raw_prop.get("delta")
        if isinstance(raw_delta, dict):
            if "operations" not in raw_prop:
                return OntologyDelta.model_validate(raw_delta).canonical_sha256()

            cleaned_delta = {
                k: v
                for k, v in raw_delta.items()
                if k not in ("base_sha256", "base_version", "target_version")
            }
            eff_ns = cleaned_delta.get("domain_namespace") or meta.domain_namespace
            t_ver = _compute_expected_semver_for_ops(
                base_version=prop_base_ver,
                base_sha256=eff_parent_sha,
                norm_ops=ops,
                base_ontology=base_ont,
                domain_namespace=eff_ns,
            )
            cleaned_delta.update(
                {
                    "parent_sha256": (
                        ""
                        if prop_base_sha == "" and raw_delta.get("parent_sha256") == ""
                        else eff_parent_sha
                    ),
                    "base_ontology_version": prop_base_ver,
                    "target_ontology_version": t_ver,
                    "operations": ops,
                }
            )
            return OntologyDelta.model_validate(cleaned_delta).canonical_sha256()

        t_ver = _compute_expected_semver_for_ops(
            base_version=prop_base_ver,
            base_sha256=eff_parent_sha,
            norm_ops=ops,
            base_ontology=base_ont,
            domain_namespace=meta.domain_namespace,
        )
        reconstructed = OntologyDelta(
            delta_id=str(raw_prop.get("delta_id") or f"delta-prop-{prop_data['proposal_id']}"),
            parent_sha256=eff_parent_sha,
            base_version=prop_base_ver,
            target_version=t_ver,
            domain_namespace=meta.domain_namespace,
            operations=ops,
            author=prop_data.get("proposer"),
            rationale=prop_data.get("rationale"),
        )
        return reconstructed.canonical_sha256()
    except OpenDVError as exc:
        raise _raise_from_opendv_error(exc) from exc
    except (ValidationError, ValueError, TypeError) as exc:
        raise StudioAPIError(
            "CORRUPT_SESSION_RECORD",
            f"Corrupt proposal delta in proposal '{proposal_id}': {exc}",
        ) from exc


def _compute_submitted_request_delta_sha256(
    *,
    payload: OntologyDeltaRequest,
    norm_ops: list[Any],
    fallback_base_version: str,
    fallback_parent_sha256: str,
    fallback_target_version: str,
    fallback_sha256: str,
) -> str:
    """Compute canonical SHA-256 of caller-submitted pre-normalization delta when delta_id set."""
    if not payload.delta_id:
        return fallback_sha256
    raw_parent = (
        payload.parent_sha256
        if payload.parent_sha256 is not None
        else (payload.base_sha256 if payload.base_sha256 is not None else fallback_parent_sha256)
    )
    try:
        sub_delta = OntologyDelta(
            delta_id=payload.delta_id,
            parent_sha256=raw_parent,
            base_version=(payload.base_version or fallback_base_version).lstrip("v"),
            target_version=(payload.target_version or fallback_target_version).lstrip("v"),
            domain_namespace=payload.domain_namespace,
            operations=norm_ops,
            author=payload.author,
            rationale=payload.rationale,
        )
        return sub_delta.canonical_sha256()
    except (ValidationError, ValueError, TypeError):
        return fallback_sha256


def _build_and_sign_commit_waivers(
    *,
    sdir: Path,
    session_mgr: StudioSessionManager,
    base_ontology: GraphAOntology,
    delta: OntologyDelta,
    principal: StudioPrincipal,
    low_confidence_waiver: bool,
    grounding_rationale: str | None,
    explicit_ungrounded: list[str] | str | None,
    proposal_id: str | None,
    submitted_delta_sha256: str | None = None,
) -> list[StewardWaiverRecord]:
    """Assemble cryptographically signed K_waiver records from inline or pre-authorized waivers."""
    sec_mgr = session_mgr.security_manager
    delta_sha = delta.canonical_sha256()
    expected_delta_sha = delta_sha.strip().lower()
    clean_submitted_sha = (submitted_delta_sha256 or "").strip().lower()
    base_sha = delta.base_sha256
    expected_base_sha = (base_sha or "").strip().lower()
    signed_waivers: list[StewardWaiverRecord] = []

    # 1. Load pre-authorized session waivers from <session_dir>/waivers.json (if any)
    waivers_file = sdir / "waivers.json"
    if waivers_file.is_file():
        try:
            stored_entries = json.loads(waivers_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise StudioAPIError(
                "CORRUPT_SESSION_RECORD",
                f"Corrupt waivers.json in session '{sdir.name}': {exc}",
            ) from exc

        if isinstance(stored_entries, list):
            for entry in stored_entries:
                if not isinstance(entry, dict):
                    continue
                envelope = entry.get("envelope")
                sig = entry.get("hmac_signature")
                if not isinstance(envelope, dict) or not isinstance(sig, str):
                    raise StudioAPIError(
                        "INVALID_GROUNDING_WAIVER",
                        "Stored steward waiver entry lacks valid cryptographic envelope.",
                    )
                canonical_env = dumps_jcs_bytes(envelope)
                expected_sig = sec_mgr.sign_waiver(canonical_env)
                if not hmac.compare_digest(sig, expected_sig):
                    raise StudioAPIError(
                        "INVALID_GROUNDING_WAIVER",
                        "Stored steward waiver failed K_waiver cryptographic verification.",
                    )

                # P10a MAJ-01: Enforce expires_at (skip expired or malformed timestamps)
                raw_exp = envelope.get("expires_at")
                if raw_exp is None and isinstance(entry.get("record"), dict):
                    raw_exp = entry["record"].get("expires_at")
                is_expired, parsed_exp = _parse_waiver_expires_at(raw_exp)
                if is_expired:
                    continue

                # P10a MAJ-01: Enforce strict base_sha256 binding (reject empty or 0*64 wildcard)
                env_base = str(envelope.get("base_sha256") or "").strip().lower()
                if (
                    not env_base
                    or env_base == "0" * 64
                    or not expected_base_sha
                    or not hmac.compare_digest(env_base, expected_base_sha)
                ):
                    continue

                # P10a MAJ-01: Enforce strict proposal_id scoping
                raw_env_prop = envelope.get("proposal_id")
                env_prop = str(raw_env_prop).strip() if raw_env_prop is not None else ""
                if proposal_id is not None:
                    if env_prop and env_prop != str(proposal_id):
                        continue
                else:
                    if env_prop:
                        continue

                # P10a MAJ-01: Enforce strict authorized_role == "admin" and non-empty authorized_by
                raw_auth_role = envelope.get("authorized_role")
                raw_auth_by = envelope.get("authorized_by")
                auth_role = str(raw_auth_role).strip() if isinstance(raw_auth_role, str) else ""
                auth_by = str(raw_auth_by).strip() if isinstance(raw_auth_by, str) else ""
                if auth_role != "admin" or not auth_by:
                    continue

                # P10a MAJ-01: Enforce proposal_delta_sha256 binding (prevent cross-delta replay)
                raw_env_delta_sha = envelope.get("proposal_delta_sha256")
                env_delta_sha = (
                    str(raw_env_delta_sha).strip().lower() if raw_env_delta_sha is not None else ""
                )
                if env_delta_sha:
                    if env_delta_sha == "0" * 64:
                        continue
                    matches_commit_delta = hmac.compare_digest(env_delta_sha, expected_delta_sha)
                    matches_submitted_delta = bool(clean_submitted_sha) and hmac.compare_digest(
                        env_delta_sha, clean_submitted_sha
                    )
                    if not (matches_commit_delta or matches_submitted_delta):
                        continue
                else:
                    if auth_role != "admin" or not hmac.compare_digest(env_base, expected_base_sha):
                        continue

                target_uris = [str(u) for u in (envelope.get("target_concept_uris") or []) if u]
                rationale = str(envelope.get("rationale") or "").strip()
                if not target_uris or len(rationale) < 10:
                    continue

                draft_kwargs: dict[str, Any] = {
                    "proposal_id": _proposal_uuid_from_str(
                        proposal_id or env_prop or delta.delta_id
                    ),
                    "proposal_delta_sha256": delta_sha,
                    "target_concept_uris": target_uris,
                    "base_sha256": base_sha,
                    "rationale": rationale,
                    "authorized_by": auth_by,
                    "authorized_role": "admin",
                    "signature_or_token": "placeholder",
                }
                if parsed_exp is not None:
                    now_dt = datetime.now(UTC)
                    if parsed_exp <= now_dt:
                        continue
                    draft_kwargs["created_at"] = now_dt
                    draft_kwargs["expires_at"] = parsed_exp

                try:
                    draft_rec = StewardWaiverRecord(**draft_kwargs)
                except (ValidationError, ValueError):
                    continue
                real_sig = sec_mgr.sign_waiver(draft_rec.signing_payload().encode("utf-8"))
                signed_waivers.append(draft_rec.model_copy(update={"signature_or_token": real_sig}))

    # 2. Build inline admin waiver if low_confidence_waiver=True
    if low_confidence_waiver:
        if not grounding_rationale or len(grounding_rationale.strip()) < 10:
            raise StudioAPIError(
                "INVALID_GROUNDING_WAIVER",
                "Administrative grounding waiver requires a rationale of at least 10 characters.",
            )
        target_uris = _normalize_ungrounded_list(explicit_ungrounded)
        if not target_uris:
            target_uris = sorted(collect_touched_concept_uris(base_ontology, delta))
        if target_uris:
            try:
                draft_inline = StewardWaiverRecord(
                    proposal_id=_proposal_uuid_from_str(proposal_id or delta.delta_id),
                    proposal_delta_sha256=delta_sha,
                    target_concept_uris=target_uris,
                    base_sha256=base_sha,
                    rationale=grounding_rationale.strip(),
                    authorized_by=principal.principal,
                    authorized_role="admin",
                    signature_or_token="placeholder",
                )
            except (ValidationError, ValueError) as exc:
                raise StudioAPIError(
                    "INVALID_GROUNDING_WAIVER",
                    f"Invalid grounding waiver specification: {exc}",
                ) from exc
            inline_sig = sec_mgr.sign_waiver(draft_inline.signing_payload().encode("utf-8"))
            signed_waivers.append(
                draft_inline.model_copy(update={"signature_or_token": inline_sig})
            )

    return signed_waivers


# ---------------------------------------------------------------------------
# Task 5B.1 (Issue #215): Session & Source Management REST Endpoints
# ---------------------------------------------------------------------------


@router.post("/init", response_model=InitStudioResponse)
def init_studio(
    request: Request,
    studio_root: Annotated[Path, Depends(get_studio_root)],
    payload: InitStudioRequest | None = None,
    force: bool = Query(default=False),
) -> InitStudioResponse:
    """Initialize local workbench loopback token (.local_token) for human:local_admin."""
    server_mode = get_server_mode(request)
    if server_mode == SERVER_MODE_REMOTE:
        raise StudioAPIError(
            "LOOPBACK_PRIVILEGE_DISABLED_IN_REMOTE_MODE",
            "Studio bootstrap (/init) is disabled when platform runs in remote_platform mode.",
            instance=str(request.url.path),
        )

    if not is_loopback_client(request):
        raise StudioAPIError(
            "LOOPBACK_PRIVILEGE_DISABLED_IN_REMOTE_MODE",
            "Bootstrap endpoint /init is strictly restricted to loopback callers.",
            instance=str(request.url.path),
        )

    host_raw = request.headers.get("host") or request.headers.get("Host") or ""
    host = urllib.parse.urlparse(f"//{host_raw}").hostname or host_raw
    if host and not is_loopback_host(host):
        raise StudioAPIError(
            "LOOPBACK_PRIVILEGE_DISABLED_IN_REMOTE_MODE",
            f"DNS rebinding protection: untrusted host header '{host_raw}' for loopback init.",
            instance=str(request.url.path),
        )

    verify_csrf(request)

    auth_header = request.headers.get("Authorization") or request.headers.get("authorization")
    caller_token = None
    if auth_header and auth_header.strip().lower().startswith("bearer "):
        caller_token = auth_header.strip()[7:].strip()

    eff_force = (payload.force if payload else False) or force

    new_token, token_path = initialize_local_token(
        studio_root,
        force=eff_force,
        caller_token=caller_token,
        is_remote=False,
    )

    request.state.studio_principal = StudioPrincipal(
        principal="human:local_admin",
        role="admin",
        is_admin=True,
        auth_method="local_token",
    )

    return InitStudioResponse(
        status="initialized",
        token_path=str(token_path),
        principal="human:local_admin",
        role="admin",
        local_token=new_token,
        domain_namespace=payload.domain_namespace if payload else None,
    )


@router.get("/sessions", response_model=list[StudioSessionSummaryDTO])
def list_sessions(
    session_mgr: Annotated[StudioSessionManager, Depends(get_session_manager)],
    _principal: Annotated[StudioPrincipal, Depends(require_viewer)],
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    status: str | None = Query(default=None),
) -> list[StudioSessionSummaryDTO]:
    """List studio sessions with pagination and status filtering."""
    sessions = session_mgr.list_sessions()
    enriched = [
        (s, _derive_session_status(session_mgr.get_session_dir(s.session_id), s)) for s in sessions
    ]
    if status:
        target_st = status.lower()
        enriched = [
            (s, eff_st)
            for s, eff_st in enriched
            if eff_st.lower() == target_st or s.status.lower() == target_st
        ]

    paginated = enriched[offset : offset + limit]
    return [
        StudioSessionSummaryDTO(
            session_id=s.session_id,
            domain_namespace=s.domain_namespace,
            enterprise_owner=s.enterprise_owner,
            status=eff_st,
            active_version=s.head_version,
            created_at=s.created_at,
            updated_at=s.updated_at,
        )
        for s, eff_st in paginated
    ]


_DOMAIN_NAMESPACE_PATTERN: re.Pattern[str] = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


def _discover_workspace_ontology_file(ws_path: Path, domain_namespace: str) -> Path:
    """Resolve an ontology contract file from a workspace file or directory path."""
    if ws_path.is_file():
        return ws_path
    if not ws_path.is_dir():
        raise StudioAPIError(
            "INVALID_WORKSPACE_PATH",
            "Specified workspace seed path is invalid or inaccessible.",
        )

    candidates = [
        ws_path / f"{domain_namespace}_graph_a_ontology.yaml",
        ws_path / "graph_a_ontology.yaml",
        ws_path / "graph_a.yaml",
        ws_path / "ontology.yaml",
        ws_path / "ontology.json",
    ]
    for cand in candidates:
        if cand.is_file():
            return cand

    globbed = sorted(ws_path.glob("*_graph_a_ontology.yaml"))
    if globbed:
        return globbed[0]

    raise StudioAPIError(
        "FILE_NOT_FOUND",
        f"No Graph A ontology contract found in workspace seed path '{ws_path.name}'.",
    )


@router.post("/sessions", response_model=StudioSessionDTO, status_code=201)
def create_session(
    request: Request,
    payload: CreateSessionRequest,
    session_mgr: Annotated[StudioSessionManager, Depends(get_session_manager)],
    _principal: Annotated[StudioPrincipal, Depends(require_editor_or_automated)],
) -> StudioSessionDTO:
    """Create a new studio session (genesis 0.0.0 or seeded from workspace)."""
    if not _DOMAIN_NAMESPACE_PATTERN.match(payload.domain_namespace):
        raise StudioAPIError(
            "SYNTAX_INVALID_ARGUMENT",
            f"Invalid domain_namespace '{payload.domain_namespace}'. "
            "Must match ^[a-z][a-z0-9_]{0,63}$",
        )

    initial_ontology: GraphAOntology | None = None
    if payload.from_workspace:
        is_remote = get_server_mode(request) == SERVER_MODE_REMOTE
        if is_remote:
            raise StudioAPIError(
                "REMOTE_SERVER_PATH_FORBIDDEN",
                "Server-local 'from_workspace' paths are forbidden in remote_platform mode.",
            )

        allowed_roots = _resolve_allowed_workspace_roots(session_mgr)
        raw_p = Path(payload.from_workspace)
        candidate_p = raw_p if raw_p.is_absolute() else (allowed_roots[0] / raw_p)

        try:
            validated_ws = validate_workspace_containment(
                candidate_p,
                allowed_roots=allowed_roots,
                is_remote_request=False,
            )
        except OpenDVError as exc:
            raise _raise_from_opendv_error(exc) from exc

        if not validated_ws.exists():
            raise StudioAPIError(
                "INVALID_WORKSPACE_PATH",
                "Specified workspace seed path does not exist within allowed workspace roots.",
            )

        ont_file = _discover_workspace_ontology_file(validated_ws, payload.domain_namespace)
        try:
            if ont_file.suffix.lower() == ".json":
                initial_ontology = GraphAOntology.model_validate_json(
                    ont_file.read_text(encoding="utf-8")
                )
            else:
                initial_ontology = GraphAOntology.model_validate(
                    yaml.safe_load(ont_file.read_text(encoding="utf-8"))
                )
        except (ValidationError, ValueError, TypeError, yaml.YAMLError, OSError) as exc:
            raise StudioAPIError(
                "INVALID_WORKSPACE_ONTOLOGY",
                f"Workspace ontology contract '{ont_file.name}' failed validation: {exc}",
            ) from exc

    try:
        meta = session_mgr.create_session(
            session_id=payload.session_id,
            initial_ontology=initial_ontology,
            domain_namespace=payload.domain_namespace,
            enterprise_owner=payload.enterprise_owner,
        )
    except SessionAlreadyExistsError as exc:
        raise StudioAPIError("SESSION_ALREADY_EXISTS", str(exc)) from exc
    except InvalidSessionIdentifierError as exc:
        raise StudioAPIError("INVALID_SESSION_IDENTIFIER", str(exc)) from exc
    except InvalidOntologyPayloadError as exc:
        raise StudioAPIError("INVALID_WORKSPACE_ONTOLOGY", str(exc)) from exc

    sdir = session_mgr.get_session_dir(meta.session_id)
    return StudioSessionDTO(
        session_id=meta.session_id,
        domain_namespace=meta.domain_namespace,
        enterprise_owner=meta.enterprise_owner,
        status=_derive_session_status(sdir, meta),
        active_version=meta.head_version,
        head_sha256=meta.head_sha256,
        created_at=meta.created_at,
        updated_at=meta.updated_at,
    )


@router.get("/sessions/{session_id}", response_model=StudioSessionDetailDTO)
def get_session(
    session_id: str,
    session_mgr: Annotated[StudioSessionManager, Depends(get_session_manager)],
    _principal: Annotated[StudioPrincipal, Depends(require_viewer)],
) -> StudioSessionDetailDTO:
    """Retrieve studio session details and counts."""
    meta = _get_session_or_404(session_mgr, session_id)
    sdir = session_mgr.get_session_dir(session_id)

    manifest_file = sdir / "inputs" / "manifest.json"
    sources_count = 0
    if manifest_file.is_file():
        with contextlib.suppress(Exception):
            mdata = json.loads(manifest_file.read_text(encoding="utf-8"))
            sources_count = sum(1 for f in mdata.get("files", []) if not f.get("tombstoned", False))

    proposals_dir = sdir / "proposals"
    proposals_count = len(list(proposals_dir.glob("*.json"))) if proposals_dir.is_dir() else 0

    versions_dir = sdir / "versions"
    versions_count = len(list(versions_dir.glob("v*"))) if versions_dir.is_dir() else 0
    grandfathered_uris = sorted(_load_grandfathered_concept_uris(sdir))

    return StudioSessionDetailDTO(
        session_id=meta.session_id,
        domain_namespace=meta.domain_namespace,
        enterprise_owner=meta.enterprise_owner,
        status=_derive_session_status(sdir, meta),
        active_version=meta.head_version,
        head_sha256=meta.head_sha256,
        created_at=meta.created_at,
        updated_at=meta.updated_at,
        sources_count=sources_count,
        proposals_count=proposals_count,
        versions_count=versions_count,
        grandfathered_concept_uris=grandfathered_uris,
    )


@router.delete("/sessions/{session_id}", status_code=204)
def delete_session(
    session_id: str,
    session_mgr: Annotated[StudioSessionManager, Depends(get_session_manager)],
    _principal: Annotated[StudioPrincipal, Depends(require_admin)],
    force: bool = Query(default=False),
) -> None:
    """Tombstone and remove session directory (admin required)."""
    meta = _get_session_or_404(session_mgr, session_id)
    sdir = session_mgr.get_session_dir(session_id)
    is_promoted = (sdir / "promotion.json").is_file() or bool(meta.promoted_versions)
    if is_promoted and not force:
        raise StudioAPIError(
            "CANNOT_DELETE_PROMOTED_SESSION",
            f"Session '{session_id}' has already been promoted. Pass force=true to delete.",
        )

    get_job_supervisor().purge_session_jobs(session_id)
    session_mgr.delete_session(session_id)


@router.get("/sessions/{session_id}/status", response_model=StudioSessionStatusDTO)
def get_session_status(
    session_id: str,
    session_mgr: Annotated[StudioSessionManager, Depends(get_session_manager)],
    _principal: Annotated[StudioPrincipal, Depends(require_viewer)],
) -> StudioSessionStatusDTO:
    """Lightweight status check returning version state and active jobs."""
    meta = _get_session_or_404(session_mgr, session_id)
    sdir = session_mgr.get_session_dir(session_id)
    active_jobs = get_job_supervisor().count_active_jobs(session_mgr, session_id)

    return StudioSessionStatusDTO(
        session_id=meta.session_id,
        status=_derive_session_status(sdir, meta),
        active_version=meta.head_version,
        head_sha256=meta.head_sha256,
        active_jobs=active_jobs,
        taint_classification=meta.taint_classification,
    )


@router.get("/sessions/{session_id}/manifest", response_model=ManifestDTO)
def get_manifest(
    session_id: str,
    session_mgr: Annotated[StudioSessionManager, Depends(get_session_manager)],
    _principal: Annotated[StudioPrincipal, Depends(require_viewer)],
) -> ManifestDTO:
    """Inspect CAS manifest listing all registered sources."""
    _get_session_or_404(session_mgr, session_id)
    sdir = session_mgr.get_session_dir(session_id)

    mfile = sdir / "inputs" / "manifest.json"
    files: list[dict[str, Any]] = []
    if mfile.is_file():
        try:
            mdata = json.loads(mfile.read_text(encoding="utf-8"))
            files = mdata.get("files", [])
        except (OSError, json.JSONDecodeError):
            files = []

    if _principal.is_automated:
        sanitized_files: list[dict[str, Any]] = []
        for f in files:
            if f.get("do_not_send_to_agent"):
                redacted = dict(f)
                redacted["description"] = None
                sanitized_files.append(redacted)
            else:
                sanitized_files.append(f)
        files = sanitized_files

    total_bytes = sum(f.get("size_bytes", 0) for f in files if not f.get("tombstoned", False))
    return ManifestDTO(
        session_id=session_id,
        files=files,
        total_bytes=total_bytes,
    )


@router.get("/sessions/{session_id}/sources", response_model=list[SourceDocumentDTO])
def list_sources(
    session_id: str,
    session_mgr: Annotated[StudioSessionManager, Depends(get_session_manager)],
    _principal: Annotated[StudioPrincipal, Depends(require_viewer)],
) -> list[SourceDocumentDTO]:
    """List all ingested source documents registered in session manifest."""
    manifest_dto = get_manifest(session_id, session_mgr, _principal)
    return [
        SourceDocumentDTO(
            source_id=f.get("source_id", ""),
            source_sha256=f.get("sha256", ""),
            file_name=f.get("filename", ""),
            filename=f.get("filename", ""),
            size_bytes=f.get("size_bytes", 0),
            byte_count=f.get("size_bytes", 0),
            mime_type=f.get("mime_type", "application/octet-stream"),
            do_not_send_to_agent=bool(f.get("do_not_send_to_agent", False)),
            tombstoned=bool(f.get("tombstoned", False)),
        )
        for f in manifest_dto.files
        if not f.get("tombstoned", False)
    ]


def _persist_uploaded_source_sync(
    *,
    session_mgr: StudioSessionManager,
    session_id: str,
    tmp_blob_path: Path,
    size_bytes: int,
    filename: str,
    mime_type: str,
    do_not_send_to_agent: bool,
    description: str | None,
    principal: StudioPrincipal,
) -> SourceUploadResponse:
    """Persist streamed source blob into CAS, manifest, taint index, and session metadata."""
    sdir = session_mgr.get_session_dir(session_id)
    raw_bytes = tmp_blob_path.read_bytes()

    lock_path = sdir / ".session.lock"
    with reentrant_session_lock(lock_path, session_id=session_id):
        cas = session_mgr.get_cas(session_id)
        source_sha256 = cas.put(raw_bytes)
        source_id = f"src-{source_sha256[:16]}"
        mfile = sdir / "inputs" / "manifest.json"
        mfile.parent.mkdir(parents=True, exist_ok=True)
        if mfile.is_file():
            try:
                mdata = json.loads(mfile.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise StudioAPIError(
                    "CORRUPT_SESSION_RECORD",
                    f"Corrupt manifest file in session '{session_id}': {exc}",
                ) from exc
        else:
            mdata = {"files": []}

        files = mdata.get("files", [])
        existing = next((f for f in files if f.get("source_id") == source_id), None)
        if existing:
            if (
                principal.is_automated
                and not existing.get("tombstoned", False)
                and description is not None
                and existing.get("description")
                and existing.get("description") != description
            ):
                raise StudioAPIError(
                    "AGENT_METADATA_OVERWRITE_FORBIDDEN",
                    "Automated actors cannot overwrite existing human-authored source metadata.",
                )
            existing["tombstoned"] = False
            existing["deleted_at"] = None
            existing["filename"] = filename
            existing["size_bytes"] = size_bytes
            existing["mime_type"] = mime_type
            existing["updated_at"] = datetime.now(UTC).isoformat()
            # Sticky monotonic taint flag
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
                "do_not_send_to_agent": bool(do_not_send_to_agent),
                "description": description,
                "uploaded_at": datetime.now(UTC).isoformat(),
                "tombstoned": False,
            }
            files.append(existing)
            effective_do_not_send = bool(do_not_send_to_agent)

        mdata["files"] = files

        tmp_mfile = sdir / "inputs" / f".manifest.{uuid.uuid4().hex[:8]}.tmp"
        tmp_mfile.write_text(json.dumps(mdata, indent=2), encoding="utf-8")
        replace_with_retry(tmp_mfile, mfile)
        set_owner_only_permissions(mfile)

        # Monotonically propagate confidentiality taint to session.json & taint_index.json
        if effective_do_not_send:
            taint_idx_path = sdir / "taint_index.json"
            taint_data: dict[str, Any] = {
                "is_tainted": True,
                "tainted_sources": [],
            }
            if taint_idx_path.is_file():
                with contextlib.suppress(OSError, json.JSONDecodeError):
                    loaded_t = json.loads(taint_idx_path.read_text(encoding="utf-8"))
                    if isinstance(loaded_t, dict):
                        taint_data = loaded_t
                        taint_data["is_tainted"] = True
            tainted_list = list(taint_data.get("tainted_sources") or [])
            if not any(
                isinstance(item, dict) and item.get("source_id") == source_id
                for item in tainted_list
            ):
                tainted_list.append(
                    {
                        "source_id": source_id,
                        "sha256": source_sha256,
                        "filename": filename,
                        "marked_at": datetime.now(UTC).isoformat(),
                    }
                )
            taint_data["tainted_sources"] = tainted_list
            tmp_taint = sdir / f".taint_index.{uuid.uuid4().hex[:8]}.tmp"
            tmp_taint.write_text(json.dumps(taint_data, indent=2), encoding="utf-8")
            replace_with_retry(tmp_taint, taint_idx_path)
            set_owner_only_permissions(taint_idx_path)

            meta = session_mgr.get_session(session_id)
            if meta.taint_classification != "TAINTED":
                updated_meta = meta.model_copy(
                    update={
                        "taint_classification": "TAINTED",
                        "updated_at": datetime.now(UTC).isoformat(),
                    }
                )
                _atomic_write_session_json(sdir, updated_meta)

            append_signed_audit_record(
                session_dir=sdir,
                action="CONFIDENTIAL_SOURCE_REGISTERED",
                actor=principal.principal,
                details={
                    "source_id": source_id,
                    "source_sha256": source_sha256,
                    "do_not_send_to_agent": True,
                },
                security_manager=session_mgr.security_manager,
            )

    return SourceUploadResponse(
        source_id=source_id,
        source_sha256=source_sha256,
        file_name=filename,
        filename=filename,
        size_bytes=size_bytes,
        byte_count=size_bytes,
        mime_type=mime_type,
        do_not_send_to_agent=effective_do_not_send,
    )


@router.post("/sessions/{session_id}/sources", response_model=SourceUploadResponse, status_code=201)
async def upload_source(
    session_id: str,
    file: UploadFile,
    session_mgr: Annotated[StudioSessionManager, Depends(get_session_manager)],
    principal: Annotated[StudioPrincipal, Depends(require_editor_or_automated)],
    request: Request,
    do_not_send_to_agent: Annotated[bool, Form()] = False,
    description: Annotated[str | None, Form()] = None,
) -> SourceUploadResponse:
    """Stream document to CAS blob store and register in inputs/manifest.json."""
    _get_session_or_404(session_mgr, session_id)

    if not do_not_send_to_agent and "do_not_send_to_agent" in request.query_params:
        val = request.query_params.get("do_not_send_to_agent", "").lower()
        do_not_send_to_agent = val in ("true", "1", "yes")
    if description is None and "description" in request.query_params:
        description = request.query_params.get("description")

    scratch_dir = session_mgr.studio_root / ".scratch"
    tmp_blob_path, size_bytes = await _stream_upload_to_tempfile(
        file,
        scratch_dir,
        max_bytes=MAX_SOURCE_UPLOAD_BYTES,
        prefix="source_upload",
        suffix=".bin",
    )
    try:
        if size_bytes == 0:
            raise StudioAPIError("CORRUPT_SOURCE_BLOB", "Uploaded source file is empty.")

        raw_name = (file.filename or "").strip()
        base_name = PureWindowsPath(raw_name).name
        filename = base_name if base_name and base_name not in {".", ".."} else "upload.bin"
        raw_content_type = (file.content_type or "").split(";", 1)[0].strip().lower()
        if (
            not raw_content_type or raw_content_type == "application/octet-stream"
        ) and filename.lower().endswith((".md", ".markdown")):
            mime_type = "text/markdown"
        else:
            mime_type = raw_content_type or "application/octet-stream"
        return await run_in_threadpool(
            _persist_uploaded_source_sync,
            session_mgr=session_mgr,
            session_id=session_id,
            tmp_blob_path=tmp_blob_path,
            size_bytes=size_bytes,
            filename=filename,
            mime_type=mime_type,
            do_not_send_to_agent=do_not_send_to_agent,
            description=description,
            principal=principal,
        )
    finally:
        if tmp_blob_path.exists():
            with contextlib.suppress(OSError):
                tmp_blob_path.unlink()


@router.get("/sessions/{session_id}/sources/{source_id}", response_model=SourceDocumentDTO)
def get_source_detail(
    session_id: str,
    source_id: str,
    session_mgr: Annotated[StudioSessionManager, Depends(get_session_manager)],
    _principal: Annotated[StudioPrincipal, Depends(require_viewer)],
) -> SourceDocumentDTO:
    """Retrieve metadata for an ingested source document."""
    sources = list_sources(session_id, session_mgr, _principal)
    src = next((s for s in sources if s.source_id == source_id), None)
    if not src or src.tombstoned:
        raise StudioAPIError("SOURCE_NOT_FOUND", f"Source '{source_id}' not found.")
    return src


@router.delete("/sessions/{session_id}/sources/{source_id}", status_code=204)
def delete_source_endpoint(
    session_id: str,
    source_id: str,
    session_mgr: Annotated[StudioSessionManager, Depends(get_session_manager)],
    _principal: Annotated[StudioPrincipal, Depends(require_editor)],
) -> None:
    """Tombstone and purge unreferenced source document (human editor/admin required)."""
    _get_session_or_404(session_mgr, session_id)
    sdir = session_mgr.get_session_dir(session_id)

    src = get_source_detail(session_id, source_id, session_mgr, _principal)
    if not src or src.tombstoned:
        raise StudioAPIError("SOURCE_NOT_FOUND", f"Source '{source_id}' not found.")

    try:
        delete_source(session_dir=sdir, source_id=source_id)
    except OpenDVError as exc:
        raise _raise_from_opendv_error(exc) from exc
    except Exception as exc:
        err_msg = str(exc)
        if "SOURCE_NOT_FOUND" in err_msg:
            raise StudioAPIError("SOURCE_NOT_FOUND", err_msg) from exc
        if (
            "COMMITTED_VERSION" in err_msg
            or "SourceReferencedByCommittedVersionError" in type(exc).__name__
        ):
            raise StudioAPIError(
                "SOURCE_REFERENCED_BY_COMMITTED_VERSION",
                f"Source '{source_id}' is referenced by a committed version and cannot be deleted.",
            ) from exc
        if (
            "PENDING_PROPOSAL" in err_msg
            or "SourceReferencedByPendingProposalError" in type(exc).__name__
        ):
            raise StudioAPIError(
                "SOURCE_REFERENCED_BY_PENDING_PROPOSAL",
                f"Source '{source_id}' is referenced by a pending proposal and cannot be deleted.",
            ) from exc
        raise StudioAPIError("EXECUTION_FAILURE", str(exc)) from exc


@router.get("/sessions/{session_id}/sources/{source_id}/content")
def get_source_content(
    session_id: str,
    source_id: str,
    session_mgr: Annotated[StudioSessionManager, Depends(get_session_manager)],
    principal: Annotated[StudioPrincipal, Depends(require_viewer)],
) -> Response:
    """Preview or download raw source content bytes from CAS enforcing taint perimeter."""
    src = get_source_detail(session_id, source_id, session_mgr, principal)

    # P10::MAJ-01: Block automated / agent:* principals from reading confidential source bytes!
    if principal.is_automated and src.do_not_send_to_agent:
        raise StudioAPIError(
            "CONFIDENTIAL_SOURCE_AGENT_ACCESS_FORBIDDEN",
            f"Automated principal '{principal.principal}' is strictly forbidden from accessing "
            f"confidential source '{source_id}' (do_not_send_to_agent=true).",
        )

    cas = session_mgr.get_cas(session_id)
    try:
        raw_bytes = cas.get(src.source_sha256)
    except Exception as exc:
        raise StudioAPIError(
            "SOURCE_NOT_FOUND", f"Raw blob for '{source_id}' missing in CAS."
        ) from exc

    safe_filename = urllib.parse.quote(src.file_name or src.filename or f"{source_id}.bin")
    headers = {
        "Content-Disposition": f"attachment; filename*=UTF-8''{safe_filename}",
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "sandbox",
    }
    return Response(
        content=raw_bytes,
        media_type=src.mime_type or "application/octet-stream",
        headers=headers,
    )


# ---------------------------------------------------------------------------
# Task 5B.2 (Issue #216): Parse, Clarification, Construction & Jobs
# ---------------------------------------------------------------------------


@router.post("/sessions/{session_id}/parse", response_model=JobHandleDTO, status_code=202)
def parse_sources(
    session_id: str,
    payload: ParseRequest,
    session_mgr: Annotated[StudioSessionManager, Depends(get_session_manager)],
    principal: Annotated[StudioPrincipal, Depends(require_editor_or_automated)],
) -> JobHandleDTO:
    """Trigger background document layout extraction and OCR parsing job."""
    _get_session_or_404(session_mgr, session_id)
    sdir = session_mgr.get_session_dir(session_id)

    raw_ocr_mode = (payload.ocr_mode or "").strip() if isinstance(payload.ocr_mode, str) else ""
    if raw_ocr_mode not in {"auto", "force", "off"}:
        raise StudioAPIError(
            "SYNTAX_INVALID_ARGUMENT",
            f"Invalid ocr_mode '{payload.ocr_mode}'. Must be 'auto', 'force', or 'off'.",
        )

    if payload.source_ids is not None:
        mfile = sdir / "inputs" / "manifest.json"
        active_source_ids: set[str] = set()
        if mfile.is_file():
            try:
                mdata = json.loads(mfile.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise StudioAPIError(
                    "CORRUPT_SESSION_RECORD",
                    f"Corrupt manifest.json in session '{session_id}': {exc}",
                ) from exc
            if isinstance(mdata, dict) and isinstance(mdata.get("files"), list):
                active_source_ids = {
                    str(f.get("source_id"))
                    for f in mdata["files"]
                    if isinstance(f, dict)
                    and f.get("source_id")
                    and not f.get("tombstoned", False)
                    and not f.get("deleted", False)
                }
        for req_sid in payload.source_ids:
            if req_sid not in active_source_ids:
                raise StudioAPIError(
                    "SOURCE_NOT_FOUND",
                    f"Source '{req_sid}' not found or tombstoned in session '{session_id}'.",
                )

    if raw_ocr_mode == "force" and not TesseractOCRRouter(ocr_mode="force").is_available():
        raise StudioAPIError(
            "OCR_UNAVAILABLE",
            "Tesseract OCR engine is unavailable on this host for ocr_mode='force'.",
        )

    supervisor = get_job_supervisor()
    job_handle = supervisor.submit_job(
        session_mgr=session_mgr,
        session_id=session_id,
        job_type="parse",
        runner_fn=run_parse_job,
        principal=principal.principal,
        role=principal.role,
        source_ids=payload.source_ids,
        ocr_mode=raw_ocr_mode,
    )
    return JobHandleDTO(**job_handle)


@router.get("/sessions/{session_id}/clarifications", response_model=list[ClarificationQuestion])
@router.get("/sessions/{session_id}/questions", response_model=list[ClarificationQuestion])
def get_clarification_questions(
    session_id: str,
    session_mgr: Annotated[StudioSessionManager, Depends(get_session_manager)],
    _principal: Annotated[StudioPrincipal, Depends(require_viewer)],
) -> list[ClarificationQuestion]:
    """Read generated ambiguity questions from session clarifications/questions.json."""
    _get_session_or_404(session_mgr, session_id)
    sdir = session_mgr.get_session_dir(session_id)

    qfile = sdir / "clarifications" / "questions.json"
    if not qfile.is_file():
        return []

    try:
        raw_data = json.loads(qfile.read_text(encoding="utf-8"))
        if not isinstance(raw_data, list):
            raise StudioAPIError(
                "CORRUPT_SESSION_RECORD",
                f"Corrupt questions.json in session '{session_id}': expected JSON list.",
            )
        return [ClarificationQuestion.model_validate(q) for q in raw_data]
    except StudioAPIError:
        raise
    except (OSError, json.JSONDecodeError, ValidationError, ValueError, TypeError) as exc:
        raise StudioAPIError(
            "CORRUPT_SESSION_RECORD",
            f"Corrupt questions.json in session '{session_id}': {exc}",
        ) from exc


@router.post("/sessions/{session_id}/clarify", response_model=JobHandleDTO, status_code=202)
def trigger_clarification(
    session_id: str,
    payload: StudioClarifyRequest,
    session_mgr: Annotated[StudioSessionManager, Depends(get_session_manager)],
    principal: Annotated[StudioPrincipal, Depends(require_editor)],
) -> JobHandleDTO:
    """Dispatch background clarification question generation (human editor/admin required)."""
    _get_session_or_404(session_mgr, session_id)
    sdir = session_mgr.get_session_dir(session_id)
    _ensure_session_version_exists(sdir, payload.base_version)

    supervisor = get_job_supervisor()
    job_handle = supervisor.submit_job(
        session_mgr=session_mgr,
        session_id=session_id,
        job_type="clarify",
        runner_fn=run_clarify_job,
        principal=principal.principal,
        role=principal.role,
        mode=payload.mode,
        max_questions=payload.max_questions,
        focus_concepts=payload.focus_concepts,
    )
    return JobHandleDTO(**job_handle)


@router.get("/sessions/{session_id}/answers", response_model=list[dict[str, Any]])
def get_clarification_answers(
    session_id: str,
    session_mgr: Annotated[StudioSessionManager, Depends(get_session_manager)],
    _principal: Annotated[StudioPrincipal, Depends(require_viewer)],
) -> list[dict[str, Any]]:
    """Read persisted clarification answers from session clarifications/answers.json."""
    _get_session_or_404(session_mgr, session_id)
    sdir = session_mgr.get_session_dir(session_id)

    answers_file = sdir / "clarifications" / "answers.json"
    if not answers_file.is_file():
        return []

    try:
        raw_data = json.loads(answers_file.read_text(encoding="utf-8"))
        if not isinstance(raw_data, list) or not all(isinstance(x, dict) for x in raw_data):
            raise StudioAPIError(
                "CORRUPT_SESSION_RECORD",
                f"Corrupt answers.json in session '{session_id}': expected list of objects.",
            )
        return raw_data
    except StudioAPIError:
        raise
    except (OSError, json.JSONDecodeError, ValueError, TypeError) as exc:
        raise StudioAPIError(
            "CORRUPT_SESSION_RECORD",
            f"Corrupt answers.json in session '{session_id}': {exc}",
        ) from exc


@router.post("/sessions/{session_id}/answers", response_model=ClarificationAnswerResponse)
def submit_clarification_answers(
    session_id: str,
    payload: StudioClarificationAnswerRequest,
    session_mgr: Annotated[StudioSessionManager, Depends(get_session_manager)],
    principal: Annotated[StudioPrincipal, Depends(require_editor)],
) -> ClarificationAnswerResponse:
    """Persist human clarification answers with mandatory OCC expected_answers_sha256 check."""
    _get_session_or_404(session_mgr, session_id)
    sdir = session_mgr.get_session_dir(session_id)

    answers_file = sdir / "clarifications" / "answers.json"
    lock_path = sdir / ".session.lock"

    with reentrant_session_lock(lock_path, session_id=session_id):
        answers_file.parent.mkdir(parents=True, exist_ok=True)
        current_answers: list[dict[str, Any]] = []
        if answers_file.is_file():
            try:
                raw = json.loads(answers_file.read_text(encoding="utf-8"))
                if isinstance(raw, list) and all(isinstance(x, dict) for x in raw):
                    current_answers = raw
                else:
                    raise StudioAPIError(
                        "CORRUPT_SESSION_RECORD",
                        f"Corrupt answers file in session '{session_id}': expected list.",
                    )
            except (OSError, json.JSONDecodeError) as exc:
                raise StudioAPIError(
                    "CORRUPT_SESSION_RECORD",
                    f"Corrupt answers file in session '{session_id}': {exc}",
                ) from exc

        if not current_answers:
            current_sha256 = EMPTY_ANSWERS_SHA256
        else:
            clean_current = [{k: v for k, v in a.items() if v is not None} for a in current_answers]
            sorted_items = sorted(clean_current, key=lambda a: a.get("question_id", ""))
            current_sha256 = hashlib.sha256(dumps_jcs_bytes(sorted_items)).hexdigest()

        if payload.expected_answers_sha256 is None:
            raise StudioAPIError(
                "MISSING_AUTO_COMMIT_PRECONDITION",
                "Submitting clarification answers requires expected_answers_sha256 precondition.",
                instance=f"/api/v1/ontology/studio/sessions/{session_id}/answers",
            )
        if payload.expected_answers_sha256 != current_sha256:
            raise StudioAPIError(
                "ONT_EVO_CONCURRENT_MODIFICATION",
                f"OCC conflict on answers: expected '{payload.expected_answers_sha256}', "
                f"current stored answers digest is '{current_sha256}'.",
                instance=f"/api/v1/ontology/studio/sessions/{session_id}/answers",
            )

        ans_items: list[ClarificationAnswerItem] = []
        try:
            if isinstance(payload.answers, dict):
                for qid, val in payload.answers.items():
                    if isinstance(val, dict):
                        val_copy = dict(val)
                        if "custom_text" in val_copy:
                            ct = val_copy.pop("custom_text")
                            if val_copy.get("user_text") is None:
                                val_copy["user_text"] = ct
                        ans_items.append(ClarificationAnswerItem(question_id=qid, **val_copy))
                    elif isinstance(val, list):
                        ans_items.append(
                            ClarificationAnswerItem(question_id=qid, selected_options=val)
                        )
                    else:
                        ans_items.append(
                            ClarificationAnswerItem(question_id=qid, user_text=str(val))
                        )
            else:
                for raw_item in payload.answers:
                    if isinstance(raw_item, ClarificationAnswerItem):
                        ans_items.append(raw_item)
                    elif isinstance(raw_item, dict):
                        item_copy = dict(raw_item)
                        if "custom_text" in item_copy:
                            ct = item_copy.pop("custom_text")
                            if item_copy.get("user_text") is None:
                                item_copy["user_text"] = ct
                        ans_items.append(ClarificationAnswerItem.model_validate(item_copy))
                    else:
                        ans_items.append(ClarificationAnswerItem.model_validate(raw_item))
        except (ValidationError, TypeError, ValueError) as exc:
            raise StudioAPIError(
                "SYNTAX_INVALID_ARGUMENT",
                f"Invalid clarification answer item format: {exc}",
            ) from exc

        if not ans_items:
            raise StudioAPIError(
                "SYNTAX_INVALID_ARGUMENT",
                "Clarification answers submission must include at least one answer.",
            )

        seen_qids: set[str] = set()
        for ans in ans_items:
            if ans.question_id in seen_qids:
                raise StudioAPIError(
                    "SYNTAX_INVALID_ARGUMENT",
                    f"Duplicate question_id '{ans.question_id}' in clarification answers.",
                )
            seen_qids.add(ans.question_id)

        qfile = sdir / "clarifications" / "questions.json"
        if qfile.is_file():
            try:
                raw_questions = json.loads(qfile.read_text(encoding="utf-8"))
                if not isinstance(raw_questions, list):
                    raise StudioAPIError(
                        "CORRUPT_SESSION_RECORD",
                        f"Corrupt questions.json in session '{session_id}': expected list.",
                    )
                loaded_questions = [ClarificationQuestion.model_validate(q) for q in raw_questions]
            except StudioAPIError:
                raise
            except (OSError, json.JSONDecodeError, ValidationError, ValueError, TypeError) as exc:
                raise StudioAPIError(
                    "CORRUPT_SESSION_RECORD",
                    f"Corrupt questions.json in session '{session_id}': {exc}",
                ) from exc

            if loaded_questions:
                questions_by_id = {q.question_id: q for q in loaded_questions}
                for ans in ans_items:
                    if ans.question_id not in questions_by_id:
                        raise StudioAPIError(
                            "RESOURCE_NOT_FOUND",
                            f"Clarification question '{ans.question_id}' not found.",
                        )
                    q_def = questions_by_id[ans.question_id]
                    if (
                        not q_def.allow_custom_text
                        and ans.user_text is not None
                        and ans.user_text.strip() != ""
                    ):
                        raise StudioAPIError(
                            "SYNTAX_INVALID_ARGUMENT",
                            f"Question '{ans.question_id}' does not permit custom text answers.",
                        )
                    valid_options = {opt.option_id for opt in q_def.options} | {
                        opt.label for opt in q_def.options
                    }
                    for sel in ans.selected_options:
                        if sel not in valid_options:
                            raise StudioAPIError(
                                "SYNTAX_INVALID_ARGUMENT",
                                f"Invalid option '{sel}' for question '{ans.question_id}'.",
                            )

        answers_map = {
            a.get("question_id"): {k: v for k, v in a.items() if v is not None}
            for a in current_answers
            if a.get("question_id")
        }
        for ans in ans_items:
            answers_map[ans.question_id] = ans.model_dump(mode="json", exclude_none=True)

        merged_answers = sorted(answers_map.values(), key=lambda a: a.get("question_id", ""))
        jcs_bytes = dumps_jcs_bytes(merged_answers)
        new_sha256 = session_mgr.get_cas(session_id).put(jcs_bytes)

        tmp_answers = sdir / "clarifications" / f".answers.{uuid.uuid4().hex[:8]}.tmp"
        tmp_answers.write_text(json.dumps(merged_answers, indent=2), encoding="utf-8")
        replace_with_retry(tmp_answers, answers_file)
        set_owner_only_permissions(answers_file)

        append_signed_audit_record(
            session_dir=sdir,
            action="CLARIFICATION_ANSWERS_SAVED",
            actor=principal.principal,
            details={"saved_count": len(ans_items), "answers_sha256": new_sha256},
            security_manager=session_mgr.security_manager,
        )

    return ClarificationAnswerResponse(
        saved_count=len(ans_items),
        answers_sha256=new_sha256,
    )


@router.post("/sessions/{session_id}/construct", response_model=JobHandleDTO, status_code=202)
def trigger_construction(
    request: Request,
    session_id: str,
    payload: ConstructRequest,
    session_mgr: Annotated[StudioSessionManager, Depends(get_session_manager)],
    principal: Annotated[StudioPrincipal, Depends(require_editor_or_automated)],
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> JobHandleDTO:
    """Trigger background construction job (deterministic or agent-first)."""
    meta = _get_session_or_404(session_mgr, session_id)
    sdir = session_mgr.get_session_dir(session_id)
    _ensure_session_version_exists(sdir, payload.base_version)

    if payload.mode is None:
        raw_mode = "deterministic" if payload.auto_commit else "auto"
    else:
        raw_mode = payload.mode.strip().lower()
        if raw_mode not in ("auto", "agent", "deterministic", "challenger"):
            raise StudioAPIError(
                "SYNTAX_INVALID_ARGUMENT",
                f"Invalid construction mode '{payload.mode}'. "
                "Must be 'auto', 'agent', or 'deterministic'.",
            )

    clean_if_match = _parse_strong_etag_or_400(if_match) if if_match is not None else None
    clean_body_sha = (
        _parse_strong_etag_or_400(payload.base_sha256, header_name="base_sha256")
        if payload.base_sha256
        else None
    )

    if payload.auto_commit:
        if principal.is_agent_identity:
            raise StudioAPIError(
                "AGENT_PROPOSAL_ACCEPT_FORBIDDEN",
                "Automated agent identities are forbidden from auto-commit construction.",
            )
        if payload.mode is not None and raw_mode != "deterministic":
            raise StudioAPIError(
                "SYNTAX_INVALID_ARGUMENT",
                "Auto-commit construction is only permitted in deterministic mode; "
                "non-deterministic modes require human proposal review.",
            )
        if not (clean_if_match or clean_body_sha):
            raise StudioAPIError(
                "MISSING_AUTO_COMMIT_PRECONDITION",
                "Auto-commit construction requires an 'If-Match' header or 'base_sha256' "
                "anchoring session head SHA-256.",
            )
        clean_mode = "deterministic"
    else:
        clean_mode = "deterministic" if raw_mode == "challenger" else raw_mode

    base_version = payload.base_version or meta.head_version
    base_sha256 = clean_body_sha or (
        meta.head_sha256 if base_version.lstrip("v") == meta.head_version.lstrip("v") else None
    )

    if clean_if_match and clean_body_sha and clean_if_match != clean_body_sha:
        raise StudioAPIError(
            "PRECONDITION_BASE_SHA_MISMATCH",
            f"If-Match header ('{clean_if_match}') does not match "
            f"body base_sha256 ('{clean_body_sha}').",
        )

    if clean_if_match is not None and clean_if_match != meta.head_sha256:
        raise StudioAPIError(
            "ONT_EVO_CONCURRENT_MODIFICATION",
            f"If-Match ('{clean_if_match}') does not match "
            f"session head SHA-256 ('{meta.head_sha256}').",
        )
    if payload.auto_commit and clean_body_sha is not None and clean_body_sha != meta.head_sha256:
        raise StudioAPIError(
            "ONT_EVO_CONCURRENT_MODIFICATION",
            f"base_sha256 ('{clean_body_sha}') does not match "
            f"session head SHA-256 ('{meta.head_sha256}').",
        )

    agent_runner = getattr(request.app.state, "studio_agent_runner", None)
    supervisor = get_job_supervisor()
    job_handle = supervisor.submit_job(
        session_mgr=session_mgr,
        session_id=session_id,
        job_type="construct",
        runner_fn=run_construct_job,
        principal=principal.principal,
        role=principal.role,
        base_version=base_version,
        base_sha256=base_sha256,
        auto_commit=payload.auto_commit,
        fibo_modules=payload.fibo_modules,
        mode=clean_mode,
        if_match=clean_if_match,
        agent_runner=agent_runner,
    )
    return JobHandleDTO(**job_handle)


@router.get("/sessions/{session_id}/jobs/{job_id}", response_model=JobStatusDTO)
@router.get("/jobs/{job_id}", response_model=JobStatusDTO)
def get_job_status_endpoint(
    job_id: str,
    session_mgr: Annotated[StudioSessionManager, Depends(get_session_manager)],
    _principal: Annotated[StudioPrincipal, Depends(require_viewer)],
    session_id: str | None = None,
) -> JobStatusDTO:
    """Get background job status, stage, pct_complete, and error/result."""
    if session_id:
        _get_session_or_404(session_mgr, session_id)
    _validate_job_id_param(job_id)
    supervisor = get_job_supervisor()
    effective_sid = session_id or supervisor.resolve_job_session_id(session_mgr, job_id)
    job_data = supervisor.get_job(session_mgr, effective_sid, job_id)
    return JobStatusDTO(**job_data)


@router.get("/sessions/{session_id}/jobs/{job_id}/events")
@router.get("/jobs/{job_id}/events")
async def get_job_events_stream(
    job_id: str,
    request: Request,
    session_mgr: Annotated[StudioSessionManager, Depends(get_session_manager)],
    _principal: Annotated[StudioPrincipal, Depends(require_viewer)],
    session_id: str | None = None,
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
) -> StreamingResponse:
    """Stream W3C Server-Sent Events with monotonic Last-Event-ID replay and 15s keepalive."""
    del request
    if session_id:
        _get_session_or_404(session_mgr, session_id)
    _validate_job_id_param(job_id)
    if last_event_id is not None:
        trimmed = last_event_id.strip()
        if not re.match(r"^[0-9]+$", trimmed):
            raise StudioAPIError(
                "SYNTAX_INVALID_ARGUMENT",
                f"Invalid Last-Event-ID cursor '{last_event_id}'; must be a non-negative integer.",
            )

    supervisor = get_job_supervisor()
    effective_sid = session_id or supervisor.resolve_job_session_id(session_mgr, job_id)

    # P11 MAJ-02: Synchronous pre-flight validation executes BEFORE StreamingResponse is created!
    stream_iter = supervisor.stream_job_events(
        session_mgr=session_mgr,
        session_id=effective_sid,
        job_id=job_id,
        last_event_id=last_event_id,
    )
    return StreamingResponse(
        stream_iter,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/sessions/{session_id}/jobs/{job_id}/cancel", response_model=JobStatusDTO)
@router.post("/jobs/{job_id}/cancel", response_model=JobStatusDTO)
def cancel_job_endpoint(
    job_id: str,
    session_mgr: Annotated[StudioSessionManager, Depends(get_session_manager)],
    _principal: Annotated[StudioPrincipal, Depends(require_editor_or_automated)],
    session_id: str | None = None,
    payload: dict[str, Any] | None = None,
) -> JobStatusDTO:
    """Cooperatively abort a running background job, releasing L2 lease."""
    if session_id:
        _get_session_or_404(session_mgr, session_id)
    _validate_job_id_param(job_id)
    supervisor = get_job_supervisor()
    effective_sid = session_id or supervisor.resolve_job_session_id(session_mgr, job_id)

    reason = (payload or {}).get("reason")
    job_data = supervisor.cancel_job(session_mgr, effective_sid, job_id, reason=reason)
    return JobStatusDTO(**job_data)


# ---------------------------------------------------------------------------
# Task 5B.3 (Issue #217): Deltas, Proposals, Waivers, Feedback, Export & Promotion
# ---------------------------------------------------------------------------


@router.post("/sessions/{session_id}/validate", response_model=OntologyValidationResult)
@router.post(
    "/sessions/{session_id}/deltas/validate",
    response_model=OntologyValidationResult,
    include_in_schema=False,
)
def validate_delta_endpoint(
    request: Request,
    session_id: str,
    payload: OntologyDeltaRequest,
    session_mgr: Annotated[StudioSessionManager, Depends(get_session_manager)],
    _principal: Annotated[StudioPrincipal, Depends(require_viewer)],
) -> OntologyValidationResult:
    """Dry-run two-phase structural and governance validation on an OntologyDelta."""
    meta = _get_session_or_404(session_mgr, session_id)
    sdir = session_mgr.get_session_dir(session_id)
    violations: list[str] = []
    diagnostics: list[str] = []
    primary_error_code: str | None = None
    norm_ops = [_normalize_delta_operation(op) for op in payload.operations]

    eff_parent_raw = (
        payload.parent_sha256 if payload.parent_sha256 is not None else payload.base_sha256
    )
    eff_base_ver = payload.base_version or meta.head_version

    # 1. OCC and Lineage Precondition Validation
    if meta.head_version == GENESIS_VERSION:
        if eff_parent_raw not in ("", None, meta.head_sha256):
            violations.append(
                "Genesis delta parent_sha256 must be empty or match genesis digest, "
                f"got '{eff_parent_raw}'."
            )
            primary_error_code = primary_error_code or "ONT_EVO_CONCURRENT_MODIFICATION"
        expected_genesis = _derive_genesis_target_version(norm_ops)
        if payload.target_version and payload.target_version.lstrip("v") != expected_genesis:
            violations.append(
                f"target_version '{payload.target_version}' does not match expected "
                f"genesis version '{expected_genesis}'."
            )
            primary_error_code = primary_error_code or "ONT_EVO_CONTRACT_VIOLATION"
    else:
        clean_parent = (eff_parent_raw or "").strip()
        if clean_parent != meta.head_sha256:
            violations.append(
                f"parent_sha256 '{clean_parent}' does not match session head '{meta.head_sha256}'."
            )
            primary_error_code = primary_error_code or "ONT_EVO_CONCURRENT_MODIFICATION"
        clean_base_ver = eff_base_ver.lstrip("v")
        active_ver = meta.head_version.lstrip("v")
        if clean_base_ver != active_ver:
            violations.append(
                f"base_version '{eff_base_ver}' does not match "
                f"session head version '{meta.head_version}'."
            )
            primary_error_code = primary_error_code or "ONT_EVO_CONCURRENT_MODIFICATION"

    base_ontology = _load_session_version_ontology(sdir, meta, meta.head_version)

    # 2. Structural & SemVer Validation
    delta: OntologyDelta | None = None
    try:
        expected_target = _compute_expected_semver_for_ops(
            base_version=meta.head_version,
            base_sha256=meta.head_sha256,
            norm_ops=norm_ops,
            base_ontology=base_ontology,
            domain_namespace=meta.domain_namespace,
        )
        if payload.target_version and payload.target_version.lstrip("v") != expected_target.lstrip(
            "v"
        ):
            violations.append(
                f"target_version '{payload.target_version}' does not match required "
                f"semver bump '{expected_target}'."
            )
            primary_error_code = primary_error_code or "ONT_EVO_CONTRACT_VIOLATION"
        delta = OntologyDelta(
            delta_id=payload.delta_id or f"delta-val-{uuid.uuid4().hex[:8]}",
            parent_sha256=meta.head_sha256,
            base_version=meta.head_version,
            target_version=payload.target_version or expected_target,
            domain_namespace=meta.domain_namespace,
            operations=norm_ops,
            author=payload.author or "studio_validator",
            rationale=payload.rationale,
        )
    except OpenDVError as exc:
        violations.append(f"Delta structural validation failure: {exc}")
        raw_ec = getattr(exc, "error_code", None)
        primary_error_code = primary_error_code or (
            raw_ec.value
            if hasattr(raw_ec, "value")
            else str(raw_ec or "ONT_EVO_CONTRACT_VIOLATION")
        )
    except (ValidationError, ValueError, TypeError) as exc:
        violations.append(f"Delta structural validation failure: {exc}")
        primary_error_code = primary_error_code or "ONT_EVO_CONTRACT_VIOLATION"

    # 3. Structural Apply + Phase-2 Governance Dry-Run
    if delta is not None and not violations:
        from opendv_service.ontology.validator import validate_ontology_delta

        existing_concepts = {c.concept_uri for c in base_ontology.canonical_business_concepts}
        for op in delta.operations:
            if getattr(op, "op_type", None) == "add_concept":
                c_uri = getattr(op, "concept_uri", None)
                if c_uri and c_uri in existing_concepts:
                    violations.append(
                        f"Duplicate concept conflict: '{c_uri}' already exists in base ontology."
                    )
                    primary_error_code = primary_error_code or "ONT_EVO_CONTRACT_VIOLATION"

        evo_res = validate_ontology_delta(base_ontology, delta)
        if not evo_res.valid:
            violations.extend(evo_res.violations)
            primary_error_code = primary_error_code or (
                evo_res.error_code or "ONT_EVO_CONTRACT_VIOLATION"
            )
        diagnostics.extend(evo_res.diagnostics)

    if delta is not None and not violations:
        from opendv_service.ontology.applier import apply as apply_delta

        try:
            target_ont = apply_delta(base_ontology, delta, validate_structural=True)
            dep_prov = build_workspace_deployment_provider(
                getattr(session_mgr, "deployment_workspace_root", None)
                or getattr(session_mgr, "workspace_root", None)
            )
            gv = GovernanceValidator(
                deployment_provider=dep_prov,
                waiver_verifier=_LocalWaiverVerifier(session_mgr.security_manager),
            )
            gv.validate_deployed_contracts(base_ontology, delta)

            allow_dry_run_inline = (
                bool(payload.low_confidence_waiver)
                and bool(
                    payload.grounding_rationale and len(payload.grounding_rationale.strip()) >= 10
                )
                and _has_valid_waiver_authorization(request, session_mgr, _principal)
            )
            dry_run_sub_sha = _compute_submitted_request_delta_sha256(
                payload=payload,
                norm_ops=norm_ops,
                fallback_base_version=meta.head_version,
                fallback_parent_sha256=meta.head_sha256,
                fallback_target_version=delta.target_version or meta.head_version,
                fallback_sha256=delta.canonical_sha256(),
            )
            dry_run_waivers = _build_and_sign_commit_waivers(
                sdir=sdir,
                session_mgr=session_mgr,
                base_ontology=base_ontology,
                delta=delta,
                principal=_principal,
                low_confidence_waiver=allow_dry_run_inline,
                grounding_rationale=(payload.grounding_rationale if allow_dry_run_inline else None),
                explicit_ungrounded=(payload.ungrounded_concept if allow_dry_run_inline else None),
                proposal_id=None,
                submitted_delta_sha256=dry_run_sub_sha,
            )
            gv.validate_grounding(
                base_ontology,
                delta,
                target_ont,
                waivers=dry_run_waivers or None,
                grandfathered_concept_uris=_load_grandfathered_concept_uris(sdir),
            )
        except OpenDVError as gov_exc:
            violations.append(str(gov_exc))
            raw_ec = getattr(gov_exc, "error_code", None)
            primary_error_code = primary_error_code or (
                raw_ec.value
                if hasattr(raw_ec, "value")
                else str(raw_ec or "ONT_EVO_GROUNDING_VIOLATION")
            )
        except (ValidationError, ValueError) as exc:
            violations.append(f"Semantic conflict: {exc}")
            primary_error_code = primary_error_code or "ONT_EVO_CONTRACT_VIOLATION"

    is_valid = len(violations) == 0
    return OntologyValidationResult(
        valid=is_valid,
        violations=violations,
        diagnostics=diagnostics,
        error_code=None if is_valid else (primary_error_code or "ONT_EVO_CONTRACT_VIOLATION"),
    )


@router.post("/sessions/{session_id}/delta", response_model=DeltaCommitResponse)
@router.post(
    "/sessions/{session_id}/deltas/apply",
    response_model=DeltaCommitResponse,
    include_in_schema=False,
)
def apply_delta_endpoint(
    request: Request,
    session_id: str,
    payload: OntologyDeltaRequest,
    session_mgr: Annotated[StudioSessionManager, Depends(get_session_manager)],
    principal: Annotated[StudioPrincipal, Depends(require_editor)],
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> DeltaCommitResponse:
    """Direct governed delta commit with strict OCC If-Match precondition."""
    # Step 2: Precondition syntax validation before RBAC field checks if header supplied
    if not if_match:
        raise StudioAPIError(
            "MISSING_PRECONDITION_HEADER",
            "Applying a direct delta requires an 'If-Match' header anchoring session head SHA-256.",
        )
    clean_if_match = _parse_strong_etag_or_400(if_match)

    meta = _get_session_or_404(session_mgr, session_id)
    sdir = session_mgr.get_session_dir(session_id)

    norm_ops = [_normalize_delta_operation(op) for op in payload.operations]
    if not norm_ops:
        if not principal.is_admin and payload.low_confidence_waiver:
            raise StudioAPIError(
                "FORBIDDEN_ADMIN_REQUIRED",
                "Administrative grounding waivers strictly require administrator role.",
            )
        if payload.low_confidence_waiver:
            _verify_waiver_key_header_if_present(request, session_mgr)
        raise StudioAPIError(
            "NO_OP_DELTA",
            "Direct delta commit requires at least one semantic operation.",
        )

    if payload.base_version is not None:
        _validate_version_param(payload.base_version)

    base_ontology = _load_session_version_ontology(sdir, meta, meta.head_version)
    eff_delta_id = payload.delta_id or f"delta-{uuid.uuid4().hex[:12]}"
    eff_base_version = (payload.base_version or meta.head_version).lstrip("v")

    # Early field-authorization check for explicit waivers or destructive ops (Step 7)
    has_destructive_raw = any(
        (op.get("op_type") if isinstance(op, dict) else getattr(op, "op_type", None))
        in DESTRUCTIVE_OP_TYPES
        for op in norm_ops
    )
    if (payload.low_confidence_waiver or has_destructive_raw) and not principal.is_admin:
        raise StudioAPIError(
            "FORBIDDEN_ADMIN_REQUIRED",
            "Destructive (MAJOR) ontology revisions and grounding waivers "
            "require administrator role.",
        )
    if payload.low_confidence_waiver:
        _verify_waiver_key_header_if_present(request, session_mgr)

    # Evaluate If-Match / OCC head precondition BEFORE SemVer/structural checks on head_version
    raw_body_parent = (
        payload.parent_sha256 if payload.parent_sha256 is not None else payload.base_sha256
    )
    active_ver_clean = meta.head_version.lstrip("v")

    if eff_base_version != active_ver_clean:
        raise StudioAPIError(
            "ONT_EVO_CONCURRENT_MODIFICATION",
            f"Delta base_version '{payload.base_version}' does not match "
            f"active head version '{meta.head_version}'.",
        )

    if active_ver_clean != GENESIS_VERSION and raw_body_parent == "":
        raise StudioAPIError(
            "ONT_EVO_CONCURRENT_MODIFICATION",
            f"Empty genesis base_sha256 cannot be committed against head '{meta.head_version}'.",
        )

    is_genesis_empty = active_ver_clean == GENESIS_VERSION and raw_body_parent in ("", None)
    clean_body_parent: str | None = None
    if not is_genesis_empty and raw_body_parent is not None:
        clean_body_parent = _parse_strong_etag_or_400(raw_body_parent, header_name="parent_sha256")
        if clean_if_match != clean_body_parent:
            raise StudioAPIError(
                "PRECONDITION_BASE_SHA_MISMATCH",
                f"If-Match header ('{clean_if_match}') does not match "
                f"body parent_sha256 ('{clean_body_parent}').",
            )

    if meta.head_sha256 != clean_if_match:
        raise StudioAPIError(
            "ONT_EVO_CONCURRENT_MODIFICATION",
            f"Session head SHA-256 ('{meta.head_sha256}') does not match "
            f"expected ('{clean_if_match}').",
        )

    if (
        active_ver_clean != GENESIS_VERSION
        and clean_body_parent is not None
        and clean_body_parent != meta.head_sha256
    ):
        raise StudioAPIError(
            "ONT_EVO_CONCURRENT_MODIFICATION",
            f"Delta base_sha256 ('{clean_body_parent}') does not match "
            f"session head SHA-256 ('{meta.head_sha256}').",
        )

    # Compute required SemVer BEFORE instantiating OntologyDelta (P10::MAJ-03 & MAJOR-08)
    try:
        expected_semver = _compute_expected_semver_for_ops(
            base_version=meta.head_version,
            base_sha256=meta.head_sha256,
            norm_ops=norm_ops,
            base_ontology=base_ontology,
            domain_namespace=meta.domain_namespace,
        )
    except OpenDVError as exc:
        raise _raise_from_opendv_error(exc) from exc
    except (ValidationError, ValueError, TypeError) as exc:
        raise StudioAPIError(
            "ONT_EVO_CONTRACT_VIOLATION",
            f"Delta structural validation failure: {exc}",
        ) from exc

    if payload.target_version and payload.target_version.lstrip("v") != expected_semver.lstrip("v"):
        raise StudioAPIError(
            "ONT_EVO_CONTRACT_VIOLATION",
            f"Target version '{payload.target_version}' does not match "
            f"required ONT-EVO-001 SemVer bump '{expected_semver}'.",
        )

    try:
        delta = OntologyDelta(
            delta_id=eff_delta_id,
            parent_sha256=meta.head_sha256,
            base_version=meta.head_version,
            target_version=expected_semver,
            domain_namespace=meta.domain_namespace,
            operations=norm_ops,
            author=principal.principal,
            rationale=payload.rationale or payload.grounding_rationale,
        )
    except (ValidationError, ValueError, TypeError) as exc:
        raise StudioAPIError(
            "ONT_EVO_CONTRACT_VIOLATION",
            f"Delta structural validation failure: {exc}",
        ) from exc

    # Step 7: Full semantic destructive check (e.g., narrowing type / non-nullable update)
    requires_admin = is_destructive_delta(delta, base_ontology) or payload.low_confidence_waiver
    if requires_admin and not principal.is_admin:
        raise StudioAPIError(
            "FORBIDDEN_ADMIN_REQUIRED",
            "Destructive (MAJOR) ontology revisions and grounding waivers "
            "require administrator role.",
        )

    # Step 8: Waiver validation & cryptographic K_waiver signing
    submitted_delta_sha = _compute_submitted_request_delta_sha256(
        payload=payload,
        norm_ops=norm_ops,
        fallback_base_version=meta.head_version,
        fallback_parent_sha256=meta.head_sha256,
        fallback_target_version=expected_semver,
        fallback_sha256=delta.canonical_sha256(),
    )
    signed_waivers = _build_and_sign_commit_waivers(
        sdir=sdir,
        session_mgr=session_mgr,
        base_ontology=base_ontology,
        delta=delta,
        principal=principal,
        low_confidence_waiver=payload.low_confidence_waiver,
        grounding_rationale=payload.grounding_rationale,
        explicit_ungrounded=payload.ungrounded_concept,
        proposal_id=None,
        submitted_delta_sha256=submitted_delta_sha,
    )

    atomic_svc = AtomicCommitService(session_manager=session_mgr)
    grandfathered_uris = _load_grandfathered_concept_uris(sdir)
    try:
        commit_res = atomic_svc.commit_delta(
            session_id=session_id,
            delta=delta,
            proposal_id=None,
            author=principal.principal,
            actor_role=principal.role,
            if_match=clean_if_match,
            waivers=signed_waivers or None,
            grandfathered_concept_uris=grandfathered_uris,
        )
    except ConcurrentModificationError as exc:
        raise StudioAPIError("ONT_EVO_CONCURRENT_MODIFICATION", str(exc)) from exc
    except OpenDVError as exc:
        raise _raise_from_opendv_error(exc) from exc

    return DeltaCommitResponse(
        session_id=session_id,
        target_version=commit_res.version.lstrip("v"),
        target_sha256=commit_res.target_sha256,
        parent_sha256=raw_body_parent if raw_body_parent is not None else meta.head_sha256,
        delta_id=delta.delta_id,
        bump_type=delta.highest_semver_class(base_ontology),
        applied_operations_count=len(delta.operations),
    )


@router.get("/sessions/{session_id}/proposals", response_model=list[ProposalRecordDTO])
def list_proposals(
    session_id: str,
    session_mgr: Annotated[StudioSessionManager, Depends(get_session_manager)],
    _principal: Annotated[StudioPrincipal, Depends(require_viewer)],
) -> list[ProposalRecordDTO]:
    """List all candidate proposals with current status."""
    _get_session_or_404(session_mgr, session_id)
    sdir = session_mgr.get_session_dir(session_id)

    proposals_dir = sdir / "proposals"
    if not proposals_dir.is_dir():
        return []

    records: list[ProposalRecordDTO] = []
    for pfile in sorted(proposals_dir.glob("*.json")):
        try:
            raw_data = json.loads(pfile.read_text(encoding="utf-8"))
            if not isinstance(raw_data, dict):
                continue
            data = _normalize_proposal_record(
                raw_data, session_id=session_id, fallback_proposal_id=pfile.stem
            )
            records.append(
                ProposalRecordDTO(
                    proposal_id=data["proposal_id"],
                    session_id=session_id,
                    base_version=data["base_version"],
                    base_sha256=data["base_sha256"],
                    status=data["status"],
                    operations=data["operations"],
                    proposer=data["proposer"],
                    created_at=data["created_at"],
                    rationale=data["rationale"],
                )
            )
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            logger.debug("Skipping unreadable proposal file %s: %s", pfile, exc)
            continue
    return records


def _resolve_proposal_base_version_and_sha(
    sdir: Path,
    meta: SessionMetadata,
    req_base_version: str | None,
    req_base_sha256: str | None,
) -> tuple[str, str, GraphAOntology]:
    """Resolve exact proposal base_version, JCS SHA-256, and base ontology (`P10b MAJ-02`)."""
    clean_req_sha: str | None = None
    if req_base_sha256 is not None:
        s = req_base_sha256.strip()
        if s.startswith('"') and s.endswith('"') and len(s) == 66:
            s = s[1:-1]
        clean_req_sha = s

    head_ver = meta.head_version.lstrip("v")

    if req_base_version is not None:
        _ensure_session_version_exists(sdir, req_base_version)
        base_version = req_base_version.lstrip("v")
        base_ont = _load_session_version_ontology(sdir, meta, base_version)
        version_sha256 = (
            meta.head_sha256 if base_version == head_ver else compute_ontology_sha256(base_ont)
        )
        if (
            clean_req_sha is not None
            and not (clean_req_sha == "" and base_version == GENESIS_VERSION)
            and clean_req_sha != version_sha256
        ):
            raise StudioAPIError(
                "PRECONDITION_BASE_SHA_MISMATCH",
                f"Provided base_sha256 ('{req_base_sha256}') does not match "
                f"version 'v{base_version}' SHA-256 ('{version_sha256}').",
            )
        return base_version, version_sha256, base_ont

    if (
        clean_req_sha is None
        or clean_req_sha == meta.head_sha256
        or (clean_req_sha == "" and head_ver == GENESIS_VERSION)
    ):
        base_ont = _load_session_version_ontology(sdir, meta, head_ver)
        return head_ver, meta.head_sha256, base_ont

    versions_dir = sdir / "versions"
    if versions_dir.is_dir():
        for vdir in sorted(versions_dir.iterdir()):
            if not vdir.is_dir():
                continue
            cand_ver = vdir.name.lstrip("v")
            with contextlib.suppress(StudioAPIError):
                cand_ont = _load_session_version_ontology(sdir, meta, cand_ver)
                cand_sha = compute_ontology_sha256(cand_ont)
                if cand_sha == clean_req_sha:
                    return cand_ver, cand_sha, cand_ont

    raise StudioAPIError(
        "PRECONDITION_BASE_SHA_MISMATCH",
        f"Provided base_sha256 ('{req_base_sha256}') does not match active head "
        f"('{meta.head_sha256}') or any committed session version.",
    )


@router.post("/sessions/{session_id}/proposals", response_model=ProposalRecordDTO, status_code=201)
def create_proposal(
    session_id: str,
    payload: ProposalCreateRequest,
    session_mgr: Annotated[StudioSessionManager, Depends(get_session_manager)],
    principal: Annotated[StudioPrincipal, Depends(require_editor_or_automated)],
) -> ProposalRecordDTO:
    """Submit candidate proposal without committing (automated restricted to additive ops)."""
    meta = _get_session_or_404(session_mgr, session_id)
    sdir = session_mgr.get_session_dir(session_id)

    if payload.proposal_id:
        _validate_proposal_id_param(payload.proposal_id)
        proposal_id = payload.proposal_id
    else:
        proposal_id = f"prop-{uuid.uuid4().hex[:12]}"

    now_iso = datetime.now(UTC).isoformat()
    norm_ops = [_normalize_delta_operation(op) for op in payload.operations]
    if principal.is_automated and any(
        isinstance(op, dict) and op.get("op_type") in DESTRUCTIVE_OP_TYPES for op in norm_ops
    ):
        raise StudioAPIError(
            "AGENT_DESTRUCTIVE_OP_FORBIDDEN",
            "Automated principals are strictly forbidden from submitting destructive operations.",
        )

    base_version, version_sha256, base_ont = _resolve_proposal_base_version_and_sha(
        sdir=sdir,
        meta=meta,
        req_base_version=payload.base_version,
        req_base_sha256=payload.base_sha256,
    )
    base_sha256 = version_sha256

    try:
        t_ver = _compute_expected_semver_for_ops(
            base_version=base_version,
            base_sha256=version_sha256,
            norm_ops=norm_ops,
            base_ontology=base_ont,
            domain_namespace=meta.domain_namespace,
        )
        candidate_delta = OntologyDelta(
            delta_id=f"delta-{uuid.uuid4().hex[:12]}",
            parent_sha256=version_sha256,
            base_version=base_version,
            target_version=t_ver,
            domain_namespace=meta.domain_namespace,
            operations=norm_ops,
            author=principal.principal,
            rationale=payload.rationale,
        )
    except OpenDVError as exc:
        raise _raise_from_opendv_error(exc) from exc
    except (ValidationError, ValueError, TypeError) as exc:
        raise StudioAPIError(
            "ONT_EVO_CONTRACT_VIOLATION",
            f"Invalid proposal operations: {exc}",
        ) from exc

    # CRITICAL-01: Automated / agent:* callers are strictly forbidden from proposing destructive ops
    if principal.is_automated and is_destructive_delta(candidate_delta, base_ont):
        raise StudioAPIError(
            "AGENT_DESTRUCTIVE_OP_FORBIDDEN",
            "Automated principals are strictly forbidden from submitting destructive operations.",
        )

    record = {
        "proposal_id": proposal_id,
        "session_id": session_id,
        "base_version": base_version,
        "base_sha256": base_sha256,
        "status": "PENDING",
        "operations": norm_ops,
        "proposer": principal.principal,
        "created_at": now_iso,
        "rationale": payload.rationale,
        "delta": candidate_delta.model_dump(mode="json", exclude_none=True),
    }

    with reentrant_session_lock(sdir / ".session.lock", session_id=session_id):
        proposals_dir = sdir / "proposals"
        proposals_dir.mkdir(parents=True, exist_ok=True)
        prop_file = _resolve_proposal_file(sdir, proposal_id)
        if prop_file.exists():
            raise StudioAPIError(
                "PROPOSAL_ALREADY_RESOLVED",
                f"Proposal '{proposal_id}' already exists in session '{session_id}'.",
            )
        tmp_prop = proposals_dir / f".{proposal_id}.{uuid.uuid4().hex[:8]}.tmp"
        tmp_prop.write_text(json.dumps(record, indent=2), encoding="utf-8")
        replace_with_retry(tmp_prop, prop_file)
        set_owner_only_permissions(prop_file)

    return ProposalRecordDTO(**record)


@router.get("/sessions/{session_id}/proposals/{proposal_id}", response_model=ProposalRecordDTO)
def get_proposal_detail(
    session_id: str,
    proposal_id: str,
    session_mgr: Annotated[StudioSessionManager, Depends(get_session_manager)],
    _principal: Annotated[StudioPrincipal, Depends(require_viewer)],
) -> ProposalRecordDTO:
    """Fetch proposal record and conflict status."""
    _get_session_or_404(session_mgr, session_id)
    sdir = session_mgr.get_session_dir(session_id)
    prop_file = _resolve_proposal_file(sdir, proposal_id)
    if not prop_file.is_file():
        raise StudioAPIError("PROPOSAL_NOT_FOUND", f"Proposal '{proposal_id}' not found.")

    try:
        raw_data = json.loads(prop_file.read_text(encoding="utf-8"))
        if not isinstance(raw_data, dict):
            raise StudioAPIError(
                "CORRUPT_SESSION_RECORD",
                f"Corrupt proposal record '{proposal_id}': expected JSON object.",
            )
    except StudioAPIError:
        raise
    except (OSError, json.JSONDecodeError) as exc:
        raise StudioAPIError(
            "CORRUPT_SESSION_RECORD",
            f"Corrupt proposal record '{proposal_id}': {exc}",
        ) from exc

    data = _normalize_proposal_record(
        raw_data, session_id=session_id, fallback_proposal_id=proposal_id
    )
    return ProposalRecordDTO(
        proposal_id=data["proposal_id"],
        session_id=session_id,
        base_version=data["base_version"],
        base_sha256=data["base_sha256"],
        status=data["status"],
        operations=data["operations"],
        proposer=data["proposer"],
        created_at=data["created_at"],
        rationale=data["rationale"],
    )


@router.post(
    "/sessions/{session_id}/proposals/{proposal_id}/accept", response_model=DeltaCommitResponse
)
def accept_proposal(
    request: Request,
    session_id: str,
    proposal_id: str,
    payload: ProposalAcceptRequest,
    session_mgr: Annotated[StudioSessionManager, Depends(get_session_manager)],
    principal: Annotated[StudioPrincipal, Depends(require_viewer)],
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> DeltaCommitResponse:
    """Accept pending proposal enforcing 11-step precedence, RBAC, strong ETag OCC, and ONT-EVO."""
    # Step 2: Strong ETag syntax check when If-Match header is supplied
    clean_if_match_header = _parse_strong_etag_or_400(if_match) if if_match is not None else None

    # Step 3: RBAC & Actor Privileges
    if principal.is_automated:
        raise StudioAPIError(
            "AGENT_PROPOSAL_ACCEPT_FORBIDDEN",
            "Automated agent identities are strictly forbidden from accepting proposals.",
        )
    if principal.role == "viewer":
        raise StudioAPIError(
            "SERVER_OPERATION_FORBIDDEN",
            "Read-only 'viewer' role is forbidden from accepting proposals.",
        )
    if (
        payload.low_confidence_waiver or payload.self_acceptance_override
    ) and not principal.is_admin:
        raise StudioAPIError(
            "FORBIDDEN_ADMIN_REQUIRED",
            "Destructive revisions, grounding waivers, and self-acceptance overrides "
            "require admin role.",
        )
    if payload.low_confidence_waiver:
        _verify_waiver_key_header_if_present(request, session_mgr)

    meta = _get_session_or_404(session_mgr, session_id)
    sdir = session_mgr.get_session_dir(session_id)
    prop_file = _resolve_proposal_file(sdir, proposal_id)
    if not prop_file.is_file():
        raise StudioAPIError("PROPOSAL_NOT_FOUND", f"Proposal '{proposal_id}' not found.")

    try:
        raw_prop = json.loads(prop_file.read_text(encoding="utf-8"))
        if not isinstance(raw_prop, dict):
            raise StudioAPIError(
                "CORRUPT_SESSION_RECORD",
                f"Corrupt proposal record '{proposal_id}': expected JSON object.",
            )
    except StudioAPIError:
        raise
    except (OSError, json.JSONDecodeError) as exc:
        raise StudioAPIError(
            "CORRUPT_SESSION_RECORD",
            f"Corrupt proposal record '{proposal_id}': {exc}",
        ) from exc

    prop_data = _normalize_proposal_record(
        raw_prop, session_id=session_id, fallback_proposal_id=proposal_id
    )
    if prop_data.get("status") in ("ACCEPTED", "REJECTED"):
        raise StudioAPIError(
            "PROPOSAL_ALREADY_RESOLVED",
            f"Proposal '{proposal_id}' has already been resolved ({prop_data.get('status')}).",
        )

    raw_ops = prop_data.get("operations", [])
    ops = [_normalize_delta_operation(op) for op in raw_ops]
    base_ontology = _load_session_version_ontology(sdir, meta, meta.head_version)

    # Build candidate delta with pre-computed SemVer (MAJOR-08)
    try:
        next_ver = _compute_expected_semver_for_ops(
            base_version=meta.head_version,
            base_sha256=meta.head_sha256,
            norm_ops=ops,
            base_ontology=base_ontology,
            domain_namespace=meta.domain_namespace,
        )
        delta = OntologyDelta(
            delta_id=f"delta-{uuid.uuid4().hex[:12]}",
            parent_sha256=meta.head_sha256,
            base_version=meta.head_version,
            target_version=next_ver,
            domain_namespace=meta.domain_namespace,
            operations=ops,
            author=principal.principal,
            rationale=prop_data.get("rationale") or payload.grounding_rationale,
        )
    except OpenDVError as exc:
        raise _raise_from_opendv_error(exc) from exc
    except (ValidationError, ValueError, TypeError) as exc:
        raise StudioAPIError(
            "ONT_EVO_CONTRACT_VIOLATION",
            f"Proposal delta construction failure: {exc}",
        ) from exc

    # Step 7: Field Authorization BEFORE Step 8 Waiver Rationale Length (P10::MAJ-02)
    requires_admin = (
        is_destructive_delta(delta, base_ontology)
        or payload.low_confidence_waiver
        or payload.self_acceptance_override
    )
    if requires_admin and not principal.is_admin:
        raise StudioAPIError(
            "FORBIDDEN_ADMIN_REQUIRED",
            "Destructive revisions, grounding waivers, and self-acceptance overrides "
            "require admin role.",
        )

    proposer = prop_data.get("proposer")
    if proposer == principal.principal and not payload.self_acceptance_override:
        raise StudioAPIError(
            "PROPOSAL_SELF_ACCEPTANCE_FORBIDDEN",
            "Self-acceptance of proposals is forbidden without "
            "--allow-self-accepted-proposals override.",
        )

    # Step 8: Waiver validation & K_waiver signing
    submitted_prop_delta_sha = _compute_stored_proposal_delta_sha256(
        sdir=sdir,
        meta=meta,
        raw_prop=raw_prop,
        proposal_id=proposal_id,
    )
    signed_waivers = _build_and_sign_commit_waivers(
        sdir=sdir,
        session_mgr=session_mgr,
        base_ontology=base_ontology,
        delta=delta,
        principal=principal,
        low_confidence_waiver=payload.low_confidence_waiver,
        grounding_rationale=payload.grounding_rationale,
        explicit_ungrounded=payload.ungrounded_concept,
        proposal_id=proposal_id,
        submitted_delta_sha256=submitted_prop_delta_sha,
    )

    # Step 9: Strong ETag Precondition & OCC Check
    clean_body_sha = (
        _parse_strong_etag_or_400(payload.base_sha256, header_name="base_sha256")
        if payload.base_sha256
        else None
    )
    if clean_if_match_header is None and clean_body_sha is None:
        raise StudioAPIError(
            "MISSING_PRECONDITION_HEADER",
            "Accepting a proposal requires an 'If-Match' header anchoring session head SHA-256.",
        )
    if (
        clean_if_match_header is not None
        and clean_body_sha is not None
        and clean_if_match_header != clean_body_sha
    ):
        raise StudioAPIError(
            "PRECONDITION_BASE_SHA_MISMATCH",
            f"If-Match header ('{clean_if_match_header}') does not match "
            f"body base_sha256 ('{clean_body_sha}').",
        )
    clean_if_match = clean_if_match_header or clean_body_sha
    if clean_if_match != meta.head_sha256:
        raise StudioAPIError(
            "ONT_EVO_CONCURRENT_MODIFICATION",
            f"If-Match ('{clean_if_match}') does not match "
            f"session head SHA-256 ('{meta.head_sha256}').",
        )

    prop_base = prop_data.get("base_sha256") or ""
    is_genesis = prop_base == "" and meta.head_version == GENESIS_VERSION
    if prop_base != meta.head_sha256 and not is_genesis:
        raise StudioAPIError(
            "PROPOSAL_STALE_REBASE_REQUIRED",
            f"Proposal base SHA-256 ('{prop_base}') does not match current session head "
            f"('{meta.head_sha256}'). Proposal rebase is required.",
        )

    atomic_svc = AtomicCommitService(session_manager=session_mgr)
    eval_dict: dict[str, Any] | None = None
    if signed_waivers:
        eval_dict = {"waivers": [w.model_dump(mode="json") for w in signed_waivers]}

    grandfathered_uris = _load_grandfathered_concept_uris(sdir)
    try:
        commit_res = atomic_svc.commit_delta(
            session_id=session_id,
            delta=delta,
            proposal_id=proposal_id,
            author=principal.principal,
            actor_role=principal.role,
            if_match=clean_if_match,
            evaluation=eval_dict,
            waivers=signed_waivers or None,
            grandfathered_concept_uris=grandfathered_uris,
        )
    except ConcurrentModificationError as exc:
        if "already been resolved" in str(exc):
            raise StudioAPIError(
                "PROPOSAL_ALREADY_RESOLVED",
                f"Proposal '{proposal_id}' has already been resolved.",
            ) from exc
        raise StudioAPIError("ONT_EVO_CONCURRENT_MODIFICATION", str(exc)) from exc
    except OpenDVError as exc:
        raise _raise_from_opendv_error(exc) from exc

    return DeltaCommitResponse(
        session_id=session_id,
        target_version=commit_res.version.lstrip("v"),
        target_sha256=commit_res.target_sha256,
        parent_sha256=meta.head_sha256,
        delta_id=delta.delta_id,
        bump_type=delta.highest_semver_class(base_ontology),
        applied_operations_count=len(delta.operations),
    )


@router.post(
    "/sessions/{session_id}/proposals/{proposal_id}/reject", response_model=ProposalRecordDTO
)
def reject_proposal(
    session_id: str,
    proposal_id: str,
    session_mgr: Annotated[StudioSessionManager, Depends(get_session_manager)],
    principal: Annotated[StudioPrincipal, Depends(require_editor)],
    payload: RejectProposalRequest | None = None,
) -> ProposalRecordDTO:
    """Mark proposal REJECTED while preserving in CAS for audit trace (human editor/admin)."""
    if principal.is_automated:
        raise StudioAPIError(
            "AGENT_OPERATION_FORBIDDEN",
            "Automated agent principals cannot reject proposals.",
        )

    _get_session_or_404(session_mgr, session_id)
    sdir = session_mgr.get_session_dir(session_id)
    prop_file = _resolve_proposal_file(sdir, proposal_id)
    if not prop_file.is_file():
        raise StudioAPIError("PROPOSAL_NOT_FOUND", f"Proposal '{proposal_id}' not found.")

    with reentrant_session_lock(sdir / ".session.lock", session_id=session_id):
        try:
            raw_prop = json.loads(prop_file.read_text(encoding="utf-8"))
            if not isinstance(raw_prop, dict):
                raise StudioAPIError(
                    "CORRUPT_SESSION_RECORD",
                    f"Corrupt proposal record '{proposal_id}': expected JSON object.",
                )
        except StudioAPIError:
            raise
        except (OSError, json.JSONDecodeError) as exc:
            raise StudioAPIError(
                "CORRUPT_SESSION_RECORD",
                f"Corrupt proposal record '{proposal_id}': {exc}",
            ) from exc

        prop_data = _normalize_proposal_record(
            raw_prop, session_id=session_id, fallback_proposal_id=proposal_id
        )
        if prop_data.get("status") in ("ACCEPTED", "REJECTED"):
            raise StudioAPIError(
                "PROPOSAL_ALREADY_RESOLVED",
                f"Proposal '{proposal_id}' has already been resolved ({prop_data.get('status')}).",
            )

        reason = payload.reason if payload and payload.reason else "Rejected by steward"
        prop_data["status"] = "REJECTED"
        prop_data["reject_reason"] = reason
        prop_data["resolved_at"] = datetime.now(UTC).isoformat()
        tmp_prop = sdir / "proposals" / f".{proposal_id}.{uuid.uuid4().hex[:8]}.tmp"
        tmp_prop.write_text(json.dumps(prop_data, indent=2), encoding="utf-8")
        replace_with_retry(tmp_prop, prop_file)
        set_owner_only_permissions(prop_file)

        append_signed_audit_record(
            session_dir=sdir,
            action="PROPOSAL_REJECTED",
            actor=principal.principal,
            details={"proposal_id": proposal_id, "reason": reason},
            security_manager=session_mgr.security_manager,
        )

    return ProposalRecordDTO(
        proposal_id=prop_data["proposal_id"],
        session_id=session_id,
        base_version=prop_data["base_version"],
        base_sha256=prop_data["base_sha256"],
        status="REJECTED",
        operations=prop_data["operations"],
        proposer=prop_data["proposer"],
        created_at=prop_data["created_at"],
        rationale=prop_data["rationale"],
    )


def _load_version_ontology_fail_closed(
    sdir: Path,
    meta: SessionMetadata,
    version_str: str,
) -> GraphAOntology:
    """Load version ontology failing closed (`CORRUPT_SESSION_RECORD` vs non-ancestor 409)."""
    clean_ver = str(version_str).strip().lstrip("v") or GENESIS_VERSION
    if clean_ver != GENESIS_VERSION:
        vdir = sdir / "versions" / normalize_version_tag(clean_ver)
        if not vdir.is_dir():
            committed_tags = {
                normalize_version_tag(str(cv).strip().lstrip("v"))
                for cv in meta.committed_versions
                if str(cv).strip()
            }
            if normalize_version_tag(
                clean_ver
            ) in committed_tags or clean_ver == meta.head_version.lstrip("v"):
                raise StudioAPIError(
                    "CORRUPT_SESSION_RECORD",
                    f"Missing version directory 'v{clean_ver}' in session '{meta.session_id}'.",
                )
            raise StudioAPIError(
                "PROPOSAL_STALE_REBASE_REQUIRED",
                f"Proposal base_version 'v{clean_ver}' does not exist in session history.",
            )

    try:
        return _load_session_version_ontology(sdir, meta, clean_ver)
    except (StudioAPIError, OSError, ValidationError, ValueError, yaml.YAMLError) as exc:
        raise StudioAPIError(
            "CORRUPT_SESSION_RECORD",
            f"Corrupt or missing ontology artifact for version 'v{clean_ver}' in session "
            f"'{meta.session_id}': {exc}",
        ) from exc


def _collect_intervening_deltas(
    sdir: Path,
    meta: SessionMetadata,
    prop_base_version: str,
    prop_base_sha256: str,
    base_ontology_sha256: str,
) -> list[OntologyDelta]:
    """Load and cryptographically verify intervening deltas from prop_base up to HEAD."""
    clean_base_ver = str(prop_base_version or "").strip().lstrip("v") or GENESIS_VERSION
    clean_head_ver = str(meta.head_version or "").strip().lstrip("v")
    try:
        base_tup = parse_semver_tuple(clean_base_ver)
    except ValueError as exc:
        raise StudioAPIError(
            "CORRUPT_SESSION_RECORD",
            f"Malformed proposal base_version '{prop_base_version}': {exc}",
        ) from exc
    try:
        head_tup = parse_semver_tuple(clean_head_ver)
    except ValueError as exc:
        raise StudioAPIError(
            "CORRUPT_SESSION_RECORD",
            f"Malformed session head_version '{meta.head_version}': {exc}",
        ) from exc

    if base_tup > head_tup:
        raise StudioAPIError(
            "PROPOSAL_STALE_REBASE_REQUIRED",
            f"Proposal base_version '{prop_base_version}' exceeds session head_version "
            f"'{meta.head_version}'.",
        )

    expected_head_sha = str(meta.head_sha256 or "").strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", expected_head_sha):
        raise StudioAPIError(
            "CORRUPT_SESSION_RECORD",
            f"Corrupt session head_sha256 in session '{meta.session_id}'.",
        )

    eff_prop_base_sha = str(prop_base_sha256 or "").strip().lower()
    if eff_prop_base_sha:
        if not re.fullmatch(r"[0-9a-f]{64}", eff_prop_base_sha):
            raise StudioAPIError(
                "CORRUPT_SESSION_RECORD",
                f"Malformed proposal base_sha256 '{prop_base_sha256}'.",
            )
    elif clean_base_ver == GENESIS_VERSION:
        eff_prop_base_sha = base_ontology_sha256.strip().lower()
    else:
        raise StudioAPIError(
            "CORRUPT_SESSION_RECORD",
            f"Empty proposal base_sha256 for non-genesis base_version '{prop_base_version}'.",
        )

    versions_dir = sdir / "versions"
    if not versions_dir.is_dir():
        if base_tup < head_tup or meta.committed_versions:
            raise StudioAPIError(
                "CORRUPT_SESSION_RECORD",
                f"Missing versions directory in session '{meta.session_id}'.",
            )
        if eff_prop_base_sha != expected_head_sha:
            raise StudioAPIError(
                "PROPOSAL_STALE_REBASE_REQUIRED",
                f"Proposal base SHA-256 ('{eff_prop_base_sha}') is not an ancestor of "
                f"session head ('{meta.head_sha256}').",
            )
        return []

    dir_by_tuple: dict[tuple[int, int, int], Path] = {}
    for vdir in sorted(versions_dir.iterdir()):
        if not vdir.is_dir():
            continue
        if not vdir.name.startswith("v") or len(vdir.name) < 2:
            raise StudioAPIError(
                "CORRUPT_SESSION_RECORD",
                f"Malformed version directory '{vdir.name}' in session '{meta.session_id}'.",
            )
        v_clean = vdir.name[1:]
        try:
            v_tup = parse_semver_tuple(v_clean)
        except ValueError as exc:
            raise StudioAPIError(
                "CORRUPT_SESSION_RECORD",
                f"Malformed SemVer version directory '{vdir.name}' in session "
                f"'{meta.session_id}': {exc}",
            ) from exc
        if v_tup in dir_by_tuple:
            raise StudioAPIError(
                "CORRUPT_SESSION_RECORD",
                f"Duplicate SemVer version directory '{vdir.name}' in session '{meta.session_id}'.",
            )
        dir_by_tuple[v_tup] = vdir

    for cv in meta.committed_versions:
        cv_clean = str(cv or "").strip().lstrip("v")
        try:
            cv_tup = parse_semver_tuple(cv_clean)
        except ValueError as exc:
            raise StudioAPIError(
                "CORRUPT_SESSION_RECORD",
                f"Malformed committed_versions entry '{cv}' in session '{meta.session_id}': {exc}",
            ) from exc
        if base_tup < cv_tup <= head_tup and cv_tup not in dir_by_tuple:
            raise StudioAPIError(
                "CORRUPT_SESSION_RECORD",
                f"Missing version directory for committed version '{cv}' in session "
                f"'{meta.session_id}'.",
            )

    if base_tup == head_tup:
        if eff_prop_base_sha != expected_head_sha:
            raise StudioAPIError(
                "PROPOSAL_STALE_REBASE_REQUIRED",
                f"Proposal base SHA-256 ('{eff_prop_base_sha}') does not match "
                f"session head ('{meta.head_sha256}').",
            )
        return []

    collected: list[tuple[tuple[int, int, int], OntologyDelta, str, str]] = []
    for v_tup in sorted(dir_by_tuple):
        if not (base_tup < v_tup <= head_tup):
            continue
        vdir = dir_by_tuple[v_tup]
        delta_file = vdir / "delta.json"
        if not delta_file.is_file():
            raise StudioAPIError(
                "CORRUPT_SESSION_RECORD",
                f"Missing delta.json for version '{vdir.name}' in session '{meta.session_id}'.",
            )
        try:
            d = OntologyDelta.model_validate_json(delta_file.read_text(encoding="utf-8"))
        except (OSError, ValidationError, ValueError) as exc:
            raise StudioAPIError(
                "CORRUPT_SESSION_RECORD",
                f"Corrupt delta.json for version '{vdir.name}' in session "
                f"'{meta.session_id}': {exc}",
            ) from exc

        meta_file = vdir / "meta.json"
        if not meta_file.is_file():
            meta_file = vdir / "metadata.json"
        if not meta_file.is_file():
            raise StudioAPIError(
                "CORRUPT_SESSION_RECORD",
                f"Missing meta.json for version '{vdir.name}' in session '{meta.session_id}'.",
            )
        try:
            vmeta = json.loads(meta_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise StudioAPIError(
                "CORRUPT_SESSION_RECORD",
                f"Corrupt {meta_file.name} for version '{vdir.name}' in session "
                f"'{meta.session_id}': {exc}",
            ) from exc
        if not isinstance(vmeta, dict):
            raise StudioAPIError(
                "CORRUPT_SESSION_RECORD",
                f"Corrupt {meta_file.name} for version '{vdir.name}': expected JSON object.",
            )

        v_parent_sha = str(vmeta.get("parent_sha256") or "").strip().lower()
        v_target_sha = str(vmeta.get("target_sha256") or "").strip().lower()
        if not re.fullmatch(r"[0-9a-f]{64}", v_parent_sha) or not re.fullmatch(
            r"[0-9a-f]{64}", v_target_sha
        ):
            raise StudioAPIError(
                "CORRUPT_SESSION_RECORD",
                f"Corrupt SHA-256 lineage metadata in {meta_file.name} for version '{vdir.name}'.",
            )

        try:
            d_target_tup = parse_semver_tuple((d.target_version or "").lstrip("v"))
        except ValueError as exc:
            raise StudioAPIError(
                "CORRUPT_SESSION_RECORD",
                f"Corrupt delta target_version in '{vdir.name}': {exc}",
            ) from exc
        if d_target_tup != v_tup:
            raise StudioAPIError(
                "CORRUPT_SESSION_RECORD",
                f"Delta target_version '{d.target_version}' mismatches directory '{vdir.name}'.",
            )

        d_parent_sha = (d.parent_sha256 or "").strip().lower()
        if d_parent_sha and d_parent_sha != v_parent_sha:
            raise StudioAPIError(
                "CORRUPT_SESSION_RECORD",
                f"Delta parent_sha256 mismatches {meta_file.name} parent_sha256 in '{vdir.name}'.",
            )

        v_ont = _load_version_ontology_fail_closed(sdir, meta, vdir.name.lstrip("v"))
        if compute_ontology_sha256(v_ont).lower() != v_target_sha:
            raise StudioAPIError(
                "CORRUPT_SESSION_RECORD",
                f"Ontology digest mismatch for version '{vdir.name}' in session "
                f"'{meta.session_id}'.",
            )

        collected.append((v_tup, d, v_parent_sha, v_target_sha))

    if not collected:
        raise StudioAPIError(
            "CORRUPT_SESSION_RECORD",
            f"Missing intervening deltas between '{prop_base_version}' and "
            f"'{meta.head_version}' in session '{meta.session_id}'.",
        )

    clean_base_ont_sha = base_ontology_sha256.strip().lower()
    if eff_prop_base_sha != clean_base_ont_sha:
        raise StudioAPIError(
            "PROPOSAL_STALE_REBASE_REQUIRED",
            f"Proposal base SHA-256 ('{eff_prop_base_sha}') is not an ancestor of "
            f"session head ('{meta.head_sha256}').",
        )

    first_parent_sha = collected[0][2]
    if first_parent_sha != eff_prop_base_sha:
        raise StudioAPIError(
            "CORRUPT_SESSION_RECORD",
            f"Broken SHA-256 lineage at version '{collected[0][1].target_version}': "
            f"expected parent '{eff_prop_base_sha}', got '{first_parent_sha}'.",
        )

    prev_target_sha = collected[0][3]
    for v_tup, d, v_parent_sha, v_target_sha in collected[1:]:
        if v_parent_sha != prev_target_sha:
            raise StudioAPIError(
                "CORRUPT_SESSION_RECORD",
                f"Broken SHA-256 lineage at version 'v{v_tup[0]}.{v_tup[1]}.{v_tup[2]}' "
                f"('{d.delta_id}'): parent '{v_parent_sha}' != prev target '{prev_target_sha}'.",
            )
        prev_target_sha = v_target_sha

    if collected[-1][0] != head_tup or prev_target_sha != expected_head_sha:
        raise StudioAPIError(
            "CORRUPT_SESSION_RECORD",
            f"Terminal version lineage ('{prev_target_sha}') does not match session "
            f"head_sha256 ('{meta.head_sha256}').",
        )

    return [d for _, d, _, _ in collected]


@router.post(
    "/sessions/{session_id}/proposals/{proposal_id}/rebase", response_model=ProposalRecordDTO
)
def rebase_proposal(
    session_id: str,
    proposal_id: str,
    payload: ProposalRebaseRequest,
    session_mgr: Annotated[StudioSessionManager, Depends(get_session_manager)],
    principal: Annotated[StudioPrincipal, Depends(require_editor)],
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> ProposalRecordDTO:
    """Rebase proposal onto new session head via 24-op conflict matrix (P10::MAJ-04, MAJOR-06)."""
    clean_if_match_header = _parse_strong_etag_or_400(if_match) if if_match is not None else None
    clean_body_sha = (
        _parse_strong_etag_or_400(payload.base_sha256, header_name="base_sha256")
        if payload.base_sha256
        else None
    )
    if clean_if_match_header is None and clean_body_sha is None:
        raise StudioAPIError(
            "MISSING_PRECONDITION_HEADER",
            "Rebasing a proposal requires an 'If-Match' header anchoring session head SHA-256.",
        )
    if (
        clean_if_match_header is not None
        and clean_body_sha is not None
        and clean_if_match_header != clean_body_sha
    ):
        raise StudioAPIError(
            "PRECONDITION_BASE_SHA_MISMATCH",
            f"If-Match header ('{clean_if_match_header}') does not match "
            f"body base_sha256 ('{clean_body_sha}').",
        )
    clean_if_match = clean_if_match_header or clean_body_sha

    _get_session_or_404(session_mgr, session_id)
    sdir = session_mgr.get_session_dir(session_id)
    prop_file = _resolve_proposal_file(sdir, proposal_id)
    if not prop_file.is_file():
        raise StudioAPIError("PROPOSAL_NOT_FOUND", f"Proposal '{proposal_id}' not found.")

    with reentrant_session_lock(sdir / ".session.lock", session_id=session_id):
        try:
            raw_prop = json.loads(prop_file.read_text(encoding="utf-8"))
            if not isinstance(raw_prop, dict):
                raise StudioAPIError(
                    "CORRUPT_SESSION_RECORD",
                    f"Corrupt proposal record '{proposal_id}': expected JSON object.",
                )
        except StudioAPIError:
            raise
        except (OSError, json.JSONDecodeError) as exc:
            raise StudioAPIError(
                "CORRUPT_SESSION_RECORD",
                f"Corrupt proposal record '{proposal_id}': {exc}",
            ) from exc

        prop_data = _normalize_proposal_record(
            raw_prop, session_id=session_id, fallback_proposal_id=proposal_id
        )
        if prop_data.get("status") in ("ACCEPTED", "REJECTED"):
            raise StudioAPIError(
                "PROPOSAL_ALREADY_RESOLVED",
                f"Proposal '{proposal_id}' has already been resolved ({prop_data.get('status')}).",
            )

        meta = session_mgr.get_session(session_id)
        if clean_if_match != meta.head_sha256:
            raise StudioAPIError(
                "ONT_EVO_CONCURRENT_MODIFICATION",
                f"If-Match ('{clean_if_match}') does not match "
                f"session head SHA-256 ('{meta.head_sha256}').",
            )

        raw_prop_base_ver = prop_data.get("base_version")
        prop_base_ver = (
            str(raw_prop_base_ver).strip().lstrip("v")
            if raw_prop_base_ver is not None and str(raw_prop_base_ver).strip()
            else GENESIS_VERSION
        )
        prop_base_sha = str(prop_data.get("base_sha256") or "")
        try:
            base_tup = parse_semver_tuple(prop_base_ver)
        except ValueError as exc:
            raise StudioAPIError(
                "CORRUPT_SESSION_RECORD",
                f"Corrupt proposal base_version '{raw_prop_base_ver}' in proposal "
                f"'{proposal_id}': {exc}",
            ) from exc
        try:
            head_tup = parse_semver_tuple(str(meta.head_version or "").strip().lstrip("v"))
        except ValueError as exc:
            raise StudioAPIError(
                "CORRUPT_SESSION_RECORD",
                f"Corrupt session head_version '{meta.head_version}' in session "
                f"'{session_id}': {exc}",
            ) from exc
        if base_tup > head_tup:
            raise StudioAPIError(
                "PROPOSAL_STALE_REBASE_REQUIRED",
                f"Proposal base_version '{prop_base_ver}' exceeds session head_version "
                f"'{meta.head_version}'.",
            )

        raw_ops = prop_data.get("operations", [])
        ops = [_normalize_delta_operation(op) for op in raw_ops]

        base_ontology = _load_version_ontology_fail_closed(sdir, meta, prop_base_ver)
        head_ontology = _load_version_ontology_fail_closed(sdir, meta, meta.head_version)
        base_ont_sha = compute_ontology_sha256(base_ontology)
        head_ont_sha = compute_ontology_sha256(head_ontology)
        if head_ont_sha.lower() != str(meta.head_sha256 or "").strip().lower():
            raise StudioAPIError(
                "CORRUPT_SESSION_RECORD",
                f"Head ontology SHA-256 ('{head_ont_sha}') does not match session.json "
                f"head_sha256 ('{meta.head_sha256}').",
            )

        intervening_deltas = _collect_intervening_deltas(
            sdir=sdir,
            meta=meta,
            prop_base_version=prop_base_ver,
            prop_base_sha256=prop_base_sha,
            base_ontology_sha256=base_ont_sha,
        )

        eff_prop_parent = prop_base_sha.strip().lower() or base_ont_sha
        try:
            orig_t_ver = _compute_expected_semver_for_ops(
                base_version=prop_base_ver,
                base_sha256=eff_prop_parent,
                norm_ops=ops,
                base_ontology=base_ontology,
                domain_namespace=meta.domain_namespace,
            )
            prop_delta = OntologyDelta(
                delta_id=f"delta-rebase-{uuid.uuid4().hex[:8]}",
                parent_sha256=eff_prop_parent,
                base_version=prop_base_ver,
                target_version=orig_t_ver,
                domain_namespace=meta.domain_namespace,
                operations=ops,
                author=prop_data.get("proposer", "unknown"),
            )
        except OpenDVError as exc:
            raise _raise_from_opendv_error(exc) from exc
        except (ValidationError, ValueError, TypeError) as exc:
            raise StudioAPIError(
                "ONT_EVO_CONTRACT_VIOLATION",
                f"Rebase delta structural integrity check failed: {exc}",
            ) from exc

        rebase_res = check_rebase_conflicts(
            proposal_ops=prop_delta,
            base_ontology=base_ontology,
            head_ontology=head_ontology,
            intervening_deltas=intervening_deltas,
            grandfathered_concept_uris=_load_grandfathered_concept_uris(sdir),
            raise_on_conflict=False,
        )
        if rebase_res.has_conflicts:
            first_reason = (
                rebase_res.conflicts[0].reason
                if rebase_res.conflicts
                else "Proposal conflicts with concurrent modifications in HEAD."
            )
            raise StudioAPIError(
                "PROPOSAL_STALE_REBASE_REQUIRED",
                first_reason,
                extensions={"conflicts": [c.to_dict() for c in rebase_res.conflicts]},
            )

        try:
            dep_prov = build_workspace_deployment_provider(
                getattr(session_mgr, "deployment_workspace_root", None)
                or getattr(session_mgr, "workspace_root", None)
            )
            if dep_prov is not None and rebase_res.rebased_delta is not None:
                GovernanceValidator(
                    deployment_provider=dep_prov,
                    strict_verifier_required=False,
                ).validate_deployed_contracts(head_ontology, rebase_res.rebased_delta)
        except OpenDVError as exc:
            raise _raise_from_opendv_error(exc) from exc

        rebased_ops = (
            [
                op.model_dump(mode="json", exclude_none=True)
                for op in rebase_res.rebased_delta.operations
            ]
            if rebase_res.rebased_delta is not None
            else ops
        )
        prop_data["base_sha256"] = meta.head_sha256
        prop_data["base_version"] = meta.head_version.lstrip("v")
        prop_data["status"] = "PENDING"
        prop_data["operations"] = rebased_ops
        if "base_ontology_sha256" in prop_data:
            prop_data["base_ontology_sha256"] = meta.head_sha256
        if isinstance(prop_data.get("delta"), dict):
            rebased_target = _compute_expected_semver_for_ops(
                base_version=meta.head_version,
                base_sha256=meta.head_sha256,
                norm_ops=rebased_ops,
                base_ontology=head_ontology,
                domain_namespace=meta.domain_namespace,
            )
            cleaned_prop_delta = {
                k: v
                for k, v in prop_data["delta"].items()
                if k not in ("base_sha256", "base_version", "target_version")
            }
            prop_data["delta"] = {
                **cleaned_prop_delta,
                "parent_sha256": meta.head_sha256,
                "base_ontology_version": meta.head_version.lstrip("v"),
                "target_ontology_version": rebased_target,
                "operations": rebased_ops,
            }
        tmp_prop = sdir / "proposals" / f".{proposal_id}.{uuid.uuid4().hex[:8]}.tmp"
        tmp_prop.write_text(json.dumps(prop_data, indent=2), encoding="utf-8")
        replace_with_retry(tmp_prop, prop_file)
        set_owner_only_permissions(prop_file)

        append_signed_audit_record(
            session_dir=sdir,
            action="PROPOSAL_REBASED",
            actor=principal.principal,
            details={
                "proposal_id": proposal_id,
                "previous_base_version": prop_base_ver,
                "new_base_version": meta.head_version,
                "new_base_sha256": meta.head_sha256,
            },
            security_manager=session_mgr.security_manager,
        )

    return ProposalRecordDTO(
        proposal_id=prop_data["proposal_id"],
        session_id=session_id,
        base_version=meta.head_version.lstrip("v"),
        base_sha256=meta.head_sha256,
        status="PENDING",
        operations=prop_data["operations"],
        proposer=prop_data["proposer"],
        created_at=prop_data["created_at"],
        rationale=prop_data["rationale"],
    )


@router.post("/sessions/{session_id}/waiver")
def submit_waiver(
    request: Request,
    session_id: str,
    payload: WaiverSubmissionRequest,
    session_mgr: Annotated[StudioSessionManager, Depends(get_session_manager)],
    principal: Annotated[StudioPrincipal, Depends(require_admin)],
) -> dict[str, Any]:
    """Authorize K_waiver-signed steward waiver and append signed audit record (CRITICAL-05)."""
    _verify_waiver_key_header_if_present(request, session_mgr)
    meta = _get_session_or_404(session_mgr, session_id)
    sdir = session_mgr.get_session_dir(session_id)

    if payload.authorized_role is not None and payload.authorized_role.strip() != "admin":
        raise StudioAPIError(
            "INVALID_GROUNDING_WAIVER",
            "Waiver authorized_role must be 'admin'.",
        )

    rationale = (payload.rationale or "").strip()
    if len(rationale) < 10:
        raise StudioAPIError(
            "INVALID_GROUNDING_WAIVER",
            "Waiver rationale must be at least 10 characters.",
        )

    raw_uris = (
        payload.target_concept_uris
        or payload.concept_uris
        or payload.ungrounded_concepts
        or ([payload.concept_uri] if payload.concept_uri else [])
    )
    target_uris = sorted({str(u).strip() for u in raw_uris if str(u).strip()})
    if not target_uris:
        raise StudioAPIError(
            "INVALID_GROUNDING_WAIVER",
            "Waiver must specify at least one target concept URI.",
        )

    # P10a MAJ-01: Never bind to "0" * 64 wildcard; bind to meta.head_sha256 (or matching proposal)
    loaded_proposal_raw: dict[str, Any] | None = None
    loaded_proposal_norm: dict[str, Any] | None = None
    if payload.proposal_id:
        prop_file = _resolve_proposal_file(sdir, payload.proposal_id)
        if prop_file.is_file():
            try:
                raw_p = json.loads(prop_file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise StudioAPIError(
                    "CORRUPT_SESSION_RECORD",
                    f"Corrupt proposal record '{payload.proposal_id}': {exc}",
                ) from exc
            if not isinstance(raw_p, dict):
                raise StudioAPIError(
                    "CORRUPT_SESSION_RECORD",
                    f"Corrupt proposal record '{payload.proposal_id}': expected JSON object.",
                )
            loaded_proposal_raw = raw_p
            loaded_proposal_norm = _normalize_proposal_record(
                raw_p, session_id=session_id, fallback_proposal_id=payload.proposal_id
            )

    raw_req_base = (payload.base_sha256 or "").strip()
    if not raw_req_base:
        if loaded_proposal_norm and loaded_proposal_norm.get("base_sha256"):
            eff_base_sha = str(loaded_proposal_norm["base_sha256"])
        else:
            eff_base_sha = meta.head_sha256
    else:
        clean_req_base = _parse_strong_etag_or_400(raw_req_base, header_name="base_sha256")
        if clean_req_base == "0" * 64:
            raise StudioAPIError(
                "INVALID_GROUNDING_WAIVER",
                "Wildcard zero base_sha256 is forbidden for steward waivers.",
            )
        allowed_bases = {meta.head_sha256}
        if loaded_proposal_norm and loaded_proposal_norm.get("base_sha256"):
            allowed_bases.add(str(loaded_proposal_norm["base_sha256"]))
        if clean_req_base not in allowed_bases:
            raise StudioAPIError(
                "ONT_EVO_CONCURRENT_MODIFICATION",
                f"Waiver base_sha256 ('{clean_req_base}') does not match "
                f"session head SHA-256 ('{meta.head_sha256}').",
            )
        eff_base_sha = clean_req_base

    # P10a MAJ-01: Bind proposal_delta_sha256 to explicit digest or looked-up proposal delta;
    # leave envelope proposal_delta_sha256 empty "" ONLY for pre-proposal admin waivers.
    raw_req_delta_sha = (payload.proposal_delta_sha256 or "").strip().lower()
    if raw_req_delta_sha:
        if raw_req_delta_sha == "0" * 64 or not re.fullmatch(r"[0-9a-f]{64}", raw_req_delta_sha):
            raise StudioAPIError(
                "INVALID_GROUNDING_WAIVER",
                "Waiver proposal_delta_sha256 must be a valid non-zero 64-character hex digest.",
            )
        envelope_delta_sha = raw_req_delta_sha
    elif payload.proposal_id and loaded_proposal_raw is not None:
        envelope_delta_sha = _compute_stored_proposal_delta_sha256(
            sdir=sdir,
            meta=meta,
            raw_prop=loaded_proposal_raw,
            proposal_id=payload.proposal_id,
        )
    else:
        envelope_delta_sha = ""

    waiver_id_uuid = (
        _proposal_uuid_from_str(payload.waiver_id) if payload.waiver_id else uuid.uuid4()
    )
    proposal_uuid = _proposal_uuid_from_str(payload.proposal_id)
    record_delta_sha = envelope_delta_sha or ("0" * 64)

    record_kwargs: dict[str, Any] = {
        "waiver_id": waiver_id_uuid,
        "proposal_id": proposal_uuid,
        "proposal_delta_sha256": record_delta_sha,
        "target_concept_uris": target_uris,
        "base_sha256": eff_base_sha,
        "rationale": rationale,
        "authorized_by": principal.principal,
        "authorized_role": "admin",
        "signature_or_token": "placeholder",
    }
    if payload.created_at is not None:
        record_kwargs["created_at"] = payload.created_at
    if payload.expires_at is not None:
        record_kwargs["expires_at"] = payload.expires_at

    try:
        draft_record = StewardWaiverRecord(**record_kwargs)
    except (ValidationError, ValueError) as exc:
        raise StudioAPIError(
            "INVALID_GROUNDING_WAIVER",
            f"Invalid steward waiver record: {exc}",
        ) from exc

    sec_mgr = session_mgr.security_manager
    waiver_sig = sec_mgr.sign_waiver(draft_record.signing_payload().encode("utf-8"))
    signed_record = draft_record.model_copy(update={"signature_or_token": waiver_sig})

    envelope: dict[str, Any] = {
        "waiver_id": str(signed_record.waiver_id),
        "proposal_id": payload.proposal_id,
        "proposal_delta_sha256": envelope_delta_sha,
        "target_concept_uris": target_uris,
        "base_sha256": eff_base_sha,
        "rationale": rationale,
        "authorized_by": principal.principal,
        "authorized_role": "admin",
        "created_at": signed_record.created_at.isoformat(),
        "expires_at": (
            signed_record.expires_at.isoformat() if signed_record.expires_at is not None else None
        ),
    }
    envelope_sig = sec_mgr.sign_waiver(dumps_jcs_bytes(envelope))

    with reentrant_session_lock(sdir / ".session.lock", session_id=session_id):
        waivers_file = sdir / "waivers.json"
        existing_waivers: list[dict[str, Any]] = []
        if waivers_file.is_file():
            try:
                loaded_w = json.loads(waivers_file.read_text(encoding="utf-8"))
                if isinstance(loaded_w, list):
                    existing_waivers = loaded_w
            except (OSError, json.JSONDecodeError) as exc:
                raise StudioAPIError(
                    "CORRUPT_SESSION_RECORD",
                    f"Corrupt waivers.json in session '{session_id}': {exc}",
                ) from exc
        existing_waivers.append(
            {
                "envelope": envelope,
                "hmac_signature": envelope_sig,
                "record": signed_record.model_dump(mode="json"),
            }
        )
        tmp_w = sdir / f".waivers.{uuid.uuid4().hex[:8]}.tmp"
        tmp_w.write_text(json.dumps(existing_waivers, indent=2), encoding="utf-8")
        replace_with_retry(tmp_w, waivers_file)
        set_owner_only_permissions(waivers_file)

        append_signed_audit_record(
            session_dir=sdir,
            action="STEWARD_WAIVER_AUTHORIZED",
            actor=principal.principal,
            details={
                "waiver_id": str(signed_record.waiver_id),
                "target_concept_uris": target_uris,
                "base_sha256": eff_base_sha,
                "rationale": rationale,
                "expires_at": envelope["expires_at"],
            },
            security_manager=sec_mgr,
        )

    return {
        "status": "authorized",
        "waiver_id": str(signed_record.waiver_id),
        "target_concept_uris": target_uris,
        "base_sha256": eff_base_sha,
    }


@router.post(
    "/sessions/{session_id}/feedback",
    response_model=FeedbackSubmissionResponse,
    status_code=201,
)
def submit_feedback(
    session_id: str,
    payload: FeedbackSubmissionRequest,
    session_mgr: Annotated[StudioSessionManager, Depends(get_session_manager)],
    principal: Annotated[StudioPrincipal, Depends(require_editor)],
) -> FeedbackSubmissionResponse:
    """Persist steward/user feedback record to feedback.jsonl under L5 session lock."""
    if principal.is_automated:
        raise StudioAPIError(
            "AGENT_OPERATION_FORBIDDEN",
            "Automated principals are forbidden from submitting human review feedback.",
        )

    meta = _get_session_or_404(session_mgr, session_id)
    sdir = session_mgr.get_session_dir(session_id)
    feedback_id = f"fbk-{uuid.uuid4().hex[:12]}"
    now_iso = datetime.now(UTC).isoformat()

    record = {
        "feedback_id": feedback_id,
        "timestamp": now_iso,
        "actor": principal.principal,
        "role": principal.role,
        "rating": payload.rating,
        "comment": payload.comment,
        "concept_uri": payload.concept_uri,
        "proposal_id": payload.proposal_id,
        "active_version": meta.head_version,
        "head_sha256": meta.head_sha256,
    }

    with reentrant_session_lock(sdir / ".session.lock", session_id=session_id):
        fb_file = sdir / "feedback.jsonl"
        line_bytes = (json.dumps(record, sort_keys=True) + "\n").encode("utf-8")
        flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND | getattr(os, "O_BINARY", 0)
        fd = os.open(str(fb_file), flags, 0o600)
        try:
            os.write(fd, line_bytes)
            os.fsync(fd)
        finally:
            os.close(fd)
        set_owner_only_permissions(fb_file)

        append_signed_audit_record(
            session_dir=sdir,
            action="FEEDBACK_SUBMITTED",
            actor=principal.principal,
            details={"feedback_id": feedback_id, "rating": payload.rating},
            security_manager=session_mgr.security_manager,
        )

    return FeedbackSubmissionResponse(
        status="recorded",
        feedback_id=feedback_id,
        session_id=session_id,
        active_version=meta.head_version,
        head_sha256=meta.head_sha256,
        timestamp=now_iso,
    )


@router.get("/sessions/{session_id}/export")
def export_ontology_endpoint(
    session_id: str,
    session_mgr: Annotated[StudioSessionManager, Depends(get_session_manager)],
    _principal: Annotated[StudioPrincipal, Depends(require_viewer)],
    export_format: str = Query(default="yaml"),
    version: str | None = Query(default=None),
) -> Response:
    """Multi-format export of active or historical ontology snapshot."""
    meta = _get_session_or_404(session_mgr, session_id)
    sdir = session_mgr.get_session_dir(session_id)

    if version:
        _validate_version_param(version)
    target_version = version or meta.head_version
    clean_ver = target_version.lstrip("v")
    _validate_version_param(clean_ver)

    versions_dir = (sdir / "versions").resolve()
    vdir = (versions_dir / f"v{clean_ver}").resolve()
    if not vdir.is_relative_to(versions_dir):
        raise StudioAPIError(
            "INVALID_WORKSPACE_PATH",
            f"Path traversal detected in version identifier '{target_version}'.",
        )
    if not vdir.is_dir():
        raise StudioAPIError(
            "VERSION_NOT_FOUND",
            f"Version '{target_version}' not found in session.",
        )

    ont_file = vdir / "ontology.json"
    if not ont_file.is_file():
        ont_file = vdir / "ontology.yaml"
    if not ont_file.is_file():
        raise StudioAPIError(
            "VERSION_NOT_FOUND",
            f"Ontology artifact missing for v{clean_ver}.",
        )

    try:
        if ont_file.suffix == ".json":
            raw_json = ont_file.read_text(encoding="utf-8")
            ont_obj = GraphAOntology.model_validate_json(raw_json)
        else:
            ont_obj = GraphAOntology.model_validate(
                yaml.safe_load(ont_file.read_text(encoding="utf-8"))
            )
    except (
        OSError,
        UnicodeDecodeError,
        json.JSONDecodeError,
        yaml.YAMLError,
        ValidationError,
        TypeError,
        ValueError,
    ) as exc:
        raise StudioAPIError(
            "CORRUPT_SESSION_RECORD",
            f"Corrupt ontology artifact for version 'v{clean_ver}': {exc}",
        ) from exc

    clean_format = export_format.strip()
    try:
        content, media_type = export_ontology(ont_obj, export_format=clean_format)
    except OpenDVError as exc:
        raise _raise_from_opendv_error(exc) from exc
    except ValueError as exc:
        raise StudioAPIError("UNSUPPORTED_FORMAT", str(exc)) from exc

    safe_ext = re.sub(r"[^a-z0-9_-]", "", clean_format.lower()) or "txt"
    return Response(
        content=content,
        media_type=media_type,
        headers={"Content-Disposition": f'attachment; filename="ontology-v{clean_ver}.{safe_ext}"'},
    )


def _resolve_target_promotion_contract_path(
    target_ws: Path,
    domain_namespace: str,
) -> Path:
    """Resolve target Graph A contract file inside a validated workspace path."""
    if target_ws.suffix.lower() in (".yaml", ".yml", ".json"):
        return target_ws

    default_name = f"{domain_namespace}_graph_a_ontology.yaml"
    for cand_name in (
        default_name,
        "graph_a_ontology.yaml",
        "graph_a.yaml",
        "ontology.yaml",
        "ontology.json",
    ):
        cand = target_ws / cand_name
        if cand.is_file():
            return cand
    return target_ws / default_name


@router.post("/sessions/{session_id}/promote", response_model=PromoteResponse)
def promote_session(
    request: Request,
    session_id: str,
    payload: PromoteRequest,
    session_mgr: Annotated[StudioSessionManager, Depends(get_session_manager)],
    principal: Annotated[StudioPrincipal, Depends(require_admin)],
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
    x_workspace_if_match: Annotated[str | None, Header(alias="X-OpenDV-Workspace-If-Match")] = None,
) -> PromoteResponse:
    """Two-phase atomic promotion of head ontology into workspace under L4 + L5 locks."""
    if not if_match:
        raise StudioAPIError(
            "MISSING_PRECONDITION_HEADER",
            "Promoting a session requires an 'If-Match' header anchoring session head SHA-256.",
        )
    clean_if_match = _parse_strong_etag_or_400(if_match, header_name="If-Match")

    if payload.head_sha256 is not None:
        clean_body_head = _parse_strong_etag_or_400(payload.head_sha256, header_name="head_sha256")
        if clean_if_match != clean_body_head:
            raise StudioAPIError(
                "PRECONDITION_BASE_SHA_MISMATCH",
                f"If-Match header ('{clean_if_match}') does not match "
                f"body head_sha256 ('{clean_body_head}').",
            )

    clean_ws_header = (
        _parse_strong_etag_or_400(x_workspace_if_match, header_name="X-OpenDV-Workspace-If-Match")
        if x_workspace_if_match is not None
        else None
    )
    clean_ws_body = (
        _parse_strong_etag_or_400(payload.workspace_sha256, header_name="workspace_sha256")
        if payload.workspace_sha256 is not None
        else None
    )
    if clean_ws_header is None and clean_ws_body is None:
        raise StudioAPIError(
            "MISSING_PRECONDITION_HEADER",
            "Promoting a session requires 'X-OpenDV-Workspace-If-Match' "
            "or 'workspace_sha256' precondition.",
        )
    if (
        clean_ws_header is not None
        and clean_ws_body is not None
        and clean_ws_header != clean_ws_body
    ):
        raise StudioAPIError(
            "PRECONDITION_BASE_SHA_MISMATCH",
            f"X-OpenDV-Workspace-If-Match ('{clean_ws_header}') does not match "
            f"body workspace_sha256 ('{clean_ws_body}').",
        )
    expected_ws_sha = clean_ws_header or clean_ws_body or ("0" * 64)

    if not payload.rationale or len(payload.rationale.strip()) < 10:
        raise StudioAPIError(
            "PROMOTION_RATIONALE_REQUIRED",
            "Promotion rationale must be at least 10 characters.",
        )

    is_remote = get_server_mode(request) == SERVER_MODE_REMOTE
    if payload.workspace and is_remote:
        raise StudioAPIError(
            "REMOTE_SERVER_PATH_FORBIDDEN",
            "Server-local 'workspace' paths are forbidden in remote_platform mode.",
        )

    meta = _get_session_or_404(session_mgr, session_id)
    allowed_roots = _resolve_allowed_workspace_roots(session_mgr)
    ws_base = allowed_roots[0]

    if payload.workspace:
        raw_ws = Path(payload.workspace)
        candidate_ws = raw_ws if raw_ws.is_absolute() else (ws_base / raw_ws)
    else:
        candidate_ws = session_mgr.workspace_root or ws_base

    try:
        validated_ws = validate_workspace_containment(
            candidate_ws,
            allowed_roots=allowed_roots,
            is_remote_request=False,
        )
    except OpenDVError as exc:
        raise _raise_from_opendv_error(exc) from exc

    target_contract_path = _resolve_target_promotion_contract_path(
        validated_ws, meta.domain_namespace
    )

    atomic_svc = AtomicCommitService(session_manager=session_mgr)
    try:
        promo_res = atomic_svc.promote_to_workspace(
            session_id=session_id,
            target_workspace=target_contract_path,
            expected_workspace_sha256=expected_ws_sha,
            expected_head_sha256=clean_if_match,
            if_match=clean_if_match,
            rationale=payload.rationale,
            actor=principal.principal,
            actor_role=principal.role,
            acknowledge_historical_contract_warning=payload.acknowledge_historical_contract_warning,
            historical_contract_rationale=payload.historical_contract_rationale,
            allowed_workspace_roots=allowed_roots,
            is_remote_request=False,
            enforce_lineage=(not payload.acknowledge_historical_contract_warning),
            allow_noop=False,
        )
    except ConcurrentModificationError as exc:
        raise StudioAPIError("ONT_EVO_CONCURRENT_MODIFICATION", str(exc)) from exc
    except OpenDVError as exc:
        raise _raise_from_opendv_error(exc) from exc

    try:
        rel_path = str(
            promo_res.target_contract_path.relative_to(session_mgr.workspace_root.resolve())
        )
    except ValueError:
        rel_path = promo_res.target_contract_path.name

    return PromoteResponse(
        session_id=session_id,
        promoted_version=promo_res.promoted_version.lstrip("v"),
        head_sha256=promo_res.head_sha256,
        workspace_sha256=promo_res.workspace_sha256,
        promoted_at=promo_res.promoted_at,
        target_contract_path=rel_path,
    )


# ---------------------------------------------------------------------------
# Portability & Bundle Routes
# ---------------------------------------------------------------------------


@router.post("/sessions/{session_id}/bundle")
def export_bundle_endpoint(
    session_id: str,
    payload: BundleExportRequest,
    session_mgr: Annotated[StudioSessionManager, Depends(get_session_manager)],
    principal: Annotated[StudioPrincipal, Depends(require_editor)],
) -> Response:
    """Export full session into portable archive .odvstudio.zip enforcing taint gates."""
    _get_session_or_404(session_mgr, session_id)
    sdir = session_mgr.get_session_dir(session_id)

    is_tainted = compute_effective_session_taint(sdir)
    if is_tainted and not principal.is_admin:
        raise StudioAPIError(
            "FORBIDDEN_ADMIN_REQUIRED",
            "Exporting sessions with confidential/tainted sources requires administrator role.",
        )
    if is_tainted and not payload.acknowledge_confidential_source_export:
        raise StudioAPIError(
            "CONFIDENTIAL_SOURCE_EXPORT_UNACKNOWLEDGED",
            "Session contains confidential sources. "
            "Pass acknowledge_confidential_source_export=true.",
        )
    if is_tainted and (not payload.export_rationale or len(payload.export_rationale.strip()) < 10):
        raise StudioAPIError(
            "EXPORT_RATIONALE_REQUIRED",
            "Exporting a tainted session requires export_rationale of at least 10 characters.",
        )

    tmp_dir = session_mgr.studio_root / ".scratch"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp_out = tmp_dir / f".bundle_export_{uuid.uuid4().hex[:8]}.zip"
    try:
        export_bundle(
            sdir,
            tmp_out,
            admin=principal.is_admin,
            acknowledge_confidential=payload.acknowledge_confidential_source_export,
            export_rationale=payload.export_rationale,
            security_manager=session_mgr.security_manager,
            actor=principal.principal,
        )
        zip_bytes = tmp_out.read_bytes()
    except OpenDVError as exc:
        raise _raise_from_opendv_error(exc) from exc
    finally:
        if tmp_out.exists():
            with contextlib.suppress(OSError):
                tmp_out.unlink()

    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{session_id}.odvstudio.zip"'},
    )


def _import_bundle_sync(
    *,
    tmp_in: Path,
    session_mgr: StudioSessionManager,
    principal: StudioPrincipal,
    new_session_id: str | None,
    acknowledge_unverified_import_attribution: bool,
    acknowledgement_rationale: str | None,
    force: bool,
) -> StudioSessionDTO:
    """Run synchronous bundle verification and atomic import in worker threadpool."""
    try:
        meta = import_bundle(
            tmp_in,
            session_mgr.studio_root,
            security_manager=session_mgr.security_manager,
            new_session_id=new_session_id,
            acknowledge_unverified_import_attribution=acknowledge_unverified_import_attribution,
            acknowledgement_rationale=acknowledgement_rationale,
            imported_by=principal.principal,
            actor_role=principal.role,
            force=force,
        )
    except ImportGroundingRationaleRequiredError as exc:
        raise StudioAPIError("IMPORT_GROUNDING_RATIONALE_REQUIRED", str(exc)) from exc
    except ImportGroundingViolationError as exc:
        raise StudioAPIError("IMPORT_GROUNDING_VIOLATION", str(exc)) from exc
    except (SessionAlreadyExistsError, FileExistsError) as exc:
        raise StudioAPIError("SESSION_ALREADY_EXISTS", str(exc)) from exc
    except InvalidSessionIdentifierError as exc:
        raise StudioAPIError("INVALID_SESSION_IDENTIFIER", str(exc)) from exc
    except ArchiveSecurityViolationError as exc:
        raise _raise_from_opendv_error(exc) from exc
    except OpenDVError as exc:
        raise _raise_from_opendv_error(exc) from exc
    except Exception as exc:
        raise StudioAPIError("EXECUTION_FAILURE", str(exc)) from exc

    sdir = session_mgr.get_session_dir(meta.session_id)
    return StudioSessionDTO(
        session_id=meta.session_id,
        domain_namespace=meta.domain_namespace,
        enterprise_owner=meta.enterprise_owner,
        status=_derive_session_status(sdir, meta),
        active_version=meta.head_version,
        head_sha256=meta.head_sha256,
        created_at=meta.created_at,
        updated_at=meta.updated_at,
    )


@router.post("/sessions/bundle/import", response_model=StudioSessionDTO, status_code=201)
async def import_bundle_endpoint(
    file: UploadFile,
    session_mgr: Annotated[StudioSessionManager, Depends(get_session_manager)],
    principal: Annotated[StudioPrincipal, Depends(require_editor)],
    request: Request,
    acknowledge_unverified_import_attribution: bool = Query(default=False),
    acknowledgement_rationale: str | None = Query(default=None),
    new_session_id: str | None = Query(default=None),
    force: bool = Query(default=False),
    form_acknowledge: Annotated[
        bool | None, Form(alias="acknowledge_unverified_import_attribution")
    ] = None,
    form_rationale: Annotated[str | None, Form(alias="acknowledgement_rationale")] = None,
    form_new_session_id: Annotated[str | None, Form(alias="new_session_id")] = None,
    form_force: Annotated[bool | None, Form(alias="force")] = None,
) -> StudioSessionDTO:
    """Streaming import of .odvstudio.zip archive (editor allowed; admin required for ack)."""
    del request
    eff_ack = bool(acknowledge_unverified_import_attribution or form_acknowledge)
    eff_rationale = (
        acknowledgement_rationale if acknowledgement_rationale is not None else form_rationale
    )
    eff_new_sid = new_session_id or form_new_session_id
    eff_force = bool(force or form_force)

    # CRITICAL-01: Require admin only when acknowledging unverified attribution or force overwrite
    if (eff_ack or eff_force) and not principal.is_admin:
        raise StudioAPIError(
            "FORBIDDEN_ADMIN_REQUIRED",
            "Importing bundles with attribution acknowledgement or force overwrite "
            "requires administrator role.",
        )

    scratch_dir = session_mgr.studio_root / ".scratch"
    tmp_in, size_bytes = await _stream_upload_to_tempfile(
        file,
        scratch_dir,
        max_bytes=MAX_BUNDLE_IMPORT_BYTES,
        prefix="bundle_import",
        suffix=".zip",
    )
    try:
        if size_bytes == 0:
            raise StudioAPIError("ARCHIVE_SECURITY_VIOLATION", "Uploaded bundle archive is empty.")
        return await run_in_threadpool(
            _import_bundle_sync,
            tmp_in=tmp_in,
            session_mgr=session_mgr,
            principal=principal,
            new_session_id=eff_new_sid,
            acknowledge_unverified_import_attribution=eff_ack,
            acknowledgement_rationale=eff_rationale,
            force=eff_force,
        )
    finally:
        if tmp_in.exists():
            with contextlib.suppress(OSError):
                tmp_in.unlink()


@router.post(
    "/sessions/{session_id}/bundle/clone", response_model=StudioSessionDTO, status_code=201
)
def clone_session_endpoint(
    session_id: str,
    session_mgr: Annotated[StudioSessionManager, Depends(get_session_manager)],
    principal: Annotated[StudioPrincipal, Depends(require_editor)],
    payload: CloneSessionRequest | None = None,
) -> StudioSessionDTO:
    """Clone session into an isolated session with re-anchored local audit chain under L5 lock."""
    _get_session_or_404(session_mgr, session_id)
    requested_new_sid = (
        payload.new_session_id
        if payload and payload.new_session_id
        else f"clone-{session_id[:51].rstrip('-_')}-{uuid.uuid4().hex[:6]}"
    )
    role_tier = (
        "admin" if principal.is_admin else ("automated" if principal.is_automated else "editor")
    )
    try:
        meta = session_mgr.clone_session(
            source_session_id=session_id,
            new_session_id=requested_new_sid,
            actor=principal.principal,
            actor_role=role_tier,
        )
    except InvalidSessionIdentifierError as exc:
        raise StudioAPIError(
            "SYNTAX_INVALID_ARGUMENT",
            f"Invalid destination session identifier '{requested_new_sid}': {exc}",
        ) from exc
    except SessionAlreadyExistsError as exc:
        raise StudioAPIError(
            "SESSION_ALREADY_EXISTS",
            f"Session '{requested_new_sid}' already exists.",
        ) from exc
    except TamperEvidentChainCorruptError as exc:
        raise StudioAPIError(
            "TAMPER_EVIDENT_CHAIN_CORRUPT",
            str(exc),
        ) from exc
    except SessionNotFoundError as exc:
        raise StudioAPIError(
            "TAMPER_EVIDENT_CHAIN_CORRUPT",
            f"Audit ledger or session metadata missing for session '{session_id}': {exc}",
        ) from exc
    except OpenDVError as exc:
        raise StudioAPIError(exc.error_code.value, str(exc)) from exc
    except Exception as exc:
        if isinstance(exc, StudioAPIError):
            raise
        raise StudioAPIError(
            "INTERNAL_CLONE_ERROR",
            f"Failed to clone session '{session_id}': {exc}",
        ) from exc

    new_dir = session_mgr.get_session_dir(meta.session_id)
    return StudioSessionDTO(
        session_id=meta.session_id,
        domain_namespace=meta.domain_namespace,
        enterprise_owner=meta.enterprise_owner,
        status=_derive_session_status(new_dir, meta),
        active_version=meta.head_version,
        head_sha256=meta.head_sha256,
        created_at=meta.created_at,
        updated_at=meta.updated_at,
    )


# ---------------------------------------------------------------------------
# Task 5B.4 (Issue #218): Auth & Launch Nonce Endpoints
# ---------------------------------------------------------------------------


@router.post("/auth/launch-nonce", response_model=LaunchNonceResponse)
def mint_launch_nonce_endpoint(
    request: Request,
    principal: Annotated[StudioPrincipal, Depends(require_viewer)],
) -> LaunchNonceResponse:
    """Mint single-use browser launch nonce (256-bit, 60s TTL) preserving caller RBAC role."""
    server_mode = get_server_mode(request)
    if server_mode == SERVER_MODE_REMOTE:
        raise StudioAPIError(
            "LOOPBACK_PRIVILEGE_DISABLED_IN_REMOTE_MODE",
            "Launch nonce bootstrap is forbidden in remote_platform mode.",
        )

    if not is_loopback_client(request):
        raise StudioAPIError(
            "LOOPBACK_PRIVILEGE_DISABLED_IN_REMOTE_MODE",
            "Minting launch nonce is strictly restricted to loopback callers.",
            instance=str(request.url.path),
        )

    if principal.is_automated:
        raise StudioAPIError(
            "AGENT_OPERATION_FORBIDDEN",
            "Automated principals are strictly forbidden from minting browser launch nonces.",
            instance=str(request.url.path),
        )

    # CRITICAL-02: Preserve caller's exact role (stopping viewer/editor -> admin escalation!)
    nonce = mint_launch_nonce(principal=principal.principal, role=principal.role, ttl=60.0)
    return LaunchNonceResponse(launch_nonce=nonce, nonce=nonce, expires_in=60)


@router.post("/auth/token", response_model=TokenExchangeResponse)
def exchange_launch_nonce_endpoint(
    request: Request,
    response: Response,
    payload: TokenExchangeRequest,
) -> TokenExchangeResponse:
    """Exchange single-use browser launch nonce for HttpOnly session cookie."""
    server_mode = get_server_mode(request)
    if server_mode == SERVER_MODE_REMOTE:
        raise StudioAPIError(
            "LOOPBACK_PRIVILEGE_DISABLED_IN_REMOTE_MODE",
            "Nonce exchange is forbidden in remote_platform mode.",
        )

    if not is_loopback_client(request):
        raise StudioAPIError(
            "LOOPBACK_PRIVILEGE_DISABLED_IN_REMOTE_MODE",
            "Nonce exchange is strictly restricted to loopback callers.",
            instance=str(request.url.path),
        )

    # CRITICAL-03: Enforce CSRF / Sec-Fetch-Site / Origin checks on token exchange!
    verify_csrf(request)

    raw_nonce = payload.launch_nonce or payload.nonce
    if not raw_nonce:
        raise StudioAPIError("SYNTAX_INVALID_ARGUMENT", "Missing required 'nonce' field.")

    session_token, principal_str = exchange_launch_nonce(str(raw_nonce))
    session_role = get_active_session_role(session_token)
    effective_role = session_role or "admin"
    request.state.studio_principal = StudioPrincipal(
        principal=principal_str,
        role=effective_role,
        is_admin=(effective_role == "admin"),
        auth_method="cookie",
    )

    response.set_cookie(
        key="opendv_session",
        value=session_token,
        httponly=True,
        samesite="strict",
        secure=(request.url.scheme == "https"),
    )
    return TokenExchangeResponse(
        status="authenticated",
        principal=principal_str,
        role=session_role,
    )


# ---------------------------------------------------------------------------
# Parameter Validation Helpers
# ---------------------------------------------------------------------------


_PROPOSAL_ID_PATTERN: re.Pattern[str] = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
_JOB_ID_PATTERN: re.Pattern[str] = re.compile(r"^job-[a-zA-Z0-9_-]{4,64}$")
_VERSION_PATTERN: re.Pattern[str] = re.compile(
    r"^(?:v)?[0-9]+\.[0-9]+\.[0-9]+(?:-[a-zA-Z0-9_.-]+)?$"
)


def _validate_session_param(session_id: str) -> None:
    try:
        validate_session_id(session_id)
    except InvalidSessionIdentifierError as exc:
        raise StudioAPIError("INVALID_SESSION_IDENTIFIER", str(exc)) from exc


def _validate_proposal_id_param(proposal_id: str) -> None:
    if (
        not isinstance(proposal_id, str)
        or not _PROPOSAL_ID_PATTERN.match(proposal_id)
        or proposal_id.lower() in WINDOWS_RESERVED_DEVICES
    ):
        raise StudioAPIError(
            "SYNTAX_INVALID_ARGUMENT",
            f"Invalid proposal identifier '{proposal_id}'.",
        )


def _validate_job_id_param(job_id: str) -> None:
    if (
        not isinstance(job_id, str)
        or not _JOB_ID_PATTERN.match(job_id)
        or job_id.lower() in WINDOWS_RESERVED_DEVICES
    ):
        raise StudioAPIError(
            "SYNTAX_INVALID_ARGUMENT",
            f"Invalid job identifier '{job_id}'.",
        )


def _validate_version_param(version: str | None) -> None:
    if version is not None and (
        not isinstance(version, str) or not _VERSION_PATTERN.match(version)
    ):
        raise StudioAPIError(
            "SYNTAX_INVALID_ARGUMENT",
            f"Invalid version identifier '{version}'.",
        )


def _resolve_proposal_file(sdir: Path, proposal_id: str) -> Path:
    _validate_proposal_id_param(proposal_id)
    proposals_dir = (sdir / "proposals").resolve()
    target_file = (proposals_dir / f"{proposal_id}.json").resolve()
    if not target_file.is_relative_to(proposals_dir):
        raise StudioAPIError(
            "INVALID_WORKSPACE_PATH",
            f"Path traversal detected in proposal identifier '{proposal_id}'.",
        )
    return target_file


__all__ = [
    "router",
]
