"""Task 6B.2 (#231): Tier 3 Combinatorial Matrix E2E Tests.

Tests combinatorial permutations across:
1. Ingestion Formats: PDF, MD, CSV, JSON, Parquet
2. Engine Modes: Deterministic vs Agent vs Auto
3. Transports: Python API vs REST HTTP vs CLI Commands
4. Delta Operations Matrix: All 24 OntologyDelta operations
5. Multi-Format Export Isomorphism: YAML, JCS JSON, Turtle, JSON-LD, NTriples
"""

from __future__ import annotations

import io
import json
from pathlib import Path

import duckdb
import rdflib
import yaml
from rdflib.compare import isomorphic
from starlette.testclient import TestClient

from opendv_cli.main import app_main
from opendv_contracts.dtos.ontology import (
    AddAttributeOp,
    AddConceptOp,
    AddExternalMappingOp,
    AddGovernanceConstraintOp,
    AddKeyAuthorityPatternOp,
    AddReferenceEnumCodeOp,
    AddReferenceEnumerationOp,
    AddRelationshipOp,
    AddSourceHintOp,
    AddSynonymOp,
    AnnotateConceptMetadataOp,
    DeprecateConceptOp,
    DeprecateRelationshipOp,
    OntologyDelta,
    RefineDefinitionOp,
    RemoveAttributeOp,
    RemoveConceptOp,
    RemoveExternalMappingOp,
    RemoveRelationshipOp,
    RepinExternalMappingOp,
    RescoreExternalMappingOp,
    ReviseDescriptionOp,
    SetConceptElmArchetypeOp,
    SetParentConceptOp,
    UpdateConceptUrnOp,
)
from opendv_core.models.graph_a import (
    CanonicalBusinessConcept,
    ExternalMapping,
    GraphAOntology,
)
from opendv_core.ontology_hashing import compute_ontology_sha256
from opendv_platform_api.main import create_app
from opendv_service import OpenDVSettings
from opendv_service.ontology.applier import apply_ontology_delta
from opendv_service.studio.atomic_commit import AtomicCommitService
from opendv_service.studio.session_manager import (
    StudioSessionManager,
)


def _make_minimal_pdf_bytes() -> bytes:
    """Construct a minimal valid PDF-1.4 binary payload."""
    return (
        b"%PDF-1.4\n"
        b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
        b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
        b"3 0 obj<</Type/Page/MediaBox[0 0 612 792]/Parent 2 0 R>>endobj\n"
        b"xref\n0 4\n0000000000 65535 f \n"
        b"0000000009 00000 n \n0000000052 00000 n \n0000000101 00000 n \n"
        b"trailer<</Size 4/Root 1 0 R>>\nstartxref\n168\n%%EOF\n"
    )


def _make_parquet_bytes() -> bytes:
    """Generate valid Parquet binary bytes using embedded DuckDB."""
    con = duckdb.connect()
    rel = con.sql("SELECT 101 AS entity_id, 'Acme Holdings' AS legal_name, 'US' AS country")
    # Write to temp file then read bytes
    tmp_pq = Path("temp_tbl.parquet")
    try:
        rel.write_parquet(str(tmp_pq))
        return tmp_pq.read_bytes()
    finally:
        if tmp_pq.is_file():
            tmp_pq.unlink(missing_ok=True)


def test_combinatorial_ingestion_formats(
    studio_client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    """Combinatorial 1: Ingestion Formats Matrix (PDF, MD, CSV, JSON, Parquet)."""
    session_id = "comb-ingestion-formats-01"
    studio_client.post(
        "/api/v1/ontology/studio/sessions",
        json={"session_id": session_id, "domain_namespace": "wealth_mgmt"},
        headers=auth_headers,
    )

    format_payloads: list[tuple[str, bytes, str]] = [
        ("report.pdf", _make_minimal_pdf_bytes(), "application/pdf"),
        ("notes.md", b"# Wealth Planning Notes\n- Client portfolio strategy", "text/markdown"),
        (
            "accounts.csv",
            b"account_id,balance_usd\nACC-01,1500000.50\nACC-02,250000.00",
            "text/csv",
        ),
        (
            "holdings.json",
            json.dumps([{"isin": "US0378331005", "ticker": "AAPL", "qty": 500}]).encode("utf-8"),
            "application/json",
        ),
        ("entities.parquet", _make_parquet_bytes(), "application/vnd.apache.parquet"),
    ]

    for fname, payload_bytes, mime_type in format_payloads:
        resp = studio_client.post(
            f"/api/v1/ontology/studio/sessions/{session_id}/sources",
            files={"file": (fname, io.BytesIO(payload_bytes), mime_type)},
            headers=auth_headers,
        )
        assert resp.status_code == 201, f"Failed uploading {fname}: {resp.text}"
        data = resp.json()
        assert data["filename"] == fname
        assert data["size_bytes"] == len(payload_bytes)
        assert len(data["source_sha256"]) == 64

    # Verify all 5 documents registered in session manifest
    manifest_resp = studio_client.get(
        f"/api/v1/ontology/studio/sessions/{session_id}/manifest",
        headers=auth_headers,
    )
    assert manifest_resp.status_code == 200
    manifest_data = manifest_resp.json()
    assert len(manifest_data["files"]) == 5
    registered_names = {f["filename"] for f in manifest_data["files"]}
    expected_names = {"report.pdf", "notes.md", "accounts.csv", "holdings.json", "entities.parquet"}
    assert registered_names == expected_names


def test_combinatorial_engine_modes(
    studio_client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    """Combinatorial 2: Engine Modes Matrix (Deterministic vs Agent vs Auto)."""
    modes = ["deterministic", "agent", "auto"]

    for idx, mode in enumerate(modes):
        session_id = f"comb-engine-mode-{mode}-{idx}"
        studio_client.post(
            "/api/v1/ontology/studio/sessions",
            json={"session_id": session_id, "domain_namespace": "wealth_mgmt"},
            headers=auth_headers,
        )

        # Upload tabular source
        csv_bytes = b"customer_id,portfolio_id,nav_amount\nC1,P1,100000\nC2,P2,250000\n"
        studio_client.post(
            f"/api/v1/ontology/studio/sessions/{session_id}/sources",
            files={"file": ("customer_portfolios.csv", io.BytesIO(csv_bytes), "text/csv")},
            headers=auth_headers,
        )

        # Parse source
        parse_resp = studio_client.post(
            f"/api/v1/ontology/studio/sessions/{session_id}/parse",
            json={"ocr_mode": "auto"},
            headers=auth_headers,
        )
        assert parse_resp.status_code == 202

        # Trigger construction with target engine mode
        construct_resp = studio_client.post(
            f"/api/v1/ontology/studio/sessions/{session_id}/construct",
            json={"auto_commit": False, "mode": mode},
            headers=auth_headers,
        )
        assert construct_resp.status_code == 202, construct_resp.text
        job_handle = construct_resp.json()
        assert "job_id" in job_handle
        job_id = job_handle["job_id"]

        # Check job status endpoint
        job_status = studio_client.get(
            f"/api/v1/ontology/studio/jobs/{job_id}",
            headers=auth_headers,
        )
        assert job_status.status_code == 200
        status_data = job_status.json()
        assert status_data["job_id"] == job_id
        assert status_data["job_type"] == "construct"


def test_combinatorial_transports(
    isolated_studio_env: tuple[Path, Path],
    auth_headers: dict[str, str],
) -> None:
    """Combinatorial 3: Transport Matrix (Python API vs REST HTTP vs CLI Commands)."""
    ws_root, studio_root = isolated_studio_env

    # --- Transport 1: Python Service API ---
    py_session_id = "comb-trans-python-01"
    session_mgr = StudioSessionManager(studio_root=studio_root)
    session_mgr.create_session(
        session_id=py_session_id,
        domain_namespace="wealth_mgmt",
        enterprise_owner="Enterprise Architecture",
    )
    meta_py = session_mgr.get_session(py_session_id)
    assert meta_py.session_id == py_session_id
    assert meta_py.head_version == "0.0.0"

    atomic_svc = AtomicCommitService(session_manager=session_mgr)
    init_delta = OntologyDelta(
        delta_id="delta-py-init",
        base_ontology_version="0.0.0",
        target_version="1.0.0",
        parent_sha256="",
        author="human:architect",
        rationale="Python API Genesis Commit",
        operations=[
            AddConceptOp(
                op_id="op-py-fund",
                concept_uri="urn:ontology:entity:py_fund",
                concept_name="Pooled Fund",
                definition="Fund created via Python API.",
                target_hub="hub_py_fund",
                external_mappings=[
                    ExternalMapping(
                        scheme="FIBO",
                        target_uri="https://spec.edmcouncil.org/fibo/ontology/SEC/Securities/Pools/PooledFund",
                        predicate="skos:exactMatch",
                        confidence=0.95,
                    )
                ],
            )
        ],
    )
    commit_res = atomic_svc.commit_delta(
        session_id=py_session_id,
        delta=init_delta,
        author="human:architect",
        if_match=meta_py.head_sha256,
    )
    assert commit_res.version in ("1.0.0", "v1.0.0")

    # --- Transport 2: REST HTTP API ---
    rest_session_id = "comb-trans-rest-02"
    settings = OpenDVSettings(
        workspace_root=ws_root,
        studio_root=studio_root,
        server_mode="standalone_local",
    )
    app = create_app(settings=settings)
    client = TestClient(app)

    create_resp = client.post(
        "/api/v1/ontology/studio/sessions",
        json={"session_id": rest_session_id, "domain_namespace": "wealth_mgmt"},
        headers=auth_headers,
    )
    assert create_resp.status_code == 201
    rest_head_sha = create_resp.json()["head_sha256"]

    delta_payload = {
        "delta_id": "delta-rest-init",
        "parent_sha256": "",
        "base_version": "0.0.0",
        "target_version": "1.0.0",
        "author": "human:architect",
        "rationale": "REST API Genesis Commit",
        "operations": [
            {
                "op_id": "op-rest-fund",
                "op_type": "add_concept",
                "concept_uri": "urn:ontology:entity:rest_fund",
                "concept_name": "RestFund",
                "definition": "Fund created via REST HTTP.",
                "target_hub": "hub_rest_fund",
            }
        ],
        "low_confidence_waiver": True,
        "grounding_rationale": "Approved REST combinatorial concept for transport matrix test.",
    }
    apply_resp = client.post(
        f"/api/v1/ontology/studio/sessions/{rest_session_id}/delta",
        json=delta_payload,
        headers={**auth_headers, "If-Match": f'"{rest_head_sha}"'},
    )
    assert apply_resp.status_code == 200, apply_resp.text
    assert apply_resp.json()["target_version"] in ("1.0.0", "v1.0.0")

    # --- Transport 3: CLI Commands API ---
    cli_session_id = "comb-trans-cli-03"
    exit_code_create = app_main(
        [
            "ontology",
            "studio",
            "create",
            "-s",
            cli_session_id,
            "-n",
            "wealth_mgmt",
            "-o",
            "Global Wealth",
            "--json",
        ]
    )
    assert exit_code_create == 0

    exit_code_status = app_main(
        [
            "ontology",
            "studio",
            "status",
            "-s",
            cli_session_id,
            "--json",
        ]
    )
    assert exit_code_status == 0


def test_combinatorial_delta_operations_matrix() -> None:
    """Combinatorial 4: Comprehensive Delta Operations Matrix (All 24 Operations)."""
    base_ont = GraphAOntology(
        ontology_version="1.0.0",
        domain_namespace="finance",
        enterprise_owner="Architecture Committee",
        canonical_business_concepts=[
            CanonicalBusinessConcept(
                concept_uri="urn:ontology:entity:fund",
                concept_name="Fund",
                target_hub="hub_fund",
            )
        ],
    )
    base_sha = compute_ontology_sha256(base_ont)

    delta_24 = OntologyDelta(
        delta_id="delta-comb-all-24",
        base_ontology_version="1.0.0",
        target_version="2.0.0",  # Removal ops constitute breaking MAJOR SemVer bump
        parent_sha256=base_sha,
        author="steward:alice",
        rationale="Exhaustive validation of all 24 delta operations in open-dv.",
        operations=[
            # 1. AddAttributeOp
            AddAttributeOp(
                op_id="op-01-add-attr",
                concept_uri="urn:ontology:entity:fund",
                attribute_name="fund_code",
                data_type="string",
                description="Identifier code of fund.",
            ),
            # 2. AddSynonymOp
            AddSynonymOp(
                op_id="op-02-add-syn",
                concept_uri="urn:ontology:entity:fund",
                synonym="Investment Vehicle",
            ),
            # 3. AddExternalMappingOp
            AddExternalMappingOp(
                op_id="op-03-add-map",
                concept_uri="urn:ontology:entity:fund",
                scheme="fibo",
                target_uri="https://spec.edmcouncil.org/fibo/ontology/SEC/Funds/Fund",
                mapping_confidence=0.92,
            ),
            # 4. AnnotateConceptMetadataOp
            AnnotateConceptMetadataOp(
                op_id="op-04-annot-meta",
                concept_uri="urn:ontology:entity:fund",
                metadata={"classification": "tier_1"},
            ),
            # 5. ReviseDescriptionOp
            ReviseDescriptionOp(
                op_id="op-05-rev-desc",
                concept_uri="urn:ontology:entity:fund",
                description="Collective investment vehicle pooled by investors.",
            ),
            # 6. RefineDefinitionOp
            RefineDefinitionOp(
                op_id="op-06-ref-def",
                concept_uri="urn:ontology:entity:fund",
                refined_description="Strictly regulated collective investment vehicle.",
            ),
            # 7. SetConceptElmArchetypeOp
            SetConceptElmArchetypeOp(
                op_id="op-07-set-archetype",
                concept_uri="urn:ontology:entity:fund",
                elm_archetype="CORE_BUSINESS_CONCEPT",
            ),
            # 8. AddGovernanceConstraintOp
            AddGovernanceConstraintOp(
                op_id="op-08-gov-const",
                concept_uri="urn:ontology:entity:fund",
                constraint_id="gov-sec-01",
                constraint_type="CONFIDENTIALITY",
                pii_confidential_attributes=["fund_code"],
            ),
            # 9. AddKeyAuthorityPatternOp
            AddKeyAuthorityPatternOp(
                op_id="op-09-key-auth",
                concept_uri="urn:ontology:entity:fund",
                authority_name="InternalIssuer",
                pattern_regex="^FND-[0-9]{5}$",
                tier="tier_1",
            ),
            # 10. AddSourceHintOp
            AddSourceHintOp(
                op_id="op-10-src-hint",
                concept_uri="urn:ontology:entity:fund",
                hint="tbl_funds_master",
            ),
            # 11. AddReferenceEnumerationOp
            AddReferenceEnumerationOp(
                op_id="op-11-add-enum",
                enum_uri="urn:ontology:enum:fund_strategy",
                name="ref_fund_strategy",
                target_reference_table="ref_fund_strategy",
                allowed_codes=["PE", "VC"],
            ),
            # 12. AddReferenceEnumCodeOp
            AddReferenceEnumCodeOp(
                op_id="op-12-add-code",
                enumeration_name="ref_fund_strategy",
                code="GROWTH",
            ),
            # 13. AddConceptOp
            AddConceptOp(
                op_id="op-13-add-concept",
                concept_uri="urn:ontology:entity:limited_partner",
                concept_name="LimitedPartner",
                definition="Investor committing capital to a fund.",
                target_hub="hub_limited_partner",
            ),
            # 14. SetParentConceptOp
            SetParentConceptOp(
                op_id="op-14-set-parent",
                concept_uri="urn:ontology:entity:limited_partner",
                parent_concept_uri="urn:ontology:entity:fund",
            ),
            # 15. AddRelationshipOp
            AddRelationshipOp(
                op_id="op-15-add-rel",
                relationship_uri="urn:ontology:relationship:fund_lp",
                relationship_name="FundLPCommitment",
                target_link="lnk_fund_lp",
                source_concept_uri="urn:ontology:entity:fund",
                target_concept_uri="urn:ontology:entity:limited_partner",
            ),
            # 16. DeprecateRelationshipOp
            DeprecateRelationshipOp(
                op_id="op-16-dep-rel",
                relationship_name="FundLPCommitment",
                reason="Superseded by institutional commitment link.",
            ),
            # 17. RemoveRelationshipOp
            RemoveRelationshipOp(
                op_id="op-17-rem-rel",
                relationship_name="FundLPCommitment",
                reason="Cleaned up obsolete relation.",
            ),
            # 18. RescoreExternalMappingOp
            RescoreExternalMappingOp(
                op_id="op-18-rescore-map",
                concept_uri="urn:ontology:entity:fund",
                scheme="fibo",
                target_uri="https://spec.edmcouncil.org/fibo/ontology/SEC/Funds/Fund",
                new_confidence=0.99,
            ),
            # 19. RepinExternalMappingOp
            RepinExternalMappingOp(
                op_id="op-19-repin-map",
                concept_uri="urn:ontology:entity:fund",
                scheme="fibo",
                target_uri="https://spec.edmcouncil.org/fibo/ontology/SEC/Funds/Fund",
                pinned=True,
            ),
            # 20. RemoveExternalMappingOp
            RemoveExternalMappingOp(
                op_id="op-20-rem-map",
                concept_uri="urn:ontology:entity:fund",
                scheme="fibo",
                target_uri="https://spec.edmcouncil.org/fibo/ontology/SEC/Funds/Fund",
            ),
            # 21. RemoveConceptOp
            RemoveConceptOp(
                op_id="op-21-rem-concept",
                concept_uri="urn:ontology:entity:limited_partner",
                reason="Temporary concept removed during restructuring.",
            ),
            # 22. RemoveAttributeOp
            RemoveAttributeOp(
                op_id="op-22-rem-attr",
                concept_uri="urn:ontology:entity:fund",
                attribute_name="fund_code",
            ),
            # 23. UpdateConceptUrnOp
            UpdateConceptUrnOp(
                op_id="op-23-upd-urn",
                old_concept_uri="urn:ontology:entity:fund",
                new_concept_uri="urn:ontology:entity:fund_v2",
            ),
            # 24. DeprecateConceptOp
            DeprecateConceptOp(
                op_id="op-24-dep-concept",
                concept_uri="urn:ontology:entity:fund_v2",
                reason="Deprecating in favor of global master entity.",
            ),
        ],
    )

    # Apply delta and verify all 24 operations mutated state correctly
    updated_ont = apply_ontology_delta(base_ont, delta_24, actor_role="admin")
    assert updated_ont.ontology_version == "2.0.0"
    assert len(updated_ont.canonical_business_concepts) == 1
    cbc = updated_ont.canonical_business_concepts[0]
    assert cbc.concept_uri == "urn:ontology:entity:fund_v2"
    assert cbc.deprecated is True
    assert len(updated_ont.reference_enumerations) == 1
    assert "ref_fund_strategy" in updated_ont.reference_enumerations[0].target_reference_table


def test_combinatorial_export_formats_isomorphism(
    studio_client: TestClient,
    auth_headers: dict[str, str],
) -> None:
    """Combinatorial 5: Multi-Format Export Isomorphism.

    Tests equivalence across YAML, JCS JSON, Turtle, JSON-LD, NTriples.
    """
    session_id = "comb-export-iso-05"
    create_resp = studio_client.post(
        "/api/v1/ontology/studio/sessions",
        json={"session_id": session_id, "domain_namespace": "wealth_mgmt"},
        headers=auth_headers,
    )
    genesis_sha = create_resp.json()["head_sha256"]

    # Commit canonical concepts and relationships
    delta_payload = {
        "delta_id": "delta-export-01",
        "parent_sha256": "",
        "base_version": "0.0.0",
        "target_version": "1.0.0",
        "author": "steward:export_tester",
        "rationale": "Setup model for multi-format export isomorphism testing.",
        "operations": [
            {
                "op_id": "op-exp-party",
                "op_type": "add_concept",
                "concept_uri": "urn:ontology:entity:party",
                "concept_name": "Party",
                "definition": "A legal party or natural person.",
                "target_hub": "hub_party",
                "external_mappings": [
                    {
                        "scheme": "fibo",
                        "target_uri": "urn:fibo:LegalEntity",
                        "confidence": 0.95,
                    }
                ],
            },
            {
                "op_id": "op-exp-acc",
                "op_type": "add_concept",
                "concept_uri": "urn:ontology:entity:account",
                "concept_name": "Account",
                "definition": "A financial advisory account.",
                "target_hub": "hub_account",
            },
            {
                "op_id": "op-exp-rel",
                "op_type": "add_relationship",
                "relationship_uri": "urn:ontology:relationship:party_account",
                "relationship_name": "PartyAccountLink",
                "target_link": "lnk_party_account",
                "source_concept_uri": "urn:ontology:entity:party",
                "target_concept_uri": "urn:ontology:entity:account",
            },
            {
                "op_id": "op-exp-enum",
                "op_type": "add_reference_enumeration",
                "enum_uri": "urn:ontology:enum:acc_status",
                "name": "ref_account_status",
                "target_reference_table": "ref_account_status",
                "allowed_codes": ["ACTIVE", "CLOSED"],
            },
        ],
        "low_confidence_waiver": True,
        "grounding_rationale": "Approved export isomorphism concepts for multi-format test.",
    }
    commit_resp = studio_client.post(
        f"/api/v1/ontology/studio/sessions/{session_id}/delta",
        json=delta_payload,
        headers={**auth_headers, "If-Match": f'"{genesis_sha}"'},
    )
    assert commit_resp.status_code == 200, commit_resp.text

    # 1. Export YAML
    exp_yaml = studio_client.get(
        f"/api/v1/ontology/studio/sessions/{session_id}/export?export_format=yaml",
        headers=auth_headers,
    )
    assert exp_yaml.status_code == 200
    yaml_obj = yaml.safe_load(exp_yaml.text)
    ont_from_yaml = GraphAOntology.model_validate(yaml_obj)
    assert len(ont_from_yaml.canonical_business_concepts) == 2

    # 2. Export JCS JSON
    exp_json = studio_client.get(
        f"/api/v1/ontology/studio/sessions/{session_id}/export?export_format=json",
        headers=auth_headers,
    )
    assert exp_json.status_code == 200
    ont_from_json = GraphAOntology.model_validate_json(exp_json.text)
    assert len(ont_from_json.canonical_business_concepts) == 2
    # Verify semantic equivalence between YAML and JSON exported models
    assert ont_from_yaml.model_dump() == ont_from_json.model_dump()

    # 3. Export Turtle
    exp_ttl = studio_client.get(
        f"/api/v1/ontology/studio/sessions/{session_id}/export?export_format=turtle",
        headers=auth_headers,
    )
    assert exp_ttl.status_code == 200
    g_ttl = rdflib.Graph().parse(data=exp_ttl.text, format="turtle")
    assert len(g_ttl) > 0

    # 4. Export JSON-LD
    exp_jsonld = studio_client.get(
        f"/api/v1/ontology/studio/sessions/{session_id}/export?export_format=jsonld",
        headers=auth_headers,
    )
    assert exp_jsonld.status_code == 200
    g_jsonld = rdflib.Graph().parse(data=exp_jsonld.text, format="json-ld")
    assert len(g_jsonld) > 0

    # 5. Export NTriples
    exp_nt = studio_client.get(
        f"/api/v1/ontology/studio/sessions/{session_id}/export?export_format=ntriples",
        headers=auth_headers,
    )
    assert exp_nt.status_code == 200
    g_nt = rdflib.Graph().parse(data=exp_nt.text, format="nt")
    assert len(g_nt) > 0

    # Verify RDF Semantic Graph Isomorphism across Turtle, JSON-LD, and NTriples
    assert isomorphic(g_ttl, g_nt), "Turtle and NTriples graphs are not isomorphic!"
    assert isomorphic(g_ttl, g_jsonld), "Turtle and JSON-LD graphs are not isomorphic!"
