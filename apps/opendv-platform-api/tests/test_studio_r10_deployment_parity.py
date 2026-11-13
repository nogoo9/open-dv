"""Regression tests for Track 1 Final Pre-Merge Remediations (`S4-R10-MAJ-01`, `MIN-01`, `MIN-02`).

Covers:
1. `S4-R10-MAJ-01` (`ONT-EVO-001` / AC-7 deployment state provider parity):
   - Shared `build_workspace_deployment_provider` extracting `hubs`, `links`, `satellites`,
     `reference_tables`, `deployed_concepts`, `deployed_entities`, `deployed_tables`, and `tables`
     (list & mapping forms), failing closed on malformed collections, skipping Graph A ontologies,
     and pruning monorepo source directories in < 50ms.
   - Full REST API parity across `POST /sessions/{id}/validate`, `POST /sessions/{id}/delta`,
     `POST /sessions/{id}/proposals/{pid}/accept`, `POST /sessions/{id}/proposals/{pid}/rebase`,
     and `POST /sessions/{id}/promote`: rejecting removals of deployed concepts/relationships/
     attributes and additions of mandatory attributes (`is_optional=False`) with
     `ONT_EVO_CONTRACT_VIOLATION` (`422`), while allowing optional attributes and ungrounded
     proposal rebasing.
   - Corrupt contract files in `contracts/bad_contract.yaml` failing closed with
     `INVALID_WORKSPACE_ONTOLOGY` (`valid=False` on `POST /validate`, `422` on commit/accept/
     rebase/promote).
2. `S4-R10-MIN-01`:
   - `GET /sessions/{id}/export?export_format=yaml%20` trims whitespace and sanitizes the
     `Content-Disposition` filename extension (`ontology-v1.0.0.yaml`) without `h11` header errors.
3. `S4-R10-MIN-02`:
   - Corrupt `ontology.json` in `versions/vX.Y.Z/` returns canonical Studio RFC 9457 Problem
     Details (`500`, `code == "CORRUPT_SESSION_RECORD"`).
   - Unhandled `ValueError` on Studio routes returns canonical Studio Problem Details
     (`400`, `code == "SYNTAX_INVALID_ARGUMENT"`).
"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi import APIRouter
from fastapi.testclient import TestClient

from opendv_contracts.errors import ErrorCode
from opendv_platform_api.main import create_app
from opendv_service import OpenDVSettings
from opendv_service.ontology.validator import (
    OntologyGovernanceError,
    build_workspace_deployment_provider,
)


@pytest.fixture
def studio_test_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> tuple[TestClient, dict[str, str], Path, Path]:
    """Create isolated Studio TestClient with dedicated studio_root and workspace_root."""
    from opendv_core.schema_registry import get_default_schema_registry

    get_default_schema_registry()
    studio_root = tmp_path / "opendv_studio"
    workspace_root = tmp_path / "workspace"
    studio_root.mkdir(parents=True, exist_ok=True)
    workspace_root.mkdir(parents=True, exist_ok=True)

    monkeypatch.setenv("OPENDV_STUDIO_ROOT", str(studio_root))
    monkeypatch.setenv("OPENDV_WORKSPACE_ROOT", str(workspace_root))
    monkeypatch.setenv("OPENDV_SERVER_MODE", "local_standalone")

    settings = OpenDVSettings.load(
        workspace_root=workspace_root,
        state_dir=tmp_path / "state",
        is_server=True,
    )
    app = create_app(settings=settings)
    client = TestClient(app, client=("127.0.0.1", 50000), base_url="http://localhost:8000")

    init_resp = client.post(
        "/api/v1/ontology/studio/init",
        json={"domain_namespace": "wealth_mgmt", "enterprise_owner": "Global Wealth"},
    )
    assert init_resp.status_code == 200, init_resp.text
    token = init_resp.json()["local_token"]
    headers = {
        "Authorization": f"Bearer {token}",
        "X-OpenDV-Workbench": "1",
        "Origin": "http://localhost:8000",
    }
    return client, headers, workspace_root, studio_root


def _seed_session_v1(
    client: TestClient,
    headers: dict[str, str],
    session_id: str,
) -> str:
    """Create session and commit v1.0.0 containing customer, account, and holds_account."""
    create_resp = client.post(
        "/api/v1/ontology/studio/sessions",
        json={"session_id": session_id, "domain_namespace": "wealth_mgmt"},
        headers=headers,
    )
    assert create_resp.status_code == 201, create_resp.text
    genesis_sha = create_resp.json()["head_sha256"]

    commit_resp = client.post(
        f"/api/v1/ontology/studio/sessions/{session_id}/delta",
        json={
            "parent_sha256": genesis_sha,
            "base_version": "0.0.0",
            "target_version": "1.0.0",
            "operations": [
                {
                    "op_type": "add_concept",
                    "op_id": "op-cust",
                    "concept_uri": "urn:ontology:entity:customer",
                    "concept_name": "Customer",
                    "definition": "Enterprise wealth customer.",
                    "target_hub": "hub_customer",
                    "elm_archetype": "LEGAL_PARTY",
                    "attributes": ["customer_code", "segment_tier"],
                },
                {
                    "op_type": "add_concept",
                    "op_id": "op-acct",
                    "concept_uri": "urn:ontology:entity:account",
                    "concept_name": "Account",
                    "definition": "Custody investment account.",
                    "target_hub": "hub_account",
                },
                {
                    "op_type": "add_relationship",
                    "op_id": "op-rel-holds",
                    "relationship_uri": "urn:ontology:relationship:holds_account",
                    "relationship_name": "holds_account",
                    "source_concept_uri": "urn:ontology:entity:customer",
                    "target_concept_uri": "urn:ontology:entity:account",
                    "endpoints": [
                        "urn:ontology:entity:customer",
                        "urn:ontology:entity:account",
                    ],
                    "target_link": "lnk_customer_account",
                },
            ],
            "low_confidence_waiver": True,
            "grounding_rationale": "Seed deployed customer and account domain model for AC-7.",
        },
        headers={**headers, "If-Match": f'"{genesis_sha}"'},
    )
    assert commit_resp.status_code == 200, commit_resp.text
    return str(commit_resp.json()["target_sha256"])


def test_build_workspace_deployment_provider_collections_and_monorepo_guard(
    tmp_path: Path,
) -> None:
    """Verify contract schema variants, fail-closed collection checks, and monorepo fast guard."""
    assert build_workspace_deployment_provider(None) is None
    assert build_workspace_deployment_provider(tmp_path / "does_not_exist") is None

    ws = tmp_path / "ws_unit"
    contracts_dir = ws / "contracts"
    contracts_dir.mkdir(parents=True, exist_ok=True)

    # Graph A ontology named *contract* MUST be skipped rather than treated as a DV contract
    (ws / "graph_a_contract.yaml").write_text(
        yaml.safe_dump({"ontology_version": "1.0.0", "canonical_business_concepts": []}),
        encoding="utf-8",
    )

    (contracts_dir / "v5_dv.yaml").write_text(
        yaml.safe_dump(
            {
                "deployed_concepts": ["urn:ontology:entity:party"],
                "deployed_entities": ["urn:ontology:relationship:owns"],
                "deployed_tables": ["hub_party", {"table_name": "sat_party_kyc"}],
                "tables": {
                    "lnk_party_mandate": {
                        "relationship_uri": "urn:ontology:relationship:mandate_link"
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    (contracts_dir / "tables_list.json").write_text(
        '{"tables": [{"name": "hub_mandate", "concept_uri": "urn:ontology:entity:mandate"}]}',
        encoding="utf-8",
    )

    prov = build_workspace_deployment_provider(ws)
    assert prov is not None
    assert {
        "urn:ontology:entity:party",
        "urn:ontology:entity:mandate",
    }.issubset(prov.get_deployed_concepts())
    assert {
        "hub_party",
        "sat_party_kyc",
        "lnk_party_mandate",
        "hub_mandate",
    }.issubset(prov.get_deployed_tables())
    assert {
        "urn:ontology:relationship:owns",
        "urn:ontology:relationship:mandate_link",
        "urn:ontology:entity:mandate",
    }.issubset(prov.get_deployed_entities())

    # Invalid collection type in contracts/ MUST fail closed with INVALID_WORKSPACE_ONTOLOGY
    (contracts_dir / "malformed_collection.yaml").write_text(
        yaml.safe_dump({"hubs": "not_a_list"}),
        encoding="utf-8",
    )
    with pytest.raises(OntologyGovernanceError) as exc_info:
        build_workspace_deployment_provider(ws)
    assert exc_info.value.error_code == ErrorCode.INVALID_WORKSPACE_ONTOLOGY
    assert exc_info.value.status_code == 422
    assert exc_info.value.exit_code == 4

    # Monorepo root guard: scanning repo root skips packages/apps/tests/examples rapidly
    repo_root = Path(__file__).resolve().parents[3]
    assert (repo_root / "pyproject.toml").is_file()
    assert (repo_root / "packages" / "opendv-core").is_dir()
    t0 = time.perf_counter()
    repo_prov = build_workspace_deployment_provider(repo_root)
    elapsed = time.perf_counter() - t0
    assert repo_prov is None
    assert elapsed < 0.25


def test_s4_r10_maj01_http_ac7_deployed_contract_immutability_parity(
    studio_test_env: tuple[TestClient, dict[str, str], Path, Path],
) -> None:
    """Verify AC-7 ONT-EVO-001 enforcement across validate, delta, accept, rebase, and promote."""
    client, headers, workspace_root, _studio_root = studio_test_env
    sid = "s-r10-ac7"
    head_v1_sha = _seed_session_v1(client, headers, sid)

    # Promote v1.0.0 to workspace BEFORE deploying physical Data Vault contract
    prom_v1 = client.post(
        f"/api/v1/ontology/studio/sessions/{sid}/promote",
        json={"rationale": "Initial baseline workspace ontology v1.0.0 promotion."},
        headers={
            **headers,
            "If-Match": f'"{head_v1_sha}"',
            "X-OpenDV-Workspace-If-Match": f'"{"0" * 64}"',
        },
    )
    assert prom_v1.status_code == 200, prom_v1.text
    ws_v1_sha = prom_v1.json()["workspace_sha256"]

    # Create deployed Data Vault contract in workspace/contracts/dv_contract.yaml
    contracts_dir = workspace_root / "contracts"
    contracts_dir.mkdir(parents=True, exist_ok=True)
    dv_contract_path = contracts_dir / "dv_contract.yaml"
    dv_contract_payload = {
        "contract_version": "5.0",
        "hubs": [
            {
                "name": "hub_customer",
                "ontology_concept_uri": "urn:ontology:entity:customer",
            }
        ],
        "links": [
            {
                "name": "lnk_customer_account",
                "ontology_concept_uri": "urn:ontology:relationship:holds_account",
            }
        ],
    }
    dv_contract_path.write_text(yaml.safe_dump(dv_contract_payload), encoding="utf-8")

    breaking_cases: list[tuple[str, dict[str, Any]]] = [
        (
            "remove_concept",
            {
                "op_type": "remove_concept",
                "op_id": "op-break-concept",
                "concept_uri": "urn:ontology:entity:customer",
                "reason": "Attempt removing deployed hub_customer concept",
            },
        ),
        (
            "remove_relationship",
            {
                "op_type": "remove_relationship",
                "op_id": "op-break-rel",
                "relationship_uri": "urn:ontology:relationship:holds_account",
                "relationship_name": "holds_account",
                "reason": "Attempt removing deployed lnk_customer_account relationship",
            },
        ),
        (
            "remove_attribute",
            {
                "op_type": "remove_attribute",
                "op_id": "op-break-attr",
                "concept_uri": "urn:ontology:entity:customer",
                "attribute_name": "segment_tier",
                "reason": "Attempt removing attribute from deployed hub_customer",
            },
        ),
        (
            "add_mandatory_attribute",
            {
                "op_type": "add_attribute",
                "op_id": "op-break-mand",
                "concept_uri": "urn:ontology:entity:customer",
                "attribute_name": "tax_residency_code",
                "data_type": "string",
                "is_optional": False,
            },
        ),
    ]

    # 1. POST /validate -> 200 with valid=False & error_code="ONT_EVO_CONTRACT_VIOLATION"
    # 2. POST /delta -> 422 with code="ONT_EVO_CONTRACT_VIOLATION"
    for label, op in breaking_cases:
        val_resp = client.post(
            f"/api/v1/ontology/studio/sessions/{sid}/validate",
            json={
                "parent_sha256": head_v1_sha,
                "base_version": "1.0.0",
                "target_version": "2.0.0",
                "operations": [op],
                "low_confidence_waiver": True,
                "grounding_rationale": "Dry-run waiver to isolate ONT-EVO-001 violation.",
            },
            headers=headers,
        )
        assert val_resp.status_code == 200, f"{label}: {val_resp.text}"
        val_body = val_resp.json()
        assert val_body["valid"] is False, f"{label}: expected valid=False, got {val_body}"
        assert val_body["error_code"] == "ONT_EVO_CONTRACT_VIOLATION"

        apply_resp = client.post(
            f"/api/v1/ontology/studio/sessions/{sid}/delta",
            json={
                "parent_sha256": head_v1_sha,
                "base_version": "1.0.0",
                "target_version": "2.0.0",
                "operations": [op],
                "low_confidence_waiver": True,
                "grounding_rationale": "Attempted admin waiver cannot bypass ONT-EVO-001.",
            },
            headers={**headers, "If-Match": f'"{head_v1_sha}"'},
        )
        assert apply_resp.status_code == 422, f"{label}: {apply_resp.text}"
        assert apply_resp.json()["code"] == "ONT_EVO_CONTRACT_VIOLATION"
        assert apply_resp.json()["error_code"] == "ONT_EVO_CONTRACT_VIOLATION"

    # 3. POST /proposals/{id}/accept -> 422 ONT_EVO_CONTRACT_VIOLATION
    editor_headers = {
        **headers,
        "X-OpenDV-Principal": "human:editor_bob",
        "X-OpenDV-Role": "editor",
    }
    prop_break = client.post(
        f"/api/v1/ontology/studio/sessions/{sid}/proposals",
        json={
            "proposal_id": "prop-drop-cust",
            "operations": [breaking_cases[3][1]],
            "rationale": "Proposal adding mandatory attribute to deployed concept",
        },
        headers=editor_headers,
    )
    assert prop_break.status_code == 201, prop_break.text

    acc_resp = client.post(
        f"/api/v1/ontology/studio/sessions/{sid}/proposals/prop-drop-cust/accept",
        json={
            "low_confidence_waiver": True,
            "grounding_rationale": "Admin waiver should still fail ONT-EVO-001 on accept.",
        },
        headers={**headers, "If-Match": f'"{head_v1_sha}"'},
    )
    assert acc_resp.status_code == 422, acc_resp.text
    assert acc_resp.json()["code"] == "ONT_EVO_CONTRACT_VIOLATION"

    # Also create an ungrounded non-breaking proposal on v1.0.0 to verify rebase allows it
    prop_ok = client.post(
        f"/api/v1/ontology/studio/sessions/{sid}/proposals",
        json={
            "proposal_id": "prop-ungrounded-ok",
            "operations": [
                {
                    "op_type": "add_concept",
                    "op_id": "op-ungrounded-portfolio",
                    "concept_uri": "urn:ontology:entity:portfolio",
                    "concept_name": "Portfolio",
                    "definition": "Managed client portfolio.",
                }
            ],
            "rationale": "Additive ungrounded proposal",
        },
        headers=headers,
    )
    assert prop_ok.status_code == 201, prop_ok.text

    # Advance HEAD to v1.1.0 via non-breaking optional attribute addition on deployed concept
    opt_commit = client.post(
        f"/api/v1/ontology/studio/sessions/{sid}/delta",
        json={
            "parent_sha256": head_v1_sha,
            "base_version": "1.0.0",
            "target_version": "1.1.0",
            "operations": [
                {
                    "op_type": "add_attribute",
                    "op_id": "op-opt-attr",
                    "concept_uri": "urn:ontology:entity:customer",
                    "attribute_name": "preferred_locale",
                    "data_type": "string",
                    "is_optional": True,
                }
            ],
            "low_confidence_waiver": True,
            "grounding_rationale": "Optional attribute addition is permitted on deployed hub.",
        },
        headers={**headers, "If-Match": f'"{head_v1_sha}"'},
    )
    assert opt_commit.status_code == 200, opt_commit.text
    head_v1_1_sha = opt_commit.json()["target_sha256"]

    # 4. POST /proposals/{id}/rebase -> 422 ONT_EVO_CONTRACT_VIOLATION on breaking proposal,
    #    but 200 OK on ungrounded additive proposal!
    reb_break = client.post(
        f"/api/v1/ontology/studio/sessions/{sid}/proposals/prop-drop-cust/rebase",
        json={"base_sha256": head_v1_1_sha},
        headers={**headers, "If-Match": f'"{head_v1_1_sha}"'},
    )
    assert reb_break.status_code == 422, reb_break.text
    assert reb_break.json()["code"] == "ONT_EVO_CONTRACT_VIOLATION"

    reb_ok = client.post(
        f"/api/v1/ontology/studio/sessions/{sid}/proposals/prop-ungrounded-ok/rebase",
        json={"base_sha256": head_v1_1_sha},
        headers={**headers, "If-Match": f'"{head_v1_1_sha}"'},
    )
    assert reb_ok.status_code == 200, reb_ok.text

    # 5. POST /promote -> 422 ONT_EVO_CONTRACT_VIOLATION when session committed breaking change
    #    prior to DV contract deployment (e.g. mandatory attribute added before contract existed)
    dv_contract_path.unlink()
    mand_commit = client.post(
        f"/api/v1/ontology/studio/sessions/{sid}/delta",
        json={
            "parent_sha256": head_v1_1_sha,
            "base_version": "1.1.0",
            "target_version": "2.0.0",
            "operations": [breaking_cases[3][1]],
            "low_confidence_waiver": True,
            "grounding_rationale": "Committed mandatory attribute while workspace was undeployed.",
        },
        headers={**headers, "If-Match": f'"{head_v1_1_sha}"'},
    )
    assert mand_commit.status_code == 200, mand_commit.text
    head_v2_sha = mand_commit.json()["target_sha256"]

    # Re-deploy dv_contract.yaml before promoting v2.0.0 over workspace v1.0.0 -> 422!
    dv_contract_path.write_text(yaml.safe_dump(dv_contract_payload), encoding="utf-8")
    prom_v2 = client.post(
        f"/api/v1/ontology/studio/sessions/{sid}/promote",
        json={"rationale": "Attempt promoting breaking v2.0.0 over deployed hub_customer."},
        headers={
            **headers,
            "If-Match": f'"{head_v2_sha}"',
            "X-OpenDV-Workspace-If-Match": f'"{ws_v1_sha}"',
        },
    )
    assert prom_v2.status_code == 422, prom_v2.text
    assert prom_v2.json()["code"] == "ONT_EVO_CONTRACT_VIOLATION"


def test_s4_r10_maj01_corrupt_contract_fails_closed_invalid_workspace_ontology(
    studio_test_env: tuple[TestClient, dict[str, str], Path, Path],
) -> None:
    """Verify corrupt contracts/bad_contract.yaml fails closed with INVALID_WORKSPACE_ONTOLOGY."""
    client, headers, workspace_root, _studio_root = studio_test_env
    sid = "s-r10-corrupt-contract"
    head_sha = _seed_session_v1(client, headers, sid)

    # Create a pending proposal before introducing corrupt contract
    agent_headers = {**headers, "X-OpenDV-Principal": "agent:architect_bot"}
    p_resp = client.post(
        f"/api/v1/ontology/studio/sessions/{sid}/proposals",
        json={
            "proposal_id": "prop-corrupt-check",
            "operations": [
                {
                    "op_type": "add_concept",
                    "op_id": "op-cc1",
                    "concept_uri": "urn:ontology:entity:broker",
                    "concept_name": "Broker",
                    "definition": "Executing broker.",
                }
            ],
            "rationale": "Test corrupt workspace contract fail-closed behavior",
        },
        headers=agent_headers,
    )
    assert p_resp.status_code == 201, p_resp.text

    contracts_dir = workspace_root / "contracts"
    contracts_dir.mkdir(parents=True, exist_ok=True)
    (contracts_dir / "bad_contract.yaml").write_text("hubs: [unterminated\n", encoding="utf-8")

    # 1. POST /validate -> 200 with valid=False & error_code="INVALID_WORKSPACE_ONTOLOGY"
    val_resp = client.post(
        f"/api/v1/ontology/studio/sessions/{sid}/validate",
        json={
            "parent_sha256": head_sha,
            "base_version": "1.0.0",
            "target_version": "1.1.0",
            "operations": [
                {
                    "op_type": "add_concept",
                    "op_id": "op-cc2",
                    "concept_uri": "urn:ontology:entity:issuer",
                    "concept_name": "Issuer",
                    "definition": "Security issuer.",
                }
            ],
        },
        headers=headers,
    )
    assert val_resp.status_code == 200, val_resp.text
    assert val_resp.json()["valid"] is False
    assert val_resp.json()["error_code"] == "INVALID_WORKSPACE_ONTOLOGY"

    # 2. POST /delta -> 422 INVALID_WORKSPACE_ONTOLOGY
    delta_resp = client.post(
        f"/api/v1/ontology/studio/sessions/{sid}/delta",
        json={
            "parent_sha256": head_sha,
            "base_version": "1.0.0",
            "target_version": "1.1.0",
            "operations": [
                {
                    "op_type": "add_concept",
                    "op_id": "op-cc2",
                    "concept_uri": "urn:ontology:entity:issuer",
                    "concept_name": "Issuer",
                    "definition": "Security issuer.",
                }
            ],
            "low_confidence_waiver": True,
            "grounding_rationale": "Testing fail-closed corrupt contract detection.",
        },
        headers={**headers, "If-Match": f'"{head_sha}"'},
    )
    assert delta_resp.status_code == 422, delta_resp.text
    assert delta_resp.json()["code"] == "INVALID_WORKSPACE_ONTOLOGY"

    # 3. POST /proposals/{id}/accept -> 422 INVALID_WORKSPACE_ONTOLOGY
    acc_resp = client.post(
        f"/api/v1/ontology/studio/sessions/{sid}/proposals/prop-corrupt-check/accept",
        json={
            "low_confidence_waiver": True,
            "grounding_rationale": "Testing fail-closed corrupt contract on accept.",
        },
        headers={**headers, "If-Match": f'"{head_sha}"'},
    )
    assert acc_resp.status_code == 422, acc_resp.text
    assert acc_resp.json()["code"] == "INVALID_WORKSPACE_ONTOLOGY"

    # 4. POST /proposals/{id}/rebase -> 422 INVALID_WORKSPACE_ONTOLOGY
    reb_resp = client.post(
        f"/api/v1/ontology/studio/sessions/{sid}/proposals/prop-corrupt-check/rebase",
        json={"base_sha256": head_sha},
        headers={**headers, "If-Match": f'"{head_sha}"'},
    )
    assert reb_resp.status_code == 422, reb_resp.text
    assert reb_resp.json()["code"] == "INVALID_WORKSPACE_ONTOLOGY"

    # 5. POST /promote -> 422 INVALID_WORKSPACE_ONTOLOGY
    prom_resp = client.post(
        f"/api/v1/ontology/studio/sessions/{sid}/promote",
        json={"rationale": "Testing fail-closed corrupt contract on promote."},
        headers={
            **headers,
            "If-Match": f'"{head_sha}"',
            "X-OpenDV-Workspace-If-Match": f'"{"0" * 64}"',
        },
    )
    assert prom_resp.status_code == 422, prom_resp.text
    assert prom_resp.json()["code"] == "INVALID_WORKSPACE_ONTOLOGY"


def test_s4_r10_min01_and_min02_export_header_sanitization_and_corrupt_ontology_record(
    studio_test_env: tuple[TestClient, dict[str, str], Path, Path],
) -> None:
    """Verify export Content-Disposition sanitization, corrupt ontology.json, and ValueError."""
    client, headers, _workspace_root, studio_root = studio_test_env
    sid = "s-r10-min01-02"
    head_sha = _seed_session_v1(client, headers, sid)

    # S4-R10-MIN-01: trailing space in ?export_format=yaml%20 succeeds without h11 header crash
    exp_resp = client.get(
        f"/api/v1/ontology/studio/sessions/{sid}/export?export_format=yaml%20",
        headers=headers,
    )
    assert exp_resp.status_code == 200, exp_resp.text
    assert (
        exp_resp.headers.get("content-disposition") == 'attachment; filename="ontology-v1.0.0.yaml"'
    )

    # S4-R10-MIN-02 (a): Corrupt ontology.json -> 500 CORRUPT_SESSION_RECORD Problem Details
    ont_json_path = studio_root / sid / "versions" / "v1.0.0" / "ontology.json"
    orig_bytes = ont_json_path.read_bytes()
    ont_json_path.write_text("{corrupt-ontology-json", encoding="utf-8")

    exp_corrupt = client.get(
        f"/api/v1/ontology/studio/sessions/{sid}/export?export_format=json",
        headers=headers,
    )
    assert exp_corrupt.status_code == 500, exp_corrupt.text
    assert exp_corrupt.json()["code"] == "CORRUPT_SESSION_RECORD"
    assert exp_corrupt.json()["error_code"] == "CORRUPT_SESSION_RECORD"

    val_corrupt = client.post(
        f"/api/v1/ontology/studio/sessions/{sid}/validate",
        json={
            "parent_sha256": head_sha,
            "base_version": "1.0.0",
            "target_version": "1.0.1",
            "operations": [],
        },
        headers=headers,
    )
    assert val_corrupt.status_code == 500, val_corrupt.text
    assert val_corrupt.json()["code"] == "CORRUPT_SESSION_RECORD"
    ont_json_path.write_bytes(orig_bytes)

    # S4-R10-MIN-02 (b): Plain ValueError on a Studio path maps to SYNTAX_INVALID_ARGUMENT (400)
    probe_router = APIRouter()

    @probe_router.get("/api/v1/ontology/studio/_test_value_error_probe")
    def _raise_plain_value_error() -> dict[str, str]:
        raise ValueError("synthetic studio value error")

    client.app.include_router(probe_router)  # type: ignore[union-attr]
    ve_resp = client.get("/api/v1/ontology/studio/_test_value_error_probe")
    assert ve_resp.status_code == 400, ve_resp.text
    assert ve_resp.json()["code"] == "SYNTAX_INVALID_ARGUMENT"
    assert ve_resp.json()["error_code"] == "SYNTAX_INVALID_ARGUMENT"
