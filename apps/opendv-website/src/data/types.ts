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
	readonly phase:
		| "Scope & Evidence"
		| "Discovery & Ontology"
		| "CEGAR & Critics"
		| "11-Target Storage";
	readonly actor: string;
	readonly temp: string;
	readonly summary: string;
}

export interface VerifierGate {
	readonly id: "L1" | "V1_V5" | "V6" | "V6B" | "V7" | "V8";
	readonly name: string;
	readonly rules: string;
	readonly check: string;
	readonly repair: string;
}

export interface FaultPreset {
	readonly id: string;
	readonly label: string;
	readonly gateId: VerifierGate["id"];
	readonly symptom: string;
	readonly resolution: string;
	readonly traceSnippet: string;
}

export interface InferenceScenario {
	readonly id: string;
	readonly tabLabel: string;
	readonly mode:
		| "COLD_START_W1"
		| "MULTI_SOURCE_BKCC"
		| "ADDITIVE_EXT_W2"
		| "HORIZON_STEER";
	readonly inputs: string;
	readonly strategy: string;
	readonly algorithms: string;
	readonly outputs: string;
}

export interface ConnectorSpec {
	readonly id: string;
	readonly name: string;
	readonly executionClass:
		| "LOCAL_FILE_INSPECT_AND_ATTACH"
		| "COLUMNAR_VECTOR_PROFILE"
		| "SSRF_GUARDED_KEYRING_PUSHDOWN"
		| "SYNCHRONOUS_DOCUMENT_MAP";
	readonly introspection: string;
}

export interface DialectSnippet {
	readonly id: string;
	readonly label: string;
	readonly lang: "sql" | "json" | "bash";
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
