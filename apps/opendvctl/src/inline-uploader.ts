/**
 * D1 Inline File Content Uploader.
 *
 * For options flagged `inline_upload: true` in `cli-spec.json`:
 * Reads file contents from disk using Bun's native file API and injects both
 * the source path and the inline UTF-8 payload into request DTO bodies before
 * dispatching HTTP POST requests.
 */

import type { CliOption } from "./spec";

export interface InlineUploadFile {
	path: string;
	content: string;
}

export async function readInlineFile(
	filePath: string,
): Promise<InlineUploadFile> {
	const file = Bun.file(filePath);
	const exists = await file.exists();
	if (!exists) {
		throw new Error(`File not found: ${filePath}`);
	}
	const content = await file.text();
	return { path: filePath, content };
}

export async function processInlineUploads(
	options: Record<string, unknown>,
	specOptions: CliOption[],
): Promise<Record<string, unknown>> {
	const enriched: Record<string, unknown> = { ...options };

	for (const opt of specOptions) {
		if (!opt.inline_upload) {
			continue;
		}

		const key = opt.name.replace(/-/g, "_");
		const rawVal = enriched[key];

		if (typeof rawVal === "string" && rawVal.trim().length > 0) {
			const filePath = rawVal.trim();
			const file = Bun.file(filePath);
			if (await file.exists()) {
				const content = await file.text();
				enriched[`${key}_content`] = content;
				enriched[`${key}_path`] = filePath;

				// Target DTO field mappings per schemas/openapi-v1.json & Decision D1
				if (key === "contract") {
					enriched.contract_content = content;
					enriched.contract_path = filePath;
				} else if (key === "input") {
					enriched.input_spec_content = content;
					enriched.input_spec_path = filePath;
				} else if (
					key === "initial_contract" ||
					key === "initial_contract_path"
				) {
					enriched.initial_contract_content = content;
					enriched.initial_contract = content;
					enriched.initial_contract_path = filePath;
				} else if (key === "file" || key === "source_csv") {
					enriched.source_csv_content = content;
					enriched.file_content = content;
					enriched.source_csv_path = filePath;
					enriched.client_path = filePath;
				} else if (key === "canonical_ir") {
					enriched.canonical_ir_content = content;
					enriched.canonical_ir_path = filePath;
				} else if (key === "graph_a") {
					enriched.graph_a_content = content;
					enriched.graph_a_path = filePath;
				} else if (key === "graph_b") {
					enriched.graph_b_content = content;
					enriched.graph_b_path = filePath;
				} else if (key === "prior_contract") {
					enriched.prior_contract_content = content;
					enriched.prior_contract_path = filePath;
				} else if (key === "compiled_sql") {
					enriched.compiled_sql = content;
				} else if (key === "base") {
					enriched.base_ontology_content = content;
					enriched.base_ontology_path = filePath;
				} else if (key === "delta") {
					enriched.delta_content = content;
					enriched.delta_path = filePath;
				} else if (key === "target") {
					enriched.target_ontology_content = content;
					enriched.target_ontology_path = filePath;
				} else if (key === "profile") {
					enriched.empirical_profile_content = content;
					enriched.empirical_profile_path = filePath;
				} else if (key === "source") {
					enriched.bronze_source_content = content;
					enriched.bronze_source_path = filePath;
				}
			}
		}
	}

	return enriched;
}
