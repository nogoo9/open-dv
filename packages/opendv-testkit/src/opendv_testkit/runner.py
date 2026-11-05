"""Pipeline runner seam test utilities and context managers."""

from __future__ import annotations

import contextlib
from collections.abc import Generator
from typing import Any

from opendv_agent_harness import AgenticPipelineReport
from opendv_agent_harness.pipeline import run_agentic_vault_pipeline
from opendv_service.seams import PipelineRunner, set_pipeline_runner_override
from opendv_testkit.shims import build_deterministic_agy_sdk_shim


def build_deterministic_pipeline_runner(
    sdk_shim: Any | None = None,
    provider: str = "agy",
) -> PipelineRunner:
    """Construct a PipelineRunner callable injecting a deterministic vendor SDK shim."""
    effective_shim = sdk_shim if sdk_shim is not None else build_deterministic_agy_sdk_shim()

    def _runner(*args: Any, **kwargs: Any) -> AgenticPipelineReport:
        merged = {**kwargs}
        if "sdk_module" not in merged:
            merged["sdk_module"] = effective_shim
        return run_agentic_vault_pipeline(*args, **merged)

    return _runner


make_deterministic_pipeline_runner = build_deterministic_pipeline_runner


@contextlib.contextmanager
def deterministic_pipeline_seam(
    sdk_shim: Any | None = None,
    provider: str = "agy",
) -> Generator[PipelineRunner, None, None]:
    """Context manager setting and safely resetting `set_pipeline_runner_override`."""
    runner = build_deterministic_pipeline_runner(sdk_shim=sdk_shim, provider=provider)
    set_pipeline_runner_override(runner)
    try:
        yield runner
    finally:
        set_pipeline_runner_override(None)
