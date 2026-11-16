"""Architecture Gate: Zero Hardcoded Literals Enforcement.

Per ADR-020 and Doc 26 §1.1, validates via Python AST parsing and regex:
1. Zero hardcoded external dataset HTTP/HTTPS URLs in production code under
   packages/*/src and apps/*/src (exempting namespace URIs like fibo.edmcouncil.org,
   w3.org, open-dv.dev, and JSON $schema / $id).
2. Zero literal Databricks hostnames (*.cloud.databricks.com).
3. Zero literal API secrets (dapi[a-f0-9]{32}).
4. Zero unconfigured modeling thresholds outside config/constants modules.
5. Zero hardcoded domain mock tables, fallback sessions, synthetic SHA-256 hashes,
   or user-specific paths in production UI and CLI code.
"""

from __future__ import annotations

import ast
from pathlib import Path
import re
import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

PROD_SOURCE_ROOTS = [
    REPO_ROOT / "packages" / "opendv-core" / "src",
    REPO_ROOT / "packages" / "opendv-contracts" / "src",
    REPO_ROOT / "packages" / "opendv-sources" / "src",
    REPO_ROOT / "packages" / "opendv-axi-verifier" / "src",
    REPO_ROOT / "packages" / "opendv-duckdb-sandbox" / "src",
    REPO_ROOT / "packages" / "opendv-service" / "src",
    REPO_ROOT / "packages" / "opendv-agent-harness" / "src",
    REPO_ROOT / "apps" / "opendv-platform-api" / "src",
    REPO_ROOT / "apps" / "opendv-cli" / "src",
    REPO_ROOT / "apps" / "opendvctl" / "src",
    REPO_ROOT / "apps" / "opendv-web-workbench" / "src",
]

# Namespace & infrastructure URI exemptions (Decision D2 & ADR-020)
EXEMPT_URI_PATTERNS = [
    re.compile(r"^https?://([a-zA-Z0-9-]+\.)*w3\.org"),
    re.compile(r"^https?://([a-zA-Z0-9-]+\.)*openoffice\.org"),
    re.compile(r"^https?://([a-zA-Z0-9-]+\.)*json-schema\.org"),
    re.compile(r"^https?://([a-zA-Z0-9-]+\.)*edmcouncil\.org"),
    re.compile(r"^https?://([a-zA-Z0-9-]+\.)*open-dv\.dev"),
    re.compile(r"^https?://([a-zA-Z0-9-]+\.)*open-dv\.internal"),
    re.compile(r"^https?://(localhost|127\.0\.0\.1)(:\d+)?"),
    re.compile(r"^https?://generativelanguage\.googleapis\.com"),
    re.compile(r"^https?://api\.anthropic\.com"),
    re.compile(r"^https?://\$"),
]

DATABRICKS_HOST_RE = re.compile(r"[a-zA-Z0-9-]+\.cloud\.databricks\.com", re.IGNORECASE)
API_SECRET_RE = re.compile(r"dapi[a-f0-9]{32}", re.IGNORECASE)
HTTP_URL_RE = re.compile(r"https?://[^\s'\"`<>{}|\^\[\]]+", re.IGNORECASE)

EXACT_CONFIG_MODULE_ALLOWLIST = {
    "packages/opendv-core/src/opendv_core/config.py",
    "packages/opendv-sources/src/opendv_sources/config/defaults.py",
    "packages/opendv-agent-harness/src/opendv_agent_harness/pipeline/config.py",
}


def _is_exempt_uri(url: str) -> bool:
    clean = url.strip()
    return any(p.search(clean) is not None for p in EXEMPT_URI_PATTERNS)


def _collect_production_files(suffixes: tuple[str, ...]) -> list[Path]:
    files: list[Path] = []
    for root in PROD_SOURCE_ROOTS:
        if not root.is_dir():
            continue
        for f in root.glob("**/*"):
            if f.is_file() and f.suffix in suffixes:
                files.append(f)
    return sorted(files)


@pytest.mark.architecture
def test_zero_hardcoded_external_dataset_urls() -> None:
    """Check 1: Zero hardcoded external dataset HTTP/HTTPS URLs in production code.

    External dataset URLs (e.g. SEC, FRED, Northwind, Chinook downloads) must be centralized
    in tests/fixtures/public_am/dataset_manifest.yaml or runtime configuration.
    Namespace and protocol URIs (w3.org, fibo, open-dv, JSON schema, localhost, API endpoints)
    are strictly exempted.
    """
    py_files = _collect_production_files((".py",))
    assert py_files, "No production Python files found to scan"

    violations: list[str] = []
    for py_file in py_files:
        content = py_file.read_text(encoding="utf-8")
        try:
            tree = ast.parse(content, filename=str(py_file))
        except SyntaxError:
            continue

        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                for match in HTTP_URL_RE.finditer(node.value):
                    url = match.group(0)
                    if not _is_exempt_uri(url):
                        violations.append(
                            f"{py_file.relative_to(REPO_ROOT)}:{getattr(node, 'lineno', 0)}: {url}"
                        )

    # Also scan TypeScript files in apps
    ts_files = _collect_production_files((".ts", ".tsx"))
    for ts_file in ts_files:
        content = ts_file.read_text(encoding="utf-8")
        for lno, line in enumerate(content.splitlines(), start=1):
            for match in HTTP_URL_RE.finditer(line):
                url = match.group(0)
                if not _is_exempt_uri(url):
                    violations.append(
                        f"{ts_file.relative_to(REPO_ROOT)}:{lno}: {url}"
                    )

    assert not violations, (
        "Hardcoded external dataset URLs found in production packages:\n"
        + "\n".join(violations)
        + "\nMove dataset URLs to dataset_manifest.yaml or configuration."
    )


@pytest.mark.architecture
def test_zero_literal_databricks_hostnames() -> None:
    """Check 2: Zero literal Databricks hostnames (*.cloud.databricks.com) in production code."""
    prod_files = _collect_production_files((".py", ".ts", ".tsx"))
    assert prod_files, "No production files found to scan"

    violations: list[str] = []
    for p_file in prod_files:
        content = p_file.read_text(encoding="utf-8")
        for lno, line in enumerate(content.splitlines(), start=1):
            if DATABRICKS_HOST_RE.search(line):
                violations.append(f"{p_file.relative_to(REPO_ROOT)}:{lno}: {line.strip()}")

    assert not violations, (
        "Literal Databricks hostnames found in production packages:\n"
        + "\n".join(violations)
        + "\nUse environment variables or runtime parameters instead."
    )


@pytest.mark.architecture
def test_zero_literal_api_secrets() -> None:
    """Check 3: Zero literal API secrets (dapi[a-f0-9]{32}) in production code."""
    prod_files = _collect_production_files((".py", ".ts", ".tsx"))
    assert prod_files, "No production files found to scan"

    violations: list[str] = []
    for p_file in prod_files:
        content = p_file.read_text(encoding="utf-8")
        for lno, line in enumerate(content.splitlines(), start=1):
            if API_SECRET_RE.search(line):
                violations.append(f"{p_file.relative_to(REPO_ROOT)}:{lno}: found API token pattern")

    assert not violations, (
        "Literal API secrets found in production packages:\n"
        + "\n".join(violations)
    )


@pytest.mark.architecture
def test_zero_unconfigured_modeling_thresholds() -> None:
    """Check 4: Zero unconfigured modeling thresholds outside config/constants modules.

    Asserts that:
    1. CoreModelingConfig and SourceDefaultsConfig provide typed, env-overridable settings
       for algorithmic thresholds.
    2. No production modules outside config/constants files define unconfigurable module-level
       threshold constants.
    """
    from opendv_core.config import CoreModelingConfig, get_core_modeling_config
    from opendv_sources.config.defaults import SourceDefaultsConfig, get_source_defaults

    # 1. Assert centralized config exists and provides defaults
    core_cfg = get_core_modeling_config()
    assert isinstance(core_cfg, CoreModelingConfig)
    assert 0.0 < core_cfg.levenshtein_rename_threshold < 1.0
    assert 0.0 < core_cfg.jaccard_similarity_threshold < 1.0
    assert 0.0 < core_cfg.sparse_fk_null_threshold < 1.0
    assert core_cfg.max_sandbox_rows > 0

    sources_cfg = get_source_defaults()
    assert isinstance(sources_cfg, SourceDefaultsConfig)
    assert sources_cfg.default_max_sandbox_rows > 0

    # 2. AST check: ban unconfigured module-level threshold definitions (e.g. THRESHOLD = 0.85)
    # in non-config modules
    modeling_roots = [
        REPO_ROOT / "packages" / "opendv-core" / "src",
        REPO_ROOT / "packages" / "opendv-sources" / "src",
        REPO_ROOT / "packages" / "opendv-agent-harness" / "src",
        REPO_ROOT / "packages" / "opendv-duckdb-sandbox" / "src",
        REPO_ROOT / "packages" / "opendv-axi-verifier" / "src",
    ]
    py_files = [f for r in modeling_roots for f in r.glob("**/*.py") if f.is_file()]
    violations: list[str] = []

    for py_file in py_files:
        rel_posix = py_file.relative_to(REPO_ROOT).as_posix()
        if rel_posix in EXACT_CONFIG_MODULE_ALLOWLIST:
            continue

        content = py_file.read_text(encoding="utf-8")
        try:
            tree = ast.parse(content, filename=str(py_file))
        except SyntaxError:
            continue

        for stmt in tree.body:
            targets: list[ast.AST] = []
            if isinstance(stmt, ast.Assign):
                targets.extend(stmt.targets)
            elif isinstance(stmt, ast.AnnAssign):
                targets.append(stmt.target)

            for target in targets:
                if isinstance(target, ast.Name):
                    name = target.id
                    if name.isupper() and ("THRESHOLD" in name or "MAGIC" in name):
                        violations.append(
                            f"{py_file.relative_to(REPO_ROOT)}:{stmt.lineno}: "
                            f"Module-level constant '{name}' defined outside config/constants"
                        )

    assert not violations, (
        "Unconfigured module-level thresholds defined outside config modules:\n"
        + "\n".join(violations)
    )


@pytest.mark.architecture
def test_zero_hardcoded_domain_mocks_in_production_frontend() -> None:
    """Check 5: Zero hardcoded mock tables, fallback sessions, synthetic SHA-256 hashes,
    or user-specific paths in production UI and CLI code.
    """
    wb_src = REPO_ROOT / "apps" / "opendv-web-workbench" / "src"
    ctl_src = REPO_ROOT / "apps" / "opendvctl" / "src"

    banned_patterns = [
        re.compile(r"\bDEFAULT_DIFF\b"),
        re.compile(r"\bDEFAULT_SQLITE_TABLES\b"),
        re.compile(r"\bDEFAULT_DATABRICKS_TABLES\b"),
        re.compile(r"\bDEFAULT_MODELS\b"),
        re.compile(r"\bFALLBACK_[A-Z0-9_]+\b"),
        re.compile(r"\bsqlite_prod\b"),
        re.compile(r"\bdatabricks_uc\b"),
        re.compile(r"\bam_prod\b"),
        re.compile(r"\bsession_asset_mgmt_ibor\b"),
        re.compile(r"\bsession-am-core\b"),
        re.compile(r"\bsession-fibo-securities\b"),
        re.compile(r"e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"),
        re.compile(r"[A-Za-z]:[/\\][Uu]sers[/\\]"),
        re.compile(r"data/public_am/"),
        re.compile(r"Gate\s+G[0-5]:\s*Pass", re.IGNORECASE),
    ]

    violations: list[str] = []
    for root in [wb_src, ctl_src]:
        if not root.is_dir():
            continue
        for f in root.rglob("*"):
            if f.is_file() and f.suffix in (".ts", ".tsx"):
                content = f.read_text(encoding="utf-8")
                for lno, line in enumerate(content.splitlines(), start=1):
                    for pat in banned_patterns:
                        if pat.search(line):
                            violations.append(
                                f"{f.relative_to(REPO_ROOT)}:{lno}: matches '{pat.pattern}' -> {line.strip()}"
                            )

    assert not violations, (
        "Hardcoded domain mocks, fake sessions, synthetic hashes, or user paths found in production UI/CLI:\n"
        + "\n".join(violations)
    )


@pytest.mark.architecture
def test_zero_hardcoded_domain_hubs_in_production_code() -> None:
    """Check 6: Ban hardcoded domain hub strings ('hub_security', 'hub_customer') in production code."""
    pipeline_roots = [
        REPO_ROOT / "packages" / "opendv-core" / "src",
        REPO_ROOT / "packages" / "opendv-contracts" / "src",
        REPO_ROOT / "packages" / "opendv-sources" / "src",
        REPO_ROOT / "packages" / "opendv-axi-verifier" / "src",
        REPO_ROOT / "packages" / "opendv-duckdb-sandbox" / "src",
        REPO_ROOT / "packages" / "opendv-agent-harness" / "src",
        REPO_ROOT / "packages" / "opendv-service" / "src",
        REPO_ROOT / "apps" / "opendv-platform-api" / "src",
        REPO_ROOT / "apps" / "opendv-web-workbench" / "src",
        REPO_ROOT / "apps" / "opendv-cli" / "src",
        REPO_ROOT / "apps" / "opendvctl" / "src",
    ]
    banned = re.compile(r"[\"'](hub_security|hub_customer)[\"']")
    violations: list[str] = []
    for root in pipeline_roots:
        if not root.is_dir():
            continue
        for f in root.glob("**/*"):
            if f.is_file() and f.suffix in (".py", ".ts", ".tsx"):
                content = f.read_text(encoding="utf-8")
                for lno, line in enumerate(content.splitlines(), start=1):
                    if banned.search(line):
                        violations.append(f"{f.relative_to(REPO_ROOT)}:{lno}: {line.strip()}")

    assert not violations, (
        "Hardcoded domain hub strings ('hub_security', 'hub_customer') found in production code:\n"
        + "\n".join(violations)
        + "\nDomain hub strings must be dynamically derived from input specs or ontology."
    )
