import type {
	ExplorerBundleDTO,
	PlatformConfigResponse,
	ProjectRunDTO,
	Stage,
	WaveFilterId,
} from "@opendv/protocol-ts";
import type { CSSProperties } from "react";
import { RemoteUnauthenticatedBanner } from "./RemoteUnauthenticatedBanner";
import { TopBarStatusPills } from "./TopBarStatusPills";
import { CLAUDE_TOKENS, EXPLORER_FONTS } from "./design-tokens";
import { safeUseState } from "./modals/safeHooks";
import type { ExplorerRunState, ExplorerViewLevel } from "./types";

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
	borderRadius: "6px",
	display: "flex",
	flexDirection: "column",
	gap: "2px",
};

export interface ExplorerTopBarProps {
	bundle: ExplorerBundleDTO;
	view: ExplorerViewLevel;
	currentStage?: Stage | undefined;
	stageShortCode: string;
	activeWave?: WaveFilterId | undefined;
	runState: ExplorerRunState;
	statusBannerText: string;
	statusDotColor: string;
	streamedTokens: number;
	isRunSelectorOpen: boolean;
	isLoadingRuns: boolean;
	runsList: readonly ProjectRunDTO[];
	platformConfig?: PlatformConfigResponse | null | undefined;
	remoteUnauthenticatedMode?: boolean | undefined;
	agentReady?: boolean | undefined;
	sessionCostTokens?: number | undefined;
	sessionCostUsd?: number | undefined;
	sessionLogsCount?: number | undefined;
	sessionDigest?: string | null | undefined;
	onToggleRunSelector: () => void;
	onSelectRun: (run: ProjectRunDTO) => void;
	onOpenNewRunModal: () => void;
	onGoOntology?: (() => void) | undefined;
	onGoDvSchema?: (() => void) | undefined;
	onGoPipeline: () => void;
	onGoBuilder?: (() => void) | undefined;
	onGoSettings?: (() => void) | undefined;
	onOpenLogs?: (() => void) | undefined;
	onSetWaveFilter?: ((wave: WaveFilterId) => void) | undefined;
}

export function ExplorerTopBar({
	bundle,
	view,
	currentStage,
	stageShortCode,
	runState,
	statusBannerText,
	statusDotColor,
	streamedTokens,
	isRunSelectorOpen,
	isLoadingRuns,
	runsList,
	platformConfig,
	remoteUnauthenticatedMode,
	agentReady,
	sessionCostTokens = 0,
	sessionCostUsd = 0,
	sessionLogsCount = 0,
	sessionDigest,
	onToggleRunSelector,
	onSelectRun,
	onOpenNewRunModal,
	onGoOntology,
	onGoDvSchema,
	onGoPipeline,
	onGoBuilder,
	onGoSettings,
	onOpenLogs,
}: ExplorerTopBarProps) {
	const [isMobileNavOpen, setIsMobileNavOpen] = safeUseState(false);

	const showRunControls = view === "pipeline" || view === "stage";
	const isOntologyActive = view === "ontology" || view === "builder";
	const isDvSchemaActive = view === "dv_schema";
	const isExplorerActive = view === "pipeline" || view === "stage";
	const isSettingsActive = view === "settings";
	const showRemoteBanner = Boolean(
		remoteUnauthenticatedMode ?? platformConfig?.remote_unauthenticated_mode,
	);

	const tabStyle = (active: boolean): CSSProperties => ({
		...RESET_BUTTON_STYLE,
		fontFamily: EXPLORER_FONTS.sans,
		fontSize: "13px",
		fontWeight: 500,
		padding: "6px 12px",
		borderRadius: "8px",
		background: active ? CLAUDE_TOKENS.colors.surfaceCard : "transparent",
		color: active ? CLAUDE_TOKENS.colors.ink : CLAUDE_TOKENS.colors.muted,
		whiteSpace: "nowrap",
	});

	return (
		<div>
			<header
				data-testid="explorer-top-header"
				className="claude-topbar"
				style={{
					height: "54px",
					background: CLAUDE_TOKENS.colors.canvas,
					borderBottom: `1px solid ${CLAUDE_TOKENS.colors.hairline}`,
					display: "flex",
					alignItems: "center",
					justifyContent: "space-between",
					gap: "14px",
					position: "relative",
				}}
			>
				{/* Left: Spike Mark + open-dv + 4 Canonical Tabs */}
				<div
					className="claude-topbar-left"
					style={{
						display: "flex",
						alignItems: "center",
						gap: "14px",
						minWidth: 0,
					}}
				>
					<div
						style={{
							display: "flex",
							alignItems: "center",
							gap: "8px",
							flexShrink: 0,
						}}
					>
						<svg
							width="16"
							height="16"
							viewBox="0 0 24 24"
							fill="none"
							style={{ flexShrink: 0 }}
							aria-hidden="true"
						>
							<g
								stroke={CLAUDE_TOKENS.colors.ink}
								strokeWidth="2.1"
								strokeLinecap="round"
							>
								<line x1="12" y1="2.5" x2="12" y2="21.5" />
								<line x1="2.5" y1="12" x2="21.5" y2="12" />
								<line x1="5.3" y1="5.3" x2="18.7" y2="18.7" />
								<line x1="18.7" y1="5.3" x2="5.3" y2="18.7" />
							</g>
						</svg>
						<span
							style={{
								fontFamily: EXPLORER_FONTS.mono,
								fontWeight: 600,
								fontSize: "14px",
								letterSpacing: "-0.01em",
								color: CLAUDE_TOKENS.colors.ink,
								whiteSpace: "nowrap",
							}}
						>
							open-dv
						</span>
					</div>

					<span style={{ color: CLAUDE_TOKENS.colors.hairlineStrong }}>|</span>

					<button
						type="button"
						className="claude-mobile-nav-toggle"
						aria-label="Toggle workspace navigation"
						aria-expanded={isMobileNavOpen}
						onClick={() => setIsMobileNavOpen((prev) => !prev)}
						style={{
							...RESET_BUTTON_STYLE,
							padding: "4px 8px",
							borderRadius: "6px",
							border: `1px solid ${CLAUDE_TOKENS.colors.hairlineStrong}`,
							color: CLAUDE_TOKENS.colors.ink,
						}}
					>
						☰
					</button>

					<nav
						aria-label="Primary Workspace Views"
						className="claude-topbar-nav"
						data-mobile-open={isMobileNavOpen ? "true" : "false"}
					>
						<button
							type="button"
							data-testid="nav-tab-ontology"
							aria-current={isOntologyActive ? "page" : undefined}
							onClick={onGoOntology ?? onGoBuilder ?? onGoPipeline}
							style={tabStyle(isOntologyActive)}
						>
							Ontology &amp; Pipeline
						</button>
						<button
							type="button"
							data-testid="nav-tab-dv-schema"
							aria-current={isDvSchemaActive ? "page" : undefined}
							onClick={onGoDvSchema ?? onGoPipeline}
							style={tabStyle(isDvSchemaActive)}
						>
							DV Schema Explorer
						</button>
						<button
							type="button"
							data-testid="nav-tab-explorer"
							aria-current={isExplorerActive ? "page" : undefined}
							onClick={onGoPipeline}
							style={tabStyle(isExplorerActive)}
						>
							Run Explorer
						</button>
						<button
							type="button"
							data-testid="nav-tab-settings"
							aria-current={isSettingsActive ? "page" : undefined}
							onClick={onGoSettings}
							style={tabStyle(isSettingsActive)}
						>
							Settings
						</button>
					</nav>
				</div>

				{/* Right: Live Readiness, Cost Meter, Logs & Traces, Session Pill */}
				<TopBarStatusPills
					platformConfig={platformConfig}
					agentReady={agentReady}
					sessionCostTokens={sessionCostTokens + streamedTokens}
					sessionCostUsd={sessionCostUsd}
					sessionLogsCount={sessionLogsCount}
					sessionDigest={
						sessionDigest ??
						(bundle.runId ? `${bundle.displayName}` : undefined)
					}
					onGoSettings={onGoSettings}
					onOpenLogs={onOpenLogs}
				/>
			</header>

			{/* Explorer Sub-Bar (46px): Stage Breadcrumb, Run Selector, Status & Run Pipeline CTA */}
			{showRunControls && (
				<div
					className="claude-subbar claude-topbar-run-controls"
					data-testid="explorer-subbar"
				>
					<div
						style={{
							display: "flex",
							alignItems: "center",
							gap: "10px",
							flexWrap: "wrap",
							maxWidth: "100%",
							minWidth: 0,
						}}
					>
						{view === "stage" && currentStage && (
							<button
								type="button"
								data-testid="breadcrumb-stage"
								title="Back to Run Explorer overview"
								aria-label={`Back to Run Explorer from ${stageShortCode} ${currentStage.name}`}
								onClick={onGoPipeline}
								style={{
									...RESET_BUTTON_STYLE,
									display: "inline-flex",
									alignItems: "center",
									padding: "4px 10px",
									borderRadius: "8px",
									border: `1px solid ${CLAUDE_TOKENS.colors.hairlineStrong}`,
									background: CLAUDE_TOKENS.colors.canvas,
									fontFamily: EXPLORER_FONTS.mono,
									fontSize: "12px",
									fontWeight: 600,
									color: CLAUDE_TOKENS.colors.ink,
									cursor: "pointer",
								}}
							>
								{stageShortCode} · {currentStage.name}
							</button>
						)}
						<div style={{ position: "relative", maxWidth: "100%" }}>
							<button
								type="button"
								data-testid="btn-run-selector-pill"
								title="Click to select recent pipeline runs"
								onClick={onToggleRunSelector}
								style={{
									...RESET_BUTTON_STYLE,
									display: "flex",
									alignItems: "center",
									flexWrap: "wrap",
									maxWidth: "100%",
									gap: "8px",
									padding: "5px 10px",
									border: `1px solid ${CLAUDE_TOKENS.colors.hairlineStrong}`,
									borderRadius: "8px",
									background: isRunSelectorOpen
										? CLAUDE_TOKENS.colors.surfaceCreamStrong
										: CLAUDE_TOKENS.colors.canvas,
								}}
							>
								<span
									style={{
										fontSize: "11px",
										color: CLAUDE_TOKENS.colors.muted,
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
										color: CLAUDE_TOKENS.colors.ink,
									}}
								>
									{bundle.displayName}
								</span>
								<span
									style={{
										fontFamily: EXPLORER_FONTS.mono,
										fontSize: "11px",
										color: CLAUDE_TOKENS.colors.muted,
									}}
								>
									{bundle.specFile} · {bundle.wavesCount} waves
								</span>
								<span
									data-testid="run-selector-chevron"
									style={{
										fontSize: "10px",
										color: CLAUDE_TOKENS.colors.muted,
									}}
								>
									{isRunSelectorOpen ? "▲" : "▼"}
								</span>
							</button>

							{isRunSelectorOpen && (
								<div
									role="menu"
									data-testid="run-selector-dropdown"
									style={{
										position: "absolute",
										top: "calc(100% + 4px)",
										left: 0,
										zIndex: 1000,
										background: CLAUDE_TOKENS.colors.canvas,
										border: `1px solid ${CLAUDE_TOKENS.colors.hairline}`,
										borderRadius: "10px",
										boxShadow: "0 8px 24px rgba(20,20,19,0.14)",
										minWidth: "min(320px, calc(100vw - 24px))",
										maxWidth: "min(420px, calc(100vw - 24px))",
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
											color: CLAUDE_TOKENS.colors.muted,
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
												color: CLAUDE_TOKENS.colors.muted,
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
													bundle.runId === run.run_id
														? CLAUDE_TOKENS.colors.surfaceCard
														: "transparent",
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
														background: CLAUDE_TOKENS.colors.surfaceSoft,
														color: CLAUDE_TOKENS.colors.ink,
													}}
												>
													{run.status}
												</span>
											</div>
											<div
												style={{
													fontSize: "11px",
													color: CLAUDE_TOKENS.colors.muted,
												}}
											>
												{run.project_id}{" "}
												{run.event_count ? `· ${run.event_count} events` : ""}
											</div>
										</button>
									))}
									<div
										style={{
											borderTop: `1px solid ${CLAUDE_TOKENS.colors.hairline}`,
											margin: "4px 0",
										}}
									/>
									<button
										type="button"
										role="menuitem"
										data-testid="btn-dropdown-new-run"
										onClick={onOpenNewRunModal}
										style={{
											...DROPDOWN_ITEM_STYLE,
											color: CLAUDE_TOKENS.colors.primaryActive,
											fontWeight: 500,
											fontSize: "12px",
										}}
									>
										+ New Pipeline Run...
									</button>
								</div>
							)}
						</div>
					</div>

					<div
						style={{
							display: "flex",
							alignItems: "center",
							gap: "8px",
							flexWrap: "wrap",
							maxWidth: "100%",
						}}
					>
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
								background:
									runState === "hold"
										? CLAUDE_TOKENS.colors.surfaceCreamStrong
										: CLAUDE_TOKENS.colors.surfaceCard,
								color: CLAUDE_TOKENS.colors.ink,
								maxWidth: "100%",
							}}
						>
							<span
								style={{
									width: "6px",
									height: "6px",
									borderRadius: "999px",
									background: statusDotColor,
									flexShrink: 0,
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
								borderRadius: "8px",
								background: CLAUDE_TOKENS.colors.primary,
								color: CLAUDE_TOKENS.colors.onPrimary,
								whiteSpace: "nowrap",
							}}
						>
							Run pipeline
						</button>
					</div>
				</div>
			)}

			<RemoteUnauthenticatedBanner visible={showRemoteBanner} />
		</div>
	);
}
