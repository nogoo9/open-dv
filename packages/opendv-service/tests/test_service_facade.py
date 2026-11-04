"""Unit tests for the OpenDVService facade class."""

from __future__ import annotations

from pathlib import Path

from opendv_contracts import (
    CompileRequest,
    ProfileRequest,
    SandboxQueryRequest,
    VerifyRequest,
)
from opendv_service.models import (
    ArtifactUploadRequest,
    CreateProjectRequest,
    CreateRunRequest,
    PipelineValidateRequest,
)
from opendv_service.service import OpenDVService
from opendv_service.state import StateStore


def test_service_facade_all_methods(tmp_path: Path) -> None:
    """Verify that OpenDVService facade coordinates all domain use cases."""
    store = StateStore(state_dir=tmp_path / "state")
    service = OpenDVService(state_store=store)

    # 1. Profile
    prof_resp = service.profile_pipeline(
        ProfileRequest(graph_b_path="examples/private_equity/pe_graph_b_profile.json")
    )
    assert prof_resp.status == "PASS"

    # 2. Compile
    comp_resp = service.compile_pipeline(
        CompileRequest(contract_path="examples/private_equity/dv_pe_model_contract.yaml")
    )
    assert comp_resp.status == "PASS"

    # 3. Verify
    ver_resp = service.verify_pipeline(
        VerifyRequest(
            contract_path="examples/private_equity/dv_pe_model_contract.yaml",
            graph_a_path="examples/private_equity/pe_graph_a_ontology.yaml",
            graph_b_path="examples/private_equity/pe_graph_b_profile.json",
        )
    )
    assert ver_resp.overall_passed is True

    # 4. Sandbox
    sbx_resp = service.execute_sandbox_query(
        SandboxQueryRequest(
            contract_path="examples/private_equity/dv_pe_model_contract.yaml",
            query_type="ADVERSARIAL_BATCHES_1_TO_4",
            assertion_cutoff_ts="2026-11-15 23:59:59",
        )
    )
    assert sbx_resp.all_batches_passed is True

    # 5. Pipeline validate
    val_res = service.validate_pipeline(
        PipelineValidateRequest(
            input_spec_path="examples/pipeline_samples/global_commerce_vault/pipeline_input.yaml",
            harness_config_path="examples/pipeline_samples/global_commerce_vault/harness_config.yaml",
        )
    )
    assert val_res["passed"] is True

    # 6. Projects & Runs
    proj = service.create_project(
        CreateProjectRequest(project_id="facade-proj", display_name="Facade Project")
    )
    assert proj.project_id == "facade-proj"
    assert service.get_project("facade-proj").project_id == "facade-proj"

    run = service.create_run(
        CreateRunRequest(project_id="facade-proj", harness_provider="antigravity")
    )
    assert run.project_id == "facade-proj"
    assert service.get_run(run.run_id).run_id == run.run_id
    runs = service.list_runs(project_id="facade-proj")
    assert len(runs) >= 1

    # 7. Artifacts
    art = service.store_artifact(
        ArtifactUploadRequest(content={"message": "hello facade"})
    )
    assert len(art.sha256) == 64
    retrieved = service.get_artifact(art.sha256)
    assert retrieved.content == {"message": "hello facade"}

    # 8. Holds & Scorecard
    pe_run_id = "run-pe-apex-horizon-20261030"
    scorecard = service.get_scorecard(pe_run_id)
    assert scorecard is not None
    report = service.get_pipeline_report(pe_run_id)
    assert report is not None
