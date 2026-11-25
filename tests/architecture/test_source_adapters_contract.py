"""Architectural guard for pluggable V2 source adapters contract (`src/opendv/sources/`).

Verifies that production code outside `src/opendv/sources/` contains:
1. No `read_csv_auto`, `read_parquet`, or `read_json_auto` reader SQL function calls outside
   AST security denylists (`_dml_target_resolver.py`, `_readonly_sql_guard.py`).
2. No `.suffix` or extension branching for data source or document loading (excluding emitters).
3. `opendv.sources.profiler` has zero format-specific `profile_csv` or `profile_parquet` helpers.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.architecture.conftest import REPO_ROOT

BANNED_READER_STRINGS = {
    "read_csv_auto",
    "read_parquet",
    "read_json_auto",
}

READER_DENYLIST_ALLOWLIST = {
    "src/opendv/storage/_dml_target_resolver.py",
    "src/opendv/storage/_readonly_sql_guard.py",
}

DATA_EXTENSIONS = {
    ".csv",
    ".parquet",
    ".parquet.snappy",
    ".tsv",
    ".pdf",
}


def _get_production_py_files() -> list[Path]:
    """Collect production Python files in `src/opendv/` outside `src/opendv/sources/`."""
    py_files: list[Path] = []
    for py_path in (REPO_ROOT / "src" / "opendv").rglob("*.py"):
        rel = py_path.relative_to(REPO_ROOT).as_posix()
        if rel.startswith(("src/opendv/sources/", "src/opendv/api/static/")):
            continue
        py_files.append(py_path)
    return sorted(py_files)


@pytest.mark.architecture
def test_zero_hardcoded_reader_sql_outside_sources() -> None:
    """Assert no read_csv_auto, read_parquet, or read_json_auto strings outside opendv.sources."""
    prod_files = _get_production_py_files()
    assert len(prod_files) >= 20, f"Expected at least 20 production files, found {len(prod_files)}"

    violations: list[str] = []
    for py_file in prod_files:
        rel = py_file.relative_to(REPO_ROOT).as_posix()
        if rel in READER_DENYLIST_ALLOWLIST:
            continue
        content = py_file.read_text(encoding="utf-8")
        for banned in BANNED_READER_STRINGS:
            if banned in content:
                violations.append(f"{rel}: contains banned reader function string '{banned}'")

    assert not violations, (
        "Banned DuckDB reader functions found outside opendv.sources:\n" + "\n".join(violations)
    )


@pytest.mark.architecture
def test_zero_source_extension_branching_outside_sources() -> None:
    """Assert no source/document extension branching exists outside opendv.sources."""
    prod_files = _get_production_py_files()
    violations: list[str] = []

    for py_file in prod_files:
        rel = py_file.relative_to(REPO_ROOT).as_posix()
        if rel == "src/opendv/storage/emitters/parquet.py":
            continue
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))

        for node in ast.walk(tree):
            if isinstance(node, ast.Compare):
                for comparator in node.comparators:
                    found_ext: str | None = None
                    if isinstance(comparator, ast.Constant) and isinstance(comparator.value, str):
                        if comparator.value.lower() in DATA_EXTENSIONS:
                            found_ext = comparator.value
                    elif isinstance(comparator, (ast.Set, ast.List, ast.Tuple)):
                        for elt in comparator.elts:
                            if (
                                isinstance(elt, ast.Constant)
                                and isinstance(elt.value, str)
                                and elt.value.lower() in DATA_EXTENSIONS
                            ):
                                found_ext = elt.value
                                break
                    if found_ext:
                        violations.append(
                            f"{rel}:{node.lineno}: extension branching on '{found_ext}'"
                        )

    assert not violations, (
        "Detected data source / document extension branching outside opendv.sources:\n"
        + "\n".join(violations)
    )


@pytest.mark.architecture
def test_profiler_has_no_format_specific_methods() -> None:
    """Assert `opendv.sources.profiler` uses unified extraction without format-specific helpers."""
    from opendv.sources import profiler

    assert hasattr(profiler, "profile_source"), "opendv.sources.profiler must define profile_source"
    assert not hasattr(profiler, "profile_csv"), "profile_csv must not exist per SRC-02"
    assert not hasattr(profiler, "profile_parquet"), "profile_parquet must not exist per SRC-02"
