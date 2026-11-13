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
	type CliSubcommand,
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

function filterSubcommands(subs: CliSubcommand[]): CliSubcommand[] {
	const filtered: CliSubcommand[] = [];
	for (const sub of subs) {
		if (sub.availability === "standalone_only") {
			continue;
		}
		if (sub.subcommands && sub.subcommands.length > 0) {
			filtered.push({
				...sub,
				subcommands: filterSubcommands(sub.subcommands),
			});
		} else {
			filtered.push({ ...sub });
		}
	}
	return filtered;
}

export function getEffectiveCliTree(spec?: CliSpec): CliSpec {
	const sourceSpec = spec ?? loadCliSpec();

	// Global options: preserve spec global options and inject client --api-url
	const globalOptions = [...sourceSpec.global_options];
	if (!globalOptions.some((opt) => opt.flag === "--api-url")) {
		globalOptions.push(API_URL_OPTION);
	}

	// Commands: recursively filter out standalone_only commands and subcommands
	const filteredCommands: CliCommand[] = [];

	for (const cmd of sourceSpec.commands) {
		if (cmd.availability === "standalone_only") {
			continue;
		}

		if (cmd.subcommands && cmd.subcommands.length > 0) {
			filteredCommands.push({
				...cmd,
				subcommands: filterSubcommands(cmd.subcommands),
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

function buildCanonicalSubNode(sub: CliSubcommand): Record<string, unknown> {
	const node: Record<string, unknown> = {
		name: sub.name,
		description: sub.description ?? "",
		help: sub.help ?? sub.description ?? "",
		aliases: [...(sub.aliases ?? [])].sort(),
		availability: sub.availability ?? "both",
	};
	if (sub.options !== undefined) {
		node.options = sub.options.map((opt) => ({ ...opt }));
	}
	if (sub.subcommands && sub.subcommands.length > 0) {
		const childDict: Record<string, unknown> = {};
		for (const child of sub.subcommands) {
			childDict[child.name] = buildCanonicalSubNode(child);
		}
		node.subcommands = childDict;
	}
	if ("request_dto" in sub) {
		node.request_dto = sub.request_dto ?? null;
	}
	if ("response_dto" in sub) {
		node.response_dto = sub.response_dto ?? null;
	}
	return node;
}

export interface CanonicalCliTreeNode {
	name: string;
	description: string;
	help: string;
	aliases: string[];
	availability: string;
	options?: CliOption[];
	subcommands?: Record<string, CanonicalCliTreeNode>;
	request_dto?: string | null;
	response_dto?: string | null;
}

export interface CanonicalCliTree {
	commands: Record<string, CanonicalCliTreeNode>;
	contract_version: string;
	error_map: unknown[];
	global_options: CliOption[];
	prog: string;
	spec_version: string;
}

export function buildCliTree(
	spec?: CliSpec,
	prog = "opendvctl",
): CanonicalCliTree {
	const effective = getEffectiveCliTree(spec);
	const commandsTree: Record<string, unknown> = {};

	for (const cmd of effective.commands) {
		const cmdNode: Record<string, unknown> = {
			name: cmd.name,
			description: cmd.description ?? "",
			help: cmd.help ?? cmd.description ?? "",
			aliases: [...(cmd.aliases ?? [])].sort(),
			availability: cmd.availability ?? "both",
		};

		if (cmd.subcommands && cmd.subcommands.length > 0) {
			const subDict: Record<string, unknown> = {};
			for (const sub of cmd.subcommands) {
				subDict[sub.name] = buildCanonicalSubNode(sub);
			}
			cmdNode.subcommands = subDict;
		} else {
			cmdNode.options = (cmd.options ?? []).map((opt) => ({ ...opt }));
			if ("request_dto" in cmd) {
				cmdNode.request_dto = cmd.request_dto ?? null;
			}
			if ("response_dto" in cmd) {
				cmdNode.response_dto = cmd.response_dto ?? null;
			}
		}

		commandsTree[cmd.name] = cmdNode;
	}

	return {
		commands: commandsTree as Record<string, CanonicalCliTreeNode>,
		contract_version: effective.contract_version ?? "1.0.0",
		error_map: effective.error_map ?? [],
		global_options: effective.global_options.map((opt) => ({ ...opt })),
		prog,
		spec_version: effective.spec_version ?? "1.0.0",
	};
}

export function dumpCliTree(spec?: CliSpec): string {
	const tree = buildCliTree(spec);
	return dumpsJcs(tree);
}
