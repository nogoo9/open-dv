"""DuckDbProverAdapter implementing opendv.verifier.ports.IRRuntimeProver (Gate V6).

Strictly Layer 2a: imports opendv.verifier.ports (NEVER non-ports opendv.verifier),
opendv.models, duckdb, stdlib, and sibling storage modules.
"""

from __future__ import annotations

import threading
from collections.abc import Mapping, Sequence
from typing import Any

import duckdb

from opendv.models.diagnostics import IRProverReport
from opendv.models.policy import CegarPolicy
from opendv.models.sources import SourceProfile
from opendv.models.vault_ir_certified import CertifiedVaultIR
from opendv.storage.prover.dyn_invariants import run_all_dyn_invariants
from opendv.storage.prover.edge_batches import run_adversarial_batches
from opendv.storage.sql_ast_verifier import SqlAstVerifier
from opendv.storage.sql_compiler.compiler import SqlGlotVaultCompiler
from opendv.verifier.ports import IRRuntimeProver


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

    def prove(
        self,
        certified_ir: CertifiedVaultIR,
        profiles: Sequence[SourceProfile],
        *,
        policy: CegarPolicy = "balanced",
        source_rows_by_table: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
    ) -> IRProverReport:
        """Execute Alg A.6 AST checks + 4 adversarial batches + 15/15 DV-DYN invariants."""
        _ = policy
        bundle = self._compiler.compile(certified_ir)
        ast_violations = self._ast_verifier.verify_sql(
            bundle.full_sql, "duckdb_local", ir=certified_ir
        )

        conn = duckdb.connect(
            ":memory:",
            config={
                "memory_limit": self.memory_limit,
                "threads": self.threads,
                "enable_external_access": False,
                "lock_configuration": True,
            },
        )
        timer = threading.Timer(self.timeout_seconds, conn.interrupt)
        timer.daemon = True
        timer.start()
        try:
            batch_summary = run_adversarial_batches(
                conn,
                certified_ir,
                profiles,
                bundle,
                source_rows_by_table=source_rows_by_table,
            )
            dyn_results, counterexamples = run_all_dyn_invariants(
                conn,
                certified_ir,
                bundle.physical_aliases,
                bundle.stg_hd_col_map,
                batch_summary,
            )
            return IRProverReport(
                dyn_results=dyn_results,
                ast_violations=ast_violations,
                empirical_smells=batch_summary.empirical_smells,
                counterexamples=counterexamples[:25],
            )
        finally:
            timer.cancel()
            conn.close()


__all__ = ["DuckDbProverAdapter"]
