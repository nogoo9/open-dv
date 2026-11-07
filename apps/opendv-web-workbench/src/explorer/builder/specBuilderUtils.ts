/**
 * Pipeline Specification Builder Utilities (`specBuilderUtils.ts`)
 *
 * Interactive draft models, default starter templates, and serialization
 * utilities for `PipelineInputSpec` (`packages/opendv-agent-harness/src/opendv_agent_harness/pipeline.py`).
 */

import type { StewardHoldPolicyOption } from "../modals/NewRunFormFields";

export type WaveTypeOption =
	| "COLD_START"
	| "INCREMENTAL_SOURCE"
	| "SCHEMA_DRIFT"
	| "UNSTRUCTURED_ONBOARDING";

export type OntologyPolicyOption = "off" | "recommend" | "auto_accept";

export interface InteractiveWaveSourceDraft {
	_key?: string | undefined;
	sourceSystem: string;
	tableName: string;
	dataPath: string;
	targetHubName: string;
	businessKeyColumns: string;
}

export interface InteractiveWaveDocDraft {
	_key?: string | undefined;
	docId: string;
	documentPath: string;
	targetHubName: string;
}

let draftItemCounter = 0;
function nextDraftKey(prefix: string): string {
	draftItemCounter += 1;
	return `${prefix}-${draftItemCounter}`;
}

export interface InteractiveWaveDraft {
	waveIndex: number;
	waveName: string;
	waveType: WaveTypeOption;
	description: string;
	sources: InteractiveWaveSourceDraft[];
	unstructuredDocuments: InteractiveWaveDocDraft[];
}

export interface InteractivePipelineDraft {
	pipelineId: string;
	projectId: string;
	displayName: string;
	domainNamespace: string;
	graphAOntologyPath: string;
	initialContractPath: string;
	waveId: string;
	stewardHoldPolicy: StewardHoldPolicyOption;
	ontologyPolicy: OntologyPolicyOption;
	waves: InteractiveWaveDraft[];
}

export interface StagedPipelineSpec {
	mode: "interactive" | "yaml_upload";
	inputSpec?: Record<string, unknown> | undefined;
	inputSpecContent?: string | undefined;
	inputSpecPath?: string | undefined;
	fileName?: string | undefined;
	initialContractPath?: string | undefined;
	initialContractContent?: string | undefined;
	waveId?: string | undefined;
	stewardHoldPolicy: "pause" | "auto_approve_top_candidate" | "reject_on_hold";
	ontologyPolicy?: string | undefined;
	validated?: boolean | undefined;
}

export function createEmptySourceDraft(): InteractiveWaveSourceDraft {
	return {
		_key: nextDraftKey("src"),
		sourceSystem: "ERP_CORE",
		tableName: "bronze_orders",
		dataPath:
			"examples/pipeline_samples/global_commerce_vault/data/w1_erp_orders.csv",
		targetHubName: "hub_order",
		businessKeyColumns: "order_id",
	};
}

export function createEmptyDocDraft(): InteractiveWaveDocDraft {
	return {
		_key: nextDraftKey("doc"),
		docId: "doc_msa_001",
		documentPath:
			"examples/pipeline_samples/global_commerce_vault/docs/master_service_agreement.md",
		targetHubName: "hub_customer",
	};
}

export function createEmptyWaveDraft(waveIndex: number): InteractiveWaveDraft {
	return {
		waveIndex,
		waveName: `W${waveIndex}_Incremental`,
		waveType: waveIndex === 0 ? "COLD_START" : "INCREMENTAL_SOURCE",
		description: `Wave ${waveIndex} onboarding`,
		sources: [createEmptySourceDraft()],
		unstructuredDocuments: [],
	};
}

export function createDefaultPipelineDraft(): InteractivePipelineDraft {
	return {
		pipelineId: "global_commerce_vault_run",
		projectId: "global_commerce_vault",
		displayName: "Global Commerce Data Vault 2.0",
		domainNamespace: "commerce",
		graphAOntologyPath:
			"examples/pipeline_samples/global_commerce_vault/ontology/graph_a_commerce.yaml",
		initialContractPath: "",
		waveId: "",
		stewardHoldPolicy: "pause",
		ontologyPolicy: "off",
		waves: [
			{
				waveIndex: 0,
				waveName: "W0_Cold_Start",
				waveType: "COLD_START",
				description: "Initial relational onboarding wave",
				sources: [
					{
						_key: nextDraftKey("src"),
						sourceSystem: "CRM_CORE",
						tableName: "bronze_customers",
						dataPath:
							"examples/pipeline_samples/global_commerce_vault/data/w0_crm_customers.csv",
						targetHubName: "hub_customer",
						businessKeyColumns: "customer_id",
					},
				],
				unstructuredDocuments: [],
			},
		],
	};
}

export function draftToInputSpecObject(
	draft: InteractivePipelineDraft,
): Record<string, unknown> {
	const spec: Record<string, unknown> = {
		pipeline_id: draft.pipelineId.trim() || "custom_pipeline",
		project_id: draft.projectId.trim() || "custom_project",
		display_name: draft.displayName.trim() || "Open-DV Agentic Pipeline Run",
		domain_namespace: draft.domainNamespace.trim() || "general",
		graph_a_ontology_path: draft.graphAOntologyPath.trim(),
		ontology_policy: draft.ontologyPolicy,
		waves: draft.waves.map((wave, idx) => ({
			wave_index: wave.waveIndex ?? idx,
			wave_name: wave.waveName.trim() || `W${idx}`,
			wave_type: wave.waveType,
			description: wave.description.trim(),
			sources: wave.sources.map((src) => {
				const bks = src.businessKeyColumns
					.split(",")
					.map((col) => col.trim())
					.filter(Boolean);
				return {
					source_system: src.sourceSystem.trim(),
					table_name: src.tableName.trim(),
					data_path: src.dataPath.trim(),
					...(src.targetHubName.trim()
						? { target_hub_name: src.targetHubName.trim() }
						: {}),
					business_key_columns: bks,
				};
			}),
			unstructured_documents: wave.unstructuredDocuments.map((doc) => ({
				doc_id: doc.docId.trim(),
				document_path: doc.documentPath.trim(),
				...(doc.targetHubName.trim()
					? { target_hub_name: doc.targetHubName.trim() }
					: {}),
			})),
		})),
	};

	if (draft.initialContractPath.trim()) {
		spec.initial_contract_path = draft.initialContractPath.trim();
	}
	if (draft.waveId.trim()) {
		spec.wave_id = draft.waveId.trim();
	}

	return spec;
}

export function draftToYamlOrJsonText(draft: InteractivePipelineDraft): string {
	return JSON.stringify(draftToInputSpecObject(draft), null, 2);
}

export function draftToStagedSpec(
	draft: InteractivePipelineDraft,
	validated = false,
): StagedPipelineSpec {
	const inputSpec = draftToInputSpecObject(draft);
	const inputSpecContent = JSON.stringify(inputSpec, null, 2);
	return {
		mode: "interactive",
		inputSpec,
		inputSpecContent,
		initialContractPath: draft.initialContractPath.trim() || undefined,
		waveId: draft.waveId.trim() || undefined,
		stewardHoldPolicy: draft.stewardHoldPolicy,
		ontologyPolicy: draft.ontologyPolicy,
		validated,
	};
}
