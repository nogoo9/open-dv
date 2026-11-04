"""Runs and projects use case: Lifecycle management for projects and runs."""

from __future__ import annotations

from opendv_service.errors import NotFoundError
from opendv_service.models import (
    CreateProjectRequest,
    CreateRunRequest,
    ProjectResponse,
    ProjectRunDTO,
)
from opendv_service.state import StateStore


def create_project(state: StateStore, request: CreateProjectRequest) -> ProjectResponse:
    """Register and initialize a Data Vault 2.0 project in the platform state store."""
    return state.create_project(request)


def get_project(state: StateStore, project_id: str) -> ProjectResponse:
    """Retrieve details for a registered project."""
    if project_id not in state.projects:
        raise NotFoundError(f"Project '{project_id}' not found.")
    return state.projects[project_id]


def list_runs(state: StateStore, project_id: str | None = None) -> list[ProjectRunDTO]:
    """List execution runs, optionally filtered by project_id."""
    runs = list(state.runs.values())
    if project_id is not None:
        runs = [r for r in runs if r.project_id == project_id]
    return runs


def get_run(state: StateStore, run_id: str) -> ProjectRunDTO:
    """Retrieve the descriptor and gate scorecard for an execution run."""
    if run_id not in state.runs:
        raise NotFoundError(f"Run '{run_id}' not found.")
    return state.runs[run_id]


def create_run(state: StateStore, request: CreateRunRequest) -> ProjectRunDTO:
    """Initiate a verification, compilation, or sandbox execution run."""
    return state.create_run(request)


def abort_run(state: StateStore, run_id: str) -> ProjectRunDTO:
    """Abort an in-progress or paused execution run."""
    return state.abort_run(run_id)


__all__ = [
    "abort_run",
    "create_project",
    "create_run",
    "get_project",
    "get_run",
    "list_runs",
]
