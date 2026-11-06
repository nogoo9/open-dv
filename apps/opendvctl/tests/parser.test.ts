import { describe, expect, it } from "bun:test";
import { CommandLineParseError, SpecDrivenParser } from "../src/parser";

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
});
