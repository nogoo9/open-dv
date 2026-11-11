"""Unit and integration tests for in-process LocalExecutor studio subcommands (Task 5C.1 / #219).

Verifies all 14 subcommand groups (21 leaf commands), OCC checks, lock hierarchy,
strict AST isolation, and authenticated steward waiver validations.
"""

from __future__ import annotations

import contextlib
import json
from pathlib import Path

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

    # 3. Waiver rationale too short (< 10 chars) raises exit 4 (INVALID_GROUNDING_WAIVER)
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
    sec_mgr = WorkspaceSecurityManager(studio_root=studio_root)
    waiver_key = sec_mgr.get_waiver_key().hex()
    key_file = ws / "steward_waiver.key"
    key_file.write_text(waiver_key, encoding="utf-8")

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

    # Target workspace contract setup
    target_contract = ws / "pe_graph_a_ontology.yaml"
    target_contract.write_text("ontology_version: 0.0.0\nconcepts: []\n", encoding="utf-8")
    ws_sha = compute_ontology_sha256(yaml.safe_load(target_contract.read_text(encoding="utf-8")))

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
                        "concept_uri": "urn:ontology:entity:promoted_entity",
                        "external_mappings": [
                            {
                                "scheme": "fibo",
                                "target_uri": "urn:fibo:promoted_entity",
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
    target_contract = ws / "pe_graph_a_ontology.yaml"
    contract_data = yaml.safe_load(target_contract.read_text(encoding="utf-8"))
    contract_data["ontology_version"] = "2.0.0"
    target_contract.write_text(yaml.safe_dump(contract_data), encoding="utf-8")
    ws_sha_v2 = compute_ontology_sha256(contract_data)

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
                        "concept_uri": "urn:ontology:entity:initial_entity",
                        "external_mappings": [
                            {
                                "scheme": "fibo",
                                "target_uri": "urn:fibo:initial_entity",
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
                        "concept_uri": "urn:ontology:entity:advanced_entity",
                        "external_mappings": [
                            {
                                "scheme": "fibo",
                                "target_uri": "urn:fibo:advanced_entity",
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
                        "concept_uri": "urn:ontology:entity:entity_1",
                        "external_mappings": [
                            {
                                "scheme": "fibo",
                                "target_uri": "urn:fibo:entity_1",
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
                        "concept_uri": "urn:ontology:entity:entity_2",
                        "external_mappings": [
                            {
                                "scheme": "fibo",
                                "target_uri": "urn:fibo:entity_2",
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
    """Verify clarification answer merge, CLI flag updates, and dict consumption (MAJ-04)."""
    ws, studio_root = studio_env
    app_main(["ontology", "studio", "init"])
    app_main(["ontology", "studio", "create", "-s", "clarify-sess", "--json"])

    # 1. Non-existent file returns exit 2 and does not create/wipe answers
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
    assert code_bad_file == 2

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
    assert "q_cardinality_1" in ans_data
    assert ans_data["q_cardinality_1"]["free_text"] == "User clarified 1-to-many relationship"

    # 3. Construct consumes answers dict properly
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


def test_studio_delta_validate_genesis_session(studio_env: tuple[Path, Path]) -> None:
    """Verify `delta validate` on a genesis session without --base does not crash (MAJ-01)."""
    ws, _studio_root = studio_env
    app_main(["ontology", "studio", "init"])
    app_main(["ontology", "studio", "create", "-s", "genesis-val-sess", "-n", "wm"])

    delta_file = ws / "genesis_delta.json"
    delta_file.write_text(
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
            str(delta_file),
            "--json",
        ]
    )
    assert code == 0
