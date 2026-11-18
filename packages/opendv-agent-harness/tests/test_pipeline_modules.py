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
from typing import Any

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


def test_blueprint_evolution_diff_emits_golden_compatible_add_relationship_op() -> None:
    """Evolution diff relationship ops must retain exact GT-01 golden schema keys."""
    from opendv_agent_harness.pipeline.blueprint_lineage import (
        compute_blueprint_evolution_diff,
    )

    diff = compute_blueprint_evolution_diff(
        prior_contract={"hubs": [], "links": [], "satellites": []},
        new_contract={
            "hubs": [],
            "links": [
                {
                    "name": "lnk_custody_account_security",
                    "hub_references": [
                        {"hub": "hub_custody_account"},
                        {"hub": "hub_security"},
                    ],
                }
            ],
            "satellites": [],
        },
        ontology={"canonical_business_concepts": [], "canonical_relationships": []},
    )
    rel_ops = [
        op for op in diff["ontology_deltas_applied"] if op.get("op_type") == "add_relationship"
    ]
    assert len(rel_ops) == 1
    assert set(rel_ops[0].keys()) == {
        "op_type",
        "op_id",
        "relationship_uri",
        "relationship_name",
        "target_link",
        "endpoints",
    }
    assert rel_ops[0]["target_link"] == "lnk_custody_account_security"
    assert rel_ops[0]["endpoints"] == [
        "urn:ontology:entity:custody_account",
        "urn:ontology:entity:security",
    ]


def test_axi_repair_loop_expands_studio_placeholder_grain_and_reject_wave_name(
    tmp_path: Path,
) -> None:
    """Verify Studio 3-word relationship placeholders pass G1 while genuinely short grain still
    triggers DV-G1-GRAIN, and single-run Stage 05 REJECT-WITH-TRACE populates wave_name cleanly.
    """
    import datetime
    from types import SimpleNamespace

    from opendv_agent_harness.pipeline.axi_repair_loop import (
        execute_axi_repair_loop,
        execute_stage_05_static_gates,
    )
    from opendv_agent_harness.pipeline.spec import DeterministicValidationReport
    from opendv_core.models.graph_a import (
        CanonicalBusinessConcept,
        CanonicalRelationship,
        GraphAOntology,
    )

    concept_a = CanonicalBusinessConcept(
        concept_uri="urn:ontology:entity:a",
        concept_name="Entity A",
        elm_archetype="CORE_CONCEPT",
        definition="Canonical business entity representing A for unit verification.",
        target_hub="hub_a",
    )
    contract = {
        "hubs": [],
        "links": [],
        "satellites": [],
        "column_triage_ledger": [],
    }
    placeholder_ont = GraphAOntology(
        ontology_version="1.1.0",
        domain_namespace="asset_management",
        enterprise_owner="Enterprise Architecture",
        canonical_business_concepts=[concept_a],
        canonical_relationships=[
            CanonicalRelationship(
                relationship_uri="urn:ontology:relationship:a_b",
                relationship_name="A B",
                target_link="lnk_a_b",
                endpoints=["urn:ontology:entity:a", "urn:ontology:entity:b"],
                grain_semantics="Unit-of-work link lnk_a_b.",
            )
        ],
    )
    _, res_ok, _ = execute_axi_repair_loop(
        candidate_contract=contract,
        ontology=placeholder_ont,
        bronze_profiles={},
        run_id="run-placeholder-test",
    )
    assert not any("DV-G1-GRAIN" in d for d in res_ok.diagnostics)

    bad_ont = GraphAOntology(
        ontology_version="1.1.0",
        domain_namespace="asset_management",
        enterprise_owner="Enterprise Architecture",
        canonical_business_concepts=[concept_a],
        canonical_relationships=[
            CanonicalRelationship(
                relationship_uri="urn:ontology:relationship:a_b",
                relationship_name="A B",
                target_link="lnk_a_b",
                endpoints=["urn:ontology:entity:a", "urn:ontology:entity:b"],
                grain_semantics="Too short",
            )
        ],
    )
    _, res_bad, _ = execute_axi_repair_loop(
        candidate_contract=contract,
        ontology=bad_ont,
        bronze_profiles={},
        run_id="run-short-test",
    )
    assert any("DV-G1-GRAIN" in d for d in res_bad.diagnostics)

    fake_runner = SimpleNamespace(
        run_id="run-stage05-reject-wave-name",
        ontology=bad_ont,
        input_spec=SimpleNamespace(
            waves=[],
            project_id="default-project",
            domain_namespace="asset_management",
            pipeline_id="pl_test",
        ),
        harness_setup=SimpleNamespace(max_repair_retries=1, provider="agy"),
        out_dir=tmp_path,
        _notify_stage_complete=lambda *_a, **_kw: None,
        _last_axi_res=None,
    )
    step_reports: list[Any] = []
    wave_reports: list[Any] = []
    _, rej = execute_stage_05_static_gates(
        runner=fake_runner,
        w_idx=0,
        w_type="COLD_START_N1",
        contract_dict=contract,
        curr_contract=None,
        s1_profiles={},
        s05_id="STAGE_05_AXI_DV_STATIC_GATES",
        s05_persona="axi-dv",
        s05_model="gemini-3.8-flash-high",
        is_fault_injected=False,
        codemods=[],
        drift_findings=[],
        wave_steps=[],
        step_reports=step_reports,
        wave_reports=wave_reports,
        artifacts=[],
        t0=datetime.datetime.now(datetime.UTC),
        preflight=DeterministicValidationReport(stage="INPUT_VALIDATION", passed=True),
    )
    assert rej is not None
    assert len(wave_reports) == 1
    assert wave_reports[0].wave_name == "Wave 0"
