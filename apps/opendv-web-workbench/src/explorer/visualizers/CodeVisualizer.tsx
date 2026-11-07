/**
 * Syntax Outline & Code Visualizer (`CodeVisualizer.tsx`)
 * Renders structured code outline sidebar and dark code block.
 */

import { EXPLORER_FONTS } from "../design-tokens";

export interface CodeVisualizerProps {
	raw: string;
	outline?: [string, string][] | undefined;
}

export function CodeVisualizer({ raw, outline }: CodeVisualizerProps) {
	return (
		<div
			data-testid="viz-code"
			style={{
				display: "grid",
				gridTemplateColumns: "220px 1fr",
				gap: "12px",
				alignItems: "start",
			}}
		>
			<div
				style={{
					border: "1px solid #E3E5E8",
					borderRadius: "6px",
					overflow: "hidden",
				}}
			>
				<div
					style={{
						padding: "8px 10px",
						background: "#F6F7F8",
						borderBottom: "1px solid #E3E5E8",
						fontSize: "11px",
						fontWeight: 600,
						color: "#545A66",
						textTransform: "uppercase",
						letterSpacing: "0.06em",
					}}
				>
					Outline
				</div>
				{(outline ?? []).map(([k, v]) => (
					<div
						key={k}
						style={{
							padding: "8px 10px",
							borderBottom: "1px solid #F2F3F5",
						}}
					>
						<div
							style={{
								fontFamily: EXPLORER_FONTS.mono,
								fontSize: "11.5px",
								fontWeight: 600,
								color: "#16181D",
							}}
						>
							{k}
						</div>
						<div
							style={{
								fontSize: "11px",
								color: "#545A66",
								marginTop: "2px",
							}}
						>
							{v}
						</div>
					</div>
				))}
			</div>
			<pre
				style={{
					margin: 0,
					padding: "12px 14px",
					background: "#16181D",
					color: "#E6E8EB",
					borderRadius: "6px",
					fontFamily: EXPLORER_FONTS.mono,
					fontSize: "11.5px",
					lineHeight: 1.55,
					overflow: "auto",
				}}
			>
				{raw}
			</pre>
		</div>
	);
}
