"""Comprehensive T16 test suite for pluggable source & format adapters (Phase P4 / Task P4.6).

Covers:
1. Conformance suite execution for testkit's RecordsSourceAdapter.
2. Extensibility proof via dynamic entry point discovery with zero core changes.
3. Cross-format derivation & Graph B profiling (CSV, TSV, Parquet, JSONL, SQLite, DuckDB).
4. Cross-format pipeline and compiled 4-Zone SQL equivalence (identical contract & SQL SHAs).
5. Document extraction equivalence (TXT vs PDF vs CRLF, span offset grounding, corrupt PDF).
6. Negative & failure invariants (empty, corrupt, unsupported, secrets redacted, unextended).
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import duckdb
import pytest

from opendv_agent_harness.pipeline import (
    HarnessSetupConfig,
    PipelineInputSpec,
    run_agentic_vault_pipeline,
)
from opendv_contracts.errors import ErrorCode, get_error_mapping
from opendv_duckdb_sandbox.graph_b_profiler import GraphBProfiler
from opendv_sources import (
    DocumentExtractionError,
    PdfDocumentSourceAdapter,
    SourceEmptyError,
    SourceError,
    SourceLoadError,
    SourceSpec,
    SourceTableEmptyError,
    TabularAdapterConformanceSuite,
    TextDocumentSourceAdapter,
    UnsupportedFormatError,
    get_source_registry,
)
from opendv_sources.registry import SourceAdapterRegistry
from opendv_testkit.sources import RecordsSourceAdapter
from tests.architecture.test_hardening_t14 import _FailingLlmHarness
from tests.parity.derive_sources import GCV_DIR, REPO_ROOT, derive_format_sources

SRC_MATRIX_DIR = REPO_ROOT / "examples" / "scenarios" / "src_matrix"


# =============================================================================
# 1. Conformance Suite Execution for Testkit RecordsSourceAdapter
# =============================================================================


class TestRecordsAdapterConformance(TabularAdapterConformanceSuite):
    """Run RecordsSourceAdapter through TabularAdapterConformanceSuite."""

    @pytest.fixture
    def adapter(self) -> RecordsSourceAdapter:
        return RecordsSourceAdapter()

    @pytest.fixture
    def conn(self) -> Iterator[duckdb.DuckDBPyConnection]:
        c = duckdb.connect(":memory:")
        yield c
        c.close()

    @pytest.fixture
    def sample_spec(self) -> SourceSpec:
        rows = [{"id": 1, "name": "Alice", "score": 95}, {"id": 2, "name": "Bob", "score": 88}]
        return SourceSpec(adapter="records", options={"records": rows}, format="records")

    @pytest.fixture
    def mutated_spec(self) -> SourceSpec:
        rows = [{"id": 1, "name": "Alice", "score": 99}, {"id": 2, "name": "Bob", "score": 88}]
        return SourceSpec(adapter="records", options={"records": rows}, format="records")

    @pytest.fixture
    def empty_spec(self) -> SourceSpec:
        return SourceSpec(adapter="records", options={"records": []}, format="records")

    @pytest.fixture
    def corrupt_spec(self) -> SourceSpec:
        return SourceSpec(
            adapter="records", options={"records": "INVALID_NOT_A_LIST"}, format="records"
        )

    @pytest.fixture
    def secure_spec(self) -> SourceSpec:
        return SourceSpec(
            adapter="records",
            uri="memory://records?token=secret123",
            options={"records": [{"id": 1, "name": "Secure"}]},
            format="records",
        )


# =============================================================================
# 2. Extensibility Proof via Dynamic Discovery
# =============================================================================


def test_extensibility_plugin_proof() -> None:
    """Verify test-only adapter registered in opendv-testkit is dynamically discovered."""
    registry = get_source_registry()
    adapters = registry.list_adapters()
    assert "records" in adapters, f"Expected 'records' adapter in {adapters}"

    records_adapter = registry.get_tabular_adapter("records")
    assert isinstance(records_adapter, RecordsSourceAdapter)

    # Register into DuckDB relation and profile columns
    spec = SourceSpec(
        adapter="records",
        options={
            "records": [
                {"customer_id": "C001", "name": "Acme Corp", "balance": 1500.0},
                {"customer_id": "C002", "name": "Globex", "balance": 2400.0},
            ]
        },
    )
    with GraphBProfiler() as profiler:
        records_adapter.register(spec, profiler.conn, "t_records")
        cols = profiler.profile_table_columns("t_records")
        assert len(cols) == 3
        col_names = {c.column_name for c in cols}
        assert col_names == {"customer_id", "name", "balance"}
        for c in cols:
            assert c.row_count == 2
            assert c.null_count == 0


# =============================================================================
# 3. Cross-Format Dataset Derivation and Profiling Equivalence
# =============================================================================


def test_cross_format_dataset_derivation_and_profiling(tmp_path: Path) -> None:
    """Verify Graph B profiling equivalence across all derived tabular formats."""
    datasets = derive_format_sources(tmp_path)
    registry = get_source_registry()

    crm_profiles: dict[str, Any] = {}
    erp_profiles: dict[str, Any] = {}

    for fmt_name, specs in datasets.items():
        with GraphBProfiler() as profiler:
            c_adapter = registry.get_tabular_adapter(specs["crm"].adapter)
            c_adapter.register(specs["crm"], profiler.conn, "v_crm")
            c_cols = {c.column_name: c for c in profiler.profile_table_columns("v_crm")}
            crm_profiles[fmt_name] = c_cols

            e_adapter = registry.get_tabular_adapter(specs["erp"].adapter)
            e_adapter.register(specs["erp"], profiler.conn, "v_erp")
            e_cols = {c.column_name: c for c in profiler.profile_table_columns("v_erp")}
            erp_profiles[fmt_name] = e_cols

    base_crm = crm_profiles["csv"]
    base_erp = erp_profiles["csv"]

    for fmt_name in ["tsv", "parquet", "jsonl", "sqlite", "duckdb", "inline"]:
        curr_crm = crm_profiles[fmt_name]
        curr_erp = erp_profiles[fmt_name]

        assert len(curr_crm) == len(base_crm) == 8, f"CRM column count mismatch for {fmt_name}"
        assert len(curr_erp) == len(base_erp) == 7, f"ERP column count mismatch for {fmt_name}"

        for col_name, base_col in base_crm.items():
            c_col = curr_crm[col_name]
            assert c_col.row_count == base_col.row_count == 6
            assert c_col.null_count == base_col.null_count
            assert c_col.uniqueness_ratio == base_col.uniqueness_ratio
            assert c_col.bk_candidate_verdict == base_col.bk_candidate_verdict

        for col_name, base_col in base_erp.items():
            e_col = curr_erp[col_name]
            assert e_col.row_count == base_col.row_count == 6
            assert e_col.null_count == base_col.null_count
            assert e_col.uniqueness_ratio == base_col.uniqueness_ratio
            assert e_col.bk_candidate_verdict == base_col.bk_candidate_verdict


# =============================================================================
# 4. Cross-Format Pipeline and Compiled 4-Zone SQL Equivalence
# =============================================================================


def test_cross_format_pipeline_and_compiled_sql_equivalence(tmp_path: Path) -> None:
    """Verify identical contract and compiled 4-Zone SQL SHAs across formats."""
    setup = HarnessSetupConfig(stage_output_policy="symbolic_completion")
    harness = _FailingLlmHarness()
    input_spec = PipelineInputSpec.from_file(GCV_DIR / "pipeline_input.yaml")

    # 1. Base CSV pipeline run
    out_csv = tmp_path / "out_csv"
    rep_csv = run_agentic_vault_pipeline(
        input_spec.model_copy(update={"waves": [input_spec.waves[0]]}),
        setup,
        output_dir=out_csv,
        base_dir=GCV_DIR,
        harness=harness,
    )
    assert rep_csv.status == "COMPLETED"
    assert rep_csv.overall_passed is True
    c_sha_base = hashlib.sha256((out_csv / "final_contract.yaml").read_bytes()).hexdigest()
    s_sha_base = hashlib.sha256((out_csv / "final_four_zone.sql").read_bytes()).hexdigest()

    # 2. Derive datasets under tmp_path
    datasets = derive_format_sources(tmp_path)

    for fmt_name in ["parquet", "jsonl", "duckdb"]:
        manifest = input_spec.model_dump(mode="json")
        manifest["waves"][0]["sources"] = [
            {
                "source_system": "CRM_SALESFORCE",
                "table_name": "crm_customers",
                "data_path": datasets[fmt_name]["crm"].uri,
                "source": datasets[fmt_name]["crm"].model_dump(mode="json", exclude_none=True),
            },
            {
                "source_system": "ERP_NETSUITE",
                "table_name": "erp_orders",
                "data_path": datasets[fmt_name]["erp"].uri,
                "source": datasets[fmt_name]["erp"].model_dump(mode="json", exclude_none=True),
            },
        ]
        out_fmt = tmp_path / f"out_{fmt_name}"
        rep_fmt = run_agentic_vault_pipeline(
            PipelineInputSpec.model_validate(manifest).model_copy(
                update={"waves": [PipelineInputSpec.model_validate(manifest).waves[0]]}
            ),
            setup,
            output_dir=out_fmt,
            base_dir=GCV_DIR,
            harness=harness,
        )
        assert rep_fmt.status == "COMPLETED", f"Pipeline status not COMPLETED for {fmt_name}"
        assert rep_fmt.overall_passed is True, f"Pipeline failed for format {fmt_name}"

        c_sha_fmt = hashlib.sha256((out_fmt / "final_contract.yaml").read_bytes()).hexdigest()
        s_sha_fmt = hashlib.sha256((out_fmt / "final_four_zone.sql").read_bytes()).hexdigest()

        assert c_sha_fmt == c_sha_base, (
            f"Contract SHA mismatch for format {fmt_name}: {c_sha_fmt} != {c_sha_base}"
        )
        assert s_sha_fmt == s_sha_base, (
            f"Compiled SQL SHA mismatch for format {fmt_name}: {s_sha_fmt} != {s_sha_base}"
        )


# =============================================================================
# 5. Document Extraction Equivalence and Page Map
# =============================================================================


def test_document_extraction_equivalence_and_page_map() -> None:
    """Verify TXT, PDF, and CRLF fixtures produce identical text, page map, and offsets."""
    text_adapter = TextDocumentSourceAdapter(workspace_root=SRC_MATRIX_DIR)
    pdf_adapter = PdfDocumentSourceAdapter(workspace_root=SRC_MATRIX_DIR)

    txt_spec = SourceSpec(adapter="text", uri="enterprise_msa_addendum_2026.txt")
    pdf_spec = SourceSpec(adapter="pdf", uri="enterprise_msa_addendum_2026.pdf")
    crlf_spec = SourceSpec(adapter="text", uri="enterprise_msa_addendum_2026_crlf.txt")

    doc_txt = text_adapter.extract_document(txt_spec)
    doc_pdf = pdf_adapter.extract_document(pdf_spec)
    doc_crlf = text_adapter.extract_document(crlf_spec)

    # 1. Byte-identical normalized text across all three
    assert "\r" not in doc_txt.normalized_text
    assert "\r" not in doc_pdf.normalized_text
    assert "\r" not in doc_crlf.normalized_text
    assert doc_txt.normalized_text == doc_pdf.normalized_text == doc_crlf.normalized_text
    assert doc_txt.char_count == doc_pdf.char_count == doc_crlf.char_count == 733

    # 2. Page map assertions
    assert len(doc_pdf.page_map) == 2
    assert doc_pdf.page_map[0].page_number == 1
    assert doc_pdf.page_map[0].start_char == 0
    assert doc_pdf.page_map[0].end_char == 400
    assert doc_pdf.page_map[1].page_number == 2
    assert doc_pdf.page_map[1].start_char == 400
    assert doc_pdf.page_map[1].end_char == 733

    # 3. Span offsets
    quote_u1 = "Apex Industrial Holdings LLC (Tax ID: TAX-US-900101)"
    start_u1_txt = doc_txt.normalized_text.find(quote_u1)
    start_u1_pdf = doc_pdf.normalized_text.find(quote_u1)
    assert start_u1_txt == start_u1_pdf >= 0
    assert doc_pdf.page_map[0].start_char <= start_u1_pdf < doc_pdf.page_map[0].end_char

    quote_u3 = "Rheinland Precision Holding Group"
    start_u3_txt = doc_txt.normalized_text.find(quote_u3)
    start_u3_pdf = doc_pdf.normalized_text.find(quote_u3)
    assert start_u3_txt == start_u3_pdf >= 400
    assert doc_pdf.page_map[1].start_char <= start_u3_pdf < doc_pdf.page_map[1].end_char

    # 4. Corrupt PDF fails loudly, never emits replacement chars
    corrupt_spec = SourceSpec(adapter="pdf", uri="corrupt_document.pdf")
    with pytest.raises((DocumentExtractionError, SourceLoadError)):
        pdf_adapter.extract_document(corrupt_spec)


# =============================================================================
# 6. Negative and Failure Invariants
# =============================================================================


def test_negative_and_failure_invariants(tmp_path: Path) -> None:
    """Verify empty, corrupt, unsupported format, and secret redaction invariants."""
    registry = get_source_registry()
    fs_adapter = registry.get_tabular_adapter("file")

    # 1. Empty source yields typed error
    empty_csv = tmp_path / "empty.csv"
    empty_csv.write_text("", encoding="utf-8")
    with (
        duckdb.connect(":memory:") as con,
        pytest.raises((SourceTableEmptyError, SourceEmptyError, SourceLoadError)),
    ):
        fs_adapter.register(
            SourceSpec(
                adapter="file",
                uri=str(empty_csv.resolve()),
                format="csv",
                options={"allow_temp": True},
            ),
            con,
            "empty_tbl",
        )

    # 2. Corrupt source yields typed SourceLoadError / SourceError
    corrupt_pq = tmp_path / "corrupt.parquet"
    corrupt_pq.write_bytes(b"NOT_A_VALID_PARQUET_FILE")
    with duckdb.connect(":memory:") as con, pytest.raises(SourceError):
        fs_adapter.register(
            SourceSpec(
                adapter="file",
                uri=str(corrupt_pq.resolve()),
                format="parquet",
                options={"allow_temp": True},
            ),
            con,
            "corrupt_tbl",
        )

    # 3. Unknown format yields UNSUPPORTED_FORMAT
    unknown_file = tmp_path / "data.xyz"
    unknown_file.write_text("sample content", encoding="utf-8")
    unknown_spec = SourceSpec(
        adapter="file",
        uri=str(unknown_file.resolve()),
        format="xyz_format",
        options={"allow_temp": True},
    )
    with pytest.raises(UnsupportedFormatError) as exc_info:
        fs_adapter.describe(unknown_spec)
    assert exc_info.value.error_code == ErrorCode.UNSUPPORTED_FORMAT
    assert "xyz_format" in exc_info.value.message

    # 4. Secret redaction in descriptor
    secure_file = tmp_path / "secure.csv"
    secure_file.write_text("id,val\n1,secret_data\n", encoding="utf-8")
    secure_spec = SourceSpec(
        adapter="file",
        uri=str(secure_file.resolve()),
        format="csv",
        credentials_ref="secret:supersecret_vault_token",
        options={"allow_temp": True},
    )
    desc = fs_adapter.describe(secure_spec)
    for token in ["supersecret", "vault_token", "password"]:
        assert token not in desc.redacted_uri.lower()

    # Literal secrets in options are rejected
    with pytest.raises(ValueError):
        SourceSpec(adapter="file", uri="test.csv", options={"password": "raw"})

    # 5. Unextended core alone behavior (empty registry) fails loudly with UNSUPPORTED_FORMAT
    isolated_registry = SourceAdapterRegistry(entry_point_group="empty.nonexistent.group")
    with pytest.raises(UnsupportedFormatError) as iso_exc:
        isolated_registry.get_tabular_adapter("file")
    assert iso_exc.value.error_code == ErrorCode.UNSUPPORTED_FORMAT

    # Check mapping to exit code 2 and HTTP 415
    exit_code, http_status = get_error_mapping(iso_exc.value.error_code)
    assert exit_code == 2
    assert http_status == 415
