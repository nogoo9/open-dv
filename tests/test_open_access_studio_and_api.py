"""Phase 1 TDD Tests: Loopback Zero-Auth Open Access, Waiver Simplification,
Preserved Security Guards, and Audit #4/#6 RBAC & Default Provider Hardening.

Maps to requirements:
- FR-AUTH-01: Loopback zero-token open access (no .local_token, #token=, X-OpenDV-Workbench)
- FR-AUTH-02: Remove PROPOSAL_SELF_ACCEPTANCE_FORBIDDEN & K_waiver HMAC barriers; preserve 200 shims
- FR-AUTH-03: Preserve Sec-Fetch-Site, Origin: null, DNS-rebinding Host/Origin/Referer, non-loopback, RS256 JWT
- Audit #4: routers/platform_config.py enforces Depends(get_rbac_role)
- Audit #6: Async runs/pipelines propagate caller RBAC role, guard /abort, default provider from config
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from starlette.testclient import TestClient

from opendv_platform_api import PlatformStateContainer, RbacRole, RunStatus, create_app
from opendv_service import OpenDVSettings
from opendv_testkit import deterministic_pipeline_seam, seed_canonical_pe_demo

_TEST_RSA_PRIVATE_KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
_TEST_RSA_PUBLIC_KEY_PEM = (
    _TEST_RSA_PRIVATE_KEY.public_key()
    .public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    .decode("utf-8")
)


def _mint_test_jwt(
    subject: str = "user:alice",
    role: str = "admin",
    expires_in: float = 3600.0,
    issuer: str = "opendv-platform",
    audience: str = "opendv-api",
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
    return jwt.encode(payload, _TEST_RSA_PRIVATE_KEY, algorithm="RS256")


@pytest.fixture(autouse=True)
def isolated_open_access_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Isolate each test in a fresh workspace and studio root with no legacy tokens."""
    from opendv_core.schema_registry import get_default_schema_registry

    get_default_schema_registry()
    studio_root = tmp_path / "opendv_studio"
    studio_root.mkdir(parents=True, exist_ok=True)
    ws_root = tmp_path / "workspace"
    ws_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("OPENDV_STUDIO_ROOT", str(studio_root))
    monkeypatch.setenv("OPENDV_WORKSPACE_ROOT", str(ws_root))
    monkeypatch.setenv("OPENDV_SERVER_MODE", "local_standalone")
    for env_key in (
        "OPENDV_TOKEN",
        "OPENDV_STUDIO_TOKEN",
        "OPENDV_WAIVER_KEY",
        "OPENDV_JWT_PUBLIC_KEY",
        "OPENDV_JWT_ISSUER",
        "OPENDV_JWT_AUDIENCE",
    ):
        monkeypatch.delenv(env_key, raising=False)
    return studio_root


def _build_local_client(
    tmp_path: Path,
    *,
    workspace_root: Path | None = None,
    state: PlatformStateContainer | None = None,
) -> TestClient:
    ws = workspace_root or (tmp_path / "workspace")
    ws.mkdir(parents=True, exist_ok=True)
    if state is not None:
        app = create_app(platform_state=state, workspace_root=ws)
    else:
        settings = OpenDVSettings.load(
            workspace_root=ws,
            state_dir=tmp_path / "state",
            is_server=True,
        )
        app = create_app(settings=settings, workspace_root=ws, storage_root=tmp_path / "state")
    return TestClient(app, client=("127.0.0.1", 50000), base_url="http://localhost:8000")


# ===========================================================================
# 1. Zero-Token, Zero-CSRF, Zero-Waiver-Key Loopback Open Access (FR-AUTH-01..02)
# ===========================================================================


def test_open_access_loopback_studio_lifecycle_zero_token_zero_csrf(
    tmp_path: Path, isolated_open_access_env: Path
) -> None:
    """All Studio routes succeed on loopback with zero /init, zero token, zero CSRF/waiver headers."""
    studio_root = isolated_open_access_env
    client = _build_local_client(tmp_path)
    sid = "open-access-studio-01"

    # 1. List & create session without /init, Authorization, or X-OpenDV-Workbench
    list_resp = client.get("/api/v1/ontology/studio/sessions")
    assert list_resp.status_code == 200, list_resp.text
    assert list_resp.headers.get("X-OpenDV-Principal") == "human:local_user"
    assert list_resp.headers.get("X-OpenDV-Role") == "admin"

    create_resp = client.post(
        "/api/v1/ontology/studio/sessions",
        json={"session_id": sid, "domain_namespace": "wealth_mgmt"},
    )
    assert create_resp.status_code == 201, create_resp.text
    assert create_resp.headers.get("X-OpenDV-Principal") == "human:local_user"
    assert create_resp.headers.get("X-OpenDV-Role") == "admin"
    assert not (studio_root / ".local_token").exists()
    head_sha = create_resp.json()["head_sha256"]

    # 2. Validate dry-run with low_confidence_waiver=True and no X-OpenDV-Waiver-Key
    val_resp = client.post(
        f"/api/v1/ontology/studio/sessions/{sid}/validate",
        json={
            "delta_id": "d-val-open-1",
            "parent_sha256": "",
            "base_version": "0.0.0",
            "target_version": "1.0.0",
            "operations": [
                {
                    "op_type": "add_concept",
                    "op_id": "op-val-1",
                    "concept_uri": "urn:ontology:entity:ungrounded_tranche",
                    "concept_name": "Ungrounded Tranche Z99",
                    "definition": "Custom local tranche concept.",
                }
            ],
            "low_confidence_waiver": True,
            "grounding_rationale": "Local custodian approved custom concept without K_waiver.",
        },
    )
    assert val_resp.status_code == 200, val_resp.text
    assert val_resp.json()["valid"] is True

    # 3. Proposal create & self-accept (no self_acceptance_override, no X-OpenDV-Waiver-Key)
    prop_resp = client.post(
        f"/api/v1/ontology/studio/sessions/{sid}/proposals",
        json={
            "proposal_id": "prop-open-1",
            "base_sha256": head_sha,
            "operations": [
                {
                    "op_type": "add_concept",
                    "op_id": "op-prop-1",
                    "concept_uri": "urn:ontology:entity:custom_sleeve",
                    "concept_name": "Custom Sleeve X42",
                    "definition": "Custom local sleeve concept.",
                }
            ],
            "rationale": "Add custom sleeve concept.",
        },
    )
    assert prop_resp.status_code == 201, prop_resp.text

    accept_resp = client.post(
        f"/api/v1/ontology/studio/sessions/{sid}/proposals/prop-open-1/accept",
        json={
            "base_sha256": head_sha,
            "low_confidence_waiver": True,
            "grounding_rationale": "Local custodian self-accept and waiver without K_waiver.",
        },
        headers={"If-Match": f'"{head_sha}"'},
    )
    assert accept_resp.status_code == 200, accept_resp.text
    head_sha = accept_resp.json()["target_sha256"]

    # 4. Direct /delta commit with low_confidence_waiver=True and no X-OpenDV-Waiver-Key
    delta_resp = client.post(
        f"/api/v1/ontology/studio/sessions/{sid}/delta",
        json={
            "delta_id": "d-open-2",
            "parent_sha256": head_sha,
            "base_version": "1.0.0",
            "target_version": "1.1.0",
            "operations": [
                {
                    "op_type": "add_concept",
                    "op_id": "op-delta-2",
                    "concept_uri": "urn:ontology:entity:bespoke_note",
                    "concept_name": "Bespoke Note Q77",
                    "definition": "Custom local bespoke note.",
                }
            ],
            "low_confidence_waiver": True,
            "grounding_rationale": "Direct delta waiver approved on loopback without K_waiver.",
        },
        headers={"If-Match": f'"{head_sha}"'},
    )
    assert delta_resp.status_code == 200, delta_resp.text
    head_sha = delta_resp.json()["target_sha256"]

    # 5. Standalone /waiver without X-OpenDV-Waiver-Key
    waiver_resp = client.post(
        f"/api/v1/ontology/studio/sessions/{sid}/waiver",
        json={
            "target_concept_uris": ["urn:ontology:entity:custom_side_pocket"],
            "rationale": "Standalone steward waiver without K_waiver header.",
        },
    )
    assert waiver_resp.status_code == 200, waiver_resp.text

    # 6. Staged candidate Path B promote without X-OpenDV-Waiver-Key
    ev_dir = studio_root / sid / "evidence"
    ev_dir.mkdir(parents=True, exist_ok=True)
    (ev_dir / "staged_candidates.json").write_text(
        json.dumps(
            {
                "staged_candidates": [
                    {
                        "candidate_id": "cand-open-1",
                        "concept_name": "PrivateCreditFacility",
                        "suggested_hub": "hub_private_credit_facility",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    promo_resp = client.post(
        f"/api/v1/ontology/studio/sessions/{sid}/staged-candidates/cand-open-1/promote",
        json={
            "path": "path_b",
            "waiver_rationale": "Promoted via Path B without X-OpenDV-Waiver-Key.",
        },
    )
    assert promo_resp.status_code == 200, promo_resp.text

    # 7. Bundle export & delete without token or X-OpenDV-Workbench
    bundle_resp = client.post(
        f"/api/v1/ontology/studio/sessions/{sid}/bundle",
        json={},
    )
    assert bundle_resp.status_code == 200, bundle_resp.text
    assert bundle_resp.headers["content-type"] == "application/zip"

    del_resp = client.delete(f"/api/v1/ontology/studio/sessions/{sid}")
    assert del_resp.status_code == 204, del_resp.text
    assert not (studio_root / ".local_token").exists()


def test_open_access_synthesizes_human_local_user_and_privileged_custodian_default(
    tmp_path: Path, repo_root: Path
) -> None:
    """Loopback requests synthesize human:local_user (admin) & RbacRole.PRIVILEGED_CUSTODIAN."""
    state = PlatformStateContainer(state_dir=tmp_path / "state", workspace_root=repo_root)
    seed_canonical_pe_demo(state)
    client = _build_local_client(tmp_path, workspace_root=repo_root, state=state)

    # Studio identity synthesis
    studio_resp = client.get("/api/v1/ontology/studio/sessions")
    assert studio_resp.status_code == 200, studio_resp.text
    assert studio_resp.headers.get("X-OpenDV-Principal") == "human:local_user"
    assert studio_resp.headers.get("X-OpenDV-Role") == "admin"

    # Platform RBAC default on loopback (GET /report & GET /events without X-OpenDV-Role)
    rep_resp = client.get("/api/v1/runs/run-pe-apex-horizon-20261030/report")
    assert rep_resp.status_code == 200, rep_resp.text
    events = rep_resp.json()["observability_events"]
    assert "245000000" in json.dumps(events)
    assert not events[0]["layer1_story_banner"]["headline"].startswith("[REDACTED")

    sse_resp = client.get("/api/v1/runs/run-pe-apex-horizon-20261030/events")
    assert sse_resp.status_code == 200, sse_resp.text
    assert sse_resp.headers.get("X-OpenDV-Effective-Role") == RbacRole.PRIVILEGED_CUSTODIAN.value

    # Explicit X-OpenDV-Role: PUBLIC still redacts report & events
    pub_rep = client.get(
        "/api/v1/runs/run-pe-apex-horizon-20261030/report",
        headers={"X-OpenDV-Role": "PUBLIC"},
    )
    assert pub_rep.status_code == 200
    pub_events = pub_rep.json()["observability_events"]
    assert "245000000" not in json.dumps(pub_events)
    assert pub_events[0]["layer1_story_banner"]["headline"].startswith("[REDACTED")


def test_loopback_tolerates_stale_bearer_token_and_session_cookie_as_noops(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Stale/legacy Authorization Bearer tokens, OPENDV_TOKEN, and opendv_session cookies are no-ops."""
    client = _build_local_client(tmp_path)
    monkeypatch.setenv("OPENDV_TOKEN", "stale-env-token-xyz")

    resp_bearer = client.get(
        "/api/v1/ontology/studio/sessions",
        headers={"Authorization": "Bearer completely-stale-legacy-token"},
    )
    assert resp_bearer.status_code == 200, resp_bearer.text
    assert resp_bearer.headers.get("X-OpenDV-Principal") == "human:local_user"

    # Stale cookie without X-OpenDV-Workbench header is harmlessly tolerated on loopback
    cookie_client = TestClient(client.app, client=("127.0.0.1", 50001), base_url="http://localhost:8000")
    cookie_client.cookies.set("opendv_session", "stale-expired-cookie-value")
    resp_cookie = cookie_client.post(
        "/api/v1/ontology/studio/sessions",
        json={"session_id": "stale-cookie-ok", "domain_namespace": "wealth_mgmt"},
    )
    assert resp_cookie.status_code == 201, resp_cookie.text
    assert resp_cookie.headers.get("X-OpenDV-Principal") == "human:local_user"


def test_proposal_self_acceptance_allowed_without_override_flag(tmp_path: Path) -> None:
    """human:local_user can create and accept their own proposal without self_acceptance_override."""
    client = _build_local_client(tmp_path)
    sid = "self-accept-open"
    create_resp = client.post(
        "/api/v1/ontology/studio/sessions",
        json={"session_id": sid, "domain_namespace": "wealth_mgmt"},
    )
    assert create_resp.status_code == 201, create_resp.text
    head_sha = create_resp.json()["head_sha256"]

    prop_resp = client.post(
        f"/api/v1/ontology/studio/sessions/{sid}/proposals",
        json={
            "proposal_id": "prop-self-1",
            "base_sha256": head_sha,
            "operations": [
                {
                    "op_type": "add_concept",
                    "op_id": "op-self-1",
                    "concept_uri": "urn:ontology:entity:self_accept_item",
                    "concept_name": "Self Accept Custom Item",
                    "definition": "Concept proposed and accepted by local user.",
                }
            ],
            "rationale": "Verify same local principal can self-accept without override.",
        },
    )
    assert prop_resp.status_code == 201, prop_resp.text

    accept_resp = client.post(
        f"/api/v1/ontology/studio/sessions/{sid}/proposals/prop-self-1/accept",
        json={
            "base_sha256": head_sha,
            "low_confidence_waiver": True,
            "grounding_rationale": "Steward accepted own proposal on local workstation.",
        },
        headers={"If-Match": f'"{head_sha}"'},
    )
    assert accept_resp.status_code == 200, accept_resp.text
    assert accept_resp.json()["applied_operations_count"] == 1


def test_studio_compat_shims_init_launch_nonce_and_token_200_on_loopback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """/init, /auth/launch-nonce, and /auth/token remain 200 OK compatibility shims on loopback."""
    client = _build_local_client(tmp_path)
    monkeypatch.setenv("OPENDV_TOKEN", "legacy-pinned-token")

    init_1 = client.post(
        "/api/v1/ontology/studio/init",
        json={"domain_namespace": "wealth_mgmt", "enterprise_owner": "Global Wealth"},
    )
    assert init_1.status_code == 200, init_1.text

    # Re-calling /init without force or with stale Bearer remains benign 200 OK shim
    init_2 = client.post(
        "/api/v1/ontology/studio/init",
        json={"domain_namespace": "wealth_mgmt", "force": False},
        headers={"Authorization": "Bearer legacy-stale-token"},
    )
    assert init_2.status_code == 200, init_2.text

    # /auth/launch-nonce works with zero auth headers on loopback
    nonce_resp = client.post("/api/v1/ontology/studio/auth/launch-nonce")
    assert nonce_resp.status_code == 200, nonce_resp.text
    nonce = nonce_resp.json()["launch_nonce"]
    assert isinstance(nonce, str) and len(nonce) > 0

    token_resp = client.post(
        "/api/v1/ontology/studio/auth/token",
        json={"launch_nonce": nonce},
    )
    assert token_resp.status_code == 200, token_resp.text


# ===========================================================================
# 2. Audit #4 & #6: platform_config RBAC, async role propagation, default provider, /abort
# ===========================================================================


def test_audit4_platform_config_rbac_dependency_and_open_access(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Audit #4: routers/platform_config.py enforces Depends(get_rbac_role) consistently."""
    client = _build_local_client(tmp_path)

    # Loopback open-access succeeds without X-OpenDV-Role
    res_get = client.get("/api/v1/platform/config")
    assert res_get.status_code == 200, res_get.text
    assert res_get.json()["workspace_root"] != "[REDACTED]"

    res_val = client.post(
        "/api/v1/platform/config/validate",
        json={"harness": {"provider": "claude-cli"}},
    )
    assert res_val.status_code == 200, res_val.text

    res_put_open = client.put(
        "/api/v1/platform/config",
        json={"harness": {"provider": "claude-cli"}},
    )
    assert res_put_open.status_code == 200, res_put_open.text
    assert res_put_open.json()["harness"]["provider"] == "claude-cli"

    # Explicit PUBLIC / AUDITOR_FINANCE -> 403 on PUT, PUBLIC redacts GET
    for blocked_role in ("PUBLIC", "AUDITOR_FINANCE"):
        res_forbidden = client.put(
            "/api/v1/platform/config",
            headers={"X-OpenDV-Role": blocked_role},
            json={"harness": {"provider": "agy"}},
        )
        assert res_forbidden.status_code == 403, res_forbidden.text

    res_get_pub = client.get("/api/v1/platform/config", headers={"X-OpenDV-Role": "PUBLIC"})
    assert res_get_pub.status_code == 200
    assert res_get_pub.json()["workspace_root"] == "[REDACTED]"

    # In remote_platform mode without auth, get_rbac_role resolves PUBLIC -> redacts GET & blocks PUT
    monkeypatch.setenv("OPENDV_SERVER_MODE", "remote_platform")
    monkeypatch.setenv("OPENDV_JWT_PUBLIC_KEY", _TEST_RSA_PUBLIC_KEY_PEM)
    remote_get = client.get("/api/v1/platform/config")
    assert remote_get.status_code == 200
    assert remote_get.json()["workspace_root"] == "[REDACTED]"

    remote_put = client.put(
        "/api/v1/platform/config",
        json={"harness": {"provider": "agy"}},
    )
    assert remote_put.status_code == 403


def test_audit6_async_runs_propagate_role_default_provider_and_guard_abort(
    tmp_path: Path, repo_root: Path
) -> None:
    """Audit #6: async /runs & /pipelines/runs propagate role, use active provider, guard /abort."""

    class _RoleSpyStateContainer(PlatformStateContainer):
        def __init__(self, *args: Any, **kwargs: Any) -> None:
            super().__init__(*args, **kwargs)
            self.recorded_roles: list[Any] = []

        def execute_agentic_pipeline_run(
            self,
            req: Any,
            role: Any = RbacRole.PUBLIC,
            **kwargs: Any,
        ) -> dict[str, Any]:
            self.recorded_roles.append(role)
            return super().execute_agentic_pipeline_run(req, role=role, **kwargs)

    state = _RoleSpyStateContainer(state_dir=tmp_path / "state", workspace_root=repo_root)
    client = _build_local_client(tmp_path, workspace_root=repo_root, state=state)

    # 1. Create a project and start a non-pipeline run omitting harness_provider -> uses 'agy'
    proj_resp = client.post(
        "/api/v1/projects",
        json={
            "project_id": "proj-audit6-open",
            "display_name": "Audit 6 Open Project",
            "domain_namespace": "wealth_mgmt",
        },
    )
    assert proj_resp.status_code == 201, proj_resp.text

    run_resp = client.post("/api/v1/runs", json={"project_id": "proj-audit6-open"})
    assert run_resp.status_code == 202, run_resp.text
    assert run_resp.json()["harness_provider"] == "agy"
    run_id = run_resp.json()["run_id"]
    run_detail = client.get(f"/api/v1/runs/{run_id}").json()
    assert run_detail["harness_provider"] == "agy"

    # 2. POST /runs/{id}/abort enforces get_rbac_role: PUBLIC -> 403, loopback open-access -> 200
    abort_pub = client.post(
        f"/api/v1/runs/{run_id}/abort",
        headers={"X-OpenDV-Role": "PUBLIC"},
    )
    assert abort_pub.status_code == 403, abort_pub.text

    abort_open = client.post(f"/api/v1/runs/{run_id}/abort")
    assert abort_open.status_code == 200, abort_open.text
    assert abort_open.json()["status"] == RunStatus.ABORTED.value

    # 3. POST /runs with PUBLIC is blocked (403)
    run_pub = client.post(
        "/api/v1/runs",
        headers={"X-OpenDV-Role": "PUBLIC"},
        json={"project_id": "proj-audit6-open"},
    )
    assert run_pub.status_code == 403

    # 4. Update active platform_config provider -> 'claude-cli', then verify async /runs & /pipelines/runs
    cfg_put = client.put(
        "/api/v1/platform/config",
        json={"harness": {"provider": "claude-cli"}},
    )
    assert cfg_put.status_code == 200, cfg_put.text

    recorded_roles = state.recorded_roles

    with deterministic_pipeline_seam():
        sample_input = "examples/pipeline_samples/global_commerce_vault/pipeline_input.yaml"
        sample_harness = "examples/pipeline_samples/global_commerce_vault/harness_config.yaml"

        # 4a. POST /api/v1/pipelines/runs omitting provider_override / harness_provider
        pipe_resp = client.post(
            "/api/v1/pipelines/runs",
            json={
                "input_spec_path": sample_input,
                "harness_config_path": sample_harness,
                "steward_hold_policy": "auto_approve_top_candidate",
                "output_dir": str(tmp_path / "pipe_out_1"),
            },
        )
        assert pipe_resp.status_code == 202, pipe_resp.text
        pipe_run_id = pipe_resp.json()["run_id"]
        pipe_dto = client.get(f"/api/v1/runs/{pipe_run_id}").json()
        assert pipe_dto["harness_provider"] == "claude-cli"

        # Wait briefly for worker thread to invoke spy
        for _ in range(100):
            if len(recorded_roles) >= 1:
                break
            time.sleep(0.05)
        assert recorded_roles, "Worker did not execute pipeline run in time"
        assert str(recorded_roles[0]) == RbacRole.PRIVILEGED_CUSTODIAN.value

        # 4b. POST /api/v1/runs with pipeline_input_path and explicit X-OpenDV-Role: DATA_STEWARD
        runs_pipe_resp = client.post(
            "/api/v1/runs",
            headers={"X-OpenDV-Role": "DATA_STEWARD"},
            json={
                "pipeline_input_path": sample_input,
                "harness_config_path": sample_harness,
            },
        )
        assert runs_pipe_resp.status_code == 202, runs_pipe_resp.text
        runs_pipe_id = runs_pipe_resp.json()["run_id"]
        runs_pipe_dto = client.get(f"/api/v1/runs/{runs_pipe_id}").json()
        assert runs_pipe_dto["harness_provider"] == "claude-cli"

        for _ in range(100):
            if len(recorded_roles) >= 2:
                break
            time.sleep(0.05)
        assert len(recorded_roles) >= 2
        assert str(recorded_roles[1]) == RbacRole.DATA_STEWARD.value


# ===========================================================================
# 3. Preserved Security Guards (FR-AUTH-03)
# ===========================================================================


def test_preserved_security_guards_csrf_dns_rebinding_non_loopback_and_remote_jwt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Preserved perimeter guards: Fetch-Site, Origin:null, DNS rebinding, Referer, socket IP, RS256."""
    client = _build_local_client(tmp_path)

    # 1. Sec-Fetch-Site: cross-site -> 403
    r_cross_site = client.post(
        "/api/v1/ontology/studio/sessions",
        json={"session_id": "guard-cross-site", "domain_namespace": "wealth_mgmt"},
        headers={"Sec-Fetch-Site": "cross-site"},
    )
    assert r_cross_site.status_code == 403

    # 2. Origin: null -> 403 (even without cookie or X-OpenDV-Workbench)
    r_null_origin = client.post(
        "/api/v1/ontology/studio/sessions",
        json={"session_id": "guard-null-origin", "domain_namespace": "wealth_mgmt"},
        headers={"Origin": "null", "Host": "localhost:8000"},
    )
    assert r_null_origin.status_code == 403

    # 3. DNS-rebinding Origin vs Host mismatch -> 403
    r_origin_mismatch = client.post(
        "/api/v1/ontology/studio/sessions",
        json={"session_id": "guard-origin-mismatch", "domain_namespace": "wealth_mgmt"},
        headers={"Origin": "http://evil-attacker.com", "Host": "localhost:8000"},
    )
    assert r_origin_mismatch.status_code == 403

    r_host_rebinding = client.post(
        "/api/v1/ontology/studio/sessions",
        json={"session_id": "guard-host-rebinding", "domain_namespace": "wealth_mgmt"},
        headers={"Host": "attacker.example"},
    )
    assert r_host_rebinding.status_code == 403

    # 4. Malicious Referer when Origin missing -> 403 (even without cookie)
    r_bad_referer = client.post(
        "/api/v1/ontology/studio/sessions",
        json={"session_id": "guard-bad-referer", "domain_namespace": "wealth_mgmt"},
        headers={
            "Referer": "https://malicious-attacker.com/exploit.html",
            "Host": "localhost:8000",
        },
    )
    assert r_bad_referer.status_code == 403

    # 5. Non-loopback client socket (198.51.100.77) in local_standalone mode -> 403
    external_client = TestClient(
        client.app,
        client=("198.51.100.77", 48000),
        base_url="http://localhost:8000",
    )
    ext_headers = {"X-Forwarded-For": "127.0.0.1", "Host": "localhost:8000"}
    assert (
        external_client.post(
            "/api/v1/ontology/studio/init",
            json={"domain_namespace": "ext"},
            headers=ext_headers,
        ).status_code
        == 403
    )
    assert (
        external_client.post(
            "/api/v1/ontology/studio/sessions",
            json={"session_id": "guard-ext-socket", "domain_namespace": "wealth_mgmt"},
            headers=ext_headers,
        ).status_code
        == 403
    )

    # 6. OPENDV_SERVER_MODE=remote_platform enforces RS256 JWT (401) and blocks /init (403)
    monkeypatch.setenv("OPENDV_SERVER_MODE", "remote_platform")
    monkeypatch.setenv("OPENDV_JWT_PUBLIC_KEY", _TEST_RSA_PUBLIC_KEY_PEM)
    monkeypatch.setenv("OPENDV_JWT_ISSUER", "opendv-platform")
    monkeypatch.setenv("OPENDV_JWT_AUDIENCE", "opendv-api")

    r_remote_unauth = client.get("/api/v1/ontology/studio/sessions")
    assert r_remote_unauth.status_code == 401

    r_remote_init = client.post(
        "/api/v1/ontology/studio/init",
        json={"domain_namespace": "remote_test"},
    )
    assert r_remote_init.status_code == 403

    valid_jwt = _mint_test_jwt(subject="user:alice", role="admin")
    r_remote_ok = client.get(
        "/api/v1/ontology/studio/sessions",
        headers={"Authorization": f"Bearer {valid_jwt}"},
    )
    assert r_remote_ok.status_code == 200


# ===========================================================================
# 4. Verification Wave Regression Tests
# ===========================================================================


def test_remote_platform_forged_jwt_rejected_and_remote_unauth_csrf_enforced(
    tmp_path: Path, repo_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Forged/expired/missing-aud JWTs in remote_platform and CSRF in remote_unauth fail closed."""
    state = PlatformStateContainer(state_dir=tmp_path / "state", workspace_root=repo_root)
    seed_canonical_pe_demo(state)
    client = _build_local_client(tmp_path, workspace_root=repo_root, state=state)

    monkeypatch.setenv("OPENDV_SERVER_MODE", "remote_platform")
    monkeypatch.setenv("OPENDV_JWT_PUBLIC_KEY", _TEST_RSA_PUBLIC_KEY_PEM)
    monkeypatch.setenv("OPENDV_JWT_ISSUER", "opendv-platform")
    monkeypatch.setenv("OPENDV_JWT_AUDIENCE", "opendv-api")

    now = int(time.time())
    forged_iss = jwt.encode(
        {"sub": "user:mallory", "role": "viewer", "iss": "evil-issuer"},
        _TEST_RSA_PRIVATE_KEY,
        algorithm="RS256",
    )
    missing_aud = jwt.encode(
        {"sub": "user:mallory", "role": "admin", "iss": "opendv-platform", "exp": now + 3600},
        _TEST_RSA_PRIVATE_KEY,
        algorithm="RS256",
    )
    expired_tok = _mint_test_jwt(subject="user:mallory", role="admin", expires_in=-3600.0)

    for bad_tok in (forged_iss, missing_aud, expired_tok):
        hdrs = {
            "Authorization": f"Bearer {bad_tok}",
            "X-OpenDV-Role": "PRIVILEGED_CUSTODIAN",
        }
        put_cfg = client.put(
            "/api/v1/platform/config",
            headers=hdrs,
            json={"harness": {"provider": "claude-cli"}},
        )
        assert put_cfg.status_code == 403, put_cfg.text

        get_cfg = client.get("/api/v1/platform/config", headers=hdrs)
        assert get_cfg.status_code == 200
        assert get_cfg.json()["workspace_root"] == "[REDACTED]"

        rep = client.get(
            "/api/v1/runs/run-pe-apex-horizon-20261030/report",
            headers=hdrs,
        )
        assert rep.status_code == 200
        events = rep.json()["observability_events"]
        assert "245000000" not in json.dumps(events)
        assert events[0]["layer1_story_banner"]["headline"].startswith("[REDACTED")

    # challenger_m1_2 caveat: --allow-remote-unauthenticated still rejects CSRF/forged Host/Origin
    monkeypatch.setenv("OPENDV_SERVER_MODE", "local_standalone")
    rem_settings = OpenDVSettings.load(
        workspace_root=repo_root,
        state_dir=tmp_path / "rem_state",
        is_server=True,
    ).model_copy(update={"remote_unauthenticated_mode": True})
    rem_app = create_app(
        settings=rem_settings, workspace_root=repo_root, storage_root=tmp_path / "rem_state"
    )
    rem_loopback = TestClient(
        rem_app, client=("127.0.0.1", 50000), base_url="http://localhost:8000"
    )
    for bad_headers in (
        {"Sec-Fetch-Site": "cross-site"},
        {"Origin": "null", "Host": "localhost:8000"},
        {"Origin": "http://evil.example", "Host": "localhost:8000"},
        {"Referer": "https://evil.example/pwn.html", "Host": "localhost:8000"},
        {"Host": "evil.example:8000"},
        {"Host": "evil.example:8000", "Origin": "http://evil.example:8000"},
    ):
        resp = rem_loopback.put(
            "/api/v1/platform/config",
            headers={**bad_headers, "X-OpenDV-Role": "PRIVILEGED_CUSTODIAN"},
            json={"harness": {"provider": "claude-cli"}},
        )
        assert resp.status_code == 403, f"Expected 403 for {bad_headers}, got {resp.status_code}"

    # DNS-rebinding Host: evil.example:8000 from loopback socket must also be 403 on Studio routes
    for rebind_headers in (
        {"Host": "evil.example:8000"},
        {"Host": "evil.example:8000", "Origin": "http://evil.example:8000"},
    ):
        studio_get = rem_loopback.get("/api/v1/ontology/studio/sessions", headers=rebind_headers)
        assert studio_get.status_code == 403, (
            f"Expected 403 on Studio GET for {rebind_headers}, got {studio_get.status_code}"
        )
        studio_post = rem_loopback.post(
            "/api/v1/ontology/studio/sessions",
            headers=rebind_headers,
            json={"session_id": "dns-rebind-studio", "domain_namespace": "wealth_mgmt"},
        )
        assert studio_post.status_code == 403, (
            f"Expected 403 on Studio POST for {rebind_headers}, got {studio_post.status_code}"
        )

    rem_external = TestClient(
        rem_app, client=("198.51.100.77", 48000), base_url="http://198.51.100.1:8000"
    )
    assert (
        rem_external.put(
            "/api/v1/platform/config",
            headers={"Origin": "http://evil.example"},
            json={"harness": {"provider": "claude-cli"}},
        ).status_code
        == 403
    )
    assert (
        rem_external.get(
            "/api/v1/ontology/studio/sessions",
            headers={"Origin": "http://evil.example"},
        ).status_code
        == 403
    )
    assert (
        rem_external.put(
            "/api/v1/platform/config",
            json={"harness": {"provider": "claude-cli"}},
        ).status_code
        == 200
    )
    assert (
        rem_external.post(
            "/api/v1/ontology/studio/sessions",
            json={"session_id": "rem-ext-ok", "domain_namespace": "wealth_mgmt"},
        ).status_code
        == 201
    )


def test_waivers_json_bidirectional_compat_with_path_b_promote_and_delta(
    tmp_path: Path, isolated_open_access_env: Path
) -> None:
    """Both /waiver -> promote(path_b) -> /delta AND promote(path_b) -> /waiver -> /delta succeed."""
    studio_root = isolated_open_access_env
    client = _build_local_client(tmp_path)

    # Sequence 1: POST /waiver -> promote(path_b) -> POST /delta
    sid1 = "waiver-seq-a"
    r1 = client.post(
        "/api/v1/ontology/studio/sessions",
        json={"session_id": sid1, "domain_namespace": "wealth_mgmt"},
    )
    assert r1.status_code == 201
    sha1 = r1.json()["head_sha256"]

    w1 = client.post(
        f"/api/v1/ontology/studio/sessions/{sid1}/waiver",
        json={
            "target_concept_uris": ["urn:ontology:entity:seq_a_custom"],
            "rationale": "Pre-authorized steward waiver before Path B promotion.",
        },
    )
    assert w1.status_code == 200, w1.text

    ev1 = studio_root / sid1 / "evidence"
    ev1.mkdir(parents=True, exist_ok=True)
    (ev1 / "staged_candidates.json").write_text(
        json.dumps(
            {
                "staged_candidates": [
                    {
                        "candidate_id": "cand-seq-a",
                        "concept_name": "SeqACandidate",
                        "suggested_hub": "hub_seq_a",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    p1 = client.post(
        f"/api/v1/ontology/studio/sessions/{sid1}/staged-candidates/cand-seq-a/promote",
        json={"path": "path_b", "waiver_rationale": "Path B promotion after standalone waiver."},
    )
    assert p1.status_code == 200, p1.text

    stored1 = json.loads((studio_root / sid1 / "waivers.json").read_text(encoding="utf-8"))
    assert isinstance(stored1, list) and len(stored1) == 2

    d1 = client.post(
        f"/api/v1/ontology/studio/sessions/{sid1}/delta",
        json={
            "delta_id": "d-seq-a",
            "parent_sha256": sha1,
            "base_version": "0.0.0",
            "target_version": "1.0.0",
            "operations": [
                {
                    "op_type": "add_concept",
                    "op_id": "op-seq-a",
                    "concept_uri": "urn:ontology:entity:seq_a_custom",
                    "concept_name": "Seq A Custom",
                    "definition": "Custom concept covered by first waiver.",
                }
            ],
        },
        headers={"If-Match": f'"{sha1}"'},
    )
    assert d1.status_code == 200, d1.text

    # Sequence 2: promote(path_b) -> POST /waiver -> POST /delta
    sid2 = "waiver-seq-b"
    r2 = client.post(
        "/api/v1/ontology/studio/sessions",
        json={"session_id": sid2, "domain_namespace": "wealth_mgmt"},
    )
    assert r2.status_code == 201
    sha2 = r2.json()["head_sha256"]

    ev2 = studio_root / sid2 / "evidence"
    ev2.mkdir(parents=True, exist_ok=True)
    (ev2 / "staged_candidates.json").write_text(
        json.dumps(
            {
                "staged_candidates": [
                    {
                        "candidate_id": "cand-seq-b",
                        "concept_name": "SeqBCandidate",
                        "suggested_hub": "hub_seq_b",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    p2 = client.post(
        f"/api/v1/ontology/studio/sessions/{sid2}/staged-candidates/cand-seq-b/promote",
        json={"path": "path_b", "waiver_rationale": "Path B promotion before standalone waiver."},
    )
    assert p2.status_code == 200, p2.text

    w2 = client.post(
        f"/api/v1/ontology/studio/sessions/{sid2}/waiver",
        json={
            "target_concept_uris": ["urn:ontology:entity:seq_b_custom"],
            "rationale": "Standalone steward waiver after Path B promotion.",
        },
    )
    assert w2.status_code == 200, w2.text

    stored2 = json.loads((studio_root / sid2 / "waivers.json").read_text(encoding="utf-8"))
    assert isinstance(stored2, list) and len(stored2) == 2
    uris2 = {
        u
        for item in stored2
        for u in (item.get("envelope", {}).get("target_concept_uris") or [])
    }
    assert "urn:opendv:concept:seqbcandidate" in uris2
    assert "urn:ontology:entity:seq_b_custom" in uris2

    d2 = client.post(
        f"/api/v1/ontology/studio/sessions/{sid2}/delta",
        json={
            "delta_id": "d-seq-b",
            "parent_sha256": sha2,
            "base_version": "0.0.0",
            "target_version": "1.0.0",
            "operations": [
                {
                    "op_type": "add_concept",
                    "op_id": "op-seq-b",
                    "concept_uri": "urn:ontology:entity:seq_b_custom",
                    "concept_name": "Seq B Custom",
                    "definition": "Custom concept covered by second waiver.",
                }
            ],
        },
        headers={"If-Match": f'"{sha2}"'},
    )
    assert d2.status_code == 200, d2.text


def test_post_runs_handle_and_sync_dict_reflect_active_platform_config_provider(
    tmp_path: Path, repo_root: Path
) -> None:
    """POST /runs handle and execute_pipeline_run_sync dict branch honor active platform_config."""
    from opendv_agent_harness.pipeline import (
        AgenticPipelineReport,
        DeterministicValidationReport,
    )
    from opendv_core.genesis import create_genesis_ontology
    from opendv_core.models.vault_blueprint import create_vault_blueprint
    from opendv_service.use_cases.pipeline import execute_pipeline_run_sync

    state = PlatformStateContainer(state_dir=tmp_path / "state", workspace_root=repo_root)
    client = _build_local_client(tmp_path, workspace_root=repo_root, state=state)

    cfg_put = client.put(
        "/api/v1/platform/config",
        json={"harness": {"provider": "claude-cli"}},
    )
    assert cfg_put.status_code == 200, cfg_put.text

    proj_resp = client.post(
        "/api/v1/projects",
        json={
            "project_id": "proj-provider-parity",
            "display_name": "Provider Parity",
            "domain_namespace": "wealth_mgmt",
        },
    )
    assert proj_resp.status_code == 201, proj_resp.text

    run_resp = client.post("/api/v1/runs", json={"project_id": "proj-provider-parity"})
    assert run_resp.status_code == 202, run_resp.text
    assert run_resp.json()["harness_provider"] == "claude-cli"
    run_id = run_resp.json()["run_id"]
    assert client.get(f"/api/v1/runs/{run_id}").json()["harness_provider"] == "claude-cli"

    captured_providers: list[str] = []

    def _spy_runner(
        spec_obj: Any, setup_obj: Any, *args: Any, **kwargs: Any
    ) -> AgenticPipelineReport:
        captured_providers.append(str(getattr(setup_obj, "provider", "")))
        now = "2026-11-19T00:00:00Z"
        val = DeterministicValidationReport(
            stage="INPUT_VALIDATION", passed=True, checked_items_count=1, diagnostics=[]
        )
        return AgenticPipelineReport(
            run_id=getattr(spec_obj, "run_id", None) or "run-sync-dict",
            pipeline_id=spec_obj.pipeline_id,
            project_id=spec_obj.project_id,
            domain_namespace=spec_obj.domain_namespace,
            status="COMPLETED",
            overall_passed=True,
            started_at=now,
            completed_at=now,
            total_duration_ms=1.0,
            harness_setup=setup_obj.model_dump(mode="json") if hasattr(setup_obj, "model_dump") else {},
            input_validation=val,
            output_validation=val,
            wave_reports=[],
            step_reports=[],
            intermediate_artifacts=[],
            final_artifacts={},
            observability_events=[],
        )

    bp_dict = create_vault_blueprint(
        "wealth_mgmt",
        create_genesis_ontology(domain_namespace="wealth_mgmt", enterprise_owner="Test Org"),
    ).model_dump(mode="json")
    sync_res = execute_pipeline_run_sync(
        state,
        {
            "project_id": "proj-provider-parity",
            "blueprint": bp_dict,
        },
        role=RbacRole.PRIVILEGED_CUSTODIAN,
        pipeline_runner=_spy_runner,
    )
    assert sync_res.status == "COMPLETED"
    assert captured_providers == ["claude-cli"]


def test_verify_csrf_rejects_malicious_referer_with_stale_bearer_and_testclient_init_consistency(
    tmp_path: Path,
) -> None:
    """Stale Bearer on loopback never bypasses Referer CSRF; default TestClient aligns /init."""
    client = _build_local_client(tmp_path)

    resp_studio = client.post(
        "/api/v1/ontology/studio/sessions",
        json={"session_id": "csrf-bearer-referer", "domain_namespace": "wealth_mgmt"},
        headers={
            "Host": "localhost:8000",
            "Referer": "https://evil.example/pwn.html",
            "Authorization": "Bearer stale",
        },
    )
    assert resp_studio.status_code == 403, resp_studio.text
    assert resp_studio.json()["error_code"] == "CSRF_ORIGIN_MISMATCH"

    resp_cfg = client.put(
        "/api/v1/platform/config",
        json={"harness": {"provider": "claude-cli"}},
        headers={
            "Host": "localhost:8000",
            "Referer": "https://evil.example/pwn.html",
            "Authorization": "Bearer stale",
        },
    )
    assert resp_cfg.status_code == 403, resp_cfg.text

    # Default TestClient(app) works consistently across /sessions, /init, /auth/launch-nonce, /auth/token
    default_client = TestClient(client.app)
    assert default_client.post("/api/v1/ontology/studio/init", json={}).status_code == 200
    nonce_resp = default_client.post("/api/v1/ontology/studio/auth/launch-nonce")
    assert nonce_resp.status_code == 200
    tok_resp = default_client.post(
        "/api/v1/ontology/studio/auth/token",
        json={"launch_nonce": nonce_resp.json()["launch_nonce"]},
    )
    assert tok_resp.status_code == 200


def test_remote_platform_low_privilege_jwt_cannot_escalate_or_bypass_redaction(
    tmp_path: Path, repo_root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Valid viewer/editor/automated JWTs in remote_platform map RBAC tiers and block escalation."""
    state = PlatformStateContainer(state_dir=tmp_path / "state", workspace_root=repo_root)
    seed_canonical_pe_demo(state)
    client = _build_local_client(tmp_path, workspace_root=repo_root, state=state)

    monkeypatch.setenv("OPENDV_SERVER_MODE", "remote_platform")
    monkeypatch.setenv("OPENDV_JWT_PUBLIC_KEY", _TEST_RSA_PUBLIC_KEY_PEM)
    monkeypatch.setenv("OPENDV_JWT_ISSUER", "opendv-platform")
    monkeypatch.setenv("OPENDV_JWT_AUDIENCE", "opendv-api")

    viewer_jwt = _mint_test_jwt(subject="user:viewer1", role="viewer")
    for extra_headers in ({}, {"X-OpenDV-Role": "PRIVILEGED_CUSTODIAN"}):
        hdrs = {"Authorization": f"Bearer {viewer_jwt}", **extra_headers}
        assert (
            client.put(
                "/api/v1/platform/config",
                headers=hdrs,
                json={"harness": {"provider": "claude-cli"}},
            ).status_code
            == 403
        )
        assert (
            client.post(
                "/api/v1/runs",
                headers=hdrs,
                json={"project_id": "proj-apex-horizon-pe"},
            ).status_code
            == 403
        )
        assert (
            client.post(
                "/api/v1/runs/run-pe-apex-horizon-20261030/abort",
                headers=hdrs,
            ).status_code
            == 403
        )
        get_cfg = client.get("/api/v1/platform/config", headers=hdrs)
        assert get_cfg.status_code == 200
        assert get_cfg.json()["workspace_root"] == "[REDACTED]"

        rep = client.get(
            "/api/v1/runs/run-pe-apex-horizon-20261030/report",
            headers=hdrs,
        )
        assert rep.status_code == 200
        events = rep.json()["observability_events"]
        assert "245000000" not in json.dumps(events)
        assert events[0]["layer1_story_banner"]["headline"].startswith("[REDACTED")

    # Editor / automated JWTs map to INTERNAL_MODELER and cannot escalate via X-OpenDV-Role
    for role_name, subject in (("editor", "user:editor1"), ("automated", "agent:bot1")):
        tok = _mint_test_jwt(subject=subject, role=role_name)
        esc_hdrs = {
            "Authorization": f"Bearer {tok}",
            "X-OpenDV-Role": "PRIVILEGED_CUSTODIAN",
        }
        sse_esc = client.get(
            "/api/v1/runs/run-pe-apex-horizon-20261030/events",
            headers=esc_hdrs,
        )
        assert sse_esc.status_code == 200
        assert sse_esc.headers.get("X-OpenDV-Effective-Role") == RbacRole.INTERNAL_MODELER.value

        rep_esc = client.get(
            "/api/v1/runs/run-pe-apex-horizon-20261030/report",
            headers=esc_hdrs,
        )
        assert rep_esc.status_code == 200
        assert "245000000" not in json.dumps(rep_esc.json()["observability_events"])

        # Downgrade to PUBLIC is honored
        down_hdrs = {"Authorization": f"Bearer {tok}", "X-OpenDV-Role": "PUBLIC"}
        cfg_down = client.get("/api/v1/platform/config", headers=down_hdrs)
        assert cfg_down.status_code == 200
        assert cfg_down.json()["workspace_root"] == "[REDACTED]"
        assert (
            client.put(
                "/api/v1/platform/config",
                headers=down_hdrs,
                json={"harness": {"provider": "claude-cli"}},
            ).status_code
            == 403
        )

    # Admin JWT maps to PRIVILEGED_CUSTODIAN and may downgrade cleanly
    admin_jwt = _mint_test_jwt(subject="user:admin1", role="admin")
    admin_rep = client.get(
        "/api/v1/runs/run-pe-apex-horizon-20261030/report",
        headers={"Authorization": f"Bearer {admin_jwt}"},
    )
    assert admin_rep.status_code == 200
    assert "245000000" in json.dumps(admin_rep.json()["observability_events"])

    admin_down = client.get(
        "/api/v1/platform/config",
        headers={"Authorization": f"Bearer {admin_jwt}", "X-OpenDV-Role": "PUBLIC"},
    )
    assert admin_down.status_code == 200
    assert admin_down.json()["workspace_root"] == "[REDACTED]"


def test_remote_unauthenticated_normalized_origin_vs_host_matching(
    tmp_path: Path, repo_root: Path
) -> None:
    """--allow-remote-unauthenticated normalizes case and HTTP(S) default ports, rejecting spoofs."""
    rem_settings = OpenDVSettings.load(
        workspace_root=repo_root,
        state_dir=tmp_path / "rem_norm_state",
        is_server=True,
    ).model_copy(update={"remote_unauthenticated_mode": True})
    rem_app = create_app(
        settings=rem_settings,
        workspace_root=repo_root,
        storage_root=tmp_path / "rem_norm_state",
    )
    rem_external = TestClient(
        rem_app, client=("198.51.100.77", 48000), base_url="http://198.51.100.1:8000"
    )

    valid_cases = [
        ("mixed-case", "MyHost.Example:8000", "http://myhost.example:8000"),
        ("http-default-80", "198.51.100.1:80", "http://198.51.100.1"),
        ("https-default-443", "198.51.100.1:443", "https://198.51.100.1"),
    ]
    for idx, (label, host_hdr, origin_hdr) in enumerate(valid_cases):
        hdrs = {"Host": host_hdr, "Origin": origin_hdr}
        get_sess = rem_external.get("/api/v1/ontology/studio/sessions", headers=hdrs)
        assert get_sess.status_code == 200, f"{label} GET failed: {get_sess.text}"

        post_sess = rem_external.post(
            "/api/v1/ontology/studio/sessions",
            headers=hdrs,
            json={"session_id": f"rem-norm-{idx}", "domain_namespace": "wealth_mgmt"},
        )
        assert post_sess.status_code == 201, f"{label} POST failed: {post_sess.text}"

        put_cfg = rem_external.put(
            "/api/v1/platform/config",
            headers=hdrs,
            json={"harness": {"provider": "claude-cli"}},
        )
        assert put_cfg.status_code == 200, f"{label} PUT failed: {put_cfg.text}"

    rejected_cases = [
        ("cross-port-8080", "198.51.100.1:8080", "http://198.51.100.1"),
        ("scheme-port-mismatch-http-443", "198.51.100.1:443", "http://198.51.100.1"),
        ("scheme-port-mismatch-https-80", "198.51.100.1:80", "https://198.51.100.1"),
        ("non-http-ftp-scheme", "198.51.100.1:8000", "ftp://198.51.100.1:8000"),
        ("userinfo-spoof", "198.51.100.1:8000", "http://evil.com@198.51.100.1:8000"),
    ]
    for label, host_hdr, origin_hdr in rejected_cases:
        hdrs = {"Host": host_hdr, "Origin": origin_hdr}
        get_sess = rem_external.get("/api/v1/ontology/studio/sessions", headers=hdrs)
        assert get_sess.status_code == 403, f"{label} GET expected 403, got {get_sess.status_code}"

        post_sess = rem_external.post(
            "/api/v1/ontology/studio/sessions",
            headers=hdrs,
            json={"session_id": f"rem-rej-{label}", "domain_namespace": "wealth_mgmt"},
        )
        assert post_sess.status_code == 403, (
            f"{label} POST expected 403, got {post_sess.status_code}"
        )

        put_cfg = rem_external.put(
            "/api/v1/platform/config",
            headers=hdrs,
            json={"harness": {"provider": "claude-cli"}},
        )
        assert put_cfg.status_code == 403, f"{label} PUT expected 403, got {put_cfg.status_code}"


def test_loopback_rejects_malformed_userinfo_and_non_http_host_or_origin(
    tmp_path: Path, repo_root: Path
) -> None:
    """Loopback Host/Origin parsing rejects bad ports, IPv6 syntax errors, userinfo, and ftp."""
    state = PlatformStateContainer(state_dir=tmp_path / "state", workspace_root=repo_root)
    client = _build_local_client(tmp_path, workspace_root=repo_root, state=state)

    bad_loopback_headers = [
        ("host-notaport", {"Host": "127.0.0.1:notaport"}),
        ("host-out-of-range-port", {"Host": "127.0.0.1:99999"}),
        ("host-userinfo", {"Host": "evil.com@127.0.0.1:8000"}),
        ("host-bad-ipv6", {"Host": "[invalid"}),
        ("origin-ftp", {"Host": "127.0.0.1:8000", "Origin": "ftp://127.0.0.1:8000"}),
        ("origin-userinfo", {"Host": "127.0.0.1:8000", "Origin": "http://evil.com@127.0.0.1:8000"}),
        ("origin-notaport", {"Host": "127.0.0.1:8000", "Origin": "http://127.0.0.1:notaport"}),
        ("origin-bad-ipv6", {"Host": "127.0.0.1:8000", "Origin": "http://[invalid"}),
    ]
    for label, hdrs in bad_loopback_headers:
        studio_get = client.get("/api/v1/ontology/studio/sessions", headers=hdrs)
        assert studio_get.status_code == 403, (
            f"{label} Studio GET expected 403, got {studio_get.status_code}: {studio_get.text}"
        )

        cfg_put = client.put(
            "/api/v1/platform/config",
            headers=hdrs,
            json={"harness": {"provider": "claude-cli"}},
        )
        assert cfg_put.status_code == 403, (
            f"{label} Platform PUT expected 403, got {cfg_put.status_code}: {cfg_put.text}"
        )

        cfg_get = client.get("/api/v1/platform/config", headers=hdrs)
        assert cfg_get.status_code == 200, (
            f"{label} Platform GET expected 200 PUBLIC, got {cfg_get.status_code}: {cfg_get.text}"
        )
        assert cfg_get.json()["workspace_root"] == "[REDACTED]", f"{label} expected redacted config"

    # Malformed IPv6 Origin in remote_unauthenticated mode also fails closed cleanly (never 400)
    rem_settings = OpenDVSettings.load(
        workspace_root=repo_root,
        state_dir=tmp_path / "rem_bad_ipv6_state",
        is_server=True,
    ).model_copy(update={"remote_unauthenticated_mode": True})
    rem_app = create_app(
        settings=rem_settings,
        workspace_root=repo_root,
        storage_root=tmp_path / "rem_bad_ipv6_state",
    )
    rem_external = TestClient(
        rem_app, client=("198.51.100.77", 48000), base_url="http://198.51.100.1:8000"
    )
    bad_ipv6_hdrs = {"Host": "198.51.100.1:8000", "Origin": "http://[invalid"}
    assert rem_external.get("/api/v1/ontology/studio/sessions", headers=bad_ipv6_hdrs).status_code == 403
    assert (
        rem_external.put(
            "/api/v1/platform/config",
            headers=bad_ipv6_hdrs,
            json={"harness": {"provider": "claude-cli"}},
        ).status_code
        == 403
    )
    rem_cfg_get = rem_external.get("/api/v1/platform/config", headers=bad_ipv6_hdrs)
    assert rem_cfg_get.status_code == 200
    assert rem_cfg_get.json()["workspace_root"] == "[REDACTED]"

    # Port-omitted Host: localhost vs explicit :8000 Origin is rejected consistently across verify_csrf
    portless_host_resp = client.post(
        "/api/v1/ontology/studio/sessions",
        headers={"Host": "localhost", "Origin": "http://localhost:8000"},
        json={"session_id": "portless-host-mismatch", "domain_namespace": "wealth_mgmt"},
    )
    assert portless_host_resp.status_code == 403, portless_host_resp.text


def test_export_blueprint_enforces_referer_csrf_role_and_local_user_principal(
    tmp_path: Path,
) -> None:
    """/export-blueprint uses require_viewer: Referer CSRF, X-OpenDV-Role, and human:local_user."""
    client = _build_local_client(tmp_path)
    sid = "bp-csrf-01"
    create_resp = client.post(
        "/api/v1/ontology/studio/sessions",
        json={"session_id": sid, "domain_namespace": "wealth_mgmt"},
    )
    assert create_resp.status_code == 201, create_resp.text

    bad_ref = client.post(
        "/api/v1/ontology/studio/sessions/nonexistent/export-blueprint",
        headers={"Host": "localhost:8000", "Referer": "https://evil.com/pwn.html"},
    )
    assert bad_ref.status_code == 403, bad_ref.text
    assert bad_ref.json()["error_code"] == "CSRF_ORIGIN_MISMATCH"

    bad_role = client.get(
        f"/api/v1/ontology/studio/sessions/{sid}/export-blueprint",
        headers={"X-OpenDV-Role": "INVALID_ROLE"},
    )
    assert bad_role.status_code == 400, bad_role.text
    assert bad_role.json()["error_code"] == "SYNTAX_INVALID_ARGUMENT"

    ok_get = client.get(f"/api/v1/ontology/studio/sessions/{sid}/export-blueprint")
    assert ok_get.status_code == 200, ok_get.text
    assert ok_get.headers.get("X-OpenDV-Principal") == "human:local_user"

    ok_post = client.post(f"/api/v1/ontology/studio/sessions/{sid}/export-blueprint")
    assert ok_post.status_code == 200, ok_post.text
    assert ok_post.headers.get("X-OpenDV-Principal") == "human:local_user"


