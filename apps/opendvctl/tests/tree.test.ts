import * as path from "node:path";
import { describe, expect, it } from "bun:test";
import { loadsJcs } from "../src/jcs";
import type { CliSpec } from "../src/spec";
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

	it("maintains exact structural parity of ontology studio tree with Python opendv-cli-common (P13::MAJ-02)", async () => {
		const repoRoot = path.resolve(import.meta.dir, "../../..");
		const canonicalSpec = (await Bun.file(
			path.join(repoRoot, "schemas/v1/cli-spec.json"),
		).json()) as CliSpec;

		const tsJcs = dumpCliTree();
		const tsClient = loadsJcs<CanonicalCliTree>(tsJcs);
		const tsStudio = tsClient.commands.ontology?.subcommands?.studio;
		expect(tsStudio).toBeDefined();

		// 1. Always verify against canonical schemas/v1/cli-spec.json (supports pure-Bun CI runners without uv)
		const specOntology = canonicalSpec.commands.find(
			(c) => c.name === "ontology",
		);
		const specStudio = specOntology?.subcommands?.find(
			(s) => s.name === "studio",
		);
		expect(specStudio).toBeDefined();
		const expectedClientSubKeys = (specStudio?.subcommands ?? [])
			.filter((s) => s.availability !== "standalone_only")
			.map((s) => s.name)
			.sort();
		const clientSubKeys = Object.keys(tsStudio?.subcommands ?? {}).sort();
		expect(clientSubKeys).toEqual(expectedClientSubKeys);
		expect(clientSubKeys).not.toContain("init");

		// 2. When uv + Python workspace are available, also assert bit-for-bit JCS parity with opendv_cli_common
		const uvBin = Bun.which("uv");
		if (uvBin) {
			const pyCode = [
				"import json",
				"from opendv_cli_common.tree import build_cli_tree, dump_cli_tree",
				"print(json.dumps({'client': build_cli_tree(prog='opendvctl', mode='client'), 'standalone': build_cli_tree(prog='opendv', mode='standalone'), 'client_jcs': dump_cli_tree(prog='opendvctl', mode='client')}))",
			].join("; ");

			const proc = Bun.spawnSync(
				[uvBin, "run", "--no-sync", "python", "-c", pyCode],
				{
					cwd: repoRoot,
					stdout: "pipe",
					stderr: "pipe",
				},
			);
			if (proc.exitCode === 0) {
				const pyTrees = JSON.parse(new TextDecoder().decode(proc.stdout)) as {
					client: CanonicalCliTree;
					standalone: CanonicalCliTree;
					client_jcs: string;
				};
				expect(tsJcs).toBe(pyTrees.client_jcs);
				const pyClientStudio =
					pyTrees.client.commands.ontology?.subcommands?.studio;
				const pyStandaloneStudio =
					pyTrees.standalone.commands.ontology?.subcommands?.studio;
				expect(pyClientStudio).toBeDefined();
				expect(pyStandaloneStudio).toBeDefined();
				expect(tsStudio).toEqual(pyClientStudio);

				const standaloneSubKeys = Object.keys(
					pyStandaloneStudio?.subcommands ?? {},
				).sort();
				expect(standaloneSubKeys).toContain("init");
				expect(standaloneSubKeys.filter((k) => k !== "init")).toEqual(
					clientSubKeys,
				);
			}
		}
	});
});
