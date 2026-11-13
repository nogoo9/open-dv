"""Targeted regression tests for Round-3 Pre-Merge Studio Audit Remediations (Track R3-3).

Covers:
- `locks.py` (`P07b MAJ-01 & MAJ-02`):
  - Task- and thread-scoped `ContextVar` ownership so `asyncio.create_task` and `asyncio.to_thread`
    never treat a parent's `L5_SESSION` lock as reentrant or trigger false lock-order errors.
  - `MultiStudioLock` rollback of already-acquired locks on `BaseException`
    (`KeyboardInterrupt`, `asyncio.CancelledError`).
- `atomic_commit.py` & `session_manager.py` (`P07a MAJ-01..02`, `S2-CRIT-01`, `S3-CRIT-01`,
  `S3-MAJ-01`, `P08a MAJ-04`):
  - Default `enforce_governance=True`, `actor_role="editor"`, and `enforce_lineage=True`.
  - `append_signed_audit_record` tail truncation / missing `audit.jsonl` detection
    (`TAMPER_EVIDENT_CHAIN_CORRUPT`) and atomic repair preserving valid final records without `\n`.
  - `collect_touched_concept_uris` `relationship_name` lookup and admin-only waivers.
  - Greenfield workspace promotion ONT-EVO-002 re-derivation (`ws_grandfathered = set()`) and
    `PROMOTION_INTENT` recovery `promotion.json` persistence + containment validation.
  - Active proposal statuses (`None`, `"PROPOSED"`, `"PENDING"`, `"STALE"`) and `accepted_by` /
    plural `waivers` recording.
- `bundle.py` (`S3-CRIT-02`, `S3-MAJ-03`, `P08a MAJ-01..03`):
  - `target_dir` containment, non-double-nesting when `target_dir.name == session_id`, and
    `force=True` refusal on non-session directories lacking `session.json`.
  - Provisional URI scan (`IMPORT_PROVISIONAL_URI_FORBIDDEN`), post-root `delta.json` / `mode`
    lineage enforcement (`IMPORT_LINEAGE_BROKEN`), post-root ONT-EVO-002 grounding check
    (`IMPORT_GROUNDING_VIOLATION`), and grandfathered/seeded attribution acknowledgement gate.
- `job_runners.py` & `sse_journal.py` (`S3-CRIT-01`, `S3-MAJ-02`, `S3-MAJ-04`, `S2-CRIT-02`,
  `P08b MAJ-01..04`):
  - Canonical Studio proposal fields & alias-safe `OntologyDelta` re-validation.
  - Duplicate-hash tombstone safety across distinct `source_id` entries sharing a CAS blob.
  - Sticky `do_not_send_to_agent=True` filtering, `"CLEAN"` -> `PUBLIC` normalization, and
    `Pseudonymizer` redaction prior to `agent_runner`.
  - `NOOP_CONSTRUCT` string `audit_head_sha256` and concurrent head modification guard.
  - Atomic `.gz` SSE journal rotation, corrupt `.gz` resilience, and monotonic replay deduplication.
"""

from __future__ import annotations

import asyncio
import gzip
import json
import uuid
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from opendv_contracts.dtos.ingestion import TaintClassification
from opendv_contracts.dtos.ontology import (
    AddConceptOp,
    OntologyDelta,
    OntologyProposal,
    RemoveConceptOp,
    RemoveRelationshipOp,
    StewardWaiverRecord,
)
from opendv_contracts.errors import ErrorCode
from opendv_core.models.graph_a import (
    CanonicalBusinessConcept,
    CanonicalRelationship,
    ExternalMapping,
    GraphAOntology,
)
from opendv_core.ontology_hashing import compute_ontology_sha256
from opendv_service.ontology.validator import (
    InvalidGroundingWaiverError,
    UngroundedConceptError,
)
from opendv_service.studio.atomic_commit import (
    AtomicCommitService,
    ForbiddenAdminRequiredError,
    SourceReferencedByPendingProposalError,
    TamperEvidentChainCorruptError,
    collect_touched_concept_uris,
    delete_source,
)
from opendv_service.studio.bundle import (
    ImportGroundingRationaleRequiredError,
    ImportGroundingViolationError,
    ImportLineageBrokenError,
    ImportProvisionalUriForbiddenError,
    export_bundle,
    import_bundle,
)
from opendv_service.studio.job_runners import (
    StudioJobRunners,
    load_active_session_evidence,
)
from opendv_service.studio.locks import (
    L3_JOURNAL,
    L5_SESSION,
    LockTimeoutError,
    MultiStudioLock,
    StudioLock,
    get_held_locks,
    reentrant_session_lock,
    studio_lock,
)
from opendv_service.studio.session_manager import (
    StudioSessionManager,
    append_signed_audit_record,
)
from opendv_service.studio.sse_journal import SSEJournalWriter, replay_sse_events
from opendv_service.studio.workspace_security import (
    InvalidWorkspacePathError,
    WorkspaceSecurityManager,
)

FIBO_ACCOUNT_URI = (
    "https://spec.edmcouncil.org/fibo/ontology/FBC/ProductsAndServices/ClientsAndAccounts/Account"
)


def _account_mapping() -> ExternalMapping:
    return ExternalMapping(
        scheme="fibo",
        target_uri=FIBO_ACCOUNT_URI,
        predicate="skos:exactMatch",
        confidence=0.90,
    )


# ==============================================================================
# 1. Locks: Task/Thread ContextVar Isolation & BaseException Rollback
# ==============================================================================


@pytest.mark.asyncio
async def test_reentrant_session_lock_task_and_thread_isolation(tmp_path: Path) -> None:
    """P07b MAJ-01: Child tasks and threads do not inherit parent's held lock ownership."""
    sess_lock_path = tmp_path / ".session.lock"
    jnl_lock_path = tmp_path / "job.events.jsonl.lock"

    with reentrant_session_lock(sess_lock_path, session_id="s-iso", timeout=1.0):
        # Holds (.lifecycle_s-iso.lock, .session.lock) in strict L5 order
        assert len(get_held_locks()) == 2

        async def _child_task() -> None:
            # Child task must NOT see parent's L5 lock as owned in its task context
            assert get_held_locks() == ()
            # Acquiring L3 in child task must NOT trigger false LockOrderViolationError
            async with studio_lock(jnl_lock_path, level=L3_JOURNAL, timeout=0.5):
                assert len(get_held_locks()) == 1
            # Reentrant helper in child task must NOT bypass OS lock held by parent!
            with (
                pytest.raises(LockTimeoutError),
                reentrant_session_lock(sess_lock_path, session_id="s-iso", timeout=0.12),
            ):
                pass

        await asyncio.create_task(_child_task())

        def _worker_thread() -> None:
            assert get_held_locks() == ()
            with studio_lock(jnl_lock_path, level=L3_JOURNAL, timeout=0.5):
                assert len(get_held_locks()) == 1
            with (
                pytest.raises(LockTimeoutError),
                reentrant_session_lock(sess_lock_path, session_id="s-iso", timeout=0.12),
            ):
                pass

        await asyncio.to_thread(_worker_thread)


@pytest.mark.asyncio
async def test_multi_studio_lock_rolls_back_on_cancelled_error_and_keyboard_interrupt(
    tmp_path: Path,
) -> None:
    """P07b MAJ-02: BaseException (KeyboardInterrupt / CancelledError) releases acquired locks."""
    path_a = tmp_path / "a.lock"
    path_b = tmp_path / "b.lock"

    # 1. Synchronous rollback on KeyboardInterrupt
    lock_a1 = StudioLock(path_a, level=L3_JOURNAL, timeout=0.5)
    lock_b1 = StudioLock(path_b, level=L5_SESSION, session_id="s-1", timeout=0.5)
    with (
        patch.object(lock_b1, "acquire", side_effect=KeyboardInterrupt("simulated ctrl-c")),
        pytest.raises(KeyboardInterrupt),
    ):
        MultiStudioLock([lock_a1, lock_b1]).acquire()

    assert not lock_a1.is_locked
    with StudioLock(path_a, level=L3_JOURNAL, timeout=0.2) as check_a:
        assert check_a.is_locked

    # 2. Asynchronous rollback on asyncio.CancelledError
    lock_a2 = StudioLock(path_a, level=L3_JOURNAL, timeout=0.5)
    lock_b2 = StudioLock(path_b, level=L5_SESSION, session_id="s-1", timeout=0.5)
    with (
        patch.object(
            lock_b2, "acquire_async", side_effect=asyncio.CancelledError("simulated cancel")
        ),
        pytest.raises(asyncio.CancelledError),
    ):
        await MultiStudioLock([lock_a2, lock_b2]).acquire_async()

    assert not lock_a2.is_locked
    async with StudioLock(path_a, level=L3_JOURNAL, timeout=0.2) as check_a_async:
        assert check_a_async.is_locked


# ==============================================================================
# 2. Session Manager & Atomic Commit Remediations
# ==============================================================================


def test_atomic_commit_defaults_governance_true_and_editor_role(tmp_path: Path) -> None:
    """P07a MAJ-01 & S3-MAJ-01: Default enforce_governance=True, actor_role='editor', lineage."""
    studio_root = tmp_path / "studio"
    sec = WorkspaceSecurityManager(studio_root=studio_root)
    mgr = StudioSessionManager(studio_root=studio_root, security_manager=sec)
    svc = AtomicCommitService(session_manager=mgr, security_manager=sec)

    meta = mgr.create_session("sess-defaults")

    # 1. Ungrounded concept fails by default without passing enforce_governance=True
    ungrounded = OntologyDelta(
        delta_id="d-ungrounded",
        base_ontology_version="0.0.0",
        target_ontology_version="1.0.0",
        parent_sha256=meta.head_sha256,
        operations=[
            AddConceptOp(
                op_id="op-u1",
                concept_name="MysteryEntity",
                concept_uri="urn:ontology:entity:mystery",
                target_hub="hub_mystery",
            )
        ],
    )
    with pytest.raises(UngroundedConceptError):
        svc.commit_delta("sess-defaults", ungrounded)

    # 2. Grounded additive commit succeeds with default editor role
    grounded = OntologyDelta(
        delta_id="d-grounded",
        base_ontology_version="0.0.0",
        target_ontology_version="1.0.0",
        parent_sha256=meta.head_sha256,
        operations=[
            AddConceptOp(
                op_id="op-g1",
                concept_name="Account",
                concept_uri="urn:ontology:entity:account",
                target_hub="hub_account",
                external_mappings=[_account_mapping()],
            )
        ],
    )
    r1 = svc.commit_delta("sess-defaults", grounded)

    # 3. Destructive RemoveConceptOp fails by default because actor_role defaults to 'editor'
    rm_delta = OntologyDelta(
        delta_id="d-rm",
        base_ontology_version="1.0.0",
        target_ontology_version="2.0.0",
        parent_sha256=r1.target_sha256,
        operations=[RemoveConceptOp(op_id="op-rm", concept_uri="urn:ontology:entity:account")],
    )
    with pytest.raises(ForbiddenAdminRequiredError) as exc_rm:
        svc.commit_delta("sess-defaults", rm_delta)
    assert exc_rm.value.error_code == ErrorCode.FORBIDDEN_ADMIN_REQUIRED


def test_audit_chain_tail_truncation_missing_file_and_valid_unterminated_final_line(
    tmp_path: Path,
) -> None:
    """P08a MAJ-04 & P07a MAJ-02: Tail check on append + atomic repair preserving valid line."""
    studio_root = tmp_path / "studio"
    sec = WorkspaceSecurityManager(studio_root=studio_root)
    mgr = StudioSessionManager(studio_root=studio_root, security_manager=sec)
    svc = AtomicCommitService(session_manager=mgr, security_manager=sec)

    meta = mgr.create_session("sess-audit-guard")
    sdir = mgr.get_session_dir("sess-audit-guard")
    mgr.append_audit_entry("sess-audit-guard", "STEP_ONE", "0.0.0", meta.head_sha256)

    audit_file = sdir / "audit.jsonl"
    full_lines = audit_file.read_text(encoding="utf-8").splitlines()
    assert len(full_lines) == 2

    # 1. Strip trailing newline from a VALID final record -> reconcile keeps both records!
    audit_file.write_bytes(("\n".join(full_lines)).encode("utf-8"))
    assert not audit_file.read_bytes().endswith(b"\n")
    svc.reconcile_and_recover_session("sess-audit-guard")
    repaired_bytes = audit_file.read_bytes()
    assert repaired_bytes.endswith(b"\n")
    assert len(repaired_bytes.decode("utf-8").splitlines()) == 2
    assert mgr.verify_audit_chain("sess-audit-guard") is True

    # 2. Truncate the last complete record while session.json still points to it -> append fails!
    audit_file.write_text(full_lines[0] + "\n", encoding="utf-8")
    with pytest.raises(TamperEvidentChainCorruptError) as exc_trunc:
        append_signed_audit_record(
            session_dir=sdir,
            action="ILLEGAL_APPEND_AFTER_TRUNCATION",
            actor="human:editor",
            security_manager=sec,
        )
    assert exc_trunc.value.error_code == ErrorCode.TAMPER_EVIDENT_CHAIN_CORRUPT

    # 3. Delete audit.jsonl while metadata.audit_chain_head_sha256 is non-empty -> append fails!
    audit_file.unlink()
    with pytest.raises(TamperEvidentChainCorruptError):
        append_signed_audit_record(
            session_dir=sdir,
            action="ILLEGAL_APPEND_MISSING_FILE",
            actor="human:editor",
            security_manager=sec,
        )


def test_collect_touched_uris_relationship_name_and_admin_only_waivers(tmp_path: Path) -> None:
    """S2-CRIT-01: Relationship name endpoint resolution + admin-only StewardWaiverRecord."""
    studio_root = tmp_path / "studio"
    sec = WorkspaceSecurityManager(studio_root=studio_root)
    mgr = StudioSessionManager(studio_root=studio_root, security_manager=sec)
    svc = AtomicCommitService(session_manager=mgr, security_manager=sec)

    seed_ont = GraphAOntology(
        ontology_version="1.0.0",
        domain_namespace="finance",
        enterprise_owner="Platform",
        canonical_business_concepts=[
            CanonicalBusinessConcept(
                concept_uri="urn:ontology:entity: alpha".replace(" ", ""),
                concept_name="Alpha",
                target_hub="hub_alpha",
            ),
            CanonicalBusinessConcept(
                concept_uri="urn:ontology:entity:beta",
                concept_name="Beta",
                target_hub="hub_beta",
            ),
        ],
        canonical_relationships=[
            CanonicalRelationship(
                relationship_uri="urn:ontology:relationship:alpha_beta",
                relationship_name="alpha_links_beta",
                target_link="lnk_alpha_beta",
                endpoints=["urn:ontology:entity:alpha", "urn:ontology:entity:beta"],
            )
        ],
    )
    rel_rm_delta = OntologyDelta.model_construct(
        delta_id="d-rel-name",
        base_ontology_version="1.0.0",
        target_ontology_version="2.0.0",
        operations=[
            RemoveRelationshipOp.model_construct(
                op_type="remove_relationship",
                relationship_uri="urn:ontology:relationship:non_matching_uri",
                relationship_name="alpha_links_beta",
            )
        ],
    )
    touched = collect_touched_concept_uris(seed_ont, rel_rm_delta)
    assert touched == {"urn:ontology:entity:alpha", "urn:ontology:entity:beta"}

    # Waiver with authorized_role='steward' must be rejected (admin-only per master-plan.md)
    meta = mgr.create_session("sess-waiver-role")
    delta = OntologyDelta(
        delta_id="d-wv-steward",
        base_ontology_version="0.0.0",
        target_ontology_version="1.0.0",
        parent_sha256=meta.head_sha256,
        operations=[
            AddConceptOp(
                op_id="op-custom",
                concept_name="CustomAlphaSignal",
                concept_uri="urn:ontology:entity:custom_alpha",
                target_hub="hub_custom_alpha",
            )
        ],
    )
    wv_steward_raw = StewardWaiverRecord.model_construct(
        proposal_id=uuid.uuid4(),
        target_concept_uris=["urn:ontology:entity:custom_alpha"],
        proposal_delta_sha256=delta.canonical_sha256(),
        base_sha256=meta.head_sha256,
        authorized_by="human:steward_1",
        authorized_role="steward",
        rationale="Attempted non-admin steward waiver signature.",
        created_at=datetime.now(UTC) - timedelta(minutes=1),
        expires_at=None,
        signature_or_token="0" * 64,
    )
    sig = sec.sign_waiver(wv_steward_raw.signing_payload().encode("utf-8"))
    wv_steward = wv_steward_raw.model_copy(update={"signature_or_token": sig})

    with pytest.raises(InvalidGroundingWaiverError, match="admin"):
        svc.commit_delta("sess-waiver-role", delta, waivers=[wv_steward])


def test_promote_to_workspace_greenfield_grounding_gate_and_recovery_promotion_json(
    tmp_path: Path,
) -> None:
    """S3-MAJ-01: Greenfield promotion rejects ungrounded seeds; recovery writes promotion.json."""
    studio_root = tmp_path / "studio"
    sec = WorkspaceSecurityManager(studio_root=studio_root)
    mgr = StudioSessionManager(studio_root=studio_root, security_manager=sec)
    svc = AtomicCommitService(session_manager=mgr, security_manager=sec)

    # 1. Seed session with ungrounded concept -> cannot bypass ONT-EVO-002 on greenfield workspace!
    ungrounded_seed = GraphAOntology(
        ontology_version="1.0.0",
        domain_namespace="finance",
        enterprise_owner="Platform",
        canonical_business_concepts=[
            CanonicalBusinessConcept(
                concept_uri="urn:ontology:entity:ungrounded_seed",
                concept_name="UngroundedSeed",
                target_hub="hub_ungrounded_seed",
            )
        ],
    )
    mgr.create_session("sess-ungrounded-promo", initial_ontology=ungrounded_seed)
    ws_dir = tmp_path / "ws_greenfield"
    ws_dir.mkdir()
    ws_contract = ws_dir / "ontology.json"

    with pytest.raises(UngroundedConceptError):
        svc.promote_to_workspace(
            "sess-ungrounded-promo",
            ws_contract,
            "",
            rationale="Attempt to smuggle ungrounded seed into greenfield workspace.",
            actor_role="admin",
            allowed_workspace_roots=[ws_dir],
        )
    assert not ws_contract.exists()

    # 2. Simulate crash after writing target contract under PROMOTION_INTENT before promotion.json
    ws_root = tmp_path / "proj"
    ws_studio = ws_root / ".opendv" / "ontology_studio"
    sec2 = WorkspaceSecurityManager(studio_root=ws_studio)
    mgr2 = StudioSessionManager(studio_root=ws_studio, security_manager=sec2)
    svc2 = AtomicCommitService(session_manager=mgr2, security_manager=sec2)

    init2 = mgr2.create_session("sess-rec-promo")
    sdir2 = mgr2.get_session_dir("sess-rec-promo")
    r_ok = svc2.commit_delta(
        "sess-rec-promo",
        OntologyDelta(
            delta_id="d-ok",
            base_ontology_version="0.0.0",
            target_ontology_version="1.0.0",
            parent_sha256=init2.head_sha256,
            operations=[
                AddConceptOp(
                    op_id="op-acc",
                    concept_name="Account",
                    concept_uri="urn:ontology:entity:account",
                    target_hub="hub_account",
                    external_mappings=[_account_mapping()],
                )
            ],
        ),
    )

    target_ws_file = ws_root / "contracts" / "ontology.json"
    target_ws_file.parent.mkdir(parents=True, exist_ok=True)
    target_ws_file.write_bytes((sdir2 / "versions" / "v1.0.0" / "ontology.json").read_bytes())

    append_signed_audit_record(
        session_dir=sdir2,
        action="PROMOTION_INTENT",
        actor="human:admin",
        details={
            "workspace_path": str(target_ws_file),
            "target_version": "v1.0.0",
            "target_head_sha256": r_ok.target_sha256,
            "rationale": "Interrupted promotion recovery check.",
        },
        security_manager=sec2,
    )
    assert not (sdir2 / "promotion.json").exists()
    svc2.reconcile_and_recover_session("sess-rec-promo")
    assert (sdir2 / "promotion.json").is_file()
    promo_doc = json.loads((sdir2 / "promotion.json").read_text(encoding="utf-8"))
    assert promo_doc["promoted_version"] == "v1.0.0"
    assert promo_doc["workspace_sha256"] == r_ok.target_sha256


def test_proposal_lifecycle_statuses_and_recorded_metadata(tmp_path: Path) -> None:
    """S3-CRIT-01: Active proposal statuses include None/'PROPOSED'; records accepted_by/waivers."""
    studio_root = tmp_path / "studio"
    sec = WorkspaceSecurityManager(studio_root=studio_root)
    mgr = StudioSessionManager(studio_root=studio_root, security_manager=sec)
    svc = AtomicCommitService(session_manager=mgr, security_manager=sec)

    meta = mgr.create_session("sess-props")
    sdir = mgr.get_session_dir("sess-props")

    # Source referenced by a proposal whose status is None MUST block delete_source
    (sdir / "inputs" / "manifest.json").write_text(
        json.dumps({"sources": [{"source_id": "src-1", "sha256": "a" * 64, "tombstoned": False}]}),
        encoding="utf-8",
    )
    (sdir / "proposals" / "prop-none.json").write_text(
        json.dumps(
            {
                "proposal_id": "prop-none",
                "status": None,
                "source_id": "src-1",
                "base_sha256": meta.head_sha256,
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(SourceReferencedByPendingProposalError):
        delete_source(sdir, "src-1")

    (sdir / "proposals" / "prop-accept.json").write_text(
        json.dumps(
            {
                "proposal_id": "prop-accept",
                "status": "PROPOSED",
                "base_sha256": meta.head_sha256,
            }
        ),
        encoding="utf-8",
    )

    delta = OntologyDelta(
        delta_id="d-waive-prop",
        base_ontology_version="0.0.0",
        target_ontology_version="1.0.0",
        parent_sha256=meta.head_sha256,
        operations=[
            AddConceptOp(
                op_id="op-custom",
                concept_name="InternalMetric",
                concept_uri="urn:ontology:entity:internal_metric",
                target_hub="hub_internal_metric",
            )
        ],
    )
    wv_raw = StewardWaiverRecord(
        proposal_id=uuid.uuid4(),
        target_concept_uris=["urn:ontology:entity:internal_metric"],
        proposal_delta_sha256=delta.canonical_sha256(),
        base_sha256=meta.head_sha256,
        authorized_by="human:admin_1",
        authorized_role="admin",
        rationale="Approved internal metric concept for proposal test.",
        created_at=datetime.now(UTC) - timedelta(minutes=1),
        signature_or_token="0" * 64,
    )
    wv = wv_raw.model_copy(
        update={"signature_or_token": sec.sign_waiver(wv_raw.signing_payload().encode("utf-8"))}
    )

    svc.commit_delta(
        "sess-props",
        delta,
        author="human:chief_admin",
        actor_role="admin",
        proposal_id="prop-accept",
        waivers=[wv],
    )

    accepted_doc = json.loads((sdir / "proposals" / "prop-accept.json").read_text(encoding="utf-8"))
    assert accepted_doc["status"] == "ACCEPTED"
    assert accepted_doc["accepted_by"] == "human:chief_admin"
    assert isinstance(accepted_doc["waivers"], list) and len(accepted_doc["waivers"]) == 1

    stale_doc = json.loads((sdir / "proposals" / "prop-none.json").read_text(encoding="utf-8"))
    assert stale_doc["status"] == "STALE"


# ==============================================================================
# 3. Bundle Import Containment, Locking, Lineage & Post-Root Grounding
# ==============================================================================


def test_import_bundle_containment_non_double_nesting_and_force_safety(tmp_path: Path) -> None:
    """P08a MAJ-01..02: Containment check, non-double-nesting, and force=True non-session safety."""
    ws_a = tmp_path / "ws_a" / "studio"
    sec_a = WorkspaceSecurityManager(studio_root=ws_a)
    mgr_a = StudioSessionManager(studio_root=ws_a, security_manager=sec_a)
    svc_a = AtomicCommitService(session_manager=mgr_a, security_manager=sec_a)

    init_a = mgr_a.create_session("bundle-s1")
    svc_a.commit_delta(
        "bundle-s1",
        OntologyDelta(
            delta_id="d1",
            base_ontology_version="0.0.0",
            target_ontology_version="1.0.0",
            parent_sha256=init_a.head_sha256,
            operations=[
                AddConceptOp(
                    op_id="op1",
                    concept_name="Account",
                    concept_uri="urn:ontology:entity:account",
                    target_hub="hub_account",
                    external_mappings=[_account_mapping()],
                )
            ],
        ),
    )
    zip_path = ws_a.parent / "bundle-s1.odvstudio.zip"
    export_bundle(mgr_a.get_session_dir("bundle-s1"), zip_path, security_manager=sec_a)

    ws_b = tmp_path / "ws_b" / "studio"
    sec_b = WorkspaceSecurityManager(studio_root=ws_b)

    # 1. Path traversal in target_dir is rejected
    with pytest.raises(InvalidWorkspacePathError):
        import_bundle(zip_path, ws_b / ".." / "outside", security_manager=sec_b)

    # 2. Passing target_dir = ws_b / 'bundle-s1' does NOT double-nest ws_b/bundle-s1/bundle-s1
    exact_dest = ws_b / "bundle-s1"
    imported = import_bundle(zip_path, exact_dest, security_manager=sec_b)
    assert imported.session_id == "bundle-s1"
    assert (exact_dest / "session.json").is_file()
    assert not (exact_dest / "bundle-s1").exists()

    # 3. force=True on an arbitrary non-session directory (no session.json) refuses to delete it!
    non_session_dir = ws_b / "arbitrary-folder"
    non_session_dir.mkdir(parents=True)
    precious_file = non_session_dir / "important.txt"
    precious_file.write_text("do not delete", encoding="utf-8")

    with pytest.raises(InvalidWorkspacePathError):
        import_bundle(
            zip_path,
            non_session_dir,
            security_manager=sec_b,
            new_session_id="arbitrary-folder",
            force=True,
        )
    assert precious_file.read_text(encoding="utf-8") == "do not delete"


def test_import_bundle_provisional_uri_lineage_and_post_root_grounding_gates(
    tmp_path: Path,
) -> None:
    """S3-CRIT-02 & S3-MAJ-03: Provisional URIs, missing delta/seed smuggling, and ONT-EVO-002."""
    ws_a = tmp_path / "ws_a" / "studio"
    sec_a = WorkspaceSecurityManager(studio_root=ws_a)
    mgr_a = StudioSessionManager(studio_root=ws_a, security_manager=sec_a)
    svc_a = AtomicCommitService(session_manager=mgr_a, security_manager=sec_a)

    # Create seeded session with grandfathered CBC -> export -> import requires ack + rationale
    seed_ont = GraphAOntology(
        ontology_version="1.0.0",
        domain_namespace="finance",
        enterprise_owner="Enterprise",
        canonical_business_concepts=[
            CanonicalBusinessConcept(
                concept_uri="urn:ontology:entity:legacy_book",
                concept_name="LegacyBook",
                target_hub="hub_legacy_book",
            )
        ],
    )
    meta_seed = mgr_a.create_session("seed-export", initial_ontology=seed_ont)
    r_v110 = svc_a.commit_delta(
        "seed-export",
        OntologyDelta(
            delta_id="d-add-acc",
            base_ontology_version="1.0.0",
            target_ontology_version="1.1.0",
            parent_sha256=meta_seed.head_sha256,
            operations=[
                AddConceptOp(
                    op_id="op-acc",
                    concept_name="Account",
                    concept_uri="urn:ontology:entity:account",
                    target_hub="hub_account",
                    external_mappings=[_account_mapping()],
                )
            ],
        ),
    )
    seed_zip = ws_a.parent / "seed-export.odvstudio.zip"
    export_bundle(mgr_a.get_session_dir("seed-export"), seed_zip, security_manager=sec_a)

    ws_b = tmp_path / "ws_b" / "studio"
    sec_b = WorkspaceSecurityManager(studio_root=ws_b)

    # Grandfathered root requires attribution acknowledgement + >=10 char import_rationale
    with pytest.raises(ImportGroundingViolationError):
        import_bundle(seed_zip, ws_b, security_manager=sec_b, new_session_id="imp-seed-1")
    with pytest.raises(ImportGroundingRationaleRequiredError):
        import_bundle(
            seed_zip,
            ws_b,
            security_manager=sec_b,
            new_session_id="imp-seed-1",
            acknowledge_unverified_import_attribution=True,
            import_rationale="short",
        )
    imp_ok = import_bundle(
        seed_zip,
        ws_b,
        security_manager=sec_b,
        new_session_id="imp-seed-1",
        acknowledge_unverified_import_attribution=True,
        import_rationale="Verified workspace-seeded root lineage from Workspace A.",
    )
    assert imp_ok.head_sha256 == r_v110.target_sha256

    # Craft archive where post-root v1.1.0 deletes delta.json and claims mode='seed' -> rejected!
    missing_delta_zip = tmp_path / "missing_delta.odvstudio.zip"
    with (
        zipfile.ZipFile(seed_zip, "r") as zin,
        zipfile.ZipFile(missing_delta_zip, "w", compression=zipfile.ZIP_DEFLATED) as zout,
    ):
        for item in zin.infolist():
            if item.filename == "versions/v1.1.0/delta.json":
                continue
            raw = zin.read(item.filename)
            if item.filename in ("versions/v1.1.0/meta.json", "versions/v1.1.0/metadata.json"):
                m = json.loads(raw.decode("utf-8"))
                m["mode"] = "seed"
                raw = json.dumps(m).encode("utf-8")
            zout.writestr(item, raw)

    with pytest.raises(ImportLineageBrokenError) as lin_exc:
        import_bundle(
            missing_delta_zip,
            ws_b,
            security_manager=sec_b,
            new_session_id="imp-broken-lin",
            acknowledge_unverified_import_attribution=True,
            import_rationale="Attempting import of bundle missing post-root delta.json.",
        )
    assert lin_exc.value.error_code == ErrorCode.IMPORT_LINEAGE_BROKEN

    # Craft archive where post-root v1.0.0 adds an ungrounded concept with NO waiver -> rejected!
    svc_unguarded = AtomicCommitService(session_manager=mgr_a, security_manager=sec_a)
    m_ug = mgr_a.create_session("ug-post-root")
    ug_delta = OntologyDelta(
        delta_id="d-ug",
        base_ontology_version="0.0.0",
        target_ontology_version="1.0.0",
        parent_sha256=m_ug.head_sha256,
        operations=[
            AddConceptOp(
                op_id="op-ug",
                concept_name="ShadowConcept",
                concept_uri="urn:ontology:entity:shadow_concept",
                target_hub="hub_shadow",
            )
        ],
    )
    ug_wv_unsigned = StewardWaiverRecord(
        proposal_id=uuid.uuid4(),
        target_concept_uris=["urn:ontology:entity:shadow_concept"],
        proposal_delta_sha256=ug_delta.canonical_sha256(),
        base_sha256=m_ug.head_sha256,
        authorized_by="human:steward_a",
        authorized_role="admin",
        rationale="Temporary waiver stripped from exported bundle to test import rejection.",
        created_at=datetime.now(UTC) - timedelta(minutes=1),
        signature_or_token="0" * 64,
    )
    ug_wv = ug_wv_unsigned.model_copy(
        update={
            "signature_or_token": sec_a.sign_waiver(
                ug_wv_unsigned.signing_payload().encode("utf-8")
            )
        }
    )
    svc_unguarded.commit_delta("ug-post-root", ug_delta, waivers=[ug_wv])
    ug_v1_dir = mgr_a.get_session_dir("ug-post-root") / "versions" / "v1.0.0"
    (ug_v1_dir / "waivers.json").unlink(missing_ok=True)
    for meta_fname in ("meta.json", "metadata.json", "evaluation.json"):
        mf = ug_v1_dir / meta_fname
        if mf.is_file():
            m_obj = json.loads(mf.read_text(encoding="utf-8"))
            m_obj.pop("waivers", None)
            mf.write_text(json.dumps(m_obj, indent=2), encoding="utf-8")
    ug_zip = ws_a.parent / "ug.odvstudio.zip"
    export_bundle(mgr_a.get_session_dir("ug-post-root"), ug_zip, security_manager=sec_a)

    with pytest.raises(ImportGroundingViolationError) as ug_exc:
        import_bundle(
            ug_zip,
            ws_b,
            security_manager=sec_b,
            new_session_id="imp-ug",
            acknowledge_unverified_import_attribution=True,
            import_rationale="Even with ack, ungrounded concept without waiver must fail.",
        )
    assert ug_exc.value.error_code == ErrorCode.IMPORT_GROUNDING_VIOLATION

    # Craft archive containing provisional URI in versions/v1.0.0/ontology.json -> rejected!
    prov_zip = tmp_path / "prov.odvstudio.zip"
    with (
        zipfile.ZipFile(ug_zip, "r") as zin,
        zipfile.ZipFile(prov_zip, "w", compression=zipfile.ZIP_DEFLATED) as zout,
    ):
        for item in zin.infolist():
            raw = zin.read(item.filename)
            if item.filename == "versions/v1.0.0/ontology.json":
                raw = raw.replace(
                    b"urn:ontology:entity:shadow_concept",
                    b"urn:ontology:provisional:shadow_concept",
                )
            zout.writestr(item, raw)

    with pytest.raises(ImportProvisionalUriForbiddenError) as prov_exc:
        import_bundle(prov_zip, ws_b, security_manager=sec_b, new_session_id="imp-prov")
    assert prov_exc.value.error_code == ErrorCode.IMPORT_PROVISIONAL_URI_FORBIDDEN


# ==============================================================================
# 4. Job Runners & SSE Journal Remediations
# ==============================================================================


def test_job_runners_proposal_shape_duplicate_hash_tombstone_and_noop_audit_sha(
    tmp_path: Path,
) -> None:
    """S3-CRIT-01, P08b MAJ-01/03, S3-MAJ-04: Proposal shape, dup-hash safety, NOOP sha str."""
    studio_root = tmp_path / "studio"
    sec = WorkspaceSecurityManager(studio_root=studio_root)
    mgr = StudioSessionManager(studio_root=studio_root, security_manager=sec)
    runners = StudioJobRunners(session_manager=mgr)  # Exercises getattr(mgr, "security_manager")

    init = mgr.create_session("sess-jr")
    sdir = mgr.get_session_dir("sess-jr")
    cas = mgr.get_cas("sess-jr")

    # Two distinct sources sharing identical bytes; src-dup-deleted tombstoned, src-dup-live active
    csv_bytes = b"account_id,account_name\nA1,Operating\nA2,Reserve\n"
    shared_sha = cas.put(csv_bytes)
    (sdir / "inputs" / "manifest.json").write_text(
        json.dumps(
            {
                "files": [
                    {
                        "source_id": "src-dup-deleted",
                        "sha256": shared_sha,
                        "filename": "old_accounts.csv",
                        "tombstoned": True,
                    },
                    {
                        "source_id": "src-dup-live",
                        "sha256": shared_sha,
                        "filename": "live_accounts.csv",
                        "tombstoned": False,
                        "taint": "CLEAN",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    parse_res = runners.run_parse("sess-jr")
    assert parse_res["parsed_sources_count"] == 1
    _chunks, tables = load_active_session_evidence(sdir)
    assert len(tables) >= 1
    assert all((t.metadata or {}).get("source_id") == "src-dup-live" for t in tables)
    assert all(t.taint_classification == TaintClassification.PUBLIC for t in tables)

    # Construct without auto_commit -> inspect persisted canonical proposal fields
    prop_res = runners.run_construct("sess-jr", auto_commit=False, actor="human:editor")
    prop_id = prop_res["proposal_id"]
    prop_doc = json.loads((sdir / "proposals" / f"{prop_id}.json").read_text(encoding="utf-8"))

    for required_key in (
        "proposal_id",
        "session_id",
        "status",
        "base_version",
        "base_sha256",
        "proposer",
        "created_at",
        "operations",
        "staged_candidates",
        "rationale",
        "confidence_score",
        "diagnostics",
        "delta",
    ):
        assert required_key in prop_doc
    assert prop_doc["status"] == "PENDING"
    assert prop_doc["proposer"] == "deterministic:construct"
    assert prop_doc["triggered_by"] == "human:editor"
    assert prop_doc["base_sha256"] == init.head_sha256
    validated_delta = OntologyDelta.model_validate(prop_doc["delta"])
    assert validated_delta.base_sha256 == init.head_sha256

    # Commit v1.0.0 via deterministic auto_commit, then verify NOOP_CONSTRUCT returns str audit SHA
    c_res = runners.run_construct(
        "sess-jr",
        auto_commit=True,
        mode="deterministic",
        if_match=f'"{init.head_sha256}"',
        actor="human:admin",
        actor_role="admin",
    )
    noop_res = runners.run_construct(
        "sess-jr",
        auto_commit=True,
        mode="deterministic",
        if_match=f'"{c_res["target_sha256"]}"',
        actor="human:admin",
        actor_role="admin",
    )
    assert noop_res["status"] == "NOOP_CONSTRUCT"
    assert noop_res["auto_committed"] is False
    assert isinstance(noop_res["audit_head_sha256"], str)
    assert len(noop_res["audit_head_sha256"]) == 64


def test_job_runners_sticky_do_not_send_to_agent_and_pseudonymization(tmp_path: Path) -> None:
    """P08b MAJ-02 & S2-CRIT-02: Sticky do_not_send_to_agent + Pseudonymizer before LLM."""
    studio_root = tmp_path / "studio"
    sec = WorkspaceSecurityManager(studio_root=studio_root)
    mgr = StudioSessionManager(studio_root=studio_root, security_manager=sec)
    runners = StudioJobRunners(session_manager=mgr, security_manager=sec)

    mgr.create_session("sess-privacy")
    sdir = mgr.get_session_dir("sess-privacy")
    cas = mgr.get_cas("sess-privacy")

    secret_bytes = b"Top secret mergers strategy: Project Starlight internal memo.\n"
    pii_bytes = b"Customer contact email is alice.smith@example.com for account onboarding.\n"
    sha_secret = cas.put(secret_bytes)
    sha_pii = cas.put(pii_bytes)

    (sdir / "inputs" / "manifest.json").write_text(
        json.dumps(
            {
                "files": [
                    {
                        "source_id": "src-blocked",
                        "sha256": sha_secret,
                        "filename": "secret_memo.txt",
                        "tombstoned": False,
                        "taint": "CLEAN",
                        "do_not_send_to_agent": False,
                    },
                    {
                        "source_id": "src-tainted-pii",
                        "sha256": sha_pii,
                        "filename": "customer_notes.txt",
                        "tombstoned": False,
                        "taint": "TAINTED",
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    # Mark src-blocked in taint_index.json (sticky even though manifest said CLEAN/False!)
    (sdir / "taint_index.json").write_text(
        json.dumps({"do_not_send_to_agent": ["src-blocked"]}),
        encoding="utf-8",
    )

    runners.run_parse("sess-privacy")
    loaded_chunks, _ = load_active_session_evidence(sdir)
    blocked_chunks = [
        c for c in loaded_chunks if (c.metadata or {}).get("source_id") == "src-blocked"
    ]
    assert blocked_chunks
    assert all(c.taint_classification == TaintClassification.CONFIDENTIAL for c in blocked_chunks)
    assert all((c.metadata or {}).get("do_not_send_to_agent") is True for c in blocked_chunks)

    captured_chunks: list[Any] = []

    def _fake_agent_runner(
        *,
        base_ontology: GraphAOntology,
        evidence_chunks: list[Any],
        tables: list[Any],
        **kwargs: Any,
    ) -> OntologyProposal:
        captured_chunks.extend(evidence_chunks)
        return OntologyProposal(
            proposal_id="prop-agent-1",
            delta=OntologyDelta(
                delta_id="d-agent-1",
                base_ontology_version=base_ontology.ontology_version,
                target_ontology_version="1.0.0",
                parent_sha256=compute_ontology_sha256(base_ontology),
                operations=[
                    AddConceptOp(
                        op_id="op-a1",
                        concept_name="Account",
                        concept_uri="urn:ontology:entity:account",
                        target_hub="hub_account",
                        external_mappings=[_account_mapping()],
                    )
                ],
            ),
            rationale="Synthesized from pseudonymized customer notes.",
            confidence_score=0.88,
        )

    agent_runners = StudioJobRunners(
        session_manager=mgr, security_manager=sec, agent_runner=_fake_agent_runner
    )
    agent_runners.run_construct(
        "sess-privacy",
        mode="agent",
        auto_commit=False,
        actor="human:editor",
    )

    assert len(captured_chunks) >= 1
    for chk in captured_chunks:
        assert "Project Starlight" not in chk.text
        assert (chk.metadata or {}).get("source_id") != "src-blocked"
        assert "alice.smith@example.com" not in chk.text
        assert "pseudo_" in chk.text
        assert (chk.metadata or {}).get("pseudonymized") is True


def test_sse_journal_atomic_rotation_corrupt_gz_tolerance_and_dedup(tmp_path: Path) -> None:
    """S3-MAJ-02: Atomic .gz rotation, corrupt .gz tolerance, and monotonic replay deduplication."""
    sdir = tmp_path / "sess-sse"
    writer = SSEJournalWriter(sdir, job_id="job-rot", max_segment_bytes=200)

    for idx in range(6):
        writer.buffer_event("progress", {"step": idx, "padding": "x" * 60})
        writer.flush()

    gz_segments = sorted((sdir / "jobs").glob("job-rot.events.jsonl.*.gz"))
    assert len(gz_segments) >= 1
    assert not list((sdir / "jobs").glob(".*.tmp"))

    replayed = list(replay_sse_events(writer.journal_file))
    assert [e.id for e in replayed] == [1, 2, 3, 4, 5, 6]

    # Prepend last rotated event into active journal to simulate rotation overlap -> deduplicated!
    existing_active = writer.journal_file.read_text(encoding="utf-8")
    first_active_id = json.loads(existing_active.splitlines()[0])["id"]
    prev_evt = next(e for e in replayed if e.id == first_active_id - 1)
    dup_line = json.dumps(prev_evt.to_dict()) + "\n"
    writer.journal_file.write_text(dup_line + existing_active, encoding="utf-8")
    deduped = list(replay_sse_events(writer.journal_file))
    assert [e.id for e in deduped] == [1, 2, 3, 4, 5, 6]

    # Corrupt first .gz segment with truncated garbage -> replay tolerates EOFError/zlib.error!
    gz_segments[0].write_bytes(gzip.compress(b'{"id": 1, "event": "ok", "data": {}}\n')[:12])
    survived = list(replay_sse_events(writer.journal_file))
    assert survived[-1].id == 6


def test_r4_foreign_waiver_resign_promotion_and_force_import_admin_guard(tmp_path: Path) -> None:
    """S3-R4-MAJ-01..02 & MIN-02: Re-signed foreign waivers pass promotion; force=True admin."""
    ws_a = tmp_path / "ws_a" / "studio"
    sec_a = WorkspaceSecurityManager(studio_root=ws_a)
    mgr_a = StudioSessionManager(studio_root=ws_a, security_manager=sec_a)
    svc_a = AtomicCommitService(session_manager=mgr_a, security_manager=sec_a)

    meta_a = mgr_a.create_session("foreign-waiver-sess", domain_namespace="general")
    delta_a = OntologyDelta(
        delta_id="d-waived-a",
        base_ontology_version="0.0.0",
        target_ontology_version="1.0.0",
        parent_sha256=meta_a.head_sha256,
        operations=[
            AddConceptOp(
                op_id="op-custom",
                concept_name="CustomProprietaryEntity",
                concept_uri="urn:ontology:entity:custom_prop",
                target_hub="hub_custom_prop",
            )
        ],
    )
    delta_sha = (
        __import__("hashlib")
        .sha256(
            __import__(
                "opendv_core.ontology_hashing", fromlist=["dumps_jcs_bytes"]
            ).dumps_jcs_bytes(delta_a.model_dump(mode="json", exclude_none=True))
        )
        .hexdigest()
    )
    prop_uuid = str(uuid.uuid4())
    waiver_a = StewardWaiverRecord(
        waiver_id=str(uuid.uuid4()),
        proposal_id=prop_uuid,
        proposal_delta_sha256=delta_sha,
        target_concept_uris=["urn:ontology:entity:custom_prop"],
        base_sha256=meta_a.head_sha256,
        rationale="Approved bespoke proprietary concept in Workspace A.",
        authorized_by="human:admin_a",
        authorized_role="admin",
        signature_or_token="pending",
    )
    sig_a = sec_a.sign_waiver(waiver_a.signing_payload().encode("utf-8"))
    waiver_a = waiver_a.model_copy(update={"signature_or_token": sig_a})

    svc_a.commit_delta(
        "foreign-waiver-sess",
        delta_a,
        proposal_id=prop_uuid,
        author="human:admin_a",
        actor_role="admin",
        waivers=[waiver_a],
    )

    out_zip = ws_a.parent / "foreign-waiver.odvstudio.zip"
    export_bundle(mgr_a.get_session_dir("foreign-waiver-sess"), out_zip, security_manager=sec_a)

    # Workspace B has an independent K_waiver
    ws_b_root = tmp_path / "ws_b"
    ws_b = ws_b_root / "studio"
    sec_b = WorkspaceSecurityManager(studio_root=ws_b)
    mgr_b = StudioSessionManager(studio_root=ws_b, security_manager=sec_b)
    svc_b = AtomicCommitService(session_manager=mgr_b, security_manager=sec_b)

    # Non-admin actor_role rejected on acknowledge_unverified_import_attribution or force=True
    with pytest.raises(ForbiddenAdminRequiredError):
        import_bundle(
            out_zip,
            ws_b,
            security_manager=sec_b,
            acknowledge_unverified_import_attribution=True,
            acknowledgement_rationale="Editor attempting foreign waiver acknowledgement.",
            actor_role="editor",
        )

    imp_b = import_bundle(
        out_zip,
        ws_b,
        security_manager=sec_b,
        new_session_id="imp-waived-b",
        acknowledge_unverified_import_attribution=True,
        acknowledgement_rationale="Admin B reviewed and acknowledged Workspace A waiver.",
        imported_by="human:admin_b",
        actor_role="admin",
    )

    # Non-admin rejected on force=True overwrite
    with pytest.raises(ForbiddenAdminRequiredError):
        import_bundle(
            out_zip,
            ws_b,
            security_manager=sec_b,
            new_session_id="imp-waived-b",
            force=True,
            actor_role="editor",
        )

    # Promoted into Workspace B cleanly because acknowledged waiver was re-signed under K_waiver_B!
    assert imp_b.session_id == "imp-waived-b"
    target_contract = ws_b_root / "contracts" / "promoted_ontology.json"
    prom_res = svc_b.promote_to_workspace(
        "imp-waived-b",
        target_contract,
        "",
        rationale="Promote acknowledged cross-workspace session into Workspace B.",
        actor="human:admin_b",
        actor_role="admin",
        allowed_workspace_roots=[ws_b_root],
    )
    assert prom_res is not None
    assert target_contract.is_file()
