"""AST scan of module imports enforcing strict layered boundaries (T13)."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

from tests.architecture.conftest import REPO_ROOT

BANNED_IMPORTS: dict[str, set[str]] = {
    # apps/opendv-cli must never import web frameworks or platform API
    "apps/opendv-cli/src": {
        "fastapi",
        "starlette",
        "opendv_platform_api",
        "httpx",
    },
    # apps/opendv-cli/tests must never import platform API or web frameworks
    "apps/opendv-cli/tests": {
        "fastapi",
        "starlette",
        "opendv_platform_api",
        "httpx",
    },
    # apps/opendv-platform-api must never import direct engine packages or CLI
    "apps/opendv-platform-api/src": {
        "opendv_axi_verifier",
        "opendv_duckdb_sandbox",
        "opendv_agent_harness",
        "opendv_cli",
    },
    # packages/opendv-service must be headless (zero web framework or CLI imports)
    "packages/opendv-service/src": {
        "fastapi",
        "starlette",
        "opendv_platform_api",
        "opendv_cli",
    },
    # packages/opendv-contracts must be language-neutral and dependency-free
    "packages/opendv-contracts/src": {
        "opendv_service",
        "opendv_platform_api",
        "opendv_cli",
        "opendv_axi_verifier",
        "opendv_duckdb_sandbox",
        "opendv_agent_harness",
        "opendv_core",
    },
    # packages/opendv-cli-common must be clean of services, platforms, and engines
    "packages/opendv-cli-common/src": {
        "opendv_service",
        "opendv_platform_api",
        "opendv_cli",
        "opendv_axi_verifier",
        "opendv_duckdb_sandbox",
        "opendv_agent_harness",
        "opendv_core",
        "fastapi",
        "starlette",
        "httpx",
    },
    # packages/opendv-core must not import from higher layers
    "packages/opendv-core/src": {
        "opendv_service",
        "opendv_contracts",
        "opendv_platform_api",
        "opendv_cli",
        "opendv_axi_verifier",
        "opendv_duckdb_sandbox",
        "opendv_agent_harness",
    },
    # packages/opendv-agent-harness/personas must depend only on opendv-core and opendv-contracts
    "packages/opendv-agent-harness/src/opendv_agent_harness/personas": {
        "opendv_service",
        "opendv_sources",
        "opendv_platform_api",
        "opendv_cli",
        "fastapi",
        "starlette",
    },
}


def _resolve_relative_import(
    file_path: Path,
    package_root: Path,
    level: int,
    module: str | None,
) -> str | None:
    """Resolve a relative Python import against `package_root` into a dotted module path."""
    try:
        rel = file_path.relative_to(package_root)
    except ValueError:
        return module
    pkg_parts = list(rel.parent.parts)
    up = level - 1
    if up > len(pkg_parts):
        return module
    base_parts = pkg_parts[: len(pkg_parts) - up] if up > 0 else pkg_parts
    base = ".".join(base_parts)
    if module:
        return f"{base}.{module}" if base else module
    return base or None


def get_imports(
    file_path: Path,
    *,
    package_root: Path | None = None,
    include_from_targets: bool = False,
) -> list[tuple[int, str]]:
    """Extract all import module names with line numbers from a Python file via AST,
    including static imports, optional relative imports, and dynamic importlib / dunder calls.
    """
    tree = ast.parse(file_path.read_text(encoding="utf-8"), filename=str(file_path))
    imports: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append((node.lineno, alias.name))
        elif isinstance(node, ast.ImportFrom):
            resolved_mod = node.module
            if node.level > 0 and package_root is not None:
                resolved_mod = _resolve_relative_import(
                    file_path, package_root, node.level, node.module
                )
            if resolved_mod:
                if include_from_targets and node.names and not any(
                    a.name == "*" for a in node.names
                ):
                    for alias in node.names:
                        imports.append((node.lineno, f"{resolved_mod}.{alias.name}"))
                else:
                    imports.append((node.lineno, resolved_mod))
        elif isinstance(node, ast.Call) and (
            (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "import_module"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            )
            or (
                isinstance(node.func, ast.Name)
                and node.func.id in ("import_module", "__import__")
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            )
        ):
            imports.append((node.lineno, node.args[0].value))
    return imports


V2_LAYER_BANS: dict[str, tuple[bool, set[str], set[str]]] = {
    # (required_in_v2_1, banned_prefixes, allowed_exceptions)
    "src/opendv/models": (
        True,
        {
            "opendv.sources",
            "opendv.verifier",
            "opendv.storage",
            "opendv.agents",
            "opendv.service",
            "opendv.api",
            "opendv.cli",
            "duckdb",
            "sqlglot",
            "networkx",
            "fastapi",
            "starlette",
            "typer",
            "mcp",
            "rdflib",
            "pyarrow",
            "yaml",
            "httpx",
            "keyring",
            "jinja2",
            "openpyxl",
            "pypdf",
            "pypdfium2",
            "sqlite3",
            "anthropic",
            "google",
            "tiktoken",
            "jsonschema",
            "uvicorn",
            "docx",
            "pytesseract",
            "PIL",
            "databricks",
            "opendv_*",
        },
        set(),
    ),
    "src/opendv/sources": (
        False,
        {
            "opendv.verifier",
            "opendv.storage",
            "opendv.agents",
            "opendv.service",
            "opendv.api",
            "opendv.cli",
            "fastapi",
            "starlette",
            "sqlglot",
            "typer",
            "mcp",
            "rdflib",
            "opendv_*",
        },
        set(),
    ),
    "src/opendv/verifier": (
        False,
        {
            "opendv.sources",
            "opendv.storage",
            "opendv.agents",
            "opendv.service",
            "opendv.api",
            "opendv.cli",
            "duckdb",
            "sqlglot",
            "fastapi",
            "starlette",
            "typer",
            "mcp",
            "rdflib",
            "opendv_*",
        },
        set(),
    ),
    "src/opendv/storage": (
        False,
        {
            "opendv.sources",
            "opendv.verifier",
            "opendv.agents",
            "opendv.service",
            "opendv.api",
            "opendv.cli",
            "fastapi",
            "starlette",
            "typer",
            "mcp",
            "rdflib",
            "opendv_*",
        },
        {"opendv.verifier.ports"},
    ),
    "src/opendv/agents": (
        False,
        {
            "opendv.sources",
            "opendv.storage",
            "opendv.service",
            "opendv.api",
            "opendv.cli",
            "duckdb",
            "sqlglot",
            "fastapi",
            "starlette",
            "typer",
            "rdflib",
            "opendv_*",
        },
        set(),
    ),
    "src/opendv/service": (
        False,
        {
            "opendv.api",
            "opendv.cli",
            "fastapi",
            "starlette",
            "typer",
            "rdflib",
            "opendv_*",
        },
        set(),
    ),
    "src/opendv/api": (
        False,
        {
            "opendv.sources",
            "opendv.verifier",
            "opendv.storage",
            "opendv.agents",
            "opendv.cli",
            "typer",
            "duckdb",
            "sqlglot",
            "rdflib",
            "opendv_*",
        },
        set(),
    ),
    "src/opendv/cli": (
        False,
        {
            "opendv.sources",
            "opendv.verifier",
            "opendv.storage",
            "opendv.agents",
            "opendv.api",
            "fastapi",
            "starlette",
            "duckdb",
            "sqlglot",
            "rdflib",
            "opendv_*",
        },
        set(),
    ),
}


def _matches_prefix(mod: str, rule: str) -> bool:
    if rule.endswith("_*"):
        return mod.split(".")[0].startswith(rule[:-1])
    return mod == rule or mod.startswith(f"{rule}.")


@pytest.mark.architecture
@pytest.mark.parametrize("target_rel_dir,rule_spec", V2_LAYER_BANS.items())
def test_v2_import_contracts(
    target_rel_dir: str,
    rule_spec: tuple[bool, set[str], set[str]],
) -> None:
    """Enforce the strict 4-layer V2 DAG across `src/opendv/` without skips."""
    required, banned, exceptions = rule_spec
    target_dir = REPO_ROOT / target_rel_dir
    py_files = sorted(target_dir.rglob("*.py")) if target_dir.is_dir() else []
    if not py_files:
        assert not required, f"Required V2 layer directory missing or empty: {target_dir}"
        return

    src_root = REPO_ROOT / "src"
    violations: list[str] = []
    for py_file in py_files:
        rel_path = py_file.relative_to(REPO_ROOT)
        for lineno, mod in get_imports(
            py_file, package_root=src_root, include_from_targets=True
        ):
            if any(_matches_prefix(mod, exc) for exc in exceptions):
                continue
            if any(_matches_prefix(mod, b) for b in banned):
                violations.append(f"{rel_path}:{lineno}: banned V2 import '{mod}'")

    assert not violations, (
        f"V2 layering contract violations detected in '{target_rel_dir}':\n"
        + "\n".join(violations)
    )


@pytest.mark.architecture
@pytest.mark.parametrize("target_rel_dir,banned", BANNED_IMPORTS.items())
def test_import_contracts(target_rel_dir: str, banned: set[str]) -> None:
    """Verify that packages and apps observe strict dependency layering boundaries."""
    target_dir = REPO_ROOT / target_rel_dir
    assert target_dir.exists(), f"Target directory does not exist: {target_dir}"

    py_files = list(target_dir.rglob("*.py"))
    assert py_files, f"No Python files found in {target_dir}"

    violations: list[str] = []
    for py_file in py_files:
        rel_path = py_file.relative_to(REPO_ROOT)
        for lineno, mod in get_imports(py_file):
            # Reasoned exemption: StudioAgentRunner setup in platform-api main.py loads HarnessConfig
            if (
                rel_path.as_posix() == "apps/opendv-platform-api/src/opendv_platform_api/main.py"
                and mod == "opendv_agent_harness.models"
            ):
                continue
            top_mod = mod.split(".")[0]
            if top_mod in banned or mod in banned:
                violations.append(f"{rel_path}:{lineno}: banned import '{mod}'")

    assert not violations, (
        f"Layering contract violations detected in '{target_rel_dir}':\n" + "\n".join(violations)
    )
