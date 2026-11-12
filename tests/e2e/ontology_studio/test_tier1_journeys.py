"""Task 6B.1 (#230): Tier 1 E2E Canonical User Journeys.

Tests 5 complete end-to-end user journeys:
1. Journey 1: Greenfield Genesis (Draft 0.0.0 -> Ingest -> Parse -> Commit v1.0.0 -> Export)
2. Journey 2: FIBO Grounding (Table Ingestion -> BM25 Matching >= 0.6500 -> Commit v1.0.0)
3. Journey 3: Multi-Version Evolution (v1.0.0 -> Minor v1.1.0 -> Patch v1.1.1 -> Lineage check)
4. Journey 4: Workspace Promotion (Two-phase L4+L5 lock -> OCC check -> Contract updated)
5. Journey 5: Administrative Waiver (Low-confidence rejected -> Valid steward waiver succeeds)
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from starlette.testclient import TestClient

from opendv_cli.main import app_main
from opendv_core.genesis import create_genesis_ontology
from opendv_core.ontology_hashing import compute_ontology_sha256
from opendv_service.construction.clarification_harness import EMPTY_ANSWERS_SHA256
from opendv_service.studio.session_manager import (
    GENESIS_VERSION,
    StudioSessionManager,
)
from opendv_service.studio.workspace_security import WorkspaceSecurityManager


def test_journey_1_greenfield_genesis(
    studio_client: TestClient,
    auth_headers: dict[str, str],
    pe_source_fixtures: dict[str, Path],
    isolated_studio_env: tuple[Path, Path],
) -> None:
    """Journey 1: Greenfield Genesis lifecycle from Draft 0.0.0 to v1.0.0 commit and export."""
    session_id = "journey-genesis-01"

    # 1. Create Session at Greenfield Genesis 0.0.0
    create_resp = studio_client.post(
        "/api/v1/ontology/studio/sessions",
        json={"session_id": session_id, "domain_namespace": "private_equity"},
        headers=auth_headers,
    )
    assert create_resp.status_code == 201, create_resp.text
    session_data = create_resp.json()
    expected_genesis_sha = compute_ontology_sha256(
        create_genesis_ontology(
            domain_namespace="private_equity",
            enterprise_owner=session_data["enterprise_owner"],
        )
    )
    assert session_data["session_id"] == session_id
    assert session_data["active_version"] == GENESIS_VERSION
    assert session_data["head_sha256"] == expected_genesis_sha

    # 2. Upload Sources (tabular funds and portfolio companies)
    with pe_source_fixtures["funds"].open("rb") as f:
        upload_funds = studio_client.post(
            f"/api/v1/ontology/studio/sessions/{session_id}/sources",
            files={"file": ("pe_funds.csv", f, "text/csv")},
            data={"do_not_send_to_agent": "false", "description": "PE Funds registry"},
            headers=auth_headers,
        )
    assert upload_funds.status_code == 201, upload_funds.text
    funds_source_id = upload_funds.json()["source_id"]

    with pe_source_fixtures["portfolio_companies"].open("rb") as f:
        upload_companies = studio_client.post(
            f"/api/v1/ontology/studio/sessions/{session_id}/sources",
            files={"file": ("pe_portfolio_companies.csv", f, "text/csv")},
            data={"do_not_send_to_agent": "false", "description": "PE Portfolio Assets"},
            headers=auth_headers,
        )
    assert upload_companies.status_code == 201, upload_companies.text

    # 3. Parse Ingested Sources
    parse_resp = studio_client.post(
        f"/api/v1/ontology/studio/sessions/{session_id}/parse",
        json={"ocr_mode": "auto", "source_ids": [funds_source_id]},
        headers=auth_headers,
    )
    assert parse_resp.status_code == 202
    parse_job = parse_resp.json()
    assert "job_id" in parse_job

    # 4. Clarification Flow & Answer Submission with OCC check
    answers_resp = studio_client.post(
        f"/api/v1/ontology/studio/sessions/{session_id}/answers",
        json={
            "answers": {
                "q_strategy_enum": ["ref_fund_strategy"],
            },
            "expected_answers_sha256": EMPTY_ANSWERS_SHA256,
        },
        headers=auth_headers,
    )
    assert answers_resp.status_code == 200, answers_resp.text
    assert answers_resp.json()["saved_count"] == 1

    # 5. Commit Initial Greenfield Delta (0.0.0 -> 1.0.0) with AddConcept operations
    delta_payload = {
        "delta_id": "delta-genesis-01",
        "parent_sha256": "",
        "base_version": "0.0.0",
        "target_version": "1.0.0",
        "author": "steward:alice",
        "rationale": "Initial Genesis Construction of PE Ontology v1.0.0",
        "operations": [
            {
                "op_id": "op-add-fund",
                "op_type": "add_concept",
                "concept_uri": "urn:ontology:entity:fund",
                "concept_name": "Fund",
                "definition": "A private investment pool.",
                "target_hub": "hub_fund",
                "external_mappings": [
                    {
                        "scheme": "fibo",
                        "target_uri": "https://spec.edmcouncil.org/fibo/ontology/SEC/Funds/Fund",
                        "confidence": 0.85,
                    }
                ],
            },
            {
                "op_id": "op-add-company",
                "op_type": "add_concept",
                "concept_uri": "urn:ontology:entity:portfolio_company",
                "concept_name": "PortfolioCompany",
                "definition": "An operating enterprise owned by a fund.",
                "target_hub": "hub_portfolio_company",
                "external_mappings": [
                    {
                        "scheme": "fibo",
                        "target_uri": (
                            "https://spec.edmcouncil.org/fibo/ontology/BE/Corporations/Company"
                        ),
                        "confidence": 0.80,
                    }
                ],
            },
        ],
        "low_confidence_waiver": True,
        "grounding_rationale": "Approved PE genesis concepts for Journey 1 lifecycle verification.",
    }

    commit_resp = studio_client.post(
        f"/api/v1/ontology/studio/sessions/{session_id}/delta",
        json=delta_payload,
        headers={**auth_headers, "If-Match": f'"{expected_genesis_sha}"'},
    )
    assert commit_resp.status_code == 200, commit_resp.text
    commit_data = commit_resp.json()
    assert commit_data["target_version"] in ("1.0.0", "v1.0.0")
    v1_sha256 = commit_data["target_sha256"]
    assert len(v1_sha256) == 64

    # 6. Verify Session State Advanced to v1.0.0
    session_after = studio_client.get(
        f"/api/v1/ontology/studio/sessions/{session_id}",
        headers=auth_headers,
    ).json()
    assert session_after["active_version"] == "1.0.0"
    assert session_after["head_sha256"] == v1_sha256

    # 7. Multi-Format Export Verification
    for fmt, mime in [
        ("yaml", "application/yaml"),
        ("json", "application/json"),
        ("turtle", "text/turtle"),
        ("jsonld", "application/ld+json"),
        ("ntriples", "application/n-triples"),
    ]:
        exp_resp = studio_client.get(
            f"/api/v1/ontology/studio/sessions/{session_id}/export?export_format={fmt}",
            headers=auth_headers,
        )
        assert exp_resp.status_code == 200, f"Format {fmt} export failed: {exp_resp.text}"
        assert mime in exp_resp.headers["content-type"]
        assert len(exp_resp.content) > 0


def test_journey_2_fibo_grounding(
    studio_client: TestClient,
    auth_headers: dict[str, str],
    wm_source_fixtures: dict[str, Path],
) -> None:
    """Journey 2: FIBO grounding verification with confidence >= 0.6500 passes without waiver."""
    session_id = "journey-fibo-02"

    create_resp = studio_client.post(
        "/api/v1/ontology/studio/sessions",
        json={"session_id": session_id, "domain_namespace": "wealth_mgmt"},
        headers=auth_headers,
    )
    genesis_sha = create_resp.json()["head_sha256"]

    # Ingest Banking Table
    with wm_source_fixtures["parties"].open("rb") as f:
        studio_client.post(
            f"/api/v1/ontology/studio/sessions/{session_id}/sources",
            files={"file": ("wm_parties.csv", f, "text/csv")},
            data={"do_not_send_to_agent": "false"},
            headers=auth_headers,
        )

    # Apply Delta with verified FIBO groundings (confidence >= 0.6500)
    delta_payload = {
        "delta_id": "delta-fibo-01",
        "parent_sha256": "",
        "base_version": "0.0.0",
        "target_version": "1.0.0",
        "author": "steward:bob",
        "rationale": "Grounded Wealth Management initial concepts",
        "operations": [
            {
                "op_id": "op-add-party",
                "op_type": "add_concept",
                "concept_uri": "urn:ontology:entity:party",
                "concept_name": "ContractParty",
                "definition": (
                    "A legally competent party that has entered into a binding agreement."
                ),
                "target_hub": "hub_party",
                "external_mappings": [
                    {
                        "scheme": "fibo",
                        "predicate": "skos:exactMatch",
                        "target_uri": (
                            "https://spec.edmcouncil.org/fibo/ontology/FND/"
                            "Agreements/Contracts/ContractParty"
                        ),
                        "confidence": 0.8850,
                    }
                ],
            },
            {
                "op_id": "op-add-instrument",
                "op_type": "add_concept",
                "concept_uri": "urn:ontology:entity:financial_instrument",
                "concept_name": "FinancialInstrument",
                "definition": "A tradeable asset or contractual right to future cash flows.",
                "target_hub": "hub_instrument",
                "external_mappings": [
                    {
                        "scheme": "fibo",
                        "predicate": "skos:exactMatch",
                        "target_uri": (
                            "https://spec.edmcouncil.org/fibo/ontology/FBC/"
                            "FinancialInstruments/FinancialInstruments/FinancialInstrument"
                        ),
                        "confidence": 0.9420,
                    }
                ],
            },
        ],
    }

    resp = studio_client.post(
        f"/api/v1/ontology/studio/sessions/{session_id}/delta",
        json=delta_payload,
        headers={**auth_headers, "If-Match": f'"{genesis_sha}"'},
    )
    assert resp.status_code == 200, resp.text
    result = resp.json()
    assert result["target_version"] in ("1.0.0", "v1.0.0")


def test_journey_3_multiversion_evolution(
    studio_client: TestClient,
    auth_headers: dict[str, str],
    isolated_studio_env: tuple[Path, Path],
) -> None:
    """Journey 3: Multi-version evolution: v1.0.0 -> Minor v1.1.0 -> Patch v1.1.1 with lineage."""
    session_id = "journey-evolution-03"

    create_resp = studio_client.post(
        "/api/v1/ontology/studio/sessions",
        json={"session_id": session_id, "domain_namespace": "wealth_mgmt"},
        headers=auth_headers,
    )
    genesis_sha = create_resp.json()["head_sha256"]

    # 1. Commit v1.0.0
    v1_delta = {
        "delta_id": "delta-v1-0-0",
        "parent_sha256": "",
        "base_version": "0.0.0",
        "target_version": "1.0.0",
        "author": "steward:alice",
        "rationale": "Initial release",
        "operations": [
            {
                "op_id": "op-add-customer",
                "op_type": "add_concept",
                "concept_uri": "urn:ontology:entity:customer",
                "concept_name": "Customer",
                "definition": "An account-holding client.",
                "target_hub": "hub_customer",
                "external_mappings": [
                    {
                        "scheme": "fibo",
                        "target_uri": "urn:fibo:customer",
                        "confidence": 0.75,
                    }
                ],
            }
        ],
        "low_confidence_waiver": True,
        "grounding_rationale": "Approved customer concept for Journey 3 evolution test.",
    }
    r1 = studio_client.post(
        f"/api/v1/ontology/studio/sessions/{session_id}/delta",
        json=v1_delta,
        headers={**auth_headers, "If-Match": f'"{genesis_sha}"'},
    )
    assert r1.status_code == 200, r1.text
    sha_v1 = r1.json()["target_sha256"]

    # 2. Additive Delta: AddAttributeOp -> Minor Bump to v1.1.0
    v1_1_delta = {
        "delta_id": "delta-v1-1-0",
        "parent_sha256": sha_v1,
        "base_version": "1.0.0",
        "target_version": "1.1.0",
        "author": "steward:alice",
        "rationale": "Add credit_score attribute to customer",
        "operations": [
            {
                "op_id": "op-add-attr-credit-score",
                "op_type": "add_attribute",
                "concept_uri": "urn:ontology:entity:customer",
                "attribute_name": "credit_score",
                "data_type": "INTEGER",
                "description": "Standard bureau credit score",
            }
        ],
        "low_confidence_waiver": True,
        "grounding_rationale": "Approved attribute addition on customer concept.",
    }
    r2 = studio_client.post(
        f"/api/v1/ontology/studio/sessions/{session_id}/delta",
        json=v1_1_delta,
        headers={**auth_headers, "If-Match": f'"{sha_v1}"'},
    )
    assert r2.status_code == 200, r2.text
    sha_v1_1 = r2.json()["target_sha256"]
    assert r2.json()["target_version"] in ("1.1.0", "v1.1.0")

    # 3. Patch Delta: AddSynonymOp -> Patch Bump to v1.1.1
    v1_1_1_delta = {
        "delta_id": "delta-v1-1-1",
        "parent_sha256": sha_v1_1,
        "base_version": "1.1.0",
        "target_version": "1.1.1",
        "author": "steward:alice",
        "rationale": "Add alternative term synonym to customer",
        "operations": [
            {
                "op_id": "op-add-synonym-client",
                "op_type": "add_synonym",
                "concept_uri": "urn:ontology:entity:customer",
                "synonym": "Client",
            }
        ],
        "low_confidence_waiver": True,
        "grounding_rationale": "Approved synonym addition on customer concept.",
    }
    r3 = studio_client.post(
        f"/api/v1/ontology/studio/sessions/{session_id}/delta",
        json=v1_1_1_delta,
        headers={**auth_headers, "If-Match": f'"{sha_v1_1}"'},
    )
    assert r3.status_code == 200, r3.text
    sha_v1_1_1 = r3.json()["target_sha256"]
    assert r3.json()["target_version"] in ("1.1.1", "v1.1.1")

    # 4. Verify Version Lineage & Parent SHA Continuity
    session_final = studio_client.get(
        f"/api/v1/ontology/studio/sessions/{session_id}",
        headers=auth_headers,
    ).json()
    assert session_final["active_version"] == "1.1.1"
    assert session_final["head_sha256"] == sha_v1_1_1

    session_mgr = StudioSessionManager(studio_root=isolated_studio_env[1])
    stored_meta = session_mgr.get_session(session_id)
    assert "v1.0.0" in stored_meta.committed_versions
    assert "v1.1.0" in stored_meta.committed_versions
    assert "v1.1.1" in stored_meta.committed_versions


def test_journey_4_workspace_promotion(
    studio_client: TestClient,
    auth_headers: dict[str, str],
    isolated_studio_env: tuple[Path, Path],
) -> None:
    """Journey 4: Two-phase lock acquisition, OCC check, promotion intent commit and transition."""
    workspace_root, studio_root = isolated_studio_env
    session_id = "journey-promote-04"

    create_resp = studio_client.post(
        "/api/v1/ontology/studio/sessions",
        json={"session_id": session_id, "domain_namespace": "wealth_mgmt"},
        headers=auth_headers,
    )
    head_sha = create_resp.json()["head_sha256"]

    # Commit v1.0.0 so we have an active production candidate
    delta_payload = {
        "delta_id": "delta-promo-v1",
        "parent_sha256": "",
        "base_version": "0.0.0",
        "target_version": "1.0.0",
        "author": "steward:alice",
        "rationale": "Production candidate promotion v1.0.0",
        "operations": [
            {
                "op_id": "op-add-portfolio",
                "op_type": "add_concept",
                "concept_uri": "urn:ontology:entity:portfolio",
                "concept_name": "Portfolio",
                "definition": "An aggregate collection of financial assets.",
                "target_hub": "hub_portfolio",
                "external_mappings": [
                    {
                        "scheme": "fibo",
                        "target_uri": "urn:fibo:portfolio",
                        "confidence": 0.82,
                    }
                ],
            }
        ],
        "low_confidence_waiver": True,
        "grounding_rationale": "Approved portfolio concept for Journey 4 promotion test.",
    }
    commit_resp = studio_client.post(
        f"/api/v1/ontology/studio/sessions/{session_id}/delta",
        json=delta_payload,
        headers={**auth_headers, "If-Match": f'"{head_sha}"'},
    )
    assert commit_resp.status_code == 200, commit_resp.text
    v1_sha256 = commit_resp.json()["target_sha256"]

    # Execute Promotion to Workspace
    target_rel = "dv_model_contract.yaml"
    promote_resp = studio_client.post(
        f"/api/v1/ontology/studio/sessions/{session_id}/promote",
        json={
            "head_sha256": v1_sha256,
            "workspace_sha256": "0" * 64,
            "target_contract_path": target_rel,
            "rationale": "Promoting Wealth Management v1.0.0 to enterprise workspace contract.",
        },
        headers={
            **auth_headers,
            "If-Match": f'"{v1_sha256}"',
            "X-OpenDV-Workspace-If-Match": f'"{"0" * 64}"',
        },
    )
    assert promote_resp.status_code == 200, promote_resp.text
    promo_data = promote_resp.json()
    assert promo_data["promoted_version"] == "1.0.0"
    assert promo_data["head_sha256"] == v1_sha256

    # Verify target contract file written in workspace
    promoted_file = workspace_root / promo_data["target_contract_path"]
    assert promoted_file.is_file()
    assert len(promoted_file.read_bytes()) > 0

    # Verify session metadata records promoted version
    session_mgr_check = StudioSessionManager(studio_root=studio_root)
    assert "v1.0.0" in session_mgr_check.get_session(session_id).promoted_versions

    # Verify deleting promoted session without force is rejected with 409
    del_fail = studio_client.delete(
        f"/api/v1/ontology/studio/sessions/{session_id}",
        headers=auth_headers,
    )
    assert del_fail.status_code == 409
    assert del_fail.json()["error_code"] == "CANNOT_DELETE_PROMOTED_SESSION"


def test_journey_5_administrative_waiver(
    isolated_studio_env: tuple[Path, Path],
    monkeypatch: Any,
) -> None:
    """Journey 5: Low-confidence ungrounded concept rejected; signed waiver succeeds."""
    _workspace_root, studio_root = isolated_studio_env
    session_id = "journey-waiver-05"

    # Initialize studio and session via CLI
    assert app_main(["ontology", "studio", "init"]) == 0
    assert app_main(["ontology", "studio", "create", "-s", session_id, "--json"]) == 0

    sec_mgr = WorkspaceSecurityManager(studio_root=studio_root)
    valid_waiver_key = sec_mgr.get_waiver_key().hex()

    # Prepare ungrounded proposal (< 0.6500)
    meta_path = studio_root / session_id / "session.json"
    session_meta = json.loads(meta_path.read_text(encoding="utf-8"))
    head_sha = session_meta["head_sha256"]
    props_dir = studio_root / session_id / "proposals"
    props_dir.mkdir(parents=True, exist_ok=True)

    prop_id = "prop-ungrounded-01"
    prop_data = {
        "proposal_id": prop_id,
        "session_id": session_id,
        "base_version": "0.0.0",
        "base_sha256": head_sha,
        "status": "PENDING",
        "operations": [
            {
                "op": "AddConcept",
                "concept_name": "Proprietary Algorithm Model",
                "concept_uri": "urn:ontology:entity:proprietary_model",
                "definition": "Unregistered proprietary asset with zero external mapping.",
                "external_mappings": [],
            }
        ],
        "proposer": "system:deterministic",
        "created_at": "2026-11-11T00:00:00Z",
    }
    (props_dir / f"{prop_id}.json").write_text(json.dumps(prop_data), encoding="utf-8")

    # 1. Attempt accept without waiver credentials -> rejected with exit 7
    monkeypatch.delenv("OPENDV_WAIVER_KEY", raising=False)
    exit_unauth = app_main(
        [
            "ontology",
            "studio",
            "proposal",
            "accept",
            "-s",
            session_id,
            "-p",
            prop_id,
            "--base-sha256",
            head_sha,
            "--low-confidence-waiver",
            "--waiver-rationale",
            "Authorizing ungrounded proprietary algorithm concept.",
            "--json",
        ]
    )
    assert exit_unauth == 7

    # 2. Set valid OPENDV_WAIVER_KEY in environment -> acceptance succeeds with exit 0
    monkeypatch.setenv("OPENDV_WAIVER_KEY", valid_waiver_key)
    exit_success = app_main(
        [
            "ontology",
            "studio",
            "proposal",
            "accept",
            "-s",
            session_id,
            "-p",
            prop_id,
            "--base-sha256",
            head_sha,
            "--low-confidence-waiver",
            "--waiver-rationale",
            "Authorizing ungrounded proprietary algorithm concept with steward key.",
            "--json",
        ]
    )
    assert exit_success == 0
