/**
 * Unified 6-Step Ontology & Data Vault 2.0 Pipeline Wizard Shell
 * (apps/opendv-web-workbench/src/ontology/wizard/OntologyWizardShell.tsx)
 */

import type { PipelineRunHandle } from "@opendv/protocol-ts";
import type React from "react";
import { CLAUDE_TOKENS } from "../../explorer/design-tokens";
import { safeUseEffect, safeUseState } from "../../explorer/modals/safeHooks";
import type { PlatformConfigResponse } from "../../explorer/settings/usePlatformConfigActions";
import { studioStore, useStudioStore } from "../store/studio-store";
import { CitationInspectorDrawer } from "./CitationInspectorDrawer";
import { SessionLogsInspector } from "./SessionLogsInspector";
import { Step1ScopeAndBaseline } from "./Step1ScopeAndBaseline";
import { Step2SourcesAndNotes } from "./Step2SourcesAndNotes";
import { Step3PreviewEvidence } from "./Step3PreviewEvidence";
import { Step4AgentDiscovery } from "./Step4AgentDiscovery";
import { Step5DiffGraphExport } from "./Step5DiffGraphExport";
import { Step6PipelineBuildAndRun } from "./Step6PipelineBuildAndRun";
import {
	type ConceptOverrideMap,
	applyAskFiboActionToStore,
	deriveStep4State,
	syncStep4DecisionsToStore,
} from "./wizard-step4-adapter";
import {
	WIZARD_STEP_LABELS,
	type WizardStepNumber,
	computeStep5DiffSummary,
	mapAskFiboActions,
	triggerTextFileDownload,
} from "./wizard-types";

export interface OntologyWizardShellProps {
	sessionId?: string | null | undefined;
	source?: string | null | undefined;
	initialDomainNamespace?: string | null | undefined;
	initialPipelineId?: string | null | undefined;
	initialStep?: WizardStepNumber | undefined;
	platformConfig?: PlatformConfigResponse | null | undefined;
	onPlatformConfigUpdated?:
		| ((config: PlatformConfigResponse, ready: boolean) => void)
		| undefined;
	onOpenLogs?:
		| ((opts?: { stageId?: string; wizardStep?: number }) => void)
		| undefined;
	onExploreDvSchema?:
		| ((params?: {
				runId?: string | null | undefined;
				compareRunId?: string | null | undefined;
				sessionId?: string | null | undefined;
		  }) => void)
		| undefined;
	onRunStarted?: ((handle: PipelineRunHandle) => void) | undefined;
	onBackToExplorer?: (() => void) | undefined;
	fetchImpl?: typeof fetch | undefined;
	apiUrl?: string | undefined;
}

export const OntologyWizardShell: React.FC<OntologyWizardShellProps> = ({
	sessionId: initialSessionId,
	initialDomainNamespace,
	initialStep = 1,
	platformConfig,
	onPlatformConfigUpdated,
	onOpenLogs,
	onExploreDvSchema,
	onRunStarted,
	fetchImpl = fetch,
	apiUrl,
}) => {
	const [activeStep, setActiveStep] =
		safeUseState<WizardStepNumber>(initialStep);
	const [baselineMode, setBaselineMode] = safeUseState<"new" | "evolve">("new");
	const [newSourceKind, setNewSourceKind] = safeUseState<
		"blank" | "workspace_seed" | "starter_packs"
	>("blank");
	const [selectedPackIds, setSelectedPackIds] = safeUseState<string[]>([]);
	const [ocrMode, setOcrMode] = safeUseState<"auto" | "force" | "off">("auto");
	const [doNotSend, setDoNotSend] = safeUseState(false);
	const [conceptOverrides, setConceptOverrides] =
		safeUseState<ConceptOverrideMap>({});
	const [breakingOverrides, setBreakingOverrides] = safeUseState<
		Record<string, boolean>
	>({});
	const [citationDrawerOpen, setCitationDrawerOpen] = safeUseState(false);
	const [visualEditorOpen, setVisualEditorOpen] = safeUseState(false);
	const [highlightChunkId, setHighlightChunkId] = safeUseState<string | null>(
		null,
	);

	const store = useStudioStore((s) => s);

	const { concepts: step4Concepts, breakingCandidates: breakingList } =
		deriveStep4State(
			store.proposals,
			store.stagedCandidates,
			conceptOverrides,
			breakingOverrides,
		);

	const effectiveSessionId =
		store.activeSession?.session_id ?? initialSessionId ?? null;

	safeUseEffect(() => {
		void store.loadSessions(fetchImpl, { wizardOnly: true });
		void store.loadStarterPacks(fetchImpl);
		void store.loadPipelineBaselines(fetchImpl);
	}, []);

	safeUseEffect(() => {
		if (
			initialSessionId &&
			studioStore.getState().activeSession?.session_id !== initialSessionId
		) {
			void store.selectSession(initialSessionId, fetchImpl);
		}
	}, [initialSessionId]);

	safeUseEffect(() => {
		if (activeStep === 3 && effectiveSessionId) {
			void Promise.all([
				store.loadEvidenceTables(fetchImpl, effectiveSessionId),
				store.loadEvidenceChunks(fetchImpl, effectiveSessionId),
			]);
		} else if (activeStep === 6) {
			void store.loadPipelineBaselines(fetchImpl);
			if (
				effectiveSessionId &&
				!store.activeRunId &&
				!store.isRunningPipeline
			) {
				void store.hydrateSessionLatestRun(fetchImpl, effectiveSessionId);
			}
		}
	}, [activeStep, effectiveSessionId]);

	const goToStep = (nextStep: WizardStepNumber) => {
		if (activeStep === 4 && nextStep > 4) {
			void syncStep4DecisionsToStore(
				studioStore.getState(),
				step4Concepts,
				breakingList,
				fetchImpl,
			);
		}
		setActiveStep(nextStep);
	};

	const triggerLogs = (opts?: { stageId?: string; wizardStep?: number }) => {
		if (onOpenLogs) {
			onOpenLogs(opts);
			return;
		}
		store.openLogsDrawer({
			stageFilter: opts?.stageId ?? null,
			stepFilter: opts?.wizardStep ?? activeStep,
		});
	};

	return (
		<div
			data-testid="ontology-wizard-shell"
			style={{
				display: "flex",
				flexDirection: "column",
				minHeight: "calc(100vh - 54px)",
				background: CLAUDE_TOKENS.colors.canvas,
			}}
		>
			<nav
				className="claude-stepper"
				aria-label="Unified Ontology and Pipeline Wizard Steps"
				style={{
					height: CLAUDE_TOKENS.chrome.stepperHeight,
					padding: "0 20px",
					display: "flex",
					alignItems: "center",
					justifyContent: "space-between",
					borderBottom: `1px solid ${CLAUDE_TOKENS.colors.hairline}`,
					background: CLAUDE_TOKENS.colors.surfaceSoft,
					gap: "8px",
					overflowX: "auto",
				}}
			>
				<div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
					{WIZARD_STEP_LABELS.map((item) => {
						const isCurrent = activeStep === item.step;
						return (
							<button
								key={item.step}
								type="button"
								data-testid={`wizard-stepper-step-${item.step}`}
								className={
									isCurrent ? "claude-btn-primary" : "claude-btn-secondary"
								}
								style={{
									fontSize: "12px",
									padding: "5px 10px",
									whiteSpace: "nowrap",
								}}
								onClick={() => goToStep(item.step)}
							>
								{item.label}
							</button>
						);
					})}
				</div>
				{activeStep >= 2 && (
					<button
						type="button"
						className="claude-btn-secondary"
						data-testid="btn-shell-view-logs"
						style={{
							fontSize: "12px",
							padding: "5px 10px",
							whiteSpace: "nowrap",
						}}
						onClick={() => triggerLogs({ wizardStep: activeStep })}
					>
						View Stage Logs ↗
					</button>
				)}
			</nav>

			<main
				style={{
					flex: 1,
					maxWidth: "1320px",
					width: "100%",
					margin: "0 auto",
					padding: "24px 20px",
				}}
			>
				{activeStep === 1 && (
					<Step1ScopeAndBaseline
						baselineMode={baselineMode}
						newSourceKind={newSourceKind}
						starterPacks={store.starterPacks}
						selectedPackIds={selectedPackIds}
						sessions={store.sessions}
						legacySessionCount={store.legacySessionCount}
						domainNamespace={initialDomainNamespace ?? "wealth_mgmt"}
						activeDigest={
							store.activeSession
								? {
										sessionId: store.activeSession.session_id,
										version: store.activeSession.active_version,
										sha256: store.activeSession.head_sha256 || "",
										label: store.activeSession.domain_namespace,
									}
								: null
						}
						onModeChange={setBaselineMode}
						onSourceKindChange={setNewSourceKind}
						onTogglePack={(pid) =>
							setSelectedPackIds(
								selectedPackIds.includes(pid)
									? selectedPackIds.filter((x) => x !== pid)
									: [...selectedPackIds, pid],
							)
						}
						onCreateSession={async ({
							domainNamespace,
							fromWorkspace,
							packIds,
						}) => {
							const created = await store.createSession(
								{
									domain_namespace: domainNamespace,
									...(fromWorkspace ? { from_workspace: "workspace" } : {}),
								},
								fetchImpl,
							);
							if (created && packIds.length > 0) {
								await store.applyStarterPacks(packIds, fetchImpl);
							}
							if (created) setActiveStep((prev) => (prev === 1 ? 2 : prev));
						}}
						onImportOntology={async ({ format, content, domainNamespace }) => {
							const imported = await store.importOntologySession(
								{ format, content, domain_namespace: domainNamespace },
								fetchImpl,
							);
							if (imported) setActiveStep((prev) => (prev === 1 ? 2 : prev));
						}}
						onSelectSession={async (sid) => {
							// Advance Step 1 -> 2 before async hydration so jumping to Step 6 is not overwritten
							setActiveStep((prev) => (prev === 1 ? 2 : prev));
							await store.selectSession(sid, fetchImpl);
						}}
						onContinue={() => setActiveStep(2)}
					/>
				)}

				{activeStep === 2 && (
					<Step2SourcesAndNotes
						sessionId={effectiveSessionId}
						apiUrl={apiUrl}
						fetchImpl={fetchImpl}
						ocrMode={ocrMode}
						doNotSendToAgent={doNotSend}
						attachedSources={store.sources}
						activeSessionJobId={store.activeSessionJobId}
						onOcrModeChange={setOcrMode}
						onDoNotSendChange={setDoNotSend}
						onAttachConnector={async (req) => {
							await store.attachConnectorSource(req, fetchImpl);
						}}
						onUploadFile={async (file, opts) => {
							await store.uploadSource(
								file,
								opts.doNotSend,
								fetchImpl,
								opts.ocrMode,
							);
						}}
						onUploadStructuredFile={async (file, opts) => {
							const uploaded = await store.uploadSource(
								file,
								opts.doNotSend,
								fetchImpl,
								"off",
							);
							if (uploaded?.source_sha256) {
								await store.attachConnectorSource(
									{
										adapter: "file",
										cas_hash: uploaded.source_sha256,
										source_name: file.name,
									},
									fetchImpl,
								);
							}
						}}
						onAddInlineNote={async (text, opts) => {
							await store.addInlineNote(
								text,
								"domain-note.md",
								opts.doNotSend,
								fetchImpl,
							);
						}}
						onParseSources={async (mode) => {
							await store.startParse(mode, fetchImpl);
						}}
						onCancelJob={async () => {
							await store.cancelActiveJob(fetchImpl);
						}}
						onOpenLogs={triggerLogs}
					/>
				)}

				{activeStep === 3 && (
					<Step3PreviewEvidence
						sessionId={effectiveSessionId}
						tables={store.evidenceTables}
						chunks={store.evidenceChunks}
						observedTermCounts={store.observedTermCounts}
						highlightChunkId={highlightChunkId}
						onFastForwardToStep6={() => goToStep(6)}
					/>
				)}

				{activeStep === 4 && (
					<Step4AgentDiscovery
						sessionId={effectiveSessionId}
						platformConfig={platformConfig}
						agentErrorCode={store.agentErrorCode}
						agentErrorDetail={store.agentErrorDetail}
						concepts={step4Concepts}
						breakingCandidates={breakingList}
						clarifications={store.clarifications}
						activeSessionJobId={store.activeSessionJobId}
						isRunningPipeline={store.isRunningPipeline}
						budgetHold={store.budgetHold}
						askFiboAnswer={store.fiboQaResult?.answer ?? null}
						askFiboCitations={store.fiboQaResult?.citations ?? []}
						askFiboActions={mapAskFiboActions(
							store.fiboQaResult?.suggested_actions,
						)}
						onPlatformConfigUpdated={(cfg, ready) => {
							if (ready) store.clearAgentError();
							onPlatformConfigUpdated?.(cfg, ready);
						}}
						onRunDiscovery={async (steeringPrompt) => {
							await store.startConstruct(
								{ mode: "agent", steering_prompt: steeringPrompt },
								fetchImpl,
							);
						}}
						onCancelJob={async () => {
							await store.cancelActiveJob(fetchImpl);
						}}
						onRaiseBudgetAndResume={async () => {
							await store.raiseBudgetAndResume(5, fetchImpl);
						}}
						onAbortBudgetHold={async () => {
							await store.abortBudgetHold(fetchImpl);
						}}
						onSubmitClarification={(ans) =>
							store.submitAnswer(ans, undefined, fetchImpl)
						}
						onToggleConcept={(cid, checked) =>
							setConceptOverrides((prev) => ({
								...prev,
								[cid]: { ...prev[cid], checked },
							}))
						}
						onUpdateAttrType={(cid, aid, dt) =>
							setConceptOverrides((prev) => ({
								...prev,
								[cid]: {
									...prev[cid],
									attrTypes: { ...prev[cid]?.attrTypes, [aid]: dt },
								},
							}))
						}
						onToggleBreaking={(cid, checked) =>
							setBreakingOverrides((prev) => ({ ...prev, [cid]: checked }))
						}
						onInspectCitations={async (refs) => {
							setCitationDrawerOpen(true);
							await store.resolveCitations(refs, fetchImpl);
						}}
						onAskFibo={async (question) => {
							await store.askFiboQa(question, undefined, fetchImpl);
						}}
						onApplyAskFiboAction={(act) =>
							applyAskFiboActionToStore(studioStore.getState(), act)
						}
						onOpenLogs={triggerLogs}
					/>
				)}

				{activeStep === 5 && (
					<Step5DiffGraphExport
						sessionId={effectiveSessionId}
						diffSummary={computeStep5DiffSummary(
							store.nodes,
							store.edges,
							store.diffState.summary.modified_count,
							store.diffState.summary.breaking_count,
						)}
						isVisualEditorOpen={visualEditorOpen}
						hasUnsavedChanges={store.hasUnsavedChanges}
						onOpenVisualEditor={() => setVisualEditorOpen(true)}
						onCloseVisualEditor={() => setVisualEditorOpen(false)}
						onExportOnly={async (fmt) => {
							const text = await store.exportOntology(
								fmt,
								undefined,
								fetchImpl,
							);
							const fileName = `ontology-${effectiveSessionId ?? "export"}.${fmt}`;
							triggerTextFileDownload(fileName, text);
							store.addToast({
								type: "urn:opendv:notice:export-complete",
								title: "Ontology Exported",
								status: 200,
								detail: `Downloaded ${fileName}`,
								code: "OK",
							});
						}}
						onCommit={async (rationale) => {
							const ok = await store.commitChanges(rationale, fetchImpl);
							if (ok) goToStep(6);
						}}
						onOpenLogs={triggerLogs}
					/>
				)}

				{activeStep === 6 && (
					<Step6PipelineBuildAndRun
						sessionId={effectiveSessionId}
						committedVersion={store.activeSession?.active_version ?? "v1.0.0"}
						committedSha256={store.activeSession?.head_sha256 ?? ""}
						selectedDialects={store.selectedDialects}
						baselineOptions={store.baselineOptions}
						selectedBaselineRunId={store.selectedBaselineRunId}
						topologyRows={store.topologyRows}
						stageStatuses={store.stageStatuses}
						zoneSqlByZone={store.zoneSqlByZone}
						zoneSqlByDialect={store.zoneSqlByDialect}
						gateScorecard={store.gateScorecard}
						sessionRuns={store.sessionRuns}
						activeRunId={store.activeRunId}
						activeSessionJobId={store.activeSessionJobId}
						isRunningPipeline={store.isRunningPipeline}
						activeHold={store.activeHold}
						budgetHold={store.budgetHold}
						driftColumns={store.driftColumns}
						onToggleDialect={(d) =>
							store.setSelectedDialects(
								store.selectedDialects.includes(d)
									? store.selectedDialects.filter((x) => x !== d)
									: [...store.selectedDialects, d],
							)
						}
						onSelectBaseline={(rid) => store.setSelectedBaselineRunId(rid)}
						onSelectViewedRun={(rid) => store.selectViewedRun(rid, fetchImpl)}
						onUploadBaselineContract={(yaml) =>
							store.setSelectedBaselineRunId("__uploaded__", yaml)
						}
						onStartPipelineRun={async () => {
							const rid = await store.startDvPipelineRun(undefined, fetchImpl);
							if (rid) {
								onRunStarted?.({
									run_id: rid,
									status: "RUNNING",
									created_at_utc: new Date().toISOString(),
									event_stream_url: `/api/v1/runs/${rid}/events`,
									has_initial_contract: false,
									status_url: `/api/v1/runs/${rid}`,
									lifecycle_stage: "COLD_START_N1",
								});
							}
						}}
						onCancelJob={async () => {
							if (store.activeSessionJobId) {
								await store.cancelActiveJob(fetchImpl);
							} else if (store.activeRunId) {
								await store.abortPipelineRun(fetchImpl);
							}
						}}
						onResolveHold={async (res) =>
							store.resolveActiveHold(res, fetchImpl)
						}
						onCloseHold={() => store.clearActiveHold()}
						onRaiseBudgetAndResume={async () => {
							await store.raiseBudgetAndResume(5, fetchImpl);
						}}
						onAbortBudgetHold={() => void store.abortBudgetHold(fetchImpl)}
						onSyncDriftToStep4={async () => {
							goToStep(4);
							await store.syncDriftToStep4(fetchImpl);
						}}
						onExploreDvSchema={onExploreDvSchema}
						onOpenLogs={triggerLogs}
					/>
				)}
			</main>

			<footer
				className="claude-footer"
				style={{
					height: CLAUDE_TOKENS.chrome.footerHeight,
					padding: "0 20px",
					borderTop: `1px solid ${CLAUDE_TOKENS.colors.hairline}`,
					background: CLAUDE_TOKENS.colors.surfaceSoft,
					display: "flex",
					alignItems: "center",
					justifyContent: "space-between",
				}}
			>
				<button
					type="button"
					className="claude-btn-secondary"
					disabled={activeStep <= 1}
					onClick={() =>
						goToStep(Math.max(1, activeStep - 1) as WizardStepNumber)
					}
				>
					← Previous Step
				</button>
				<button
					type="button"
					className="claude-btn-primary"
					disabled={activeStep >= 6}
					onClick={() =>
						goToStep(Math.min(6, activeStep + 1) as WizardStepNumber)
					}
				>
					Next Step →
				</button>
			</footer>

			<CitationInspectorDrawer
				isOpen={citationDrawerOpen}
				sessionId={effectiveSessionId}
				resolvedCitations={store.resolvedCitations}
				onJumpToStep3={(target) => {
					setHighlightChunkId(target?.chunkId ?? null);
					setCitationDrawerOpen(false);
					goToStep(3);
				}}
				onClose={() => setCitationDrawerOpen(false)}
			/>

			{!onOpenLogs && (
				<SessionLogsInspector
					isOpen={store.isLogsDrawerOpen}
					sessionId={effectiveSessionId}
					apiUrl={apiUrl}
					fetchImpl={fetchImpl}
					initialStageFilter={store.logsStageFilter}
					initialStepFilter={store.logsStepFilter}
					onClose={() => store.closeLogsDrawer()}
				/>
			)}
		</div>
	);
};
