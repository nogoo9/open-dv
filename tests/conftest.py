"""Shared pytest fixtures for the open-dv specification test suite.

Provides reusable fixtures for:
- Repository paths (schemas, examples, Private Equity benchmark, specification docs)
- JSON Schema Draft 2020-12 definitions (Contract v5.0, Canonical IR v1.0.0, VaultObservabilityEvent)
- Model contracts (`examples/dv_model_contract.yaml`, `examples/private_equity/dv_pe_model_contract.yaml`)
- Canonical IR (`examples/private_equity/pe_canonical_ir.json`)
- Dual-Graph artifacts (`pe_graph_a_ontology.yaml`, `pe_graph_b_profile.json`)
- Reference Python algorithms extracted from `docs/05`–`docs/07`
- Verbatim unstructured PDF corpus and 4-Zone DuckDB SQL bundle from `docs/07`
- Ephemeral `:memory:` DuckDB connections
"""

from __future__ import annotations

from collections.abc import Generator
import json
from pathlib import Path
import re
from typing import Any

import duckdb
import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEMAS_DIR = REPO_ROOT / "schemas"
EXAMPLES_DIR = REPO_ROOT / "examples"
PE_DIR = EXAMPLES_DIR / "private_equity"
DOCS_DIR = REPO_ROOT / "docs"

CONTRACT_SCHEMA_PATH = SCHEMAS_DIR / "no-mistake-dv2-contract-v5.schema.json"
IR_SCHEMA_PATH = SCHEMAS_DIR / "dv2-canonical-ir.schema.json"
OBSERVABILITY_SCHEMA_PATH = SCHEMAS_DIR / "vault-observability-event.schema.json"

GENERIC_CONTRACT_PATH = EXAMPLES_DIR / "dv_model_contract.yaml"
PE_CONTRACT_PATH = PE_DIR / "dv_pe_model_contract.yaml"
PE_IR_PATH = PE_DIR / "pe_canonical_ir.json"
GRAPH_A_PATH = PE_DIR / "pe_graph_a_ontology.yaml"
GRAPH_B_PATH = PE_DIR / "pe_graph_b_profile.json"

DOC_04_PATH = DOCS_DIR / "04-unstructured-and-multimodal-dv2.md"
DOC_05_PATH = DOCS_DIR / "05-elm-calculus-and-reference-algorithms.md"
DOC_06_PATH = DOCS_DIR / "06-explainability-and-observability-spec.md"
DOC_07_PATH = DOCS_DIR / "07-end-to-end-private-equity-demo.md"


def extract_python_algorithms_from_doc(doc_path: Path) -> dict[str, Any]:
    """Compile and execute all fenced Python blocks in a markdown specification document."""
    doc_text = doc_path.read_text(encoding="utf-8")
    py_blocks = re.findall(r"```python\n(.*?)```", doc_text, flags=re.DOTALL)
    namespace: dict[str, Any] = {}
    for idx, block in enumerate(py_blocks, 1):
        code = compile(block, f"{doc_path.name}-block-{idx}.py", "exec")
        exec(code, namespace)
    return namespace


def extract_doc07_pdf_corpus() -> dict[str, str]:
    """Extract the two verbatim unstructured PDF texts from docs/07 §1.3."""
    doc07_text = DOC_07_PATH.read_text(encoding="utf-8")
    text_blocks = re.findall(r"```text\n(.*?)\n```", doc07_text, flags=re.DOTALL)
    assert len(text_blocks) >= 2, f"Expected >= 2 text blocks in docs/07, found {len(text_blocks)}"
    side_letter_text = text_blocks[0]
    ic_memo_text = text_blocks[1]
    return {
        "s3://apex-pe-vault-bronze/legal/side-letters/calpers_side_letter_fund_iv_2026.pdf": side_letter_text,
        "calpers_side_letter_fund_iv_2026.pdf": side_letter_text,
        "lpa_apex_buyout_fund_iv_2026.pdf": side_letter_text,
        "s3://apex-pe-vault-bronze/ic-memos/2026-q3/ic_q3_valuation_nordic_cloud.pdf": ic_memo_text,
        "ic_q3_valuation_nordic_cloud.pdf": ic_memo_text,
        "ic_memo_project_odin_2026Q3.pdf": ic_memo_text,
    }


def extract_doc07_sql_bundle() -> tuple[str, str, str, str]:
    """Extract the Artifact 3 4-Zone SQL bundle and Queries D.10 / D.10b from docs/07."""
    doc07_text = DOC_07_PATH.read_text(encoding="utf-8")
    sql_blocks = re.findall(r"```sql\n(.*?)```", doc07_text, flags=re.DOTALL)
    assert len(sql_blocks) == 1, f"Expected 1 SQL block in docs/07, found {len(sql_blocks)}"
    full_sql = sql_blocks[0]
    zone0_to_3, zone4 = full_sql.split(
        "-- =============================================================================\n"
        "-- ZONE 4: BI-TEMPORAL MULTIMODAL POINT-IN-TIME AUDIT QUERIES"
    )
    q_d10, q_d10b = zone4.split(
        "-- Query D.10b: Latest Portfolio Company Valuation Visible at the Assertion Cutoff"
    )
    return full_sql, zone0_to_3, q_d10, q_d10b


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture(scope="session")
def contract_schema() -> dict[str, Any]:
    return json.loads(CONTRACT_SCHEMA_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def ir_schema() -> dict[str, Any]:
    return json.loads(IR_SCHEMA_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def observability_schema() -> dict[str, Any]:
    return json.loads(OBSERVABILITY_SCHEMA_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def generic_contract() -> dict[str, Any]:
    return yaml.safe_load(GENERIC_CONTRACT_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def pe_contract() -> dict[str, Any]:
    return yaml.safe_load(PE_CONTRACT_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def pe_ir() -> dict[str, Any]:
    return json.loads(PE_IR_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def graph_a() -> dict[str, Any]:
    return yaml.safe_load(GRAPH_A_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def graph_b() -> dict[str, Any]:
    return json.loads(GRAPH_B_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="session")
def pe_profile(graph_b: dict[str, Any]) -> dict[str, Any]:
    return graph_b


@pytest.fixture(scope="session")
def doc05_algos() -> dict[str, Any]:
    return extract_python_algorithms_from_doc(DOC_05_PATH)


@pytest.fixture(scope="session")
def doc06_algos() -> dict[str, Any]:
    return extract_python_algorithms_from_doc(DOC_06_PATH)


@pytest.fixture(scope="session")
def pdf_corpus() -> dict[str, str]:
    return extract_doc07_pdf_corpus()


@pytest.fixture(scope="session")
def doc07_sql_bundle() -> tuple[str, str, str, str]:
    return extract_doc07_sql_bundle()


@pytest.fixture
def duckdb_conn() -> Generator[duckdb.DuckDBPyConnection, None, None]:
    """Provide an isolated ephemeral in-memory DuckDB connection per test."""
    con = duckdb.connect(":memory:")
    try:
        yield con
    finally:
        con.close()


@pytest.fixture
def populated_pe_duckdb(
    duckdb_conn: duckdb.DuckDBPyConnection,
    doc07_sql_bundle: tuple[str, str, str, str],
) -> duckdb.DuckDBPyConnection:
    """Provide an ephemeral in-memory DuckDB connection loaded with Zones 0–3 of Artifact 3."""
    _, zone0_to_3, _, _ = doc07_sql_bundle
    duckdb_conn.execute(zone0_to_3)
    return duckdb_conn
