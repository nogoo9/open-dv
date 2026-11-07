/**
 * Presenter & Output Formatter.
 *
 * Implements output formatting for both CLIs:
 * - Deterministic RFC 8785 canonical JSON for `--json` / `--format=json`.
 * - Server pre-rendered diagnostic `@toon_v1` trace for `--toon` / `--format=toon`.
 * - Server pre-rendered human-readable tables for `--format=text`.
 */

import { dumpsJcs } from "./jcs";

export interface PresentationOptions {
	format?: string | undefined;
	json?: boolean | undefined;
	toon?: boolean | undefined;
	verbose?: boolean | undefined;
	writer?: ((out: string) => void) | undefined;
}

export function presentResult(
	payload: unknown,
	options: PresentationOptions,
): void {
	const write = options.writer ?? ((s: string) => console.log(s));

	if (typeof payload === "string") {
		write(payload);
		return;
	}

	const isJson = options.json === true || options.format === "json";
	const isToon = options.toon === true || options.format === "toon";
	const isText = options.format === "text";

	if (isJson) {
		write(dumpsJcs(payload));
		return;
	}

	if (typeof payload === "object" && payload !== null) {
		const obj = payload as {
			rendered?: {
				toon?: string;
				text?: string;
				summary?: string;
			};
			summary?: string;
			error?: string;
			message?: string;
		};

		if (isToon) {
			if (obj.rendered?.toon) {
				write(obj.rendered.toon);
				return;
			}
			write(dumpsJcs(payload));
			return;
		}

		if (isText) {
			if (obj.rendered?.text) {
				write(obj.rendered.text);
				return;
			}
			if (obj.rendered?.toon) {
				write(obj.rendered.toon);
				return;
			}
		}

		// Default fallback
		if (obj.rendered?.text) {
			write(obj.rendered.text);
			return;
		}
		if (obj.rendered?.toon) {
			write(obj.rendered.toon);
			return;
		}
		if (obj.summary) {
			write(obj.summary);
			return;
		}
	}

	write(dumpsJcs(payload));
}
