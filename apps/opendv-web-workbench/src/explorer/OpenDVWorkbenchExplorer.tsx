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
import { useEffect, useState } from "react";
import type { StoreApi, UseBoundStore } from "zustand";
import { ArtifactFullModal } from "./ArtifactFullModal";
import { BlockingErrorBanner } from "./BlockingErrorBanner";
import { ExplorerTopBar } from "./ExplorerTopBar";
import { NewRunModal, type PipelineValidationReport } from "./NewRunModal";
import { PipelineOverviewView } from "./PipelineOverviewView";
import { StageDrillDownView } from "./StageDrillDownView";
import { EXPLORER_COLORS, EXPLORER_FONTS } from "./design-tokens";
import {
	type ExplorerStoreState,
	hasActiveReactDispatcher,
	useExplorerStore,
} from "./explorer-store";

export interface ExplorerDataSource {
	readonly mode?: ("live" | "file") | undefined;
	readonly sourceName?: string | undefined;
	fetchRunsList(): Promise<ProjectRunDTO[]>;
	fetchExplorerBundle(
		runId: string,
		waveId?: string | undefined,
	): Promise<ExplorerBundleDTO>;
	fetchArtifactContent?(runId: string, artifactId: string): Promise<string>;
	createPipelineRun?(request: PipelineRunRequest): Promise<PipelineRunHandle>;
	validatePipeline?(
		request: PipelineValidateRequest,
	): Promise<PipelineValidationReport>;
	subscribeEvents?(
		runId: string,
		onEvent: (event: VaultObservabilityEventDTO) => void,
		onError?: ((err: Error) => void) | undefined,
	): () => void;
}

export interface OpenDVWorkbenchExplorerProps {
	store?: UseBoundStore<StoreApi<ExplorerStoreState>> | undefined;
	fetchImpl?: typeof fetch | undefined;
	dataSource?: ExplorerDataSource | undefined;
	apiUrl?: string | undefined;
	onRunStarted?: ((handle: PipelineRunHandle) => void) | undefined;
}

function safeUseEffect(
	effect: Parameters<typeof useEffect>[0],
	deps?: readonly unknown[] | undefined,
): void {
	if (!hasActiveReactDispatcher()) return;
	try {
		useEffect(effect, deps);
	} catch {
		// Ignore hook errors outside React render tree
	}
}

function safeUseState<T>(
	initialValue: T | (() => T),
): [T, (val: T | ((prev: T) => T)) => void] {
	if (!hasActiveReactDispatcher()) {
		const val =
			typeof initialValue === "function"
				? (initialValue as () => T)()
				: initialValue;
		return [val, () => {}];
	}
	try {
		return useState(initialValue);
	} catch {
		const val =
			typeof initialValue === "function"
				? (initialValue as () => T)()
				: initialValue;
		return [val, () => {}];
	}
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
	const statusBannerText = store((s) => s.statusBannerText);
	const blockingError = store((s) => s.blockingError);

	const goPipeline = store((s) => s.goPipeline);
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
					const fetchFn =
						fetchImpl ??
						(typeof globalThis.fetch === "function"
							? globalThis.fetch.bind(globalThis)
							: undefined);
					const base = (apiUrl ?? "").replace(/\/+$/, "");
					if (fetchFn) {
						const resp = await fetchFn(`${base}/api/v1/runs`);
						if (resp.ok) {
							setRunsList((await resp.json()) as ProjectRunDTO[]);
						} else {
							store.setState({
								blockingError: {
									hasError: true,
									title: "Platform Backend Unreachable",
									endpoint: `${base}/api/v1/runs`,
									status: resp.status,
									details: `HTTP ${resp.status} error fetching runs list`,
									retryAction: () => void handleToggleRunSelector(),
									openFileAction: (file: File) => {
										void store.getState().loadFromFile(file);
									},
								},
							});
						}
					}
				}
			} catch (err) {
				store.setState({
					blockingError: {
						hasError: true,
						title: "Platform Backend Unreachable",
						endpoint: "/api/v1/runs",
						details: err instanceof Error ? err.message : String(err),
						retryAction: () => void handleToggleRunSelector(),
						openFileAction: (file: File) => {
							void store.getState().loadFromFile(file);
						},
					},
				});
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
					runState:
						run.status === "RUNNING"
							? "running"
							: run.status === "PAUSED_HOLD"
								? "hold"
								: "sealed",
					statusBannerText:
						run.status === "RUNNING"
							? `Running · ${run.current_stage_id.slice(0, 8)}`
							: run.status === "PAUSED_HOLD"
								? `Hold · ${run.current_stage_id.slice(0, 8)} awaiting steward`
								: "Sealed · 6/6 gates PASS",
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
						endpoint: `/api/v1/runs/${run.run_id}/bundle`,
						details: err instanceof Error ? err.message : String(err),
						retryAction: () => void handleSelectRun(run),
						openFileAction: (file: File) => {
							void store.getState().loadFromFile(file);
						},
					},
				});
			}
		}
	};

	const handleRunStarted = (handle: PipelineRunHandle) => {
		setIsNewRunModalOpen(false);
		store.setState({
			runState: "running",
			activeRunningStageIdx: 0,
			statusBannerText: "Running · STAGE_00",
		});
		setStreamedTokens(0);
		onRunStarted?.(handle);

		if (dataSource?.subscribeEvents) {
			dataSource.subscribeEvents(handle.run_id, (ev) => {
				store.getState().ingestLiveObservabilityEvent(ev);
				const tokens = Number(
					ev.layer2_visual_payload?.summary_metrics?.tokens_streamed ?? 0,
				);
				if (tokens > 0) setStreamedTokens((prev) => prev + tokens);
			});
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
				onSetWaveFilter={setWaveFilter}
			/>

			{/* Main Content Workspace or Explicit Blocking Error Banner (REQ-MOCK-01, REQ-FAIL-01) */}
			{blockingError !== null && blockingError.hasError ? (
				<BlockingErrorBanner error={blockingError} />
			) : (
				<div
					data-testid="explorer-main-workspace"
					inert={fullArtifact ? true : undefined}
				>
					{view === "pipeline" ? (
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
				createRunFn={dataSource?.createPipelineRun?.bind(dataSource)}
				validatePipelineFn={dataSource?.validatePipeline?.bind(dataSource)}
			/>
		</main>
	);
}
