"""Headless Platform REST + SSE ASGI Server (`opendv_platform_api.main`)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

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
from opendv_platform_api.routers import (
    artifacts,
    compile,
    decisions,
    explorer,
    gates,
    health,
    ontology,
    platform_config,
    profile,
    projects,
    runs,
    sandbox,
    verify,
    web_ui,
)
from opendv_service import OpenDVService, OpenDVSettings, PlatformStateContainer
from opendv_service.errors import OpenDVError

_HTTP_STATUS_TITLES: dict[int, str] = {
    400: "Bad Request",
    401: "Unauthorized",
    403: "Forbidden",
    404: "Resource Not Found",
    409: "Conflict",
    422: "Unprocessable Content",
    500: "Internal Server Error",
}


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

    application = FastAPI(
        title="Open-DV Platform API",
        version="0.1.0",
        description=(
            "Headless Platform REST + SSE API Server for No-Mistake Agentic Data Vault 2.0 "
            "verification, 4-Zone DuckDB sandbox execution, 5-Tier RBAC observability, "
            "and Steward Captain-Hold resolution."
        ),
    )
    application.openapi_version = "3.1.0"
    application.state.settings = active_settings
    application.state.service = active_service
    application.state.platform_state = active_service.state
    if getattr(active_service.state, "settings", None) is None:
        active_service.state.settings = active_settings

    application.add_middleware(
        CORSMiddleware,
        allow_origins=active_settings.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "OPTIONS"],
        allow_headers=["Content-Type", "X-OpenDV-Role", "Last-Event-ID"],
    )

    @application.middleware("http")
    async def enforce_payload_size_limit(
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        max_limit = getattr(active_settings, "max_inline_bytes", DEFAULT_MAX_INLINE_BYTES)
        cl_header = request.headers.get("content-length")
        if cl_header:
            try:
                content_length = int(cl_header.strip())
                if content_length > max_limit:
                    return build_problem_details_response(
                        status_code=413,
                        detail=(
                            f"Request payload size ({content_length} bytes) "
                            f"exceeds limit ({max_limit} bytes)."
                        ),
                        instance=str(request.url.path),
                        problem_type="urn:opendv:payload-too-large",
                        title="Payload Too Large",
                    )
            except ValueError:
                return build_problem_details_response(
                    status_code=400,
                    detail="Invalid Content-Length header.",
                    instance=str(request.url.path),
                    problem_type="urn:opendv:invalid-request",
                    title="Invalid Request",
                )

        # Wrap receive to enforce limits on chunked / streamed requests
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
            return await call_next(request)
        except PayloadTooLargeError as exc:
            return build_problem_details_response(
                status_code=413,
                detail=str(exc),
                instance=str(request.url.path),
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

    @application.exception_handler(OpenDVError)
    async def handle_opendv_error(
        request: Request,
        exc: OpenDVError,
    ) -> JSONResponse:
        mapping = ERROR_MAP.get(exc.error_code) or ERROR_MAP[ErrorCode.INTERNAL_ERROR]
        status_code = getattr(exc, "http_status", None) or mapping.http_status
        return build_problem_details_response(
            status_code=status_code,
            detail=str(getattr(exc, "message", None) or str(exc)),
            instance=str(request.url.path),
            problem_type=mapping.problem_type_uri,
            title=mapping.problem_title,
        )

    @application.exception_handler(StarletteHTTPException)
    @application.exception_handler(HTTPException)
    async def handle_http_exception(
        request: Request,
        exc: StarletteHTTPException,
    ) -> JSONResponse:
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
            return build_problem_details_response(
                status_code=413,
                detail=str(payload_too_large),
                instance=str(request.url.path),
                problem_type="urn:opendv:payload-too-large",
                title="Payload Too Large",
            )

        return build_problem_details_response(
            status_code=exc.status_code,
            detail=str(exc.detail),
            instance=str(request.url.path),
        )

    @application.exception_handler(RequestValidationError)
    async def handle_validation_error(
        request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        sanitized_errors: list[dict[str, Any]] = []
        for err in exc.errors():
            sanitized_errors.append(
                {
                    "loc": [str(part) for part in err.get("loc", ())],
                    "msg": str(err.get("msg", "Validation error")),
                    "type": str(err.get("type", "value_error")),
                }
            )
        return build_problem_details_response(
            status_code=422,
            detail="Request payload failed schema validation.",
            instance=str(request.url.path),
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
        return build_problem_details_response(
            status_code=400,
            detail=str(exc),
            instance=str(request.url.path),
        )

    @application.exception_handler(Exception)
    async def handle_unexpected_exception(
        request: Request,
        exc: Exception,
    ) -> JSONResponse:
        try:
            raise exc
        except Exception as unhandled_exc:  # noqa: BLE001 - API global exception handler boundary
            return build_problem_details_response(
                status_code=500,
                detail=f"An unexpected internal server error occurred: {unhandled_exc}",
                instance=str(request.url.path),
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
    application.include_router(web_ui.router)

    return application


def __getattr__(name: str) -> Any:
    """Lazy module attribute access for `app` allowing startup validation upon invocation."""
    if name == "app":
        return create_app()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
