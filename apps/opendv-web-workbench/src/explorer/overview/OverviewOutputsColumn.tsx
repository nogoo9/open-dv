/**
 * Outputs Column for Pipeline Overview (User Directive R9 Modularity).
 */

import type { Artifact } from "@opendv/protocol-ts";
import { EXPLORER_FONTS } from "../design-tokens";
import type { ExplorerSelectionType } from "../types";
import { ArtifactCardItem } from "./ArtifactCardItem";

export interface OverviewOutputsColumnProps {
	outputIds: readonly string[];
	artifacts: Readonly<Record<string, Artifact>>;
	selType: ExplorerSelectionType;
	selId: string | number | null;
	onSelectArtifact: (artifactId: string) => void;
	onOpenFullArtifact: (artifactId: string) => void;
}

export function OverviewOutputsColumn({
	outputIds,
	artifacts,
	selType,
	selId,
	onSelectArtifact,
	onOpenFullArtifact,
}: OverviewOutputsColumnProps) {
	return (
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
	);
}
