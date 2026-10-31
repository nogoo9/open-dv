/**
 * Resilient SSE Client (`VaultEventStreamClient`) with `Last-Event-ID` / `last_event_id` replay,
 * `seenEventIds` deduplication, jittered exponential backoff reconnection, and `run_complete` handling.
 */

import type { VaultObservabilityEventDTO } from "../types/observability-event";
import { SseStreamParser } from "../utils/sse-parser";

export interface EventSourceLike {
	addEventListener(
		type: string,
		listener: (ev: MessageEvent<string>) => void,
	): void;
	close(): void;
	onerror: ((err: unknown) => void) | null;
}

export interface EventSourceFactoryInit {
	headers?: Record<string, string> | undefined;
}

export type EventSourceFactory = (
	url: string,
	init?: EventSourceFactoryInit,
) => EventSourceLike;

export interface VaultEventStreamOptions {
	streamUrl: string;
	lastEventId?: string | undefined;
	reconnectBaseDelayMs?: number | undefined;
	maxReconnectDelayMs?: number | undefined;
	onEvent: (event: VaultObservabilityEventDTO) => void;
	onRunComplete?:
		| ((summary: { status: string; [key: string]: unknown }) => void)
		| undefined;
	onError?: ((error: unknown) => void) | undefined;
	eventSourceFactory?: EventSourceFactory | undefined;
	randomFn?: (() => number) | undefined;
}

export class VaultEventStreamClient {
	private readonly options: VaultEventStreamOptions;
	private readonly seenEventIds = new Set<string>();
	private readonly sseParser = new SseStreamParser();
	private eventSource: EventSourceLike | null = null;
	private lastEventId: string | undefined;
	private reconnectAttempts = 0;
	private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
	private closedByCaller = false;

	constructor(options: VaultEventStreamOptions) {
		this.options = options;
		this.lastEventId = options.lastEventId;
		if (options.lastEventId) {
			this.seenEventIds.add(options.lastEventId);
		}
	}

	getLastEventId(): string | undefined {
		return this.lastEventId;
	}

	getSeenEventIds(): ReadonlySet<string> {
		return this.seenEventIds;
	}

	getReconnectAttempts(): number {
		return this.reconnectAttempts;
	}

	isConnected(): boolean {
		return this.eventSource !== null && !this.closedByCaller;
	}

	connect(): void {
		this.closedByCaller = false;
		const baseOrigin =
			typeof globalThis.location !== "undefined" && globalThis.location?.origin
				? globalThis.location.origin
				: "http://localhost";
		const url = new URL(this.options.streamUrl, baseOrigin);
		const headers: Record<string, string> = {};
		if (this.lastEventId) {
			url.searchParams.set("last_event_id", this.lastEventId);
			headers["Last-Event-ID"] = this.lastEventId;
		}

		const factory: EventSourceFactory =
			this.options.eventSourceFactory ??
			((targetUrl: string) =>
				new EventSource(targetUrl) as unknown as EventSourceLike);

		const es = factory(url.toString(), { headers });
		this.eventSource = es;

		es.addEventListener("message", (ev: MessageEvent<string>) => {
			this.handleMessagePayload(ev.data, ev.lastEventId);
		});

		es.addEventListener("run_complete", (ev: MessageEvent<string>) => {
			this.handleRunCompletePayload(ev.data);
		});

		es.onerror = (err: unknown) => {
			this.options.onError?.(err);
			es.close();
			this.eventSource = null;
			if (!this.closedByCaller) {
				this.scheduleReconnect();
			}
		};
	}

	/**
	 * Feed raw W3C SSE wire frames directly (useful for `fetch` ReadableStream consumers or unit tests).
	 */
	ingestSseChunk(rawChunk: string): void {
		const frames = this.sseParser.feed(rawChunk);
		for (const frame of frames) {
			if (frame.event === "run_complete") {
				this.handleRunCompletePayload(frame.data);
			} else if (frame.event === "message") {
				this.handleMessagePayload(frame.data, frame.id);
			}
		}
	}

	private handleMessagePayload(rawJson: string, wireEventId?: string): void {
		this.reconnectAttempts = 0;
		const parsed = JSON.parse(rawJson) as VaultObservabilityEventDTO;
		const eventId = parsed.event_id || wireEventId;
		if (eventId) {
			this.lastEventId = eventId;
			if (this.seenEventIds.has(eventId)) {
				return;
			}
			this.seenEventIds.add(eventId);
		}
		this.options.onEvent(parsed);
	}

	private handleRunCompletePayload(rawJson: string): void {
		this.disconnect();
		const summary = JSON.parse(rawJson) as {
			status: string;
			[key: string]: unknown;
		};
		this.options.onRunComplete?.(summary);
	}

	/**
	 * Compute the next jittered exponential backoff delay in milliseconds (`docs/10` §2.3).
	 */
	computeNextReconnectDelayMs(): number {
		const base = this.options.reconnectBaseDelayMs ?? 500;
		const maxDelay = this.options.maxReconnectDelayMs ?? 10_000;
		const expDelay = Math.min(maxDelay, base * 2 ** this.reconnectAttempts);
		const rand = this.options.randomFn ?? Math.random;
		const jitter = Math.floor(
			rand() * Math.max(1, Math.floor(expDelay * 0.25)),
		);
		return Math.min(maxDelay, expDelay + jitter);
	}

	private scheduleReconnect(): void {
		const delay = this.computeNextReconnectDelayMs();
		this.reconnectAttempts += 1;
		this.reconnectTimer = setTimeout(() => this.connect(), delay);
	}

	disconnect(): void {
		this.closedByCaller = true;
		if (this.reconnectTimer !== null) {
			clearTimeout(this.reconnectTimer);
			this.reconnectTimer = null;
		}
		this.eventSource?.close();
		this.eventSource = null;
	}
}
