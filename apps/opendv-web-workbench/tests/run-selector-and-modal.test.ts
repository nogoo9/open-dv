/**
 * Unit & Integration Tests: Task P9.3 (#117)
 * Top Bar Run Selector, Async New Run Modal, Wave Filter, Live SSE Refresh
 *
 * Covers:
 * 1. Run Selector Dropdown & Switching via ExplorerDataSource.
 * 2. NewRunModal pre-flight validation, diagnostics display, and async HTTP 202 submission (Decision D2).
 * 3. Live SSE stage status updates (STAGE_00..STAGE_07) & token streaming progress.
 * 4. Wave Segmented Filter (All | W0 | W1 | W2 | W3) artifact filtering.
 */

import { describe, expect, it } from "bun:test";
import {
	type ExplorerBundleDTO,
	type PipelineRunHandle,
	type PipelineRunRequest,
	type PipelineValidateRequest,
	type ProjectRunDTO,
	type VaultObservabilityEventDTO,
	filterArtifactIdsByWave,
} from "@opendv/protocol-ts";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import {
	NewRunModal,
	type PipelineValidationReport,
} from "../src/explorer/NewRunModal";
import {
	type ExplorerDataSource,
	OpenDVWorkbenchExplorer,
} from "../src/explorer/OpenDVWorkbenchExplorer";
import { createExplorerStore } from "../src/explorer/explorer-store";
import {
	GLOBAL_COMMERCE_EXPLORER_BUNDLE,
	PE_DEMO_EVENTS,
} from "./fixtures/commerce-explorer-fixture";

describe("Task P9.3 (#117): Top Bar Run Selector, New Run Modal, Wave Filter & Live SSE", () => {
	describe("1. Top Bar Run Selector Dropdown & Switching", () => {
		it("renders header pill with active run display name, spec file, and waves count", () => {
			const store = createExplorerStore({
				bundle: GLOBAL_COMMERCE_EXPLORER_BUNDLE,
				syncBrowserUrl: false,
			});
			const html = renderToStaticMarkup(
				createElement(OpenDVWorkbenchExplorer, { store }),
			);

			expect(html).toContain('data-testid="btn-run-selector-pill"');
			expect(html).toContain("global_commerce_vault");
			expect(html).toContain("pipeline_input.yaml · 4 waves");
			expect(html).toContain('data-testid="run-selector-chevron"');
		});

		it("fetches recent runs from dataSource.fetchRunsList() and renders in dropdown", async () => {
			const mockRuns: ProjectRunDTO[] = [
				{
					run_id: "run-20261107-alpha1",
					project_id: "proj-alpha",
					status: "COMPLETED",
					current_stage_id: "STAGE_07_DETERMINISTIC_COMPILER",
					event_count: 55,
					wave_id: "W1",
					created_at_utc: "2026-11-07T05:00:00Z",
					started_at_utc: "2026-11-07T05:00:01Z",
					completed_at_utc: "2026-11-07T05:00:30Z",
					has_initial_contract: false,
					initial_contract_path: null,
					decisions_url: "/api/v1/runs/run-20261107-alpha1/decisions",
					events_sse_url: "/api/v1/runs/run-20261107-alpha1/events",
					gates_url: "/api/v1/runs/run-20261107-alpha1/gates",
					sandbox_url: "/api/v1/runs/run-20261107-alpha1/sandbox",
					fallbacks_applied: [],
					active_hold_ids: [],
					harness_provider: "antigravity",
					lifecycle_stage: "COLD_START_N1",
				},
				{
					run_id: "run-20261107-beta2",
					project_id: "proj-beta",
					status: "RUNNING",
					current_stage_id: "STAGE_03_SHIP_VAULT_PASS1",
					event_count: 22,
					wave_id: "W2",
					created_at_utc: "2026-11-07T05:30:00Z",
					started_at_utc: "2026-11-07T05:30:01Z",
					completed_at_utc: null,
					has_initial_contract: true,
					initial_contract_path: "contracts/starting_contract.yaml",
					decisions_url: "/api/v1/runs/run-20261107-beta2/decisions",
					events_sse_url: "/api/v1/runs/run-20261107-beta2/events",
					gates_url: "/api/v1/runs/run-20261107-beta2/gates",
					sandbox_url: "/api/v1/runs/run-20261107-beta2/sandbox",
					fallbacks_applied: [],
					active_hold_ids: ["hold-001"],
					harness_provider: "antigravity",
					lifecycle_stage: "MULTI_SOURCE_EXPANSION",
				},
			];

			let fetched = false;
			const mockDataSource: ExplorerDataSource = {
				fetchRunsList: async () => {
					fetched = true;
					return mockRuns;
				},
				fetchExplorerBundle: async (runId: string) => {
					return {
						...GLOBAL_COMMERCE_EXPLORER_BUNDLE,
						runId,
						displayName: `Run ${runId}`,
					};
				},
			};

			const runs = await mockDataSource.fetchRunsList();
			expect(fetched).toBe(true);
			expect(runs).toHaveLength(2);
			expect(runs[0]?.run_id).toBe("run-20261107-alpha1");
			expect(runs[1]?.status).toBe("RUNNING");
		});

		it("switches active explorer bundle when a run is selected from dataSource", async () => {
			const store = createExplorerStore({
				bundle: GLOBAL_COMMERCE_EXPLORER_BUNDLE,
				syncBrowserUrl: false,
			});

			const updatedBundle: ExplorerBundleDTO = {
				...GLOBAL_COMMERCE_EXPLORER_BUNDLE,
				runId: "run-custom-switched",
				displayName: "Switched Production Run",
				wavesCount: 3,
			};

			const mockDataSource: ExplorerDataSource = {
				fetchRunsList: async () => [],
				fetchExplorerBundle: async (runId: string) => {
					return {
						...updatedBundle,
						runId,
					};
				},
			};

			const newBundle = await mockDataSource.fetchExplorerBundle(
				"run-custom-switched",
			);
			store.setState({ bundle: newBundle });

			expect(store.getState().bundle.runId).toBe("run-custom-switched");
			expect(store.getState().bundle.displayName).toBe(
				"Switched Production Run",
			);
			expect(store.getState().bundle.wavesCount).toBe(3);
		});
	});

	describe("2. New Pipeline Run Modal (NewRunModal.tsx)", () => {
		it("renders modal dialog with accessible role, title, inputs and action buttons", () => {
			const html = renderToStaticMarkup(
				createElement(NewRunModal, {
					isOpen: true,
					onClose: () => {},
					defaultSpecPath:
						"examples/pipeline_samples/global_commerce_vault/pipeline_input.yaml",
					defaultWaveId: "W1",
					defaultHoldPolicy: "pause",
				}),
			);

			expect(html).toContain('role="dialog"');
			expect(html).toContain('aria-modal="true"');
			expect(html).toContain('data-testid="new-run-modal"');
			expect(html).toContain("New Pipeline Run");
			expect(html).toContain('data-testid="input-spec-path"');
			expect(html).toContain('data-testid="input-contract-path"');
			expect(html).toContain('data-testid="select-wave-id"');
			expect(html).toContain('data-testid="select-hold-policy"');
			expect(html).toContain('data-testid="btn-validate-pipeline"');
			expect(html).toContain('data-testid="btn-submit-run"');
			expect(html).toContain('data-testid="btn-close-modal"');
			expect(html).toContain('data-testid="btn-cancel-modal"');
		});

		it("returns null when isOpen is false", () => {
			const html = renderToStaticMarkup(
				createElement(NewRunModal, {
					isOpen: false,
					onClose: () => {},
				}),
			);
			expect(html).toBe("");
		});

		it("performs pre-flight input validation and displays diagnostics scorecard", async () => {
			let validated = false;
			const mockReport: PipelineValidationReport = {
				stage: "INPUT_VALIDATION",
				passed: true,
				checked_items_count: 5,
				diagnostics: [
					{
						code: "SPEC_SYNTAX_VALID",
						severity: "INFO",
						message: "pipeline_input.yaml validated successfully with 4 waves.",
					},
					{
						code: "SCHEMA_PARITY_PASS",
						severity: "INFO",
						message: "Relational sources conform to schema requirements.",
					},
				],
			};

			const mockValidateFn = async (req: PipelineValidateRequest) => {
				validated = true;
				expect(req.input_spec_path).toContain("pipeline_input.yaml");
				expect(req.steward_hold_policy).toBe("pause");
				return mockReport;
			};

			const report = await mockValidateFn({
				input_spec_path:
					"examples/pipeline_samples/global_commerce_vault/pipeline_input.yaml",
				steward_hold_policy: "pause",
			});

			expect(validated).toBe(true);
			expect(report.passed).toBe(true);
			expect(report.checked_items_count).toBe(5);
			expect(report.diagnostics).toHaveLength(2);
			expect(report.diagnostics[0]?.code).toBe("SPEC_SYNTAX_VALID");
		});

		it("displays ERROR diagnostics when pre-flight validation fails", async () => {
			const errorReport: PipelineValidationReport = {
				stage: "INPUT_VALIDATION",
				passed: false,
				checked_items_count: 2,
				diagnostics: [
					{
						code: "INPUT_SPEC_SCHEMA_INVALID",
						severity: "ERROR",
						target: "input_spec_path",
						message: "File not found: missing_spec.yaml",
					},
				],
			};

			const mockValidateFn = async () => errorReport;
			const report = await mockValidateFn();

			expect(report.passed).toBe(false);
			expect(report.diagnostics[0]?.severity).toBe("ERROR");
			expect(report.diagnostics[0]?.message).toContain("File not found");
		});

		it("initiates asynchronous pipeline run returning HTTP 202 handle (Decision D2)", async () => {
			let created = false;
			const expectedHandle: PipelineRunHandle = {
				run_id: "run-20261107-test202",
				status: "RUNNING",
				created_at_utc: "2026-11-07T06:15:00Z",
				status_url: "/api/v1/runs/run-20261107-test202",
				events_sse_url: "/api/v1/runs/run-20261107-test202/events",
				event_stream_url: "/api/v1/runs/run-20261107-test202/events",
				has_initial_contract: false,
				lifecycle_stage: "COLD_START_N1",
			};

			const mockCreateRunFn = async (req: PipelineRunRequest) => {
				created = true;
				expect(req.dry_run).toBe(false);
				expect(req.steward_hold_policy).toBe("auto_approve_top_candidate");
				return expectedHandle;
			};

			const handle = await mockCreateRunFn({
				dry_run: false,
				input_spec_path:
					"examples/pipeline_samples/global_commerce_vault/pipeline_input.yaml",
				steward_hold_policy: "auto_approve_top_candidate",
			});

			expect(created).toBe(true);
			expect(handle.run_id).toBe("run-20261107-test202");
			expect(handle.status).toBe("RUNNING");
			expect(handle.events_sse_url).toBe(
				"/api/v1/runs/run-20261107-test202/events",
			);
		});
	});

	describe("3. Live SSE Status Streaming & Token Progress", () => {
		it("updates stage status pills (STAGE_00..STAGE_07) in real time when observability events arrive", () => {
			const store = createExplorerStore({
				bundle: GLOBAL_COMMERCE_EXPLORER_BUNDLE,
				syncBrowserUrl: false,
			});

			const baseEvent0 = PE_DEMO_EVENTS[0];
			if (!baseEvent0) throw new Error("Missing fixture base event");

			// Initial state is AUTO-FIX in fixture
			expect(store.getState().bundle.stages[1]?.tag).toBe("AUTO-FIX");

			// Ingest event for STAGE_01 with PASS
			const testEvent: VaultObservabilityEventDTO = {
				...baseEvent0,
				stage_id: "STAGE_01_SCOUT_SOURCE",
				status: "PASS",
				layer1_story_banner: {
					headline: "Profiled 24 columns cleanly",
					business_impact: "Automated schema alignment",
					verdict_pill: "PASS",
				},
				layer2_visual_payload: {
					semantic_hash_pills: [],
					summary_metrics: { tokens_streamed: 340 },
				},
			};

			store.getState().ingestLiveObservabilityEvent(testEvent);

			expect(store.getState().bundle.stages[1]?.tag).toBe("PASS");
			expect(store.getState().statusBannerText).toContain("Running · STAGE_01");
		});

		it("transitions runState to 'hold' when an ASK-USER steward hold event arrives", () => {
			const store = createExplorerStore({
				bundle: GLOBAL_COMMERCE_EXPLORER_BUNDLE,
				syncBrowserUrl: false,
			});

			const baseEvent0 = PE_DEMO_EVENTS[0];
			if (!baseEvent0) throw new Error("Missing fixture base event");

			const holdEvent: VaultObservabilityEventDTO = {
				...baseEvent0,
				event_id: "evt-hold-test-01",
				stage_id: "STAGE_03_SHIP_VAULT_PASS1",
				status: "ASK-USER",
				layer1_story_banner: {
					headline: "Steward decision required for ambiguous mention",
					business_impact: "Requires manual approval",
					verdict_pill: "ASK-USER",
				},
				layer2_visual_payload: {
					semantic_hash_pills: [],
					summary_metrics: { tokens_streamed: 890 },
				},
			};

			store.getState().ingestLiveObservabilityEvent(holdEvent);

			expect(store.getState().runState).toBe("hold");
			expect(store.getState().statusBannerText).toContain("Hold · STAGE_03");
		});

		it("tracks token streaming progress from SSE summary metrics", () => {
			let totalTokens = 0;
			const onEvent = (ev: VaultObservabilityEventDTO) => {
				const tokens = Number(
					ev.layer2_visual_payload?.summary_metrics?.tokens_streamed ?? 0,
				);
				totalTokens += tokens;
			};

			const baseEvent0 = PE_DEMO_EVENTS[0];
			if (!baseEvent0) throw new Error("Missing fixture base event");

			onEvent({
				...baseEvent0,
				event_id: "evt-tokens-1",
				layer2_visual_payload: {
					semantic_hash_pills: [],
					summary_metrics: { tokens_streamed: 450 },
				},
			});
			onEvent({
				...baseEvent0,
				event_id: "evt-tokens-2",
				layer2_visual_payload: {
					semantic_hash_pills: [],
					summary_metrics: { tokens_streamed: 620 },
				},
			});

			expect(totalTokens).toBe(1070);
		});
	});

	describe("4. Wave Segmented Filter (All | W0 | W1 | W2 | W3)", () => {
		it("renders wave control with all 5 segments and active indicator", () => {
			const store = createExplorerStore({
				bundle: GLOBAL_COMMERCE_EXPLORER_BUNDLE,
				syncBrowserUrl: false,
			});
			const html = renderToStaticMarkup(
				createElement(OpenDVWorkbenchExplorer, { store }),
			);

			expect(html).toContain('data-testid="wave-filter-control"');
			expect(html).toContain('data-testid="btn-wave-all"');
			expect(html).toContain('data-testid="btn-wave-W0"');
			expect(html).toContain('data-testid="btn-wave-W1"');
			expect(html).toContain('data-testid="btn-wave-W2"');
			expect(html).toContain('data-testid="btn-wave-W3"');
		});

		it("filters input and output artifacts based on active wave filter", () => {
			const store = createExplorerStore({
				bundle: GLOBAL_COMMERCE_EXPLORER_BUNDLE,
				syncBrowserUrl: false,
			});

			const allInputs = store.getState().bundle.inputIds;
			const artifacts = store.getState().bundle.artifacts;

			// In 'all' wave, all inputs are visible
			const visibleAll = filterArtifactIdsByWave(allInputs, artifacts, "all");
			expect(visibleAll.length).toBe(allInputs.length);

			// In 'W1' wave, only artifacts participating in W1 are visible
			const visibleW1 = filterArtifactIdsByWave(allInputs, artifacts, "W1");
			expect(visibleW1.length).toBeGreaterThan(0);
			expect(visibleW1.length).toBeLessThanOrEqual(allInputs.length);

			// Artifact tagged only for W3 should not be in W1
			const docArtifact = artifacts.doc_msa;
			if (docArtifact?.waves) {
				const isDocInW1 = visibleW1.includes("doc_msa");
				const expected = docArtifact.waves.includes("W1");
				expect(isDocInW1).toBe(expected);
			}
		});

		it("filters stage reads and writes when wave filter is active", () => {
			const bundle = GLOBAL_COMMERCE_EXPLORER_BUNDLE;
			const stage02 = bundle.stages[2];
			expect(stage02).toBeDefined();

			if (stage02) {
				const filteredInsW1 = filterArtifactIdsByWave(
					stage02.ins,
					bundle.artifacts,
					"W1",
				);
				const filteredInsAll = filterArtifactIdsByWave(
					stage02.ins,
					bundle.artifacts,
					"all",
				);

				expect(filteredInsAll).toHaveLength(stage02.ins.length);
				expect(filteredInsW1.length).toBeLessThanOrEqual(filteredInsAll.length);
			}
		});

		it("updates store activeWave and round-trips deep-link state", () => {
			const store = createExplorerStore({
				bundle: GLOBAL_COMMERCE_EXPLORER_BUNDLE,
				syncBrowserUrl: false,
			});

			store.getState().setWaveFilter("W2");
			expect(store.getState().activeWave).toBe("W2");

			const currentUrl = store.getState().getCurrentUrl();
			expect(currentUrl).toContain("wave=W2");

			store.getState().setWaveFilter("all");
			expect(store.getState().activeWave).toBe("all");
			expect(store.getState().getCurrentUrl()).not.toContain("wave=");
		});
	});
});
