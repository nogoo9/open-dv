/**
/**
 * Empirical Adversarial Challenger Test Suite: Milestone M2 Feature 10
 *
 * Exhaustively stress-tests:
 * 1. url-router.ts query param parsing and serialization:
 *    - Full, minimal, empty, malformed, encoded, extra, garbage, and adversarial params
 *    - Path variations and roundtrips
 * 2. createOntologyStudioPipelineDraft:
 *    - AM domain vs custom domains, 4 target hubs, Wave 0 doc drafts, input spec conversion
 * 3. PipelineBuilderView auto-staging on mount:
 *    - Studio deep-link trigger vs direct access, existing stagedSpec preservation, location.search fallback
 */

import { describe, expect, it } from "bun:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import {
	PipelineBuilderView,
	buildCurrentStagedSpec,
} from "../src/explorer/builder/PipelineBuilderView";
import {
	type InteractivePipelineDraft,
	type StagedPipelineSpec,
	createDefaultPipelineDraft,
	createOntologyStudioPipelineDraft,
	draftToInputSpecObject,
	draftToStagedSpec,
	draftToYamlOrJsonText,
} from "../src/explorer/builder/specBuilderUtils";
import {
	buildExplorerUrl,
	parseExplorerUrlState,
} from "../src/explorer/url-router";

describe("Empirical Challenger M2: Feature 10 Adversarial Stress Test Suite", () => {
	// ---------------------------------------------------------------------------
	// 1. url-router.ts Adversarial Parsing & Serialization
	// ---------------------------------------------------------------------------
	describe("1. url-router query param parsing & serialization", () => {
		it("correctly parses full parameters in standard format", () => {
			const parsed = parseExplorerUrlState(
				"/builder",
				"?session_id=sess_123&source=ontology_studio&domain_namespace=credit&pipeline_id=pl_credit",
			);
			expect(parsed.view).toBe("builder");
			expect(parsed.builderSessionId).toBe("sess_123");
			expect(parsed.builderSource).toBe("ontology_studio");
			expect(parsed.builderDomain).toBe("credit");
			expect(parsed.builderPipelineId).toBe("pl_credit");
		});

		it("correctly parses minimal parameter combinations", () => {
			// Only session_id
			const p1 = parseExplorerUrlState("/builder", "?session_id=s1");
			expect(p1.view).toBe("builder");
			expect(p1.builderSessionId).toBe("s1");
			expect(p1.builderSource).toBeUndefined();
			expect(p1.builderDomain).toBeUndefined();
			expect(p1.builderPipelineId).toBeUndefined();

			// Only source
			const p2 = parseExplorerUrlState(
				"/builder",
				"?source=ontology_studio",
			);
			expect(p2.view).toBe("builder");
			expect(p2.builderSessionId).toBeUndefined();
			expect(p2.builderSource).toBe("ontology_studio");

			// Only domain_namespace
			const p3 = parseExplorerUrlState(
				"/builder",
				"?domain_namespace=asset_management",
			);
			expect(p3.view).toBe("builder");
			expect(p3.builderDomain).toBe("asset_management");

			// Only pipeline_id
			const p4 = parseExplorerUrlState("/builder", "?pipeline_id=pl_custom");
			expect(p4.view).toBe("builder");
			expect(p4.builderPipelineId).toBe("pl_custom");
		});

		it("handles empty and whitespace-only query parameters without setting empty strings", () => {
			const parsed = parseExplorerUrlState(
				"/builder",
				"?session_id=&source=   &domain_namespace=\t&pipeline_id=\n",
			);
			expect(parsed.view).toBe("builder");
			expect(parsed.builderSessionId).toBeUndefined();
			expect(parsed.builderSource).toBeUndefined();
			expect(parsed.builderDomain).toBeUndefined();
			expect(parsed.builderPipelineId).toBeUndefined();
		});

		it("resiliently handles search strings missing the leading question mark", () => {
			const parsed = parseExplorerUrlState(
				"/builder",
				"session_id=sess_no_q&source=ontology_studio",
			);
			expect(parsed.view).toBe("builder");
			expect(parsed.builderSessionId).toBe("sess_no_q");
			expect(parsed.builderSource).toBe("ontology_studio");
		});

		it("correctly decodes percent-encoded query parameters and special characters", () => {
			const search =
				"?session_id=sess%2F2026%20alpha&source=ontology%2Bstudio&domain_namespace=credit%26risk&pipeline_id=pl%3D42";
			const parsed = parseExplorerUrlState("/builder", search);
			expect(parsed.builderSessionId).toBe("sess/2026 alpha");
			expect(parsed.builderSource).toBe("ontology+studio");
			expect(parsed.builderDomain).toBe("credit&risk");
			expect(parsed.builderPipelineId).toBe("pl=42");
		});

		it("resists extra, garbage, and adversarial injection parameters", () => {
			const search =
				"?extra=garbage&session_id=valid_sess&<script>=alert(1)&steward_hold=reject&source=ontology_studio&sql=DROP%20TABLE%20hubs";
			const parsed = parseExplorerUrlState("/builder", search);
			expect(parsed.view).toBe("builder");
			expect(parsed.builderSessionId).toBe("valid_sess");
			expect(parsed.builderSource).toBe("ontology_studio");
			// Extra parameters do not corrupt explorer state
			expect(parsed.stageIdx).toBe(5);
			expect(parsed.fullTab).toBe("split");
			expect(parsed.activeWave).toBe("all");
		});

		it("correctly identifies builder view across path variations", () => {
			expect(parseExplorerUrlState("/builder").view).toBe("builder");
			expect(parseExplorerUrlState("/builder/").view).toBe("builder");
			expect(parseExplorerUrlState("/builder/step2").view).toBe("builder");

			// Non-builder paths
			expect(parseExplorerUrlState("/stages/03").view).toBe("stage");
			expect(parseExplorerUrlState("/stages/03").stageIdx).toBe(3);
			expect(parseExplorerUrlState("/settings").view).toBe("settings");
			expect(parseExplorerUrlState("/").view).toBe("pipeline");
		});

		it("guarantees buildExplorerUrl serialization and roundtrip fidelity", () => {
			const originalState = {
				view: "builder" as const,
				stageIdx: 5,
				fullId: null,
				fullTab: "split" as const,
				activeWave: "all" as const,
				builderSessionId: "am-session-2026",
				builderSource: "ontology_studio",
				builderDomain: "asset_management",
				builderPipelineId: "pl_am_walkthrough",
			};

			const serializedUrl = buildExplorerUrl(originalState);
			expect(serializedUrl.startsWith("/builder?")).toBe(true);

			const [pathname, search] = serializedUrl.split("?");
			const reparsed = parseExplorerUrlState(pathname, `?${search}`);

			expect(reparsed.view).toBe(originalState.view);
			expect(reparsed.builderSessionId).toBe(originalState.builderSessionId);
			expect(reparsed.builderSource).toBe(originalState.builderSource);
			expect(reparsed.builderDomain).toBe(originalState.builderDomain);
			expect(reparsed.builderPipelineId).toBe(originalState.builderPipelineId);
		});

		it("does not leak builder query params when view is not builder", () => {
			const pipelineState = {
				view: "pipeline" as const,
				stageIdx: 5,
				fullId: null,
				fullTab: "split" as const,
				activeWave: "all" as const,
				builderSessionId: "am-session-2026",
				builderSource: "ontology_studio",
			};

			const url = buildExplorerUrl(pipelineState);
			expect(url).toBe("/");
			expect(url).not.toContain("session_id");
			expect(url).not.toContain("source");
		});

		it("handles invalid tab and wave query params gracefully alongside builder params", () => {
			const parsed = parseExplorerUrlState(
				"/builder",
				"?session_id=sess_valid&tab=nonexistent&wave=W9999",
			);
			expect(parsed.view).toBe("builder");
			expect(parsed.builderSessionId).toBe("sess_valid");
			// Fallback defaults
			expect(parsed.fullTab).toBe("split");
			expect(parsed.activeWave).toBe("all");
		});

		it("handles duplicate query parameters predictably", () => {
			const parsed = parseExplorerUrlState(
				"/builder",
				"?session_id=first&session_id=second&source=ontology_studio",
			);
			expect(parsed.view).toBe("builder");
			// URLSearchParams returns the first occurrence
			expect(parsed.builderSessionId).toBe("first");
			expect(parsed.builderSource).toBe("ontology_studio");
		});
	});

	// ---------------------------------------------------------------------------
	// 2. createOntologyStudioPipelineDraft Adversarial Tests
	// ---------------------------------------------------------------------------
	describe("2. createOntologyStudioPipelineDraft pre-population", () => {
		it("provides full Asset Management pre-population when called with default/undefined params", () => {
			const draft = createOntologyStudioPipelineDraft();
			expect(draft.pipelineId).toBe("pl_am_walkthrough");
			expect(draft.projectId).toBe("proj_asset_management");
			expect(draft.displayName).toBe("Asset Management Data Vault 2.0 Pipeline");
			expect(draft.domainNamespace).toBe("asset_management");
			expect(draft.graphAOntologyPath).toBe(
				"examples/asset_management/am_graph_a_ontology.yaml",
			);
			expect(draft.ontologyPolicy).toBe("recommend");
			expect(draft.waves).toHaveLength(1);

			const wave0 = draft.waves[0];
			expect(wave0.waveIndex).toBe(0);
			expect(wave0.waveName).toBe("W0_Cold_Start");
			expect(wave0.waveType).toBe("COLD_START");
			expect(wave0.sources).toHaveLength(3);

			// Check all 4 target hubs across relational sources and unstructured doc
			const sources = wave0.sources;
			expect(sources.find((s) => s.targetHubName === "hub_security")).toBeDefined();
			expect(sources.find((s) => s.targetHubName === "hub_investor")).toBeDefined();
			expect(sources.find((s) => s.targetHubName === "hub_rate_index")).toBeDefined();

			expect(wave0.unstructuredDocuments).toHaveLength(1);
			expect(wave0.unstructuredDocuments[0].targetHubName).toBe("hub_document");
			expect(wave0.unstructuredDocuments[0].docId).toBe("doc_sec_filing_001");
			expect(wave0.unstructuredDocuments[0].documentPath).toBe(
				"examples/asset_management/docs/sec_filing_sample.md",
			);
		});

		it("correctly derives pipelineId from sessionId when pipelineId is omitted", () => {
			const draft = createOntologyStudioPipelineDraft({
				sessionId: "custom_sess_99",
			});
			expect(draft.pipelineId).toBe("pl_custom_sess_99");
			expect(draft.domainNamespace).toBe("asset_management");
		});

		it("supports AM domain variations (e.g. 'am_credit', 'private_am')", () => {
			const draft = createOntologyStudioPipelineDraft({
				domainNamespace: "am_private_equity",
			});
			expect(draft.domainNamespace).toBe("am_private_equity");
			expect(draft.displayName).toBe("Asset Management Data Vault 2.0 Pipeline");
			expect(draft.waves[0].sources).toHaveLength(3);
			expect(draft.waves[0].unstructuredDocuments).toHaveLength(1);
		});

		it("falls back to generic starter template when non-AM domain is specified", () => {
			const draft = createOntologyStudioPipelineDraft({
				domainNamespace: "supply_chain",
				pipelineId: "pl_supply_chain",
			});
			expect(draft.domainNamespace).toBe("supply_chain");
			expect(draft.pipelineId).toBe("pl_supply_chain");
			expect(draft.displayName).toBe("supply_chain Data Vault 2.0 Pipeline");
			expect(draft.graphAOntologyPath).toBe(
				"examples/supply_chain/supply_chain_graph_a_ontology.yaml",
			);
			// Non-AM domain should have 1 generic empty source and 0 unstructured docs
			expect(draft.waves[0].sources).toHaveLength(1);
			expect(draft.waves[0].unstructuredDocuments).toHaveLength(0);
		});

		it("converts draft to valid PipelineInputSpec object with parsed business keys", () => {
			const draft = createOntologyStudioPipelineDraft();
			const spec = draftToInputSpecObject(draft);

			expect(spec.pipeline_id).toBe("pl_am_walkthrough");
			expect(spec.project_id).toBe("proj_asset_management");
			expect(spec.ontology_policy).toBe("recommend");

			const waves = spec.waves as Array<Record<string, unknown>>;
			expect(waves).toHaveLength(1);

			const wave0 = waves[0];
			const sources = wave0.sources as Array<{
				source_system: string;
				table_name: string;
				data_path: string;
				target_hub_name?: string;
				business_key_columns: string[];
			}>;
			expect(sources).toHaveLength(3);

			// Verify business_key_columns is array of strings without commas/spaces
			const secMaster = sources.find((s) => s.table_name === "stg_sec_security_master");
			expect(secMaster?.business_key_columns).toEqual(["ticker", "cik"]);

			const sec13f = sources.find((s) => s.table_name === "stg_sec_13f_holdings");
			expect(sec13f?.business_key_columns).toEqual(["cik"]);

			const fred = sources.find((s) => s.table_name === "stg_fred_treasury_rates");
			expect(fred?.business_key_columns).toEqual(["series_id"]);

			const docs = wave0.unstructured_documents as Array<{
				doc_id: string;
				document_path: string;
				target_hub_name: string;
			}>;
			expect(docs).toHaveLength(1);
			expect(docs[0].target_hub_name).toBe("hub_document");
		});

		it("serializes draft to valid JSON text", () => {
			const draft = createOntologyStudioPipelineDraft();
			const jsonText = draftToYamlOrJsonText(draft);
			expect(() => JSON.parse(jsonText)).not.toThrow();
			const parsed = JSON.parse(jsonText);
			expect(parsed.pipeline_id).toBe("pl_am_walkthrough");
			expect(parsed.waves).toHaveLength(1);
		});
	});

	// ---------------------------------------------------------------------------
	// 3. PipelineBuilderView Auto-Staging & Component Life-Cycle
	// ---------------------------------------------------------------------------
	describe("3. PipelineBuilderView auto-staging on mount", () => {
		it("auto-stages draft on mount when navigated from Ontology Studio via props", () => {
			let capturedStaged: StagedPipelineSpec | null = null;

			const html = renderToStaticMarkup(
				createElement(PipelineBuilderView, {
					sessionId: "sess_am_e2e",
					source: "ontology_studio",
					initialDomainNamespace: "asset_management",
					initialPipelineId: "pl_am_walkthrough",
					onStageSpec: (staged) => {
						capturedStaged = staged;
					},
				}),
			);

			expect(html).toContain('data-testid="pipeline-builder-view"');
			expect(html).toContain("Asset Management Data Vault 2.0 Pipeline");
			expect(html).toContain("hub_security");
			expect(html).toContain("hub_investor");
			expect(html).toContain("hub_rate_index");
			expect(html).toContain("hub_document");

			expect(capturedStaged).not.toBeNull();
			const spec = capturedStaged as unknown as StagedPipelineSpec;
			expect(spec.mode).toBe("interactive");
			expect(spec.inputSpec?.pipeline_id).toBe("pl_am_walkthrough");
			expect(spec.inputSpec?.domain_namespace).toBe("asset_management");
			expect(spec.validated).toBe(false);
		});

		it("auto-stages draft when only sessionId is present without explicit source prop", () => {
			let capturedStaged: StagedPipelineSpec | null = null;

			renderToStaticMarkup(
				createElement(PipelineBuilderView, {
					sessionId: "sess_standalone",
					onStageSpec: (staged) => {
						capturedStaged = staged;
					},
				}),
			);

			expect(capturedStaged).not.toBeNull();
			const spec = capturedStaged as unknown as StagedPipelineSpec;
			expect(spec.inputSpec?.pipeline_id).toBe("pl_sess_standalone");
		});

		it("does NOT overwrite when stagedSpec is already provided", () => {
			let stageCallCount = 0;
			const existingStaged: StagedPipelineSpec = {
				mode: "interactive",
				inputSpec: {
					pipeline_id: "pl_preexisting",
					domain_namespace: "custom",
				},
				stewardHoldPolicy: "pause",
				validated: true,
			};

			renderToStaticMarkup(
				createElement(PipelineBuilderView, {
					sessionId: "sess_new",
					source: "ontology_studio",
					stagedSpec: existingStaged,
					onStageSpec: () => {
						stageCallCount++;
					},
				}),
			);

			// Should not trigger onStageSpec when stagedSpec is already present
			expect(stageCallCount).toBe(0);
		});

		it("does not auto-stage when opened standalone without session or ontology source", () => {
			let stageCallCount = 0;

			const html = renderToStaticMarkup(
				createElement(PipelineBuilderView, {
					onStageSpec: () => {
						stageCallCount++;
					},
				}),
			);

			expect(stageCallCount).toBe(0);
			// Default draft is Global Commerce Vault
			expect(html).toContain("Global Commerce Data Vault 2.0");
			expect(html).toContain("hub_customer");
		});

		it("enforces two-step execution and NEVER auto-submits a run on mount", () => {
			let runStartedCount = 0;

			renderToStaticMarkup(
				createElement(PipelineBuilderView, {
					sessionId: "sess_am_e2e",
					source: "ontology_studio",
					onRunStarted: () => {
						runStartedCount++;
					},
				}),
			);

			expect(runStartedCount).toBe(0);
		});

		it("falls back to globalThis.location.search when props are omitted", () => {
			const originalLocation = (globalThis as unknown as { location?: unknown }).location;
			try {
				// Mock globalThis.location.search
				(globalThis as unknown as { location: { search: string } }).location = {
					search: "?source=ontology_studio&session_id=sess_from_global_url&domain_namespace=asset_management",
				};

				let capturedStaged: StagedPipelineSpec | null = null;
				renderToStaticMarkup(
					createElement(PipelineBuilderView, {
						onStageSpec: (staged) => {
							capturedStaged = staged;
						},
					}),
				);

				expect(capturedStaged).not.toBeNull();
				const spec = capturedStaged as unknown as StagedPipelineSpec;
				expect(spec.inputSpec?.pipeline_id).toBe("pl_sess_from_global_url");
				expect(spec.inputSpec?.domain_namespace).toBe("asset_management");
			} finally {
				(globalThis as unknown as { location?: unknown }).location = originalLocation;
			}
		});
	});
});
