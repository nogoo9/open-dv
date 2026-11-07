/**
 * View 2 — Stage Drill-Down (`StageDrillDownView.tsx`)
 * Cohesive container coordinating StageHeader, StageComponentsColumn,
 * StageFlowGrid, and InspectorPanel.
 */

import type {
	Artifact,
	HoldResolutionParams,
	Stage,
} from "@opendv/protocol-ts";
import { InspectorPanel } from "../InspectorPanel";
import { EXPLORER_FONTS } from "../design-tokens";
import type { ExplorerSelectionType } from "../types";
import { StageComponentsColumn } from "./StageComponentsColumn";
import { StageFlowGrid } from "./StageFlowGrid";
import { StageHeader } from "./StageHeader";

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
	onResolveHold?: ((params: HoldResolutionParams) => void) | undefined;
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

	return (
		<main
			data-testid="stage-drilldown-view"
			onClick={() => onClearSelection()}
			onKeyDown={(ev) => {
				if (ev.key === "Escape") {
					ev.preventDefault();
					onGoPipeline();
				} else if (
					(ev.key === "ArrowLeft" && ev.altKey) ||
					(ev.key === "ArrowLeft" && ev.target === ev.currentTarget)
				) {
					ev.preventDefault();
					onPrevStage();
				} else if (
					(ev.key === "ArrowRight" && ev.altKey) ||
					(ev.key === "ArrowRight" && ev.target === ev.currentTarget)
				) {
					ev.preventDefault();
					onNextStage();
				}
			}}
			tabIndex={-1}
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
			<StageHeader
				stage={stage}
				stageIdx={stageIdx}
				onGoPipeline={onGoPipeline}
				onPrevStage={onPrevStage}
				onNextStage={onNextStage}
			/>

			<div style={{ display: "flex", gap: "20px", alignItems: "flex-start" }}>
				<StageComponentsColumn
					stage={stage}
					selType={selType}
					selId={selId}
					onSelectComponent={onSelectComponent}
				/>

				<StageFlowGrid
					stage={stage}
					stageIdx={stageIdx}
					stages={stages}
					artifacts={artifacts}
					selType={selType}
					selId={selId}
					onSelectArtifact={onSelectArtifact}
					onSelectStep={onSelectStep}
				/>

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
