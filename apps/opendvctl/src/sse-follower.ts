/**
 * D2 Live SSE Event Follower & Stream Formatter.
 *
 * Consumes real-time W3C Server-Sent Events from `events_sse_url` and renders
 * live stage transitions and token streams matching standalone `opendv` format:
 * `[Wave {w}][STAGE {s}] Starting {stage_id} ({persona}, model={model})...`
 * `[Wave {w}][STAGE {s}] {stage_id} ({persona}, model={model}) -> {status}: {summary} ({duration}ms)`
 */

import { SseStreamParser } from "@opendv/protocol-ts";

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

export async function followEventStream(
	options: SseFollowerOptions,
): Promise<TerminalRunSummary> {
	const fetchFn = options.fetchImpl ?? globalThis.fetch.bind(globalThis);
	const res = await fetchFn(options.streamUrl, {
		headers: {
			Accept: "text/event-stream",
			...options.headers,
		},
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
