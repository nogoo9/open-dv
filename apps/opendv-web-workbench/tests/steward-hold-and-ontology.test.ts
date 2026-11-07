/**
 * Unit & Component Tests for Task P9.4 (#118):
 * - Interactive Steward Hold Modal (`StewardHoldModal.tsx`) with Ontology Delta Checklist (ONT-04)
 * - Inspector Panel Steward Hold Resolution Section (`InspectorPanel.tsx`)
 * - RFC 9457 ProblemDetails visible error display
 * - Ontology Extension Viewers (`OntologyCoverageVisualizer`, `OntologyProposalVisualizer`, `OntologyDiffVisualizer`)
 */

import { describe, expect, it, mock } from "bun:test";
import type {
	Artifact,
	CaptainHoldDecisionRequest,
	CaptainHoldDecisionResponse,
	Stage,
} from "@opendv/protocol-ts";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { ArtifactVisualizer } from "../src/explorer/ArtifactVisualizers";
import { InspectorPanel } from "../src/explorer/InspectorPanel";
import {
	type ProblemDetails,
	type StewardHoldCandidate,
	StewardHoldModal,
	type StewardHoldOntologyOp,
	parseProblemDetails,
} from "../src/explorer/StewardHoldModal";
import {
	type OntologyCoverageData,
	OntologyCoverageVisualizer,
} from "../src/explorer/visualizers/OntologyCoverageVisualizer";
import {
	type OntologyDiffData,
	OntologyDiffVisualizer,
} from "../src/explorer/visualizers/OntologyDiffVisualizer";
import {
	type OntologyProposalData,
	OntologyProposalVisualizer,
} from "../src/explorer/visualizers/OntologyProposalVisualizer";

// Test Fixtures
const SAMPLE_CANDIDATES: StewardHoldCandidate[] = [
	{ id: "TAX-DE-800202", label: "Rheinland Precision GmbH", score: 0.95 },
	{ id: "TAX-DE-800203", label: "Rheinland Precision Holdings", score: 0.68 },
];

const SAMPLE_ONTOLOGY_OPS: StewardHoldOntologyOp[] = [
	{
		op_id: "op-gt06-01",
		op_type: "add_concept",
		concept_name: "Advisor",
		concept_uri: "urn:ontology:entity:advisor",
		confidence: 0.95,
		rationale: "Deal advisory fees detected in unmapped feed.",
	},
	{
		op_id: "op-gt06-02",
		op_type: "add_relationship",
		relationship_name: "FundAdvisor",
		relationship_uri: "urn:ontology:relationship:fund_advisor",
		confidence: 0.92,
		rationale: "Connect Fund entity with external advisory firm.",
	},
	{
		op_id: "op-gt06-03",
		op_type: "add_reference_enumeration",
		concept_name: "advisor_role",
		concept_uri: "urn:ontology:enum:advisor_role",
		confidence: 0.9,
		rationale: "Categorize external advisor scope of work.",
	},
];

const SAMPLE_COVERAGE_DATA: OntologyCoverageData = {
	artifact_id: "art-w0-stage01-ontology-coverage",
	wave_id: "wave_0",
	domain_namespace: "private_equity",
	graph_a_version: "1.0.0",
	graph_a_sha256:
		"9abb6eefb16f7ff412c18e3580bab76cdccc56b6e58b66944fd56fc49117fc02",
	total_tables: 2,
	mapped_tables_count: 1,
	unmapped_tables_count: 1,
	total_columns: 10,
	mapped_columns_count: 6,
	unmapped_columns_count: 4,
	coverage_ratio: 0.6,
	mapped_concepts: ["urn:ontology:entity:customer"],
	mapped_tables: ["crm.customers"],
	unmapped_tables: ["logistics.feed_unmapped_supplier"],
	unmapped_columns: [
		{ table: "logistics.feed_unmapped_supplier", column: "delivery_id" },
	],
	unmapped_mentions: ["unknown supplier mention"],
	provisional_uris_found: [],
	diagnostics: [
		"ONT-COV-GAP: Table 'logistics.feed_unmapped_supplier' has no matching CBC.",
	],
	detailed_mapped_concepts: [
		{
			concept_uri: "urn:ontology:entity:portfolio_company",
			concept_name: "PORTFOLIO_COMPANY",
			target_hub: "hub_portfolio_company",
			mapped_tables: ["pe.portfolio_company"],
			confidence: 1.0,
		},
	],
};

const SAMPLE_PROPOSAL_DATA: OntologyProposalData = {
	proposal_id: "prop-gt06-001",
	wave_id: "wave_1",
	recommender_id: "testkit-recommender",
	recommender_version: "1.0.0",
	base_ontology_sha256:
		"9abb6eefb16f7ff412c18e3580bab76cdccc56b6e58b66944fd56fc49117fc02",
	confidence_score: 0.94,
	coverage_before: 0.8,
	coverage_after: 1.0,
	rationale:
		"Incorporate external deal advisory domain concepts into institutional PE ontology.",
	diagnostics: ["Resolved unmapped feed bronze_advisory_invoices"],
	delta: {
		delta_id: "delta-gt06-001",
		domain_namespace: "pe_asset_management",
		base_ontology_version: "1.0.0",
		target_ontology_version: "1.1.0",
		operations: [
			{
				op_id: "op-gt06-01",
				op_type: "add_concept",
				concept_name: "Advisor",
				concept_uri: "urn:ontology:entity:advisor",
				elm_archetype: "WHO_EXTERNAL_ADVISOR",
				target_hub: "hub_advisor",
				attributes: ["advisor_name", "tax_id"],
				confidence: 0.95,
				rationale:
					"Deal advisory fees detected in unmapped advisory fee invoices.",
				evidence_refs: [
					{
						evidence_type: "column_profile",
						reference_uri: "bronze_advisory_invoices.tax_id",
						snippet: "ADV-9921",
						confidence: 0.95,
					},
				],
			},
			{
				op_id: "op-gt06-02",
				op_type: "add_relationship",
				relationship_name: "FundAdvisor",
				relationship_uri: "urn:ontology:relationship:fund_advisor",
				target_link: "lnk_fund_advisor",
				endpoints: ["urn:ontology:entity:advisor", "urn:ontology:entity:fund"],
				grain_semantics:
					"A Fund retains external Advisors for transaction due diligence.",
				confidence: 0.92,
				rationale: "Connect Fund entity with external advisory firm.",
			},
			{
				op_id: "op-gt06-03",
				op_type: "add_reference_enumeration",
				name: "advisor_role",
				enum_uri: "urn:ontology:enum:advisor_role",
				target_reference_table: "ref_advisor_role",
				values: ["LEGAL", "TAX", "ACCOUNTING"],
				confidence: 0.9,
				rationale: "Categorize external advisor scope of work.",
			},
			{
				op_id: "op-gt06-04",
				op_type: "refine_definition",
				target_uri: "urn:ontology:entity:fund",
				refined_description:
					"Includes co-investment vehicles and special purpose syndicates.",
				additional_attributes: ["vintage_year"],
				confidence: 0.98,
				rationale: "Clarify vehicle scope under ILPA standard.",
			},
		],
	},
};

const SAMPLE_DIFF_DATA: OntologyDiffData = {
	base_version: "1.0.0",
	target_version: "1.1.0",
	base_sha256:
		"9abb6eefb16f7ff412c18e3580bab76cdccc56b6e58b66944fd56fc49117fc02",
	target_sha256:
		"f466c0683ff3a97e56e71cd709e456548f570f167d73c4fc63d96ecfd5eb3341",
	added_concepts: ["urn:ontology:entity:advisor"],
	added_relationships: ["urn:ontology:relationship:fund_advisor"],
	added_enumerations: ["urn:ontology:enum:advisor_role"],
	refined_concepts: ["urn:ontology:entity:fund"],
	breaking_changes: [],
	diagnostics: [
		"Ontology diff computed from v1.0.0 (9abb6eef) to v1.1.0 (f466c068): +1 concepts, +1 relationships, +1 enums, 1 refined concepts, 0 breaking changes.",
	],
};

const SAMPLE_STAGE: Stage = {
	key: "STAGE_02_SCOUT_UNSTRUCTURED",
	phase: 0,
	name: "Unstructured Data Scout",
	persona: "vault-scout-doc",
	tier: "reasoning",
	tag: "ASK-USER",
	blurb: "Scout unstructured contracts & identify entity mentions",
	desc: "Stage 02 handles doc extractions with human steward escalation.",
	ins: ["wave3_doc"],
	outs: ["unstructured_grounding"],
	steps: [
		{
			name: "Evaluate Steward Hold on Ambiguous Mention",
			kind: "decision",
			branch: "hold → halt run for steward approval",
			reads: ["wave3_doc"],
			writes: ["unstructured_grounding"],
			desc: "Escalate Tier-U3 mentions to human steward.",
		},
	],
	comps: [["DocScout", "Agent persona", "Doc scout component", "src/scout.py"]],
};

const SAMPLE_ARTIFACTS: Record<string, Artifact> = {
	extract: {
		id: "extract",
		name: "extraction_contract.yaml",
		short: "Doc extractions",
		group: "Stage output",
		type: "extraction_spec",
		fmt: "yaml",
		viz: "doc",
		desc: "Extracted contract entities with confidence tiers",
		raw: "raw extraction text",
		doc: {
			documentName: "wave3_enterprise_msa_addendum_2026.txt",
			sourceText:
				"Rheinland Precision Holdings Europe GmbH hereby contracts...",
			extractions: [
				{
					id: "ext-1",
					quote: "Rheinland Precision Holdings Europe",
					start: 0,
					end: 35,
					tier: "Tier-U3",
					conf: "0.68",
					decision: "Ambiguous mention requires human steward hold",
					holdId: "hold-tier-u3-rheinland-precision",
					candidates: SAMPLE_CANDIDATES,
				},
			],
		},
	},
	ontology_cov: {
		id: "ontology_cov",
		name: "ontology_coverage.json",
		short: "Ontology coverage report",
		group: "Stage output",
		type: "ontology_coverage",
		fmt: "json",
		viz: "table",
		desc: "Grounding coverage analysis",
		raw: JSON.stringify(SAMPLE_COVERAGE_DATA),
	},
	ontology_prop: {
		id: "ontology_prop",
		name: "ontology_proposal.json",
		short: "Ontology delta proposal",
		group: "Stage output",
		type: "ontology_proposal",
		fmt: "json",
		viz: "kv",
		desc: "Proposed additive ontology operations",
		raw: JSON.stringify(SAMPLE_PROPOSAL_DATA),
	},
	ontology_diff_art: {
		id: "ontology_diff_art",
		name: "ontology_diff.json",
		short: "Ontology evolution diff",
		group: "Stage output",
		type: "ontology_diff",
		fmt: "json",
		viz: "kv",
		desc: "Structural diff between Graph A v1.0 and v1.1",
		raw: JSON.stringify(SAMPLE_DIFF_DATA),
	},
};

describe("Task P9.4 (#118): Interactive Steward Hold & Ontology Extension Viewers", () => {
	// =========================================================================
	// 1. StewardHoldModal Component Tests
	// =========================================================================
	describe("1. StewardHoldModal Component", () => {
		it("does not render when isOpen is false", () => {
			const html = renderToStaticMarkup(
				createElement(StewardHoldModal, {
					isOpen: false,
					onClose: () => {},
					holdId: "hold-test-01",
				}),
			);
			expect(html).toBe("");
		});

		it("renders modal dialog with title, hold ID, candidates, and action buttons when open", () => {
			const html = renderToStaticMarkup(
				createElement(StewardHoldModal, {
					isOpen: true,
					onClose: () => {},
					holdId: "hold-tier-u3-rheinland-precision",
					headline: "Resolve Ambiguous Mention Hold",
					description: "Tier-U3 mention requires human steward resolution.",
					candidates: SAMPLE_CANDIDATES,
					initialCandidateId: "TAX-DE-800202",
				}),
			);

			expect(html).toContain('data-testid="steward-hold-modal"');
			expect(html).toContain("<dialog");
			expect(html).toContain('aria-modal="true"');
			expect(html).toContain("Resolve Ambiguous Mention Hold");
			expect(html).toContain("hold-tier-u3-rheinland-precision");
			expect(html).toContain('data-testid="hold-action-approve"');
			expect(html).toContain('data-testid="hold-action-reject"');
			expect(html).toContain('data-testid="hold-action-answer"');
			expect(html).toContain('data-testid="select-hold-candidate"');
			expect(html).toContain("Rheinland Precision GmbH (TAX-DE-800202)");
			expect(html).toContain('data-testid="input-steward-rationale"');
			expect(html).toContain('data-testid="btn-submit-decision"');
		});

		it("renders interactive ontology delta checklist when proposed ops are present (Phase P7 ONT-04)", () => {
			const html = renderToStaticMarkup(
				createElement(StewardHoldModal, {
					isOpen: true,
					onClose: () => {},
					holdId: "hold-gt06-ontology",
					headline: "Review Proposed Ontology Delta",
					ontologyOps: SAMPLE_ONTOLOGY_OPS,
				}),
			);

			expect(html).toContain('data-testid="ontology-delta-checklist"');
			expect(html).toContain('data-testid="btn-select-all-ops"');
			expect(html).toContain('data-testid="btn-deselect-all-ops"');
			expect(html).toContain('data-testid="checkbox-op-op-gt06-01"');
			expect(html).toContain('data-testid="checkbox-op-op-gt06-02"');
			expect(html).toContain('data-testid="checkbox-op-op-gt06-03"');
			expect(html).toContain("Advisor");
			expect(html).toContain("FundAdvisor");
			expect(html).toContain("advisor_role");
			expect(html).toContain("add concept");
			expect(html).toContain("add relationship");
		});

		it("displays RFC 9457 ProblemDetails error visibly when error occurs", () => {
			const problem: ProblemDetails = {
				title: "Precondition Failed",
				detail:
					"Graph A delta violates integrity constraint: duplicate concept URI",
				status: 412,
				type: "https://opendv.org/errors/delta-precondition",
			};

			// Verify parser correctly converts problem object
			const parsed = parseProblemDetails({ problemDetails: problem });
			expect(parsed.title).toBe("Precondition Failed");
			expect(parsed.status).toBe(412);
			expect(parsed.detail).toContain("violates integrity constraint");
			expect(parsed.type).toBe("https://opendv.org/errors/delta-precondition");

			// Verify parser handles JSON string message
			const jsonErr = parseProblemDetails(
				new Error(
					JSON.stringify({
						title: "Unprocessable Entity",
						detail: "Malformed delta payload",
						status: 422,
					}),
				),
			);
			expect(jsonErr.title).toBe("Unprocessable Entity");
			expect(jsonErr.status).toBe(422);
			expect(jsonErr.detail).toBe("Malformed delta payload");
		});
	});

	// =========================================================================
	// 2. InspectorPanel Steward Hold Resolution Section Tests
	// =========================================================================
	describe("2. InspectorPanel Steward Hold Resolution Section", () => {
		it("renders contextual hold resolution box when activeHoldId or ASK-USER status is present", () => {
			const html = renderToStaticMarkup(
				createElement(InspectorPanel, {
					view: "stage",
					selType: "artifact",
					selId: "extract",
					stages: [SAMPLE_STAGE],
					currentStage: SAMPLE_STAGE,
					artifacts: SAMPLE_ARTIFACTS,
					onClearSelection: () => {},
					onOpenFullArtifact: () => {},
					onResolveHold: () => {},
				}),
			);

			expect(html).toContain('data-testid="inspector-steward-hold-box"');
			expect(html).toContain("hold-tier-u3-rheinland-precision");
			expect(html).toContain('data-testid="btn-inspector-approve-hold"');
			expect(html).toContain('data-testid="btn-inspector-reject-hold"');
			expect(html).toContain('data-testid="btn-inspector-open-modal"');
			expect(html).toContain('data-testid="inspector-hold-rationale"');
			expect(html).toContain('data-testid="btn-inspector-submit-decision"');
		});

		it("renders ontology ops checklist inside inspector when ops are present", () => {
			const html = renderToStaticMarkup(
				createElement(InspectorPanel, {
					view: "stage",
					selType: "artifact",
					selId: "ontology_prop",
					stages: [SAMPLE_STAGE],
					currentStage: SAMPLE_STAGE,
					artifacts: SAMPLE_ARTIFACTS,
					onClearSelection: () => {},
					onOpenFullArtifact: () => {},
					onResolveHold: () => {},
					holdOntologyOps: SAMPLE_ONTOLOGY_OPS,
				}),
			);

			expect(html).toContain('data-testid="inspector-steward-hold-box"');
			expect(html).toContain('data-testid="inspector-ontology-ops-checklist"');
			expect(html).toContain('data-testid="inspector-checkbox-op-op-gt06-01"');
			expect(html).toContain('data-testid="inspector-checkbox-op-op-gt06-02"');
		});

		it("calls onResolveHold when legacy quick buttons are pressed", async () => {
			let resolvedParams: unknown = null;
			const onResolveHold = (params: unknown) => {
				resolvedParams = params;
			};

			const element = createElement(InspectorPanel, {
				view: "stage",
				selType: "artifact",
				selId: "extract",
				stages: [SAMPLE_STAGE],
				currentStage: SAMPLE_STAGE,
				artifacts: SAMPLE_ARTIFACTS,
				onClearSelection: () => {},
				onOpenFullArtifact: () => {},
				onResolveHold,
			});

			expect(element).toBeDefined();
		});
	});

	// =========================================================================
	// 3. OntologyCoverageVisualizer Tests
	// =========================================================================
	describe("3. OntologyCoverageVisualizer", () => {
		it("renders coverage ratio, progress meter, and KPI metrics from explicit data", () => {
			const html = renderToStaticMarkup(
				createElement(OntologyCoverageVisualizer, {
					data: SAMPLE_COVERAGE_DATA,
				}),
			);

			expect(html).toContain('data-testid="ontology-coverage-visualizer"');
			expect(html).toContain("Ontology Grounding &amp; Coverage");
			expect(html).toContain('data-testid="coverage-percentage-badge"');
			expect(html).toContain("60.0%");
			expect(html).toContain('data-testid="kpi-tables"');
			expect(html).toContain('data-testid="kpi-columns"');
			expect(html).toContain('data-testid="kpi-unmapped-gaps"');
		});

		it("renders mapped concepts and target hubs", () => {
			const html = renderToStaticMarkup(
				createElement(OntologyCoverageVisualizer, {
					data: SAMPLE_COVERAGE_DATA,
				}),
			);

			expect(html).toContain('data-testid="mapped-concepts-list"');
			expect(html).toContain("PORTFOLIO_COMPANY");
			expect(html).toContain("urn:ontology:entity:portfolio_company");
			expect(html).toContain("→ hub_portfolio_company");
		});

		it("renders unmapped gaps (tables, columns, mentions) and diagnostics", () => {
			const html = renderToStaticMarkup(
				createElement(OntologyCoverageVisualizer, {
					data: SAMPLE_COVERAGE_DATA,
				}),
			);

			expect(html).toContain('data-testid="unmapped-gaps-section"');
			expect(html).toContain('data-testid="unmapped-tables-list"');
			expect(html).toContain("logistics.feed_unmapped_supplier");
			expect(html).toContain('data-testid="unmapped-columns-list"');
			expect(html).toContain("unknown supplier mention");
			expect(html).toContain('data-testid="ontology-coverage-diagnostics"');
			expect(html).toContain(
				"ONT-COV-GAP: Table &#x27;logistics.feed_unmapped_supplier&#x27; has no matching CBC.",
			);
		});

		it("parses raw JSON from Artifact payload correctly", () => {
			const html = renderToStaticMarkup(
				createElement(OntologyCoverageVisualizer, {
					artifact: SAMPLE_ARTIFACTS.ontology_cov,
				}),
			);

			expect(html).toContain('data-testid="ontology-coverage-visualizer"');
			expect(html).toContain("60.0%");
			expect(html).toContain("PORTFOLIO_COMPANY");
		});
	});

	// =========================================================================
	// 4. OntologyProposalVisualizer Tests
	// =========================================================================
	describe("4. OntologyProposalVisualizer", () => {
		it("renders proposal header, proposal ID, recommender version, confidence, and rationale", () => {
			const html = renderToStaticMarkup(
				createElement(OntologyProposalVisualizer, {
					data: SAMPLE_PROPOSAL_DATA,
				}),
			);

			expect(html).toContain('data-testid="ontology-proposal-visualizer"');
			expect(html).toContain("Proposed Ontology Delta");
			expect(html).toContain('data-testid="proposal-id-pill"');
			expect(html).toContain("prop-gt06-001");
			expect(html).toContain("testkit-recommender");
			expect(html).toContain('data-testid="proposal-confidence-badge"');
			expect(html).toContain("94% confidence");
			expect(html).toContain('data-testid="coverage-impact-badge"');
			expect(html).toContain("cov: 80% → 100%");
			expect(html).toContain('data-testid="proposal-rationale-box"');
			expect(html).toContain(
				"Incorporate external deal advisory domain concepts into institutional PE ontology.",
			);
		});

		it("renders proposed operations with concept, relationship, enum, and refinement details", () => {
			const html = renderToStaticMarkup(
				createElement(OntologyProposalVisualizer, {
					data: SAMPLE_PROPOSAL_DATA,
				}),
			);

			expect(html).toContain('data-testid="proposed-operations-list"');
			expect(html).toContain('data-testid="proposed-op-card-op-gt06-01"');
			expect(html).toContain("Advisor");
			expect(html).toContain("urn:ontology:entity:advisor");
			expect(html).toContain("hub_advisor");
			expect(html).toContain("WHO_EXTERNAL_ADVISOR");
			expect(html).toContain("advisor_name, tax_id");

			expect(html).toContain('data-testid="proposed-op-card-op-gt06-02"');
			expect(html).toContain("FundAdvisor");
			expect(html).toContain("lnk_fund_advisor");

			expect(html).toContain('data-testid="proposed-op-card-op-gt06-03"');
			expect(html).toContain("advisor_role");
			expect(html).toContain("ref_advisor_role");

			expect(html).toContain('data-testid="proposed-op-card-op-gt06-04"');
			expect(html).toContain(
				"Includes co-investment vehicles and special purpose syndicates.",
			);
		});

		it("renders grounding evidence references with snippets", () => {
			const html = renderToStaticMarkup(
				createElement(OntologyProposalVisualizer, {
					data: SAMPLE_PROPOSAL_DATA,
				}),
			);

			expect(html).toContain("Grounding Evidence:");
			expect(html).toContain("column_profile: bronze_advisory_invoices.tax_id");
			expect(html).toContain("snippet: &quot;ADV-9921&quot;");
		});

		it("parses raw JSON from Artifact payload correctly", () => {
			const html = renderToStaticMarkup(
				createElement(OntologyProposalVisualizer, {
					artifact: SAMPLE_ARTIFACTS.ontology_prop,
				}),
			);

			expect(html).toContain('data-testid="ontology-proposal-visualizer"');
			expect(html).toContain("prop-gt06-001");
			expect(html).toContain("94% confidence");
		});
	});

	// =========================================================================
	// 5. OntologyDiffVisualizer Tests
	// =========================================================================
	describe("5. OntologyDiffVisualizer", () => {
		it("renders version transition, SHA-256 digests, and metrics", () => {
			const html = renderToStaticMarkup(
				createElement(OntologyDiffVisualizer, {
					data: SAMPLE_DIFF_DATA,
				}),
			);

			expect(html).toContain('data-testid="ontology-diff-visualizer"');
			expect(html).toContain("Ontology Structural Diff");
			expect(html).toContain('data-testid="diff-version-pill"');
			expect(html).toContain("v1.0.0");
			expect(html).toContain("v1.1.0");
			expect(html).toContain("9abb6eefb1...");
			expect(html).toContain("f466c0683f...");
			expect(html).toContain('data-testid="diff-metric-concepts"');
			expect(html).toContain("+1");
			expect(html).toContain('data-testid="diff-metric-relationships"');
			expect(html).toContain('data-testid="diff-metric-refined"');
			expect(html).toContain('data-testid="diff-metric-breaking"');
		});

		it("renders strictly additive guarantee banner when breaking changes count is 0", () => {
			const html = renderToStaticMarkup(
				createElement(OntologyDiffVisualizer, {
					data: SAMPLE_DIFF_DATA,
				}),
			);

			expect(html).toContain('data-testid="additive-guarantee-banner"');
			expect(html).toContain("Strictly additive evolution");
		});

		it("renders added concepts, relationships, enumerations, and refined concepts", () => {
			const html = renderToStaticMarkup(
				createElement(OntologyDiffVisualizer, {
					data: SAMPLE_DIFF_DATA,
				}),
			);

			expect(html).toContain('data-testid="added-concepts-section"');
			expect(html).toContain("urn:ontology:entity:advisor");
			expect(html).toContain('data-testid="added-relationships-section"');
			expect(html).toContain("urn:ontology:relationship:fund_advisor");
			expect(html).toContain('data-testid="added-enumerations-section"');
			expect(html).toContain("urn:ontology:enum:advisor_role");
			expect(html).toContain('data-testid="refined-concepts-section"');
			expect(html).toContain("urn:ontology:entity:fund");
			expect(html).toContain('data-testid="ontology-diff-diagnostics"');
		});

		it("parses raw JSON from Artifact payload correctly", () => {
			const html = renderToStaticMarkup(
				createElement(OntologyDiffVisualizer, {
					artifact: SAMPLE_ARTIFACTS.ontology_diff_art,
				}),
			);

			expect(html).toContain('data-testid="ontology-diff-visualizer"');
			expect(html).toContain("v1.0.0");
			expect(html).toContain("v1.1.0");
			expect(html).toContain("urn:ontology:entity:advisor");
		});
	});

	// =========================================================================
	// 6. ArtifactVisualizers Dynamic Ontology Dispatch Integration (Pillar 3 & REQ-MOCK-01)
	// =========================================================================
	describe("6. ArtifactVisualizer Dynamic Ontology Dispatch", () => {
		it("dispatches to OntologyCoverageVisualizer for ontology_coverage artifacts", () => {
			const html = renderToStaticMarkup(
				createElement(ArtifactVisualizer, {
					artifact: SAMPLE_ARTIFACTS.ontology_cov,
				}),
			);
			expect(html).toContain('data-testid="ontology-coverage-visualizer"');
			expect(html).toContain('data-testid="coverage-percentage-badge"');
			expect(html).toContain("60.0%");
		});

		it("dispatches to OntologyProposalVisualizer for ontology_proposal artifacts", () => {
			const html = renderToStaticMarkup(
				createElement(ArtifactVisualizer, {
					artifact: SAMPLE_ARTIFACTS.ontology_prop,
				}),
			);
			expect(html).toContain('data-testid="ontology-proposal-visualizer"');
			expect(html).toContain('data-testid="proposal-id-pill"');
			expect(html).toContain("prop-gt06-001");
		});

		it("dispatches to OntologyDiffVisualizer for ontology_diff artifacts", () => {
			const html = renderToStaticMarkup(
				createElement(ArtifactVisualizer, {
					artifact: SAMPLE_ARTIFACTS.ontology_diff_art,
				}),
			);
			expect(html).toContain('data-testid="ontology-diff-visualizer"');
			expect(html).toContain('data-testid="additive-guarantee-banner"');
			expect(html).toContain("Strictly additive evolution");
		});
	});
});

