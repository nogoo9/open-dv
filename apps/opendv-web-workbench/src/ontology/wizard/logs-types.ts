/**
 * Types & stage-pill mapping helpers for SessionLogsInspector (`docs/28 §4.4`).
 */

export type DeepLogTabId = "prompt" | "response" | "tools" | "raw";

export interface SessionLogTokenUsage {
	input_tokens?: number | undefined;
	output_tokens?: number | undefined;
	total_tokens?: number | undefined;
	cost_usd?: number | undefined;
}

export interface SessionLogEntry {
	seq: number;
	raw_id?: number | undefined;
	timestamp: string;
	origin?: string | undefined;
	event: string;
	wizard_step?: number | null | undefined;
	stage_id?: string | null | undefined;
	persona?: string | null | undefined;
	source_id?: string | null | undefined;
	job_id?: string | null | undefined;
	category?: string | null | undefined;
	provider?: string | null | undefined;
	model_id?: string | null | undefined;
	duration_ms?: number | null | undefined;
	token_usage?: SessionLogTokenUsage | null | undefined;
	masked_prompt?: string | null | undefined;
	structured_or_raw_response?: string | null | undefined;
	tool_calls?: readonly Record<string, unknown>[] | undefined;
	toon_diagnostics?: readonly string[] | undefined;
	status?: string | null | undefined;
	headline?: string | null | undefined;
	data?: Record<string, unknown> | null | undefined;
}

export interface ConnectSessionLogsStreamOptions {
	sessionId: string;
	apiUrl?: string | undefined;
	stageFilter?: string | null | undefined;
	wizardStep?: number | null | undefined;
	personaFilter?: string | null | undefined;
	categoryFilter?: string | null | undefined;
	lastEventId?: string | number | null | undefined;
	once?: boolean | undefined;
	onLogEntry: (entry: SessionLogEntry, lastEventId?: string) => void;
	onStreamEnd?:
		| ((payload: { status: string; session_id?: string }) => void)
		| undefined;
	onError?: ((event: unknown) => void) | undefined;
}

export interface SessionLogsStreamHandle {
	close: () => void;
	getLastEventId: () => string | null;
}

export const PILL_TO_CANONICAL_STAGES: Readonly<
	Record<string, readonly string[]>
> = {
	"00": ["STAGE_00_ORCHESTRATION"],
	"01": ["STAGE_01_SCOUT_SOURCE"],
	"02": ["STAGE_02_SCOUT_UNSTRUCTURED"],
	"03": ["STAGE_03_SHIP_VAULT_PASS1", "STAGE_04_SHIP_VAULT_PASS2"],
	"04": ["STAGE_05_AXI_DV_STATIC_GATES"],
	"05": ["STAGE_07_DETERMINISTIC_COMPILER"],
	"06": ["STAGE_06_AXI_DV_DYNAMIC_SANDBOX"],
};

export function normalizeStageFilterToPill(raw?: string | null): string {
	if (!raw) return "all";
	const clean = raw.trim().toUpperCase();
	if (!clean || clean === "ALL") return "all";
	if (clean in PILL_TO_CANONICAL_STAGES) return clean;
	const stripped = clean.replace(/^S/, "");
	if (stripped in PILL_TO_CANONICAL_STAGES) return stripped;
	for (const [pill, stages] of Object.entries(PILL_TO_CANONICAL_STAGES)) {
		if (stages.includes(clean)) return pill;
	}
	return "all";
}

export const STAGE_PILL_OPTIONS: ReadonlyArray<{
	pill: string;
	label: string;
}> = [
	{ pill: "00", label: "00 Orch (STAGE_00)" },
	{ pill: "01", label: "01 Profile (STAGE_01)" },
	{ pill: "02", label: "02 Drift (STAGE_02)" },
	{ pill: "03", label: "03 Synth (STAGE_03/04)" },
	{ pill: "04", label: "04 Repair (STAGE_05)" },
	{ pill: "05", label: "05 Lower (STAGE_07)" },
	{ pill: "06", label: "06 Prover (STAGE_06)" },
];

export function resolvePillToCanonicalStages(pill: string): string[] {
	const clean = pill.trim().toUpperCase();
	if (!clean || clean === "ALL") {
		return [];
	}
	const mapped = PILL_TO_CANONICAL_STAGES[clean];
	if (mapped) {
		return [...mapped];
	}
	if (clean.startsWith("STAGE_")) {
		return [clean];
	}
	return [];
}

export function normalizeApiBaseUrl(apiUrl?: string): string {
	return (apiUrl ?? "").replace(/\/api\/v1\/?$/, "").replace(/\/+$/, "");
}

export function buildLogsExportUrl(sessionId: string, apiUrl?: string): string {
	const base = normalizeApiBaseUrl(apiUrl);
	return `${base}/api/v1/ontology/studio/sessions/${encodeURIComponent(sessionId)}/logs/export`;
}

export function buildLogsSnapshotUrl(
	sessionId: string,
	opts?: {
		apiUrl?: string | undefined;
		stageFilter?: string | null | undefined;
		wizardStep?: number | string | null | undefined;
		limit?: number | undefined;
	},
): string {
	const base = normalizeApiBaseUrl(opts?.apiUrl);
	const params = new URLSearchParams();
	params.set("limit", String(opts?.limit ?? 500));
	const pill = normalizeStageFilterToPill(opts?.stageFilter);
	if (pill !== "all") {
		params.set("stage_id", pill);
	}
	if (
		opts?.wizardStep !== undefined &&
		opts?.wizardStep !== null &&
		String(opts.wizardStep).trim() !== "" &&
		String(opts.wizardStep).trim().toLowerCase() !== "all"
	) {
		params.set("wizard_step", String(opts.wizardStep).trim());
	}
	return `${base}/api/v1/ontology/studio/sessions/${encodeURIComponent(sessionId)}/logs?${params.toString()}`;
}

export function connectSessionLogsStream(
	options: ConnectSessionLogsStreamOptions,
): SessionLogsStreamHandle {
	if (typeof EventSource === "undefined") {
		return { close: () => {}, getLastEventId: () => null };
	}

	const base = normalizeApiBaseUrl(options.apiUrl);
	const params = new URLSearchParams();
	if (options.stageFilter && options.stageFilter !== "all") {
		params.set("stage_id", options.stageFilter);
	}
	if (options.wizardStep !== undefined && options.wizardStep !== null) {
		params.set("wizard_step", String(options.wizardStep));
	}
	if (options.personaFilter && options.personaFilter !== "all") {
		params.set("persona", options.personaFilter);
	}
	if (options.categoryFilter && options.categoryFilter !== "all") {
		params.set("category", options.categoryFilter);
	}
	if (options.once) {
		params.set("once", "true");
	}
	if (
		options.lastEventId !== undefined &&
		options.lastEventId !== null &&
		String(options.lastEventId).trim() !== ""
	) {
		params.set("last_event_id", String(options.lastEventId).trim());
	}

	const qs = params.toString();
	const url = `${base}/api/v1/ontology/studio/sessions/${encodeURIComponent(options.sessionId)}/logs/stream${qs ? `?${qs}` : ""}`;

	let lastId: string | null =
		options.lastEventId !== undefined && options.lastEventId !== null
			? String(options.lastEventId)
			: null;
	const es = new EventSource(url);

	es.addEventListener("log_entry", ((ev: MessageEvent<string>) => {
		try {
			const parsed = JSON.parse(ev.data) as SessionLogEntry;
			if (ev.lastEventId) {
				lastId = ev.lastEventId;
			} else if (parsed.seq !== undefined) {
				lastId = String(parsed.seq);
			}
			options.onLogEntry(parsed, ev.lastEventId);
		} catch {
			// Ignore malformed SSE JSON payload
		}
	}) as EventListener);

	es.addEventListener("stream_end", ((ev: MessageEvent<string>) => {
		try {
			const payload = JSON.parse(ev.data) as {
				status: string;
				session_id?: string;
			};
			options.onStreamEnd?.(payload);
		} catch {
			options.onStreamEnd?.({
				status: "COMPLETED",
				session_id: options.sessionId,
			});
		} finally {
			es.close();
		}
	}) as EventListener);

	if (options.onError) {
		es.addEventListener("error", ((ev: Event) => {
			options.onError?.(ev);
		}) as EventListener);
	}

	return {
		close: () => es.close(),
		getLastEventId: () => lastId,
	};
}
