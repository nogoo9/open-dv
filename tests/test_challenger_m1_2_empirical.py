"""open-dv/tests/test_challenger_m1_2_empirical.py

Empirical Challenger M1.2 Adversarial & Stress Verification Suite.
Certifies Features 1–8 of Milestone M1 (Asset Management Core Capabilities).
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import time
import uuid
from pathlib import Path
from typing import Any

import pytest
from starlette.testclient import TestClient

from opendv_agent_harness.pipeline import (
    PipelineInputSpec,
    PipelineUnstructuredDocInput,
    _synthesize_cumulative_contract,
    validate_pipeline_inputs,
)
from opendv_contracts.catalogs.polysemy import KNOWN_POLYSEMIC_DEFINITIONS
from opendv_contracts.dtos.promotion import PromoteCandidateRequest
from opendv_contracts.dtos.starter_packs import AssetManagementPackId
from opendv_contracts.errors import ErrorCode, OpenDVError
from opendv_core.models.graph_a import (
    CanonicalBusinessConcept,
    CanonicalRelationship,
    ConceptAttribute,
    GraphAOntology,
)
from opendv_core.ontology_hashing import compute_ontology_sha256
from opendv_platform_api.main import create_app
from opendv_service import OpenDVSettings
from opendv_service.construction.clarification_harness import ClarificationHarness
from opendv_service.ontology.matching.verifier import get_default_grounding_verifier
from opendv_service.studio.agent_runner import StudioAgentRunner
from opendv_sources.asset_management import (
    AssetManagementStarterPackRegistry,
    compose_asset_management_ontology,
    compute_pack_hash,
    get_all_domains_pack,
    get_core_spine_pack,
    get_custody_clearing_pack,
    get_fixed_income_rates_pack,
    get_multi_asset_ibor_pack,
    get_private_credit_direct_lending_pack,
    get_private_equity_buyout_pack,
    get_public_equities_oms_pack,
)

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

    # Initialize studio
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


# ============================================================================
# FEATURE 1: STARTER PACKS & ALL DOMAINS PRESET CORNER CASES
# ============================================================================


class TestFeature1StarterPacksCornerCases:
    """Corner cases in Starter Packs & All Domains Preset."""

    def test_circular_inheritance_detection_three_nodes(self) -> None:
        """Adversarial cycle detection: A -> B -> C -> A must be rejected."""
        c_a = CanonicalBusinessConcept(
            concept_uri="urn:opendv:concept:node_a",
            concept_name="NodeA",
            parent_concept_uri="urn:opendv:concept:node_c",
        )
        c_b = CanonicalBusinessConcept(
            concept_uri="urn:opendv:concept:node_b",
            concept_name="NodeB",
            parent_concept_uri="urn:opendv:concept:node_a",
        )
        c_c = CanonicalBusinessConcept(
            concept_uri="urn:opendv:concept:node_c",
            concept_name="NodeC",
            parent_concept_uri="urn:opendv:concept:node_b",
        )
        with pytest.raises(ValueError, match="CYCLIC_CONCEPT_HIERARCHY"):
            GraphAOntology(
                domain_namespace="test",
                canonical_business_concepts=[c_a, c_b, c_c],
            )

    def test_self_referential_inheritance_rejection(self) -> None:
        """Self-referential inheritance A -> A must be rejected."""
        c = CanonicalBusinessConcept(
            concept_uri="urn:opendv:concept:self_ref",
            concept_name="SelfRef",
            parent_concept_uri="urn:opendv:concept:self_ref",
        )
        with pytest.raises(ValueError, match="SELF_REFERENTIAL_PARENT"):
            GraphAOntology(
                domain_namespace="test",
                canonical_business_concepts=[c],
            )

    def test_dangling_parent_concept_uri_rejection(self) -> None:
        """Concept referencing non-existent parent must be rejected."""
        c = CanonicalBusinessConcept(
            concept_uri="urn:opendv:concept:child",
            concept_name="Child",
            parent_concept_uri="urn:opendv:concept:ghost_parent",
        )
        with pytest.raises(ValueError, match="DANGLING_PARENT_CONCEPT"):
            GraphAOntology(
                domain_namespace="test",
                canonical_business_concepts=[c],
            )

    def test_duplicate_concept_uri_detection(self) -> None:
        """Ontology with duplicate concept URIs must be rejected."""
        c1 = CanonicalBusinessConcept(
            concept_uri="urn:opendv:concept:dup",
            concept_name="FirstInstance",
        )
        c2 = CanonicalBusinessConcept(
            concept_uri="urn:opendv:concept:dup",
            concept_name="SecondInstance",
        )
        with pytest.raises(ValueError, match="DUPLICATE_CONCEPT_URI"):
            GraphAOntology(
                domain_namespace="test",
                canonical_business_concepts=[c1, c2],
            )

    def test_dangling_relationship_endpoint_detection(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Relationship referencing endpoint not in concepts must raise DANGLING_RELATIONSHIP_ENDPOINT."""
        from opendv_sources.asset_management import presets

        dangling_rel = CanonicalRelationship(
            relationship_uri="urn:opendv:relationship:dangling_test",
            relationship_name="DanglingTest",
            target_link="lnk_dangling_test",
            endpoints=["urn:opendv:concept:ghost_one", "urn:opendv:concept:ghost_two"],
        )
        monkeypatch.setattr(
            presets,
            "get_core_spine_relationships",
            lambda: [dangling_rel],
        )
        with pytest.raises(ValueError, match="DANGLING_RELATIONSHIP_ENDPOINT"):
            compose_asset_management_ontology(include_packs=["core_spine"])

    def test_sha256_stability_across_all_packs(self) -> None:
        """Verify deterministic SHA-256 computation across all 8 starter pack definitions."""
        spine = get_core_spine_pack()
        all_domains = get_all_domains_pack()

        sha_spine_1 = compute_pack_hash(spine)
        sha_spine_2 = compute_pack_hash(spine)
        assert sha_spine_1 == sha_spine_2
        assert len(sha_spine_1) == 64

        sha_all_1 = compute_pack_hash(all_domains)
        sha_all_2 = compute_pack_hash(all_domains)
        assert sha_all_1 == sha_all_2
        assert len(sha_all_1) == 64

        # Verify all vertical pack SHA-256 determinism
        for pack_id in AssetManagementPackId:
            pack = AssetManagementStarterPackRegistry.load_pack(pack_id)
            h1 = compute_pack_hash(pack)
            h2 = compute_ontology_sha256(pack)
            assert h1 == h2


# ============================================================================
# FEATURE 2: POLYSEMY CATALOG DISAMBIGUATION & FIBO URIs
# ============================================================================


class TestFeature2PolysemyCatalog:
    """Polysemy Catalog Disambiguation and FIBO pack integrity."""

    def test_zero_broken_fibo_uris_in_polysemy_catalog(self) -> None:
        """Assert 100% of target FIBO URIs in KNOWN_POLYSEMIC_DEFINITIONS exist in pinned pack."""
        fibo_path = REPO_ROOT / "packages/opendv-sources/src/opendv_sources/fibo/fibo-core-bebd6ae8.json"
        assert fibo_path.is_file(), f"FIBO pack file missing at {fibo_path}"

        fibo_data = json.loads(fibo_path.read_text(encoding="utf-8"))
        fibo_classes = set(fibo_data.get("classes", {}).keys())
        assert len(fibo_classes) > 0

        broken_uris: list[tuple[str, str]] = []
        total_options = 0

        for term, options in KNOWN_POLYSEMIC_DEFINITIONS.items():
            for opt in options:
                total_options += 1
                uri = opt.get("fibo_uri")
                if uri and uri not in fibo_classes:
                    broken_uris.append((term, uri))

        assert total_options >= 42, f"Expected at least 42 options, found {total_options}"
        assert len(broken_uris) == 0, f"Found {len(broken_uris)} broken FIBO URIs: {broken_uris}"

    def test_polysemy_disambiguation_casing_and_slug_matching(self, tmp_path: Path) -> None:
        """Test ambiguity edge cases: casing, camelCase, unknown terms, whitespace."""
        h1 = ClarificationHarness(session_dir=tmp_path / "s1")
        q1 = h1.generate_questions(candidate_terms=["Account"])
        assert len(q1) >= 1
        assert "account" in q1[0].question_id

        # Unknown nonsense term generates zero questions
        h2 = ClarificationHarness(session_dir=tmp_path / "s2")
        q2 = h2.generate_questions(candidate_terms=["XyzUnrecognizedToken12345"])
        assert len(q2) == 0

        # Whitespace-padded term
        h3 = ClarificationHarness(session_dir=tmp_path / "s3")
        q3 = h3.generate_questions(candidate_terms=["   portfolio   "])
        assert len(q3) >= 1


# ============================================================================
# FEATURE 3: STUDIO AGENT RUNNER FALLBACK & SLA
# ============================================================================


class TestFeature3StudioAgentRunnerFallbackAndSLA:
    """StudioAgentRunner Fallback & SLA resilience (<2.5s)."""

    def test_fallback_sla_under_missing_harness_binary(self, tmp_path: Path) -> None:
        """Explicit deterministic mode executes within <2.5s SLA when harness is absent (D28)."""
        runner = StudioAgentRunner(harness=None)
        base_ont = get_core_spine_pack()

        start = time.monotonic()
        proposal = runner(
            mode="deterministic",
            base_ontology=base_ont,
            session_dir=tmp_path,
            principal="human:analyst",
            job_id="job-sla-test",
        )
        elapsed = time.monotonic() - start

        # Assert SLA < 2.5s
        assert elapsed < 2.50, f"Deterministic mode exceeded 2.5s SLA: took {elapsed:.3f}s"
        assert proposal.recommender_id.startswith("deterministic:")
        assert any("engine_mode:deterministic" in d for d in (proposal.diagnostics or []))
        assert any("proposer_principal:deterministic:job-sla-test" in d for d in (proposal.diagnostics or []))

    def test_agent_runner_none_base_ontology_validation_defect(self, tmp_path: Path) -> None:
        """D28: StudioAgentRunner(mode='deterministic') cleanly initializes genesis when base_ontology=None."""
        runner = StudioAgentRunner(harness=None)
        proposal = runner(
            mode="deterministic",
            base_ontology=None,
            session_dir=tmp_path,
            principal="human:analyst",
        )
        assert proposal is not None
        assert any("engine_mode:deterministic" in d for d in (proposal.diagnostics or []))


# ============================================================================
# FEATURE 4: TEXT CAS INGESTION BOUNDARY TESTS
# ============================================================================


class TestFeature4TextCASIngestionBoundaries:
    """Text CAS Ingestion Boundary Tests (unicode, multibyte, whitespace, large inputs)."""

    def test_text_cas_ingestion_unicode_and_emojis(
        self, auth_api_context: tuple[TestClient, dict[str, str], dict[str, str], dict[str, str], Path]
    ) -> None:
        """Verify unicode, emojis, and multilingual text ingested with exact SHA-256."""
        client, admin_headers, _, _, studio_root = auth_api_context
        sid = f"sess_text_uni_{uuid.uuid4().hex[:8]}"
        client.post(
            "/api/v1/ontology/studio/sessions",
            json={"session_id": sid, "domain_namespace": "asset_management"},
            headers=admin_headers,
        )

        unicode_text = "Asset Management 📈: ポートフォリオ 決済 (Settlement) & Crédit Facility 🏦."
        raw_bytes = unicode_text.encode("utf-8")
        expected_sha = hashlib.sha256(raw_bytes).hexdigest()

        resp = client.post(
            f"/api/v1/ontology/studio/sessions/{sid}/sources/text",
            json={"text": unicode_text, "source_name": "unicode_notes.txt"},
            headers=admin_headers,
        )
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert data["source_sha256"] == expected_sha
        assert data["byte_count"] == len(raw_bytes)

        # Verify CAS blob on disk
        blob_path = studio_root / sid / "inputs" / "cas" / expected_sha[:2] / expected_sha[2:]
        if not blob_path.is_file():
            # Check flat layout fallback
            blob_path = studio_root / sid / "inputs" / "cas" / expected_sha
        assert blob_path.is_file()
        assert blob_path.read_bytes() == raw_bytes

    def test_text_cas_ingestion_zero_length_rejection(
        self, auth_api_context: tuple[TestClient, dict[str, str], dict[str, str], dict[str, str], Path]
    ) -> None:
        """Zero-length text must be rejected with 400 or 422."""
        client, admin_headers, _, _, _ = auth_api_context
        sid = f"sess_text_empty_{uuid.uuid4().hex[:8]}"
        client.post(
            "/api/v1/ontology/studio/sessions",
            json={"session_id": sid, "domain_namespace": "asset_management"},
            headers=admin_headers,
        )

        resp = client.post(
            f"/api/v1/ontology/studio/sessions/{sid}/sources/text",
            json={"text": "", "source_name": "empty.txt"},
            headers=admin_headers,
        )
        assert resp.status_code in (400, 422)

    def test_text_cas_ingestion_whitespace_only(
        self, auth_api_context: tuple[TestClient, dict[str, str], dict[str, str], dict[str, str], Path]
    ) -> None:
        """Whitespace-only text passes validation (length >= 1) and stores exact hash."""
        client, admin_headers, _, _, studio_root = auth_api_context
        sid = f"sess_text_ws_{uuid.uuid4().hex[:8]}"
        client.post(
            "/api/v1/ontology/studio/sessions",
            json={"session_id": sid, "domain_namespace": "asset_management"},
            headers=admin_headers,
        )

        ws_text = "   \t\n   "
        raw_bytes = ws_text.encode("utf-8")
        expected_sha = hashlib.sha256(raw_bytes).hexdigest()

        resp = client.post(
            f"/api/v1/ontology/studio/sessions/{sid}/sources/text",
            json={"text": ws_text, "source_name": "spaces.txt"},
            headers=admin_headers,
        )
        assert resp.status_code == 201
        assert resp.json()["source_sha256"] == expected_sha

    def test_text_cas_ingestion_large_input(
        self, auth_api_context: tuple[TestClient, dict[str, str], dict[str, str], dict[str, str], Path]
    ) -> None:
        """Ingest 1MB+ text input within sub-second latency."""
        client, admin_headers, _, _, studio_root = auth_api_context
        sid = f"sess_text_large_{uuid.uuid4().hex[:8]}"
        client.post(
            "/api/v1/ontology/studio/sessions",
            json={"session_id": sid, "domain_namespace": "asset_management"},
            headers=admin_headers,
        )

        large_text = "Private Equity Investment Agreement Analysis Clause Section.\n" * 20000  # ~1.2MB
        raw_bytes = large_text.encode("utf-8")
        expected_sha = hashlib.sha256(raw_bytes).hexdigest()

        t0 = time.monotonic()
        resp = client.post(
            f"/api/v1/ontology/studio/sessions/{sid}/sources/text",
            json={"text": large_text, "source_name": "large_clause.txt"},
            headers=admin_headers,
        )
        elapsed = time.monotonic() - t0

        assert resp.status_code == 201
        assert elapsed < 1.0, f"1.2MB ingestion took too long: {elapsed:.2f}s"
        assert resp.json()["source_sha256"] == expected_sha
        assert resp.json()["byte_count"] == len(raw_bytes)


# ============================================================================
# FEATURE 5: PROPOSAL REFINEMENT LINEAGE & SUPERSEDING
# ============================================================================


class TestFeature5ProposalRefinementLineage:
    """Proposal Refinement Lineage and 409 rejections."""

    def test_refine_accepted_proposal_rejected_409(
        self, auth_api_context: tuple[TestClient, dict[str, str], dict[str, str], dict[str, str], Path]
    ) -> None:
        """Attempting to refine an ACCEPTED proposal must return HTTP 409 PROPOSAL_ALREADY_RESOLVED."""
        client, admin_headers, _, _, studio_root = auth_api_context
        sid = f"sess_refine_acc_{uuid.uuid4().hex[:8]}"
        client.post(
            "/api/v1/ontology/studio/sessions",
            json={"session_id": sid, "domain_namespace": "asset_management"},
            headers=admin_headers,
        )
        pdir = studio_root / sid / "proposals"
        pdir.mkdir(parents=True, exist_ok=True)
        prop_file = pdir / "prop-accepted.json"
        prop_file.write_text(
            json.dumps({"proposal_id": "prop-accepted", "session_id": sid, "status": "ACCEPTED", "delta": {"operations": []}}),
            encoding="utf-8",
        )

        resp = client.post(
            f"/api/v1/ontology/studio/sessions/{sid}/proposals/prop-accepted/refine",
            json={"steering_prompt": "Refining accepted proposal"},
            headers=admin_headers,
        )
        assert resp.status_code == 409
        assert resp.json()["error_code"] == "PROPOSAL_ALREADY_RESOLVED"

    def test_refine_rejected_proposal_rejected_409(
        self, auth_api_context: tuple[TestClient, dict[str, str], dict[str, str], dict[str, str], Path]
    ) -> None:
        """Attempting to refine a REJECTED proposal must return HTTP 409 PROPOSAL_ALREADY_RESOLVED."""
        client, admin_headers, _, _, studio_root = auth_api_context
        sid = f"sess_refine_rej_{uuid.uuid4().hex[:8]}"
        client.post(
            "/api/v1/ontology/studio/sessions",
            json={"session_id": sid, "domain_namespace": "asset_management"},
            headers=admin_headers,
        )
        pdir = studio_root / sid / "proposals"
        pdir.mkdir(parents=True, exist_ok=True)
        prop_file = pdir / "prop-rejected.json"
        prop_file.write_text(
            json.dumps({"proposal_id": "prop-rejected", "session_id": sid, "status": "REJECTED", "delta": {"operations": []}}),
            encoding="utf-8",
        )

        resp = client.post(
            f"/api/v1/ontology/studio/sessions/{sid}/proposals/prop-rejected/refine",
            json={"steering_prompt": "Refining rejected proposal"},
            headers=admin_headers,
        )
        assert resp.status_code == 409
        assert resp.json()["error_code"] == "PROPOSAL_ALREADY_RESOLVED"

    def test_bug_6_refine_superseded_proposal_contract_violation(
        self, auth_api_context: tuple[TestClient, dict[str, str], dict[str, str], dict[str, str], Path]
    ) -> None:
        """CRITICAL DEFECT BUG 6: Attempting to refine a SUPERSEDED proposal fails to return 409.
        Line 4050 in ontology_studio.py only checks in ('ACCEPTED', 'REJECTED'), permitting
        superseded proposals to be refined again and corrupting proposal lineage.
        """
        client, admin_headers, _, _, studio_root = auth_api_context
        sid = f"sess_refine_sup_{uuid.uuid4().hex[:8]}"
        client.post(
            "/api/v1/ontology/studio/sessions",
            json={"session_id": sid, "domain_namespace": "asset_management"},
            headers=admin_headers,
        )
        pdir = studio_root / sid / "proposals"
        pdir.mkdir(parents=True, exist_ok=True)
        prop_file = pdir / "prop-superseded.json"
        prop_file.write_text(
            json.dumps({
                "proposal_id": "prop-superseded",
                "session_id": sid,
                "status": "SUPERSEDED",
                "superseded_by": "prop-v2",
                "delta": {"operations": []},
            }),
            encoding="utf-8",
        )

        resp = client.post(
            f"/api/v1/ontology/studio/sessions/{sid}/proposals/prop-superseded/refine",
            json={"steering_prompt": "Refining already superseded proposal"},
            headers=admin_headers,
        )
        # REMEDIATED: The endpoint returns 409 PROPOSAL_ALREADY_RESOLVED
        assert resp.status_code == 409
        assert resp.json().get("error_code") == "PROPOSAL_ALREADY_RESOLVED"


# ============================================================================
# FEATURE 6: STAGED CANDIDATE PROMOTER INVARIANT CHECKS & DEFECTS
# ============================================================================


class TestFeature6StagedCandidatePromoter:
    """Staged Candidate Promoter Invariant Checks (Path A & Path B) and Defects."""

    def test_path_a_rescore_boundary_logic(self) -> None:
        """Path A threshold logic: BM25 score >= 0.6500 passes, < 0.6500 fails."""
        verifier = get_default_grounding_verifier()

        # 1. High match query (> 0.6500)
        matches_high = verifier._matcher.match("Financial Instrument", top_k=5)
        assert len(matches_high) > 0
        assert matches_high[0].normalized_score >= 0.6500

        # 2. Nonsense query (< 0.6500)
        matches_low = verifier._matcher.match("XyZ987GibberishNonExistentTerm", top_k=5)
        top_score = matches_low[0].normalized_score if matches_low else 0.0
        assert top_score < 0.6500

    def test_bug_4_promoter_missing_op_id_runtime_crash(
        self, auth_api_context: tuple[TestClient, dict[str, str], dict[str, str], dict[str, str], Path]
    ) -> None:
        """CRITICAL DEFECT BUG 4 REMEDIATION: StagedCandidatePromoter succeeds with HTTP 200
        now that required op_id is generated and target_version is computed.
        """
        client, admin_headers, _, _, studio_root = auth_api_context
        sid = f"sess_bug4_{uuid.uuid4().hex[:8]}"
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
                    "candidate_id": "cand-test-defect",
                    "concept_name": "TestCandidateConcept",
                    "suggested_hub": "hub_test_candidate",
                }
            ]
        }
        (ev_dir / "staged_candidates.json").write_text(json.dumps(staged_payload), encoding="utf-8")

        # Execute Path B promotion
        resp = client.post(
            f"/api/v1/ontology/studio/sessions/{sid}/staged-candidates/cand-test-defect/promote",
            json={"path": "path_b", "waiver_rationale": "Valid architectural justification exceeding ten chars"},
            headers=admin_headers,
        )
        # REMEDIATED: Promotion succeeds with HTTP 200
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("promoted") is True
        assert data.get("status") == "PROMOTED"

    def test_bug_3_api_starter_pack_apply_str_delta_attribute_error(
        self, auth_api_context: tuple[TestClient, dict[str, str], dict[str, str], dict[str, str], Path]
    ) -> None:
        """CRITICAL DEFECT BUG 3 REMEDIATION: POST /starter-packs/apply succeeds with HTTP 200."""
        client, admin_headers, _, _, _ = auth_api_context
        sid = f"sess_bug3_{uuid.uuid4().hex[:8]}"
        res_create = client.post(
            "/api/v1/ontology/studio/sessions",
            json={"session_id": sid, "domain_namespace": "asset_management"},
            headers=admin_headers,
        )
        h0 = res_create.json()["head_sha256"]

        sp_res = client.post(
            f"/api/v1/ontology/studio/sessions/{sid}/starter-packs/apply",
            json={"pack_ids": ["core_spine"]},
            headers={**admin_headers, "If-Match": f'"{h0}"'},
        )
        # REMEDIATED: POST /starter-packs/apply succeeds with HTTP 200
        assert sp_res.status_code == 200
        data = sp_res.json()
        assert data.get("concept_count", 0) > 0


# ============================================================================
# FEATURE 7: UNSTRUCTURED-ONLY PIPELINE MODE
# ============================================================================


class TestFeature7UnstructuredOnlyPipelineMode:
    """Unstructured-Only Pipeline Mode execution."""

    def test_unstructured_only_validation_and_contract_synthesis(self, tmp_path: Path) -> None:
        """Verify unstructured_only=True bypasses validation error and synthesizes 0 dropped columns."""
        doc_file = tmp_path / "agreement.txt"
        doc_file.write_text("Master Service Agreement between LP and GP.", encoding="utf-8")

        ont = GraphAOntology(
            domain_namespace="test",
            canonical_business_concepts=[
                CanonicalBusinessConcept(
                    concept_uri="urn:opendv:concept:document",
                    concept_name="Document",
                    target_hub="hub_document",
                )
            ],
        )
        ont_file = tmp_path / "ont.json"
        ont_file.write_text(ont.model_dump_json(), encoding="utf-8")

        unstr_docs = [
            PipelineUnstructuredDocInput(
                doc_id="DOC-MSA-001",
                document_path=str(doc_file),
                record_source="UNSTRUCTURED",
            )
        ]

        spec = PipelineInputSpec(
            pipeline_id="pipe-unstr-01",
            project_id="proj-am-01",
            graph_a_ontology_path=str(ont_file),
            unstructured_documents=unstr_docs,
            unstructured_only=True,
        )

        # 1. Validation report passes
        val_report = validate_pipeline_inputs(spec, base_dir=tmp_path)
        assert not any(d["code"] == "UNSTRUCTURED_WITHOUT_RELATIONAL_SOURCE" for d in val_report.diagnostics)

        # 2. Cumulative contract synthesis has hub_document and sat_document_metadata
        contract = _synthesize_cumulative_contract(
            ontology=ont,
            cumulative_sources={},
            cumulative_source_systems={},
            cumulative_unstructured_docs=[(spec.unstructured_documents[0], "sha256_mock")],
            added_drift_cols_by_table={},
            current_wave_drift_tables=set(),
            wave_index=0,
            domain_namespace="test",
            deployed_contract=None,
        )
        assert any(h["name"] == "hub_document" for h in contract["hubs"])
        assert any(s["name"] == "sat_document_metadata" for s in contract["satellites"])
        assert len(contract["column_triage_ledger"]) == 6


# ============================================================================
# FEATURE 8: DUAL-CLI PARITY & RUNTIME DEFECTS
# ============================================================================


class TestFeature8DualCliParityAndDefects:
    """Empirical verification of Python and TypeScript CLI commands and execution defects."""

    def test_bug_1_cli_staged_promoter_init_mismatch(self) -> None:
        """CRITICAL DEFECT BUG 1: opendv ontology studio staged list & promote crash
        due to StagedCandidatePromoter.__init__() missing session_mgr argument.
        """
        # Create session first so command proceeds to promoter
        sid = f"cli_test_b1_{uuid.uuid4().hex[:8]}"
        subprocess.run(
            ["uv", "run", "--directory", str(REPO_ROOT), "opendv", "ontology", "studio", "create", "-s", sid, "--json"],
            capture_output=True,
            text=True,
            check=True,
        )

        # Run opendv ontology studio staged list
        res = subprocess.run(
            ["uv", "run", "--directory", str(REPO_ROOT), "opendv", "ontology", "studio", "staged", "list", "-s", sid, "--json"],
            capture_output=True,
            text=True,
        )
        assert res.returncode == 0
        data = json.loads(res.stdout)
        assert data.get("session_id") == sid
        assert "staged_candidates" in data

    def test_bug_2_cli_text_add_extra_field_validation(self) -> None:
        """CRITICAL DEFECT BUG 2 REMEDIATION: opendv ontology studio text add succeeds with exit 0."""
        sid = f"cli_test_b2_{uuid.uuid4().hex[:8]}"
        subprocess.run(
            ["uv", "run", "--directory", str(REPO_ROOT), "opendv", "ontology", "studio", "create", "-s", sid, "--json"],
            capture_output=True,
            text=True,
            check=True,
        )

        res = subprocess.run(
            ["uv", "run", "--directory", str(REPO_ROOT), "opendv", "ontology", "studio", "text", "add", "-s", sid, "-t", "hello", "--json"],
            capture_output=True,
            text=True,
        )
        assert res.returncode == 0
        data = json.loads(res.stdout)
        assert "source_id" in data or "source_sha256" in data

    def test_bug_cli_starter_pack_apply_bad_import(self) -> None:
        """CRITICAL DEFECT REMEDIATION: opendv ontology studio starter-pack apply succeeds with exit 0."""
        sid = f"cli_test_b3_{uuid.uuid4().hex[:8]}"
        subprocess.run(
            ["uv", "run", "--directory", str(REPO_ROOT), "opendv", "ontology", "studio", "create", "-s", sid, "--json"],
            capture_output=True,
            text=True,
            check=True,
        )

        res = subprocess.run(
            ["uv", "run", "--directory", str(REPO_ROOT), "opendv", "ontology", "studio", "starter-pack", "apply", "-s", sid, "--pack", "core_spine", "--json"],
            capture_output=True,
            text=True,
        )
        assert res.returncode == 0
        data = json.loads(res.stdout)
        assert data.get("applied") is True
