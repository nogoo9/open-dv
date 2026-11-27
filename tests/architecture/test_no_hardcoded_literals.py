"""Architecture Gate: Zero Hardcoded Literals Enforcement across `src/opendv/` and `web/src/`."""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

from tests.architecture.conftest import REPO_ROOT

PROD_SOURCE_ROOTS = [
    REPO_ROOT / "src" / "opendv",
    REPO_ROOT / "web" / "src",
]

# Namespace & infrastructure URI exemptions (Decision D2 & ADR-020)
EXEMPT_URI_PATTERNS = [
    re.compile(r"^https?://([a-zA-Z0-9-]+\.)*w3\.org"),
    re.compile(r"^https?://([a-zA-Z0-9-]+\.)*omg\.org"),
    re.compile(r"^https?://([a-zA-Z0-9-]+\.)*openoffice\.org"),
    re.compile(r"^https?://([a-zA-Z0-9-]+\.)*json-schema\.org"),
    re.compile(r"^https?://([a-zA-Z0-9-]+\.)*edmcouncil\.org"),
    re.compile(r"^https?://([a-zA-Z0-9-]+\.)*open-dv\.dev"),
    re.compile(r"^https?://([a-zA-Z0-9-]+\.)*opendv\.dev"),
    re.compile(r"^https?://([a-zA-Z0-9-]+\.)*open-dv\.internal"),
    re.compile(r"^https?://(localhost|127\.0\.0\.1)(:\d+)?"),
    re.compile(r"^https?://generativelanguage\.googleapis\.com"),
    re.compile(r"^https?://api\.anthropic\.com"),
    re.compile(r"^https?://\$"),
]

DATABRICKS_HOST_RE = re.compile(r"[a-zA-Z0-9-]+\.cloud\.databricks\.com", re.IGNORECASE)
API_SECRET_RE = re.compile(r"dapi[a-f0-9]{32}", re.IGNORECASE)
HTTP_URL_RE = re.compile(r"https?://[^\s'\"`<>{}|\^\[\]]+", re.IGNORECASE)

# Canonical configuration & security/matcher threshold modules in V2
EXACT_CONFIG_MODULE_ALLOWLIST = {
    "src/opendv/service/settings_store.py",
    "src/opendv/service/bundles/zip_guard.py",
    "src/opendv/sources/clustering.py",
    "src/opendv/grounding/providers/fibo/closure_rules.py",
    "src/opendv/grounding/kernel/pii_core.py",
    "src/opendv/sources/pii.py",
}


def _is_exempt_uri(url: str) -> bool:
    clean = url.strip()
    return any(p.search(clean) is not None for p in EXEMPT_URI_PATTERNS)


def _collect_production_files(suffixes: tuple[str, ...]) -> list[Path]:
    files: list[Path] = []
    for root in PROD_SOURCE_ROOTS:
        if not root.is_dir():
            continue
        for f in root.rglob("*"):
            if not f.is_file() or f.suffix not in suffixes:
                continue
            rel = f.relative_to(REPO_ROOT).as_posix()
            if (
                rel.startswith("src/opendv/api/static/")
                or rel == "web/src/api/types.generated.ts"
                or "tests" in f.parts
                or "e2e" in f.parts
            ):
                continue
            files.append(f)
    return sorted(files)


@pytest.mark.architecture
def test_zero_hardcoded_external_dataset_urls() -> None:
    """Check 1: Zero hardcoded external dataset HTTP/HTTPS URLs in production code."""
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

    ts_files = _collect_production_files((".ts", ".tsx"))
    assert ts_files, "No production TypeScript files found to scan"
    for ts_file in ts_files:
        content = ts_file.read_text(encoding="utf-8")
        for lno, line in enumerate(content.splitlines(), start=1):
            for match in HTTP_URL_RE.finditer(line):
                url = match.group(0)
                if not _is_exempt_uri(url):
                    violations.append(f"{ts_file.relative_to(REPO_ROOT)}:{lno}: {url}")

    assert not violations, (
        "Hardcoded external dataset URLs found in production sources:\n"
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

    assert not violations, "Literal API secrets found in production sources:\n" + "\n".join(
        violations
    )


@pytest.mark.architecture
def test_zero_unconfigured_modeling_thresholds(tmp_path: Path) -> None:
    """Check 4: Thresholds live in typed V2 settings/matcher configs, not arbitrary modules."""
    from opendv.models import CEGAR_POLICY_SPECS, STEP4_DEFAULT_BUDGET
    from opendv.service.settings_store import SettingsStore

    settings = SettingsStore(tmp_path).load_settings()
    assert settings.cegar_policy in CEGAR_POLICY_SPECS
    assert STEP4_DEFAULT_BUDGET.max_turns > 0
    assert STEP4_DEFAULT_BUDGET.max_tokens > 0
    assert STEP4_DEFAULT_BUDGET.timeout_seconds > 0

    bm25_cfg_path = REPO_ROOT / "schemas" / "v2" / "matchers" / "bm25_fibo_v1.json"
    assert bm25_cfg_path.is_file(), f"Missing {bm25_cfg_path}"
    bm25_cfg = json.loads(bm25_cfg_path.read_text(encoding="utf-8"))
    assert float(bm25_cfg["grounding_threshold"]) >= 0.65

    py_files = _collect_production_files((".py",))
    violations: list[str] = []
    for py_file in py_files:
        rel_posix = py_file.relative_to(REPO_ROOT).as_posix()
        if rel_posix in EXACT_CONFIG_MODULE_ALLOWLIST:
            continue

        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        for stmt in tree.body:
            targets: list[ast.AST] = []
            if isinstance(stmt, ast.Assign):
                targets.extend(stmt.targets)
            elif isinstance(stmt, ast.AnnAssign):
                targets.append(stmt.target)
            for target in targets:
                if (
                    isinstance(target, ast.Name)
                    and target.id.isupper()
                    and ("THRESHOLD" in target.id or "MAGIC" in target.id)
                ):
                    violations.append(
                        f"{rel_posix}:{stmt.lineno}: "
                        f"Module-level constant '{target.id}' defined outside config allowlist"
                    )

    assert not violations, (
        "Unconfigured module-level thresholds defined outside allowlisted config modules:\n"
        + "\n".join(violations)
    )


@pytest.mark.architecture
def test_zero_hardcoded_domain_mocks_in_production_frontend() -> None:
    """Check 5: Zero hardcoded domain mocks, fallback tables, synthetic hashes, or fixture paths."""
    banned_patterns = [
        re.compile(r"\bDEFAULT_DIFF\b"),
        re.compile(r"\bDEFAULT_SQLITE_TABLES\b"),
        re.compile(r"\bDEFAULT_DATABRICKS_TABLES\b"),
        re.compile(r"\bDEFAULT_MODELS\b"),
        re.compile(r"\bFALLBACK_[A-Z0-9_]+\b"),
        re.compile(r"\bsqlite_prod\b"),
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
    for f in _collect_production_files((".ts", ".tsx")):
        content = f.read_text(encoding="utf-8")
        for lno, line in enumerate(content.splitlines(), start=1):
            for pat in banned_patterns:
                if pat.search(line):
                    violations.append(
                        f"{f.relative_to(REPO_ROOT).as_posix()}:{lno}: "
                        f"matches '{pat.pattern}' -> {line.strip()}"
                    )

    assert not violations, (
        "Hardcoded domain mocks, fake sessions, synthetic hashes, or user paths in web/src:\n"
        + "\n".join(violations)
    )


@pytest.mark.architecture
def test_zero_hardcoded_domain_hubs_in_production_code() -> None:
    """Check 6: Ban hardcoded domain hub strings ('hub_security', 'hub_customer') in production."""
    banned = re.compile(r"[\"'](hub_security|hub_customer)[\"']")
    violations: list[str] = []
    for f in _collect_production_files((".py", ".ts", ".tsx")):
        content = f.read_text(encoding="utf-8")
        for lno, line in enumerate(content.splitlines(), start=1):
            if banned.search(line):
                violations.append(f"{f.relative_to(REPO_ROOT).as_posix()}:{lno}: {line.strip()}")

    assert not violations, (
        "Hardcoded domain hub strings ('hub_security', 'hub_customer') found in production code:\n"
        + "\n".join(violations)
        + "\nDomain hub strings must be dynamically derived from input specs or ontology."
    )
