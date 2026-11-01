import { describe, expect, it } from "bun:test";
import { SseStreamParser, parseSseFrame, parseSseStream } from "../src/index";

describe("sse-parser (W3C Server-Sent Events parser)", () => {
	it("parses id:, event:, data:, retry:, and : comment lines in a single frame", () => {
		const raw = [
			": keep-alive heartbeat",
			":second comment without leading space",
			"id: evt-pe-001",
			"event: message",
			"retry: 1500",
			'data: {"hello":"world",',
			'data: "line":2}',
		].join("\n");

		const parsed = parseSseFrame(raw);
		expect(parsed).not.toBeNull();
		expect(parsed?.id).toBe("evt-pe-001");
		expect(parsed?.event).toBe("message");
		expect(parsed?.retry).toBe(1500);
		expect(parsed?.data).toBe('{"hello":"world",\n"line":2}');
		expect(parsed?.comments).toEqual([
			"keep-alive heartbeat",
			"second comment without leading space",
		]);
	});

	it("returns null on comment-only frames by default and supports includeControlFrames", () => {
		const commentOnly = ": heartbeat ping\nretry: 2500";
		expect(parseSseFrame(commentOnly)).toBeNull();

		const control = parseSseFrame(commentOnly, { includeControlFrames: true });
		expect(control).toEqual({
			event: "message",
			data: "",
			retry: 2500,
			comments: ["heartbeat ping"],
		});
	});

	it("ignores invalid non-digit retry values and NULL characters in id", () => {
		const frame = parseSseFrame("id: bad\0id\nretry: 500ms\ndata: ok");
		expect(frame).toEqual({
			event: "message",
			data: "ok",
		});
	});

	it("parses multi-frame SSE streams and incremental chunked streams via SseStreamParser", () => {
		const streamText = [
			"id: evt-pe-001",
			'data: {"event_id":"evt-pe-001","status":"PASS"}',
			"",
			": comment frame between events",
			"",
			"event: run_complete",
			'data: {"status":"COMPLETED"}',
			"",
		].join("\r\n");

		const frames = parseSseStream(streamText);
		expect(frames).toHaveLength(2);
		expect(frames[0]?.id).toBe("evt-pe-001");
		expect(frames[0]?.event).toBe("message");
		expect(frames[1]?.event).toBe("run_complete");
		expect(frames[1]?.data).toBe('{"status":"COMPLETED"}');

		// Incremental chunked parser across arbitrary split points
		const parser = new SseStreamParser();
		const chunk1 = "retry: 750\nid: evt-pe-0";
		const chunk2 = '01\ndata: {"event_id":"evt-pe-001"}\n\nevent: run_comp';
		const chunk3 = 'lete\ndata: {"status":"COMPLETED"}\n\n';

		expect(parser.feed(chunk1)).toEqual([]);
		const batch1 = parser.feed(chunk2);
		expect(batch1).toHaveLength(1);
		expect(batch1[0]?.id).toBe("evt-pe-001");
		expect(parser.getLastEventId()).toBe("evt-pe-001");
		expect(parser.getRetryMs()).toBe(750);

		const batch2 = parser.feed(chunk3);
		expect(batch2).toHaveLength(1);
		expect(batch2[0]?.event).toBe("run_complete");

		parser.reset();
		expect(parser.getLastEventId()).toBeUndefined();
		expect(parser.getRetryMs()).toBeUndefined();

		expect(parser.feed("event: run_complete\r")).toEqual([]);
		expect(parser.feed("")).toEqual([]);
		const splitCrlfBatch = parser.feed(
			'\ndata: {"status":"COMPLETED"}\r\n\r\n',
		);
		expect(splitCrlfBatch).toEqual([
			{
				event: "run_complete",
				data: '{"status":"COMPLETED"}',
			},
		]);
	});
});
