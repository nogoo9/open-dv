/**
 * Top-Level 3-Level Pipeline Explorer (`OpenDVWorkbenchExplorer.tsx`)
 * (`Pipeline -> Stage -> Artifact`) (`docs/design/open-dv-workbench/HANDOFF.md`).
 *
 * Implements REQ-MOCK-01 & REQ-FAIL-01: Explicit blocking error banner when offline,
 * with zero demo data fallback.
 * Adheres to User Directive R9 (modular decomposition into submodules <= 450 LOC).
 */

import {
	type ExplorerBundleDTO,
	type HoldResolutionParams,
	type PipelineRunHandle,
	type PipelineRunRequest,
	type PipelineValidateRequest,
	type ProjectRunDTO,
	type VaultObservabilityEventDTO,
	filterArtifactIdsByWave,
} from "@opendv/protocol-ts";
import type { StoreApi, UseBoundStore } from "zustand";
import { ArtifactFullModal } from "./ArtifactFullModal";
import { BlockingErrorBanner } from "./BlockingErrorBanner";
import { ExplorerTopBar } from "./ExplorerTopBar";
import { NewRunModal, type PipelineValidationReport } from "./NewRunModal";
import { PipelineOverviewView } from "./PipelineOverviewView";
import { StageDrillDownView } from "./StageDrillDownView";
import { PipelineBuilderView } from "./builder/PipelineBuilderView";
import type { StagedPipelineSpec } from "./builder/specBuilderUtils";
import type { ExplorerDataSource } from "./data-source";
import { EXPLORER_COLORS, EXPLORER_FONTS } from "./design-tokens";
import { type ExplorerStoreState, useExplorerStore } from "./explorer-store";
import { safeUseEffect, safeUseState } from "./modals/safeHooks";
import { PipelineRunProgressBar } from "./progress/PipelineRunProgressBar";
import { PlatformSettingsView } from "./settings/PlatformSettingsView";

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
	const stageIdx = store((s) => s.stageIdx);
	const selType = store((s) => s.selType);
	const selId = store((s) => s.selId);
	const fullId = store((s) => s.fullId);
	const fullTab = store((s) => s.fullTab);
	const activeWave = store((s) => s.activeWave);
	const runState = store((s) => s.runState);
	const activeRunningStageIdx = store((s) => s.activeRunningStageIdx);
	const statusBannerText = store((s) => s.statusBannerText);
	const blockingError = store((s) => s.blockingError);

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

	// Local UI State for Task P9.3
	const [isRunSelectorOpen, setIsRunSelectorOpen] = safeUseState(false);
	const [runsList, setRunsList] = safeUseState<readonly ProjectRunDTO[]>([]);
	const [isLoadingRuns, setIsLoadingRuns] = safeUseState(false);
	const [streamedTokens, setStreamedTokens] = safeUseState<number>(0);
	const [isNewRunModalOpen, setIsNewRunModalOpen] = safeUseState(false);
	const [stagedSpec, setStagedSpec] = safeUseState<StagedPipelineSpec | null>(
		null,
	);

	// Sync initial URL and browser popstate
	safeUseEffect(() => {
		if (
			typeof globalThis !== "undefined" &&
			"location" in globalThis &&
			globalThis.location
		) {
			const loc = globalThis.location;
			if (loc.pathname !== "/" || loc.search !== "")
				syncFromUrl(loc.pathname, loc.search);
		}
		if (
			typeof globalThis !== "undefined" &&
			typeof globalThis.addEventListener === "function"
		) {
			const onPopState = () => {
				if (globalThis.location)
					syncFromUrl(globalThis.location.pathname, globalThis.location.search);
			};
			globalThis.addEventListener("popstate", onPopState);
			return () => globalThis.removeEventListener("popstate", onPopState);
		}
		return;
	}, [syncFromUrl]);

	// Connect to data source or load initial runs list if empty on startup (B1)
	safeUseEffect(() => {
		if (dataSource) {
			void store.getState().setDataSource(dataSource);
		} else if (!bundle.runId && !store.getState().blockingError) {
			void store
				.getState()
				.loadRunsList()
				.then((runs) => {
					if (runs.length > 0 && runs[0]?.run_id) {
						void store.getState().loadRun(runs[0].run_id);
					}
				});
		}
	}, [dataSource, store, bundle.runId]);

	// Escape key handler: close dropdown or modal or clear selection
	safeUseEffect(() => {
		if (
			typeof globalThis === "undefined" ||
			typeof globalThis.addEventListener !== "function"
		)
			return;
		const onKeyDown = (ev: KeyboardEvent) => {
			if (ev.key === "Escape") {
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
				if (dataSource) {
					const runs = await dataSource.fetchRunsList();
					setRunsList(runs);
				} else {
					const runs = await store.getState().loadRunsList();
					setRunsList(runs);
				}
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
				const hasActiveHold =
					nextBundle.stages.some(
						(s) => s.tag === "ASK-USER" || (s.tag as string) === "HOLD",
					) ||
					Object.values(nextBundle.artifacts).some((a) =>
						a.doc?.extractions?.some(
							(e) => e.tier === "Tier-U3" && !e.resolved,
						),
					);
				const hasFailure =
					run.status === "FAILED" ||
					nextBundle.stages.some((s) => s.tag === "REJECT-WITH-TRACE");
				const runState = hasActiveHold
					? "hold"
					: hasFailure
						? "failed"
						: run.status === "RUNNING"
							? "running"
							: "sealed";
				const statusBannerText = hasActiveHold
					? `Hold · ${run.current_stage_id.slice(0, 8)} awaiting steward`
					: hasFailure
						? `Failed · ${run.current_stage_id.slice(0, 8)}`
						: run.status === "RUNNING"
							? `Running · ${run.current_stage_id.slice(0, 8)}`
							: nextBundle.statusLabel;

				store.setState({
					bundle: nextBundle,
					activeRunId: run.run_id,
					runState,
					statusBannerText,
				});
				if (run.status === "RUNNING" && dataSource.subscribeEvents) {
					dataSource.subscribeEvents(run.run_id, (ev) => {
						store.getState().ingestLiveObservabilityEvent(ev);
						const tokens = Number(
							ev.layer2_visual_payload?.summary_metrics?.tokens_streamed ?? 0,
						);
						if (tokens > 0) setStreamedTokens((prev) => prev + tokens);
					});
				}
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

	// Filter stage artifacts by active wave
	const displayedStages =
		activeWave === "all"
			? bundle.stages
			: bundle.stages.map((st) => ({
					...st,
					ins: filterArtifactIdsByWave(st.ins, bundle.artifacts, activeWave),
					outs: filterArtifactIdsByWave(st.outs, bundle.artifacts, activeWave),
					steps: st.steps.map((sp) => ({
						...sp,
						reads: filterArtifactIdsByWave(
							sp.reads,
							bundle.artifacts,
							activeWave,
						),
						writes: filterArtifactIdsByWave(
							sp.writes,
							bundle.artifacts,
							activeWave,
						),
					})),
				}));

	const statusDotColor =
		runState === "running" || runState === "hold" ? "#B45309" : "#16181D";

	return (
		<main
			className="opendv-workbench-explorer"
			data-testid="workbench-explorer-root"
			style={{
				minHeight: "100vh",
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
				onToggleRunSelector={handleToggleRunSelector}
				onSelectRun={handleSelectRun}
				onOpenNewRunModal={() => setIsNewRunModalOpen(true)}
				onGoPipeline={goPipeline}
				onGoBuilder={goBuilder}
				onGoSettings={goSettings}
				onSetWaveFilter={setWaveFilter}
			/>

			{/* Main Content Workspace or Explicit Blocking Error Banner (REQ-MOCK-01, REQ-FAIL-01) */}
			{blockingError?.hasError ? (
				<BlockingErrorBanner error={blockingError} />
			) : (
				<div
					data-testid="explorer-main-workspace"
					inert={fullArtifact ? true : undefined}
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
					{view === "builder" ? (
						<PipelineBuilderView
							stagedSpec={stagedSpec}
							onStageSpec={setStagedSpec}
							onRunStarted={(handle) => {
								handleRunStarted(handle);
								goPipeline();
							}}
							fetchImpl={fetchImpl}
							apiUrl={apiUrl}
							validatePipelineFn={dataSource?.validatePipeline?.bind(
								dataSource,
							)}
							createRunFn={dataSource?.createPipelineRun?.bind(dataSource)}
						/>
					) : view === "settings" ? (
						<PlatformSettingsView
							apiUrl={apiUrl}
							fetchImpl={fetchImpl}
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

			{/* View 3: Artifact Full Modal */}
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

			{/* New Pipeline Run Modal */}
			<NewRunModal
				isOpen={isNewRunModalOpen}
				onClose={() => setIsNewRunModalOpen(false)}
				onRunStarted={handleRunStarted}
				fetchImpl={fetchImpl}
				apiUrl={apiUrl}
				defaultWaveId={activeWave !== "all" ? activeWave : ""}
				stagedSpec={stagedSpec}
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
