/**
 * Key-Value Pairs Visualizer (`KeyValueVisualizer.tsx`)
 * Renders structured two-column definition list.
 */

import { EXPLORER_FONTS } from "../design-tokens";

export interface KeyValueVisualizerProps {
	kv: [string, string][];
}

export function KeyValueVisualizer({ kv }: KeyValueVisualizerProps) {
	return (
		<dl
			data-testid="viz-kv"
			style={{
				margin: 0,
				border: "1px solid #E3E5E8",
				borderRadius: "6px",
				overflow: "hidden",
			}}
		>
			{kv.map(([k, v]) => (
				<div
					key={k}
					style={{
						display: "grid",
						gridTemplateColumns: "180px 1fr",
						gap: "12px",
						padding: "9px 12px",
						borderBottom: "1px solid #F2F3F5",
						fontSize: "12px",
					}}
				>
					<dt
						style={{
							fontFamily: EXPLORER_FONTS.mono,
							color: "#545A66",
						}}
					>
						{k}
					</dt>
					<dd
						style={{
							margin: 0,
							fontFamily: EXPLORER_FONTS.mono,
							color: "#16181D",
							fontWeight: 500,
						}}
					>
						{v}
					</dd>
				</div>
			))}
		</dl>
	);
}
