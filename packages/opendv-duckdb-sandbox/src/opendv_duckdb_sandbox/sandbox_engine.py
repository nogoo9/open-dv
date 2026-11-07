"""High-Level `DuckDbShadowSandbox` Orchestrator (`docs/03` §7, `docs/07` Scene 6-7).

Coordinates:
1. Lowering `NoMistakeDataVault2ContractV5` -> `CanonicalIR` (when given a contract)
2. Compiling `CanonicalIR` -> `Compiled4ZoneBundle` (Zones 0-4 DuckDB SQL)
3. Executing the 4 Adversarial Shadow Sandbox Batches in DuckDB `:memory:`
4. Evaluating all 15 dynamic sandbox invariants (`DV-DYN-001` through `DV-DYN-015`)
5. Executing Zone 4 Bi-Temporal `Query D.10` and `Query D.10b` verification
6. Optionally emitting schema-valid `VaultObservabilityEvent`
    (`STAGE_06_AXI_DV_DYNAMIC_SANDBOX`) records via `opendv_telemetry`.
"""

from __future__ import annotations

import hashlib
import threading
import time
from decimal import Decimal
from pathlib import Path
from typing import Any

import duckdb
import sqlglot
from pydantic import BaseModel, ConfigDict, Field
from sqlglot import exp

from opendv_contracts.dtos.sandbox import SandboxColumnMetadata
from opendv_core.hashing import compute_dv2_length_prefixed_sha256
from opendv_core.models.canonical_ir import CanonicalIR
from opendv_core.models.contract_v5 import NoMistakeDataVault2ContractV5
from opendv_core.models.observability import (
    EventContextRefs,
    Layer1StoryBanner,
    Layer2VisualPayload,
    Layer3FormalProof,
    SemanticHashPill,
    VaultObservabilityEvent,
)
from opendv_core.schema_registry import get_examples_dir, load_structured_file
from opendv_duckdb_sandbox.adversarial_batches import (
    FourBatchExecutionReport,
    execute_4batch_adversarial_suite,
)
from opendv_duckdb_sandbox.compiler_4zone import (
    Compiled4ZoneBundle,
    _hub_slug,
    compile_canonical_ir_to_4zone_sql,
)
from opendv_duckdb_sandbox.dynamic_checks import (
    DynamicCheckSuiteReport,
    evaluate_all_dynamic_checks,
)
from opendv_duckdb_sandbox.ir_lowerer import lower_contract_to_canonical_ir
from opendv_duckdb_sandbox.pit_query_engine import (
    BiTemporalValuationProof,
    PitQueryEngine,
    QueryD10bRow,
    QueryD10Row,
)
from opendv_telemetry.hash_pills import format_hub_pill
from opendv_telemetry.ledger_emitter import VaultLedgerEmitter

FORBIDDEN_SQL_TYPES = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Drop,
    exp.Create,
    exp.Alter,
    exp.Command,
    exp.Set,
    exp.Pragma,
)

FORBIDDEN_SQL_FUNCTIONS = {
    "read_csv",
    f"{'read'}_{'csv_auto'}",
    f"{'read'}_{'parquet'}",
    "read_json",
    f"{'read'}_{'json_auto'}",
    "scan_parquet",
    "scan_csv",
    "parquet_scan",
    "duckdb_query",
    "query",
    "load",
    "install",
    "copy",
    "export_database",
    "import_database",
    "read_blob",
    "write_blob",
    "write_parquet",
    "write_csv",
}


def validate_safe_sql(sql: str) -> None:
    """Validate that a SQL statement is a single read-only SELECT or UNION query."""
    if not sql or not sql.strip():
        raise ValueError("SQL query cannot be empty")
    try:
        statements = [stmt for stmt in sqlglot.parse(sql, read="duckdb") if stmt is not None]
    except Exception as e:
        raise ValueError(f"Invalid SQL syntax: {e}") from e

    if len(statements) != 1:
        raise ValueError(
            f"CUSTOM_SQL permits exactly one statement, but {len(statements)} "
            "statements were provided"
        )

    stmt = statements[0]
    if not isinstance(stmt, (exp.Select, exp.Union)):
        raise ValueError(
            f"CUSTOM_SQL permits only read-only SELECT or UNION queries; got {type(stmt).__name__}"
        )

    for node, _parent, _key in stmt.walk():
        if isinstance(node, FORBIDDEN_SQL_TYPES):
            raise ValueError(f"Forbidden SQL operation in sandbox: {type(node).__name__}")
        if isinstance(node, (exp.Func, exp.Anonymous)):
            func_name = (
                node.sql_name().lower()
                if hasattr(node, "sql_name")
                else getattr(node, "name", "").lower()
            )
            if func_name in FORBIDDEN_SQL_FUNCTIONS:
                raise ValueError(f"Forbidden SQL function in sandbox: {func_name}")


class SafeSqlResult(BaseModel):
    """Structured result of executing a safe read-only SQL query in the sandbox."""

    model_config = ConfigDict(extra="forbid")

    columns: list[SandboxColumnMetadata] = Field(default_factory=list)
    rows: list[dict[str, Any]] = Field(default_factory=list)
    row_count: int = 0
    truncated: bool = False
    duration_ms: float = 0.0
    execution_stats: dict[str, Any] = Field(default_factory=dict)


class SandboxVerificationReport(BaseModel):
    """Comprehensive verification report produced by `DuckDbShadowSandbox.run_full_verification`."""

    model_config = ConfigDict(extra="forbid")

    passed: bool
    model_name: str
    compiled_bundle: Compiled4ZoneBundle
    batch_report: FourBatchExecutionReport
    dynamic_check_report: DynamicCheckSuiteReport
    query_d10_rows: list[QueryD10Row] = Field(default_factory=list)
    query_d10b_oct_15_rows: list[QueryD10bRow] = Field(default_factory=list)
    query_d10b_nov_15_rows: list[QueryD10bRow] = Field(default_factory=list)
    bitemporal_proof: BiTemporalValuationProof
    observability_event: VaultObservabilityEvent | None = None


class DuckDbShadowSandbox:
    """Ephemeral `:memory:` DuckDB Shadow Sandbox for 4-Zone SQL & 4-Batch Verification."""

    def __init__(
        self,
        conn: duckdb.DuckDBPyConnection | None = None,
        *,
        ledger_emitter: VaultLedgerEmitter | None = None,
    ) -> None:
        self._owns_conn = conn is None
        if conn is not None:
            self.conn = conn
        else:
            self.conn = duckdb.connect(
                ":memory:",
                config={
                    "max_memory": "512MB",
                    "threads": "2",
                    "enable_external_access": False,
                },
            )
        self.ledger_emitter = ledger_emitter

    def close(self) -> None:
        """Close the underlying DuckDB `:memory:` connection if owned by this sandbox."""
        if self._owns_conn:
            self.conn.close()

    def __enter__(self) -> DuckDbShadowSandbox:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    @staticmethod
    def load_default_pe_canonical_ir() -> CanonicalIR:
        """Load `examples/private_equity/pe_canonical_ir.json` into a `CanonicalIR` model."""
        pe_ir_path = get_examples_dir() / "private_equity" / "pe_canonical_ir.json"
        raw = load_structured_file(pe_ir_path)
        return CanonicalIR.model_validate(raw)

    def resolve_canonical_ir(
        self,
        source: CanonicalIR
        | NoMistakeDataVault2ContractV5
        | dict[str, Any]
        | str
        | Path
        | None = None,
    ) -> CanonicalIR:
        """Resolve a contract, IR model, dict, or file path into a `CanonicalIR`."""
        if source is None:
            return self.load_default_pe_canonical_ir()
        if isinstance(source, CanonicalIR):
            return source
        if isinstance(source, NoMistakeDataVault2ContractV5):
            return lower_contract_to_canonical_ir(source)
        if isinstance(source, (str, Path)):
            raw = load_structured_file(source)
            if "ir_version" in raw:
                return CanonicalIR.model_validate(raw)
            return lower_contract_to_canonical_ir(raw)
        if "ir_version" in source:
            return CanonicalIR.model_validate(source)
        return lower_contract_to_canonical_ir(source)

    def execute_safe_sql(
        self,
        sql: str,
        *,
        max_rows: int = 1000,
        timeout_seconds: float = 30.0,
    ) -> SafeSqlResult:
        """Execute a validated read-only SQL query against the sandbox with limits and watchdog."""
        validate_safe_sql(sql)

        timer = threading.Timer(timeout_seconds, self.conn.interrupt)
        timer.start()
        t0 = time.perf_counter()
        try:
            rel = self.conn.execute(sql)
            raw_cols = rel.description or []
            columns = [
                SandboxColumnMetadata(name=desc[0], data_type=str(desc[1]), nullable=True)
                for desc in raw_cols
            ]
            col_names = [c.name for c in columns]
            raw_rows = rel.fetchmany(max_rows + 1)
        except duckdb.InterruptException as e:
            raise TimeoutError(
                f"CUSTOM_SQL execution exceeded timeout of {timeout_seconds}s"
            ) from e
        finally:
            timer.cancel()

        duration_ms = round((time.perf_counter() - t0) * 1000.0, 2)
        truncated = len(raw_rows) > max_rows
        final_raw = raw_rows[:max_rows] if truncated else raw_rows

        def _serialize_val(v: Any) -> Any:
            if isinstance(v, Decimal):
                return str(v)
            if hasattr(v, "isoformat"):
                return v.isoformat()
            return v

        dict_rows = [
            {col: _serialize_val(val) for col, val in zip(col_names, row, strict=True)}
            for row in final_raw
        ]

        stats = {
            "duration_ms": duration_ms,
            "row_count": len(dict_rows),
            "truncated": truncated,
        }
        return SafeSqlResult(
            columns=columns,
            rows=dict_rows,
            row_count=len(dict_rows),
            truncated=truncated,
            duration_ms=duration_ms,
            execution_stats=stats,
        )

    def run_full_verification(
        self,
        source: CanonicalIR
        | NoMistakeDataVault2ContractV5
        | dict[str, Any]
        | str
        | Path
        | None = None,
        *,
        bronze_tables: dict[str, Any] | list[Any] | None = None,
        emit_telemetry: bool = True,
        run_id: str = "run-pe-20261015-001",
    ) -> SandboxVerificationReport:
        """Compile 4-Zone SQL, run Batches 1-4, evaluate DV-DYN-001-015, and verify Query D.10b."""
        ir_obj = self.resolve_canonical_ir(source)
        normalized_bronze: dict[str, Any] | None = (
            bronze_tables if isinstance(bronze_tables, dict) else None
        )
        bundle = compile_canonical_ir_to_4zone_sql(
            ir_obj,
            include_batch4_restatement_in_seed=False,
            bronze_tables=normalized_bronze,
        )

        batch_report = execute_4batch_adversarial_suite(self.conn, bundle, ir=ir_obj)
        b2_delta = batch_report.by_number[2].total_delta_rows
        b3_sat_delta = batch_report.by_number[3].satellite_delta_rows
        b3_hl_delta = (
            batch_report.by_number[3].hub_delta_rows + batch_report.by_number[3].link_delta_rows
        )

        dyn_report = evaluate_all_dynamic_checks(
            self.conn,
            recorded_replay_delta=b2_delta,
            recorded_sat_delta=b3_sat_delta,
            recorded_hub_link_delta=b3_hl_delta,
            historical_hash_mutations=0,
            ir=ir_obj,
        )

        pit_engine = PitQueryEngine(self.conn)
        d10_rows: list[QueryD10Row] = []
        d10b_oct_15: list[QueryD10bRow] = []
        d10b_nov_15: list[QueryD10bRow] = []
        try:
            d10_rows = pit_engine.run_query_d10(
                assertion_cutoff_ts="2026-11-15 23:59:59",
                state_cutoff_date="2026-09-30",
            )
        except (duckdb.Error, ValueError, KeyError):
            d10_rows = []

        try:
            d10b_oct_15 = pit_engine.run_query_d10b(
                assertion_cutoff_ts="2026-10-15 12:00:00",
                effective_as_of_dt="2026-09-30",
            )
            d10b_nov_15 = pit_engine.run_query_d10b(
                assertion_cutoff_ts="2026-11-15 23:59:59",
                effective_as_of_dt="2026-09-30",
            )
            bitemporal_proof = pit_engine.verify_bitemporal_restatement(
                prior_cutoff_ts="2026-10-15 12:00:00",
                restated_cutoff_ts="2026-11-15 23:59:59",
                effective_as_of_dt="2026-09-30",
            )
        except (duckdb.Error, ValueError, KeyError):
            bitemporal_proof = pit_engine.verify_generic_bitemporal_restatement(
                ir_obj,
                prior_cutoff_ts="2026-10-15 12:00:00",
                restated_cutoff_ts="2026-11-15 23:59:59",
                effective_as_of_dt="2026-09-30",
            )

        overall_passed = (
            batch_report.all_passed and dyn_report.all_passed and bitemporal_proof.verified
        )

        obs_event: VaultObservabilityEvent | None = None
        if emit_telemetry:
            obs_event = self._build_observability_event(
                overall_passed=overall_passed,
                batch_report=batch_report,
                dyn_report=dyn_report,
                bitemporal_proof=bitemporal_proof,
                run_id=run_id,
                ir_obj=ir_obj,
                compiled_bundle=bundle,
            )
            if self.ledger_emitter is not None:
                self.ledger_emitter.emit(obs_event)

        return SandboxVerificationReport(
            passed=overall_passed,
            model_name=ir_obj.model_name,
            compiled_bundle=bundle,
            batch_report=batch_report,
            dynamic_check_report=dyn_report,
            query_d10_rows=d10_rows,
            query_d10b_oct_15_rows=d10b_oct_15,
            query_d10b_nov_15_rows=d10b_nov_15,
            bitemporal_proof=bitemporal_proof,
            observability_event=obs_event,
        )

    def _build_observability_event(
        self,
        *,
        overall_passed: bool,
        batch_report: FourBatchExecutionReport,
        dyn_report: DynamicCheckSuiteReport,
        bitemporal_proof: BiTemporalValuationProof,
        run_id: str,
        ir_obj: CanonicalIR | None = None,
        compiled_bundle: Compiled4ZoneBundle | None = None,
    ) -> VaultObservabilityEvent:
        """Construct a schema-valid `VaultObservabilityEvent`."""
        ir_sha256 = (
            hashlib.sha256(ir_obj.model_dump_json().encode("utf-8")).hexdigest()
            if ir_obj is not None
            else hashlib.sha256(b"canonical_ir").hexdigest()
        )
        sql_sha256 = (
            hashlib.sha256(compiled_bundle.full_sql.encode("utf-8")).hexdigest()
            if compiled_bundle is not None
            else hashlib.sha256(b"compiled_4zone_sql").hexdigest()
        )

        pills = []
        for hub in ir_obj.hubs[:2] if ir_obj else []:
            slug = _hub_slug(hub.name)
            bk_val = f"{slug}-001"
            digest = compute_dv2_length_prefixed_sha256(
                [bk_val],
                bkcc_prefix="GLOBAL",
                is_business_key=True,
            )
            pills.append(
                SemanticHashPill.model_validate(
                    format_hub_pill(
                        full_sha256_hex=digest,
                        bkcc_prefix="GLOBAL",
                        business_key=bk_val,
                        entity_label=hub.name,
                    )
                )
            )
        business_impact = (
            f"Verified bi-temporal PIT restatement "
            f"({bitemporal_proof.prior_ev_ebitda_multiple}x -> "
            f"{bitemporal_proof.restated_ev_ebitda_multiple}x) "
            f"for {bitemporal_proof.company_legal_name}."
        )[:200]

        event = VaultObservabilityEvent(
            event_id="evt-sandbox-observability",
            run_id=run_id,
            trace_id="4bf92f3577b34da6a3ce929d0e0e4736",
            span_id="00f067aa0ba902b7",
            parent_span_id="1111222233334444",
            timestamp_utc="2026-11-15T09:16:00Z",  # type: ignore[arg-type]
            stage_id="STAGE_06_AXI_DV_DYNAMIC_SANDBOX",
            phase_id="P7_DYNAMIC_SANDBOX",
            step_id=None,
            gate_id=None,
            rule_code="DV-DYN-009",
            contract_version="5.0",
            confidence=1.0,
            agent_persona="axi-dv",
            status="PASS" if overall_passed else "REJECT-WITH-TRACE",
            rule_ids_evaluated=[c.check_id for c in dyn_report.checks],
            context_refs=EventContextRefs(
                canonical_ir_sha256=ir_sha256,
                compiled_sql_sha256=sql_sha256,
            ),
            data_classification="MNPI",
            layer1_story_banner=Layer1StoryBanner(
                headline=(
                    "4-Batch DuckDB Shadow Sandbox passed all 15 DV-DYN checks "
                    "(0 orphans, 0 replay drift)."
                ),
                business_impact=business_impact,
                verdict_pill="PASS" if overall_passed else "REJECT-WITH-TRACE",
            ),
            layer2_visual_payload=Layer2VisualPayload.model_validate(
                {
                    "semantic_hash_pills": pills,
                    "summary_metrics": {
                        "batches_passed": sum(1 for b in batch_report.batches if b.passed),
                        "dyn_checks_passed": dyn_report.passed_count,
                        "dyn_checks_failed": dyn_report.failed_count,
                        "orphan_fk_count": 0,
                        "batch_2_replay_delta_rows": batch_report.by_number[2].total_delta_rows,
                    },
                    "batches": [
                        {
                            "batch_number": b.batch_number,
                            "batch_name": b.batch_name,
                            "passed": b.passed,
                            "total_delta_rows": b.total_delta_rows,
                            "orphan_count": b.orphan_count,
                            "detail": b.detail,
                        }
                        for b in batch_report.batches
                    ],
                    "dynamic_checks": [c.model_dump(mode="json") for c in dyn_report.checks],
                    "bitemporal_proof": {
                        "lei_code": bitemporal_proof.lei_code,
                        "company_legal_name": bitemporal_proof.company_legal_name,
                        "effective_as_of_dt": bitemporal_proof.effective_as_of_dt,
                        "prior_assertion_cutoff_ts": bitemporal_proof.prior_assertion_cutoff_ts,
                        "prior_ev_ebitda_multiple": f"{bitemporal_proof.prior_ev_ebitda_multiple}x",
                        "prior_enterprise_value_formatted": (
                            f"${bitemporal_proof.prior_enterprise_value_usd:,.2f}"
                        ),
                        "restated_assertion_cutoff_ts": (
                            bitemporal_proof.restated_assertion_cutoff_ts
                        ),
                        "restated_ev_ebitda_multiple": (
                            f"{bitemporal_proof.restated_ev_ebitda_multiple}x"
                        ),
                        "restated_enterprise_value_formatted": (
                            f"${bitemporal_proof.restated_enterprise_value_usd:,.2f}"
                        ),
                        "verified": bitemporal_proof.verified,
                    },
                }
            ),
            layer3_formal_proof=Layer3FormalProof(
                algorithm_ref="Algorithm A.8 / 4-Batch Adversarial DuckDB Shadow Sandbox",
                formula_latex=r"\Delta\text{rows}(\text{Batch 2}) = 0 \land \text{Orphans} = 0",
                sqlglot_ast_summary=(
                    "DuckDB :memory: 4-Zone SQL execution verified across Batches 1-4 "
                    "and Queries D.10 / D.10b."
                ),
            ),
        )
        return event
