"""Round 4 Track B Remediation Tests (R4-M3, R4-M5, R4-M6, R4-M7).

Verifies:
1. R4-M3: `_normalize_delta_operation` in ontology_studio router preserves `name` on
   `add_reference_enumeration`, handles nested `attribute` dict on `add_attribute`,
   and avoids slugifying URN URIs containing `:`.
2. R4-M5: CAS format mapping onto `SourceSpec.format` in sources inspect and tabular discovery.
3. R4-M6: Preflight validation on `PipelineInputDTO` in `validate_pipeline` returning `passed`
   and `diagnostics` on `InferredExecutionPlanDTO`.
4. R4-M7: `SANDBOX_COMMAND` includes `CUSTOM_SQL` and CLI executor sets query_type="CUSTOM_SQL"
   when --sql is passed.
"""

from __future__ import annotations

import argparse
from typing import Any

import duckdb

from opendv_cli.executor import LocalExecutor
from opendv_contracts.dtos.pipeline import (
    InferredExecutionPlanDTO,
    PipelineInputDTO,
    SourceFeedDTO,
)
from opendv_contracts.dtos.sources import SourceInspectRequestDTO
from opendv_platform_api.routers.ontology_studio import _normalize_delta_operation
from opendv_service.cli_spec.base_commands import SANDBOX_COMMAND
from opendv_service.use_cases.pipeline import validate_pipeline
from opendv_service.use_cases.sources_inspect import inspect_source


def test_r4_m3_normalize_delta_operation_add_reference_enumeration_preserves_name() -> None:
    """R4-M3: add_reference_enumeration must retain 'name' and not pop it."""
    raw_op = {
        "op_type": "add_reference_enumeration",
        "name": "TradeCurrencyEnum",
        "concept_uri": "urn:open-dv:concept:trade_currency",
        "values": ["USD", "EUR", "GBP"],
    }
    normalized = _normalize_delta_operation(raw_op)
    assert normalized.get("name") == "TradeCurrencyEnum"
    assert normalized.get("op_type") == "add_reference_enumeration"


def test_r4_m3_normalize_delta_operation_nested_attribute_dict_preserved() -> None:
    """R4-M3: add_attribute with nested attribute dict must not overwrite canonical fields."""
    raw_op = {
        "op_type": "add_attribute",
        "concept_uri": "urn:open-dv:concept:portfolio",
        "attribute": {
            "name": "portfolio_code",
            "attribute_name": "portfolio_code",
            "datatype": "string",
            "is_primary_key": True,
        },
    }
    normalized = _normalize_delta_operation(raw_op)
    assert normalized.get("op_type") == "add_attribute"
    assert normalized.get("concept_uri") == "urn:open-dv:concept:portfolio"
    assert normalized.get("attribute_name") == "portfolio_code"
    assert normalized.get("data_type") == "string"
    assert normalized.get("is_primary_key") is True
    assert "attribute" in normalized
    assert isinstance(normalized["attribute"], dict)
    assert normalized["attribute"]["attribute_name"] == "portfolio_code"


def test_r4_m3_normalize_delta_operation_does_not_slugify_uris_with_colon() -> None:
    """R4-M3: Only bare names should be slugified; invalid URNs with ':' must not be rewritten."""
    # 1. Bare name -> prefixed
    bare_op = {
        "op_type": "add_concept",
        "concept_uri": "fund vehicle",
    }
    norm_bare = _normalize_delta_operation(bare_op)
    assert norm_bare["concept_uri"] == "urn:ontology:entity:fund vehicle"

    # 2. URI with colon (even invalid syntax) -> preserved verbatim so regex rejects it downstream
    invalid_urn_op = {
        "op_type": "add_concept",
        "concept_uri": "urn:bad_ns:invalid concept name!",
    }
    norm_urn = _normalize_delta_operation(invalid_urn_op)
    assert norm_urn["concept_uri"] == "urn:bad_ns:invalid concept name!"


def test_r4_m5_sources_inspect_options_source_format_mapping(tmp_path: Any) -> None:
    """R4-M5: inspect_source maps req.options.source_format onto SourceSpec.format."""
    parquet_file = tmp_path / "test_data.parquet"
    conn = duckdb.connect()
    conn.execute(
        f"COPY (SELECT 1 AS id, 'alpha' AS name) TO '{parquet_file.as_posix()}' (FORMAT PARQUET)"
    )

    req = SourceInspectRequestDTO(
        uri=str(parquet_file),
        options={"source_format": "parquet"},
    )
    desc = inspect_source(req)
    assert desc.adapter_id == "file"
    assert len(desc.tables) == 1
    assert desc.tables[0]["table_name"] == "test_data"


def test_r4_m6_validate_pipeline_preflight_dto_diagnostics() -> None:
    """R4-M6: validate_pipeline executes preflight on PipelineInputDTO.

    Verifies returning passed & diagnostics.
    """
    # 1. Valid single-run greenfield input
    valid_input = PipelineInputDTO(
        sources=[
            SourceFeedDTO(
                feed_id="feed-1",
                source_system="crm",
                uri="sqlite:///crm.db",
            )
        ]
    )
    plan = validate_pipeline(valid_input)
    assert isinstance(plan, InferredExecutionPlanDTO)
    assert plan.passed is True
    assert plan.primary_action == "COLD_START_N1"
    assert isinstance(plan.diagnostics, list)

    # 2. Empty input fails preflight closed
    empty_input = PipelineInputDTO()
    plan_empty = validate_pipeline(empty_input)
    assert plan_empty.passed is False
    assert any("EMPTY_PIPELINE_INPUT" in str(d.get("code")) for d in plan_empty.diagnostics)


def test_r4_m7_sandbox_command_custom_sql_choice() -> None:
    """R4-M7: SANDBOX_COMMAND specifies CUSTOM_SQL in query_type choices."""
    opts = {opt.name: opt for opt in SANDBOX_COMMAND.options}
    query_type_opt = opts.get("query_type")
    assert query_type_opt is not None
    assert query_type_opt.choices is not None
    assert "CUSTOM_SQL" in query_type_opt.choices


def test_r4_m7_executor_selects_custom_sql_when_sql_present() -> None:
    """R4-M7: LocalExecutor selects CUSTOM_SQL when sql arg is provided."""
    executor = LocalExecutor()
    args = argparse.Namespace(
        run_id=None,
        contract=None,
        canonical_ir=None,
        sql="SELECT 1 as x",
        query_type="AUTO",
        project=None,
        engine=None,
        output_format="json",
        timeout=30,
        dialect="duckdb",
    )
    called_query_type = None

    class DummyResp:
        all_batches_passed = True

        def model_dump(self, mode: str = "json") -> dict[str, Any]:
            return {"status": "SUCCESS", "row_count": 1}

    def dummy_query(req: Any) -> Any:
        nonlocal called_query_type
        called_query_type = req.query_type
        return DummyResp()

    executor.service = argparse.Namespace(execute_sandbox_query=dummy_query)
    code, _ = executor._execute_sandbox(args)
    assert code == 0
    assert called_query_type == "CUSTOM_SQL"
