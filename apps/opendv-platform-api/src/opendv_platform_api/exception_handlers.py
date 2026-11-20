"""HTTP middlewares and RFC 9457 exception handlers for Open-DV Platform API."""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from typing import Any, Final

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
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
from opendv_platform_api.routers import cas
from opendv_platform_api.security.error_catalog import (
    StudioAPIError,
    build_studio_problem_response,
)
from opendv_platform_api.studio_deps import (
    get_effective_max_bundle_import_bytes,
    get_effective_max_source_upload_bytes,
)
from opendv_service.errors import OpenDVError
from opendv_service.studio.atomic_commit import TamperEvidentChainCorruptError
from opendv_service.studio.locks import LockTimeoutError
from opendv_service.studio.session_manager import SessionNotFoundError

STUDIO_API_PREFIX: Final[str] = "/api/v1/ontology/studio"

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
    if request.method == "POST":
        if _is_studio_path(path):
            if path.endswith("/sources"):
                return get_effective_max_source_upload_bytes()
            if path.endswith("/sessions/bundle/import"):
                return get_effective_max_bundle_import_bytes()
        if path.endswith("/cas/blobs"):
            return cas.MAX_CAS_UPLOAD_BYTES
    return default_limit


def _extract_payload_too_large(exc: BaseException) -> PayloadTooLargeError | None:
    if isinstance(exc, PayloadTooLargeError):
        return exc
    if isinstance(exc, BaseExceptionGroup):
        for sub_exc in exc.exceptions:
            found = _extract_payload_too_large(sub_exc)
            if found is not None:
                return found
    return None


def build_problem_details_response(
    status_code: int,
    detail: str,
    instance: str,
    problem_type: str | None = None,
    title: str | None = None,
    errors: list[dict[str, Any]] | None = None,
    error_code: str | None = None,
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
    if error_code is not None:
        body["error_code"] = error_code
    if errors is not None:
        body["errors"] = errors
    return JSONResponse(
        status_code=status_code,
        content=body,
        media_type="application/problem+json",
    )


def _payload_too_large_response(is_studio: bool, detail: str, path_str: str) -> JSONResponse:
    if is_studio:
        return build_studio_problem_response(
            code="PAYLOAD_TOO_LARGE",
            detail=detail,
            status_code=413,
            instance=path_str,
        )
    return build_problem_details_response(
        status_code=413,
        detail=detail,
        instance=path_str,
        problem_type="urn:opendv:payload-too-large",
        title="Payload Too Large",
    )


def register_middlewares(application: FastAPI, active_settings: Any) -> None:
    """Register payload-limit and security-header HTTP middlewares."""

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
                    return _payload_too_large_response(is_studio, detail_msg, path_str)
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
        except (PayloadTooLargeError, BaseExceptionGroup) as exc:
            ptl = _extract_payload_too_large(exc)
            if ptl is None:
                raise
            return _payload_too_large_response(is_studio, str(ptl), path_str)

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


def register_exception_handlers(application: FastAPI) -> None:
    """Register canonical RFC 9457 & Studio ProblemDetail exception handlers."""

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
        code_val = (
            exc.error_code.value
            if exc.error_code in {ErrorCode.AGENT_NOT_CONFIGURED, ErrorCode.SESSION_JOB_ACTIVE}
            else None
        )
        return build_problem_details_response(
            status_code=status_code,
            detail=str(getattr(exc, "message", None) or str(exc)),
            instance=path_str,
            problem_type=mapping.problem_type_uri,
            title=mapping.problem_title,
            error_code=code_val,
        )

    @application.exception_handler(StarletteHTTPException)
    @application.exception_handler(HTTPException)
    async def handle_http_exception(
        request: Request,
        exc: StarletteHTTPException,
    ) -> JSONResponse:
        path_str = str(request.url.path)
        cause = exc.__cause__
        payload_too_large = _extract_payload_too_large(cause) if cause is not None else None
        if payload_too_large is not None:
            return _payload_too_large_response(
                _is_studio_path(path_str), str(payload_too_large), path_str
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
        try:
            raw_errors = exc.errors(include_input=False)  # type: ignore[call-arg]
        except TypeError:
            raw_errors = exc.errors()
        for err in raw_errors:
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
        _ = exc
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
        sanitized_errors: list[dict[str, Any]] | None = None
        if hasattr(exc, "errors") and callable(exc.errors):
            raw_errs = None
            try:
                raw_errs = exc.errors(include_input=False)  # type: ignore[call-arg]
            except TypeError:
                try:
                    raw_errs = exc.errors()
                except (ValueError, AttributeError):
                    raw_errs = None
            except (ValueError, AttributeError):
                raw_errs = None

            if raw_errs:
                sanitized_errors = [
                    {
                        "loc": [str(part) for part in err.get("loc", ())],
                        "msg": str(err.get("msg", "Validation error")),
                        "type": str(err.get("type", "value_error")),
                    }
                    for err in raw_errs
                ]
        if sanitized_errors:
            detail_str = "; ".join(
                f"{'.'.join(err['loc'])}: {err['msg']}" if err.get("loc") else err["msg"]
                for err in sanitized_errors
            ) or "Validation error"
        elif type(exc).__name__ == "ValidationError":
            detail_str = "Validation error"
        else:
            detail_str = str(exc)
        if (
            "password" in detail_str.lower()
            or "secret" in detail_str.lower()
            or "token" in detail_str.lower()
        ):
            detail_str = re.sub(r"'(?:env|secret):[^']+'", "'[REDACTED]'", detail_str)
        if _is_studio_path(str(request.url.path)):
            return build_studio_problem_response(
                code="SYNTAX_INVALID_ARGUMENT",
                detail=detail_str,
                status_code=400,
                instance=str(request.url.path),
                invalid_params=sanitized_errors,
            )
        return build_problem_details_response(
            status_code=400,
            detail=detail_str,
            instance=str(request.url.path),
            errors=sanitized_errors,
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
        except Exception:  # noqa: BLE001 - API global exception handler boundary
            if _is_studio_path(path_str):
                return build_studio_problem_response(
                    code="INTERNAL_ERROR",
                    detail="An unexpected internal server error occurred.",
                    status_code=500,
                    instance=path_str,
                )
            return build_problem_details_response(
                status_code=500,
                detail="An unexpected internal server error occurred.",
                instance=path_str,
                problem_type="https://open-dv.dev/problems/internal-error",
                title="Internal Server Error",
            )


__all__ = [
    "STUDIO_API_PREFIX",
    "build_problem_details_response",
    "register_exception_handlers",
    "register_middlewares",
]
