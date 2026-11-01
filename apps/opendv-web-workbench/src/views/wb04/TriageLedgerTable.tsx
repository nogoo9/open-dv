/**
 * Column Triage Ledger Table (`TriageLedgerTable.tsx`)
 * Interactive searchable ledger table for Gate 1 column_triage_ledger (VIZ-GATE-05 Part A) (`docs/06` §3.6).
 */

import type { VaultObservabilityEventDTO } from "@opendv/protocol-ts";
import { useMemo, useState } from "react";
import type {
	GateCardPluginProps,
	WorkbenchGateCardPlugin,
} from "../../plugins/plugin-types";

export const VIZ_GATE_05_ID = "VIZ-GATE-05";

export interface TriageLedgerEntry {
	table: string;
	column: string;
	dataType: string;
	disposition:
		| "BUSINESS_KEY"
		| "FOREIGN_KEY_REF"
		| "OPTIONAL_FOREIGN_KEY_REF"
		| "DEGENERATE_KEY"
		| "DESCRIPTIVE_PAYLOAD"
		| "REFERENCE_CODE"
		| "TECHNICAL_METADATA"
		| "SYSTEM_SURROGATE_DROPPED";
	targetEntity: string;
	rationale: string;
}

export const SAMPLE_TRIAGE_ENTRIES: readonly TriageLedgerEntry[] = [
	// Keys
	{
		table: "investran.src_pe_funds",
		column: "fund_code",
		dataType: "VARCHAR(32)",
		disposition: "BUSINESS_KEY",
		targetEntity: "hub_fund.fund_code",
		rationale: "Natural business key for buyout fund vehicle.",
	},
	{
		table: "investran.src_lp_investors",
		column: "lp_id",
		dataType: "VARCHAR(32)",
		disposition: "BUSINESS_KEY",
		targetEntity: "hub_limited_partner.lp_id",
		rationale: "Global master LP natural key.",
	},
	{
		table: "dealcloud.src_portfolio_companies",
		column: "lei",
		dataType: "VARCHAR(20)",
		disposition: "BUSINESS_KEY",
		targetEntity: "hub_portfolio_company.lei",
		rationale: "ISO 17442 Global LEI business key.",
	},
	{
		table: "legacy_gp.src_legacy_lp_roster",
		column: "lp_num",
		dataType: "VARCHAR(32)",
		disposition: "BUSINESS_KEY",
		targetEntity: "hub_limited_partner.lp_id",
		rationale: "Tier-3 local LP key requiring BKCC=LEGACY_GP_ADMIN.",
	},
	{
		table: "dms.documents",
		column: "doc_uri",
		dataType: "VARCHAR(255)",
		disposition: "BUSINESS_KEY",
		targetEntity: "hub_document.doc_uri",
		rationale: "Content-addressable S3 URI natural key.",
	},

	// Foreign Keys & Split Link
	{
		table: "investran.src_lp_commitments_and_calls",
		column: "fund_code",
		dataType: "VARCHAR(32)",
		disposition: "FOREIGN_KEY_REF",
		targetEntity: "lnk_lp_commitment.hk_fund_hash",
		rationale: "Mandatory driving foreign key to hub_fund.",
	},
	{
		table: "investran.src_lp_commitments_and_calls",
		column: "lp_id",
		dataType: "VARCHAR(32)",
		disposition: "FOREIGN_KEY_REF",
		targetEntity: "lnk_lp_commitment.hk_limited_partner_hash",
		rationale: "Mandatory driving foreign key to hub_limited_partner.",
	},
	{
		table: "investran.src_lp_commitments_and_calls",
		column: "commitment_id",
		dataType: "VARCHAR(32)",
		disposition: "DEGENERATE_KEY",
		targetEntity: "lnk_lp_commitment.commitment_id",
		rationale: "Degenerate transaction key at link grain.",
	},
	{
		table: "investran.src_lp_commitments_and_calls",
		column: "co_invest_portco_lei",
		dataType: "VARCHAR(20)",
		disposition: "OPTIONAL_FOREIGN_KEY_REF",
		targetEntity: "lnk_lp_commitment_co_invest.hk_portco_hash",
		rationale:
			"Sparse contextual FK (71.4% NULL) split under Alg A.4 into co-invest link.",
	},
	{
		table: "mdm.src_lp_steward_crosswalk",
		column: "master_lp_id",
		dataType: "VARCHAR(32)",
		disposition: "FOREIGN_KEY_REF",
		targetEntity: "sal_limited_partner.master_hk_lp_hash",
		rationale: "Master LP side of Same-As Link.",
	},
	{
		table: "mdm.src_lp_steward_crosswalk",
		column: "legacy_lp_num",
		dataType: "VARCHAR(32)",
		disposition: "FOREIGN_KEY_REF",
		targetEntity: "sal_limited_partner.alias_hk_lp_hash",
		rationale: "Legacy alias side of Same-As Link.",
	},

	// Reference Codes
	{
		table: "investran.src_pe_funds",
		column: "strategy",
		dataType: "VARCHAR(32)",
		disposition: "REFERENCE_CODE",
		targetEntity: "ref_fund_strategy.strategy_code",
		rationale: "Matched to ref_fund_strategy enumeration codes.",
	},
	{
		table: "dealcloud.src_portfolio_companies",
		column: "sector",
		dataType: "VARCHAR(32)",
		disposition: "REFERENCE_CODE",
		targetEntity: "ref_industry_sector.sector_code",
		rationale: "Matched to ref_industry_sector industry codes.",
	},

	// Dropped Surrogate (Tombstone)
	{
		table: "investran.src_lp_investors",
		column: "row_seq_id",
		dataType: "BIGINT",
		disposition: "SYSTEM_SURROGATE_DROPPED",
		targetEntity: "DROPPED",
		rationale:
			"Monotonic integer surrogate (Δ=+1). Explicitly quarantined in DROPPED sink to prevent hub pollution.",
	},

	// Selected Payload Attributes
	{
		table: "investran.src_pe_funds",
		column: "fund_name",
		dataType: "VARCHAR(128)",
		disposition: "DESCRIPTIVE_PAYLOAD",
		targetEntity: "sat_fund_investran.fund_name",
		rationale: "Core fund descriptive payload.",
	},
	{
		table: "investran.src_lp_investors",
		column: "wire_iban",
		dataType: "VARCHAR(34)",
		disposition: "DESCRIPTIVE_PAYLOAD",
		targetEntity: "sat_limited_partner_pii.wire_iban",
		rationale:
			"PII_HIGH governance tag; isolated into dedicated PII satellite.",
	},
	{
		table: "dealcloud.src_portfolio_companies",
		column: "ebitda_amount_usd",
		dataType: "NUMERIC(18,2)",
		disposition: "DESCRIPTIVE_PAYLOAD",
		targetEntity: "sat_portco_valuation_mnpi.ebitda_amount_usd",
		rationale:
			"MNPI_CONFIDENTIAL governance tag; isolated into confidential valuation sat.",
	},
	{
		table: "investran.src_lp_commitments_and_calls",
		column: "eur_usd_rate",
		dataType: "NUMERIC(10,4)",
		disposition: "DESCRIPTIVE_PAYLOAD",
		targetEntity: "sat_lp_commitment_investran.eur_usd_rate",
		rationale:
			"Gate 4 YAGNI cost guard auto-merged into parent commitment sat.",
	},
];

export function TriageLedgerTable() {
	const [activeFilter, setActiveFilter] = useState<string>("ALL");
	const [searchQuery, setSearchQuery] = useState<string>("");

	const filteredEntries = useMemo(() => {
		let list = [...SAMPLE_TRIAGE_ENTRIES];
		if (activeFilter === "KEYS") {
			list = list.filter((e) => e.disposition === "BUSINESS_KEY");
		} else if (activeFilter === "FOREIGN_KEYS") {
			list = list.filter(
				(e) =>
					e.disposition === "FOREIGN_KEY_REF" ||
					e.disposition === "OPTIONAL_FOREIGN_KEY_REF" ||
					e.disposition === "DEGENERATE_KEY",
			);
		} else if (activeFilter === "PAYLOAD") {
			list = list.filter((e) => e.disposition === "DESCRIPTIVE_PAYLOAD");
		} else if (activeFilter === "REFERENCES") {
			list = list.filter((e) => e.disposition === "REFERENCE_CODE");
		} else if (activeFilter === "DROPPED") {
			list = list.filter((e) => e.disposition === "SYSTEM_SURROGATE_DROPPED");
		}

		if (searchQuery.trim()) {
			const q = searchQuery.toLowerCase();
			list = list.filter(
				(e) =>
					e.column.toLowerCase().includes(q) ||
					e.table.toLowerCase().includes(q) ||
					e.targetEntity.toLowerCase().includes(q) ||
					e.rationale.toLowerCase().includes(q),
			);
		}
		return list;
	}, [activeFilter, searchQuery]);

	return (
		<div
			aria-label="Pass 1 Column Triage Ledger Table"
			data-testid="triage-ledger-table"
			data-gate-id={VIZ_GATE_05_ID}
			style={{
				backgroundColor: "var(--dv-surface-1)",
				border: "1px solid var(--dv-border-subtle)",
				borderRadius: "0.5rem",
				padding: "1rem",
				display: "flex",
				flexDirection: "column",
				gap: "0.75rem",
			}}
		>
			{/* Top Bar with Filter Tabs & Search */}
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
						Gate 1 Column Triage Ledger (VIZ-GATE-05 Part A)
					</strong>
					<div
						style={{ fontSize: "0.7rem", color: "var(--dv-text-secondary)" }}
					>
						Searchable ledger proving 100% column routing and surrogate
						tombstoning
					</div>
				</div>

				<div style={{ display: "flex", gap: "0.5rem", alignItems: "center" }}>
					<input
						type="text"
						placeholder="Search ledger..."
						value={searchQuery}
						onChange={(e) => setSearchQuery(e.target.value)}
						style={{
							backgroundColor: "var(--dv-surface-2)",
							color: "var(--dv-text-primary)",
							border: "1px solid var(--dv-border-strong)",
							borderRadius: "0.25rem",
							padding: "0.25rem 0.5rem",
							fontSize: "0.75rem",
						}}
					/>

					<div style={{ display: "flex", gap: "0.25rem" }}>
						{[
							{ id: "ALL", label: "All" },
							{ id: "KEYS", label: "Keys" },
							{ id: "FOREIGN_KEYS", label: "FKs" },
							{ id: "PAYLOAD", label: "Payload" },
							{ id: "REFERENCES", label: "Refs" },
							{ id: "DROPPED", label: "Dropped" },
						].map((tab) => {
							const isSelected = activeFilter === tab.id;
							return (
								<button
									key={tab.id}
									type="button"
									onClick={() => setActiveFilter(tab.id)}
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
			</div>

			{/* Ledger Table */}
			<div style={{ overflowX: "auto" }}>
				<table
					style={{
						width: "100%",
						borderCollapse: "collapse",
						fontSize: "0.75rem",
						fontFamily: "ui-monospace, monospace",
					}}
				>
					<thead>
						<tr
							style={{
								borderBottom: "1.5px solid var(--dv-border-strong)",
								backgroundColor: "var(--dv-surface-2)",
								textAlign: "left",
							}}
						>
							<th style={{ padding: "0.4rem 0.6rem" }}>Source Table</th>
							<th style={{ padding: "0.4rem 0.6rem" }}>Column</th>
							<th style={{ padding: "0.4rem 0.6rem" }}>Type</th>
							<th style={{ padding: "0.4rem 0.6rem" }}>ELM Disposition</th>
							<th style={{ padding: "0.4rem 0.6rem" }}>Target DV Entity</th>
							<th style={{ padding: "0.4rem 0.6rem" }}>Triage Rationale</th>
						</tr>
					</thead>
					<tbody>
						{filteredEntries.map((row) => {
							const isDropped = row.disposition === "SYSTEM_SURROGATE_DROPPED";
							const isKey = row.disposition === "BUSINESS_KEY";
							const isOpt = row.disposition === "OPTIONAL_FOREIGN_KEY_REF";

							let dispColor = "var(--dv-text-secondary)";
							if (isDropped) dispColor = "var(--dv-surrogate-accent)";
							else if (isKey) dispColor = "var(--dv-hub-accent)";
							else if (isOpt) dispColor = "var(--dv-sat-accent)";
							else if (row.disposition === "FOREIGN_KEY_REF")
								dispColor = "var(--dv-lnk-accent)";
							else if (row.disposition === "REFERENCE_CODE")
								dispColor = "var(--dv-ref-accent)";

							return (
								<tr
									key={`${row.table}.${row.column}`}
									data-testid={`ledger-row-${row.column}`}
									style={{
										borderBottom: "1px solid var(--dv-border-subtle)",
										backgroundColor: isDropped
											? "rgba(100, 116, 139, 0.1)"
											: undefined,
									}}
								>
									<td
										style={{
											padding: "0.4rem 0.6rem",
											color: "var(--dv-text-secondary)",
										}}
									>
										{row.table.split(".")[1]}
									</td>
									<td
										style={{
											padding: "0.4rem 0.6rem",
											fontWeight: isDropped || isKey ? 700 : 500,
											color: isDropped
												? "var(--dv-gate-reject)"
												: "var(--dv-text-primary)",
										}}
									>
										{row.column}
										{isDropped && " ⚰️"}
									</td>
									<td
										style={{
											padding: "0.4rem 0.6rem",
											color: "var(--dv-text-muted)",
										}}
									>
										{row.dataType}
									</td>
									<td style={{ padding: "0.4rem 0.6rem" }}>
										<span
											style={{
												color: dispColor,
												border: `1px solid ${dispColor}`,
												borderRadius: "0.2rem",
												padding: "0.1rem 0.35rem",
												fontSize: "0.6875rem",
												fontWeight: 600,
											}}
										>
											[{row.disposition}]
										</span>
									</td>
									<td
										style={{
											padding: "0.4rem 0.6rem",
											fontWeight: isDropped ? 700 : 400,
											color: isDropped
												? "var(--dv-surrogate-accent)"
												: "var(--dv-text-primary)",
										}}
									>
										{row.targetEntity}
									</td>
									<td
										style={{
											padding: "0.4rem 0.6rem",
											fontSize: "0.7rem",
											color: "var(--dv-text-secondary)",
											fontFamily: "ui-sans-serif, sans-serif",
										}}
									>
										{row.rationale}
									</td>
								</tr>
							);
						})}
					</tbody>
				</table>
			</div>
		</div>
	);
}

export const VIZ_GATE_05_PART_A_PLUGIN: WorkbenchGateCardPlugin = {
	gateId: VIZ_GATE_05_ID,
	title: "Gate 1 Column Triage Ledger & Surrogate Sink",
	ruleIds: ["DV-COL-001", "DV-COL-002", "DV-COL-012", "COLUMN-TRIAGE-01"],
	component: (_props: GateCardPluginProps) => <TriageLedgerTable />,
};
