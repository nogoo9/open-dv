/**
 * Explorer Store and View Types & Pure Utilities (User Directive R9 Modularity).
 */

import type {
	Artifact,
	CaptainHoldDecisionResponse,
	ExplorerBundleDTO,
	OpenDVPlatformClient,
	PipelineRunHandle,
	PipelineRunRequest,
	ProjectRunDTO,
	RbacRole,
	Stage,
	StatusTag,
	VaultObservabilityEventDTO,
	WaveFilterId,
} from "@opendv/protocol-ts";
import type { StoreApi, UseBoundStore } from "zustand";
import type { BlockingErrorState } from "./BlockingErrorBanner";
import type { ExplorerDataSource } from "./data-source";

export type ExplorerViewLevel = "pipeline" | "stage";
export type ExplorerSelectionType = "artifact" | "step" | "component" | null;
export type ArtifactFullTab = "viz" | "raw" | "split";
export type ExplorerRunState = "sealed" | "running" | "hold" | "failed";
export type ExplorerPresetBundleKey = string;

export interface ExplorerStoreInitOptions {
	dataSource?: ExplorerDataSource | undefined;
	bundle?: ExplorerBundleDTO | undefined;
	startView?: (ExplorerViewLevel | "artifact") | undefined;
	startStage?: number | undefined;
	startArtifact?: string | null | undefined;
	startWave?: WaveFilterId | undefined;
	platformClient?: OpenDVPlatformClient | undefined;
	syncBrowserUrl?: boolean | undefined;
	autoLoad?: boolean | undefined;
}

export interface HoldResolutionRecord {
	holdId: string;
	action: "APPROVE" | "REJECT";
	selectedCandidateId?: string | undefined;
	resolutionNotes?: string | undefined;
	decisionId: string;
}

export interface ExplorerStoreState {
	bundle: ExplorerBundleDTO;
	dataSource: ExplorerDataSource;
	blockingError: BlockingErrorState | null;
	sourceLabel: string;
	availableRuns: ProjectRunDTO[];
	activeRunId: string | null;
	isLoading: boolean;
	activeBundleKey: ExplorerPresetBundleKey;
	view: ExplorerViewLevel;
	stageIdx: number;
	selType: ExplorerSelectionType;
	selId: string | number | null;
	fullId: string | null;
	fullTab: ArtifactFullTab;
	activeWave: WaveFilterId;
	runState: ExplorerRunState;
	activeRunningStageIdx: number | null;
	statusBannerText: string;
	holdResolutions: Record<string, HoldResolutionRecord>;
	lastError: string | null;

	// Navigation & Selection Actions
	goPipeline: () => void;
	openStage: (stageIdx: number) => void;
	prevStage: () => void;
	nextStage: () => void;
	selectArtifact: (artifactId: string) => void;
	selectStep: (stepIdx: number) => void;
	selectComponent: (compIdx: number) => void;
	clearSelection: () => void;
	openFullArtifact: (
		artifactId: string,
		tab?: ArtifactFullTab | undefined,
	) => void;
	closeFullArtifact: () => void;
	setFullTab: (tab: ArtifactFullTab) => void;

	// Wave Filtering & Bundle Switching
	setWaveFilter: (wave: WaveFilterId) => void;
	getVisibleInputIds: () => string[];
	switchPresetBundle: (key: ExplorerPresetBundleKey) => void;
	loadBundleFromEvents: (
		events: readonly VaultObservabilityEventDTO[],
		rbacRole?: RbacRole | undefined,
	) => void;

	// Data Source & Live Backend Actions
	setDataSource: (dataSource: ExplorerDataSource) => Promise<void>;
	loadRunsList: () => Promise<ProjectRunDTO[]>;
	loadRun: (runId: string, waveId?: string | undefined) => Promise<void>;
	loadFromFile: (file: File) => Promise<void>;
	loadFromBundleText: (filename: string, content: string) => Promise<void>;
	retryConnection: () => Promise<void>;
	clearBlockingError: () => void;

	// URL Synchronization
	syncFromUrl: (pathname: string, search: string) => void;
	getCurrentUrl: () => string;

	// Live Run & Steward Hold Actions
	triggerPipelineRun: (
		options?:
			| {
					inputSpecPath?: string | undefined;
					baseUrl?: string | undefined;
					fetchImpl?: typeof fetch | undefined;
					request?: PipelineRunRequest | undefined;
			  }
			| undefined,
	) => Promise<PipelineRunHandle | undefined>;
	ingestLiveObservabilityEvent: (event: VaultObservabilityEventDTO) => void;
	resolveStewardHold: (params: {
		holdId: string;
		action: "APPROVE" | "REJECT";
		selectedCandidateId?: string | undefined;
		resolutionNotes?: string | undefined;
		eventId?: string | undefined;
		fetchImpl?: typeof fetch | undefined;
	}) => Promise<CaptainHoldDecisionResponse>;
}

export type ExplorerStore = UseBoundStore<StoreApi<ExplorerStoreState>>;

export function cloneBundle(bundle: ExplorerBundleDTO): ExplorerBundleDTO {
	const artifacts: Record<string, Artifact> = {};
	for (const [key, art] of Object.entries(bundle.artifacts)) {
		artifacts[key] = {
			...art,
			waves: art.waves ? [...art.waves] : undefined,
			cols: art.cols ? [...art.cols] : undefined,
			rows: art.rows ? art.rows.map((r) => [...r]) : undefined,
			kv: art.kv ? art.kv.map((p) => [...p] as [string, string]) : undefined,
			checks: art.checks
				? art.checks.map((c) => [...c] as [string, string, StatusTag])
				: undefined,
			events: art.events
				? art.events.map((e) => [...e] as [string, string, StatusTag])
				: undefined,
			outline: art.outline
				? art.outline.map((o) => [...o] as [string, string])
				: undefined,
			doc: art.doc
				? {
						...art.doc,
						extractions: art.doc.extractions.map((ex) => ({
							...ex,
							candidates: ex.candidates
								? ex.candidates.map((cand) => ({ ...cand }))
								: undefined,
						})),
					}
				: undefined,
		};
	}

	const stages: Stage[] = bundle.stages.map((st) => ({
		...st,
		ins: [...st.ins],
		outs: [...st.outs],
		steps: st.steps.map((sp) => ({
			...sp,
			reads: [...sp.reads],
			writes: [...sp.writes],
		})),
		comps: st.comps.map((c) => [...c] as [string, string, string, string]),
	}));

	return {
		...bundle,
		waves: bundle.waves.map((w) => ({ ...w })),
		phases: bundle.phases.map((p) => ({ ...p })),
		stages,
		artifacts,
		settingIds: [...bundle.settingIds],
		inputIds: [...bundle.inputIds],
		outputIds: [...bundle.outputIds],
	};
}
