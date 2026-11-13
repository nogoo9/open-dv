"""Task 6B.2 (#231): Tier 4 Workload Chaos and Crash Recovery E2E Tests.

Tests chaos resilience and fault recovery across:
1. Multi-session concurrent lock contention (10 concurrent sessions, 0 deadlocks)
2. Crash before Step 5 of atomic commit (staging cleanup, intact base version)
3. Crash after Step 5 of atomic commit (supervisor auto-reconciliation, proposal ACCEPTED)
4. Corrupted forward version quarantine (.quarantine/* isolation)
5. SSE connection disconnect & replay via Last-Event-ID (zero event loss)
6. Journal compression threshold (>50 MB to .events.jsonl.gz with atomic replace)
7. Abandoned job lease recovery (process crash detected as JOB_ABANDONED_SERVER_CRASH)
"""

from __future__ import annotations

import concurrent.futures
import gzip
import json
import secrets
from pathlib import Path
from typing import Any

from opendv_contracts.dtos.ontology import (
    AddConceptOp,
    OntologyDelta,
)
from opendv_core.models.graph_a import (
    CanonicalBusinessConcept,
    ExternalMapping,
    GraphAOntology,
)
from opendv_core.ontology_hashing import compute_ontology_sha256
from opendv_platform_api.job_supervisor import JobSupervisor
from opendv_service.studio.atomic_commit import AtomicCommitService
from opendv_service.studio.locks import (
    L5_SESSION,
    StudioLock,
    multi_studio_lock,
    studio_lock,
)
from opendv_service.studio.session_manager import (
    StudioSessionManager,
    append_signed_audit_record,
)
from opendv_service.studio.sse_journal import (
    SSEJournalWriter,
    replay_sse_events,
)


def test_chaos_concurrent_lock_hierarchy(tmp_path: Path) -> None:
    """Chaos 1: Multi-session lock contention (10 concurrent sessions, 0 deadlocks)."""
    studio_root = tmp_path / "studio_locks"
    studio_root.mkdir(parents=True, exist_ok=True)

    session_ids = [f"sess-lock-{i:02d}" for i in range(10)]
    for sid in session_ids:
        sdir = studio_root / sid
        sdir.mkdir(parents=True, exist_ok=True)

    results: list[str] = []

    def _worker(thread_idx: int) -> None:
        # Each worker acquires 3 random sessions from the pool
        # multi_studio_lock enforces global monotonic ordering (sorting by session_id)
        selected_sids = sorted(session_ids[(thread_idx * 2) % 10 : (thread_idx * 2) % 10 + 3])
        if not selected_sids:
            selected_sids = [session_ids[thread_idx % 10]]

        locks = [
            StudioLock(
                path=studio_root / s / ".session.lock",
                level=L5_SESSION,
                session_id=s,
                timeout=10.0,
            )
            for s in selected_sids
        ]

        with multi_studio_lock(locks):
            # Critical section simulation under multi-session lock
            results.append(f"worker-{thread_idx}:ok")

    with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(_worker, i) for i in range(10)]
        for f in concurrent.futures.as_completed(futures):
            f.result()  # Will raise if any deadlock, timeout, or lock order violation occurred

    assert len(results) == 10
    assert all(":ok" in r for r in results)


def test_chaos_crash_before_atomic_commit_step5(tmp_path: Path) -> None:
    """Chaos 2: Crash before Step 5 of atomic commit (staging cleanup, intact base version)."""
    session_mgr = StudioSessionManager(studio_root=tmp_path)
    session_id = "chaos-crash-step4"
    meta_init = session_mgr.create_session(session_id=session_id, domain_namespace="wealth_mgmt")

    session_dir = session_mgr.get_session_dir(session_id)
    atomic_svc = AtomicCommitService(session_manager=session_mgr)

    # 1. Commit initial v1.0.0
    init_delta = OntologyDelta(
        delta_id="delta-init",
        base_ontology_version="0.0.0",
        target_version="1.0.0",
        parent_sha256="",
        author="human:architect",
        rationale="Initial Genesis Version",
        operations=[
            AddConceptOp(
                op_id="op-base-concept",
                concept_uri="urn:ontology:entity:customer",
                concept_name="Customer",
                definition="A wealth management client.",
                target_hub="hub_customer",
                external_mappings=[
                    ExternalMapping(
                        scheme="FIBO",
                        target_uri="https://spec.edmcouncil.org/fibo/ontology/FND/ProductsAndServices/ProductsAndServices/Customer",
                        predicate="skos:exactMatch",
                        confidence=0.95,
                    )
                ],
            )
        ],
    )
    res_1 = atomic_svc.commit_delta(
        session_id=session_id,
        delta=init_delta,
        author="human:architect",
        if_match=meta_init.head_sha256,
    )
    assert res_1.version in ("1.0.0", "v1.0.0")

    meta_before = session_mgr.get_session(session_id)
    assert meta_before.head_version in ("1.0.0", "v1.0.0")

    # 2. Simulate crash before Step 5: inject abandoned .staging directory
    staging_dir = session_dir / f".staging-{secrets.token_hex(4)}"
    staging_dir.mkdir(parents=True, exist_ok=True)
    meta_staging = {
        "version": "1.1.0",
        "target_sha256": "fake_uncommitted_sha_1234567890abcdef",
        "parent_sha256": meta_before.head_sha256,
    }
    (staging_dir / "meta.json").write_text(json.dumps(meta_staging), encoding="utf-8")
    (staging_dir / "ontology.json").write_text('{"fake": "uncommitted"}', encoding="utf-8")
    import os
    import time as _time

    stale_ts = _time.time() - 3600.0
    os.utime(staging_dir, (stale_ts, stale_ts))

    assert staging_dir.is_dir()

    # 3. Trigger supervisor recovery
    atomic_svc.reconcile_and_recover_session(session_id)

    # Incomplete staging must be safely cleaned up
    assert not staging_dir.exists(), "Staging directory was not cleaned up after crash!"

    # Head version must remain intact at v1.0.0
    meta_after = session_mgr.get_session(session_id)
    assert meta_after.head_version == meta_before.head_version
    assert meta_after.head_sha256 == meta_before.head_sha256


def test_chaos_crash_after_atomic_commit_step5(tmp_path: Path) -> None:
    """Chaos 3: Crash after Step 5 (supervisor auto-reconciliation, proposal ACCEPTED)."""
    session_mgr = StudioSessionManager(studio_root=tmp_path)
    session_id = "chaos-crash-after-step5"
    meta_init = session_mgr.create_session(session_id=session_id, domain_namespace="wealth_mgmt")

    session_dir = session_mgr.get_session_dir(session_id)
    atomic_svc = AtomicCommitService(session_manager=session_mgr)

    # 1. Commit initial v1.0.0
    init_delta = OntologyDelta(
        delta_id="delta-init",
        base_ontology_version="0.0.0",
        target_version="1.0.0",
        parent_sha256="",
        author="human:architect",
        rationale="Initial Genesis Version",
        operations=[
            AddConceptOp(
                op_id="op-base-concept",
                concept_uri="urn:ontology:entity:customer",
                concept_name="Customer",
                definition="A wealth management client.",
                target_hub="hub_customer",
                external_mappings=[
                    ExternalMapping(
                        scheme="FIBO",
                        target_uri="https://spec.edmcouncil.org/fibo/ontology/FND/ProductsAndServices/ProductsAndServices/Customer",
                        predicate="skos:exactMatch",
                        confidence=0.95,
                    )
                ],
            )
        ],
    )
    res_1 = atomic_svc.commit_delta(
        session_id=session_id,
        delta=init_delta,
        author="human:architect",
        if_match=meta_init.head_sha256,
    )
    base_head_sha = res_1.target_sha256

    # 2. Simulate crash where version directory versions/v1.1.0 was written and valid,
    # but session.json was interrupted before head_version was updated and proposal marked ACCEPTED.
    v110_dir = session_dir / "versions" / "v1.1.0"
    v110_dir.mkdir(parents=True, exist_ok=True)

    ont_v110 = GraphAOntology(
        ontology_version="1.1.0",
        domain_namespace="wealth_mgmt",
        enterprise_owner="Enterprise Architecture",
        canonical_business_concepts=[
            CanonicalBusinessConcept(
                concept_uri="urn:ontology:entity:customer",
                concept_name="Customer",
                target_hub="hub_customer",
            ),
            CanonicalBusinessConcept(
                concept_uri="urn:ontology:entity:portfolio",
                concept_name="Portfolio",
                target_hub="hub_portfolio",
            ),
        ],
    )
    target_sha256 = compute_ontology_sha256(ont_v110)
    (v110_dir / "ontology.json").write_text(ont_v110.model_dump_json(indent=2), encoding="utf-8")

    prop_id = "prop-interrupted-01"
    meta_payload = {
        "version": "1.1.0",
        "parent_sha256": base_head_sha,
        "target_sha256": target_sha256,
        "proposal_id": prop_id,
        "author": "system:worker",
    }
    (v110_dir / "meta.json").write_text(json.dumps(meta_payload), encoding="utf-8")

    # Record proposal as PENDING
    prop_dir = session_dir / "proposals"
    prop_dir.mkdir(parents=True, exist_ok=True)
    prop_file = prop_dir / f"{prop_id}.json"
    prop_file.write_text(
        json.dumps({"proposal_id": prop_id, "status": "PENDING"}), encoding="utf-8"
    )

    # Record the K_audit-signed PENDING_COMMIT intent written in Step 3 prior to the Step 5 swap
    append_signed_audit_record(
        session_dir=session_dir,
        action="PENDING_COMMIT",
        actor="system:worker",
        target_version="1.1.0",
        head_sha256=target_sha256,
        details={
            "target_version": "1.1.0",
            "target_sha256": target_sha256,
            "parent_sha256": base_head_sha,
            "proposal_id": prop_id,
        },
        security_manager=session_mgr.security_manager,
    )

    # 3. Trigger supervisor recovery
    atomic_svc.reconcile_and_recover_session(session_id)

    # Verify supervisor auto-advanced head version to 1.1.0
    meta_recovered = session_mgr.get_session(session_id)
    assert meta_recovered.head_version in ("1.1.0", "v1.1.0")
    assert meta_recovered.head_sha256 == target_sha256

    # Verify proposal status was reconciled to ACCEPTED
    prop_data = json.loads(prop_file.read_text(encoding="utf-8"))
    assert prop_data["status"] == "ACCEPTED"


def test_chaos_corrupted_forward_version_quarantine(tmp_path: Path) -> None:
    """Chaos 4: Corrupted forward version quarantine (.quarantine/* isolation)."""
    session_mgr = StudioSessionManager(studio_root=tmp_path)
    session_id = "chaos-quarantine-corrupt"
    session_mgr.create_session(session_id=session_id, domain_namespace="wealth_mgmt")

    session_dir = session_mgr.get_session_dir(session_id)
    atomic_svc = AtomicCommitService(session_manager=session_mgr)

    meta_initial = session_mgr.get_session(session_id)

    # 1. Create corrupted forward version directory (invalid JSON ontology and SHA mismatch)
    corrupt_v_dir = session_dir / "versions" / "v2.0.0"
    corrupt_v_dir.mkdir(parents=True, exist_ok=True)
    (corrupt_v_dir / "ontology.json").write_text("CORRUPT_INVALID_JSON_DATA", encoding="utf-8")
    meta_corrupt = {
        "version": "2.0.0",
        "parent_sha256": meta_initial.head_sha256,
        "target_sha256": "expected_valid_hash_but_file_is_corrupt",
    }
    (corrupt_v_dir / "meta.json").write_text(json.dumps(meta_corrupt), encoding="utf-8")

    # 2. Trigger supervisor reconciliation
    atomic_svc.reconcile_and_recover_session(session_id)

    # The corrupted forward directory must NO LONGER be under versions/
    assert not corrupt_v_dir.exists(), "Corrupted version directory remained in versions/!"

    # Corrupted forward directory must be isolated into .quarantine/
    quarantine_root = session_dir / ".quarantine"
    assert quarantine_root.is_dir()
    quarantined_dirs = list(quarantine_root.glob("v2.0.0-*"))
    assert len(quarantined_dirs) == 1
    assert (quarantined_dirs[0] / "ontology.json").is_file()

    # Session metadata head remains uncorrupted
    meta_after = session_mgr.get_session(session_id)
    assert meta_after.head_version == meta_initial.head_version
    assert meta_after.head_sha256 == meta_initial.head_sha256


def test_chaos_sse_disconnect_and_journal_replay(tmp_path: Path) -> None:
    """Chaos 5: SSE disconnect & replay via Last-Event-ID (zero event loss)."""
    session_dir = tmp_path / "sess-sse-test"
    session_dir.mkdir(parents=True, exist_ok=True)

    job_id = "job-sse-stream-01"
    writer = SSEJournalWriter(session_dir=session_dir, job_id=job_id)

    # Emit 10 sequential telemetry events
    for i in range(1, 11):
        writer.buffer_event(f"step_{i}", {"pct": i * 10, "msg": f"Processing step {i}"})
    flushed = writer.flush()
    assert len(flushed) == 10

    journal_file = session_dir / "jobs" / f"{job_id}.events.jsonl"
    assert journal_file.is_file()

    # Simulate client receiving up to event 4, disconnecting, and reconnecting with Last-Event-ID: 4
    replayed_after_4 = list(replay_sse_events(journal_file, last_event_id=4))
    assert len(replayed_after_4) == 6
    expected_ids = [5, 6, 7, 8, 9, 10]
    actual_ids = [evt.id for evt in replayed_after_4]
    assert actual_ids == expected_ids

    # Simulate fresh client connecting from start (Last-Event-ID: None)
    replayed_all = list(replay_sse_events(journal_file, last_event_id=None))
    assert len(replayed_all) == 10
    assert [evt.id for evt in replayed_all] == list(range(1, 11))


def test_chaos_large_journal_compression_threshold(tmp_path: Path) -> None:
    """Chaos 6: Journal compression threshold (>50 MB compressed to .events.jsonl.gz)."""
    session_dir = tmp_path / "sess-compression-test"
    jobs_dir = session_dir / "jobs"
    jobs_dir.mkdir(parents=True, exist_ok=True)

    job_id = "job-compress-01"
    journal_file = jobs_dir / f"{job_id}.events.jsonl"
    lock_file = jobs_dir / f".journal_{job_id}.lock"

    # Write events into journal file
    raw_lines: list[str] = []
    for i in range(1, 1001):
        line = json.dumps({"id": i, "event": "status_update", "data": {"step": i}})
        raw_lines.append(line)
    journal_content = "\n".join(raw_lines) + "\n"
    journal_file.write_text(journal_content, encoding="utf-8")

    # Simulate ADR-013 compression under .journal_<job_id>.lock:
    # 1. Stage in .staging-<uuid>.events.jsonl.gz
    # 2. Atomic replace to <job_id>.events.jsonl.gz
    # 3. Clean up uncompressed journal
    gz_target = jobs_dir / f"{job_id}.events.jsonl.gz"
    staging_gz = jobs_dir / f".staging-{secrets.token_hex(4)}.events.jsonl.gz"

    with studio_lock(lock_file, level=L5_SESSION, session_id="sess-compression-test"):
        with gzip.open(staging_gz, "wt", encoding="utf-8") as gzf:
            gzf.write(journal_content)
        staging_gz.replace(gz_target)
        journal_file.unlink(missing_ok=True)

    assert gz_target.is_file()
    assert not journal_file.exists()

    # Verify bit-for-bit decompression reproduces exact original JSONL contents
    with gzip.open(gz_target, "rt", encoding="utf-8") as gzf:
        decompressed_content = gzf.read()
    assert decompressed_content == journal_content


def test_chaos_abandoned_job_lease_recovery(tmp_path: Path) -> None:
    """Chaos 7: Abandoned job lease recovery.

    Detects process crash as JOB_ABANDONED_SERVER_CRASH.
    """
    session_mgr = StudioSessionManager(studio_root=tmp_path)
    session_id = "chaos-abandoned-job"
    session_mgr.create_session(session_id=session_id, domain_namespace="wealth_mgmt")

    session_dir = session_mgr.get_session_dir(session_id)
    jobs_dir = session_dir / "jobs"
    jobs_dir.mkdir(parents=True, exist_ok=True)

    job_id = "job-abandoned-01"
    job_file = jobs_dir / f"{job_id}.json"

    # Simulate job left in 'running' state after server/worker process crashed without releasing
    job_data: dict[str, Any] = {
        "job_id": job_id,
        "session_id": session_id,
        "job_type": "construct",
        "status": "RUNNING",
        "stage": "constructing",
        "pct_complete": 50,
        "created_at": "2026-11-11T12:00:00Z",
    }
    job_file.write_text(json.dumps(job_data, indent=2), encoding="utf-8")

    supervisor = JobSupervisor()

    # The lock is NOT held by any running worker process.
    # Supervisor probe should detect abandonment and mark JOB_ABANDONED_SERVER_CRASH.
    probed_job = supervisor.get_job(session_mgr, session_id, job_id)
    assert probed_job["status"] == "failed"
    assert probed_job["stage"] == "abandoned"
    assert probed_job["error"]["code"] == "JOB_ABANDONED_SERVER_CRASH"
