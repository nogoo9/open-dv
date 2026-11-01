/**
 * WB-02: Empirical Column Profiler & Candidate Discovery Matrix (`WB02EmpiricalProfiler.tsx`)
 * Primary View Plugin for STAGE_01_SCOUT_SOURCE (VIZ-STEP-02) (`docs/06` §3.3, `docs/10` §4).
 */

import { useMemo, useState } from "react";
import type { ViewPluginProps } from "../../plugins/plugin-types";
import { VERDICT_VISUAL_GRAMMAR } from "../../theme/theme-registry";
import { FourQuadrantScatterPlot } from "./FourQuadrantScatterPlot";
import { InclusionDependencyDAG } from "./InclusionDependencyDAG";

export interface RelationalColumnProfile {
	table: string;
	column: string;
	dataType: string;
	uniqueness: number;
	nonNullRatio: number;
	entropy: number;
	classification:
		| "BUSINESS_KEY"
		| "SURROGATE_DROP"
		| "FOREIGN_KEY"
		| "OPTIONAL_FK"
		| "REF_CODE"
		| "PAYLOAD";
	notes: string;
}

export const ALL_44_RELATIONAL_COLUMNS: readonly RelationalColumnProfile[] = [
	// 1. investran.src_pe_funds (9 columns)
	{
		table: "investran.src_pe_funds",
		column: "fund_code",
		dataType: "VARCHAR(32)",
		uniqueness: 1.0,
		nonNullRatio: 1.0,
		entropy: 2.8,
		classification: "BUSINESS_KEY",
		notes: "Primary natural key for hub_fund.",
	},
	{
		table: "investran.src_pe_funds",
		column: "fund_name",
		dataType: "VARCHAR(128)",
		uniqueness: 0.67,
		nonNullRatio: 1.0,
		entropy: 2.1,
		classification: "PAYLOAD",
		notes: "Descriptive fund title.",
	},
	{
		table: "investran.src_pe_funds",
		column: "vintage_year",
		dataType: "INTEGER",
		uniqueness: 0.33,
		nonNullRatio: 1.0,
		entropy: 1.1,
		classification: "PAYLOAD",
		notes: "Fund inception year.",
	},
	{
		table: "investran.src_pe_funds",
		column: "target_size_usd",
		dataType: "NUMERIC(18,2)",
		uniqueness: 0.67,
		nonNullRatio: 1.0,
		entropy: 2.2,
		classification: "PAYLOAD",
		notes: "Target fund commitment volume.",
	},
	{
		table: "investran.src_pe_funds",
		column: "strategy",
		dataType: "VARCHAR(32)",
		uniqueness: 0.33,
		nonNullRatio: 1.0,
		entropy: 1.1,
		classification: "REF_CODE",
		notes: "Matched to ref_fund_strategy (BUYOUT, CO_INVEST).",
	},
	{
		table: "investran.src_pe_funds",
		column: "currency",
		dataType: "VARCHAR(3)",
		uniqueness: 0.33,
		nonNullRatio: 1.0,
		entropy: 1.0,
		classification: "PAYLOAD",
		notes: "Base accounting currency (USD).",
	},
	{
		table: "investran.src_pe_funds",
		column: "domicile_state",
		dataType: "VARCHAR(2)",
		uniqueness: 0.33,
		nonNullRatio: 1.0,
		entropy: 1.0,
		classification: "PAYLOAD",
		notes: "Legal registration state (DE).",
	},
	{
		table: "investran.src_pe_funds",
		column: "gp_entity_name",
		dataType: "VARCHAR(128)",
		uniqueness: 0.67,
		nonNullRatio: 1.0,
		entropy: 2.0,
		classification: "PAYLOAD",
		notes: "General Partner legal manager.",
	},
	{
		table: "investran.src_pe_funds",
		column: "created_ts",
		dataType: "TIMESTAMP",
		uniqueness: 0.67,
		nonNullRatio: 1.0,
		entropy: 2.0,
		classification: "PAYLOAD",
		notes: "Source record creation timestamp.",
	},

	// 2. investran.src_lp_investors (8 columns)
	{
		table: "investran.src_lp_investors",
		column: "lp_id",
		dataType: "VARCHAR(32)",
		uniqueness: 1.0,
		nonNullRatio: 1.0,
		entropy: 2.5,
		classification: "BUSINESS_KEY",
		notes: "Master LP code for hub_limited_partner.",
	},
	{
		table: "investran.src_lp_investors",
		column: "lp_legal_name",
		dataType: "VARCHAR(128)",
		uniqueness: 1.0,
		nonNullRatio: 1.0,
		entropy: 2.4,
		classification: "PAYLOAD",
		notes: "Legal LP entity name (CalPERS).",
	},
	{
		table: "investran.src_lp_investors",
		column: "domicile_country",
		dataType: "VARCHAR(3)",
		uniqueness: 0.5,
		nonNullRatio: 1.0,
		entropy: 1.5,
		classification: "PAYLOAD",
		notes: "Tax residence ISO country code.",
	},
	{
		table: "investran.src_lp_investors",
		column: "tier_status",
		dataType: "VARCHAR(16)",
		uniqueness: 0.25,
		nonNullRatio: 1.0,
		entropy: 1.2,
		classification: "PAYLOAD",
		notes: "Institutional investor status tier.",
	},
	{
		table: "investran.src_lp_investors",
		column: "wire_iban",
		dataType: "VARCHAR(34)",
		uniqueness: 1.0,
		nonNullRatio: 1.0,
		entropy: 2.6,
		classification: "PAYLOAD",
		notes:
			"Confidential banking wire details; routes to sat_limited_partner_pii.",
	},
	{
		table: "investran.src_lp_investors",
		column: "tax_id",
		dataType: "VARCHAR(32)",
		uniqueness: 1.0,
		nonNullRatio: 1.0,
		entropy: 2.5,
		classification: "PAYLOAD",
		notes: "Taxpayer ID; routes to sat_limited_partner_pii.",
	},
	{
		table: "investran.src_lp_investors",
		column: "row_seq_id",
		dataType: "BIGINT",
		uniqueness: 1.0,
		nonNullRatio: 1.0,
		entropy: 1.38,
		classification: "SURROGATE_DROP",
		notes:
			"Monotonic integer surrogate (Delta=+1). Tombstoned in DROPPED sink.",
	},
	{
		table: "investran.src_lp_investors",
		column: "created_ts",
		dataType: "TIMESTAMP",
		uniqueness: 0.75,
		nonNullRatio: 1.0,
		entropy: 2.0,
		classification: "PAYLOAD",
		notes: "Source record creation timestamp.",
	},

	// 3. investran.src_lp_commitments_and_calls (10 columns)
	{
		table: "investran.src_lp_commitments_and_calls",
		column: "commitment_id",
		dataType: "VARCHAR(32)",
		uniqueness: 1.0,
		nonNullRatio: 1.0,
		entropy: 2.7,
		classification: "BUSINESS_KEY",
		notes: "Degenerate commitment transaction key in lnk_lp_commitment.",
	},
	{
		table: "investran.src_lp_commitments_and_calls",
		column: "fund_code",
		dataType: "VARCHAR(32)",
		uniqueness: 0.5,
		nonNullRatio: 1.0,
		entropy: 1.8,
		classification: "FOREIGN_KEY",
		notes: "Foreign key reference to hub_fund.",
	},
	{
		table: "investran.src_lp_commitments_and_calls",
		column: "lp_id",
		dataType: "VARCHAR(32)",
		uniqueness: 0.75,
		nonNullRatio: 1.0,
		entropy: 2.1,
		classification: "FOREIGN_KEY",
		notes: "Foreign key reference to hub_limited_partner.",
	},
	{
		table: "investran.src_lp_commitments_and_calls",
		column: "commitment_amount_usd",
		dataType: "NUMERIC(18,2)",
		uniqueness: 0.8,
		nonNullRatio: 1.0,
		entropy: 2.3,
		classification: "PAYLOAD",
		notes: "Committed capital volume.",
	},
	{
		table: "investran.src_lp_commitments_and_calls",
		column: "commitment_date",
		dataType: "DATE",
		uniqueness: 0.6,
		nonNullRatio: 1.0,
		entropy: 2.0,
		classification: "PAYLOAD",
		notes: "Legal signing date.",
	},
	{
		table: "investran.src_lp_commitments_and_calls",
		column: "co_invest_portco_lei",
		dataType: "VARCHAR(20)",
		uniqueness: 0.2857,
		nonNullRatio: 0.2857,
		entropy: 1.2,
		classification: "OPTIONAL_FK",
		notes:
			"Sparse contextual FK (71.4% NULL) split into lnk_lp_commitment_co_invest.",
	},
	{
		table: "investran.src_lp_commitments_and_calls",
		column: "eur_usd_rate",
		dataType: "NUMERIC(10,4)",
		uniqueness: 0.5,
		nonNullRatio: 1.0,
		entropy: 1.9,
		classification: "PAYLOAD",
		notes: "Exchange rate payload; merged under Gate 4 YAGNI.",
	},
	{
		table: "investran.src_lp_commitments_and_calls",
		column: "uncalled_capital_usd",
		dataType: "NUMERIC(18,2)",
		uniqueness: 0.8,
		nonNullRatio: 1.0,
		entropy: 2.3,
		classification: "PAYLOAD",
		notes: "Remaining callable commitment.",
	},
	{
		table: "investran.src_lp_commitments_and_calls",
		column: "nav_usd",
		dataType: "NUMERIC(18,2)",
		uniqueness: 0.8,
		nonNullRatio: 1.0,
		entropy: 2.3,
		classification: "PAYLOAD",
		notes: "Quarterly net asset value.",
	},
	{
		table: "investran.src_lp_commitments_and_calls",
		column: "updated_ts",
		dataType: "TIMESTAMP",
		uniqueness: 0.8,
		nonNullRatio: 1.0,
		entropy: 2.2,
		classification: "PAYLOAD",
		notes: "Record update timestamp.",
	},

	// 4. dealcloud.src_portfolio_companies (7 columns)
	{
		table: "dealcloud.src_portfolio_companies",
		column: "lei",
		dataType: "VARCHAR(20)",
		uniqueness: 1.0,
		nonNullRatio: 1.0,
		entropy: 2.7,
		classification: "BUSINESS_KEY",
		notes: "ISO 17442 Global LEI for hub_portfolio_company.",
	},
	{
		table: "dealcloud.src_portfolio_companies",
		column: "deal_code",
		dataType: "VARCHAR(32)",
		uniqueness: 1.0,
		nonNullRatio: 1.0,
		entropy: 2.6,
		classification: "BUSINESS_KEY",
		notes: "Alternate internal deal code identifier.",
	},
	{
		table: "dealcloud.src_portfolio_companies",
		column: "company_name",
		dataType: "VARCHAR(128)",
		uniqueness: 1.0,
		nonNullRatio: 1.0,
		entropy: 2.5,
		classification: "PAYLOAD",
		notes: "Portfolio company legal name.",
	},
	{
		table: "dealcloud.src_portfolio_companies",
		column: "sector",
		dataType: "VARCHAR(32)",
		uniqueness: 0.4,
		nonNullRatio: 1.0,
		entropy: 1.2,
		classification: "REF_CODE",
		notes: "Matched to ref_industry_sector.",
	},
	{
		table: "dealcloud.src_portfolio_companies",
		column: "country_iso",
		dataType: "VARCHAR(3)",
		uniqueness: 0.4,
		nonNullRatio: 1.0,
		entropy: 1.3,
		classification: "PAYLOAD",
		notes: "Country of operations.",
	},
	{
		table: "dealcloud.src_portfolio_companies",
		column: "ebitda_amount_usd",
		dataType: "NUMERIC(18,2)",
		uniqueness: 1.0,
		nonNullRatio: 1.0,
		entropy: 2.7,
		classification: "PAYLOAD",
		notes:
			"Confidential financial metric; routes to sat_portco_valuation_mnpi.",
	},
	{
		table: "dealcloud.src_portfolio_companies",
		column: "ev_multiple",
		dataType: "NUMERIC(6,2)",
		uniqueness: 0.8,
		nonNullRatio: 1.0,
		entropy: 2.2,
		classification: "PAYLOAD",
		notes: "Valuation multiple; routes to sat_portco_valuation_mnpi.",
	},

	// 5. legacy_gp.src_legacy_lp_roster (4 columns)
	{
		table: "legacy_gp.src_legacy_lp_roster",
		column: "lp_num",
		dataType: "VARCHAR(32)",
		uniqueness: 1.0,
		nonNullRatio: 1.0,
		entropy: 2.2,
		classification: "BUSINESS_KEY",
		notes: "Tier-3 locally scoped key requiring bkcc_prefix='LEGACY_GP_ADMIN'.",
	},
	{
		table: "legacy_gp.src_legacy_lp_roster",
		column: "legacy_name",
		dataType: "VARCHAR(128)",
		uniqueness: 1.0,
		nonNullRatio: 1.0,
		entropy: 2.1,
		classification: "PAYLOAD",
		notes: "Legacy investor name (Bavarian Pension Reserve Fund).",
	},
	{
		table: "legacy_gp.src_legacy_lp_roster",
		column: "account_status",
		dataType: "VARCHAR(16)",
		uniqueness: 0.33,
		nonNullRatio: 1.0,
		entropy: 1.1,
		classification: "PAYLOAD",
		notes: "Account activity flag.",
	},
	{
		table: "legacy_gp.src_legacy_lp_roster",
		column: "onboard_date",
		dataType: "DATE",
		uniqueness: 0.67,
		nonNullRatio: 1.0,
		entropy: 2.0,
		classification: "PAYLOAD",
		notes: "Legacy onboarding date.",
	},

	// 6. dms.documents (4 columns)
	{
		table: "dms.documents",
		column: "doc_id",
		dataType: "VARCHAR(32)",
		uniqueness: 1.0,
		nonNullRatio: 1.0,
		entropy: 2.4,
		classification: "BUSINESS_KEY",
		notes: "Internal DMS document identifier.",
	},
	{
		table: "dms.documents",
		column: "doc_uri",
		dataType: "VARCHAR(255)",
		uniqueness: 1.0,
		nonNullRatio: 1.0,
		entropy: 2.6,
		classification: "BUSINESS_KEY",
		notes: "S3 URI natural key for hub_document.",
	},
	{
		table: "dms.documents",
		column: "file_sha256",
		dataType: "VARCHAR(64)",
		uniqueness: 1.0,
		nonNullRatio: 1.0,
		entropy: 2.8,
		classification: "PAYLOAD",
		notes: "Cryptographic payload hash in sat_document_manifest.",
	},
	{
		table: "dms.documents",
		column: "mime_type",
		dataType: "VARCHAR(64)",
		uniqueness: 0.25,
		nonNullRatio: 1.0,
		entropy: 1.0,
		classification: "PAYLOAD",
		notes: "Document content format (application/pdf).",
	},

	// 7. mdm.src_lp_steward_crosswalk (2 columns)
	{
		table: "mdm.src_lp_steward_crosswalk",
		column: "master_lp_id",
		dataType: "VARCHAR(32)",
		uniqueness: 0.5,
		nonNullRatio: 1.0,
		entropy: 1.5,
		classification: "FOREIGN_KEY",
		notes: "Links to master hub_limited_partner via sal_limited_partner.",
	},
	{
		table: "mdm.src_lp_steward_crosswalk",
		column: "legacy_lp_num",
		dataType: "VARCHAR(32)",
		uniqueness: 0.5,
		nonNullRatio: 1.0,
		entropy: 1.5,
		classification: "FOREIGN_KEY",
		notes: "Links to legacy hub_limited_partner via sal_limited_partner.",
	},
];

export function WB02EmpiricalProfiler({
	currentEvent,
	onInspectProof,
}: ViewPluginProps) {
	const [searchTerm, setSearchTerm] = useState<string>("");
	const [selectedClassification, setSelectedClassification] =
		useState<string>("ALL");
	const [sortKey, setSortKey] =
		useState<keyof RelationalColumnProfile>("table");
	const [sortAsc, setSortAsc] = useState<boolean>(true);

	const payload = currentEvent?.layer2_visual_payload;
	const summaryMetrics = payload?.summary_metrics ?? {};
	const pills = payload?.semantic_hash_pills ?? [];
	const verdict = currentEvent?.status ?? "PASS";
	const verdictMeta =
		VERDICT_VISUAL_GRAMMAR[verdict] ?? VERDICT_VISUAL_GRAMMAR.PASS;

	const filteredColumns = useMemo(() => {
		let list = [...ALL_44_RELATIONAL_COLUMNS];
		if (selectedClassification !== "ALL") {
			list = list.filter((c) => c.classification === selectedClassification);
		}
		if (searchTerm.trim()) {
			const q = searchTerm.toLowerCase();
			list = list.filter(
				(c) =>
					c.column.toLowerCase().includes(q) ||
					c.table.toLowerCase().includes(q) ||
					c.notes.toLowerCase().includes(q),
			);
		}
		list.sort((a, b) => {
			const valA = a[sortKey];
			const valB = b[sortKey];
			if (typeof valA === "number" && typeof valB === "number") {
				return sortAsc ? valA - valB : valB - valA;
			}
			return sortAsc
				? String(valA).localeCompare(String(valB))
				: String(valB).localeCompare(String(valA));
		});
		return list;
	}, [searchTerm, selectedClassification, sortKey, sortAsc]);

	const handleSort = (key: keyof RelationalColumnProfile) => {
		if (sortKey === key) {
			setSortAsc((prev) => !prev);
		} else {
			setSortKey(key);
			setSortAsc(true);
		}
	};

	return (
		<div
			data-testid="wb02-empirical-profiler"
			style={{
				display: "flex",
				flexDirection: "column",
				gap: "1rem",
				color: "var(--dv-text-primary)",
			}}
		>
			{/* Top Bar */}
			<section
				style={{
					backgroundColor: "var(--dv-surface-1)",
					border: "1px solid var(--dv-border-subtle)",
					borderRadius: "0.5rem",
					padding: "1rem",
					display: "flex",
					justifyContent: "space-between",
					alignItems: "center",
					flexWrap: "wrap",
					gap: "0.75rem",
				}}
			>
				<div>
					<div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
						<span
							style={{
								fontWeight: 700,
								fontSize: "0.8125rem",
								color: `var(${verdictMeta.cssColorVar})`,
							}}
						>
							{verdictMeta.shapeSymbol} {verdictMeta.prefixBadge}
						</span>
						<h2 style={{ margin: 0, fontSize: "1rem", fontWeight: 700 }}>
							WB-02: Empirical Column Profiler &amp; Candidate Discovery Matrix
						</h2>
					</div>
					<p
						style={{
							margin: "0.25rem 0 0",
							fontSize: "0.75rem",
							color: "var(--dv-text-secondary)",
						}}
					>
						VIZ-STEP-02 Empirical profiling of 44 relational columns across 7
						Bronze tables before YAML synthesis
					</p>
				</div>

				<button
					type="button"
					onClick={onInspectProof}
					data-testid="btn-inspect-proof"
					style={{
						backgroundColor: "var(--dv-surface-2)",
						color: "var(--dv-text-primary)",
						border: "1px solid var(--dv-border-strong)",
						borderRadius: "0.375rem",
						padding: "0.4rem 0.75rem",
						fontSize: "0.75rem",
						fontWeight: 600,
						cursor: "pointer",
					}}
				>
					Inspect Layer 3 Formal Proof ↗
				</button>
			</section>

			{/* Summary Metrics Cards */}
			<section
				data-testid="profiler-metrics"
				style={{
					display: "grid",
					gridTemplateColumns: "repeat(auto-fit, minmax(130px, 1fr))",
					gap: "0.5rem",
				}}
			>
				{[
					{
						label: "Relational Columns",
						value: summaryMetrics.relational_columns_profiled ?? 44,
					},
					{
						label: "Natural BK Candidates",
						value: summaryMetrics.business_key_candidates ?? 8,
					},
					{
						label: "Quarantined Surrogates",
						value: summaryMetrics.surrogates_quarantined ?? 1,
						color: "var(--dv-gate-reject)",
					},
					{
						label: "Optional FKs (Sparsity)",
						value: `${summaryMetrics.optional_fks_detected ?? 1} (28.6% non-null)`,
						color: "var(--dv-sat-accent)",
					},
					{
						label: "Ref Code Columns",
						value: summaryMetrics.ref_code_columns ?? 2,
					},
				].map((m) => (
					<div
						key={m.label}
						style={{
							backgroundColor: "var(--dv-surface-1)",
							border: "1px solid var(--dv-border-subtle)",
							borderRadius: "0.375rem",
							padding: "0.6rem 0.75rem",
						}}
					>
						<div
							style={{
								fontSize: "0.6875rem",
								color: "var(--dv-text-secondary)",
							}}
						>
							{m.label}
						</div>
						<div
							style={{
								fontSize: "1.125rem",
								fontWeight: 700,
								marginTop: "0.25rem",
								color: m.color ?? "var(--dv-text-primary)",
							}}
						>
							{String(m.value)}
						</div>
					</div>
				))}
			</section>

			{/* Semantic Hash Pills Bar */}
			{pills.length > 0 && (
				<section
					style={{
						backgroundColor: "var(--dv-surface-1)",
						border: "1px solid var(--dv-border-subtle)",
						borderRadius: "0.5rem",
						padding: "0.75rem 1rem",
					}}
				>
					<div
						style={{
							fontSize: "0.75rem",
							fontWeight: 600,
							color: "var(--dv-text-secondary)",
							marginBottom: "0.5rem",
						}}
					>
						Profiled Business Key Semantic Hash Pills:
					</div>
					<div style={{ display: "flex", flexWrap: "wrap", gap: "0.5rem" }}>
						{pills.map((pill) => (
							<button
								key={`${pill.full_sha256_hex}-${pill.business_key}`}
								type="button"
								onClick={onInspectProof}
								title={`Full SHA-256: ${pill.full_sha256_hex}`}
								data-testid="semantic-hash-pill"
								style={{
									backgroundColor: "var(--dv-surface-2)",
									color: "var(--dv-text-primary)",
									border: "1px solid var(--dv-hub-accent)",
									borderRadius: "9999px",
									padding: "0.2rem 0.6rem",
									fontFamily: "ui-monospace, monospace",
									fontSize: "0.75rem",
									cursor: "pointer",
								}}
							>
								{pill.pill_label}
							</button>
						))}
					</div>
				</section>
			)}

			{/* Scatter Plot & Inclusion DAG Split Layout */}
			<section
				style={{
					display: "grid",
					gridTemplateColumns: "1fr 1fr",
					gap: "1rem",
				}}
			>
				<FourQuadrantScatterPlot />
				<InclusionDependencyDAG />
			</section>

			{/* Sortable 44-Column Profile Grid */}
			<section
				data-testid="profile-grid-44-columns"
				style={{
					backgroundColor: "var(--dv-surface-1)",
					border: "1px solid var(--dv-border-subtle)",
					borderRadius: "0.5rem",
					padding: "1rem",
				}}
			>
				<div
					style={{
						display: "flex",
						justifyContent: "space-between",
						alignItems: "center",
						flexWrap: "wrap",
						gap: "0.75rem",
						marginBottom: "0.75rem",
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
							Sortable 44-Column Empirical Profile Matrix (
							{filteredColumns.length} / 44 columns shown)
						</h3>
						<span
							style={{ fontSize: "0.7rem", color: "var(--dv-text-secondary)" }}
						>
							Click headers to sort; filter by candidate classification or
							column name
						</span>
					</div>

					<div style={{ display: "flex", gap: "0.5rem", alignItems: "center" }}>
						<input
							type="text"
							placeholder="Search column, table..."
							value={searchTerm}
							onChange={(e) => setSearchTerm(e.target.value)}
							style={{
								backgroundColor: "var(--dv-surface-2)",
								color: "var(--dv-text-primary)",
								border: "1px solid var(--dv-border-strong)",
								borderRadius: "0.25rem",
								padding: "0.25rem 0.5rem",
								fontSize: "0.75rem",
							}}
						/>

						<select
							value={selectedClassification}
							onChange={(e) => setSelectedClassification(e.target.value)}
							style={{
								backgroundColor: "var(--dv-surface-2)",
								color: "var(--dv-text-primary)",
								border: "1px solid var(--dv-border-strong)",
								borderRadius: "0.25rem",
								padding: "0.25rem 0.5rem",
								fontSize: "0.75rem",
							}}
						>
							<option value="ALL">All Dispositions</option>
							<option value="BUSINESS_KEY">Business Keys (8)</option>
							<option value="SURROGATE_DROP">Surrogate Drop (1)</option>
							<option value="FOREIGN_KEY">Foreign Keys (4)</option>
							<option value="OPTIONAL_FK">Optional FK (1)</option>
							<option value="REF_CODE">Reference Codes (2)</option>
							<option value="PAYLOAD">Descriptive Payload (28)</option>
						</select>
					</div>
				</div>

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
								<th style={{ padding: "0.4rem 0.6rem" }}>
									<button
										type="button"
										onClick={() => handleSort("table")}
										style={{
											background: "none",
											border: "none",
											padding: 0,
											font: "inherit",
											fontWeight: 700,
											color: "inherit",
											cursor: "pointer",
										}}
									>
										Source Table{" "}
										{sortKey === "table" ? (sortAsc ? "▲" : "▼") : ""}
									</button>
								</th>
								<th style={{ padding: "0.4rem 0.6rem" }}>
									<button
										type="button"
										onClick={() => handleSort("column")}
										style={{
											background: "none",
											border: "none",
											padding: 0,
											font: "inherit",
											fontWeight: 700,
											color: "inherit",
											cursor: "pointer",
										}}
									>
										Column Name{" "}
										{sortKey === "column" ? (sortAsc ? "▲" : "▼") : ""}
									</button>
								</th>
								<th style={{ padding: "0.4rem 0.6rem" }}>Data Type</th>
								<th style={{ padding: "0.4rem 0.6rem" }}>
									<button
										type="button"
										onClick={() => handleSort("uniqueness")}
										style={{
											background: "none",
											border: "none",
											padding: 0,
											font: "inherit",
											fontWeight: 700,
											color: "inherit",
											cursor: "pointer",
										}}
									>
										U(C) {sortKey === "uniqueness" ? (sortAsc ? "▲" : "▼") : ""}
									</button>
								</th>
								<th style={{ padding: "0.4rem 0.6rem" }}>
									<button
										type="button"
										onClick={() => handleSort("nonNullRatio")}
										style={{
											background: "none",
											border: "none",
											padding: 0,
											font: "inherit",
											fontWeight: 700,
											color: "inherit",
											cursor: "pointer",
										}}
									>
										U_nn{" "}
										{sortKey === "nonNullRatio" ? (sortAsc ? "▲" : "▼") : ""}
									</button>
								</th>
								<th style={{ padding: "0.4rem 0.6rem" }}>
									<button
										type="button"
										onClick={() => handleSort("entropy")}
										style={{
											background: "none",
											border: "none",
											padding: 0,
											font: "inherit",
											fontWeight: 700,
											color: "inherit",
											cursor: "pointer",
										}}
									>
										H(C) {sortKey === "entropy" ? (sortAsc ? "▲" : "▼") : ""}
									</button>
								</th>
								<th style={{ padding: "0.4rem 0.6rem" }}>Disposition</th>
								<th style={{ padding: "0.4rem 0.6rem" }}>Rationale</th>
							</tr>
						</thead>
						<tbody>
							{filteredColumns.map((col) => {
								const isSurrogate = col.classification === "SURROGATE_DROP";
								const isKey = col.classification === "BUSINESS_KEY";
								const isOptFk = col.classification === "OPTIONAL_FK";

								let badgeColor = "var(--dv-text-secondary)";
								if (isSurrogate) badgeColor = "var(--dv-gate-reject)";
								else if (isKey) badgeColor = "var(--dv-hub-accent)";
								else if (isOptFk) badgeColor = "var(--dv-sat-accent)";
								else if (col.classification === "FOREIGN_KEY")
									badgeColor = "var(--dv-lnk-accent)";
								else if (col.classification === "REF_CODE")
									badgeColor = "var(--dv-ref-accent)";

								return (
									<tr
										key={`${col.table}.${col.column}`}
										data-testid={`profile-row-${col.column}`}
										style={{
											borderBottom: "1px solid var(--dv-border-subtle)",
											backgroundColor: isSurrogate
												? "rgba(239, 68, 68, 0.08)"
												: undefined,
										}}
									>
										<td
											style={{
												padding: "0.4rem 0.6rem",
												color: "var(--dv-text-secondary)",
											}}
										>
											{col.table.split(".")[1]}
										</td>
										<td
											style={{
												padding: "0.4rem 0.6rem",
												fontWeight: isSurrogate || isKey ? 700 : 500,
												color: isSurrogate
													? "var(--dv-gate-reject)"
													: "var(--dv-text-primary)",
											}}
										>
											{col.column}
										</td>
										<td
											style={{
												padding: "0.4rem 0.6rem",
												color: "var(--dv-text-muted)",
											}}
										>
											{col.dataType}
										</td>
										<td style={{ padding: "0.4rem 0.6rem" }}>
											{col.uniqueness.toFixed(2)}
										</td>
										<td
											style={{
												padding: "0.4rem 0.6rem",
												color:
													col.nonNullRatio < 0.5
														? "var(--dv-sat-accent)"
														: "inherit",
												fontWeight: col.nonNullRatio < 0.5 ? 700 : 400,
											}}
										>
											{col.nonNullRatio.toFixed(2)}
										</td>
										<td style={{ padding: "0.4rem 0.6rem" }}>
											{col.entropy.toFixed(2)}
										</td>
										<td style={{ padding: "0.4rem 0.6rem" }}>
											<span
												style={{
													color: badgeColor,
													border: `1px solid ${badgeColor}`,
													borderRadius: "0.2rem",
													padding: "0.1rem 0.35rem",
													fontSize: "0.6875rem",
													fontWeight: 600,
												}}
											>
												[{col.classification}]
											</span>
										</td>
										<td
											style={{
												padding: "0.4rem 0.6rem",
												fontSize: "0.7rem",
												color: "var(--dv-text-secondary)",
												fontFamily: "ui-sans-serif, sans-serif",
											}}
										>
											{col.notes}
										</td>
									</tr>
								);
							})}
						</tbody>
					</table>
				</div>
			</section>
		</div>
	);
}
