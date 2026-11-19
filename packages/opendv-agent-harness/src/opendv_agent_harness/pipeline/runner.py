"""Single-run coordinator and public orchestrator for Open-DV pipeline."""

from __future__ import annotations

import asyncio
import os
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import duckdb

from opendv_agent_harness.adapters.antigravity_adapter import ProviderConfigurationError
from opendv_agent_harness.hold_manager import StewardHoldManager
from opendv_agent_harness.pipeline.axi_repair_loop import (
    execute_resume_from_hold,
    execute_stage_05_static_gates,
)
from opendv_agent_harness.pipeline.config import HarnessSetupConfig, _load_yaml_or_json
from opendv_agent_harness.pipeline.discovery_tabular import execute_stage_01_tabular
from opendv_agent_harness.pipeline.discovery_unstructured import execute_stage_02_unstructured
from opendv_agent_harness.pipeline.drift_projection import (
    emit_runner_stream_event,
    materialize_intermediate_contract_artifact,
    project_stage_drift_findings,
    record_runner_fallback,
    resolve_runner_harness,
)
from opendv_agent_harness.pipeline.mode_detector import (
    execute_stage_00_orchestration,
    resolve_initial_blueprint_and_ontology,
)
from opendv_agent_harness.pipeline.sandbox_compiler import (
    execute_stage_06_dynamic_sandbox,
    execute_stage_07_compilation,
)
from opendv_agent_harness.pipeline.spec import (
    AgenticPipelineReport,
    PipelineArtifactRef,
    PipelineInputSpec,
    PipelineStepReport,
    PipelineWaveInput,
    PipelineWaveReport,
)
from opendv_agent_harness.pipeline.stages_agentic import (
    execute_stages_03_and_04,
    resolve_stage_model,
)
from opendv_agent_harness.pipeline.validation_postflight import (
    build_completed_pipeline_report,
    build_paused_pipeline_report,
    build_preflight_failure_report,
    build_rejected_pipeline_report,
    materialize_final_pipeline_outputs,
)
from opendv_agent_harness.pipeline.validation_preflight import validate_pipeline_inputs
from opendv_core.models.contract_v5 import NoMistakeDataVault2ContractV5
from opendv_core.models.graph_a import GraphAOntology
from opendv_core.models.vault_blueprint import VaultBlueprint


class AgenticVaultPipeline:
    """Executes the Data Vault 2.0 single-run and multi-wave pipeline lifecycle."""

    def __init__(
        self,
        input_spec: PipelineInputSpec,
        harness_setup: HarnessSetupConfig | None = None,
        *,
        output_dir: Path | None = None,
        base_dir: Path | None = None,
        cas_store: Any | None = None,
        sdk_module: Any | None = None,
        harness: Any | None = None,
        extension_hooks: Any | None = None,
        on_stage_start: Callable[[int, str, str, str], None] | None = None,
        on_stage_complete: Callable[[int, PipelineStepReport], None] | None = None,
        on_stream_event: Callable[[Any], None] | None = None,
        is_server: bool = False,
        allowed_hosts: list[str] | None = None,
        **kwargs: Any,
    ) -> None:
        self.input_spec = input_spec
        self.harness_setup = harness_setup or HarnessSetupConfig()
        self.workspace_root = Path(os.environ.get("OPENDV_WORKSPACE_ROOT") or Path.cwd()).resolve()
        self.base_dir = (base_dir or self.workspace_root).resolve()
        self.output_dir = (
            Path(output_dir).resolve() if output_dir else (self.base_dir / "artifacts")
        )
        self.cas_store = cas_store
        self.sdk_module = sdk_module
        self.harness = harness
        self.extension_hooks = extension_hooks
        self.on_stage_start = on_stage_start
        self.on_stage_complete = on_stage_complete
        self.on_stream_event = on_stream_event
        self.is_server = is_server
        self.allowed_hosts = allowed_hosts
        self.run_id = getattr(self.input_spec, "run_id", None) or f"run_{uuid.uuid4().hex[:12]}"
        self.hold_manager = StewardHoldManager(state_dir=self.output_dir / "state")
        self.ontology: GraphAOntology | None = None
        self.graph_a_sha256: str | None = None
        self.fallbacks_applied: list[dict[str, Any]] = []
        self.observability_events: list[dict[str, Any]] = []

        if self.harness is None:
            self.harness = resolve_runner_harness(
                self.harness_setup,
                workspace_root=self.workspace_root,
                output_dir=self.output_dir,
                sdk_module=self.sdk_module,
            )

    def _notify_stage_start(
        self, wave_index: int, stage_id: str, persona: str, model_used: str
    ) -> None:
        if self.on_stage_start is not None:
            self.on_stage_start(wave_index, stage_id, persona, model_used)

    def _notify_stage_complete(self, wave_index: int, step_report: PipelineStepReport) -> None:
        if self.on_stage_complete is not None:
            self.on_stage_complete(wave_index, step_report)

    def record_fallback(
        self,
        wave_index: int,
        stage_id: Any,
        details: dict[str, Any] | None = None,
        fallback_key: str = "stage_output_policy.symbolic_completion",
    ) -> None:
        record_runner_fallback(self, wave_index, stage_id, details, fallback_key)

    def _emit_stream(
        self, text: str, stage_id: str = "STAGE_00_ORCHESTRATION", persona: str = "vault-first-mate"
    ) -> None:
        emit_runner_stream_event(self, text, stage_id=stage_id, persona=persona)

    def resolve_initial_state(
        self,
    ) -> tuple[VaultBlueprint | None, GraphAOntology, NoMistakeDataVault2ContractV5 | None]:
        bp, ont, cnt, loaded = resolve_initial_blueprint_and_ontology(
            self.input_spec, self.base_dir, self.workspace_root, self.cas_store
        )
        self._loaded_graph_a = loaded
        return bp, ont, cnt

    def _materialize_intermediate_contract(self, contract_dict: dict[str, Any] | None) -> None:
        materialize_intermediate_contract_artifact(self.output_dir, self.cas_store, contract_dict)

    async def run_async(self) -> AgenticPipelineReport:
        t0 = datetime.now(UTC)

        preflight = validate_pipeline_inputs(
            self.input_spec, self.harness_setup,
            base_dir=self.base_dir, workspace_root=self.workspace_root,
        )

        if not preflight.passed:
            return build_preflight_failure_report(self, t0, preflight)

        self.output_dir.mkdir(parents=True, exist_ok=True)
        in_bp, ontology, deployed_contract = self.resolve_initial_state()
        self.ontology = ontology
        if self._loaded_graph_a:
            self.graph_a_sha256 = self._loaded_graph_a.sha256

        conn = duckdb.connect(":memory:")
        wave_reports: list[PipelineWaveReport] = []
        step_reports: list[PipelineStepReport] = []
        artifacts: list[PipelineArtifactRef] = []
        final_artifacts: dict[str, PipelineArtifactRef] = {}
        resolved_holds: list[dict[str, Any]] = []
        drift_findings: list[dict[str, Any]] = []
        rec_errors: list[str] = []

        curr_contract = (
            deployed_contract.model_dump(mode="json", exclude_none=True)
            if deployed_contract
            else None
        )
        out_blueprint = in_bp
        four_zone_sql: dict[str, Any] = {}
        diff_summary: dict[str, Any] = {}
        inference_plan = None

        waves_to_run = (
            self.input_spec.waves
            if getattr(self.input_spec, "waves", None)
            else [
                PipelineWaveInput(
                    wave_index=0, wave_name="single_run",
                    sources=self.input_spec.sources,
                    unstructured_documents=self.input_spec.unstructured_documents,
                )
            ]
        )

        try:
            for w_idx, wave in enumerate(waves_to_run):
                drift_findings = []
                wave_sources = list(wave.sources or [])
                wave_docs = list(wave.unstructured_documents or [])
                wave_steps: list[PipelineStepReport] = []

                # Stage 00: Mode Detection
                inference_plan = await execute_stage_00_orchestration(
                    runner=self, w_idx=w_idx, in_bp=in_bp, curr_contract=curr_contract,
                    wave_sources=wave_sources, wave_docs=wave_docs,
                    wave_steps=wave_steps, step_reports=step_reports,
                )

                pri_act = getattr(inference_plan, "primary_action", None)
                drift_acts = ("INTRA_SOURCE_SCHEMA_DRIFT", "SCHEMA_DRIFT", "INTRA_SOURCE_DRIFT")
                is_cold = (not deployed_contract and not in_bp and not curr_contract and w_idx == 0)
                w_type = getattr(wave, "wave_type", None) or (
                    "SCHEMA_DRIFT" if pri_act in drift_acts
                    else "UNSTRUCTURED_ONBOARDING" if (wave_docs and not wave_sources)
                    else "COLD_START" if is_cold
                    else "INCREMENTAL_SOURCE"
                )
                is_fault_injected = getattr(
                    self.harness_setup, "inject_adversarial_fault_on_attempt_1", False
                )
                codemods: list[str] = []
                if w_type == "COLD_START" or is_cold or pri_act == "COLD_START_N1":
                    codemods = ["ALG_A1_3AXIS_SATELLITE_SPLIT", "ALG_A4_COLD_START_EXTENSIBILITY"]
                has_drift = w_type == "SCHEMA_DRIFT" or pri_act in drift_acts or drift_findings
                if has_drift and "ALG_A9A_INTRA_SOURCE_SCHEMA_DRIFT" not in codemods:
                    codemods.append("ALG_A9A_INTRA_SOURCE_SCHEMA_DRIFT")

                # Stage 01: Tabular Discovery
                s1_out, paused_reason = await execute_stage_01_tabular(
                    runner=self, w_idx=w_idx, wave_sources=wave_sources, conn=conn,
                    wave_steps=wave_steps, step_reports=step_reports, artifacts=artifacts,
                    rec_errors=rec_errors,
                )
                projected_drift = project_stage_drift_findings(
                    curr_contract, getattr(s1_out, "discovered_feeds", [])
                )
                if paused_reason:
                    return build_paused_pipeline_report(
                        self,
                        t0,
                        preflight,
                        step_reports,
                        artifacts,
                        paused_reason,
                        drift_findings=projected_drift,
                    )

                # Stage 02: Unstructured Discovery
                _s2_out, s2_status, s2_reason = await execute_stage_02_unstructured(
                    runner=self, w_idx=w_idx, wave_docs=wave_docs, curr_contract=curr_contract,
                    wave_steps=wave_steps, step_reports=step_reports, resolved_holds=resolved_holds,
                )
                if s2_status == "PAUSE":
                    p_why = s2_reason or "TIER_U3_AMBIGUOUS_ENTITY"
                    return build_paused_pipeline_report(
                        self,
                        t0,
                        preflight,
                        step_reports,
                        artifacts,
                        p_why,
                        drift_findings=projected_drift,
                    )
                if s2_status == "REJECT":
                    r_why = s2_reason or "STAGE_02_SCOUT_UNSTRUCTURED"
                    return build_rejected_pipeline_report(
                        self, t0, preflight, step_reports, artifacts, r_why
                    )

                # Stages 03-04: Contract Synthesis
                contract_dict, _attempt1_contract = await execute_stages_03_and_04(
                    runner=self, w_idx=w_idx, w_type=w_type, s1_out=s1_out,
                    wave_docs=wave_docs, curr_contract=curr_contract, inference_plan=inference_plan,
                    drift_findings=drift_findings, wave_steps=wave_steps, step_reports=step_reports,
                )
                if contract_dict is None:
                    last_step = step_reports[-1] if step_reports else None
                    failed_stage = last_step.stage_id if last_step else "STAGE_03_SHIP_VAULT_PASS1"
                    fail_msg = last_step.summary if last_step else f"{failed_stage} failed."
                    diag = [{
                        "code": "LLM_STAGE_OUTPUT_MISSING",
                        "severity": "ERROR",
                        "message": fail_msg,
                    }]
                    return build_rejected_pipeline_report(
                        self, t0, preflight, step_reports, artifacts,
                        stage_id=failed_stage, diagnostics=diag,
                    )

                has_kd = any(
                    (f.get("category") == "KEY_DRIFT" or f.get("action") == "ASK-USER")
                    if isinstance(f, dict)
                    else (
                        getattr(f, "category", None) == "KEY_DRIFT"
                        or getattr(f, "action", None) == "ASK-USER"
                    )
                    for f in drift_findings
                )
                if has_kd:
                    if self.harness_setup.steward_hold_policy == "reject_on_hold":
                        return build_rejected_pipeline_report(
                            self, t0, preflight, step_reports, artifacts,
                            stage_id="STAGE_04_SHIP_VAULT_PASS2", wave_reports=wave_reports,
                        )
                    if self.harness_setup.steward_hold_policy == "pause":
                        return build_paused_pipeline_report(
                            self, t0, preflight, step_reports, artifacts, "KEY_DRIFT"
                        )

                # Stage 05: Static Verification
                s05_persona, s05_id = "axi-dv", "STAGE_05_AXI_DV_STATIC_GATES"
                s05_model = resolve_stage_model(
                    s05_id, s05_persona, self.harness_setup, self.harness
                )
                self._notify_stage_start(w_idx, s05_id, s05_persona, s05_model)
                verified_contract, rej_rep = execute_stage_05_static_gates(
                    runner=self, w_idx=w_idx, w_type=w_type, contract_dict=contract_dict,
                    curr_contract=curr_contract, s1_profiles=s1_out.profile_summaries,
                    s05_id=s05_id, s05_persona=s05_persona, s05_model=s05_model,
                    is_fault_injected=is_fault_injected, codemods=codemods,
                    drift_findings=drift_findings, wave_steps=wave_steps,
                    step_reports=step_reports, wave_reports=wave_reports,
                    artifacts=artifacts, t0=t0, preflight=preflight,
                )
                if rej_rep is not None:
                    return rej_rep

                # Stage 06: Dynamic Sandbox
                s06_persona, s06_id = "axi-dv", "STAGE_06_AXI_DV_DYNAMIC_SANDBOX"
                s06_model = resolve_stage_model(
                    s06_id, s06_persona, self.harness_setup, self.harness
                )
                self._notify_stage_start(w_idx, s06_id, s06_persona, s06_model)
                s06_res = execute_stage_06_dynamic_sandbox(
                    runner=self, w_idx=w_idx, verified_contract=verified_contract,
                    s06_id=s06_id, s06_persona=s06_persona, s06_model=s06_model,
                    wave_steps=wave_steps, step_reports=step_reports,
                    t0=t0, preflight=preflight, artifacts=artifacts, wave_reports=wave_reports,
                )
                four_zone_sql, _sbx_summary, *s06_extra = s06_res
                if s06_extra and s06_extra[0] is not None:
                    return s06_extra[0]

                # Stage 07: Compilation
                has_u3 = any(
                    cand.er_tier == "Tier-U3" or getattr(cand, "confidence_score", 1.0) < 0.85
                    for doc in wave_docs
                    for cand in (
                        getattr(doc, "candidates", []) or getattr(doc, "extracted_candidates", [])
                    )
                )
                out_blueprint, curr_contract, diff_summary = execute_stage_07_compilation(
                    runner=self, w_idx=w_idx, w_type=w_type, wave=wave, in_bp=in_bp,
                    curr_contract=curr_contract, verified_contract=verified_contract,
                    wave_docs=wave_docs, drift_findings=drift_findings, codemods=codemods,
                    is_fault_injected=is_fault_injected, has_u3=has_u3, wave_steps=wave_steps,
                    step_reports=step_reports, wave_reports=wave_reports,
                    axi_res=getattr(self, "_last_axi_res", None), sbx_summary=_sbx_summary,
                )
                in_bp = out_blueprint

            # Materialize final outputs
            materialize_final_pipeline_outputs(
                output_dir=self.output_dir, cas_store=self.cas_store,
                out_blueprint=out_blueprint, curr_contract=curr_contract,
                four_zone_sql=four_zone_sql, graph_a_sha256=self.graph_a_sha256,
                loaded_graph_a=getattr(self, "_loaded_graph_a", None),
                artifacts=artifacts, final_artifacts=final_artifacts,
            )
            return build_completed_pipeline_report(
                runner=self, t0=t0, preflight=preflight, out_blueprint=out_blueprint,
                wave_reports=wave_reports, step_reports=step_reports,
                artifacts=artifacts, final_artifacts=final_artifacts,
                resolved_holds=resolved_holds, diff_summary=diff_summary,
                rec_errors=rec_errors, inference_plan=inference_plan,
                four_zone_sql=four_zone_sql,
            )
        except ProviderConfigurationError as exc:
            st = step_reports[-1].stage_id if step_reports else "STAGE_00_ORCHESTRATION"
            diag = [{"code": "PROVIDER_ERROR", "severity": "ERROR", "message": str(exc)}]
            return build_rejected_pipeline_report(
                self, t0, preflight, step_reports, artifacts, stage_id=st, diagnostics=diag
            )
        finally:
            conn.close()

    def run(self) -> AgenticPipelineReport:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop and loop.is_running():
            import nest_asyncio

            nest_asyncio.apply()
            return loop.run_until_complete(self.run_async())
        return asyncio.run(self.run_async())

    def resume_from_hold(
        self,
        hold_id: str,
        option: Any | None = None,
        *,
        steward_id: str = "steward",
        new_ontology: GraphAOntology | None = None,
        new_graph_a_sha256: str | None = None,
    ) -> AgenticPipelineReport:
        rec = getattr(self, "hold_manager", None) and self.hold_manager.get_hold(hold_id)
        if rec and rec.status != "PENDING":
            raise RuntimeError(
                f"Cannot resume pipeline: hold '{hold_id}' is {rec.status} (no paused wave context)"
            )
        return execute_resume_from_hold(
            runner=self, hold_id=hold_id, option=option, steward_id=steward_id,
            new_ontology=new_ontology, new_graph_a_sha256=new_graph_a_sha256,
        )


def run_agentic_vault_pipeline(
    input_spec: PipelineInputSpec | str | Path,
    harness_setup: HarnessSetupConfig | str | Path | None = None,
    *,
    output_dir: str | Path | None = None,
    base_dir: str | Path | None = None,
    cas_store: Any | None = None,
    sdk_module: Any | None = None,
    harness: Any | None = None,
    on_stage_start: Callable[[int, str, str, str], None] | None = None,
    on_stage_complete: Callable[[int, PipelineStepReport], None] | None = None,
    on_stream_event: Callable[[Any], None] | None = None,
    is_server: bool = False,
    allowed_hosts: list[str] | None = None,
    **kwargs: Any,
) -> AgenticPipelineReport:
    """Public functional entry point executing the Data Vault 2.0 pipeline."""
    eff_base = Path(base_dir).resolve() if base_dir is not None else None
    if isinstance(input_spec, (str, Path)):
        p = Path(input_spec).resolve()
        eff_base = eff_base or p.parent
        spec_obj = PipelineInputSpec.model_validate(_load_yaml_or_json(p))
    else:
        spec_obj = input_spec

    setup_obj = (
        HarnessSetupConfig.model_validate(_load_yaml_or_json(Path(harness_setup).resolve()))
        if isinstance(harness_setup, (str, Path))
        else (harness_setup or HarnessSetupConfig())
    )

    return AgenticVaultPipeline(
        input_spec=spec_obj, harness_setup=setup_obj,
        output_dir=Path(output_dir).resolve() if output_dir is not None else None,
        base_dir=eff_base, cas_store=cas_store, sdk_module=sdk_module, harness=harness,
        on_stage_start=on_stage_start, on_stage_complete=on_stage_complete,
        on_stream_event=on_stream_event, is_server=is_server, allowed_hosts=allowed_hosts,
    ).run()


def resume_agentic_vault_pipeline(
    pipeline: AgenticVaultPipeline,
    hold_id: str,
    option: Any | None = None,
    *,
    steward_id: str = "steward",
    new_ontology: GraphAOntology | None = None,
    new_graph_a_sha256: str | None = None,
) -> AgenticPipelineReport:
    """Resume a paused pipeline after hold resolution."""
    return pipeline.resume_from_hold(
        hold_id=hold_id,
        option=option,
        steward_id=steward_id,
        new_ontology=new_ontology,
        new_graph_a_sha256=new_graph_a_sha256,
    )


__all__ = [
    "AgenticVaultPipeline",
    "resume_agentic_vault_pipeline",
    "run_agentic_vault_pipeline",
]
