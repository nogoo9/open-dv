/**
 * ExplorerDataSource Service Provider Interface (SPI) (`data-source.ts`)
 *
 * Implements decoupled live REST/SSE streaming (`RestSseExplorerDataSource`)
 * and offline exported bundle inspection (`FileExplorerDataSource`).
 *
 * Zero hardcoded ports/localhost (REQ-CFG-01).
 * Zero demo data fallback (REQ-MOCK-01, REQ-FAIL-01).
 */

import {
	type CaptainHoldDecisionRequest,
	type CaptainHoldDecisionResponse,
	type EventSourceFactory,
	type ExplorerBundleDTO,
	LedgerReplayClient,
	OpenDVPlatformClient,
	type PipelineRunHandle,
	type PipelineRunRequest,
	type ProjectRunDTO,
	type RbacRole,
	type RunArtifactDTO,
	VaultEventStreamClient,
	type VaultObservabilityEventDTO,
	buildExplorerBundleFromObservabilityEvents,
	createEmptyExplorerBundle,
} from "@opendv/protocol-ts";

export type PipelineRunSummary = ProjectRunDTO;
export type ExplorerBundle = ExplorerBundleDTO;
export type ObservabilityEvent = VaultObservabilityEventDTO;

declare global {
	interface Window {
		__OPENDV_CONFIG__?: {
			apiUrl?: string | undefined;
			[key: string]: unknown;
		};
	}
}

/**
 * Resolves the platform API base URL adhering to REQ-CFG-01:
 * 1. Explicit argument if passed
 * 2. window.__OPENDV_CONFIG__?.apiUrl
 * 3. import.meta.env.VITE_OPENDV_API_URL
 * 4. Default: "/api/v1"
 * Zero hardcoded ports or localhost addresses.
 */
export function resolveApiBaseUrl(customUrl?: string): string {
	if (customUrl !== undefined && customUrl.trim() !== "") {
		return customUrl.trim();
	}
	if (
		typeof globalThis !== "undefined" &&
		"window" in globalThis &&
		(
			globalThis as unknown as {
				window?: { __OPENDV_CONFIG__?: { apiUrl?: string } };
			}
		).window?.__OPENDV_CONFIG__?.apiUrl
	) {
		const cfg = (
			globalThis as unknown as {
				window?: { __OPENDV_CONFIG__?: { apiUrl?: string } };
			}
		).window?.__OPENDV_CONFIG__?.apiUrl;
		if (cfg && cfg.trim() !== "") {
			return cfg.trim();
		}
	}
	if (
		typeof import.meta !== "undefined" &&
		typeof (import.meta as unknown as { env?: Record<string, string> }).env
			?.VITE_OPENDV_API_URL === "string"
	) {
		const envUrl = (import.meta as unknown as { env?: Record<string, string> })
			.env?.VITE_OPENDV_API_URL;
		if (envUrl && envUrl.trim() !== "") {
			return envUrl.trim();
		}
	}
	return "/api/v1";
}

/**
 * Service Provider Interface for Explorer Data Sources.
 */
export interface ExplorerDataSource {
	readonly mode: "live" | "file";
	readonly sourceName: string;

	fetchRunsList(): Promise<ProjectRunDTO[]>;
	fetchExplorerBundle(
		runId: string,
		waveId?: string,
	): Promise<ExplorerBundleDTO>;
	fetchArtifactContent(runId: string, artifactId: string): Promise<string>;
	createPipelineRun(request: PipelineRunRequest): Promise<PipelineRunHandle>;
	submitDecision(
		runId: string,
		decision: CaptainHoldDecisionRequest,
	): Promise<CaptainHoldDecisionResponse>;
	subscribeEvents(
		runId: string,
		onEvent: (event: VaultObservabilityEventDTO) => void,
		onError?: ((err: Error) => void) | undefined,
	): () => void;
}

export interface RestSseDataSourceOptions {
	baseUrl?: string | undefined;
	rbacRole?: RbacRole | undefined;
	fetchImpl?: typeof fetch | undefined;
	eventSourceFactory?: EventSourceFactory | undefined;
	client?: OpenDVPlatformClient | undefined;
}

/**
 * Live REST/SSE Data Source connecting to the Headless Open-DV Platform API.
 */
export class RestSseExplorerDataSource implements ExplorerDataSource {
	readonly mode = "live" as const;
	readonly sourceName: string;
	readonly client: OpenDVPlatformClient;
	private readonly eventSourceFactory?: EventSourceFactory | undefined;

	constructor(options: RestSseDataSourceOptions = {}) {
		const resolvedUrl = resolveApiBaseUrl(options.baseUrl);
		this.client =
			options.client ??
			new OpenDVPlatformClient({
				baseUrl: resolvedUrl,
				rbacRole: options.rbacRole ?? "INTERNAL_MODELER",
				fetchImpl: options.fetchImpl,
			});
		this.eventSourceFactory = options.eventSourceFactory;
		this.sourceName = `Live API (${resolvedUrl || "/api/v1"})`;
	}

	async fetchRunsList(): Promise<ProjectRunDTO[]> {
		return this.client.listRuns();
	}

	async fetchExplorerBundle(
		runId: string,
		waveId?: string,
	): Promise<ExplorerBundleDTO> {
		return this.client.getExplorerBundle(runId, waveId);
	}

	async fetchArtifactContent(
		runId: string,
		artifactId: string,
	): Promise<string> {
		const art = await this.client.getRunArtifact(runId, artifactId);
		if (art.content) {
			return art.content;
		}
		if (art.sha256) {
			return this.client.getArtifact(art.sha256);
		}
		return "";
	}

	async createPipelineRun(
		request: PipelineRunRequest,
	): Promise<PipelineRunHandle> {
		return this.client.createPipelineRun(request);
	}

	async submitDecision(
		runId: string,
		decision: CaptainHoldDecisionRequest,
	): Promise<CaptainHoldDecisionResponse> {
		return this.client.submitDecision(runId, decision);
	}

	subscribeEvents(
		runId: string,
		onEvent: (event: VaultObservabilityEventDTO) => void,
		onError?: ((err: Error) => void) | undefined,
	): () => void {
		const base = this.client.getBaseUrl();
		const streamUrl = `${base}/api/v1/runs/${encodeURIComponent(runId)}/events`;
		const sseClient = new VaultEventStreamClient({
			streamUrl,
			onEvent,
			onError: (err) => {
				onError?.(err instanceof Error ? err : new Error(String(err)));
			},
			eventSourceFactory: this.eventSourceFactory,
		});
		sseClient.connect();
		return () => {
			sseClient.disconnect();
		};
	}
}

export interface FileExplorerDataSourceOptions {
	bundle: ExplorerBundleDTO;
	sourceName?: string | undefined;
	ledgerEvents?: readonly VaultObservabilityEventDTO[] | undefined;
}

/**
 * File Export Data Source loading exported runs:
 * `explorer_bundle.json`, `pipeline_report.json`, or `vault-ledger.jsonl`.
 */
export class FileExplorerDataSource implements ExplorerDataSource {
	readonly mode = "file" as const;
	readonly sourceName: string;
	private bundle: ExplorerBundleDTO;
	private readonly ledgerEvents: readonly VaultObservabilityEventDTO[];

	constructor(options: FileExplorerDataSourceOptions) {
		this.bundle = options.bundle;
		this.sourceName = options.sourceName ?? "Exported Run";
		this.ledgerEvents = options.ledgerEvents ?? [];
	}

	static fromBundle(
		bundle: ExplorerBundleDTO,
		sourceName = "Exported Run (explorer_bundle.json)",
	): FileExplorerDataSource {
		return new FileExplorerDataSource({ bundle, sourceName });
	}

	static fromJSONL(
		jsonlText: string,
		sourceName = "Exported Run (vault-ledger.jsonl)",
	): FileExplorerDataSource {
		const replayClient = LedgerReplayClient.loadFromJSONL(jsonlText);
		const events = replayClient.getEvents();
		const bundle = buildExplorerBundleFromObservabilityEvents(
			undefined,
			undefined,
			events,
			"INTERNAL_MODELER",
		);
		return new FileExplorerDataSource({
			bundle,
			sourceName,
			ledgerEvents: events,
		});
	}

	static fromReport(
		report: string | Record<string, unknown>,
		sourceName = "Exported Run (pipeline_report.json)",
	): FileExplorerDataSource {
		const parsed =
			typeof report === "string"
				? (JSON.parse(report) as Record<string, unknown>)
				: report;
		if (parsed.explorer_bundle && typeof parsed.explorer_bundle === "object") {
			return new FileExplorerDataSource({
				bundle: parsed.explorer_bundle as ExplorerBundleDTO,
				sourceName,
			});
		}
		const bundle = createEmptyExplorerBundle();
		bundle.runId = (parsed.run_id as string) ?? "exported-run";
		bundle.displayName =
			(parsed.display_name as string) ?? "Exported Pipeline Run";
		bundle.statusLabel = (parsed.overall_status as string) ?? "Sealed";
		if (typeof parsed.spec_file === "string") {
			bundle.specFile = parsed.spec_file;
		}
		if (typeof parsed.gates_passed === "number") {
			bundle.gatesPassed = parsed.gates_passed;
		}
		if (typeof parsed.gates_total === "number") {
			bundle.gatesTotal = parsed.gates_total;
		}
		return new FileExplorerDataSource({ bundle, sourceName });
	}

	static async fromFile(file: File): Promise<FileExplorerDataSource> {
		const content = await file.text();
		return FileExplorerDataSource.fromFileContent(file.name, content);
	}

	static fromFileContent(
		filename: string,
		content: string,
	): FileExplorerDataSource {
		const lower = filename.toLowerCase();
		if (
			lower.endsWith(".jsonl") ||
			(content.trimStart().startsWith("{") && content.includes("\n{"))
		) {
			try {
				return FileExplorerDataSource.fromJSONL(
					content,
					`Exported Run (${filename})`,
				);
			} catch {
				// Fall back to JSON parsing if JSONL fails
			}
		}

		try {
			const parsed = JSON.parse(content) as Record<string, unknown>;
			if (parsed.stages && parsed.artifacts) {
				return FileExplorerDataSource.fromBundle(
					parsed as unknown as ExplorerBundleDTO,
					`Exported Run (${filename})`,
				);
			}
			return FileExplorerDataSource.fromReport(
				parsed,
				`Exported Run (${filename})`,
			);
		} catch (err) {
			throw new Error(
				`Failed to parse exported run from ${filename}: ${String(err)}`,
			);
		}
	}

	async fetchRunsList(): Promise<ProjectRunDTO[]> {
		const runId = this.bundle.runId || "exported-run";
		return [
			{
				run_id: runId,
				project_id: this.bundle.projectId || "exported-project",
				harness_provider: "antigravity",
				status: "COMPLETED",
				started_at_utc: new Date().toISOString(),
				completed_at_utc: new Date().toISOString(),
				current_stage_id: "STAGE_07_DETERMINISTIC_COMPILER",
				event_count: this.ledgerEvents.length,
				active_hold_ids: [],
				events_sse_url: `/api/v1/runs/${runId}/events`,
				gates_url: `/api/v1/runs/${runId}/gates`,
				sandbox_url: `/api/v1/runs/${runId}/sandbox`,
				decisions_url: `/api/v1/runs/${runId}/decisions`,
				has_initial_contract: this.bundle.hasInitialContract,
				lifecycle_stage: "COLD_START_N1",
			},
		];
	}

	async fetchExplorerBundle(
		_runId: string,
		_waveId?: string,
	): Promise<ExplorerBundleDTO> {
		return this.bundle;
	}

	async fetchArtifactContent(
		_runId: string,
		artifactId: string,
	): Promise<string> {
		const art = this.bundle.artifacts[artifactId];
		if (!art) {
			throw new Error(
				`Artifact ${artifactId} not found in exported file bundle`,
			);
		}
		return art.raw ?? JSON.stringify(art, null, 2);
	}

	async createPipelineRun(
		_request: PipelineRunRequest,
	): Promise<PipelineRunHandle> {
		throw new Error(
			"Cannot trigger new pipeline run in exported-run file mode (read-only).",
		);
	}

	async submitDecision(
		runId: string,
		decision: CaptainHoldDecisionRequest,
	): Promise<CaptainHoldDecisionResponse> {
		const chosenCandidate =
			decision.selected_candidate_id ?? "APPROVED_CANDIDATE";
		const decisionId = `dec-${decision.hold_id ?? "hold"}-${decision.action.toLowerCase()}`;

		for (const art of Object.values(this.bundle.artifacts)) {
			if (art.doc) {
				art.doc.extractions = art.doc.extractions.map((ex) => {
					if (ex.holdId === decision.hold_id || ex.tier === "Tier-U3") {
						return {
							...ex,
							resolved: true,
							resolutionAction:
								decision.action === "APPROVE" ? "APPROVE" : "REJECT",
							selectedCandidateId: chosenCandidate,
							decision: `Offline hold resolved: ${decision.action} (${chosenCandidate})`,
						};
					}
					return ex;
				});
			}
		}

		return {
			decision_id: decisionId,
			status: decision.action === "REJECT" ? "REJECTED" : "ACCEPTED",
			resumed_run_id: runId,
			next_stage_id: "STAGE_03_SHIP_VAULT_PASS1",
			next_event_id: `evt-${decisionId}`,
		};
	}

	subscribeEvents(
		_runId: string,
		onEvent: (event: VaultObservabilityEventDTO) => void,
		_onError?: ((err: Error) => void) | undefined,
	): () => void {
		let cancelled = false;
		if (this.ledgerEvents.length > 0) {
			const timer = setTimeout(() => {
				if (!cancelled) {
					for (const ev of this.ledgerEvents) {
						if (cancelled) break;
						onEvent(ev);
					}
				}
			}, 0);
			return () => {
				cancelled = true;
				clearTimeout(timer);
			};
		}
		return () => {
			cancelled = true;
		};
	}
}
