"""FastAPI `/api/v1` routers for Open-DV Platform API."""

from opendv_platform_api.routers import (
    artifacts,
    cas,
    compile,
    decisions,
    explorer,
    gates,
    health,
    ontology,
    pipelines,
    profile,
    projects,
    runs,
    sandbox,
    sources,
    verify,
)

__all__ = [
    "artifacts",
    "cas",
    "compile",
    "decisions",
    "explorer",
    "gates",
    "health",
    "ontology",
    "pipelines",
    "profile",
    "projects",
    "runs",
    "sandbox",
    "sources",
    "verify",
]
