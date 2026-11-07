/**
 * Ontology Proposal Visualizer (`OntologyProposalVisualizer.tsx`)
 * Renders the `ontology_proposal` artifact (proposed additive delta operations,
 * confidence scores, business rationales, and grounding evidence) (Phase P7 ONT-02 / IP-6).
 */

import type { Artifact } from "@opendv/protocol-ts";
import { useMemo, useState } from "react";
import { EXPLORER_COLORS, EXPLORER_FONTS } from "../design-tokens";
import { ProposedOpCard } from "./ProposedOpCard";

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

	const operations = useMemo(() => {
		return data.delta?.operations ?? [];
	}, [data]);

	const opTypes = useMemo(() => {
		const types = new Set<string>();
		for (const op of operations) {
			types.add(op.op_type);
		}
		return Array.from(types).sort();
	}, [operations]);

	const filteredOps = useMemo(() => {
		if (filterOpType === "all") return operations;
		return operations.filter((op) => op.op_type === filterOpType);
	}, [operations, filterOpType]);

	const overallConf =
		typeof data.confidence_score === "number"
			? Math.round(data.confidence_score * 100)
			: null;

	return (
		<section
			aria-label="Ontology Delta Proposal"
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
						<div
							style={{ display: "flex", alignItems: "baseline", gap: "10px" }}
						>
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
										fontSize: "12px",
										color: EXPLORER_COLORS.inkMuted,
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
									Recommender: <strong>{data.recommender_id}</strong>
									{data.recommender_version && ` (v${data.recommender_version})`} ·{" "}
								</span>
							)}
							{data.delta?.target_ontology_version && (
								<span>Target: v{data.delta.target_ontology_version} · </span>
							)}
							{data.delta?.delta_id && (
								<span style={{ fontFamily: EXPLORER_FONTS.mono }}>
									Delta: {data.delta.delta_id}
								</span>
							)}
						</div>
					</div>

					<div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
						{overallConf !== null && (
							<div
								data-testid="proposal-confidence-badge"
								style={{
									padding: "4px 10px",
									borderRadius: "999px",
									background: "#EEF2FF",
									border: "1px solid #C7D2FE",
									color: "#1D4ED8",
									fontSize: "12px",
									fontWeight: 600,
									fontFamily: EXPLORER_FONTS.mono,
								}}
							>
								{overallConf}% confidence
							</div>
						)}
						{typeof data.coverage_before === "number" &&
							typeof data.coverage_after === "number" && (
								<div
									data-testid="coverage-impact-badge"
									style={{
										padding: "4px 10px",
										borderRadius: "999px",
										background: "#ECFDF5",
										border: "1px solid #A7F3D0",
										color: "#047857",
										fontSize: "12px",
										fontWeight: 600,
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
				{filteredOps.map((op) => (
					<ProposedOpCard key={op.op_id} op={op} />
				))}
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
