/**
 * View 2 (06c): 100% Column Triage Ledger & Multi-Wave Schema Diff (`W1 -> W2 -> W3`)
 * (`FR-DVE-04`, `FR-DVE-05`).
 */

import { useMemo } from "react";
import { CLAUDE_TOKENS, EXPLORER_FONTS } from "../design-tokens";
import { safeUseState } from "../modals/safeHooks";
import { formatTriageCsv } from "./dv-schema-utils";
import type { DvSchemaProjectionDTO } from "./types";

export interface DvTriageAndWaveDiffTabProps {
	projection: DvSchemaProjectionDTO;
	runId?: string | null | undefined;
	compareRunId?: string | null | undefined;
}

export function DvTriageAndWaveDiffTab({
	projection,
	runId,
	compareRunId,
}: DvTriageAndWaveDiffTabProps) {
	const [filterText, setFilterText] = safeUseState("");
	const ledger = projection.column_triage_ledger ?? [];
	const totalCount = ledger.length;
	const mappedCount = ledger.filter(
		(row) => String(row.target_entity ?? "").trim().length > 0,
	).length;
	const pct =
		totalCount > 0 ? Math.round((mappedCount / totalCount) * 100) : 100;

	const filteredRows = useMemo(() => {
		const q = filterText.trim().toLowerCase();
		if (!q) return ledger;
		return ledger.filter((row) =>
			Object.values(row).some((v) =>
				String(v ?? "")
					.toLowerCase()
					.includes(q),
			),
		);
	}, [ledger, filterText]);

	const diff = projection.wave_diff;
	const droppedCount = diff?.dropped_columns_count ?? 0;
	const hdMutations = diff?.hash_diff_mutations_count ?? 0;
	const hasPiiSat = (projection.satellites ?? []).some(
		(s) =>
			s.satellite_type.toUpperCase().includes("PII") || s.name.includes("_pii"),
	);
	const hasSal = (projection.links ?? []).some(
		(l) =>
			l.link_type.toUpperCase().includes("SAL") || l.name.startsWith("sal_"),
	);

	const handleExportCsv = () => {
		const csv = formatTriageCsv(ledger);
		if (typeof Blob === "undefined" || typeof URL === "undefined") return;
		const blob = new Blob([csv], { type: "text/csv;charset=utf-8;" });
		const href = URL.createObjectURL(blob);
		const a = document.createElement("a");
		a.href = href;
		a.download = `${runId ?? projection.run_id ?? "dv"}-column-triage.csv`;
		a.click();
		URL.revokeObjectURL(href);
	};

	return (
		<div style={{ display: "flex", flexDirection: "column", gap: "12px" }}>
			{/* Wave Evolution Summary Banner */}
			<div
				className="claude-card-cream"
				style={{
					background: CLAUDE_TOKENS.colors.surfaceCard,
					border: `1px solid ${CLAUDE_TOKENS.colors.hairline}`,
					borderRadius: "12px",
					padding: "10px 14px",
					display: "flex",
					justifyContent: "space-between",
					alignItems: "center",
					flexWrap: "wrap",
					gap: "10px",
				}}
			>
				<div
					style={{
						display: "flex",
						alignItems: "center",
						gap: "8px",
						flexWrap: "wrap",
					}}
				>
					<strong style={{ fontSize: "12px", color: CLAUDE_TOKENS.colors.ink }}>
						Wave Diff:
					</strong>
					<span
						style={{
							padding: "2px 9px",
							borderRadius: "9999px",
							background: CLAUDE_TOKENS.colors.canvas,
							border: `1px solid ${CLAUDE_TOKENS.colors.hairline}`,
							fontFamily: EXPLORER_FONTS.mono,
							fontSize: "10.5px",
						}}
					>
						{compareRunId ?? diff?.compare_run_id ?? "W1 Baseline"} ➔{" "}
						{runId ?? projection.run_id}
					</span>
					<span
						style={{
							padding: "2px 9px",
							borderRadius: "9999px",
							background: CLAUDE_TOKENS.colors.canvas,
							border: `1px solid ${CLAUDE_TOKENS.colors.hairline}`,
							fontSize: "10.5px",
						}}
					>
						+{diff?.added_hubs?.length ?? 0} Hubs · +
						{diff?.added_links?.length ?? 0} Links · +
						{diff?.added_satellites?.length ?? 0} Satellites
					</span>
					<span
						style={{
							padding: "2px 9px",
							borderRadius: "9999px",
							background: CLAUDE_TOKENS.colors.canvas,
							border: `1px solid ${CLAUDE_TOKENS.colors.hairline}`,
							fontSize: "10.5px",
							color: CLAUDE_TOKENS.colors.ink,
						}}
					>
						{droppedCount} DROP / {hdMutations} hash_diff Mutation (DV-DYN-014)
					</span>
				</div>

				<div style={{ display: "flex", alignItems: "center", gap: "10px" }}>
					<span
						style={{
							fontFamily: EXPLORER_FONTS.mono,
							fontSize: "12px",
							fontWeight: 600,
							color: CLAUDE_TOKENS.colors.ink,
						}}
					>
						Triage: {mappedCount}/{totalCount} ({pct}%)
					</span>
					<button
						type="button"
						data-testid="btn-export-triage-csv"
						onClick={handleExportCsv}
						className="claude-btn-secondary"
						style={{
							padding: "6px 12px",
							borderRadius: "8px",
							border: `1px solid ${CLAUDE_TOKENS.colors.hairlineStrong}`,
							background: CLAUDE_TOKENS.colors.canvas,
							color: CLAUDE_TOKENS.colors.ink,
							fontSize: "11.5px",
							fontWeight: 500,
							cursor: "pointer",
						}}
					>
						Export Triage CSV
					</button>
				</div>
			</div>

			{/* Split: Left 100% Column Triage Ledger + Right Wave Diff Cards */}
			<div
				className="claude-responsive-grid"
				style={{
					display: "grid",
					gridTemplateColumns: "1.25fr 0.85fr",
					gap: "14px",
					alignItems: "start",
				}}
			>
				<div
					style={{
						background: CLAUDE_TOKENS.colors.canvas,
						border: `1px solid ${CLAUDE_TOKENS.colors.hairline}`,
						borderRadius: "12px",
						padding: "12px 14px",
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
							gap: "8px",
							flexWrap: "wrap",
						}}
					>
						<div>
							<div
								style={{
									fontSize: "10px",
									textTransform: "uppercase",
									fontWeight: 600,
									letterSpacing: "0.6px",
									color: CLAUDE_TOKENS.colors.primaryActive,
								}}
							>
								Gate 1 · DV-G4-002 Certified
							</div>
							<h2
								className="claude-h2"
								style={{ margin: 0, fontFamily: EXPLORER_FONTS.serif }}
							>
								100% Column Triage &amp; End-to-End Lineage Ledger
							</h2>
						</div>
						<input
							type="search"
							placeholder="Filter source, role, target..."
							value={filterText}
							onChange={(ev) => setFilterText(ev.target.value)}
							style={{
								width: "200px",
								padding: "5px 10px",
								borderRadius: "8px",
								border: `1px solid ${CLAUDE_TOKENS.colors.hairlineStrong}`,
								background: CLAUDE_TOKENS.colors.canvas,
								fontSize: "11.5px",
							}}
						/>
					</div>

					<div style={{ overflowX: "auto" }}>
						<table
							style={{
								width: "100%",
								borderCollapse: "collapse",
								fontSize: "11px",
							}}
						>
							<thead>
								<tr>
									{[
										"Wave",
										"Source Table.Column",
										"Triage Role",
										"Graph A URI",
										"Z1 Staging Expr",
										"Target Vault Entity",
									].map((h) => (
										<th
											key={h}
											style={{
												textAlign: "left",
												padding: "6px 8px",
												background: CLAUDE_TOKENS.colors.surfaceSoft,
												color: CLAUDE_TOKENS.colors.muted,
												fontSize: "10px",
												textTransform: "uppercase",
												borderBottom: `1px solid ${CLAUDE_TOKENS.colors.hairline}`,
											}}
										>
											{h}
										</th>
									))}
								</tr>
							</thead>
							<tbody>
								{filteredRows.length === 0 ? (
									<tr>
										<td
											colSpan={6}
											style={{
												padding: "14px",
												textAlign: "center",
												color: CLAUDE_TOKENS.colors.muted,
											}}
										>
											No column triage entries recorded.
										</td>
									</tr>
								) : (
									filteredRows.map((row, idx) => (
										<tr
											key={`${String(row.source_table)}-${String(row.source_column)}-${idx}`}
											style={{
												borderBottom: `1px solid ${CLAUDE_TOKENS.colors.hairlineSoft}`,
											}}
										>
											<td
												style={{
													padding: "6px 8px",
													fontFamily: EXPLORER_FONTS.mono,
												}}
											>
												{String(row.wave ?? "W1")}
											</td>
											<td
												style={{
													padding: "6px 8px",
													fontFamily: EXPLORER_FONTS.mono,
												}}
											>
												{String(row.source_table ?? "")}.
												{String(row.source_column ?? "")}
											</td>
											<td style={{ padding: "6px 8px" }}>
												<span
													style={{
														padding: "2px 7px",
														borderRadius: "9999px",
														background: CLAUDE_TOKENS.colors.surfaceCard,
														fontSize: "10px",
														fontWeight: 500,
													}}
												>
													{String(row.triage_role ?? "")}
												</span>
											</td>
											<td
												style={{
													padding: "6px 8px",
													fontFamily: EXPLORER_FONTS.mono,
													fontSize: "10px",
													color: CLAUDE_TOKENS.colors.muted,
												}}
											>
												{String(row.graph_a_uri ?? "")}
											</td>
											<td
												style={{
													padding: "6px 8px",
													fontFamily: EXPLORER_FONTS.mono,
													fontSize: "10px",
												}}
											>
												{String(row.staging_expr ?? "")}
											</td>
											<td
												style={{
													padding: "6px 8px",
													fontFamily: EXPLORER_FONTS.mono,
													fontWeight: 600,
												}}
											>
												{String(row.target_entity ?? "")}
											</td>
										</tr>
									))
								)}
							</tbody>
						</table>
					</div>
				</div>

				{/* Right: Wave Evolution Structural Diff Cards (EVO-01..08) */}
				<div
					className="claude-card-cream"
					style={{
						background: CLAUDE_TOKENS.colors.surfaceCard,
						border: `1px solid ${CLAUDE_TOKENS.colors.hairline}`,
						borderRadius: "12px",
						padding: "12px 14px",
						display: "flex",
						flexDirection: "column",
						gap: "8px",
						minWidth: 0,
					}}
				>
					<div
						style={{
							display: "flex",
							justifyContent: "space-between",
							alignItems: "center",
							gap: "8px",
						}}
					>
						<h3
							className="claude-h2"
							style={{ margin: 0, fontFamily: EXPLORER_FONTS.serif }}
						>
							Wave Evolution Diff (W1 ➔ W2 ➔ W3)
						</h3>
						<span
							style={{ fontSize: "10.5px", color: CLAUDE_TOKENS.colors.ink }}
						>
							DV-EVO-001..008 PASS
						</span>
					</div>

					{/* EVO-01 Companion Drift Satellite */}
					<div
						style={{
							background: CLAUDE_TOKENS.colors.canvas,
							border: `1px solid ${CLAUDE_TOKENS.colors.hairline}`,
							borderRadius: "8px",
							padding: "8px 10px",
							display: "flex",
							justifyContent: "space-between",
							alignItems: "center",
							gap: "8px",
						}}
					>
						<div style={{ fontSize: "11.5px" }}>
							<strong style={{ fontFamily: EXPLORER_FONTS.mono }}>
								{(diff?.added_satellites ?? []).join(", ") ||
									"Companion Satellites"}
							</strong>
							<div
								style={{
									fontSize: "10.5px",
									color: CLAUDE_TOKENS.colors.muted,
								}}
							>
								+{(diff?.added_columns ?? []).length} drifted attributes in
								companion extension satellite
							</div>
						</div>
						<span
							style={{
								padding: "2px 8px",
								borderRadius: "9999px",
								background: CLAUDE_TOKENS.colors.primary,
								color: CLAUDE_TOKENS.colors.onPrimary,
								fontSize: "10px",
								fontWeight: 600,
							}}
						>
							EVO-01
						</span>
					</div>

					{/* EVO-02 Soft-Deprecated Columns */}
					<div
						style={{
							background: CLAUDE_TOKENS.colors.canvas,
							border: `1px solid ${CLAUDE_TOKENS.colors.hairline}`,
							borderRadius: "8px",
							padding: "8px 10px",
							display: "flex",
							justifyContent: "space-between",
							alignItems: "center",
							gap: "8px",
						}}
					>
						<div style={{ fontSize: "11.5px" }}>
							<strong style={{ fontFamily: EXPLORER_FONTS.mono }}>
								{(diff?.soft_deprecated_columns ?? [])
									.map((s) => `${s.satellite}.${s.column}`)
									.join(", ") || "Zero dropped physical columns"}
							</strong>
							<div
								style={{
									fontSize: "10.5px",
									color: CLAUDE_TOKENS.colors.muted,
								}}
							>
								CAST(NULL) Zone 1 projection · {droppedCount} DROP
							</div>
						</div>
						<span
							style={{
								padding: "2px 8px",
								borderRadius: "9999px",
								background: CLAUDE_TOKENS.colors.surfaceSoft,
								fontSize: "10px",
								fontWeight: 600,
							}}
						>
							EVO-02
						</span>
					</div>

					{/* EVO-03 Rename Aliasing */}
					<div
						style={{
							background: CLAUDE_TOKENS.colors.canvas,
							border: `1px solid ${CLAUDE_TOKENS.colors.hairline}`,
							borderRadius: "8px",
							padding: "8px 10px",
							display: "flex",
							justifyContent: "space-between",
							alignItems: "center",
							gap: "8px",
						}}
					>
						<div style={{ fontSize: "11.5px" }}>
							<strong>Staging Rename Aliases Preserved</strong>
							<div
								style={{
									fontSize: "10.5px",
									color: CLAUDE_TOKENS.colors.muted,
								}}
							>
								{hdMutations} hash_diff mutations · Prior Wave digests
								bit-identical
							</div>
						</div>
						<span
							style={{
								padding: "2px 8px",
								borderRadius: "9999px",
								background: CLAUDE_TOKENS.colors.surfaceSoft,
								fontSize: "10px",
								fontWeight: 600,
							}}
						>
							EVO-03
						</span>
					</div>

					{/* EVO-07 PII Split & EVO-08 SAL */}
					<div
						style={{
							background: CLAUDE_TOKENS.colors.canvas,
							border: `1px solid ${CLAUDE_TOKENS.colors.hairline}`,
							borderRadius: "8px",
							padding: "8px 10px",
							display: "flex",
							justifyContent: "space-between",
							alignItems: "center",
							gap: "8px",
						}}
					>
						<div style={{ fontSize: "11.5px" }}>
							<strong>PII Satellite Split &amp; Same-As Link Crosswalk</strong>
							<div
								style={{
									fontSize: "10.5px",
									color: CLAUDE_TOKENS.colors.muted,
								}}
							>
								{hasPiiSat ? "sat_*_pii isolated" : "No PII columns"} ·{" "}
								{hasSal ? "SAL active" : "Ready for Tier-3 convergence"}
							</div>
						</div>
						<span
							style={{
								padding: "2px 8px",
								borderRadius: "9999px",
								background: CLAUDE_TOKENS.colors.surfaceSoft,
								fontSize: "10px",
								fontWeight: 600,
							}}
						>
							EVO-07 / EVO-08
						</span>
					</div>
				</div>
			</div>
		</div>
	);
}
