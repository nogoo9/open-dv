/**
 * Behavioral & Visual Specification Tests for the 3-Level Pipeline Explorer Workbench
 * (`Pipeline -> Stage -> Artifact`) (`docs/design/open-dv-workbench/HANDOFF.md`).
 */

import { describe, expect, it } from "bun:test";
import {
	type VaultObservabilityEventDTO,
	getArtifactConsumedBy,
	getArtifactProducedBy,
} from "@opendv/protocol-ts";
import {
	GLOBAL_COMMERCE_EXPLORER_BUNDLE,
	PE_DEMO_EVENTS,
} from "./fixtures/commerce-explorer-fixture";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { createWorkbenchDevServer } from "../src/dev-server";
import {
	ArtifactFullModal,
	ArtifactVisualizer,
	EXPLORER_GLYPHS,
	EXPLORER_STATUS_TAGS,
	EXPLORER_VIZ_LABELS,
	OpenDVWorkbenchExplorer,
	buildExplorerUrl,
	createExplorerStore,
	parseExplorerUrlState,
} from "../src/index";

describe("3-Level Pipeline Explorer Workbench (HANDOFF.md)", () => {
	describe("1. Design Tokens, Glyphs & @opendv/protocol-ts Data Lineage", () => {
		it("defines the 3 element glyphs (control diamond, data square, component circle) and status pills", () => {
			expect(EXPLORER_GLYPHS.control.bg).toBe("#B45309");
			expect(EXPLORER_GLYPHS.control.clip).toBe(
				"polygon(50% 0,100% 50%,50% 100%,0 50%)",
			);
			expect(EXPLORER_GLYPHS.data.bg).toBe("#1D4ED8");
			expect(EXPLORER_GLYPHS.setting.border).toBe("1.5px solid #1D4ED8");
			expect(EXPLORER_GLYPHS.component.bg).toBe("#4B5563");
			expect(EXPLORER_GLYPHS.component.radius).toBe("999px");

			expect(EXPLORER_STATUS_TAGS.PASS).toEqual({
				bg: "#EEF0F2",
				fg: "#16181D",
			});
			expect(EXPLORER_STATUS_TAGS.AUTO_REPAIRED).toEqual({
				bg: "#FFF4E5",
				fg: "#8A3B07",
			});
			expect(EXPLORER_STATUS_TAGS["ASK-USER → resolved"]).toEqual({
				bg: "#FFF4E5",
				fg: "#8A3B07",
			});
			expect(EXPLORER_STATUS_TAGS["REJECT-WITH-TRACE"]).toEqual({
				bg: "#FDECEA",
				fg: "#8F1D12",
			});
			expect(EXPLORER_STATUS_TAGS["AUTO-FIX"]).toEqual({
				bg: "#EEF2FF",
				fg: "#1E3A8A",
			});
		});

		it("computes Produced by and Consumed by lineage across all 8 canonical stages", () => {
			const stages = GLOBAL_COMMERCE_EXPLORER_BUNDLE.stages;
			expect(stages).toHaveLength(8);
			expect(getArtifactProducedBy(stages, "ontology")).toBe("— (provided)");
			expect(getArtifactConsumedBy(stages, "ontology")).toBe(
				"STAGE_00, STAGE_02, STAGE_03, STAGE_04",
			);
			expect(getArtifactProducedBy(stages, "final_contract")).toBe("STAGE_05");
			expect(getArtifactConsumedBy(stages, "final_contract")).toBe(
				"STAGE_06, STAGE_07",
			);
			expect(getArtifactConsumedBy(stages, "final_sql")).toBe("— (final)");
		});
	});

	describe("2. View 1 — Pipeline Overview & Contextual Inspector", () => {
		it("renders Global Top Bar, Settings, 7 Inputs, 4 Phases (8 Stages), and 5 Outputs", () => {
			const store = createExplorerStore({
				bundle: GLOBAL_COMMERCE_EXPLORER_BUNDLE,
				syncBrowserUrl: false,
			});
			const html = renderToStaticMarkup(
				createElement(OpenDVWorkbenchExplorer, { store }),
			);

			// Top bar
			expect(html).toContain('data-testid="explorer-top-header"');
			expect(html).toContain("open-dv");
			expect(html).toContain("global_commerce_vault");
			expect(html).toContain("pipeline_input.yaml · 4 waves");
			expect(html).toContain("Sealed · 6/6 gates PASS");
			expect(html).toContain("Run pipeline");

			// Settings & Inputs
			expect(html).toContain("harness_config.yaml");
			expect(html).toContain("run options");
			expect(html).toContain("Inputs · 7");
			expect(html).toContain("graph_a_ontology.yaml");
			expect(html).toContain("wave0_crm_customers.csv");
			expect(html).toContain("wave3_enterprise_msa_addendum_2026.txt");
			expect(html).toContain("none (Wave 0 cold start)");

			// 4 Phases & 8 Stages
			expect(html).toContain("Discovery");
			expect(html).toContain("Modelling");
			expect(html).toContain("Verification");
			expect(html).toContain("Compilation");
			expect(html).toContain("STAGE_00_ORCHESTRATION");
			expect(html).toContain("STAGE_05_AXI_DV_STATIC_GATES");
			expect(html).toContain("STAGE_07_DETERMINISTIC_COMPILER");

			// 5 Final Outputs
			expect(html).toContain("Outputs · 5");
			expect(html).toContain("final_contract.yaml");
			expect(html).toContain("final_canonical_ir.json");
			expect(html).toContain("final_four_zone.sql");
			expect(html).toContain("pipeline_report.json");
			expect(html).toContain("vault-ledger.jsonl");

			// Inspector hidden when nothing selected in Pipeline view
			expect(html).not.toContain('data-testid="explorer-inspector"');
		});

		it("opens the contextual Inspector when an artifact card is selected in Pipeline view", () => {
			const store = createExplorerStore({
				bundle: GLOBAL_COMMERCE_EXPLORER_BUNDLE,
				syncBrowserUrl: false,
			});
			store.getState().selectArtifact("final_contract");

			const html = renderToStaticMarkup(
				createElement(OpenDVWorkbenchExplorer, { store }),
			);
			expect(html).toContain('data-testid="explorer-inspector"');
			expect(html).toContain("Data · Final output");
			expect(html).toContain("DVModelContract v6.0");
			expect(html).toContain("STAGE_06, STAGE_07");
			expect(html).toContain("Open full view (Data Vault 2.0 topology) →");
		});
	});

	describe("3. View 2 — Stage Drill-Down (Components, Control & Data Flow, Inspector)", () => {
		it("renders Stage 05 drill-down with Enters with, 6 steps, Hands off, Components, and Step Inspector", () => {
			const store = createExplorerStore({
				bundle: GLOBAL_COMMERCE_EXPLORER_BUNDLE,
				startView: "stage",
				startStage: 5,
				syncBrowserUrl: false,
			});

			const html = renderToStaticMarkup(
				createElement(OpenDVWorkbenchExplorer, { store }),
			);

			expect(html).toContain('data-testid="stage-drilldown-view"');
			expect(html).toContain("STAGE_05 · Gates 0–4 · Static Verification");
			expect(html).toContain("← All stages");
			expect(html).toContain("axi-dv");
			expect(html).toContain("gate3_networkx_dag");
			expect(html).toContain("toon_serializer");
			expect(html).toContain('data-testid="stage-enters-with-strip"');
			expect(html).toContain('data-testid="stage-hands-off-strip"');
			expect(html).toContain("→ STAGE_06_DUCKDB_SANDBOX");
			expect(html).toContain("Closed-loop self-healing retry (Attempt 2/3)");
			expect(html).toContain("Control · step 6 of 6");
			expect(html).toContain("patches bkcc_prefix: STRIPE_EU → 99/99 PASS");

			// Navigate to next stage (Stage 06) and verify empty inspector prompt
			store.getState().nextStage();
			expect(store.getState().stageIdx).toBe(6);
			const stage6Html = renderToStaticMarkup(
				createElement(OpenDVWorkbenchExplorer, { store }),
			);
			expect(stage6Html).toContain("Gate 5 · DuckDB Ephemeral Sandbox");
			expect(stage6Html).toContain(
				"Select a step, a data item or a component to see what it does.",
			);

			// Select a component in Stage 06
			store.getState().selectComponent(0);
			const compHtml = renderToStaticMarkup(
				createElement(OpenDVWorkbenchExplorer, { store }),
			);
			expect(compHtml).toContain("Component · Sandbox");
			expect(compHtml).toContain("duckdb-sandbox");
		});
	});

	describe("4. View 3 — Artifact Full View & All 8 Visual Renderers", () => {
		it("renders all 8 viz modes (ontology, dv, table, kv, checks, events, doc, code) and Split/Visual/Raw tabs", () => {
			const artifacts = GLOBAL_COMMERCE_EXPLORER_BUNDLE.artifacts;
			const expectedVizByArtifact: Record<string, string> = {
				ontology: "viz-ontology",
				final_contract: "viz-dv",
				crm: "viz-table",
				harness: "viz-kv",
				gate_static: "viz-checks",
				ledger: "viz-events",
				msa: "viz-doc",
				final_sql: "viz-code",
			};

			for (const [artId, testId] of Object.entries(expectedVizByArtifact)) {
				const art = artifacts[artId];
				expect(art).toBeDefined();
				if (art) {
					const vizHtml = renderToStaticMarkup(
						createElement(ArtifactVisualizer, { artifact: art }),
					);
					expect(vizHtml).toContain(`data-testid="${testId}"`);
				}
			}
			expect(Object.keys(EXPLORER_VIZ_LABELS)).toHaveLength(8);

			// Verify Split mode on ontology shows both Visual and Raw panes
			const ontologyArt = artifacts.ontology;
			expect(ontologyArt).toBeDefined();
			if (ontologyArt) {
				const splitModalHtml = renderToStaticMarkup(
					createElement(ArtifactFullModal, {
						artifact: ontologyArt,
						activeTab: "split",
						rbacRole: "INTERNAL_MODELER",
						onClose: () => {},
						onSelectTab: () => {},
					}),
				);
				expect(splitModalHtml).toContain('role="dialog"');
				expect(splitModalHtml).toContain('role="tablist"');
				expect(splitModalHtml).toContain('data-testid="artifact-visual-pane"');
				expect(splitModalHtml).toContain('data-testid="artifact-raw-pane"');
				expect(splitModalHtml).toContain("role: INTERNAL_MODELER");
			}

			// Verify Split mode on viz === 'code' (final_sql) suppresses duplicate Raw pane, while Raw tab shows Raw pane
			const sqlArt = artifacts.final_sql;
			expect(sqlArt).toBeDefined();
			if (sqlArt) {
				const codeSplitHtml = renderToStaticMarkup(
					createElement(ArtifactFullModal, {
						artifact: sqlArt,
						activeTab: "split",
						rbacRole: "INTERNAL_MODELER",
						onClose: () => {},
						onSelectTab: () => {},
					}),
				);
				expect(codeSplitHtml).toContain('data-testid="artifact-visual-pane"');
				expect(codeSplitHtml).not.toContain('data-testid="artifact-raw-pane"');

				const codeRawHtml = renderToStaticMarkup(
					createElement(ArtifactFullModal, {
						artifact: sqlArt,
						activeTab: "raw",
						rbacRole: "INTERNAL_MODELER",
						onClose: () => {},
						onSelectTab: () => {},
					}),
				);
				expect(codeRawHtml).not.toContain('data-testid="artifact-visual-pane"');
				expect(codeRawHtml).toContain('data-testid="artifact-raw-pane"');
			}
		});
	});

	describe("5. Wave Filter, URL Deep-Linking, Live Runs & Steward Hold Resolution", () => {
		it("filters inputs and stage counts by Wave (All, W0, W1, W2, W3) and round-trips deep-link URLs", () => {
			const store = createExplorerStore({
				bundle: GLOBAL_COMMERCE_EXPLORER_BUNDLE,
				syncBrowserUrl: false,
			});
			expect(store.getState().getVisibleInputIds()).toEqual([
				"spec",
				"ontology",
				"crm",
				"erp",
				"stripe",
				"crm2",
				"msa",
			]);

			store.getState().setWaveFilter("W0");
			expect(store.getState().getVisibleInputIds()).toEqual([
				"spec",
				"ontology",
				"crm",
				"erp",
			]);

			store.getState().setWaveFilter("W3");
			expect(store.getState().getVisibleInputIds()).toEqual([
				"spec",
				"ontology",
				"msa",
			]);

			// URL deep-linking
			store.getState().openStage(5);
			store.getState().openFullArtifact("final_contract", "viz");
			const url = store.getState().getCurrentUrl();
			expect(url).toBe("/stages/05?artifact=final_contract&tab=viz&wave=W3");

			const parsed = parseExplorerUrlState(
				"/stages/05",
				"?artifact=final_contract&tab=viz&wave=W3",
			);
			expect(parsed).toEqual({
				view: "stage",
				stageIdx: 5,
				fullId: "final_contract",
				fullTab: "viz",
				activeWave: "W3",
			});
			expect(buildExplorerUrl(parsed)).toBe(
				"/stages/05?artifact=final_contract&tab=viz&wave=W3",
			);
		});

		it("resolves a Tier-U3 Steward Hold via POST /api/v1/runs/{id}/decisions and updates live SSE events", async () => {
			let postedDecision: unknown = null;
			const mockFetch = (async (
				input: string | URL | Request,
				init?: RequestInit,
			) => {
				const url = String(input);
				if (url.endsWith("/decisions") && init?.method === "POST") {
					postedDecision = JSON.parse(String(init.body));
					return new Response(
						JSON.stringify({
							decision_id: "dec-steward-u3-approved",
							status: "ACCEPTED",
							resumed_run_id: "run_2026_01_16_commerce_4w",
							next_stage_id: "STAGE_03_SHIP_VAULT_PASS1",
							next_event_id: "evt-commerce-005",
						}),
						{ status: 200, headers: { "Content-Type": "application/json" } },
					);
				}
				if (url.endsWith("/api/v1/pipelines/runs") && init?.method === "POST") {
					return new Response(
						JSON.stringify({
							run_id: "run_live_2026_11_04",
							status: "RUNNING",
							status_url: "/api/v1/runs/run_live_2026_11_04",
							events_sse_url: "/api/v1/runs/run_live_2026_11_04/events",
						}),
						{ status: 202, headers: { "Content-Type": "application/json" } },
					);
				}
				return new Response("{}", { status: 200 });
			}) as unknown as typeof fetch;

			const store = createExplorerStore({
				bundle: GLOBAL_COMMERCE_EXPLORER_BUNDLE,
				syncBrowserUrl: false,
			});

			// Ingest a live event
			const sampleEvent = PE_DEMO_EVENTS[0] as VaultObservabilityEventDTO;
			const prevLedgerLen =
				store.getState().bundle.artifacts.ledger?.events?.length ?? 0;
			store.getState().ingestLiveObservabilityEvent(sampleEvent);
			expect(
				store.getState().bundle.artifacts.ledger?.events?.length,
			).toBe(prevLedgerLen + 1);

			// Resolve Steward Hold
			const resp = await store.getState().resolveStewardHold({
				holdId: "hold-tier-u3-rheinland-precision",
				action: "APPROVE",
				selectedCandidateId: "TAX-DE-800202",
				fetchImpl: mockFetch,
			});
			expect(resp.decision_id).toBe("dec-steward-u3-approved");
			expect(postedDecision).toMatchObject({
				hold_id: "hold-tier-u3-rheinland-precision",
				action: "APPROVE",
				selected_candidate_id: "TAX-DE-800202",
			});
			expect(
				store.getState().holdResolutions["hold-tier-u3-rheinland-precision"]
					?.decisionId,
			).toBe("dec-steward-u3-approved");

			// Trigger pipeline run
			await store.getState().triggerPipelineRun({ fetchImpl: mockFetch });
			expect(store.getState().bundle.runId).toBe("run_live_2026_11_04");
			expect(store.getState().runState).toBe("running");
		});

		it("serves deep-linked SPA routes and trailing-slash paths from Bun dev server (MAJ-01)", async () => {
			const devServer = createWorkbenchDevServer({
				port: 0,
				silent: true,
			});
			try {
				const testRoutes = [
					"/",
					"/index.html",
					"/builder",
					"/builder/",
					"/settings",
					"/settings/",
					"/ontology",
					"/ontology/",
					"/ontology/sessions/sess_123",
					"/ontology/sessions/sess.with.dots",
					"/stages/05",
					"/stages/05?artifact=final_contract",
				];
				for (const route of testRoutes) {
					const res = await fetch(`http://127.0.0.1:${devServer.port}${route}`);
					expect(res.status).toBe(200);
					expect(res.headers.get("content-type")).toContain("text/html");
				}
			} finally {
				devServer.stop(true);
			}
		});
	});
});
