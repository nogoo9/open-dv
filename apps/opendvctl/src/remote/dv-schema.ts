/**
 * Remote handler for `opendvctl run dv-schema` (D13 / Phase 2 Track A Explorer).
 */

import { CliDomainError } from "../error-handler";
import type { RemoteHttpClient } from "./http-client";

export async function handleDvSchema(
	client: RemoteHttpClient,
	runId: string | undefined,
	enrichedOptions: Record<string, unknown>,
): Promise<unknown> {
	if (!runId) {
		throw new CliDomainError(
			"SYNTAX_INVALID_ARGUMENT",
			"Missing required --run-id flag for 'run dv-schema'",
			400,
		);
	}
	const encRun = encodeURIComponent(runId);
	const sql = enrichedOptions.sql as string | undefined;
	const table = enrichedOptions.table as string | undefined;
	const compare = enrichedOptions.compare as string | undefined;
	const limit = enrichedOptions.limit;
	const offset = enrichedOptions.offset;

	if (sql) {
		const body: Record<string, unknown> = { sql };
		if (limit !== undefined) {
			body.max_rows = Number(limit);
		}
		return client.postJson(`/api/v1/runs/${encRun}/dv-schema/query`, body);
	}

	if (table) {
		const params = new URLSearchParams();
		if (limit !== undefined) params.set("limit", String(limit));
		if (offset !== undefined) params.set("offset", String(offset));
		const qs = params.toString() ? `?${params.toString()}` : "";
		return client.getJson(
			`/api/v1/runs/${encRun}/dv-schema/tables/${encodeURIComponent(table)}/rows${qs}`,
		);
	}

	const params = new URLSearchParams();
	if (compare) params.set("compare", compare);
	const qs = params.toString() ? `?${params.toString()}` : "";
	return client.getJson(`/api/v1/runs/${encRun}/dv-schema${qs}`);
}
