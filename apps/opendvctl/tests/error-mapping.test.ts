import { describe, expect, it } from "bun:test";
import {
	CliDomainError,
	ErrorHandler,
	errorHandler,
} from "../src/error-handler";
import { loadsJcs } from "../src/jcs";
import { loadCliSpec } from "../src/spec";

describe("error-mapping", () => {
	const spec = loadCliSpec();

	it("verifies all domain error codes in cli-spec.json error_map", () => {
		expect(spec.error_map.length).toBeGreaterThanOrEqual(20);

		for (const mapping of spec.error_map) {
			if (mapping.error_code === "OK") {
				expect(mapping.exit_code).toBe(0);
				expect(mapping.http_status).toBe(200);
			} else if (mapping.error_code === "VERIFICATION_FAILED") {
				expect(mapping.exit_code).toBe(1);
				expect(mapping.http_status).toBe(200);
			} else {
				expect(mapping.exit_code).toBeGreaterThanOrEqual(1);
				expect(mapping.exit_code).toBeLessThanOrEqual(7);
				expect(mapping.http_status).toBeGreaterThanOrEqual(400);
				expect(mapping.http_status).toBeLessThanOrEqual(599);
			}
		}
	});

	it("resolves specific error messages to correct domain codes and exit codes", () => {
		const handler = new ErrorHandler();

		// Contract version mismatch -> exit 2
		const errMismatch = handler.resolveError(
			new Error("Contract version mismatch (client: 1.0.0, server: 2.0.0)"),
		);
		expect(errMismatch.exitCode).toBe(2);
		expect(errMismatch.errorCode).toBe("CONTRACT_VERSION_MISMATCH");

		// Missing api url -> exit 2
		const errMissingUrl = handler.resolveError(
			new Error(
				"Missing required --api-url flag or OPENDV_API_URL environment variable.",
			),
		);
		expect(errMissingUrl.exitCode).toBe(2);
		expect(errMissingUrl.errorCode).toBe("INVALID_REQUEST");

		// Network unreachable -> exit 2
		const errUnreachable = handler.resolveError(
			new Error("Failed to connect: ECONNREFUSED 127.0.0.1:8000"),
		);
		expect(errUnreachable.exitCode).toBe(2);
		expect(errUnreachable.errorCode).toBe("API_UNREACHABLE");

		// Domain error instance
		const domainErr = handler.resolveError(
			new CliDomainError("VERIFICATION_FAILED", "Gate 1 failed", 200),
		);
		expect(domainErr.exitCode).toBe(1);
		expect(domainErr.errorCode).toBe("VERIFICATION_FAILED");
	});

	it("resolves payload exit codes correctly", () => {
		expect(errorHandler.resolvePayloadExitCode({ overall_passed: true })).toBe(
			0,
		);
		expect(errorHandler.resolvePayloadExitCode({ overall_passed: false })).toBe(
			1,
		);
		expect(errorHandler.resolvePayloadExitCode({ status: "COMPLETED" })).toBe(
			0,
		);
		expect(errorHandler.resolvePayloadExitCode({ status: "FAILED" })).toBe(1);
		expect(errorHandler.resolvePayloadExitCode({ verdict: "FAILED" })).toBe(1);
		expect(
			errorHandler.resolvePayloadExitCode({
				error_code: "CONTRACT_VERSION_MISMATCH",
			}),
		).toBe(2);
	});

	it("renders errors in JSON mode as canonical JCS", () => {
		const lines: string[] = [];
		const code = errorHandler.renderError(
			new Error("Missing required --api-url flag"),
			true,
			(msg) => lines.push(msg),
		);

		expect(code).toBe(2);
		expect(lines.length).toBe(1);
		const parsed = loadsJcs<{
			status: string;
			error_code: string;
			detail: string;
		}>(lines[0] ?? "");
		expect(parsed.status).toBe("ERROR");
		expect(parsed.error_code).toBe("INVALID_REQUEST");
		expect(parsed.detail).toContain("Missing required --api-url");
	});

	it("renders errors in text mode to stderr format", () => {
		const lines: string[] = [];
		const code = errorHandler.renderError(
			new Error("Contract version mismatch (client: 1.0.0, server: 2.0.0)"),
			false,
			(msg) => lines.push(msg),
		);

		expect(code).toBe(2);
		expect(lines.length).toBe(1);
		expect(lines[0]).toBe(
			"Error: Contract version mismatch (client: 1.0.0, server: 2.0.0)",
		);
	});
});
