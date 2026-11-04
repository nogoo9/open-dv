"""Explicit test and LLM boundary seams for opendv-service (resolving LAY-02)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from opendv_agent_harness import (
    AgenticPipelineReport,
    HarnessSetupConfig,
    HarnessStreamEvent,
    PipelineInputSpec,
    PipelineStepReport,
)
from opendv_telemetry import ContentAddressableStore


class PipelineRunner(Protocol):
    """Protocol for executing an end-to-end agentic Data Vault 2.0 pipeline."""

    def __call__(
        self,
        input_spec: PipelineInputSpec | str | Path,
        harness_setup: HarnessSetupConfig | str | Path | None = None,
        *,
        output_dir: str | Path | None = None,
        base_dir: str | Path | None = None,
        cas_store: ContentAddressableStore | None = None,
        sdk_module: Any | None = None,
        harness: Any | None = None,
        on_stage_start: Callable[[int, str, str, str], None] | None = None,
        on_stage_complete: Callable[[int, PipelineStepReport], None] | None = None,
        on_stream_event: Callable[[HarnessStreamEvent], None] | None = None,
        **kwargs: Any,
    ) -> AgenticPipelineReport: ...


_CURRENT_PIPELINE_RUNNER: PipelineRunner | None = None


def get_default_pipeline_runner() -> PipelineRunner:
    """Return configured pipeline runner or default to production run_agentic_vault_pipeline."""
    if _CURRENT_PIPELINE_RUNNER is not None:
        return _CURRENT_PIPELINE_RUNNER
    from opendv_agent_harness.pipeline import run_agentic_vault_pipeline

    return run_agentic_vault_pipeline


def set_pipeline_runner_override(runner: PipelineRunner | None) -> None:
    """Global test hook allowing fixtures to inject deterministic shims without monkeypatching."""
    global _CURRENT_PIPELINE_RUNNER
    _CURRENT_PIPELINE_RUNNER = runner
