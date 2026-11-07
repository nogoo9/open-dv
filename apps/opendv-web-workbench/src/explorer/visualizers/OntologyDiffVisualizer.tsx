/**
 * Ontology Diff Visualizer (`OntologyDiffVisualizer.tsx`)
 * Renders the `ontology_diff` artifact (structural differences between Graph A vN and vN+1:
 * added concepts, added relationships, added enumerations, refined concepts, and breaking changes)
 * (Phase P7 ONT-02 / IP-6).
 */

import type { Artifact } from "@opendv/protocol-ts";
import { useMemo } from "react";
import { EXPLORER_COLORS, EXPLORER_FONTS } from "../design-tokens";
import { DiffOperationsGrid } from "./DiffOperationsGrid";

export interface OntologyDiffData {
	base_version?: string | undefined;
	target_version?: string | undefined;
	base_sha256?: string | undefined;
	target_sha256?: string | undefined;
	added_concepts?: string[] | undefined;
	added_relationships?: string[] | undefined;
	added_enumerations?: string[] | undefined;
	refined_concepts?: string[] | undefined;
	breaking_changes?: string[] | undefined;
	diagnostics?: string[] | undefined;
}

export interface OntologyDiffVisualizerProps {
	artifact?: Artifact | undefined;
	data?: OntologyDiffData | undefined;
}

function parseDiffPayload(
	artifact?: Artifact,
	explicitData?: OntologyDiffData,
): OntologyDiffData {
	if (explicitData) {
		return explicitData;
	}
	if (!artifact?.raw) {
		return {};
	}
	try {
		return JSON.parse(artifact.raw) as OntologyDiffData;
	} catch {
		return { diagnostics: ["Failed to parse raw ontology diff JSON"] };
	}
}

export function OntologyDiffVisualizer({
	artifact,
	data: explicitData,
}: OntologyDiffVisualizerProps) {
	const data = useMemo(
		() => parseDiffPayload(artifact, explicitData),
		[artifact, explicitData],
	);

	const addedConcepts = data.added_concepts ?? [];
	const addedRelationships = data.added_relationships ?? [];
	const addedEnumerations = data.added_enumerations ?? [];
	const refinedConcepts = data.refined_concepts ?? [];
	const breakingChanges = data.breaking_changes ?? [];

	const isAdditive = breakingChanges.length === 0;

	return (
		<section
			aria-label="Ontology Structural Diff"
			data-testid="ontology-diff-visualizer"
			style={{
				display: "flex",
				flexDirection: "column",
				gap: "20px",
				padding: "16px",
				backgroundColor: EXPLORER_COLORS.bgSurface,
				borderRadius: "8px",
				fontFamily: EXPLORER_FONTS.sans,
				color: EXPLORER_COLORS.inkPrimary,
			}}
		>
			{/* Top Header */}
			<div
				style={{
					borderBottom: `1px solid ${EXPLORER_COLORS.borderLine}`,
					paddingBottom: "16px",
				}}
			>
				<div
					style={{
						display: "flex",
						alignItems: "center",
						justifyContent: "space-between",
						flexWrap: "wrap",
						gap: "12px",
					}}
				>
					<div>
						<h3
							data-testid="ontology-diff-title"
							style={{
								margin: 0,
								fontSize: "16px",
								fontWeight: 600,
								color: EXPLORER_COLORS.inkPrimary,
							}}
						>
							Ontology Structural Diff
						</h3>
						<div
							style={{
								fontSize: "12px",
								color: EXPLORER_COLORS.inkMuted,
								marginTop: "4px",
							}}
						>
							Evolution from Graph A base version to target release
						</div>
					</div>

					{/* Version Comparison Pill */}
					<div
						data-testid="diff-version-pill"
						style={{
							display: "flex",
							alignItems: "center",
							gap: "6px",
							padding: "4px 12px",
							borderRadius: "999px",
							backgroundColor: EXPLORER_COLORS.bgPage,
							border: `1px solid ${EXPLORER_COLORS.borderLine}`,
							fontFamily: EXPLORER_FONTS.mono,
							fontSize: "12px",
						}}
					>
						<span style={{ color: EXPLORER_COLORS.inkLabel }}>
							v{data.base_version ?? "1.0"}
						</span>
						<span style={{ color: EXPLORER_COLORS.inkMuted }}>→</span>
						<span
							style={{ fontWeight: 600, color: EXPLORER_COLORS.inkPrimary }}
						>
							v{data.target_version ?? "1.1"}
						</span>
					</div>
				</div>

				{/* SHA-256 Hashes Comparison */}
				{(data.base_sha256 || data.target_sha256) && (
					<div
						style={{
							marginTop: "12px",
							display: "flex",
							gap: "16px",
							fontSize: "11px",
							fontFamily: EXPLORER_FONTS.mono,
							color: EXPLORER_COLORS.inkLabel,
						}}
					>
						{data.base_sha256 && (
							<span>
								Base SHA: <code>{data.base_sha256.slice(0, 10)}...</code>
							</span>
						)}
						{data.target_sha256 && (
							<span>
								Target SHA: <code>{data.target_sha256.slice(0, 10)}...</code>
							</span>
						)}
					</div>
				)}
			</div>

			{/* KPI Metric Summary Badges */}
			<div
				style={{
					display: "grid",
					gridTemplateColumns: "repeat(auto-fit, minmax(130px, 1fr))",
					gap: "10px",
				}}
			>
				<div
					data-testid="diff-metric-concepts"
					style={{
						padding: "10px 12px",
						borderRadius: "6px",
						background: EXPLORER_COLORS.bgPage,
						border: `1px solid ${EXPLORER_COLORS.borderLine}`,
					}}
				>
					<div
						style={{
							fontSize: "11px",
							color: EXPLORER_COLORS.inkLabel,
							fontWeight: 500,
						}}
					>
						ADDED CONCEPTS
					</div>
					<div
						style={{
							fontFamily: EXPLORER_FONTS.mono,
							fontSize: "18px",
							fontWeight: 600,
							marginTop: "2px",
							color:
								addedConcepts.length > 0
									? "#15803D"
									: EXPLORER_COLORS.inkPrimary,
						}}
					>
						+{addedConcepts.length}
					</div>
				</div>

				<div
					data-testid="diff-metric-relationships"
					style={{
						padding: "10px 12px",
						borderRadius: "6px",
						background: EXPLORER_COLORS.bgPage,
						border: `1px solid ${EXPLORER_COLORS.borderLine}`,
					}}
				>
					<div
						style={{
							fontSize: "11px",
							color: EXPLORER_COLORS.inkLabel,
							fontWeight: 500,
						}}
					>
						ADDED RELATIONS
					</div>
					<div
						style={{
							fontFamily: EXPLORER_FONTS.mono,
							fontSize: "18px",
							fontWeight: 600,
							marginTop: "2px",
							color:
								addedRelationships.length > 0
									? "#B45309"
									: EXPLORER_COLORS.inkPrimary,
						}}
					>
						+{addedRelationships.length}
					</div>
				</div>

				<div
					data-testid="diff-metric-refined"
					style={{
						padding: "10px 12px",
						borderRadius: "6px",
						background: EXPLORER_COLORS.bgPage,
						border: `1px solid ${EXPLORER_COLORS.borderLine}`,
					}}
				>
					<div
						style={{
							fontSize: "11px",
							color: EXPLORER_COLORS.inkLabel,
							fontWeight: 500,
						}}
					>
						REFINED CONCEPTS
					</div>
					<div
						style={{
							fontFamily: EXPLORER_FONTS.mono,
							fontSize: "18px",
							fontWeight: 600,
							marginTop: "2px",
							color:
								refinedConcepts.length > 0
									? "#1D4ED8"
									: EXPLORER_COLORS.inkPrimary,
						}}
					>
						~{refinedConcepts.length}
					</div>
				</div>

				<div
					data-testid="diff-metric-breaking"
					style={{
						padding: "10px 12px",
						borderRadius: "6px",
						background: isAdditive ? EXPLORER_COLORS.bgPage : "#FDECEA",
						border: `1px solid ${isAdditive ? EXPLORER_COLORS.borderLine : "#F5C2C7"}`,
					}}
				>
					<div
						style={{
							fontSize: "11px",
							color: isAdditive ? EXPLORER_COLORS.inkLabel : "#8F1D12",
							fontWeight: 500,
						}}
					>
						BREAKING CHANGES
					</div>
					<div
						style={{
							fontFamily: EXPLORER_FONTS.mono,
							fontSize: "18px",
							fontWeight: 600,
							marginTop: "2px",
							color: isAdditive ? "#15803D" : "#8F1D12",
						}}
					>
						{breakingChanges.length}
					</div>
				</div>
			</div>

			{/* Additive Guarantee Banner */}
			{isAdditive ? (
				<div
					data-testid="additive-guarantee-banner"
					style={{
						padding: "8px 12px",
						borderRadius: "6px",
						background: "#EEF0F2",
						fontSize: "12px",
						color: EXPLORER_COLORS.inkPrimary,
						display: "flex",
						alignItems: "center",
						gap: "8px",
					}}
				>
					<span style={{ color: "#15803D", fontWeight: 700 }}>✓</span>
					<span>
						Strictly additive evolution — Zero breaking changes to existing hubs
						and links.
					</span>
				</div>
			) : (
				<div
					data-testid="breaking-changes-banner"
					style={{
						padding: "10px 12px",
						borderRadius: "6px",
						background: "#FDECEA",
						border: "1px solid #F5C2C7",
						fontSize: "12px",
						color: "#8F1D12",
					}}
				>
					<strong>Warning:</strong> Breaking changes detected in ontology
					evolution!
				</div>
			)}

			{/* Detailed Operations Grid */}
			<DiffOperationsGrid
				addedConcepts={addedConcepts}
				addedRelationships={addedRelationships}
				addedEnumerations={addedEnumerations}
				refinedConcepts={refinedConcepts}
			/>

			{/* Diagnostics */}
			{(data.diagnostics ?? []).length > 0 && (
				<div
					data-testid="ontology-diff-diagnostics"
					style={{
						padding: "10px 12px",
						borderRadius: "6px",
						background: EXPLORER_COLORS.bgPage,
						border: `1px solid ${EXPLORER_COLORS.borderLine}`,
						fontSize: "11.5px",
						fontFamily: EXPLORER_FONTS.mono,
						color: EXPLORER_COLORS.inkBody,
					}}
				>
					<div
						style={{
							fontWeight: 600,
							color: EXPLORER_COLORS.inkLabel,
							marginBottom: "4px",
						}}
					>
						Diagnostics
					</div>
					{data.diagnostics?.map((diag, i) => (
						<div key={`diff-diag-${i}-${diag}`}>{diag}</div>
					))}
				</div>
			)}
		</section>
	);
}
