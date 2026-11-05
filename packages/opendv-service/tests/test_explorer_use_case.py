"""Unit tests for Explorer bundle and run-isolated artifact use cases.

Conforms to GAP-05, T6, Decision D3, and User Directive R9 (<500 LOC / <20 KB).
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from opendv_contracts import ExplorerBundleDTO, RbacRole, RunArtifactDTO
from opendv_service.errors import NotFoundError
from opendv_service.models import CreateProjectRequest, CreateRunRequest, RunStatus
from opendv_service.state import StateStore
from opendv_service.use_cases.explorer import get_explorer_bundle, get_run_artifact
from opendv_service.use_cases.runs import list_runs


def _create_sample_store(tmp_path: Path) -> tuple[StateStore, str]:
    """Helper to initialize a StateStore with a project and run."""
    store = StateStore(storage_root=tmp_path / "storage", workspace_root=tmp_path)
    proj = store.create_project(
        CreateProjectRequest(
            project_id="test-explorer-proj",
            display_name="Explorer Test Project",
        )
    )
    run = store.create_run(
        CreateRunRequest(
            project_id=proj.project_id,
            harness_provider="antigravity",
        )
    )
    # Mark standard sample run as COMPLETED for default assertions
    store.runs[run.run_id] = run.model_copy(update={"status": RunStatus.COMPLETED})
    return store, run.run_id


def test_get_explorer_bundle_8_stages_and_roundtrip(tmp_path: Path) -> None:
    """Verify bundle contains strictly 8 canonical stages in order and lossless round-trip."""
    store, run_id = _create_sample_store(tmp_path)

    bundle = get_explorer_bundle(store, run_id)
    assert isinstance(bundle, ExplorerBundleDTO)

    # 1. Strictly 8 stages in exact order
    assert len(bundle.stages) == 8
    expected_stage_keys = [
        "STAGE_00_ORCHESTRATION",
        "STAGE_01_SCOUT_SOURCE",
        "STAGE_02_SCOUT_UNSTRUCTURED",
        "STAGE_03_SHIP_VAULT_PASS1",
        "STAGE_04_SHIP_VAULT_PASS2",
        "STAGE_05_AXI_DV_STATIC_GATES",
        "STAGE_06_DUCKDB_SANDBOX",
        "STAGE_07_DETERMINISTIC_COMPILER",
    ]
    for idx, expected_key in enumerate(expected_stage_keys):
        assert bundle.stages[idx].key == expected_key

    # 2. Phases and waves
    assert len(bundle.phases) == 4
    assert len(bundle.waves) == 4

    # 3. Artifact groupings
    assert bundle.setting_ids == ["harness", "runopts"]
    assert bundle.input_ids == ["spec", "ontology", "crm", "erp", "stripe", "crm2", "msa"]
    assert len(bundle.artifacts) == 24
    for art_id in bundle.setting_ids + bundle.input_ids + bundle.output_ids:
        assert art_id in bundle.artifacts

    # 4. Property access: snake_case and camelCase
    assert bundle.project_id == "test-explorer-proj"
    assert bundle.projectId == "test-explorer-proj"
    assert bundle.runId == run_id
    assert bundle.wavesCount == 4
    assert bundle.settingIds == bundle.setting_ids

    # 5. Lossless Pydantic serialization round-trip
    payload = bundle.model_dump(mode="json")
    restored = ExplorerBundleDTO.model_validate(payload)
    assert restored.run_id == bundle.run_id
    assert restored.stages[0].key == bundle.stages[0].key
    assert len(restored.artifacts) == len(bundle.artifacts)


def test_get_explorer_bundle_wave_filter(tmp_path: Path) -> None:
    """Verify wave filtering returns strict subset and handles aliases."""
    store, run_id = _create_sample_store(tmp_path)

    # Filter by W1
    bundle_w1 = get_explorer_bundle(store, run_id, wave_id="W1")
    for art in bundle_w1.artifacts.values():
        assert "W1" in art.waves

    # Numeric alias '1' maps to 'W1'
    bundle_1 = get_explorer_bundle(store, run_id, wave_id="1")
    assert set(bundle_1.artifacts.keys()) == set(bundle_w1.artifacts.keys())

    # Filter by W3
    bundle_w3 = get_explorer_bundle(store, run_id, wave_id="W3")
    assert "msa" in bundle_w3.artifacts
    assert "crm" not in bundle_w3.artifacts

    # Invalid wave raises NotFoundError
    with pytest.raises(NotFoundError, match="Wave 'W99' not found"):
        get_explorer_bundle(store, run_id, wave_id="W99")


def test_get_explorer_bundle_paused_hold_status(tmp_path: Path) -> None:
    """Verify PAUSED_HOLD run marks stage 2 as HOLD with extraction options."""
    store, run_id = _create_sample_store(tmp_path)

    # Put run into PAUSED_HOLD status
    run_dto = store.runs[run_id]
    store.runs[run_id] = run_dto.model_copy(
        update={
            "status": RunStatus.PAUSED_HOLD,
            "current_stage_id": "STAGE_02_SCOUT_UNSTRUCTURED",
            "active_hold_ids": ["hold-tier-u3-rheinland-precision"],
        }
    )

    bundle = get_explorer_bundle(store, run_id)
    assert "PAUSED" in bundle.status_label
    assert "PAUSED" in bundle.statusLabel

    stage_02 = next(s for s in bundle.stages if s.key == "STAGE_02_SCOUT_UNSTRUCTURED")
    assert stage_02.tag == "HOLD"

    extract_art = bundle.artifacts["extract"]
    assert extract_art.doc is not None
    hold_ext = next(e for e in extract_art.doc.extractions if e.tier == "Tier-U3")
    assert hold_ext.resolved is False
    assert len(hold_ext.candidates) > 0


def test_get_run_artifact_success_and_cas_integrity(tmp_path: Path) -> None:
    """Verify run-isolated artifact retrieval, 64-hex SHA-256 and CAS storage."""
    store, run_id = _create_sample_store(tmp_path)

    artifact = get_run_artifact(store, run_id, "harness")
    assert isinstance(artifact, RunArtifactDTO)
    assert artifact.artifact_id == "harness"
    assert artifact.run_id == run_id

    # Verify lowercase 64-hex SHA-256
    assert re.match(r"^[0-9a-f]{64}$", artifact.sha256) is not None

    # Verify stored in CAS
    assert store.cas_store.has(artifact.sha256)
    cas_content = store.cas_store.get_str(artifact.sha256)
    assert cas_content == artifact.content


def test_get_run_artifact_run_isolation(tmp_path: Path) -> None:
    """Verify T6 Invariant: cross-run artifact access or unknown run returns 404."""
    store, run_a = _create_sample_store(tmp_path)

    run_b_dto = store.create_run(
        CreateRunRequest(project_id="test-explorer-proj", harness_provider="antigravity")
    )
    run_b = run_b_dto.run_id

    # Run A has artifact 'harness'
    art_a = get_run_artifact(store, run_a, "harness")
    assert art_a.run_id == run_a

    # Requesting non-existent artifact under Run A raises NotFoundError
    with pytest.raises(NotFoundError, match="Artifact 'nonexistent' not found"):
        get_run_artifact(store, run_a, "nonexistent")

    # Requesting non-existent artifact under Run B raises NotFoundError
    with pytest.raises(NotFoundError, match="Artifact 'nonexistent' not found"):
        get_run_artifact(store, run_b, "nonexistent")

    # Unknown run raises NotFoundError
    with pytest.raises(NotFoundError, match="Run 'unknown-run' not found"):
        get_run_artifact(store, "unknown-run", "harness")


def test_get_run_artifact_rbac_redaction(tmp_path: Path) -> None:
    """Verify RBAC role masking for PII attributes."""
    store, run_id = _create_sample_store(tmp_path)

    art_public = get_run_artifact(store, run_id, "msa", role=RbacRole.PUBLIC)
    assert "<PII>" in art_public.content

    art_steward = get_run_artifact(store, run_id, "msa", role=RbacRole.DATA_STEWARD)
    assert "<PII>" not in art_steward.content


def test_list_runs_filtering(tmp_path: Path) -> None:
    """Verify list_runs supports status and stage_id filtering."""
    store, run_id = _create_sample_store(tmp_path)

    run2 = store.create_run(
        CreateRunRequest(project_id="test-explorer-proj", harness_provider="antigravity")
    )
    store.runs[run2.run_id] = run2.model_copy(
        update={
            "status": RunStatus.PAUSED_HOLD,
            "current_stage_id": "STAGE_02_SCOUT_UNSTRUCTURED",
        }
    )

    # Filter by status
    completed_runs = list_runs(store, status=RunStatus.COMPLETED)
    assert any(r.run_id == run_id for r in completed_runs)
    assert all(r.status == RunStatus.COMPLETED for r in completed_runs)

    paused_runs = list_runs(store, status=RunStatus.PAUSED_HOLD)
    assert any(r.run_id == run2.run_id for r in paused_runs)
    assert all(r.status == RunStatus.PAUSED_HOLD for r in paused_runs)

    # Filter by stage_id
    stage2_runs = list_runs(store, stage_id="STAGE_02_SCOUT_UNSTRUCTURED")
    assert any(r.run_id == run2.run_id for r in stage2_runs)
    assert all(r.current_stage_id == "STAGE_02_SCOUT_UNSTRUCTURED" for r in stage2_runs)
