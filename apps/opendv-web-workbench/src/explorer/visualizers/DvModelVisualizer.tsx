/**
 * Data Vault 2.0 Topology Visualizer (`DvModelVisualizer.tsx`)
 * Renders SVG topology with Hub, Link, and Satellite representations.
 */

import type { Artifact } from "@opendv/protocol-ts";

export interface DvModelVisualizerProps {
	artifact: Artifact;
}

export function DvModelVisualizer({ artifact }: DvModelVisualizerProps) {
	const dvSpec = artifact.dv;

	return (
		<div
			data-testid="viz-dv"
			style={{ display: "flex", flexDirection: "column", gap: "14px" }}
		>
			<svg
				viewBox="0 0 660 285"
				role="img"
				aria-label="Data Vault 2.0 Topology Diagram"
				style={{
					width: "100%",
					height: "auto",
					background: "#FAFBFD",
					border: "1px solid #E3E5E8",
					borderRadius: "6px",
				}}
			>
				<title>Data Vault 2.0 Topology Diagram</title>
				<g stroke="#9CA3AF" strokeWidth="1.2" fill="none">
					{(dvSpec?.edges ?? []).map((edge, idx) => (
						<line
							key={`dv-edge-${idx}`}
							x1={edge.x1}
							y1={edge.y1}
							x2={edge.x2}
							y2={edge.y2}
							strokeDasharray={edge.dashed ? "3 2" : undefined}
						/>
					))}
				</g>
				<g fontFamily="IBM Plex Mono, monospace">
					{(dvSpec?.nodes ?? []).map((node) => {
						if (node.kind === "hub") {
							return (
								<g key={node.id}>
									<rect
										x={node.x}
										y={node.y}
										width={node.w}
										height={node.h}
										rx="5"
										fill="#1D4ED8"
									/>
									<text
										x={node.x + 12}
										y={node.y + 19}
										fontSize="11"
										fontWeight="600"
										fill="#FFFFFF"
									>
										{node.label}
									</text>
									<text
										x={node.x + 12}
										y={node.y + 35}
										fontSize="9.5"
										fill="#DBEAFE"
									>
										{node.sub}
									</text>
								</g>
							);
						}
						if (node.kind === "link") {
							return (
								<g key={node.id}>
									<rect
										x={node.x}
										y={node.y}
										width={node.w}
										height={node.h}
										rx="18"
										fill="#FFF4E5"
										stroke="#B45309"
										strokeWidth="1.3"
									/>
									<text
										x={node.x + node.w / 2}
										y={node.y + 15}
										textAnchor="middle"
										fontSize="10.5"
										fontWeight="600"
										fill="#8A3B07"
									>
										{node.label}
									</text>
									<text
										x={node.x + node.w / 2}
										y={node.y + 28}
										textAnchor="middle"
										fontSize="9"
										fill="#8A3B07"
									>
										{node.sub}
									</text>
								</g>
							);
						}
						const isPii = node.kind === "sat_pii";
						return (
							<g key={node.id}>
								<rect
									x={node.x}
									y={node.y}
									width={node.w}
									height={node.h}
									rx="4"
									fill="#F2F3F5"
									stroke={isPii ? "#B45309" : "#D4D7DC"}
									strokeDasharray={isPii ? "3 2" : undefined}
								/>
								<text
									x={node.x + 8}
									y={node.y + 14}
									fontSize="9.5"
									fontWeight="600"
									fill={isPii ? "#8A3B07" : "#16181D"}
								>
									{node.label}
								</text>
								<text
									x={node.x + 8}
									y={node.y + 26}
									fontSize="8.5"
									fill={isPii ? "#8A3B07" : "#545A66"}
								>
									{node.sub}
								</text>
							</g>
						);
					})}
				</g>
			</svg>

			<div
				style={{
					display: "flex",
					flexWrap: "wrap",
					gap: "16px",
					fontSize: "11.5px",
					color: "#545A66",
				}}
			>
				<span style={{ display: "inline-flex", alignItems: "center", gap: "6px" }}>
					<span
						style={{
							width: "12px",
							height: "12px",
							borderRadius: "3px",
							background: "#1D4ED8",
						}}
					/>
					Hub (business key)
				</span>
				<span style={{ display: "inline-flex", alignItems: "center", gap: "6px" }}>
					<span
						style={{
							width: "12px",
							height: "12px",
							borderRadius: "999px",
							background: "#FFF4E5",
							border: "1.2px solid #B45309",
						}}
					/>
					Link (relationship)
				</span>
				<span style={{ display: "inline-flex", alignItems: "center", gap: "6px" }}>
					<span
						style={{
							width: "12px",
							height: "12px",
							borderRadius: "3px",
							background: "#F2F3F5",
							border: "1px solid #D4D7DC",
						}}
					/>
					Satellite (attributes)
				</span>
				<span style={{ display: "inline-flex", alignItems: "center", gap: "6px" }}>
					<span
						style={{
							width: "12px",
							height: "12px",
							borderRadius: "3px",
							background: "#F2F3F5",
							border: "1px dashed #B45309",
						}}
					/>
					Satellite (RESTRICTED_PII)
				</span>
			</div>
		</div>
	);
}
