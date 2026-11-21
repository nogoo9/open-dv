/**
 * Right-hand 455px Physical Table, BKCC, Algorithm A.5 & 6-Gate Inspector (`06b`).
 */

import { CLAUDE_TOKENS, EXPLORER_FONTS } from "../design-tokens";
import type { DvGraphEntity } from "./types";

export interface DvTableLineageInspectorProps {
	entity: DvGraphEntity | null;
	scorecard?: Record<string, unknown> | undefined;
	onOpenDdlDialog: () => void;
	onPreviewRows?: ((tableName: string) => void) | undefined;
}

export interface GateBadgeResult {
	label: string;
	tone: "success" | "error" | "neutral";
}

const CANONICAL_GATE_KEYS = [
	"gate_0_profile_drift",
	"gate_1_contract_grain",
	"gate_2_sqlglot_ast",
	"gate_3_networkx_dag",
	"gate_4_yagni_cost_guard",
	"gate_5_duckdb_sandbox",
] as const;

const PASS_TOKENS = new Set([
	"PASS",
	"AUTO-FIX",
	"AUTO_REPAIRED",
	"OK",
	"TRUE",
]);

export function deriveGateBadge(
	scorecard?: Record<string, unknown> | undefined,
): GateBadgeResult {
	if (!scorecard || typeof scorecard !== "object") {
		return { label: "Gates 0–6 · Pending / Unverified", tone: "neutral" };
	}
	const rawPassed = scorecard.dv_dyn_passed;
	const rawTotal = scorecard.dv_dyn_total;
	if (
		typeof rawPassed === "number" &&
		typeof rawTotal === "number" &&
		Number.isFinite(rawPassed) &&
		Number.isFinite(rawTotal) &&
		rawTotal > 0
	) {
		return {
			label: `Gates 0–6 · ${rawPassed}/${rawTotal} DV-DYN`,
			tone: rawPassed === rawTotal ? "success" : "error",
		};
	}
	const canonicalEntries = CANONICAL_GATE_KEYS.map((k) => scorecard[k]).filter(
		(v) => v !== undefined && v !== null && String(v).trim() !== "",
	);
	const gateEntries =
		canonicalEntries.length > 0
			? canonicalEntries
			: ["gate_0", "gate_1", "gate_2", "gate_3", "gate_4", "gate_5", "gate_6"]
					.map((k) => scorecard[k])
					.filter(
						(v) => v !== undefined && v !== null && String(v).trim() !== "",
					);
	const shadowVal = scorecard.shadow_passed ?? scorecard.sandbox_passed;
	const hasShadow = typeof shadowVal === "boolean";
	if (gateEntries.length > 0 || hasShadow) {
		const passCount = gateEntries.filter(
			(v) => v === true || PASS_TOKENS.has(String(v).trim().toUpperCase()),
		).length;
		const gateCount = gateEntries.length;
		const shadowOk = !hasShadow || shadowVal === true;
		const allPassed = (gateCount === 0 || passCount === gateCount) && shadowOk;
		const shadowStr = hasShadow
			? ` · Sandbox ${shadowVal ? "PASS" : "FAIL"}`
			: "";
		if (gateCount > 0) {
			return {
				label: `Gates 0–5 · ${passCount}/${gateCount} PASS${shadowStr}`,
				tone: allPassed ? "success" : "error",
			};
		}
		return {
			label: `Gates 0–6 · Sandbox ${shadowVal ? "PASS" : "FAIL"}`,
			tone: shadowVal ? "success" : "error",
		};
	}
	return { label: "Gates 0–6 · Pending / Unverified", tone: "neutral" };
}

export function DvTableLineageInspector({
	entity,
	scorecard,
	onOpenDdlDialog,
	onPreviewRows,
}: DvTableLineageInspectorProps) {
	const gateBadge = deriveGateBadge(scorecard);
	const gateColor =
		gateBadge.tone === "success"
			? CLAUDE_TOKENS.colors.success
			: gateBadge.tone === "error"
				? CLAUDE_TOKENS.colors.error
				: CLAUDE_TOKENS.colors.onDarkSoft;

	if (!entity) {
		return (
			<aside
				data-testid="dv-table-lineage-inspector"
				className="claude-card-cream"
				style={{
					background: CLAUDE_TOKENS.colors.surfaceCard,
					border: `1px solid ${CLAUDE_TOKENS.colors.hairline}`,
					borderRadius: "12px",
					padding: "16px",
					color: CLAUDE_TOKENS.colors.muted,
				}}
			>
				Select any Hub, Link, Satellite, Reference, or PIT node to inspect its
				physical schema, BKCC, Algorithm A.5 hash, and Gate 0–6 proof.
			</aside>
		);
	}

	return (
		<aside
			data-testid="dv-table-lineage-inspector"
			className="claude-card-cream"
			style={{
				background: CLAUDE_TOKENS.colors.surfaceCard,
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
					alignItems: "flex-start",
					borderBottom: `1px solid ${CLAUDE_TOKENS.colors.hairline}`,
					paddingBottom: "8px",
					gap: "8px",
				}}
			>
				<div style={{ minWidth: 0 }}>
					<div
						style={{
							fontSize: "10px",
							textTransform: "uppercase",
							fontWeight: 600,
							letterSpacing: "0.6px",
							color: CLAUDE_TOKENS.colors.primaryActive,
						}}
					>
						Physical Table &amp; Lineage Inspector
					</div>
					<h2
						className="claude-h2"
						style={{
							margin: "2px 0 0",
							fontFamily: EXPLORER_FONTS.serif,
							fontSize: "20px",
							color: CLAUDE_TOKENS.colors.ink,
							wordBreak: "break-word",
						}}
					>
						{entity.schemaName}.{entity.name}
					</h2>
				</div>
				<span
					style={{
						padding: "3px 9px",
						borderRadius: "9999px",
						background: entity.isPii
							? CLAUDE_TOKENS.colors.primary
							: CLAUDE_TOKENS.colors.surfaceDark,
						color: CLAUDE_TOKENS.colors.onDark,
						fontFamily: EXPLORER_FONTS.mono,
						fontSize: "10px",
						whiteSpace: "nowrap",
					}}
				>
					{entity.zone} · {entity.kind}
				</span>
			</div>

			{/* Physical Columns */}
			<div>
				<div
					style={{
						fontSize: "11px",
						fontWeight: 500,
						color: CLAUDE_TOKENS.colors.muted,
						marginBottom: "4px",
					}}
				>
					Physical Columns (Canonical IR ➔ DuckDB / Databricks Delta)
				</div>
				<div
					style={{
						background: CLAUDE_TOKENS.colors.canvas,
						border: `1px solid ${CLAUDE_TOKENS.colors.hairline}`,
						borderRadius: "8px",
						overflowX: "auto",
					}}
				>
					<table
						style={{
							width: "100%",
							borderCollapse: "collapse",
							fontSize: "11px",
						}}
					>
						<thead>
							<tr>
								<th
									style={{
										textAlign: "left",
										padding: "6px 8px",
										background: CLAUDE_TOKENS.colors.surfaceSoft,
										color: CLAUDE_TOKENS.colors.muted,
										fontSize: "10px",
										textTransform: "uppercase",
									}}
								>
									Column
								</th>
								<th
									style={{
										textAlign: "left",
										padding: "6px 8px",
										background: CLAUDE_TOKENS.colors.surfaceSoft,
										color: CLAUDE_TOKENS.colors.muted,
										fontSize: "10px",
										textTransform: "uppercase",
									}}
								>
									Type
								</th>
								<th
									style={{
										textAlign: "left",
										padding: "6px 8px",
										background: CLAUDE_TOKENS.colors.surfaceSoft,
										color: CLAUDE_TOKENS.colors.muted,
										fontSize: "10px",
										textTransform: "uppercase",
									}}
								>
									Role / Lineage
								</th>
							</tr>
						</thead>
						<tbody>
							{entity.columns.map((col) => (
								<tr
									key={col.name}
									style={{
										borderTop: `1px solid ${CLAUDE_TOKENS.colors.hairlineSoft}`,
									}}
								>
									<td
										style={{
											padding: "5px 8px",
											fontFamily: EXPLORER_FONTS.mono,
											fontWeight: 600,
											color: CLAUDE_TOKENS.colors.ink,
										}}
									>
										{col.name}
									</td>
									<td
										style={{
											padding: "5px 8px",
											fontFamily: EXPLORER_FONTS.mono,
											color: CLAUDE_TOKENS.colors.primaryActive,
										}}
									>
										{col.sqlType}
									</td>
									<td
										style={{
											padding: "5px 8px",
											color: CLAUDE_TOKENS.colors.body,
										}}
									>
										{col.role}
									</td>
								</tr>
							))}
						</tbody>
					</table>
				</div>
			</div>

			{/* BKCC & Lineage */}
			<div
				style={{
					background: CLAUDE_TOKENS.colors.canvas,
					border: `1px solid ${CLAUDE_TOKENS.colors.hairline}`,
					borderRadius: "8px",
					padding: "8px 10px",
					display: "flex",
					flexDirection: "column",
					gap: "4px",
				}}
			>
				<div
					style={{
						display: "flex",
						justifyContent: "space-between",
						alignItems: "center",
						gap: "6px",
						flexWrap: "wrap",
					}}
				>
					<strong style={{ fontSize: "11px", color: CLAUDE_TOKENS.colors.ink }}>
						End-to-End Lineage &amp; BKCC Tier
					</strong>
					{entity.bkccExpr && (
						<span
							style={{
								padding: "2px 7px",
								borderRadius: "9999px",
								background: CLAUDE_TOKENS.colors.surfaceCreamStrong,
								fontFamily: EXPLORER_FONTS.mono,
								fontSize: "10px",
								color: CLAUDE_TOKENS.colors.ink,
							}}
						>
							BKCC: {entity.bkccExpr}
						</span>
					)}
				</div>
				<div
					style={{
						fontFamily: EXPLORER_FONTS.mono,
						fontSize: "10px",
						color: CLAUDE_TOKENS.colors.body,
						wordBreak: "break-word",
					}}
				>
					{entity.sourceLineage}
				</div>
				{entity.softDeprecated && entity.softDeprecated.length > 0 && (
					<div
						style={{
							fontSize: "10.5px",
							color: CLAUDE_TOKENS.colors.primaryActive,
						}}
					>
						Soft-deprecated (EVO-02 0 DROP): {entity.softDeprecated.join(", ")}
					</div>
				)}
			</div>

			{/* Dark Algorithm A.5 & Gate 0-6 Proof Card */}
			<div
				style={{
					background: CLAUDE_TOKENS.colors.surfaceDark,
					color: CLAUDE_TOKENS.colors.onDark,
					borderRadius: "10px",
					padding: "10px 12px",
					display: "flex",
					flexDirection: "column",
					gap: "7px",
					marginTop: "auto",
				}}
			>
				<div
					style={{
						display: "flex",
						justifyContent: "space-between",
						alignItems: "center",
						flexWrap: "wrap",
						gap: "6px",
					}}
				>
					<span
						style={{
							fontSize: "10.5px",
							fontWeight: 600,
							color: CLAUDE_TOKENS.colors.accentAmber,
						}}
					>
						Algorithm A.5 Hash &amp; Gate 0–6 Proof
					</span>
					<span
						data-testid="dv-gate-badge"
						style={{
							padding: "2px 8px",
							borderRadius: "9999px",
							background: CLAUDE_TOKENS.colors.surfaceDarkElevated,
							border: `1px solid ${CLAUDE_TOKENS.colors.body}`,
							fontFamily: EXPLORER_FONTS.mono,
							fontSize: "10px",
							color: gateColor,
						}}
					>
						{gateBadge.label}
					</span>
				</div>

				<div
					style={{
						background: CLAUDE_TOKENS.colors.surfaceDarkSoft,
						padding: "7px 9px",
						borderRadius: "6px",
						fontFamily: EXPLORER_FONTS.mono,
						fontSize: "10px",
						color: CLAUDE_TOKENS.colors.onDark,
						wordBreak: "break-word",
					}}
				>
					{entity.algA5Sql}
				</div>

				<div
					style={{
						display: "flex",
						justifyContent: "space-between",
						alignItems: "center",
						gap: "8px",
						flexWrap: "wrap",
					}}
				>
					{onPreviewRows && (
						<button
							type="button"
							onClick={() =>
								onPreviewRows(`${entity.schemaName}.${entity.name}`)
							}
							style={{
								padding: "4px 10px",
								borderRadius: "6px",
								fontSize: "10.5px",
								cursor: "pointer",
								background: CLAUDE_TOKENS.colors.surfaceDarkElevated,
								border: `1px solid ${CLAUDE_TOKENS.colors.body}`,
								color: CLAUDE_TOKENS.colors.onDark,
							}}
						>
							Preview Vault Rows
						</button>
					)}
					<button
						type="button"
						data-testid="btn-open-entity-ddl"
						onClick={onOpenDdlDialog}
						style={{
							padding: "4px 10px",
							borderRadius: "6px",
							fontSize: "10.5px",
							fontWeight: 500,
							cursor: "pointer",
							background: CLAUDE_TOKENS.colors.primary,
							border: "none",
							color: CLAUDE_TOKENS.colors.onPrimary,
						}}
					>
						Table DDL/DML
					</button>
				</div>
			</div>
		</aside>
	);
}
