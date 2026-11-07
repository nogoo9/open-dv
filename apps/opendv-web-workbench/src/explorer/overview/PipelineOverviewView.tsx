/**
 * View 1 — Pipeline Overview (`PipelineOverviewView.tsx`)
 * Coordinates Settings & Inputs column, 8-Stage Pipeline, Outputs column,
 * and contextual Inspector (User Directive R9 Modularity).
 */

import {
	type Artifact,
	type HoldResolutionParams,
	type PhaseSpec,
	type Stage,
	type WaveFilterId,
	filterArtifactIdsByWave,
} from "@opendv/protocol-ts";
import { InspectorPanel } from "../InspectorPanel";
import { EXPLORER_FONTS } from "../design-tokens";
import type { ExplorerSelectionType } from "../types";
import { OverviewInputsColumn } from "./OverviewInputsColumn";
import { OverviewOutputsColumn } from "./OverviewOutputsColumn";
import { OverviewSettingsColumn } from "./OverviewSettingsColumn";
import { OverviewStagesTrack } from "./OverviewStagesTrack";

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
				<OverviewSettingsColumn
					settingIds={settingIds}
					artifacts={artifacts}
					selType={selType}
					selId={selId}
					onSelectArtifact={onSelectArtifact}
					onOpenFullArtifact={onOpenFullArtifact}
				/>

				<OverviewInputsColumn
					visibleInputIds={visibleInputIds}
					artifacts={artifacts}
					hasInitialContract={hasInitialContract}
					selType={selType}
					selId={selId}
					onSelectArtifact={onSelectArtifact}
					onOpenFullArtifact={onOpenFullArtifact}
				/>
			</section>

			{/* Center Column: 8-Stage Pipeline */}
			<OverviewStagesTrack
				phases={phases}
				stages={stages}
				artifacts={artifacts}
				activeWave={activeWave}
				onOpenStage={onOpenStage}
			/>

			{/* Right Column: Outputs */}
			<OverviewOutputsColumn
				outputIds={outputIds}
				artifacts={artifacts}
				selType={selType}
				selId={selId}
				onSelectArtifact={onSelectArtifact}
				onOpenFullArtifact={onOpenFullArtifact}
			/>

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
