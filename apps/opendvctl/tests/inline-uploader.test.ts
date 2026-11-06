import { describe, expect, it } from "bun:test";
import { processInlineUploads, readInlineFile } from "../src/inline-uploader";
import type { CliOption } from "../src/spec";

describe("inline-uploader (Decision D1)", () => {
	it("reads an existing file and throws on non-existent file", async () => {
		const tempPath = "temp-test-file.txt";
		await Bun.write(tempPath, "sample file content 123");

		try {
			const res = await readInlineFile(tempPath);
			expect(res.path).toBe(tempPath);
			expect(res.content).toBe("sample file content 123");

			expect(
				readInlineFile("non-existent-file-path-xyz.unknown"),
			).rejects.toThrow("File not found");
		} finally {
			const file = Bun.file(tempPath);
			if (await file.exists()) {
				try {
					const { unlinkSync } = await import("node:fs");
					unlinkSync(tempPath);
				} catch {}
			}
		}
	});

	it("processes options flagged inline_upload: true and injects content", async () => {
		const tempContract = "temp-contract.yaml";
		await Bun.write(tempContract, "contract_id: test-01\nspec_version: 1.0.0");

		try {
			const specOptions: CliOption[] = [
				{
					name: "contract",
					flag: "--contract",
					short: "-c",
					type: "path",
					required: true,
					default: null,
					choices: null,
					env_var: null,
					inline_upload: true,
					help: "Path to contract YAML",
				},
				{
					name: "source",
					flag: "--source",
					short: "-s",
					type: "string",
					required: false,
					default: null,
					choices: null,
					env_var: null,
					inline_upload: false,
					help: "Source URI",
				},
			];

			const rawOptions = {
				contract: tempContract,
				source: "db://table",
			};

			const enriched = await processInlineUploads(rawOptions, specOptions);

			expect(enriched.contract).toBe(tempContract);
			expect(enriched.contract_path).toBe(tempContract);
			expect(enriched.contract_content).toBe(
				"contract_id: test-01\nspec_version: 1.0.0",
			);
			expect(enriched.source).toBe("db://table");
			expect(enriched.source_content).toBeUndefined();
		} finally {
			const file = Bun.file(tempContract);
			if (await file.exists()) {
				try {
					const { unlinkSync } = await import("node:fs");
					unlinkSync(tempContract);
				} catch {}
			}
		}
	});
});
