/**
 * Top-Level 4-Tab Workbench Shell (`OpenDVWorkbenchExplorer.tsx`)
 * (`Ontology & Pipeline | DV Schema Explorer | Run Explorer | Settings`).
 *
 * Adheres to Claude `DESIGN.md` tokens, zero-demo-data fallback, and `<5,000` token budget.
 */

import type {
	HoldResolutionParams,
	PipelineRunHandle,
	PlatformConfigResponse,
	ProjectRunDTO,
} from "@opendv/protocol-ts";
import { Suspense, lazy } from "react";
import type { StoreApi, UseBoundStore } from "zustand";
import { ArtifactFullModal } from "./ArtifactFullModal";
import { BlockingErrorBanner } from "./BlockingErrorBanner";
import { DvSchemaExplorerShell } from "./DvSchemaExplorerShell";
import { ExplorerTopBar } from "./ExplorerTopBar";
import { NewRunModal } from "./NewRunModal";
import { PipelineOverviewView } from "./PipelineOverviewView";
import { StageDrillDownView } from "./StageDrillDownView";
import { WizardShell } from "./builder/WizardShell";
import {
	type SessionCostLogEntry,
	aggregateSessionCostFromLogs,
} from "./cost-meter-utils";
import type { ExplorerDataSource } from "./data-source";
import {
	CLAUDE_TOKENS,
	EXPLORER_COLORS,
	EXPLORER_FONTS,
} from "./design-tokens";
import { type ExplorerStoreState, useExplorerStore } from "./explorer-store";
import { safeUseEffect, safeUseState } from "./modals/safeHooks";
import { PipelineRunProgressBar } from "./progress/PipelineRunProgressBar";
import { PlatformSettingsView } from "./settings/PlatformSettingsView";
import { fetchPlatformConfig } from "./settings/usePlatformConfigActions";

const LazyOntologyStudioApp = lazy(() =>
	import("../ontology/OntologyStudioApp").then((mod) => ({
		default: mod.OntologyStudioApp,
	})),
);

export { aggregateSessionCostFromLogs, type SessionCostLogEntry };

export interface OpenDVWorkbenchExplorerProps {
	store?: UseBoundStore<StoreApi<ExplorerStoreState>> | undefined;
	fetchImpl?: typeof fetch | undefined;
	dataSource?: ExplorerDataSource | undefined;
	apiUrl?: string | undefined;
	onRunStarted?: ((handle: PipelineRunHandle) => void) | undefined;
}

export function OpenDVWorkbenchExplorer({
	store = useExplorerStore,
	fetchImpl,
	dataSource,
	apiUrl,
	onRunStarted,
}: OpenDVWorkbenchExplorerProps = {}) {
	const bundle = store((s) => s.bundle);
	const view = store((s) => s.view);
	const ontologySubView = store((s) => s.ontologySubView);
	const stageIdx = store((s) => s.stageIdx);
	const selType = store((s) => s.selType);
	const selId = store((s) => s.selId);
	const fullId = store((s) => s.fullId);
	const fullTab = store((s) => s.fullTab);
	const activeWave = store((s) => s.activeWave);
	const runId = store((s) => s.runId);
	const compareRunId = store((s) => s.compareRunId);
	const sessionId = store((s) => s.sessionId);
	const runState = store((s) => s.runState);
	const activeRunningStageIdx = store((s) => s.activeRunningStageIdx);
	const statusBannerText = store((s) => s.statusBannerText);
	const blockingError = store((s) => s.blockingError);

	const goOntology = store((s) => s.goOntology);
	const goDvSchema = store((s) => s.goDvSchema);
	const goPipeline = store((s) => s.goPipeline);
	const goBuilder = store((s) => s.goBuilder);
	const goSettings = store((s) => s.goSettings);
	const openStage = store((s) => s.openStage);
	const prevStage = store((s) => s.prevStage);
	const nextStage = store((s) => s.nextStage);
	const selectArtifact = store((s) => s.selectArtifact);
	const selectStep = store((s) => s.selectStep);
	const selectComponent = store((s) => s.selectComponent);
	const clearSelection = store((s) => s.clearSelection);
	const openFullArtifact = store((s) => s.openFullArtifact);
	const closeFullArtifact = store((s) => s.closeFullArtifact);
	const setFullTab = store((s) => s.setFullTab);
	const setWaveFilter = store((s) => s.setWaveFilter);
	const syncFromUrl = store((s) => s.syncFromUrl);
	const resolveStewardHold = store((s) => s.resolveStewardHold);
	const builderSessionId = store((s) => s.builderSessionId);
	const builderSource = store((s) => s.builderSource);
	const builderDomain = store((s) => s.builderDomain);
	const builderPipelineId = store((s) => s.builderPipelineId);

	const [isRunSelectorOpen, setIsRunSelectorOpen] = safeUseState(false);
	const [runsList, setRunsList] = safeUseState<readonly ProjectRunDTO[]>([]);
	const [isLoadingRuns, setIsLoadingRuns] = safeUseState(false);
	const [streamedTokens, setStreamedTokens] = safeUseState<number>(0);
	const [isNewRunModalOpen, setIsNewRunModalOpen] = safeUseState(false);
	const [platformConfig, setPlatformConfig] =
		safeUseState<PlatformConfigResponse | null>(null);
	const [sessionLogsCount, setSessionLogsCount] = safeUseState(0);
	const [sessionCostTokens, setSessionCostTokens] = safeUseState(0);
	const [sessionCostUsd, setSessionCostUsd] = safeUseState(0);

	// Sync initial URL and browser popstate
	safeUseEffect(() => {
		if (
			typeof globalThis !== "undefined" &&
			"location" in globalThis &&
			globalThis.location
		) {
			const loc = globalThis.location;
			syncFromUrl(loc.pathname, loc.search);
		}
		if (
			typeof globalThis !== "undefined" &&
			typeof globalThis.addEventListener === "function"
		) {
			const onPopState = () => {
				if (globalThis.location) {
					syncFromUrl(globalThis.location.pathname, globalThis.location.search);
				}
			};
			globalThis.addEventListener("popstate", onPopState);
			return () => globalThis.removeEventListener("popstate", onPopState);
		}
		return;
	}, [syncFromUrl]);

	// Hydrate live platform config (Agent Readiness + Remote Unauthenticated Banner)
	safeUseEffect(() => {
		let cancelled = false;
		void fetchPlatformConfig({ apiUrl, fetchImpl })
			.then((cfg) => {
				if (!cancelled) setPlatformConfig(cfg);
			})
			.catch(() => {
				// Graceful degradation when offline in SSR/unit tests
			});
		return () => {
			cancelled = true;
		};
	}, [apiUrl, fetchImpl]);

	// Hydrate session logs & cost meter when sessionId is present
	safeUseEffect(() => {
		const activeSession = sessionId || builderSessionId;
		if (!activeSession) return;
		const fetchFn =
			fetchImpl ??
			(typeof globalThis.fetch === "function"
				? globalThis.fetch.bind(globalThis)
				: undefined);
		if (!fetchFn) return;
		let cancelled = false;
		const base = (apiUrl ?? "")
			.replace(/\/api\/v1\/?$/, "")
			.replace(/\/+$/, "");
		void fetchFn(
			`${base}/api/v1/ontology/studio/sessions/${encodeURIComponent(activeSession)}/logs`,
			{ headers: { Accept: "application/json" } },
		)
			.then(async (resp) => {
				if (!resp.ok || cancelled) return;
				const body = (await resp.json()) as {
					total?: number;
					entries?: SessionCostLogEntry[];
				};
				if (cancelled) return;
				const entries = body.entries ?? [];
				const { totalTokens, totalUsd } = aggregateSessionCostFromLogs(entries);
				setSessionLogsCount(body.total ?? entries.length);
				setSessionCostTokens(totalTokens);
				setSessionCostUsd(totalUsd);
			})
			.catch(() => {});
		return () => {
			cancelled = true;
		};
	}, [sessionId, builderSessionId, apiUrl, fetchImpl]);

	// Connect to data source or load initial runs list on Run Explorer / DV Schema routes
	safeUseEffect(() => {
		const needsRuns =
			view === "pipeline" || view === "stage" || view === "dv_schema";
		if (dataSource) {
			void store.getState().setDataSource(dataSource);
		} else if (needsRuns && !bundle.runId && !store.getState().blockingError) {
			void store
				.getState()
				.loadRunsList()
				.then((runs) => {
					const preferredRunId = store.getState().runId;
					const targetRunId =
						preferredRunId && runs.some((r) => r.run_id === preferredRunId)
							? preferredRunId
							: runs[0]?.run_id;
					if (targetRunId) {
						void store.getState().loadRun(targetRunId);
					}
				});
		}
	}, [dataSource, store, bundle.runId, view]);

	// Escape key handler
	safeUseEffect(() => {
		if (
			typeof globalThis === "undefined" ||
			typeof globalThis.addEventListener !== "function"
		) {
			return;
		}
		const onKeyDown = (ev: KeyboardEvent) => {
			if (ev.key !== "Escape") return;
			if (isRunSelectorOpen) {
				ev.preventDefault();
				setIsRunSelectorOpen(false);
			} else if (isNewRunModalOpen) {
				ev.preventDefault();
				setIsNewRunModalOpen(false);
			} else if (store.getState().fullId) {
				ev.preventDefault();
				closeFullArtifact();
			} else if (store.getState().selType !== null) {
				ev.preventDefault();
				clearSelection();
			}
		};
		globalThis.addEventListener("keydown", onKeyDown);
		return () => globalThis.removeEventListener("keydown", onKeyDown);
	}, [
		store,
		isRunSelectorOpen,
		isNewRunModalOpen,
		closeFullArtifact,
		clearSelection,
	]);

	const currentStage = bundle.stages[stageIdx] ?? bundle.stages[0];
	const stageShortCode = currentStage?.key.slice(0, 8) ?? "STAGE_00";
	const fullArtifact = fullId ? bundle.artifacts[fullId] : undefined;

	const handleResolveHold = (params: HoldResolutionParams) => {
		void resolveStewardHold({
			...params,
			...(fetchImpl !== undefined ? { fetchImpl } : {}),
		});
	};

	const handleToggleRunSelector = async () => {
		const nextOpen = !isRunSelectorOpen;
		setIsRunSelectorOpen(nextOpen);
		if (nextOpen) {
			setIsLoadingRuns(true);
			try {
				const runs = dataSource
					? await dataSource.fetchRunsList()
					: await store.getState().loadRunsList();
				setRunsList(runs);
			} finally {
				setIsLoadingRuns(false);
			}
		}
	};

	const handleSelectRun = async (run: ProjectRunDTO) => {
		setIsRunSelectorOpen(false);
		if (dataSource) {
			try {
				const nextBundle = await dataSource.fetchExplorerBundle(
					run.run_id,
					activeWave !== "all" ? activeWave : undefined,
				);
				store.setState({
					bundle: nextBundle,
					activeRunId: run.run_id,
					runId: run.run_id,
					runState:
						run.status === "FAILED"
							? "failed"
							: run.status === "RUNNING"
								? "running"
								: "sealed",
					statusBannerText: nextBundle.statusLabel,
				});
				store.getState().syncUrl();
			} catch (err) {
				store.setState({
					blockingError: {
						hasError: true,
						title: "Error Loading Run Bundle",
						endpoint: `/api/v1/runs/${run.run_id}/explorer`,
						details: err instanceof Error ? err.message : String(err),
						retryAction: () => void handleSelectRun(run),
						openFileAction: (file: File) => {
							void store.getState().loadFromFile(file);
						},
					},
				});
			}
		} else {
			await store
				.getState()
				.loadRun(run.run_id, activeWave !== "all" ? activeWave : undefined);
		}
	};

	const handleRunStarted = (handle: PipelineRunHandle) => {
		setIsNewRunModalOpen(false);
		setStreamedTokens(0);
		onRunStarted?.(handle);
		if (handle.run_id) {
			void store.getState().loadRun(handle.run_id);
		}
	};

	const displayedStages = bundle.stages;
	const statusDotColor =
		runState === "running" || runState === "hold"
			? CLAUDE_TOKENS.colors.warning
			: CLAUDE_TOKENS.colors.success;

	const showBlockingRunError =
		Boolean(blockingError?.hasError) &&
		(view === "pipeline" || view === "stage" || view === "dv_schema");

	const isStudioView = view === "ontology" && ontologySubView === "studio";

	return (
		<main
			className="opendv-workbench-explorer"
			data-testid="workbench-explorer-root"
			style={{
				display: "flex",
				flexDirection: "column",
				...(isStudioView ? { height: "100vh" } : {}),
				minHeight: "100vh",
				maxWidth: "100%",
				backgroundColor: EXPLORER_COLORS.bgPage,
				color: EXPLORER_COLORS.inkPrimary,
				fontFamily: EXPLORER_FONTS.sans,
				fontSize: "13px",
				lineHeight: 1.45,
			}}
		>
			<ExplorerTopBar
				bundle={bundle}
				view={view}
				currentStage={currentStage}
				stageShortCode={stageShortCode}
				activeWave={activeWave}
				runState={runState}
				statusBannerText={statusBannerText}
				statusDotColor={statusDotColor}
				streamedTokens={streamedTokens}
				isRunSelectorOpen={isRunSelectorOpen}
				isLoadingRuns={isLoadingRuns}
				runsList={runsList}
				platformConfig={platformConfig}
				sessionCostTokens={sessionCostTokens}
				sessionCostUsd={sessionCostUsd}
				sessionLogsCount={sessionLogsCount}
				sessionDigest={sessionId || builderSessionId || undefined}
				onToggleRunSelector={handleToggleRunSelector}
				onSelectRun={handleSelectRun}
				onOpenNewRunModal={() => setIsNewRunModalOpen(true)}
				onGoOntology={() => goOntology("studio")}
				onGoDvSchema={goDvSchema}
				onGoPipeline={goPipeline}
				onGoBuilder={goBuilder}
				onGoSettings={goSettings}
				onOpenLogs={() => goOntology("studio")}
				onSetWaveFilter={setWaveFilter}
			/>

			{showBlockingRunError && blockingError ? (
				<BlockingErrorBanner error={blockingError} />
			) : (
				<div
					data-testid="explorer-main-workspace"
					inert={fullArtifact ? true : undefined}
					style={
						isStudioView
							? {
									flex: 1,
									minHeight: 0,
									display: "flex",
									flexDirection: "column",
								}
							: undefined
					}
				>
					{(view === "pipeline" || view === "stage") && (
						<div style={{ padding: "12px 24px 0" }}>
							<PipelineRunProgressBar
								stages={displayedStages}
								runState={runState}
								activeRunningStageIdx={activeRunningStageIdx}
								statusBannerText={statusBannerText}
								streamedTokens={streamedTokens}
								onSelectStage={openStage}
							/>
						</div>
					)}

					{isStudioView ? (
						<div
							data-testid="ontology-studio-tab-host"
							style={{ flex: 1, minHeight: 0, overflow: "hidden" }}
						>
							<Suspense
								fallback={
									<div className="studio-loading" style={{ padding: "24px" }}>
										Loading Ontology Studio...
									</div>
								}
							>
								<LazyOntologyStudioApp />
							</Suspense>
						</div>
					) : view === "ontology" || view === "builder" ? (
						<WizardShell
							sessionId={builderSessionId ?? undefined}
							source={builderSource ?? undefined}
							initialDomainNamespace={builderDomain ?? undefined}
							initialPipelineId={builderPipelineId ?? undefined}
							onRunStarted={handleRunStarted}
							onBackToExplorer={goPipeline}
							fetchImpl={fetchImpl}
							apiUrl={apiUrl}
						/>
					) : view === "dv_schema" ? (
						<DvSchemaExplorerShell
							runId={runId ?? (bundle.runId || null)}
							compareRunId={compareRunId}
							sessionId={sessionId ?? builderSessionId}
							onGoPipeline={goPipeline}
							onGoOntology={() => goOntology("studio")}
						/>
					) : view === "settings" ? (
						<PlatformSettingsView
							apiUrl={apiUrl}
							fetchImpl={fetchImpl}
							onConfigSaved={(saved) => setPlatformConfig(saved)}
							onBackToExplorer={goPipeline}
						/>
					) : view === "pipeline" ? (
						<PipelineOverviewView
							settingIds={bundle.settingIds}
							inputIds={bundle.inputIds}
							outputIds={bundle.outputIds}
							artifacts={bundle.artifacts}
							phases={bundle.phases}
							stages={displayedStages}
							hasInitialContract={bundle.hasInitialContract}
							activeWave={activeWave}
							selType={selType}
							selId={selId}
							onSelectArtifact={selectArtifact}
							onOpenFullArtifact={openFullArtifact}
							onOpenStage={openStage}
							onClearSelection={clearSelection}
							onResolveHold={handleResolveHold}
							runState={runState}
							activeRunningStageIdx={activeRunningStageIdx}
						/>
					) : (
						<StageDrillDownView
							stageIdx={stageIdx}
							stages={displayedStages}
							artifacts={bundle.artifacts}
							selType={selType}
							selId={selId}
							onGoPipeline={goPipeline}
							onPrevStage={prevStage}
							onNextStage={nextStage}
							onSelectArtifact={selectArtifact}
							onSelectStep={selectStep}
							onSelectComponent={selectComponent}
							onClearSelection={clearSelection}
							onOpenFullArtifact={openFullArtifact}
							onResolveHold={handleResolveHold}
						/>
					)}
				</div>
			)}

			{fullArtifact && (
				<ArtifactFullModal
					artifact={fullArtifact}
					activeTab={fullTab}
					rbacRole={bundle.rbacRole}
					onClose={closeFullArtifact}
					onSelectTab={setFullTab}
					onResolveHold={handleResolveHold}
				/>
			)}

			<NewRunModal
				isOpen={isNewRunModalOpen}
				onClose={() => setIsNewRunModalOpen(false)}
				onRunStarted={handleRunStarted}
				fetchImpl={fetchImpl}
				apiUrl={apiUrl}
				defaultWaveId={activeWave !== "all" ? activeWave : ""}
				onOpenBuilderView={() => {
					setIsNewRunModalOpen(false);
					goBuilder();
				}}
				createRunFn={dataSource?.createPipelineRun?.bind(dataSource)}
				validatePipelineFn={dataSource?.validatePipeline?.bind(dataSource)}
			/>
		</main>
	);
}
