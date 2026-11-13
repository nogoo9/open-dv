"""Headless Platform REST + SSE ASGI Server (`opendv_platform_api.main`)."""

from __future__ import annotations

import contextlib
import os
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Final

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import Message

from opendv_contracts.errors import (
    DEFAULT_MAX_INLINE_BYTES,
    ERROR_MAP,
    ErrorCode,
    PayloadTooLargeError,
)
from opendv_core.paths import PathContainmentError
from opendv_platform_api.job_supervisor import get_job_supervisor
from opendv_platform_api.routers import (
    artifacts,
    compile,
    decisions,
    explorer,
    gates,
    health,
    ontology,
    ontology_studio,
    platform_config,
    profile,
    projects,
    runs,
    sandbox,
    verify,
    web_ui,
)
from opendv_platform_api.security.error_catalog import (
    StudioAPIError,
    build_studio_problem_response,
)
from opendv_service import OpenDVService, OpenDVSettings, PlatformStateContainer
from opendv_service.errors import OpenDVError
from opendv_service.studio.atomic_commit import TamperEvidentChainCorruptError
from opendv_service.studio.locks import LockTimeoutError
from opendv_service.studio.session_manager import SessionNotFoundError

STUDIO_API_PREFIX: Final[str] = "/api/v1/ontology/studio"
STUDIO_MAX_SOURCE_UPLOAD_BYTES: Final[int] = ontology_studio.MAX_SOURCE_UPLOAD_BYTES
STUDIO_MAX_BUNDLE_IMPORT_BYTES: Final[int] = ontology_studio.MAX_BUNDLE_IMPORT_BYTES

_HTTP_STATUS_TITLES: dict[int, str] = {
    400: "Bad Request",
    401: "Unauthorized",
    403: "Forbidden",
    404: "Resource Not Found",
    409: "Conflict",
    413: "Payload Too Large",
    422: "Unprocessable Content",
    500: "Internal Server Error",
}


def _is_studio_path(path: str) -> bool:
    return path == STUDIO_API_PREFIX or path.startswith(f"{STUDIO_API_PREFIX}/")


def _resolve_request_size_limit(request: Request, default_limit: int) -> int:
    path = request.url.path.rstrip("/")
    if request.method == "POST" and _is_studio_path(path):
        if path.endswith("/sources"):
            return STUDIO_MAX_SOURCE_UPLOAD_BYTES
        if path.endswith("/sessions/bundle/import"):
            return STUDIO_MAX_BUNDLE_IMPORT_BYTES
    return default_limit


def build_problem_details_response(
    status_code: int,
    detail: str,
    instance: str,
    problem_type: str | None = None,
    title: str | None = None,
    errors: list[dict[str, Any]] | None = None,
) -> JSONResponse:
    """Create an RFC 9457 `application/problem+json` response."""
    resolved_title = title or _HTTP_STATUS_TITLES.get(status_code, "Platform API Error")
    resolved_type = problem_type or f"https://open-dv.dev/problems/http-{status_code}"
    body: dict[str, Any] = {
        "type": resolved_type,
        "title": resolved_title,
        "status": status_code,
        "detail": detail,
        "instance": instance,
    }
    if errors is not None:
        body["errors"] = errors
    return JSONResponse(
        status_code=status_code,
        content=body,
        media_type="application/problem+json",
    )


def create_app(
    settings: OpenDVSettings | None = None,
    *,
    workspace_root: Path | None = None,
    storage_root: Path | None = None,
    repo_root: Path | None = None,
    state_dir: Path | None = None,
    platform_state: PlatformStateContainer | None = None,
    service: OpenDVService | None = None,
) -> FastAPI:
    """Construct and configure the Open-DV Headless Platform FastAPI application."""
    eff_ws = workspace_root or repo_root
    has_state_ws = bool(platform_state and getattr(platform_state, "_explicit_workspace", False))
    if eff_ws is None and has_state_ws:
        eff_ws = platform_state.workspace_root
    has_svc_ws = bool(service and getattr(service, "_explicit_workspace", False))
    if eff_ws is None and has_svc_ws:
        eff_ws = service.workspace_root

    eff_storage = storage_root or state_dir
    has_state_storage = bool(platform_state and getattr(platform_state, "_explicit_storage", False))
    if eff_storage is None and has_state_storage:
        eff_storage = getattr(platform_state, "storage_root", None)
    has_svc_storage = bool(service and getattr(service, "_explicit_storage", False))
    if eff_storage is None and has_svc_storage:
        eff_storage = getattr(service, "storage_root", None)

    explicit_ws_configured = bool(
        eff_ws is not None
        or settings is not None
        or has_state_ws
        or has_svc_ws
        or os.environ.get("OPENDV_WORKSPACE_ROOT", "").strip()
    )

    active_settings = settings or OpenDVSettings.load(
        workspace_root=eff_ws,
        state_dir=eff_storage,
        is_server=True,
    )

    if service is not None:
        active_service = service
    elif platform_state is not None:
        active_service = OpenDVService(
            state_store=platform_state,
            workspace_root=active_settings.workspace_root,
            storage_root=active_settings.state_dir,
            settings=active_settings,
        )
        active_service._explicit_workspace = explicit_ws_configured
    else:
        active_state = PlatformStateContainer(
            workspace_root=active_settings.workspace_root,
            storage_root=active_settings.state_dir,
            settings=active_settings,
        )
        active_service = OpenDVService(
            state_store=active_state,
            workspace_root=active_settings.workspace_root,
            storage_root=active_settings.state_dir,
            settings=active_settings,
        )
        active_service._explicit_workspace = explicit_ws_configured

    @asynccontextmanager
    async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
        with contextlib.suppress(Exception):
            env_studio = os.environ.get("OPENDV_STUDIO_ROOT", "").strip()
            studio_root = (
                Path(env_studio).resolve()
                if env_studio
                else (active_service.workspace_root / ".opendv" / "ontology_studio").resolve()
            )
            if studio_root.is_dir():
                get_job_supervisor().recover_abandoned_jobs(studio_root)
        yield

    application = FastAPI(
        title="Open-DV Platform API",
        version="0.1.0",
        description=(
            "Headless Platform REST + SSE API Server for No-Mistake Agentic Data Vault 2.0 "
            "verification, 4-Zone DuckDB sandbox execution, 5-Tier RBAC observability, "
            "and Steward Captain-Hold resolution."
        ),
        lifespan=_lifespan,
    )
    application.openapi_version = "3.1.0"
    application.state.settings = active_settings
    application.state.service = active_service
    application.state.platform_state = active_service.state
    if getattr(active_service.state, "settings", None) is None:
        active_service.state.settings = active_settings

    cors_origins = list(active_settings.cors_origins or [])
    allow_cors_credentials = bool(cors_origins) and "*" not in cors_origins

    application.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=allow_cors_credentials,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=[
            "Content-Type",
            "Authorization",
            "If-Match",
            "Last-Event-ID",
            "X-OpenDV-Role",
            "X-OpenDV-Principal",
            "X-OpenDV-Workbench",
            "X-OpenDV-Workspace-If-Match",
            "X-OpenDV-Waiver-Key",
        ],
        expose_headers=["ETag", "Content-Disposition", "X-OpenDV-Role", "X-OpenDV-Principal"],
    )

    @application.middleware("http")
    async def enforce_payload_size_limit(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        default_limit = getattr(active_settings, "max_inline_bytes", DEFAULT_MAX_INLINE_BYTES)
        max_limit = _resolve_request_size_limit(request, default_limit)
        path_str = str(request.url.path)
        is_studio = _is_studio_path(path_str)

        cl_header = request.headers.get("content-length")
        if cl_header:
            try:
                content_length = int(cl_header.strip())
                if content_length > max_limit:
                    detail_msg = (
                        f"Request payload size ({content_length} bytes) "
                        f"exceeds limit ({max_limit} bytes)."
                    )
                    if is_studio:
                        return build_studio_problem_response(
                            code="PAYLOAD_TOO_LARGE",
                            detail=detail_msg,
                            status_code=413,
                            instance=path_str,
                        )
                    return build_problem_details_response(
                        status_code=413,
                        detail=detail_msg,
                        instance=path_str,
                        problem_type="urn:opendv:payload-too-large",
                        title="Payload Too Large",
                    )
            except ValueError:
                if is_studio:
                    return build_studio_problem_response(
                        code="SYNTAX_INVALID_ARGUMENT",
                        detail="Invalid Content-Length header.",
                        status_code=400,
                        instance=path_str,
                    )
                return build_problem_details_response(
                    status_code=400,
                    detail="Invalid Content-Length header.",
                    instance=path_str,
                    problem_type="urn:opendv:invalid-request",
                    title="Invalid Request",
                )

        original_receive = request.receive
        received_bytes = 0

        async def limited_receive() -> Message:
            nonlocal received_bytes
            message = await original_receive()
            if message.get("type") == "http.request":
                chunk = message.get("body", b"")
                received_bytes += len(chunk)
                if received_bytes > max_limit:
                    raise PayloadTooLargeError(
                        f"Request payload stream ({received_bytes} bytes) "
                        f"exceeds limit ({max_limit} bytes)."
                    )
            return message

        request._receive = limited_receive
        try:
            response = await call_next(request)
            studio_principal = getattr(request.state, "studio_principal", None)
            if studio_principal is not None:
                response.headers["X-OpenDV-Role"] = str(studio_principal.role)
                response.headers["X-OpenDV-Principal"] = str(studio_principal.principal)
            return response
        except PayloadTooLargeError as exc:
            if is_studio:
                return build_studio_problem_response(
                    code="PAYLOAD_TOO_LARGE",
                    detail=str(exc),
                    status_code=413,
                    instance=path_str,
                )
            return build_problem_details_response(
                status_code=413,
                detail=str(exc),
                instance=path_str,
                problem_type="urn:opendv:payload-too-large",
                title="Payload Too Large",
            )

    @application.middleware("http")
    async def attach_security_headers(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Cache-Control", "no-store")
        return response

    @application.exception_handler(SessionNotFoundError)
    async def handle_session_not_found(
        request: Request,
        exc: SessionNotFoundError,
    ) -> JSONResponse:
        return build_studio_problem_response(
            code="SESSION_NOT_FOUND",
            detail=str(exc),
            instance=str(request.url.path),
        )

    @application.exception_handler(LockTimeoutError)
    async def handle_lock_timeout(
        request: Request,
        exc: LockTimeoutError,
    ) -> JSONResponse:
        return build_studio_problem_response(
            code="LOCK_TIMEOUT",
            detail=str(exc),
            instance=str(request.url.path),
        )

    @application.exception_handler(TamperEvidentChainCorruptError)
    async def handle_tamper_evident_chain_corrupt(
        request: Request,
        exc: TamperEvidentChainCorruptError,
    ) -> JSONResponse:
        return build_studio_problem_response(
            code="TAMPER_EVIDENT_CHAIN_CORRUPT",
            detail=str(exc),
            instance=str(request.url.path),
        )

    @application.exception_handler(OpenDVError)
    async def handle_opendv_error(
        request: Request,
        exc: OpenDVError,
    ) -> JSONResponse:
        path_str = str(request.url.path)
        if _is_studio_path(path_str):
            raw_code = getattr(exc, "error_code", ErrorCode.INTERNAL_ERROR)
            code_str = raw_code.value if hasattr(raw_code, "value") else str(raw_code)
            extensions = getattr(exc, "details", None) or None
            return build_studio_problem_response(
                code=code_str,
                detail=str(getattr(exc, "message", None) or str(exc)),
                status_code=getattr(exc, "http_status", None),
                instance=path_str,
                extensions=extensions if isinstance(extensions, dict) else None,
            )

        mapping = ERROR_MAP.get(exc.error_code) or ERROR_MAP[ErrorCode.INTERNAL_ERROR]
        status_code = getattr(exc, "http_status", None) or mapping.http_status
        return build_problem_details_response(
            status_code=status_code,
            detail=str(getattr(exc, "message", None) or str(exc)),
            instance=path_str,
            problem_type=mapping.problem_type_uri,
            title=mapping.problem_title,
        )

    @application.exception_handler(StarletteHTTPException)
    @application.exception_handler(HTTPException)
    async def handle_http_exception(
        request: Request,
        exc: StarletteHTTPException,
    ) -> JSONResponse:
        path_str = str(request.url.path)
        cause = exc.__cause__
        payload_too_large: PayloadTooLargeError | None = None
        if isinstance(cause, PayloadTooLargeError):
            payload_too_large = cause
        elif isinstance(cause, BaseExceptionGroup):
            for sub_exc in cause.exceptions:
                if isinstance(sub_exc, PayloadTooLargeError):
                    payload_too_large = sub_exc
                    break

        if payload_too_large is not None:
            if _is_studio_path(path_str):
                return build_studio_problem_response(
                    code="PAYLOAD_TOO_LARGE",
                    detail=str(payload_too_large),
                    status_code=413,
                    instance=path_str,
                )
            return build_problem_details_response(
                status_code=413,
                detail=str(payload_too_large),
                instance=path_str,
                problem_type="urn:opendv:payload-too-large",
                title="Payload Too Large",
            )

        return build_problem_details_response(
            status_code=exc.status_code,
            detail=str(exc.detail),
            instance=path_str,
        )

    @application.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        path_str = str(request.url.path)
        sanitized_errors: list[dict[str, Any]] = []
        for err in exc.errors():
            sanitized_errors.append(
                {
                    "loc": [str(part) for part in err.get("loc", ())],
                    "msg": str(err.get("msg", "Validation error")),
                    "type": str(err.get("type", "value_error")),
                }
            )
        if _is_studio_path(path_str):
            return build_studio_problem_response(
                code="SYNTAX_INVALID_ARGUMENT",
                detail="Request payload failed schema validation.",
                status_code=400,
                instance=path_str,
                invalid_params=sanitized_errors,
            )
        return build_problem_details_response(
            status_code=422,
            detail="Request payload failed schema validation.",
            instance=path_str,
            problem_type="https://open-dv.dev/problems/validation-error",
            title="Unprocessable Content",
            errors=sanitized_errors,
        )

    @application.exception_handler(KeyError)
    async def handle_key_error(
        request: Request,
        exc: KeyError,
    ) -> JSONResponse:
        detail = str(exc.args[0]) if exc.args else str(exc)
        return build_problem_details_response(
            status_code=404,
            detail=detail,
            instance=str(request.url.path),
        )

    @application.exception_handler(PathContainmentError)
    async def handle_path_containment_error(
        request: Request,
        exc: PathContainmentError,
    ) -> JSONResponse:
        return build_problem_details_response(
            status_code=400,
            detail=(
                "Path containment violation: requested path is "
                "outside permitted workspace boundaries."
            ),
            instance=str(request.url.path),
            problem_type="https://open-dv.dev/problems/path-containment-error",
            title="Path Containment Violation",
        )

    @application.exception_handler(ValueError)
    async def handle_value_error(
        request: Request,
        exc: ValueError,
    ) -> JSONResponse:
        if _is_studio_path(str(request.url.path)):
            return build_studio_problem_response(
                code="SYNTAX_INVALID_ARGUMENT",
                detail=str(exc),
                status_code=400,
                instance=str(request.url.path),
            )
        return build_problem_details_response(
            status_code=400,
            detail=str(exc),
            instance=str(request.url.path),
        )

    @application.exception_handler(StudioAPIError)
    async def handle_studio_api_error(
        request: Request,
        exc: StudioAPIError,
    ) -> JSONResponse:
        return build_studio_problem_response(
            code=exc.code,
            detail=exc.detail,
            status_code=exc.status_code,
            instance=exc.instance or str(request.url.path),
            invalid_params=exc.invalid_params,
            extensions=exc.extensions,
        )

    @application.exception_handler(Exception)
    async def handle_unexpected_exception(
        request: Request,
        exc: Exception,
    ) -> JSONResponse:
        path_str = str(request.url.path)
        try:
            raise exc
        except Exception as unhandled_exc:  # noqa: BLE001 - API global exception handler boundary
            if _is_studio_path(path_str):
                return build_studio_problem_response(
                    code="INTERNAL_ERROR",
                    detail=f"An unexpected internal server error occurred: {unhandled_exc}",
                    status_code=500,
                    instance=path_str,
                )
            return build_problem_details_response(
                status_code=500,
                detail=f"An unexpected internal server error occurred: {unhandled_exc}",
                instance=path_str,
                problem_type="https://open-dv.dev/problems/internal-error",
                title="Internal Server Error",
            )

    api_prefix = "/api/v1"
    application.include_router(health.router, prefix=api_prefix)
    application.include_router(platform_config.router, prefix=api_prefix)
    application.include_router(profile.router, prefix=api_prefix)
    application.include_router(compile.router, prefix=api_prefix)
    application.include_router(verify.router, prefix=api_prefix)
    application.include_router(projects.router, prefix=api_prefix)
    application.include_router(runs.router, prefix=api_prefix)
    application.include_router(explorer.router, prefix=api_prefix)
    application.include_router(gates.router, prefix=api_prefix)
    application.include_router(sandbox.router, prefix=api_prefix)
    application.include_router(decisions.router, prefix=api_prefix)
    application.include_router(artifacts.router, prefix=api_prefix)
    application.include_router(ontology.router, prefix=api_prefix)
    application.include_router(ontology_studio.router, prefix=api_prefix)
    application.include_router(web_ui.router)

    return application


def __getattr__(name: str) -> Any:
    """Lazy module attribute access for `app` allowing startup validation upon invocation."""
    if name == "app":
        return create_app()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
