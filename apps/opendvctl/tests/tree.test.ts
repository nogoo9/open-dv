import * as path from "node:path";
import { describe, expect, it } from "bun:test";
import { loadsJcs } from "../src/jcs";
import { type CanonicalCliTree, dumpCliTree, getEffectiveCliTree } from "../src/tree";

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

		// Ontology studio init is standalone_only and excluded in client tree
		const ontologyCmd = tree.commands.find((c) => c.name === "ontology");
		const studioSub = ontologyCmd?.subcommands?.find((s) => s.name === "studio");
		expect(studioSub).toBeDefined();
		expect(studioSub?.subcommands?.find((s) => s.name === "init")).toBeUndefined();
		const deltaSub = studioSub?.subcommands?.find((s) => s.name === "delta");
		expect(deltaSub?.subcommands?.find((s) => s.name === "validate")).toBeDefined();
		expect(deltaSub?.subcommands?.find((s) => s.name === "apply")).toBeDefined();

		// Pipeline run and validate should be present
		const runSub = pipelineCmd?.subcommands?.find((s) => s.name === "run");
		expect(runSub).toBeDefined();
		const validateSub = pipelineCmd?.subcommands?.find(
			(s) => s.name === "validate",
		);
		expect(validateSub).toBeDefined();
	});

	it("dumpCliTree outputs canonical JCS matching keyed commands schema", () => {
		const jcsString = dumpCliTree();
		expect(typeof jcsString).toBe("string");
		expect(jcsString.startsWith("{")).toBe(true);
		expect(jcsString.endsWith("}")).toBe(true);

		const parsed = loadsJcs<CanonicalCliTree>(jcsString);
		expect(parsed.contract_version).toBe("1.0.0");
		expect(parsed.prog).toBe("opendvctl");
		expect(Object.keys(parsed.commands).length).toBeGreaterThan(0);
		expect(parsed.commands.ontology?.subcommands?.studio).toBeDefined();
	});

	it("maintains exact structural parity of ontology studio tree with Python opendv-cli-common (P13::MAJ-02)", () => {
		const repoRoot = path.resolve(import.meta.dir, "../../..");
		const pyCode = [
			"import json",
			"from opendv_cli_common.tree import build_cli_tree",
			"print(json.dumps({'client': build_cli_tree(prog='opendvctl', mode='client'), 'standalone': build_cli_tree(prog='opendv', mode='standalone')}))",
		].join("; ");

		const proc = Bun.spawnSync(["uv", "run", "python", "-c", pyCode], {
			cwd: repoRoot,
			stdout: "pipe",
			stderr: "pipe",
		});
		expect(proc.exitCode).toBe(0);
		const pyTrees = JSON.parse(new TextDecoder().decode(proc.stdout)) as {
			client: CanonicalCliTree;
			standalone: CanonicalCliTree;
		};

		const tsClient = loadsJcs<CanonicalCliTree>(dumpCliTree());
		const tsStudio = tsClient.commands.ontology?.subcommands?.studio;
		const pyClientStudio = pyTrees.client.commands.ontology?.subcommands?.studio;
		const pyStandaloneStudio = pyTrees.standalone.commands.ontology?.subcommands?.studio;

		expect(tsStudio).toBeDefined();
		expect(pyClientStudio).toBeDefined();
		expect(pyStandaloneStudio).toBeDefined();

		// Exact JSON equality on ontology studio subtree in client mode
		expect(tsStudio).toEqual(pyClientStudio);

		// Standalone mode differs ONLY by including standalone_only `init`
		const standaloneSubKeys = Object.keys(pyStandaloneStudio?.subcommands ?? {}).sort();
		const clientSubKeys = Object.keys(tsStudio?.subcommands ?? {}).sort();
		expect(standaloneSubKeys).toContain("init");
		expect(clientSubKeys).not.toContain("init");
		expect(standaloneSubKeys.filter((k) => k !== "init")).toEqual(clientSubKeys);
	});
});
