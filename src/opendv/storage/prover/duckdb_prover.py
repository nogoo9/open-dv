"""DuckDbProverAdapter implementing opendv.verifier.ports.IRRuntimeProver (Gate V6).

Strictly Layer 2a: imports opendv.verifier.ports (NEVER non-ports opendv.verifier),
opendv.models, duckdb, stdlib, and sibling storage modules.
"""

from __future__ import annotations

import re
import threading
from collections.abc import Mapping, Sequence
from typing import Any

import duckdb

from opendv.models.diagnostics import IRProverReport, RealDataRuleResult, VerifierDiagnostic
from opendv.models.pii_tokenizer import RE_CC_PAN, RE_EMAIL, RE_IBAN, RE_PHONE, RE_SSN
from opendv.models.policy import CegarPolicy
from opendv.models.sources import SourceProfile
from opendv.models.vault_ir_certified import CertifiedVaultIR
from opendv.storage.prover._real_data_prover import run_real_data_proof_pass
from opendv.storage.prover.dyn_invariants import run_all_dyn_invariants
from opendv.storage.prover.edge_batches import run_adversarial_batches
from opendv.storage.sql_ast_verifier import SqlAstVerifier
from opendv.storage.sql_compiler.compiler import SqlGlotVaultCompiler
from opendv.verifier.ports import IRRuntimeProver

_SINGLE_QUOTED_RE = re.compile(r"'(?:[^'\\]|\\.|'')*'")
_DOUBLE_QUOTED_RE = re.compile(r'"(?:[^"\\]|\\.|"")*"')
_LONG_DIGITS_RE = re.compile(r"\b\d{6,}\b")


def scrub_prover_exception(msg: str) -> str:
    """Redact quoted literals, PII patterns, and long digit runs before 500-char truncation."""
    scrubbed = _SINGLE_QUOTED_RE.sub("'<REDACTED>'", msg)
    scrubbed = _DOUBLE_QUOTED_RE.sub('"<REDACTED>"', scrubbed)
    scrubbed = RE_EMAIL.sub("<REDACTED>", scrubbed)
    scrubbed = RE_SSN.sub("<REDACTED>", scrubbed)
    scrubbed = RE_PHONE.sub("<REDACTED>", scrubbed)
    scrubbed = RE_IBAN.sub("<REDACTED>", scrubbed)
    scrubbed = RE_CC_PAN.sub("<REDACTED>", scrubbed)
    scrubbed = _LONG_DIGITS_RE.sub("<REDACTED>", scrubbed)
    return scrubbed[:500]


class DuckDbProverAdapter(IRRuntimeProver):
    """Isolated :memory: DuckDB V6 runtime prover implementing IRRuntimeProver."""

    def __init__(
        self,
        *,
        memory_limit: str = "512MB",
        threads: int = 2,
        timeout_seconds: float = 30.0,
    ) -> None:
        self.memory_limit = memory_limit
        self.threads = threads
        self.timeout_seconds = timeout_seconds
        self._compiler = SqlGlotVaultCompiler("duckdb_local")
        self._ast_verifier = SqlAstVerifier()

    def _open_conn(self) -> duckdb.DuckDBPyConnection:
        return duckdb.connect(
            ":memory:",
            config={
                "memory_limit": self.memory_limit,
                "threads": self.threads,
                "enable_external_access": False,
                "lock_configuration": True,
            },
        )

    def prove(
        self,
        certified_ir: CertifiedVaultIR,
        profiles: Sequence[SourceProfile],
        *,
        policy: CegarPolicy = "balanced",
        source_rows_by_table: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
        table_sample_flags: Mapping[str, bool] | None = None,
        real_row_proving: bool = True,
    ) -> IRProverReport:
        """Execute Alg A.6 AST checks + isolated Pass-1 schema_proof + Pass-2 real_data_proof."""
        bundle = self._compiler.compile(certified_ir)
        ast_violations = self._ast_verifier.verify_sql(
            bundle.full_sql, "duckdb_local", ir=certified_ir
        )
        pass_timeout = max(0.05, self.timeout_seconds / 2.0)

        # Pass 1 (schema_proof): 15 DV-DYN invariants on deterministic edge fixtures
        schema_proof_results: dict[str, bool]
        counterexamples: list[dict[str, str | int | float | None]]
        empirical_smells: list[VerifierDiagnostic]
        conn1 = self._open_conn()
        timer1 = threading.Timer(pass_timeout, conn1.interrupt)
        timer1.daemon = True
        timer1.start()
        try:
            batch_summary = run_adversarial_batches(
                conn1,
                certified_ir,
                profiles,
                bundle,
                source_rows_by_table=None,
                policy=policy,
            )
            schema_proof_results, counterexamples = run_all_dyn_invariants(
                conn1,
                certified_ir,
                bundle.physical_aliases,
                bundle.stg_hd_col_map,
                batch_summary,
            )
            empirical_smells = batch_summary.empirical_smells
        except duckdb.Error as exc:
            schema_proof_results = {"DV-DYN-004": False}
            counterexamples = [{"rule_id": "DV-DYN-004", "error": scrub_prover_exception(str(exc))}]
            empirical_smells = []
        finally:
            timer1.cancel()
            conn1.close()

        # Pass 2 (real_data_proof): isolated real-row pass or explicit opt-out
        real_data_proof_results: dict[str, RealDataRuleResult] = {}
        real_counterexamples: list[dict[str, str | int | float | None]] = []
        if not real_row_proving:
            real_data_proof_results = {
                "DV-DYN-ALL": RealDataRuleResult(
                    status="DISABLED_BY_USER",
                    detail=(
                        "Real-row proving disabled by caller; duplicate BKs, cast failures, "
                        "unexpected NULLs, and empirical churn will only be caught in Step 7."
                    ),
                )
            }
        elif source_rows_by_table and any(
            bool(source_rows_by_table.get(b.source_table_id)) for b in certified_ir.bronze_tables
        ):
            conn2 = self._open_conn()
            timer2 = threading.Timer(pass_timeout, conn2.interrupt)
            timer2.daemon = True
            timer2.start()
            try:
                real_data_proof_results, real_counterexamples = run_real_data_proof_pass(
                    conn2,
                    certified_ir,
                    profiles,
                    bundle,
                    source_rows_by_table=source_rows_by_table,
                    table_sample_flags=table_sample_flags,
                )
            except duckdb.Error as exc:
                scrubbed = scrub_prover_exception(str(exc))
                real_data_proof_results["DV-DYN-004"] = RealDataRuleResult(
                    status="FAIL", detail=scrubbed
                )
                real_counterexamples.append({"rule_id": "DV-DYN-004", "error": scrubbed})
            finally:
                timer2.cancel()
                conn2.close()

        dyn_results = dict(schema_proof_results)
        for rule_id, r_res in real_data_proof_results.items():
            if r_res.status == "FAIL":
                dyn_results[rule_id] = False

        return IRProverReport(
            dyn_results=dyn_results,
            ast_violations=ast_violations,
            empirical_smells=empirical_smells,
            counterexamples=[*counterexamples, *real_counterexamples][:25],
            schema_proof_results=schema_proof_results,
            real_data_proof_results=real_data_proof_results,
        )


__all__ = ["DuckDbProverAdapter", "scrub_prover_exception"]
