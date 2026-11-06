"""Integration tests for RemoteExecutor and SSE streaming follower (GAP-06, Decision D2, D6)."""

from __future__ import annotations

import argparse
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest
from starlette.testclient import TestClient

from opendv_cli import RemoteExecutor
from opendv_platform_api import PlatformStateContainer, create_app
from opendv_service import set_pipeline_runner_override
from opendv_testkit import build_deterministic_agy_sdk_shim, seed_canonical_pe_demo

REPO_ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(autouse=True)
def _setup_pipeline_shim() -> Generator[None, None, None]:
    from opendv_agent_harness.pipeline import run_agentic_vault_pipeline as orig_run

    def _shim(*args: Any, **kwargs: Any) -> Any:
        return orig_run(*args, **{**kwargs, "sdk_module": build_deterministic_agy_sdk_shim()})

    set_pipeline_runner_override(_shim)
    yield
    set_pipeline_runner_override(None)


def test_remote_executor_pipeline_run_flow(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Verify RemoteExecutor follows live SSE events and fetches final report."""
    state = PlatformStateContainer(state_dir=tmp_path / "state", workspace_root=REPO_ROOT)
    seed_canonical_pe_demo(state)
    app = create_app(platform_state=state, workspace_root=REPO_ROOT)

    client = TestClient(app)
    executor = RemoteExecutor(base_url="http://testserver", client=client)

    sample_input = str(
        REPO_ROOT / "examples/pipeline_samples/global_commerce_vault/pipeline_input.yaml"
    )
    sample_harness = str(
        REPO_ROOT / "examples/pipeline_samples/global_commerce_vault/harness_config.yaml"
    )

    args = argparse.Namespace(
        pipeline_action="run",
        input=sample_input,
        harness_config=sample_harness,
        initial_contract=None,
        output_dir=str(tmp_path / "out"),
        provider="agy",
        hold_policy="auto_approve_top_candidate",
        role="INTERNAL_MODELER",
        stream=True,
        stream_tokens=True,
        json=False,
        toon=False,
    )

    exit_code, payload = executor.execute("pipeline", args)
    assert exit_code == 0, f"Expected 0 exit code, got {exit_code}: {payload}"
    assert payload["mode"] == "remote"
    assert payload["status"] == "COMPLETED"
    assert payload["overall_passed"] is True

    captured = capsys.readouterr()
    assert "[Wave 0][STAGE 00] Starting STAGE_00_ORCHESTRATION" in captured.out
    assert "STAGE_00_ORCHESTRATION" in captured.out
    assert "-> PASS:" in captured.out or "-> COMPLETED:" in captured.out


def test_remote_executor_subcommands_parity(
    tmp_path: Path,
) -> None:
    """Verify RemoteExecutor executes profile, sandbox, run, and hold against REST endpoints."""
    state = PlatformStateContainer(state_dir=tmp_path / "state", workspace_root=REPO_ROOT)
    seed_canonical_pe_demo(state)
    app = create_app(platform_state=state, workspace_root=REPO_ROOT)

    client = TestClient(app)
    executor = RemoteExecutor(base_url="http://testserver", client=client)

    pe_contract = str(REPO_ROOT / "examples/private_equity/dv_pe_model_contract.yaml")
    pe_graph_a = str(REPO_ROOT / "examples/private_equity/pe_graph_a_ontology.yaml")
    pe_graph_b = str(REPO_ROOT / "examples/private_equity/pe_graph_b_profile.json")
    pe_ir = str(REPO_ROOT / "examples/private_equity/pe_canonical_ir.json")

    # 1. verify
    v_args = argparse.Namespace(
        contract=pe_contract,
        graph_a=pe_graph_a,
        graph_b=pe_graph_b,
        prior_contract=None,
        run_id=None,
        role="INTERNAL_MODELER",
    )
    rc, v_pay = executor.execute("verify", v_args)
    if rc == 0:
        assert v_pay["mode"] == "remote"
        assert v_pay["overall_passed"] is True
    else:
        assert rc == 2
        assert v_pay["status"] == "ERROR"

    # 2. profile
    p_args = argparse.Namespace(
        source=None,
        file=None,
        source_csv=None,
        graph_b=pe_graph_b,
        project_id="proj-apex-horizon-pe",
        source_system="BRONZE_SOURCE",
        table_name=None,
    )
    rc, p_pay = executor.execute("profile", p_args)
    assert rc == 0
    assert p_pay["mode"] == "remote"
    assert p_pay["status"] == "PASS"

    # 3. compile
    c_args = argparse.Namespace(
        contract=pe_contract,
        canonical_ir=pe_ir,
        run_id=None,
    )
    rc, c_pay = executor.execute("compile", c_args)
    if rc == 0:
        assert c_pay["mode"] == "remote"
        assert c_pay["status"] == "PASS"
        assert "canonical_ir_sha256" in c_pay
    else:
        assert rc == 2
        assert c_pay["status"] == "ERROR"

    # 4. sandbox
    s_args = argparse.Namespace(
        run_id="run-pe-apex-horizon-20261030",
        contract=pe_contract,
        canonical_ir=pe_ir,
        query_type="ADVERSARIAL_BATCHES_1_TO_4",
        cutoff="2026-11-15 23:59:59",
    )
    rc, s_pay = executor.execute("sandbox", s_args)
    assert rc == 0
    assert s_pay["mode"] == "remote"
    assert s_pay["all_batches_passed"] is True

    # 5. run list & get
    r_list_args = argparse.Namespace(
        run_action="list",
        project_id="proj-apex-horizon-pe",
        run_id=None,
    )
    rc, r_list = executor.execute("run", r_list_args)
    assert rc == 0
    assert r_list["mode"] == "remote"
    assert len(r_list["items"]) >= 1

    r_get_args = argparse.Namespace(
        run_action="get",
        project_id=None,
        run_id="run-pe-apex-horizon-20261030",
    )
    rc, r_get = executor.execute("run", r_get_args)
    assert rc == 0
    assert r_get["mode"] == "remote"
    assert r_get["run_id"] == "run-pe-apex-horizon-20261030"

    # 6. hold list
    h_args = argparse.Namespace(
        hold_action="list",
        run_id="run-pe-apex-horizon-20261030",
    )
    rc, h_pay = executor.execute("hold", h_args)
    assert rc == 0
    assert h_pay["mode"] == "remote"
    assert "active_holds" in h_pay


def test_remote_executor_exit_code_and_validation_errors(tmp_path: Path) -> None:
    """Verify RemoteExecutor matches exit code conventions (0 success, 1 reject, 2 error)."""
    state = PlatformStateContainer(state_dir=tmp_path / "state", workspace_root=REPO_ROOT)
    app = create_app(platform_state=state, workspace_root=REPO_ROOT)

    client = TestClient(app)
    executor = RemoteExecutor(base_url="http://testserver", client=client)

    # Missing contract flag
    rc, pay = executor.execute("verify", argparse.Namespace(contract=None))
    assert rc == 2
    assert pay["status"] == "ERROR"

    # Unknown command
    rc, pay = executor.execute("unknown_cmd", argparse.Namespace())
    assert rc == 2
    assert pay["status"] == "ERROR"

    # Missing input on pipeline run
    rc, pay = executor.execute("pipeline", argparse.Namespace(pipeline_action="run", input=None))
    assert rc == 2
    assert pay["status"] == "ERROR"
