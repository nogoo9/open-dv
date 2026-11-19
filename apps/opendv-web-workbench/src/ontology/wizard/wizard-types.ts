/**
 * Shared TypeScript Contracts & Pure Helpers for the 6-Step Unified Wizard
 * (apps/opendv-web-workbench/src/ontology/wizard/wizard-types.ts)
 */

import type { ConceptNode, RelationshipEdge } from "../store/types";
import { PILL_TO_CANONICAL_STAGES } from "./logs-types";

export type WizardStepNumber = 1 | 2 | 3 | 4 | 5 | 6;

export interface WizardCandidateAttribute {
	attribute_id: string;
	name: string;
	data_type: string;
	checked: boolean;
	is_optional?: boolean | undefined;
}

export interface WizardCandidateLink {
	link_id: string;
	name: string;
	checked: boolean;
	target_concept?: string | undefined;
}

export interface WizardConceptCandidate {
	candidate_id: string;
	concept_uri: string;
	concept_name: string;
	grounding_origin?: string | undefined;
	bucket?: "structured" | "unstructured" | string | undefined;
	mention_count?: number | undefined;
	checked: boolean;
	fibo_match?:
		| {
				uri: string;
				predicate: string;
				score: number;
		  }
		| undefined;
	attributes: WizardCandidateAttribute[];
	dependent_links: WizardCandidateLink[];
	evidence_refs?: Array<Record<string, unknown>> | undefined;
}

export interface WizardBreakingCandidate {
	candidate_id: string;
	op_type: string;
	label: string;
	impact: "MAJOR" | "MINOR" | string;
	checked: boolean;
}

export interface WizardDiffSummary {
	fiboAdded: number;
	customAdded: number;
	enrichedCount: number;
	linksAdded: number;
	validationPassed: boolean;
	diagnostics?: string[] | undefined;
}

export interface WizardBaselineDigest {
	sessionId: string;
	version: string;
	sha256: string;
	label: string;
}

export interface PipelineStagePillDef {
	pill: "00" | "01" | "02" | "03" | "04" | "05" | "06";
	title: string;
	internalStages: string[];
}

export const PIPELINE_STAGE_PILLS: PipelineStagePillDef[] = [
	{
		pill: "00",
		title: "Preflight & Baseline",
		internalStages: [
			...(PILL_TO_CANONICAL_STAGES["00"] ?? ["STAGE_00_ORCHESTRATION"]),
		],
	},
	{
		pill: "01",
		title: "Structured Scout",
		internalStages: [
			...(PILL_TO_CANONICAL_STAGES["01"] ?? ["STAGE_01_SCOUT_SOURCE"]),
		],
	},
	{
		pill: "02",
		title: "Unstructured Scout",
		internalStages: [
			...(PILL_TO_CANONICAL_STAGES["02"] ?? ["STAGE_02_SCOUT_UNSTRUCTURED"]),
		],
	},
	{
		pill: "03",
		title: "Ship Vault Synthesis",
		internalStages: [
			...(PILL_TO_CANONICAL_STAGES["03"] ?? [
				"STAGE_03_SHIP_VAULT_PASS1",
				"STAGE_04_SHIP_VAULT_PASS2",
			]),
		],
	},
	{
		pill: "04",
		title: "Static Gates & Repair",
		internalStages: [
			...(PILL_TO_CANONICAL_STAGES["04"] ?? ["STAGE_05_AXI_DV_STATIC_GATES"]),
		],
	},
	{
		pill: "05",
		title: "5-Zone Lower",
		internalStages: [
			...(PILL_TO_CANONICAL_STAGES["05"] ?? [
				"STAGE_07_DETERMINISTIC_COMPILER",
			]),
		],
	},
	{
		pill: "06",
		title: "Prover & Sandbox",
		internalStages: [
			...(PILL_TO_CANONICAL_STAGES["06"] ?? [
				"STAGE_06_AXI_DV_DYNAMIC_SANDBOX",
			]),
		],
	},
];

export function cascadeUncheckConcept<
	T extends {
		candidate_id: string;
		checked: boolean;
		attributes: Array<{ checked: boolean }>;
		dependent_links: Array<{ checked: boolean }>;
	},
>(concepts: T[], candidateId: string, checked: boolean): T[] {
	return concepts.map((item) => {
		if (item.candidate_id !== candidateId) return item;
		return {
			...item,
			checked,
			attributes: checked
				? item.attributes
				: item.attributes.map((a) => ({ ...a, checked: false })),
			dependent_links: checked
				? item.dependent_links
				: item.dependent_links.map((l) => ({ ...l, checked: false })),
		};
	});
}

export function computeStep5DiffSummary(
	nodes: ConceptNode[],
	edges: RelationshipEdge[],
	modifiedCount: number,
	breakingCount: number,
): WizardDiffSummary {
	return {
		fiboAdded: nodes.filter((n) => n.external_mappings?.length > 0).length,
		customAdded: nodes.filter((n) => !n.external_mappings?.length).length,
		enrichedCount: modifiedCount,
		linksAdded: edges.length,
		validationPassed: breakingCount === 0,
	};
}

export function mapAskFiboActions(
	rawActions: Array<Record<string, unknown>> | undefined,
) {
	return (rawActions ?? []).map((a, i) => ({
		action_id: String(a.action_id ?? `act-${i}`),
		label: String(a.label ?? a.op_type ?? "Apply action"),
		op_type: String(a.op_type ?? "add_external_mapping"),
		payload: a,
	}));
}

export function triggerTextFileDownload(fileName: string, text: string): void {
	if (
		typeof document === "undefined" ||
		typeof URL === "undefined" ||
		typeof URL.createObjectURL !== "function"
	) {
		return;
	}
	const url = URL.createObjectURL(
		new Blob([text], { type: "text/plain;charset=utf-8" }),
	);
	const a = document.createElement("a");
	a.href = url;
	a.download = fileName;
	document.body.appendChild(a);
	a.click();
	a.remove();
	URL.revokeObjectURL?.(url);
}
