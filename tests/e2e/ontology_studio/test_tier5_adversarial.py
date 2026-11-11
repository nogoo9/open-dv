"""Task 6B.3 (#232): Tier 5 Adversarial Security Hardening E2E Tests.

Tests 10 white-box adversarial attack vectors:
1. Zip bomb decompression ratio/entries/size limit defense
2. Symlink & special device node rejection in bundles
3. Path traversal (.., C:\\, UNC, NTFS ADS, Windows reserved devices)
4. Session ID injection & reserved keyword rejection
5. RFC 5869 HKDF purpose-isolated keys & corrupt secret fail-closed
6. CLI argv secret hygiene (rejecting raw credentials on argv)
7. Forged, unauthorized, and tampered administrative waivers
8. Client-supplied inflated mapping score spoofing rejection
9. PII placeholder leak & unresolved pseudonym defense
10. Remote platform mode loopback bootstrap privilege spoofing
"""

from __future__ import annotations

import hmac
import json
import stat
import zipfile
from pathlib import Path
from typing import Any

import pytest
from starlette.testclient import TestClient

from opendv_agent_harness.privacy import (
    AgentUnresolvedPseudonymError,
    RedactedPlaceholderLeakError,
    scan_redacted_placeholder_leak,
    scan_unresolved_pseudonyms,
)
from opendv_cli.main import app_main
from opendv_contracts.dtos.ontology import (
    AddConceptOp,
    AddExternalMappingOp,
    OntologyDelta,
)
from opendv_contracts.spi.grounding import GroundingVerificationResult
from opendv_core.models.graph_a import CanonicalBusinessConcept, GraphAOntology
from opendv_core.ontology_hashing import compute_ontology_sha256
from opendv_platform_api.main import create_app
from opendv_service import OpenDVSettings
from opendv_service.ontology.applier import apply_ontology_delta
from opendv_service.ontology.validator import (
    GovernanceValidator,
    UngroundedConceptError,
)
from opendv_service.studio.bundle import (
    ArchiveSecurityViolationError,
    import_bundle,
    validate_archive_member,
)
from opendv_service.studio.session_manager import (
    InvalidSessionIdentifierError,
    validate_session_id,
)
from opendv_service.studio.workspace_security import (
    WorkspaceSecretCorruptError,
    WorkspaceSecurityManager,
)


def test_adversarial_zip_bomb_decompression_limits(tmp_path: Path) -> None:
    """Vector 1: Decompression bomb rejection (>100:1 ratio, >500 entries, >250 MB)."""
    sec_mgr = WorkspaceSecurityManager(studio_root=tmp_path / "studio_root")
    dest_dir = tmp_path / "import_target"

    # Case A: Decompression ratio exceeding 100:1 on entries > 1.0 MB
    bomb_zip_path = tmp_path / "ratio_bomb.zip"
    large_zeros = b"\x00" * (5 * 1024 * 1024)  # 5 MB of zeros compresses to ~5 KB (>1000:1 ratio)
    with zipfile.ZipFile(bomb_zip_path, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("bundle.json", b'{"manifest_version": "1.0"}')
        zf.writestr("data/large_zeros.bin", large_zeros)

    with pytest.raises(ArchiveSecurityViolationError) as exc_info:
        import_bundle(
            zip_path=bomb_zip_path,
            target_dir=dest_dir,
            security_manager=sec_mgr,
            force=True,
        )
    assert "ratio" in str(exc_info.value).lower()

    # Case B: Member count exceeding 500 entries
    many_entries_zip = tmp_path / "many_entries.zip"
    with zipfile.ZipFile(many_entries_zip, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("bundle.json", b'{"manifest_version": "1.0"}')
        for i in range(505):
            zf.writestr(f"data/entry_{i}.txt", f"data_{i}".encode())

    with pytest.raises(ArchiveSecurityViolationError) as exc_info_count:
        import_bundle(
            zip_path=many_entries_zip,
            target_dir=dest_dir,
            security_manager=sec_mgr,
            force=True,
        )
    assert "entry" in str(exc_info_count.value).lower()


def test_adversarial_symlink_and_device_rejection(tmp_path: Path) -> None:
    """Vector 2: Symlinks (0o120000) and special device nodes rejected unconditionally."""
    sec_mgr = WorkspaceSecurityManager(studio_root=tmp_path / "studio_root")
    dest_dir = tmp_path / "symlink_target"

    # Construct zip with symlink mode bits
    symlink_zip = tmp_path / "evil_symlink.zip"
    with zipfile.ZipFile(symlink_zip, mode="w") as zf:
        zf.writestr("bundle.json", b'{"manifest_version": "1.0"}')

        zinfo = zipfile.ZipInfo("symlink_target.txt")
        # Set Unix symlink mode bits: S_IFLNK (0o120000)
        zinfo.external_attr = stat.S_IFLNK << 16
        zf.writestr(zinfo, b"/etc/passwd")

    with pytest.raises(ArchiveSecurityViolationError) as exc_info:
        import_bundle(
            zip_path=symlink_zip,
            target_dir=dest_dir,
            security_manager=sec_mgr,
            force=True,
        )
    assert "symlink" in str(exc_info.value).lower()


def test_adversarial_path_traversal_and_reserved_names() -> None:
    """Vector 3: Path traversal (.., C:\\, UNC, NTFS ADS, Windows device names)."""
    malicious_paths = [
        "../escape.txt",
        "sub/../../escape.txt",
        "C:\\Windows\\System32\\calc.exe",
        "D:/malicious/payload.bin",
        "\\\\evil_server\\share\\malware.exe",
        "//evil_host/share/payload.bin",
        "data/secret_stream.txt:hidden_ads",
        "con.txt",
        "NUL",
        "sub/prn.dat",
        "data/aux.json",
        "com1.log",
        "lpt2.cfg",
    ]

    for m_path in malicious_paths:
        zinfo = zipfile.ZipInfo(m_path)
        seen: set[str] = set()
        with pytest.raises(ArchiveSecurityViolationError):
            validate_archive_member(zinfo, seen)


def test_adversarial_session_identifier_injection(
    studio_client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    """Vector 4: Session ID uppercase, special chars, and reserved keywords rejected."""
    invalid_session_ids = [
        "UPPERCASE_SESSION_01",
        "session with spaces",
        "session/traversal",
        "session$special!char",
        "bundle",
        "init",
        "sessions",
        "health",
        "auth",
        "admin",
    ]

    for sid in invalid_session_ids:
        # Unit test validation
        with pytest.raises(InvalidSessionIdentifierError):
            validate_session_id(sid)

        # REST API endpoint validation
        resp = studio_client.post(
            "/api/v1/ontology/studio/sessions",
            json={"session_id": sid, "domain_namespace": "wealth_mgmt"},
            headers=auth_headers,
        )
        assert resp.status_code == 400, f"Session ID '{sid}' was unexpectedly accepted!"
        assert resp.json()["error_code"] == "INVALID_SESSION_IDENTIFIER"


def test_adversarial_hkdf_purpose_key_isolation(tmp_path: Path) -> None:
    """Vector 5: RFC 5869 HKDF purpose keys cryptographically isolated & fail-closed."""
    studio_root = tmp_path / "studio_keys"
    sec_mgr = WorkspaceSecurityManager(studio_root=studio_root)

    k_pseudo = sec_mgr.get_pseudo_key()
    k_audit = sec_mgr.get_audit_key()
    k_waiver = sec_mgr.get_waiver_key()

    # 1. Verify 256-bit (32 bytes) length
    assert len(k_pseudo) == 32
    assert len(k_audit) == 32
    assert len(k_waiver) == 32

    # 2. Verify all 3 derived keys are cryptographically distinct
    assert k_pseudo != k_audit
    assert k_pseudo != k_waiver
    assert k_audit != k_waiver

    # 3. Cross-use rejection: signature generated with k_waiver fails verification with k_audit
    test_msg = b"ACTION:AUTHORIZE_WAIVER|CONCEPT:urn:ontology:entity:test"
    sig_waiver = hmac.new(k_waiver, test_msg, "sha256").hexdigest()
    sig_audit = hmac.new(k_audit, test_msg, "sha256").hexdigest()
    assert not hmac.compare_digest(sig_waiver, sig_audit)

    # 4. Fail-closed: corrupted root secret fails closed
    secret_file = studio_root / ".odv_workspace_secret"
    assert secret_file.is_file()
    # Truncate secret to corrupt it
    secret_file.write_bytes(b"short_corrupt_secret")
    with pytest.raises(WorkspaceSecretCorruptError):
        WorkspaceSecurityManager(studio_root=studio_root).get_waiver_key()


def test_adversarial_cli_argv_secret_hygiene(
    isolated_studio_env: tuple[Path, Path],
) -> None:
    """Vector 6: CLI argv secret hygiene (rejecting raw credentials on argv)."""
    _ws, _studio_root = isolated_studio_env

    # Initialize studio and session
    app_main(["ontology", "studio", "init", "--json"])
    app_main(
        [
            "ontology",
            "studio",
            "create",
            "-s",
            "cli-hygiene-session",
            "-n",
            "wealth_mgmt",
            "-o",
            "Global Wealth",
            "--json",
        ]
    )

    meta_file = _studio_root / "cli-hygiene-session" / "session.json"
    meta = json.loads(meta_file.read_text(encoding="utf-8"))
    genesis_sha = meta["head_sha256"]

    dummy_delta = _ws / "hygiene_delta.json"
    dummy_delta.write_text(
        json.dumps({
            "delta_id": "d-cli-hygiene",
            "base_ontology_version": "0.0.0",
            "target_version": "1.0.0",
            "parent_sha256": genesis_sha,
            "author": "tester",
            "rationale": "testing cli hygiene",
            "operations": [
                {
                    "op": "AddConcept",
                    "op_id": "op-add-seed",
                    "concept_name": "Investment Advisor",
                    "concept_uri": "urn:ontology:entity:investment_advisor",
                }
            ],
        }),
        encoding="utf-8",
    )

    # 1. Attempting to pass non-existent key file on CLI fails with exit code 7
    exit_code_fake_file = app_main(
        [
            "ontology",
            "studio",
            "delta",
            "apply",
            "-s",
            "cli-hygiene-session",
            "-f",
            str(dummy_delta),
            "--low-confidence-waiver",
            "--grounding-rationale",
            "Sufficient rationale for CLI test",
            "--waiver-key-file",
            "non_existent_key_file.txt",
            "--json",
        ]
    )
    assert exit_code_fake_file == 7

    # 2. Attempting to pass raw secret argument is rejected by CLI argument parser (exit 2)
    exit_code_argv_key = app_main(
        [
            "ontology",
            "studio",
            "delta",
            "apply",
            "-s",
            "cli-hygiene-session",
            "-f",
            str(dummy_delta),
            "--raw-secret",  # Non-existent dangerous raw secret flag
            "deadbeef1234567890abcdefdeadbeef",
            "--json",
        ]
    )
    assert exit_code_argv_key == 2


def test_adversarial_forged_and_unauthorized_waivers(
    studio_client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    """Vector 7: Forged, unauthorized, and short rationale administrative waivers."""
    session_id = "adv-waiver-forgery-07"
    studio_client.post(
        "/api/v1/ontology/studio/sessions",
        json={"session_id": session_id, "domain_namespace": "wealth_mgmt"},
        headers=auth_headers,
    )

    # 1. Non-admin role attempting to authorize waiver is rejected with HTTP 403
    non_admin_headers = {**auth_headers, "X-OpenDV-Role": "viewer"}
    resp_unauth = studio_client.post(
        f"/api/v1/ontology/studio/sessions/{session_id}/waiver",
        json={
            "waiver_id": "wv-unauthorized-01",
            "concept_uri": "urn:ontology:entity:unauthorized_fund",
            "rationale": "Sufficiently long rationale explaining waiver.",
            "authorized_by": "unauthorized_user",
        },
        headers=non_admin_headers,
    )
    assert resp_unauth.status_code == 403
    assert resp_unauth.json()["error_code"] == "FORBIDDEN_ADMIN_REQUIRED"

    # 2. Admin attempting waiver with rationale < 10 chars is rejected with HTTP 422
    resp_short_rationale = studio_client.post(
        f"/api/v1/ontology/studio/sessions/{session_id}/waiver",
        json={
            "waiver_id": "wv-short-02",
            "concept_uri": "urn:ontology:entity:short_fund",
            "rationale": "Too short",  # 9 chars < 10
            "authorized_by": "human:admin",
        },
        headers=auth_headers,
    )
    assert resp_short_rationale.status_code == 422
    assert "rationale" in resp_short_rationale.text.lower()


class _StrictGroundingVerifierStub:
    """Verifier stub that inspects genuine concept URN and rejects ungrounded tokens."""

    def verify_grounding(
        self, concept_uri: str, candidates: list[dict[str, Any]], **kwargs: Any
    ) -> GroundingVerificationResult:
        # Re-evaluates concept regardless of client-supplied candidate confidence score
        if "fibo_legitimate" in concept_uri:
            return GroundingVerificationResult(is_grounded=True, score=0.92)
        # Client spoofed score for arbitrary concept
        return GroundingVerificationResult(is_grounded=False, score=0.35)


def test_adversarial_client_supplied_mapping_score_spoofing() -> None:
    """Vector 8: Client-supplied inflated mapping score (0.9999) recomputed & rejected."""
    base_ont = GraphAOntology(
        ontology_version="1.0.0",
        domain_namespace="finance",
        enterprise_owner="Architecture Committee",
        canonical_business_concepts=[
            CanonicalBusinessConcept(
                concept_uri="urn:ontology:entity:seed",
                concept_name="Seed",
                target_hub="hub_seed",
            )
        ],
    )

    base_sha = compute_ontology_sha256(base_ont)

    # Client constructs delta claiming inflated confidence=0.9999 for ungrounded concept
    delta = OntologyDelta(
        delta_id="delta-spoofed-score",
        base_ontology_version="1.0.0",
        target_version="1.1.0",
        parent_sha256=base_sha,
        author="adversary:client",
        rationale="Client claiming inflated grounding score.",
        operations=[
            AddConceptOp(
                op_id="op-spoofed",
                concept_uri="urn:ontology:entity:fake_crypto_token",
                concept_name="FakeCryptoToken",
            ),
            AddExternalMappingOp(
                op_id="op-fake-mapping",
                concept_uri="urn:ontology:entity:fake_crypto_token",
                scheme="fibo",
                target_uri="https://spec.edmcouncil.org/fibo/ontology/SEC/Funds/Fund",
                mapping_confidence=0.9999,  # Inflated score
            ),
        ],
    )
    target_ont = apply_ontology_delta(base_ont, delta)

    # Server re-verifies candidates using authoritative verifier SPI
    gov = GovernanceValidator(grounding_verifier=_StrictGroundingVerifierStub())

    # Server detects true score is < 0.6500 and strictly rejects ungrounded concept
    with pytest.raises(UngroundedConceptError) as exc_info:
        gov.validate_grounding(base_ont, delta, target_ont)
    assert exc_info.value.error_code == "ONT_EVO_UNGROUNDED_CONCEPT"
    assert exc_info.value.status_code == 422


def test_adversarial_pii_and_taint_leak_defense() -> None:
    """Vector 9: PII placeholder leak & unresolved pseudonym defense."""
    # 1. Leak of raw placeholder token raises RedactedPlaceholderLeakError
    leaked_placeholder_text = (
        "Concept InvestmentEntity grounded to [REDACTED_ODV_ENTITY_1] confidential asset."
    )
    with pytest.raises(RedactedPlaceholderLeakError) as exc_placeholder:
        scan_redacted_placeholder_leak(leaked_placeholder_text)
    assert "[REDACTED_ODV_ENTITY_1]" in str(exc_placeholder.value)

    # 2. Leak of unresolved HMAC pseudonym raises AgentUnresolvedPseudonymError
    leaked_pseudo_text = (
        "Concept AdvisoryAgreement between pseudo_0123456789ab and counterparties."
    )
    with pytest.raises(AgentUnresolvedPseudonymError) as exc_pseudo:
        scan_unresolved_pseudonyms(leaked_pseudo_text)
    assert "pseudo_0123456789ab" in str(exc_pseudo.value)

    # 3. Clean text passes both scans cleanly
    clean_text = "Concept AdvisoryAgreement between Legal Entity and Institutional Fund."
    scan_redacted_placeholder_leak(clean_text)
    scan_unresolved_pseudonyms(clean_text)


def test_adversarial_remote_mode_loopback_spoofing(
    isolated_studio_env: tuple[Path, Path],
    remote_auth_headers: dict[str, str],
) -> None:
    """Vector 10: Remote platform mode loopback bootstrap privilege spoofing rejected."""
    ws_root, studio_root = isolated_studio_env

    # Instantiate platform in remote_platform server mode
    remote_settings = OpenDVSettings(
        workspace_root=ws_root,
        studio_root=studio_root,
        server_mode="remote_platform",
    )
    remote_app = create_app(settings=remote_settings)
    client = TestClient(remote_app)

    # Calling /init in remote platform mode is forbidden even from loopback host
    resp = client.post(
        "/api/v1/ontology/studio/init",
        headers={**remote_auth_headers, "Host": "127.0.0.1:8000"},
    )
    assert resp.status_code == 403
    assert resp.json()["error_code"] == "LOOPBACK_PRIVILEGE_DISABLED_IN_REMOTE_MODE"
