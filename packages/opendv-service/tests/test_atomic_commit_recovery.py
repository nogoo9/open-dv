"""Unit and integration tests for Multi-File Atomic Commit, Crash Recovery & CAS GC.

Tests Task 4D.3 (#210):
- 6-step multi-file atomic commit protocol under L5 session lock.
- OCC precondition verification (delta.base_sha256 and If-Match header).
- Staged artifacts durability, directory fsync, and atomic rename.
- Proposal auto-transition to ACCEPTED upon commit.
- Startup crash recovery & reconciliation:
    - Incomplete staging directory cleanup and COMMIT_ABORTED logging.
    - Interrupted commit auto-reconciliation (orphan forward version committed).
    - Corrupt forward version quarantine.
    - Missing active version recovery from staging.
    - Staging cleanup safety invariant (never delete committed/active versions).
- Forward-only SHA-256 HMAC audit chain tamper detection (TAMPER_EVIDENT_CHAIN_CORRUPT).
- CAS mark-and-sweep garbage collection and deletion protection.
- Portable session bundle export and zero-trust Windows normalized extraction security.
"""

from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path
from typing import Any

import pytest

from opendv_contracts.dtos.ontology import (
    AddConceptOp,
    OntologyDelta,
)
from opendv_core.models.graph_a import (
    CanonicalBusinessConcept,
    ExternalMapping,
    GraphAOntology,
)
from opendv_core.ontology_hashing import compute_ontology_sha256, dumps_jcs_bytes
from opendv_service.studio.atomic_commit import (
    AtomicCommitService,
    CommitValidationError,
    ConcurrentModificationError,
    InvalidPreconditionHeaderError,
    SourceReferencedByCommittedVersionError,
    SourceReferencedByPendingProposalError,
    TamperEvidentChainCorruptError,
    delete_source,
    mark_and_sweep_cas,
    parse_semver_tuple,
)
from opendv_service.studio.bundle import (
    ArchiveSecurityViolationError,
    export_bundle,
    import_bundle,
)
from opendv_service.studio.cas_storage import ContentAddressableStorage
from opendv_service.studio.session_manager import (
    GENESIS_DRAFT_SHA256,
    StudioSessionManager,
)
from opendv_service.studio.workspace_security import (
    WorkspaceSecurityManager,
)

FIBO_PARTY_URI = "https://spec.edmcouncil.org/fibo/ontology/FND/Agreements/Contracts/ContractParty"
FIBO_ACCOUNT_URI = (
    "https://spec.edmcouncil.org/fibo/ontology/FBC/ProductsAndServices/ClientsAndAccounts/Account"
)


@pytest.fixture
def test_env(tmp_path: Path) -> dict[str, Any]:
    """Initialize a test environment with security manager, session manager, and service."""
    studio_root = tmp_path / "studio"
    studio_root.mkdir(parents=True, exist_ok=True)
    sec_mgr = WorkspaceSecurityManager(studio_root=studio_root)
    session_mgr = StudioSessionManager(studio_root=studio_root, security_manager=sec_mgr)
    commit_svc = AtomicCommitService(
        session_manager=session_mgr,
        security_manager=sec_mgr,
    )
    return {
        "root": tmp_path,
        "studio_root": studio_root,
        "sec_mgr": sec_mgr,
        "session_mgr": session_mgr,
        "commit_svc": commit_svc,
    }


def _make_genesis_delta(parent_sha: str = GENESIS_DRAFT_SHA256) -> OntologyDelta:
    """Helper to create an additive genesis delta from 0.0.0 to 1.0.0."""
    return OntologyDelta(
        delta_id="delta-genesis-1",
        base_ontology_version="0.0.0",
        target_ontology_version="1.0.0",
        parent_sha256=parent_sha,
        operations=[
            AddConceptOp(
                op_id="op-1",
                concept_name="Party",
                concept_uri="urn:ontology:entity:party",
                description="Autonomous legal or natural entity",
                synonyms=["Contract Party"],
                external_mappings=[
                    ExternalMapping(
                        scheme="fibo",
                        target_uri=FIBO_PARTY_URI,
                        predicate="skos:exactMatch",
                        confidence=0.95,
                    )
                ],
            )
        ],
    )


def _make_v110_delta(parent_sha: str) -> OntologyDelta:
    """Helper to create an additive delta from 1.0.0 to 1.1.0."""
    return OntologyDelta(
        delta_id="delta-evolve-2",
        base_ontology_version="1.0.0",
        target_ontology_version="1.1.0",
        parent_sha256=parent_sha,
        operations=[
            AddConceptOp(
                op_id="op-2",
                concept_name="Account",
                concept_uri="urn:ontology:entity:account",
                description="Financial account structure",
                external_mappings=[
                    ExternalMapping(
                        scheme="fibo",
                        target_uri=FIBO_ACCOUNT_URI,
                        predicate="skos:exactMatch",
                        confidence=0.95,
                    )
                ],
            )
        ],
    )


# ==============================================================================
# 1. 6-Step Multi-File Atomic Commit Protocol
# ==============================================================================


def test_atomic_commit_genesis_to_v100_success(test_env: dict[str, Any]) -> None:
    session_mgr: StudioSessionManager = test_env["session_mgr"]
    commit_svc: AtomicCommitService = test_env["commit_svc"]

    session = session_mgr.create_session("session-genesis-test")
    assert session.head_version == "0.0.0"
    assert session.committed_versions == []

    delta = _make_genesis_delta()
    result = commit_svc.commit_delta(
        session_id="session-genesis-test",
        delta=delta,
        author="architect",
        evaluation={"score": 0.95},
    )

    assert result.version == "v1.0.0"
    assert len(result.target_sha256) == 64

    # Verify session.json state
    updated_session = session_mgr.get_session("session-genesis-test")
    assert updated_session.head_version == "1.0.0"
    assert updated_session.head_sha256 == result.target_sha256
    assert "v1.0.0" in updated_session.committed_versions
    assert updated_session.audit_chain_head_sha256 == result.audit_head_sha256

    # Verify version directory artifacts
    sdir = session_mgr.get_session_dir("session-genesis-test")
    v_dir = sdir / "versions" / "v1.0.0"
    assert (v_dir / "ontology.json").exists()
    assert (v_dir / "ontology.yaml").exists()
    assert (v_dir / "delta.json").exists()
    assert (v_dir / "meta.json").exists()
    assert (v_dir / "evaluation.json").exists()

    # Verify meta.json content
    meta = json.loads((v_dir / "meta.json").read_text(encoding="utf-8"))
    assert meta["target_sha256"] == result.target_sha256
    assert meta["target_version"] == "v1.0.0"
    assert meta["author"] == "architect"

    # Verify audit.jsonl entries
    audit_lines = (sdir / "audit.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(audit_lines) == 3  # SESSION_CREATED, PENDING_COMMIT, COMMITTED
    last_entry = json.loads(audit_lines[-1])
    assert last_entry["action"] == "COMMITTED"
    assert last_entry["target_version"] == "v1.0.0"
    assert last_entry["audit_sha256"] == result.audit_head_sha256


def test_atomic_commit_sequential_lineage(test_env: dict[str, Any]) -> None:
    session_mgr: StudioSessionManager = test_env["session_mgr"]
    commit_svc: AtomicCommitService = test_env["commit_svc"]

    session_mgr.create_session("session-lineage")
    res1 = commit_svc.commit_delta("session-lineage", _make_genesis_delta())

    delta2 = _make_v110_delta(parent_sha=res1.target_sha256)
    res2 = commit_svc.commit_delta("session-lineage", delta2)

    assert res2.version == "v1.1.0"
    assert res2.parent_sha256 == res1.target_sha256

    session = session_mgr.get_session("session-lineage")
    assert session.head_version == "1.1.0"
    assert session.head_sha256 == res2.target_sha256
    assert "v1.0.0" in session.committed_versions
    assert "v1.1.0" in session.committed_versions


def test_atomic_commit_updates_proposal_to_accepted(test_env: dict[str, Any]) -> None:
    session_mgr: StudioSessionManager = test_env["session_mgr"]
    commit_svc: AtomicCommitService = test_env["commit_svc"]

    session_mgr.create_session("session-prop")
    sdir = session_mgr.get_session_dir("session-prop")

    # Create a pending proposal in proposals/
    proposals_dir = sdir / "proposals"
    prop_data = {
        "proposal_id": "prop-42",
        "status": "PENDING",
        "author": "agent-architect",
    }
    (proposals_dir / "prop-42.json").write_text(json.dumps(prop_data, indent=2), encoding="utf-8")

    commit_svc.commit_delta(
        "session-prop",
        _make_genesis_delta(),
        proposal_id="prop-42",
    )

    # Verify proposal status transitioned to ACCEPTED
    updated_prop = json.loads((proposals_dir / "prop-42.json").read_text(encoding="utf-8"))
    assert updated_prop["status"] == "ACCEPTED"


# ==============================================================================
# 2. OCC Check & Precondition Enforcement
# ==============================================================================


def test_occ_mismatch_raises_concurrent_modification_error(test_env: dict[str, Any]) -> None:
    session_mgr: StudioSessionManager = test_env["session_mgr"]
    commit_svc: AtomicCommitService = test_env["commit_svc"]

    session_mgr.create_session("session-occ")
    res1 = commit_svc.commit_delta("session-occ", _make_genesis_delta())

    # Create delta with bogus base hash
    bad_delta = _make_v110_delta(parent_sha="0" * 64)

    with pytest.raises(ConcurrentModificationError) as exc_info:
        commit_svc.commit_delta("session-occ", bad_delta)

    err = exc_info.value
    assert err.status_code == 412
    assert err.exit_code == 5
    assert "OCC check failed" in str(err)

    # Verify session state untouched
    session = session_mgr.get_session("session-occ")
    assert session.head_sha256 == res1.target_sha256


def test_if_match_header_validation(test_env: dict[str, Any]) -> None:
    session_mgr: StudioSessionManager = test_env["session_mgr"]
    commit_svc: AtomicCommitService = test_env["commit_svc"]

    session_mgr.create_session("session-if-match")
    res1 = commit_svc.commit_delta("session-if-match", _make_genesis_delta())

    delta2 = _make_v110_delta(parent_sha=res1.target_sha256)

    # Matching If-Match succeeds
    res2 = commit_svc.commit_delta(
        "session-if-match",
        delta2,
        if_match=f'"{res1.target_sha256}"',
    )
    assert res2.version == "v1.1.0"

    # Mismatched If-Match raises 412
    delta3 = OntologyDelta(
        delta_id="delta-3",
        base_ontology_version="1.1.0",
        target_ontology_version="1.2.0",
        parent_sha256=res2.target_sha256,
        operations=[
            AddConceptOp(
                op_id="op-3",
                concept_name="Instrument",
                concept_uri="urn:ontology:entity:instrument",
                description="Financial instrument",
            )
        ],
    )

    # Weak ETag is strictly rejected with HTTP 400 / exit 2 (INVALID_PRECONDITION_HEADER)
    with pytest.raises(InvalidPreconditionHeaderError) as weak_exc:
        commit_svc.commit_delta(
            "session-if-match",
            delta3,
            if_match=f'W/"{res2.target_sha256}"',
        )
    assert weak_exc.value.status_code == 400
    assert weak_exc.value.exit_code == 2

    # Strong stale ETag raises HTTP 412 / exit 5 (ConcurrentModificationError)
    with pytest.raises(ConcurrentModificationError) as exc_info:
        commit_svc.commit_delta(
            "session-if-match",
            delta3,
            if_match=f'"{res1.target_sha256}"',  # Stale strong ETag
        )

    assert exc_info.value.status_code == 412
    assert exc_info.value.exit_code == 5


def test_semver_downgrade_rejected(test_env: dict[str, Any]) -> None:
    session_mgr: StudioSessionManager = test_env["session_mgr"]
    commit_svc: AtomicCommitService = test_env["commit_svc"]

    session_mgr.create_session("session-semver")
    res1 = commit_svc.commit_delta("session-semver", _make_genesis_delta())

    # Try to commit version <= current head
    bad_delta = OntologyDelta.model_construct(
        delta_id="delta-downgrade",
        base_ontology_version="1.0.0",
        target_ontology_version="1.0.0",
        parent_sha256=res1.target_sha256,
        operations=[],
    )

    with pytest.raises(CommitValidationError) as exc_info:
        commit_svc.commit_delta("session-semver", bad_delta)

    assert exc_info.value.status_code == 422


# ==============================================================================
# 3. Crash Recovery & Reconciliation
# ==============================================================================


def test_crash_recovery_incomplete_staging_before_rename_is_aborted(
    test_env: dict[str, Any],
) -> None:
    """Interrupted before rename: staging directory left behind, PENDING_COMMIT in audit."""
    session_mgr: StudioSessionManager = test_env["session_mgr"]
    commit_svc: AtomicCommitService = test_env["commit_svc"]
    sec_mgr: WorkspaceSecurityManager = test_env["sec_mgr"]

    session_mgr.create_session("session-crash-staging")
    sdir = session_mgr.get_session_dir("session-crash-staging")
    session = session_mgr.get_session("session-crash-staging")

    # Simulate crash before Step 4 rename:
    # 1. PENDING_COMMIT logged to audit.jsonl
    target_sha = "a" * 64
    pending_entry = {
        "timestamp": "2026-11-10T00:00:00Z",
        "session_id": "session-crash-staging",
        "actor": "steward",
        "action": "PENDING_COMMIT",
        "target_version": "v1.0.0",
        "head_sha256": target_sha,
        "details": {"target_version": "v1.0.0", "target_sha256": target_sha},
        "prev_audit_sha256": session.audit_chain_head_sha256,
    }
    canon_bytes = dumps_jcs_bytes(pending_entry)
    pending_entry["audit_sha256"] = hashlib.sha256(canon_bytes).hexdigest().lower()
    pending_entry["hmac_signature"] = sec_mgr.sign_audit_entry(canon_bytes)

    with (sdir / "audit.jsonl").open("a", encoding="utf-8") as fp:
        fp.write(json.dumps(pending_entry) + "\n")

    # 2. Abandoned .staging directory
    staging_dir = sdir / ".staging-deadbeef"
    staging_dir.mkdir(parents=True, exist_ok=True)
    (staging_dir / "meta.json").write_text(
        json.dumps({"target_sha256": target_sha, "target_version": "v1.0.0"}), encoding="utf-8"
    )

    # Execute recovery (pass staging_grace_seconds=0.0 so brand-new test fixture dir is swept)
    commit_svc.reconcile_and_recover_session("session-crash-staging", staging_grace_seconds=0.0)

    # Staging directory must be cleaned up
    assert not staging_dir.exists()

    # Audit ledger should have COMMIT_ABORTED entry
    audit_lines = (sdir / "audit.jsonl").read_text(encoding="utf-8").splitlines()
    last_rec = json.loads(audit_lines[-1])
    assert last_rec["action"] == "COMMIT_ABORTED"
    assert last_rec["details"]["aborted_pending_sha256"] == target_sha


def test_crash_recovery_interrupted_after_rename_before_session_json(
    test_env: dict[str, Any],
) -> None:
    """Interrupted after rename: versions/v1.1.0 exists, but session.json not committed."""
    session_mgr: StudioSessionManager = test_env["session_mgr"]
    commit_svc: AtomicCommitService = test_env["commit_svc"]

    session_mgr.create_session("session-crash-post-rename")
    res1 = commit_svc.commit_delta("session-crash-post-rename", _make_genesis_delta())

    sdir = session_mgr.get_session_dir("session-crash-post-rename")

    # Build target ontology v1.1.0
    v1_ont = GraphAOntology.model_validate_json(
        (sdir / "versions" / "v1.0.0" / "ontology.json").read_text(encoding="utf-8")
    )
    v110_ont = GraphAOntology(
        ontology_version="1.1.0",
        domain_namespace=v1_ont.domain_namespace,
        enterprise_owner=v1_ont.enterprise_owner,
        canonical_business_concepts=[
            *v1_ont.canonical_business_concepts,
            CanonicalBusinessConcept(
                concept_name="Account",
                concept_uri="urn:ontology:entity:account",
                definition="Account entity",
            ),
        ],
        canonical_relationships=[],
        reference_enumerations=[],
    )
    target_sha = compute_ontology_sha256(v110_ont)

    # Create forward version directory versions/v1.1.0
    v110_dir = sdir / "versions" / "v1.1.0"
    v110_dir.mkdir(parents=True, exist_ok=True)
    (v110_dir / "ontology.json").write_bytes(
        dumps_jcs_bytes(v110_ont.model_dump(mode="json", exclude_none=True))
    )
    meta_record = {
        "target_sha256": target_sha,
        "parent_sha256": res1.target_sha256,
        "target_version": "v1.1.0",
        "proposal_id": "prop-crash",
    }
    (v110_dir / "meta.json").write_text(json.dumps(meta_record), encoding="utf-8")

    # Add proposal file
    prop_file = sdir / "proposals" / "prop-crash.json"
    prop_file.write_text(
        json.dumps({"proposal_id": "prop-crash", "status": "PENDING"}), encoding="utf-8"
    )

    # Record signed PENDING_COMMIT intent prior to crash after Step 4 rename
    from opendv_service.studio.session_manager import append_signed_audit_record

    append_signed_audit_record(
        session_dir=sdir,
        action="PENDING_COMMIT",
        actor="architect",
        target_version="v1.1.0",
        head_sha256=target_sha,
        details={
            "proposal_id": "prop-crash",
            "target_version": "v1.1.0",
            "target_sha256": target_sha,
            "parent_sha256": res1.target_sha256,
        },
        security_manager=test_env["sec_mgr"],
    )

    # Run recovery
    commit_svc.reconcile_and_recover_session("session-crash-post-rename")

    # Verify commit finalized!
    session = session_mgr.get_session("session-crash-post-rename")
    assert session.head_version == "1.1.0"
    assert session.head_sha256 == target_sha
    assert "v1.1.0" in session.committed_versions

    # Verify proposal accepted
    prop = json.loads(prop_file.read_text(encoding="utf-8"))
    assert prop["status"] == "ACCEPTED"


def test_crash_recovery_corrupt_forward_version_quarantined(
    test_env: dict[str, Any],
) -> None:
    session_mgr: StudioSessionManager = test_env["session_mgr"]
    commit_svc: AtomicCommitService = test_env["commit_svc"]

    session_mgr.create_session("session-corrupt-fwd")
    res1 = commit_svc.commit_delta("session-corrupt-fwd", _make_genesis_delta())

    sdir = session_mgr.get_session_dir("session-corrupt-fwd")

    # Create corrupt forward version directory
    v110_dir = sdir / "versions" / "v1.1.0"
    v110_dir.mkdir(parents=True, exist_ok=True)
    # Target sha in meta does not match ontology.json content
    (v110_dir / "meta.json").write_text(
        json.dumps({"target_sha256": "0" * 64, "parent_sha256": res1.target_sha256}),
        encoding="utf-8",
    )
    v1_ont = GraphAOntology.model_validate_json(
        (sdir / "versions" / "v1.0.0" / "ontology.json").read_text(encoding="utf-8")
    )
    (v110_dir / "ontology.json").write_bytes(
        dumps_jcs_bytes(v1_ont.model_dump(mode="json", exclude_none=True))
    )

    commit_svc.reconcile_and_recover_session("session-corrupt-fwd")

    # Forward directory must have been moved to quarantine
    assert not v110_dir.exists()
    quarantine_dirs = list((sdir / ".quarantine").glob("v1.1.0-*"))
    assert len(quarantine_dirs) == 1


def test_staging_cleanup_safety_invariant_preserves_committed(
    test_env: dict[str, Any],
) -> None:
    session_mgr: StudioSessionManager = test_env["session_mgr"]
    commit_svc: AtomicCommitService = test_env["commit_svc"]

    session_mgr.create_session("session-safety")
    res1 = commit_svc.commit_delta("session-safety", _make_genesis_delta())

    sdir = session_mgr.get_session_dir("session-safety")

    # Create staging dir matching committed version
    keep_staging = sdir / ".staging-keep"
    keep_staging.mkdir(parents=True, exist_ok=True)
    (keep_staging / "meta.json").write_text(
        json.dumps({"target_sha256": res1.target_sha256, "target_version": "v1.0.0"}),
        encoding="utf-8",
    )

    # Create truly abandoned staging dir
    abandon_staging = sdir / ".staging-abandon"
    abandon_staging.mkdir(parents=True, exist_ok=True)
    (abandon_staging / "meta.json").write_text(
        json.dumps({"target_sha256": "9" * 64, "target_version": "v9.9.9"}),
        encoding="utf-8",
    )

    commit_svc.reconcile_and_recover_session("session-safety", staging_grace_seconds=0.0)

    # keep_staging must be preserved!
    assert keep_staging.exists()
    # abandon_staging must be deleted!
    assert not abandon_staging.exists()


# ==============================================================================
# 4. Audit Chain HMAC Tampering & Corruption Detection
# ==============================================================================


def test_audit_chain_tamper_detected_raises_error(test_env: dict[str, Any]) -> None:
    session_mgr: StudioSessionManager = test_env["session_mgr"]
    commit_svc: AtomicCommitService = test_env["commit_svc"]

    session_mgr.create_session("session-tamper")
    commit_svc.commit_delta("session-tamper", _make_genesis_delta())

    sdir = session_mgr.get_session_dir("session-tamper")
    audit_path = sdir / "audit.jsonl"
    lines = audit_path.read_text(encoding="utf-8").splitlines()

    # Tamper with the details of the middle entry
    mid_entry = json.loads(lines[1])
    mid_entry["details"]["tampered"] = True
    lines[1] = json.dumps(mid_entry)
    audit_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    with pytest.raises(TamperEvidentChainCorruptError) as exc_info:
        commit_svc.reconcile_and_recover_session("session-tamper")

    assert exc_info.value.status_code == 422
    assert exc_info.value.exit_code == 4
    assert exc_info.value.error_code == "TAMPER_EVIDENT_CHAIN_CORRUPT"


def test_audit_chain_broken_hash_link_raises_error(test_env: dict[str, Any]) -> None:
    session_mgr: StudioSessionManager = test_env["session_mgr"]
    commit_svc: AtomicCommitService = test_env["commit_svc"]

    session_mgr.create_session("session-break")
    commit_svc.commit_delta("session-break", _make_genesis_delta())

    sdir = session_mgr.get_session_dir("session-break")
    audit_path = sdir / "audit.jsonl"
    lines = audit_path.read_text(encoding="utf-8").splitlines()

    # Break hash chaining in last record
    last_rec = json.loads(lines[-1])
    last_rec["prev_audit_sha256"] = "f" * 64
    lines[-1] = json.dumps(last_rec)
    audit_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

    with pytest.raises(TamperEvidentChainCorruptError):
        commit_svc.reconcile_and_recover_session("session-break")


# ==============================================================================
# 5. CAS Mark-and-Sweep Garbage Collection & Source Protection
# ==============================================================================


def test_cas_mark_and_sweep_purges_unreferenced_blobs(test_env: dict[str, Any]) -> None:
    session_mgr: StudioSessionManager = test_env["session_mgr"]
    session_mgr.create_session("session-gc")
    sdir = session_mgr.get_session_dir("session-gc")

    cas = ContentAddressableStorage(root_dir=sdir / "inputs")

    # Store 3 blobs
    sha1 = cas.put(b"source data 1 - active in manifest")
    sha2 = cas.put(b"source data 2 - referenced in chunk")
    sha_orphan = cas.put(b"unreferenced orphan blob")

    # 1. Register sha1 in inputs/manifest.json
    manifest_data = {"sources": [{"source_id": "src-1", "sha256": sha1, "tombstoned": False}]}
    (sdir / "inputs" / "manifest.json").write_text(json.dumps(manifest_data), encoding="utf-8")

    # 2. Register sha2 in evidence/chunks.jsonl
    chunk_data = {"chunk_id": "chk-1", "source_id": "src-2", "blob_sha256": sha2}
    (sdir / "evidence" / "chunks.jsonl").write_text(json.dumps(chunk_data) + "\n", encoding="utf-8")

    # Execute mark-and-sweep
    purged = mark_and_sweep_cas(sdir)

    assert sha_orphan in purged
    assert sha1 not in purged
    assert sha2 not in purged

    assert cas.has(sha1)
    assert cas.has(sha2)
    assert not cas.has(sha_orphan)


def test_delete_source_safety_guards(test_env: dict[str, Any]) -> None:
    session_mgr: StudioSessionManager = test_env["session_mgr"]
    session_mgr.create_session("session-src-prot")
    sdir = session_mgr.get_session_dir("session-src-prot")

    # Add active source to manifest
    manifest_data = {
        "sources": [{"source_id": "src-protected", "sha256": "a" * 64, "tombstoned": False}]
    }
    (sdir / "inputs" / "manifest.json").write_text(json.dumps(manifest_data), encoding="utf-8")

    # 1. Guard against pending proposal citation
    (sdir / "proposals" / "prop-1.json").write_text(
        json.dumps({"proposal_id": "prop-1", "status": "PENDING", "source_id": "src-protected"}),
        encoding="utf-8",
    )

    with pytest.raises(SourceReferencedByPendingProposalError) as exc1:
        delete_source(sdir, "src-protected")
    assert exc1.value.status_code == 422
    assert exc1.value.exit_code == 4

    # Remove pending proposal citation
    (sdir / "proposals" / "prop-1.json").unlink()

    # 2. Guard against committed version citation
    v_dir = sdir / "versions" / "v1.0.0"
    v_dir.mkdir(parents=True, exist_ok=True)
    (v_dir / "ontology.json").write_text(
        json.dumps({"cited_sources": ["src-protected"]}), encoding="utf-8"
    )

    with pytest.raises(SourceReferencedByCommittedVersionError) as exc2:
        delete_source(sdir, "src-protected")
    assert exc2.value.status_code == 422
    assert exc2.value.exit_code == 4


# ==============================================================================
# 6. Portable Session Bundle (.odvstudio.zip) Security & Portability
# ==============================================================================


def test_bundle_export_and_import_roundtrip(test_env: dict[str, Any]) -> None:
    session_mgr: StudioSessionManager = test_env["session_mgr"]
    commit_svc: AtomicCommitService = test_env["commit_svc"]
    sec_mgr: WorkspaceSecurityManager = test_env["sec_mgr"]

    session_mgr.create_session("session-export-test")
    res1 = commit_svc.commit_delta("session-export-test", _make_genesis_delta())

    sdir = session_mgr.get_session_dir("session-export-test")
    zip_dest = test_env["root"] / "export.odvstudio.zip"

    # Export bundle
    exported_path = export_bundle(sdir, zip_dest, security_manager=sec_mgr)
    assert exported_path.exists()

    # Verify exported zip structure
    with zipfile.ZipFile(exported_path, "r") as zf:
        namelist = zf.namelist()
        assert "bundle.json" in namelist
        assert "session.json" in namelist
        assert "versions/v1.0.0/ontology.json" in namelist
        assert ".session_secret" not in namelist
        assert ".session.lock" not in namelist

    # Import bundle into new session ID
    new_dir = test_env["studio_root"] / "session-imported"
    imported_meta = import_bundle(
        zip_path=exported_path,
        target_dir=new_dir,
        security_manager=sec_mgr,
        new_session_id="session-imported",
    )

    assert imported_meta.session_id == "session-imported"
    assert imported_meta.head_version == "1.0.0"
    assert imported_meta.head_sha256 == res1.target_sha256
    assert (new_dir / ".session_secret").exists()


def _valid_bundle_session_json(session_id: str) -> str:
    return json.dumps(
        {
            "session_id": session_id,
            "created_at": "2026-11-10T00:00:00Z",
            "updated_at": "2026-11-10T00:00:00Z",
            "head_version": "0.0.0",
            "head_sha256": "0" * 64,
            "audit_chain_head_sha256": "0" * 64,
            "committed_versions": [],
        }
    )


@pytest.mark.parametrize(
    "malicious_entry, expected_err",
    [
        ("../evil.txt", "Path traversal"),
        ("/root/evil.txt", "leading slash"),
        ("dir\\evil.txt", "backslash"),
        ("D:/evil.txt", "Windows drive prefix"),
        ("//evil-host/share/data.txt", "UNC prefix"),
        ("safe.txt:evil.stream", "NTFS ADS"),
        ("CON.txt", "Windows reserved device"),
        ("dir/aux.json", "Windows reserved device"),
        ("com1", "Windows reserved device"),
    ],
)
def test_bundle_import_rejects_malicious_paths(
    test_env: dict[str, Any], malicious_entry: str, expected_err: str
) -> None:
    sec_mgr: WorkspaceSecurityManager = test_env["sec_mgr"]
    zip_path = test_env["root"] / f"malicious_{abs(hash(malicious_entry))}.zip"

    # Create crafted malicious zip using ZipInfo to preserve raw string
    with zipfile.ZipFile(zip_path, mode="w") as zf:
        zf.writestr("session.json", _valid_bundle_session_json("target-bad"))
        zinfo = zipfile.ZipInfo()
        zinfo.filename = malicious_entry
        zf.writestr(zinfo, "malicious payload")

    target_dir = test_env["studio_root"] / "target-bad"

    with pytest.raises(ArchiveSecurityViolationError) as exc_info:
        import_bundle(zip_path, target_dir, sec_mgr)

    assert exc_info.value.status_code == 422
    assert exc_info.value.exit_code == 4
    assert expected_err in str(exc_info.value)


def test_bundle_import_rejects_symlink_entries(test_env: dict[str, Any]) -> None:
    sec_mgr: WorkspaceSecurityManager = test_env["sec_mgr"]
    zip_path = test_env["root"] / "symlink.zip"

    with zipfile.ZipFile(zip_path, mode="w") as zf:
        zf.writestr("session.json", _valid_bundle_session_json("target-symlink"))
        zinfo = zipfile.ZipInfo("link_entry.txt")
        # Set Unix symlink mode bits (0o120000)
        zinfo.external_attr = 0o120777 << 16
        zf.writestr(zinfo, "/etc/passwd")

    target_dir = test_env["studio_root"] / "target-symlink"

    with pytest.raises(ArchiveSecurityViolationError) as exc_info:
        import_bundle(zip_path, target_dir, sec_mgr)

    assert "symlink" in str(exc_info.value).lower()


def test_bundle_import_rejects_duplicate_normalized_collisions(
    test_env: dict[str, Any],
) -> None:
    sec_mgr: WorkspaceSecurityManager = test_env["sec_mgr"]
    zip_path = test_env["root"] / "casefold.zip"

    with zipfile.ZipFile(zip_path, mode="w") as zf:
        zf.writestr("session.json", _valid_bundle_session_json("target-casefold"))
        zf.writestr("folder/file.txt", "data 1")
        zf.writestr("folder/FILE.TXT", "data 2")

    target_dir = test_env["studio_root"] / "target-casefold"

    with pytest.raises(ArchiveSecurityViolationError) as exc_info:
        import_bundle(zip_path, target_dir, sec_mgr)

    assert "normalization" in str(exc_info.value).lower()


def test_bundle_import_rejects_zip_bomb_ratio(test_env: dict[str, Any]) -> None:
    sec_mgr: WorkspaceSecurityManager = test_env["sec_mgr"]
    zip_path = test_env["root"] / "zipbomb.zip"

    # 5 MB of zeroes compresses to < 5 KB (ratio > 1000:1)
    large_zeros = b"\x00" * (5 * 1024 * 1024)

    with zipfile.ZipFile(zip_path, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("session.json", _valid_bundle_session_json("target-bomb"))
        zf.writestr("bomb.bin", large_zeros)

    target_dir = test_env["studio_root"] / "target-bomb"

    with pytest.raises(ArchiveSecurityViolationError) as exc_info:
        import_bundle(zip_path, target_dir, sec_mgr)

    assert "ZIP bomb" in str(exc_info.value)


def test_semver_tuple_parsing() -> None:
    assert parse_semver_tuple("1.0.0") == (1, 0, 0)
    assert parse_semver_tuple("v2.15.3") == (2, 15, 3)
    assert parse_semver_tuple("0.0.1-rc.1") == (0, 0, 1)
    with pytest.raises(ValueError):
        parse_semver_tuple("not-a-version")
