import type { Artifact, Stage } from "@opendv/protocol-ts";
import {
	EXPLORER_FONTS,
	EXPLORER_GLYPHS,
	getGlyphCssStyle,
} from "../design-tokens";
import type { ExplorerSelectionType } from "../types";

export interface StageFlowGridProps {
	stage: Stage;
	stageIdx: number;
	stages: readonly Stage[];
	artifacts: Readonly<Record<string, Artifact>>;
	selType: ExplorerSelectionType;
	selId: string | number | null;
	onSelectArtifact: (artifactId: string) => void;
	onSelectStep: (stepIdx: number) => void;
}

export function StageFlowGrid({
	stage,
	stageIdx,
	stages,
	artifacts,
	selType,
	selId,
	onSelectArtifact,
	onSelectStep,
}: StageFlowGridProps) {
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
									data-testid={`stage-step-card-${stepNumStr}`}
									onClick={() => onSelectStep(idx)}
									onKeyDown={(ev) => {
										if (ev.key === "Enter" || ev.key === " ") {
											ev.preventDefault();
											onSelectStep(idx);
										} else if (ev.key === "ArrowDown") {
											ev.preventDefault();
											const cards = Array.from(
												document.querySelectorAll<HTMLElement>(
													'[data-testid^="stage-step-card-"]',
												),
											);
											const currentIdx = cards.indexOf(ev.currentTarget);
											if (currentIdx >= 0 && currentIdx < cards.length - 1) {
												cards[currentIdx + 1]?.focus();
											}
										} else if (ev.key === "ArrowUp") {
											ev.preventDefault();
											const cards = Array.from(
												document.querySelectorAll<HTMLElement>(
													'[data-testid^="stage-step-card-"]',
												),
											);
											const currentIdx = cards.indexOf(ev.currentTarget);
											if (currentIdx > 0) {
												cards[currentIdx - 1]?.focus();
											}
										}
									}}
									tabIndex={0}
									role="button"
									aria-pressed={isSelected}
									aria-label={`Step ${stepNumStr}: ${step.name}`}
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
	);
}
