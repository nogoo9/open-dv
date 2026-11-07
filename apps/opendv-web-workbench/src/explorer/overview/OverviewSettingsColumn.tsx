/**
 * Settings Column for Pipeline Overview (User Directive R9 Modularity).
 */

import type { Artifact } from "@opendv/protocol-ts";
import { EXPLORER_FONTS } from "../design-tokens";
import type { ExplorerSelectionType } from "../types";
import { ArtifactCardItem } from "./ArtifactCardItem";

export interface OverviewSettingsColumnProps {
	settingIds: readonly string[];
	artifacts: Readonly<Record<string, Artifact>>;
	selType: ExplorerSelectionType;
	selId: string | number | null;
	onSelectArtifact: (artifactId: string) => void;
	onOpenFullArtifact: (artifactId: string) => void;
}

export function OverviewSettingsColumn({
	settingIds,
	artifacts,
	selType,
	selId,
	onSelectArtifact,
	onOpenFullArtifact,
}: OverviewSettingsColumnProps) {
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
	);
}
