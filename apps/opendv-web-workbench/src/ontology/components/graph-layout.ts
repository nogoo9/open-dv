/**
 * Graph Layout Adapter (Task 5D.4, #225)
 * (apps/opendv-web-workbench/src/ontology/components/graph-layout.ts)
 *
 * Implements high-performance layered graph layout (< 300 ms for 100+ nodes)
 * using elkjs with deterministic hierarchical fallback.
 */

import ELK from "elkjs/lib/elk.bundled.js";
import type { ConceptNode, RelationshipEdge } from "../store/types";

export interface LayoutOptions {
	direction?: "RIGHT" | "DOWN";
	nodeSpacing?: number;
	layerSpacing?: number;
	nodeWidth?: number;
	nodeHeight?: number;
	timeoutMs?: number;
	elkInstance?: { layout: (graph: unknown) => Promise<unknown> };
}

export interface LayoutResult {
	nodes: ConceptNode[];
	edges: RelationshipEdge[];
	width: number;
	height: number;
	durationMs: number;
}

let elkInstance: InstanceType<typeof ELK> | null = null;

function getElk(): InstanceType<typeof ELK> {
	if (!elkInstance) {
		elkInstance = new ELK();
	}
	return elkInstance;
}

/**
 * Fast deterministic layered layout fallback algorithm.
 * Guarantees < 20 ms execution even for 200+ nodes in headless or restricted environments.
 */
export function computeFallbackLayeredLayout(
	nodes: ConceptNode[],
	edges: RelationshipEdge[],
	options: LayoutOptions = {},
): LayoutResult {
	const start = performance.now();
	const nodeWidth = options.nodeWidth ?? 240;
	const nodeHeight = options.nodeHeight ?? 140;
	const nodeSpacing = options.nodeSpacing ?? 60;
	const layerSpacing = options.layerSpacing ?? 100;
	const direction = options.direction ?? "RIGHT";

	const uriToId = new Map<string, string>();
	for (const n of nodes) {
		uriToId.set(n.concept_uri, n.id);
		uriToId.set(n.id, n.id);
	}

	// 1. Build adjacency
	const inDegree: Record<string, number> = {};
	const adj: Record<string, string[]> = {};
	for (const n of nodes) {
		inDegree[n.id] = 0;
		adj[n.id] = [];
	}
	for (const e of edges) {
		const srcId = uriToId.get(e.source_concept_uri);
		const tgtId = uriToId.get(e.target_concept_uri);
		if (!srcId || !tgtId) {
			continue;
		}
		if (adj[srcId]) {
			adj[srcId]?.push(tgtId);
		}
		if (inDegree[tgtId] !== undefined) {
			inDegree[tgtId] =
				(inDegree[tgtId] || 0) + 1;
		}
	}

	// 2. Assign layers using BFS / topological ranking
	const layers: string[][] = [];
	const visited = new Set<string>();
	let currentLayer = nodes
		.filter((n) => (inDegree[n.id] || 0) === 0)
		.map((n) => n.id);

	if (currentLayer.length === 0 && nodes.length > 0) {
		const firstNode = nodes[0];
		if (firstNode) {
			currentLayer = [firstNode.id];
		}
	}

	while (currentLayer.length > 0) {
		layers.push(currentLayer);
		for (const id of currentLayer) {
			visited.add(id);
		}
		const nextLayer: string[] = [];
		const nextLayerSet = new Set<string>();
		for (const id of currentLayer) {
			const neighbors = adj[id] || [];
			for (const nb of neighbors) {
				if (!visited.has(nb) && !nextLayerSet.has(nb)) {
					nextLayerSet.add(nb);
					nextLayer.push(nb);
				}
			}
		}
		currentLayer = nextLayer;
	}

	// Add any unvisited components
	const unvisited = nodes.filter((n) => !visited.has(n.id)).map((n) => n.id);
	if (unvisited.length > 0) {
		layers.push(unvisited);
	}

	// 3. Position nodes by layer
	const positions: Record<string, { x: number; y: number }> = {};
	let maxMain = 0;
	let maxCross = 0;

	for (let layerIdx = 0; layerIdx < layers.length; layerIdx++) {
		const layerNodes = layers[layerIdx] || [];
		const layerCrossTotal =
			layerNodes.length * nodeHeight + (layerNodes.length - 1) * nodeSpacing;
		maxCross = Math.max(maxCross, layerCrossTotal);

		for (let rowIdx = 0; rowIdx < layerNodes.length; rowIdx++) {
			const nodeId = layerNodes[rowIdx];
			if (!nodeId) continue;
			if (direction === "RIGHT") {
				const x = 50 + layerIdx * (nodeWidth + layerSpacing);
				const y = 50 + rowIdx * (nodeHeight + nodeSpacing);
				positions[nodeId] = { x, y };
				maxMain = Math.max(maxMain, x + nodeWidth);
			} else {
				const x = 50 + rowIdx * (nodeWidth + nodeSpacing);
				const y = 50 + layerIdx * (nodeHeight + layerSpacing);
				positions[nodeId] = { x, y };
				maxMain = Math.max(maxMain, y + nodeHeight);
			}
		}
	}

	const positionedNodes = nodes.map((n) => {
		const pos = positions[n.id] || { x: 50, y: 50 };
		return {
			...n,
			x: pos.x,
			y: pos.y,
		};
	});
	const validNodeIds = new Set(nodes.map((n) => n.id));
	const validEdges = edges.filter((e) => {
		const srcId = uriToId.get(e.source_concept_uri);
		const tgtId = uriToId.get(e.target_concept_uri);
		return Boolean(
			srcId && tgtId && validNodeIds.has(srcId) && validNodeIds.has(tgtId),
		);
	});

	const durationMs = performance.now() - start;
	return {
		nodes: positionedNodes,
		edges: validEdges,
		width: direction === "RIGHT" ? maxMain + 50 : maxCross + 50,
		height: direction === "RIGHT" ? maxCross + 50 : maxMain + 50,
		durationMs,
	};
}

/**
 * Execute graph layout using elkjs with fallback.
 * Best-effort layout budget: falls back to deterministic layered layout on slow environments or layout timeout.
 */
export async function layoutGraph(
	nodes: ConceptNode[],
	edges: RelationshipEdge[],
	options: LayoutOptions = {},
): Promise<LayoutResult> {
	if (nodes.length === 0) {
		return {
			nodes: [],
			edges: [],
			width: 0,
			height: 0,
			durationMs: 0,
		};
	}

	const start = performance.now();
	const nodeWidth = options.nodeWidth ?? 240;
	const nodeHeight = options.nodeHeight ?? 140;

	try {
		const uriToId = new Map<string, string>();
		for (const n of nodes) {
			uriToId.set(n.concept_uri, n.id);
			uriToId.set(n.id, n.id);
		}

		const knownNodeIds = new Set<string>();
		for (const n of nodes) {
			knownNodeIds.add(n.id);
			knownNodeIds.add(n.concept_uri);
		}

		const validEdges = edges.filter(
			(e) =>
				knownNodeIds.has(e.source_concept_uri) &&
				knownNodeIds.has(e.target_concept_uri),
		);

		const elk = (options.elkInstance as InstanceType<typeof ELK>) ?? getElk();
		const elkGraph = {
			id: "root",
			layoutOptions: {
				"elk.algorithm": "layered",
				"elk.direction": options.direction ?? "RIGHT",
				"elk.spacing.nodeNode": String(options.nodeSpacing ?? 60),
				"elk.layered.spacing.nodeNodeBetweenLayers": String(
					options.layerSpacing ?? 100,
				),
			},
			children: nodes.map((n) => ({
				id: n.id,
				width: nodeWidth,
				height: nodeHeight,
			})),
			edges: validEdges.map((e) => ({
				id: e.id,
				sources: [uriToId.get(e.source_concept_uri) || e.source_concept_uri],
				targets: [uriToId.get(e.target_concept_uri) || e.target_concept_uri],
			})),
		};

		const timeoutMs = options.timeoutMs ?? 250;
		let timeoutHandle: ReturnType<typeof setTimeout> | undefined;
		const timeoutPromise = new Promise<never>((_, reject) => {
			timeoutHandle = setTimeout(
				() => reject(new Error(`ELK layout timed out after ${timeoutMs}ms`)),
				timeoutMs,
			);
		});
		timeoutPromise.catch(() => {});

		let res: Awaited<ReturnType<typeof elk.layout>>;
		try {
			const elkLayoutPromise = elk.layout(elkGraph);
			// Attach catch to prevent unhandled rejection if timeout wins race (MIN-05)
			elkLayoutPromise.catch(() => {});
			res = await Promise.race([elkLayoutPromise, timeoutPromise]);
		} finally {
			if (timeoutHandle !== undefined) {
				clearTimeout(timeoutHandle);
			}
		}
		const durationMs = performance.now() - start;

		const posMap = new Map<string, { x: number; y: number }>();
		if (res.children) {
			for (const child of res.children) {
				posMap.set(child.id, { x: child.x ?? 0, y: child.y ?? 0 });
			}
		}

		const positionedNodes = nodes.map((n) => {
			const pos = posMap.get(n.id);
			return {
				...n,
				x: pos ? pos.x + 50 : (n.x ?? 50),
				y: pos ? pos.y + 50 : (n.y ?? 50),
			};
		});

		const layoutResult = res as unknown as { width?: number; height?: number };
		return {
			nodes: positionedNodes,
			edges: validEdges,
			width: (layoutResult.width ?? 800) + 100,
			height: (layoutResult.height ?? 600) + 100,
			durationMs,
		};
	} catch (err) {
		console.warn("ELK layout failed, falling back to deterministic layout:", err);
		const fallback = computeFallbackLayeredLayout(nodes, edges, options);
		return {
			...fallback,
			durationMs: performance.now() - start,
		};
	}
}
