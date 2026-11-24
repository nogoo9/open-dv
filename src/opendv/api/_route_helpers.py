"""Shared dependency accessors, workspace containment, and dual JSON/multipart parsers."""

from __future__ import annotations

import contextlib
import os
import tempfile
import uuid
from pathlib import Path
from typing import Literal, cast

from fastapi import Request, UploadFile

from opendv.models.errors import ErrorCode, OpenDVError
from opendv.models.sources import SourceSummary
from opendv.models.wire_dtos_discovery import ProjectImportPathReq, SourceReattachReq
from opendv.service.app_service import OpenDVService
from opendv.service.os_platform import atomic_write_bytes, validate_workspace_containment

from ._security import (
    MAX_REQUEST_BODY_BYTES,
    is_remote_request,
    parse_env_workspace_roots,
)
from ._tasks import RunTaskManager


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
    fd, tmp_name = tempfile.mkstemp(prefix="opendv_import_", suffix=".opendv.zip")
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
    "get_service",
    "get_task_manager",
    "handle_project_import",
    "handle_source_reattach",
    "read_upload_bounded",
]
