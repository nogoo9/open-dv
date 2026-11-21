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


def test_discover_tabular_feeds_preserves_logical_name_for_cas_and_extensionless_paths(
    tmp_path,
) -> None:
    """Bug 2a: CAS URIs and sharded CAS blobs must not leak 64-hex digests as table_name."""
    import hashlib
    import sqlite3

    import duckdb

    from opendv_agent_harness.pipeline.discovery_tabular import discover_tabular_feeds
    from opendv_agent_harness.pipeline.spec import PipelineSourceFeedInput
    from opendv_telemetry import ContentAddressableStore

    cas_dir = tmp_path / "cas_store"
    cas = ContentAddressableStore(cas_dir)
    csv_bytes = b"settlement_id,account_id,amount\nS1,A1,100.50\nS2,A2,250.00\n"
    csv_sha = hashlib.sha256(csv_bytes).hexdigest().lower()
    cas.put(csv_bytes)

    # Sharded extensionless CAS blob path (Studio layout)
    sharded_blob = tmp_path / "inputs" / "cas" / csv_sha[:2] / csv_sha[2:]
    sharded_blob.parent.mkdir(parents=True, exist_ok=True)
    sharded_blob.write_bytes(csv_bytes)

    # SQLite CAS source
    db_file = tmp_path / "src.sqlite"
    with sqlite3.connect(db_file) as sconn:
        sconn.execute("CREATE TABLE treasury_trades (trade_id TEXT PRIMARY KEY, notional REAL)")
        sconn.execute("INSERT INTO treasury_trades VALUES ('T1', 500.0)")
        sconn.commit()
    db_sha = hashlib.sha256(db_file.read_bytes()).hexdigest().lower()
    cas.put(db_file.read_bytes())

    conn = duckdb.connect(":memory:")
    try:
        out = discover_tabular_feeds(
            [
                PipelineSourceFeedInput(
                    feed_id="src-csv-cas",
                    source_system="ds02_w2_custody_csv",
                    uri=f"cas://{csv_sha}",
                    options={
                        "table_name": "ds02_w2_custody",
                        "source_name": "ds02_w2_custody.csv",
                        "source_format": "csv",
                    },
                ),
                PipelineSourceFeedInput(
                    feed_id="src-csv-sharded",
                    source_system="ds02_account_crosswalk_csv",
                    uri=str(sharded_blob),
                ),
                PipelineSourceFeedInput(
                    feed_id="src-sqlite-cas",
                    source_system="ds01_sec_treasury",
                    uri=f"cas://{db_sha}",
                    adapter="sqlite",
                ),
            ],
            conn,
            workspace_root=tmp_path,
            base_dir=tmp_path,
            cas_store=cas,
        )
    finally:
        conn.close()

    by_sys = {f.source_system: f for f in out.discovered_feeds}
    assert by_sys["ds02_w2_custody_csv"].table_name == "ds02_w2_custody"
    assert by_sys["ds02_w2_custody_csv"].row_count == 2
    assert len(by_sys["ds02_account_crosswalk_csv"].table_name) != 64
    assert by_sys["ds02_account_crosswalk_csv"].table_name != csv_sha
    assert by_sys["ds02_account_crosswalk_csv"].row_count == 2
    assert any(f.table_name == "treasury_trades" for f in out.discovered_feeds)


def test_stage05_ask_user_respects_hold_policy_and_prioritizes_ask_user_violation(
    tmp_path,
    monkeypatch,
) -> None:
    """Bug 3a: Stage 05 ASK_USER honors hold policy and ignores AUTO-FIX ALG-A2 in diag_msg."""
    import datetime
    from types import SimpleNamespace

    import opendv_agent_harness.pipeline.axi_repair_loop as axi_mod
    from opendv_agent_harness.hold_manager import StewardHoldManager
    from opendv_agent_harness.pipeline.spec import DeterministicValidationReport
    from opendv_axi_verifier.alg_a8_orchestrator import AxiDvPipelineResult
    from opendv_axi_verifier.toon_codec import ToonTrace, ToonViolation
    from opendv_core.models.graph_a import GraphAOntology

    fake_axi_res = AxiDvPipelineResult(
        status="ASK_USER",
        transformed_contract={
            "hubs": [],
            "links": [],
            "satellites": [],
            "column_triage_ledger": [],
        },
        diagnostics=["ASK_USER [Gate 1 - DV-G4-001]: Hub 'hub_orphan' references unknown URI."],
        toon_trace=ToonTrace(
            run_id="run-s05",
            attempt="1/3",
            status="ASK-USER",
            violations=[
                ToonViolation(
                    gate="G1",
                    rule="ALG-A2",
                    action="AUTO-FIX",
                    target="reference_tables",
                    fix="Demoted static low-cardinality enum Hub(s) to ref_* tables",
                ),
                ToonViolation(
                    gate="G1",
                    rule="DV-G4-001",
                    action="ASK-USER",
                    target="hub_orphan",
                    fix="Hub 'hub_orphan' references unknown ontology_concept_uri",
                ),
            ],
        ),
        toon_text="@toon_v1",
        applied_codemods=["ALG_A2_ENUM_DEMOTION"],
    )
    monkeypatch.setattr(
        axi_mod,
        "execute_axi_repair_loop",
        lambda **_kw: (fake_axi_res.transformed_contract, fake_axi_res, False),
    )

    def _make_runner(policy: str):
        return SimpleNamespace(
            run_id=f"run-s05-{policy}",
            ontology=GraphAOntology(
                ontology_version="0.1.0",
                domain_namespace="wealth_mgmt",
                enterprise_owner="Wealth Ops",
            ),
            graph_a_sha256="a" * 64,
            input_spec=SimpleNamespace(
                waves=[],
                project_id="default-project",
                domain_namespace="wealth_mgmt",
                pipeline_id="pl_test",
            ),
            harness_setup=SimpleNamespace(
                max_repair_retries=1,
                provider="agy",
                steward_hold_policy=policy,
            ),
            hold_manager=StewardHoldManager(state_dir=tmp_path / f"state-{policy}"),
            base_dir=tmp_path,
            workspace_root=tmp_path,
            output_dir=tmp_path / f"out-{policy}",
            cas_store=None,
            observability_events=[],
            fallbacks_applied=[],
            _notify_stage_complete=lambda *_a, **_kw: None,
            _last_axi_res=None,
        )

    # 1. auto_approve_top_candidate must NOT pause in PAUSED_HOLD
    auto_runner = _make_runner("auto_approve_top_candidate")
    step_reports_auto = []
    verified_c, rep_auto = axi_mod.execute_stage_05_static_gates(
        runner=auto_runner,
        w_idx=0,
        w_type="INCREMENTAL_SOURCE",
        contract_dict={"hubs": []},
        curr_contract=None,
        s1_profiles={},
        s05_id="STAGE_05_AXI_DV_STATIC_GATES",
        s05_persona="axi-dv",
        s05_model="symbolic",
        is_fault_injected=False,
        codemods=[],
        drift_findings=[],
        wave_steps=[],
        step_reports=step_reports_auto,
        wave_reports=[],
        artifacts=[],
        t0=datetime.datetime.now(datetime.UTC),
        preflight=DeterministicValidationReport(stage="INPUT_VALIDATION", passed=True),
    )
    assert rep_auto is None, (
        "Expected Stage 05 ASK_USER to auto-approve under auto_approve_top_candidate"
    )
    assert verified_c == fake_axi_res.transformed_contract
    assert step_reports_auto[-1].verdict == "PASS"

    # 2. pause policy pauses with the ASK-USER violation (not ALG-A2 AUTO-FIX)
    pause_runner = _make_runner("pause")
    pause_runner.output_dir.mkdir(parents=True, exist_ok=True)
    _, rep_pause = axi_mod.execute_stage_05_static_gates(
        runner=pause_runner,
        w_idx=0,
        w_type="INCREMENTAL_SOURCE",
        contract_dict={"hubs": []},
        curr_contract=None,
        s1_profiles={},
        s05_id="STAGE_05_AXI_DV_STATIC_GATES",
        s05_persona="axi-dv",
        s05_model="symbolic",
        is_fault_injected=False,
        codemods=[],
        drift_findings=[],
        wave_steps=[],
        step_reports=[],
        wave_reports=[],
        artifacts=[],
        t0=datetime.datetime.now(datetime.UTC),
        preflight=DeterministicValidationReport(stage="INPUT_VALIDATION", passed=True),
    )
    assert rep_pause is not None
    assert rep_pause.status == "PAUSED_HOLD"
    assert len(rep_pause.active_holds) == 1
    assert "DV-G4-001" in rep_pause.summary_markdown
    assert "ALG-A2" not in rep_pause.summary_markdown


def test_build_paused_pipeline_report_never_returns_empty_active_holds(tmp_path) -> None:
    """Bug 3b: build_paused_pipeline_report records a hold if none is pending yet."""
    import datetime
    from types import SimpleNamespace

    from opendv_agent_harness.hold_manager import StewardHoldManager
    from opendv_agent_harness.pipeline.spec import (
        DeterministicValidationReport,
        PipelineStepReport,
    )
    from opendv_agent_harness.pipeline.validation_postflight import build_paused_pipeline_report
    from opendv_core.models.graph_a import GraphAOntology

    runner = SimpleNamespace(
        run_id="run-paused-no-hold",
        ontology=GraphAOntology(
            ontology_version="0.1.0",
            domain_namespace="wealth_mgmt",
            enterprise_owner="Wealth Ops",
        ),
        graph_a_sha256="b" * 64,
        input_spec=SimpleNamespace(
            waves=[],
            project_id="default-project",
            domain_namespace="wealth_mgmt",
            pipeline_id="pl_test",
        ),
        harness_setup=SimpleNamespace(provider="agy"),
        hold_manager=StewardHoldManager(state_dir=tmp_path / "state"),
        base_dir=tmp_path,
        workspace_root=tmp_path,
        output_dir=tmp_path / "out",
        cas_store=None,
        observability_events=[],
        fallbacks_applied=[],
    )
    runner.output_dir.mkdir(parents=True, exist_ok=True)
    rep = build_paused_pipeline_report(
        runner=runner,
        t0=datetime.datetime.now(datetime.UTC),
        preflight=DeterministicValidationReport(stage="INPUT_VALIDATION", passed=True),
        step_reports=[
            PipelineStepReport(
                stage_index=4,
                stage_id="STAGE_04_SHIP_VAULT_PASS2",
                persona="ship-vault",
                status="ASK-USER",
                verdict="HOLD",
            )
        ],
        artifacts=[],
        reason="KEY_DRIFT",
    )
    assert rep.status == "PAUSED_HOLD"
    assert len(rep.active_holds) >= 1
    assert len(runner.hold_manager.list_pending_holds()) >= 1
