/**
 * Session Cost Meter Aggregation Utilities (`cost-meter-utils.ts`).
 *
 * Deduplicates per-job token and USD usage from session logs while keeping
 * terminal `agent_telemetry` records authoritative over `agent_stream` events.
 */

export interface SessionCostLogEntry {
	job_id?: string | null | undefined;
	event?: string | undefined;
	token_usage?:
		| {
				total_tokens?: number | undefined;
				cost_usd?: number | undefined;
		  }
		| null
		| undefined;
	data?:
		| {
				job_id?: string | undefined;
				cumulative_cost_usd?: number | undefined;
				cost_usd?: number | undefined;
		  }
		| null
		| undefined;
}

function toPositiveFinite(val: unknown): number | null {
	if (val === null || val === undefined) return null;
	const num = Number(val);
	return Number.isFinite(num) && num > 0 ? num : null;
}

export function aggregateSessionCostFromLogs(
	entries: readonly SessionCostLogEntry[],
): { totalTokens: number; totalUsd: number } {
	const perJob = new Map<
		string,
		{ tokens: number; usd: number; hasTelemetry: boolean }
	>();
	entries.forEach((e, idx) => {
		const rawJobId = e.job_id?.trim() || e.data?.job_id?.trim() || "";
		const key = rawJobId || `__entry_${idx}`;
		const tok = toPositiveFinite(e.token_usage?.total_tokens) ?? 0;
		const usd =
			toPositiveFinite(e.token_usage?.cost_usd) ??
			toPositiveFinite(e.data?.cumulative_cost_usd) ??
			toPositiveFinite(e.data?.cost_usd) ??
			0;
		const isTelemetry = e.event === "agent_telemetry";
		const prev = perJob.get(key);
		if (!prev) {
			perJob.set(key, { tokens: tok, usd, hasTelemetry: isTelemetry });
			return;
		}
		if (prev.hasTelemetry && !isTelemetry) {
			// Terminal agent_telemetry is authoritative over out-of-order stream entries
			return;
		}
		if (isTelemetry && !prev.hasTelemetry) {
			perJob.set(key, {
				tokens: tok > 0 ? tok : prev.tokens,
				usd: usd > 0 ? usd : prev.usd,
				hasTelemetry: true,
			});
			return;
		}
		perJob.set(key, {
			tokens: Math.max(prev.tokens, tok),
			usd: Math.max(prev.usd, usd),
			hasTelemetry: prev.hasTelemetry || isTelemetry,
		});
	});
	let totalTokens = 0;
	let totalUsd = 0;
	for (const val of perJob.values()) {
		totalTokens += val.tokens;
		totalUsd += val.usd;
	}
	return { totalTokens, totalUsd };
}
