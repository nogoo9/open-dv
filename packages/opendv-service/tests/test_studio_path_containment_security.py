"""Security regression tests for Studio path containment and canonical session lock IDs."""

from __future__ import annotations

import contextlib
import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from opendv_core.genesis import create_genesis_ontology
from opendv_service.studio.connector_ingestion import _persist_connector_evidence
from opendv_service.studio.delta_normalizer import (
    StudioWorkflowError,
    load_session_version_ontology,
    load_version_ontology_fail_closed,
    resolve_session_version_dir_safe,
    validate_version_syntax,
)
from opendv_service.studio.proposal_helpers import (
    ensure_session_version_exists,
    resolve_proposal_file_safe,
)
from opendv_service.studio.session_manager import (
    InvalidSessionIdentifierError,
    StudioSessionManager,
)


def _try_symlink(link_path: Path, target_path: Path) -> bool:
    link_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.symlink(target_path, link_path, target_is_directory=target_path.is_dir())
        return True
    except OSError:
        return False


def test_version_ontology_loaders_reject_symlink_escape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Version ontology loaders and existence checks block symlink escapes."""
    mgr = StudioSessionManager(studio_root=tmp_path / "studio")
    meta = mgr.create_session(session_id="sess-ver-sec", domain_namespace="wealth_mgmt")
    sdir = mgr.get_session_dir("sess-ver-sec")

    outside_dir = tmp_path / "outside_ver"
    outside_dir.mkdir(parents=True, exist_ok=True)
    outside_ont = create_genesis_ontology("evil_outside")
    (outside_dir / "ontology.json").write_text(
        json.dumps(outside_ont.model_dump(mode="json")), encoding="utf-8"
    )

    escaped_vdir = sdir / "versions" / "v9.9.9"
    has_symlink = _try_symlink(escaped_vdir, outside_dir)
    if not has_symlink:
        escaped_vdir.mkdir(parents=True, exist_ok=True)
        (escaped_vdir / "ontology.json").write_text(
            json.dumps(outside_ont.model_dump(mode="json")), encoding="utf-8"
        )
        orig_realpath = os.path.realpath

        def _fake_realpath(p: str | os.PathLike[str], *, strict: bool = False) -> str:
            s = str(p)
            if "v9.9.9" in s:
                return str(outside_dir)
            return orig_realpath(p, strict=strict)

        monkeypatch.setattr(os.path, "realpath", _fake_realpath)

    with pytest.raises(StudioWorkflowError) as exc1:
        load_session_version_ontology(sdir, meta, "9.9.9")
    assert exc1.value.code == "INVALID_WORKSPACE_PATH"

    with pytest.raises(StudioWorkflowError) as exc2:
        load_version_ontology_fail_closed(sdir, meta, "9.9.9")
    assert exc2.value.code in ("INVALID_WORKSPACE_PATH", "CORRUPT_SESSION_RECORD")

    with pytest.raises(StudioWorkflowError) as exc3:
        ensure_session_version_exists(sdir, "9.9.9")
    assert exc3.value.code == "INVALID_WORKSPACE_PATH"


def test_resolve_proposal_file_safe_blocks_invalid_syntax_and_symlink_escape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """resolve_proposal_file_safe rejects invalid proposal IDs and symlink escapes."""
    mgr = StudioSessionManager(studio_root=tmp_path / "studio")
    mgr.create_session(session_id="sess-prop-sec", domain_namespace="wealth_mgmt")
    sdir = mgr.get_session_dir("sess-prop-sec")

    for bad_id in ("../evil", "a/b", "CON", "", "p1\n"):
        with pytest.raises(StudioWorkflowError) as exc_bad:
            resolve_proposal_file_safe(sdir, bad_id)
        assert exc_bad.value.code == "SYNTAX_INVALID_ARGUMENT"

    outside_file = tmp_path / "outside.json"
    outside_file.write_text('{"secret": true}', encoding="utf-8")
    link_file = sdir / "proposals" / "prop-link.json"
    if not _try_symlink(link_file, outside_file):
        link_file.write_text('{"secret": true}', encoding="utf-8")
        orig_realpath = os.path.realpath

        def _fake_realpath(p: str | os.PathLike[str], *, strict: bool = False) -> str:
            s = str(p)
            if "prop-link.json" in s:
                return str(outside_file)
            return orig_realpath(p, strict=strict)

        monkeypatch.setattr(os.path, "realpath", _fake_realpath)

    with pytest.raises(StudioWorkflowError) as exc_link:
        resolve_proposal_file_safe(sdir, "prop-link")
    assert exc_link.value.code == "INVALID_WORKSPACE_PATH"


def test_clone_session_scratch_containment_and_canonical_lock_session_ids(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """clone_session blocks scratch escapes and callers pass canonical sdir.name to locks."""
    studio_root = tmp_path / "studio"
    mgr = StudioSessionManager(studio_root=studio_root)
    mgr.create_session(session_id="src-clean", domain_namespace="wealth_mgmt")
    sdir = mgr.get_session_dir("src-clean")

    # 1. Verify scratch path escape check in clone_session
    outside_scratch = tmp_path / "outside_scratch"
    outside_scratch.mkdir(parents=True, exist_ok=True)
    orig_realpath = os.path.realpath

    def _fake_realpath(p: str | os.PathLike[str], *, strict: bool = False) -> str:
        s = str(p)
        if ".staging_clone_" in s:
            return str(outside_scratch / Path(s).name)
        return orig_realpath(p, strict=strict)

    with monkeypatch.context() as mp:
        mp.setattr(os.path, "realpath", _fake_realpath)
        with pytest.raises(InvalidSessionIdentifierError):
            mgr.clone_session("src-clean", "clone-escape", actor_role="editor")

    # 2. Spy on reentrant_session_lock to confirm canonical sdir.name is passed
    recorded_sids: list[str | None] = []
    from opendv_service.studio import connector_ingestion as ci_mod
    from opendv_service.studio import session_manager as sm_mod

    orig_lock = sm_mod.reentrant_session_lock

    @contextlib.contextmanager
    def _spy_lock(lock_path: Path, *, session_id: str | None = None, **kwargs):
        recorded_sids.append(session_id)
        with orig_lock(lock_path, session_id=session_id, **kwargs) as guard:
            yield guard

    with (
        patch.object(sm_mod, "reentrant_session_lock", side_effect=_spy_lock),
        patch.object(ci_mod, "reentrant_session_lock", side_effect=_spy_lock),
    ):
        mgr.add_text_source("src-clean", text="Bond maturity is 10Y.", source_name="bond.txt")
        _persist_connector_evidence(
            session_mgr=mgr,
            session_id="src-clean",
            source_id="src-conn-1",
            source_filename="custody.csv",
            source_sha256="a" * 64,
            size_bytes=12,
            mime_type="text/csv",
            adapter="csv",
            parsed_tables=[],
            principal="human:steward",
        )
        mgr.clone_session("src-clean", "clone-ok", actor_role="editor")
        mgr.delete_session("clone-ok")

    assert len(recorded_sids) >= 4
    assert all(sid in (sdir.name, "clone-ok") for sid in recorded_sids), recorded_sids


def test_resolve_session_version_dir_safe_rejects_nul_byte(tmp_path: Path) -> None:
    """resolve_session_version_dir_safe explicitly rejects NUL bytes in version strings."""
    mgr = StudioSessionManager(studio_root=tmp_path / "studio")
    mgr.create_session(session_id="sess-ver-nul", domain_namespace="wealth_mgmt")
    sdir = mgr.get_session_dir("sess-ver-nul")

    with pytest.raises(StudioWorkflowError) as exc_nul_ver:
        resolve_session_version_dir_safe(sdir, "0.0.0\x00.json")
    assert exc_nul_ver.value.code == "INVALID_WORKSPACE_PATH"


def test_validate_version_syntax_rejects_trailing_newline() -> None:
    """validate_version_syntax uses \\A...\\Z anchoring and rejects trailing newlines."""
    validate_version_syntax("1.0.0")
    for bad_ver in ("1.0.0\n", "v1.0.0\r\n"):
        with pytest.raises(StudioWorkflowError) as exc_ver:
            validate_version_syntax(bad_ver)
        assert exc_ver.value.code == "SYNTAX_INVALID_ARGUMENT"
