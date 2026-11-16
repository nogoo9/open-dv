/** Step 4: Pre-Flight Action Plan, SSE Console & Diff */
import type { BlueprintEvolutionDiffDTO, InferredPrimaryAction, PipelineRunHandle, VaultBlueprintDTO } from "@opendv/protocol-ts";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { parse as parseYaml } from "yaml";
import { getStudioAuthHeaders } from "../../ontology/store/studio-store";
import { EXPLORER_COLORS as C, EXPLORER_FONTS as F } from "../design-tokens";
import type { PipelineValidationReport } from "../modals/NewRunValidationSection";
import { DiffInspector } from "./DiffInspector";
import { INFERRED_ACTION_DESC as I_DESC, PIPELINE_STAGES, S_STEP4 as S, extractSqlString, fetchRunArtifacts, pollPipelineRun, resolveDiffFallback } from "./specBuilderUtils";
import type { Step4ValidateRunProps } from "./wizard-types";
import { formStateToPipelineInputDto, serializeSpecToYaml } from "./wizardSpecMapper";

export { PIPELINE_STAGES };

type RunResult = Record<string, any>;

export function Step4ValidateAndRun({
	formState,
	onChange,
	onRunStarted,
	createRunFn,
	validatePipelineFn,
	apiUrl = "/api/v1",
	fetchImpl = fetch,
	onEvolveSchema,
	onSyncOntologyDelta,
}: Step4ValidateRunProps) {
	const [isValidating, setIsValidating] = useState(false);
	const [validationReport, setValidationReport] = useState<PipelineValidationReport | null>(formState.validationReport ?? null);
	const [isExecuting, setIsExecuting] = useState(false);
	const [isComplete, setIsComplete] = useState(false);
	const [runHandle, setRunHandle] = useState<PipelineRunHandle | null>(null);
	const [executionResult, setExecutionResult] = useState<RunResult | null>(null);
	const [runError, setRunError] = useState<string | null>(null);
	const [currentStageIdx, setCurrentStageIdx] = useState<number>(-1);
	const [elapsedSeconds, setElapsedSeconds] = useState(0);

	const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

	const hasExistingContract = Boolean(formState.contract !== null || formState.contractContent.trim());
	const sourcesCount = formState.configuredSources.length;
	const docsCount = formState.unstructuredDocs.length;
	const isZeroData = sourcesCount === 0 && docsCount === 0;

	const inferredAction: InferredPrimaryAction = useMemo(() => {
		if (sourcesCount > 0 && docsCount > 0) return "HYBRID_PER_SOURCE";
		if (formState.inferredPlan?.primary_action) return formState.inferredPlan.primary_action;
		if (isZeroData) return hasExistingContract ? "VERIFIED_NO_OP" : "COLD_START_N1";
		if (docsCount > 0) return "UNSTRUCTURED_FIRST";
		return formState.ontologyEvolutionPolicy === "recommend" && hasExistingContract ? "SCHEMA_DRIFT" : (hasExistingContract ? "MULTI_SOURCE_EXPANSION" : "COLD_START_N1");
	}, [formState.inferredPlan, isZeroData, hasExistingContract, sourcesCount, docsCount, formState.ontologyEvolutionPolicy]);

	const candidateTables = useMemo(
		() => formState.configuredSources.flatMap((f) => (f.table_selection?.tables?.length ? f.table_selection.tables.map((t) => ({ system: f.source_system, name: t })) : [{ system: f.source_system, name: f.feed_id }])),
		[formState.configuredSources],
	);
	const expectedHubs = Math.max(1, candidateTables.length), expectedLinks = Math.max(0, candidateTables.length - 1), expectedSats = Math.max(1, candidateTables.length);
	const lastValidatedSpecRef = useRef<string>("");

	const runValidation = useCallback(async () => {
		const specYaml = serializeSpecToYaml(formStateToPipelineInputDto(formState));
		if (specYaml === lastValidatedSpecRef.current && validationReport) {
			return;
		}
		lastValidatedSpecRef.current = specYaml;
		setIsValidating(true);
		try {
			if (validatePipelineFn) {
				const rep = await validatePipelineFn({ input_spec_content: specYaml });
				setValidationReport(rep);
			} else {
				const fetcher = fetchImpl ?? (typeof fetch !== "undefined" ? fetch : null);
				if (!fetcher) throw new Error("No HTTP fetch implementation available");
				const res = await fetcher(`${apiUrl}/runs/validate`, {
					method: "POST",
					credentials: "include",
					headers: getStudioAuthHeaders({ "Content-Type": "application/json" }),
					body: JSON.stringify(formStateToPipelineInputDto(formState)),
				});
				if (res.ok) {
					const plan = (await res.json()) as Record<string, unknown>;
					const passed = plan.passed !== undefined ? Boolean(plan.passed) : false;
					const diags = Array.isArray(plan.diagnostics) ? plan.diagnostics : [];
					const count = typeof plan.checked_items_count === "number" ? plan.checked_items_count : (diags.length || 1);
					setValidationReport({
						passed,
						checked_items_count: count,
						diagnostics: diags.length > 0 ? diags : [{ severity: "INFO", message: `Inferred Plan: ${String(plan.primary_action ?? "COLD_START_N1")}` }],
					});
					if (plan?.primary_action && formState.inferredPlan?.primary_action !== plan.primary_action && onChange) {
						onChange({ inferredPlan: plan as any });
					}
				} else {
					setValidationReport({ passed: false, checked_items_count: 0, diagnostics: [{ severity: "ERROR", message: `Validation returned HTTP ${res.status}` }] });
				}
			}
		} catch (err) {
			setValidationReport({ passed: false, checked_items_count: 0, diagnostics: [{ severity: "ERROR", message: err instanceof Error ? err.message : "Validation network failure" }] });
		} finally {
			setIsValidating(false);
		}
	}, [apiUrl, fetchImpl, formState, onChange, validatePipelineFn, validationReport]);

	useEffect(() => {
		void runValidation();
	}, [runValidation]);

	const handleTriggerRun = async () => {
		setIsExecuting(true);
		setIsComplete(false);
		setRunError(null);
		setCurrentStageIdx(0);
		setElapsedSeconds(0);

		if (timerRef.current) clearInterval(timerRef.current);
		timerRef.current = setInterval(() => { setElapsedSeconds((s) => s + 1); }, 1000);

		try {
			const inputDto = formStateToPipelineInputDto(formState);
			const fetcher = fetchImpl ?? (typeof fetch !== "undefined" ? fetch : null);

			let rId = "";
			if (createRunFn) {
				const handle = await createRunFn({ dry_run: false, input_spec_content: serializeSpecToYaml(inputDto) });
				if (handle) { setRunHandle(handle); rId = handle.run_id; onRunStarted?.(handle); }
			} else {
				if (!fetcher) throw new Error("Fetch unavailable");
				if (!inputDto.blueprint && formState.activeSessionId) {
					try {
						const bpUrl = `${apiUrl}/ontology/studio/sessions/${encodeURIComponent(formState.activeSessionId)}/export-blueprint`;
						const r = await fetcher(bpUrl, { credentials: "include", headers: getStudioAuthHeaders(undefined, bpUrl) });
						if (r.ok) { const d = await r.json(); if (d?.blueprint) inputDto.blueprint = d.blueprint; }
					} catch {}
				}
				const res = await fetcher(`${apiUrl}/pipelines/runs`, {
					method: "POST",
					credentials: "include",
					headers: getStudioAuthHeaders({ "Content-Type": "application/json" }),
					body: JSON.stringify(inputDto),
				});
				if (!res.ok) throw new Error(`Pipeline run failed: HTTP ${res.status}`);
				const handle = (await res.json()) as PipelineRunHandle;
				rId = handle.run_id; setRunHandle(handle); onRunStarted?.(handle);
			}

			const headers = getStudioAuthHeaders();
			const statusData = await pollPipelineRun(fetcher, apiUrl, rId, headers, (stageId) => {
				const idx = PIPELINE_STAGES.findIndex((s) => stageId.startsWith(s.id));
				if (idx >= 0) setCurrentStageIdx(idx);
			});

			if (!statusData || !statusData.status) {
				setRunError("Pipeline status check failed or timed out: No response from server");
				setIsExecuting(false);
				setIsComplete(false);
				return;
			}
			const finalStatus = String(statusData.status);
			if (finalStatus !== "COMPLETED") {
				const errMsg = finalStatus === "PAUSED_HOLD" ? "Pipeline paused on Steward Hold: Action required" : finalStatus === "ABORTED" ? "Pipeline run aborted" : `Pipeline execution failed: ${finalStatus}`;
				setRunError(errMsg);
				setIsExecuting(false);
				setIsComplete(false);
				return;
			}

			let repData: Record<string, unknown> | null = null;
			if (fetcher && rId) {
				for (let repAttempts = 0; repAttempts < 20; repAttempts++) {
					try {
						const rRes = await fetcher(`${apiUrl}/runs/${encodeURIComponent(rId)}/report`, { credentials: "include", headers });
						if (rRes.ok) {
							repData = (await rRes.json()) as Record<string, unknown>;
							break;
						}
					} catch {}
					await new Promise((resolve) => setTimeout(resolve, 500));
				}
			}

			const artifacts = await fetchRunArtifacts(fetcher, apiUrl, rId, headers);
			let duckdbSql = artifacts.duckdbSql, databricksSql = artifacts.databricksSql, contractContent = artifacts.contractContent;
			const outContract = contractContent ? (parseYaml(contractContent) as Record<string, unknown>) : (repData?.contract as Record<string, unknown> | undefined);
			let outBp: any = artifacts.outputBlueprint ?? (repData?.blueprint as VaultBlueprintDTO | undefined);
			if (!contractContent && repData?.contract_content) {
				contractContent = String(repData.contract_content);
			} else if (!contractContent && outContract) {
				try {
					contractContent = serializeSpecToYaml(outContract);
				} catch {}
			}
			if (outBp && outContract && !outBp.contract) {
				outBp = { ...outBp, contract: outContract };
			}
			const fz = repData?.four_zone_sql as Record<string, unknown> | undefined;
			if (fz?.duckdb) duckdbSql = extractSqlString(fz.duckdb);
			if (fz?.databricks) databricksSql = extractSqlString(fz.databricks);

			const evolutionDiff = resolveDiffFallback(repData, outContract, outBp);

			setExecutionResult({
				run_id: rId,
				status: "COMPLETED",
				compiled_sql: duckdbSql,
				four_zone_sql: { duckdb: duckdbSql, databricks: databricksSql },
				output_contract: outContract,
				contract_content: contractContent,
				blueprint: outBp,
				evolution_diff: evolutionDiff,
				gate_scorecard: (repData?.gate_scorecard ?? (statusData?.gate_scorecard as Record<string, unknown> | undefined) ?? repData?.axi_results ?? repData?.output_validation) as Record<string, unknown> | undefined,
			});
			setCurrentStageIdx(PIPELINE_STAGES.length - 1);
			setIsComplete(true);
		} catch (err) {
			setRunError(err instanceof Error ? err.message : "Pipeline execution failed");
			setIsComplete(false);
		} finally {
			if (timerRef.current) clearInterval(timerRef.current);
			setIsExecuting(false);
		}
	};

	useEffect(() => {
		return () => { if (timerRef.current) clearInterval(timerRef.current); };
	}, []);

	return (
		<section data-testid="wizard-step-validate-run" style={S.sec}>
			<h2 style={{ fontSize: 16, fontWeight: 700, margin: "0 0 6px" }}>Step 4: Pre-Flight Action Plan, Validate & Run</h2>
			<p style={{ fontSize: 13, color: C.inkMuted, margin: "0 0 14px" }}>Verify inferred action plan and launch single-run pipeline.</p>

			<div data-testid="validation-diagnostics" style={S.box}>
				<div style={S.between}>
					<div style={{ display: "flex", alignItems: "center", gap: 8 }}>
						<span style={{ fontSize: 13, fontWeight: 700, color: C.inkPrimary }}>Pre-Flight Diagnostics</span>
						<span
							data-testid="validation-status"
							style={
								validationReport === null
									? S.chip("#0369A1", "#F0F9FF", "#BAE6FD")
									: validationReport.passed !== false
									? S.chip("#166534", "#DCFCE7", "#BBF7D0")
									: S.chip("#991B1B", "#FEE2E2", "#FECACA")
							}
						>
							{validationReport === null
								? "VALIDATION PENDING"
								: validationReport.passed !== false
								? "✓ ALL CHECKS PASSED"
								: "⚠ ISSUES DETECTED"}
						</span>
					</div>
					<button type="button" onClick={runValidation} disabled={isValidating} style={{ fontSize: 11, background: "none", border: `1px solid ${C.borderLine}`, borderRadius: 4, padding: "2px 6px", cursor: "pointer" }}>
						{isValidating ? "Validating..." : "Re-validate"}
					</button>
				</div>

				<div style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap", marginTop: 4 }}>
					{validationReport?.diagnostics && validationReport.diagnostics.length > 0 ? (
						validationReport.diagnostics.map((diag) => (
							<span key={`diag-${diag.code || diag.message}`} style={S.diag(diag.severity === "ERROR")}>
								{diag.message}
							</span>
						))
					) : (
						<span style={{ fontSize: 11, color: C.inkMuted }}>
							{validationReport ? "Pre-flight checks verified cleanly." : "Pre-flight diagnostics will appear after validation."}
						</span>
					)}
				</div>
			</div>

			<div data-testid="inferred-action-plan-card" style={{ ...S.box, backgroundColor: "#F0F9FF", border: "1px solid #BAE6FD" }}>
				<div style={S.between}>
					<div style={{ fontWeight: 700, fontSize: 13, color: "#0369A1" }}>Inferred Primary Action: {inferredAction}</div>
					<span style={S.chip("#0369A1", "#E0F2FE", "#BAE6FD")}>{candidateTables.length} Candidate Table(s)</span>
				</div>
				<div style={{ fontSize: 12, color: "#0C4A6E", marginBottom: 8 }}>
					{I_DESC[inferredAction] ?? "Multi-source expansion into Data Vault 2.0 schema."}
				</div>
				{formState.inferredPlan?.table_actions && formState.inferredPlan.table_actions.length > 0 ? (
					<div style={{ marginTop: 8, borderTop: "1px solid #BAE6FD", paddingTop: 6 }}>
						<div style={{ fontSize: 11, fontWeight: 600, color: "#0369A1", marginBottom: 4 }}>Table Actions:</div>
						<div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
							{formState.inferredPlan.table_actions.map((act) => (
								<div key={`${act.source_system}-${act.table_name}`} style={{ fontSize: 11, fontFamily: F.mono, display: "flex", justifyContent: "space-between" }}>
									<span>{act.source_system}.{act.table_name}</span>
									<span style={S.chip("#0369A1", "#FFFFFF", "#BAE6FD")}>{act.action} ➔ {(act as any).target_hub || (act as any).target_satellite || (act as any).matched_existing_entity || "vault"}</span>
								</div>
							))}
						</div>
					</div>
				) : (
					<div style={{ display: "flex", gap: 12, fontSize: 12, color: "#0369A1", borderTop: "1px solid #BAE6FD", paddingTop: 6 }}>
						<div>Expected Hubs: <strong>{expectedHubs}</strong></div>
						<div>Expected Links: <strong>{expectedLinks}</strong></div>
						<div>Expected Satellites: <strong>{expectedSats}</strong></div>
					</div>
				)}
				{formState.inferredPlan?.doc_actions && formState.inferredPlan.doc_actions.length > 0 && (
					<div style={{ marginTop: 8, borderTop: "1px solid #BAE6FD", paddingTop: 6 }}>
						<div style={{ fontSize: 11, fontWeight: 600, color: "#9333EA", marginBottom: 4 }}>Document Extraction Actions:</div>
						<div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
							{formState.inferredPlan.doc_actions.map((act) => (
								<div key={(act as any).document_path || (act as any).document_id || (act as any).doc_id || "doc"} style={{ fontSize: 11, fontFamily: F.mono, display: "flex", justifyContent: "space-between" }}>
									<span>{(act as any).document_path || (act as any).document_id || (act as any).doc_id || "doc"}</span>
									<span style={S.chip("#9333EA", "#FAF5FF", "#E9D5FF")}>{act.action} ➔ {(act as any).target_hub || (act as any).matched_existing_entity || "hub_document"}</span>
								</div>
							))}
						</div>
					</div>
				)}
				{candidateTables.length > 0 && (
					<div style={{ marginTop: 6, display: "flex", gap: 4, flexWrap: "wrap" }}>
						{candidateTables.map((t) => (
							<span key={`${t.system}-${t.name}`} style={S.chip("#0C4A6E", "#FFFFFF", "#BAE6FD")}>{t.system}.{t.name}</span>
						))}
					</div>
				)}
			</div>

			<div style={S.box}>
				<div style={{ ...S.between, marginBottom: 8 }}>
					<button type="button" data-testid="btn-run-pipeline" onClick={handleTriggerRun} disabled={isExecuting} style={S.btnRun(isExecuting)}>
						{isExecuting ? "Executing Pipeline..." : "▶ Run Pipeline"}
					</button>
					{runHandle && (
						<div style={{ display: "flex", alignItems: "center", gap: 10 }}>
							<span data-testid="run-started-badge" style={{ color: "#16A34A", fontWeight: 600 }}>✓ Run {runHandle.run_id} {isComplete ? "Completed" : "Active"}</span>
							<span data-testid="live-elapsed-timer" style={{ fontFamily: F.mono, color: C.inkMuted }}>⏱ {elapsedSeconds}s</span>
						</div>
					)}
				</div>
				<div data-testid="live-stage-stepper" style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(110px, 1fr))", gap: 4, marginTop: 8 }}>
					{PIPELINE_STAGES.map((st, idx) => {
						const isCurr = idx === currentStageIdx, done = idx < currentStageIdx || isComplete;
						return (
							<div key={st.id} data-testid={`stage-step-${st.id}`} style={S.step(isCurr, done)}>
								<div style={{ fontWeight: 600 }}>{done ? "✓ " : ""}{st.id}</div>
								<div style={{ fontSize: 10, opacity: 0.85 }}>{st.label}</div>
							</div>
						);
					})}
				</div>
			</div>

			{runError && (
				<div data-testid="run-error-banner" style={{ padding: "8px 12px", borderRadius: 4, backgroundColor: "#FEE2E2", color: "#DC2626", fontSize: 12, marginTop: 8, marginBottom: 12 }}>
					Execution Error: {runError}
				</div>
			)}
			{isComplete && (
				<DiffInspector
					runId={runHandle?.run_id ?? "run-main"}
					activeSessionId={formState.activeSessionId}
					blueprint={executionResult?.blueprint}
					contractContent={executionResult?.contract_content}
					diff={executionResult?.evolution_diff}
					gateScorecard={executionResult?.gate_scorecard}
					duckdbSql={extractSqlString(executionResult?.four_zone_sql?.duckdb)}
					databricksSql={extractSqlString(executionResult?.four_zone_sql?.databricks)}
					apiUrl={apiUrl}
					fetchImpl={fetchImpl}
					onEvolveSchema={onEvolveSchema}
					onSyncOntologyDelta={onSyncOntologyDelta}
				/>
			)}
		</section>
	);
}
