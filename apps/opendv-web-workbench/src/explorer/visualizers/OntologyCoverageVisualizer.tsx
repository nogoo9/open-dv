/**
 * Ontology Coverage Visualizer (`OntologyCoverageVisualizer.tsx`)
 * Renders the `ontology_coverage` artifact (mapped vs unmapped tables, columns,
 * mentions, coverage ratio, and gap diagnostics) (Phase P7 ONT-03 / IP-6).
 */

import type { Artifact } from "@opendv/protocol-ts";
import { useMemo } from "react";
import { EXPLORER_COLORS, EXPLORER_FONTS } from "../design-tokens";
import {
	CoverageDiagnosticsSection,
	MappedConceptsList,
	UnmappedGapsSection,
} from "./CoverageSections";

export interface MappedConceptItem {
	concept_uri?: string | undefined;
	concept_name?: string | undefined;
	target_hub?: string | undefined;
	mapped_tables?: string[] | undefined;
	mapped_columns?: Array<{ table: string; column: string }> | undefined;
	confidence?: number | undefined;
}

export interface UnmappedColumnItem {
	table: string;
	column: string;
	reason?: string | undefined;
}

export interface OntologyCoverageData {
	artifact_id?: string | undefined;
	wave_id?: string | undefined;
	domain_namespace?: string | undefined;
	graph_a_version?: string | undefined;
	graph_a_sha256?: string | undefined;
	coverage_ratio?: number | undefined;
	ontology_coverage_pct?: number | undefined;
	total_tables?: number | undefined;
	mapped_tables_count?: number | undefined;
	unmapped_tables_count?: number | undefined;
	total_columns?: number | undefined;
	mapped_columns_count?: number | undefined;
	unmapped_columns_count?: number | undefined;
	mapped_concepts?: (string | MappedConceptItem)[] | undefined;
	mappings?:
		| Array<{ source: string; concept: string; target_hub?: string }>
		| undefined;
	mapped_tables?: string[] | undefined;
	unmapped_tables?: string[] | undefined;
	unmapped_columns?: (string | UnmappedColumnItem)[] | undefined;
	unmapped_mentions?: string[] | undefined;
	provisional_uris_found?: string[] | undefined;
	diagnostics?: string[] | undefined;
	detailed_mapped_concepts?: MappedConceptItem[] | undefined;
}

export interface OntologyCoverageVisualizerProps {
	artifact?: Artifact | undefined;
	data?: OntologyCoverageData | undefined;
}

function parseCoveragePayload(
	artifact?: Artifact,
	explicitData?: OntologyCoverageData,
): OntologyCoverageData {
	if (explicitData) {
		return explicitData;
	}
	if (!artifact?.raw) {
		return {};
	}
	try {
		return JSON.parse(artifact.raw) as OntologyCoverageData;
	} catch {
		return { diagnostics: ["Failed to parse raw coverage JSON"] };
	}
}

export function OntologyCoverageVisualizer({
	artifact,
	data: explicitData,
}: OntologyCoverageVisualizerProps) {
	const data = useMemo(
		() => parseCoveragePayload(artifact, explicitData),
		[artifact, explicitData],
	);

	const coveragePct = useMemo(() => {
		if (typeof data.ontology_coverage_pct === "number") {
			return data.ontology_coverage_pct;
		}
		if (typeof data.coverage_ratio === "number") {
			return Math.round(data.coverage_ratio * 1000) / 10;
		}
		const mapped = data.mapped_tables_count ?? data.mapped_tables?.length ?? 0;
		const total =
			data.total_tables ?? mapped + (data.unmapped_tables?.length ?? 0);
		return total > 0 ? Math.round((mapped / total) * 1000) / 10 : 0;
	}, [data]);

	const totalTables =
		data.total_tables ??
		(data.mapped_tables?.length ?? 0) + (data.unmapped_tables?.length ?? 0);
	const mappedTables =
		data.mapped_tables_count ??
		data.mapped_tables?.length ??
		data.mappings?.length ??
		0;
	const unmappedTables =
		data.unmapped_tables_count ?? data.unmapped_tables?.length ?? 0;

	const totalCols = data.total_columns ?? 0;
	const mappedCols = data.mapped_columns_count ?? 0;
	const unmappedCols =
		data.unmapped_columns_count ?? data.unmapped_columns?.length ?? 0;
	const unmappedMentions = data.unmapped_mentions?.length ?? 0;

	const meterColor =
		coveragePct >= 90 ? "#15803D" : coveragePct >= 70 ? "#1D4ED8" : "#B45309";

	return (
		<section
			aria-label="Ontology Coverage Analysis"
			data-testid="ontology-coverage-visualizer"
			style={{
				display: "flex",
				flexDirection: "column",
				gap: "20px",
				padding: "16px",
				backgroundColor: EXPLORER_COLORS.bgSurface,
				borderRadius: "8px",
				fontFamily: EXPLORER_FONTS.sans,
				color: EXPLORER_COLORS.inkPrimary,
			}}
		>
			{/* Top Header & Overview Bar */}
			<div
				style={{
					borderBottom: `1px solid ${EXPLORER_COLORS.borderLine}`,
					paddingBottom: "16px",
				}}
			>
				<div
					style={{
						display: "flex",
						alignItems: "center",
						justifyContent: "space-between",
						flexWrap: "wrap",
						gap: "12px",
					}}
				>
					<div>
						<h3
							data-testid="ontology-coverage-title"
							style={{
								margin: 0,
								fontSize: "16px",
								fontWeight: 600,
								color: EXPLORER_COLORS.inkPrimary,
							}}
						>
							Ontology Grounding & Coverage
						</h3>
						<div
							style={{
								fontSize: "12px",
								color: EXPLORER_COLORS.inkMuted,
								marginTop: "4px",
							}}
						>
							{data.domain_namespace && (
								<span>
									Domain: <strong>{data.domain_namespace}</strong> ·{" "}
								</span>
							)}
							{data.graph_a_version && (
								<span>Graph A v{data.graph_a_version} · </span>
							)}
							{data.graph_a_sha256 && (
								<span
									style={{
										fontFamily: EXPLORER_FONTS.mono,
										fontSize: "11px",
										color: EXPLORER_COLORS.inkLabel,
									}}
								>
									SHA: {data.graph_a_sha256.slice(0, 8)}...
								</span>
							)}
						</div>
					</div>

					<div
						data-testid="coverage-percentage-badge"
						style={{
							display: "flex",
							alignItems: "baseline",
							gap: "4px",
							backgroundColor: EXPLORER_COLORS.bgSubtle,
							padding: "6px 14px",
							borderRadius: "999px",
							border: `1px solid ${EXPLORER_COLORS.borderLine}`,
						}}
					>
						<span
							data-testid="coverage-percentage-value"
							style={{
								fontFamily: EXPLORER_FONTS.mono,
								fontSize: "20px",
								fontWeight: 700,
								color: meterColor,
							}}
						>
							{coveragePct.toFixed(1)}%
						</span>
						<span style={{ fontSize: "11px", color: EXPLORER_COLORS.inkMuted }}>
							coverage
						</span>
					</div>
				</div>

				{/* Visual Progress Meter */}
				<div
					role="progressbar"
					tabIndex={0}
					aria-valuenow={coveragePct}
					aria-valuemin={0}
					aria-valuemax={100}
					aria-label="Ontology Coverage Ratio"
					style={{
						marginTop: "14px",
						height: "8px",
						width: "100%",
						backgroundColor: EXPLORER_COLORS.borderLine,
						borderRadius: "4px",
						overflow: "hidden",
					}}
				>
					<div
						data-testid="coverage-meter-fill"
						style={{
							height: "100%",
							width: `${Math.min(100, Math.max(0, coveragePct))}%`,
							backgroundColor: meterColor,
							borderRadius: "4px",
							transition: "width 0.3s ease",
						}}
					/>
				</div>
			</div>

			{/* KPI Summary Cards */}
			<div
				style={{
					display: "grid",
					gridTemplateColumns: "repeat(auto-fit, minmax(130px, 1fr))",
					gap: "12px",
				}}
			>
				<div
					data-testid="kpi-tables"
					style={{
						padding: "12px",
						borderRadius: "6px",
						background: EXPLORER_COLORS.bgPage,
						border: `1px solid ${EXPLORER_COLORS.borderLine}`,
					}}
				>
					<div
						style={{
							fontSize: "11px",
							color: EXPLORER_COLORS.inkLabel,
							fontWeight: 500,
						}}
					>
						TABLES MAPPED
					</div>
					<div
						style={{
							fontFamily: EXPLORER_FONTS.mono,
							fontSize: "17px",
							fontWeight: 600,
							marginTop: "4px",
							color: unmappedTables > 0 ? "#8A3B07" : "#16181D",
						}}
					>
						{mappedTables}{" "}
						<span style={{ fontSize: "12px", color: EXPLORER_COLORS.inkMuted }}>
							/ {totalTables}
						</span>
					</div>
				</div>

				<div
					data-testid="kpi-columns"
					style={{
						padding: "12px",
						borderRadius: "6px",
						background: EXPLORER_COLORS.bgPage,
						border: `1px solid ${EXPLORER_COLORS.borderLine}`,
					}}
				>
					<div
						style={{
							fontSize: "11px",
							color: EXPLORER_COLORS.inkLabel,
							fontWeight: 500,
						}}
					>
						COLUMNS MAPPED
					</div>
					<div
						style={{
							fontFamily: EXPLORER_FONTS.mono,
							fontSize: "17px",
							fontWeight: 600,
							marginTop: "4px",
							color: unmappedCols > 0 ? "#8A3B07" : "#16181D",
						}}
					>
						{mappedCols}{" "}
						<span style={{ fontSize: "12px", color: EXPLORER_COLORS.inkMuted }}>
							/ {totalCols || mappedCols}
						</span>
					</div>
				</div>

				<div
					data-testid="kpi-unmapped-gaps"
					style={{
						padding: "12px",
						borderRadius: "6px",
						background:
							unmappedTables + unmappedCols + unmappedMentions > 0
								? "#FFF9F0"
								: EXPLORER_COLORS.bgPage,
						border: `1px solid ${unmappedTables + unmappedCols + unmappedMentions > 0 ? "#F3D9B1" : EXPLORER_COLORS.borderLine}`,
					}}
				>
					<div
						style={{
							fontSize: "11px",
							color:
								unmappedTables + unmappedCols + unmappedMentions > 0
									? "#8A3B07"
									: EXPLORER_COLORS.inkLabel,
							fontWeight: 500,
						}}
					>
						UNMAPPED GAPS
					</div>
					<div
						style={{
							fontFamily: EXPLORER_FONTS.mono,
							fontSize: "17px",
							fontWeight: 600,
							marginTop: "4px",
							color:
								unmappedTables + unmappedCols + unmappedMentions > 0
									? "#8A3B07"
									: "#15803D",
						}}
					>
						{unmappedTables + unmappedCols + unmappedMentions}
					</div>
				</div>
			</div>

			{/* Mapped Concepts Section */}
			<MappedConceptsList data={data} />

			{/* Unmapped Gaps Section */}
			<UnmappedGapsSection
				data={data}
				unmappedTables={unmappedTables}
				unmappedCols={unmappedCols}
				unmappedMentions={unmappedMentions}
			/>

			{/* Diagnostics Warnings */}
			<CoverageDiagnosticsSection diagnostics={data.diagnostics} />
		</section>
	);
}
