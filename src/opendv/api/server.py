"""FastAPI application factory, conditional SPA static mount, and OpenAPI schema export."""

from __future__ import annotations

import mimetypes
import os
import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.openapi.utils import get_openapi
from fastapi.responses import HTMLResponse

from opendv import __version__
from opendv.models.errors import ErrorCode, OpenDVError, ProblemDetails
from opendv.models.wire_dtos_discovery import ProjectImportPathReq, SourceReattachReq
from opendv.service.app_service import OpenDVService

from ._security import LoopbackAndContentTypeMiddleware, register_exception_handlers
from ._tasks import RunTaskManager
from .routes_projects import router as projects_router
from .routes_workflows import router as workflows_router

_MEDIA_TYPES: dict[str, str] = {
    ".js": "application/javascript",
    ".mjs": "application/javascript",
    ".css": "text/css",
    ".html": "text/html; charset=utf-8",
    ".json": "application/json",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".ico": "image/x-icon",
    ".map": "application/json",
}


def _resolve_static_dir(static_dir: Path | str | None) -> Path:
    if static_dir is not None:
        return Path(static_dir).resolve()
    env_dir = os.environ.get("OPENDV_STATIC_DIR", "").strip()
    if env_dir:
        return Path(env_dir).resolve()
    return (Path(__file__).resolve().parent / "static").resolve()


def _resolve_allow_remote(explicit: bool | None) -> bool:
    if explicit is not None:
        return explicit
    return os.environ.get("OPENDV_ALLOW_REMOTE_UNAUTHENTICATED", "").strip().lower() in {
        "1",
        "true",
        "yes",
    }


def _resolve_bound_host(explicit: str | None) -> str | None:
    if explicit is not None:
        cleaned = explicit.strip()
        return cleaned or None
    env_val = os.environ.get("OPENDV_BOUND_HOST", "").strip()
    return env_val or None


def _mount_conditional_spa(app: FastAPI) -> None:
    def _serve_spa_index(request: Request) -> HTMLResponse:
        sdir = Path(request.app.state.static_dir)
        index_file = sdir / "index.html"
        if not index_file.is_file():
            raise OpenDVError(
                "Web UI static bundle is not installed.",
                error_code=ErrorCode.RESOURCE_NOT_FOUND,
            )
        return HTMLResponse(content=index_file.read_text(encoding="utf-8"))

    @app.get("/assets/{asset_path:path}", include_in_schema=False)
    def serve_static_asset(request: Request, asset_path: str) -> Response:
        if "\x00" in asset_path or asset_path.startswith("/") or ".." in asset_path.split("/"):
            raise OpenDVError(
                f"Static asset '{asset_path}' not found.",
                error_code=ErrorCode.RESOURCE_NOT_FOUND,
            )
        assets_dir = Path(os.path.realpath(Path(request.app.state.static_dir) / "assets"))
        if not assets_dir.is_dir():
            raise OpenDVError(
                "Static assets directory not installed.",
                error_code=ErrorCode.RESOURCE_NOT_FOUND,
            )
        target = Path(os.path.realpath(assets_dir / asset_path))
        try:
            common = os.path.commonpath([os.fspath(target), os.fspath(assets_dir)])
        except ValueError as exc:
            raise OpenDVError(
                f"Static asset '{asset_path}' not found.",
                error_code=ErrorCode.RESOURCE_NOT_FOUND,
            ) from exc
        norm_target = os.path.normcase(os.fspath(assets_dir))
        if os.path.normcase(common) != norm_target or not target.is_file():
            raise OpenDVError(
                f"Static asset '{asset_path}' not found.",
                error_code=ErrorCode.RESOURCE_NOT_FOUND,
            )
        media_type = (
            _MEDIA_TYPES.get(target.suffix.lower())
            or mimetypes.guess_type(target.name)[0]
            or "application/octet-stream"
        )
        return Response(content=target.read_bytes(), media_type=media_type)

    for spa_path in (
        "/",
        "/projects",
        "/projects/{rest:path}",
        "/explorer",
        "/explorer/{rest:path}",
        "/runs",
        "/runs/{rest:path}",
        "/settings",
        "/settings/{rest:path}",
    ):
        app.add_api_route(
            spa_path,
            _serve_spa_index,
            methods=["GET"],
            include_in_schema=False,
        )


def _install_openapi_customizer(app: FastAPI) -> None:
    def custom_openapi() -> dict[str, Any]:
        if app.openapi_schema is not None:
            return app.openapi_schema
        schema = get_openapi(
            title=app.title,
            version=app.version,
            description=app.description,
            routes=app.routes,
        )
        components = schema.setdefault("components", {})
        schemas = components.setdefault("schemas", {})
        for model_cls in (ProjectImportPathReq, SourceReattachReq, ProblemDetails):
            if model_cls.__name__ not in schemas:
                model_schema = model_cls.model_json_schema(
                    ref_template="#/components/schemas/{model}"
                )
                defs = model_schema.pop("$defs", None)
                if isinstance(defs, dict):
                    for def_name, def_schema in defs.items():
                        schemas.setdefault(def_name, def_schema)
                schemas[model_cls.__name__] = model_schema
        app.openapi_schema = schema
        return schema

    app.openapi = custom_openapi  # type: ignore[method-assign]


def create_app(
    service: OpenDVService | None = None,
    *,
    static_dir: Path | str | None = None,
    allow_remote_unauthenticated: bool | None = None,
    bound_host: str | None = None,
    workspace_root: Path | str | None = None,
) -> FastAPI:
    """Create the Open-DV V2 FastAPI application over OpenDVService."""
    task_manager = RunTaskManager()

    @asynccontextmanager
    async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
        try:
            yield
        finally:
            await task_manager.shutdown()

    app = FastAPI(
        title="Open-DV V2 Platform API",
        version=__version__,
        description="Neuro-Symbolic Data Vault 2.0 Discovery, CEGAR Synthesis, and Emitter API.",
        lifespan=_lifespan,
    )

    resolved_svc = service
    if resolved_svc is None:
        ws = Path(workspace_root) if workspace_root is not None else Path.cwd()
        resolved_svc = OpenDVService(ws)

    app.state.service = resolved_svc
    app.state.task_manager = task_manager
    app.state.static_dir = _resolve_static_dir(static_dir)
    app.state.allow_remote_unauthenticated = _resolve_allow_remote(allow_remote_unauthenticated)
    app.state.bound_host = _resolve_bound_host(bound_host)

    register_exception_handlers(app)
    app.add_middleware(LoopbackAndContentTypeMiddleware)

    app.include_router(projects_router, prefix="/api/v1")
    app.include_router(workflows_router, prefix="/api/v1")
    _mount_conditional_spa(app)
    _install_openapi_customizer(app)
    return app


def export_openapi_schema() -> dict[str, Any]:
    """Generate the deterministic OpenAPI 3.1 schema without touching user state."""
    with tempfile.TemporaryDirectory(prefix="opendv_openapi_") as tmp:
        tmp_path = Path(tmp)
        svc = OpenDVService(tmp_path / "ws", state_dir=tmp_path / "state")
        app = create_app(svc, static_dir=tmp_path / "empty_static")
        return app.openapi()


__all__ = ["create_app", "export_openapi_schema"]
