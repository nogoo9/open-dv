/**
 * Unit & Integration Tests: Wave 2 — Track B2
 * Interactive Pipeline Builder (`PipelineBuilderView`, `InteractiveSpecForm`, `YamlSpecUploadEditor`)
 * and Two-Step Execution (`Stage & Validate` -> `Run Pipeline`).
 */

import { describe, expect, it } from "bun:test";
import type {
	PipelineRunHandle,
	PipelineRunRequest,
	PipelineValidateRequest,
} from "@opendv/protocol-ts";
import { type ReactElement, createElement, isValidElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { NewRunModal } from "../src/explorer/NewRunModal";
import { OpenDVWorkbenchExplorer } from "../src/explorer/OpenDVWorkbenchExplorer";
import {
	PipelineBuilderView,
	buildCurrentStagedSpec,
} from "../src/explorer/builder/PipelineBuilderView";
import {
	type StagedPipelineSpec,
	createDefaultPipelineDraft,
	createEmptyDocDraft,
	createEmptySourceDraft,
	createEmptyWaveDraft,
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

function findElementByTestId(
	node: unknown,
	testId: string,
): ReactElement<Record<string, unknown>> | null {
	if (!node || typeof node !== "object") return null;
	if (Array.isArray(node)) {
		for (const child of node) {
			const found = findElementByTestId(child, testId);
			if (found) return found;
		}
		return null;
	}
	if (isValidElement(node)) {
		const el = node as ReactElement<Record<string, unknown>>;
		if (el.props?.["data-testid"] === testId) {
			return el;
		}
		if (typeof el.type === "function") {
			const rendered = (el.type as (props: Record<string, unknown>) => unknown)(
				el.props,
			);
			const foundInRendered = findElementByTestId(rendered, testId);
			if (foundInRendered) return foundInRendered;
		}
		if (el.props?.children) {
			const found = findElementByTestId(el.props.children, testId);
			if (found) return found;
		}
	}
	return null;
}

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

	describe("2. Interactive Form Mode & Two-Step Execution in PipelineBuilderView", () => {
		it("renders semantic form, fieldsets, interactive wave/source controls, and live preview", () => {
			const draft = createDefaultPipelineDraft();
			draft.waves.push(createEmptyWaveDraft(1));

			const html = renderToStaticMarkup(
				createElement(PipelineBuilderView, {
					initialMode: "interactive",
					initialDraft: draft,
				}),
			);

			expect(html).toContain('data-testid="pipeline-builder-view"');
			expect(html).toContain("<form");
			expect(html).toContain("<fieldset");
			expect(html).toContain("<legend");
			expect(html).toContain('data-testid="btn-builder-mode-form"');
			expect(html).toContain('data-testid="btn-builder-mode-yaml"');
			expect(html).toContain('data-testid="builder-input-pipeline-id"');
			expect(html).toContain('data-testid="builder-input-project-id"');
			expect(html).toContain('data-testid="builder-input-display-name"');
			expect(html).toContain('data-testid="builder-input-domain-namespace"');
			expect(html).toContain('data-testid="builder-input-graph-a-path"');
			expect(html).toContain(
				'data-testid="builder-input-initial-contract-path"',
			);
			expect(html).toContain('data-testid="builder-select-wave-id"');
			expect(html).toContain('data-testid="builder-select-hold-policy"');
			expect(html).toContain('data-testid="builder-select-ontology-policy"');
			expect(html).toContain('data-testid="btn-builder-add-wave"');
			expect(html).toContain('data-testid="builder-wave-name-0"');
			expect(html).toContain('data-testid="builder-wave-type-0"');
			expect(html).toContain('data-testid="btn-builder-add-source-0"');
			expect(html).toContain('data-testid="btn-builder-add-doc-0"');
			expect(html).toContain('data-testid="btn-builder-remove-wave-1"');
			expect(html).toContain('data-testid="builder-live-spec-preview"');
			expect(html).toContain('data-testid="btn-builder-validate"');
			expect(html).toContain('data-testid="btn-builder-run-pipeline"');
			expect(html).toContain('autoCorrect="off"');
			expect(html).toContain('autoCapitalize="off"');
			expect(html).toContain('spellCheck="false"');
		});

		it("enforces two-step execution: Stage & Validate does NOT auto-run; Run Pipeline requires explicit trigger", async () => {
			const draft = createDefaultPipelineDraft();
			draft.pipelineId = "interactive_two_step_test";

			const validateCalls: PipelineValidateRequest[] = [];
			const runCalls: PipelineRunRequest[] = [];
			const stagedSnapshots: StagedPipelineSpec[] = [];
			const startedHandles: PipelineRunHandle[] = [];

			const tree = PipelineBuilderView({
				initialMode: "interactive",
				initialDraft: draft,
				onStageSpec: (staged) => {
					stagedSnapshots.push(staged);
				},
				onRunStarted: (handle) => {
					startedHandles.push(handle);
				},
				validatePipelineFn: async (req) => {
					validateCalls.push(req);
					return {
						stage: "INPUT_VALIDATION",
						passed: true,
						checked_items_count: 6,
						diagnostics: [
							{
								code: "INTERACTIVE_SPEC_OK",
								severity: "INFO",
								message: "Interactive pipeline spec valid.",
							},
						],
					};
				},
				createRunFn: async (req) => {
					runCalls.push(req);
					return {
						run_id: "run-interactive-202",
						status: "RUNNING",
						created_at_utc: "2026-11-07T18:00:00Z",
						status_url: "/api/v1/runs/run-interactive-202",
						events_sse_url: "/api/v1/runs/run-interactive-202/events",
						event_stream_url: "/api/v1/runs/run-interactive-202/events",
						has_initial_contract: false,
						lifecycle_stage: "COLD_START_N1",
					};
				},
			});

			const validateBtn = findElementByTestId(tree, "btn-builder-validate");
			const runBtn = findElementByTestId(tree, "btn-builder-run-pipeline");
			expect(validateBtn).not.toBeNull();
			expect(runBtn).not.toBeNull();

			// Step 1: Click "Stage & Validate Spec"
			const onValidateClick = validateBtn?.props.onClick as () => Promise<void>;
			await onValidateClick();

			expect(validateCalls).toHaveLength(1);
			expect(validateCalls[0]?.input_spec?.pipeline_id).toBe(
				"interactive_two_step_test",
			);
			expect(validateCalls[0]?.input_spec_content).toContain(
				'"pipeline_id": "interactive_two_step_test"',
			);
			expect(stagedSnapshots.at(-1)?.validated).toBe(true);
			// Crucial invariant: validating NEVER auto-runs the pipeline
			expect(runCalls).toHaveLength(0);
			expect(startedHandles).toHaveLength(0);

			// Step 2: Explicitly click "▶ Run Pipeline"
			const onRunClick = runBtn?.props.onClick as () => Promise<void>;
			await onRunClick();

			expect(runCalls).toHaveLength(1);
			expect(runCalls[0]?.input_spec?.pipeline_id).toBe(
				"interactive_two_step_test",
			);
			expect(runCalls[0]?.input_spec_content).toContain(
				'"pipeline_id": "interactive_two_step_test"',
			);
			expect(startedHandles).toHaveLength(1);
			expect(startedHandles[0]?.run_id).toBe("run-interactive-202");
		});
	});

	describe("3. Upload / YAML Spec Editor Mode & Non-Auto-Running Invariant", () => {
		const sampleYamlSpec = [
			"pipeline_id: yaml_uploaded_pipeline",
			"project_id: yaml_project",
			"display_name: Uploaded YAML Pipeline",
			"domain_namespace: finance",
			"graph_a_ontology_path: ontology/graph_a_finance.yaml",
			"waves:",
			"  - wave_index: 0",
			"    wave_name: W0_Finance",
			"    wave_type: COLD_START",
			"    sources:",
			"      - source_system: LEDGER_CORE",
			"        table_name: bronze_entries",
			"        data_path: data/entries.csv",
			"        business_key_columns: [entry_id]",
		].join("\n");

		it("renders YAML upload input, monospace textarea, and staged indicator without auto-running", () => {
			const html = renderToStaticMarkup(
				createElement(PipelineBuilderView, {
					initialMode: "yaml_upload",
					initialYamlContent: sampleYamlSpec,
					initialFileName: "finance_pipeline.yaml",
				}),
			);

			expect(html).toContain('data-testid="yaml-spec-upload-editor"');
			expect(html).toContain('data-testid="builder-upload-yaml-input"');
			expect(html).toContain('data-testid="builder-yaml-spec-path"');
			expect(html).toContain('data-testid="builder-yaml-textarea"');
			expect(html).toContain('data-testid="builder-uploaded-filename"');
			expect(html).toContain("finance_pipeline.yaml");
			expect(html).toContain('data-testid="builder-staged-indicator"');
			expect(html).toContain("✓ Spec staged (ready to validate or run)");
		});

		it("stages uploaded/edited YAML without auto-running, and sends input_spec_content to /api/v1/pipelines/runs on Run click", async () => {
			const fetchUrls: Array<{ url: string; body: Record<string, unknown> }> =
				[];
			const stagedEvents: StagedPipelineSpec[] = [];

			const mockFetch = (async (
				input: RequestInfo | URL,
				init?: RequestInit,
			) => {
				const url = String(input);
				const body = init?.body
					? (JSON.parse(String(init.body)) as Record<string, unknown>)
					: {};
				fetchUrls.push({ url, body });

				if (url.endsWith("/api/v1/pipelines/validate")) {
					return new Response(
						JSON.stringify({
							stage: "INPUT_VALIDATION",
							passed: true,
							checked_items_count: 4,
							diagnostics: [],
						}),
						{ status: 200, headers: { "Content-Type": "application/json" } },
					);
				}

				return new Response(
					JSON.stringify({
						run_id: "run-yaml-upload-202",
						status: "RUNNING",
						created_at_utc: "2026-11-07T18:05:00Z",
						status_url: "/api/v1/runs/run-yaml-upload-202",
						events_sse_url: "/api/v1/runs/run-yaml-upload-202/events",
						event_stream_url: "/api/v1/runs/run-yaml-upload-202/events",
						has_initial_contract: false,
						lifecycle_stage: "COLD_START_N1",
					}),
					{ status: 202, headers: { "Content-Type": "application/json" } },
				);
			}) as unknown as typeof fetch;

			const tree = PipelineBuilderView({
				initialMode: "yaml_upload",
				initialYamlContent: sampleYamlSpec,
				initialFileName: "finance_pipeline.yaml",
				fetchImpl: mockFetch,
				onStageSpec: (staged) => {
					stagedEvents.push(staged);
				},
			});

			// Simulate uploading a file on the file input: should ONLY stage, never call fetch
			const uploadInput = findElementByTestId(
				tree,
				"builder-upload-yaml-input",
			);
			expect(uploadInput).not.toBeNull();
			const onUploadChange = uploadInput?.props.onChange as (
				e: unknown,
			) => void;
			onUploadChange({
				target: {
					files: [{ name: "uploaded_spec.yaml", content: sampleYamlSpec }],
				},
			});

			expect(fetchUrls).toHaveLength(0);
			expect(stagedEvents).toHaveLength(1);
			expect(stagedEvents[0]?.inputSpecContent).toBe(sampleYamlSpec);

			// Now click "▶ Run Pipeline" (btn-builder-run-pipeline)
			const runBtn = findElementByTestId(tree, "btn-builder-run-pipeline");
			expect(runBtn).not.toBeNull();
			const onRunClick = runBtn?.props.onClick as () => Promise<void>;
			await onRunClick();

			expect(fetchUrls).toHaveLength(1);
			expect(fetchUrls[0]?.url).toBe("/api/v1/pipelines/runs");
			expect(fetchUrls[0]?.body.input_spec_content).toBe(sampleYamlSpec);
			expect(fetchUrls[0]?.body.steward_hold_policy).toBe("pause");
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
			const staged = buildCurrentStagedSpec({
				mode: "interactive",
				draft: createDefaultPipelineDraft(),
				yamlSpecPath: "",
				yamlContent: "",
				yamlContractPath: "",
				yamlWaveId: "W0",
				yamlHoldPolicy: "pause",
				validated: true,
			});

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

			// 3. PipelineBuilderView renders failed validation diagnostics and submit error alerts
			const errorHtml = renderToStaticMarkup(
				createElement(PipelineBuilderView, {
					initialValidationReport: {
						stage: "INPUT_VALIDATION",
						passed: false,
						checked_items_count: 2,
						diagnostics: [
							{
								code: "MISSING_ONTOLOGY",
								severity: "ERROR",
								message: "graph_a_ontology_path does not exist",
							},
						],
					},
					initialSubmitError: "Pipeline run rejected by server (422)",
				}),
			);
			expect(errorHtml).toContain('data-testid="validation-diagnostics"');
			expect(errorHtml).toContain('data-testid="validation-status"');
			expect(errorHtml).toContain("INVALID");
			expect(errorHtml).toContain("MISSING_ONTOLOGY");
			expect(errorHtml).toContain('data-testid="run-error-banner"');
			expect(errorHtml).toContain("Pipeline run rejected by server (422)");
		});
	});

	describe("5. Shell & URL Router Integration (Explorer | Pipeline Builder | Settings)", () => {
		it("routes /builder and /settings URLs and renders top navigation tabs, PipelineRunProgressBar, PipelineBuilderView, and PlatformSettingsView", () => {
			expect(parseExplorerUrlState("/builder").view).toBe("builder");
			expect(parseExplorerUrlState("/settings").view).toBe("settings");

			const store = createExplorerStore({ syncBrowserUrl: false });
			const explorerHtml = renderToStaticMarkup(
				createElement(OpenDVWorkbenchExplorer, { store }),
			);
			expect(explorerHtml).toContain('data-testid="nav-tab-explorer"');
			expect(explorerHtml).toContain('data-testid="nav-tab-builder"');
			expect(explorerHtml).toContain('data-testid="nav-tab-settings"');
			expect(explorerHtml).toContain('data-testid="pipeline-run-progress-bar"');

			store.getState().goBuilder();
			expect(store.getState().view).toBe("builder");
			expect(store.getState().getCurrentUrl()).toBe("/builder");
			const builderHtml = renderToStaticMarkup(
				createElement(OpenDVWorkbenchExplorer, { store }),
			);
			expect(builderHtml).toContain('data-testid="pipeline-builder-view"');

			store.getState().goSettings();
			expect(store.getState().view).toBe("settings");
			expect(store.getState().getCurrentUrl()).toBe("/settings");
			const settingsHtml = renderToStaticMarkup(
				createElement(OpenDVWorkbenchExplorer, { store }),
			);
			expect(settingsHtml).toContain('data-testid="platform-settings-view"');
		});
	});
});
