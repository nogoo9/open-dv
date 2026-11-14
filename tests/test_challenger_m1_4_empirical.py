"""open-dv/tests/test_challenger_m1_4_empirical.py

Milestone M1 Iteration 2 Independent Empirical Challenger Suite.
Authored by challenger_m1_4 to stress-test:
1. Candidate promotion op_id validation (Path A and Path B)
2. Staged list, text add, starter pack apply across Python and Bun CLIs
3. Proposal refinement state machine and HTTP 409 rejection of SUPERSEDED proposals
4. Concurrency stress testing on proposal refinement
"""

from __future__ import annotations

import concurrent.futures
import json
import os
import re
import subprocess
import uuid
from pathlib import Path
from typing import Any

import pytest
from starlette.testclient import TestClient

from opendv_contracts.dtos.ontology import (
    AddAttributeOp,
    AddConceptOp,
    AddExternalMappingOp,
    AddRelationshipOp,
    OntologyDelta,
)
from opendv_contracts.dtos.promotion import PromoteCandidateRequest
from opendv_contracts.dtos.starter_packs import ApplyStarterPackRequest
from opendv_contracts.errors import ErrorCode, OpenDVError
from opendv_platform_api.main import create_app
from opendv_service import OpenDVSettings
from opendv_service.studio.promoter import StagedCandidatePromoter
from opendv_service.studio.session_manager import StudioSessionManager

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def auth_api_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[TestClient, dict[str, str], dict[str, str], dict[str, str], Path]:
    """Create isolated FastAPI client with loopback caller and admin/editor/viewer credentials."""
    studio_root = tmp_path / "opendv_studio"
    studio_root.mkdir(parents=True, exist_ok=True)
    ws_root = tmp_path / "workspace"
    ws_root.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("OPENDV_STUDIO_ROOT", str(studio_root))
    monkeypatch.setenv("OPENDV_WORKSPACE_ROOT", str(ws_root))
    monkeypatch.setenv("OPENDV_SERVER_MODE", "local_standalone")

    settings = OpenDVSettings.load(
        workspace_root=ws_root,
        state_dir=tmp_path / "state",
        is_server=True,
    )
    app = create_app(settings=settings)
    client = TestClient(app, client=("127.0.0.1", 50000), base_url="http://localhost:8000")

    init_res = client.post(
        "/api/v1/ontology/studio/init",
        json={"domain_namespace": "asset_management", "enterprise_owner": "Enterprise Architecture"},
    )
    assert init_res.status_code == 200, init_res.text
    token = init_res.json()["local_token"]

    admin_headers = {"Authorization": f"Bearer {token}", "X-OpenDV-Role": "admin"}
    editor_headers = {"Authorization": f"Bearer {token}", "X-OpenDV-Role": "editor"}
    viewer_headers = {"Authorization": f"Bearer {token}", "X-OpenDV-Role": "viewer"}

    return client, admin_headers, editor_headers, viewer_headers, studio_root


# ==============================================================================
# SECTION 1: CANDIDATE PROMOTION OP_ID VALIDATION & DELTA INTEGRITY
# ==============================================================================


class TestCandidatePromotionOpIdValidation:
    """Stress tests candidate promotion ensuring op_id is valid, non-empty, and unique."""

    def test_path_a_promotion_op_id_and_delta_serialization(
        self, auth_api_context: tuple[TestClient, dict[str, str], dict[str, str], dict[str, str], Path]
    ) -> None:
        """Verify Path A candidate promotion produces valid op_id on all generated delta operations."""
        client, admin_headers, _, _, studio_root = auth_api_context
        sid = f"sess_cand_pa_{uuid.uuid4().hex[:8]}"
        res = client.post(
            "/api/v1/ontology/studio/sessions",
            json={"session_id": sid, "domain_namespace": "asset_management"},
            headers=admin_headers,
        )
        assert res.status_code in (200, 201)

        sdir = studio_root / sid
        ev_dir = sdir / "evidence"
        ev_dir.mkdir(parents=True, exist_ok=True)
        staged_payload = {
            "staged_candidates": [
                {
                    "candidate_id": "cand-fibo-bond",
                    "concept_name": "SovereignBond",
                    "suggested_hub": "hub_sovereign_bond",
                    "score": 0.62,
                    "matched_fibo_uri": "https://spec.edmcouncil.org/fibo/ontology/SEC/Debt/Bonds/SovereignBond",
                }
            ]
        }
        (ev_dir / "staged_candidates.json").write_text(json.dumps(staged_payload), encoding="utf-8")

        # Promote via Path A with matching synonym to cross threshold
        resp = client.post(
            f"/api/v1/ontology/studio/sessions/{sid}/staged-candidates/cand-fibo-bond/promote",
            json={"path": "path_a", "synonyms": ["Government Bond", "Treasury Bond"]},
            headers=admin_headers,
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data.get("promoted") is True
        assert data.get("status") == "PROMOTED"

        # Verify operations
        ops = data.get("operations", [])
        assert len(ops) >= 1
        seen_op_ids: set[str] = set()
        for op in ops:
            op_id = op.get("op_id")
            assert op_id is not None, f"Operation missing op_id: {op}"
            assert isinstance(op_id, str) and len(op_id) > 5, f"Invalid op_id: {op_id}"
            assert re.match(r"^op-[a-z0-9_-]+$", op_id), f"op_id format invalid: {op_id}"
            assert op_id not in seen_op_ids, f"Duplicate op_id found: {op_id}"
            seen_op_ids.add(op_id)

    def test_path_b_promotion_admin_waiver_op_id_and_permissions(
        self, auth_api_context: tuple[TestClient, dict[str, str], dict[str, str], dict[str, str], Path]
    ) -> None:
        """Verify Path B admin waiver candidate promotion enforces RBAC, rationale, and valid op_ids."""
        client, admin_headers, editor_headers, viewer_headers, studio_root = auth_api_context
        sid = f"sess_cand_pb_{uuid.uuid4().hex[:8]}"
        client.post(
            "/api/v1/ontology/studio/sessions",
            json={"session_id": sid, "domain_namespace": "asset_management"},
            headers=admin_headers,
        )

        sdir = studio_root / sid
        ev_dir = sdir / "evidence"
        ev_dir.mkdir(parents=True, exist_ok=True)
        staged_payload = {
            "staged_candidates": [
                {
                    "candidate_id": "cand-am-waiver",
                    "concept_name": "PrivateCreditLoanFacility",
                    "suggested_hub": "hub_loan_facility",
                }
            ]
        }
        (ev_dir / "staged_candidates.json").write_text(json.dumps(staged_payload), encoding="utf-8")

        # 1. Non-admin editor must be rejected with 403
        r_ed = client.post(
            f"/api/v1/ontology/studio/sessions/{sid}/staged-candidates/cand-am-waiver/promote",
            json={"path": "path_b", "waiver_rationale": "Valid architectural justification over 10 chars"},
            headers=editor_headers,
        )
        assert r_ed.status_code == 403

        # 2. Short rationale (< 10 chars) must be rejected with 422 INVALID_GROUNDING_WAIVER
        r_short = client.post(
            f"/api/v1/ontology/studio/sessions/{sid}/staged-candidates/cand-am-waiver/promote",
            json={"path": "path_b", "waiver_rationale": "too short"},
            headers=admin_headers,
        )
        assert r_short.status_code == 422
        assert r_short.json().get("error_code") == "INVALID_GROUNDING_WAIVER"

        # 3. Missing candidate must return 404
        r_404 = client.post(
            f"/api/v1/ontology/studio/sessions/{sid}/staged-candidates/cand-nonexistent/promote",
            json={"path": "path_b", "waiver_rationale": "Valid architectural justification over 10 chars"},
            headers=admin_headers,
        )
        assert r_404.status_code == 404

        # 4. Valid admin Path B promotion succeeds with 200 and valid op_ids
        r_ok = client.post(
            f"/api/v1/ontology/studio/sessions/{sid}/staged-candidates/cand-am-waiver/promote",
            json={"path": "path_b", "waiver_rationale": "Specialized direct lending vehicle not in FIBO core"},
            headers=admin_headers,
        )
        assert r_ok.status_code == 200, r_ok.text
        data = r_ok.json()
        assert data.get("promoted") is True
        ops = data.get("operations", [])
        assert len(ops) >= 1
        for op in ops:
            op_id = op.get("op_id")
            assert op_id is not None
            assert op_id.startswith("op-concept-")


# ==============================================================================
# SECTION 2: PROPOSAL REFINEMENT STATE MACHINE & 409 REJECTION
# ==============================================================================


class TestProposalRefinementStateMachine:
    """Stress tests proposal refinement OCC checks and rejection of SUPERSEDED proposals with 409."""

    def test_refine_superseded_proposal_returns_409(
        self, auth_api_context: tuple[TestClient, dict[str, str], dict[str, str], dict[str, str], Path]
    ) -> None:
        """Verify that refining a SUPERSEDED proposal returns HTTP 409 PROPOSAL_ALREADY_RESOLVED."""
        client, admin_headers, _, _, studio_root = auth_api_context
        sid = f"sess_sup_{uuid.uuid4().hex[:8]}"
        client.post(
            "/api/v1/ontology/studio/sessions",
            json={"session_id": sid, "domain_namespace": "asset_management"},
            headers=admin_headers,
        )

        pdir = studio_root / sid / "proposals"
        pdir.mkdir(parents=True, exist_ok=True)
        prop_file = pdir / "prop-v1.json"
        prop_file.write_text(
            json.dumps({
                "proposal_id": "prop-v1",
                "session_id": sid,
                "status": "SUPERSEDED",
                "superseded_by": "prop-v2",
                "delta": {"operations": []},
            }),
            encoding="utf-8",
        )

        resp = client.post(
            f"/api/v1/ontology/studio/sessions/{sid}/proposals/prop-v1/refine",
            json={"steering_prompt": "Attempt to refine an obsolete superseded proposal"},
            headers=admin_headers,
        )
        assert resp.status_code == 409
        assert resp.json().get("error_code") == "PROPOSAL_ALREADY_RESOLVED"

    def test_refine_accepted_and_rejected_proposals_return_409(
        self, auth_api_context: tuple[TestClient, dict[str, str], dict[str, str], dict[str, str], Path]
    ) -> None:
        """Verify that refining ACCEPTED or REJECTED proposals returns HTTP 409."""
        client, admin_headers, _, _, studio_root = auth_api_context
        sid = f"sess_res_{uuid.uuid4().hex[:8]}"
        client.post(
            "/api/v1/ontology/studio/sessions",
            json={"session_id": sid, "domain_namespace": "asset_management"},
            headers=admin_headers,
        )

        pdir = studio_root / sid / "proposals"
        pdir.mkdir(parents=True, exist_ok=True)
        (pdir / "prop-acc.json").write_text(
            json.dumps({"proposal_id": "prop-acc", "session_id": sid, "status": "ACCEPTED", "delta": {"operations": []}}),
            encoding="utf-8",
        )
        (pdir / "prop-rej.json").write_text(
            json.dumps({"proposal_id": "prop-rej", "session_id": sid, "status": "REJECTED", "delta": {"operations": []}}),
            encoding="utf-8",
        )

        r_acc = client.post(
            f"/api/v1/ontology/studio/sessions/{sid}/proposals/prop-acc/refine",
            json={"steering_prompt": "Refining accepted"},
            headers=admin_headers,
        )
        assert r_acc.status_code == 409
        assert r_acc.json().get("error_code") == "PROPOSAL_ALREADY_RESOLVED"

        r_rej = client.post(
            f"/api/v1/ontology/studio/sessions/{sid}/proposals/prop-rej/refine",
            json={"steering_prompt": "Refining rejected"},
            headers=admin_headers,
        )
        assert r_rej.status_code == 409
        assert r_rej.json().get("error_code") == "PROPOSAL_ALREADY_RESOLVED"

    def test_concurrent_refinement_race_condition_isolation(
        self, auth_api_context: tuple[TestClient, dict[str, str], dict[str, str], dict[str, str], Path]
    ) -> None:
        """Stress test: Concurrent refinement requests on the same pending proposal.
        Session lock and status check must ensure only one request succeeds (200),
        while concurrent contenders receive HTTP 409 PROPOSAL_ALREADY_RESOLVED.
        """
        client, admin_headers, _, _, studio_root = auth_api_context
        sid = f"sess_race_{uuid.uuid4().hex[:8]}"
        client.post(
            "/api/v1/ontology/studio/sessions",
            json={"session_id": sid, "domain_namespace": "asset_management"},
            headers=admin_headers,
        )

        pdir = studio_root / sid / "proposals"
        pdir.mkdir(parents=True, exist_ok=True)
        prop_id = "prop-race-target"
        (pdir / f"{prop_id}.json").write_text(
            json.dumps({
                "proposal_id": prop_id,
                "session_id": sid,
                "status": "PENDING",
                "base_version": "0.0.0",
                "base_sha256": "1916a5c6d501015984b887fd76609b225b72d5d393e5813d8df210b92ab3b4c9",
                "delta": {
                    "target_ontology_version": "0.1.0",
                    "operations": [],
                },
            }),
            encoding="utf-8",
        )

        def do_refine(prompt: str) -> tuple[int, dict[str, Any]]:
            r = client.post(
                f"/api/v1/ontology/studio/sessions/{sid}/proposals/{prop_id}/refine",
                json={"steering_prompt": prompt, "mode": "deterministic"},
                headers=admin_headers,
            )
            return r.status_code, r.json()

        # Fire 5 concurrent refinement calls on the exact same proposal
        with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
            futures = [
                executor.submit(do_refine, f"Steering iteration {i}")
                for i in range(5)
            ]
            results = [f.result() for f in futures]

        status_codes = [code for code, _ in results]
        # Exactly one request must succeed with 200, all other 4 must be rejected with 409
        success_count = status_codes.count(200)
        conflict_count = status_codes.count(409)

        assert success_count == 1, f"Expected exactly 1 success, got status codes: {status_codes}"
        assert conflict_count == 4, f"Expected 4 conflicts, got status codes: {status_codes}"


# ==============================================================================
# SECTION 3: PYTHON STANDALONE CLI STRESS TESTS (OPENDV)
# ==============================================================================


class TestPythonCliEdgeCases:
    """Empirical verification of standalone Python CLI `opendv ontology studio` subcommands."""

    def test_cli_staged_list_and_promote_lifecycle(self) -> None:
        """Verify `opendv ontology studio staged list` and `staged promote`."""
        sid = f"cli_sess_{uuid.uuid4().hex[:8]}"
        # Create session
        res_create = subprocess.run(
            ["uv", "run", "--directory", str(REPO_ROOT), "opendv", "ontology", "studio", "create", "-s", sid, "--json"],
            capture_output=True,
            text=True,
            check=True,
        )
        assert res_create.returncode == 0

        # Staged list (initially empty)
        res_list = subprocess.run(
            ["uv", "run", "--directory", str(REPO_ROOT), "opendv", "ontology", "studio", "staged", "list", "-s", sid, "--json"],
            capture_output=True,
            text=True,
            check=True,
        )
        assert res_list.returncode == 0
        list_data = json.loads(res_list.stdout)
        assert list_data.get("session_id") == sid
        assert list_data.get("total_count") == 0

        # Manually plant a staged candidate into session
        sdir = Path.home() / ".opendv" / "ontology_studio" / sid
        if not sdir.exists():
            # Check local directory structure
            sdir = REPO_ROOT / ".opendv" / "ontology_studio" / sid
        ev_dir = sdir / "evidence"
        ev_dir.mkdir(parents=True, exist_ok=True)
        (ev_dir / "staged_candidates.json").write_text(
            json.dumps({
                "staged_candidates": [
                    {
                        "candidate_id": "cand-cli-01",
                        "concept_name": "CollateralizedLoanObligation",
                        "suggested_hub": "hub_clo",
                        "score": 0.55,
                    }
                ]
            }),
            encoding="utf-8",
        )

        # Staged list (now count 1)
        res_list2 = subprocess.run(
            ["uv", "run", "--directory", str(REPO_ROOT), "opendv", "ontology", "studio", "staged", "list", "-s", sid, "--json"],
            capture_output=True,
            text=True,
            check=True,
        )
        assert res_list2.returncode == 0
        list_data2 = json.loads(res_list2.stdout)
        assert list_data2.get("total_count") == 1

        # Staged promote Path B with admin waiver
        res_prom = subprocess.run(
            [
                "uv", "run", "--directory", str(REPO_ROOT), "opendv", "ontology", "studio", "staged", "promote",
                "-s", sid,
                "--candidate-id", "cand-cli-01",
                "--waiver",
                "--rationale", "Structured finance tranche vehicle for asset management",
                "--role", "admin",
                "--json",
            ],
            capture_output=True,
            text=True,
        )
        assert res_prom.returncode == 0, res_prom.stderr
        prom_data = json.loads(res_prom.stdout)
        assert prom_data.get("promoted") is True
        assert prom_data.get("status") == "PROMOTED"

    def test_cli_text_add_inline_and_file(self, tmp_path: Path) -> None:
        """Verify `opendv ontology studio text add` with inline string and file input."""
        sid = f"cli_txt_{uuid.uuid4().hex[:8]}"
        subprocess.run(
            ["uv", "run", "--directory", str(REPO_ROOT), "opendv", "ontology", "studio", "create", "-s", sid, "--json"],
            capture_output=True,
            text=True,
            check=True,
        )

        # Inline text
        res_txt = subprocess.run(
            [
                "uv", "run", "--directory", str(REPO_ROOT), "opendv", "ontology", "studio", "text", "add",
                "-s", sid,
                "-t", "Portfolio managers execute trades on multilateral trading facilities.",
                "--source-name", "notes.txt",
                "--json",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        assert res_txt.returncode == 0
        txt_data = json.loads(res_txt.stdout)
        assert "source_id" in txt_data or "source_sha256" in txt_data

        # File input
        doc_file = tmp_path / "domain_input.txt"
        doc_file.write_text("Private equity fund formation clauses and limited partnership agreements.", encoding="utf-8")
        res_file = subprocess.run(
            [
                "uv", "run", "--directory", str(REPO_ROOT), "opendv", "ontology", "studio", "text", "add",
                "-s", sid,
                "--file", str(doc_file),
                "--json",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        assert res_file.returncode == 0
        file_data = json.loads(res_file.stdout)
        assert "source_id" in file_data or "source_sha256" in file_data

    def test_cli_starter_pack_apply_and_version_progression(self) -> None:
        """Verify `opendv ontology studio starter-pack apply` applies pack and advances semver."""
        sid = f"cli_pack_{uuid.uuid4().hex[:8]}"
        subprocess.run(
            ["uv", "run", "--directory", str(REPO_ROOT), "opendv", "ontology", "studio", "create", "-s", sid, "--json"],
            capture_output=True,
            text=True,
            check=True,
        )

        # Apply core_spine
        res_pack = subprocess.run(
            [
                "uv", "run", "--directory", str(REPO_ROOT), "opendv", "ontology", "studio", "starter-pack", "apply",
                "-s", sid,
                "--pack", "core_spine",
                "--json",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        assert res_pack.returncode == 0
        data = json.loads(res_pack.stdout)
        assert data.get("applied") is True
        assert data.get("pack_id") == "core_spine"
        assert data.get("concepts_added") == 6


# ==============================================================================
# SECTION 4: BUN CLI CONTRACT DEFECT EMPIRICAL CHALLENGE
# ==============================================================================


class TestBunCliDefectEmpiricalChallenge:
    """Empirical demonstration of Bun CLI starter-pack apply contract mismatch defect."""

    def test_apply_starter_pack_request_extra_field_forbid(self) -> None:
        """Verify ApplyStarterPackRequest supports pack_id, pack_ids, and pack alias.

        Also confirms that unknown extra fields are forbidden by extra='forbid'.
        """
        # When sending 'pack_id' or 'pack_ids', schema validation succeeds
        valid_req = ApplyStarterPackRequest.model_validate({"pack_id": "core_spine"})
        assert valid_req.pack_ids == ["core_spine"]

        valid_req2 = ApplyStarterPackRequest.model_validate({"pack_ids": ["core_spine"]})
        assert valid_req2.pack_ids == ["core_spine"]

        # When sending 'pack' alias (defense-in-depth), schema validation succeeds and normalizes
        valid_req3 = ApplyStarterPackRequest.model_validate({"pack": "core_spine"})
        assert valid_req3.pack_ids == ["core_spine"]

        # When sending an unknown extra field, validation fails with extra_forbidden
        with pytest.raises(Exception) as exc_info:
            ApplyStarterPackRequest.model_validate({"unknown_extra_pack": "core_spine"})
        assert "Extra inputs are not permitted" in str(exc_info.value)
