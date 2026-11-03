/**
 * View 2 — Stage Drill-Down (`StageDrillDownView.tsx`)
 * Stage Sub-header, Components Column, Control & Data Flow Column (`reads | step | writes`),
 * and Inspector Column (`docs/design/open-dv-workbench/HANDOFF.md`).
 */

import type { Artifact, Stage } from "@opendv/protocol-ts";
import { InspectorPanel } from "./InspectorPanel";
import {
	EXPLORER_FONTS,
	EXPLORER_GLYPHS,
	getGlyphCssStyle,
	getStatusTagStyle,
} from "./design-tokens";
import type { ExplorerSelectionType } from "./explorer-store";

export interface StageDrillDownViewProps {
	stageIdx: number;
	stages: readonly Stage[];
	artifacts: Readonly<Record<string, Artifact>>;
	selType: ExplorerSelectionType;
	selId: string | number | null;
	onGoPipeline: () => void;
	onPrevStage: () => void;
	onNextStage: () => void;
	onSelectArtifact: (artifactId: string) => void;
	onSelectStep: (stepIdx: number) => void;
	onSelectComponent: (compIdx: number) => void;
	onClearSelection: () => void;
	onOpenFullArtifact: (artifactId: string) => void;
	onResolveHold?: (params: {
		holdId: string;
		action: "APPROVE" | "REJECT";
		selectedCandidateId?: string;
		resolutionNotes?: string;
		eventId?: string;
	}) => void;
}

export function StageDrillDownView({
	stageIdx,
	stages,
	artifacts,
	selType,
	selId,
	onGoPipeline,
	onPrevStage,
	onNextStage,
	onSelectArtifact,
	onSelectStep,
	onSelectComponent,
	onClearSelection,
	onOpenFullArtifact,
	onResolveHold,
}: StageDrillDownViewProps) {
	const stage = stages[stageIdx] ?? stages[0];
	if (!stage) {
		return null;
	}
	const numStr = String(stageIdx).padStart(2, "0");
	const tagStyle = getStatusTagStyle(stage.tag);
	const nextLabel =
		stageIdx < stages.length - 1
			? (stages[stageIdx + 1]?.key ?? "pipeline outputs")
			: "pipeline outputs";

	const renderArtifactChip = (artifactId: string, compact = false) => {
		const art = artifacts[artifactId];
		if (!art) {
			return null;
		}
		const isSelected = selType === "artifact" && selId === artifactId;
		return (
			<button
				key={artifactId}
				type="button"
				data-testid={`stage-artifact-chip-${artifactId}`}
				onClick={(ev) => {
					ev.stopPropagation();
					onSelectArtifact(artifactId);
				}}
				style={{
					all: "unset",
					cursor: "pointer",
					display: "inline-flex",
					alignItems: "center",
					gap: compact ? "5px" : "6px",
					padding: compact ? "3px 8px" : "4px 9px",
					borderRadius: "5px",
					background: "#FFFFFF",
					border: `1.5px solid ${isSelected ? "#1D4ED8" : "#E3E5E8"}`,
					fontFamily: EXPLORER_FONTS.mono,
					fontSize: compact ? "11px" : "11.5px",
					color: "#16181D",
					maxWidth: compact ? "100%" : undefined,
				}}
			>
				<span
					style={{
						width: compact ? "7px" : "8px",
						height: compact ? "7px" : "8px",
						borderRadius: "2px",
						background: "#1D4ED8",
						flexShrink: 0,
					}}
				/>
				<span
					style={
						compact
							? {
									overflow: "hidden",
									textOverflow: "ellipsis",
									whiteSpace: "nowrap",
								}
							: undefined
					}
				>
					{art.name}
				</span>
			</button>
		);
	};

	return (
		<main
			data-testid="stage-drilldown-view"
			onClick={() => onClearSelection()}
			style={{
				maxWidth: "1600px",
				margin: "0 auto",
				padding: "20px 24px",
				display: "flex",
				flexDirection: "column",
				gap: "18px",
				fontFamily: EXPLORER_FONTS.sans,
			}}
		>
			{/* Stage Sub-header */}
			<div
				onClick={(ev) => ev.stopPropagation()}
				style={{
					background: "#FFFFFF",
					border: "1px solid #E3E5E8",
					borderRadius: "8px",
					padding: "16px 20px",
					display: "flex",
					flexDirection: "column",
					gap: "10px",
				}}
			>
				<div
					style={{
						display: "flex",
						alignItems: "center",
						justifyContent: "space-between",
						gap: "12px",
					}}
				>
					<div style={{ display: "flex", alignItems: "center", gap: "12px" }}>
						<button
							type="button"
							data-testid="btn-stage-all-stages"
							onClick={onGoPipeline}
							style={{
								all: "unset",
								cursor: "pointer",
								fontSize: "12.5px",
								fontWeight: 500,
								color: "#1D4ED8",
								padding: "4px 8px",
								borderRadius: "4px",
							}}
						>
							← All stages
						</button>
						<span style={{ color: "#D4D7DC" }}>|</span>
						<div
							style={{
								width: "34px",
								height: "34px",
								borderRadius: "999px",
								border: "1.5px solid #16181D",
								display: "flex",
								alignItems: "center",
								justifyContent: "center",
								fontFamily: EXPLORER_FONTS.mono,
								fontSize: "13px",
								fontWeight: 600,
							}}
						>
							{numStr}
						</div>
						<div>
							<div
								style={{
									display: "flex",
									alignItems: "center",
									gap: "10px",
								}}
							>
								<h1
									style={{
										margin: 0,
										fontSize: "20px",
										fontWeight: 600,
										letterSpacing: "-0.01em",
										color: "#16181D",
									}}
								>
									{stage.name}
								</h1>
								<span
									style={{
										fontFamily: EXPLORER_FONTS.mono,
										fontSize: "11px",
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
									fontFamily: EXPLORER_FONTS.mono,
									fontSize: "11.5px",
									color: "#6B7280",
									marginTop: "2px",
								}}
							>
								{stage.key} · {stage.persona} · {stage.tier}
							</div>
						</div>
					</div>

					{/* Prev / Next Stage Navigation */}
					<div style={{ display: "flex", alignItems: "center", gap: "6px" }}>
						<button
							type="button"
							data-testid="btn-prev-stage"
							onClick={onPrevStage}
							aria-label="Previous stage"
							style={{
								all: "unset",
								cursor: "pointer",
								fontFamily: EXPLORER_FONTS.mono,
								fontSize: "12px",
								padding: "5px 10px",
								border: "1px solid #E3E5E8",
								borderRadius: "6px",
								color: "#16181D",
								background: "#FFFFFF",
							}}
						>
							←
						</button>
						<button
							type="button"
							data-testid="btn-next-stage"
							onClick={onNextStage}
							aria-label="Next stage"
							style={{
								all: "unset",
								cursor: "pointer",
								fontFamily: EXPLORER_FONTS.mono,
								fontSize: "12px",
								padding: "5px 10px",
								border: "1px solid #E3E5E8",
								borderRadius: "6px",
								color: "#16181D",
								background: "#FFFFFF",
							}}
						>
							→
						</button>
					</div>
				</div>

				<div
					style={{
						fontSize: "13.5px",
						color: "#3B414C",
						lineHeight: 1.5,
						maxWidth: "76ch",
					}}
				>
					{stage.desc}
				</div>
			</div>

			{/* Stage Body: Components + Control & Data Flow + Inspector */}
			<div style={{ display: "flex", gap: "20px", alignItems: "flex-start" }}>
				{/* Left Column: Components */}
				<section
					aria-label="Stage Components"
					onClick={(ev) => ev.stopPropagation()}
					style={{
						width: "260px",
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
							Components
						</span>
						<span
							style={{
								fontFamily: EXPLORER_FONTS.mono,
								fontSize: "11px",
								color: "#6B7280",
							}}
						>
							agents &amp; modules
						</span>
					</div>

					{stage.comps.map((comp, idx) => {
						const isSelected = selType === "component" && selId === idx;
						return (
							<div
								key={`${comp[0]}-${idx}`}
								data-testid={`stage-component-${comp[0]}`}
								onClick={() => onSelectComponent(idx)}
								onKeyDown={(ev) => {
									if (ev.key === "Enter" || ev.key === " ") {
										ev.preventDefault();
										onSelectComponent(idx);
									}
								}}
								tabIndex={0}
								role="button"
								aria-pressed={isSelected}
								style={{
									background: "#FFFFFF",
									border: `1.5px solid ${isSelected ? "#1D4ED8" : "#E3E5E8"}`,
									borderRadius: "6px",
									padding: "9px 11px",
									cursor: "pointer",
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
										gap: "8px",
										minWidth: 0,
									}}
								>
									<span
										style={getGlyphCssStyle(EXPLORER_GLYPHS.component, 10)}
									/>
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
										{comp[0]}
									</span>
								</div>
								<span
									style={{
										fontFamily: EXPLORER_FONTS.mono,
										fontSize: "10.5px",
										color: "#6B7280",
										flexShrink: 0,
									}}
								>
									{comp[1]}
								</span>
							</div>
						);
					})}
				</section>

				{/* Center Column: Control & Data Flow */}
				<section
					aria-label="Control and Data Flow"
					onClick={(ev) => ev.stopPropagation()}
					style={{
						flex: 1,
						minWidth: 0,
						display: "flex",
						flexDirection: "column",
						gap: "12px",
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
							Control &amp; data flow
						</span>
						<span
							style={{
								fontFamily: EXPLORER_FONTS.mono,
								fontSize: "11px",
								color: "#6B7280",
							}}
						>
							reads ■ → ◆ step → ■ writes
						</span>
					</div>

					{/* Enters with strip */}
					<div
						data-testid="stage-enters-with-strip"
						style={{
							border: "1px dashed #C9CDD3",
							borderRadius: "6px",
							padding: "9px 12px",
							background: "#FAF9F6",
							display: "flex",
							alignItems: "center",
							gap: "10px",
							flexWrap: "wrap",
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
							Enters with
						</span>
						{stage.ins.map((id) => renderArtifactChip(id, false))}
					</div>

					{/* Steps sequence */}
					<div
						style={{
							display: "flex",
							flexDirection: "column",
							gap: "0px",
						}}
					>
						{stage.steps.map((step, idx) => {
							const stepNumStr = String(idx + 1).padStart(2, "0");
							const isSelected = selType === "step" && selId === idx;

							return (
								<div
									key={`${step.name}-${idx}`}
									data-testid={`stage-step-${stepNumStr}`}
									style={{ display: "flex", flexDirection: "column" }}
								>
									{/* 14px #B45309 vertical connector line between steps */}
									{idx > 0 && (
										<div
											style={{
												height: "14px",
												display: "grid",
												gridTemplateColumns:
													"180px minmax(260px, 1fr) 180px",
												gap: "12px",
											}}
										>
											<div />
											<div
												style={{
													display: "flex",
													justifyContent: "flex-start",
													paddingLeft: "22px",
												}}
											>
												<div
													style={{
														width: "1.5px",
														height: "14px",
														background: "#B45309",
													}}
												/>
											</div>
											<div />
										</div>
									)}

									<div
										style={{
											display: "grid",
											gridTemplateColumns:
												"180px minmax(260px, 1fr) 180px",
											gap: "12px",
											alignItems: "center",
										}}
									>
										{/* Left: reads ■ */}
										<div
											style={{
												display: "flex",
												flexDirection: "column",
												alignItems: "flex-end",
												gap: "4px",
											}}
										>
											{step.reads.map((id) => renderArtifactChip(id, true))}
										</div>

										{/* Center: ◆ step card */}
										<div
											onClick={() => onSelectStep(idx)}
											onKeyDown={(ev) => {
												if (ev.key === "Enter" || ev.key === " ") {
													ev.preventDefault();
													onSelectStep(idx);
												}
											}}
											tabIndex={0}
											role="button"
											aria-pressed={isSelected}
											style={{
												background: "#FFFFFF",
												border: `1.5px solid ${isSelected ? "#1D4ED8" : "#E3E5E8"}`,
												borderRadius: "6px",
												padding: "10px 12px",
												cursor: "pointer",
												display: "flex",
												flexDirection: "column",
												gap: "4px",
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
														{stepNumStr}
													</span>
													<span
														style={getGlyphCssStyle(
															EXPLORER_GLYPHS.control,
															10,
														)}
													/>
													<span
														style={{
															fontSize: "13px",
															fontWeight: 600,
															color: "#16181D",
														}}
													>
														{step.name}
													</span>
												</div>
											</div>

											{step.branch && (
												<div
													style={{
														fontFamily: EXPLORER_FONTS.mono,
														fontSize: "11px",
														color: "#8A3B07",
														background: "#FFF9F0",
														border: "1px solid #F3D9B1",
														borderRadius: "4px",
														padding: "3px 7px",
														marginTop: "2px",
													}}
												>
													{step.branch}
												</div>
											)}
										</div>

										{/* Right: ■ writes */}
										<div
											style={{
												display: "flex",
												flexDirection: "column",
												alignItems: "flex-start",
												gap: "4px",
											}}
										>
											{step.writes.map((id) => renderArtifactChip(id, true))}
										</div>
									</div>
								</div>
							);
						})}
					</div>

					{/* Hands off strip */}
					<div
						data-testid="stage-hands-off-strip"
						style={{
							border: "1px dashed #C9CDD3",
							borderRadius: "6px",
							padding: "9px 12px",
							background: "#FAF9F6",
							display: "flex",
							alignItems: "center",
							justifyContent: "space-between",
							gap: "10px",
							flexWrap: "wrap",
						}}
					>
						<div
							style={{
								display: "flex",
								alignItems: "center",
								gap: "10px",
								flexWrap: "wrap",
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
								Hands off
							</span>
							{stage.outs.map((id) => renderArtifactChip(id, false))}
						</div>
						<span
							style={{
								fontFamily: EXPLORER_FONTS.mono,
								fontSize: "11px",
								color: "#6B7280",
							}}
						>
							→ {nextLabel}
						</span>
					</div>
				</section>

				{/* Right Column: Inspector (always present in Stage view) */}
				<div onClick={(ev) => ev.stopPropagation()}>
					<InspectorPanel
						view="stage"
						selType={selType}
						selId={selId}
						stages={stages}
						currentStage={stage}
						artifacts={artifacts}
						onClearSelection={onClearSelection}
						onOpenFullArtifact={onOpenFullArtifact}
						onResolveHold={onResolveHold}
					/>
				</div>
			</div>
		</main>
	);
}
