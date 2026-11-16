"""Unit tests for Single-Run contracts, VaultBlueprint, and Universal SourceFeed DTOs (WS1.4).

Validates:
- Universal SourceFeedDTO (namespace, table_selection, sampling_policy, credentials_ref)
- Rejection of literal secrets in credentials_ref (strict 'env:NAME' / 'secret:NAME' enforcement)
- ConnectorUIManifestDTO capabilities descriptor
- SourceInspectRequestDTO and SourceInspectResponseDTO
- InferredExecutionPlanDTO (7-row matrix per-table and per-doc actions)
- PipelineInputDTO and BlueprintExportResponseDTO
- Backward-compatible PipelineValidateRequest and PipelineRunRequest
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from opendv_contracts.dtos import (
    BlueprintExportResponseDTO,
    BlueprintLineageDTO,
    ConnectorUIManifestDTO,
    DocExecutionActionDTO,
    InferredExecutionPlanDTO,
    NamespaceSpecDTO,
    PipelineInputDTO,
    PipelineValidateRequest,
    SamplingPolicyDTO,
    SourceFeedDTO,
    SourceInspectRequestDTO,
    SourceInspectResponseDTO,
    TableExecutionActionDTO,
    TableSelectionDTO,
    VaultBlueprintDTO,
)


def test_source_feed_dto_sqlite_valid() -> None:
    """Validate SourceFeedDTO configured for multi-table SQLite."""
    feed = SourceFeedDTO(
        feed_id="sqlite_sec_treasury",
        source_system="sec_edgar_treasury",
        uri="sqlite:///data/public_am/am_sec_treasury_ibor.sqlite",
        adapter="sqlite",
        table_selection=TableSelectionDTO(
            mode="EXPLICIT",
            tables=["issuers", "securities", "holdings_13f"],
        ),
        sampling_policy=SamplingPolicyDTO(
            max_sandbox_rows=5000,
            strategy="DETERMINISTIC_LIMIT",
        ),
        target_dialect="duckdb",
    )
    assert feed.feed_id == "sqlite_sec_treasury"
    assert feed.adapter == "sqlite"
    assert feed.table_selection is not None
    assert feed.table_selection.mode == "EXPLICIT"
    assert feed.table_selection.tables == ["issuers", "securities", "holdings_13f"]
    assert feed.sampling_policy.max_sandbox_rows == 5000


def test_source_feed_dto_databricks_uc_with_safe_credentials() -> None:
    """Validate Databricks Unity Catalog 3-level namespace and env credential reference."""
    feed = SourceFeedDTO(
        feed_id="databricks_ibor_positions",
        source_system="ibor_databricks",
        uri="databricks://dbc-prod-001.cloud.databricks.com/am_prod/ibor",
        adapter="databricks",
        namespace=NamespaceSpecDTO(catalog="am_prod", schema_name="ibor"),
        table_selection=TableSelectionDTO(mode="ALL_USER_TABLES"),
        credentials_ref="env:DATABRICKS_TOKEN",
        sampling_policy=SamplingPolicyDTO(
            max_sandbox_rows=10000,
            strategy="TABLESAMPLE_BERNOULLI",
            sample_ratio=0.10,
        ),
        target_dialect="databricks",
        options={"http_path": "/sql/1.0/warehouses/abc123def456"},
    )
    assert feed.namespace is not None
    assert feed.namespace.catalog == "am_prod"
    assert feed.namespace.schema_name == "ibor"
    assert feed.credentials_ref == "env:DATABRICKS_TOKEN"
    assert feed.target_dialect == "databricks"


def test_source_feed_dto_rejects_literal_secrets() -> None:
    """Security invariant: credentials_ref strictly rejects literal tokens/secrets."""
    # Plain secret literal rejected
    with pytest.raises(ValidationError) as exc:
        SourceFeedDTO(
            feed_id="feed_insecure",
            source_system="insecure_system",
            uri="databricks://host/catalog/schema",
            credentials_ref="dapi_1234567890abcdef_raw_token",
        )
    assert "Invalid credentials_ref" in str(exc.value)

    # Secret without env: or secret: prefix rejected
    with pytest.raises(ValidationError):
        SourceFeedDTO(
            feed_id="feed_insecure",
            source_system="insecure_system",
            uri="databricks://host/catalog/schema",
            credentials_ref="MY_TOKEN_VAR",
        )

    # Valid secret: format accepted
    valid_secret = SourceFeedDTO(
        feed_id="feed_secure",
        source_system="secure_system",
        uri="databricks://host/catalog/schema",
        credentials_ref="secret:AWS_SM_DATABRICKS_KEY",
    )
    assert valid_secret.credentials_ref == "secret:AWS_SM_DATABRICKS_KEY"


def test_connector_ui_manifest_dto() -> None:
    """Validate dynamic UI manifest descriptor."""
    manifest = ConnectorUIManifestDTO(
        adapter_id="databricks",
        display_name="Databricks Unity Catalog",
        description="3-level catalog introspection with column_tags and Delta time travel",
        schemes=["databricks", "uc"],
        supported_file_extensions=[],
        execution_mode="REMOTE_PUSHDOWN_PLUS_ARROW_SAMPLE",
        client_uploadable=False,
        supports_multi_table=True,
        supports_catalog_hierarchy=True,
        supports_foreign_keys=True,
        supports_governance_tags=True,
        supports_time_travel=True,
        sample_uri="databricks://<host>/<catalog>/<schema>",
        connection_fields=[
            {"name": "host", "label": "Workspace Hostname", "type": "string", "required": True},
            {
                "name": "http_path",
                "label": "SQL Warehouse HTTP Path",
                "type": "string",
                "required": True,
            },
        ],
        secret_ref_fields=["credentials_ref"],
    )
    assert manifest.adapter_id == "databricks"
    assert manifest.execution_mode == "REMOTE_PUSHDOWN_PLUS_ARROW_SAMPLE"
    assert manifest.supports_catalog_hierarchy is True
    assert manifest.client_uploadable is False


def test_source_inspect_request_and_response() -> None:
    """Validate source inspection request and response DTOs."""
    req = SourceInspectRequestDTO(
        uri="sqlite:///workspace/data.sqlite",
        adapter="sqlite",
        namespace=NamespaceSpecDTO(schema_name="main"),
        options={"read_only": True},
    )
    assert req.adapter == "sqlite"

    resp = SourceInspectResponseDTO(
        adapter_id="sqlite",
        namespaces=[{"schema_name": "main"}],
        tables=[
            {
                "table_name": "securities",
                "row_count_estimate": 4500,
                "columns": [
                    {"name": "isin", "type": "VARCHAR(12)", "is_primary_key": True},
                    {"name": "security_name", "type": "VARCHAR(255)"},
                ],
            }
        ],
        capabilities={"supports_foreign_keys": True},
        fingerprint="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        inspected_at_utc="2026-11-15T04:00:00Z",
    )
    assert resp.tables[0]["table_name"] == "securities"
    assert resp.fingerprint is not None


def test_inferred_execution_plan_dto() -> None:
    """Validate 7-row matrix InferredExecutionPlanDTO."""
    plan = InferredExecutionPlanDTO(
        plan_id="plan_hybrid_001",
        primary_action="HYBRID_PER_SOURCE",
        table_actions=[
            TableExecutionActionDTO(
                source_system="sqlite_sec",
                table_name="issuers",
                action="INCREMENTAL_ONBOARDING",
                matched_existing_entity="hub_issuer",
            ),
            TableExecutionActionDTO(
                source_system="sqlite_sec",
                table_name="holdings_drifted",
                action="INTRA_SOURCE_DRIFT",
                matched_existing_entity="sat_holdings",
                drift_findings=[
                    {"category": "ADDITIVE", "column_name": "esg_score", "action": "AUTO-FIX"}
                ],
            ),
        ],
        doc_actions=[
            DocExecutionActionDTO(
                doc_id="doc_offering_memo",
                action="EXTRACT_SPANS_AND_LINKS",
                target_hub="hub_document",
            )
        ],
        inferred_at_utc="2026-11-15T04:05:00Z",
        explanation=(
            "Hybrid single-run: 1 incremental table, 1 drifted table (Alg A.9a), "
            "1 document span extraction."
        ),
    )
    assert plan.primary_action == "HYBRID_PER_SOURCE"
    assert len(plan.table_actions) == 2
    assert plan.table_actions[1].action == "INTRA_SOURCE_DRIFT"
    assert len(plan.doc_actions) == 1


def test_pipeline_input_dto() -> None:
    """Validate single-run PipelineInputDTO."""
    blueprint_dto = VaultBlueprintDTO(
        domain_namespace="asset_management",
        lineage=BlueprintLineageDTO(
            blueprint_revision=1,
            domain_namespace="asset_management",
            ontology_sha256="0" * 64,
        ),
        ontology={"ontology_version": "1.0.0", "canonical_business_concepts": []},
    )

    feed = SourceFeedDTO(
        feed_id="f1",
        source_system="src",
        uri="file:///path/to/data.parquet",
    )

    input_dto = PipelineInputDTO(
        blueprint=blueprint_dto,
        sources=[feed],
        target_dialect="databricks",
    )
    assert input_dto.target_dialect == "databricks"
    assert len(input_dto.sources) == 1
    assert input_dto.blueprint is not None


def test_blueprint_export_response_dto() -> None:
    """Validate Studio BlueprintExportResponseDTO."""
    resp = BlueprintExportResponseDTO(
        blueprint=VaultBlueprintDTO(
            domain_namespace="asset_management",
            lineage=BlueprintLineageDTO(
                blueprint_revision=1,
                domain_namespace="asset_management",
                ontology_sha256="0" * 64,
            ),
            ontology={"ontology_version": "1.0.0", "canonical_business_concepts": []},
        ),
        raw_vault_ddl="CREATE TABLE raw_vault.hub_fund (...);",
        target_tables=[{"name": "hub_fund", "type": "HUB"}],
        exported_at_utc="2026-11-15T04:10:00Z",
    )
    assert resp.raw_vault_ddl.startswith("CREATE TABLE")
    assert resp.target_tables[0]["name"] == "hub_fund"


def test_pipeline_validate_request_backward_compatibility() -> None:
    """Validate backward compatibility of PipelineValidateRequest with single-run extensions."""
    # Legacy style with input_spec_content
    req_legacy = PipelineValidateRequest(
        input_spec_content="legacy_yaml_content",
    )
    assert req_legacy.input_spec_content == "legacy_yaml_content"

    # Single-run style with blueprint and universal sources
    req_single_run = PipelineValidateRequest(
        blueprint={"domain_namespace": "asset_management"},
        sources=[
            {
                "feed_id": "feed_1",
                "source_system": "sys_1",
                "uri": "sqlite:///test.sqlite",
            }
        ],
        target_dialect="databricks",
    )
    assert req_single_run.target_dialect == "databricks"
    assert len(req_single_run.sources) == 1
