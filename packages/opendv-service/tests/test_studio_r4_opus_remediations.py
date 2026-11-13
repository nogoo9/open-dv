"""Comprehensive regression tests for Round-4 Claude CLI Opus 5.5 Studio MAJOR remediations.

Covers all 9 R4-B Studio findings across `locks.py`, `session_manager.py`, `atomic_commit.py`,
`bundle.py`, and `job_runners.py`:
- `P07b::MAJ-01` & `P08a2::MAJ-02`:
  - POSIX post-`flock` `(st_dev, st_ino)` inode verification & retry on unlink/replace race.
  - `.lifecycle_<sid>.lock` `LockLevel.L5` inference and deterministic `resource_id` ordering
    (`0_lifecycle` < `1_session`) preserving multi-session order (`(5, s1, ...) < (5, s2, ...)`).
  - `reentrant_session_lock` acquiring `.lifecycle_<sid>.lock` (`remove_on_release=False`) prior to
    `.session.lock` with reentrancy and deletion race guard.
  - `import_bundle` Step 9 and `delete_session` holding `.lifecycle_<sid>.lock`
    (`remove_on_release=False`) across swap/delete.
  - `StudioSessionManager.clone_session` RBAC, audit verification, taint admin gate, artifact
    filtering, fresh `.session_secret`, `SESSION_CLONED` genesis audit record, and foreign anchor.
- `P08a1::MAJ-01`:
  - Write-ahead `audit.jsonl` roll-forward reconciliation under L5 lock and in `verify_audit_chain`
    (torn trailing fragment truncation + single `K_audit`-signed uncommitted record roll-forward).
- `P07a1::MAJ-01`:
  - Removal of `run_governance` / `enforce_governance` bypasses so governance runs unconditionally.
- `P07a1::MAJ-02`:
  - Pre-normalization `submitted_delta_sha` waiver validation (`hmac.compare_digest`), persistence
    in `meta.json`, and downstream verification in `_collect_locally_waived_session_concepts` &
    `bundle.py`.
- `P07a2::MAJ-01`:
  - SemVer sorting (`1.10.0 > 1.9.0`) in `reconcile_and_recover_session` Step 4 and quarantine of
    unauthenticated or `COMMIT_ABORTED` forward `versions/v*` directories.
- `P08a2::MAJ-01`:
  - Genuine `create_genesis_ontology` digest enforcement for `v0.0.0` / `mode="genesis"` roots and
    acknowledgement requirement for non-CBC `mode="seed"` roots (`first_sha != genesis_sha`).
- `P08b::MAJ-01`, `P08b::MAJ-02`, `P08b::MAJ-03`:
  - Monotonic sticky taint via `_max_taint` at parse & load time + `_prepare_evidence_for_agent`
    table `sensitive_terms` union.
  - Strict `X.Y.Z` SemVer regex & `versions/` containment check in `run_construct_session` and
    `_load_session_version_ontology`.
  - Tombstoned SHA filtering even when `source_id` is active + L5 re-parse purge of prior source
    chunks and tables in `run_parse_session_sources`.
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import types
import uuid
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

import opendv_service.studio.locks as locks_mod
from opendv_contracts.dtos.ingestion import (
    EvidenceChunk,
    IngestionResult,
    TableStructure,
    TaintClassification,
)
from opendv_contracts.dtos.ontology import (
    AddConceptOp,
    OntologyDelta,
    StewardWaiverRecord,
)
from opendv_contracts.errors import ErrorCode, OpenDVError
from opendv_core.models.graph_a import (
    CanonicalBusinessConcept,
    ExternalMapping,
    GraphAOntology,
    ReferenceEnumeration,
)
from opendv_core.ontology_hashing import compute_ontology_sha256, dumps_jcs_bytes
from opendv_service.ontology.validator import UngroundedConceptError
from opendv_service.studio.atomic_commit import (
    AtomicCommitService,
    TamperEvidentChainCorruptError,
)
from opendv_service.studio.bundle import (
    ImportDeltaReplayHashMismatchError,
    ImportGroundingViolationError,
    export_bundle,
    import_bundle,
)
from opendv_service.studio.job_runners import (
    _load_session_version_ontology,
    _max_taint,
    _prepare_evidence_for_agent,
    load_active_session_evidence,
    run_construct_session,
    run_parse_session_sources,
)
from opendv_service.studio.locks import (
    L5_SESSION,
    LockLevel,
    LockOrderViolationError,
    StudioLock,
    get_held_locks,
    infer_lock_level,
    reentrant_session_lock,
    studio_lock,
)
from opendv_service.studio.session_manager import (
    SessionAlreadyExistsError,
    StudioSessionManager,
    append_signed_audit_record,
)
from opendv_service.studio.workspace_security import WorkspaceSecurityManager
from opendv_sources.ingestion.adapter import DocumentAdapter
from opendv_sources.ingestion.service import IngestionService

FIBO_PARTY_URI = "https://spec.edmcouncil.org/fibo/ontology/FND/Agreements/Contracts/ContractParty"
FIBO_ACCOUNT_URI = (
    "https://spec.edmcouncil.org/fibo/ontology/FBC/ProductsAndServices/ClientsAndAccounts/Account"
)


def _fibo_mapping(uri: str = FIBO_ACCOUNT_URI) -> ExternalMapping:
    return ExternalMapping(
        scheme="fibo",
        target_uri=uri,
        predicate="skos:exactMatch",
        confidence=0.95,
    )


# ==============================================================================
# 1. P07b::MAJ-01 & P08a2::MAJ-02 — Locks, Swap Safety, Delete & Clone Session
# ==============================================================================


def test_p07b_maj01_posix_flock_inode_mismatch_and_unlink_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Simulated POSIX flock retries cleanly on (st_dev, st_ino) mismatch or FileNotFoundError."""
    lock_path = tmp_path / ".lifecycle_s-posix.lock"

    flock_ops: list[tuple[int, int]] = []
    fake_fcntl = types.SimpleNamespace(
        LOCK_EX=2,
        LOCK_NB=4,
        LOCK_UN=8,
        flock=lambda fd, op: flock_ops.append((fd, op)),
    )
    fake_sys = types.SimpleNamespace(platform="linux")
    monkeypatch.setattr(locks_mod, "sys", fake_sys)
    monkeypatch.setattr(locks_mod, "fcntl", fake_fcntl, raising=False)

    real_stat = os.stat
    stat_calls = 0

    def _patched_stat(p: Any, *args: Any, **kwargs: Any) -> Any:
        nonlocal stat_calls
        if Path(p).resolve() == lock_path.resolve():
            stat_calls += 1
            if stat_calls == 1:
                raise FileNotFoundError("Simulated unlink between open and flock")
            if stat_calls == 2:
                st = real_stat(p, *args, **kwargs)
                return types.SimpleNamespace(st_dev=st.st_dev + 999, st_ino=st.st_ino + 999)
        return real_stat(p, *args, **kwargs)

    # Patch os.stat called inside StudioLock._acquire_lowlevel_lock while leaving Path.exists intact
    monkeypatch.setattr(locks_mod.os, "stat", _patched_stat)

    lk = StudioLock(lock_path, timeout=2.0)
    lk.acquire()
    try:
        assert lk.is_locked
        # Two aborted attempts unlocked (LOCK_UN=8) before the third succeeded on matching inode
        unlock_events = [op for _, op in flock_ops if op == 8]
        assert len(unlock_events) == 2
        assert stat_calls >= 3
    finally:
        lk.release()


def test_p07b_maj01_lifecycle_lock_inference_and_deterministic_l5_ordering(
    tmp_path: Path,
) -> None:
    """Verify .lifecycle_<sid>.lock L5 inference and (0_lifecycle < 1_session) ordering."""
    lc_a = StudioLock(tmp_path / ".lifecycle_s-alpha.lock")
    sess_a = StudioLock(tmp_path / "s-alpha" / ".session.lock")
    lc_b = StudioLock(tmp_path / ".lifecycle_s-beta.lock")
    sess_b = StudioLock(tmp_path / "s-beta" / ".session.lock")

    assert infer_lock_level(tmp_path / ".lifecycle_s-alpha.lock") == LockLevel.L5
    assert lc_a.level == LockLevel.L5
    assert lc_a.session_id == "s-alpha"
    assert lc_a.resource_id == "0_lifecycle"

    assert sess_a.level == LockLevel.L5
    assert sess_a.session_id == "s-alpha"
    assert sess_a.resource_id == "1_session"

    # Intra-session order: .lifecycle_<sid>.lock strictly precedes .session.lock
    assert lc_a.order_key < sess_a.order_key
    assert lc_b.order_key < sess_b.order_key
    # Multi-session order preserved: all s-alpha locks precede all s-beta locks
    assert sess_a.order_key < lc_b.order_key

    # Acquiring .session.lock directly before .lifecycle_<sid>.lock raises LockOrderViolationError
    (tmp_path / "s-alpha").mkdir(parents=True, exist_ok=True)
    with sess_a, pytest.raises(LockOrderViolationError):
        lc_a.acquire(timeout=0.2)


def test_p07b_maj01_reentrant_session_lock_acquires_lifecycle_and_handles_missing_dir(
    tmp_path: Path,
) -> None:
    """reentrant_session_lock acquires .lifecycle_<sid>.lock first and re-verifies parent exists."""
    sdir = tmp_path / "sess-1"
    sdir.mkdir()
    sess_lock = sdir / ".session.lock"
    lc_lock = tmp_path / ".lifecycle_sess-1.lock"

    # 1. Normal nested reentrant acquisition
    with reentrant_session_lock(sess_lock, session_id="sess-1", timeout=1.0) as outer:
        held = get_held_locks()
        assert [h.path for h in held] == [lc_lock.resolve(), sess_lock.resolve()]
        with reentrant_session_lock(sess_lock, session_id="sess-1", timeout=1.0) as inner:
            assert inner is outer
            assert len(get_held_locks()) == 2
    assert get_held_locks() == ()
    # remove_on_release=False keeps .lifecycle_sess-1.lock inode stable on disk
    assert lc_lock.exists()

    # 2. Entering reentrant_session_lock when .lifecycle_<sid>.lock is ALREADY held
    with studio_lock(lc_lock, level=L5_SESSION, session_id="sess-1", remove_on_release=False):
        assert len(get_held_locks()) == 1
        with reentrant_session_lock(sess_lock, session_id="sess-1", timeout=1.0):
            assert len(get_held_locks()) == 2
        # Exiting inner reentrant_session_lock leaves outer lifecycle lock held
        assert len(get_held_locks()) == 1
    assert get_held_locks() == ()

    # 3. Missing session directory raises FileNotFoundError and releases .lifecycle lock cleanly
    missing_dir = tmp_path / "sess-deleted"
    with (
        pytest.raises(FileNotFoundError),
        reentrant_session_lock(missing_dir / ".session.lock", session_id="sess-deleted"),
    ):
        pass
    assert get_held_locks() == ()


def test_p08a2_maj02_import_force_swap_and_delete_session_hold_lifecycle_lock(
    tmp_path: Path,
) -> None:
    """import_bundle(force=True) and delete_session hold .lifecycle_<sid>.lock across swap."""
    ws_a = tmp_path / "ws_a"
    ws_b = tmp_path / "ws_b"
    sec_a = WorkspaceSecurityManager(studio_root=ws_a)
    sec_b = WorkspaceSecurityManager(studio_root=ws_b)
    mgr_a = StudioSessionManager(studio_root=ws_a, security_manager=sec_a)
    mgr_b = StudioSessionManager(studio_root=ws_b, security_manager=sec_b)
    svc_a = AtomicCommitService(session_manager=mgr_a, security_manager=sec_a)

    meta_a = mgr_a.create_session("swap-target")
    svc_a.commit_delta(
        "swap-target",
        OntologyDelta(
            delta_id="d-swap-1",
            base_ontology_version="0.0.0",
            target_ontology_version="1.0.0",
            parent_sha256=meta_a.head_sha256,
            operations=[
                AddConceptOp(
                    op_id="op-acct",
                    concept_name="Account",
                    concept_uri="urn:ontology:entity:account",
                    target_hub="hub_account",
                    external_mappings=[_fibo_mapping()],
                )
            ],
        ),
    )
    bundle_zip = tmp_path / "swap.odvstudio.zip"
    export_bundle(mgr_a.get_session_dir("swap-target"), bundle_zip, security_manager=sec_a)

    # Create pre-existing session in ws_b with same ID "swap-target" at v0.0.0
    mgr_b.create_session("swap-target")
    dest_dir = mgr_b.get_session_dir("swap-target")
    lc_path = ws_b / ".lifecycle_swap-target.lock"

    # Concurrent reader trying reentrant_session_lock while import_bundle(force=True) swaps
    entered_after_swap: list[str] = []
    ready_evt = threading.Event()

    orig_replace = WorkspaceSecurityManager  # keep ref
    import opendv_service.studio.bundle as bundle_mod

    real_replace = bundle_mod.replace_with_retry
    swap_paused = threading.Event()
    proceed_swap = threading.Event()

    def _slow_replace(src: Path, dst: Path, *args: Any, **kwargs: Any) -> None:
        if dst.resolve() == dest_dir.resolve():
            swap_paused.set()
            proceed_swap.wait(timeout=5.0)
        real_replace(src, dst, *args, **kwargs)

    def _concurrent_accessor() -> None:
        ready_evt.set()
        swap_paused.wait(timeout=5.0)
        with reentrant_session_lock(dest_dir / ".session.lock", session_id="swap-target"):
            m = json.loads((dest_dir / "session.json").read_text(encoding="utf-8"))
            entered_after_swap.append(m["head_version"])

    worker = threading.Thread(target=_concurrent_accessor)
    orig_bundle_replace = bundle_mod.replace_with_retry
    bundle_mod.replace_with_retry = _slow_replace
    try:
        worker.start()
        ready_evt.wait(timeout=2.0)

        def _release_soon() -> None:
            swap_paused.wait(timeout=5.0)
            time.sleep(0.05)
            assert entered_after_swap == []
            proceed_swap.set()

        releaser = threading.Thread(target=_release_soon)
        releaser.start()

        imp_meta = import_bundle(
            bundle_zip,
            ws_b,
            security_manager=sec_b,
            new_session_id="swap-target",
            force=True,
        )
        worker.join(timeout=5.0)
        releaser.join(timeout=5.0)
        assert imp_meta.head_version == "1.0.0"
        # Concurrent accessor could ONLY enter after swap completed and saw 1.0.0!
        assert entered_after_swap == ["1.0.0"]
        assert lc_path.exists()
    finally:
        bundle_mod.replace_with_retry = orig_bundle_replace
        _ = orig_replace

    # delete_session also retains .lifecycle_<sid>.lock (remove_on_release=False)
    mgr_b.delete_session("swap-target")
    assert not dest_dir.exists()
    assert lc_path.exists()


def test_clone_session_rbac_taint_audit_fresh_secret_and_foreign_anchor(tmp_path: Path) -> None:
    """Verify StudioSessionManager.clone_session end-to-end contracts."""
    ws = tmp_path / "studio"
    sec = WorkspaceSecurityManager(studio_root=ws)
    mgr = StudioSessionManager(studio_root=ws, security_manager=sec)
    svc = AtomicCommitService(session_manager=mgr, security_manager=sec)

    src_meta = mgr.create_session("src-clean", domain_namespace="finance")
    sdir = mgr.get_session_dir("src-clean")
    svc.commit_delta(
        "src-clean",
        OntologyDelta(
            delta_id="d-c1",
            base_ontology_version="0.0.0",
            target_ontology_version="1.0.0",
            parent_sha256=src_meta.head_sha256,
            operations=[
                AddConceptOp(
                    op_id="op-party",
                    concept_name="Party",
                    concept_uri="urn:ontology:entity:party",
                    synonyms=["Contract Party"],
                    target_hub="hub_party",
                    external_mappings=[_fibo_mapping(FIBO_PARTY_URI)],
                )
            ],
        ),
    )

    # Populate session-scoped .session_secret + artifacts that MUST be ignored vs rewritten
    (sdir / ".session_secret").write_bytes(b"\xaa" * 32)
    (sdir / "jobs").mkdir(exist_ok=True)
    (sdir / "jobs" / "job-1.json").write_text("{}", encoding="utf-8")
    (sdir / "promotion.json").write_text('{"promoted": true}', encoding="utf-8")
    (sdir / "waivers.json").write_text("[]", encoding="utf-8")
    (sdir / "audit.imported.jsonl").write_text('{"imported": 1}\n', encoding="utf-8")
    (sdir / ".scratch-temp").write_text("scratch", encoding="utf-8")
    (sdir / ".staging-temp").mkdir(exist_ok=True)
    (sdir / ".bundle.tmp").write_text("bundle", encoding="utf-8")
    (sdir / "proposals" / "prop-1.json").write_text(
        json.dumps({"proposal_id": "prop-1", "session_id": "src-clean", "status": "PENDING"}),
        encoding="utf-8",
    )

    # 1. Automated & viewer roles rejected
    with pytest.raises(OpenDVError) as auto_exc:
        mgr.clone_session("src-clean", "clone-auto", actor_role="automated")
    assert auto_exc.value.error_code == ErrorCode.AGENT_OPERATION_FORBIDDEN

    with pytest.raises(OpenDVError) as view_exc:
        mgr.clone_session("src-clean", "clone-view", actor_role="viewer")
    assert view_exc.value.error_code == ErrorCode.FORBIDDEN_ADMIN_REQUIRED

    # 2. Editor role clones clean session successfully
    cloned_meta = mgr.clone_session(
        "src-clean", "clone-clean", actor="human:alice", actor_role="editor"
    )
    cdir = mgr.get_session_dir("clone-clean")
    assert cloned_meta.session_id == "clone-clean"
    assert cloned_meta.head_version == "1.0.0"
    assert cloned_meta.promoted_versions == []
    assert cloned_meta.last_promoted_sha256 is None
    assert cloned_meta.imported_audit_anchor_sha256 is None
    assert mgr.verify_audit_chain("clone-clean") is True

    # Fresh 32-byte .session_secret distinct from source
    src_secret = (sdir / ".session_secret").read_bytes()
    clone_secret = (cdir / ".session_secret").read_bytes()
    assert len(clone_secret) == 32
    assert clone_secret != src_secret

    # Ignored artifacts excluded; proposals/*.json session_id rewritten
    assert not (cdir / "jobs" / "job-1.json").exists()
    assert not (cdir / "promotion.json").exists()
    assert not (cdir / "waivers.json").exists()
    assert not (cdir / "audit.imported.jsonl").exists()
    assert not (cdir / ".scratch-temp").exists()
    assert not (cdir / ".staging-temp").exists()
    assert not (cdir / ".bundle.tmp").exists()

    rewritten_prop = json.loads((cdir / "proposals" / "prop-1.json").read_text(encoding="utf-8"))
    assert rewritten_prop["session_id"] == "clone-clean"

    audit_lines = (cdir / "audit.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(audit_lines) == 1
    genesis_rec = json.loads(audit_lines[0])
    assert genesis_rec["action"] == "SESSION_CLONED"
    assert genesis_rec["prev_audit_sha256"] is None
    assert genesis_rec["details"] == {
        "source_session_id": "src-clean",
        "new_session_id": "clone-clean",
    }

    # Collision raises SessionAlreadyExistsError
    with pytest.raises(SessionAlreadyExistsError):
        mgr.clone_session("src-clean", "clone-clean", actor_role="editor")

    # 3. Tainted session requires admin role
    (sdir / "inputs" / "manifest.json").write_text(
        json.dumps(
            {
                "files": [
                    {
                        "source_id": "src-conf",
                        "sha256": "a" * 64,
                        "taint": "TAINTED",
                        "tombstoned": False,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(OpenDVError) as taint_exc:
        mgr.clone_session("src-clean", "clone-tainted-ed", actor_role="editor")
    assert taint_exc.value.error_code == ErrorCode.FORBIDDEN_ADMIN_REQUIRED

    tainted_clone = mgr.clone_session("src-clean", "clone-tainted-adm", actor_role="admin")
    assert tainted_clone.session_id == "clone-tainted-adm"

    # 4. Imported session anchor is recorded in SESSION_CLONED details on fresh local chain
    imp_rec = append_signed_audit_record(
        session_dir=cdir,
        action="SESSION_IMPORTED",
        actor="human:admin",
        security_manager=sec,
    )
    anchor_sha = imp_rec["audit_sha256"]
    assert mgr.get_session("clone-clean").imported_audit_anchor_sha256 == anchor_sha

    anchored_clone = mgr.clone_session("clone-clean", "clone-anchored", actor_role="editor")
    assert anchored_clone.imported_audit_anchor_sha256 is None
    assert mgr.verify_audit_chain("clone-anchored") is True
    anchored_dir = mgr.get_session_dir("clone-anchored")
    anchored_genesis = json.loads(
        (anchored_dir / "audit.jsonl").read_text(encoding="utf-8").splitlines()[0]
    )
    assert anchored_genesis["details"]["source_imported_audit_anchor_sha256"] == anchor_sha
    append_signed_audit_record(
        session_dir=anchored_dir,
        action="POST_CLONE_EVENT",
        actor="human:alice",
        security_manager=sec,
    )
    assert mgr.verify_audit_chain("clone-anchored") is True

    # 4b. Unverified attribution acknowledgement requires admin and stays sticky across hops
    append_signed_audit_record(
        session_dir=cdir,
        action="BUNDLE_IMPORTED",
        actor="human:admin",
        details={"unverified_attribution_acknowledged": True},
        security_manager=sec,
    )
    with pytest.raises(OpenDVError) as ack_ed_exc:
        mgr.clone_session("clone-clean", "clone-ack-ed", actor_role="editor")
    assert ack_ed_exc.value.error_code == ErrorCode.FORBIDDEN_ADMIN_REQUIRED

    ack_adm_clone = mgr.clone_session("clone-clean", "clone-ack-adm", actor_role="admin")
    assert ack_adm_clone.session_id == "clone-ack-adm"
    with pytest.raises(OpenDVError) as ack_hop2_exc:
        mgr.clone_session("clone-ack-adm", "clone-ack-hop2-ed", actor_role="editor")
    assert ack_hop2_exc.value.error_code == ErrorCode.FORBIDDEN_ADMIN_REQUIRED

    # 5. Corrupt source audit chain rejected
    (sdir / "audit.jsonl").write_text('{"corrupt": true}\n', encoding="utf-8")
    with pytest.raises(TamperEvidentChainCorruptError):
        mgr.clone_session("src-clean", "clone-corrupt", actor_role="admin")


# ==============================================================================
# 2. P08a1::MAJ-01 — Write-Ahead Audit Roll-Forward & Torn Fragment Repair
# ==============================================================================


def test_p08a1_maj01_audit_torn_fragment_and_single_signed_write_ahead_roll_forward(
    tmp_path: Path,
) -> None:
    """Verify torn incomplete fragment truncation and 1-record signed write-ahead roll-forward."""
    ws = tmp_path / "studio"
    sec = WorkspaceSecurityManager(studio_root=ws)
    mgr = StudioSessionManager(studio_root=ws, security_manager=sec)
    svc = AtomicCommitService(session_manager=mgr, security_manager=sec)

    meta = mgr.create_session("sess-wal")
    sdir = mgr.get_session_dir("sess-wal")
    audit_path = sdir / "audit.jsonl"

    # (a) Torn non-newline-terminated fragment after declared_head
    orig_bytes = audit_path.read_bytes()
    audit_path.write_bytes(orig_bytes + b'{"timestamp": "2026-11-13T00:00')
    assert mgr.verify_audit_chain("sess-wal") is True

    # Appending under L5 lock truncates the torn fragment and chains cleanly onto record k
    rec_after_torn = append_signed_audit_record(
        session_dir=sdir,
        action="AFTER_TORN_REPAIR",
        actor="human:steward",
        security_manager=sec,
    )
    assert rec_after_torn["prev_audit_sha256"] == meta.audit_chain_head_sha256
    assert mgr.verify_audit_chain("sess-wal") is True

    # (b) Crash between audit.jsonl fsync and _atomic_write_session_json:
    # Build a valid K_audit-signed newline-terminated record k+1 while session.json stays at k
    declared_k = mgr.get_session("sess-wal").audit_chain_head_sha256
    wal_payload: dict[str, Any] = {
        "timestamp": datetime.now(UTC).isoformat(),
        "session_id": "sess-wal",
        "actor": "human:steward",
        "action": "UNFLUSHED_HEAD_RECORD",
        "target_version": "0.0.0",
        "head_sha256": meta.head_sha256,
        "details": {"note": "fsynced to audit.jsonl before crash"},
        "prev_audit_sha256": declared_k,
    }
    canon_wal = dumps_jcs_bytes(wal_payload)
    wal_sha = hashlib.sha256(canon_wal).hexdigest().lower()
    wal_sig = sec.sign_audit_entry(canon_wal)
    wal_record = {**wal_payload, "audit_sha256": wal_sha, "hmac_signature": wal_sig}

    with audit_path.open("ab") as fp:
        fp.write(json.dumps(wal_record, separators=(",", ":")).encode("utf-8") + b"\n")

    # Read-only verify_audit_chain honors the 1-record signed write-ahead state!
    assert mgr.verify_audit_chain("sess-wal") is True
    assert mgr.get_session("sess-wal").audit_chain_head_sha256 == declared_k

    # Mutating append rolls session.json forward to wal_sha FIRST, then appends k+2 onto wal_sha!
    rec_k2 = append_signed_audit_record(
        session_dir=sdir,
        action="FOLLOWUP_AFTER_WAL",
        actor="human:steward",
        security_manager=sec,
    )
    assert rec_k2["prev_audit_sha256"] == wal_sha
    assert mgr.get_session("sess-wal").audit_chain_head_sha256 == rec_k2["audit_sha256"]
    assert len(audit_path.read_text(encoding="utf-8").splitlines()) == 4
    assert mgr.verify_audit_chain("sess-wal") is True

    # (c) Negative checks: unsigned/forged k+1 or >=2 uncommitted records MUST fail!
    base_bytes = audit_path.read_bytes()
    head_now = mgr.get_session("sess-wal").audit_chain_head_sha256

    bad_hmac_payload = {
        **wal_payload,
        "prev_audit_sha256": head_now,
    }
    bad_canon = dumps_jcs_bytes(bad_hmac_payload)
    bad_rec = {
        **bad_hmac_payload,
        "audit_sha256": hashlib.sha256(bad_canon).hexdigest().lower(),
        "hmac_signature": "0" * 64,
    }
    audit_path.write_bytes(base_bytes + json.dumps(bad_rec).encode("utf-8") + b"\n")
    assert mgr.verify_audit_chain("sess-wal") is False
    with pytest.raises(TamperEvidentChainCorruptError):
        svc.reconcile_and_recover_session("sess-wal")

    # Two valid signed uncommitted records (>=2 divergence) MUST also fail
    w1_payload = {**wal_payload, "action": "W1", "prev_audit_sha256": head_now}
    w1_canon = dumps_jcs_bytes(w1_payload)
    w1_sha = hashlib.sha256(w1_canon).hexdigest().lower()
    w1 = {**w1_payload, "audit_sha256": w1_sha, "hmac_signature": sec.sign_audit_entry(w1_canon)}

    w2_payload = {**wal_payload, "action": "W2", "prev_audit_sha256": w1_sha}
    w2_canon = dumps_jcs_bytes(w2_payload)
    w2_sha = hashlib.sha256(w2_canon).hexdigest().lower()
    w2 = {**w2_payload, "audit_sha256": w2_sha, "hmac_signature": sec.sign_audit_entry(w2_canon)}

    audit_path.write_bytes(
        base_bytes + json.dumps(w1).encode("utf-8") + b"\n" + json.dumps(w2).encode("utf-8") + b"\n"
    )
    assert mgr.verify_audit_chain("sess-wal") is False
    with pytest.raises(TamperEvidentChainCorruptError):
        append_signed_audit_record(
            session_dir=sdir,
            action="REJECT_MULTI_WAL",
            actor="human:steward",
            security_manager=sec,
        )


# ==============================================================================
# 3. P07a1::MAJ-01, P07a1::MAJ-02, P07a2::MAJ-01 — AtomicCommitService
# ==============================================================================


def test_p07a1_maj01_governance_bypass_parameters_removed(tmp_path: Path) -> None:
    """enforce_governance and run_governance parameters/properties no longer exist."""
    ws = tmp_path / "studio"
    sec = WorkspaceSecurityManager(studio_root=ws)
    mgr = StudioSessionManager(studio_root=ws, security_manager=sec)
    svc = AtomicCommitService(session_manager=mgr, security_manager=sec)

    assert not hasattr(svc, "enforce_governance")
    with pytest.raises(TypeError):
        AtomicCommitService(  # type: ignore[call-arg]
            session_manager=mgr,
            security_manager=sec,
            enforce_governance=False,
        )

    meta = mgr.create_session("sess-no-bypass")
    ungrounded = OntologyDelta(
        delta_id="d-ug",
        base_ontology_version="0.0.0",
        target_ontology_version="1.0.0",
        parent_sha256=meta.head_sha256,
        operations=[
            AddConceptOp(
                op_id="op-ug",
                concept_name="RawUnmapped",
                concept_uri="urn:ontology:entity:raw_unmapped",
                target_hub="hub_raw_unmapped",
            )
        ],
    )
    with pytest.raises(TypeError):
        svc.commit_delta("sess-no-bypass", ungrounded, enforce_governance=False)  # type: ignore[call-arg]
    with pytest.raises(TypeError):
        svc.commit_delta("sess-no-bypass", ungrounded, run_governance=False)  # type: ignore[call-arg]
    with pytest.raises(UngroundedConceptError):
        svc.commit_delta("sess-no-bypass", ungrounded)


def test_p07a1_maj02_submitted_delta_sha_waiver_verification_and_portability(
    tmp_path: Path,
) -> None:
    """Waivers signed over pre-normalization submitted_delta_sha succeed across commit & bundle."""
    ws_a = tmp_path / "ws_a"
    ws_b = tmp_path / "ws_b"
    sec_a = WorkspaceSecurityManager(studio_root=ws_a)
    sec_b = WorkspaceSecurityManager(studio_root=ws_b)
    mgr_a = StudioSessionManager(studio_root=ws_a, security_manager=sec_a)
    svc_a = AtomicCommitService(session_manager=mgr_a, security_manager=sec_a)

    meta = mgr_a.create_session("sess-sub-sha")
    # Construct base_sha256-anchored delta WITHOUT target_ontology_version so normalization mutates
    # canonical_sha256() when populating target_ontology_version='1.0.0'!
    submitted_delta = OntologyDelta(
        delta_id="d-sub-waiver",
        base_ontology_version="0.0.0",
        target_ontology_version=None,
        parent_sha256="",
        operations=[
            AddConceptOp(
                op_id="op-custom",
                concept_name="AlphaDeskMetric",
                concept_uri="urn:ontology:entity:alpha_desk_metric",
                target_hub="hub_alpha_desk_metric",
            )
        ],
    )
    submitted_sha = submitted_delta.canonical_sha256()
    normalized_probe = submitted_delta.model_copy(
        update={"parent_sha256": meta.head_sha256, "target_ontology_version": "1.0.0"}
    )
    assert submitted_sha != normalized_probe.canonical_sha256()

    wv_unsigned = StewardWaiverRecord(
        proposal_id=uuid.uuid4(),
        target_concept_uris=["urn:ontology:entity:alpha_desk_metric"],
        proposal_delta_sha256=submitted_sha,
        base_sha256=meta.head_sha256,
        authorized_by="human:chief_steward",
        authorized_role="admin",
        rationale="Approved internal desk metric signed against pre-normalized delta SHA.",
        created_at=datetime.now(UTC) - timedelta(minutes=1),
        signature_or_token="0" * 64,
    )
    wv_signed = wv_unsigned.model_copy(
        update={
            "signature_or_token": sec_a.sign_waiver(wv_unsigned.signing_payload().encode("utf-8"))
        }
    )

    r1 = svc_a.commit_delta(
        "sess-sub-sha",
        submitted_delta,
        if_match=f'"{meta.head_sha256}"',
        waivers=[wv_signed],
    )
    assert r1.version == "v1.0.0"

    sdir = mgr_a.get_session_dir("sess-sub-sha")
    v1_meta = json.loads((sdir / "versions" / "v1.0.0" / "meta.json").read_text(encoding="utf-8"))
    assert v1_meta["submitted_delta_sha256"] == submitted_sha
    assert v1_meta["delta_sha256"] == normalized_probe.canonical_sha256()

    # Subsequent commit v1.1.0 touching an unrelated grounded concept preserves local waiver!
    r2 = svc_a.commit_delta(
        "sess-sub-sha",
        OntologyDelta(
            delta_id="d-v110",
            base_ontology_version="1.0.0",
            target_ontology_version="1.1.0",
            parent_sha256=r1.target_sha256,
            operations=[
                AddConceptOp(
                    op_id="op-acct",
                    concept_name="Account",
                    concept_uri="urn:ontology:entity:account",
                    target_hub="hub_account",
                    external_mappings=[_fibo_mapping()],
                )
            ],
        ),
    )
    assert r2.version == "v1.1.0"

    # Export and import into Workspace B -> bundle.py honors submitted_delta_sha256
    b_zip = tmp_path / "sub_sha.odvstudio.zip"
    export_bundle(sdir, b_zip, security_manager=sec_a)
    imp_meta = import_bundle(
        b_zip,
        ws_b,
        security_manager=sec_b,
        new_session_id="imp-sub-sha",
        actor_role="admin",
        acknowledge_unverified_import_attribution=True,
        import_rationale="Importing session with pre-normalized waiver delta digest.",
    )
    assert imp_meta.head_version == "1.1.0"


def test_p07a2_maj01_semver_recovery_sorting_and_pending_commit_authentication(
    tmp_path: Path,
) -> None:
    """Recovery sorts 1.10.0 > 1.9.0 via SemVer and quarantines unauthenticated/aborted dirs."""
    ws = tmp_path / "studio"
    sec = WorkspaceSecurityManager(studio_root=ws)
    mgr = StudioSessionManager(studio_root=ws, security_manager=sec)
    svc = AtomicCommitService(session_manager=mgr, security_manager=sec)

    # Seed session directly at 1.9.0 so next additive commit is 1.10.0 (lexicographically < 1.9.0!)
    ont_190 = GraphAOntology(
        ontology_version="1.9.0",
        domain_namespace="finance",
        enterprise_owner="Enterprise Architecture",
        canonical_business_concepts=[
            CanonicalBusinessConcept(
                concept_name="Party",
                concept_uri="urn:ontology:entity:party",
                target_hub="hub_party",
                external_mappings=[_fibo_mapping(FIBO_PARTY_URI)],
            )
        ],
    )
    meta_190 = mgr.create_session("sess-semver-rec", initial_ontology=ont_190)
    sdir = mgr.get_session_dir("sess-semver-rec")
    assert meta_190.head_version == "1.9.0"

    # Stage v1.10.0 WITH authenticated PENDING_COMMIT
    ont_1100 = GraphAOntology(
        ontology_version="1.10.0",
        domain_namespace="finance",
        enterprise_owner="Enterprise Architecture",
        parent_sha256=meta_190.head_sha256,
        canonical_business_concepts=[
            *ont_190.canonical_business_concepts,
            CanonicalBusinessConcept(
                concept_name="Account",
                concept_uri="urn:ontology:entity:account",
                target_hub="hub_account",
                external_mappings=[_fibo_mapping(FIBO_ACCOUNT_URI)],
            ),
        ],
    )
    sha_1100 = compute_ontology_sha256(ont_1100)
    v1100_dir = sdir / "versions" / "v1.10.0"
    v1100_dir.mkdir(parents=True, exist_ok=True)
    (v1100_dir / "ontology.json").write_bytes(
        dumps_jcs_bytes(ont_1100.model_dump(mode="json", exclude_none=True))
    )
    (v1100_dir / "meta.json").write_text(
        json.dumps(
            {
                "target_version": "v1.10.0",
                "target_sha256": sha_1100,
                "parent_sha256": meta_190.head_sha256,
                "proposal_id": None,
            }
        ),
        encoding="utf-8",
    )
    append_signed_audit_record(
        session_dir=sdir,
        action="PENDING_COMMIT",
        actor="human:steward",
        target_version="v1.10.0",
        head_sha256=sha_1100,
        details={
            "target_version": "v1.10.0",
            "target_sha256": sha_1100,
            "parent_sha256": meta_190.head_sha256,
        },
        security_manager=sec,
    )

    # Stage v1.11.0 WITH PENDING_COMMIT followed by COMMIT_ABORTED -> must be quarantined!
    ont_1110 = ont_1100.model_copy(update={"ontology_version": "1.11.0", "parent_sha256": sha_1100})
    sha_1110 = compute_ontology_sha256(ont_1110)
    v1110_dir = sdir / "versions" / "v1.11.0"
    v1110_dir.mkdir(parents=True, exist_ok=True)
    (v1110_dir / "ontology.json").write_bytes(
        dumps_jcs_bytes(ont_1110.model_dump(mode="json", exclude_none=True))
    )
    (v1110_dir / "meta.json").write_text(
        json.dumps(
            {
                "target_version": "v1.11.0",
                "target_sha256": sha_1110,
                "parent_sha256": sha_1100,
                "proposal_id": None,
            }
        ),
        encoding="utf-8",
    )
    append_signed_audit_record(
        session_dir=sdir,
        action="PENDING_COMMIT",
        actor="human:steward",
        target_version="v1.11.0",
        head_sha256=sha_1110,
        details={
            "target_version": "v1.11.0",
            "target_sha256": sha_1110,
            "parent_sha256": sha_1100,
        },
        security_manager=sec,
    )
    append_signed_audit_record(
        session_dir=sdir,
        action="COMMIT_ABORTED",
        actor="system:recovery",
        target_version="v1.11.0",
        head_sha256=meta_190.head_sha256,
        details={
            "aborted_pending_sha256": sha_1110,
            "target_version": "v1.11.0",
        },
        security_manager=sec,
    )

    svc.reconcile_and_recover_session("sess-semver-rec")
    recovered = mgr.get_session("sess-semver-rec")
    assert recovered.head_version == "1.10.0"
    assert recovered.head_sha256 == sha_1100
    assert "v1.10.0" in recovered.committed_versions
    assert "v1.11.0" not in recovered.committed_versions
    assert not v1110_dir.exists()
    assert len(list((sdir / ".quarantine").glob("v1.11.0-*"))) == 1


# ==============================================================================
# 4. P08a2::MAJ-01 — Genuine Genesis Root Check & Non-CBC Seed Attribution Gate
# ==============================================================================


def test_p08a2_maj01_forged_v000_genesis_root_rejected_and_enum_seed_requires_ack(
    tmp_path: Path,
) -> None:
    """Tampered v0.0.0 genesis root fails replay check; enum-only seed root requires admin ack."""
    ws_a = tmp_path / "ws_a"
    ws_b = tmp_path / "ws_b"
    sec_a = WorkspaceSecurityManager(studio_root=ws_a)
    sec_b = WorkspaceSecurityManager(studio_root=ws_b)
    mgr_a = StudioSessionManager(studio_root=ws_a, security_manager=sec_a)

    # 1. Tamper v0.0.0 root ontology inside exported archive to smuggle an ungrounded concept
    mgr_a.create_session("gen-clean", domain_namespace="finance")
    clean_zip = tmp_path / "gen_clean.odvstudio.zip"
    export_bundle(mgr_a.get_session_dir("gen-clean"), clean_zip, security_manager=sec_a)

    forged_ont = GraphAOntology(
        ontology_version="0.0.0",
        domain_namespace="finance",
        enterprise_owner="Enterprise Architecture",
        canonical_business_concepts=[
            CanonicalBusinessConcept(
                concept_name="SmuggledGenesisConcept",
                concept_uri="urn:ontology:entity:smuggled",
                target_hub="hub_smuggled",
            )
        ],
    )
    forged_sha = compute_ontology_sha256(forged_ont)
    forged_ont_bytes = dumps_jcs_bytes(forged_ont.model_dump(mode="json", exclude_none=True))

    forged_zip = tmp_path / "forged_v0.odvstudio.zip"
    with (
        zipfile.ZipFile(clean_zip, "r") as zin,
        zipfile.ZipFile(forged_zip, "w", compression=zipfile.ZIP_DEFLATED) as zout,
    ):
        for item in zin.infolist():
            raw = zin.read(item.filename)
            if item.filename == "versions/v0.0.0/ontology.json":
                raw = forged_ont_bytes
            elif item.filename in ("versions/v0.0.0/meta.json", "versions/v0.0.0/metadata.json"):
                m = json.loads(raw.decode("utf-8"))
                m["target_sha256"] = forged_sha
                raw = json.dumps(m).encode("utf-8")
            elif item.filename == "session.json":
                s = json.loads(raw.decode("utf-8"))
                s["head_sha256"] = forged_sha
                raw = json.dumps(s).encode("utf-8")
            elif item.filename == "bundle.json":
                b = json.loads(raw.decode("utf-8"))
                b["head_sha256"] = forged_sha
                raw = json.dumps(b).encode("utf-8")
            zout.writestr(item, raw)

    with pytest.raises(ImportDeltaReplayHashMismatchError):
        import_bundle(
            forged_zip,
            ws_b,
            security_manager=sec_b,
            new_session_id="imp-forged-v0",
            actor_role="admin",
            acknowledge_unverified_import_attribution=True,
            import_rationale="Testing rejection of forged v0.0.0 genesis ontology.",
        )

    # 2. Seeded root (mode="seed") with 0 CBCs and 1 ReferenceEnumeration MUST require admin ack
    enum_seed_ont = GraphAOntology(
        ontology_version="0.1.0",
        domain_namespace="finance",
        enterprise_owner="Enterprise Architecture",
        canonical_business_concepts=[],
        reference_enumerations=[
            ReferenceEnumeration(
                enum_uri="urn:ontology:enum:currency_code",
                target_reference_table="ref_currency_code",
                allowed_codes=["USD", "EUR", "GBP"],
            )
        ],
    )
    mgr_a.create_session("seed-enum-only", initial_ontology=enum_seed_ont)
    enum_zip = tmp_path / "seed_enum.odvstudio.zip"
    export_bundle(mgr_a.get_session_dir("seed-enum-only"), enum_zip, security_manager=sec_a)

    with pytest.raises(ImportGroundingViolationError):
        import_bundle(
            enum_zip,
            ws_b,
            security_manager=sec_b,
            new_session_id="imp-enum-unack",
            actor_role="admin",
            acknowledge_unverified_import_attribution=False,
        )

    imp_enum = import_bundle(
        enum_zip,
        ws_b,
        security_manager=sec_b,
        new_session_id="imp-enum-ack",
        actor_role="admin",
        acknowledge_unverified_import_attribution=True,
        import_rationale="Acknowledging seeded reference enumeration root without CBCs.",
    )
    assert imp_enum.head_version == "0.1.0"


# ==============================================================================
# 5. P08b::MAJ-01, P08b::MAJ-02, P08b::MAJ-03 — Job Runners
# ==============================================================================


class _ConfidentialOverrideAdapter(DocumentAdapter):
    """Test adapter returning CONFIDENTIAL chunk and table regardless of caller taint."""

    @property
    def adapter_name(self) -> str:
        return "ConfidentialOverrideAdapter"

    @property
    def supported_mime_types(self) -> tuple[str, ...]:
        return ("application/x-conf-test",)

    @property
    def supported_extensions(self) -> tuple[str, ...]:
        return (".conftest",)

    def supports_mime_type(self, mime_type: str) -> bool:
        return mime_type.strip().lower() == "application/x-conf-test"

    def supports_extension(self, extension: str) -> bool:
        ext = extension.lower().strip()
        return ext in (".conftest", "conftest")

    def parse_bytes(
        self,
        data: bytes,
        filename: str = "doc.conftest",
        mime_type: str | None = None,
        **kwargs: Any,
    ) -> IngestionResult:
        text = data.decode("utf-8")
        return IngestionResult(
            document_id="doc-conf",
            filename=filename,
            mime_type=mime_type or "application/x-conf-test",
            total_pages=1,
            total_characters=len(text),
            normalized_text=text,
            taint=TaintClassification.CONFIDENTIAL,
            chunks=[
                EvidenceChunk(
                    chunk_id="chk-conf-1",
                    document_id="doc-conf",
                    text=text,
                    start_char=0,
                    end_char=len(text),
                    page_number=1,
                    taint=TaintClassification.CONFIDENTIAL,
                )
            ],
            tables=[
                TableStructure(
                    table_id="tbl-conf-1",
                    headers=["Client", "Code"],
                    rows=[["AcmeVault", "ProjectNightfall"]],
                    taint=TaintClassification.CONFIDENTIAL,
                    metadata={"sensitive_entities": ["AcmeVault"]},
                )
            ],
        )


def test_p08b_maj01_monotonic_taint_and_table_sensitive_terms_union(tmp_path: Path) -> None:
    """Verify _max_taint monotonicity at parse & load time and table sensitive_terms union."""
    assert _max_taint("CLEAN", None) == TaintClassification.PUBLIC
    assert _max_taint("CLEAN", "internal") == TaintClassification.INTERNAL
    assert _max_taint("CLEAN", "TAINTED") == TaintClassification.CONFIDENTIAL
    assert _max_taint("TAINTED", "restricted") == TaintClassification.RESTRICTED

    ws = tmp_path / "studio"
    sec = WorkspaceSecurityManager(studio_root=ws)
    mgr = StudioSessionManager(studio_root=ws, security_manager=sec)
    mgr.create_session("sess-taint")
    sdir = mgr.get_session_dir("sess-taint")
    cas = mgr.get_cas("sess-taint")

    # 1. Parse time: CLEAN manifest NEVER downgrades CONFIDENTIAL adapter chunk/table!
    conf_sha = cas.put(b"Classified counterparty schedule.")
    clean_sha = cas.put(b"Public glossary definition.")
    (sdir / "inputs" / "manifest.json").write_text(
        json.dumps(
            {
                "files": [
                    {
                        "source_id": "src-adapter-conf",
                        "filename": "schedule.conftest",
                        "mime_type": "application/x-conf-test",
                        "sha256": conf_sha,
                        "taint": "CLEAN",
                        "tombstoned": False,
                    },
                    {
                        "source_id": "src-public-txt",
                        "filename": "glossary.txt",
                        "mime_type": "text/plain",
                        "sha256": clean_sha,
                        "taint": "CLEAN",
                        "tombstoned": False,
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    ing_svc = IngestionService(adapters=[_ConfidentialOverrideAdapter()])
    from opendv_sources.ingestion import PlainTextDocumentAdapter

    ing_svc.register_adapter(PlainTextDocumentAdapter())

    run_parse_session_sources(mgr, "sess-taint", ingestion_service=ing_svc)
    chunks, tables = load_active_session_evidence(sdir)
    by_src = {(c.metadata or {}).get("source_id"): c for c in chunks}
    assert by_src["src-adapter-conf"].taint == TaintClassification.CONFIDENTIAL
    assert by_src["src-public-txt"].taint == TaintClassification.PUBLIC
    assert len(tables) == 1
    assert tables[0].taint == TaintClassification.CONFIDENTIAL

    # 2. Load time: reclassifying src-public-txt to TAINTED immediately escalates loaded chunk!
    (sdir / "inputs" / "manifest.json").write_text(
        json.dumps(
            {
                "files": [
                    {
                        "source_id": "src-adapter-conf",
                        "filename": "schedule.conftest",
                        "mime_type": "application/x-conf-test",
                        "sha256": conf_sha,
                        "taint": "CLEAN",
                        "tombstoned": False,
                    },
                    {
                        "source_id": "src-public-txt",
                        "filename": "glossary.txt",
                        "mime_type": "text/plain",
                        "sha256": clean_sha,
                        "taint": "TAINTED",
                        "tombstoned": False,
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    chunks_escalated, _ = load_active_session_evidence(sdir)
    by_src_after = {(c.metadata or {}).get("source_id"): c for c in chunks_escalated}
    assert by_src_after["src-public-txt"].taint == TaintClassification.CONFIDENTIAL
    agent_chunks_after, _ = _prepare_evidence_for_agent(chunks_escalated, [], sec)
    by_agent_src = {(c.metadata or {}).get("source_id"): c for c in agent_chunks_after}
    assert by_agent_src["src-public-txt"].metadata["pseudonymized"] is True

    # 3. _prepare_evidence_for_agent unions t_meta['sensitive_entities'] with caller sensitive_terms
    non_dns_table = TableStructure(
        table_id="tbl-union",
        headers=["Counterparty", "Codename"],
        rows=[["AcmeVault", "ProjectNightfall"]],
        caption="Deal AcmeVault / ProjectNightfall",
        taint=TaintClassification.CONFIDENTIAL,
        metadata={"sensitive_entities": ["AcmeVault"]},
    )
    _, safe_tables = _prepare_evidence_for_agent(
        [],
        [non_dns_table],
        sec,
        sensitive_terms=["ProjectNightfall"],
    )
    assert len(safe_tables) == 1
    rendered = json.dumps(safe_tables[0].model_dump(mode="json")).lower()
    assert "acmevault" not in rendered
    assert "projectnightfall" not in rendered
    assert safe_tables[0].metadata["pseudonymized"] is True


@pytest.mark.parametrize(
    "bad_version",
    [
        "../v0.0.0",
        "..\\v0.0.0",
        "1.0",
        "1.0.0-alpha",
        "1.0.0+build1",
        "vv0.0.0",
        "v",
        "",
        "0.0.0/../../secret",
    ],
)
def test_p08b_maj02_strict_semver_and_containment_in_construct_and_loader(
    tmp_path: Path, bad_version: str
) -> None:
    """Malformed or path-traversal base_version raises OpenDVError(VERSION_NOT_FOUND)."""
    ws = tmp_path / "studio"
    sec = WorkspaceSecurityManager(studio_root=ws)
    mgr = StudioSessionManager(studio_root=ws, security_manager=sec)
    mgr.create_session("sess-ver-guard")
    sdir = mgr.get_session_dir("sess-ver-guard")

    with pytest.raises(OpenDVError) as exc1:
        _load_session_version_ontology(sdir, "sess-ver-guard", bad_version, "0.0.0")
    assert exc1.value.error_code == ErrorCode.VERSION_NOT_FOUND

    with pytest.raises(OpenDVError) as exc2:
        run_construct_session(mgr, "sess-ver-guard", base_version=bad_version)
    assert exc2.value.error_code == ErrorCode.VERSION_NOT_FOUND


def test_p08b_maj03_tombstoned_sha_with_active_source_id_and_reparse_l5_purge(
    tmp_path: Path,
) -> None:
    """Tombstoned SHA items drop even if source_id is active, and re-parse purges stale items."""
    ws = tmp_path / "studio"
    sec = WorkspaceSecurityManager(studio_root=ws)
    mgr = StudioSessionManager(studio_root=ws, security_manager=sec)
    mgr.create_session("sess-reparse")
    sdir = mgr.get_session_dir("sess-reparse")
    cas = mgr.get_cas("sess-reparse")

    csv_v1 = b"col_a,col_b\nval1,val2\nval3,val4\n"
    sha_old = cas.put(csv_v1)
    txt_keep = b"Keep this independent source chunk."
    sha_keep = cas.put(txt_keep)

    (sdir / "inputs" / "manifest.json").write_text(
        json.dumps(
            {
                "files": [
                    {
                        "source_id": "src-evolving",
                        "filename": "data.csv",
                        "mime_type": "text/csv",
                        "sha256": sha_old,
                        "tombstoned": False,
                    },
                    {
                        "source_id": "src-keep",
                        "filename": "keep.txt",
                        "mime_type": "text/plain",
                        "sha256": sha_keep,
                        "tombstoned": False,
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    res1 = run_parse_session_sources(mgr, "sess-reparse")
    assert res1["parsed_sources_count"] == 2
    c1, t1 = load_active_session_evidence(sdir)
    assert any((c.metadata or {}).get("source_id") == "src-evolving" for c in c1)
    assert any((t.metadata or {}).get("source_id") == "src-evolving" for t in t1)

    # 1. Tombstone sha_old while re-registering SAME source_id='src-evolving' with sha_new:
    # BEFORE re-parsing, load_active_session_evidence MUST drop stale sha_old chunks & tables!
    txt_v2 = b"Updated plain prose without any CSV table."
    sha_new = cas.put(txt_v2)
    (sdir / "inputs" / "manifest.json").write_text(
        json.dumps(
            {
                "files": [
                    {
                        "source_id": "src-evolving",
                        "filename": "data.csv",
                        "mime_type": "text/csv",
                        "sha256": sha_old,
                        "tombstoned": True,
                    },
                    {
                        "source_id": "src-evolving",
                        "filename": "notes.txt",
                        "mime_type": "text/plain",
                        "sha256": sha_new,
                        "tombstoned": False,
                    },
                    {
                        "source_id": "src-keep",
                        "filename": "keep.txt",
                        "mime_type": "text/plain",
                        "sha256": sha_keep,
                        "tombstoned": False,
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    c_mid, t_mid = load_active_session_evidence(sdir)
    assert all((c.metadata or {}).get("source_id") != "src-evolving" for c in c_mid)
    assert all((t.metadata or {}).get("source_id") != "src-evolving" for t in t_mid)
    assert any((c.metadata or {}).get("source_id") == "src-keep" for c in c_mid)

    # 2. Also test L5 merge purge directly when sha_old was NOT tombstoned (e.g. manifest updated
    # in-place to sha_new without a tombstone entry): prior chunks & tables for src-evolving MUST
    # be purged from chunks.jsonl and tables.jsonl when src-evolving is re-parsed!
    (sdir / "inputs" / "manifest.json").write_text(
        json.dumps(
            {
                "files": [
                    {
                        "source_id": "src-evolving",
                        "filename": "notes.txt",
                        "mime_type": "text/plain",
                        "sha256": sha_new,
                        "tombstoned": False,
                    },
                    {
                        "source_id": "src-keep",
                        "filename": "keep.txt",
                        "mime_type": "text/plain",
                        "sha256": sha_keep,
                        "tombstoned": False,
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    # Before re-parse, old non-tombstoned CSV chunks/tables are still on disk
    c_before, t_before = load_active_session_evidence(sdir)
    assert any(c.source_sha256 == sha_old for c in c_before)
    assert len(t_before) >= 1

    # Re-parse ONLY src-evolving -> old CSV chunks & tables for src-evolving are purged!
    run_parse_session_sources(mgr, "sess-reparse", source_ids=["src-evolving"])
    c_after, t_after = load_active_session_evidence(sdir)
    evolving_chunks = [c for c in c_after if (c.metadata or {}).get("source_id") == "src-evolving"]
    assert len(evolving_chunks) == 1
    assert evolving_chunks[0].source_sha256 == sha_new
    assert "Updated plain prose" in evolving_chunks[0].text
    assert t_after == []
    assert any((c.metadata or {}).get("source_id") == "src-keep" for c in c_after)
