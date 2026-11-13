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
from opendv_contracts.dtos.clarification import (
    ClarificationAnswerItem,
    ClarificationAnswerRequest,
)
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
from opendv_service.construction.clarification_harness import ClarificationHarness
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
    lk = StudioLock(lock_path, timeout=2.0)
    target_path_str = os.fspath(lk.path)

    flock_ops: list[tuple[int, int]] = []
    awaiting_post_flock_stat = False

    def _fake_flock(fd: int, op: int) -> None:
        nonlocal awaiting_post_flock_stat
        flock_ops.append((fd, op))
        if op & 2:  # LOCK_EX
            awaiting_post_flock_stat = True
        elif op & 8:  # LOCK_UN
            awaiting_post_flock_stat = False

    fake_fcntl = types.SimpleNamespace(
        LOCK_EX=2,
        LOCK_NB=4,
        LOCK_UN=8,
        flock=_fake_flock,
    )
    fake_sys = types.SimpleNamespace(platform="linux")
    monkeypatch.setattr(locks_mod, "sys", fake_sys)
    monkeypatch.setattr(locks_mod, "fcntl", fake_fcntl, raising=False)

    real_stat = os.stat
    stat_calls = 0

    def _patched_stat(p: Any, *args: Any, **kwargs: Any) -> Any:
        nonlocal awaiting_post_flock_stat, stat_calls
        if awaiting_post_flock_stat and not isinstance(p, int) and os.fspath(p) == target_path_str:
            awaiting_post_flock_stat = False
            stat_calls += 1
            if stat_calls == 1:
                raise FileNotFoundError("Simulated unlink between open and flock")
            if stat_calls == 2:
                st = real_stat(p, *args, **kwargs)
                return types.SimpleNamespace(st_dev=st.st_dev + 999, st_ino=st.st_ino + 999)
        return real_stat(p, *args, **kwargs)

    # Intercept only post-flock os.stat(self.path) without recursing via Path.resolve/exists
    monkeypatch.setattr(locks_mod.os, "stat", _patched_stat)

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


def test_s3_r6_maj02_and_min01_clone_preserves_version_waivers_and_probe_no_target(
    tmp_path: Path,
) -> None:
    """[S3-R6-MAJ-02] & [S3-R6-MIN-01]: clone_session preserves versions/v*/waivers.json,
    strips root waivers.json/*.lock/*.tmp, and _collect_locally_waived_session_concepts
    accepts probe_no_target digests for both promote_to_workspace and export->import.
    """
    ws_a = tmp_path / "ws_a"
    ws_b = tmp_path / "ws_b"
    sec_a = WorkspaceSecurityManager(studio_root=ws_a)
    sec_b = WorkspaceSecurityManager(studio_root=ws_b)
    mgr_a = StudioSessionManager(studio_root=ws_a, security_manager=sec_a)
    svc_a = AtomicCommitService(session_manager=mgr_a, security_manager=sec_a)

    meta = mgr_a.create_session("sess-waiver-src")
    # Sign waiver over delta with target_ontology_version=None AND parent_sha256=meta.head_sha256
    # (matching probe_no_target), then strip submitted_delta_sha256 from meta.json to exercise
    # [S3-R6-MIN-01] probe_no_target fallback directly!
    delta_no_target = OntologyDelta(
        delta_id="d-clone-waiver",
        base_ontology_version="0.0.0",
        target_ontology_version=None,
        parent_sha256=meta.head_sha256,
        operations=[
            AddConceptOp(
                op_id="op-custom-exp",
                concept_name="CustomDeskExposure",
                concept_uri="urn:ontology:entity:custom_desk_exposure",
                target_hub="hub_custom_desk_exposure",
            )
        ],
    )
    probe_sha = delta_no_target.canonical_sha256()

    wv_unsigned = StewardWaiverRecord(
        proposal_id=uuid.uuid4(),
        target_concept_uris=["urn:ontology:entity:custom_desk_exposure"],
        proposal_delta_sha256=probe_sha,
        base_sha256=meta.head_sha256,
        authorized_by="human:chief_steward",
        authorized_role="admin",
        rationale="Approved custom desk exposure concept with steward grounding waiver.",
        created_at=datetime.now(UTC) - timedelta(minutes=1),
        signature_or_token="0" * 64,
    )
    wv_signed = wv_unsigned.model_copy(
        update={
            "signature_or_token": sec_a.sign_waiver(wv_unsigned.signing_payload().encode("utf-8"))
        }
    )

    r1 = svc_a.commit_delta(
        "sess-waiver-src",
        delta_no_target,
        if_match=f'"{meta.head_sha256}"',
        waivers=[wv_signed],
    )
    assert r1.version == "v1.0.0"

    sdir = mgr_a.get_session_dir("sess-waiver-src")
    assert (sdir / "versions" / "v1.0.0" / "waivers.json").is_file()

    # Remove submitted_delta_sha256 from meta.json so _collect_locally_waived_session_concepts
    # MUST rely on probe_no_target.canonical_sha256() ([S3-R6-MIN-01])!
    meta_path = sdir / "versions" / "v1.0.0" / "meta.json"
    mdata = json.loads(meta_path.read_text(encoding="utf-8"))
    mdata.pop("submitted_delta_sha256", None)
    meta_path.write_text(json.dumps(mdata, indent=2), encoding="utf-8")

    # Seed root waivers.json plus transient *.tmp / *.lock files that clone_session must exclude
    (sdir / "waivers.json").write_text('[{"legacy": true}]', encoding="utf-8")
    (sdir / "transient.tmp").write_text("scratch", encoding="utf-8")
    (sdir / "versions" / "v1.0.0" / "stale.lock").write_text("lock", encoding="utf-8")

    cloned_meta = mgr_a.clone_session(
        "sess-waiver-src",
        new_session_id="sess-waiver-cloned",
        actor="human:steward",
        actor_role="editor",
    )
    assert cloned_meta.head_version == "1.0.0"

    cdir = mgr_a.get_session_dir("sess-waiver-cloned")
    assert not (cdir / "waivers.json").exists()
    assert not (cdir / "transient.tmp").exists()
    assert not (cdir / "versions" / "v1.0.0" / "stale.lock").exists()
    assert (cdir / "versions" / "v1.0.0" / "waivers.json").is_file()

    # 1. promote_to_workspace on the cloned session succeeds!
    target_ws_contract = tmp_path / "promoted_graph_a.yaml"
    promo_res = svc_a.promote_to_workspace(
        "sess-waiver-cloned",
        target_workspace=target_ws_contract,
        expected_workspace_sha256="",
        rationale="Promoting cloned session with preserved version-scoped waivers.",
        actor="human:chief_steward",
        actor_role="admin",
        allowed_workspace_roots=[tmp_path],
    )
    assert promo_res.promoted_version == "v1.0.0"
    assert target_ws_contract.is_file()

    # 2. export_bundle -> import_bundle on the cloned session succeeds!
    bundle_zip = tmp_path / "cloned_waiver.odvstudio.zip"
    export_bundle(cdir, bundle_zip, security_manager=sec_a)
    imported_meta = import_bundle(
        bundle_zip,
        ws_b,
        security_manager=sec_b,
        new_session_id="sess-waiver-imported",
        actor_role="admin",
        acknowledge_unverified_import_attribution=True,
        import_rationale="Importing cloned session bundle with preserved v1.0.0 waivers.",
    )
    assert imported_meta.head_version == "1.0.0"


def test_s3_r6_maj01_promote_and_clarification_harness_lifecycle_lock_and_no_ghost_dir(
    tmp_path: Path,
) -> None:
    """[S3-R6-MAJ-01]: promote_to_workspace and ClarificationHarness block behind
    .lifecycle_<sid>.lock during swap/delete and never resurrect ghost directories after delete.
    """
    ws = tmp_path / "studio"
    sec = WorkspaceSecurityManager(studio_root=ws)
    mgr = StudioSessionManager(studio_root=ws, security_manager=sec)
    svc = AtomicCommitService(session_manager=mgr, security_manager=sec)

    meta = mgr.create_session("sess-life-promo")
    svc.commit_delta(
        "sess-life-promo",
        OntologyDelta(
            delta_id="d-grounded",
            base_ontology_version="0.0.0",
            target_ontology_version="1.0.0",
            parent_sha256=meta.head_sha256,
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

    sdir = mgr.get_session_dir("sess-life-promo")
    harness = ClarificationHarness(session_dir=sdir)
    target_ws_contract = tmp_path / "life_graph_a.yaml"

    promo_done = threading.Event()
    clar_done = threading.Event()
    errors: list[BaseException] = []

    def _promo_worker() -> None:
        try:
            svc.promote_to_workspace(
                "sess-life-promo",
                target_workspace=target_ws_contract,
                expected_workspace_sha256="",
                rationale="Serialize promote_to_workspace behind .lifecycle_<sid>.lock.",
                actor="human:admin",
                actor_role="admin",
                allowed_workspace_roots=[tmp_path],
            )
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)
        finally:
            promo_done.set()

    def _clar_worker() -> None:
        try:
            harness.generate_questions(candidate_terms=["Account"])
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)
        finally:
            clar_done.set()

    lifecycle_lock_path = ws / ".lifecycle_sess-life-promo.lock"
    with studio_lock(
        lifecycle_lock_path,
        level=L5_SESSION,
        session_id="sess-life-promo",
        remove_on_release=False,
    ):
        t_promo = threading.Thread(target=_promo_worker)
        t_clar = threading.Thread(target=_clar_worker)
        t_promo.start()
        t_clar.start()
        # Both MUST block while .lifecycle_sess-life-promo.lock is held!
        assert not promo_done.wait(0.15)
        assert not clar_done.wait(0.05)

    t_promo.join(timeout=5.0)
    t_clar.join(timeout=5.0)
    assert promo_done.is_set()
    assert clar_done.is_set()
    assert errors == []
    assert target_ws_contract.is_file()

    # Verify ghost-directory prevention after delete_session!
    mgr.create_session("sess-ghost-guard")
    ghost_dir = mgr.get_session_dir("sess-ghost-guard")
    ghost_harness = ClarificationHarness(session_dir=ghost_dir)
    mgr.delete_session("sess-ghost-guard")
    assert not ghost_dir.exists()

    with pytest.raises(FileNotFoundError):
        ghost_harness.generate_questions(candidate_terms=["Account"])
    assert not ghost_dir.exists()

    with pytest.raises(FileNotFoundError):
        ghost_harness.get_answers()
    assert not ghost_dir.exists()

    with pytest.raises(FileNotFoundError):
        ghost_harness.get_binding_constraints()
    assert not ghost_dir.exists()

    with pytest.raises(FileNotFoundError):
        ghost_harness.save_answers(
            ClarificationAnswerRequest(
                answers=[
                    ClarificationAnswerItem(
                        question_id="q-missing",
                        selected_options=["opt-1"],
                    )
                ]
            )
        )
    assert not ghost_dir.exists()

    with pytest.raises(FileNotFoundError):
        svc.promote_to_workspace(
            "sess-ghost-guard",
            target_workspace=tmp_path / "ghost_out.yaml",
            expected_workspace_sha256="",
            rationale="Must fail without creating ghost session directory.",
            actor="human:admin",
            actor_role="admin",
            allowed_workspace_roots=[tmp_path],
        )
    assert not ghost_dir.exists()


# ==============================================================================
# Round-7 Opus 5.5 Follow-Up Remediations (P06a, P07a2, P07b, P08a1, P08a2)
# ==============================================================================


def test_p06a_maj01_construct_proposal_typed_opendv_errors() -> None:
    """P06a::MAJ-01: construct_proposal raises OpenDVError on stale if_match & invalid mode."""
    from opendv_contracts.errors import ErrorCode, OpenDVError
    from opendv_core.genesis import create_genesis_ontology
    from opendv_service.construction.deterministic_pipeline import (
        DeterministicConstructionPipeline,
    )
    from opendv_service.construction.models import ConstructOptions

    base_ont = create_genesis_ontology()
    pipeline = DeterministicConstructionPipeline()

    with pytest.raises(OpenDVError) as occ_exc:
        pipeline.construct_proposal(
            base_ont,
            options=ConstructOptions(mode="deterministic", if_match="f" * 64),
        )
    assert isinstance(occ_exc.value, ValueError)
    assert occ_exc.value.error_code == ErrorCode.ONT_EVO_CONCURRENT_MODIFICATION
    assert occ_exc.value.status_code == 412
    assert occ_exc.value.exit_code == 5

    bad_opts = ConstructOptions.model_construct(mode="llm_only", if_match=None, auto_commit=False)
    with pytest.raises(OpenDVError) as mode_exc:
        pipeline.construct_proposal(base_ont, options=bad_opts)
    assert isinstance(mode_exc.value, ValueError)
    assert mode_exc.value.error_code == ErrorCode.SYNTAX_INVALID_ARGUMENT
    assert mode_exc.value.status_code == 400
    assert mode_exc.value.exit_code == 2


def test_p07a2_maj01_promotion_forward_destructive_bump_and_rationale_invariant(
    tmp_path: Path,
) -> None:
    """P07a2::MAJ-01: acknowledge_historical_contract_warning never waives forward MAJOR bump
    and always requires >=10 char historical_contract_rationale.
    """
    from opendv_service.ontology.validator import ContractViolationError
    from opendv_service.studio.atomic_commit import HistoricalContractRationaleRequiredError

    ws_root = tmp_path / "ws"
    ws_root.mkdir(parents=True, exist_ok=True)
    studio_root = ws_root / "studio"
    sec = WorkspaceSecurityManager(studio_root=studio_root)
    mgr = StudioSessionManager(studio_root=studio_root, security_manager=sec)
    svc = AtomicCommitService(session_manager=mgr, security_manager=sec)

    meta0 = mgr.create_session("sess-promo-gov")
    r100 = svc.commit_delta(
        "sess-promo-gov",
        OntologyDelta(
            delta_id="d-100",
            parent_sha256=meta0.head_sha256,
            base_ontology_version="0.0.0",
            target_ontology_version="1.0.0",
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

    ws_contract = ws_root / "graph_a_ontology.json"

    # 1. acknowledge_historical_contract_warning=True without rationale >=10 is ALWAYS rejected
    with pytest.raises(HistoricalContractRationaleRequiredError):
        svc.promote_to_workspace(
            "sess-promo-gov",
            ws_contract,
            "",
            rationale="Greenfield promotion with stray ack flag.",
            acknowledge_historical_contract_warning=True,
            historical_contract_rationale="short",
            actor_role="admin",
            allowed_workspace_roots=[ws_root],
        )

    # Deploy v1.0.0 containing BOTH Account and Party in workspace contract
    ws_v100 = GraphAOntology(
        ontology_version="1.0.0",
        domain_namespace="general",
        enterprise_owner="Enterprise Architecture",
        parent_sha256="0" * 64,
        canonical_business_concepts=[
            CanonicalBusinessConcept(
                concept_uri="urn:ontology:entity:account",
                concept_name="Account",
                target_hub="hub_account",
                external_mappings=[_fibo_mapping()],
            ),
            CanonicalBusinessConcept(
                concept_uri="urn:ontology:entity:party",
                concept_name="Contract Party",
                target_hub="hub_party",
                external_mappings=[_fibo_mapping(FIBO_PARTY_URI)],
            ),
        ],
    )
    ws_contract.write_text(ws_v100.model_dump_json(indent=2), encoding="utf-8")
    ws_v100_raw_sha = hashlib.sha256(ws_contract.read_bytes()).hexdigest()

    # Advance session to v1.1.0 (lacks 'party' vs deployed v1.0.0 -> destructive diff!)
    svc.commit_delta(
        "sess-promo-gov",
        OntologyDelta(
            delta_id="d-110",
            parent_sha256=r100.target_sha256,
            base_ontology_version="1.0.0",
            target_ontology_version="1.1.0",
            operations=[
                AddConceptOp(
                    op_id="op-party-2",
                    concept_name="Contract Party",
                    concept_uri="urn:ontology:entity:contract_party",
                    target_hub="hub_contract_party",
                    external_mappings=[_fibo_mapping(FIBO_PARTY_URI)],
                )
            ],
        ),
    )

    # 2. Forward 1.0.0 -> 1.1.0 destructive removal MUST reject with ContractViolationError
    # even when acknowledge_historical_contract_warning=True and rationale >= 10 chars!
    with pytest.raises(ContractViolationError):
        svc.promote_to_workspace(
            "sess-promo-gov",
            ws_contract,
            ws_v100_raw_sha,
            rationale="Attempt forward 1.1.0 promotion dropping Party.",
            acknowledge_historical_contract_warning=True,
            historical_contract_rationale="Attempting to waive MAJOR bump via historical flag.",
            enforce_lineage=False,
            actor_role="admin",
            allowed_workspace_roots=[ws_root],
        )

    # 3. Historical rollback (2.0.0 -> 1.1.0) with ack + rationale succeeds
    # and records historical_contract_rationale in PROMOTION_INTENT and PROMOTION_COMMITTED!
    ws_v200 = ws_v100.model_copy(update={"ontology_version": "2.0.0"})
    ws_contract.write_text(ws_v200.model_dump_json(indent=2), encoding="utf-8")
    ws_v200_raw_sha = hashlib.sha256(ws_contract.read_bytes()).hexdigest()

    hist_rationale = "Approved emergency rollback from 2.0.0 to validated 1.1.0."
    svc.promote_to_workspace(
        "sess-promo-gov",
        ws_contract,
        ws_v200_raw_sha,
        rationale="Emergency workspace rollback.",
        acknowledge_historical_contract_warning=True,
        historical_contract_rationale=hist_rationale,
        enforce_lineage=False,
        actor_role="admin",
        allowed_workspace_roots=[ws_root],
    )
    sdir = mgr.get_session_dir("sess-promo-gov")
    audit_recs = [
        json.loads(ln)
        for ln in (sdir / "audit.jsonl").read_text(encoding="utf-8").splitlines()
        if ln.strip()
    ]
    intent_rec = next(r for r in reversed(audit_recs) if r["action"] == "PROMOTION_INTENT")
    commit_rec = next(r for r in reversed(audit_recs) if r["action"] == "PROMOTION_COMMITTED")
    assert intent_rec["details"]["historical_contract_rationale"] == hist_rationale
    assert commit_rec["details"]["historical_contract_rationale"] == hist_rationale


def test_p07b_maj01_release_unlinks_under_flock_on_posix_and_concurrent_remove_on_release(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """P07b::MAJ-01: On POSIX, StudioLock.release unlinks BEFORE _close_file(); concurrent
    remove_on_release=True preserves strict mutual exclusion.
    """
    import opendv_service.studio.locks as locks_mod

    # 1. Verify POSIX release order: unlink runs WHILE self._file is still open & locked
    order_events: list[tuple[str, bool]] = []
    posix_lock_path = tmp_path / "posix_order.lock"
    lk = StudioLock(posix_lock_path, level=LockLevel.L4, remove_on_release=True)
    lk.acquire()

    orig_close = lk._close_file

    def _spy_close() -> None:
        order_events.append(("close", lk._file is not None and not lk._file.closed))
        orig_close()

    orig_unlink = Path.unlink

    def _spy_unlink(self_path: Path, missing_ok: bool = False) -> None:
        if self_path == lk.path:
            # Record that self._file is STILL open (flock held on POSIX) when unlink executes!
            order_events.append(("unlink", lk._file is not None and not lk._file.closed))
            return
        orig_unlink(self_path, missing_ok=missing_ok)

    monkeypatch.setattr(lk, "_close_file", _spy_close)
    monkeypatch.setattr(Path, "unlink", _spy_unlink)
    monkeypatch.setattr(locks_mod.os, "name", "posix")
    try:
        lk.release()
    finally:
        monkeypatch.undo()
        posix_lock_path.unlink(missing_ok=True)

    assert order_events == [("unlink", True), ("close", True)]

    # 2. Multi-threaded contention stress with remove_on_release=True
    shared_lock_path = tmp_path / "contended_ephemeral.lock"
    active_holders = 0
    overlap_detected = False
    errors: list[BaseException] = []

    def _worker() -> None:
        nonlocal active_holders, overlap_detected
        try:
            for _ in range(8):
                with studio_lock(
                    shared_lock_path,
                    level=LockLevel.L4,
                    timeout=5.0,
                    remove_on_release=True,
                ):
                    active_holders += 1
                    if active_holders > 1:
                        overlap_detected = True
                    time.sleep(0.004)
                    active_holders -= 1
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=_worker) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10.0)

    assert errors == []
    assert overlap_detected is False


def test_p08a1_maj01_clone_session_strict_role_allowlist_and_required_kwarg(
    tmp_path: Path,
) -> None:
    """P08a1::MAJ-01: clone_session requires keyword actor_role and enforces strict allow-list."""
    from opendv_contracts.errors import ErrorCode, OpenDVError

    sec = WorkspaceSecurityManager(studio_root=tmp_path / "studio")
    mgr = StudioSessionManager(studio_root=tmp_path / "studio", security_manager=sec)
    mgr.create_session("src-allowlist")

    # Missing actor_role raises TypeError
    with pytest.raises(TypeError):
        mgr.clone_session("src-allowlist", "clone-missing")  # type: ignore[call-arg]

    for agent_role in ("agent:llm", "Automated", "  AGENT:planner  "):
        with pytest.raises(OpenDVError) as ag_exc:
            mgr.clone_session("src-allowlist", "clone-agent", actor_role=agent_role)
        assert ag_exc.value.error_code == ErrorCode.AGENT_OPERATION_FORBIDDEN
        assert ag_exc.value.status_code == 403

    for bad_role in ("", "   ", "guest", "viewer", "steward"):
        with pytest.raises(OpenDVError) as role_exc:
            mgr.clone_session("src-allowlist", "clone-bad", actor_role=bad_role)
        assert role_exc.value.error_code == ErrorCode.FORBIDDEN_ADMIN_REQUIRED
        assert role_exc.value.status_code == 403

    cloned = mgr.clone_session("src-allowlist", "clone-editor-ok", actor_role="Editor")
    assert cloned.session_id == "clone-editor-ok"


def test_p08a2_maj01_waiver_transplant_via_meta_json_rejected_and_local_hmac_enforced(
    tmp_path: Path,
) -> None:
    """P08a2::MAJ-01: meta.json delta_sha256 cannot transplant a valid K_waiver onto another delta,
    and forged K_waiver raises ImportAttributionUnverifiedError without admin acknowledgement.
    """
    from opendv_service.studio.bundle import (
        ImportAttributionUnverifiedError,
        ImportGroundingViolationError,
    )

    ws_a = tmp_path / "ws_a"
    sec_a = WorkspaceSecurityManager(studio_root=ws_a)
    mgr_a = StudioSessionManager(studio_root=ws_a, security_manager=sec_a)
    svc_a = AtomicCommitService(session_manager=mgr_a, security_manager=sec_a)

    meta = mgr_a.create_session("sess-transplant")

    # Legitimate local K_waiver signed for delta_x (concept_x)
    delta_x = OntologyDelta(
        delta_id="d-legit-x",
        base_ontology_version="0.0.0",
        target_ontology_version="1.0.0",
        parent_sha256=meta.head_sha256,
        operations=[
            AddConceptOp(
                op_id="op-x",
                concept_name="ConceptX",
                concept_uri="urn:ontology:entity:concept_x",
                target_hub="hub_concept_x",
            )
        ],
    )
    wv_x_raw = StewardWaiverRecord(
        proposal_id=uuid.uuid4(),
        target_concept_uris=["urn:ontology:entity:concept_y"],
        proposal_delta_sha256=delta_x.canonical_sha256(),
        base_sha256=meta.head_sha256,
        authorized_by="human:admin_a",
        authorized_role="admin",
        rationale="Signed under local K_waiver for delta_x digest.",
        created_at=datetime.now(UTC) - timedelta(minutes=2),
        signature_or_token="0" * 64,
    )
    wv_x_signed = wv_x_raw.model_copy(
        update={"signature_or_token": sec_a.sign_waiver(wv_x_raw.signing_payload().encode("utf-8"))}
    )

    # Commit unrelated delta_y using its own valid waiver first so bundle is structurally valid
    delta_y = OntologyDelta(
        delta_id="d-unrelated-y",
        base_ontology_version="0.0.0",
        target_ontology_version="1.0.0",
        parent_sha256=meta.head_sha256,
        operations=[
            AddConceptOp(
                op_id="op-y",
                concept_name="ConceptY",
                concept_uri="urn:ontology:entity:concept_y",
                target_hub="hub_concept_y",
            )
        ],
    )
    wv_y_raw = wv_x_raw.model_copy(update={"proposal_delta_sha256": delta_y.canonical_sha256()})
    wv_y_signed = wv_y_raw.model_copy(
        update={"signature_or_token": sec_a.sign_waiver(wv_y_raw.signing_payload().encode("utf-8"))}
    )
    svc_a.commit_delta("sess-transplant", delta_y, waivers=[wv_y_signed])

    base_zip = tmp_path / "base_y.odvstudio.zip"
    export_bundle(mgr_a.get_session_dir("sess-transplant"), base_zip, security_manager=sec_a)

    # Attack 1: Forge meta.json delta_sha256 = delta_x.sha and transplant wv_x_signed!
    transplant_zip = tmp_path / "transplant.odvstudio.zip"
    with (
        zipfile.ZipFile(base_zip, "r") as zin,
        zipfile.ZipFile(transplant_zip, "w", compression=zipfile.ZIP_DEFLATED) as zout,
    ):
        for info in zin.infolist():
            raw = zin.read(info.filename)
            if info.filename in ("versions/v1.0.0/meta.json", "versions/v1.0.0/metadata.json"):
                m_obj = json.loads(raw.decode("utf-8"))
                m_obj["delta_sha256"] = delta_x.canonical_sha256()
                m_obj["submitted_delta_sha256"] = delta_x.canonical_sha256()
                m_obj["waivers"] = [wv_x_signed.model_dump(mode="json")]
                raw = json.dumps(m_obj).encode("utf-8")
            elif info.filename in (
                "versions/v1.0.0/waivers.json",
                "versions/v1.0.0/evaluation.json",
            ):
                raw = json.dumps([wv_x_signed.model_dump(mode="json")]).encode("utf-8")
            zout.writestr(info, raw)

    with pytest.raises(ImportGroundingViolationError):
        import_bundle(
            transplant_zip,
            ws_a,
            security_manager=sec_a,
            new_session_id="imp-transplant",
            actor_role="admin",
        )

    # Attack 2: Matching delta_y digest with forged signature -> ImportAttributionUnverifiedError
    forged_sig_wv = wv_y_signed.model_copy(update={"signature_or_token": "b" * 64})
    forged_sig_zip = tmp_path / "forged_sig.odvstudio.zip"
    with (
        zipfile.ZipFile(base_zip, "r") as zin,
        zipfile.ZipFile(forged_sig_zip, "w", compression=zipfile.ZIP_DEFLATED) as zout,
    ):
        for info in zin.infolist():
            raw = zin.read(info.filename)
            if info.filename in ("versions/v1.0.0/meta.json", "versions/v1.0.0/metadata.json"):
                m_obj = json.loads(raw.decode("utf-8"))
                m_obj["waivers"] = [forged_sig_wv.model_dump(mode="json")]
                raw = json.dumps(m_obj).encode("utf-8")
            elif info.filename in (
                "versions/v1.0.0/waivers.json",
                "versions/v1.0.0/evaluation.json",
            ):
                raw = json.dumps([forged_sig_wv.model_dump(mode="json")]).encode("utf-8")
            zout.writestr(info, raw)

    with pytest.raises(ImportAttributionUnverifiedError):
        import_bundle(
            forged_sig_zip,
            ws_a,
            security_manager=sec_a,
            new_session_id="imp-forged-sig",
            actor_role="editor",
        )


def test_p08a2_maj02_session_fenced_marker_and_rollback_unfencing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """P08a2::MAJ-02: .session_fenced blocks reentrant_session_lock with FileNotFoundError
    and is cleaned up if import_bundle(force=True) rolls back on swap failure.
    """
    import opendv_service.studio.bundle as bundle_mod
    from opendv_service.studio.workspace_security import replace_with_retry as real_replace

    ws = tmp_path / "ws_fence"
    sec = WorkspaceSecurityManager(studio_root=ws)
    mgr = StudioSessionManager(studio_root=ws, security_manager=sec)
    mgr.create_session("fence-sess")
    sdir = mgr.get_session_dir("fence-sess")

    b_zip = tmp_path / "fence.odvstudio.zip"
    export_bundle(sdir, b_zip, security_manager=sec)

    # Manual .session_fenced immediately causes reentrant_session_lock to raise FileNotFoundError
    fence_file = sdir / ".session_fenced"
    fence_file.write_bytes(b"fenced")
    with (
        pytest.raises(FileNotFoundError, match="fenced"),
        reentrant_session_lock(sdir / ".session.lock", session_id="fence-sess"),
    ):
        pass
    fence_file.unlink()

    # Simulate failure on second replace_with_retry(staging_dir, dest_dir) during force=True swap
    call_count = 0

    def _failing_second_replace(src: Path, dst: Path, **kwargs: Any) -> None:
        nonlocal call_count
        if dst == sdir and src.name.startswith(".staging-import-"):
            call_count += 1
            raise OSError("Simulated staging->dest swap failure")
        real_replace(src, dst, **kwargs)

    monkeypatch.setattr(bundle_mod, "replace_with_retry", _failing_second_replace)
    with pytest.raises(OSError, match="Simulated staging->dest swap failure"):
        import_bundle(
            b_zip,
            ws,
            security_manager=sec,
            force=True,
            actor_role="admin",
        )
    monkeypatch.undo()

    assert call_count == 1
    assert sdir.is_dir()
    assert not (sdir / ".session_fenced").exists()
    # Restored session can immediately re-acquire reentrant_session_lock!
    with reentrant_session_lock(sdir / ".session.lock", session_id="fence-sess"):
        assert (sdir / "session.json").is_file()


def test_p08a2_maj03_export_bundle_containment_nesting_and_overwrite_guard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """P08a2::MAJ-03: export_bundle enforces containment, nesting & overwrite guards."""
    from opendv_service.studio.workspace_security import InvalidWorkspacePathError

    ws_root = tmp_path / "allowed_ws"
    outside_root = tmp_path / "outside_ws"
    ws_root.mkdir(parents=True, exist_ok=True)
    outside_root.mkdir(parents=True, exist_ok=True)

    studio_root = ws_root / "studio"
    sec = WorkspaceSecurityManager(studio_root=studio_root)
    mgr = StudioSessionManager(studio_root=studio_root, security_manager=sec)
    mgr.create_session("exp-guard")
    sdir = mgr.get_session_dir("exp-guard")

    # 1. Output inside session_dir is rejected
    with pytest.raises(InvalidWorkspacePathError):
        export_bundle(sdir, sdir / "nested.odvstudio.zip", security_manager=sec)

    # 2. Traversal '..' is rejected
    with pytest.raises(InvalidWorkspacePathError):
        export_bundle(
            sdir,
            ws_root / ".." / "outside_ws" / "trav.zip",
            security_manager=sec,
        )

    # 3. OPENDV_WORKSPACE_ROOTS containment blocks outside_root while allowing studio_root/.scratch
    monkeypatch.setenv("OPENDV_WORKSPACE_ROOTS", str(ws_root))
    with pytest.raises(InvalidWorkspacePathError):
        export_bundle(sdir, outside_root / "escaped.zip", security_manager=sec)

    scratch_zip = studio_root / ".scratch" / "api_export.zip"
    export_bundle(sdir, scratch_zip, security_manager=sec)
    assert scratch_zip.is_file()

    # 4. Existing output file rejected when overwrite=False (default), allowed when overwrite=True
    valid_zip = ws_root / "exp-guard.odvstudio.zip"
    export_bundle(sdir, valid_zip, security_manager=sec)
    with pytest.raises(InvalidWorkspacePathError, match="overwrite=True"):
        export_bundle(sdir, valid_zip, security_manager=sec)
    export_bundle(sdir, valid_zip, security_manager=sec, overwrite=True)
    assert valid_zip.is_file()


def test_p08a2_maj04_historical_expired_waiver_bundle_roundtrip_and_post_import_guard(
    tmp_path: Path,
) -> None:
    """P08a2::MAJ-04: Historical waivers valid at commit time remain importable after wall-clock
    expiry, while post-import promotion blocks until re-waived (and commit-time-expired fails!).
    """
    from opendv_service.ontology.validator import UngroundedConceptError
    from opendv_service.studio.bundle import ImportGroundingViolationError

    ws_a = tmp_path / "ws_a"
    ws_b = tmp_path / "ws_b"
    sec_a = WorkspaceSecurityManager(studio_root=ws_a)
    sec_b = WorkspaceSecurityManager(studio_root=ws_b)
    mgr_a = StudioSessionManager(studio_root=ws_a, security_manager=sec_a)
    svc_a = AtomicCommitService(session_manager=mgr_a, security_manager=sec_a)

    meta_a = mgr_a.create_session("hist-waiver-sess")
    delta = OntologyDelta(
        delta_id="d-hist-wv",
        base_ontology_version="0.0.0",
        target_ontology_version="1.0.0",
        parent_sha256=meta_a.head_sha256,
        operations=[
            AddConceptOp(
                op_id="op-hist",
                concept_name="LegacyDeskFactor",
                concept_uri="urn:ontology:entity:legacy_desk_factor",
                target_hub="hub_legacy_desk_factor",
            )
        ],
    )

    # Issue waiver active for ~1.5s, commit while active, then let wall-clock pass expires_at!
    now_utc = datetime.now(UTC)
    wv_raw = StewardWaiverRecord(
        proposal_id=uuid.uuid4(),
        target_concept_uris=["urn:ontology:entity:legacy_desk_factor"],
        proposal_delta_sha256=delta.canonical_sha256(),
        base_sha256=meta_a.head_sha256,
        authorized_by="human:admin_a",
        authorized_role="admin",
        rationale="Short-lived waiver active at commit time for portability test.",
        created_at=now_utc - timedelta(seconds=5),
        expires_at=now_utc + timedelta(milliseconds=900),
        signature_or_token="0" * 64,
    )
    wv_signed = wv_raw.model_copy(
        update={"signature_or_token": sec_a.sign_waiver(wv_raw.signing_payload().encode("utf-8"))}
    )
    svc_a.commit_delta("hist-waiver-sess", delta, waivers=[wv_signed])

    # Wait until waiver is strictly expired relative to wall-clock datetime.now(UTC)
    time.sleep(1.1)
    assert wv_signed.expires_at is not None and wv_signed.expires_at <= datetime.now(UTC)

    hist_zip = tmp_path / "hist_waiver.odvstudio.zip"
    export_bundle(mgr_a.get_session_dir("hist-waiver-sess"), hist_zip, security_manager=sec_a)

    # 1. Same-workspace import succeeds WITHOUT acknowledge_unverified_import_attribution!
    imp_same = import_bundle(
        hist_zip,
        ws_a,
        security_manager=sec_a,
        new_session_id="hist-waiver-reimp-a",
        actor_role="editor",
    )
    assert imp_same.head_version == "1.0.0"

    # 2. Cross-workspace import with admin acknowledgement also succeeds!
    mgr_b = StudioSessionManager(studio_root=ws_b, security_manager=sec_b)
    svc_b = AtomicCommitService(session_manager=mgr_b, security_manager=sec_b)
    imp_b = import_bundle(
        hist_zip,
        ws_b,
        security_manager=sec_b,
        new_session_id="hist-waiver-reimp-b",
        actor_role="admin",
        acknowledge_unverified_import_attribution=True,
        import_rationale="Importing historical session whose waiver expired after commit.",
    )
    assert imp_b.head_version == "1.0.0"

    # 3. Post-import promotion on BOTH same-workspace and cross-workspace sessions blocks
    # with UngroundedConceptError because the waiver is expired at promotion time!
    with pytest.raises(UngroundedConceptError):
        svc_a.promote_to_workspace(
            "hist-waiver-reimp-a",
            tmp_path / "ws_a_contract.json",
            "",
            rationale="Attempt promotion with expired historical waiver.",
            actor_role="admin",
            allowed_workspace_roots=[tmp_path],
        )
    with pytest.raises(UngroundedConceptError):
        svc_b.promote_to_workspace(
            "hist-waiver-reimp-b",
            tmp_path / "ws_b_contract.json",
            "",
            rationale="Attempt promotion with expired historical waiver in ws_b.",
            actor_role="admin",
            allowed_workspace_roots=[tmp_path],
        )

    # 4. Verify audit.jsonl COMMITTED timestamp precedence: if waiver was ALREADY expired when
    # COMMITTED was logged, backdating meta.json created_at CANNOT bypass expiry!
    expired_before_commit_wv_raw = wv_raw.model_copy(
        update={
            "created_at": now_utc - timedelta(minutes=10),
            "expires_at": now_utc - timedelta(minutes=5),
        }
    )
    expired_before_commit_wv = expired_before_commit_wv_raw.model_copy(
        update={
            "signature_or_token": sec_a.sign_waiver(
                expired_before_commit_wv_raw.signing_payload().encode("utf-8")
            )
        }
    )
    backdated_zip = tmp_path / "backdated_meta.odvstudio.zip"
    with (
        zipfile.ZipFile(hist_zip, "r") as zin,
        zipfile.ZipFile(backdated_zip, "w", compression=zipfile.ZIP_DEFLATED) as zout,
    ):
        for info in zin.infolist():
            raw = zin.read(info.filename)
            if info.filename in ("versions/v1.0.0/meta.json", "versions/v1.0.0/metadata.json"):
                m_obj = json.loads(raw.decode("utf-8"))
                m_obj["created_at"] = (now_utc - timedelta(minutes=8)).isoformat()
                m_obj["waivers"] = [expired_before_commit_wv.model_dump(mode="json")]
                raw = json.dumps(m_obj).encode("utf-8")
            elif info.filename in (
                "versions/v1.0.0/waivers.json",
                "versions/v1.0.0/evaluation.json",
            ):
                raw = json.dumps([expired_before_commit_wv.model_dump(mode="json")]).encode("utf-8")
            zout.writestr(info, raw)

    with pytest.raises(ImportGroundingViolationError, match="not active at commit time"):
        import_bundle(
            backdated_zip,
            ws_a,
            security_manager=sec_a,
            new_session_id="imp-backdated-fail",
            actor_role="admin",
        )


def test_s2_r8_maj01_job_runners_prepare_evidence_for_agent_table_pseudonymization(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Regression S2-R8-MAJ-01: `_prepare_evidence_for_agent` delegates table masking to
    `Pseudonymizer.pseudonymize_table` so benign word superstrings ('Annual' with 'Ann'),
    `pseudo_<16hex>` overlaps ('cafe'), `table_id` secrets, and nested metadata are handled
    without false-positive crashes or plaintext secret leaks in `OpenDVError`.
    """
    from opendv_agent_harness.privacy import Pseudonymizer, validate_task_spec_privacy
    from opendv_contracts.dtos.ingestion import (
        EvidenceChunk,
        TableStructure,
        TaintClassification,
    )
    from opendv_service.studio.job_runners import _prepare_evidence_for_agent

    sec = WorkspaceSecurityManager(tmp_path)

    chk = EvidenceChunk(
        chunk_id="c-ann-1",
        document_id="d-ann-1",
        text="Annual Revenue reviewed by Ann (ann@secretcorp.example).",
        taint=TaintClassification.PUBLIC,
        metadata={"taint": "confidential", "sensitive_entities": ["Ann"]},
    )
    tbl = TableStructure(
        table_id="tbl_Ann_annual_summary",
        headers=["annual_revenue", "contact_email"],
        rows=[["1000", "Annual check by Ann at cafe (ops@secretcorp.example)"]],
        caption="Annual summary for Ann",
        taint=TaintClassification.CONFIDENTIAL,
        metadata={
            "sensitive_entities": ["Ann", "cafe", "pseudo"],
            "nested": {
                "sensitive_entities": ["ProjectMercury"],
                "owner": "Managed by ProjectMercury and Ann",
            },
        },
    )

    safe_chunks, safe_tables = _prepare_evidence_for_agent([chk], [tbl], sec)
    assert len(safe_chunks) == 1 and len(safe_tables) == 1
    stbl = safe_tables[0]
    assert stbl.metadata is not None and stbl.metadata["pseudonymized"] is True
    assert "sensitive_entities" not in stbl.metadata
    assert "sensitive_entities" not in stbl.metadata["nested"]
    assert "Annual" in stbl.rows[0][1]
    assert "ops@secretcorp.example" not in stbl.rows[0][1]
    assert "ann" not in stbl.table_id.lower().replace("annual", "")
    validate_task_spec_privacy(
        "Verify agent task payload",
        input_data={"chunks": safe_chunks, "tables": safe_tables},
    )

    # Simulate faulty pseudonymize_text leaving secret unmasked -> OpenDVError MUST NOT leak secret
    orig_ptext = Pseudonymizer.pseudonymize_text
    monkeypatch.setattr(
        Pseudonymizer,
        "pseudonymize_text",
        lambda self, text, terms: (
            text if "TopSecretClient" in text else orig_ptext(self, text, terms)
        ),
    )
    bad_tbl = TableStructure(
        table_id="tbl_TopSecretClient",
        headers=["client"],
        rows=[["TopSecretClient"]],
        taint=TaintClassification.CONFIDENTIAL,
        metadata={"sensitive_entities": ["TopSecretClient"]},
    )
    with pytest.raises(OpenDVError) as exc_info:
        _prepare_evidence_for_agent([], [bad_tbl], sec)
    assert exc_info.value.error_code == ErrorCode.CONFIDENTIAL_SOURCE_AGENT_ACCESS_FORBIDDEN
    assert "topsecretclient" not in str(exc_info.value).lower()


def test_r4_opus_p07a2_crash_after_committed_audit_rolls_session_json_forward(
    tmp_path: Path,
) -> None:
    """R4 Opus P07a2::MAJ-01 & MIN-02:
    If the process crashes after appending `COMMITTED` to `audit.jsonl` but before updating
    `session.json`, `reconcile_and_recover_session` rolls `session.json` forward without
    quarantining the committed version or appending a duplicate `COMMITTED` record; and a
    `PROMOTION_COMMITTED` tail record replays `promoted_versions` and `last_promoted_sha256`.
    """
    ws = tmp_path / "studio"
    sec = WorkspaceSecurityManager(studio_root=ws)
    mgr = StudioSessionManager(studio_root=ws, security_manager=sec)
    svc = AtomicCommitService(session_manager=mgr, security_manager=sec)

    meta0 = mgr.create_session("sess-crash-committed")
    sdir = mgr.get_session_dir("sess-crash-committed")
    stale_session_bytes = (sdir / "session.json").read_bytes()

    res = svc.commit_delta(
        "sess-crash-committed",
        OntologyDelta(
            delta_id="d-crash-1",
            base_ontology_version="0.0.0",
            target_ontology_version="1.0.0",
            parent_sha256=meta0.head_sha256,
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
    assert (sdir / "versions" / "v1.0.0" / "ontology.json").is_file()
    pre_audit_lines = (sdir / "audit.jsonl").read_text(encoding="utf-8").strip().splitlines()
    pre_committed_count = sum(
        1 for line in pre_audit_lines if json.loads(line).get("action") == "COMMITTED"
    )
    assert pre_committed_count == 1
    pending_rec = next(
        json.loads(line)
        for line in pre_audit_lines
        if json.loads(line).get("action") == "PENDING_COMMIT"
    )

    # Simulate crash in recovery Steps 4/5 right after COMMITTED is appended to audit.jsonl
    # (when session.json still holds v0.0.0 + PENDING_COMMIT's audit_chain_head_sha256)!
    stale_obj = json.loads(stale_session_bytes.decode("utf-8"))
    stale_obj["audit_chain_head_sha256"] = pending_rec["audit_sha256"]
    (sdir / "session.json").write_text(json.dumps(stale_obj, indent=2), encoding="utf-8")
    assert mgr.get_session("sess-crash-committed").head_version == "0.0.0"

    svc.reconcile_and_recover_session("sess-crash-committed")

    recovered = mgr.get_session("sess-crash-committed")
    assert recovered.head_version == "1.0.0"
    assert recovered.head_sha256 == res.target_sha256
    assert "v1.0.0" in recovered.committed_versions
    assert (sdir / "versions" / "v1.0.0" / "ontology.json").is_file()
    assert not (sdir / ".quarantine").exists()

    post_audit_lines = (sdir / "audit.jsonl").read_text(encoding="utf-8").strip().splitlines()
    post_committed_count = sum(
        1 for line in post_audit_lines if json.loads(line).get("action") == "COMMITTED"
    )
    assert post_committed_count == 1

    # Also test PROMOTION_COMMITTED tail replay when session.json was stale
    append_signed_audit_record(
        session_dir=sdir,
        action="PROMOTION_COMMITTED",
        actor="human:admin",
        target_version="v1.0.0",
        head_sha256=res.target_sha256,
        details={
            "target_version": "v1.0.0",
            "workspace_sha256": res.target_sha256,
        },
        security_manager=sec,
    )
    # Revert promoted_versions in session.json while keeping audit_chain_head_sha256
    cur_meta = json.loads((sdir / "session.json").read_text(encoding="utf-8"))
    cur_meta["promoted_versions"] = []
    cur_meta["last_promoted_sha256"] = None
    (sdir / "session.json").write_text(json.dumps(cur_meta, indent=2), encoding="utf-8")

    svc.reconcile_and_recover_session("sess-crash-committed")
    recovered_promo = mgr.get_session("sess-crash-committed")
    assert "v1.0.0" in recovered_promo.promoted_versions
    assert recovered_promo.last_promoted_sha256 == res.target_sha256


def test_r4_opus_p08a2_export_bundle_rejects_internal_symlinks_and_unset_env_escape(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """R4 Opus P08a2::MAJ-01 & MAJ-02:
    - `export_bundle` rejects symlinks/reparse points planted inside `session_dir` so external
      files (such as `.odv_workspace_secret`) can never be exfiltrated.
    - When `OPENDV_WORKSPACE_ROOTS` is unset, `export_bundle` with `security_manager` STILL
      rejects absolute output paths outside the workspace perimeter (even with `overwrite=True`).
    """
    import stat as std_stat

    from opendv_service.studio.bundle import ArchiveSecurityViolationError
    from opendv_service.studio.workspace_security import InvalidWorkspacePathError

    monkeypatch.delenv("OPENDV_WORKSPACE_ROOTS", raising=False)
    monkeypatch.delenv("OPENDV_STUDIO_ROOT", raising=False)
    monkeypatch.delenv("OPENDV_WORKSPACE_ROOT", raising=False)

    ws_root = tmp_path / "workspace_a"
    outside_dir = tmp_path / "outside_target"
    outside_dir.mkdir(parents=True, exist_ok=True)
    victim_file = outside_dir / "precious.txt"
    victim_file.write_text("do-not-clobber", encoding="utf-8")

    studio_root = ws_root / "studio"
    sec = WorkspaceSecurityManager(studio_root=studio_root)
    mgr = StudioSessionManager(studio_root=studio_root, security_manager=sec)
    mgr.create_session("sess-exp-sec")
    sdir = mgr.get_session_dir("sess-exp-sec")

    # 1. MAJ-02: With OPENDV_WORKSPACE_ROOTS unset, out-of-root path is rejected!
    monkeypatch.chdir(ws_root)
    with pytest.raises(InvalidWorkspacePathError):
        export_bundle(
            sdir,
            victim_file,
            security_manager=sec,
            overwrite=True,
        )
    assert victim_file.read_text(encoding="utf-8") == "do-not-clobber"

    # 2. MAJ-01: Planted symlink / reparse entry inside sdir/inputs/ is rejected!
    planted = sdir / "inputs" / "smuggled_secret.txt"
    planted.parent.mkdir(parents=True, exist_ok=True)
    secret_target = studio_root / ".odv_workspace_secret"
    created_real_symlink = False
    try:
        os.symlink(secret_target, planted)
        created_real_symlink = True
    except OSError:
        planted.write_text("placeholder", encoding="utf-8")

    valid_out = ws_root / "clean.odvstudio.zip"
    if created_real_symlink:
        with pytest.raises(ArchiveSecurityViolationError):
            export_bundle(sdir, valid_out, security_manager=sec)
        planted.unlink()
    else:
        real_lstat = os.lstat

        class _FakeSymlinkStat:
            def __init__(self, st: os.stat_result) -> None:
                self.st_mode = std_stat.S_IFLNK | 0o777
                self.st_size = st.st_size
                self.st_file_attributes = 0x0400

        def _patched_lstat(p: Any, *args: Any, **kwargs: Any) -> Any:
            st = real_lstat(p, *args, **kwargs)
            if Path(p) == planted:
                return _FakeSymlinkStat(st)
            return st

        monkeypatch.setattr(os, "lstat", _patched_lstat)
        with pytest.raises(ArchiveSecurityViolationError):
            export_bundle(sdir, valid_out, security_manager=sec)
        monkeypatch.setattr(os, "lstat", real_lstat)
        planted.unlink()

    assert not valid_out.exists()
    export_bundle(sdir, valid_out, security_manager=sec)
    assert valid_out.is_file()


def test_r4_opus_p08b_corrupt_manifest_or_taint_index_fails_closed(tmp_path: Path) -> None:
    """R4 Opus P08b::MAJ-01: Corrupt `taint_index.json` or `inputs/manifest.json` raises typed
    `CORRUPT_SESSION_RECORD` instead of failing open with empty privacy/tombstone sets.
    """
    from opendv_service.studio.job_runners import load_active_session_evidence

    ws = tmp_path / "studio"
    sec = WorkspaceSecurityManager(studio_root=ws)
    mgr = StudioSessionManager(studio_root=ws, security_manager=sec)
    mgr.create_session("sess-fail-closed")
    sdir = mgr.get_session_dir("sess-fail-closed")

    ev_dir = sdir / "evidence"
    ev_dir.mkdir(parents=True, exist_ok=True)
    chk = EvidenceChunk(
        chunk_id="c-1",
        document_id="doc-1",
        start_char=0,
        end_char=26,
        text="Confidential board minutes",
        taint=TaintClassification.PUBLIC,
        metadata={"source_id": "src-1"},
    )
    (ev_dir / "chunks.jsonl").write_text(chk.model_dump_json() + "\n", encoding="utf-8")

    # 1. Corrupt taint_index.json MUST raise CORRUPT_SESSION_RECORD (never return raw chunks!)
    taint_idx = sdir / "taint_index.json"
    taint_idx.write_text("{truncated-json", encoding="utf-8")
    with pytest.raises(OpenDVError) as exc_taint:
        load_active_session_evidence(sdir)
    assert exc_taint.value.error_code == ErrorCode.CORRUPT_SESSION_RECORD
    taint_idx.unlink()

    # 2. Corrupt inputs/manifest.json MUST raise CORRUPT_SESSION_RECORD!
    manifest_path = sdir / "inputs" / "manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(OpenDVError) as exc_man:
        load_active_session_evidence(sdir)
    assert exc_man.value.error_code == ErrorCode.CORRUPT_SESSION_RECORD
