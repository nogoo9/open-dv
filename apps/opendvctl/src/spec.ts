/**
 * Spec-driven CLI Specification types and loader for `opendvctl`.
 * Directly mirrors `schemas/v1/cli-spec.json` (and `cli-spec.schema.json`).
 */

import rawSpec from "../../../schemas/v1/cli-spec.json" with { type: "json" };

export interface GlobalOption {
	name: string;
	flag: string;
	short: string | null;
	type: "string" | "boolean" | "integer" | "path" | "array";
	required: boolean;
	default: unknown;
	choices: string[] | null;
	env_var: string | null;
	inline_upload: boolean;
	help: string;
}

export type CliOption = GlobalOption;

export interface CliSubcommand {
	name: string;
	description: string;
	help: string;
	aliases: string[];
	availability: "both" | "standalone_only" | "client_only";
	request_dto?: string | null;
	response_dto?: string | null;
	options: CliOption[];
}

export interface CliCommand {
	name: string;
	description: string;
	help: string;
	aliases: string[];
	availability: "both" | "standalone_only" | "client_only";
	request_dto?: string | null;
	response_dto?: string | null;
	options?: CliOption[];
	subcommands?: CliSubcommand[];
}

export interface ErrorMapping {
	error_code: string;
	exit_code: number;
	http_status: number;
	problem_title: string;
	problem_type_uri: string;
}

export interface CliSpec {
	spec_version: string;
	contract_version: string;
	description?: string;
	global_options: GlobalOption[];
	commands: CliCommand[];
	error_map: ErrorMapping[];
}

export const spec: CliSpec = rawSpec as unknown as CliSpec;

export function loadCliSpec(): CliSpec {
	return spec;
}
