/**
 * Interactive ELM Topology Canvas (`TopologyCanvas.tsx`)
 * Visualizes Data Vault 2.0 Hubs, Links, Satellites, SALs, and Ref tables with WCAG AA dual-encoding (VIZ-STEP-06) (`docs/06` §3.7).
 */

import { useState } from "react";
import { ENTITY_VISUAL_GRAMMAR } from "../../theme/theme-registry";

export interface TopologyNode {
	id: string;
	name: string;
	kind: "HUB" | "LNK" | "SAL" | "SAT" | "SAT_PII" | "REF";
	category: string;
	primaryKey: string;
	parentEntity?: string;
	classification?: string;
	columns: string[];
	semanticPill?: string;
	notes: string;
}

export const TOPOLOGY_NODES: readonly TopologyNode[] = [
	// Hubs
	{
		id: "hub_fund",
		name: "hub_fund",
		kind: "HUB",
		category: "Enterprise Hub",
		primaryKey: "hk_fund_hash",
		columns: ["hk_fund_hash", "fund_code", "load_dts", "rec_src"],
		semanticPill: "[HK: 94A6..8759 | GLOBAL:APEX_BO_IV Fund IV]",
		notes: "Master key for private equity buyout funds. Passive integration.",
	},
	{
		id: "hub_limited_partner",
		name: "hub_limited_partner",
		kind: "HUB",
		category: "Enterprise Hub",
		primaryKey: "hk_limited_partner_hash",
		columns: ["hk_limited_partner_hash", "lp_id", "load_dts", "rec_src"],
		semanticPill:
			"[HK: 5D15..0AE4 CalPERS] & [HK: 6B7C..D8B9 Bavarian Pension]",
		notes:
			"Enterprise LP hub isolating local Tier-3 keys with BKCC=LEGACY_GP_ADMIN.",
	},
	{
		id: "hub_portfolio_company",
		name: "hub_portfolio_company",
		kind: "HUB",
		category: "Enterprise Hub",
		primaryKey: "hk_portfolio_company_hash",
		columns: ["hk_portfolio_company_hash", "lei", "load_dts", "rec_src"],
		semanticPill: "[HK: 0C18..454E | GLOBAL:529900T8..DO55 Nordic Cloud]",
		notes: "Master key for portfolio companies using ISO 17442 Global LEI.",
	},
	{
		id: "hub_document",
		name: "hub_document",
		kind: "HUB",
		category: "Enterprise Hub",
		primaryKey: "hk_document_hash",
		columns: ["hk_document_hash", "doc_uri", "load_dts", "rec_src"],
		semanticPill: "[HK: 864D..EE6B Side Letter] & [HK: 99D8..4158 IC Memo]",
		notes: "Content-addressable S3 URI natural key hub.",
	},

	// Links
	{
		id: "lnk_lp_commitment",
		name: "lnk_lp_commitment",
		kind: "LNK",
		category: "Enterprise Relationship",
		primaryKey: "hk_lnk_commitment_hash",
		parentEntity: "hub_fund + hub_limited_partner",
		columns: [
			"hk_lnk_commitment_hash",
			"hk_fund_hash",
			"hk_limited_partner_hash",
			"commitment_id",
			"load_dts",
			"rec_src",
		],
		notes:
			"Standard driving link with degenerate commitment_id transaction key.",
	},
	{
		id: "lnk_lp_commitment_co_invest",
		name: "lnk_lp_commitment_co_invest",
		kind: "LNK",
		category: "Contextual Split Link",
		primaryKey: "hk_lnk_co_invest_hash",
		parentEntity: "hub_fund + hub_limited_partner + hub_portfolio_company",
		columns: [
			"hk_lnk_co_invest_hash",
			"hk_fund_hash",
			"hk_limited_partner_hash",
			"hk_portfolio_company_hash",
			"commitment_id",
			"load_dts",
			"rec_src",
		],
		notes:
			"Algorithm A.4 split link filtering WHERE co_invest_portco_lei IS NOT NULL.",
	},
	{
		id: "sal_limited_partner",
		name: "sal_limited_partner",
		kind: "SAL",
		category: "Same-As Link (Alias Resolution)",
		primaryKey: "hk_sal_lp_hash",
		parentEntity: "hub_limited_partner (Master) + hub_limited_partner (Legacy)",
		columns: [
			"hk_sal_lp_hash",
			"master_hk_lp_hash",
			"alias_hk_lp_hash",
			"steward_approved_dts",
			"load_dts",
			"rec_src",
		],
		notes:
			"Links CalPERS master account with Bavarian Pension and cross-system aliases.",
	},
	{
		id: "lnk_document_lp_commitment",
		name: "lnk_document_lp_commitment",
		kind: "LNK",
		category: "Document Relationship",
		primaryKey: "hk_lnk_doc_commitment_hash",
		parentEntity: "hub_document + lnk_lp_commitment",
		columns: [
			"hk_lnk_doc_commitment_hash",
			"hk_document_hash",
			"hk_lnk_commitment_hash",
			"load_dts",
			"rec_src",
		],
		notes: "Links legal side letter PDFs to underlying LP commitments.",
	},
	{
		id: "lnk_document_portfolio_company",
		name: "lnk_document_portfolio_company",
		kind: "LNK",
		category: "Document Relationship",
		primaryKey: "hk_lnk_doc_portco_hash",
		parentEntity: "hub_document + hub_portfolio_company",
		columns: [
			"hk_lnk_doc_portco_hash",
			"hk_document_hash",
			"hk_portfolio_company_hash",
			"load_dts",
			"rec_src",
		],
		notes: "Links audited IC memo PDFs to portfolio company valuations.",
	},

	// Satellites
	{
		id: "sat_fund_investran",
		name: "sat_fund_investran",
		kind: "SAT",
		category: "Fund Descriptive Context",
		primaryKey: "hk_fund_hash + load_dts",
		parentEntity: "hub_fund",
		columns: [
			"hk_fund_hash",
			"load_dts",
			"hash_diff",
			"fund_name",
			"vintage_year",
			"target_size_usd",
			"currency",
			"rec_src",
		],
		notes: "Core fund descriptive satellite.",
	},
	{
		id: "sat_fund_investran_esg",
		name: "sat_fund_investran_esg",
		kind: "SAT",
		category: "Additive Drift Companion",
		primaryKey: "hk_fund_hash + load_dts",
		parentEntity: "hub_fund",
		columns: [
			"hk_fund_hash",
			"load_dts",
			"hash_diff",
			"ilpa_esg_rating",
			"rec_src",
		],
		notes:
			"Day-3 schema drift additive companion satellite; zero historical rewrite.",
	},
	{
		id: "sat_limited_partner_investran",
		name: "sat_limited_partner_investran",
		kind: "SAT",
		category: "LP Context (INTERNAL)",
		primaryKey: "hk_limited_partner_hash + load_dts",
		parentEntity: "hub_limited_partner",
		columns: [
			"hk_limited_partner_hash",
			"load_dts",
			"hash_diff",
			"lp_legal_name",
			"domicile_country",
			"tier_status",
			"rec_src",
		],
		notes: "Standard non-sensitive investor attributes.",
	},
	{
		id: "sat_limited_partner_pii",
		name: "sat_limited_partner_pii",
		kind: "SAT_PII",
		category: "Governance Restricted PII",
		primaryKey: "hk_limited_partner_hash + load_dts",
		parentEntity: "hub_limited_partner",
		classification: "RESTRICTED_PII",
		columns: [
			"hk_limited_partner_hash",
			"load_dts",
			"hash_diff",
			"wire_iban",
			"tax_id",
			"rec_src",
		],
		notes:
			"Isolated into dedicated PII satellite to enforce column-level RBAC masking.",
	},
	{
		id: "sat_limited_partner_legacy",
		name: "sat_limited_partner_legacy",
		kind: "SAT",
		category: "Legacy Source Context",
		primaryKey: "hk_limited_partner_hash + load_dts",
		parentEntity: "hub_limited_partner",
		columns: [
			"hk_limited_partner_hash",
			"load_dts",
			"hash_diff",
			"legacy_name",
			"account_status",
			"onboard_date",
			"rec_src",
		],
		notes: "Legacy GP Admin investor attributes.",
	},
	{
		id: "sat_lp_commitment_investran",
		name: "sat_lp_commitment_investran",
		kind: "SAT",
		category: "Commitment Financial Context",
		primaryKey: "hk_lnk_commitment_hash + load_dts",
		parentEntity: "lnk_lp_commitment",
		columns: [
			"hk_lnk_commitment_hash",
			"load_dts",
			"hash_diff",
			"commitment_amount_usd",
			"commitment_date",
			"eur_usd_rate",
			"uncalled_capital_usd",
			"nav_usd",
			"rec_src",
		],
		notes:
			"Gate 4 YAGNI cost guard merged FX rates directly into parent satellite.",
	},
	{
		id: "sat_portfolio_company_dealcloud",
		name: "sat_portfolio_company_dealcloud",
		kind: "SAT",
		category: "Company Profile Context",
		primaryKey: "hk_portfolio_company_hash + load_dts",
		parentEntity: "hub_portfolio_company",
		columns: [
			"hk_portfolio_company_hash",
			"load_dts",
			"hash_diff",
			"company_name",
			"country_iso",
			"rec_src",
		],
		notes: "DealCloud CRM portfolio company attributes.",
	},
	{
		id: "sat_portco_valuation_mnpi",
		name: "sat_portco_valuation_mnpi",
		kind: "SAT_PII",
		category: "Valuation Context (MNPI)",
		primaryKey: "hk_portfolio_company_hash + load_dts",
		parentEntity: "hub_portfolio_company",
		classification: "MNPI",
		columns: [
			"hk_portfolio_company_hash",
			"load_dts",
			"hash_diff",
			"ebitda_amount_usd",
			"ev_multiple",
			"rec_src",
		],
		notes:
			"Strictly isolated valuation satellite masked for non-cleared steward roles.",
	},
	{
		id: "sat_document_manifest",
		name: "sat_document_manifest",
		kind: "SAT",
		category: "Document Manifest",
		primaryKey: "hk_document_hash + load_dts",
		parentEntity: "hub_document",
		columns: [
			"hk_document_hash",
			"load_dts",
			"hash_diff",
			"file_sha256",
			"mime_type",
			"rec_src",
		],
		notes: "Content-addressable file metadata.",
	},

	// References
	{
		id: "ref_fund_strategy",
		name: "ref_fund_strategy",
		kind: "REF",
		category: "Reference Code Table",
		primaryKey: "strategy_code",
		columns: ["strategy_code", "strategy_label", "load_dts", "rec_src"],
		notes: "Allowed enumeration codes for private equity buyout strategies.",
	},
	{
		id: "ref_industry_sector",
		name: "ref_industry_sector",
		kind: "REF",
		category: "Reference Code Table",
		primaryKey: "sector_code",
		columns: ["sector_code", "sector_name", "load_dts", "rec_src"],
		notes: "Standard industry sector codes (B2B_SAAS, INDUSTRIAL_TECH).",
	},
];

export function TopologyCanvas() {
	const [selectedNodeId, setSelectedNodeId] = useState<string>(
		"hub_limited_partner",
	);
	const [kindFilter, setKindFilter] = useState<string>("ALL");

	const filteredNodes =
		kindFilter === "ALL"
			? TOPOLOGY_NODES
			: TOPOLOGY_NODES.filter((n) => {
					if (kindFilter === "GOVERNANCE") {
						return n.kind === "SAT_PII" || n.classification !== undefined;
					}
					return n.kind === kindFilter;
				});

	const defaultNode = TOPOLOGY_NODES[0] as TopologyNode;
	const activeNode =
		TOPOLOGY_NODES.find((n) => n.id === selectedNodeId) ?? defaultNode;

	return (
		<div
			aria-label="Interactive ELM Topology Canvas"
			data-testid="topology-canvas"
			style={{
				backgroundColor: "var(--dv-surface-1)",
				border: "1px solid var(--dv-border-subtle)",
				borderRadius: "0.5rem",
				padding: "1rem",
				display: "flex",
				flexDirection: "column",
				gap: "1rem",
			}}
		>
			{/* Top Bar with Filter Controls */}
			<div
				style={{
					display: "flex",
					justifyContent: "space-between",
					alignItems: "center",
					flexWrap: "wrap",
					gap: "0.5rem",
				}}
			>
				<div>
					<strong
						style={{ fontSize: "0.8125rem", color: "var(--dv-text-primary)" }}
					>
						Synthesized Data Vault 2.0 Topology Viewport (WCAG AA Dual-Encoded)
					</strong>
					<div
						style={{ fontSize: "0.7rem", color: "var(--dv-text-secondary)" }}
					>
						Shape + Color + Text Prefix grammar: [HUB], [LNK], [SAL], [SAT],
						[SAT-PII], [REF]
					</div>
				</div>

				<div style={{ display: "flex", gap: "0.25rem" }}>
					{[
						{ id: "ALL", label: "All Entities (22)" },
						{ id: "HUB", label: "Hubs (4)" },
						{ id: "LNK", label: "Links (4)" },
						{ id: "SAL", label: "SAL (1)" },
						{ id: "SAT", label: "Sats (9)" },
						{ id: "GOVERNANCE", label: "PII/MNPI (2)" },
						{ id: "REF", label: "Refs (2)" },
					].map((tab) => {
						const isSelected = kindFilter === tab.id;
						return (
							<button
								key={tab.id}
								type="button"
								onClick={() => setKindFilter(tab.id)}
								style={{
									backgroundColor: isSelected
										? "var(--dv-surface-3)"
										: "var(--dv-surface-2)",
									color: isSelected
										? "var(--dv-text-primary)"
										: "var(--dv-text-secondary)",
									border: isSelected
										? "1px solid var(--dv-hub-accent)"
										: "1px solid var(--dv-border-subtle)",
									borderRadius: "0.25rem",
									padding: "0.25rem 0.45rem",
									fontSize: "0.7rem",
									fontWeight: isSelected ? 700 : 500,
									cursor: "pointer",
								}}
							>
								{tab.label}
							</button>
						);
					})}
				</div>
			</div>

			{/* Nodes Viewport Grid */}
			<div
				style={{
					display: "grid",
					gridTemplateColumns: "repeat(auto-fill, minmax(180px, 1fr))",
					gap: "0.5rem",
					maxHeight: "340px",
					overflowY: "auto",
					padding: "0.25rem",
				}}
			>
				{filteredNodes.map((node) => {
					const grammar =
						ENTITY_VISUAL_GRAMMAR[node.kind] ?? ENTITY_VISUAL_GRAMMAR.HUB;
					const isSelected = node.id === selectedNodeId;

					return (
						<button
							key={node.id}
							type="button"
							data-testid={`node-${node.id}`}
							onClick={() => setSelectedNodeId(node.id)}
							style={{
								display: "block",
								width: "100%",
								textAlign: "left",
								backgroundColor: isSelected
									? "var(--dv-surface-2)"
									: "var(--dv-surface-1)",
								border: `${grammar.borderStyle} var(${grammar.cssColorVar})`,
								borderRadius:
									node.kind === "HUB"
										? "0.5rem"
										: node.kind === "SAT"
											? "9999px"
											: "0.375rem",
								padding: "0.5rem 0.65rem",
								cursor: "pointer",
								position: "relative",
								boxShadow: isSelected
									? "0 0 0 2px var(--dv-text-primary)"
									: undefined,
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
										fontSize: "0.6875rem",
										fontWeight: 700,
										color: `var(${grammar.cssColorVar})`,
										fontFamily: "ui-monospace, monospace",
									}}
								>
									{grammar.prefixBadge}
								</span>
								{node.classification && (
									<span
										style={{
											fontSize: "0.6rem",
											fontWeight: 700,
											color: "var(--dv-sat-pii-accent)",
											backgroundColor: "rgba(168, 85, 247, 0.15)",
											padding: "0.05rem 0.25rem",
											borderRadius: "0.2rem",
										}}
									>
										🔒 {node.classification}
									</span>
								)}
							</div>

							<div
								style={{
									fontWeight: 700,
									fontSize: "0.75rem",
									color: "var(--dv-text-primary)",
									wordBreak: "break-all",
								}}
							>
								{node.name}
							</div>

							<div
								style={{
									fontSize: "0.65rem",
									color: "var(--dv-text-secondary)",
									marginTop: "0.2rem",
								}}
							>
								{node.category}
							</div>
						</button>
					);
				})}
			</div>

			{/* Selected Node Details Card */}
			<div
				data-testid="selected-node-details"
				style={{
					backgroundColor: "var(--dv-surface-2)",
					border: "1px solid var(--dv-border-strong)",
					borderRadius: "0.375rem",
					padding: "0.75rem",
					fontSize: "0.75rem",
				}}
			>
				<div
					style={{
						display: "flex",
						justifyContent: "space-between",
						alignItems: "center",
						marginBottom: "0.4rem",
					}}
				>
					<div>
						<strong
							style={{ fontSize: "0.875rem", color: "var(--dv-text-primary)" }}
						>
							{activeNode.name}
						</strong>
						<span
							style={{
								marginLeft: "0.5rem",
								fontFamily: "ui-monospace, monospace",
								color: "var(--dv-text-secondary)",
							}}
						>
							({activeNode.category})
						</span>
					</div>

					{activeNode.classification && (
						<span
							style={{
								fontSize: "0.7rem",
								fontWeight: 700,
								color: "var(--dv-sat-pii-accent)",
								border: "1px solid var(--dv-sat-pii-accent)",
								padding: "0.1rem 0.35rem",
								borderRadius: "0.25rem",
							}}
						>
							🔒 RBAC: {activeNode.classification}
						</span>
					)}
				</div>

				<div
					style={{
						display: "grid",
						gridTemplateColumns: "1fr 1fr",
						gap: "0.75rem",
						margin: "0.5rem 0",
					}}
				>
					<div>
						<div
							style={{ color: "var(--dv-text-secondary)", fontSize: "0.7rem" }}
						>
							Primary Key / Hash Key:
						</div>
						<code style={{ color: "var(--dv-hub-accent)" }}>
							{activeNode.primaryKey}
						</code>
						{activeNode.parentEntity && (
							<div style={{ marginTop: "0.3rem" }}>
								<span
									style={{
										color: "var(--dv-text-secondary)",
										fontSize: "0.7rem",
									}}
								>
									Parent Attachment:{" "}
								</span>
								<code>{activeNode.parentEntity}</code>
							</div>
						)}
					</div>

					<div>
						<div
							style={{ color: "var(--dv-text-secondary)", fontSize: "0.7rem" }}
						>
							Columns ({activeNode.columns.length}):
						</div>
						<div
							style={{
								fontFamily: "ui-monospace, monospace",
								fontSize: "0.6875rem",
								color: "var(--dv-text-primary)",
							}}
						>
							{activeNode.columns.join(", ")}
						</div>
					</div>
				</div>

				{activeNode.semanticPill && (
					<div style={{ marginBottom: "0.4rem" }}>
						<span
							style={{ color: "var(--dv-text-secondary)", fontSize: "0.7rem" }}
						>
							Semantic Hash Pill:{" "}
						</span>
						<code
							style={{
								backgroundColor: "var(--dv-surface-1)",
								border: "1px solid var(--dv-border-subtle)",
								borderRadius: "0.2rem",
								padding: "0.1rem 0.35rem",
							}}
						>
							{activeNode.semanticPill}
						</code>
					</div>
				)}

				<div style={{ color: "var(--dv-text-secondary)", fontSize: "0.7rem" }}>
					<strong>Architectural Role: </strong>
					{activeNode.notes}
				</div>
			</div>
		</div>
	);
}
