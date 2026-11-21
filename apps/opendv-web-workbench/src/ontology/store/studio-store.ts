/**
 * Zustand Studio Store Composer & Backward-Compatible Barrel
 * (apps/opendv-web-workbench/src/ontology/store/studio-store.ts)
 */

import { useRef, useSyncExternalStore } from "react";
import { createStore } from "zustand/vanilla";
import { hasActiveReactDispatcher } from "../../explorer/explorer-store";
import { createEmptyZoneSqlByDialect } from "../wizard/step6-run-hydration";
import { createAgentDiscoverySlice } from "./agent-discovery-slice";
import { createExportPromoteSlice } from "./export-promote-slice";
import { createDefaultDiffState, createGraphDiffSlice } from "./graph-diff";
import {
	createGraphEditorSlice,
	selectFilteredNodes,
} from "./graph-editor-slice";
import { createJobMonitorController } from "./job-monitor";
import { createDefaultLayoutState, createLayoutSlice } from "./layout-slice";
import { createPipelineExecutionSlice } from "./pipeline-execution-slice";
import {
	createProposalStewardshipSlice,
	normalizeProposalOperationsForSubmission,
	resolveProposalBaseVersionAndSha,
	sortProposalsForStewardship,
} from "./proposal-stewardship-slice";
import {
	computeQualityScorecard,
	createDefaultQualityScorecard,
	deriveGroundingStatus,
} from "./scorecard";
import { createSessionLogsSlice } from "./session-logs-slice";
import { createSessionSlice } from "./session-slice";
import {
	createDefaultParsingState,
	createSourcesPreviewSlice,
} from "./sources-preview-slice";
import type { StoreGet, StoreSet, StudioStoreState } from "./store-types";
import {
	createStudioFetcher,
	normalizeBaseApiUrl,
	resolveStudioUrl,
} from "./studio-fetch";
import type { QualityScorecardOptions } from "./types";
import type { StudioStoreOptions, StudioUserRole } from "./types";
import { createWaiverFeedbackSlice } from "./waiver-feedback-slice";

export * from "./types";
export type {
	PipelineBaselineOption,
	PipelineHoldSummary,
	PipelineTopologyRow,
	SessionLogEntryDTO,
	StudioStoreState,
	ZoneSqlByDialect,
} from "./store-types";
export {
	canonicalizeGraphADict,
	dumpsJcs,
	serializeGraphAYaml,
} from "./jcs-export";
export {
	escapeIri,
	escapeTurtleString,
	escapeXml,
	escapeYamlString,
	generateLocalExport,
} from "./local-export";
export {
	computeQualityScorecard,
	createDefaultQualityScorecard,
	deriveGroundingStatus,
	EMPTY_ANSWERS_SHA256,
	recomputeScorecard,
	VALID_PATH_A_PREDICATES,
} from "./scorecard";
export type { QualityScorecardOptions };
export {
	hasGraphChangesFromBaseline,
	isConceptMutatedFromBaseline,
	isRelationshipMutatedFromBaseline,
} from "./graph-parser";
export {
	normalizeProposalOperationsForSubmission,
	resolveProposalBaseVersionAndSha,
	sortProposalsForStewardship,
} from "./proposal-stewardship-slice";
export { isSupportedSourceFile } from "./sources-preview-slice";
export { createDefaultParsingState } from "./sources-preview-slice";
export { createDefaultLayoutState } from "./layout-slice";
export { createDefaultDiffState } from "./graph-diff";
export { selectFilteredNodes } from "./graph-editor-slice";
export {
	getStudioAuthHeaders,
	normalizeStudioRole,
} from "./studio-fetch";

export type StudioStore = ReturnType<typeof createStudioStore>;

export function createStudioStore(options: StudioStoreOptions = {}) {
	const baseApiUrl = normalizeBaseApiUrl(options.apiUrl);
	const resolveUrl = (path: string) => resolveStudioUrl(baseApiUrl, path);

	let activeSessionRequestId = 0;
	let activeGraphRequestId = 0;
	let activeClarificationsRequestId = 0;
	let activeSourcesRequestId = 0;
	let activeProposalsRequestId = 0;
	const extraCleanups: Array<() => void> = [];

	return createStore<StudioStoreState>((rawSet, rawGet) => {
		const set: StoreSet = (partial) => rawSet(partial);
		const get: StoreGet = () => rawGet();
		let currentRole: StudioUserRole = options.userRole ?? "editor";

		const studioFetch = createStudioFetcher(
			baseApiUrl,
			() => get()?.userRole ?? currentRole,
			(nextRole) => {
				currentRole = nextRole;
				set({ userRole: nextRole });
			},
		);

		const jobMonitor = createJobMonitorController(
			set,
			get,
			studioFetch,
			resolveUrl,
			options,
		);

		return {
			...createSessionSlice(
				set,
				get,
				studioFetch,
				jobMonitor,
				options,
				() => ++activeSessionRequestId,
				() => activeSessionRequestId,
			),
			...createSourcesPreviewSlice(
				set,
				get,
				studioFetch,
				jobMonitor,
				() => ++activeSourcesRequestId,
				() => activeSourcesRequestId,
			),
			...createAgentDiscoverySlice(
				set,
				get,
				studioFetch,
				jobMonitor,
				() => ++activeClarificationsRequestId,
				() => activeClarificationsRequestId,
			),
			...createProposalStewardshipSlice(
				set,
				get,
				studioFetch,
				() => ++activeProposalsRequestId,
				() => activeProposalsRequestId,
			),
			...createGraphEditorSlice(
				set,
				get,
				studioFetch,
				() => ++activeGraphRequestId,
				() => activeGraphRequestId,
			),
			...createGraphDiffSlice(set, get, studioFetch),
			...createWaiverFeedbackSlice(set, get, studioFetch),
			...createExportPromoteSlice(set, get, studioFetch),
			...createPipelineExecutionSlice(set, get, studioFetch, resolveUrl, (fn) =>
				extraCleanups.push(fn),
			),
			...createSessionLogsSlice(set, get, studioFetch),
			...createLayoutSlice(set, get),

			reset: () => {
				activeSessionRequestId++;
				activeGraphRequestId++;
				activeClarificationsRequestId++;
				activeSourcesRequestId++;
				activeProposalsRequestId++;
				jobMonitor.nextJobGeneration();
				jobMonitor.cancelActiveJobWatchers();
				for (const fn of extraCleanups) {
					try {
						fn();
					} catch {
						// ignore cleanup failure
					}
				}
				set({
					userRole: options.userRole ?? "editor",
					activeSession: options.initialSession ?? null,
					sessions: options.initialSession ? [options.initialSession] : [],
					legacySessionCount: 0,
					starterPacks: [],
					isLoadingSessions: false,
					sources: [],
					isLoadingSources: false,
					parsingState: createDefaultParsingState(),
					evidenceTables: [],
					evidenceChunks: [],
					observedTermCounts: {},
					clarifications: [],
					answers: {},
					answers_sha256: undefined,
					isLoadingClarifications: false,
					isClarifying: false,
					isConstructing: false,
					activeSessionJobId: null,
					agentErrorCode: null,
					agentErrorDetail: null,
					budgetHold: null,
					stagedCandidates: [],
					fiboQaResult: null,
					resolvedCitations: [],
					proposals: [],
					selectedProposalId: null,
					proposalDrafts: {},
					isLoadingProposals: false,
					nodes: [],
					edges: [],
					committedNodes: [],
					committedEdges: [],
					selectedNodeId: null,
					selectedEdgeId: null,
					isLoadingGraph: false,
					hasUnsavedChanges: false,
					viewedVersion: null,
					isHistoricalView: false,
					isCommitting: false,
					isPromoting: false,
					isSubmittingWaiver: false,
					diffState: createDefaultDiffState(),
					qualityScorecard: createDefaultQualityScorecard(),
					sessionWaivers: {},
					selectedDialects: ["duckdb"],
					baselineOptions: [
						{
							run_id: "__cold_start__",
							label: "Fresh Cold-Start Vault",
							is_latest_certified: false,
						},
					],
					selectedBaselineRunId: "__cold_start__",
					customBaselineContractContent: null,
					topologyRows: [],
					stageStatuses: {},
					zoneSqlByZone: {},
					zoneSqlByDialect: createEmptyZoneSqlByDialect(),
					gateScorecard: null,
					sessionRuns: [],
					activeRunId: null,
					isRunningPipeline: false,
					activeHold: null,
					driftColumns: [],
					sessionLogEntries: [],
					sessionLogsTotal: 0,
					sessionCostTokens: 0,
					sessionCostUsd: 0,
					isLogsDrawerOpen: false,
					logsStageFilter: null,
					logsStepFilter: null,
					followLiveTail: true,
					layout: createDefaultLayoutState(),
					sseEvents: [],
					toasts: [],
				});
			},
		};
	});
}

export const studioStore = createStudioStore();

export function useStudioStore<T>(selector: (state: StudioStoreState) => T): T {
	if (!hasActiveReactDispatcher()) {
		return selector(studioStore.getState());
	}
	const lastStateRef = useRef<StudioStoreState | undefined>(undefined);
	const lastSelectorRef = useRef(selector);
	const lastResultRef = useRef<T | undefined>(undefined);
	const hasCachedRef = useRef(false);

	const getSnapshot = () => {
		const state = studioStore.getState();
		if (
			hasCachedRef.current &&
			lastStateRef.current === state &&
			lastSelectorRef.current === selector
		) {
			return lastResultRef.current as T;
		}
		lastStateRef.current = state;
		lastSelectorRef.current = selector;
		lastResultRef.current = selector(state);
		hasCachedRef.current = true;
		return lastResultRef.current;
	};

	return useSyncExternalStore(studioStore.subscribe, getSnapshot, getSnapshot);
}
