/**
 * Artifact Card Item for Pipeline Overview (User Directive R9 Modularity).
 */

import type { Artifact } from "@opendv/protocol-ts";
import {
	EXPLORER_FONTS,
	EXPLORER_GLYPHS,
	getGlyphCssStyle,
} from "../design-tokens";

export function buildCardPreviewLines(artifact: Artifact): string {
	if (artifact.viz === "table" && artifact.cols && artifact.rows) {
		return [
			artifact.cols.join(" | "),
			...artifact.rows.slice(0, 2).map((r) => r.join(" | ")),
		].join("\n");
	}
	return artifact.raw.split("\n").slice(0, 3).join("\n");
}

export interface ArtifactCardItemProps {
	artifact: Artifact;
	isSelected: boolean;
	showPreviewLines?: boolean | undefined;
	onSelect: () => void;
	onExpand: () => void;
}

export function ArtifactCardItem({
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
				} else if (ev.key === "ArrowDown") {
					ev.preventDefault();
					const current = ev.currentTarget;
					const container = current.closest("section");
					const cards = Array.from(
						container?.querySelectorAll<HTMLElement>(
							'[data-testid^="artifact-card-"]',
						) ?? [],
					);
					const idx = cards.indexOf(current);
					if (idx >= 0 && idx < cards.length - 1) {
						cards[idx + 1]?.focus();
					}
				} else if (ev.key === "ArrowUp") {
					ev.preventDefault();
					const current = ev.currentTarget;
					const container = current.closest("section");
					const cards = Array.from(
						container?.querySelectorAll<HTMLElement>(
							'[data-testid^="artifact-card-"]',
						) ?? [],
					);
					const idx = cards.indexOf(current);
					if (idx > 0) {
						cards[idx - 1]?.focus();
					}
				}
			}}
			tabIndex={0}
			role="button"
			aria-pressed={isSelected}
			aria-label={`Artifact ${artifact.name}, format ${artifact.fmt}, group ${artifact.group}`}
			style={{
				background: "#FFFFFF",
				border: `1.5px solid ${borderColor}`,
				outline: isSelected ? "2px solid #1D4ED8" : "none",
				outlineOffset: "1px",
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
