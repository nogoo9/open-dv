/**
 * Inputs Column for Pipeline Overview (User Directive R9 Modularity).
 */

import type { Artifact, WaveFilterId } from "@opendv/protocol-ts";
import { EXPLORER_FONTS } from "../design-tokens";
import type { ExplorerSelectionType } from "../types";
import { ArtifactCardItem } from "./ArtifactCardItem";

export interface OverviewInputsColumnProps {
	visibleInputIds: readonly string[];
	artifacts: Readonly<Record<string, Artifact>>;
	hasInitialContract: boolean;
	selType: ExplorerSelectionType;
	selId: string | number | null;
	onSelectArtifact: (artifactId: string) => void;
	onOpenFullArtifact: (artifactId: string) => void;
}

export function OverviewInputsColumn({
	visibleInputIds,
	artifacts,
	hasInitialContract,
	selType,
	selId,
	onSelectArtifact,
	onOpenFullArtifact,
}: OverviewInputsColumnProps) {
	return (
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
	);
}
