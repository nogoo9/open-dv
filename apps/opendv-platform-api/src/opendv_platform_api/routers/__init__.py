"""FastAPI `/api/v1` routers for Open-DV Platform API."""

from opendv_platform_api.routers import (
    artifacts,
    compile,
    decisions,
    explorer,
    gates,
    health,
    ontology,
    profile,
    projects,
    runs,
    sandbox,
    verify,
)

__all__ = [
    "artifacts",
    "compile",
    "decisions",
    "explorer",
    "gates",
    "health",
    "ontology",
    "profile",
    "projects",
    "runs",
    "sandbox",
    "verify",
]
