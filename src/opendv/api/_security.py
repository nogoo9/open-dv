"""Pure ASGI loopback/origin/content-type/size guards and RFC 9457 ProblemDetails handlers."""

from __future__ import annotations

import ipaddress
import os
import re
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from opendv.models.errors import (
    ErrorCode,
    OpenDVError,
    ProblemDetails,
    create_problem_details,
)

MAX_REQUEST_BODY_BYTES: int = 262_144_000
_LOOPBACK_HOSTS: frozenset[str] = frozenset({"127.0.0.1", "localhost", "::1"})
_IMPORT_PATH = "/api/v1/projects/import"
_UPLOAD_RE = re.compile(r"^/api/v1/projects/[^/]+/sources/upload$")
_PATCH_SOURCE_RE = re.compile(r"^/api/v1/projects/[^/]+/sources/[^/]+$")


class _StreamLimitExceededError(BaseException):
    pass


class _StreamUnsupportedMediaTypeError(BaseException):
    pass


def parse_env_workspace_roots() -> list[Path] | None:
    """Return configured OPENDV_WORKSPACE_ROOTS entries if set and non-empty."""
    raw = os.environ.get("OPENDV_WORKSPACE_ROOTS", "").strip()
    if not raw:
        return None
    roots = [Path(item.strip()) for item in raw.split(os.pathsep) if item.strip()]
    return roots or None


def problem_json_response(
    problem: ProblemDetails,
    *,
    status_code: int | None = None,
) -> JSONResponse:
    """Serialize ProblemDetails with application/problem+json media type."""
    status = (
        status_code
        if status_code is not None
        else (problem.status if problem.status is not None else 500)
    )
    return JSONResponse(
        status_code=status,
        content=problem.model_dump(mode="json"),
        media_type="application/problem+json",
    )


def _normalize_host_port(raw_authority: str, scheme: str) -> tuple[str, int] | None:
    text = raw_authority.strip()
    if not text or any(ch.isspace() or ord(ch) < 32 or ord(ch) == 127 for ch in text):
        return None
    parsed = urlparse(f"//{text}")
    if (
        not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path
        or parsed.params
        or parsed.query
        or parsed.fragment
    ):
        return None
    try:
        port = parsed.port
    except ValueError:
        return None
    if port is not None and not (1 <= port <= 65535):
        return None
    hostname = (parsed.hostname or "").strip().lower()
    if not hostname:
        return None
    eff_port = port if port is not None else (443 if scheme == "https" else 80)
    return hostname, eff_port


def _normalize_bound_host(bound_host: str | None) -> str | None:
    if not bound_host or not bound_host.strip():
        return None
    text = bound_host.strip()
    parsed = _normalize_host_port(text, "http")
    if parsed is not None:
        return parsed[0]
    return text.strip("[]").lower()


def is_peer_loopback(client: Any) -> bool:
    """Return True if client peer is TestClient or loopback IP."""
    if not client:
        return False
    peer = str(client[0])
    if peer == "testclient":
        return True
    try:
        return ipaddress.ip_address(peer).is_loopback
    except ValueError:
        return False


def is_remote_request(request: Request) -> bool:
    """Return True if peer IP or Host header is non-loopback (excluding testserver)."""
    if not is_peer_loopback(request.scope.get("client")):
        return True
    raw_host = request.headers.get("host", "")
    parsed = _normalize_host_port(raw_host, request.url.scheme)
    if parsed is None:
        return True
    hostname, _ = parsed
    if hostname == "testserver":
        server = request.scope.get("server")
        return not (isinstance(server, (tuple, list)) and server and server[0] == "testserver")
    return hostname not in _LOOPBACK_HOSTS


def _is_host_allowed(
    hostname: str,
    server_host: str | None,
    *,
    allow_remote: bool,
    bound_host: str | None,
) -> bool:
    if hostname == "testserver":
        return server_host == "testserver"
    if hostname in _LOOPBACK_HOSTS:
        return True
    if allow_remote:
        norm_bound = _normalize_bound_host(bound_host)
        if norm_bound in ("0.0.0.0", "::"):
            return True
        if norm_bound is not None and hostname == norm_bound:
            return True
    return False


def _validate_origin(
    headers_multi: dict[str, list[str]],
    req_scheme: str,
    host_name: str,
    host_port: int,
    instance: str,
) -> None:
    for sf_raw in headers_multi.get("sec-fetch-site", ()):
        tokens = [tok.strip().lower() for tok in sf_raw.split(",")]
        if "cross-site" in tokens:
            raise OpenDVError(
                "Cross-site Sec-Fetch-Site header rejected on loopback API.",
                error_code=ErrorCode.LOOPBACK_ORIGIN_FORBIDDEN,
            )
    for origin in headers_multi.get("origin", ()):
        raw_orig = origin.strip()
        if not raw_orig or raw_orig.lower() == "null":
            raise OpenDVError(
                "Null or empty Origin header rejected on loopback API.",
                error_code=ErrorCode.LOOPBACK_ORIGIN_FORBIDDEN,
            )
        parsed = urlparse(raw_orig)
        scheme = parsed.scheme.lower()
        if (
            scheme not in {"http", "https"}
            or not parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path
            or parsed.params
            or parsed.query
            or parsed.fragment
        ):
            raise OpenDVError(
                f"Malformed Origin '{origin}' rejected for '{instance}'.",
                error_code=ErrorCode.LOOPBACK_ORIGIN_FORBIDDEN,
            )
        try:
            orig_port = parsed.port
        except ValueError as exc:
            raise OpenDVError(
                f"Invalid Origin port in '{origin}'.",
                error_code=ErrorCode.LOOPBACK_ORIGIN_FORBIDDEN,
            ) from exc
        orig_host = (parsed.hostname or "").strip().lower()
        eff_orig_port = orig_port if orig_port is not None else (443 if scheme == "https" else 80)
        if scheme != req_scheme or orig_host != host_name or eff_orig_port != host_port:
            raise OpenDVError(
                f"Origin '{origin}' does not match '{req_scheme}://{host_name}:{host_port}'.",
                error_code=ErrorCode.LOOPBACK_ORIGIN_FORBIDDEN,
            )


async def _send_problem(send: Send, exc: OpenDVError, instance: str) -> None:
    problem = exc.to_problem_details(instance=instance)
    status = exc.http_status if exc.http_status is not None else 500
    body = problem.model_dump_json().encode("utf-8")
    await send(
        {
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/problem+json"),
                (b"content-length", str(len(body)).encode("ascii")),
            ],
        }
    )
    await send({"type": "http.response.body", "body": body})


class LoopbackAndContentTypeMiddleware:
    """Pure ASGI middleware enforcing loopback Host/Origin/CORS, 415 MIME, and 413 250MB cap."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        path = str(scope.get("path") or "/")
        norm_path = path.rstrip("/") or "/"
        scheme = str(scope.get("scheme") or "http").lower()
        method = str(scope.get("method") or "GET").upper()
        headers_multi: dict[str, list[str]] = {}
        for k_bytes, v_bytes in scope.get("headers", ()):
            k_str = k_bytes.decode("latin-1").lower()
            headers_multi.setdefault(k_str, []).append(v_bytes.decode("latin-1"))

        app_obj = scope.get("app")
        app_state = getattr(app_obj, "state", None)
        allow_remote = bool(getattr(app_state, "allow_remote_unauthenticated", False))
        bound_host = getattr(app_state, "bound_host", None)

        response_started = False

        async def guarded_send(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            if not allow_remote and not is_peer_loopback(scope.get("client")):
                raise OpenDVError(
                    "Non-loopback client connection rejected.",
                    error_code=ErrorCode.LOOPBACK_ORIGIN_FORBIDDEN,
                )

            host_vals = headers_multi.get("host", [])
            if len(host_vals) != 1:
                raise OpenDVError(
                    "Missing or duplicate Host header.",
                    error_code=ErrorCode.LOOPBACK_ORIGIN_FORBIDDEN,
                )
            raw_host = host_vals[0]
            host_parsed = _normalize_host_port(raw_host, scheme)
            if host_parsed is None:
                raise OpenDVError(
                    f"Missing or malformed Host header '{raw_host}'.",
                    error_code=ErrorCode.LOOPBACK_ORIGIN_FORBIDDEN,
                )
            host_name, host_port = host_parsed
            server = scope.get("server")
            server_host = str(server[0]) if isinstance(server, (tuple, list)) and server else None
            if not _is_host_allowed(
                host_name, server_host, allow_remote=allow_remote, bound_host=bound_host
            ):
                raise OpenDVError(
                    f"Forbidden Host header '{raw_host}'.",
                    error_code=ErrorCode.LOOPBACK_ORIGIN_FORBIDDEN,
                )

            _validate_origin(headers_multi, scheme, host_name, host_port, path)

            media_type = ""
            is_json_only = False
            if method in {"POST", "PUT", "PATCH"}:
                cl_vals = headers_multi.get("content-length", [])
                parsed_cls: list[int] = []
                for raw_cl in cl_vals:
                    try:
                        cl_num = int(raw_cl.strip())
                    except ValueError as exc:
                        raise OpenDVError(
                            f"Invalid Content-Length header '{raw_cl}'.",
                            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
                        ) from exc
                    if cl_num < 0:
                        raise OpenDVError(
                            f"Negative Content-Length header '{raw_cl}'.",
                            error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
                        )
                    if cl_num > MAX_REQUEST_BODY_BYTES:
                        raise OpenDVError(
                            f"Request body exceeds 250 MB limit ({cl_num} bytes).",
                            error_code=ErrorCode.ARCHIVE_SIZE_LIMIT_EXCEEDED,
                        )
                    parsed_cls.append(cl_num)
                if len(parsed_cls) > 1:
                    raise OpenDVError(
                        "Duplicate Content-Length headers are forbidden.",
                        error_code=ErrorCode.SYNTAX_INVALID_ARGUMENT,
                    )
                content_length = parsed_cls[0] if parsed_cls else None

                ct_vals = headers_multi.get("content-type", [])
                if len(ct_vals) > 1:
                    raise OpenDVError(
                        "Duplicate Content-Type headers are forbidden.",
                        error_code=ErrorCode.UNSUPPORTED_MEDIA_TYPE,
                    )
                media_type = ct_vals[0].split(";", 1)[0].strip().lower() if ct_vals else ""
                te_vals = headers_multi.get("transfer-encoding", [])
                has_chunked = any("chunked" in te.lower() for te in te_vals)
                has_declared_body = (
                    content_length is not None and content_length > 0
                ) or has_chunked

                is_upload = method == "POST" and _UPLOAD_RE.fullmatch(norm_path) is not None
                is_dual = (method == "POST" and norm_path == _IMPORT_PATH) or (
                    method == "PATCH" and _PATCH_SOURCE_RE.fullmatch(norm_path) is not None
                )
                if is_upload:
                    if media_type != "multipart/form-data":
                        raise OpenDVError(
                            "Upload route requires Content-Type: multipart/form-data.",
                            error_code=ErrorCode.UNSUPPORTED_MEDIA_TYPE,
                        )
                elif is_dual:
                    if media_type and media_type not in {"application/json", "multipart/form-data"}:
                        raise OpenDVError(
                            f"Unsupported media type '{media_type}'.",
                            error_code=ErrorCode.UNSUPPORTED_MEDIA_TYPE,
                        )
                    if not media_type and (has_declared_body or norm_path == _IMPORT_PATH):
                        raise OpenDVError(
                            "Request requires application/json or multipart/form-data.",
                            error_code=ErrorCode.UNSUPPORTED_MEDIA_TYPE,
                        )
                else:
                    is_json_only = True
                    if media_type in {
                        "application/x-www-form-urlencoded",
                        "multipart/form-data",
                        "text/plain",
                    } or (media_type and media_type != "application/json") or (
                        has_declared_body and media_type != "application/json"
                    ):
                        raise OpenDVError(
                            f"Unsupported media type '{media_type or 'missing'}'; "
                            "expected application/json.",
                            error_code=ErrorCode.UNSUPPORTED_MEDIA_TYPE,
                        )

            received_bytes = 0

            async def bounded_receive() -> Message:
                nonlocal received_bytes
                msg = await receive()
                if msg.get("type") == "http.request":
                    body = msg.get("body", b"")
                    received_bytes += len(body)
                    if received_bytes > MAX_REQUEST_BODY_BYTES:
                        raise _StreamLimitExceededError
                    if is_json_only and received_bytes > 0 and media_type != "application/json":
                        raise _StreamUnsupportedMediaTypeError
                return msg

            await self.app(scope, bounded_receive, guarded_send)
        except _StreamLimitExceededError:
            if not response_started:
                await _send_problem(
                    send,
                    OpenDVError(
                        "Request body exceeded 250 MB (262,144,000 bytes) limit.",
                        error_code=ErrorCode.ARCHIVE_SIZE_LIMIT_EXCEEDED,
                    ),
                    path,
                )
        except _StreamUnsupportedMediaTypeError:
            if not response_started:
                await _send_problem(
                    send,
                    OpenDVError(
                        "Non-empty body requires Content-Type: application/json.",
                        error_code=ErrorCode.UNSUPPORTED_MEDIA_TYPE,
                    ),
                    path,
                )
        except OpenDVError as exc:
            if not response_started:
                await _send_problem(send, exc, path)
            else:
                raise


def _format_validation_detail(exc: RequestValidationError | ValidationError) -> str:
    errs = exc.errors(include_input=False) if isinstance(exc, ValidationError) else exc.errors()
    parts: list[str] = []
    for err in errs[:5]:
        loc = ".".join(str(x) for x in err.get("loc", ()))
        msg = str(err.get("msg", "validation error"))
        parts.append(f"{loc}: {msg}" if loc else msg)
    return "; ".join(parts) or "Request validation failed."


def register_exception_handlers(app: FastAPI) -> None:
    """Register RFC 9457 ProblemDetails exception handlers on FastAPI app."""

    async def handle_opendv_error(request: Request, exc: Exception) -> JSONResponse:
        assert isinstance(exc, OpenDVError)
        problem = exc.to_problem_details(instance=str(request.url.path))
        return problem_json_response(problem)

    async def handle_validation_error(request: Request, exc: Exception) -> JSONResponse:
        assert isinstance(exc, (RequestValidationError, ValidationError))
        problem = create_problem_details(
            ErrorCode.SYNTAX_INVALID_ARGUMENT,
            _format_validation_detail(exc),
            instance=str(request.url.path),
        )
        return problem_json_response(problem, status_code=400)

    async def handle_http_error(request: Request, exc: Exception) -> JSONResponse:
        assert isinstance(exc, StarletteHTTPException)
        status_map: dict[int, ErrorCode] = {
            400: ErrorCode.SYNTAX_INVALID_ARGUMENT,
            403: ErrorCode.LOOPBACK_ORIGIN_FORBIDDEN,
            404: ErrorCode.RESOURCE_NOT_FOUND,
            413: ErrorCode.ARCHIVE_SIZE_LIMIT_EXCEEDED,
            415: ErrorCode.UNSUPPORTED_MEDIA_TYPE,
        }
        if exc.status_code in status_map:
            code = status_map[exc.status_code]
        elif exc.status_code >= 500:
            code = ErrorCode.INTERNAL_SERVER_ERROR
        else:
            code = ErrorCode.SYNTAX_INVALID_ARGUMENT
        problem = create_problem_details(
            code,
            str(exc.detail or "HTTP error"),
            instance=str(request.url.path),
        ).model_copy(update={"status": exc.status_code})
        return problem_json_response(problem, status_code=exc.status_code)

    async def handle_unhandled_error(request: Request, _exc: Exception) -> JSONResponse:
        problem = create_problem_details(
            ErrorCode.INTERNAL_SERVER_ERROR,
            "Internal server error.",
            instance=str(request.url.path),
        )
        return problem_json_response(problem, status_code=500)

    app.add_exception_handler(OpenDVError, handle_opendv_error)
    app.add_exception_handler(RequestValidationError, handle_validation_error)
    app.add_exception_handler(ValidationError, handle_validation_error)
    app.add_exception_handler(StarletteHTTPException, handle_http_error)
    app.add_exception_handler(Exception, handle_unhandled_error)


__all__ = [
    "MAX_REQUEST_BODY_BYTES",
    "LoopbackAndContentTypeMiddleware",
    "is_peer_loopback",
    "is_remote_request",
    "parse_env_workspace_roots",
    "problem_json_response",
    "register_exception_handlers",
]
