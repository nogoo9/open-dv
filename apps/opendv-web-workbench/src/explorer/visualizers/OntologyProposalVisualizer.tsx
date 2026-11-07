/**
 * Ontology Proposal Visualizer (`OntologyProposalVisualizer.tsx`)
 * Renders the `ontology_proposal` artifact (proposed additive delta operations,
 * confidence scores, business rationales, and grounding evidence) (Phase P7 ONT-02 / IP-6).
 */

import type { Artifact } from "@opendv/protocol-ts";
import { useMemo, useState } from "react";
import { EXPLORER_COLORS, EXPLORER_FONTS } from "../design-tokens";

export interface EvidenceRef {
	evidence_type?: string | undefined;
	reference_uri?: string | undefined;
	snippet?: string | undefined;
	confidence?: number | undefined;
}

export interface ProposedOp {
	op_id: string;
	op_type: string;
	confidence?: number | undefined;
	rationale?: string | undefined;
	evidence_refs?: EvidenceRef[] | undefined;
	// Concept
	concept_name?: string | undefined;
	concept_uri?: string | undefined;
	elm_archetype?: string | undefined;
	target_hub?: string | undefined;
	attributes?: string[] | undefined;
	definition?: string | undefined;
	// Relationship
	relationship_name?: string | undefined;
	relationship_uri?: string | undefined;
	target_link?: string | undefined;
	endpoints?: string[] | undefined;
	grain_semantics?: string | undefined;
	// Enum
	enum_uri?: string | undefined;
	name?: string | undefined;
	target_reference_table?: string | undefined;
	values?: string[] | undefined;
	allowed_codes?: string[] | undefined;
	// Refinement
	target_uri?: string | undefined;
	refined_description?: string | undefined;
	additional_attributes?: string[] | undefined;
	// Governance
	constraint_type?: string | undefined;
	pii_confidential_attributes?: string[] | undefined;
	mnpi_confidential_attributes?: string[] | undefined;
}

export interface OntologyProposalData {
	artifact_kind?: string | undefined;
	proposal_id?: string | undefined;
	wave_id?: string | undefined;
	recommender_id?: string | undefined;
	recommender_version?: string | undefined;
	base_ontology_sha256?: string | undefined;
	confidence_score?: number | undefined;
	rationale?: string | undefined;
	coverage_before?: number | undefined;
	coverage_after?: number | undefined;
	diagnostics?: string[] | undefined;
	delta?:
		| {
				delta_id?: string | undefined;
				parent_sha256?: string | undefined;
				domain_namespace?: string | undefined;
				base_ontology_version?: string | undefined;
				target_ontology_version?: string | undefined;
				operations?: ProposedOp[] | undefined;
		  }
		| undefined;
}

export interface OntologyProposalVisualizerProps {
	artifact?: Artifact | undefined;
	data?: OntologyProposalData | undefined;
}

function parseProposalPayload(
	artifact?: Artifact,
	explicitData?: OntologyProposalData,
): OntologyProposalData {
	if (explicitData) {
		return explicitData;
	}
	if (!artifact?.raw) {
		return {};
	}
	try {
		return JSON.parse(artifact.raw) as OntologyProposalData;
	} catch {
		return { diagnostics: ["Failed to parse raw proposal JSON"] };
	}
}

export function OntologyProposalVisualizer({
	artifact,
	data: explicitData,
}: OntologyProposalVisualizerProps) {
	const data = useMemo(
		() => parseProposalPayload(artifact, explicitData),
		[artifact, explicitData],
	);

	const [filterOpType, setFilterOpType] = useState<string>("all");

	const operations = useMemo(
		() => data.delta?.operations ?? [],
		[data.delta?.operations],
	);

	const filteredOps = useMemo(() => {
		if (filterOpType === "all") {
			return operations;
		}
		return operations.filter((op) => op.op_type === filterOpType);
	}, [operations, filterOpType]);

	const confidencePct =
		typeof data.confidence_score === "number"
			? Math.round(data.confidence_score * 100)
			: null;

	const opTypes = useMemo(() => {
		const types = new Set<string>();
		for (const op of operations) {
			types.add(op.op_type);
		}
		return Array.from(types);
	}, [operations]);

	return (
		<section
			aria-label="Ontology Proposal Details"
			data-testid="ontology-proposal-visualizer"
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
			{/* Top Header Card */}
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
						<div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
							<h3
								data-testid="ontology-proposal-title"
								style={{
									margin: 0,
									fontSize: "16px",
									fontWeight: 600,
									color: EXPLORER_COLORS.inkPrimary,
								}}
							>
								Proposed Ontology Delta
							</h3>
							{data.proposal_id && (
								<span
									data-testid="proposal-id-pill"
									style={{
										fontFamily: EXPLORER_FONTS.mono,
										fontSize: "11px",
										padding: "2px 8px",
										borderRadius: "4px",
										background: EXPLORER_COLORS.bgSubtle,
										color: EXPLORER_COLORS.inkLabel,
									}}
								>
									{data.proposal_id}
								</span>
							)}
						</div>
						<div
							style={{
								fontSize: "12px",
								color: EXPLORER_COLORS.inkMuted,
								marginTop: "4px",
							}}
						>
							{data.recommender_id && (
								<span>
									Recommender: <strong>{data.recommender_id}</strong> (v
									{data.recommender_version ?? "1.0"}) ·{" "}
								</span>
							)}
							{data.delta?.base_ontology_version && (
								<span>
									v{data.delta.base_ontology_version} → v
									{data.delta.target_ontology_version ?? "next"}
								</span>
							)}
						</div>
					</div>

					<div style={{ display: "flex", gap: "8px", alignItems: "center" }}>
						{confidencePct !== null && (
							<div
								data-testid="proposal-confidence-badge"
								style={{
									padding: "4px 10px",
									borderRadius: "999px",
									backgroundColor: confidencePct >= 90 ? "#EEF2FF" : "#FFF4E5",
									color: confidencePct >= 90 ? "#1E3A8A" : "#8A3B07",
									fontFamily: EXPLORER_FONTS.mono,
									fontSize: "12px",
									fontWeight: 600,
								}}
							>
								{confidencePct}% confidence
							</div>
						)}
						{typeof data.coverage_before === "number" &&
							typeof data.coverage_after === "number" && (
								<div
									data-testid="coverage-impact-badge"
									style={{
										padding: "4px 10px",
										borderRadius: "999px",
										backgroundColor: "#EEF0F2",
										color: EXPLORER_COLORS.inkPrimary,
										fontSize: "12px",
										fontFamily: EXPLORER_FONTS.mono,
									}}
								>
									cov: {Math.round(data.coverage_before * 100)}% →{" "}
									{Math.round(data.coverage_after * 100)}%
								</div>
							)}
					</div>
				</div>

				{/* High-Level Rationale */}
				{data.rationale && (
					<div
						data-testid="proposal-rationale-box"
						style={{
							marginTop: "12px",
							padding: "10px 12px",
							borderRadius: "6px",
							backgroundColor: "#FFF9F0",
							border: "1px solid #F3D9B1",
							fontSize: "12.5px",
							color: "#8A3B07",
							lineHeight: 1.5,
						}}
					>
						<strong>Rationale:</strong> {data.rationale}
					</div>
				)}
			</div>

			{/* Filter Bar */}
			<div
				style={{
					display: "flex",
					alignItems: "center",
					justifyContent: "space-between",
					flexWrap: "wrap",
					gap: "8px",
				}}
			>
				<div
					style={{
						fontSize: "12px",
						fontWeight: 600,
						color: EXPLORER_COLORS.inkLabel,
						textTransform: "uppercase",
					}}
				>
					Proposed Operations ({operations.length})
				</div>
				<div style={{ display: "flex", gap: "6px" }}>
					<button
						type="button"
						data-testid="filter-all"
						onClick={() => setFilterOpType("all")}
						style={{
							all: "unset",
							cursor: "pointer",
							fontSize: "11px",
							padding: "3px 8px",
							borderRadius: "4px",
							background:
								filterOpType === "all"
									? EXPLORER_COLORS.inkPrimary
									: EXPLORER_COLORS.bgPage,
							color:
								filterOpType === "all" ? "#FFFFFF" : EXPLORER_COLORS.inkPrimary,
							border: `1px solid ${EXPLORER_COLORS.borderLine}`,
						}}
					>
						All ({operations.length})
					</button>
					{opTypes.map((type) => (
						<button
							key={type}
							type="button"
							data-testid={`filter-${type}`}
							onClick={() => setFilterOpType(type)}
							style={{
								all: "unset",
								cursor: "pointer",
								fontSize: "11px",
								padding: "3px 8px",
								borderRadius: "4px",
								background:
									filterOpType === type
										? EXPLORER_COLORS.inkPrimary
										: EXPLORER_COLORS.bgPage,
								color:
									filterOpType === type
										? "#FFFFFF"
										: EXPLORER_COLORS.inkPrimary,
								border: `1px solid ${EXPLORER_COLORS.borderLine}`,
							}}
						>
							{type.replace(/_/g, " ")}
						</button>
					))}
				</div>
			</div>

			{/* Operations Cards List */}
			<div
				data-testid="proposed-operations-list"
				style={{
					display: "flex",
					flexDirection: "column",
					gap: "10px",
					maxHeight: "440px",
					overflowY: "auto",
				}}
			>
				{filteredOps.map((op) => {
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
								<div
									style={{ display: "flex", alignItems: "center", gap: "8px" }}
								>
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
								<div
									style={{ fontSize: "12px", color: EXPLORER_COLORS.inkBody }}
								>
									<div>
										<strong>Concept:</strong> {op.concept_name} (
										<span
											style={{
												fontFamily: EXPLORER_FONTS.mono,
												fontSize: "11px",
											}}
										>
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
								<div
									style={{ fontSize: "12px", color: EXPLORER_COLORS.inkBody }}
								>
									<div>
										<strong>Relationship:</strong> {op.relationship_name} (
										<span
											style={{
												fontFamily: EXPLORER_FONTS.mono,
												fontSize: "11px",
											}}
										>
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
								<div
									style={{ fontSize: "12px", color: EXPLORER_COLORS.inkBody }}
								>
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
								<div
									style={{ fontSize: "12px", color: EXPLORER_COLORS.inkBody }}
								>
									<div>
										<strong>Target:</strong>{" "}
										<span style={{ fontFamily: EXPLORER_FONTS.mono }}>
											{op.target_uri ?? op.concept_uri}
										</span>
									</div>
									{op.refined_description && (
										<div>
											<strong>Refined Description:</strong>{" "}
											{op.refined_description}
										</div>
									)}
									{(op.additional_attributes ?? []).length > 0 && (
										<div>
											<strong>Added Attributes:</strong>{" "}
											{op.additional_attributes?.join(", ")}
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
									<span
										style={{ fontWeight: 600, color: EXPLORER_COLORS.inkLabel }}
									>
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
				})}
			</div>

			{/* Diagnostics */}
			{(data.diagnostics ?? []).length > 0 && (
				<div
					data-testid="ontology-proposal-diagnostics"
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
						<div key={`prop-diag-${i}-${diag}`}>• {diag}</div>
					))}
				</div>
			)}
		</section>
	);
}
