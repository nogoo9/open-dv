/**
 * Tabular Data Visualizer (`TableVisualizer.tsx`)
 * Renders horizontally scrollable table with PII indicator styling.
 */

import { EXPLORER_FONTS } from "../design-tokens";

export interface TableVisualizerProps {
	name?: string | undefined;
	cols: string[];
	rows: (string | number)[][];
	foot?: string | undefined;
}

export function TableVisualizer({ name, cols, rows, foot }: TableVisualizerProps) {
	const gridCols = cols
		.map((col, ci) => {
			let maxLen = col.length;
			for (const row of rows) {
				maxLen = Math.max(maxLen, String(row[ci] ?? "").length);
			}
			return `${Math.max(72, Math.ceil(maxLen * 7.4) + 28)}px`;
		})
		.join(" ");

	return (
		<div
			data-testid="viz-table"
			style={{ display: "flex", flexDirection: "column", gap: "10px" }}
		>
			<div
				role="table"
				aria-label={name}
				style={{
					overflowX: "auto",
					border: "1px solid #E3E5E8",
					borderRadius: "6px",
				}}
			>
				<div
					role="row"
					style={{
						display: "grid",
						gridTemplateColumns: gridCols,
						background: "#F6F7F8",
						borderBottom: "1px solid #E3E5E8",
						fontFamily: EXPLORER_FONTS.mono,
						fontSize: "11px",
						fontWeight: 600,
						color: "#3B414C",
					}}
				>
					{cols.map((c) => (
						<div
							key={c}
							role="columnheader"
							style={{
								padding: "8px 10px",
								borderRight: "1px solid #ECEEF1",
								whiteSpace: "nowrap",
								overflow: "hidden",
								textOverflow: "ellipsis",
							}}
						>
							{c}
						</div>
					))}
				</div>
				{rows.map((row, ri) => (
					<div
						key={`row-${ri}`}
						role="row"
						style={{
							display: "grid",
							gridTemplateColumns: gridCols,
							borderBottom: "1px solid #F2F3F5",
							fontFamily: EXPLORER_FONTS.mono,
							fontSize: "11.5px",
						}}
					>
						{row.map((cell, ci) => (
							<div
								key={`cell-${ri}-${ci}`}
								role="cell"
								style={{
									padding: "7px 10px",
									borderRight: "1px solid #F6F7F8",
									whiteSpace: "nowrap",
									overflow: "hidden",
									textOverflow: "ellipsis",
									color: cell === "‹PII›" ? "#8A3B07" : "#16181D",
								}}
							>
								{cell}
							</div>
						))}
					</div>
				))}
			</div>
			{foot && (
				<div style={{ fontSize: "11.5px", color: "#545A66" }}>
					{foot}
				</div>
			)}
		</div>
	);
}
