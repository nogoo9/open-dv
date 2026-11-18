"""End-to-End tests for catalog ingestion, feed expansion,
and prior-driven contract synthesis (WS3.3).

Verifies:
1. `CatalogSourceAdapter.expand_feed()` integration in STAGE_01 (SQLite and catalog sources).
2. Propagation of Primary Key & Foreign Key priors into Stage01FeedDiscoveryItem.
3. Alg A.1 3-axis satellite splitting driven by column governance tags (pii, mnpi).
4. Recording of `source_sql_type` on all `ColumnTriageEntry` items in the synthesized contract.
5. Alg A.9a contract-diff schema drift detection reconstructing baseline metadata without raw data.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import duckdb
import pytest

from opendv_agent_harness.pipeline.contract_synthesizer import (
    _synthesize_cumulative_contract,
)
from opendv_agent_harness.pipeline.discovery_tabular import discover_tabular_feeds
from opendv_agent_harness.pipeline.spec import (
    PipelineSourceFeedInput,
    Stage01FeedDiscoveryItem,
)
from opendv_axi_verifier.codemods.alg_a9a_schema_drift import (
    detect_schema_drift_from_contract,
    reconstruct_baseline_metadata_from_contract,
)
from opendv_core.models.contract_v5 import (
    ColumnTriageEntry,
    NoMistakeDataVault2ContractV5,
)
from opendv_core.models.graph_a import GraphAOntology


@pytest.fixture
def test_ontology() -> GraphAOntology:
    return GraphAOntology(
        ontology_version="0.1.0",
        domain_namespace="corporate_finance",
    )


@pytest.fixture
def sample_sqlite_db(tmp_path: Path) -> Path:
    """Create a temporary SQLite database with PKs and FKs for multi-table catalog testing."""
    db_path = tmp_path / "corp_finance.sqlite"
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    cur.execute(
        """
        CREATE TABLE departments (
            dept_id VARCHAR(32) PRIMARY KEY,
            dept_name VARCHAR(128) NOT NULL,
            cost_center VARCHAR(64)
        );
        """
    )
    cur.execute(
        """
        CREATE TABLE employees (
            emp_id VARCHAR(32) PRIMARY KEY,
            dept_id VARCHAR(32) NOT NULL,
            first_name VARCHAR(64),
            last_name VARCHAR(64),
            tax_id VARCHAR(32),
            salary_amount NUMERIC(12,2),
            FOREIGN KEY (dept_id) REFERENCES departments(dept_id)
        );
        """
    )

    cur.execute("INSERT INTO departments VALUES ('D01', 'Engineering', 'CC-101');")
    cur.execute(
        "INSERT INTO employees VALUES ('E001', 'D01', 'Alice', 'Smith', 'TAX-999', 150000.0);"
    )
    conn.commit()
    conn.close()
    return db_path


def test_sqlite_catalog_feed_expansion_and_priors(sample_sqlite_db: Path) -> None:
    """Verify STAGE_01 discovers multi-table feeds, PK priors, and FK priors from SQLite."""
    conn = duckdb.connect(":memory:")
    src_input = PipelineSourceFeedInput(
        source_system="corp_hr",
        uri=str(sample_sqlite_db),
        adapter="sqlite",
        options={"multi_table": True},
    )

    output = discover_tabular_feeds([src_input], conn)
    assert len(output.discovered_feeds) >= 2

    # Verify departments feed
    dept_feed = next((f for f in output.discovered_feeds if f.table_name == "departments"), None)
    assert dept_feed is not None
    assert "dept_id" in dept_feed.primary_keys

    # Verify employees feed
    emp_feed = next((f for f in output.discovered_feeds if f.table_name == "employees"), None)
    assert emp_feed is not None
    assert "emp_id" in emp_feed.primary_keys
    assert any(
        fk.get("fk_column") == "dept_id" or fk.get("target_table") == "departments"
        for fk in emp_feed.foreign_keys
    )


def test_contract_synthesis_with_pk_fk_priors_and_sql_types(
    test_ontology: GraphAOntology,
) -> None:
    """Verify synthesizer assigns PKs to Hubs, FKs to Links, and records source_sql_type."""
    discovered = [
        Stage01FeedDiscoveryItem(
            source_system="corp_hr",
            table_name="departments",
            column_count=3,
            row_count=1,
            primary_keys=["dept_id"],
            foreign_keys=[],
            column_descriptors=[
                {"name": "dept_id", "physical_type": "VARCHAR(32)", "is_primary_key": True},
                {"name": "dept_name", "physical_type": "VARCHAR(128)", "is_primary_key": False},
                {"name": "cost_center", "physical_type": "VARCHAR(64)", "is_primary_key": False},
            ],
        ),
        Stage01FeedDiscoveryItem(
            source_system="corp_hr",
            table_name="employees",
            column_count=6,
            row_count=1,
            primary_keys=["emp_id"],
            foreign_keys=[
                {"fk_column": "dept_id", "target_table": "departments", "target_column": "dept_id"}
            ],
            column_descriptors=[
                {"name": "emp_id", "physical_type": "VARCHAR(32)", "is_primary_key": True},
                {"name": "dept_id", "physical_type": "VARCHAR(32)", "is_primary_key": False},
                {"name": "first_name", "physical_type": "VARCHAR(64)", "is_primary_key": False},
                {"name": "last_name", "physical_type": "VARCHAR(64)", "is_primary_key": False},
                {
                    "name": "tax_id",
                    "physical_type": "VARCHAR(32)",
                    "is_primary_key": False,
                    "governance_tags": {"classification": "pii"},
                },
                {
                    "name": "salary_amount",
                    "physical_type": "NUMERIC(12,2)",
                    "is_primary_key": False,
                    "governance_tags": {"classification": "confidential"},
                },
            ],
        ),
    ]

    contract_dict = _synthesize_cumulative_contract(
        ontology=test_ontology,
        discovered_feeds=discovered,
        domain_namespace="corp_hr",
    )

    contract = NoMistakeDataVault2ContractV5.model_validate(contract_dict)
    assert len(contract.hubs) >= 2
    hub_names = {h.name for h in contract.hubs}
    assert "hub_departments" in hub_names or "hub_department" in hub_names
    assert "hub_employees" in hub_names or "hub_employee" in hub_names

    # Verify Link exists for the FK relationship
    assert len(contract.links) >= 1
    assert any("employees" in lnk.name or "employee" in lnk.name for lnk in contract.links)

    # Verify Alg A.1 3-axis split: PII satellite is created for tax_id & salary_amount
    sat_names = {s.name for s in contract.satellites}
    assert any("pii" in sname for sname in sat_names)

    # Verify every ColumnTriageEntry records source_sql_type
    assert len(contract.column_triage_ledger) >= 8
    for entry in contract.column_triage_ledger:
        assert entry.source_sql_type is not None, (
            f"Entry {entry.column_name} missing source_sql_type"
        )
        assert len(entry.source_sql_type) > 0


def test_schema_drift_reconstructs_baseline_from_contract(
    test_ontology: GraphAOntology,
) -> None:
    """Verify Alg A.9a diffs column types and renames directly from ledger without baseline data."""
    initial_contract = NoMistakeDataVault2ContractV5(
        domain_namespace="corp_hr",
        lifecycle_stage="COLD_START_N1",
        column_triage_ledger=[
            ColumnTriageEntry(
                bronze_table="corp_hr.employees",
                column_name="emp_id",
                elm_classification="BUSINESS_KEY",
                target_dv_entity="hub_employees",
                source_sql_type="VARCHAR(32)",
            ),
            ColumnTriageEntry(
                bronze_table="corp_hr.employees",
                column_name="first_name",
                elm_classification="DESCRIPTIVE_PAYLOAD",
                target_dv_entity="sat_employees",
                source_sql_type="VARCHAR(64)",
            ),
            ColumnTriageEntry(
                bronze_table="corp_hr.employees",
                column_name="salary_amount",
                elm_classification="DESCRIPTIVE_PAYLOAD",
                target_dv_entity="sat_employees_pii",
                source_sql_type="NUMERIC(12,2)",
            ),
        ],
        hubs=[],
        links=[],
        satellites=[],
        reference_tables=[],
        unmapped_escalations=[],
    )

    baseline_cols, baseline_types, _ = reconstruct_baseline_metadata_from_contract(
        initial_contract, "corp_hr.employees"
    )
    assert baseline_cols == {"emp_id", "first_name", "salary_amount"}
    assert baseline_types["salary_amount"] == "NUMERIC(12,2)"
    assert baseline_types["first_name"] == "VARCHAR(64)"

    # Incoming table has widened type for salary_amount, added column email,
    # dropped column first_name
    candidate_cols = ["emp_id", "salary_amount", "email"]
    candidate_types = {
        "emp_id": "VARCHAR(32)",
        "salary_amount": "NUMERIC(16,2)",
        "email": "VARCHAR(128)",
    }

    findings = detect_schema_drift_from_contract(
        contract=initial_contract,
        source_table="corp_hr.employees",
        candidate_columns=candidate_cols,
        candidate_types=candidate_types,
        target_entity="sat_employees",
    )

    categories = {str(f.category) for f in findings}
    assert "ADDITIVE" in categories  # email
    assert "TYPE_WIDENED" in categories  # salary_amount
    assert "SOFT_DEPRECATED" in categories  # first_name
