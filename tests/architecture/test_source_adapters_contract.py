"""Architectural guard for pluggable source adapters contract (Phase P4 / Task P4.5).

Verifies that production code outside `packages/opendv-sources/` contains:
1. No `read_csv_auto`, `read_parquet`, or `read_json_auto` reader SQL function strings.
2. No `.suffix` or extension branching for data source or document loading.
3. GraphBProfiler has no format-specific profile_csv or profile_parquet methods.
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

DATA_EXTENSIONS = {
    ".csv",
    ".parquet",
    ".parquet.snappy",
    ".tsv",
    ".pdf",
}


def _get_production_py_files() -> list[Path]:
    """Collect production Python files in packages/ and apps/."""
    py_files: list[Path] = []
    for root_dir in [REPO_ROOT / "packages", REPO_ROOT / "apps"]:
        for py_path in root_dir.rglob("*.py"):
            rel = py_path.relative_to(REPO_ROOT).as_posix()
            if rel.startswith("packages/opendv-sources/"):
                continue
            # Exclude tests and fixtures
            parts = py_path.parts
            if "tests" in parts or "test" in parts or "testing" in parts or "fixtures" in parts:
                continue
            py_files.append(py_path)
    return sorted(py_files)


@pytest.mark.architecture
def test_zero_hardcoded_reader_sql_outside_sources() -> None:
    """Assert no read_csv_auto, read_parquet, or read_json_auto strings outside opendv-sources."""
    prod_files = _get_production_py_files()
    assert len(prod_files) >= 20, f"Expected at least 20 production files, found {len(prod_files)}"

    violations: list[str] = []
    for py_file in prod_files:
        content = py_file.read_text(encoding="utf-8")
        for banned in BANNED_READER_STRINGS:
            if banned in content:
                rel = py_file.relative_to(REPO_ROOT).as_posix()
                violations.append(f"{rel}: contains banned reader function string '{banned}'")

    assert not violations, (
        "Banned DuckDB reader functions found outside opendv-sources:\n" + "\n".join(violations)
    )


@pytest.mark.architecture
def test_zero_source_extension_branching_outside_sources() -> None:
    """Assert no source/document extension branching exists outside opendv-sources."""
    prod_files = _get_production_py_files()
    violations: list[str] = []

    for py_file in prod_files:
        rel = py_file.relative_to(REPO_ROOT).as_posix()
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))

        for node in ast.walk(tree):
            # Check for binary comparison or membership test involving data extensions
            if isinstance(node, ast.Compare):
                # Check if comparison targets data extensions (e.g. .csv, .parquet, .pdf)
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
        "Detected data source / document extension branching outside opendv-sources:\n"
        + "\n".join(violations)
    )


@pytest.mark.architecture
def test_graph_b_profiler_has_no_format_specific_methods() -> None:
    """Assert GraphBProfiler defines profile_relation without format-specific methods."""
    from opendv_duckdb_sandbox.graph_b_profiler import GraphBProfiler, profile_relation

    assert callable(profile_relation), "profile_relation function must be callable"
    assert hasattr(GraphBProfiler, "profile_relation"), "GraphBProfiler must have profile_relation"
    assert not hasattr(GraphBProfiler, "profile_csv"), (
        "GraphBProfiler.profile_csv must be deleted per SRC-02"
    )
    assert not hasattr(GraphBProfiler, "profile_parquet"), (
        "GraphBProfiler.profile_parquet must be deleted per SRC-02"
    )
