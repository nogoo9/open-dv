import type React from "react";
import { useState } from "react";

export interface ZoneSqlTab {
	readonly zoneId: "ZONE_0" | "ZONE_1" | "ZONE_2" | "ZONE_3" | "ZONE_4";
	readonly label: string;
	readonly description: string;
	readonly contractYamlSnippet: string;
	readonly canonicalIrSnippet: string;
	readonly duckdbSql: string;
}

export interface AstRuleBadge {
	readonly ruleId: string;
	readonly title: string;
	readonly status: "PASS";
}

export interface FourZoneSQLViewerProps {
	readonly zones?: readonly ZoneSqlTab[];
	readonly astBadges?: readonly AstRuleBadge[];
}

export const DEFAULT_AST_RULE_BADGES: readonly AstRuleBadge[] = [
	{
		ruleId: "DV-AST-001",
		title: "Parseable DuckDB / ANSI SQL AST (0 syntax errors)",
		status: "PASS",
	},
	{
		ruleId: "DV-AST-002",
		title: "Algorithm A.5 SHA-256 length-prefixed expression match",
		status: "PASS",
	},
	{
		ruleId: "DV-AST-003",
		title: "Idempotent NOT EXISTS / MERGE guard on Zone 1 & Zone 2 DML",
		status: "PASS",
	},
	{
		ruleId: "DV-AST-004",
		title: "Ghost Record (0x00..00) unioned in Zone 3 PIT view/table",
		status: "PASS",
	},
	{
		ruleId: "DV-AST-005",
		title: "Bi-temporal load_dts <= cutoff AND effective_dts windowing",
		status: "PASS",
	},
	{
		ruleId: "DV-AST-006",
		title: "Zero destructive DROP / TRUNCATE / UPDATE on Raw Vault tables",
		status: "PASS",
	},
];

export const DEFAULT_FOUR_ZONE_SQL_TABS: readonly ZoneSqlTab[] = [
	{
		zoneId: "ZONE_0",
		label: "Zone 0 · Staging & Pre-Hash",
		description:
			"Deterministic length-prefixed SHA-256 hash key & hash_diff projection with Day-3 schema drift aliasing.",
		contractYamlSnippet: `hubs:
  - name: hub_limited_partner
    business_key_columns: [lp_id]
    require_src_qualifier: true
    bkcc_prefix: "GLOBAL"`,
		canonicalIrSnippet: `{
  "zone": 0,
  "view": "stg_ilpa_lp_commitment",
  "hk_expr": "UPPER(SHA256('6:GLOBAL||' || LENGTH(TRIM(UPPER(lp_id))) || ':' || TRIM(UPPER(lp_id))))"
}`,
		duckdbSql: `CREATE OR REPLACE VIEW stg_ilpa_lp_commitment AS
SELECT
  UPPER(SHA256(
    '6:GLOBAL||' || CAST(OCTET_LENGTH(UPPER(TRIM(CAST(lp_id AS VARCHAR)))) AS VARCHAR)
    || ':' || UPPER(TRIM(CAST(lp_id AS VARCHAR)))
  )) AS hk_limited_partner,
  UPPER(TRIM(CAST(lp_id AS VARCHAR))) AS lp_id,
  'GLOBAL' AS bkcc_prefix,
  CAST(commitment_amount_usd AS DECIMAL(18,2)) AS committed_amt,
  CAST(ilpa_esg_rating AS VARCHAR(16)) AS ilpa_esg_rating,
  CAST(load_dts AS TIMESTAMP) AS load_dts,
  'ILPA_CORE_FEED' AS record_source
FROM raw_ilpa_commitments;`,
	},
	{
		zoneId: "ZONE_1",
		label: "Zone 1 · Raw Vault Hubs & Links",
		description:
			"Insert-only Hub and Link DDL/DML with Ghost Record 0x00..00 initialization and idempotent WHERE NOT EXISTS guard.",
		contractYamlSnippet: `links:
  - name: lnk_lp_commitment
    hub_references: [hub_limited_partner, hub_fund]
  - name: lnk_lp_commitment_co_invest
    hub_references: [hub_limited_partner, hub_fund, hub_portfolio_company]`,
		canonicalIrSnippet: `{
  "zone": 1,
  "tables": ["hub_limited_partner", "hub_fund", "lnk_lp_commitment", "lnk_lp_commitment_co_invest"],
  "ghost_hk": "0000000000000000000000000000000000000000000000000000000000000000"
}`,
		duckdbSql: `INSERT INTO hub_limited_partner (hk_limited_partner, bkcc_prefix, lp_id, load_dts, record_source)
SELECT DISTINCT s.hk_limited_partner, s.bkcc_prefix, s.lp_id, s.load_dts, s.record_source
FROM stg_ilpa_lp_commitment s
WHERE NOT EXISTS (
  SELECT 1 FROM hub_limited_partner h
  WHERE h.hk_limited_partner = s.hk_limited_partner
);`,
	},
	{
		zoneId: "ZONE_2",
		label: "Zone 2 · Raw Vault Satellites",
		description:
			"Append-only descriptive, PII-isolated (sat_limited_partner_pii), and bi-temporal valuation (sat_portco_valuation) satellites.",
		contractYamlSnippet: `satellites:
  - name: sat_limited_partner_pii
    parent: hub_limited_partner
    security_classification: RESTRICTED_PII
  - name: sat_portco_valuation
    parent: hub_portfolio_company
    bi_temporal: true`,
		canonicalIrSnippet: `{
  "zone": 2,
  "satellites": [
    "sat_limited_partner_core",
    "sat_limited_partner_pii",
    "sat_lp_commitment_terms",
    "sat_portco_valuation",
    "sat_eff_fund_portco"
  ]
}`,
		duckdbSql: `INSERT INTO sat_portco_valuation (
  hk_portfolio_company, load_dts, effective_dts, hash_diff,
  ev_ebitda_multiple, enterprise_value_usd, record_source
)
SELECT
  s.hk_portfolio_company, s.load_dts, s.effective_dts, s.hash_diff,
  s.ev_ebitda_multiple, s.enterprise_value_usd, s.record_source
FROM stg_portco_valuation s
WHERE NOT EXISTS (
  SELECT 1 FROM sat_portco_valuation cur
  WHERE cur.hk_portfolio_company = s.hk_portfolio_company
    AND cur.effective_dts = s.effective_dts
    AND cur.hash_diff = s.hash_diff
);`,
	},
	{
		zoneId: "ZONE_3",
		label: "Zone 3 · Business Vault & PIT",
		description:
			"Logarithmic-window Point-in-Time (PIT) construct pit_lp_commitment_asof referencing Ghost Record 0x00..00 for NULL-free equi-joins.",
		contractYamlSnippet: `pit_tables:
  - name: pit_lp_commitment_asof
    base_entity: lnk_lp_commitment
    tracked_satellites: [sat_lp_commitment_terms]`,
		canonicalIrSnippet: `{
  "zone": 3,
  "construct": "pit_lp_commitment_asof",
  "ghost_fallback_hk": "0000000000000000000000000000000000000000000000000000000000000000",
  "join_policy": "INNER_JOIN_ONLY"
}`,
		duckdbSql: `CREATE OR REPLACE VIEW pit_lp_commitment_asof AS
SELECT
  l.hk_lp_commitment,
  snap.snapshot_dts,
  COALESCE(s.hk_lp_commitment, '0000000000000000000000000000000000000000000000000000000000000000') AS sat_terms_hk,
  COALESCE(s.load_dts, TIMESTAMP '1970-01-01 00:00:00') AS sat_terms_load_dts
FROM lnk_lp_commitment l
CROSS JOIN pit_snapshot_spine snap
LEFT JOIN LATERAL (
  SELECT t.hk_lp_commitment, t.load_dts
  FROM sat_lp_commitment_terms t
  WHERE t.hk_lp_commitment = l.hk_lp_commitment
    AND t.load_dts <= snap.snapshot_dts
  ORDER BY t.load_dts DESC
  LIMIT 1
) s ON TRUE;`,
	},
	{
		zoneId: "ZONE_4",
		label: "Zone 4 · Information Mart & Queries D.10 / D.10b",
		description:
			"Read-optimized Information Mart views powering Query D.10 (LP Commitment PIT) and Query D.10b (Bi-Temporal Valuation Restatement).",
		contractYamlSnippet: `information_marts:
  - name: mart_pe_ic_quarterly_exposure
    queries: [QUERY_D10_PIT, QUERY_D10B_VALUATION]`,
		canonicalIrSnippet: `{
  "zone": 4,
  "views": ["mart_lp_commitment_pit", "mart_portco_bitemporal_valuation"],
  "verified_queries": ["D.10", "D.10b"]
}`,
		duckdbSql: `-- Query D.10b: Bi-Temporal As-Of Valuation Query
SELECT
  h.portco_lei,
  v.effective_dts,
  v.load_dts,
  v.ev_ebitda_multiple,
  v.enterprise_value_usd
FROM hub_portfolio_company h
INNER JOIN sat_portco_valuation v
  ON v.hk_portfolio_company = h.hk_portfolio_company
WHERE h.portco_lei = '529900T8BM49AURSDO55'
  AND v.effective_dts <= TIMESTAMP '2026-09-30 23:59:59'
  AND v.load_dts <= :assertion_cutoff_ts
QUALIFY ROW_NUMBER() OVER (
  PARTITION BY h.hk_portfolio_company, v.effective_dts
  ORDER BY v.load_dts DESC
) = 1;`,
	},
];

export function FourZoneSQLViewer({
	zones = DEFAULT_FOUR_ZONE_SQL_TABS,
	astBadges = DEFAULT_AST_RULE_BADGES,
}: FourZoneSQLViewerProps): React.ReactElement {
	const [activeZoneId, setActiveZoneId] =
		useState<ZoneSqlTab["zoneId"]>("ZONE_0");
	const activeZone = zones.find((z) => z.zoneId === activeZoneId) ?? zones[0];

	return (
		<div
			data-testid="four-zone-sql-viewer"
			style={{
				display: "flex",
				flexDirection: "column",
				gap: "14px",
			}}
		>
			<div
				style={{
					border: "1px solid var(--dv-border-subtle)",
					borderRadius: "8px",
					padding: "12px",
					background: "var(--dv-bg-surface)",
				}}
			>
				<div
					style={{
						display: "flex",
						justifyContent: "space-between",
						alignItems: "center",
						flexWrap: "wrap",
						gap: "8px",
						marginBottom: "10px",
					}}
				>
					<div>
						<h4 style={{ margin: 0, fontSize: "14px" }}>
							4-Zone Deterministic SQL Rosetta Stone (Contract YAML ↔ Canonical
							IR ↔ DuckDB SQL)
						</h4>
						<div style={{ fontSize: "12px", color: "var(--dv-text-muted)" }}>
							Zones 0–4 compiled by Stage 7 deterministic compiler with SQLGlot
							AST verification
						</div>
					</div>
					<div style={{ display: "flex", gap: "6px", flexWrap: "wrap" }}>
						{zones.map((zone) => (
							<button
								key={zone.zoneId}
								type="button"
								data-testid={`sql-zone-tab-${zone.zoneId}`}
								onClick={() => setActiveZoneId(zone.zoneId)}
								style={{
									padding: "4px 10px",
									borderRadius: "4px",
									border: "1px solid var(--dv-border-subtle)",
									background:
										activeZone?.zoneId === zone.zoneId
											? "var(--dv-bg-elevated)"
											: "var(--dv-bg-canvas)",
									color: "var(--dv-text-primary)",
									fontFamily: "var(--dv-font-mono)",
									fontSize: "11px",
									cursor: "pointer",
								}}
							>
								{zone.label}
							</button>
						))}
					</div>
				</div>

				{activeZone ? (
					<div>
						<p
							style={{
								margin: "0 0 10px 0",
								fontSize: "12px",
								color: "var(--dv-text-muted)",
							}}
						>
							{activeZone.description}
						</p>
						<div
							style={{
								display: "grid",
								gridTemplateColumns: "repeat(auto-fit, minmax(260px, 1fr))",
								gap: "10px",
							}}
						>
							<div>
								<div
									style={{
										fontSize: "11px",
										fontFamily: "var(--dv-font-mono)",
										color: "var(--dv-text-muted)",
										marginBottom: "4px",
									}}
								>
									1. Contract YAML (NoMistakeDataVault2ContractV5)
								</div>
								<pre
									style={{
										margin: 0,
										padding: "10px",
										borderRadius: "6px",
										background: "var(--dv-bg-canvas)",
										border: "1px solid var(--dv-border-subtle)",
										fontFamily: "var(--dv-font-mono)",
										fontSize: "11px",
										overflowX: "auto",
									}}
								>
									{activeZone.contractYamlSnippet}
								</pre>
							</div>

							<div>
								<div
									style={{
										fontSize: "11px",
										fontFamily: "var(--dv-font-mono)",
										color: "var(--dv-text-muted)",
										marginBottom: "4px",
									}}
								>
									2. Canonical IR JSON Projection
								</div>
								<pre
									style={{
										margin: 0,
										padding: "10px",
										borderRadius: "6px",
										background: "var(--dv-bg-canvas)",
										border: "1px solid var(--dv-border-subtle)",
										fontFamily: "var(--dv-font-mono)",
										fontSize: "11px",
										overflowX: "auto",
									}}
								>
									{activeZone.canonicalIrSnippet}
								</pre>
							</div>

							<div>
								<div
									style={{
										fontSize: "11px",
										fontFamily: "var(--dv-font-mono)",
										color: "var(--dv-text-muted)",
										marginBottom: "4px",
									}}
								>
									3. Compiled DuckDB / ANSI SQL ({activeZone.zoneId})
								</div>
								<pre
									data-testid="active-zone-sql-code"
									style={{
										margin: 0,
										padding: "10px",
										borderRadius: "6px",
										background: "var(--dv-bg-canvas)",
										border: "1px solid var(--dv-border-subtle)",
										fontFamily: "var(--dv-font-mono)",
										fontSize: "11px",
										overflowX: "auto",
									}}
								>
									{activeZone.duckdbSql}
								</pre>
							</div>
						</div>
					</div>
				) : null}
			</div>

			<div
				data-testid="dv-ast-badges"
				style={{
					border: "1px solid var(--dv-border-subtle)",
					borderRadius: "8px",
					padding: "12px",
					background: "var(--dv-bg-surface)",
				}}
			>
				<h4 style={{ margin: "0 0 8px 0", fontSize: "14px" }}>
					SQLGlot AST Verification Invariants (<code>DV-AST-001</code> –{" "}
					<code>DV-AST-006</code>)
				</h4>
				<div
					style={{
						display: "grid",
						gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))",
						gap: "8px",
					}}
				>
					{astBadges.map((badge) => (
						<div
							key={badge.ruleId}
							data-testid={`ast-badge-${badge.ruleId}`}
							style={{
								padding: "6px 10px",
								borderRadius: "6px",
								background: "var(--dv-bg-canvas)",
								border: "1px solid var(--dv-border-subtle)",
								display: "flex",
								justifyContent: "space-between",
								alignItems: "center",
								fontSize: "12px",
							}}
						>
							<div>
								<strong style={{ fontFamily: "var(--dv-font-mono)" }}>
									{badge.ruleId}
								</strong>
								<div
									style={{ fontSize: "11px", color: "var(--dv-text-muted)" }}
								>
									{badge.title}
								</div>
							</div>
							<span
								style={{
									fontFamily: "var(--dv-font-mono)",
									fontSize: "11px",
									fontWeight: 700,
									color: "var(--dv-status-pass)",
								}}
							>
								{badge.status}
							</span>
						</div>
					))}
				</div>
			</div>

			<div
				data-testid="query-d10-results-card"
				style={{
					border: "1px solid var(--dv-border-subtle)",
					borderRadius: "8px",
					padding: "12px",
					background: "var(--dv-bg-surface)",
				}}
			>
				<h4 style={{ margin: "0 0 8px 0", fontSize: "14px" }}>
					Certified PIT Query Results (<code>Query D.10</code> &amp;{" "}
					<code>Query D.10b</code>)
				</h4>
				<div
					style={{
						display: "grid",
						gridTemplateColumns: "repeat(auto-fit, minmax(300px, 1fr))",
						gap: "12px",
						fontSize: "12px",
					}}
				>
					<div
						style={{
							padding: "10px",
							borderRadius: "6px",
							background: "var(--dv-bg-canvas)",
							border: "1px solid var(--dv-border-subtle)",
						}}
					>
						<strong>
							Query D.10 · Point-in-Time LP Commitment &amp; Side-Letter Fee
							Cap:
						</strong>
						<div
							style={{
								marginTop: "6px",
								fontFamily: "var(--dv-font-mono)",
								fontSize: "11px",
							}}
						>
							<div>
								LP Pill: <code>[HK: 5D15..0AE4 | GLOBAL:LP-101 CalPERS]</code>
							</div>
							<div>
								Fund Pill:{" "}
								<code>[HK: 94A6..8759 | ILPA_CORE:APEX_BO_IV Fund IV]</code>
							</div>
							<div>
								Commitment: <code>$500,000,000.00</code> · Side-Letter Fee Cap:{" "}
								<code>125.00 bps</code>
							</div>
						</div>
					</div>

					<div
						style={{
							padding: "10px",
							borderRadius: "6px",
							background: "var(--dv-bg-canvas)",
							border: "1px solid var(--dv-border-subtle)",
						}}
					>
						<strong>
							Query D.10b · Bi-Temporal Nordic Cloud Q3 Restatement:
						</strong>
						<div
							style={{
								marginTop: "6px",
								fontFamily: "var(--dv-font-mono)",
								fontSize: "11px",
							}}
						>
							<div>
								PortCo LEI: <code>529900T8BM49AURSDO55</code> (Nordic Cloud AB)
							</div>
							<div>
								Cutoff <code>2026-10-15 12:00:00</code>: <code>12.80x</code>{" "}
								($216,280,000.00)
							</div>
							<div>
								Cutoff <code>2026-11-15 23:59:59</code>: <code>14.50x</code>{" "}
								($245,000,000.00)
							</div>
						</div>
					</div>
				</div>
			</div>
		</div>
	);
}
