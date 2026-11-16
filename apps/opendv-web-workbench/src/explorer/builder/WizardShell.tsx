/**
 * 4-Step Guided Wizard Pipeline Builder Shell (`WizardShell.tsx`).
 *
 * Replaces the legacy flat multi-wave builder with an intuitive, 4-step guided
 * pipeline authoring workflow synchronized with the live YAML Spec drawer.
 *
 * Steps:
 * 1. Foundation: Studio Session / VaultBlueprint / Raw Ontology & Inferred Mode Banner
 * 2. Connectors: Dynamic Multi-Connector Sources, SQLite/Catalog Pickers & Docs
 * 3. Governance: Agent Model, SQL Dialects, Sampling & Steward Hold Policies
 * 4. Validate & Run: Pre-Flight Action Plan, SSE Live Console & Evolution Diff
 */

import type {
	InferredPrimaryAction,
	VaultBlueprintDTO,
} from "@opendv/protocol-ts";
import { type CSSProperties, useEffect, useMemo, useState } from "react";
import { EXPLORER_COLORS, EXPLORER_FONTS } from "../design-tokens";
import { Step1Foundation } from "./Step1Foundation";
import { Step2ConnectorSources } from "./Step2ConnectorSources";
import { Step3GovernanceAndDialect } from "./Step3GovernanceAndDialect";
import { Step4ValidateAndRun } from "./Step4ValidateAndRun";
import { YamlDrawer } from "./YamlDrawer";
import {
	WIZARD_STEPS,
	type WizardFormState,
	type WizardShellProps,
	type WizardStepId,
	createDefaultWizardFormState,
} from "./wizard-types";
import {
	formStateToPipelineInputDto,
	parseYamlToPipelineInputDto,
	pipelineInputDtoToFormState,
	serializeSpecToYaml,
} from "./wizardSpecMapper";
import { getStudioAuthHeaders } from "../../ontology/store/studio-store";

const CONTAINER_STYLE: CSSProperties = {
	display: "flex",
	flexDirection: "column",
	minHeight: "calc(100vh - 64px)",
	backgroundColor: EXPLORER_COLORS.bgPage,
	fontFamily: EXPLORER_FONTS.sans,
	color: EXPLORER_COLORS.inkPrimary,
};

const STEP_ORDER: readonly WizardStepId[] = [
	"foundation",
	"connectors",
	"governance",
	"validate_run",
];

export function WizardShell({
	initialStep = "foundation",
	initialFormState,
	sessionId,
	source,
	initialDomainNamespace,
	initialPipelineId,
	onRunStarted,
	onBackToExplorer,
	apiUrl,
	fetchImpl,
	validatePipelineFn,
	createRunFn,
	onEvolveSchema,
	onSyncOntologyDelta,
	initialAdapters,
}: WizardShellProps) {
	// Initialize canonical wizard form state
	const [formState, setFormState] = useState<WizardFormState>(() => {
		const base = createDefaultWizardFormState(initialFormState);
		if (sessionId) {
			base.activeSessionId = sessionId;
			base.foundationMode = "studio_session";
		}
		if (initialDomainNamespace) {
			base.domainNamespace = initialDomainNamespace;
		}
		if (initialPipelineId) {
			base.pipelineId = initialPipelineId;
		}
		return base;
	});

	const [currentStep, setCurrentStep] = useState<WizardStepId>(initialStep);
	const [completedSteps, setCompletedSteps] = useState<Set<WizardStepId>>(
		new Set(),
	);
	const [isYamlDrawerOpen, setIsYamlDrawerOpen] = useState(false);

	const currentStepIdx = STEP_ORDER.indexOf(currentStep);

	const updateFormState = (updates: Partial<WizardFormState>) => {
		setFormState((prev) => ({ ...prev, ...updates }));
	};

	// Ensure active studio session blueprint is loaded into persistent form state
	useEffect(() => {
		const sid =
			sessionId ||
			formState.activeSessionId ||
			(typeof window !== "undefined" && window.location?.search
				? new URLSearchParams(window.location.search).get("session_id")
				: null);
		if (!sid || formState.blueprint) return;
		let isMounted = true;
		const fetcher = fetchImpl ?? (typeof fetch !== "undefined" ? fetch : null);
		if (!fetcher) return;
		const exportUrl = `${apiUrl || "/api/v1"}/ontology/studio/sessions/${encodeURIComponent(sid)}/export-blueprint`;
		fetcher(
			exportUrl,
			{ credentials: "include", headers: getStudioAuthHeaders(undefined, exportUrl) },
		)
			.then((r) => (r.ok ? r.json() : null))
			.then((d) => {
				if (!isMounted || !d?.blueprint) return;
				setFormState((prev) => ({
					...prev,
					activeSessionId: sid,
					blueprint: d.blueprint,
					contract: d.blueprint.contract ?? prev.contract,
					domainNamespace: d.blueprint.domain_namespace ?? prev.domainNamespace,
				}));
			})
			.catch(() => {});
		return () => {
			isMounted = false;
		};
	}, [sessionId, formState.activeSessionId, formState.blueprint, apiUrl, fetchImpl]);

	// Fetch authoritative InferredExecutionPlanDTO on form state changes
	useEffect(() => {
		let isMounted = true;
		const fetcher = fetchImpl ?? (typeof fetch !== "undefined" ? fetch : null);
		if (!fetcher) return;
		const timer = setTimeout(() => {
			const dto = formStateToPipelineInputDto(formState);
			const validateUrl = `${apiUrl || "/api/v1"}/runs/validate`;
			fetcher(validateUrl, {
				method: "POST",
				credentials: "include",
				headers: getStudioAuthHeaders({
					"Content-Type": "application/json",
				}, validateUrl),
				body: JSON.stringify(dto),
			})
				.then((r) => (r.ok ? r.json() : null))
				.then((plan) => {
					if (!isMounted || !plan?.primary_action) return;
					setFormState((prev) => ({ ...prev, inferredPlan: plan }));
				})
				.catch(() => {});
		}, 300);
		return () => {
			isMounted = false;
			clearTimeout(timer);
		};
	}, [
		formState.foundationMode,
		formState.activeSessionId,
		formState.blueprint,
		formState.blueprintPath,
		formState.blueprintContent,
		formState.ontologySource,
		formState.ontologyPath,
		formState.ontologyContent,
		formState.contract,
		formState.contractPath,
		formState.contractContent,
		formState.configuredSources,
		formState.unstructuredDocs,
		formState.targetDialects,
		apiUrl,
		fetchImpl,
	]);

	// Determine Inferred Mode banner metadata
	const hasExistingContract = Boolean(
		formState.foundationMode === "vault_blueprint" ||
			formState.contract !== null ||
			formState.contractContent.trim(),
	);
	const isZeroData =
		formState.configuredSources.length === 0 &&
		formState.unstructuredDocs.length === 0;

	const inferredAction: InferredPrimaryAction = useMemo(() => {
		if (formState.configuredSources.length > 0 && formState.unstructuredDocs.length > 0) {
			return "HYBRID_PER_SOURCE";
		}
		if (formState.inferredPlan?.primary_action) {
			return formState.inferredPlan.primary_action;
		}
		if (isZeroData) {
			return hasExistingContract ? "VERIFIED_NO_OP" : "COLD_START_N1";
		}
		if (!hasExistingContract) {
			return "COLD_START_N1";
		}
		return "MULTI_SOURCE_EXPANSION";
	}, [
		formState.inferredPlan,
		formState.configuredSources.length,
		formState.unstructuredDocs.length,
		hasExistingContract,
		isZeroData,
	]);

	// Serialized YAML for drawer synchronization
	const liveYamlText = useMemo(() => {
		const dto = formStateToPipelineInputDto(formState);
		return serializeSpecToYaml(dto);
	}, [formState]);

	const handleApplyYamlFromDrawer = (yamlContent: string) => {
		try {
			const dto = parseYamlToPipelineInputDto(yamlContent);
			const updatedState = pipelineInputDtoToFormState(dto, formState);
			setFormState(updatedState);
		} catch {
			// Handled by YamlDrawer error display
		}
	};

	const handleNextStep = () => {
		setCompletedSteps((prev) => new Set(prev).add(currentStep));
		if (currentStepIdx < STEP_ORDER.length - 1) {
			const nextStepId = STEP_ORDER[currentStepIdx + 1];
			if (nextStepId) {
				setCurrentStep(nextStepId);
			}
		}
	};

	const handlePrevStep = () => {
		if (currentStepIdx > 0) {
			const prevStepId = STEP_ORDER[currentStepIdx - 1];
			if (prevStepId) {
				setCurrentStep(prevStepId);
			}
		}
	};

	const handleStepClick = (stepId: WizardStepId) => {
		const targetIdx = STEP_ORDER.indexOf(stepId);
		// Allow clicking current or any previously visited/completed step
		if (targetIdx <= currentStepIdx || completedSteps.has(stepId)) {
			setCurrentStep(stepId);
		}
	};

	const handleEvolveSchema = (newBlueprint: VaultBlueprintDTO) => {
		const bpContract = newBlueprint.contract || (newBlueprint as any).final_contract || formState.contract;
		const effectiveBp = bpContract && !newBlueprint.contract ? { ...newBlueprint, contract: bpContract } : newBlueprint;
		const bpContent = serializeSpecToYaml(effectiveBp);
		const contractContent = bpContract ? serializeSpecToYaml(bpContract) : "";

		setFormState((prev) => ({
			...prev,
			foundationMode: "vault_blueprint",
			blueprint: effectiveBp,
			blueprintContent: bpContent,
			blueprintPath: "",
			contract: bpContract ?? null,
			contractContent,
			contractPath: "",
			domainNamespace: effectiveBp.domain_namespace || prev.domainNamespace,
			configuredSources: [], // Reset Step 2 connector sources to empty, ready for Run 2!
			unstructuredDocs: [],
			inferredPlan: {
				plan_id: `plan_evolve_${Date.now()}`,
				primary_action: "VERIFIED_NO_OP",
				explanation: "Row 5: Deterministic Verification Pass on existing contract with 0 mutations.",
				table_actions: [],
				doc_actions: [],
				inferred_at_utc: new Date().toISOString(),
			},
			validationReport: null,
		}));

		// Navigate back to Step 1 in Existing Schema Evolution mode
		setCurrentStep("foundation");
		onEvolveSchema?.(newBlueprint);
	};

	return (
		<div
			style={CONTAINER_STYLE}
			data-testid="wizard-shell"
			id="pipeline-builder-root"
		>
			<div
				data-testid="pipeline-builder-view"
				style={{
					position: "absolute",
					width: "1px",
					height: "1px",
					overflow: "hidden",
				}}
			/>

			{/* Top Bar with Mode Banner & YAML Spec Action */}
			<header
				style={{
					padding: "14px 24px",
					backgroundColor: EXPLORER_COLORS.bgSurface,
					borderBottom: `1px solid ${EXPLORER_COLORS.borderLine}`,
					display: "flex",
					alignItems: "center",
					justifyContent: "space-between",
					gap: "16px",
					flexWrap: "wrap",
				}}
			>
				<div style={{ display: "flex", alignItems: "center", gap: "14px" }}>
					{onBackToExplorer && (
						<button
							type="button"
							data-testid="btn-back-to-explorer"
							onClick={onBackToExplorer}
							style={{
								border: `1px solid ${EXPLORER_COLORS.borderLine}`,
								backgroundColor: EXPLORER_COLORS.bgPage,
								padding: "5px 10px",
								borderRadius: "4px",
								fontSize: "12px",
								cursor: "pointer",
								color: EXPLORER_COLORS.inkMuted,
							}}
						>
							← Back to Explorer
						</button>
					)}
					<div>
						<h1
							style={{
								fontSize: "17px",
								fontWeight: 700,
								margin: 0,
								color: EXPLORER_COLORS.inkPrimary,
							}}
						>
							Pipeline Builder
						</h1>
						<div
							style={{
								fontSize: "12px",
								color: EXPLORER_COLORS.inkMuted,
								marginTop: "2px",
							}}
						>
							Data-Driven Single-Run Architecture · Zero Manual Wave Filtering
						</div>
					</div>
				</div>

				{/* Inferred Mode Banner Badge */}
				<div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
					<div
						data-testid="inferred-mode-banner"
						style={{
							padding: "4px 10px",
							borderRadius: "16px",
							fontSize: "12px",
							fontFamily: EXPLORER_FONTS.mono,
							fontWeight: 600,
							display: "flex",
							alignItems: "center",
							gap: "6px",
							backgroundColor: hasExistingContract ? "#FEF3C7" : "#E0F2FE",
							color: hasExistingContract ? "#92400E" : "#0369A1",
							border: `1px solid ${hasExistingContract ? "#FDE68A" : "#BAE6FD"}`,
						}}
					>
						<span>●</span>
						{hasExistingContract
							? "Existing Schema Evolution"
							: "Greenfield Future-Proof Design"}
						{isZeroData && (
							<span
								style={{
									opacity: 0.85,
									fontSize: "11px",
									marginLeft: "4px",
								}}
							>
								({inferredAction})
							</span>
						)}
					</div>

					{/* Toggle live YAML Drawer */}
					<button
						type="button"
						data-testid="toggle-yaml-drawer"
						onClick={() => setIsYamlDrawerOpen(true)}
						style={{
							padding: "6px 12px",
							borderRadius: "6px",
							fontSize: "12px",
							fontFamily: EXPLORER_FONTS.mono,
							fontWeight: 600,
							backgroundColor: EXPLORER_COLORS.bgPage,
							border: `1px solid ${EXPLORER_COLORS.borderLine}`,
							color: EXPLORER_COLORS.inkPrimary,
							cursor: "pointer",
							display: "flex",
							alignItems: "center",
							gap: "6px",
						}}
					>
						<span>{"{ }"}</span> YAML Spec
					</button>
				</div>
			</header>

			{/* 4-Step Stepper Progress Bar */}
			<nav
				aria-label="Wizard Steps"
				style={{
					padding: "16px 24px",
					backgroundColor: EXPLORER_COLORS.bgSurface,
					borderBottom: `1px solid ${EXPLORER_COLORS.borderLine}`,
					display: "flex",
					alignItems: "center",
					justifyContent: "center",
				}}
			>
				<div
					style={{
						display: "flex",
						alignItems: "center",
						width: "100%",
						maxWidth: "840px",
					}}
				>
					{WIZARD_STEPS.map((step, idx) => {
						const isCurrent = step.id === currentStep;
						const isCompleted = completedSteps.has(step.id);
						const isClickable =
							idx <= currentStepIdx || completedSteps.has(step.id);

						return (
							<div
								key={step.id}
								style={{
									display: "flex",
									alignItems: "center",
									flex: idx === WIZARD_STEPS.length - 1 ? "none" : 1,
								}}
							>
								<button
									type="button"
									data-testid={`wizard-step-tab-${step.id}`}
									onClick={() => isClickable && handleStepClick(step.id)}
									disabled={!isClickable}
									style={{
										background: "none",
										border: "none",
										display: "flex",
										alignItems: "center",
										gap: "10px",
										cursor: isClickable ? "pointer" : "default",
										padding: "4px 8px",
										borderRadius: "6px",
										textAlign: "left",
									}}
								>
									{/* Step Badge */}
									<div
										data-testid={`step-badge-${step.id}`}
										style={{
											width: "28px",
											height: "28px",
											borderRadius: "50%",
											display: "flex",
											alignItems: "center",
											justifyContent: "center",
											fontSize: "12px",
											fontFamily: EXPLORER_FONTS.mono,
											fontWeight: 700,
											backgroundColor: isCurrent
												? EXPLORER_COLORS.borderSelected
												: isCompleted
													? "#16A34A"
													: "#E2E8F0",
											color: isCurrent || isCompleted ? "#FFFFFF" : "#64748B",
											transition: "all 0.2s ease",
										}}
									>
										{isCompleted ? "✓" : step.stepNumber}
									</div>

									<div>
										<div
											style={{
												fontSize: "13px",
												fontWeight: isCurrent ? 700 : 500,
												color: isCurrent
													? EXPLORER_COLORS.borderSelected
													: EXPLORER_COLORS.inkPrimary,
											}}
										>
											{step.shortTitle}
										</div>
									</div>
								</button>

								{/* Step Connector Line */}
								{idx < WIZARD_STEPS.length - 1 && (
									<div
										style={{
											flex: 1,
											height: "2px",
											backgroundColor: isCompleted ? "#16A34A" : "#E2E8F0",
											margin: "0 12px",
											transition: "background-color 0.2s ease",
										}}
									/>
								)}
							</div>
						);
					})}
				</div>
			</nav>

			{/* Main Step Content Container */}
			<main
				style={{
					flex: 1,
					padding: "24px",
					display: "flex",
					justifyContent: "center",
				}}
			>
				<div style={{ width: "100%", maxWidth: "840px" }}>
					{currentStep === "foundation" && (
						<Step1Foundation
							formState={formState}
							onChange={updateFormState}
							apiUrl={apiUrl}
							fetchImpl={fetchImpl}
						/>
					)}

					{currentStep === "connectors" && (
						<Step2ConnectorSources
							formState={formState}
							onChange={updateFormState}
							apiUrl={apiUrl}
							fetchImpl={fetchImpl}
							initialAdapters={initialAdapters}
						/>
					)}

					{currentStep === "governance" && (
						<Step3GovernanceAndDialect
							formState={formState}
							onChange={updateFormState}
							apiUrl={apiUrl}
							fetchImpl={fetchImpl}
						/>
					)}

					{currentStep === "validate_run" && (
						<Step4ValidateAndRun
							formState={formState}
							onChange={updateFormState}
							apiUrl={apiUrl}
							fetchImpl={fetchImpl}
							createRunFn={createRunFn}
							validatePipelineFn={validatePipelineFn}
							onEvolveSchema={handleEvolveSchema}
							onSyncOntologyDelta={onSyncOntologyDelta}
						/>
					)}
				</div>
			</main>

			{/* Footer Navigation Bar */}
			<footer
				style={{
					padding: "14px 24px",
					backgroundColor: EXPLORER_COLORS.bgSurface,
					borderTop: `1px solid ${EXPLORER_COLORS.borderLine}`,
					display: "flex",
					alignItems: "center",
					justifyContent: "space-between",
				}}
			>
				<button
					type="button"
					data-testid="btn-wizard-back"
					onClick={handlePrevStep}
					disabled={currentStepIdx === 0}
					style={{
						padding: "7px 16px",
						borderRadius: "5px",
						fontSize: "13px",
						fontWeight: 500,
						backgroundColor: EXPLORER_COLORS.bgPage,
						border: `1px solid ${EXPLORER_COLORS.borderLine}`,
						color:
							currentStepIdx === 0 ? "#94A3B8" : EXPLORER_COLORS.inkPrimary,
						cursor: currentStepIdx === 0 ? "not-allowed" : "pointer",
					}}
				>
					← Back
				</button>

				<div
					data-testid="wizard-step-indicator"
					style={{
						fontSize: "12px",
						fontFamily: EXPLORER_FONTS.mono,
						color: EXPLORER_COLORS.inkMuted,
					}}
				>
					Step {currentStepIdx + 1} of {STEP_ORDER.length}
				</div>

				<button
					type="button"
					data-testid="btn-wizard-next"
					onClick={handleNextStep}
					disabled={currentStepIdx === STEP_ORDER.length - 1}
					style={{
						padding: "7px 16px",
						borderRadius: "5px",
						fontSize: "13px",
						fontWeight: 500,
						backgroundColor:
							currentStepIdx === STEP_ORDER.length - 1
								? "#CBD5E1"
								: EXPLORER_COLORS.borderSelected,
						border: "none",
						color: "#FFFFFF",
						cursor:
							currentStepIdx === STEP_ORDER.length - 1
								? "not-allowed"
								: "pointer",
					}}
				>
					Next ➔
				</button>
			</footer>

			{/* Slide-Out Live YAML Spec Drawer */}
			<YamlDrawer
				isOpen={isYamlDrawerOpen}
				onClose={() => setIsYamlDrawerOpen(false)}
				yamlText={liveYamlText}
				onApplyYaml={handleApplyYamlFromDrawer}
			/>
		</div>
	);
}
