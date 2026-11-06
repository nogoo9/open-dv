"""tests/parity/test_golden.py — Golden Test Suite Verifications.

Implements:
- GT-01 Global Commerce lifecycle baseline assertions (canonical IR, contract, 4-zone SQL).
- GT-02 Private Equity reference baseline (verify.json, canonical_ir.json, four_zone.sql).
- GT-04 Normative Hash Vectors parsed directly from docs/17 §G.2 (Python + DuckDB parity).
- Three-Way Agreement verification: committed files ≡ MANIFEST.json ≡ docs/17 §G table.
- Golden update workflow security: missing or short justifications raise ValueError.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import duckdb
import pytest

from opendv_core import validate_canonical_ir
from opendv_core.constants import ZERO_KEY_ERROR, ZERO_KEY_UNKNOWN
from opendv_core.hashing import (
    compute_dv2_length_prefixed_sha256,
    compute_dv2_prehash_string,
    resolve_fk_hash_key_with_zero_keys,
)
from opendv_duckdb_sandbox import compile_canonical_ir_to_4zone_sql
from tests.parity.golden import GoldenManager
from tests.parity.golden_fixtures import (
    capture_gt03_rejections,
    render_explorer_text_snapshot,
    run_gt01_lifecycle_waves,
)
from tests.parity.normalize import normalize_for_parity
from tests.parity.transports import DirectRestTransport, StandaloneCliTransport

pytestmark = [pytest.mark.golden, pytest.mark.parity]

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_golden_determinism_prechecks(golden_manager: GoldenManager) -> None:
    """Verify compiler byte-stability, LF line-ending invariance, and float serialization."""
    golden_manager.run_determinism_prechecks()


def test_golden_three_way_agreement(golden_manager: GoldenManager) -> None:
    """Verify committed files == MANIFEST.json == docs/17 §G table bit-for-bit."""
    golden_manager.verify_three_way_agreement()


def test_golden_update_without_justification_fails(tmp_path: Path) -> None:
    """Verify --update-golden without a justification or with < 10 characters raises ValueError."""
    fake_golden_root = tmp_path / "golden"
    fake_golden_root.mkdir(parents=True)
    fake_docs17 = tmp_path / "docs17.md"
    fake_docs17.write_text("### G.3 Golden Manifest Table\n", encoding="utf-8")

    # Empty justification
    gm_no_just = GoldenManager(
        fake_golden_root,
        fake_docs17,
        update_mode=True,
        justification="",
    )
    with pytest.raises(ValueError, match="requires a non-empty --golden-justification"):
        gm_no_just.assert_golden_byte_equality(
            "GT-01", "test.txt", b"sample content", "test_origin"
        )

    # Short justification (< 10 chars)
    gm_short_just = GoldenManager(
        fake_golden_root,
        fake_docs17,
        update_mode=True,
        justification="short",
    )
    with pytest.raises(ValueError, match="requires a non-empty --golden-justification"):
        gm_short_just.assert_golden_byte_equality(
            "GT-01", "test.txt", b"sample content", "test_origin"
        )


def test_golden_update_without_issue_number_fails(tmp_path: Path) -> None:
    """Verify --update-golden with justification missing issue/PR number raises ValueError."""
    fake_golden_root = tmp_path / "golden"
    fake_golden_root.mkdir(parents=True)
    fake_docs17 = tmp_path / "docs17.md"
    fake_docs17.write_text("### G.3 Golden Manifest Table\n", encoding="utf-8")

    gm_no_issue = GoldenManager(
        fake_golden_root,
        fake_docs17,
        update_mode=True,
        justification="justification without issue number reference",
    )
    with pytest.raises(ValueError, match="requires a non-empty --golden-justification"):
        gm_no_issue.assert_golden_byte_equality(
            "GT-01", "test.txt", b"sample content", "test_origin"
        )


def test_golden_divergence_fails_without_update_mode(tmp_path: Path) -> None:
    """Verify modifying a golden file without --update-golden raises AssertionError."""
    fake_golden_root = tmp_path / "golden"
    fake_golden_root.mkdir(parents=True)
    fake_docs17 = tmp_path / "docs17.md"
    target_file = fake_golden_root / "sample.txt"
    target_file.write_bytes(b"original content\n")

    gm = GoldenManager(
        fake_golden_root,
        fake_docs17,
        update_mode=False,
    )
    with pytest.raises(AssertionError, match="Golden divergence detected"):
        gm.assert_golden_byte_equality("GT-01", "sample.txt", b"divergent content\n", "test_origin")


def test_gt01_lifecycle_waves(golden_manager: GoldenManager, tmp_path: Path) -> None:
    """Verify GT-01 Global Commerce 4-wave lifecycle across all 25 golden files."""
    bundle = run_gt01_lifecycle_waves(golden_manager, REPO_ROOT, tmp_path)
    assert bundle, "Expected non-empty explorer bundle from GT-01"


def test_gt02_private_equity_baseline(
    golden_manager: GoldenManager,
    cli_transport: StandaloneCliTransport,
    rest_transport: DirectRestTransport,
) -> None:
    """Verify GT-02 Private Equity reference outputs across CLI and REST transports."""
    pe_contract = REPO_ROOT / "examples" / "private_equity" / "dv_pe_model_contract.yaml"
    graph_a = REPO_ROOT / "examples" / "private_equity" / "pe_graph_a_ontology.yaml"
    graph_b = REPO_ROOT / "examples" / "private_equity" / "pe_graph_b_profile.json"
    pe_ir_path = REPO_ROOT / "examples" / "private_equity" / "pe_canonical_ir.json"

    # 1. verify.json (with parity check)
    verify_cli = cli_transport.verify(contract=pe_contract, graph_a=graph_a, graph_b=graph_b)
    verify_rest = rest_transport.verify(contract=pe_contract, graph_a=graph_a, graph_b=graph_b)
    norm_cli = normalize_for_parity(verify_cli, path_root=str(REPO_ROOT))
    norm_rest = normalize_for_parity(verify_rest, path_root=str(REPO_ROOT))
    assert norm_cli == norm_rest, "Cross-surface parity mismatch on GT-02 verify"
    verify_bytes = (json.dumps(norm_cli, indent=2, sort_keys=True) + "\n").encode("utf-8")
    golden_manager.assert_golden_byte_equality(
        golden_id="GT-02",
        relative_file="GT-02/verify.json",
        actual_bytes=verify_bytes,
        generated_from="PAR-PE-S5: opendv verify",
    )

    # 2. canonical_ir.json
    golden_manager.assert_golden_byte_equality(
        golden_id="GT-02",
        relative_file="GT-02/canonical_ir.json",
        actual_bytes=pe_ir_path.read_bytes(),
        generated_from="PAR-PE-S5: opendv compile",
    )

    # 3. four_zone.sql
    pe_ir_doc = json.loads(pe_ir_path.read_text(encoding="utf-8"))
    pe_ir_obj = validate_canonical_ir(pe_ir_doc)
    pe_bundle = compile_canonical_ir_to_4zone_sql(
        pe_ir_obj, include_batch4_restatement_in_seed=False
    )
    pe_sql_bytes = (pe_bundle.full_sql_bundle.replace("\r\n", "\n") + "\n").encode("utf-8")
    golden_manager.assert_golden_byte_equality(
        golden_id="GT-02",
        relative_file="GT-02/four_zone.sql",
        actual_bytes=pe_sql_bytes,
        generated_from="PAR-PE-S5: opendv compile",
    )

    # 4. sandbox_batches.json (4 adversarial batches across CLI and REST)
    batches_cli = cli_transport.sandbox(
        contract=pe_contract, canonical_ir=pe_ir_path, query_type="ADVERSARIAL_BATCHES_1_TO_4"
    )
    batches_rest = rest_transport.sandbox(
        contract=pe_contract, canonical_ir=pe_ir_path, query_type="ADVERSARIAL_BATCHES_1_TO_4"
    )
    norm_b_cli = normalize_for_parity(batches_cli, path_root=str(REPO_ROOT))
    norm_b_rest = normalize_for_parity(batches_rest, path_root=str(REPO_ROOT))
    assert norm_b_cli == norm_b_rest, "Cross-surface parity mismatch on GT-02 sandbox batches"
    batches_bytes = (json.dumps(norm_b_cli, indent=2, sort_keys=True) + "\n").encode("utf-8")
    golden_manager.assert_golden_byte_equality(
        golden_id="GT-02",
        relative_file="GT-02/sandbox_batches.json",
        actual_bytes=batches_bytes,
        generated_from="PAR-PE-S6: opendv sandbox --query-type ADVERSARIAL_BATCHES_1_TO_4",
    )

    # 5. d10_2026-10-15.json (D.10 PIT query rows across CLI and REST)
    d10_cli = cli_transport.sandbox(
        contract=pe_contract,
        canonical_ir=pe_ir_path,
        query_type="QUERY_D10_PIT",
        cutoff="2026-10-15 00:00:00",
    )
    d10_rest = rest_transport.sandbox(
        contract=pe_contract,
        canonical_ir=pe_ir_path,
        query_type="QUERY_D10_PIT",
        cutoff="2026-10-15 00:00:00",
    )
    norm_d10_cli = normalize_for_parity(d10_cli, path_root=str(REPO_ROOT))
    norm_d10_rest = normalize_for_parity(d10_rest, path_root=str(REPO_ROOT))
    assert norm_d10_cli == norm_d10_rest, "Cross-surface parity mismatch on GT-02 D.10 PIT"
    d10_bytes = (json.dumps(norm_d10_cli, indent=2, sort_keys=True) + "\n").encode("utf-8")
    golden_manager.assert_golden_byte_equality(
        golden_id="GT-02",
        relative_file="GT-02/d10_2026-10-15.json",
        actual_bytes=d10_bytes,
        generated_from="PAR-PE-S6: opendv sandbox --query-type QUERY_D10_PIT",
    )

    # 6. d10b_2026-11-15.json (D.10b valuation query rows across CLI and REST)
    d10b_cli = cli_transport.sandbox(
        contract=pe_contract,
        canonical_ir=pe_ir_path,
        query_type="QUERY_D10B_VALUATION",
        cutoff="2026-11-15 23:59:59",
    )
    d10b_rest = rest_transport.sandbox(
        contract=pe_contract,
        canonical_ir=pe_ir_path,
        query_type="QUERY_D10B_VALUATION",
        cutoff="2026-11-15 23:59:59",
    )
    norm_d10b_cli = normalize_for_parity(d10b_cli, path_root=str(REPO_ROOT))
    norm_d10b_rest = normalize_for_parity(d10b_rest, path_root=str(REPO_ROOT))
    assert norm_d10b_cli == norm_d10b_rest, "Cross-surface parity mismatch on GT-02 D.10b valuation"
    d10b_bytes = (json.dumps(norm_d10b_cli, indent=2, sort_keys=True) + "\n").encode("utf-8")
    golden_manager.assert_golden_byte_equality(
        golden_id="GT-02",
        relative_file="GT-02/d10b_2026-11-15.json",
        actual_bytes=d10b_bytes,
        generated_from="PAR-PE-S6: opendv sandbox --query-type QUERY_D10B_VALUATION",
    )


def test_gt03_golden_rejections(
    golden_manager: GoldenManager,
    rest_transport: DirectRestTransport,
    tmp_path: Path,
) -> None:
    """Verify GT-03 6 canonical bad inputs across exit codes, ProblemDetails, and trace.toon."""
    capture_gt03_rejections(
        golden_manager, REPO_ROOT, tmp_path, rest_transport=rest_transport
    )


def test_gt04_normative_hash_vectors_parsed_from_docs17(golden_manager: GoldenManager) -> None:
    """Verify all 21 GT-04 normative hash vectors parsed directly from docs/17 §G.2 in Python."""
    vectors = golden_manager.parse_docs17_gt04_vectors()
    assert len(vectors) == 21, f"Expected 21 vectors in docs/17 §G.2, found {len(vectors)}"

    for vec in vectors:
        vid = vec["vector_id"]
        tokens = vec["tokens"]
        is_bk = vec["is_business_key"]
        bkcc = vec["bkcc_prefix"]
        expected_prehash = vec["canonical_prehash"]
        expected_sha = vec["expected_sha256"]

        if vid == "VEC-20":
            # Mandatory FK NULL -> ZERO_KEY_UNKNOWN
            sha = resolve_fk_hash_key_with_zero_keys(tokens, bkcc_prefix=bkcc)
            assert sha == ZERO_KEY_UNKNOWN
            assert sha == expected_sha
        elif vid == "VEC-21":
            # Optional Inapplicable FK -> ZERO_KEY_ERROR
            sha = resolve_fk_hash_key_with_zero_keys(tokens, bkcc_prefix=bkcc)
            assert sha == ZERO_KEY_ERROR
            assert sha == expected_sha
        else:
            actual_prehash = compute_dv2_prehash_string(
                tokens, is_business_key=is_bk, bkcc_prefix=bkcc
            )
            actual_sha = compute_dv2_length_prefixed_sha256(
                tokens, is_business_key=is_bk, bkcc_prefix=bkcc
            )
            assert actual_prehash == expected_prehash, (
                f"{vid} prehash mismatch: computed '{actual_prehash}' "
                f"!= expected '{expected_prehash}'"
            )
            assert actual_sha == expected_sha, (
                f"{vid} SHA-256 mismatch: computed '{actual_sha}' != expected '{expected_sha}'"
            )


def test_gt04_normative_hash_vectors_duckdb_parity(
    golden_manager: GoldenManager,
    duckdb_conn: duckdb.DuckDBPyConnection,
) -> None:
    """Verify DuckDB SQL computes bit-for-bit identical SHA-256 digests for all GT-04 vectors."""
    vectors = golden_manager.parse_docs17_gt04_vectors()

    for vec in vectors:
        vid = vec["vector_id"]
        prehash = vec["canonical_prehash"]
        expected_sha = vec["expected_sha256"]

        if vid in ("VEC-20", "VEC-21"):
            # Sentinels are checked against DuckDB constants
            if vid == "VEC-20":
                row = duckdb_conn.execute("SELECT REPEAT('0', 64)").fetchone()
                assert row is not None and row[0] == expected_sha
            else:
                row = duckdb_conn.execute("SELECT REPEAT('f', 64)").fetchone()
                assert row is not None and row[0] == expected_sha
            continue

        query = "SELECT SHA256(?)"
        res = duckdb_conn.execute(query, [prehash]).fetchone()
        assert res is not None, f"DuckDB query returned None for {vid}"
        duckdb_sha = str(res[0]).lower()
        assert duckdb_sha == expected_sha, (
            f"DuckDB SHA-256 mismatch for {vid}: DuckDB '{duckdb_sha}' != expected '{expected_sha}'"
        )


def test_gt04_collision_resistance_invariants(golden_manager: GoldenManager) -> None:
    """Verify boundary-shift, embedded delimiter, and positional NULL collision resistance."""
    vecs = {v["vector_id"]: v for v in golden_manager.parse_docs17_gt04_vectors()}

    # 1. Boundary-shift: ['AB', 'C'] vs ['A', 'BC']
    assert vecs["VEC-14"]["canonical_prehash"] != vecs["VEC-15"]["canonical_prehash"]
    assert vecs["VEC-14"]["expected_sha256"] != vecs["VEC-15"]["expected_sha256"]

    # 2. Embedded delimiter: ['ANNE||O', 'BRIEN'] vs ['ANNE', 'O||BRIEN']
    assert vecs["VEC-16"]["canonical_prehash"] != vecs["VEC-17"]["canonical_prehash"]
    assert vecs["VEC-16"]["expected_sha256"] != vecs["VEC-17"]["expected_sha256"]

    # 3. Positional NULL: ['VAL_X', None, 'VAL_Y'] vs ['VAL_X', 'VAL_Y', None]
    assert vecs["VEC-18"]["canonical_prehash"] != vecs["VEC-19"]["canonical_prehash"]
    assert vecs["VEC-18"]["expected_sha256"] != vecs["VEC-19"]["expected_sha256"]

    # 4. Multi-Tenant isolation: 'ACCT-1001' under TENANT_EU vs TENANT_US
    assert vecs["VEC-07"]["canonical_prehash"] != vecs["VEC-08"]["canonical_prehash"]
    assert vecs["VEC-07"]["expected_sha256"] != vecs["VEC-08"]["expected_sha256"]


def test_gt05_explorer_render_snapshot(
    golden_manager: GoldenManager,
    tmp_path: Path,
) -> None:
    """Verify GT-05 deterministic Explorer text snapshot generated from GT-01 Wave 3 bundle."""
    bundle = run_gt01_lifecycle_waves(golden_manager, REPO_ROOT, tmp_path)
    snapshot_text = render_explorer_text_snapshot(bundle)
    golden_manager.assert_golden_byte_equality(
        golden_id="GT-05",
        relative_file="GT-05/render_snapshot.txt",
        actual_bytes=(snapshot_text.strip() + "\n").encode("utf-8"),
        generated_from="PAR-S8-W3: opendv explorer render snapshot",
    )
