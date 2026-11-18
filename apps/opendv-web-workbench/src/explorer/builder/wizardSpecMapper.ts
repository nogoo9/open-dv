/**
 * Bi-Directional Specification Mapper: WizardFormState <-> PipelineInputDTO (ADR-017, ADR-019).
 *
 * Provides lossless, deterministic transformation between the interactive 4-step wizard
 * state and the normative single-run PipelineInputDTO wire format, alongside YAML
 * serialization and parsing.
 */

import type {
	PipelineInputDTO,
	SamplingPolicyDTO,
	SamplingStrategy,
	SourceFeedDTO,
	VaultBlueprintDTO,
} from "@opendv/protocol-ts";
import { parse as parseYaml, stringify as stringifyYaml } from "yaml";
import {
	type WizardFormState,
	createDefaultWizardFormState,
} from "./wizard-types";

/**
 * Transforms an active WizardFormState into a normative PipelineInputDTO.
 */
export function formStateToPipelineInputDto(
	form: WizardFormState,
): PipelineInputDTO {
	const dto: PipelineInputDTO = {};

	// 1. Foundation mapping
	if (form.foundationMode === "vault_blueprint") {
		if (form.blueprint) {
			dto.blueprint = form.blueprint;
		} else if (form.blueprintContent.trim()) {
			try {
				const parsed = parseYaml(form.blueprintContent);
				if (parsed && typeof parsed === "object") {
					dto.blueprint = parsed as VaultBlueprintDTO;
				} else {
					dto.blueprint_content = form.blueprintContent;
				}
			} catch {
				dto.blueprint_content = form.blueprintContent;
			}
		}
		if (!dto.blueprint && !dto.blueprint_content && form.blueprintPath.trim() && form.blueprintPath.trim() !== "vault_blueprint.yaml") {
			dto.blueprint_path = form.blueprintPath.trim();
		}

		if (form.contractContent.trim()) {
			dto.initial_contract_content = form.contractContent.trim();
		} else if (form.contract) {
			dto.initial_contract_content = serializeSpecToYaml(form.contract);
		} else if (dto.blueprint && (dto.blueprint as any).contract) {
			dto.initial_contract_content = serializeSpecToYaml((dto.blueprint as any).contract);
		}

		if (!dto.initial_contract_content && form.contractPath.trim() && form.contractPath.trim() !== "dv_contract.yaml") {
			dto.initial_contract_path = form.contractPath.trim();
		}
	} else if (form.foundationMode === "raw_ontology") {
		if (form.ontologySource) {
			dto.ontology_source = form.ontologySource;
		} else if (form.ontologyContent.trim()) {
			try {
				const parsed = parseYaml(form.ontologyContent);
				if (parsed && typeof parsed === "object") {
					dto.ontology_source = parsed as Record<string, unknown>;
				}
			} catch {
				// Omit undeclared ontology_content
			}
		}

		if (form.contractContent.trim()) {
			dto.initial_contract_content = form.contractContent.trim();
		} else if (form.contract) {
			dto.initial_contract_content = serializeSpecToYaml(form.contract);
		} else if (dto.blueprint && (dto.blueprint as any).contract) {
			dto.initial_contract_content = serializeSpecToYaml((dto.blueprint as any).contract);
		} else if (form.contractPath.trim() && form.contractPath.trim() !== "dv_contract.yaml") {
			dto.initial_contract_path = form.contractPath.trim();
		}
	} else if (form.foundationMode === "studio_session") {
		if (form.blueprint) {
			dto.blueprint = form.blueprint;
		} else if (form.activeSessionId) {
			if (form.blueprintPath.trim() && form.blueprintPath.trim() !== "vault_blueprint.yaml") {
				dto.blueprint_path = form.blueprintPath.trim();
			}
		}
		if (form.ontologySource) {
			dto.ontology_source = form.ontologySource;
		}
		if (form.contract) {
			dto.initial_contract_content = serializeSpecToYaml(form.contract);
		}
	}

	// 2. Data Sources & Unstructured Documents
	if (form.configuredSources && form.configuredSources.length > 0) {
		dto.sources = form.configuredSources.map((feed) => ({ ...feed }));
	} else {
		dto.sources = [];
	}

	if (form.unstructuredDocs && form.unstructuredDocs.length > 0) {
		dto.unstructured_documents = form.unstructuredDocs.map((doc) => {
			const document_path = (doc.document_path || doc.file_path || doc.title || "") as string;
			const docType = String(doc.doc_type || "");
			const mime_type = (doc.mime_type ||
				(docType === "pdf"
					? "application/pdf"
					: docType === "markdown"
						? "text/markdown"
						: "text/plain")) as string;
			const rawContent = doc.content ? String(doc.content).trim() : "";
			const content =
				rawContent ||
				(docType === "inline_note" ? document_path.trim() || undefined : undefined);
			return {
				doc_id: doc.doc_id ? String(doc.doc_id) : `doc_${Date.now()}`,
				document_path,
				mime_type,
				...(content ? { content } : {}),
				...(doc.target_hub ? { target_hub: String(doc.target_hub) } : {}),
				...(doc.metadata ? { metadata: doc.metadata as Record<string, unknown> } : {}),
			};
		});
	}

	// 3. Dialect & Harness
	dto.target_dialect = form.targetDialects.includes("databricks")
		? "databricks"
		: "duckdb";
	dto.harness_provider = form.harnessProvider || "agy";

	// 4. Governance Policy
	const samplingPolicy: SamplingPolicyDTO = {
		max_sandbox_rows: form.samplingRows,
		strategy: form.samplingStrategy,
	};

	dto.governance_policy = {
		steward_hold_policy: form.stewardHoldPolicy,
		ontology_policy: form.ontologyEvolutionPolicy,
		agent_model: form.agentModel,
		sampling_policy: samplingPolicy,
		...(form.advancedOverrides ?? {}),
	};

	return dto;
}

/**
 * Hydrates or updates a WizardFormState from a PipelineInputDTO.
 */
export function pipelineInputDtoToFormState(
	dto: PipelineInputDTO,
	base?: WizardFormState,
): WizardFormState {
	const state: WizardFormState = createDefaultWizardFormState(base);

	// 1. Foundation inference
	if (dto.blueprint || dto.blueprint_path || dto.blueprint_content) {
		state.foundationMode = "vault_blueprint";
		if (dto.blueprint && typeof dto.blueprint === "object") {
			state.blueprint = dto.blueprint as VaultBlueprintDTO;
			state.blueprintContent = serializeSpecToYaml(dto.blueprint);
			const bp = dto.blueprint as Record<string, unknown>;
			if (typeof bp.domain_namespace === "string") {
				state.domainNamespace = bp.domain_namespace;
			}
		}
		if (dto.blueprint_path) {
			state.blueprintPath = dto.blueprint_path;
		}
		if (dto.blueprint_content) {
			state.blueprintContent = dto.blueprint_content;
		}
	} else if (
		dto.ontology_source ||
		(dto as any).ontology_path ||
		(dto as any).ontology_content ||
		dto.initial_contract_path ||
		dto.initial_contract_content
	) {
		state.foundationMode = "raw_ontology";
		if ((dto as any).ontology_path) {
			state.ontologyPath = String((dto as any).ontology_path);
		}
		if ((dto as any).ontology_content) {
			state.ontologyContent = String((dto as any).ontology_content);
		}
		if (dto.ontology_source && typeof dto.ontology_source === "object") {
			state.ontologySource = dto.ontology_source;
			state.ontologyContent = serializeSpecToYaml(dto.ontology_source);
		}
		if (dto.initial_contract_path) {
			state.contractPath = dto.initial_contract_path;
		}
		if (dto.initial_contract_content) {
			state.contractContent = dto.initial_contract_content;
			try {
				const parsed = parseYaml(dto.initial_contract_content);
				if (parsed && typeof parsed === "object") {
					state.contract = parsed as Record<string, unknown>;
				}
			} catch {
				// Keep content as raw string
			}
		}
	}

	// 2. Data Sources & Unstructured Docs
	if (Array.isArray(dto.sources)) {
		state.configuredSources = dto.sources.map((feed) => ({ ...feed }));
	}

	if (Array.isArray(dto.unstructured_documents)) {
		state.unstructuredDocs = dto.unstructured_documents.map((doc: any, idx: number) => {
			const baseDoc = base?.unstructuredDocs?.[idx];
			if (baseDoc && (baseDoc.doc_id === doc.doc_id || baseDoc.title === doc.document_path)) {
				return { ...baseDoc };
			}
			return {
				doc_id: doc.doc_id ?? `doc_${Date.now()}`,
				document_path: doc.document_path ?? doc.file_path ?? doc.title ?? "",
				mime_type: doc.mime_type ?? "text/plain",
				...(doc.content !== undefined ? { content: doc.content } : {}),
				title: doc.title ?? doc.document_path ?? doc.file_path ?? "Document",
				doc_type:
					doc.doc_type ??
					(doc.mime_type?.includes("pdf")
						? "pdf"
						: doc.mime_type?.includes("markdown")
							? "markdown"
							: "text"),
				...(doc.metadata ? { metadata: doc.metadata } : {}),
			};
		});
	}

	// 3. Dialect & Harness
	if (dto.target_dialect) {
		state.targetDialects =
			dto.target_dialect.toLowerCase() === "databricks"
				? ["duckdb", "databricks"]
				: ["duckdb"];
	}
	if (dto.harness_provider) {
		state.harnessProvider = dto.harness_provider;
	}

	// 4. Governance Policy
	if (dto.governance_policy && typeof dto.governance_policy === "object") {
		const pol = dto.governance_policy as Record<string, unknown>;
		if (
			pol.steward_hold_policy === "pause" ||
			pol.steward_hold_policy === "auto_approve_top_candidate" ||
			pol.steward_hold_policy === "reject_on_hold"
		) {
			state.stewardHoldPolicy = pol.steward_hold_policy;
		}
		if (
			pol.ontology_policy === "recommend" ||
			pol.ontology_policy === "auto_accept" ||
			pol.ontology_policy === "strict_lock"
		) {
			state.ontologyEvolutionPolicy = pol.ontology_policy;
		}
		if (typeof pol.agent_model === "string" && pol.agent_model.trim()) {
			state.agentModel = pol.agent_model.trim();
		}
		if (pol.sampling_policy && typeof pol.sampling_policy === "object") {
			const sp = pol.sampling_policy as Record<string, unknown>;
			if (typeof sp.max_sandbox_rows === "number") {
				state.samplingRows = sp.max_sandbox_rows;
			}
			if (
				sp.strategy === "DETERMINISTIC_LIMIT" ||
				sp.strategy === "TABLESAMPLE_BERNOULLI"
			) {
				state.samplingStrategy = sp.strategy as SamplingStrategy;
			}
		}
	}

	return state;
}

/**
 * Deterministically serializes a PipelineInputDTO or arbitrary spec object to clean YAML.
 */
export function serializeSpecToYaml(spec: unknown): string {
	if (!spec) return "";
	return stringifyYaml(spec, {
		indent: 2,
		lineWidth: 0,
		singleQuote: false,
		defaultStringType: "PLAIN",
	}).trimEnd();
}

/**
 * Parses a YAML text string into a typed PipelineInputDTO.
 * Throws a descriptive Error if the string contains invalid syntax or is not a mapping.
 */
export function parseYamlToPipelineInputDto(
	yamlText: string,
): PipelineInputDTO {
	const trimmed = yamlText.trim();
	if (!trimmed) {
		return {};
	}

	let parsed: unknown;
	try {
		parsed = parseYaml(trimmed);
	} catch (err) {
		const message =
			err instanceof Error ? err.message : "YAML syntax parse error";
		throw new Error(`Invalid YAML spec format: ${message}`);
	}

	if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
		throw new Error(
			"Invalid PipelineInputDTO spec: expected top-level YAML mapping object.",
		);
	}

	return parsed as PipelineInputDTO;
}
