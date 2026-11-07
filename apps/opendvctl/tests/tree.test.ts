import { describe, expect, it } from "bun:test";
import { loadsJcs } from "../src/jcs";
import { type CliSpec, loadCliSpec } from "../src/spec";
import { dumpCliTree, getEffectiveCliTree } from "../src/tree";

describe("tree (--dump-cli-tree)", () => {
	it("derives effective tree from cli-spec.json without standalone_only subcommands", () => {
		const tree = getEffectiveCliTree();
		expect(tree.spec_version).toBe("1.0.0");
		expect(tree.contract_version).toBe("1.0.0");

		// Global options includes --api-url
		const hasApiUrl = tree.global_options.some(
			(opt) => opt.flag === "--api-url",
		);
		expect(hasApiUrl).toBe(true);

		// Pipeline command should not contain standalone_only report subcommand
		const pipelineCmd = tree.commands.find((c) => c.name === "pipeline");
		expect(pipelineCmd).toBeDefined();
		const reportSub = pipelineCmd?.subcommands?.find(
			(s) => s.name === "report",
		);
		expect(reportSub).toBeUndefined();

		// Pipeline run and validate should be present
		const runSub = pipelineCmd?.subcommands?.find((s) => s.name === "run");
		expect(runSub).toBeDefined();
		const validateSub = pipelineCmd?.subcommands?.find(
			(s) => s.name === "validate",
		);
		expect(validateSub).toBeDefined();
	});

	it("dumpCliTree outputs canonical JCS that parses back cleanly", () => {
		const jcsString = dumpCliTree();
		expect(typeof jcsString).toBe("string");
		expect(jcsString.startsWith("{")).toBe(true);
		expect(jcsString.endsWith("}")).toBe(true);

		const parsed = loadsJcs<CliSpec>(jcsString);
		expect(parsed.contract_version).toBe("1.0.0");
		expect(parsed.commands.length).toBeGreaterThan(0);
	});
});
