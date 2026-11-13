import { describe, expect, it } from "bun:test";
import {
	CliDomainError,
	ErrorHandler,
	errorHandler,
	mapHttpStatusToExitCode,
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

	it("S5-MAJ-03: every error code referenced in error-handler.ts is registered in canonical ErrorCode catalog", async () => {
		const rawCatalog = (await Bun.file(
			"../../schemas/v1/error-codes.json",
		).json()) as {
			error_codes: Array<{ code: string; exit_code: number }>;
		};
		const registered = new Set<string>([
			...rawCatalog.error_codes.map((e) => e.code),
			...spec.error_map.map((e) => e.error_code),
		]);

		const src = await Bun.file("src/error-handler.ts").text();
		const nonErrorCodeLiterals = new Set([
			"ERROR",
			"FAILED",
			"COMPLETED",
			"ECONNREFUSED",
		]);
		const codeTokens = [...src.matchAll(/"([A-Z][A-Z0-9_]{2,})"/g)]
			.map((m) => m[1]!)
			.filter((t) => !nonErrorCodeLiterals.has(t));

		expect(codeTokens.length).toBeGreaterThanOrEqual(10);
		for (const tok of codeTokens) {
			if (tok === "OK") continue;
			expect(registered.has(tok)).toBe(true);
		}

		// Verify PROPOSAL_STALE_REBASE_REQUIRED maps to exit 6 (409 conflict)
		const staleRebase = errorHandler.resolveError(
			new CliDomainError("PROPOSAL_STALE_REBASE_REQUIRED", "Rebase required", 409),
		);
		expect(staleRebase.exitCode).toBe(6);
		expect(staleRebase.errorCode).toBe("PROPOSAL_STALE_REBASE_REQUIRED");

		// Unregistered HTTP fallback strings normalize strictly onto canonical ErrorCode members
		const norm401 = errorHandler.resolveError(
			new CliDomainError("AUTH_UNAUTHORIZED", "Missing token", 401),
		);
		expect(norm401.exitCode).toBe(7);
		expect(norm401.errorCode).toBe("UNAUTHENTICATED");

		const norm422 = errorHandler.resolveError(
			new CliDomainError("VALIDATION_FAILED", "Invalid schema", 422),
		);
		expect(norm422.exitCode).toBe(4);
		expect(norm422.errorCode).toBe("ONT_EVO_CONTRACT_VIOLATION");
	});

	it("P13a::MAJ-01: cross-catalog parity — cli-spec.json unconditionally overwrites error-codes.json and INTERNAL_ERROR maps to exit 2", async () => {
		const rawCatalog = (await Bun.file(
			"../../schemas/v1/error-codes.json",
		).json()) as {
			error_codes: Array<{
				code: string;
				exit_code: number;
				http_status: number;
				problem_title: string;
				problem_type_uri: string;
			}>;
		};
		const specByCode = new Map(
			spec.error_map.map((m) => [m.error_code, m]),
		);

		// 1. Every code in schemas/v1/cli-spec.json error_map resolves to its exact cli-spec exit_code
		for (const mapping of spec.error_map) {
			expect(errorHandler.getExitCode(mapping.error_code)).toBe(
				mapping.exit_code,
			);
			expect(
				errorHandler.getExitCodeForPayload({
					error_code: mapping.error_code,
					...(mapping.error_code === "OK"
						? { status: "COMPLETED" }
						: { status: "FAILED" }),
				}),
			).toBe(mapping.exit_code);

			if (mapping.error_code !== "OK") {
				const resolved = errorHandler.resolveError(
					new CliDomainError(
						mapping.error_code,
						`Test ${mapping.error_code}`,
						mapping.http_status,
					),
				);
				expect(resolved.exitCode).toBe(mapping.exit_code);
				expect(resolved.errorCode).toBe(mapping.error_code);
			}
		}

		// 2. Every Studio code in schemas/v1/error-codes.json outside cli-spec.json resolves to its error-codes.json exit_code
		for (const entry of rawCatalog.error_codes) {
			const expectedExit =
				specByCode.get(entry.code)?.exit_code ?? entry.exit_code;
			expect(errorHandler.getExitCode(entry.code)).toBe(expectedExit);
			expect(
				errorHandler.getExitCodeForPayload({
					error_code: entry.code,
					...(entry.code === "OK"
						? { status: "COMPLETED" }
						: { status: "FAILED" }),
				}),
			).toBe(expectedExit);
		}

		// 3. Wave-1 INTERNAL_ERROR and HTTP 500 fallback resolve to exit 2 (matching Python ErrorCode.INTERNAL_ERROR)
		expect(errorHandler.getExitCode("INTERNAL_ERROR")).toBe(2);
		expect(
			errorHandler.getExitCodeForPayload({
				error_code: "INTERNAL_ERROR",
				status: "FAILED",
			}),
		).toBe(2);
		expect(
			errorHandler.resolveError(
				new CliDomainError("INTERNAL_ERROR", "Database pool crash", 500),
			).exitCode,
		).toBe(2);
		expect(
			errorHandler.resolveError(new Error("Unexpected unhandled exception"))
				.exitCode,
		).toBe(2);
		expect(mapHttpStatusToExitCode(500)).toBe(2);
		expect(mapHttpStatusToExitCode(503)).toBe(2);
		expect(mapHttpStatusToExitCode(501)).toBe(1);
		const unknown500 = errorHandler.resolveError(
			new CliDomainError("UNREGISTERED_500_CODE", "Opaque 500", 500),
		);
		expect(unknown500.errorCode).toBe("INTERNAL_ERROR");
		expect(unknown500.exitCode).toBe(2);

		// Explicit Studio 500/503/501 capability codes in errorMap take precedence over mapHttpStatusToExitCode
		expect(
			errorHandler.resolveError(
				new CliDomainError("EXECUTION_FAILURE", "Job failed", 500),
			).exitCode,
		).toBe(1);
		expect(
			errorHandler.resolveError(
				new CliDomainError("AGENT_UNAVAILABLE", "Agent offline", 503),
			).exitCode,
		).toBe(1);
		expect(
			errorHandler.resolveError(
				new CliDomainError("OCR_UNAVAILABLE", "Tesseract missing", 501),
			).exitCode,
		).toBe(1);

		// 4. Authoritative overwrite test: cli-spec / customMappings overrides conflicting error-codes.json entry
		const customHandler = new ErrorHandler([
			{
				error_code: "EXECUTION_FAILURE",
				exit_code: 2,
				http_status: 500,
				problem_title: "Overridden Execution Failure",
				problem_type_uri: "urn:opendv:error:execution-failure",
			},
		]);
		// Overridden code uses authoritative overlay (2 instead of error-codes.json's 1)
		expect(customHandler.getExitCode("EXECUTION_FAILURE")).toBe(2);
		// Non-overridden Studio code from error-codes.json remains available
		expect(customHandler.getExitCode("PROPOSAL_STALE_REBASE_REQUIRED")).toBe(6);
	});
});
