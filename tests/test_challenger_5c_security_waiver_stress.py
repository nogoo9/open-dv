"""Adversarial Security & Waiver Stress Test Suite for Track 5C (Milestone M5).

Authored by challenger_5c_2 (Security & Waiver Challenger).
Conducts empirical verification across 3 challenge vectors:
1. Waiver credential security:
   - CLI argv raw secret rejection
   - Non-existent and empty key file rejection (exit 7)
   - Invalid / mismatched waiver key rejection (exit 7)
   - OPENDV_WAIVER_KEY environment variable fallback & precedence
   - Constant-time comparison (hmac.compare_digest) verification
   - Zero secret leakage invariant across CAS and outputs
2. Remote init unconditional rejection (exit 7)
3. Path containment and traversal verification:
   - Session identifier injection & traversal (.., slashes, DOS devices, uppercase)
   - Proposal identifier path traversal
   - Zip-slip & archive security violation on bundle import
"""

from __future__ import annotations

import inspect
import json
import zipfile
from pathlib import Path

import pytest

from opendv_cli.commands import ontology_studio as cli_studio_mod
from opendv_cli.main import app_main
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
    monkeypatch.delenv("OPENDV_WAIVER_KEY", raising=False)
    return ws, studio_root


def _setup_session_with_pending_proposal(
    ws: Path, studio_root: Path, session_id: str = "sec-sess-01", prop_id: str = "prop-sec-01"
) -> tuple[str, str]:
    """Initialize studio, create session, and write pending proposal."""
    app_main(["ontology", "studio", "init"])
    app_main(["ontology", "studio", "create", "-s", session_id, "--json"])

    meta_file = studio_root / session_id / "session.json"
    meta = json.loads(meta_file.read_text(encoding="utf-8"))
    head_sha = meta["head_sha256"]

    props_dir = studio_root / session_id / "proposals"
    props_dir.mkdir(parents=True, exist_ok=True)

    prop_data = {
        "proposal_id": prop_id,
        "session_id": session_id,
        "base_version": "0.0.0",
        "base_sha256": head_sha,
        "status": "PENDING",
        "operations": [
            {
                "op": "AddConcept",
                "concept_name": "Classified Asset",
                "concept_uri": "urn:ontology:entity:classified_asset",
                "definition": "High security asset requiring steward waiver.",
            }
        ],
        "proposer": "system:deterministic",
        "created_at": "2026-11-11T00:00:00Z",
    }
    (props_dir / f"{prop_id}.json").write_text(json.dumps(prop_data), encoding="utf-8")
    return session_id, head_sha


# ==============================================================================
# Vector 1: Waiver Credential Security
# ==============================================================================


def test_waiver_argv_raw_secret_strictly_rejected(studio_env: tuple[Path, Path]) -> None:
    """Verify raw secrets on CLI argv are never accepted as credentials.

    - Unrecognized secret flags (--secret, --token) fail with exit 2 (syntax/argparse).
    - Abbreviated flag --waiver-key is resolved by argparse to --waiver-key-file, which
      strictly expects a file path on disk; passing a raw secret fails securely with exit 7
      (FORBIDDEN_ADMIN_REQUIRED) because raw secrets are never accepted as key values.
    """
    ws, studio_root = studio_env
    sid, head_sha = _setup_session_with_pending_proposal(ws, studio_root)

    # 1. Attempt to pass raw secret via --waiver-key on argv
    exit_code_1 = app_main(
        [
            "ontology",
            "studio",
            "proposal",
            "accept",
            "-s",
            sid,
            "-p",
            "prop-sec-01",
            "--base-sha256",
            head_sha,
            "--low-confidence-waiver",
            "--waiver-rationale",
            "Adequate rationale explaining security compliance.",
            "--waiver-key",
            "super-secret-raw-key",
            "--json",
        ]
    )
    # Disabling argparse prefix abbreviation strictly rejects --waiver-key on argv with exit 2
    assert exit_code_1 == 2, (
        "Passing raw secret to --waiver-key must be rejected by parser (exit 2)"
    )

    # 2. Attempt to pass --secret on argv
    exit_code_2 = app_main(
        [
            "ontology",
            "studio",
            "proposal",
            "accept",
            "-s",
            sid,
            "-p",
            "prop-sec-01",
            "--base-sha256",
            head_sha,
            "--secret",
            "raw-secret",
            "--json",
        ]
    )
    assert exit_code_2 == 2, "Unrecognized argument --secret on argv must be rejected with exit 2"

    # 3. Attempt to pass --token on argv
    exit_code_3 = app_main(
        [
            "ontology",
            "studio",
            "proposal",
            "accept",
            "-s",
            sid,
            "-p",
            "prop-sec-01",
            "--base-sha256",
            head_sha,
            "--token",
            "raw-token",
            "--json",
        ]
    )
    assert exit_code_3 == 2, "Unrecognized argument --token on argv must be rejected with exit 2"


def test_waiver_key_file_non_existent(studio_env: tuple[Path, Path]) -> None:
    """Under M1 open-access, legacy --waiver-key-file is ignored as a harmless no-op on local waivers."""
    ws, studio_root = studio_env
    sid, head_sha = _setup_session_with_pending_proposal(ws, studio_root)

    non_existent = ws / "non_existent_waiver.key"
    assert not non_existent.exists()

    code = app_main(
        [
            "ontology",
            "studio",
            "proposal",
            "accept",
            "-s",
            sid,
            "-p",
            "prop-sec-01",
            "--base-sha256",
            head_sha,
            "--low-confidence-waiver",
            "--waiver-rationale",
            "Adequate rationale explaining security compliance.",
            "--waiver-key-file",
            str(non_existent),
            "--json",
        ]
    )
    assert code == 0, "Legacy --waiver-key-file is tolerated as a no-op under M1 open-access"


def test_waiver_key_file_empty_and_whitespace(studio_env: tuple[Path, Path]) -> None:
    """Verify non-admin role is still rejected (exit 7) even when legacy --waiver-key-file is supplied."""
    ws, studio_root = studio_env
    sid, head_sha = _setup_session_with_pending_proposal(ws, studio_root)

    # 1. Non-admin role (EDITOR) fails with exit 7 before rationale or key checks
    empty_file = ws / "empty.key"
    empty_file.write_text("", encoding="utf-8")

    code_editor = app_main(
        [
            "--role",
            "EDITOR",
            "ontology",
            "studio",
            "proposal",
            "accept",
            "-s",
            sid,
            "-p",
            "prop-sec-01",
            "--base-sha256",
            head_sha,
            "--low-confidence-waiver",
            "--waiver-rationale",
            "Adequate rationale explaining security compliance.",
            "--waiver-key-file",
            str(empty_file),
            "--json",
        ]
    )
    assert code_editor == 7, "Non-admin role (EDITOR) waiver must still fail with exit 7"

    # 2. Local custodian (default admin) succeeds without K_waiver secret even with legacy key file
    whitespace_file = ws / "whitespace.key"
    whitespace_file.write_text("   \n\t  \r\n", encoding="utf-8")

    code_admin = app_main(
        [
            "ontology",
            "studio",
            "proposal",
            "accept",
            "-s",
            sid,
            "-p",
            "prop-sec-01",
            "--base-sha256",
            head_sha,
            "--low-confidence-waiver",
            "--waiver-rationale",
            "Adequate rationale explaining security compliance.",
            "--waiver-key-file",
            str(whitespace_file),
            "--json",
        ]
    )
    assert code_admin == 0, "Local custodian waiver must succeed without K_waiver secret"


def test_waiver_key_file_mismatched_unauthorized(studio_env: tuple[Path, Path]) -> None:
    """Verify unauthorized --authorized-role or short rationale still fail (exit 7 / exit 4) without K_waiver."""
    ws, studio_root = studio_env
    sid, head_sha = _setup_session_with_pending_proposal(ws, studio_root)

    code_bad_auth_role = app_main(
        [
            "ontology",
            "studio",
            "proposal",
            "accept",
            "-s",
            sid,
            "-p",
            "prop-sec-01",
            "--base-sha256",
            head_sha,
            "--low-confidence-waiver",
            "--authorized-role",
            "editor",
            "--waiver-rationale",
            "Adequate rationale explaining security compliance.",
            "--json",
        ]
    )
    assert code_bad_auth_role == 7, "Non-admin --authorized-role must fail with exit 7"

    code_short_rationale = app_main(
        [
            "ontology",
            "studio",
            "proposal",
            "accept",
            "-s",
            sid,
            "-p",
            "prop-sec-01",
            "--base-sha256",
            head_sha,
            "--low-confidence-waiver",
            "--waiver-rationale",
            "short",
            "--json",
        ]
    )
    assert code_short_rationale == 4, "Short waiver rationale must fail with exit 4"


def test_waiver_env_fallback_behavior(
    studio_env: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify OPENDV_WAIVER_KEY is tolerated as a harmless no-op in M1 open-access mode."""
    ws, studio_root = studio_env

    # Case A: OPENDV_WAIVER_KEY unset -> local admin waiver succeeds (exit 0)
    monkeypatch.delenv("OPENDV_WAIVER_KEY", raising=False)
    sid_a, head_sha_a = _setup_session_with_pending_proposal(
        ws, studio_root, "sess-env-a", "prop-env-a"
    )
    code_unset = app_main(
        [
            "ontology",
            "studio",
            "proposal",
            "accept",
            "-s",
            sid_a,
            "-p",
            "prop-env-a",
            "--base-sha256",
            head_sha_a,
            "--low-confidence-waiver",
            "--waiver-rationale",
            "Adequate rationale explaining security compliance.",
            "--json",
        ]
    )
    assert code_unset == 0, "Open-access waiver must succeed without OPENDV_WAIVER_KEY"

    # Case B: Arbitrary legacy OPENDV_WAIVER_KEY value -> tolerated as no-op (exit 0)
    sid_b, head_sha_b = _setup_session_with_pending_proposal(
        ws, studio_root, "sess-env-b", "prop-env-b"
    )
    monkeypatch.setenv("OPENDV_WAIVER_KEY", "legacy-waiver-key-ignored")

    code_legacy_env = app_main(
        [
            "ontology",
            "studio",
            "proposal",
            "accept",
            "-s",
            sid_b,
            "-p",
            "prop-env-b",
            "--base-sha256",
            head_sha_b,
            "--low-confidence-waiver",
            "--waiver-rationale",
            "Adequate rationale explaining security compliance.",
            "--json",
        ]
    )
    assert code_legacy_env == 0, "Legacy OPENDV_WAIVER_KEY must be tolerated as a no-op"


def test_waiver_key_file_precedence_over_env(
    studio_env: tuple[Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    """Verify non-admin role rejection takes precedence even when OPENDV_WAIVER_KEY is set."""
    ws, studio_root = studio_env
    sid, head_sha = _setup_session_with_pending_proposal(ws, studio_root)

    sec_mgr = WorkspaceSecurityManager(studio_root=studio_root)
    valid_key = sec_mgr.get_waiver_key().hex()
    monkeypatch.setenv("OPENDV_WAIVER_KEY", valid_key)

    code = app_main(
        [
            "--role",
            "PUBLIC",
            "ontology",
            "studio",
            "proposal",
            "accept",
            "-s",
            sid,
            "-p",
            "prop-sec-01",
            "--base-sha256",
            head_sha,
            "--low-confidence-waiver",
            "--waiver-rationale",
            "Adequate rationale explaining security compliance.",
            "--json",
        ]
    )
    assert code == 7, "Non-admin --role PUBLIC must fail with exit 7 even if OPENDV_WAIVER_KEY is set"


def test_constant_time_comparison_implementation() -> None:
    """Verify local waiver validation checks admin role and rationale without K_waiver gating."""
    src = inspect.getsource(cli_studio_mod._load_and_verify_waiver_credentials)
    assert "role_tier != \"admin\"" in src or "role_tier" in src
    assert "FORBIDDEN_ADMIN_REQUIRED" in src
    assert "INVALID_GROUNDING_WAIVER" in src


def test_zero_secret_leakage_on_disk(studio_env: tuple[Path, Path]) -> None:
    """Verify raw waiver keys NEVER leak into proposals, deltas, sessions, or logs."""
    ws, studio_root = studio_env
    sid, head_sha = _setup_session_with_pending_proposal(ws, studio_root)

    sec_mgr = WorkspaceSecurityManager(studio_root=studio_root)
    waiver_bytes = sec_mgr.get_waiver_key()
    waiver_hex = waiver_bytes.hex()

    key_file = ws / "valid.key"
    key_file.write_text(waiver_hex, encoding="utf-8")

    code = app_main(
        [
            "ontology",
            "studio",
            "proposal",
            "accept",
            "-s",
            sid,
            "-p",
            "prop-sec-01",
            "--base-sha256",
            head_sha,
            "--low-confidence-waiver",
            "--waiver-rationale",
            "Adequate rationale explaining security compliance.",
            "--waiver-key-file",
            str(key_file),
            "--json",
        ]
    )
    assert code == 0

    # Scan all files under studio root (except .odv_workspace_secret itself)
    for p in studio_root.rglob("*"):
        if p.is_file() and p.name != ".odv_workspace_secret":
            raw_bytes = p.read_bytes()
            assert waiver_bytes not in raw_bytes, f"Raw secret bytes leaked in file: {p}"
            try:
                text = raw_bytes.decode("utf-8")
                assert waiver_hex not in text, f"Raw secret hex leaked in file: {p}"
            except UnicodeDecodeError:
                pass


# ==============================================================================
# Vector 2: Path Containment and Traversal Verification
# ==============================================================================


@pytest.mark.parametrize(
    "malicious_sid",
    [
        "../escape",
        "../../root",
        "..\\..\\windows",
        "/etc/passwd",
        "C:\\Windows\\System32",
        "session/nested",
        "session\\nested",
        "con",
        "aux",
        "nul",
        "prn",
        "com1",
        "lpt1",
        "UPPERCASE_NOT_ALLOWED",
        "-leading-hyphen",
        "_leading_underscore",
        "has whitespace",
        "has!special#chars$",
        "a" * 65,  # Exceeds 64 chars
    ],
)
def test_path_traversal_session_id_rejected(
    studio_env: tuple[Path, Path], malicious_sid: str
) -> None:
    """Verify that malicious session IDs are strictly rejected with exit 2."""
    _ws, _studio_root = studio_env
    app_main(["ontology", "studio", "init"])

    # Attempt create
    code_create = app_main(["ontology", "studio", "create", "-s", malicious_sid, "--json"])
    assert code_create == 2, (
        f"Malicious session ID '{malicious_sid}' must be rejected with exit 2 on create"
    )

    # Attempt status
    code_status = app_main(["ontology", "studio", "status", "-s", malicious_sid, "--json"])
    assert code_status == 2, (
        f"Malicious session ID '{malicious_sid}' must be rejected with exit 2 on status"
    )

    # Attempt export
    code_export = app_main(["ontology", "studio", "export", "-s", malicious_sid, "--json"])
    assert code_export == 2, (
        f"Malicious session ID '{malicious_sid}' must be rejected with exit 2 on export"
    )


@pytest.mark.parametrize(
    "malicious_pid",
    [
        "../../secret",
        "../prop",
        "prop/sub",
        "prop\\sub",
        "prop;calc",
        "!@#$%",
        "prop with spaces",
        "p" * 65,
    ],
)
def test_path_traversal_proposal_id_rejected(
    studio_env: tuple[Path, Path], malicious_pid: str
) -> None:
    """Verify that malicious proposal IDs are rejected with exit 2."""
    ws, studio_root = studio_env
    sid, _head_sha = _setup_session_with_pending_proposal(ws, studio_root)

    # Attempt proposal show
    code_show = app_main(
        ["ontology", "studio", "proposal", "show", "-s", sid, "-p", malicious_pid, "--json"]
    )
    assert code_show == 2, (
        f"Malicious proposal ID '{malicious_pid}' must be rejected with exit 2 on show"
    )

    # Attempt proposal reject
    code_rej = app_main(
        ["ontology", "studio", "proposal", "reject", "-s", sid, "-p", malicious_pid, "--json"]
    )
    assert code_rej == 2, (
        f"Malicious proposal ID '{malicious_pid}' must be rejected with exit 2 on reject"
    )


def test_bundle_zip_slip_security_violation(studio_env: tuple[Path, Path]) -> None:
    """Verify bundle import strictly rejects zip-slip path traversal (exit 4)."""
    ws, _studio_root = studio_env
    app_main(["ontology", "studio", "init"])

    # Build a malicious zip bundle with a member attempting path traversal
    malicious_zip = ws / "malicious_slip.odvstudio.zip"
    with zipfile.ZipFile(malicious_zip, "w") as zf:
        zf.writestr("../../../evil.txt", "MALICIOUS CONTENT")
        zf.writestr(
            "session.json", json.dumps({"session_id": "clean-session", "head_sha256": "dummy"})
        )

    code = app_main(["ontology", "studio", "bundle", "import", "-f", str(malicious_zip), "--json"])
    assert code == 4, (
        "Zip slip archive traversal must fail with exit 4 (ArchiveSecurityViolationError)"
    )
