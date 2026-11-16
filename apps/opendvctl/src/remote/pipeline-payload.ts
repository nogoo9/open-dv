/**
 * Payload builder for single-run data-driven pipelines.
 *
 * Decomposed from pipeline.ts to comply with ADR-020 (< 5,000 tokens per module).
 */

import { createHash } from "node:crypto";
import { existsSync, readFileSync } from "node:fs";
import path from "node:path";
import { dumpsJcs } from "../jcs";
import { isClientUploadableFile, stageCasBlob } from "./cas";
import type { RemoteHttpClient } from "./http-client";

export async function buildSingleRunPayload(
	client: RemoteHttpClient,
	options: Record<string, unknown>,
): Promise<Record<string, unknown>> {
	const body: Record<string, unknown> = {
		target_dialect: String(options.dialect || "duckdb"),
	};

	if (options.blueprint_content) {
		body.blueprint_content = String(options.blueprint_content);
	} else if (options.blueprint) {
		const bpPath = String(options.blueprint);
		if (existsSync(bpPath)) {
			try {
				body.blueprint_content = readFileSync(bpPath, "utf-8");
			} catch {
				// ignore
			}
		}
	}

	let parsedOntology: Record<string, unknown> | null = null;
	if (options.ontology_content) {
		const rawContent = String(options.ontology_content);
		body.ontology_source = { raw_yaml: rawContent };
		const yamlApi = (
			Bun as typeof Bun & { YAML?: { parse: (s: string) => unknown } }
		).YAML;
		try {
			parsedOntology = (
				yamlApi ? yamlApi.parse(rawContent) : JSON.parse(rawContent)
			) as Record<string, unknown>;
		} catch {
			// ignore
		}
	} else if (options.ontology) {
		const ontPath = String(options.ontology);
		if (existsSync(ontPath)) {
			try {
				const content = readFileSync(ontPath, "utf-8");
				const yamlApi = (
					Bun as typeof Bun & { YAML?: { parse: (s: string) => unknown } }
				).YAML;
				try {
					parsedOntology = (
						yamlApi ? yamlApi.parse(content) : JSON.parse(content)
					) as Record<string, unknown>;
				} catch {
					try {
						parsedOntology = JSON.parse(content);
					} catch {
						// raw yaml
					}
				}
				body.ontology_source = parsedOntology ?? {
					raw_yaml: content,
				};
			} catch {
				// ignore
			}
		}
	}

	if (
		!body.blueprint &&
		parsedOntology &&
		typeof parsedOntology === "object"
	) {
		const domainNs = (parsedOntology.domain_namespace as string) || "general";
		const ontologySha = createHash("sha256")
			.update(dumpsJcs(parsedOntology), "utf-8")
			.digest("hex");
		body.blueprint = {
			schema_version: "1.0.0",
			domain_namespace: domainNs,
			lineage: {
				schema_version: "1.0.0",
				blueprint_revision: 1,
				domain_namespace: domainNs,
				ontology_sha256: ontologySha,
				contract_sha256: null,
				parent_blueprint_sha256: null,
			},
			ontology: parsedOntology,
			contract: null,
		};
	}

	if (options.contract_content || options.initial_contract_content) {
		body.initial_contract_content = String(
			options.contract_content || options.initial_contract_content,
		);
	} else if (options.contract) {
		const contractPath = String(options.contract);
		if (existsSync(contractPath)) {
			try {
				body.initial_contract_content = readFileSync(contractPath, "utf-8");
			} catch {
				// ignore
			}
		}
	}

	const sourcesList: Array<Record<string, unknown>> = [];
	const sourceOpt = options.source ?? options.sources;
	if (sourceOpt) {
		const rawSources = Array.isArray(sourceOpt)
			? sourceOpt
			: [sourceOpt];
		const flatSources: string[] = [];
		for (const s of rawSources) {
			if (Array.isArray(s)) {
				flatSources.push(...s.map(String));
			} else if (s !== undefined && s !== null) {
				flatSources.push(String(s));
			}
		}
		for (let idx = 0; idx < flatSources.length; idx++) {
			const sStr = flatSources[idx]!;
			const stem =
				path
					.basename(sStr.split("://").pop() || "")
					.replace(/\.[^/.]+$/, "") || `source_${idx + 1}`;
			let effUri = sStr;
			let casHash: string | undefined;

			let origAdapter = options.adapter ? String(options.adapter) : undefined;
			// Infer detected format strictly from file extension, ignoring CLI global --format
			const lowerSource = sStr.toLowerCase();
			const pathPart = lowerSource.split(/[?#]/)[0] ?? "";
			let detectedFormat: string | undefined;

			if (/\.csv$/i.test(pathPart)) {
				detectedFormat = "csv";
				if (!origAdapter) origAdapter = "filesystem";
			} else if (/\.tsv$/i.test(pathPart)) {
				detectedFormat = "tsv";
				if (!origAdapter) origAdapter = "filesystem";
			} else if (/\.(parquet|pq)$/i.test(pathPart)) {
				detectedFormat = "parquet";
				if (!origAdapter) origAdapter = "filesystem";
			} else if (/\.jsonl$/i.test(pathPart)) {
				detectedFormat = "jsonl";
				if (!origAdapter) origAdapter = "filesystem";
			} else if (/\.json$/i.test(pathPart)) {
				detectedFormat = "json";
				if (!origAdapter) origAdapter = "filesystem";
			} else if (
				lowerSource.startsWith("sqlite:") ||
				/\.(sqlite|sqlite3|db)$/i.test(pathPart)
			) {
				detectedFormat = "sqlite";
				if (!origAdapter) origAdapter = "sqlite";
			} else if (
				lowerSource.startsWith("duckdb:") ||
				/\.(duckdb|ddb)$/i.test(pathPart)
			) {
				detectedFormat = "duckdb";
				if (!origAdapter) origAdapter = "duckdb";
			}

			if (isClientUploadableFile(sStr)) {
				casHash = await stageCasBlob(client, sStr);
				if (casHash) {
					const baseName = path.basename(sStr);
					effUri = baseName ? `cas://${casHash}/${baseName}` : `cas://${casHash}`;
				}
			}
			sourcesList.push({
				feed_id: `feed-${idx + 1}`,
				source_system: stem,
				uri: effUri,
				target_dialect: String(options.dialect || "duckdb"),
				...(origAdapter ? { adapter: origAdapter } : {}),
				...(detectedFormat || casHash
					? {
							options: {
								...(detectedFormat
									? { format: detectedFormat, source_format: detectedFormat }
									: {}),
								...(casHash ? { cas_hash: casHash } : {}),
							},
						}
					: {}),
			});
		}
	}
	body.sources = sourcesList;

	if (options.provider) {
		body.harness_provider = String(options.provider);
	}
	const holdPolicy = options.hold_policy || options.steward_hold_policy;
	if (holdPolicy) {
		const gov = (body.governance_policy as Record<string, unknown>) || {};
		gov.steward_hold_policy = String(holdPolicy);
		body.governance_policy = gov;
	} else if (
		options.governance_policy &&
		typeof options.governance_policy === "object"
	) {
		body.governance_policy = options.governance_policy as Record<
			string,
			unknown
		>;
	}

	return body;
}
