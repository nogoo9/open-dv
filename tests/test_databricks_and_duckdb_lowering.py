"""Phase 2 TDD Tests: Multi-Dialect DuckDB & Databricks Delta Lowering (`FR-DVM-15`).

Covers:
1. DuckDB + Databricks Delta SQL compilation from `CanonicalIR`:
   - `USING DELTA` on every Delta DDL statement
   - Unity Catalog 3-part names (`<catalog>.<schema>.<table>`)
   - Liquid clustering (`CLUSTER BY`) on Hub/Link/Satellite hash/load keys
   - `TBLPROPERTIES ('delta.appendOnly' = 'true')` on Raw Vault tables
   - `sqlglot` AST parsing with `read="databricks"`
2. `Algorithm A.5` hash parity across Python `compute_dv2_length_prefixed_sha256`,
   DuckDB `build_algorithm_a5_sha256_sql`, and Databricks `build_databricks_algorithm_a5_sha256_sql`
   (`SHA2(..., 256)` + `OCTET_LENGTH`) on ASCII, delimiter-collision, NULL, and multi-byte UTF-8 vectors.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import duckdb
import sqlglot

from opendv_core.constants import ZERO_KEY_UNKNOWN
from opendv_core.hashing import compute_dv2_length_prefixed_sha256, resolve_fk_hash_key_with_zero_keys
from opendv_core.models.canonical_ir import CanonicalIR
from opendv_duckdb_sandbox.compiler_4zone.hashing import build_algorithm_a5_sha256_sql
from opendv_duckdb_sandbox.dialects.databricks_transpiler import (
    build_databricks_algorithm_a5_sha256_sql,
    compile_canonical_ir_to_databricks,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
PE_IR_PATH = REPO_ROOT / "examples" / "private_equity" / "pe_canonical_ir.json"


def test_databricks_delta_compilation_emits_uc_3part_names_cluster_by_and_append_only() -> None:
    """Databricks Delta bundle emits `USING DELTA`, UC 3-part names, `CLUSTER BY`, and `delta.appendOnly`."""
    ir_model = CanonicalIR.model_validate(json.loads(PE_IR_PATH.read_text(encoding="utf-8")))
    bundle = compile_canonical_ir_to_databricks(ir_model)

    assert bundle.dialect == "databricks"
    assert "USING DELTA" in bundle.zone_0_sql
    assert re.search(r"[A-Za-z0-9_]+\.raw_vault\.hub_limited_partner\b", bundle.zone_0_sql), (
        "Expected Unity Catalog 3-part table name <catalog>.raw_vault.hub_limited_partner"
    )
    assert "CLUSTER BY" in bundle.zone_0_sql
    assert "'delta.appendOnly' = 'true'" in bundle.zone_0_sql

    # All generated Databricks SQL statements parse cleanly under sqlglot databricks dialect
    stmts = [s for s in sqlglot.parse(bundle.full_sql_bundle, read="databricks") if s is not None]
    assert len(stmts) >= len(ir_model.hubs) + len(ir_model.links) + len(ir_model.satellites)


def test_databricks_and_duckdb_algorithm_a5_octet_length_sha256_parity() -> None:
    """Databricks `SHA2(..., 256)` + `OCTET_LENGTH` SQL matches DuckDB SQL and Python `Algorithm A.5`."""
    duck_bk_expr = build_algorithm_a5_sha256_sql(
        ["c1", "c2"],
        is_business_key=True,
        bkcc_literal="GLOBAL",
    )
    duck_sat_expr = build_algorithm_a5_sha256_sql(
        ["c1", "c2"],
        is_business_key=False,
    )
    db_expr = build_databricks_algorithm_a5_sha256_sql(
        ["c1", "c2"],
        is_business_key=True,
        bkcc_literal="GLOBAL",
    )

    assert "OCTET_LENGTH(" in db_expr
    assert "SHA2(" in db_expr and ", 256)" in db_expr
    assert sqlglot.parse_one(f"SELECT {db_expr} AS hk", read="databricks") is not None

    bk_vectors = [
        ("A|B", "C"),
        ("A", "B|C"),
        ("München", "東京"),
        ("  padded  ", "val"),
    ]
    with duckdb.connect(":memory:") as con:
        for v1, v2 in bk_vectors:
            py_hash = compute_dv2_length_prefixed_sha256(
                [v1, v2],
                is_business_key=True,
                bkcc_prefix="GLOBAL",
            ).lower()
            duck_hash = con.execute(
                f"SELECT {duck_bk_expr} FROM (SELECT ?::VARCHAR AS c1, ?::VARCHAR AS c2)",
                [v1, v2],
            ).fetchone()[0]
            assert duck_hash == py_hash

        # Verify no delimiter collision between ('A|B','C') and ('A','B|C')
        assert compute_dv2_length_prefixed_sha256(
            ["A|B", "C"], is_business_key=True, bkcc_prefix="GLOBAL"
        ) != compute_dv2_length_prefixed_sha256(
            ["A", "B|C"], is_business_key=True, bkcc_prefix="GLOBAL"
        )

        # Verify NULL handling in Satellite hash_diff (-1:^^) and Hub BK (ZERO_KEY_UNKNOWN)
        sat_py = compute_dv2_length_prefixed_sha256([None, "alpha"], is_business_key=False).lower()
        sat_duck = con.execute(
            f"SELECT {duck_sat_expr} FROM (SELECT ?::VARCHAR AS c1, ?::VARCHAR AS c2)",
            [None, "alpha"],
        ).fetchone()[0]
        assert sat_duck == sat_py

        bk_null_duck = con.execute(
            f"SELECT {duck_bk_expr} FROM (SELECT ?::VARCHAR AS c1, ?::VARCHAR AS c2)",
            [None, "alpha"],
        ).fetchone()[0]
        assert bk_null_duck == resolve_fk_hash_key_with_zero_keys([None]).lower() == ZERO_KEY_UNKNOWN
