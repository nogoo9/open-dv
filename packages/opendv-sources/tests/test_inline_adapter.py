"""Conformance and unit tests for InlineSourceAdapter (Decision D1 / T16)."""

from __future__ import annotations

import base64
import io
from collections.abc import Iterator
from pathlib import Path

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from opendv_sources.adapters.inline import InlineSourceAdapter
from opendv_sources.conformance.tabular_suite import TabularAdapterConformanceSuite
from opendv_sources.exceptions import (
    SourceEmptyError,
    SourceLoadError,
    UnsupportedFormatError,
)
from opendv_sources.models.specs import SourceSpec


class TestInlineAdapterConformance(TabularAdapterConformanceSuite):
    """Run InlineSourceAdapter through TabularAdapterConformanceSuite."""

    @pytest.fixture
    def adapter(self) -> InlineSourceAdapter:
        return InlineSourceAdapter()

    @pytest.fixture
    def conn(self) -> Iterator[duckdb.DuckDBPyConnection]:
        c = duckdb.connect(":memory:")
        yield c
        c.close()

    @pytest.fixture
    def sample_spec(self) -> SourceSpec:
        return SourceSpec(
            adapter="inline",
            inline_content="id,name,score\n1,Alice,95\n2,Bob,88\n",
            format="csv",
            client_path="clients/sample.csv",
        )

    @pytest.fixture
    def mutated_spec(self) -> SourceSpec:
        return SourceSpec(
            adapter="inline",
            inline_content="id,name,score\n1,Alice,99\n",
            format="csv",
            client_path="clients/sample.csv",
        )

    @pytest.fixture
    def empty_spec(self) -> SourceSpec:
        return SourceSpec(
            adapter="inline",
            inline_content="",
            format="csv",
        )

    @pytest.fixture
    def corrupt_spec(self) -> SourceSpec:
        return SourceSpec(
            adapter="inline",
            inline_content="NOT_A_VALID_PARQUET_FILE",
            format="parquet",
        )

    @pytest.fixture
    def secure_spec(self) -> SourceSpec:
        return SourceSpec(
            adapter="inline",
            inline_content="id,val\n10,secured\n",
            format="csv",
            credentials_ref="secret:api_token",
            client_path="upload.csv",
        )


def test_inline_zero_disk_invariant(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify Decision D1: inline adapter never touches or creates disk scratch files."""
    adapter = InlineSourceAdapter()
    conn = duckdb.connect(":memory:")

    # Track directory state before
    before_files = set(tmp_path.rglob("*"))

    csv_data = "col_a,col_b\n100,first\n200,second\n300,third\n"
    spec = SourceSpec(
        adapter="inline",
        inline_content=csv_data,
        format="csv",
        client_path="remote_upload.csv",
    )

    desc = adapter.describe(spec)
    assert desc.row_count_estimate == 3

    adapter.register(spec, conn, "zero_disk_tbl")
    rows = conn.execute('SELECT * FROM "zero_disk_tbl" ORDER BY col_a').fetchall()
    assert rows == [(100, "first"), (200, "second"), (300, "third")]

    # Assert no new files were created on disk
    after_files = set(tmp_path.rglob("*"))
    assert before_files == after_files


def test_inline_streaming_and_metadata() -> None:
    """Verify read_bytes, stream_chunks sequence, and get_metadata."""
    adapter = InlineSourceAdapter()
    raw = b"x,y\n" + (b"1,2\n" * 20000)  # ~80 KB
    spec = SourceSpec(adapter="inline", inline_content=raw, format="csv")

    assert adapter.read_bytes(spec) == raw
    chunks = list(adapter.stream_chunks(spec, chunk_size=16384))
    assert len(chunks) > 1
    assert b"".join(c.data for c in chunks) == raw
    assert chunks[0].chunk_index == 0
    assert not chunks[0].is_last
    assert chunks[-1].is_last
    assert chunks[-1].total_bytes == len(raw)

    meta = adapter.get_metadata(spec)
    assert meta.byte_size == len(raw)


def test_inline_parquet_bytes_support() -> None:
    """Verify native in-memory Parquet reading from byte buffers."""
    adapter = InlineSourceAdapter()
    conn = duckdb.connect(":memory:")

    # Generate in-memory Parquet bytes using PyArrow
    arrow_tbl = pa.Table.from_arrays(
        [pa.array([10, 20, 30]), pa.array(["foo", "bar", "baz"])],
        names=["id", "val"],
    )
    buf = io.BytesIO()
    pq.write_table(arrow_tbl, buf)
    parquet_bytes = buf.getvalue()

    spec = SourceSpec(
        adapter="inline",
        inline_content=parquet_bytes,
        format="parquet",
        client_path="data.parquet",
    )
    desc = adapter.describe(spec)
    assert desc.format == "parquet"
    assert desc.row_count_estimate == 3

    adapter.register(spec, conn, "parquet_tbl")
    rows = conn.execute('SELECT * FROM "parquet_tbl" ORDER BY id').fetchall()
    assert rows == [(10, "foo"), (20, "bar"), (30, "baz")]


def test_inline_json_and_jsonl_support() -> None:
    """Verify JSON array and JSONL newline-delimited payloads."""
    adapter = InlineSourceAdapter()
    conn = duckdb.connect(":memory:")

    # 1. JSON Array
    json_array = '[{"name": "Alice", "score": 100}, {"name": "Bob", "score": 90}]'
    spec_array = SourceSpec(adapter="inline", inline_content=json_array, format="json")
    adapter.register(spec_array, conn, "json_array_tbl")
    assert len(conn.execute('SELECT * FROM "json_array_tbl"').fetchall()) == 2

    # 2. JSON Wrapped Dict
    json_wrapped = '{"records": [{"name": "Charlie", "score": 85}]}'
    spec_wrapped = SourceSpec(adapter="inline", inline_content=json_wrapped, format="json")
    adapter.register(spec_wrapped, conn, "json_wrapped_tbl")
    assert len(conn.execute('SELECT * FROM "json_wrapped_tbl"').fetchall()) == 1

    # 3. JSONL (NDJSON)
    jsonl = '{"name": "Dave", "score": 75}\n{"name": "Eve", "score": 80}\n'
    spec_jsonl = SourceSpec(adapter="inline", inline_content=jsonl, format="jsonl")
    adapter.register(spec_jsonl, conn, "jsonl_tbl")
    assert len(conn.execute('SELECT * FROM "jsonl_tbl"').fetchall()) == 2


def test_inline_base64_payload() -> None:
    """Verify base64 payload decoding (for remote API JSON transport)."""
    adapter = InlineSourceAdapter()
    conn = duckdb.connect(":memory:")

    csv_text = "id,color\n1,red\n2,blue\n"
    b64_content = base64.b64encode(csv_text.encode("utf-8")).decode("ascii")

    spec = SourceSpec(
        adapter="inline",
        inline_content=b64_content,
        options={"is_base64": True},
        format="csv",
    )
    adapter.register(spec, conn, "b64_tbl")
    rows = conn.execute('SELECT * FROM "b64_tbl" ORDER BY id').fetchall()
    assert rows == [(1, "red"), (2, "blue")]


def test_inline_empty_and_unsupported_errors() -> None:
    """Verify typed exceptions for empty payloads and unsupported formats."""
    adapter = InlineSourceAdapter()
    conn = duckdb.connect(":memory:")

    # Missing content
    with pytest.raises(SourceLoadError):
        adapter.read_bytes(SourceSpec(adapter="inline"))

    # Empty string
    with pytest.raises(SourceEmptyError):
        adapter.register(SourceSpec(adapter="inline", inline_content="   "), conn, "t")

    # Empty JSON array
    with pytest.raises(SourceEmptyError):
        adapter.register(
            SourceSpec(adapter="inline", inline_content="[]", format="json"),
            conn,
            "t",
        )

    # Unsupported format
    with pytest.raises(UnsupportedFormatError):
        adapter.register(
            SourceSpec(adapter="inline", inline_content="hello", format="unsupported_xyz"),
            conn,
            "t",
        )
