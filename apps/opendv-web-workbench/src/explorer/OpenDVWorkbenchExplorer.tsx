/**
 * Top-Level 3-Level Pipeline Explorer (`OpenDVWorkbenchExplorer.tsx`)
 * (`Pipeline -> Stage -> Artifact`) (`docs/design/open-dv-workbench/HANDOFF.md`).
 */

import type { HoldResolutionParams, WaveFilterId } from "@opendv/protocol-ts";
import { useEffect } from "react";
import type { StoreApi, UseBoundStore } from "zustand";
import { ArtifactFullModal } from "./ArtifactFullModal";
import { PipelineOverviewView } from "./PipelineOverviewView";
import { StageDrillDownView } from "./StageDrillDownView";
import { EXPLORER_COLORS, EXPLORER_FONTS } from "./design-tokens";
import {
	type ExplorerStoreState,
	hasActiveReactDispatcher,
	useExplorerStore,
} from "./explorer-store";

export interface OpenDVWorkbenchExplorerProps {
	store?: UseBoundStore<StoreApi<ExplorerStoreState>> | undefined;
	fetchImpl?: (typeof fetch) | undefined;
}

function safeUseEffect(
	effect: () => void | (() => void),
	deps?: readonly unknown[] | undefined,
): void {
	if (!hasActiveReactDispatcher()) {
		return;
	}
	try {
		useEffect(effect, deps);
	} catch {
		// Ignore hook errors when called outside of a React render tree
	}
}

const WAVE_SEGMENTS: readonly { label: string; id: WaveFilterId }[] = [
	{ label: "All", id: "all" },
	{ label: "W0", id: "W0" },
	{ label: "W1", id: "W1" },
	{ label: "W2", id: "W2" },
	{ label: "W3", id: "W3" },
] as const;

export function OpenDVWorkbenchExplorer({
	store = useExplorerStore,
	fetchImpl,
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

	// Sync initial URL and browser popstate
	safeUseEffect(() => {
		if (
			typeof globalThis !== "undefined" &&
			"location" in globalThis &&
			globalThis.location
		) {
			const loc = globalThis.location;
			if (loc.pathname !== "/" || loc.search !== "") {
				syncFromUrl(loc.pathname, loc.search);
			}
		}
		if (
			typeof globalThis !== "undefined" &&
			typeof globalThis.addEventListener === "function"
		) {
			const onPopState = () => {
				if (globalThis.location) {
					syncFromUrl(
						globalThis.location.pathname,
						globalThis.location.search,
					);
				}
			};
			globalThis.addEventListener("popstate", onPopState);
			return () => {
				globalThis.removeEventListener("popstate", onPopState);
			};
		}
		return;
	}, [syncFromUrl]);

	// Escape key handler: close Artifact Full View first, otherwise clear selection
	safeUseEffect(() => {
		if (
			typeof globalThis === "undefined" ||
			typeof globalThis.addEventListener !== "function"
		) {
			return;
		}
		const onKeyDown = (ev: KeyboardEvent) => {
			if (ev.key === "Escape") {
				if (store.getState().fullId) {
					ev.preventDefault();
					closeFullArtifact();
				} else if (store.getState().selType !== null) {
					ev.preventDefault();
					clearSelection();
				}
			}
		};
		globalThis.addEventListener("keydown", onKeyDown);
		return () => {
			globalThis.removeEventListener("keydown", onKeyDown);
		};
	}, [store, closeFullArtifact, clearSelection]);

	const currentStage = bundle.stages[stageIdx] ?? bundle.stages[0];
	const stageShortCode = currentStage?.key.slice(0, 8) ?? "STAGE_00";
	const fullArtifact = fullId ? bundle.artifacts[fullId] : undefined;

	const handleResolveHold = (params: HoldResolutionParams) => {
		void resolveStewardHold({
			...params,
			...(fetchImpl !== undefined ? { fetchImpl } : {}),
		});
	};

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
								style={{
									display: "flex",
									alignItems: "center",
									gap: "8px",
								}}
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

				{/* Center: Run Selector Pill & Wave Segmented Filter */}
				<div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
					<button
						type="button"
						data-testid="btn-run-selector-pill"
						title="Click to switch between Global Commerce Vault and Apex Horizon PE Vault runs"
						onClick={() =>
							switchPresetBundle(
								activeBundleKey === "global_commerce_vault"
									? "proj-apex-horizon-pe"
									: "global_commerce_vault",
							)
						}
						style={{
							all: "unset",
							cursor: "pointer",
							display: "flex",
							alignItems: "center",
							gap: "8px",
							padding: "5px 10px",
							border: "1px solid #E3E5E8",
							borderRadius: "6px",
							background: "#FAFBFD",
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
					</button>

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

				{/* Right: Run Status & Primary Run Pipeline Action */}
				<div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
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
						onClick={() => {
							void triggerPipelineRun(
								fetchImpl !== undefined ? { fetchImpl } : undefined,
							);
						}}
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
						stages={bundle.stages}
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
						stages={bundle.stages}
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
		</main>
	);
}
