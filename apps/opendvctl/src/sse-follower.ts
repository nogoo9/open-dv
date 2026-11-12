/**
 * D2 Live SSE Event Follower & Stream Formatter (Pipeline Runs + Ontology Studio Jobs).
 *
 * Consumes real-time W3C Server-Sent Events from `events_sse_url` or
 * `/api/v1/ontology/studio/sessions/{sid}/jobs/{job_id}/events` and renders
 * live stage transitions, Studio job progress, and token streams.
 */

import { SseStreamParser } from "@opendv/protocol-ts";
import { CliDomainError } from "./error-handler";

export function formatShortStage(stageId: string): string {
	const parts = stageId.split("_");
	if (parts.length >= 2 && parts[0]?.toUpperCase() === "STAGE") {
		return `STAGE ${parts[1]}`;
	}
	return stageId;
}

export interface SseFollowerOptions {
	streamUrl: string;
	headers?: Record<string, string> | undefined;
	lastEventId?: string | undefined;
	streamEnabled?: boolean | undefined;
	tokensEnabled?: boolean | undefined;
	writer?: ((line: string) => void) | undefined;
	tokenWriter?: ((token: string) => void) | undefined;
	fetchImpl?: typeof fetch | undefined;
}

export interface TerminalRunSummary {
	status: string;
	[key: string]: unknown;
}

export class StudioSsePrematureEofError extends Error {
	readonly lastEventId?: string | undefined;

	constructor(message: string, lastEventId?: string) {
		super(message);
		this.name = "StudioSsePrematureEofError";
		this.lastEventId = lastEventId;
	}
}

export async function followEventStream(
	options: SseFollowerOptions,
): Promise<TerminalRunSummary> {
	const fetchFn = options.fetchImpl ?? globalThis.fetch.bind(globalThis);
	const reqHeaders: Record<string, string> = {
		Accept: "text/event-stream",
		...options.headers,
	};
	if (options.lastEventId) {
		reqHeaders["Last-Event-ID"] = options.lastEventId;
	}
	const res = await fetchFn(options.streamUrl, {
		headers: reqHeaders,
	});

	if (!res.ok || !res.body) {
		throw new Error(
			`Failed to establish SSE connection (${options.streamUrl}): HTTP ${res.status}`,
		);
	}

	const reader = res.body.getReader();
	const decoder = new TextDecoder();
	const sseParser = new SseStreamParser();
	let terminalResult: TerminalRunSummary = { status: "COMPLETED" };

	const streamEnabled = options.streamEnabled ?? true;
	const tokensEnabled = options.tokensEnabled ?? false;
	const logLine = options.writer ?? ((line: string) => console.log(line));
	const logToken =
		options.tokenWriter ??
		((tok: string) => {
			if (typeof process !== "undefined" && process.stdout?.write) {
				process.stdout.write(tok);
			} else {
				console.log(tok);
			}
		});

	try {
		while (true) {
			const { done, value } = await reader.read();
			if (done) {
				break;
			}

			const chunk = decoder.decode(value, { stream: true });
			const frames = sseParser.feed(chunk);

			for (const frame of frames) {
				const evType = frame.event || "message";

				if (evType === "run_complete") {
					try {
						terminalResult = JSON.parse(frame.data) as TerminalRunSummary;
					} catch {
						terminalResult = { status: "COMPLETED", data: frame.data };
					}
					return terminalResult;
				}

				if (evType === "hold") {
					try {
						terminalResult = JSON.parse(frame.data) as TerminalRunSummary;
					} catch {
						terminalResult = { status: "PAUSED_HOLD", data: frame.data };
					}
					return terminalResult;
				}

				try {
					const dObj = JSON.parse(frame.data) as Record<string, unknown>;
					const eventType = String(dObj.event_type || evType).toUpperCase();
					const payload = (dObj.payload as Record<string, unknown>) || dObj;

					const w = payload.wave_index ?? 0;
					const st = String(payload.stage_id ?? "");
					const p = String(payload.agent_persona || payload.persona || "");
					const m = String(payload.model || payload.model_used || "");

					if (eventType === "STAGE_STARTED") {
						if (streamEnabled) {
							const msg = `[Wave ${w}][${formatShortStage(st)}] Starting ${st} (${p}, model=${m})...`;
							logLine(msg);
						}
					} else if (
						eventType === "STAGE_COMPLETED" ||
						eventType === "STAGE_FAILED"
					) {
						if (streamEnabled) {
							const st_s = String(payload.status ?? "");
							const sm = String(payload.summary ?? "");
							const dur = Number(payload.duration_ms ?? 0);
							const msg = `[Wave ${w}][${formatShortStage(st)}] ${st} (${p}, model=${m}) -> ${st_s}: ${sm} (${dur}ms)`;
							logLine(msg);
						}
					} else if (eventType === "TOKEN_DELTA") {
						if (tokensEnabled) {
							const tok = String(payload.delta || payload.delta_text || "");
							if (tok) {
								logToken(tok);
							}
						}
					}
				} catch {
					// Ignore non-JSON comments or keepalive frames
				}
			}
		}
	} finally {
		reader.releaseLock();
	}

	return terminalResult;
}

/**
 * Follow Ontology Studio background job SSE events (`job_started`, `progress_update`,
 * `job_completed`, `job_cancelled`, `job_failed`).
 *
 * Strictly requires an explicit terminal event before returning `COMPLETED`; premature
 * stream EOF throws `StudioSsePrematureEofError` so callers never mask disconnects as success.
 */
export async function followStudioJobEvents(
	options: SseFollowerOptions,
): Promise<TerminalRunSummary> {
	const fetchFn = options.fetchImpl ?? globalThis.fetch.bind(globalThis);
	const reqHeaders: Record<string, string> = {
		Accept: "text/event-stream",
		...options.headers,
	};
	if (options.lastEventId) {
		reqHeaders["Last-Event-ID"] = options.lastEventId;
	}

	const res = await fetchFn(options.streamUrl, {
		headers: reqHeaders,
	});

	if (!res.ok || !res.body) {
		throw new CliDomainError(
			res.status === 404 ? "JOB_NOT_FOUND" : "API_UNREACHABLE",
			`Failed to establish Studio SSE connection (${options.streamUrl}): HTTP ${res.status}`,
			res.status,
		);
	}

	const reader = res.body.getReader();
	const decoder = new TextDecoder();
	const sseParser = new SseStreamParser();
	const streamEnabled = options.streamEnabled ?? true;
	const logLine = options.writer ?? ((line: string) => console.error(line));
	let lastEventId: string | undefined = options.lastEventId;

	const processFrames = (
		frames: Array<{
			id?: string | undefined;
			event?: string | undefined;
			data: string;
		}>,
	): TerminalRunSummary | null => {
		for (const frame of frames) {
			if (frame.id) {
				lastEventId = frame.id;
			}
			const rawEvent = (frame.event || "message").trim();
			let parsed: Record<string, unknown> = {};
			if (frame.data && frame.data.trim() !== "") {
				try {
					const obj = JSON.parse(frame.data);
					if (typeof obj === "object" && obj !== null && !Array.isArray(obj)) {
						parsed = obj as Record<string, unknown>;
					}
				} catch {
					// Ignore keepalive or non-JSON comment payloads
				}
			}

			const evLower = String(parsed.event_type || rawEvent).toLowerCase();

			if (evLower === "job_failed" || evLower === "error") {
				const errObj =
					typeof parsed.error === "object" && parsed.error !== null
						? (parsed.error as Record<string, unknown>)
						: parsed;
				const errCode = String(
					errObj.code ||
						errObj.error_code ||
						parsed.error_code ||
						"INTERNAL_ERROR",
				);
				const errDetail = String(
					errObj.detail ||
						errObj.message ||
						parsed.detail ||
						parsed.message ||
						"Studio background job failed.",
				);
				const errStatus =
					typeof errObj.status === "number"
						? errObj.status
						: typeof parsed.http_status === "number"
							? parsed.http_status
							: undefined;
				throw new CliDomainError(errCode, errDetail, errStatus);
			}

			if (evLower === "job_cancelled") {
				const errObj =
					typeof parsed.error === "object" && parsed.error !== null
						? (parsed.error as Record<string, unknown>)
						: parsed;
				const errCode = String(
					errObj.code || errObj.error_code || "JOB_ALREADY_TERMINAL",
				);
				const errDetail = String(
					errObj.detail ||
						errObj.message ||
						parsed.reason ||
						"Studio background job was cancelled.",
				);
				throw new CliDomainError(errCode, errDetail, 409);
			}

			if (evLower === "job_completed") {
				if (streamEnabled) {
					const stage = String(parsed.stage || "completed");
					logLine(`[Studio Job] Completed (${stage})`);
				}
				return {
					status: "COMPLETED",
					...parsed,
					...(lastEventId ? { last_event_id: lastEventId } : {}),
				};
			}

			if (
				streamEnabled &&
				(evLower === "job_started" ||
					evLower === "progress_update" ||
					evLower === "chunk_parsed" ||
					evLower === "clarification_generated" ||
					evLower === "fibo_grounding" ||
					evLower === "noop_construct" ||
					evLower === "version_committed")
			) {
				const stage = parsed.stage ? ` [${parsed.stage}]` : "";
				const pct =
					typeof parsed.pct_complete === "number"
						? ` ${parsed.pct_complete}%`
						: "";
				const detail = parsed.message || parsed.summary || "";
				const suffix = detail ? `: ${detail}` : "";
				logLine(`[Studio Job] ${evLower}${stage}${pct}${suffix}`);
			}
		}
		return null;
	};

	try {
		while (true) {
			const { done, value } = await reader.read();
			if (done) {
				const trailing = decoder.decode();
				const flushed = sseParser.feed(`${trailing}\n\n`);
				const term = processFrames(flushed);
				if (term) {
					return term;
				}
				break;
			}

			const chunk = decoder.decode(value, { stream: true });
			const frames = sseParser.feed(chunk);
			const term = processFrames(frames);
			if (term) {
				return term;
			}
		}
	} finally {
		reader.releaseLock();
	}

	throw new StudioSsePrematureEofError(
		`SSE stream closed before terminal Studio job event (${options.streamUrl})`,
		lastEventId,
	);
}
