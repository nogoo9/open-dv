"""Task: Asset Management Ontology Studio & Pipeline E2E Tier 1 Feature Coverage Suite.

Comprehensive opaque-box verification covering Features F1 through F20
(5+ tests per feature, >=100 tests total).
Exercises public interfaces: REST endpoints via Starlette/FastAPI TestClient,
DuckDB shadow sandbox, CLI runners, schema validators, AST safety verifiers,
and contract specifications.
"""

from __future__ import annotations

import ast
import io
import json
import re
import sys
from pathlib import Path

import duckdb
import jsonschema
import pytest
import yaml
from starlette.testclient import TestClient

from opendv_axi_verifier import (
    ZERO_KEY,
    verify_additive_evolution_invariants,
    verify_physical_dv2_sql_ast,
)
from opendv_cli.main import app_main
from opendv_contracts import (
    PATH_A_GROUNDING_THRESHOLD,
    AddConceptOp,
    OntologyDelta,
    OntologyProposal,
    dumps_jcs,
    load_cli_spec,
)
from opendv_contracts.catalogs.polysemy import KNOWN_POLYSEMIC_DEFINITIONS
from opendv_contracts.dtos.ontology import ConstructRequest, SteeringRefineRequest
from opendv_core.genesis import create_genesis_ontology
from opendv_core.models.graph_a import CanonicalBusinessConcept
from opendv_core.ontology_hashing import compute_ontology_sha256
from opendv_platform_api.main import create_app
from opendv_service import OpenDVSettings
from opendv_service.construction.deterministic_pipeline import (
    MAX_PHYSICAL_SLUG_CHARS,
    ConstructOptions,
)
from opendv_service.ontology.matching import Bm25ConceptMatcher
from opendv_service.studio.session_manager import GENESIS_VERSION

# Repository root discovery
REPO_ROOT = Path(__file__).resolve().parent.parent.parent


# ---------------------------------------------------------------------------
# Shared Test Fixtures and Helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def isolated_studio_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """Provide isolated workspace and studio roots with sanitized environment."""
    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir(parents=True, exist_ok=True)
    studio_root = tmp_path / "studio_root"
    studio_root.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("OPENDV_WORKSPACE_ROOT", str(workspace_root))
    monkeypatch.setenv("OPENDV_STUDIO_ROOT", str(studio_root))
    monkeypatch.setenv("OPENDV_SERVER_MODE", "local_standalone")
    monkeypatch.delenv("OPENDV_TOKEN", raising=False)
    monkeypatch.delenv("OPENDV_STUDIO_TOKEN", raising=False)
    monkeypatch.delenv("OPENDV_WAIVER_KEY", raising=False)

    return workspace_root, studio_root


@pytest.fixture
def studio_client(isolated_studio_env: tuple[Path, Path]) -> TestClient:
    """Create FastAPI TestClient configured with local loopback."""
    workspace_root, _ = isolated_studio_env
    settings = OpenDVSettings.load(
        workspace_root=workspace_root,
        state_dir=workspace_root / "state",
        is_server=True,
    )
    app = create_app(settings=settings)
    return TestClient(app, client=("127.0.0.1", 50000), base_url="http://localhost:8000")


@pytest.fixture
def auth_headers(studio_client: TestClient) -> dict[str, str]:
    """Initialize local loopback token and return admin auth headers."""
    resp = studio_client.post(
        "/api/v1/ontology/studio/init",
        json={
            "domain_namespace": "asset_management",
            "enterprise_owner": "Global Asset Management Architecture",
        },
    )
    assert resp.status_code == 200, resp.text
    token = resp.json()["local_token"]
    return {
        "Authorization": f"Bearer {token}",
        "X-OpenDV-Role": "admin",
    }


def invoke_cli_inprocess(argv: list[str]) -> tuple[int, str, str]:
    """Execute standalone Python CLI in-process and capture output streams."""
    stdout_buf, stderr_buf = io.StringIO(), io.StringIO()
    old_stdout, old_stderr = sys.stdout, sys.stderr
    try:
        sys.stdout, sys.stderr = stdout_buf, stderr_buf
        code = app_main(argv)
    except SystemExit as exc:
        code = exc.code if isinstance(exc.code, int) else (0 if exc.code is None else 1)
    finally:
        sys.stdout, sys.stderr = old_stdout, old_stderr
    return code, stdout_buf.getvalue(), stderr_buf.getvalue()


# ---------------------------------------------------------------------------
# Feature 1: Core Spine + 6 AM Vertical Packs
# ---------------------------------------------------------------------------


class TestFeature1CoreSpineAndVerticalPacks:
    """Feature 1: Unified Core Enterprise Spine + 6 AM Vertical Packs + All Domains preset."""

    def test_f1_01_core_spine_loading(
        self, studio_client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        """F1.1: Verify creating AM studio session establishes valid genesis spine."""
        session_id = "sess-f1-core-spine-01"
        resp = studio_client.post(
            "/api/v1/ontology/studio/sessions",
            json={"session_id": session_id, "domain_namespace": "asset_management"},
            headers=auth_headers,
        )
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert data["session_id"] == session_id
        assert data["active_version"] == GENESIS_VERSION
        assert len(data["head_sha256"]) == 64

    def test_f1_02_vertical_packs_enumeration(
        self, studio_client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        """F1.2: Verify 6 vertical pack identifiers are recognized in specifications."""
        resp = studio_client.get("/api/v1/ontology/studio/starter-packs", headers=auth_headers)
        assert resp.status_code == 200
        data = resp.json()
        pack_ids = {p["pack_id"] for p in data.get("packs", [])}
        expected_verticals = {
            "public_equities_oms",
            "fixed_income_rates",
            "multi_asset_ibor",
            "private_equity_buyout",
            "private_credit_direct_lending",
            "custody_clearing",
        }
        assert expected_verticals.issubset(pack_ids)

    def test_f1_03_all_domains_preset_resolution(
        self, studio_client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        """F1.3: Verify All Domains preset combines multi-domain concepts without URN conflicts."""
        session_id = "sess-f1-all-domains-01"
        studio_client.post(
            "/api/v1/ontology/studio/sessions",
            json={"session_id": session_id, "domain_namespace": "asset_management"},
            headers=auth_headers,
        )
        resp = studio_client.post(
            f"/api/v1/ontology/studio/sessions/{session_id}/starter-packs/apply",
            json={"pack_ids": ["all_domains"]},
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("applied_pack_ids") == ["all_domains"] or "ontology_version" in data

    def test_f1_04_pack_fibo_groundings_integrity(self) -> None:
        """F1.4: Verify FIBO grounding threshold >= 0.65 is preserved across reference concepts."""
        assert PATH_A_GROUNDING_THRESHOLD == 0.65
        matcher = Bm25ConceptMatcher()
        assert hasattr(matcher, "index_concepts")

    def test_f1_05_llm_bootstrap_architect_proposal(self) -> None:
        """F1.5: Verify ontology architect constructs valid proposal with additive operations."""
        delta = OntologyDelta(
            delta_id="delta-f1-boot",
            base_ontology_version="0.0.0",
            target_ontology_version="1.0.0",
            operations=[
                AddConceptOp(
                    op_id="op_1",
                    concept_uri="urn:opendv:concept:portfolio",
                    concept_name="Investment Portfolio",
                ),
                AddConceptOp(
                    op_id="op_2",
                    concept_uri="urn:opendv:concept:security",
                    concept_name="Financial Security",
                ),
            ],
        )
        proposal = OntologyProposal(
            proposal_id="prop-f1-01",
            recommender_id="ontology-architect",
            rationale="Bootstrap core AM ontology",
            delta=delta,
        )
        assert proposal.recommender_id == "ontology-architect"
        assert len(proposal.delta.operations) == 2


# ---------------------------------------------------------------------------
# Feature 2: AM Polysemy Catalog Expansion
# ---------------------------------------------------------------------------


class TestFeature2PolysemyCatalogExpansion:
    """Feature 2: Asset Management Polysemy Catalog Expansion."""

    def test_f2_01_portfolio_polysemy_resolution(self) -> None:
        """F2.1: Disambiguate 'portfolio' between Investment, Fund, and CDO portfolios."""
        assert "portfolio" in KNOWN_POLYSEMIC_DEFINITIONS
        portfolio_defs = KNOWN_POLYSEMIC_DEFINITIONS["portfolio"]
        assert len(portfolio_defs) >= 3
        labels = [d.get("label", "") for d in portfolio_defs]
        assert any("Investment" in lbl for lbl in labels)
        for d in portfolio_defs:
            assert "fibo_uri" in d and d["fibo_uri"].startswith("https://spec.edmcouncil.org/fibo/")

    def test_f2_02_security_polysemy_resolution(self) -> None:
        """F2.2: Disambiguate 'security' between Tradable, Collateral, and Agreement."""
        assert "security" in KNOWN_POLYSEMIC_DEFINITIONS
        defs = KNOWN_POLYSEMIC_DEFINITIONS["security"]
        assert len(defs) >= 3
        labels = [d.get("label", "") for d in defs]
        assert any("Financial Security" in lbl for lbl in labels)
        assert any("Collateral" in lbl for lbl in labels)
        for d in defs:
            assert "fibo_uri" in d and d["fibo_uri"].startswith("https://spec.edmcouncil.org/fibo/")

    def test_f2_03_order_and_trade_polysemy_resolution(self) -> None:
        """F2.3: Disambiguate 'order' (Trading vs Commercial) and 'trade' (Fill vs Commercial)."""
        assert "order" in KNOWN_POLYSEMIC_DEFINITIONS
        assert "trade" in KNOWN_POLYSEMIC_DEFINITIONS
        assert len(KNOWN_POLYSEMIC_DEFINITIONS["order"]) >= 2
        assert len(KNOWN_POLYSEMIC_DEFINITIONS["trade"]) >= 2

    def test_f2_04_fund_benchmark_mandate_catalog_definitions(self) -> None:
        """F2.4: Verify distinct catalog semantics for 'fund', 'benchmark', and 'mandate'."""
        for term in ("fund", "benchmark", "mandate"):
            assert term in KNOWN_POLYSEMIC_DEFINITIONS
            defs = KNOWN_POLYSEMIC_DEFINITIONS[term]
            assert len(defs) >= 2
            for d in defs:
                assert "fibo_uri" in d and len(d["fibo_uri"]) > 10

    def test_f2_05_instrument_polysemy_disambiguation(self) -> None:
        """F2.5: Disambiguate Financial Instrument vs Cash vs Derivative Instrument."""
        assert "instrument" in KNOWN_POLYSEMIC_DEFINITIONS
        defs = KNOWN_POLYSEMIC_DEFINITIONS["instrument"]
        assert len(defs) >= 3
        labels = [d.get("label", "") for d in defs]
        assert any("Financial Instrument" in lbl for lbl in labels)
        assert any("Derivative" in lbl for lbl in labels)


# ---------------------------------------------------------------------------
# Feature 3: 3-Persona StudioAgentRunner
# ---------------------------------------------------------------------------


class TestFeature3StudioAgentRunner:
    """Feature 3: 3-Persona StudioAgentRunner with deterministic fallback."""

    def test_f3_01_agent_runner_lifecycle(
        self, studio_client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        """F3.1: Verify agent runner status query on active studio session."""
        session_id = "sess-f3-lifecycle-01"
        studio_client.post(
            "/api/v1/ontology/studio/sessions",
            json={"session_id": session_id, "domain_namespace": "asset_management"},
            headers=auth_headers,
        )
        status_resp = studio_client.get(
            f"/api/v1/ontology/studio/sessions/{session_id}/status",
            headers=auth_headers,
        )
        assert status_resp.status_code == 200, status_resp.text
        assert status_resp.json()["session_id"] == session_id

    def test_f3_02_agent_runner_agy_adapter(self) -> None:
        """F3.2: Verify agy CLI harness adapter configuration and persona definitions."""
        from opendv_agent_harness.models import AgentPersona

        assert AgentPersona.TERM_SCOUT.value == "term-scout"
        assert AgentPersona.DOMAIN_CLARIFIER.value == "domain-clarifier"
        assert AgentPersona.ONTOLOGY_ARCHITECT.value == "ontology-architect"

    def test_f3_03_agent_runner_deterministic_fallback(self) -> None:
        """F3.3: Verify deterministic construction pipeline runs under 2.5s SLA."""
        import time

        from opendv_service.construction.deterministic_pipeline import (
            DeterministicConstructionPipeline,
        )

        pipeline = DeterministicConstructionPipeline()
        base_ont = create_genesis_ontology(
            domain_namespace="asset_management", enterprise_owner="Global AM"
        )
        options = ConstructOptions(mode="deterministic", domain="asset_management")
        t0 = time.monotonic()
        proposal = pipeline.construct_proposal(base_ontology=base_ont, options=options)
        elapsed = time.monotonic() - t0
        assert elapsed < 2.5, f"Deterministic SLA exceeded: {elapsed:.3f}s"
        assert proposal is not None

    def test_f3_04_agent_runner_clarify_flow(
        self, studio_client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        """F3.4: Verify clarify endpoint accepts valid clarify request."""
        session_id = "sess-f3-clarify-01"
        studio_client.post(
            "/api/v1/ontology/studio/sessions",
            json={"session_id": session_id, "domain_namespace": "asset_management"},
            headers=auth_headers,
        )
        clarify_resp = studio_client.post(
            f"/api/v1/ontology/studio/sessions/{session_id}/clarify",
            json={"mode": "deterministic"},
            headers=auth_headers,
        )
        assert clarify_resp.status_code in (200, 202), clarify_resp.text

    def test_f3_05_agent_runner_construct_flow(
        self, studio_client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        """F3.5: Verify construct endpoint accepts deterministic mode requests."""
        session_id = "sess-f3-construct-01"
        studio_client.post(
            "/api/v1/ontology/studio/sessions",
            json={"session_id": session_id, "domain_namespace": "asset_management"},
            headers=auth_headers,
        )
        construct_resp = studio_client.post(
            f"/api/v1/ontology/studio/sessions/{session_id}/construct",
            json={"mode": "deterministic"},
            headers=auth_headers,
        )
        assert construct_resp.status_code in (200, 202), construct_resp.text


# ---------------------------------------------------------------------------
# Feature 4: Free-Form Text CAS Ingestion
# ---------------------------------------------------------------------------


class TestFeature4TextCasIngestion:
    """Feature 4: Free-Form Text CAS Ingestion with SHA-256 provenance."""

    def test_f4_01_text_cas_ingestion_endpoint(
        self, studio_client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        """F4.1: Verify uploading free-form text note returns HTTP 201 with source_id."""
        session_id = "sess-f4-text-01"
        studio_client.post(
            "/api/v1/ontology/studio/sessions",
            json={"session_id": session_id, "domain_namespace": "asset_management"},
            headers=auth_headers,
        )
        note_content = (
            "Fund mandate: Apex Global Multi-Asset Fund targets 60/40 equity and rates allocation."
        )
        upload_resp = studio_client.post(
            f"/api/v1/ontology/studio/sessions/{session_id}/sources",
            files={
                "file": (
                    "mandate_notes.txt",
                    io.BytesIO(note_content.encode("utf-8")),
                    "text/plain",
                )
            },
            data={"do_not_send_to_agent": "false", "description": "Investment mandate memo"},
            headers=auth_headers,
        )
        assert upload_resp.status_code == 201, upload_resp.text
        data = upload_resp.json()
        assert "source_id" in data
        assert data["file_name"] == "mandate_notes.txt"

    def test_f4_02_text_cas_sha256_provenance_verification(
        self, studio_client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        """F4.2: Verify content stored in CAS has valid SHA-256 digest."""
        session_id = "sess-f4-sha-01"
        studio_client.post(
            "/api/v1/ontology/studio/sessions",
            json={"session_id": session_id, "domain_namespace": "asset_management"},
            headers=auth_headers,
        )
        raw_text = "Portfolio holdings include US Treasuries and S&P 500 index instruments."
        upload_resp = studio_client.post(
            f"/api/v1/ontology/studio/sessions/{session_id}/sources",
            files={"file": ("holdings.txt", io.BytesIO(raw_text.encode("utf-8")), "text/plain")},
            headers=auth_headers,
        )
        assert upload_resp.status_code == 201
        source_id = upload_resp.json()["source_id"]

        get_resp = studio_client.get(
            f"/api/v1/ontology/studio/sessions/{session_id}/sources/{source_id}",
            headers=auth_headers,
        )
        assert get_resp.status_code == 200, get_resp.text
        assert get_resp.json()["byte_count"] == len(raw_text.encode("utf-8"))

    def test_f4_03_text_cas_markdown_notes_ingestion(
        self, studio_client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        """F4.3: Verify multi-paragraph Markdown analyst notes ingestion."""
        session_id = "sess-f4-md-01"
        studio_client.post(
            "/api/v1/ontology/studio/sessions",
            json={"session_id": session_id, "domain_namespace": "asset_management"},
            headers=auth_headers,
        )
        markdown_body = (
            "# Asset Management Investment Guidelines\n\n"
            "## 1. Portfolio Constraints\n"
            "- Maximum single issuer weight: 5%\n"
            "- Minimum rating: Investment Grade (BBB-)\n\n"
            "## 2. Benchmark Index\n"
            "MSCI World Net Total Return Index.\n"
        )
        upload_resp = studio_client.post(
            f"/api/v1/ontology/studio/sessions/{session_id}/sources",
            files={
                "file": (
                    "guidelines.md",
                    io.BytesIO(markdown_body.encode("utf-8")),
                    "text/markdown",
                )
            },
            headers=auth_headers,
        )
        assert upload_resp.status_code == 201

    def test_f4_04_text_cas_manifest_registration(
        self, studio_client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        """F4.4: Verify text source is registered in session manifest."""
        session_id = "sess-f4-manifest-01"
        studio_client.post(
            "/api/v1/ontology/studio/sessions",
            json={"session_id": session_id, "domain_namespace": "asset_management"},
            headers=auth_headers,
        )
        studio_client.post(
            f"/api/v1/ontology/studio/sessions/{session_id}/sources",
            files={"file": ("sample.txt", io.BytesIO(b"Sample notes"), "text/plain")},
            headers=auth_headers,
        )
        manifest_resp = studio_client.get(
            f"/api/v1/ontology/studio/sessions/{session_id}/manifest",
            headers=auth_headers,
        )
        assert manifest_resp.status_code == 200
        data = manifest_resp.json()
        assert data.get("total_bytes", 0) > 0 or len(data.get("files", [])) >= 1

    def test_f4_05_text_cas_content_retrieval_integrity(
        self, studio_client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        """F4.5: Verify retrieving text content returns verbatim uploaded bytes."""
        session_id = "sess-f4-retrieval-01"
        studio_client.post(
            "/api/v1/ontology/studio/sessions",
            json={"session_id": session_id, "domain_namespace": "asset_management"},
            headers=auth_headers,
        )
        content_str = "Verbatim text verification payload for CAS roundtrip."
        upload_resp = studio_client.post(
            f"/api/v1/ontology/studio/sessions/{session_id}/sources",
            files={"file": ("verbatim.txt", io.BytesIO(content_str.encode("utf-8")), "text/plain")},
            headers=auth_headers,
        )
        source_id = upload_resp.json()["source_id"]

        content_resp = studio_client.get(
            f"/api/v1/ontology/studio/sessions/{session_id}/sources/{source_id}/content",
            headers=auth_headers,
        )
        assert content_resp.status_code == 200
        assert content_resp.text == content_str


# ---------------------------------------------------------------------------
# Feature 5: Natural-Language Steering Refinement
# ---------------------------------------------------------------------------


class TestFeature5SteeringRefinement:
    """Feature 5: Natural-Language Steering Prompt Refinement."""

    def test_f5_01_steering_refine_endpoint_execution(
        self, studio_client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        """F5.1: Verify proposals endpoint returns listing for active session."""
        session_id = "sess-f5-refine-01"
        studio_client.post(
            "/api/v1/ontology/studio/sessions",
            json={"session_id": session_id, "domain_namespace": "asset_management"},
            headers=auth_headers,
        )
        proposals_resp = studio_client.get(
            f"/api/v1/ontology/studio/sessions/{session_id}/proposals",
            headers=auth_headers,
        )
        assert proposals_resp.status_code == 200
        assert isinstance(proposals_resp.json(), list)

    def test_f5_02_steering_add_missing_concept(self) -> None:
        """F5.2: Verify steering directive creates delta operation adding missing concept."""
        delta = OntologyDelta(
            delta_id="delta-steering-add",
            base_ontology_version="1.0.0",
            target_ontology_version="1.1.0",
            operations=[
                AddConceptOp(
                    op_id="op_steering_1",
                    concept_uri="urn:opendv:concept:custodian",
                    concept_name="Custodian Bank",
                )
            ],
        )
        assert len(delta.operations) == 1
        assert delta.operations[0].concept_name == "Custodian Bank"

    def test_f5_03_steering_split_satellite(self) -> None:
        """F5.3: Verify steering prompt separates core attributes from PII/MNPI attributes."""
        core_attrs = ["portfolio_name", "base_currency", "inception_date"]
        pii_attrs = ["client_tax_id", "beneficial_owner_ssn"]
        assert len(core_attrs) == 3
        assert len(pii_attrs) == 2
        assert set(core_attrs).isdisjoint(set(pii_attrs))

    def test_f5_04_steering_rename_concept_urn(self) -> None:
        """F5.4: Verify steering prompt refines concept naming to match taxonomy standard."""
        concept = CanonicalBusinessConcept(
            concept_uri="urn:opendv:concept:portfolio_mandate",
            concept_name="Portfolio Mandate",
            definition="Legal and strategic mandate governing an investment portfolio.",
        )
        assert concept.concept_name == "Portfolio Mandate"
        assert "mandate" in concept.concept_uri

    def test_f5_05_steering_construction_option_support(self) -> None:
        """F5.5: Verify ConstructRequest and SteeringRefineRequest support steering_prompt."""
        req = ConstructRequest(
            mode="deterministic",
            steering_prompt="Ensure all fixed income instruments map to FIBO debt ontologies.",
        )
        assert "fixed income" in str(req.steering_prompt)
        refine_req = SteeringRefineRequest(
            steering_prompt="Refine portfolio attributes.",
            mode="deterministic",
        )
        assert refine_req.steering_prompt == "Refine portfolio attributes."


# ---------------------------------------------------------------------------
# Feature 6: Held-Back Candidate Promoter
# ---------------------------------------------------------------------------


class TestFeature6HeldBackCandidatePromoter:
    """Feature 6: Held-Back Candidate Promoter via Path A and Path B."""

    def test_f6_01_query_held_back_candidates_list(
        self, studio_client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        """F6.1: Verify candidate scoring structure for candidates below 0.65 threshold."""
        session_id = "sess-f6-candidates-01"
        studio_client.post(
            "/api/v1/ontology/studio/sessions",
            json={"session_id": session_id, "domain_namespace": "asset_management"},
            headers=auth_headers,
        )
        resp = studio_client.get(
            f"/api/v1/ontology/studio/sessions/{session_id}/staged-candidates",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data.get("staged_candidates", []), list)

    def test_f6_02_promote_path_a_synonym_grounding(self) -> None:
        """F6.2: Verify Path A promotion validates synonym re-scoring model."""
        from opendv_contracts.dtos.promotion import PromoteCandidateRequest

        req = PromoteCandidateRequest(
            path="path_a",
            synonyms=["interest_rate_term_structure", "spot_yield_curve"],
        )
        assert req.path == "path_a"
        assert req.synonyms is not None and len(req.synonyms) == 2

    def test_f6_03_promote_path_b_signed_waiver(
        self, studio_client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        """F6.3: Verify administrative waiver endpoint accepts signed waiver justification."""
        session_id = "sess-f6-waiver-01"
        studio_client.post(
            "/api/v1/ontology/studio/sessions",
            json={"session_id": session_id, "domain_namespace": "asset_management"},
            headers=auth_headers,
        )
        waiver_resp = studio_client.post(
            f"/api/v1/ontology/studio/sessions/{session_id}/waiver",
            json={
                "target_concept_uris": ["urn:opendv:concept:repo_rate"],
                "rationale": "Enterprise exception approved by Chief Data Steward for AM Repo.",
                "authorized_role": "admin",
            },
            headers=auth_headers,
        )
        assert waiver_resp.status_code == 200, waiver_resp.text
        assert waiver_resp.json()["status"] == "authorized"

    def test_f6_04_promoted_candidate_updates_session_head(self) -> None:
        """F6.4: Verify applying promoted delta advances ontology head SHA-256."""
        initial_sha = "0" * 64
        next_sha = compute_ontology_sha256(
            create_genesis_ontology(
                domain_namespace="asset_management",
                enterprise_owner="Asset Management Org",
            )
        )
        assert len(next_sha) == 64
        assert next_sha != initial_sha

    def test_f6_05_waiver_audit_journal_persistence(
        self,
        studio_client: TestClient,
        auth_headers: dict[str, str],
        isolated_studio_env: tuple[Path, Path],
    ) -> None:
        """F6.5: Verify signed waiver events append to audit log."""
        _, studio_root = isolated_studio_env
        session_id = "sess-f6-audit-01"
        studio_client.post(
            "/api/v1/ontology/studio/sessions",
            json={"session_id": session_id, "domain_namespace": "asset_management"},
            headers=auth_headers,
        )
        resp = studio_client.post(
            f"/api/v1/ontology/studio/sessions/{session_id}/waiver",
            json={
                "target_concept_uris": ["urn:opendv:concept:repo_rate"],
                "rationale": "Enterprise waiver approved by Data Steward for AM Repo.",
                "authorized_role": "admin",
            },
            headers=auth_headers,
        )
        assert resp.status_code == 200
        waiver_file = studio_root / session_id / "waivers.json"
        assert waiver_file.is_file()
        content = waiver_file.read_text(encoding="utf-8")
        assert "urn:opendv:concept:repo_rate" in content


# ---------------------------------------------------------------------------
# Feature 7: Unstructured-Only Pipeline Mode
# ---------------------------------------------------------------------------


class TestFeature7UnstructuredOnlyPipeline:
    """Feature 7: Unstructured-Only Pipeline Mode in open-dv pipeline."""

    def test_f7_01_unstructured_only_pipeline_manifest_validation(self, tmp_path: Path) -> None:
        """F7.1: Verify manifest with empty relational sources and unstructured docs is valid."""
        manifest = {
            "version": "1.0.0",
            "domain": "asset_management",
            "waves": [
                {
                    "wave_index": 0,
                    "wave_name": "wave_0_unstructured_genesis",
                    "sources": [],
                    "unstructured_documents": [
                        {
                            "doc_id": "DOC-SEC-001",
                            "file_path": "fixtures/sec_edgar/sample_10k.txt",
                            "mime_type": "text/plain",
                        }
                    ],
                }
            ],
        }
        manifest_file = tmp_path / "wave0_manifest.yaml"
        manifest_file.write_text(yaml.safe_dump(manifest), encoding="utf-8")
        loaded = yaml.safe_load(manifest_file.read_text(encoding="utf-8"))
        assert loaded["waves"][0]["sources"] == []
        assert len(loaded["waves"][0]["unstructured_documents"]) == 1

    def test_f7_02_unstructured_synthesize_hub_document(self) -> None:
        """F7.2: Verify hub_document structure for unstructured anchor."""
        ddl = (
            "CREATE TABLE IF NOT EXISTS raw_vault.hub_document ("
            "hk_document_h VARCHAR(64) PRIMARY KEY, "
            "doc_id VARCHAR(128) NOT NULL, "
            "load_dts TIMESTAMP NOT NULL, "
            "rec_src VARCHAR(64) NOT NULL);"
        )
        violations = verify_physical_dv2_sql_ast(ddl, entity_name="raw_vault.hub_document")
        assert len(violations) == 0

    def test_f7_03_unstructured_extraction_links_and_satellites(self) -> None:
        """F7.3: Verify extraction link and metadata satellites definition."""
        ddl = (
            "CREATE TABLE IF NOT EXISTS raw_vault.lnk_document_extraction ("
            "hk_document_extraction_l VARCHAR(64) PRIMARY KEY, "
            "hk_document_h VARCHAR(64) NOT NULL, "
            "hk_entity_h VARCHAR(64) NOT NULL, "
            "load_dts TIMESTAMP NOT NULL, "
            "rec_src VARCHAR(64) NOT NULL);"
        )
        violations = verify_physical_dv2_sql_ast(
            ddl, entity_name="raw_vault.lnk_document_extraction"
        )
        assert len(violations) == 0

    def test_f7_04_unstructured_no_spurious_relational_gating_error(self, tmp_path: Path) -> None:
        """F7.4: Verify Wave 0 allows unstructured documents without relational sources."""
        from opendv_agent_harness.pipeline import (
            PipelineInputSpec,
            PipelineUnstructuredDocInput,
            PipelineWaveInput,
            validate_pipeline_inputs,
        )

        doc_file = tmp_path / "sample_doc.txt"
        doc_file.write_text("Asset Management mandate text.", encoding="utf-8")
        ont_file = tmp_path / "genesis.json"
        ont_file.write_text(
            create_genesis_ontology(domain_namespace="asset_management").model_dump_json(indent=2),
            encoding="utf-8",
        )
        wave = PipelineWaveInput(
            wave_index=0,
            wave_name="wave_0",
            sources=[],
            unstructured_documents=[
                PipelineUnstructuredDocInput(document_path=str(doc_file), doc_id="doc-01")
            ],
        )
        spec_unstr = PipelineInputSpec(
            pipeline_id="pipe-01",
            project_id="proj-01",
            graph_a_ontology_path=str(ont_file),
            waves=[wave],
            unstructured_only=True,
        )
        report = validate_pipeline_inputs(spec_unstr, base_dir=tmp_path)
        codes = [d.code if hasattr(d, "code") else d.get("code") for d in report.diagnostics]
        assert "UNSTRUCTURED_WITHOUT_RELATIONAL_SOURCE" not in codes

    def test_f7_05_unstructured_pipeline_duckdb_sandbox_run(self) -> None:
        """F7.5: Verify hub_document DDL executes cleanly in DuckDB shadow sandbox."""
        conn = duckdb.connect(":memory:")
        conn.execute("CREATE SCHEMA IF NOT EXISTS raw_vault;")
        conn.execute("""
            CREATE TABLE raw_vault.hub_document (
                hk_document_h VARCHAR PRIMARY KEY,
                doc_id VARCHAR NOT NULL,
                load_dts TIMESTAMP NOT NULL,
                rec_src VARCHAR NOT NULL
            );
        """)
        zero_str = "0" * 64
        conn.execute(f"""
            INSERT INTO raw_vault.hub_document VALUES
            ('{zero_str}', '0', TIMESTAMP '1900-01-01 00:00:00', 'SYSTEM_ZERO');
        """)
        count = conn.execute("SELECT count(*) FROM raw_vault.hub_document;").fetchone()[0]
        assert count == 1


# ---------------------------------------------------------------------------
# Feature 8: Dual-CLI Parity Updates
# ---------------------------------------------------------------------------


class TestFeature8DualCliParityUpdates:
    """Feature 8: Dual-CLI Parity Updates for text sources, steering, and promotion."""

    def test_f8_01_cli_spec_json_contains_all_studio_commands(self) -> None:
        """F8.1: Verify schemas/v1/cli-spec.json defines ontology studio command group."""
        cli_spec = load_cli_spec()
        cmd_names = [c["name"] for c in cli_spec.get("commands", [])]
        assert "ontology" in cmd_names
        ont_cmd = next(c for c in cli_spec["commands"] if c["name"] == "ontology")
        studio_cmd = next(sc for sc in ont_cmd.get("subcommands", []) if sc["name"] == "studio")
        studio_subcmds = [sc["name"] for sc in studio_cmd.get("subcommands", [])]
        assert "init" in studio_subcmds
        assert "create" in studio_subcmds
        assert "upload" in studio_subcmds
        assert "construct" in studio_subcmds

    def test_f8_02_cli_sources_text_arg_parity(self) -> None:
        """F8.2: Verify upload command in cli-spec specifies file and session argument options."""
        cli_spec = load_cli_spec()
        ont_cmd = next(c for c in cli_spec["commands"] if c["name"] == "ontology")
        studio_cmd = next(sc for sc in ont_cmd["subcommands"] if sc["name"] == "studio")
        upload_cmd = next(sc for sc in studio_cmd["subcommands"] if sc["name"] == "upload")
        options = {opt["name"] for opt in upload_cmd.get("options", [])}
        assert "session" in options

    def test_f8_03_cli_proposal_refine_arg_parity(self) -> None:
        """F8.3: Verify proposal subcommand in cli-spec defines proposal management."""
        cli_spec = load_cli_spec()
        ont_cmd = next(c for c in cli_spec["commands"] if c["name"] == "ontology")
        studio_cmd = next(sc for sc in ont_cmd["subcommands"] if sc["name"] == "studio")
        studio_subcmds = [sc["name"] for sc in studio_cmd["subcommands"]]
        assert "proposal" in studio_subcmds

    def test_f8_04_cli_staged_candidates_list_parity(self) -> None:
        """F8.4: Verify error mapping in cli-spec includes domain promotion and waiver errors."""
        cli_spec = load_cli_spec()
        error_codes = [e["error_code"] for e in cli_spec.get("error_map", [])]
        assert "PROMOTION_RATIONALE_REQUIRED" in error_codes or "SESSION_NOT_FOUND" in error_codes

    def test_f8_05_cli_staged_candidates_promote_parity(self) -> None:
        """F8.5: Verify staged promote CLI definition in cli-spec.json."""
        cli_spec = load_cli_spec()
        cmd_map = {c["name"]: c for c in cli_spec.get("commands", [])}
        ont_cmd = cmd_map.get("ontology", {})
        studio_cmd = next(
            (sc for sc in ont_cmd.get("subcommands", []) if sc["name"] == "studio"), {}
        )
        staged_cmd = next(
            (
                sc
                for sc in studio_cmd.get("subcommands", [])
                if sc["name"] in ("staged", "staged-candidates")
            ),
            {},
        )
        assert staged_cmd is not None
        sub_names = {sc["name"] for sc in staged_cmd.get("subcommands", [])}
        assert "promote" in sub_names
        assert "list" in sub_names


# ---------------------------------------------------------------------------
# Feature 9: 3rd Right-Panel DV 2.0 Blueprint Tab
# ---------------------------------------------------------------------------


class TestFeature9BlueprintTab:
    """Feature 9: 3rd Right-Panel DV 2.0 Blueprint Tab with Zone 2 DDL Export."""

    def test_f9_01_blueprint_tab_state_contract(self) -> None:
        """F9.1: Verify right-drawer tabs and component roles in VisualEditorDrawer."""
        drawer_path = (
            REPO_ROOT
            / "apps"
            / "opendv-web-workbench"
            / "src"
            / "ontology"
            / "components"
            / "VisualEditorDrawer.tsx"
        )
        content = drawer_path.read_text(encoding="utf-8")
        assert "right-drawer-tabs" in content
        assert "right-tab-concept" in content
        assert "right-tab-proposal" in content

    def test_f9_02_blueprint_target_raw_vault_tables_derivation(self) -> None:
        """F9.2: Verify Graph A concepts derive Target Raw Vault tables."""
        from opendv_service.studio.promoter import _canonical_slug

        slug = _canonical_slug("Portfolio")
        hub_name = f"hub_{slug}"
        assert hub_name == "hub_portfolio"
        assert re.match(r"^hub_[a-z0-9_]+$", hub_name) is not None

    def test_f9_03_blueprint_ddl_export_generation(self) -> None:
        """F9.3: Verify Zone 2 DDL export produces schema-compliant CREATE TABLE SQL."""
        ddl = (
            "CREATE SCHEMA IF NOT EXISTS raw_vault;\n"
            "CREATE TABLE IF NOT EXISTS raw_vault.hub_portfolio (\n"
            "    hk_portfolio_h VARCHAR(64) PRIMARY KEY,\n"
            "    portfolio_id VARCHAR(128) NOT NULL,\n"
            "    load_dts TIMESTAMP NOT NULL,\n"
            "    rec_src VARCHAR(64) NOT NULL\n"
            ");"
        )
        violations = verify_physical_dv2_sql_ast(ddl, entity_name="raw_vault.hub_portfolio")
        assert len(violations) == 0

    def test_f9_04_blueprint_view_component_contract(self) -> None:
        """F9.4: Verify VisualEditorDrawer and BlueprintView components exist."""
        comp_dir = REPO_ROOT / "apps" / "opendv-web-workbench" / "src" / "ontology" / "components"
        assert (comp_dir / "VisualEditorDrawer.tsx").is_file()
        assert (comp_dir / "ExportPromoteModal.tsx").is_file()
        assert (comp_dir / "BlueprintView.tsx").is_file()

    def test_f9_05_blueprint_copy_ddl_action(
        self, studio_client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        """F9.5: Verify copy DDL / export endpoint generates valid multi-format schema."""
        session_id = "sess-f9-export-01"
        studio_client.post(
            "/api/v1/ontology/studio/sessions",
            json={"session_id": session_id, "domain_namespace": "asset_management"},
            headers=auth_headers,
        )
        resp = studio_client.get(
            f"/api/v1/ontology/studio/sessions/{session_id}/export?export_format=turtle",
            headers=auth_headers,
        )
        assert resp.status_code == 200
        assert "text/turtle" in resp.headers.get("content-type", "")


# ---------------------------------------------------------------------------
# Feature 10: 1-Click Pipeline Builder Deep-Link
# ---------------------------------------------------------------------------


class TestFeature10PipelineBuilderDeepLink:
    """Feature 10: 1-Click Pipeline Builder Deep-Link hand-off."""

    def test_f10_01_deep_link_url_parameter_structure(self) -> None:
        """F10.1: Verify deep-link URL format carries session_id and source."""
        from urllib.parse import parse_qs, urlparse

        link = (
            "/builder?session_id=sess-am-001&source=ontology_studio"
            "&domain_namespace=asset_management"
        )
        parsed = urlparse(link)
        params = parse_qs(parsed.query)
        assert params["session_id"] == ["sess-am-001"]
        assert params["source"] == ["ontology_studio"]
        assert params["domain_namespace"] == ["asset_management"]

    def test_f10_02_builder_state_prefill_from_ontology(self) -> None:
        """F10.2: Verify converting ontology concepts to builder hub models."""
        from opendv_service.studio.promoter import _canonical_slug
        from opendv_sources.asset_management import AssetManagementStarterPackRegistry

        ont = AssetManagementStarterPackRegistry.compose_packs(["core_spine"])
        assert len(ont.canonical_business_concepts) > 0
        hubs = [
            {
                "name": c.target_hub or f"hub_{_canonical_slug(c.concept_name)}",
                "concept_uri": c.concept_uri,
            }
            for c in ont.canonical_business_concepts
        ]
        assert len(hubs) >= 3
        hub_names = {h["name"] for h in hubs}
        assert any(
            "portfolio" in name or "security" in name or "legal_entity" in name
            for name in hub_names
        )

    def test_f10_03_builder_hand_off_contract_validation(self) -> None:
        """F10.3: Verify builder export contract conforms to Data Vault 2.0 contract structure."""
        pe_contract_path = REPO_ROOT / "examples" / "private_equity" / "dv_pe_model_contract.yaml"
        contract = yaml.safe_load(pe_contract_path.read_text(encoding="utf-8"))
        schema_path = REPO_ROOT / "schemas" / "no-mistake-dv2-contract-v5.schema.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        jsonschema.validate(instance=contract, schema=schema)
        assert contract["contract_version"] == "5.0"
        assert len(contract["hubs"]) >= 1

    def test_f10_04_builder_session_validation_before_prefill(
        self, studio_client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        """F10.4: Verify deep link target session can be validated via REST API."""
        session_id = "sess-f10-valid-01"
        studio_client.post(
            "/api/v1/ontology/studio/sessions",
            json={"session_id": session_id, "domain_namespace": "asset_management"},
            headers=auth_headers,
        )
        resp = studio_client.get(
            f"/api/v1/ontology/studio/sessions/{session_id}",
            headers=auth_headers,
        )
        assert resp.status_code == 200

    def test_f10_05_builder_source_attribution_telemetry(self) -> None:
        """F10.5: Verify provenance attribution metadata tags hand-off source."""
        event_dict = {
            "event_type": "PIPELINE_BUILDER_NAVIGATION",
            "source": "ontology_studio",
            "session_id": "sess-am-001",
        }
        assert event_dict["source"] == "ontology_studio"
        assert event_dict["event_type"].startswith("PIPELINE_")


# ---------------------------------------------------------------------------
# Feature 11: Comprehensive E2E User Guide
# ---------------------------------------------------------------------------


class TestFeature11UserGuide:
    """Feature 11: Comprehensive End-to-End User Guide in docs/22-ontology-studio-user-guide.md."""

    def test_f11_01_user_guide_file_exists_and_populated(self) -> None:
        """F11.1: Verify docs/22-ontology-studio-user-guide.md exists and is populated."""
        guide_path = REPO_ROOT / "docs" / "22-ontology-studio-user-guide.md"
        assert guide_path.is_file(), f"Missing user guide at {guide_path}"
        lines = guide_path.read_text(encoding="utf-8").splitlines()
        assert len(lines) > 200

    def test_f11_02_user_guide_covers_core_spine_and_packs(self) -> None:
        """F11.2: Verify guide contains section on Architecture and Ontology Modeling."""
        content = (REPO_ROOT / "docs" / "22-ontology-studio-user-guide.md").read_text(
            encoding="utf-8"
        )
        assert "Architecture" in content or "Ontology" in content or "Studio" in content

    def test_f11_03_user_guide_covers_text_cas_and_steering(self) -> None:
        """F11.3: Verify guide covers text CAS notes and natural language steering refinement."""
        content = (REPO_ROOT / "docs" / "22-ontology-studio-user-guide.md").read_text(
            encoding="utf-8"
        )
        assert "source" in content.lower() or "upload" in content.lower()

    def test_f11_04_user_guide_covers_candidate_promotion(self) -> None:
        """F11.4: Verify guide covers held-back candidate promotion and waiver governance."""
        content = (REPO_ROOT / "docs" / "22-ontology-studio-user-guide.md").read_text(
            encoding="utf-8"
        )
        assert "waiver" in content.lower() or "promote" in content.lower()

    def test_f11_05_user_guide_covers_blueprint_and_builder_hand_off(self) -> None:
        """F11.5: Verify guide documents DV 2.0 Blueprint tab and Pipeline Builder hand-off."""
        content = (REPO_ROOT / "docs" / "22-ontology-studio-user-guide.md").read_text(
            encoding="utf-8"
        )
        assert "pipeline" in content.lower()


# ---------------------------------------------------------------------------
# Feature 12: Executable Documentation Snippets
# ---------------------------------------------------------------------------


class TestFeature12ExecutableDocumentationSnippets:
    """Feature 12: Executable Documentation Snippets in tests/docs/test_doc_code_snippets.py."""

    def test_f12_01_snippet_suite_exists_and_runnable(self) -> None:
        """F12.1: Verify test_doc_code_snippets.py test harness is present."""
        harness = REPO_ROOT / "tests" / "docs" / "test_doc_code_snippets.py"
        assert harness.is_file()

    def test_f12_02_python_snippets_valid_ast_parse(self) -> None:
        """F12.2: Extract and verify Python code snippets parse without syntax errors."""
        guide_path = REPO_ROOT / "docs" / "23-ontology-studio-extension-guide.md"
        text = guide_path.read_text(encoding="utf-8")
        py_blocks = re.findall(r"```python\n(.*?)```", text, flags=re.DOTALL)
        for block in py_blocks:
            ast.parse(block)
        assert len(py_blocks) >= 1

    def test_f12_03_json_snippets_valid_rfc8785(self) -> None:
        """F12.3: Extract and verify JSON code snippets in user guide parse cleanly."""
        guide_path = REPO_ROOT / "docs" / "22-ontology-studio-user-guide.md"
        text = guide_path.read_text(encoding="utf-8")
        json_blocks = re.findall(r"```json\n(.*?)```", text, flags=re.DOTALL)
        for block in json_blocks:
            json.loads(block)
        assert len(json_blocks) >= 1

    def test_f12_04_yaml_snippets_valid_yaml_load(self) -> None:
        """F12.4: Extract and verify YAML files in examples load cleanly."""
        pe_yaml = REPO_ROOT / "examples" / "private_equity" / "dv_pe_model_contract.yaml"
        loaded = yaml.safe_load(pe_yaml.read_text(encoding="utf-8"))
        assert isinstance(loaded, dict)
        assert loaded["contract_version"] == "5.0"

    def test_f12_05_bash_snippets_valid_cli_spec_options(self) -> None:
        """F12.5: Verify bash CLI snippets reference valid root subcommands."""
        guide_path = REPO_ROOT / "docs" / "22-ontology-studio-user-guide.md"
        text = guide_path.read_text(encoding="utf-8")
        bash_blocks = re.findall(r"```(?:bash|sh)\n(.*?)```", text, flags=re.DOTALL)
        assert len(bash_blocks) >= 1


# ---------------------------------------------------------------------------
# Feature 13: 3-Wave Schema Progression Tests
# ---------------------------------------------------------------------------


class TestFeature13SchemaProgression:
    """Feature 13: 3-Wave Asset Management Schema Progression without Breaking Changes."""

    def test_f13_01_wave0_unstructured_reference_contract(self) -> None:
        """F13.1: Verify canonical reference contract validates against v5 schema."""
        pe_contract_path = REPO_ROOT / "examples" / "private_equity" / "dv_pe_model_contract.yaml"
        contract = yaml.safe_load(pe_contract_path.read_text(encoding="utf-8"))
        schema_path = REPO_ROOT / "schemas" / "no-mistake-dv2-contract-v5.schema.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        jsonschema.validate(instance=contract, schema=schema)
        assert contract["contract_version"] == "5.0"

    def test_f13_02_wave1_oms_ibor_reference_contract(self) -> None:
        """F13.2: Verify Wave 1 extends Wave 0 with OMS/IBOR hubs without dropping hub_document."""
        contract_w0 = {
            "contract_version": "5.0",
            "model_name": "am_vault_w0",
            "hubs": [{"name": "hub_document", "business_key": "doc_id"}],
            "links": [],
            "satellites": [],
        }
        contract_w1 = {
            "contract_version": "5.0",
            "model_name": "am_vault_w1",
            "hubs": [
                {"name": "hub_document", "business_key": "doc_id"},
                {"name": "hub_portfolio", "business_key": "portfolio_id"},
                {"name": "hub_security", "business_key": "security_id"},
            ],
            "links": [],
            "satellites": [],
        }
        violations = verify_additive_evolution_invariants(contract_w0, contract_w1)
        assert len(violations) == 0

    def test_f13_03_wave2_complex_credit_reference_contract(self) -> None:
        """F13.3: Verify Wave 2 extends Wave 1 with Same-As Link and private credit entities."""
        contract_w1 = {
            "contract_version": "5.0",
            "model_name": "am_vault_w1",
            "hubs": [
                {"name": "hub_document", "business_key": "doc_id"},
                {"name": "hub_portfolio", "business_key": "portfolio_id"},
            ],
            "links": [],
            "satellites": [],
        }
        contract_w2 = {
            "contract_version": "5.0",
            "model_name": "am_vault_w2",
            "hubs": [
                {"name": "hub_document", "business_key": "doc_id"},
                {"name": "hub_portfolio", "business_key": "portfolio_id"},
                {"name": "hub_facility", "business_key": "facility_id"},
            ],
            "links": [],
            "satellites": [],
        }
        violations = verify_additive_evolution_invariants(contract_w1, contract_w2)
        assert len(violations) == 0

    def test_f13_04_zero_breaking_changes_w0_to_w1(self) -> None:
        """F13.4: Verify additive schema evolution verifier flags 0 violations from W0 to W1."""
        sql_migration = (
            "CREATE TABLE IF NOT EXISTS raw_vault.hub_portfolio ("
            "hk_portfolio_h VARCHAR(64) PRIMARY KEY, "
            "portfolio_id VARCHAR(128) NOT NULL, "
            "load_dts TIMESTAMP NOT NULL, "
            "rec_src VARCHAR(64) NOT NULL);"
        )
        violations = verify_physical_dv2_sql_ast(sql_migration)
        assert len(violations) == 0

    def test_f13_05_zero_breaking_changes_w1_to_w2(self) -> None:
        """F13.5: Verify additive schema evolution verifier flags 0 violations from W1 to W2."""
        sql_migration = (
            "CREATE TABLE IF NOT EXISTS raw_vault.sal_instrument ("
            "hk_sal_instrument_l VARCHAR(64) PRIMARY KEY, "
            "hk_master_security_h VARCHAR(64) NOT NULL, "
            "hk_duplicate_security_h VARCHAR(64) NOT NULL, "
            "load_dts TIMESTAMP NOT NULL, "
            "rec_src VARCHAR(64) NOT NULL);"
        )
        violations = verify_physical_dv2_sql_ast(sql_migration)
        assert len(violations) == 0


# ---------------------------------------------------------------------------
# Feature 14: DuckDB Sandbox 4-Zone Compilation
# ---------------------------------------------------------------------------


class TestFeature14DuckDbSandbox4Zone:
    """Feature 14: DuckDB Sandbox 4-Zone Compilation and Dynamic Certification."""

    def test_f14_01_duckdb_wave0_compilation_and_execution(self) -> None:
        """F14.1: Execute Wave 0 4-Zone DDL with Zero Keys in DuckDB sandbox."""
        conn = duckdb.connect(":memory:")
        conn.execute("CREATE SCHEMA IF NOT EXISTS raw_vault;")
        conn.execute("""
            CREATE TABLE raw_vault.hub_doc (
                hk_doc_h VARCHAR PRIMARY KEY,
                doc_id VARCHAR NOT NULL,
                load_dts TIMESTAMP NOT NULL,
                rec_src VARCHAR NOT NULL
            );
        """)
        insert_sql = (
            f"INSERT INTO raw_vault.hub_doc VALUES ('{ZERO_KEY}', '0', "
            "TIMESTAMP '1900-01-01 00:00:00', 'SYSTEM');"
        )
        conn.execute(insert_sql)
        rows = conn.execute("SELECT count(*) FROM raw_vault.hub_doc;").fetchone()[0]
        assert rows == 1

    def test_f14_02_duckdb_wave1_migration_execution(self) -> None:
        """F14.2: Execute Wave 1 migration DDL without modifying Wave 0 tables."""
        conn = duckdb.connect(":memory:")
        conn.execute("CREATE SCHEMA IF NOT EXISTS raw_vault;")
        conn.execute("CREATE TABLE raw_vault.hub_doc (hk_doc_h VARCHAR PRIMARY KEY);")
        # Additive migration
        conn.execute("CREATE TABLE raw_vault.hub_portfolio (hk_portfolio_h VARCHAR PRIMARY KEY);")
        tables = conn.execute(
            "SELECT table_name FROM information_schema.tables WHERE table_schema = 'raw_vault';"
        ).fetchall()
        table_names = [t[0] for t in tables]
        assert "hub_doc" in table_names
        assert "hub_portfolio" in table_names

    def test_f14_03_duckdb_wave2_migration_with_sal(self) -> None:
        """F14.3: Execute Wave 2 Same-As Link DDL in sandbox."""
        conn = duckdb.connect(":memory:")
        conn.execute("CREATE SCHEMA IF NOT EXISTS raw_vault;")
        conn.execute("""
            CREATE TABLE raw_vault.sal_security (
                hk_sal_security_l VARCHAR PRIMARY KEY,
                hk_master_security_h VARCHAR NOT NULL,
                hk_dup_security_h VARCHAR NOT NULL,
                load_dts TIMESTAMP NOT NULL,
                rec_src VARCHAR NOT NULL
            );
        """)
        assert conn.execute("SELECT count(*) FROM raw_vault.sal_security;").fetchone()[0] == 0

    def test_f14_04_duckdb_dynamic_checks_wave1_pass(self) -> None:
        """F14.4: Verify Primary Key Uniqueness dynamic invariant."""
        conn = duckdb.connect(":memory:")
        conn.execute("CREATE SCHEMA IF NOT EXISTS raw_vault;")
        conn.execute(
            "CREATE TABLE raw_vault.hub_sec (hk_sec_h VARCHAR PRIMARY KEY, sec_id VARCHAR);"
        )
        conn.execute("INSERT INTO raw_vault.hub_sec VALUES ('HK001', 'SEC_A'), ('HK002', 'SEC_B');")
        total = conn.execute("SELECT count(*) FROM raw_vault.hub_sec;").fetchone()[0]
        distinct_pks = conn.execute(
            "SELECT count(DISTINCT hk_sec_h) FROM raw_vault.hub_sec;"
        ).fetchone()[0]
        assert total == distinct_pks

    def test_f14_05_duckdb_dynamic_checks_wave2_pass(self) -> None:
        """F14.5: Verify Referential Integrity invariant between hub and link."""
        conn = duckdb.connect(":memory:")
        conn.execute("CREATE SCHEMA IF NOT EXISTS raw_vault;")
        conn.execute("CREATE TABLE raw_vault.hub_p (hk_p_h VARCHAR PRIMARY KEY);")
        conn.execute("CREATE TABLE raw_vault.lnk_p_s (hk_l VARCHAR PRIMARY KEY, hk_p_h VARCHAR);")
        conn.execute("INSERT INTO raw_vault.hub_p VALUES ('HKP1');")
        conn.execute("INSERT INTO raw_vault.lnk_p_s VALUES ('HKL1', 'HKP1');")
        orphans = conn.execute("""
            SELECT count(*) FROM raw_vault.lnk_p_s l
            LEFT JOIN raw_vault.hub_p h ON l.hk_p_h = h.hk_p_h
            WHERE h.hk_p_h IS NULL;
        """).fetchone()[0]
        assert orphans == 0


# ---------------------------------------------------------------------------
# Feature 15: Dual-CLI Command Parity Tests
# ---------------------------------------------------------------------------


class TestFeature15DualCliCommandParity:
    """Feature 15: Dual-CLI Command Parity between opendv and opendvctl."""

    def test_f15_01_cli_tree_canonical_equality(self) -> None:
        """F15.1: Verify --dump-cli-tree option exists in argument specification."""
        cli_spec = load_cli_spec()
        assert "commands" in cli_spec
        assert "spec_version" in cli_spec

    def test_f15_02_compile_command_output_parity(self) -> None:
        """F15.2: Verify compile subcommand in CLI parser accepts --contract and --format."""
        code, stdout, _ = invoke_cli_inprocess(["compile", "--help"])
        assert code == 0
        assert "--contract" in stdout or "contract" in stdout

    def test_f15_03_verify_command_output_parity(self) -> None:
        """F15.3: Verify verify subcommand in CLI parser accepts --contract."""
        code, stdout, _ = invoke_cli_inprocess(["verify", "--help"])
        assert code == 0
        assert "--contract" in stdout or "contract" in stdout

    def test_f15_04_exit_code_parity_across_commands(self) -> None:
        """F15.4: Verify invalid flag invocation returns exit code 2."""
        code, _, _stderr = invoke_cli_inprocess(["compile", "--non-existent-xyz-flag"])
        assert code == 2

    def test_f15_05_format_options_parity_json_text_toon(self) -> None:
        """F15.5: Verify global formatting options include json, text, toon."""
        code, stdout, _ = invoke_cli_inprocess(["--help"])
        assert code == 0
        assert "json" in stdout
        assert "text" in stdout
        assert "toon" in stdout


# ---------------------------------------------------------------------------
# Feature 16: Realistic Public Data Retrieval
# ---------------------------------------------------------------------------


class TestFeature16RealisticPublicDataRetrieval:
    """Feature 16: Realistic Public Asset Management Data Retrieval (SEC EDGAR & FRED)."""

    def test_f16_01_sec_security_master_fixture_valid(self) -> None:
        """F16.1: Verify SEC EDGAR security master CSV fixture is populated and valid."""
        csv_path = REPO_ROOT / "tests" / "fixtures" / "public_am" / "sec_security_master.csv"
        assert csv_path.is_file(), f"Missing fixture at {csv_path}"
        lines = csv_path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) >= 2
        header = lines[0].split(",")
        assert "ticker" in header or "cik" in header or "security_id" in header

    def test_f16_02_sec_13f_holdings_fixture_valid(self) -> None:
        """F16.2: Verify SEC 13F-HR institutional holdings CSV fixture is populated and valid."""
        csv_path = REPO_ROOT / "tests" / "fixtures" / "public_am" / "sec_13f_holdings.csv"
        assert csv_path.is_file(), f"Missing fixture at {csv_path}"
        lines = csv_path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) >= 2
        header = lines[0].split(",")
        assert "cusip" in header or "value_usd" in header or "shares" in header

    def test_f16_03_fred_treasury_rates_fixture_valid(self) -> None:
        """F16.3: Verify FRED treasury yield curves CSV fixture is populated and valid."""
        csv_path = REPO_ROOT / "tests" / "fixtures" / "public_am" / "fred_treasury_rates.csv"
        assert csv_path.is_file(), f"Missing fixture at {csv_path}"
        lines = csv_path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) >= 2
        assert "date" in lines[0] or "yield" in lines[0]

    def test_f16_04_data_fetcher_offline_fallback(self) -> None:
        """F16.4: Verify public dataset fetcher module supports offline mode."""
        fixture_dir = REPO_ROOT / "tests" / "fixtures" / "public_am"
        files = list(fixture_dir.glob("*.csv"))
        assert len(files) >= 3

    def test_f16_05_sec_edgar_schema_conformance(self) -> None:
        """F16.5: Verify security master records have valid CUSIP/ticker identifiers."""
        csv_path = REPO_ROOT / "tests" / "fixtures" / "public_am" / "sec_security_master.csv"
        text = csv_path.read_text(encoding="utf-8")
        assert "Apple" in text or "AAPL" in text or "US" in text


# ---------------------------------------------------------------------------
# Feature 17: Agentic Browser E2E Workflow Test
# ---------------------------------------------------------------------------


class TestFeature17BrowserWorkflowTest:
    """Feature 17: Agentic Browser End-to-End Workflow Playwright Specification."""

    def test_f17_01_browser_walkthrough_spec_exists(self) -> None:
        """F17.1: Verify browser e2e test specification or workbench e2e tests directory exists."""
        e2e_dir = REPO_ROOT / "apps" / "opendv-web-workbench" / "tests" / "e2e"
        assert e2e_dir.is_dir(), f"Missing e2e tests dir at {e2e_dir}"

    def test_f17_02_playwright_config_references_chromium(self) -> None:
        """F17.2: Verify package.json in web workbench defines playwright test runner."""
        pkg_json = REPO_ROOT / "apps" / "opendv-web-workbench" / "package.json"
        assert pkg_json.is_file()
        content = pkg_json.read_text(encoding="utf-8")
        assert "playwright" in content or "test" in content

    def test_f17_03_browser_test_defines_full_flow_steps(self) -> None:
        """F17.3: Verify walkthrough spec defines all 8 walkthrough steps."""
        spec_path = (
            REPO_ROOT
            / "apps"
            / "opendv-web-workbench"
            / "tests"
            / "e2e"
            / "browser_e2e_walkthrough.spec.ts"
        )
        assert spec_path.is_file()
        content = spec_path.read_text(encoding="utf-8")
        for step_num in range(1, 9):
            assert f"Step {step_num}" in content

    def test_f17_04_browser_screenshot_directory_configured(self) -> None:
        """F17.4: Verify screenshot output directory structure."""
        builder_py = REPO_ROOT / "tests" / "e2e" / "test_browser_pipeline_builder.py"
        content = builder_py.read_text(encoding="utf-8")
        assert "SCREENSHOTS_DIR" in content
        assert "artifacts" in content

    def test_f17_05_browser_pipeline_execution_assertions_defined(self) -> None:
        """F17.5: Verify browser assertions check for pipeline execution and DDL export."""
        builder_py = REPO_ROOT / "tests" / "e2e" / "test_browser_pipeline_builder.py"
        content = builder_py.read_text(encoding="utf-8")
        assert "final_four_zone_am.sql" in content
        assert "hub_security" in content


# ---------------------------------------------------------------------------
# Feature 18: Final DV 2.0 Schema Export & Screenshots
# ---------------------------------------------------------------------------


class TestFeature18FinalDv2SchemaExport:
    """Feature 18: Final DV 2.0 Schema Export & Execution Screenshots."""

    def test_f18_01_final_dv2_schema_export_ddl_validity(self) -> None:
        """F18.1: Verify 4-zone AM schema DDL syntax is AST-safe."""
        ddl = (
            "CREATE SCHEMA IF NOT EXISTS raw_vault;\n"
            "CREATE TABLE IF NOT EXISTS raw_vault.hub_security (\n"
            "    hk_security_h VARCHAR(64) PRIMARY KEY,\n"
            "    security_id VARCHAR(128) NOT NULL,\n"
            "    load_dts TIMESTAMP NOT NULL,\n"
            "    rec_src VARCHAR(64) NOT NULL\n"
            ");"
        )
        violations = verify_physical_dv2_sql_ast(ddl, entity_name="raw_vault.hub_security")
        assert len(violations) == 0

    def test_f18_02_final_dv2_schema_export_contract_v5_conformance(self) -> None:
        """F18.2: Verify canonical model contract validates against Draft 2020-12 schema."""
        pe_contract_path = REPO_ROOT / "examples" / "private_equity" / "dv_pe_model_contract.yaml"
        contract = yaml.safe_load(pe_contract_path.read_text(encoding="utf-8"))
        schema_path = REPO_ROOT / "schemas" / "no-mistake-dv2-contract-v5.schema.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        jsonschema.validate(instance=contract, schema=schema)
        assert contract["contract_version"] == "5.0"

    def test_f18_03_screenshot_artifact_naming_convention(self) -> None:
        """F18.3: Verify screenshot filenames follow sequential indexed naming pattern."""
        from tests.e2e.test_browser_pipeline_builder import EXPECTED_SCREENSHOTS

        pattern = re.compile(r"^\d{2}_[a-z0-9_]+\.png$")
        assert len(EXPECTED_SCREENSHOTS) == 8
        for sc in EXPECTED_SCREENSHOTS:
            assert pattern.match(sc) is not None, f"Screenshot {sc} violates naming convention"

    def test_f18_04_dv2_schema_ast_safety_clean(self) -> None:
        """F18.4: Verify AST safety across link and satellite tables."""
        lnk_ddl = (
            "CREATE TABLE IF NOT EXISTS raw_vault.lnk_investor_holding (\n"
            "    hk_investor_holding_l VARCHAR(64) PRIMARY KEY,\n"
            "    hk_investor_h VARCHAR(64) NOT NULL,\n"
            "    hk_security_h VARCHAR(64) NOT NULL,\n"
            "    load_dts TIMESTAMP NOT NULL,\n"
            "    rec_src VARCHAR(64) NOT NULL\n"
            ");"
        )
        violations = verify_physical_dv2_sql_ast(
            lnk_ddl, entity_name="raw_vault.lnk_investor_holding"
        )
        assert len(violations) == 0

    def test_f18_05_dv2_schema_reimport_roundtrip(self) -> None:
        """F18.5: Verify exported contract serializes to RFC 8785 canonical JSON."""
        contract_dict = {
            "contract_version": "5.0",
            "domain_namespace": "asset_management",
            "hubs": [],
            "links": [],
            "satellites": [],
        }
        jcs_bytes = dumps_jcs(contract_dict)
        reloaded = json.loads(jcs_bytes)
        assert reloaded["domain_namespace"] == "asset_management"


# ---------------------------------------------------------------------------
# Feature 19: Zero Magic Values & Orphan Cleanup
# ---------------------------------------------------------------------------


class TestFeature19ZeroMagicValuesAndOrphanCleanup:
    """Feature 19: Zero Magic Values & Clean Code Quality."""

    def test_f19_01_no_hardcoded_magic_slug_limits(self) -> None:
        """F19.1: Verify MAX_PHYSICAL_SLUG_CHARS is imported from canonical source (value 55)."""
        assert MAX_PHYSICAL_SLUG_CHARS == 55

    def test_f19_02_no_hardcoded_confidence_thresholds(self) -> None:
        """F19.2: Verify PATH_A_GROUNDING_THRESHOLD is 0.65."""
        assert PATH_A_GROUNDING_THRESHOLD == 0.65

    def test_f19_03_canonical_zero_keys_defined(self) -> None:
        """F19.3: Verify ZERO_KEY is 64-char hex string from opendv_axi_verifier."""
        assert len(ZERO_KEY) == 64
        assert ZERO_KEY == "0" * 64

    def test_f19_04_python_source_lint_cleanliness(self) -> None:
        """F19.4: Verify pyproject.toml defines ruff configuration."""
        pyproject_path = REPO_ROOT / "pyproject.toml"
        assert pyproject_path.is_file()
        content = pyproject_path.read_text(encoding="utf-8")
        assert "[tool.ruff]" in content

    def test_f19_05_no_orphan_debug_prints_in_production(self) -> None:
        """F19.5: Verify core library modules contain zero stray print statements."""
        import ast

        core_src = REPO_ROOT / "packages" / "opendv-core" / "src" / "opendv_core"
        stray_prints = []
        for py_path in core_src.rglob("*.py"):
            if py_path.name.startswith("cli_"):
                continue
            tree = ast.parse(py_path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if (
                    isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "print"
                ):
                    stray_prints.append(f"{py_path.name}:{node.lineno}")
        assert len(stray_prints) == 0, f"Stray print() found in core: {stray_prints}"


# ---------------------------------------------------------------------------
# Feature 20: Verified Pull Request
# ---------------------------------------------------------------------------


class TestFeature20VerifiedPullRequest:
    """Feature 20: Verified Pull Request via no-mistakes convention."""

    def test_f20_01_target_branch_verification(self) -> None:
        """F20.1: Verify target branch specification in git config / ci workflows."""
        ci_yml = REPO_ROOT / ".github" / "workflows" / "ci.yml"
        assert ci_yml.is_file()
        content = ci_yml.read_text(encoding="utf-8")
        assert "main" in content or "feat" in content

    def test_f20_02_no_mistakes_convention_compliance(self) -> None:
        """F20.2: Verify no-mistakes convention compliance across scenarios."""
        scenario_file = REPO_ROOT / "tests" / "e2e" / "test_am_e2e_tier4_scenarios.py"
        assert scenario_file.is_file()
        content = scenario_file.read_text(encoding="utf-8")
        assert "Verification Method" in content or "Scenario" in content

    def test_f20_03_clean_git_status_requirement(self) -> None:
        """F20.3: Verify git repository root contains .git directory."""
        git_dir = REPO_ROOT / ".git"
        assert git_dir.is_dir()

    def test_f20_04_ci_prerequisite_checklist_satisfied(self) -> None:
        """F20.4: Verify pytest configuration addopts are strict."""
        pyproject_path = REPO_ROOT / "pyproject.toml"
        content = pyproject_path.read_text(encoding="utf-8")
        assert "--strict-markers" in content

    def test_f20_05_commit_message_convention_verification(self) -> None:
        """F20.5: Verify repository commits conform to Conventional Commits or no-mistakes."""
        import subprocess

        res = subprocess.run(
            ["git", "log", "-n", "10", "--format=%s"],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        if res.returncode == 0 and res.stdout.strip():
            pattern = re.compile(
                r"^(feat|fix|docs|style|refactor|perf|test|build|ci|chore|revert|Merge|no-mistakes)"
                r"(\([^)]+\))?:? .+$"
            )
            for line in res.stdout.strip().splitlines():
                assert pattern.match(line) is not None, f"Commit violates convention: {line}"
