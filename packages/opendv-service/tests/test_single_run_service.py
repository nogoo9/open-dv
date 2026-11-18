"""Unit tests for WS4.1 Single-Run Service Layer, Source Inspection, and Studio Blueprint Export."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import pytest
import yaml

from opendv_contracts.dtos.blueprint import BlueprintExportResponseDTO, VaultBlueprintDTO
from opendv_contracts.dtos.pipeline import (
    InferredExecutionPlanDTO,
    PipelineInputDTO,
)
from opendv_contracts.dtos.sources import (
    ConnectorUIManifestDTO,
    SourceFeedDTO,
    SourceInspectRequestDTO,
    SourceInspectResponseDTO,
)
from opendv_core.genesis import create_genesis_ontology
from opendv_core.models.vault_blueprint import create_vault_blueprint
from opendv_service.state import StateStore
from opendv_service.studio.blueprint_export import (
    export_vault_blueprint,
    export_vault_blueprint_response,
)
from opendv_service.studio.session_manager import StudioSessionManager
from opendv_service.use_cases.pipeline import (
    DualBlueprintIngressNormalizer,
    SingleRunExecutionResultDTO,
    execute_pipeline_run_sync,
    validate_pipeline,
)
from opendv_service.use_cases.sources_inspect import inspect_source, list_adapters
from opendv_telemetry.cas_store import ContentAddressableStore
from opendv_testkit.uc_emulator import DuckDBUnityCatalogEmulator


def _get_pe_contract_path() -> Path:
    candidates = [
        Path(__file__).resolve().parents[3]
        / "examples"
        / "private_equity"
        / "dv_pe_model_contract.yaml",
        Path("open-dv/examples/private_equity/dv_pe_model_contract.yaml"),
        Path("examples/private_equity/dv_pe_model_contract.yaml"),
    ]
    for c in candidates:
        if c.is_file():
            return c.resolve()
    raise FileNotFoundError("Could not find dv_pe_model_contract.yaml in repo")


@pytest.fixture
def sqlite_test_db(tmp_path: Path) -> Path:
    """Create a realistic SQLite database fixture with PKs and FKs."""
    db_file = tmp_path / "test_store.db"
    conn = sqlite3.connect(str(db_file))
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE customers (
            customer_id INTEGER PRIMARY KEY,
            customer_name TEXT NOT NULL,
            email TEXT,
            created_at TEXT
        );
        """
    )
    cur.execute(
        """
        CREATE TABLE orders (
            order_id INTEGER PRIMARY KEY,
            customer_id INTEGER NOT NULL,
            order_total REAL NOT NULL,
            order_date TEXT,
            FOREIGN KEY (customer_id) REFERENCES customers(customer_id)
        );
        """
    )
    cur.execute("INSERT INTO customers VALUES (1, 'Alice Corp', 'alice@corp.com', '2026-01-01');")
    cur.execute("INSERT INTO customers VALUES (2, 'Bob LLC', 'bob@llc.com', '2026-01-02');")
    cur.execute("INSERT INTO orders VALUES (101, 1, 99.95, '2026-02-01');")
    cur.execute("INSERT INTO orders VALUES (102, 1, 149.50, '2026-02-02');")
    cur.execute("INSERT INTO orders VALUES (103, 2, 29.00, '2026-02-03');")
    conn.commit()
    conn.close()
    return db_file


@pytest.fixture
def uc_emulator() -> DuckDBUnityCatalogEmulator:
    """Provide local DuckDB Unity Catalog emulator seeded with IBOR data."""
    emulator = DuckDBUnityCatalogEmulator(default_catalog="am_prod")
    yield emulator
    emulator.close()


def test_list_adapters_manifests() -> None:
    """Verify list_adapters returns manifests for SQLite, Databricks UC, DuckDB, File."""
    manifests = list_adapters()
    assert isinstance(manifests, list)
    assert len(manifests) >= 4

    by_id = {m.adapter_id: m for m in manifests}

    # 1. SQLite manifest
    assert "sqlite" in by_id
    sqlite_m = by_id["sqlite"]
    assert isinstance(sqlite_m, ConnectorUIManifestDTO)
    assert sqlite_m.client_uploadable is True
    assert sqlite_m.supports_multi_table is True
    assert sqlite_m.supports_foreign_keys is True
    assert sqlite_m.execution_mode == "DIRECT_DUCKDB_ATTACH"
    assert "sqlite" in sqlite_m.schemes
    assert ".sqlite" in sqlite_m.supported_file_extensions
    assert ".db" in sqlite_m.supported_file_extensions

    # 2. Databricks Unity Catalog manifest
    assert "databricks" in by_id
    dbx_m = by_id["databricks"]
    assert isinstance(dbx_m, ConnectorUIManifestDTO)
    assert dbx_m.client_uploadable is False
    assert dbx_m.supports_catalog_hierarchy is True
    assert dbx_m.supports_governance_tags is True
    assert dbx_m.execution_mode == "REMOTE_PUSHDOWN_PLUS_ARROW_SAMPLE"
    assert "databricks" in dbx_m.schemes

    # 3. DuckDB manifest
    assert "duckdb" in by_id
    duck_m = by_id["duckdb"]
    assert duck_m.client_uploadable is True
    assert duck_m.supports_multi_table is True

    # 4. File manifest
    assert "file" in by_id
    file_m = by_id["file"]
    assert file_m.client_uploadable is True


def test_inspect_source_sqlite_with_pk_fk(sqlite_test_db: Path) -> None:
    """Verify inspect_source extracts tables, PKs, and FKs on an SQLite database."""
    req = SourceInspectRequestDTO(
        uri=str(sqlite_test_db),
    )
    resp = inspect_source(req)

    assert isinstance(resp, SourceInspectResponseDTO)
    assert resp.adapter_id == "sqlite"
    assert len(resp.tables) == 2
    assert resp.fingerprint is not None

    tables_by_name = {t["table_name"]: t for t in resp.tables}
    assert "customers" in tables_by_name
    assert "orders" in tables_by_name

    cust_tbl = tables_by_name["customers"]
    assert cust_tbl["primary_key_columns"] == ["customer_id"]
    cust_cols = {c["name"]: c for c in cust_tbl["columns"]}
    assert cust_cols["customer_id"]["is_primary_key"] is True
    assert cust_cols["customer_id"]["nullable"] is False

    orders_tbl = tables_by_name["orders"]
    assert orders_tbl["primary_key_columns"] == ["order_id"]
    orders_fks = orders_tbl["foreign_keys"]
    assert len(orders_fks) >= 1
    assert any(fk["target_table"] == "customers" for fk in orders_fks)


def test_inspect_source_sqlite_table_selection(sqlite_test_db: Path) -> None:
    """Verify explicit table selection filtering during inspection."""
    req = SourceInspectRequestDTO(
        uri=str(sqlite_test_db),
        options={
            "table_selection": {
                "mode": "EXPLICIT",
                "tables": ["orders"],
            }
        },
    )
    resp = inspect_source(req)
    assert len(resp.tables) == 1
    assert resp.tables[0]["table_name"] == "orders"


def test_inspect_source_cas_staging(sqlite_test_db: Path, tmp_path: Path) -> None:
    """Verify inspect_source resolves CAS-staged binary SQLite databases."""
    cas = ContentAddressableStore(base_dir=tmp_path / "cas_artifacts")
    raw_bytes = sqlite_test_db.read_bytes()
    cas_sha = cas.put(raw_bytes)

    req = SourceInspectRequestDTO(
        uri="staged_upload.db",
        cas_hash=cas_sha,
    )
    resp = inspect_source(req, cas_store=cas)
    assert resp.adapter_id == "sqlite"
    assert len(resp.tables) == 2


def test_inspect_source_security_enforcement(sqlite_test_db: Path) -> None:
    """Verify credentials_ref and option security fail-closed semantics."""
    # Literal secret in credentials_ref fails validation
    with pytest.raises(ValueError, match="Invalid credentials_ref"):
        inspect_source(
            SourceInspectRequestDTO(
                uri=str(sqlite_test_db),
                credentials_ref="my_raw_password_123",
            )
        )

    # Valid credentials_ref passes format validation
    valid_req = SourceInspectRequestDTO(
        uri=str(sqlite_test_db),
        credentials_ref="env:MY_DB_KEY",
    )
    resp = inspect_source(valid_req)
    assert resp.adapter_id == "sqlite"

    # Literal password in options fails closed
    with pytest.raises(ValueError, match=r"[Ll]iteral credentials in options forbidden"):
        inspect_source(
            SourceInspectRequestDTO(
                uri=str(sqlite_test_db),
                options={"password": "secret_password"},
            )
        )


def test_inspect_source_databricks_uc_emulator(uc_emulator: DuckDBUnityCatalogEmulator) -> None:
    """Verify inspect_source on Databricks Unity Catalog emulator."""
    req = SourceInspectRequestDTO(
        uri="databricks://am_prod/ibor",
        adapter="databricks",
        options={"emulator": uc_emulator, "catalog": "am_prod"},
    )
    resp = inspect_source(req)

    assert resp.adapter_id == "databricks"
    assert any(ns.get("schema_name") == "ibor" for ns in resp.namespaces)

    table_names = {t["table_name"] for t in resp.tables}
    assert "positions" in table_names
    assert "trades" in table_names
    assert "security_master" in table_names

    pos_tbl = next(t for t in resp.tables if t["table_name"] == "positions")
    assert pos_tbl["primary_key_columns"] == ["position_id"]

    pos_cols = {c["name"]: c for c in pos_tbl["columns"]}
    assert "custodian_account_no" in pos_cols
    assert pos_cols["custodian_account_no"]["governance_tags"].get("pii") == "true"
    assert pos_cols["custodian_account_no"]["governance_tags"].get("sensitivity") == "restricted"
    assert pos_cols["market_value"]["governance_tags"].get("sensitivity") == "confidential"


def test_export_vault_blueprint_studio(tmp_path: Path) -> None:
    """Verify export_vault_blueprint creates valid VaultBlueprintDTO with RFC 8785 JCS lineage."""
    session_mgr = StudioSessionManager(studio_root=tmp_path / "studio")
    genesis_ont = create_genesis_ontology(
        domain_namespace="private_equity", enterprise_owner="Data Arch"
    )
    session_meta = session_mgr.create_session(
        session_id="sess-export-001",
        domain_namespace="private_equity",
        initial_ontology=genesis_ont,
    )
    assert session_meta.session_id == "sess-export-001"

    # Export greenfield blueprint
    bp_dto = export_vault_blueprint("sess-export-001", session_manager=session_mgr)
    assert isinstance(bp_dto, VaultBlueprintDTO)
    assert bp_dto.schema_version == "1.0.0"
    assert bp_dto.domain_namespace == "private_equity"
    assert bp_dto.contract is None
    assert bp_dto.lineage.blueprint_revision == 1
    assert len(bp_dto.lineage.ontology_sha256) == 64
    assert bp_dto.lineage.contract_sha256 is None

    # Export with contract from canonical private equity fixture
    pe_contract_path = _get_pe_contract_path()
    contract_data = yaml.safe_load(pe_contract_path.read_text(encoding="utf-8"))
    bp_with_contract = export_vault_blueprint(
        "sess-export-001",
        session_manager=session_mgr,
        contract=contract_data,
    )
    assert bp_with_contract.contract is not None
    assert bp_with_contract.lineage.contract_sha256 is not None
    assert len(bp_with_contract.lineage.contract_sha256) == 64

    # Export response wrapper
    resp_dto = export_vault_blueprint_response(
        "sess-export-001",
        session_manager=session_mgr,
        contract=contract_data,
    )
    assert isinstance(resp_dto, BlueprintExportResponseDTO)
    assert resp_dto.blueprint.domain_namespace == "private_equity"
    assert len(resp_dto.target_tables) >= 1


def test_validate_pipeline_greenfield_and_existing_contract() -> None:
    """Verify validate_pipeline returns InferredExecutionPlanDTO for both modes."""
    # 1. Greenfield scenario: No contract, 1 source feed
    greenfield_input = PipelineInputDTO(
        sources=[
            SourceFeedDTO(
                feed_id="feed-crm",
                source_system="crm",
                uri="sqlite:///crm.db",
            )
        ]
    )
    plan_gf = validate_pipeline(greenfield_input)
    assert isinstance(plan_gf, InferredExecutionPlanDTO)
    assert plan_gf.primary_action == "COLD_START_N1"
    assert len(plan_gf.table_actions) == 1
    assert plan_gf["passed"] is True
    assert plan_gf["stage"] == "INPUT_VALIDATION"

    # 2. Existing contract scenario: Initial contract + new incremental feed
    pe_contract_path = _get_pe_contract_path()
    existing_contract_input = PipelineInputDTO(
        initial_contract_path=str(pe_contract_path),
        sources=[
            SourceFeedDTO(
                feed_id="feed-billing",
                source_system="stripe",
                uri="sqlite:///billing.db",
            )
        ],
    )
    plan_ec = validate_pipeline(existing_contract_input)
    assert isinstance(plan_ec, InferredExecutionPlanDTO)
    assert plan_ec.primary_action == "MULTI_SOURCE_EXPANSION"
    assert len(plan_ec.table_actions) == 1


def test_dual_blueprint_ingress_normalizer() -> None:
    """Verify DualBlueprintIngressNormalizer bundles ontology and contract into VaultBlueprint."""
    genesis_ont = create_genesis_ontology("private_equity", "EA")
    pe_contract_path = _get_pe_contract_path()
    contract_dict = yaml.safe_load(pe_contract_path.read_text(encoding="utf-8"))
    input_data = {
        "ontology": genesis_ont.model_dump(mode="json"),
        "contract": contract_dict,
        "sources": [{"source_system": "wms", "table_name": "inventory"}],
    }
    bp, contract, sources, _docs = DualBlueprintIngressNormalizer.normalize(input_data)
    assert bp is not None
    assert contract is not None
    assert len(sources) == 1
    assert sources[0]["table_name"] == "inventory"


def test_single_run_pipeline_execution_seam(tmp_path: Path) -> None:
    """Verify execute_pipeline_run_sync returns SingleRunExecutionResultDTO."""
    from opendv_agent_harness.pipeline.spec import (
        AgenticPipelineReport,
        DeterministicValidationReport,
    )

    store = StateStore(state_dir=tmp_path / "state")
    ont = create_genesis_ontology("retail", "Enterprise Arch")
    bp = create_vault_blueprint(domain_namespace="retail", ontology=ont)

    mock_report = AgenticPipelineReport(
        run_id="run-single-run-001",
        pipeline_id="pipe-retail",
        project_id="proj-retail",
        domain_namespace="retail",
        status="COMPLETED",
        overall_passed=True,
        blueprint=bp,
        evolution_diff={"added_hubs": ["hub_customer"]},
        input_validation=DeterministicValidationReport(),
        output_validation=DeterministicValidationReport(),
    )

    def mock_runner(*args: Any, **kwargs: Any) -> AgenticPipelineReport:
        return mock_report

    input_dto = PipelineInputDTO(
        blueprint=bp.model_dump(mode="json"),
        sources=[
            SourceFeedDTO(
                feed_id="feed-1",
                source_system="pos",
                uri="sqlite:///pos.db",
            )
        ],
    )

    result = execute_pipeline_run_sync(
        store,
        input_dto,
        pipeline_runner=mock_runner,
    )

    assert isinstance(result, SingleRunExecutionResultDTO)
    assert result.run_id == "run-single-run-001"
    assert result.status == "COMPLETED"
    assert result.overall_passed is True
    assert result.blueprint is not None
    assert isinstance(result.blueprint, VaultBlueprintDTO)
    assert result.evolution_diff == {"added_hubs": ["hub_customer"]}

    # Verify dict-like access
    assert result["run_id"] == "run-single-run-001"
    assert result["status"] == "COMPLETED"
    assert result.get("status") == "COMPLETED"
