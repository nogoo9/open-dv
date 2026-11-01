/**
 * Inclusion Dependency DAG (`InclusionDependencyDAG.tsx`)
 * Visualizes cross-table empirical inclusion dependencies I(A.x ⊆ B.y) >= 0.90 (VIZ-STEP-02) (`docs/06` §3.3).
 */

export interface InclusionEdge {
	sourceTable: string;
	sourceColumn: string;
	targetTable: string;
	targetColumn: string;
	inclusionRatio: number; // 0.0 to 1.0
	nullRatio: number; // 0.0 to 1.0
	verdict: "MANDATORY_HUB_FK" | "ALG_A4_SPLIT_LINK" | "SAL_CROSSWALK";
	rationale: string;
}

export const INCLUSION_DEPENDENCIES: readonly InclusionEdge[] = [
	{
		sourceTable: "investran.src_lp_commitments_and_calls",
		sourceColumn: "fund_code",
		targetTable: "investran.src_pe_funds",
		targetColumn: "fund_code",
		inclusionRatio: 1.0,
		nullRatio: 0.0,
		verdict: "MANDATORY_HUB_FK",
		rationale:
			"100% inclusion, 0% NULLs → Mandatory driving FK in lnk_lp_commitment.",
	},
	{
		sourceTable: "investran.src_lp_commitments_and_calls",
		sourceColumn: "lp_id",
		targetTable: "investran.src_lp_investors",
		targetColumn: "lp_id",
		inclusionRatio: 1.0,
		nullRatio: 0.0,
		verdict: "MANDATORY_HUB_FK",
		rationale:
			"100% inclusion, 0% NULLs → Mandatory driving FK in lnk_lp_commitment.",
	},
	{
		sourceTable: "investran.src_lp_commitments_and_calls",
		sourceColumn: "co_invest_portco_lei",
		targetTable: "dealcloud.src_portfolio_companies",
		targetColumn: "lei",
		inclusionRatio: 1.0,
		nullRatio: 0.7143,
		verdict: "ALG_A4_SPLIT_LINK",
		rationale:
			"100% non-null inclusion, but 71.4% NULLs → High sparsity triggers Algorithm A.4 split into lnk_lp_commitment_co_invest.",
	},
	{
		sourceTable: "legacy_gp.src_legacy_lp_roster",
		sourceColumn: "lp_num",
		targetTable: "mdm.src_lp_steward_crosswalk",
		targetColumn: "legacy_lp_num",
		inclusionRatio: 1.0,
		nullRatio: 0.0,
		verdict: "SAL_CROSSWALK",
		rationale:
			"Crosswalk relationship linking Tier-3 legacy key to master LP via sal_limited_partner.",
	},
];

export function InclusionDependencyDAG() {
	return (
		<div
			aria-label="Inclusion Dependency DAG"
			data-testid="inclusion-dependency-dag"
			style={{
				display: "flex",
				flexDirection: "column",
				gap: "0.75rem",
			}}
		>
			<div>
				<strong
					style={{ fontSize: "0.8125rem", color: "var(--dv-text-primary)" }}
				>
					{"Inclusion Dependency DAG: I(A.x ⊆ B.y) ≥ 0.90"}
				</strong>
				<div style={{ fontSize: "0.7rem", color: "var(--dv-text-secondary)" }}>
					Discovers mandatory foreign keys vs. optional contextual relationships
					requiring link splitting
				</div>
			</div>

			<div style={{ display: "flex", flexDirection: "column", gap: "0.5rem" }}>
				{INCLUSION_DEPENDENCIES.map((edge) => {
					const isSplit = edge.verdict === "ALG_A4_SPLIT_LINK";
					const isSal = edge.verdict === "SAL_CROSSWALK";

					let badgeColor = "var(--dv-lnk-accent)";
					let badgeText = "Mandatory Hub FK";
					if (isSplit) {
						badgeColor = "var(--dv-sat-accent)";
						badgeText = "ALG A.4 Split Link";
					} else if (isSal) {
						badgeColor = "var(--dv-sal-accent)";
						badgeText = "Same-As Link (SAL)";
					}

					return (
						<div
							key={`${edge.sourceTable}.${edge.sourceColumn}->${edge.targetTable}.${edge.targetColumn}`}
							data-testid={`inclusion-edge-${edge.sourceColumn}`}
							style={{
								backgroundColor: "var(--dv-surface-2)",
								border: isSplit
									? "1.5px solid var(--dv-sat-accent)"
									: "1px solid var(--dv-border-subtle)",
								borderRadius: "0.375rem",
								padding: "0.6rem 0.75rem",
							}}
						>
							<div
								style={{
									display: "flex",
									justifyContent: "space-between",
									alignItems: "center",
									marginBottom: "0.3rem",
								}}
							>
								<div
									style={{
										fontFamily: "ui-monospace, monospace",
										fontSize: "0.75rem",
										color: "var(--dv-text-primary)",
									}}
								>
									<strong>{edge.sourceColumn}</strong>
									<span style={{ color: "var(--dv-text-muted)" }}>
										{" "}
										({edge.sourceTable.split(".")[1]})
									</span>
									<span style={{ color: "var(--dv-text-secondary)" }}> ⊆ </span>
									<strong>{edge.targetColumn}</strong>
									<span style={{ color: "var(--dv-text-muted)" }}>
										{" "}
										({edge.targetTable.split(".")[1]})
									</span>
								</div>

								<span
									style={{
										fontSize: "0.65rem",
										fontWeight: 700,
										color: badgeColor,
										backgroundColor: "var(--dv-surface-1)",
										border: `1px solid ${badgeColor}`,
										padding: "0.15rem 0.4rem",
										borderRadius: "0.2rem",
									}}
								>
									[{badgeText}]
								</span>
							</div>

							<div
								style={{
									display: "flex",
									gap: "1rem",
									fontSize: "0.7rem",
									fontFamily: "ui-monospace, monospace",
									color: "var(--dv-text-secondary)",
									marginBottom: "0.25rem",
								}}
							>
								<div>
									Inclusion ratio: {(edge.inclusionRatio * 100).toFixed(1)}%
								</div>
								<div>
									Null Rate: {(edge.nullRatio * 100).toFixed(1)}%
									{isSplit && (
										<span
											style={{ color: "var(--dv-sat-accent)", fontWeight: 700 }}
										>
											{" "}
											(High Sparsity!)
										</span>
									)}
								</div>
							</div>

							<div
								style={{
									fontSize: "0.7rem",
									color: "var(--dv-text-secondary)",
								}}
							>
								{edge.rationale}
							</div>
						</div>
					);
				})}
			</div>
		</div>
	);
}
