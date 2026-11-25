import {
	Background,
	Controls,
	type Edge,
	Handle,
	MiniMap,
	type Node,
	type NodeProps,
	Position,
	ReactFlow,
} from "@xyflow/react";
import ELK, { type ElkNode } from "elkjs/lib/elk-api.js";
import { useEffect, useMemo, useRef, useState } from "react";

export type BipartiteNodeKind =
	| "hub"
	| "link"
	| "sal"
	| "sat"
	| "sat_pii"
	| "sat_ai"
	| "pit"
	| "mart"
	| "ref"
	| "bronze"
	| "staging"
	| "planned"
	| "concept_locked"
	| "concept_a2"
	| "concept_b1"
	| "concept_b2"
	| "concept_default";

export interface BipartiteNodeData extends Record<string, unknown> {
	label: string;
	iri?: string;
	kind: BipartiteNodeKind;
	subtitle?: string;
	badge?: string;
	zone?: "Z0" | "Z1" | "Z2" | "Z3" | "Z4";
	parentHubId?: string;
	collapsedCount?: number;
}

export type BipartiteCanvasNode = Node<BipartiteNodeData, "bipartite">;
export type BipartiteCanvasEdge = Edge;
export type ElkLayoutRunner = (graph: ElkNode) => Promise<ElkNode>;

export function extractIriTerminalLabel(raw: unknown): string {
	const text = typeof raw === "string" ? raw.trim() : "";
	if (!text) return "";
	return text.split(/[:/#]/).filter(Boolean).pop() || text;
}

const NODE_STYLE_MAP: Record<
	BipartiteNodeKind,
	{ bg: string; ink: string; border: string; radius: number }
> = {
	hub: { bg: "#181715", ink: "#faf9f5", border: "#181715", radius: 8 },
	concept_locked: {
		bg: "#181715",
		ink: "#faf9f5",
		border: "#cc785c",
		radius: 8,
	},
	concept_default: {
		bg: "#efe9de",
		ink: "#141413",
		border: "#d5cdc2",
		radius: 8,
	},
	concept_a2: { bg: "#faf9f5", ink: "#8a5112", border: "#e8a55a", radius: 8 },
	concept_b1: { bg: "#eef8f5", ink: "#145349", border: "#5db8a6", radius: 8 },
	concept_b2: { bg: "#fdf2f2", ink: "#8c2323", border: "#c64545", radius: 8 },
	planned: { bg: "#f5f0e8", ink: "#57554f", border: "#d5cdc2", radius: 8 },
	link: { bg: "#e8e0d2", ink: "#141413", border: "#cc785c", radius: 20 },
	sal: { bg: "#eef8f5", ink: "#145349", border: "#5db8a6", radius: 20 },
	sat: { bg: "#faf9f5", ink: "#8a5112", border: "#e8a55a", radius: 8 },
	sat_pii: { bg: "#fdf4f0", ink: "#a9583e", border: "#cc785c", radius: 8 },
	sat_ai: { bg: "#eef8f5", ink: "#145349", border: "#5db8a6", radius: 8 },
	pit: { bg: "#eef8f5", ink: "#145349", border: "#5db8a6", radius: 10 },
	mart: { bg: "#1f1e1b", ink: "#faf9f5", border: "#5db8a6", radius: 8 },
	bronze: { bg: "#f5f0e8", ink: "#3d3d3a", border: "#d5cdc2", radius: 6 },
	ref: { bg: "#efe9de", ink: "#3d3d3a", border: "#d5cdc2", radius: 6 },
	staging: { bg: "#faf9f5", ink: "#252523", border: "#d5cdc2", radius: 6 },
};

function BipartiteNodeView({ data, selected }: NodeProps<BipartiteCanvasNode>) {
	const st = NODE_STYLE_MAP[data.kind];
	const tooltip = data.iri ? `${data.label} (${data.iri})` : data.label;
	const terminal = data.iri ? extractIriTerminalLabel(data.iri) : "";
	return (
		<div
			title={tooltip}
			style={{
				background: st.bg,
				color: st.ink,
				border: `${selected ? 2 : 1.5}px solid ${selected ? "#a9583e" : st.border}`,
				borderRadius: st.radius,
				padding: "7px 11px",
				minWidth: 148,
				maxWidth: 210,
				fontSize: 12,
				overflow: "hidden",
				boxShadow: selected ? "0 0 0 2px rgba(204, 120, 92, 0.25)" : undefined,
			}}
		>
			<Handle
				type="target"
				position={Position.Left}
				style={{ background: st.border, width: 7, height: 7 }}
			/>
			<div
				style={{
					fontWeight: 600,
					overflow: "hidden",
					textOverflow: "ellipsis",
					whiteSpace: "nowrap",
				}}
			>
				{data.label}
			</div>
			{(data.badge || terminal || data.subtitle) && (
				<div
					style={{
						fontSize: 10.5,
						opacity: 0.9,
						overflow: "hidden",
						textOverflow: "ellipsis",
						whiteSpace: "nowrap",
						marginTop: 2,
					}}
				>
					{data.badge ?? data.subtitle ?? terminal}
				</div>
			)}
			<Handle
				type="source"
				position={Position.Right}
				style={{ background: st.border, width: 7, height: 7 }}
			/>
		</div>
	);
}

const NODE_TYPES = { bipartite: BipartiteNodeView };

function fallbackGridLayout(
	nodes: BipartiteCanvasNode[],
): BipartiteCanvasNode[] {
	const zoneOrder = { Z0: 0, Z1: 1, Z2: 2, Z3: 3, Z4: 4 } as const;
	const colCounts = [0, 0, 0, 0, 0];
	return nodes.map((n, idx) => {
		const z = n.data.zone
			? zoneOrder[n.data.zone]
			: n.data.kind === "link"
				? 1
				: 0;
		const row = colCounts[z] ?? idx;
		colCounts[z] = row + 1;
		return {
			...n,
			position: { x: 24 + z * 235, y: 24 + row * 78 },
		};
	});
}

export interface BipartiteGraphCanvasProps {
	nodes: BipartiteCanvasNode[];
	edges: BipartiteCanvasEdge[];
	selectedNodeId?: string | null;
	onSelectNode?: (nodeId: string, data: BipartiteNodeData) => void;
	layoutRunner?: ElkLayoutRunner;
	height?: number;
	ariaLabel?: string;
}

export function BipartiteGraphCanvas({
	nodes,
	edges,
	selectedNodeId,
	onSelectNode,
	layoutRunner,
	height = 460,
	ariaLabel = "Bipartite Schema Graph",
}: BipartiteGraphCanvasProps) {
	const elkRef = useRef<InstanceType<typeof ELK> | null>(null);
	const [laidOutNodes, setLaidOutNodes] = useState<BipartiteCanvasNode[]>(() =>
		fallbackGridLayout(nodes),
	);
	const [layoutError, setLayoutError] = useState<string | null>(null);

	useEffect(() => {
		let cancelled = false;
		setLayoutError(null);
		if (nodes.length === 0) {
			setLaidOutNodes([]);
			return;
		}
		const run = async () => {
			const runner: ElkLayoutRunner | null =
				layoutRunner ??
				(typeof Worker !== "undefined"
					? (graph) => {
							elkRef.current ??= new ELK({
								workerUrl: "/assets/elk-worker.js",
							});
							return elkRef.current.layout(graph);
						}
					: null);
			if (!runner) {
				if (!cancelled) setLaidOutNodes(fallbackGridLayout(nodes));
				return;
			}
			const elkGraph: ElkNode = {
				id: "root",
				layoutOptions: {
					"elk.algorithm": "layered",
					"elk.direction": "RIGHT",
					"elk.spacing.nodeNode": "32",
					"elk.layered.spacing.nodeNodeBetweenLayers": "68",
				},
				children: nodes.map((n) => ({
					id: n.id,
					width: 184,
					height: 58,
				})),
				edges: edges.map((e) => ({
					id: e.id,
					sources: [e.source],
					targets: [e.target],
				})),
			};
			try {
				const out = await runner(elkGraph);
				if (cancelled) return;
				const posMap = new Map<string, { x: number; y: number }>();
				for (const c of out.children ?? []) {
					posMap.set(c.id, { x: c.x ?? 0, y: c.y ?? 0 });
				}
				setLaidOutNodes(
					nodes.map((n, idx) => ({
						...n,
						position: posMap.get(n.id) ?? {
							x: (idx % 4) * 210,
							y: Math.floor(idx / 4) * 80,
						},
					})),
				);
			} catch (err) {
				if (cancelled) return;
				setLayoutError(
					err instanceof Error ? err.message : "ELK layout failed.",
				);
				setLaidOutNodes(fallbackGridLayout(nodes));
			}
		};
		void run();
		return () => {
			cancelled = true;
		};
	}, [nodes, edges, layoutRunner]);

	useEffect(
		() => () => {
			elkRef.current?.terminateWorker();
			elkRef.current = null;
		},
		[],
	);

	const renderedNodes = useMemo(
		() =>
			laidOutNodes.map((n) => ({
				...n,
				selected: selectedNodeId === n.id,
			})),
		[laidOutNodes, selectedNodeId],
	);

	return (
		<section
			className="dag-canvas"
			aria-label={ariaLabel}
			style={{ height, width: "100%", position: "relative" }}
		>
			{layoutError && (
				<output className="banner banner-warn">
					Graph layout fallback active: {layoutError}
				</output>
			)}
			<ReactFlow
				nodes={renderedNodes}
				edges={edges}
				nodeTypes={NODE_TYPES}
				fitView
				nodesDraggable={false}
				nodesConnectable={false}
				onNodeClick={(_, node) => onSelectNode?.(node.id, node.data)}
			>
				<Background color="#d5cdc2" gap={18} size={1} />
				<Controls
					style={{
						backgroundColor: "#181715",
						color: "#faf9f5",
						borderColor: "#33312d",
					}}
				/>
				<MiniMap
					bgColor="#181715"
					maskColor="rgba(24, 23, 21, 0.72)"
					nodeColor="#faf9f5"
				/>
			</ReactFlow>
		</section>
	);
}
