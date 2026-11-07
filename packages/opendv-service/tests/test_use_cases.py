"""Unit tests for individual domain use cases in opendv_service.use_cases."""

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
    PipelineValidateRequest,
)
from opendv_service.state import StateStore
from opendv_service.use_cases.artifacts import get_artifact, store_artifact
from opendv_service.use_cases.compile import compile_pipeline
from opendv_service.use_cases.holds import list_holds
from opendv_service.use_cases.pipeline import validate_pipeline
from opendv_service.use_cases.profile import profile_pipeline
from opendv_service.use_cases.sandbox import execute_sandbox_query
from opendv_service.use_cases.verify import verify_pipeline


def test_compile_use_case() -> None:
    """Verify compile_pipeline use case."""
    pe_contract = "examples/private_equity/dv_pe_model_contract.yaml"
    resp = compile_pipeline(CompileRequest(contract_path=pe_contract))
    assert resp.status == "PASS"
    assert resp.hubs_compiled == 4
    assert resp.links_compiled == 5
    assert resp.satellites_compiled == 11
    assert resp.rendered is not None


def test_profile_use_case() -> None:
    """Verify profile_pipeline use case."""
    pe_graph_b = "examples/private_equity/pe_graph_b_profile.json"
    resp = profile_pipeline(ProfileRequest(graph_b_path=pe_graph_b))
    assert resp.status == "PASS"
    assert resp.table_count >= 5
    assert resp.rendered is not None


def test_profile_use_case_with_source_spec(tmp_path: Path) -> None:
    """Verify profile_pipeline with SourceSpec enforces server workspace containment."""
    data_file = tmp_path / "data.csv"
    data_file.write_text("id,val\n1,100\n2,200\n", encoding="utf-8")
    resp = profile_pipeline(
        ProfileRequest(source={"adapter": "file", "uri": str(data_file), "format": "csv"}),
        workspace_root=tmp_path,
    )
    assert resp.status == "PASS"
    assert resp.table_count == 1
    assert resp.column_count == 2


def test_verify_use_case() -> None:
    """Verify verify_pipeline use case."""
    pe_contract = "examples/private_equity/dv_pe_model_contract.yaml"
    pe_graph_a = "examples/private_equity/pe_graph_a_ontology.yaml"
    pe_graph_b = "examples/private_equity/pe_graph_b_profile.json"
    resp = verify_pipeline(
        VerifyRequest(
            contract_path=pe_contract,
            graph_a_path=pe_graph_a,
            graph_b_path=pe_graph_b,
        )
    )
    assert resp.overall_passed is True
    assert resp.status == "PASS"
    assert resp.rendered is not None
    assert resp.graph_a_sha256 is not None
    assert len(resp.graph_a_sha256) == 64
    assert resp.ontology_version == "1.0.0"


def test_verify_use_case_with_graph_a_source() -> None:
    """Verify verify_pipeline with graph_a_source SourceSpec dictionary."""
    pe_contract = "examples/private_equity/dv_pe_model_contract.yaml"
    pe_graph_a = "examples/private_equity/pe_graph_a_ontology.yaml"
    pe_graph_b = "examples/private_equity/pe_graph_b_profile.json"
    resp = verify_pipeline(
        VerifyRequest(
            contract_path=pe_contract,
            graph_a_source={"adapter": "file", "uri": pe_graph_a},
            graph_b_path=pe_graph_b,
        )
    )
    assert resp.overall_passed is True
    assert resp.status == "PASS"
    assert resp.graph_a_sha256 is not None
    assert len(resp.graph_a_sha256) == 64
    assert resp.ontology_version == "1.0.0"


def test_sandbox_use_case() -> None:
    """Verify execute_sandbox_query use case."""
    pe_contract = "examples/private_equity/dv_pe_model_contract.yaml"
    resp = execute_sandbox_query(
        SandboxQueryRequest(
            contract_path=pe_contract,
            query_type="ADVERSARIAL_BATCHES_1_TO_4",
            assertion_cutoff_ts="2026-11-15 23:59:59",
        )
    )
    assert resp.all_batches_passed is True
    assert len(resp.batches) == 4
    assert resp.rendered is not None


def test_pipeline_validation_use_case() -> None:
    """Verify validate_pipeline use case."""
    store = StateStore()
    sample_input = "examples/pipeline_samples/global_commerce_vault/pipeline_input.yaml"
    sample_harness = "examples/pipeline_samples/global_commerce_vault/harness_config.yaml"
    val = validate_pipeline(
        store,
        PipelineValidateRequest(
            input_spec_path=sample_input,
            harness_config_path=sample_harness,
            provider_override="agy",
        ),
    )
    assert val["stage"] == "INPUT_VALIDATION"
    assert val["passed"] is True


def test_holds_and_artifacts_use_cases(tmp_path: Path) -> None:
    """Verify holds and artifacts use cases."""
    store = StateStore(state_dir=tmp_path / "state")

    # Artifacts
    art_meta = store_artifact(
        store,
        ArtifactUploadRequest(content={"alpha": 123}, media_type="application/json"),
    )
    assert len(art_meta.sha256) == 64

    retrieved = get_artifact(store, art_meta.sha256)
    assert retrieved.sha256 == art_meta.sha256
    assert retrieved.content == {"alpha": 123}

    # Holds on seeded PE run
    from opendv_testkit import seed_canonical_pe_demo

    seed_canonical_pe_demo(store)
    pe_run_id = "run-pe-apex-horizon-20261030"
    holds = list_holds(store, pe_run_id)
    assert isinstance(holds, list)
