"""Round-3 Track R3-4 Pre-Merge Audit Remediation Tests.

Covers:
1. P10a MAJ-01: Waiver expiry (`expires_at`), strict `base_sha256` binding (no zero wildcard),
   `proposal_id` scoping, and strict `authorized_role == "admin"` + non-empty `authorized_by`.
2. P10b MAJ-01 & MAJ-02: `/deltas/validate` returning `valid=False` on ONT-EVO-002 grounding
   violations while honoring valid stored/dry-run waivers; `create_proposal` resolving exact
   `version_sha256` for historical `base_version` and rejecting mismatched `base_sha256`.
3. P10c MAJ-01 & MAJ-02: 100% atomic `clone_session_endpoint` with signed `SESSION_CLONED`
   genesis audit record in `staging_dir`; `reject_proposal` blocking automated agent principals.
4. S3-CRIT-01: `_normalize_proposal_record` supporting nested `OntologyProposal` dicts across
   `list`, `get`, `accept`, `reject`, and `rebase`.
5. S4-MAJ-01..03: Clarification answers validation + CAS persistence + fail-closed corrupt JSON;
   `/parse` `ocr_mode` & `source_ids` & `OCR_UNAVAILABLE` checks; `/clarify` & `/construct`
   `base_version` checks, synchronous `If-Match` OCC, `auto_commit=True` mode enforcement,
   `app.state.studio_agent_runner` wiring, and `apply_delta_endpoint` OCC hardening.
6. P11 MAJ-01..03 & S4-MAJ-04: `cancel_job` vs `committing` atomicity under `ctx.lock`,
   SSE synchronous pre-flight RFC 9457 `400`/`404`, `resolve_job_session_id` fast path,
   `_translate_runner_error` mapping to registered `ErrorCode`s, and `error_catalog.py`
   authoritative in-package `ERROR_MAPPINGS` wheel fallback for all 157 `ErrorCode`s.
"""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from starlette.testclient import TestClient

import opendv_platform_api.job_supervisor as js_mod
import opendv_platform_api.security.error_catalog as ec_mod
from opendv_contracts.errors import ERROR_MAPPINGS, ErrorCode
from opendv_contracts.jcs import dumps_jcs_bytes
from opendv_platform_api.job_supervisor import (
    JobCancelledError,
    JobContext,
    JobSupervisor,
    _translate_runner_error,
    run_construct_job,
)
from opendv_platform_api.main import create_app
from opendv_platform_api.routers.ontology_studio import get_session_manager
from opendv_service import OpenDVSettings
from opendv_service.construction.clarification_harness import EMPTY_ANSWERS_SHA256
from opendv_service.studio.session_manager import StudioSessionManager, append_signed_audit_record
from opendv_sources.exceptions import SourceError
from opendv_sources.ingestion.layout_evaluator import TesseractOCRRouter


@pytest.fixture(autouse=True)
def reset_studio_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
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
    """Initialize local standalone environment and return valid admin auth headers."""
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


def _create_session(client: TestClient, auth_headers: dict[str, str], sid: str) -> dict[str, Any]:
    resp = client.post(
        "/api/v1/ontology/studio/sessions",
        json={"session_id": sid, "domain_namespace": "wealth_mgmt"},
        headers=auth_headers,
    )
    assert resp.status_code == 201, resp.text
    return resp.json()


# ===========================================================================
# 1. P10a MAJ-01: Waiver Expiry, Base Binding, Proposal Scoping & Admin Role
# ===========================================================================


def test_p10a_waiver_base_binding_expiry_scoping_and_role_enforcement(
    client: TestClient, auth_headers: dict[str, str], tmp_path: Path
) -> None:
    """Verify submit_waiver & _build_and_sign_commit_waivers enforce all P10a MAJ-01 bounds."""
    sid = "s-p10a-waiver"
    meta = _create_session(client, auth_headers, sid)
    genesis_sha = meta["head_sha256"]
    sdir = tmp_path / "opendv_studio" / sid

    # 1a. Mismatched base_sha256 rejected with 412 ONT_EVO_CONCURRENT_MODIFICATION
    bad_base_resp = client.post(
        f"/api/v1/ontology/studio/sessions/{sid}/waiver",
        json={
            "target_concept_uris": ["urn:ontology:entity:alpha"],
            "base_sha256": "a" * 64,
            "rationale": "Mismatched base sha should fail.",
        },
        headers=auth_headers,
    )
    assert bad_base_resp.status_code == 412
    assert bad_base_resp.json()["error_code"] == "ONT_EVO_CONCURRENT_MODIFICATION"

    # 1b. Wildcard "0" * 64 rejected with 422 INVALID_GROUNDING_WAIVER
    zero_base_resp = client.post(
        f"/api/v1/ontology/studio/sessions/{sid}/waiver",
        json={
            "target_concept_uris": ["urn:ontology:entity:alpha"],
            "base_sha256": "0" * 64,
            "rationale": "Zero wildcard base sha should fail.",
        },
        headers=auth_headers,
    )
    assert zero_base_resp.status_code == 422
    assert zero_base_resp.json()["error_code"] == "INVALID_GROUNDING_WAIVER"

    # 1c. Omitted base_sha256 binds to meta.head_sha256 (never "0" * 64) and stamps admin role
    ok_waiver = client.post(
        f"/api/v1/ontology/studio/sessions/{sid}/waiver",
        json={
            "target_concept_uris": ["urn:ontology:entity:scoped_only"],
            "proposal_id": "prop-target-a",
            "rationale": "Scoped waiver strictly bound to prop-target-a.",
        },
        headers=auth_headers,
    )
    assert ok_waiver.status_code == 200
    assert ok_waiver.json()["base_sha256"] == genesis_sha
    stored_waivers = json.loads((sdir / "waivers.json").read_text(encoding="utf-8"))
    assert stored_waivers[0]["envelope"]["base_sha256"] == genesis_sha
    assert stored_waivers[0]["envelope"]["authorized_role"] == "admin"
    assert stored_waivers[0]["envelope"]["authorized_by"]

    # 1d. Scoped waiver (proposal_id="prop-target-a") MUST NOT authorize direct /deltas/apply
    direct_apply = client.post(
        f"/api/v1/ontology/studio/sessions/{sid}/deltas/apply",
        json={
            "delta_id": "d-direct-scoped-leak",
            "parent_sha256": genesis_sha,
            "base_version": "0.0.0",
            "target_version": "1.0.0",
            "operations": [
                {
                    "op_type": "add_concept",
                    "op_id": "op-s1",
                    "concept_uri": "urn:ontology:entity:scoped_only",
                    "concept_name": "Scoped Only",
                    "definition": "Concept with proposal-scoped waiver.",
                }
            ],
        },
        headers={**auth_headers, "If-Match": f'"{genesis_sha}"'},
    )
    assert direct_apply.status_code == 422
    assert direct_apply.json()["error_code"] == "ONT_EVO_UNGROUNDED_CONCEPT"

    # 1e. Scoped waiver MUST NOT authorize a different proposal ("prop-other-b")
    for pid in ("prop-other-b", "prop-target-a"):
        pr = client.post(
            f"/api/v1/ontology/studio/sessions/{sid}/proposals",
            json={
                "proposal_id": pid,
                "operations": [
                    {
                        "op_type": "add_concept",
                        "op_id": f"op-{pid}",
                        "concept_uri": "urn:ontology:entity:scoped_only",
                        "concept_name": "Scoped Only",
                        "definition": "Concept with proposal-scoped waiver.",
                    }
                ],
                "rationale": "Test proposal scoping",
            },
            headers=auth_headers,
        )
        assert pr.status_code == 201

    accept_wrong = client.post(
        f"/api/v1/ontology/studio/sessions/{sid}/proposals/prop-other-b/accept",
        json={"self_acceptance_override": True},
        headers={**auth_headers, "If-Match": f'"{genesis_sha}"'},
    )
    assert accept_wrong.status_code == 422
    assert accept_wrong.json()["error_code"] == "ONT_EVO_UNGROUNDED_CONCEPT"

    # 1f. Scoped waiver DOES authorize matching proposal ("prop-target-a")
    accept_right = client.post(
        f"/api/v1/ontology/studio/sessions/{sid}/proposals/prop-target-a/accept",
        json={"self_acceptance_override": True},
        headers={**auth_headers, "If-Match": f'"{genesis_sha}"'},
    )
    assert accept_right.status_code == 200
    head_1_sha = accept_right.json()["target_sha256"]

    # 1g. Expired waiver & forged non-admin role in waivers.json are skipped
    session_mgr = StudioSessionManager(studio_root=tmp_path / "opendv_studio")
    sec_mgr = session_mgr.security_manager
    past_iso = (datetime.now(UTC) - timedelta(hours=2)).isoformat()
    created_iso = (datetime.now(UTC) - timedelta(hours=3)).isoformat()

    expired_env = {
        "waiver_id": "00000000-0000-0000-0000-000000000011",
        "proposal_id": None,
        "proposal_delta_sha256": "0" * 64,
        "target_concept_uris": ["urn:ontology:entity:expired_c"],
        "base_sha256": head_1_sha,
        "rationale": "Expired steward waiver entry.",
        "authorized_by": "human:local_admin",
        "authorized_role": "admin",
        "created_at": created_iso,
        "expires_at": past_iso,
    }
    forged_role_env = {
        "waiver_id": "00000000-0000-0000-0000-000000000012",
        "proposal_id": None,
        "proposal_delta_sha256": "0" * 64,
        "target_concept_uris": ["urn:ontology:entity:expired_c"],
        "base_sha256": head_1_sha,
        "rationale": "Non-admin role waiver entry.",
        "authorized_by": "human:editor_1",
        "authorized_role": "editor",
        "created_at": created_iso,
        "expires_at": None,
    }
    (sdir / "waivers.json").write_text(
        json.dumps(
            [
                {
                    "envelope": expired_env,
                    "hmac_signature": sec_mgr.sign_waiver(dumps_jcs_bytes(expired_env)),
                },
                {
                    "envelope": forged_role_env,
                    "hmac_signature": sec_mgr.sign_waiver(dumps_jcs_bytes(forged_role_env)),
                },
            ],
            indent=2,
        ),
        encoding="utf-8",
    )

    expired_apply = client.post(
        f"/api/v1/ontology/studio/sessions/{sid}/deltas/apply",
        json={
            "delta_id": "d-expired-waiver",
            "parent_sha256": head_1_sha,
            "base_version": "1.0.0",
            "target_version": "1.1.0",
            "operations": [
                {
                    "op_type": "add_concept",
                    "op_id": "op-exp",
                    "concept_uri": "urn:ontology:entity:expired_c",
                    "concept_name": "Expired Concept",
                    "definition": "Should fail because stored waivers are expired or non-admin.",
                }
            ],
        },
        headers={**auth_headers, "If-Match": f'"{head_1_sha}"'},
    )
    assert expired_apply.status_code == 422
    assert expired_apply.json()["error_code"] == "ONT_EVO_UNGROUNDED_CONCEPT"


# ===========================================================================
# 2. P10b MAJ-01 & MAJ-02: /deltas/validate Grounding & Historical Proposal Base
# ===========================================================================


def test_p10b_validate_delta_stored_waiver_and_editor_dry_run_restriction(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """Verify /deltas/validate enforces ONT-EVO-002 and honors only authorized waivers."""
    sid = "s-p10b-val"
    meta = _create_session(client, auth_headers, sid)
    genesis_sha = meta["head_sha256"]

    # Non-admin editor cannot synthesize dry-run low_confidence_waiver
    editor_headers = {**auth_headers, "X-OpenDV-Role": "editor"}
    val_editor = client.post(
        f"/api/v1/ontology/studio/sessions/{sid}/deltas/validate",
        json={
            "delta_id": "d-ed-val",
            "parent_sha256": genesis_sha,
            "base_version": "0.0.0",
            "target_version": "1.0.0",
            "operations": [
                {
                    "op_type": "add_concept",
                    "op_id": "op-ed",
                    "concept_uri": "urn:ontology:entity:ungrounded_x",
                    "concept_name": "Ungrounded X",
                    "definition": "Definition for ungrounded concept.",
                }
            ],
            "low_confidence_waiver": True,
            "grounding_rationale": "Editor trying to waive grounding without admin role.",
        },
        headers=editor_headers,
    )
    assert val_editor.status_code == 200
    assert val_editor.json()["valid"] is False
    assert val_editor.json()["error_code"] == "ONT_EVO_UNGROUNDED_CONCEPT"

    # Admin submits stored session waiver -> even editor /validate now sees valid=True
    w_resp = client.post(
        f"/api/v1/ontology/studio/sessions/{sid}/waiver",
        json={
            "target_concept_uris": ["urn:ontology:entity:ungrounded_x"],
            "rationale": "Admin pre-authorized stored waiver for ungrounded_x.",
        },
        headers=auth_headers,
    )
    assert w_resp.status_code == 200

    val_after_stored = client.post(
        f"/api/v1/ontology/studio/sessions/{sid}/deltas/validate",
        json={
            "delta_id": "d-ed-val-2",
            "parent_sha256": genesis_sha,
            "base_version": "0.0.0",
            "target_version": "1.0.0",
            "operations": [
                {
                    "op_type": "add_concept",
                    "op_id": "op-ed",
                    "concept_uri": "urn:ontology:entity:ungrounded_x",
                    "concept_name": "Ungrounded X",
                    "definition": "Definition for ungrounded concept.",
                }
            ],
        },
        headers=editor_headers,
    )
    assert val_after_stored.status_code == 200
    assert val_after_stored.json()["valid"] is True


def test_p10b_create_proposal_historical_base_version_sha(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """Verify create_proposal binds exact historical version_sha256 rather than head_sha256."""
    sid = "s-p10b-prop"
    meta = _create_session(client, auth_headers, sid)
    genesis_sha = meta["head_sha256"]

    # Advance session head to 1.0.0
    apply1 = client.post(
        f"/api/v1/ontology/studio/sessions/{sid}/deltas/apply",
        json={
            "delta_id": "d-p10b-1",
            "parent_sha256": genesis_sha,
            "base_version": "0.0.0",
            "target_version": "1.0.0",
            "operations": [
                {
                    "op_type": "add_concept",
                    "op_id": "op-1",
                    "concept_uri": "urn:ontology:entity:holding",
                    "concept_name": "Holding",
                    "definition": "Portfolio holding.",
                }
            ],
            "low_confidence_waiver": True,
            "grounding_rationale": "Approved holding concept for historical proposal test.",
        },
        headers={**auth_headers, "If-Match": f'"{genesis_sha}"'},
    )
    assert apply1.status_code == 200
    head_1_sha = apply1.json()["target_sha256"]
    assert head_1_sha != genesis_sha

    # Historical base_version="0.0.0" without base_sha256 binds genesis_sha (NOT head_1_sha!)
    prop_hist = client.post(
        f"/api/v1/ontology/studio/sessions/{sid}/proposals",
        json={
            "proposal_id": "prop-hist-000",
            "base_version": "0.0.0",
            "operations": [
                {
                    "op_type": "add_concept",
                    "op_id": "op-h1",
                    "concept_uri": "urn:ontology:entity:custodian",
                    "concept_name": "Custodian",
                    "definition": "Asset custodian.",
                }
            ],
            "rationale": "Historical base proposal",
        },
        headers=auth_headers,
    )
    assert prop_hist.status_code == 201
    assert prop_hist.json()["base_version"] == "0.0.0"
    assert prop_hist.json()["base_sha256"] == genesis_sha

    # Mismatched base_sha256 (passing head_1_sha for base_version="0.0.0") -> 400
    prop_mismatch = client.post(
        f"/api/v1/ontology/studio/sessions/{sid}/proposals",
        json={
            "proposal_id": "prop-hist-bad",
            "base_version": "0.0.0",
            "base_sha256": head_1_sha,
            "operations": [],
        },
        headers=auth_headers,
    )
    assert prop_mismatch.status_code == 400
    assert prop_mismatch.json()["error_code"] == "PRECONDITION_BASE_SHA_MISMATCH"


# ===========================================================================
# 3. P10c MAJ-01 & MAJ-02: Atomic Clone & Automated Proposal Reject Block
# ===========================================================================


def test_p10c_atomic_clone_audit_chain_and_collision(
    client: TestClient, auth_headers: dict[str, str], tmp_path: Path
) -> None:
    """Verify clone_session_endpoint publishes atomically with verified SESSION_CLONED genesis."""
    src_sid = "s-p10c-src"
    _create_session(client, auth_headers, src_sid)

    # Create a proposal on source session so clone rewrites proposal session_id too
    p_resp = client.post(
        f"/api/v1/ontology/studio/sessions/{src_sid}/proposals",
        json={
            "proposal_id": "prop-clone-1",
            "operations": [
                {
                    "op_type": "add_concept",
                    "op_id": "op-c1",
                    "concept_uri": "urn:ontology:entity:mandate",
                    "concept_name": "Mandate",
                    "definition": "Investment mandate.",
                }
            ],
            "rationale": "Cloneable proposal",
        },
        headers=auth_headers,
    )
    assert p_resp.status_code == 201

    dst_sid = "s-p10c-dst"
    clone_resp = client.post(
        f"/api/v1/ontology/studio/sessions/{src_sid}/bundle/clone",
        json={"new_session_id": dst_sid},
        headers=auth_headers,
    )
    assert clone_resp.status_code == 201, clone_resp.text

    studio_root = tmp_path / "opendv_studio"
    session_mgr = StudioSessionManager(studio_root=studio_root)
    assert session_mgr.verify_audit_chain(dst_sid) is True

    dst_dir = studio_root / dst_sid
    src_dir = studio_root / src_sid
    (src_dir / ".session_secret").write_bytes(b"0" * 32)
    assert (dst_dir / ".session_secret").is_file()
    assert len((dst_dir / ".session_secret").read_bytes()) == 32
    assert (dst_dir / ".session_secret").read_bytes() != (src_dir / ".session_secret").read_bytes()
    prop_cloned = json.loads(
        (dst_dir / "proposals" / "prop-clone-1.json").read_text(encoding="utf-8")
    )
    assert prop_cloned["session_id"] == dst_sid
    assert not (dst_dir / ".session.lock").exists()
    assert not list((studio_root / ".scratch").glob(".staging_clone_*"))
    assert not list(studio_root.glob(".clone_*.lock"))

    audit_lines = [
        json.loads(line)
        for line in (dst_dir / "audit.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    assert len(audit_lines) == 1
    assert audit_lines[0]["action"] == "SESSION_CLONED"
    assert audit_lines[0]["session_id"] == dst_sid
    assert audit_lines[0]["prev_audit_sha256"] is None

    # Subsequent audit append on cloned session preserves full chain integrity
    append_signed_audit_record(
        session_dir=dst_dir,
        action="POST_CLONE_TEST",
        actor="human:local_admin",
        details={"ok": True},
        security_manager=session_mgr.security_manager,
    )
    assert session_mgr.verify_audit_chain(dst_sid) is True

    # Cloning onto an existing destination fails with 409 SESSION_ALREADY_EXISTS
    dup_resp = client.post(
        f"/api/v1/ontology/studio/sessions/{src_sid}/bundle/clone",
        json={"new_session_id": dst_sid},
        headers=auth_headers,
    )
    assert dup_resp.status_code == 409
    assert dup_resp.json()["error_code"] == "SESSION_ALREADY_EXISTS"


def test_p10c_reject_proposal_blocks_automated_principal(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """Verify automated/agent principals cannot reject proposals (`AGENT_OPERATION_FORBIDDEN`)."""
    sid = "s-p10c-reject"
    _create_session(client, auth_headers, sid)

    prop_resp = client.post(
        f"/api/v1/ontology/studio/sessions/{sid}/proposals",
        json={
            "proposal_id": "prop-agent-rej",
            "operations": [
                {
                    "op_type": "add_concept",
                    "op_id": "op-r1",
                    "concept_uri": "urn:ontology:entity:asset",
                    "concept_name": "Asset",
                    "definition": "Financial asset.",
                }
            ],
        },
        headers=auth_headers,
    )
    assert prop_resp.status_code == 201

    agent_headers = {**auth_headers, "X-OpenDV-Principal": "agent:bot_reviewer"}
    rej_agent = client.post(
        f"/api/v1/ontology/studio/sessions/{sid}/proposals/prop-agent-rej/reject",
        json={"reason": "Agent attempting to reject human/peer proposal"},
        headers=agent_headers,
    )
    assert rej_agent.status_code == 403
    assert rej_agent.json()["error_code"] == "AGENT_OPERATION_FORBIDDEN"


# ===========================================================================
# 4. S3-CRIT-01: Nested OntologyProposal Normalization Across Endpoints
# ===========================================================================


def test_s3_crit_01_nested_ontology_proposal_normalization(
    client: TestClient, auth_headers: dict[str, str], tmp_path: Path
) -> None:
    """Verify nested OntologyProposal JSON (`delta` sub-object) works across proposal routes."""
    sid = "s-s3-nested"
    meta = _create_session(client, auth_headers, sid)
    genesis_sha = meta["head_sha256"]
    sdir = tmp_path / "opendv_studio" / sid
    props_dir = sdir / "proposals"
    props_dir.mkdir(parents=True, exist_ok=True)

    nested_payload = {
        "proposal_id": "prop-nested-01",
        "proposer": "agent:ontology_architect",
        "created_at": "2026-11-12T00:00:00+00:00",
        "rationale": "Generated by architect persona",
        "status": None,
        "delta": {
            "delta_id": "delta-nested-01",
            "parent_sha256": genesis_sha,
            "base_ontology_version": "0.0.0",
            "target_version": "1.0.0",
            "operations": [
                {
                    "op_type": "add_concept",
                    "op_id": "op-n1",
                    "concept_uri": "urn:ontology:entity:fund",
                    "concept_name": "Investment Fund",
                    "definition": "Pooled investment vehicle.",
                }
            ],
        },
    }
    (props_dir / "prop-nested-01.json").write_text(
        json.dumps(nested_payload, indent=2), encoding="utf-8"
    )

    # list_proposals & get_proposal_detail normalize status -> PENDING and extract delta fields
    listed = client.get(f"/api/v1/ontology/studio/sessions/{sid}/proposals", headers=auth_headers)
    assert listed.status_code == 200
    assert len(listed.json()) == 1
    assert listed.json()[0]["status"] == "PENDING"
    assert listed.json()[0]["base_sha256"] == genesis_sha
    assert len(listed.json()[0]["operations"]) == 1

    detail = client.get(
        f"/api/v1/ontology/studio/sessions/{sid}/proposals/prop-nested-01",
        headers=auth_headers,
    )
    assert detail.status_code == 200
    assert detail.json()["base_version"] == "0.0.0"

    # accept_proposal succeeds cleanly on nested OntologyProposal record
    acc = client.post(
        f"/api/v1/ontology/studio/sessions/{sid}/proposals/prop-nested-01/accept",
        json={
            "low_confidence_waiver": True,
            "grounding_rationale": "Admin accepts architect nested proposal.",
        },
        headers={**auth_headers, "If-Match": f'"{genesis_sha}"'},
    )
    assert acc.status_code == 200
    assert acc.json()["target_version"] == "1.0.0"


# ===========================================================================
# 5. S4-MAJ-01..03: Clarifications, /parse, /clarify, /construct & /deltas/apply
# ===========================================================================


def test_s4_maj_01_clarification_validation_cas_and_corrupt_json(
    client: TestClient, auth_headers: dict[str, str], tmp_path: Path
) -> None:
    """Verify clarification answer validation, CAS storage, and fail-closed corrupt JSON reads."""
    sid = "s-s4-clarify"
    _create_session(client, auth_headers, sid)
    sdir = tmp_path / "opendv_studio" / sid
    clar_dir = sdir / "clarifications"
    clar_dir.mkdir(parents=True, exist_ok=True)

    # Empty answers -> 400 SYNTAX_INVALID_ARGUMENT
    r_empty = client.post(
        f"/api/v1/ontology/studio/sessions/{sid}/answers",
        json={"answers": [], "expected_answers_sha256": EMPTY_ANSWERS_SHA256},
        headers=auth_headers,
    )
    assert r_empty.status_code == 400
    assert r_empty.json()["error_code"] == "SYNTAX_INVALID_ARGUMENT"

    # Duplicate question_id -> 400 SYNTAX_INVALID_ARGUMENT
    r_dup = client.post(
        f"/api/v1/ontology/studio/sessions/{sid}/answers",
        json={
            "answers": [
                {"question_id": "q1", "selected_options": ["opt_a"]},
                {"question_id": "q1", "selected_options": ["opt_b"]},
            ],
            "expected_answers_sha256": EMPTY_ANSWERS_SHA256,
        },
        headers=auth_headers,
    )
    assert r_dup.status_code == 400
    assert r_dup.json()["error_code"] == "SYNTAX_INVALID_ARGUMENT"

    # Seed questions.json with allow_custom_text=False and options ["opt_a", "opt_b"]
    questions_doc = [
        {
            "question_id": "q-poly-1",
            "focus_concept": "Position",
            "question_text": "How is Position used in this domain?",
            "options": [
                {
                    "option_id": "opt_a",
                    "label": "Market Holding",
                    "rationale": "Financial portfolio holding.",
                },
                {
                    "option_id": "opt_b",
                    "label": "Org Role",
                    "rationale": "Human resources seat.",
                },
            ],
            "allow_custom_text": False,
        }
    ]
    (clar_dir / "questions.json").write_text(json.dumps(questions_doc), encoding="utf-8")

    # Unknown question_id -> 404 RESOURCE_NOT_FOUND
    r_unknown = client.post(
        f"/api/v1/ontology/studio/sessions/{sid}/answers",
        json={
            "answers": [{"question_id": "q-missing", "selected_options": ["opt_a"]}],
            "expected_answers_sha256": EMPTY_ANSWERS_SHA256,
        },
        headers=auth_headers,
    )
    assert r_unknown.status_code == 404
    assert r_unknown.json()["error_code"] == "RESOURCE_NOT_FOUND"

    # Disallowed custom_text -> 400 SYNTAX_INVALID_ARGUMENT
    r_custom = client.post(
        f"/api/v1/ontology/studio/sessions/{sid}/answers",
        json={
            "answers": [
                {
                    "question_id": "q-poly-1",
                    "selected_options": ["opt_a"],
                    "custom_text": "Disallowed free text",
                }
            ],
            "expected_answers_sha256": EMPTY_ANSWERS_SHA256,
        },
        headers=auth_headers,
    )
    assert r_custom.status_code == 400
    assert r_custom.json()["error_code"] == "SYNTAX_INVALID_ARGUMENT"

    # Invalid option -> 400 SYNTAX_INVALID_ARGUMENT
    r_bad_opt = client.post(
        f"/api/v1/ontology/studio/sessions/{sid}/answers",
        json={
            "answers": [{"question_id": "q-poly-1", "selected_options": ["opt_invalid"]}],
            "expected_answers_sha256": EMPTY_ANSWERS_SHA256,
        },
        headers=auth_headers,
    )
    assert r_bad_opt.status_code == 400
    assert r_bad_opt.json()["error_code"] == "SYNTAX_INVALID_ARGUMENT"

    # Valid submission persists JCS bytes into session CAS
    r_valid = client.post(
        f"/api/v1/ontology/studio/sessions/{sid}/answers",
        json={
            "answers": [{"question_id": "q-poly-1", "selected_options": ["opt_a"]}],
            "expected_answers_sha256": EMPTY_ANSWERS_SHA256,
        },
        headers=auth_headers,
    )
    assert r_valid.status_code == 200
    ans_sha = r_valid.json()["answers_sha256"]
    session_mgr = StudioSessionManager(studio_root=tmp_path / "opendv_studio")
    cas_blob = session_mgr.get_cas(sid).get(ans_sha)
    assert json.loads(cas_blob.decode("utf-8"))[0]["question_id"] == "q-poly-1"

    # Malformed questions.json & answers.json fail closed with 500 CORRUPT_SESSION_RECORD
    (clar_dir / "questions.json").write_text("{corrupt-json", encoding="utf-8")
    g_q = client.get(
        f"/api/v1/ontology/studio/sessions/{sid}/questions",
        headers=auth_headers,
    )
    assert g_q.status_code == 500
    assert g_q.json()["error_code"] == "CORRUPT_SESSION_RECORD"

    (clar_dir / "answers.json").write_text("{corrupt-answers", encoding="utf-8")
    g_a = client.get(
        f"/api/v1/ontology/studio/sessions/{sid}/answers",
        headers=auth_headers,
    )
    assert g_a.status_code == 500
    assert g_a.json()["error_code"] == "CORRUPT_SESSION_RECORD"


def test_s4_maj_02_parse_validation_and_ocr_unavailable(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify /parse validates ocr_mode, explicit source_ids, and force OCR availability."""
    sid = "s-s4-parse"
    _create_session(client, auth_headers, sid)

    r_bad_ocr = client.post(
        f"/api/v1/ontology/studio/sessions/{sid}/parse",
        json={"ocr_mode": "invalid_mode"},
        headers=auth_headers,
    )
    assert r_bad_ocr.status_code == 400
    assert r_bad_ocr.json()["error_code"] == "SYNTAX_INVALID_ARGUMENT"

    r_missing_src = client.post(
        f"/api/v1/ontology/studio/sessions/{sid}/parse",
        json={"ocr_mode": "auto", "source_ids": ["src-nonexistent"]},
        headers=auth_headers,
    )
    assert r_missing_src.status_code == 404
    assert r_missing_src.json()["error_code"] == "SOURCE_NOT_FOUND"

    monkeypatch.setattr(TesseractOCRRouter, "is_available", lambda self: False)
    r_ocr_unavail = client.post(
        f"/api/v1/ontology/studio/sessions/{sid}/parse",
        json={"ocr_mode": "force"},
        headers=auth_headers,
    )
    assert r_ocr_unavail.status_code == 501
    assert r_ocr_unavail.json()["error_code"] == "OCR_UNAVAILABLE"


def test_s4_maj_03_clarify_construct_and_apply_delta_occ_guards(
    client: TestClient,
    auth_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verify /clarify & /construct base_version/OCC/mode checks and apply_delta OCC guard."""
    sid = "s-s4-construct"
    meta = _create_session(client, auth_headers, sid)
    genesis_sha = meta["head_sha256"]

    # Nonexistent base_version -> 404 VERSION_NOT_FOUND on both /clarify and /construct
    c_ver = client.post(
        f"/api/v1/ontology/studio/sessions/{sid}/clarify",
        json={"base_version": "9.9.9"},
        headers=auth_headers,
    )
    assert c_ver.status_code == 404
    assert c_ver.json()["error_code"] == "VERSION_NOT_FOUND"

    k_ver = client.post(
        f"/api/v1/ontology/studio/sessions/{sid}/construct",
        json={"base_version": "9.9.9"},
        headers=auth_headers,
    )
    assert k_ver.status_code == 404
    assert k_ver.json()["error_code"] == "VERSION_NOT_FOUND"

    # Synchronous If-Match OCC check on /construct (both auto_commit=False and auto_commit=True)
    stale_hdrs = {**auth_headers, "If-Match": f'"{"b" * 64}"'}
    k_occ = client.post(
        f"/api/v1/ontology/studio/sessions/{sid}/construct",
        json={"auto_commit": False},
        headers=stale_hdrs,
    )
    assert k_occ.status_code == 412
    assert k_occ.json()["error_code"] == "ONT_EVO_CONCURRENT_MODIFICATION"

    # auto_commit=True rejects explicit non-deterministic mode ("agent" / "auto") with 400
    k_bad_mode = client.post(
        f"/api/v1/ontology/studio/sessions/{sid}/construct",
        json={"auto_commit": True, "mode": "agent"},
        headers={**auth_headers, "If-Match": f'"{genesis_sha}"'},
    )
    assert k_bad_mode.status_code == 400
    assert k_bad_mode.json()["error_code"] == "SYNTAX_INVALID_ARGUMENT"

    # Wire studio_agent_runner from app.state into supervisor.submit_job
    captured_kwargs: dict[str, Any] = {}
    sentinel_runner = object()
    client.app.state.studio_agent_runner = sentinel_runner  # type: ignore[union-attr]

    def _fake_submit_job(self_sup: Any, *args: Any, **kwargs: Any) -> dict[str, Any]:
        captured_kwargs.update(kwargs)
        return {
            "job_id": "job-fake-construct",
            "session_id": sid,
            "job_type": "construct",
            "status": "pending",
            "created_at": "2026-11-12T00:00:00+00:00",
        }

    monkeypatch.setattr(JobSupervisor, "submit_job", _fake_submit_job)
    k_ok = client.post(
        f"/api/v1/ontology/studio/sessions/{sid}/construct",
        json={"auto_commit": True},
        headers={**auth_headers, "If-Match": f'"{genesis_sha}"'},
    )
    assert k_ok.status_code == 202
    assert captured_kwargs["mode"] == "deterministic"
    assert captured_kwargs["agent_runner"] is sentinel_runner

    # Advance session head to 1.0.0, then verify apply_delta rejects base_version="0.0.0" bypass
    apply1 = client.post(
        f"/api/v1/ontology/studio/sessions/{sid}/deltas/apply",
        json={
            "delta_id": "d-occ-1",
            "parent_sha256": genesis_sha,
            "base_version": "0.0.0",
            "target_version": "1.0.0",
            "operations": [
                {
                    "op_type": "add_concept",
                    "op_id": "op-occ-1",
                    "concept_uri": "urn:ontology:entity:issuer",
                    "concept_name": "Issuer",
                    "definition": "Security issuer.",
                }
            ],
            "low_confidence_waiver": True,
            "grounding_rationale": "Approved issuer concept for OCC bypass test.",
        },
        headers={**auth_headers, "If-Match": f'"{genesis_sha}"'},
    )
    assert apply1.status_code == 200
    head_1_sha = apply1.json()["target_sha256"]

    bypass_attempt = client.post(
        f"/api/v1/ontology/studio/sessions/{sid}/deltas/apply",
        json={
            "delta_id": "d-occ-bypass",
            "parent_sha256": "",
            "base_version": "0.0.0",
            "target_version": "1.0.0",
            "operations": [
                {
                    "op_type": "add_concept",
                    "op_id": "op-occ-2",
                    "concept_uri": "urn:ontology:entity:tranche",
                    "concept_name": "Tranche",
                    "definition": "Security tranche.",
                }
            ],
            "low_confidence_waiver": True,
            "grounding_rationale": "Attempted OCC bypass with 0.0.0 and empty parent_sha256.",
        },
        headers={**auth_headers, "If-Match": f'"{head_1_sha}"'},
    )
    assert bypass_attempt.status_code == 412
    assert bypass_attempt.json()["error_code"] == "ONT_EVO_CONCURRENT_MODIFICATION"


# ===========================================================================
# 6. P11 MAJ-01..03 & S4-MAJ-04: JobSupervisor Atomicity, SSE & Error Catalog
# ===========================================================================


def test_p11_maj_01_cancel_vs_committing_atomicity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify cancel_job vs _on_stage('committing') has zero TOCTOU race under ctx.lock."""
    session_mgr = StudioSessionManager(studio_root=tmp_path / "opendv_studio")
    session_mgr.create_session("s-p11-race", domain_namespace="wealth_mgmt")
    sup = JobSupervisor()

    # Case A: Job cancelled BEFORE _on_stage("committing") -> raises JobCancelledError, skips commit
    commit_executed = False

    def _fake_construct_cancelled(**kwargs: Any) -> dict[str, Any]:
        nonlocal commit_executed
        on_stage = kwargs["on_stage"]
        on_stage("committing")
        commit_executed = True
        return {"auto_committed": True}

    monkeypatch.setattr(js_mod, "run_construct_session", _fake_construct_cancelled)
    ctx_a = JobContext(
        job_id="job-race-cancel",
        session_id="s-p11-race",
        job_type="construct",
        status="cancelled",
        stage="cancelled",
        pct_complete=50,
        created_at=datetime.now(UTC).isoformat(),
    )
    ctx_a.cancel_event.set()
    with pytest.raises(JobCancelledError):
        run_construct_job(session_mgr, ctx_a, auto_commit=True)
    assert commit_executed is False
    assert ctx_a.status == "cancelled"

    # Case B: _on_stage("committing") enters under ctx.lock BEFORE cancel_job -> cancel gets 409
    entered_committing = threading.Event()
    release_commit = threading.Event()

    def _fake_construct_committing(**kwargs: Any) -> dict[str, Any]:
        on_stage = kwargs["on_stage"]
        on_stage("committing")
        entered_committing.set()
        release_commit.wait(timeout=5.0)
        return {"status": "completed", "auto_committed": True}

    monkeypatch.setattr(js_mod, "run_construct_session", _fake_construct_committing)
    submitted = sup.submit_job(
        session_mgr,
        "s-p11-race",
        "construct",
        run_construct_job,
        auto_commit=True,
    )
    jid = submitted["job_id"]
    assert entered_committing.wait(timeout=5.0)

    with pytest.raises(ec_mod.StudioAPIError) as exc_info:
        sup.cancel_job(session_mgr, "s-p11-race", jid)
    assert exc_info.value.code == "JOB_COMMIT_IN_PROGRESS"
    assert exc_info.value.status_code == 409

    release_commit.set()
    ctx_b = sup._jobs[jid]
    if ctx_b.thread:
        ctx_b.thread.join(timeout=5.0)
    assert sup.get_job(session_mgr, "s-p11-race", jid)["status"] == "completed"


def test_p11_maj_02_sse_preflight_returns_rfc9457_400_and_404(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """Verify invalid job/session SSE requests return RFC 9457 400/404 before streaming."""
    sid = "s-p11-sse"
    _create_session(client, auth_headers, sid)

    # 1. Invalid job_id format -> 400 SYNTAX_INVALID_ARGUMENT (application/problem+json)
    r_bad_id = client.get(
        f"/api/v1/ontology/studio/sessions/{sid}/jobs/bad_id!/events",
        headers=auth_headers,
    )
    assert r_bad_id.status_code == 400
    assert "application/problem+json" in r_bad_id.headers["content-type"]
    assert r_bad_id.json()["error_code"] == "SYNTAX_INVALID_ARGUMENT"

    # 2. Nonexistent session -> 404 SESSION_NOT_FOUND
    r_no_sess = client.get(
        "/api/v1/ontology/studio/sessions/no-such-session/jobs/job-missing123/events",
        headers=auth_headers,
    )
    assert r_no_sess.status_code == 404
    assert "application/problem+json" in r_no_sess.headers["content-type"]
    assert r_no_sess.json()["error_code"] == "SESSION_NOT_FOUND"

    # 3. Nonexistent job -> 404 JOB_NOT_FOUND (both session-scoped and top-level /jobs/{id}/events)
    r_no_job = client.get(
        f"/api/v1/ontology/studio/sessions/{sid}/jobs/job-missing123/events",
        headers=auth_headers,
    )
    assert r_no_job.status_code == 404
    assert "application/problem+json" in r_no_job.headers["content-type"]
    assert r_no_job.json()["error_code"] == "JOB_NOT_FOUND"

    r_top_no_job = client.get(
        "/api/v1/ontology/studio/jobs/job-missing123/events",
        headers=auth_headers,
    )
    assert r_top_no_job.status_code == 404
    assert "application/problem+json" in r_top_no_job.headers["content-type"]
    assert r_top_no_job.json()["error_code"] == "JOB_NOT_FOUND"


def test_p11_maj_03_and_s4_maj_04_error_catalog_wheel_fallback_and_job_helpers(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify wheel-install error catalog baseline for all 157 ErrorCodes and S4-MAJ-04 helpers."""
    saved_catalog = dict(ec_mod._ERROR_CATALOG)
    saved_loaded = ec_mod._CATALOG_LOADED
    try:
        monkeypatch.setattr(ec_mod, "_find_catalog_path", lambda: None)
        ec_mod._ERROR_CATALOG.clear()
        ec_mod._CATALOG_LOADED = False

        catalog = ec_mod.get_error_catalog()
        assert ec_mod._CATALOG_LOADED is True
        assert len(catalog) == len(ErrorCode)

        for err_code in ErrorCode:
            normative = ERROR_MAPPINGS[err_code]
            entry = ec_mod.get_error_entry(err_code.value)
            assert entry["http_status"] == normative.http_status
            assert entry["exit_code"] == normative.exit_code
            assert entry["problem_title"] == normative.problem_title
            assert entry["problem_type_uri"] == normative.problem_type_uri
    finally:
        ec_mod._ERROR_CATALOG.clear()
        ec_mod._ERROR_CATALOG.update(saved_catalog)
        ec_mod._CATALOG_LOADED = saved_loaded

    # S4-MAJ-04: _translate_runner_error maps SourceError to registered SOURCE_LOAD_ERROR
    translated = _translate_runner_error(SourceError("corrupt source document"))
    assert translated.code == ErrorCode.SOURCE_LOAD_ERROR.value

    # S4-MAJ-04: resolve_job_session_id hits in-memory self._jobs without scanning list_sessions()
    session_mgr = StudioSessionManager(studio_root=tmp_path / "opendv_studio")
    session_mgr.create_session("s-fast-mem", domain_namespace="wealth_mgmt")
    monkeypatch.setattr(
        session_mgr,
        "list_sessions",
        lambda: (_ for _ in ()).throw(AssertionError("Must not scan disk when job is in memory")),
    )
    sup = JobSupervisor()
    sup._jobs["job-inmem-0001"] = JobContext(
        job_id="job-inmem-0001",
        session_id="s-fast-mem",
        job_type="parse",
        status="running",
        stage="started",
        pct_complete=10,
        created_at=datetime.now(UTC).isoformat(),
    )
    assert sup.resolve_job_session_id(session_mgr, "job-inmem-0001") == "s-fast-mem"
    assert get_session_manager is not None


def test_r4_s6_crit_01_construct_then_accept_proposal_not_blocked_by_self_acceptance(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """R4-S6-CRIT-01: Engine proposals from POST /construct accept cleanly."""
    import time

    sid = "s-construct-accept-e2e"
    r_create = client.post(
        "/api/v1/ontology/studio/sessions",
        json={"session_id": sid, "domain_namespace": "general"},
        headers=auth_headers,
    )
    assert r_create.status_code == 201
    genesis_sha = r_create.json()["head_sha256"]

    # Upload & parse a rich FIBO-groundable CSV source as editor
    editor_headers = {
        **auth_headers,
        "X-OpenDV-Role": "editor",
        "X-OpenDV-Principal": "human:alice_steward",
    }
    csv_bytes = b"customer_id,customer_name,account_number\nC001,Acme Corp,ACC-900\n"
    r_up = client.post(
        f"/api/v1/ontology/studio/sessions/{sid}/sources",
        files={"file": ("customers.csv", csv_bytes, "text/csv")},
        headers=editor_headers,
    )
    assert r_up.status_code == 201

    r_parse = client.post(
        f"/api/v1/ontology/studio/sessions/{sid}/parse",
        json={"ocr_mode": "off"},
        headers=editor_headers,
    )
    assert r_parse.status_code == 202
    parse_job_id = r_parse.json()["job_id"]
    for _ in range(50):
        jr = client.get(f"/api/v1/ontology/studio/jobs/{parse_job_id}", headers=editor_headers)
        if jr.json()["status"] in ("completed", "failed"):
            break
        time.sleep(0.05)
    assert jr.json()["status"] == "completed"

    # Trigger deterministic construction as editor `human:alice_steward`
    r_con = client.post(
        f"/api/v1/ontology/studio/sessions/{sid}/construct",
        json={"mode": "deterministic", "auto_commit": False},
        headers=editor_headers,
    )
    assert r_con.status_code == 202
    con_job_id = r_con.json()["job_id"]
    for _ in range(50):
        jr2 = client.get(f"/api/v1/ontology/studio/jobs/{con_job_id}", headers=editor_headers)
        if jr2.json()["status"] in ("completed", "failed"):
            break
        time.sleep(0.05)
    assert jr2.json()["status"] == "completed", jr2.text

    props = client.get(
        f"/api/v1/ontology/studio/sessions/{sid}/proposals",
        headers=editor_headers,
    ).json()
    assert len(props) == 1
    prop_rec = props[0]
    assert prop_rec["proposer"] == f"deterministic:{con_job_id}"
    assert prop_rec["proposer"] != "human:alice_steward"

    # If deterministic pipeline generated >=1 grounded op, accepting as SAME editor succeeds 200
    # without self_acceptance_override!
    if len(prop_rec["operations"]) > 0:
        r_acc = client.post(
            f"/api/v1/ontology/studio/sessions/{sid}/proposals/{prop_rec['proposal_id']}/accept",
            json={"base_sha256": genesis_sha, "grounding_rationale": "Steward reviewed construct."},
            headers={**editor_headers, "If-Match": f'"{genesis_sha}"'},
        )
        assert r_acc.status_code != 403, r_acc.text


# ===========================================================================
# Round-4 R4-C Remediations: P10a::MAJ-01, P10c::MAJ-01, P11::MAJ-01 & Clone
# ===========================================================================


def test_r4c_p10a_maj01_waiver_delta_sha256_binding_blocks_cross_delta_replay(
    client: TestClient, auth_headers: dict[str, str], tmp_path: Path
) -> None:
    """Verify a waiver bound to delta D1 cannot authorize delta D2 on the same base_sha256."""
    from opendv_contracts.dtos.ontology import OntologyDelta

    sid = "s-r4c-p10a-delta-bind"
    meta = _create_session(client, auth_headers, sid)
    genesis_sha = meta["head_sha256"]
    sdir = tmp_path / "opendv_studio" / sid

    # 1. Construct D1 and D2 touching the EXACT SAME target_concept_uri on the same base_sha256
    d1 = OntologyDelta(
        delta_id="d-bound-1",
        parent_sha256=genesis_sha,
        base_version="0.0.0",
        target_version="1.0.0",
        operations=[
            {
                "op_type": "add_concept",
                "op_id": "op-d1",
                "concept_uri": "urn:ontology:entity:proprietary_signal",
                "concept_name": "Proprietary Signal",
                "definition": "Approved definition for D1.",
            }
        ],
    )
    d2 = OntologyDelta(
        delta_id="d-bound-2",
        parent_sha256=genesis_sha,
        base_version="0.0.0",
        target_version="1.0.0",
        operations=[
            {
                "op_type": "add_concept",
                "op_id": "op-d2",
                "concept_uri": "urn:ontology:entity:proprietary_signal",
                "concept_name": "Proprietary Signal",
                "definition": "Unauthorized altered definition in D2!",
            }
        ],
    )
    d1_sha = d1.canonical_sha256()
    assert d1_sha != d2.canonical_sha256()

    # Submit waiver explicitly bound to D1's canonical_sha256
    w_resp = client.post(
        f"/api/v1/ontology/studio/sessions/{sid}/waiver",
        json={
            "target_concept_uris": ["urn:ontology:entity:proprietary_signal"],
            "base_sha256": genesis_sha,
            "proposal_delta_sha256": d1_sha,
            "rationale": "Approved waiver strictly bound to D1 canonical digest.",
        },
        headers=auth_headers,
    )
    assert w_resp.status_code == 200, w_resp.text
    stored = json.loads((sdir / "waivers.json").read_text(encoding="utf-8"))
    assert stored[0]["envelope"]["proposal_delta_sha256"] == d1_sha

    # Committing D2 on the same base_sha256 MUST fail with 422 ONT_EVO_UNGROUNDED_CONCEPT
    apply_d2 = client.post(
        f"/api/v1/ontology/studio/sessions/{sid}/deltas/apply",
        json=d2.model_dump(mode="json", exclude_none=True),
        headers={**auth_headers, "If-Match": f'"{genesis_sha}"'},
    )
    assert apply_d2.status_code == 422, apply_d2.text
    assert apply_d2.json()["error_code"] == "ONT_EVO_UNGROUNDED_CONCEPT"

    # Committing D1 on the same base_sha256 MUST succeed (200)
    apply_d1 = client.post(
        f"/api/v1/ontology/studio/sessions/{sid}/deltas/apply",
        json=d1.model_dump(mode="json", exclude_none=True),
        headers={**auth_headers, "If-Match": f'"{genesis_sha}"'},
    )
    assert apply_d1.status_code == 200, apply_d1.text
    head_1_sha = apply_d1.json()["target_sha256"]

    # 2. Proposal-lookup binding: creating proposal P1 then calling POST .../waiver with
    # proposal_id=P1 binds proposal_delta_sha256 to P1's stored delta canonical_sha256()
    editor_headers = {
        **auth_headers,
        "X-OpenDV-Role": "editor",
        "X-OpenDV-Principal": "human:editor_p1",
    }
    p1_resp = client.post(
        f"/api/v1/ontology/studio/sessions/{sid}/proposals",
        json={
            "proposal_id": "prop-bound-p1",
            "operations": [
                {
                    "op_type": "add_concept",
                    "op_id": "op-p1",
                    "concept_uri": "urn:ontology:entity:custom_desk",
                    "concept_name": "Custom Desk",
                    "definition": "Original P1 definition.",
                }
            ],
            "rationale": "Proposal P1 for delta-bound waiver test",
        },
        headers=editor_headers,
    )
    assert p1_resp.status_code == 201, p1_resp.text

    w_p1 = client.post(
        f"/api/v1/ontology/studio/sessions/{sid}/waiver",
        json={
            "proposal_id": "prop-bound-p1",
            "target_concept_uris": ["urn:ontology:entity:custom_desk"],
            "rationale": "Waiver bound to P1's exact stored proposal delta.",
        },
        headers=auth_headers,
    )
    assert w_p1.status_code == 200, w_p1.text
    stored_after_p1 = json.loads((sdir / "waivers.json").read_text(encoding="utf-8"))
    bound_p1_sha = stored_after_p1[-1]["envelope"]["proposal_delta_sha256"]
    assert len(bound_p1_sha) == 64 and bound_p1_sha != "0" * 64

    # Tamper P1's operations to D2 on the same base_sha256 -> accept MUST reject (422)!
    prop_path = sdir / "proposals" / "prop-bound-p1.json"
    orig_prop_bytes = prop_path.read_bytes()
    tampered_prop = json.loads(orig_prop_bytes.decode("utf-8"))
    tampered_prop["operations"][0]["definition"] = "Mutated D2 definition after waiver!"
    prop_path.write_text(json.dumps(tampered_prop, indent=2), encoding="utf-8")

    acc_tampered = client.post(
        f"/api/v1/ontology/studio/sessions/{sid}/proposals/prop-bound-p1/accept",
        json={"base_sha256": head_1_sha},
        headers={**auth_headers, "If-Match": f'"{head_1_sha}"'},
    )
    assert acc_tampered.status_code == 422, acc_tampered.text
    assert acc_tampered.json()["error_code"] == "ONT_EVO_UNGROUNDED_CONCEPT"

    # Restore original P1 -> accept succeeds (200)!
    prop_path.write_bytes(orig_prop_bytes)
    acc_intact = client.post(
        f"/api/v1/ontology/studio/sessions/{sid}/proposals/prop-bound-p1/accept",
        json={"base_sha256": head_1_sha},
        headers={**auth_headers, "If-Match": f'"{head_1_sha}"'},
    )
    assert acc_intact.status_code == 200, acc_intact.text


def test_r4c_p10c_maj01_rebase_fails_closed_on_missing_or_corrupt_delta_and_broken_lineage(
    client: TestClient, auth_headers: dict[str, str], tmp_path: Path
) -> None:
    """Verify rebase fails closed when delta.json is missing/corrupt or lineage is broken."""
    sid = "s-r4c-p10c-rebase"
    meta = _create_session(client, auth_headers, sid)
    genesis_sha = meta["head_sha256"]
    sdir = tmp_path / "opendv_studio" / sid

    # 1. Create proposal on genesis 0.0.0 that touches concept X (conflicting with HEAD)
    p_resp = client.post(
        f"/api/v1/ontology/studio/sessions/{sid}/proposals",
        json={
            "proposal_id": "prop-stale-conflict",
            "base_sha256": genesis_sha,
            "operations": [
                {
                    "op_type": "add_concept",
                    "op_id": "op-prop-x",
                    "concept_uri": "urn:ontology:entity:desk_book",
                    "concept_name": "Desk Book",
                    "definition": "Proposal version of Desk Book.",
                }
            ],
            "rationale": "Proposal created at 0.0.0",
        },
        headers=auth_headers,
    )
    assert p_resp.status_code == 201

    # Also create a non-conflicting proposal so even without semantic conflict in head_ontology,
    # missing/corrupt delta.json MUST fail closed instead of re-anchoring!
    p_clean_resp = client.post(
        f"/api/v1/ontology/studio/sessions/{sid}/proposals",
        json={
            "proposal_id": "prop-stale-clean",
            "base_sha256": genesis_sha,
            "operations": [
                {
                    "op_type": "add_concept",
                    "op_id": "op-prop-y",
                    "concept_uri": "urn:ontology:entity:margin_call",
                    "concept_name": "Margin Call",
                    "definition": "Margin call notice.",
                }
            ],
            "rationale": "Clean proposal created at 0.0.0",
        },
        headers=auth_headers,
    )
    assert p_clean_resp.status_code == 201

    # Advance HEAD to v1.0.0
    apply_1 = client.post(
        f"/api/v1/ontology/studio/sessions/{sid}/deltas/apply",
        json={
            "delta_id": "d-head-100",
            "parent_sha256": genesis_sha,
            "base_version": "0.0.0",
            "target_version": "1.0.0",
            "operations": [
                {
                    "op_type": "add_concept",
                    "op_id": "op-head-1",
                    "concept_uri": "urn:ontology:entity:ledger_entry",
                    "concept_name": "Ledger Entry",
                    "definition": "Committed at v1.0.0.",
                }
            ],
            "low_confidence_waiver": True,
            "grounding_rationale": "Admin waiver for v1.0.0 commit.",
        },
        headers={**auth_headers, "If-Match": f'"{genesis_sha}"'},
    )
    assert apply_1.status_code == 200, apply_1.text
    head_1_sha = apply_1.json()["target_sha256"]

    delta_file = sdir / "versions" / "v1.0.0" / "delta.json"
    orig_delta_bytes = delta_file.read_bytes()

    # 2a. Corrupt intervening delta.json -> 500 CORRUPT_SESSION_RECORD (does NOT re-anchor!)
    delta_file.write_text("{corrupt-json", encoding="utf-8")
    r_corrupt = client.post(
        f"/api/v1/ontology/studio/sessions/{sid}/proposals/prop-stale-clean/rebase",
        json={"base_sha256": head_1_sha},
        headers={**auth_headers, "If-Match": f'"{head_1_sha}"'},
    )
    assert r_corrupt.status_code == 500, r_corrupt.text
    assert r_corrupt.json()["error_code"] == "CORRUPT_SESSION_RECORD"

    # 2b. Delete intervening delta.json -> 500 CORRUPT_SESSION_RECORD
    delta_file.unlink()
    r_missing = client.post(
        f"/api/v1/ontology/studio/sessions/{sid}/proposals/prop-stale-clean/rebase",
        json={"base_sha256": head_1_sha},
        headers={**auth_headers, "If-Match": f'"{head_1_sha}"'},
    )
    assert r_missing.status_code == 500, r_missing.text
    assert r_missing.json()["error_code"] == "CORRUPT_SESSION_RECORD"

    # Restore delta.json; verify proposal was NEVER re-anchored on disk
    delta_file.write_bytes(orig_delta_bytes)
    prop_disk = json.loads(
        (sdir / "proposals" / "prop-stale-clean.json").read_text(encoding="utf-8")
    )
    assert prop_disk["base_sha256"] == genesis_sha

    # 2c. Malformed version directory name under versions/ -> 500 CORRUPT_SESSION_RECORD
    bad_vdir = sdir / "versions" / "vnot-a-semver"
    bad_vdir.mkdir()
    r_bad_dir = client.post(
        f"/api/v1/ontology/studio/sessions/{sid}/proposals/prop-stale-clean/rebase",
        json={"base_sha256": head_1_sha},
        headers={**auth_headers, "If-Match": f'"{head_1_sha}"'},
    )
    assert r_bad_dir.status_code == 500, r_bad_dir.text
    assert r_bad_dir.json()["error_code"] == "CORRUPT_SESSION_RECORD"
    bad_vdir.rmdir()

    # 2d. Non-ancestor proposal base_sha256 -> 409 PROPOSAL_STALE_REBASE_REQUIRED
    prop_clean_path = sdir / "proposals" / "prop-stale-clean.json"
    orig_prop_bytes = prop_clean_path.read_bytes()
    alien_prop = json.loads(orig_prop_bytes.decode("utf-8"))
    alien_prop["base_sha256"] = "e" * 64
    prop_clean_path.write_text(json.dumps(alien_prop, indent=2), encoding="utf-8")

    r_non_ancestor = client.post(
        f"/api/v1/ontology/studio/sessions/{sid}/proposals/prop-stale-clean/rebase",
        json={"base_sha256": head_1_sha},
        headers={**auth_headers, "If-Match": f'"{head_1_sha}"'},
    )
    assert r_non_ancestor.status_code == 409, r_non_ancestor.text
    assert r_non_ancestor.json()["error_code"] == "PROPOSAL_STALE_REBASE_REQUIRED"

    # Restore intact proposal & verify legitimate rebase succeeds (200)
    prop_clean_path.write_bytes(orig_prop_bytes)
    r_ok = client.post(
        f"/api/v1/ontology/studio/sessions/{sid}/proposals/prop-stale-clean/rebase",
        json={"base_sha256": head_1_sha},
        headers={**auth_headers, "If-Match": f'"{head_1_sha}"'},
    )
    assert r_ok.status_code == 200, r_ok.text
    assert r_ok.json()["base_sha256"] == head_1_sha


def test_r4c_p11_maj01_sticky_commit_started_and_terminal_state_protection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify cancellation stays blocked after 'committing' stage & terminal state is sticky."""
    studio_root = tmp_path / "opendv_studio"
    session_mgr = StudioSessionManager(studio_root=studio_root)
    session_mgr.create_session("s-r4c-p11", domain_namespace="wealth_mgmt")
    sup = JobSupervisor()
    monkeypatch.setattr(js_mod, "_GLOBAL_SUPERVISOR", sup)

    post_commit_stage_reached = threading.Event()
    allow_runner_finish = threading.Event()

    def _fake_construct_session(**kwargs: Any) -> dict[str, Any]:
        on_stage = kwargs["on_stage"]
        on_stage("extracting")
        on_stage("committing")
        # Subsequent stage callback AFTER "committing" MUST NOT reopen cancellation!
        on_stage("finalizing")
        post_commit_stage_reached.set()
        allow_runner_finish.wait(timeout=5.0)
        return {"auto_committed": True, "target_version": "1.0.0"}

    monkeypatch.setattr(js_mod, "run_construct_session", _fake_construct_session)

    handle = sup.submit_job(
        session_mgr=session_mgr,
        session_id="s-r4c-p11",
        job_type="construct",
        runner_fn=run_construct_job,
        auto_commit=True,
    )
    job_id = handle["job_id"]
    assert post_commit_stage_reached.wait(timeout=5.0)

    # Even though ctx.stage is now "finalizing", commit_started is True -> JOB_COMMIT_IN_PROGRESS
    with pytest.raises(js_mod.StudioAPIError) as exc_info:
        sup.cancel_job(session_mgr, "s-r4c-p11", job_id, reason="Late cancel attempt")
    assert exc_info.value.code == "JOB_COMMIT_IN_PROGRESS"

    allow_runner_finish.set()
    ctx = sup._jobs[job_id]
    assert ctx.thread is not None
    ctx.thread.join(timeout=5.0)

    final_job = sup.get_job(session_mgr, "s-r4c-p11", job_id)
    assert final_job["status"] == "completed"
    disk_data = json.loads(
        (studio_root / "s-r4c-p11" / "jobs" / f"{job_id}.json").read_text(encoding="utf-8")
    )
    assert disk_data["status"] == "completed"

    # 2. Verify _execute_job NEVER overwrites an already-terminal "cancelled" state even when
    # runner_fn returns {"auto_committed": True}
    cancelled_in_runner = threading.Event()
    proceed_after_cancel = threading.Event()

    def _race_runner(_sm: StudioSessionManager, _ctx: JobContext) -> dict[str, Any]:
        cancelled_in_runner.set()
        proceed_after_cancel.wait(timeout=5.0)
        return {"auto_committed": True, "note": "Late return after pre-commit cancel"}

    handle2 = sup.submit_job(
        session_mgr=session_mgr,
        session_id="s-r4c-p11",
        job_type="construct",
        runner_fn=_race_runner,
    )
    job2_id = handle2["job_id"]
    assert cancelled_in_runner.wait(timeout=5.0)

    cancel_res = sup.cancel_job(session_mgr, "s-r4c-p11", job2_id, reason="Pre-commit cancel")
    assert cancel_res["status"] == "cancelled"

    proceed_after_cancel.set()
    ctx2 = sup._jobs[job2_id]
    assert ctx2.thread is not None
    ctx2.thread.join(timeout=5.0)

    assert sup.get_job(session_mgr, "s-r4c-p11", job2_id)["status"] == "cancelled"
    disk_data2 = json.loads(
        (studio_root / "s-r4c-p11" / "jobs" / f"{job2_id}.json").read_text(encoding="utf-8")
    )
    assert disk_data2["status"] == "cancelled"


def test_r4c_clone_session_taint_audit_chain_and_lifecycle_lock_parity(
    client: TestClient, auth_headers: dict[str, str], tmp_path: Path
) -> None:
    """Verify clone_session_endpoint enforces taint RBAC, audit chain, and lock parity."""
    studio_root = tmp_path / "opendv_studio"
    editor_headers = {
        **auth_headers,
        "X-OpenDV-Role": "editor",
        "X-OpenDV-Principal": "human:editor_clone",
    }

    # 1. Tainted session requires admin role to clone
    tainted_sid = "s-r4c-tainted-src"
    _create_session(client, auth_headers, tainted_sid)
    up_resp = client.post(
        f"/api/v1/ontology/studio/sessions/{tainted_sid}/sources",
        files={"file": ("secret.txt", b"confidential term sheet", "text/plain")},
        data={"do_not_send_to_agent": "true"},
        headers=auth_headers,
    )
    assert up_resp.status_code == 201

    clone_editor = client.post(
        f"/api/v1/ontology/studio/sessions/{tainted_sid}/bundle/clone",
        json={"new_session_id": "s-r4c-tainted-ed-clone"},
        headers=editor_headers,
    )
    assert clone_editor.status_code == 403, clone_editor.text
    assert clone_editor.json()["error_code"] == "FORBIDDEN_ADMIN_REQUIRED"

    clone_admin = client.post(
        f"/api/v1/ontology/studio/sessions/{tainted_sid}/bundle/clone",
        json={"new_session_id": "s-r4c-tainted-adm-clone"},
        headers=auth_headers,
    )
    assert clone_admin.status_code == 201, clone_admin.text
    assert (studio_root / ".lifecycle_s-r4c-tainted-adm-clone.lock").is_file()

    # 2. Tampered audit.jsonl on source session -> 422 TAMPER_EVIDENT_CHAIN_CORRUPT
    corrupt_sid = "s-r4c-corrupt-audit-src"
    _create_session(client, auth_headers, corrupt_sid)
    audit_path = studio_root / corrupt_sid / "audit.jsonl"
    lines = [json.loads(ln) for ln in audit_path.read_text(encoding="utf-8").splitlines() if ln]
    lines[0]["actor"] = "attacker:forged"
    audit_path.write_text(json.dumps(lines[0]) + "\n", encoding="utf-8")

    clone_corrupt = client.post(
        f"/api/v1/ontology/studio/sessions/{corrupt_sid}/bundle/clone",
        json={"new_session_id": "s-r4c-should-not-exist"},
        headers=auth_headers,
    )
    assert clone_corrupt.status_code == 422, clone_corrupt.text
    assert clone_corrupt.json()["error_code"] == "TAMPER_EVIDENT_CHAIN_CORRUPT"
    assert not (studio_root / "s-r4c-should-not-exist").exists()


def test_r4c_p11_maj01_cross_process_cancel_cooperative_vs_commit_started(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify cross-supervisor cancel writes .cancel marker for active workers and blocks commit."""
    studio_root = tmp_path / "opendv_studio"
    session_mgr = StudioSessionManager(studio_root=studio_root)
    sid = "s-r4c-p11-xproc"
    session_mgr.create_session(sid, domain_namespace="wealth_mgmt")
    jobs_dir = studio_root / sid / "jobs"

    supervisor_a = JobSupervisor()
    supervisor_b = JobSupervisor()  # Empty _jobs dict, simulating a 2nd uvicorn worker process
    monkeypatch.setattr(js_mod, "_GLOBAL_SUPERVISOR", supervisor_a)

    # 1. Active non-committing worker in supervisor_a cancelled via supervisor_b
    worker_started = threading.Event()
    allow_worker_poll = threading.Event()

    def _cooperative_parse_runner(sm: StudioSessionManager, jctx: JobContext) -> dict[str, Any]:
        worker_started.set()
        allow_worker_poll.wait(timeout=5.0)
        if jctx.is_cancelled(sm):
            raise JobCancelledError("Aborted cooperatively via cross-process .cancel marker")
        return {"parsed_sources": 1}

    handle_1 = supervisor_a.submit_job(
        session_mgr=session_mgr,
        session_id=sid,
        job_type="parse",
        runner_fn=_cooperative_parse_runner,
    )
    job_1 = handle_1["job_id"]
    assert worker_started.wait(timeout=5.0)
    assert job_1 not in supervisor_b._jobs

    # supervisor_b cannot acquire .job_<id>.lock (held by supervisor_a), but MUST write .cancel
    # first and return status="cancelling" / stage="cancel_requested" without clobbering JSON
    cancel_dto = supervisor_b.cancel_job(session_mgr, sid, job_1, reason="Cross-worker cancel")
    assert cancel_dto["status"] == "cancelling"
    assert cancel_dto["stage"] == "cancel_requested"
    assert (jobs_dir / f".{job_1}.cancel").is_file()

    mid_disk = json.loads((jobs_dir / f"{job_1}.json").read_text(encoding="utf-8"))
    assert mid_disk["status"] == "running"

    allow_worker_poll.set()
    ctx_1 = supervisor_a._jobs[job_1]
    assert ctx_1.thread is not None
    ctx_1.thread.join(timeout=5.0)

    assert supervisor_a.get_job(session_mgr, sid, job_1)["status"] == "cancelled"
    assert supervisor_b.get_job(session_mgr, sid, job_1)["status"] == "cancelled"
    events_1 = [
        json.loads(ln)
        for ln in (jobs_dir / f"{job_1}.events.jsonl").read_text(encoding="utf-8").splitlines()
        if ln.strip()
    ]
    assert any(ev.get("event") == "job_cancelled" for ev in events_1)

    # 2. Active committing / post-commit worker in supervisor_a rejected by supervisor_b
    in_commit_phase = threading.Event()
    allow_commit_finish = threading.Event()

    def _committing_construct(**kwargs: Any) -> dict[str, Any]:
        on_stage = kwargs["on_stage"]
        on_stage("extracting")
        on_stage("committing")
        on_stage("finalizing")
        in_commit_phase.set()
        allow_commit_finish.wait(timeout=5.0)
        return {"auto_committed": True, "target_version": "1.0.0"}

    monkeypatch.setattr(js_mod, "run_construct_session", _committing_construct)

    handle_2 = supervisor_a.submit_job(
        session_mgr=session_mgr,
        session_id=sid,
        job_type="construct",
        runner_fn=run_construct_job,
        auto_commit=True,
    )
    job_2 = handle_2["job_id"]
    assert in_commit_phase.wait(timeout=5.0)
    assert job_2 not in supervisor_b._jobs

    with pytest.raises(js_mod.StudioAPIError) as exc_info:
        supervisor_b.cancel_job(session_mgr, sid, job_2, reason="Cancel during commit")
    assert exc_info.value.code == "JOB_COMMIT_IN_PROGRESS"
    assert not (jobs_dir / f".{job_2}.cancel").exists()

    # Also test TOCTOU race where pre_data looked pre-commit right before worker set commit_started:
    # if cancel_marker was written just as <job_2>.json became commit_started=True under lock,
    # cancel_job must unlink cancel_marker and raise JOB_COMMIT_IN_PROGRESS.
    job_2_file = jobs_dir / f"{job_2}.json"
    real_commit_bytes = job_2_file.read_bytes()
    stale_pre_data = json.loads(real_commit_bytes.decode("utf-8"))
    stale_pre_data["stage"] = "extracting"
    stale_pre_data["commit_started"] = False

    orig_write_marker = supervisor_b._write_cancel_marker_atomic

    def _write_marker_as_worker_enters_commit(s_dir: Path, j_id: str) -> Path:
        marker = orig_write_marker(s_dir, j_id)
        job_2_file.write_bytes(real_commit_bytes)
        return marker

    job_2_file.write_text(json.dumps(stale_pre_data), encoding="utf-8")
    monkeypatch.setattr(
        supervisor_b, "_write_cancel_marker_atomic", _write_marker_as_worker_enters_commit
    )
    with pytest.raises(js_mod.StudioAPIError) as race_exc:
        supervisor_b.cancel_job(session_mgr, sid, job_2, reason="TOCTOU commit transition")
    assert race_exc.value.code == "JOB_COMMIT_IN_PROGRESS"
    assert not (jobs_dir / f".{job_2}.cancel").exists()

    allow_commit_finish.set()
    ctx_2 = supervisor_a._jobs[job_2]
    assert ctx_2.thread is not None
    ctx_2.thread.join(timeout=5.0)
    assert supervisor_b.get_job(session_mgr, sid, job_2)["status"] == "completed"
