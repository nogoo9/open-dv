import { describe, expect, it } from "bun:test";
import { runCli } from "../src/main";
import {
	CliSyntaxError,
	CommandLineParseError,
	SpecDrivenParser,
} from "../src/parser";

describe("SpecDrivenParser", () => {
	const parser = new SpecDrivenParser();

	it("parses global flags", () => {
		const parsed = parser.parse(["--verbose", "--format", "json"]);
		expect(parsed.options.verbose).toBe(true);
		expect(parsed.options.format).toBe("json");
	});

	it("handles --json alias shortcut", () => {
		const parsed = parser.parse(["--json"]);
		expect(parsed.options.json).toBe(true);
		expect(parsed.options.format).toBe("json");
	});

	it("handles --dump-cli-tree", () => {
		const parsed = parser.parse(["--dump-cli-tree"]);
		expect(parsed.isDumpCliTree).toBe(true);
	});

	it("handles --version", () => {
		const parsed = parser.parse(["--version"]);
		expect(parsed.isVersion).toBe(true);
	});

	it("parses compile command with short flag -c", () => {
		const parsed = parser.parse(["compile", "-c", "path/to/contract.yaml"]);
		expect(parsed.command).toBe("compile");
		expect(parsed.options.contract).toBe("path/to/contract.yaml");
	});

	it("parses verify with alias axi-dv", () => {
		const parsed = parser.parse(["axi-dv", "--contract", "contract.yaml"]);
		expect(parsed.command).toBe("verify");
		expect(parsed.options.contract).toBe("contract.yaml");
	});

	it("falls back to verify when no subcommand is specified", () => {
		const parsed = parser.parse(["--contract", "contract.yaml"]);
		expect(parsed.command).toBe("verify");
		expect(parsed.options.contract).toBe("contract.yaml");
	});

	it("parses nested subcommand pipeline run", () => {
		const parsed = parser.parse([
			"pipeline",
			"run",
			"--input",
			"pipeline.yaml",
		]);
		expect(parsed.command).toBe("pipeline");
		expect(parsed.subcommand).toBe("run");
		expect(parsed.options.input).toBe("pipeline.yaml");
	});

	it("resolves flag alias --input-spec to input", () => {
		const parsed = parser.parse([
			"pipeline",
			"run",
			"--input-spec",
			"pipeline.yaml",
		]);
		expect(parsed.options.input).toBe("pipeline.yaml");
	});

	it("validates choices for format", () => {
		expect(() => parser.parse(["--format", "invalid-format"])).toThrow(
			CommandLineParseError,
		);
	});

	it("enforces required options when not help/version", () => {
		expect(() => parser.parse(["compile"])).toThrow(CommandLineParseError);
	});

	it("formats help text without error", () => {
		const generalHelp = parser.formatHelp();
		expect(generalHelp).toContain("Usage: opendvctl");
		expect(generalHelp).toContain("Commands:");

		const compileHelp = parser.formatHelp("compile");
		expect(compileHelp).toContain("Usage: opendvctl compile");
		expect(compileHelp).toContain("--contract");

		const subHelp = parser.formatHelp("pipeline", "run");
		expect(subHelp).toContain("Usage: opendvctl pipeline run");
		expect(subHelp).toContain("--input");
	});

	it("S5-MAJ-01: rejects unknown commands, unknown subcommands, missing subcommands, and stray positional args with CliSyntaxError (exit 2)", async () => {
		expect(() => parser.parse(["bogus-cmd"])).toThrow(CliSyntaxError);
		expect(() => parser.parse(["ontology", "bogus-sub"])).toThrow(CliSyntaxError);
		expect(() => parser.parse(["ontology", "studio", "bogus-leaf"])).toThrow(
			CliSyntaxError,
		);
		expect(() => parser.parse(["ontology"])).toThrow(CliSyntaxError);
		expect(() => parser.parse(["ontology", "studio"])).toThrow(CliSyntaxError);
		expect(() =>
			parser.parse(["ontology", "studio", "list", "stray-positional"]),
		).toThrow(CliSyntaxError);
		expect(() =>
			parser.parse(["compile", "-c", "contract.yaml", "extra-arg"]),
		).toThrow(CliSyntaxError);

		const errs: string[] = [];
		const code = await runCli(
			["bogus-cmd", "--json"],
			() => {},
			(msg) => errs.push(msg),
		);
		expect(code).toBe(2);
		const parsedErr = JSON.parse(errs.join("\n"));
		expect(parsedErr.error_code).toBe("SYNTAX_INVALID_ARGUMENT");
	});

	it("S5-MAJ-01 & P12a MAJ-03: consumes multi-token array options (--source-ids s1 s2) and applies normative create defaults", () => {
		const multi = parser.parse([
			"ontology",
			"studio",
			"parse",
			"--session",
			"s1",
			"--source-ids",
			"src-1",
			"src-2",
			"--wait",
		]);
		expect(multi.command).toBe("ontology");
		expect(multi.subcommand).toBe("studio parse");
		expect(multi.options.source_ids).toEqual(["src-1", "src-2"]);
		expect(multi.options.wait).toBe(true);

		const repeated = parser.parse([
			"ontology",
			"studio",
			"parse",
			"--session",
			"s1",
			"--source-ids",
			"src-a",
			"--source-ids",
			"src-b",
		]);
		expect(repeated.options.source_ids).toEqual(["src-a", "src-b"]);

		const created = parser.parse(["ontology", "studio", "create", "-s", "s1"]);
		expect(created.options.domain_namespace).toBe("general");
		expect(created.options.enterprise_owner).toBe("Enterprise Architecture");
	});
});
