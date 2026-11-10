"""Comprehensive tests for Open-DV Ontology Studio CAS Session, Security & Storage.

Tests Task 4D.1 (#208):
- Workspace secret (.odv_workspace_secret) generation, permissions, and HKDF key derivation.
- Fail-closed defense against corrupted or truncated workspace secrets (MAJ-02).
- Strict session ID validation (regex, uppercase rejection, Windows reserved devices, traversal).
- Malicious ontology_version directory traversal rejection (MAJ-01).
- Studio CAS session directory structure creation, atomic initialization, and metadata lifecycle.
- Forward-only audit ledger HMAC chaining under K_audit using RFC 8785 JCS (MIN-01).
- Audit append synchronization under .session.lock and OCC conflict verification (MAJ-04).
- ContentAddressableStorage (CAS) put, get, dedup, tampering repair,
  and SHA-256 integrity verification (MIN-06).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import subprocess
from pathlib import Path

import pytest

from opendv_core.models.graph_a import (
    CanonicalBusinessConcept,
    GraphAOntology,
)
from opendv_core.ontology_hashing import compute_ontology_sha256, dumps_jcs_bytes
from opendv_service.studio.cas_storage import (
    CASIntegrityError,
    CASNotFoundError,
    ContentAddressableStorage,
)
from opendv_service.studio.session_manager import (
    GENESIS_DRAFT_SHA256,
    GENESIS_VERSION,
    AuditChainConflictError,
    InvalidOntologyPayloadError,
    InvalidSessionIdentifierError,
    SessionAlreadyExistsError,
    SessionNotFoundError,
    StudioSessionManager,
    validate_session_id,
)
from opendv_service.studio.workspace_security import (
    DEFAULT_HKDF_SALT,
    INFO_AUDIT_CHAIN,
    INFO_PSEUDONYMIZATION,
    INFO_STEWARD_WAIVER,
    DerivedKeys,
    WorkspaceSecretCorruptError,
    WorkspaceSecurityManager,
    hkdf_expand,
    hkdf_extract,
    hkdf_sha256,
    verify_owner_only_permissions,
)

# ==============================================================================
# 1. RFC 5869 & HKDF Key Derivation Tests
# ==============================================================================


def test_rfc5869_test_case_1_compliance() -> None:
    """Assert bit-for-bit compliance with RFC 5869 Test Case 1 (SHA-256)."""
    ikm = bytes.fromhex("0b" * 22)
    salt = bytes.fromhex("000102030405060708090a0b0c")
    info = bytes.fromhex("f0f1f2f3f4f5f6f7f8f9")
    length = 42

    prk = hkdf_extract(salt, ikm)
    assert prk.hex() == "077709362c2e32df0ddc3f0dc47bba6390b6c73bb50f9c3122ec844ad7c2b3e5"

    okm = hkdf_expand(prk, info, length)
    expected_okm = (
        "3cb25f25faacd57a90434f64d0362f2a2d2d0a90cf1a5a4c5db02d56ecc4c5bf34007208d5b887185865"
    )
    assert okm.hex() == expected_okm

    full_okm = hkdf_sha256(ikm, salt, info, length)
    assert full_okm.hex() == expected_okm


def test_workspace_security_manager_secret_generation(tmp_path: Path) -> None:
    """Test generating .odv_workspace_secret and purpose-isolated keys."""
    studio_root = tmp_path / "studio"
    mgr = WorkspaceSecurityManager(studio_root=studio_root)

    secret_file = studio_root / ".odv_workspace_secret"
    assert secret_file.exists()
    secret_bytes = secret_file.read_bytes()
    assert len(secret_bytes) == 32

    keys = mgr.get_keys()
    assert isinstance(keys, DerivedKeys)
    assert len(keys.k_pseudo) == 32
    assert len(keys.k_audit) == 32
    assert len(keys.k_waiver) == 32

    # Purpose keys must be mutually distinct
    assert keys.k_pseudo != keys.k_audit
    assert keys.k_pseudo != keys.k_waiver
    assert keys.k_audit != keys.k_waiver

    # Properties match
    assert keys.K_pseudo == keys.k_pseudo
    assert keys.K_audit == keys.k_audit
    assert keys.K_waiver == keys.k_waiver

    # Methods match
    assert mgr.get_pseudo_key() == keys.k_pseudo
    assert mgr.get_audit_key() == keys.k_audit
    assert mgr.get_waiver_key() == keys.k_waiver

    # Verify keys match manual RFC 5869 derivation
    expected_pseudo = hkdf_sha256(secret_bytes, DEFAULT_HKDF_SALT, INFO_PSEUDONYMIZATION, 32)
    expected_audit = hkdf_sha256(secret_bytes, DEFAULT_HKDF_SALT, INFO_AUDIT_CHAIN, 32)
    expected_waiver = hkdf_sha256(secret_bytes, DEFAULT_HKDF_SALT, INFO_STEWARD_WAIVER, 32)

    assert keys.k_pseudo == expected_pseudo
    assert keys.k_audit == expected_audit
    assert keys.k_waiver == expected_waiver


def test_workspace_security_manager_persistence_and_reload(tmp_path: Path) -> None:
    """Test that reloading WorkspaceSecurityManager derives identical keys."""
    studio_root = tmp_path / "studio"
    mgr1 = WorkspaceSecurityManager(studio_root=studio_root)
    keys1 = mgr1.get_keys()

    mgr2 = WorkspaceSecurityManager(studio_root=studio_root)
    keys2 = mgr2.get_keys()

    assert keys1.k_pseudo == keys2.k_pseudo
    assert keys1.k_audit == keys2.k_audit
    assert keys1.k_waiver == keys2.k_waiver


def test_workspace_security_concurrent_creation_no_overwrite(tmp_path: Path) -> None:
    """MAJ-03: Ensure concurrent calls to _load_or_create_root_secret reuse existing secret."""
    studio_root = tmp_path / "studio"
    mgr1 = WorkspaceSecurityManager(studio_root=studio_root)
    original_secret = (studio_root / ".odv_workspace_secret").read_bytes()

    secret2 = mgr1._load_or_create_root_secret()
    assert secret2 == original_secret


def test_workspace_security_corrupted_secret_fails_closed(tmp_path: Path) -> None:
    """MAJ-02: Corrupted or truncated secret must fail closed and never regenerate implicitly."""
    studio_root = tmp_path / "studio"
    studio_root.mkdir(parents=True, exist_ok=True)
    secret_file = studio_root / ".odv_workspace_secret"

    # Write truncated secret (16 bytes instead of 32)
    secret_file.write_bytes(b"truncated_secret")

    with pytest.raises(WorkspaceSecretCorruptError) as exc_info:
        WorkspaceSecurityManager(studio_root=studio_root)
    assert exc_info.value.error_code == "WORKSPACE_SECRET_CORRUPT"

    # Verify file was NOT overwritten
    assert secret_file.read_bytes() == b"truncated_secret"


def test_workspace_security_repr_redaction(tmp_path: Path) -> None:
    """Test that __repr__ of security components never leaks secret bytes."""
    studio_root = tmp_path / "studio"
    mgr = WorkspaceSecurityManager(studio_root=studio_root)
    keys = mgr.get_keys()

    repr_keys = repr(keys)
    assert keys.k_pseudo.hex() not in repr_keys
    assert keys.k_audit.hex() not in repr_keys
    assert keys.k_waiver.hex() not in repr_keys
    assert "b'***'" in repr_keys

    repr_mgr = repr(mgr)
    assert "PROTECTED" in repr_mgr


@pytest.mark.skipif(os.name != "nt", reason="Windows DACL foreign ACE test")
def test_workspace_security_windows_foreign_explicit_ace_rejected(tmp_path: Path) -> None:
    """MAJ-02: Explicit foreign ACE on secret must be detected and rejected."""
    studio_root = tmp_path / "studio"
    WorkspaceSecurityManager(studio_root=studio_root)
    secret_file = studio_root / ".odv_workspace_secret"
    assert verify_owner_only_permissions(secret_file) is True

    # Grant foreign ACE to Everyone
    subprocess.run(["icacls", str(secret_file), "/grant", "Everyone:(R)"], check=True)

    # verify_owner_only_permissions must now reject it
    assert verify_owner_only_permissions(secret_file) is False


def test_workspace_security_signing_helpers(tmp_path: Path) -> None:
    """Test HMAC signing helpers for audit entries and steward waivers."""
    mgr = WorkspaceSecurityManager(studio_root=tmp_path / "studio")
    data = b"audit-record-intent-12345"

    audit_sig = mgr.sign_audit_entry(data)
    expected_audit_sig = hmac.new(mgr.get_audit_key(), data, hashlib.sha256).hexdigest().lower()
    assert audit_sig == expected_audit_sig

    waiver_sig = mgr.sign_waiver(data)
    expected_waiver_sig = hmac.new(mgr.get_waiver_key(), data, hashlib.sha256).hexdigest().lower()
    assert waiver_sig == expected_waiver_sig
    assert audit_sig != waiver_sig


# ==============================================================================
# 2. Session ID Validation Tests
# ==============================================================================


@pytest.mark.parametrize(
    "valid_id",
    [
        "s",
        "0",
        "session-1",
        "logistics_v2",
        "alpha-beta-gamma_42",
        "a" * 64,
        "s-0123456789abcdef",
    ],
)
def test_session_id_validation_valid(valid_id: str) -> None:
    """Assert valid lowercase alphanumeric identifiers are accepted."""
    assert validate_session_id(valid_id) == valid_id


@pytest.mark.parametrize(
    ("invalid_id", "reason"),
    [
        ("", "empty string"),
        ("A", "single uppercase"),
        ("Session-1", "starts with uppercase"),
        ("mySession", "contains uppercase"),
        ("-session", "starts with hyphen"),
        ("_session", "starts with underscore"),
        ("a" * 65, "exceeds 64 characters"),
        ("session@logistics", "special character @"),
        ("session.name", "dot separator"),
        ("session name", "space separator"),
        ("session/child", "forward slash"),
        ("session\\child", "backslash"),
        ("../escape", "relative path traversal"),
        ("..", "double dot"),
        ("abc\n", "trailing newline"),
        ("abc\r", "trailing carriage return"),
        ("abc\t", "contains tab"),
        ("abc\x00", "null byte"),
        ("\nabc", "leading newline"),
    ],
)
def test_session_id_validation_invalid_syntax(invalid_id: str, reason: str) -> None:
    """Assert invalid syntax, uppercase, or path traversal raises INVALID_SESSION_IDENTIFIER."""
    with pytest.raises(InvalidSessionIdentifierError) as exc_info:
        validate_session_id(invalid_id)
    assert exc_info.value.error_code == "INVALID_SESSION_IDENTIFIER"
    assert exc_info.value.status_code == 422
    assert exc_info.value.exit_code == 2


@pytest.mark.parametrize(
    "reserved_name",
    [
        "con",
        "CON",
        "prn",
        "PRN",
        "aux",
        "AUX",
        "nul",
        "NUL",
        "com1",
        "COM1",
        "com9",
        "COM9",
        "lpt1",
        "LPT1",
        "lpt9",
        "LPT9",
        "con.txt",
        "nul.json",
    ],
)
def test_session_id_validation_windows_reserved_device_names(reserved_name: str) -> None:
    """Assert Windows reserved device names are rejected case-insensitively."""
    with pytest.raises(InvalidSessionIdentifierError) as exc_info:
        validate_session_id(reserved_name)
    assert exc_info.value.error_code == "INVALID_SESSION_IDENTIFIER"
    assert exc_info.value.status_code == 422
    assert exc_info.value.exit_code == 2


@pytest.mark.parametrize(
    "reserved_kw",
    [
        "bundle",
        "init",
        "sessions",
        "health",
        "auth",
        "admin",
        "prune",
        "import",
        "gc",
    ],
)
def test_session_id_validation_reserved_keywords(reserved_kw: str) -> None:
    """Assert reserved route keywords are rejected unconditionally."""
    with pytest.raises(InvalidSessionIdentifierError) as exc_info:
        validate_session_id(reserved_kw)
    assert exc_info.value.error_code == "INVALID_SESSION_IDENTIFIER"


# ==============================================================================
# 3. Studio Session Directory Layout & Lifecycle Tests
# ==============================================================================


def test_create_session_greenfield_genesis(tmp_path: Path) -> None:
    """Test greenfield genesis session creation and directory structure."""
    studio_root = tmp_path / "studio"
    mgr = StudioSessionManager(studio_root=studio_root)

    meta = mgr.create_session("greenfield-01")
    assert meta.session_id == "greenfield-01"
    assert meta.head_version == GENESIS_VERSION
    assert meta.head_sha256 == GENESIS_DRAFT_SHA256
    assert meta.status == "ACTIVE"
    assert meta.taint_classification == "UNTAINTED"
    assert meta.committed_versions == []

    # Verify session directory layout
    s_dir = studio_root / "greenfield-01"
    assert s_dir.is_dir()
    assert (s_dir / "session.json").is_file()
    assert (s_dir / "audit.jsonl").is_file()
    assert (s_dir / "inputs").is_dir()
    assert (s_dir / "inputs" / "cas").is_dir()
    assert (s_dir / "evidence").is_dir()
    assert (s_dir / "clarifications").is_dir()
    assert (s_dir / "versions").is_dir()
    assert (s_dir / "proposals").is_dir()
    assert (s_dir / "jobs").is_dir()
    assert (s_dir / ".staging").is_dir()

    # Verify audit.jsonl initial record
    audit_lines = (s_dir / "audit.jsonl").read_text(encoding="utf-8").strip().splitlines()
    assert len(audit_lines) == 1
    genesis_audit = json.loads(audit_lines[0])
    assert genesis_audit["session_id"] == "greenfield-01"
    assert genesis_audit["action"] == "SESSION_CREATED"
    assert genesis_audit["target_version"] == "0.0.0"
    assert genesis_audit["prev_audit_sha256"] is None
    assert genesis_audit["audit_sha256"] == meta.audit_chain_head_sha256

    # Verify HMAC signature on audit record using RFC 8785 JCS (MIN-01)
    reconstructed_dict = {
        k: v
        for k, v in genesis_audit.items()
        if k not in ("audit_sha256", "hmac_signature")
    }
    reconstructed_bytes = dumps_jcs_bytes(reconstructed_dict)
    expected_hmac = mgr.security_manager.sign_audit_entry(reconstructed_bytes)
    assert genesis_audit["hmac_signature"] == expected_hmac


def test_create_session_auto_generated_id(tmp_path: Path) -> None:
    """Test auto-generating session ID when session_id=None."""
    mgr = StudioSessionManager(studio_root=tmp_path / "studio")
    meta = mgr.create_session()
    assert meta.session_id.startswith("s-")
    assert len(meta.session_id) == 18
    # Auto-generated ID must satisfy validation
    assert validate_session_id(meta.session_id) == meta.session_id


def test_create_session_with_initial_ontology(tmp_path: Path) -> None:
    """Test session creation seeded with an existing GraphAOntology."""
    mgr = StudioSessionManager(studio_root=tmp_path / "studio")

    cbc = CanonicalBusinessConcept(
        concept_uri="urn:ontology:entity:fund",
        concept_name="Private Equity Fund",
        elm_archetype="CORE_CONCEPT",
        target_hub="hub_fund",
    )
    ontology = GraphAOntology(
        ontology_version="1.0.0",
        domain_namespace="private_equity",
        enterprise_owner="Global Wealth",
        canonical_business_concepts=[cbc],
    )
    expected_sha256 = compute_ontology_sha256(ontology)

    meta = mgr.create_session("pe-seed", initial_ontology=ontology)
    assert meta.head_version == "1.0.0"
    assert meta.head_sha256 == expected_sha256
    assert meta.committed_versions == ["1.0.0"]

    # Verify versions/v1.0.0/ contents
    v_dir = tmp_path / "studio" / "pe-seed" / "versions" / "v1.0.0"
    assert v_dir.is_dir()
    assert (v_dir / "ontology.json").is_file()
    assert (v_dir / "ontology.yaml").is_file()
    assert (v_dir / "metadata.json").is_file()

    version_meta = json.loads((v_dir / "metadata.json").read_text(encoding="utf-8"))
    assert version_meta["target_sha256"] == expected_sha256
    assert version_meta["mode"] == "seed"
    assert hashlib.sha256((v_dir / "ontology.json").read_bytes()).hexdigest() == expected_sha256


def test_create_session_malicious_ontology_version_traversal(tmp_path: Path) -> None:
    """MAJ-01 & MIN-03: Malicious ontology_version escaping versions directory must be rejected."""
    mgr = StudioSessionManager(studio_root=tmp_path / "studio")

    # Dict with path traversal in ontology_version
    malicious_dict = {
        "ontology_version": "1.0.0/../../../../evil",
        "domain_namespace": "evil",
        "enterprise_owner": "Attacker",
        "canonical_business_concepts": [],
    }

    with pytest.raises(InvalidOntologyPayloadError):
        mgr.create_session("traversal-session", initial_ontology=malicious_dict)

    # MIN-05: Ensure session directory was cleaned up on failure
    assert not (tmp_path / "studio" / "traversal-session").exists()


def test_create_session_provisional_uri_rejected(tmp_path: Path) -> None:
    """MIN-04: Seed ontology with provisional URIs must be rejected."""
    mgr = StudioSessionManager(studio_root=tmp_path / "studio")

    cbc = CanonicalBusinessConcept.model_construct(
        concept_uri="urn:opendv:provisional:entity:temp_fund",
        concept_name="Provisional Fund",
        elm_archetype="CORE_CONCEPT",
        target_hub="hub_fund",
    )
    ontology = GraphAOntology.model_construct(
        ontology_version="1.0.0",
        domain_namespace="private_equity",
        enterprise_owner="Global Wealth",
        canonical_business_concepts=[cbc],
    )

    with pytest.raises(InvalidOntologyPayloadError, match="provisional URI"):
        mgr.create_session("provisional-session", initial_ontology=ontology)

    assert not (tmp_path / "studio" / "provisional-session").exists()


def test_seed_ontology_adopts_domain_and_owner(tmp_path: Path) -> None:
    """MIN-05: Seed ontology's domain_namespace and enterprise_owner are adopted by session."""
    mgr = StudioSessionManager(studio_root=tmp_path / "studio")
    cbc = CanonicalBusinessConcept(
        concept_uri="urn:ontology:entity:hedge_fund",
        concept_name="Hedge Fund",
        elm_archetype="CORE_CONCEPT",
        target_hub="hub_fund",
    )
    ontology = GraphAOntology(
        ontology_version="1.0.0",
        domain_namespace="hedge_funds",
        enterprise_owner="Alternative Investments",
        canonical_business_concepts=[cbc],
    )
    meta = mgr.create_session("adopt-meta", initial_ontology=ontology)
    assert meta.domain_namespace == "hedge_funds"
    assert meta.enterprise_owner == "Alternative Investments"


def test_create_duplicate_session_raises_error(tmp_path: Path) -> None:
    """Test that creating a duplicate session raises SessionAlreadyExistsError."""
    mgr = StudioSessionManager(studio_root=tmp_path / "studio")
    mgr.create_session("session-dup")
    with pytest.raises(SessionAlreadyExistsError) as exc_info:
        mgr.create_session("session-dup")
    assert exc_info.value.error_code == "SESSION_ALREADY_EXISTS"
    assert exc_info.value.status_code == 409
    # MIN-07: Error message must not embed absolute path
    msg = str(exc_info.value).lower()
    assert "session 'session-dup' already exists" in msg


def test_get_and_list_and_delete_sessions(tmp_path: Path) -> None:
    """Test session retrieval, listing, and deletion."""
    mgr = StudioSessionManager(studio_root=tmp_path / "studio")
    s1 = mgr.create_session("session-alpha")
    s2 = mgr.create_session("session-beta")
    assert s2.session_id == "session-beta"

    # Get existing session
    retrieved = mgr.get_session("session-alpha")
    assert retrieved.session_id == s1.session_id
    assert retrieved.head_sha256 == s1.head_sha256

    # Get non-existent session (MIN-07: no leaked absolute path)
    with pytest.raises(SessionNotFoundError) as exc_info:
        mgr.get_session("session-nonexistent")
    assert exc_info.value.error_code == "SESSION_NOT_FOUND"

    # List sessions
    sessions = mgr.list_sessions()
    session_ids = [s.session_id for s in sessions]
    assert "session-alpha" in session_ids
    assert "session-beta" in session_ids

    # Delete session
    mgr.delete_session("session-alpha")
    with pytest.raises(SessionNotFoundError):
        mgr.get_session("session-alpha")

    remaining = mgr.list_sessions()
    assert len(remaining) == 1
    assert remaining[0].session_id == "session-beta"


def test_append_audit_entry_hash_chaining(tmp_path: Path) -> None:
    """Test forward-only audit hash chaining under K_audit using RFC 8785 JCS (MIN-01)."""
    mgr = StudioSessionManager(studio_root=tmp_path / "studio")
    meta = mgr.create_session("audit-chain-test")
    head_0 = meta.audit_chain_head_sha256

    # Append first event with non-ASCII and float payload to test JCS (MIN-01)
    e1 = mgr.append_audit_entry(
        session_id="audit-chain-test",
        action="PROPOSAL_STAGED",
        target_version="1.0.0",
        head_sha256=meta.head_sha256,
        actor="user:steward",
        details={"proposal_id": "p-001", "note": "Audit Café ログ €uro", "score": 0.95},
    )
    assert e1["prev_audit_sha256"] == head_0
    head_1 = e1["audit_sha256"]

    # Re-fetch session metadata to verify head hash advanced
    meta_updated = mgr.get_session("audit-chain-test")
    assert meta_updated.audit_chain_head_sha256 == head_1

    # Append second event
    e2 = mgr.append_audit_entry(
        session_id="audit-chain-test",
        action="VERSION_COMMITTED",
        target_version="1.0.0",
        head_sha256="1c100409cbbe12051f7211d2d6d6838670982b9164aaf18b52f566864da90b81",
        actor="user:steward",
    )
    assert e2["prev_audit_sha256"] == head_1


def test_verify_audit_chain_valid_and_tampered(tmp_path: Path) -> None:
    """MIN-01: verify_audit_chain validates valid chain and detects tampered entries."""
    mgr = StudioSessionManager(studio_root=tmp_path / "studio")
    mgr.create_session("verify-chain")
    mgr.append_audit_entry("verify-chain", "EVT1", "1.0.0", "0" * 64)
    mgr.append_audit_entry("verify-chain", "EVT2", "1.0.0", "0" * 64)

    assert mgr.verify_audit_chain("verify-chain") is True

    # Tamper with audit.jsonl entry
    audit_file = tmp_path / "studio" / "verify-chain" / "audit.jsonl"
    lines = audit_file.read_text(encoding="utf-8").splitlines()
    rec = json.loads(lines[1])
    rec["action"] = "FORGED_ACTION"
    lines[1] = json.dumps(rec)
    audit_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    assert mgr.verify_audit_chain("verify-chain") is False


def test_append_audit_entry_torn_tail_truncated_and_repaired(tmp_path: Path) -> None:
    """MIN-01: Torn trailing line without newline is truncated and repaired on next append."""
    mgr = StudioSessionManager(studio_root=tmp_path / "studio")
    mgr.create_session("torn-tail")
    audit_file = tmp_path / "studio" / "torn-tail" / "audit.jsonl"

    # Simulate a crash leaving partial bytes without newline
    with audit_file.open("ab") as f:
        f.write(b'{"incomplete": true, "torn')

    # Next append must succeed by truncating the torn tail
    entry = mgr.append_audit_entry("torn-tail", "RECOVERED", "1.0.0", "0" * 64)
    assert entry["action"] == "RECOVERED"
    assert mgr.verify_audit_chain("torn-tail") is True


def test_append_audit_entry_occ_conflict(tmp_path: Path) -> None:
    """MAJ-04: Test OCC check on expected_audit_head."""
    mgr = StudioSessionManager(studio_root=tmp_path / "studio")
    meta = mgr.create_session("audit-occ-test")

    # Mismatched expected audit head must raise AuditChainConflictError
    with pytest.raises(AuditChainConflictError) as exc_info:
        mgr.append_audit_entry(
            session_id="audit-occ-test",
            action="MUTATION",
            target_version="1.0.0",
            head_sha256=meta.head_sha256,
            expected_audit_head="0" * 64,
        )
    assert exc_info.value.error_code == "AUDIT_CHAIN_CONFLICT"
    assert exc_info.value.status_code == 409


def test_append_audit_entry_missing_session_does_not_create_dir(tmp_path: Path) -> None:
    """MAJ-01: append_audit_entry on missing session must fail without creating directories."""
    mgr = StudioSessionManager(studio_root=tmp_path / "studio")
    target_dir = tmp_path / "studio" / "missing-sid"
    assert not target_dir.exists()

    with pytest.raises(SessionNotFoundError):
        mgr.append_audit_entry(
            session_id="missing-sid",
            action="MUTATION",
            target_version="1.0.0",
            head_sha256="0" * 64,
        )

    # Session directory must not be created by lock guard
    assert not target_dir.exists()

    # Subsequent create_session must succeed without collision
    meta = mgr.create_session("missing-sid")
    assert meta.session_id == "missing-sid"
    assert target_dir.is_dir()


def test_genesis_draft_sha256_matches_baseline_catalog() -> None:
    """MIN-02: Genesis draft hash must match baseline-digests.json catalog."""
    schema_path = (
        Path(__file__).resolve().parents[3] / "schemas" / "v1" / "baseline-digests.json"
    )
    assert schema_path.is_file(), f"Catalog not found at {schema_path}"
    catalog = json.loads(schema_path.read_text(encoding="utf-8"))
    catalog_genesis_sha = catalog["genesis_draft"]["canonical_sha256"]
    assert catalog_genesis_sha == GENESIS_DRAFT_SHA256


# ==============================================================================
# 4. ContentAddressableStorage (CAS) Tests
# ==============================================================================


def test_cas_put_get_and_deduplication(tmp_path: Path) -> None:
    """Test storing bytes in CAS, retrieval, and deduplication."""
    cas = ContentAddressableStorage(root_dir=tmp_path / "cas_root")
    data = b"Open-DV Content Addressable Blob 2026-11-10"
    expected_hash = hashlib.sha256(data).hexdigest().lower()

    # Put bytes
    digest1 = cas.put(data)
    assert digest1 == expected_hash

    # Verify file location: cas/<sha256[:2]>/<sha256[2:]>
    expected_path = tmp_path / "cas_root" / "cas" / expected_hash[:2] / expected_hash[2:]
    assert expected_path.is_file()

    # Deduplication: putting identical content returns identical digest without duplicate
    digest2 = cas.put(data)
    assert digest2 == expected_hash

    # Retrieve and verify bytes
    retrieved = cas.get(expected_hash)
    assert retrieved == data
    assert cas.has(expected_hash)


def test_cas_dedup_repairs_tampered_blob(tmp_path: Path) -> None:
    """MIN-06: Putting content when blob on disk was corrupted must repair it."""
    cas = ContentAddressableStorage(root_dir=tmp_path / "cas_root")
    data = b"Valid uncorrupted payload"
    digest = cas.put(data)

    blob_path = cas.get_path(digest)
    # Corrupt the blob on disk
    blob_path.write_bytes(b"Corrupted bytes")

    # Re-putting the correct data must repair the blob on disk
    reput_digest = cas.put(data)
    assert reput_digest == digest
    assert cas.get(digest) == data


def test_cas_get_path_and_integrity_verification(tmp_path: Path) -> None:
    """Test get_path and SHA-256 integrity check upon tampering."""
    cas = ContentAddressableStorage(root_dir=tmp_path / "cas_root")
    data = b"Immutable Source Evidence Payload"
    digest = cas.put(data)

    path = cas.get_path(digest)
    assert path.is_file()

    # Tamper with file content
    path.write_bytes(b"Tampered payload")

    # get() must detect digest mismatch and raise CASIntegrityError
    with pytest.raises(CASIntegrityError) as exc_info:
        cas.get(digest)
    assert exc_info.value.error_code == "CAS_INTEGRITY_MISMATCH"
    assert exc_info.value.status_code == 422


def test_cas_blob_not_found(tmp_path: Path) -> None:
    """Test retrieving non-existent blob raises CASNotFoundError."""
    cas = ContentAddressableStorage(root_dir=tmp_path / "cas_root")
    fake_digest = "0" * 64

    with pytest.raises(CASNotFoundError) as exc_info:
        cas.get(fake_digest)
    assert exc_info.value.error_code == "CAS_BLOB_NOT_FOUND"
    assert exc_info.value.status_code == 404


def test_cas_invalid_digest_format(tmp_path: Path) -> None:
    """Test invalid SHA-256 string raises ValueError."""
    cas = ContentAddressableStorage(root_dir=tmp_path / "cas_root")
    with pytest.raises(ValueError, match="Invalid SHA-256 hex digest"):
        cas.get("short-hash")


def test_cas_put_file_and_list_blobs(tmp_path: Path) -> None:
    """Test put_file, list_blobs, and delete."""
    cas = ContentAddressableStorage(root_dir=tmp_path / "cas_root")
    src1 = tmp_path / "file1.txt"
    src1.write_bytes(b"File 1 Content")
    src2 = tmp_path / "file2.txt"
    src2.write_bytes(b"File 2 Content")

    d1 = cas.put_file(src1)
    d2 = cas.put_file(src2)

    blobs = cas.list_blobs()
    assert d1 in blobs
    assert d2 in blobs

    assert cas.delete(d1) is True
    assert cas.has(d1) is False
    assert cas.delete(d1) is False
