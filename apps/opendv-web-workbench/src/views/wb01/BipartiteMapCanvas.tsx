/**
 * Bipartite Map Canvas (`BipartiteMapCanvas.tsx`)
 * Visualizes the bipartite concept grounding between Graph A (CBC Ontology) and Graph B (Bronze Tables) (`docs/06` §3.2).
 */

import { useState } from "react";

export interface BipartiteMappingNode {
	cbc_uri: string;
	concept_name: string;
	target_hub: string;
	hub_key: string;
	alternate_key?: string;
	governance_tier?: string;
	status: "MAPPED" | "UNALLOCATED";
	bronze_tables: string[];
	notes: string;
}

export const BIPARTITE_MAPPINGS: readonly BipartiteMappingNode[] = [
	{
		cbc_uri: "urn:ontology:entity:fund",
		concept_name: "Fund",
		target_hub: "hub_fund",
		hub_key: "fund_code",
		alternate_key: "ISIN",
		status: "MAPPED",
		bronze_tables: ["investran.src_pe_funds"],
		notes: "Mapped 9 columns; Hub key fund_code with alternate ISIN.",
	},
	{
		cbc_uri: "urn:ontology:entity:limited_partner",
		concept_name: "Limited Partner",
		target_hub: "hub_limited_partner",
		hub_key: "lp_id",
		alternate_key: "LEI",
		governance_tier: "PII_HIGH (wire_iban, tax_id)",
		status: "MAPPED",
		bronze_tables: [
			"investran.src_lp_investors",
			"legacy_gp.src_legacy_lp_roster",
			"mdm.src_lp_steward_crosswalk",
		],
		notes:
			"Multi-source mapping with Tier-3 BKCC isolation for legacy_gp.lp_num='LP-101'.",
	},
	{
		cbc_uri: "urn:ontology:entity:portfolio_company",
		concept_name: "Portfolio Company",
		target_hub: "hub_portfolio_company",
		hub_key: "LEI",
		alternate_key: "deal_code",
		governance_tier: "MNPI_CONFIDENTIAL (valuation)",
		status: "MAPPED",
		bronze_tables: [
			"dealcloud.src_portfolio_companies",
			"investran.src_lp_commitments_and_calls",
		],
		notes:
			"Passive integration on Global ISO 17442 LEI; optional FK split into co-invest link.",
	},
	{
		cbc_uri: "urn:ontology:entity:document",
		concept_name: "Document",
		target_hub: "hub_document",
		hub_key: "doc_uri",
		status: "MAPPED",
		bronze_tables: [
			"dms.documents",
			"dms.src_side_letter_extractions",
			"dms.src_ic_memo_extractions",
		],
		notes:
			"Content-addressable document manifest with AI extraction provenance links.",
	},
	{
		cbc_uri: "urn:ontology:entity:custodian_bank",
		concept_name: "Custodian Bank",
		target_hub: "hub_custodian_bank",
		hub_key: "bank_bic",
		status: "UNALLOCATED",
		bronze_tables: [],
		notes:
			"Unallocated in Wave 1/2 (No Bronze source table in Graph B). Zero-Hallucination Guard prevents phantom hub creation!",
	},
];

export interface BipartiteMapCanvasProps {
	onSelectConcept?: (node: BipartiteMappingNode) => void;
}

export function BipartiteMapCanvas({
	onSelectConcept,
}: BipartiteMapCanvasProps) {
	const [selectedUri, setSelectedUri] = useState<string>(
		"urn:ontology:entity:fund",
	);

	const defaultConcept = BIPARTITE_MAPPINGS[0] as BipartiteMappingNode;
	const activeConcept =
		BIPARTITE_MAPPINGS.find((c) => c.cbc_uri === selectedUri) ?? defaultConcept;

	const mappedCount = BIPARTITE_MAPPINGS.filter(
		(m) => m.status === "MAPPED",
	).length;
	const totalCount = BIPARTITE_MAPPINGS.length;
	const coveragePct = Math.round((mappedCount / totalCount) * 100);

	return (
		<section
			aria-label="Dual-Graph Bipartite Mapping Canvas"
			data-testid="bipartite-map-canvas"
			style={{
				backgroundColor: "var(--dv-surface-1)",
				border: "1px solid var(--dv-border-subtle)",
				borderRadius: "0.5rem",
				padding: "1rem",
			}}
		>
			{/* Top Gauge & Stats */}
			<div
				style={{
					display: "flex",
					justifyContent: "space-between",
					alignItems: "center",
					flexWrap: "wrap",
					gap: "0.75rem",
					marginBottom: "1rem",
					borderBottom: "1px solid var(--dv-border-subtle)",
					paddingBottom: "0.75rem",
				}}
			>
				<div>
					<h3
						style={{
							margin: 0,
							fontSize: "0.875rem",
							fontWeight: 600,
							color: "var(--dv-text-primary)",
						}}
					>
						Graph A (CBC Ontology) ↔ Graph B (Bronze Tables) Bipartite Mapping
					</h3>
					<span
						style={{ fontSize: "0.75rem", color: "var(--dv-text-secondary)" }}
					>
						Grounding top-down business concepts into verified bottom-up
						physical schemas
					</span>
				</div>

				<div
					data-testid="ontology-coverage-gauge"
					style={{
						display: "flex",
						alignItems: "center",
						gap: "0.75rem",
					}}
				>
					<div
						style={{
							backgroundColor: "var(--dv-surface-2)",
							border: "1px solid var(--dv-border-strong)",
							borderRadius: "0.375rem",
							padding: "0.35rem 0.65rem",
							textAlign: "right",
						}}
					>
						<span
							style={{
								fontSize: "0.7rem",
								color: "var(--dv-text-secondary)",
								marginRight: "0.5rem",
							}}
						>
							Ontology Coverage:
						</span>
						<strong
							style={{
								fontSize: "0.875rem",
								color: "var(--dv-gate-pass)",
							}}
						>
							{mappedCount} / {totalCount} ({coveragePct}.0%)
						</strong>
					</div>

					<div
						style={{
							backgroundColor: "var(--dv-surface-2)",
							border: "1px solid var(--dv-border-strong)",
							borderRadius: "0.375rem",
							padding: "0.35rem 0.65rem",
						}}
					>
						<span
							style={{
								fontSize: "0.7rem",
								color: "var(--dv-text-secondary)",
								marginRight: "0.5rem",
							}}
						>
							Phantom Hubs:
						</span>
						<strong
							style={{ fontSize: "0.875rem", color: "var(--dv-text-primary)" }}
						>
							0 (Zero-Hallucination)
						</strong>
					</div>
				</div>
			</div>

			{/* Bipartite Dual Columns */}
			<div
				style={{
					display: "grid",
					gridTemplateColumns: "1fr 1fr",
					gap: "1.5rem",
				}}
			>
				{/* Left Column: Graph A (CBC Ontology) */}
				<div>
					<div
						style={{
							fontSize: "0.75rem",
							fontWeight: 700,
							color: "var(--dv-hub-accent)",
							marginBottom: "0.5rem",
							textTransform: "uppercase",
							letterSpacing: "0.05em",
						}}
					>
						Graph A: CBC Enterprise Ontology Concepts
					</div>

					<div
						style={{ display: "flex", flexDirection: "column", gap: "0.5rem" }}
					>
						{BIPARTITE_MAPPINGS.map((concept) => {
							const isSelected = concept.cbc_uri === selectedUri;
							const isUnallocated = concept.status === "UNALLOCATED";

							return (
								<button
									key={concept.cbc_uri}
									type="button"
									data-testid={`ontology-node-${concept.concept_name.toLowerCase().replace(/\s+/g, "_")}`}
									onClick={() => {
										setSelectedUri(concept.cbc_uri);
										onSelectConcept?.(concept);
									}}
									style={{
										display: "block",
										width: "100%",
										textAlign: "left",
										backgroundColor: isSelected
											? "var(--dv-surface-2)"
											: "var(--dv-surface-1)",
										border: isSelected
											? "2px solid var(--dv-hub-accent)"
											: isUnallocated
												? "1.5px dashed var(--dv-border-strong)"
												: "1px solid var(--dv-border-subtle)",
										borderRadius: "0.375rem",
										padding: "0.6rem 0.75rem",
										cursor: "pointer",
										opacity: isUnallocated ? 0.75 : 1,
										color: "inherit",
									}}
								>
									<div
										style={{
											display: "flex",
											justifyContent: "space-between",
											alignItems: "center",
											marginBottom: "0.25rem",
										}}
									>
										<span
											style={{
												fontWeight: 700,
												fontSize: "0.8125rem",
												color: isUnallocated
													? "var(--dv-text-muted)"
													: "var(--dv-text-primary)",
											}}
										>
											[CBC] {concept.concept_name}
										</span>
										{isUnallocated ? (
											<span
												style={{
													fontSize: "0.65rem",
													fontWeight: 700,
													color: "var(--dv-text-muted)",
													backgroundColor: "var(--dv-surface-3)",
													padding: "0.1rem 0.35rem",
													borderRadius: "0.2rem",
												}}
											>
												[UNALLOCATED]
											</span>
										) : (
											<span
												style={{
													fontSize: "0.65rem",
													fontWeight: 700,
													color: "var(--dv-hub-accent)",
													backgroundColor: "var(--dv-surface-2)",
													padding: "0.1rem 0.35rem",
													borderRadius: "0.2rem",
													border: "1px solid var(--dv-hub-accent)",
												}}
											>
												→ {concept.target_hub}
											</span>
										)}
									</div>

									<div
										style={{
											fontSize: "0.7rem",
											fontFamily: "ui-monospace, monospace",
											color: "var(--dv-text-secondary)",
										}}
									>
										Key: {concept.hub_key}
										{concept.alternate_key &&
											` | Alt: ${concept.alternate_key}`}
									</div>

									{concept.governance_tier && (
										<div
											style={{
												marginTop: "0.25rem",
												fontSize: "0.65rem",
												color: "var(--dv-sat-pii-accent)",
												fontWeight: 600,
											}}
										>
											Gov: {concept.governance_tier}
										</div>
									)}
								</button>
							);
						})}
					</div>
				</div>

				{/* Right Column: Graph B (Physical Bronze Tables) */}
				<div>
					<div
						style={{
							fontSize: "0.75rem",
							fontWeight: 700,
							color: "var(--dv-lnk-accent)",
							marginBottom: "0.5rem",
							textTransform: "uppercase",
							letterSpacing: "0.05em",
						}}
					>
						Graph B: Grounded Physical Bronze Tables
					</div>

					<div
						data-testid="grounded-tables-container"
						style={{
							backgroundColor: "var(--dv-surface-2)",
							border: "1px solid var(--dv-border-subtle)",
							borderRadius: "0.375rem",
							padding: "0.75rem",
							display: "flex",
							flexDirection: "column",
							gap: "0.75rem",
						}}
					>
						<div>
							<div
								style={{
									fontSize: "0.7rem",
									color: "var(--dv-text-secondary)",
									marginBottom: "0.25rem",
								}}
							>
								Selected Concept Grounding:
							</div>
							<strong
								style={{
									fontSize: "0.875rem",
									color: "var(--dv-text-primary)",
								}}
							>
								{activeConcept.concept_name} ({activeConcept.cbc_uri})
							</strong>
						</div>

						{activeConcept.bronze_tables.length > 0 ? (
							<div
								style={{
									display: "flex",
									flexDirection: "column",
									gap: "0.4rem",
								}}
							>
								<div
									style={{
										fontSize: "0.7rem",
										color: "var(--dv-text-secondary)",
									}}
								>
									Connected Source Bronze Tables:
								</div>
								{activeConcept.bronze_tables.map((table) => (
									<div
										key={table}
										data-testid={`bronze-node-${table}`}
										style={{
											backgroundColor: "var(--dv-surface-1)",
											border: "1px solid var(--dv-border-strong)",
											borderRadius: "0.25rem",
											padding: "0.4rem 0.6rem",
											fontFamily: "ui-monospace, monospace",
											fontSize: "0.75rem",
											display: "flex",
											justifyContent: "space-between",
											alignItems: "center",
										}}
									>
										<span>{table}</span>
										<span
											style={{
												color: "var(--dv-lnk-accent)",
												fontWeight: 700,
												fontSize: "0.7rem",
											}}
										>
											Grounded ✓
										</span>
									</div>
								))}
							</div>
						) : (
							<div
								data-testid="unallocated-notice"
								style={{
									backgroundColor: "var(--dv-surface-1)",
									border: "1px dashed var(--dv-border-strong)",
									borderRadius: "0.25rem",
									padding: "0.6rem",
									fontSize: "0.75rem",
									color: "var(--dv-text-secondary)",
								}}
							>
								<div
									style={{
										fontWeight: 700,
										color: "var(--dv-gate-autofix)",
										marginBottom: "0.25rem",
									}}
								>
									Zero-Hallucination Safe State
								</div>
								No Bronze table exists in Graph B for Custodian Bank in Waves
								1/2. Open-DV safely marks this concept unallocated and refuses
								to generate a phantom hub table.
							</div>
						)}

						<div
							style={{
								borderTop: "1px solid var(--dv-border-subtle)",
								paddingTop: "0.5rem",
								fontSize: "0.7rem",
								color: "var(--dv-text-secondary)",
							}}
						>
							<strong>Mapping Rationale: </strong>
							{activeConcept.notes}
						</div>
					</div>
				</div>
			</div>
		</section>
	);
}
