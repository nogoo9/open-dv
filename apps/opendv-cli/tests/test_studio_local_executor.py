"""Unit and integration tests for in-process LocalExecutor studio subcommands (Task 5C.1 / #219).

Verifies all 14 subcommand groups (21 leaf commands), OCC checks, lock hierarchy,
strict AST isolation, and authenticated steward waiver validations.
"""

from __future__ import annotations

import contextlib
import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from opendv_cli.main import app_main
from opendv_core.ontology_hashing import compute_ontology_sha256
from opendv_service.studio.workspace_security import WorkspaceSecurityManager


@pytest.fixture
def studio_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    """Provide isolated workspace and studio storage roots."""
    ws = tmp_path / "workspace"
    ws.mkdir(parents=True, exist_ok=True)
    studio_root = tmp_path / "studio_root"
    studio_root.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("OPENDV_WORKSPACE_ROOT", str(ws))
    monkeypatch.setenv("OPENDV_STUDIO_ROOT", str(studio_root))
    return ws, studio_root


def test_studio_init_and_rotation(studio_env: tuple[Path, Path]) -> None:
    """Verify `opendv ontology studio init` creates root secret and respects --force."""
    _ws, studio_root = studio_env

    # 1. Initial creation
    code = app_main(["ontology", "studio", "init", "--json"])
    assert code == 0
    assert (studio_root / ".odv_workspace_secret").is_file()
    assert (studio_root / ".local_token").is_file()

    # 2. Re-init without force fails with exit 6 (STUDIO_ALREADY_INITIALIZED)
    code_dupe = app_main(["ontology", "studio", "init", "--json"])
    assert code_dupe == 6

    # 3. Re-init with --force succeeds (rotation)
    code_force = app_main(["ontology", "studio", "init", "--force", "--json"])
    assert code_force == 0


def test_studio_create_and_lifecycle(studio_env: tuple[Path, Path]) -> None:
    """Verify session creation, list, status, and duplicate rejection."""
    _ws, _studio_root = studio_env
    app_main(["ontology", "studio", "init"])

    # 1. Create valid session
    code = app_main(
        [
            "ontology",
            "studio",
            "create",
            "-s",
            "test-session",
            "-n",
            "wealth_mgmt",
            "-o",
            "Global Wealth",
            "--json",
        ]
    )
    assert code == 0

    # 2. Duplicate session creation raises exit 6
    code_dupe = app_main(
        [
            "ontology",
            "studio",
            "create",
            "-s",
            "test-session",
            "--json",
        ]
    )
    assert code_dupe == 6

    # 3. Invalid session identifier raises exit 2
    code_invalid = app_main(
        [
            "ontology",
            "studio",
            "create",
            "-s",
            "INVALID_NAME_WITH_UPPERCASE",
            "--json",
        ]
    )
    assert code_invalid == 2

    # 4. Status inspection
    code_status = app_main(
        [
            "ontology",
            "studio",
            "status",
            "-s",
            "test-session",
            "--json",
        ]
    )
    assert code_status == 0

    # 5. Status on non-existent session raises exit 3
    code_not_found = app_main(
        [
            "ontology",
            "studio",
            "status",
            "-s",
            "non-existent-session",
            "--json",
        ]
    )
    assert code_not_found == 3

    # 6. List sessions
    code_list = app_main(["ontology", "studio", "list", "--json"])
    assert code_list == 0


def test_studio_upload_parse_clarify_answer(studio_env: tuple[Path, Path]) -> None:
    """Verify document upload, parse, clarify, and answer recording."""
    ws, _studio_root = studio_env
    app_main(["ontology", "studio", "init"])
    app_main(["ontology", "studio", "create", "-s", "s1", "--json"])

    # Create dummy source doc
    sample_doc = ws / "report.md"
    sample_doc.write_text("# Investment Advisor Report\nFund advisor guidelines.", encoding="utf-8")

    # Upload
    code_up = app_main(
        [
            "ontology",
            "studio",
            "upload",
            "-s",
            "s1",
            "-f",
            str(sample_doc),
            "--json",
        ]
    )
    assert code_up == 0

    # Parse
    code_parse = app_main(["ontology", "studio", "parse", "-s", "s1", "--json"])
    assert code_parse == 0

    # Clarify
    code_clarify = app_main(["ontology", "studio", "clarify", "-s", "s1", "--json"])
    assert code_clarify == 0

    # Answer
    ans_file = ws / "answers.json"
    ans_file.write_text(
        json.dumps([{"question_id": "q1", "selected_option_id": "opt1"}]), encoding="utf-8"
    )
    code_ans = app_main(["ontology", "studio", "answer", "-s", "s1", "-f", str(ans_file), "--json"])
    assert code_ans == 0


def test_studio_construct_and_delta(studio_env: tuple[Path, Path]) -> None:
    """Verify construction in deterministic fallback mode and delta validation."""
    _ws, _studio_root = studio_env
    app_main(["ontology", "studio", "init"])
    app_main(["ontology", "studio", "create", "-s", "s1", "--json"])

    # Construct
    code_construct = app_main(
        [
            "ontology",
            "studio",
            "construct",
            "-s",
            "s1",
            "--mode",
            "deterministic",
            "--json",
        ]
    )
    assert code_construct == 0

    # Auto-commit precondition check (requires --base-version and --base-sha256)
    code_ac_bad = app_main(
        [
            "ontology",
            "studio",
            "construct",
            "-s",
            "s1",
            "--auto-commit",
            "--json",
        ]
    )
    assert code_ac_bad == 2


def test_studio_proposal_accept_waiver_matrix(studio_env: tuple[Path, Path]) -> None:
    """Verify proposal accept OCC checks, waiver rationale length, and waiver key authentication."""
    ws, studio_root = studio_env
    app_main(["ontology", "studio", "init"])
    app_main(["ontology", "studio", "create", "-s", "s1", "--json"])

    # Read genesis session metadata
    meta_file = studio_root / "s1" / "session.json"
    meta = json.loads(meta_file.read_text(encoding="utf-8"))
    genesis_sha = meta["head_sha256"]

    # Write a test proposal into session
    prop_id = "prop-test-01"
    prop_data = {
        "proposal_id": prop_id,
        "session_id": "s1",
        "base_version": "0.0.0",
        "base_sha256": genesis_sha,
        "status": "PENDING",
        "operations": [
            {
                "op": "AddConcept",
                "concept_name": "Investment Advisor",
                "concept_uri": "urn:ontology:entity:investment_advisor",
                "definition": "Registered investment advisor under regulatory oversight.",
            }
        ],
        "proposer": "system:deterministic",
        "created_at": "2026-11-10T00:00:00Z",
    }
    props_dir = studio_root / "s1" / "proposals"
    props_dir.mkdir(parents=True, exist_ok=True)
    (props_dir / f"{prop_id}.json").write_text(json.dumps(prop_data), encoding="utf-8")

    # 1. Proposal list & show
    assert app_main(["ontology", "studio", "proposal", "list", "-s", "s1", "--json"]) == 0
    assert (
        app_main(["ontology", "studio", "proposal", "show", "-s", "s1", "-p", prop_id, "--json"])
        == 0
    )

    # 2. OCC check: wrong base-sha256 raises exit 5
    code_occ = app_main(
        [
            "ontology",
            "studio",
            "proposal",
            "accept",
            "-s",
            "s1",
            "-p",
            prop_id,
            "--base-sha256",
            "0000000000000000000000000000000000000000000000000000000000000000",
            "--json",
        ]
    )
    assert code_occ == 5

    sec_mgr = WorkspaceSecurityManager(studio_root=studio_root)
    waiver_key = sec_mgr.get_waiver_key().hex()
    key_file = ws / "steward_waiver.key"
    key_file.write_text(waiver_key, encoding="utf-8")

    # 3. Waiver without credentials checks auth/role FIRST (exit 7) BEFORE rationale length (MAJ-03)
    code_no_key_first = app_main(
        [
            "ontology",
            "studio",
            "proposal",
            "accept",
            "-s",
            "s1",
            "-p",
            prop_id,
            "--base-sha256",
            genesis_sha,
            "--low-confidence-waiver",
            "--waiver-rationale",
            "too short",
            "--json",
        ]
    )
    assert code_no_key_first == 7

    # 3b. Valid waiver key with short rationale (< 10 chars) -> exit 4 (INVALID_GROUNDING_WAIVER)
    code_short = app_main(
        [
            "ontology",
            "studio",
            "proposal",
            "accept",
            "-s",
            "s1",
            "-p",
            prop_id,
            "--base-sha256",
            genesis_sha,
            "--low-confidence-waiver",
            "--waiver-rationale",
            "too short",
            "--waiver-key-file",
            str(key_file),
            "--json",
        ]
    )
    assert code_short == 4

    # 4. Low-confidence waiver without waiver key or admin role raises exit 7
    # (FORBIDDEN_ADMIN_REQUIRED)
    code_no_cred = app_main(
        [
            "ontology",
            "studio",
            "proposal",
            "accept",
            "-s",
            "s1",
            "-p",
            prop_id,
            "--base-sha256",
            genesis_sha,
            "--low-confidence-waiver",
            "--waiver-rationale",
            "Valid rationale explaining SEC regulatory compliance.",
            "--role",
            "INTERNAL_MODELER",
            "--json",
        ]
    )
    assert code_no_cred == 7

    # 5. Low-confidence waiver with --waiver-key-file succeeds (exit 0)

    code_success = app_main(
        [
            "ontology",
            "studio",
            "proposal",
            "accept",
            "-s",
            "s1",
            "-p",
            prop_id,
            "--base-sha256",
            genesis_sha,
            "--low-confidence-waiver",
            "--waiver-rationale",
            "Valid rationale explaining SEC regulatory compliance.",
            "--waiver-key-file",
            str(key_file),
            "--json",
        ]
    )
    assert code_success == 0

    # 6. Re-accepting already accepted proposal raises exit 6 (PROPOSAL_ALREADY_RESOLVED)
    code_reaccept = app_main(
        [
            "ontology",
            "studio",
            "proposal",
            "accept",
            "-s",
            "s1",
            "-p",
            prop_id,
            "--base-sha256",
            genesis_sha,
            "--json",
        ]
    )
    assert code_reaccept == 6


def test_studio_export_promote_bundle(studio_env: tuple[Path, Path]) -> None:
    """Verify export, promote, bundle export, bundle clone, and bundle import."""
    ws, studio_root = studio_env
    app_main(["ontology", "studio", "init"])
    app_main(["ontology", "studio", "create", "-s", "s1", "--json"])

    # Export genesis
    code_exp = app_main(
        [
            "ontology",
            "studio",
            "export",
            "-s",
            "s1",
            "-e",
            "yaml",
            "--json",
        ]
    )
    assert code_exp == 0

    # Read updated head SHA
    meta = json.loads((studio_root / "s1" / "session.json").read_text(encoding="utf-8"))
    head_sha = meta["head_sha256"]

    # Target workspace contract setup (valid GraphAOntology contract)
    from opendv_core.genesis import create_genesis_ontology
    from opendv_core.models.graph_a import GraphAOntology

    target_contract = ws / "graph_a_ontology.yaml"
    genesis_ws_ont = create_genesis_ontology(
        domain_namespace="general", enterprise_owner="Enterprise Architecture"
    )
    target_contract.write_text(
        yaml.safe_dump(genesis_ws_ont.model_dump(mode="json", exclude_none=True)),
        encoding="utf-8",
    )
    ws_sha = compute_ontology_sha256(
        GraphAOntology.model_validate(yaml.safe_load(target_contract.read_text(encoding="utf-8")))
    )

    # 1. Attempt to promote uncommitted genesis draft (version 0.0.0) -> exit 4
    # Expected error code: INVALID_PROPOSAL_STATE
    code_prom_genesis = app_main(
        [
            "ontology",
            "studio",
            "promote",
            "-s",
            "s1",
            "-w",
            str(ws),
            "--workspace-sha256",
            ws_sha,
            "--head-sha256",
            head_sha,
            "-r",
            "Attempting to promote uncommitted genesis draft",
            "--json",
        ]
    )
    assert code_prom_genesis == 4

    # 2. Commit a genesis delta to bump to v1.0.0
    init_delta = ws / "init_delta.json"
    init_delta.write_text(
        json.dumps(
            {
                "operations": [
                    {
                        "op_id": "op-init",
                        "op_type": "add_concept",
                        "concept_name": "Account",
                        "concept_uri": "urn:ontology:entity:promoted_entity",
                        "external_mappings": [
                            {
                                "scheme": "fibo",
                                "target_uri": "https://spec.edmcouncil.org/fibo/ontology/FBC/ProductsAndServices/ClientsAndAccounts/Account",
                                "predicate": "skos:exactMatch",
                                "confidence": 0.95,
                            }
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    assert (
        app_main(
            ["ontology", "studio", "delta", "apply", "-s", "s1", "-f", str(init_delta), "--json"]
        )
        == 0
    )

    # Re-read committed head
    meta_committed = json.loads((studio_root / "s1" / "session.json").read_text(encoding="utf-8"))
    committed_head_sha = meta_committed["head_sha256"]

    # 3. Promote with short rationale raises exit 1
    code_prom_short = app_main(
        [
            "ontology",
            "studio",
            "promote",
            "-s",
            "s1",
            "-w",
            str(ws),
            "--workspace-sha256",
            ws_sha,
            "--head-sha256",
            committed_head_sha,
            "-r",
            "short",
            "--json",
        ]
    )
    assert code_prom_short == 1

    # 4. Promote committed version with valid rationale succeeds (exit 0)
    code_prom = app_main(
        [
            "ontology",
            "studio",
            "promote",
            "-s",
            "s1",
            "-w",
            str(ws),
            "--workspace-sha256",
            ws_sha,
            "--head-sha256",
            committed_head_sha,
            "-r",
            "Promoting verified baseline ontology to workspace",
            "--json",
        ]
    )
    assert code_prom == 0

    # 4b. Test historical contract downgrade enforcement (MAJ-01)
    # Simulate an existing higher-version workspace contract (v2.0.0)
    target_contract = ws / "graph_a_ontology.yaml"
    contract_data = yaml.safe_load(target_contract.read_text(encoding="utf-8"))
    contract_data["ontology_version"] = "2.0.0"
    target_contract.write_text(yaml.safe_dump(contract_data), encoding="utf-8")
    ws_sha_v2 = compute_ontology_sha256(GraphAOntology.model_validate(contract_data))

    # Promoting s1 (v1.0.0) without acknowledge flag fails with exit 4
    # (HISTORICAL_CONTRACT_WARNING_UNACKNOWLEDGED)
    code_downgrade_unack = app_main(
        [
            "ontology",
            "studio",
            "promote",
            "-s",
            "s1",
            "-w",
            str(ws),
            "--workspace-sha256",
            ws_sha_v2,
            "--head-sha256",
            committed_head_sha,
            "-r",
            "Attempting historical downgrade to v1.0.0",
            "--json",
        ]
    )
    assert code_downgrade_unack == 4

    # Promoting s1 with acknowledge flag but missing/short rationale fails with exit 1
    code_downgrade_no_rat = app_main(
        [
            "ontology",
            "studio",
            "promote",
            "-s",
            "s1",
            "-w",
            str(ws),
            "--workspace-sha256",
            ws_sha_v2,
            "--head-sha256",
            committed_head_sha,
            "-r",
            "Attempting historical downgrade to v1.0.0",
            "--acknowledge-historical-contract-warning",
            "--json",
        ]
    )
    assert code_downgrade_no_rat == 1

    # Promoting s1 with acknowledge flag AND valid rationale succeeds with exit 0
    code_downgrade_ok = app_main(
        [
            "ontology",
            "studio",
            "promote",
            "-s",
            "s1",
            "-w",
            str(ws),
            "--workspace-sha256",
            ws_sha_v2,
            "--head-sha256",
            committed_head_sha,
            "-r",
            "Attempting historical downgrade to v1.0.0",
            "--acknowledge-historical-contract-warning",
            "--historical-contract-rationale",
            "Steward-approved rollback to previous baseline version",
            "--json",
        ]
    )
    assert code_downgrade_ok == 0

    # Bundle export
    zip_path = ws / "bundle.zip"
    code_bexp = app_main(
        [
            "ontology",
            "studio",
            "bundle",
            "export",
            "-s",
            "s1",
            "-o",
            str(zip_path),
            "--json",
        ]
    )
    assert code_bexp == 0
    assert zip_path.is_file()

    # Bundle clone
    code_bclone = app_main(
        [
            "ontology",
            "studio",
            "bundle",
            "clone",
            "-s",
            "s1",
            "--new-session-id",
            "s1-cloned",
            "--json",
        ]
    )
    assert code_bclone == 0
    assert (studio_root / "s1-cloned" / "session.json").is_file()

    # Bundle import
    code_bimp = app_main(
        [
            "ontology",
            "studio",
            "bundle",
            "import",
            "-f",
            str(zip_path),
            "--new-session-id",
            "s1-imported",
            "--json",
        ]
    )
    assert code_bimp == 0


def test_studio_stale_proposal_and_occ_conflict(studio_env: tuple[Path, Path]) -> None:
    """Verify OCC conflict and stale proposal rejection with exit code 5 (MAJ-01, MAJ-05)."""
    ws, _studio_root = studio_env
    app_main(["ontology", "studio", "init"])
    app_main(["ontology", "studio", "create", "-s", "occ-sess", "--json"])

    # Initial head is empty greenfield genesis
    p_initial = ws / "init_delta.json"
    p_initial.write_text(
        json.dumps(
            {
                "operations": [
                    {
                        "op_id": "op-init",
                        "op_type": "add_concept",
                        "concept_name": "Account",
                        "concept_uri": "urn:ontology:entity:initial_entity",
                        "external_mappings": [
                            {
                                "scheme": "fibo",
                                "target_uri": "https://spec.edmcouncil.org/fibo/ontology/FBC/ProductsAndServices/ClientsAndAccounts/Account",
                                "predicate": "skos:exactMatch",
                                "confidence": 0.95,
                            }
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    code_init = app_main(
        ["ontology", "studio", "delta", "apply", "-s", "occ-sess", "-f", str(p_initial), "--json"]
    )
    assert code_init == 0

    # Read active head
    head_meta = json.loads(
        Path(_studio_root / "occ-sess" / "session.json").read_text(encoding="utf-8")
    )
    head_sha = head_meta["head_sha256"]
    assert head_sha

    # Construct proposal based on current head
    code_c = app_main(["ontology", "studio", "construct", "-s", "occ-sess", "--json"])
    assert code_c == 0

    # Advance head with another delta apply before proposal is accepted
    p_advance = ws / "advance_delta.json"
    p_advance.write_text(
        json.dumps(
            {
                "operations": [
                    {
                        "op_id": "op-adv",
                        "op_type": "add_concept",
                        "concept_name": "Customer",
                        "concept_uri": "urn:ontology:entity:advanced_entity",
                        "external_mappings": [
                            {
                                "scheme": "fibo",
                                "target_uri": "https://spec.edmcouncil.org/fibo/ontology/FND/ProductsAndServices/ProductsAndServices/Customer",
                                "predicate": "skos:exactMatch",
                                "confidence": 0.95,
                            }
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    code_adv = app_main(
        [
            "ontology",
            "studio",
            "delta",
            "apply",
            "-s",
            "occ-sess",
            "-f",
            str(p_advance),
            "--base-sha256",
            head_sha,
            "--json",
        ]
    )
    assert code_adv == 0

    # Now the proposal's base_sha is stale (it was constructed against head_sha, not advanced head)
    proposals_dir = _studio_root / "occ-sess" / "proposals"
    prop_files = list(proposals_dir.glob("*.json"))
    assert len(prop_files) >= 1
    pid = prop_files[0].stem

    # Attempt to accept with the original (now stale) head_sha -> exit 5 OCC mismatch
    code_stale_occ = app_main(
        [
            "ontology",
            "studio",
            "proposal",
            "accept",
            "-s",
            "occ-sess",
            "-p",
            pid,
            "--base-sha256",
            head_sha,
            "--json",
        ]
    )
    assert code_stale_occ == 5

    # Attempt to accept with new head_sha but un-rebased proposal -> exit 5 stale proposal
    new_head_meta = json.loads(
        Path(_studio_root / "occ-sess" / "session.json").read_text(encoding="utf-8")
    )
    new_head_sha = new_head_meta["head_sha256"]
    code_stale_prop = app_main(
        [
            "ontology",
            "studio",
            "proposal",
            "accept",
            "-s",
            "occ-sess",
            "-p",
            pid,
            "--base-sha256",
            new_head_sha,
            "--json",
        ]
    )
    assert code_stale_prop == 5

    # Rebase proposal onto new head
    code_rebase = app_main(
        [
            "ontology",
            "studio",
            "proposal",
            "rebase",
            "-s",
            "occ-sess",
            "-p",
            pid,
            "--head-sha256",
            new_head_sha,
            "--json",
        ]
    )
    assert code_rebase == 0

    # Acceptance now succeeds
    code_accept = app_main(
        [
            "ontology",
            "studio",
            "proposal",
            "accept",
            "-s",
            "occ-sess",
            "-p",
            pid,
            "--base-sha256",
            new_head_sha,
            "--json",
        ]
    )
    assert code_accept == 0


def test_studio_waiver_security_no_secret_leakage(studio_env: tuple[Path, Path]) -> None:
    """Verify cryptographic waiver authorization and assert raw secrets NEVER leak to disk
    (CRIT-01, MAJ-01).
    """
    ws, studio_root = studio_env
    app_main(["ontology", "studio", "init"])
    app_main(["ontology", "studio", "create", "-s", "sec-sess", "--json"])

    sec_mgr = WorkspaceSecurityManager(studio_root=studio_root)
    waiver_key_bytes = sec_mgr.get_waiver_key()
    waiver_key_hex = waiver_key_bytes.hex()

    # Write dedicated waiver key file
    wk_file = ws / "steward_waiver.key"
    wk_file.write_text(waiver_key_hex, encoding="utf-8")

    # Manually stage proposal with low confidence external mapping (< 0.7)
    props_dir = studio_root / "sec-sess" / "proposals"
    props_dir.mkdir(parents=True, exist_ok=True)
    prop_id = "prop-low-conf-1"
    prop_data = {
        "proposal_id": prop_id,
        "session_id": "sec-sess",
        "base_version": "0.0.0",
        "base_sha256": "",
        "status": "PENDING",
        "operations": [
            {
                "op_id": "op-low-1",
                "op_type": "add_concept",
                "concept_uri": "urn:ontology:entity:ungrounded_concept",
                "external_mappings": [
                    {
                        "source_system": "fibo",
                        "external_id": "fibo-test",
                        "confidence": 0.45,
                    }
                ],
            }
        ],
        "rationale": "Test low confidence evolution",
        "proposer": "system:deterministic",
    }
    (props_dir / f"{prop_id}.json").write_text(json.dumps(prop_data), encoding="utf-8")

    # 1. Attempt to accept without waiver -> exit 4 LOW_CONFIDENCE_GROUNDING
    code_no_waiver = app_main(
        [
            "ontology",
            "studio",
            "proposal",
            "accept",
            "-s",
            "sec-sess",
            "-p",
            prop_id,
            "--base-sha256",
            "",
            "--json",
        ]
    )
    assert code_no_waiver == 4

    # 2. Attempt to accept with local token or invalid key -> exit 7 FORBIDDEN_ADMIN_REQUIRED
    bogus_key_file = ws / "bogus.key"
    bogus_key_file.write_text("invalid_token_xyz_123456", encoding="utf-8")
    code_invalid_waiver = app_main(
        [
            "ontology",
            "studio",
            "proposal",
            "accept",
            "-s",
            "sec-sess",
            "-p",
            prop_id,
            "--base-sha256",
            "",
            "--low-confidence-waiver",
            "--waiver-rationale",
            "Administrative grounding waiver for ungrounded concept",
            "--waiver-key-file",
            str(bogus_key_file),
            "--json",
        ]
    )
    assert code_invalid_waiver == 7

    # 3. Accept with valid dedicated waiver key file -> exit 0
    code_valid_waiver = app_main(
        [
            "ontology",
            "studio",
            "proposal",
            "accept",
            "-s",
            "sec-sess",
            "-p",
            prop_id,
            "--base-sha256",
            "",
            "--low-confidence-waiver",
            "--waiver-rationale",
            "Administrative grounding waiver for ungrounded concept",
            "--waiver-key-file",
            str(wk_file),
            "--json",
        ]
    )
    assert code_valid_waiver == 0

    # 4. REGRESSION ASSERTION (CRIT-01): The raw waiver key bytes must NEVER appear
    # in session or bundle artifacts
    for p in studio_root.rglob("*"):
        if p.is_file() and p.name not in (".odv_workspace_secret",):
            with contextlib.suppress(OSError):
                content = p.read_text(encoding="utf-8", errors="ignore")
                assert waiver_key_hex not in content, f"Secret waiver key leaked in file: {p}"


def test_studio_delta_apply_mandatory_base_sha(studio_env: tuple[Path, Path]) -> None:
    """Verify delta apply enforces mandatory base SHA and OCC check (MAJ-05)."""
    ws, _studio_root = studio_env
    app_main(["ontology", "studio", "init"])
    app_main(["ontology", "studio", "create", "-s", "delta-occ-sess", "--json"])

    # Advance head once
    p_first = ws / "first_delta.json"
    p_first.write_text(
        json.dumps(
            {
                "operations": [
                    {
                        "op_id": "op-1",
                        "op_type": "add_concept",
                        "concept_name": "Account",
                        "concept_uri": "urn:ontology:entity:entity_1",
                        "external_mappings": [
                            {
                                "scheme": "fibo",
                                "target_uri": "https://spec.edmcouncil.org/fibo/ontology/FBC/ProductsAndServices/ClientsAndAccounts/Account",
                                "predicate": "skos:exactMatch",
                                "confidence": 0.95,
                            }
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    code_first = app_main(
        [
            "ontology",
            "studio",
            "delta",
            "apply",
            "-s",
            "delta-occ-sess",
            "-f",
            str(p_first),
            "--json",
        ]
    )
    assert code_first == 0

    # Second delta missing base_sha256 -> exit 2 MISSING_PRECONDITION
    p_second = ws / "second_delta.json"
    p_second.write_text(
        json.dumps(
            {
                "operations": [
                    {
                        "op_id": "op-2",
                        "op_type": "add_concept",
                        "concept_name": "Customer",
                        "concept_uri": "urn:ontology:entity:entity_2",
                        "external_mappings": [
                            {
                                "scheme": "fibo",
                                "target_uri": "https://spec.edmcouncil.org/fibo/ontology/FND/ProductsAndServices/ProductsAndServices/Customer",
                                "predicate": "skos:exactMatch",
                                "confidence": 0.95,
                            }
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    code_missing_base = app_main(
        [
            "ontology",
            "studio",
            "delta",
            "apply",
            "-s",
            "delta-occ-sess",
            "-f",
            str(p_second),
            "--json",
        ]
    )
    assert code_missing_base == 2

    # Second delta with mismatching base SHA -> exit 5 OCC conflict
    code_wrong_base = app_main(
        [
            "ontology",
            "studio",
            "delta",
            "apply",
            "-s",
            "delta-occ-sess",
            "-f",
            str(p_second),
            "--base-sha256",
            "wrong_base_sha_1234567890",
            "--json",
        ]
    )
    assert code_wrong_base == 5


def test_studio_clarification_answer_merge_and_flags(studio_env: tuple[Path, Path]) -> None:
    """Verify clarification answer merge, CLI flags, and wrapper consumption (CRIT-01)."""
    ws, studio_root = studio_env
    app_main(["ontology", "studio", "init"])
    app_main(["ontology", "studio", "create", "-s", "clarify-sess", "--json"])

    # 1. Non-existent file returns exit 3 (FILE_NOT_FOUND) and does not create/wipe answers
    code_bad_file = app_main(
        [
            "ontology",
            "studio",
            "answer",
            "-s",
            "clarify-sess",
            "-f",
            str(ws / "non_existent_answers.json"),
            "--json",
        ]
    )
    assert code_bad_file == 3

    # 2. Record answer via CLI flags (-q, -o, -t)
    code_flag_ans = app_main(
        [
            "ontology",
            "studio",
            "answer",
            "-s",
            "clarify-sess",
            "-q",
            "q_cardinality_1",
            "-o",
            "opt_1_to_n",
            "-t",
            "User clarified 1-to-many relationship",
            "--json",
        ]
    )
    assert code_flag_ans == 0

    ans_file = studio_root / "clarify-sess" / "clarifications" / "answers.json"
    assert ans_file.is_file()
    ans_data = json.loads(ans_file.read_text(encoding="utf-8"))
    assert isinstance(ans_data, list)
    assert len(ans_data) == 1
    assert ans_data[0]["question_id"] == "q_cardinality_1"
    assert ans_data[0]["selected_options"] == ["opt_1_to_n"]
    assert ans_data[0]["user_text"] == "User clarified 1-to-many relationship"

    # 2b. File payload with {"answers": [...]} object wrapper merges cleanly & OCC checks sha
    wrapped_file = ws / "wrapped_answers.json"
    wrapped_file.write_text(
        json.dumps(
            {
                "answers": [
                    {
                        "question_id": "q_grain_2",
                        "selected_options": ["opt_daily"],
                        "user_text": "Daily grain confirmed",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    assert (
        app_main(
            [
                "ontology",
                "studio",
                "answer",
                "-s",
                "clarify-sess",
                "-f",
                str(wrapped_file),
                "--expected-answers-sha256",
                "stale_sha_mismatch",
                "--json",
            ]
        )
        == 5
    )
    assert (
        app_main(
            [
                "ontology",
                "studio",
                "answer",
                "-s",
                "clarify-sess",
                "-f",
                str(wrapped_file),
                "--json",
            ]
        )
        == 0
    )
    merged_ans = json.loads(ans_file.read_text(encoding="utf-8"))
    assert [item["question_id"] for item in merged_ans] == ["q_cardinality_1", "q_grain_2"]

    # 3. Construct consumes canonical answers list properly
    code_construct = app_main(
        [
            "ontology",
            "studio",
            "construct",
            "-s",
            "clarify-sess",
            "--json",
        ]
    )
    assert code_construct == 0


def test_studio_path_traversal_guards(studio_env: tuple[Path, Path]) -> None:
    """Verify path traversal prevention across proposal, version, and workspace options."""
    _ws, _studio_root = studio_env
    app_main(["ontology", "studio", "init"])
    app_main(["ontology", "studio", "create", "-s", "trav-sess", "--json"])

    # 1. Proposal traversal
    code_prop_trav = app_main(
        [
            "ontology",
            "studio",
            "proposal",
            "show",
            "-s",
            "trav-sess",
            "-p",
            "../../escape",
            "--json",
        ]
    )
    assert code_prop_trav == 2

    # 2. Export version traversal
    code_exp_trav = app_main(
        [
            "ontology",
            "studio",
            "export",
            "-s",
            "trav-sess",
            "--version",
            "../../etc",
            "--json",
        ]
    )
    assert code_exp_trav == 2

    # 3. Promote workspace traversal
    code_prom_trav = app_main(
        [
            "ontology",
            "studio",
            "promote",
            "-s",
            "trav-sess",
            "-w",
            "../../../../outside_workspace",
            "--workspace-sha256",
            "dummy_sha",
            "--head-sha256",
            "dummy_sha",
            "-r",
            "Valid rationale exceeding ten characters",
            "--json",
        ]
    )
    assert code_prom_trav == 2


def test_studio_grounding_edge_cases_and_waiver_scope(studio_env: tuple[Path, Path]) -> None:
    """Verify ONT-EVO-002 grounding gate edge cases and waiver scope enforcement."""
    ws, studio_root = studio_env
    app_main(["ontology", "studio", "init"])
    app_main(["ontology", "studio", "create", "-s", "scope-sess", "--json"])

    sec_mgr = WorkspaceSecurityManager(studio_root=studio_root)
    waiver_key_hex = sec_mgr.get_waiver_key().hex()
    wk_file = ws / "waiver.key"
    wk_file.write_text(waiver_key_hex, encoding="utf-8")

    # 1. Delta with confidence 0.0 mapping -> exit 4 without waiver
    p_zero = ws / "zero_conf_delta.json"
    p_zero.write_text(
        json.dumps(
            {
                "operations": [
                    {
                        "op_id": "op-zero",
                        "op_type": "add_concept",
                        "concept_uri": "urn:ontology:entity:zero_concept",
                        "external_mappings": [
                            {"scheme": "fibo", "target_uri": "urn:fibo:zero", "confidence": 0.0}
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    assert (
        app_main(
            [
                "ontology",
                "studio",
                "delta",
                "apply",
                "-s",
                "scope-sess",
                "-f",
                str(p_zero),
                "--json",
            ]
        )
        == 4
    )

    # 2. Delta with None confidence mapping -> exit 4 without waiver
    p_none = ws / "none_conf_delta.json"
    p_none.write_text(
        json.dumps(
            {
                "operations": [
                    {
                        "op_id": "op-none",
                        "op_type": "add_concept",
                        "concept_uri": "urn:ontology:entity:none_concept",
                        "external_mappings": [
                            {"scheme": "fibo", "target_uri": "urn:fibo:none", "confidence": None}
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    assert (
        app_main(
            [
                "ontology",
                "studio",
                "delta",
                "apply",
                "-s",
                "scope-sess",
                "-f",
                str(p_none),
                "--json",
            ]
        )
        == 4
    )

    # 3. Delta with two ungrounded concepts (c1 and c2)
    p_multi = ws / "multi_ungrounded.json"
    p_multi.write_text(
        json.dumps(
            {
                "operations": [
                    {
                        "op_id": "op-c1",
                        "op_type": "add_concept",
                        "concept_uri": "urn:ontology:entity:concept_1",
                        "external_mappings": [
                            {"scheme": "fibo", "target_uri": "urn:fibo:c1", "confidence": 0.50}
                        ],
                    },
                    {
                        "op_id": "op-c2",
                        "op_type": "add_concept",
                        "concept_uri": "urn:ontology:entity:concept_2",
                        "external_mappings": [
                            {"scheme": "fibo", "target_uri": "urn:fibo:c2", "confidence": 0.55}
                        ],
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    # Partial waiver scope: only waiving concept_1 -> exit 4 (ONT_EVO_UNGROUNDED_CONCEPT)
    code_partial = app_main(
        [
            "ontology",
            "studio",
            "delta",
            "apply",
            "-s",
            "scope-sess",
            "-f",
            str(p_multi),
            "--low-confidence-waiver",
            "--grounding-rationale",
            "Steward verified partial regulatory mapping",
            "--waiver-key-file",
            str(wk_file),
            "--ungrounded-concept",
            "urn:ontology:entity:concept_1",
            "--json",
        ]
    )
    assert code_partial == 4

    # Full waiver scope: waiving both concept_1 and concept_2 -> exit 0
    code_full = app_main(
        [
            "ontology",
            "studio",
            "delta",
            "apply",
            "-s",
            "scope-sess",
            "-f",
            str(p_multi),
            "--low-confidence-waiver",
            "--grounding-rationale",
            "Steward verified comprehensive regulatory mapping for all concepts",
            "--waiver-key-file",
            str(wk_file),
            "--ungrounded-concept",
            "urn:ontology:entity:concept_1",
            "urn:ontology:entity:concept_2",
            "--json",
        ]
    )
    assert code_full == 0


def test_studio_delta_validate_genesis_session(
    studio_env: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """Verify `delta validate` on a genesis session enforces ONT-EVO-002 grounding (P12::MAJ-01)."""
    ws, _studio_root = studio_env
    app_main(["ontology", "studio", "init"])
    app_main(["ontology", "studio", "create", "-s", "genesis-val-sess", "-n", "wm"])
    capsys.readouterr()

    # 1. Ungrounded concept fails validation with exit 4 (ONT_EVO_UNGROUNDED_CONCEPT)
    ungrounded_file = ws / "genesis_ungrounded.json"
    ungrounded_file.write_text(
        json.dumps(
            {
                "delta_id": "delta-genesis-ungrounded",
                "parent_sha256": "",
                "base_version": "0.0.0",
                "target_version": "1.0.0",
                "operations": [
                    {
                        "op_type": "add_concept",
                        "concept_name": "Account",
                        "concept_uri": "urn:ontology:entity:account",
                        "definition": "A financial account",
                    }
                ],
                "author": "validator",
                "rationale": "Initial genesis concepts",
            }
        ),
        encoding="utf-8",
    )
    code_ungrounded = app_main(
        [
            "ontology",
            "studio",
            "delta",
            "validate",
            "-s",
            "genesis-val-sess",
            "-f",
            str(ungrounded_file),
            "--json",
        ]
    )
    out_ungrounded = capsys.readouterr()
    assert code_ungrounded == 4
    err_payload = json.loads(out_ungrounded.err or out_ungrounded.out)
    assert err_payload["error_code"] == "ONT_EVO_UNGROUNDED_CONCEPT"

    # 2. Grounded concept (skos:exactMatch >= 0.65) passes with exit 0
    grounded_file = ws / "genesis_delta.json"
    grounded_file.write_text(
        json.dumps(
            {
                "delta_id": "delta-genesis-val",
                "parent_sha256": "",
                "base_version": "0.0.0",
                "target_version": "1.0.0",
                "operations": [
                    {
                        "op_type": "add_concept",
                        "concept_name": "Account",
                        "concept_uri": "urn:ontology:entity:account",
                        "definition": "A financial account",
                        "external_mappings": [
                            {
                                "scheme": "fibo",
                                "target_uri": "https://spec.edmcouncil.org/fibo/ontology/FBC/ProductsAndServices/ClientsAndAccounts/Account",
                                "predicate": "skos:exactMatch",
                                "confidence": 0.85,
                            }
                        ],
                    }
                ],
                "author": "validator",
                "rationale": "Initial genesis concepts",
            }
        ),
        encoding="utf-8",
    )
    code = app_main(
        [
            "ontology",
            "studio",
            "delta",
            "validate",
            "-s",
            "genesis-val-sess",
            "-f",
            str(grounded_file),
            "--json",
        ]
    )
    assert code == 0


def _read_cli_json(capsys: pytest.CaptureFixture[str]) -> dict[str, Any]:
    """Read and parse the last JSON object emitted to stdout or stderr."""
    captured = capsys.readouterr()
    raw = (captured.out or captured.err).strip()
    lines = [ln.strip() for ln in raw.splitlines() if ln.strip().startswith("{")]
    return json.loads(lines[-1] if lines else raw)


def test_studio_grounding_threshold_065_and_predicates_p12_maj01(
    studio_env: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """Verify PATH_A_GROUNDING_THRESHOLD = 0.65 and ALLOWED_PATH_A_PREDICATES (P12::MAJ-01)."""
    ws, studio_root = studio_env
    app_main(["ontology", "studio", "init"])
    app_main(["ontology", "studio", "create", "-s", "thresh-sess", "--json"])
    capsys.readouterr()

    def _write_delta(
        name: str,
        mappings: list[dict[str, object]],
        uri_slug: str,
        concept_name: str | None = None,
    ) -> Path:
        p = ws / f"{name}.json"
        p.write_text(
            json.dumps(
                {
                    "operations": [
                        {
                            "op_type": "add_concept",
                            "concept_uri": f"urn:ontology:entity:{uri_slug}",
                            "concept_name": concept_name or uri_slug.title(),
                            "external_mappings": mappings,
                        }
                    ]
                }
            ),
            encoding="utf-8",
        )
        return p

    # 1. Confidence 0.6499 with skos:exactMatch -> exit 4 ONT_EVO_UNGROUNDED_CONCEPT
    p_below = _write_delta(
        "below_065",
        [
            {
                "scheme": "fibo",
                "target_uri": "https://spec.edmcouncil.org/fibo/ontology/FBC/ProductsAndServices/ClientsAndAccounts/Account",
                "predicate": "skos:exactMatch",
                "confidence": 0.6499,
            }
        ],
        "c_below",
        concept_name="Account",
    )
    assert (
        app_main(
            [
                "ontology",
                "studio",
                "delta",
                "apply",
                "-s",
                "thresh-sess",
                "-f",
                str(p_below),
                "--json",
            ]
        )
        == 4
    )
    err_below = _read_cli_json(capsys)
    assert err_below["error_code"] == "ONT_EVO_UNGROUNDED_CONCEPT"

    # 2. Confidence 0.99 with skos:broadMatch -> exit 4 ONT_EVO_UNGROUNDED_CONCEPT
    p_broad = _write_delta(
        "broad_099",
        [
            {
                "scheme": "fibo",
                "target_uri": "https://spec.edmcouncil.org/fibo/ontology/FBC/ProductsAndServices/ClientsAndAccounts/Account",
                "predicate": "skos:broadMatch",
                "confidence": 0.99,
            }
        ],
        "c_broad",
        concept_name="Account",
    )
    assert (
        app_main(
            [
                "ontology",
                "studio",
                "delta",
                "apply",
                "-s",
                "thresh-sess",
                "-f",
                str(p_broad),
                "--json",
            ]
        )
        == 4
    )
    err_broad = _read_cli_json(capsys)
    assert err_broad["error_code"] == "ONT_EVO_UNGROUNDED_CONCEPT"

    # 3. Exact boundary 0.65 with skos:exactMatch -> exit 0!
    p_exact_065 = _write_delta(
        "exact_065",
        [
            {
                "scheme": "fibo",
                "target_uri": "https://spec.edmcouncil.org/fibo/ontology/FBC/ProductsAndServices/ClientsAndAccounts/Account",
                "predicate": "skos:exactMatch",
                "confidence": 0.65,
            }
        ],
        "c_exact_065",
        concept_name="Account",
    )
    assert (
        app_main(
            [
                "ontology",
                "studio",
                "delta",
                "apply",
                "-s",
                "thresh-sess",
                "-f",
                str(p_exact_065),
                "--json",
            ]
        )
        == 0
    )
    capsys.readouterr()

    # 4. Dual mapping [skos:exactMatch@0.70, skos:broadMatch@0.30] -> exit 0!
    meta = json.loads((studio_root / "thresh-sess" / "session.json").read_text(encoding="utf-8"))
    p_dual = _write_delta(
        "dual_mappings",
        [
            {
                "scheme": "fibo",
                "target_uri": "https://spec.edmcouncil.org/fibo/ontology/FND/ProductsAndServices/ProductsAndServices/Customer",
                "predicate": "skos:exactMatch",
                "confidence": 0.70,
            },
            {
                "scheme": "fibo",
                "target_uri": "urn:fibo:secondary_broad",
                "predicate": "skos:broadMatch",
                "confidence": 0.30,
            },
        ],
        "c_dual",
        concept_name="Customer",
    )
    assert (
        app_main(
            [
                "ontology",
                "studio",
                "delta",
                "apply",
                "-s",
                "thresh-sess",
                "-f",
                str(p_dual),
                "--base-sha256",
                meta["head_sha256"],
                "--json",
            ]
        )
        == 0
    )


def test_studio_18_plus_2_concept_mutating_ops_and_deployed_contracts_p12_maj02(
    studio_env: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """Verify 18+2 concept-mutating ops grounding & ONT-EVO-001 deployed contracts (P12::MAJ-02)."""
    from opendv_core.genesis import create_genesis_ontology
    from opendv_core.models.graph_a import CanonicalBusinessConcept, ExternalMapping

    ws, studio_root = studio_env
    app_main(["ontology", "studio", "init"])

    # Seed workspace ontology with 1 grandfathered ungrounded concept + 1 grounded deployed concept
    seed_ont = create_genesis_ontology(
        domain_namespace="wealth",
        enterprise_owner="WealthEnterprise",
    ).model_copy(
        update={
            "ontology_version": "1.0.0",
            "canonical_business_concepts": [
                CanonicalBusinessConcept(
                    concept_uri="urn:ontology:entity:legacy_ungrounded",
                    concept_name="LegacyUngrounded",
                    definition="Grandfathered ungrounded concept",
                    target_hub="hub_legacy_ungrounded",
                ),
                CanonicalBusinessConcept(
                    concept_uri="urn:ontology:entity:account",
                    concept_name="Account",
                    definition="Deployed financial account",
                    target_hub="hub_account",
                    external_mappings=[
                        ExternalMapping(
                            scheme="fibo",
                            target_uri="urn:fibo:account",
                            predicate="skos:exactMatch",
                            confidence=0.90,
                        )
                    ],
                ),
            ],
        }
    )
    ws_ont_path = ws / "graph_a_ontology.yaml"
    ws_ont_path.write_text(
        yaml.safe_dump(seed_ont.model_dump(mode="json", exclude_none=True)),
        encoding="utf-8",
    )

    assert (
        app_main(
            [
                "ontology",
                "studio",
                "create",
                "-s",
                "gov-sess",
                "--from-workspace",
                str(ws),
                "--json",
            ]
        )
        == 0
    )
    capsys.readouterr()
    head_sha = json.loads((studio_root / "gov-sess" / "session.json").read_text(encoding="utf-8"))[
        "head_sha256"
    ]

    # 1. Touching grandfathered ungrounded concept with `refine_definition` triggers ONT-EVO-002!
    p_refine = ws / "refine_legacy.json"
    p_refine.write_text(
        json.dumps(
            {
                "operations": [
                    {
                        "op_type": "refine_definition",
                        "concept_uri": "urn:ontology:entity:legacy_ungrounded",
                        "refined_description": "Updated definition touches ungrounded concept",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    assert (
        app_main(
            [
                "ontology",
                "studio",
                "delta",
                "apply",
                "-s",
                "gov-sess",
                "-f",
                str(p_refine),
                "--base-sha256",
                head_sha,
                "--json",
            ]
        )
        == 4
    )
    err_refine = _read_cli_json(capsys)
    assert err_refine["error_code"] == "ONT_EVO_UNGROUNDED_CONCEPT"

    # 2. Stripping sole skos:exactMatch mapping via `remove_external_mapping` triggers ONT-EVO-002!
    p_strip = ws / "strip_mapping.json"
    p_strip.write_text(
        json.dumps(
            {
                "operations": [
                    {
                        "op_type": "remove_external_mapping",
                        "concept_uri": "urn:ontology:entity:account",
                        "scheme": "fibo",
                        "target_uri": "urn:fibo:account",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    assert (
        app_main(
            [
                "ontology",
                "studio",
                "delta",
                "apply",
                "-s",
                "gov-sess",
                "-f",
                str(p_strip),
                "--base-sha256",
                head_sha,
                "--json",
            ]
        )
        == 4
    )
    err_strip = _read_cli_json(capsys)
    assert err_strip["error_code"] == "ONT_EVO_UNGROUNDED_CONCEPT"

    # 3. Write deployed DV contract in workspace; `remove_concept` on deployed concept -> exit 4!
    dv_contract = ws / "dv_contract.yaml"
    dv_contract.write_text(
        yaml.safe_dump(
            {
                "hubs": [
                    {
                        "name": "hub_account",
                        "ontology_concept_uri": "urn:ontology:entity:account",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    p_remove_deployed = ws / "remove_deployed.json"
    p_remove_deployed.write_text(
        json.dumps(
            {
                "operations": [
                    {
                        "op_type": "remove_concept",
                        "concept_uri": "urn:ontology:entity:account",
                        "reason": "Attempt destructive drop of deployed Hub concept",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    assert (
        app_main(
            [
                "ontology",
                "studio",
                "delta",
                "apply",
                "-s",
                "gov-sess",
                "-f",
                str(p_remove_deployed),
                "--base-sha256",
                head_sha,
                "--json",
            ]
        )
        == 4
    )
    err_deployed = _read_cli_json(capsys)
    assert err_deployed["error_code"] == "ONT_EVO_CONTRACT_VIOLATION"


def test_studio_sticky_taint_on_reupload_p12_maj03(
    studio_env: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """Verify sticky `do_not_send_to_agent`, `src-` prefix, description, and taint_index."""
    ws, studio_root = studio_env
    app_main(["ontology", "studio", "init"])
    app_main(["ontology", "studio", "create", "-s", "taint-sess", "--json"])
    capsys.readouterr()

    doc = ws / "secret_spec.md"
    doc.write_text("# Confidential Architecture\nAccount has Customer.\n", encoding="utf-8")

    # 1. Initial upload with --confidential and description
    assert (
        app_main(
            [
                "ontology",
                "studio",
                "upload",
                "-s",
                "taint-sess",
                "-f",
                str(doc),
                "--confidential",
                "-d",
                "Confidential board architecture spec",
                "--json",
            ]
        )
        == 0
    )
    up1 = json.loads(capsys.readouterr().out)
    src_id = up1["source_id"]
    assert src_id.startswith("src-")
    assert up1["do_not_send_to_agent"] is True
    assert up1["description"] == "Confidential board architecture spec"

    sess_json = json.loads(
        (studio_root / "taint-sess" / "session.json").read_text(encoding="utf-8")
    )
    assert sess_json["taint_classification"] == "TAINTED"
    taint_idx = json.loads(
        (studio_root / "taint-sess" / "taint_index.json").read_text(encoding="utf-8")
    )
    assert src_id in taint_idx["tainted_source_ids"]

    # 2. Re-upload identical content WITHOUT --confidential -> sticky True preserved!
    assert (
        app_main(
            [
                "ontology",
                "studio",
                "upload",
                "-s",
                "taint-sess",
                "-f",
                str(doc),
                "--json",
            ]
        )
        == 0
    )
    up2 = json.loads(capsys.readouterr().out)
    assert up2["source_id"] == src_id
    assert up2["do_not_send_to_agent"] is True
    assert up2["description"] == "Confidential board architecture spec"

    sess_json_after = json.loads(
        (studio_root / "taint-sess" / "session.json").read_text(encoding="utf-8")
    )
    assert sess_json_after["taint_classification"] == "TAINTED"


def test_studio_rbac_self_acceptance_and_waiver_key_errors_p12_maj04_p13_maj01(
    studio_env: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """Verify RBAC tiers, self-acceptance guard, missing key FILE_NOT_FOUND, and auth precedence."""
    ws, studio_root = studio_env
    app_main(["ontology", "studio", "init"])
    app_main(["ontology", "studio", "create", "-s", "rbac-sess", "--json"])
    capsys.readouterr()

    # 1. Viewer role (`PUBLIC`) blocked from mutating operations -> exit 7
    assert (
        app_main(
            [
                "--role",
                "PUBLIC",
                "ontology",
                "studio",
                "create",
                "-s",
                "blocked-sess",
                "--json",
            ]
        )
        == 7
    )
    capsys.readouterr()

    doc = ws / "sample.md"
    doc.write_text("# Sample\n", encoding="utf-8")
    assert (
        app_main(
            [
                "--role",
                "PUBLIC",
                "ontology",
                "studio",
                "upload",
                "-s",
                "rbac-sess",
                "-f",
                str(doc),
                "--json",
            ]
        )
        == 7
    )
    capsys.readouterr()

    # 2. Missing --waiver-key-file reports exit 3 FILE_NOT_FOUND (not exit 7)
    p_ungrounded = ws / "ungrounded.json"
    p_ungrounded.write_text(
        json.dumps(
            {
                "operations": [
                    {
                        "op_type": "add_concept",
                        "concept_uri": "urn:ontology:entity:ungrounded_x",
                        "concept_name": "UngroundedX",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    assert (
        app_main(
            [
                "ontology",
                "studio",
                "delta",
                "apply",
                "-s",
                "rbac-sess",
                "-f",
                str(p_ungrounded),
                "--low-confidence-waiver",
                "--grounding-rationale",
                "Valid rationale exceeding ten chars",
                "--waiver-key-file",
                str(ws / "does_not_exist.key"),
                "--json",
            ]
        )
        == 3
    )
    err_missing_key = _read_cli_json(capsys)
    assert err_missing_key["error_code"] == "FILE_NOT_FOUND"

    # 3. Non-admin (`EDITOR`) waiver with short rationale fails with exit 7 BEFORE rationale exit 4!
    assert (
        app_main(
            [
                "--role",
                "EDITOR",
                "ontology",
                "studio",
                "delta",
                "apply",
                "-s",
                "rbac-sess",
                "-f",
                str(p_ungrounded),
                "--low-confidence-waiver",
                "--grounding-rationale",
                "short",
                "--json",
            ]
        )
        == 7
    )
    err_role_first = _read_cli_json(capsys)
    assert err_role_first["error_code"] == "FORBIDDEN_ADMIN_REQUIRED"

    # 4. Automated role (`INTERNAL_MODELER`) blocked from `proposal accept` -> exit 7
    sdir = studio_root / "rbac-sess"
    head_sha = json.loads((sdir / "session.json").read_text(encoding="utf-8"))["head_sha256"]
    prop_path = sdir / "proposals" / "prop-self-1.json"
    prop_path.parent.mkdir(parents=True, exist_ok=True)
    prop_path.write_text(
        json.dumps(
            {
                "proposal_id": "prop-self-1",
                "status": "PENDING",
                "proposer": "human:local_admin",
                "base_version": "0.0.0",
                "base_sha256": head_sha,
                "operations": [
                    {
                        "op_type": "add_concept",
                        "concept_uri": "urn:ontology:entity:party",
                        "concept_name": "Organization",
                        "external_mappings": [
                            {
                                "scheme": "fibo",
                                "target_uri": "https://spec.edmcouncil.org/fibo/ontology/FND/Organizations/Organizations/Organization",
                                "predicate": "skos:exactMatch",
                                "confidence": 0.90,
                            }
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    assert (
        app_main(
            [
                "--role",
                "INTERNAL_MODELER",
                "ontology",
                "studio",
                "proposal",
                "accept",
                "-s",
                "rbac-sess",
                "-p",
                "prop-self-1",
                "--base-sha256",
                head_sha,
                "--json",
            ]
        )
        == 7
    )
    err_agent_accept = _read_cli_json(capsys)
    assert err_agent_accept["error_code"] == "AGENT_PROPOSAL_ACCEPT_FORBIDDEN"

    # 5. Self-acceptance blocked by default (`PROPOSAL_SELF_ACCEPTANCE_FORBIDDEN`, exit 7)
    assert (
        app_main(
            [
                "ontology",
                "studio",
                "proposal",
                "accept",
                "-s",
                "rbac-sess",
                "-p",
                "prop-self-1",
                "--base-sha256",
                head_sha,
                "--json",
            ]
        )
        == 7
    )
    err_self = _read_cli_json(capsys)
    assert err_self["error_code"] == "PROPOSAL_SELF_ACCEPTANCE_FORBIDDEN"

    # 6. `--role EDITOR --allow-self-accepted-proposals` fails with exit 7 (admin-only override)
    assert (
        app_main(
            [
                "--role",
                "EDITOR",
                "ontology",
                "studio",
                "proposal",
                "accept",
                "-s",
                "rbac-sess",
                "-p",
                "prop-self-1",
                "--base-sha256",
                head_sha,
                "--allow-self-accepted-proposals",
                "--json",
            ]
        )
        == 7
    )
    capsys.readouterr()

    # 7. Admin with `--allow-self-accepted-proposals` succeeds with exit 0!
    assert (
        app_main(
            [
                "ontology",
                "studio",
                "proposal",
                "accept",
                "-s",
                "rbac-sess",
                "-p",
                "prop-self-1",
                "--base-sha256",
                head_sha,
                "--allow-self-accepted-proposals",
                "--json",
            ]
        )
        == 0
    )
    capsys.readouterr()

    # 8. Destructive MAJOR delta (`remove_concept`) blocked for `EDITOR` role -> exit 7
    new_head_sha = json.loads((sdir / "session.json").read_text(encoding="utf-8"))["head_sha256"]
    p_drop = ws / "drop_party.json"
    p_drop.write_text(
        json.dumps(
            {
                "operations": [
                    {
                        "op_type": "remove_concept",
                        "concept_uri": "urn:ontology:entity:party",
                        "reason": "Destructive drop test",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    assert (
        app_main(
            [
                "--role",
                "EDITOR",
                "ontology",
                "studio",
                "delta",
                "apply",
                "-s",
                "rbac-sess",
                "-f",
                str(p_drop),
                "--base-sha256",
                new_head_sha,
                "--json",
            ]
        )
        == 7
    )


def test_studio_24op_rebase_conflicts_and_corrupt_head_fail_closed_p12_maj05(
    studio_env: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """Verify 24-op `check_rebase_conflicts` and fail-closed missing head check (P12::MAJ-05)."""
    ws, studio_root = studio_env
    app_main(["ontology", "studio", "init"])
    app_main(["ontology", "studio", "create", "-s", "rebase-sess", "--json"])

    # 1. Create v1.0.0 with grounded concept c1
    p_v1 = ws / "v1.json"
    p_v1.write_text(
        json.dumps(
            {
                "operations": [
                    {
                        "op_type": "add_concept",
                        "concept_uri": "urn:ontology:entity:c1",
                        "concept_name": "Contract",
                        "definition": "Initial definition",
                        "external_mappings": [
                            {
                                "scheme": "fibo",
                                "target_uri": "https://spec.edmcouncil.org/fibo/ontology/FND/Agreements/Contracts/Contract",
                                "predicate": "skos:exactMatch",
                                "confidence": 0.88,
                            }
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    assert (
        app_main(
            ["ontology", "studio", "delta", "apply", "-s", "rebase-sess", "-f", str(p_v1), "--json"]
        )
        == 0
    )
    sdir = studio_root / "rebase-sess"
    meta_v1 = json.loads((sdir / "session.json").read_text(encoding="utf-8"))
    v1_sha = meta_v1["head_sha256"]
    v1_ver = meta_v1["head_version"]

    # Seed proposal against v1 touching `urn:ontology:entity:c1` with `refine_definition`
    prop_file = sdir / "proposals" / "prop-conflict.json"
    prop_file.parent.mkdir(parents=True, exist_ok=True)
    prop_file.write_text(
        json.dumps(
            {
                "proposal_id": "prop-conflict",
                "status": "PENDING",
                "proposer": "agent:modeler",
                "base_version": v1_ver,
                "base_sha256": v1_sha,
                "operations": [
                    {
                        "op_type": "refine_definition",
                        "concept_uri": "urn:ontology:entity:c1",
                        "refined_description": "Proposal refined description",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    # 2. Advance head with `deprecate_concept` on `urn:ontology:entity:c1` (v1.1.0)
    p_dep = ws / "deprecate_c1.json"
    p_dep.write_text(
        json.dumps(
            {
                "operations": [
                    {
                        "op_type": "deprecate_concept",
                        "concept_uri": "urn:ontology:entity:c1",
                        "reason": "Deprecated in intervening commit",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    assert (
        app_main(
            [
                "ontology",
                "studio",
                "delta",
                "apply",
                "-s",
                "rebase-sess",
                "-f",
                str(p_dep),
                "--base-sha256",
                v1_sha,
                "--json",
            ]
        )
        == 0
    )
    capsys.readouterr()

    # 3. `proposal rebase` detects 24-op conflict against `deprecate_concept` -> exit 6!
    assert (
        app_main(
            [
                "ontology",
                "studio",
                "proposal",
                "rebase",
                "-s",
                "rebase-sess",
                "-p",
                "prop-conflict",
                "--json",
            ]
        )
        == 6
    )
    err_rebase = _read_cli_json(capsys)
    assert err_rebase["error_code"] == "PROPOSAL_STALE_REBASE_REQUIRED"
    assert len(err_rebase["conflicts"]) >= 1

    # 4. Delete head ontology artifact -> `proposal rebase` fails closed with exit 1!
    meta_head = json.loads((sdir / "session.json").read_text(encoding="utf-8"))
    head_vdir = sdir / "versions" / f"v{meta_head['head_version'].lstrip('v')}"
    for fname in ("ontology.json", "ontology.yaml"):
        f = head_vdir / fname
        if f.exists():
            f.unlink()

    assert (
        app_main(
            [
                "ontology",
                "studio",
                "proposal",
                "rebase",
                "-s",
                "rebase-sess",
                "-p",
                "prop-conflict",
                "--json",
            ]
        )
        == 1
    )
    err_corrupt = _read_cli_json(capsys)
    assert err_corrupt["error_code"] == "CORRUPT_SESSION_RECORD"


def test_studio_construct_modes_delete_and_greenfield_promote(
    studio_env: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """Verify `construct --mode agent`, `NOOP_CONSTRUCT`, greenfield promote, and `delete`."""
    ws, studio_root = studio_env
    app_main(["ontology", "studio", "init"])
    app_main(["ontology", "studio", "create", "-s", "lifecycle-sess", "-n", "retail", "--json"])
    capsys.readouterr()

    sdir = studio_root / "lifecycle-sess"
    genesis_sha = json.loads((sdir / "session.json").read_text(encoding="utf-8"))["head_sha256"]

    # 1. `construct --mode agent` without agent harness fails closed (exit 1 AGENT_UNAVAILABLE)
    assert (
        app_main(
            [
                "ontology",
                "studio",
                "construct",
                "-s",
                "lifecycle-sess",
                "--mode",
                "agent",
                "--json",
            ]
        )
        == 1
    )
    err_agent = _read_cli_json(capsys)
    assert err_agent["error_code"] == "AGENT_UNAVAILABLE"

    # 2. Empty-evidence `construct --auto-commit` returns exit 0 `NOOP_CONSTRUCT` (does not fail!)
    assert (
        app_main(
            [
                "ontology",
                "studio",
                "construct",
                "-s",
                "lifecycle-sess",
                "--mode",
                "deterministic",
                "--auto-commit",
                "--base-version",
                "0.0.0",
                "--base-sha256",
                genesis_sha,
                "--json",
            ]
        )
        == 0
    )
    noop_out = json.loads(capsys.readouterr().out)
    assert noop_out["status"] == "NOOP_CONSTRUCT"

    # Advance session to v0.1.0 so we can promote a non-genesis head to greenfield workspace
    p_add = ws / "add_order.json"
    p_add.write_text(
        json.dumps(
            {
                "operations": [
                    {
                        "op_type": "add_concept",
                        "concept_uri": "urn:ontology:entity:order",
                        "concept_name": "Merchant",
                        "external_mappings": [
                            {
                                "scheme": "fibo",
                                "target_uri": "https://spec.edmcouncil.org/fibo/ontology/BE/FunctionalEntities/FunctionalEntities/Merchant",
                                "predicate": "skos:exactMatch",
                                "confidence": 0.91,
                            }
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    assert (
        app_main(
            [
                "ontology",
                "studio",
                "delta",
                "apply",
                "-s",
                "lifecycle-sess",
                "-f",
                str(p_add),
                "--json",
            ]
        )
        == 0
    )
    capsys.readouterr()
    head_sha = json.loads((sdir / "session.json").read_text(encoding="utf-8"))["head_sha256"]

    # 3. Greenfield `promote` rejects non-zero `--workspace-sha256` (5) and accepts `"0"*64` (0)
    assert (
        app_main(
            [
                "ontology",
                "studio",
                "promote",
                "-s",
                "lifecycle-sess",
                "--workspace-sha256",
                "f" * 64,
                "--head-sha256",
                head_sha,
                "-r",
                "Promote retail ontology to greenfield workspace",
                "--json",
            ]
        )
        == 5
    )
    capsys.readouterr()

    assert (
        app_main(
            [
                "ontology",
                "studio",
                "promote",
                "-s",
                "lifecycle-sess",
                "--workspace-sha256",
                "0" * 64,
                "--head-sha256",
                head_sha,
                "-r",
                "Promote retail ontology to greenfield workspace",
                "--json",
            ]
        )
        == 0
    )
    capsys.readouterr()

    # 4. `delete` on promoted session fails with exit 6 without `--force`, and 0 with `--force`!
    assert (
        app_main(
            [
                "ontology",
                "studio",
                "delete",
                "-s",
                "lifecycle-sess",
                "--json",
            ]
        )
        == 6
    )
    err_del = _read_cli_json(capsys)
    assert err_del["error_code"] == "CANNOT_DELETE_PROMOTED_SESSION"

    assert (
        app_main(
            [
                "ontology",
                "studio",
                "delete",
                "-s",
                "lifecycle-sess",
                "--force",
                "--json",
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert not sdir.exists()


def test_r3_s5_crit01_offline_fibo_grounding_verifier(
    studio_env: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """S5-CRIT-01: Fabricated FIBO URIs fail offline FiboPackGroundingVerifier with exit 4."""
    ws, studio_root = studio_env
    app_main(["ontology", "studio", "init"])
    app_main(["ontology", "studio", "create", "-s", "fibo-verify-sess", "--json"])
    capsys.readouterr()

    fake_fibo_delta = ws / "fake_fibo.json"
    fake_fibo_delta.write_text(
        json.dumps(
            {
                "operations": [
                    {
                        "op_type": "add_concept",
                        "concept_uri": "urn:ontology:entity:fake_account",
                        "concept_name": "Account",
                        "external_mappings": [
                            {
                                "scheme": "fibo",
                                "target_uri": "urn:fibo:fabricated_nonexistent_concept",
                                "predicate": "skos:exactMatch",
                                "confidence": 0.99,
                            }
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    # 1. delta validate fails with valid=False and ONT_EVO_UNGROUNDED_CONCEPT (exit 4)
    assert (
        app_main(
            [
                "ontology",
                "studio",
                "delta",
                "validate",
                "-s",
                "fibo-verify-sess",
                "-f",
                str(fake_fibo_delta),
                "--json",
            ]
        )
        == 4
    )
    val_out = _read_cli_json(capsys)
    assert val_out["valid"] is False
    assert val_out["error_code"] == "ONT_EVO_UNGROUNDED_CONCEPT"
    assert len(val_out["violations"]) >= 1

    # 2. delta apply fails with exit 4 ONT_EVO_UNGROUNDED_CONCEPT
    assert (
        app_main(
            [
                "ontology",
                "studio",
                "delta",
                "apply",
                "-s",
                "fibo-verify-sess",
                "-f",
                str(fake_fibo_delta),
                "--json",
            ]
        )
        == 4
    )
    apply_out = _read_cli_json(capsys)
    assert apply_out["error_code"] == "ONT_EVO_UNGROUNDED_CONCEPT"

    # 3. proposal accept fails with exit 4 ONT_EVO_UNGROUNDED_CONCEPT
    sdir = studio_root / "fibo-verify-sess"
    head_sha = json.loads((sdir / "session.json").read_text(encoding="utf-8"))["head_sha256"]
    prop_path = sdir / "proposals" / "prop-fake-fibo.json"
    prop_path.parent.mkdir(parents=True, exist_ok=True)
    prop_path.write_text(
        json.dumps(
            {
                "proposal_id": "prop-fake-fibo",
                "status": "PENDING",
                "proposer": "agent:internal_modeler",
                "base_version": "0.0.0",
                "base_sha256": head_sha,
                "operations": json.loads(fake_fibo_delta.read_text(encoding="utf-8"))["operations"],
            }
        ),
        encoding="utf-8",
    )
    assert (
        app_main(
            [
                "ontology",
                "studio",
                "proposal",
                "accept",
                "-s",
                "fibo-verify-sess",
                "-p",
                "prop-fake-fibo",
                "--base-sha256",
                head_sha,
                "--json",
            ]
        )
        == 4
    )
    prop_out = _read_cli_json(capsys)
    assert prop_out["error_code"] == "ONT_EVO_UNGROUNDED_CONCEPT"


def test_r3_p12a_maj01_and_maj03_role_resolution_and_normative_genesis_sha(
    studio_env: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """P12a MAJ-01 & MAJ-03: Role resolution precedence + normative genesis 0.0.0 SHA."""
    import argparse

    from opendv_cli.commands.ontology_studio import resolve_studio_principal

    _ws, _studio_root = studio_env
    monkeypatch.delenv("OPENDV_ROLE", raising=False)

    # 1. Default invocation with argparse default role="INTERNAL_MODELER" & _explicit_role=None
    ns_default = argparse.Namespace(role="INTERNAL_MODELER", _explicit_role=None)
    assert resolve_studio_principal(ns_default) == ("human:local_admin", "admin")

    # 2. Explicit CLI --role INTERNAL_MODELER resolves strictly to automated agent
    ns_explicit_agent = argparse.Namespace(
        role="INTERNAL_MODELER", _explicit_role="INTERNAL_MODELER"
    )
    assert resolve_studio_principal(ns_explicit_agent) == ("agent:internal_modeler", "automated")

    # 3. Explicit STEWARD -> editor, VIEWER -> viewer
    assert resolve_studio_principal(
        argparse.Namespace(role="STEWARD", _explicit_role="STEWARD")
    ) == ("human:local_editor", "editor")
    assert resolve_studio_principal(argparse.Namespace(role="VIEWER", _explicit_role="VIEWER")) == (
        "human:local_viewer",
        "viewer",
    )

    # 4. OPENDV_ROLE env var is NEVER shadowed by unpassed argparse default 'INTERNAL_MODELER'
    monkeypatch.setenv("OPENDV_ROLE", "VIEWER")
    assert resolve_studio_principal(ns_default) == ("human:local_viewer", "viewer")
    monkeypatch.setenv("OPENDV_ROLE", "INTERNAL_MODELER")
    assert resolve_studio_principal(ns_default) == ("agent:internal_modeler", "automated")
    monkeypatch.delenv("OPENDV_ROLE", raising=False)

    # 5. Direct unit test Namespace(role="viewer") without _explicit_role honored
    assert resolve_studio_principal(argparse.Namespace(role="viewer")) == (
        "human:local_viewer",
        "viewer",
    )

    # 6. P12a MAJ-03: Normative genesis 0.0.0 SHA-256
    app_main(["ontology", "studio", "init"])
    capsys.readouterr()
    assert app_main(["ontology", "studio", "create", "-s", "normative-gen", "--json"]) == 0
    gen_out = json.loads(capsys.readouterr().out)
    assert gen_out["domain_namespace"] == "general"
    assert gen_out["enterprise_owner"] == "Enterprise Architecture"
    assert (
        gen_out["head_sha256"] == "1916a5c6d501015984b887fd76609b225b72d5d393e5813d8df210b92ab3b4c9"
    )


def test_r3_p12a_maj02_recursive_deployment_provider_and_fail_closed_parse(
    studio_env: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """P12a MAJ-02: Recursive contract scan (including 'ontology' filenames) & fail-closed parse."""
    from opendv_core.genesis import create_genesis_ontology
    from opendv_core.models.graph_a import CanonicalBusinessConcept, ExternalMapping

    ws, studio_root = studio_env
    app_main(["ontology", "studio", "init"])

    seed_ont = create_genesis_ontology().model_copy(
        update={
            "ontology_version": "1.0.0",
            "canonical_business_concepts": [
                CanonicalBusinessConcept(
                    concept_uri="urn:ontology:entity:account",
                    concept_name="Account",
                    definition="Deployed financial account",
                    target_hub="hub_account",
                    external_mappings=[
                        ExternalMapping(
                            scheme="fibo",
                            target_uri="https://spec.edmcouncil.org/fibo/ontology/FBC/ProductsAndServices/ClientsAndAccounts/Account",
                            predicate="skos:exactMatch",
                            confidence=0.95,
                        )
                    ],
                )
            ],
        }
    )
    (ws / "graph_a_ontology.yaml").write_text(
        yaml.safe_dump(seed_ont.model_dump(mode="json", exclude_none=True)),
        encoding="utf-8",
    )

    # Nested DV contract whose filename contains "ontology"
    nested_contract = ws / "contracts" / "sub" / "core_ontology_dv.yaml"
    nested_contract.parent.mkdir(parents=True, exist_ok=True)
    nested_contract.write_text(
        yaml.safe_dump({"contract_version": "1.0.0", "hubs": [{"name": "hub_account"}]}),
        encoding="utf-8",
    )

    assert (
        app_main(
            [
                "ontology",
                "studio",
                "create",
                "-s",
                "dep-scan-sess",
                "--from-workspace",
                str(ws),
                "--json",
            ]
        )
        == 0
    )
    capsys.readouterr()
    head_sha = json.loads((studio_root / "dep-scan-sess" / "session.json").read_text("utf-8"))[
        "head_sha256"
    ]

    drop_delta = ws / "drop_deployed.json"
    drop_delta.write_text(
        json.dumps(
            {
                "operations": [
                    {
                        "op_type": "remove_concept",
                        "concept_uri": "urn:ontology:entity:account",
                        "reason": "Attempt drop of deployed hub",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    assert (
        app_main(
            [
                "ontology",
                "studio",
                "delta",
                "apply",
                "-s",
                "dep-scan-sess",
                "-f",
                str(drop_delta),
                "--base-sha256",
                head_sha,
                "--json",
            ]
        )
        == 4
    )
    assert _read_cli_json(capsys)["error_code"] == "ONT_EVO_CONTRACT_VIOLATION"

    # Malformed YAML inside contracts/ MUST fail closed (exit 4 INVALID_WORKSPACE_ONTOLOGY)
    bad_contract = ws / "contracts" / "corrupt_contract.yaml"
    bad_contract.write_text("hubs: [unterminated\n", encoding="utf-8")
    assert (
        app_main(
            [
                "ontology",
                "studio",
                "delta",
                "apply",
                "-s",
                "dep-scan-sess",
                "-f",
                str(drop_delta),
                "--base-sha256",
                head_sha,
                "--json",
            ]
        )
        == 4
    )
    assert _read_cli_json(capsys)["error_code"] == "INVALID_WORKSPACE_ONTOLOGY"


def test_r3_p12b_maj01_maj02_s5_maj02_out_containment_and_promote_isolation(
    studio_env: tuple[Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """P12b MAJ-01..02 & S5-MAJ-02: --out containment + promote namespace/lineage/noop guards."""
    from opendv_core.genesis import create_genesis_ontology
    from opendv_core.models.graph_a import GraphAOntology

    ws, studio_root = studio_env
    app_main(["ontology", "studio", "init"])
    app_main(["ontology", "studio", "create", "-s", "prom-iso", "--json"])
    capsys.readouterr()

    # 1. Relative `..` path traversal escaping workspace_root rejected (exit 2)
    monkeypatch.chdir(ws)
    assert (
        app_main(
            [
                "ontology",
                "studio",
                "export",
                "-s",
                "prom-iso",
                "-o",
                "../escaped_export.yaml",
                "--json",
            ]
        )
        == 2
    )
    assert _read_cli_json(capsys)["error_code"] == "PATH_CONTAINMENT_ERROR"

    assert (
        app_main(
            [
                "ontology",
                "studio",
                "bundle",
                "export",
                "-s",
                "prom-iso",
                "-o",
                "../escaped_bundle.zip",
                "--json",
            ]
        )
        == 2
    )
    assert _read_cli_json(capsys)["error_code"] == "PATH_CONTAINMENT_ERROR"

    # 2. OPENDV_WORKSPACE_ROOTS strict containment on absolute --out
    monkeypatch.setenv("OPENDV_WORKSPACE_ROOTS", str(ws))
    assert (
        app_main(
            [
                "ontology",
                "studio",
                "export",
                "-s",
                "prom-iso",
                "-o",
                str(ws.parent / "outside_root.yaml"),
                "--json",
            ]
        )
        == 2
    )
    assert _read_cli_json(capsys)["error_code"] == "PATH_CONTAINMENT_ERROR"
    monkeypatch.delenv("OPENDV_WORKSPACE_ROOTS", raising=False)

    # 3. Seed an unrelated `pe_graph_a_ontology.yaml` — `general` session MUST NOT touch it!
    pe_file = ws / "pe_graph_a_ontology.yaml"
    pe_ont = create_genesis_ontology(domain_namespace="private_equity", enterprise_owner="PE")
    pe_raw = yaml.safe_dump(pe_ont.model_dump(mode="json", exclude_none=True))
    pe_file.write_text(pe_raw, encoding="utf-8")

    p_add = ws / "add_acct.json"
    p_add.write_text(
        json.dumps(
            {
                "operations": [
                    {
                        "op_type": "add_concept",
                        "concept_uri": "urn:ontology:entity:account",
                        "concept_name": "Account",
                        "external_mappings": [
                            {
                                "scheme": "fibo",
                                "target_uri": "https://spec.edmcouncil.org/fibo/ontology/FBC/ProductsAndServices/ClientsAndAccounts/Account",
                                "predicate": "skos:exactMatch",
                                "confidence": 0.95,
                            }
                        ],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    assert (
        app_main(
            ["ontology", "studio", "delta", "apply", "-s", "prom-iso", "-f", str(p_add), "--json"]
        )
        == 0
    )
    capsys.readouterr()
    head_sha = json.loads((studio_root / "prom-iso" / "session.json").read_text("utf-8"))[
        "head_sha256"
    ]

    # Greenfield promote writes `general_graph_a_ontology.yaml`, leaving PE contract untouched
    gen_contract = ws / "general_graph_a_ontology.yaml"
    assert (
        app_main(
            [
                "ontology",
                "studio",
                "promote",
                "-s",
                "prom-iso",
                "-w",
                str(ws),
                "--workspace-sha256",
                "0" * 64,
                "--head-sha256",
                head_sha,
                "-r",
                "Initial greenfield promotion of general ontology",
                "--json",
            ]
        )
        == 0
    )
    capsys.readouterr()
    assert pe_file.read_text(encoding="utf-8") == pe_raw
    assert gen_contract.is_file()

    # Session status is now PROMOTED
    assert app_main(["ontology", "studio", "status", "-s", "prom-iso", "--json"]) == 0
    st_out = json.loads(capsys.readouterr().out)
    assert st_out["status"] == "PROMOTED"

    # Re-promoting identical ontology fails with exit 1 NO_OP_DELTA
    ws_sha = compute_ontology_sha256(
        GraphAOntology.model_validate(yaml.safe_load(gen_contract.read_text("utf-8")))
    )
    assert (
        app_main(
            [
                "ontology",
                "studio",
                "promote",
                "-s",
                "prom-iso",
                "-w",
                str(ws),
                "--workspace-sha256",
                ws_sha,
                "--head-sha256",
                head_sha,
                "-r",
                "Duplicate identical promotion attempt",
                "--json",
            ]
        )
        == 1
    )
    assert _read_cli_json(capsys)["error_code"] == "NO_OP_DELTA"

    # Lineage root mismatch (unrelated workspace contract) fails with exit 5 LINEAGE_ROOT_MISMATCH
    unrelated_ont = create_genesis_ontology(
        domain_namespace="unrelated_lineage", enterprise_owner="Other"
    ).model_copy(update={"ontology_version": "0.1.0"})
    gen_contract.write_text(
        yaml.safe_dump(unrelated_ont.model_dump(mode="json", exclude_none=True)),
        encoding="utf-8",
    )
    unrelated_sha = compute_ontology_sha256(unrelated_ont)
    assert (
        app_main(
            [
                "ontology",
                "studio",
                "promote",
                "-s",
                "prom-iso",
                "-w",
                str(ws),
                "--workspace-sha256",
                unrelated_sha,
                "--head-sha256",
                head_sha,
                "-r",
                "Attempt promote across mismatched lineage root",
                "--json",
            ]
        )
        == 5
    )
    assert _read_cli_json(capsys)["error_code"] == "LINEAGE_ROOT_MISMATCH"


def test_r3_s5_maj03_canonical_error_codes_and_exit_parity(
    studio_env: tuple[Path, Path], capsys: pytest.CaptureFixture[str]
) -> None:
    """S5-MAJ-03: Every error code in ontology_studio.py is in ErrorCode + exit code parity."""
    import ast

    import opendv_cli.commands.ontology_studio as studio_mod
    from opendv_contracts.errors import ErrorCode

    registered = {e.value for e in ErrorCode}
    src = Path(studio_mod.__file__).read_text(encoding="utf-8")
    tree = ast.parse(src)

    found_codes: list[str] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "_err"
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and isinstance(node.args[1].value, str)
        ):
            found_codes.append(node.args[1].value)
        elif isinstance(node, ast.Dict):
            for k, v in zip(node.keys, node.values, strict=False):
                if (
                    isinstance(k, ast.Constant)
                    and k.value == "error_code"
                    and isinstance(v, ast.Constant)
                    and isinstance(v.value, str)
                ):
                    found_codes.append(v.value)

    assert len(found_codes) >= 25
    unregistered = sorted({c for c in found_codes if c not in registered})
    assert unregistered == [], f"Unregistered error codes in ontology_studio.py: {unregistered}"

    ws, _studio_root = studio_env
    app_main(["ontology", "studio", "init"])
    app_main(["ontology", "studio", "create", "-s", "parity-sess", "--json"])
    capsys.readouterr()

    # Empty delta apply -> exit 1 NO_OP_DELTA
    empty_delta = ws / "empty_delta.json"
    empty_delta.write_text(json.dumps({"operations": []}), encoding="utf-8")
    assert (
        app_main(
            [
                "ontology",
                "studio",
                "delta",
                "apply",
                "-s",
                "parity-sess",
                "-f",
                str(empty_delta),
                "--json",
            ]
        )
        == 1
    )
    assert _read_cli_json(capsys)["error_code"] == "NO_OP_DELTA"

    # Missing upload --file -> exit 3 FILE_NOT_FOUND
    assert (
        app_main(
            [
                "ontology",
                "studio",
                "upload",
                "-s",
                "parity-sess",
                "-f",
                str(ws / "missing_doc.pdf"),
                "--json",
            ]
        )
        == 3
    )
    assert _read_cli_json(capsys)["error_code"] == "FILE_NOT_FOUND"

    # Missing bundle import --file -> exit 3 FILE_NOT_FOUND
    assert (
        app_main(
            [
                "ontology",
                "studio",
                "bundle",
                "import",
                "-f",
                str(ws / "missing_bundle.zip"),
                "--json",
            ]
        )
        == 3
    )
    assert _read_cli_json(capsys)["error_code"] == "FILE_NOT_FOUND"

    # S3-R4-MAJ-02: bundle import --force with editor role -> exit 7 FORBIDDEN_ADMIN_REQUIRED
    dummy_zip = ws / "dummy.odvstudio.zip"
    dummy_zip.write_bytes(b"PK\x05\x06" + b"\x00" * 18)
    assert (
        app_main(
            [
                "ontology",
                "studio",
                "bundle",
                "import",
                "-f",
                str(dummy_zip),
                "--force",
                "--role",
                "editor",
                "--json",
            ]
        )
        == 7
    )
    assert _read_cli_json(capsys)["error_code"] == "FORBIDDEN_ADMIN_REQUIRED"


def test_r4_p12b_maj01_bundle_clone_rbac_and_signed_audit_lineage(
    studio_env: tuple[Path, Path],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """P12b::MAJ-01: bundle clone rejects automated agents, preserves SESSION_CLONED audit,
    and never forges unverified-import admin acknowledgements.
    """
    from opendv_core.genesis import create_genesis_ontology
    from opendv_service.studio.bundle import export_bundle
    from opendv_service.studio.session_manager import StudioSessionManager
    from opendv_service.studio.workspace_security import WorkspaceSecurityManager

    ws, studio_root = studio_env
    assert app_main(["ontology", "studio", "init"]) == 0
    capsys.readouterr()

    # Create a workspace-seeded non-tainted session
    seed_ont = create_genesis_ontology(
        domain_namespace="logistics_clone", enterprise_owner="Enterprise"
    ).model_copy(update={"ontology_version": "0.1.0"})
    seed_yaml = ws / "contracts" / "ontology.yaml"
    seed_yaml.parent.mkdir(parents=True, exist_ok=True)
    seed_yaml.write_text(
        yaml.safe_dump(seed_ont.model_dump(mode="json", exclude_none=True)),
        encoding="utf-8",
    )
    assert (
        app_main(
            [
                "ontology",
                "studio",
                "create",
                "-s",
                "seed-src",
                "--from-workspace",
                str(seed_yaml),
                "--json",
            ]
        )
        == 0
    )
    created_payload = _read_cli_json(capsys)
    expected_sha = created_payload["head_sha256"]

    # a) Automated agent (`--role automated` and `OPENDV_ROLE=agent:bot`) is rejected (exit 7)
    assert (
        app_main(
            [
                "--role",
                "automated",
                "ontology",
                "studio",
                "bundle",
                "clone",
                "-s",
                "seed-src",
                "--new-session-id",
                "bot-clone-1",
                "--json",
            ]
        )
        == 7
    )
    err_auto = _read_cli_json(capsys)
    assert err_auto["error_code"] == "AGENT_OPERATION_FORBIDDEN"
    assert not (studio_root / "bot-clone-1").exists()

    monkeypatch.setenv("OPENDV_ROLE", "agent:bot")
    assert (
        app_main(
            [
                "ontology",
                "studio",
                "bundle",
                "clone",
                "-s",
                "seed-src",
                "--new-session-id",
                "bot-clone-2",
                "--json",
            ]
        )
        == 7
    )
    err_bot = _read_cli_json(capsys)
    assert err_bot["error_code"] == "AGENT_OPERATION_FORBIDDEN"
    assert not (studio_root / "bot-clone-2").exists()
    monkeypatch.delenv("OPENDV_ROLE", raising=False)

    # Viewer role is rejected with exit 7 FORBIDDEN_ADMIN_REQUIRED
    assert (
        app_main(
            [
                "--role",
                "viewer",
                "ontology",
                "studio",
                "bundle",
                "clone",
                "-s",
                "seed-src",
                "--new-session-id",
                "viewer-clone",
                "--json",
            ]
        )
        == 7
    )
    assert _read_cli_json(capsys)["error_code"] == "FORBIDDEN_ADMIN_REQUIRED"

    # b) Editor cloning a workspace-seeded non-tainted session succeeds cleanly with SESSION_CLONED
    assert (
        app_main(
            [
                "--role",
                "editor",
                "ontology",
                "studio",
                "bundle",
                "clone",
                "-s",
                "seed-src",
                "--new-session-id",
                "editor-cloned",
                "--json",
            ]
        )
        == 0
    )
    clone_res = _read_cli_json(capsys)
    assert clone_res["session_id"] == "editor-cloned"
    assert clone_res["new_session_id"] == "editor-cloned"
    assert clone_res["source_session_id"] == "seed-src"
    assert clone_res["status"] == "CLONED"
    assert clone_res["head_version"] == "0.1.0"
    assert clone_res["head_sha256"] == expected_sha

    session_mgr = StudioSessionManager(studio_root=studio_root)
    assert session_mgr.verify_audit_chain("editor-cloned") is True

    audit_lines = [
        json.loads(line)
        for line in (studio_root / "editor-cloned" / "audit.jsonl")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip()
    ]
    actions = [rec.get("action") for rec in audit_lines]
    assert "SESSION_CLONED" in actions
    assert "BUNDLE_IMPORTED" not in actions
    assert "IMPORT_ATTRIBUTION_ACKNOWLEDGED" not in actions
    for rec in audit_lines:
        details = rec.get("details") or {}
        assert details.get("unverified_attribution_acknowledged") is not True

    # Meanwhile, an editor CANNOT launder an external bundle signed by a foreign K_audit!
    ext_root = ws / "external_studio_root"
    ext_root.mkdir(parents=True, exist_ok=True)
    ext_sec = WorkspaceSecurityManager(studio_root=ext_root)
    ext_mgr = StudioSessionManager(studio_root=ext_root, security_manager=ext_sec)
    ext_mgr.create_session("foreign-sess", initial_ontology=seed_ont)
    foreign_zip = ws / "foreign.odvstudio.zip"
    export_bundle(
        session_dir=ext_mgr.get_session_dir("foreign-sess"),
        output_zip_path=foreign_zip,
        security_manager=ext_sec,
        admin=True,
        actor="human:foreign_admin",
    )

    # Without acknowledgement flag -> fails (cannot silently import unverified foreign bundle)
    code_unack = app_main(
        [
            "--role",
            "editor",
            "ontology",
            "studio",
            "bundle",
            "import",
            "-f",
            str(foreign_zip),
            "--new-session-id",
            "laundered-1",
            "--json",
        ]
    )
    assert code_unack != 0
    assert not (studio_root / "laundered-1").exists()
    capsys.readouterr()

    # With --acknowledge-unverified-import-attribution -> editor rejected (exit 7)
    assert (
        app_main(
            [
                "--role",
                "editor",
                "ontology",
                "studio",
                "bundle",
                "import",
                "-f",
                str(foreign_zip),
                "--new-session-id",
                "laundered-2",
                "--acknowledge-unverified-import-attribution",
                "--acknowledgement-rationale",
                "Attempted editor attribution override",
                "--json",
            ]
        )
        == 7
    )
    assert _read_cli_json(capsys)["error_code"] == "FORBIDDEN_ADMIN_REQUIRED"
    assert not (studio_root / "laundered-2").exists()


def test_studio_r10_answer_rbac_questions_validation_and_signed_audit(
    studio_env: tuple[Path, Path],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """S5-R10-MAJ-02: answer rejects automated/empty -q, checks questions.json, signs audit."""
    from opendv_service.studio.session_manager import StudioSessionManager

    _, studio_root = studio_env
    app_main(["ontology", "studio", "init"])
    app_main(["ontology", "studio", "create", "-s", "r10-ans-sess", "--json"])
    capsys.readouterr()

    # 1. Automated actor is strictly rejected (exit 7, AGENT_OPERATION_FORBIDDEN)
    assert (
        app_main(
            [
                "--role",
                "automated",
                "ontology",
                "studio",
                "answer",
                "-s",
                "r10-ans-sess",
                "-q",
                "q1",
                "-o",
                "opt_a",
                "--json",
            ]
        )
        == 7
    )
    assert _read_cli_json(capsys)["error_code"] == "AGENT_OPERATION_FORBIDDEN"

    monkeypatch.setenv("OPENDV_ROLE", "agent:bot")
    assert (
        app_main(
            [
                "ontology",
                "studio",
                "answer",
                "-s",
                "r10-ans-sess",
                "-q",
                "q1",
                "-o",
                "opt_a",
                "--json",
            ]
        )
        == 7
    )
    assert _read_cli_json(capsys)["error_code"] == "AGENT_OPERATION_FORBIDDEN"
    monkeypatch.delenv("OPENDV_ROLE", raising=False)

    # 2. Empty single-question `-q` (or whitespace-only `-t`) rejected with exit 2
    assert (
        app_main(
            [
                "ontology",
                "studio",
                "answer",
                "-s",
                "r10-ans-sess",
                "-q",
                "q1",
                "--json",
            ]
        )
        == 2
    )
    assert _read_cli_json(capsys)["error_code"] == "SYNTAX_INVALID_ARGUMENT"

    assert (
        app_main(
            [
                "ontology",
                "studio",
                "answer",
                "-s",
                "r10-ans-sess",
                "-q",
                "q1",
                "-t",
                "   ",
                "--json",
            ]
        )
        == 2
    )
    assert _read_cli_json(capsys)["error_code"] == "SYNTAX_INVALID_ARGUMENT"

    # 3. Seed clarifications/questions.json & verify question_id, allow_custom_text, options
    sdir = studio_root / "r10-ans-sess"
    q_file = sdir / "clarifications" / "questions.json"
    q_file.parent.mkdir(parents=True, exist_ok=True)
    q_file.write_text(
        json.dumps(
            [
                {
                    "question_id": "q_strict",
                    "prompt": "Select settlement cycle",
                    "allow_custom_text": False,
                    "options": [
                        {"option_id": "opt_t1", "label": "T+1"},
                        {"option_id": "opt_t2", "label": "T+2"},
                    ],
                }
            ]
        ),
        encoding="utf-8",
    )

    # Unknown question_id -> exit 3 RESOURCE_NOT_FOUND
    assert (
        app_main(
            [
                "ontology",
                "studio",
                "answer",
                "-s",
                "r10-ans-sess",
                "-q",
                "q_nonexistent",
                "-o",
                "opt_t1",
                "--json",
            ]
        )
        == 3
    )
    assert _read_cli_json(capsys)["error_code"] == "RESOURCE_NOT_FOUND"

    # Custom text when allow_custom_text is False -> exit 2 SYNTAX_INVALID_ARGUMENT
    assert (
        app_main(
            [
                "ontology",
                "studio",
                "answer",
                "-s",
                "r10-ans-sess",
                "-q",
                "q_strict",
                "-o",
                "opt_t1",
                "-t",
                "Custom T+0 override",
                "--json",
            ]
        )
        == 2
    )
    assert _read_cli_json(capsys)["error_code"] == "SYNTAX_INVALID_ARGUMENT"

    # Invalid option -> exit 2 SYNTAX_INVALID_ARGUMENT
    assert (
        app_main(
            [
                "ontology",
                "studio",
                "answer",
                "-s",
                "r10-ans-sess",
                "-q",
                "q_strict",
                "-o",
                "opt_invalid",
                "--json",
            ]
        )
        == 2
    )
    assert _read_cli_json(capsys)["error_code"] == "SYNTAX_INVALID_ARGUMENT"

    # Valid option succeeds (exit 0) and appends signed CLARIFICATION_ANSWERS_SAVED audit entry
    assert (
        app_main(
            [
                "ontology",
                "studio",
                "answer",
                "-s",
                "r10-ans-sess",
                "-q",
                "q_strict",
                "-o",
                "opt_t1",
                "--json",
            ]
        )
        == 0
    )
    ans_out = _read_cli_json(capsys)
    assert ans_out["status"] == "ACCEPTED"
    assert ans_out["answers_recorded"] == 1

    session_mgr = StudioSessionManager(studio_root=studio_root)
    assert session_mgr.verify_audit_chain("r10-ans-sess") is True
    audit_records = [
        json.loads(line)
        for line in (sdir / "audit.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    ans_audits = [r for r in audit_records if r.get("action") == "CLARIFICATION_ANSWERS_SAVED"]
    assert len(ans_audits) == 1
    assert ans_audits[0]["details"]["answered_count"] == 1
    assert ans_audits[0]["details"]["answers_sha256"] == ans_out["answers_sha256"]


def test_studio_r10_upload_metadata_overwrite_rebase_deployed_contract_and_agent_rbac(
    studio_env: tuple[Path, Path],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """S5-R10-MAJ-02 & MIN-03: upload metadata guard, rebase ONT-EVO-001, and agent RBAC."""
    ws, studio_root = studio_env
    app_main(["ontology", "studio", "init"])
    app_main(["ontology", "studio", "create", "-s", "r10-gov-sess", "--json"])
    capsys.readouterr()

    # 1. Human uploads source with description; automated agent cannot overwrite description
    src_file = ws / "domain_glossary.txt"
    src_file.write_text("Account: core ledger entity.\n", encoding="utf-8")
    assert (
        app_main(
            [
                "ontology",
                "studio",
                "upload",
                "-s",
                "r10-gov-sess",
                "-f",
                str(src_file),
                "--description",
                "Steward curated glossary",
                "--json",
            ]
        )
        == 0
    )
    capsys.readouterr()

    assert (
        app_main(
            [
                "--role",
                "automated",
                "ontology",
                "studio",
                "upload",
                "-s",
                "r10-gov-sess",
                "-f",
                str(src_file),
                "--description",
                "Bot overwritten metadata",
                "--json",
            ]
        )
        == 7
    )
    assert _read_cli_json(capsys)["error_code"] == "AGENT_METADATA_OVERWRITE_FORBIDDEN"

    # Automated re-upload without --description succeeds and preserves human description
    assert (
        app_main(
            [
                "--role",
                "automated",
                "ontology",
                "studio",
                "upload",
                "-s",
                "r10-gov-sess",
                "-f",
                str(src_file),
                "--json",
            ]
        )
        == 0
    )
    up_ok = _read_cli_json(capsys)
    assert up_ok["description"] == "Steward curated glossary"

    # 2. Seed v1.0.0 with two grounded Contract concepts (c1 + c_keep)
    p_v1 = ws / "r10_v1.json"
    p_v1.write_text(
        json.dumps(
            {
                "operations": [
                    {
                        "op_type": "add_concept",
                        "concept_uri": "urn:ontology:entity:c1",
                        "concept_name": "Contract",
                        "target_hub": "hub_contract",
                        "definition": "Initial definition",
                        "external_mappings": [
                            {
                                "scheme": "fibo",
                                "target_uri": (
                                    "https://spec.edmcouncil.org/fibo/ontology/"
                                    "FND/Agreements/Contracts/Contract"
                                ),
                                "predicate": "skos:exactMatch",
                                "confidence": 0.88,
                            }
                        ],
                    },
                    {
                        "op_type": "add_concept",
                        "concept_uri": "urn:ontology:entity:c_keep",
                        "concept_name": "Contract",
                        "target_hub": "hub_master_contract",
                        "definition": "Retained master contract definition",
                        "external_mappings": [
                            {
                                "scheme": "fibo",
                                "target_uri": (
                                    "https://spec.edmcouncil.org/fibo/ontology/"
                                    "FND/Agreements/Contracts/Contract"
                                ),
                                "predicate": "skos:exactMatch",
                                "confidence": 0.88,
                            }
                        ],
                    },
                ]
            }
        ),
        encoding="utf-8",
    )

    # S5-R10-MIN-03: automated role on delta apply -> exit 7 AGENT_OPERATION_FORBIDDEN
    assert (
        app_main(
            [
                "--role",
                "automated",
                "ontology",
                "studio",
                "delta",
                "apply",
                "-s",
                "r10-gov-sess",
                "-f",
                str(p_v1),
                "--json",
            ]
        )
        == 7
    )
    assert _read_cli_json(capsys)["error_code"] == "AGENT_OPERATION_FORBIDDEN"

    assert (
        app_main(
            [
                "ontology",
                "studio",
                "delta",
                "apply",
                "-s",
                "r10-gov-sess",
                "-f",
                str(p_v1),
                "--json",
            ]
        )
        == 0
    )
    capsys.readouterr()

    sdir = studio_root / "r10-gov-sess"
    meta_v1 = json.loads((sdir / "session.json").read_text(encoding="utf-8"))
    v1_sha = meta_v1["head_sha256"]
    v1_ver = meta_v1["head_version"]

    # Seed proposal against v1.0.0 removing `urn:ontology:entity:c1`
    prop_file = sdir / "proposals" / "prop-drop-deployed.json"
    prop_file.parent.mkdir(parents=True, exist_ok=True)
    prop_file.write_text(
        json.dumps(
            {
                "proposal_id": "prop-drop-deployed",
                "status": "PENDING",
                "proposer": "agent:modeler",
                "base_version": v1_ver,
                "base_sha256": v1_sha,
                "operations": [
                    {
                        "op_type": "remove_concept",
                        "concept_uri": "urn:ontology:entity:c1",
                        "reason": "Drop deployed hub concept",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    # S5-R10-MIN-03: automated role on reject & rebase -> exit 7 AGENT_OPERATION_FORBIDDEN
    assert (
        app_main(
            [
                "--role",
                "automated",
                "ontology",
                "studio",
                "proposal",
                "reject",
                "-s",
                "r10-gov-sess",
                "-p",
                "prop-drop-deployed",
                "--json",
            ]
        )
        == 7
    )
    assert _read_cli_json(capsys)["error_code"] == "AGENT_OPERATION_FORBIDDEN"

    assert (
        app_main(
            [
                "--role",
                "automated",
                "ontology",
                "studio",
                "proposal",
                "rebase",
                "-s",
                "r10-gov-sess",
                "-p",
                "prop-drop-deployed",
                "--json",
            ]
        )
        == 7
    )
    assert _read_cli_json(capsys)["error_code"] == "AGENT_OPERATION_FORBIDDEN"

    # Advance session head with an additive reference enumeration
    p_v1_1 = ws / "r10_v1_1.json"
    p_v1_1.write_text(
        json.dumps(
            {
                "operations": [
                    {
                        "op_type": "add_reference_enumeration",
                        "enum_uri": "urn:ontology:enum:currency",
                        "name": "currency",
                        "allowed_codes": ["USD", "EUR"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    assert (
        app_main(
            [
                "ontology",
                "studio",
                "delta",
                "apply",
                "-s",
                "r10-gov-sess",
                "-f",
                str(p_v1_1),
                "--base-sha256",
                v1_sha,
                "--json",
            ]
        )
        == 0
    )
    capsys.readouterr()

    # Bind c1 as a deployed Hub in workspace dv_contract.yaml -> rebase fails with exit 4!
    (ws / "dv_contract.yaml").write_text(
        yaml.safe_dump(
            {
                "hubs": [
                    {
                        "name": "hub_contract",
                        "ontology_concept_uri": "urn:ontology:entity:c1",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )

    assert (
        app_main(
            [
                "ontology",
                "studio",
                "proposal",
                "rebase",
                "-s",
                "r10-gov-sess",
                "-p",
                "prop-drop-deployed",
                "--json",
            ]
        )
        == 4
    )
    err_rebase_dep = _read_cli_json(capsys)
    assert err_rebase_dep["error_code"] == "ONT_EVO_CONTRACT_VIOLATION"
    # Proposal on disk remains un-rebased at v1_sha
    assert json.loads(prop_file.read_text(encoding="utf-8"))["base_sha256"] == v1_sha
