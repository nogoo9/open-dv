/**
 * Pipeline Run Progress Bar (`PipelineRunProgressBar.tsx`)
 *
 * Displays overall pipeline run completion progress, live status announcements,
 * native semantic `<progress>` bar, visual progress fill, and 8-stage stepper pills.
 * Implements `modern-web-guidance` progress bar accessibility & styling best practices.
 */

import type { Stage } from "@opendv/protocol-ts";
import { EXPLORER_FONTS } from "../design-tokens";
import type { ExplorerRunState } from "../types";

export interface PipelineRunProgressBarProps {
	stages: readonly Stage[];
	runState: ExplorerRunState;
	activeRunningStageIdx: number | null;
	statusBannerText: string;
	streamedTokens?: number | undefined;
	eventCount?: number | undefined;
	onSelectStage?: ((stageIdx: number) => void) | undefined;
}

export type StepperPillState =
	| "active"
	| "hold"
	| "failed"
	| "completed"
	| "queued";

interface RunBadgeSpec {
	label: "RUNNING" | "PAUSED ON HOLD" | "FAILED" | "SEALED";
	icon: string;
	fg: string;
	bg: string;
	border: string;
}

function getRunBadgeSpec(runState: ExplorerRunState): RunBadgeSpec {
	switch (runState) {
		case "running":
			return {
				label: "RUNNING",
				icon: "●",
				fg: "#1D4ED8",
				bg: "#DBEAFE",
				border: "#93C5FD",
			};
		case "hold":
			return {
				label: "PAUSED ON HOLD",
				icon: "⏸",
				fg: "#B45309",
				bg: "#FEF3C7",
				border: "#FCD34D",
			};
		case "failed":
			return {
				label: "FAILED",
				icon: "✕",
				fg: "#B91C1C",
				bg: "#FEE2E2",
				border: "#FCA5A5",
			};
		case "sealed":
			return {
				label: "SEALED",
				icon: "✓",
				fg: "#15803D",
				bg: "#DCFCE7",
				border: "#86EFAC",
			};
	}
}

export function computeCompletedStagesCount(
	stages: readonly Stage[],
	runState: ExplorerRunState,
	activeRunningStageIdx: number | null,
): number {
	const total = stages.length;
	if (total === 0) return 0;

	if (runState === "sealed") {
		const passedCount = stages.filter(
			(s) =>
				s.tag === "PASS" ||
				s.tag === "AUTO-FIX" ||
				s.tag === "AUTO_REPAIRED" ||
				s.tag === "ASK-USER → resolved",
		).length;
		return passedCount > 0 ? passedCount : total;
	}

	if (runState === "running") {
		const raw =
			activeRunningStageIdx !== null
				? activeRunningStageIdx
				: stages.filter((s) => s.tag === "PASS").length;
		return Math.max(0, Math.min(total, raw));
	}

	if (runState === "hold") {
		const holdIdx = stages.findIndex((s) => s.tag === "ASK-USER");
		if (holdIdx >= 0) return holdIdx;
		if (activeRunningStageIdx !== null) {
			return Math.max(0, Math.min(total, activeRunningStageIdx));
		}
		return stages.filter((s) => s.tag === "PASS").length;
	}

	// runState === "failed"
	const failedIdx = stages.findIndex((s) => s.tag === "REJECT-WITH-TRACE");
	if (failedIdx >= 0) return failedIdx;
	if (activeRunningStageIdx !== null) {
		return Math.max(0, Math.min(total, activeRunningStageIdx));
	}
	return stages.filter((s) => s.tag === "PASS").length;
}

export function getStepperPillState(
	stage: Stage,
	idx: number,
	runState: ExplorerRunState,
	activeRunningStageIdx: number | null,
	completedCount: number,
): StepperPillState {
	if (runState === "sealed") {
		return "completed";
	}
	if (runState === "running" && activeRunningStageIdx === idx) {
		return "active";
	}
	if (stage.tag === "ASK-USER") {
		return "hold";
	}
	if (stage.tag === "REJECT-WITH-TRACE") {
		return "failed";
	}
	if (
		(activeRunningStageIdx !== null && idx < activeRunningStageIdx) ||
		((runState === "hold" || runState === "failed") && idx < completedCount)
	) {
		return "completed";
	}
	if (
		runState === "running" &&
		activeRunningStageIdx !== null &&
		idx > activeRunningStageIdx
	) {
		return "queued";
	}
	return "queued";
}

function getStepperSymbol(state: StepperPillState): string {
	switch (state) {
		case "active":
			return "●";
		case "hold":
			return "⏸";
		case "failed":
			return "✕";
		case "completed":
			return "✓";
		case "queued":
			return "○";
	}
}

export function PipelineRunProgressBar({
	stages,
	runState,
	activeRunningStageIdx,
	statusBannerText,
	streamedTokens,
	eventCount,
	onSelectStage,
}: PipelineRunProgressBarProps) {
	const totalStages = Math.max(1, stages.length);
	const completedCount = computeCompletedStagesCount(
		stages,
		runState,
		activeRunningStageIdx,
	);
	const progressPercent = Math.round((completedCount / totalStages) * 100);
	const badge = getRunBadgeSpec(runState);

	const activeStage =
		activeRunningStageIdx !== null ? stages[activeRunningStageIdx] : undefined;
	const activeStageShortCode = activeStage?.key.slice(0, 8);

	const totalStr = String(stages.length).padStart(2, "0");
	const summaryLabel =
		runState === "running" && activeRunningStageIdx !== null
			? `Stage ${String(activeRunningStageIdx + 1).padStart(2, "0")} of ${totalStr} · ${progressPercent}%`
			: `${completedCount} / ${stages.length} Stages Complete · ${progressPercent}%`;

	return (
		<section
			data-testid="pipeline-run-progress-bar"
			aria-label="Pipeline run progress"
			style={{
				background: "#FFFFFF",
				border: "1px solid #E3E5E8",
				borderRadius: "8px",
				padding: "12px 16px",
				display: "flex",
				flexDirection: "column",
				gap: "10px",
				fontFamily: EXPLORER_FONTS.sans,
			}}
		>
			{/* Polite live status region (modern-web-guidance: announce status without stealing focus) */}
			{/* biome-ignore lint/a11y/useSemanticElements: explicit role="status" + aria-live="polite" required by spec */}
			<div
				role="status"
				aria-live="polite"
				style={{
					display: "flex",
					alignItems: "center",
					justifyContent: "space-between",
					flexWrap: "wrap",
					gap: "10px",
				}}
			>
				<div
					style={{
						display: "flex",
						alignItems: "center",
						flexWrap: "wrap",
						gap: "10px",
					}}
				>
					<span
						data-testid="pipeline-run-status-badge"
						style={{
							fontFamily: EXPLORER_FONTS.mono,
							fontSize: "11px",
							fontWeight: 600,
							padding: "3px 9px",
							borderRadius: "999px",
							color: badge.fg,
							background: badge.bg,
							border: `1px solid ${badge.border}`,
							display: "inline-flex",
							alignItems: "center",
							gap: "5px",
						}}
					>
						<span aria-hidden="true">{badge.icon}</span>
						<span>{badge.label}</span>
					</span>

					<span
						data-testid="pipeline-progress-summary"
						style={{
							fontFamily: EXPLORER_FONTS.mono,
							fontSize: "12px",
							fontWeight: 600,
							color: "#16181D",
						}}
					>
						{summaryLabel}
					</span>

					{activeStage && activeStageShortCode && (
						<span
							data-testid="pipeline-active-stage-pill"
							style={{
								fontFamily: EXPLORER_FONTS.mono,
								fontSize: "11px",
								fontWeight: 500,
								padding: "2px 8px",
								borderRadius: "6px",
								background: "#EFF6FF",
								color: "#1D4ED8",
								border: "1px solid #BFDBFE",
							}}
						>
							{activeStageShortCode} · {activeStage.persona}
						</span>
					)}

					{statusBannerText && (
						<span
							data-testid="pipeline-status-banner-text"
							style={{
								fontSize: "12px",
								color: "#525866",
							}}
						>
							{statusBannerText}
						</span>
					)}
				</div>

				<div
					style={{
						display: "flex",
						alignItems: "center",
						gap: "8px",
						fontFamily: EXPLORER_FONTS.mono,
						fontSize: "11px",
						color: "#525866",
					}}
				>
					{eventCount !== undefined && eventCount > 0 && (
						<span
							data-testid="pipeline-telemetry-events"
							style={{
								padding: "2px 7px",
								borderRadius: "4px",
								background: "#F2F3F5",
							}}
						>
							{eventCount} events
						</span>
					)}
					{streamedTokens !== undefined && streamedTokens > 0 && (
						<span
							data-testid="pipeline-telemetry-tokens"
							style={{
								padding: "2px 7px",
								borderRadius: "4px",
								background: "#F2F3F5",
							}}
						>
							{streamedTokens} tokens
						</span>
					)}
				</div>
			</div>

			{/* Native semantic <progress> + custom visual track/fill bar (modern-web-guidance) */}
			<div style={{ position: "relative", width: "100%" }}>
				<progress
					data-testid="pipeline-native-progress"
					aria-label="Pipeline stage completion progress"
					max={stages.length}
					value={completedCount}
					style={{
						appearance: "none",
						border: "0 solid",
						overflow: "clip",
						width: "100%",
						height: "6px",
						borderRadius: "999px",
						background: "#E3E5E8",
						display: "block",
						opacity: 0,
						position: "absolute",
						inset: 0,
						pointerEvents: "none",
					}}
				/>
				<div
					aria-hidden="true"
					style={{
						width: "100%",
						height: "6px",
						borderRadius: "999px",
						background: "#EEF0F2",
						overflow: "clip",
					}}
				>
					<div
						data-testid="pipeline-progress-fill"
						style={{
							width: `${progressPercent}%`,
							height: "100%",
							borderRadius: "999px",
							background: badge.fg,
							transition: "width 0.25s ease",
						}}
					/>
				</div>
			</div>

			{/* 8-Stage Stepper Pills (00..07) */}
			<nav
				aria-label="Pipeline stage steps"
				style={{
					display: "grid",
					gridTemplateColumns: `repeat(${Math.max(1, stages.length)}, minmax(0, 1fr))`,
					gap: "6px",
				}}
			>
				{stages.map((stage, idx) => {
					const numStr = String(idx).padStart(2, "0");
					const stepState = getStepperPillState(
						stage,
						idx,
						runState,
						activeRunningStageIdx,
						completedCount,
					);
					const symbol = getStepperSymbol(stepState);

					const isStepActive = stepState === "active";
					const isStepHold = stepState === "hold";
					const isStepFailed = stepState === "failed";
					const isStepCompleted = stepState === "completed";

					const pillBg = isStepActive
						? "#EFF6FF"
						: isStepHold
							? "#FFFBEB"
							: isStepFailed
								? "#FEF2F2"
								: isStepCompleted
									? "#F0FDF4"
									: "#F8FAFC";
					const pillBorder = isStepActive
						? "1.5px solid #1D4ED8"
						: isStepHold
							? "1px solid #F59E0B"
							: isStepFailed
								? "1px solid #EF4444"
								: isStepCompleted
									? "1px solid #86EFAC"
									: "1px solid #E2E8F0";
					const pillColor = isStepActive
						? "#1D4ED8"
						: isStepHold
							? "#B45309"
							: isStepFailed
								? "#B91C1C"
								: isStepCompleted
									? "#15803D"
									: "#64748B";

					return (
						<button
							key={stage.key}
							type="button"
							data-testid={`progress-step-${numStr}`}
							data-step-state={stepState}
							aria-current={isStepActive ? "step" : undefined}
							aria-label={`Stage ${numStr}: ${stage.name} (${stepState})`}
							onClick={() => onSelectStage?.(idx)}
							style={{
								display: "flex",
								alignItems: "center",
								justifyContent: "center",
								gap: "5px",
								padding: "5px 6px",
								borderRadius: "6px",
								background: pillBg,
								border: pillBorder,
								color: pillColor,
								fontFamily: EXPLORER_FONTS.mono,
								fontSize: "11px",
								fontWeight: isStepActive ? 600 : 500,
								cursor: "pointer",
							}}
						>
							<span aria-hidden="true">{symbol}</span>
							<span>{numStr}</span>
						</button>
					);
				})}
			</nav>
		</section>
	);
}
