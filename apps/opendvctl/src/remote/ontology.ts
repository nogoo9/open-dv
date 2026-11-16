/**
 * Remote execution handlers for ontology subcommands (suggest, validate-delta, diff, accept, reject).
 */

import { CliDomainError } from "../error-handler";
import type { RemoteHttpClient } from "./http-client";

export async function handleOntology(
	client: RemoteHttpClient,
	subcommand: string | undefined,
	enrichedOptions: Record<string, unknown>,
): Promise<unknown> {
	if (!subcommand) {
		throw new CliDomainError(
			"INVALID_REQUEST",
			"Missing ontology subcommand (suggest, validate-delta, diff, accept, reject, studio)",
			400,
		);
	}
	const sub = subcommand.toLowerCase();
	let payload: unknown;

	if (sub === "suggest") {
		const body: Record<string, unknown> = {};
		if (enrichedOptions.base)
			body.base_ontology_path = enrichedOptions.base;
		if (enrichedOptions.base_content)
			body.base_ontology_content = enrichedOptions.base_content;
		if (enrichedOptions.base_ontology_content)
			body.base_ontology_content = enrichedOptions.base_ontology_content;
		if (enrichedOptions.profile)
			body.empirical_profile_path = enrichedOptions.profile;
		if (enrichedOptions.profile_content)
			body.empirical_profile_content = enrichedOptions.profile_content;
		if (enrichedOptions.empirical_profile_content)
			body.empirical_profile_content =
				enrichedOptions.empirical_profile_content;
		if (enrichedOptions.source)
			body.bronze_source_path = enrichedOptions.source;
		if (enrichedOptions.source_content)
			body.bronze_source_content = enrichedOptions.source_content;
		if (enrichedOptions.bronze_source_content)
			body.bronze_source_content = enrichedOptions.bronze_source_content;
		if (enrichedOptions.context_doc)
			body.context_doc_path = enrichedOptions.context_doc;
		if (enrichedOptions.context_doc_content)
			body.context_doc_content = enrichedOptions.context_doc_content;
		if (enrichedOptions.mode) body.mode = enrichedOptions.mode;
		if (enrichedOptions.domain_namespace)
			body.domain_namespace = enrichedOptions.domain_namespace;
		if (enrichedOptions.recommender_id)
			body.recommender_id = enrichedOptions.recommender_id;

		payload = await client.postJson("/api/v1/ontology/suggestions", body);

		if (
			enrichedOptions.output &&
			typeof enrichedOptions.output === "string"
		) {
			const fs = await import("node:fs/promises");
			await fs.writeFile(
				enrichedOptions.output,
				JSON.stringify(payload, null, 2),
				"utf-8",
			);
		}
	} else if (sub === "validate-delta") {
		const body: Record<string, unknown> = {};
		if (enrichedOptions.delta) body.delta_path = enrichedOptions.delta;
		if (enrichedOptions.delta_content)
			body.delta_content = enrichedOptions.delta_content;
		if (enrichedOptions.base)
			body.base_ontology_path = enrichedOptions.base;
		if (enrichedOptions.base_content)
			body.base_ontology_content = enrichedOptions.base_content;
		if (enrichedOptions.base_ontology_content)
			body.base_ontology_content = enrichedOptions.base_ontology_content;
		if (enrichedOptions.contract)
			body.contract_path = enrichedOptions.contract;
		if (enrichedOptions.contract_content)
			body.contract_content = enrichedOptions.contract_content;

		payload = await client.postJson(
			"/api/v1/ontology/validate-delta",
			body,
		);
	} else if (sub === "diff") {
		const body: Record<string, unknown> = {};
		if (enrichedOptions.base)
			body.base_ontology_path = enrichedOptions.base;
		if (enrichedOptions.base_content)
			body.base_ontology_content = enrichedOptions.base_content;
		if (enrichedOptions.base_ontology_content)
			body.base_ontology_content = enrichedOptions.base_ontology_content;
		if (enrichedOptions.target)
			body.target_ontology_path = enrichedOptions.target;
		if (enrichedOptions.target_content)
			body.target_ontology_content = enrichedOptions.target_content;
		if (enrichedOptions.target_ontology_content)
			body.target_ontology_content =
				enrichedOptions.target_ontology_content;

		payload = await client.postJson("/api/v1/ontology/diff", body);
	} else if (sub === "accept") {
		const body: Record<string, unknown> = {
			action: "ACCEPT",
		};
		if (enrichedOptions.delta) body.delta_path = enrichedOptions.delta;
		if (enrichedOptions.delta_content)
			body.delta_content = enrichedOptions.delta_content;
		if (enrichedOptions.base)
			body.base_ontology_path = enrichedOptions.base;
		if (enrichedOptions.base_content)
			body.base_ontology_content = enrichedOptions.base_content;
		if (enrichedOptions.base_ontology_content)
			body.base_ontology_content = enrichedOptions.base_ontology_content;
		if (enrichedOptions.contract)
			body.contract_path = enrichedOptions.contract;
		if (enrichedOptions.contract_content)
			body.contract_content = enrichedOptions.contract_content;
		if (enrichedOptions.op_id) {
			const ops = String(enrichedOptions.op_id)
				.split(",")
				.map((s) => s.trim())
				.filter(Boolean);
			body.accepted_op_ids = ops;
		}
		if (enrichedOptions.decided_by)
			body.decided_by = enrichedOptions.decided_by;
		if (enrichedOptions.notes) body.steward_notes = enrichedOptions.notes;

		payload = await client.postJson("/api/v1/ontology/decisions", body);

		if (
			enrichedOptions.output &&
			typeof enrichedOptions.output === "string"
		) {
			const resp = payload as Record<string, unknown>;
			if (typeof resp.resulting_ontology_yaml === "string") {
				const fs = await import("node:fs/promises");
				await fs.writeFile(
					enrichedOptions.output,
					resp.resulting_ontology_yaml,
					"utf-8",
				);
			}
		}
	} else if (sub === "reject") {
		const body: Record<string, unknown> = {
			action: "REJECT",
		};
		if (enrichedOptions.proposal_id)
			body.proposal_id = enrichedOptions.proposal_id;
		if (enrichedOptions.delta) body.delta_path = enrichedOptions.delta;
		if (enrichedOptions.delta_content)
			body.delta_content = enrichedOptions.delta_content;
		if (enrichedOptions.base)
			body.base_ontology_path = enrichedOptions.base;
		if (enrichedOptions.base_content)
			body.base_ontology_content = enrichedOptions.base_content;
		if (enrichedOptions.base_ontology_content)
			body.base_ontology_content = enrichedOptions.base_ontology_content;
		if (enrichedOptions.decided_by)
			body.decided_by = enrichedOptions.decided_by;
		if (enrichedOptions.notes) body.steward_notes = enrichedOptions.notes;

		payload = await client.postJson("/api/v1/ontology/decisions", body);
	} else {
		throw new CliDomainError(
			"INVALID_REQUEST",
			`Unknown ontology subcommand: ${subcommand}`,
			400,
		);
	}

	return payload;
}
