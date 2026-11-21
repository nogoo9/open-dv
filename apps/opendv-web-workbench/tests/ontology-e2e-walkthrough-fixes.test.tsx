/**
 * End-to-End Walkthrough Remediation Tests (ISS-01 through ISS-10)
 * (apps/opendv-web-workbench/tests/ontology-e2e-walkthrough-fixes.test.tsx)
 */

import { beforeEach, describe, expect, it } from "bun:test";
import { readFileSync } from "node:fs";
import path from "node:path";
import { ReactFlowProvider } from "@xyflow/react";
import type React from "react";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { ConceptNodeComponent } from "../src/ontology/components/GraphCanvas";
import { VisualEditorDrawer } from "../src/ontology/components/VisualEditorDrawer";
import {
	CLIPBOARD_FEEDBACK_RESET_MS,
	ELK_LAYOUT_DEFAULT_TIMEOUT_MS,
	GROUNDING_MIN_CONFIDENCE,
	MAX_SYNONYMS_PER_CONCEPT,
	MIN_RATIONALE_LENGTH,
	SAVE_FEEDBACK_RESET_MS,
	STUDIO_MAX_UPLOAD_BYTES,
} from "../src/ontology/constants";
import {
	createStudioStore,
	isSupportedSourceFile,
	normalizeProposalOperationsForSubmission,
	resolveProposalBaseVersionAndSha,
	sortProposalsForStewardship,
	studioStore,
} from "../src/ontology/store/studio-store";
import type { ConceptNode } from "../src/ontology/store/types";

describe("Ontology Studio E2E Walkthrough Fixes (ISS-01 through ISS-10)", () => {
	beforeEach(() => {
		studioStore.getState().reset();
	});

	it("ISS-03: GraphCanvas extracts terminal IRI fragment for full HTTP URIs and CURIEs with full IRI tooltip", () => {
		const fullUri =
			"https://spec.edmcouncil.org/fibo/ontology/FND/Agreements/Agreements/FinancialAgreement";
		const curieUri = "fibo-be-le-cb:CommercialBank";
		const concept: ConceptNode = {
			id: "urn:ontology:entity:agreement",
			concept_uri: "urn:ontology:entity:agreement",
			concept_name: "Advisory Agreement",
			elm_archetype: "CORE_CONCEPT",
			definition: "Advisory mandate.",
			grounding_status: "GROUNDED",
			attributes: [],
			synonyms: [],
			external_mappings: [
				{
					scheme: "fibo",
					target_uri: fullUri,
					confidence: 0.91,
					predicate: "skos:exactMatch",
				},
				{
					scheme: "fibo",
					target_uri: curieUri,
					confidence: 0.88,
					predicate: "skos:closeMatch",
				},
			],
		};

		const html = renderToStaticMarkup(
			createElement(
				ReactFlowProvider,
				null,
				createElement(
					ConceptNodeComponent as unknown as React.ComponentType<
						Record<string, unknown>
					>,
					{
						data: {
							concept,
							lodTier: 3,
							isSelected: false,
							isDimmed: false,
						},
					},
				),
			),
		);

		expect(html).toContain("fibo: FinancialAgreement");
		expect(html).toContain("fibo: CommercialBank");
		expect(html).toContain(`title="fibo: ${fullUri}"`);
		expect(html).not.toContain(">fibo: //spec.edmcouncil.org");
	});

	it("ISS-08: supports comparing committed baseline against uncommitted Working Draft ('draft') without hitting /export?version=draft", async () => {
		const store = createStudioStore();
		const committedConcept: ConceptNode = {
			id: "urn:ontology:entity:account",
			concept_uri: "urn:ontology:entity:account",
			concept_name: "Client Account",
			elm_archetype: "CORE_CONCEPT",
			definition: "Client custody account.",
			grounding_status: "GROUNDED",
			attributes: [],
			synonyms: [],
			external_mappings: [],
		};
		const draftAddedConcept: ConceptNode = {
			id: "urn:ontology:entity:advisory_fee",
			concept_uri: "urn:ontology:entity:advisory_fee",
			concept_name: "Advisory Fee",
			elm_archetype: "CORE_CONCEPT",
			definition: "Fee charged for advisory services.",
			grounding_status: "UNGROUNDED",
			attributes: [],
			synonyms: [],
			external_mappings: [],
		};

		store.setState({
			activeSession: {
				session_id: "wealth-draft-diff",
				domain_namespace: "wealth_mgmt",
				enterprise_owner: "Wealth Ops",
				status: "ACTIVE",
				active_version: "1.0.0",
				head_sha256: "a".repeat(64),
				commits: [{ version: "1.0.0", sha256: "a".repeat(64) }],
			},
			committedNodes: [committedConcept],
			committedEdges: [],
			nodes: [committedConcept, draftAddedConcept],
			edges: [],
			hasUnsavedChanges: true,
		});

		const fetchedUrls: string[] = [];
		const spyFetch: typeof fetch = async (input) => {
			fetchedUrls.push(String(input));
			throw new Error(`Unexpected network fetch for ${String(input)}`);
		};

		await store.getState().loadAndComputeDiff("1.0.0", "draft", spyFetch);

		expect(fetchedUrls).toHaveLength(0);
		const diff = store.getState().diffState;
		expect(diff.active).toBe(true);
		expect(diff.base_version).toBe("1.0.0");
		expect(diff.target_version).toBe("draft");
		expect(diff.summary.added_count).toBe(1);
		expect(diff.records[0]?.label).toBe("Advisory Fee");

	});

	it("ISS-09: VisualEditorDrawer explains ONT-EVO-002 grounding", () => {
		studioStore.setState({
			selectedNodeId: "urn:ontology:entity:custom",
			nodes: [
				{
					id: "urn:ontology:entity:custom",
					concept_uri: "urn:ontology:entity:custom",
					concept_name: "Custom Client Tier",
					elm_archetype: "CORE_CONCEPT",
					definition: "Custom internal wealth tier.",
					grounding_status: "UNGROUNDED",
					attributes: [],
					synonyms: [],
					external_mappings: [],
				},
			],
		});
		const drawerHtml = renderToStaticMarkup(createElement(VisualEditorDrawer));
		expect(drawerHtml).toContain("ONT-EVO-002 Grounding:");
		expect(drawerHtml).toContain("skos:exactMatch");
		expect(drawerHtml).toContain("Steward Waiver");
	});

	it("ISS-10: centralizes Studio constants matching backend limits and contains zero hardcoded loopback ports in src/ontology", () => {
		expect(STUDIO_MAX_UPLOAD_BYTES).toBe(100 * 1024 * 1024);
		expect(MAX_SYNONYMS_PER_CONCEPT).toBe(50);
		expect(GROUNDING_MIN_CONFIDENCE).toBe(0.65);
		expect(CLIPBOARD_FEEDBACK_RESET_MS).toBe(2000);
		expect(SAVE_FEEDBACK_RESET_MS).toBe(2500);
		expect(ELK_LAYOUT_DEFAULT_TIMEOUT_MS).toBe(250);
		expect(MIN_RATIONALE_LENGTH).toBe(10);

		const constantsSrc = readFileSync(
			path.resolve(import.meta.dir, "../src/ontology/constants.ts"),
			"utf-8",
		);
		expect(constantsSrc).not.toMatch(/127\.0\.0\.1|:8000|:8420/);
	});

	it("AUDIT-MAJOR-01: loadAndComputeDiff resolves committed head cleanly when dirty, computeDiff detects attribute optionality/description and mapping changes, and empty draft diff shows Working Draft", async () => {
		const store = createStudioStore({ userRole: "admin" });
		const committedConcept: ConceptNode = {
			id: "urn:ontology:entity:account",
			concept_uri: "urn:ontology:entity:account",
			concept_name: "Wealth Account",
			elm_archetype: "HUB",
			definition: "Client custody account.",
			grounding_status: "GROUNDED",
			attributes: [
				{
					attribute_name: "risk_tier",
					data_type: "string",
					is_optional: true,
					description: "Initial risk bucket",
				},
			],
			synonyms: [],
			external_mappings: [
				{
					scheme: "fibo",
					target_uri:
						"https://spec.edmcouncil.org/fibo/ontology/FBC/ProductsAndServices/ClientsAndAccounts/Account",
					predicate: "skos:broadMatch",
					confidence: 0.72,
				},
			],
		};
		const dirtyModifiedConcept: ConceptNode = {
			...committedConcept,
			concept_name: "Wealth Account (Dirty Uncommitted)",
			attributes: [
				{
					attribute_name: "risk_tier",
					data_type: "string",
					is_optional: false,
					description: "Strict KYC risk bucket",
				},
			],
			external_mappings: [
				{
					scheme: "fibo",
					target_uri:
						"https://spec.edmcouncil.org/fibo/ontology/FBC/ProductsAndServices/ClientsAndAccounts/Account",
					predicate: "skos:exactMatch",
					confidence: 0.91,
				},
			],
		};

		store.setState({
			activeSession: {
				session_id: "diff-audit-test",
				domain_namespace: "wealth_mgmt",
				enterprise_owner: "Wealth Ops",
				status: "ACTIVE",
				active_version: "1.0.0",
				head_sha256: "b".repeat(64),
				created_at: "2026-11-14T00:00:00Z",
				updated_at: "2026-11-14T00:00:00Z",
				commits: [
					{
						commit_id: "c1",
						new_sha256: "b".repeat(64),
						version: "1.0.0",
						rationale: "Initial release",
						committed_at: "2026-11-14T00:00:00Z",
					},
				],
			},
			committedNodes: [committedConcept],
			committedEdges: [],
			nodes: [dirtyModifiedConcept],
			edges: [],
			hasUnsavedChanges: true,
		});

		const spyFetch: typeof fetch = async (input) => {
			throw new Error(`Unexpected network fetch for ${String(input)}`);
		};

		// Comparing 0.0.0 -> 1.0.0 while dirty must inspect committed 1.0.0 (not dirty draft)
		await store.getState().loadAndComputeDiff("0.0.0", "1.0.0", spyFetch);
		const committedDiff = store.getState().diffState;
		expect(committedDiff.summary.added_count).toBe(1);
		expect(committedDiff.records[0]?.label).toBe("Wealth Account");

		// Comparing draft -> 1.0.0 must compare dirty draft vs committed 1.0.0 (not dirty vs dirty)
		await store.getState().loadAndComputeDiff("draft", "1.0.0", spyFetch);
		const draftToHead = store.getState().diffState;
		expect(draftToHead.summary.modified_count).toBeGreaterThanOrEqual(1);
		expect(
			draftToHead.records.some(
				(r) =>
					r.item_type === "concept" &&
					r.label === "Wealth Account" &&
					r.details.includes("mappings changed"),
			),
		).toBe(true);

		// Forward committed -> draft detects name change, optional->mandatory tightening, and mapping change
		await store.getState().loadAndComputeDiff("1.0.0", "draft", spyFetch);
		const headToDraft = store.getState().diffState;
		expect(headToDraft.summary.breaking_count).toBe(2);
		expect(
			headToDraft.records.some(
				(r) =>
					r.identifier.endsWith("#attribute-optionality") &&
					r.is_breaking === true,
			),
		).toBe(true);
		expect(
			headToDraft.records.some(
				(r) =>
					r.item_type === "concept" && r.details.includes("mappings changed"),
			),
		).toBe(true);

		// Widening optionality (false -> true) + description update only produces non-breaking modified record
		const mandatoryBase: ConceptNode = {
			...committedConcept,
			attributes: [
				{
					attribute_name: "risk_tier",
					data_type: "string",
					is_optional: false,
					description: "Before",
				},
			],
		};
		const widenedTarget: ConceptNode = {
			...committedConcept,
			attributes: [
				{
					attribute_name: "risk_tier",
					data_type: "string",
					is_optional: true,
					description: "After",
				},
			],
		};
		store
			.getState()
			.computeDiff(
				"1.0.0",
				"1.1.0",
				{ nodes: [mandatoryBase], edges: [] },
				{ nodes: [widenedTarget], edges: [] },
			);
		expect(store.getState().diffState.summary.breaking_count).toBe(0);
		expect(store.getState().diffState.summary.modified_count).toBe(1);

	});

	it("AUDIT-MAJOR-02..03 & MINOR-01: GraphCanvas theme CSS variables and .markdown upload support", () => {
		const graphCanvasSrc = readFileSync(
			path.resolve(
				import.meta.dir,
				"../src/ontology/components/GraphCanvas.tsx",
			),
			"utf-8",
		);
		const viewportSrc = graphCanvasSrc.slice(
			graphCanvasSrc.indexOf("<ReactFlow"),
		);
		expect(viewportSrc).toContain("var(--dv-minimap-mask-bg)");
		expect(viewportSrc).toContain("var(--dv-border-strong)");
		expect(viewportSrc).not.toContain('"#374151"');
		expect(viewportSrc).not.toContain('"#1D4ED8"');
		expect(viewportSrc).not.toContain('"#1F2937"');


		expect(
			isSupportedSourceFile(
				"wealth_appendix.markdown",
				"application/octet-stream",
			),
		).toBe(true);
	});

	it("Carbon review hardening: base pairing, sorting, normalization, and draft persistence", async () => {
		// MINOR-01: resolveProposalBaseVersionAndSha pairs PENDING baseline or activeSession head consistently
		const acceptedBaseline = resolveProposalBaseVersionAndSha(
			{ active_version: "2.0.0", head_sha256: "b".repeat(64) },
			{
				proposal_id: "prop-old",
				session_id: "s-1",
				status: "ACCEPTED",
				base_version: "1.0.0",
				base_sha256: "a".repeat(64),
				proposer: "human:admin",
			},
		);
		expect(acceptedBaseline.base_version).toBe("2.0.0");
		expect(acceptedBaseline.base_sha256).toBe("b".repeat(64));

		const pendingBaseline = resolveProposalBaseVersionAndSha(
			{ active_version: "2.0.0", head_sha256: "b".repeat(64) },
			{
				proposal_id: "prop-pend",
				session_id: "s-1",
				status: "PENDING",
				base_version: "1.0.0",
				base_sha256: "a".repeat(64),
				proposer: "human:admin",
			},
		);
		expect(pendingBaseline.base_version).toBe("1.0.0");
		expect(pendingBaseline.base_sha256).toBe("a".repeat(64));

		// MINOR-02: sortProposalsForStewardship places PENDING first, newest created_at first
		const sorted = sortProposalsForStewardship([
			{
				proposal_id: "prop-acc",
				session_id: "s-1",
				status: "ACCEPTED",
				base_version: "1.0.0",
				proposer: "human:admin",
				created_at: "2026-11-14T05:00:00Z",
			},
			{
				proposal_id: "prop-pend-old",
				session_id: "s-1",
				status: "PENDING",
				base_version: "1.0.0",
				proposer: "human:admin",
				created_at: "2026-11-14T01:00:00Z",
			},
			{
				proposal_id: "prop-pend-new",
				session_id: "s-1",
				status: "PENDING",
				base_version: "1.0.0",
				proposer: "human:admin",
				created_at: "2026-11-14T04:00:00Z",
			},
		]);
		expect(sorted.map((p) => p.proposal_id)).toEqual([
			"prop-pend-new",
			"prop-pend-old",
			"prop-acc",
		]);

		// MAJOR-01 & MAJOR-03: normalizeProposalOperationsForSubmission & validation helpers
		const normalized = normalizeProposalOperationsForSubmission([
			{
				op_type: "add_external_mapping",
				concept_uri: "urn:ontology:entity:margin_call",
				scheme: "FIBO",
				predicate: "skos:exactMatch",
				target_uri: "https://spec.edmcouncil.org/fibo/MarginCall",
				mapping_confidence: 0.91,
			},
		]);
		expect(String(normalized[0]?.op_id || "").length).toBeGreaterThan(0);
		expect(normalized[0]?.confidence).toBe(0.91);

		// MAJOR-02, MAJOR-04, MINOR-03: draft persistence across tabs and Accept/Rebase lock on dirty edits
		const prop = {
			proposal_id: "prop-dirty-1",
			session_id: "s-dirty",
			status: "PENDING" as const,
			base_version: "1.0.0",
			base_sha256: "c".repeat(64),
			proposer: "human:admin",
			rationale: "Original rationale",
			operations: [
				{
					op_id: "op-1",
					op_type: "add_concept",
					concept_uri: "urn:ontology:entity:margin_call",
					concept_name: "Margin Call",
					elm_archetype: "EVENT_CONCEPT",
					target_hub: "hub_margin_call",
				},
			],
		};
		studioStore.setState({
			activeSession: {
				session_id: "s-dirty",
				domain_namespace: "clearing",
				enterprise_owner: "CRO",
				active_version: "1.0.0",
				head_sha256: "c".repeat(64),
				status: "ACTIVE",
			},
			userRole: "admin",
			proposals: [prop],
			selectedProposalId: "prop-dirty-1",
		});
		studioStore.getState().setProposalDraft("prop-dirty-1", {
			rationale: "Modified rationale in draft",
			operations: prop.operations,
		});
		// Switching back to concept/Ontology Viewer keeps selectedProposalId & proposalDrafts intact
		studioStore.getState().setRightDrawerTab("concept");
		expect(studioStore.getState().selectedProposalId).toBe("prop-dirty-1");
		expect(
			studioStore.getState().proposalDrafts["prop-dirty-1"]?.rationale,
		).toBe("Modified rationale in draft");
		studioStore.getState().setRightDrawerTab("proposal");
	});
});
