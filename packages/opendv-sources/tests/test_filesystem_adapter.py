"""Conformance and unit tests for LocalFilesystemSourceAdapter (T16 / DUP-04)."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import duckdb
import pytest

from opendv_core.paths import PathContainmentError
from opendv_sources.adapters.filesystem import LocalFilesystemSourceAdapter
from opendv_sources.conformance.tabular_suite import TabularAdapterConformanceSuite
from opendv_sources.exceptions import SourceEmptyError, SourceNotFoundError
from opendv_sources.models.specs import SourceSpec


class TestFilesystemAdapterConformance(TabularAdapterConformanceSuite):
    """Run LocalFilesystemSourceAdapter through TabularAdapterConformanceSuite."""

    @pytest.fixture
    def workspace(self, tmp_path: Path) -> Path:
        ws = tmp_path / "ws"
        ws.mkdir(parents=True)
        return ws

    @pytest.fixture
    def adapter(self, workspace: Path) -> LocalFilesystemSourceAdapter:
        return LocalFilesystemSourceAdapter(workspace_root=workspace)

    @pytest.fixture
    def conn(self) -> Iterator[duckdb.DuckDBPyConnection]:
        c = duckdb.connect(":memory:")
        yield c
        c.close()

    @pytest.fixture
    def sample_spec(self, workspace: Path) -> SourceSpec:
        p = workspace / "sample.csv"
        p.write_text("id,name,score\n1,Alice,95\n2,Bob,88\n", encoding="utf-8")
        return SourceSpec(adapter="file", uri="sample.csv", format="csv")

    @pytest.fixture
    def mutated_spec(self, workspace: Path) -> SourceSpec:
        p = workspace / "mutated.csv"
        p.write_text("id,name,score\n1,Alice,99\n", encoding="utf-8")
        return SourceSpec(adapter="file", uri="mutated.csv", format="csv")

    @pytest.fixture
    def empty_spec(self, workspace: Path) -> SourceSpec:
        p = workspace / "empty.csv"
        p.write_text("", encoding="utf-8")
        return SourceSpec(adapter="file", uri="empty.csv", format="csv")

    @pytest.fixture
    def corrupt_spec(self, workspace: Path) -> SourceSpec:
        p = workspace / "corrupt.parquet"
        p.write_bytes(b"NOT_A_VALID_PARQUET_FILE_HEADER")
        return SourceSpec(adapter="file", uri="corrupt.parquet", format="parquet")

    @pytest.fixture
    def secure_spec(self, workspace: Path) -> SourceSpec:
        p = workspace / "secure.csv"
        p.write_text("id,val\n10,secured\n", encoding="utf-8")
        return SourceSpec(
            adapter="file",
            uri="secure.csv",
            credentials_ref="secret:workspace_token",
            format="csv",
        )


def test_filesystem_streaming_and_metadata(tmp_path: Path) -> None:
    """Verify read_bytes, stream_chunks sequence, and get_metadata."""
    ws = tmp_path / "ws"
    ws.mkdir()
    adapter = LocalFilesystemSourceAdapter(workspace_root=ws)

    sample = ws / "data.csv"
    payload = b"id,val\n1,foo\n2,bar\n" * 5000  # ~95 KB
    sample.write_bytes(payload)

    spec = SourceSpec(adapter="file", uri="data.csv", format="csv")
    assert adapter.read_bytes(spec) == payload

    chunks = list(adapter.stream_chunks(spec, chunk_size=32768))
    assert len(chunks) > 1
    assert b"".join(c.data for c in chunks) == payload
    assert chunks[0].chunk_index == 0
    assert not chunks[0].is_last
    assert chunks[-1].is_last
    assert chunks[-1].total_bytes == len(payload)

    meta = adapter.get_metadata(spec)
    assert meta.byte_size == len(payload)
    assert meta.modified_at is not None


def test_filesystem_glob_pattern_reading(tmp_path: Path) -> None:
    """Verify glob patterns match files and union them in DuckDB."""
    ws = tmp_path / "ws"
    data_dir = ws / "partitioned"
    data_dir.mkdir(parents=True)
    (data_dir / "part1.csv").write_text("id,val\n1,a\n2,b\n", encoding="utf-8")
    (data_dir / "part2.csv").write_text("id,val\n3,c\n", encoding="utf-8")

    adapter = LocalFilesystemSourceAdapter(workspace_root=ws)
    conn = duckdb.connect(":memory:")

    spec = SourceSpec(adapter="file", uri="partitioned/*.csv", format="csv")
    adapter.register(spec, conn, "glob_table")

    rows = conn.execute('SELECT * FROM "glob_table" ORDER BY id').fetchall()
    assert len(rows) == 3
    assert rows == [(1, "a"), (2, "b"), (3, "c")]

    desc = adapter.describe(spec)
    assert desc.row_count_estimate == 3


def test_filesystem_directory_walk(tmp_path: Path) -> None:
    """Verify directory URI enumerates all files recursively."""
    ws = tmp_path / "ws"
    dir_path = ws / "subdir"
    dir_path.mkdir(parents=True)
    (dir_path / "f1.csv").write_text("x,y\n10,20\n", encoding="utf-8")
    (dir_path / "f2.csv").write_text("x,y\n30,40\n", encoding="utf-8")

    adapter = LocalFilesystemSourceAdapter(workspace_root=ws)
    conn = duckdb.connect(":memory:")

    spec = SourceSpec(adapter="file", uri="subdir", format="csv")
    adapter.register(spec, conn, "dir_table")
    rows = conn.execute('SELECT * FROM "dir_table" ORDER BY x').fetchall()
    assert len(rows) == 2


def test_filesystem_path_containment_security(tmp_path: Path) -> None:
    """Verify path traversal, null bytes, and jail breaks are rejected."""
    ws = tmp_path / "ws"
    ws.mkdir()
    adapter = LocalFilesystemSourceAdapter(workspace_root=ws)

    # 1. Traversal token '..'
    with pytest.raises(PathContainmentError):
        adapter.read_bytes(SourceSpec(adapter="file", uri="../outside.txt"))

    # 2. Null byte injection
    with pytest.raises(PathContainmentError):
        adapter.read_bytes(SourceSpec(adapter="file", uri="file\0.csv"))

    # 3. Absolute path escaping workspace root
    outside = tmp_path / "outside.csv"
    outside.write_text("a,b\n1,2\n", encoding="utf-8")
    with pytest.raises(PathContainmentError):
        adapter.read_bytes(SourceSpec(adapter="file", uri=str(outside.resolve())))


def test_filesystem_missing_and_empty_errors(tmp_path: Path) -> None:
    """Verify typed exceptions for missing paths and empty directories."""
    ws = tmp_path / "ws"
    ws.mkdir()
    adapter = LocalFilesystemSourceAdapter(workspace_root=ws)
    conn = duckdb.connect(":memory:")

    # Missing file
    with pytest.raises(SourceNotFoundError):
        adapter.read_bytes(SourceSpec(adapter="file", uri="nonexistent.csv"))

    with pytest.raises(SourceNotFoundError):
        adapter.register(SourceSpec(adapter="file", uri="nonexistent.csv"), conn, "t")

    # Glob matching nothing
    with pytest.raises(SourceNotFoundError):
        adapter.register(SourceSpec(adapter="file", uri="*.nonexistent"), conn, "t")

    # Empty directory
    empty_dir = ws / "empty_dir"
    empty_dir.mkdir()
    with pytest.raises(SourceEmptyError):
        adapter.register(SourceSpec(adapter="file", uri="empty_dir", format="csv"), conn, "t")
