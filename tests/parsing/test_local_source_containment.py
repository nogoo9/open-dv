"""Critical-path security & containment tests for resolve_local_source_blob and SourceBlob."""

from __future__ import annotations

import hashlib
import os
import stat
import tempfile
import types
from pathlib import Path
from typing import Any

import pytest

from opendv.models.document_ir import (
    DEFAULT_MAX_SOURCE_BYTES,
    SourceBlob,
    sanitize_source_uri,
)
from opendv.models.errors import CliExitCode, ErrorCode, OpenDVError
from opendv.service.bundles.zip_guard import WIN_REPARSE_ATTR
from opendv.service.parsing_svc import resolve_local_source_blob


def test_windows_drive_prefix_and_file_uri_sanitization(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Windows C:\\... paths bypass urlparse scheme parsing and file:// strips query/userinfo."""
    ws = tmp_path / "ws"
    ws.mkdir()
    doc = ws / "report.txt"
    payload = b"Custody policy addendum.\n"
    doc.write_bytes(payload)
    expected_sha = hashlib.sha256(payload).hexdigest()

    # Drive-letter path (C:\... and C:/...) must never be treated as URI scheme "c"
    blob_native = resolve_local_source_blob(str(doc), allowed_roots=[ws])
    blob_posix = resolve_local_source_blob(doc.as_posix(), allowed_roots=[ws])
    assert blob_native.binary_sha256 == expected_sha
    assert blob_posix.binary_sha256 == expected_sha
    assert blob_native.byte_size == len(payload)
    assert blob_native.is_spooled_temp is False

    # sanitize_source_uri preserves Windows drive letters while stripping URI userinfo/query/frag
    assert sanitize_source_uri(r"C:\docs\report.pdf") == r"C:\docs\report.pdf"
    assert sanitize_source_uri("c:/docs/report.pdf") == "c:/docs/report.pdf"
    assert (
        sanitize_source_uri("file://alice:secret@/docs/report.pdf?token=xyz#sec2")
        == "file:///docs/report.pdf"
    )

    # Valid file:// URI with empty netloc + query/fragment strips query/fragment in uri_sanitized
    dirty_uri = f"{doc.resolve().as_uri()}?access_token=topsecret#page=1"
    blob_uri = resolve_local_source_blob(dirty_uri, allowed_roots=[ws])
    assert blob_uri.binary_sha256 == expected_sha
    assert "topsecret" not in blob_uri.uri_sanitized
    assert "?" not in blob_uri.uri_sanitized
    assert "#" not in blob_uri.uri_sanitized

    # Non-empty netloc in file:// -> SYNTAX_INVALID_ARGUMENT (400)
    with pytest.raises(OpenDVError) as exc_netloc:
        resolve_local_source_blob("file://fileserver/share/report.txt", allowed_roots=[ws])
    assert exc_netloc.value.error_code == ErrorCode.SYNTAX_INVALID_ARGUMENT
    assert exc_netloc.value.http_status == 400

    # Mocked Windows-style URI path (/C:/...) with url2pathname handling
    captured: list[str] = []
    monkeypatch.setattr("opendv.service.parsing_svc.urllib.request.url2pathname", lambda p: (captured.append(p) or str(doc)))
    blob_win_uri = resolve_local_source_blob("file:///C:/ignored/report.txt?q=1", allowed_roots=[ws])
    assert captured == ["/C:/ignored/report.txt"]
    assert blob_win_uri.binary_sha256 == expected_sha


def test_symlinks_rejected_before_containment_with_stop_roots(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """reject_symlinks_on_path_and_ancestors(..., stop_roots=...) runs before containment."""
    ws = tmp_path / "ws"
    sub = ws / "nested"
    sub.mkdir(parents=True)
    doc = sub / "note.txt"
    doc.write_text("clean", encoding="utf-8")

    # 1. Verify execution order on valid path: symlink check precedes validate_workspace_containment
    call_order: list[str] = []
    import opendv.service.parsing_svc as parsing_svc_mod

    orig_reject = parsing_svc_mod.reject_symlinks_on_path_and_ancestors
    orig_validate = parsing_svc_mod.validate_workspace_containment

    def _spy_reject(target: Path, *, stop_roots: Any = ()) -> None:
        call_order.append("reject_symlinks")
        orig_reject(target, stop_roots=stop_roots)

    def _spy_validate(workspace_path: Path | str, allowed_roots: Any = None, **kw: Any) -> Path:
        call_order.append("validate_containment")
        return orig_validate(workspace_path, allowed_roots=allowed_roots, **kw)

    monkeypatch.setattr(parsing_svc_mod, "reject_symlinks_on_path_and_ancestors", _spy_reject)
    monkeypatch.setattr(parsing_svc_mod, "validate_workspace_containment", _spy_validate)

    blob_ok = resolve_local_source_blob(doc, allowed_roots=[ws])
    assert blob_ok.path == doc.resolve()
    assert call_order == ["reject_symlinks", "validate_containment"]

    # 2. Ancestor reparse point inside stop_roots aborts BEFORE validate_workspace_containment
    call_order.clear()
    orig_lstat = Path.lstat

    def _reparse_ancestor_lstat(self: Path, *args: Any, **kwargs: Any) -> Any:
        st = orig_lstat(self, *args, **kwargs)
        if os.path.normcase(os.path.abspath(str(self))) == os.path.normcase(os.path.abspath(str(sub))):
            return types.SimpleNamespace(
                st_mode=stat.S_IFDIR | 0o755,
                st_file_attributes=WIN_REPARSE_ATTR,
                st_size=st.st_size,
            )
        return st

    monkeypatch.setattr(Path, "lstat", _reparse_ancestor_lstat)
    with pytest.raises(OpenDVError) as exc_anc:
        resolve_local_source_blob(doc, allowed_roots=[ws])
    assert exc_anc.value.error_code == ErrorCode.ARCHIVE_SECURITY_VIOLATION
    assert exc_anc.value.http_status == 422
    assert call_order == ["reject_symlinks"]

    # 3. Leaf symlink (S_IFLNK) inside stop_roots -> ARCHIVE_SECURITY_VIOLATION (422)
    call_order.clear()

    def _symlink_leaf_lstat(self: Path, *args: Any, **kwargs: Any) -> Any:
        st = orig_lstat(self, *args, **kwargs)
        if os.path.normcase(os.path.abspath(str(self))) == os.path.normcase(os.path.abspath(str(doc))):
            return types.SimpleNamespace(
                st_mode=stat.S_IFLNK | 0o777,
                st_file_attributes=0,
                st_size=st.st_size,
            )
        return st

    monkeypatch.setattr(Path, "lstat", _symlink_leaf_lstat)
    with pytest.raises(OpenDVError) as exc_leaf:
        resolve_local_source_blob(doc, allowed_roots=[ws])
    assert exc_leaf.value.error_code == ErrorCode.ARCHIVE_SECURITY_VIOLATION
    assert exc_leaf.value.http_status == 422
    assert call_order == ["reject_symlinks"]

    # 4. Ancestor ABOVE stop_roots marked as reparse point must NOT fail containment
    def _reparse_above_root_lstat(self: Path, *args: Any, **kwargs: Any) -> Any:
        st = orig_lstat(self, *args, **kwargs)
        if os.path.normcase(os.path.abspath(str(self))) == os.path.normcase(os.path.abspath(str(tmp_path))):
            return types.SimpleNamespace(
                st_mode=stat.S_IFDIR | 0o755,
                st_file_attributes=WIN_REPARSE_ATTR,
                st_size=st.st_size,
            )
        return st

    monkeypatch.setattr(Path, "lstat", _reparse_above_root_lstat)
    blob_above = resolve_local_source_blob(doc, allowed_roots=[ws])
    assert blob_above.byte_size == len("clean")


def test_containment_security_syntax_and_not_found_mappings(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify exact 422, 400, and 404 ErrorCode mappings across adversarial inputs."""
    ws = tmp_path / "ws"
    ws.mkdir()
    sample = ws / "valid.pdf"
    sample.write_bytes(b"%PDF-1.4\n")
    outside = tmp_path / "outside.pdf"
    outside.write_bytes(b"%PDF-1.4\n")

    # 422 ARCHIVE_SECURITY_VIOLATION: UNC, ADS, pipe, traversal, root escape, resolved UNC
    security_cases = [
        r"\\server\share\doc.pdf",
        "//server/share/doc.pdf",
        f"{sample}:Zone.Identifier",
        f"{sample}|stream",
        f"{sample.resolve().as_uri()}:ads",
        str(ws / ".." / "outside.pdf"),
        str(outside),
    ]
    for bad in security_cases:
        with pytest.raises(OpenDVError) as exc_sec:
            resolve_local_source_blob(bad, allowed_roots=[ws])
        assert exc_sec.value.error_code == ErrorCode.ARCHIVE_SECURITY_VIOLATION, bad
        assert exc_sec.value.http_status == 422

    # Resolved UNC path from os.path.realpath inside validate_workspace_containment -> 422
    monkeypatch.setattr("opendv.service.os_platform.os.path.realpath", lambda _p: r"\\?\UNC\srv\sh\f.pdf")
    with pytest.raises(OpenDVError) as exc_unc_res:
        resolve_local_source_blob(sample, allowed_roots=[ws])
    assert exc_unc_res.value.error_code == ErrorCode.ARCHIVE_SECURITY_VIOLATION
    assert exc_unc_res.value.http_status == 422
    monkeypatch.undo()

    # 400 SYNTAX_INVALID_ARGUMENT: chained ::, non-file scheme, empty/control/null, reserved device, directory
    syntax_cases = [
        "",
        "   \t  ",
        str(ws / "bad\x00.pdf"),
        str(ws / "bad\x07.pdf"),
        "file::https://example.org/doc.pdf",
        "https://example.org/doc.pdf",
        "ftp://example.org/doc.pdf",
        "file://nonempty-host/ws/valid.pdf",
        str(ws / "CON.pdf"),
        str(ws / "NUL.txt"),
        str(ws / "LPT1.docx"),
        str(ws),
    ]
    if os.name == "nt":
        syntax_cases.append(str(ws / "wild?.pdf"))
    for bad_syntax in syntax_cases:
        with pytest.raises(OpenDVError) as exc_400:
            resolve_local_source_blob(bad_syntax, allowed_roots=[ws])
        assert exc_400.value.error_code == ErrorCode.SYNTAX_INVALID_ARGUMENT, bad_syntax
        assert exc_400.value.http_status == 400
        assert exc_400.value.exit_code == CliExitCode.VALIDATION_ERROR

    # 404 RESOURCE_NOT_FOUND: FileNotFoundError mapped cleanly
    missing = ws / "missing_doc.pdf"
    with pytest.raises(OpenDVError) as exc_404:
        resolve_local_source_blob(missing, allowed_roots=[ws])
    assert exc_404.value.error_code == ErrorCode.RESOURCE_NOT_FOUND
    assert exc_404.value.http_status == 404
    assert exc_404.value.exit_code == CliExitCode.NOT_FOUND

    with pytest.raises(OpenDVError) as exc_blob_404:
        SourceBlob.from_path(missing)
    assert exc_blob_404.value.error_code == ErrorCode.RESOURCE_NOT_FOUND


def test_250_mib_size_cap_and_source_blob_from_bytes_winerror32_cleanup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify 250 MiB (262,144,000) cap -> 413 and WinError 32-safe SourceBlob.from_bytes cleanup."""
    assert DEFAULT_MAX_SOURCE_BYTES == 262_144_000

    ws = tmp_path / "ws"
    ws.mkdir()
    doc = ws / "bounded.txt"
    content = b"0123456789abcdef"
    doc.write_bytes(content)

    # Exact byte cap succeeds; 1 byte over fails with 413 ARCHIVE_SIZE_LIMIT_EXCEEDED
    ok_blob = resolve_local_source_blob(doc, allowed_roots=[ws], max_bytes=len(content))
    assert ok_blob.byte_size == len(content)

    with pytest.raises(OpenDVError) as exc_over:
        resolve_local_source_blob(doc, allowed_roots=[ws], max_bytes=len(content) - 1)
    assert exc_over.value.error_code == ErrorCode.ARCHIVE_SIZE_LIMIT_EXCEEDED
    assert exc_over.value.http_status == 413

    # Default 250 MiB cap triggered via fstat > 262_144_000 without allocating 250 MiB on disk
    monkeypatch.setattr(
        "opendv.service.parsing_svc.os.fstat",
        lambda _fd: types.SimpleNamespace(
            st_mode=stat.S_IFREG | 0o644,
            st_size=DEFAULT_MAX_SOURCE_BYTES + 1,
        ),
    )
    with pytest.raises(OpenDVError) as exc_250m:
        resolve_local_source_blob(doc, allowed_roots=[ws])
    assert exc_250m.value.error_code == ErrorCode.ARCHIVE_SIZE_LIMIT_EXCEEDED
    assert exc_250m.value.http_status == 413
    monkeypatch.undo()

    # Streaming read overflow when st_size reports 0
    monkeypatch.setattr(
        "opendv.service.parsing_svc.os.fstat",
        lambda _fd: types.SimpleNamespace(
            st_mode=stat.S_IFREG | 0o644,
            st_size=0,
        ),
    )
    with pytest.raises(OpenDVError) as exc_stream:
        resolve_local_source_blob(doc, allowed_roots=[ws], max_bytes=8)
    assert exc_stream.value.error_code == ErrorCode.ARCHIVE_SIZE_LIMIT_EXCEEDED
    assert exc_stream.value.http_status == 413
    monkeypatch.undo()

    # SourceBlob.from_bytes max_bytes enforcement -> 413
    with pytest.raises(OpenDVError) as exc_bytes_cap:
        SourceBlob.from_bytes(b"x" * 17, filename_hint="over.txt", max_bytes=16)
    assert exc_bytes_cap.value.error_code == ErrorCode.ARCHIVE_SIZE_LIMIT_EXCEEDED
    assert exc_bytes_cap.value.http_status == 413

    # WinError 32-safe closed spool handle: readable & unlinkable inside context, cleaned on exit
    spool_path: Path
    with SourceBlob.from_bytes(content, filename_hint="spool.pdf") as spooled:
        spool_path = spooled.path
        assert spooled.is_spooled_temp is True
        assert spooled.uri_sanitized == "bytes://spool.pdf"
        assert spooled.binary_sha256 == hashlib.sha256(content).hexdigest()
        assert spool_path.suffix == ".pdf"
        # Open and read again on Windows to confirm no exclusive lock / WinError 32 remains
        with spool_path.open("rb") as reopened:
            assert reopened.read() == content
    assert not spool_path.exists()

    # __exit__ also cleans up when an exception occurs inside the with-block
    err_spool_path: Path
    with (
        pytest.raises(RuntimeError, match="boom"),
        SourceBlob.from_bytes(content, filename_hint="err.txt") as spooled_err,
    ):
        err_spool_path = spooled_err.path
        assert err_spool_path.exists()
        raise RuntimeError("boom")
    assert not err_spool_path.exists()

    # Partial-write failure during NamedTemporaryFile unlinks the temp file cleanly
    partial_path = tmp_path / "partial_spool.pdf"

    class _FailingTempFile:
        def __init__(self, target: Path) -> None:
            self.name = str(target)
            self._fh = target.open("wb")

        def __enter__(self) -> _FailingTempFile:
            return self

        def write(self, data: bytes) -> int:
            self._fh.write(data[:4])
            raise OSError("Simulated disk full mid-write")

        def flush(self) -> None:
            self._fh.flush()

        def __exit__(self, *_args: object) -> None:
            self._fh.close()

    monkeypatch.setattr(
        tempfile,
        "NamedTemporaryFile",
        lambda **_kw: _FailingTempFile(partial_path),
    )
    with pytest.raises(OpenDVError) as exc_partial:
        SourceBlob.from_bytes(content, filename_hint="partial.pdf")
    assert exc_partial.value.error_code == ErrorCode.INTERNAL_SERVER_ERROR
    assert not partial_path.exists()
