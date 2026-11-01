import { describe, expect, it } from "bun:test";
import { PROTOCOL_VERSION } from "../src/index";

describe("opendv-protocol-ts smoke", () => {
	it("exports PROTOCOL_VERSION", () => {
		expect(PROTOCOL_VERSION).toBe("1.0.0");
	});
});
