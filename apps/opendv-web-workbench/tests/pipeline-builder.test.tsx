/**
 * Unit & Integration Tests: Wave 2 — Track B2
 * Spec Builder Utils, NewRunModal Staged Spec Submission, Shell Routing & Deep-Link Hand-Off.
 */

import { describe, expect, it } from "bun:test";
import type {
	PipelineRunRequest,
	PipelineValidateRequest,
} from "@opendv/protocol-ts";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { NewRunModal } from "../src/explorer/NewRunModal";
import { OpenDVWorkbenchExplorer } from "../src/explorer/OpenDVWorkbenchExplorer";
import {
	createDefaultPipelineDraft,
	createEmptyDocDraft,
	createEmptySourceDraft,
	createEmptyWaveDraft,
	createOntologyStudioPipelineDraft,
	draftToInputSpecObject,
	draftToStagedSpec,
	draftToYamlOrJsonText,
} from "../src/explorer/builder/specBuilderUtils";
import {
	createExplorerStore,
	parseExplorerUrlState,
} from "../src/explorer/explorer-store";
import {
	executeSubmitRun,
	executeValidate,
} from "../src/explorer/modals/useNewRunActions";
import { buildExplorerUrl } from "../src/explorer/url-router";

describe("Wave 2 — Track B2: Interactive Pipeline Builder & Two-Step Execution", () => {
	describe("1. Interactive Pipeline Draft & Spec Serialization (specBuilderUtils.ts)", () => {
		it("creates a starter draft and converts multi-wave relational + unstructured draft into PipelineInputSpec", () => {
			const draft = createDefaultPipelineDraft();
			expect(draft.pipelineId).toBe("global_commerce_vault_run");
			expect(draft.waves).toHaveLength(1);
			expect(draft.waves[0]?.sources).toHaveLength(1);

			// Add Wave 1 with an extra relational source and an unstructured document
			const wave1 = createEmptyWaveDraft(1);
			wave1.waveName = "W1_ERP_And_Contracts";
			wave1.waveType = "UNSTRUCTURED_ONBOARDING";
			wave1.sources = [
				{
					...createEmptySourceDraft(),
					sourceSystem: "ERP_SAP",
					tableName: "bronze_invoices",
					dataPath: "data/w1_invoices.csv",
					targetHubName: "hub_invoice",
					businessKeyColumns: "invoice_id, company_code",
				},
			];
			wave1.unstructuredDocuments = [
				{
					...createEmptyDocDraft(),
					docId: "doc_contract_99",
					documentPath: "docs/contract_99.md",
					targetHubName: "hub_customer",
				},
			];

			const updatedDraft = {
				...draft,
				pipelineId: "custom_commerce_pipeline",
				projectId: "proj_commerce",
				displayName: "Custom Commerce Pipeline",
				domainNamespace: "commerce_v2",
				initialContractPath: "contracts/v1_base.yaml",
				waveId: "W1",
				stewardHoldPolicy: "auto_approve_top_candidate" as const,
				ontologyPolicy: "recommend" as const,
				waves: [...draft.waves, wave1],
			};

			const specObj = draftToInputSpecObject(updatedDraft);
			expect(specObj.pipeline_id).toBe("custom_commerce_pipeline");
			expect(specObj.project_id).toBe("proj_commerce");
			expect(specObj.display_name).toBe("Custom Commerce Pipeline");
			expect(specObj.domain_namespace).toBe("commerce_v2");
			expect(specObj.initial_contract_path).toBe("contracts/v1_base.yaml");
			expect(specObj.wave_id).toBe("W1");
			expect(specObj.ontology_policy).toBe("recommend");

			const waves = specObj.waves as Array<Record<string, unknown>>;
			expect(waves).toHaveLength(2);
			expect(waves[1]?.wave_name).toBe("W1_ERP_And_Contracts");
			const w1Sources = waves[1]?.sources as Array<Record<string, unknown>>;
			expect(w1Sources[0]?.business_key_columns).toEqual([
				"invoice_id",
				"company_code",
			]);
			const w1Docs = waves[1]?.unstructured_documents as Array<
				Record<string, unknown>
			>;
			expect(w1Docs[0]?.doc_id).toBe("doc_contract_99");

			const previewText = draftToYamlOrJsonText(updatedDraft);
			expect(previewText).toContain(
				'"pipeline_id": "custom_commerce_pipeline"',
			);

			const staged = draftToStagedSpec(updatedDraft, true);
			expect(staged.mode).toBe("interactive");
			expect(staged.validated).toBe(true);
			expect(staged.stewardHoldPolicy).toBe("auto_approve_top_candidate");
		});
	});

	describe("4. NewRunModal Staged Spec & Uploaded Spec Submission", () => {
		it("forwards specFileContent and parsed JSON input_spec in executeValidate and executeSubmitRun", async () => {
			const jsonSpec = JSON.stringify({
				pipeline_id: "modal_json_spec",
				project_id: "modal_proj",
			});

			let validatedReq: PipelineValidateRequest | undefined;
			await executeValidate({
				specPath: "",
				specFileContent: jsonSpec,
				contractPath: "",
				waveId: "W0",
				holdPolicy: "pause",
				validatePipelineFn: async (req) => {
					validatedReq = req;
					return {
						stage: "INPUT_VALIDATION",
						passed: true,
						checked_items_count: 1,
						diagnostics: [],
					};
				},
			});
			expect(validatedReq?.input_spec_content).toBe(jsonSpec);
			expect(validatedReq?.input_spec?.pipeline_id).toBe("modal_json_spec");

			let submittedReq: PipelineRunRequest | undefined;
			await executeSubmitRun({
				specPath: "",
				specFileContent: jsonSpec,
				contractPath: "",
				waveId: "W0",
				holdPolicy: "pause",
				createRunFn: async (req) => {
					submittedReq = req;
					return {
						run_id: "run-modal-202",
						status: "RUNNING",
						created_at_utc: "2026-11-07T18:10:00Z",
						status_url: "/api/v1/runs/run-modal-202",
						events_sse_url: "/api/v1/runs/run-modal-202/events",
						event_stream_url: "/api/v1/runs/run-modal-202/events",
						has_initial_contract: false,
						lifecycle_stage: "COLD_START_N1",
					};
				},
			});
			expect(submittedReq?.input_spec_content).toBe(jsonSpec);
			expect(submittedReq?.input_spec?.pipeline_id).toBe("modal_json_spec");
		});

		it("renders staged spec banner and Interactive Builder button in NewRunModal", () => {
			const staged = draftToStagedSpec(createDefaultPipelineDraft(), true);

			const html = renderToStaticMarkup(
				createElement(NewRunModal, {
					isOpen: true,
					onClose: () => {},
					stagedSpec: staged,
					onOpenBuilderView: () => {},
				}),
			);

			expect(html).toContain('data-testid="modal-staged-spec-banner"');
			expect(html).toContain('data-testid="btn-modal-open-builder"');
		});

		it("handles validation failure diagnostics, submit HTTP errors, and missing fetch fallback cleanly", async () => {
			// 1a. executeValidate with HTTP 422 returns structured VALIDATION_FAILED diagnostic
			const http422Fetch = (async () =>
				new Response(
					JSON.stringify({
						error_code: "SPEC_PARSE_ERROR",
						detail: "Invalid YAML indentation at line 4",
					}),
					{
						status: 422,
						headers: { "Content-Type": "application/json" },
					},
				)) as unknown as typeof fetch;

			const failedReport = await executeValidate({
				specPath: "examples/pipeline.yaml",
				contractPath: "",
				waveId: "W0",
				holdPolicy: "pause",
				fetchImpl: http422Fetch,
			});
			expect(failedReport.passed).toBe(false);
			expect(failedReport.diagnostics[0]?.code).toBe("SPEC_PARSE_ERROR");
			expect(failedReport.diagnostics[0]?.message).toContain(
				"Invalid YAML indentation",
			);

			// 1b. executeValidate without validatePipelineFn or globalThis.fetch returns BACKEND_UNAVAILABLE
			const origFetch = globalThis.fetch;
			try {
				(globalThis as unknown as { fetch: undefined }).fetch = undefined;
				const noBackendReport = await executeValidate({
					specPath: "examples/pipeline.yaml",
					contractPath: "",
					waveId: "W0",
					holdPolicy: "pause",
				});
				expect(noBackendReport.passed).toBe(false);
				expect(noBackendReport.diagnostics[0]?.code).toBe(
					"BACKEND_UNAVAILABLE",
				);
			} finally {
				globalThis.fetch = origFetch;
			}

			// 2. executeSubmitRun throws on HTTP error
			const failingFetch = (async () =>
				new Response(JSON.stringify({ detail: "Invalid spec syntax" }), {
					status: 422,
					headers: { "Content-Type": "application/json" },
				})) as unknown as typeof fetch;

			await expect(
				executeSubmitRun({
					specPath: "examples/pipeline.yaml",
					contractPath: "",
					waveId: "W0",
					holdPolicy: "pause",
					fetchImpl: failingFetch,
				}),
			).rejects.toThrow("Invalid spec syntax");
		});
	});

	describe("5. Shell & URL Router Integration (4-Tab M3 Shell)", () => {
		it("routes /builder and /settings URLs and renders 4-tab navigation, PipelineRunProgressBar, OntologyWizardShell, and PlatformSettingsView", () => {
			const parsedBuilder = parseExplorerUrlState("/builder");
			expect(parsedBuilder.view).toBe("ontology");
			expect(parsedBuilder.ontologySubView).toBe("builder");
			expect(parseExplorerUrlState("/settings").view).toBe("settings");

			const store = createExplorerStore({
				startView: "pipeline",
				syncBrowserUrl: false,
			});
			const explorerHtml = renderToStaticMarkup(
				createElement(OpenDVWorkbenchExplorer, { store }),
			);
			expect(explorerHtml).toContain('data-testid="nav-tab-ontology"');
			expect(explorerHtml).toContain('data-testid="nav-tab-dv-schema"');
			expect(explorerHtml).toContain('data-testid="nav-tab-explorer"');
			expect(explorerHtml).toContain('data-testid="nav-tab-settings"');
			expect(explorerHtml).toContain('data-testid="pipeline-run-progress-bar"');

			store.getState().goBuilder();
			expect(store.getState().view).toBe("ontology");
			expect(store.getState().ontologySubView).toBe("builder");
			expect(store.getState().getCurrentUrl()).toBe("/builder");
			const builderHtml = renderToStaticMarkup(
				createElement(OpenDVWorkbenchExplorer, { store }),
			);
			expect(builderHtml).toContain('data-testid="ontology-wizard-shell"');

			store.getState().goSettings();
			expect(store.getState().view).toBe("settings");
			expect(store.getState().getCurrentUrl()).toBe("/settings");
			const settingsHtml = renderToStaticMarkup(
				createElement(OpenDVWorkbenchExplorer, { store }),
			);
			expect(settingsHtml).toContain('data-testid="platform-settings-view"');
		});
	});

	describe("6. Feature 10: 1-Click Pipeline Builder Deep-Link Hand-Off", () => {
		it("parses deep-link query parameters from /builder URL", () => {
			const parsed = parseExplorerUrlState(
				"/builder",
				"?session_id=am-walkthrough&source=ontology_studio&domain_namespace=asset_management&pipeline_id=pl_am_walkthrough",
			);
			expect(parsed.view).toBe("ontology");
			expect(parsed.ontologySubView).toBe("builder");
			expect(parsed.builderSessionId).toBe("am-walkthrough");
			expect(parsed.builderSource).toBe("ontology_studio");
			expect(parsed.builderDomain).toBe("asset_management");
			expect(parsed.builderPipelineId).toBe("pl_am_walkthrough");
		});

		it("round-trips builder query parameters through buildExplorerUrl", () => {
			const url = buildExplorerUrl({
				view: "builder",
				stageIdx: 5,
				fullId: null,
				fullTab: "split",
				activeWave: "all",
				builderSessionId: "sess-123",
				builderSource: "ontology_studio",
				builderDomain: "asset_management",
				builderPipelineId: "pl_test",
			});
			expect(url).toContain("/builder?");
			expect(url).toContain("session_id=sess-123");
			expect(url).toContain("source=ontology_studio");
			expect(url).toContain("domain_namespace=asset_management");
			expect(url).toContain("pipeline_id=pl_test");
		});

		it("creates Asset Management pipeline draft with target Data Vault 2.0 hubs", () => {
			const draft = createOntologyStudioPipelineDraft({
				sessionId: "sess-custom",
				source: "ontology_studio",
				domainNamespace: "asset_management",
				pipelineId: "pl_custom",
			});

			expect(draft.pipelineId).toBe("pl_custom");
			expect(draft.domainNamespace).toBe("asset_management");
			expect(draft.graphAOntologyPath).toContain("am_graph_a_ontology.yaml");
			expect(draft.ontologyPolicy).toBe("recommend");

			const sources = draft.waves[0]?.sources ?? [];
			const targetHubs = sources.map((s) => s.targetHubName);
			expect(targetHubs).toContain("hub_security");
			expect(targetHubs).toContain("hub_investor");
			expect(targetHubs).toContain("hub_rate_index");

			const docs = draft.waves[0]?.unstructuredDocuments ?? [];
			expect(docs.some((d) => d.targetHubName === "hub_document")).toBe(true);
		});
	});
});
