import { ApiProblemError, apiRequest } from "./client";
import type * as T from "./openapi";
import {
	API_ROUTES as R,
	buildApiRoute as br,
	isRunHaltStatus,
} from "./openapi";

export interface ParsedSseFrame {
	id?: string | undefined;
	event: string;
	data: string;
	retry?: number | undefined;
}

export function parseSseFrame(raw: string): ParsedSseFrame | null {
	let id: string | undefined;
	let event = "message";
	const data: string[] = [];
	let retry: number | undefined;
	for (const line of raw.split(/\r\n|\r|\n/)) {
		if (!line || line[0] === ":") continue;
		const c = line.indexOf(":");
		const f = c === -1 ? line : line.slice(0, c);
		const v = c === -1 ? "" : line.slice(line[c + 1] === " " ? c + 2 : c + 1);
		if (f === "id" && !v.includes("\0")) id = v;
		else if (f === "event" && v) event = v;
		else if (f === "data") data.push(v);
		else if (f === "retry" && /^\d+$/.test(v)) retry = Number.parseInt(v, 10);
	}
	return data.length ? { event, data: data.join("\n"), id, retry } : null;
}

export class SseStreamParser {
	private buf = "";
	private cr = false;
	private lastId: string | undefined;
	feed(chunk: string): ParsedSseFrame[] {
		if (!chunk) return [];
		this.buf += (this.cr && chunk[0] === "\n" ? chunk.slice(1) : chunk).replace(
			/\r\n|\r/g,
			"\n",
		);
		this.cr = chunk.endsWith("\r");
		const out: ParsedSseFrame[] = [];
		for (
			let i = this.buf.indexOf("\n\n");
			i !== -1;
			i = this.buf.indexOf("\n\n")
		) {
			const f = parseSseFrame(this.buf.slice(0, i));
			this.buf = this.buf.slice(i + 2);
			if (f) {
				if (f.id !== undefined) this.lastId = f.id;
				else if (this.lastId !== undefined) f.id = this.lastId;
				out.push(f);
			}
		}
		return out;
	}
	reset(): void {
		this.buf = "";
		this.cr = false;
		this.lastId = undefined;
	}
}

export async function fetchAllRunLogs(
	pid: string,
	rid: string,
	sinceSeq = 0,
	signal?: AbortSignal,
	fetchImpl?: typeof fetch,
): Promise<T.RunStreamEvent[]> {
	const out: T.RunStreamEvent[] = [];
	const seen = new Set<number>();
	let cur: number | null = sinceSeq;
	const url = br(R.runLogs, { project_id: pid, rid });
	while (cur !== null) {
		const page: T.RunEventLogPage = await apiRequest<T.RunEventLogPage>(url, {
			query: { since_seq: cur },
			signal,
			fetchImpl,
		});
		for (const ev of page.events)
			if (!seen.has(ev.seq)) {
				seen.add(ev.seq);
				out.push(ev);
			}
		const next: number | null = page.next_since_seq ?? null;
		if (!page.events.length && next !== null && next <= cur) break;
		cur = next;
	}
	return out;
}

export interface StreamRunEventsOptions {
	projectId: string;
	runId: string;
	step: "step4" | "step6";
	getLastSeq: () => number;
	signal: AbortSignal;
	onEvents: (events: T.RunStreamEvent[]) => void;
	onReconciledState?: (state: T.RunStateResp) => void;
	onDisconnected?: () => void;
	fetchImpl?: typeof fetch;
}

const BACKOFF = [250, 500, 1000, 2000, 2000] as const;

const sleepSig = (ms: number, sig: AbortSignal): Promise<boolean> =>
	sig.aborted
		? Promise.resolve(false)
		: new Promise((res) => {
				const onA = () => {
					clearTimeout(t);
					res(false);
				};
				const t = setTimeout(() => {
					sig.removeEventListener("abort", onA);
					res(true);
				}, ms);
				sig.addEventListener("abort", onA, { once: true });
			});

export async function streamRunEvents(
	o: StreamRunEventsOptions,
): Promise<void> {
	const fetchFn = o.fetchImpl ?? globalThis.fetch;
	const url = br(o.step === "step4" ? R.ontologyRunEvents : R.runEvents, {
		project_id: o.projectId,
		rid: o.runId,
	});
	const runUrl = br(R.run, { project_id: o.projectId, rid: o.runId });
	let retries = 0;

	while (!o.signal.aborted) {
		let pending: T.RunStreamEvent[] = [];
		let stop: (() => void) | null = null;
		const flush = () => {
			stop?.();
			stop = null;
			if (pending.length) {
				o.onEvents(pending);
				pending = [];
			}
		};
		const sched = () => {
			if (stop) return;
			const run = () => {
				stop = null;
				flush();
			};
			if (typeof requestAnimationFrame === "function") {
				const id = requestAnimationFrame(run);
				stop = () => cancelAnimationFrame(id);
			} else {
				const id = setTimeout(run, 0);
				stop = () => clearTimeout(id);
			}
		};
		o.signal.addEventListener("abort", flush, { once: true });
		let term = false;
		try {
			const res = await fetchFn(url, {
				headers: {
					Accept: "text/event-stream",
					"Last-Event-ID": String(Math.max(0, o.getLastSeq())),
				},
				signal: o.signal,
			});
			if (!res.ok || !res.body) {
				if (res.status >= 400 && res.status < 500) {
					o.onDisconnected?.();
					return;
				}
				throw new Error(`HTTP ${res.status}`);
			}
			const rd = res.body.getReader();
			const dec = new TextDecoder("utf-8");
			const parser = new SseStreamParser();
			while (!o.signal.aborted) {
				const { done, value } = await rd.read();
				const txt = done ? dec.decode() : dec.decode(value, { stream: true });
				for (const f of parser.feed(txt)) {
					try {
						const ev = JSON.parse(f.data) as T.RunStreamEvent;
						pending.push(ev);
						if (
							ev.event_type === "RUN_COMPLETED" ||
							ev.event_type === "RUN_FAILED"
						) {
							term = true;
						}
					} catch {
						/* ignore malformed event payload */
					}
				}
				if (term) {
					flush();
					return;
				}
				if (pending.length) sched();
				if (done) break;
			}
			flush();
		} catch {
			flush();
			if (o.signal.aborted) return;
		} finally {
			o.signal.removeEventListener("abort", flush);
		}
		if (o.signal.aborted || term) return;
		try {
			const st = await apiRequest<T.RunStateResp>(runUrl, {
				signal: o.signal,
				fetchImpl: fetchFn,
			});
			o.onReconciledState?.(st);
			if (isRunHaltStatus(st.status)) return;
		} catch (e) {
			if (o.signal.aborted) return;
			if (e instanceof ApiProblemError && e.status >= 400 && e.status < 500) {
				o.onDisconnected?.();
				return;
			}
		}
		if (retries >= BACKOFF.length) {
			o.onDisconnected?.();
			return;
		}
		if (!(await sleepSig(BACKOFF[retries++] ?? 2000, o.signal))) return;
	}
}
