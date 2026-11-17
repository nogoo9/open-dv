export interface SiteMeta {
	readonly name: string;
	readonly version: string;
	readonly license: string;
	readonly repoUrl: string;
	readonly docsBaseUrl: string;
	readonly heroInstallCmd: string;
	readonly tourDurationSec: number;
}

export interface TrapItem {
	readonly id: string;
	readonly title: string;
	readonly summary: string;
}

export interface PipelineStage {
	readonly id: string;
	readonly code: string;
	readonly phase: "Discovery" | "Modelling" | "Verification" | "Compilation";
	readonly actor: string;
	readonly temp: string;
	readonly summary: string;
}

export interface AxiGate {
	readonly id: "G0" | "G1" | "G2" | "G3" | "G4" | "G5";
	readonly name: string;
	readonly rules: string;
	readonly check: string;
	readonly repair: string;
}

export interface FaultPreset {
	readonly id: string;
	readonly label: string;
	readonly gateId: AxiGate["id"];
	readonly symptom: string;
	readonly resolution: string;
	readonly traceSnippet: string;
}

export interface InferenceScenario {
	readonly id: string;
	readonly tabLabel: string;
	readonly mode:
		| "COLD_START_N1"
		| "MULTI_SOURCE_EXPANSION"
		| "HYBRID_PER_SOURCE"
		| "VERIFIED_NO_OP";
	readonly inputs: string;
	readonly strategy: string;
	readonly algorithms: string;
	readonly outputs: string;
}

export interface ConnectorSpec {
	readonly id: string;
	readonly name: string;
	readonly executionClass:
		| "DIRECT_DUCKDB_ATTACH"
		| "REMOTE_PUSHDOWN_PLUS_ARROW_SAMPLE"
		| "DOCUMENT_SPAN_EXTRACTOR";
	readonly introspection: string;
}

export interface DialectSnippet {
	readonly id: string;
	readonly label: string;
	readonly lang: "sql" | "yaml" | "bash";
	readonly filename: string;
	readonly code: string;
}

export interface ScreenshotPin {
	readonly id: string;
	readonly label: string;
	readonly xPct: number;
	readonly yPct: number;
	readonly detail: string;
}

export interface WorkbenchScreen {
	readonly id: string;
	readonly tabLabel: string;
	readonly title: string;
	readonly route: string;
	readonly theme: "dark" | "light";
	readonly webpPath: string;
	readonly pngPath: string;
	readonly width: number;
	readonly height: number;
	readonly alt: string;
	readonly summary: string;
	readonly pins: readonly ScreenshotPin[];
}

export interface TourChapter {
	readonly id: string;
	readonly number: number;
	readonly timestamp: string;
	readonly startSec: number;
	readonly endSec: number;
	readonly title: string;
	readonly callouts: readonly string[];
	readonly transcript: string;
}

export interface QuickstartStep {
	readonly step: number;
	readonly title: string;
	readonly command: string;
	readonly note: string;
}

export interface DocRef {
	readonly id: string;
	readonly path: string;
	readonly topic: string;
}
