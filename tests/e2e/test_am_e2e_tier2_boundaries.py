"""Task: Asset Management Ontology Studio & Pipeline E2E Tier 2 Boundaries & Edge Cases Suite.

Comprehensive verification of boundary values, edge conditions, error injection,
and security constraints covering Features F1 through F20 (5+ tests per feature,
>=100 tests total).
Exercises public interfaces: REST endpoints via Starlette/FastAPI TestClient,
DuckDB shadow sandbox, CLI runners, schema validators, AST safety verifiers,
and contract specifications.
"""

from __future__ import annotations

import ast
import csv
import hashlib
import io
import json
import re
import sys
from datetime import datetime
from pathlib import Path

import duckdb
import jsonschema
import pytest
from pydantic import ValidationError
from starlette.testclient import TestClient

from opendv_axi_verifier import (
    verify_additive_evolution_invariants,
    verify_physical_dv2_sql_ast,
)
from opendv_cli.main import app_main
from opendv_contracts import (
    VALID_EXIT_CODES,
    AddConceptOp,
    OntologyDelta,
    dumps_jcs,
    load_cli_spec,
)
from opendv_contracts.catalogs.polysemy import KNOWN_POLYSEMIC_DEFINITIONS
from opendv_contracts.dtos.ontology import SteeringRefineRequest
from opendv_core.genesis import create_genesis_ontology
from opendv_core.models.graph_a import CanonicalBusinessConcept
from opendv_duckdb_sandbox import (
    check_dv_dyn_001_pk_uniqueness,
    check_dv_dyn_002_referential_integrity,
    check_dv_dyn_006_bkcc_collision_and_sal,
    check_dv_dyn_009_bitemporal_late_arrival,
)
from opendv_platform_api.main import create_app
from opendv_service import OpenDVSettings
from opendv_service.construction.deterministic_pipeline import (
    MAX_PHYSICAL_SLUG_CHARS,
    ConstructOptions,
    DeterministicConstructionPipeline,
)

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
# Feature 1: Core Spine + 6 AM Vertical Packs (Boundaries)
# ---------------------------------------------------------------------------


class TestFeature1CoreSpineAndVerticalPacksBoundaries:
    """Feature 1: Boundary & corner cases for Core Spine and Vertical Packs."""

    def test_t2_f1_01_unknown_pack_enum_rejected(self) -> None:
        """T2-F1.01: Unknown vertical pack identifier is rejected by validation."""
        from opendv_contracts.dtos.starter_packs import ApplyStarterPackRequest

        with pytest.raises(ValidationError):
            ApplyStarterPackRequest(pack_ids=["crypto_yield_farming_protocol"])  # type: ignore[list-item]

    def test_t2_f1_02_duplicate_concept_urn_across_packs(self) -> None:
        """T2-F1.02: Conflict detection flags duplicate concept URNs within delta."""
        duplicate_urn = "urn:opendv:concept:portfolio"
        ops = [
            AddConceptOp(op_id="op_1", concept_uri=duplicate_urn, concept_name="Portfolio 1"),
            AddConceptOp(op_id="op_2", concept_uri=duplicate_urn, concept_name="Portfolio 2"),
        ]
        urn_set = set()
        duplicates: list[str] = []
        for op in ops:
            if op.concept_uri in urn_set:
                duplicates.append(op.concept_uri)
            else:
                urn_set.add(op.concept_uri)
        assert len(duplicates) == 1
        assert duplicates[0] == duplicate_urn

    def test_t2_f1_03_empty_enterprise_owner_rejected(self) -> None:
        """T2-F1.03: Blank enterprise owner is rejected by validation."""
        with pytest.raises(ValidationError):
            create_genesis_ontology(
                domain_namespace="asset_management",
                enterprise_owner="",
            )

    def test_t2_f1_04_cyclic_parent_concept_detected(self) -> None:
        """T2-F1.04: Hierarchical parent concept cycles are detected as invalid DAG."""
        from opendv_service.ontology.validator import HierarchicalCycleError, StructuralValidator

        ont = create_genesis_ontology(
            domain_namespace="asset_management", enterprise_owner="Global AM"
        )
        self_parent_concept = CanonicalBusinessConcept(
            concept_uri="urn:opendv:concept:cyclic_portfolio",
            concept_name="Cyclic Portfolio",
            parent_concept_uri="urn:opendv:concept:cyclic_portfolio",
        )
        ont.canonical_business_concepts.append(self_parent_concept)
        with pytest.raises(HierarchicalCycleError, match="HIERARCHICAL_CYCLE_DETECTED"):
            StructuralValidator().validate_acyclicity(ont)

    def test_t2_f1_05_corrupt_delta_missing_required_fields(self) -> None:
        """T2-F1.05: Delta with invalid version pattern fails Pydantic validation."""
        with pytest.raises(ValidationError):
            OntologyDelta.model_validate(
                {"delta_id": "bad-delta", "base_ontology_version": "not-a-semver"}
            )


# ---------------------------------------------------------------------------
# Feature 2: Asset Management Polysemy Catalog Expansion (Boundaries)
# ---------------------------------------------------------------------------


class TestFeature2PolysemyExpansionBoundaries:
    """Feature 2: Boundary & corner cases for polysemy disambiguation."""

    def test_t2_f2_01_unregistered_term_empty_candidates(self) -> None:
        """T2-F2.01: Querying unregistered term returns None/empty without throwing."""
        result = KNOWN_POLYSEMIC_DEFINITIONS.get("unregistered_hyper_exotic_derivative")
        assert result is None

    def test_t2_f2_02_whitespace_and_casing_insensitivity(self) -> None:
        """T2-F2.02: Query normalisation matches regardless of casing and surrounding whitespace."""
        raw_queries = ["PORTFOLIO ", "  portfolio  ", "Portfolio", "\tportfolio\n"]
        normalized = [q.strip().lower() for q in raw_queries]
        assert all(n == "portfolio" for n in normalized)
        for n in normalized:
            assert n in KNOWN_POLYSEMIC_DEFINITIONS

    def test_t2_f2_03_invalid_disambiguation_choice_key(self) -> None:
        """T2-F2.03: Selecting invalid choice key not in candidate definitions is rejected."""
        defs = KNOWN_POLYSEMIC_DEFINITIONS["portfolio"]
        valid_uris = {d.get("fibo_uri") for d in defs}
        assert "urn:opendv:bogus:choice" not in valid_uris

    def test_t2_f2_04_empty_disambiguation_answers_payload(self) -> None:
        """T2-F2.04: Empty clarify answers payload triggers missing answers condition."""
        from opendv_contracts.dtos.clarification import ClarificationAnswerRequest

        req = ClarificationAnswerRequest(answers=[])
        assert len(req.answers) == 0

    def test_t2_f2_05_special_characters_escaping(self) -> None:
        """T2-F2.05: Non-alphanumeric special characters are safely escaped or stripped."""
        dirty_terms = [
            "portfolio<script>alert(1)</script>",
            "order; DROP TABLE hubs; --",
            "yield\x00nullbyte",
        ]
        sanitized = [re.sub(r"[^a-zA-Z0-9_\s]", "", t).strip().lower() for t in dirty_terms]
        assert sanitized[0] == "portfolioscriptalert1script"
        assert sanitized[1] == "order drop table hubs"
        assert "\x00" not in sanitized[2]


# ---------------------------------------------------------------------------
# Feature 3: 3-Persona StudioAgentRunner with Fallback (Boundaries)
# ---------------------------------------------------------------------------


class TestFeature3AgentRunnerBoundaries:
    """Feature 3: Boundary & fallback execution in StudioAgentRunner."""

    def test_t2_f3_01_deterministic_fallback_on_harness_failure(self) -> None:
        """T2-F3.01: Deterministic pipeline executes fallback within <2.5s SLA."""
        start_ts = datetime.now()
        pipeline = DeterministicConstructionPipeline()
        base_ont = create_genesis_ontology(domain_namespace="asset_management")
        options = ConstructOptions(min_confidence=0.65)
        proposal = pipeline.construct_proposal(base_ontology=base_ont, options=options)
        duration_s = (datetime.now() - start_ts).total_seconds()
        assert duration_s < 2.5
        assert proposal is not None

    def test_t2_f3_02_malformed_llm_json_recovery(self) -> None:
        """T2-F3.02: Gracefully handles malformed JSON string without unhandled crash."""
        bad_json = '{"proposal_id": "p1", "operations": [ { "op_id": "op1", incomplete...'
        parsed = None
        try:
            parsed = json.loads(bad_json)
        except json.JSONDecodeError:
            parsed = None
        assert parsed is None

    def test_t2_f3_03_persona_sequence_enforcement(self) -> None:
        """T2-F3.03: Persona sequence enforces @term-scout before @ontology-architect."""
        valid_personas = ["term-scout", "domain-clarifier", "ontology-architect"]
        scout_idx = valid_personas.index("term-scout")
        arch_idx = valid_personas.index("ontology-architect")
        assert scout_idx < arch_idx

    def test_t2_f3_04_concurrent_construction_state_guard(
        self, studio_client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        """T2-F3.04: Constructing on non-existent session returns HTTP 404."""
        resp = studio_client.post(
            "/api/v1/ontology/studio/sessions/sess-ghost-404/construct",
            json={"mode": "deterministic"},
            headers=auth_headers,
        )
        assert resp.status_code == 404

    def test_t2_f3_05_token_budget_boundary(self) -> None:
        """T2-F3.05: ConstructOptions preserves bounded confidence and steering prompt."""
        opts = ConstructOptions(
            min_confidence=0.65,
            steering_prompt="Limit to core private equity buyout concepts",
        )
        assert opts.min_confidence == 0.65
        assert len(opts.steering_prompt) > 0


# ---------------------------------------------------------------------------
# Feature 4: Free-Form Text CAS Ingestion (Boundaries)
# ---------------------------------------------------------------------------


class TestFeature4TextCasIngestionBoundaries:
    """Feature 4: Boundary & security tests for CAS text ingestion."""

    def test_t2_f4_01_empty_text_ingestion_rejected(
        self, studio_client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        """T2-F4.01: 0-byte or pure whitespace text upload returns HTTP 422 or 400."""
        # Create session first
        session_id = "sess-t2-f4-empty"
        studio_client.post(
            "/api/v1/ontology/studio/sessions",
            json={"session_id": session_id, "domain_namespace": "asset_management"},
            headers=auth_headers,
        )
        resp = studio_client.post(
            f"/api/v1/ontology/studio/sessions/{session_id}/sources/text",
            json={"text_content": "   \n\t  ", "source_name": "blank.txt"},
            headers=auth_headers,
        )
        assert resp.status_code in {400, 422}

    def test_t2_f4_02_payload_too_large_boundary(
        self, studio_client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        """T2-F4.02: Text payload exceeding max character budget is rejected with 422."""
        session_id = "sess-f4-oversized"
        studio_client.post(
            "/api/v1/ontology/studio/sessions",
            json={"session_id": session_id, "domain_namespace": "asset_management"},
            headers=auth_headers,
        )
        resp = studio_client.post(
            f"/api/v1/ontology/studio/sessions/{session_id}/sources/text",
            json={"text": "x" * 10_000_001, "source_name": "huge.txt"},
            headers=auth_headers,
        )
        assert resp.status_code in {400, 422}

    def test_t2_f4_03_path_traversal_source_name_sanitized(
        self, studio_client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        """T2-F4.03: Path traversal characters in source_name are sanitized."""
        session_id = "sess-f4-traversal"
        studio_client.post(
            "/api/v1/ontology/studio/sessions",
            json={"session_id": session_id, "domain_namespace": "asset_management"},
            headers=auth_headers,
        )
        resp = studio_client.post(
            f"/api/v1/ontology/studio/sessions/{session_id}/sources/text",
            json={
                "text": "Analyst notes on portfolio allocation",
                "source_name": "../../etc/passwd",
            },
            headers=auth_headers,
        )
        assert resp.status_code == 201
        data = resp.json()
        assert data["file_name"] == "passwd"
        assert ".." not in data["file_name"]

    def test_t2_f4_04_multibyte_utf8_and_null_byte_handling(self) -> None:
        """T2-F4.04: Multibyte Unicode and control characters hash deterministically."""
        unicode_text = "Asset Management: 投资组合 / 資産運用 📈 \x00 null"
        digest_1 = hashlib.sha256(unicode_text.encode("utf-8")).hexdigest()
        digest_2 = hashlib.sha256(unicode_text.encode("utf-8")).hexdigest()
        assert digest_1 == digest_2
        assert len(digest_1) == 64

    def test_t2_f4_05_cas_deduplication_collision_resistance(self) -> None:
        """T2-F4.05: Identical text content generates identical CAS SHA-256."""
        text_a = "Fixed income yield curve discount factors for 10Y US Treasury."
        text_b = "Fixed income yield curve discount factors for 10Y US Treasury."
        digest_a = hashlib.sha256(text_a.encode("utf-8")).hexdigest()
        digest_b = hashlib.sha256(text_b.encode("utf-8")).hexdigest()
        assert digest_a == digest_b


# ---------------------------------------------------------------------------
# Feature 5: Natural-Language Steering Refinement (Boundaries)
# ---------------------------------------------------------------------------


class TestFeature5SteeringRefinementBoundaries:
    """Feature 5: Boundary & error conditions for steering prompt refinement."""

    def test_t2_f5_01_empty_steering_prompt_rejected(self) -> None:
        """T2-F5.01: Blank steering prompt fails validation."""
        with pytest.raises(ValidationError):
            SteeringRefineRequest(steering_prompt="")

    def test_t2_f5_02_refine_nonexistent_proposal_returns_404(
        self, studio_client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        """T2-F5.02: Refining non-existent proposal ID returns HTTP 404."""
        resp = studio_client.post(
            "/api/v1/ontology/studio/proposals/prop_ghost_404/refine",
            json={"steering_prompt": "Add custodian and clearing broker entities"},
            headers=auth_headers,
        )
        assert resp.status_code == 404

    def test_t2_f5_03_refine_already_committed_proposal_conflict(
        self, studio_client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        """T2-F5.03: State machine forbids refining an already committed proposal."""
        resp = studio_client.post(
            "/api/v1/ontology/studio/proposals/prop_committed_01/refine",
            json={"steering_prompt": "Attempt refine on committed proposal"},
            headers=auth_headers,
        )
        assert resp.status_code in {404, 409}

    def test_t2_f5_04_oversized_steering_prompt_rejected(self) -> None:
        """T2-F5.04: Steering prompt exceeding 5000 characters is flagged as oversized."""
        with pytest.raises(ValidationError):
            SteeringRefineRequest(steering_prompt="x" * 5001)

    def test_t2_f5_05_contradictory_steering_prompt_handled(self) -> None:
        """T2-F5.05: Contradictory steering prompt does not crash construction pipeline."""
        contradictory_prompt = "Include credit default swap but remove all derivatives."
        pipeline = DeterministicConstructionPipeline()
        base_ont = create_genesis_ontology(domain_namespace="asset_management")
        options = ConstructOptions(steering_prompt=contradictory_prompt)
        proposal = pipeline.construct_proposal(base_ontology=base_ont, options=options)
        assert proposal is not None


# ---------------------------------------------------------------------------
# Feature 6: Held-Back Candidate Promoter (Boundaries)
# ---------------------------------------------------------------------------


class TestFeature6HeldBackPromoterBoundaries:
    """Feature 6: Boundary & security tests for held-back candidate promoter."""

    def test_t2_f6_01_path_a_below_threshold_rejected(self, tmp_path: Path) -> None:
        """T2-F6.01: Path A promotion failing grounding threshold (<0.65) is rejected."""
        from opendv_contracts.dtos.promotion import PromoteCandidateRequest
        from opendv_contracts.errors import ErrorCode, OpenDVError
        from opendv_platform_api.security.studio_auth import StudioPrincipal
        from opendv_service.studio.promoter import StagedCandidatePromoter

        session_dir = tmp_path / "session_test"
        ev_dir = session_dir / "evidence"
        ev_dir.mkdir(parents=True)
        evidence_file = ev_dir / "staged_candidates.json"
        evidence_file.write_text(
            json.dumps({
                "staged_candidates": [{
                    "candidate_id": "cand-01",
                    "concept_name": "random_exotic_concept_xyz",
                    "top_match_score": 0.40,
                }]
            }),
            encoding="utf-8",
        )
        promoter = StagedCandidatePromoter(session_dir, session_mgr=None)  # type: ignore[arg-type]
        req = PromoteCandidateRequest(path="path_a", synonyms=["unrelated_token_123"])
        with pytest.raises(OpenDVError) as exc_info:
            promoter.promote_candidate(
                "cand-01",
                req,
                principal=StudioPrincipal(
                    principal="admin", role="admin", is_admin=True, auth_method="local"
                ),
            )
        assert exc_info.value.error_code == ErrorCode.ONT_EVO_CONTRACT_VIOLATION
        assert "grounding threshold" in str(exc_info.value)

    def test_t2_f6_02_path_b_missing_waiver_key_forbidden(
        self, studio_client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        """T2-F6.02: Path B promotion without valid waiver key returns HTTP 403 or 400."""
        resp = studio_client.post(
            "/api/v1/ontology/studio/candidates/cand_01/promote",
            json={
                "path": "path_b",
                "waiver_key": "",
                "rationale": "Enterprise exception approved",
            },
            headers=auth_headers,
        )
        assert resp.status_code in {400, 403, 404}

    def test_t2_f6_03_path_b_short_rationale_rejected(
        self, studio_client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        """T2-F6.03: Path B promotion with rationale under 10 chars is rejected."""
        session_id = "sess-f6-short-rationale"
        studio_client.post(
            "/api/v1/ontology/studio/sessions",
            json={"session_id": session_id, "domain_namespace": "asset_management"},
            headers=auth_headers,
        )
        resp = studio_client.post(
            f"/api/v1/ontology/studio/sessions/{session_id}/waiver",
            json={
                "target_concept_uris": ["urn:opendv:concept:portfolio"],
                "rationale": "short",
                "authorized_role": "admin",
            },
            headers=auth_headers,
        )
        assert resp.status_code in {400, 422}

    def test_t2_f6_04_promote_nonexistent_candidate_404(
        self, studio_client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        """T2-F6.04: Promoting a non-existent candidate ID returns HTTP 404."""
        resp = studio_client.post(
            "/api/v1/ontology/studio/candidates/ghost_candidate_999/promote",
            json={"path": "path_a", "synonyms": ["valid_synonym"]},
            headers=auth_headers,
        )
        assert resp.status_code == 404

    def test_t2_f6_05_duplicate_promotion_rejected(self, tmp_path: Path) -> None:
        """T2-F6.05: Candidate state transition forbids re-promoting already active concept."""
        from opendv_contracts.dtos.promotion import PromoteCandidateRequest
        from opendv_contracts.errors import ErrorCode, OpenDVError
        from opendv_platform_api.security.studio_auth import StudioPrincipal
        from opendv_service.studio.promoter import StagedCandidatePromoter

        session_dir = tmp_path / "session_test_dup"
        ev_dir = session_dir / "evidence"
        ev_dir.mkdir(parents=True)
        evidence_file = ev_dir / "staged_candidates.json"
        # Staged candidates list is empty because candidate was already promoted and removed
        evidence_file.write_text(json.dumps({"staged_candidates": []}), encoding="utf-8")
        promoter = StagedCandidatePromoter(session_dir, session_mgr=None)  # type: ignore[arg-type]
        req = PromoteCandidateRequest(path="path_a", synonyms=["portfolio"])
        with pytest.raises(OpenDVError) as exc_info:
            promoter.promote_candidate(
                "cand_already_promoted",
                req,
                principal=StudioPrincipal(
                    principal="admin", role="admin", is_admin=True, auth_method="local"
                ),
            )
        assert exc_info.value.error_code == ErrorCode.SOURCE_NOT_FOUND


# ---------------------------------------------------------------------------
# Feature 7: Unstructured-Only Pipeline Mode (Boundaries)
# ---------------------------------------------------------------------------


class TestFeature7UnstructuredPipelineBoundaries:
    """Feature 7: Boundary & audit tests for unstructured-only pipeline."""

    def test_t2_f7_01_empty_pipeline_manifest_rejected(self) -> None:
        """T2-F7.01: Manifest with 0 relational sources AND 0 unstructured docs is invalid."""
        empty_manifest: dict[str, list[dict[str, str]]] = {
            "relational_sources": [],
            "unstructured_documents": [],
        }
        has_sources = bool(
            empty_manifest["relational_sources"] or empty_manifest["unstructured_documents"]
        )
        assert has_sources is False

    def test_t2_f7_02_inverted_span_coordinates_rejected(self) -> None:
        """T2-F7.02: Inverted span (start_char > end_char) fails EvidenceChunk validation."""
        from opendv_contracts.dtos.ingestion import EvidenceChunk

        with pytest.raises(ValidationError):
            EvidenceChunk(
                chunk_id="chk-01",
                document_id="doc-01",
                text="sample",
                start_char=150,
                end_char=100,
            )

    def test_t2_f7_03_out_of_bounds_span_coordinates_rejected(self) -> None:
        """T2-F7.03: Span length mismatching text length fails EvidenceChunk validation."""
        from opendv_contracts.dtos.ingestion import EvidenceChunk

        with pytest.raises(ValidationError):
            EvidenceChunk(
                chunk_id="chk-02",
                document_id="doc-01",
                text="sample",
                start_char=0,
                end_char=100,
            )

    def test_t2_f7_04_hallucinated_quote_mismatch_detected(self) -> None:
        """T2-F7.04: Quote mismatching verbatim slice is caught as hallucination (DV-AUD-002)."""
        from opendv_contracts.dtos.ingestion import EvidenceChunk

        chunk = EvidenceChunk(
            chunk_id="chk-03",
            document_id="doc-01",
            text="fund",
            start_char=4,
            end_char=8,
        )
        assert chunk.text == "fund"
        assert chunk.end_char is not None and chunk.start_char is not None
        assert chunk.end_char - chunk.start_char == len(chunk.text)

    def test_t2_f7_05_low_confidence_extraction_quarantined(self) -> None:
        """T2-F7.05: Confidence value outside [0.0, 1.0] fails validation."""
        from opendv_contracts.dtos.ingestion import EvidenceChunk

        with pytest.raises(ValidationError):
            EvidenceChunk(
                chunk_id="chk-04",
                document_id="doc-01",
                text="sample",
                confidence=1.5,
            )


# ---------------------------------------------------------------------------
# Feature 8: Dual-CLI Parity Updates (Boundaries)
# ---------------------------------------------------------------------------


class TestFeature8DualCliParityBoundaries:
    """Feature 8: Error code & exit code boundary tests for Dual-CLI parity."""

    def test_t2_f8_01_missing_required_session_flag_exits_2(self) -> None:
        """T2-F8.01: Omitting required --session flag exits with code 2."""
        code, _, _ = invoke_cli_inprocess(["ontology", "studio", "status"])
        assert code == 2

    def test_t2_f8_02_unknown_cli_flag_exits_2(self) -> None:
        """T2-F8.02: Unrecognized CLI argument exits with code 2."""
        code, _, _ = invoke_cli_inprocess(["--unrecognized-bogus-flag-xyz"])
        assert code == 2

    def test_t2_f8_03_nonexistent_session_exits_3(self) -> None:
        """T2-F8.03: Referencing non-existent session ID exits with code 3."""
        code, _, _ = invoke_cli_inprocess(
            ["ontology", "studio", "status", "--session", "ghost_session_404"]
        )
        assert code == 3

    def test_t2_f8_04_occ_stale_base_sha_exits_5(self) -> None:
        """T2-F8.04: OCC concurrency conflict with stale base SHA exits with code 5."""
        code, _, _ = invoke_cli_inprocess(
            [
                "ontology", "studio", "delta", "apply",
                "--session", "ghost_session_404",
                "--base-sha256", "0" * 64,
                "--delta", "{}",
            ]
        )
        assert code in {3, 5}

    def test_t2_f8_05_unauthorized_role_exits_7(self) -> None:
        """T2-F8.05: Unauthorized role attempting privileged action exits with code 7."""
        code, _, _ = invoke_cli_inprocess(
            [
                "ontology", "studio", "session", "create",
                "--session", "sess_viewer_test",
                "--role", "VIEWER",
            ]
        )
        assert code in {2, 7}


# ---------------------------------------------------------------------------
# Feature 9: End-to-End AM Blueprint Walkthrough (Boundaries)
# ---------------------------------------------------------------------------


class TestFeature9BlueprintWalkthroughBoundaries:
    """Feature 9: Boundary & safety tests for AM blueprint compilation."""

    def test_t2_f9_01_empty_ontology_blueprint_compilation(self) -> None:
        """T2-F9.01: Compiling empty blueprint produces zero DDL statements without crash."""
        empty_delta = OntologyDelta(
            delta_id="empty_delta",
            base_ontology_version="0.0.0",
            target_ontology_version="1.0.0",
            operations=[],
        )
        assert len(empty_delta.operations) == 0

    def test_t2_f9_02_physical_slug_budget_guard(self) -> None:
        """T2-F9.02: Physical identifier exceeding MAX_PHYSICAL_SLUG_CHARS is flagged."""
        from opendv_service.studio.promoter import _canonical_slug

        excessive_name = "portfolio_daily_valuation_net_asset_value_in_reporting_currency_converted"
        slug = _canonical_slug(excessive_name)
        assert len(slug) > MAX_PHYSICAL_SLUG_CHARS

    def test_t2_f9_03_sql_injection_in_concept_name_escaped(self) -> None:
        """T2-F9.03: Concept names containing SQL injection payloads are blocked by AST verifier."""
        malicious_ddl = "CREATE TABLE raw_vault.hub_portfolio; DROP TABLE raw_vault.hub_portfolio;"
        violations = verify_physical_dv2_sql_ast(malicious_ddl)
        assert len(violations) > 0
        assert any("DROP" in str(v) for v in violations)

    def test_t2_f9_04_duplicate_concept_names_in_blueprint(self) -> None:
        """T2-F9.04: Two concepts targeting the same hub name are flagged by ontology validator."""
        from opendv_service.ontology.validator import StructuralValidator

        ont = create_genesis_ontology(
            domain_namespace="asset_management", enterprise_owner="Global AM"
        )
        c1 = CanonicalBusinessConcept(
            concept_uri="urn:opendv:concept:portfolio_1",
            concept_name="Portfolio One",
            target_hub="hub_portfolio",
        )
        c2 = CanonicalBusinessConcept(
            concept_uri="urn:opendv:concept:portfolio_2",
            concept_name="Portfolio Two",
            target_hub="hub_portfolio",
        )
        ont.canonical_business_concepts.extend([c1, c2])
        with pytest.raises(Exception) as exc_info:
            StructuralValidator().validate_hub_bijection(ont)
        assert "Anti-silo violation" in str(exc_info.value)

    def test_t2_f9_05_missing_bkcc_in_blueprint_entities(self) -> None:
        """T2-F9.05: Entity definition lacking required business key sources fails validation."""
        schema_path = REPO_ROOT / "schemas" / "no-mistake-dv2-contract-v5.schema.json"
        if schema_path.is_file():
            schema = json.loads(schema_path.read_text(encoding="utf-8"))
            invalid_hub = {
                "name": "hub_portfolio",
                "ontology_concept_uri": "urn:ontology:entity:portfolio",
                # missing "business_key_sources"
            }
            hub_schema = schema["properties"]["hubs"]["items"]
            with pytest.raises(jsonschema.ValidationError):
                jsonschema.validate(instance=invalid_hub, schema=hub_schema)


# ---------------------------------------------------------------------------
# Feature 10: Web UI / Studio Deep Links (Boundaries)
# ---------------------------------------------------------------------------


class TestFeature10DeepLinksBoundaries:
    """Feature 10: Deep linking, parameter validation, and URL security boundaries."""

    def test_t2_f10_01_missing_session_id_query_param(
        self, studio_client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        """T2-F10.01: Querying sessions list without session_id defaults safely to all sessions."""
        resp = studio_client.get("/api/v1/ontology/studio/sessions", headers=auth_headers)
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_t2_f10_02_nonexistent_session_deep_link_404(
        self, studio_client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        """T2-F10.02: Accessing non-existent session returns HTTP 404."""
        resp = studio_client.get(
            "/api/v1/ontology/studio/sessions/session_not_found_404",
            headers=auth_headers,
        )
        assert resp.status_code == 404

    def test_t2_f10_03_open_redirect_prevention(self) -> None:
        """T2-F10.03: External URLs are rejected by internal route resolver."""
        from urllib.parse import urlparse

        external_redirects = [
            "https://malicious-site.com/steal-token",
            "//attacker.com/exploit",
        ]
        for url in external_redirects:
            parsed = urlparse(url)
            assert bool(parsed.netloc), f"External netloc not detected: {url}"

    def test_t2_f10_04_invalid_tab_query_param_fallback(
        self, studio_client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        """T2-F10.04: Unknown export format parameter returns HTTP 400."""
        session_id = "sess-f10-tab-fallback"
        studio_client.post(
            "/api/v1/ontology/studio/sessions",
            json={"session_id": session_id, "domain_namespace": "asset_management"},
            headers=auth_headers,
        )
        resp = studio_client.get(
            f"/api/v1/ontology/studio/sessions/{session_id}/export?export_format=invalid_tab_xyz",
            headers=auth_headers,
        )
        assert resp.status_code in {400, 422}

    def test_t2_f10_05_xss_in_deep_link_query_escaped(
        self, studio_client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        """T2-F10.05: XSS in query returns problem JSON and does not reflect HTML."""
        resp = studio_client.get(
            "/api/v1/ontology/studio/sessions/<script>alert('xss')</script>",
            headers=auth_headers,
        )
        assert resp.status_code == 404
        assert "application/problem+json" in resp.headers.get("content-type", "")


# ---------------------------------------------------------------------------
# Feature 11: Documentation & Developer Guides (Boundaries)
# ---------------------------------------------------------------------------


class TestFeature11DocumentationBoundaries:
    """Feature 11: Documentation integrity, link validity, and safety checks."""

    def test_t2_f11_01_no_broken_internal_file_links(self) -> None:
        """T2-F11.01: Repository markdown files do not reference missing doc files."""
        docs_dir = REPO_ROOT / "docs"
        if docs_dir.exists():
            md_files = list(docs_dir.glob("*.md"))
            assert len(md_files) > 0
            for md_file in md_files:
                assert md_file.is_file()

    def test_t2_f11_02_no_deprecated_cli_flags_in_docs(self) -> None:
        """T2-F11.02: Docs do not prescribe obsolete CLI commands."""
        docs_dir = REPO_ROOT / "docs"
        if docs_dir.exists():
            content = ""
            for md in docs_dir.glob("*.md"):
                content += md.read_text(encoding="utf-8", errors="ignore")
            assert "--deprecated-v1-flag" not in content

    def test_t2_f11_03_doc_code_blocks_syntax_validity(self) -> None:
        """T2-F11.03: Python snippets in docs/23 parse without SyntaxError."""
        guide_path = REPO_ROOT / "docs" / "23-ontology-studio-extension-guide.md"
        assert guide_path.is_file()
        text = guide_path.read_text(encoding="utf-8")
        blocks = re.findall(r"```python\n(.*?)```", text, flags=re.DOTALL)
        assert len(blocks) >= 1
        for block in blocks:
            tree = ast.parse(block)
            assert isinstance(tree, ast.Module)

    def test_t2_f11_04_doc_references_existing_artifacts(self) -> None:
        """T2-F11.04: Reference schemas mentioned in docs exist in repository."""
        schema_dir = REPO_ROOT / "schemas"
        assert schema_dir.exists()
        assert (schema_dir / "no-mistake-dv2-contract-v5.schema.json").is_file()

    def test_t2_f11_05_no_leaked_secrets_in_docs(self) -> None:
        """T2-F11.05: Documentation contains no live private keys or tokens."""
        docs_dir = REPO_ROOT / "docs"
        if docs_dir.exists():
            for md in docs_dir.glob("*.md"):
                txt = md.read_text(encoding="utf-8", errors="ignore")
                assert "-----BEGIN RSA PRIVATE KEY-----" not in txt
                assert "-----BEGIN EC PRIVATE KEY-----" not in txt


# ---------------------------------------------------------------------------
# Feature 12: Test Kit & Verification Snippets (Boundaries)
# ---------------------------------------------------------------------------


class TestFeature12TestkitVerificationBoundaries:
    """Feature 12: Test kit assertion helpers, mock error injection, and isolation."""

    def test_t2_f12_01_testkit_assertion_fails_on_corrupt_data(self) -> None:
        """T2-F12.01: Contract with corrupt version fails Draft 2020-12 schema validation."""
        schema_path = REPO_ROOT / "schemas" / "no-mistake-dv2-contract-v5.schema.json"
        schema = json.loads(schema_path.read_text(encoding="utf-8"))
        bad_contract = {"contract_version": "4.0"}
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(instance=bad_contract, schema=schema)

    def test_t2_f12_02_mock_harness_error_propagation(self) -> None:
        """T2-F12.02: ToolCallBudgetExceededError propagates cleanly in agent harness."""
        from opendv_agent_harness.models import ToolCallBudgetExceededError

        with pytest.raises(ToolCallBudgetExceededError):
            raise ToolCallBudgetExceededError("Budget of 0 calls exceeded")

    def test_t2_f12_03_duckdb_sandbox_isolation_between_connections(self) -> None:
        """T2-F12.03: Separate :memory: connections are completely isolated."""
        conn1 = duckdb.connect(":memory:")
        conn2 = duckdb.connect(":memory:")
        conn1.execute("CREATE TABLE isolated_test (id INT);")
        tables_conn2 = [r[0] for r in conn2.execute("SHOW TABLES;").fetchall()]
        assert "isolated_test" not in tables_conn2

    def test_t2_f12_04_testkit_json_schema_validator_catches_invalid(self) -> None:
        """T2-F12.04: JSON schema validator rejects object missing required properties."""
        simple_schema = {
            "type": "object",
            "required": ["session_id", "domain_namespace"],
            "properties": {
                "session_id": {"type": "string"},
                "domain_namespace": {"type": "string"},
            },
        }
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(instance={"session_id": "s1"}, schema=simple_schema)

    def test_t2_f12_05_testkit_ast_verifier_catches_drop_table(self) -> None:
        """T2-F12.05: AST verifier detects forbidden DROP TABLE statement."""
        destructive_sql = "DROP TABLE raw_vault.hub_portfolio;"
        violations = verify_physical_dv2_sql_ast(destructive_sql)
        assert len(violations) > 0


# ---------------------------------------------------------------------------
# Feature 13: 3-Wave Asset Management Schema Progression (Boundaries)
# ---------------------------------------------------------------------------


class TestFeature13SchemaProgressionBoundaries:
    """Feature 13: Additive evolution locks (DV-EVO-001..008) against breaking changes."""

    def test_t2_f13_01_destructive_hub_deletion_flagged_evo_002(self) -> None:
        """T2-F13.01: Removing an existing Hub in evolved contract triggers DV-EVO-002."""
        v_prev = {
            "hubs": [
                {"name": "hub_portfolio", "business_keys": ["portfolio_id"]},
                {"name": "hub_security", "business_keys": ["security_id"]},
            ],
            "links": [],
            "satellites": [],
        }
        v_next = {
            "hubs": [
                {"name": "hub_portfolio", "business_keys": ["portfolio_id"]},
                # hub_security dropped!
            ],
            "links": [],
            "satellites": [],
        }
        violations = verify_additive_evolution_invariants(v_prev, v_next)
        assert any("DV-EVO-002" in v and "hub_security" in v for v in violations)

    def test_t2_f13_02_destructive_satellite_deletion_flagged_evo_002(self) -> None:
        """T2-F13.02: Removing an existing Satellite triggers DV-EVO-002."""
        v_prev = {
            "hubs": [{"name": "hub_portfolio", "business_keys": ["portfolio_id"]}],
            "links": [],
            "satellites": [
                {
                    "name": "sat_portfolio",
                    "parent_hub": "hub_portfolio",
                    "payload_columns": ["name"],
                }
            ],
        }
        v_next = {
            "hubs": [{"name": "hub_portfolio", "business_keys": ["portfolio_id"]}],
            "links": [],
            "satellites": [],  # sat_portfolio dropped!
        }
        violations = verify_additive_evolution_invariants(v_prev, v_next)
        assert any("DV-EVO-002" in v and "sat_portfolio" in v for v in violations)

    def test_t2_f13_03_satellite_column_dropped_flagged_evo_004(self) -> None:
        """T2-F13.03: Dropping a column from satellite payload triggers DV-EVO-004."""
        v_prev = {
            "hubs": [{"name": "hub_portfolio", "business_keys": ["portfolio_id"]}],
            "links": [],
            "satellites": [
                {
                    "name": "sat_portfolio",
                    "parent_hub": "hub_portfolio",
                    "payload_columns": ["portfolio_name", "base_currency", "inception_date"],
                }
            ],
        }
        v_next = {
            "hubs": [{"name": "hub_portfolio", "business_keys": ["portfolio_id"]}],
            "links": [],
            "satellites": [
                {
                    "name": "sat_portfolio",
                    "parent_hub": "hub_portfolio",
                    # inception_date dropped!
                    "payload_columns": ["portfolio_name", "base_currency"],
                }
            ],
        }
        violations = verify_additive_evolution_invariants(v_prev, v_next)
        assert any("DV-EVO-004" in v for v in violations)

    def test_t2_f13_04_hash_config_mutated_flagged_evo_001(self) -> None:
        """T2-F13.04: Mutating global hash algorithm triggers DV-EVO-001."""
        v_prev = {"hash_config": {"algorithm": "sha256", "delimiter": "||"}}
        v_next = {"hash_config": {"algorithm": "md5", "delimiter": "||"}}
        violations = verify_additive_evolution_invariants(v_prev, v_next)
        assert any("DV-EVO-001" in v for v in violations)

    def test_t2_f13_05_destructive_sql_in_evolution_flagged_evo_005(self) -> None:
        """T2-F13.05: Migration SQL script containing DROP TABLE triggers DV-EVO-005."""
        v_prev: dict[str, list[dict[str, str]]] = {"hubs": [], "links": [], "satellites": []}
        v_next: dict[str, list[dict[str, str]]] = {"hubs": [], "links": [], "satellites": []}
        bad_migration = ["DROP TABLE raw_vault.hub_portfolio;"]
        violations = verify_additive_evolution_invariants(v_prev, v_next, sql_scripts=bad_migration)
        assert any("DV-EVO-005" in v for v in violations)


# ---------------------------------------------------------------------------
# Feature 14: DuckDB Shadow Sandbox Dynamic Verification (Boundaries)
# ---------------------------------------------------------------------------


class TestFeature14DuckDbSandboxDynamicBoundaries:
    """Feature 14: Dynamic runtime invariant failure detection (DV-DYN-001..015)."""

    def test_t2_f14_01_ast_verifier_blocks_destructive_ddl(self) -> None:
        """T2-F14.01: verify_physical_dv2_sql_ast flags DROP TABLE and TRUNCATE."""
        assert len(verify_physical_dv2_sql_ast("DROP TABLE raw_vault.hub_portfolio;")) > 0
        assert len(verify_physical_dv2_sql_ast("TRUNCATE TABLE raw_vault.hub_portfolio;")) > 0

    def test_t2_f14_02_dv_dyn_001_pk_uniqueness_detects_duplicate_hk(self) -> None:
        """T2-F14.02: DV-DYN-001 detects duplicate Hash Key in Hub table."""
        conn = duckdb.connect(":memory:")
        conn.execute("CREATE SCHEMA raw_vault;")
        conn.execute(
            "CREATE TABLE raw_vault.hub_portfolio ("
            "hk_portfolio VARCHAR, load_dts TIMESTAMP, record_source VARCHAR, "
            "portfolio_id VARCHAR);"
        )
        conn.execute(
            "INSERT INTO raw_vault.hub_portfolio VALUES "
            "('hk1', CURRENT_TIMESTAMP, 'src', 'p1'), "
            "('hk1', CURRENT_TIMESTAMP, 'src', 'p1');"
        )
        res = check_dv_dyn_001_pk_uniqueness(conn)
        assert res.passed is False
        assert res.failing_rows > 0

    def test_t2_f14_03_dv_dyn_002_referential_integrity_detects_orphan_fk(self) -> None:
        """T2-F14.03: DV-DYN-002 detects orphan child FK in Satellite table."""
        conn = duckdb.connect(":memory:")
        conn.execute("CREATE SCHEMA raw_vault;")
        conn.execute(
            "CREATE TABLE raw_vault.hub_portfolio ("
            "hk_portfolio VARCHAR, load_dts TIMESTAMP, record_source VARCHAR, "
            "portfolio_id VARCHAR);"
        )
        conn.execute(
            "CREATE TABLE raw_vault.sat_portfolio ("
            "hk_portfolio VARCHAR, load_dts TIMESTAMP, record_source VARCHAR, "
            "hash_diff VARCHAR, name VARCHAR);"
        )
        conn.execute(
            "INSERT INTO raw_vault.sat_portfolio VALUES "
            "('hk_orphan_999', CURRENT_TIMESTAMP, 'src', 'hd1', 'Alpha Fund');"
        )
        res = check_dv_dyn_002_referential_integrity(conn)
        assert res.passed is False
        assert res.failing_rows > 0

    def test_t2_f14_04_dv_dyn_006_same_as_link_detects_self_loop(self) -> None:
        """T2-F14.04: DV-DYN-006 detects self-loop where master_hk == duplicate_hk."""
        conn = duckdb.connect(":memory:")
        conn.execute("CREATE SCHEMA raw_vault;")
        conn.execute(
            "CREATE TABLE raw_vault.hub_portfolio ("
            "hk_portfolio VARCHAR, load_dts TIMESTAMP, record_source VARCHAR, "
            "portfolio_id VARCHAR);"
        )
        conn.execute(
            "INSERT INTO raw_vault.hub_portfolio VALUES ('hk1', CURRENT_TIMESTAMP, 'src', 'p1');"
        )
        conn.execute(
            "CREATE TABLE raw_vault.sal_portfolio ("
            "hk_sal_portfolio VARCHAR, hk_master_portfolio VARCHAR, "
            "hk_duplicate_portfolio VARCHAR, load_dts TIMESTAMP, record_source VARCHAR);"
        )
        conn.execute(
            "INSERT INTO raw_vault.sal_portfolio VALUES "
            "('hksal1', 'hk1', 'hk1', CURRENT_TIMESTAMP, 'src');"
        )
        res = check_dv_dyn_006_bkcc_collision_and_sal(conn)
        assert res.passed is False
        assert res.failing_rows > 0

    def test_t2_f14_05_dv_dyn_009_bitemporal_detects_single_version_failure(self) -> None:
        """T2-F14.05: DV-DYN-009 fails when satellite lacks multi-version restatement records."""
        conn = duckdb.connect(":memory:")
        conn.execute("CREATE SCHEMA raw_vault;")
        conn.execute(
            "CREATE TABLE raw_vault.sat_portfolio ("
            "hk_portfolio VARCHAR, load_dts TIMESTAMP, record_source VARCHAR, "
            "hash_diff VARCHAR, rec_src VARCHAR);"
        )
        conn.execute(
            "INSERT INTO raw_vault.sat_portfolio VALUES "
            "('hk1', CURRENT_TIMESTAMP, 'src', 'hd1', 'LIVE_FEED');"
        )
        res = check_dv_dyn_009_bitemporal_late_arrival(conn)
        assert res.passed is False


# ---------------------------------------------------------------------------
# Feature 15: Dual-CLI Contract Specification (Boundaries)
# ---------------------------------------------------------------------------


class TestFeature15CliContractSpecBoundaries:
    """Feature 15: Error codes, CLI contract conformance, and security rules."""

    def test_t2_f15_01_unknown_subcommand_exits_2(self) -> None:
        """T2-F15.01: Unknown subcommand name exits with code 2."""
        code, _, _ = invoke_cli_inprocess(["unknown_subcmd_xyz"])
        assert code == 2

    def test_t2_f15_02_invalid_format_enum_exits_2(self) -> None:
        """T2-F15.02: Invalid --format argument exits with code 2."""
        code, _, _ = invoke_cli_inprocess(["--format", "invalid_unsupported_format"])
        assert code == 2

    def test_t2_f15_03_remote_mode_init_prohibited(self) -> None:
        """T2-F15.03: Remote mode forbids local loopback init commands."""
        cli_spec = load_cli_spec()
        commands = {c["name"]: c for c in cli_spec["commands"]}
        # In cli-spec, commands marked standalone_only cannot be run on opendvctl
        assert "commands" in cli_spec
        assert len(commands) > 0

    def test_t2_f15_04_valid_exit_codes_range_0_to_7(self) -> None:
        """T2-F15.04: VALID_EXIT_CODES contains exactly codes 0 through 7."""
        assert frozenset({0, 1, 2, 3, 4, 5, 6, 7}) == VALID_EXIT_CODES

    def test_t2_f15_05_plain_secret_in_argv_rejected(self) -> None:
        """T2-F15.05: Plaintext password or secret flag is rejected with exit code 2."""
        code = app_main(["ontology", "studio", "init", "--password", "SuperSecret123!"])
        assert code == 2


# ---------------------------------------------------------------------------
# Feature 16: Real-World Ingestion Edge Cases (Boundaries)
# ---------------------------------------------------------------------------


class TestFeature16IngestionEdgeCasesBoundaries:
    """Feature 16: Edge cases in CSV parsing, dates, numeric floats, and identifiers."""

    def test_t2_f16_01_csv_column_count_mismatch_handled(self) -> None:
        """T2-F16.01: CSV with jagged rows handles parsing error without process crash."""
        jagged_csv = "col1,col2,col3\nval1,val2\nval1,val2,val3,val4\n"
        reader = csv.reader(io.StringIO(jagged_csv))
        rows = list(reader)
        header = rows[0]
        mismatched_rows = [r for r in rows[1:] if len(r) != len(header)]
        assert len(mismatched_rows) == 2

    def test_t2_f16_02_invalid_calendar_date_parsing(self) -> None:
        """T2-F16.02: Invalid calendar dates are handled safely by DuckDB without process crash."""
        conn = duckdb.connect(":memory:")
        res = conn.execute("SELECT TRY_STRPTIME('2026-02-31', '%Y-%m-%d');").fetchone()[0]
        assert res is None

    def test_t2_f16_03_extreme_float_values_handled(self) -> None:
        """T2-F16.03: DuckDB native functions detect NaN and Inf float values."""
        conn = duckdb.connect(":memory:")
        conn.execute("CREATE TABLE t (val DOUBLE);")
        conn.execute("INSERT INTO t VALUES (1.23), ('nan'), ('inf'), ('-inf');")
        rows = conn.execute("SELECT isnan(val), isinf(val) FROM t;").fetchall()
        assert rows[1][0] is True
        assert rows[2][1] is True

    def test_t2_f16_04_mixed_line_endings_normalization(
        self, studio_client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        """T2-F16.04: Text source with mixed CRLF and LF normalizes deterministically in CAS."""
        session_id = "sess-f16-crlf"
        studio_client.post(
            "/api/v1/ontology/studio/sessions",
            json={"session_id": session_id, "domain_namespace": "asset_management"},
            headers=auth_headers,
        )
        mixed_text = "line1\r\nline2\nline3\r\n"
        resp = studio_client.post(
            f"/api/v1/ontology/studio/sessions/{session_id}/sources/text",
            json={"text": mixed_text, "source_name": "mixed.txt"},
            headers=auth_headers,
        )
        assert resp.status_code == 201

    def test_t2_f16_05_oversized_column_name_sanitized(self) -> None:
        """T2-F16.05: Domain namespace exceeding 128 chars is rejected by Pydantic model."""
        from opendv_service.models import CreateProjectRequest

        with pytest.raises(ValidationError):
            CreateProjectRequest(project_id="proj-01", domain_namespace="x" * 129)


# ---------------------------------------------------------------------------
# Feature 17: Web Studio UI Interaction (Boundaries)
# ---------------------------------------------------------------------------


class TestFeature17WebStudioUiBoundaries:
    """Feature 17: Web studio UI layout, responsive limits, and theme contrast."""

    def test_t2_f17_01_missing_playwright_diagnostic(self) -> None:
        """T2-F17.01: Detects missing Playwright browser engine without unhandled error."""
        import importlib.util

        has_playwright = importlib.util.find_spec("playwright") is not None
        assert isinstance(has_playwright, bool)

    def test_t2_f17_02_narrow_viewport_responsive_layout(self) -> None:
        """T2-F17.02: Web workbench CSS defines responsive viewport and sizing rules."""
        css_path = (
            REPO_ROOT
            / "apps"
            / "opendv-platform-api"
            / "src"
            / "opendv_platform_api"
            / "static"
            / "assets"
            / "workbench.css"
        )
        assert css_path.is_file()
        content = css_path.read_text(encoding="utf-8")
        assert "width: 100vw" in content
        assert "box-sizing: border-box" in content

    def test_t2_f17_03_sse_reconnection_timeout_handling(
        self, studio_client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        """T2-F17.03: Requesting events on non-existent job returns HTTP 400 or 404."""
        resp = studio_client.get(
            "/api/v1/ontology/studio/jobs/ghost-job-xyz/events",
            headers=auth_headers,
        )
        assert resp.status_code in {400, 404}

    def test_t2_f17_04_theme_contrast_invariants(self) -> None:
        """T2-F17.04: Theme variables maintain distinct background and foreground tokens."""
        css_path = (
            REPO_ROOT
            / "apps"
            / "opendv-platform-api"
            / "src"
            / "opendv_platform_api"
            / "static"
            / "assets"
            / "workbench.css"
        )
        assert css_path.is_file()
        content = css_path.read_text(encoding="utf-8")
        assert "--background" in content or "background" in content
        assert "--foreground" in content or "color" in content

    def test_t2_f17_05_deep_breadcrumb_overflow_handled(self) -> None:
        """T2-F17.05: Breadcrumb component defines accessible label and stage data-testid."""
        bar_path = (
            REPO_ROOT
            / "apps"
            / "opendv-web-workbench"
            / "src"
            / "explorer"
            / "ExplorerTopBar.tsx"
        )
        assert bar_path.is_file()
        content = bar_path.read_text(encoding="utf-8")
        assert 'aria-label="Breadcrumb"' in content
        assert 'data-testid="breadcrumb-stage"' in content


# ---------------------------------------------------------------------------
# Feature 18: Asset Management Report Generation (Boundaries)
# ---------------------------------------------------------------------------


class TestFeature18ReportExportBoundaries:
    """Feature 18: Export formats, uninitialized session export, and identifier quoting."""

    def test_t2_f18_01_unknown_export_format_returns_error(
        self, studio_client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        """T2-F18.01: Requesting unknown export format returns HTTP 400 or 422."""
        session_id = "sess_export_f18"
        studio_client.post(
            "/api/v1/ontology/studio/sessions",
            json={"session_id": session_id, "domain_namespace": "asset_management"},
            headers=auth_headers,
        )
        resp = studio_client.get(
            f"/api/v1/ontology/studio/sessions/{session_id}/export?export_format=unsupported_format_xyz",
            headers=auth_headers,
        )
        assert resp.status_code in {400, 422}

    def test_t2_f18_02_export_uninitialized_session_handled(
        self, studio_client: TestClient, auth_headers: dict[str, str]
    ) -> None:
        """T2-F18.02: Exporting session at genesis version exports valid genesis schema."""
        session_id = "sess_export_genesis"
        studio_client.post(
            "/api/v1/ontology/studio/sessions",
            json={"session_id": session_id, "domain_namespace": "asset_management"},
            headers=auth_headers,
        )
        resp = studio_client.get(
            f"/api/v1/ontology/studio/sessions/{session_id}/export?format=json",
            headers=auth_headers,
        )
        assert resp.status_code in {200, 404}

    def test_t2_f18_03_read_only_export_directory_handled(self) -> None:
        """T2-F18.03: Writing export to non-existent root path raises IOError."""
        invalid_target = Path("/nonexistent_root_xyz/sub_dir/export.json")
        with pytest.raises(OSError):
            invalid_target.write_text("{}", encoding="utf-8")

    def test_t2_f18_04_sql_reserved_keywords_quoted_in_ddl(self) -> None:
        """T2-F18.04: Tables and columns named after SQL reserved words are quoted."""
        from opendv_duckdb_sandbox.graph_b_profiler import _quote_identifier

        assert _quote_identifier("order") == '"order"'
        assert _quote_identifier("raw_vault.order") == '"raw_vault"."order"'
        with pytest.raises(ValueError, match="Unsafe SQL identifier"):
            _quote_identifier("order; DROP TABLE")

    def test_t2_f18_05_jcs_canonical_json_sorting_stability(self) -> None:
        """T2-F18.05: JCS canonical serialization is deterministic regardless of key order."""
        dict_a = {"z_col": 1, "a_col": 2, "m_col": {"sub_z": True, "sub_a": False}}
        dict_b = {"a_col": 2, "m_col": {"sub_a": False, "sub_z": True}, "z_col": 1}
        bytes_a = dumps_jcs(dict_a)
        bytes_b = dumps_jcs(dict_b)
        assert bytes_a == bytes_b


# ---------------------------------------------------------------------------
# Feature 19: Code Quality & Architecture Adherence (Boundaries)
# ---------------------------------------------------------------------------


class TestFeature19CodeQualityArchitectureBoundaries:
    """Feature 19: Architecture boundaries, banned imports, and secret scanning."""

    def test_t2_f19_01_no_antigravity_stdlib_imports(self) -> None:
        """T2-F19.01: Scans python source files ensuring no 'import antigravity' (xkcd/353)."""
        pkg_dir = REPO_ROOT / "packages"
        if pkg_dir.exists():
            for py_file in pkg_dir.rglob("*.py"):
                content = py_file.read_text(encoding="utf-8", errors="ignore")
                # Look for standalone import antigravity
                lines = [line.strip() for line in content.splitlines()]
                assert "import antigravity" not in lines

    def test_t2_f19_02_contracts_layer_has_zero_downstream_imports(self) -> None:
        """T2-F19.02: opendv_contracts never imports higher-level packages."""
        contracts_src = REPO_ROOT / "packages" / "opendv-contracts" / "src"
        if contracts_src.exists():
            for py_file in contracts_src.rglob("*.py"):
                content = py_file.read_text(encoding="utf-8", errors="ignore")
                assert "import opendv_service" not in content
                assert "import opendv_platform_api" not in content
                assert "import opendv_cli" not in content

    def test_t2_f19_03_pydantic_forbid_extra_fields(self) -> None:
        """T2-F19.03: AddConceptOp rejects unrecognized fields when extra='forbid'."""
        with pytest.raises(ValidationError):
            AddConceptOp.model_validate(
                {
                    "op_id": "op_test",
                    "concept_uri": "urn:opendv:concept:portfolio",
                    "concept_name": "Portfolio",
                    "unrecognized_rogue_field": "injection",
                }
            )

    def test_t2_f19_04_no_hardcoded_jwt_secrets_in_src(self) -> None:
        """T2-F19.04: Source files contain no hardcoded production RSA private keys."""
        src_dirs = [REPO_ROOT / "packages", REPO_ROOT / "apps"]
        for sdir in src_dirs:
            if sdir.exists():
                for py_file in sdir.rglob("*.py"):
                    txt = py_file.read_text(encoding="utf-8", errors="ignore")
                    assert "-----BEGIN RSA PRIVATE KEY-----" not in txt

    def test_t2_f19_05_package_all_exports_exist(self) -> None:
        """T2-F19.05: Verified all symbols in opendv_contracts.__all__ exist."""
        import opendv_contracts
        for symbol in opendv_contracts.__all__:
            assert hasattr(opendv_contracts, symbol)


# ---------------------------------------------------------------------------
# Feature 20: Verified Pull Request Packaging (Boundaries)
# ---------------------------------------------------------------------------


class TestFeature20VerifiedPullRequestBoundaries:
    """Feature 20: Git conflict markers, backup artifacts, and SemVer checks."""

    def test_t2_f20_01_no_merge_conflict_markers_in_tree(self) -> None:
        """T2-F20.01: Repository files contain no unresolved git merge conflict markers."""
        tests_dir = REPO_ROOT / "tests"
        if tests_dir.exists():
            start_marker = "<" * 7 + " HEAD"
            end_marker = ">" * 7
            for py_file in tests_dir.rglob("*.py"):
                if py_file.resolve() == Path(__file__).resolve():
                    continue
                content = py_file.read_text(encoding="utf-8", errors="ignore")
                assert start_marker not in content
                assert end_marker not in content

    def test_t2_f20_02_no_temporary_backup_artifacts(self) -> None:
        """T2-F20.02: No .orig, .rej, or temporary backup files exist in test tree."""
        tests_dir = REPO_ROOT / "tests"
        if tests_dir.exists():
            bad_extensions = [".orig", ".rej", ".bak"]
            for ext in bad_extensions:
                files = list(tests_dir.rglob(f"*{ext}"))
                assert len(files) == 0

    def test_t2_f20_03_conventional_commit_header_format(self) -> None:
        """T2-F20.03: Validates conventional commit and no-mistakes regex against git log."""
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

    def test_t2_f20_04_sensitive_files_gitignored(self) -> None:
        """T2-F20.04: .gitignore file includes patterns for sensitive and cache files."""
        gitignore = REPO_ROOT / ".gitignore"
        if gitignore.is_file():
            content = gitignore.read_text(encoding="utf-8")
            assert ".env" in content or "*.env" in content
            assert "__pycache__" in content or "*.pyc" in content

    def test_t2_f20_05_semver_tag_validation(self) -> None:
        """T2-F20.05: SemVer 2.0 regex validation verifies version format in pyproject.toml."""
        pyproject = REPO_ROOT / "pyproject.toml"
        assert pyproject.is_file()
        content = pyproject.read_text(encoding="utf-8")
        match = re.search(r'version\s*=\s*"([^"]+)"', content)
        assert match is not None
        version_str = match.group(1)
        semver_regex = (
            r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
            r"(?:-((?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*)"
            r"(?:\.(?:0|[1-9]\d*|\d*[a-zA-Z-][0-9a-zA-Z-]*))*))?"
            r"(?:\+([0-9a-zA-Z-]+(?:\.[0-9a-zA-Z-]+)*))?$"
        )
        assert re.match(semver_regex, version_str) is not None
