/**
 * Interactive Pipeline Builder & Two-Step Execution View (`PipelineBuilderView.tsx`)
 *
 * Provides two authoring modes:
 * 1. Interactive Form Builder (`InteractiveSpecForm`)
 * 2. Upload / YAML Spec Editor (`YamlSpecUploadEditor`)
 *
 * Enforces strict two-step execution:
 * - Step 1: Stage & Validate Spec (`POST /api/v1/pipelines/validate`)
 * - Step 2: Explicit "▶ Run Pipeline" action (`POST /api/v1/pipelines/runs`)
 * Uploading or editing a spec NEVER auto-runs a pipeline.
 */

import type {
	PipelineRunHandle,
	PipelineRunRequest,
	PipelineValidateRequest,
} from "@opendv/protocol-ts";
import { EXPLORER_COLORS, EXPLORER_FONTS } from "../design-tokens";
import type { StewardHoldPolicyOption } from "../modals/NewRunFormFields";
import { NewRunStatusBanners } from "../modals/NewRunStatusBanners";
import {
	NewRunValidationSection,
	type PipelineValidationReport,
} from "../modals/NewRunValidationSection";
import { safeUseState } from "../modals/safeHooks";
import { executeSubmitRun, executeValidate } from "../modals/useNewRunActions";
import { InteractiveSpecForm } from "./InteractiveSpecForm";
import { YamlSpecUploadEditor } from "./YamlSpecUploadEditor";
import {
	type InteractivePipelineDraft,
	type StagedPipelineSpec,
	createDefaultPipelineDraft,
	draftToStagedSpec,
	draftToYamlOrJsonText,
} from "./specBuilderUtils";

export type BuilderMode = "interactive" | "yaml_upload";

export interface PipelineBuilderViewProps {
	initialMode?: BuilderMode | undefined;
	initialDraft?: InteractivePipelineDraft | undefined;
	initialYamlContent?: string | undefined;
	initialSpecPath?: string | undefined;
	initialFileName?: string | undefined;
	initialContractPath?: string | undefined;
	initialWaveId?: string | undefined;
	initialHoldPolicy?: StewardHoldPolicyOption | undefined;
	initialValidationReport?: PipelineValidationReport | null | undefined;
	initialCreatedHandle?: PipelineRunHandle | null | undefined;
	initialSubmitError?: string | null | undefined;
	stagedSpec?: StagedPipelineSpec | null | undefined;
	onStageSpec?: ((staged: StagedPipelineSpec) => void) | undefined;
	onRunStarted?: ((handle: PipelineRunHandle) => void) | undefined;
	fetchImpl?: typeof fetch | undefined;
	apiUrl?: string | undefined;
	validatePipelineFn?:
		| ((req: PipelineValidateRequest) => Promise<PipelineValidationReport>)
		| undefined;
	createRunFn?:
		| ((req: PipelineRunRequest) => Promise<PipelineRunHandle>)
		| undefined;
}

export function buildCurrentStagedSpec(params: {
	mode: BuilderMode;
	draft: InteractivePipelineDraft;
	yamlSpecPath: string;
	yamlContent: string;
	yamlFileName?: string | undefined;
	yamlContractPath: string;
	yamlContractContent?: string | undefined;
	yamlWaveId: string;
	yamlHoldPolicy: StewardHoldPolicyOption;
	validated?: boolean | undefined;
}): StagedPipelineSpec {
	const {
		mode,
		draft,
		yamlSpecPath,
		yamlContent,
		yamlFileName,
		yamlContractPath,
		yamlContractContent,
		yamlWaveId,
		yamlHoldPolicy,
		validated = false,
	} = params;

	if (mode === "interactive") {
		return draftToStagedSpec(draft, validated);
	}

	let parsedObj: Record<string, unknown> | undefined;
	const trimmed = yamlContent.trim();
	if (trimmed.startsWith("{")) {
		try {
			const parsed = JSON.parse(trimmed) as unknown;
			if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
				parsedObj = parsed as Record<string, unknown>;
			}
		} catch {
			// Raw YAML content; backend parses input_spec_content
		}
	}

	return {
		mode: "yaml_upload",
		inputSpec: parsedObj,
		inputSpecContent: yamlContent || undefined,
		inputSpecPath: yamlSpecPath.trim() || undefined,
		fileName: yamlFileName,
		initialContractPath: yamlContractPath.trim() || undefined,
		initialContractContent: yamlContractContent,
		waveId: yamlWaveId.trim() || undefined,
		stewardHoldPolicy: yamlHoldPolicy,
		validated,
	};
}

export function PipelineBuilderView({
	initialMode = "interactive",
	initialDraft,
	initialYamlContent,
	initialSpecPath = "",
	initialFileName,
	initialContractPath = "",
	initialWaveId = "",
	initialHoldPolicy = "pause",
	initialValidationReport = null,
	initialCreatedHandle = null,
	initialSubmitError = null,
	stagedSpec,
	onStageSpec,
	onRunStarted,
	fetchImpl,
	apiUrl,
	validatePipelineFn,
	createRunFn,
}: PipelineBuilderViewProps = {}) {
	const starterDraft = initialDraft ?? createDefaultPipelineDraft();
	const [mode, setMode] = safeUseState<BuilderMode>(
		stagedSpec?.mode ?? initialMode,
	);
	const [draft, setDraft] =
		safeUseState<InteractivePipelineDraft>(starterDraft);

	const [yamlSpecPath, setYamlSpecPath] = safeUseState<string>(
		stagedSpec?.inputSpecPath ?? initialSpecPath,
	);
	const [yamlContent, setYamlContent] = safeUseState<string>(
		stagedSpec?.inputSpecContent ??
			initialYamlContent ??
			draftToYamlOrJsonText(starterDraft),
	);
	const [yamlFileName, setYamlFileName] = safeUseState<string | undefined>(
		stagedSpec?.fileName ?? initialFileName,
	);
	const [yamlContractPath, setYamlContractPath] = safeUseState<string>(
		stagedSpec?.initialContractPath ?? initialContractPath,
	);
	const [yamlContractContent, setYamlContractContent] = safeUseState<
		string | undefined
	>(stagedSpec?.initialContractContent);
	const [yamlContractFileName, setYamlContractFileName] = safeUseState<
		string | undefined
	>(undefined);
	const [yamlWaveId, setYamlWaveId] = safeUseState<string>(
		stagedSpec?.waveId ?? initialWaveId,
	);
	const [yamlHoldPolicy, setYamlHoldPolicy] =
		safeUseState<StewardHoldPolicyOption>(
			stagedSpec?.stewardHoldPolicy ?? initialHoldPolicy,
		);

	const [isValidating, setIsValidating] = safeUseState(false);
	const [validationReport, setValidationReport] =
		safeUseState<PipelineValidationReport | null>(initialValidationReport);

	const [isSubmitting, setIsSubmitting] = safeUseState(false);
	const [createdHandle, setCreatedHandle] =
		safeUseState<PipelineRunHandle | null>(initialCreatedHandle);
	const [submitError, setSubmitError] = safeUseState<string | null>(
		initialSubmitError,
	);

	const currentStaged = buildCurrentStagedSpec({
		mode,
		draft,
		yamlSpecPath,
		yamlContent,
		yamlFileName,
		yamlContractPath,
		yamlContractContent,
		yamlWaveId,
		yamlHoldPolicy,
		validated: validationReport?.passed ?? stagedSpec?.validated ?? false,
	});

	const handleDraftChange = (nextDraft: InteractivePipelineDraft) => {
		setDraft(nextDraft);
		const nextStaged = draftToStagedSpec(nextDraft, false);
		onStageSpec?.(nextStaged);
	};

	const handleYamlContentChange = (nextContent: string) => {
		setYamlContent(nextContent);
		onStageSpec?.(
			buildCurrentStagedSpec({
				mode: "yaml_upload",
				draft,
				yamlSpecPath,
				yamlContent: nextContent,
				yamlFileName,
				yamlContractPath,
				yamlContractContent,
				yamlWaveId,
				yamlHoldPolicy,
				validated: false,
			}),
		);
	};

	const handleStageAndValidate = async () => {
		setIsValidating(true);
		setSubmitError(null);
		onStageSpec?.(currentStaged);
		try {
			const report = await executeValidate({
				specPath: currentStaged.inputSpecPath ?? "",
				specFileName: currentStaged.fileName,
				specFileContent: currentStaged.inputSpecContent,
				inputSpec: currentStaged.inputSpec,
				contractPath: currentStaged.initialContractPath ?? "",
				contractFileContent: currentStaged.initialContractContent,
				waveId: currentStaged.waveId ?? "",
				holdPolicy: currentStaged.stewardHoldPolicy,
				apiUrl,
				fetchImpl,
				validatePipelineFn,
			});
			setValidationReport(report);
			onStageSpec?.({
				...currentStaged,
				validated: report.passed,
			});
		} catch (err) {
			setValidationReport({
				stage: "INPUT_VALIDATION",
				passed: false,
				checked_items_count: 1,
				diagnostics: [
					{
						severity: "ERROR",
						message: err instanceof Error ? err.message : String(err),
					},
				],
			});
		} finally {
			setIsValidating(false);
		}
	};

	const handleRunPipeline = async () => {
		setIsSubmitting(true);
		setSubmitError(null);
		onStageSpec?.(currentStaged);
		try {
			const handle = await executeSubmitRun({
				specPath: currentStaged.inputSpecPath ?? "",
				specFileContent: currentStaged.inputSpecContent,
				inputSpec: currentStaged.inputSpec,
				contractPath: currentStaged.initialContractPath ?? "",
				contractFileContent: currentStaged.initialContractContent,
				waveId: currentStaged.waveId ?? "",
				holdPolicy: currentStaged.stewardHoldPolicy,
				apiUrl,
				fetchImpl,
				createRunFn,
			});
			setCreatedHandle(handle);
			onRunStarted?.(handle);
		} catch (err) {
			setSubmitError(err instanceof Error ? err.message : String(err));
		} finally {
			setIsSubmitting(false);
		}
	};

	return (
		<section
			data-testid="pipeline-builder-view"
			aria-label="Interactive Pipeline Builder"
			style={{
				maxWidth: "1120px",
				margin: "0 auto",
				padding: "20px 24px",
				fontFamily: EXPLORER_FONTS.sans,
				color: EXPLORER_COLORS.inkPrimary,
			}}
		>
			<form
				onSubmit={(e) => e.preventDefault()}
				style={{ display: "flex", flexDirection: "column", gap: "16px" }}
			>
				{/* Header & Mode Switcher */}
				<div
					style={{
						display: "flex",
						flexWrap: "wrap",
						alignItems: "center",
						justifyContent: "space-between",
						gap: "12px",
						padding: "14px 18px",
						borderRadius: "8px",
						border: `1px solid ${EXPLORER_COLORS.borderLine}`,
						backgroundColor: EXPLORER_COLORS.bgSurface,
					}}
				>
					<div>
						<h2 style={{ margin: 0, fontSize: "16px", fontWeight: 600 }}>
							Pipeline Specification Builder
						</h2>
						<p
							style={{
								margin: "4px 0 0",
								fontSize: "12px",
								color: EXPLORER_COLORS.inkMuted,
							}}
						>
							Stage and validate your pipeline specification first, then click
							Run Pipeline to start execution.
						</p>
					</div>

					<fieldset
						aria-label="Builder Authoring Mode"
						style={{
							display: "inline-flex",
							borderRadius: "6px",
							border: `1px solid ${EXPLORER_COLORS.borderLine}`,
							backgroundColor: EXPLORER_COLORS.bgSubtle,
							padding: "2px",
							margin: 0,
							gap: "2px",
						}}
					>
						<button
							type="button"
							data-testid="btn-builder-mode-form"
							aria-pressed={mode === "interactive"}
							onClick={() => setMode("interactive")}
							style={{
								padding: "6px 12px",
								fontSize: "12px",
								fontWeight: mode === "interactive" ? 600 : 500,
								borderRadius: "4px",
								border: "none",
								backgroundColor:
									mode === "interactive"
										? EXPLORER_COLORS.bgSurface
										: "transparent",
								color: EXPLORER_COLORS.inkPrimary,
								cursor: "pointer",
							}}
						>
							Interactive Form Builder
						</button>
						<button
							type="button"
							data-testid="btn-builder-mode-yaml"
							aria-pressed={mode === "yaml_upload"}
							onClick={() => setMode("yaml_upload")}
							style={{
								padding: "6px 12px",
								fontSize: "12px",
								fontWeight: mode === "yaml_upload" ? 600 : 500,
								borderRadius: "4px",
								border: "none",
								backgroundColor:
									mode === "yaml_upload"
										? EXPLORER_COLORS.bgSurface
										: "transparent",
								color: EXPLORER_COLORS.inkPrimary,
								cursor: "pointer",
							}}
						>
							Upload / YAML Spec Editor
						</button>
					</fieldset>
				</div>

				{/* Active Builder Mode Body */}
				{mode === "interactive" ? (
					<InteractiveSpecForm draft={draft} onChange={handleDraftChange} />
				) : (
					<YamlSpecUploadEditor
						specPath={yamlSpecPath}
						onSpecPathChange={setYamlSpecPath}
						specContent={yamlContent}
						onSpecContentChange={handleYamlContentChange}
						fileName={yamlFileName}
						onFileNameChange={setYamlFileName}
						contractPath={yamlContractPath}
						onContractPathChange={setYamlContractPath}
						contractContent={yamlContractContent}
						onContractContentChange={setYamlContractContent}
						contractFileName={yamlContractFileName}
						onContractFileNameChange={setYamlContractFileName}
						waveId={yamlWaveId}
						onWaveIdChange={setYamlWaveId}
						holdPolicy={yamlHoldPolicy}
						onHoldPolicyChange={setYamlHoldPolicy}
					/>
				)}

				{/* Validation Diagnostics & Status Banners */}
				<NewRunValidationSection
					isValidating={isValidating}
					onValidate={handleStageAndValidate}
					validationReport={validationReport}
				/>

				<NewRunStatusBanners
					createdHandle={createdHandle}
					submitError={submitError}
				/>

				{/* Two-Step Action Bar */}
				<div
					style={{
						display: "flex",
						alignItems: "center",
						justifyContent: "space-between",
						padding: "14px 18px",
						borderRadius: "8px",
						border: `1px solid ${EXPLORER_COLORS.borderLine}`,
						backgroundColor: EXPLORER_COLORS.bgSurface,
						gap: "12px",
						flexWrap: "wrap",
					}}
				>
					<span style={{ fontSize: "12px", color: EXPLORER_COLORS.inkMuted }}>
						Step 1: Stage & Validate · Step 2: Run Pipeline
					</span>
					<div style={{ display: "flex", gap: "10px", alignItems: "center" }}>
						<button
							type="button"
							data-testid="btn-builder-validate"
							onClick={handleStageAndValidate}
							disabled={isValidating}
							style={{
								padding: "7px 14px",
								fontSize: "12.5px",
								fontWeight: 500,
								border: `1px solid ${EXPLORER_COLORS.borderLine}`,
								borderRadius: "4px",
								background: EXPLORER_COLORS.bgSubtle,
								color: EXPLORER_COLORS.inkPrimary,
								cursor: isValidating ? "wait" : "pointer",
							}}
						>
							{isValidating ? "Validating..." : "Stage & Validate Spec"}
						</button>
						<button
							type="button"
							data-testid="btn-builder-run-pipeline"
							onClick={handleRunPipeline}
							disabled={isSubmitting}
							style={{
								padding: "7px 16px",
								fontSize: "12.5px",
								fontWeight: 600,
								border: "none",
								borderRadius: "4px",
								background: EXPLORER_COLORS.inkPrimary,
								color: "#FFFFFF",
								cursor: isSubmitting ? "wait" : "pointer",
							}}
						>
							{isSubmitting ? "Starting Run..." : "▶ Run Pipeline"}
						</button>
					</div>
				</div>
			</form>
		</section>
	);
}
