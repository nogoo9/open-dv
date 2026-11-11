"""Ontology Construction Studio Platform REST & SSE API Router (Track 5B / Issues #215-#218).

Implements all 27 canonical endpoints conforming to:
- docs/20-ontology-studio-spec.md (§3 REST API, §4 SSE, §7 Export/Promotion)
- docs/19-ontology-studio-architecture.md (§6 CAS, §7 ONT-EVO, §8 Security)
- docs/21-ontology-studio-implementation-plan.md (§Track 5B Tasks 5B.1-5B.4)
- schemas/v1/openapi.json & schemas/v1/error-codes.json
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import re
import shutil
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import urlparse

import yaml
from fastapi import APIRouter, Depends, Form, Header, Query, Request, Response, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, ValidationError

from opendv_contracts.dtos.clarification import (
    ClarificationAnswerItem,
    ClarificationAnswerResponse,
    ClarificationQuestion,
    ClarifyRequest,
)
from opendv_contracts.dtos.ontology import (
    OntologyDelta,
    OntologyValidationResult,
    StewardWaiverRecord,
)
from opendv_contracts.jcs import dumps_jcs_bytes
from opendv_core.models.graph_a import GraphAOntology
from opendv_platform_api.deps import get_opendv_service
from opendv_platform_api.job_supervisor import (
    get_job_supervisor,
    run_clarify_job,
    run_construct_job,
    run_parse_job,
)
from opendv_platform_api.security.csrf import is_loopback_host, verify_csrf
from opendv_platform_api.security.error_catalog import (
    StudioAPIError,
)
from opendv_platform_api.security.studio_auth import (
    SERVER_MODE_REMOTE,
    StudioPrincipal,
    authenticate_studio_request,
    exchange_launch_nonce,
    get_server_mode,
    initialize_local_token,
    is_loopback_client,
    mint_launch_nonce,
)
from opendv_service.construction.clarification_harness import EMPTY_ANSWERS_SHA256
from opendv_service.export.rdf_exporter import export_ontology
from opendv_service.ontology.applier import compute_next_semver
from opendv_service.studio.atomic_commit import (
    AtomicCommitService,
    ConcurrentModificationError,
    delete_source,
    parse_semver_tuple,
)
from opendv_service.studio.bundle import (
    ArchiveSecurityViolationError,
    ImportGroundingRationaleRequiredError,
    ImportGroundingViolationError,
    export_bundle,
    import_bundle,
)
from opendv_service.studio.locks import (
    L4_WORKSPACE,
    L5_SESSION,
    studio_lock,
)
from opendv_service.studio.session_manager import (
    InvalidSessionIdentifierError,
    SessionAlreadyExistsError,
    SessionNotFoundError,
    StudioSessionManager,
    validate_session_id,
)
from opendv_service.studio.workspace_security import set_owner_only_permissions

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/ontology/studio", tags=["ontology-studio"])


# ---------------------------------------------------------------------------
# Request & Response Wire Models (Strictly matching schemas/v1/openapi.json)
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


class ConstructRequest(BaseModel):
    base_version: str | None = None
    base_sha256: str | None = None
    auto_commit: bool = False
    fibo_modules: list[str] = Field(
        default_factory=lambda: ["FND", "BE", "FBC", "SEC", "LOAN", "IND"]
    )
    mode: str = "auto"


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
    answers: list[ClarificationAnswerItem] | dict[str, Any] = Field(default_factory=list)
    expected_answers_sha256: str | None = None


class OntologyDeltaRequest(BaseModel):
    delta_id: str
    parent_sha256: str
    base_version: str
    target_version: str
    operations: list[dict[str, Any]]
    author: str | None = None
    rationale: str | None = None


class DeltaCommitResponse(BaseModel):
    session_id: str
    target_version: str
    target_sha256: str
    parent_sha256: str
    delta_id: str
    bump_type: str
    applied_operations_count: int


class ProposalCreateRequest(BaseModel):
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
    ungrounded_concept: list[str] | None = None


class ProposalRebaseRequest(BaseModel):
    base_sha256: str


class RejectProposalRequest(BaseModel):
    reason: str | None = None


class CloneSessionRequest(BaseModel):
    new_session_id: str | None = None


class PromoteRequest(BaseModel):
    workspace_sha256: str
    head_sha256: str
    rationale: str
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


# ---------------------------------------------------------------------------
# Dependency Injectors
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


def get_session_manager(
    request: Request,
    studio_root: Annotated[Path, Depends(get_studio_root)],
) -> StudioSessionManager:
    """Create StudioSessionManager configured with active workspace roots."""
    service = get_opendv_service(request)
    mgr = StudioSessionManager(studio_root=studio_root)
    mgr.workspace_root = service.workspace_root
    return mgr


def require_principal(
    request: Request,
    studio_root: Annotated[Path, Depends(get_studio_root)],
) -> StudioPrincipal:
    """Authenticate caller and verify CSRF protection for state mutations."""
    verify_csrf(request)
    return authenticate_studio_request(request, studio_root)


def require_admin(
    request: Request,
    studio_root: Annotated[Path, Depends(get_studio_root)],
) -> StudioPrincipal:
    """Authenticate caller asserting administrator privileges."""
    verify_csrf(request)
    return authenticate_studio_request(request, studio_root, required_role="admin")


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
            status_code=403,
            instance=str(request.url.path),
        )

    if not is_loopback_client(request):
        raise StudioAPIError(
            "LOOPBACK_PRIVILEGE_DISABLED_IN_REMOTE_MODE",
            "Bootstrap endpoint /init is strictly restricted to loopback callers.",
            status_code=403,
            instance=str(request.url.path),
        )

    # DNS rebinding protection: Host header must be loopback
    host_raw = request.headers.get("host") or request.headers.get("Host") or ""
    host = urlparse(f"//{host_raw}").hostname or host_raw
    if host and not is_loopback_host(host):
        raise StudioAPIError(
            "LOOPBACK_PRIVILEGE_DISABLED_IN_REMOTE_MODE",
            f"DNS rebinding protection: untrusted host header '{host_raw}' for loopback init.",
            status_code=403,
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

    return InitStudioResponse(
        status="initialized",
        token_path=str(token_path),
        principal="human:local_admin",
        local_token=new_token,
        domain_namespace=payload.domain_namespace if payload else None,
    )


@router.get("/sessions", response_model=list[StudioSessionSummaryDTO])
def list_sessions(
    session_mgr: Annotated[StudioSessionManager, Depends(get_session_manager)],
    _principal: Annotated[StudioPrincipal, Depends(require_principal)],
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    status: str | None = Query(default=None),
) -> list[StudioSessionSummaryDTO]:
    """List studio sessions with pagination and status filtering."""
    sessions = session_mgr.list_sessions()
    if status:
        sessions = [s for s in sessions if s.status.lower() == status.lower()]

    paginated = sessions[offset : offset + limit]
    return [
        StudioSessionSummaryDTO(
            session_id=s.session_id,
            domain_namespace=s.domain_namespace,
            enterprise_owner=s.enterprise_owner,
            status=s.status,
            active_version=s.head_version,
            created_at=s.created_at,
            updated_at=s.updated_at,
        )
        for s in paginated
    ]


_DOMAIN_NAMESPACE_PATTERN: re.Pattern[str] = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


@router.post("/sessions", response_model=StudioSessionDTO, status_code=201)
def create_session(
    payload: CreateSessionRequest,
    session_mgr: Annotated[StudioSessionManager, Depends(get_session_manager)],
    _principal: Annotated[StudioPrincipal, Depends(require_principal)],
) -> StudioSessionDTO:
    """Create a new studio session (genesis 0.0.0 or seeded from workspace)."""
    if not _DOMAIN_NAMESPACE_PATTERN.match(payload.domain_namespace):
        raise StudioAPIError(
            "SYNTAX_INVALID_ARGUMENT",
            f"Invalid domain_namespace '{payload.domain_namespace}'. "
            "Must match ^[a-z][a-z0-9_]{0,63}$",
            status_code=400,
        )

    # Workspace path validation if from_workspace provided
    from_ws_path: Path | None = None
    if payload.from_workspace:
        raw_p = Path(payload.from_workspace)
        if not raw_p.is_absolute():
            ws_root = session_mgr.workspace_root
            raw_p = (ws_root / raw_p).resolve()
        else:
            raw_p = raw_p.resolve()

        try:
            ws_root_norm = os.path.normcase(session_mgr.workspace_root)
            raw_norm = os.path.normcase(raw_p)
            if os.path.commonpath([ws_root_norm, raw_norm]) != ws_root_norm:
                raise StudioAPIError(
                    "INVALID_WORKSPACE_PATH",
                    f"Target workspace path '{payload.from_workspace}' escapes workspace boundary.",
                    status_code=400,
                )
        except ValueError as exc:
            raise StudioAPIError(
                "INVALID_WORKSPACE_PATH",
                f"Invalid workspace path '{payload.from_workspace}'.",
                status_code=400,
            ) from exc

        if not raw_p.exists():
            raise StudioAPIError(
                "INVALID_WORKSPACE_PATH",
                f"Workspace path does not exist: {payload.from_workspace}",
                status_code=400,
            )
        from_ws_path = raw_p

    initial_ontology = None
    if from_ws_path:
        if from_ws_path.is_file():
            if from_ws_path.suffix == ".json":
                initial_ontology = GraphAOntology.model_validate_json(
                    from_ws_path.read_text(encoding="utf-8")
                )
            else:
                initial_ontology = GraphAOntology.model_validate(
                    yaml.safe_load(from_ws_path.read_text(encoding="utf-8"))
                )
        elif from_ws_path.is_dir():
            cand = from_ws_path / "ontology.yaml"
            if not cand.is_file():
                cand = from_ws_path / "ontology.json"
            if cand.is_file():
                initial_ontology = GraphAOntology.model_validate(
                    yaml.safe_load(cand.read_text(encoding="utf-8"))
                )

    try:
        meta = session_mgr.create_session(
            session_id=payload.session_id,
            initial_ontology=initial_ontology,
            domain_namespace=payload.domain_namespace,
            enterprise_owner=payload.enterprise_owner,
        )
    except SessionAlreadyExistsError as exc:
        raise StudioAPIError("SESSION_ALREADY_EXISTS", str(exc), status_code=409) from exc
    except InvalidSessionIdentifierError as exc:
        raise StudioAPIError("INVALID_SESSION_IDENTIFIER", str(exc), status_code=400) from exc

    return StudioSessionDTO(
        session_id=meta.session_id,
        domain_namespace=meta.domain_namespace,
        enterprise_owner=meta.enterprise_owner,
        status=meta.status,
        active_version=meta.head_version,
        head_sha256=meta.head_sha256,
        created_at=meta.created_at,
        updated_at=meta.updated_at,
    )


@router.get("/sessions/{session_id}", response_model=StudioSessionDetailDTO)
def get_session(
    session_id: str,
    session_mgr: Annotated[StudioSessionManager, Depends(get_session_manager)],
    _principal: Annotated[StudioPrincipal, Depends(require_principal)],
) -> StudioSessionDetailDTO:
    """Retrieve studio session details and counts."""
    _validate_session_param(session_id)
    try:
        meta = session_mgr.get_session(session_id)
    except SessionNotFoundError as exc:
        raise StudioAPIError("SESSION_NOT_FOUND", str(exc), status_code=404) from exc

    sdir = session_mgr.get_session_dir(session_id)
    manifest_file = sdir / "inputs" / "manifest.json"
    sources_count = 0
    if manifest_file.is_file():
        with contextlib.suppress(Exception):
            mdata = json.loads(manifest_file.read_text(encoding="utf-8"))
            sources_count = len(mdata.get("files", []))

    proposals_dir = sdir / "proposals"
    proposals_count = len(list(proposals_dir.glob("*.json"))) if proposals_dir.is_dir() else 0

    versions_dir = sdir / "versions"
    versions_count = len(list(versions_dir.glob("v*"))) if versions_dir.is_dir() else 0

    return StudioSessionDetailDTO(
        session_id=meta.session_id,
        domain_namespace=meta.domain_namespace,
        enterprise_owner=meta.enterprise_owner,
        status=meta.status,
        active_version=meta.head_version,
        head_sha256=meta.head_sha256,
        created_at=meta.created_at,
        updated_at=meta.updated_at,
        sources_count=sources_count,
        proposals_count=proposals_count,
        versions_count=versions_count,
        grandfathered_concept_uris=[],
    )


@router.delete("/sessions/{session_id}", status_code=204)
def delete_session(
    session_id: str,
    session_mgr: Annotated[StudioSessionManager, Depends(get_session_manager)],
    principal: Annotated[StudioPrincipal, Depends(require_admin)],
    force: bool = Query(default=False),
) -> None:
    """Tombstone and remove session directory (admin required)."""
    _validate_session_param(session_id)
    try:
        session_mgr.get_session(session_id)
    except SessionNotFoundError as exc:
        raise StudioAPIError("SESSION_NOT_FOUND", str(exc), status_code=404) from exc

    sdir = session_mgr.get_session_dir(session_id)
    is_promoted = (sdir / "promotion.json").is_file()
    if is_promoted and not force:
        raise StudioAPIError(
            "CANNOT_DELETE_PROMOTED_SESSION",
            f"Session '{session_id}' has already been promoted. Pass force=true to delete.",
            status_code=422,
        )

    session_mgr.delete_session(session_id)


@router.get("/sessions/{session_id}/status", response_model=StudioSessionStatusDTO)
def get_session_status(
    session_id: str,
    session_mgr: Annotated[StudioSessionManager, Depends(get_session_manager)],
    _principal: Annotated[StudioPrincipal, Depends(require_principal)],
) -> StudioSessionStatusDTO:
    """Lightweight status check returning version state and active jobs."""
    _validate_session_param(session_id)
    try:
        meta = session_mgr.get_session(session_id)
    except SessionNotFoundError as exc:
        raise StudioAPIError("SESSION_NOT_FOUND", str(exc), status_code=404) from exc

    return StudioSessionStatusDTO(
        session_id=meta.session_id,
        status=meta.status,
        active_version=meta.head_version,
        head_sha256=meta.head_sha256,
        active_jobs=0,
    )


@router.get("/sessions/{session_id}/manifest", response_model=ManifestDTO)
def get_manifest(
    session_id: str,
    session_mgr: Annotated[StudioSessionManager, Depends(get_session_manager)],
    _principal: Annotated[StudioPrincipal, Depends(require_principal)],
) -> ManifestDTO:
    """Inspect CAS manifest listing all registered sources."""
    _validate_session_param(session_id)
    sdir = session_mgr.get_session_dir(session_id)
    if not sdir.is_dir():
        raise StudioAPIError(
            "SESSION_NOT_FOUND", f"Session '{session_id}' not found.", status_code=404
        )

    mfile = sdir / "inputs" / "manifest.json"
    files: list[dict[str, Any]] = []
    if mfile.is_file():
        try:
            mdata = json.loads(mfile.read_text(encoding="utf-8"))
            files = mdata.get("files", [])
        except (OSError, json.JSONDecodeError):
            files = []

    total_bytes = sum(f.get("size_bytes", 0) for f in files)
    return ManifestDTO(
        session_id=session_id,
        files=files,
        total_bytes=total_bytes,
    )


@router.get("/sessions/{session_id}/sources", response_model=list[SourceDocumentDTO])
def list_sources(
    session_id: str,
    session_mgr: Annotated[StudioSessionManager, Depends(get_session_manager)],
    _principal: Annotated[StudioPrincipal, Depends(require_principal)],
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


@router.post("/sessions/{session_id}/sources", response_model=SourceUploadResponse, status_code=201)
async def upload_source(
    session_id: str,
    file: UploadFile,
    session_mgr: Annotated[StudioSessionManager, Depends(get_session_manager)],
    _principal: Annotated[StudioPrincipal, Depends(require_principal)],
    request: Request,
    do_not_send_to_agent: Annotated[bool, Form()] = False,
    description: Annotated[str | None, Form()] = None,
) -> SourceUploadResponse:
    """Upload document to CAS blob store and register in inputs/manifest.json."""
    _validate_session_param(session_id)
    sdir = session_mgr.get_session_dir(session_id)
    if not sdir.is_dir():
        raise StudioAPIError(
            "SESSION_NOT_FOUND", f"Session '{session_id}' not found.", status_code=404
        )

    # Check query params fallback
    if not do_not_send_to_agent and "do_not_send_to_agent" in request.query_params:
        val = request.query_params.get("do_not_send_to_agent", "").lower()
        do_not_send_to_agent = val in ("true", "1", "yes")
    if description is None and "description" in request.query_params:
        description = request.query_params.get("description")

    content = await file.read()
    if not content:
        raise StudioAPIError(
            "CORRUPT_SOURCE_BLOB", "Uploaded source file is empty.", status_code=422
        )

    cas = session_mgr.get_cas(session_id)
    source_sha256 = cas.put(content)
    source_id = f"src-{source_sha256[:16]}"
    filename = file.filename or f"upload-{source_id}.bin"
    mime_type = file.content_type or "application/octet-stream"

    # Register in manifest under L5 session lock
    lock_path = sdir / ".session.lock"
    with studio_lock(lock_path, level=L5_SESSION, session_id=session_id):
        mfile = sdir / "inputs" / "manifest.json"
        if mfile.is_file():
            try:
                mdata = json.loads(mfile.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise StudioAPIError(
                    "CORRUPT_SESSION_RECORD",
                    f"Corrupt manifest file in session '{session_id}': {exc}",
                    status_code=500,
                ) from exc
        else:
            mdata = {"files": []}

        files = mdata.get("files", [])
        # Check if already present
        existing = next((f for f in files if f.get("source_id") == source_id), None)
        if existing:
            # Re-upload restores tombstoned source and refreshes metadata (MAJ-02)
            existing["tombstoned"] = False
            existing["deleted_at"] = None
            existing["filename"] = filename
            existing["size_bytes"] = len(content)
            existing["mime_type"] = mime_type
            existing["updated_at"] = datetime.now(UTC).isoformat()
            if do_not_send_to_agent:
                existing["do_not_send_to_agent"] = True
            if description is not None:
                existing["description"] = description
            effective_do_not_send = bool(existing.get("do_not_send_to_agent", False))
        else:
            existing = {
                "source_id": source_id,
                "filename": filename,
                "sha256": source_sha256,
                "size_bytes": len(content),
                "mime_type": mime_type,
                "do_not_send_to_agent": do_not_send_to_agent,
                "description": description,
                "uploaded_at": datetime.now(UTC).isoformat(),
                "tombstoned": False,
            }
            files.append(existing)
            effective_do_not_send = do_not_send_to_agent

        mdata["files"] = files

        # Atomic write for manifest
        tmp_mfile = sdir / "inputs" / f".manifest.{uuid.uuid4().hex[:8]}.tmp"
        tmp_mfile.write_text(json.dumps(mdata, indent=2), encoding="utf-8")
        os.replace(tmp_mfile, mfile)
        set_owner_only_permissions(mfile)

    return SourceUploadResponse(
        source_id=source_id,
        source_sha256=source_sha256,
        file_name=filename,
        filename=filename,
        size_bytes=len(content),
        byte_count=len(content),
        mime_type=mime_type,
        do_not_send_to_agent=effective_do_not_send,
    )


@router.get("/sessions/{session_id}/sources/{source_id}", response_model=SourceDocumentDTO)
def get_source_detail(
    session_id: str,
    source_id: str,
    session_mgr: Annotated[StudioSessionManager, Depends(get_session_manager)],
    _principal: Annotated[StudioPrincipal, Depends(require_principal)],
) -> SourceDocumentDTO:
    """Retrieve metadata for an ingested source document."""
    sources = list_sources(session_id, session_mgr, _principal)
    src = next((s for s in sources if s.source_id == source_id), None)
    if not src or src.tombstoned:
        raise StudioAPIError(
            "SOURCE_NOT_FOUND", f"Source '{source_id}' not found.", status_code=404
        )
    return src


@router.delete("/sessions/{session_id}/sources/{source_id}", status_code=204)
def delete_source_endpoint(
    session_id: str,
    source_id: str,
    session_mgr: Annotated[StudioSessionManager, Depends(get_session_manager)],
    _principal: Annotated[StudioPrincipal, Depends(require_principal)],
) -> None:
    _validate_session_param(session_id)
    sdir = session_mgr.get_session_dir(session_id)
    if not sdir.is_dir():
        raise StudioAPIError(
            "SESSION_NOT_FOUND", f"Session '{session_id}' not found.", status_code=404
        )

    # Check source exists
    src = get_source_detail(session_id, source_id, session_mgr, _principal)
    if not src or src.tombstoned:
        raise StudioAPIError(
            "SOURCE_NOT_FOUND", f"Source '{source_id}' not found.", status_code=404
        )

    # delete_source acquires L5 session lock, checks references, tombstones manifest, and purges
    try:
        delete_source(session_dir=sdir, source_id=source_id)
    except Exception as exc:
        err_msg = str(exc)
        if "SOURCE_NOT_FOUND" in err_msg:
            raise StudioAPIError("SOURCE_NOT_FOUND", err_msg, status_code=404) from exc
        if (
            "COMMITTED_VERSION" in err_msg
            or "SourceReferencedByCommittedVersionError" in type(exc).__name__
        ):
            raise StudioAPIError(
                "SOURCE_REFERENCED_BY_COMMITTED_VERSION",
                f"Source '{source_id}' is referenced by a committed version and cannot be deleted.",
                status_code=422,
            ) from exc
        if (
            "PENDING_PROPOSAL" in err_msg
            or "SourceReferencedByPendingProposalError" in type(exc).__name__
        ):
            raise StudioAPIError(
                "SOURCE_REFERENCED_BY_PENDING_PROPOSAL",
                f"Source '{source_id}' is referenced by a pending proposal and cannot be deleted.",
                status_code=422,
            ) from exc
        raise StudioAPIError("EXECUTION_FAILURE", str(exc), status_code=400) from exc


@router.get("/sessions/{session_id}/sources/{source_id}/content")
def get_source_content(
    session_id: str,
    source_id: str,
    session_mgr: Annotated[StudioSessionManager, Depends(get_session_manager)],
    _principal: Annotated[StudioPrincipal, Depends(require_principal)],
) -> Response:
    """Preview or download raw source content bytes from CAS (MAJ-06 security headers)."""
    import urllib.parse

    src = get_source_detail(session_id, source_id, session_mgr, _principal)
    cas = session_mgr.get_cas(session_id)
    try:
        raw_bytes = cas.get(src.source_sha256)
    except Exception as exc:
        raise StudioAPIError(
            "SOURCE_NOT_FOUND", f"Raw blob for '{source_id}' missing in CAS.", status_code=404
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
    _principal: Annotated[StudioPrincipal, Depends(require_principal)],
) -> JobHandleDTO:
    """Trigger background document layout extraction and OCR parsing job."""
    _validate_session_param(session_id)
    supervisor = get_job_supervisor()
    job_handle = supervisor.submit_job(
        session_mgr=session_mgr,
        session_id=session_id,
        job_type="parse",
        runner_fn=run_parse_job,
        principal=_principal.principal,
        source_ids=payload.source_ids,
        ocr_mode=payload.ocr_mode,
    )
    return JobHandleDTO(**job_handle)


@router.get("/sessions/{session_id}/clarifications", response_model=list[ClarificationQuestion])
@router.get("/sessions/{session_id}/questions", response_model=list[ClarificationQuestion])
def get_clarification_questions(
    session_id: str,
    session_mgr: Annotated[StudioSessionManager, Depends(get_session_manager)],
    _principal: Annotated[StudioPrincipal, Depends(require_principal)],
) -> list[ClarificationQuestion]:
    """Read generated ambiguity questions from session clarifications/questions.json."""
    _validate_session_param(session_id)
    sdir = session_mgr.get_session_dir(session_id)
    if not sdir.is_dir():
        raise StudioAPIError(
            "SESSION_NOT_FOUND", f"Session '{session_id}' not found.", status_code=404
        )

    qfile = sdir / "clarifications" / "questions.json"
    if not qfile.is_file():
        return []

    try:
        raw_data = json.loads(qfile.read_text(encoding="utf-8"))
        if isinstance(raw_data, list):
            return [ClarificationQuestion.model_validate(q) for q in raw_data]
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        logger.warning("Failed to parse questions.json: %s", exc)
    return []


@router.post("/sessions/{session_id}/clarify", response_model=JobHandleDTO, status_code=202)
def trigger_clarification(
    session_id: str,
    payload: ClarifyRequest,
    session_mgr: Annotated[StudioSessionManager, Depends(get_session_manager)],
    _principal: Annotated[StudioPrincipal, Depends(require_principal)],
) -> JobHandleDTO:
    """Dispatch background clarification question generation."""
    _validate_session_param(session_id)
    supervisor = get_job_supervisor()
    job_handle = supervisor.submit_job(
        session_mgr=session_mgr,
        session_id=session_id,
        job_type="clarify",
        runner_fn=run_clarify_job,
        principal=_principal.principal,
        mode=payload.mode,
        max_questions=payload.max_questions,
    )
    return JobHandleDTO(**job_handle)


@router.get("/sessions/{session_id}/answers", response_model=list[dict[str, Any]])
def get_clarification_answers(
    session_id: str,
    session_mgr: Annotated[StudioSessionManager, Depends(get_session_manager)],
    _principal: Annotated[StudioPrincipal, Depends(require_principal)],
) -> list[dict[str, Any]]:
    """Read persisted clarification answers from session clarifications/answers.json."""
    _validate_session_param(session_id)
    sdir = session_mgr.get_session_dir(session_id)
    if not sdir.is_dir():
        raise StudioAPIError(
            "SESSION_NOT_FOUND", f"Session '{session_id}' not found.", status_code=404
        )

    answers_file = sdir / "clarifications" / "answers.json"
    if not answers_file.is_file():
        return []

    try:
        raw_data = json.loads(answers_file.read_text(encoding="utf-8"))
        if isinstance(raw_data, list):
            return raw_data
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        logger.warning("Failed to parse answers.json: %s", exc)
    return []


@router.post("/sessions/{session_id}/answers", response_model=ClarificationAnswerResponse)
def submit_clarification_answers(
    session_id: str,
    payload: StudioClarificationAnswerRequest,
    session_mgr: Annotated[StudioSessionManager, Depends(get_session_manager)],
    _principal: Annotated[StudioPrincipal, Depends(require_principal)],
) -> ClarificationAnswerResponse:
    """Persist human clarification answers with mandatory OCC expected_answers_sha256 check."""
    _validate_session_param(session_id)
    sdir = session_mgr.get_session_dir(session_id)
    if not sdir.is_dir():
        raise StudioAPIError(
            "SESSION_NOT_FOUND", f"Session '{session_id}' not found.", status_code=404
        )

    answers_file = sdir / "clarifications" / "answers.json"
    answers_file.parent.mkdir(parents=True, exist_ok=True)
    lock_path = sdir / ".session.lock"

    with studio_lock(lock_path, level=L5_SESSION, session_id=session_id):
        # 1. Compute current stored answers SHA-256
        current_answers: list[dict[str, Any]] = []
        if answers_file.is_file():
            try:
                raw = json.loads(answers_file.read_text(encoding="utf-8"))
                if isinstance(raw, list):
                    current_answers = raw
                else:
                    raise StudioAPIError(
                        "CORRUPT_SESSION_RECORD",
                        f"Corrupt answers file in session '{session_id}': expected list.",
                        status_code=500,
                    )
            except json.JSONDecodeError as exc:
                raise StudioAPIError(
                    "CORRUPT_SESSION_RECORD",
                    f"Corrupt answers file in session '{session_id}': {exc}",
                    status_code=500,
                ) from exc

        if not current_answers:
            current_sha256 = EMPTY_ANSWERS_SHA256
        else:
            clean_current = [{k: v for k, v in a.items() if v is not None} for a in current_answers]
            sorted_items = sorted(clean_current, key=lambda a: a.get("question_id", ""))
            current_sha256 = hashlib.sha256(dumps_jcs_bytes(sorted_items)).hexdigest()

        # 2. Strict OCC verification
        if payload.expected_answers_sha256 is None:
            raise StudioAPIError(
                "MISSING_AUTO_COMMIT_PRECONDITION",
                "Submitting clarification answers requires expected_answers_sha256 precondition.",
                status_code=400,
                instance=f"/api/v1/ontology/studio/sessions/{session_id}/answers",
            )
        if payload.expected_answers_sha256 != current_sha256:
            raise StudioAPIError(
                "ONT_EVO_CONCURRENT_MODIFICATION",
                f"OCC conflict on answers: expected '{payload.expected_answers_sha256}', "
                f"current stored answers digest is '{current_sha256}'.",
                status_code=409,
                instance=f"/api/v1/ontology/studio/sessions/{session_id}/answers",
            )

        # 3. Merge newly answered items by question_id
        ans_items: list[ClarificationAnswerItem] = []
        try:
            if isinstance(payload.answers, dict):
                for qid, val in payload.answers.items():
                    if isinstance(val, dict):
                        ans_items.append(ClarificationAnswerItem(question_id=qid, **val))
                    elif isinstance(val, list):
                        ans_items.append(
                            ClarificationAnswerItem(question_id=qid, selected_options=val)
                        )
                    else:
                        ans_items.append(
                            ClarificationAnswerItem(question_id=qid, user_text=str(val))
                        )
            else:
                ans_items = payload.answers
        except (ValidationError, TypeError, ValueError) as exc:
            raise StudioAPIError(
                "SYNTAX_INVALID_ARGUMENT",
                f"Invalid clarification answer item format: {exc}",
                status_code=422,
            ) from exc

        answers_map = {
            a.get("question_id"): {k: v for k, v in a.items() if v is not None}
            for a in current_answers
            if a.get("question_id")
        }
        for ans in ans_items:
            answers_map[ans.question_id] = ans.model_dump(mode="json", exclude_none=True)

        merged_answers = sorted(answers_map.values(), key=lambda a: a.get("question_id", ""))
        new_sha256 = hashlib.sha256(dumps_jcs_bytes(merged_answers)).hexdigest()

        # Write to answers.json atomically
        tmp_answers = sdir / "clarifications" / f".answers.{uuid.uuid4().hex[:8]}.tmp"
        tmp_answers.write_text(json.dumps(merged_answers, indent=2), encoding="utf-8")
        os.replace(tmp_answers, answers_file)
        set_owner_only_permissions(answers_file)

    return ClarificationAnswerResponse(
        saved_count=len(ans_items),
        answers_sha256=new_sha256,
    )


@router.post("/sessions/{session_id}/construct", response_model=JobHandleDTO, status_code=202)
def trigger_construction(
    session_id: str,
    payload: ConstructRequest,
    session_mgr: Annotated[StudioSessionManager, Depends(get_session_manager)],
    _principal: Annotated[StudioPrincipal, Depends(require_principal)],
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> JobHandleDTO:
    """Trigger background construction job (offline deterministic pipeline)."""
    _validate_session_param(session_id)

    # Auto-commit precondition check
    if payload.auto_commit and not if_match:
        raise StudioAPIError(
            "MISSING_AUTO_COMMIT_PRECONDITION",
            "Auto-commit construction requires an 'If-Match' header "
            "anchoring session head SHA-256.",
            status_code=400,
        )

    meta = session_mgr.get_session(session_id)
    base_version = payload.base_version or meta.head_version
    base_sha256 = payload.base_sha256 or meta.head_sha256

    clean_if_match = if_match.strip('"') if if_match else None
    if clean_if_match and clean_if_match != base_sha256:
        raise StudioAPIError(
            "PRECONDITION_BASE_SHA_MISMATCH",
            f"If-Match header ('{clean_if_match}') does not match "
            f"body base_sha256 ('{base_sha256}').",
            status_code=400,
        )

    if payload.auto_commit:
        if _principal.principal.startswith("agent:"):
            raise StudioAPIError(
                "AGENT_PROPOSAL_ACCEPT_FORBIDDEN",
                "Automated agent identities are forbidden from auto-commit construction.",
                status_code=403,
            )
        if not _principal.is_admin:
            raise StudioAPIError(
                "FORBIDDEN_ADMIN_REQUIRED",
                "Auto-commit construction requires two-person review or admin role.",
                status_code=403,
            )

    supervisor = get_job_supervisor()
    job_handle = supervisor.submit_job(
        session_mgr=session_mgr,
        session_id=session_id,
        job_type="construct",
        runner_fn=run_construct_job,
        principal=_principal.principal,
        base_version=base_version,
        base_sha256=base_sha256,
        auto_commit=payload.auto_commit,
        fibo_modules=payload.fibo_modules,
        mode=payload.mode,
        if_match=clean_if_match,
    )
    return JobHandleDTO(**job_handle)


@router.get("/sessions/{session_id}/jobs/{job_id}", response_model=JobStatusDTO)
@router.get("/jobs/{job_id}", response_model=JobStatusDTO)
def get_job_status_endpoint(
    job_id: str,
    session_mgr: Annotated[StudioSessionManager, Depends(get_session_manager)],
    _principal: Annotated[StudioPrincipal, Depends(require_principal)],
    session_id: str | None = None,
) -> JobStatusDTO:
    """Get background job status, stage, pct_complete, and error/result."""
    if session_id:
        _validate_session_param(session_id)
    _validate_job_id_param(job_id)
    supervisor = get_job_supervisor()
    effective_sid = session_id
    if not effective_sid:
        # Search across active sessions
        for s in session_mgr.list_sessions():
            sdir = session_mgr.get_session_dir(s.session_id)
            if (sdir / "jobs" / f"{job_id}.json").is_file():
                effective_sid = s.session_id
                break
    if not effective_sid:
        raise StudioAPIError("JOB_NOT_FOUND", f"Job '{job_id}' not found.", status_code=404)

    job_data = supervisor.get_job(session_mgr, effective_sid, job_id)
    return JobStatusDTO(**job_data)


@router.get("/sessions/{session_id}/jobs/{job_id}/events")
@router.get("/jobs/{job_id}/events")
async def get_job_events_stream(
    job_id: str,
    request: Request,
    session_mgr: Annotated[StudioSessionManager, Depends(get_session_manager)],
    _principal: Annotated[StudioPrincipal, Depends(require_principal)],
    session_id: str | None = None,
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
) -> StreamingResponse:
    """Stream W3C Server-Sent Events with monotonic Last-Event-ID cursor replay
    and 15s keepalive."""
    if session_id:
        _validate_session_param(session_id)
    _validate_job_id_param(job_id)
    # Validate Last-Event-ID cursor
    if last_event_id is not None:
        trimmed = last_event_id.strip()
        if not re.match(r"^[0-9]+$", trimmed):
            raise StudioAPIError(
                "SYNTAX_INVALID_ARGUMENT",
                f"Invalid Last-Event-ID cursor '{last_event_id}'; must be a non-negative integer.",
                status_code=400,
            )

    supervisor = get_job_supervisor()
    effective_sid = session_id
    if not effective_sid:
        for s in session_mgr.list_sessions():
            sdir = session_mgr.get_session_dir(s.session_id)
            if (sdir / "jobs" / f"{job_id}.json").is_file():
                effective_sid = s.session_id
                break
    if not effective_sid:
        raise StudioAPIError("JOB_NOT_FOUND", f"Job '{job_id}' not found.", status_code=404)

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
    _principal: Annotated[StudioPrincipal, Depends(require_principal)],
    session_id: str | None = None,
    payload: dict[str, Any] | None = None,
) -> JobStatusDTO:
    """Cooperatively abort a running background job, releasing L2 lease."""
    if session_id:
        _validate_session_param(session_id)
    _validate_job_id_param(job_id)
    supervisor = get_job_supervisor()
    effective_sid = session_id
    if not effective_sid:
        for s in session_mgr.list_sessions():
            sdir = session_mgr.get_session_dir(s.session_id)
            if (sdir / "jobs" / f"{job_id}.json").is_file():
                effective_sid = s.session_id
                break
    if not effective_sid:
        raise StudioAPIError("JOB_NOT_FOUND", f"Job '{job_id}' not found.", status_code=404)

    reason = (payload or {}).get("reason")
    job_data = supervisor.cancel_job(session_mgr, effective_sid, job_id, reason=reason)
    return JobStatusDTO(**job_data)


# ---------------------------------------------------------------------------
# Task 5B.3 (Issue #217): Deltas, Proposals, Waivers, Export & Promotion
# ---------------------------------------------------------------------------


def _derive_genesis_target_version(operations: list[Any]) -> str:
    """Consistently derive genesis semver target based on canonical concepts (MIN-02)."""
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


@router.post("/sessions/{session_id}/deltas/validate", response_model=OntologyValidationResult)
def validate_delta_endpoint(
    session_id: str,
    payload: OntologyDeltaRequest,
    session_mgr: Annotated[StudioSessionManager, Depends(get_session_manager)],
    _principal: Annotated[StudioPrincipal, Depends(require_principal)],
) -> OntologyValidationResult:
    """Dry-run two-phase structural and governance validation on an OntologyDelta."""
    _validate_session_param(session_id)
    sdir = session_mgr.get_session_dir(session_id)
    if not sdir.is_dir():
        raise StudioAPIError(
            "SESSION_NOT_FOUND", f"Session '{session_id}' not found.", status_code=404
        )

    meta = session_mgr.get_session(session_id)
    violations: list[str] = []
    diagnostics: list[str] = []
    norm_ops = [_normalize_delta_operation(op) for op in payload.operations]

    # 1. OCC and Lineage Precondition Validation
    if meta.head_version == "0.0.0":
        if payload.parent_sha256 not in ("", None):
            violations.append(
                f"Genesis delta must have empty parent_sha256, got '{payload.parent_sha256}'."
            )
        expected_genesis = _derive_genesis_target_version(norm_ops)
        if payload.target_version and payload.target_version.lstrip("v") != expected_genesis:
            violations.append(
                f"target_version '{payload.target_version}' does not match expected "
                f"genesis version '{expected_genesis}'."
            )
    else:
        clean_parent = (payload.parent_sha256 or "").strip()
        if clean_parent != meta.head_sha256:
            violations.append(
                f"parent_sha256 '{clean_parent}' does not match session head '{meta.head_sha256}'."
            )
        clean_base_ver = (payload.base_version or "").lstrip("v")
        active_ver = meta.head_version.lstrip("v")
        if clean_base_ver != active_ver:
            violations.append(
                f"base_version '{payload.base_version}' does not match "
                f"session head version '{meta.head_version}'."
            )

    # 2. Structural & Schema Delta Construction Validation
    delta: OntologyDelta | None = None
    try:
        delta = OntologyDelta(
            delta_id=payload.delta_id,
            parent_sha256=payload.parent_sha256,
            base_version=payload.base_version,
            target_version=payload.target_version,
            operations=norm_ops,
            author=payload.author or "studio_validator",
            rationale=payload.rationale,
        )
    except (ValidationError, ValueError, TypeError) as exc:
        violations.append(f"Delta structural validation failure: {exc}")

    # 3. SemVer Bump and Semantic Concept Conflict Checks
    if meta.head_version != "0.0.0":
        if delta is not None:
            try:
                expected_next = compute_next_semver(base_version=meta.head_version, delta=delta)
                if payload.target_version and payload.target_version.lstrip(
                    "v"
                ) != expected_next.lstrip("v"):
                    violations.append(
                        f"target_version '{payload.target_version}' does not match "
                        f"required semver bump '{expected_next}'."
                    )
            except (ValueError, TypeError, KeyError) as exc:
                violations.append(f"SemVer bump computation failure: {exc}")

        head_ont_file = sdir / "versions" / f"v{meta.head_version}" / "ontology.json"
        if not head_ont_file.is_file():
            violations.append(
                f"Missing head ontology artifact: versions/v{meta.head_version}/ontology.json."
            )
        else:
            try:
                head_data = json.loads(head_ont_file.read_text(encoding="utf-8"))
                head_concepts = {
                    c.get("concept_uri")
                    for c in head_data.get("canonical_business_concepts", [])
                    if isinstance(c, dict)
                }
                for op in payload.operations:
                    op_type = (
                        op.get("op_type") if isinstance(op, dict) else getattr(op, "op_type", None)
                    )
                    if op_type in ("add_concept", "AddConceptOp"):
                        c_uri = (
                            op.get("concept_uri")
                            if isinstance(op, dict)
                            else getattr(op, "concept_uri", None)
                        )
                        if c_uri and c_uri in head_concepts:
                            violations.append(
                                f"Semantic conflict: concept '{c_uri}' already exists "
                                f"in head ontology version {meta.head_version}."
                            )
            except (OSError, json.JSONDecodeError) as exc:
                violations.append(f"Corrupt head ontology artifact: {exc}")

    is_valid = len(violations) == 0
    return OntologyValidationResult(
        valid=is_valid,
        violations=violations,
        diagnostics=diagnostics,
        error_code=None if is_valid else "VALIDATION_FAILED",
    )


@router.post("/sessions/{session_id}/delta", response_model=DeltaCommitResponse)
@router.post("/sessions/{session_id}/deltas/apply", response_model=DeltaCommitResponse)
def apply_delta_endpoint(
    session_id: str,
    payload: OntologyDeltaRequest,
    session_mgr: Annotated[StudioSessionManager, Depends(get_session_manager)],
    principal: Annotated[StudioPrincipal, Depends(require_principal)],
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> DeltaCommitResponse:
    """Direct delta commit with strict OCC If-Match precondition."""
    _validate_session_param(session_id)
    sdir = session_mgr.get_session_dir(session_id)
    if not sdir.is_dir():
        raise StudioAPIError(
            "SESSION_NOT_FOUND", f"Session '{session_id}' not found.", status_code=404
        )

    if principal.principal.startswith("agent:"):
        raise StudioAPIError(
            "AGENT_PROPOSAL_ACCEPT_FORBIDDEN",
            "Automated agent identities are forbidden from direct delta commits "
            "without proposal review.",
            status_code=403,
        )

    if not principal.is_admin:
        raise StudioAPIError(
            "FORBIDDEN_ADMIN_REQUIRED",
            "Direct delta commit without proposal review requires administrator role.",
            status_code=403,
        )

    if not if_match:
        raise StudioAPIError(
            "MISSING_PRECONDITION_HEADER",
            "Applying a direct delta requires an 'If-Match' header anchoring session head SHA-256.",
            status_code=400,
        )

    clean_if_match = if_match.strip('"')
    meta = session_mgr.get_session(session_id)

    is_genesis = meta.head_version == "0.0.0" and payload.parent_sha256 in ("", None)
    if is_genesis:
        if clean_if_match not in ("", meta.head_sha256):
            raise StudioAPIError(
                "ONT_EVO_CONCURRENT_MODIFICATION",
                f"Session head SHA-256 ('{meta.head_sha256}') does not match "
                f"expected ('{clean_if_match}').",
                status_code=412,
            )
    else:
        if clean_if_match != payload.parent_sha256:
            raise StudioAPIError(
                "PRECONDITION_BASE_SHA_MISMATCH",
                f"If-Match header ('{clean_if_match}') does not match "
                f"body parent_sha256 ('{payload.parent_sha256}').",
                status_code=400,
            )
        if meta.head_sha256 != clean_if_match:
            raise StudioAPIError(
                "ONT_EVO_CONCURRENT_MODIFICATION",
                f"Session head SHA-256 ('{meta.head_sha256}') does not match "
                f"expected ('{clean_if_match}').",
                status_code=412,
            )

    norm_ops = [_normalize_delta_operation(op) for op in payload.operations]
    try:
        delta = OntologyDelta(
            delta_id=payload.delta_id,
            parent_sha256=payload.parent_sha256,
            base_version=payload.base_version,
            target_version=payload.target_version,
            operations=norm_ops,
            author=payload.author or principal.principal,
            rationale=payload.rationale,
        )
    except (ValidationError, ValueError, TypeError) as exc:
        raise StudioAPIError(
            "ONT_EVO_CONTRACT_VIOLATION",
            f"Delta structural validation failure: {exc}",
            status_code=422,
        ) from exc

    atomic_svc = AtomicCommitService(session_manager=session_mgr)
    try:
        commit_res = atomic_svc.commit_delta(
            session_id=session_id,
            delta=delta,
            proposal_id=None,
            author=principal.principal,
            if_match=clean_if_match,
        )
    except ConcurrentModificationError as exc:
        raise StudioAPIError("ONT_EVO_CONCURRENT_MODIFICATION", str(exc), status_code=412) from exc
    except Exception as exc:
        raise StudioAPIError("ONT_EVO_CONTRACT_VIOLATION", str(exc), status_code=422) from exc

    return DeltaCommitResponse(
        session_id=session_id,
        target_version=commit_res.version,
        target_sha256=commit_res.target_sha256,
        parent_sha256=payload.parent_sha256,
        delta_id=payload.delta_id,
        bump_type=delta.bump_type.value if hasattr(delta, "bump_type") else "MINOR",
        applied_operations_count=len(payload.operations),
    )


@router.get("/sessions/{session_id}/proposals", response_model=list[ProposalRecordDTO])
def list_proposals(
    session_id: str,
    session_mgr: Annotated[StudioSessionManager, Depends(get_session_manager)],
    _principal: Annotated[StudioPrincipal, Depends(require_principal)],
) -> list[ProposalRecordDTO]:
    """List all candidate proposals with current status."""
    _validate_session_param(session_id)
    sdir = session_mgr.get_session_dir(session_id)
    if not sdir.is_dir():
        raise StudioAPIError(
            "SESSION_NOT_FOUND", f"Session '{session_id}' not found.", status_code=404
        )

    proposals_dir = sdir / "proposals"
    if not proposals_dir.is_dir():
        return []

    records: list[ProposalRecordDTO] = []
    for pfile in sorted(proposals_dir.glob("*.json")):
        try:
            data = json.loads(pfile.read_text(encoding="utf-8"))
            records.append(
                ProposalRecordDTO(
                    proposal_id=data.get("proposal_id", pfile.stem),
                    session_id=session_id,
                    base_version=data.get("base_version", "1.0.0"),
                    base_sha256=data.get("base_sha256", ""),
                    status=data.get("status", "PENDING"),
                    operations=data.get("operations", []),
                    proposer=data.get("proposer", "agent:unknown"),
                    created_at=data.get("created_at", datetime.now(UTC).isoformat()),
                    rationale=data.get("rationale"),
                )
            )
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            logger.debug("Skipping unreadable proposal file %s: %s", pfile, exc)
            continue
    return records


@router.post("/sessions/{session_id}/proposals", response_model=ProposalRecordDTO, status_code=201)
def create_proposal(
    session_id: str,
    payload: ProposalCreateRequest,
    session_mgr: Annotated[StudioSessionManager, Depends(get_session_manager)],
    principal: Annotated[StudioPrincipal, Depends(require_principal)],
) -> ProposalRecordDTO:
    """Submit a candidate proposal without committing."""
    _validate_session_param(session_id)
    sdir = session_mgr.get_session_dir(session_id)
    if not sdir.is_dir():
        raise StudioAPIError(
            "SESSION_NOT_FOUND", f"Session '{session_id}' not found.", status_code=404
        )

    proposal_id = f"prop-{uuid.uuid4().hex[:12]}"
    now_iso = datetime.now(UTC).isoformat()
    meta = session_mgr.get_session(session_id)
    base_version = payload.base_version or meta.head_version
    base_sha256 = payload.base_sha256 or meta.head_sha256

    # Structurally validate candidate delta operations (MIN-01)
    norm_ops = [_normalize_delta_operation(op) for op in payload.operations]
    try:
        t_ver = _derive_genesis_target_version(norm_ops) if base_version == "0.0.0" else "1.0.0"
        OntologyDelta(
            delta_id=f"delta-{uuid.uuid4().hex[:12]}",
            parent_sha256="" if base_version == "0.0.0" else base_sha256,
            base_version=base_version,
            target_version=t_ver,
            operations=norm_ops,
            author=principal.principal,
        )
    except (ValidationError, ValueError, TypeError) as exc:
        raise StudioAPIError(
            "ONT_EVO_CONTRACT_VIOLATION",
            f"Invalid proposal operations: {exc}",
            status_code=422,
        ) from exc

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
    }

    proposals_dir = sdir / "proposals"
    proposals_dir.mkdir(parents=True, exist_ok=True)
    prop_file = proposals_dir / f"{proposal_id}.json"
    tmp_prop = proposals_dir / f".{proposal_id}.{uuid.uuid4().hex[:8]}.tmp"
    tmp_prop.write_text(json.dumps(record, indent=2), encoding="utf-8")
    os.replace(tmp_prop, prop_file)
    set_owner_only_permissions(prop_file)

    return ProposalRecordDTO(**record)


@router.get("/sessions/{session_id}/proposals/{proposal_id}", response_model=ProposalRecordDTO)
def get_proposal_detail(
    session_id: str,
    proposal_id: str,
    session_mgr: Annotated[StudioSessionManager, Depends(get_session_manager)],
    _principal: Annotated[StudioPrincipal, Depends(require_principal)],
) -> ProposalRecordDTO:
    """Fetch proposal record and conflict status."""
    _validate_session_param(session_id)
    sdir = session_mgr.get_session_dir(session_id)
    prop_file = _resolve_proposal_file(sdir, proposal_id)
    if not prop_file.is_file():
        raise StudioAPIError(
            "PROPOSAL_NOT_FOUND", f"Proposal '{proposal_id}' not found.", status_code=404
        )

    data = json.loads(prop_file.read_text(encoding="utf-8"))
    return ProposalRecordDTO(
        proposal_id=data.get("proposal_id", proposal_id),
        session_id=session_id,
        base_version=data.get("base_version", "1.0.0"),
        base_sha256=data.get("base_sha256", ""),
        status=data.get("status", "PENDING"),
        operations=data.get("operations", []),
        proposer=data.get("proposer", "unknown"),
        created_at=data.get("created_at", datetime.now(UTC).isoformat()),
        rationale=data.get("rationale"),
    )


@router.post(
    "/sessions/{session_id}/proposals/{proposal_id}/accept", response_model=DeltaCommitResponse
)
def accept_proposal(
    session_id: str,
    proposal_id: str,
    payload: ProposalAcceptRequest,
    session_mgr: Annotated[StudioSessionManager, Depends(get_session_manager)],
    principal: Annotated[StudioPrincipal, Depends(require_principal)],
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> DeltaCommitResponse:
    """Accept pending proposal with OCC If-Match, role check, and waiver validation."""
    _validate_session_param(session_id)
    sdir = session_mgr.get_session_dir(session_id)
    prop_file = _resolve_proposal_file(sdir, proposal_id)
    if not prop_file.is_file():
        raise StudioAPIError(
            "PROPOSAL_NOT_FOUND", f"Proposal '{proposal_id}' not found.", status_code=404
        )

    prop_data = json.loads(prop_file.read_text(encoding="utf-8"))
    if prop_data.get("status") in ("ACCEPTED", "REJECTED"):
        raise StudioAPIError(
            "PROPOSAL_ALREADY_RESOLVED",
            f"Proposal '{proposal_id}' has already been resolved ({prop_data.get('status')}).",
            status_code=409,
        )

    # Reject automated agent callers
    if principal.principal.startswith("agent:"):
        raise StudioAPIError(
            "AGENT_PROPOSAL_ACCEPT_FORBIDDEN",
            "Automated agent identities are strictly forbidden from accepting proposals.",
            status_code=403,
        )

    # Enforce admin role on waivers/overrides
    if (
        payload.self_acceptance_override or payload.low_confidence_waiver
    ) and not principal.is_admin:
        raise StudioAPIError(
            "FORBIDDEN_ADMIN_REQUIRED",
            "Administrative waivers and self-acceptance overrides strictly require admin role.",
            status_code=403,
        )

    # Self-acceptance check
    proposer = prop_data.get("proposer")
    if proposer == principal.principal and not payload.self_acceptance_override:
        raise StudioAPIError(
            "PROPOSAL_SELF_ACCEPTANCE_FORBIDDEN",
            "Self-acceptance of proposals is forbidden without "
            "--allow-self-accepted-proposals override.",
            status_code=403,
        )

    # OCC check
    meta = session_mgr.get_session(session_id)
    clean_if_match = if_match.strip('"') if if_match else payload.base_sha256
    if clean_if_match != meta.head_sha256:
        raise StudioAPIError(
            "ONT_EVO_CONCURRENT_MODIFICATION",
            f"If-Match header ('{clean_if_match}') does not match "
            f"session head SHA-256 ('{meta.head_sha256}').",
            status_code=412,
        )

    # Stale proposal check (MAJ-03, MIN-11)
    prop_base = prop_data.get("base_sha256") or ""
    is_genesis = prop_base == "" and meta.head_version == "0.0.0"
    if prop_base != meta.head_sha256 and not is_genesis:
        raise StudioAPIError(
            "PROPOSAL_STALE_REBASE_REQUIRED",
            f"Proposal base SHA-256 ('{prop_base}') does not match "
            f"current session head ('{meta.head_sha256}'). Proposal rebase is required.",
            status_code=409,
        )

    # Waiver rationale validation if waiver requested
    waiver_record: StewardWaiverRecord | None = None
    if payload.low_confidence_waiver:
        if not payload.grounding_rationale or len(payload.grounding_rationale.strip()) < 10:
            raise StudioAPIError(
                "INVALID_GROUNDING_WAIVER",
                "Administrative grounding waiver requires a rationale of at least 10 characters.",
                status_code=422,
            )
        waiver_record = StewardWaiverRecord(
            steward=principal.principal,
            waived_at=datetime.now(UTC).isoformat(),
            rationale=payload.grounding_rationale.strip(),
            ungrounded_concepts=payload.ungrounded_concept or [],
            evidence_file=payload.evidence_grounding_file,
        )

    # Convert proposal operations to delta
    raw_ops = prop_data.get("operations", [])
    ops = [_normalize_delta_operation(op) for op in raw_ops]
    try:
        if meta.head_version == "0.0.0":
            next_ver = _derive_genesis_target_version(ops)
        else:
            b_maj, b_min, _b_pat = parse_semver_tuple(meta.head_version)
            temp_delta = OntologyDelta(
                delta_id=f"delta-{uuid.uuid4().hex[:12]}",
                parent_sha256=meta.head_sha256,
                base_version=meta.head_version,
                target_version=f"{b_maj}.{b_min + 1}.0",
                operations=ops,
            )
            next_ver = compute_next_semver(base_version=meta.head_version, delta=temp_delta)

        delta = OntologyDelta(
            delta_id=f"delta-{uuid.uuid4().hex[:12]}",
            parent_sha256="" if meta.head_version == "0.0.0" else meta.head_sha256,
            base_version=meta.head_version,
            target_version=next_ver,
            operations=ops,
            author=principal.principal,
            rationale=prop_data.get("rationale") or payload.grounding_rationale,
        )
    except (ValidationError, ValueError, TypeError) as exc:
        raise StudioAPIError(
            "ONT_EVO_CONTRACT_VIOLATION",
            f"Proposal delta construction failure: {exc}",
            status_code=422,
        ) from exc

    atomic_svc = AtomicCommitService(session_manager=session_mgr)
    eval_dict = {"waiver": waiver_record.model_dump(mode="json")} if waiver_record else None
    try:
        commit_res = atomic_svc.commit_delta(
            session_id=session_id,
            delta=delta,
            proposal_id=proposal_id,
            author=principal.principal,
            if_match=clean_if_match,
            evaluation=eval_dict,
        )
    except ConcurrentModificationError as exc:
        if "already been resolved" in str(exc):
            raise StudioAPIError(
                "PROPOSAL_ALREADY_RESOLVED",
                f"Proposal '{proposal_id}' has already been resolved.",
                status_code=409,
            ) from exc
        raise StudioAPIError("ONT_EVO_CONCURRENT_MODIFICATION", str(exc), status_code=412) from exc
    except Exception as exc:
        raise StudioAPIError("ONT_EVO_CONTRACT_VIOLATION", str(exc), status_code=422) from exc

    return DeltaCommitResponse(
        session_id=session_id,
        target_version=commit_res.version,
        target_sha256=commit_res.target_sha256,
        parent_sha256=meta.head_sha256,
        delta_id=delta.delta_id,
        bump_type=delta.bump_type.value if hasattr(delta, "bump_type") else "MINOR",
        applied_operations_count=len(ops),
    )


@router.post(
    "/sessions/{session_id}/proposals/{proposal_id}/reject", response_model=ProposalRecordDTO
)
def reject_proposal(
    session_id: str,
    proposal_id: str,
    session_mgr: Annotated[StudioSessionManager, Depends(get_session_manager)],
    _principal: Annotated[StudioPrincipal, Depends(require_principal)],
    payload: RejectProposalRequest | None = None,
) -> ProposalRecordDTO:
    """Mark proposal REJECTED while preserving in CAS for audit trace."""
    _validate_session_param(session_id)
    sdir = session_mgr.get_session_dir(session_id)
    if not sdir.is_dir():
        raise StudioAPIError(
            "SESSION_NOT_FOUND", f"Session '{session_id}' not found.", status_code=404
        )
    prop_file = _resolve_proposal_file(sdir, proposal_id)
    if not prop_file.is_file():
        raise StudioAPIError(
            "PROPOSAL_NOT_FOUND", f"Proposal '{proposal_id}' not found.", status_code=404
        )

    with studio_lock(sdir / ".session.lock", level=L5_SESSION, session_id=session_id):
        try:
            prop_data = json.loads(prop_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise StudioAPIError(
                "CORRUPT_SESSION_RECORD",
                f"Corrupt proposal record '{proposal_id}': {exc}",
                status_code=500,
            ) from exc

        if prop_data.get("status") in ("ACCEPTED", "REJECTED"):
            raise StudioAPIError(
                "PROPOSAL_ALREADY_RESOLVED",
                f"Proposal '{proposal_id}' has already been resolved ({prop_data.get('status')}).",
                status_code=409,
            )

        prop_data["status"] = "REJECTED"
        prop_data["reject_reason"] = (
            payload.reason if payload and payload.reason else "Rejected by steward"
        )
        prop_data["resolved_at"] = datetime.now(UTC).isoformat()
        tmp_prop = sdir / "proposals" / f".{proposal_id}.{uuid.uuid4().hex[:8]}.tmp"
        tmp_prop.write_text(json.dumps(prop_data, indent=2), encoding="utf-8")
        os.replace(tmp_prop, prop_file)
        set_owner_only_permissions(prop_file)

    return ProposalRecordDTO(
        proposal_id=prop_data.get("proposal_id", proposal_id),
        session_id=session_id,
        base_version=prop_data.get("base_version", "1.0.0"),
        base_sha256=prop_data.get("base_sha256", ""),
        status="REJECTED",
        operations=prop_data.get("operations", []),
        proposer=prop_data.get("proposer", "unknown"),
        created_at=prop_data.get("created_at", datetime.now(UTC).isoformat()),
        rationale=prop_data.get("rationale"),
    )


@router.post(
    "/sessions/{session_id}/proposals/{proposal_id}/rebase", response_model=ProposalRecordDTO
)
def rebase_proposal(
    session_id: str,
    proposal_id: str,
    payload: ProposalRebaseRequest,
    session_mgr: Annotated[StudioSessionManager, Depends(get_session_manager)],
    _principal: Annotated[StudioPrincipal, Depends(require_principal)],
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> ProposalRecordDTO:
    """Rebase proposal onto new session head SHA-256."""
    _validate_session_param(session_id)
    sdir = session_mgr.get_session_dir(session_id)
    prop_file = _resolve_proposal_file(sdir, proposal_id)
    if not prop_file.is_file():
        raise StudioAPIError(
            "PROPOSAL_NOT_FOUND", f"Proposal '{proposal_id}' not found.", status_code=404
        )

    with studio_lock(sdir / ".session.lock", level=L5_SESSION, session_id=session_id):
        prop_data = json.loads(prop_file.read_text(encoding="utf-8"))
        if prop_data.get("status") in ("ACCEPTED", "REJECTED"):
            raise StudioAPIError(
                "PROPOSAL_ALREADY_RESOLVED",
                f"Proposal '{proposal_id}' has already been resolved ({prop_data.get('status')}).",
                status_code=409,
            )

        meta = session_mgr.get_session(session_id)
        clean_if_match = if_match.strip('"') if if_match else payload.base_sha256
        if not clean_if_match:
            raise StudioAPIError(
                "SYNTAX_INVALID_ARGUMENT",
                "Rebase requires an If-Match header or base_sha256 in payload.",
                status_code=400,
            )
        if clean_if_match != meta.head_sha256:
            raise StudioAPIError(
                "ONT_EVO_CONCURRENT_MODIFICATION",
                f"If-Match header ('{clean_if_match}') does not match "
                f"session head SHA-256 ('{meta.head_sha256}').",
                status_code=412,
            )

        # Semantic validation against head ontology (MAJ-02, MAJ-04)
        raw_ops = prop_data.get("operations", [])
        ops = [_normalize_delta_operation(op) for op in raw_ops]
        if meta.head_version != "0.0.0":
            head_ont_file = sdir / "versions" / f"v{meta.head_version}" / "ontology.json"
            if not head_ont_file.is_file():
                raise StudioAPIError(
                    "CORRUPT_SESSION_RECORD",
                    f"Head ontology missing for version '{meta.head_version}' "
                    f"in session '{session_id}'.",
                    status_code=500,
                )
            try:
                head_data = json.loads(head_ont_file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise StudioAPIError(
                    "CORRUPT_SESSION_RECORD",
                    f"Corrupt head ontology for version '{meta.head_version}': {exc}",
                    status_code=500,
                ) from exc

            head_concepts = {
                c.get("concept_uri")
                for c in head_data.get("canonical_business_concepts", [])
                if isinstance(c, dict)
            }
            for op in ops:
                op_type = (
                    op.get("op_type") if isinstance(op, dict) else getattr(op, "op_type", None)
                )
                if op_type in ("add_concept", "AddConceptOp"):
                    c_uri = (
                        op.get("concept_uri")
                        if isinstance(op, dict)
                        else getattr(op, "concept_uri", None)
                    )
                    if c_uri and c_uri in head_concepts:
                        err = (
                            f"Rebase conflict: concept '{c_uri}' already exists "
                            f"in head ontology version {meta.head_version}."
                        )
                        raise StudioAPIError(
                            "ONT_EVO_CONTRACT_VIOLATION",
                            err,
                            status_code=409,
                        )

        # Validate structural integrity of delta operations
        if meta.head_version == "0.0.0":
            t_ver = _derive_genesis_target_version(ops)
        else:
            b_maj, b_min, _ = parse_semver_tuple(meta.head_version)
            t_ver = f"{b_maj}.{b_min + 1}.0"
        try:
            OntologyDelta(
                delta_id=f"delta-{uuid.uuid4().hex[:12]}",
                parent_sha256="" if meta.head_version == "0.0.0" else meta.head_sha256,
                base_version=meta.head_version,
                target_version=t_ver,
                operations=ops,
                author=prop_data.get("proposer", "unknown"),
            )
        except (ValidationError, ValueError, TypeError) as exc:
            raise StudioAPIError(
                "ONT_EVO_CONTRACT_VIOLATION",
                f"Rebase delta structural integrity check failed: {exc}",
                status_code=422,
            ) from exc

        prop_data["base_sha256"] = meta.head_sha256
        prop_data["base_version"] = meta.head_version
        prop_data["status"] = "PENDING"
        prop_data["operations"] = ops
        tmp_prop = sdir / "proposals" / f".{proposal_id}.{uuid.uuid4().hex[:8]}.tmp"
        tmp_prop.write_text(json.dumps(prop_data, indent=2), encoding="utf-8")
        os.replace(tmp_prop, prop_file)
        set_owner_only_permissions(prop_file)

    return ProposalRecordDTO(
        proposal_id=prop_data.get("proposal_id", proposal_id),
        session_id=session_id,
        base_version=meta.head_version,
        base_sha256=meta.head_sha256,
        status="PENDING",
        operations=prop_data.get("operations", []),
        proposer=prop_data.get("proposer", "unknown"),
        created_at=prop_data.get("created_at", datetime.now(UTC).isoformat()),
        rationale=prop_data.get("rationale"),
    )


@router.post("/sessions/{session_id}/waiver")
def submit_waiver(
    session_id: str,
    waiver: StewardWaiverRecord,
    session_mgr: Annotated[StudioSessionManager, Depends(get_session_manager)],
    principal: Annotated[StudioPrincipal, Depends(require_admin)],
) -> dict[str, Any]:
    """Authorize administrative steward waiver with rationale >= 10 chars."""
    _validate_session_param(session_id)
    if not waiver.rationale or len(waiver.rationale.strip()) < 10:
        raise StudioAPIError(
            "INVALID_GROUNDING_WAIVER",
            "Waiver rationale must be at least 10 characters.",
            status_code=422,
        )

    sdir = session_mgr.get_session_dir(session_id)
    if not sdir.is_dir():
        raise StudioAPIError(
            "SESSION_NOT_FOUND", f"Session '{session_id}' not found.", status_code=404
        )
    audit_file = sdir / "audit.jsonl"
    with studio_lock(sdir / ".session.lock", level=L5_SESSION, session_id=session_id):
        waiver_dict = waiver.model_dump(mode="json")
        waiver_dict["steward"] = principal.principal
        with open(audit_file, "a", encoding="utf-8") as fp:
            fp.write(
                json.dumps({"action": "STEWARD_WAIVER_AUTHORIZED", "waiver": waiver_dict}) + "\n"
            )
        set_owner_only_permissions(audit_file)

    waiver_id_str = str(getattr(waiver, "waiver_id", ""))
    return {"status": "authorized", "waiver_id": waiver_id_str}


@router.get("/sessions/{session_id}/export")
def export_ontology_endpoint(
    session_id: str,
    session_mgr: Annotated[StudioSessionManager, Depends(get_session_manager)],
    _principal: Annotated[StudioPrincipal, Depends(require_principal)],
    export_format: str = Query(default="yaml"),
    version: str | None = Query(default=None),
) -> Response:
    """Multi-format export of active or historical ontology snapshot."""
    _validate_session_param(session_id)
    sdir = session_mgr.get_session_dir(session_id)
    if not sdir.is_dir():
        raise StudioAPIError(
            "SESSION_NOT_FOUND", f"Session '{session_id}' not found.", status_code=404
        )

    meta = session_mgr.get_session(session_id)
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
            status_code=400,
        )
    if not vdir.is_dir():
        raise StudioAPIError(
            "VERSION_NOT_FOUND",
            f"Version '{target_version}' not found in session.",
            status_code=404,
        )

    ont_file = vdir / "ontology.json"
    if not ont_file.is_file():
        ont_file = vdir / "ontology.yaml"
    if not ont_file.is_file():
        raise StudioAPIError(
            "VERSION_NOT_FOUND",
            f"Ontology artifact missing for v{target_version}.",
            status_code=404,
        )

    if ont_file.suffix == ".json":
        raw_json = ont_file.read_text(encoding="utf-8")
        ont_obj = GraphAOntology.model_validate_json(raw_json)
    else:
        ont_obj = GraphAOntology.model_validate(
            yaml.safe_load(ont_file.read_text(encoding="utf-8"))
        )

    try:
        content, media_type = export_ontology(ont_obj, export_format=export_format)
    except ValueError as exc:
        raise StudioAPIError("UNSUPPORTED_FORMAT", str(exc), status_code=400) from exc

    return Response(
        content=content,
        media_type=media_type,
        headers={
            "Content-Disposition": (
                f'attachment; filename="ontology-v{target_version}.{export_format}"'
            )
        },
    )


@router.post("/sessions/{session_id}/promote", response_model=PromoteResponse)
def promote_session(
    session_id: str,
    payload: PromoteRequest,
    session_mgr: Annotated[StudioSessionManager, Depends(get_session_manager)],
    principal: Annotated[StudioPrincipal, Depends(require_admin)],
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
    x_workspace_if_match: Annotated[str | None, Header(alias="X-OpenDV-Workspace-If-Match")] = None,
) -> PromoteResponse:
    """Two-phase atomic promotion of head ontology into workspace under L4 + L5 locks."""
    _validate_session_param(session_id)
    if not payload.rationale or len(payload.rationale.strip()) < 10:
        raise StudioAPIError(
            "PROMOTION_RATIONALE_REQUIRED",
            "Promotion rationale must be at least 10 characters.",
            status_code=400,
        )

    clean_if_match = if_match.strip('"') if if_match else payload.head_sha256
    clean_ws_if_match = (
        x_workspace_if_match.strip('"') if x_workspace_if_match else payload.workspace_sha256
    )

    if clean_if_match != payload.head_sha256:
        raise StudioAPIError(
            "SYNTAX_INVALID_ARGUMENT",
            f"If-Match header ('{clean_if_match}') does not match "
            f"body head_sha256 ('{payload.head_sha256}').",
            status_code=400,
        )

    sdir = session_mgr.get_session_dir(session_id)
    if not sdir.is_dir():
        raise StudioAPIError(
            "SESSION_NOT_FOUND", f"Session '{session_id}' not found.", status_code=404
        )

    meta = session_mgr.get_session(session_id)
    if meta.head_sha256 != clean_if_match:
        raise StudioAPIError(
            "ONT_EVO_CONCURRENT_MODIFICATION",
            f"If-Match ('{clean_if_match}') does not match "
            f"session head SHA-256 ('{meta.head_sha256}').",
            status_code=412,
        )

    # Workspace containment check (MIN-07)
    ws_root = session_mgr.workspace_root.resolve()
    if payload.workspace:
        custom_path = Path(payload.workspace)
        custom_ws = (
            (ws_root / custom_path).resolve()
            if not custom_path.is_absolute()
            else custom_path.resolve()
        )
        try:
            ws_norm = os.path.normcase(ws_root)
            custom_norm = os.path.normcase(custom_ws)
            if os.path.commonpath([ws_norm, custom_norm]) != ws_norm:
                raise StudioAPIError(
                    "INVALID_WORKSPACE_PATH",
                    "Target workspace escapes workspace root boundaries.",
                    status_code=400,
                )
            target_ws = custom_ws
        except ValueError as exc:
            raise StudioAPIError("INVALID_WORKSPACE_PATH", str(exc), status_code=400) from exc
    else:
        target_ws = ws_root

    target_contract_filename = f"{meta.domain_namespace}_graph_a_ontology.yaml"
    target_contract_path = (target_ws / target_contract_filename).resolve()
    if not target_contract_path.is_relative_to(target_ws):
        raise StudioAPIError(
            "INVALID_WORKSPACE_PATH",
            "Target contract path escapes workspace boundary.",
            status_code=400,
        )
    target_contract_rel = str(target_contract_path.relative_to(ws_root))

    # Lock order: L4 Workspace lock then L5 Session lock
    l4_lock_file = target_ws / "graph_a.lock"
    l5_lock_file = sdir / ".session.lock"

    with (
        studio_lock(l4_lock_file, level=L4_WORKSPACE),
        studio_lock(l5_lock_file, level=L5_SESSION, session_id=session_id),
    ):
        # Re-load session metadata under lock and verify OCC again (MAJ-01)
        meta = session_mgr.get_session(session_id)
        if meta.head_sha256 != clean_if_match:
            raise StudioAPIError(
                "ONT_EVO_CONCURRENT_MODIFICATION",
                f"If-Match ('{clean_if_match}') does not match "
                f"session head SHA-256 ('{meta.head_sha256}').",
                status_code=412,
            )

        # Lineage root & workspace digest verification
        ws_digest = "0" * 64
        if target_contract_path.is_file():
            ws_digest = hashlib.sha256(target_contract_path.read_bytes()).hexdigest()

        if clean_ws_if_match != ws_digest:
            raise StudioAPIError(
                "ONT_EVO_CONCURRENT_MODIFICATION",
                f"Target workspace contract digest ('{ws_digest}') does not match "
                f"expected ('{clean_ws_if_match}').",
                status_code=412,
            )

        # Check contract version downgrade guard and rationale (MAJ-03, MIN-06)
        if target_contract_path.is_file():
            try:
                existing_doc = yaml.safe_load(target_contract_path.read_text(encoding="utf-8"))
                if (
                    isinstance(existing_doc, dict)
                    and "ontology_version" in existing_doc
                    and parse_semver_tuple(meta.head_version)
                    < parse_semver_tuple(str(existing_doc["ontology_version"]))
                ):
                    existing_ver = str(existing_doc["ontology_version"])
                    if not payload.acknowledge_historical_contract_warning:
                        msg = (
                            f"Promotion version '{meta.head_version}' is lower than existing "
                            f"workspace contract version '{existing_ver}'. "
                            "Acknowledge historical contract warning required for downgrade."
                        )
                        raise StudioAPIError("ONT_EVO_CONTRACT_VIOLATION", msg, status_code=422)
                    if (
                        payload.historical_contract_rationale is not None
                        and len(payload.historical_contract_rationale.strip()) < 10
                    ):
                        raise StudioAPIError(
                            "ONT_EVO_CONTRACT_VIOLATION",
                            "Contract downgrade historical_contract_rationale "
                            "must be at least 10 characters.",
                            status_code=422,
                        )
            except StudioAPIError:
                raise
            except (yaml.YAMLError, OSError, ValueError):
                pass

        # Load active version yaml to promote
        vdir = sdir / "versions" / f"v{meta.head_version}"
        yaml_src = vdir / "ontology.yaml"
        if not yaml_src.is_file():
            # Generate from json if yaml missing
            ont_json = vdir / "ontology.json"
            if ont_json.is_file():
                raw_dict = json.loads(ont_json.read_text(encoding="utf-8"))
                yaml_content = yaml.dump(raw_dict, sort_keys=False)
            else:
                raise StudioAPIError(
                    "VERSION_NOT_FOUND", "Active version artifacts missing.", status_code=500
                )
        else:
            yaml_content = yaml_src.read_text(encoding="utf-8")

        # Atomic binary write to target contract path avoiding Windows CRLF translation
        data = yaml_content.encode("utf-8")
        tmp_contract = target_ws / f".{target_contract_filename}.{uuid.uuid4().hex[:8]}.tmp"
        tmp_contract.write_bytes(data)
        os.replace(tmp_contract, target_contract_path)
        set_owner_only_permissions(target_contract_path)

        now_iso = datetime.now(UTC).isoformat()
        new_ws_sha256 = hashlib.sha256(target_contract_path.read_bytes()).hexdigest()

        # Record promotion in promotion.json atomically (MAJ-03, MIN-06)
        promo_file = sdir / "promotion.json"
        promo_data = {
            "promoted_at": now_iso,
            "target_contract_path": target_contract_rel,
            "workspace_sha256": new_ws_sha256,
            "promoted_version": meta.head_version,
            "promoted_by": principal.principal,
            "rationale": payload.rationale,
            "downgrade_acknowledged": bool(payload.acknowledge_historical_contract_warning),
            "historical_contract_rationale": payload.historical_contract_rationale,
        }
        tmp_promo = sdir / f".promotion.{uuid.uuid4().hex[:8]}.tmp"
        tmp_promo.write_text(json.dumps(promo_data, indent=2), encoding="utf-8")
        os.replace(tmp_promo, promo_file)
        set_owner_only_permissions(promo_file)

        # Audit ledger record
        audit_file = sdir / "audit.jsonl"
        audit_rec = {
            "action": "PROMOTE_SESSION",
            "timestamp": now_iso,
            "promoted_by": principal.principal,
            "promoted_version": meta.head_version,
            "workspace_sha256": new_ws_sha256,
            "rationale": payload.rationale,
        }
        with audit_file.open("a", encoding="utf-8") as af:
            af.write(json.dumps(audit_rec) + "\n")

    return PromoteResponse(
        session_id=session_id,
        promoted_version=meta.head_version,
        head_sha256=meta.head_sha256,
        workspace_sha256=new_ws_sha256,
        promoted_at=now_iso,
        target_contract_path=target_contract_rel,
    )


# ---------------------------------------------------------------------------
# Portability & Bundle Routes
# ---------------------------------------------------------------------------


@router.post("/sessions/{session_id}/bundle")
def export_bundle_endpoint(
    session_id: str,
    payload: BundleExportRequest,
    session_mgr: Annotated[StudioSessionManager, Depends(get_session_manager)],
    principal: Annotated[StudioPrincipal, Depends(require_principal)],
) -> Response:
    """Export full session into portable archive .odvstudio.zip."""
    _validate_session_param(session_id)
    sdir = session_mgr.get_session_dir(session_id)
    if not sdir.is_dir():
        raise StudioAPIError(
            "SESSION_NOT_FOUND", f"Session '{session_id}' not found.", status_code=404
        )

    # Confidential source check
    manifest_file = sdir / "inputs" / "manifest.json"
    has_confidential = False
    if manifest_file.is_file():
        try:
            mdata = json.loads(manifest_file.read_text(encoding="utf-8"))
            has_confidential = any(f.get("do_not_send_to_agent") for f in mdata.get("files", []))
        except (OSError, json.JSONDecodeError) as exc:
            raise StudioAPIError(
                "CORRUPT_SESSION_RECORD",
                f"Corrupt manifest file in session '{session_id}': {exc}",
                status_code=500,
            ) from exc

    if has_confidential and not payload.acknowledge_confidential_source_export:
        raise StudioAPIError(
            "CONFIDENTIAL_SOURCE_EXPORT_UNACKNOWLEDGED",
            "Session contains confidential sources. You must pass "
            "acknowledge_confidential_source_export=true.",
            status_code=422,
        )

    if has_confidential and not principal.is_admin:
        raise StudioAPIError(
            "FORBIDDEN_ADMIN_REQUIRED",
            "Exporting sessions with confidential sources requires administrator role.",
            status_code=403,
        )

    tmp_dir = session_mgr.studio_root / ".scratch"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp_out = tmp_dir / f".bundle_export_{uuid.uuid4().hex[:8]}.zip"
    try:
        export_bundle(sdir, tmp_out)
        zip_bytes = tmp_out.read_bytes()
    finally:
        if tmp_out.exists():
            tmp_out.unlink()

    return Response(
        content=zip_bytes,
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{session_id}.odvstudio.zip"'},
    )


@router.post("/sessions/bundle/import", response_model=StudioSessionDTO, status_code=201)
async def import_bundle_endpoint(
    file: UploadFile,
    session_mgr: Annotated[StudioSessionManager, Depends(get_session_manager)],
    principal: Annotated[StudioPrincipal, Depends(require_admin)],
    request: Request,
    acknowledge_unverified_import_attribution: bool = Query(default=False),
    acknowledgement_rationale: str | None = Query(default=None),
) -> StudioSessionDTO:
    """Streaming import of .odvstudio.zip archive with attribution governance."""
    has_ack = "acknowledge_unverified_import_attribution" in request.query_params
    if not acknowledge_unverified_import_attribution and has_ack:
        val = request.query_params.get("acknowledge_unverified_import_attribution", "").lower()
        acknowledge_unverified_import_attribution = val in ("true", "1", "yes")
    if acknowledgement_rationale is None and "acknowledgement_rationale" in request.query_params:
        acknowledgement_rationale = request.query_params.get("acknowledgement_rationale")

    scratch_dir = session_mgr.studio_root / ".scratch"
    scratch_dir.mkdir(parents=True, exist_ok=True)
    tmp_in = scratch_dir / f".import_{uuid.uuid4().hex[:8]}.zip"
    content = await file.read()
    tmp_in.write_bytes(content)

    try:
        meta = import_bundle(
            tmp_in,
            session_mgr.studio_root,
            security_manager=session_mgr.security_manager,
            acknowledge_unverified_import_attribution=acknowledge_unverified_import_attribution,
            acknowledgement_rationale=acknowledgement_rationale,
            imported_by=principal.principal,
        )
        return StudioSessionDTO(
            session_id=meta.session_id,
            domain_namespace=meta.domain_namespace,
            enterprise_owner=meta.enterprise_owner,
            status=meta.status,
            active_version=meta.head_version,
            head_sha256=meta.head_sha256,
            created_at=meta.created_at,
            updated_at=meta.updated_at,
        )
    except ImportGroundingRationaleRequiredError as exc:
        raise StudioAPIError(
            "IMPORT_GROUNDING_RATIONALE_REQUIRED",
            str(exc),
            status_code=400,
        ) from exc
    except ImportGroundingViolationError as exc:
        raise StudioAPIError(
            "IMPORT_GROUNDING_VIOLATION",
            str(exc),
            status_code=422,
        ) from exc
    except FileExistsError as exc:
        raise StudioAPIError(
            "SESSION_ALREADY_EXISTS",
            str(exc),
            status_code=409,
        ) from exc
    except ArchiveSecurityViolationError as exc:
        raise StudioAPIError("ARCHIVE_SECURITY_VIOLATION", str(exc), status_code=422) from exc
    except Exception as exc:
        err_msg = str(exc)
        if "ARCHIVE_SECURITY_VIOLATION" in err_msg:
            raise StudioAPIError("ARCHIVE_SECURITY_VIOLATION", err_msg, status_code=422) from exc
        if "IMPORT_DELTA_REPLAY_HASH_MISMATCH" in err_msg:
            raise StudioAPIError(
                "IMPORT_DELTA_REPLAY_HASH_MISMATCH", err_msg, status_code=422
            ) from exc
        raise StudioAPIError("EXECUTION_FAILURE", str(exc), status_code=400) from exc
    finally:
        if tmp_in.exists():
            tmp_in.unlink()


@router.post(
    "/sessions/{session_id}/bundle/clone", response_model=StudioSessionDTO, status_code=201
)
def clone_session_endpoint(
    session_id: str,
    session_mgr: Annotated[StudioSessionManager, Depends(get_session_manager)],
    _principal: Annotated[StudioPrincipal, Depends(require_principal)],
    payload: CloneSessionRequest | None = None,
) -> StudioSessionDTO:
    """Clone session into an isolated new session with new session ID under L5 lock."""
    _validate_session_param(session_id)
    sdir = session_mgr.get_session_dir(session_id)
    if not sdir.is_dir():
        raise StudioAPIError(
            "SESSION_NOT_FOUND", f"Session '{session_id}' not found.", status_code=404
        )

    new_sid = (
        payload.new_session_id
        if payload and payload.new_session_id
        else f"clone-{session_id}-{uuid.uuid4().hex[:6]}"
    )
    try:
        validate_session_id(new_sid)
    except InvalidSessionIdentifierError as exc:
        raise StudioAPIError(
            "SYNTAX_INVALID_ARGUMENT",
            f"Invalid destination session identifier '{new_sid}': {exc}",
            status_code=400,
        ) from exc

    new_dir = session_mgr.get_session_dir(new_sid)
    if new_dir.exists():
        raise StudioAPIError(
            "SESSION_ALREADY_EXISTS", f"Session '{new_sid}' already exists.", status_code=409
        )

    scratch_dir = session_mgr.studio_root / ".scratch"
    scratch_dir.mkdir(parents=True, exist_ok=True)
    staging_dir = scratch_dir / f".staging_clone_{uuid.uuid4().hex[:12]}"

    # Acquire L5 lock on source session before cloning (MAJ-04)
    with studio_lock(sdir / ".session.lock", level=L5_SESSION, session_id=session_id):
        try:
            shutil.copytree(
                sdir,
                staging_dir,
                ignore=shutil.ignore_patterns(
                    ".session.lock*",
                    "jobs",
                    "promotion.json",
                    ".scratch*",
                    ".staging*",
                    ".bundle*",
                ),
            )
            # Re-key session.json in private staging directory
            clone_meta_dict = json.loads((staging_dir / "session.json").read_text(encoding="utf-8"))
            now_iso = datetime.now(UTC).isoformat()
            clone_meta_dict["session_id"] = new_sid
            clone_meta_dict["created_at"] = now_iso
            clone_meta_dict["updated_at"] = now_iso
            (staging_dir / "session.json").write_text(
                json.dumps(clone_meta_dict, indent=2), encoding="utf-8"
            )
            set_owner_only_permissions(staging_dir / "session.json")

            # Atomically claim new_dir exclusively via mkdir (MIN-05)
            try:
                new_dir.mkdir(parents=True, exist_ok=False)
            except FileExistsError as exc:
                raise StudioAPIError(
                    "SESSION_ALREADY_EXISTS",
                    f"Session '{new_sid}' already exists.",
                    status_code=409,
                ) from exc

            # Re-key session_id in copied proposals
            proposals_dir = staging_dir / "proposals"
            if proposals_dir.is_dir():
                for prop_f in proposals_dir.glob("*.json"):
                    try:
                        p_data = json.loads(prop_f.read_text(encoding="utf-8"))
                        p_data["session_id"] = new_sid
                        prop_f.write_text(json.dumps(p_data, indent=2), encoding="utf-8")
                    except (OSError, json.JSONDecodeError):
                        pass

            # Move staged contents directly into the claimed new_dir
            for item in list(staging_dir.iterdir()):
                shutil.move(str(item), str(new_dir / item.name))
            shutil.rmtree(staging_dir, ignore_errors=True)
            set_owner_only_permissions(new_dir)
        except Exception as exc:
            if staging_dir.exists():
                shutil.rmtree(staging_dir, ignore_errors=True)
            if new_dir.is_dir() and not (new_dir / "session.json").exists():
                shutil.rmtree(new_dir, ignore_errors=True)
            if isinstance(exc, StudioAPIError):
                raise
            raise StudioAPIError(
                "INTERNAL_CLONE_ERROR",
                f"Failed to clone session '{session_id}': {exc}",
                status_code=500,
            ) from exc

    meta = session_mgr.get_session(new_sid)
    return StudioSessionDTO(
        session_id=meta.session_id,
        domain_namespace=meta.domain_namespace,
        enterprise_owner=meta.enterprise_owner,
        status=meta.status,
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
    _principal: Annotated[StudioPrincipal, Depends(require_principal)],
) -> LaunchNonceResponse:
    """Mint single-use browser launch nonce (256-bit, 60s TTL) for local Web Workbench bootstrap."""
    server_mode = get_server_mode(request)
    if server_mode == SERVER_MODE_REMOTE:
        raise StudioAPIError(
            "LOOPBACK_PRIVILEGE_DISABLED_IN_REMOTE_MODE",
            "Launch nonce bootstrap is forbidden in remote_platform mode.",
            status_code=403,
        )

    if not is_loopback_client(request):
        raise StudioAPIError(
            "LOOPBACK_PRIVILEGE_DISABLED_IN_REMOTE_MODE",
            "Minting launch nonce is strictly restricted to loopback callers.",
            status_code=403,
            instance=str(request.url.path),
        )

    if _principal.principal.startswith("agent:"):
        raise StudioAPIError(
            "AGENT_LAUNCH_NONCE_FORBIDDEN",
            "Automated agents are strictly forbidden from minting browser launch nonces.",
            status_code=403,
            instance=str(request.url.path),
        )

    nonce = mint_launch_nonce(principal=_principal.principal, ttl=60.0)
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
            status_code=403,
        )

    if not is_loopback_client(request):
        raise StudioAPIError(
            "LOOPBACK_PRIVILEGE_DISABLED_IN_REMOTE_MODE",
            "Nonce exchange is strictly restricted to loopback callers.",
            status_code=403,
            instance=str(request.url.path),
        )

    raw_nonce = payload.launch_nonce or payload.nonce
    if not raw_nonce:
        raise StudioAPIError(
            "SYNTAX_INVALID_ARGUMENT", "Missing required 'nonce' field.", status_code=400
        )

    session_token, principal = exchange_launch_nonce(str(raw_nonce))

    response.set_cookie(
        key="opendv_session",
        value=session_token,
        httponly=True,
        samesite="strict",
        secure=False,
    )
    return TokenExchangeResponse(
        status="authenticated",
        principal=principal,
    )


# ---------------------------------------------------------------------------
# Parameter Validation Helper
# ---------------------------------------------------------------------------


_PROPOSAL_ID_PATTERN: re.Pattern[str] = re.compile(r"^prop-[a-zA-Z0-9_-]{4,64}$")
_JOB_ID_PATTERN: re.Pattern[str] = re.compile(r"^job-[a-zA-Z0-9_-]{4,64}$")
_VERSION_PATTERN: re.Pattern[str] = re.compile(
    r"^(?:v)?[0-9]+\.[0-9]+\.[0-9]+(?:-[a-zA-Z0-9_.-]+)?$"
)


def _validate_session_param(session_id: str) -> None:
    try:
        validate_session_id(session_id)
    except InvalidSessionIdentifierError as exc:
        raise StudioAPIError("INVALID_SESSION_IDENTIFIER", str(exc), status_code=400) from exc


def _validate_proposal_id_param(proposal_id: str) -> None:
    if not isinstance(proposal_id, str) or not _PROPOSAL_ID_PATTERN.match(proposal_id):
        raise StudioAPIError(
            "SYNTAX_INVALID_ARGUMENT",
            f"Invalid proposal identifier '{proposal_id}'.",
            status_code=400,
        )


def _validate_job_id_param(job_id: str) -> None:
    if not isinstance(job_id, str) or not _JOB_ID_PATTERN.match(job_id):
        raise StudioAPIError(
            "SYNTAX_INVALID_ARGUMENT",
            f"Invalid job identifier '{job_id}'.",
            status_code=400,
        )


def _validate_version_param(version: str | None) -> None:
    if version is not None and (
        not isinstance(version, str) or not _VERSION_PATTERN.match(version)
    ):
        raise StudioAPIError(
            "SYNTAX_INVALID_ARGUMENT",
            f"Invalid version identifier '{version}'.",
            status_code=400,
        )


def _resolve_proposal_file(sdir: Path, proposal_id: str) -> Path:
    _validate_proposal_id_param(proposal_id)
    proposals_dir = (sdir / "proposals").resolve()
    target_file = (proposals_dir / f"{proposal_id}.json").resolve()
    if not target_file.is_relative_to(proposals_dir):
        raise StudioAPIError(
            "INVALID_WORKSPACE_PATH",
            f"Path traversal detected in proposal identifier '{proposal_id}'.",
            status_code=400,
        )
    return target_file


__all__ = [
    "router",
]
