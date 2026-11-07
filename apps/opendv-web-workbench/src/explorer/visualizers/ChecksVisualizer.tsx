/**
 * Axi-DV Verification Checks Visualizer (`ChecksVisualizer.tsx`)
 * Renders checks scorecard with status tags.
 */

import type { StatusTag } from "@opendv/protocol-ts";
import { EXPLORER_FONTS, getStatusTagStyle } from "../design-tokens";

export interface ChecksVisualizerProps {
	checks: [string, string, StatusTag][];
}

export function ChecksVisualizer({ checks }: ChecksVisualizerProps) {
	return (
		<div
			data-testid="viz-checks"
			style={{ display: "flex", flexDirection: "column", gap: "8px" }}
		>
			{checks.map(([id, name, status]) => {
				const tagStyle = getStatusTagStyle(status);
				return (
					<div
						key={id}
						style={{
							display: "flex",
							alignItems: "center",
							justifyContent: "space-between",
							gap: "12px",
							padding: "10px 12px",
							border: "1px solid #E3E5E8",
							borderRadius: "6px",
						}}
					>
						<div
							style={{
								display: "flex",
								alignItems: "center",
								gap: "10px",
								minWidth: 0,
							}}
						>
							<span
								style={{
									fontFamily: EXPLORER_FONTS.mono,
									fontSize: "11.5px",
									fontWeight: 600,
									color: "#16181D",
								}}
							>
								{id}
							</span>
							<span style={{ fontSize: "12.5px", color: "#3B414C" }}>
								{name}
							</span>
						</div>
						<span
							style={{
								fontFamily: EXPLORER_FONTS.mono,
								fontSize: "10.5px",
								fontWeight: 500,
								padding: "2px 8px",
								borderRadius: "999px",
								flexShrink: 0,
								background: tagStyle.bg,
								color: tagStyle.fg,
							}}
						>
							{status}
						</span>
					</div>
				);
			})}
		</div>
	);
}
