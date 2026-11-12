"""Comprehensive Integration Tests for Ontology Studio REST API & Job Supervisor (Track 5B).

Covers:
- Task 5B.1 (Issue #215): Session & Source Management REST Endpoints (Routes 1-11)
- Task 5B.2 (Issue #216): Parse, Clarification, Construction & Job REST / SSE (Routes 12-19)
- Task 5B.3 (Issue #217): Deltas, Proposals, Waiver, Multi-Format Export & Promotion (Routes 20-29)
- Task 5B.4 (Issue #218): Dual Server Modes, Launch Nonce, CSRF, and 11-Step Error Precedence
"""

from __future__ import annotations

import io
import json
import time
import zipfile
from pathlib import Path
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from starlette.testclient import TestClient

from opendv_contracts.dtos.ingestion import TableStructure
from opendv_platform_api.main import create_app
from opendv_service import OpenDVSettings
from opendv_service.construction.clarification_harness import EMPTY_ANSWERS_SHA256
from opendv_service.studio.locks import L2_JOB, studio_lock

# Ephemeral RS256 test keypair strictly isolated to integration test suite
_TEST_RSA_PRIVATE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_TEST_RSA_PUBLIC_KEY_PEM = (
    _TEST_RSA_PRIVATE_KEY.public_key()
    .public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    .decode("utf-8")
)


def mint_test_jwt(
    subject: str = "user:alice",
    role: str = "admin",
    expires_in: float = 3600.0,
    issuer: str = "opendv-platform",
    audience: str = "opendv-api",
    headers: dict[str, Any] | None = None,
) -> str:
    now = int(time.time())
    payload = {
        "sub": subject,
        "role": role,
        "iss": issuer,
        "aud": audience,
        "iat": now,
        "exp": int(now + expires_in),
    }
    return jwt.encode(payload, _TEST_RSA_PRIVATE_KEY, algorithm="RS256", headers=headers)


@pytest.fixture(autouse=True)
def reset_studio_environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate each test in a fresh temporary directory."""
    from opendv_core.schema_registry import get_default_schema_registry

    get_default_schema_registry()
    studio_root = tmp_path / "opendv_studio"
    studio_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("OPENDV_STUDIO_ROOT", str(studio_root))
    monkeypatch.setenv("OPENDV_WORKSPACE_ROOT", str(tmp_path / "workspace"))
    monkeypatch.setenv("OPENDV_SERVER_MODE", "local_standalone")
    (tmp_path / "workspace").mkdir(parents=True, exist_ok=True)


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    """Create test client with fresh local OpenDVSettings."""
    settings = OpenDVSettings.load(
        workspace_root=tmp_path / "workspace",
        state_dir=tmp_path / "state",
        is_server=True,
    )
    app = create_app(settings=settings)
    return TestClient(app, client=("127.0.0.1", 50000), base_url="http://localhost:8000")


@pytest.fixture
def auth_headers(client: TestClient) -> dict[str, str]:
    """Initialize local standalone environment and return valid auth headers."""
    resp = client.post(
        "/api/v1/ontology/studio/init",
        json={"domain_namespace": "wealth_mgmt", "enterprise_owner": "Global Wealth"},
    )
    assert resp.status_code == 200, resp.text
    token = resp.json()["local_token"]
    return {
        "Authorization": f"Bearer {token}",
        "X-OpenDV-Role": "admin",
    }


# ===========================================================================
# Task 5B.1: Session & Source Management REST Endpoints (Routes 1-11)
# ===========================================================================


def test_init_endpoint_local_standalone(client: TestClient) -> None:
    """Verify POST /init in local standalone mode."""
    resp = client.post(
        "/api/v1/ontology/studio/init",
        json={"domain_namespace": "wealth_mgmt", "enterprise_owner": "Global Wealth"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "initialized"
    assert "local_token" in data
    assert data["domain_namespace"] == "wealth_mgmt"

    # Re-init without force fails if token doesn't match
    reinit_fail = client.post(
        "/api/v1/ontology/studio/init",
        json={"domain_namespace": "wealth_mgmt", "force": False},
        headers={"Authorization": "Bearer bad-token"},
    )
    assert reinit_fail.status_code == 409


def test_session_lifecycle_crud(client: TestClient, auth_headers: dict[str, str]) -> None:
    """Verify session creation, retrieval, listing, status, manifest, and deletion."""
    # List sessions initially empty
    resp = client.get("/api/v1/ontology/studio/sessions", headers=auth_headers)
    assert resp.status_code == 200
    assert resp.json() == []

    # Create session
    create_resp = client.post(
        "/api/v1/ontology/studio/sessions",
        json={
            "session_id": "test-session-1",
            "domain_namespace": "wealth_mgmt",
            "enterprise_owner": "Global Wealth",
        },
        headers=auth_headers,
    )
    assert create_resp.status_code == 201
    created = create_resp.json()
    assert created["session_id"] == "test-session-1"
    assert created["active_version"] == "0.0.0"

    # Get session detail
    detail_resp = client.get(
        "/api/v1/ontology/studio/sessions/test-session-1",
        headers=auth_headers,
    )
    assert detail_resp.status_code == 200
    assert detail_resp.json()["session_id"] == "test-session-1"

    # Get session status
    status_resp = client.get(
        "/api/v1/ontology/studio/sessions/test-session-1/status",
        headers=auth_headers,
    )
    assert status_resp.status_code == 200
    assert status_resp.json()["session_id"] == "test-session-1"

    # Get manifest
    manifest_resp = client.get(
        "/api/v1/ontology/studio/sessions/test-session-1/manifest",
        headers=auth_headers,
    )
    assert manifest_resp.status_code == 200
    assert manifest_resp.json()["files"] == []

    # Delete session
    del_resp = client.delete(
        "/api/v1/ontology/studio/sessions/test-session-1",
        headers=auth_headers,
    )
    assert del_resp.status_code == 204

    # Verify session is gone
    not_found_resp = client.get(
        "/api/v1/ontology/studio/sessions/test-session-1",
        headers=auth_headers,
    )
    assert not_found_resp.status_code == 404


def test_source_management_and_cas(client: TestClient, auth_headers: dict[str, str]) -> None:
    """Verify source upload to CAS, listing, content retrieval, and deletion."""
    # Create session
    client.post(
        "/api/v1/ontology/studio/sessions",
        json={"session_id": "src-session", "domain_namespace": "wealth_mgmt"},
        headers=auth_headers,
    )

    # Upload source document
    doc_content = b"# Financial Services Policy\n\nAll accounts must satisfy AML requirements."
    files = {
        "file": ("policy.md", io.BytesIO(doc_content), "text/markdown"),
    }
    data = {
        "description": "AML Policy Document",
        "do_not_send_to_agent": "false",
    }
    upload_resp = client.post(
        "/api/v1/ontology/studio/sessions/src-session/sources",
        files=files,
        data=data,
        headers=auth_headers,
    )
    assert upload_resp.status_code == 201
    source_meta = upload_resp.json()
    source_id = source_meta["source_id"]
    assert source_meta["filename"] == "policy.md"
    assert source_meta["byte_count"] == len(doc_content)

    # List sources
    list_resp = client.get(
        "/api/v1/ontology/studio/sessions/src-session/sources",
        headers=auth_headers,
    )
    assert list_resp.status_code == 200
    sources = list_resp.json()
    assert len(sources) == 1
    assert sources[0]["source_id"] == source_id

    # Get single source
    get_resp = client.get(
        f"/api/v1/ontology/studio/sessions/src-session/sources/{source_id}",
        headers=auth_headers,
    )
    assert get_resp.status_code == 200
    assert get_resp.json()["source_id"] == source_id

    # Download source content
    content_resp = client.get(
        f"/api/v1/ontology/studio/sessions/src-session/sources/{source_id}/content",
        headers=auth_headers,
    )
    assert content_resp.status_code == 200
    assert content_resp.content == doc_content

    # Delete source
    del_resp = client.delete(
        f"/api/v1/ontology/studio/sessions/src-session/sources/{source_id}",
        headers=auth_headers,
    )
    assert del_resp.status_code == 204

    # Verify source marked deleted
    get_after_del = client.get(
        f"/api/v1/ontology/studio/sessions/src-session/sources/{source_id}",
        headers=auth_headers,
    )
    assert get_after_del.status_code == 404


# ===========================================================================
# Task 5B.2: Parse, Clarifications, Construction & Jobs / SSE (Routes 12-19)
# ===========================================================================


def test_parse_job_and_events(client: TestClient, auth_headers: dict[str, str]) -> None:
    """Verify asynchronous parse job trigger and job status inspection."""
    client.post(
        "/api/v1/ontology/studio/sessions",
        json={"session_id": "parse-session", "domain_namespace": "wealth_mgmt"},
        headers=auth_headers,
    )

    # Trigger parse
    parse_resp = client.post(
        "/api/v1/ontology/studio/sessions/parse-session/parse",
        json={"ocr_mode": "auto"},
        headers=auth_headers,
    )
    assert parse_resp.status_code == 202
    job_handle = parse_resp.json()
    job_id = job_handle["job_id"]
    assert job_handle["status"] in ("pending", "running", "completed")

    # Inspect job status
    status_resp = client.get(
        f"/api/v1/ontology/studio/jobs/{job_id}",
        headers=auth_headers,
    )
    assert status_resp.status_code == 200
    assert status_resp.json()["job_id"] == job_id


def test_clarifications_and_answers_occ(client: TestClient, auth_headers: dict[str, str]) -> None:
    """Verify clarification flow and strict OCC conflict on answers."""
    client.post(
        "/api/v1/ontology/studio/sessions",
        json={"session_id": "clarify-session", "domain_namespace": "wealth_mgmt"},
        headers=auth_headers,
    )

    # Trigger clarify job
    clarify_resp = client.post(
        "/api/v1/ontology/studio/sessions/clarify-session/clarify",
        json={"max_questions": 5},
        headers=auth_headers,
    )
    assert clarify_resp.status_code == 202

    # Get clarifications list
    get_clarifications = client.get(
        "/api/v1/ontology/studio/sessions/clarify-session/clarifications",
        headers=auth_headers,
    )
    assert get_clarifications.status_code == 200
    clarifications = get_clarifications.json()
    assert isinstance(clarifications, list)

    # Submit answers with wrong expected_answers_sha256 triggers 412 Precondition Failed
    bad_occ_resp = client.post(
        "/api/v1/ontology/studio/sessions/clarify-session/answers",
        json={
            "answers": {"q1": "Selected Answer"},
            "expected_answers_sha256": "bad-hash-" + "0" * 55,
        },
        headers=auth_headers,
    )
    assert bad_occ_resp.status_code == 412
    assert bad_occ_resp.json()["error_code"] == "ONT_EVO_CONCURRENT_MODIFICATION"


def test_construct_job_and_cancel(client: TestClient, auth_headers: dict[str, str]) -> None:
    """Verify construct job submission and cancellation endpoint."""
    client.post(
        "/api/v1/ontology/studio/sessions",
        json={"session_id": "construct-session", "domain_namespace": "wealth_mgmt"},
        headers=auth_headers,
    )

    construct_resp = client.post(
        "/api/v1/ontology/studio/sessions/construct-session/construct",
        json={"auto_commit": False, "mode": "challenger"},
        headers=auth_headers,
    )
    assert construct_resp.status_code == 202
    job_id = construct_resp.json()["job_id"]

    # Cancel job
    cancel_resp = client.post(
        f"/api/v1/ontology/studio/jobs/{job_id}/cancel",
        headers=auth_headers,
    )
    assert cancel_resp.status_code == 200
    assert cancel_resp.json()["status"] == "cancelled"


# ===========================================================================
# Task 5B.3: Deltas, Proposals, Export & Promotion (Routes 20-29)
# ===========================================================================


def test_proposal_lifecycle_and_self_acceptance_rejection(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """Verify proposal submission, self-acceptance rejection (403), and admin rejection."""
    client.post(
        "/api/v1/ontology/studio/sessions",
        json={"session_id": "prop-session", "domain_namespace": "wealth_mgmt"},
        headers=auth_headers,
    )

    # Submit proposal
    prop_resp = client.post(
        "/api/v1/ontology/studio/sessions/prop-session/proposals",
        json={
            "operations": [
                {
                    "op_type": "AddConceptOp",
                    "concept": {
                        "concept_uri": "urn:ontology:entity:instrument",
                        "concept_name": "Financial Instrument",
                        "definition": "A monetary contract between parties.",
                        "target_hub": "hub_instrument",
                    },
                }
            ],
            "rationale": "Add Core Financial Instrument Concept",
        },
        headers=auth_headers,
    )
    assert prop_resp.status_code == 201
    prop = prop_resp.json()
    prop_id = prop["proposal_id"]
    assert prop["status"] == "PENDING"

    # Self-acceptance check: proposer cannot accept their own proposal (403)
    # The header has X-OpenDV-Role: admin, but the user is the same proposer
    # Let's verify self-acceptance check logic
    self_accept_resp = client.post(
        f"/api/v1/ontology/studio/sessions/prop-session/proposals/{prop_id}/accept",
        json={},
        headers=auth_headers,
    )
    # Proposer in headers is "local_user", matching proposal author
    assert self_accept_resp.status_code == 403
    assert self_accept_resp.json()["error_code"] in (
        "FORBIDDEN_SELF_ACCEPTANCE",
        "PROPOSAL_SELF_ACCEPTANCE_FORBIDDEN",
    )

    # Reject proposal
    reject_resp = client.post(
        f"/api/v1/ontology/studio/sessions/prop-session/proposals/{prop_id}/reject",
        json={"reason": "Instrument schema needs additional review by governance steward."},
        headers=auth_headers,
    )
    assert reject_resp.status_code == 200
    assert reject_resp.json()["status"] == "REJECTED"


def test_multi_format_export_endpoint(client: TestClient, auth_headers: dict[str, str]) -> None:
    """Verify GET /export for YAML, JSON, Turtle, JSON-LD, NTriples."""
    client.post(
        "/api/v1/ontology/studio/sessions",
        json={"session_id": "export-session", "domain_namespace": "wealth_mgmt"},
        headers=auth_headers,
    )

    for fmt, expected_mime in [
        ("yaml", "application/yaml"),
        ("json", "application/json"),
        ("turtle", "text/turtle"),
        ("jsonld", "application/ld+json"),
        ("ntriples", "application/n-triples"),
    ]:
        resp = client.get(
            f"/api/v1/ontology/studio/sessions/export-session/export?export_format={fmt}",
            headers=auth_headers,
        )
        assert resp.status_code == 200, f"Failed for format {fmt}: {resp.text}"
        assert expected_mime in resp.headers["content-type"]
        assert len(resp.content) > 0


def test_promote_session_with_occ(client: TestClient, auth_headers: dict[str, str]) -> None:
    """Verify two-phase atomic promotion of session head into target workspace contract."""
    create_resp = client.post(
        "/api/v1/ontology/studio/sessions",
        json={"session_id": "promo-session", "domain_namespace": "wealth_mgmt"},
        headers=auth_headers,
    )
    genesis_sha = create_resp.json()["head_sha256"]

    # Promoting empty greenfield GenesisDraft (v0.0.0) fails with 422 ONTOLOGY_REQUIRES_CONCEPT
    empty_promo = client.post(
        "/api/v1/ontology/studio/sessions/promo-session/promote",
        json={
            "head_sha256": genesis_sha,
            "workspace_sha256": "0" * 64,
            "rationale": "Attempted promotion of empty genesis ontology.",
        },
        headers={
            **auth_headers,
            "If-Match": f'"{genesis_sha}"',
            "X-OpenDV-Workspace-If-Match": f'"{"0" * 64}"',
        },
    )
    assert empty_promo.status_code == 422
    assert empty_promo.json()["error_code"] == "ONTOLOGY_REQUIRES_CONCEPT"

    # Commit v1.0.0 with a canonical business concept
    commit1 = client.post(
        "/api/v1/ontology/studio/sessions/promo-session/deltas/apply",
        json={
            "delta_id": "d-promo-1",
            "parent_sha256": "",
            "base_version": "0.0.0",
            "target_version": "1.0.0",
            "operations": [
                {
                    "op_type": "add_concept",
                    "op_id": "op-p1",
                    "concept_uri": "urn:ontology:entity:portfolio",
                    "concept_name": "Portfolio",
                    "definition": "Managed investment portfolio.",
                }
            ],
            "low_confidence_waiver": True,
            "grounding_rationale": "Approved portfolio concept for promotion test.",
        },
        headers={**auth_headers, "If-Match": f'"{genesis_sha}"'},
    )
    assert commit1.status_code == 200, commit1.text
    head_sha256 = commit1.json()["target_sha256"]

    # Promote v1.0.0 with valid If-Match
    promote_resp = client.post(
        "/api/v1/ontology/studio/sessions/promo-session/promote",
        json={
            "head_sha256": head_sha256,
            "workspace_sha256": "0" * 64,
            "rationale": "Initial production release of Wealth Management ontology.",
        },
        headers={
            **auth_headers,
            "If-Match": f'"{head_sha256}"',
            "X-OpenDV-Workspace-If-Match": f'"{"0" * 64}"',
        },
    )
    assert promote_resp.status_code == 200, promote_resp.text
    promo_data = promote_resp.json()
    assert promo_data["promoted_version"] == "1.0.0"
    ws_sha = promo_data["workspace_sha256"]

    # Re-promoting identical v1.0.0 head returns 400 NO_OP_DELTA
    noop_promo = client.post(
        "/api/v1/ontology/studio/sessions/promo-session/promote",
        json={
            "head_sha256": head_sha256,
            "workspace_sha256": ws_sha,
            "rationale": "Duplicate promotion check for Wealth Management ontology.",
        },
        headers={
            **auth_headers,
            "If-Match": f'"{head_sha256}"',
            "X-OpenDV-Workspace-If-Match": f'"{ws_sha}"',
        },
    )
    assert noop_promo.status_code == 400
    assert noop_promo.json()["error_code"] == "NO_OP_DELTA"

    # Commit v1.1.0 and promote sequentially with new workspace_sha256
    commit2 = client.post(
        "/api/v1/ontology/studio/sessions/promo-session/deltas/apply",
        json={
            "delta_id": "d-promo-2",
            "parent_sha256": head_sha256,
            "base_version": "1.0.0",
            "target_version": "1.1.0",
            "operations": [
                {
                    "op_type": "add_concept",
                    "op_id": "op-p2",
                    "concept_uri": "urn:ontology:entity:account",
                    "concept_name": "Account",
                    "definition": "Client investment account.",
                }
            ],
            "low_confidence_waiver": True,
            "grounding_rationale": "Approved account concept for v1.1.0 promotion test.",
        },
        headers={**auth_headers, "If-Match": f'"{head_sha256}"'},
    )
    assert commit2.status_code == 200, commit2.text
    head_1_1_sha = commit2.json()["target_sha256"]

    promote2 = client.post(
        "/api/v1/ontology/studio/sessions/promo-session/promote",
        json={
            "head_sha256": head_1_1_sha,
            "workspace_sha256": ws_sha,
            "rationale": "Second release verification of Wealth Management ontology.",
        },
        headers={
            **auth_headers,
            "If-Match": f'"{head_1_1_sha}"',
            "X-OpenDV-Workspace-If-Match": f'"{ws_sha}"',
        },
    )
    assert promote2.status_code == 200, promote2.text
    assert promote2.json()["promoted_version"] == "1.1.0"

    # Verify deleting a promoted session is rejected with 409 CANNOT_DELETE_PROMOTED_SESSION
    del_resp = client.delete(
        "/api/v1/ontology/studio/sessions/promo-session",
        headers=auth_headers,
    )
    assert del_resp.status_code == 409
    assert del_resp.json()["error_code"] == "CANNOT_DELETE_PROMOTED_SESSION"


def test_bundle_export_and_confidential_guard(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """Verify bundle export and rejection when confidential sources unacknowledged."""
    client.post(
        "/api/v1/ontology/studio/sessions",
        json={"session_id": "bundle-session", "domain_namespace": "wealth_mgmt"},
        headers=auth_headers,
    )

    # Upload confidential source
    files = {"file": ("secret.txt", io.BytesIO(b"Confidential data"), "text/plain")}
    data = {"do_not_send_to_agent": "true"}
    client.post(
        "/api/v1/ontology/studio/sessions/bundle-session/sources",
        files=files,
        data=data,
        headers=auth_headers,
    )

    # Export without acknowledgment fails with 422
    fail_bundle = client.post(
        "/api/v1/ontology/studio/sessions/bundle-session/bundle",
        json={"acknowledge_confidential_source_export": False},
        headers=auth_headers,
    )
    assert fail_bundle.status_code == 422
    assert fail_bundle.json()["error_code"] == "CONFIDENTIAL_SOURCE_EXPORT_UNACKNOWLEDGED"

    # Export with acknowledgment but missing rationale fails with 400 EXPORT_RATIONALE_REQUIRED
    missing_rat_bundle = client.post(
        "/api/v1/ontology/studio/sessions/bundle-session/bundle",
        json={"acknowledge_confidential_source_export": True},
        headers=auth_headers,
    )
    assert missing_rat_bundle.status_code == 400
    assert missing_rat_bundle.json()["error_code"] == "EXPORT_RATIONALE_REQUIRED"

    # Export with acknowledgment and rationale succeeds
    ok_bundle = client.post(
        "/api/v1/ontology/studio/sessions/bundle-session/bundle",
        json={
            "acknowledge_confidential_source_export": True,
            "export_rationale": "Approved confidential archive export for audit review.",
        },
        headers=auth_headers,
    )
    assert ok_bundle.status_code == 200
    assert ok_bundle.headers["content-type"] == "application/zip"


# ===========================================================================
# Task 5B.4: Dual Server Modes, Launch Nonce, CSRF & Error Precedence (Issue #218)
# ===========================================================================


def test_remote_platform_mode_blocks_init(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify /init returns 403 in remote_platform server mode."""
    monkeypatch.setenv("OPENDV_SERVER_MODE", "remote_platform")
    resp = client.post(
        "/api/v1/ontology/studio/init",
        json={"domain_namespace": "wealth_mgmt"},
    )
    assert resp.status_code == 403
    assert resp.json()["error_code"] == "LOOPBACK_PRIVILEGE_DISABLED_IN_REMOTE_MODE"


def test_launch_nonce_lifecycle_and_replay_rejection(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """Verify launch nonce creation, token exchange, and single-use replay rejection."""
    # Calling /auth/launch-nonce without auth fails with 401
    unauth_resp = client.post("/api/v1/ontology/studio/auth/launch-nonce")
    assert unauth_resp.status_code == 401
    assert unauth_resp.json()["error_code"] == "UNAUTHENTICATED"

    # Create launch nonce with valid auth
    nonce_resp = client.post("/api/v1/ontology/studio/auth/launch-nonce", headers=auth_headers)
    assert nonce_resp.status_code == 200
    nonce = nonce_resp.json()["launch_nonce"]
    assert nonce_resp.json()["nonce"] == nonce

    # Exchange nonce for HttpOnly session cookie
    token_resp = client.post(
        "/api/v1/ontology/studio/auth/token",
        json={"launch_nonce": nonce},
    )
    assert token_resp.status_code == 200
    assert "opendv_session" in token_resp.cookies

    # Replay of used launch nonce fails with 401
    replay_resp = client.post(
        "/api/v1/ontology/studio/auth/token",
        json={"launch_nonce": nonce},
    )
    assert replay_resp.status_code == 401
    assert replay_resp.json()["error_code"] == "UNAUTHENTICATED"


def test_remote_platform_rs256_jwt_authentication(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify genuine RS256 JWT validation, algorithm enforcement, and expiration in remote mode."""
    monkeypatch.setenv("OPENDV_SERVER_MODE", "remote_platform")

    # 1. Missing public key in remote mode fails with 500 AUTHENTICATION_CONFIG_ERROR
    resp_unconfigured = client.get(
        "/api/v1/ontology/studio/sessions",
        headers={"Authorization": "Bearer any.token.here"},
    )
    assert resp_unconfigured.status_code == 500
    assert resp_unconfigured.json()["error_code"] == "AUTHENTICATION_CONFIG_ERROR"

    # Configure public key and claims
    monkeypatch.setenv("OPENDV_JWT_PUBLIC_KEY", _TEST_RSA_PUBLIC_KEY_PEM)
    monkeypatch.setenv("OPENDV_JWT_ISSUER", "opendv-platform")
    monkeypatch.setenv("OPENDV_JWT_AUDIENCE", "opendv-api")

    # 2. Valid RS256 JWT succeeds
    valid_jwt = mint_test_jwt(subject="user:alice", role="admin", expires_in=3600)
    resp_valid = client.get(
        "/api/v1/ontology/studio/sessions",
        headers={"Authorization": f"Bearer {valid_jwt}"},
    )
    assert resp_valid.status_code == 200

    # 3. Forged / invalid JWT fails with 401 UNAUTHENTICATED
    resp_forged = client.get(
        "/api/v1/ontology/studio/sessions",
        headers={"Authorization": "Bearer not.a.valid.jwt.token"},
    )
    assert resp_forged.status_code == 401
    assert resp_forged.json()["error_code"] == "UNAUTHENTICATED"

    # 4. Wrong issuer fails with 401 UNAUTHENTICATED
    wrong_iss_jwt = mint_test_jwt(issuer="untrusted-issuer")
    resp_iss = client.get(
        "/api/v1/ontology/studio/sessions",
        headers={"Authorization": f"Bearer {wrong_iss_jwt}"},
    )
    assert resp_iss.status_code == 401
    assert resp_iss.json()["error_code"] == "UNAUTHENTICATED"

    # 5. Wrong audience fails with 401 UNAUTHENTICATED
    wrong_aud_jwt = mint_test_jwt(audience="wrong-api")
    resp_aud = client.get(
        "/api/v1/ontology/studio/sessions",
        headers={"Authorization": f"Bearer {wrong_aud_jwt}"},
    )
    assert resp_aud.status_code == 401
    assert resp_aud.json()["error_code"] == "UNAUTHENTICATED"

    # 6. Algorithm-confusion attack: HS256 token rejected with 401 UNAUTHENTICATED
    hs256_token = jwt.encode(
        {
            "sub": "user:mallory",
            "role": "admin",
            "iss": "opendv-platform",
            "aud": "opendv-api",
            "iat": int(time.time()),
            "exp": int(time.time() + 3600),
        },
        "symmetric-secret-key-32-bytes-!!",
        algorithm="HS256",
    )
    resp_hs256 = client.get(
        "/api/v1/ontology/studio/sessions",
        headers={"Authorization": f"Bearer {hs256_token}"},
    )
    assert resp_hs256.status_code == 401
    assert resp_hs256.json()["error_code"] == "UNAUTHENTICATED"

    # 7. Expired RS256 JWT fails with 401 UNAUTHENTICATED
    expired_jwt = mint_test_jwt(subject="user:alice", role="admin", expires_in=-60)
    resp_expired = client.get(
        "/api/v1/ontology/studio/sessions",
        headers={"Authorization": f"Bearer {expired_jwt}"},
    )
    assert resp_expired.status_code == 401
    assert resp_expired.json()["error_code"] == "UNAUTHENTICATED"

    # 8. Remote mode rejects loopback endpoints /auth/launch-nonce and /auth/token with 403
    resp_nonce = client.post(
        "/api/v1/ontology/studio/auth/launch-nonce",
        headers={"Authorization": f"Bearer {valid_jwt}"},
    )
    assert resp_nonce.status_code == 403
    assert resp_nonce.json()["error_code"] == "LOOPBACK_PRIVILEGE_DISABLED_IN_REMOTE_MODE"

    resp_token = client.post(
        "/api/v1/ontology/studio/auth/token",
        json={"nonce": "dummy-nonce"},
    )
    assert resp_token.status_code == 403
    assert resp_token.json()["error_code"] == "LOOPBACK_PRIVILEGE_DISABLED_IN_REMOTE_MODE"


def test_csrf_cookie_validation(client: TestClient, auth_headers: dict[str, str]) -> None:
    """Verify state-modifying requests with cookie require CSRF header and valid Origin."""
    # Mint session cookie via valid nonce lifecycle
    nonce_resp = client.post("/api/v1/ontology/studio/auth/launch-nonce", headers=auth_headers)
    assert nonce_resp.status_code == 200
    nonce = nonce_resp.json()["launch_nonce"]
    token_resp = client.post(
        "/api/v1/ontology/studio/auth/token",
        json={"launch_nonce": nonce},
    )
    assert token_resp.status_code == 200
    session_cookie = token_resp.cookies.get("opendv_session")
    assert session_cookie is not None

    cookie_client = TestClient(
        client.app, client=("127.0.0.1", 50000), base_url="http://localhost:8000"
    )
    cookie_client.cookies.set("opendv_session", session_cookie)

    # 1. Mutating request with cookie but missing X-OpenDV-Workbench header fails with 403
    blocked_resp = cookie_client.post(
        "/api/v1/ontology/studio/sessions",
        json={"session_id": "csrf-no-header", "domain_namespace": "wealth_mgmt"},
    )
    assert blocked_resp.status_code == 403
    assert blocked_resp.json()["error_code"] == "CSRF_ORIGIN_MISMATCH"

    # 2. Mutating request with cookie, X-OpenDV-Workbench: 1, but Origin: null fails with 403
    null_origin_resp = cookie_client.post(
        "/api/v1/ontology/studio/sessions",
        json={"session_id": "csrf-null-origin", "domain_namespace": "wealth_mgmt"},
        headers={
            "X-OpenDV-Workbench": "1",
            "Origin": "null",
        },
    )
    assert null_origin_resp.status_code == 403
    assert null_origin_resp.json()["error_code"] == "CSRF_ORIGIN_MISMATCH"

    # 3. Mutating request with cookie, X-OpenDV-Workbench: 1, but foreign Origin fails with 403
    foreign_origin_resp = cookie_client.post(
        "/api/v1/ontology/studio/sessions",
        json={"session_id": "csrf-foreign-origin", "domain_namespace": "wealth_mgmt"},
        headers={
            "X-OpenDV-Workbench": "1",
            "Origin": "http://evil-attacker.com",
            "Host": "localhost:8000",
        },
    )
    assert foreign_origin_resp.status_code == 403
    assert foreign_origin_resp.json()["error_code"] == "CSRF_ORIGIN_MISMATCH"

    # 4. Mutating request with cookie, X-OpenDV-Workbench: 1, and matching loopback Origin passes
    allowed_resp = cookie_client.post(
        "/api/v1/ontology/studio/sessions",
        json={"session_id": "csrf-allowed", "domain_namespace": "wealth_mgmt"},
        headers={
            "X-OpenDV-Workbench": "1",
            "Origin": "http://localhost:8000",
            "Host": "localhost:8000",
        },
    )
    assert allowed_resp.status_code == 201


def test_token_rotation_and_lifecycle(
    client: TestClient, auth_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify local token rotation rules, existing token validation, and OPENDV_TOKEN guard."""
    # 1. Calling /init without force when already initialized fails with 409
    resp_dup = client.post(
        "/api/v1/ontology/studio/init",
        json={"domain_namespace": "wealth_mgmt"},
    )
    assert resp_dup.status_code == 409
    assert resp_dup.json()["error_code"] == "STUDIO_ALREADY_INITIALIZED"

    # 2. Calling /init with force=True but invalid/missing Bearer token fails with 401
    resp_unauth = client.post(
        "/api/v1/ontology/studio/init",
        json={"domain_namespace": "wealth_mgmt", "force": True},
        headers={"Authorization": "Bearer wrong-token"},
    )
    assert resp_unauth.status_code == 401
    assert resp_unauth.json()["error_code"] == "UNAUTHENTICATED"

    # 3. Calling /init with force=True and valid existing token rotates successfully
    resp_rotated = client.post(
        "/api/v1/ontology/studio/init",
        json={"domain_namespace": "wealth_mgmt", "force": True},
        headers=auth_headers,
    )
    assert resp_rotated.status_code == 200
    new_token = resp_rotated.json()["local_token"]
    assert new_token != auth_headers["Authorization"].split()[1]

    # 4. When OPENDV_TOKEN is statically configured, initialization and rotation return 500
    monkeypatch.setenv("OPENDV_TOKEN", "statically-pinned-token")
    resp_static = client.post(
        "/api/v1/ontology/studio/init",
        json={"domain_namespace": "wealth_mgmt", "force": True},
        headers={"Authorization": f"Bearer {new_token}"},
    )
    assert resp_static.status_code == 500
    assert resp_static.json()["error_code"] == "STATIC_TOKEN_CONFIGURED"


def test_role_header_validation(client: TestClient, auth_headers: dict[str, str]) -> None:
    """Verify X-OpenDV-Role validation rejects illegal values with 400."""
    resp_invalid_role = client.get(
        "/api/v1/ontology/studio/sessions",
        headers={
            **auth_headers,
            "X-OpenDV-Role": "superuser",  # Illegal role
        },
    )
    assert resp_invalid_role.status_code == 400
    assert resp_invalid_role.json()["error_code"] == "SYNTAX_INVALID_ARGUMENT"

    resp_valid_role = client.get(
        "/api/v1/ontology/studio/sessions",
        headers={
            **auth_headers,
            "X-OpenDV-Role": "viewer",
        },
    )
    assert resp_valid_role.status_code == 200


def test_non_loopback_rejection(client: TestClient, auth_headers: dict[str, str]) -> None:
    """Verify that non-loopback clients are rejected from bootstrap endpoints."""
    external_client = TestClient(
        client.app,
        client=("198.51.100.1", 50000),
        base_url="http://external-host:8000",
    )

    resp_init = external_client.post(
        "/api/v1/ontology/studio/init",
        json={"domain_namespace": "wealth_mgmt"},
    )
    assert resp_init.status_code == 403
    assert resp_init.json()["error_code"] == "LOOPBACK_PRIVILEGE_DISABLED_IN_REMOTE_MODE"

    resp_nonce = external_client.post(
        "/api/v1/ontology/studio/auth/launch-nonce",
        headers=auth_headers,
    )
    assert resp_nonce.status_code == 403
    assert resp_nonce.json()["error_code"] == "LOOPBACK_PRIVILEGE_DISABLED_IN_REMOTE_MODE"

    resp_token = external_client.post(
        "/api/v1/ontology/studio/auth/token",
        json={"nonce": "dummy-nonce"},
    )
    assert resp_token.status_code == 403
    assert resp_token.json()["error_code"] == "LOOPBACK_PRIVILEGE_DISABLED_IN_REMOTE_MODE"


def test_canonical_error_precedence_order(client: TestClient, auth_headers: dict[str, str]) -> None:
    """Verify 11-step canonical error precedence order."""
    # Step 0 / Step 4: Invalid syntax returns 400 before checking auth/state
    resp_invalid = client.post(
        "/api/v1/ontology/studio/sessions",
        content="not valid json",
        headers={"Content-Type": "application/json"},
    )
    assert resp_invalid.status_code in (400, 422)

    # Step 1: Missing auth returns 401 UNAUTHENTICATED
    resp_unauth = client.get("/api/v1/ontology/studio/sessions/test-session")
    assert resp_unauth.status_code == 401
    assert resp_unauth.json()["error_code"] == "UNAUTHENTICATED"

    # Step 3: Non-admin calling admin endpoint returns 403
    client.post(
        "/api/v1/ontology/studio/sessions",
        json={"session_id": "role-session", "domain_namespace": "wealth_mgmt"},
        headers=auth_headers,
    )
    editor_headers = {
        **auth_headers,
        "X-OpenDV-Role": "editor",  # Not admin
    }
    resp_forbidden = client.delete(
        "/api/v1/ontology/studio/sessions/role-session",
        headers=editor_headers,
    )
    assert resp_forbidden.status_code == 403
    assert resp_forbidden.json()["error_code"] == "FORBIDDEN_ADMIN_REQUIRED"

    # Step 5: Resource not found returns 404
    resp_notfound = client.get(
        "/api/v1/ontology/studio/sessions/nonexistent-session",
        headers=auth_headers,
    )
    assert resp_notfound.status_code == 404
    assert resp_notfound.json()["error_code"] == "SESSION_NOT_FOUND"


# ===========================================================================
# Track 5B Remediation: End-to-End Integration Tests
# ===========================================================================


def test_clarification_answers_successful_submission_and_read(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """Test successful clarification answer submission with dumps_jcs_bytes and subsequent read."""
    session_id = "s-answers-success"
    client.post(
        "/api/v1/ontology/studio/sessions",
        json={"session_id": session_id, "domain_namespace": "wealth_mgmt"},
        headers=auth_headers,
    )

    # 1. Submit initial answer
    resp_submit = client.post(
        f"/api/v1/ontology/studio/sessions/{session_id}/answers",
        json={
            "answers": [
                {
                    "question_id": "q1",
                    "selected_options": ["option_a"],
                    "user_text": "Option A chosen",
                }
            ],
            "expected_answers_sha256": EMPTY_ANSWERS_SHA256,
        },
        headers=auth_headers,
    )
    assert resp_submit.status_code == 200, resp_submit.text
    submit_body = resp_submit.json()
    assert submit_body["saved_count"] == 1
    new_sha = submit_body["answers_sha256"]
    assert new_sha and new_sha != EMPTY_ANSWERS_SHA256

    # 2. Read back answers
    resp_get = client.get(
        f"/api/v1/ontology/studio/sessions/{session_id}/answers",
        headers=auth_headers,
    )
    assert resp_get.status_code == 200
    answers_list = resp_get.json()
    assert len(answers_list) == 1
    assert answers_list[0]["question_id"] == "q1"
    assert answers_list[0]["selected_options"] == ["option_a"]

    # 3. Submit second answer referencing new_sha OCC
    resp_submit2 = client.post(
        f"/api/v1/ontology/studio/sessions/{session_id}/answers",
        json={
            "answers": [
                {
                    "question_id": "q2",
                    "selected_options": ["option_b"],
                    "user_text": "Option B chosen",
                }
            ],
            "expected_answers_sha256": new_sha,
        },
        headers=auth_headers,
    )
    assert resp_submit2.status_code == 200
    assert resp_submit2.json()["saved_count"] == 1


def test_construct_job_with_auto_commit_success(
    client: TestClient, auth_headers: dict[str, str], tmp_path: Path
) -> None:
    """Test successful construction job with auto_commit=True asserting version bump."""
    session_id = "s-construct-autocommit"
    client.post(
        "/api/v1/ontology/studio/sessions",
        json={"session_id": session_id, "domain_namespace": "wealth_mgmt"},
        headers=auth_headers,
    )

    # In deterministic mode, provide an evidence table so construction generates AddConceptOp
    sdir = tmp_path / "opendv_studio" / session_id
    evidence_dir = sdir / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    table = TableStructure(
        table_id="tbl_portfolio_01",
        caption="Investment Portfolios",
        headers=["portfolio_id", "portfolio_name", "risk_profile"],
        rows=[["P01", "Global Growth", "High"]],
    )
    (evidence_dir / "tables.jsonl").write_text(table.model_dump_json() + "\n", encoding="utf-8")

    sess_meta = client.get(
        f"/api/v1/ontology/studio/sessions/{session_id}", headers=auth_headers
    ).json()
    head_sha = sess_meta["head_sha256"]

    # Trigger construct with auto_commit=True
    resp = client.post(
        f"/api/v1/ontology/studio/sessions/{session_id}/construct",
        json={
            "mode": "deterministic",
            "auto_commit": True,
            "fibo_modules": ["FND"],
        },
        headers={**auth_headers, "If-Match": f'"{head_sha}"'},
    )
    assert resp.status_code == 202
    job_handle = resp.json()
    job_id = job_handle["job_id"]

    # Poll until job finishes
    status_data: dict[str, Any] = {}
    for _ in range(50):
        poll = client.get(
            f"/api/v1/ontology/studio/sessions/{session_id}/jobs/{job_id}",
            headers=auth_headers,
        )
        assert poll.status_code == 200
        status_data = poll.json()
        if status_data.get("status") in ("completed", "failed"):
            break
        time.sleep(0.1)

    assert status_data.get("status") == "completed", status_data
    result = status_data.get("result", {})
    assert result.get("auto_committed") is True
    assert result.get("target_version") in ("v1.0.0", "1.0.0")

    # Verify session active version bumped
    sess_resp = client.get(
        f"/api/v1/ontology/studio/sessions/{session_id}",
        headers=auth_headers,
    )
    assert sess_resp.status_code == 200
    assert sess_resp.json()["active_version"] in ("v1.0.0", "1.0.0")


def test_direct_delta_application_endpoint(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """Test successful direct delta application (POST /sessions/{id}/deltas/apply)."""
    session_id = "s-delta-apply-test"
    client.post(
        "/api/v1/ontology/studio/sessions",
        json={"session_id": session_id, "domain_namespace": "wealth_mgmt"},
        headers=auth_headers,
    )

    sess_meta = client.get(
        f"/api/v1/ontology/studio/sessions/{session_id}", headers=auth_headers
    ).json()
    head_sha = sess_meta["head_sha256"]

    resp = client.post(
        f"/api/v1/ontology/studio/sessions/{session_id}/deltas/apply",
        json={
            "delta_id": "d-genesis-direct-01",
            "parent_sha256": "",
            "base_version": "0.0.0",
            "target_version": "1.0.0",
            "operations": [
                {
                    "op_type": "add_concept",
                    "op_id": "op-add-01",
                    "concept_uri": "urn:ontology:entity:portfolio",
                    "concept_name": "Portfolio",
                    "definition": "An investment portfolio.",
                }
            ],
            "rationale": "Direct genesis delta application",
            "low_confidence_waiver": True,
            "grounding_rationale": "Approved portfolio concept for direct delta test.",
        },
        headers={
            **auth_headers,
            "If-Match": f'"{head_sha}"',
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["session_id"] == session_id
    assert body["target_version"] in ("1.0.0", "v1.0.0")
    assert body["applied_operations_count"] == 1
    assert body["delta_id"] == "d-genesis-direct-01"


def test_proposal_acceptance_governance_and_security(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """Test proposal acceptance governance: agent rejected, waiver rejected, admin accepted."""
    session_id = "s-prop-gov"
    client.post(
        "/api/v1/ontology/studio/sessions",
        json={"session_id": session_id, "domain_namespace": "wealth_mgmt"},
        headers=auth_headers,
    )

    # 1. Create a proposal
    prop_resp = client.post(
        f"/api/v1/ontology/studio/sessions/{session_id}/proposals",
        json={
            "proposal_id": "prop-gov-01",
            "operations": [
                {
                    "op_type": "add_concept",
                    "op_id": "op-01",
                    "concept_uri": "urn:ontology:entity:holding",
                    "concept_name": "Holding",
                    "definition": "A financial holding.",
                }
            ],
            "rationale": "Holding concept proposal",
        },
        headers=auth_headers,
    )
    assert prop_resp.status_code == 201
    prop_id = prop_resp.json()["proposal_id"]

    sess_meta = client.get(
        f"/api/v1/ontology/studio/sessions/{session_id}", headers=auth_headers
    ).json()
    head_sha = sess_meta["head_sha256"]

    # 2. Automated agent identity is strictly rejected (403 AGENT_PROPOSAL_ACCEPT_FORBIDDEN)
    resp_agent = client.post(
        f"/api/v1/ontology/studio/sessions/{session_id}/proposals/{prop_id}/accept",
        json={"base_sha256": head_sha},
        headers={
            **auth_headers,
            "X-OpenDV-Principal": "agent:modeler_1",
            "If-Match": f'"{head_sha}"',
        },
    )
    assert resp_agent.status_code == 403
    assert resp_agent.json()["error_code"] == "AGENT_PROPOSAL_ACCEPT_FORBIDDEN"

    # 3. Non-admin editor requesting override or waiver is rejected (403 FORBIDDEN_ADMIN_REQUIRED)
    resp_override = client.post(
        f"/api/v1/ontology/studio/sessions/{session_id}/proposals/{prop_id}/accept",
        json={"base_sha256": head_sha, "self_acceptance_override": True},
        headers={
            **auth_headers,
            "X-OpenDV-Role": "editor",
            "X-OpenDV-Principal": "user:bob",
            "If-Match": f'"{head_sha}"',
        },
    )
    assert resp_override.status_code == 403
    assert resp_override.json()["error_code"] == "FORBIDDEN_ADMIN_REQUIRED"

    resp_waiver = client.post(
        f"/api/v1/ontology/studio/sessions/{session_id}/proposals/{prop_id}/accept",
        json={
            "base_sha256": head_sha,
            "low_confidence_waiver": True,
            "grounding_rationale": "Administrative grounding waiver for holding",
        },
        headers={
            **auth_headers,
            "X-OpenDV-Role": "editor",
            "X-OpenDV-Principal": "user:bob",
            "If-Match": f'"{head_sha}"',
        },
    )
    assert resp_waiver.status_code == 403
    assert resp_waiver.json()["error_code"] == "FORBIDDEN_ADMIN_REQUIRED"

    # 4. Admin successfully accepts the proposal
    resp_accept = client.post(
        f"/api/v1/ontology/studio/sessions/{session_id}/proposals/{prop_id}/accept",
        json={
            "base_sha256": head_sha,
            "self_acceptance_override": True,
            "low_confidence_waiver": True,
            "grounding_rationale": "Approved holding concept for governance test.",
        },
        headers={
            **auth_headers,
            "X-OpenDV-Role": "admin",
            "X-OpenDV-Principal": "user:admin",
            "If-Match": f'"{head_sha}"',
        },
    )
    assert resp_accept.status_code == 200, resp_accept.text
    accept_body = resp_accept.json()
    assert accept_body["session_id"] == session_id
    assert accept_body["target_version"] in ("1.0.0", "v1.0.0")
    assert accept_body["applied_operations_count"] == 1


def test_proposal_rebase_already_resolved_rejection(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """Test proposal rebase rejection (409 PROPOSAL_ALREADY_RESOLVED) when already ACCEPTED."""
    session_id = "s-rebase-resolved"
    client.post(
        "/api/v1/ontology/studio/sessions",
        json={"session_id": session_id, "domain_namespace": "wealth_mgmt"},
        headers=auth_headers,
    )

    # 1. Create a proposal
    prop_resp = client.post(
        f"/api/v1/ontology/studio/sessions/{session_id}/proposals",
        json={
            "proposal_id": "prop-rebase-01",
            "operations": [
                {
                    "op_type": "add_concept",
                    "op_id": "op-01",
                    "concept_uri": "urn:ontology:entity:asset",
                    "concept_name": "Asset",
                    "definition": "An economic asset.",
                }
            ],
            "rationale": "Asset proposal",
        },
        headers=auth_headers,
    )
    assert prop_resp.status_code == 201
    prop_id = prop_resp.json()["proposal_id"]

    sess_meta = client.get(
        f"/api/v1/ontology/studio/sessions/{session_id}", headers=auth_headers
    ).json()
    head_sha = sess_meta["head_sha256"]

    # 2. Accept the proposal
    accept_resp = client.post(
        f"/api/v1/ontology/studio/sessions/{session_id}/proposals/{prop_id}/accept",
        json={
            "base_sha256": head_sha,
            "self_acceptance_override": True,
            "low_confidence_waiver": True,
            "grounding_rationale": "Approved asset concept for rebase test.",
        },
        headers={**auth_headers, "If-Match": f'"{head_sha}"'},
    )
    assert accept_resp.status_code == 200

    # 3. Attempt to rebase the already ACCEPTED proposal -> 409 PROPOSAL_ALREADY_RESOLVED
    new_head_sha = accept_resp.json()["target_sha256"]
    rebase_resp = client.post(
        f"/api/v1/ontology/studio/sessions/{session_id}/proposals/{prop_id}/rebase",
        json={"base_sha256": new_head_sha},
        headers={**auth_headers, "If-Match": f'"{new_head_sha}"'},
    )
    assert rebase_resp.status_code == 409
    assert rebase_resp.json()["error_code"] == "PROPOSAL_ALREADY_RESOLVED"


def test_dns_rebinding_rejected_on_init(client: TestClient) -> None:
    """Test that /init rejects requests with non-loopback Host header (DNS rebinding guard)."""
    resp = client.post(
        "/api/v1/ontology/studio/init",
        headers={"Host": "attacker.example"},
    )
    assert resp.status_code == 403
    assert resp.json()["error_code"] == "LOOPBACK_PRIVILEGE_DISABLED_IN_REMOTE_MODE"


def test_cross_origin_loopback_csrf_rejected(client: TestClient) -> None:
    """Test that requests with non-loopback Origin to loopback Host are rejected."""
    resp = client.post(
        "/api/v1/ontology/studio/init",
        headers={
            "Host": "localhost",
            "Origin": "http://evil-attacker.com",
        },
    )
    assert resp.status_code == 403


def test_auto_commit_without_precondition_rejected(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """Test that auto-commit construction without an OCC precondition is rejected."""
    session_id = "s-noprec"
    client.post(
        "/api/v1/ontology/studio/sessions",
        json={"session_id": session_id, "domain_namespace": "wealth_mgmt"},
        headers=auth_headers,
    )
    resp = client.post(
        f"/api/v1/ontology/studio/sessions/{session_id}/construct",
        json={"auto_commit": True},
        headers=auth_headers,
    )
    assert resp.status_code in (400, 428)
    assert resp.json()["error_code"] == "MISSING_AUTO_COMMIT_PRECONDITION"


def test_cancel_vs_commit_lock_concurrency(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """Test cancelling a job in committing stage raises 409 JOB_CANNOT_BE_CANCELLED."""
    from opendv_platform_api.job_supervisor import JobContext, get_job_supervisor

    session_id = "s-committest"
    client.post(
        "/api/v1/ontology/studio/sessions",
        json={"session_id": session_id, "domain_namespace": "wealth_mgmt"},
        headers=auth_headers,
    )

    supervisor = get_job_supervisor()
    job_id = "job-a1b2c3d4e5f6"
    ctx = JobContext(
        job_id=job_id,
        session_id=session_id,
        job_type="construct",
        status="running",
        stage="committing",
        pct_complete=80,
        created_at="2026-11-10T12:00:00Z",
    )
    with supervisor._lock:
        supervisor._jobs[job_id] = ctx

    try:
        resp = client.post(
            f"/api/v1/ontology/studio/sessions/{session_id}/jobs/{job_id}/cancel",
            headers=auth_headers,
        )
        assert resp.status_code == 409
        assert resp.json()["error_code"] == "JOB_COMMIT_IN_PROGRESS"
    finally:
        with supervisor._lock:
            supervisor._jobs.pop(job_id, None)


def test_job_lookup_on_nonexistent_session_does_not_create_directory(
    client: TestClient, auth_headers: dict[str, str], tmp_path: Path
) -> None:
    """Test read-only job lookup on nonexistent session returns 404 without disk side effects."""
    ghost_sid = "s-ghost-session"
    resp = client.get(
        f"/api/v1/ontology/studio/sessions/{ghost_sid}/jobs/job-0123456789ab",
        headers=auth_headers,
    )
    assert resp.status_code == 404
    assert resp.json()["error_code"] == "SESSION_NOT_FOUND"

    # Verify no directory created on disk
    ghost_dir = tmp_path / "opendv_studio" / ghost_sid
    assert not ghost_dir.exists()


def test_auto_commit_construct_governance_rejections(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """Test auto-commit construct governance: agent, viewer, and agent-mode auto-commit rejected."""
    session_id = "s-autocommit-gov"
    client.post(
        "/api/v1/ontology/studio/sessions",
        json={"session_id": session_id, "domain_namespace": "wealth_mgmt"},
        headers=auth_headers,
    )

    sess_meta = client.get(
        f"/api/v1/ontology/studio/sessions/{session_id}", headers=auth_headers
    ).json()
    head_sha = sess_meta["head_sha256"]

    # 1. Agent caller in challenger mode is rejected with 403 AGENT_PROPOSAL_ACCEPT_FORBIDDEN
    resp_agent = client.post(
        f"/api/v1/ontology/studio/sessions/{session_id}/construct",
        json={"auto_commit": True, "mode": "challenger"},
        headers={
            **auth_headers,
            "X-OpenDV-Principal": "agent:builder_bot",
            "If-Match": f'"{head_sha}"',
        },
    )
    assert resp_agent.status_code == 403
    assert resp_agent.json()["error_code"] == "AGENT_PROPOSAL_ACCEPT_FORBIDDEN"

    # 2. Viewer is rejected with 403 SERVER_OPERATION_FORBIDDEN
    resp_viewer = client.post(
        f"/api/v1/ontology/studio/sessions/{session_id}/construct",
        json={"auto_commit": True, "mode": "deterministic"},
        headers={
            **auth_headers,
            "X-OpenDV-Role": "viewer",
            "X-OpenDV-Principal": "user:viewer_jane",
            "If-Match": f'"{head_sha}"',
        },
    )
    assert resp_viewer.status_code == 403
    assert resp_viewer.json()["error_code"] == "SERVER_OPERATION_FORBIDDEN"

    # 3. mode=agent with auto_commit=True is rejected with 400 SYNTAX_INVALID_ARGUMENT
    resp_agent_mode = client.post(
        f"/api/v1/ontology/studio/sessions/{session_id}/construct",
        json={"auto_commit": True, "mode": "agent"},
        headers={
            **auth_headers,
            "X-OpenDV-Role": "editor",
            "X-OpenDV-Principal": "user:editor_joe",
            "If-Match": f'"{head_sha}"',
        },
    )
    assert resp_agent_mode.status_code == 400
    assert resp_agent_mode.json()["error_code"] == "SYNTAX_INVALID_ARGUMENT"


def test_csrf_loopback_port_mismatch_rejected(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """Test cookie-authenticated request with loopback Origin on different port is rejected."""
    nonce_resp = client.post("/api/v1/ontology/studio/auth/launch-nonce", headers=auth_headers)
    assert nonce_resp.status_code == 200
    nonce = nonce_resp.json()["launch_nonce"]

    token_resp = client.post(
        "/api/v1/ontology/studio/auth/token",
        json={"launch_nonce": nonce},
    )
    assert token_resp.status_code == 200

    # Cross-port loopback origin is rejected
    resp = client.post(
        "/api/v1/ontology/studio/sessions",
        json={"session_id": "s-csrf-port", "domain_namespace": "wealth_mgmt"},
        headers={
            "X-OpenDV-Workbench": "1",
            "Origin": "http://127.0.0.1:9999",
            "Host": "localhost:8000",
        },
    )
    assert resp.status_code == 403
    assert resp.json()["error_code"] == "CSRF_ORIGIN_MISMATCH"


def test_parse_job_extracts_and_persists_tables(
    client: TestClient, auth_headers: dict[str, str], tmp_path: Path
) -> None:
    """Verify parse job extracts tabular structure and persists to evidence/tables.jsonl
    (MAJ-04).
    """
    session_id = "s-parse-tables"
    client.post(
        "/api/v1/ontology/studio/sessions",
        json={"session_id": session_id, "domain_namespace": "wealth_mgmt"},
        headers=auth_headers,
    )
    sdir = tmp_path / "opendv_studio" / session_id
    csv_content = (
        b"customer_id,customer_name,tier\nC01,Acme Corp,Enterprise\nC02,Beta LLC,MidMarket\n"
    )
    source_resp = client.post(
        f"/api/v1/ontology/studio/sessions/{session_id}/sources",
        files={"file": ("customers.csv", csv_content, "text/csv")},
        headers=auth_headers,
    )
    assert source_resp.status_code == 201

    # Trigger parse job
    parse_resp = client.post(
        f"/api/v1/ontology/studio/sessions/{session_id}/parse",
        json={},
        headers=auth_headers,
    )
    assert parse_resp.status_code == 202
    job_id = parse_resp.json()["job_id"]

    for _ in range(50):
        poll = client.get(
            f"/api/v1/ontology/studio/sessions/{session_id}/jobs/{job_id}",
            headers=auth_headers,
        )
        if poll.json()["status"] in ("completed", "failed"):
            break
        time.sleep(0.05)

    assert poll.json()["status"] == "completed"
    tables_file = sdir / "evidence" / "tables.jsonl"
    assert tables_file.is_file(), "evidence/tables.jsonl must exist after parse job"
    lines = tables_file.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) >= 1
    tbl_data = json.loads(lines[0])
    assert "headers" in tbl_data
    assert "customer_id" in tbl_data["headers"]


def test_cancel_job_committing_stage_rejected_and_on_disk_marker(
    client: TestClient, auth_headers: dict[str, str], tmp_path: Path
) -> None:
    """Verify job cancellation is rejected when committing and writes .cancel marker (MAJ-03)."""
    import threading
    from contextlib import contextmanager

    @contextmanager
    def hold_worker_lock(lock_path: Path, sid: str):
        ready = threading.Event()
        stop = threading.Event()
        err_box: list[Exception] = []

        def worker() -> None:
            try:
                with studio_lock(lock_path, level=L2_JOB, session_id=sid):
                    ready.set()
                    stop.wait()
            except (OSError, RuntimeError) as exc:
                err_box.append(exc)
                ready.set()

        t = threading.Thread(target=worker, daemon=True)
        t.start()
        ready.wait(timeout=5.0)
        if err_box:
            raise err_box[0]
        try:
            yield
        finally:
            stop.set()
            t.join(timeout=5.0)

    session_id = "s-cancel-committing"
    client.post(
        "/api/v1/ontology/studio/sessions",
        json={"session_id": session_id, "domain_namespace": "wealth_mgmt"},
        headers=auth_headers,
    )
    sdir = tmp_path / "opendv_studio" / session_id
    jobs_dir = sdir / "jobs"
    jobs_dir.mkdir(parents=True, exist_ok=True)

    # Simulate an on-disk job in 'committing' stage owned by another process
    job_id = "job-committing-01"
    job_data = {
        "job_id": job_id,
        "session_id": session_id,
        "job_type": "construct",
        "status": "running",
        "stage": "committing",
        "pct_complete": 95,
        "created_at": "2026-11-10T00:00:00Z",
        "finished_at": None,
        "principal": "human:local_admin",
        "error": None,
        "result": None,
    }
    (jobs_dir / f"{job_id}.json").write_text(json.dumps(job_data), encoding="utf-8")

    # Attempting to cancel actively committing job fails with 409
    with hold_worker_lock(jobs_dir / f".job_{job_id}.lock", session_id):
        cancel_resp = client.post(
            f"/api/v1/ontology/studio/sessions/{session_id}/jobs/{job_id}/cancel",
            headers=auth_headers,
        )
        assert cancel_resp.status_code == 409
        assert cancel_resp.json()["error_code"] == "JOB_COMMIT_IN_PROGRESS"

    # Now simulate a running job in 'extracting' stage
    job_id_2 = "job-running-02"
    job_data_2 = {
        "job_id": job_id_2,
        "session_id": session_id,
        "job_type": "construct",
        "status": "running",
        "stage": "extracting",
        "pct_complete": 30,
        "created_at": "2026-11-10T00:00:00Z",
        "finished_at": None,
        "principal": "human:local_admin",
        "error": None,
        "result": None,
    }
    (jobs_dir / f"{job_id_2}.json").write_text(json.dumps(job_data_2), encoding="utf-8")

    # Active worker holding L2_JOB lock on disk rejects cross-process fallback with 409
    with hold_worker_lock(jobs_dir / f".job_{job_id_2}.lock", session_id):
        cancel_resp_locked = client.post(
            f"/api/v1/ontology/studio/sessions/{session_id}/jobs/{job_id_2}/cancel",
            headers=auth_headers,
        )
        assert cancel_resp_locked.status_code == 409
        assert cancel_resp_locked.json()["error_code"] == "JOB_COMMIT_IN_PROGRESS"

    # Once L2_JOB lock is free, on-disk cancel acquires L2_JOB, writes .cancel & appends event
    cancel_resp_2 = client.post(
        f"/api/v1/ontology/studio/sessions/{session_id}/jobs/{job_id_2}/cancel",
        headers=auth_headers,
    )
    assert cancel_resp_2.status_code == 200
    assert (jobs_dir / f".{job_id_2}.cancel").is_file()
    assert (jobs_dir / f"{job_id_2}.events.jsonl").is_file()


def test_agent_launch_nonce_and_direct_delta_forbidden(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """Verify agent identities cannot mint nonces or commit direct deltas."""
    # 1. Agent calling launch nonce returns 403
    agent_headers = {**auth_headers, "X-OpenDV-Principal": "agent:bot_1"}
    resp_nonce = client.post("/api/v1/ontology/studio/auth/launch-nonce", headers=agent_headers)
    assert resp_nonce.status_code == 403
    assert resp_nonce.json()["error_code"] == "AGENT_OPERATION_FORBIDDEN"

    # 2. Agent calling direct delta apply returns 403
    session_id = "s-agent-delta"
    client.post(
        "/api/v1/ontology/studio/sessions",
        json={"session_id": session_id, "domain_namespace": "wealth_mgmt"},
        headers=auth_headers,
    )
    meta = client.get(f"/api/v1/ontology/studio/sessions/{session_id}", headers=auth_headers).json()
    resp_delta = client.post(
        f"/api/v1/ontology/studio/sessions/{session_id}/deltas/apply",
        json={
            "delta_id": "d-agent-01",
            "parent_sha256": "",
            "base_version": "0.0.0",
            "target_version": "1.0.0",
            "operations": [],
        },
        headers={**agent_headers, "If-Match": f'"{meta["head_sha256"]}"'},
    )
    assert resp_delta.status_code == 403
    assert resp_delta.json()["error_code"] == "AGENT_OPERATION_FORBIDDEN"


def test_proposal_traversal_and_stale_rebase_required(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """Verify proposal traversal rejected (400) and stale proposals require rebase (409)."""
    session_id = "s-traversal-stale"
    client.post(
        "/api/v1/ontology/studio/sessions",
        json={"session_id": session_id, "domain_namespace": "wealth_mgmt"},
        headers=auth_headers,
    )

    # 1. Invalid/traversal proposal_id returns 400
    resp_traversal = client.get(
        f"/api/v1/ontology/studio/sessions/{session_id}/proposals/prop-..-evil",
        headers=auth_headers,
    )
    assert resp_traversal.status_code == 400

    # 2. Create proposal against genesis head
    meta_1 = client.get(
        f"/api/v1/ontology/studio/sessions/{session_id}", headers=auth_headers
    ).json()
    genesis_sha = meta_1["head_sha256"]

    prop_resp = client.post(
        f"/api/v1/ontology/studio/sessions/{session_id}/proposals",
        json={
            "proposal_id": "prop-stale-01",
            "base_sha256": genesis_sha,
            "operations": [
                {
                    "op_type": "add_concept",
                    "op_id": "op-01",
                    "concept_uri": "urn:ontology:entity:instrument",
                    "concept_name": "Financial Instrument",
                    "definition": "A monetary contract.",
                }
            ],
            "rationale": "Add instrument concept",
        },
        headers=auth_headers,
    )
    assert prop_resp.status_code == 201
    prop_id = prop_resp.json()["proposal_id"]

    # Apply direct delta to advance session head
    apply_resp = client.post(
        f"/api/v1/ontology/studio/sessions/{session_id}/deltas/apply",
        json={
            "delta_id": "d-advance-01",
            "parent_sha256": "",
            "base_version": "0.0.0",
            "target_version": "1.0.0",
            "operations": [
                {
                    "op_type": "add_concept",
                    "op_id": "op-adv-01",
                    "concept_uri": "urn:ontology:entity:account",
                    "concept_name": "Account",
                    "definition": "A client account.",
                }
            ],
            "low_confidence_waiver": True,
            "grounding_rationale": "Approved account concept for stale rebase test.",
        },
        headers={**auth_headers, "If-Match": f'"{genesis_sha}"'},
    )
    assert apply_resp.status_code == 200
    new_head_sha = apply_resp.json()["target_sha256"]

    # Accepting prop with new_head_sha If-Match returns 409 PROPOSAL_STALE_REBASE_REQUIRED
    accept_resp = client.post(
        f"/api/v1/ontology/studio/sessions/{session_id}/proposals/{prop_id}/accept",
        json={"base_sha256": new_head_sha, "self_acceptance_override": True},
        headers={**auth_headers, "If-Match": f'"{new_head_sha}"'},
    )
    assert accept_resp.status_code == 409
    assert accept_resp.json()["error_code"] == "PROPOSAL_STALE_REBASE_REQUIRED"


def test_domain_namespace_validation_and_non_admin_delta_rejected(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """Verify domain_namespace validation and non-admin direct delta rejection."""
    # 1. Invalid domain_namespace returns 400
    bad_domain_resp = client.post(
        "/api/v1/ontology/studio/sessions",
        json={"domain_namespace": "123_invalid_namespace"},
        headers=auth_headers,
    )
    assert bad_domain_resp.status_code == 400
    assert bad_domain_resp.json()["error_code"] == "SYNTAX_INVALID_ARGUMENT"

    # 2. Non-admin human destructive direct delta rejected with 403 FORBIDDEN_ADMIN_REQUIRED
    session_id = "s-nonadmin-delta"
    client.post(
        "/api/v1/ontology/studio/sessions",
        json={"session_id": session_id, "domain_namespace": "wealth_mgmt"},
        headers=auth_headers,
    )
    genesis_sha = client.get(
        f"/api/v1/ontology/studio/sessions/{session_id}", headers=auth_headers
    ).json()["head_sha256"]
    seed_resp = client.post(
        f"/api/v1/ontology/studio/sessions/{session_id}/deltas/apply",
        json={
            "delta_id": "d-seed-01",
            "parent_sha256": "",
            "base_version": "0.0.0",
            "target_version": "1.0.0",
            "operations": [
                {
                    "op_type": "add_concept",
                    "op_id": "op-seed-1",
                    "concept_uri": "urn:ontology:entity:account",
                    "concept_name": "Account",
                    "definition": "Client account.",
                }
            ],
            "low_confidence_waiver": True,
            "grounding_rationale": "Approved seed concept for destructive delta RBAC test.",
        },
        headers={**auth_headers, "If-Match": f'"{genesis_sha}"'},
    )
    assert seed_resp.status_code == 200
    head_1_sha = seed_resp.json()["target_sha256"]
    editor_headers = {
        **auth_headers,
        "X-OpenDV-Role": "editor",
        "X-OpenDV-Principal": "user:charlie",
        "If-Match": f'"{head_1_sha}"',
    }
    resp_delta = client.post(
        f"/api/v1/ontology/studio/sessions/{session_id}/deltas/apply",
        json={
            "delta_id": "d-editor-01",
            "parent_sha256": head_1_sha,
            "base_version": "1.0.0",
            "target_version": "2.0.0",
            "operations": [
                {
                    "op_type": "remove_concept",
                    "op_id": "op-del-1",
                    "concept_uri": "urn:ontology:entity:account",
                    "reason": "Destructive removal requires admin role.",
                }
            ],
        },
        headers=editor_headers,
    )
    assert resp_delta.status_code == 403
    assert resp_delta.json()["error_code"] == "FORBIDDEN_ADMIN_REQUIRED"


def test_clarification_answers_missing_precondition_and_manifest_resilience(
    client: TestClient, auth_headers: dict[str, str], tmp_path: Path
) -> None:
    """Verify missing expected_answers_sha256 returns 400 and manifest re-upload updates flags."""
    session_id = "s-answers-precond"
    client.post(
        "/api/v1/ontology/studio/sessions",
        json={"session_id": session_id, "domain_namespace": "wealth_mgmt"},
        headers=auth_headers,
    )

    # 1. Missing expected_answers_sha256 returns 400
    missing_precond_resp = client.post(
        f"/api/v1/ontology/studio/sessions/{session_id}/answers",
        json={"answers": {"q1": "Answer"}},
        headers=auth_headers,
    )
    assert missing_precond_resp.status_code == 400
    assert missing_precond_resp.json()["error_code"] == "MISSING_AUTO_COMMIT_PRECONDITION"

    # 2. Re-upload existing source updates do_not_send_to_agent
    content = b"header1,header2\nval1,val2\n"
    up1 = client.post(
        f"/api/v1/ontology/studio/sessions/{session_id}/sources",
        files={"file": ("doc.csv", content, "text/csv")},
        data={"do_not_send_to_agent": "false"},
        headers=auth_headers,
    )
    assert up1.status_code == 201

    up2 = client.post(
        f"/api/v1/ontology/studio/sessions/{session_id}/sources",
        files={"file": ("doc.csv", content, "text/csv")},
        data={"do_not_send_to_agent": "true"},
        headers=auth_headers,
    )
    assert up2.status_code == 201

    srcs = client.get(
        f"/api/v1/ontology/studio/sessions/{session_id}/sources", headers=auth_headers
    ).json()
    assert len(srcs) == 1
    assert srcs[0]["do_not_send_to_agent"] is True

    # 3. Corrupt manifest returns 500 CORRUPT_SESSION_RECORD
    sdir = tmp_path / "opendv_studio" / session_id
    (sdir / "inputs" / "manifest.json").write_text("NOT_VALID_JSON{{{", encoding="utf-8")
    up3 = client.post(
        f"/api/v1/ontology/studio/sessions/{session_id}/sources",
        files={"file": ("doc2.csv", b"other,content", "text/csv")},
        headers=auth_headers,
    )
    assert up3.status_code == 500
    assert up3.json()["error_code"] == "CORRUPT_SESSION_RECORD"


def test_rebase_semantic_conflict_and_success(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """Verify proposal rebase fails on concept conflict (409) and succeeds when clean."""
    session_id = "s-rebase-conflict"
    client.post(
        "/api/v1/ontology/studio/sessions",
        json={"session_id": session_id, "domain_namespace": "wealth_mgmt"},
        headers=auth_headers,
    )

    # 1. Advance head to 1.0.0 with concept A
    genesis_sha = client.get(
        f"/api/v1/ontology/studio/sessions/{session_id}", headers=auth_headers
    ).json()["head_sha256"]
    apply1 = client.post(
        f"/api/v1/ontology/studio/sessions/{session_id}/deltas/apply",
        json={
            "delta_id": "d-init-01",
            "parent_sha256": "",
            "base_version": "0.0.0",
            "target_version": "1.0.0",
            "operations": [
                {
                    "op_type": "add_concept",
                    "op_id": "op-a",
                    "concept_uri": "urn:ontology:entity:portfolio",
                    "concept_name": "Portfolio",
                    "definition": "An investment portfolio.",
                }
            ],
            "low_confidence_waiver": True,
            "grounding_rationale": "Approved portfolio concept for rebase conflict test.",
        },
        headers={**auth_headers, "If-Match": f'"{genesis_sha}"'},
    )
    assert apply1.status_code == 200
    head_1_sha = apply1.json()["target_sha256"]

    # 2. Create proposal targeting old genesis base with conflicting concept A
    prop_resp = client.post(
        f"/api/v1/ontology/studio/sessions/{session_id}/proposals",
        json={
            "proposal_id": "prop-conflict-01",
            "base_sha256": genesis_sha,
            "operations": [
                {
                    "op_type": "add_concept",
                    "op_id": "op-a-dupe",
                    "concept_uri": "urn:ontology:entity:portfolio",
                    "concept_name": "Duplicate Portfolio",
                    "definition": "Duplicate.",
                }
            ],
            "rationale": "Add portfolio",
        },
        headers=auth_headers,
    )
    assert prop_resp.status_code == 201
    prop_conflict_id = prop_resp.json()["proposal_id"]

    # 3. Attempt rebase onto head_1_sha -> 409 PROPOSAL_STALE_REBASE_REQUIRED due to conflict
    rebase_fail = client.post(
        f"/api/v1/ontology/studio/sessions/{session_id}/proposals/{prop_conflict_id}/rebase",
        json={"base_sha256": head_1_sha},
        headers={**auth_headers, "If-Match": f'"{head_1_sha}"'},
    )
    assert rebase_fail.status_code == 409
    assert rebase_fail.json()["error_code"] == "PROPOSAL_STALE_REBASE_REQUIRED"

    # 4. Create proposal targeting old genesis base with non-conflicting concept B
    prop_ok_resp = client.post(
        f"/api/v1/ontology/studio/sessions/{session_id}/proposals",
        json={
            "base_sha256": genesis_sha,
            "operations": [
                {
                    "op_type": "add_concept",
                    "op_id": "op-b",
                    "concept_uri": "urn:ontology:entity:risk_profile",
                    "concept_name": "Risk Profile",
                    "definition": "A risk tolerance assessment.",
                }
            ],
            "rationale": "Add risk profile",
        },
        headers=auth_headers,
    )
    assert prop_ok_resp.status_code == 201
    prop_clean_id = prop_ok_resp.json()["proposal_id"]

    # 5. Rebase onto head_1_sha -> 200 OK
    rebase_ok = client.post(
        f"/api/v1/ontology/studio/sessions/{session_id}/proposals/{prop_clean_id}/rebase",
        json={"base_sha256": head_1_sha},
        headers={**auth_headers, "If-Match": f'"{head_1_sha}"'},
    )
    assert rebase_ok.status_code == 200
    assert rebase_ok.json()["base_sha256"] == head_1_sha
    assert rebase_ok.json()["status"] == "PENDING"


def test_promote_downgrade_prevention_and_acknowledgement(
    client: TestClient, auth_headers: dict[str, str], tmp_path: Path
) -> None:
    """Verify promote rejects downgrade (422) unless historical warning and rationale provided."""
    import hashlib

    import yaml

    from opendv_core.genesis import create_genesis_ontology
    from opendv_core.models.graph_a import CanonicalBusinessConcept

    ws = tmp_path / "workspace"
    ws.mkdir(parents=True, exist_ok=True)
    existing_contract = ws / "wealth_mgmt_graph_a_ontology.yaml"
    ws_ont = create_genesis_ontology(domain_namespace="wealth_mgmt").model_copy(
        update={
            "ontology_version": "2.0.0",
            "canonical_business_concepts": [
                CanonicalBusinessConcept(
                    concept_uri="urn:ontology:entity:asset",
                    concept_name="Asset",
                    target_hub="hub_asset",
                )
            ],
        }
    )
    existing_contract.write_text(
        yaml.safe_dump(ws_ont.model_dump(mode="json"), sort_keys=True),
        encoding="utf-8",
    )

    ws_sha = hashlib.sha256(existing_contract.read_bytes()).hexdigest()

    session_id = "s-promote-downgrade"
    client.post(
        "/api/v1/ontology/studio/sessions",
        json={"session_id": session_id, "domain_namespace": "wealth_mgmt"},
        headers=auth_headers,
    )
    genesis_sha = client.get(
        f"/api/v1/ontology/studio/sessions/{session_id}", headers=auth_headers
    ).json()["head_sha256"]

    # Commit version 1.0.0 (lower than workspace 2.0.0)
    apply_resp = client.post(
        f"/api/v1/ontology/studio/sessions/{session_id}/deltas/apply",
        json={
            "delta_id": "d-downgrade-01",
            "parent_sha256": "",
            "base_version": "0.0.0",
            "target_version": "1.0.0",
            "operations": [
                {
                    "op_type": "add_concept",
                    "op_id": "op-d1",
                    "concept_uri": "urn:ontology:entity:asset",
                    "concept_name": "Asset",
                    "definition": "An economic resource.",
                }
            ],
            "low_confidence_waiver": True,
            "grounding_rationale": "Approved asset concept for downgrade promotion test.",
        },
        headers={**auth_headers, "If-Match": f'"{genesis_sha}"'},
    )
    assert apply_resp.status_code == 200
    head_sha = apply_resp.json()["target_sha256"]

    # Attempt promotion without acknowledge flag -> 422 HISTORICAL_CONTRACT_WARNING_UNACKNOWLEDGED
    fail_promo = client.post(
        f"/api/v1/ontology/studio/sessions/{session_id}/promote",
        json={
            "workspace_sha256": ws_sha,
            "head_sha256": head_sha,
            "rationale": "Attempted downgrade promotion",
            "workspace": str(ws),
            "acknowledge_historical_contract_warning": False,
        },
        headers={
            **auth_headers,
            "If-Match": f'"{head_sha}"',
            "X-OpenDV-Workspace-If-Match": f'"{ws_sha}"',
        },
    )
    assert fail_promo.status_code == 422
    assert fail_promo.json()["error_code"] == "HISTORICAL_CONTRACT_WARNING_UNACKNOWLEDGED"

    # Attempt promotion with acknowledge flag but missing historical_contract_rationale -> 400
    missing_hist_rat = client.post(
        f"/api/v1/ontology/studio/sessions/{session_id}/promote",
        json={
            "workspace_sha256": ws_sha,
            "head_sha256": head_sha,
            "rationale": "Downgrade acknowledged for rollback",
            "workspace": str(ws),
            "acknowledge_historical_contract_warning": True,
        },
        headers={
            **auth_headers,
            "If-Match": f'"{head_sha}"',
            "X-OpenDV-Workspace-If-Match": f'"{ws_sha}"',
        },
    )
    assert missing_hist_rat.status_code == 400
    assert missing_hist_rat.json()["error_code"] == "HISTORICAL_CONTRACT_RATIONALE_REQUIRED"

    # Attempt promotion with acknowledge flag + historical_contract_rationale -> 200 OK
    ok_promo = client.post(
        f"/api/v1/ontology/studio/sessions/{session_id}/promote",
        json={
            "workspace_sha256": ws_sha,
            "head_sha256": head_sha,
            "rationale": "Downgrade acknowledged for rollback",
            "workspace": str(ws),
            "acknowledge_historical_contract_warning": True,
            "historical_contract_rationale": "Approved historical contract rollback to 1.0.0.",
        },
        headers={
            **auth_headers,
            "If-Match": f'"{head_sha}"',
            "X-OpenDV-Workspace-If-Match": f'"{ws_sha}"',
        },
    )
    assert ok_promo.status_code == 200, ok_promo.text
    assert ok_promo.json()["promoted_version"] == "1.0.0"


def test_clone_session_isolation_and_atomic_metadata(
    client: TestClient, auth_headers: dict[str, str], tmp_path: Path
) -> None:
    """Verify session clone creates isolated session excluding ephemeral locks and runtime jobs."""
    session_id = "s-clone-src"
    client.post(
        "/api/v1/ontology/studio/sessions",
        json={"session_id": session_id, "domain_namespace": "wealth_mgmt"},
        headers=auth_headers,
    )
    sdir = tmp_path / "opendv_studio" / session_id
    # Create runtime artifacts that must be ignored
    (sdir / "jobs").mkdir(exist_ok=True)
    (sdir / "jobs" / "job_dummy.json").write_text("{}", encoding="utf-8")
    (sdir / ".session.lock").write_text("lock", encoding="utf-8")

    clone_resp = client.post(
        f"/api/v1/ontology/studio/sessions/{session_id}/bundle/clone",
        json={"new_session_id": "s-clone-dst"},
        headers=auth_headers,
    )
    assert clone_resp.status_code == 201
    assert clone_resp.json()["session_id"] == "s-clone-dst"

    clone_dir = tmp_path / "opendv_studio" / "s-clone-dst"
    assert clone_dir.is_dir()
    assert not (clone_dir / "jobs").exists()
    assert not (clone_dir / ".session.lock").exists()
    assert (clone_dir / "session.json").is_file()


def test_validate_delta_preconditions_and_conflicts(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """Verify POST /deltas/validate returns valid=False for OCC mismatch,
    invalid bump, and conflict.
    """
    session_id = "s-val-delta"
    client.post(
        "/api/v1/ontology/studio/sessions",
        json={"session_id": session_id, "domain_namespace": "wealth_mgmt"},
        headers=auth_headers,
    )
    meta_0 = client.get(
        f"/api/v1/ontology/studio/sessions/{session_id}", headers=auth_headers
    ).json()
    genesis_sha = meta_0["head_sha256"]

    # 1. Advance head to 1.0.0
    apply1 = client.post(
        f"/api/v1/ontology/studio/sessions/{session_id}/deltas/apply",
        json={
            "delta_id": "d-v-init",
            "parent_sha256": "",
            "base_version": "0.0.0",
            "target_version": "1.0.0",
            "operations": [
                {
                    "op_type": "add_concept",
                    "op_id": "op-c1",
                    "concept_uri": "urn:ontology:entity:claim",
                    "concept_name": "Insurance Claim",
                    "definition": "A formal request for coverage.",
                }
            ],
            "low_confidence_waiver": True,
            "grounding_rationale": "Approved claim concept for validation test.",
        },
        headers={**auth_headers, "If-Match": f'"{genesis_sha}"'},
    )
    assert apply1.status_code == 200
    head_1_sha = apply1.json()["target_sha256"]

    # 2. Validate delta with stale parent -> valid=False
    val_stale = client.post(
        f"/api/v1/ontology/studio/sessions/{session_id}/deltas/validate",
        json={
            "delta_id": "d-stale",
            "parent_sha256": genesis_sha,
            "base_version": "0.0.0",
            "target_version": "1.0.0",
            "operations": [],
        },
        headers=auth_headers,
    )
    assert val_stale.status_code == 200
    assert val_stale.json()["valid"] is False
    assert len(val_stale.json()["violations"]) > 0

    # 3. Validate delta with duplicate concept -> valid=False
    val_conflict = client.post(
        f"/api/v1/ontology/studio/sessions/{session_id}/deltas/validate",
        json={
            "delta_id": "d-conflict",
            "parent_sha256": head_1_sha,
            "base_version": "1.0.0",
            "target_version": "1.1.0",
            "operations": [
                {
                    "op_type": "add_concept",
                    "op_id": "op-c1-dupe",
                    "concept_uri": "urn:ontology:entity:claim",
                    "concept_name": "Duplicate Claim",
                    "definition": "Duplicate definition.",
                }
            ],
        },
        headers=auth_headers,
    )
    assert val_conflict.status_code == 200
    assert val_conflict.json()["valid"] is False

    # 4a. Validate ungrounded delta without waiver -> valid=False (ONT-EVO-002 / P10b MAJ-01)
    val_ungrounded = client.post(
        f"/api/v1/ontology/studio/sessions/{session_id}/deltas/validate",
        json={
            "delta_id": "d-ungrounded",
            "parent_sha256": head_1_sha,
            "base_version": "1.0.0",
            "target_version": "1.1.0",
            "operations": [
                {
                    "op_type": "add_concept",
                    "op_id": "op-c2",
                    "concept_uri": "urn:ontology:entity:policy",
                    "concept_name": "Policy",
                    "definition": "Insurance policy.",
                }
            ],
        },
        headers=auth_headers,
    )
    assert val_ungrounded.status_code == 200
    assert val_ungrounded.json()["valid"] is False
    assert val_ungrounded.json()["error_code"] == "ONT_EVO_UNGROUNDED_CONCEPT"
    assert len(val_ungrounded.json()["violations"]) > 0

    # 4b. Validate clean delta with dry-run admin waiver -> valid=True
    val_clean = client.post(
        f"/api/v1/ontology/studio/sessions/{session_id}/deltas/validate",
        json={
            "delta_id": "d-clean",
            "parent_sha256": head_1_sha,
            "base_version": "1.0.0",
            "target_version": "1.1.0",
            "operations": [
                {
                    "op_type": "add_concept",
                    "op_id": "op-c2",
                    "concept_uri": "urn:ontology:entity:policy",
                    "concept_name": "Policy",
                    "definition": "Insurance policy.",
                }
            ],
            "low_confidence_waiver": True,
            "grounding_rationale": "Approved dry-run grounding waiver for policy concept.",
        },
        headers=auth_headers,
    )
    assert val_clean.status_code == 200
    assert val_clean.json()["valid"] is True
    assert len(val_clean.json()["violations"]) == 0


def test_reupload_tombstoned_source_restores_manifest_and_list(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """Verify that re-uploading a previously tombstoned source clears tombstone
    and restores list visibility.
    """
    session_id = "s-reupload-src"
    client.post(
        "/api/v1/ontology/studio/sessions",
        json={"session_id": session_id, "domain_namespace": "wealth_mgmt"},
        headers=auth_headers,
    )

    # 1. Upload initial source
    f_content = b"sample content for testing tombstone revival"
    up1 = client.post(
        f"/api/v1/ontology/studio/sessions/{session_id}/sources",
        files={"file": ("notes.txt", io.BytesIO(f_content), "text/plain")},
        data={"do_not_send_to_agent": "false"},
        headers=auth_headers,
    )
    assert up1.status_code == 201
    source_id = up1.json()["source_id"]

    # 2. Verify source in list
    list1 = client.get(
        f"/api/v1/ontology/studio/sessions/{session_id}/sources", headers=auth_headers
    )
    assert list1.status_code == 200
    assert any(s["source_id"] == source_id for s in list1.json())

    # 3. Delete source -> tombstone
    del_res = client.delete(
        f"/api/v1/ontology/studio/sessions/{session_id}/sources/{source_id}",
        headers=auth_headers,
    )
    assert del_res.status_code == 204

    # 4. Verify source not in list
    list2 = client.get(
        f"/api/v1/ontology/studio/sessions/{session_id}/sources", headers=auth_headers
    )
    assert list2.status_code == 200
    assert not any(s["source_id"] == source_id for s in list2.json())

    # 5. Re-upload identical content with do_not_send_to_agent=True
    up2 = client.post(
        f"/api/v1/ontology/studio/sessions/{session_id}/sources",
        files={"file": ("notes_revived.txt", io.BytesIO(f_content), "text/plain")},
        data={"do_not_send_to_agent": "true"},
        headers=auth_headers,
    )
    assert up2.status_code == 201
    assert up2.json()["source_id"] == source_id
    assert up2.json()["do_not_send_to_agent"] is True

    # 6. Verify restored in list with tombstoned=False
    list3 = client.get(
        f"/api/v1/ontology/studio/sessions/{session_id}/sources", headers=auth_headers
    )
    assert list3.status_code == 200
    matching = [s for s in list3.json() if s["source_id"] == source_id]
    assert len(matching) == 1
    assert matching[0]["tombstoned"] is False
    assert matching[0]["do_not_send_to_agent"] is True


def test_bundle_import_unverified_attribution_governance_gate(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """Verify import bundle rejects unverified attribution without explicit waiver and rationale."""
    session_id = "s-import-src"
    client.post(
        "/api/v1/ontology/studio/sessions",
        json={"session_id": session_id, "domain_namespace": "wealth_mgmt"},
        headers=auth_headers,
    )

    # Export valid bundle
    exp = client.post(
        f"/api/v1/ontology/studio/sessions/{session_id}/bundle",
        json={"acknowledge_confidential_source_export": True},
        headers=auth_headers,
    )
    assert exp.status_code == 200
    zip_bytes = exp.content

    # Inject unverified_attribution into bundle.json in memory
    buf_in = io.BytesIO(zip_bytes)
    buf_out = io.BytesIO()
    with zipfile.ZipFile(buf_in, "r") as zin, zipfile.ZipFile(buf_out, "w") as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if item.filename == "bundle.json":
                b_data = json.loads(data.decode("utf-8"))
                b_data["unverified_attribution"] = True
                data = json.dumps(b_data).encode("utf-8")
            zout.writestr(item, data)

    tampered_zip = buf_out.getvalue()

    # Delete source session so import destination does not conflict
    del_s = client.delete(
        f"/api/v1/ontology/studio/sessions/{session_id}?force=true", headers=auth_headers
    )
    assert del_s.status_code == 204

    # 1. Import without acknowledgment -> 422 IMPORT_GROUNDING_VIOLATION
    imp_fail = client.post(
        "/api/v1/ontology/studio/sessions/bundle/import",
        files={"file": ("bundle.zip", io.BytesIO(tampered_zip), "application/zip")},
        headers=auth_headers,
    )
    assert imp_fail.status_code == 422
    assert imp_fail.json()["error_code"] == "IMPORT_GROUNDING_VIOLATION"

    # 2. Import with acknowledgement but missing rationale -> 400
    imp_no_rat = client.post(
        "/api/v1/ontology/studio/sessions/bundle/import",
        files={"file": ("bundle.zip", io.BytesIO(tampered_zip), "application/zip")},
        params={"acknowledge_unverified_import_attribution": "true"},
        headers=auth_headers,
    )
    assert imp_no_rat.status_code == 400
    assert imp_no_rat.json()["error_code"] == "IMPORT_GROUNDING_RATIONALE_REQUIRED"

    # 3. Import with acknowledgement and valid rationale -> 201 StudioSessionDTO
    imp_ok = client.post(
        "/api/v1/ontology/studio/sessions/bundle/import",
        files={"file": ("bundle.zip", io.BytesIO(tampered_zip), "application/zip")},
        params={
            "acknowledge_unverified_import_attribution": "true",
            "acknowledgement_rationale": "Audited and approved external vendor attribution waiver.",
        },
        headers=auth_headers,
    )
    assert imp_ok.status_code == 201
    assert imp_ok.json()["session_id"] == session_id
