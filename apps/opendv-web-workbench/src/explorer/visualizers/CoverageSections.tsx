/**
 * Coverage Sections for Ontology Coverage Visualizer (User Directive R9 Modularity).
 */

import { EXPLORER_COLORS, EXPLORER_FONTS } from "../design-tokens";
import type { MappedConceptItem, OntologyCoverageData, UnmappedColumnItem } from "./OntologyCoverageVisualizer";

export function MappedConceptsList({ data }: { data: OntologyCoverageData }) {
	return (
		<div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
			<h4
				style={{
					margin: 0,
					fontSize: "12px",
					textTransform: "uppercase",
					letterSpacing: "0.06em",
					color: EXPLORER_COLORS.inkLabel,
				}}
			>
				Mapped Business Concepts & Hubs
			</h4>
			<div
				data-testid="mapped-concepts-list"
				style={{
					display: "flex",
					flexDirection: "column",
					gap: "6px",
					maxHeight: "220px",
					overflowY: "auto",
				}}
			>
				{(data.detailed_mapped_concepts ?? []).length > 0 ? (
					data.detailed_mapped_concepts?.map((c, idx) => (
						<div
							key={c.concept_uri ?? `concept-${idx}`}
							data-testid="mapped-concept-item"
							style={{
								padding: "8px 10px",
								borderRadius: "6px",
								background: EXPLORER_COLORS.bgPage,
								border: `1px solid ${EXPLORER_COLORS.borderLine}`,
								display: "flex",
								justifyContent: "space-between",
								alignItems: "center",
								fontSize: "12px",
							}}
						>
							<div>
								<div style={{ fontWeight: 600, color: EXPLORER_COLORS.inkPrimary }}>
									{c.concept_name ?? c.concept_uri}
								</div>
								<div
									style={{
										fontFamily: EXPLORER_FONTS.mono,
										fontSize: "11px",
										color: EXPLORER_COLORS.inkLabel,
									}}
								>
									{c.concept_uri}
								</div>
							</div>
							{c.target_hub && (
								<span
									style={{
										fontFamily: EXPLORER_FONTS.mono,
										fontSize: "11px",
										padding: "2px 6px",
										borderRadius: "4px",
										background: "#EEF2FF",
										color: "#1D4ED8",
										fontWeight: 500,
									}}
								>
									→ {c.target_hub}
								</span>
							)}
						</div>
					))
				) : (data.mappings ?? []).length > 0 ? (
					data.mappings?.map((m, idx) => (
						<div
							key={m.source ?? `mapping-${idx}`}
							data-testid="mapped-concept-item"
							style={{
								padding: "8px 10px",
								borderRadius: "6px",
								background: EXPLORER_COLORS.bgPage,
								border: `1px solid ${EXPLORER_COLORS.borderLine}`,
								display: "flex",
								justifyContent: "space-between",
								alignItems: "center",
								fontSize: "12px",
							}}
						>
							<div>
								<span style={{ fontFamily: EXPLORER_FONTS.mono, fontWeight: 500 }}>
									{m.source}
								</span>
								<span style={{ color: EXPLORER_COLORS.inkMuted }}>
									{" "}→ {m.concept}
								</span>
							</div>
							{m.target_hub && (
								<span
									style={{
										fontFamily: EXPLORER_FONTS.mono,
										fontSize: "11px",
										padding: "2px 6px",
										borderRadius: "4px",
										background: "#EEF2FF",
										color: "#1D4ED8",
									}}
								>
									{m.target_hub}
								</span>
							)}
						</div>
					))
				) : (
					<div
						data-testid="no-mapped-concepts"
						style={{
							fontSize: "12px",
							color: EXPLORER_COLORS.inkMuted,
							fontStyle: "italic",
						}}
					>
						No explicit mapped concept records.
					</div>
				)}
			</div>
		</div>
	);
}

export function UnmappedGapsSection({
	data,
	unmappedTables,
	unmappedCols,
	unmappedMentions,
}: {
	data: OntologyCoverageData;
	unmappedTables: number;
	unmappedCols: number;
	unmappedMentions: number;
}) {
	if (unmappedTables === 0 && unmappedCols === 0 && unmappedMentions === 0) {
		return null;
	}

	return (
		<div
			data-testid="unmapped-gaps-section"
			style={{
				padding: "12px",
				borderRadius: "6px",
				backgroundColor: "#FFF9F0",
				border: "1px solid #F3D9B1",
				display: "flex",
				flexDirection: "column",
				gap: "8px",
			}}
		>
			<div
				style={{
					fontSize: "12px",
					fontWeight: 600,
					color: "#8A3B07",
					textTransform: "uppercase",
					letterSpacing: "0.06em",
				}}
			>
				Identified Ontology Gaps
			</div>

			{unmappedTables > 0 && (
				<div>
					<div style={{ fontSize: "11.5px", fontWeight: 600, color: "#8A3B07" }}>
						Unmapped Tables ({unmappedTables}):
					</div>
					<ul
						data-testid="unmapped-tables-list"
						style={{
							margin: "4px 0 0 16px",
							padding: 0,
							fontSize: "11.5px",
							fontFamily: EXPLORER_FONTS.mono,
							color: EXPLORER_COLORS.inkPrimary,
						}}
					>
						{data.unmapped_tables?.map((tbl) => (
							<li key={tbl}>{tbl}</li>
						))}
					</ul>
				</div>
			)}

			{unmappedCols > 0 && (
				<div>
					<div style={{ fontSize: "11.5px", fontWeight: 600, color: "#8A3B07" }}>
						Unmapped Columns ({unmappedCols}):
					</div>
					<ul
						data-testid="unmapped-columns-list"
						style={{
							margin: "4px 0 0 16px",
							padding: 0,
							fontSize: "11.5px",
							fontFamily: EXPLORER_FONTS.mono,
							color: EXPLORER_COLORS.inkPrimary,
						}}
					>
						{data.unmapped_columns?.map((col, idx) => (
							<li
								key={
									typeof col === "string"
										? col
										: `${(col as UnmappedColumnItem).table}.${(col as UnmappedColumnItem).column}-${idx}`
								}
							>
								{typeof col === "string"
									? col
									: `${(col as UnmappedColumnItem).table}.${(col as UnmappedColumnItem).column}`}
							</li>
						))}
					</ul>
				</div>
			)}

			{unmappedMentions > 0 && (
				<div>
					<div style={{ fontSize: "11.5px", fontWeight: 600, color: "#8A3B07" }}>
						Unmapped Unstructured Mentions ({unmappedMentions}):
					</div>
					<ul
						data-testid="unmapped-mentions-list"
						style={{
							margin: "4px 0 0 16px",
							padding: 0,
							fontSize: "11.5px",
							color: EXPLORER_COLORS.inkPrimary,
						}}
					>
						{data.unmapped_mentions?.map((m) => (
							<li key={m}>"{m}"</li>
						))}
					</ul>
				</div>
			)}
		</div>
	);
}

export function CoverageDiagnosticsSection({
	diagnostics,
}: {
	diagnostics?: string[] | undefined;
}) {
	if (!diagnostics || diagnostics.length === 0) return null;

	return (
		<div
			data-testid="ontology-coverage-diagnostics"
			style={{
				padding: "10px 12px",
				borderRadius: "6px",
				background: EXPLORER_COLORS.bgPage,
				border: `1px solid ${EXPLORER_COLORS.borderLine}`,
				display: "flex",
				flexDirection: "column",
				gap: "4px",
			}}
		>
			<div
				style={{
					fontSize: "11px",
					fontWeight: 600,
					textTransform: "uppercase",
					letterSpacing: "0.06em",
					color: EXPLORER_COLORS.inkLabel,
				}}
			>
				Diagnostics ({diagnostics.length})
			</div>
			<ul
				style={{
					margin: "4px 0 0 16px",
					padding: 0,
					fontSize: "11.5px",
					fontFamily: EXPLORER_FONTS.mono,
					color: EXPLORER_COLORS.inkBody,
					lineHeight: 1.4,
				}}
			>
				{diagnostics.map((diag, i) => (
					<li key={`cov-diag-${i}-${diag}`}>{diag}</li>
				))}
			</ul>
		</div>
	);
}
