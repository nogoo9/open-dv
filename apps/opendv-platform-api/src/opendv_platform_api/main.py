"""Headless Platform REST + SSE ASGI Server (`opendv_platform_api.main`)."""

from __future__ import annotations

import contextlib
import inspect
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from opendv_platform_api.exception_handlers import (
    STUDIO_API_PREFIX,
    build_problem_details_response,
    register_exception_handlers,
    register_middlewares,
)
from opendv_platform_api.job_supervisor import get_job_supervisor
from opendv_platform_api.routers import register_api_routers
from opendv_platform_api.studio_deps import MAX_BUNDLE_IMPORT_BYTES, MAX_SOURCE_UPLOAD_BYTES
from opendv_service import OpenDVService, OpenDVSettings, PlatformStateContainer

STUDIO_MAX_SOURCE_UPLOAD_BYTES = MAX_SOURCE_UPLOAD_BYTES
STUDIO_MAX_BUNDLE_IMPORT_BYTES = MAX_BUNDLE_IMPORT_BYTES


def _init_studio_agent_runner(
    active_service: OpenDVService, active_settings: OpenDVSettings
) -> Any:
    from opendv_agent_harness.models import HarnessConfig
    from opendv_service.studio.agent_runner import StudioAgentRunner
    from opendv_service.studio.agent_runner_helpers import DEFAULT_AGENT_TIMEOUT_SECONDS

    provider_str = os.environ.get("OPENDV_AGENT_PROVIDER", "agy").strip().lower()
    harness_cfg = HarnessConfig(
        provider=provider_str,
        workspace_dir=active_service.workspace_root,
        state_dir=(active_service.workspace_root / ".opendv" / "harness").resolve(),
        app_data_dir=getattr(active_settings, "app_data_dir", None)
        or (active_service.workspace_root / ".opendv").resolve(),
        save_dir=(active_service.workspace_root / ".opendv" / "runs").resolve(),
    )
    kwargs: dict[str, Any] = {
        "harness_config": harness_cfg,
        "timeout_seconds": float(
            os.environ.get("OPENDV_STUDIO_AGENT_TIMEOUT", str(DEFAULT_AGENT_TIMEOUT_SECONDS))
        ),
    }
    if "state_container" in inspect.signature(StudioAgentRunner.__init__).parameters:
        kwargs["state_container"] = active_service.state
    return StudioAgentRunner(**kwargs)


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
            is_server=True,
        )
        active_service._explicit_workspace = explicit_ws_configured
    else:
        active_state = PlatformStateContainer(
            workspace_root=active_settings.workspace_root,
            storage_root=active_settings.state_dir,
            settings=active_settings,
            is_server=True,
        )
        active_service = OpenDVService(
            state_store=active_state,
            workspace_root=active_settings.workspace_root,
            storage_root=active_settings.state_dir,
            settings=active_settings,
            is_server=True,
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

    application.state.studio_agent_runner = _init_studio_agent_runner(
        active_service, active_settings
    )

    cors_origins = list(active_settings.cors_origins or [])
    application.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=bool(cors_origins) and "*" not in cors_origins,
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
            "X-CAS-SHA256",
        ],
        expose_headers=["ETag", "Content-Disposition", "X-OpenDV-Role", "X-OpenDV-Principal"],
    )

    register_middlewares(application, active_settings)
    register_exception_handlers(application)
    register_api_routers(application, api_prefix="/api/v1")

    return application


def __getattr__(name: str) -> Any:
    """Lazy module attribute access for `app` allowing startup validation upon invocation."""
    if name == "app":
        return create_app()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "STUDIO_API_PREFIX",
    "STUDIO_MAX_BUNDLE_IMPORT_BYTES",
    "STUDIO_MAX_SOURCE_UPLOAD_BYTES",
    "build_problem_details_response",
    "create_app",
]
