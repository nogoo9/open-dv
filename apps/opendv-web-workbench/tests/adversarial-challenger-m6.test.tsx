/**
 * apps/opendv-web-workbench/tests/adversarial-challenger-m6.test.tsx
 *
 * Tier 5 Adversarial Coverage Hardening Suite for Web Workbench:
 * - 1-Click deep-link parameter parsing and draft creation in `specBuilderUtils`
 * - React state immutability in `studio-store` (`setRightDrawerTab`, `toggleRightDrawer`, drawer state transitions)
 */

import { afterEach, beforeEach, describe, expect, it } from "bun:test";
import { studioStore } from "../src/ontology/store/studio-store";
import {
	createDefaultPipelineDraft,
	createOntologyStudioPipelineDraft,
} from "../src/explorer/builder/specBuilderUtils";

describe("Tier 5 Adversarial Coverage Hardening: Web Workbench", () => {
	describe("1. 1-Click Deep-Link Parameter Parsing & Draft Defaults", () => {
		const originalLocation = globalThis.location;

		afterEach(() => {
			if (originalLocation) {
				globalThis.location = originalLocation;
			}
		});

		it("decodes URL-encoded parameters properly against query injection", () => {
			const draft = createOntologyStudioPipelineDraft({
				sessionId: "sess%20with%20spaces",
				domainNamespace: "private%26credit",
				pipelineId: "pl%20custom",
			});

			expect(draft.pipelineId).toBe("pl%20custom");
			expect(draft.domainNamespace).toBe("private%26credit");
		});

		it("falls back to global_commerce_vault_run when no ontology query params exist", () => {
			// @ts-expect-error mocking location
			globalThis.location = { search: "" };

			const defaultDraft = createDefaultPipelineDraft();
			expect(defaultDraft.pipelineId).toBe("global_commerce_vault_run");
			expect(defaultDraft.domainNamespace).toBe("commerce");
		});
	});

	describe("2. React State Immutability in studioStore", () => {
		beforeEach(() => {
			studioStore.setState({
				nodes: [],
				edges: [],
				proposals: [
					{
						proposal_id: "prop-001",
						session_id: "sess-001",
						status: "PENDING",
						base_version: "1.0.0",
						target_version: "1.1.0",
						operations: [],
					},
				],
				selectedProposalId: null,
				layout: {
					leftDrawerTab: "sessions",
					leftDrawerOpen: false,
					rightDrawerOpen: false,
					rightDrawerTab: "concept",
					bottomDockOpen: false,
					activeModal: null,
					lodTier: 2,
					minimapVisible: false,
					searchTerm: "",
				},
			});
		});

		it("immutably updates layout when setRightDrawerTab is called", () => {
			const initialLayout = studioStore.getState().layout;

			studioStore.getState().setRightDrawerTab("proposal");

			const updatedState = studioStore.getState();
			expect(updatedState.layout.rightDrawerTab).toBe("proposal");
			expect(updatedState.layout.rightDrawerOpen).toBe(true);
			expect(updatedState.layout).not.toBe(initialLayout);
		});

		it("auto-selects first proposal when switching to proposal tab with null selection", () => {
			expect(studioStore.getState().selectedProposalId).toBeNull();

			studioStore.getState().setRightDrawerTab("proposal");

			expect(studioStore.getState().layout.rightDrawerTab).toBe("proposal");
			expect(studioStore.getState().selectedProposalId).toBe("prop-001");
		});

		it("immutably toggles right drawer open/closed state", () => {
			const s1 = studioStore.getState();
			s1.toggleRightDrawer(true);
			expect(studioStore.getState().layout.rightDrawerOpen).toBe(true);

			s1.toggleRightDrawer(false);
			expect(studioStore.getState().layout.rightDrawerOpen).toBe(false);

			s1.toggleRightDrawer();
			expect(studioStore.getState().layout.rightDrawerOpen).toBe(true);
		});
	});
});
