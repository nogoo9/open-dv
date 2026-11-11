/**
 * Component & Interaction Tests: Ontology Panels (Tasks 5D.2, 5D.3, 5D.5)
 * (apps/opendv-web-workbench/tests/ontology-panels.test.tsx)
 *
 * Covers:
 * - describe("SourcesPanel", ...) (Task 5D.2, #223)
 * - describe("ClarificationPanel", ...) (Task 5D.3, #224)
 * - describe("DiffOverlay", ...) (Task 5D.5, #226)
 * - describe("VisualEditorDrawer", ...) (Task 5D.5, #226)
 */

import { beforeEach, describe, expect, it } from "bun:test";
import React, { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { ClarificationPanel } from "../src/ontology/components/ClarificationPanel";
import { DiffOverlay } from "../src/ontology/components/DiffOverlay";
import { QualityFeedbackPanel } from "../src/ontology/components/QualityFeedbackPanel";
import {
	SourcesPanel,
	formatBytes,
	getComplexityBadgeClass,
} from "../src/ontology/components/SourcesPanel";
import {
	VisualEditorDrawer,
	validateConceptEdit,
	type ConceptEditDraft,
} from "../src/ontology/components/VisualEditorDrawer";
import { studioStore } from "../src/ontology/store/studio-store";

beforeEach(() => {
	studioStore.getState().reset();
});

describe("SourcesPanel", () => {
	it("formats byte values accurately across B, KB, MB, GB", () => {
		expect(formatBytes(0)).toBe("0 B");
		expect(formatBytes(1024)).toBe("1 KB");
		expect(formatBytes(1024 * 1024)).toBe("1 MB");
		expect(formatBytes(5.5 * 1024 * 1024)).toBe("5.5 MB");
	});

	it("resolves layout complexity badge classes", () => {
		expect(getComplexityBadgeClass("SIMPLE")).toBe("badge-simple");
		expect(getComplexityBadgeClass("TABULAR")).toBe("badge-tabular");
		expect(getComplexityBadgeClass("MULTI_COLUMN")).toBe("badge-multi-col");
		expect(getComplexityBadgeClass("COMPLEX")).toBe("badge-complex");
	});

	it("renders empty sources message when zero documents uploaded", () => {
		studioStore.setState({ sources: [], parsingState: { status: "idle", progress_pct: 0, active_job_id: null, stage: "", ocr_mode: "auto", evidence_chunks: [], error: null } });
		const html = renderToStaticMarkup(createElement(SourcesPanel));

		expect(html).toContain("Sources &amp; Ingestion");
		expect(html).toContain("No documents uploaded yet");
		expect(html).toContain("data-testid=\"sources-panel\"");
		expect(html).toContain("data-testid=\"file-input\"");
		expect(html).toContain("data-testid=\"confidential-toggle\"");
	});

	it("renders sources table when sources are loaded", () => {
		studioStore.setState({
			sources: [
				{
					source_id: "src-1",
					source_sha256: "1c100409cbbe12051f7211d2d6d6838670982b9164aaf18b52f566864da90b81",
					file_name: "sec_10k_annual_report.pdf",
					size_bytes: 524288,
					mime_type: "application/pdf",
					do_not_send_to_agent: true,
				},
			],
		});

		const html = renderToStaticMarkup(createElement(SourcesPanel));

		expect(html).toContain("sec_10k_annual_report.pdf");
		expect(html).toContain("application/pdf");
		expect(html).toContain("512 KB");
		expect(html).toContain("1c100409");
		expect(html).toContain("CONFIDENTIAL");
		expect(html).toContain("data-testid=\"delete-source-src-1\"");
	});

	it("renders parsing progress bar when state is parsing", () => {
		studioStore.setState({
			parsingState: {
				status: "parsing",
				progress_pct: 65,
				active_job_id: "job-8812",
				stage: "tokenization",
				ocr_mode: "auto",
				evidence_chunks: [],
				error: null,
			},
		});

		const html = renderToStaticMarkup(createElement(SourcesPanel));

		expect(html).toContain("Stage: tokenization");
		expect(html).toContain("65%");
		expect(html).toContain("Job ID: job-8812");
		expect(html).toContain("data-testid=\"parsing-progress-box\"");
	});

	it("renders evidence chunks with complexity badges and token counts", () => {
		studioStore.setState({
			parsingState: {
				status: "completed",
				progress_pct: 100,
				active_job_id: "job-8812",
				stage: "complete",
				ocr_mode: "auto",
				evidence_chunks: [
					{
						chunk_id: "chk-01",
						source_id: "src-1",
						source_file: "sec_10k.pdf",
						text_snippet: "Investment Advisory Agreement executed pursuant to Section 203.",
						token_count: 145,
						layout_complexity: "TABULAR",
					},
				],
				error: null,
			},
		});

		const html = renderToStaticMarkup(createElement(SourcesPanel));

		expect(html).toContain("Extracted Evidence Chunks (1)");
		expect(html).toContain("TABULAR");
		expect(html).toContain("145 tokens");
		expect(html).toContain("Investment Advisory Agreement executed");
	});
});

describe("ClarificationPanel", () => {
	it("renders empty semantic clarity box when zero questions pending", () => {
		studioStore.setState({ clarifications: [] });
		const html = renderToStaticMarkup(createElement(ClarificationPanel));

		expect(html).toContain("Clarification Q&amp;A");
		expect(html).toContain("All Semantic Questions Resolved");
		expect(html).toContain("0 Pending");
		expect(html).toContain("0 Answered");
	});

	it("renders question cards with rationale options, certainty badge, and citations", () => {
		studioStore.setState({
			clarifications: [
				{
					question_id: "q-441",
					concept_name: "Fund Advisor",
					question_text: "Does 'Fund Advisor' refer to a registered InvestmentAdvisor or an internal ManagementCompany?",
					certainty: "HIGH",
					rationale_options: [
						{ id: "opt-1", label: "InvestmentAdvisor (SEC Rule 203)", description: "Registered investment advisor" },
						{ id: "opt-2", label: "FundManagementCompany", description: "Internal general partner entity" },
					],
					evidence_citations: [
						{
							source_file: "annual_report.pdf",
							chunk_id: "chk-12",
							excerpt: "Advisory services provided by registered advisor affiliates under SEC oversight.",
						},
					],
					status: "PENDING",
				},
			],
			answers: {},
		});

		const html = renderToStaticMarkup(createElement(ClarificationPanel));

		expect(html).toContain("Fund Advisor");
		expect(html).toContain("HIGH CERTAINTY");
		expect(html).toContain("InvestmentAdvisor (SEC Rule 203)");
		expect(html).toContain("FundManagementCompany");
		expect(html).toContain("annual_report.pdf (chk-12)");
		expect(html).toContain("data-testid=\"option-btn-q-441-opt-1\"");
		expect(html).toContain("data-testid=\"submit-answer-btn-q-441\"");
		expect(html).toContain("1 Pending");
		expect(html).toContain("0 Answered");
	});

	it("renders answered status badge and timestamp when question is resolved", () => {
		studioStore.setState({
			clarifications: [
				{
					question_id: "q-441",
					concept_name: "Fund Advisor",
					question_text: "Clarification question text",
					certainty: "HIGH",
					rationale_options: [],
					evidence_citations: [],
					status: "ANSWERED",
					answered_at: "2026-11-11T03:00:00Z",
					selected_option_ids: ["opt-1"],
				},
			],
		});

		const html = renderToStaticMarkup(createElement(ClarificationPanel));

		expect(html).toContain("✓ Answered");
		expect(html).toContain("0 Pending");
		expect(html).toContain("1 Answered");
		expect(html).toContain("question-card-answered");
	});
});

describe("DiffOverlay", () => {
	it("renders strictly additive guarantee banner when breaking count is zero", () => {
		studioStore.setState({
			diffState: {
				active: true,
				mode: "unified",
				base_version: "1.0.0",
				target_version: "1.1.0",
				records: [
					{
						item_type: "concept",
						change_type: "added",
						identifier: "urn:ontology:entity:advisory_agreement",
						label: "Advisory Agreement",
						details: "Added 4 attributes, 1 FIBO mapping",
						is_breaking: false,
					},
				],
				summary: {
					added_count: 1,
					deleted_count: 0,
					modified_count: 0,
					breaking_count: 0,
				},
			},
		});

		const html = renderToStaticMarkup(createElement(DiffOverlay));

		expect(html).toContain("Ontology Version Diff: v1.0.0 → v1.1.0");
		expect(html).toContain("STRICTLY ADDITIVE GUARANTEE (ONT-EVO-001)");
		expect(html).toContain("+1 Added");
		expect(html).toContain("-0 Deleted");
		expect(html).toContain("Advisory Agreement");
		expect(html).toContain("urn:ontology:entity:advisory_agreement");
	});

	it("renders contract modification warning when breaking changes exist", () => {
		studioStore.setState({
			diffState: {
				active: true,
				mode: "unified",
				base_version: "1.0.0",
				target_version: "2.0.0",
				records: [
					{
						item_type: "concept",
						change_type: "deleted",
						identifier: "urn:ontology:entity:legacy_fund",
						label: "Legacy Fund",
						details: "Deleted deployed hub table",
						is_breaking: true,
					},
				],
				summary: {
					added_count: 0,
					deleted_count: 1,
					modified_count: 0,
					breaking_count: 1,
				},
			},
		});

		const html = renderToStaticMarkup(createElement(DiffOverlay));

		expect(html).toContain("CONTRACT MODIFICATION DETECTED");
		expect(html).toContain("1 breaking changes");
		expect(html).toContain("- DEL");
		expect(html).toContain("BREAKING");
	});

	it("renders side-by-side mode columns", () => {
		studioStore.setState({
			diffState: {
				active: true,
				mode: "side-by-side",
				base_version: "1.0.0",
				target_version: "1.1.0",
				records: [
					{
						item_type: "concept",
						change_type: "added",
						identifier: "urn:ontology:entity:new_deal",
						label: "New Deal",
						details: "Added concept",
						is_breaking: false,
					},
				],
				summary: {
					added_count: 1,
					deleted_count: 0,
					modified_count: 0,
					breaking_count: 0,
				},
			},
		});

		const html = renderToStaticMarkup(createElement(DiffOverlay));

		expect(html).toContain("Baseline (v1.0.0)");
		expect(html).toContain("Target (v1.1.0)");
		expect(html).toContain("New Deal");
		expect(html).toContain("data-testid=\"diff-side-by-side-view\"");
	});
});

describe("VisualEditorDrawer", () => {
	it("renders null when no node is selected", () => {
		studioStore.setState({ selectedNodeId: null, nodes: [] });
		const html = renderToStaticMarkup(createElement(VisualEditorDrawer));
		expect(html).toBe("");
	});

	it("renders form inputs with concept attributes, synonyms, and FIBO mappings when node is selected", () => {
		studioStore.setState({
			selectedNodeId: "urn:ontology:entity:fund",
			nodes: [
				{
					id: "urn:ontology:entity:fund",
					concept_uri: "urn:ontology:entity:fund",
					concept_name: "Investment Fund",
					elm_archetype: "CORE_CONCEPT",
					definition: "Vehicle collecting capital from limited partners.",
					target_hub: "hub_fund",
					parent_concept_uri: "urn:ontology:entity:financial_entity",
					attributes: [
						{ attribute_name: "fund_id", data_type: "string", is_optional: false, description: "Surrogate business key" },
						{ attribute_name: "nav_amount", data_type: "decimal", is_optional: true, description: "Net asset value" },
					],
					synonyms: ["Private Equity Vehicle", "Fund Entity"],
					external_mappings: [
						{ scheme: "fibo", target_uri: "fibo-fbc-fct-fse:InvestmentFund", confidence: 0.95, predicate: "skos:exactMatch" },
					],
					grounding_status: "GROUNDED",
				},
			],
		});

		const html = renderToStaticMarkup(createElement(VisualEditorDrawer));

		expect(html).toContain("CONCEPT EDITOR");
		expect(html).toContain("Investment Fund");
		expect(html).toContain("urn:ontology:entity:fund");
		expect(html).toContain("value=\"Investment Fund\"");
		expect(html).toContain("Vehicle collecting capital from limited partners.");
		expect(html).toContain("value=\"hub_fund\"");
		expect(html).toContain("value=\"urn:ontology:entity:financial_entity\"");
		expect(html).toContain("fund_id");
		expect(html).toContain("nav_amount");
		expect(html).toContain("Private Equity Vehicle");
		expect(html).toContain("Fund Entity");
		expect(html).toContain("FIBO");
		expect(html).toContain("fibo-fbc-fct-fse:InvestmentFund");
		expect(html).toContain("95%");
		expect(html).toContain("data-testid=\"save-concept-btn\"");
	});
});

describe("QualityFeedbackPanel", () => {
	it("renders dynamic gauges for passing acyclicity, compliant contracts, and grounding ratio", () => {
		studioStore.setState({
			nodes: [
				{
					id: "urn:ontology:entity:fund",
					concept_uri: "urn:ontology:entity:fund",
					concept_name: "Fund",
					elm_archetype: "CORE_CONCEPT",
					definition: "Fund entity.",
					grounding_status: "GROUNDED",
					attributes: [],
					synonyms: [],
					external_mappings: [],
				},
			],
			qualityScorecard: {
				grounding_score: 1.0,
				dag_acyclic: true,
				deployed_contracts_ok: true,
				yagni_compliant: true,
				checks: [
					{
						rule_id: "ONT-EVO-001",
						rule_name: "Strictly Additive Evolution",
						status: "PASS",
						details: "Zero breaking changes detected",
					},
					{
						rule_id: "ONT-EVO-002",
						rule_name: "Ontological Grounding",
						status: "PASS",
						details: "All concepts grounded",
					},
					{
						rule_id: "ONT-DAG-001",
						rule_name: "Acyclic Concept Inheritance",
						status: "PASS",
						details: "Zero cycles detected",
					},
				],
				waiver_applied: false,
				waiver_rationale: "",
				feedback_rating: 5,
				feedback_comment: "All rules pass",
			},
		});

		const html = renderToStaticMarkup(createElement(QualityFeedbackPanel));
		expect(html).toContain("data-testid=\"quality-feedback-panel\"");
		expect(html).toContain("data-testid=\"grounding-gauge-value\">100%<");
		expect(html).toContain("data-testid=\"acyclicity-gauge-value\">PASS<");
		expect(html).toContain("data-testid=\"table-contracts-gauge-value\">COMPLIANT<");
	});

	it("renders dynamic gauges for failing acyclicity, broken contracts, and N/A grounding ratio when empty", () => {
		studioStore.setState({
			nodes: [],
			qualityScorecard: {
				grounding_score: 0,
				dag_acyclic: false,
				deployed_contracts_ok: false,
				yagni_compliant: false,
				checks: [
					{
						rule_id: "ONT-EVO-001",
						rule_name: "Strictly Additive Evolution",
						status: "FAIL",
						details: "Breaking change detected",
					},
					{
						rule_id: "ONT-DAG-001",
						rule_name: "Acyclic Concept Inheritance",
						status: "FAIL",
						details: "Cycle detected",
					},
				],
				waiver_applied: false,
				waiver_rationale: "",
				feedback_rating: 1,
				feedback_comment: "Issues found",
			},
		});

		const html = renderToStaticMarkup(createElement(QualityFeedbackPanel));
		expect(html).toContain("data-testid=\"grounding-gauge-value\">N/A<");
		expect(html).toContain("data-testid=\"acyclicity-gauge-value\">FAIL<");
		expect(html).toContain("data-testid=\"table-contracts-gauge-value\">VIOLATION<");
	});
});

describe("VisualEditorDrawer Validation (ONT-EVO-001 & ONT-DAG-001)", () => {
	const committedNode = {
		id: "c_committed",
		concept_uri: "urn:opendv:concept:c_committed",
		concept_name: "Committed Entity",
		elm_archetype: "CORE_CONCEPT" as const,
		definition: "Immutable entity.",
		target_hub: "hub_financial",
		parent_concept_uri: "urn:opendv:concept:c_parent",
		attributes: [
			{ attribute_name: "id", data_type: "string", is_optional: false },
		],
		synonyms: ["Old Synonym"],
		external_mappings: [
			{ scheme: "FIBO", target_uri: "fibo:Entity", confidence: 0.95 },
		],
		grounding_status: "GROUNDED" as const,
	};

	const baseDraft: ConceptEditDraft = {
		concept_uri: "urn:opendv:concept:c_committed",
		concept_name: "Committed Entity",
		elm_archetype: "CORE_CONCEPT",
		definition: "Immutable entity.",
		target_hub: "hub_financial",
		parent_concept_uri: "urn:opendv:concept:c_parent",
		attributes: [
			{ attribute_name: "id", data_type: "string", is_optional: false },
		],
		synonyms: ["Old Synonym"],
		external_mappings: [
			{ scheme: "FIBO", target_uri: "fibo:Entity", confidence: 0.95 },
		],
	};

	it("passes valid purely additive changes on committed concept", () => {
		const additiveDraft: ConceptEditDraft = {
			...baseDraft,
			attributes: [
				...baseDraft.attributes,
				{ attribute_name: "new_attr", data_type: "string", is_optional: true },
			],
			synonyms: [...baseDraft.synonyms, "New Synonym"],
			external_mappings: [
				...baseDraft.external_mappings,
				{ scheme: "GLEIF", target_uri: "gleif:Entity", confidence: 0.9 },
			],
		};
		const errors = validateConceptEdit(additiveDraft, committedNode);
		expect(errors).toHaveLength(0);
	});

	it("blocks renaming committed concept (CANNOT_RENAME_COMMITTED_CONCEPT)", () => {
		const errors = validateConceptEdit(
			{ ...baseDraft, concept_name: "Renamed Entity" },
			committedNode,
		);
		expect(
			errors.some((e) => e.includes("CANNOT_RENAME_COMMITTED_CONCEPT")),
		).toBe(true);
	});

	it("blocks modifying archetype on committed concept (CANNOT_CHANGE_ARCHETYPE)", () => {
		const errors = validateConceptEdit(
			{ ...baseDraft, elm_archetype: "REFERENCE_CONCEPT" },
			committedNode,
		);
		expect(errors.some((e) => e.includes("CANNOT_CHANGE_ARCHETYPE"))).toBe(
			true,
		);
	});

	it("blocks reparenting committed concept (CANNOT_REPARENT_COMMITTED_CONCEPT)", () => {
		const errors = validateConceptEdit(
			{ ...baseDraft, parent_concept_uri: "urn:opendv:concept:new_parent" },
			committedNode,
		);
		expect(
			errors.some((e) => e.includes("CANNOT_REPARENT_COMMITTED_CONCEPT")),
		).toBe(true);
	});

	it("blocks modifying or clearing committed target_hub (CANNOT_MODIFY_COMMITTED_TARGET_HUB)", () => {
		const retargetErrors = validateConceptEdit(
			{ ...baseDraft, target_hub: "hub_different" },
			committedNode,
		);
		expect(
			retargetErrors.some((e) =>
				e.includes("CANNOT_MODIFY_COMMITTED_TARGET_HUB"),
			),
		).toBe(true);

		const clearErrors = validateConceptEdit(
			{ ...baseDraft, target_hub: null },
			committedNode,
		);
		expect(
			clearErrors.some((e) =>
				e.includes("CANNOT_MODIFY_COMMITTED_TARGET_HUB"),
			),
		).toBe(true);
	});

	it("blocks removing committed synonyms, attributes, or mappings under ONT-EVO-001", () => {
		const removeSynErrors = validateConceptEdit(
			{ ...baseDraft, synonyms: [] },
			committedNode,
		);
		expect(
			removeSynErrors.some((e) =>
				e.includes("CANNOT_REMOVE_COMMITTED_SYNONYM"),
			),
		).toBe(true);

		const removeAttrErrors = validateConceptEdit(
			{ ...baseDraft, attributes: [] },
			committedNode,
		);
		expect(
			removeAttrErrors.some((e) =>
				e.includes("CANNOT_REMOVE_COMMITTED_ATTRIBUTE"),
			),
		).toBe(true);

		const removeMapErrors = validateConceptEdit(
			{ ...baseDraft, external_mappings: [] },
			committedNode,
		);
		expect(
			removeMapErrors.some((e) =>
				e.includes("CANNOT_REMOVE_COMMITTED_MAPPING"),
			),
		).toBe(true);

		const mutateAttrTypeErrors = validateConceptEdit(
			{
				...baseDraft,
				attributes: [
					{ attribute_name: "id", data_type: "integer", is_optional: false },
				],
			},
			committedNode,
		);
		expect(
			mutateAttrTypeErrors.some((e) =>
				e.includes("CANNOT_MODIFY_COMMITTED_ATTRIBUTE_TYPE"),
			),
		).toBe(true);
	});

	it("prevents self-parenting and multi-hop cycles under ONT-DAG-001", () => {
		const selfParentErrors = validateConceptEdit(
			{ ...baseDraft, parent_concept_uri: baseDraft.concept_uri },
			null,
		);
		expect(selfParentErrors.some((e) => e.includes("ONT-DAG-001"))).toBe(true);

		const allNodes = [
			{
				...committedNode,
				id: "a",
				concept_uri: "urn:opendv:concept:a",
				parent_concept_uri: "urn:opendv:concept:b",
			},
			{
				...committedNode,
				id: "b",
				concept_uri: "urn:opendv:concept:b",
				parent_concept_uri: "urn:opendv:concept:a",
			},
		];
		const cycleErrors = validateConceptEdit(
			{
				...baseDraft,
				concept_uri: "urn:opendv:concept:a",
				parent_concept_uri: "urn:opendv:concept:b",
			},
			null,
			allNodes,
		);
		expect(cycleErrors.some((e) => e.includes("CYCLE_DETECTED"))).toBe(true);
	});

	it("enforces mandatory concept name and valid target_hub syntax", () => {
		const emptyNameErrors = validateConceptEdit(
			{ ...baseDraft, concept_name: "  " },
			null,
		);
		expect(emptyNameErrors.some((e) => e.includes("mandatory"))).toBe(true);

		const badHubErrors = validateConceptEdit(
			{ ...baseDraft, target_hub: "bad_hub_syntax!" },
			null,
		);
		expect(
			badHubErrors.some((e) =>
				e.includes("Target Hub name must match pattern"),
			),
		).toBe(true);

		const dupAttrErrors = validateConceptEdit(
			{
				...baseDraft,
				attributes: [
					{ attribute_name: "ID", data_type: "string", is_optional: false },
					{ attribute_name: "id", data_type: "integer", is_optional: true },
				],
			},
			null,
		);
		expect(
			dupAttrErrors.some((e) => e.includes("Duplicate attribute names")),
		).toBe(true);
	});

	it("validateConceptEdit blocks adding a parent to a committed root concept (MIN-10)", () => {
		const committedRoot = {
			id: "root",
			concept_uri: "urn:ontology:entity:root",
			concept_name: "Root",
			elm_archetype: "CORE_CONCEPT",
			definition: "Root entity",
			grounding_status: "GROUNDED" as const,
			attributes: [],
			synonyms: [],
			external_mappings: [],
		};
		const draft = {
			concept_uri: "urn:ontology:entity:root",
			concept_name: "Root",
			elm_archetype: "CORE_CONCEPT",
			definition: "Root entity",
			parent_concept_uri: "urn:ontology:entity:new_parent",
			attributes: [],
			synonyms: [],
			external_mappings: [],
		};
		const errors = validateConceptEdit(draft, committedRoot, [committedRoot]);
		expect(
			errors.some((e) => e.includes("CANNOT_REPARENT_COMMITTED_CONCEPT")),
		).toBe(true);
	});

	it("validateConceptEdit blocks removing definition of committed concept (MAJ-01)", () => {
		const committedWithDef = {
			id: "c1",
			concept_uri: "urn:ontology:entity:c1",
			concept_name: "C1",
			elm_archetype: "CORE_CONCEPT",
			definition: "Non-empty committed definition",
			grounding_status: "GROUNDED" as const,
			attributes: [],
			synonyms: [],
			external_mappings: [],
		};
		const draft = {
			concept_uri: "urn:ontology:entity:c1",
			concept_name: "C1",
			elm_archetype: "CORE_CONCEPT",
			definition: "   ",
			attributes: [],
			synonyms: [],
			external_mappings: [],
		};
		const errors = validateConceptEdit(draft, committedWithDef, [
			committedWithDef,
		]);
		expect(
			errors.some((e) => e.includes("CANNOT_REMOVE_COMMITTED_DEFINITION")),
		).toBe(true);
	});

	it("validateConceptEdit allows removing uncommitted draft values on committed concept", () => {
		const committedNode = {
			id: "c1",
			concept_uri: "urn:ontology:entity:c1",
			concept_name: "C1",
			elm_archetype: "CORE_CONCEPT",
			definition: "Committed def",
			target_hub: null,
			parent_concept_uri: null,
			grounding_status: "GROUNDED" as const,
			attributes: [],
			synonyms: [],
			external_mappings: [],
		};
		const draftWithClearedDraftHub: ConceptEditDraft = {
			concept_uri: "urn:ontology:entity:c1",
			concept_name: "C1",
			elm_archetype: "CORE_CONCEPT",
			definition: "Committed def",
			target_hub: null,
			parent_concept_uri: null,
			attributes: [],
			synonyms: [],
			external_mappings: [],
		};
		const errors = validateConceptEdit(draftWithClearedDraftHub, committedNode);
		expect(errors).toHaveLength(0);
	});

	it("VisualEditorDrawer renders concept details and handles validation", () => {
		const concept = {
			id: "c1",
			concept_uri: "urn:ontology:entity:fund",
			concept_name: "Fund",
			elm_archetype: "CORE_CONCEPT",
			definition: "Investment fund entity.",
			grounding_status: "GROUNDED" as const,
			attributes: [
				{ attribute_name: "id", data_type: "string", is_optional: false },
			],
			synonyms: ["Mutual Fund"],
			external_mappings: [],
		};
		studioStore.setState({
			nodes: [concept],
			committedNodes: [concept],
			selectedNodeId: "urn:ontology:entity:fund",
		});
		const html = renderToStaticMarkup(createElement(VisualEditorDrawer));
		expect(html).toContain("data-testid=\"visual-editor-drawer\"");
		expect(html).toContain("Fund");
		expect(html).toContain("urn:ontology:entity:fund");
	});

	it("SourcesPanel renders inventory with TXT support and privacy pills", () => {
		studioStore.setState({
			sources: [
				{
					source_id: "s1",
					source_sha256: "abc123456789",
					file_name: "prospectus.pdf",
					size_bytes: 1024,
					mime_type: "application/pdf",
					do_not_send_to_agent: true,
				},
			],
		});
		const html = renderToStaticMarkup(createElement(SourcesPanel));
		expect(html).toContain("data-testid=\"sources-panel\"");
		expect(html).toContain("prospectus.pdf");
		expect(html).toContain("CONFIDENTIAL");
		expect(html).toContain(
			"Supported: PDF, DOCX, XLSX, HTML, JSON, CSV, Markdown, TXT",
		);
	});

	it("ClarificationPanel renders question list and pending count badge", () => {
		studioStore.setState({
			clarifications: [
				{
					question_id: "q1",
					concept_name: "Fund",
					question_text: "Is this open-ended or closed-ended?",
					certainty: "HIGH",
					rationale_options: [
						{ id: "opt1", label: "Open-ended fund" },
						{ id: "opt2", label: "Closed-ended fund" },
					],
					evidence_citations: [],
					status: "PENDING",
				},
			],
		});
		const html = renderToStaticMarkup(createElement(ClarificationPanel));
		expect(html).toContain("data-testid=\"clarification-panel\"");
		expect(html).toContain("Is this open-ended or closed-ended?");
		expect(html).toContain("Open-ended fund");
		expect(html).toContain("1 Pending");
	});
});
