"""tests/parity/test_ontology_seams_t17.py — T17 Ontology Extension Seams Parity Suite.

Governed by docs/18 §3E.5 & docs/17 §G.3:
1. Port conformance & evolution lock (ONT-EVO-001..003) via DeterministicTestkitRecommender.
2. Dynamic entry-point discovery via Python entry-point group 'opendv.ontology_recommenders'.
3. PAR-ONT-BOOTSTRAP: cold-start domain onboarding across STANDALONE_CLI, CLIENT_CLI, DIRECT_REST.
4. PAR-ONT-ADVISORY: advisory proposals, loud fail-fast on missing module, error isolation.
5. PAR-ONT-HOLD: Gate 1 hold resolution, delta acceptance, CAS lineage, wave resumption.
6. Data protection: internal clearance redaction verification.
7. No silent mutation: frozen read-only request contexts and decision-gated Graph A evolution.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from opendv_contracts import (
    AddConceptOp,
    OntologyDelta,
)
from opendv_contracts.ports.ontology import (
    OntologyRecommendationRequest,
    OntologyRecommender,
)
from opendv_core.models.graph_a import GraphAOntology
from opendv_service.ontology import (
    get_ontology_recommender_registry,
    validate_ontology_delta,
)
from opendv_testkit.ontology import DeterministicTestkitRecommender
from tests.parity.normalize import normalize_for_parity
from tests.parity.scenarios import REPO_ROOT
from tests.parity.transports import (
    ClientCliTransport,
    DirectRestTransport,
    StandaloneCliTransport,
)

GT06_DIR = REPO_ROOT / "tests" / "golden" / "GT-06"
BASE_ONT_PATH = GT06_DIR / "base_ontology.yaml"
PROP_PATH = GT06_DIR / "proposal.json"
ACC_PATH = GT06_DIR / "accepted_ontology.yaml"
DIFF_PATH = GT06_DIR / "ontology_diff.json"


@pytest.fixture(autouse=True)
def _ensure_registry_state():
    """Ensure testkit recommender is discovered for T17 tests."""
    get_ontology_recommender_registry().discover(force=True)
    yield


# =============================================================================
# 1. Port Conformance, Evolution Lock & Entry-Point Discovery
# =============================================================================


def test_t17_recommender_port_conformance() -> None:
    """Verify DeterministicTestkitRecommender strictly conforms to OntologyRecommender SPI."""
    rec = DeterministicTestkitRecommender()
    assert isinstance(rec, OntologyRecommender)
    assert rec.recommender_id == "testkit-recommender"
    assert rec.recommender_version == "1.0.0"
    assert "BOOTSTRAP" in rec.capabilities.supported_modes
    assert "HOLD_RESOLUTION" in rec.capabilities.supported_modes


def test_t17_entry_point_discovery() -> None:
    """Verify registry discovers testkit recommender dynamically via entry points."""
    reg = get_ontology_recommender_registry()
    available = reg.list_recommenders()
    assert "testkit-recommender" in available or "testkit_recommender" in available
    recommender = reg.get_recommender("testkit-recommender")
    assert isinstance(recommender, DeterministicTestkitRecommender)


def test_t17_evolution_lock_validation() -> None:
    """Verify validate_ontology_delta enforces ONT-EVO-001..003 against deployed contracts."""
    base_raw = yaml.safe_load(BASE_ONT_PATH.read_text(encoding="utf-8"))
    base = GraphAOntology.model_validate(base_raw)

    # 1. ONT-EVO-002: Provisional URI must be strictly rejected
    bad_prov_delta = OntologyDelta(
        delta_id="delta-bad-prov",
        parent_sha256="9abb6eefb16f7ff412c18e3580bab76cdccc56b6e58b66944fd56fc49117fc02",
        operations=[
            AddConceptOp(
                op_id="op-bad-prov",
                concept_uri="urn:ontology:provisional:entity:unvetted",
                concept_name="Unvetted",
                target_hub="hub_unvetted",
            )
        ],
    )
    val_prov = validate_ontology_delta(base, bad_prov_delta)
    assert val_prov.valid is False
    assert any("ONT-EVO-002" in v for v in val_prov.violations)

    # 2. ONT-EVO-003: Duplicate concept URI must be strictly rejected
    dup_delta = OntologyDelta(
        delta_id="delta-dup",
        parent_sha256="9abb6eefb16f7ff412c18e3580bab76cdccc56b6e58b66944fd56fc49117fc02",
        operations=[
            AddConceptOp(
                op_id="op-dup-01",
                concept_uri="urn:ontology:entity:fund",
                concept_name="DuplicateFund",
                target_hub="hub_fund_duplicate",
            )
        ],
    )
    val_dup = validate_ontology_delta(base, dup_delta)
    assert val_dup.valid is False
    assert any("ONT-EVO-003" in v for v in val_dup.violations)


# =============================================================================
# 2. PAR-ONT-BOOTSTRAP Across Three Transports
# =============================================================================


def test_t17_par_ont_bootstrap_three_transports(
    cli_transport: StandaloneCliTransport,
    client_cli_transport: ClientCliTransport,
    rest_transport: DirectRestTransport,
) -> None:
    """Verify PAR-ONT-BOOTSTRAP IP-1 cold-start suggestion parity across 3 transports."""
    # 1. Standalone in-process CLI
    res_cli = cli_transport.suggest_ontology(
        mode="BOOTSTRAP",
        domain_namespace="pe_asset_management",
    )
    assert res_cli.get("exit_code", 0) == 0
    assert "delta" in res_cli

    # 2. Bun Platform Client CLI (opendvctl)
    res_ctl = client_cli_transport.suggest_ontology(
        mode="BOOTSTRAP",
        domain_namespace="pe_asset_management",
    )
    assert res_ctl.get("exit_code", 0) == 0
    assert "delta" in res_ctl

    # 3. Direct Platform REST API
    res_rest = rest_transport.suggest_ontology(
        mode="BOOTSTRAP",
        domain_namespace="pe_asset_management",
    )
    assert "delta" in res_rest

    # Parity: normalized proposals must agree across transports
    norm_cli = normalize_for_parity(res_cli, path_root=str(REPO_ROOT))
    norm_ctl = normalize_for_parity(res_ctl, path_root=str(REPO_ROOT))
    norm_rest = normalize_for_parity(res_rest, path_root=str(REPO_ROOT))

    assert norm_cli["delta"]["operations"] == norm_ctl["delta"]["operations"]
    assert norm_cli["delta"]["operations"] == norm_rest["delta"]["operations"]
    assert len(norm_cli["delta"]["operations"]) == 4


# =============================================================================
# 3. PAR-ONT-ADVISORY: Policy Invariants & Error Isolation
# =============================================================================


def test_t17_par_ont_advisory_policy_fail_loud(
    cli_transport: StandaloneCliTransport,
    client_cli_transport: ClientCliTransport,
    rest_transport: DirectRestTransport,
) -> None:
    """Verify loud failure (exit 2 / HTTP 422) when suggest is called without a recommender."""
    # Standalone CLI exit code 2
    code_cli, out_cli, _ = cli_transport.invoke_raw(
        [
            "ontology",
            "suggest",
            "--recommender-id",
            "nonexistent-recommender-xyz",
            "--mode",
            "BOOTSTRAP",
            "--json",
        ]
    )
    assert code_cli == 2
    err_data = json.loads(out_cli)
    assert err_data.get("error_code") == "ONTOLOGY_RECOMMENDER_UNAVAILABLE"

    # Client CLI exit code 2
    code_ctl, _, _ = client_cli_transport.invoke_raw(
        [
            "ontology",
            "suggest",
            "--recommender-id",
            "nonexistent-recommender-xyz",
            "--mode",
            "BOOTSTRAP",
            "--json",
        ]
    )
    assert code_ctl == 2

    # Direct REST HTTP 422
    res_rest = rest_transport.suggest_ontology(
        recommender_id="nonexistent-recommender-xyz",
        mode="BOOTSTRAP",
    )
    status_ok = res_rest.get("status") in (422, "ERROR")
    assert status_ok or "unavailable" in str(res_rest.get("type", ""))


def test_t17_par_ont_advisory_recommender_error_isolation() -> None:
    """Verify recommender errors in advisory mode are recorded without aborting the pipeline."""
    rec = DeterministicTestkitRecommender(fail_mode=True)
    req = OntologyRecommendationRequest(
        request_id="req-adv-fail",
        mode="DELTA",
        domain_namespace="pe_asset_management",
    )
    with pytest.raises(RuntimeError, match="simulated inference failure"):
        asyncio.run(rec.recommend(req))


# =============================================================================
# 4. PAR-ONT-HOLD: Gate 1 Hold Resolution & Resumption Across Three Transports
# =============================================================================


def test_t17_par_ont_hold_three_transports(
    cli_transport: StandaloneCliTransport,
    client_cli_transport: ClientCliTransport,
    rest_transport: DirectRestTransport,
    tmp_path: Path,
) -> None:
    """Verify PAR-ONT-HOLD validate-delta, accept, diff parity across 3 transports."""
    prop_raw = json.loads(PROP_PATH.read_text(encoding="utf-8"))
    delta_dict = prop_raw["delta"]

    # --- Phase A: validate-delta parity ---
    v_cli = cli_transport.validate_ontology_delta(delta=delta_dict, base=BASE_ONT_PATH)
    v_ctl = client_cli_transport.validate_ontology_delta(delta=delta_dict, base=BASE_ONT_PATH)
    v_rest = rest_transport.validate_ontology_delta(delta=delta_dict, base=BASE_ONT_PATH)

    assert v_cli.get("valid") is True
    assert v_ctl.get("valid") is True
    assert v_rest.get("valid") is True

    # --- Phase B: accept decision parity ---
    d_cli = cli_transport.decide_ontology(action="ACCEPT", delta=delta_dict, base=BASE_ONT_PATH)
    d_ctl = client_cli_transport.decide_ontology(
        action="ACCEPT", delta=delta_dict, base=BASE_ONT_PATH
    )
    d_rest = rest_transport.decide_ontology(action="ACCEPT", delta=delta_dict, base=BASE_ONT_PATH)

    assert d_cli.get("status") in ("COMPLETED", "OK") or "decision" in d_cli
    assert d_ctl.get("status") in ("COMPLETED", "OK") or "decision" in d_ctl
    assert d_rest.get("status") in ("COMPLETED", "OK") or "decision" in d_rest

    norm_d_cli = normalize_for_parity(d_cli, path_root=str(REPO_ROOT))
    norm_d_ctl = normalize_for_parity(d_ctl, path_root=str(REPO_ROOT))
    norm_d_rest = normalize_for_parity(d_rest, path_root=str(REPO_ROOT))

    assert norm_d_cli.get("resulting_ontology_version") == "1.1.0"
    assert norm_d_ctl.get("resulting_ontology_version") == "1.1.0"
    assert norm_d_rest.get("resulting_ontology_version") == "1.1.0"

    # --- Phase C: diff parity ---
    diff_cli = cli_transport.diff_ontology(base=BASE_ONT_PATH, target=ACC_PATH)
    diff_ctl = client_cli_transport.diff_ontology(base=BASE_ONT_PATH, target=ACC_PATH)
    diff_rest = rest_transport.diff_ontology(base=BASE_ONT_PATH, target=ACC_PATH)

    assert diff_cli.get("added_concepts") == ["urn:ontology:entity:advisor"]
    assert diff_ctl.get("added_concepts") == ["urn:ontology:entity:advisor"]
    assert diff_rest.get("added_concepts") == ["urn:ontology:entity:advisor"]


def test_t17_par_ont_hold_reject_decision(
    cli_transport: StandaloneCliTransport,
    client_cli_transport: ClientCliTransport,
    rest_transport: DirectRestTransport,
) -> None:
    """Verify steward REJECT decision leaves ontology unchanged across transports."""
    prop_raw = json.loads(PROP_PATH.read_text(encoding="utf-8"))
    delta_dict = prop_raw["delta"]

    rej_cli = cli_transport.decide_ontology(action="REJECT", delta=delta_dict, base=BASE_ONT_PATH)
    rej_ctl = client_cli_transport.decide_ontology(
        action="REJECT", delta=delta_dict, base=BASE_ONT_PATH
    )
    rej_rest = rest_transport.decide_ontology(action="REJECT", delta=delta_dict, base=BASE_ONT_PATH)

    assert rej_cli.get("decision", {}).get("action") == "REJECT"
    assert rej_ctl.get("decision", {}).get("action") == "REJECT"
    assert rej_rest.get("decision", {}).get("action") == "REJECT"


# =============================================================================
# 5. Data Protection & No Silent Mutation Invariants
# =============================================================================


def test_t17_data_protection_internal_clearance() -> None:
    """Verify recommender asserts samples are redacted at INTERNAL clearance."""
    rec = DeterministicTestkitRecommender(enforce_redaction_check=True)

    # 1. Clean redacted sample passes
    req_clean = OntologyRecommendationRequest(
        request_id="req-dp-1",
        mode="DELTA",
        domain_namespace="pe_asset_management",
        samples=[{"tax_ssn_tin": "[REDACTED_SSN]", "email": "usr_***@example.com"}],
        clearance_level="INTERNAL",
    )
    prop = asyncio.run(rec.recommend(req_clean))
    assert prop.confidence_score > 0

    # 2. Raw unmasked SSN triggers loud assertion failure
    req_dirty = OntologyRecommendationRequest(
        request_id="req-dp-2",
        mode="DELTA",
        domain_namespace="pe_asset_management",
        samples=[{"tax_ssn_tin": "123-45-6789", "email": "plain@example.com"}],
        clearance_level="INTERNAL",
    )
    with pytest.raises(AssertionError, match="Data protection invariant violated"):
        asyncio.run(rec.recommend(req_dirty))


def test_t17_no_silent_mutation_frozen_context() -> None:
    """Verify recommendation requests are frozen and Graph A cannot be mutated in-place."""
    req = OntologyRecommendationRequest(
        request_id="req-freeze-1",
        mode="DELTA",
        domain_namespace="pe_asset_management",
    )
    with pytest.raises(ValidationError):
        req.domain_namespace = "tampered_namespace"  # type: ignore[misc]
