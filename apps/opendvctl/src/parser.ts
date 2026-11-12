/**
 * Spec-driven Declarative Argument Parser for `opendvctl`.
 *
 * Dynamically builds argument parsing rules directly from `cli-spec.json`
 * and enforces strict alignment with `opendv` Python command builder:
 * - Zero hand-written commands or options outside the spec.
 * - Recursive subcommand resolution (e.g. `pipeline run`, `ontology studio delta apply`).
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
import { getEffectiveCliTree } from "./tree";

export interface ParsedCliArgs {
	command: string;
	subcommand?: string | undefined;
	options: Record<string, unknown>;
	rawArgs: string[];
	isHelp: boolean;
	isVersion: boolean;
	isDumpCliTree: boolean;
	explicitRole?: boolean | undefined;
}

const FLAG_ALIASES: Record<string, string[]> = {
	"--cutoff": ["--assertion-cutoff-ts"],
	"--query-type": ["--query-id"],
	"--candidate-id": ["--candidate"],
	"--input": ["--input-spec"],
	"--json": ["--json-output"],
	"--grounding-rationale": ["--waiver-rationale"],
	"--project-id": ["--project"],
};

export class CommandLineParseError extends Error {
	constructor(message: string) {
		super(message);
		this.name = "CommandLineParseError";
	}
}

export class CliSyntaxError extends CommandLineParseError {
	constructor(message: string) {
		super(message);
		this.name = "CliSyntaxError";
	}
}

function collectAlwaysBooleanFlags(spec: CliSpec): Set<string> {
	const boolFlags = new Set<string>(["-h", "--help", "--dump-cli-tree"]);
	const nonBoolFlags = new Set<string>();

	const visitOption = (opt: CliOption) => {
		const flags = [opt.flag];
		if (opt.short) flags.push(opt.short);
		for (const alias of FLAG_ALIASES[opt.flag] ?? []) {
			flags.push(alias);
		}
		if (opt.type === "boolean") {
			for (const f of flags) boolFlags.add(f);
			boolFlags.add(`--no-${opt.name.replace(/_/g, "-")}`);
		} else {
			for (const f of flags) nonBoolFlags.add(f);
		}
	};

	const visitSub = (sub: CliSubcommand) => {
		for (const opt of sub.options ?? []) visitOption(opt);
		for (const child of sub.subcommands ?? []) visitSub(child);
	};

	for (const opt of spec.global_options ?? []) visitOption(opt);
	for (const cmd of spec.commands ?? []) {
		for (const opt of cmd.options ?? []) visitOption(opt);
		for (const sub of cmd.subcommands ?? []) visitSub(sub);
	}

	for (const f of nonBoolFlags) {
		boolFlags.delete(f);
	}
	return boolFlags;
}

export class SpecDrivenParser {
	private readonly rawSpec: CliSpec;
	private readonly spec: CliSpec;
	private readonly commandMap = new Map<string, CliCommand>();
	private readonly rawCommandMap = new Map<string, CliCommand>();
	private readonly aliasMap = new Map<string, string>();
	private readonly globalOptions: CliOption[];
	private readonly alwaysBooleanFlags: Set<string>;

	constructor(customSpec?: CliSpec) {
		this.rawSpec = customSpec ?? loadCliSpec();
		this.spec = getEffectiveCliTree(this.rawSpec);
		this.globalOptions = this.spec.global_options;
		this.alwaysBooleanFlags = collectAlwaysBooleanFlags(this.rawSpec);

		for (const cmd of this.rawSpec.commands) {
			this.rawCommandMap.set(cmd.name, cmd);
		}

		for (const cmd of this.spec.commands) {
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
		const explicitOptions = new Set<string>();
		const envRole = process.env.OPENDV_ROLE;
		let explicitRole = Boolean(
			(envRole && envRole.trim() !== "" && envRole !== "undefined") ||
				rawArgs.some((t) => t === "--role" || t.startsWith("--role=")),
		);

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
					explicitOptions.add(key);
				}
			}
		}

		const doubleDashIdx = rawArgs.indexOf("--");
		const preDoubleDash =
			doubleDashIdx === -1 ? rawArgs : rawArgs.slice(0, doubleDashIdx);

		if (preDoubleDash.includes("--dump-cli-tree")) {
			return {
				command: "",
				options,
				rawArgs,
				isHelp: false,
				isVersion: false,
				isDumpCliTree: true,
				explicitRole,
			};
		}

		const isHelp =
			preDoubleDash.includes("--help") || preDoubleDash.includes("-h");

		let commandName = "";
		let implicitVerifyCandidate = false;
		let currentCmd: CliCommand | CliSubcommand | undefined;
		let rawCurrentCmd: CliCommand | CliSubcommand | undefined;
		const traversedSubs: CliSubcommand[] = [];
		const subParts: string[] = [];
		let sawGlobalVersion = false;

		type DeferredAssignment =
			| {
					opt: CliOption;
					rawToken: string;
					flagName: string;
					kind: "boolean";
					value: boolean;
			  }
			| {
					opt: CliOption;
					rawToken: string;
					flagName: string;
					kind: "negated_boolean";
					key: string;
			  }
			| {
					opt: CliOption;
					rawToken: string;
					flagName: string;
					kind: "array";
					values: string[];
			  }
			| {
					opt: CliOption;
					rawToken: string;
					flagName: string;
					kind: "scalar";
					value: string;
			  };

		const deferredAssignments: DeferredAssignment[] = [];

		const getActiveOptions = (): CliOption[] => {
			const scoped: CliOption[] = [];
			for (let k = traversedSubs.length - 1; k >= 0; k--) {
				const sub = traversedSubs[k];
				if (sub?.options) {
					scoped.push(...sub.options);
				}
			}
			if (commandName) {
				const topCmd = this.commandMap.get(commandName);
				if (topCmd?.options) {
					scoped.push(...topCmd.options);
				}
			} else {
				const verifyOpts = this.commandMap.get("verify")?.options ?? [];
				scoped.push(...verifyOpts);
			}
			return [...scoped, ...this.globalOptions];
		};

		let i = 0;
		while (i < rawArgs.length) {
			const tok = rawArgs[i];
			if (!tok) {
				i++;
				continue;
			}
			if (tok === "--") {
				const trailing = rawArgs.slice(i + 1).filter(Boolean);
				if (trailing.length > 0) {
					throw new CliSyntaxError(
						`Unexpected positional argument: '${trailing[0]}'`,
					);
				}
				break;
			}

			if (!tok.startsWith("-") || tok === "-") {
				if (!commandName) {
					const resolvedCmd = this.aliasMap.get(tok) ?? tok;
					if (!this.commandMap.has(resolvedCmd)) {
						throw new CliSyntaxError(`Unknown command: '${tok}'`);
					}
					commandName = resolvedCmd;
					currentCmd = this.commandMap.get(resolvedCmd);
					rawCurrentCmd = this.rawCommandMap.get(resolvedCmd);
					i++;
					continue;
				}

				const foundSub = currentCmd?.subcommands?.find(
					(s: CliSubcommand) => s.name === tok || s.aliases.includes(tok),
				);
				if (foundSub) {
					subParts.push(foundSub.name);
					traversedSubs.push(foundSub);
					currentCmd = foundSub;
					rawCurrentCmd = rawCurrentCmd?.subcommands?.find(
						(s: CliSubcommand) => s.name === foundSub.name,
					);
					i++;
					continue;
				}

				// Allow recognizing standalone_only nested subcommands (e.g. `ontology studio init`)
				// so RemoteExecutor can return normative exit codes (e.g. exit 7 LOOPBACK_PRIVILEGE_DISABLED_IN_REMOTE_MODE).
				const standaloneSub = rawCurrentCmd?.subcommands?.find(
					(s: CliSubcommand) =>
						s.availability === "standalone_only" &&
						(s.name === tok || s.aliases.includes(tok)),
				);
				if (standaloneSub) {
					subParts.push(standaloneSub.name);
					traversedSubs.push(standaloneSub);
					currentCmd = standaloneSub;
					rawCurrentCmd = standaloneSub;
					i++;
					continue;
				}

				if ((currentCmd?.subcommands?.length ?? 0) > 0) {
					const pathLabel = [commandName, ...subParts].join(" ");
					throw new CliSyntaxError(
						`Unknown subcommand '${tok}' for '${pathLabel}'`,
					);
				}
				throw new CliSyntaxError(`Unexpected positional argument: '${tok}'`);
			}

			// Flag token
			let flagName = tok;
			let flagVal: string | undefined;
			if (tok.includes("=")) {
				const eqIdx = tok.indexOf("=");
				flagName = tok.slice(0, eqIdx);
				flagVal = tok.slice(eqIdx + 1);
			}

			for (const [canon, aliases] of Object.entries(FLAG_ALIASES)) {
				if (aliases.includes(flagName)) {
					flagName = canon;
					break;
				}
			}

			if (flagName === "--role") {
				explicitRole = true;
			}

			if (flagName === "-h" || flagName === "--help") {
				i++;
				continue;
			}

			const subDefinesVersionNow = traversedSubs.some((s) =>
				s.options?.some((o) => o.flag === "--version"),
			);
			if (
				!commandName &&
				!subDefinesVersionNow &&
				flagName === "--version" &&
				flagVal === undefined
			) {
				sawGlobalVersion = true;
				i++;
				continue;
			}

			const activeOpts = getActiveOptions();
			const matched = activeOpts.find(
				(opt) => opt.flag === flagName || opt.short === flagName,
			);

			if (!matched) {
				if (flagName.startsWith("--no-")) {
					const baseName = flagName.slice(5).replace(/-/g, "_");
					const boolOpt = activeOpts.find(
						(o) => o.name === baseName && o.type === "boolean",
					);
					if (boolOpt) {
						deferredAssignments.push({
							opt: boolOpt,
							rawToken: tok,
							flagName,
							kind: "negated_boolean",
							key: baseName,
						});
						i++;
						continue;
					}
				}
				throw new CliSyntaxError(`Unrecognized flag: ${tok}`);
			}

			if (
				!commandName &&
				!this.globalOptions.some(
					(g) => g.flag === matched.flag && g.name === matched.name,
				) &&
				(matched.flag === "--contract" || matched.short === "-c")
			) {
				implicitVerifyCandidate = true;
			}

			if (matched.type === "boolean") {
				const boolVal =
					flagVal !== undefined
						? flagVal.toLowerCase() === "true" || flagVal === "1"
						: true;
				deferredAssignments.push({
					opt: matched,
					rawToken: tok,
					flagName,
					kind: "boolean",
					value: boolVal,
				});
				i++;
			} else if (matched.type === "array") {
				const vals: string[] = [];
				if (flagVal !== undefined) {
					if (flagVal === "") {
						throw new CliSyntaxError(`Option ${tok} requires a value`);
					}
					vals.push(flagVal);
					i++;
				} else {
					while (
						i + 1 < rawArgs.length &&
						rawArgs[i + 1] !== undefined &&
						!rawArgs[i + 1]!.startsWith("-")
					) {
						i++;
						vals.push(rawArgs[i]!);
					}
					if (vals.length === 0) {
						throw new CliSyntaxError(`Option ${tok} requires a value`);
					}
					i++;
				}
				deferredAssignments.push({
					opt: matched,
					rawToken: tok,
					flagName,
					kind: "array",
					values: vals,
				});
			} else {
				let valueToAssign = flagVal;
				if (valueToAssign === undefined) {
					const next = rawArgs[i + 1];
					if (next === undefined || next.startsWith("-")) {
						throw new CliSyntaxError(`Option ${tok} requires a value`);
					}
					valueToAssign = next;
					i += 2;
				} else {
					i++;
				}
				deferredAssignments.push({
					opt: matched,
					rawToken: tok,
					flagName,
					kind: "scalar",
					value: valueToAssign,
				});
			}
		}

		const subCommandName =
			subParts.length > 0 ? subParts.join(" ") : undefined;
		const subDefinesVersion = traversedSubs.some((s) =>
			s.options?.some((o) => o.flag === "--version"),
		);

		if (!commandName && !implicitVerifyCandidate && sawGlobalVersion) {
			return {
				command: "",
				options,
				rawArgs,
				isHelp: false,
				isVersion: true,
				isDumpCliTree: false,
				explicitRole,
			};
		}

		if (!commandName && implicitVerifyCandidate) {
			commandName = "verify";
			currentCmd = this.commandMap.get("verify");
		}

		if (sawGlobalVersion && commandName) {
			throw new CliSyntaxError("Unrecognized flag: --version");
		}

		if (
			!isHelp &&
			!sawGlobalVersion &&
			commandName &&
			commandName !== "config" &&
			(currentCmd?.subcommands?.length ?? 0) > 0
		) {
			const pathLabel = [commandName, ...subParts].join(" ");
			throw new CliSyntaxError(`Missing subcommand for '${pathLabel}'`);
		}

		const cmdDef = commandName ? this.commandMap.get(commandName) : undefined;

		// Collect applicable options: deepest subcommand options take precedence over parent/globals
		const scopedOptions: CliOption[] = [];
		for (let k = traversedSubs.length - 1; k >= 0; k--) {
			const sub = traversedSubs[k];
			if (sub?.options) {
				scopedOptions.push(...sub.options);
			}
		}
		if (cmdDef?.options) {
			scopedOptions.push(...cmdDef.options);
		}
		const allowedOptions: CliOption[] = [
			...scopedOptions,
			...this.globalOptions,
		];

		if (subDefinesVersion) {
			delete options.version;
		}

		// Apply command/subcommand defaults & env_vars
		for (const opt of scopedOptions) {
			const key = opt.name.replace(/-/g, "_");
			if (!explicitOptions.has(key)) {
				if (opt.default !== null && opt.default !== undefined) {
					options[key] = opt.default;
				}
				if (opt.env_var) {
					const envVal = process.env[opt.env_var];
					if (envVal && envVal.trim() !== "" && envVal !== "undefined") {
						options[key] = envVal;
						explicitOptions.add(key);
					}
				}
			}
		}

		// Re-validate and apply deferred flag assignments against final resolved command scope
		for (const item of deferredAssignments) {
			if (item.kind === "negated_boolean") {
				const boolOpt = allowedOptions.find(
					(o) => o.name === item.key && o.type === "boolean",
				);
				if (!boolOpt) {
					throw new CliSyntaxError(`Unrecognized flag: ${item.rawToken}`);
				}
				options[item.key] = false;
				explicitOptions.add(item.key);
				continue;
			}

			const resolvedOpt = allowedOptions.find(
				(o) => o.flag === item.flagName || o.short === item.flagName,
			);
			if (!resolvedOpt) {
				throw new CliSyntaxError(`Unrecognized flag: ${item.rawToken}`);
			}

			const optKey = resolvedOpt.name.replace(/-/g, "_");
			explicitOptions.add(optKey);

			if (item.kind === "boolean") {
				options[optKey] = item.value;
			} else if (item.kind === "array") {
				const existing = (options[optKey] as unknown[]) || [];
				for (const val of item.values) {
					if (
						resolvedOpt.choices &&
						resolvedOpt.choices.length > 0 &&
						!resolvedOpt.choices.includes(String(val))
					) {
						throw new CliSyntaxError(
							`Invalid value for ${resolvedOpt.flag}: '${val}'. Allowed choices: ${resolvedOpt.choices.join(", ")}`,
						);
					}
					existing.push(val);
				}
				options[optKey] = existing;
			} else if (resolvedOpt.type === "integer") {
				const parsedInt = Number.parseInt(item.value, 10);
				if (Number.isNaN(parsedInt)) {
					throw new CliSyntaxError(
						`Invalid integer for ${item.rawToken}: ${item.value}`,
					);
				}
				options[optKey] = parsedInt;
				if (
					resolvedOpt.choices &&
					resolvedOpt.choices.length > 0 &&
					!resolvedOpt.choices.includes(String(options[optKey]))
				) {
					throw new CliSyntaxError(
						`Invalid value for ${resolvedOpt.flag}: '${options[optKey]}'. Allowed choices: ${resolvedOpt.choices.join(", ")}`,
					);
				}
			} else {
				options[optKey] = item.value;
				if (
					resolvedOpt.choices &&
					resolvedOpt.choices.length > 0 &&
					!resolvedOpt.choices.includes(String(options[optKey]))
				) {
					throw new CliSyntaxError(
						`Invalid value for ${resolvedOpt.flag}: '${options[optKey]}'. Allowed choices: ${resolvedOpt.choices.join(", ")}`,
					);
				}
			}
		}

		// P12a MAJ-03: Normative genesis 0.0.0 defaults on `ontology studio create`
		if (commandName === "ontology" && subCommandName === "studio create") {
			if (
				!explicitOptions.has("domain_namespace") &&
				(options.domain_namespace === undefined ||
					options.domain_namespace === "default")
			) {
				options.domain_namespace = "general";
			}
			if (
				!explicitOptions.has("enterprise_owner") &&
				(options.enterprise_owner === undefined ||
					options.enterprise_owner === "Enterprise")
			) {
				options.enterprise_owner = "Enterprise Architecture";
			}
		}

		// Mutually exclusive / alias flags formatting
		if (options.json === true) {
			options.format = "json";
		} else if (options.toon === true) {
			options.format = "toon";
		}

		// Enforce required options if not asking for help/version
		if (!isHelp && !sawGlobalVersion) {
			for (const opt of allowedOptions) {
				const key = opt.name.replace(/-/g, "_");
				if (
					opt.required &&
					(options[key] === undefined ||
						options[key] === null ||
						options[key] === "")
				) {
					throw new CliSyntaxError(`Missing required option: ${opt.flag}`);
				}
			}
		}

		if (
			options.grounding_rationale !== undefined &&
			options.waiver_rationale === undefined
		) {
			options.waiver_rationale = options.grounding_rationale;
		}
		if (
			options.waiver_rationale !== undefined &&
			options.grounding_rationale === undefined
		) {
			options.grounding_rationale = options.waiver_rationale;
		}

		return {
			command: commandName,
			subcommand: subCommandName,
			options,
			rawArgs,
			isHelp,
			isVersion: false,
			isDumpCliTree: false,
			explicitRole,
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
			const parts = subcommand.split(/\s+/).filter(Boolean);
			let current: CliCommand | CliSubcommand | undefined = cmdDef;
			for (const part of parts) {
				current = current?.subcommands?.find(
					(s) => s.name === part || s.aliases.includes(part),
				);
			}
			if (current && current !== cmdDef) {
				lines.push(`Usage: opendvctl ${command} ${subcommand} [options]`);
				lines.push("");
				lines.push(current.description);
				lines.push("");
				if (current.subcommands && current.subcommands.length > 0) {
					lines.push("Subcommands:");
					for (const sub of current.subcommands) {
						lines.push(`  ${sub.name.padEnd(16)} ${sub.help}`);
					}
					lines.push("");
				}
				if (current.options && current.options.length > 0) {
					lines.push("Options:");
					for (const opt of current.options) {
						const req = opt.required ? " (required)" : "";
						const short = opt.short ? `${opt.short}, ` : "    ";
						lines.push(`  ${short}${opt.flag.padEnd(24)} ${opt.help}${req}`);
					}
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
