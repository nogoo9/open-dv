/**
 * Component & Validation Tests: VisualEditorDrawer (Task 5D.5, #226)
 * (apps/opendv-web-workbench/tests/ontology-panels.test.tsx)
 */

import { beforeEach, describe, expect, it } from "bun:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import {
	VisualEditorDrawer,
	validateConceptEdit,
	type ConceptEditDraft,
} from "../src/ontology/components/VisualEditorDrawer";
import { studioStore } from "../src/ontology/store/studio-store";

beforeEach(() => {
	studioStore.getState().reset();
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
});
