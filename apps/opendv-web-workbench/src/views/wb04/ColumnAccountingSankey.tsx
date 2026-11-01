/**
 * Column Accounting Sankey & Surrogate Sink (`ColumnAccountingSankey.tsx`)
 * Visualizes 100% column triage routing from Bronze tables across ELM dispositions into DV entities (VIZ-STEP-05) (`docs/06` §3.6).
 */

export interface SourceTableCard {
	name: string;
	columnCount: number;
}

export interface ElmDispositionBucket {
	id: string;
	label: string;
	count: number;
	color: string;
	targetEntityCategory: string;
}

export interface TargetEntityBucket {
	name: string;
	entityCount: number;
	badge: string;
	color: string;
}

export const BRONZE_SOURCE_TABLES: readonly SourceTableCard[] = [
	{ name: "investran.src_pe_funds", columnCount: 9 },
	{ name: "investran.src_lp_investors", columnCount: 8 },
	{ name: "investran.src_lp_commitments_and_calls", columnCount: 10 },
	{ name: "dealcloud.src_portfolio_companies", columnCount: 7 },
	{ name: "legacy_gp.src_legacy_lp_roster", columnCount: 4 },
	{ name: "dms.documents", columnCount: 4 },
	{ name: "mdm.src_lp_steward_crosswalk", columnCount: 2 },
];

export const ELM_DISPOSITION_BUCKETS: readonly ElmDispositionBucket[] = [
	{
		id: "BUSINESS_KEY",
		label: "BUSINESS_KEY",
		count: 5,
		color: "var(--dv-hub-accent)",
		targetEntityCategory: "Hubs",
	},
	{
		id: "FOREIGN_KEY_REF",
		label: "FOREIGN_KEY_REF",
		count: 8,
		color: "var(--dv-lnk-accent)",
		targetEntityCategory: "Links",
	},
	{
		id: "OPTIONAL_FOREIGN_KEY_REF",
		label: "OPTIONAL_FK",
		count: 1,
		color: "var(--dv-sat-accent)",
		targetEntityCategory: "Split Links",
	},
	{
		id: "DEGENERATE_KEY",
		label: "DEGENERATE_KEY",
		count: 1,
		color: "var(--dv-lnk-accent)",
		targetEntityCategory: "Links",
	},
	{
		id: "DESCRIPTIVE_PAYLOAD",
		label: "DESCRIPTIVE_PAYLOAD",
		count: 25,
		color: "var(--dv-sat-accent)",
		targetEntityCategory: "Satellites",
	},
	{
		id: "REFERENCE_CODE",
		label: "REFERENCE_CODE",
		count: 2,
		color: "var(--dv-ref-accent)",
		targetEntityCategory: "Ref Tables",
	},
	{
		id: "TECHNICAL_METADATA",
		label: "TECHNICAL_METADATA",
		count: 1,
		color: "var(--dv-text-muted)",
		targetEntityCategory: "Satellites",
	},
	{
		id: "SYSTEM_SURROGATE_DROPPED",
		label: "SURROGATE_DROPPED",
		count: 1,
		color: "var(--dv-surrogate-accent)",
		targetEntityCategory: "Quarantine Sink",
	},
];

export const TARGET_ENTITY_BUCKETS: readonly TargetEntityBucket[] = [
	{
		name: "4 Enterprise Hubs",
		entityCount: 4,
		badge: "[HUB]",
		color: "var(--dv-hub-accent)",
	},
	{
		name: "5 Relationships & SAL Links",
		entityCount: 5,
		badge: "[LNK]",
		color: "var(--dv-lnk-accent)",
	},
	{
		name: "11 Context & Provenance Sats",
		entityCount: 11,
		badge: "[SAT]",
		color: "var(--dv-sat-accent)",
	},
	{
		name: "2 Reference Code Tables",
		entityCount: 2,
		badge: "[REF]",
		color: "var(--dv-ref-accent)",
	},
	{
		name: "Dropped Surrogate Sink",
		entityCount: 1,
		badge: "[DROPPED-SURROGATE]",
		color: "var(--dv-surrogate-accent)",
	},
];

export function ColumnAccountingSankey() {
	const totalBronzeColumns = BRONZE_SOURCE_TABLES.reduce(
		(sum, t) => sum + t.columnCount,
		0,
	);
	const totalTriaged = ELM_DISPOSITION_BUCKETS.reduce(
		(sum, b) => sum + b.count,
		0,
	);

	return (
		<div
			aria-label="100% Column Accounting Sankey & Surrogate Sink"
			data-testid="column-accounting-sankey"
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
			{/* Top Header & Conservation of Mass Pill */}
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
					<div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
						<span
							style={{
								color: "var(--dv-gate-pass)",
								fontWeight: 700,
								fontSize: "0.75rem",
							}}
						>
							● [PASS]
						</span>
						<strong
							style={{ fontSize: "0.875rem", color: "var(--dv-text-primary)" }}
						>
							Pass 1 Column Triage Flow &amp; Conservation of Mass
						</strong>
					</div>
					<div
						style={{ fontSize: "0.7rem", color: "var(--dv-text-secondary)" }}
					>
						Every single Bronze column is accounted for: 0 silent drops, 1
						surrogate tombstoned
					</div>
				</div>

				<div
					style={{
						backgroundColor: "var(--dv-surface-2)",
						border: "1px solid var(--dv-gate-pass)",
						borderRadius: "0.25rem",
						padding: "0.25rem 0.6rem",
						fontSize: "0.75rem",
						fontFamily: "ui-monospace, monospace",
						color: "var(--dv-gate-pass)",
						fontWeight: 700,
					}}
				>
					Accountability: {totalTriaged} / {totalBronzeColumns} Columns (100.0%)
				</div>
			</div>

			{/* 3-Column Sankey Flow Canvas */}
			<div
				style={{
					display: "grid",
					gridTemplateColumns: "1.2fr 1fr 1.2fr",
					gap: "1rem",
				}}
			>
				{/* Left: Bronze Source Tables */}
				<div
					style={{ display: "flex", flexDirection: "column", gap: "0.4rem" }}
				>
					<div
						style={{
							fontSize: "0.75rem",
							fontWeight: 700,
							color: "var(--dv-text-secondary)",
							textTransform: "uppercase",
						}}
					>
						Bronze Tables ({totalBronzeColumns} cols)
					</div>

					{BRONZE_SOURCE_TABLES.map((tbl) => (
						<div
							key={tbl.name}
							style={{
								backgroundColor: "var(--dv-surface-2)",
								border: "1px solid var(--dv-border-subtle)",
								borderRadius: "0.25rem",
								padding: "0.35rem 0.6rem",
								fontSize: "0.75rem",
								display: "flex",
								justifyContent: "space-between",
								alignItems: "center",
								fontFamily: "ui-monospace, monospace",
							}}
						>
							<span style={{ color: "var(--dv-text-primary)" }}>
								{tbl.name}
							</span>
							<span
								style={{
									backgroundColor: "var(--dv-surface-3)",
									padding: "0.1rem 0.35rem",
									borderRadius: "0.2rem",
									fontWeight: 700,
									fontSize: "0.6875rem",
								}}
							>
								{tbl.columnCount}
							</span>
						</div>
					))}
				</div>

				{/* Center: ELM Dispositions */}
				<div
					style={{ display: "flex", flexDirection: "column", gap: "0.4rem" }}
				>
					<div
						style={{
							fontSize: "0.75rem",
							fontWeight: 700,
							color: "var(--dv-text-secondary)",
							textTransform: "uppercase",
						}}
					>
						ELM Dispositions ({totalTriaged})
					</div>

					{ELM_DISPOSITION_BUCKETS.map((bucket) => (
						<div
							key={bucket.id}
							data-testid={`triage-bucket-${bucket.id}`}
							style={{
								backgroundColor: "var(--dv-surface-2)",
								border: `1.5px solid ${bucket.color}`,
								borderRadius: "0.25rem",
								padding: "0.35rem 0.6rem",
								fontSize: "0.7rem",
								display: "flex",
								justifyContent: "space-between",
								alignItems: "center",
								fontFamily: "ui-monospace, monospace",
							}}
						>
							<span
								style={{ color: "var(--dv-text-primary)", fontWeight: 600 }}
							>
								{bucket.label}
							</span>
							<span
								style={{
									color: bucket.color,
									fontWeight: 700,
									fontSize: "0.75rem",
								}}
							>
								{bucket.count}
							</span>
						</div>
					))}
				</div>

				{/* Right: Target Data Vault Entities & Surrogate Sink */}
				<div
					style={{ display: "flex", flexDirection: "column", gap: "0.4rem" }}
				>
					<div
						style={{
							fontSize: "0.75rem",
							fontWeight: 700,
							color: "var(--dv-text-secondary)",
							textTransform: "uppercase",
						}}
					>
						Target DV Entities &amp; Sinks
					</div>

					{TARGET_ENTITY_BUCKETS.map((target) => (
						<div
							key={target.name}
							style={{
								backgroundColor: "var(--dv-surface-2)",
								border: `1px solid ${target.color}`,
								borderRadius: "0.25rem",
								padding: "0.45rem 0.6rem",
								fontSize: "0.75rem",
							}}
						>
							<div
								style={{
									display: "flex",
									justifyContent: "space-between",
									alignItems: "center",
									marginBottom: "0.2rem",
								}}
							>
								<strong
									style={{
										fontSize: "0.75rem",
										color: "var(--dv-text-primary)",
									}}
								>
									{target.name}
								</strong>
								<span
									style={{
										fontSize: "0.65rem",
										color: target.color,
										fontFamily: "ui-monospace, monospace",
										fontWeight: 700,
									}}
								>
									{target.badge}
								</span>
							</div>
						</div>
					))}
				</div>
			</div>

			{/* Prominent Dropped Surrogate Sink Badge */}
			<div
				data-testid="surrogate-sink-badge"
				style={{
					backgroundColor: "var(--dv-surface-2)",
					border: "1.5px dashed var(--dv-surrogate-accent)",
					borderRadius: "0.375rem",
					padding: "0.75rem 1rem",
					display: "flex",
					alignItems: "center",
					gap: "1rem",
				}}
			>
				<div
					style={{
						fontSize: "1.5rem",
						lineHeight: 1,
						color: "var(--dv-surrogate-accent)",
					}}
				>
					⚰️
				</div>
				<div>
					<div
						style={{
							fontSize: "0.75rem",
							fontWeight: 700,
							color: "var(--dv-surrogate-accent)",
							marginBottom: "0.2rem",
						}}
					>
						[DROPPED-SURROGATE] SINK: Tombstoned
						investran.src_lp_investors.row_seq_id
					</div>
					<div
						style={{ fontSize: "0.7rem", color: "var(--dv-text-secondary)" }}
					>
						Algorithm A.2 identified monotonic integer sequence (Δ = +1) in
						Quadrant 2. Explicitly quarantined from hub_limited_partner into the
						DROPPED sink. Zero surrogate pollution in enterprise hub business
						keys!
					</div>
				</div>
			</div>
		</div>
	);
}
