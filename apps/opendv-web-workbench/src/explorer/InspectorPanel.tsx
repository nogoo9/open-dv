/**
 * Contextual Inspector Panel (`InspectorPanel.tsx`)
 * Shared right-hand inspector for Pipeline Overview and Stage Drill-Down views
 * (`docs/design/open-dv-workbench/HANDOFF.md`).
 */

import {
	type Artifact,
	type HoldResolutionParams,
	type Stage,
	getArtifactConsumedBy,
	getArtifactProducedBy,
} from "@opendv/protocol-ts";
import {
	EXPLORER_FONTS,
	EXPLORER_GLYPHS,
	EXPLORER_VIZ_LABELS,
	type GlyphStyleSpec,
	getGlyphCssStyle,
} from "./design-tokens";
import type {
	ExplorerSelectionType,
	ExplorerViewLevel,
} from "./explorer-store";

export interface InspectorPanelProps {
	view: ExplorerViewLevel;
	selType: ExplorerSelectionType;
	selId: string | number | null;
	stages: readonly Stage[];
	currentStage?: Stage | undefined;
	artifacts: Readonly<Record<string, Artifact>>;
	onClearSelection: () => void;
	onOpenFullArtifact: (artifactId: string) => void;
	onResolveHold?: ((params: HoldResolutionParams) => void) | undefined;
}

function buildArtifactPreview(artifact: Artifact): string {
	if (artifact.id === "extract" && artifact.doc) {
		return artifact.doc.extractions
			.map(
				(e) =>
					`"${e.quote}"\n  → ${e.tier} · ${e.conf}${e.tier === "Tier-U3" ? " · hold" : ""}`,
			)
			.join("\n");
	}
	if (artifact.viz === "table" && artifact.cols && artifact.rows) {
		return [
			artifact.cols.join(" | "),
			...artifact.rows.slice(0, 6).map((r) => r.join(" | ")),
		].join("\n");
	}
	return artifact.raw.split("\n").slice(0, 10).join("\n");
}

export function InspectorPanel({
	view,
	selType,
	selId,
	stages,
	currentStage,
	artifacts,
	onClearSelection,
	onOpenFullArtifact,
	onResolveHold,
}: InspectorPanelProps) {
	let hasSelection = false;
	let isArtifact = false;
	let glyph: GlyphStyleSpec = EXPLORER_GLYPHS.data;
	let kindLabel = "";
	let title = "";
	let desc = "";
	let meta: { k: string; v: string }[] = [];
	let preview = "";
	let vizName = "";
	let selectedArtifactId: string | null = null;
	let activeHoldId: string | null = null;

	if (selType === "artifact" && typeof selId === "string" && artifacts[selId]) {
		const art = artifacts[selId];
		hasSelection = true;
		isArtifact = true;
		selectedArtifactId = selId;
		glyph =
			art.group === "Setting"
				? EXPLORER_GLYPHS.setting
				: EXPLORER_GLYPHS.data;
		kindLabel = `Data · ${art.group}`;
		title = art.name;
		desc = art.desc;
		meta = [
			{ k: "Type", v: art.type },
			{ k: "Format", v: art.fmt },
			{ k: "Produced by", v: getArtifactProducedBy(stages, selId) },
			{ k: "Consumed by", v: getArtifactConsumedBy(stages, selId) },
		];
		preview = buildArtifactPreview(art);
		vizName = EXPLORER_VIZ_LABELS[art.viz];
		const u3Hold = art.doc?.extractions.find(
			(e) => e.tier === "Tier-U3" && e.holdId,
		);
		if (u3Hold?.holdId) {
			activeHoldId = u3Hold.holdId;
		}
	} else if (
		view === "stage" &&
		selType === "step" &&
		typeof selId === "number" &&
		currentStage?.steps[selId]
	) {
		const step = currentStage.steps[selId];
		hasSelection = true;
		glyph = EXPLORER_GLYPHS.control;
		kindLabel = `Control · step ${selId + 1} of ${currentStage.steps.length}`;
		title = step.name;
		desc = step.desc;
		const stepKind =
			step.kind === "decision"
				? "decision"
				: step.kind === "loop"
					? "loop"
					: "action";
		meta = [{ k: "Kind", v: stepKind }];
		if (step.branch) {
			meta.push({ k: "Branches", v: step.branch });
		}
		meta.push({
			k: "Reads",
			v:
				step.reads.map((r) => artifacts[r]?.name ?? r).join(", ") || "—",
		});
		meta.push({
			k: "Writes",
			v:
				step.writes.map((r) => artifacts[r]?.name ?? r).join(", ") || "—",
		});
		if (
			currentStage.key === "STAGE_02_SCOUT_UNSTRUCTURED" &&
			step.name.toLowerCase().includes("steward hold")
		) {
			activeHoldId = "hold-tier-u3-rheinland-precision";
		}
	} else if (
		view === "stage" &&
		selType === "component" &&
		typeof selId === "number" &&
		currentStage?.comps[selId]
	) {
		const comp = currentStage.comps[selId];
		hasSelection = true;
		glyph = EXPLORER_GLYPHS.component;
		kindLabel = `Component · ${comp[1]}`;
		title = comp[0];
		desc = comp[2];
		meta = [
			{ k: "Source", v: comp[3] },
			{ k: "Stage", v: currentStage.key },
		];
	}

	if (!hasSelection && view === "pipeline") {
		return null;
	}

	return (
		<aside
			aria-label="Details Inspector"
			data-testid="explorer-inspector"
			style={{
				width: "340px",
				flexShrink: 0,
				position: "sticky",
				top: "24px",
			}}
		>
			{!hasSelection ? (
				<div
					data-testid="inspector-empty-state"
					style={{
						background: "#FFFFFF",
						border: "1px dashed #C9CDD3",
						borderRadius: "8px",
						padding: "16px",
						fontSize: "12.5px",
						color: "#6B7280",
						lineHeight: 1.5,
					}}
				>
					Select a step, a data item or a component to see what it does.
				</div>
			) : (
				<div
					style={{
						background: "#FFFFFF",
						border: "1px solid #E3E5E8",
						borderRadius: "8px",
						padding: "16px",
						display: "flex",
						flexDirection: "column",
						gap: "12px",
					}}
				>
					{/* Header */}
					<div
						style={{
							display: "flex",
							alignItems: "center",
							justifyContent: "space-between",
							gap: "8px",
						}}
					>
						<div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
							<span style={getGlyphCssStyle(glyph, 10)} />
							<span
								style={{
									fontSize: "11px",
									fontWeight: 600,
									textTransform: "uppercase",
									letterSpacing: "0.06em",
									color: "#6B7280",
								}}
							>
								{kindLabel}
							</span>
						</div>
						<button
							type="button"
							data-testid="btn-inspector-close"
							onClick={onClearSelection}
							style={{
								all: "unset",
								cursor: "pointer",
								fontSize: "12px",
								color: "#6B7280",
								padding: "2px 6px",
							}}
						>
							Close
						</button>
					</div>

					{/* Title */}
					<div
						data-testid="inspector-title"
						style={{
							fontFamily: EXPLORER_FONTS.mono,
							fontSize: "14px",
							fontWeight: 600,
							color: "#16181D",
							wordBreak: "break-word",
						}}
					>
						{title}
					</div>

					{/* Description */}
					<div
						style={{
							fontSize: "12.5px",
							color: "#3B414C",
							lineHeight: 1.5,
						}}
					>
						{desc}
					</div>

					{/* Metadata definition list */}
					<dl
						style={{
							margin: 0,
							borderTop: "1px solid #ECEEF1",
							paddingTop: "10px",
							display: "flex",
							flexDirection: "column",
							gap: "6px",
						}}
					>
						{meta.map((row) => (
							<div
								key={row.k}
								style={{
									display: "flex",
									justifyContent: "space-between",
									gap: "10px",
									fontSize: "12px",
								}}
							>
								<dt style={{ color: "#6B7280" }}>{row.k}</dt>
								<dd
									style={{
										margin: 0,
										fontFamily: EXPLORER_FONTS.mono,
										fontSize: "11.5px",
										color: "#16181D",
										textAlign: "right",
									}}
								>
									{row.v}
								</dd>
							</div>
						))}
					</dl>

					{/* Steward Hold Quick Resolution inside Inspector */}
					{activeHoldId && onResolveHold && (
						<div
							data-testid="inspector-steward-hold-box"
							style={{
								padding: "10px 12px",
								borderRadius: "6px",
								background: "#FFF9F0",
								border: "1px solid #F3D9B1",
								display: "flex",
								flexDirection: "column",
								gap: "8px",
							}}
						>
							<div
								style={{
									fontFamily: EXPLORER_FONTS.mono,
									fontSize: "11px",
									fontWeight: 600,
									color: "#8A3B07",
								}}
							>
								Steward hold · {activeHoldId}
							</div>
							<div style={{ display: "flex", gap: "6px" }}>
								<button
									type="button"
									data-testid="btn-inspector-approve-hold"
									onClick={() =>
										onResolveHold({
											holdId: activeHoldId as string,
											action: "APPROVE",
											selectedCandidateId: "TAX-DE-800202",
										})
									}
									style={{
										flex: 1,
										fontFamily: EXPLORER_FONTS.sans,
										fontSize: "11.5px",
										fontWeight: 500,
										padding: "5px 8px",
										borderRadius: "5px",
										border: "1px solid #16181D",
										background: "#16181D",
										color: "#FFFFFF",
										cursor: "pointer",
									}}
								>
									Approve candidate
								</button>
								<button
									type="button"
									data-testid="btn-inspector-reject-hold"
									onClick={() =>
										onResolveHold({
											holdId: activeHoldId as string,
											action: "REJECT",
										})
									}
									style={{
										flex: 1,
										fontFamily: EXPLORER_FONTS.sans,
										fontSize: "11.5px",
										fontWeight: 500,
										padding: "5px 8px",
										borderRadius: "5px",
										border: "1px solid #E3E5E8",
										background: "#FDECEA",
										color: "#8F1D12",
										cursor: "pointer",
									}}
								>
									Reject
								</button>
							</div>
						</div>
					)}

					{/* Artifact Preview + Open Full View CTA */}
					{isArtifact && selectedArtifactId && (
						<div
							style={{
								borderTop: "1px solid #ECEEF1",
								paddingTop: "10px",
								display: "flex",
								flexDirection: "column",
								gap: "8px",
							}}
						>
							<div
								style={{
									fontSize: "11px",
									fontWeight: 600,
									textTransform: "uppercase",
									letterSpacing: "0.06em",
									color: "#6B7280",
								}}
							>
								Preview
							</div>
							<pre
								data-testid="inspector-artifact-preview"
								style={{
									margin: 0,
									padding: "10px",
									background: "#F6F7F8",
									border: "1px solid #E3E5E8",
									borderRadius: "6px",
									fontFamily: EXPLORER_FONTS.mono,
									fontSize: "11px",
									lineHeight: 1.5,
									color: "#16181D",
									overflow: "auto",
									maxHeight: "200px",
								}}
							>
								{preview}
							</pre>
							<button
								type="button"
								data-testid="btn-inspector-open-full-view"
								onClick={() => onOpenFullArtifact(selectedArtifactId)}
								style={{
									all: "unset",
									cursor: "pointer",
									textAlign: "center",
									padding: "8px 12px",
									borderRadius: "6px",
									background: "#16181D",
									color: "#FFFFFF",
									fontSize: "12.5px",
									fontWeight: 500,
								}}
							>
								Open full view ({vizName}) →
							</button>
						</div>
					)}
				</div>
			)}
		</aside>
	);
}
