/**
 * Proposed Operation Card for Ontology Proposal Visualizer (User Directive R9 Modularity).
 */

import { EXPLORER_COLORS, EXPLORER_FONTS } from "../design-tokens";
import type { ProposedOp } from "./OntologyProposalVisualizer";

export function ProposedOpCard({ op }: { op: ProposedOp }) {
	const opConf =
		typeof op.confidence === "number"
			? Math.round(op.confidence * 100)
			: null;

	return (
		<div
			key={op.op_id}
			data-testid={`proposed-op-card-${op.op_id}`}
			style={{
				padding: "12px",
				borderRadius: "6px",
				border: `1px solid ${EXPLORER_COLORS.borderLine}`,
				backgroundColor: EXPLORER_COLORS.bgPage,
				display: "flex",
				flexDirection: "column",
				gap: "8px",
			}}
		>
			{/* Header Row */}
			<div
				style={{
					display: "flex",
					alignItems: "center",
					justifyContent: "space-between",
					gap: "8px",
				}}
			>
				<div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
					<span
						style={{
							fontFamily: EXPLORER_FONTS.mono,
							fontSize: "11px",
							fontWeight: 600,
							color: EXPLORER_COLORS.inkPrimary,
						}}
					>
						{op.op_id}
					</span>
					<span
						style={{
							fontSize: "11px",
							padding: "2px 6px",
							borderRadius: "4px",
							background: "#EEF2FF",
							color: "#1D4ED8",
							fontWeight: 500,
							textTransform: "uppercase",
						}}
					>
						{op.op_type.replace(/_/g, " ")}
					</span>
				</div>
				{opConf !== null && (
					<span
						style={{
							fontFamily: EXPLORER_FONTS.mono,
							fontSize: "11px",
							color: EXPLORER_COLORS.inkMuted,
						}}
					>
						conf: {opConf}%
					</span>
				)}
			</div>

			{/* Entity Specific Attributes */}
			{op.op_type === "add_concept" && (
				<div style={{ fontSize: "12px", color: EXPLORER_COLORS.inkBody }}>
					<div>
						<strong>Concept:</strong> {op.concept_name} (
						<span style={{ fontFamily: EXPLORER_FONTS.mono, fontSize: "11px" }}>
							{op.concept_uri}
						</span>
						)
					</div>
					{op.target_hub && (
						<div>
							<strong>Target Hub:</strong>{" "}
							<code style={{ fontFamily: EXPLORER_FONTS.mono }}>
								{op.target_hub}
							</code>
						</div>
					)}
					{op.elm_archetype && (
						<div>
							<strong>Archetype:</strong> {op.elm_archetype}
						</div>
					)}
					{(op.attributes ?? []).length > 0 && (
						<div>
							<strong>Attributes:</strong> {op.attributes?.join(", ")}
						</div>
					)}
				</div>
			)}

			{op.op_type === "add_relationship" && (
				<div style={{ fontSize: "12px", color: EXPLORER_COLORS.inkBody }}>
					<div>
						<strong>Relationship:</strong> {op.relationship_name} (
						<span style={{ fontFamily: EXPLORER_FONTS.mono, fontSize: "11px" }}>
							{op.relationship_uri}
						</span>
						)
					</div>
					{op.target_link && (
						<div>
							<strong>Target Link:</strong>{" "}
							<code style={{ fontFamily: EXPLORER_FONTS.mono }}>
								{op.target_link}
							</code>
						</div>
					)}
					{(op.endpoints ?? []).length > 0 && (
						<div>
							<strong>Endpoints:</strong> {op.endpoints?.join(" ↔ ")}
						</div>
					)}
					{op.grain_semantics && (
						<div>
							<strong>Semantics:</strong> {op.grain_semantics}
						</div>
					)}
				</div>
			)}

			{op.op_type === "add_reference_enumeration" && (
				<div style={{ fontSize: "12px", color: EXPLORER_COLORS.inkBody }}>
					<div>
						<strong>Enum:</strong> {op.name ?? op.enum_uri}
					</div>
					{op.target_reference_table && (
						<div>
							<strong>Ref Table:</strong>{" "}
							<code style={{ fontFamily: EXPLORER_FONTS.mono }}>
								{op.target_reference_table}
							</code>
						</div>
					)}
					{(op.values ?? op.allowed_codes ?? []).length > 0 && (
						<div>
							<strong>Allowed Codes:</strong>{" "}
							{(op.values ?? op.allowed_codes)?.join(", ")}
						</div>
					)}
				</div>
			)}

			{op.op_type === "refine_definition" && (
				<div style={{ fontSize: "12px", color: EXPLORER_COLORS.inkBody }}>
					<div>
						<strong>Target:</strong>{" "}
						<span style={{ fontFamily: EXPLORER_FONTS.mono }}>
							{op.target_uri ?? op.concept_uri}
						</span>
					</div>
					{op.refined_description && (
						<div>
							<strong>Refined Description:</strong> {op.refined_description}
						</div>
					)}
					{(op.additional_attributes ?? []).length > 0 && (
						<div>
							<strong>Added Attributes:</strong> {op.additional_attributes?.join(", ")}
						</div>
					)}
				</div>
			)}

			{/* Business Rationale */}
			{op.rationale && (
				<div
					style={{
						fontSize: "11.5px",
						color: EXPLORER_COLORS.inkMuted,
						fontStyle: "italic",
					}}
				>
					"{op.rationale}"
				</div>
			)}

			{/* Evidence Refs */}
			{(op.evidence_refs ?? []).length > 0 && (
				<div
					style={{
						marginTop: "4px",
						padding: "6px 8px",
						borderRadius: "4px",
						background: EXPLORER_COLORS.bgSurface,
						border: `1px solid ${EXPLORER_COLORS.borderLine}`,
						fontSize: "11px",
						display: "flex",
						flexDirection: "column",
						gap: "2px",
					}}
				>
					<span style={{ fontWeight: 600, color: EXPLORER_COLORS.inkLabel }}>
						Grounding Evidence:
					</span>
					{op.evidence_refs?.map((ev, i) => (
						<div
							key={`ev-${op.op_id}-${ev.reference_uri ?? i}`}
							style={{ fontFamily: EXPLORER_FONTS.mono }}
						>
							• {ev.evidence_type}: {ev.reference_uri}
							{ev.snippet && ` — snippet: "${ev.snippet}"`}
						</div>
					))}
				</div>
			)}
		</div>
	);
}
