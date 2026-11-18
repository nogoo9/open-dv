"""Tests for MultiWaveScenarioRunner in opendv-testkit.

Validates multi-wave chaining (Wave 0 Greenfield -> Wave 1 Incremental),
Algorithm A.7 additive evolution locks (DV-EVO-001..008), hash_diff continuity
verification in DuckDB shadow warehouse, and fail-closed security invariants.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from opendv_agent_harness.pipeline.spec import (
    PipelineInputSpec,
    PipelineSourceFeedInput,
)
from opendv_contracts.dtos.pipeline import PipelineInputDTO
from opendv_contracts.dtos.sources import SourceFeedDTO
from opendv_core.models.graph_a import (
    CanonicalBusinessConcept,
    GraphAOntology,
)
from opendv_core.models.vault_blueprint import compute_blueprint_sha256
from opendv_testkit.multi_wave_runner import (
    EvolutionLockViolationError,
    HashDiffContinuityError,
    MultiWaveScenarioReport,
    MultiWaveScenarioRunner,
    MultiWaveStepResult,
)


@pytest.fixture
def banking_ontology() -> GraphAOntology:
    return GraphAOntology(
        ontology_version="1.0.0",
        domain_namespace="banking",
        canonical_business_concepts=[
            CanonicalBusinessConcept(
                concept_uri="urn:ontology:entity:customer",
                concept_name="Customer",
                definition="A banking retail customer",
                target_hub="hub_customer",
            ),
            CanonicalBusinessConcept(
                concept_uri="urn:ontology:entity:account",
                concept_name="Account",
                definition="A financial deposit or credit account",
                target_hub="hub_account",
            ),
        ],
    )


@pytest.fixture
def wave_0_sqlite(tmp_path: Path) -> Path:
    db_path = tmp_path / "wave_0_core.sqlite"
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE customers (
            cust_id VARCHAR(32) PRIMARY KEY,
            full_name VARCHAR(128) NOT NULL,
            email VARCHAR(128)
        );
        """
    )
    cur.execute("INSERT INTO customers VALUES ('C101', 'Alice Banker', 'alice@bank.org');")
    cur.execute("INSERT INTO customers VALUES ('C102', 'Bob Smith', 'bob@bank.org');")
    conn.commit()
    conn.close()
    return db_path


@pytest.fixture
def wave_1_sqlite(tmp_path: Path) -> Path:
    db_path = tmp_path / "wave_1_accounts.sqlite"
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE accounts (
            acct_id VARCHAR(32) PRIMARY KEY,
            cust_id VARCHAR(32) NOT NULL,
            account_type VARCHAR(16) NOT NULL,
            balance REAL
        );
        """
    )
    cur.execute("INSERT INTO accounts VALUES ('A501', 'C101', 'CHECKING', 12500.50);")
    cur.execute("INSERT INTO accounts VALUES ('A502', 'C102', 'SAVINGS', 3400.00);")
    conn.commit()
    conn.close()
    return db_path


def test_wave_0_cold_start_to_wave_1_incremental(
    banking_ontology: GraphAOntology, wave_0_sqlite: Path, wave_1_sqlite: Path, tmp_path: Path
) -> None:
    """Verify Wave 0 Greenfield -> Wave 1 Incremental multi-table evolution."""
    runner = MultiWaveScenarioRunner(output_dir=tmp_path / "runs")
    try:
        # 1. Wave 0: Cold Start Greenfield
        spec_0 = PipelineInputSpec(
            domain_namespace="banking",
            ontology=banking_ontology,
            sources=[
                PipelineSourceFeedInput(
                    source_system="core_banking",
                    uri=str(wave_0_sqlite),
                    adapter="sqlite",
                    options={"multi_table": True},
                )
            ],
            target_dialect="duckdb",
        )
        step_0 = runner.execute_wave(spec_0, wave_name="wave_0_greenfield")

        assert isinstance(step_0, MultiWaveStepResult)
        assert step_0.status == "COMPLETED"
        assert step_0.passed is True
        assert step_0.blueprint is not None
        assert step_0.blueprint.lineage.blueprint_revision == 1
        assert step_0.violations == []
        assert "raw_vault.hub_customer" in step_0.recorded_tables
        assert step_0.hash_diff_row_count > 0

        # Verify DuckDB warehouse state after Wave 0
        w0_tables = [
            r[0]
            for r in runner.conn.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'raw_vault'"
            ).fetchall()
        ]
        assert "hub_customer" in w0_tables
        assert "sat_customer_core" in w0_tables

        # 2. Wave 1: Incremental Multi-Table Expansion (using dict input)
        wave_1_input = {
            "domain_namespace": "banking",
            "sources": [
                {
                    "source_system": "core_banking",
                    "uri": str(wave_1_sqlite),
                    "adapter": "sqlite",
                    "options": {"multi_table": True},
                }
            ],
            "target_dialect": "duckdb",
        }
        step_1 = runner.execute_wave(wave_1_input, wave_name="wave_1_accounts")

        assert step_1.status == "COMPLETED"
        assert step_1.passed is True
        assert step_1.blueprint is not None
        assert step_1.blueprint.lineage.blueprint_revision == 2
        assert step_1.blueprint.lineage.parent_blueprint_sha256 == compute_blueprint_sha256(
            step_0.blueprint
        )
        assert step_1.violations == []

        # Verify DuckDB warehouse expanded with account entities
        w1_tables = [
            r[0]
            for r in runner.conn.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = 'raw_vault'"
            ).fetchall()
        ]
        assert "hub_customer" in w1_tables
        assert "sat_customer_core" in w1_tables
        assert "hub_account" in w1_tables
        assert "sat_accounts" in w1_tables

        assert len(runner.waves) == 2
    finally:
        runner.close()


def test_pipeline_input_dto_support(
    banking_ontology: GraphAOntology, wave_0_sqlite: Path, wave_1_sqlite: Path, tmp_path: Path
) -> None:
    """Verify MultiWaveScenarioRunner seamlessly accepts PipelineInputDTO across waves."""
    runner = MultiWaveScenarioRunner(output_dir=tmp_path / "runs_dto")
    try:
        dto_0 = PipelineInputDTO(
            ontology_source=banking_ontology.model_dump(),
            sources=[
                SourceFeedDTO(
                    feed_id="feed_cust",
                    source_system="core_banking",
                    uri=str(wave_0_sqlite),
                    adapter="sqlite",
                    options={"multi_table": True},
                )
            ],
            target_dialect="duckdb",
        )
        step_0 = runner.execute_wave(dto_0)
        assert step_0.passed is True
        assert step_0.blueprint.lineage.blueprint_revision == 1

        dto_1 = PipelineInputDTO(
            sources=[
                SourceFeedDTO(
                    feed_id="feed_acct",
                    source_system="core_banking",
                    uri=str(wave_1_sqlite),
                    adapter="sqlite",
                    options={"multi_table": True},
                )
            ],
            target_dialect="duckdb",
        )
        step_1 = runner.execute_wave(dto_1)
        assert step_1.passed is True
        assert step_1.blueprint.lineage.blueprint_revision == 2
    finally:
        runner.close()


def test_additive_evolution_lock_valid_transition(
    banking_ontology: GraphAOntology, wave_0_sqlite: Path, tmp_path: Path
) -> None:
    """Verify that valid additive evolution transitions pass DV-EVO-001..008 checks."""
    runner = MultiWaveScenarioRunner(output_dir=tmp_path / "runs_valid")
    try:
        spec_0 = PipelineInputSpec(
            domain_namespace="banking",
            ontology=banking_ontology,
            sources=[
                PipelineSourceFeedInput(
                    source_system="core_banking",
                    uri=str(wave_0_sqlite),
                    adapter="sqlite",
                    options={"multi_table": True},
                )
            ],
        )
        step_0 = runner.execute_wave(spec_0)
        assert step_0.passed is True

        # Construct valid additive contract addition (new companion satellite)
        base_contract = dict(runner.current_contract or {})
        valid_evolved_contract = {
            **base_contract,
            "satellites": [
                *base_contract.get("satellites", []),
                {
                    "name": "sat_customer_ext",
                    "parent_entity": "hub_customer",
                    "payload_columns": ["loyalty_tier", "rewards_balance"],
                },
            ],
        }

        # Execute wave with valid evolved contract override
        step_1 = runner.execute_wave(
            spec_0,
            wave_name="wave_1_valid_ext",
            contract_override=valid_evolved_contract,
        )
        assert step_1.passed is True
        assert step_1.violations == []
    finally:
        runner.close()


def test_non_additive_evolution_rejected_dropping_column(
    banking_ontology: GraphAOntology, wave_0_sqlite: Path, tmp_path: Path
) -> None:
    """Verify DV-EVO-004 violation when a satellite column is dropped without soft-deprecation."""
    runner = MultiWaveScenarioRunner(output_dir=tmp_path / "runs_drop_col")
    try:
        spec_0 = PipelineInputSpec(
            domain_namespace="banking",
            ontology=banking_ontology,
            sources=[
                PipelineSourceFeedInput(
                    source_system="core_banking",
                    uri=str(wave_0_sqlite),
                    adapter="sqlite",
                    options={"multi_table": True},
                )
            ],
        )
        runner.execute_wave(spec_0)

        # Create breaking contract mutation: drop payload column from sat_customer_core
        breaking_contract = dict(runner.current_contract or {})
        breaking_contract["satellites"] = [
            {
                "name": "sat_customer_core",
                "parent_entity": "hub_customer",
                "payload_columns": [],  # 'full_name' column dropped!
            },
            {
                "name": "sat_customer_pii",
                "parent_entity": "hub_customer",
                "payload_columns": ["email"],
            },
        ]

        with pytest.raises(EvolutionLockViolationError) as exc_info:
            runner.execute_wave(
                spec_0,
                wave_name="wave_1_drop_col",
                contract_override=breaking_contract,
            )

        err_msg = str(exc_info.value)
        assert "DV-EVO-004" in err_msg or "SAT_COLUMN_DROPPED" in err_msg
        assert len(exc_info.value.violations) >= 1
    finally:
        runner.close()


def test_non_additive_evolution_rejected_mutating_hash_config(
    banking_ontology: GraphAOntology, wave_0_sqlite: Path, tmp_path: Path
) -> None:
    """Verify DV-EVO-001 violation when global hash_config is mutated."""
    runner = MultiWaveScenarioRunner(output_dir=tmp_path / "runs_hash_cfg")
    try:
        spec_0 = PipelineInputSpec(
            domain_namespace="banking",
            ontology=banking_ontology,
            sources=[
                PipelineSourceFeedInput(
                    source_system="core_banking",
                    uri=str(wave_0_sqlite),
                    adapter="sqlite",
                    options={"multi_table": True},
                )
            ],
        )
        runner.execute_wave(spec_0)

        # Baseline hash_config
        runner.current_contract["hash_config"] = {
            "algorithm": "sha256",
            "collision_prevention": "LENGTH_PREFIXED",
        }

        # Mutate hash_config in breaking contract
        breaking_contract = dict(runner.current_contract)
        breaking_contract["hash_config"] = {
            "algorithm": "md5",  # Mutated from sha256 to md5
            "collision_prevention": "LENGTH_PREFIXED",
        }

        with pytest.raises(EvolutionLockViolationError) as exc_info:
            runner.execute_wave(
                spec_0,
                wave_name="wave_1_hash_cfg",
                contract_override=breaking_contract,
            )

        err_msg = str(exc_info.value)
        assert "DV-EVO-001" in err_msg or "HASH_CONFIG_MUTATED" in err_msg
    finally:
        runner.close()


def test_non_additive_evolution_rejected_hub_deleted(
    banking_ontology: GraphAOntology, wave_0_sqlite: Path, tmp_path: Path
) -> None:
    """Verify DV-EVO-002 violation when a deployed Hub entity is deleted."""
    runner = MultiWaveScenarioRunner(output_dir=tmp_path / "runs_hub_del")
    try:
        spec_0 = PipelineInputSpec(
            domain_namespace="banking",
            ontology=banking_ontology,
            sources=[
                PipelineSourceFeedInput(
                    source_system="core_banking",
                    uri=str(wave_0_sqlite),
                    adapter="sqlite",
                    options={"multi_table": True},
                )
            ],
        )
        runner.execute_wave(spec_0)

        # Delete hub_customer from contract
        breaking_contract = dict(runner.current_contract or {})
        breaking_contract["hubs"] = []

        with pytest.raises(EvolutionLockViolationError) as exc_info:
            runner.execute_wave(
                spec_0,
                wave_name="wave_1_hub_del",
                contract_override=breaking_contract,
            )

        err_msg = str(exc_info.value)
        assert "DV-EVO-002" in err_msg or "HUB_DELETED" in err_msg
    finally:
        runner.close()


def test_hash_diff_continuity_verification_and_mutation_detection(
    banking_ontology: GraphAOntology, wave_0_sqlite: Path, tmp_path: Path
) -> None:
    """Verify hash_diff continuity check across waves and detection of row/hash tampering."""
    runner = MultiWaveScenarioRunner(output_dir=tmp_path / "runs_hash_cont")
    try:
        spec_0 = PipelineInputSpec(
            domain_namespace="banking",
            ontology=banking_ontology,
            sources=[
                PipelineSourceFeedInput(
                    source_system="core_banking",
                    uri=str(wave_0_sqlite),
                    adapter="sqlite",
                    options={"multi_table": True},
                )
            ],
        )
        step_0 = runner.execute_wave(spec_0)
        assert step_0.hash_diff_row_count > 0

        # Normal verification passes
        assert runner.validate_hash_diff_continuity() is True

        # 1. Tamper with historical hash_diff in warehouse
        runner.conn.execute(
            """
            UPDATE raw_vault.sat_customer_core
            SET hash_diff = 'corrupted_hash_diff_value_1234567890'
            WHERE hash_diff != '0000000000000000000000000000000000000000000000000000000000000000'
            """
        )

        with pytest.raises(HashDiffContinuityError) as exc_info:
            runner.validate_hash_diff_continuity()

        assert "Hash diff mismatch" in str(exc_info.value)
        assert issubclass(HashDiffContinuityError, EvolutionLockViolationError)

        # 2. Tamper with row presence (delete row from warehouse)
        runner.conn.execute(
            """
            DELETE FROM raw_vault.sat_customer_core
            WHERE hash_diff != '0000000000000000000000000000000000000000000000000000000000000000'
            """
        )

        with pytest.raises(HashDiffContinuityError) as exc_del_info:
            runner.validate_hash_diff_continuity()

        assert "Row disappeared" in str(exc_del_info.value)
    finally:
        runner.close()


def test_execute_scenario_end_to_end(
    banking_ontology: GraphAOntology, wave_0_sqlite: Path, wave_1_sqlite: Path, tmp_path: Path
) -> None:
    """Verify execute_scenario executes a multi-wave sequence and outputs comprehensive report."""
    runner = MultiWaveScenarioRunner(output_dir=tmp_path / "runs_scenario")
    try:
        dto_0 = PipelineInputDTO(
            ontology_source=banking_ontology.model_dump(),
            sources=[
                SourceFeedDTO(
                    feed_id="feed_c",
                    source_system="core_banking",
                    uri=str(wave_0_sqlite),
                    adapter="sqlite",
                    options={"multi_table": True},
                )
            ],
            target_dialect="duckdb",
        )

        dto_1 = PipelineInputDTO(
            sources=[
                SourceFeedDTO(
                    feed_id="feed_a",
                    source_system="core_banking",
                    uri=str(wave_1_sqlite),
                    adapter="sqlite",
                    options={"multi_table": True},
                )
            ],
            target_dialect="duckdb",
        )

        scenario_rep = runner.execute_scenario(
            [dto_0, dto_1],
            scenario_name="banking_greenfield_to_incremental",
        )

        assert isinstance(scenario_rep, MultiWaveScenarioReport)
        assert scenario_rep.passed is True
        assert scenario_rep.total_waves == 2
        assert len(scenario_rep.step_results) == 2
        assert scenario_rep.final_blueprint is not None
        assert scenario_rep.final_blueprint.lineage.blueprint_revision == 2
        assert len(scenario_rep.summary_markdown) > 0
        assert (
            "Multi-Wave Report: banking_greenfield_to_incremental" in scenario_rep.summary_markdown
        )
    finally:
        runner.close()


def test_evolution_lock_fails_closed_when_next_contract_is_empty(tmp_path: Path) -> None:
    """M23: evolution lock records violation and fails closed if next contract is wiped."""
    from unittest.mock import MagicMock

    from opendv_core.models.vault_blueprint import VaultBlueprint

    runner = MultiWaveScenarioRunner(output_dir=tmp_path / "runs_wiped", fail_closed=True)
    runner.current_contract = {"version": "1.0.0", "hubs": {"hub_customer": {}}}

    mock_bp = MagicMock(spec=VaultBlueprint)
    mock_bp.contract = None
    runner.current_blueprint = mock_bp
    mock_report = MagicMock()
    mock_report.status = "COMPLETED"
    mock_report.overall_passed = True
    mock_report.blueprint = mock_bp
    mock_report.four_zone_sql = {}
    mock_report.evolution_diff = {}

    runner.pipeline_runner = MagicMock(return_value=mock_report)

    with pytest.raises(EvolutionLockViolationError) as exc_info:
        runner.execute_wave({"sources": []}, wave_index=1, contract_override={})

    assert "empty or missing contract" in str(exc_info.value)
    runner.close()


def test_wave_1_fails_closed_when_prior_wave_produced_no_blueprint(tmp_path: Path) -> None:
    """M23 verification: wave 1 cannot execute if prior wave produced no valid blueprint."""
    runner = MultiWaveScenarioRunner(output_dir=tmp_path / "runs_nobp", fail_closed=True)
    runner.current_blueprint = None

    with pytest.raises(RuntimeError) as exc_info:
        runner.execute_wave({"sources": []}, wave_index=1)

    assert "prior step did not produce a valid VaultBlueprint" in str(exc_info.value)
    runner.close()
