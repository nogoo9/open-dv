"""Unit tests for mode_detector.py (7-row data-driven inference matrix).

Tests that the engine correctly infers execution plans and per-table/per-doc actions
without requiring user wave_type (ADR-018 / WS-3).
"""

from __future__ import annotations

import pytest

from opendv_agent_harness.pipeline.mode_detector import (
    infer_execution_plan,
)
from opendv_agent_harness.pipeline.spec import (
    PipelineSourceFeedInput,
    PipelineUnstructuredDocInput,
)
from opendv_core.models.contract_v5 import (
    BusinessKeySource,
    ColumnTriageEntry,
    ContractHubSpec,
    ContractSatelliteSpec,
    NoMistakeDataVault2ContractV5,
)
from opendv_core.models.graph_a import GraphAOntology
from opendv_core.models.vault_blueprint import create_vault_blueprint


@pytest.fixture
def empty_ontology() -> GraphAOntology:
    return GraphAOntology(
        ontology_version="0.1.0",
        domain_namespace="general",
    )


@pytest.fixture
def deployed_v5_contract() -> NoMistakeDataVault2ContractV5:
    return NoMistakeDataVault2ContractV5(
        domain_namespace="asset_management",
        lifecycle_stage="COLD_START_N1",
        column_triage_ledger=[
            ColumnTriageEntry(
                bronze_table="bronze_positions",
                column_name="position_id",
                elm_classification="BUSINESS_KEY",
                target_dv_entity="hub_position",
                source_sql_type="VARCHAR(64)",
            ),
            ColumnTriageEntry(
                bronze_table="bronze_positions",
                column_name="quantity",
                elm_classification="DESCRIPTIVE_PAYLOAD",
                target_dv_entity="sat_position",
                source_sql_type="NUMERIC(14,2)",
            ),
        ],
        hubs=[
            ContractHubSpec(
                name="hub_position",
                ontology_concept_uri="urn:ontology:entity:position",
                business_key_sources=[
                    BusinessKeySource(
                        bronze_table="bronze_positions",
                        key_columns=["position_id"],
                        record_source_code="SRC_POS",
                        key_authority_tier="TIER_2_ENTERPRISE_SOR",
                        require_src_qualifier=False,
                    )
                ],
            )
        ],
        links=[],
        satellites=[
            ContractSatelliteSpec(
                name="sat_position",
                satellite_type="STANDARD_SATELLITE",
                parent_entity="hub_position",
                source_bronze_table="bronze_positions",
                payload_columns=["quantity"],
            )
        ],
        reference_tables=[],
        unmapped_escalations=[],
    )


def test_row1_cold_start_n1_ontology_seed(empty_ontology: GraphAOntology) -> None:
    """Row 1: contract=None, sources=[], docs=[] -> COLD_START_N1 ontology seed."""
    bp = create_vault_blueprint(
        domain_namespace="asset_management",
        ontology=empty_ontology,
        contract=None,
    )
    plan = infer_execution_plan(blueprint=bp, sources=[], documents=[])

    assert plan.primary_action == "COLD_START_N1"
    assert "Row 1" in plan.explanation
    assert len(plan.table_actions) == 0
    assert len(plan.doc_actions) == 0


def test_row2_greenfield_structured_only(empty_ontology: GraphAOntology) -> None:
    """Row 2: contract=None, sources present, docs=[] -> GREENFIELD_SYNTHESIS."""
    sources = [
        PipelineSourceFeedInput(
            source_system="sqlite_feed",
            table_name="trades",
            business_keys=["trade_id"],
        )
    ]
    plan = infer_execution_plan(blueprint=None, contract=None, sources=sources, documents=[])

    assert plan.primary_action == "COLD_START_N1"
    assert "Row 2" in plan.explanation
    assert len(plan.table_actions) == 1
    assert plan.table_actions[0].action == "GREENFIELD_SYNTHESIS"
    assert plan.table_actions[0].table_name == "trades"


def test_row3_unstructured_first(empty_ontology: GraphAOntology) -> None:
    """Row 3: contract=None, sources=[], docs present -> UNSTRUCTURED_FIRST."""
    docs = [
        PipelineUnstructuredDocInput(
            doc_id="om_memo_pdf",
            file_path="offering_memorandum.pdf",
            target_hub="hub_document",
        )
    ]
    plan = infer_execution_plan(blueprint=None, contract=None, sources=[], documents=docs)

    assert plan.primary_action == "UNSTRUCTURED_FIRST"
    assert "Row 3" in plan.explanation
    assert len(plan.doc_actions) == 1
    assert plan.doc_actions[0].action == "GROUND_ONTOLOGY_CONCEPT"


def test_row4_greenfield_multi_source_hybrid(empty_ontology: GraphAOntology) -> None:
    """Row 4: contract=None, sources present, docs present -> Greenfield Hybrid."""
    sources = [
        PipelineSourceFeedInput(
            source_system="crm", table_name="clients", business_keys=["client_id"]
        )
    ]
    docs = [PipelineUnstructuredDocInput(doc_id="doc1", target_hub="hub_document")]
    plan = infer_execution_plan(blueprint=None, contract=None, sources=sources, documents=docs)

    assert plan.primary_action == "COLD_START_N1"
    assert "Row 4" in plan.explanation
    assert len(plan.table_actions) == 1
    assert len(plan.doc_actions) == 1


def test_row5_verified_no_op(
    empty_ontology: GraphAOntology,
    deployed_v5_contract: NoMistakeDataVault2ContractV5,
) -> None:
    """Row 5: contract!=None, sources=[], docs=[] -> VERIFIED_NO_OP re-verification."""
    bp = create_vault_blueprint(
        domain_namespace="asset_management",
        ontology=empty_ontology,
        contract=deployed_v5_contract,
    )
    plan = infer_execution_plan(blueprint=bp, sources=[], documents=[])

    assert plan.primary_action == "VERIFIED_NO_OP"
    assert "Row 5" in plan.explanation
    assert len(plan.table_actions) == 0


def test_row6_intra_source_drift(
    deployed_v5_contract: NoMistakeDataVault2ContractV5,
) -> None:
    """Row 6: contract!=None, table IS in contract with added/dropped cols -> SCHEMA_DRIFT."""
    # Deployed table bronze_positions has position_id, quantity.
    # Incoming table has position_id, quantity, and new column esg_rating.
    sources = [
        PipelineSourceFeedInput(
            source_system="trading",
            table_name="bronze_positions",
            business_keys=["position_id", "quantity", "esg_rating"],
        )
    ]
    plan = infer_execution_plan(
        blueprint=None,
        contract=deployed_v5_contract,
        sources=sources,
        documents=[],
    )

    assert plan.primary_action == "SCHEMA_DRIFT"
    assert len(plan.table_actions) == 1
    t_act = plan.table_actions[0]
    assert t_act.action == "INTRA_SOURCE_DRIFT"
    assert t_act.matched_existing_entity == "sat_position"
    # Should find additive column esg_rating
    assert any(
        f.get("category") == "ADDITIVE" and f.get("column_name") == "esg_rating"
        for f in t_act.drift_findings
    )


def test_row6_incremental_onboarding(
    deployed_v5_contract: NoMistakeDataVault2ContractV5,
) -> None:
    """Row 6 (Incremental): contract!=None, table NOT in contract -> MULTI_SOURCE_EXPANSION."""
    sources = [
        PipelineSourceFeedInput(
            source_system="custody",
            table_name="custody_settlements",
            business_keys=["settlement_id"],
        )
    ]
    plan = infer_execution_plan(
        blueprint=None,
        contract=deployed_v5_contract,
        sources=sources,
        documents=[],
    )

    assert plan.primary_action == "MULTI_SOURCE_EXPANSION"
    assert len(plan.table_actions) == 1
    assert plan.table_actions[0].action == "INCREMENTAL_ONBOARDING"


def test_row7_hybrid_per_source_plan(
    deployed_v5_contract: NoMistakeDataVault2ContractV5,
) -> None:
    """Row 7: contract!=None, mixed (drifted table + new table + docs) in single run."""
    sources = [
        # 1. Drifted table
        PipelineSourceFeedInput(
            source_system="trading",
            table_name="bronze_positions",
            business_keys=["position_id", "quantity", "liquidity_bucket"],
        ),
        # 2. Brand new table
        PipelineSourceFeedInput(
            source_system="market_data",
            table_name="yield_curves",
            business_keys=["curve_id"],
        ),
    ]
    docs = [
        PipelineUnstructuredDocInput(
            doc_id="fund_factsheet",
            target_hub="hub_document",
        )
    ]

    plan = infer_execution_plan(
        blueprint=None,
        contract=deployed_v5_contract,
        sources=sources,
        documents=docs,
    )

    assert plan.primary_action == "HYBRID_PER_SOURCE"
    assert len(plan.table_actions) == 2
    actions_by_table = {t.table_name: t.action for t in plan.table_actions}
    assert actions_by_table["bronze_positions"] == "INTRA_SOURCE_DRIFT"
    assert actions_by_table["yield_curves"] == "INCREMENTAL_ONBOARDING"
    assert len(plan.doc_actions) == 1


def test_resolve_initial_blueprint_parses_initial_contract_content_when_bp_contract_none(
    empty_ontology: GraphAOntology,
    deployed_v5_contract: NoMistakeDataVault2ContractV5,
    tmp_path,
) -> None:
    """Bug 1: Parse initial_contract_content/path when blueprint.contract is None."""
    import yaml

    from opendv_agent_harness.pipeline.mode_detector import resolve_initial_blueprint_and_ontology
    from opendv_agent_harness.pipeline.spec import PipelineInputSpec

    bp = create_vault_blueprint(
        domain_namespace="asset_management",
        ontology=empty_ontology,
        contract=None,
    )
    cnt_yaml = yaml.safe_dump(deployed_v5_contract.model_dump(mode="json"), sort_keys=False)

    # 1. Inline initial_contract_content with blueprint(contract=None)
    spec_inline = PipelineInputSpec(
        blueprint=bp,
        initial_contract_content=cnt_yaml,
    )
    out_bp, _out_ont, out_cnt, _ = resolve_initial_blueprint_and_ontology(spec_inline)
    assert out_bp is not None
    assert out_cnt is not None, (
        "Expected initial_contract_content to be parsed when bp.contract is None"
    )
    assert out_cnt.hubs[0].name == "hub_position"

    plan = infer_execution_plan(
        blueprint=out_bp,
        contract=out_cnt,
        sources=[
            PipelineSourceFeedInput(
                source_system="custody",
                table_name="custody_settlements",
                business_keys=["settlement_id"],
            )
        ],
    )
    assert plan.primary_action == "MULTI_SOURCE_EXPANSION"

    # 2. File-backed initial_contract_path with blueprint(contract=None)
    cnt_file = tmp_path / "deployed_contract.yaml"
    cnt_file.write_text(cnt_yaml, encoding="utf-8")
    spec_file = PipelineInputSpec(
        blueprint=bp,
        initial_contract_path=str(cnt_file),
    )
    _, _, out_cnt_path, _ = resolve_initial_blueprint_and_ontology(spec_file)
    assert out_cnt_path is not None
    assert out_cnt_path.hubs[0].name == "hub_position"
