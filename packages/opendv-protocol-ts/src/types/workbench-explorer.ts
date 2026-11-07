/**
 * 3-Level Pipeline Explorer Workbench (`Pipeline -> Stage -> Artifact`) Data Contracts
 * (`docs/design/open-dv-workbench/HANDOFF.md`).
 */

import type { RbacRole } from "./common";

export type ArtifactGroup =
	| "Setting"
	| "Input"
	| "Stage output"
	| "Final output";

export type VizKind =
	| "ontology"
	| "dv"
	| "table"
	| "kv"
	| "checks"
	| "events"
	| "doc"
	| "code";

export type StatusTag =
	| "PASS"
	| "AUTO_REPAIRED"
	| "ASK-USER"
	| "ASK-USER → resolved"
	| "REJECT-WITH-TRACE"
	| "AUTO-FIX";

export type WaveFilterId = "all" | "W0" | "W1" | "W2" | "W3";

export interface OntologyConceptSpec {
	name: string;
	arch: string;
	def: string;
	keys: string;
}

export interface OntologySvgNodeSpec {
	id: string;
	label: string;
	sub: string;
	x: number;
	y: number;
	w: number;
	h?: number | undefined;
}

export interface OntologySvgEdgeSpec {
	path: string;
	label?: string | undefined;
	lx?: number | undefined;
	ly?: number | undefined;
}

export interface DvSvgNodeSpec {
	id: string;
	kind: "hub" | "link" | "sat" | "sat_pii";
	label: string;
	sub: string;
	x: number;
	y: number;
	w: number;
	h: number;
}

export interface DvSvgEdgeSpec {
	x1: number;
	y1: number;
	x2: number;
	y2: number;
	dashed?: boolean | undefined;
}

export interface DocCandidateOption {
	id: string;
	label: string;
	score: number;
}

export interface DocExtractionSpec {
	id: string;
	quote: string;
	start: number;
	end: number;
	tier: "Tier-U1" | "Tier-U2" | "Tier-U3";
	conf: string;
	decision: string;
	holdId?: string | undefined;
	eventId?: string | undefined;
	resolved?: boolean | undefined;
	resolutionAction?: ("APPROVE" | "REJECT") | undefined;
	selectedCandidateId?: string | undefined;
	candidates?: DocCandidateOption[] | undefined;
}

export interface HoldResolutionParams {
	holdId: string;
	action: "APPROVE" | "REJECT";
	selectedCandidateId?: string | undefined;
	resolutionNotes?: string | undefined;
	eventId?: string | undefined;
}

export interface Artifact {
	id: string;
	name: string;
	short: string;
	group: ArtifactGroup;
	type: string;
	fmt: string;
	viz: VizKind;
	desc: string;
	raw: string;
	waves?: Exclude<WaveFilterId, "all">[] | undefined;
	sha256?: string | undefined;
	// For viz === 'table'
	cols?: string[] | undefined;
	rows?: string[][] | undefined;
	foot?: string | undefined;
	// For viz === 'kv'
	kv?: [string, string][] | undefined;
	// For viz === 'checks'
	checks?: [string, string, StatusTag][] | undefined;
	// For viz === 'events'
	events?: [string, string, StatusTag][] | undefined;
	// For viz === 'code'
	outline?: [string, string][] | undefined;
	// For viz === 'ontology'
	ontology?:
		| {
				concepts: OntologyConceptSpec[];
				nodes: OntologySvgNodeSpec[];
				edges: OntologySvgEdgeSpec[];
		  }
		| undefined;
	// For viz === 'dv'
	dv?:
		| {
				nodes: DvSvgNodeSpec[];
				edges: DvSvgEdgeSpec[];
		  }
		| undefined;
	// For viz === 'doc'
	doc?:
		| {
				documentName: string;
				sourceText: string;
				extractions: DocExtractionSpec[];
		  }
		| undefined;
}

export interface Step {
	name: string;
	kind?: ("action" | "decision" | "loop") | undefined;
	branch?: string | undefined;
	reads: string[];
	writes: string[];
	desc: string;
}

export interface StageComponentSpec {
	name: string;
	type: string;
	desc: string;
	sourcePath: string;
}

export interface Stage {
	key: string;
	phase: 0 | 1 | 2 | 3;
	name: string;
	persona: string;
	tier: string;
	tag: StatusTag;
	blurb: string;
	desc: string;
	note?: string | undefined;
	ins: string[];
	outs: string[];
	steps: Step[];
	comps: [string, string, string, string][];
}

export interface PhaseSpec {
	index: 0 | 1 | 2 | 3;
	label: "Discovery" | "Modelling" | "Verification" | "Compilation";
	sub: string;
}

export interface WaveMetadataSpec {
	id: Exclude<WaveFilterId, "all">;
	name: string;
	description: string;
}

export interface ExplorerBundleDTO {
	projectId: string;
	displayName: string;
	runId: string;
	specFile: string;
	wavesCount: number;
	waves: WaveMetadataSpec[];
	statusLabel: string;
	gatesPassed: number;
	gatesTotal: number;
	rbacRole: RbacRole;
	hasInitialContract: boolean;
	phases: PhaseSpec[];
	stages: Stage[];
	artifacts: Record<string, Artifact>;
	settingIds: string[];
	inputIds: string[];
	outputIds: string[];
}
