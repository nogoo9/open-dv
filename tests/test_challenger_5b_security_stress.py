"""Empirical Adversarial & Security Stress Test Suite for Track 5B (Milestone M5).

Authored by challenger_5b_2 (Security Challenger).
Conducts empirical security & boundary stress verification across 4 core vectors:
1. CSRF bypass attempts & origin validation on mutating requests
2. Remote mode loopback spoofing & proxy header injection guards
3. RS256 JWT claim fuzzing (expired, wrong aud/iss, invalid signatures, alg confusion/none)
4. Multi-format RDF exporter roundtrip & malformed input fuzzing
   (graph isomorphism, Unicode, empty graphs)
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import jwt
import pytest
import yaml
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from rdflib import Graph
from rdflib.compare import isomorphic
from starlette.testclient import TestClient

from opendv_core.models.graph_a import (
    CanonicalBusinessConcept,
    CanonicalRelationship,
    ConceptAttribute,
    ExternalMapping,
    GraphAOntology,
    ReferenceEnumeration,
)
from opendv_platform_api.main import create_app
from opendv_service import OpenDVSettings
from opendv_service.export.rdf_exporter import export_ontology

# Primary RSA keypair for remote platform mode
_VALID_RSA_PRIVATE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_VALID_RSA_PUBLIC_KEY_PEM = (
    _VALID_RSA_PRIVATE_KEY.public_key()
    .public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    .decode("utf-8")
)

# Adversary / attacker RSA keypair for signature forgery attacks
_ATTACKER_RSA_PRIVATE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)


def mint_jwt_token(
    subject: str = "user:security_auditor",
    role: str = "admin",
    expires_in: float = 3600.0,
    issuer: str = "opendv-platform",
    audience: str = "opendv-api",
    key: Any = _VALID_RSA_PRIVATE_KEY,
    algorithm: str = "RS256",
    headers: dict[str, Any] | None = None,
    custom_claims: dict[str, Any] | None = None,
) -> str:
    """Helper to mint arbitrary JWT tokens for adversarial fuzzing."""
    now = int(time.time())
    payload: dict[str, Any] = {
        "sub": subject,
        "role": role,
        "iss": issuer,
        "aud": audience,
        "iat": now,
        "exp": int(now + expires_in),
    }
    if custom_claims:
        payload.update(custom_claims)
    return jwt.encode(payload, key, algorithm=algorithm, headers=headers)


@pytest.fixture(autouse=True)
def isolate_studio_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Ensure clean isolated studio root and workspace for every test."""
    studio_root = tmp_path / "opendv_studio"
    studio_root.mkdir(parents=True, exist_ok=True)
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("OPENDV_STUDIO_ROOT", str(studio_root))
    monkeypatch.setenv("OPENDV_WORKSPACE_ROOT", str(workspace_root))
    monkeypatch.setenv("OPENDV_SERVER_MODE", "local_standalone")
    monkeypatch.delenv("OPENDV_TOKEN", raising=False)


@pytest.fixture
def local_client(tmp_path: Path) -> TestClient:
    """Create local TestClient representing loopback connection."""
    settings = OpenDVSettings.load(
        workspace_root=tmp_path / "workspace",
        state_dir=tmp_path / "state",
        is_server=True,
    )
    app = create_app(settings=settings)
    return TestClient(app, client=("127.0.0.1", 52000), base_url="http://localhost:8000")


@pytest.fixture
def session_cookie(local_client: TestClient) -> str:
    """Initialize studio on loopback and complete launch nonce exchange to get session cookie."""
    init_resp = local_client.post(
        "/api/v1/ontology/studio/init",
        json={"domain_namespace": "stress_sec", "enterprise_owner": "Stress Security Test"},
    )
    assert init_resp.status_code == 200, init_resp.text
    local_token = init_resp.json()["local_token"]

    nonce_resp = local_client.post(
        "/api/v1/ontology/studio/auth/launch-nonce",
        headers={"Authorization": f"Bearer {local_token}"},
    )
    assert nonce_resp.status_code == 200
    nonce = nonce_resp.json()["launch_nonce"]

    exchange_resp = local_client.post(
        "/api/v1/ontology/studio/auth/token",
        json={"launch_nonce": nonce},
    )
    assert exchange_resp.status_code == 200
    cookie_val = exchange_resp.cookies.get("opendv_session")
    assert cookie_val is not None
    return cookie_val


# ===========================================================================
# Category 1: CSRF Bypass Attempts & Origin Stress Testing
# ===========================================================================


class TestCSRFBypassAttempts:
    """Adversarial stress-testing of CSRF protection on mutating cookie-authenticated requests."""

    def test_missing_csrf_header_rejected(
        self, local_client: TestClient, session_cookie: str
    ) -> None:
        """Attempt mutating requests with missing X-OpenDV-Workbench header."""
        cookie_client = TestClient(
            local_client.app, client=("127.0.0.1", 52001), base_url="http://localhost:8000"
        )
        cookie_client.cookies.set("opendv_session", session_cookie)

        # POST /sessions without X-OpenDV-Workbench
        resp = cookie_client.post(
            "/api/v1/ontology/studio/sessions",
            json={"session_id": "csrf-fail-missing-header", "domain_namespace": "sec"},
        )
        assert resp.status_code == 403
        data = resp.json()
        assert data["error_code"] == "CSRF_ORIGIN_MISMATCH"
        assert "X-OpenDV-Workbench: 1" in data["detail"]

    @pytest.mark.parametrize(
        "invalid_header_value",
        ["0", "true", "false", "", "2", "yes", "None", "1.0", "bearer"],
    )
    def test_invalid_csrf_header_values_rejected(
        self, local_client: TestClient, session_cookie: str, invalid_header_value: str
    ) -> None:
        """Attempt mutating request with non-'1' X-OpenDV-Workbench header values."""
        cookie_client = TestClient(
            local_client.app, client=("127.0.0.1", 52001), base_url="http://localhost:8000"
        )
        cookie_client.cookies.set("opendv_session", session_cookie)

        resp = cookie_client.post(
            "/api/v1/ontology/studio/sessions",
            json={"session_id": f"csrf-val-{invalid_header_value}", "domain_namespace": "sec"},
            headers={"X-OpenDV-Workbench": invalid_header_value},
        )
        assert resp.status_code == 403
        assert resp.json()["error_code"] == "CSRF_ORIGIN_MISMATCH"

    def test_origin_null_rejected(self, local_client: TestClient, session_cookie: str) -> None:
        """Attempt mutating request with Origin: null.

        Simulates sandboxed iframes, file:// URLs, or privacy browsing modes.
        """
        cookie_client = TestClient(
            local_client.app, client=("127.0.0.1", 52001), base_url="http://localhost:8000"
        )
        cookie_client.cookies.set("opendv_session", session_cookie)

        resp = cookie_client.post(
            "/api/v1/ontology/studio/sessions",
            json={"session_id": "csrf-null-origin", "domain_namespace": "sec"},
            headers={
                "X-OpenDV-Workbench": "1",
                "Origin": "null",
                "Host": "localhost:8000",
            },
        )
        assert resp.status_code == 403
        data = resp.json()
        assert data["error_code"] == "CSRF_ORIGIN_MISMATCH"
        assert "null" in data["detail"]

    @pytest.mark.parametrize(
        "malicious_origin",
        [
            "https://malicious-attacker.com",
            "http://malicious-attacker.com:8000",
            "http://localhost.evil.com",
            "http://localhost.evil.com:8000",
            "http://127.0.0.1.attacker.com",
            "http://127.0.0.1.attacker.com:8000",
            "http://attacker.com#localhost:8000",
            "http://evil-localhost:8000",
            "http://not-localhost:8000",
            "https://subdomain.localhost.attacker.org",
        ],
    )
    def test_cross_origin_and_forged_loopback_rejected(
        self, local_client: TestClient, session_cookie: str, malicious_origin: str
    ) -> None:
        """Attempt mutating request with cross-origin or forged loopback Origin."""
        cookie_client = TestClient(
            local_client.app, client=("127.0.0.1", 52001), base_url="http://localhost:8000"
        )
        cookie_client.cookies.set("opendv_session", session_cookie)

        resp = cookie_client.post(
            "/api/v1/ontology/studio/sessions",
            json={"session_id": "csrf-forged-test", "domain_namespace": "sec"},
            headers={
                "X-OpenDV-Workbench": "1",
                "Origin": malicious_origin,
                "Host": "localhost:8000",
            },
        )
        assert resp.status_code == 403
        data = resp.json()
        assert data["error_code"] == "CSRF_ORIGIN_MISMATCH"

    def test_malicious_referer_rejected_when_origin_missing(
        self, local_client: TestClient, session_cookie: str
    ) -> None:
        """Attempt mutating request with omitted Origin and malicious Referer."""
        cookie_client = TestClient(
            local_client.app, client=("127.0.0.1", 52001), base_url="http://localhost:8000"
        )
        cookie_client.cookies.set("opendv_session", session_cookie)

        resp = cookie_client.post(
            "/api/v1/ontology/studio/sessions",
            json={"session_id": "csrf-referer-test", "domain_namespace": "sec"},
            headers={
                "X-OpenDV-Workbench": "1",
                "Referer": "https://malicious-attacker.com/exploit.html",
                "Host": "localhost:8000",
            },
        )
        assert resp.status_code == 403
        assert resp.json()["error_code"] == "CSRF_ORIGIN_MISMATCH"

    @pytest.mark.parametrize(
        "valid_origin",
        [
            "http://localhost:8000",
            "http://127.0.0.1:8000",
            "http://[::1]:8000",
        ],
    )
    def test_legitimate_loopback_origins_succeed(
        self, local_client: TestClient, session_cookie: str, valid_origin: str
    ) -> None:
        """Verify that genuine loopback origins are permitted for Workbench UI."""
        cookie_client = TestClient(
            local_client.app, client=("127.0.0.1", 52001), base_url="http://localhost:8000"
        )
        cookie_client.cookies.set("opendv_session", session_cookie)

        sid = f"valid-origin-{int(time.time() * 1000) % 100000}"
        resp = cookie_client.post(
            "/api/v1/ontology/studio/sessions",
            json={"session_id": sid, "domain_namespace": "sec"},
            headers={
                "X-OpenDV-Workbench": "1",
                "Origin": valid_origin,
                "Host": "localhost:8000",
            },
        )
        assert resp.status_code == 201
        assert resp.json()["session_id"] == sid


# ===========================================================================
# Category 2: Remote Mode Loopback Spoofing & Header Injection
# ===========================================================================


class TestRemoteModeLoopbackSpoofing:
    """Stress-testing perimeter defense: disabling loopback bootstrap in remote mode."""

    def test_remote_mode_init_rejected_from_direct_loopback_socket(
        self, local_client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """In remote_platform mode, direct 127.0.0.1 socket calling /init is rejected with 403."""
        monkeypatch.setenv("OPENDV_SERVER_MODE", "remote_platform")

        resp = local_client.post(
            "/api/v1/ontology/studio/init",
            json={"domain_namespace": "remote_test"},
        )
        assert resp.status_code == 403
        data = resp.json()
        assert data["error_code"] == "LOOPBACK_PRIVILEGE_DISABLED_IN_REMOTE_MODE"
        assert "remote_platform mode" in data["detail"]

    def test_remote_mode_init_rejected_with_spoofed_forwarded_headers(
        self, local_client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """In remote_platform mode, spoofed X-Forwarded-For: 127.0.0.1 cannot bypass the block."""
        monkeypatch.setenv("OPENDV_SERVER_MODE", "remote_platform")

        resp = local_client.post(
            "/api/v1/ontology/studio/init",
            json={"domain_namespace": "remote_test"},
            headers={
                "X-Forwarded-For": "127.0.0.1",
                "X-Real-IP": "127.0.0.1",
                "Forwarded": "for=127.0.0.1;proto=http",
            },
        )
        assert resp.status_code == 403
        assert resp.json()["error_code"] == "LOOPBACK_PRIVILEGE_DISABLED_IN_REMOTE_MODE"

    def test_remote_mode_launch_nonce_and_token_exchange_rejected(
        self, local_client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """In remote_platform mode, /auth/launch-nonce and /auth/token are blocked."""
        monkeypatch.setenv("OPENDV_SERVER_MODE", "remote_platform")
        monkeypatch.setenv("OPENDV_JWT_PUBLIC_KEY", _VALID_RSA_PUBLIC_KEY_PEM)

        valid_jwt = mint_jwt_token(subject="user:admin", role="admin")

        # 1. /auth/launch-nonce rejected
        nonce_resp = local_client.post(
            "/api/v1/ontology/studio/auth/launch-nonce",
            headers={
                "Authorization": f"Bearer {valid_jwt}",
                "X-Forwarded-For": "127.0.0.1",
            },
        )
        assert nonce_resp.status_code == 403
        assert nonce_resp.json()["error_code"] == "LOOPBACK_PRIVILEGE_DISABLED_IN_REMOTE_MODE"

        # 2. /auth/token rejected
        token_resp = local_client.post(
            "/api/v1/ontology/studio/auth/token",
            json={"launch_nonce": "forged-nonce-attempt"},
            headers={"X-Forwarded-For": "127.0.0.1"},
        )
        assert token_resp.status_code == 403
        assert token_resp.json()["error_code"] == "LOOPBACK_PRIVILEGE_DISABLED_IN_REMOTE_MODE"

    def test_local_mode_rejects_external_socket_even_with_spoofed_forwarded_for(
        self, local_client: TestClient
    ) -> None:
        """In local_standalone mode, external socket spoofing X-Forwarded-For is rejected."""
        # Initialize studio on loopback first to generate genuine local token
        init_resp = local_client.post(
            "/api/v1/ontology/studio/init",
            json={"domain_namespace": "spoof_base"},
        )
        assert init_resp.status_code == 200
        valid_local_token = init_resp.json()["local_token"]

        external_client = TestClient(
            local_client.app,
            client=("198.51.100.77", 48000),  # Public/external IP socket
            base_url="http://remote-server:8000",
        )

        spoofed_headers = {
            "X-Forwarded-For": "127.0.0.1",
            "X-Real-IP": "127.0.0.1",
            "Forwarded": "for=127.0.0.1",
        }

        # 1. /init rejected with 403
        resp_init = external_client.post(
            "/api/v1/ontology/studio/init",
            json={"domain_namespace": "spoof_attempt"},
            headers=spoofed_headers,
        )
        assert resp_init.status_code == 403
        assert resp_init.json()["error_code"] == "LOOPBACK_PRIVILEGE_DISABLED_IN_REMOTE_MODE"

        # 2. /auth/launch-nonce with valid local token but external socket is rejected with 403
        resp_nonce = external_client.post(
            "/api/v1/ontology/studio/auth/launch-nonce",
            headers={"Authorization": f"Bearer {valid_local_token}", **spoofed_headers},
        )
        assert resp_nonce.status_code == 403
        assert resp_nonce.json()["error_code"] == "LOOPBACK_PRIVILEGE_DISABLED_IN_REMOTE_MODE"

        # 3. /auth/token with external socket is rejected with 403
        resp_token = external_client.post(
            "/api/v1/ontology/studio/auth/token",
            json={"launch_nonce": "nonce-123"},
            headers=spoofed_headers,
        )
        assert resp_token.status_code == 403
        assert resp_token.json()["error_code"] == "LOOPBACK_PRIVILEGE_DISABLED_IN_REMOTE_MODE"


# ===========================================================================
# Category 3: RS256 JWT Claim Fuzzing & Algorithm Enforcement
# ===========================================================================


class TestRS256JWTClaimFuzzing:
    """Adversarial claim fuzzing against PyJWT RS256 verification in remote_platform mode."""

    @pytest.fixture(autouse=True)
    def configure_remote_mode(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("OPENDV_SERVER_MODE", "remote_platform")
        monkeypatch.setenv("OPENDV_JWT_PUBLIC_KEY", _VALID_RSA_PUBLIC_KEY_PEM)
        monkeypatch.setenv("OPENDV_JWT_ISSUER", "opendv-platform")
        monkeypatch.setenv("OPENDV_JWT_AUDIENCE", "opendv-api")

    def test_expired_token_rejected(self, local_client: TestClient) -> None:
        """Token with exp in the past (beyond 60s clock skew) fails with 401."""
        expired_token = mint_jwt_token(expires_in=-120)  # Expired 2 minutes ago
        resp = local_client.get(
            "/api/v1/ontology/studio/sessions",
            headers={"Authorization": f"Bearer {expired_token}"},
        )
        assert resp.status_code == 401
        data = resp.json()
        assert data["error_code"] == "UNAUTHENTICATED"
        assert "Signature has expired" in data["detail"]

    def test_wrong_issuer_rejected(self, local_client: TestClient) -> None:
        """Token with untrusted issuer fails with 401."""
        bad_iss_token = mint_jwt_token(issuer="https://evil-identity-provider.com")
        resp = local_client.get(
            "/api/v1/ontology/studio/sessions",
            headers={"Authorization": f"Bearer {bad_iss_token}"},
        )
        assert resp.status_code == 401
        data = resp.json()
        assert data["error_code"] == "UNAUTHENTICATED"
        assert "Invalid issuer" in data["detail"]

    def test_wrong_audience_rejected(self, local_client: TestClient) -> None:
        """Token with wrong audience fails with 401."""
        bad_aud_token = mint_jwt_token(audience="other-opendv-service")
        resp = local_client.get(
            "/api/v1/ontology/studio/sessions",
            headers={"Authorization": f"Bearer {bad_aud_token}"},
        )
        assert resp.status_code == 401
        data = resp.json()
        assert data["error_code"] == "UNAUTHENTICATED"
        assert "Audience doesn't match" in data["detail"] or "Invalid audience" in data["detail"]

    def test_attacker_rsa_signature_rejected(self, local_client: TestClient) -> None:
        """Token signed with attacker's private key fails signature verification."""
        forged_token = mint_jwt_token(
            subject="user:attacker_admin",
            role="admin",
            key=_ATTACKER_RSA_PRIVATE_KEY,  # Wrong key
        )
        resp = local_client.get(
            "/api/v1/ontology/studio/sessions",
            headers={"Authorization": f"Bearer {forged_token}"},
        )
        assert resp.status_code == 401
        data = resp.json()
        assert data["error_code"] == "UNAUTHENTICATED"
        assert (
            "Signature verification failed" in data["detail"]
            or "Invalid signature" in data["detail"]
        )

    def test_corrupted_signature_bytes_rejected(self, local_client: TestClient) -> None:
        """Token with modified/corrupted signature bytes fails with 401."""
        valid_token = mint_jwt_token(subject="user:alice", role="admin")
        parts = valid_token.split(".")
        # Corrupt the signature segment
        corrupted_sig = parts[2][:-4] + "XXXX"
        tampered_token = f"{parts[0]}.{parts[1]}.{corrupted_sig}"

        resp = local_client.get(
            "/api/v1/ontology/studio/sessions",
            headers={"Authorization": f"Bearer {tampered_token}"},
        )
        assert resp.status_code == 401
        assert resp.json()["error_code"] == "UNAUTHENTICATED"

    def test_algorithm_none_rejected(self, local_client: TestClient) -> None:
        """Algorithm 'none' (unsigned JWT vulnerability attack) is strictly rejected."""
        now = int(time.time())
        unsigned_payload = {
            "sub": "user:attacker",
            "role": "admin",
            "iss": "opendv-platform",
            "aud": "opendv-api",
            "iat": now,
            "exp": now + 3600,
        }
        none_token = jwt.encode(unsigned_payload, key="", algorithm="none")

        resp = local_client.get(
            "/api/v1/ontology/studio/sessions",
            headers={"Authorization": f"Bearer {none_token}"},
        )
        assert resp.status_code == 401
        data = resp.json()
        assert data["error_code"] == "UNAUTHENTICATED"
        assert "forbidden" in data["detail"] or "RS256" in data["detail"]

    @pytest.mark.parametrize("disallowed_alg", ["HS256", "HS384", "HS512", "RS384"])
    def test_disallowed_algorithms_rejected(
        self, local_client: TestClient, disallowed_alg: str
    ) -> None:
        """Algorithms other than RS256 fail algorithm check."""
        now = int(time.time())
        payload = {
            "sub": "user:attacker",
            "role": "admin",
            "iss": "opendv-platform",
            "aud": "opendv-api",
            "iat": now,
            "exp": now + 3600,
        }

        if disallowed_alg.startswith("HS"):
            token = jwt.encode(
                payload, key="symmetric-secret-key-32-bytes-!!", algorithm=disallowed_alg
            )
        elif disallowed_alg == "RS384":
            token = jwt.encode(payload, key=_VALID_RSA_PRIVATE_KEY, algorithm=disallowed_alg)
        else:
            return

        resp = local_client.get(
            "/api/v1/ontology/studio/sessions",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 401
        data = resp.json()
        assert data["error_code"] == "UNAUTHENTICATED"
        assert (
            f"Algorithm '{disallowed_alg}' forbidden" in data["detail"]
            or "only RS256 is permitted" in data["detail"]
        )

    @pytest.mark.parametrize("missing_claim", ["exp", "sub", "iss", "aud"])
    def test_missing_mandatory_claims_rejected(
        self, local_client: TestClient, missing_claim: str
    ) -> None:
        """Tokens omitting any mandatory claim (exp, sub, iss, aud) fail with 401."""
        now = int(time.time())
        claims = {
            "sub": "user:test",
            "role": "admin",
            "iss": "opendv-platform",
            "aud": "opendv-api",
            "iat": now,
            "exp": now + 3600,
        }
        claims.pop(missing_claim)

        token = jwt.encode(claims, key=_VALID_RSA_PRIVATE_KEY, algorithm="RS256")
        resp = local_client.get(
            "/api/v1/ontology/studio/sessions",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 401
        data = resp.json()
        assert data["error_code"] == "UNAUTHENTICATED"

    @pytest.mark.parametrize("illegal_role", ["root", "SYSTEM", "superuser", "owner"])
    def test_illegal_role_claim_rejected(self, local_client: TestClient, illegal_role: str) -> None:
        """JWT with unrecognized role claim fails with 401 UNAUTHENTICATED."""
        bad_role_token = mint_jwt_token(role=illegal_role)
        resp = local_client.get(
            "/api/v1/ontology/studio/sessions",
            headers={"Authorization": f"Bearer {bad_role_token}"},
        )
        assert resp.status_code == 401
        data = resp.json()
        assert data["error_code"] == "UNAUTHENTICATED"
        assert "not a valid role" in data["detail"]

    def test_empty_role_claim_privilege_boundary(self, local_client: TestClient) -> None:
        """JWT with empty role defaults to viewer and is blocked from admin mutations."""
        empty_role_token = mint_jwt_token(role="")
        # 1. Viewer can read sessions
        resp_read = local_client.get(
            "/api/v1/ontology/studio/sessions",
            headers={"Authorization": f"Bearer {empty_role_token}"},
        )
        assert resp_read.status_code == 200

        # 2. But cannot perform admin operations (e.g. DELETE /sessions/{id})
        resp_admin = local_client.delete(
            "/api/v1/ontology/studio/sessions/any-session-id",
            headers={"Authorization": f"Bearer {empty_role_token}"},
        )
        assert resp_admin.status_code == 403
        assert resp_admin.json()["error_code"] == "FORBIDDEN_ADMIN_REQUIRED"


# ===========================================================================
# Category 4: Multi-Format RDF Exporter Roundtrip & Malformed Input Fuzzing
# ===========================================================================


class TestRDFExporterRoundtripAndFuzzing:
    """Stress-testing multi-format RDF export: isomorphism, Unicode, minimal graphs, edge cases."""

    @pytest.fixture
    def rich_ontology(self) -> GraphAOntology:
        """Construct a comprehensive GraphAOntology fixture conforming to Graph A schema."""
        return GraphAOntology(
            ontology_version="1.2.0",
            domain_namespace="wealth_mgmt",
            enterprise_owner="Global Wealth Management Ltd",
            description="Enterprise Wealth Management Domain Ontology",
            canonical_business_concepts=[
                CanonicalBusinessConcept(
                    concept_uri="urn:ontology:entity:party",
                    concept_name="Party",
                    definition="A natural person, corporate body, or trust.",
                    synonyms=["Entity", "Legal Subject"],
                    target_hub="hub_party",
                    attributes=[
                        ConceptAttribute(
                            attribute_name="legal_name",
                            data_type="string",
                            description="Full legal name",
                        ),
                        ConceptAttribute(
                            attribute_name="total_assets",
                            data_type="decimal",
                            description="Aggregate assets under management",
                        ),
                        ConceptAttribute(
                            attribute_name="is_resident",
                            data_type="boolean",
                            description="Tax residency flag",
                        ),
                    ],
                    external_mappings=[
                        ExternalMapping(
                            scheme="fibo",
                            target_uri="https://spec.edmcouncil.org/fibo/ontology/FBC/FunctionalEntities/FinancialServicesEntities/FinancialServiceProvider",
                            predicate="skos:exactMatch",
                            confidence=0.98,
                        )
                    ],
                ),
                CanonicalBusinessConcept(
                    concept_uri="urn:ontology:entity:customer",
                    concept_name="Customer",
                    definition="A party that purchases financial advisory services.",
                    parent_concept_uri="urn:ontology:entity:party",
                    synonyms=["Client"],
                    target_hub="hub_customer",
                    attributes=[
                        ConceptAttribute(
                            attribute_name="onboarding_date",
                            data_type="date",
                            description="Date account was opened",
                        ),
                        ConceptAttribute(
                            attribute_name="risk_tolerance_score",
                            data_type="integer",
                            description="Numerical risk assessment score (1-10)",
                        ),
                    ],
                ),
            ],
            canonical_relationships=[
                CanonicalRelationship(
                    relationship_uri="urn:ontology:relationship:party_guarantees_customer",
                    relationship_name="Guarantees",
                    endpoints=[
                        "urn:ontology:entity:party",
                        "urn:ontology:entity:customer",
                    ],
                    target_link="lnk_party_guarantees_customer",
                    grain_semantics="One party may guarantee zero or more customer credit lines.",
                )
            ],
            reference_enumerations=[
                ReferenceEnumeration(
                    enum_uri="urn:ontology:enum:customer_risk_level",
                    target_reference_table="ref_customer_risk_level",
                    enumeration_name="CustomerRiskLevel",
                    description="Standardized portfolio risk tiers",
                    allowed_codes=["LOW", "MEDIUM", "HIGH", "SPECULATIVE"],
                )
            ],
        )

    def test_export_all_five_formats_succeed(self, rich_ontology: GraphAOntology) -> None:
        """Verify export succeeds across all 5 canonical formats."""
        formats = ["turtle", "jsonld", "ntriples", "yaml", "json"]
        for fmt in formats:
            content, media_type = export_ontology(rich_ontology, export_format=fmt)
            assert content, f"Empty export for format {fmt}"
            assert media_type, f"Missing media type for format {fmt}"

    def test_graph_isomorphism_across_rdf_formats(self, rich_ontology: GraphAOntology) -> None:
        """Verify rdflib parses Turtle, JSON-LD, and NTriples into isomorphic RDF graphs."""
        ttl_content, _ = export_ontology(rich_ontology, export_format="turtle")
        jsonld_content, _ = export_ontology(rich_ontology, export_format="jsonld")
        nt_content, _ = export_ontology(rich_ontology, export_format="ntriples")

        g_ttl = Graph()
        g_ttl.parse(data=ttl_content, format="turtle")

        g_jsonld = Graph()
        g_jsonld.parse(data=jsonld_content, format="json-ld")

        g_nt = Graph()
        g_nt.parse(data=nt_content, format="nt")

        # Check triple counts
        assert len(g_ttl) == len(g_jsonld)
        assert len(g_ttl) == len(g_nt)
        assert len(g_ttl) > 20

        # Assert graph isomorphism
        assert isomorphic(g_ttl, g_jsonld), "Turtle and JSON-LD graphs are not isomorphic!"
        assert isomorphic(g_ttl, g_nt), "Turtle and NTriples graphs are not isomorphic!"
        assert isomorphic(g_jsonld, g_nt), "JSON-LD and NTriples graphs are not isomorphic!"

    def test_minimal_ontology_roundtrip(self) -> None:
        """Stress-test export of minimal ontology (1 concept, 0 relationships, 0 enums)."""
        minimal_ont = GraphAOntology(
            ontology_version="1.0.0",
            domain_namespace="minimal_test",
            canonical_business_concepts=[
                CanonicalBusinessConcept(
                    concept_uri="urn:ontology:entity:root_entity",
                    concept_name="RootEntity",
                    definition="Minimal base entity without optional extensions.",
                )
            ],
            canonical_relationships=[],
            reference_enumerations=[],
        )

        for fmt in ("turtle", "jsonld", "ntriples", "yaml", "json"):
            content, _mt = export_ontology(minimal_ont, export_format=fmt)
            assert content

        # Check isomorphism on minimal graph
        ttl_content, _ = export_ontology(minimal_ont, export_format="turtle")
        jsonld_content, _ = export_ontology(minimal_ont, export_format="jsonld")
        nt_content, _ = export_ontology(minimal_ont, export_format="ntriples")

        g_ttl = Graph().parse(data=ttl_content, format="turtle")
        g_jsonld = Graph().parse(data=jsonld_content, format="json-ld")
        g_nt = Graph().parse(data=nt_content, format="nt")

        assert isomorphic(g_ttl, g_jsonld)
        assert isomorphic(g_ttl, g_nt)

        # YAML and JCS deserialization
        yaml_content, _ = export_ontology(minimal_ont, export_format="yaml")
        loaded_yaml = yaml.safe_load(yaml_content)
        assert loaded_yaml["domain_namespace"] == "minimal_test"

        json_content, _ = export_ontology(minimal_ont, export_format="json")
        loaded_json = json.loads(json_content)
        assert loaded_json["ontology_version"] == "1.0.0"

    def test_unicode_concept_names_and_definitions_fuzzing(self) -> None:
        """Fuzz export with multi-lingual Unicode strings (CJK, Cyrillic, Arabic, Emoji)."""
        desc = "Multi-lingual ontology: 中文, 日本語, 한국어, Русский, العربية, Deutsch, Emoji 🚀"
        unicode_ont = GraphAOntology(
            ontology_version="1.0.0",
            domain_namespace="unicode_stress",
            enterprise_owner="跨国金融集团 🌍 (International Financial Group)",
            description=desc,
            canonical_business_concepts=[
                CanonicalBusinessConcept(
                    concept_uri="urn:ontology:entity:client_entity",
                    concept_name="客户 (Corporate Client) 🏦",
                    definition="自然人或法人 юридическое лицо — täst 123 \u2603 \U0001f680",
                    synonyms=["Клиент", "顾客", "زبون", "Vertragspartner"],
                    target_hub="hub_client_unicode",
                    attributes=[
                        ConceptAttribute(
                            attribute_name="net_worth_eur",
                            data_type="decimal",
                            description="净资产总额 in Euros (€)",
                        )
                    ],
                )
            ],
            canonical_relationships=[],
            reference_enumerations=[],
        )

        ttl_content, _ = export_ontology(unicode_ont, export_format="turtle")
        jsonld_content, _ = export_ontology(unicode_ont, export_format="jsonld")
        nt_content, _ = export_ontology(unicode_ont, export_format="ntriples")

        # Verify parsing preserves Unicode literals accurately
        g_ttl = Graph().parse(data=ttl_content, format="turtle")
        g_jsonld = Graph().parse(data=jsonld_content, format="json-ld")
        g_nt = Graph().parse(data=nt_content, format="nt")

        assert isomorphic(g_ttl, g_jsonld)
        assert isomorphic(g_ttl, g_nt)

        # Verify JCS JSON preserves UTF-8 without unicode escape regressions
        json_content, _ = export_ontology(unicode_ont, export_format="json")
        assert "客户" in json_content
        assert "自然人或法人" in json_content
        assert "🚀" in json_content

    def test_missing_optional_fields_and_attribute_quote_safety(self) -> None:
        """Verify robustness when optional fields are None and unknown datatypes are passed."""
        special_ont = GraphAOntology(
            ontology_version="1.0.0",
            domain_namespace="special_chars",
            description=None,
            enterprise_owner="Special Enterprise Architecture",
            canonical_business_concepts=[
                CanonicalBusinessConcept(
                    concept_uri="urn:ontology:entity:special_account",
                    concept_name="Special Account",
                    definition="Account with non-standard attribute names.",
                    target_hub=None,
                    parent_concept_uri=None,
                    synonyms=None,
                    attributes=[
                        ConceptAttribute(
                            attribute_name="account_number",
                            # Should fall back gracefully to XSD.string
                            data_type="unknown_custom_type",
                        ),
                        ConceptAttribute(
                            attribute_name="fee_surcharge",
                            data_type="string",
                        ),
                    ],
                    external_mappings=[
                        ExternalMapping(
                            scheme="custom",
                            target_uri="https://example.com/custom#Account",
                            predicate="relatedMatch",  # Case variation test
                            confidence=0.8,
                        )
                    ],
                )
            ],
            canonical_relationships=[],
            reference_enumerations=[],
        )

        ttl_content, _ = export_ontology(special_ont, export_format="turtle")
        g = Graph().parse(data=ttl_content, format="turtle")
        assert len(g) > 5

    def test_export_endpoint_integration_across_formats(self, local_client: TestClient) -> None:
        """Test GET /api/v1/ontology/studio/sessions/{id}/export HTTP endpoint."""
        # Create session
        init_resp = local_client.post(
            "/api/v1/ontology/studio/init",
            json={"domain_namespace": "export_test"},
        )
        token = init_resp.json()["local_token"]
        auth_hdr = {"Authorization": f"Bearer {token}", "X-OpenDV-Role": "admin"}

        create_resp = local_client.post(
            "/api/v1/ontology/studio/sessions",
            json={"session_id": "export-session", "domain_namespace": "export_test"},
            headers=auth_hdr,
        )
        assert create_resp.status_code == 201

        format_to_media_type = {
            "turtle": "text/turtle",
            "ttl": "text/turtle",
            "jsonld": "application/ld+json",
            "json-ld": "application/ld+json",
            "ntriples": "application/n-triples",
            "nt": "application/n-triples",
            "yaml": "application/yaml",
            "json": "application/json",
        }

        for fmt, expected_content_type in format_to_media_type.items():
            resp = local_client.get(
                f"/api/v1/ontology/studio/sessions/export-session/export?export_format={fmt}",
                headers=auth_hdr,
            )
            assert resp.status_code == 200, f"Failed for format {fmt}: {resp.text}"
            assert expected_content_type in resp.headers["content-type"]
            assert len(resp.content) > 0

        # Unsupported format returns 400 UNSUPPORTED_FORMAT
        bad_resp = local_client.get(
            "/api/v1/ontology/studio/sessions/export-session/export?export_format=excel",
            headers=auth_hdr,
        )
        assert bad_resp.status_code == 400
        assert bad_resp.json()["error_code"] == "UNSUPPORTED_FORMAT"
