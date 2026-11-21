/**
 * Step 6: Data Vault 2.0 Pipeline Build & Run (`D12`, `D14`, `D21`, `D27`, `FR-DVM-16`)
 * (apps/opendv-web-workbench/src/ontology/wizard/Step6PipelineBuildAndRun.tsx)
 */

import type React from "react";
import { StewardHoldModal } from "../../explorer/StewardHoldModal";
import { CLAUDE_TOKENS, EXPLORER_FONTS } from "../../explorer/design-tokens";
import { safeUseState } from "../../explorer/modals/safeHooks";
import type {
	PipelineBaselineOption,
	PipelineHoldSummary,
	PipelineTopologyRow,
	ZoneSqlByDialect,
} from "../store/store-types";
import type { BudgetHoldInfo } from "../store/types";
import { BudgetHoldCard } from "./BudgetHoldCard";
import { Step6GateBadges } from "./Step6GateBadges";
import { Step6SqlPreviewCard } from "./Step6SqlPreviewCard";
import { aggregateStagePillStatus } from "./wizard-step6-adapter";
import { PIPELINE_STAGE_PILLS } from "./wizard-types";

export { PIPELINE_STAGE_PILLS };

export interface Step6PipelineBuildAndRunProps {
	sessionId?: string | null | undefined;
	committedVersion?: string | undefined;
	committedSha256?: string | undefined;
	selectedDialects?: Array<"duckdb" | "databricks"> | undefined;
	baselineOptions?: PipelineBaselineOption[] | undefined;
	selectedBaselineRunId?: string | undefined;
	topologyRows?: PipelineTopologyRow[] | undefined;
	stageStatuses?: Record<string, string> | undefined;
	zoneSqlByZone?: Record<string, string> | undefined;
	zoneSqlByDialect?: ZoneSqlByDialect | undefined;
	gateScorecard?: Record<string, unknown> | null | undefined;
	sessionRuns?: Array<Record<string, unknown>> | undefined;
	activeRunId?: string | null | undefined;
	activeSessionJobId?: string | null | undefined;
	isRunningPipeline?: boolean | undefined;
	activeHold?: PipelineHoldSummary | null | undefined;
	budgetHold?: BudgetHoldInfo | null | undefined;
	driftColumns?: string[] | undefined;
	onToggleDialect?: ((dialect: "duckdb" | "databricks") => void) | undefined;
	onSelectBaseline?: ((runId: string) => void) | undefined;
	onSelectViewedRun?: ((runId: string) => Promise<void> | void) | undefined;
	onUploadBaselineContract?:
		| ((yamlContent: string, fileName: string) => void)
		| undefined;
	onStartPipelineRun?: (() => Promise<void> | void) | undefined;
	onCancelJob?: (() => Promise<void> | void) | undefined;
	onResolveHold?:
		| ((params: {
				holdId: string;
				action: "APPROVE" | "REJECT" | "ANSWER";
				selectedCandidateId?: string;
				resolutionNotes?: string;
		  }) => Promise<boolean>)
		| undefined;
	onCloseHold?: (() => void) | undefined;
	onRaiseBudgetAndResume?: (() => Promise<void>) | undefined;
	onAbortBudgetHold?: (() => void) | undefined;
	onSyncDriftToStep4?: (() => void) | undefined;
	onExploreDvSchema?:
		| ((params?: {
				runId?: string | null | undefined;
				compareRunId?: string | null | undefined;
				sessionId?: string | null | undefined;
		  }) => void)
		| undefined;
	onOpenLogs?:
		| ((opts?: { stageId?: string; wizardStep?: number }) => void)
		| undefined;
}

export const Step6PipelineBuildAndRun: React.FC<
	Step6PipelineBuildAndRunProps
> = ({
	sessionId,
	committedVersion = "v1.0.0",
	committedSha256 = "",
	selectedDialects = ["duckdb"],
	baselineOptions = [
		{
			run_id: "__cold_start__",
			label: "Fresh Cold-Start Vault",
			is_latest_certified: false,
		},
	],
	selectedBaselineRunId = "__cold_start__",
	topologyRows = [],
	stageStatuses = {},
	zoneSqlByZone = {},
	zoneSqlByDialect,
	gateScorecard,
	sessionRuns = [],
	activeRunId,
	activeSessionJobId,
	isRunningPipeline = false,
	activeHold,
	budgetHold,
	driftColumns = [],
	onToggleDialect,
	onSelectBaseline,
	onSelectViewedRun,
	onUploadBaselineContract,
	onStartPipelineRun,
	onCancelJob,
	onResolveHold,
	onCloseHold,
	onRaiseBudgetAndResume,
	onAbortBudgetHold,
	onSyncDriftToStep4,
	onExploreDvSchema,
	onOpenLogs,
}) => {
	const [uploadedContractName, setUploadedContractName] = safeUseState<
		string | null
	>(null);
	const isJobLocked = Boolean(activeSessionJobId || isRunningPipeline);

	const handleBaselineFileUpload = async (
		e: React.ChangeEvent<HTMLInputElement>,
	) => {
		const file = e.target.files?.[0];
		if (!file || !onUploadBaselineContract) return;
		const text = await file.text();
		if (text.trim()) {
			setUploadedContractName(file.name);
			onUploadBaselineContract(text, file.name);
		}
	};

	return (
		<section
			data-testid="wizard-step-6"
			style={{ display: "flex", flexDirection: "column", gap: "18px" }}
		>
			<header
				style={{
					display: "flex",
					justifyContent: "space-between",
					alignItems: "flex-start",
					gap: "12px",
					flexWrap: "wrap",
				}}
			>
				<div>
					<div
						style={{
							display: "flex",
							alignItems: "center",
							gap: "8px",
							marginBottom: "4px",
						}}
					>
						<span
							className="claude-pill"
							style={{
								background: "#e4f4ea",
								color: CLAUDE_TOKENS.colors.success,
								fontWeight: 600,
							}}
						>
							Committed {committedVersion}
						</span>
						{committedSha256 && (
							<code
								style={{
									fontFamily: EXPLORER_FONTS.mono,
									fontSize: "11.5px",
									color: CLAUDE_TOKENS.colors.muted,
								}}
							>
								{committedSha256.slice(0, 12)}
							</code>
						)}
						{sessionId && (
							<span
								style={{ fontSize: "12px", color: CLAUDE_TOKENS.colors.muted }}
							>
								{sessionId}
							</span>
						)}
					</div>
					<h1 className="claude-h1" style={{ margin: "0 0 6px" }}>
						Step 6 · 5-Zone Data Vault 2.0 Build &amp; Run
					</h1>
				</div>
				<button
					type="button"
					className="claude-btn-secondary"
					data-testid="btn-step6-view-logs"
					onClick={() => onOpenLogs?.({ wizardStep: 6 })}
				>
					View Stage Logs ↗
				</button>
			</header>

			<div
				className="claude-card-cream claude-responsive-grid"
				style={{
					padding: "14px 16px",
					display: "grid",
					gridTemplateColumns: "1fr 1fr auto",
					gap: "12px",
					alignItems: "end",
				}}
			>
				<div style={{ minWidth: 0 }}>
					<div
						style={{ fontSize: "12px", fontWeight: 600, marginBottom: "6px" }}
					>
						Target Dialects (5-Zone SQL)
					</div>
					<div style={{ display: "flex", gap: "10px", flexWrap: "wrap" }}>
						<label
							style={{
								fontSize: "13px",
								display: "flex",
								alignItems: "center",
								gap: "6px",
							}}
						>
							<input
								type="checkbox"
								checked={selectedDialects.includes("duckdb")}
								onChange={() => onToggleDialect?.("duckdb")}
							/>
							DuckDB (5-Zone Local Sandbox)
						</label>
						<label
							style={{
								fontSize: "13px",
								display: "flex",
								alignItems: "center",
								gap: "6px",
							}}
						>
							<input
								type="checkbox"
								checked={selectedDialects.includes("databricks")}
								onChange={() => onToggleDialect?.("databricks")}
							/>
							Databricks Delta
						</label>
					</div>
				</div>

				<div
					style={{
						minWidth: 0,
						display: "flex",
						flexDirection: "column",
						gap: "6px",
					}}
				>
					<label
						style={{
							display: "flex",
							flexDirection: "column",
							gap: "4px",
							fontSize: "12px",
							fontWeight: 600,
						}}
					>
						Deployed Contract Baseline (D12)
						<select
							data-testid="select-deployed-contract-baseline"
							value={selectedBaselineRunId}
							onChange={(e) => {
								setUploadedContractName(null);
								onSelectBaseline?.(e.target.value);
							}}
							style={{
								padding: "7px 10px",
								borderRadius: "6px",
								border: `1px solid ${CLAUDE_TOKENS.colors.hairlineStrong}`,
								background: CLAUDE_TOKENS.colors.canvas,
								color: CLAUDE_TOKENS.colors.ink,
							}}
						>
							{baselineOptions.map((opt) => (
								<option key={opt.run_id} value={opt.run_id}>
									{opt.label}
								</option>
							))}
							{uploadedContractName && (
								<option value="__uploaded__">
									Uploaded: {uploadedContractName}
								</option>
							)}
						</select>
					</label>
					<input
						type="file"
						accept=".yaml,.yml"
						aria-label="Upload baseline deployed_contract.yaml"
						data-testid="input-upload-baseline-contract"
						onChange={handleBaselineFileUpload}
						style={{ fontSize: "11.5px" }}
					/>
				</div>

				<div style={{ minWidth: 0, display: "flex", gap: "8px" }}>
					<button
						type="button"
						className="claude-btn-primary"
						data-testid="btn-start-pipeline-run"
						disabled={isJobLocked}
						onClick={() => onStartPipelineRun?.()}
					>
						{isJobLocked
							? "Run in progress — view live logs"
							: "Run 5-Zone Vault Pipeline"}
					</button>
					{isJobLocked && (
						<button
							type="button"
							className="claude-btn-secondary"
							data-testid="btn-cancel-active-job"
							onClick={() => onCancelJob?.()}
						>
							Cancel
						</button>
					)}
				</div>
			</div>

			<BudgetHoldCard
				budgetHold={budgetHold}
				onRaiseBudgetAndResume={onRaiseBudgetAndResume}
				onAbortBudgetHold={onAbortBudgetHold}
			/>

			{driftColumns.length > 0 && (
				<div
					className="claude-card-cream"
					style={{
						padding: "12px 16px",
						display: "flex",
						justifyContent: "space-between",
						alignItems: "center",
						gap: "10px",
					}}
				>
					<span style={{ fontSize: "12.5px" }}>
						Unmodeled source drift detected:{" "}
						<code>{driftColumns.join(", ")}</code>
					</span>
					<button
						type="button"
						className="claude-btn-secondary"
						data-testid="btn-sync-drift-to-step4"
						onClick={() => onSyncDriftToStep4?.()}
					>
						↺ Sync Drift to Step 4
					</button>
				</div>
			)}

			<div
				data-testid="step6-pipeline-console"
				className="claude-card-dark"
				style={{
					backgroundColor: CLAUDE_TOKENS.colors.surfaceDark,
					color: "#faf9f5",
					padding: "16px",
					borderRadius: "10px",
					display: "flex",
					flexDirection: "column",
					gap: "12px",
				}}
			>
				<div
					style={{
						display: "flex",
						justifyContent: "space-between",
						alignItems: "center",
						flexWrap: "wrap",
						gap: "8px",
					}}
				>
					<strong style={{ fontSize: "13px" }}>
						Live Pipeline Execution Console{" "}
						{activeRunId ? `· ${activeRunId}` : ""}
					</strong>
					<div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
						{sessionRuns.length > 1 && onSelectViewedRun && (
							<select
								aria-label="Select session run to inspect"
								data-testid="select-step6-viewed-run"
								value={activeRunId ?? ""}
								disabled={isJobLocked}
								onChange={(e) => void onSelectViewedRun(e.target.value)}
								style={{
									backgroundColor: CLAUDE_TOKENS.colors.surfaceDarkElevated,
									color: CLAUDE_TOKENS.colors.onDark,
									border: "1px solid #33312c",
									borderRadius: "6px",
									padding: "3px 8px",
									fontFamily: EXPLORER_FONTS.mono,
									fontSize: "11px",
								}}
							>
								{sessionRuns.map((r) => {
									const rid = String(r.run_id ?? "");
									const rst = String(r.status ?? "");
									return (
										<option key={rid} value={rid}>
											{rid} ({rst})
										</option>
									);
								})}
							</select>
						)}
						<span
							style={{
								fontSize: "11.5px",
								color: CLAUDE_TOKENS.colors.onDarkSoft,
							}}
						>
							Click any stage pill to filter Session Logs
						</span>
					</div>
				</div>
				<Step6GateBadges gateScorecard={gateScorecard} />
				<div
					className="claude-responsive-grid"
					style={{
						display: "grid",
						gridTemplateColumns: "repeat(7, minmax(0, 1fr))",
						gap: "8px",
					}}
				>
					{PIPELINE_STAGE_PILLS.map((p) => {
						const st = aggregateStagePillStatus(
							p.internalStages,
							stageStatuses,
							p.pill,
						);
						return (
							<button
								key={p.pill}
								type="button"
								data-testid={`console-stage-pill-${p.pill}`}
								onClick={() =>
									onOpenLogs?.({
										stageId: p.pill,
										wizardStep: 6,
									})
								}
								style={{
									minWidth: 0,
									padding: "8px",
									borderRadius: "8px",
									border: "1px solid #33312c",
									background: "#252320",
									color: "#faf9f5",
									textAlign: "left",
									cursor: "pointer",
								}}
							>
								<div
									style={{
										fontFamily: EXPLORER_FONTS.mono,
										fontSize: "11px",
										color: "#5db8a6",
									}}
								>
									{p.pill} · {st}
								</div>
								<div style={{ fontSize: "11.5px", fontWeight: 500 }}>
									{p.title}
								</div>
							</button>
						);
					})}
				</div>
			</div>

			{topologyRows.length > 0 && (
				<div className="claude-card" style={{ padding: "14px 16px" }}>
					<h2 className="claude-h2" style={{ margin: "0 0 8px" }}>
						Derived Data Vault 2.0 Topology ({topologyRows.length})
					</h2>
					<div
						style={{
							overflowX: "auto",
							maxHeight: "190px",
							overflowY: "auto",
							minWidth: 0,
						}}
					>
						<table
							style={{
								width: "100%",
								borderCollapse: "collapse",
								fontSize: "12.5px",
							}}
						>
							<thead>
								<tr
									style={{
										textAlign: "left",
										borderBottom: `1px solid ${CLAUDE_TOKENS.colors.hairline}`,
									}}
								>
									<th style={{ padding: "5px 8px" }}>Entity</th>
									<th style={{ padding: "5px 8px" }}>Kind</th>
									<th style={{ padding: "5px 8px" }}>Zone</th>
									<th style={{ padding: "5px 8px" }}>Source Mapping</th>
								</tr>
							</thead>
							<tbody>
								{topologyRows.map((r) => (
									<tr
										key={r.entity_name}
										style={{
											borderBottom: `1px solid ${CLAUDE_TOKENS.colors.hairline}`,
										}}
									>
										<td
											style={{
												padding: "5px 8px",
												fontFamily: EXPLORER_FONTS.mono,
											}}
										>
											{r.entity_name}
										</td>
										<td style={{ padding: "5px 8px" }}>{r.entity_kind}</td>
										<td style={{ padding: "5px 8px" }}>{r.zone}</td>
										<td
											style={{
												padding: "5px 8px",
												fontFamily: EXPLORER_FONTS.mono,
											}}
										>
											{r.source_mapping}
										</td>
									</tr>
								))}
							</tbody>
						</table>
					</div>
				</div>
			)}

			<Step6SqlPreviewCard
				activeRunId={activeRunId}
				selectedDialects={selectedDialects}
				zoneSqlByZone={zoneSqlByZone}
				zoneSqlByDialect={zoneSqlByDialect}
				isRunningPipeline={isRunningPipeline}
			/>

			{activeHold && (
				<StewardHoldModal
					isOpen={true}
					onClose={() => onCloseHold?.()}
					holdId={activeHold.hold_id}
					runId={activeHold.run_id}
					stageKey={activeHold.stage_id}
					headline={activeHold.headline}
					onResolveHold={(params) => {
						void onResolveHold?.({
							holdId: params.holdId,
							action: params.action,
							...(params.selectedCandidateId
								? { selectedCandidateId: params.selectedCandidateId }
								: {}),
							...(params.resolutionNotes
								? { resolutionNotes: params.resolutionNotes }
								: {}),
						});
					}}
				/>
			)}

			<div style={{ display: "flex", justifyContent: "flex-end" }}>
				<button
					type="button"
					className="claude-btn-primary"
					data-testid="btn-explore-dv-schema"
					onClick={() => {
						const compareId = selectedBaselineRunId?.trim();
						const hasValidCompare =
							Boolean(compareId) &&
							!compareId.startsWith("__") &&
							compareId !== "cold_start" &&
							compareId !== activeRunId;
						onExploreDvSchema?.({
							...(activeRunId ? { runId: activeRunId } : {}),
							...(hasValidCompare ? { compareRunId: compareId } : {}),
							...(sessionId ? { sessionId } : {}),
						});
					}}
				>
					Explore Generated DV 2.0 Schema &amp; Lineage ➔
				</button>
			</div>
		</section>
	);
};
