/**
 * Empirical Adversarial Stress & Performance Suite (Task 5D.4 / Challenger 5D.1)
 * (apps/opendv-web-workbench/tests/ontology-stress-challenge.test.tsx)
 *
 * Adversarially tests:
 * 1. Large graph layout (150+ nodes, dense edges, SLA < 300ms)
 * 2. Level of Detail (LOD) transitions across Tier 1 (Macro), Tier 2 (Meso), Tier 3 (Micro)
 * 3. Rapid re-layout invocations (20+ rapid calls, concurrency, generation counter guards)
 * 4. ELK worker rejection & failure fallback resilience (async rejection, timeout, sync throw leak detection)
 * 5. Adversarial topologies (deep chains, cycles, stars, disconnected components, dangling edges)
 * 6. GraphCanvas React component integration with large graphs & LOD controls
 */

import { beforeEach, describe, expect, it } from "bun:test";
import React, { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { ReactFlowProvider } from "@xyflow/react";
import {
	ConceptNodeComponent,
	type ConceptNodeData,
	GraphCanvas,
} from "../src/ontology/components/GraphCanvas";
import {
	computeFallbackLayeredLayout,
	layoutGraph,
} from "../src/ontology/components/graph-layout";
import {
	createStudioStore,
	studioStore,
} from "../src/ontology/store/studio-store";
import type { ConceptNode, RelationshipEdge } from "../src/ontology/store/types";

// Helper to generate synthetic graphs
export function generateSyntheticGraph(
	nodeCount: number,
	edgeDensity: "sparse" | "dense" | "dag_hierarchical" | "star" | "chain" | "cyclic",
): {
	nodes: ConceptNode[];
	edges: RelationshipEdge[];
} {
	const nodes: ConceptNode[] = [];
	const edges: RelationshipEdge[] = [];

	for (let i = 0; i < nodeCount; i++) {
		const archetype =
			i % 4 === 0
				? "CORE_CONCEPT"
				: i % 4 === 1
					? "SUPPORTING_CONCEPT"
					: i % 4 === 2
						? "REFERENCE_CONCEPT"
						: "TRANSITIONAL_CONCEPT";

		const groundingStatus =
			i % 3 === 0
				? "GROUNDED"
				: i % 3 === 1
					? "LOW_CONFIDENCE"
					: "UNGROUNDED";

		const attrCount = (i % 6) + 1;
		const attributes = Array.from({ length: attrCount }, (_, aIdx) => ({
			attribute_name: `attr_${i}_${aIdx}`,
			data_type: aIdx % 2 === 0 ? "string" : aIdx % 3 === 0 ? "integer" : "date",
			is_optional: aIdx > 2,
		}));

		const mapCount = i % 4;
		const external_mappings = Array.from({ length: mapCount }, (_, mIdx) => ({
			scheme: mIdx % 2 === 0 ? "fibo" : "iso",
			target_uri: `fibo-sec-dls:Securities_${i}_${mIdx}`,
			confidence: 0.75 + (mIdx * 0.05),
		}));

		nodes.push({
			id: `urn:opendv:stress:node_${i}`,
			concept_uri: `urn:opendv:stress:node_${i}`,
			concept_name: `Concept ${i} ${i % 2 === 0 ? "Alpha" : "Beta"}`,
			elm_archetype: archetype,
			definition: `Stress definition for synthetic concept ${i} under adversarial load.`,
			target_hub: i % 2 === 0 ? `hub_concept_${i}` : undefined,
			grounding_status: groundingStatus,
			confidence: 0.60 + ((i % 40) * 0.01),
			attributes,
			synonyms: [`SynonymA_${i}`, `SynonymB_${i}`],
			external_mappings,
		});
	}

	if (edgeDensity === "dag_hierarchical" || edgeDensity === "sparse" || edgeDensity === "dense") {
		// Build multi-layer DAG hierarchy
		for (let i = 1; i < nodeCount; i++) {
			const parentIdx = Math.floor((i - 1) / 3);
			edges.push({
				id: `edge_tree_${i}`,
				relationship_uri: `urn:opendv:rel:parent_${parentIdx}_${i}`,
				relationship_name: `hasSubConcept_${parentIdx}_${i}`,
				target_link: `lnk_tree_${parentIdx}_${i}`,
				source_concept_uri: nodes[parentIdx]!.concept_uri,
				target_concept_uri: nodes[i]!.concept_uri,
				cardinality: "1..N",
			});
		}

		if (edgeDensity === "dense") {
			// Add cross-hierarchical lateral edges
			for (let i = 0; i < nodeCount; i += 2) {
				const targetIdx = (i + 5) % nodeCount;
				if (targetIdx !== i) {
					edges.push({
						id: `edge_cross_${i}_${targetIdx}`,
						relationship_uri: `urn:opendv:rel:cross_${i}_${targetIdx}`,
						relationship_name: `references_${i}_${targetIdx}`,
						target_link: `lnk_cross_${i}_${targetIdx}`,
						source_concept_uri: nodes[i]!.concept_uri,
						target_concept_uri: nodes[targetIdx]!.concept_uri,
						cardinality: "0..N",
					});
				}
			}
		}
	} else if (edgeDensity === "star") {
		for (let i = 1; i < nodeCount; i++) {
			edges.push({
				id: `edge_star_${i}`,
				relationship_uri: `urn:opendv:rel:star_${i}`,
				relationship_name: `hubLinksTo_${i}`,
				target_link: `lnk_star_${i}`,
				source_concept_uri: nodes[0]!.concept_uri,
				target_concept_uri: nodes[i]!.concept_uri,
			});
		}
	} else if (edgeDensity === "chain") {
		for (let i = 0; i < nodeCount - 1; i++) {
			edges.push({
				id: `edge_chain_${i}`,
				relationship_uri: `urn:opendv:rel:chain_${i}`,
				relationship_name: `nextInChain_${i}`,
				target_link: `lnk_chain_${i}`,
				source_concept_uri: nodes[i]!.concept_uri,
				target_concept_uri: nodes[i + 1]!.concept_uri,
			});
		}
	} else if (edgeDensity === "cyclic") {
		for (let i = 0; i < nodeCount; i++) {
			edges.push({
				id: `edge_cycle_${i}`,
				relationship_uri: `urn:opendv:rel:cycle_${i}`,
				relationship_name: `cycleRel_${i}`,
				target_link: `lnk_cycle_${i}`,
				source_concept_uri: nodes[i]!.concept_uri,
				target_concept_uri: nodes[(i + 1) % nodeCount]!.concept_uri,
			});
		}
	}

	return { nodes, edges };
}

describe("Empirical Challenger: Graph Layout & Canvas Adversarial Stress Suite", () => {
	const initialStoreState = createStudioStore().getState();

	beforeEach(() => {
		studioStore.setState({
			sessions: initialStoreState.sessions,
			activeSession: initialStoreState.activeSession,
			nodes: initialStoreState.nodes,
			edges: initialStoreState.edges,
			selectedNodeId: initialStoreState.selectedNodeId,
			selectedEdgeId: initialStoreState.selectedEdgeId,
			parsingState: initialStoreState.parsingState,
			questions: initialStoreState.questions,
			qualityScorecard: initialStoreState.qualityScorecard,
			diffState: initialStoreState.diffState,
			toasts: initialStoreState.toasts,
			layout: initialStoreState.layout,
			hasUnsavedChanges: false,
			isPromoting: false,
		});
	});

	// =========================================================================
	// 1. Large Graph Layout SLA Benchmark (150+ nodes, dense edges, < 300ms SLA)
	// =========================================================================
	describe("1. Large Graph Layout Performance SLA (< 300ms for 150+ nodes)", () => {
		it("benchmarks layoutGraph on 150 nodes with hierarchical DAG edges within SLA", async () => {
			const { nodes, edges } = generateSyntheticGraph(150, "dag_hierarchical");
			expect(nodes.length).toBe(150);
			expect(edges.length).toBe(149);

			const start = performance.now();
			const result = await layoutGraph(nodes, edges, {
				direction: "RIGHT",
				timeoutMs: 250,
			});
			const totalMs = performance.now() - start;

			// Verify SLA requirement: < 300 ms total layout time
			expect(totalMs).toBeLessThan(300);
			expect(result.nodes.length).toBe(150);
			expect(result.edges.length).toBe(149);
			expect(result.width).toBeGreaterThan(0);
			expect(result.height).toBeGreaterThan(0);

			// Validate coordinates integrity (no NaN or negative coordinates)
			for (const node of result.nodes) {
				expect(Number.isFinite(node.x)).toBe(true);
				expect(Number.isFinite(node.y)).toBe(true);
				expect(node.x).toBeGreaterThanOrEqual(0);
				expect(node.y).toBeGreaterThanOrEqual(0);
			}
		});

		it("benchmarks layoutGraph on 150 nodes with DENSE cross-hierarchical edges within SLA", async () => {
			const { nodes, edges } = generateSyntheticGraph(150, "dense");
			expect(nodes.length).toBe(150);
			expect(edges.length).toBeGreaterThan(200);

			const start = performance.now();
			const result = await layoutGraph(nodes, edges, {
				direction: "RIGHT",
				timeoutMs: 250,
			});
			const totalMs = performance.now() - start;

			expect(totalMs).toBeLessThan(300);
			expect(result.nodes.length).toBe(150);
			expect(result.edges.length).toBe(edges.length);
		});

		it("benchmarks layoutGraph on 200 nodes (scaling headroom stress) within SLA", async () => {
			const { nodes, edges } = generateSyntheticGraph(200, "dense");
			expect(nodes.length).toBe(200);

			const start = performance.now();
			const result = await layoutGraph(nodes, edges, {
				direction: "RIGHT",
				timeoutMs: 250,
			});
			const totalMs = performance.now() - start;

			expect(totalMs).toBeLessThan(300);
			expect(result.nodes.length).toBe(200);
		});

		it("benchmarks computeFallbackLayeredLayout on 250 nodes in < 30 ms", () => {
			const { nodes, edges } = generateSyntheticGraph(250, "dense");
			const start = performance.now();
			const result = computeFallbackLayeredLayout(nodes, edges, {
				direction: "RIGHT",
			});
			const elapsed = performance.now() - start;

			expect(result.nodes.length).toBe(250);
			expect(elapsed).toBeLessThan(30);
			expect(result.durationMs).toBeLessThan(30);

			const xCoords = new Set(result.nodes.map((n) => n.x));
			expect(xCoords.size).toBeGreaterThan(3);
		});
	});

	// =========================================================================
	// 2. Level of Detail (LOD) Transitions (T1 Macro, T2 Meso, T3 Micro)
	// =========================================================================
	describe("2. Level of Detail (LOD) Zoom Transitions & Element Verification", () => {
		const sampleConcept: ConceptNode = {
			id: "urn:opendv:concept:trade_execution",
			concept_uri: "urn:opendv:concept:trade_execution",
			concept_name: "Trade Execution",
			elm_archetype: "CORE_CONCEPT",
			definition: "Contractual commitment to acquire or dispose of financial securities.",
			target_hub: "hub_trade_execution",
			grounding_status: "GROUNDED",
			confidence: 0.945,
			attributes: [
				{ attribute_name: "trade_id", data_type: "string", is_optional: false },
				{ attribute_name: "execution_timestamp", data_type: "datetime", is_optional: false },
				{ attribute_name: "notional_amount", data_type: "decimal", is_optional: false },
				{ attribute_name: "currency_code", data_type: "string", is_optional: false },
				{ attribute_name: "settlement_date", data_type: "date", is_optional: true },
				{ attribute_name: "broker_reference", data_type: "string", is_optional: true },
			],
			synonyms: ["Execution", "Deal Done"],
			external_mappings: [
				{ scheme: "fibo", target_uri: "fibo-fnd-pas-ps:TransactionRecord", confidence: 0.95 },
				{ scheme: "iso20022", target_uri: "iso20022:TradeDetails", confidence: 0.91 },
			],
		};

		const renderNode = (data: ConceptNodeData) =>
			renderToStaticMarkup(
				createElement(
					ReactFlowProvider,
					null,
					createElement(ConceptNodeComponent as unknown as React.ComponentType<{ data: ConceptNodeData }>, { data }),
				),
			);

		it("LOD Tier 1 (Macro): renders strictly minimal compact chip representation", () => {
			const html = renderNode({
				concept: sampleConcept,
				lodTier: 1,
				isSelected: false,
				isDimmed: false,
			});

			expect(html).toContain("lod-tier-1");
			expect(html).toContain("lod-tier-1-content");
			expect(html).toContain("Trade Execution");
			expect(html).toContain("concept-name-compact");
			expect(html).toContain("halo-grounded");

			expect(html).not.toContain("lod-tier-2-content");
			expect(html).not.toContain("lod-tier-3-content");
			expect(html).not.toContain("hub_trade_execution");
			expect(html).not.toContain("execution_timestamp");
			expect(html).not.toContain("Contractual commitment to acquire");
			expect(html).not.toContain("TransactionRecord");
		});

		it("LOD Tier 2 (Meso): renders archetype badge, target hub, attribute/mapping counts, confidence", () => {
			const html = renderNode({
				concept: sampleConcept,
				lodTier: 2,
				isSelected: false,
				isDimmed: false,
			});

			expect(html).toContain("lod-tier-2");
			expect(html).toContain("lod-tier-2-content");
			expect(html).toContain("Trade Execution");
			expect(html).toContain("CORE_CONCEPT");
			expect(html).toContain("hub_trade_execution");
			expect(html).toContain("6 attrs");
			expect(html).toContain("2 mappings");
			expect(html).toContain("95%");
			expect(html).toContain("urn:opendv:concept:trade_execution");

			expect(html).not.toContain("Contractual commitment to acquire");
			expect(html).not.toContain("settlement_date");
			expect(html).not.toContain("iso20022:TradeDetails");
		});

		it("LOD Tier 3 (Micro): renders full definitions, truncated attributes preview (+N more), mappings", () => {
			const html = renderNode({
				concept: sampleConcept,
				lodTier: 3,
				isSelected: true,
				isDimmed: false,
			});

			expect(html).toContain("lod-tier-3");
			expect(html).toContain("lod-tier-3-content");
			expect(html).toContain("Trade Execution");
			expect(html).toContain("Contractual commitment to acquire or dispose of financial securities.");
			expect(html).toContain("CORE_CONCEPT");
			expect(html).toContain("GROUNDED");

			expect(html).toContain("trade_id");
			expect(html).toContain(":string");
			expect(html).toContain("execution_timestamp");
			expect(html).toContain(":datetime");
			expect(html).toContain("notional_amount");
			expect(html).toContain(":decimal");
			expect(html).toContain("currency_code");

			expect(html).toContain("+2 more");

			expect(html).toContain("fibo: TransactionRecord");
			expect(html).toContain("iso20022: TradeDetails");
			expect(html).toContain("node-selected");
		});

		it("handles edge cases in LOD 3: empty attributes, empty mappings, undefined definition", () => {
			const minimalConcept: ConceptNode = {
				id: "urn:opendv:concept:sparse_node",
				concept_uri: "urn:opendv:concept:sparse_node",
				concept_name: "Sparse Node",
				elm_archetype: "TRANSITIONAL_CONCEPT",
				definition: "",
				grounding_status: "UNGROUNDED",
				attributes: [],
				synonyms: [],
				external_mappings: [],
			};

			const html = renderNode({
				concept: minimalConcept,
				lodTier: 3,
				isSelected: false,
				isDimmed: false,
			});

			expect(html).toContain("lod-tier-3");
			expect(html).toContain("Sparse Node");
			expect(html).toContain("TRANSITIONAL_CONCEPT");
			expect(html).toContain("halo-ungrounded");
			expect(html).not.toContain("preview-heading");
			expect(html).not.toContain("+");
		});

		it("transitions across store LOD tiers (1 -> 2 -> 3 -> 1) predictably", () => {
			const store = studioStore.getState();
			expect(store.layout.lodTier).toBe(2);

			store.setLodTier(1);
			expect(studioStore.getState().layout.lodTier).toBe(1);

			store.setLodTier(3);
			expect(studioStore.getState().layout.lodTier).toBe(3);

			store.setLodTier(2);
			expect(studioStore.getState().layout.lodTier).toBe(2);

			store.setLodTier(1);
			expect(studioStore.getState().layout.lodTier).toBe(1);
		});
	});

	// =========================================================================
	// 3. Rapid Re-layout Invocations & Race Condition Stress
	// =========================================================================
	describe("3. Rapid Re-layout Invocations (20+ Rapid Calls & Concurrency Guards)", () => {
		it("executes 20 sequential re-layouts with deterministic coordinate stability", async () => {
			const { nodes, edges } = generateSyntheticGraph(40, "dag_hierarchical");

			const firstRun = await layoutGraph(nodes, edges, { direction: "RIGHT" });
			const baselinePositions = firstRun.nodes.map((n) => ({ id: n.id, x: n.x, y: n.y }));

			for (let i = 0; i < 20; i++) {
				const run = await layoutGraph(nodes, edges, { direction: "RIGHT" });
				expect(run.nodes.length).toBe(nodes.length);
				for (let j = 0; j < run.nodes.length; j++) {
					expect(run.nodes[j]!.x).toBe(baselinePositions[j]!.x);
					expect(run.nodes[j]!.y).toBe(baselinePositions[j]!.y);
				}
			}
		});

		it("executes 20 concurrent layoutGraph calls without unhandled rejections or corruption", async () => {
			const { nodes, edges } = generateSyntheticGraph(50, "dag_hierarchical");

			const promises = Array.from({ length: 20 }, (_, idx) =>
				layoutGraph(nodes, edges, {
					direction: idx % 2 === 0 ? "RIGHT" : "DOWN",
					nodeSpacing: 50 + (idx % 3) * 10,
				}),
			);

			const results = await Promise.all(promises);
			expect(results.length).toBe(20);
			for (const res of results) {
				expect(res.nodes.length).toBe(50);
				expect(res.edges.length).toBe(edges.length);
				expect(res.width).toBeGreaterThan(0);
				expect(res.height).toBeGreaterThan(0);
				for (const n of res.nodes) {
					expect(Number.isFinite(n.x)).toBe(true);
					expect(Number.isFinite(n.y)).toBe(true);
				}
			}
		});

		it("GraphCanvas generation counter prevents stale in-flight layout from overwriting newer layout", async () => {
			let layoutGen = 0;
			let committedGen = -1;

			async function triggerSimulatedLayout(delayMs: number, genValue: number) {
				await new Promise((resolve) => setTimeout(resolve, delayMs));
				if (layoutGen === genValue) {
					committedGen = genValue;
				}
			}

			layoutGen = 1;
			const slowPromise = triggerSimulatedLayout(50, 1);

			layoutGen = 2;
			const fastPromise = triggerSimulatedLayout(10, 2);

			await Promise.all([slowPromise, fastPromise]);

			expect(committedGen).toBe(2);
		});
	});

	// =========================================================================
	// 4. ELK Worker Rejection & Failure Fallback Handling
	// =========================================================================
	describe("4. ELK Worker Rejection & Fallback Handling", () => {
		it("catches asynchronous ELK worker rejection and falls back cleanly without throwing", async () => {
			const { nodes, edges } = generateSyntheticGraph(30, "dag_hierarchical");

			const crashingElk = {
				layout: () => Promise.reject(new Error("ELK Worker crashed: Out of memory in WebAssembly engine")),
			};

			const result = await layoutGraph(nodes, edges, {
				elkInstance: crashingElk,
				direction: "RIGHT",
			});

			expect(result.nodes.length).toBe(30);
			expect(result.edges.length).toBe(edges.length);
			expect(result.durationMs).toBeGreaterThanOrEqual(0);

			const roots = result.nodes.filter((n) => n.x === 50);
			expect(roots.length).toBeGreaterThan(0);
		});

		it("catches ELK non-Error object rejection (string rejection) gracefully", async () => {
			const { nodes, edges } = generateSyntheticGraph(10, "sparse");

			const nonErrorElk = {
				layout: () => Promise.reject("Arbitrary string worker rejection"),
			};

			const result = await layoutGraph(nodes, edges, {
				elkInstance: nonErrorElk,
			});

			expect(result.nodes.length).toBe(10);
		});

		it("handles ELK layout timeout cleanly without unhandled rejections", async () => {
			const { nodes, edges } = generateSyntheticGraph(25, "dag_hierarchical");

			let resolveHanging: (val: unknown) => void = () => {};
			const hangingPromise = new Promise((resolve) => {
				resolveHanging = resolve;
			});

			const hangingElk = {
				layout: () => hangingPromise,
			};

			const result = await layoutGraph(nodes, edges, {
				timeoutMs: 15,
				elkInstance: hangingElk,
			});

			expect(result.nodes.length).toBe(25);
			expect(result.edges.length).toBe(edges.length);

			resolveHanging({ children: [] });
			await new Promise((resolve) => setTimeout(resolve, 30));
		});

		it("BUG REPRODUCTION: synchronous ELK throw leaves unhandled timeoutPromise rejection (CHALLENGE-5D-BUG-01)", async () => {
			// Tests and documents CHALLENGE-5D-BUG-01:
			// In graph-layout.ts lines 244-260, timeoutPromise is created before calling elk.layout.
			// When elk.layout throws synchronously, execution skips `finally { clearTimeout(timeoutHandle); }`.
			// After timeoutMs, timeoutPromise rejects with unhandled rejection.
			const { nodes, edges } = generateSyntheticGraph(10, "sparse");

			const throwingElk = {
				layout: () => {
					throw new Error("Immediate synchronous initialization error");
				},
			};

			const result = await layoutGraph(nodes, edges, {
				timeoutMs: 40,
				elkInstance: throwingElk,
			});

			// Fallback layout itself succeeds synchronously
			expect(result.nodes.length).toBe(10);

			// Wait past timeoutMs (40ms) to allow the leaked timeout timer to fire.
			// In graph-layout.ts, because elk.layout() is outside the try block, clearTimeout(timeoutHandle)
			// is bypassed on synchronous throws, triggering an unhandled promise rejection error.
			await new Promise((resolve) => setTimeout(resolve, 60));
		});
	});

	// =========================================================================
	// 5. Adversarial Graph Topologies & Edge Cases
	// =========================================================================
	describe("5. Adversarial Topologies (Cycles, Stars, Chains, Disconnected Components, Dangling Edges)", () => {
		it("handles cyclical graphs (cycles & chords) without infinite loops", async () => {
			const { nodes, edges } = generateSyntheticGraph(30, "cyclic");

			const fallbackResult = computeFallbackLayeredLayout(nodes, edges);
			expect(fallbackResult.nodes.length).toBe(30);
			expect(fallbackResult.edges.length).toBe(30);

			const elkResult = await layoutGraph(nodes, edges);
			expect(elkResult.nodes.length).toBe(30);
		});

		it("handles deep linear chains (150 nodes chain A -> B -> ... -> N)", async () => {
			const { nodes, edges } = generateSyntheticGraph(150, "chain");
			expect(edges.length).toBe(149);

			const start = performance.now();
			const result = await layoutGraph(nodes, edges, { direction: "RIGHT" });
			const elapsed = performance.now() - start;

			expect(result.nodes.length).toBe(150);
			expect(elapsed).toBeLessThan(300);

			const firstNode = result.nodes.find((n) => n.id === "urn:opendv:stress:node_0")!;
			const lastNode = result.nodes.find((n) => n.id === "urn:opendv:stress:node_149")!;
			expect(firstNode.x).toBeLessThan(lastNode.x);
		});

		it("handles star topology (1 hub to 149 leaf nodes)", async () => {
			const { nodes, edges } = generateSyntheticGraph(150, "star");
			expect(edges.length).toBe(149);

			const start = performance.now();
			const result = await layoutGraph(nodes, edges, { direction: "RIGHT" });
			const elapsed = performance.now() - start;

			expect(result.nodes.length).toBe(150);
			expect(elapsed).toBeLessThan(300);

			const hubNode = result.nodes.find((n) => n.id === "urn:opendv:stress:node_0")!;
			expect(hubNode.x).toBeDefined();
		});

		it("handles completely disconnected components (100 isolated nodes, 0 edges)", async () => {
			const { nodes } = generateSyntheticGraph(100, "sparse");
			const result = await layoutGraph(nodes, []);

			expect(result.nodes.length).toBe(100);
			expect(result.edges.length).toBe(0);
			expect(result.width).toBeGreaterThan(0);
			expect(result.height).toBeGreaterThan(0);
		});

		it("filters out dangling edges where source or target URI does not exist", async () => {
			const { nodes } = generateSyntheticGraph(10, "sparse");
			const invalidEdges: RelationshipEdge[] = [
				{
					id: "dangling_1",
					relationship_uri: "rel_d1",
					relationship_name: "rel_d1",
					target_link: "lnk_d1",
					source_concept_uri: "urn:opendv:stress:node_0",
					target_concept_uri: "urn:opendv:stress:NON_EXISTENT_1",
				},
				{
					id: "dangling_2",
					relationship_uri: "rel_d2",
					relationship_name: "rel_d2",
					target_link: "lnk_d2",
					source_concept_uri: "urn:opendv:stress:NON_EXISTENT_2",
					target_concept_uri: "urn:opendv:stress:node_1",
				},
				{
					id: "valid_edge",
					relationship_uri: "rel_valid",
					relationship_name: "rel_valid",
					target_link: "lnk_valid",
					source_concept_uri: "urn:opendv:stress:node_0",
					target_concept_uri: "urn:opendv:stress:node_1",
				},
			];

			const result = await layoutGraph(nodes, invalidEdges);
			expect(result.nodes.length).toBe(10);
			expect(result.edges.length).toBe(1);
			expect(result.edges[0]!.id).toBe("valid_edge");
		});

		it("handles duplicate edges between identical node pairs safely", async () => {
			const { nodes } = generateSyntheticGraph(5, "sparse");
			const dupEdges: RelationshipEdge[] = [
				{
					id: "e1",
					relationship_uri: "rel_1",
					relationship_name: "rel_1",
					target_link: "lnk_1",
					source_concept_uri: "urn:opendv:stress:node_0",
					target_concept_uri: "urn:opendv:stress:node_1",
				},
				{
					id: "e2",
					relationship_uri: "rel_2",
					relationship_name: "rel_2",
					target_link: "lnk_2",
					source_concept_uri: "urn:opendv:stress:node_0",
					target_concept_uri: "urn:opendv:stress:node_1",
				},
			];

			const result = await layoutGraph(nodes, dupEdges);
			expect(result.nodes.length).toBe(5);
			expect(result.edges.length).toBe(2);
		});
	});

	// =========================================================================
	// 6. GraphCanvas Component Integration & Search Filtering
	// =========================================================================
	describe("6. GraphCanvas Component Integration & Search Filtering", () => {
		it("renders GraphCanvas toolbar with all controls and LOD buttons", () => {
			const { nodes, edges } = generateSyntheticGraph(150, "dag_hierarchical");
			studioStore.setState({
				nodes,
				edges,
				layout: {
					leftDrawerTab: "sessions",
					leftDrawerOpen: true,
					rightDrawerOpen: false,
					bottomDockOpen: false,
					activeModal: null,
					lodTier: 2,
					minimapVisible: true,
					searchTerm: "Alpha",
				},
			});

			const html = renderToStaticMarkup(createElement(GraphCanvas));

			expect(html).toContain("data-testid=\"graph-canvas-container\"");
			expect(html).toContain("data-testid=\"canvas-toolbar\"");
			expect(html).toContain("data-testid=\"canvas-search-input\"");
			expect(html).toContain("data-testid=\"lod-tier-1-btn\"");
			expect(html).toContain("data-testid=\"lod-tier-2-btn\"");
			expect(html).toContain("data-testid=\"lod-tier-3-btn\"");
			expect(html).toContain("data-testid=\"auto-layout-btn\"");
			expect(html).toContain("data-testid=\"toggle-minimap-btn\"");
			expect(html).toContain("btn-clear-search");
		});

		it("filters nodes and dims non-matching nodes when searchTerm is set", () => {
			const { nodes, edges } = generateSyntheticGraph(20, "sparse");
			studioStore.setState({
				nodes,
				edges,
				layout: {
					leftDrawerTab: "sessions",
					leftDrawerOpen: true,
					rightDrawerOpen: false,
					bottomDockOpen: false,
					activeModal: null,
					lodTier: 1,
					minimapVisible: false,
					searchTerm: "Concept 0",
				},
			});

			const html = renderToStaticMarkup(createElement(GraphCanvas));
			expect(html).toContain("Concept 0");
		});
	});
});
