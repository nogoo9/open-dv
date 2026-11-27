"""Shared dependency accessors, workspace containment, and dual JSON/multipart parsers."""

from __future__ import annotations

import contextlib
import os
import re
import tempfile
import uuid
from pathlib import Path
from typing import Literal, cast

from fastapi import Request, UploadFile

from opendv.models.errors import ErrorCode, OpenDVError
from opendv.models.project import WINDOWS_RESERVED_NAMES
from opendv.models.sources import SourceSummary
from opendv.models.wire_dtos_discovery import ProjectImportPathReq, SourceReattachReq
from opendv.service.app_service import OpenDVService
from opendv.service.bundles.zip_guard import (
    reject_symlinks_on_path_and_ancestors,
    safe_open_no_follow,
)
from opendv.service.os_platform import atomic_write_bytes, validate_workspace_containment

from ._security import (
    MAX_REQUEST_BODY_BYTES,
    is_remote_request,
    parse_env_workspace_roots,
)
from ._tasks import RunTaskManager

_DRIVE_PREFIX_RE = re.compile(r"^[A-Za-z]:")


def get_service(request: Request) -> OpenDVService:
    """Retrieve the OpenDVService instance bound to app.state."""
    return cast(OpenDVService, request.app.state.service)


def get_task_manager(request: Request) -> RunTaskManager:
    """Retrieve the RunTaskManager instance bound to app.state."""
    return cast(RunTaskManager, request.app.state.task_manager)


def enforce_api_workspace_path(
    request: Request,
    service: OpenDVService,
    raw_path: str,
) -> str:
    """Enforce OPENDV_WORKSPACE_ROOTS and is_remote_request for local source paths."""
    env_roots = parse_env_workspace_roots()
    remote = is_remote_request(request)
    if remote and env_roots is None:
        raise OpenDVError(
            "Server filesystem paths are forbidden on remote requests.",
            error_code=ErrorCode.LOOPBACK_ORIGIN_FORBIDDEN,
        )
    if env_roots is not None:
        stripped = raw_path.strip()
        if not stripped:
            raise OpenDVError(
                "Source path cannot be empty.",
                error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
            )
        cand = Path(stripped)
        if not cand.is_absolute():
            cand = service.workspace_root / cand
        resolved = validate_workspace_containment(
            cand,
            allowed_roots=env_roots,
            is_remote_request=False,
        )
        return str(resolved)
    return raw_path


def enforce_grounding_asset_path(
    request: Request,
    service: OpenDVService,
    raw_path: str,
    *,
    default_roots: tuple[Path, ...] | None = None,
) -> tuple[Path, Path]:
    """Validate grounding asset path (UNC/ADS/containment/symlinks) returning (path, root)."""
    if is_remote_request(request):
        raise OpenDVError(
            "Server filesystem asset paths are forbidden on remote requests.",
            error_code=ErrorCode.LOOPBACK_ORIGIN_FORBIDDEN,
        )
    stripped = raw_path.strip()
    if (
        not stripped
        or "\x00" in raw_path
        or any(ord(ch) < 32 or ord(ch) == 127 for ch in raw_path)
        or raw_path.startswith(("\\\\", "//"))
        or stripped.startswith(("\\\\", "//"))
    ):
        raise OpenDVError(
            "Invalid or forbidden grounding asset path.",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        )
    has_drive_prefix = bool(_DRIVE_PREFIX_RE.match(stripped))
    if has_drive_prefix and (len(stripped) < 3 or stripped[2] not in ("/", "\\")):
        raise OpenDVError(
            f"Drive-relative path is forbidden in asset path: '{raw_path}'.",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        )
    remainder = stripped[2:] if has_drive_prefix else stripped
    if ":" in remainder:
        raise OpenDVError(
            f"Alternate Data Stream ':' is forbidden in asset path: '{raw_path}'.",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        )
    for seg in remainder.replace("\\", "/").split("/"):
        if not seg:
            continue
        if seg == "..":
            raise OpenDVError(
                f"Path traversal '..' is forbidden in asset path: '{raw_path}'.",
                error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
            )
        stem = seg.split(".", 1)[0].lower()
        if seg.lower() in WINDOWS_RESERVED_NAMES or stem in WINDOWS_RESERVED_NAMES:
            raise OpenDVError(
                f"Reserved device name forbidden in asset path: '{raw_path}'.",
                error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
            )

    cand = Path(stripped)
    if not cand.is_absolute():
        cand = service.workspace_root / cand
    env_roots = parse_env_workspace_roots()
    if env_roots is not None:
        roots = tuple(env_roots)
    elif default_roots is not None:
        roots = default_roots
    else:
        raw_ws = getattr(service, "raw_workspace_root", service.workspace_root)
        packs_root = service.state_dir / "packs" / "fibo"
        default_list: list[Path] = [raw_ws]
        if os.path.normcase(str(service.workspace_root)) != os.path.normcase(str(raw_ws)):
            default_list.append(service.workspace_root)
        default_list.append(packs_root)
        roots = tuple(default_list)
    cand_real = os.path.realpath(str(cand))
    if cand_real.startswith("\\\\?\\UNC\\") or cand_real.startswith("\\\\"):
        raise OpenDVError(
            f"UNC asset path rejected after resolution: '{raw_path}'.",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        )

    matched: tuple[Path, Path] | None = None
    cand_real_norm = os.path.normcase(cand_real)
    for raw_root in roots:
        resolved_root = raw_root.resolve()
        root_norm = os.path.normcase(str(resolved_root))
        try:
            if os.path.commonpath([cand_real_norm, root_norm]) == root_norm:
                matched = (raw_root, resolved_root)
                break
        except ValueError:
            continue
    if matched is None:
        raise OpenDVError(
            f"Grounding asset path '{raw_path}' escapes allowed containment roots.",
            error_code=ErrorCode.LOOPBACK_ORIGIN_FORBIDDEN,
        )
    raw_matched_root, resolved_matched_root = matched

    cand_abs_str = os.path.normpath(os.path.abspath(str(cand)))
    raw_root_str = os.path.normpath(os.path.abspath(str(raw_matched_root)))
    resolved_root_str = os.path.normpath(os.path.realpath(str(resolved_matched_root)))
    if os.name == "nt":
        if len(cand_abs_str) >= 2 and cand_abs_str[1] == ":":
            cand_abs_str = cand_abs_str[0].upper() + cand_abs_str[1:]
        if len(raw_root_str) >= 2 and raw_root_str[1] == ":":
            raw_root_str = raw_root_str[0].upper() + raw_root_str[1:]
        if len(resolved_root_str) >= 2 and resolved_root_str[1] == ":":
            resolved_root_str = resolved_root_str[0].upper() + resolved_root_str[1:]
    raw_prefix = raw_root_str if raw_root_str.endswith(os.sep) else raw_root_str + os.sep
    res_prefix = (
        resolved_root_str if resolved_root_str.endswith(os.sep) else resolved_root_str + os.sep
    )
    if not (
        cand_abs_str in (raw_root_str, resolved_root_str)
        or cand_abs_str.startswith(raw_prefix)
        or cand_abs_str.startswith(res_prefix)
    ):
        raise OpenDVError(
            f"Grounding asset path '{raw_path}' escapes allowed containment roots.",
            error_code=ErrorCode.LOOPBACK_ORIGIN_FORBIDDEN,
        )
    cand_abs = Path(cand_abs_str)
    raw_root_abs = Path(raw_root_str)
    try:
        reject_symlinks_on_path_and_ancestors(
            cand_abs, stop_roots=[raw_matched_root, resolved_matched_root]
        )
    except OpenDVError as exc:
        if exc.error_code == ErrorCode.ARCHIVE_SECURITY_VIOLATION:
            raise OpenDVError(exc.message, error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT) from exc
        raise

    if cand_abs_str == raw_root_str or cand_abs_str.startswith(raw_prefix):
        suffix_parts = cand_abs.parts[len(raw_root_abs.parts) :]
        resolved_path = Path(resolved_root_str).joinpath(*suffix_parts)
    else:
        resolved_path = Path(cand_real)
    resolved_str = os.path.normpath(os.path.abspath(str(resolved_path)))
    if os.name == "nt" and len(resolved_str) >= 2 and resolved_str[1] == ":":
        resolved_str = resolved_str[0].upper() + resolved_str[1:]
    if resolved_str != resolved_root_str and not resolved_str.startswith(res_prefix):
        raise OpenDVError(
            f"Grounding asset path '{raw_path}' escapes allowed containment roots.",
            error_code=ErrorCode.LOOPBACK_ORIGIN_FORBIDDEN,
        )
    return Path(resolved_str), Path(resolved_root_str)


def read_grounding_asset_bytes(
    resolved_path: Path,
    resolved_matched_root: Path,
    *,
    max_bytes: int,
    label: str,
) -> bytes:
    """Open verified grounding asset via O_NOFOLLOW and read bounded bytes."""
    safe_root = os.path.normpath(os.path.realpath(str(resolved_matched_root)))
    safe_target = os.path.normpath(os.path.abspath(str(resolved_path)))
    if os.name == "nt":
        if len(safe_root) >= 2 and safe_root[1] == ":":
            safe_root = safe_root[0].upper() + safe_root[1:]
        if len(safe_target) >= 2 and safe_target[1] == ":":
            safe_target = safe_target[0].upper() + safe_target[1:]
    safe_root_prefix = safe_root if safe_root.endswith(os.sep) else safe_root + os.sep
    if safe_target != safe_root and not safe_target.startswith(safe_root_prefix):
        raise OpenDVError(
            f"{label} path escapes allowed containment root.",
            error_code=ErrorCode.LOOPBACK_ORIGIN_FORBIDDEN,
        )
    try:
        fd = safe_open_no_follow(
            Path(safe_target), os.O_RDONLY, stop_roots=[Path(safe_root)]
        )
    except FileNotFoundError as exc:
        raise OpenDVError(
            f"{label} file not found: '{resolved_path.name}'.",
            error_code=ErrorCode.RESOURCE_NOT_FOUND,
        ) from exc
    except OpenDVError as exc:
        if exc.error_code == ErrorCode.ARCHIVE_SECURITY_VIOLATION:
            raise OpenDVError(exc.message, error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT) from exc
        raise
    with os.fdopen(fd, "rb") as fh:
        raw_bytes = fh.read(max_bytes + 1)
    if len(raw_bytes) > max_bytes:
        raise OpenDVError(
            f"{label} file exceeds maximum size of {max_bytes} bytes.",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        )
    return raw_bytes


def enforce_import_containment(
    request: Request,
    service: OpenDVService,
    bundle_path: str,
) -> tuple[Path, list[Path] | None, bool]:
    """Resolve import bundle path and allowed_roots under OPENDV_WORKSPACE_ROOTS."""
    env_roots = parse_env_workspace_roots()
    remote = is_remote_request(request)
    cand = Path(bundle_path)
    if not cand.is_absolute():
        cand = service.workspace_root / cand
    if env_roots is not None:
        resolved_roots = [Path(os.fspath(r)) for r in env_roots]
        return cand, resolved_roots, False
    return cand, None, remote


async def read_upload_bounded(
    upload: UploadFile,
    *,
    max_bytes: int = MAX_REQUEST_BODY_BYTES,
) -> bytes:
    """Read an UploadFile up to max_bytes, raising 413 ARCHIVE_SIZE_LIMIT_EXCEEDED if exceeded."""
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await upload.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise OpenDVError(
                f"Uploaded payload exceeds {max_bytes} byte limit.",
                error_code=ErrorCode.ARCHIVE_SIZE_LIMIT_EXCEEDED,
            )
        chunks.append(chunk)
    return b"".join(chunks)


def _parse_bool_form(raw: object, *, default: bool = False) -> bool:
    if raw is None:
        return default
    if isinstance(raw, bool):
        return raw
    val = str(raw).strip().lower()
    if val in {"1", "true", "yes", "on"}:
        return True
    if val in {"0", "false", "no", "off", ""}:
        return False
    raise OpenDVError(
        f"Invalid boolean form value '{raw}'.",
        error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
    )


async def handle_project_import(
    request: Request,
    service: OpenDVService,
) -> tuple[
    Path,
    Literal["fail", "rename", "replace"],
    bool,
    Path | None,
    list[Path] | None,
    bool,
    bool,
]:
    """Parse JSON ProjectImportPathReq or multipart .opendv.zip upload for /projects/import."""
    media_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if media_type == "application/json":
        raw_body = await request.body()
        req_dto = ProjectImportPathReq.model_validate_json(raw_body)
        bpath, allowed_roots, is_remote = enforce_import_containment(
            request, service, req_dto.bundle_path
        )
        return (
            bpath,
            req_dto.conflict,
            req_dto.trust_workspace,
            None,
            allowed_roots,
            is_remote,
            True,
        )

    if media_type != "multipart/form-data":
        raise OpenDVError(
            "Import requires application/json or multipart/form-data.",
            error_code=ErrorCode.UNSUPPORTED_MEDIA_TYPE,
        )

    form = await request.form()
    upload_obj = form.get("file")
    if upload_obj is None or not hasattr(upload_obj, "read"):
        raise OpenDVError(
            "Multipart import requires a 'file' upload part.",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        )
    upload = cast(UploadFile, upload_obj)
    conflict_raw = str(form.get("conflict") or "fail").strip()
    if conflict_raw not in ("fail", "rename", "replace"):
        raise OpenDVError(
            f"Invalid conflict policy '{conflict_raw}'.",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        )
    conflict = cast(Literal["fail", "rename", "replace"], conflict_raw)
    trust_ws = _parse_bool_form(form.get("trust_workspace"), default=False)
    content = await read_upload_bounded(upload)
    tmp_dir = service.state_manager.opendv_home / "tmp"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix="opendv_import_", suffix=".opendv.zip", dir=tmp_dir)
    os.close(fd)
    tmp_path = Path(tmp_name)
    atomic_write_bytes(tmp_path, content)
    return tmp_path, conflict, trust_ws, tmp_path, None, False, False


async def handle_source_reattach(
    request: Request,
    service: OpenDVService,
    project_id: str,
    source_id: str,
) -> SourceSummary:
    """Handle dual JSON SourceReattachReq or multipart upload PATCH for /sources/{sid}."""
    media_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    sources = service.list_sources(project_id)
    existing = next((s for s in sources if s.source_id == source_id), None)
    if existing is None:
        raise OpenDVError(
            f"Source '{source_id}' not found in project '{project_id}'.",
            error_code=ErrorCode.RESOURCE_NOT_FOUND,
        )

    if media_type == "application/json":
        req_dto = SourceReattachReq.model_validate_json(await request.body())
        if req_dto.uri_or_filename is not None and existing.kind != "databricks_uc":
            checked = enforce_api_workspace_path(request, service, req_dto.uri_or_filename)
            req_dto = req_dto.model_copy(update={"uri_or_filename": checked})
        return service.reattach_source(project_id, source_id, req_dto)

    if media_type != "multipart/form-data":
        raise OpenDVError(
            "Source reattach requires application/json or multipart/form-data.",
            error_code=ErrorCode.UNSUPPORTED_MEDIA_TYPE,
        )

    form = await request.form()
    upload_obj = form.get("file")
    if upload_obj is None or not hasattr(upload_obj, "read"):
        raise OpenDVError(
            "Multipart source reattach requires a 'file' upload part.",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        )
    upload = cast(UploadFile, upload_obj)
    safe_name = Path(upload.filename or "reattached_source").name
    if not safe_name or safe_name in {".", ".."}:
        raise OpenDVError(
            f"Invalid uploaded filename '{upload.filename}'.",
            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
        )
    content = await read_upload_bounded(upload)
    src_dir = service.state_manager.sources_dir(project_id) / source_id
    stage_dir = src_dir / f"_reattach_{uuid.uuid4().hex[:6]}"
    stage_dir.mkdir(parents=True, exist_ok=True)
    dest = validate_workspace_containment(stage_dir / safe_name, allowed_roots=(stage_dir,))
    atomic_write_bytes(dest, content)
    try:
        return service.reattach_source(
            project_id,
            source_id,
            SourceReattachReq(uri_or_filename=str(dest)),
        )
    except Exception:
        if dest.is_file():
            dest.unlink(missing_ok=True)
        with contextlib.suppress(OSError):
            stage_dir.rmdir()
        raise


def dual_request_body_openapi(
    json_schema_name: str,
    form_properties: dict[str, object],
) -> dict[str, object]:
    """Build OpenAPI requestBody metadata for dual JSON + multipart/form-data routes."""
    return {
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {
                    "schema": {"$ref": f"#/components/schemas/{json_schema_name}"}
                },
                "multipart/form-data": {
                    "schema": {
                        "type": "object",
                        "properties": {
                            "file": {"type": "string", "format": "binary"},
                            **form_properties,
                        },
                        "required": ["file"],
                    }
                },
            },
        }
    }


__all__ = [
    "dual_request_body_openapi",
    "enforce_api_workspace_path",
    "enforce_grounding_asset_path",
    "get_service",
    "get_task_manager",
    "handle_project_import",
    "handle_source_reattach",
    "read_grounding_asset_bytes",
    "read_upload_bounded",
]
