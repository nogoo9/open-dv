/**
 * Component & Layout Engine Tests: Graph Canvas & Multi-Tier LOD (Task 5D.4, #225)
 * (apps/opendv-web-workbench/tests/ontology-graph-canvas.test.tsx)
 */

import { beforeEach, describe, expect, it } from "bun:test";
import React, { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { ReactFlowProvider } from "@xyflow/react";
import {
	ConceptNodeComponent,
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

describe("GraphCanvas & Elk Layout Engine (Task 5D.4, #225)", () => {
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
	describe("1. Layout Adapter Performance & Scaling", () => {
		it("handles empty nodes and edges cleanly", async () => {
			const res = await layoutGraph([], []);
			expect(res.nodes.length).toBe(0);
			expect(res.edges.length).toBe(0);
			expect(res.durationMs).toBe(0);
		});

		it("lays out small graph with layered coordinates", async () => {
			const sampleNodes: ConceptNode[] = [
				{
					id: "urn:ontology:entity:fund",
					concept_uri: "urn:ontology:entity:fund",
					concept_name: "Fund",
					elm_archetype: "CORE_CONCEPT",
					definition: "Investment fund.",
					grounding_status: "GROUNDED",
					attributes: [],
					synonyms: [],
					external_mappings: [],
				},
				{
					id: "urn:ontology:entity:investor",
					concept_uri: "urn:ontology:entity:investor",
					concept_name: "Investor",
					elm_archetype: "CORE_CONCEPT",
					definition: "Limited partner.",
					grounding_status: "GROUNDED",
					attributes: [],
					synonyms: [],
					external_mappings: [],
				},
			];

			const sampleEdges: RelationshipEdge[] = [
				{
					id: "rel-1",
					relationship_uri: "urn:ontology:relationship:fund_investor",
					relationship_name: "FundInvestor",
					target_link: "lnk_fund_investor",
					source_concept_uri: "urn:ontology:entity:fund",
					target_concept_uri: "urn:ontology:entity:investor",
				},
			];

			const res = await layoutGraph(sampleNodes, sampleEdges, { direction: "RIGHT" });
			expect(res.nodes.length).toBe(2);
			expect(res.nodes[0]?.x).toBeDefined();
			expect(res.nodes[0]?.y).toBeDefined();
			expect(res.nodes[1]?.x).toBeDefined();
			expect(res.nodes[1]?.y).toBeDefined();
			expect(res.width).toBeGreaterThan(0);
			expect(res.height).toBeGreaterThan(0);
		});

		it("executes layout for 100+ nodes within performance budget", async () => {
			const largeNodes: ConceptNode[] = [];
			const largeEdges: RelationshipEdge[] = [];

			for (let i = 0; i < 120; i++) {
				largeNodes.push({
					id: `urn:ontology:entity:concept_${i}`,
					concept_uri: `urn:ontology:entity:concept_${i}`,
					concept_name: `Concept ${i}`,
					elm_archetype: i % 3 === 0 ? "CORE_CONCEPT" : "REFERENCE_CONCEPT",
					definition: `Definition for concept ${i}`,
					grounding_status: i % 4 === 0 ? "GROUNDED" : "LOW_CONFIDENCE",
					attributes: [
						{ attribute_name: `attr_${i}_id`, data_type: "string", is_optional: false },
					],
					synonyms: [],
					external_mappings: [],
				});

				if (i > 0) {
					largeEdges.push({
						id: `edge_${i}`,
						relationship_uri: `urn:ontology:relationship:rel_${i}`,
						relationship_name: `rel_${i}`,
						target_link: `lnk_${i}`,
						source_concept_uri: `urn:ontology:entity:concept_${Math.floor(i / 2)}`,
						target_concept_uri: `urn:ontology:entity:concept_${i}`,
					});
				}
			}

			const start = performance.now();
			const res = await layoutGraph(largeNodes, largeEdges, { direction: "RIGHT" });
			const durationMs = performance.now() - start;

			expect(res.nodes.length).toBe(120);
			expect(res.edges.length).toBe(119);
			// Must complete within performance budget (generous headroom for varied CI environments)
			expect(durationMs).toBeLessThan(1500);
		});

		it("computes deterministic fallback layered layout with x=50 starting coordinates", () => {
			const fallbackNodes: ConceptNode[] = [
				{
					id: "fb_1",
					concept_uri: "urn:ontology:entity:fb_1",
					concept_name: "FB 1",
					elm_archetype: "CORE_CONCEPT",
					definition: "Fallback 1.",
					grounding_status: "GROUNDED",
					attributes: [],
					synonyms: [],
					external_mappings: [],
				},
				{
					id: "fb_2",
					concept_uri: "urn:ontology:entity:fb_2",
					concept_name: "FB 2",
					elm_archetype: "CORE_CONCEPT",
					definition: "Fallback 2.",
					grounding_status: "GROUNDED",
					attributes: [],
					synonyms: [],
					external_mappings: [],
				},
			];
			const fallbackEdges: RelationshipEdge[] = [
				{
					id: "edge_fb",
					relationship_uri: "urn:ontology:rel:fb",
					relationship_name: "RelFB",
					target_link: "lnk_fb",
					source_concept_uri: "urn:ontology:entity:fb_1",
					target_concept_uri: "urn:ontology:entity:fb_2",
				},
			];

			const res = computeFallbackLayeredLayout(fallbackNodes, fallbackEdges, {
				direction: "RIGHT",
			});
			expect(res.nodes.length).toBe(2);
			// Layer 0 starts at x = 50 in fallback layered layout
			expect(res.nodes[0]?.x).toBe(50);
			expect(res.edges.length).toBe(1);
		});

		it("wires edges and computes layout correctly when node id differs from concept_uri (MAJ-01)", async () => {
			const nodesWithDistinctIds: ConceptNode[] = [
				{
					id: "node_alpha",
					concept_uri: "urn:opendv:concept:alpha",
					concept_name: "Alpha Concept",
					elm_archetype: "CORE_CONCEPT",
					definition: "Alpha.",
					grounding_status: "GROUNDED",
					attributes: [],
					synonyms: [],
					external_mappings: [],
				},
				{
					id: "node_beta",
					concept_uri: "urn:opendv:concept:beta",
					concept_name: "Beta Concept",
					elm_archetype: "CORE_CONCEPT",
					definition: "Beta.",
					grounding_status: "GROUNDED",
					attributes: [],
					synonyms: [],
					external_mappings: [],
				},
			];

			const edgeWithUris: RelationshipEdge[] = [
				{
					id: "rel_ab",
					relationship_uri: "urn:opendv:rel:ab",
					relationship_name: "AlphaToBeta",
					target_link: "lnk_ab",
					source_concept_uri: "urn:opendv:concept:alpha",
					target_concept_uri: "urn:opendv:concept:beta",
					cardinality: "1..N",
				},
			];

			const fallbackResult = computeFallbackLayeredLayout(
				nodesWithDistinctIds,
				edgeWithUris,
				{ direction: "RIGHT" },
			);
			const posAlpha = fallbackResult.nodes.find((n) => n.id === "node_alpha")!;
			const posBeta = fallbackResult.nodes.find((n) => n.id === "node_beta")!;
			expect(posAlpha).toBeDefined();
			expect(posBeta).toBeDefined();
			// Alpha is source, Beta is target, so Alpha must be in an earlier layer (lower x in RIGHT layout)
			expect(posAlpha.x).toBeLessThan(posBeta.x);

			const elkResult = await layoutGraph(nodesWithDistinctIds, edgeWithUris);
			expect(elkResult.nodes.length).toBe(2);
			expect(elkResult.edges.length).toBe(1);
		});

		it("executes deterministic fallback layered layout in < 25 ms", () => {
			const testNodes: ConceptNode[] = Array.from({ length: 50 }, (_, i) => ({
				id: `c_${i}`,
				concept_uri: `urn:ontology:entity:c_${i}`,
				concept_name: `Concept ${i}`,
				elm_archetype: "CORE_CONCEPT",
				definition: "def",
				grounding_status: "GROUNDED",
				attributes: [],
				synonyms: [],
				external_mappings: [],
			}));

			const res = computeFallbackLayeredLayout(testNodes, []);
			expect(res.nodes.length).toBe(50);
			expect(res.durationMs).toBeLessThan(25);
		});
	});

	describe("2. ConceptNodeComponent 3-Tier Level-of-Detail (LOD)", () => {
		const sampleConcept: ConceptNode = {
			id: "urn:ontology:entity:portfolio_company",
			concept_uri: "urn:ontology:entity:portfolio_company",
			concept_name: "Portfolio Company",
			elm_archetype: "CORE_CONCEPT",
			definition: "Operating commercial enterprise acquired by private equity fund.",
			target_hub: "hub_portfolio_company",
			grounding_status: "GROUNDED",
			confidence: 0.92,
			attributes: [
				{ attribute_name: "company_id", data_type: "string", is_optional: false },
				{ attribute_name: "incorporation_date", data_type: "date", is_optional: true },
			],
			synonyms: ["Target Co", "OpCo"],
			external_mappings: [
				{ scheme: "fibo", target_uri: "fibo-be-le-cb:CommercialBank", confidence: 0.92 },
			],
		};

		const renderNode = (data: any) =>
			renderToStaticMarkup(
				createElement(
					ReactFlowProvider,
					null,
					createElement(ConceptNodeComponent as unknown as React.ComponentType<any>, { data }),
				),
			);

		it("renders Tier 1 Macro LOD as compact chip", () => {
			const html = renderNode({
				concept: sampleConcept,
				lodTier: 1,
				isSelected: false,
				isDimmed: false,
			});

			expect(html).toContain("lod-tier-1");
			expect(html).toContain("Portfolio Company");
			expect(html).toContain("concept-name-compact");
			expect(html).toContain("halo-grounded");
			// Should not render full attribute list in Tier 1
			expect(html).not.toContain("incorporation_date");
		});

		it("renders Tier 2 Meso LOD with entity archetype, hub badge, and counts", () => {
			const html = renderNode({
				concept: sampleConcept,
				lodTier: 2,
				isSelected: false,
				isDimmed: false,
			});

			expect(html).toContain("lod-tier-2");
			expect(html).toContain("Portfolio Company");
			expect(html).toContain("CORE_CONCEPT");
			expect(html).toContain("hub_portfolio_company");
			expect(html).toContain("2 attrs");
			expect(html).toContain("1 mappings");
			expect(html).toContain("92%");
			expect(html).toContain("halo-grounded");
		});

		it("renders Tier 3 Micro LOD with full attributes, data types, definitions, and external mappings", () => {
			const html = renderNode({
				concept: sampleConcept,
				lodTier: 3,
				isSelected: true,
				isDimmed: false,
			});

			expect(html).toContain("lod-tier-3");
			expect(html).toContain("Portfolio Company");
			expect(html).toContain("Operating commercial enterprise acquired");
			expect(html).toContain("company_id");
			expect(html).toContain(":string");
			expect(html).toContain("incorporation_date");
			expect(html).toContain(":date");
			expect(html).toContain("CommercialBank");
			expect(html).toContain("node-selected");
		});

		it("renders confidence halo variants (grounded, low-confidence, ungrounded)", () => {
			const groundedHtml = renderNode({
				concept: { ...sampleConcept, grounding_status: "GROUNDED" },
				lodTier: 1,
				isSelected: false,
				isDimmed: false,
			});
			expect(groundedHtml).toContain("halo-grounded");

			const lowConfHtml = renderNode({
				concept: { ...sampleConcept, grounding_status: "LOW_CONFIDENCE" },
				lodTier: 1,
				isSelected: false,
				isDimmed: false,
			});
			expect(lowConfHtml).toContain("halo-low-confidence");

			const ungroundedHtml = renderNode({
				concept: { ...sampleConcept, grounding_status: "UNGROUNDED" },
				lodTier: 1,
				isSelected: false,
				isDimmed: false,
			});
			expect(ungroundedHtml).toContain("halo-ungrounded");
		});
	});

	describe("3. GraphCanvas Toolbar & Controls", () => {
		it("renders canvas toolbar with search input, LOD buttons, and auto-layout button", () => {
			studioStore.setState({
				nodes: [
					{
						id: "urn:ontology:entity:fund",
						concept_uri: "urn:ontology:entity:fund",
						concept_name: "Fund",
						elm_archetype: "CORE_CONCEPT",
						definition: "Investment fund entity.",
						grounding_status: "GROUNDED",
						attributes: [],
						synonyms: [],
						external_mappings: [],
					},
				],
				edges: [],
				layout: {
					leftDrawerTab: "sessions",
					leftDrawerOpen: true,
					rightDrawerOpen: false,
					bottomDockOpen: false,
					activeModal: null,
					lodTier: 2,
					minimapVisible: true,
					searchTerm: "",
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
		});
	});

	describe("4. Graph Layout Fallback & Dangling Edge Handling", () => {
		it("executes computeFallbackLayeredLayout with cycle and isolated node resilience", () => {
			const nodes: ConceptNode[] = [
				{
					id: "n1",
					concept_uri: "urn:concept:n1",
					concept_name: "N1",
					elm_archetype: "CORE_CONCEPT",
					definition: "",
					grounding_status: "GROUNDED",
					attributes: [],
					synonyms: [],
					external_mappings: [],
				},
				{
					id: "n2",
					concept_uri: "urn:concept:n2",
					concept_name: "N2",
					elm_archetype: "CORE_CONCEPT",
					definition: "",
					grounding_status: "GROUNDED",
					attributes: [],
					synonyms: [],
					external_mappings: [],
				},
				{
					id: "n3",
					concept_uri: "urn:concept:n3",
					concept_name: "N3",
					elm_archetype: "CORE_CONCEPT",
					definition: "",
					grounding_status: "GROUNDED",
					attributes: [],
					synonyms: [],
					external_mappings: [],
				},
			];
			const edges: RelationshipEdge[] = [
				{
					id: "e1",
					relationship_uri: "r1",
					relationship_name: "r1",
					target_link: "lnk1",
					source_concept_uri: "urn:concept:n1",
					target_concept_uri: "urn:concept:n2",
				},
				{
					id: "e2",
					relationship_uri: "r2",
					relationship_name: "r2",
					target_link: "lnk2",
					source_concept_uri: "urn:concept:n2",
					target_concept_uri: "urn:concept:n1",
				},
				{
					id: "e_dangling",
					relationship_uri: "r3",
					relationship_name: "r3",
					target_link: "lnk3",
					source_concept_uri: "urn:concept:n1",
					target_concept_uri: "urn:concept:missing",
				},
			];
			const layout = computeFallbackLayeredLayout(nodes, edges);
			expect(layout.nodes).toHaveLength(3);
			expect(layout.durationMs).toBeGreaterThanOrEqual(0);
		});

		it("computeFallbackLayeredLayout drops dangling edges without creating phantom slots", () => {
			const nodes: ConceptNode[] = [
				{
					id: "n1",
					concept_uri: "urn:concept:n1",
					concept_name: "N1",
					elm_archetype: "CORE_CONCEPT",
					definition: "",
					grounding_status: "GROUNDED",
					attributes: [],
					synonyms: [],
					external_mappings: [],
				},
			];
			const edges: RelationshipEdge[] = [
				{
					id: "dangling",
					relationship_uri: "r1",
					relationship_name: "r1",
					target_link: "lnk1",
					source_concept_uri: "urn:concept:n1",
					target_concept_uri: "urn:concept:non_existent",
				},
			];
			const layout = computeFallbackLayeredLayout(nodes, edges);
			expect(layout.nodes).toHaveLength(1);
			expect(layout.nodes[0]?.id).toBe("n1");
		});

		it("layoutGraph respects timeoutMs and falls back to deterministic layered layout with specific coordinates", async () => {
			const nodes: ConceptNode[] = [
				{
					id: "n1",
					concept_uri: "urn:concept:n1",
					concept_name: "Parent",
					elm_archetype: "CORE_CONCEPT",
					definition: "Root entity",
					grounding_status: "GROUNDED",
					attributes: [],
					synonyms: [],
					external_mappings: [],
				},
				{
					id: "n2",
					concept_uri: "urn:concept:n2",
					concept_name: "Child",
					elm_archetype: "SUPPORTING_CONCEPT",
					definition: "Dependent entity",
					grounding_status: "GROUNDED",
					attributes: [],
					synonyms: [],
					external_mappings: [],
				},
			];
			const edges: RelationshipEdge[] = [
				{
					id: "e1",
					relationship_uri: "urn:concept:n1",
					relationship_name: "has_child",
					target_link: "lnk1",
					source_concept_uri: "urn:concept:n1",
					target_concept_uri: "urn:concept:n2",
				},
			];

			// Inject hanging ELK instance to deterministically force the timeout race condition (MIN-04)
			const hangingElk = {
				layout: () => new Promise<never>(() => {}),
			};

			const layout = await layoutGraph(nodes, edges, {
				timeoutMs: 10,
				elkInstance: hangingElk,
				nodeWidth: 240,
				layerSpacing: 100,
				nodeSpacing: 60,
			});

			expect(layout.nodes).toHaveLength(2);
			// Layer 0 root node at x = 50, y = 50
			expect(layout.nodes[0]?.x).toBe(50);
			expect(layout.nodes[0]?.y).toBe(50);
			// Layer 1 child node at x = 50 + nodeWidth (240) + layerSpacing (100) = 390
			expect(layout.nodes[1]?.x).toBe(390);
			expect(layout.nodes[1]?.y).toBe(50);
			expect(layout.durationMs).toBeGreaterThanOrEqual(10);
		});
	});
});
