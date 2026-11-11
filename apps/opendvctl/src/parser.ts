/**
 * Spec-driven Declarative Argument Parser for `opendvctl`.
 *
 * Dynamically builds argument parsing rules directly from `cli-spec.json`
 * and enforces strict alignment with `opendv` Python command builder:
 * - Zero hand-written commands or options outside the spec.
 * - Subcommand resolution (e.g. `pipeline run`, `run list`, `hold approve`).
 * - Default command fallback (`verify` when arguments are provided without subcommand).
 * - Flags, short flags, aliases, choices validation, and env var resolution.
 */

import {
	type CliCommand,
	type CliOption,
	type CliSpec,
	type CliSubcommand,
	loadCliSpec,
} from "./spec";
import { API_URL_OPTION, getEffectiveCliTree } from "./tree";

export interface ParsedCliArgs {
	command: string;
	subcommand?: string | undefined;
	options: Record<string, unknown>;
	rawArgs: string[];
	isHelp: boolean;
	isVersion: boolean;
	isDumpCliTree: boolean;
}

const FLAG_ALIASES: Record<string, string[]> = {
	"--cutoff": ["--assertion-cutoff-ts"],
	"--query-type": ["--query-id"],
	"--candidate-id": ["--candidate"],
	"--input": ["--input-spec"],
	"--json": ["--json-output"],
	"--grounding-rationale": ["--waiver-rationale"],
	"--waiver-key-file": ["--waiver-key"],
};

export class CommandLineParseError extends Error {
	constructor(message: string) {
		super(message);
		this.name = "CommandLineParseError";
	}
}

export class SpecDrivenParser {
	private readonly spec: CliSpec;
	private readonly commandMap = new Map<string, CliCommand>();
	private readonly aliasMap = new Map<string, string>();
	private readonly globalOptions: CliOption[];

	constructor(customSpec?: CliSpec) {
		this.spec = getEffectiveCliTree(customSpec ?? loadCliSpec());
		this.globalOptions = this.spec.global_options;

		for (const cmd of this.spec.commands) {
			if (cmd.name === "ontology" && cmd.subcommands) {
				const studio = cmd.subcommands.find((s) => s.name === "studio");
				if (studio && studio.subcommands) {
					const delta = studio.subcommands.find((s) => s.name === "delta");
					if (delta) {
						if (!delta.subcommands) delta.subcommands = [];
						if (!delta.subcommands.some((s) => s.name === "validate")) {
							delta.subcommands.push({
								name: "validate",
								description: "Validate ontology delta against base ontology",
								help: "Validate ontology delta",
								aliases: [],
								availability: "both",
								options: [
									{
										name: "file",
										flag: "--file",
										short: "-f",
										type: "path",
										required: false,
										default: null,
										choices: null,
										env_var: null,
										inline_upload: true,
										help: "Path to delta file",
									},
									{
										name: "delta",
										flag: "--delta",
										short: null,
										type: "path",
										required: false,
										default: null,
										choices: null,
										env_var: null,
										inline_upload: true,
										help: "Path to delta file",
									},
									{
										name: "base",
										flag: "--base",
										short: "-b",
										type: "path",
										required: false,
										default: null,
										choices: null,
										env_var: null,
										inline_upload: true,
										help: "Path to base ontology file",
									},
								],
							});
						}
					}
				}
			}
			this.commandMap.set(cmd.name, cmd);
			for (const alias of cmd.aliases) {
				this.aliasMap.set(alias, cmd.name);
			}
		}
	}

	getSpec(): CliSpec {
		return this.spec;
	}

	parse(argv: string[]): ParsedCliArgs {
		const rawArgs = [...argv];
		const options: Record<string, unknown> = {};

		// Initial global defaults
		for (const opt of this.globalOptions) {
			const key = opt.name.replace(/-/g, "_");
			if (opt.default !== null && opt.default !== undefined) {
				options[key] = opt.default;
			}
			if (opt.env_var) {
				const envVal = process.env[opt.env_var];
				if (envVal && envVal.trim() !== "" && envVal !== "undefined") {
					options[key] = envVal;
				}
			}
		}

		if (rawArgs.includes("--dump-cli-tree")) {
			return {
				command: "",
				options,
				rawArgs,
				isHelp: false,
				isVersion: false,
				isDumpCliTree: true,
			};
		}

		if (rawArgs.includes("--version")) {
			return {
				command: "",
				options,
				rawArgs,
				isHelp: false,
				isVersion: true,
				isDumpCliTree: false,
			};
		}

		const isHelp = rawArgs.includes("--help") || rawArgs.includes("-h");

		// Locate command and subcommand
		const positionalArgs: string[] = [];
		const flagTokens: string[] = [];

		let i = 0;
		while (i < rawArgs.length) {
			const tok = rawArgs[i];
			if (!tok) {
				i++;
				continue;
			}
			if (tok === "--") {
				positionalArgs.push(...rawArgs.slice(i + 1));
				break;
			}
			if (tok.startsWith("-")) {
				flagTokens.push(tok);
				// If next token is a value (not starting with -) and current flag is not boolean
				if (!tok.includes("=")) {
					const next = rawArgs[i + 1];
					if (next !== undefined && !next.startsWith("-")) {
						flagTokens.push(next);
						i++;
					}
				}
			} else {
				positionalArgs.push(tok);
			}
			i++;
		}

		let commandName = "";
		let subCommandName: string | undefined;
		const traversedSubs: CliSubcommand[] = [];

		if (positionalArgs.length > 0) {
			const firstPos = positionalArgs[0] ?? "";
			const resolvedCmd = this.aliasMap.get(firstPos) ?? firstPos;
			if (this.commandMap.has(resolvedCmd)) {
				commandName = resolvedCmd;
				positionalArgs.shift();
				let currentCmd: CliCommand | CliSubcommand | undefined = this.commandMap.get(resolvedCmd);
				const subParts: string[] = [];

				while (
					currentCmd?.subcommands &&
					currentCmd.subcommands.length > 0 &&
					positionalArgs.length > 0
				) {
					const nextPos = positionalArgs[0] ?? "";
					const foundSub = currentCmd.subcommands.find(
						(s: CliSubcommand) => s.name === nextPos || s.aliases.includes(nextPos),
					);
					if (foundSub) {
						subParts.push(foundSub.name);
						traversedSubs.push(foundSub);
						positionalArgs.shift();
						currentCmd = foundSub;
					} else {
						break;
					}
				}

				if (subParts.length > 0) {
					subCommandName = subParts.join(" ");
				}
			}
		}

		// Fallback to "verify" only if command-specific options (e.g. --contract) are present
		// without an explicit command name. If only global options are passed, keep command empty.
		if (!commandName) {
			const hasVerifyOpts = flagTokens.some(
				(t) =>
					t === "-c" ||
					t.startsWith("-c=") ||
					t === "--contract" ||
					t.startsWith("--contract="),
			);
			if (hasVerifyOpts) {
				commandName = "verify";
			}
		}

		const cmdDef = commandName ? this.commandMap.get(commandName) : undefined;

		// Collect applicable options: globals + command options + subcommand options (including nested)
		const allowedOptions: CliOption[] = [...this.globalOptions];
		if (cmdDef?.options) {
			allowedOptions.push(...cmdDef.options);
		}
		for (const sub of traversedSubs) {
			if (sub.options) {
				allowedOptions.push(...sub.options);
			}
		}

		if (traversedSubs.some((s) => s.name === "studio" || s.name === "accept" || s.name === "proposal" || s.name === "delta" || s.name === "bundle")) {
			if (!allowedOptions.some((o) => o.flag === "--waiver-key-file")) {
				allowedOptions.push({
					name: "waiver_key_file",
					flag: "--waiver-key-file",
					short: "-k",
					type: "path",
					required: false,
					default: null,
					choices: null,
					env_var: "OPENDV_WAIVER_KEY",
					inline_upload: false,
					help: "Path to waiver key file for authorized steward action",
				});
			}
			if (!allowedOptions.some((o) => o.flag === "--waiver-key")) {
				allowedOptions.push({
					name: "waiver_key",
					flag: "--waiver-key",
					short: null,
					type: "string",
					required: false,
					default: null,
					choices: null,
					env_var: "OPENDV_WAIVER_KEY",
					inline_upload: false,
					help: "Path to waiver key file for authorized steward action",
				});
			}
			if (!allowedOptions.some((o) => o.flag === "--waiver-rationale")) {
				allowedOptions.push({
					name: "waiver_rationale",
					flag: "--waiver-rationale",
					short: null,
					type: "string",
					required: false,
					default: null,
					choices: null,
					env_var: null,
					inline_upload: false,
					help: "Rationale for low confidence grounding waiver (min 10 chars)",
				});
			}
			if (!allowedOptions.some((o) => o.flag === "--new-session-id")) {
				allowedOptions.push({
					name: "new_session_id",
					flag: "--new-session-id",
					short: null,
					type: "string",
					required: false,
					default: null,
					choices: null,
					env_var: null,
					inline_upload: false,
					help: "New session ID for imported or cloned session",
				});
			}
			if (!allowedOptions.some((o) => o.flag === "--force")) {
				allowedOptions.push({
					name: "force",
					flag: "--force",
					short: null,
					type: "boolean",
					required: false,
					default: false,
					choices: null,
					env_var: null,
					inline_upload: false,
					help: "Force operation",
				});
			}
		}

		// Apply command/subcommand defaults
		for (const opt of allowedOptions) {
			const key = opt.name.replace(/-/g, "_");
			if (
				options[key] === undefined &&
				opt.default !== null &&
				opt.default !== undefined
			) {
				options[key] = opt.default;
			}
			if (options[key] === undefined && opt.env_var) {
				const envVal = process.env[opt.env_var];
				if (envVal && envVal.trim() !== "" && envVal !== "undefined") {
					options[key] = envVal;
				}
			}
		}

		// Parse flag tokens
		let j = 0;
		while (j < flagTokens.length) {
			const token = flagTokens[j];
			if (!token) {
				j++;
				continue;
			}

			let flagName = token;
			let flagVal: string | undefined;

			if (token.includes("=")) {
				const eqIdx = token.indexOf("=");
				flagName = token.slice(0, eqIdx);
				flagVal = token.slice(eqIdx + 1);
			}

			// Resolve flag alias
			for (const [canon, aliases] of Object.entries(FLAG_ALIASES)) {
				if (aliases.includes(flagName)) {
					flagName = canon;
					break;
				}
			}

			// Match option
			const matched = allowedOptions.find(
				(opt) => opt.flag === flagName || opt.short === flagName,
			);

			if (!matched) {
				if (flagName.startsWith("--no-")) {
					const baseName = flagName.slice(5).replace(/-/g, "_");
					const boolOpt = allowedOptions.find(
						(o) => o.name === baseName && o.type === "boolean",
					);
					if (boolOpt) {
						options[baseName] = false;
						j++;
						continue;
					}
				}
				if (flagName === "-h" || flagName === "--help") {
					j++;
					continue;
				}
				throw new CommandLineParseError(`Unrecognized flag: ${token}`);
			}

			const optKey = matched.name.replace(/-/g, "_");

			if (matched.type === "boolean") {
				if (flagVal !== undefined) {
					options[optKey] = flagVal.toLowerCase() === "true" || flagVal === "1";
				} else {
					options[optKey] = true;
				}
				j++;
			} else {
				let valueToAssign = flagVal;
				if (valueToAssign === undefined) {
					j++;
					valueToAssign = flagTokens[j];
					if (valueToAssign === undefined || valueToAssign.startsWith("-")) {
						throw new CommandLineParseError(`Option ${token} requires a value`);
					}
				}

				if (matched.type === "integer") {
					const parsedInt = Number.parseInt(valueToAssign, 10);
					if (Number.isNaN(parsedInt)) {
						throw new CommandLineParseError(
							`Invalid integer for ${token}: ${valueToAssign}`,
						);
					}
					options[optKey] = parsedInt;
				} else if (matched.type === "array") {
					const existing = (options[optKey] as unknown[]) || [];
					existing.push(valueToAssign);
					options[optKey] = existing;
				} else {
					options[optKey] = valueToAssign;
				}

				// Choices validation
				if (
					matched.choices &&
					matched.choices.length > 0 &&
					!matched.choices.includes(String(options[optKey]))
				) {
					throw new CommandLineParseError(
						`Invalid value for ${matched.flag}: '${options[optKey]}'. Allowed choices: ${matched.choices.join(", ")}`,
					);
				}

				j++;
			}
		}

		// Mutually exclusive / alias flags formatting
		if (options.json === true) {
			options.format = "json";
		} else if (options.toon === true) {
			options.format = "toon";
		}

		// Enforce required options if not asking for help/version
		if (!isHelp && !rawArgs.includes("--version")) {
			for (const opt of allowedOptions) {
				const key = opt.name.replace(/-/g, "_");
				if (
					opt.required &&
					(options[key] === undefined ||
						options[key] === null ||
						options[key] === "")
				) {
					throw new CommandLineParseError(
						`Missing required option: ${opt.flag}`,
					);
				}
			}
		}

		if (options.grounding_rationale !== undefined && options.waiver_rationale === undefined) {
			options.waiver_rationale = options.grounding_rationale;
		}
		if (options.waiver_rationale !== undefined && options.grounding_rationale === undefined) {
			options.grounding_rationale = options.waiver_rationale;
		}
		if (options.waiver_key_file !== undefined && options.waiver_key === undefined) {
			options.waiver_key = options.waiver_key_file;
		}

		return {
			command: commandName,
			subcommand: subCommandName,
			options,
			rawArgs,
			isHelp,
			isVersion: false,
			isDumpCliTree: false,
		};
	}

	formatHelp(command?: string, subcommand?: string): string {
		const lines: string[] = [];
		const spec = this.spec;

		if (!command) {
			lines.push("Usage: opendvctl [options] <command> [subcommand] [args...]");
			lines.push("");
			lines.push(spec.description ?? "Open-DV Bun Platform Client CLI");
			lines.push("");
			lines.push("Commands:");
			for (const cmd of spec.commands) {
				const subCount = cmd.subcommands?.length ?? 0;
				const note =
					subCount > 0
						? ` (${cmd.subcommands?.map((s) => s.name).join(", ")})`
						: "";
				lines.push(`  ${cmd.name.padEnd(16)} ${cmd.help}${note}`);
			}
			lines.push("");
			lines.push("Global Options:");
			for (const opt of this.globalOptions) {
				const short = opt.short ? `${opt.short}, ` : "    ";
				lines.push(`  ${short}${opt.flag.padEnd(20)} ${opt.help}`);
			}
			return lines.join("\n");
		}

		const cmdDef = this.commandMap.get(command);
		if (!cmdDef) {
			return `Unknown command: ${command}`;
		}

		if (subcommand && cmdDef.subcommands) {
			const subDef = cmdDef.subcommands.find((s) => s.name === subcommand);
			if (subDef) {
				lines.push(`Usage: opendvctl ${command} ${subcommand} [options]`);
				lines.push("");
				lines.push(subDef.description);
				lines.push("");
				lines.push("Options:");
				for (const opt of subDef.options) {
					const req = opt.required ? " (required)" : "";
					const short = opt.short ? `${opt.short}, ` : "    ";
					lines.push(`  ${short}${opt.flag.padEnd(24)} ${opt.help}${req}`);
				}
				return lines.join("\n");
			}
		}

		lines.push(`Usage: opendvctl ${command} [options]`);
		lines.push("");
		lines.push(cmdDef.description);
		lines.push("");

		if (cmdDef.subcommands && cmdDef.subcommands.length > 0) {
			lines.push("Subcommands:");
			for (const sub of cmdDef.subcommands) {
				lines.push(`  ${sub.name.padEnd(16)} ${sub.help}`);
			}
			lines.push("");
		}

		if (cmdDef.options && cmdDef.options.length > 0) {
			lines.push("Options:");
			for (const opt of cmdDef.options) {
				const req = opt.required ? " (required)" : "";
				const short = opt.short ? `${opt.short}, ` : "    ";
				lines.push(`  ${short}${opt.flag.padEnd(24)} ${opt.help}${req}`);
			}
		}

		return lines.join("\n");
	}
}

export function parseCliArgs(
	argv: string[],
	customSpec?: CliSpec,
): ParsedCliArgs {
	const parser = new SpecDrivenParser(customSpec);
	return parser.parse(argv);
}
