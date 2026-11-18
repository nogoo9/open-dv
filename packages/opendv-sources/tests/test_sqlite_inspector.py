"""Unit tests for SqliteCatalogInspector and SQLite multi-table introspection (WS2A.3)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from opendv_sources.adapters.duckdb_attach import DuckdbAttachSourceAdapter
from opendv_sources.adapters.sqlite_inspector import SqliteCatalogInspector
from opendv_sources.exceptions import SourceNotFoundError, SourceTableEmptyError
from opendv_sources.models.specs import (
    SamplingPolicy,
    SourceSpec,
    TableRef,
    TableSelectionSpec,
)


@pytest.fixture
def multi_table_sqlite_db(tmp_path: Path) -> Path:
    """Create a temporary SQLite database with PKs, composite PKs, FKs, and internal tables."""
    db_path = tmp_path / "test_catalog.sqlite"
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    # Table 1: Simple PK
    cur.execute(
        """
        CREATE TABLE departments (
            dept_id INTEGER PRIMARY KEY,
            dept_name TEXT NOT NULL
        )
        """
    )
    cur.executemany(
        "INSERT INTO departments VALUES (?, ?)",
        [(10, "Engineering"), (20, "Finance"), (30, "Compliance")],
    )

    # Table 2: Foreign key pointing to departments
    cur.execute(
        """
        CREATE TABLE employees (
            emp_id INTEGER PRIMARY KEY,
            first_name TEXT NOT NULL,
            last_name TEXT NOT NULL,
            dept_id INTEGER,
            salary REAL,
            FOREIGN KEY (dept_id) REFERENCES departments(dept_id)
        )
        """
    )
    cur.executemany(
        "INSERT INTO employees VALUES (?, ?, ?, ?, ?)",
        [
            (101, "Alice", "Smith", 10, 120000.0),
            (102, "Bob", "Jones", 20, 95000.0),
            (103, "Charlie", "Brown", None, 80000.0),
        ],
    )

    # Table 3: Composite Primary Key + Multiple Foreign Keys
    cur.execute(
        """
        CREATE TABLE project_assignments (
            project_id TEXT NOT NULL,
            emp_id INTEGER NOT NULL,
            role TEXT,
            assigned_hours INTEGER,
            PRIMARY KEY (project_id, emp_id),
            FOREIGN KEY (emp_id) REFERENCES employees(emp_id)
        )
        """
    )
    cur.executemany(
        "INSERT INTO project_assignments VALUES (?, ?, ?, ?)",
        [
            ("PROJ-A", 101, "Lead", 40),
            ("PROJ-A", 102, "Analyst", 20),
            ("PROJ-B", 101, "Reviewer", 10),
        ],
    )

    # Internal SQLite sequence table (auto created with AUTOINCREMENT)
    cur.execute(
        """
        CREATE TABLE audit_log (
            log_id INTEGER PRIMARY KEY AUTOINCREMENT,
            action TEXT
        )
        """
    )
    cur.execute("INSERT INTO audit_log (action) VALUES ('BOOTSTRAP')")

    # Native SQLite ANALYZE creates sqlite_stat1 table
    cur.execute("CREATE INDEX idx_dept_name ON departments(dept_name)")
    cur.execute("ANALYZE")

    conn.commit()
    conn.close()
    return db_path


def test_sqlite_catalog_inspector_list_namespaces(multi_table_sqlite_db: Path) -> None:
    """Verify list_namespaces reports the main SQLite schema."""
    inspector = SqliteCatalogInspector()
    spec = SourceSpec(adapter="sqlite", uri=str(multi_table_sqlite_db))
    namespaces = inspector.list_namespaces(spec)

    assert len(namespaces) == 1
    assert namespaces[0].schema_name == "main"
    assert namespaces[0].catalog is None


def test_sqlite_catalog_inspector_list_tables_skips_internal(
    multi_table_sqlite_db: Path,
) -> None:
    """Verify list_tables enumerates user tables and skips sqlite_sequence and sqlite_stat1."""
    inspector = SqliteCatalogInspector()
    spec = SourceSpec(adapter="sqlite", uri=str(multi_table_sqlite_db))
    tables = inspector.list_tables(spec)

    table_names = [t.table_name for t in tables]
    assert "departments" in table_names
    assert "employees" in table_names
    assert "project_assignments" in table_names
    assert "audit_log" in table_names

    # Internal tables MUST be excluded
    assert "sqlite_sequence" not in table_names
    assert "sqlite_stat1" not in table_names

    # Row counts verified
    dept_summary = next(t for t in tables if t.table_name == "departments")
    assert dept_summary.row_count_estimate == 3
    assert dept_summary.column_count == 2
    assert dept_summary.table_type == "BASE_TABLE"


def test_sqlite_catalog_inspector_inspect_table_single_pk_and_fk(
    multi_table_sqlite_db: Path,
) -> None:
    """Verify inspection of single-column PK and FK references."""
    inspector = SqliteCatalogInspector()
    spec = SourceSpec(adapter="sqlite", uri=str(multi_table_sqlite_db))

    schema = inspector.inspect_table(spec, TableRef(table_name="employees"))
    assert schema.table_name == "employees"
    assert schema.primary_key_columns == ["emp_id"]
    assert schema.row_count_estimate == 3

    col_map = {c.name: c for c in schema.columns}
    assert col_map["emp_id"].is_primary_key is True
    assert col_map["emp_id"].primary_key_ordinal == 1
    assert col_map["emp_id"].nullable is False

    assert col_map["dept_id"].is_primary_key is False
    assert col_map["dept_id"].foreign_key_ref is not None
    assert col_map["dept_id"].foreign_key_ref.target_table == "departments"
    assert col_map["dept_id"].foreign_key_ref.target_column == "dept_id"
    assert col_map["dept_id"].nullable is True


def test_sqlite_catalog_inspector_inspect_table_composite_pk(
    multi_table_sqlite_db: Path,
) -> None:
    """Verify composite primary key column ordering and ordinals."""
    inspector = SqliteCatalogInspector()
    spec = SourceSpec(adapter="sqlite", uri=str(multi_table_sqlite_db))

    schema = inspector.inspect_table(spec, TableRef(table_name="project_assignments"))
    assert schema.table_name == "project_assignments"
    assert schema.primary_key_columns == ["project_id", "emp_id"]

    col_map = {c.name: c for c in schema.columns}
    assert col_map["project_id"].is_primary_key is True
    assert col_map["project_id"].primary_key_ordinal == 1
    assert col_map["emp_id"].is_primary_key is True
    assert col_map["emp_id"].primary_key_ordinal == 2
    assert col_map["role"].is_primary_key is False


def test_sqlite_catalog_inspector_expand_feed_modes(
    multi_table_sqlite_db: Path,
) -> None:
    """Verify feed expansion with ALL_USER_TABLES, EXPLICIT, PATTERN, and table arg."""
    inspector = SqliteCatalogInspector()

    # 1. Mode: ALL_USER_TABLES (default)
    spec_all = SourceSpec(
        adapter="sqlite",
        uri=str(multi_table_sqlite_db),
        options={"source_system": "corp_hr"},
    )
    expanded_all = inspector.expand_feed(spec_all)
    names_all = [r.table_name for r in expanded_all]
    assert set(names_all) == {"departments", "employees", "project_assignments", "audit_log"}
    assert all(r.qualified_name.startswith("corp_hr.") for r in expanded_all)
    assert all(r.execution_mode == "DIRECT_DUCKDB_ATTACH" for r in expanded_all)

    # 2. Mode: EXPLICIT
    spec_explicit = SourceSpec(
        adapter="sqlite",
        uri=str(multi_table_sqlite_db),
        table_selection=TableSelectionSpec(mode="EXPLICIT", tables=["departments", "employees"]),
    )
    expanded_exp = inspector.expand_feed(spec_explicit)
    assert [r.table_name for r in expanded_exp] == ["departments", "employees"]

    # 3. Mode: PATTERN
    spec_pattern = SourceSpec(
        adapter="sqlite",
        uri=str(multi_table_sqlite_db),
        table_selection=TableSelectionSpec(mode="PATTERN", include_pattern=r"^emp|^dep"),
    )
    expanded_pat = inspector.expand_feed(spec_pattern)
    assert set(r.table_name for r in expanded_pat) == {"departments", "employees"}

    # 4. Explicit table argument overrides selection
    expanded_single = inspector.expand_feed(spec_all, TableRef(table_name="project_assignments"))
    assert len(expanded_single) == 1
    assert expanded_single[0].table_name == "project_assignments"


def test_sqlite_catalog_inspector_strict_read_only(multi_table_sqlite_db: Path) -> None:
    """Verify inspector connects in strict read-only mode and write attempts fail."""
    inspector = SqliteCatalogInspector()
    spec = SourceSpec(adapter="sqlite", uri=str(multi_table_sqlite_db))
    db_path = inspector._resolve_db_path(spec)

    conn = inspector._connect_ro(db_path)
    try:
        cur = conn.cursor()
        with pytest.raises(sqlite3.OperationalError, match="readonly database"):
            cur.execute("INSERT INTO departments VALUES (99, 'Hacked')")
        with pytest.raises(sqlite3.OperationalError, match="readonly database"):
            cur.execute("CREATE TABLE hacked (x INT)")
    finally:
        conn.close()


def test_sqlite_catalog_inspector_profile_pushdown(multi_table_sqlite_db: Path) -> None:
    """Verify profile_pushdown returns row counts and column stats."""
    inspector = SqliteCatalogInspector()
    spec = SourceSpec(adapter="sqlite", uri=str(multi_table_sqlite_db))
    policy = SamplingPolicy(max_sandbox_rows=1000)

    profile = inspector.profile_pushdown(spec, TableRef(table_name="employees"), policy)
    assert profile.table_name == "employees"
    assert profile.total_row_count == 3
    assert "dept_id" in profile.column_stats
    assert profile.column_stats["dept_id"]["null_count"] == 1
    assert profile.column_stats["emp_id"]["null_count"] == 0
    assert profile.column_stats["emp_id"]["approx_distinct"] == 3


def test_sqlite_catalog_inspector_capabilities() -> None:
    """Verify capabilities declare client_uploadable=True and multi_table=True."""
    inspector = SqliteCatalogInspector()
    caps = inspector.capabilities()
    assert caps.client_uploadable is True
    assert caps.supports_multi_table is True
    assert caps.supports_foreign_keys is True
    assert caps.execution_mode == "DIRECT_DUCKDB_ATTACH"


def test_sqlite_catalog_inspector_missing_or_empty_database(tmp_path: Path) -> None:
    """Verify SourceNotFoundError on missing file and SourceTableEmptyError on 0-byte file."""
    inspector = SqliteCatalogInspector()
    missing_spec = SourceSpec(adapter="sqlite", uri=str(tmp_path / "missing.sqlite"))
    with pytest.raises(SourceNotFoundError):
        inspector.list_tables(missing_spec)

    empty_file = tmp_path / "empty.sqlite"
    empty_file.write_bytes(b"")
    empty_spec = SourceSpec(adapter="sqlite", uri=str(empty_file))
    with pytest.raises(SourceTableEmptyError):
        inspector.list_tables(empty_spec)


def test_duckdb_attach_adapter_delegates_to_sqlite_inspector(
    multi_table_sqlite_db: Path,
) -> None:
    """Verify DuckdbAttachSourceAdapter exposes catalog methods delegating to SQLite inspector."""
    adapter = DuckdbAttachSourceAdapter()
    spec = SourceSpec(adapter="duckdb_attach", uri=str(multi_table_sqlite_db))

    namespaces = adapter.list_namespaces(spec)
    assert len(namespaces) == 1
    assert namespaces[0].schema_name == "main"

    tables = adapter.list_tables(spec)
    assert any(t.table_name == "departments" for t in tables)

    schema = adapter.inspect_table(spec, TableRef(table_name="departments"))
    assert schema.primary_key_columns == ["dept_id"]

    expanded = adapter.expand_feed(spec)
    assert len(expanded) >= 3

    profile = adapter.profile_pushdown(spec, TableRef(table_name="departments"), SamplingPolicy())
    assert profile.total_row_count == 3

    caps = adapter.capabilities(spec)
    assert caps.client_uploadable is True
    assert caps.supports_multi_table is True
    assert caps.supports_foreign_keys is True
