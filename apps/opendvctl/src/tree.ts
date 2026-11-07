/**
 * Command tree generator for `opendvctl --dump-cli-tree`.
 * Generates the effective parsed command tree derived strictly from `cli-spec.json`,
 * filtering out standalone-only commands and declaring client transport options.
 */

import { dumpsJcs } from "./jcs";
import {
	type CliCommand,
	type CliOption,
	type CliSpec,
	loadCliSpec,
} from "./spec";

export const API_URL_OPTION: CliOption = {
	name: "api_url",
	flag: "--api-url",
	short: null,
	type: "string",
	required: false,
	default: null,
	choices: null,
	env_var: "OPENDV_API_URL",
	inline_upload: false,
	help: "Open-DV Platform REST/SSE API base endpoint (e.g. https://api.open-dv.internal)",
};

export function getEffectiveCliTree(spec?: CliSpec): CliSpec {
	const sourceSpec = spec ?? loadCliSpec();

	// Global options: preserve spec global options and inject client --api-url
	const globalOptions = [...sourceSpec.global_options];
	if (!globalOptions.some((opt) => opt.flag === "--api-url")) {
		globalOptions.push(API_URL_OPTION);
	}

	// Commands: filter out standalone_only commands and subcommands
	const filteredCommands: CliCommand[] = [];

	for (const cmd of sourceSpec.commands) {
		if (cmd.availability === "standalone_only") {
			continue;
		}

		if (cmd.subcommands && cmd.subcommands.length > 0) {
			const activeSubcommands = cmd.subcommands.filter(
				(sub) => sub.availability !== "standalone_only",
			);
			filteredCommands.push({
				...cmd,
				subcommands: activeSubcommands,
			});
		} else {
			filteredCommands.push({ ...cmd });
		}
	}

	return {
		spec_version: sourceSpec.spec_version,
		contract_version: sourceSpec.contract_version,
		description: sourceSpec.description ?? "Open-DV Bun Platform Client CLI",
		global_options: globalOptions,
		commands: filteredCommands,
		error_map: sourceSpec.error_map,
	};
}

export function dumpCliTree(spec?: CliSpec): string {
	const tree = getEffectiveCliTree(spec);
	return dumpsJcs(tree);
}
