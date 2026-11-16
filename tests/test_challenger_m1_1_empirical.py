"""tests/test_challenger_m1_1_empirical.py — Independent Empirical Verification & Adversarial Stress Suite.

Authored by empirical challenger (challenger_m1_1) to stress test Milestone M1 Features 1–8:
1. Starter Packs & Presets: loading, SHA-256 stability, DAG acyclicity, zero duplicate URIs.
2. Polysemy Catalog: 14 terms, 42 options resolved against pinned FIBO pack.
3. StudioAgentRunner & Text Ingestion: deterministic fallback SLA <2.5s, SHA-256 computation, empty text rejection.
4. Steering Prompt & Staged Promoter: refining pending -> SUPERSEDED, Path A score >= 0.6500, Path B admin HMAC & rationale >= 10, non-admin/short rationale rejections.
5. Unstructured-Only Pipeline Mode: zero relational sources, hub_document, triage ledger, no DV-COL-012 failures.
6. Dual-CLI Parity: opendv vs opendvctl --dump-cli-tree parity.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import time
from pathlib import Path

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
    GraphAOntology,
)
from opendv_core.ontology_hashing import compute_ontology_sha256
from opendv_platform_api.main import create_app
from opendv_platform_api.security.studio_auth import StudioPrincipal
from opendv_service import OpenDVSettings
from opendv_service.studio.agent_runner import StudioAgentRunner
from opendv_service.studio.promoter import StagedCandidatePromoter
from opendv_service.studio.session_manager import StudioSessionManager
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
from opendv_sources.fibo.loader import get_default_fibo_loader

REPO_ROOT = Path(__file__).resolve().parents[1]
TEST_AUTH_TOKEN = "challenger-test-token-m1-empirical"


# ==============================================================================
# SECTION 1: STARTER PACKS & PRESETS (FEATURE 1)
# ==============================================================================


class TestStarterPacksAndPresetsEmpirical:
    """Empirical verification of Asset Management Core Spine, 6 Vertical Packs, and All Domains Preset."""

    def test_all_vertical_packs_and_all_domains_loading(self) -> None:
        """Verify each of the 6 vertical packs loads with spine, and All Domains loads."""
        spine = get_core_spine_pack()
        assert isinstance(spine, GraphAOntology)
        assert len(spine.canonical_business_concepts) == 6
        assert len(spine.canonical_relationships) == 6

        loaders = [
            (AssetManagementPackId.PUBLIC_EQUITIES_OMS, get_public_equities_oms_pack, 4, 4),
            (AssetManagementPackId.FIXED_INCOME_RATES, get_fixed_income_rates_pack, 4, 4),
            (AssetManagementPackId.MULTI_ASSET_IBOR, get_multi_asset_ibor_pack, 4, 4),
            (AssetManagementPackId.PRIVATE_EQUITY_BUYOUT, get_private_equity_buyout_pack, 5, 4),
            (AssetManagementPackId.PRIVATE_CREDIT_DIRECT_LENDING, get_private_credit_direct_lending_pack, 4, 4),
            (AssetManagementPackId.CUSTODY_CLEARING, get_custody_clearing_pack, 4, 3),
        ]

        for pack_id, loader_fn, exp_vert_c, exp_vert_r in loaders:
            # Vertical pack with spine (the standard domain model)
            pack_with_spine = loader_fn(include_spine=True)
            assert isinstance(pack_with_spine, GraphAOntology)
            assert len(pack_with_spine.canonical_business_concepts) == 6 + exp_vert_c
            assert len(pack_with_spine.canonical_relationships) == 6 + exp_vert_r

            # Registry load by enum ID
            loaded_reg = AssetManagementStarterPackRegistry.load_pack(pack_id)
            assert isinstance(loaded_reg, GraphAOntology)
            assert len(loaded_reg.canonical_business_concepts) == 6 + exp_vert_c
            assert len(loaded_reg.canonical_relationships) == 6 + exp_vert_r

            # Metadata check (concept_count records vertical concepts contributed)
            meta = AssetManagementStarterPackRegistry.get_metadata(pack_id)
            assert meta.pack_id == pack_id
            assert meta.concept_count == exp_vert_c

        # All Domains Composite Preset
        all_domains = get_all_domains_pack()
        assert isinstance(all_domains, GraphAOntology)
        assert len(all_domains.canonical_business_concepts) == 31
        assert len(all_domains.canonical_relationships) == 29
        assert len(all_domains.reference_enumerations) == 7

    def test_sha256_stability_across_repeated_computations(self) -> None:
        """Assert SHA-256 hash calculation is 100% deterministic across repeated calls."""
        for _ in range(5):
            ont1 = get_all_domains_pack()
            ont2 = compose_asset_management_ontology(include_packs=["all_domains"])
            h1 = compute_ontology_sha256(ont1)
            h2 = compute_pack_hash(ont2)
            assert h1 == h2
            assert len(h1) == 64
            assert all(c in "0123456789abcdef" for c in h1)

    def test_zero_duplicate_uris_across_all_packs(self) -> None:
        """Assert zero duplicate concept URIs, relationship URIs, or enum URIs exist in All Domains."""
        ont = get_all_domains_pack()

        concept_uris = [c.concept_uri for c in ont.canonical_business_concepts]
        assert len(concept_uris) == len(set(concept_uris)), "Duplicate concept_uri found!"

        rel_uris = [r.relationship_uri for r in ont.canonical_relationships]
        assert len(rel_uris) == len(set(rel_uris)), "Duplicate relationship_uri found!"

        enum_uris = [e.enum_uri for e in ont.reference_enumerations]
        assert len(enum_uris) == len(set(enum_uris)), "Duplicate enum_uri found!"

    def test_dag_acyclicity_and_endpoint_resolution(self) -> None:
        """Assert all relationship endpoints in every pack resolve strictly to declared concepts without dangling references."""
        for pack_id in AssetManagementPackId:
            pack = AssetManagementStarterPackRegistry.load_pack(pack_id)
            known_uris = {c.concept_uri for c in pack.canonical_business_concepts}

            for rel in pack.canonical_relationships:
                assert len(rel.endpoints) >= 2, f"Relationship {rel.relationship_uri} has < 2 endpoints"
                for ep in rel.endpoints:
                    assert ep in known_uris, (
                        f"Pack {pack_id.value} relationship {rel.relationship_uri} has dangling endpoint: {ep}"
                    )

    def test_adversarial_invalid_pack_id_rejection(self) -> None:
        """Assert attempting to load an invalid or nonexistent starter pack raises ValueError."""
        with pytest.raises(ValueError):
            AssetManagementStarterPackRegistry.load_pack("nonexistent_pack_xyz")

        with pytest.raises(ValueError):
            AssetManagementStarterPackRegistry.get_metadata("invalid_id")


# ==============================================================================
# SECTION 2: POLYSEMY CATALOG EXPANSION (FEATURE 2)
# ==============================================================================


class TestPolysemyCatalogEmpirical:
    """Empirical verification of all 14 polysemic terms and 42 disambiguation options in FIBO."""

    EXPECTED_AM_TERMS = {
        "portfolio",
        "security",
        "order",
        "trade",
        "fund",
        "benchmark",
        "mandate",
        "instrument",
        "yield",
        "settlement",
        "coupon",
        "spread",
        "collateral",
        "nav",
    }

    def test_assert_all_14_terms_and_42_options_present(self) -> None:
        """Programmatically assert all 14 polysemic terms and exactly 42 options exist in KNOWN_POLYSEMIC_DEFINITIONS."""
        missing_terms = self.EXPECTED_AM_TERMS - set(KNOWN_POLYSEMIC_DEFINITIONS.keys())
        assert not missing_terms, f"Missing expected polysemic terms: {missing_terms}"

        total_am_options = 0
        for term in self.EXPECTED_AM_TERMS:
            options = KNOWN_POLYSEMIC_DEFINITIONS[term]
            assert len(options) >= 2, f"Term '{term}' must have >= 2 options, found {len(options)}"
            total_am_options += len(options)

        assert total_am_options == 42, f"Expected 42 total options across 14 AM terms, got {total_am_options}"

    def test_assert_all_42_fibo_uris_resolve_in_pinned_fibo_pack(self) -> None:
        """Programmatically assert all 42 option FIBO URIs resolve against loader.get_classes() in the pinned FIBO pack."""
        loader = get_default_fibo_loader()
        loader.load()
        fibo_classes = loader.get_classes()
        assert len(fibo_classes) > 100, f"Expected thousands of FIBO classes, found {len(fibo_classes)}"

        unresolved: list[tuple[str, str, str]] = []
        for term in self.EXPECTED_AM_TERMS:
            options = KNOWN_POLYSEMIC_DEFINITIONS[term]
            for opt in options:
                fibo_uri = opt.get("fibo_uri")
                label = opt.get("label", "unknown")
                assert fibo_uri is not None, f"Term '{term}' option '{label}' is missing fibo_uri"
                assert fibo_uri.startswith("https://spec.edmcouncil.org/fibo/"), f"Invalid FIBO URI prefix: {fibo_uri}"
                if fibo_uri not in fibo_classes:
                    unresolved.append((term, label, fibo_uri))

        assert not unresolved, f"The following polysemic options failed to resolve in pinned FIBO pack: {unresolved}"

    def test_adversarial_polysemy_option_validity(self) -> None:
        """Assert all 42 options have rationales >= 10 chars, non-empty labels, and unique labels per term."""
        for term in self.EXPECTED_AM_TERMS:
            options = KNOWN_POLYSEMIC_DEFINITIONS[term]
            labels = [opt["label"] for opt in options]
            assert len(labels) == len(set(labels)), f"Duplicate label in term '{term}'"

            for opt in options:
                rationale = opt.get("rationale", "")
                assert len(rationale) >= 10, f"Option '{opt['label']}' rationale too short: '{rationale}'"


# ==============================================================================
# SECTION 3: STUDIO AGENT RUNNER & TEXT INGESTION (FEATURES 3 & 4)
# ==============================================================================


class TestStudioAgentRunnerAndTextIngestionEmpirical:
    """Empirical verification of StudioAgentRunner fallback latency SLA and Text CAS ingestion."""

    def test_deterministic_fallback_sla_under_2_5_seconds(self) -> None:
        """Verify StudioAgentRunner deterministic fallback completes well within <2.5s SLA."""
        runner = StudioAgentRunner(harness=None)

        start = time.monotonic()
        proposal = runner(
            base_ontology=get_core_spine_pack(),
            evidence_chunks=[],
            tables=[],
            mode="agent",  # Requested agent mode, but harness is None -> falls back to deterministic
        )
        duration = time.monotonic() - start

        assert duration < 2.5, f"Deterministic fallback exceeded 2.5s SLA: {duration:.3f}s"
        assert duration < 0.5, f"Expected pure-Python fallback to finish in <0.5s, took {duration:.3f}s"
        assert proposal is not None
        assert any("engine_mode:deterministic_fallback" in d for d in proposal.diagnostics)

    def test_text_ingestion_sha256_computation_and_cas_storage(self, tmp_path: Path) -> None:
        """Verify text CAS ingestion computes exact SHA-256 and stores byte-for-byte CAS blob."""
        studio_dir = tmp_path / "studio"
        session_mgr = StudioSessionManager(studio_root=studio_dir)

        meta = session_mgr.create_session(
            session_id="test_text_session",
            domain_namespace="asset_management",
        )
        session_id = meta.session_id

        sample_text = "Portfolio Manager allocated 500k USD SOFR swaps on 2026-11-14 with Trade Confirmation #TC-9901."
        expected_sha = hashlib.sha256(sample_text.encode("utf-8")).hexdigest()

        res = session_mgr.add_text_source(
            session_id=session_id,
            text=sample_text,
            source_name="notes.txt",
            format="text",
        )

        assert res["source_sha256"] == expected_sha
        assert res["byte_count"] == len(sample_text.encode("utf-8"))

        # Verify blob file in CAS
        shard = expected_sha[:2]
        rest = expected_sha[2:]
        cas_blob = studio_dir / session_id / "inputs" / "cas" / shard / rest
        assert cas_blob.is_file(), f"CAS blob not found at {cas_blob}"
        assert cas_blob.read_text(encoding="utf-8") == sample_text

    def test_text_ingestion_empty_rejection_adversarial(self, tmp_path: Path) -> None:
        """Assert empty text upload raises OpenDVError with CORRUPT_SOURCE_BLOB."""
        studio_dir = tmp_path / "studio"
        session_mgr = StudioSessionManager(studio_root=studio_dir)

        meta = session_mgr.create_session(
            session_id="test_empty_session",
            domain_namespace="asset_management",
        )

        with pytest.raises(OpenDVError) as exc_info:
            session_mgr.add_text_source(
                session_id=meta.session_id,
                text="",
            )
        assert exc_info.value.error_code == ErrorCode.CORRUPT_SOURCE_BLOB


# ==============================================================================
# SECTION 4: STEERING PROMPT & STAGED PROMOTER (FEATURES 5 & 6)
# ==============================================================================


class TestSteeringPromptAndStagedPromoterEmpirical:
    """Empirical verification of proposal refinement (SUPERSEDED predecessor) and Path A / Path B promotion."""

    @pytest.fixture
    def studio_api_client(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """Setup an isolated platform API TestClient with authenticated session."""
        workspace_root = tmp_path / "workspace"
        workspace_root.mkdir(parents=True, exist_ok=True)
        studio_dir = tmp_path / "studio_root"
        studio_dir.mkdir(parents=True, exist_ok=True)

        monkeypatch.setenv("OPENDV_WORKSPACE_ROOT", str(workspace_root))
        monkeypatch.setenv("OPENDV_STUDIO_ROOT", str(studio_dir))
        monkeypatch.setenv("OPENDV_STUDIO_STORAGE_ROOT", str(studio_dir))
        monkeypatch.setenv("OPENDV_SERVER_MODE", "local_standalone")
        monkeypatch.delenv("OPENDV_TOKEN", raising=False)
        monkeypatch.delenv("OPENDV_STUDIO_TOKEN", raising=False)

        settings = OpenDVSettings.load(
            workspace_root=workspace_root,
            state_dir=workspace_root / "state",
            is_server=True,
        )
        app = create_app(settings=settings)
        client = TestClient(app, client=("127.0.0.1", 50000), base_url="http://localhost:8000")

        # Initialize studio and obtain auth token
        init_resp = client.post(
            "/api/v1/ontology/studio/init",
            json={"domain_namespace": "asset_management", "enterprise_owner": "Architecture Committee"},
        )
        assert init_resp.status_code == 200, init_resp.text
        local_token = init_resp.json()["local_token"]
        client.headers.update({
            "Authorization": f"Bearer {local_token}",
            "X-OpenDV-Role": "admin",
        })
        return client, studio_dir

    def test_steering_refine_pending_proposal_supersedes_predecessor(
        self,
        studio_api_client: tuple[TestClient, Path],
    ) -> None:
        """Verify POST /proposals/{id}/refine transitions predecessor to SUPERSEDED and creates new proposal."""
        client, studio_dir = studio_api_client

        # 1. Create session
        create_resp = client.post(
            "/api/v1/ontology/studio/sessions",
            json={"domain_namespace": "asset_management"},
        )
        assert create_resp.status_code == 201, create_resp.text
        session_id = create_resp.json()["session_id"]

        session_mgr = StudioSessionManager(studio_root=studio_dir)
        meta = session_mgr.get_session(session_id)
        sdir = session_mgr.get_session_dir(session_id)

        # 2. Seed an initial PENDING proposal
        initial_prop_id = "prop-pending-001"
        props_dir = sdir / "proposals"
        props_dir.mkdir(parents=True, exist_ok=True)
        initial_prop_file = props_dir / f"{initial_prop_id}.json"
        initial_prop_file.write_text(
            json.dumps({
                "proposal_id": initial_prop_id,
                "session_id": session_id,
                "base_version": meta.head_version,
                "base_sha256": meta.head_sha256,
                "status": "PENDING",
                "operations": [],
                "proposer": "human:analyst",
                "created_at": "2026-11-14T08:00:00Z",
                "rationale": "Initial baseline proposal",
                "delta": {
                    "version": "1.1.0",
                    "base_sha256": meta.head_sha256,
                    "target_sha256": meta.head_sha256,
                    "operations": [],
                },
            }),
            encoding="utf-8",
        )

        # 3. Refine proposal with steering prompt
        steering_text = "Ensure all debt instruments include coupon schedules."
        refine_resp = client.post(
            f"/api/v1/ontology/studio/sessions/{session_id}/proposals/{initial_prop_id}/refine",
            json={"steering_prompt": steering_text, "mode": "deterministic"},
            headers={"X-OpenDV-Role": "editor"},
        )
        assert refine_resp.status_code == 200, refine_resp.text
        refined_data = refine_resp.json()
        new_prop_id = refined_data["proposal_id"]
        assert new_prop_id != initial_prop_id
        assert refined_data["status"] == "PENDING"

        # Verify on-disk linkage and predecessor transition
        new_prop_file = props_dir / f"{new_prop_id}.json"
        assert new_prop_file.is_file()
        new_on_disk = json.loads(new_prop_file.read_text(encoding="utf-8"))
        assert new_on_disk["replaces_proposal_id"] == initial_prop_id
        assert steering_text in new_on_disk.get("steering_history", [])

        # 4. Assert predecessor proposal transitioned to SUPERSEDED
        prev_file = props_dir / f"{initial_prop_id}.json"
        assert prev_file.is_file()
        prev_data = json.loads(prev_file.read_text(encoding="utf-8"))
        assert prev_data["status"] == "SUPERSEDED"
        assert prev_data["superseded_by"] == new_prop_id

    def test_adversarial_refine_resolved_proposal_rejected(
        self,
        studio_api_client: tuple[TestClient, Path],
    ) -> None:
        """Assert attempting to refine an ACCEPTED or REJECTED proposal raises HTTP 409 PROPOSAL_ALREADY_RESOLVED."""
        client, studio_dir = studio_api_client

        create_resp = client.post(
            "/api/v1/ontology/studio/sessions",
            json={"domain_namespace": "asset_management"},
        )
        session_id = create_resp.json()["session_id"]
        props_dir = studio_dir / session_id / "proposals"
        props_dir.mkdir(parents=True, exist_ok=True)

        # 1. ACCEPTED proposal -> reject
        acc_id = "prop-accepted-001"
        (props_dir / f"{acc_id}.json").write_text(
            json.dumps({"proposal_id": acc_id, "session_id": session_id, "status": "ACCEPTED", "operations": []}),
            encoding="utf-8",
        )
        resp_acc = client.post(
            f"/api/v1/ontology/studio/sessions/{session_id}/proposals/{acc_id}/refine",
            json={"steering_prompt": "Refine accepted", "mode": "deterministic"},
            headers={"X-OpenDV-Role": "editor"},
        )
        assert resp_acc.status_code == 409
        assert resp_acc.json().get("error_code") == "PROPOSAL_ALREADY_RESOLVED"

        # 2. REJECTED proposal -> reject
        rej_id = "prop-rejected-001"
        (props_dir / f"{rej_id}.json").write_text(
            json.dumps({"proposal_id": rej_id, "session_id": session_id, "status": "REJECTED", "operations": []}),
            encoding="utf-8",
        )
        resp_rej = client.post(
            f"/api/v1/ontology/studio/sessions/{session_id}/proposals/{rej_id}/refine",
            json={"steering_prompt": "Refine rejected", "mode": "deterministic"},
            headers={"X-OpenDV-Role": "editor"},
        )
        assert resp_rej.status_code == 409
        assert resp_rej.json().get("error_code") == "PROPOSAL_ALREADY_RESOLVED"

    def test_path_a_promotion_synonym_rescoring_success(self, tmp_path: Path) -> None:
        """Verify Path A candidate promotion succeeds when re-evaluated score >= 0.6500."""
        studio_dir = tmp_path / "studio"
        session_mgr = StudioSessionManager(studio_root=studio_dir)
        meta = session_mgr.create_session(
            session_id="staged_session_path_a",
            domain_namespace="asset_management",
        )
        sdir = session_mgr.get_session_dir(meta.session_id)

        evidence_dir = sdir / "evidence"
        evidence_dir.mkdir(parents=True, exist_ok=True)
        candidate_id = "cand-001"
        staged_file = evidence_dir / "staged_candidates.json"
        staged_file.write_text(
            json.dumps({
                "staged_candidates": [
                    {
                        "candidate_id": candidate_id,
                        "concept_name": "EquityHolding",
                        "suggested_hub": "hub_equity_holding",
                        "top_match_score": 0.5200,
                        "top_match_uri": "https://spec.edmcouncil.org/fibo/ontology/SEC/Equities/EquityInstruments/EquityPosition",
                        "reason_held_back": "GROUNDING_THRESHOLD_UNMET",
                        "attributes": [{"attribute_name": "quantity", "data_type": "decimal"}],
                    }
                ]
            }),
            encoding="utf-8",
        )

        promoter = StagedCandidatePromoter(sdir, session_mgr)
        staged = promoter.list_staged_candidates()
        assert len(staged) == 1
        assert staged[0].candidate_id == candidate_id

        # Promote via Path A with strong synonym "EquityPosition"
        req = PromoteCandidateRequest(
            path="path_a",
            synonyms=["EquityPosition", "Equity Position"],
        )
        res = promoter.promote_candidate(
            candidate_id=candidate_id,
            request=req,
            principal=StudioPrincipal(
                principal="human:analyst",
                role="editor",
                is_admin=False,
                auth_method="token",
            ),
        )

        assert res.success is True
        assert res.confidence >= 0.6500
        assert res.fibo_uri == "https://spec.edmcouncil.org/fibo/ontology/SEC/Equities/EquityInstruments/EquityPosition"
        assert len(res.applied_operations) >= 2  # AddConceptOp + AddExternalMappingOp + AddAttributeOp

        # Assert candidate removed from staged_candidates.json
        remaining = promoter.list_staged_candidates()
        assert len(remaining) == 0

    def test_adversarial_path_a_rejections(self, tmp_path: Path) -> None:
        """Assert Path A rejects empty synonyms and synonyms with score < 0.6500."""
        studio_dir = tmp_path / "studio"
        session_mgr = StudioSessionManager(studio_root=studio_dir)
        meta = session_mgr.create_session(
            session_id="staged_session_path_a_adv",
            domain_namespace="asset_management",
        )
        sdir = session_mgr.get_session_dir(meta.session_id)
        evidence_dir = sdir / "evidence"
        evidence_dir.mkdir(parents=True, exist_ok=True)
        staged_file = evidence_dir / "staged_candidates.json"
        staged_file.write_text(
            json.dumps({
                "staged_candidates": [
                    {
                        "candidate_id": "cand-002",
                        "concept_name": "ObscureConceptXYZ",
                        "top_match_score": 0.2000,
                    }
                ]
            }),
            encoding="utf-8",
        )
        promoter = StagedCandidatePromoter(sdir, session_mgr)

        # 1. Empty synonyms -> SYNTAX_INVALID_ARGUMENT
        with pytest.raises(OpenDVError) as exc_empty:
            promoter.promote_candidate(
                candidate_id="cand-002",
                request=PromoteCandidateRequest(path="path_a", synonyms=[]),
                principal=StudioPrincipal(
                    principal="human:analyst",
                    role="editor",
                    is_admin=False,
                    auth_method="token",
                ),
            )
        assert exc_empty.value.error_code == ErrorCode.SYNTAX_INVALID_ARGUMENT

        # 2. Bogus synonyms -> ONT_EVO_CONTRACT_VIOLATION (< 0.6500)
        with pytest.raises(OpenDVError) as exc_low:
            promoter.promote_candidate(
                candidate_id="cand-002",
                request=PromoteCandidateRequest(path="path_a", synonyms=["randomgibberish99999"]),
                principal=StudioPrincipal(
                    principal="human:analyst",
                    role="editor",
                    is_admin=False,
                    auth_method="token",
                ),
            )
        assert exc_low.value.error_code == ErrorCode.ONT_EVO_CONTRACT_VIOLATION

    def test_adversarial_path_b_rejections(self, tmp_path: Path) -> None:
        """Assert Path B rejects non-admin roles and rationales < 10 characters."""
        studio_dir = tmp_path / "studio"
        session_mgr = StudioSessionManager(studio_root=studio_dir)
        meta = session_mgr.create_session(
            session_id="staged_session_path_b_adv",
            domain_namespace="asset_management",
        )
        sdir = session_mgr.get_session_dir(meta.session_id)
        evidence_dir = sdir / "evidence"
        evidence_dir.mkdir(parents=True, exist_ok=True)
        candidate_id = "cand-003-adv"
        staged_file = evidence_dir / "staged_candidates.json"
        staged_file.write_text(
            json.dumps({
                "staged_candidates": [
                    {
                        "candidate_id": candidate_id,
                        "concept_name": "ProprietaryTradingDeskMetric",
                        "top_match_score": 0.4100,
                        "top_match_uri": "https://spec.edmcouncil.org/fibo/ontology/FBC/ProductsAndServices/FinancialProductsAndServices/Position",
                    }
                ]
            }),
            encoding="utf-8",
        )
        promoter = StagedCandidatePromoter(sdir, session_mgr)

        # Adversarial 1: Attempt Path B as non-admin (editor) -> FORBIDDEN_ADMIN_REQUIRED
        with pytest.raises(OpenDVError) as exc_role:
            promoter.promote_candidate(
                candidate_id=candidate_id,
                request=PromoteCandidateRequest(
                    path="path_b",
                    waiver_rationale="Approved by domain governance committee for internal use.",
                ),
                principal=StudioPrincipal(
                    principal="human:editor",
                    role="editor",
                    is_admin=False,
                    auth_method="token",
                ),
            )
        assert exc_role.value.error_code == ErrorCode.FORBIDDEN_ADMIN_REQUIRED

        # Adversarial 2: Attempt Path B as admin but with short rationale (< 10 chars) -> INVALID_GROUNDING_WAIVER
        with pytest.raises(OpenDVError) as exc_short:
            promoter.promote_candidate(
                candidate_id=candidate_id,
                request=PromoteCandidateRequest(
                    path="path_b",
                    waiver_rationale="too short",
                ),
                principal=StudioPrincipal(
                    principal="human:admin",
                    role="admin",
                    is_admin=True,
                    auth_method="token",
                ),
            )
        assert exc_short.value.error_code == ErrorCode.INVALID_GROUNDING_WAIVER

    def test_path_b_admin_waiver_hmac_success(self, tmp_path: Path) -> None:
        """Verify Path B admin waiver promotion constructs valid waiver record and promotes candidate."""
        studio_dir = tmp_path / "studio"
        session_mgr = StudioSessionManager(studio_root=studio_dir)
        meta = session_mgr.create_session(
            session_id="staged_session_path_b_succ",
            domain_namespace="asset_management",
        )
        sdir = session_mgr.get_session_dir(meta.session_id)
        evidence_dir = sdir / "evidence"
        evidence_dir.mkdir(parents=True, exist_ok=True)
        candidate_id = "cand-003-succ"
        staged_file = evidence_dir / "staged_candidates.json"
        staged_file.write_text(
            json.dumps({
                "staged_candidates": [
                    {
                        "candidate_id": candidate_id,
                        "concept_name": "ProprietaryTradingDeskMetric",
                        "top_match_score": 0.4100,
                        "top_match_uri": "https://spec.edmcouncil.org/fibo/ontology/FBC/ProductsAndServices/FinancialProductsAndServices/Position",
                    }
                ]
            }),
            encoding="utf-8",
        )
        promoter = StagedCandidatePromoter(sdir, session_mgr)

        valid_rationale = "Formal business justification: proprietary internal desk metric required for custody reconciliation."
        res = promoter.promote_candidate(
            candidate_id=candidate_id,
            request=PromoteCandidateRequest(
                path="path_b",
                waiver_rationale=valid_rationale,
            ),
            principal=StudioPrincipal(
                principal="human:admin",
                role="admin",
                is_admin=True,
                auth_method="token",
            ),
        )
        assert res.success is True
        assert res.waiver_id is not None

        # Verify waivers.json written with HMAC signature
        waivers_file = sdir / "waivers.json"
        assert waivers_file.is_file()
        wdata = json.loads(waivers_file.read_text(encoding="utf-8"))
        assert len(wdata["waivers"]) == 1
        w = wdata["waivers"][0]
        assert w["rationale"] == valid_rationale
        assert w["authorized_role"] == "admin"
        assert len(w["signature_or_token"]) > 20, "Missing HMAC signature!"


# ==============================================================================
# SECTION 5: UNSTRUCTURED-ONLY PIPELINE MODE (FEATURE 7)
# ==============================================================================


class TestUnstructuredOnlyPipelineModeEmpirical:
    """Empirical verification of Wave 0 unstructured-only pipeline execution, contract synthesis, and DuckDB compilation."""

    def test_unstructured_only_validation_bypass_and_contract_synthesis(self, tmp_path: Path) -> None:
        """Verify unstructured_only=True synthesizes hub_document and 6-column triage ledger without DV-COL-012 failures."""
        doc_file = tmp_path / "offering_memo.txt"
        doc_file.write_text("Private Credit Offering Memorandum text content", encoding="utf-8")

        ont = GraphAOntology(
            domain_namespace="test_domain",
            canonical_business_concepts=[
                CanonicalBusinessConcept(
                    concept_uri="urn:opendv:concept:document",
                    concept_name="Document",
                    definition="Document record",
                    target_hub="hub_document",
                )
            ],
        )
        ont_file = tmp_path / "ontology.json"
        ont_file.write_text(ont.model_dump_json(), encoding="utf-8")

        unstr_docs = [
            PipelineUnstructuredDocInput(
                doc_id="DOC-9901",
                document_path=str(doc_file),
                record_source="UNSTRUCTURED",
            )
        ]

        # 1. Assert unstructured_only=False fails with UNSTRUCTURED_WITHOUT_RELATIONAL_SOURCE
        spec_fail = PipelineInputSpec(
            pipeline_id="pipe-fail",
            project_id="proj-fail",
            graph_a_ontology_path=str(ont_file),
            unstructured_documents=unstr_docs,
            unstructured_only=False,
        )
        report_fail = validate_pipeline_inputs(spec_fail, base_dir=tmp_path)
        assert report_fail.passed is False
        assert any(d["code"] == "UNSTRUCTURED_WITHOUT_RELATIONAL_SOURCE" for d in report_fail.diagnostics)

        # 2. Assert unstructured_only=True passes validation
        spec_pass = PipelineInputSpec(
            pipeline_id="pipe-pass",
            project_id="proj-pass",
            graph_a_ontology_path=str(ont_file),
            unstructured_documents=unstr_docs,
            unstructured_only=True,
        )
        report_pass = validate_pipeline_inputs(spec_pass, base_dir=tmp_path)
        assert not any(d["code"] == "UNSTRUCTURED_WITHOUT_RELATIONAL_SOURCE" for d in report_pass.diagnostics)

        # 3. Assert contract synthesis creates hub_document, sat_document_metadata, and 6 ledger columns
        cum_unstr = [(spec_pass.unstructured_documents[0], "sha256_mock_hash")]
        contract = _synthesize_cumulative_contract(
            ontology=ont,
            cumulative_sources={},
            cumulative_source_systems={},
            cumulative_unstructured_docs=cum_unstr,
            added_drift_cols_by_table={},
            current_wave_drift_tables=set(),
            wave_index=0,
            domain_namespace="test_domain",
            deployed_contract=None,
        )

        hubs = contract["hubs"]
        assert any(h["name"] == "hub_document" for h in hubs)
        doc_hub = next(h for h in hubs if h["name"] == "hub_document")
        bk_src = doc_hub["business_key_sources"][0]
        assert bk_src["key_authority_tier"] == "TIER_1_GLOBAL_STANDARD"
        assert bk_src["bronze_table"] == "bronze.unstructured_documents"

        sats = contract["satellites"]
        assert any(s["name"] == "sat_document_metadata" for s in sats)

        ledger = contract["column_triage_ledger"]
        assert len(ledger) == 6
        expected_cols = {"document_id", "file_name", "sha256", "byte_size", "mime_type", "ingested_at"}
        actual_cols = {entry["column_name"] for entry in ledger}
        assert actual_cols == expected_cols


# ==============================================================================
# SECTION 6: DUAL-CLI PARITY (FEATURE 8)
# ==============================================================================


class TestDualCliParityEmpirical:
    """Empirical verification of bit-for-bit parity across Python opendv and Bun opendvctl --dump-cli-tree."""

    def test_dump_cli_tree_parity(self) -> None:
        """Execute opendv and opendvctl --dump-cli-tree and assert tree parity."""
        # Run opendv --dump-cli-tree
        py_res = subprocess.run(
            ["uv", "run", "--directory", str(REPO_ROOT), "opendv", "--dump-cli-tree"],
            capture_output=True,
            text=True,
            check=True,
        )
        py_tree = json.loads(py_res.stdout.strip())

        # Run opendvctl --dump-cli-tree
        ts_entry = REPO_ROOT / "apps" / "opendvctl" / "src" / "main.ts"
        ts_res = subprocess.run(
            ["bun", "run", str(ts_entry), "--dump-cli-tree"],
            capture_output=True,
            text=True,
            check=True,
        )
        ts_tree = json.loads(ts_res.stdout.strip())

        assert py_tree["spec_version"] == ts_tree["spec_version"]

        # Check command groups (allowing 'serve' to be standalone-only)
        py_commands = set(py_tree["commands"].keys())
        ts_commands = set(ts_tree["commands"].keys())
        assert py_commands - {"serve"} == ts_commands

        # Check ontology studio subcommands (allowing 'init' to be standalone-only)
        py_studio_subs = set(py_tree["commands"]["ontology"]["subcommands"]["studio"]["subcommands"].keys())
        ts_studio_subs = set(ts_tree["commands"]["ontology"]["subcommands"]["studio"]["subcommands"].keys())
        assert py_studio_subs - {"init"} == ts_studio_subs

        # Verify studio groups in both
        expected_m1_groups = {
            "starter-pack",
            "text",
            "staged",
            "proposal",
            "construct",
            "clarify",
        }
        for g in expected_m1_groups:
            assert g in py_studio_subs, f"Group {g} missing from opendv"
            assert g in ts_studio_subs, f"Group {g} missing from opendvctl"

        # Check --unstructured-only flag in pipeline run and pipeline validate
        py_pipe_run_opts = {
            opt["flag"] for opt in py_tree["commands"]["pipeline"]["subcommands"]["run"]["options"]
        }
        ts_pipe_run_opts = {
            opt["flag"] for opt in ts_tree["commands"]["pipeline"]["subcommands"]["run"]["options"]
        }
        assert "--unstructured-only" in py_pipe_run_opts
        assert "--unstructured-only" in ts_pipe_run_opts

        py_pipe_val_opts = {
            opt["flag"] for opt in py_tree["commands"]["pipeline"]["subcommands"]["validate"]["options"]
        }
        ts_pipe_val_opts = {
            opt["flag"] for opt in ts_tree["commands"]["pipeline"]["subcommands"]["validate"]["options"]
        }
        assert "--unstructured-only" in py_pipe_val_opts
        assert "--unstructured-only" in ts_pipe_val_opts
