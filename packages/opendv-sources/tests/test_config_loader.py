"""Unit tests for load_structured_document (SRC-05) and security containment."""

from __future__ import annotations

from pathlib import Path

import pytest

from opendv_core.paths import PathContainmentError
from opendv_sources.config.loader import load_structured_document
from opendv_sources.exceptions import FormatError, SourceNotFoundError


def test_load_valid_json_file(tmp_path: Path) -> None:
    """Verify loading a valid JSON file from workspace."""
    json_file = tmp_path / "config.json"
    json_file.write_text('{"pipeline_id": "test_pipe", "waves": 3}', encoding="utf-8")

    result = load_structured_document(json_file, workspace_root=tmp_path)
    assert result == {"pipeline_id": "test_pipe", "waves": 3}


def test_load_valid_yaml_file(tmp_path: Path) -> None:
    """Verify loading a valid YAML file from workspace."""
    yaml_file = tmp_path / "config.yaml"
    yaml_file.write_text(
        "pipeline_id: yaml_pipe\nwaves:\n  - wave_0\n  - wave_1\n",
        encoding="utf-8",
    )

    result = load_structured_document(yaml_file, workspace_root=tmp_path)
    assert result == {"pipeline_id": "yaml_pipe", "waves": ["wave_0", "wave_1"]}


def test_load_inline_json_string() -> None:
    """Verify loading an inline JSON document string."""
    inline_json = '{"model": "test_model", "enabled": true}'
    result = load_structured_document(inline_json)
    assert result == {"model": "test_model", "enabled": True}


def test_load_inline_yaml_string() -> None:
    """Verify loading an inline multi-line YAML document string."""
    inline_yaml = "agent:\n  name: OpenDV\n  temperature: 0.2\n"
    result = load_structured_document(inline_yaml)
    assert result == {"agent": {"name": "OpenDV", "temperature": 0.2}}


def test_file_not_found_raises_source_not_found_error(tmp_path: Path) -> None:
    """Verify missing file raises SourceNotFoundError (404)."""
    with pytest.raises(SourceNotFoundError) as exc_info:
        load_structured_document("missing_file.yaml", workspace_root=tmp_path)
    assert "Config document file not found" in str(exc_info.value)
    assert exc_info.value.http_status == 404


def test_path_traversal_forbidden(tmp_path: Path) -> None:
    """Verify path traversal attempts raise PathContainmentError (400)."""
    with pytest.raises(PathContainmentError):
        load_structured_document("../../etc/passwd", workspace_root=tmp_path)


def test_null_byte_path_rejected(tmp_path: Path) -> None:
    """Verify null bytes in file paths are rejected."""
    with pytest.raises(PathContainmentError):
        load_structured_document("config.yaml\0.txt", workspace_root=tmp_path)


def test_invalid_json_syntax_raises_format_error() -> None:
    """Verify corrupted JSON raises FormatError (422)."""
    with pytest.raises(FormatError) as exc_info:
        load_structured_document('{"incomplete_json": ')
    assert "Failed to parse structured document" in str(exc_info.value)
    assert exc_info.value.http_status == 422


def test_non_dict_root_raises_format_error() -> None:
    """Verify documents with array or scalar roots raise FormatError."""
    with pytest.raises(FormatError) as exc_array:
        load_structured_document("[1, 2, 3]")
    assert "Expected object mapping, got list" in str(exc_array.value)

    with pytest.raises(FormatError) as exc_scalar:
        load_structured_document("only_a_string\n")
    assert "Expected object mapping, got str" in str(exc_scalar.value)


def test_empty_content_raises_format_error() -> None:
    """Verify empty content raises FormatError."""
    with pytest.raises(FormatError) as exc_info:
        load_structured_document("   ")
    assert "Expected non-empty structured document mapping" in str(exc_info.value)


def test_load_structured_document_rejects_ambient_absolute_path_without_root() -> None:
    """Verify loading an absolute path with no workspace_root raises PathContainmentError."""
    with pytest.raises(PathContainmentError):
        load_structured_document("C:/Windows/win.ini")
