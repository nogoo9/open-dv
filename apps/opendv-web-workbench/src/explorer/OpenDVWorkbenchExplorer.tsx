/**
 * Top-Level 3-Level Pipeline Explorer (`OpenDVWorkbenchExplorer.tsx`)
 * (`Pipeline -> Stage -> Artifact`) (`docs/design/open-dv-workbench/HANDOFF.md`).
 *
 * Task P9.3 (#117):
 * - Top Bar Run Selector pill with dropdown listing recent runs from `dataSource.fetchRunsList()`.
 * - New Pipeline Run Modal (`NewRunModal.tsx`) with pre-flight validation and async HTTP 202 submission.
 * - Live SSE Status Streaming and Token streaming progress updates.
 * - Wave Segmented Filter (`All | W0 | W1 | W2 | W3`) filtering settings, inputs, outputs, and stage artifacts.
 */

import {
	type ExplorerBundleDTO,
	type HoldResolutionParams,
	type PipelineRunHandle,
	type PipelineRunRequest,
	type PipelineValidateRequest,
	type ProjectRunDTO,
	type VaultObservabilityEventDTO,
	type WaveFilterId,
	filterArtifactIdsByWave,
} from "@opendv/protocol-ts";
import { type CSSProperties, useEffect, useState } from "react";
import type { StoreApi, UseBoundStore } from "zustand";
import { ArtifactFullModal } from "./ArtifactFullModal";
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

const WAVE_SEGMENTS: readonly { label: string; id: WaveFilterId }[] = [
	{ label: "All", id: "all" },
	{ label: "W0", id: "W0" },
	{ label: "W1", id: "W1" },
	{ label: "W2", id: "W2" },
	{ label: "W3", id: "W3" },
] as const;

const DROPDOWN_ITEM_STYLE: CSSProperties = {
	all: "unset",
	cursor: "pointer",
	padding: "6px 8px",
	borderRadius: "4px",
	display: "flex",
	flexDirection: "column",
	gap: "2px",
};

export function OpenDVWorkbenchExplorer({
	store = useExplorerStore,
	fetchImpl,
	dataSource,
	apiUrl,
	onRunStarted,
}: OpenDVWorkbenchExplorerProps = {}) {
	const bundle = store((s) => s.bundle);
	const activeBundleKey = store((s) => s.activeBundleKey);
	const view = store((s) => s.view);
	const stageIdx = store((s) => s.stageIdx);
	const selType = store((s) => s.selType);
	const selId = store((s) => s.selId);
	const fullId = store((s) => s.fullId);
	const fullTab = store((s) => s.fullTab);
	const activeWave = store((s) => s.activeWave);
	const runState = store((s) => s.runState);
	const statusBannerText = store((s) => s.statusBannerText);

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
	const switchPresetBundle = store((s) => s.switchPresetBundle);
	const syncFromUrl = store((s) => s.syncFromUrl);
	const triggerPipelineRun = store((s) => s.triggerPipelineRun);
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
						if (resp.ok) setRunsList((await resp.json()) as ProjectRunDTO[]);
					}
				}
			} catch {
				// Keep existing runsList if fetch fails
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
			} catch {
				// Graceful error handling
			}
		} else {
			switchPresetBundle(
				activeBundleKey === "global_commerce_vault"
					? "proj-apex-horizon-pe"
					: "global_commerce_vault",
			);
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
			{/* Global Top Bar (56px) */}
			<header
				data-testid="explorer-top-header"
				style={{
					height: "56px",
					padding: "0 24px",
					background: "#FFFFFF",
					borderBottom: "1px solid #E3E5E8",
					display: "flex",
					alignItems: "center",
					justifyContent: "space-between",
					gap: "16px",
					position: "relative",
				}}
			>
				{/* Left: Wordmark & Breadcrumb */}
				<div style={{ display: "flex", alignItems: "center", gap: "14px" }}>
					<div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
						<span
							style={{
								width: "10px",
								height: "10px",
								background: "#16181D",
								borderRadius: "2px",
								display: "inline-block",
							}}
						/>
						<span
							style={{
								fontFamily: EXPLORER_FONTS.mono,
								fontWeight: 600,
								fontSize: "14px",
								letterSpacing: "-0.01em",
							}}
						>
							open-dv
						</span>
					</div>
					<span style={{ color: "#D4D7DC" }}>|</span>
					<nav
						aria-label="Breadcrumb"
						style={{
							display: "flex",
							alignItems: "center",
							gap: "8px",
							fontSize: "13px",
						}}
					>
						<button
							type="button"
							data-testid="breadcrumb-pipeline"
							onClick={goPipeline}
							style={{
								all: "unset",
								cursor: "pointer",
								fontWeight: 500,
								color: view === "stage" ? "#1D4ED8" : "#16181D",
							}}
						>
							Pipeline
						</button>
						{view === "stage" && currentStage && (
							<div
								data-testid="breadcrumb-stage"
								style={{ display: "flex", alignItems: "center", gap: "8px" }}
							>
								<span style={{ color: "#9CA3AF" }}>/</span>
								<span
									style={{
										fontFamily: EXPLORER_FONTS.mono,
										fontSize: "12.5px",
										fontWeight: 600,
										color: "#16181D",
									}}
								>
									{stageShortCode} · {currentStage.name}
								</span>
							</div>
						)}
					</nav>
				</div>

				{/* Center: Run Selector Pill & Dropdown + Wave Segmented Filter */}
				<div
					style={{
						display: "flex",
						alignItems: "center",
						gap: "10px",
						position: "relative",
					}}
				>
					<div style={{ position: "relative" }}>
						<button
							type="button"
							data-testid="btn-run-selector-pill"
							title="Click to select recent pipeline runs or switch presets"
							onClick={handleToggleRunSelector}
							style={{
								all: "unset",
								cursor: "pointer",
								display: "flex",
								alignItems: "center",
								gap: "8px",
								padding: "5px 10px",
								border: "1px solid #E3E5E8",
								borderRadius: "6px",
								background: isRunSelectorOpen ? "#EEF0F2" : "#FAFBFD",
							}}
						>
							<span
								style={{
									fontSize: "11px",
									color: "#6B7280",
									textTransform: "uppercase",
									letterSpacing: "0.05em",
								}}
							>
								Run
							</span>
							<span
								style={{
									fontFamily: EXPLORER_FONTS.mono,
									fontSize: "12px",
									fontWeight: 500,
									color: "#16181D",
								}}
							>
								{bundle.displayName}
							</span>
							<span
								style={{
									fontFamily: EXPLORER_FONTS.mono,
									fontSize: "11px",
									color: "#6B7280",
								}}
							>
								{bundle.specFile} · {bundle.wavesCount} waves
							</span>
							<span
								data-testid="run-selector-chevron"
								style={{ fontSize: "10px", color: "#6B7280" }}
							>
								{isRunSelectorOpen ? "▲" : "▼"}
							</span>
						</button>

						{/* Run Selector Dropdown Menu */}
						{isRunSelectorOpen && (
							<div
								role="menu"
								data-testid="run-selector-dropdown"
								style={{
									position: "absolute",
									top: "calc(100% + 4px)",
									left: 0,
									zIndex: 1000,
									background: "#FFFFFF",
									border: "1px solid #E3E5E8",
									borderRadius: "8px",
									boxShadow: "0 8px 20px rgba(0,0,0,0.12)",
									minWidth: "320px",
									maxWidth: "420px",
									padding: "6px",
									display: "flex",
									flexDirection: "column",
									gap: "2px",
								}}
							>
								<div
									style={{
										fontSize: "11px",
										fontWeight: 600,
										color: "#6B7280",
										padding: "4px 8px",
										textTransform: "uppercase",
									}}
								>
									Recent Runs
								</div>
								{isLoadingRuns && (
									<div
										data-testid="run-selector-loading"
										style={{
											padding: "8px",
											fontSize: "12px",
											color: "#6B7280",
										}}
									>
										Loading runs...
									</div>
								)}
								{runsList.map((run) => (
									<button
										key={run.run_id}
										type="button"
										role="menuitem"
										data-testid={`run-option-${run.run_id}`}
										onClick={() => void handleSelectRun(run)}
										style={{
											...DROPDOWN_ITEM_STYLE,
											background:
												bundle.runId === run.run_id ? "#F2F3F5" : "transparent",
										}}
									>
										<div
											style={{
												display: "flex",
												alignItems: "center",
												justifyContent: "space-between",
												gap: "8px",
											}}
										>
											<span
												style={{
													fontFamily: EXPLORER_FONTS.mono,
													fontSize: "12px",
													fontWeight: 600,
												}}
											>
												{run.run_id}
											</span>
											<span
												style={{
													fontFamily: EXPLORER_FONTS.mono,
													fontSize: "10.5px",
													padding: "2px 6px",
													borderRadius: "4px",
													background:
														run.status === "RUNNING" ? "#FFF4E5" : "#EEF0F2",
													color:
														run.status === "RUNNING" ? "#8A3B07" : "#16181D",
												}}
											>
												{run.status}
											</span>
										</div>
										<div style={{ fontSize: "11px", color: "#6B7280" }}>
											{run.project_id} {run.wave_id ? `· ${run.wave_id}` : ""}{" "}
											{run.event_count ? `· ${run.event_count} events` : ""}
										</div>
									</button>
								))}
								<div
									style={{ borderTop: "1px solid #E3E5E8", margin: "4px 0" }}
								/>
								<button
									type="button"
									role="menuitem"
									data-testid="run-option-global-commerce"
									onClick={() => {
										switchPresetBundle("global_commerce_vault");
										setIsRunSelectorOpen(false);
									}}
									style={{
										...DROPDOWN_ITEM_STYLE,
										background:
											activeBundleKey === "global_commerce_vault"
												? "#F2F3F5"
												: "transparent",
									}}
								>
									<div style={{ fontWeight: 500, fontSize: "12px" }}>
										Global Commerce Vault
									</div>
									<div style={{ fontSize: "11px", color: "#6B7280" }}>
										pipeline_input.yaml · 4 waves (Preset)
									</div>
								</button>
								<button
									type="button"
									role="menuitem"
									data-testid="run-option-apex-horizon"
									onClick={() => {
										switchPresetBundle("proj-apex-horizon-pe");
										setIsRunSelectorOpen(false);
									}}
									style={{
										...DROPDOWN_ITEM_STYLE,
										background:
											activeBundleKey === "proj-apex-horizon-pe"
												? "#F2F3F5"
												: "transparent",
									}}
								>
									<div style={{ fontWeight: 500, fontSize: "12px" }}>
										Apex Horizon PE Vault
									</div>
									<div style={{ fontSize: "11px", color: "#6B7280" }}>
										pipeline_input.yaml · 2 waves (Preset)
									</div>
								</button>
								<div
									style={{ borderTop: "1px solid #E3E5E8", margin: "4px 0" }}
								/>
								<button
									type="button"
									role="menuitem"
									data-testid="btn-dropdown-new-run"
									onClick={() => {
										setIsRunSelectorOpen(false);
										setIsNewRunModalOpen(true);
									}}
									style={{
										...DROPDOWN_ITEM_STYLE,
										color: "#1D4ED8",
										fontWeight: 500,
										fontSize: "12px",
									}}
								>
									+ New Pipeline Run...
								</button>
							</div>
						)}
					</div>

					{/* Wave Segmented Control */}
					<div
						role="group"
						aria-label="Wave filter"
						data-testid="wave-filter-control"
						style={{
							display: "flex",
							alignItems: "center",
							border: "1px solid #E3E5E8",
							borderRadius: "6px",
							overflow: "hidden",
							fontFamily: EXPLORER_FONTS.mono,
							fontSize: "11.5px",
						}}
					>
						{WAVE_SEGMENTS.map((seg, idx) => {
							const isActive = activeWave === seg.id;
							return (
								<button
									key={seg.id}
									type="button"
									data-testid={`btn-wave-${seg.id}`}
									aria-pressed={isActive}
									onClick={() => setWaveFilter(seg.id)}
									style={{
										all: "unset",
										cursor: "pointer",
										padding: "5px 9px",
										background: isActive ? "#16181D" : "#FFFFFF",
										color: isActive ? "#FFFFFF" : "#545A66",
										borderLeft:
											idx > 0 && !isActive ? "1px solid #E3E5E8" : "none",
									}}
								>
									{seg.label}
								</button>
							);
						})}
					</div>
				</div>

				{/* Right: Live SSE Run Status & Primary Run Pipeline Action */}
				<div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
					{/* Token Streaming Indicator */}
					{streamedTokens > 0 && (
						<span
							data-testid="token-streaming-progress"
							style={{
								display: "inline-flex",
								alignItems: "center",
								gap: "4px",
								fontFamily: EXPLORER_FONTS.mono,
								fontSize: "11px",
								padding: "3px 8px",
								borderRadius: "999px",
								background: "#F2F3F5",
								color: "#3B414C",
							}}
						>
							<span>⚡</span>
							<span>{streamedTokens.toLocaleString()} tokens</span>
						</span>
					)}

					<span
						data-testid="explorer-run-status-pill"
						style={{
							display: "inline-flex",
							alignItems: "center",
							gap: "6px",
							fontFamily: EXPLORER_FONTS.mono,
							fontSize: "11.5px",
							padding: "4px 10px",
							borderRadius: "999px",
							background: runState === "hold" ? "#FFF4E5" : "#EEF0F2",
							color: runState === "hold" ? "#8A3B07" : "#16181D",
						}}
					>
						<span
							style={{
								width: "6px",
								height: "6px",
								borderRadius: "999px",
								background: statusDotColor,
							}}
						/>
						{statusBannerText}
					</span>
					<button
						type="button"
						data-testid="btn-explorer-run-pipeline"
						onClick={() => setIsNewRunModalOpen(true)}
						style={{
							all: "unset",
							cursor: "pointer",
							fontSize: "12.5px",
							fontWeight: 500,
							padding: "6px 12px",
							borderRadius: "6px",
							background: "#16181D",
							color: "#FFFFFF",
						}}
					>
						Run pipeline
					</button>
				</div>
			</header>

			{/* Main Content Workspace (marked inert when Artifact Full Modal is open) */}
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
