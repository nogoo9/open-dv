"""TDD tests verifying fail-closed audit ledger checks precede source/workflow state mutations."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from opendv_contracts.dtos.starter_packs import AssetManagementPackId
from opendv_contracts.errors import ErrorCode
from opendv_service.studio.session_manager import (
    StudioSessionManager,
    TamperEvidentChainCorruptError,
)
from opendv_service.studio.session_source_workflows import (
    apply_starter_pack_workflow,
    persist_uploaded_source_sync,
    submit_clarification_answers_workflow,
)


def _rotate_workspace_secret(studio_root: Path) -> StudioSessionManager:
    """Replace `.odv_workspace_secret` so prior session audit HMACs fail verification."""
    secret_path = studio_root / ".odv_workspace_secret"
    old_secret = secret_path.read_bytes()
    new_secret = bytes((b ^ 0xFF) for b in old_secret)
    secret_path.write_bytes(new_secret)
    return StudioSessionManager(studio_root=studio_root)


def test_persist_uploaded_source_sync_confidential_fails_closed_without_partial_mutation(
    tmp_path: Path,
) -> None:
    """When audit.jsonl fails HMAC verification during do_not_send_to_agent=True upload,
    TamperEvidentChainCorruptError (422) must be raised with ZERO mutations to
    inputs/manifest.json, taint_index.json, session.json, audit.jsonl, or CAS blobs.
    """
    studio_root = tmp_path / "studio"
    mgr = StudioSessionManager(studio_root=studio_root)
    sid = "sess-atomicity-upload"
    mgr.create_session(sid, domain_namespace="asset_management")
    sdir = mgr.get_session_dir(sid)

    # First upload a valid non-confidential file so inputs/manifest.json already exists
    clean_blob = tmp_path / "clean.csv"
    clean_blob.write_bytes(b"account_id,balance\nA1,100\n")
    persist_uploaded_source_sync(
        session_mgr=mgr,
        session_id=sid,
        tmp_blob_path=clean_blob,
        size_bytes=clean_blob.stat().st_size,
        filename="clean.csv",
        mime_type="text/csv",
        do_not_send_to_agent=False,
        description="Clean baseline",
        principal_id="human:steward",
        is_automated=False,
    )

    manifest_path = sdir / "inputs" / "manifest.json"
    taint_path = sdir / "taint_index.json"
    session_json_path = sdir / "session.json"
    audit_path = sdir / "audit.jsonl"

    before_manifest = manifest_path.read_bytes()
    before_session = session_json_path.read_bytes()
    before_audit = audit_path.read_bytes()
    before_blobs = sorted(mgr.get_cas(sid).list_blobs())
    assert not taint_path.exists()
    assert json.loads(before_session)["taint_classification"] == "UNTAINTED"

    # Rotate workspace secret -> existing audit.jsonl fails HMAC verification
    rotated_mgr = _rotate_workspace_secret(studio_root)

    mnpi_blob = tmp_path / "ds06_confidential_mnpi.txt"
    mnpi_blob.write_bytes(b"CONFIDENTIAL MNPI BOARD RESTRUCTURING MEMO\n")

    with pytest.raises(TamperEvidentChainCorruptError) as exc_info:
        persist_uploaded_source_sync(
            session_mgr=rotated_mgr,
            session_id=sid,
            tmp_blob_path=mnpi_blob,
            size_bytes=mnpi_blob.stat().st_size,
            filename="ds06_confidential_mnpi.txt",
            mime_type="text/plain",
            do_not_send_to_agent=True,
            description="MNPI test",
            principal_id="human:steward",
            is_automated=False,
        )

    assert exc_info.value.status_code == 422
    assert exc_info.value.error_code == ErrorCode.TAMPER_EVIDENT_CHAIN_CORRUPT

    # Strictly zero partial mutations on disk
    assert manifest_path.read_bytes() == before_manifest
    assert not taint_path.exists()
    assert session_json_path.read_bytes() == before_session
    loaded_session = json.loads(session_json_path.read_text(encoding="utf-8"))
    assert loaded_session["taint_classification"] == "UNTAINTED"
    assert audit_path.read_bytes() == before_audit
    assert sorted(rotated_mgr.get_cas(sid).list_blobs()) == before_blobs


def test_persist_uploaded_source_sync_rollback_if_audit_append_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If append_signed_audit_record fails unexpectedly after pre-flight verification,
    manifest.json, taint_index.json, and session.json remain consistent with audit.jsonl.
    """
    studio_root = tmp_path / "studio"
    mgr = StudioSessionManager(studio_root=studio_root)
    sid = "sess-atomicity-rollback"
    mgr.create_session(sid, domain_namespace="asset_management")
    sdir = mgr.get_session_dir(sid)

    manifest_path = sdir / "inputs" / "manifest.json"
    taint_path = sdir / "taint_index.json"
    session_json_path = sdir / "session.json"
    audit_path = sdir / "audit.jsonl"

    before_session = session_json_path.read_bytes()
    before_audit = audit_path.read_bytes()

    def _boom(*_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise OSError("Simulated disk failure during audit append")

    monkeypatch.setattr(
        "opendv_service.studio.session_source_workflows.append_signed_audit_record",
        _boom,
    )

    mnpi_blob = tmp_path / "secret.txt"
    mnpi_blob.write_bytes(b"TOP SECRET\n")

    with pytest.raises(OSError, match="Simulated disk failure"):
        persist_uploaded_source_sync(
            session_mgr=mgr,
            session_id=sid,
            tmp_blob_path=mnpi_blob,
            size_bytes=mnpi_blob.stat().st_size,
            filename="secret.txt",
            mime_type="text/plain",
            do_not_send_to_agent=True,
            description=None,
            principal_id="human:steward",
            is_automated=False,
        )

    assert not manifest_path.exists()
    assert not taint_path.exists()
    assert session_json_path.read_bytes() == before_session
    assert audit_path.read_bytes() == before_audit


def test_persist_uploaded_source_sync_confidential_succeeds_on_valid_audit_chain(
    tmp_path: Path,
) -> None:
    """Valid audit ledger permits do_not_send_to_agent=True upload and updates all metadata."""
    studio_root = tmp_path / "studio"
    mgr = StudioSessionManager(studio_root=studio_root)
    sid = "sess-valid-mnpi"
    mgr.create_session(sid, domain_namespace="asset_management")
    sdir = mgr.get_session_dir(sid)

    mnpi_blob = tmp_path / "mnpi.txt"
    mnpi_blob.write_bytes(b"Confidential custody deal memo\n")

    res = persist_uploaded_source_sync(
        session_mgr=mgr,
        session_id=sid,
        tmp_blob_path=mnpi_blob,
        size_bytes=mnpi_blob.stat().st_size,
        filename="mnpi.txt",
        mime_type="text/plain",
        do_not_send_to_agent=True,
        description="MNPI memo",
        principal_id="human:steward",
        is_automated=False,
    )

    assert res["do_not_send_to_agent"] is True
    assert (sdir / "inputs" / "manifest.json").is_file()
    assert json.loads((sdir / "taint_index.json").read_text(encoding="utf-8"))["is_tainted"] is True
    assert mgr.get_session(sid).taint_classification == "TAINTED"
    assert mgr.verify_audit_chain(sid) is True
    audit_actions = [
        json.loads(line)["action"]
        for line in (sdir / "audit.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert "CONFIDENTIAL_SOURCE_REGISTERED" in audit_actions


def test_add_text_source_tampered_audit_fails_closed_without_partial_mutation(
    tmp_path: Path,
) -> None:
    """Sibling `add_text_source` must fail closed before mutating CAS, manifest, or session."""
    studio_root = tmp_path / "studio"
    mgr = StudioSessionManager(studio_root=studio_root)
    sid = "sess-text-atomicity"
    mgr.create_session(sid, domain_namespace="asset_management")
    sdir = mgr.get_session_dir(sid)

    before_session = (sdir / "session.json").read_bytes()
    before_audit = (sdir / "audit.jsonl").read_bytes()
    rotated_mgr = _rotate_workspace_secret(studio_root)

    with pytest.raises(TamperEvidentChainCorruptError):
        rotated_mgr.add_text_source(
            sid,
            text="Confidential analyst note",
            source_name="analyst_note.md",
            do_not_send_to_agent=True,
            principal="human:steward",
        )

    assert not (sdir / "inputs" / "manifest.json").exists()
    assert not (sdir / "taint_index.json").exists()
    assert (sdir / "session.json").read_bytes() == before_session
    assert (sdir / "audit.jsonl").read_bytes() == before_audit
    assert rotated_mgr.get_cas(sid).list_blobs() == []


def test_clarification_and_starter_pack_tampered_audit_fail_closed_without_mutation(
    tmp_path: Path,
) -> None:
    """Sibling workflows (`submit_clarification_answers_workflow`, `apply_starter_pack_workflow`)
    must verify audit ledger before mutating answers.json, versions/, session.json, or CAS.
    """
    studio_root = tmp_path / "studio"
    mgr = StudioSessionManager(studio_root=studio_root)
    sid = "sess-sibling-atomicity"
    mgr.create_session(sid, domain_namespace="asset_management")
    sdir = mgr.get_session_dir(sid)

    before_session = (sdir / "session.json").read_bytes()
    before_audit = (sdir / "audit.jsonl").read_bytes()
    before_versions = sorted(p.name for p in (sdir / "versions").iterdir())
    rotated_mgr = _rotate_workspace_secret(studio_root)
    meta = rotated_mgr.get_session(sid)

    from opendv_service.construction.clarification_harness import EMPTY_ANSWERS_SHA256

    with pytest.raises(TamperEvidentChainCorruptError):
        submit_clarification_answers_workflow(
            session_mgr=rotated_mgr,
            sdir=sdir,
            session_id=sid,
            raw_answers=[{"question_id": "q-1", "user_text": "Use trade_date"}],
            expected_answers_sha256=EMPTY_ANSWERS_SHA256,
            principal_id="human:steward",
        )

    assert not (sdir / "clarifications" / "answers.json").exists()
    assert rotated_mgr.get_cas(sid).list_blobs() == []

    with pytest.raises(TamperEvidentChainCorruptError):
        apply_starter_pack_workflow(
            session_mgr=rotated_mgr,
            meta=meta,
            sdir=sdir,
            pack_ids=[AssetManagementPackId.CORE_SPINE],
            base_version=None,
            domain_namespace=None,
            expected_sha=meta.head_sha256,
            principal_id="human:steward",
        )

    assert sorted(p.name for p in (sdir / "versions").iterdir()) == before_versions
    assert (sdir / "session.json").read_bytes() == before_session
    assert (sdir / "audit.jsonl").read_bytes() == before_audit
