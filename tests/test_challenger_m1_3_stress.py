"""tests/test_challenger_m1_3_stress.py - Iteration 2 Empirical Challenger Stress Harness.

Authored by empirical challenger (challenger_m1_3) to stress test Milestone M1 Features 1-8:
1. Candidate promotion op_id validation:
   - op_id format regex and uniqueness across all applied delta operations.
   - Target SemVer progression (0.0.0 -> 1.0.0 or greater).
   - Invariant checks for Path A and Path B promotion edge cases.
2. CLI verification:
   - opendv ontology studio staged list
   - opendv ontology studio staged promote
   - opendv ontology studio text add
   - opendv ontology studio starter-pack apply
3. Proposal refinement conflict handling:
   - Asserts HTTP 409 PROPOSAL_ALREADY_RESOLVED when refining SUPERSEDED, ACCEPTED,
     or REJECTED proposals.
4. Parity and TypeScript CLI execution:
   - Bun CLI command execution and option compatibility.
"""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
import uuid
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from opendv_contracts.dtos.promotion import PromoteCandidateRequest
from opendv_contracts.errors import ErrorCode, OpenDVError
from opendv_platform_api.main import create_app
from opendv_platform_api.security.studio_auth import StudioPrincipal
from opendv_service import OpenDVSettings
from opendv_service.studio.promoter import StagedCandidatePromoter
from opendv_service.studio.session_manager import StudioSessionManager

REPO_ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def auth_api_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[TestClient, dict[str, str], dict[str, str], Path]:
    """Create isolated FastAPI client with loopback caller and admin/editor credentials."""
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

    # Initialize studio
    init_res = client.post(
        "/api/v1/ontology/studio/init",
        json={
            "domain_namespace": "asset_management",
            "enterprise_owner": "Enterprise Architecture",
        },
    )
    assert init_res.status_code == 200, init_res.text
    token = init_res.json()["local_token"]

    admin_headers = {"Authorization": f"Bearer {token}", "X-OpenDV-Role": "admin"}
    editor_headers = {"Authorization": f"Bearer {token}", "X-OpenDV-Role": "editor"}

    return client, admin_headers, editor_headers, studio_root


# ==============================================================================
# SECTION 1: CANDIDATE PROMOTION OP_ID & SEMVER VALIDATION (TASK 1)
# ==============================================================================


class TestCandidatePromotionOpIdAndSemVerValidation:
    """Rigorous empirical validation of candidate promotion op_id and SemVer progression."""

    def test_promotion_generates_valid_unique_op_ids(self, tmp_path: Path) -> None:
        """Verify that all generated delta operations have non-empty, unique, valid op_ids."""
        studio_dir = tmp_path / "studio"
        session_mgr = StudioSessionManager(studio_root=studio_dir)
        meta = session_mgr.create_session(
            session_id="stress_promoter_op_ids",
            domain_namespace="asset_management",
        )
        sdir = session_mgr.get_session_dir(meta.session_id)
        ev_dir = sdir / "evidence"
        ev_dir.mkdir(parents=True, exist_ok=True)

        candidate_id = "cand-complex-001"
        fibo_uri = (
            "https://spec.edmcouncil.org/fibo/ontology/SEC/Equities/EquityInstruments/"
            "CollateralPosition"
        )
        staged_payload = {
            "staged_candidates": [
                {
                    "candidate_id": candidate_id,
                    "concept_name": "CollateralPosition",
                    "suggested_hub": "hub_collateral_position",
                    "top_match_uri": fibo_uri,
                    "top_match_score": 0.4500,
                    "attributes": [
                        {"attribute_name": "valuation_amt", "datatype": "decimal(18,4)"},
                        {"attribute_name": "margin_rate", "datatype": "decimal(8,4)"},
                    ],
                    "dependent_links": [
                        {
                            "target_link": "lnk_collateral_instrument",
                            "relationship_uri": "urn:opendv:relationship:secured_by",
                        }
                    ],
                }
            ]
        }
        (ev_dir / "staged_candidates.json").write_text(json.dumps(staged_payload), encoding="utf-8")

        promoter = StagedCandidatePromoter(sdir, session_mgr)
        res = promoter.promote_candidate(
            candidate_id=candidate_id,
            request=PromoteCandidateRequest(
                path="path_b",
                waiver_rationale="Architectural approval for bilateral OTC collateral tracking.",
            ),
            principal=StudioPrincipal(
                principal="human:chief_architect",
                role="admin",
                is_admin=True,
                auth_method="token",
            ),
        )

        assert res.success is True
        assert len(res.applied_operations) == 5  # concept, mapping, 2 attributes, relationship

        # Regex for valid op_id: op-(concept|map|attr|rel)-[a-z0-9_-]+
        op_id_pattern = re.compile(r"^op-(concept|map|attr|rel)-[a-z0-9_-]+$")
        seen_op_ids: set[str] = set()

        for op in res.applied_operations:
            assert "op_id" in op, f"Operation {op} missing op_id"
            op_id = op["op_id"]
            assert isinstance(op_id, str) and len(op_id) > 5, f"Invalid op_id value: {op_id}"
            assert op_id_pattern.match(op_id), f"op_id does not match canonical pattern: {op_id}"
            assert op_id not in seen_op_ids, f"Duplicate op_id detected: {op_id}"
            seen_op_ids.add(op_id)

    def test_promotion_target_semver_monotonicity(self, tmp_path: Path) -> None:
        """Verify target_ontology_version is strictly valid SemVer and > base version."""
        studio_dir = tmp_path / "studio"
        session_mgr = StudioSessionManager(studio_root=studio_dir)
        meta = session_mgr.create_session(
            session_id="stress_semver_progression",
            domain_namespace="asset_management",
        )
        sdir = session_mgr.get_session_dir(meta.session_id)
        ev_dir = sdir / "evidence"
        ev_dir.mkdir(parents=True, exist_ok=True)

        candidate_id = "cand-semver-001"
        staged_payload = {
            "staged_candidates": [
                {
                    "candidate_id": candidate_id,
                    "concept_name": "PortfolioHoldings",
                    "top_match_score": 0.5000,
                }
            ]
        }
        (ev_dir / "staged_candidates.json").write_text(json.dumps(staged_payload), encoding="utf-8")

        promoter = StagedCandidatePromoter(sdir, session_mgr)
        res = promoter.promote_candidate(
            candidate_id=candidate_id,
            request=PromoteCandidateRequest(
                path="path_b",
                waiver_rationale="Waiver to promote portfolio holdings into canonical schema.",
            ),
            principal=StudioPrincipal(
                principal="human:admin",
                role="admin",
                is_admin=True,
                auth_method="token",
            ),
        )

        assert res.success is True
        # Read the generated proposal on disk to verify target SemVer
        prop_file = sdir / "proposals" / f"{res.proposal_id}.json"
        assert prop_file.is_file()
        prop_data = json.loads(prop_file.read_text(encoding="utf-8"))
        assert prop_data["base_version"] == "0.0.0"
        assert prop_data["target_version"] == "1.0.0"
        assert prop_data["status"] == "PENDING"

    def test_path_b_rationale_boundary_conditions(self, tmp_path: Path) -> None:
        """Verify rationale length boundary: 9 chars fails, exactly 10 chars succeeds."""
        studio_dir = tmp_path / "studio"
        session_mgr = StudioSessionManager(studio_root=studio_dir)
        meta = session_mgr.create_session(
            session_id="stress_path_b_boundary",
            domain_namespace="asset_management",
        )
        sdir = session_mgr.get_session_dir(meta.session_id)
        ev_dir = sdir / "evidence"
        ev_dir.mkdir(parents=True, exist_ok=True)

        candidate_id = "cand-boundary-001"
        staged_payload = {
            "staged_candidates": [
                {
                    "candidate_id": candidate_id,
                    "concept_name": "BoundaryTestConcept",
                    "top_match_score": 0.3000,
                }
            ]
        }
        (ev_dir / "staged_candidates.json").write_text(json.dumps(staged_payload), encoding="utf-8")

        promoter = StagedCandidatePromoter(sdir, session_mgr)
        admin_principal = StudioPrincipal(
            principal="human:admin", role="admin", is_admin=True, auth_method="token"
        )

        # 1. 9 characters -> fails with INVALID_GROUNDING_WAIVER
        with pytest.raises(OpenDVError) as exc_info:
            promoter.promote_candidate(
                candidate_id=candidate_id,
                request=PromoteCandidateRequest(path="path_b", waiver_rationale="123456789"),
                principal=admin_principal,
            )
        assert exc_info.value.error_code == ErrorCode.INVALID_GROUNDING_WAIVER

        # 2. Exactly 10 characters -> succeeds
        res = promoter.promote_candidate(
            candidate_id=candidate_id,
            request=PromoteCandidateRequest(path="path_b", waiver_rationale="1234567890"),
            principal=admin_principal,
        )
        assert res.success is True
        assert res.candidate_id == candidate_id


# ==============================================================================
# SECTION 2: PYTHON CLI STRESS TESTING (TASK 3)
# ==============================================================================


class TestPythonCliEdgeCases:
    """Empirical verification of standalone Python CLI handlers."""

    def test_cli_staged_list_empty_and_populated(self) -> None:
        """Verify opendv ontology studio staged list with empty and populated candidates."""
        sid = f"cli_stress_staged_{uuid.uuid4().hex[:8]}"
        # Create session
        res_create = subprocess.run(
            [
                "uv", "run", "--directory", str(REPO_ROOT),
                "opendv", "ontology", "studio", "create",
                "-s", sid, "--json",
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        assert res_create.returncode == 0

        # List empty staged candidates
        res_empty = subprocess.run(
            [
                "uv", "run", "--directory", str(REPO_ROOT),
                "opendv", "ontology", "studio", "staged", "list",
                "-s", sid, "--json",
            ],
            capture_output=True,
            text=True,
        )
        assert res_empty.returncode == 0
        data_empty = json.loads(res_empty.stdout)
        assert data_empty.get("session_id") == sid
        assert data_empty.get("staged_candidates") == []

        # Populate staged candidates on disk in REPO_ROOT/.opendv/ontology_studio/sid
        session_dir = REPO_ROOT / ".opendv" / "ontology_studio" / sid
        ev_dir = session_dir / "evidence"
        ev_dir.mkdir(parents=True, exist_ok=True)
        fibo_uri = (
            "https://spec.edmcouncil.org/fibo/ontology/DER/DerivativesContracts/"
            "DerivativesInstruments/DerivativeInstrument"
        )
        staged_data = {
            "staged_candidates": [
                {
                    "candidate_id": "cand-cli-101",
                    "concept_name": "DerivativesContract",
                    "top_match_score": 0.5800,
                    "matched_fibo_uri": fibo_uri,
                }
            ]
        }
        (ev_dir / "staged_candidates.json").write_text(json.dumps(staged_data), encoding="utf-8")

        # List populated staged candidates
        res_pop = subprocess.run(
            [
                "uv", "run", "--directory", str(REPO_ROOT),
                "opendv", "ontology", "studio", "staged", "list",
                "-s", sid, "--json",
            ],
            capture_output=True,
            text=True,
        )
        assert res_pop.returncode == 0
        data_pop = json.loads(res_pop.stdout)
        assert len(data_pop.get("staged_candidates")) == 1
        assert data_pop["staged_candidates"][0]["candidate_id"] == "cand-cli-101"

    def test_cli_staged_promote_path_b_and_rejections(self) -> None:
        """Verify opendv ontology studio staged promote succeeds via Path B and rejects."""
        sid = f"cli_stress_promote_{uuid.uuid4().hex[:8]}"
        subprocess.run(
            [
                "uv", "run", "--directory", str(REPO_ROOT),
                "opendv", "ontology", "studio", "create",
                "-s", sid, "--json",
            ],
            capture_output=True,
            text=True,
            check=True,
        )

        session_dir = REPO_ROOT / ".opendv" / "ontology_studio" / sid
        ev_dir = session_dir / "evidence"
        ev_dir.mkdir(parents=True, exist_ok=True)
        staged_data = {
            "staged_candidates": [
                {
                    "candidate_id": "cand-cli-promote-01",
                    "concept_name": "SwapLeg",
                    "top_match_score": 0.5200,
                }
            ]
        }
        (ev_dir / "staged_candidates.json").write_text(json.dumps(staged_data), encoding="utf-8")

        # 1. Promote with short rationale -> must fail
        res_short = subprocess.run(
            [
                "uv", "run", "--directory", str(REPO_ROOT),
                "opendv", "ontology", "studio", "staged", "promote",
                "-s", sid,
                "-c", "cand-cli-promote-01",
                "--waiver",
                "--rationale", "short",
                "--role", "admin",
                "--json",
            ],
            capture_output=True,
            text=True,
        )
        assert res_short.returncode != 0

        # 2. Promote with valid rationale -> succeeds with exit 0
        res_valid = subprocess.run(
            [
                "uv", "run", "--directory", str(REPO_ROOT),
                "opendv", "ontology", "studio", "staged", "promote",
                "-s", sid,
                "-c", "cand-cli-promote-01",
                "--waiver",
                "--rationale", "Approved architectural waiver for swap legs.",
                "--role", "admin",
                "--json",
            ],
            capture_output=True,
            text=True,
        )
        assert res_valid.returncode == 0
        data_valid = json.loads(res_valid.stdout)
        assert data_valid.get("promoted") is True
        assert data_valid.get("candidate_id") == "cand-cli-promote-01"

    def test_cli_text_add_unicode_and_large_content(self, tmp_path: Path) -> None:
        """Verify opendv ontology studio text add handles unicode, multiline, and large text."""
        sid = f"cli_stress_text_{uuid.uuid4().hex[:8]}"
        subprocess.run(
            [
                "uv", "run", "--directory", str(REPO_ROOT),
                "opendv", "ontology", "studio", "create",
                "-s", sid, "--json",
            ],
            capture_output=True,
            text=True,
            check=True,
        )

        # Unicode text with Japanese, Arabic, and emojis
        unicode_text = "資産運用 ポートフォリオ 管理 📈 🏛️ المحفظة الاستثمارية"
        res_unicode = subprocess.run(
            [
                "uv", "run", "--directory", str(REPO_ROOT),
                "opendv", "ontology", "studio", "text", "add",
                "-s", sid,
                "-t", unicode_text,
                "--source-name", "unicode_doc.txt",
                "--json",
            ],
            capture_output=True,
            text=True,
        )
        assert res_unicode.returncode == 0
        data_unicode = json.loads(res_unicode.stdout)
        expected_sha = hashlib.sha256(unicode_text.encode("utf-8")).hexdigest()
        assert (
            data_unicode.get("source_sha256") == expected_sha
            or data_unicode.get("sha256") == expected_sha
        )

        # Ingest via file (-f)
        large_file = tmp_path / "large_doc.md"
        line = "Portfolio consists of positions and cash balances.\n"
        large_content = "# Asset Management Taxonomy\n" + (line * 2000)
        large_file.write_text(large_content, encoding="utf-8")

        res_file = subprocess.run(
            [
                "uv", "run", "--directory", str(REPO_ROOT),
                "opendv", "ontology", "studio", "text", "add",
                "-s", sid,
                "-f", str(large_file),
                "--json",
            ],
            capture_output=True,
            text=True,
        )
        assert res_file.returncode == 0
        data_file = json.loads(res_file.stdout)
        file_sha = hashlib.sha256(large_content.encode("utf-8")).hexdigest()
        assert (
            data_file.get("source_sha256") == file_sha
            or data_file.get("sha256") == file_sha
        )

    def test_cli_starter_pack_apply_progression(self) -> None:
        """Verify opendv ontology studio starter-pack apply increments version and updates."""
        sid = f"cli_stress_packs_{uuid.uuid4().hex[:8]}"
        subprocess.run(
            [
                "uv", "run", "--directory", str(REPO_ROOT),
                "opendv", "ontology", "studio", "create",
                "-s", sid, "--json",
            ],
            capture_output=True,
            text=True,
            check=True,
        )

        # 1. Apply core spine
        res_spine = subprocess.run(
            [
                "uv", "run", "--directory", str(REPO_ROOT),
                "opendv", "ontology", "studio", "starter-pack", "apply",
                "-s", sid,
                "--pack", "core_spine",
                "--json",
            ],
            capture_output=True,
            text=True,
        )
        assert res_spine.returncode == 0
        data_spine = json.loads(res_spine.stdout)
        assert data_spine.get("applied") is True
        assert data_spine.get("concepts_added") == 6

        # 2. Apply public equities vertical
        res_eq = subprocess.run(
            [
                "uv", "run", "--directory", str(REPO_ROOT),
                "opendv", "ontology", "studio", "starter-pack", "apply",
                "-s", sid,
                "--pack", "public_equities_oms",
                "--json",
            ],
            capture_output=True,
            text=True,
        )
        assert res_eq.returncode == 0
        data_eq = json.loads(res_eq.stdout)
        assert data_eq.get("applied") is True
        assert data_eq.get("concepts_added") == 10  # 6 spine + 4 vertical


# ==============================================================================
# SECTION 3: PROPOSAL REFINEMENT 409 CONFLICT HANDLING (TASK 3)
# ==============================================================================


class TestProposalRefinementConflictHandling:
    """Empirical verification that refining non-PENDING proposals returns HTTP 409."""

    def test_refining_superseded_proposal_returns_409(
        self, auth_api_context: tuple[TestClient, dict[str, str], dict[str, str], Path]
    ) -> None:
        """Verify POST /proposals/{id}/refine rejects SUPERSEDED proposals with HTTP 409."""
        client, admin_headers, _, studio_root = auth_api_context
        sid = f"sess_refine_super_{uuid.uuid4().hex[:8]}"
        client.post(
            "/api/v1/ontology/studio/sessions",
            json={"session_id": sid, "domain_namespace": "asset_management"},
            headers=admin_headers,
        )

        # Place a SUPERSEDED proposal on disk
        props_dir = studio_root / sid / "proposals"
        props_dir.mkdir(parents=True, exist_ok=True)
        (props_dir / "prop-superseded.json").write_text(
            json.dumps({
                "proposal_id": "prop-superseded",
                "session_id": sid,
                "status": "SUPERSEDED",
                "superseded_by": "prop-replacement-v2",
                "delta": {"operations": []},
            }),
            encoding="utf-8",
        )

        resp = client.post(
            f"/api/v1/ontology/studio/sessions/{sid}/proposals/prop-superseded/refine",
            json={"steering_prompt": "Refining obsolete superseded proposal"},
            headers=admin_headers,
        )
        assert resp.status_code == 409
        body = resp.json()
        assert body.get("error_code") == "PROPOSAL_ALREADY_RESOLVED"

    def test_refining_accepted_or_rejected_proposal_returns_409(
        self, auth_api_context: tuple[TestClient, dict[str, str], dict[str, str], Path]
    ) -> None:
        """Verify POST /proposals/{id}/refine rejects ACCEPTED and REJECTED proposals with 409."""
        client, admin_headers, _, studio_root = auth_api_context
        sid = f"sess_refine_res_{uuid.uuid4().hex[:8]}"
        client.post(
            "/api/v1/ontology/studio/sessions",
            json={"session_id": sid, "domain_namespace": "asset_management"},
            headers=admin_headers,
        )

        props_dir = studio_root / sid / "proposals"
        props_dir.mkdir(parents=True, exist_ok=True)

        for status in ("ACCEPTED", "REJECTED"):
            pid = f"prop-{status.lower()}"
            (props_dir / f"{pid}.json").write_text(
                json.dumps({
                    "proposal_id": pid,
                    "session_id": sid,
                    "status": status,
                    "delta": {"operations": []},
                }),
                encoding="utf-8",
            )

            resp = client.post(
                f"/api/v1/ontology/studio/sessions/{sid}/proposals/{pid}/refine",
                json={"steering_prompt": f"Attempting to steer an already {status} proposal"},
                headers=admin_headers,
            )
            assert resp.status_code == 409, f"Expected 409 for {status}: {resp.text}"
            assert resp.json().get("error_code") == "PROPOSAL_ALREADY_RESOLVED"


# ==============================================================================
# SECTION 4: BUN CLI PARITY & REMOTE EXECUTOR VERIFICATION (TASK 3)
# ==============================================================================


class TestBunCliParityAndRemoteExecutor:
    """Empirical verification of Bun TypeScript CLI options and execution parity."""

    def test_bun_cli_staged_list_help_and_options(self) -> None:
        """Verify Bun CLI exposes identical options for staged list and promote."""
        res_list = subprocess.run(
            ["bun", "run", "src/main.ts", "ontology", "studio", "staged", "list", "--help"],
            cwd=str(REPO_ROOT / "apps" / "opendvctl"),
            capture_output=True,
            text=True,
        )
        assert res_list.returncode == 0
        assert "-s, --session" in res_list.stdout

        res_promote = subprocess.run(
            ["bun", "run", "src/main.ts", "ontology", "studio", "staged", "promote", "--help"],
            cwd=str(REPO_ROOT / "apps" / "opendvctl"),
            capture_output=True,
            text=True,
        )
        assert res_promote.returncode == 0
        assert "-c, --candidate-id" in res_promote.stdout
        assert "--synonyms" in res_promote.stdout
        assert "--waiver" in res_promote.stdout
        assert "--rationale" in res_promote.stdout

    def test_bun_cli_text_add_options(self) -> None:
        """Verify Bun CLI exposes identical options for text add."""
        res = subprocess.run(
            ["bun", "run", "src/main.ts", "ontology", "studio", "text", "add", "--help"],
            cwd=str(REPO_ROOT / "apps" / "opendvctl"),
            capture_output=True,
            text=True,
        )
        assert res.returncode == 0
        assert "-s, --session" in res.stdout
        assert "-t, --text" in res.stdout
        assert "-f, --file" in res.stdout
        assert "--source-name" in res.stdout

    def test_bun_cli_starter_pack_options(self) -> None:
        """Verify Bun CLI exposes identical options for starter-pack apply."""
        res = subprocess.run(
            ["bun", "run", "src/main.ts", "ontology", "studio", "starter-pack", "apply", "--help"],
            cwd=str(REPO_ROOT / "apps" / "opendvctl"),
            capture_output=True,
            text=True,
        )
        assert res.returncode == 0
        assert "-s, --session" in res.stdout
        assert "-p, --pack" in res.stdout
