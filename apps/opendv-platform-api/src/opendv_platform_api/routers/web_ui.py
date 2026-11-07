"""Unified static Web UI and SPA fallback routes (`/`, `/builder`, `/settings`, `/stages/*`)."""

from __future__ import annotations

import mimetypes
import os
from pathlib import Path

from fastapi import APIRouter, Response
from fastapi.responses import HTMLResponse

from opendv_contracts.errors import NotFoundError
from opendv_platform_api.deps import get_opendv_service
from opendv_platform_api.routers.health import WORKBENCH_CSP, WORKBENCH_INDEX_HTML
from opendv_service import OpenDVService

router = APIRouter(tags=["web-ui"], include_in_schema=False)

_MEDIA_TYPES: dict[str, str] = {
    ".js": "application/javascript",
    ".mjs": "application/javascript",
    ".css": "text/css",
    ".svg": "image/svg+xml",
    ".json": "application/json",
    ".map": "application/json",
    ".html": "text/html",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".ico": "image/x-icon",
}


def resolve_static_dir(service: OpenDVService | None = None) -> Path:
    """Resolve the compiled Web Workbench static asset directory."""
    _ = (service, get_opendv_service)
    env_dir = os.environ.get("OPENDV_STATIC_DIR", "").strip()
    if env_dir:
        return Path(env_dir).resolve()
    return (Path(__file__).resolve().parent.parent / "static").resolve()


def _serve_spa_index() -> HTMLResponse:
    """Serve compiled `index.html` if bundled, falling back to `WORKBENCH_INDEX_HTML`."""
    static_dir = resolve_static_dir()
    index_path = static_dir / "index.html"
    html = index_path.read_text(encoding="utf-8") if index_path.is_file() else WORKBENCH_INDEX_HTML
    return HTMLResponse(
        content=html,
        status_code=200,
        headers={"Content-Security-Policy": WORKBENCH_CSP},
    )


@router.get("/", include_in_schema=False)
async def get_root_index() -> HTMLResponse:
    """Serve unified Web UI root."""
    return _serve_spa_index()


@router.get("/index.html", include_in_schema=False)
async def get_index_html() -> HTMLResponse:
    """Serve unified Web UI index.html."""
    return _serve_spa_index()


@router.get("/builder", include_in_schema=False)
async def get_builder_route() -> HTMLResponse:
    """Serve SPA shell for `/builder` client route."""
    return _serve_spa_index()


@router.get("/settings", include_in_schema=False)
async def get_settings_route() -> HTMLResponse:
    """Serve SPA shell for `/settings` client route."""
    return _serve_spa_index()


@router.get("/stages/{stage_num}", include_in_schema=False)
async def get_stage_route(stage_num: str) -> HTMLResponse:
    """Serve SPA shell for `/stages/{stage_num}` client route."""
    _ = stage_num
    return _serve_spa_index()


@router.get("/assets/{asset_path:path}", include_in_schema=False)
async def get_static_asset(asset_path: str) -> Response:
    """Serve compiled Vite static assets with strict path-traversal containment."""
    if not asset_path or "\x00" in asset_path or ".." in Path(asset_path).parts:
        raise NotFoundError(f"Static asset not found: {asset_path}")

    static_dir = resolve_static_dir()
    assets_dir = (static_dir / "assets").resolve()
    cand = (assets_dir / asset_path).resolve()

    if not cand.is_relative_to(assets_dir) or not cand.is_file():
        raise NotFoundError(f"Static asset not found: {asset_path}")

    suffix = cand.suffix.lower()
    media_type = _MEDIA_TYPES.get(suffix) or (
        mimetypes.guess_type(cand.name)[0] or "application/octet-stream"
    )
    return Response(
        content=cand.read_bytes(),
        status_code=200,
        media_type=media_type,
        headers={"Content-Security-Policy": WORKBENCH_CSP},
    )


__all__ = ["resolve_static_dir", "router"]
