/**
 * Stages Track for Pipeline Overview (User Directive R9 Modularity).
 * Renders the 8 canonical stages grouped by 4 phases with status indicators.
 */

import {
	type Artifact,
	type PhaseSpec,
	type Stage,
	type WaveFilterId,
	filterArtifactIdsByWave,
} from "@opendv/protocol-ts";
import {
	EXPLORER_FONTS,
	EXPLORER_GLYPHS,
	getGlyphCssStyle,
	getStatusIndicator,
	getStatusTagStyle,
} from "../design-tokens";
import type { ExplorerRunState } from "../types";

export interface OverviewStagesTrackProps {
	phases: readonly PhaseSpec[];
	stages: readonly Stage[];
	artifacts: Readonly<Record<string, Artifact>>;
	activeWave: WaveFilterId;
	onOpenStage: (stageIdx: number) => void;
	runState?: ExplorerRunState | undefined;
	activeRunningStageIdx?: number | null | undefined;
}

export function OverviewStagesTrack({
	phases,
	stages,
	artifacts,
	activeWave,
	onOpenStage,
	runState,
	activeRunningStageIdx = null,
}: OverviewStagesTrackProps) {
	return (
		<section
			aria-label="8-Stage Pipeline"
			onClick={(ev) => ev.stopPropagation()}
			style={{
				flex: 1,
				minWidth: "440px",
				display: "flex",
				flexDirection: "column",
				gap: "8px",
			}}
		>
			<div
				style={{
					display: "flex",
					alignItems: "center",
					justifyContent: "space-between",
					marginBottom: "4px",
				}}
			>
				<span
					style={{
						fontSize: "11px",
						fontWeight: 600,
						textTransform: "uppercase",
						letterSpacing: "0.06em",
						color: "#6B7280",
					}}
				>
					Pipeline · 8 stages (click a stage to inspect its steps)
				</span>
				<div
					style={{
						display: "flex",
						alignItems: "center",
						gap: "12px",
						fontSize: "11px",
						color: "#6B7280",
					}}
				>
					<span
						style={{
							display: "inline-flex",
							alignItems: "center",
							gap: "5px",
						}}
					>
						<span style={getGlyphCssStyle(EXPLORER_GLYPHS.control, 8)} />
						control
					</span>
					<span
						style={{
							display: "inline-flex",
							alignItems: "center",
							gap: "5px",
						}}
					>
						<span style={getGlyphCssStyle(EXPLORER_GLYPHS.data, 8)} />
						data
					</span>
					<span
						style={{
							display: "inline-flex",
							alignItems: "center",
							gap: "5px",
						}}
					>
						<span style={getGlyphCssStyle(EXPLORER_GLYPHS.component, 8)} />
						component
					</span>
				</div>
			</div>

			<div
				style={{
					position: "relative",
					display: "flex",
					flexDirection: "column",
					gap: "16px",
				}}
			>
				{/* Vertical 1px rail behind numbered circles */}
				<div
					style={{
						position: "absolute",
						left: "19px",
						top: "28px",
						bottom: "28px",
						width: "1px",
						background: "#D4D7DC",
					}}
				/>

				{phases.map((phase) => {
					const stagesInPhase = stages
						.map((stage, idx) => ({ stage, idx }))
						.filter((x) => x.stage.phase === phase.index);

					return (
						<div
							key={phase.label}
							data-testid={`pipeline-phase-${phase.label.toLowerCase()}`}
							style={{
								display: "flex",
								flexDirection: "column",
								gap: "10px",
							}}
						>
							<div
								style={{
									display: "flex",
									alignItems: "center",
									gap: "8px",
									paddingLeft: "52px",
								}}
							>
								<span
									style={{
										fontSize: "11px",
										fontWeight: 600,
										textTransform: "uppercase",
										letterSpacing: "0.06em",
										color: "#6B7280",
									}}
								>
									{phase.label}
								</span>
								<span
									style={{
										fontFamily: EXPLORER_FONTS.mono,
										fontSize: "11px",
										color: "#6B7280",
									}}
								>
									{phase.sub}
								</span>
							</div>

							{stagesInPhase.map(({ stage, idx }) => {
								const numStr = String(idx).padStart(2, "0");
								const tagStyle = getStatusTagStyle(stage.tag);
								const indicator = getStatusIndicator(stage.tag);
								const filteredIns = filterArtifactIdsByWave(
									stage.ins,
									artifacts,
									activeWave,
								);
								const filteredOuts = filterArtifactIdsByWave(
									stage.outs,
									artifacts,
									activeWave,
								);
								const countsLabel = `${filteredIns.length} in · ${filteredOuts.length} out · ${stage.steps.length} steps`;

								const isStageRunning =
									runState === "running" && activeRunningStageIdx === idx;
								const isStageCompleted =
									(runState === "running" &&
										activeRunningStageIdx !== null &&
										idx < activeRunningStageIdx) ||
									(runState === "sealed" &&
										(stage.tag === "PASS" ||
											stage.tag === "AUTO-FIX" ||
											stage.tag === "AUTO_REPAIRED"));
								const isStageQueued =
									runState === "running" &&
									activeRunningStageIdx !== null &&
									idx > activeRunningStageIdx;
								const runStatus = isStageRunning
									? "running"
									: isStageCompleted
										? "completed"
										: isStageQueued
											? "queued"
											: "idle";

								return (
									<div
										key={stage.key}
										data-testid={`stage-card-${numStr}`}
										data-run-status={runStatus}
										onClick={() => onOpenStage(idx)}
										onKeyDown={(ev) => {
											if (ev.key === "Enter" || ev.key === " ") {
												ev.preventDefault();
												onOpenStage(idx);
											} else if (
												ev.key === "ArrowDown" ||
												ev.key === "ArrowUp"
											) {
												ev.preventDefault();
												const container =
													ev.currentTarget.closest(
														'section[aria-label="8-Stage Pipeline"]',
													) ?? ev.currentTarget.parentElement;
												if (!container) return;
												const cards = Array.from(
													container.querySelectorAll<HTMLElement>(
														'[data-testid^="stage-card-"]',
													),
												);
												const currentIdx = cards.indexOf(ev.currentTarget);
												if (
													ev.key === "ArrowDown" &&
													currentIdx >= 0 &&
													currentIdx < cards.length - 1
												) {
													cards[currentIdx + 1]?.focus();
												} else if (ev.key === "ArrowUp" && currentIdx > 0) {
													cards[currentIdx - 1]?.focus();
												}
											}
										}}
										tabIndex={0}
										role="button"
										aria-label={`Stage ${numStr}: ${stage.name}. Status: ${stage.tag} (${indicator.label}). Press Enter to open stage.`}
										style={{
											display: "flex",
											gap: "12px",
											alignItems: "stretch",
											cursor: "pointer",
											opacity: isStageQueued ? 0.78 : 1,
										}}
									>
										<div
											style={{
												width: "40px",
												flexShrink: 0,
												display: "flex",
												justifyContent: "center",
												paddingTop: "12px",
											}}
										>
											<div
												style={{
													width: "30px",
													height: "30px",
													borderRadius: "999px",
													background: isStageRunning ? "#1D4ED8" : "#FFFFFF",
													border: isStageRunning
														? "2px solid #1D4ED8"
														: "1.5px solid #16181D",
													display: "flex",
													alignItems: "center",
													justifyContent: "center",
													fontFamily: EXPLORER_FONTS.mono,
													fontSize: "12px",
													fontWeight: 600,
													color: isStageRunning ? "#FFFFFF" : "#16181D",
													position: "relative",
													zIndex: 1,
												}}
											>
												{numStr}
											</div>
										</div>

										<div
											style={{
												flex: 1,
												background: "#FFFFFF",
												border: isStageRunning
													? "2px solid #1D4ED8"
													: "1px solid #E3E5E8",
												boxShadow: isStageRunning
													? "0 0 0 3px rgba(29, 78, 216, 0.12)"
													: undefined,
												borderRadius: "8px",
												padding: "13px 16px",
												display: "flex",
												flexDirection: "column",
												gap: "6px",
											}}
										>
											{isStageRunning && (
												<div
													aria-hidden="true"
													style={{
														width: "100%",
														height: "3px",
														borderRadius: "999px",
														background: "#DBEAFE",
														overflow: "clip",
														marginBottom: "2px",
													}}
												>
													<div
														style={{
															width: "65%",
															height: "100%",
															borderRadius: "999px",
															background: "#1D4ED8",
														}}
													/>
												</div>
											)}

											<div
												style={{
													display: "flex",
													alignItems: "center",
													justifyContent: "space-between",
													gap: "10px",
												}}
											>
												<div
													style={{
														display: "flex",
														alignItems: "center",
														gap: "8px",
														minWidth: 0,
													}}
												>
													<span
														style={{
															fontSize: "15px",
															fontWeight: 600,
															color: "#16181D",
														}}
													>
														{stage.name}
													</span>
													{isStageRunning && (
														<span
															data-testid={`stage-running-indicator-${numStr}`}
															style={{
																fontFamily: EXPLORER_FONTS.mono,
																fontSize: "10px",
																fontWeight: 600,
																padding: "1.5px 7px",
																borderRadius: "999px",
																background: "#DBEAFE",
																color: "#1D4ED8",
															}}
														>
															● RUNNING
														</span>
													)}
													{isStageCompleted && (
														<span
															data-testid={`stage-completed-indicator-${numStr}`}
															style={{
																fontFamily: EXPLORER_FONTS.mono,
																fontSize: "10px",
																fontWeight: 600,
																padding: "1.5px 7px",
																borderRadius: "999px",
																background: "#DCFCE7",
																color: "#15803D",
															}}
														>
															✓ COMPLETED
														</span>
													)}
													{isStageQueued && (
														<span
															data-testid={`stage-queued-indicator-${numStr}`}
															style={{
																fontFamily: EXPLORER_FONTS.mono,
																fontSize: "10px",
																fontWeight: 500,
																padding: "1.5px 7px",
																borderRadius: "999px",
																background: "#F1F5F9",
																color: "#64748B",
															}}
														>
															◌ QUEUED
														</span>
													)}
												</div>
												<span
													style={{
														fontSize: "12px",
														fontWeight: 500,
														color: "#1D4ED8",
														flexShrink: 0,
													}}
												>
													Open stage →
												</span>
											</div>

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
														fontSize: "11px",
														color: "#525866",
													}}
												>
													{stage.key} · {stage.persona} · {stage.tier}
												</span>
												<span
													aria-label={`Status: ${stage.tag} (${indicator.label})`}
													style={{
														fontFamily: EXPLORER_FONTS.mono,
														fontSize: "10.5px",
														fontWeight: 500,
														padding: "2px 8px",
														borderRadius: "999px",
														background: tagStyle.bg,
														color: tagStyle.fg,
														display: "inline-flex",
														alignItems: "center",
														gap: "4px",
													}}
												>
													<span aria-hidden="true">{indicator.symbol}</span>
													<span>{stage.tag}</span>
												</span>
											</div>

											<div
												style={{
													fontSize: "13px",
													color: "#3B414C",
													lineHeight: 1.45,
												}}
											>
												{stage.blurb}
											</div>

											{stage.note && (
												<div
													style={{
														fontFamily: EXPLORER_FONTS.mono,
														fontSize: "11px",
														padding: "5px 8px",
														borderRadius: "4px",
														background: "#FFF9F0",
														color: "#8A3B07",
														border: "1px solid #F3D9B1",
													}}
												>
													{stage.note}
												</div>
											)}

											<div
												style={{
													fontFamily: EXPLORER_FONTS.mono,
													fontSize: "11px",
													color: "#6B7280",
													paddingTop: "4px",
													borderTop: "1px solid #F2F3F5",
												}}
											>
												{countsLabel}
											</div>
										</div>
									</div>
								);
							})}
						</div>
					);
				})}
			</div>
		</section>
	);
}
