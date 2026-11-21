/**
 * Unit Tests for YamlDrawer.tsx.
 */

import { describe, expect, it } from "bun:test";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { YamlDrawer } from "../src/explorer/builder/YamlDrawer";

describe("YamlDrawer Slide-Out Component", () => {
	it("renders drawer overlay, header, monospace textarea, and action buttons when open", () => {
		const sampleYaml =
			"blueprint_path: data/test.yaml\ntarget_dialect: duckdb";
		let appliedText: string | null = null;

		const html = renderToStaticMarkup(
			createElement(YamlDrawer, {
				isOpen: true,
				onClose: () => {},
				yamlText: sampleYaml,
				onApplyYaml: (text) => {
					appliedText = text;
				},
			}),
		);

		expect(appliedText).toBeNull();
		expect(html).toContain('data-testid="yaml-drawer"');
		expect(html).toContain('data-testid="yaml-spec-textarea"');
		expect(html).toContain('data-testid="btn-yaml-copy"');
		expect(html).toContain('data-testid="btn-yaml-download"');
		expect(html).toContain('data-testid="btn-yaml-apply"');
		expect(html).toContain("blueprint_path: data/test.yaml");
	});

	it("does not render into DOM when isOpen is false", () => {
		const html = renderToStaticMarkup(
			createElement(YamlDrawer, {
				isOpen: false,
				onClose: () => {},
				yamlText: "target_dialect: duckdb",
				onApplyYaml: () => {},
			}),
		);

		expect(html).toBe("");
	});

	it("renders error banner when validationError prop is provided", () => {
		const html = renderToStaticMarkup(
			createElement(YamlDrawer, {
				isOpen: true,
				onClose: () => {},
				yamlText: "foo: bar",
				validationError: "Syntax Error at line 1",
				onApplyYaml: () => {},
			}),
		);

		expect(html).toContain('data-testid="yaml-drawer-error-banner"');
		expect(html).toContain("Syntax Error at line 1");
	});
});
