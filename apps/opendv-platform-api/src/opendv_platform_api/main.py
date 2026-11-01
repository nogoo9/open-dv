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

from opendv_platform_api.deps import PlatformStateContainer
from opendv_platform_api.routers import (
    artifacts,
    decisions,
    gates,
    health,
    projects,
    runs,
    sandbox,
    workbench,
)

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
    repo_root: Path | None = None,
    state_dir: Path | None = None,
    platform_state: PlatformStateContainer | None = None,
) -> FastAPI:
    """Construct and configure the Open-DV Headless Platform FastAPI application."""
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

    application.state.platform_state = platform_state or PlatformStateContainer(
        repo_root=repo_root,
        state_dir=state_dir,
    )

    application.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "http://localhost:8420",
            "http://127.0.0.1:8420",
        ],
        allow_credentials=False,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type", "X-OpenDV-Role", "Last-Event-ID"],
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

    @application.exception_handler(StarletteHTTPException)
    @application.exception_handler(HTTPException)
    async def handle_http_exception(
        request: Request,
        exc: StarletteHTTPException,
    ) -> JSONResponse:
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
        return build_problem_details_response(
            status_code=404,
            detail=str(exc),
            instance=str(request.url.path),
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

    api_prefix = "/api/v1"
    application.include_router(health.router, prefix=api_prefix)
    application.include_router(projects.router, prefix=api_prefix)
    application.include_router(runs.router, prefix=api_prefix)
    application.include_router(gates.router, prefix=api_prefix)
    application.include_router(sandbox.router, prefix=api_prefix)
    application.include_router(decisions.router, prefix=api_prefix)
    application.include_router(workbench.router, prefix=api_prefix)
    application.include_router(artifacts.router, prefix=api_prefix)

    return application


app = create_app()
