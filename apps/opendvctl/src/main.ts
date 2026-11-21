/**
 * Binary CLI entrypoint (`opendvctl`) for the Open-DV Platform Client.
 */

import { errorHandler } from "./error-handler";
import {
	CliSyntaxError,
	CommandLineParseError,
	SpecDrivenParser,
} from "./parser";
import { presentResult } from "./presenter";
import { RemoteExecutor } from "./remote-executor";
import { loadCliSpec } from "./spec";
import { dumpCliTree } from "./tree";

export { CliSyntaxError, CommandLineParseError };

function argvWantsJson(argv: string[]): boolean {
	for (let i = 0; i < argv.length; i++) {
		const tok = argv[i];
		if (
			tok === "--json" ||
			tok === "--json-output" ||
			tok === "--format=json"
		) {
			return true;
		}
		if (tok === "--format" && argv[i + 1] === "json") {
			return true;
		}
	}
	return false;
}

export function stripLegacyTokenFlags(rawArgs: string[]): string[] {
	const cleaned: string[] = [];
	let i = 0;
	while (i < rawArgs.length) {
		const tok = rawArgs[i];
		if (tok === undefined) {
			i += 1;
			continue;
		}
		if (tok === "--") {
			cleaned.push(...rawArgs.slice(i));
			break;
		}
		if (tok === "--token") {
			const nextTok = rawArgs[i + 1];
			if (nextTok !== undefined && !nextTok.startsWith("-")) {
				i += 2;
			} else {
				i += 1;
			}
			continue;
		}
		if (tok.startsWith("--token=")) {
			i += 1;
			continue;
		}
		cleaned.push(tok);
		i += 1;
	}
	return cleaned;
}

function normalizeSourceInspectArgv(rawArgs: string[]): string[] {
	if (!rawArgs.includes("source") || !rawArgs.includes("inspect")) {
		return rawArgs;
	}
	if (rawArgs.includes("--uri")) {
		return rawArgs;
	}
	const sIdx = rawArgs.indexOf("source");
	if (sIdx === -1 || rawArgs[sIdx + 1] !== "inspect") {
		return rawArgs;
	}
	const optsWithArg = new Set([
		"--adapter",
		"-a",
		"--namespace",
		"-n",
		"--table",
		"-t",
		"--uri",
		"-u",
		"--credentials-ref",
		"--format",
		"--api-url",
		"--role",
		"--token",
		"--tenant-id",
		"--project-id",
	]);
	let i = sIdx + 2;
	while (i < rawArgs.length) {
		const tok = rawArgs[i];
		if (!tok) {
			i++;
			continue;
		}
		if (tok.startsWith("-")) {
			if (tok.includes("=")) {
				i++;
			} else if (optsWithArg.has(tok)) {
				i += 2;
			} else {
				i++;
			}
		} else {
			return [...rawArgs.slice(0, i), "--uri", tok, ...rawArgs.slice(i + 1)];
		}
	}
	return rawArgs;
}

export async function runCli(
	argv: string[],
	writer: (msg: string) => void = (s) => console.log(s),
	errWriter: (msg: string) => void = (s) => console.error(s),
): Promise<number> {
	const spec = loadCliSpec();
	const parser = new SpecDrivenParser();

	if (argv.length === 0) {
		writer(parser.formatHelp());
		return 0;
	}

	const sanitizedArgv = stripLegacyTokenFlags(argv);
	const normalizedArgv = normalizeSourceInspectArgv(sanitizedArgv);
	let isJson = argvWantsJson(normalizedArgv);

	try {
		const parsed = parser.parse(normalizedArgv);
		isJson =
			isJson ||
			parsed.options.json === true ||
			parsed.options.format === "json";

		if (parsed.isDumpCliTree) {
			writer(dumpCliTree());
			return 0;
		}

		if (parsed.isVersion) {
			writer(
				`opendvctl ${spec.spec_version} (contract ${spec.contract_version})`,
			);
			return 0;
		}

		if (parsed.isHelp) {
			writer(parser.formatHelp(parsed.command, parsed.subcommand));
			return 0;
		}

		if (!parsed.command) {
			writer(parser.formatHelp());
			return 0;
		}

		const executor = new RemoteExecutor({
			baseUrl: parsed.options.api_url as string | undefined,
			role: parsed.options.role as string | undefined,
			explicitRole: parsed.explicitRole,
			writer,
			errWriter,
		});

		const { exitCode, payload } = await executor.execute(
			parsed.command,
			parsed.subcommand,
			parsed.options,
		);

		presentResult(payload, {
			format: parsed.options.format as string | undefined,
			json: parsed.options.json as boolean | undefined,
			toon: parsed.options.toon as boolean | undefined,
			verbose: parsed.options.verbose as boolean | undefined,
			writer,
		});

		return exitCode;
	} catch (err) {
		return errorHandler.renderError(err, isJson, errWriter);
	}
}

if (import.meta.main) {
	runCli(process.argv.slice(2))
		.then((code) => {
			process.exit(code);
		})
		.catch((err) => {
			console.error(err);
			process.exit(2);
		});
}
