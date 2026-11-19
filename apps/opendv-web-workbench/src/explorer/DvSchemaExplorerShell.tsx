/**
 * M3 Shell View for Tab 2 — DV Schema Explorer (`/dv-schema`).
 * Full 4-view interactive Data Vault 2.0 Schema Explorer lands in M4.
 */

import { CLAUDE_TOKENS, EXPLORER_FONTS } from "./design-tokens";

export interface DvSchemaExplorerShellProps {
	runId?: string | null | undefined;
	compareRunId?: string | null | undefined;
	sessionId?: string | null | undefined;
	onGoPipeline: () => void;
	onGoOntology: () => void;
}

const VIEW_TABS = [
	{ id: "graph", label: "Bipartite Topology (06b)" },
	{ id: "triage", label: "100% Triage & Wave Diff (06c)" },
	{ id: "rows", label: "Vault Rows & Safe SQL (D11)" },
	{ id: "sql", label: "5-Zone SQL (Z0–Z4) & IR" },
] as const;

export function DvSchemaExplorerShell({
	runId,
	compareRunId,
	sessionId,
	onGoPipeline,
	onGoOntology,
}: DvSchemaExplorerShellProps) {
	return (
		<section
			data-testid="dv-schema-explorer-view"
			aria-label="DV Schema Explorer"
			style={{
				padding: "24px",
				maxWidth: "1360px",
				margin: "0 auto",
				display: "flex",
				flexDirection: "column",
				gap: "18px",
			}}
		>
			<div
				data-testid="dv-schema-explorer-placeholder"
				className="claude-card-cream"
				style={{
					padding: "20px 24px",
					background: CLAUDE_TOKENS.colors.surfaceCard,
					border: `1px solid ${CLAUDE_TOKENS.colors.hairline}`,
					borderRadius: "12px",
					display: "flex",
					flexWrap: "wrap",
					alignItems: "center",
					justifyContent: "space-between",
					gap: "16px",
				}}
			>
				<div>
					<h1
						className="claude-h1"
						style={{
							margin: 0,
							fontFamily: EXPLORER_FONTS.serif,
							fontSize: "26px",
							fontWeight: 400,
							letterSpacing: "-0.45px",
							color: CLAUDE_TOKENS.colors.ink,
						}}
					>
						DV Schema Explorer
					</h1>
					<p
						style={{
							margin: "6px 0 0",
							fontSize: "13px",
							color: CLAUDE_TOKENS.colors.body,
						}}
					>
						Interactive Data Vault 2.0 bipartite topology, 100% column triage
						lineage, multi-wave diffs, populated DuckDB row previews, and 5-Zone
						(Z0–Z4) SQL bundles.
					</p>
				</div>

				<div style={{ display: "flex", alignItems: "center", gap: "8px" }}>
					{runId && (
						<span
							style={{
								padding: "4px 10px",
								borderRadius: "9999px",
								background: CLAUDE_TOKENS.colors.surfaceDark,
								color: CLAUDE_TOKENS.colors.onDark,
								fontFamily: EXPLORER_FONTS.mono,
								fontSize: "11.5px",
							}}
						>
							run: {runId}
						</span>
					)}
					{compareRunId && (
						<span
							style={{
								padding: "4px 10px",
								borderRadius: "9999px",
								background: CLAUDE_TOKENS.colors.surfaceCreamStrong,
								color: CLAUDE_TOKENS.colors.ink,
								fontFamily: EXPLORER_FONTS.mono,
								fontSize: "11.5px",
							}}
						>
							compare: {compareRunId}
						</span>
					)}
					{sessionId && (
						<span
							style={{
								padding: "4px 10px",
								borderRadius: "9999px",
								background: CLAUDE_TOKENS.colors.surfaceSoft,
								border: `1px solid ${CLAUDE_TOKENS.colors.hairlineStrong}`,
								color: CLAUDE_TOKENS.colors.ink,
								fontFamily: EXPLORER_FONTS.mono,
								fontSize: "11.5px",
							}}
						>
							session: {sessionId}
						</span>
					)}
				</div>
			</div>

			<div
				className="claude-responsive-grid"
				style={{
					display: "grid",
					gridTemplateColumns: "repeat(auto-fit, minmax(240px, 1fr))",
					gap: "12px",
				}}
			>
				{VIEW_TABS.map((tab) => (
					<div
						key={tab.id}
						style={{
							padding: "16px",
							background: CLAUDE_TOKENS.colors.canvas,
							border: `1px solid ${CLAUDE_TOKENS.colors.hairline}`,
							borderRadius: "10px",
						}}
					>
						<div
							style={{
								fontWeight: 500,
								fontSize: "13.5px",
								color: CLAUDE_TOKENS.colors.ink,
							}}
						>
							{tab.label}
						</div>
						<div
							style={{
								marginTop: "6px",
								fontSize: "12px",
								color: CLAUDE_TOKENS.colors.muted,
							}}
						>
							{runId
								? `Ready to inspect run ${runId} via /api/v1/runs/${runId}/dv-schema.`
								: "Select a completed pipeline run or build one in the Ontology & Pipeline wizard."}
						</div>
					</div>
				))}
			</div>

			<div style={{ display: "flex", gap: "10px", flexWrap: "wrap" }}>
				<button
					type="button"
					onClick={onGoOntology}
					className="claude-btn-primary"
					style={{
						padding: "8px 16px",
						background: CLAUDE_TOKENS.colors.primary,
						color: CLAUDE_TOKENS.colors.onPrimary,
						border: "none",
						borderRadius: "8px",
						fontSize: "13px",
						fontWeight: 500,
						cursor: "pointer",
					}}
				>
					Open Ontology &amp; Pipeline
				</button>
				<button
					type="button"
					onClick={onGoPipeline}
					className="claude-btn-secondary"
					style={{
						padding: "8px 16px",
						background: CLAUDE_TOKENS.colors.canvas,
						color: CLAUDE_TOKENS.colors.ink,
						border: `1px solid ${CLAUDE_TOKENS.colors.hairlineStrong}`,
						borderRadius: "8px",
						fontSize: "13px",
						fontWeight: 500,
						cursor: "pointer",
					}}
				>
					Open Run Explorer
				</button>
			</div>
		</section>
	);
}
