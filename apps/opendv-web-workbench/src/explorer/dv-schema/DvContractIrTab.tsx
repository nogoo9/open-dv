/**
 * View 4 (FR-DVE-07): Per-Zone Z0..Z4 DuckDB & Databricks Delta SQL Switcher
 * (`information_mart`) + `final_contract.yaml` & `final_canonical_ir.json` Inspector.
 */

import { CLAUDE_TOKENS, EXPLORER_FONTS } from "../design-tokens";
import { safeUseState } from "../modals/safeHooks";
import { generateZoneSqlFromProjection } from "./dv-schema-layout";
import type { DvSchemaProjectionDTO, ZoneSqlMap } from "./types";

const ZONES: ReadonlyArray<"Z0" | "Z1" | "Z2" | "Z3" | "Z4"> = [
	"Z0",
	"Z1",
	"Z2",
	"Z3",
	"Z4",
];

export interface DvContractIrTabProps {
	projection: DvSchemaProjectionDTO;
	zoneSqlDuckDb?: ZoneSqlMap | undefined;
	zoneSqlDatabricks?: ZoneSqlMap | undefined;
	finalContractYaml?: string | undefined;
	finalCanonicalIrJson?: string | undefined;
	initialDialect?: "duckdb" | "databricks" | undefined;
	initialZone?: "Z0" | "Z1" | "Z2" | "Z3" | "Z4" | undefined;
	initialArtifactTab?: "contract" | "ir" | undefined;
}

function buildReconstructedIrJson(projection: DvSchemaProjectionDTO): string {
	return JSON.stringify(
		{
			ir_version: "1.0.0",
			model_name: projection.model_name || "default_vault",
			run_id: projection.run_id,
			hubs: projection.hubs ?? [],
			links: projection.links ?? [],
			satellites: projection.satellites ?? [],
			reference_tables: projection.reference_tables ?? [],
			pit_tables: projection.pit_tables ?? [],
		},
		null,
		2,
	);
}

function downloadTextFile(filename: string, content: string, mime: string) {
	if (typeof Blob === "undefined" || typeof URL === "undefined") return;
	const blob = new Blob([content], { type: mime });
	const href = URL.createObjectURL(blob);
	const a = document.createElement("a");
	a.href = href;
	a.download = filename;
	a.click();
	URL.revokeObjectURL(href);
}

function resolveDialectZones(
	realSql: ZoneSqlMap | undefined,
	projection: DvSchemaProjectionDTO,
	dialect: "duckdb" | "databricks",
): ZoneSqlMap {
	if (realSql && Object.keys(realSql).length > 0) {
		return {
			...realSql,
			Z4: realSql.Z4 ?? Object.values(realSql).filter(Boolean).join("\n\n"),
		};
	}
	return generateZoneSqlFromProjection(projection, dialect);
}

export function DvContractIrTab({
	projection,
	zoneSqlDuckDb,
	zoneSqlDatabricks,
	finalContractYaml,
	finalCanonicalIrJson,
	initialDialect = "duckdb",
	initialZone = "Z4",
	initialArtifactTab = "contract",
}: DvContractIrTabProps) {
	const [dialect, setDialect] = safeUseState<"duckdb" | "databricks">(
		initialDialect,
	);
	const [activeZone, setActiveZone] = safeUseState<
		"Z0" | "Z1" | "Z2" | "Z3" | "Z4"
	>(initialZone);
	const [artifactTab, setArtifactTab] = safeUseState<"contract" | "ir">(
		initialArtifactTab,
	);

	const duckZones = resolveDialectZones(zoneSqlDuckDb, projection, "duckdb");
	const deltaZones = resolveDialectZones(
		zoneSqlDatabricks,
		projection,
		"databricks",
	);

	const activeSqlMap = dialect === "duckdb" ? duckZones : deltaZones;
	const availableZones = ZONES.filter((z) => Boolean(activeSqlMap[z]?.trim()));
	const dialectLabel = dialect === "duckdb" ? "DuckDB" : "Databricks Delta";
	const rawZoneSql = activeSqlMap[activeZone];
	const activeZoneSql =
		rawZoneSql && rawZoneSql.trim().length > 0
			? rawZoneSql
			: `-- ${dialectLabel} does not emit a separate ${activeZone} section for this run.\n-- Available zones for ${dialectLabel}: ${availableZones.join(", ") || "None"}\n-- Switch to DuckDB to inspect ${activeZone} SQL or select Z0, Z2, or Z4.`;
	const contractText =
		finalContractYaml ??
		`# final_contract.yaml (${projection.model_name})\nmodel_name: ${projection.model_name}\nrun_id: ${projection.run_id}\nhubs_count: ${(projection.hubs ?? []).length}`;
	const irText = finalCanonicalIrJson ?? buildReconstructedIrJson(projection);

	return (
		<div
			className="claude-responsive-grid"
			style={{
				display: "grid",
				gridTemplateColumns: "1.1fr 0.9fr",
				gap: "14px",
				alignItems: "stretch",
			}}
		>
			{/* Left: 5-Zone (Z0..Z4) DuckDB & Databricks Delta SQL */}
			<div
				className="claude-card-dark"
				style={{
					background: CLAUDE_TOKENS.colors.surfaceDark,
					color: CLAUDE_TOKENS.colors.onDark,
					borderRadius: "12px",
					padding: "14px",
					display: "flex",
					flexDirection: "column",
					gap: "10px",
					minWidth: 0,
				}}
			>
				<div
					style={{
						display: "flex",
						justifyContent: "space-between",
						alignItems: "center",
						flexWrap: "wrap",
						gap: "8px",
					}}
				>
					<div>
						<div
							style={{
								fontSize: "10px",
								textTransform: "uppercase",
								fontWeight: 600,
								letterSpacing: "0.6px",
								color: CLAUDE_TOKENS.colors.accentAmber,
							}}
						>
							5-Zone (Z0–Z4) · information_mart Standard
						</div>
						<div style={{ fontSize: "13.5px", fontWeight: 500 }}>
							Compiled Multi-Dialect SQL Bundle
						</div>
					</div>

					<div style={{ display: "flex", gap: "6px" }}>
						<button
							type="button"
							onClick={() => setDialect("duckdb")}
							style={{
								padding: "4px 10px",
								borderRadius: "6px",
								fontSize: "11px",
								cursor: "pointer",
								border: `1px solid ${CLAUDE_TOKENS.colors.body}`,
								background:
									dialect === "duckdb"
										? CLAUDE_TOKENS.colors.primary
										: CLAUDE_TOKENS.colors.surfaceDarkElevated,
								color: CLAUDE_TOKENS.colors.onDark,
							}}
						>
							DuckDB
						</button>
						<button
							type="button"
							onClick={() => setDialect("databricks")}
							style={{
								padding: "4px 10px",
								borderRadius: "6px",
								fontSize: "11px",
								cursor: "pointer",
								border: `1px solid ${CLAUDE_TOKENS.colors.body}`,
								background:
									dialect === "databricks"
										? CLAUDE_TOKENS.colors.primary
										: CLAUDE_TOKENS.colors.surfaceDarkElevated,
								color: CLAUDE_TOKENS.colors.onDark,
							}}
						>
							Databricks Delta
						</button>
					</div>
				</div>

				<div style={{ display: "flex", gap: "6px", flexWrap: "wrap" }}>
					{ZONES.map((z) => {
						const hasZoneSql = Boolean(activeSqlMap[z]?.trim());
						return (
							<button
								key={z}
								type="button"
								title={
									hasZoneSql
										? `${dialectLabel} ${z}`
										: `${dialectLabel} omits ${z} (click for note)`
								}
								onClick={() => setActiveZone(z)}
								style={{
									padding: "4px 10px",
									borderRadius: "9999px",
									fontSize: "11px",
									fontFamily: EXPLORER_FONTS.mono,
									cursor: "pointer",
									opacity: hasZoneSql || activeZone === z ? 1 : 0.6,
									border: `1px solid ${CLAUDE_TOKENS.colors.body}`,
									background:
										activeZone === z
											? CLAUDE_TOKENS.colors.primary
											: CLAUDE_TOKENS.colors.surfaceDarkElevated,
									color: CLAUDE_TOKENS.colors.onDark,
								}}
							>
								{z}
							</button>
						);
					})}
				</div>

				<pre
					style={{
						margin: 0,
						flex: 1,
						minHeight: "320px",
						padding: "12px",
						borderRadius: "8px",
						background: CLAUDE_TOKENS.colors.surfaceDarkSoft,
						color: CLAUDE_TOKENS.colors.onDark,
						fontFamily: EXPLORER_FONTS.mono,
						fontSize: "11px",
						lineHeight: 1.5,
						overflow: "auto",
					}}
				>
					{activeZoneSql}
				</pre>
			</div>

			{/* Right: final_contract.yaml & final_canonical_ir.json Inspector */}
			<div
				style={{
					background: CLAUDE_TOKENS.colors.canvas,
					border: `1px solid ${CLAUDE_TOKENS.colors.hairline}`,
					borderRadius: "12px",
					padding: "14px",
					display: "flex",
					flexDirection: "column",
					gap: "10px",
					minWidth: 0,
				}}
			>
				<div
					style={{
						display: "flex",
						justifyContent: "space-between",
						alignItems: "center",
						flexWrap: "wrap",
						gap: "8px",
					}}
				>
					<div style={{ display: "flex", gap: "6px" }}>
						<button
							type="button"
							onClick={() => setArtifactTab("contract")}
							style={{
								padding: "5px 10px",
								borderRadius: "8px",
								fontSize: "11.5px",
								fontFamily: EXPLORER_FONTS.mono,
								cursor: "pointer",
								border: `1px solid ${CLAUDE_TOKENS.colors.hairlineStrong}`,
								background:
									artifactTab === "contract"
										? CLAUDE_TOKENS.colors.surfaceCreamStrong
										: CLAUDE_TOKENS.colors.canvas,
								color: CLAUDE_TOKENS.colors.ink,
							}}
						>
							final_contract.yaml
						</button>
						<button
							type="button"
							onClick={() => setArtifactTab("ir")}
							style={{
								padding: "5px 10px",
								borderRadius: "8px",
								fontSize: "11.5px",
								fontFamily: EXPLORER_FONTS.mono,
								cursor: "pointer",
								border: `1px solid ${CLAUDE_TOKENS.colors.hairlineStrong}`,
								background:
									artifactTab === "ir"
										? CLAUDE_TOKENS.colors.surfaceCreamStrong
										: CLAUDE_TOKENS.colors.canvas,
								color: CLAUDE_TOKENS.colors.ink,
							}}
						>
							final_canonical_ir.json
						</button>
					</div>

					<button
						type="button"
						onClick={() =>
							downloadTextFile(
								artifactTab === "contract"
									? "final_contract.yaml"
									: "final_canonical_ir.json",
								artifactTab === "contract" ? contractText : irText,
								artifactTab === "contract"
									? "text/yaml;charset=utf-8"
									: "application/json;charset=utf-8",
							)
						}
						style={{
							padding: "5px 11px",
							borderRadius: "8px",
							fontSize: "11.5px",
							cursor: "pointer",
							border: `1px solid ${CLAUDE_TOKENS.colors.hairlineStrong}`,
							background: CLAUDE_TOKENS.colors.canvas,
							color: CLAUDE_TOKENS.colors.ink,
						}}
					>
						Download {artifactTab === "contract" ? "YAML" : "JSON"}
					</button>
				</div>

				<pre
					style={{
						margin: 0,
						flex: 1,
						minHeight: "320px",
						padding: "12px",
						borderRadius: "8px",
						background: CLAUDE_TOKENS.colors.surfaceDark,
						color: CLAUDE_TOKENS.colors.onDark,
						fontFamily: EXPLORER_FONTS.mono,
						fontSize: "11px",
						lineHeight: 1.5,
						overflow: "auto",
					}}
				>
					{artifactTab === "contract" ? contractText : irText}
				</pre>
			</div>
		</div>
	);
}
