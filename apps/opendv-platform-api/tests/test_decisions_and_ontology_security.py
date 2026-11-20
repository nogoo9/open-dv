"""Security & RBAC regression tests for `/runs/{run_id}/decisions` and `/ontology/decisions`."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from starlette.testclient import TestClient

from opendv_contracts import RunStatus
from opendv_platform_api.main import create_app
from opendv_service import OpenDVSettings, PlatformStateContainer
from opendv_testkit import seed_canonical_pe_demo

_TEST_RSA_PRIVATE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_TEST_RSA_PUBLIC_KEY_PEM = (
    _TEST_RSA_PRIVATE_KEY.public_key()
    .public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    .decode("utf-8")
)

_CSRF_AND_HOST_VECTORS: tuple[tuple[str, dict[str, str]], ...] = (
    ("dns-rebinding-host", {"Host": "evil.attacker.com:8000"}),
    ("cross-origin", {"Origin": "https://evil.attacker.com"}),
    ("cross-site-fetch", {"Sec-Fetch-Site": "cross-site"}),
    ("opaque-origin-null", {"Origin": "null"}),
)


def _mint_test_jwt(*, subject: str, role: str) -> str:
    now = int(time.time())
    return jwt.encode(
        {
            "sub": subject,
            "role": role,
            "iss": "opendv-platform",
            "aud": "opendv-api",
            "iat": now,
            "exp": now + 3600,
        },
        _TEST_RSA_PRIVATE_KEY,
        algorithm="RS256",
    )


def _sample_base_ontology() -> dict[str, Any]:
    return {
        "ontology_version": "1.0.0",
        "domain_namespace": "logistics",
        "enterprise_owner": "Apex Logistics",
        "standards_references": ["ISO 8601"],
        "canonical_business_concepts": [
            {
                "concept_uri": "urn:ontology:entity:depot",
                "concept_name": "Depot",
                "elm_archetype": "CORE_BUSINESS_CONCEPT",
                "definition": "A distribution center depot.",
                "target_hub": "hub_depot",
            }
        ],
        "canonical_relationships": [],
        "reference_enumerations": [],
    }


def _sample_hold_payload() -> dict[str, Any]:
    return {
        "event_id": "evt-pe-004",
        "hold_id": "hold-tier-u3-horizon-sovereign-vehicle",
        "decision_type": "RESOLVE_CAPTAIN_HOLD",
        "action": "APPROVE",
        "selected_candidate_id": "APEX_CO_I",
        "resolution_notes": "Confirmed co-invest sleeve.",
        "steward_id": "steward-jane-doe",
    }


def _sample_ontology_reject_payload() -> dict[str, Any]:
    return {
        "action": "REJECT",
        "proposal_id": "prop-sec-test-01",
        "base_ontology": _sample_base_ontology(),
        "decided_by": "lead-steward",
        "steward_notes": "Security test decision.",
    }


@pytest.fixture
def security_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[TestClient, PlatformStateContainer, str]:
    ws = tmp_path / "ws"
    state_dir = tmp_path / "state"
    studio_root = tmp_path / "studio"
    ws.mkdir(parents=True, exist_ok=True)
    studio_root.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("OPENDV_WORKSPACE_ROOT", str(ws))
    monkeypatch.setenv("OPENDV_STUDIO_ROOT", str(studio_root))
    monkeypatch.setenv("OPENDV_SERVER_MODE", "local_standalone")
    for k in ("OPENDV_JWT_PUBLIC_KEY", "OPENDV_JWT_ISSUER", "OPENDV_JWT_AUDIENCE"):
        monkeypatch.delenv(k, raising=False)

    state = PlatformStateContainer(state_dir=state_dir, workspace_root=ws)
    seed_canonical_pe_demo(state)
    settings = OpenDVSettings.load(workspace_root=ws, state_dir=state_dir, is_server=True)
    app = create_app(
        platform_state=state,
        settings=settings,
        workspace_root=ws,
        storage_root=state_dir,
    )
    client = TestClient(app, client=("127.0.0.1", 50000), base_url="http://127.0.0.1:8000")
    return client, state, "run-pe-apex-horizon-20261030"


def test_run_decisions_get_and_post_reject_csrf_and_dns_rebinding(
    security_client: tuple[TestClient, PlatformStateContainer, str],
) -> None:
    """GET and POST /runs/{run_id}/decisions reject Host/Origin/Fetch spoofs with 403."""
    client, state, run_id = security_client

    for label, headers in _CSRF_AND_HOST_VECTORS:
        get_resp = client.get(f"/api/v1/runs/{run_id}/decisions", headers=headers)
        assert get_resp.status_code == 403, (
            f"GET /decisions ({label}) expected 403, got {get_resp.status_code}: {get_resp.text}"
        )

        post_resp = client.post(
            f"/api/v1/runs/{run_id}/decisions",
            json=_sample_hold_payload(),
            headers=headers,
        )
        assert post_resp.status_code == 403, (
            f"POST /decisions ({label}) expected 403, got {post_resp.status_code}: {post_resp.text}"
        )

    assert state.runs[run_id].status == RunStatus.PAUSED_HOLD


def test_run_decisions_post_rejects_public_role_and_allows_loopback_custodian(
    security_client: tuple[TestClient, PlatformStateContainer, str],
) -> None:
    """POST /runs/{run_id}/decisions rejects PUBLIC with 403 and allows loopback custodian."""
    client, state, run_id = security_client

    pub_resp = client.post(
        f"/api/v1/runs/{run_id}/decisions",
        json=_sample_hold_payload(),
        headers={"Host": "127.0.0.1:8000", "X-OpenDV-Role": "PUBLIC"},
    )
    assert pub_resp.status_code == 403, pub_resp.text
    assert state.runs[run_id].status == RunStatus.PAUSED_HOLD

    # Legitimate loopback GET + POST (default PRIVILEGED_CUSTODIAN) still succeed cleanly
    list_ok = client.get(
        f"/api/v1/runs/{run_id}/decisions",
        headers={"Host": "127.0.0.1:8000"},
    )
    assert list_ok.status_code == 200, list_ok.text
    assert len(list_ok.json()["active_holds"]) == 1

    post_ok = client.post(
        f"/api/v1/runs/{run_id}/decisions",
        json=_sample_hold_payload(),
        headers={"Host": "127.0.0.1:8000"},
    )
    assert post_ok.status_code == 200, post_ok.text
    assert post_ok.json()["status"] == "ACCEPTED"
    assert state.runs[run_id].status == RunStatus.COMPLETED


def test_ontology_decisions_post_rejects_csrf_dns_rebinding_and_public_role(
    security_client: tuple[TestClient, PlatformStateContainer, str],
) -> None:
    """POST /api/v1/ontology/decisions rejects PUBLIC and CSRF/Host spoofs with 403."""
    client, _state, _run_id = security_client
    payload = _sample_ontology_reject_payload()

    for label, headers in _CSRF_AND_HOST_VECTORS:
        resp = client.post("/api/v1/ontology/decisions", json=payload, headers=headers)
        assert resp.status_code == 403, (
            f"POST /ontology/decisions ({label}) expected 403, got {resp.status_code}: {resp.text}"
        )

    pub_resp = client.post(
        "/api/v1/ontology/decisions",
        json=payload,
        headers={"Host": "127.0.0.1:8000", "X-OpenDV-Role": "PUBLIC"},
    )
    assert pub_resp.status_code == 403, pub_resp.text

    # Legitimate loopback POST succeeds
    ok_resp = client.post(
        "/api/v1/ontology/decisions",
        json=payload,
        headers={"Host": "127.0.0.1:8000"},
    )
    assert ok_resp.status_code == 200, ok_resp.text
    assert ok_resp.json()["status"] == "COMPLETED"


def test_decisions_and_ontology_decisions_enforce_remote_platform_auth_and_rbac(
    security_client: tuple[TestClient, PlatformStateContainer, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """In remote_platform mode, unauthenticated calls fail closed and viewer JWT gets 403."""
    client, state, run_id = security_client

    monkeypatch.setenv("OPENDV_SERVER_MODE", "remote_platform")
    monkeypatch.setenv("OPENDV_JWT_PUBLIC_KEY", _TEST_RSA_PUBLIC_KEY_PEM)
    monkeypatch.setenv("OPENDV_JWT_ISSUER", "opendv-platform")
    monkeypatch.setenv("OPENDV_JWT_AUDIENCE", "opendv-api")

    # Unauthenticated calls in remote_platform mode fail closed (401 UNAUTHENTICATED via ViewerDep)
    r_get_unauth = client.get(f"/api/v1/runs/{run_id}/decisions")
    assert r_get_unauth.status_code in (401, 403), r_get_unauth.text

    r_post_hold_unauth = client.post(
        f"/api/v1/runs/{run_id}/decisions",
        json=_sample_hold_payload(),
    )
    assert r_post_hold_unauth.status_code in (401, 403), r_post_hold_unauth.text

    r_post_ont_unauth = client.post(
        "/api/v1/ontology/decisions",
        json=_sample_ontology_reject_payload(),
    )
    assert r_post_ont_unauth.status_code in (401, 403), r_post_ont_unauth.text

    # Viewer JWT maps to RbacRole.PUBLIC -> GET allowed (200), POST rejected (403)
    viewer_jwt = _mint_test_jwt(subject="user:viewer", role="viewer")
    viewer_headers = {"Authorization": f"Bearer {viewer_jwt}"}
    assert client.get(f"/api/v1/runs/{run_id}/decisions", headers=viewer_headers).status_code == 200
    assert (
        client.post(
            f"/api/v1/runs/{run_id}/decisions",
            json=_sample_hold_payload(),
            headers=viewer_headers,
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/v1/ontology/decisions",
            json=_sample_ontology_reject_payload(),
            headers=viewer_headers,
        ).status_code
        == 403
    )
    assert state.runs[run_id].status == RunStatus.PAUSED_HOLD

    # Admin JWT succeeds on both POST endpoints
    admin_jwt = _mint_test_jwt(subject="user:admin", role="admin")
    admin_headers = {"Authorization": f"Bearer {admin_jwt}"}
    assert (
        client.post(
            "/api/v1/ontology/decisions",
            json=_sample_ontology_reject_payload(),
            headers=admin_headers,
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"/api/v1/runs/{run_id}/decisions",
            json=_sample_hold_payload(),
            headers=admin_headers,
        ).status_code
        == 200
    )
    assert state.runs[run_id].status == RunStatus.COMPLETED
