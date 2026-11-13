/**
 * React Flow + elkjs Graph Canvas & Multi-Tier LOD (Task 5D.4, #225)
 * (apps/opendv-web-workbench/src/ontology/components/GraphCanvas.tsx)
 *
 * Implements:
 * - Interactive node-link canvas integrating @xyflow/react
 * - High performance layout using elkjs / layout adapter (< 300 ms for 100+ nodes)
 * - Custom concept nodes with confidence halos and type badges
 * - Relationship edges with link types and cardinality
 * - 3-tier Level of Detail (LOD) zoom rendering (Tier 1: Macro, Tier 2: Meso, Tier 3: Micro)
 * - Canvas controls: Zoom in/out, fit view, auto-layout trigger, minimap toggle, search filter
 */

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
	useEdgesState,
	useNodesState,
} from "@xyflow/react";
import type React from "react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
	CANVAS_FALLBACK_GRID_COLS,
	ELK_LAYER_SPACING,
	ELK_NODE_HEIGHT,
	ELK_NODE_SPACING,
	ELK_NODE_WIDTH,
	GRID_LAYOUT_ORIGIN,
	GRID_LAYOUT_X_STRIDE,
	GRID_LAYOUT_Y_STRIDE,
} from "../constants";
import { useStudioStore } from "../store/studio-store";
import type { ConceptNode, RelationshipEdge } from "../store/types";
import { layoutGraph } from "./graph-layout";

export interface GraphCanvasProps {
	className?: string;
	onNodeClick?: (node: ConceptNode) => void;
	onEdgeClick?: (edge: RelationshipEdge) => void;
	onLayoutComplete?: (durationMs: number) => void;
}

export interface ConceptNodeData extends Record<string, unknown> {
	concept: ConceptNode;
	lodTier: 1 | 2 | 3;
	isSelected: boolean;
	isDimmed: boolean;
}

/**
 * Custom Concept Node Component rendering with 3-Tier Level-of-Detail (LOD).
 */
export const ConceptNodeComponent: React.FC<
	NodeProps<Node<ConceptNodeData>>
> = ({ data }) => {
	const { concept, lodTier, isSelected, isDimmed } = data;

	const haloClass =
		concept.grounding_status === "GROUNDED"
			? "halo-grounded"
			: concept.grounding_status === "LOW_CONFIDENCE"
				? "halo-low-confidence"
				: "halo-ungrounded";

	const dimClass = isDimmed ? "node-dimmed" : "";
	const selClass = isSelected ? "node-selected" : "";

	return (
		<div
			className={`concept-node ${haloClass} ${selClass} ${dimClass} lod-tier-${lodTier}`}
			data-testid={`concept-node-${concept.id}`}
			data-uri={concept.concept_uri}
		>
			<Handle type="target" position={Position.Left} className="flow-handle" />

			{/* Tier 1: Macro / Domain Overview (Compact Chip) */}
			{lodTier === 1 && (
				<div className="lod-tier-1-content">
					<span className="concept-icon" aria-hidden="true">
						◈
					</span>
					<span className="concept-name-compact" title={concept.concept_name}>
						{concept.concept_name}
					</span>
				</div>
			)}

			{/* Tier 2: Meso / Entity Detail (Entity Card) */}
			{lodTier === 2 && (
				<div className="lod-tier-2-content">
					<div className="card-header">
						<span className="archetype-badge">{concept.elm_archetype}</span>
						{concept.target_hub && (
							<span className="hub-badge" title="Data Vault 2.0 Target Hub">
								{concept.target_hub}
							</span>
						)}
					</div>
					<div className="card-title-group">
						<h4 className="concept-title">{concept.concept_name}</h4>
						<span className="uri-subtext">{concept.concept_uri}</span>
					</div>
					<div className="card-stats">
						<span className="stat-pill">{concept.attributes.length} attrs</span>
						<span className="stat-pill">
							{concept.external_mappings.length} mappings
						</span>
						{concept.confidence !== undefined && (
							<span
								className="confidence-pill"
								title={`Grounding confidence: ${(concept.confidence * 100).toFixed(1)}%`}
							>
								{(concept.confidence * 100).toFixed(0)}%
							</span>
						)}
					</div>
				</div>
			)}

			{/* Tier 3: Micro / Attribute & Definition Deep-Dive (Full Card) */}
			{lodTier === 3 && (
				<div className="lod-tier-3-content">
					<div className="card-header">
						<span className="archetype-badge">{concept.elm_archetype}</span>
						<span className="grounding-status-pill">
							{concept.grounding_status}
						</span>
					</div>
					<h4 className="concept-title">{concept.concept_name}</h4>
					<p className="concept-def-snippet">{concept.definition}</p>

					{/* Attributes list */}
					{concept.attributes.length > 0 && (
						<div className="attributes-preview">
							<span className="preview-heading">Attributes:</span>
							<ul className="attr-list">
								{concept.attributes.slice(0, 4).map((a) => (
									<li key={a.attribute_name} className="attr-item">
										<span className="attr-name">{a.attribute_name}</span>
										<span className="attr-type">:{a.data_type}</span>
									</li>
								))}
								{concept.attributes.length > 4 && (
									<li className="attr-more">
										+{concept.attributes.length - 4} more
									</li>
								)}
							</ul>
						</div>
					)}

					{/* External Mappings list */}
					{concept.external_mappings.length > 0 && (
						<div className="mappings-preview">
							<span className="preview-heading">Mappings:</span>
							<div className="mappings-tags">
								{concept.external_mappings.map((m, idx) => {
									const shortTarget =
										m.target_uri
											.split(/[\/#:]/)
											.filter(Boolean)
											.pop() || m.target_uri;
									return (
										<span
											key={`${m.target_uri}-${idx}`}
											className="mapping-tag"
											title={`${m.scheme}: ${m.target_uri}`}
										>
											{m.scheme}: {shortTarget}
										</span>
									);
								})}
							</div>
						</div>
					)}
				</div>
			)}

			<Handle type="source" position={Position.Right} className="flow-handle" />
		</div>
	);
};

const nodeTypes = {
	conceptNode: ConceptNodeComponent,
};

export const GraphCanvas: React.FC<GraphCanvasProps> = ({
	className = "",
	onNodeClick,
	onEdgeClick,
	onLayoutComplete,
}) => {
	const activeSession = useStudioStore((s) => s.activeSession);
	const rawNodes = useStudioStore((s) => s.nodes);
	const rawEdges = useStudioStore((s) => s.edges);
	const selectedNodeId = useStudioStore((s) => s.selectedNodeId);
	const selectedEdgeId = useStudioStore((s) => s.selectedEdgeId);
	const layoutState = useStudioStore((s) => s.layout);
	const selectNode = useStudioStore((s) => s.selectNode);
	const selectEdge = useStudioStore((s) => s.selectEdge);
	const setLodTier = useStudioStore((s) => s.setLodTier);
	const setMinimapVisible = useStudioStore((s) => s.setMinimapVisible);
	const setSearchTerm = useStudioStore((s) => s.setSearchTerm);

	const [isLayoutRunning, setIsLayoutRunning] = useState(false);
	const [lastLayoutDurationMs, setLastLayoutDurationMs] = useState<
		number | null
	>(null);

	const matchesSearch = useCallback((concept: ConceptNode, term: string) => {
		if (!term) return true;
		const clean = term.toLowerCase().trim();
		return (
			concept.concept_name.toLowerCase().includes(clean) ||
			concept.concept_uri.toLowerCase().includes(clean) ||
			concept.attributes.some((a) =>
				a.attribute_name.toLowerCase().includes(clean),
			)
		);
	}, []);

	const uriToNodeId = useMemo(() => {
		const map = new Map<string, string>();
		for (const n of rawNodes) {
			map.set(n.concept_uri, n.id);
			map.set(n.id, n.id);
		}
		return map;
	}, [rawNodes]);

	// Convert raw store nodes into React Flow format
	const initialFlowNodes: Node<ConceptNodeData>[] = useMemo(() => {
		const term = layoutState.searchTerm.toLowerCase().trim();
		return rawNodes.map((n) => {
			const matches = matchesSearch(n, term);

			return {
				id: n.id,
				type: "conceptNode",
				position: { x: n.x ?? 100, y: n.y ?? 100 },
				data: {
					concept: n,
					lodTier: layoutState.lodTier,
					isSelected:
						n.id === selectedNodeId || n.concept_uri === selectedNodeId,
					isDimmed: Boolean(term && !matches),
				},
			};
		});
	}, [
		rawNodes,
		layoutState.lodTier,
		layoutState.searchTerm,
		selectedNodeId,
		matchesSearch,
	]);

	// Convert raw store edges into React Flow format
	const initialFlowEdges: Edge[] = useMemo(() => {
		const term = layoutState.searchTerm.toLowerCase().trim();
		return rawEdges.map((e) => {
			const isSel = e.id === selectedEdgeId;
			const matches =
				!term ||
				e.relationship_name.toLowerCase().includes(term) ||
				e.target_link.toLowerCase().includes(term);

			const sourceId =
				uriToNodeId.get(e.source_concept_uri) || e.source_concept_uri;
			const targetId =
				uriToNodeId.get(e.target_concept_uri) || e.target_concept_uri;

			return {
				id: e.id,
				source: sourceId,
				target: targetId,
				label: `${e.relationship_name} (${e.target_link})${e.cardinality ? ` [${e.cardinality}]` : ""}`,
				animated: isSel,
				className: `graph-edge ${isSel ? "edge-selected" : ""} ${term && !matches ? "edge-dimmed" : ""}`,
				style: {
					stroke: isSel ? "#1D4ED8" : "#64748B",
					strokeWidth: isSel ? 3 : 1.5,
				},
			};
		});
	}, [rawEdges, selectedEdgeId, layoutState.searchTerm, uriToNodeId]);

	const [nodes, setNodes, onNodesChange] = useNodesState(initialFlowNodes);
	const [edges, setEdges, onEdgesChange] = useEdgesState(initialFlowEdges);

	const sessionIdRef = useRef(activeSession?.session_id);
	useEffect(() => {
		sessionIdRef.current = activeSession?.session_id;
	}, [activeSession?.session_id]);
	const layoutGenRef = useRef(0);

	// Sync when initialFlowNodes change, preserving existing layout/dragged coordinates (MAJ-02)
	useEffect(() => {
		setNodes((prevNodes) => {
			const posMap = new Map<string, { x: number; y: number }>();
			for (const pn of prevNodes) {
				posMap.set(pn.id, pn.position);
			}
			return initialFlowNodes.map((fn, idx) => {
				const existingPos = posMap.get(fn.id);
				if (existingPos) {
					return {
						...fn,
						position: existingPos,
					};
				}
				// Stagger newly added unplaced nodes to prevent stacking
				return {
					...fn,
					position: {
						x:
							GRID_LAYOUT_ORIGIN +
							(idx % CANVAS_FALLBACK_GRID_COLS) * GRID_LAYOUT_X_STRIDE,
						y:
							GRID_LAYOUT_ORIGIN +
							Math.floor(idx / CANVAS_FALLBACK_GRID_COLS) *
								GRID_LAYOUT_Y_STRIDE,
					},
				};
			});
		});
	}, [initialFlowNodes, setNodes]);

	useEffect(() => {
		setEdges(initialFlowEdges);
	}, [initialFlowEdges, setEdges]);

	// Auto-layout trigger
	const runAutoLayout = useCallback(async () => {
		if (rawNodes.length === 0) return;
		const targetSessionId = sessionIdRef.current;
		const currentGen = ++layoutGenRef.current;
		setIsLayoutRunning(true);
		try {
			const result = await layoutGraph(rawNodes, rawEdges, {
				direction: "RIGHT",
				nodeWidth: ELK_NODE_WIDTH,
				nodeHeight: ELK_NODE_HEIGHT,
				nodeSpacing: ELK_NODE_SPACING,
				layerSpacing: ELK_LAYER_SPACING,
			});

			if (layoutGenRef.current !== currentGen) {
				return;
			}
			if (sessionIdRef.current !== targetSessionId) {
				return;
			}

			setLastLayoutDurationMs(result.durationMs);

			const term = layoutState.searchTerm.toLowerCase().trim();
			const posMap = new Map<string, { x: number; y: number }>();
			for (const rn of result.nodes) {
				posMap.set(rn.id, { x: rn.x ?? 100, y: rn.y ?? 100 });
			}

			setNodes((prevNodes) => {
				const base = prevNodes.length > 0 ? prevNodes : initialFlowNodes;
				return base.map((pn) => {
					const newPos = posMap.get(pn.id);
					const matches = matchesSearch(pn.data.concept, term);
					return {
						...pn,
						position: newPos ?? pn.position,
						data: {
							...pn.data,
							lodTier: layoutState.lodTier,
							isSelected:
								pn.id === selectedNodeId ||
								pn.data.concept.concept_uri === selectedNodeId,
							isDimmed: Boolean(term && !matches),
						},
					};
				});
			});

			if (onLayoutComplete) {
				try {
					onLayoutComplete(result.durationMs);
				} catch (err) {
					console.warn("onLayoutComplete callback error:", err);
				}
			}
		} catch (err) {
			console.warn("Auto layout failed:", err);
		} finally {
			if (layoutGenRef.current === currentGen) {
				setIsLayoutRunning(false);
			}
		}
	}, [
		rawNodes,
		rawEdges,
		activeSession?.session_id,
		initialFlowNodes,
		layoutState.lodTier,
		layoutState.searchTerm,
		selectedNodeId,
		setNodes,
		onLayoutComplete,
		matchesSearch,
	]);

	const activeSessionId = activeSession?.session_id;
	const prevSessionIdRef = useRef<string | undefined>(activeSessionId);
	const hasAutoLaidOutRef = useRef(false);
	const prevLayoutTriggerCountRef = useRef<number>(
		layoutState.layoutTriggerCount || 0,
	);

	useEffect(() => {
		if (prevSessionIdRef.current !== activeSessionId) {
			prevSessionIdRef.current = activeSessionId;
			hasAutoLaidOutRef.current = false;
			setNodes([]);
		}
		if (!hasAutoLaidOutRef.current && rawNodes.length > 0) {
			hasAutoLaidOutRef.current = true;
			runAutoLayout().catch((err) => {
				console.warn("Auto layout failed:", err);
			});
		}
	}, [rawNodes.length, activeSessionId, runAutoLayout, setNodes]);

	// Subscribe to layoutTriggerCount increments from Header Re-layout button / store triggerRelayout (P14a::MAJ-01)
	useEffect(() => {
		const nextCount = layoutState.layoutTriggerCount || 0;
		if (nextCount !== prevLayoutTriggerCountRef.current) {
			prevLayoutTriggerCountRef.current = nextCount;
			runAutoLayout().catch((err) => {
				console.warn("Triggered relayout failed:", err);
			});
		}
	}, [layoutState.layoutTriggerCount, runAutoLayout]);

	const handleNodeClick = useCallback(
		(_: React.MouseEvent, node: Node) => {
			const conceptData = (node.data as ConceptNodeData)?.concept;
			if (conceptData) {
				selectNode(conceptData.concept_uri);
				if (onNodeClick) {
					onNodeClick(conceptData);
				}
			}
		},
		[selectNode, onNodeClick],
	);

	const handleEdgeClick = useCallback(
		(_: React.MouseEvent, edge: Edge) => {
			const found = rawEdges.find((e) => e.id === edge.id);
			if (found) {
				selectEdge(found.id);
				if (onEdgeClick) {
					onEdgeClick(found);
				}
			}
		},
		[rawEdges, selectEdge, onEdgeClick],
	);

	return (
		<div
			className={`graph-canvas-container ${className}`}
			data-testid="graph-canvas-container"
		>
			{/* Canvas Toolbar Controls */}
			<div className="canvas-toolbar" data-testid="canvas-toolbar">
				{/* Search & Filter */}
				<div className="search-box">
					<input
						type="text"
						className="canvas-search-input"
						placeholder="Search concepts, attributes, links..."
						value={layoutState.searchTerm}
						onChange={(e) => setSearchTerm(e.target.value)}
						data-testid="canvas-search-input"
						aria-label="Filter canvas graph concepts and relationships"
					/>
					{layoutState.searchTerm && (
						<button
							type="button"
							className="btn-clear-search"
							onClick={() => setSearchTerm("")}
							aria-label="Clear search filter"
						>
							✕
						</button>
					)}
				</div>

				{/* 3-Tier LOD Selector */}
				<div
					className="lod-selector-group"
					role="group"
					aria-label="Level of Detail selector"
				>
					<span className="toolbar-label">LOD:</span>
					<button
						type="button"
						className={`lod-btn ${layoutState.lodTier === 1 ? "lod-btn-active" : ""}`}
						onClick={() => setLodTier(1)}
						data-testid="lod-tier-1-btn"
						title="Tier 1: Domain Macro Overview"
					>
						T1 Macro
					</button>
					<button
						type="button"
						className={`lod-btn ${layoutState.lodTier === 2 ? "lod-btn-active" : ""}`}
						onClick={() => setLodTier(2)}
						data-testid="lod-tier-2-btn"
						title="Tier 2: Entity & Relationship Meso View"
					>
						T2 Meso
					</button>
					<button
						type="button"
						className={`lod-btn ${layoutState.lodTier === 3 ? "lod-btn-active" : ""}`}
						onClick={() => setLodTier(3)}
						data-testid="lod-tier-3-btn"
						title="Tier 3: Attribute & Mapping Micro Deep-Dive"
					>
						T3 Micro
					</button>
				</div>

				{/* Action Buttons */}
				<div className="toolbar-actions">
					<button
						type="button"
						className="btn-toolbar"
						onClick={runAutoLayout}
						disabled={isLayoutRunning || rawNodes.length === 0}
						data-testid="auto-layout-btn"
						title="Execute Elk Layered Auto Layout (< 300 ms)"
					>
						{isLayoutRunning ? "Layout..." : "Auto Layout"}
					</button>

					<button
						type="button"
						className={`btn-toolbar ${layoutState.minimapVisible ? "active" : ""}`}
						onClick={() => setMinimapVisible(!layoutState.minimapVisible)}
						data-testid="toggle-minimap-btn"
						title="Toggle Minimap View"
					>
						Minimap
					</button>
				</div>

				{/* Performance duration badge */}
				{lastLayoutDurationMs !== null && (
					<span className="layout-perf-badge" data-testid="layout-perf-badge">
						Layout: {lastLayoutDurationMs.toFixed(1)} ms
					</span>
				)}
			</div>

			{/* Main Canvas Canvas Viewport */}
			<div className="canvas-flow-wrapper" data-testid="canvas-flow-wrapper">
				<ReactFlow
					nodes={nodes}
					edges={edges}
					onNodesChange={onNodesChange}
					onEdgesChange={onEdgesChange}
					onNodeClick={handleNodeClick}
					onEdgeClick={handleEdgeClick}
					nodeTypes={nodeTypes}
					deleteKeyCode={null}
					fitView
				>
					<Background color="var(--dv-border-strong)" gap={24} size={1} />
					<Controls showInteractive={false} />
					{layoutState.minimapVisible && (
						<MiniMap
							nodeStrokeColor="var(--dv-hub-accent)"
							nodeColor="var(--dv-minimap-node-bg)"
							maskColor="var(--dv-minimap-mask-bg)"
							nodeBorderRadius={4}
							ariaLabel="Ontology graph overview minimap"
						/>
					)}
				</ReactFlow>
			</div>
		</div>
	);
};
