import { describe, expect, it } from "bun:test";
import { getWorkbenchVersion } from "../src/index";

describe("opendv-web-workbench smoke", () => {
	it("returns workbench version with protocol version", () => {
		expect(getWorkbenchVersion()).toContain("Protocol 1.0.0");
	});
});
