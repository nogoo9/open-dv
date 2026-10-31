/**
 * Offline `.jsonl` Replay Engine (`MockVaultEventStreamClient`) supporting
 * `loadFromJSONL`, `getEvents`, `getCursor`, `setSpeed`, `seek`, `step`, and `replay`.
 */

import type { VaultObservabilityEventDTO } from "../types/observability-event";

export interface OfflineReplayOptions {
	stepDelayMs?: number | undefined;
	speedMultiplier?: number | undefined;
	signal?: AbortSignal | undefined;
}

export class MockVaultEventStreamClient {
	private readonly events: readonly VaultObservabilityEventDTO[];
	private cursor = 0;
	private speedMultiplier = 1;

	constructor(events: readonly VaultObservabilityEventDTO[]) {
		this.events = events;
	}

	static loadFromJSONL(jsonlText: string): MockVaultEventStreamClient {
		const events: VaultObservabilityEventDTO[] = [];
		for (const rawLine of jsonlText.split(/\r?\n/)) {
			const trimmed = rawLine.trim();
			if (!trimmed) {
				continue;
			}
			events.push(JSON.parse(trimmed) as VaultObservabilityEventDTO);
		}
		return new MockVaultEventStreamClient(events);
	}

	toJSONL(): string {
		return this.events.map((ev) => JSON.stringify(ev)).join("\n");
	}

	getEvents(): readonly VaultObservabilityEventDTO[] {
		return this.events;
	}

	getCursor(): number {
		return this.cursor;
	}

	getSpeed(): number {
		return this.speedMultiplier;
	}

	setSpeed(speedMultiplier: number): void {
		if (!Number.isFinite(speedMultiplier) || speedMultiplier <= 0) {
			throw new RangeError("speedMultiplier must be > 0");
		}
		this.speedMultiplier = speedMultiplier;
	}

	reset(): void {
		this.cursor = 0;
	}

	seek(index: number): VaultObservabilityEventDTO | null {
		if (index < 0 || index > this.events.length) {
			throw new RangeError(
				`Replay cursor index ${index} out of bounds [0, ${this.events.length}]`,
			);
		}
		this.cursor = index;
		return index < this.events.length ? (this.events[index] ?? null) : null;
	}

	step(
		onEvent?: (event: VaultObservabilityEventDTO, index: number) => void,
	): VaultObservabilityEventDTO | null {
		if (this.cursor >= this.events.length) {
			return null;
		}
		const index = this.cursor;
		const event = this.events[index];
		if (!event) {
			return null;
		}
		this.cursor += 1;
		onEvent?.(event, index);
		return event;
	}

	async replay(
		onEvent: (event: VaultObservabilityEventDTO, index: number) => void,
		options: OfflineReplayOptions = {},
	): Promise<number> {
		const speed = options.speedMultiplier ?? this.speedMultiplier;
		const baseDelayMs = options.stepDelayMs ?? 0;
		const effectiveDelayMs =
			speed > 0 ? Math.max(0, Math.floor(baseDelayMs / speed)) : 0;
		let emitted = 0;
		while (this.cursor < this.events.length) {
			if (options.signal?.aborted) {
				break;
			}
			if (effectiveDelayMs > 0) {
				await new Promise((resolve) => setTimeout(resolve, effectiveDelayMs));
				if (options.signal?.aborted) {
					break;
				}
			}
			this.step(onEvent);
			emitted += 1;
		}
		return emitted;
	}
}
