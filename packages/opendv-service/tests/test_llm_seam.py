"""Unit tests for the PipelineRunner seam and test double injection (LAY-02)."""

from __future__ import annotations

from opendv_service.models import PipelineRunRequest
from opendv_service.seams import (
    get_default_pipeline_runner,
    set_pipeline_runner_override,
)
from opendv_service.service import OpenDVService


def test_pipeline_runner_default_and_override() -> None:
    """Verify default runner retrieval, override setting, and clearing."""
    try:
        default_runner = get_default_pipeline_runner()
        assert callable(default_runner)

        from opendv_agent_harness.adapters.deterministic_adapter import (
            build_deterministic_agy_sdk_shim,
        )
        from opendv_agent_harness.pipeline import run_agentic_vault_pipeline

        called = False

        def shim_runner(*args, **kwargs):
            nonlocal called
            called = True
            return run_agentic_vault_pipeline(
                *args,
                **{**kwargs, "sdk_module": build_deterministic_agy_sdk_shim()},
            )

        set_pipeline_runner_override(shim_runner)
        assert get_default_pipeline_runner() is shim_runner

        service = OpenDVService()
        res = service.execute_pipeline_run_sync(
            PipelineRunRequest(
                input_spec_path="examples/pipeline_samples/global_commerce_vault/pipeline_input.yaml",
                harness_config_path="examples/pipeline_samples/global_commerce_vault/harness_config.yaml",
                provider_override="agy",
            )
        )
        assert called is True
        assert res["status"] == "COMPLETED"
    finally:
        set_pipeline_runner_override(None)
        assert get_default_pipeline_runner() is not None
