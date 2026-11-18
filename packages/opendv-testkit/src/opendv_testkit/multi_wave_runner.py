"""MultiWaveScenarioRunner: Test-only sequential wave pipeline orchestration runner.

Chains single-run executions asserting DV-EVO-001..008 and hash_diff continuity.
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from pathlib import Path
from typing import Any

import duckdb
from pydantic import BaseModel, ConfigDict, Field

from opendv_agent_harness import AgenticPipelineReport
from opendv_agent_harness.pipeline.spec import (
    PipelineInputSpec,
    PipelineSourceFeedInput,
    PipelineUnstructuredDocInput,
)
from opendv_axi_verifier.verifiers.alg_a7_evolution_lock import verify_additive_evolution_invariants
from opendv_contracts.dtos.pipeline import PipelineInputDTO
from opendv_core.models.vault_blueprint import VaultBlueprint
from opendv_testkit.runner import build_deterministic_pipeline_runner


class EvolutionLockViolationError(Exception):
    """Raised when DV-EVO-001..008 additive evolution invariants are violated."""

    def __init__(self, message: str, violations: list[str] | None = None) -> None:
        super().__init__(message)
        self.violations = violations or []


class HashDiffContinuityError(EvolutionLockViolationError):
    """Raised when historical hash_diff continuity is violated across warehouse waves."""

    def __init__(
        self,
        message: str,
        table: str | None = None,
        pk_key: Any = None,
        expected_hash: str | None = None,
        actual_hash: str | None = None,
    ) -> None:
        super().__init__(message, [message])
        self.table, self.pk_key = table, pk_key
        self.expected_hash, self.actual_hash = expected_hash, actual_hash


class _BaseModel(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, extra="allow")


class MultiWaveStepResult(_BaseModel):
    wave_index: int
    wave_name: str = ""
    status: str = "COMPLETED"
    passed: bool = True
    report: AgenticPipelineReport | None = None
    blueprint: VaultBlueprint | None = None
    contract: dict[str, Any] = Field(default_factory=dict)
    evolution_diff: dict[str, Any] = Field(default_factory=dict)
    violations: list[str] = Field(default_factory=list)
    recorded_tables: list[str] = Field(default_factory=list)
    hash_diff_row_count: int = 0


class MultiWaveScenarioReport(_BaseModel):
    scenario_name: str = "multi_wave_scenario"
    passed: bool = True
    total_waves: int = 0
    step_results: list[MultiWaveStepResult] = Field(default_factory=list)
    final_blueprint: VaultBlueprint | None = None
    final_contract: dict[str, Any] = Field(default_factory=dict)
    cumulative_evolution_diffs: list[dict[str, Any]] = Field(default_factory=list)
    summary_markdown: str = ""


class MultiWaveScenarioRunner:
    """Test-only orchestration runner chaining sequential single-run pipeline executions."""

    def __init__(
        self,
        duckdb_conn: duckdb.DuckDBPyConnection | None = None,
        pipeline_runner: Callable[..., AgenticPipelineReport] | None = None,
        *,
        output_dir: Path | str | None = None,
        base_dir: Path | str | None = None,
        fail_closed: bool = True,
    ) -> None:
        self._owns_conn = duckdb_conn is None
        self.conn = duckdb_conn or duckdb.connect(":memory:")
        self.pipeline_runner = pipeline_runner or build_deterministic_pipeline_runner()
        self.output_dir = Path(output_dir).resolve() if output_dir else None
        self.base_dir = Path(base_dir).resolve() if base_dir else None
        self.fail_closed = fail_closed
        self.waves: list[MultiWaveStepResult] = []
        self.current_blueprint: VaultBlueprint | None = None
        self.current_contract: dict[str, Any] | None = None
        self.baseline_schemas: dict[int, Any] = {}
        self.recorded_hash_diffs: dict[tuple[str, str, tuple], str] = {}

    def close(self) -> None:
        if self._owns_conn and self.conn is not None:
            with contextlib.suppress(duckdb.Error, RuntimeError):
                self.conn.close()

    def execute_wave(
        self,
        wave_input: PipelineInputDTO | PipelineInputSpec | dict[str, Any],
        wave_index: int | None = None,
        wave_name: str | None = None,
        *,
        assert_evolution_lock: bool = True,
        validate_hash_continuity: bool = True,
        contract_override: dict[str, Any] | None = None,
    ) -> MultiWaveStepResult:
        w_idx = len(self.waves) if wave_index is None else wave_index
        w_name = wave_name or f"wave_{w_idx}"
        spec = self._prepare_wave_spec(wave_input, w_idx)
        out_dir = (self.output_dir / w_name) if self.output_dir else None
        if out_dir:
            out_dir.mkdir(parents=True, exist_ok=True)
        report = self.pipeline_runner(spec, output_dir=out_dir, base_dir=self.base_dir)
        next_bp = report.blueprint
        c_obj = getattr(next_bp, "contract", None) if next_bp else None
        next_cnt = (
            contract_override
            if contract_override is not None
            else (
                c_obj.model_dump(mode="json") if hasattr(c_obj, "model_dump") else dict(c_obj or {})
            )
        )

        sql = (
            report.four_zone_sql.get("duckdb", {}).get("all_zones")
            if report.four_zone_sql
            else None
        )
        violations: list[str] = []
        if w_idx >= 1 and assert_evolution_lock and self.current_contract:
            if not next_cnt:
                violations = [
                    f"Prior contract existed in wave {w_idx - 1} "
                    f"but {w_name} produced an empty or missing contract."
                ]
                if self.fail_closed:
                    raise EvolutionLockViolationError(
                        f"Breaking DV 2.0 evolution in {w_name}: {violations}", violations
                    )
            else:
                violations = verify_additive_evolution_invariants(self.current_contract, next_cnt)
                if violations and self.fail_closed:
                    raise EvolutionLockViolationError(
                        f"Breaking DV 2.0 evolution in {w_name}: {violations}", violations
                    )

        if self.fail_closed and (not report.overall_passed or next_bp is None):
            failed_steps = [
                f"{s.stage_id or s.stage_name}: {s.errors or s.status or s.summary}"
                for s in getattr(report, "step_reports", [])
                if s.status != "PASS" or s.errors
            ]
            raise RuntimeError(
                f"Step {w_name} (index {w_idx}) failed execution "
                f"without producing a valid blueprint. status={report.status}, "
                f"passed={report.overall_passed}, failed_steps={failed_steps}"
            )

        if sql:
            self._apply_sql_to_warehouse(sql)

        tables = self._snapshot_warehouse_schemas(w_idx)
        if w_idx >= 1 and validate_hash_continuity:
            self.validate_hash_diff_continuity()
        hash_count = self._record_warehouse_hash_diffs()
        self.current_blueprint, self.current_contract = next_bp, next_cnt
        result = MultiWaveStepResult(
            wave_index=w_idx,
            wave_name=w_name,
            status=report.status,
            passed=(bool(report.overall_passed) and not violations),
            report=report,
            blueprint=next_bp,
            contract=next_cnt,
            evolution_diff=report.evolution_diff or {},
            violations=violations,
            recorded_tables=tables,
            hash_diff_row_count=hash_count,
        )
        self.waves.append(result)
        return result

    def execute_scenario(
        self,
        waves: list[PipelineInputDTO | PipelineInputSpec | dict[str, Any]],
        scenario_name: str = "multi_wave_scenario",
        *,
        assert_evolution_lock: bool = True,
        validate_hash_continuity: bool = True,
    ) -> MultiWaveScenarioReport:
        step_results: list[MultiWaveStepResult] = []
        cumulative_diffs: list[dict[str, Any]] = []
        all_passed = True
        for idx, w_in in enumerate(waves):
            step = self.execute_wave(
                w_in,
                wave_index=idx,
                assert_evolution_lock=assert_evolution_lock,
                validate_hash_continuity=validate_hash_continuity,
            )
            step_results.append(step)
            if step.evolution_diff:
                cumulative_diffs.append(step.evolution_diff)
            if not step.passed:
                all_passed = False

        md = self._generate_scenario_summary_markdown(scenario_name, step_results)
        return MultiWaveScenarioReport(
            scenario_name=scenario_name,
            passed=all_passed,
            total_waves=len(step_results),
            step_results=step_results,
            final_blueprint=self.current_blueprint,
            final_contract=self.current_contract or {},
            cumulative_evolution_diffs=cumulative_diffs,
            summary_markdown=md,
        )

    def _prepare_wave_spec(self, wave_input: Any, w_idx: int) -> PipelineInputSpec:
        bp = self.current_blueprint if w_idx >= 1 else None
        if w_idx >= 1 and bp is None and self.fail_closed:
            raise RuntimeError(
                f"Step {w_idx} cannot execute: prior step did not produce a valid VaultBlueprint."
            )
        if isinstance(wave_input, PipelineInputSpec):
            return (
                wave_input.model_copy(
                    update={"blueprint": bp, "ontology": None, "initial_contract": None}
                )
                if bp
                else wave_input
            )
        d = (
            wave_input.model_dump(mode="python", exclude_none=True)
            if hasattr(wave_input, "model_dump")
            else dict(wave_input)
        )
        if bp:
            d["blueprint"] = bp
            for k in (
                "ontology",
                "ontology_source",
                "initial_contract",
                "graph_a_source",
                "graph_a_ontology_path",
            ):
                d.pop(k, None)
        elif "ontology_source" in d and "ontology" not in d:
            d["ontology"] = d.pop("ontology_source")
        if "sources" in d:
            d["sources"] = [PipelineSourceFeedInput.model_validate(s) for s in d["sources"]]
        if "unstructured_documents" in d:
            d["unstructured_documents"] = [
                PipelineUnstructuredDocInput.model_validate(u) for u in d["unstructured_documents"]
            ]
        return PipelineInputSpec.model_validate(d)

    def _apply_sql_to_warehouse(self, sql: str) -> None:
        for s in (stmt.strip() for stmt in sql.split(";")):
            if s and not all(
                line.strip().startswith("--") for line in s.splitlines() if line.strip()
            ):
                self.conn.execute(s)

    def _snapshot_warehouse_schemas(self, wave_index: int) -> list[str]:
        q = (
            "SELECT table_schema, table_name, column_name, data_type, ordinal_position "
            "FROM information_schema.columns WHERE table_schema IN "
            "('staging', 'raw_vault', 'business_vault', 'information_mart', 'bronze')"
        )
        s_dict: dict[str, list[dict[str, Any]]] = {}
        for s, t, col, dt, pos in self.conn.execute(q).fetchall():
            s_dict.setdefault(f"{s}.{t}", []).append({"col": col, "type": dt, "pos": pos})
        self.baseline_schemas[wave_index] = s_dict
        return sorted(s_dict.keys())

    def snapshot_warehouse_hash_diffs(self) -> dict[tuple[str, str, tuple], str]:
        q = (
            "SELECT DISTINCT table_schema, table_name FROM information_schema.columns "
            "WHERE column_name = 'hash_diff' AND table_schema IN ('raw_vault', 'business_vault')"
        )
        res: dict[tuple[str, str, tuple], str] = {}
        for s, t in self.conn.execute(q).fetchall():
            cols = [c[0] for c in self.conn.execute(f"DESCRIBE {s}.{t}").fetchall()]
            pks = [
                c for c in cols if c.startswith("hk_") or c.startswith("sub_") or c == "load_dts"
            ] or [cols[0]]
            pk_cols = ", ".join(pks)
            for r in self.conn.execute(f"SELECT {pk_cols}, hash_diff FROM {s}.{t}").fetchall():
                res[(s, t, tuple(r[:-1]))] = str(r[-1]) if r[-1] is not None else ""
        return res

    def _record_warehouse_hash_diffs(self) -> int:
        self.recorded_hash_diffs.update(self.snapshot_warehouse_hash_diffs())
        return len(self.recorded_hash_diffs)

    def validate_hash_diff_continuity(self) -> bool:
        curr = self.snapshot_warehouse_hash_diffs()
        for (s, t, pk), prev_h in self.recorded_hash_diffs.items():
            curr_h = curr.get((s, t, pk))
            tbl = f"{s}.{t}"
            if curr_h is None:
                raise HashDiffContinuityError(
                    f"Row disappeared from {tbl} with PK {pk}",
                    table=tbl,
                    pk_key=pk,
                    expected_hash=prev_h,
                )
            if curr_h != prev_h:
                raise HashDiffContinuityError(
                    f"Hash diff mismatch on {tbl} PK {pk}: expected '{prev_h}', got '{curr_h}'",
                    table=tbl,
                    pk_key=pk,
                    expected_hash=prev_h,
                    actual_hash=curr_h,
                )
        return True

    def _generate_scenario_summary_markdown(
        self, name: str, steps: list[MultiWaveStepResult]
    ) -> str:
        lines = [
            f"# Multi-Wave Report: {name} (Waves: {len(steps)})",
            "| Wave | Status | Rev | Hubs | Links | Sats | Hashes |",
            "|---|---|---|---|---|---|---|",
        ]
        for s in steps:
            lin = getattr(s.blueprint, "lineage", None)
            rev = lin.blueprint_revision if lin else 1
            d = s.evolution_diff or {}
            h = len(d.get("added_hubs", []))
            lk = len(d.get("added_links", []))
            st = len(d.get("added_satellites", []))
            lines.append(
                f"| {s.wave_name} | {s.status} | {rev} | {h} | {lk} | {st} | "
                f"{s.hash_diff_row_count} |"
            )
        return "\n".join(lines)


__all__ = [
    "EvolutionLockViolationError",
    "HashDiffContinuityError",
    "MultiWaveScenarioReport",
    "MultiWaveScenarioRunner",
    "MultiWaveStepResult",
]
