"""Conformance and unit tests for DuckdbAttachSourceAdapter (T16 / DUP-04)."""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Iterator
from pathlib import Path

import duckdb
import pytest
from pydantic import ValidationError

from opendv_core.paths import PathContainmentError
from opendv_sources.adapters.duckdb_attach import DuckdbAttachSourceAdapter
from opendv_sources.conformance.tabular_suite import TabularAdapterConformanceSuite
from opendv_sources.exceptions import SourceLoadError, SourceNotFoundError, SourceTableEmptyError
from opendv_sources.models.specs import SourceSpec
from opendv_sources.registry.source_registry import get_source_registry


class TestDuckdbAttachAdapterConformance(TabularAdapterConformanceSuite):
    """Run DuckdbAttachSourceAdapter (SQLite mode) through TabularAdapterConformanceSuite."""

    @pytest.fixture
    def workspace(self, tmp_path: Path) -> Path:
        ws = tmp_path / "ws"
        ws.mkdir(parents=True)
        return ws

    @pytest.fixture
    def adapter(self, workspace: Path) -> DuckdbAttachSourceAdapter:
        return DuckdbAttachSourceAdapter(workspace_root=workspace)

    @pytest.fixture
    def conn(self) -> Iterator[duckdb.DuckDBPyConnection]:
        c = duckdb.connect(":memory:")
        yield c
        c.close()

    @pytest.fixture
    def sample_spec(self, workspace: Path) -> SourceSpec:
        p = workspace / "sample.sqlite"
        sconn = sqlite3.connect(p)
        sconn.execute("CREATE TABLE sample_tbl (id INT, name TEXT, score INT)")
        sconn.execute("INSERT INTO sample_tbl VALUES (1, 'Alice', 95), (2, 'Bob', 88)")
        sconn.commit()
        sconn.close()
        return SourceSpec(adapter="duckdb_attach", uri="sample.sqlite#sample_tbl", format="sqlite")

    @pytest.fixture
    def mutated_spec(self, workspace: Path) -> SourceSpec:
        p = workspace / "mutated.sqlite"
        sconn = sqlite3.connect(p)
        sconn.execute("CREATE TABLE sample_tbl (id INT, name TEXT, score INT)")
        sconn.execute("INSERT INTO sample_tbl VALUES (1, 'Alice', 99)")
        sconn.commit()
        sconn.close()
        return SourceSpec(adapter="duckdb_attach", uri="mutated.sqlite#sample_tbl", format="sqlite")

    @pytest.fixture
    def empty_spec(self, workspace: Path) -> SourceSpec:
        p = workspace / "empty.sqlite"
        p.write_bytes(b"")
        return SourceSpec(adapter="duckdb_attach", uri="empty.sqlite#sample_tbl", format="sqlite")

    @pytest.fixture
    def corrupt_spec(self, workspace: Path) -> SourceSpec:
        p = workspace / "corrupt.sqlite"
        p.write_bytes(b"NOT_A_VALID_SQLITE_DATABASE_HEADER_DATA")
        return SourceSpec(adapter="duckdb_attach", uri="corrupt.sqlite#sample_tbl", format="sqlite")

    @pytest.fixture
    def secure_spec(self, workspace: Path) -> SourceSpec:
        p = workspace / "secure.sqlite"
        sconn = sqlite3.connect(p)
        sconn.execute("CREATE TABLE sample_tbl (id INT, val TEXT)")
        sconn.execute("INSERT INTO sample_tbl VALUES (10, 'secured')")
        sconn.commit()
        sconn.close()
        return SourceSpec(
            adapter="duckdb_attach",
            uri="secure.sqlite#sample_tbl",
            credentials_ref="secret:workspace_token",
            format="sqlite",
        )


class TestDuckdbNativeAttachConformance(TabularAdapterConformanceSuite):
    """Run DuckdbAttachSourceAdapter (DuckDB mode) through TabularAdapterConformanceSuite."""

    @pytest.fixture
    def workspace(self, tmp_path: Path) -> Path:
        ws = tmp_path / "ws_native"
        ws.mkdir(parents=True)
        return ws

    @pytest.fixture
    def adapter(self, workspace: Path) -> DuckdbAttachSourceAdapter:
        return DuckdbAttachSourceAdapter(workspace_root=workspace)

    @pytest.fixture
    def conn(self) -> Iterator[duckdb.DuckDBPyConnection]:
        c = duckdb.connect(":memory:")
        yield c
        c.close()

    @pytest.fixture
    def sample_spec(self, workspace: Path) -> SourceSpec:
        p = workspace / "sample.duckdb"
        dconn = duckdb.connect(str(p))
        dconn.execute("CREATE TABLE d_tbl (id INT, name TEXT, score INT)")
        dconn.execute("INSERT INTO d_tbl VALUES (1, 'Alice', 95), (2, 'Bob', 88)")
        dconn.close()
        return SourceSpec(adapter="duckdb_attach", uri="sample.duckdb#d_tbl", format="duckdb")

    @pytest.fixture
    def mutated_spec(self, workspace: Path) -> SourceSpec:
        p = workspace / "mutated.duckdb"
        dconn = duckdb.connect(str(p))
        dconn.execute("CREATE TABLE d_tbl (id INT, name TEXT, score INT)")
        dconn.execute("INSERT INTO d_tbl VALUES (1, 'Alice', 99)")
        dconn.close()
        return SourceSpec(adapter="duckdb_attach", uri="mutated.duckdb#d_tbl", format="duckdb")

    @pytest.fixture
    def empty_spec(self, workspace: Path) -> SourceSpec:
        p = workspace / "empty.duckdb"
        p.write_bytes(b"")
        return SourceSpec(adapter="duckdb_attach", uri="empty.duckdb#d_tbl", format="duckdb")

    @pytest.fixture
    def corrupt_spec(self, workspace: Path) -> SourceSpec:
        p = workspace / "corrupt.duckdb"
        p.write_bytes(b"NOT_A_VALID_DUCKDB_DATABASE_HEADER_DATA")
        return SourceSpec(adapter="duckdb_attach", uri="corrupt.duckdb#d_tbl", format="duckdb")

    @pytest.fixture
    def secure_spec(self, workspace: Path) -> SourceSpec:
        p = workspace / "secure.duckdb"
        dconn = duckdb.connect(str(p))
        dconn.execute("CREATE TABLE d_tbl (id INT, val TEXT)")
        dconn.execute("INSERT INTO d_tbl VALUES (10, 'secured')")
        dconn.close()
        return SourceSpec(
            adapter="duckdb_attach",
            uri="secure.duckdb#d_tbl",
            credentials_ref="secret:workspace_token",
            format="duckdb",
        )


def test_sqlite_uri_variations(tmp_path: Path) -> None:
    """Verify SQLite attachment across various URI schemes and options."""
    ws = tmp_path / "ws"
    ws.mkdir()
    db_file = ws / "test.db"
    sconn = sqlite3.connect(db_file)
    sconn.execute("CREATE TABLE items (id INT, title TEXT)")
    sconn.execute("INSERT INTO items VALUES (1, 'Widget'), (2, 'Gadget')")
    sconn.commit()
    sconn.close()

    adapter = DuckdbAttachSourceAdapter(workspace_root=ws)
    conn = duckdb.connect(":memory:")

    specs = [
        SourceSpec(adapter="sqlite", uri="sqlite:///test.db#items"),
        SourceSpec(adapter="sqlite", uri="sqlite://test.db#items"),
        SourceSpec(adapter="sqlite", uri="sqlite:test.db#items"),
        SourceSpec(adapter="duckdb_attach", uri="test.db", options={"table": "items"}),
    ]

    for idx, spec in enumerate(specs):
        rel = f"sqlite_var_{idx}"
        adapter.register(spec, conn, rel)
        rows = conn.execute(f'SELECT * FROM "{rel}" ORDER BY id').fetchall()
        assert rows == [(1, "Widget"), (2, "Gadget")]


def test_duckdb_uri_variations(tmp_path: Path) -> None:
    """Verify native DuckDB attachment across URI schemes and options."""
    ws = tmp_path / "ws"
    ws.mkdir()
    db_file = ws / "analytics.duckdb"
    dconn = duckdb.connect(str(db_file))
    dconn.execute("CREATE TABLE metrics (name TEXT, val DOUBLE)")
    dconn.execute("INSERT INTO metrics VALUES ('cpu', 0.85), ('mem', 0.62)")
    dconn.close()

    adapter = DuckdbAttachSourceAdapter(workspace_root=ws)
    conn = duckdb.connect(":memory:")

    specs = [
        SourceSpec(adapter="duckdb", uri="duckdb:///analytics.duckdb#metrics"),
        SourceSpec(adapter="duckdb", uri="duckdb:analytics.duckdb#metrics"),
        SourceSpec(adapter="duckdb_attach", uri="analytics.duckdb", options={"table": "metrics"}),
    ]

    for idx, spec in enumerate(specs):
        rel = f"duck_var_{idx}"
        adapter.register(spec, conn, rel)
        rows = conn.execute(f'SELECT * FROM "{rel}" ORDER BY name').fetchall()
        assert rows == [("cpu", 0.85), ("mem", 0.62)]


def test_path_containment_security(tmp_path: Path) -> None:
    """Verify path traversal, null bytes, and jail breaks are rejected."""
    ws = tmp_path / "ws"
    ws.mkdir()
    adapter = DuckdbAttachSourceAdapter(workspace_root=ws)

    # 1. Traversal token '..'
    with pytest.raises(PathContainmentError):
        adapter.read_bytes(SourceSpec(adapter="sqlite", uri="sqlite://../outside.db#t"))

    # 2. Null byte injection
    with pytest.raises(PathContainmentError):
        adapter.read_bytes(SourceSpec(adapter="sqlite", uri="test\0.db#t"))

    # 3. Absolute path escaping workspace
    outside = tmp_path / "outside.db"
    outside.write_bytes(b"data")
    with pytest.raises(PathContainmentError):
        adapter.read_bytes(SourceSpec(adapter="sqlite", uri=str(outside.resolve())))


def test_missing_and_empty_errors(tmp_path: Path) -> None:
    """Verify typed exceptions for missing files, missing tables, and empty tables."""
    ws = tmp_path / "ws"
    ws.mkdir()
    adapter = DuckdbAttachSourceAdapter(workspace_root=ws)
    conn = duckdb.connect(":memory:")

    # 1. Missing database file
    with pytest.raises(SourceNotFoundError):
        adapter.register(SourceSpec(adapter="sqlite", uri="nonexistent.db#t"), conn, "r1")

    # 2. Database with missing table
    db_file = ws / "valid.db"
    sconn = sqlite3.connect(db_file)
    sconn.execute("CREATE TABLE existing_tbl (x INT)")
    sconn.execute("INSERT INTO existing_tbl VALUES (1)")
    sconn.commit()
    sconn.close()

    with pytest.raises(SourceTableEmptyError):
        adapter.register(SourceSpec(adapter="sqlite", uri="valid.db#missing_tbl"), conn, "r2")

    # 3. Database with empty table (0 rows)
    sconn = sqlite3.connect(db_file)
    sconn.execute("CREATE TABLE empty_tbl (x INT)")
    sconn.commit()
    sconn.close()

    with pytest.raises(SourceTableEmptyError):
        adapter.register(SourceSpec(adapter="sqlite", uri="valid.db#empty_tbl"), conn, "r3")

    # 4. Multiple tables and no table specified
    with pytest.raises(SourceLoadError):
        adapter.register(SourceSpec(adapter="sqlite", uri="valid.db"), conn, "r4")


def test_streaming_metadata_and_fingerprint(tmp_path: Path) -> None:
    """Verify byte streaming, metadata calculation, and deterministic fingerprinting."""
    ws = tmp_path / "ws"
    ws.mkdir()
    db_file = ws / "large.sqlite"
    sconn = sqlite3.connect(db_file)
    sconn.execute("CREATE TABLE data (id INT, txt TEXT)")
    sconn.executemany("INSERT INTO data VALUES (?, ?)", [(i, f"val_{i}") for i in range(1000)])
    sconn.commit()
    sconn.close()

    adapter = DuckdbAttachSourceAdapter(workspace_root=ws)
    spec = SourceSpec(adapter="duckdb_attach", uri="large.sqlite#data", format="sqlite")

    raw = adapter.read_bytes(spec)
    assert raw == db_file.read_bytes()

    chunks = list(adapter.stream_chunks(spec, chunk_size=4096))
    assert len(chunks) > 1
    assert b"".join(c.data for c in chunks) == raw
    assert chunks[0].chunk_index == 0
    assert not chunks[0].is_last
    assert chunks[-1].is_last

    meta = adapter.get_metadata(spec)
    assert meta.byte_size == len(raw)
    assert meta.modified_at is not None

    expected_fp = hashlib.sha256(raw).hexdigest()
    assert adapter.fingerprint(spec) == expected_fp


def test_registry_integration() -> None:
    """Verify registry resolves duckdb_attach, sqlite, and duckdb adapter schemes."""
    reg = get_source_registry()
    for name in ("duckdb_attach", "sqlite", "duckdb"):
        ad = reg.get_tabular_adapter(name)
        assert isinstance(ad, DuckdbAttachSourceAdapter)


def test_duckdb_attach_credential_redaction_in_not_found_error() -> None:
    """Verify credentials in URI are masked in SourceNotFoundError."""
    adapter = DuckdbAttachSourceAdapter()
    spec = SourceSpec(
        adapter="duckdb_attach", uri="sqlite://admin:SuperSecretPassword123@nonexistent.db"
    )
    with pytest.raises(SourceNotFoundError) as exc_info:
        adapter.read_bytes(spec)
    err_str = str(exc_info.value)
    assert "SuperSecretPassword123" not in err_str
    assert "admin:***@" in err_str


def test_duckdb_attach_client_workspace_root_cannot_override_server_jail(tmp_path: Path) -> None:
    """Verify client options cannot supply containment overrides and server jail holds."""
    server_ws = tmp_path / "server_ws"
    server_ws.mkdir()
    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    outside_db = outside_dir / "secret.db"
    outside_db.write_bytes(b"data")
    adapter = DuckdbAttachSourceAdapter(workspace_root=server_ws)

    # Client-supplied containment options are rejected at spec validation time
    with pytest.raises(ValidationError, match="containment option"):
        SourceSpec(
            adapter="duckdb_attach",
            uri=str(outside_db.resolve()),
            options={"workspace_root": str(outside_dir.resolve())},
        )

    # Legitimate spec targeting outside path is blocked by containment
    evil_spec = SourceSpec(
        adapter="duckdb_attach",
        uri=str(outside_db.resolve()),
    )
    with pytest.raises(PathContainmentError):
        adapter.read_bytes(evil_spec)
