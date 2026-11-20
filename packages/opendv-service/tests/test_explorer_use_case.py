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
        assert art.wave_index == 1

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


def test_list_runs_populates_session_id_and_orders_newest_first(tmp_path: Path) -> None:
    """Bug 2b: list_runs enriches session_id from run_session_ids and sorts newest first."""
    store, run1_id = _create_sample_store(tmp_path)
    run2 = store.create_run(
        CreateRunRequest(project_id="test-explorer-proj", harness_provider="antigravity")
    )
    store.runs[run1_id] = store.runs[run1_id].model_copy(
        update={
            "started_at_utc": "2026-11-19T18:00:00Z",
            "completed_at_utc": "2026-11-19T18:05:00Z",
        }
    )
    store.runs[run2.run_id] = run2.model_copy(
        update={
            "status": RunStatus.COMPLETED,
            "started_at_utc": "2026-11-19T19:00:00Z",
            "completed_at_utc": "2026-11-19T19:05:00Z",
        }
    )
    store.run_session_ids[run2.run_id] = "s-session-alpha"

    runs = list_runs(store)
    assert runs[0].run_id == run2.run_id
    assert runs[0].session_id == "s-session-alpha"
    assert runs[1].run_id == run1_id
    assert runs[1].session_id is None


def test_resolve_cas_sources_list_preserves_table_name_and_format_from_manifest(
    tmp_path: Path,
) -> None:
    """Bug 2a: resolve_cas_sources_list preserves table_name and format from manifest/options."""
    import hashlib
    import json
    import sqlite3

    from opendv_service.studio.session_manager import StudioSessionManager
    from opendv_service.use_cases.pipeline_preflight import resolve_cas_sources_list

    ws = tmp_path / "ws"
    studio_root = ws / ".opendv" / "ontology_studio"
    ws.mkdir(parents=True, exist_ok=True)
    mgr = StudioSessionManager(studio_root=studio_root, workspace_root=ws)
    meta = mgr.create_session(session_id="s-cas-names", domain_namespace="wealth_mgmt")
    sdir = mgr.get_session_dir(meta.session_id)
    cas = mgr.get_cas(meta.session_id)

    csv_bytes = b"account_id,custodian\nA1,BNY\n"
    csv_sha = hashlib.sha256(csv_bytes).hexdigest().lower()
    cas.put(csv_bytes)

    db_file = tmp_path / "treasury.sqlite"
    with sqlite3.connect(db_file) as sconn:
        sconn.execute("CREATE TABLE positions (pos_id TEXT PRIMARY KEY)")
        sconn.execute("INSERT INTO positions VALUES ('P1')")
        sconn.commit()
    db_bytes = db_file.read_bytes()
    db_sha = hashlib.sha256(db_bytes).hexdigest().lower()
    cas.put(db_bytes)

    manifest_path = sdir / "inputs" / "manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(
            {
                "files": [
                    {
                        "source_id": f"src-{csv_sha[:16]}",
                        "filename": "ds02_w2_custody.csv",
                        "sha256": csv_sha,
                        "mime_type": "text/csv",
                    },
                    {
                        "source_id": f"src-{db_sha[:16]}",
                        "filename": "ds01_sec_treasury",
                        "sha256": db_sha,
                        "mime_type": "application/x-sqlite",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    store = StateStore(storage_root=ws / ".opendv" / "state", workspace_root=ws)
    resolved = resolve_cas_sources_list(
        [
            {
                "feed_id": f"src-{csv_sha[:16]}",
                "source_system": "ds02_w2_custody_csv",
                "uri": f"cas://{csv_sha}",
            },
            {
                "feed_id": f"src-{db_sha[:16]}",
                "source_system": "ds01_sec_treasury",
                "uri": f"cas://{db_sha}",
                "adapter": "sqlite",
            },
        ],
        store.cas_store,
        session_id=meta.session_id,
        state=store,
    )
    assert resolved[0]["table_name"] == "ds02_w2_custody"
    assert resolved[0]["format"] == "csv"
    assert resolved[0]["adapter"] == "file"
    assert resolved[1]["uri"].startswith("sqlite:///")
    assert resolved[1]["adapter"] == "sqlite"


def test_list_run_artifacts_returns_real_artifacts_without_dummy_stubs(tmp_path: Path) -> None:
    """BUG-3: list_run_artifacts returns real contract/IR/SQL without dummy stubs."""
    import json

    import yaml

    from opendv_service.use_cases.explorer import list_run_artifacts

    store = StateStore(storage_root=tmp_path / "storage", workspace_root=tmp_path)
    proj = store.create_project(
        CreateProjectRequest(project_id="proj-real-arts", display_name="Real Arts")
    )
    run_dir = tmp_path / "storage" / "pipeline_runs" / "run_0001"
    run_dir.mkdir(parents=True, exist_ok=True)
    contract_obj = {
        "contract_version": "5.0",
        "domain_namespace": "fixed_income_vault",
        "lifecycle_stage": "COLD_START_N1",
        "column_triage_ledger": [
            {
                "bronze_table": "bonds.trades",
                "column_name": "trade_id",
                "elm_classification": "BUSINESS_KEY",
                "target_dv_entity": "hub_bond_trade",
            }
        ],
        "hubs": [],
        "links": [],
        "satellites": [],
        "reference_tables": [],
        "unmapped_escalations": [],
    }
    ir_obj = {
        "ir_version": "1.0.0",
        "model_name": "fixed_income_vault",
        "hubs": [{"name": "hub_bond_trade", "hk_column": {"name": "hk_bond_trade"}}],
        "links": [],
        "satellites": [],
    }
    sql_str = "CREATE TABLE raw_vault.hub_bond_trade (hk_bond_trade CHAR(64));"
    fc_yaml = yaml.safe_dump(contract_obj, sort_keys=False)
    ir_json = json.dumps(ir_obj, indent=2)
    (run_dir / "final_contract.yaml").write_text(fc_yaml, encoding="utf-8")
    (run_dir / "final_canonical_ir.json").write_text(ir_json, encoding="utf-8")
    (run_dir / "final_four_zone.sql").write_text(sql_str, encoding="utf-8")

    run_id = "run-real-arts-1"
    created = store.create_run(
        CreateRunRequest(project_id=proj.project_id, harness_provider="antigravity")
    )
    store.runs.pop(created.run_id, None)
    store.runs[run_id] = created.model_copy(
        update={"run_id": run_id, "status": RunStatus.COMPLETED}
    )
    store.pipeline_reports[run_id] = {
        "run_id": run_id,
        "project_id": proj.project_id,
        "contract": contract_obj,
        "contract_content": fc_yaml,
        "four_zone_sql": {"duckdb": {"all_zones": sql_str}},
        "final_artifacts": {
            "final_contract": {"file_path": str(run_dir / "final_contract.yaml")}
        },
    }

    arts = {a.artifact_id: a for a in list_run_artifacts(store, run_id, role=RbacRole.DATA_STEWARD)}
    assert "fixed_income_vault" in arts["final_contract"].content
    assert "global_commerce_vault" not in arts["final_contract"].content
    assert "hub_bond_trade" in arts["final_ir"].content
    assert "hub_party" not in arts["final_ir"].content
    assert "hub_bond_trade" in arts["final_sql"].content
    assert "hub_customer" not in arts["final_sql"].content

    # Even for a bare run with no pipeline artifacts, no dummy stubs appear
    bare_run = store.create_run(
        CreateRunRequest(project_id=proj.project_id, harness_provider="antigravity")
    )
    bare_arts = {
        a.artifact_id: a
        for a in list_run_artifacts(store, bare_run.run_id, role=RbacRole.DATA_STEWARD)
    }
    assert "global_commerce_vault" not in bare_arts["final_contract"].content
    assert "hub_party" not in bare_arts["final_ir"].content
    assert "hub_customer" not in bare_arts["final_sql"].content

