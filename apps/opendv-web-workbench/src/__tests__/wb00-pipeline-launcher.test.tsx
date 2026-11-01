/**
 * Component & Behavioral Unit Tests for WB-00 Interactive Agentic Pipeline Launcher & Report Inspector
 * (`src/__tests__/wb00-pipeline-launcher.test.tsx`).
 */

import { afterEach, beforeEach, describe, expect, it } from "bun:test";
import { PE_DEMO_EVENTS } from "@opendv/protocol-ts";
import { type ReactElement, isValidElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import {
	type AgenticPipelineReport,
	EMPTY_INPUT_PATH_VALIDATION_ERROR,
	WB00CommandCenter,
	createPipelineLauncherStore,
	resetPipelineLauncherStore,
} from "../views/wb00/WB00CommandCenter";

interface ElementWithTestIdProps {
	"data-testid"?: string;
	value?: string;
	placeholder?: string;
	onChange?: (ev: { target: { value: string } }) => void;
	onClick?: () => void;
	children?: unknown;
}

function findElementByTestId(
	node: unknown,
	testId: string,
): ReactElement<ElementWithTestIdProps> | null {
	if (!node) {
		return null;
	}
	if (Array.isArray(node)) {
		for (const item of node) {
			const found = findElementByTestId(item, testId);
			if (found) {
				return found;
			}
		}
		return null;
	}
	if (isValidElement(node)) {
		const element = node as ReactElement<ElementWithTestIdProps>;
		if (element.props["data-testid"] === testId) {
			return element;
		}
		return findElementByTestId(element.props.children, testId);
	}
	return null;
}

describe("WB-00 Agentic Pipeline Launcher & Report Inspector", () => {
	const demoEvent =
		PE_DEMO_EVENTS.find((e) => e.workbench_view_id === "WB-00") ?? null;

	beforeEach(() => {
		resetPipelineLauncherStore();
	});

	afterEach(() => {
		resetPipelineLauncherStore();
	});

	it("renders the pipeline launcher with empty input fields and zero hardcoded sample paths", () => {
		const store = createPipelineLauncherStore();
		const tree = WB00CommandCenter({
			currentEvent: demoEvent,
			eventsForView: demoEvent ? [demoEvent] : [],
			onInspectProof: () => {},
			launcherStore: store,
		});

		const specInput = findElementByTestId(tree, "input-pipeline-spec-path");
		const harnessInput = findElementByTestId(tree, "input-harness-config-path");
		const providerSelect = findElementByTestId(tree, "select-harness-provider");
		const policySelect = findElementByTestId(tree, "select-hold-policy");

		expect(specInput).not.toBeNull();
		expect(specInput?.props.value).toBe("");
		expect(specInput?.props.placeholder).toBe(
			"Enter path to pipeline_input.yaml...",
		);

		expect(harnessInput).not.toBeNull();
		expect(harnessInput?.props.value).toBe("");
		expect(harnessInput?.props.placeholder).toBe(
			"Optional path to harness_config.yaml...",
		);

		expect(providerSelect?.props.value).toBe("");
		expect(policySelect?.props.value).toBe("auto_approve_top_candidate");

		const html = renderToStaticMarkup(tree);
		expect(html).toContain('data-testid="wb00-pipeline-launcher"');
		expect(html).toContain(
			"Trigger Agentic Pipeline (VaultFirstMate + No-Mistakes)",
		);
		expect(html).toContain('data-testid="input-pipeline-spec-path"');
		expect(html).toContain('data-testid="input-harness-config-path"');
		expect(html).toContain('data-testid="select-harness-provider"');
		expect(html).toContain('data-testid="select-hold-policy"');
		expect(html).toContain('data-testid="btn-validate-pipeline"');
		expect(html).toContain('data-testid="btn-run-pipeline"');

		// Verify zero hardcoded sample paths exist in the rendered launcher markup
		expect(html).not.toContain("global_commerce_vault");
		expect(html).not.toContain("examples/pipeline_samples");
		expect(html).not.toContain("examples/private_equity");
	});

	it("displays a deterministic client-side validation error without making a network call when clicking Run Agentic Pipeline or Validate Inputs with an empty path", async () => {
		const store = createPipelineLauncherStore();
		let networkCallCount = 0;
		const mockFetch = (async () => {
			networkCallCount += 1;
			return new Response("{}", { status: 200 });
		}) as unknown as typeof fetch;

		const initialTree = WB00CommandCenter({
			currentEvent: demoEvent,
			eventsForView: demoEvent ? [demoEvent] : [],
			onInspectProof: () => {},
			launcherStore: store,
			fetchImpl: mockFetch,
		});

		const runBtn = findElementByTestId(initialTree, "btn-run-pipeline");
		expect(runBtn).not.toBeNull();
		runBtn?.props.onClick?.();

		expect(networkCallCount).toBe(0);
		expect(store.getState().validationError).toBe(
			EMPTY_INPUT_PATH_VALIDATION_ERROR,
		);

		const htmlAfterRunClick = renderToStaticMarkup(
			<WB00CommandCenter
				currentEvent={demoEvent}
				eventsForView={demoEvent ? [demoEvent] : []}
				onInspectProof={() => {}}
				launcherStore={store}
				fetchImpl={mockFetch}
			/>,
		);
		expect(htmlAfterRunClick).toContain(
			'data-testid="wb00-pipeline-validation-error"',
		);
		expect(htmlAfterRunClick).toContain(EMPTY_INPUT_PATH_VALIDATION_ERROR);
		expect(htmlAfterRunClick).not.toContain(
			'data-testid="wb00-pipeline-report-panel"',
		);

		// Also verify Validate Inputs button with empty path
		store.getState().reset();
		const validateBtn = findElementByTestId(
			initialTree,
			"btn-validate-pipeline",
		);
		expect(validateBtn).not.toBeNull();
		validateBtn?.props.onClick?.();
		expect(networkCallCount).toBe(0);
		expect(store.getState().validationError).toBe(
			EMPTY_INPUT_PATH_VALIDATION_ERROR,
		);
	});

	it("dispatches POST /api/v1/pipelines/runs when entering a path and clicking Run Agentic Pipeline and renders the AgenticPipelineReport steps, waves, and intermediate artifact SHA-256 digests", async () => {
		const store = createPipelineLauncherStore();
		const requests: Array<{
			url: string;
			method: string;
			body: Record<string, unknown>;
		}> = [];

		const mockReport: AgenticPipelineReport = {
			run_id: "run-agentic-pipeline-20261101",
			pipeline_id: "pipe-commerce-v1",
			project_id: "proj-commerce-vault",
			status: "COMPLETED",
			overall_passed: true,
			input_validation: {
				passed: true,
				status: "PASS",
				summary: "All wave sources and ontology paths verified.",
			},
			output_validation: {
				passed: true,
				status: "PASS",
				pii_leak_check_passed: true,
				cas_integrity_passed: true,
			},
			wave_reports: [
				{
					wave_id: "wave_0_cold_start",
					wave_index: 0,
					description: "Cold Start N=1 onboarding",
					status: "PASS",
					passed: true,
				},
				{
					wave_id: "wave_1_incremental_billing",
					wave_index: 1,
					description: "Incremental source N=1 -> N=2 BKCC shield",
					status: "PASS",
					passed: true,
				},
			],
			step_reports: [
				{
					step_id: "STAGE_00_ORCHESTRATION",
					stage_id: "STAGE_00_ORCHESTRATION",
					step_name: "Dual-Graph Grounding & Orchestration",
					agent_persona: "@vault-first-mate",
					wave_id: "wave_0_cold_start",
					status: "PASS",
					passed: true,
				},
				{
					step_id: "STAGE_07_DETERMINISTIC_COMPILER",
					stage_id: "STAGE_07_DETERMINISTIC_COMPILER",
					step_name: "4-Zone DuckDB SQL Compilation",
					agent_persona: "@deterministic-compiler",
					wave_id: "wave_1_incremental_billing",
					status: "PASS",
					passed: true,
				},
			],
			intermediate_artifacts: [
				{
					artifact_id: "art-wave0-contract",
					name: "dv_model_contract.yaml",
					wave_id: "wave_0_cold_start",
					path: "waves/wave_0/dv_model_contract.yaml",
					sha256:
						"a1b2c3d4e5f60718293a4b5c6d7e8f90123456789abcdef0123456789abcdef0",
				},
				{
					artifact_id: "art-wave1-sql",
					name: "compiled_4zone.sql",
					wave_id: "wave_1_incremental_billing",
					path: "waves/wave_1/compiled_4zone.sql",
					sha256:
						"f0e1d2c3b4a5968778695a4b3c2d1e0f9a8b7c6d5e4f3a2b1c0d9e8f7a6b5c4d",
				},
			],
		};

		const mockFetch = (async (
			input: string | URL | Request,
			init?: RequestInit,
		) => {
			const url = String(input);
			const method = init?.method ?? "GET";
			const body = init?.body
				? (JSON.parse(String(init.body)) as Record<string, unknown>)
				: {};
			requests.push({ url, method, body });

			if (url === "/api/v1/pipelines/validate" && method === "POST") {
				return new Response(
					JSON.stringify({
						passed: true,
						status: "PASS",
						summary: "Validated successfully",
					}),
					{
						status: 200,
						headers: { "Content-Type": "application/json" },
					},
				);
			}

			if (url === "/api/v1/pipelines/runs" && method === "POST") {
				return new Response(JSON.stringify(mockReport), {
					status: 200,
					headers: { "Content-Type": "application/json" },
				});
			}

			return new Response("Not Found", { status: 404 });
		}) as unknown as typeof fetch;

		// 1. Render initial tree and populate inputs via onChange handlers
		const tree = WB00CommandCenter({
			currentEvent: demoEvent,
			eventsForView: demoEvent ? [demoEvent] : [],
			onInspectProof: () => {},
			launcherStore: store,
			fetchImpl: mockFetch,
		});

		const specInput = findElementByTestId(tree, "input-pipeline-spec-path");
		const harnessInput = findElementByTestId(tree, "input-harness-config-path");
		const providerSelect = findElementByTestId(tree, "select-harness-provider");
		const policySelect = findElementByTestId(tree, "select-hold-policy");

		specInput?.props.onChange?.({
			target: { value: "/custom/workspace/pipeline_input.yaml" },
		});
		harnessInput?.props.onChange?.({
			target: { value: "/custom/workspace/harness_config.yaml" },
		});
		providerSelect?.props.onChange?.({
			target: { value: "deterministic" },
		});
		policySelect?.props.onChange?.({
			target: { value: "auto_approve_top_candidate" },
		});

		// 2. Click Validate Inputs first
		await store.getState().validatePipelineInputs(mockFetch);
		expect(requests).toHaveLength(1);
		expect(requests[0]).toEqual({
			url: "/api/v1/pipelines/validate",
			method: "POST",
			body: {
				input_spec_path: "/custom/workspace/pipeline_input.yaml",
				harness_config_path: "/custom/workspace/harness_config.yaml",
				provider_override: "deterministic",
				steward_hold_policy: "auto_approve_top_candidate",
			},
		});

		// 3. Click Run Agentic Pipeline
		const updatedTree = WB00CommandCenter({
			currentEvent: demoEvent,
			eventsForView: demoEvent ? [demoEvent] : [],
			onInspectProof: () => {},
			launcherStore: store,
			fetchImpl: mockFetch,
		});
		const runBtn = findElementByTestId(updatedTree, "btn-run-pipeline");
		runBtn?.props.onClick?.();
		// Wait for async fetch resolution
		await new Promise((resolve) => setTimeout(resolve, 10));

		expect(requests).toHaveLength(2);
		expect(requests[1]).toEqual({
			url: "/api/v1/pipelines/runs",
			method: "POST",
			body: {
				input_spec_path: "/custom/workspace/pipeline_input.yaml",
				harness_config_path: "/custom/workspace/harness_config.yaml",
				provider_override: "deterministic",
				steward_hold_policy: "auto_approve_top_candidate",
			},
		});

		const reportHtml = renderToStaticMarkup(
			<WB00CommandCenter
				currentEvent={demoEvent}
				eventsForView={demoEvent ? [demoEvent] : []}
				onInspectProof={() => {}}
				launcherStore={store}
				fetchImpl={mockFetch}
			/>,
		);

		expect(reportHtml).toContain('data-testid="wb00-pipeline-report-panel"');
		expect(reportHtml).toContain("run-agentic-pipeline-20261101");
		expect(reportHtml).toContain("COMPLETED");
		expect(reportHtml).toContain("true");

		// Waves
		expect(reportHtml).toContain('data-testid="report-waves-section"');
		expect(reportHtml).toContain("wave_0_cold_start");
		expect(reportHtml).toContain("wave_1_incremental_billing");

		// Steps
		expect(reportHtml).toContain('data-testid="report-steps-section"');
		expect(reportHtml).toContain("STAGE_00_ORCHESTRATION");
		expect(reportHtml).toContain("STAGE_07_DETERMINISTIC_COMPILER");
		expect(reportHtml).toContain("@vault-first-mate");
		expect(reportHtml).toContain("@deterministic-compiler");

		// Intermediate Artifacts & SHA-256 digests
		expect(reportHtml).toContain('data-testid="report-artifacts-section"');
		expect(reportHtml).toContain("dv_model_contract.yaml");
		expect(reportHtml).toContain("compiled_4zone.sql");
		expect(reportHtml).toContain(
			"a1b2c3d4e5f60718293a4b5c6d7e8f90123456789abcdef0123456789abcdef0",
		);
		expect(reportHtml).toContain(
			"f0e1d2c3b4a5968778695a4b3c2d1e0f9a8b7c6d5e4f3a2b1c0d9e8f7a6b5c4d",
		);
	});
});
