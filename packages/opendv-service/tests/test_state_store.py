"""Unit tests for domain StateStore and CAS operations."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from opendv_service.models import CreateProjectRequest, CreateRunRequest
from opendv_service.state import StateStore, store_cas_artifact


def test_state_store_initialization_and_seeding(tmp_path: Path) -> None:
    """Verify StateStore initializes and seeds PE demo data."""
    store = StateStore(state_dir=tmp_path / "state")
    assert "proj-apex-horizon-pe" in store.projects
    assert "run-pe-apex-horizon-20261030" in store.runs
    assert "run-pe-apex-horizon-20261030" in store.run_events
    assert len(store.run_events["run-pe-apex-horizon-20261030"]) >= 9


def test_cas_artifact_storage_and_integrity(tmp_path: Path) -> None:
    """Verify Content-Addressable Storage and hash addressing."""
    store = StateStore(state_dir=tmp_path / "state")
    data = {"project": "alpha", "metric": 42}
    sha = store_cas_artifact(store.cas_store.base_dir, json.dumps(data))
    assert len(sha) == 64
    assert store.cas_store.has(sha)


def test_project_and_run_lifecycle(tmp_path: Path) -> None:
    """Verify project creation, run creation, and run abortion."""
    store = StateStore(state_dir=tmp_path / "state")
    proj = store.create_project(
        CreateProjectRequest(
            project_id="test-proj-lifecycle",
            display_name="Lifecycle Project",
        )
    )
    assert proj.project_id == "test-proj-lifecycle"

    run = store.create_run(
        CreateRunRequest(
            project_id="test-proj-lifecycle",
            harness_provider="antigravity",
        )
    )
    assert run.project_id == "test-proj-lifecycle"
    assert run.status.value in {"COMPLETED", "RUNNING", "PAUSED_HOLD"}

    # Abort run
    aborted = store.abort_run(run.run_id)
    assert aborted.status.value == "ABORTED"

    with pytest.raises((ValueError, KeyError)):
        store.abort_run("nonexistent-run-id")
