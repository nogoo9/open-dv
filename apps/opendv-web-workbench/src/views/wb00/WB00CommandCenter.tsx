/**
 * WB-00: Master Pipeline Command Center & 6-Gate Scoreboard (`WB00CommandCenter.tsx`)
 * Primary View Plugin for STAGE_00 through STAGE_07 (VIZ-PIPE-00 + VIZ-GATE-01) (`docs/06` §3.1, `docs/10` §4),
 * including the interactive Agentic Pipeline Launcher & Report Inspector (`createPipelineLauncherStore`).
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

function isPassingStatusToken(status: string | undefined): boolean {
	if (typeof status !== "string" || status.trim().length === 0) {
		return false;
	}
	const upper = status.trim().toUpperCase();
	return (
		upper === "PASS" ||
		upper === "PASSED" ||
		upper === "VALID" ||
		upper === "OK" ||
		upper === "COMPLETED"
	);
}

function isFailingStatusToken(status: string | undefined): boolean {
	if (typeof status !== "string" || status.trim().length === 0) {
		return false;
	}
	const upper = status.trim().toUpperCase();
	return (
		upper === "FAIL" ||
		upper === "FAILED" ||
		upper === "ERROR" ||
		upper === "INVALID" ||
		upper === "REJECT" ||
		upper === "REJECT_WITH_TRACE"
	);
}

function isValidationSectionPassed(
	section: PipelineValidationSection | boolean | undefined,
): boolean {
	if (typeof section === "boolean") {
		return section;
	}
	if (!section || typeof section !== "object") {
		return false;
	}
	if (section.passed === false || section.valid === false) {
		return false;
	}
	if (Array.isArray(section.errors) && section.errors.length > 0) {
		return false;
	}
	if (
		section.pii_leak_check_passed === false ||
		section.cas_integrity_passed === false ||
		section.schema_validation_passed === false
	) {
		return false;
	}
	if (isFailingStatusToken(section.status)) {
		return false;
	}
	if (section.passed === true || section.valid === true) {
		return true;
	}
	if (isPassingStatusToken(section.status)) {
		return true;
	}
	if (
		section.pii_leak_check_passed === true ||
		section.cas_integrity_passed === true ||
		section.schema_validation_passed === true
	) {
		return true;
	}
	return false;
}

function formatValidationStatus(
	section: PipelineValidationSection | boolean | undefined,
): string {
	if (typeof section === "boolean") {
		return section ? "PASS" : "FAIL";
	}
	if (!section || typeof section !== "object") {
		return "FAIL";
	}
	const passed = isValidationSectionPassed(section);
	if (typeof section.status === "string" && section.status.trim().length > 0) {
		const trimmed = section.status.trim();
		if (passed || !isPassingStatusToken(trimmed)) {
			return trimmed;
		}
	}
	return passed ? "PASS" : "FAIL";
}

function formatWaveStatus(wave: PipelineWaveReport): string {
	if (wave.passed === false) {
		if (
			typeof wave.status === "string" &&
			wave.status.trim().length > 0 &&
			!isPassingStatusToken(wave.status) &&
			wave.status.trim().toUpperCase() !== "DONE"
		) {
			return wave.status.trim();
		}
		return "FAIL";
	}
	if (wave.passed === true) {
		if (
			typeof wave.status === "string" &&
			wave.status.trim().length > 0 &&
			!isFailingStatusToken(wave.status)
		) {
			return wave.status.trim();
		}
		return "PASS";
	}
	if (typeof wave.status === "string" && wave.status.trim().length > 0) {
		return wave.status.trim();
	}
	if (Array.isArray(wave.step_reports) && wave.step_reports.length > 0) {
		const anyFailed = wave.step_reports.some(
			(s) => s.passed === false || isFailingStatusToken(s.status),
		);
		if (anyFailed) {
			return "FAIL";
		}
		const allPassed = wave.step_reports.every(
			(s) => s.passed === true || isPassingStatusToken(s.status),
		);
		if (allPassed) {
			return "PASS";
		}
	}
	return "FAIL";
}

function normalizeStepReport(
	step: PipelineStepReport,
	defaultWaveId?: string,
): PipelineStepReport {
	const rawStatus =
		typeof step.status === "string" && step.status.trim().length > 0
			? step.status.trim()
			: undefined;
	const status =
		rawStatus && !(step.passed === false && isPassingStatusToken(rawStatus))
			? rawStatus
			: step.passed === true
				? "PASS"
				: "FAIL";
	return {
		...step,
		wave_id: step.wave_id ?? defaultWaveId,
		status,
	};
}

function normalizeReportPayload(raw: unknown): AgenticPipelineReport {
	const root =
		raw && typeof raw === "object" && "report" in raw && raw.report
			? (raw.report as Record<string, unknown>)
			: ((raw ?? {}) as Record<string, unknown>);
	const waveReports = Array.isArray(root.wave_reports)
		? (root.wave_reports as PipelineWaveReport[])
		: [];
	const topLevelSteps = Array.isArray(root.step_reports)
		? (root.step_reports as PipelineStepReport[])
		: [];
	const stepReports =
		topLevelSteps.length > 0
			? topLevelSteps.map((s) => normalizeStepReport(s))
			: waveReports.flatMap((w) =>
					Array.isArray(w.step_reports)
						? w.step_reports.map((s) => normalizeStepReport(s, w.wave_id))
						: [],
				);
	const inputValidation = root.input_validation as
		| PipelineValidationSection
		| boolean
		| undefined;
	const outputValidation = root.output_validation as
		| PipelineValidationSection
		| boolean
		| undefined;
	const hasExplicitFailure =
		(inputValidation !== undefined &&
			!isValidationSectionPassed(inputValidation)) ||
		(outputValidation !== undefined &&
			!isValidationSectionPassed(outputValidation)) ||
		waveReports.some(
			(w) => w.passed === false || isFailingStatusToken(w.status),
		) ||
		stepReports.some(
			(s) => s.passed === false || isFailingStatusToken(s.status),
		);
	const rawOverallPassed =
		typeof root.overall_passed === "boolean"
			? root.overall_passed
			: typeof root.status === "string"
				? isPassingStatusToken(root.status)
				: false;
	const overallPassed = !hasExplicitFailure && rawOverallPassed;
	const rawStatus =
		typeof root.status === "string" && root.status.trim().length > 0
			? root.status.trim()
			: undefined;
	return {
		run_id: String(root.run_id ?? root.pipeline_id ?? "run-unknown"),
		pipeline_id:
			typeof root.pipeline_id === "string" ? root.pipeline_id : undefined,
		project_id:
			typeof root.project_id === "string" ? root.project_id : undefined,
		status: rawStatus ?? (overallPassed ? "COMPLETED" : "FAILED"),
		overall_passed: overallPassed,
		input_validation: inputValidation,
		output_validation: outputValidation,
		wave_reports: waveReports,
		step_reports: stepReports,
		intermediate_artifacts: Array.isArray(root.intermediate_artifacts)
			? (root.intermediate_artifacts as PipelineIntermediateArtifact[])
			: [],
	};
}

async function readPipelineEndpointResponse(
	response: Response,
): Promise<
	| { ok: true; body: Record<string, unknown> }
	| { ok: false; errorDetail: string }
> {
	const rawText = await response.text();
	const trimmedText = rawText.trim();
	let parsedJson: Record<string, unknown> | null = null;
	if (trimmedText.length > 0) {
		try {
			const parsed = JSON.parse(trimmedText) as unknown;
			if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
				parsedJson = parsed as Record<string, unknown>;
			}
		} catch {
			parsedJson = null;
		}
	}
	if (!response.ok) {
		if (parsedJson) {
			const detailCandidate =
				parsedJson.detail ??
				parsedJson.title ??
				parsedJson.message ??
				parsedJson.error;
			if (
				typeof detailCandidate === "string" &&
				detailCandidate.trim().length > 0
			) {
				return { ok: false, errorDetail: detailCandidate.trim() };
			}
		}
		if (trimmedText.length > 0 && !parsedJson) {
			return {
				ok: false,
				errorDetail: `HTTP ${response.status}: ${trimmedText}`,
			};
		}
		return { ok: false, errorDetail: `HTTP ${response.status}` };
	}
	if (!parsedJson) {
		return {
			ok: false,
			errorDetail: "Invalid JSON response from pipeline endpoint.",
		};
	}
	return { ok: true, body: parsedJson };
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
				validationResponse: null,
			});
		},

		setHarnessConfigPath: (path: string) => {
			set({
				harnessConfigPath: path,
				validationError: null,
				validationResponse: null,
			});
		},

		setProviderOverride: (provider: HarnessProviderOverrideOption) => {
			set({
				providerOverride: provider,
				validationError: null,
				validationResponse: null,
			});
		},

		setStewardHoldPolicy: (policy: StewardHoldPolicyOption) => {
			set({
				stewardHoldPolicy: policy,
				validationError: null,
				validationResponse: null,
			});
		},

		validatePipelineInputs: async (fetchImpl?: typeof fetch) => {
			const state = get();
			const trimmedInput = state.inputSpecPath.trim();
			if (!trimmedInput) {
				set({
					validationError: EMPTY_INPUT_PATH_VALIDATION_ERROR,
					validationResponse: null,
					pipelineReport: null,
					isSubmitting: false,
				});
				return null;
			}

			set({
				isSubmitting: true,
				validationError: null,
				validationResponse: null,
				pipelineReport: null,
			});
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
				const parsed = await readPipelineEndpointResponse(response);
				if (!parsed.ok) {
					set({
						validationError: parsed.errorDetail,
						validationResponse: null,
						pipelineReport: null,
						isSubmitting: false,
					});
					return null;
				}
				const { body } = parsed;
				const validationSection: PipelineValidationSection =
					body.input_validation && typeof body.input_validation === "object"
						? (body.input_validation as PipelineValidationSection)
						: (body as PipelineValidationSection);
				set({
					validationResponse: validationSection,
					validationError: null,
					pipelineReport: null,
					isSubmitting: false,
				});
				return validationSection;
			} catch (err) {
				set({
					validationError:
						err instanceof Error
							? err.message
							: "Failed to validate pipeline inputs.",
					validationResponse: null,
					pipelineReport: null,
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
					validationResponse: null,
					pipelineReport: null,
					isSubmitting: false,
				});
				return null;
			}

			set({
				isSubmitting: true,
				validationError: null,
				validationResponse: null,
				pipelineReport: null,
			});
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
				const parsed = await readPipelineEndpointResponse(response);
				if (!parsed.ok) {
					set({
						validationError: parsed.errorDetail,
						validationResponse: null,
						pipelineReport: null,
						isSubmitting: false,
					});
					return null;
				}
				const report = normalizeReportPayload(parsed.body);
				set({
					pipelineReport: report,
					validationResponse: null,
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
					validationResponse: null,
					pipelineReport: null,
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
							border: `1px solid ${
								isValidationSectionPassed(launcher.validationResponse)
									? "var(--dv-gate-pass)"
									: "var(--dv-gate-reject)"
							}`,
							borderRadius: "0.375rem",
							padding: "0.5rem 0.75rem",
							fontSize: "0.75rem",
						}}
					>
						<div>
							<strong>Pre-Flight Input Validation: </strong>
							<span>{formatValidationStatus(launcher.validationResponse)}</span>
							{launcher.validationResponse.summary && (
								<span> — {launcher.validationResponse.summary}</span>
							)}
						</div>
						{launcher.validationResponse.errors &&
							launcher.validationResponse.errors.length > 0 && (
								<ul
									data-testid="wb00-pipeline-validation-errors-list"
									style={{
										margin: "0.35rem 0 0",
										paddingLeft: "1.25rem",
										color: "var(--dv-gate-reject)",
									}}
								>
									{launcher.validationResponse.errors.map((err) => (
										<li key={err}>{err}</li>
									))}
								</ul>
							)}
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
												Status: <code>{formatWaveStatus(wave)}</code>
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
