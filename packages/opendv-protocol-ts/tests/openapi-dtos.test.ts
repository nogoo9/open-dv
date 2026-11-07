import { describe, expect, it } from "bun:test";
import { readFileSync } from "node:fs";
import * as path from "node:path";
import type {
	AcceptOntologyDeltaOption,
	AddConceptOp,
	ArtifactDTO,
	ArtifactMetadataResponse,
	ArtifactUploadRequest,
	CaptainHoldDecisionRequest,
	CaptainHoldDecisionResponse,
	CompileRequest,
	CompileResponse,
	CreateProjectRequest,
	CreateRunRequest,
	CustomOntologyDeltaOption,
	DocCandidateOptionDTO,
	DocExtractionSpecDTO,
	DocVizDTO,
	DvSvgEdgeSpecDTO,
	DvSvgNodeSpecDTO,
	DvVizDTO,
	EvidenceRef,
	GeneralHoldOption,
	OntologyDecision,
	OntologyDecisionRequest,
	OntologyDecisionResponse,
	OntologyDelta,
	OntologyDiff,
	OntologyDiffRequest,
	OntologyProposal,
	OntologySuggestionRequest,
	OntologySvgEdgeSpecDTO,
	OntologySvgNodeSpecDTO,
	OntologyValidateDeltaRequest,
	OntologyValidationResult,
	OntologyVizDTO,
	PhaseSpecDTO,
	PipelineRunHandle,
	PipelineRunRequest,
	PipelineValidateRequest,
	ProfileRequest,
	ProfileResponse,
	ProjectResponse,
	ProjectRunDTO,
	RefineDefinitionOp,
	RejectOntologyDeltaOption,
	RuleEvaluationDTO,
	RunArtifactDTO,
	SandboxBatchResult,
	SandboxColumnMetadata,
	SandboxExecutionResponse,
	SandboxQueryRequest,
	SandboxQueryResponse,
	StageDTO,
	StepDTO,
	VerifyRequest,
	VerifyResponse,
	WaveMetadataSpecDTO,
} from "../src/types/api-dtos";

describe("OpenAPI 3.1 & TypeScript SDK Synchronization (Task P10.1 / Issue #121)", () => {
	const schemaPath = path.resolve(
		__dirname,
		"../../../schemas/openapi-v1.json",
	);
	const schemaJson = JSON.parse(readFileSync(schemaPath, "utf-8"));

	it("conforms to OpenAPI 3.1.0 and JSON Schema Draft 2020-12 dialect metadata", () => {
		expect(schemaJson.openapi).toBe("3.1.0");
		expect(schemaJson.$schema).toBe(
			"https://json-schema.org/draft/2020-12/schema",
		);
		expect(schemaJson.jsonSchemaDialect).toBe(
			"https://json-schema.org/draft/2020-12/schema",
		);
		expect(schemaJson.info.title).toBe("Open-DV Platform API");
	});

	it("enforces zero legacy /workbench routes in OpenAPI specification", () => {
		const paths = Object.keys(schemaJson.paths || {});
		const legacyRoutes = paths.filter((p) => p.includes("workbench"));
		expect(legacyRoutes).toEqual([]);
		expect(paths.length).toBeGreaterThanOrEqual(25);
	});

	it("validates typed CaptainHoldDecisionRequest with ontology hold_option", () => {
		const acceptOption: AcceptOntologyDeltaOption = {
			kind: "accept_ontology_delta",
			delta: {
				delta_id: "delta-w0-01",
				base_ontology_version: "1.0.0",
				target_ontology_version: "1.1.0",
				operations: [],
			},
			delta_sha256: "0".repeat(64),
			steward_note: "Accepted ontology proposal delta",
		};

		const requestWithHoldOption: CaptainHoldDecisionRequest = {
			action: "APPROVE",
			decision: "APPROVE",
			decision_type: "RESOLVE_CAPTAIN_HOLD",
			event_id: "evt-hold-01",
			hold_id: "hold-01",
			resolution_notes: "Accepted ontology proposal delta",
			selected_candidate_id: "cand-01",
			steward_id: "steward-01",
			steward_rationale: "Approved under governance review",
			hold_option: acceptOption,
		};

		expect(requestWithHoldOption.action).toBe("APPROVE");
		expect(requestWithHoldOption.hold_option?.kind).toBe("accept_ontology_delta");
	});

	it("validates typed CaptainHoldDecisionResponse structure", () => {
		const response: CaptainHoldDecisionResponse = {
			action: "APPROVE",
			decision_id: "dec-01",
			hold_id: "hold-01",
			next_event_id: "evt-resumed-01",
			next_stage_id: "STAGE_02_SHIP_VAULT_PASS1",
			resolved_at_utc: "2026-11-07T09:00:00Z",
			resumed_run_id: "run-01",
			status: "ACCEPTED",
			updated_run_status: "RUNNING",
		};

		expect(response.status).toBe("ACCEPTED");
		expect(response.decision_id).toBe("dec-01");
	});

	it("validates typed pipeline and project creation DTOs", () => {
		const createProj: CreateProjectRequest = {
			project_id: "proj-ecommerce-01",
			display_name: "E-Commerce Pipeline",
			domain_namespace: "ecommerce",
		};
		expect(createProj.project_id).toBe("proj-ecommerce-01");

		const createRun: CreateRunRequest = {
			project_id: "proj-ecommerce-01",
			harness_provider: "antigravity",
			initial_contract: "contract_v5: true\n",
			wave_id: "W0",
			assertion_cutoff_ts: "2026-11-07T09:00:00Z",
			execute_duckdb_sandbox: true,
			max_repair_retries: 3,
			run_sandbox_batches: true,
		};
		expect(createRun.wave_id).toBe("W0");

		const runHandle: PipelineRunHandle = {
			run_id: "run-01",
			status: "RUNNING",
			created_at_utc: "2026-11-07T09:00:00Z",
			event_stream_url: "/api/v1/runs/run-01/events",
			events_sse_url: "/api/v1/runs/run-01/events",
			status_url: "/api/v1/runs/run-01",
			has_initial_contract: true,
			lifecycle_stage: "STAGE_01_RAW_SCHEMA_DISCOVERY",
		};
		expect(runHandle.status).toBe("RUNNING");
	});

	it("validates typed ontology recommendation and delta DTOs", () => {
		const addConcept: AddConceptOp = {
			op_id: "op-01",
			op_type: "add_concept",
			concept_name: "customer_loyalty",
			concept_uri: "urn:opendv:concept:customer_loyalty",
			confidence: 0.95,
			definition: "Customer loyalty program member metrics",
			description: "Customer loyalty program member metrics",
			elm_archetype: "CONCEPT",
			rationale: "Discovered from bronze sources",
		};
		expect(addConcept.concept_name).toBe("customer_loyalty");

		const delta: OntologyDelta = {
			delta_id: "delta-01",
			base_ontology_version: "1.0.0",
			target_ontology_version: "1.1.0",
			operations: [addConcept],
			parent_sha256: "a".repeat(64),
		};
		expect(delta.operations?.length).toBe(1);

		const diffReq: OntologyDiffRequest = {
			base_ontology_content: "version: 1.0.0\n",
			target_ontology_content: "version: 1.1.0\n",
		};
		expect(diffReq.base_ontology_content).toBe("version: 1.0.0\n");
	});

	it("validates typed Explorer bundle component DTOs", () => {
		const step: StepDTO = {
			name: "Step 1: Ingest Bronze Sources",
			reads: ["bronze.customer", "bronze.orders"],
			writes: ["raw_vault.hub_customer"],
			desc: "Extracts business keys and loads Hub",
		};
		expect(step.reads?.length).toBe(2);

		const phase: PhaseSpecDTO = {
			index: 0,
			label: "Discovery",
			sub: "Ingest and profile bronze datasets",
		};
		expect(phase.index).toBe(0);

		const wave: WaveMetadataSpecDTO = {
			id: "W0",
			name: "Core Foundation Wave",
			description: "Foundational Hubs and Links",
		};
		expect(wave.id).toBe("W0");
	});
});
