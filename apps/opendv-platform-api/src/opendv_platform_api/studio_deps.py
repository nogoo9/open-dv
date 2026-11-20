"""Shared FastAPI dependencies, RBAC guards, and validation helpers for Ontology Studio."""

from __future__ import annotations

import contextlib
import os
import re
import secrets
import sys
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Annotated, Any, Final

from fastapi import Depends, Request, UploadFile

from opendv_contracts.errors import ErrorCode, OpenDVError
from opendv_platform_api.deps import get_opendv_service
from opendv_platform_api.job_supervisor import get_job_supervisor
from opendv_platform_api.security.csrf import verify_csrf
from opendv_platform_api.security.error_catalog import StudioAPIError
from opendv_platform_api.security.studio_auth import StudioPrincipal, authenticate_studio_request
from opendv_service.studio.atomic_commit import parse_strong_etag
from opendv_service.studio.delta_normalizer import StudioWorkflowError
from opendv_service.studio.session_manager import (
    InvalidSessionIdentifierError,
    SessionMetadata,
    SessionNotFoundError,
    StudioSessionManager,
    validate_session_id,
)
from opendv_service.studio.workspace_security import (
    WINDOWS_RESERVED_DEVICES,
    set_owner_only_permissions,
)

UPLOAD_STREAM_CHUNK_BYTES: Final[int] = 64 * 1024  # 64 KiB
MAX_SOURCE_UPLOAD_BYTES: Final[int] = 100 * 1024 * 1024  # 100 MiB
MAX_BUNDLE_IMPORT_BYTES: Final[int] = 500 * 1024 * 1024  # 500 MiB

_PROPOSAL_ID_PATTERN: re.Pattern[str] = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
_JOB_ID_PATTERN: re.Pattern[str] = re.compile(r"^job-[a-zA-Z0-9_-]{4,64}$")
_VERSION_PATTERN: re.Pattern[str] = re.compile(
    r"^(?:v)?[0-9]+\.[0-9]+\.[0-9]+(?:-[a-zA-Z0-9_.-]+)?$"
)


def get_effective_max_source_upload_bytes() -> int:
    """Resolve upload cap respecting monkeypatches on routers.ontology_studio."""
    facade = sys.modules.get("opendv_platform_api.routers.ontology_studio")
    if facade is not None and hasattr(facade, "MAX_SOURCE_UPLOAD_BYTES"):
        return int(facade.MAX_SOURCE_UPLOAD_BYTES)
    return MAX_SOURCE_UPLOAD_BYTES


def get_effective_max_bundle_import_bytes() -> int:
    """Resolve bundle import cap respecting monkeypatches on routers.ontology_studio."""
    facade = sys.modules.get("opendv_platform_api.routers.ontology_studio")
    if facade is not None and hasattr(facade, "MAX_BUNDLE_IMPORT_BYTES"):
        return int(facade.MAX_BUNDLE_IMPORT_BYTES)
    return MAX_BUNDLE_IMPORT_BYTES


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


def _resolve_allowed_workspace_roots(session_mgr: StudioSessionManager) -> list[Path]:
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


require_principal = require_viewer

MgrDep = Annotated[StudioSessionManager, Depends(get_session_manager)]
ViewerDep = Annotated[StudioPrincipal, Depends(require_viewer)]
EditorDep = Annotated[StudioPrincipal, Depends(require_editor)]
EdAutoDep = Annotated[StudioPrincipal, Depends(require_editor_or_automated)]
AdminDep = Annotated[StudioPrincipal, Depends(require_admin)]


def raise_from_opendv_error(exc: OpenDVError) -> StudioAPIError:
    """Convert a domain OpenDVError into a canonical catalog StudioAPIError."""
    raw_code = getattr(exc, "error_code", ErrorCode.INTERNAL_ERROR)
    code_str = raw_code.value if hasattr(raw_code, "value") else str(raw_code)
    details = getattr(exc, "details", None)
    extra = dict(details) if isinstance(details, dict) and details else None
    return StudioAPIError(
        code_str,
        str(getattr(exc, "message", None) or str(exc)),
        status_code=getattr(exc, "http_status", None),
        extensions=extra,
    )


@contextlib.contextmanager
def translate_studio_errors() -> Any:
    """Translate StudioWorkflowError/OpenDVError into canonical StudioAPIError."""
    try:
        yield
    except StudioWorkflowError as exc:
        raise raise_from_opendv_error(exc) from exc


def get_session_or_404(
    session_mgr: StudioSessionManager,
    session_id: str,
) -> SessionMetadata:
    validate_session_param(session_id)
    try:
        return session_mgr.get_session(session_id)
    except SessionNotFoundError as exc:
        raise StudioAPIError("SESSION_NOT_FOUND", str(exc)) from exc


def derive_session_status(sdir: Path, meta: SessionMetadata) -> str:
    """Return 'PROMOTED' when session has active promotion metadata, else meta.status."""
    if meta.status == "ACTIVE" and ((sdir / "promotion.json").is_file() or meta.promoted_versions):
        return "PROMOTED"
    return meta.status


def parse_strong_etag_or_400(raw_etag: str, header_name: str = "If-Match") -> str:
    """Parse strong 64-hex ETag or raise 400 INVALID_PRECONDITION_HEADER."""
    try:
        return parse_strong_etag(raw_etag)
    except OpenDVError as exc:
        raise StudioAPIError(
            "INVALID_PRECONDITION_HEADER",
            f"Invalid {header_name} precondition '{raw_etag}': {exc}",
        ) from exc


async def stream_upload_to_tempfile(
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


def validate_session_param(session_id: str) -> None:
    try:
        validate_session_id(session_id)
    except InvalidSessionIdentifierError as exc:
        raise StudioAPIError("INVALID_SESSION_IDENTIFIER", str(exc)) from exc


def validate_proposal_id_param(proposal_id: str) -> None:
    if (
        not isinstance(proposal_id, str)
        or not _PROPOSAL_ID_PATTERN.match(proposal_id)
        or proposal_id.lower() in WINDOWS_RESERVED_DEVICES
    ):
        raise StudioAPIError(
            "SYNTAX_INVALID_ARGUMENT",
            f"Invalid proposal identifier '{proposal_id}'.",
        )


def validate_job_id_param(job_id: str) -> None:
    if (
        not isinstance(job_id, str)
        or not _JOB_ID_PATTERN.match(job_id)
        or job_id.lower() in WINDOWS_RESERVED_DEVICES
    ):
        raise StudioAPIError(
            "SYNTAX_INVALID_ARGUMENT",
            f"Invalid job identifier '{job_id}'.",
        )


def validate_version_param(version: str | None) -> None:
    if version is not None and (
        not isinstance(version, str) or not _VERSION_PATTERN.match(version)
    ):
        raise StudioAPIError(
            "SYNTAX_INVALID_ARGUMENT",
            f"Invalid version identifier '{version}'.",
        )


def resolve_proposal_file(sdir: Path, proposal_id: str) -> Path:
    validate_proposal_id_param(proposal_id)
    proposals_dir = (sdir / "proposals").resolve()
    target_file = (proposals_dir / f"{proposal_id}.json").resolve()
    if not target_file.is_relative_to(proposals_dir):
        raise StudioAPIError(
            "INVALID_WORKSPACE_PATH",
            f"Path traversal detected in proposal identifier '{proposal_id}'.",
        )
    return target_file


def has_active_pipeline_run(platform_state: Any | None, session_id: str) -> bool:
    """Return True if `platform_state.runs` has an active pipeline run for `session_id`."""
    if platform_state is None or not session_id or not hasattr(platform_state, "runs"):
        return False
    lock = getattr(platform_state, "_state_lock", None)
    with lock if lock is not None else contextlib.nullcontext():
        runs_snapshot = list(platform_state.runs.values())
        run_session_ids = dict(getattr(platform_state, "run_session_ids", {}) or {})
    for r in runs_snapshot:
        st = str(getattr(getattr(r, "status", None), "value", getattr(r, "status", ""))).upper()
        if st not in {"PENDING", "QUEUED", "RUNNING"}:
            continue
        rid = getattr(r, "run_id", None)
        rsid = getattr(r, "session_id", None) or (run_session_ids.get(rid) if rid else None)
        if rsid == session_id:
            return True
    return False


def ensure_no_active_session_job(
    session_mgr: StudioSessionManager,
    session_id: str,
    platform_state: Any | None = None,
) -> None:
    """Enforce single active job per session (`409 SESSION_JOB_ACTIVE`, `D27`)."""
    active_jobs = get_job_supervisor().count_active_jobs(session_mgr, session_id)
    if active_jobs > 0 or has_active_pipeline_run(platform_state, session_id):
        raise StudioAPIError(
            "SESSION_JOB_ACTIVE",
            f"Session '{session_id}' already has an active running job.",
            status_code=409,
        )


@contextlib.contextmanager
def reserve_session_run(service: Any, session_id: Any | None) -> Iterator[None]:
    """Atomically enforce D27 single active job/run per session while the body registers a run."""
    sid_str = str(session_id or "").strip()
    if not sid_str:
        yield
        return
    from opendv_service.studio.workspace_security import DEFAULT_STUDIO_DIR

    studio_env = os.environ.get("OPENDV_STUDIO_ROOT", "").strip()
    studio_root = (
        Path(studio_env).resolve()
        if studio_env
        else (service.workspace_root / DEFAULT_STUDIO_DIR).resolve()
    )
    smgr = StudioSessionManager(studio_root=studio_root, workspace_root=service.workspace_root)
    with get_job_supervisor().reserve_run_session(
        smgr, sid_str, lambda: has_active_pipeline_run(service.state, sid_str)
    ):
        yield


def resolve_server_allowed_hosts(service: Any) -> list[str] | None:
    """Resolve SSRF host allowlist strictly from server settings or env vars."""
    raw_hosts = getattr(getattr(service, "settings", None), "allowed_hosts", None)
    if raw_hosts is None:
        raw_hosts = os.environ.get("OPENDV_ALLOWED_HOSTS") or os.environ.get(
            "OPENDV_ALLOWED_DATABRICKS_HOSTS"
        )
    if isinstance(raw_hosts, str):
        return [h.strip().lower() for h in raw_hosts.split(",") if h.strip()]
    if isinstance(raw_hosts, (list, set, tuple)):
        return [str(h).strip().lower() for h in raw_hosts if str(h).strip()]
    return None


def resolve_platform_ledger_dirs(request: Request) -> tuple[Path, ...]:
    """Return active PlatformStateContainer.ledger_dir (if present) for session log discovery."""
    pstate = getattr(request.app.state, "platform_state", None)
    if pstate is None and getattr(request.app.state, "service", None) is not None:
        pstate = getattr(request.app.state.service, "state", None)
    ldir = getattr(pstate, "ledger_dir", None)
    return (Path(ldir),) if ldir else ()


def session_logs_activity_hooks(
    session_mgr: StudioSessionManager,
    session_id: str,
    platform_state: Any | None = None,
) -> tuple[Callable[[], bool], Callable[[], str]]:
    """Build is_active and terminal_status callables across Studio jobs and linked pipeline runs."""
    from opendv_service.studio.wizard_workflows import (
        collect_disk_job_records,
        normalize_iso_utc,
        resolve_latest_terminal_status,
    )

    supervisor = get_job_supervisor()

    def _run_session_id(r: Any) -> str | None:
        rid = getattr(r, "run_id", None)
        lock = getattr(platform_state, "_state_lock", None)
        with lock if lock is not None else contextlib.nullcontext():
            run_session_ids = getattr(platform_state, "run_session_ids", None)
            if rid and isinstance(run_session_ids, dict) and run_session_ids.get(rid):
                return str(run_session_ids[rid])
        metrics = getattr(r, "summary_metrics", None) or {}
        rsid = getattr(r, "session_id", None) or (
            metrics.get("session_id") if isinstance(metrics, dict) else None
        )
        if rsid:
            return str(rsid)
        with lock if lock is not None else contextlib.nullcontext():
            run_events = getattr(platform_state, "run_events", None)
            events_snapshot = (
                list(run_events.get(rid, ())) if rid and isinstance(run_events, dict) else []
            )
        for ev in events_snapshot:
            if not isinstance(ev, dict):
                continue
            l2 = (
                ev.get("layer2_visual_payload")
                if isinstance(ev.get("layer2_visual_payload"), dict)
                else {}
            )
            l2_m = l2.get("summary_metrics") if isinstance(l2.get("summary_metrics"), dict) else {}
            top_m = ev.get("summary_metrics") if isinstance(ev.get("summary_metrics"), dict) else {}
            ev_sid = ev.get("session_id") or l2_m.get("session_id") or top_m.get("session_id")
            if ev_sid:
                return str(ev_sid)
        return None

    def _linked_runs() -> list[Any]:
        if platform_state is None or not hasattr(platform_state, "runs"):
            return []
        lock = getattr(platform_state, "_state_lock", None)
        with lock if lock is not None else contextlib.nullcontext():
            runs_snapshot = list(platform_state.runs.values())
        return [r for r in runs_snapshot if _run_session_id(r) == session_id]

    def _is_active() -> bool:
        if supervisor.count_active_jobs(session_mgr, session_id) > 0:
            return True
        for r in _linked_runs():
            st = str(getattr(getattr(r, "status", None), "value", getattr(r, "status", ""))).upper()
            if st in {"PENDING", "QUEUED", "RUNNING"}:
                return True
        return False

    def _terminal_status() -> str:
        records = collect_disk_job_records(session_mgr, session_id)
        for r in _linked_runs():
            st = str(getattr(getattr(r, "status", None), "value", getattr(r, "status", ""))).upper()
            if st:
                key = (
                    normalize_iso_utc(getattr(r, "started_at_utc", "")),
                    normalize_iso_utc(getattr(r, "completed_at_utc", "")),
                    0,
                    str(getattr(r, "run_id", "") or ""),
                )
                records.append((key, st))
        return resolve_latest_terminal_status(records)

    return _is_active, _terminal_status


__all__ = [
    "MAX_BUNDLE_IMPORT_BYTES",
    "MAX_SOURCE_UPLOAD_BYTES",
    "UPLOAD_STREAM_CHUNK_BYTES",
    "AdminDep",
    "EdAutoDep",
    "EditorDep",
    "MgrDep",
    "ViewerDep",
    "_resolve_allowed_workspace_roots",
    "_resolve_deployment_workspace_root",
    "derive_session_status",
    "ensure_no_active_session_job",
    "get_effective_max_bundle_import_bytes",
    "get_effective_max_source_upload_bytes",
    "get_job_supervisor",
    "get_session_manager",
    "get_session_or_404",
    "get_studio_root",
    "has_active_pipeline_run",
    "parse_strong_etag_or_400",
    "raise_from_opendv_error",
    "require_admin",
    "require_editor",
    "require_editor_or_automated",
    "require_principal",
    "require_viewer",
    "reserve_session_run",
    "resolve_platform_ledger_dirs",
    "resolve_proposal_file",
    "resolve_server_allowed_hosts",
    "session_logs_activity_hooks",
    "stream_upload_to_tempfile",
    "translate_studio_errors",
    "validate_job_id_param",
    "validate_proposal_id_param",
    "validate_session_param",
    "validate_version_param",
]
