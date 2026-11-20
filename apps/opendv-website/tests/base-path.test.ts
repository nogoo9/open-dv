import { describe, expect, test } from "bun:test";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { getBasePath, withBase } from "../src/lib/base-path";

const WEBSITE_ROOT = resolve(import.meta.dir, "..");

describe("base-path linear-time normalization", () => {
	test("base-path.ts contains zero leading/trailing slash regexes", () => {
		const src = readFileSync(
			resolve(WEBSITE_ROOT, "src/lib/base-path.ts"),
			"utf-8",
		);
		expect(src).not.toContain("/^\\/+|\\/+$/g");
		expect(src).not.toContain("/\\/+$/");
	});

	test("getBasePath and withBase preserve normalization equivalence", () => {
		const prevEnv = process.env.OPENDV_SITE_BASE_PATH;
		Reflect.deleteProperty(process.env, "OPENDV_SITE_BASE_PATH");
		try {
			expect(getBasePath(undefined)).toBe("/open-dv");
			expect(getBasePath("")).toBe("");
			expect(getBasePath("/")).toBe("");
			expect(getBasePath("   ///   ")).toBe("");
			expect(getBasePath("/open-dv/")).toBe("/open-dv");
			expect(getBasePath("///open-dv///")).toBe("/open-dv");
			expect(getBasePath("open-dv")).toBe("/open-dv");

			expect(withBase("/", "/open-dv/")).toBe("/open-dv/");
			expect(withBase("/tour/", "///open-dv///")).toBe("/open-dv/tour/");
			expect(withBase("/tour/", "/")).toBe("/tour/");
		} finally {
			if (prevEnv === undefined) {
				Reflect.deleteProperty(process.env, "OPENDV_SITE_BASE_PATH");
			} else {
				process.env.OPENDV_SITE_BASE_PATH = prevEnv;
			}
		}
	});

	test("adversarial 50k-slash input completes in < 50ms", () => {
		const payload = `a${"/".repeat(50_000)}b`;
		const t0 = performance.now();
		const result = getBasePath(payload);
		const elapsedMs = performance.now() - t0;
		expect(result).toBe(`/${payload}`);
		expect(elapsedMs).toBeLessThan(50);
	});
});
