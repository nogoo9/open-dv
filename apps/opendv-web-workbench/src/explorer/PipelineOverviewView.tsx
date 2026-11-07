/**
 * View 1 — Pipeline Overview (`PipelineOverviewView.tsx`)
 * Settings + Inputs column, 8-Stage Pipeline grouped by 4 Phases, Outputs column,
 * and contextual Inspector (`docs/design/open-dv-workbench/HANDOFF.md`).
 */

import {
	type Artifact,
	type HoldResolutionParams,
	type PhaseSpec,
	type Stage,
	type WaveFilterId,
	filterArtifactIdsByWave,
} from "@opendv/protocol-ts";
import { InspectorPanel } from "./InspectorPanel";
import {
	EXPLORER_FONTS,
	EXPLORER_GLYPHS,
	getGlyphCssStyle,
	getStatusTagStyle,
} from "./design-tokens";
import type { ExplorerSelectionType } from "./explorer-store";

export interface PipelineOverviewViewProps {
	settingIds: readonly string[];
	inputIds: readonly string[];
	outputIds: readonly string[];
	artifacts: Readonly<Record<string, Artifact>>;
	phases: readonly PhaseSpec[];
	stages: readonly Stage[];
	hasInitialContract: boolean;
	activeWave: WaveFilterId;
	selType: ExplorerSelectionType;
	selId: string | number | null;
	onSelectArtifact: (artifactId: string) => void;
	onOpenFullArtifact: (artifactId: string) => void;
	onOpenStage: (stageIdx: number) => void;
	onClearSelection: () => void;
	onResolveHold?: ((params: HoldResolutionParams) => void) | undefined;
}

function buildCardPreviewLines(artifact: Artifact): string {
	if (artifact.viz === "table" && artifact.cols && artifact.rows) {
		return [
			artifact.cols.join(" | "),
			...artifact.rows.slice(0, 2).map((r) => r.join(" | ")),
		].join("\n");
	}
	return artifact.raw.split("\n").slice(0, 3).join("\n");
}

interface ArtifactCardItemProps {
	artifact: Artifact;
	isSelected: boolean;
	showPreviewLines?: boolean | undefined;
	onSelect: () => void;
	onExpand: () => void;
}

function ArtifactCardItem({
	artifact,
	isSelected,
	showPreviewLines = false,
	onSelect,
	onExpand,
}: ArtifactCardItemProps) {
	const glyph =
		artifact.group === "Setting"
			? EXPLORER_GLYPHS.setting
			: EXPLORER_GLYPHS.data;
	const borderColor = isSelected ? "#1D4ED8" : "#E3E5E8";
	const previewLines = showPreviewLines
		? buildCardPreviewLines(artifact)
		: "";

	return (
		<div
			data-testid={`artifact-card-${artifact.id}`}
			onClick={(ev) => {
				ev.stopPropagation();
				onSelect();
			}}
			onKeyDown={(ev) => {
				if (ev.key === "Enter" || ev.key === " ") {
					ev.preventDefault();
					ev.stopPropagation();
					onSelect();
				}
			}}
			tabIndex={0}
			role="button"
			aria-pressed={isSelected}
			style={{
				background: "#FFFFFF",
				border: `1.5px solid ${borderColor}`,
				borderRadius: "6px",
				padding: showPreviewLines ? "10px 12px" : "9px 11px",
				cursor: "pointer",
				display: "flex",
				flexDirection: "column",
				gap: showPreviewLines ? "4px" : "3px",
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
				<div
					style={{
						display: "flex",
						alignItems: "center",
						gap: "7px",
						minWidth: 0,
					}}
				>
					<span style={getGlyphCssStyle(glyph, 10)} />
					<span
						style={{
							fontFamily: EXPLORER_FONTS.mono,
							fontSize: "12px",
							fontWeight: 500,
							color: "#16181D",
							overflow: "hidden",
							textOverflow: "ellipsis",
							whiteSpace: "nowrap",
						}}
					>
						{artifact.name}
					</span>
				</div>
				<button
					type="button"
					data-testid={`btn-expand-artifact-${artifact.id}`}
					onClick={(ev) => {
						ev.stopPropagation();
						onExpand();
					}}
					style={{
						all: "unset",
						cursor: "pointer",
						fontSize: "11px",
						color: "#545A66",
						padding: "1px 5px",
						borderRadius: "4px",
						flexShrink: 0,
					}}
				>
					Expand ↗
				</button>
			</div>
			<div style={{ fontSize: "11.5px", color: "#545A66" }}>
				{artifact.short}
			</div>
			{showPreviewLines && (
				<pre
					style={{
						margin: "4px 0 0",
						padding: "6px 8px",
						background: "#F6F7F8",
						borderRadius: "4px",
						fontFamily: EXPLORER_FONTS.mono,
						fontSize: "10.5px",
						lineHeight: 1.45,
						color: "#3B414C",
						overflow: "hidden",
						maxHeight: "54px",
						whiteSpace: "pre",
					}}
				>
					{previewLines}
				</pre>
			)}
		</div>
	);
}

export function PipelineOverviewView({
	settingIds,
	inputIds,
	outputIds,
	artifacts,
	phases,
	stages,
	hasInitialContract,
	activeWave,
	selType,
	selId,
	onSelectArtifact,
	onOpenFullArtifact,
	onOpenStage,
	onClearSelection,
	onResolveHold,
}: PipelineOverviewViewProps) {
	const visibleInputIds = filterArtifactIdsByWave(
		inputIds,
		artifacts,
		activeWave,
	);

	return (
		<main
			data-testid="pipeline-overview-view"
			onClick={() => onClearSelection()}
			style={{
				maxWidth: "1600px",
				margin: "0 auto",
				padding: "24px",
				display: "flex",
				gap: "24px",
				alignItems: "flex-start",
				fontFamily: EXPLORER_FONTS.sans,
			}}
		>
			{/* Left Column: Settings & Inputs */}
			<section
				aria-label="Settings and Inputs"
				onClick={(ev) => ev.stopPropagation()}
				style={{
					width: "296px",
					flexShrink: 0,
					display: "flex",
					flexDirection: "column",
					gap: "18px",
				}}
			>
				{/* Settings */}
				<div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
					<div
						style={{
							display: "flex",
							alignItems: "center",
							justifyContent: "space-between",
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
							Settings
						</span>
						<span
							style={{
								fontFamily: EXPLORER_FONTS.mono,
								fontSize: "11px",
								color: "#6B7280",
							}}
						>
							harness &amp; flags
						</span>
					</div>
					{settingIds.map((id) => {
						const art = artifacts[id];
						if (!art) {
							return null;
						}
						return (
							<ArtifactCardItem
								key={id}
								artifact={art}
								isSelected={selType === "artifact" && selId === id}
								onSelect={() => onSelectArtifact(id)}
								onExpand={() => onOpenFullArtifact(id)}
							/>
						);
					})}
				</div>

				{/* Inputs */}
				<div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
					<div
						style={{
							display: "flex",
							alignItems: "center",
							justifyContent: "space-between",
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
							Inputs · {visibleInputIds.length}
						</span>
						<span
							style={{
								fontFamily: EXPLORER_FONTS.mono,
								fontSize: "11px",
								color: "#6B7280",
							}}
						>
							spec · ontology · sources
						</span>
					</div>
					{visibleInputIds.map((id) => {
						const art = artifacts[id];
						if (!art) {
							return null;
						}
						return (
							<ArtifactCardItem
								key={id}
								artifact={art}
								isSelected={selType === "artifact" && selId === id}
								showPreviewLines
								onSelect={() => onSelectArtifact(id)}
								onExpand={() => onOpenFullArtifact(id)}
							/>
						);
					})}

					{!hasInitialContract && (
						<div
							data-testid="initial-contract-placeholder"
							style={{
								border: "1px dashed #C9CDD3",
								borderRadius: "6px",
								padding: "8px 11px",
								fontSize: "11.5px",
								color: "#6B7280",
							}}
						>
							<span style={{ fontFamily: EXPLORER_FONTS.mono }}>
								initial_contract
							</span>{" "}
							— none (Wave 0 cold start)
						</div>
					)}
				</div>
			</section>

			{/* Center Column: 8-Stage Pipeline */}
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

									return (
										<div
											key={stage.key}
											data-testid={`stage-card-${numStr}`}
											onClick={() => onOpenStage(idx)}
											onKeyDown={(ev) => {
												if (ev.key === "Enter" || ev.key === " ") {
													ev.preventDefault();
													onOpenStage(idx);
												}
											}}
											tabIndex={0}
											role="button"
											style={{
												display: "flex",
												gap: "12px",
												alignItems: "stretch",
												cursor: "pointer",
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
														background: "#FFFFFF",
														border: "1.5px solid #16181D",
														display: "flex",
														alignItems: "center",
														justifyContent: "center",
														fontFamily: EXPLORER_FONTS.mono,
														fontSize: "12px",
														fontWeight: 600,
														color: "#16181D",
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
													border: "1px solid #E3E5E8",
													borderRadius: "8px",
													padding: "13px 16px",
													display: "flex",
													flexDirection: "column",
													gap: "6px",
												}}
											>
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
															alignItems: "baseline",
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
															color: "#6B7280",
														}}
													>
														{stage.key} · {stage.persona} · {stage.tier}
													</span>
													<span
														style={{
															fontFamily: EXPLORER_FONTS.mono,
															fontSize: "10.5px",
															fontWeight: 500,
															padding: "2px 8px",
															borderRadius: "999px",
															background: tagStyle.bg,
															color: tagStyle.fg,
														}}
													>
														{stage.tag}
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

			{/* Right Column: Outputs */}
			<section
				aria-label="Final Pipeline Outputs"
				onClick={(ev) => ev.stopPropagation()}
				style={{
					width: "288px",
					flexShrink: 0,
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
						Outputs · {outputIds.length}
					</span>
					<span
						style={{
							fontFamily: EXPLORER_FONTS.mono,
							fontSize: "11px",
							color: "#6B7280",
						}}
					>
						CAS-sealed
					</span>
				</div>
				{outputIds.map((id) => {
					const art = artifacts[id];
					if (!art) {
						return null;
					}
					return (
						<ArtifactCardItem
							key={id}
							artifact={art}
							isSelected={selType === "artifact" && selId === id}
							showPreviewLines
							onSelect={() => onSelectArtifact(id)}
							onExpand={() => onOpenFullArtifact(id)}
						/>
					);
				})}
			</section>

			{/* Contextual Inspector */}
			<div onClick={(ev) => ev.stopPropagation()}>
				<InspectorPanel
					view="pipeline"
					selType={selType}
					selId={selId}
					stages={stages}
					artifacts={artifacts}
					onClearSelection={onClearSelection}
					onOpenFullArtifact={onOpenFullArtifact}
					onResolveHold={onResolveHold}
				/>
			</div>
		</main>
	);
}
