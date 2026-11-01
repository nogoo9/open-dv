/**
 * WB-00: Master Pipeline Command Center & 6-Gate Scoreboard (`WB00CommandCenter.tsx`)
 * Primary View Plugin for STAGE_00 through STAGE_07 (VIZ-PIPE-00 + VIZ-GATE-01) (`docs/06` §3.1, `docs/10` §4).
 */

import { type StoreApi, useStore } from "zustand";
import { createStore } from "zustand/vanilla";
import type { ViewPluginProps } from "../../plugins/plugin-types";
import { VERDICT_VISUAL_GRAMMAR } from "../../theme/theme-registry";
import { MultiAgentTimeline } from "./MultiAgentTimeline";
import { SixGateFunnelCard } from "./SixGateFunnelCard";

export type HarnessProviderOverrideOption =
	| ""
	| "deterministic"
	| "antigravity"
	| "claude";

export type StewardHoldPolicyOption =
	| "auto_approve_top_candidate"
	| "pause"
	| "reject_on_hold";

export interface PipelineValidationSection {
	passed?: boolean | undefined;
	valid?: boolean | undefined;
	status?: string | undefined;
	errors?: string[] | undefined;
	warnings?: string[] | undefined;
	checked_files?: string[] | undefined;
	pii_leak_check_passed?: boolean | undefined;
	cas_integrity_passed?: boolean | undefined;
	schema_validation_passed?: boolean | undefined;
	summary?: string | undefined;
}

export interface PipelineWaveReport {
	wave_id: string;
	wave_index?: number | undefined;
	description?: string | undefined;
	status?: string | undefined;
	passed?: boolean | undefined;
	sources_count?: number | undefined;
	unstructured_count?: number | undefined;
	step_reports?: PipelineStepReport[] | undefined;
}

export interface PipelineStepReport {
	step_id?: string | undefined;
	stage_id?: string | undefined;
	step_name?: string | undefined;
	agent_persona?: string | undefined;
	wave_id?: string | undefined;
	status: string;
	passed?: boolean | undefined;
	summary?: string | undefined;
}

export interface PipelineIntermediateArtifact {
	artifact_id?: string | undefined;
	name?: string | undefined;
	wave_id?: string | undefined;
	stage_id?: string | undefined;
	path?: string | undefined;
	file_path?: string | undefined;
	sha256?: string | undefined;
	sha256_digest?: string | undefined;
	size_bytes?: number | undefined;
}

export interface AgenticPipelineReport {
	run_id: string;
	pipeline_id?: string | undefined;
	project_id?: string | undefined;
	status: string;
	overall_passed: boolean;
	input_validation?: PipelineValidationSection | boolean | undefined;
	output_validation?: PipelineValidationSection | boolean | undefined;
	wave_reports?: PipelineWaveReport[] | undefined;
	step_reports?: PipelineStepReport[] | undefined;
	intermediate_artifacts?: PipelineIntermediateArtifact[] | undefined;
}

export interface PipelineRequestPayload {
	input_spec_path: string;
	harness_config_path: string;
	provider_override: HarnessProviderOverrideOption;
	steward_hold_policy: StewardHoldPolicyOption;
}

export interface PipelineLauncherStoreState {
	inputSpecPath: string;
	harnessConfigPath: string;
	providerOverride: HarnessProviderOverrideOption;
	stewardHoldPolicy: StewardHoldPolicyOption;
	validationError: string | null;
	validationResponse: PipelineValidationSection | null;
	pipelineReport: AgenticPipelineReport | null;
	isSubmitting: boolean;
	setInputSpecPath: (path: string) => void;
	setHarnessConfigPath: (path: string) => void;
	setProviderOverride: (provider: HarnessProviderOverrideOption) => void;
	setStewardHoldPolicy: (policy: StewardHoldPolicyOption) => void;
	validatePipelineInputs: (
		fetchImpl?: typeof fetch,
	) => Promise<PipelineValidationSection | null>;
	runAgenticPipeline: (
		fetchImpl?: typeof fetch,
	) => Promise<AgenticPipelineReport | null>;
	reset: () => void;
}

export const EMPTY_INPUT_PATH_VALIDATION_ERROR =
	"Deterministic Validation Error: Pipeline Input Path (input_spec_path) is required and must not be empty.";

function normalizeReportPayload(raw: unknown): AgenticPipelineReport {
	const root =
		raw && typeof raw === "object" && "report" in raw && raw.report
			? (raw.report as Record<string, unknown>)
			: ((raw ?? {}) as Record<string, unknown>);
	return {
		run_id: String(root.run_id ?? root.pipeline_id ?? "run-unknown"),
		pipeline_id:
			typeof root.pipeline_id === "string" ? root.pipeline_id : undefined,
		project_id:
			typeof root.project_id === "string" ? root.project_id : undefined,
		status: String(
			root.status ?? (root.overall_passed ? "COMPLETED" : "FAILED"),
		),
		overall_passed: Boolean(root.overall_passed ?? root.status === "PASS"),
		input_validation: root.input_validation as
			| PipelineValidationSection
			| boolean
			| undefined,
		output_validation: root.output_validation as
			| PipelineValidationSection
			| boolean
			| undefined,
		wave_reports: Array.isArray(root.wave_reports)
			? (root.wave_reports as PipelineWaveReport[])
			: [],
		step_reports: Array.isArray(root.step_reports)
			? (root.step_reports as PipelineStepReport[])
			: [],
		intermediate_artifacts: Array.isArray(root.intermediate_artifacts)
			? (root.intermediate_artifacts as PipelineIntermediateArtifact[])
			: [],
	};
}

function formatValidationStatus(
	section: PipelineValidationSection | boolean | undefined,
): string {
	if (typeof section === "boolean") {
		return section ? "PASS" : "FAIL";
	}
	if (!section || typeof section !== "object") {
		return "PASS";
	}
	if (typeof section.status === "string" && section.status.length > 0) {
		return section.status;
	}
	const flag = section.passed ?? section.valid ?? true;
	return flag ? "PASS" : "FAIL";
}

export function createPipelineLauncherStore(): StoreApi<PipelineLauncherStoreState> {
	const store = createStore<PipelineLauncherStoreState>()((set, get) => ({
		inputSpecPath: "",
		harnessConfigPath: "",
		providerOverride: "",
		stewardHoldPolicy: "auto_approve_top_candidate",
		validationError: null,
		validationResponse: null,
		pipelineReport: null,
		isSubmitting: false,

		setInputSpecPath: (path: string) => {
			set({
				inputSpecPath: path,
				validationError: null,
			});
		},

		setHarnessConfigPath: (path: string) => {
			set({ harnessConfigPath: path });
		},

		setProviderOverride: (provider: HarnessProviderOverrideOption) => {
			set({ providerOverride: provider });
		},

		setStewardHoldPolicy: (policy: StewardHoldPolicyOption) => {
			set({ stewardHoldPolicy: policy });
		},

		validatePipelineInputs: async (fetchImpl?: typeof fetch) => {
			const state = get();
			const trimmedInput = state.inputSpecPath.trim();
			if (!trimmedInput) {
				set({
					validationError: EMPTY_INPUT_PATH_VALIDATION_ERROR,
					validationResponse: null,
				});
				return null;
			}

			set({ isSubmitting: true, validationError: null });
			const payload: PipelineRequestPayload = {
				input_spec_path: trimmedInput,
				harness_config_path: state.harnessConfigPath.trim(),
				provider_override: state.providerOverride,
				steward_hold_policy: state.stewardHoldPolicy,
			};

			try {
				const fn = fetchImpl ?? globalThis.fetch;
				const response = await fn("/api/v1/pipelines/validate", {
					method: "POST",
					headers: { "Content-Type": "application/json" },
					body: JSON.stringify(payload),
				});
				const body = (await response.json()) as Record<string, unknown>;
				if (!response.ok) {
					const errDetail = String(
						body.detail ?? body.title ?? `HTTP ${response.status}`,
					);
					set({
						validationError: errDetail,
						validationResponse: null,
						isSubmitting: false,
					});
					return null;
				}
				const validationSection: PipelineValidationSection =
					body.input_validation && typeof body.input_validation === "object"
						? (body.input_validation as PipelineValidationSection)
						: (body as PipelineValidationSection);
				set({
					validationResponse: validationSection,
					validationError: null,
					isSubmitting: false,
				});
				return validationSection;
			} catch (err) {
				set({
					validationError:
						err instanceof Error
							? err.message
							: "Failed to validate pipeline inputs.",
					isSubmitting: false,
				});
				return null;
			}
		},

		runAgenticPipeline: async (fetchImpl?: typeof fetch) => {
			const state = get();
			const trimmedInput = state.inputSpecPath.trim();
			if (!trimmedInput) {
				set({
					validationError: EMPTY_INPUT_PATH_VALIDATION_ERROR,
					pipelineReport: null,
				});
				return null;
			}

			set({ isSubmitting: true, validationError: null });
			const payload: PipelineRequestPayload = {
				input_spec_path: trimmedInput,
				harness_config_path: state.harnessConfigPath.trim(),
				provider_override: state.providerOverride,
				steward_hold_policy: state.stewardHoldPolicy,
			};

			try {
				const fn = fetchImpl ?? globalThis.fetch;
				const response = await fn("/api/v1/pipelines/runs", {
					method: "POST",
					headers: { "Content-Type": "application/json" },
					body: JSON.stringify(payload),
				});
				const body = (await response.json()) as Record<string, unknown>;
				if (!response.ok) {
					const errDetail = String(
						body.detail ?? body.title ?? `HTTP ${response.status}`,
					);
					set({
						validationError: errDetail,
						isSubmitting: false,
					});
					return null;
				}
				const report = normalizeReportPayload(body);
				set({
					pipelineReport: report,
					validationError: null,
					isSubmitting: false,
				});
				return report;
			} catch (err) {
				set({
					validationError:
						err instanceof Error
							? err.message
							: "Failed to run agentic pipeline.",
					isSubmitting: false,
				});
				return null;
			}
		},

		reset: () => {
			set({
				inputSpecPath: "",
				harnessConfigPath: "",
				providerOverride: "",
				stewardHoldPolicy: "auto_approve_top_candidate",
				validationError: null,
				validationResponse: null,
				pipelineReport: null,
				isSubmitting: false,
			});
		},
	}));
	store.getInitialState = () => store.getState();
	return store;
}

export const defaultPipelineLauncherStore = createPipelineLauncherStore();

export function resetPipelineLauncherStore(): void {
	defaultPipelineLauncherStore.getState().reset();
}

function useSafeLauncherStore(
	store: StoreApi<PipelineLauncherStoreState>,
): PipelineLauncherStoreState {
	if (typeof document === "undefined") {
		return store.getState();
	}
	try {
		return useStore(store);
	} catch {
		return store.getState();
	}
}

export interface WB00CommandCenterProps extends ViewPluginProps {
	launcherStore?: StoreApi<PipelineLauncherStoreState> | undefined;
	fetchImpl?: typeof fetch | undefined;
}

export function WB00CommandCenter({
	currentEvent,
	eventsForView,
	onInspectProof,
	launcherStore = defaultPipelineLauncherStore,
	fetchImpl,
}: WB00CommandCenterProps) {
	const payload = currentEvent?.layer2_visual_payload;
	const summaryMetrics = payload?.summary_metrics ?? {};
	const pills = payload?.semantic_hash_pills ?? [];
	const verdict = currentEvent?.status ?? "PASS";
	const verdictMeta =
		VERDICT_VISUAL_GRAMMAR[verdict] ?? VERDICT_VISUAL_GRAMMAR.PASS;
	const launcher = useSafeLauncherStore(launcherStore);
	const report = launcher.pipelineReport;

	return (
		<div
			data-testid="wb00-command-center"
			style={{
				display: "flex",
				flexDirection: "column",
				gap: "1rem",
				color: "var(--dv-text-primary)",
			}}
		>
			{/* Top Overview Bar */}
			<section
				style={{
					backgroundColor: "var(--dv-surface-1)",
					border: "1px solid var(--dv-border-subtle)",
					borderRadius: "0.5rem",
					padding: "1rem",
					display: "flex",
					justifyContent: "space-between",
					alignItems: "center",
					flexWrap: "wrap",
					gap: "0.75rem",
				}}
			>
				<div>
					<div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
						<span
							style={{
								fontWeight: 700,
								fontSize: "0.8125rem",
								color: `var(${verdictMeta.cssColorVar})`,
							}}
						>
							{verdictMeta.shapeSymbol} {verdictMeta.prefixBadge}
						</span>
						<h2 style={{ margin: 0, fontSize: "1rem", fontWeight: 700 }}>
							WB-00: Master Pipeline Command Center &amp; 6-Gate Scoreboard
						</h2>
					</div>
					<p
						style={{
							margin: "0.25rem 0 0",
							fontSize: "0.75rem",
							color: "var(--dv-text-secondary)",
						}}
					>
						VIZ-PIPE-00 Dual-Loop State Machine Orchestration &amp; VIZ-GATE-01
						Master Invariant Verification
					</p>
				</div>

				<button
					type="button"
					onClick={onInspectProof}
					data-testid="btn-inspect-proof"
					style={{
						backgroundColor: "var(--dv-surface-2)",
						color: "var(--dv-text-primary)",
						border: "1px solid var(--dv-border-strong)",
						borderRadius: "0.375rem",
						padding: "0.4rem 0.75rem",
						fontSize: "0.75rem",
						fontWeight: 600,
						cursor: "pointer",
					}}
				>
					Inspect Layer 3 Formal Proof ↗
				</button>
			</section>

			{/* Interactive Agentic Pipeline Launcher & Report Inspector */}
			<section
				data-testid="wb00-pipeline-launcher"
				aria-label="Trigger Agentic Pipeline (VaultFirstMate + No-Mistakes)"
				style={{
					backgroundColor: "var(--dv-surface-1)",
					border: "1px solid var(--dv-border-subtle)",
					borderRadius: "0.5rem",
					padding: "1rem",
					display: "flex",
					flexDirection: "column",
					gap: "0.75rem",
				}}
			>
				<div>
					<h3 style={{ margin: 0, fontSize: "0.875rem", fontWeight: 700 }}>
						Trigger Agentic Pipeline (VaultFirstMate + No-Mistakes)
					</h3>
					<p
						style={{
							margin: "0.2rem 0 0",
							fontSize: "0.75rem",
							color: "var(--dv-text-secondary)",
						}}
					>
						Execute deterministic pre-flight input validation, multi-wave
						STAGE_00..STAGE_07 agentic orchestration, and post-flight output
						verification.
					</p>
				</div>

				<div
					style={{
						display: "grid",
						gridTemplateColumns: "repeat(auto-fit, minmax(220px, 1fr))",
						gap: "0.75rem",
					}}
				>
					<label
						style={{
							display: "flex",
							flexDirection: "column",
							gap: "0.25rem",
							fontSize: "0.75rem",
							fontWeight: 600,
						}}
					>
						<span>Pipeline Input Path (input_spec_path)</span>
						<input
							type="text"
							data-testid="input-pipeline-spec-path"
							placeholder="Enter path to pipeline_input.yaml..."
							value={launcher.inputSpecPath}
							onChange={(e) => launcher.setInputSpecPath(e.target.value)}
							style={{
								backgroundColor: "var(--dv-surface-2)",
								color: "var(--dv-text-primary)",
								border: "1px solid var(--dv-border-strong)",
								borderRadius: "0.375rem",
								padding: "0.4rem 0.6rem",
								fontSize: "0.75rem",
								fontFamily: "ui-monospace, monospace",
							}}
						/>
					</label>

					<label
						style={{
							display: "flex",
							flexDirection: "column",
							gap: "0.25rem",
							fontSize: "0.75rem",
							fontWeight: 600,
						}}
					>
						<span>Harness Config Path (harness_config_path)</span>
						<input
							type="text"
							data-testid="input-harness-config-path"
							placeholder="Optional path to harness_config.yaml..."
							value={launcher.harnessConfigPath}
							onChange={(e) => launcher.setHarnessConfigPath(e.target.value)}
							style={{
								backgroundColor: "var(--dv-surface-2)",
								color: "var(--dv-text-primary)",
								border: "1px solid var(--dv-border-strong)",
								borderRadius: "0.375rem",
								padding: "0.4rem 0.6rem",
								fontSize: "0.75rem",
								fontFamily: "ui-monospace, monospace",
							}}
						/>
					</label>

					<label
						style={{
							display: "flex",
							flexDirection: "column",
							gap: "0.25rem",
							fontSize: "0.75rem",
							fontWeight: 600,
						}}
					>
						<span>Harness Provider Override</span>
						<select
							data-testid="select-harness-provider"
							value={launcher.providerOverride}
							onChange={(e) =>
								launcher.setProviderOverride(
									e.target.value as HarnessProviderOverrideOption,
								)
							}
							style={{
								backgroundColor: "var(--dv-surface-2)",
								color: "var(--dv-text-primary)",
								border: "1px solid var(--dv-border-strong)",
								borderRadius: "0.375rem",
								padding: "0.4rem 0.6rem",
								fontSize: "0.75rem",
							}}
						>
							<option value="">Use Config File</option>
							<option value="deterministic">deterministic</option>
							<option value="antigravity">antigravity</option>
							<option value="claude">claude</option>
						</select>
					</label>

					<label
						style={{
							display: "flex",
							flexDirection: "column",
							gap: "0.25rem",
							fontSize: "0.75rem",
							fontWeight: 600,
						}}
					>
						<span>Steward Hold Policy</span>
						<select
							data-testid="select-hold-policy"
							value={launcher.stewardHoldPolicy}
							onChange={(e) =>
								launcher.setStewardHoldPolicy(
									e.target.value as StewardHoldPolicyOption,
								)
							}
							style={{
								backgroundColor: "var(--dv-surface-2)",
								color: "var(--dv-text-primary)",
								border: "1px solid var(--dv-border-strong)",
								borderRadius: "0.375rem",
								padding: "0.4rem 0.6rem",
								fontSize: "0.75rem",
							}}
						>
							<option value="auto_approve_top_candidate">
								auto_approve_top_candidate
							</option>
							<option value="pause">pause</option>
							<option value="reject_on_hold">reject_on_hold</option>
						</select>
					</label>
				</div>

				<div style={{ display: "flex", gap: "0.5rem", flexWrap: "wrap" }}>
					<button
						type="button"
						data-testid="btn-validate-pipeline"
						disabled={launcher.isSubmitting}
						onClick={() => {
							void launcher.validatePipelineInputs(fetchImpl);
						}}
						style={{
							backgroundColor: "var(--dv-surface-2)",
							color: "var(--dv-text-primary)",
							border: "1px solid var(--dv-border-strong)",
							borderRadius: "0.375rem",
							padding: "0.4rem 0.75rem",
							fontSize: "0.75rem",
							fontWeight: 600,
							cursor: launcher.isSubmitting ? "not-allowed" : "pointer",
						}}
					>
						Validate Inputs
					</button>

					<button
						type="button"
						data-testid="btn-run-pipeline"
						disabled={launcher.isSubmitting}
						onClick={() => {
							void launcher.runAgenticPipeline(fetchImpl);
						}}
						style={{
							backgroundColor: "var(--dv-hub-accent)",
							color: "#000",
							border: "none",
							borderRadius: "0.375rem",
							padding: "0.4rem 0.85rem",
							fontSize: "0.75rem",
							fontWeight: 700,
							cursor: launcher.isSubmitting ? "not-allowed" : "pointer",
						}}
					>
						Run Agentic Pipeline
					</button>
				</div>

				{launcher.validationError && (
					<div
						role="alert"
						data-testid="wb00-pipeline-validation-error"
						style={{
							backgroundColor: "var(--dv-surface-2)",
							border: "1px solid var(--dv-gate-reject)",
							color: "var(--dv-gate-reject)",
							borderRadius: "0.375rem",
							padding: "0.5rem 0.75rem",
							fontSize: "0.75rem",
							fontWeight: 600,
						}}
					>
						{launcher.validationError}
					</div>
				)}

				{launcher.validationResponse && !report && (
					<div
						data-testid="wb00-pipeline-validation-result"
						style={{
							backgroundColor: "var(--dv-surface-2)",
							border: "1px solid var(--dv-gate-pass)",
							borderRadius: "0.375rem",
							padding: "0.5rem 0.75rem",
							fontSize: "0.75rem",
						}}
					>
						<strong>Pre-Flight Input Validation: </strong>
						<span>{formatValidationStatus(launcher.validationResponse)}</span>
					</div>
				)}

				{report && (
					<div
						data-testid="wb00-pipeline-report-panel"
						style={{
							backgroundColor: "var(--dv-surface-2)",
							border: "1px solid var(--dv-border-strong)",
							borderRadius: "0.375rem",
							padding: "0.75rem",
							display: "flex",
							flexDirection: "column",
							gap: "0.75rem",
							fontSize: "0.75rem",
						}}
					>
						<div
							style={{
								display: "flex",
								flexWrap: "wrap",
								justifyContent: "space-between",
								gap: "0.5rem",
								borderBottom: "1px solid var(--dv-border-subtle)",
								paddingBottom: "0.5rem",
							}}
						>
							<div>
								<strong>Run ID: </strong>
								<code data-testid="report-run-id">{report.run_id}</code>
							</div>
							<div>
								<strong>Status: </strong>
								<code data-testid="report-status">{report.status}</code>
							</div>
							<div>
								<strong>Overall Passed: </strong>
								<code data-testid="report-overall-passed">
									{String(report.overall_passed)}
								</code>
							</div>
							<div>
								<strong>Input Validation: </strong>
								<code data-testid="report-input-validation">
									{formatValidationStatus(report.input_validation)}
								</code>
							</div>
							<div>
								<strong>Output Validation: </strong>
								<code data-testid="report-output-validation">
									{formatValidationStatus(report.output_validation)}
								</code>
							</div>
						</div>

						{report.wave_reports && report.wave_reports.length > 0 && (
							<div data-testid="report-waves-section">
								<div style={{ fontWeight: 700, marginBottom: "0.35rem" }}>
									Wave Reports ({report.wave_reports.length})
								</div>
								<div
									style={{
										display: "grid",
										gridTemplateColumns: "repeat(auto-fit, minmax(180px, 1fr))",
										gap: "0.4rem",
									}}
								>
									{report.wave_reports.map((wave) => (
										<div
											key={wave.wave_id}
											data-testid={`report-wave-${wave.wave_id}`}
											style={{
												backgroundColor: "var(--dv-surface-1)",
												border: "1px solid var(--dv-border-subtle)",
												borderRadius: "0.25rem",
												padding: "0.4rem 0.6rem",
											}}
										>
											<div style={{ fontWeight: 700 }}>
												<code>{wave.wave_id}</code>
											</div>
											{wave.description && (
												<div style={{ color: "var(--dv-text-secondary)" }}>
													{wave.description}
												</div>
											)}
											<div>
												Status:{" "}
												<code>
													{wave.status ?? (wave.passed ? "PASS" : "DONE")}
												</code>
											</div>
										</div>
									))}
								</div>
							</div>
						)}

						{report.step_reports && report.step_reports.length > 0 && (
							<div data-testid="report-steps-section">
								<div style={{ fontWeight: 700, marginBottom: "0.35rem" }}>
									Pipeline Step Reports ({report.step_reports.length})
								</div>
								<div
									style={{
										display: "flex",
										flexDirection: "column",
										gap: "0.25rem",
									}}
								>
									{report.step_reports.map((step) => {
										const stepKey =
											step.step_id ?? step.stage_id ?? step.step_name ?? "step";
										return (
											<div
												key={`${step.wave_id ?? "w"}-${stepKey}`}
												data-testid={`report-step-${stepKey}`}
												style={{
													display: "flex",
													justifyContent: "space-between",
													backgroundColor: "var(--dv-surface-1)",
													border: "1px solid var(--dv-border-subtle)",
													borderRadius: "0.25rem",
													padding: "0.3rem 0.6rem",
												}}
											>
												<span>
													<code>{stepKey}</code>
													{step.step_name ? ` — ${step.step_name}` : ""}
													{step.agent_persona ? ` (${step.agent_persona})` : ""}
												</span>
												<code>{step.status}</code>
											</div>
										);
									})}
								</div>
							</div>
						)}

						{report.intermediate_artifacts &&
							report.intermediate_artifacts.length > 0 && (
								<div data-testid="report-artifacts-section">
									<div style={{ fontWeight: 700, marginBottom: "0.35rem" }}>
										Intermediate Artifacts &amp; SHA-256 Digests (
										{report.intermediate_artifacts.length})
									</div>
									<div
										style={{
											display: "flex",
											flexDirection: "column",
											gap: "0.25rem",
										}}
									>
										{report.intermediate_artifacts.map((art) => {
											const label =
												art.name ??
												art.artifact_id ??
												art.path ??
												art.file_path ??
												"artifact";
											const digest = art.sha256 ?? art.sha256_digest ?? "";
											const filePath = art.path ?? art.file_path ?? "";
											return (
												<div
													key={`${art.wave_id ?? "w"}-${label}-${digest}-${filePath}`}
													data-testid={`report-artifact-${label}`}
													style={{
														display: "flex",
														flexWrap: "wrap",
														justifyContent: "space-between",
														gap: "0.5rem",
														backgroundColor: "var(--dv-surface-1)",
														border: "1px solid var(--dv-border-subtle)",
														borderRadius: "0.25rem",
														padding: "0.3rem 0.6rem",
														fontFamily: "ui-monospace, monospace",
													}}
												>
													<span>
														<strong>{label}</strong>
														{filePath && filePath !== label
															? ` (${filePath})`
															: ""}
													</span>
													<code data-testid="report-artifact-sha256">
														{digest}
													</code>
												</div>
											);
										})}
									</div>
								</div>
							)}
					</div>
				)}
			</section>

			{/* Executive Summary Metrics Grid */}
			<section
				data-testid="command-center-metrics"
				style={{
					display: "grid",
					gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))",
					gap: "0.5rem",
				}}
			>
				{[
					{
						label: "Sources Onboarded",
						value: summaryMetrics.sources_onboarded ?? 5,
					},
					{
						label: "Bronze Columns Audited",
						value: `${summaryMetrics.bronze_columns_audited ?? 67} (100%)`,
					},
					{
						label: "Hubs Synthesized",
						value: summaryMetrics.hubs_count ?? 4,
					},
					{
						label: "Links Synthesized",
						value: summaryMetrics.links_count ?? 5,
					},
					{
						label: "Satellites Synthesized",
						value: summaryMetrics.satellites_count ?? 11,
					},
					{
						label: "Checks Passed",
						value: `${summaryMetrics.checks_passed ?? 99} / 99`,
					},
					{
						label: "Self-Healed Exceptions",
						value: summaryMetrics.self_healed_exceptions ?? 2,
					},
					{
						label: "Captain Holds Active",
						value: summaryMetrics.captain_holds_active ?? 1,
					},
				].map((metric) => (
					<div
						key={metric.label}
						style={{
							backgroundColor: "var(--dv-surface-1)",
							border: "1px solid var(--dv-border-subtle)",
							borderRadius: "0.375rem",
							padding: "0.6rem 0.75rem",
						}}
					>
						<div
							style={{
								fontSize: "0.6875rem",
								color: "var(--dv-text-secondary)",
								lineHeight: 1.2,
							}}
						>
							{metric.label}
						</div>
						<div
							style={{
								fontSize: "1.125rem",
								fontWeight: 700,
								marginTop: "0.25rem",
								color: "var(--dv-text-primary)",
							}}
						>
							{String(metric.value)}
						</div>
					</div>
				))}
			</section>

			{/* Semantic Hash Pills Bar */}
			{pills.length > 0 && (
				<section
					style={{
						backgroundColor: "var(--dv-surface-1)",
						border: "1px solid var(--dv-border-subtle)",
						borderRadius: "0.5rem",
						padding: "0.75rem 1rem",
					}}
				>
					<div
						style={{
							fontSize: "0.75rem",
							fontWeight: 600,
							color: "var(--dv-text-secondary)",
							marginBottom: "0.5rem",
						}}
					>
						Active Semantic Hash Pills (Zero 64-Hex String Fatigue):
					</div>
					<div
						style={{
							display: "flex",
							flexWrap: "wrap",
							gap: "0.5rem",
						}}
					>
						{pills.map((pill) => (
							<button
								key={`${pill.full_sha256_hex}-${pill.business_key}`}
								type="button"
								onClick={onInspectProof}
								title={`Full SHA-256: ${pill.full_sha256_hex}`}
								data-testid="semantic-hash-pill"
								style={{
									backgroundColor: "var(--dv-surface-2)",
									color: "var(--dv-text-primary)",
									border: "1px solid var(--dv-hub-accent)",
									borderRadius: "9999px",
									padding: "0.2rem 0.6rem",
									fontFamily: "ui-monospace, monospace",
									fontSize: "0.75rem",
									cursor: "pointer",
								}}
							>
								{pill.pill_label}
							</button>
						))}
					</div>
				</section>
			)}

			{/* Multi-Agent Timeline */}
			<MultiAgentTimeline currentEvent={currentEvent} events={eventsForView} />

			{/* 6-Gate Verification Funnel */}
			<SixGateFunnelCard event={currentEvent} />
		</div>
	);
}
