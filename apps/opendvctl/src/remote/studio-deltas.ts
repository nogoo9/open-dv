/**
 * Handlers for ontology studio delta operations:
 * delta validate, delta apply.
 */

import { randomUUID } from "node:crypto";
import { CliDomainError } from "../error-handler";
import type { RemoteHttpClient } from "./http-client";
import {
	normalizeDeltaOperations,
	normalizeStringArrayOption,
} from "./types";

export async function handleStudioDeltas(
	client: RemoteHttpClient,
	studioSub: string,
	options: Record<string, unknown>,
): Promise<{ handled: boolean; payload?: unknown }> {
	switch (studioSub) {
			case "delta validate": {
				let deltaContent = options.file_content as unknown;
				const deltaPath = options.file || options.delta;
				if (deltaContent === undefined && deltaPath) {
					deltaContent = await client.readLocalJsonFile(
						String(deltaPath),
						"Delta",
					);
				} else if (typeof deltaContent === "string") {
					try {
						deltaContent = JSON.parse(deltaContent);
					} catch (err) {
						throw new CliDomainError(
							"SYNTAX_INVALID_ARGUMENT",
							`Malformed delta JSON: ${err}`,
							400,
						);
					}
				}
				if (!deltaContent || typeof deltaContent !== "object") {
					throw new CliDomainError(
						"SYNTAX_INVALID_ARGUMENT",
						"Missing required --file (-f) or --delta JSON payload.",
						400,
					);
				}

				const rawDeltaObj: Record<string, unknown> = Array.isArray(deltaContent)
					? { operations: deltaContent }
					: { ...(deltaContent as Record<string, unknown>) };
				const normalizedOps = normalizeDeltaOperations(rawDeltaObj.operations);

				const sid = options.session;
				if (sid) {
					const waiverInfo = await client.resolveWaiverHeaders(options);
					let resolvedBaseVer = options.base_version
						? String(options.base_version)
						: typeof rawDeltaObj.base_version === "string" &&
								rawDeltaObj.base_version.trim() !== ""
							? rawDeltaObj.base_version.trim()
							: typeof rawDeltaObj.base_ontology_version === "string" &&
									rawDeltaObj.base_ontology_version.trim() !== ""
								? rawDeltaObj.base_ontology_version.trim()
								: undefined;
					const explicitTargetVer =
						typeof options.target_version === "string" &&
						options.target_version.trim() !== ""
							? options.target_version.trim()
							: typeof rawDeltaObj.target_version === "string" &&
									rawDeltaObj.target_version.trim() !== ""
								? rawDeltaObj.target_version.trim()
								: typeof rawDeltaObj.target_ontology_version === "string" &&
										rawDeltaObj.target_ontology_version.trim() !== ""
									? rawDeltaObj.target_ontology_version.trim()
									: undefined;
					const deltaProvidedSha =
						typeof rawDeltaObj.parent_sha256 === "string" &&
						rawDeltaObj.parent_sha256.trim() !== ""
							? rawDeltaObj.parent_sha256.trim().replace(/^"|"$/g, "")
							: typeof rawDeltaObj.base_sha256 === "string" &&
									rawDeltaObj.base_sha256.trim() !== ""
								? rawDeltaObj.base_sha256.trim().replace(/^"|"$/g, "")
								: undefined;
					const callerProvidedSha =
						options.base_sha256 !== undefined &&
						String(options.base_sha256).trim() !== ""
							? String(options.base_sha256).trim().replace(/^"|"$/g, "")
							: deltaProvidedSha;
					let resolvedParentSha = callerProvidedSha;

					if (
						resolvedBaseVer === undefined ||
						resolvedParentSha === undefined
					) {
						const sessionDetail = await client.fetchSessionDetail(String(sid));
						if (resolvedBaseVer === undefined) {
							resolvedBaseVer = String(
								sessionDetail.active_version ??
									sessionDetail.head_version ??
									"0.0.0",
							);
						}
						if (resolvedParentSha === undefined) {
							resolvedParentSha = String(sessionDetail.head_sha256 ?? "")
								.trim()
								.replace(/^"|"$/g, "");
						}
					}

					const {
						base_ontology_version: _ignoredBaseOntVer,
						target_ontology_version: _ignoredTargetOntVer,
						target_version: _ignoredTargetVer,
						...restDeltaObj
					} = rawDeltaObj;
					const body: Record<string, unknown> = {
						...restDeltaObj,
						delta_id:
							typeof rawDeltaObj.delta_id === "string" && rawDeltaObj.delta_id
								? rawDeltaObj.delta_id
								: `delta-${randomUUID().replace(/-/g, "").slice(0, 12)}`,
						base_version: resolvedBaseVer,
						parent_sha256: resolvedParentSha,
						...(explicitTargetVer !== undefined
							? { target_version: explicitTargetVer }
							: {}),
						operations: normalizedOps,
					};
					if (options.verify_grounding) {
						body.verify_grounding = true;
					}
					if (waiverInfo.low_confidence_waiver) {
						body.low_confidence_waiver = true;
						body.grounding_rationale = waiverInfo.grounding_rationale;
					}

					const encodedSid = encodeURIComponent(String(sid));
					let valRes: unknown;
					try {
						valRes = await client.postJson(
							`/api/v1/ontology/studio/sessions/${encodedSid}/validate`,
							body,
							waiverInfo.headers,
						);
					} catch (err) {
						if (
							err instanceof CliDomainError &&
							err.httpStatus === 404 &&
							err.errorCode !== "SESSION_NOT_FOUND"
						) {
							valRes = await client.postJson(
								`/api/v1/ontology/studio/sessions/${encodedSid}/deltas/validate`,
								body,
								waiverInfo.headers,
							);
						} else {
							throw err;
						}
					}
					if (
						typeof valRes === "object" &&
						valRes !== null &&
						(valRes as Record<string, unknown>).valid === false &&
						!(valRes as Record<string, unknown>).error_code
					) {
						return {
							handled: true,
							payload: {							...(valRes as Record<string, unknown>),
							error_code: "ONT_EVO_CONTRACT_VIOLATION",
}
						};
					}
					return { handled: true, payload: valRes };
				}

				const hasBase = Boolean(
					options.base ||
						options.base_ontology_path ||
						options.base_ontology_content,
				);
				if (!hasBase) {
					throw new CliDomainError(
						"SYNTAX_INVALID_ARGUMENT",
						"Either --session or --base must be provided for delta validation.",
						400,
					);
				}

				const standaloneBaseVer =
					typeof options.base_version === "string" &&
					options.base_version.trim() !== ""
						? options.base_version.trim().replace(/^v/i, "")
						: typeof rawDeltaObj.base_ontology_version === "string" &&
								rawDeltaObj.base_ontology_version.trim() !== ""
							? rawDeltaObj.base_ontology_version.trim().replace(/^v/i, "")
							: typeof rawDeltaObj.base_version === "string" &&
									rawDeltaObj.base_version.trim() !== ""
								? rawDeltaObj.base_version.trim().replace(/^v/i, "")
								: undefined;
				const standaloneTargetVer =
					typeof options.target_version === "string" &&
					options.target_version.trim() !== ""
						? options.target_version.trim().replace(/^v/i, "")
						: typeof rawDeltaObj.target_ontology_version === "string" &&
								rawDeltaObj.target_ontology_version.trim() !== ""
							? rawDeltaObj.target_ontology_version.trim().replace(/^v/i, "")
							: typeof rawDeltaObj.target_version === "string" &&
									rawDeltaObj.target_version.trim() !== ""
								? rawDeltaObj.target_version.trim().replace(/^v/i, "")
								: undefined;
				const standaloneParentSha =
					options.base_sha256 !== undefined &&
					options.base_sha256 !== null &&
					String(options.base_sha256).trim() !== ""
						? String(options.base_sha256).trim().replace(/^"|"$/g, "")
						: typeof rawDeltaObj.parent_sha256 === "string" &&
								rawDeltaObj.parent_sha256.trim() !== ""
							? rawDeltaObj.parent_sha256.trim().replace(/^"|"$/g, "")
							: typeof rawDeltaObj.base_sha256 === "string" &&
									rawDeltaObj.base_sha256.trim() !== ""
								? rawDeltaObj.base_sha256.trim().replace(/^"|"$/g, "")
								: undefined;

				const {
					base_version: _ignoredStandaloneBaseVer,
					base_ontology_version: _ignoredStandaloneBaseOntVer,
					target_version: _ignoredStandaloneTargetVer,
					target_ontology_version: _ignoredStandaloneTargetOntVer,
					base_sha256: _ignoredStandaloneBaseSha,
					parent_sha256: _ignoredStandaloneParentSha,
					...restStandaloneDeltaObj
				} = rawDeltaObj;

				const body: Record<string, unknown> = {
					delta: {
						...restStandaloneDeltaObj,
						delta_id:
							typeof rawDeltaObj.delta_id === "string" &&
							rawDeltaObj.delta_id.trim() !== ""
								? rawDeltaObj.delta_id.trim()
								: `delta-${randomUUID().replace(/-/g, "").slice(0, 12)}`,
						...(standaloneBaseVer !== undefined
							? { base_ontology_version: standaloneBaseVer }
							: {}),
						...(standaloneTargetVer !== undefined
							? { target_ontology_version: standaloneTargetVer }
							: {}),
						...(standaloneParentSha !== undefined
							? { parent_sha256: standaloneParentSha }
							: {}),
						operations: normalizedOps,
					},
				};
				let baseContent = options.base_ontology_content;
				if (!baseContent && (options.base_ontology_path || options.base)) {
					const basePath = String(options.base_ontology_path || options.base);
					try {
						const fs = await import("node:fs/promises");
						baseContent = await fs.readFile(basePath, "utf-8");
					} catch {
						throw new CliDomainError(
							"FILE_NOT_FOUND",
							`Base ontology file not found: ${basePath}`,
							404,
						);
					}
				}
				if (baseContent) {
					body.base_ontology_content = baseContent;
				}
				if (options.contract) {
					body.contract_path = options.contract;
				}
				const valRes = await client.postJson(
					"/api/v1/ontology/validate-delta",
					body,
				);
				if (
					typeof valRes === "object" &&
					valRes !== null &&
					(valRes as Record<string, unknown>).valid === false &&
					!(valRes as Record<string, unknown>).error_code
				) {
					return {
						handled: true,
						payload: {						...(valRes as Record<string, unknown>),
						error_code: "ONT_EVO_CONTRACT_VIOLATION",
}
					};
				}
				return { handled: true, payload: valRes };
			}

			case "delta apply": {
				const sid = options.session;
				if (!sid) {
					throw new CliDomainError(
						"SYNTAX_INVALID_ARGUMENT",
						"Missing required --session flag",
						400,
					);
				}

				// Resolve waiver credentials/role check BEFORE reading/applying if requested
				const waiverInfo = await client.resolveWaiverHeaders(options);

				let deltaContent = options.file_content as unknown;
				const deltaPath = options.file || options.delta;
				if (deltaContent === undefined && deltaPath) {
					deltaContent = await client.readLocalJsonFile(
						String(deltaPath),
						"Delta",
					);
				} else if (typeof deltaContent === "string") {
					try {
						deltaContent = JSON.parse(deltaContent);
					} catch (err) {
						throw new CliDomainError(
							"SYNTAX_INVALID_ARGUMENT",
							`Malformed delta JSON: ${err}`,
							400,
						);
					}
				}
				if (!deltaContent || typeof deltaContent !== "object") {
					throw new CliDomainError(
						"SYNTAX_INVALID_ARGUMENT",
						"Missing required --file (-f) or --delta JSON payload.",
						400,
					);
				}

				const rawDeltaObj: Record<string, unknown> = Array.isArray(deltaContent)
					? { operations: deltaContent }
					: { ...(deltaContent as Record<string, unknown>) };
				const normalizedOps = normalizeDeltaOperations(rawDeltaObj.operations);
				if (normalizedOps.length === 0) {
					throw new CliDomainError(
						"NO_OP_DELTA",
						"Cannot apply delta with zero operations.",
						400,
					);
				}

				let resolvedBaseVer = options.base_version
					? String(options.base_version)
					: typeof rawDeltaObj.base_version === "string" &&
							rawDeltaObj.base_version.trim() !== ""
						? rawDeltaObj.base_version.trim()
						: typeof rawDeltaObj.base_ontology_version === "string" &&
								rawDeltaObj.base_ontology_version.trim() !== ""
							? rawDeltaObj.base_ontology_version.trim()
							: undefined;
				const explicitTargetVer =
					typeof options.target_version === "string" &&
					options.target_version.trim() !== ""
						? options.target_version.trim()
						: typeof rawDeltaObj.target_version === "string" &&
								rawDeltaObj.target_version.trim() !== ""
							? rawDeltaObj.target_version.trim()
							: typeof rawDeltaObj.target_ontology_version === "string" &&
									rawDeltaObj.target_ontology_version.trim() !== ""
								? rawDeltaObj.target_ontology_version.trim()
								: undefined;
				const explicitBaseSha =
					options.base_sha256 !== undefined
						? String(options.base_sha256).trim().replace(/^"|"$/g, "")
						: "";
				const deltaBaseSha =
					typeof rawDeltaObj.base_sha256 === "string" &&
					rawDeltaObj.base_sha256.trim() !== ""
						? rawDeltaObj.base_sha256.trim().replace(/^"|"$/g, "")
						: "";
				const deltaParentSha =
					typeof rawDeltaObj.parent_sha256 === "string" &&
					rawDeltaObj.parent_sha256.trim() !== ""
						? rawDeltaObj.parent_sha256.trim().replace(/^"|"$/g, "")
						: "";
				if (deltaBaseSha && deltaParentSha && deltaBaseSha !== deltaParentSha) {
					throw new CliDomainError(
						"PRECONDITION_BASE_SHA_MISMATCH",
						`Delta base_sha256 '${deltaBaseSha}' conflicts with parent_sha256 '${deltaParentSha}'.`,
						400,
					);
				}
				const deltaSha = deltaBaseSha || deltaParentSha;
				if (explicitBaseSha && deltaSha && explicitBaseSha !== deltaSha) {
					throw new CliDomainError(
						"PRECONDITION_BASE_SHA_MISMATCH",
						`CLI --base-sha256 '${explicitBaseSha}' conflicts with delta SHA '${deltaSha}'.`,
						400,
					);
				}
				const effectiveSha = explicitBaseSha || deltaSha;
				let sessionHeadSha: string | undefined;

				if (resolvedBaseVer === undefined || !effectiveSha) {
					const sessionDetail = await client.fetchSessionDetail(String(sid));
					sessionHeadSha = String(sessionDetail.head_sha256 ?? "")
						.trim()
						.replace(/^"|"$/g, "");
					if (resolvedBaseVer === undefined) {
						resolvedBaseVer = String(
							sessionDetail.active_version ??
								sessionDetail.head_version ??
								"0.0.0",
						);
					}
				}

				const isGenesis = resolvedBaseVer.replace(/^v/i, "") === "0.0.0";
				if (!effectiveSha && !isGenesis) {
					throw new CliDomainError(
						"MISSING_PRECONDITION_HEADER",
						"Missing mandatory --base-sha256 (or delta parent_sha256) precondition for non-genesis delta apply.",
						400,
					);
				}

				const resolvedHeadSha = effectiveSha || (sessionHeadSha ?? "");
				if (!resolvedHeadSha) {
					throw new CliDomainError(
						"MISSING_PRECONDITION_HEADER",
						"Missing mandatory --base-sha256, delta parent_sha256, or session head_sha256 precondition for delta apply.",
						400,
					);
				}

				const parentSha = resolvedHeadSha;
				const ifMatchSha = resolvedHeadSha;

				const {
					base_ontology_version: _ignoredApplyBaseOntVer,
					target_ontology_version: _ignoredApplyTargetOntVer,
					target_version: _ignoredApplyTargetVer,
					...restApplyDeltaObj
				} = rawDeltaObj;
				const body: Record<string, unknown> = {
					...restApplyDeltaObj,
					delta_id:
						typeof rawDeltaObj.delta_id === "string" && rawDeltaObj.delta_id
							? rawDeltaObj.delta_id
							: `delta-${randomUUID().replace(/-/g, "").slice(0, 12)}`,
					base_version: resolvedBaseVer,
					parent_sha256: parentSha,
					...(explicitTargetVer !== undefined
						? { target_version: explicitTargetVer }
						: {}),
					operations: normalizedOps,
				};
				if (options.rationale) {
					body.rationale = options.rationale;
				}
				if (options.ungrounded_concept) {
					body.ungrounded_concept = normalizeStringArrayOption(
						options.ungrounded_concept,
					);
				}
				if (options.evidence_grounding_file) {
					body.evidence_grounding_file = String(
						options.evidence_grounding_file,
					);
				}

				const headers: Record<string, string> = {
					"If-Match": `"${ifMatchSha}"`,
				};
				if (waiverInfo.low_confidence_waiver) {
					body.low_confidence_waiver = true;
					body.grounding_rationale = waiverInfo.grounding_rationale;
					Object.assign(headers, waiverInfo.headers);
				}

				return { handled: true, payload: await client.postJson( `/api/v1/ontology/studio/sessions/${encodeURIComponent(String(sid))}/delta`, body, headers, ) };
			}

		default:
			return { handled: false };
	}
}
