/**
 * Four-Quadrant Uniqueness vs. Entropy Scatter Plot (`FourQuadrantScatterPlot.tsx`)
 * Visualizes empirical column distribution U(C) vs H(C) and isolates surrogate row_seq_id (Delta=1) (VIZ-STEP-02) (`docs/06` §3.3).
 */

import { useState } from "react";

export interface ColumnProfilePoint {
	table: string;
	column: string;
	uniqueness: number; // 0.0 to 1.0
	nonNullRatio: number; // 0.0 to 1.0
	entropy: number; // 0.0 to 3.0
	quadrant: 1 | 2 | 3 | 4;
	stepDelta?: number | undefined;
	classification:
		| "NATURAL_KEY"
		| "SURROGATE_QUARANTINE"
		| "REFERENCE_CODE"
		| "DESCRIPTIVE_PAYLOAD";
	notes: string;
}

export const SAMPLE_PROFILE_POINTS: readonly ColumnProfilePoint[] = [
	// Quadrant I: Natural Business Keys
	{
		table: "investran.src_pe_funds",
		column: "fund_code",
		uniqueness: 1.0,
		nonNullRatio: 1.0,
		entropy: 2.8,
		quadrant: 1,
		classification: "NATURAL_KEY",
		notes: "Primary business key candidate for hub_fund; ISO-standard naming.",
	},
	{
		table: "investran.src_lp_investors",
		column: "lp_id",
		uniqueness: 1.0,
		nonNullRatio: 1.0,
		entropy: 2.5,
		quadrant: 1,
		classification: "NATURAL_KEY",
		notes: "Global master LP key for hub_limited_partner.",
	},
	{
		table: "dealcloud.src_portfolio_companies",
		column: "lei",
		uniqueness: 1.0,
		nonNullRatio: 1.0,
		entropy: 2.7,
		quadrant: 1,
		classification: "NATURAL_KEY",
		notes: "ISO 17442 Global LEI business key for hub_portfolio_company.",
	},
	{
		table: "investran.src_pe_funds",
		column: "isin",
		uniqueness: 1.0,
		nonNullRatio: 1.0,
		entropy: 2.9,
		quadrant: 1,
		classification: "NATURAL_KEY",
		notes: "Alternate security identifier with 100% uniqueness.",
	},
	{
		table: "dms.documents",
		column: "doc_uri",
		uniqueness: 1.0,
		nonNullRatio: 1.0,
		entropy: 2.6,
		quadrant: 1,
		classification: "NATURAL_KEY",
		notes: "Content-addressable URI for hub_document.",
	},

	// Quadrant II: Surrogate Quarantine Zone (U=1.0, Delta=+1)
	{
		table: "investran.src_lp_investors",
		column: "row_seq_id",
		uniqueness: 1.0,
		nonNullRatio: 1.0,
		entropy: 1.38,
		quadrant: 2,
		stepDelta: 1,
		classification: "SURROGATE_QUARANTINE",
		notes:
			"Monotonic integer surrogate (10001, 10002, 10003, 10004) with Delta=+1. Quarantined to DROPPED sink!",
	},

	// Quadrant III: Low-Cardinality Reference Codes
	{
		table: "investran.src_pe_funds",
		column: "strategy",
		uniqueness: 0.33,
		nonNullRatio: 1.0,
		entropy: 1.1,
		quadrant: 3,
		classification: "REFERENCE_CODE",
		notes: "Enumeration code matched to ref_fund_strategy (BUYOUT, CO_INVEST).",
	},
	{
		table: "dealcloud.src_portfolio_companies",
		column: "sector",
		uniqueness: 0.4,
		nonNullRatio: 1.0,
		entropy: 1.2,
		quadrant: 3,
		classification: "REFERENCE_CODE",
		notes: "Matched to ref_industry_sector (B2B_SAAS, INDUSTRIAL_TECH).",
	},

	// Quadrant IV: Descriptive Payload & Context
	{
		table: "investran.src_pe_funds",
		column: "fund_name",
		uniqueness: 0.67,
		nonNullRatio: 1.0,
		entropy: 2.1,
		quadrant: 4,
		classification: "DESCRIPTIVE_PAYLOAD",
		notes: "Descriptive name attribute; routes to sat_fund_investran.",
	},
	{
		table: "investran.src_lp_commitments_and_calls",
		column: "commitment_amount_usd",
		uniqueness: 0.8,
		nonNullRatio: 1.0,
		entropy: 2.3,
		quadrant: 4,
		classification: "DESCRIPTIVE_PAYLOAD",
		notes: "Financial context; routes to sat_lp_commitment_investran.",
	},
	{
		table: "investran.src_lp_commitments_and_calls",
		column: "eur_usd_rate",
		uniqueness: 0.5,
		nonNullRatio: 1.0,
		entropy: 1.9,
		quadrant: 4,
		classification: "DESCRIPTIVE_PAYLOAD",
		notes: "Daily exchange rate payload; merged under Gate 4 YAGNI.",
	},
	{
		table: "investran.src_lp_investors",
		column: "wire_iban",
		uniqueness: 0.8,
		nonNullRatio: 1.0,
		entropy: 2.0,
		quadrant: 4,
		classification: "DESCRIPTIVE_PAYLOAD",
		notes:
			"Confidential wire instruction; isolated into sat_limited_partner_pii.",
	},
];

export interface FourQuadrantScatterPlotProps {
	points?: readonly ColumnProfilePoint[];
	onSelectPoint?: (point: ColumnProfilePoint) => void;
}

export function FourQuadrantScatterPlot({
	points = SAMPLE_PROFILE_POINTS,
	onSelectPoint,
}: FourQuadrantScatterPlotProps) {
	const [selectedColumn, setSelectedColumn] = useState<string>("row_seq_id");

	const defaultPoint = points[0] as ColumnProfilePoint;
	const activePoint =
		points.find((p) => p.column === selectedColumn) ?? defaultPoint;

	// SVG Dimensions
	const width = 480;
	const height = 260;
	const padLeft = 45;
	const padRight = 20;
	const padTop = 25;
	const padBottom = 35;

	const plotWidth = width - padLeft - padRight;
	const plotHeight = height - padTop - padBottom;

	// Scale functions: x: uniqueness [0, 1] -> [padLeft, padLeft + plotWidth]
	const scaleX = (u: number) => padLeft + u * plotWidth;
	// Scale functions: y: entropy [0, 3] -> [padTop + plotHeight, padTop]
	const scaleY = (h: number) => padTop + plotHeight - (h / 3.0) * plotHeight;

	const uThresholdX = scaleX(0.85);
	const hThresholdY = scaleY(1.5);

	return (
		<div
			aria-label="Four-Quadrant Column Scatter Plot"
			data-testid="four-quadrant-scatter-plot"
			style={{
				display: "flex",
				flexDirection: "column",
				gap: "0.75rem",
			}}
		>
			<div
				style={{
					display: "flex",
					justifyContent: "space-between",
					alignItems: "center",
				}}
			>
				<div>
					<strong
						style={{ fontSize: "0.8125rem", color: "var(--dv-text-primary)" }}
					>
						4-Quadrant Scatter: Uniqueness U(C) vs. Shannon Entropy H(C)
					</strong>
					<div
						style={{ fontSize: "0.7rem", color: "var(--dv-text-secondary)" }}
					>
						Detecting natural keys, reference codes, payload, and monotonic
						surrogate traps
					</div>
				</div>
				<span
					style={{
						fontSize: "0.7rem",
						fontWeight: 700,
						color: "var(--dv-gate-reject)",
						backgroundColor: "var(--dv-surface-2)",
						padding: "0.15rem 0.4rem",
						borderRadius: "0.25rem",
						border: "1px solid var(--dv-gate-reject)",
					}}
				>
					Quadrant 2: Surrogate Trap Isolated
				</span>
			</div>

			{/* SVG Scatter Plot */}
			<div
				style={{
					backgroundColor: "var(--dv-surface-2)",
					border: "1px solid var(--dv-border-subtle)",
					borderRadius: "0.375rem",
					padding: "0.5rem",
					overflowX: "auto",
				}}
			>
				<svg
					viewBox={`0 0 ${width} ${height}`}
					style={{ width: "100%", height: "auto", display: "block" }}
					role="img"
					aria-label="Scatter plot showing column uniqueness versus entropy"
				>
					{/* Background Quadrant Highlight for Quadrant II (Surrogate Quarantine) */}
					<rect
						x={uThresholdX}
						y={hThresholdY}
						width={scaleX(1.0) - uThresholdX}
						height={padTop + plotHeight - hThresholdY}
						fill="var(--dv-gate-reject)"
						fillOpacity="0.12"
						stroke="var(--dv-gate-reject)"
						strokeDasharray="3 3"
					/>

					{/* Quadrant Divider Lines */}
					<line
						x1={uThresholdX}
						y1={padTop}
						x2={uThresholdX}
						y2={padTop + plotHeight}
						stroke="var(--dv-border-strong)"
						strokeDasharray="4 4"
					/>
					<line
						x1={padLeft}
						y1={hThresholdY}
						x2={padLeft + plotWidth}
						y2={hThresholdY}
						stroke="var(--dv-border-strong)"
						strokeDasharray="4 4"
					/>

					{/* Axes */}
					<line
						x1={padLeft}
						y1={padTop + plotHeight}
						x2={padLeft + plotWidth}
						y2={padTop + plotHeight}
						stroke="var(--dv-text-secondary)"
					/>
					<line
						x1={padLeft}
						y1={padTop}
						x2={padLeft}
						y2={padTop + plotHeight}
						stroke="var(--dv-text-secondary)"
					/>

					{/* Quadrant Text Labels */}
					<text
						x={uThresholdX + 8}
						y={padTop + 14}
						fill="var(--dv-hub-accent)"
						fontSize="9"
						fontWeight="bold"
					>
						[I: Natural Business Keys]
					</text>
					<text
						x={uThresholdX + 8}
						y={hThresholdY + 16}
						fill="var(--dv-gate-reject)"
						fontSize="9"
						fontWeight="bold"
					>
						[II: SURROGATE TRAP (Δ=1)]
					</text>
					<text
						x={padLeft + 8}
						y={hThresholdY + 16}
						fill="var(--dv-ref-accent)"
						fontSize="9"
						fontWeight="bold"
					>
						[III: Ref / Enum Codes]
					</text>
					<text
						x={padLeft + 8}
						y={padTop + 14}
						fill="var(--dv-sat-accent)"
						fontSize="9"
						fontWeight="bold"
					>
						[IV: Descriptive Payload]
					</text>

					{/* Axis Ticks & Labels */}
					<text
						x={padLeft}
						y={padTop + plotHeight + 16}
						fill="var(--dv-text-muted)"
						fontSize="8"
						textAnchor="middle"
					>
						0.0
					</text>
					<text
						x={uThresholdX}
						y={padTop + plotHeight + 16}
						fill="var(--dv-text-muted)"
						fontSize="8"
						textAnchor="middle"
					>
						0.85
					</text>
					<text
						x={scaleX(1.0)}
						y={padTop + plotHeight + 16}
						fill="var(--dv-text-muted)"
						fontSize="8"
						textAnchor="middle"
					>
						1.0
					</text>
					<text
						x={padLeft + plotWidth / 2}
						y={padTop + plotHeight + 28}
						fill="var(--dv-text-primary)"
						fontSize="10"
						fontWeight="bold"
						textAnchor="middle"
					>
						Uniqueness U(C) →
					</text>

					<text
						x={padLeft - 6}
						y={padTop + plotHeight}
						fill="var(--dv-text-muted)"
						fontSize="8"
						textAnchor="end"
					>
						0.0
					</text>
					<text
						x={padLeft - 6}
						y={hThresholdY + 3}
						fill="var(--dv-text-muted)"
						fontSize="8"
						textAnchor="end"
					>
						1.5
					</text>
					<text
						x={padLeft - 6}
						y={padTop + 6}
						fill="var(--dv-text-muted)"
						fontSize="8"
						textAnchor="end"
					>
						3.0
					</text>

					{/* Plot Points */}
					{points.map((pt) => {
						const cx = scaleX(pt.uniqueness);
						const cy = scaleY(pt.entropy);
						const isSelected = pt.column === selectedColumn;
						const isSurrogate = pt.classification === "SURROGATE_QUARANTINE";

						let color = "var(--dv-hub-accent)";
						if (isSurrogate) color = "var(--dv-gate-reject)";
						else if (pt.quadrant === 3) color = "var(--dv-ref-accent)";
						else if (pt.quadrant === 4) color = "var(--dv-sat-accent)";

						return (
							<g
								key={`${pt.table}-${pt.column}`}
								tabIndex={0}
								data-testid={
									isSurrogate
										? "quadrant-2-surrogate-row_seq_id"
										: `plot-point-${pt.column}`
								}
								onClick={() => {
									setSelectedColumn(pt.column);
									onSelectPoint?.(pt);
								}}
								onKeyDown={(e) => {
									if (e.key === "Enter" || e.key === " ") {
										setSelectedColumn(pt.column);
										onSelectPoint?.(pt);
									}
								}}
								style={{ cursor: "pointer" }}
							>
								{isSurrogate ? (
									/* Square indicator for surrogate */
									<rect
										x={cx - 6}
										y={cy - 6}
										width={12}
										height={12}
										fill={color}
										stroke={isSelected ? "var(--dv-text-primary)" : "#000"}
										strokeWidth={isSelected ? 2 : 1}
									/>
								) : (
									<circle
										cx={cx}
										cy={cy}
										r={isSelected ? 6 : 4.5}
										fill={color}
										stroke={isSelected ? "var(--dv-text-primary)" : "#000"}
										strokeWidth={isSelected ? 2 : 1}
									/>
								)}

								<text
									x={cx + 7}
									y={cy + 3}
									fill={
										isSelected
											? "var(--dv-text-primary)"
											: "var(--dv-text-secondary)"
									}
									fontSize={isSelected ? "9" : "8"}
									fontWeight={isSelected || isSurrogate ? "bold" : "normal"}
								>
									{pt.column}
									{pt.stepDelta ? " (Δ=1)" : ""}
								</text>
							</g>
						);
					})}
				</svg>
			</div>

			{/* Selected Point Inspection Card */}
			<div
				data-testid="selected-point-details"
				style={{
					backgroundColor: "var(--dv-surface-1)",
					border:
						activePoint.classification === "SURROGATE_QUARANTINE"
							? "1.5px solid var(--dv-gate-reject)"
							: "1px solid var(--dv-border-subtle)",
					borderRadius: "0.375rem",
					padding: "0.6rem 0.8rem",
					fontSize: "0.75rem",
				}}
			>
				<div
					style={{
						display: "flex",
						justifyContent: "space-between",
						alignItems: "center",
						marginBottom: "0.25rem",
					}}
				>
					<strong
						style={{ fontSize: "0.8125rem", color: "var(--dv-text-primary)" }}
					>
						{activePoint.table}.{activePoint.column}
					</strong>
					<span
						style={{
							fontWeight: 700,
							fontFamily: "ui-monospace, monospace",
							fontSize: "0.7rem",
							color:
								activePoint.classification === "SURROGATE_QUARANTINE"
									? "var(--dv-gate-reject)"
									: "var(--dv-text-primary)",
						}}
					>
						[{activePoint.classification}]
					</span>
				</div>

				<div
					style={{
						display: "grid",
						gridTemplateColumns: "repeat(auto-fit, minmax(100px, 1fr))",
						gap: "0.5rem",
						margin: "0.4rem 0",
						fontFamily: "ui-monospace, monospace",
						fontSize: "0.7rem",
						color: "var(--dv-text-secondary)",
					}}
				>
					<div>Uniqueness: {activePoint.uniqueness.toFixed(2)}</div>
					<div>Non-Null: {activePoint.nonNullRatio.toFixed(2)}</div>
					<div>Entropy H(C): {activePoint.entropy.toFixed(2)}</div>
					<div>Quadrant: {activePoint.quadrant}</div>
					{activePoint.stepDelta && (
						<div style={{ color: "var(--dv-gate-reject)", fontWeight: 700 }}>
							Step Δ: +{activePoint.stepDelta}
						</div>
					)}
				</div>

				<div style={{ color: "var(--dv-text-secondary)", fontSize: "0.7rem" }}>
					<strong>Empirical Decision: </strong>
					{activePoint.notes}
				</div>
			</div>
		</div>
	);
}
