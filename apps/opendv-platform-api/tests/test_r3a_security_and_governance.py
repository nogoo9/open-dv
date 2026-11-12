"""Wave R3-A Regression Suite: 4-Tier RBAC, Auth Perimeter, Governance & Job Supervisor.

Covers:
- CRITICAL-01, P10::MAJ-01, P10::MAJ-02: 4-tier RBAC matrix (viewer, editor, admin, automated)
  and confidential source agent taint guard + 11-step accept_proposal error precedence.
- CRITICAL-02, CRITICAL-03, MAJOR-05, MINOR-03, a981cf1e::CRIT-01: Launch nonce role preservation,
  bounded nonce/session stores, remote cookie rejection, Origin: null & Sec-Fetch-Site: cross-site
  CSRF guards, /ontology SPA routes + Referrer-Policy, CORS headers/methods, streaming upload caps.
- CRITICAL-04..07, P10::MAJ-03..04, MAJOR-01..08, a981cf1e::CRIT-05: Signed audit.jsonl chain
  continuity across waiver/accept/promote/delta, SemVer validation past 1.1.0,
  check_rebase_conflicts, strong ETag enforcement, workspace contract discovery & grandfathered
  concepts, containment & remote path guards, monotonic taint propagation, and /feedback.
- CRITICAL-06, P11::MAJ-01..03, MINOR-02: Startup abandoned job recovery
  (JOB_ABANDONED_SERVER_CRASH) and real StudioJobRunners wiring.
"""

from __future__ import annotations

import io
import json
import time
from pathlib import Path

import jwt
import pytest
import yaml
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from opendv_core.models.graph_a import CanonicalBusinessConcept, GraphAOntology
from opendv_core.schema_registry import get_default_schema_registry
from opendv_platform_api.job_supervisor import JobSupervisor
from opendv_platform_api.main import create_app
from opendv_platform_api.routers import ontology_studio as studio_router
from opendv_platform_api.security import studio_auth
from opendv_service import OpenDVSettings
from opendv_service.studio.session_manager import StudioSessionManager

_FIBO_INSTRUMENT_MAPPING = {
    "scheme": "fibo",
    "predicate": "skos:exactMatch",
    "target_uri": (
        "https://spec.edmcouncil.org/fibo/ontology/FBC/"
        "FinancialInstruments/FinancialInstruments/FinancialInstrument"
    ),
    "confidence": 0.94,
}

_FIBO_PARTY_MAPPING = {
    "scheme": "fibo",
    "predicate": "skos:exactMatch",
    "target_uri": (
        "https://spec.edmcouncil.org/fibo/ontology/FND/Agreements/Contracts/ContractParty"
    ),
    "confidence": 0.89,
}


@pytest.fixture(autouse=True)
def isolated_r3a_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Isolate each test in clean studio & workspace roots while keeping schema registry warm."""
    get_default_schema_registry()
    studio_root = tmp_path / "opendv_studio"
    workspace_root = tmp_path / "workspace"
    studio_root.mkdir(parents=True, exist_ok=True)
    workspace_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("OPENDV_STUDIO_ROOT", str(studio_root))
    monkeypatch.setenv("OPENDV_WORKSPACE_ROOT", str(workspace_root))
    monkeypatch.setenv("OPENDV_SERVER_MODE", "local_standalone")
    monkeypatch.delenv("OPENDV_TOKEN", raising=False)
    monkeypatch.delenv("OPENDV_WORKSPACE_ROOTS", raising=False)


@pytest.fixture
def client(tmp_path: Path) -> TestClient:
    """Loopback TestClient bound to localhost:8000."""
    settings = OpenDVSettings.load(
        workspace_root=tmp_path / "workspace",
        state_dir=tmp_path / "state",
        is_server=True,
    )
    app = create_app(settings=settings)
    return TestClient(app, client=("127.0.0.1", 50000), base_url="http://localhost:8000")


@pytest.fixture
def admin_headers(client: TestClient) -> dict[str, str]:
    """Initialize Studio and return admin Bearer headers."""
    resp = client.post(
        "/api/v1/ontology/studio/init",
        json={"domain_namespace": "wealth_mgmt", "enterprise_owner": "Global Wealth"},
    )
    assert resp.status_code == 200, resp.text
    token = resp.json()["local_token"]
    return {
        "Authorization": f"Bearer {token}",
        "X-OpenDV-Role": "admin",
        "X-OpenDV-Principal": "user:admin_alice",
    }


def _role_headers(base_headers: dict[str, str], role: str, principal: str) -> dict[str, str]:
    return {
        "Authorization": base_headers["Authorization"],
        "X-OpenDV-Role": role,
        "X-OpenDV-Principal": principal,
    }


def test_four_tier_rbac_viewer_and_automated_perimeter(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    """Verify viewer read-only perimeter, automated allow-list, and confidential taint guard."""
    viewer_hdrs = _role_headers(admin_headers, "viewer", "user:viewer_1")
    auto_hdrs = _role_headers(admin_headers, "automated", "agent:scout_bot")

    # 1. Viewer cannot create sessions
    assert (
        client.post(
            "/api/v1/ontology/studio/sessions",
            json={"session_id": "s-viewer-deny", "domain_namespace": "wealth_mgmt"},
            headers=viewer_hdrs,
        ).status_code
        == 403
    )

    # 2. Automated caller CAN create session, upload source, parse, and construct
    create_resp = client.post(
        "/api/v1/ontology/studio/sessions",
        json={"session_id": "s-rbac-01", "domain_namespace": "wealth_mgmt"},
        headers=auto_hdrs,
    )
    assert create_resp.status_code == 201, create_resp.text
    head_sha = create_resp.json()["head_sha256"]

    # Viewer CAN read sessions and call POST /validate, but CANNOT upload/mutate/export bundle
    assert (
        client.get("/api/v1/ontology/studio/sessions/s-rbac-01", headers=viewer_hdrs).status_code
        == 200
    )
    assert (
        client.post(
            "/api/v1/ontology/studio/sessions/s-rbac-01/validate",
            json={"operations": []},
            headers=viewer_hdrs,
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/v1/ontology/studio/sessions/s-rbac-01/bundle",
            json={},
            headers=viewer_hdrs,
        ).status_code
        == 403
    )

    # 3. Upload a confidential source (do_not_send_to_agent=True) -> monotonic session taint
    up_conf = client.post(
        "/api/v1/ontology/studio/sessions/s-rbac-01/sources",
        files={
            "file": ("mnpi_deals.csv", io.BytesIO(b"deal_id,secret\nD1,top_secret\n"), "text/csv")
        },
        data={"do_not_send_to_agent": "true"},
        headers=admin_headers,
    )
    assert up_conf.status_code == 201
    conf_source_id = up_conf.json()["source_id"]

    # Automated caller is blocked from reading confidential source content (P10::MAJ-01)
    agent_src_resp = client.get(
        f"/api/v1/ontology/studio/sessions/s-rbac-01/sources/{conf_source_id}/content",
        headers=auto_hdrs,
    )
    assert agent_src_resp.status_code == 403
    assert agent_src_resp.json()["error_code"] == "CONFIDENTIAL_SOURCE_AGENT_ACCESS_FORBIDDEN"

    # Human viewer CAN read source content
    viewer_src_resp = client.get(
        f"/api/v1/ontology/studio/sessions/s-rbac-01/sources/{conf_source_id}/content",
        headers=viewer_hdrs,
    )
    assert viewer_src_resp.status_code == 200

    # 4. Automated caller can create additive proposal, but destructive op is rejected (403)
    ok_prop = client.post(
        "/api/v1/ontology/studio/sessions/s-rbac-01/proposals",
        json={
            "operations": [
                {
                    "op_type": "add_concept",
                    "op_id": "op-agent-1",
                    "concept_uri": "urn:ontology:entity:instrument",
                    "concept_name": "FinancialInstrument",
                    "definition": "Tradeable contract.",
                    "external_mappings": [_FIBO_INSTRUMENT_MAPPING],
                }
            ],
            "rationale": "Additive proposal from agent",
        },
        headers=auto_hdrs,
    )
    assert ok_prop.status_code == 201
    prop_id = ok_prop.json()["proposal_id"]

    bad_prop = client.post(
        "/api/v1/ontology/studio/sessions/s-rbac-01/proposals",
        json={
            "operations": [
                {
                    "op_type": "remove_concept",
                    "op_id": "op-agent-del",
                    "concept_uri": "urn:ontology:entity:instrument",
                    "reason": "Agent attempting destructive delete",
                }
            ],
            "rationale": "Destructive proposal from agent",
        },
        headers=auto_hdrs,
    )
    assert bad_prop.status_code == 403
    assert bad_prop.json()["error_code"] == "AGENT_DESTRUCTIVE_OP_FORBIDDEN"

    # 5. Automated caller rejected across human/admin endpoints with exact catalog codes
    r_accept = client.post(
        f"/api/v1/ontology/studio/sessions/s-rbac-01/proposals/{prop_id}/accept",
        json={"base_sha256": head_sha},
        headers={**auto_hdrs, "If-Match": f'"{head_sha}"'},
    )
    assert r_accept.status_code == 403
    assert r_accept.json()["error_code"] == "AGENT_PROPOSAL_ACCEPT_FORBIDDEN"

    for path, payload in [
        (
            f"/api/v1/ontology/studio/sessions/s-rbac-01/proposals/{prop_id}/reject",
            {"reason": "Agent reject"},
        ),
        (
            f"/api/v1/ontology/studio/sessions/s-rbac-01/proposals/{prop_id}/rebase",
            {"base_sha256": head_sha},
        ),
        ("/api/v1/ontology/studio/sessions/s-rbac-01/clarify", {}),
        (
            "/api/v1/ontology/studio/sessions/s-rbac-01/answers",
            {"answers": {}, "expected_answers_sha256": "0" * 64},
        ),
    ]:
        resp = client.post(path, json=payload, headers={**auto_hdrs, "If-Match": f'"{head_sha}"'})
        assert resp.status_code == 403
        assert resp.json()["error_code"] == "AGENT_OPERATION_FORBIDDEN"

    for admin_path, admin_payload in [
        (
            "/api/v1/ontology/studio/sessions/s-rbac-01/waiver",
            {"concept_uri": "urn:x", "rationale": "Ten chars minimum"},
        ),
        (
            "/api/v1/ontology/studio/sessions/s-rbac-01/promote",
            {
                "head_sha256": head_sha,
                "workspace_sha256": "0" * 64,
                "rationale": "Ten chars minimum",
            },
        ),
    ]:
        resp_adm = client.post(
            admin_path,
            json=admin_payload,
            headers={**auto_hdrs, "If-Match": f'"{head_sha}"'},
        )
        assert resp_adm.status_code == 403
        assert resp_adm.json()["error_code"] == "AGENT_ADMIN_OP_FORBIDDEN"

    del_agent = client.delete("/api/v1/ontology/studio/sessions/s-rbac-01", headers=auto_hdrs)
    assert del_agent.status_code == 403
    assert del_agent.json()["error_code"] == "AGENT_ADMIN_OP_FORBIDDEN"

    # Agent:* identity blocked on auto_commit=True; CI automated (system:*) allowed on deterministic
    agent_autocommit_deny = client.post(
        "/api/v1/ontology/studio/sessions/s-rbac-01/construct",
        json={"mode": "deterministic", "auto_commit": True, "base_sha256": head_sha},
        headers={**auto_hdrs, "If-Match": f'"{head_sha}"'},
    )
    assert agent_autocommit_deny.status_code == 403
    assert agent_autocommit_deny.json()["error_code"] == "AGENT_PROPOSAL_ACCEPT_FORBIDDEN"

    ci_hdrs = _role_headers(admin_headers, "automated", "system:ci_runner")
    det_resp = client.post(
        "/api/v1/ontology/studio/sessions/s-rbac-01/construct",
        json={"mode": "deterministic", "auto_commit": True, "base_sha256": head_sha},
        headers={**ci_hdrs, "If-Match": f'"{head_sha}"'},
    )
    assert det_resp.status_code == 202, det_resp.text


def test_editor_additive_delta_and_accept_proposal_11_step_precedence(
    client: TestClient, admin_headers: dict[str, str]
) -> None:
    """Verify editor additive delta, bundle import RBAC, and accept_proposal 11-step order."""
    editor_hdrs = _role_headers(admin_headers, "editor", "user:editor_bob")

    create_resp = client.post(
        "/api/v1/ontology/studio/sessions",
        json={"session_id": "s-editor-gov", "domain_namespace": "wealth_mgmt"},
        headers=editor_hdrs,
    )
    assert create_resp.status_code == 201
    genesis_sha = create_resp.json()["head_sha256"]

    # 1. Editor CAN commit additive grounded delta directly (CRITICAL-01)
    add_resp = client.post(
        "/api/v1/ontology/studio/sessions/s-editor-gov/delta",
        json={
            "operations": [
                {
                    "op_type": "add_concept",
                    "op_id": "op-ed-1",
                    "concept_uri": "urn:ontology:entity:instrument",
                    "concept_name": "FinancialInstrument",
                    "definition": "Tradeable financial asset.",
                    "external_mappings": [_FIBO_INSTRUMENT_MAPPING],
                }
            ],
            "rationale": "Editor additive FIBO-grounded commit",
        },
        headers={**editor_hdrs, "If-Match": f'"{genesis_sha}"'},
    )
    assert add_resp.status_code == 200, add_resp.text
    v1_sha = add_resp.json()["target_sha256"]

    # 2. Destructive proposal accepted by editor returns 403 FORBIDDEN_ADMIN_REQUIRED (P10::MAJ-02)
    dest_prop = client.post(
        "/api/v1/ontology/studio/sessions/s-editor-gov/proposals",
        json={
            "operations": [
                {
                    "op_type": "remove_concept",
                    "op_id": "op-rem-inst",
                    "concept_uri": "urn:ontology:entity:instrument",
                    "reason": "Breaking destructive removal requires admin",
                }
            ],
            "rationale": "Remove instrument",
        },
        headers=admin_headers,
    )
    assert dest_prop.status_code == 201
    dest_prop_id = dest_prop.json()["proposal_id"]

    ed_accept_dest = client.post(
        f"/api/v1/ontology/studio/sessions/s-editor-gov/proposals/{dest_prop_id}/accept",
        json={"base_sha256": v1_sha},
        headers={**editor_hdrs, "If-Match": f'"{v1_sha}"'},
    )
    assert ed_accept_dest.status_code == 403
    assert ed_accept_dest.json()["error_code"] == "FORBIDDEN_ADMIN_REQUIRED"

    # 3. 11-step precedence: editor passing low_confidence_waiver=True + short rationale ("bad")
    # MUST receive 403 FORBIDDEN_ADMIN_REQUIRED (Step 7) BEFORE 422 INVALID_GROUNDING_WAIVER
    ungrounded_prop = client.post(
        "/api/v1/ontology/studio/sessions/s-editor-gov/proposals",
        json={
            "operations": [
                {
                    "op_type": "add_concept",
                    "op_id": "op-ung-1",
                    "concept_uri": "urn:ontology:entity:custom_alpha",
                    "concept_name": "CustomAlphaSignal",
                    "definition": "Proprietary signal.",
                }
            ],
            "rationale": "Add ungrounded concept",
        },
        headers=admin_headers,
    )
    assert ungrounded_prop.status_code == 201
    ung_prop_id = ungrounded_prop.json()["proposal_id"]

    ed_waiver_short = client.post(
        f"/api/v1/ontology/studio/sessions/s-editor-gov/proposals/{ung_prop_id}/accept",
        json={
            "base_sha256": v1_sha,
            "low_confidence_waiver": True,
            "grounding_rationale": "short",
        },
        headers={**editor_hdrs, "If-Match": f'"{v1_sha}"'},
    )
    assert ed_waiver_short.status_code == 403
    assert ed_waiver_short.json()["error_code"] == "FORBIDDEN_ADMIN_REQUIRED"

    # 4. Editor CAN export/import clean bundle; unverified attribution ack requires admin
    bundle_resp = client.post(
        "/api/v1/ontology/studio/sessions/s-editor-gov/bundle",
        json={},
        headers=editor_hdrs,
    )
    assert bundle_resp.status_code == 200

    imp_ok = client.post(
        "/api/v1/ontology/studio/sessions/bundle/import",
        files={
            "file": (
                "s-editor-gov.odvstudio.zip",
                io.BytesIO(bundle_resp.content),
                "application/zip",
            )
        },
        data={
            "new_session_id": "s-editor-imported",
            "acknowledge_unverified_import_attribution": "false",
        },
        headers=editor_hdrs,
    )
    assert imp_ok.status_code == 201, imp_ok.text
    assert imp_ok.json()["session_id"] == "s-editor-imported"

    imp_admin_req = client.post(
        "/api/v1/ontology/studio/sessions/bundle/import",
        files={
            "file": (
                "s-editor-gov.odvstudio.zip",
                io.BytesIO(bundle_resp.content),
                "application/zip",
            )
        },
        data={
            "new_session_id": "s-editor-imp-deny",
            "acknowledge_unverified_import_attribution": "true",
        },
        headers=editor_hdrs,
    )
    assert imp_admin_req.status_code == 403
    assert imp_admin_req.json()["error_code"] == "FORBIDDEN_ADMIN_REQUIRED"


def test_launch_nonce_role_preservation_csrf_cors_and_spa_routes(
    client: TestClient, admin_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify launch nonce preserves caller role, blocks Origin: null, and serves /ontology."""
    # 1. Viewer mints launch nonce -> exchanged session cookie retains 'viewer' role (CRITICAL-02)
    viewer_hdrs = _role_headers(admin_headers, "viewer", "user:viewer_bob")
    nonce_resp = client.post("/api/v1/ontology/studio/auth/launch-nonce", headers=viewer_hdrs)
    assert nonce_resp.status_code == 200
    viewer_nonce = nonce_resp.json()["launch_nonce"]

    cookie_client = TestClient(
        client.app, client=("127.0.0.1", 50001), base_url="http://localhost:8000"
    )
    tok_resp = cookie_client.post(
        "/api/v1/ontology/studio/auth/token",
        json={"launch_nonce": viewer_nonce},
    )
    assert tok_resp.status_code == 200
    assert tok_resp.json()["status"] == "authenticated"
    assert tok_resp.json()["principal"] == "user:viewer_bob"

    # Viewer cookie cannot create sessions (no escalation to admin!)
    esc_attempt = cookie_client.post(
        "/api/v1/ontology/studio/sessions",
        json={"session_id": "s-escalated", "domain_namespace": "wealth_mgmt"},
        headers={"X-OpenDV-Workbench": "1", "Origin": "http://localhost:8000"},
    )
    assert esc_attempt.status_code == 403
    assert esc_attempt.json()["error_code"] == "SERVER_OPERATION_FORBIDDEN"

    # Remote mode strictly rejects session cookies
    monkeypatch.setenv("OPENDV_SERVER_MODE", "remote_platform")
    remote_cookie_attempt = cookie_client.get("/api/v1/ontology/studio/sessions")
    assert remote_cookie_attempt.status_code == 401
    monkeypatch.setenv("OPENDV_SERVER_MODE", "local_standalone")

    # 2. Origin: null & Sec-Fetch-Site: cross-site rejected with 403 CSRF_ORIGIN_MISMATCH
    null_origin_token = client.post(
        "/api/v1/ontology/studio/auth/token",
        json={"launch_nonce": "unused"},
        headers={"Origin": "null"},
    )
    assert null_origin_token.status_code == 403
    assert null_origin_token.json()["error_code"] == "CSRF_ORIGIN_MISMATCH"

    cross_site_mut = cookie_client.post(
        "/api/v1/ontology/studio/sessions",
        json={"session_id": "s-csrf-cross", "domain_namespace": "wealth_mgmt"},
        headers={
            "X-OpenDV-Workbench": "1",
            "Origin": "http://localhost:8000",
            "Sec-Fetch-Site": "cross-site",
        },
    )
    assert cross_site_mut.status_code == 403
    assert cross_site_mut.json()["error_code"] == "CSRF_ORIGIN_MISMATCH"

    # 3. Bounded nonce store capacity check
    for idx in range(studio_auth.MAX_ACTIVE_NONCES + 20):
        studio_auth.mint_launch_nonce(principal=f"user:u_{idx}", role="viewer")
    assert len(studio_auth._LAUNCH_NONCES) <= studio_auth.MAX_ACTIVE_NONCES

    # 4. /ontology and /ontology/{subpath} SPA routes return HTML + Referrer-Policy: no-referrer
    for spa_path in ("/ontology", "/ontology/sessions/s-demo"):
        spa_resp = client.get(spa_path)
        assert spa_resp.status_code == 200
        assert "text/html" in spa_resp.headers["content-type"]
        assert spa_resp.headers.get("referrer-policy") == "no-referrer"

    # 5. CORS preflight exposes PUT/PATCH/DELETE + studio headers + credentials for loopback origin
    cors_resp = client.options(
        "/api/v1/ontology/studio/sessions",
        headers={
            "Origin": "http://localhost:5173",
            "Access-Control-Request-Method": "DELETE",
            "Access-Control-Request-Headers": (
                "Authorization,If-Match,X-OpenDV-Workbench,X-OpenDV-Workspace-If-Match"
            ),
        },
    )
    assert cors_resp.status_code == 200
    assert cors_resp.headers.get("access-control-allow-credentials") == "true"
    allow_methods = cors_resp.headers.get("access-control-allow-methods", "")
    for method in ("PUT", "PATCH", "DELETE"):
        assert method in allow_methods


def test_streaming_upload_cap_returns_413(
    client: TestClient, admin_headers: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify streaming multipart upload cap rejects oversized uploads with 413."""
    monkeypatch.setattr(studio_router, "MAX_SOURCE_UPLOAD_BYTES", 1024)
    client.post(
        "/api/v1/ontology/studio/sessions",
        json={"session_id": "s-upload-cap", "domain_namespace": "wealth_mgmt"},
        headers=admin_headers,
    )
    oversized = b"x" * 2048
    resp = client.post(
        "/api/v1/ontology/studio/sessions/s-upload-cap/sources",
        files={"file": ("big.csv", io.BytesIO(oversized), "text/csv")},
        headers=admin_headers,
    )
    assert resp.status_code == 413
    assert resp.json()["error_code"] == "PAYLOAD_TOO_LARGE"


def test_signed_audit_chain_continuity_semver_rebase_and_feedback(
    client: TestClient, admin_headers: dict[str, str], tmp_path: Path
) -> None:
    """Verify signed audit chain across waiver/accept/promote/delta (>1.1.0), rebase & feedback."""
    session_id = "s-audit-semver"
    create_resp = client.post(
        "/api/v1/ontology/studio/sessions",
        json={"session_id": session_id, "domain_namespace": "wealth_mgmt"},
        headers=admin_headers,
    )
    assert create_resp.status_code == 201
    sha_0 = create_resp.json()["head_sha256"]

    # Weak ETag W/"..." is rejected with 400 INVALID_PRECONDITION_HEADER
    weak_etag = client.post(
        f"/api/v1/ontology/studio/sessions/{session_id}/delta",
        json={"operations": []},
        headers={**admin_headers, "If-Match": f'W/"{sha_0}"'},
    )
    assert weak_etag.status_code == 400
    assert weak_etag.json()["error_code"] == "INVALID_PRECONDITION_HEADER"

    # Submit pre-authorized steward waiver (signed into audit.jsonl)
    waiver_resp = client.post(
        f"/api/v1/ontology/studio/sessions/{session_id}/waiver",
        json={
            "target_concept_uris": ["urn:ontology:entity:alpha_factor"],
            "rationale": "Steward waiver for proprietary alpha factor concept.",
        },
        headers=admin_headers,
    )
    assert waiver_resp.status_code == 200, waiver_resp.text

    # Proposer (editor) creates proposal for alpha_factor; admin accepts using stored waiver!
    editor_hdrs = _role_headers(admin_headers, "editor", "user:editor_proposer")
    prop1 = client.post(
        f"/api/v1/ontology/studio/sessions/{session_id}/proposals",
        json={
            "operations": [
                {
                    "op_type": "add_concept",
                    "op_id": "op-alpha-1",
                    "concept_uri": "urn:ontology:entity:alpha_factor",
                    "concept_name": "AlphaFactor",
                    "definition": "Quantitative return signal.",
                }
            ],
            "rationale": "Initial alpha factor proposal",
        },
        headers=editor_hdrs,
    )
    assert prop1.status_code == 201
    prop1_id = prop1.json()["proposal_id"]

    acc1 = client.post(
        f"/api/v1/ontology/studio/sessions/{session_id}/proposals/{prop1_id}/accept",
        json={"base_sha256": sha_0},
        headers={**admin_headers, "If-Match": f'"{sha_0}"'},
    )
    assert acc1.status_code == 200, acc1.text
    sha_1_0 = acc1.json()["target_sha256"]
    assert acc1.json()["target_version"] == "1.0.0"

    # Promote v1.0.0 to workspace (signed into audit.jsonl)
    promo = client.post(
        f"/api/v1/ontology/studio/sessions/{session_id}/promote",
        json={
            "head_sha256": sha_1_0,
            "workspace_sha256": "0" * 64,
            "rationale": "Promote v1.0.0 alpha ontology to workspace.",
        },
        headers={
            **admin_headers,
            "If-Match": f'"{sha_1_0}"',
            "X-OpenDV-Workspace-If-Match": f'"{"0" * 64}"',
        },
    )
    assert promo.status_code == 200, promo.text

    # Reject wrong explicit target_version on /delta (P10::MAJ-03)
    bad_semver = client.post(
        f"/api/v1/ontology/studio/sessions/{session_id}/delta",
        json={
            "target_version": "2.0.0",
            "operations": [
                {
                    "op_type": "add_concept",
                    "op_id": "op-inst",
                    "concept_uri": "urn:ontology:entity:instrument",
                    "concept_name": "FinancialInstrument",
                    "definition": "Financial instrument.",
                    "external_mappings": [_FIBO_INSTRUMENT_MAPPING],
                }
            ],
        },
        headers={**admin_headers, "If-Match": f'"{sha_1_0}"'},
    )
    assert bad_semver.status_code == 422
    assert bad_semver.json()["error_code"] == "ONT_EVO_CONTRACT_VIOLATION"

    # Advance to v1.1.0 then v1.2.0 via proposal (MAJOR-08: >1.1.0 SemVer & intact audit chain!)
    d_1_1 = client.post(
        f"/api/v1/ontology/studio/sessions/{session_id}/delta",
        json={
            "operations": [
                {
                    "op_type": "add_concept",
                    "op_id": "op-inst",
                    "concept_uri": "urn:ontology:entity:instrument",
                    "concept_name": "FinancialInstrument",
                    "definition": "Financial instrument.",
                    "external_mappings": [_FIBO_INSTRUMENT_MAPPING],
                }
            ],
        },
        headers={**admin_headers, "If-Match": f'"{sha_1_0}"'},
    )
    assert d_1_1.status_code == 200, d_1_1.text
    sha_1_1 = d_1_1.json()["target_sha256"]
    assert d_1_1.json()["target_version"] == "1.1.0"

    prop_1_2 = client.post(
        f"/api/v1/ontology/studio/sessions/{session_id}/proposals",
        json={
            "operations": [
                {
                    "op_type": "add_concept",
                    "op_id": "op-party",
                    "concept_uri": "urn:ontology:entity:party",
                    "concept_name": "ContractParty",
                    "definition": "Contract party.",
                    "external_mappings": [_FIBO_PARTY_MAPPING],
                }
            ],
            "rationale": "Advance from 1.1.0 to 1.2.0 via proposal",
        },
        headers=editor_hdrs,
    )
    assert prop_1_2.status_code == 201, prop_1_2.text
    prop_1_2_id = prop_1_2.json()["proposal_id"]
    acc_1_2 = client.post(
        f"/api/v1/ontology/studio/sessions/{session_id}/proposals/{prop_1_2_id}/accept",
        json={"base_sha256": sha_1_1},
        headers={**admin_headers, "If-Match": f'"{sha_1_1}"'},
    )
    assert acc_1_2.status_code == 200, acc_1_2.text
    sha_1_2 = acc_1_2.json()["target_sha256"]
    assert acc_1_2.json()["target_version"] == "1.2.0"

    # Rebase conflict detection via check_rebase_conflicts (P10::MAJ-04):
    # Proposal mutates instrument on sha_1_2 while head deprecates it -> 409 conflict
    stale_prop = client.post(
        f"/api/v1/ontology/studio/sessions/{session_id}/proposals",
        json={
            "base_sha256": sha_1_2,
            "operations": [
                {
                    "op_type": "add_attribute",
                    "op_id": "op-attr-isin",
                    "concept_uri": "urn:ontology:entity:instrument",
                    "attribute_name": "isin",
                    "data_type": "STRING",
                }
            ],
            "rationale": "Add ISIN to instrument",
        },
        headers=editor_hdrs,
    )
    assert stale_prop.status_code == 201
    stale_prop_id = stale_prop.json()["proposal_id"]

    dep_head = client.post(
        f"/api/v1/ontology/studio/sessions/{session_id}/delta",
        json={
            "operations": [
                {
                    "op_type": "deprecate_concept",
                    "op_id": "op-dep-inst-2",
                    "concept_uri": "urn:ontology:entity:instrument",
                    "reason": "Instrument deprecated on head",
                }
            ],
        },
        headers={**admin_headers, "If-Match": f'"{sha_1_2}"'},
    )
    assert dep_head.status_code == 200, dep_head.text
    sha_2_0 = dep_head.json()["target_sha256"]

    rebase_conflict = client.post(
        f"/api/v1/ontology/studio/sessions/{session_id}/proposals/{stale_prop_id}/rebase",
        json={"base_sha256": sha_2_0},
        headers={**admin_headers, "If-Match": f'"{sha_2_0}"'},
    )
    assert rebase_conflict.status_code == 409
    assert rebase_conflict.json()["error_code"] == "PROPOSAL_STALE_REBASE_REQUIRED"

    # Feedback endpoint persists to feedback.jsonl (and rejects read-only viewer)
    viewer_hdrs = _role_headers(admin_headers, "viewer", "user:viewer_audit")
    assert (
        client.post(
            f"/api/v1/ontology/studio/sessions/{session_id}/feedback",
            json={"rating": "up", "comment": "Viewer should not mutate"},
            headers=viewer_hdrs,
        ).status_code
        == 403
    )
    fb_resp = client.post(
        f"/api/v1/ontology/studio/sessions/{session_id}/feedback",
        json={
            "concept_uri": "urn:ontology:entity:party",
            "rating": "up",
            "comment": "FIBO ContractParty mapping verified by domain steward.",
        },
        headers=editor_hdrs,
    )
    assert fb_resp.status_code == 201, fb_resp.text
    fb_file = tmp_path / "opendv_studio" / session_id / "feedback.jsonl"
    assert fb_file.is_file()
    assert "urn:ontology:entity:party" in fb_file.read_text(encoding="utf-8")


def test_from_workspace_discovery_grandfathered_concepts_and_containment(
    client: TestClient,
    admin_headers: dict[str, str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify {ns}_graph_a_ontology.yaml discovery, grandfathered concepts & containment."""
    ws = tmp_path / "workspace"
    seeded_ont = GraphAOntology(
        ontology_version="1.0.0",
        domain_namespace="wealth_mgmt",
        enterprise_owner="Global Wealth",
        canonical_business_concepts=[
            CanonicalBusinessConcept(
                concept_uri="urn:ontology:entity:legacy_ungrounded",
                concept_name="LegacyUngrounded",
                target_hub="hub_legacy",
            )
        ],
    )
    (ws / "wealth_mgmt_graph_a_ontology.yaml").write_text(
        yaml.safe_dump(seeded_ont.model_dump(mode="json"), sort_keys=True),
        encoding="utf-8",
    )

    seeded_resp = client.post(
        "/api/v1/ontology/studio/sessions",
        json={
            "session_id": "s-seeded-ws",
            "domain_namespace": "wealth_mgmt",
            "from_workspace": str(ws),
        },
        headers=admin_headers,
    )
    assert seeded_resp.status_code == 201, seeded_resp.text
    detail = client.get(
        "/api/v1/ontology/studio/sessions/s-seeded-ws", headers=admin_headers
    ).json()
    assert "urn:ontology:entity:legacy_ungrounded" in detail["grandfathered_concept_uris"]

    # Adding a grounded concept succeeds without waiver for untouched grandfathered concept!
    head_sha = detail["head_sha256"]
    delta_ok = client.post(
        "/api/v1/ontology/studio/sessions/s-seeded-ws/delta",
        json={
            "operations": [
                {
                    "op_type": "add_concept",
                    "op_id": "op-new-grounded",
                    "concept_uri": "urn:ontology:entity:instrument",
                    "concept_name": "FinancialInstrument",
                    "definition": "Grounded financial instrument.",
                    "external_mappings": [_FIBO_INSTRUMENT_MAPPING],
                }
            ],
        },
        headers={**admin_headers, "If-Match": f'"{head_sha}"'},
    )
    assert delta_ok.status_code == 200, delta_ok.text

    # Workspace containment blocks escaping outside OPENDV_WORKSPACE_ROOTS
    outside_dir = tmp_path / "outside_secret_dir"
    outside_dir.mkdir(parents=True, exist_ok=True)
    escape_resp = client.post(
        "/api/v1/ontology/studio/sessions",
        json={
            "session_id": "s-escape",
            "domain_namespace": "wealth_mgmt",
            "from_workspace": str(outside_dir),
        },
        headers=admin_headers,
    )
    assert escape_resp.status_code in (400, 403)

    # Remote mode blocks server-local from_workspace path with REMOTE_SERVER_PATH_FORBIDDEN
    priv_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    pub_pem = (
        priv_key.public_key()
        .public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        .decode("utf-8")
    )
    now = int(time.time())
    jwt_tok = jwt.encode(
        {
            "sub": "user:remote_admin",
            "role": "admin",
            "iss": "opendv-platform",
            "aud": "opendv-api",
            "iat": now,
            "exp": now + 3600,
        },
        priv_key,
        algorithm="RS256",
    )
    monkeypatch.setenv("OPENDV_SERVER_MODE", "remote_platform")
    monkeypatch.setenv("OPENDV_JWT_PUBLIC_KEY", pub_pem)
    remote_resp = client.post(
        "/api/v1/ontology/studio/sessions",
        json={
            "session_id": "s-remote-ws",
            "domain_namespace": "wealth_mgmt",
            "from_workspace": str(ws),
        },
        headers={"Authorization": f"Bearer {jwt_tok}"},
    )
    assert remote_resp.status_code == 400
    assert remote_resp.json()["error_code"] == "REMOTE_SERVER_PATH_FORBIDDEN"


def test_startup_recover_abandoned_jobs_marks_crash_state(tmp_path: Path) -> None:
    """Verify recover_abandoned_jobs marks orphaned running jobs JOB_ABANDONED_SERVER_CRASH."""
    studio_root = tmp_path / "opendv_studio"
    mgr = StudioSessionManager(studio_root=studio_root)
    mgr.create_session("s-crash-job", domain_namespace="wealth_mgmt")
    jobs_dir = mgr.get_session_dir("s-crash-job") / "jobs"
    jobs_dir.mkdir(parents=True, exist_ok=True)

    job_id = "job-orphan000001"
    (jobs_dir / f"{job_id}.json").write_text(
        json.dumps(
            {
                "job_id": job_id,
                "session_id": "s-crash-job",
                "job_type": "construct",
                "status": "running",
                "stage": "extracting",
                "pct_complete": 40,
                "created_at": "2026-11-12T00:00:00Z",
                "finished_at": None,
            }
        ),
        encoding="utf-8",
    )

    sup = JobSupervisor()
    recovered = sup.recover_abandoned_jobs(studio_root)
    assert recovered == 1

    persisted = json.loads((jobs_dir / f"{job_id}.json").read_text(encoding="utf-8"))
    assert persisted["status"] == "failed"
    assert persisted["error"]["error_code"] == "JOB_ABANDONED_SERVER_CRASH"
