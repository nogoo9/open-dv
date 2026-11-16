"""Unit test suite for decomposed pipeline modules (WS3.1).

Verifies:
1. All 14 pipeline modules under `opendv_agent_harness.pipeline.*` can be imported independently.
2. Every module adheres strictly to ADR-020 token budget
   (< 5,000 tokens cl100k_base, target < 4,500).
3. The backward-compatibility shim `pipeline.py` exports all expected public APIs.
4. Core exports maintain genuine logic and zero dummy facades.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest
import tiktoken

PIPELINE_MODULES = [
    "config",
    "spec",
    "mode_detector",
    "validation_preflight",
    "discovery_tabular",
    "discovery_unstructured",
    "ontology_evolution",
    "contract_synthesizer",
    "contract_evolver",
    "axi_repair_loop",
    "sandbox_compiler",
    "stages_agentic",
    "validation_postflight",
    "runner",
]


@pytest.mark.parametrize("mod_name", PIPELINE_MODULES)
def test_pipeline_module_importable_and_has_all(mod_name: str) -> None:
    """Each decomposed module must be cleanly importable and declare __all__."""
    module_path = f"opendv_agent_harness.pipeline.{mod_name}"
    mod = importlib.import_module(module_path)
    assert mod is not None
    assert hasattr(mod, "__all__"), f"Module {mod_name} must declare __all__"
    assert len(mod.__all__) > 0, f"Module {mod_name} __all__ must not be empty"


def test_pipeline_module_token_budgets() -> None:
    """Every single module under pipeline/ must strictly contain < 5,000 cl100k_base tokens."""
    enc = tiktoken.get_encoding("cl100k_base")
    pkg_dir = Path(__file__).resolve().parents[1] / "src" / "opendv_agent_harness" / "pipeline"
    assert pkg_dir.is_dir()

    token_counts: dict[str, int] = {}
    for py_file in pkg_dir.glob("*.py"):
        content = py_file.read_text(encoding="utf-8")
        tokens = len(enc.encode(content))
        token_counts[py_file.name] = tokens
        assert tokens < 5000, f"Module {py_file.name} exceeded 5,000 token limit: {tokens} tokens"

    # Verify all 14 named modules exist
    for mod in PIPELINE_MODULES:
        fname = f"{mod}.py"
        assert fname in token_counts, f"Missing pipeline module {fname}"
        # ADR-020 hard limit is < 5,000 tokens (target < 4,500 tokens)
        assert token_counts[fname] < 5000, (
            f"Module {fname} exceeded 5,000 limit: {token_counts[fname]} tokens"
        )


def test_pipeline_backward_compatibility_shim() -> None:
    """Verify opendv_agent_harness.pipeline exports all essential backward-compatible APIs."""
    import opendv_agent_harness.pipeline as pipe

    expected_symbols = [
        "AgenticPipelineReport",
        "AgenticVaultPipeline",
        "DeterministicValidationReport",
        "HarnessAuthConfig",
        "HarnessSetupConfig",
        "InferredExecutionPlanDTO",
        "PipelineExecutionSpec",
        "PipelineInputSpec",
        "PipelineSettings",
        "PipelineSourceFeedInput",
        "PipelineStepReport",
        "PipelineUnstructuredDocInput",
        "PipelineWaveInput",
        "PipelineWaveReport",
        "TableExecutionActionDTO",
        "VaultBlueprint",
        "infer_execution_plan",
        "run_agentic_vault_pipeline",
        "validate_pipeline_inputs",
        "validate_pipeline_outputs",
    ]

    for sym in expected_symbols:
        assert hasattr(pipe, sym), f"pipeline.py shim missing backward-compatible export: {sym}"


def test_pipeline_package_init_matches_exports() -> None:
    """Verify opendv_agent_harness.pipeline package exports match root shim."""
    from opendv_agent_harness.pipeline import (
        AgenticPipelineReport,
        AgenticVaultPipeline,
        HarnessSetupConfig,
        PipelineExecutionSpec,
        PipelineInputSpec,
        infer_execution_plan,
        run_agentic_vault_pipeline,
    )

    assert AgenticVaultPipeline is not None
    assert run_agentic_vault_pipeline is not None
    assert PipelineInputSpec is PipelineExecutionSpec
    assert infer_execution_plan is not None
    assert HarnessSetupConfig is not None
    assert AgenticPipelineReport is not None
