"""open-dv/tests/test_challenger_m1_6_empirical.py

Milestone M1 Iteration 3 Independent Empirical Challenger Suite.
Authored by challenger_m1_6 to stress-test:
1. Held-back candidate promotion:
   - Path A grounding threshold (>= 0.6500) enforcement & rejection.
   - Path B admin waiver RBAC (admin required), rationale length (>= 10 chars),
     HMAC signature generation, and waivers.json storage.
   - Delta operation IDs: verify all emitted operations contain non-empty
     op_id matching ^op-[a-z0-9_-]+$.
2. Unstructured-only pipeline mode (`ship-vault`):
   - Synthesis of hub_document, extraction links, and AI provenance satellites
     with empty relational sources.
   - Suppression of UNSTRUCTURED_WITHOUT_RELATIONAL_SOURCE when unstructured_only=True.
   - Emission of UNSTRUCTURED_WITHOUT_RELATIONAL_SOURCE when unstructured_only=False.
3. StudioAgentRunner deterministic fallback SLA and error handling:
   - Fallback cleanly creates proposals for uncommitted genesis states.
   - Fallback latency strictly adheres to <2.5s SLA across multiple iterations.
   - Exception in agent harness cleanly falls back to deterministic pipeline.
4. Dual-CLI parity remediation verification:
   - ApplyStarterPackRequest schema acceptance of pack, pack_id, and pack_ids.
   - Live opendvctl binary execution against standalone API server with starter pack.
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import threading
import time
import uuid
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
import uvicorn
from pydantic import ValidationError
from starlette.testclient import TestClient

from opendv_agent_harness.pipeline import (
    PipelineInputSpec,
    PipelineUnstructuredCandidateInput,
    PipelineUnstructuredDocInput,
    _synthesize_cumulative_contract,
    evolve_contract_single_run,
    validate_pipeline_inputs,
)
from opendv_core.models.graph_a import CanonicalBusinessConcept, GraphAOntology
from opendv_contracts.dtos.ingestion import EvidenceChunk, TableStructure
from opendv_contracts.dtos.starter_packs import ApplyStarterPackRequest
from opendv_platform_api import PlatformStateContainer
from opendv_platform_api.main import create_app
from opendv_service import OpenDVSettings
from opendv_service.studio.agent_runner import (
    DETERMINISTIC_FALLBACK_SLA_SECONDS,
    StudioAgentRunner,
)
from opendv_sources.asset_management.loader import get_core_spine_pack
from tests.parity.transports import ClientCliTransport

REPO_ROOT = Path(__file__).resolve().parents[1]
OP_ID_REGEX = re.compile(r"^op-[a-z0-9_-]+$")


@pytest.fixture
def auth_api_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[TestClient, dict[str, str], dict[str, str], dict[str, str], Path]:
    """Create isolated FastAPI client with caller credentials."""
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
        json={
            "domain_namespace": "asset_management",
            "enterprise_owner": "Enterprise Architecture",
        },
    )
    assert init_res.status_code == 200, init_res.text
    token = init_res.json()["local_token"]

    admin_headers = {"Authorization": f"Bearer {token}", "X-OpenDV-Role": "admin"}
    editor_headers = {"Authorization": f"Bearer {token}", "X-OpenDV-Role": "editor"}
    viewer_headers = {"Authorization": f"Bearer {token}", "X-OpenDV-Role": "viewer"}

    return client, admin_headers, editor_headers, viewer_headers, studio_root


# ==============================================================================
# SECTION 1: HELD-BACK CANDIDATE PROMOTION — PATH A & PATH B & OP_ID INTEGRITY
# ==============================================================================


class TestCandidatePromotionEmpirical:
    """Empirical verification of candidate promotion paths, thresholds, and delta op_ids."""

    def _setup_staged_candidate(
        self,
        session_dir: Path,
        candidate_id: str = "cand-test-001",
        concept_name: str = "PrivateFund",
        score: float = 0.5200,
        attributes: list[dict[str, str]] | None = None,
        dependent_links: list[dict[str, Any]] | None = None,
    ) -> Path:
        """Seed a held-back candidate in session evidence directory."""
        evidence_dir = session_dir / "evidence"
        evidence_dir.mkdir(parents=True, exist_ok=True)
        staged_file = evidence_dir / "staged_candidates.json"
        top_uri = "https://spec.edmcouncil.org/fibo/ontology/SEC/Funds/InvestmentFund"
        staged_data = {
            "session_id": session_dir.name,
            "staged_candidates": [
                {
                    "candidate_id": candidate_id,
                    "concept_name": concept_name,
                    "suggested_hub": f"hub_{concept_name.lower()}",
                    "top_match_uri": top_uri,
                    "top_match_score": score,
                    "reason_held_back": "GROUNDING_THRESHOLD_UNMET",
                    "evidence_refs": [{"chunk_id": "chunk_001", "verbatim_quote": "test fund"}],
                    "attributes": attributes
                    or [
                        {"attribute_name": "fund_size", "data_type": "numeric"},
                        {"attribute_name": "inception_date", "data_type": "date"},
                    ],
                    "dependent_links": dependent_links
                    or [
                        {
                            "relationship_uri": (
                                f"urn:opendv:relationship:{concept_name.lower()}_sponsor"
                            ),
                            "target_link": f"lnk_{concept_name.lower()}_sponsor",
                            "endpoints": [
                                f"urn:opendv:concept:{concept_name.lower()}",
                                "urn:opendv:concept:legal_entity",
                            ],
                        }
                    ],
                }
            ],
        }
        staged_file.write_text(json.dumps(staged_data, indent=2), encoding="utf-8")
        return staged_file

    def test_path_a_grounding_threshold_enforcement_success(
        self,
        auth_api_context: tuple[
            TestClient, dict[str, str], dict[str, str], dict[str, str], Path
        ],
    ) -> None:
        """Verify Path A succeeds when re-scored synonyms meet or exceed 0.6500 threshold."""
        client, admin_headers, _, _, studio_root = auth_api_context
        sid = f"sess_pa_succ_{uuid.uuid4().hex[:8]}"
        res = client.post(
            "/api/v1/ontology/studio/sessions",
            json={"session_id": sid, "domain_namespace": "asset_management"},
            headers=admin_headers,
        )
        assert res.status_code in (200, 201)

        self._setup_staged_candidate(
            session_dir=studio_root / sid,
            candidate_id="cand-fund-01",
            concept_name="PrivateFund",
            score=0.5200,
        )

        promote_res = client.post(
            f"/api/v1/ontology/studio/sessions/{sid}/staged-candidates/cand-fund-01/promote",
            json={
                "path": "path_a",
                "synonyms": ["InvestmentFund", "MutualFund", "CollectiveInvestmentVehicle"],
            },
            headers=admin_headers,
        )
        assert promote_res.status_code == 200, promote_res.text
        data = promote_res.json()
        assert data["status"] == "PROMOTED"
        assert data["path"] == "path_a"
        assert data["confidence_score"] >= 0.6500
        assert data["fibo_uri"] is not None

        # Verify operations
        ops = data["operations"]
        assert len(ops) >= 4
        for op in ops:
            assert "op_id" in op
            assert op["op_id"]
            assert OP_ID_REGEX.match(op["op_id"])

        # Verify candidate is removed from staged holding area
        list_res = client.get(
            f"/api/v1/ontology/studio/sessions/{sid}/staged-candidates",
            headers=admin_headers,
        )
        assert list_res.status_code == 200
        staged_list = list_res.json().get("staged_candidates", [])
        assert not any(c["candidate_id"] == "cand-fund-01" for c in staged_list)

    def test_path_a_grounding_threshold_rejection_below_threshold(
        self,
        auth_api_context: tuple[
            TestClient, dict[str, str], dict[str, str], dict[str, str], Path
        ],
    ) -> None:
        """Verify Path A strictly rejects promotion when score < 0.6500."""
        client, admin_headers, _, _, studio_root = auth_api_context
        sid = f"sess_pa_fail_{uuid.uuid4().hex[:8]}"
        res = client.post(
            "/api/v1/ontology/studio/sessions",
            json={"session_id": sid, "domain_namespace": "asset_management"},
            headers=admin_headers,
        )
        assert res.status_code in (200, 201)

        self._setup_staged_candidate(
            session_dir=studio_root / sid,
            candidate_id="cand-obscure-01",
            concept_name="ObscureSyndicateX",
            score=0.3100,
        )

        promote_res = client.post(
            f"/api/v1/ontology/studio/sessions/{sid}/staged-candidates/cand-obscure-01/promote",
            json={
                "path": "path_a",
                "synonyms": ["UnmatchedNonsenseAlpha999", "ZzQqXxJunkTerm"],
            },
            headers=admin_headers,
        )
        assert promote_res.status_code == 422, promote_res.text
        err = promote_res.json()
        assert err.get("error_code") == "ONT_EVO_CONTRACT_VIOLATION"
        assert "0.6500" in (err.get("detail") or err.get("error", ""))

        # Verify candidate remains staged
        list_res = client.get(
            f"/api/v1/ontology/studio/sessions/{sid}/staged-candidates",
            headers=admin_headers,
        )
        assert list_res.status_code == 200
        staged_list = list_res.json().get("staged_candidates", [])
        assert any(c["candidate_id"] == "cand-obscure-01" for c in staged_list)

    def test_path_a_empty_synonyms_rejected(
        self,
        auth_api_context: tuple[
            TestClient, dict[str, str], dict[str, str], dict[str, str], Path
        ],
    ) -> None:
        """Verify Path A rejects empty or whitespace-only synonyms list."""
        client, admin_headers, _, _, studio_root = auth_api_context
        sid = f"sess_pa_empty_{uuid.uuid4().hex[:8]}"
        res = client.post(
            "/api/v1/ontology/studio/sessions",
            json={"session_id": sid, "domain_namespace": "asset_management"},
            headers=admin_headers,
        )
        assert res.status_code in (200, 201)

        self._setup_staged_candidate(
            session_dir=studio_root / sid,
            candidate_id="cand-empty-01",
            concept_name="PrivateFund",
            score=0.5200,
        )

        promote_res = client.post(
            f"/api/v1/ontology/studio/sessions/{sid}/staged-candidates/cand-empty-01/promote",
            json={"path": "path_a", "synonyms": ["   ", ""]},
            headers=admin_headers,
        )
        assert promote_res.status_code == 400, promote_res.text
        assert promote_res.json().get("error_code") == "SYNTAX_INVALID_ARGUMENT"

    def test_path_b_admin_waiver_rbac_enforcement(
        self,
        auth_api_context: tuple[
            TestClient, dict[str, str], dict[str, str], dict[str, str], Path
        ],
    ) -> None:
        """Verify Path B requires admin role and strictly forbids non-admin callers (403)."""
        client, admin_headers, editor_headers, viewer_headers, studio_root = auth_api_context
        sid = f"sess_pb_rbac_{uuid.uuid4().hex[:8]}"
        res = client.post(
            "/api/v1/ontology/studio/sessions",
            json={"session_id": sid, "domain_namespace": "asset_management"},
            headers=admin_headers,
        )
        assert res.status_code in (200, 201)

        self._setup_staged_candidate(
            session_dir=studio_root / sid,
            candidate_id="cand-waiver-01",
            concept_name="CustomPrivateAsset",
            score=0.4200,
        )

        valid_payload = {
            "path": "path_b",
            "waiver_rationale": "Enterprise Chief Data Steward approved for Q4 release.",
        }

        # 1. Test viewer receives 403
        v_res = client.post(
            f"/api/v1/ontology/studio/sessions/{sid}/staged-candidates/cand-waiver-01/promote",
            json=valid_payload,
            headers=viewer_headers,
        )
        assert v_res.status_code == 403, v_res.text
        assert v_res.json().get("error_code") in (
            "FORBIDDEN_ADMIN_REQUIRED",
            "SERVER_OPERATION_FORBIDDEN",
        )

        # 2. Test editor receives 403
        e_res = client.post(
            f"/api/v1/ontology/studio/sessions/{sid}/staged-candidates/cand-waiver-01/promote",
            json=valid_payload,
            headers=editor_headers,
        )
        assert e_res.status_code == 403, e_res.text
        assert e_res.json().get("error_code") in (
            "FORBIDDEN_ADMIN_REQUIRED",
            "SERVER_OPERATION_FORBIDDEN",
        )

    def test_path_b_rationale_validation(
        self,
        auth_api_context: tuple[
            TestClient, dict[str, str], dict[str, str], dict[str, str], Path
        ],
    ) -> None:
        """Verify Path B rejects rationale shorter than 10 characters."""
        client, admin_headers, _, _, studio_root = auth_api_context
        sid = f"sess_pb_rat_{uuid.uuid4().hex[:8]}"
        res = client.post(
            "/api/v1/ontology/studio/sessions",
            json={"session_id": sid, "domain_namespace": "asset_management"},
            headers=admin_headers,
        )
        assert res.status_code in (200, 201)

        self._setup_staged_candidate(
            session_dir=studio_root / sid,
            candidate_id="cand-waiver-02",
            concept_name="CustomPrivateAsset",
            score=0.4200,
        )

        short_rationales = ["", "short", "123456789", "   abc   "]
        for r in short_rationales:
            res = client.post(
                f"/api/v1/ontology/studio/sessions/{sid}/staged-candidates/cand-waiver-02/promote",
                json={"path": "path_b", "waiver_rationale": r},
                headers=admin_headers,
            )
            assert res.status_code == 422, f"Expected 422 for '{r}', got {res.status_code}"
            assert res.json().get("error_code") == "INVALID_GROUNDING_WAIVER"

    def test_path_b_waivers_storage_and_hmac_integrity(
        self,
        auth_api_context: tuple[
            TestClient, dict[str, str], dict[str, str], dict[str, str], Path
        ],
    ) -> None:
        """Verify Path B writes signed StewardWaiverRecord into waivers.json with HMAC."""
        client, admin_headers, _, _, studio_root = auth_api_context
        sid = f"sess_pb_store_{uuid.uuid4().hex[:8]}"
        res = client.post(
            "/api/v1/ontology/studio/sessions",
            json={"session_id": sid, "domain_namespace": "asset_management"},
            headers=admin_headers,
        )
        assert res.status_code in (200, 201)

        self._setup_staged_candidate(
            session_dir=studio_root / sid,
            candidate_id="cand-waiver-03",
            concept_name="PrivateCreditFacility",
            score=0.4800,
        )

        rationale = "Formal business justification: direct lending credit facility agreed by Risk."
        promote_res = client.post(
            f"/api/v1/ontology/studio/sessions/{sid}/staged-candidates/cand-waiver-03/promote",
            json={"path": "path_b", "waiver_rationale": rationale},
            headers=admin_headers,
        )
        assert promote_res.status_code == 200, promote_res.text
        data = promote_res.json()
        waiver_id = data.get("waiver_id")
        assert waiver_id is not None

        # Verify waivers.json file exists and contains valid signed record
        waivers_file = studio_root / sid / "waivers.json"
        assert waivers_file.is_file()
        waiver_content = json.loads(waivers_file.read_text(encoding="utf-8"))
        records = waiver_content.get("waivers", [])
        assert len(records) >= 1

        rec = next((r for r in records if r.get("waiver_id") == waiver_id), None)
        assert rec is not None
        assert rec["rationale"] == rationale
        assert rec["authorized_role"] == "admin"
        assert "urn:opendv:concept:privatecreditfacility" in rec["target_concept_uris"]
        assert rec.get("signature_or_token") is not None
        assert len(rec["signature_or_token"]) > 20

        # Verify proposal delta metadata has waiver details
        prop_file = studio_root / sid / "proposals" / f"{data['proposal_id']}.json"
        assert prop_file.is_file()
        prop_data = json.loads(prop_file.read_text(encoding="utf-8"))
        delta_meta = prop_data["delta"].get("metadata", {})
        assert delta_meta.get("low_confidence_grounding_waiver") is True
        assert delta_meta.get("waiver_id") == waiver_id
        assert delta_meta.get("promotion_path") == "path_b"

    def test_delta_operation_ids_regex_and_uniqueness(
        self,
        auth_api_context: tuple[
            TestClient, dict[str, str], dict[str, str], dict[str, str], Path
        ],
    ) -> None:
        """Verify all operations emitted during promotion have unique op_id matching regex."""
        client, admin_headers, _, _, studio_root = auth_api_context
        sid = f"sess_opid_{uuid.uuid4().hex[:8]}"
        res = client.post(
            "/api/v1/ontology/studio/sessions",
            json={"session_id": sid, "domain_namespace": "asset_management"},
            headers=admin_headers,
        )
        assert res.status_code in (200, 201)

        self._setup_staged_candidate(
            session_dir=studio_root / sid,
            candidate_id="cand-opid-01",
            concept_name="CollateralAccount",
            score=0.4500,
            attributes=[
                {"attribute_name": "currency", "data_type": "string"},
                {"attribute_name": "pledged_amount", "data_type": "numeric"},
            ],
            dependent_links=[
                {
                    "relationship_uri": "urn:opendv:relationship:account_custodian",
                    "target_link": "lnk_account_custodian",
                    "endpoints": [
                        "urn:opendv:concept:collateralaccount",
                        "urn:opendv:concept:legal_entity",
                    ],
                }
            ],
        )

        promote_res = client.post(
            f"/api/v1/ontology/studio/sessions/{sid}/staged-candidates/cand-opid-01/promote",
            json={
                "path": "path_b",
                "waiver_rationale": "Enterprise collateral account required for ISDA compliance.",
            },
            headers=admin_headers,
        )
        assert promote_res.status_code == 200, promote_res.text
        ops = promote_res.json()["operations"]
        assert len(ops) >= 5

        op_ids = [op.get("op_id") for op in ops]
        assert all(op_id is not None for op_id in op_ids)
        assert len(op_ids) == len(set(op_ids)), f"Duplicate op_ids detected: {op_ids}"

        for op_id in op_ids:
            assert OP_ID_REGEX.match(op_id), f"op_id '{op_id}' violates regex ^op-[a-z0-9_-]+$"


# ==============================================================================
# SECTION 2: UNSTRUCTURED-ONLY PIPELINE MODE (SHIP-VAULT)
# ==============================================================================


class TestUnstructuredOnlyPipelineModeEmpirical:
    """Empirical verification of unstructured-only mode in pipeline builder."""

    def test_unstructured_only_pipeline_synthesizes_hub_document_and_links(
        self, tmp_path: Path
    ) -> None:
        """Verify ship-vault synthesizes hub_document and extraction link."""
        doc_path = tmp_path / "credit_agreement.txt"
        doc_path.write_text(
            "Credit and Term Loan Agreement between Borrower and Syndicate.",
            encoding="utf-8",
        )

        cand = PipelineUnstructuredCandidateInput(
            target_hub="hub_agreement",
            extracted_business_key="AGR-CREDIT-2026",
            verbatim_quote="Credit and Term Loan Agreement",
            confidence_score=0.96,
        )
        unstr_doc = PipelineUnstructuredDocInput(
            document_path=str(doc_path),
            doc_id="doc-credit-001",
            link_name="lnk_agreement_document",
            extracted_candidates=[cand],
        )

        spine = get_core_spine_pack()
        deployed_contract = {
            "hubs": [
                {
                    "name": "hub_agreement",
                    "business_key_sources": [
                        {"bronze_table": "bronze.agreements", "key_columns": ["agreement_id"]}
                    ],
                }
            ]
        }

        # Case A: Evolve deployed contract containing hub_agreement
        contract, _ = evolve_contract_single_run(
            deployed_contract=deployed_contract,
            ontology=spine,
            discovered_feeds=[],
            cumulative_unstructured_docs=[unstr_doc],
        )

        hub_names = [h["name"] for h in contract.get("hubs", [])]
        link_names = [lnk["name"] for lnk in contract.get("links", [])]
        sat_names = [s["name"] for s in contract.get("satellites", [])]

        assert "hub_document" in hub_names
        assert "lnk_agreement_document" in link_names
        assert any("ai_provenance" in s for s in sat_names)
        assert any("mas_document_extractions" in s for s in sat_names)

        # Case B: Synthesize with completely empty deployed contract
        contract_pure = _synthesize_cumulative_contract(
            ontology=spine,
            cumulative_sources={},
            cumulative_source_systems={},
            cumulative_unstructured_docs=[(unstr_doc, "sha256dummycredit")],
            added_drift_cols_by_table={},
            wave_index=0,
            domain_namespace="asset_management",
            deployed_contract=None,
        )
        doc_hub_pure = next(h for h in contract_pure["hubs"] if h["name"] == "hub_document")
        bk_src_pure = doc_hub_pure["business_key_sources"][0]
        assert bk_src_pure["bronze_table"] == "bronze.unstructured_documents"
        assert bk_src_pure["key_columns"] == ["document_id"]
        assert bk_src_pure["key_authority_tier"] == "TIER_1_GLOBAL_STANDARD"

    def test_unstructured_without_relational_sources_flag_gating(
        self, tmp_path: Path
    ) -> None:
        """Verify UNSTRUCTURED_WITHOUT_RELATIONAL_SOURCE diagnostic is emitted iff not set."""
        doc_path = tmp_path / "memo.txt"
        doc_path.write_text("Investment Committee Memorandum.", encoding="utf-8")

        cand = PipelineUnstructuredCandidateInput(
            target_hub="hub_agreement",
            extracted_business_key="MEMO-01",
            verbatim_quote="Memorandum",
            confidence_score=0.90,
            char_start=21,
            char_end=31,
        )
        unstr_doc = PipelineUnstructuredDocInput(
            document_path=str(doc_path),
            doc_id="doc-memo-001",
            extracted_candidates=[cand],
        )

        ont = GraphAOntology(
            domain_namespace="asset_management",
            canonical_business_concepts=[
                CanonicalBusinessConcept(
                    concept_uri="urn:ontology:entity:document",
                    concept_name="Document",
                    definition="Document concept",
                    target_hub="hub_document",
                )
            ],
        )
        ont_file = tmp_path / "ont.json"
        ont_file.write_text(ont.model_dump_json(), encoding="utf-8")

        # Case 1: unstructured_only=False -> diagnostic emitted
        spec_false = PipelineInputSpec(
            pipeline_id="pipe-unstr-false",
            project_id="proj-am",
            domain_namespace="asset_management",
            unstructured_only=False,
            graph_a_ontology_path=str(ont_file),
            unstructured_documents=[unstr_doc],
        )
        rep_false = validate_pipeline_inputs(
            spec_false, base_dir=tmp_path, workspace_root=tmp_path
        )
        codes_false = [d.get("code") for d in rep_false.diagnostics]
        assert "UNSTRUCTURED_WITHOUT_RELATIONAL_SOURCE" in codes_false

        # Case 2: unstructured_only=True -> diagnostic suppressed
        spec_true = PipelineInputSpec(
            pipeline_id="pipe-unstr-true",
            project_id="proj-am",
            domain_namespace="asset_management",
            unstructured_only=True,
            graph_a_ontology_path=str(ont_file),
            unstructured_documents=[unstr_doc],
        )
        rep_true = validate_pipeline_inputs(spec_true, base_dir=tmp_path, workspace_root=tmp_path)
        codes_true = [d.get("code") for d in rep_true.diagnostics]
        assert "UNSTRUCTURED_WITHOUT_RELATIONAL_SOURCE" not in codes_true


# ==============================================================================
# SECTION 3: AGENT RUNNER DETERMINISTIC FALLBACK SLA & GENESIS HANDLING
# ==============================================================================


class TestStudioAgentRunnerEmpirical:
    """Empirical verification of deterministic fallback latency and fault recovery."""

    def test_agent_runner_fallback_cleanly_handles_uncommitted_genesis_state(self) -> None:
        """Verify StudioAgentRunner cleanly handles base_ontology=None (uncommitted genesis)."""
        runner = StudioAgentRunner()
        tbl = TableStructure(
            table_id="tbl_portfolios",
            headers=["portfolio_id", "portfolio_name", "strategy"],
            rows=[["P-001", "Global Alpha", "LONG_SHORT"]],
        )

        proposal = runner(
            base_ontology=None,
            tables=[tbl],
            mode="deterministic",
        )
        assert proposal is not None
        assert proposal.delta.base_version == "0.0.0"
        assert len(proposal.delta.operations) > 0
        assert any("engine_mode:deterministic_fallback" in d for d in proposal.diagnostics)

    def test_agent_runner_deterministic_fallback_sla(self) -> None:
        """Verify deterministic fallback latency is strictly below 2.5s SLA threshold."""
        runner = StudioAgentRunner()
        chunk = EvidenceChunk(
            chunk_id="chk-sla-01",
            document_id="doc-sla-01",
            text="Private credit direct lending provides secured debt financing.",
        )
        tbl = TableStructure(
            table_id="tbl_facilities",
            headers=["facility_id", "borrower_name", "facility_amount", "currency"],
            rows=[["FAC-100", "MidMarket Corp", "50000000", "USD"]],
        )

        durations: list[float] = []
        for i in range(10):
            t0 = time.monotonic()
            proposal = runner(
                base_ontology=None,
                evidence_chunks=[chunk],
                tables=[tbl],
                mode="deterministic",
            )
            dt = time.monotonic() - t0
            durations.append(dt)
            assert dt < DETERMINISTIC_FALLBACK_SLA_SECONDS, (
                f"Iteration {i} fallback duration {dt:.4f}s exceeded SLA"
            )
            assert len(proposal.delta.operations) > 0

        avg_dt = sum(durations) / len(durations)
        max_dt = max(durations)
        assert avg_dt < 0.500, f"Average fallback latency {avg_dt:.4f}s was too slow"
        assert max_dt < DETERMINISTIC_FALLBACK_SLA_SECONDS

    def test_agent_runner_graceful_recovery_on_harness_crash(self) -> None:
        """Verify StudioAgentRunner catches agent harness exceptions and seamlessly falls back."""

        class MockFailingHarness:
            provider_name = "mock_failing_agy"

            def __init__(self) -> None:
                pass

        runner = StudioAgentRunner(harness=MockFailingHarness())
        runner._is_harness_executable_available = MagicMock(return_value=True)
        runner._run_persona_orchestration_sync = MagicMock(
            side_effect=RuntimeError("Simulated LLM API 500 internal server error")
        )

        tbl = TableStructure(
            table_id="tbl_trades",
            headers=["trade_id", "security_id", "quantity", "price"],
            rows=[["TR-01", "SEC-US-01", "100", "150.25"]],
        )

        proposal = runner(
            base_ontology=None,
            tables=[tbl],
            mode="agent",
        )
        assert proposal is not None
        assert any("engine_mode:deterministic_fallback" in d for d in proposal.diagnostics)
        assert proposal.recommender_id.startswith("deterministic:")


# ==============================================================================
# SECTION 4: DUAL-CLI STARTER PACK PARITY REMEDIATION VERIFICATION
# ==============================================================================


class TestDualCliStarterPackParityRemediation:
    """Empirical verification of worker_m1_3 remediation on starter-pack apply parity."""

    def test_apply_starter_pack_request_schema_normalization(self) -> None:
        """Verify ApplyStarterPackRequest normalizes pack, pack_id, and pack_ids correctly."""
        # 1. Using pack alias
        req1 = ApplyStarterPackRequest.model_validate({"pack": "core_spine"})
        assert req1.pack_ids == ["core_spine"]
        assert req1.pack_id == "core_spine"

        # 2. Using pack_id
        req2 = ApplyStarterPackRequest.model_validate({"pack_id": "core_spine"})
        assert req2.pack_ids == ["core_spine"]
        assert req2.pack_id == "core_spine"

        # 3. Using pack_ids
        req3 = ApplyStarterPackRequest.model_validate(
            {"pack_ids": ["core_spine", "public_equities_oms"]}
        )
        assert req3.pack_ids == ["core_spine", "public_equities_oms"]

        # 4. Forbid unknown extra inputs
        with pytest.raises(ValidationError):
            ApplyStarterPackRequest.model_validate({"pack": "core_spine", "unknown_field": True})

    def test_live_opendvctl_binary_starter_pack_apply_against_live_server(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Verify compiled native opendvctl binary applies starter pack against live server."""
        with tempfile.TemporaryDirectory() as td:
            tpath = Path(td)
            studio_root = tpath / "studio"
            studio_root.mkdir(parents=True, exist_ok=True)
            ws_dir = tpath / "ws"
            ws_dir.mkdir(parents=True, exist_ok=True)

            monkeypatch.setenv("OPENDV_STUDIO_ROOT", str(studio_root))
            monkeypatch.setenv("OPENDV_TOKEN", "token-challenger-m1-6")
            monkeypatch.setenv("OPENDV_STUDIO_TOKEN", "token-challenger-m1-6")
            monkeypatch.setenv("OPENDV_SERVER_MODE", "local_standalone")

            state = PlatformStateContainer(workspace_root=Path("."))
            app = create_app(platform_state=state)
            port = 58960

            class SilentServer(uvicorn.Server):
                def install_signal_handlers(self) -> None:
                    pass

            server = SilentServer(
                uvicorn.Config(
                    app, host="127.0.0.1", port=port, log_level="error", access_log=False
                )
            )
            t = threading.Thread(target=server.run, daemon=True)
            t.start()
            time.sleep(1.0)

            try:
                transport = ClientCliTransport(
                    workspace_dir=ws_dir, server_url=f"http://127.0.0.1:{port}", role="admin"
                )

                # Create session
                code_create, _out_c, err_c = transport.invoke_raw(
                    ["ontology", "studio", "create", "-s", "s-challenger-live", "--json"]
                )
                assert code_create == 0, f"Session creation failed ({code_create}): {err_c}"

                # Apply starter pack via opendvctl
                code_apply, out_apply, err_apply = transport.invoke_raw([
                    "ontology",
                    "studio",
                    "starter-pack",
                    "apply",
                    "-s",
                    "s-challenger-live",
                    "--pack",
                    "core_spine",
                    "--json",
                ])
                assert code_apply == 0, f"starter-pack apply failed ({code_apply}): {err_apply}"

                res_data = json.loads(out_apply)
                assert res_data.get("applied") is True
                assert res_data.get("concept_count") == 6
                assert res_data.get("relationship_count") == 6
                assert res_data.get("ontology_sha256") is not None
            finally:
                server.should_exit = True
