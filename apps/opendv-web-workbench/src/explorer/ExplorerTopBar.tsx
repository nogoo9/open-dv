import type {
	ExplorerBundleDTO,
	ProjectRunDTO,
	Stage,
	WaveFilterId,
} from "@opendv/protocol-ts";
import type { CSSProperties } from "react";
import { EXPLORER_FONTS } from "./design-tokens";
import type { ExplorerRunState, ExplorerViewLevel } from "./types";

const WAVE_SEGMENTS: readonly { label: string; id: WaveFilterId }[] = [
	{ label: "All", id: "all" },
	{ label: "W0", id: "W0" },
	{ label: "W1", id: "W1" },
	{ label: "W2", id: "W2" },
	{ label: "W3", id: "W3" },
] as const;

const RESET_BUTTON_STYLE: CSSProperties = {
	background: "transparent",
	border: "none",
	padding: 0,
	margin: 0,
	font: "inherit",
	textAlign: "left",
	cursor: "pointer",
};

const DROPDOWN_ITEM_STYLE: CSSProperties = {
	...RESET_BUTTON_STYLE,
	padding: "6px 8px",
	borderRadius: "4px",
	display: "flex",
	flexDirection: "column",
	gap: "2px",
};

export interface ExplorerTopBarProps {
	bundle: ExplorerBundleDTO;
	view: ExplorerViewLevel;
	currentStage?: Stage | undefined;
	stageShortCode: string;
	activeWave: WaveFilterId;
	runState: ExplorerRunState;
	statusBannerText: string;
	statusDotColor: string;
	streamedTokens: number;
	isRunSelectorOpen: boolean;
	isLoadingRuns: boolean;
	runsList: readonly ProjectRunDTO[];
	onToggleRunSelector: () => void;
	onSelectRun: (run: ProjectRunDTO) => void;
	onOpenNewRunModal: () => void;
	onGoPipeline: () => void;
	onGoBuilder?: (() => void) | undefined;
	onGoSettings?: (() => void) | undefined;
	onSetWaveFilter: (wave: WaveFilterId) => void;
}

export function ExplorerTopBar({
	bundle,
	view,
	currentStage,
	stageShortCode,
	activeWave,
	runState,
	statusBannerText,
	statusDotColor,
	streamedTokens,
	isRunSelectorOpen,
	isLoadingRuns,
	runsList,
	onToggleRunSelector,
	onSelectRun,
	onOpenNewRunModal,
	onGoPipeline,
	onGoBuilder,
	onGoSettings,
	onSetWaveFilter,
}: ExplorerTopBarProps) {
	return (
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
						onClick={onGoPipeline}
						style={{
							...RESET_BUTTON_STYLE,
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

				<span style={{ color: "#D4D7DC" }}>|</span>
				<nav
					aria-label="Primary Workspace Views"
					style={{
						display: "flex",
						alignItems: "center",
						gap: "4px",
					}}
				>
					<button
						type="button"
						data-testid="nav-tab-explorer"
						aria-current={
							view === "pipeline" || view === "stage" ? "page" : undefined
						}
						onClick={onGoPipeline}
						style={{
							...RESET_BUTTON_STYLE,
							fontSize: "12px",
							fontWeight: 500,
							padding: "4px 9px",
							borderRadius: "6px",
							background:
								view === "pipeline" || view === "stage"
									? "#EEF0F2"
									: "transparent",
							color:
								view === "pipeline" || view === "stage" ? "#16181D" : "#545A66",
						}}
					>
						Explorer
					</button>
					{onGoBuilder && (
						<button
							type="button"
							data-testid="nav-tab-builder"
							aria-current={view === "builder" ? "page" : undefined}
							onClick={onGoBuilder}
							style={{
								...RESET_BUTTON_STYLE,
								fontSize: "12px",
								fontWeight: 500,
								padding: "4px 9px",
								borderRadius: "6px",
								background: view === "builder" ? "#EEF0F2" : "transparent",
								color: view === "builder" ? "#16181D" : "#545A66",
							}}
						>
							Pipeline Builder
						</button>
					)}
					{onGoSettings && (
						<button
							type="button"
							data-testid="nav-tab-settings"
							aria-current={view === "settings" ? "page" : undefined}
							onClick={onGoSettings}
							style={{
								...RESET_BUTTON_STYLE,
								fontSize: "12px",
								fontWeight: 500,
								padding: "4px 9px",
								borderRadius: "6px",
								background: view === "settings" ? "#EEF0F2" : "transparent",
								color: view === "settings" ? "#16181D" : "#545A66",
							}}
						>
							Settings
						</button>
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
						title="Click to select recent pipeline runs"
						onClick={onToggleRunSelector}
						style={{
							...RESET_BUTTON_STYLE,
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
									onClick={() => onSelectRun(run)}
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
												color: run.status === "RUNNING" ? "#8A3B07" : "#16181D",
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
								data-testid="btn-dropdown-new-run"
								onClick={onOpenNewRunModal}
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
								onClick={() => onSetWaveFilter(seg.id)}
								style={{
									...RESET_BUTTON_STYLE,
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
					onClick={onOpenNewRunModal}
					style={{
						...RESET_BUTTON_STYLE,
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
	);
}
