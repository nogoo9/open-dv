"""Unit tests for canonical workspace path containment and resolution (DUP-04)."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from opendv_core import PathContainmentError, resolve_workspace_path


def test_resolve_workspace_path_success_relative(tmp_path: Path) -> None:
    test_file = tmp_path / "sub" / "file.txt"
    test_file.parent.mkdir(parents=True)
    test_file.write_text("hello", encoding="utf-8")

    resolved = resolve_workspace_path("sub/file.txt", workspace_root=tmp_path)
    assert resolved == test_file.resolve()
    assert resolved.exists()


def test_resolve_workspace_path_success_absolute(tmp_path: Path) -> None:
    test_file = tmp_path / "data.csv"
    test_file.write_text("a,b\n1,2", encoding="utf-8")

    resolved = resolve_workspace_path(test_file, workspace_root=tmp_path)
    assert resolved == test_file.resolve()


def test_resolve_workspace_path_rejects_empty_and_whitespace() -> None:
    with pytest.raises(PathContainmentError, match="must not be empty"):
        resolve_workspace_path("")

    with pytest.raises(PathContainmentError, match="must not be empty"):
        resolve_workspace_path("   \t  \n  ")


def test_resolve_workspace_path_rejects_null_bytes(tmp_path: Path) -> None:
    with pytest.raises(PathContainmentError, match="must not contain null bytes"):
        resolve_workspace_path("valid_data.json\x00extra", workspace_root=tmp_path)

    with pytest.raises(PathContainmentError, match="must not contain null bytes"):
        resolve_workspace_path("sub/\0secrets.txt", workspace_root=tmp_path)


def test_resolve_workspace_path_rejects_parent_traversal(tmp_path: Path) -> None:
    with pytest.raises(PathContainmentError, match=r"Path traversal .* is strictly forbidden"):
        resolve_workspace_path("../outside.txt", workspace_root=tmp_path)

    with pytest.raises(PathContainmentError, match=r"Path traversal .* is strictly forbidden"):
        resolve_workspace_path("sub/../../outside.txt", workspace_root=tmp_path)

    with pytest.raises(PathContainmentError, match=r"Path traversal .* is strictly forbidden"):
        resolve_workspace_path(f"{tmp_path}/../outside.txt", workspace_root=tmp_path)


def test_resolve_workspace_path_rejects_escape_from_workspace_root(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    outside = tmp_path / "outside_dir" / "secret.txt"
    outside.parent.mkdir()
    outside.write_text("secret", encoding="utf-8")

    with pytest.raises(PathContainmentError, match="escaping permitted workspace roots"):
        resolve_workspace_path(outside, workspace_root=root, allow_temp=False)


def test_resolve_workspace_path_allows_allowed_roots(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    extra_dir = tmp_path / "extra"
    extra_dir.mkdir()
    extra_file = extra_dir / "target.json"
    extra_file.write_text("{}", encoding="utf-8")

    # Fails when extra_dir is not in allowed_roots
    with pytest.raises(PathContainmentError):
        resolve_workspace_path(extra_file, workspace_root=root, allow_temp=False)

    # Succeeds when extra_dir is in allowed_roots
    resolved = resolve_workspace_path(
        extra_file, workspace_root=root, allowed_roots=[extra_dir], allow_temp=False
    )
    assert resolved == extra_file.resolve()


def test_resolve_workspace_path_tempdir_policy(tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    temp_dir = Path(tempfile.gettempdir()).resolve()
    temp_file = temp_dir / "opendv_test_paths_temp.txt"
    temp_file.write_text("temp", encoding="utf-8")

    try:
        # Default allows tempdir
        resolved = resolve_workspace_path(temp_file, workspace_root=root, allow_temp=True)
        assert resolved == temp_file.resolve()

        # allow_temp=False rejects tempdir if not under workspace_root
        with pytest.raises(PathContainmentError):
            resolve_workspace_path(temp_file, workspace_root=root, allow_temp=False)
    finally:
        if temp_file.exists():
            temp_file.unlink()


def test_resolve_workspace_path_base_dir_resolution(tmp_path: Path) -> None:
    ws_root = tmp_path / "ws"
    ws_root.mkdir()
    base_dir = tmp_path / "base"
    base_dir.mkdir()

    base_file = base_dir / "config.yaml"
    base_file.write_text("key: value", encoding="utf-8")

    # Relative path existing in base_dir
    resolved = resolve_workspace_path("config.yaml", workspace_root=ws_root, base_dir=base_dir)
    assert resolved == base_file.resolve()

    # Relative path existing in ws_root
    ws_file = ws_root / "ws_config.yaml"
    ws_file.write_text("ws: true", encoding="utf-8")
    resolved_ws = resolve_workspace_path(
        "ws_config.yaml", workspace_root=ws_root, base_dir=base_dir
    )
    assert resolved_ws == ws_file.resolve()


def test_resolve_workspace_path_requires_workspace_root_or_base_dir() -> None:
    with pytest.raises(PathContainmentError, match="REQ-CFG-01"):
        resolve_workspace_path("examples")
