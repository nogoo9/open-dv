/**
 * Store Action Handlers for Workbench Explorer (User Directive R9 Modularity).
 *
 * Implements genuine Decision D2 asynchronous run initiation, generic hold resolution,
 * and data source loading without hardcoded fixtures.
 */

import {
	type CaptainHoldDecisionRequest,
	type CaptainHoldDecisionResponse,
	OpenDVPlatformClient,
	type PipelineRunHandle,
	type PipelineRunRequest,
	type ProjectRunDTO,
	type StatusTag,
	type VaultObservabilityEventDTO,
	createEmptyExplorerBundle,
} from "@opendv/protocol-ts";
import { FileExplorerDataSource, resolveApiBaseUrl } from "./data-source";
import {
	type ExplorerStoreState,
	type HoldResolutionRecord,
	cloneBundle,
} from "./types";

export type StoreGet = () => ExplorerStoreState;
export type StoreSet = (
	partial:
		| Partial<ExplorerStoreState>
		| ((state: ExplorerStoreState) => Partial<ExplorerStoreState>),
) => void;

export async function executeLoadRunsList(
	get: StoreGet,
	set: StoreSet,
): Promise<ProjectRunDTO[]> {
	const ds = get().dataSource;
	set({ isLoading: true, lastError: null });
	try {
		const runs = await ds.fetchRunsList();
		set({ availableRuns: runs, isLoading: false, blockingError: null });
		return runs;
	} catch (err) {
		const endpoint = ds.mode === "live" ? "/api/v1/runs" : "offline file";
		const status = (err as { status?: number })?.status;
		set({
			isLoading: false,
			lastError: String(err),
			bundle: createEmptyExplorerBundle(),
			blockingError: {
				hasError: true,
				title: "Platform Backend Unreachable",
				endpoint,
				status,
				details:
					err instanceof Error
						? err.message
						: "Failed to connect to the platform API.",
				retryAction: () => {
					void get().retryConnection();
				},
				openFileAction: (file: File) => {
					void get().loadFromFile(file);
				},
			},
		});
		return [];
	}
}

export async function executeLoadRun(
	get: StoreGet,
	set: StoreSet,
	runId: string,
	waveId?: string | undefined,
	getUnsubscribeSse?: () => (() => void) | null,
	setUnsubscribeSse?: (fn: (() => void) | null) => void,
): Promise<void> {
	const ds = get().dataSource;
	set({ isLoading: true, lastError: null });
	try {
		const bundle = await ds.fetchExplorerBundle(runId, waveId);
		set({
			bundle,
			activeRunId: runId,
			isLoading: false,
			blockingError: null,
			statusBannerText: bundle.statusLabel,
			sourceLabel: ds.sourceName,
		});
		if (ds.mode === "live") {
			const existingUnsub = getUnsubscribeSse?.();
			if (existingUnsub) existingUnsub();
			const newUnsub = ds.subscribeEvents(
				runId,
				(evt: VaultObservabilityEventDTO) =>
					get().ingestLiveObservabilityEvent(evt),
				(err: Error) => set({ lastError: err.message }),
			);
			setUnsubscribeSse?.(newUnsub);
		}
	} catch (err) {
		const endpoint =
			ds.mode === "live"
				? `/api/v1/runs/${runId}/explorer`
				: "offline file";
		const status = (err as { status?: number })?.status;
		set({
			isLoading: false,
			lastError: String(err),
			bundle: createEmptyExplorerBundle(),
			blockingError: {
				hasError: true,
				title: "Platform Backend Unreachable",
				endpoint,
				status,
				details:
					err instanceof Error
						? err.message
						: `Failed to load explorer bundle for run ${runId}.`,
				retryAction: () => {
					void get().retryConnection();
				},
				openFileAction: (file: File) => {
					void get().loadFromFile(file);
				},
			},
		});
	}
}

export async function executeLoadFromFile(
	get: StoreGet,
	set: StoreSet,
	file: File,
): Promise<void> {
	try {
		const fileDs = await FileExplorerDataSource.fromFile(file);
		await get().setDataSource(fileDs);
	} catch (err) {
		set({
			lastError: String(err),
			blockingError: {
				hasError: true,
				title: "Failed to Open Exported Run",
				endpoint: file.name,
				details: err instanceof Error ? err.message : String(err),
				retryAction: () => {
					void get().retryConnection();
				},
				openFileAction: (f: File) => {
					void get().loadFromFile(f);
				},
			},
		});
	}
}

export async function executeLoadFromBundleText(
	get: StoreGet,
	set: StoreSet,
	filename: string,
	content: string,
): Promise<void> {
	try {
		const fileDs = FileExplorerDataSource.fromFileContent(filename, content);
		await get().setDataSource(fileDs);
	} catch (err) {
		set({
			lastError: String(err),
			blockingError: {
				hasError: true,
				title: "Failed to Parse Run Content",
				endpoint: filename,
				details: err instanceof Error ? err.message : String(err),
				retryAction: () => {
					void get().retryConnection();
				},
				openFileAction: (f: File) => {
					void get().loadFromFile(f);
				},
			},
		});
	}
}

export function executeIngestLiveObservabilityEvent(
	get: StoreGet,
	set: StoreSet,
	event: VaultObservabilityEventDTO,
): void {
	const currentBundle = cloneBundle(get().bundle);
	const shortStage = event.stage_id.slice(0, 8);
	const stageMatchIdx = currentBundle.stages.findIndex((s) =>
		s.key.startsWith(shortStage),
	);
	const statusTag: StatusTag =
		event.status === "PASS" ||
		event.status === "AUTO_REPAIRED" ||
		event.status === "ASK-USER" ||
		event.status === "REJECT-WITH-TRACE" ||
		event.status === "AUTO-FIX"
			? event.status
			: "PASS";

	if (stageMatchIdx >= 0 && currentBundle.stages[stageMatchIdx]) {
		currentBundle.stages[stageMatchIdx].tag = statusTag;
	}

	if (currentBundle.artifacts.ledger) {
		const existingEvents = currentBundle.artifacts.ledger.events ?? [];
		const nextEvents: [string, string, StatusTag][] = [
			...existingEvents,
			[shortStage, event.layer1_story_banner.headline, statusTag],
		];
		currentBundle.artifacts.ledger.events = nextEvents;
		currentBundle.artifacts.ledger.short = `${nextEvents.length} events · RBAC-redacted stream`;
		currentBundle.artifacts.ledger.raw = `${currentBundle.artifacts.ledger.raw}\n${JSON.stringify(
			{
				seq: nextEvents.length,
				stage: event.stage_id,
				status: event.status,
				headline: event.layer1_story_banner.headline,
			},
		)}`;
	}

	const isHold = event.status === "ASK-USER";
	set({
		bundle: currentBundle,
		runState: isHold ? "hold" : get().runState,
		activeRunningStageIdx:
			stageMatchIdx >= 0 ? stageMatchIdx : get().activeRunningStageIdx,
		statusBannerText: isHold
			? `Hold · ${shortStage} awaiting steward`
			: `Running · ${shortStage}`,
	});
}

export async function executeTriggerPipelineRun(
	get: StoreGet,
	set: StoreSet,
	runOptions: {
		inputSpecPath?: string | undefined;
		baseUrl?: string | undefined;
		fetchImpl?: typeof fetch | undefined;
		request?: PipelineRunRequest | undefined;
	} = {},
): Promise<PipelineRunHandle | undefined> {
	set({
		runState: "running",
		activeRunningStageIdx: 0,
		statusBannerText: "Running · STAGE_00",
		lastError: null,
	});

	const ds = get().dataSource;
	const req: PipelineRunRequest = runOptions.request ?? {
		input_spec_path: runOptions.inputSpecPath ?? get().bundle.specFile ?? "",
		steward_hold_policy: "auto_approve_top_candidate",
		dry_run: false,
	};

	try {
		let handle: PipelineRunHandle;
		if (runOptions.fetchImpl) {
			const baseUrl = resolveApiBaseUrl(runOptions.baseUrl);
			const client = new OpenDVPlatformClient({
				baseUrl,
				rbacRole: get().bundle.rbacRole,
				fetchImpl: runOptions.fetchImpl,
			});
			handle = await client.createPipelineRun(req);
		} else {
			handle = await ds.createPipelineRun(req);
		}

		if (handle.run_id) {
			const nextBundle = cloneBundle(get().bundle);
			nextBundle.runId = handle.run_id;
			set({
				bundle: nextBundle,
				activeRunId: handle.run_id,
				runState: "running",
				activeRunningStageIdx: 0,
				statusBannerText: "Running · STAGE_00",
				lastError: null,
			});

			if (get().dataSource.subscribeEvents) {
				get().dataSource.subscribeEvents(handle.run_id, (ev) => {
					get().ingestLiveObservabilityEvent(ev);
				});
			}
		}
		return handle;
	} catch (err) {
		set({
			lastError: String(err),
			statusBannerText: `Failed to initiate run: ${err instanceof Error ? err.message : String(err)}`,
		});
		return undefined;
	}
}

export async function executeResolveStewardHold(
	get: StoreGet,
	set: StoreSet,
	params: {
		holdId: string;
		action: "APPROVE" | "REJECT";
		selectedCandidateId?: string | undefined;
		resolutionNotes?: string | undefined;
		eventId?: string | undefined;
		fetchImpl?: typeof fetch | undefined;
	},
): Promise<CaptainHoldDecisionResponse> {
	const { holdId, action, selectedCandidateId, resolutionNotes, eventId, fetchImpl } = params;
	const currentRunId = get().bundle.runId;
	const req: CaptainHoldDecisionRequest = {
		event_id: eventId ?? undefined,
		hold_id: holdId,
		decision_type: "RESOLVE_CAPTAIN_HOLD",
		action,
		selected_candidate_id: selectedCandidateId ?? undefined,
		resolution_notes:
			resolutionNotes ??
			(action === "APPROVE"
				? (selectedCandidateId
						? `Approved candidate ${selectedCandidateId}.`
						: `Approved hold ${holdId}.`)
				: `Rejected hold ${holdId}.`),
	};

	let response: CaptainHoldDecisionResponse;
	try {
		if (fetchImpl) {
			const client = new OpenDVPlatformClient({
				baseUrl: resolveApiBaseUrl(),
				rbacRole: get().bundle.rbacRole,
				fetchImpl,
			});
			response = await client.submitDecision(currentRunId, req);
		} else {
			response = await get().dataSource.submitDecision(currentRunId, req);
		}
	} catch (err) {
		set({ lastError: String(err) });
		throw err;
	}

	const nextBundle = cloneBundle(get().bundle);
	const chosenCandidate = req.selected_candidate_id ?? selectedCandidateId ?? "";
	const candidateSuffix = chosenCandidate ? ` (${chosenCandidate})` : "";
	const decisionSummary =
		action === "APPROVE"
			? `STEWARD_HOLD → APPROVED${candidateSuffix} by Steward (${response.decision_id}).`
			: `STEWARD_HOLD → REJECTED by Steward (${response.decision_id}); routed to quarantine.`;

	for (const art of Object.values(nextBundle.artifacts)) {
		if (art?.doc?.extractions) {
			art.doc.extractions = art.doc.extractions.map((ex) => {
				if (ex.holdId === holdId || (ex.tier === "Tier-U3" && !ex.resolved)) {
					return {
						...ex,
						resolved: true,
						resolutionAction: action,
						selectedCandidateId: chosenCandidate || undefined,
						decision: decisionSummary,
					};
				}
				return ex;
			});
		}
	}

	const targetStage =
		nextBundle.stages.find((s) => s.tag === "ASK-USER" || (s.tag as string) === "HOLD") ||
		nextBundle.stages.find((s) =>
			s.steps.some(
				(step) =>
					step.name.toLowerCase().includes("hold") ||
					(step as { holdId?: string }).holdId === holdId,
			),
		) ||
		nextBundle.stages[get().stageIdx] ||
		nextBundle.stages[0];

	if (targetStage) {
		targetStage.tag =
			action === "REJECT" ? "REJECT-WITH-TRACE" : "ASK-USER → resolved";
		const existingPrefix = targetStage.note
			? targetStage.note.split(" — ")[0]
			: `Hold ${holdId}`;
		const actionText =
			action === "APPROVE"
				? `approved${candidateSuffix}`
				: "rejected by steward";
		targetStage.note = `${existingPrefix} — ${actionText}`;
	}

	if (nextBundle.artifacts.ledger?.events) {
		const stageLabel =
			targetStage?.key?.split("_").slice(0, 2).join("_") ?? "STAGE_HOLD";
		nextBundle.artifacts.ledger.events = [
			...nextBundle.artifacts.ledger.events,
			[
				stageLabel,
				`Steward decision (${response.decision_id}): ${action} on ${holdId}${candidateSuffix}.`,
				action === "REJECT" ? "REJECT-WITH-TRACE" : "ASK-USER → resolved",
			],
		];
		nextBundle.artifacts.ledger.short = `${nextBundle.artifacts.ledger.events.length} events · RBAC-redacted stream`;
	}

	const record: HoldResolutionRecord = {
		holdId,
		action,
		selectedCandidateId: chosenCandidate || undefined,
		resolutionNotes: req.resolution_notes ?? undefined,
		decisionId: response.decision_id,
	};

	set((prev) => ({
		bundle: nextBundle,
		holdResolutions: {
			...prev.holdResolutions,
			[holdId]: record,
		},
	}));

	return response;
}
