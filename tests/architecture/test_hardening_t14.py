"""Static scans and behavioral hardening checks for Open-DV V2 (`src/opendv/`, T14).

Enforces:
1. No hardcoded OS paths (/tmp, /usr, /etc, home/expanduser) outside allowlisted CLI/path utils.
2. No unparameterized 127.0.0.1 / localhost literals outside loopback security/SSRF guards.
3. No Private Equity demo dataset identifiers in `src/opendv/`.
4. Strictly allowlisted `noqa: BLE001` boundaries (`3` modules in `src/opendv/`).
5. Behavioral T14 checks on `OpenDVService`, `create_app`, `SettingsStore`, `zip_guard`,
   and bounded uploads (`ARCHIVE_SIZE_LIMIT_EXCEEDED`, `ARCHIVE_SECURITY_VIOLATION`,
   `BUNDLE_HASH_MISMATCH`, `RESOURCE_NOT_FOUND`).
"""

from __future__ import annotations

import ast
import asyncio
import io
import os
import zipfile
from pathlib import Path

import pytest
from fastapi import UploadFile
from starlette.testclient import TestClient

from opendv.api._route_helpers import read_upload_bounded
from opendv.api.server import create_app
from opendv.models import ErrorCode, OpenDVError, ProjectInitReq
from opendv.service import OpenDVService
from opendv.service.bundles.zip_guard import inspect_and_verify_archive
from opendv.service.settings_store import SettingsStore
from tests.architecture.conftest import REPO_ROOT
from tests.architecture.test_mock_guards import PRODUCTION_PY_DIRS

BANNED_PATH_LITERALS = [
    "/tmp",
    "/usr",
    "/etc",
    "C:\\Program Files",
    "C:\\Windows",
    "find_workspace_root",
    "REPO_ROOT",
    "expanduser",
]

BANNED_RELATIVE_PREFIXES = ["examples/", "docs/"]

BANNED_PE_IDENTIFIERS = [
    "hub_fund",
    "hub_limited_partner",
    "lp-101",
    "proj-apex",
    "run-pe-apex",
    "calpers",
    "is_pe_domain",
]

ALLOWLISTED_EXPANDER_MODULES = {
    "src/opendv/cli/_client.py",
    "src/opendv/cli/_commands_sources.py",
    "src/opendv/cli/_output.py",
    "src/opendv/service/workflows/projects_svc.py",
    "src/opendv/service/os_platform.py",
}

ALLOWLISTED_LOOPBACK_MODULES = {
    "src/opendv/api/_security.py",
    "src/opendv/cli/commands_core.py",
    "src/opendv/sources/adapters.py",
    "src/opendv/service/mcp_broker.py",
}

ALLOWLISTED_BLE001_BOUNDARIES = {
    "src/opendv/sources/_extractors.py",
    "src/opendv/storage/_readonly_sql_guard.py",
    "src/opendv/storage/prover/edge_batches.py",
}


def _extract_docstring_nodes(tree: ast.AST) -> set[ast.Constant]:
    docstrings: set[ast.Constant] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module))
            and node.body
            and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Constant)
            and isinstance(node.body[0].value.value, str)
        ):
            docstrings.add(node.body[0].value)
    return docstrings


@pytest.mark.architecture
@pytest.mark.hardening
@pytest.mark.parametrize("rel_dir", PRODUCTION_PY_DIRS)
def test_no_hardcoded_paths_in_production(rel_dir: str) -> None:
    """Assert zero hardcoded OS paths, repo roots, or unallowlisted expanduser/home calls."""
    target_dir = REPO_ROOT / rel_dir
    assert target_dir.is_dir(), f"Target directory does not exist: {target_dir}"

    violations: list[str] = []
    for py_file in sorted(target_dir.rglob("*.py")):
        rel = py_file.relative_to(REPO_ROOT).as_posix()
        if rel.startswith("src/opendv/api/static/"):
            continue
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                val = node.value
                for banned in BANNED_PATH_LITERALS:
                    if banned in val:
                        violations.append(f"{rel}:{node.lineno}: banned path literal '{val}'")
                for pref in BANNED_RELATIVE_PREFIXES:
                    if val.startswith(pref) or f"/{pref}" in val:
                        violations.append(f"{rel}:{node.lineno}: banned prefix '{val}'")
            elif isinstance(node, ast.Attribute) and node.attr in {"home", "expanduser"}:
                if node.attr == "expanduser" and rel in ALLOWLISTED_EXPANDER_MODULES:
                    continue
                violations.append(f"{rel}:{node.lineno}: banned method call '{node.attr}'")

    assert not violations, (
        f"Production directory '{rel_dir}' contains hardcoded paths:\n" + "\n".join(violations)
    )


@pytest.mark.architecture
@pytest.mark.hardening
@pytest.mark.parametrize("rel_dir", PRODUCTION_PY_DIRS)
def test_no_unparameterized_endpoints_in_production(rel_dir: str) -> None:
    """Assert zero unparameterized localhost/127.0.0.1 literals outside security/SSRF modules."""
    target_dir = REPO_ROOT / rel_dir
    assert target_dir.is_dir(), f"Target directory does not exist: {target_dir}"

    violations: list[str] = []
    for py_file in sorted(target_dir.rglob("*.py")):
        rel = py_file.relative_to(REPO_ROOT).as_posix()
        if rel in ALLOWLISTED_LOOPBACK_MODULES or rel.startswith("src/opendv/api/static/"):
            continue
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                val_lower = node.value.lower()
                if "127.0.0.1" in val_lower or "localhost" in val_lower:
                    violations.append(f"{rel}:{node.lineno}: unparameterized endpoint '{node.value}'")

    assert not violations, (
        f"Production directory '{rel_dir}' contains unallowlisted endpoints:\n"
        + "\n".join(violations)
    )


@pytest.mark.architecture
@pytest.mark.hardening
@pytest.mark.parametrize("rel_dir", PRODUCTION_PY_DIRS)
def test_no_pe_identifiers_in_production(rel_dir: str) -> None:
    """Assert zero Private Equity demo dataset identifiers in `src/opendv/`."""
    target_dir = REPO_ROOT / rel_dir
    assert target_dir.is_dir(), f"Target directory does not exist: {target_dir}"

    violations: list[str] = []
    for py_file in sorted(target_dir.rglob("*.py")):
        rel = py_file.relative_to(REPO_ROOT).as_posix()
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        docstring_nodes = _extract_docstring_nodes(tree)
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                if node in docstring_nodes:
                    continue
                val_lower = node.value.lower()
                for banned in [*BANNED_PE_IDENTIFIERS, "investran"]:
                    if banned in val_lower:
                        violations.append(f"{rel}:{node.lineno}: PE string literal '{node.value}'")
            elif isinstance(node, ast.Name):
                name_lower = node.id.lower()
                for banned in BANNED_PE_IDENTIFIERS:
                    if banned in name_lower:
                        violations.append(f"{rel}:{node.lineno}: PE identifier Name '{node.id}'")
            elif isinstance(node, ast.Attribute):
                attr_lower = node.attr.lower()
                for banned in BANNED_PE_IDENTIFIERS:
                    if banned in attr_lower:
                        violations.append(f"{rel}:{node.lineno}: PE attribute '{node.attr}'")

    assert not violations, (
        f"Production directory '{rel_dir}' contains PE identifiers:\n" + "\n".join(violations)
    )


@pytest.mark.architecture
@pytest.mark.hardening
def test_noqa_ble001_strictly_limited_to_allowlisted_boundaries() -> None:
    """Assert `# noqa: BLE001` in `src/opendv/` is strictly confined to allowlisted boundaries."""
    found_files: set[str] = set()
    violations: list[str] = []

    for rel_dir in PRODUCTION_PY_DIRS:
        for py_file in sorted((REPO_ROOT / rel_dir).rglob("*.py")):
            rel_path = py_file.relative_to(REPO_ROOT).as_posix()
            for lineno, line in enumerate(py_file.read_text(encoding="utf-8").splitlines(), start=1):
                if "# noqa: BLE001" in line or "# noqa:BLE001" in line:
                    found_files.add(rel_path)
                    if rel_path not in ALLOWLISTED_BLE001_BOUNDARIES:
                        violations.append(f"{rel_path}:{lineno}: unallowlisted {line.strip()}")

    assert not violations, "Unallowlisted noqa: BLE001 detected:\n" + "\n".join(violations)
    assert found_files == ALLOWLISTED_BLE001_BOUNDARIES, (
        f"Allowlisted BLE001 set drifted: found={sorted(found_files)}, "
        f"expected={sorted(ALLOWLISTED_BLE001_BOUNDARIES)}"
    )


@pytest.mark.architecture
@pytest.mark.hardening
def test_t14_out_of_repo_unicode_workspace_and_api(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T14 Behavioral: OpenDVService, SettingsStore, and FastAPI run in unicode out-of-repo paths."""
    out_of_repo = tmp_path / "custom test ws with spaces and ünicöde"
    ws_dir = out_of_repo / "workspace"
    state_dir = out_of_repo / "state"
    ws_dir.mkdir(parents=True)
    state_dir.mkdir(parents=True)
    monkeypatch.setenv("OPENDV_WORKSPACE_ROOTS", str(ws_dir))

    store = SettingsStore(state_dir)
    settings = store.load_settings().model_copy(update={"cegar_policy": "strict"})
    store.save_settings(settings)
    assert store.load_settings().cegar_policy == "strict"

    service = OpenDVService(workspace_root=ws_dir, state_dir=state_dir)
    proj = service.init_project(
        ProjectInitReq(
            project_id="proj-ext-01",
            domain_namespace="asset_management",
        )
    )
    assert proj.project_id == "proj-ext-01"

    app = create_app(service=service, workspace_root=ws_dir)
    client = TestClient(app)
    resp = client.get("/api/v1/projects")
    assert resp.status_code == 200
    assert any(p["project_id"] == "proj-ext-01" for p in resp.json())

    missing_resp = client.get("/api/v1/projects/does-not-exist")
    assert missing_resp.status_code == 404
    assert missing_resp.json()["error_code"] == ErrorCode.RESOURCE_NOT_FOUND


@pytest.mark.architecture
@pytest.mark.hardening
def test_t14_zip_guard_and_bounded_upload_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T14 Behavioral: zip-slip, hash tampering, and over-limit uploads fail loudly."""
    zip_slip_path = tmp_path / "slip.opendv.zip"
    with zipfile.ZipFile(zip_slip_path, "w") as zf:
        zf.writestr("../escape.txt", "evil")
    with pytest.raises(OpenDVError) as exc_slip:
        inspect_and_verify_archive(zip_slip_path)
    assert exc_slip.value.error_code == ErrorCode.ARCHIVE_SECURITY_VIOLATION

    ws_dir = tmp_path / "ws"
    state_dir = tmp_path / "state"
    ws_dir.mkdir()
    state_dir.mkdir()
    monkeypatch.setenv("OPENDV_WORKSPACE_ROOTS", f"{tmp_path}{os.pathsep}{REPO_ROOT}")
    service = OpenDVService(workspace_root=ws_dir, state_dir=state_dir)
    service.init_project(ProjectInitReq(project_id="proj-tamper", domain_namespace="general"))
    good_zip, _ = service.export_bundle("proj-tamper", output_path=tmp_path / "good.opendv.zip")

    tampered_path = tmp_path / "tampered.opendv.zip"
    with zipfile.ZipFile(good_zip, "r") as src, zipfile.ZipFile(tampered_path, "w") as dst:
        for item in src.infolist():
            raw = src.read(item.filename)
            if item.filename != "manifest.json" and item.filename.endswith(".json"):
                raw = b'{"tampered": true}\n'
            dst.writestr(item.filename, raw)

    with pytest.raises(OpenDVError) as exc_hash:
        inspect_and_verify_archive(tampered_path)
    assert exc_hash.value.error_code == ErrorCode.BUNDLE_HASH_MISMATCH

    upload = UploadFile(filename="big.csv", file=io.BytesIO(b"x" * 128))
    with pytest.raises(OpenDVError) as exc_size:
        asyncio.run(read_upload_bounded(upload, max_bytes=64))
    assert exc_size.value.error_code == ErrorCode.ARCHIVE_SIZE_LIMIT_EXCEEDED
    assert exc_size.value.http_status == 413
