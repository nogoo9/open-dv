"""FastAPI `/api/v1` routers and central registration for Open-DV Platform API."""

from __future__ import annotations

import importlib.util

from fastapi import FastAPI

from opendv_platform_api.routers import (
    artifacts,
    cas,
    compile,
    decisions,
    explorer,
    gates,
    health,
    ontology,
    ontology_studio_blueprint,
    ontology_studio_citations,
    ontology_studio_evidence,
    ontology_studio_exports,
    ontology_studio_logs,
    ontology_studio_proposals,
    ontology_studio_sessions,
    pipelines,
    platform_config,
    profile,
    projects,
    runs,
    sandbox,
    sources,
    verify,
    web_ui,
)


def register_api_routers(application: FastAPI, api_prefix: str = "/api/v1") -> None:
    """Register all versioned REST, SSE, and Web UI routers on `application`."""
    for mod in (
        health,
        platform_config,
        profile,
        compile,
        verify,
        projects,
        runs,
        pipelines,
        explorer,
        gates,
        sandbox,
        decisions,
        artifacts,
        cas,
        ontology,
        ontology_studio_sessions,
        ontology_studio_evidence,
        ontology_studio_proposals,
        ontology_studio_citations,
        ontology_studio_logs,
        ontology_studio_exports,
        ontology_studio_blueprint,
        sources,
    ):
        application.include_router(mod.router, prefix=api_prefix)

    if importlib.util.find_spec("opendv_platform_api.routers.dv_schema_explorer") is not None:
        from opendv_platform_api.routers import dv_schema_explorer

        application.include_router(dv_schema_explorer.router, prefix=api_prefix)

    application.include_router(web_ui.router)


__all__ = [
    "artifacts",
    "cas",
    "compile",
    "decisions",
    "explorer",
    "gates",
    "health",
    "ontology",
    "ontology_studio_blueprint",
    "ontology_studio_citations",
    "ontology_studio_evidence",
    "ontology_studio_exports",
    "ontology_studio_logs",
    "ontology_studio_proposals",
    "ontology_studio_sessions",
    "pipelines",
    "profile",
    "projects",
    "register_api_routers",
    "runs",
    "sandbox",
    "sources",
    "verify",
]
