/**
 * Error Handler and Exit Code Mapper.
 *
 * Driven by `error_map` in `cli-spec.json` and `schemas/v1/error-codes.json`:
 * - Exit code 0: Success (`OK`, `overall_passed = true`, `status = COMPLETED`).
 * - Exit code 1: Verification or runtime failure (`VERIFICATION_FAILED`, `INTERNAL_ERROR`).
 * - Exit code 2: Syntax, invalid CLI argument, configuration error, or missing precondition.
 * - Exit code 3: Resource, session, proposal, job, or local file not found.
 * - Exit code 4: Governance, contract, grounding, or validation violation (`422`).
 * - Exit code 5: Optimistic concurrency / stale precondition conflict (`412`).
 * - Exit code 6: Resource state conflict (`409`).
 * - Exit code 7: Authentication, RBAC role clearance, waiver, or transport forbidden (`401`/`403`).
 */

import rawErrorCodes from "../../../schemas/v1/error-codes.json" with { type: "json" };
import { dumpsJcs } from "./jcs";
import { type ErrorMapping, loadCliSpec } from "./spec";

export interface ResolvedError {
	exitCode: number;
	errorCode: string;
	detail: string;
	httpStatus: number;
	problemTitle: string;
	problemTypeUri: string;
}

export class CliDomainError extends Error {
	readonly errorCode: string;
	readonly httpStatus?: number | undefined;

	constructor(errorCode: string, message: string, httpStatus?: number) {
		super(message);
		this.name = "CliDomainError";
		this.errorCode = errorCode;
		this.httpStatus = httpStatus;
	}
}

export function mapHttpStatusToExitCode(httpStatus?: number): number {
	if (httpStatus === undefined) {
		return 2;
	}
	if (httpStatus === 401 || httpStatus === 403) {
		return 7;
	}
	if (httpStatus === 409) {
		return 6;
	}
	if (httpStatus === 412) {
		return 5;
	}
	if (httpStatus === 422) {
		return 4;
	}
	if (httpStatus === 404) {
		return 3;
	}
	if (httpStatus === 400 || httpStatus === 413 || httpStatus === 415) {
		return 2;
	}
	if (httpStatus >= 500) {
		return 1;
	}
	return 2;
}

function canonicalFallbackCodeForStatus(httpStatus?: number): string {
	if (httpStatus === 401) return "UNAUTHENTICATED";
	if (httpStatus === 403) return "FORBIDDEN_ADMIN_REQUIRED";
	if (httpStatus === 404) return "RESOURCE_NOT_FOUND";
	if (httpStatus === 409) return "SESSION_ALREADY_EXISTS";
	if (httpStatus === 412) return "ONT_EVO_CONCURRENT_MODIFICATION";
	if (httpStatus === 413) return "PAYLOAD_TOO_LARGE";
	if (httpStatus === 415) return "UNSUPPORTED_FORMAT";
	if (httpStatus === 422) return "ONT_EVO_CONTRACT_VIOLATION";
	if (httpStatus !== undefined && httpStatus >= 500) return "INTERNAL_ERROR";
	return "INVALID_REQUEST";
}

export class ErrorHandler {
	private readonly errorMap = new Map<string, ErrorMapping>();
	private readonly httpStatusMap = new Map<number, ErrorMapping>();

	constructor(customMappings?: ErrorMapping[]) {
		// 1. Authoritative normative mapping to 0..7 from schemas/v1/error-codes.json (Issue #220)
		const catalog =
			(
				rawErrorCodes as {
					error_codes?: Array<{
						code: string;
						exit_code: number;
						http_status: number;
						problem_title: string;
						problem_type_uri: string;
					}>;
				}
			).error_codes ?? [];

		for (const item of catalog) {
			const mapping: ErrorMapping = {
				error_code: item.code,
				exit_code: item.exit_code,
				http_status: item.http_status,
				problem_title: item.problem_title,
				problem_type_uri: item.problem_type_uri,
			};
			this.errorMap.set(mapping.error_code, mapping);
			if (!this.httpStatusMap.has(mapping.http_status)) {
				this.httpStatusMap.set(mapping.http_status, mapping);
			}
		}

		// 2. Overlay / supplement with cli-spec.json mappings
		const mappings = customMappings ?? loadCliSpec().error_map;
		for (const mapping of mappings) {
			if (!this.errorMap.has(mapping.error_code)) {
				this.errorMap.set(mapping.error_code, mapping);
			}
			if (!this.httpStatusMap.has(mapping.http_status)) {
				this.httpStatusMap.set(mapping.http_status, mapping);
			}
		}
	}

	resolveError(error: unknown): ResolvedError {
		if (error instanceof CliDomainError) {
			const mapped = this.errorMap.get(error.errorCode);
			if (mapped) {
				return {
					exitCode: mapped.exit_code,
					errorCode: mapped.error_code,
					detail: error.message,
					httpStatus: error.httpStatus ?? mapped.http_status,
					problemTitle: mapped.problem_title,
					problemTypeUri: mapped.problem_type_uri,
				};
			}

			// Normalize any unrecognized error string (e.g. generic HTTP status placeholders)
			// strictly onto registered canonical ErrorCode members (S5-MAJ-03).
			const fallbackCode = canonicalFallbackCodeForStatus(error.httpStatus);
			const fallbackMapped = this.errorMap.get(fallbackCode);
			const fallbackStatus =
				error.httpStatus ?? fallbackMapped?.http_status ?? 400;
			return {
				exitCode:
					fallbackMapped?.exit_code ??
					mapHttpStatusToExitCode(error.httpStatus),
				errorCode: fallbackMapped?.error_code ?? fallbackCode,
				detail: error.message,
				httpStatus: fallbackStatus,
				problemTitle: fallbackMapped?.problem_title ?? fallbackCode,
				problemTypeUri:
					fallbackMapped?.problem_type_uri ??
					`urn:opendv:error:${fallbackCode.toLowerCase().replace(/_/g, "-")}`,
			};
		}

		const errMsg = error instanceof Error ? error.message : String(error);
		const errName = error instanceof Error ? error.name : "";

		if (
			errName === "CliSyntaxError" ||
			errName === "CommandLineParseError"
		) {
			const m = this.errorMap.get("SYNTAX_INVALID_ARGUMENT");
			return {
				exitCode: m?.exit_code ?? 2,
				errorCode: "SYNTAX_INVALID_ARGUMENT",
				detail: errMsg,
				httpStatus: m?.http_status ?? 400,
				problemTitle: m?.problem_title ?? "Invalid CLI Argument",
				problemTypeUri:
					m?.problem_type_uri ?? "urn:opendv:error:syntax-invalid-argument",
			};
		}

		// Specific error matching
		if (errMsg.includes("Contract version mismatch")) {
			const m = this.errorMap.get("CONTRACT_VERSION_MISMATCH");
			return {
				exitCode: m?.exit_code ?? 2,
				errorCode: "CONTRACT_VERSION_MISMATCH",
				detail: errMsg,
				httpStatus: m?.http_status ?? 422,
				problemTitle: m?.problem_title ?? "Contract Version Mismatch",
				problemTypeUri: m?.problem_type_uri ?? "urn:opendv:version-mismatch",
			};
		}

		if (
			errMsg.includes("Missing required --api-url") ||
			errMsg.includes("Missing required option") ||
			errMsg.includes("Unrecognized flag") ||
			errMsg.includes("Invalid value for")
		) {
			const m = this.errorMap.get("INVALID_REQUEST");
			return {
				exitCode: m?.exit_code ?? 2,
				errorCode: "INVALID_REQUEST",
				detail: errMsg,
				httpStatus: m?.http_status ?? 400,
				problemTitle: m?.problem_title ?? "Bad Request",
				problemTypeUri: m?.problem_type_uri ?? "urn:opendv:invalid-request",
			};
		}

		if (
			errMsg.includes("ECONNREFUSED") ||
			errMsg.includes("Failed to connect") ||
			errMsg.includes("fetch failed") ||
			errMsg.includes("NetworkError") ||
			errMsg.includes("API Service Unreachable")
		) {
			const m = this.errorMap.get("API_UNREACHABLE");
			return {
				exitCode: m?.exit_code ?? 2,
				errorCode: "API_UNREACHABLE",
				detail: errMsg,
				httpStatus: m?.http_status ?? 503,
				problemTitle: m?.problem_title ?? "API Service Unreachable",
				problemTypeUri: m?.problem_type_uri ?? "urn:opendv:api-unreachable",
			};
		}

		const def = this.errorMap.get("INTERNAL_ERROR");
		return {
			exitCode: def?.exit_code ?? 1,
			errorCode: "INTERNAL_ERROR",
			detail: errMsg,
			httpStatus: def?.http_status ?? 500,
			problemTitle: def?.problem_title ?? "Internal Server Error",
			problemTypeUri: def?.problem_type_uri ?? "urn:opendv:internal-error",
		};
	}

	resolvePayloadExitCode(payload: unknown): number {
		if (typeof payload === "object" && payload !== null) {
			const obj = payload as Record<string, unknown>;

			if (obj.error_code === "VERIFICATION_FAILED") {
				return 1;
			}
			// Check explicit domain error_code BEFORE generic `valid === false` so
			// `delta validate` governance/contract failures exit 4 instead of 1 (MAJ-05).
			if (obj.error_code && obj.error_code !== "OK") {
				const codeStr = String(obj.error_code);
				const mapped = this.errorMap.get(codeStr);
				if (mapped) {
					return mapped.exit_code;
				}
				return 2;
			}

			if (obj.overall_passed === false) {
				return 1;
			}
			if (obj.valid === false) {
				return 1;
			}
			if (obj.verdict === "FAILED" || obj.status === "FAILED") {
				return 1;
			}
		}
		return 0;
	}

	renderError(
		error: unknown,
		isJson: boolean,
		writer: (msg: string) => void = (s) => console.error(s),
	): number {
		const resolved = this.resolveError(error);
		if (isJson) {
			const jsonOutput = dumpsJcs({
				status: "ERROR",
				error_code: resolved.errorCode,
				error: resolved.detail,
				detail: resolved.detail,
			});
			writer(jsonOutput);
		} else {
			writer(`Error: ${resolved.detail}`);
		}
		return resolved.exitCode;
	}
}

export const errorHandler = new ErrorHandler();
