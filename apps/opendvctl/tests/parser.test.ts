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

	it("P13a::MAJ-01: scopes alias rewriting to current command options and eliminates post-parse key-copying", () => {
		const syntheticSpec = {
			spec_version: "1.0.0",
			contract_version: "1.0.0",
			description: "Test spec",
			global_options: parser.getSpec().global_options,
			error_map: [],
			commands: [
				{
					name: "verify",
					description: "Verify",
					help: "Verify",
					aliases: [],
					availability: "both" as const,
					options: [],
					subcommands: [],
				},
				{
					name: "alpha",
					description: "Alpha group",
					help: "Alpha group",
					aliases: [],
					availability: "both" as const,
					options: [],
					subcommands: [
						{
							name: "canon-only",
							description: "Declares only --grounding-rationale and --project-id",
							help: "Canon only",
							aliases: [],
							availability: "both" as const,
							options: [
								{
									name: "grounding_rationale",
									flag: "--grounding-rationale",
									short: null,
									type: "string" as const,
									required: false,
									default: null,
									choices: null,
									env_var: null,
									inline_upload: false,
									help: "Grounding rationale",
								},
								{
									name: "project_id",
									flag: "--project-id",
									short: null,
									type: "string" as const,
									required: false,
									default: null,
									choices: null,
									env_var: null,
									inline_upload: false,
									help: "Project ID",
								},
							],
							subcommands: [],
						},
						{
							name: "direct-alias-flag",
							description: "Declares --waiver-rationale and --project directly",
							help: "Direct flag",
							aliases: [],
							availability: "both" as const,
							options: [
								{
									name: "waiver_rationale",
									flag: "--waiver-rationale",
									short: null,
									type: "string" as const,
									required: false,
									default: null,
									choices: null,
									env_var: null,
									inline_upload: false,
									help: "Direct waiver rationale",
								},
								{
									name: "project",
									flag: "--project",
									short: null,
									type: "string" as const,
									required: false,
									default: null,
									choices: null,
									env_var: null,
									inline_upload: false,
									help: "Direct project",
								},
							],
							subcommands: [],
						},
						{
							name: "neither",
							description: "Declares neither flag",
							help: "Neither",
							aliases: [],
							availability: "both" as const,
							options: [],
							subcommands: [],
						},
					],
				},
			],
		};

		const scopedParser = new SpecDrivenParser(syntheticSpec);

		// 1. Subcommand declaring ONLY canonical flag accepts alias and maps strictly to canonical key
		const canonViaAlias = scopedParser.parse([
			"alpha",
			"canon-only",
			"--waiver-rationale",
			"Steward domain justification",
			"--project",
			"proj-42",
		]);
		expect(canonViaAlias.options.grounding_rationale).toBe(
			"Steward domain justification",
		);
		expect(canonViaAlias.options.waiver_rationale).toBeUndefined();
		expect(canonViaAlias.options.project_id).toBe("proj-42");
		expect(canonViaAlias.options.project).toBeUndefined();

		// 2. Subcommand declaring --waiver-rationale / --project directly preserves its own option key
		const directParsed = scopedParser.parse([
			"alpha",
			"direct-alias-flag",
			"--waiver-rationale",
			"Direct waiver text",
			"--project",
			"direct-proj",
		]);
		expect(directParsed.options.waiver_rationale).toBe("Direct waiver text");
		expect(directParsed.options.grounding_rationale).toBeUndefined();
		expect(directParsed.options.project).toBe("direct-proj");
		expect(directParsed.options.project_id).toBeUndefined();

		// Passing undeclared --grounding-rationale or --project-id to direct-alias-flag is rejected
		expect(() =>
			scopedParser.parse([
				"alpha",
				"direct-alias-flag",
				"--grounding-rationale",
				"Should fail",
			]),
		).toThrow(CliSyntaxError);
		expect(() =>
			scopedParser.parse([
				"alpha",
				"direct-alias-flag",
				"--project-id",
				"Should fail",
			]),
		).toThrow(CliSyntaxError);

		// 3. Subcommand declaring neither flag rejects alias flags instead of rewriting globally
		expect(() =>
			scopedParser.parse(["alpha", "neither", "--waiver-rationale", "bad"]),
		).toThrow(CliSyntaxError);
		expect(() =>
			scopedParser.parse(["alpha", "neither", "--project", "bad"]),
		).toThrow(CliSyntaxError);
		expect(() =>
			parser.parse(["ontology", "studio", "list", "--waiver-rationale", "bad"]),
		).toThrow(CliSyntaxError);

		// 4. Live cli-spec.json commands declaring BOTH flags do NOT cross-copy keys post-parse
		const liveApplyWaiver = parser.parse([
			"ontology",
			"studio",
			"delta",
			"apply",
			"-s",
			"s1",
			"-f",
			"d.json",
			"--waiver-rationale",
			"Only waiver key should be populated",
		]);
		expect(liveApplyWaiver.options.waiver_rationale).toBe(
			"Only waiver key should be populated",
		);
		expect(liveApplyWaiver.options.grounding_rationale).toBeUndefined();

		const liveApplyGrounding = parser.parse([
			"ontology",
			"studio",
			"delta",
			"apply",
			"-s",
			"s1",
			"-f",
			"d.json",
			"--grounding-rationale",
			"Only grounding key should be populated",
		]);
		expect(liveApplyGrounding.options.grounding_rationale).toBe(
			"Only grounding key should be populated",
		);
		expect(liveApplyGrounding.options.waiver_rationale).toBeUndefined();

		// And profile --project still aliases cleanly to project_id
		const profileProj = parser.parse(["profile", "--project", "p-alpha"]);
		expect(profileProj.options.project_id).toBe("p-alpha");
		expect(profileProj.options.project).toBeUndefined();
	});
});
