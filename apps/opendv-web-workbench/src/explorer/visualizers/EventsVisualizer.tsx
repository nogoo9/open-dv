/**
 * Event Stream Timeline Visualizer (`EventsVisualizer.tsx`)
 * Renders ordered event stream with status tags and stage indicators.
 */

import type { StatusTag } from "@opendv/protocol-ts";
import { EXPLORER_FONTS, getStatusTagStyle } from "../design-tokens";

export interface EventsVisualizerProps {
	events: [string, string, StatusTag][];
}

export function EventsVisualizer({ events }: EventsVisualizerProps) {
	return (
		<div
			data-testid="viz-events"
			style={{
				position: "relative",
				paddingLeft: "18px",
				display: "flex",
				flexDirection: "column",
				gap: "8px",
			}}
		>
			<div
				style={{
					position: "absolute",
					left: "5px",
					top: "8px",
					bottom: "8px",
					width: "1px",
					background: "#D4D7DC",
				}}
			/>
			{events.map(([stage, msg, status], idx) => {
				const tagStyle = getStatusTagStyle(status);
				const dotColor = status === "PASS" ? "#16181D" : "#B45309";
				return (
					<div
						key={`evt-${idx}-${stage}`}
						style={{
							position: "relative",
							padding: "9px 12px",
							border: "1px solid #E3E5E8",
							borderRadius: "6px",
							background: "#FFFFFF",
						}}
					>
						<span
							style={{
								position: "absolute",
								left: "-16px",
								top: "14px",
								width: "7px",
								height: "7px",
								borderRadius: "999px",
								background: dotColor,
							}}
						/>
						<div
							style={{
								display: "flex",
								alignItems: "center",
								justifyContent: "space-between",
								gap: "8px",
							}}
						>
							<div
								style={{
									display: "flex",
									alignItems: "center",
									gap: "8px",
								}}
							>
								<span
									style={{
										fontFamily: EXPLORER_FONTS.mono,
										fontSize: "10.5px",
										color: "#6B7280",
									}}
								>
									#{idx + 1}
								</span>
								<span
									style={{
										fontFamily: EXPLORER_FONTS.mono,
										fontSize: "11px",
										fontWeight: 600,
										color: "#16181D",
									}}
								>
									{stage}
								</span>
							</div>
							<span
								style={{
									fontFamily: EXPLORER_FONTS.mono,
									fontSize: "10px",
									padding: "1px 7px",
									borderRadius: "999px",
									background: tagStyle.bg,
									color: tagStyle.fg,
								}}
							>
								{status}
							</span>
						</div>
						<div
							style={{
								fontSize: "12px",
								color: "#3B414C",
								marginTop: "3px",
							}}
						>
							{msg}
						</div>
					</div>
				);
			})}
		</div>
	);
}
