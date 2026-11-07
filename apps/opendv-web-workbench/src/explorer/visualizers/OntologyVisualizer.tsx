/**
 * Graph A Ontology Visualizer (`OntologyVisualizer.tsx`)
 * Renders SVG node-link diagram and concept cards.
 */

import type {
	OntologyConceptSpec,
	OntologySvgEdgeSpec,
	OntologySvgNodeSpec,
} from "@opendv/protocol-ts";
import { EXPLORER_FONTS } from "../design-tokens";

export interface OntologySpec {
	concepts: OntologyConceptSpec[];
	nodes: OntologySvgNodeSpec[];
	edges: OntologySvgEdgeSpec[];
}

export interface OntologyVisualizerProps {
	ontology: OntologySpec;
}

export function OntologyVisualizer({ ontology }: OntologyVisualizerProps) {
	const { concepts, nodes, edges } = ontology;

	return (
		<div
			data-testid="viz-ontology"
			style={{ display: "flex", flexDirection: "column", gap: "16px" }}
		>
			<svg
				viewBox="0 0 640 245"
				role="img"
				aria-label="Graph A Ontology Concept Diagram"
				style={{
					width: "100%",
					height: "auto",
					background: "#FAFBFD",
					border: "1px solid #E3E5E8",
					borderRadius: "6px",
				}}
			>
				<title>Graph A Ontology Concept Diagram</title>
				<g stroke="#9CA3AF" strokeWidth="1.2" fill="none">
					{edges.map((edge, idx) => (
						<path
							key={`edge-${idx}-${edge.path}`}
							d={edge.path}
							strokeDasharray="3 2"
						/>
					))}
				</g>
				<g
					fontFamily="IBM Plex Mono, monospace"
					fontSize="9.5"
					fill="#545A66"
				>
					{edges.map((edge, idx) =>
						edge.label ? (
							<text
								key={`edge-lbl-${idx}-${edge.label}`}
								x={edge.lx ?? 0}
								y={edge.ly ?? 0}
								textAnchor={
									(edge.lx ?? 0) > 150 && (edge.ly ?? 0) < 60
										? "middle"
										: undefined
								}
							>
								{edge.label}
							</text>
						) : null,
					)}
				</g>
				<g fontFamily="IBM Plex Mono, monospace">
					{nodes.map((node) => (
						<g key={node.id}>
							<rect
								x={node.x}
								y={node.y}
								width={node.w}
								height={node.h ?? 56}
								rx="5"
								fill="#FFFFFF"
								stroke="#16181D"
								strokeWidth="1.4"
							/>
							<text
								x={node.x + 12}
								y={node.y + 22}
								fontSize="11.5"
								fontWeight="600"
								fill="#16181D"
							>
								{node.label}
							</text>
							<text
								x={node.x + 12}
								y={node.y + 40}
								fontSize="10"
								fill="#545A66"
							>
								{node.sub}
							</text>
						</g>
					))}
				</g>
			</svg>

			<div style={{ display: "flex", flexDirection: "column", gap: "8px" }}>
				{concepts.map((c) => (
					<div
						key={c.name}
						data-testid={`ontology-concept-${c.name}`}
						style={{
							padding: "10px 12px",
							border: "1px solid #E3E5E8",
							borderRadius: "6px",
						}}
					>
						<div
							style={{
								display: "flex",
								alignItems: "center",
								justifyContent: "space-between",
								gap: "8px",
							}}
						>
							<span
								style={{
									fontFamily: EXPLORER_FONTS.mono,
									fontSize: "12.5px",
									fontWeight: 600,
									color: "#16181D",
								}}
							>
								{c.name}
							</span>
							<span
								style={{
									fontFamily: EXPLORER_FONTS.mono,
									fontSize: "10.5px",
									padding: "1px 7px",
									borderRadius: "999px",
									background: "#EEF0F2",
									color: "#3B414C",
								}}
							>
								{c.arch}
							</span>
						</div>
						<div
							style={{
								fontSize: "12px",
								color: "#3B414C",
								marginTop: "4px",
							}}
						>
							{c.def}
						</div>
						<div
							style={{
								fontFamily: EXPLORER_FONTS.mono,
								fontSize: "11px",
								color: "#6B7280",
								marginTop: "4px",
							}}
						>
							{c.keys}
						</div>
					</div>
				))}
			</div>
		</div>
	);
}
