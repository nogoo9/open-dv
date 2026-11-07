import { describe, expect, it } from "bun:test";
import {
	OpenDVWorkbenchExplorer,
	getWorkbenchVersion,
	mountWorkbenchApp,
} from "../src/index";

describe("opendv-web-workbench smoke", () => {
	it("returns workbench version with protocol version", () => {
		expect(getWorkbenchVersion()).toContain("Protocol 1.0.0");
	});

	it("renders OpenDVWorkbenchExplorer react element cleanly", () => {
		const element = OpenDVWorkbenchExplorer();
		expect(element).toBeDefined();
		expect(element.type).toBe("main");
		expect(element.props["data-testid"]).toBe("workbench-explorer-root");
	});

	it("returns null when mounting to null container", () => {
		expect(mountWorkbenchApp(null)).toBeNull();
	});
});
