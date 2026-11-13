"""Adversarial stress and concurrency verification test suite for Milestone M4 Quality Gate.

Authored by: challenger_m4_2
Empirically tests:
1. 5-level lock order enforcement and out-of-order rejection (LockOrderViolationError).
2. Multi-session tie-break ordering (min(s1, s2) -> max(s1, s2)).
3. MultiStudioLock canonical sorting and atomic rollback on failure.
4. LIFO release order and foreign session early release exception.
5. LockTimeoutError (code="LOCK_TIMEOUT", http_status=409, exit_code=6).
6. High-concurrency multi-session multi-thread stress (deadlock freedom).
7. Interrupted commit and crash recovery reconciliation.
8. Abandoned staging directory cleanup.
9. Corrupt orphan version quarantine and session head protection.
10. HMAC-signed audit chain tamper, signature corruption, hash-break, and truncation detection.
11. Adversarial session ID rejection across 46 vectors (traversal, windows devices, control chars).
12. Workspace secret RFC 5869 HKDF key derivation and fail-closed corrupt secret defense.
"""

from __future__ import annotations

import json
import random
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from opendv_core.models.graph_a import CanonicalBusinessConcept, GraphAOntology
from opendv_core.ontology_hashing import compute_ontology_sha256
from opendv_service.studio.atomic_commit import (
    AtomicCommitService,
    TamperEvidentChainCorruptError,
)
from opendv_service.studio.locks import (
    LockLevel,
    LockOrderViolationError,
    LockReleaseOrderViolationError,
    LockTimeoutError,
    MultiStudioLock,
    StudioLock,
    StudioLockError,
)
from opendv_service.studio.session_manager import (
    GENESIS_DRAFT_SHA256,
    GENESIS_VERSION,
    InvalidSessionIdentifierError,
    StudioSessionManager,
    validate_session_id,
)
from opendv_service.studio.workspace_security import (
    WorkspaceSecretCorruptError,
    WorkspaceSecurityManager,
)


class TestLockHierarchyEmpirical:
    """Empirical tests for 5-level total lock hierarchy and tie-breaking."""

    def test_monotonic_acquisition_and_inversion_rejection(self, tmp_path: Path) -> None:
        """Verify strict monotonic L1->L5 acquisition and immediate rejection of inversions."""
        l1 = StudioLock(tmp_path / ".local_token.lock", LockLevel.L1)
        l2 = StudioLock(tmp_path / ".job_1.lock", LockLevel.L2)
        l3 = StudioLock(tmp_path / ".journal_1.lock", LockLevel.L3)
        l4 = StudioLock(tmp_path / "graph_a.lock", LockLevel.L4)
        l5 = StudioLock(tmp_path / ".session.lock", LockLevel.L5, session_id="sess-a")

        # Monotonic acquisition L1 -> L2 -> L3 -> L4 -> L5 succeeds
        with l1, l2, l3, l4, l5:
            pass

        # Inversion: L5 before L4 raises LockOrderViolationError
        with l5, pytest.raises(LockOrderViolationError) as exc_info, l4:
            pass
        assert exc_info.value.code == "LOCK_ORDER_VIOLATION"
        assert exc_info.value.http_status == 409

        # Inversion: L3 before L2 raises LockOrderViolationError
        with l3, pytest.raises(LockOrderViolationError) as exc_info2, l2:
            pass
        assert exc_info2.value.code == "LOCK_ORDER_VIOLATION"

    def test_multi_session_tie_break(self, tmp_path: Path) -> None:
        """Verify lexicographical tie-break ordering min(s1, s2) -> max(s1, s2)."""
        l5_alpha = StudioLock(
            tmp_path / "alpha" / ".session.lock", LockLevel.L5, session_id="alpha"
        )
        l5_beta = StudioLock(tmp_path / "beta" / ".session.lock", LockLevel.L5, session_id="beta")

        # alpha -> beta succeeds
        with l5_alpha, l5_beta:
            pass

        # beta -> alpha fails
        with l5_beta, pytest.raises(LockOrderViolationError) as exc_info, l5_alpha:
            pass
        assert exc_info.value.code == "LOCK_ORDER_VIOLATION"

    def test_multi_studio_lock_auto_sort(self, tmp_path: Path) -> None:
        """Verify MultiStudioLock sorts arbitrary lock collections into canonical order."""
        l5_c = StudioLock(tmp_path / "c" / ".session.lock", LockLevel.L5, session_id="c")
        l5_b = StudioLock(tmp_path / "b" / ".session.lock", LockLevel.L5, session_id="b")
        l5_a = StudioLock(tmp_path / "a" / ".session.lock", LockLevel.L5, session_id="a")

        with MultiStudioLock([l5_c, l5_a, l5_b]) as acquired:
            acquired_sids = [lk.session_id for lk in acquired]
            assert acquired_sids == ["a", "b", "c"]

    def test_release_order_lifo_and_foreign_exception(self, tmp_path: Path) -> None:
        """Verify LIFO release order and foreign session early-release exception."""
        l1 = StudioLock(tmp_path / ".local_token.lock", LockLevel.L1)
        l2 = StudioLock(tmp_path / ".job_1.lock", LockLevel.L2)

        l1.acquire()
        l2.acquire()
        try:
            with pytest.raises(LockReleaseOrderViolationError):
                l1.release()
        finally:
            l2.release()
            l1.release()

        # Foreign session early release exception
        l5_foreign = StudioLock(
            tmp_path / "foreign" / ".session.lock",
            LockLevel.L5,
            session_id="foreign",
            is_foreign=True,
        )
        l5_local = StudioLock(
            tmp_path / "local" / ".session.lock",
            LockLevel.L5,
            session_id="local",
        )
        with l5_foreign, l5_local:
            l5_foreign.release()  # Permitted foreign early release

    def test_lock_timeout_across_threads(self, tmp_path: Path) -> None:
        """Verify LockTimeoutError raised with 409 status and exit code 6."""
        session_file = tmp_path / "sess-timeout" / ".session.lock"
        holder = StudioLock(session_file, LockLevel.L5, session_id="sess-timeout")
        holder.acquire()

        timeout_result: list[tuple[str, Any]] = []

        def contender_worker() -> None:
            try:
                contender = StudioLock(
                    session_file, LockLevel.L5, session_id="sess-timeout", timeout=0.25
                )
                contender.acquire()
                timeout_result.append(("FAIL", "Acquired lock"))
            except LockTimeoutError as exc:
                timeout_result.append(("PASS", exc))
            except (StudioLockError, OSError) as exc:
                timeout_result.append(("FAIL", exc))

        t = threading.Thread(target=contender_worker)
        t.start()
        t.join(timeout=5.0)
        assert not t.is_alive()
        holder.release()

        assert len(timeout_result) == 1
        status, exc = timeout_result[0]
        assert status == "PASS"
        assert exc.code == "LOCK_TIMEOUT"
        assert exc.http_status == 409
        assert exc.exit_code == 6

    def test_high_concurrency_deadlock_freedom(self, tmp_path: Path) -> None:
        """Stress-test multi-lock acquisitions across 8 threads for deadlock freedom."""
        sessions = ["s_alpha", "s_beta", "s_gamma", "s_delta"]
        for s in sessions:
            (tmp_path / s).mkdir(parents=True, exist_ok=True)

        errors: list[Any] = []
        completed_tx = [0]
        tx_lock = threading.Lock()

        def worker(worker_id: int) -> None:
            for i in range(15):
                chosen = sorted(random.sample(sessions, k=random.choice([2, 3])))
                shuffled = list(chosen)
                random.shuffle(shuffled)
                locks = [
                    StudioLock(
                        tmp_path / sid / ".session.lock",
                        LockLevel.L5,
                        session_id=sid,
                        timeout=10.0,
                    )
                    for sid in shuffled
                ]
                try:
                    with MultiStudioLock(locks, timeout=10.0) as acquired:
                        keys = [lk.order_key for lk in acquired]
                        for idx in range(len(keys) - 1):
                            assert keys[idx] < keys[idx + 1]
                        time.sleep(0.002)
                    with tx_lock:
                        completed_tx[0] += 1
                except (StudioLockError, OSError) as exc:
                    errors.append((worker_id, i, exc))

        threads = [threading.Thread(target=worker, args=(wid,)) for wid in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30.0)
            assert not t.is_alive(), "Worker thread deadlocked!"

        assert len(errors) == 0, f"Encountered concurrency errors: {errors}"
        assert completed_tx[0] == 8 * 15


class TestCrashRecoveryAndAuditHMAC:
    """Empirical tests for atomic commit crash recovery and HMAC tamper detection."""

    def test_incomplete_staging_cleanup(self, tmp_path: Path) -> None:
        """Verify abandoned staging directories from crashed commits are pruned cleanly."""
        studio_root = tmp_path / "studio"
        studio_root.mkdir()
        sec_mgr = WorkspaceSecurityManager(studio_root=studio_root)
        sess_mgr = StudioSessionManager(studio_root=studio_root, security_manager=sec_mgr)
        commit_svc = AtomicCommitService(session_manager=sess_mgr, security_manager=sec_mgr)

        sid = "recovery-staging-test"
        sess_mgr.create_session(session_id=sid, domain_namespace="test.rec")
        s_dir = sess_mgr.get_session_dir(sid)

        abandoned = s_dir / ".staging-crash-test"
        abandoned.mkdir()
        (abandoned / "partial.tmp").write_text("partial data", encoding="utf-8")
        (abandoned / "meta.json").write_text(
            json.dumps({"target_sha256": "orphan_sha"}), encoding="utf-8"
        )

        commit_svc.reconcile_and_recover_session(sid, staging_grace_seconds=0.0)
        assert not abandoned.exists()

    def test_interrupted_commit_forward_reconciliation(self, tmp_path: Path) -> None:
        """Verify signed PENDING_COMMIT forward version recovers; unauthenticated quarantines."""
        from opendv_service.studio.session_manager import append_signed_audit_record

        studio_root = tmp_path / "studio"
        studio_root.mkdir()
        sec_mgr = WorkspaceSecurityManager(studio_root=studio_root)
        sess_mgr = StudioSessionManager(studio_root=studio_root, security_manager=sec_mgr)
        commit_svc = AtomicCommitService(session_manager=sess_mgr, security_manager=sec_mgr)

        sid = "recovery-interrupted-test"
        init_meta = sess_mgr.create_session(session_id=sid, domain_namespace="test.rec")
        s_dir = sess_mgr.get_session_dir(sid)

        ont_100 = GraphAOntology(
            canonical_business_concepts=[
                CanonicalBusinessConcept(concept_uri="urn:ontology:entity:customer")
            ]
        )
        sha_100 = compute_ontology_sha256(ont_100)

        v1_dir = s_dir / "versions" / "v1.0.0"
        v1_dir.mkdir(parents=True, exist_ok=True)
        (v1_dir / "ontology.json").write_text(ont_100.model_dump_json(indent=2), encoding="utf-8")
        (v1_dir / "meta.json").write_text(
            json.dumps(
                {
                    "version": "1.0.0",
                    "target_sha256": sha_100,
                    "parent_sha256": init_meta.head_sha256,
                    "proposal_id": None,
                }
            ),
            encoding="utf-8",
        )
        # Write unauthenticated forward v2.0.0 with NO PENDING_COMMIT -> must be quarantined
        ont_200 = GraphAOntology(
            canonical_business_concepts=[
                CanonicalBusinessConcept(concept_uri="urn:ontology:entity:rogue")
            ]
        )
        sha_200 = compute_ontology_sha256(ont_200)
        v2_unauth_dir = s_dir / "versions" / "v2.0.0"
        v2_unauth_dir.mkdir(parents=True, exist_ok=True)
        (v2_unauth_dir / "ontology.json").write_text(
            ont_200.model_dump_json(indent=2), encoding="utf-8"
        )
        (v2_unauth_dir / "meta.json").write_text(
            json.dumps(
                {
                    "version": "2.0.0",
                    "target_sha256": sha_200,
                    "parent_sha256": sha_100,
                    "proposal_id": None,
                }
            ),
            encoding="utf-8",
        )

        # Authenticate v1.0.0 via signed PENDING_COMMIT in K_audit chain
        append_signed_audit_record(
            session_dir=s_dir,
            action="PENDING_COMMIT",
            actor="steward",
            target_version="v1.0.0",
            head_sha256=sha_100,
            details={
                "target_version": "v1.0.0",
                "target_sha256": sha_100,
                "parent_sha256": init_meta.head_sha256,
            },
            security_manager=sec_mgr,
        )

        commit_svc.reconcile_and_recover_session(sid)
        meta = sess_mgr.get_session(sid)
        assert meta.head_version == "1.0.0"
        assert meta.head_sha256 == sha_100
        assert "v1.0.0" in meta.committed_versions
        assert "v2.0.0" not in meta.committed_versions
        assert not v2_unauth_dir.exists()
        assert list((s_dir / ".quarantine").glob("v2.0.0-*"))

    def test_corrupt_orphan_version_quarantine(self, tmp_path: Path) -> None:
        """Verify a forward orphan version with invalid/corrupt data is quarantined."""
        studio_root = tmp_path / "studio"
        studio_root.mkdir()
        sec_mgr = WorkspaceSecurityManager(studio_root=studio_root)
        sess_mgr = StudioSessionManager(studio_root=studio_root, security_manager=sec_mgr)
        commit_svc = AtomicCommitService(session_manager=sess_mgr, security_manager=sec_mgr)

        sid = "recovery-quarantine-test"
        sess_mgr.create_session(session_id=sid, domain_namespace="test.rec")
        s_dir = sess_mgr.get_session_dir(sid)

        v2_dir = s_dir / "versions" / "v2.0.0"
        v2_dir.mkdir(parents=True, exist_ok=True)
        (v2_dir / "ontology.json").write_text(json.dumps({"invalid": "corrupt"}), encoding="utf-8")
        (v2_dir / "meta.json").write_text(
            json.dumps(
                {
                    "version": "2.0.0",
                    "target_sha256": "deadbeef0000",
                    "parent_sha256": GENESIS_DRAFT_SHA256,
                }
            ),
            encoding="utf-8",
        )

        commit_svc.reconcile_and_recover_session(sid)
        meta = sess_mgr.get_session(sid)
        assert meta.head_version == GENESIS_VERSION
        quarantined = list((s_dir / "versions").glob(".quarantine-*"))
        assert not v2_dir.exists() or len(quarantined) > 0

    def test_audit_chain_tamper_and_truncation_detection(self, tmp_path: Path) -> None:
        """Verify HMAC audit ledger detects tampering, bad signature, breaks, truncation."""
        studio_root = tmp_path / "studio"
        studio_root.mkdir()
        sec_mgr = WorkspaceSecurityManager(studio_root=studio_root)
        sess_mgr = StudioSessionManager(studio_root=studio_root, security_manager=sec_mgr)
        commit_svc = AtomicCommitService(session_manager=sess_mgr, security_manager=sec_mgr)

        sid = "audit-tamper-test"
        meta = sess_mgr.create_session(session_id=sid, domain_namespace="test.audit")
        s_dir = sess_mgr.get_session_dir(sid)
        audit_file = s_dir / "audit.jsonl"

        entry2 = commit_svc._append_raw_audit_entry_under_lock(
            session_dir=s_dir,
            session_id=sid,
            action="SOURCE_UPLOADED",
            target_version="0.0.0",
            head_sha256=meta.head_sha256,
            actor="tester",
            details={"file": "test.csv"},
            prev_audit_sha=meta.audit_chain_head_sha256,
        )
        meta.audit_chain_head_sha256 = entry2["audit_sha256"]
        (s_dir / "session.json").write_text(meta.model_dump_json(indent=2), encoding="utf-8")

        # Baseline: clean verification
        assert commit_svc._verify_audit_chain_or_raise(sid, meta) == entry2["audit_sha256"]
        original_lines = audit_file.read_text(encoding="utf-8").splitlines()

        # 1. Content tampering (modified actor)
        rec1 = json.loads(original_lines[1])
        rec1["actor"] = "attacker"
        audit_file.write_text(f"{original_lines[0]}\n{json.dumps(rec1)}\n", encoding="utf-8")
        with pytest.raises(TamperEvidentChainCorruptError, match="digest mismatch"):
            commit_svc.reconcile_and_recover_session(sid)

        # 2. Fake signature
        rec2 = json.loads(original_lines[1])
        rec2["hmac_signature"] = "0" * 64
        audit_file.write_text(f"{original_lines[0]}\n{json.dumps(rec2)}\n", encoding="utf-8")
        with pytest.raises(TamperEvidentChainCorruptError, match="HMAC signature verification"):
            commit_svc.reconcile_and_recover_session(sid)

        # 3. Hash break
        rec3 = json.loads(original_lines[1])
        rec3["prev_audit_sha256"] = "1" * 64
        audit_file.write_text(f"{original_lines[0]}\n{json.dumps(rec3)}\n", encoding="utf-8")
        with pytest.raises(TamperEvidentChainCorruptError, match="hash break"):
            commit_svc.reconcile_and_recover_session(sid)

        # 4. Truncation (missing anchor)
        audit_file.write_text(f"{original_lines[0]}\n", encoding="utf-8")
        with pytest.raises(TamperEvidentChainCorruptError, match="missing from audit chain"):
            commit_svc.reconcile_and_recover_session(sid)


class TestCASSessionSecurityAndHKDF:
    """Empirical tests for CAS session ID validation and HKDF key derivation."""

    @pytest.mark.parametrize(
        "invalid_sid",
        [
            "Session1",
            "sEssion",
            "SESSION",
            "sess 1",
            " sess",
            "sess ",
            "sess\t1",
            "sess\n1",
            "sess\r1",
            "sess\x001",
            "../session",
            "..\\session",
            "..",
            "session/sub",
            "session\\sub",
            "a/../b",
            "con",
            "CON",
            "prn",
            "aux",
            "nul",
            "com1",
            "com9",
            "lpt1",
            "lpt9",
            "con.txt",
            "aux.json",
            "-session",
            "_session",
            "bundle",
            "init",
            "sessions",
            "health",
            "auth",
            "admin",
            "prune",
            "import",
            "gc",
            "",
            "a" * 65,
            "sess@ion",
            "sess#ion",
            "sess$ion",
            "sess!ion",
            "sess~ion",
            "sess:ion",
        ],
    )
    def test_adversarial_session_ids_rejected(self, invalid_sid: str) -> None:
        """Verify strict rejection of 46 illegal session ID vectors."""
        with pytest.raises(InvalidSessionIdentifierError) as exc_info:
            validate_session_id(invalid_sid)
        assert exc_info.value.error_code == "INVALID_SESSION_IDENTIFIER"

    @pytest.mark.parametrize(
        "valid_sid",
        [
            "s1",
            "session-1",
            "session_1",
            "session-123_abc",
            "007-agent",
            "a" * 64,
        ],
    )
    def test_valid_session_ids_accepted(self, valid_sid: str) -> None:
        """Verify legitimate lowercase alphanumeric session IDs are accepted."""
        assert validate_session_id(valid_sid) == valid_sid

    def test_workspace_secret_hkdf_and_fail_closed(self, tmp_path: Path) -> None:
        """Verify RFC 5869 HKDF purpose-isolated keys and fail-closed defense."""
        mgr = WorkspaceSecurityManager(studio_root=tmp_path)
        secret_file = tmp_path / ".odv_workspace_secret"
        assert secret_file.exists()
        assert len(secret_file.read_bytes()) == 32

        keys = mgr.get_keys()
        assert len(keys.k_pseudo) == 32
        assert len(keys.k_audit) == 32
        assert len(keys.k_waiver) == 32
        assert len({keys.k_pseudo, keys.k_audit, keys.k_waiver}) == 3

        # Truncated secret (16 bytes) -> fails closed on init
        secret_file.write_bytes(b"x" * 16)
        with pytest.raises(WorkspaceSecretCorruptError) as exc_info:
            WorkspaceSecurityManager(studio_root=tmp_path)
        assert exc_info.value.error_code == "CORRUPT_SESSION_RECORD"

        # Empty secret (0 bytes) -> fails closed on init
        secret_file.write_bytes(b"")
        with pytest.raises(WorkspaceSecretCorruptError) as exc_info:
            WorkspaceSecurityManager(studio_root=tmp_path)
        assert exc_info.value.error_code == "CORRUPT_SESSION_RECORD"
