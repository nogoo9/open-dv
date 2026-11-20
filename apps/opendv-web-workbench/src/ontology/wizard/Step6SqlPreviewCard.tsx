/**
 * Step 6 Dark-Chrome 5-Zone SQL Preview Card with Dialect Switcher (`DESIGN.md`, `UAT-12`, `UAT-17`)
 * (apps/opendv-web-workbench/src/ontology/wizard/Step6SqlPreviewCard.tsx)
 */

import type React from "react";
import { CLAUDE_TOKENS, EXPLORER_FONTS } from "../../explorer/design-tokens";
import { safeUseState } from "../../explorer/modals/safeHooks";
import type { ZoneSqlByDialect } from "../store/store-types";

export interface Step6SqlPreviewCardProps {
	activeRunId?: string | null | undefined;
	selectedDialects?: Array<"duckdb" | "databricks"> | undefined;
	zoneSqlByZone?: Record<string, string> | undefined;
	zoneSqlByDialect?: ZoneSqlByDialect | undefined;
	isRunningPipeline?: boolean | undefined;
}

const ZONE_KEYS = ["Z0", "Z1", "Z2", "Z3", "Z4"] as const;

const ZONE_LABELS: Record<string, string> = {
	Z0: "Z0 · Control & Landing",
	Z1: "Z1 · Staging",
	Z2: "Z2 · Raw Vault",
	Z3: "Z3 · Business Vault",
	Z4: "Z4 · information_mart",
};

const EMPTY_SQL_FALLBACK = "CREATE SCHEMA IF NOT EXISTS information_mart;";

export const Step6SqlPreviewCard: React.FC<Step6SqlPreviewCardProps> = ({
	activeRunId,
	selectedDialects = ["duckdb"],
	zoneSqlByZone = {},
	zoneSqlByDialect,
	isRunningPipeline = false,
}) => {
	const initialDialect: "duckdb" | "databricks" =
		selectedDialects.includes("databricks") &&
		!selectedDialects.includes("duckdb")
			? "databricks"
			: "duckdb";
	const [previewDialect, setPreviewDialect] = safeUseState<
		"duckdb" | "databricks"
	>(initialDialect);
	const [activeZone, setActiveZone] = safeUseState<string>("Z0");

	const dialectMaps = zoneSqlByDialect ?? { duckdb: {}, databricks: {} };
	const hasDuckSql = Object.keys(dialectMaps.duckdb).length > 0;
	const hasDbSql = Object.keys(dialectMaps.databricks).length > 0;

	const activeSqlMap =
		previewDialect === "databricks"
			? hasDbSql
				? dialectMaps.databricks
				: hasDuckSql
					? {}
					: zoneSqlByZone
			: hasDuckSql
				? dialectMaps.duckdb
				: hasDbSql && !selectedDialects.includes("duckdb")
					? dialectMaps.databricks
					: zoneSqlByZone;

	const nonEmptyZones = ZONE_KEYS.filter((z) =>
		Boolean(activeSqlMap[z]?.trim()),
	);
	const currentZone = activeSqlMap[activeZone]?.trim()
		? activeZone
		: (nonEmptyZones[0] ?? "Z0");
	const hasAnySqlForDialect = nonEmptyZones.length > 0;

	return (
		<div
			data-testid="step6-sql-preview-card"
			className="claude-card-dark"
			style={{
				backgroundColor: CLAUDE_TOKENS.colors.surfaceDark,
				color: CLAUDE_TOKENS.colors.onDark,
				border: `1px solid ${CLAUDE_TOKENS.colors.surfaceDarkElevated}`,
				borderRadius: "10px",
				padding: "16px",
				display: "flex",
				flexDirection: "column",
				gap: "12px",
			}}
		>
			<div
				style={{
					display: "flex",
					justifyContent: "space-between",
					alignItems: "center",
					flexWrap: "wrap",
					gap: "10px",
				}}
			>
				<h2
					className="claude-h2"
					style={{
						margin: 0,
						color: CLAUDE_TOKENS.colors.onDark,
						fontSize: "18px",
					}}
				>
					5-Zone Generated SQL (Z0..Z4 · information_mart)
				</h2>
				<div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
					{activeRunId && (
						<span
							style={{
								display: "inline-flex",
								alignItems: "center",
								gap: "6px",
								padding: "3px 10px",
								borderRadius: "999px",
								backgroundColor: CLAUDE_TOKENS.colors.surfaceDarkElevated,
								border: "1px solid #33312c",
								fontFamily: EXPLORER_FONTS.mono,
								fontSize: "11px",
								color: CLAUDE_TOKENS.colors.onDark,
							}}
						>
							<span
								style={{
									color: isRunningPipeline
										? CLAUDE_TOKENS.colors.warning
										: CLAUDE_TOKENS.colors.success,
								}}
							>
								●
							</span>
							<span>
								{activeRunId} · {isRunningPipeline ? "Running" : "Verified"}
							</span>
						</span>
					)}
					<div style={{ display: "flex", gap: "4px" }}>
						{(
							[
								{ id: "duckdb", label: "DuckDB" },
								{ id: "databricks", label: "Databricks Delta" },
							] as const
						).map((d) => {
							const active = previewDialect === d.id;
							return (
								<button
									key={d.id}
									type="button"
									data-testid={`btn-sql-dialect-${d.id}`}
									onClick={() => setPreviewDialect(d.id)}
									style={{
										fontSize: "11.5px",
										fontWeight: 500,
										padding: "4px 10px",
										borderRadius: "7px",
										cursor: "pointer",
										border: active ? "none" : "1px solid #33312c",
										backgroundColor: active
											? CLAUDE_TOKENS.colors.primary
											: CLAUDE_TOKENS.colors.surfaceDarkElevated,
										color: active
											? CLAUDE_TOKENS.colors.onPrimary
											: CLAUDE_TOKENS.colors.onDarkSoft,
									}}
								>
									{d.label}
								</button>
							);
						})}
					</div>
				</div>
			</div>

			<div style={{ display: "flex", gap: "6px", flexWrap: "wrap" }}>
				{ZONE_KEYS.map((z) => {
					const isSelected = currentZone === z;
					const hasZoneSql = Boolean(activeSqlMap[z]?.trim());
					const isDisabled = hasAnySqlForDialect && !hasZoneSql;
					return (
						<button
							key={z}
							type="button"
							disabled={isDisabled}
							onClick={() => setActiveZone(z)}
							style={{
								fontSize: "11.5px",
								fontWeight: 500,
								padding: "4px 9px",
								borderRadius: "7px",
								cursor: isDisabled ? "not-allowed" : "pointer",
								opacity: isDisabled ? 0.45 : 1,
								border: isSelected ? "none" : "1px solid #33312c",
								backgroundColor: isSelected
									? CLAUDE_TOKENS.colors.primary
									: CLAUDE_TOKENS.colors.surfaceDarkElevated,
								color: isSelected
									? CLAUDE_TOKENS.colors.onPrimary
									: CLAUDE_TOKENS.colors.onDark,
							}}
						>
							{ZONE_LABELS[z]}
						</button>
					);
				})}
			</div>

			<pre
				data-testid="step6-sql-preview-pre"
				style={{
					margin: 0,
					padding: "14px",
					borderRadius: "8px",
					backgroundColor: CLAUDE_TOKENS.colors.surfaceDarkSoft,
					color: CLAUDE_TOKENS.colors.onDark,
					border: "1px solid #2c2a26",
					fontFamily: EXPLORER_FONTS.mono,
					fontSize: "12px",
					lineHeight: 1.5,
					overflowX: "auto",
					maxHeight: "420px",
					overflowY: "auto",
				}}
			>
				{activeSqlMap[currentZone] || EMPTY_SQL_FALLBACK}
			</pre>
		</div>
	);
};
