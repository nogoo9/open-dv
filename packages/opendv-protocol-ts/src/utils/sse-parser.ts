/**
 * Pure TypeScript W3C Server-Sent Events (SSE) Frame & Stream Parser
 * supporting `id:`, `event:`, `data:`, `retry:`, and `:` comment lines (`docs/08` §5.3 & `docs/10` §2).
 */

export interface ParsedSseFrame {
	id?: string | undefined;
	event: string;
	data: string;
	retry?: number | undefined;
	comments?: string[] | undefined;
}

export interface ParseSseOptions {
	/**
	 * When true, frames that only set `id:`, `retry:`, or `:` comments without `data:`
	 * are also returned instead of being skipped. Defaults to `false` per W3C SSE dispatch semantics.
	 */
	includeControlFrames?: boolean | undefined;
}

function stripSingleLeadingSpace(value: string): string {
	return value.startsWith(" ") ? value.slice(1) : value;
}

/**
 * Parse a single W3C SSE frame block (lines separated by `\n`, `\r\n`, or `\r`).
 * Returns `null` if the block contains no `data:` lines (unless `includeControlFrames: true`).
 */
export function parseSseFrame(
	rawBlock: string,
	options: ParseSseOptions = {},
): ParsedSseFrame | null {
	const lines = rawBlock.split(/\r\n|\r|\n/);
	let id: string | undefined;
	let eventType = "message";
	const dataLines: string[] = [];
	let retry: number | undefined;
	const comments: string[] = [];

	for (const line of lines) {
		if (line.length === 0) {
			continue;
		}
		if (line.startsWith(":")) {
			comments.push(stripSingleLeadingSpace(line.slice(1)));
			continue;
		}

		const colonIndex = line.indexOf(":");
		let field: string;
		let rawValue: string;
		if (colonIndex === -1) {
			field = line;
			rawValue = "";
		} else {
			field = line.slice(0, colonIndex);
			rawValue = stripSingleLeadingSpace(line.slice(colonIndex + 1));
		}

		switch (field) {
			case "id":
				// W3C SSE spec: ignore `id` field if value contains NULL character.
				if (!rawValue.includes("\0")) {
					id = rawValue;
				}
				break;
			case "event":
				if (rawValue.length > 0) {
					eventType = rawValue;
				}
				break;
			case "data":
				dataLines.push(rawValue);
				break;
			case "retry":
				if (/^\d+$/.test(rawValue)) {
					retry = Number.parseInt(rawValue, 10);
				}
				break;
			default:
				break;
		}
	}

	const hasData = dataLines.length > 0;
	const hasControl =
		id !== undefined || retry !== undefined || comments.length > 0;

	if (!hasData && !(options.includeControlFrames && hasControl)) {
		return null;
	}

	const frame: ParsedSseFrame = {
		event: eventType,
		data: dataLines.join("\n"),
	};
	if (id !== undefined) {
		frame.id = id;
	}
	if (retry !== undefined) {
		frame.retry = retry;
	}
	if (comments.length > 0) {
		frame.comments = comments;
	}
	return frame;
}

/**
 * Parse a complete or multi-frame SSE payload string into an array of `ParsedSseFrame` objects.
 */
export function parseSseStream(
	rawStreamText: string,
	options: ParseSseOptions = {},
): ParsedSseFrame[] {
	if (!rawStreamText) {
		return [];
	}
	const normalized = rawStreamText.replace(/\r\n/g, "\n").replace(/\r/g, "\n");
	const rawBlocks = normalized.split(/\n\n+/);
	const frames: ParsedSseFrame[] = [];
	for (const block of rawBlocks) {
		if (block.trim().length === 0) {
			continue;
		}
		const parsed = parseSseFrame(block, options);
		if (parsed !== null) {
			frames.push(parsed);
		}
	}
	return frames;
}

/**
 * Incremental stateful W3C SSE stream parser that buffers arbitrary chunk boundaries
 * and tracks `lastEventId` and `retryMs`.
 */
export class SseStreamParser {
	private buffer = "";
	private trailingCr = false;
	private lastEventId: string | undefined;
	private retryMs: number | undefined;
	private readonly options: ParseSseOptions;

	constructor(options: ParseSseOptions = {}) {
		this.options = options;
	}

	feed(chunk: string): ParsedSseFrame[] {
		if (chunk.length === 0) {
			return [];
		}
		let effectiveChunk = chunk;
		if (this.trailingCr && effectiveChunk.startsWith("\n")) {
			effectiveChunk = effectiveChunk.slice(1);
		}
		this.trailingCr = effectiveChunk.endsWith("\r");
		this.buffer += effectiveChunk.replace(/\r\n/g, "\n").replace(/\r/g, "\n");
		const frames: ParsedSseFrame[] = [];

		let separatorIndex = this.buffer.indexOf("\n\n");
		while (separatorIndex !== -1) {
			const rawBlock = this.buffer.slice(0, separatorIndex);
			this.buffer = this.buffer.slice(separatorIndex + 2);

			// Update persistent lastEventId/retryMs even if block has no data lines
			const controlFrame = parseSseFrame(rawBlock, {
				includeControlFrames: true,
			});
			if (controlFrame) {
				if (controlFrame.id !== undefined) {
					this.lastEventId = controlFrame.id;
				}
				if (controlFrame.retry !== undefined) {
					this.retryMs = controlFrame.retry;
				}
			}

			const dispatched = parseSseFrame(rawBlock, this.options);
			if (dispatched !== null) {
				if (dispatched.id === undefined && this.lastEventId !== undefined) {
					dispatched.id = this.lastEventId;
				}
				frames.push(dispatched);
			}

			separatorIndex = this.buffer.indexOf("\n\n");
		}

		return frames;
	}

	getLastEventId(): string | undefined {
		return this.lastEventId;
	}

	getRetryMs(): number | undefined {
		return this.retryMs;
	}

	reset(): void {
		this.buffer = "";
		this.trailingCr = false;
		this.lastEventId = undefined;
		this.retryMs = undefined;
	}
}
