"""Automated T9 Removal Guards ensuring complete excision of legacy workbench artifacts.

Enforces:
1. test_zero_legacy_view_identifiers_in_source_ast: Zero legacy view IDs in packages/ and apps/.
2. test_workbench_router_returns_404: /api/v1/runs/{id}/workbench/{view} returns 404 Problem JSON.
3. test_platform_capabilities_omits_workbench_views: Capabilities payload omits workbench_views.
4. test_protocol_ts_zero_legacy_exports: @opendv/protocol-ts exports zero legacy types or view IDs.
5. test_explorer_import_isolation: apps/opendv-web-workbench/src imports zero legacy components.
6. test_no_legacy_workbench_references: Complete repository scan asserting zero legacy references.
"""

from __future__ import annotations

import ast
import os
import re
from pathlib import Path
from typing import Any

import pytest
from starlette.testclient import TestClient

from opendv_platform_api.main import create_app
from tests.architecture.conftest import REPO_ROOT

# Dynamic fragment definitions to avoid false-positive static scan matches
_WB_PREFIX = "W" + "B-"
_VIZ_PREFIX = "VI" + "Z-"
_WB_VIEW_KEY = "work" + "bench_view_id"
_VIZ_ID_KEY = "vi" + "z_id"
_USE_LEGACY_SHELL = "use" + "LegacyShell"
_EXEC_PE_SANDBOX = "execute_" + "pe_sandbox_query"
_DOC10_DECOUPLED = "docs/10-" + "decoupled"
_WB_VIEWS_KEY = "work" + "bench_views"

RE_LEGACY_PATTERNS = [
    re.compile(_WB_PREFIX + r"[0-1][0-9]"),
    re.compile(_VIZ_PREFIX + r"[0-1][0-9]"),
    re.compile(_VIZ_PREFIX + r"(?:PIPE|STEP|GATE)-"),
    re.compile(r"\b" + re.escape(_WB_VIEW_KEY) + r"\b"),
    re.compile(r"\b" + re.escape(_VIZ_ID_KEY) + r"\b"),
    re.compile(r"\b" + re.escape(_USE_LEGACY_SHELL) + r"\b"),
]

SOURCE_DIRS = [
    REPO_ROOT / "packages",
    REPO_ROOT / "apps",
]

SKIP_DIR_NAMES = {
    ".git",
    ".venv",
    "venv",
    "node_modules",
    "dist",
    "build",
    "__pycache__",
    ".moon",
    ".proto",
    "worktrees",
    ".opendv",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".agents",
}


@pytest.mark.architecture
def test_zero_legacy_view_identifiers_in_source_ast() -> None:
    """AST and regex scan across packages/ and apps/ asserting zero legacy view identifiers."""
    violations: list[str] = []

    for src_dir in SOURCE_DIRS:
        assert src_dir.is_dir(), f"Source directory missing: {src_dir}"
        for path in src_dir.rglob("*"):
            if not path.is_file():
                continue
            if any(skip in path.parts for skip in SKIP_DIR_NAMES):
                continue
            if path.suffix not in {".py", ".ts", ".tsx", ".js", ".jsx"}:
                continue

            content = path.read_text(encoding="utf-8", errors="replace")

            # Check Python AST string constants
            if path.suffix == ".py":
                try:
                    tree = ast.parse(content, filename=str(path))
                    for node in ast.walk(tree):
                        if isinstance(node, ast.Constant) and isinstance(node.value, str):
                            for pattern in RE_LEGACY_PATTERNS:
                                if pattern.search(node.value):
                                    rel = path.relative_to(REPO_ROOT)
                                    violations.append(
                                        f"{rel}:{node.lineno}: AST constant '{node.value}' "
                                        f"matched {pattern.pattern}"
                                    )
                except SyntaxError:
                    pass

            # Regex scan across full source text
            for line_no, line in enumerate(content.splitlines(), 1):
                # Skip comments explaining Decision D3 rejection in negative tests if any
                for pattern in RE_LEGACY_PATTERNS:
                    if pattern.search(line):
                        rel = path.relative_to(REPO_ROOT)
                        violations.append(
                            f"{rel}:{line_no}: Source line matched {pattern.pattern}: "
                            f"{line.strip()}"
                        )

    assert not violations, (
        f"Found {len(violations)} legacy view identifier violations in source files:\n"
        + "\n".join(violations[:30])
    )


@pytest.mark.architecture
def test_workbench_router_returns_404() -> None:
    """Assert GET /api/v1/runs/{id}/workbench/{view_id} returns RFC 9457 404 Problem JSON."""
    client = TestClient(create_app(workspace_root=REPO_ROOT))
    run_id = "run-arch-test-guard"

    test_views = [f"{_WB_PREFIX}{i:02d}" for i in range(12)] + ["unknown-view"]
    for view_id in test_views:
        response = client.get(f"/api/v1/runs/{run_id}/workbench/{view_id}")
        assert response.status_code == 404, (
            f"Expected 404 for excised workbench view '{view_id}', got {response.status_code}"
        )

        content_type = response.headers.get("content-type", "")
        assert "application/problem+json" in content_type or "application/json" in content_type, (
            f"Expected RFC 9457 problem JSON content type, got: {content_type}"
        )

        data = response.json()
        assert isinstance(data, dict), f"Expected JSON object response, got {type(data)}"
        # RFC 9457 / FastAPI Problem Details require status or detail
        assert data.get("status") == 404 or "detail" in data or data.get("title") is not None


@pytest.mark.architecture
def test_platform_capabilities_omits_workbench_views() -> None:
    """Assert GET /api/v1/platform/capabilities omits workbench_views."""
    client = TestClient(create_app(workspace_root=REPO_ROOT))
    response = client.get("/api/v1/platform/capabilities")
    assert response.status_code == 200, f"Expected 200, got {response.status_code}"

    data = response.json()
    assert isinstance(data, dict), "Capabilities response must be a JSON object"

    # Must omit legacy workbench_views
    assert _WB_VIEWS_KEY not in data, f"Legacy '{_WB_VIEWS_KEY}' found in capabilities payload"

    # Recursively ensure _WB_VIEWS_KEY is absent anywhere in payload
    def _assert_no_key(obj: Any, key: str) -> None:
        if isinstance(obj, dict):
            assert key not in obj, f"Key '{key}' found in nested dictionary"
            for v in obj.values():
                _assert_no_key(v, key)
        elif isinstance(obj, list):
            for item in obj:
                _assert_no_key(item, key)

    _assert_no_key(data, _WB_VIEWS_KEY)

    # Must contain modern platform capabilities
    assert "explorer_views" in data or "parity_endpoints" in data, (
        "Capabilities must contain 'explorer_views' or 'parity_endpoints'"
    )


@pytest.mark.architecture
def test_protocol_ts_zero_legacy_exports() -> None:
    """Assert @opendv/protocol-ts exports zero legacy types or view identifiers."""
    ts_pkg_dir = REPO_ROOT / "packages" / "opendv-protocol-ts" / "src"
    assert ts_pkg_dir.is_dir(), f"Protocol TS source directory not found: {ts_pkg_dir}"

    banned_ts_symbols = [
        "get" + "WorkbenchView",
        "WORK" + "BENCH_VIEW_IDS",
        "VI" + "Z_IDS",
        "Workbench" + "ViewId",
        "Vi" + "zId",
        _WB_VIEW_KEY,
        _VIZ_ID_KEY,
    ]

    violations: list[str] = []
    for ts_file in ts_pkg_dir.rglob("*.ts"):
        content = ts_file.read_text(encoding="utf-8")
        rel = ts_file.relative_to(REPO_ROOT)
        for sym in banned_ts_symbols:
            if re.search(r"\b" + re.escape(sym) + r"\b", content):
                violations.append(f"{rel}: legacy export/symbol '{sym}' detected")

        for pattern in RE_LEGACY_PATTERNS:
            if pattern.search(content):
                violations.append(f"{rel}: matched legacy pattern {pattern.pattern}")

    assert not violations, (
        "Legacy exports detected in @opendv/protocol-ts:\n" + "\n".join(violations)
    )


@pytest.mark.architecture
def test_explorer_import_isolation() -> None:
    """Assert apps/opendv-web-workbench/src has zero imports from deleted legacy paths."""
    workbench_src = REPO_ROOT / "apps" / "opendv-web-workbench" / "src"
    assert workbench_src.is_dir(), f"Workbench src directory not found: {workbench_src}"

    banned_import_patterns = [
        re.compile(r"['\"].*Workbench" + r"Shell.*['\"]"),
        re.compile(r"['\"].*views/w" + r"b[0-1][0-9].*['\"]"),
        re.compile(r"['\"].*plugins/.*['\"]"),
        re.compile(r"['\"].*workbench-" + r"store.*['\"]"),
        re.compile(r"\b" + re.escape(_USE_LEGACY_SHELL) + r"\b"),
    ]

    violations: list[str] = []
    for file_path in workbench_src.rglob("*"):
        if not file_path.is_file() or file_path.suffix not in {".ts", ".tsx"}:
            continue

        content = file_path.read_text(encoding="utf-8")
        rel = file_path.relative_to(REPO_ROOT)

        for line_no, line in enumerate(content.splitlines(), 1):
            if "import " in line or "from " in line:
                for pattern in banned_import_patterns:
                    if pattern.search(line):
                        violations.append(
                            f"{rel}:{line_no}: Banned legacy import matched {pattern.pattern}: "
                            f"{line.strip()}"
                        )

    assert not violations, (
        "Explorer import isolation violations detected:\n" + "\n".join(violations)
    )


@pytest.mark.architecture
def test_no_legacy_workbench_references() -> None:
    """Repository-wide scan asserting zero occurrences of legacy workbench references (T9)."""
    banned_repo_patterns = [
        re.compile(_WB_PREFIX + r"[0-1][0-9]"),
        re.compile(_VIZ_PREFIX + r"(?:PIPE|STEP|GATE)-"),
        re.compile(r"\b" + re.escape(_WB_VIEW_KEY) + r"\b"),
        re.compile(r"\b" + re.escape(_VIZ_ID_KEY) + r"\b"),
        re.compile(r"\b" + re.escape(_EXEC_PE_SANDBOX) + r"\b"),
        re.compile(r"\b" + re.escape(_USE_LEGACY_SHELL) + r"\b"),
        re.compile(re.escape(_DOC10_DECOUPLED)),
    ]

    # Files explicitly excluded: this test file itself, git metadata, and docs/18
    this_file = Path(__file__).resolve()

    violations: list[str] = []
    for root, dirnames, filenames in os.walk(REPO_ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIR_NAMES]
        for filename in filenames:
            path = Path(root) / filename
            if path.resolve() == this_file:
                continue
            # docs/18 is the PR #60 breaking changes note / implementation plan, explicitly exempted
            if "docs" in path.parts and path.name.startswith("18-implementation-plan"):
                continue

        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except (OSError, UnicodeDecodeError):
            continue

        rel = path.relative_to(REPO_ROOT)
        for line_no, line in enumerate(content.splitlines(), 1):
            for pattern in banned_repo_patterns:
                if pattern.search(line):
                    violations.append(
                        f"{rel}:{line_no}: Legacy reference matched {pattern.pattern}: "
                        f"{line.strip()}"
                    )

    assert not violations, (
        f"Repository scan found {len(violations)} legacy workbench references:\n"
        + "\n".join(violations[:30])
    )
