/**
 * Unit Tests for wizardSpecMapper.ts (WS5.1).
 *
 * Verifies bi-directional lossless transformation between WizardFormState and
 * PipelineInputDTO, deterministic YAML serialization, and syntax parsing validation.
 */

import { describe, expect, it } from "bun:test";
import type {
	PipelineInputDTO,
	SourceFeedDTO,
	VaultBlueprintDTO,
} from "@opendv/protocol-ts";
import {
	type WizardFormState,
	createDefaultWizardFormState,
} from "../src/explorer/builder/wizard-types";
import {
	formStateToPipelineInputDto,
	parseYamlToPipelineInputDto,
	pipelineInputDtoToFormState,
	serializeSpecToYaml,
} from "../src/explorer/builder/wizardSpecMapper";

describe("wizardSpecMapper (WS5.1)", () => {
	describe("1. formStateToPipelineInputDto transformation", () => {
		it("transforms greenfield studio_session form state into PipelineInputDTO", () => {
			const form = createDefaultWizardFormState({
				foundationMode: "studio_session",
				activeSessionId: "session-am-01",
				blueprintPath: "data/blueprints/am_session_blueprint.yaml",
				agentModel: "gemini-4-argon",
				targetDialects: ["duckdb"],
				samplingRows: 5000,
				samplingStrategy: "DETERMINISTIC_LIMIT",
				stewardHoldPolicy: "pause",
				ontologyEvolutionPolicy: "recommend",
				harnessProvider: "agy",
				configuredSources: [
					{
						feed_id: "sec_feed",
						source_system: "sec_edgar",
						uri: "data/public_am/am_sec_treasury_ibor.sqlite",
						adapter: "sqlite",
						table_selection: {
							mode: "EXPLICIT",
							tables: ["securities", "issuers"],
						},
					},
				],
			});

			const dto = formStateToPipelineInputDto(form);

			expect(dto.blueprint_path).toBe(
				"data/blueprints/am_session_blueprint.yaml",
			);
			expect(dto.target_dialect).toBe("duckdb");
			expect(dto.harness_provider).toBe("agy");
			expect(dto.sources).toHaveLength(1);
			expect(dto.sources?.[0]?.feed_id).toBe("sec_feed");
			expect(dto.sources?.[0]?.table_selection?.tables).toEqual([
				"securities",
				"issuers",
			]);

			const gov = dto.governance_policy as Record<string, unknown>;
			expect(gov.agent_model).toBe("gemini-4-argon");
			expect(gov.steward_hold_policy).toBe("pause");
			expect(gov.ontology_policy).toBe("recommend");
			const samp = gov.sampling_policy as Record<string, unknown>;
			expect(samp.max_sandbox_rows).toBe(5000);
			expect(samp.strategy).toBe("DETERMINISTIC_LIMIT");
		});

		it("transforms vault_blueprint mode with parsed VaultBlueprintDTO", () => {
			const blueprint: VaultBlueprintDTO = {
				schema_version: "1.0.0",
				blueprint_version: "1.0.0",
				domain_namespace: "asset_management",
				lineage: {
					domain_namespace: "asset_management",
					ontology_sha256: "abc123sha",
					contract_sha256: "def456sha",
				},
				ontology: {
					canonical_business_concepts: [{ concept_name: "Security" }],
				},
				contract: {
					hubs: [{ table_name: "hub_security" }],
				},
			};

			const form = createDefaultWizardFormState({
				foundationMode: "vault_blueprint",
				blueprint,
				targetDialects: ["duckdb", "databricks"],
			});

			const dto = formStateToPipelineInputDto(form);
			expect(dto.blueprint).toEqual(blueprint);
			expect(dto.target_dialect).toBe("databricks");
		});

		it("transforms raw_ontology mode with contract path and content (omits path when content is present)", () => {
			const form = createDefaultWizardFormState({
				foundationMode: "raw_ontology",
				ontologyPath: "schemas/ontology.yaml",
				contractPath: "schemas/dv_contract.yaml",
				contractContent: "hubs:\n  - table_name: hub_instrument",
			});

			const dto = formStateToPipelineInputDto(form);
			expect(dto.initial_contract_path).toBeUndefined();
			expect(dto.initial_contract_content).toBe(
				"hubs:\n  - table_name: hub_instrument",
			);
		});

		it("emits initial_contract_path in raw_ontology mode when contract content is absent", () => {
			const form = createDefaultWizardFormState({
				foundationMode: "raw_ontology",
				ontologyPath: "schemas/ontology.yaml",
				contractPath: "schemas/dv_contract.yaml",
				contractContent: "",
			});

			const dto = formStateToPipelineInputDto(form);
			expect(dto.initial_contract_path).toBe("schemas/dv_contract.yaml");
			expect(dto.initial_contract_content).toBeUndefined();
		});

		it("populates content from title/document_path for inline_note documents when content is omitted", () => {
			const form = createDefaultWizardFormState({
				unstructuredDocs: [
					{
						doc_id: "doc_note_1",
						title: "Custody FX settlement SLA addendum",
						document_path: "Custody FX settlement SLA addendum",
						doc_type: "inline_note",
						mime_type: "text/plain",
					},
				],
			});

			const dto = formStateToPipelineInputDto(form);
			expect(dto.unstructured_documents).toHaveLength(1);
			expect(dto.unstructured_documents?.[0]?.content).toBe(
				"Custody FX settlement SLA addendum",
			);
		});
	});

	describe("2. pipelineInputDtoToFormState hydration", () => {
		it("hydrates form state from a full PipelineInputDTO", () => {
			const sourceFeed: SourceFeedDTO = {
				feed_id: "feed_uc",
				source_system: "databricks_prod",
				uri: "databricks://warehouse/catalog/schema/positions",
				adapter: "databricks",
				credentials_ref: "env:DATABRICKS_TOKEN",
			};

			const dto: PipelineInputDTO = {
				blueprint_path: "artifacts/vault_blueprint.yaml",
				sources: [sourceFeed],
				unstructured_documents: [{ doc_id: "doc_fund_prospectus.pdf" }],
				target_dialect: "databricks",
				harness_provider: "claude-code",
				governance_policy: {
					agent_model: "claude-opus-5-5-high",
					steward_hold_policy: "auto_approve_top_candidate",
					ontology_policy: "auto_accept",
					sampling_policy: {
						max_sandbox_rows: 25000,
						strategy: "TABLESAMPLE_BERNOULLI",
					},
				},
			};

			const form = pipelineInputDtoToFormState(dto);

			expect(form.foundationMode).toBe("vault_blueprint");
			expect(form.blueprintPath).toBe("artifacts/vault_blueprint.yaml");
			expect(form.configuredSources).toHaveLength(1);
			expect(form.configuredSources[0]?.feed_id).toBe("feed_uc");
			expect(form.unstructuredDocs).toHaveLength(1);
			expect(form.targetDialects).toEqual(["duckdb", "databricks"]);
			expect(form.harnessProvider).toBe("claude-code");
			expect(form.agentModel).toBe("claude-opus-5-5-high");
			expect(form.stewardHoldPolicy).toBe("auto_approve_top_candidate");
			expect(form.ontologyEvolutionPolicy).toBe("auto_accept");
			expect(form.samplingRows).toBe(25000);
			expect(form.samplingStrategy).toBe("TABLESAMPLE_BERNOULLI");
		});
	});

	describe("3. Roundtrip Lossless Conversion", () => {
		it("preserves configuration across roundtrip form -> DTO -> form", () => {
			const initialForm = createDefaultWizardFormState({
				foundationMode: "vault_blueprint",
				blueprintPath: "vaults/am_v1.yaml",
				agentModel: "gemini-4-argon",
				targetDialects: ["duckdb", "databricks"],
				samplingRows: 15000,
				samplingStrategy: "TABLESAMPLE_BERNOULLI",
				stewardHoldPolicy: "reject_on_hold",
				ontologyEvolutionPolicy: "strict_lock",
				harnessProvider: "agy",
				configuredSources: [
					{
						feed_id: "sqlite_1",
						source_system: "sec",
						uri: "data/sec.sqlite",
						adapter: "sqlite",
					},
				],
				unstructuredDocs: [{ doc_id: "notes.txt" }],
			});

			const dto = formStateToPipelineInputDto(initialForm);
			const roundtripForm = pipelineInputDtoToFormState(dto, initialForm);

			expect(roundtripForm.foundationMode).toBe(initialForm.foundationMode);
			expect(roundtripForm.blueprintPath).toBe(initialForm.blueprintPath);
			expect(roundtripForm.agentModel).toBe(initialForm.agentModel);
			expect(roundtripForm.targetDialects).toEqual(initialForm.targetDialects);
			expect(roundtripForm.samplingRows).toBe(initialForm.samplingRows);
			expect(roundtripForm.samplingStrategy).toBe(initialForm.samplingStrategy);
			expect(roundtripForm.stewardHoldPolicy).toBe(
				initialForm.stewardHoldPolicy,
			);
			expect(roundtripForm.ontologyEvolutionPolicy).toBe(
				initialForm.ontologyEvolutionPolicy,
			);
			expect(roundtripForm.configuredSources).toEqual(
				initialForm.configuredSources,
			);
			expect(roundtripForm.unstructuredDocs).toEqual(
				initialForm.unstructuredDocs,
			);
		});
	});

	describe("4. YAML Serialization & Parsing", () => {
		it("serializes spec to valid, deterministic YAML", () => {
			const dto: PipelineInputDTO = {
				blueprint_path: "path/to/blueprint.yaml",
				target_dialect: "duckdb",
				sources: [
					{
						feed_id: "feed1",
						source_system: "sys",
						uri: "sqlite://test.db",
					},
				],
			};

			const yamlText = serializeSpecToYaml(dto);
			expect(yamlText).toContain("blueprint_path: path/to/blueprint.yaml");
			expect(yamlText).toContain("target_dialect: duckdb");
			expect(yamlText).toContain("feed_id: feed1");

			const parsed = parseYamlToPipelineInputDto(yamlText);
			expect(parsed.blueprint_path).toBe("path/to/blueprint.yaml");
			expect(parsed.sources?.[0]?.feed_id).toBe("feed1");
		});

		it("throws descriptive error when parsing invalid YAML syntax", () => {
			const malformedYaml = "key: [unclosed array\n  another: val";
			expect(() => parseYamlToPipelineInputDto(malformedYaml)).toThrow(
				/Invalid YAML spec format:/,
			);
		});

		it("throws descriptive error when YAML is a primitive or array instead of mapping", () => {
			expect(() => parseYamlToPipelineInputDto("just a plain string")).toThrow(
				/Invalid PipelineInputDTO spec: expected top-level YAML mapping object/,
			);
			expect(() => parseYamlToPipelineInputDto("- item 1\n- item 2")).toThrow(
				/Invalid PipelineInputDTO spec: expected top-level YAML mapping object/,
			);
		});

		it("returns empty object for empty or whitespace-only YAML string", () => {
			expect(parseYamlToPipelineInputDto("")).toEqual({});
			expect(parseYamlToPipelineInputDto("   \n\t  ")).toEqual({});
		});
	});
});
