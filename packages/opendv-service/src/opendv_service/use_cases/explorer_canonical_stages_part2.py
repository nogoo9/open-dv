"""Stages 04 through 07 builders for Explorer canonical pipeline.

Conforms to docs/design/open-dv-workbench/HANDOFF.md, Decision D3, and Directive R9.
"""

from __future__ import annotations

from opendv_contracts.dtos.explorer import (
    StageDTO,
)
from opendv_service.use_cases.explorer_canonical_stages_part1 import (
    _stage,
    _step,
)


def build_stages_04_to_07() -> list[StageDTO]:
    """Construct canonical stages STAGE_04 through STAGE_07."""
    return [
        _stage(
            "STAGE_04_SHIP_VAULT_PASS2",
            1,
            "Pass 2/3 · Vault Topology & Keys",
            "@ship-vault",
            "pro · T=0.1",
            "PASS",
            "Synthesises Hubs, Links and Satellites with PII isolation and companion sats.",
            "Drafts Data Vault 2.0 contract: Hubs, Links, and Satellites.",
            ["ontology", "triage", "drift", "extract"],
            ["draft_contract"],
            [
                _step(
                    "Synthesise Hubs from Graph A concepts",
                    "Creates Hub definitions anchored to concepts.",
                    ["ontology", "triage"],
                    ["draft_contract"],
                ),
                _step(
                    "Synthesise Links",
                    "Routes relationships into unit-of-work Links.",
                    ["triage"],
                    ["draft_contract"],
                ),
                _step(
                    "Split RESTRICTED_PII satellites",
                    "Isolates PII attributes into GDPR satellites.",
                    ["triage"],
                    ["draft_contract"],
                ),
                _step(
                    "Attach companion schema drift satellites",
                    "Absorbs additive drift into companion sat.",
                    ["drift"],
                    ["draft_contract"],
                ),
            ],
            [
                (
                    "ship-vault",
                    "Agent",
                    "Declarative DV 2.0 synthesiser.",
                    "packages/opendv-agent-harness/src/opendv_agent_harness/personas.py",
                ),
                (
                    "BKCCAuthority",
                    "Algorithm",
                    "Algorithm A.4 BKCC collision shield.",
                    "packages/opendv-core/src/opendv_core/bkcc.py",
                ),
            ],
        ),
        _stage(
            "STAGE_05_AXI_DV_STATIC_GATES",
            2,
            "Gates 0-4 · Static Verification",
            "@axi-dv",
            "deterministic",
            "AUTO_REPAIRED",
            "Runs formal checks across Gates 0-4; closed-loop repair on Attempt 2.",
            "Deterministic symbolic verifier (Pydantic, SQLGlot, NetworkX, YAGNI cost guard).",
            ["draft_contract", "profile", "drift", "triage"],
            ["toon", "gate_static", "final_contract"],
            [
                _step(
                    "Gate 0 · Profile & drift alignment",
                    "Verifies profiled columns match contract.",
                    ["draft_contract", "profile", "drift"],
                    ["gate_static"],
                ),
                _step(
                    "Gate 1 · Schema, grain & BKCC check",
                    "Detects unscoped business keys.",
                    ["draft_contract", "triage"],
                    ["toon", "gate_static"],
                    "decision",
                    "Attempt 1: DV-COL-012 violation → @toon_v1 trace",
                ),
                _step(
                    "Gate 2 · SQLGlot AST & Algorithm A.5 recipe",
                    "Parses staging and hash formulas.",
                    ["draft_contract"],
                    ["gate_static"],
                ),
                _step(
                    "Gate 3 · NetworkX bipartite DAG verification",
                    "Proves outdeg, arity, and zero cycles.",
                    ["draft_contract"],
                    ["gate_static"],
                ),
                _step(
                    "Gate 4 · YAGNI write-amplification guard",
                    "Checks write amplification against budget.",
                    ["draft_contract", "profile"],
                    ["gate_static"],
                ),
                _step(
                    "Closed-loop self-healing retry",
                    "Patches contract using diagnostic trace.",
                    ["draft_contract", "toon"],
                    ["final_contract", "gate_static"],
                    "loop",
                    "Attempt 2: PASS",
                ),
            ],
            [
                (
                    "axi-dv",
                    "Verifier",
                    "Deterministic 6-gate symbolic verifier.",
                    "packages/opendv-axi-verifier/src/opendv_axi_verifier/verifier.py",
                ),
                (
                    "gate1_schema_grain",
                    "Verifier",
                    "Pydantic + BKCC collision verifier.",
                    (
                        "packages/opendv-axi-verifier/src/opendv_axi_verifier/"
                        "rules/gate1_schema_grain.py"
                    ),
                ),
                (
                    "gate2_sqlglot_ast",
                    "Verifier",
                    "SQLGlot AST verifier.",
                    (
                        "packages/opendv-axi-verifier/src/opendv_axi_verifier/"
                        "rules/gate2_sqlglot_ast.py"
                    ),
                ),
                (
                    "gate3_networkx_dag",
                    "Verifier",
                    "NetworkX bipartite DAG verifier.",
                    (
                        "packages/opendv-axi-verifier/src/opendv_axi_verifier/"
                        "rules/gate3_networkx_dag.py"
                    ),
                ),
            ],
        ),
        _stage(
            "STAGE_06_DUCKDB_SANDBOX",
            2,
            "Gate 5 · Analytical Sandbox",
            "@duckdb-sandbox",
            "deterministic",
            "PASS",
            "Compiles 4-zone SQL, ingests batches, and verifies dynamic assertions.",
            "4-Zone DuckDB shadow sandbox (Bronze / Staging / Raw Vault / Business Vault).",
            ["final_contract", "crm", "erp"],
            ["gate_sandbox", "sandbox_report"],
            [
                _step(
                    "Compile 4-zone DDL in in-memory DuckDB",
                    "Builds Bronze, Staging, and Vault schemas.",
                    ["final_contract"],
                    ["gate_sandbox"],
                ),
                _step(
                    "Attach Parquet/CSV sources via view staging",
                    "Executes Zone 0/1 loading pipelines.",
                    ["crm", "erp"],
                    ["gate_sandbox"],
                ),
                _step(
                    "Execute multi-wave adversarial inserts",
                    "Ingests chronological batches.",
                    ["crm", "erp"],
                    ["gate_sandbox"],
                ),
                _step(
                    "Verify dynamic DV-DYN-001..015 assertions",
                    "Asserts uniqueness and PIT correctness.",
                    ["gate_sandbox"],
                    ["sandbox_report"],
                ),
            ],
            [
                (
                    "duckdb-sandbox",
                    "Engine",
                    "In-memory 4-zone shadow sandbox.",
                    ("packages/opendv-duckdb-sandbox/src/opendv_duckdb_sandbox/sandbox_engine.py"),
                ),
                (
                    "compiler_4zone",
                    "Compiler",
                    "Deterministic 4-zone SQL generator.",
                    (
                        "packages/opendv-duckdb-sandbox/src/opendv_duckdb_sandbox/"
                        "compiler_4zone/__init__.py"
                    ),
                ),
            ],
        ),
        _stage(
            "STAGE_07_DETERMINISTIC_COMPILER",
            3,
            "Deterministic Artifact Seal & CAS",
            "@deterministic-compiler",
            "deterministic",
            "PASS",
            "Seals Canonical IR, compiles production 4-zone SQL, and writes CAS manifest.",
            "Emits final contract, canonical IR, 4-zone SQL, pipeline report, and ledger.",
            ["final_contract", "grounding"],
            ["final_ir", "final_sql", "report", "ledger"],
            [
                _step(
                    "Emit canonical IR (Draft 2020-12)",
                    "Serializes normalized schema-stable IR.",
                    ["final_contract"],
                    ["final_ir"],
                ),
                _step(
                    "Generate production 4-zone SQL",
                    "Renders executable SQL DDL and DML.",
                    ["final_ir"],
                    ["final_sql"],
                ),
                _step(
                    "Seal pipeline report & ledger in CAS",
                    "Computes SHA-256 digests and seals content.",
                    ["final_contract", "final_ir", "final_sql"],
                    ["report", "ledger"],
                ),
            ],
            [
                (
                    "deterministic-compiler",
                    "Compiler",
                    "Canonical IR and SQL code generator.",
                    "packages/opendv-core/src/opendv_core/compiler.py",
                ),
                (
                    "cas_sealer",
                    "Storage",
                    "SHA-256 CAS storage engine.",
                    "packages/opendv-telemetry/src/opendv_telemetry/cas.py",
                ),
            ],
        ),
    ]
