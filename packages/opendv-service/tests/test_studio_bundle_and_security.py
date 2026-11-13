"""Comprehensive regression tests for Wave R2-B Studio CAS, Locks, Security, Bundles & Runners.

Covers:
- Lock handle rollback on metadata write failure, 4-tuple order_key, dead-PID / 0-byte stale
  detection, and reentrant L5 session locking (`CRIT-01`, `MIN-01`).
- Greenfield GenesisDraft (`0` synthetic CBCs, exact JCS SHA-256 parity across default and custom
  namespaces), promoted-session deletion guard (`409` / exit `6`), tail-truncation audit checks,
  and unified signed audit records (`P08::MAJ-01`, `MAJ-01`, `MIN-02`, `MIN-05`, `CRIT-05`).
- Governed `commit_delta` (`enforce_governance=True`, Path A `0.65` exact/close match, Path B
  `K_waiver`, grandfathered revocation on touch, destructive MAJOR admin RBAC, exact SemVer,
  proposal `ACCEPTED`/`STALE`), two-phase `promote_to_workspace`, 3600s staging grace & promotion
  intent recovery, fail-closed L5 CAS GC, and `delete_source` `SOURCE_NOT_FOUND` (`404` / exit `3`).
- `validate_workspace_containment`, cross-workspace bundle delta replay verification, foreign
  `K_audit` archiving & local re-anchoring, foreign `K_waiver` acknowledgement, atomic `force=True`
  rollback, effective taint confidential export gates, and Windows `0x0400` reparse rejection.
- `StudioJobRunners` (`parse`, `clarify`, `construct`) adapter registration, capability propagation,
  binding clarification constraints (`CRIT-07`), strict `base_sha256` OCC (`P11::MAJ-01`),
  `NOOP_CONSTRUCT`, and governed `auto_commit=True` (`P11::MAJ-02`).
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import time
import uuid
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

from opendv_contracts.dtos.clarification import (
    ClarificationAnswerItem,
    ClarificationAnswerRequest,
)
from opendv_contracts.dtos.ontology import (
    AddAttributeOp,
    AddConceptOp,
    AddSynonymOp,
    OntologyDelta,
    RemoveConceptOp,
    StewardWaiverRecord,
)
from opendv_contracts.errors import ErrorCode, OpenDVError
from opendv_core.models.graph_a import (
    CanonicalBusinessConcept,
    ExternalMapping,
    GraphAOntology,
)
from opendv_service.construction.clarification_harness import ClarificationHarness
from opendv_service.ontology.validator import UngroundedConceptError
from opendv_service.studio import (
    GENESIS_DRAFT_SHA256,
    L3_JOURNAL,
    L5_SESSION,
    AdminRoleRequiredError,
    ArchiveSecurityViolationError,
    AtomicCommitService,
    BundleAuditChainCorruptError,
    CannotDeletePromotedSessionError,
    CommitValidationError,
    ConcurrentModificationError,
    ConfidentialSourceExportUnacknowledgedError,
    ContentAddressableStorage,
    ExportRationaleRequiredError,
    ForbiddenAdminRequiredError,
    HistoricalContractRationaleRequiredError,
    HistoricalContractWarningUnacknowledgedError,
    ImportDeltaReplayHashMismatchError,
    ImportGroundingRationaleRequiredError,
    ImportGroundingViolationError,
    InvalidWorkspacePathError,
    LineageRootMismatchError,
    LockOrderViolationError,
    NoOpPromotionError,
    PromotionRationaleRequiredError,
    RemoteServerPathForbiddenError,
    SourceNotFoundError,
    SourceReferencedByPendingProposalError,
    SSEJournalWriter,
    StudioJobRunners,
    StudioLock,
    StudioSessionManager,
    TamperEvidentChainCorruptError,
    WorkspaceSecurityManager,
    append_signed_audit_record,
    build_studio_ingestion_service,
    compute_effective_session_taint,
    delete_source,
    export_bundle,
    import_bundle,
    is_lock_stale,
    load_binding_clarification_answers,
    mark_and_sweep_cas,
    reentrant_session_lock,
    studio_lock,
    validate_archive_member,
    validate_workspace_containment,
    verify_owner_only_permissions,
)
from opendv_sources.adapters import LibreOfficeDocumentAdapter, LiteParseDocumentAdapter
from opendv_sources.exceptions import OCRUnavailableError

FIBO_PARTY_URI = "https://spec.edmcouncil.org/fibo/ontology/FND/Parties/Parties/IndependentParty"
FIBO_ACCOUNT_URI = (
    "https://spec.edmcouncil.org/fibo/ontology/FBC/ProductsAndServices/ClientsAndAccounts/Account"
)


def _grounded_mapping(uri: str = FIBO_ACCOUNT_URI, confidence: float = 0.85) -> ExternalMapping:
    return ExternalMapping(
        scheme="fibo",
        target_uri=uri,
        predicate="skos:exactMatch",
        confidence=confidence,
    )


# ==============================================================================
# 1. Locks, Handle-Leak Rollback, Stale Detection & Greenfield Genesis
# ==============================================================================


def test_lock_acquire_rolls_back_kernel_handle_on_metadata_failure(tmp_path: Path) -> None:
    """CRIT-01: Any failure in _write_metadata unlocks and closes raw handle cleanly."""
    lock_file = tmp_path / ".session.lock"
    bad_lock = StudioLock(lock_file, level=L5_SESSION, session_id="s-leak-test", timeout=0.5)

    with (
        patch.object(StudioLock, "_write_metadata", side_effect=OSError("disk full")),
        pytest.raises(OSError, match="disk full"),
    ):
        bad_lock.acquire()

    assert not bad_lock.is_locked

    # Subsequent lock acquisition on the same file must succeed immediately without timeout!
    good_lock = StudioLock(lock_file, level=L5_SESSION, session_id="s-leak-test", timeout=0.5)
    with good_lock:
        assert good_lock.is_locked


def test_lock_order_key_4tuple_and_stale_zero_byte_detection(tmp_path: Path) -> None:
    """MIN-01: 4-tuple order_key includes normalized path; >5s 0-byte lock file is stale."""
    lock_a = StudioLock(tmp_path / "a.lock", level=L3_JOURNAL)
    lock_b = StudioLock(tmp_path / "b.lock", level=L3_JOURNAL)
    assert len(lock_a.order_key) == 4
    assert lock_a.order_key < lock_b.order_key

    zero_lock = tmp_path / "empty.lock"
    zero_lock.write_bytes(b"")
    old_ts = time.time() - 10.0
    os.utime(zero_lock, (old_ts, old_ts))
    assert is_lock_stale(zero_lock) is True


def test_reentrant_session_lock_preserves_l3_inversion_guard(tmp_path: Path) -> None:
    """CRIT-01: reentrant_session_lock allows nested L5 while blocking L3-after-L5."""
    lock_file = tmp_path / ".session.lock"
    journal_lock = tmp_path / "job.events.jsonl.lock"

    with (
        reentrant_session_lock(lock_file, session_id="sess-1"),
        reentrant_session_lock(lock_file, session_id="sess-1"),
        pytest.raises(LockOrderViolationError),
        studio_lock(journal_lock, level=L3_JOURNAL),
    ):
        pass


def test_greenfield_genesis_zero_cbcs_and_custom_namespace_jcs_parity(tmp_path: Path) -> None:
    """P08::MAJ-01: Default genesis yields GENESIS_DRAFT_SHA256; custom yields JCS SHA-256."""
    mgr = StudioSessionManager(studio_root=tmp_path / "studio")

    default_meta = mgr.create_session("gen-default")
    assert default_meta.head_sha256 == GENESIS_DRAFT_SHA256
    v0_default = tmp_path / "studio" / "gen-default" / "versions" / "v0.0.0" / "ontology.json"
    assert hashlib.sha256(v0_default.read_bytes()).hexdigest() == GENESIS_DRAFT_SHA256
    ont_default = GraphAOntology.model_validate_json(v0_default.read_text(encoding="utf-8"))
    assert ont_default.canonical_business_concepts == []

    custom_meta = mgr.create_session("gen-custom", domain_namespace="Retail.Banking")
    assert custom_meta.domain_namespace == "retail_banking"
    v0_custom = tmp_path / "studio" / "gen-custom" / "versions" / "v0.0.0" / "ontology.json"
    actual_custom_sha = hashlib.sha256(v0_custom.read_bytes()).hexdigest()
    assert custom_meta.head_sha256 == actual_custom_sha
    assert custom_meta.head_sha256 != GENESIS_DRAFT_SHA256


def test_delete_promoted_session_guard_and_tail_truncation_detection(tmp_path: Path) -> None:
    """MIN-02 & CRIT-05: Promoted sessions reject delete without force; tail truncation fails."""
    studio_root = tmp_path / "studio"
    sec = WorkspaceSecurityManager(studio_root=studio_root)
    mgr = StudioSessionManager(studio_root=studio_root, security_manager=sec)
    commit_svc = AtomicCommitService(session_manager=mgr, security_manager=sec)

    meta = mgr.create_session("sess-promo")
    sdir = mgr.get_session_dir("sess-promo")

    delta = OntologyDelta(
        delta_id="d-1",
        base_ontology_version="0.0.0",
        target_ontology_version="1.0.0",
        parent_sha256=meta.head_sha256,
        operations=[
            AddConceptOp(
                op_id="op-1",
                concept_name="Account",
                concept_uri="urn:ontology:entity:account",
                target_hub="hub_account",
                external_mappings=[_grounded_mapping()],
            )
        ],
    )
    commit_svc.commit_delta("sess-promo", delta)

    ws_root = tmp_path / "workspace"
    ws_root.mkdir()
    ws_contract = ws_root / "ontology.json"
    promo_res = commit_svc.promote_to_workspace(
        "sess-promo",
        ws_contract,
        "",
        rationale="Promote initial enterprise Account model to workspace.",
        actor_role="admin",
        allowed_workspace_roots=[ws_root],
    )
    assert promo_res.promoted_version == "v1.0.0"
    assert ws_contract.is_file()

    with pytest.raises(CannotDeletePromotedSessionError) as exc_info:
        mgr.delete_session("sess-promo", force=False)
    assert exc_info.value.status_code == 409
    assert exc_info.value.exit_code == 6

    # Verify audit tail-truncation detection: drop the last line of audit.jsonl
    audit_file = sdir / "audit.jsonl"
    lines = audit_file.read_text(encoding="utf-8").splitlines()
    assert len(lines) >= 4
    audit_file.write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8")
    assert mgr.verify_audit_chain("sess-promo") is False
    with pytest.raises(TamperEvidentChainCorruptError):
        commit_svc.reconcile_and_recover_session("sess-promo")

    # Restore lines and verify force=True deletes cleanly
    audit_file.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert mgr.verify_audit_chain("sess-promo") is True
    mgr.delete_session("sess-promo", force=True)
    assert not sdir.exists()


# ==============================================================================
# 2. Governed Commit, Two-Phase Promotion, CAS GC & Delete Source
# ==============================================================================


def test_commit_delta_governance_path_a_path_b_grandfathered_and_destructive_rbac(
    tmp_path: Path,
) -> None:
    """565f7024::CRIT-04 & P10::MAJ-02..03: Enforce Path A/B ONT-EVO-002, SemVer, and admin RBAC."""
    studio_root = tmp_path / "studio"
    sec = WorkspaceSecurityManager(studio_root=studio_root)
    mgr = StudioSessionManager(studio_root=studio_root, security_manager=sec)
    svc = AtomicCommitService(session_manager=mgr, security_manager=sec)

    # Seed with an ungrounded grandfathered concept
    seed_ont = GraphAOntology(
        ontology_version="1.0.0",
        domain_namespace="finance",
        enterprise_owner="Platform",
        canonical_business_concepts=[
            CanonicalBusinessConcept(
                concept_uri="urn:ontology:entity:legacy_ledger",
                concept_name="Legacy Ledger",
                target_hub="hub_legacy_ledger",
            )
        ],
    )
    meta = mgr.create_session("sess-gov", initial_ontology=seed_ont)
    sdir = mgr.get_session_dir("sess-gov")

    # Stale proposal should become STALE after next commit
    (sdir / "proposals" / "prop-stale.json").write_text(
        json.dumps(
            {
                "proposal_id": "prop-stale",
                "status": "PENDING",
                "delta": {"parent_sha256": meta.head_sha256},
            }
        ),
        encoding="utf-8",
    )

    # 1. Wrong SemVer (jumping 1.0.0 -> 1.5.0 instead of 1.1.0) rejected
    wrong_ver_delta = OntologyDelta(
        delta_id="d-wrong-ver",
        base_ontology_version="1.0.0",
        target_ontology_version="1.5.0",
        parent_sha256=meta.head_sha256,
        operations=[
            AddConceptOp(
                op_id="op-add-party",
                concept_name="Party",
                concept_uri="urn:ontology:entity:party",
                target_hub="hub_party",
                external_mappings=[_grounded_mapping()],
            )
        ],
    )
    with pytest.raises(CommitValidationError):
        svc.commit_delta("sess-gov", wrong_ver_delta)

    # 2. skos:broadMatch rejected under Path A
    broad_delta = OntologyDelta(
        delta_id="d-broad",
        base_ontology_version="1.0.0",
        target_ontology_version="1.1.0",
        parent_sha256=meta.head_sha256,
        operations=[
            AddConceptOp(
                op_id="op-broad",
                concept_name="CustomWidget",
                concept_uri="urn:ontology:entity:widget",
                target_hub="hub_widget",
                external_mappings=[
                    ExternalMapping(
                        scheme="fibo",
                        target_uri=FIBO_PARTY_URI,
                        predicate="skos:broadMatch",
                        confidence=0.95,
                    )
                ],
            )
        ],
    )
    with pytest.raises(UngroundedConceptError):
        svc.commit_delta("sess-gov", broad_delta)

    # 3. Path B signed StewardWaiverRecord allows custom domain concept commit!
    ungrounded_delta = OntologyDelta(
        delta_id="d-waived",
        base_ontology_version="1.0.0",
        target_ontology_version="1.1.0",
        parent_sha256=meta.head_sha256,
        operations=[
            AddConceptOp(
                op_id="op-waived",
                concept_name="ProprietarySignal",
                concept_uri="urn:ontology:entity:prop_signal",
                target_hub="hub_prop_signal",
                external_mappings=[],
            )
        ],
    )
    unsigned_waiver = StewardWaiverRecord(
        proposal_id=uuid.uuid4(),
        target_concept_uris=["urn:ontology:entity:prop_signal"],
        proposal_delta_sha256=ungrounded_delta.canonical_sha256(),
        base_sha256=meta.head_sha256,
        authorized_by="human:chief_steward",
        authorized_role="admin",
        rationale="Proprietary alpha factor with zero external standard equivalent.",
        created_at=datetime.now(UTC) - timedelta(minutes=1),
        expires_at=datetime.now(UTC) + timedelta(days=30),
        signature_or_token="0" * 64,
    )
    sig = sec.sign_waiver(unsigned_waiver.signing_payload().encode("utf-8"))
    signed_waiver = unsigned_waiver.model_copy(update={"signature_or_token": sig})

    res_110 = svc.commit_delta(
        "sess-gov",
        ungrounded_delta,
        waivers=[signed_waiver],
    )
    assert res_110.version == "v1.1.0"
    v110_bytes = (sdir / "versions" / "v1.1.0" / "ontology.json").read_bytes()
    assert hashlib.sha256(v110_bytes).hexdigest() == res_110.target_sha256

    # Stale proposal targeting old head must now be marked STALE
    stale_prop = json.loads((sdir / "proposals" / "prop-stale.json").read_text(encoding="utf-8"))
    assert stale_prop["status"] == "STALE"

    # 4. Touching the grandfathered concept revokes its exemption unless grounded or waived!
    touch_grandfathered = OntologyDelta(
        delta_id="d-touch-gf",
        base_ontology_version="1.1.0",
        target_ontology_version="1.1.1",
        parent_sha256=res_110.target_sha256,
        operations=[
            AddSynonymOp(
                op_id="op-touch-syn",
                concept_uri="urn:ontology:entity:legacy_ledger",
                synonym="Legacy General Ledger",
            )
        ],
    )
    with pytest.raises(UngroundedConceptError):
        svc.commit_delta("sess-gov", touch_grandfathered)

    # Adding an attribute to the grandfathered concept also revokes exemption
    touch_attr = OntologyDelta(
        delta_id="d-touch-attr",
        base_ontology_version="1.1.0",
        target_ontology_version="1.2.0",
        parent_sha256=res_110.target_sha256,
        operations=[
            AddAttributeOp(
                op_id="op-touch-attr",
                concept_uri="urn:ontology:entity:legacy_ledger",
                attribute_name="ledger_region",
                data_type="STRING",
            )
        ],
    )
    with pytest.raises(UngroundedConceptError):
        svc.commit_delta("sess-gov", touch_attr)

    # 5. Non-admin editor attempting destructive MAJOR RemoveConceptOp is rejected with 403/7
    destructive_delta = OntologyDelta(
        delta_id="d-rm",
        base_ontology_version="1.1.0",
        target_ontology_version="2.0.0",
        parent_sha256=res_110.target_sha256,
        operations=[
            RemoveConceptOp(
                op_id="op-rm",
                concept_uri="urn:ontology:entity:legacy_ledger",
            )
        ],
    )
    with pytest.raises(ForbiddenAdminRequiredError) as rbac_exc:
        svc.commit_delta(
            "sess-gov",
            destructive_delta,
            actor_role="editor",
        )
    assert rbac_exc.value.status_code == 403
    assert rbac_exc.value.exit_code == 7

    # Admin succeeds on the same destructive delta
    res_200 = svc.commit_delta(
        "sess-gov",
        destructive_delta,
        actor_role="admin",
    )
    assert res_200.version == "v2.0.0"


def test_promotion_lineage_downgrade_noop_and_crash_recovery(tmp_path: Path) -> None:
    """MAJ-06 & P07::MAJ-01: Two-phase promotion guards + 3600s staging grace + intent recovery."""
    studio_root = tmp_path / "studio"
    sec = WorkspaceSecurityManager(studio_root=studio_root)
    mgr = StudioSessionManager(studio_root=studio_root, security_manager=sec)
    svc = AtomicCommitService(session_manager=mgr, security_manager=sec)

    init = mgr.create_session("sess-p2")
    sdir = mgr.get_session_dir("sess-p2")

    # Young staging directory (< 3600s) is preserved by default reconcile_and_recover_session
    young_staging = sdir / ".staging-inflight"
    young_staging.mkdir()
    (young_staging / "meta.json").write_text(
        json.dumps({"target_sha256": "b" * 64, "target_version": "v1.0.0"}), encoding="utf-8"
    )
    svc.reconcile_and_recover_session("sess-p2")
    assert young_staging.exists()
    svc.reconcile_and_recover_session("sess-p2", staging_grace_seconds=0.0)
    assert not young_staging.exists()

    # Commit v1.0.0
    d1 = OntologyDelta(
        delta_id="d1",
        base_ontology_version="0.0.0",
        target_ontology_version="1.0.0",
        parent_sha256=init.head_sha256,
        operations=[
            AddConceptOp(
                op_id="op1",
                concept_name="Account",
                concept_uri="urn:ontology:entity:account",
                target_hub="hub_account",
                external_mappings=[_grounded_mapping()],
            )
        ],
    )
    r1 = svc.commit_delta("sess-p2", d1)

    ws_root = tmp_path / "ws"
    ws_root.mkdir()
    ws_file = ws_root / "ontology.json"

    # Default non-admin role is rejected on promote_to_workspace (403 / exit 7)
    with pytest.raises(ForbiddenAdminRequiredError):
        svc.promote_to_workspace(
            "sess-p2",
            ws_file,
            "",
            rationale="Initial v1.0.0 contract promotion.",
            allowed_workspace_roots=[ws_root],
        )

    # Short rationale fails
    with pytest.raises(PromotionRationaleRequiredError):
        svc.promote_to_workspace(
            "sess-p2",
            ws_file,
            "",
            rationale="short",
            actor_role="admin",
            allowed_workspace_roots=[ws_root],
        )

    # Promote v1.0.0 succeeds
    p1 = svc.promote_to_workspace(
        "sess-p2",
        ws_file,
        "",
        rationale="Initial v1.0.0 contract promotion.",
        actor_role="admin",
        allowed_workspace_roots=[ws_root],
    )

    # Promoting identical head again raises NoOpPromotionError
    with pytest.raises(NoOpPromotionError):
        svc.promote_to_workspace(
            "sess-p2",
            ws_file,
            p1.workspace_sha256,
            rationale="Duplicate promotion attempt.",
            actor_role="admin",
            allowed_workspace_roots=[ws_root],
        )

    # Write a higher version (v2.0.0) sharing lineage into ws_file -> historical warning required
    higher_ont = GraphAOntology(
        ontology_version="2.0.0",
        domain_namespace="general",
        enterprise_owner="Enterprise Architecture",
        parent_sha256=r1.target_sha256,
        canonical_business_concepts=[
            CanonicalBusinessConcept(
                concept_uri="urn:ontology:entity:party",
                concept_name="Party",
                target_hub="hub_party",
            )
        ],
    )
    ws_file.write_text(higher_ont.model_dump_json(indent=2), encoding="utf-8")
    higher_raw_sha = hashlib.sha256(ws_file.read_bytes()).hexdigest()

    with pytest.raises(HistoricalContractWarningUnacknowledgedError):
        svc.promote_to_workspace(
            "sess-p2",
            ws_file,
            higher_raw_sha,
            rationale="Attempt downgrade without ack.",
            actor_role="admin",
            allowed_workspace_roots=[ws_root],
        )
    with pytest.raises(HistoricalContractRationaleRequiredError):
        svc.promote_to_workspace(
            "sess-p2",
            ws_file,
            higher_raw_sha,
            rationale="Attempt downgrade with short historical rationale.",
            acknowledge_historical_contract_warning=True,
            historical_contract_rationale="short",
            actor_role="admin",
            allowed_workspace_roots=[ws_root],
        )

    # Unrelated lineage root in workspace raises LineageRootMismatchError (412 / exit 5)
    foreign_ont = GraphAOntology(
        ontology_version="1.0.0",
        domain_namespace="foreign",
        enterprise_owner="Other Org",
        parent_sha256="c" * 64,
        canonical_business_concepts=[
            CanonicalBusinessConcept(
                concept_uri="urn:ontology:entity:other",
                concept_name="Other",
                target_hub="hub_other",
            )
        ],
    )
    ws_file.write_text(foreign_ont.model_dump_json(indent=2), encoding="utf-8")
    foreign_raw_sha = hashlib.sha256(ws_file.read_bytes()).hexdigest()
    with pytest.raises(LineageRootMismatchError) as lin_exc:
        svc.promote_to_workspace(
            "sess-p2",
            ws_file,
            foreign_raw_sha,
            rationale="Attempt overwrite of unrelated lineage.",
            actor_role="admin",
            allowed_workspace_roots=[ws_root],
        )
    assert lin_exc.value.status_code == 412
    assert lin_exc.value.exit_code == 5

    # Simulate crash after PROMOTION_INTENT before PROMOTION_COMMITTED (with target written)
    ws_file.write_bytes((sdir / "versions" / "v1.0.0" / "ontology.json").read_bytes())
    append_signed_audit_record(
        session_dir=sdir,
        action="PROMOTION_INTENT",
        actor="human:admin",
        details={
            "workspace_path": str(ws_file),
            "target_version": "v1.0.0",
            "target_head_sha256": r1.target_sha256,
        },
        security_manager=sec,
    )
    svc.reconcile_and_recover_session("sess-p2")
    last_audit = json.loads((sdir / "audit.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert last_audit["action"] == "PROMOTION_COMMITTED"
    assert mgr.verify_audit_chain("sess-p2") is True


def test_cas_gc_fail_closed_and_delete_source_not_found_and_hash_protection(
    tmp_path: Path,
) -> None:
    """CRIT-02 & P07::MAJ-03: CAS GC fails closed on bad manifest; delete_source 404 & SHA guard."""
    mgr = StudioSessionManager(studio_root=tmp_path / "studio")
    mgr.create_session("sess-cas-gc")
    sdir = mgr.get_session_dir("sess-cas-gc")
    cas = ContentAddressableStorage(root_dir=sdir / "inputs")

    sha_file = cas.put(b"active file in manifest.files")
    sha_clar = cas.put(b"clarification answer blob")
    sha_orphan = cas.put(b"true orphan")

    # 1. Missing manifest.json -> fail closed with ZERO deletions!
    assert mark_and_sweep_cas(sdir) == set()
    assert cas.has(sha_orphan)

    # 2. Corrupt manifest.json -> fail closed with ZERO deletions!
    manifest_path = sdir / "inputs" / "manifest.json"
    manifest_path.write_text("{corrupt json", encoding="utf-8")
    assert mark_and_sweep_cas(sdir) == set()
    assert cas.has(sha_orphan)

    # 3. Valid manifest using `files` key + clarifications/*.json protects sha_file & sha_clar
    manifest_path.write_text(
        json.dumps(
            {
                "files": [
                    {
                        "source_id": "src-f1",
                        "sha256": sha_file,
                        "filename": "customers.csv",
                        "tombstoned": False,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    (sdir / "clarifications" / "answers.json").write_text(
        json.dumps({"answers_cas_sha256": sha_clar}), encoding="utf-8"
    )
    purged = mark_and_sweep_cas(sdir)
    assert purged == {sha_orphan}
    assert cas.has(sha_file)
    assert cas.has(sha_clar)

    # 4. delete_source on non-existent source raises SourceNotFoundError (404 / exit 3)
    with pytest.raises(SourceNotFoundError) as nf_exc:
        delete_source(sdir, "src-does-not-exist")
    assert nf_exc.value.status_code == 404
    assert nf_exc.value.exit_code == 3

    # 5. delete_source checks STALE proposals citing ONLY source_sha256 (not source_id!)
    stale_prop = sdir / "proposals" / "p-stale.json"
    stale_prop.write_text(
        json.dumps({"proposal_id": "p-stale", "status": "STALE", "evidence_sha": sha_file}),
        encoding="utf-8",
    )
    with pytest.raises(SourceReferencedByPendingProposalError):
        delete_source(sdir, "src-f1")
    stale_prop.unlink()

    # Now delete_source succeeds and sweeps sha_file!
    swept = delete_source(sdir, "src-f1")
    assert sha_file in swept
    assert not cas.has(sha_file)


# ==============================================================================
# 3. Workspace Containment & Portable Bundle Replay / Taint / Re-Anchoring
# ==============================================================================


def test_workspace_security_permissions_and_containment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """MAJ-05: Owner-only DACL verification + OPENDV_WORKSPACE_ROOTS + remote server_path guard."""
    studio_root = tmp_path / "studio"
    sec = WorkspaceSecurityManager(studio_root=studio_root)
    assert verify_owner_only_permissions(studio_root) is True
    assert verify_owner_only_permissions(sec.secret_path) is True

    allowed = tmp_path / "allowed_ws"
    allowed.mkdir()
    outside = tmp_path / "outside_ws"
    outside.mkdir()

    monkeypatch.setenv("OPENDV_WORKSPACE_ROOTS", str(allowed))
    valid_path = validate_workspace_containment(allowed / "contracts" / "ontology.json")
    assert valid_path == (allowed / "contracts" / "ontology.json").resolve()

    with pytest.raises(InvalidWorkspacePathError) as out_exc:
        validate_workspace_containment(outside / "ontology.json")
    assert out_exc.value.status_code == 400
    assert out_exc.value.exit_code == 2

    with pytest.raises(RemoteServerPathForbiddenError) as rem_exc:
        validate_workspace_containment(allowed / "ontology.json", is_remote_request=True)
    assert rem_exc.value.status_code == 400
    assert rem_exc.value.exit_code == 2


def test_bundle_cross_workspace_reanchor_replay_tamper_taint_and_force_swap(
    tmp_path: Path,
) -> None:
    """CRIT-03, CRIT-04, P08::MAJ-02..04: Bundle replay, foreign keys, taint, force swap."""
    # Workspace A creates a session with a waived concept & confidential source
    ws_a = tmp_path / "ws_a" / "studio"
    sec_a = WorkspaceSecurityManager(studio_root=ws_a)
    mgr_a = StudioSessionManager(studio_root=ws_a, security_manager=sec_a)
    svc_a = AtomicCommitService(session_manager=mgr_a, security_manager=sec_a)

    meta_a = mgr_a.create_session("portable-01")
    sdir_a = mgr_a.get_session_dir("portable-01")

    # Add confidential file to manifest["files"] to test effective taint detection
    cas_a = mgr_a.get_cas("portable-01")
    conf_sha = cas_a.put(b"secret balance sheet")
    (sdir_a / "inputs" / "manifest.json").write_text(
        json.dumps(
            {
                "files": [
                    {
                        "source_id": "src-secret",
                        "sha256": conf_sha,
                        "filename": "secret.csv",
                        "do_not_send_to_agent": True,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    assert compute_effective_session_taint(sdir_a) is True

    delta_a = OntologyDelta(
        delta_id="d-waived-a",
        base_ontology_version="0.0.0",
        target_ontology_version="1.0.0",
        parent_sha256=meta_a.head_sha256,
        operations=[
            AddConceptOp(
                op_id="op-a1",
                concept_name="InternalDesk",
                concept_uri="urn:ontology:entity:internal_desk",
                target_hub="hub_internal_desk",
                external_mappings=[],
            )
        ],
    )
    wv_raw = StewardWaiverRecord(
        proposal_id=uuid.uuid4(),
        target_concept_uris=["urn:ontology:entity:internal_desk"],
        proposal_delta_sha256=delta_a.canonical_sha256(),
        base_sha256=meta_a.head_sha256,
        authorized_by="human:steward_a",
        authorized_role="admin",
        rationale="Workspace A approved internal desk concept.",
        created_at=datetime.now(UTC) - timedelta(minutes=1),
        signature_or_token="0" * 64,
    )
    wv_a = wv_raw.model_copy(
        update={"signature_or_token": sec_a.sign_waiver(wv_raw.signing_payload().encode("utf-8"))}
    )
    svc_a.commit_delta("portable-01", delta_a, waivers=[wv_a])
    # Persist waivers.json in versions/v1.0.0 so importer inspects foreign K_waiver
    (sdir_a / "versions" / "v1.0.0" / "waivers.json").write_text(
        json.dumps([wv_a.model_dump(mode="json")]), encoding="utf-8"
    )

    bundle_zip = ws_a.parent / "portable-01.odvstudio.zip"

    # Confidential export gates: editor forbidden (403/7), unack (422/4), short rationale (400/1)
    with pytest.raises(AdminRoleRequiredError) as ed_exc:
        export_bundle(
            sdir_a,
            bundle_zip,
            admin=False,
            acknowledge_confidential=True,
            export_rationale="Authorized export for DR test.",
            security_manager=sec_a,
        )
    assert ed_exc.value.status_code == 403
    assert ed_exc.value.exit_code == 7

    with pytest.raises(ConfidentialSourceExportUnacknowledgedError):
        export_bundle(sdir_a, bundle_zip, admin=True, security_manager=sec_a)

    with pytest.raises(ExportRationaleRequiredError) as rat_exc:
        export_bundle(
            sdir_a,
            bundle_zip,
            admin=True,
            acknowledge_confidential=True,
            export_rationale="tiny",
            security_manager=sec_a,
        )
    assert rat_exc.value.status_code == 400
    assert rat_exc.value.exit_code == 1

    export_bundle(
        sdir_a,
        bundle_zip,
        admin=True,
        acknowledge_confidential=True,
        export_rationale="Authorized cross-workspace transfer.",
        actor="human:admin_a",
        security_manager=sec_a,
    )
    assert mgr_a.verify_audit_chain("portable-01") is True

    # Workspace B has an independent .odv_workspace_secret (foreign K_audit & foreign K_waiver!)
    ws_b = tmp_path / "ws_b" / "studio"
    sec_b = WorkspaceSecurityManager(studio_root=ws_b)
    mgr_b = StudioSessionManager(studio_root=ws_b, security_manager=sec_b)

    # Foreign K_waiver requires explicit attribution acknowledgement + >=10 char rationale
    with pytest.raises(ImportGroundingViolationError):
        import_bundle(bundle_zip, ws_b, security_manager=sec_b)
    with pytest.raises(ImportGroundingRationaleRequiredError) as ig_exc:
        import_bundle(
            bundle_zip,
            ws_b,
            security_manager=sec_b,
            acknowledge_unverified_import_attribution=True,
            acknowledgement_rationale="short",
        )
    assert ig_exc.value.status_code == 400
    assert ig_exc.value.exit_code == 1

    imported_meta = import_bundle(
        bundle_zip,
        ws_b,
        security_manager=sec_b,
        acknowledge_unverified_import_attribution=True,
        acknowledgement_rationale=(
            "Reviewed Workspace A steward waiver and accepted into Workspace B."
        ),
        imported_by="human:admin_b",
    )
    imported_dir = mgr_b.get_session_dir(imported_meta.session_id)
    # Foreign audit ledger archived + local K_audit re-anchored + effective taint preserved!
    archived_audits = list(imported_dir.glob("audit.imported.*.jsonl"))
    assert len(archived_audits) == 1
    assert mgr_b.verify_audit_chain("portable-01") is True
    meta_b = mgr_b.get_session("portable-01")
    assert meta_b.taint_classification == "TAINTED"
    assert meta_b.imported_audit_anchor_sha256 is not None

    # Tamper with ontology.json inside a copy of bundle_zip -> ImportDeltaReplayHashMismatchError,
    # AND live session in ws_b must remain 100% intact when force=True fails!
    tampered_zip = tmp_path / "tampered_replay.odvstudio.zip"
    with (
        zipfile.ZipFile(bundle_zip, "r") as zin,
        zipfile.ZipFile(tampered_zip, "w", compression=zipfile.ZIP_DEFLATED) as zout,
    ):
        for item in zin.infolist():
            raw = zin.read(item.filename)
            if item.filename == "versions/v1.0.0/ontology.json":
                obj = json.loads(raw.decode("utf-8"))
                obj["enterprise_owner"] = "Tampered Owner"
                raw = json.dumps(obj).encode("utf-8")
            zout.writestr(item, raw)

    with pytest.raises(ImportDeltaReplayHashMismatchError) as replay_exc:
        import_bundle(
            tampered_zip,
            ws_b,
            security_manager=sec_b,
            force=True,
            acknowledge_unverified_import_attribution=True,
            acknowledgement_rationale="Attempting overwrite with tampered replay archive.",
        )
    assert replay_exc.value.status_code == 422
    assert replay_exc.value.exit_code == 4
    # Live session in ws_b must still exist and pass audit verification!
    assert mgr_b.verify_audit_chain("portable-01") is True

    # Tamper with audit.jsonl inside archive -> BundleAuditChainCorruptError (422 / exit 4)
    corrupt_audit_zip = tmp_path / "corrupt_audit.odvstudio.zip"
    with (
        zipfile.ZipFile(bundle_zip, "r") as zin,
        zipfile.ZipFile(corrupt_audit_zip, "w", compression=zipfile.ZIP_DEFLATED) as zout,
    ):
        for item in zin.infolist():
            raw = zin.read(item.filename)
            if item.filename == "audit.jsonl":
                lines = raw.decode("utf-8").splitlines()
                first = json.loads(lines[0])
                first["actor"] = "attacker"
                lines[0] = json.dumps(first)
                raw = ("\n".join(lines) + "\n").encode("utf-8")
            zout.writestr(item, raw)

    with pytest.raises(BundleAuditChainCorruptError) as aud_exc:
        import_bundle(
            corrupt_audit_zip,
            ws_b,
            security_manager=sec_b,
            new_session_id="corrupt-1",
        )
    assert aud_exc.value.status_code == 422
    assert aud_exc.value.exit_code == 4


def test_validate_archive_member_rejects_windows_reparse_point_attribute() -> None:
    """MIN-03: Windows FILE_ATTRIBUTE_REPARSE_POINT (0x0400) in external_attr is rejected."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zi = zipfile.ZipInfo("session.json")
        zi.create_system = 0  # MS-DOS / Windows
        zi.external_attr = 0x0400  # FILE_ATTRIBUTE_REPARSE_POINT in low 16 bits
        zf.writestr(zi, b"{}")
    buf.seek(0)
    with zipfile.ZipFile(buf, "r") as zf:
        info = zf.infolist()[0]
        with pytest.raises(ArchiveSecurityViolationError, match="reparse"):
            validate_archive_member(info, set())


# ==============================================================================
# 4. Headless StudioJobRunners (Parse, Clarify, Binding Answers, Construct)
# ==============================================================================


def test_studio_job_runners_parse_clarify_binding_answers_and_governed_construct(
    tmp_path: Path,
) -> None:
    """P11::MAJ-01..03, CRIT-01, CRIT-07: Full parse -> clarify -> answer -> auto_commit."""
    studio_root = tmp_path / "studio"
    sec = WorkspaceSecurityManager(studio_root=studio_root)
    mgr = StudioSessionManager(studio_root=studio_root, security_manager=sec)
    runners = StudioJobRunners(session_manager=mgr, security_manager=sec)

    # 1. Verify PDF and LibreOffice adapters are registered in build_studio_ingestion_service
    ing_svc = build_studio_ingestion_service()
    adapter_types = {type(a) for a in ing_svc._adapters}
    assert LiteParseDocumentAdapter in adapter_types
    assert LibreOfficeDocumentAdapter in adapter_types

    init_meta = mgr.create_session("sess-run")
    sdir = mgr.get_session_dir("sess-run")
    cas = mgr.get_cas("sess-run")

    # Upload a CSV with `account_id` (polysemic term `account` + grounded FIBO concept!)
    csv_bytes = b"account_id,account_balance\nACC-1,100.50\nACC-2,250.75\n"
    csv_sha = cas.put(csv_bytes)
    (sdir / "inputs" / "manifest.json").write_text(
        json.dumps(
            {
                "files": [
                    {
                        "source_id": "src-accounts",
                        "sha256": csv_sha,
                        "filename": "accounts.csv",
                        "tombstoned": False,
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    # Unknown source_id raises SourceNotFoundError (404 / exit 3)
    with pytest.raises(SourceNotFoundError) as nf_exc:
        runners.run_parse("sess-run", source_ids=["src-missing"])
    assert nf_exc.value.status_code == 404
    assert nf_exc.value.exit_code == 3

    # OCRUnavailableError propagates (501 / exit 1) instead of silently returning 0 chunks
    with patch.object(
        ing_svc, "ingest_bytes", side_effect=OCRUnavailableError("tesseract missing")
    ):
        err_runners = StudioJobRunners(
            session_manager=mgr, security_manager=sec, ingestion_service=ing_svc
        )
        with pytest.raises(OCRUnavailableError) as ocr_exc:
            err_runners.run_parse("sess-run")
        assert ocr_exc.value.http_status == 501
        assert ocr_exc.value.exit_code == 1

    # Parse succeeds and flushes SSE outside L5
    journal = SSEJournalWriter(sdir / "jobs" / "job-1.events.jsonl", job_id="job-1")
    parse_res = runners.run_parse("sess-run", job_id="job-1", writer=journal)
    assert parse_res["parsed_sources_count"] == 1
    assert parse_res["tables_count"] >= 1

    # Clarify generates polysemy question for `Account` and steward saves answer
    clar_res = runners.run_clarify("sess-run", job_id="job-1", writer=journal)
    assert clar_res["questions_count"] >= 1
    qid = clar_res["question_ids"][0]

    harness = ClarificationHarness(session_dir=sdir)
    questions = harness.get_questions()
    target_q = next(q for q in questions if q.question_id == qid)
    chosen_opt = next(opt for opt in target_q.options if opt.fibo_uri)
    harness.save_answers(
        ClarificationAnswerRequest(
            answers=[
                ClarificationAnswerItem(
                    question_id=qid,
                    selected_options=[chosen_opt.option_id],
                    answered_by="human:steward",
                )
            ]
        )
    )

    # CRIT-07: load_binding_clarification_answers converts list answers into preferred_mappings!
    binding = load_binding_clarification_answers(sdir)
    assert chosen_opt.fibo_uri in binding["preferred_mappings"].values()

    # P11::MAJ-02 & P08b::MAJ-04: Agent or automated principal auto_commit -> 403 / exit 7
    with pytest.raises(OpenDVError) as ag_exc:
        runners.run_construct(
            "sess-run",
            auto_commit=True,
            if_match=f'"{init_meta.head_sha256}"',
            actor="agent:bot_1",
        )
    assert ag_exc.value.error_code == ErrorCode.AGENT_PROPOSAL_ACCEPT_FORBIDDEN
    assert ag_exc.value.http_status == 403

    with pytest.raises(OpenDVError) as auto_exc:
        runners.run_construct(
            "sess-run",
            auto_commit=True,
            if_match=f'"{init_meta.head_sha256}"',
            actor="ci:pipeline",
            actor_role="automated",
        )
    assert auto_exc.value.error_code == ErrorCode.AGENT_PROPOSAL_ACCEPT_FORBIDDEN
    assert auto_exc.value.http_status == 403

    # Governed deterministic auto_commit by human steward/editor commits v1.0.0 cleanly!
    con_res = runners.run_construct(
        "sess-run",
        job_id="job-1",
        auto_commit=True,
        mode="deterministic",
        if_match=f'"{init_meta.head_sha256}"',
        actor="human:editor",
        actor_role="editor",
        writer=journal,
    )
    assert con_res["auto_committed"] is True
    assert con_res["target_version"] == "v1.0.0"

    head_after = mgr.get_session("sess-run")
    assert head_after.head_version == "1.0.0"
    assert head_after.head_sha256 == con_res["target_sha256"]

    # P11::MAJ-01: Constructing against v0.0.0 while passing v1.0.0's head_sha256 MUST fail 412!
    with pytest.raises(ConcurrentModificationError) as occ_exc:
        runners.run_construct(
            "sess-run",
            base_version="0.0.0",
            base_sha256=head_after.head_sha256,
            auto_commit=False,
        )
    assert occ_exc.value.status_code == 412
    assert occ_exc.value.exit_code == 5

    # Re-running deterministic auto_commit with identical concepts yields NOOP_CONSTRUCT!
    noop_res = runners.run_construct(
        "sess-run",
        auto_commit=True,
        mode="deterministic",
        if_match=f'"{head_after.head_sha256}"',
        actor="human:admin",
        actor_role="admin",
    )
    assert noop_res["status"] == "NOOP_CONSTRUCT"
    assert noop_res["auto_committed"] is False
    assert mgr.verify_audit_chain("sess-run") is True
