/**
 * Handlers for session lifecycle and analysis studio subcommands:
 * init, create, delete, list, status, upload, parse, clarify, answer, construct.
 */

import { CliDomainError } from "../error-handler";
import type { RemoteHttpClient } from "./http-client";
import {
	computeCanonicalAnswersSha256,
	normalizeStringArrayOption,
} from "./types";

export async function handleStudioSessions(
	client: RemoteHttpClient,
	studioSub: string,
	options: Record<string, unknown>,
): Promise<{ handled: boolean; payload?: unknown }> {
	switch (studioSub) {
			case "init": {
				throw new CliDomainError(
					"LOOPBACK_PRIVILEGE_DISABLED_IN_REMOTE_MODE",
					"The 'ontology studio init' command generates a local workstation loopback secret and is prohibited against remote platform API endpoints.",
					403,
				);
			}

			case "create": {
				client.assertLoopbackForServerPath(
					"--from-workspace",
					options.from_workspace,
				);
				const body: Record<string, unknown> = {
					domain_namespace: options.domain_namespace || "general",
					enterprise_owner:
						options.enterprise_owner || "Enterprise Architecture",
				};
				if (options.session) body.session_id = options.session;
				if (options.description) body.description = options.description;
				if (options.ontology_id) body.ontology_id = options.ontology_id;
				if (options.from_workspace)
					body.from_workspace = options.from_workspace;
				if (options.session_label) body.session_label = options.session_label;
				return { handled: true, payload: await client.postJson("/api/v1/ontology/studio/sessions", body) };
			}

			case "delete": {
				const sid = options.session;
				if (!sid) {
					throw new CliDomainError(
						"SYNTAX_INVALID_ARGUMENT",
						"Missing required --session flag",
						400,
					);
				}
				const forceParam = options.force ? "?force=true" : "";
				const resp = await client.deleteRequest(
					`/api/v1/ontology/studio/sessions/${encodeURIComponent(String(sid))}${forceParam}`,
				);
				return { handled: true, payload: resp ?? { session_id: String(sid), status: "DELETED" } };
			}

			case "list": {
				const params = new URLSearchParams();
				const limit =
					options.limit !== undefined &&
					Number.isInteger(Number(options.limit)) &&
					Number(options.limit) >= 0
						? String(options.limit)
						: "50";
				const offset =
					options.offset !== undefined &&
					Number.isInteger(Number(options.offset)) &&
					Number(options.offset) >= 0
						? String(options.offset)
						: "0";
				params.set("limit", limit);
				params.set("offset", offset);
				if (options.status) {
					params.set("status", String(options.status));
				}
				return { handled: true, payload: await client.getJson( `/api/v1/ontology/studio/sessions?${params.toString()}`, ) };
			}

			case "status": {
				const sid = options.session;
				if (!sid) {
					throw new CliDomainError(
						"SYNTAX_INVALID_ARGUMENT",
						"Missing required --session flag",
						400,
					);
				}
				const encodedSid = encodeURIComponent(String(sid));
				try {
					return { handled: true, payload: await client.getJson( `/api/v1/ontology/studio/sessions/${encodedSid}/status`, ) };
				} catch (err) {
					if (
						err instanceof CliDomainError &&
						err.httpStatus === 404 &&
						err.errorCode !== "SESSION_NOT_FOUND"
					) {
						return { handled: true, payload: await client.fetchSessionDetail(String(sid)) };
					}
					throw err;
				}
			}

			case "upload": {
				const sid = options.session;
				if (!sid) {
					throw new CliDomainError(
						"SYNTAX_INVALID_ARGUMENT",
						"Missing required --session flag",
						400,
					);
				}
				const filePath = (options.file_path || options.file) as
					| string
					| undefined;
				let rawBuffer: Buffer | Uint8Array | null = null;
				let fileName = "upload";

				if (filePath) {
					const fs = await import("node:fs/promises");
					try {
						const stat = await fs.stat(filePath);
						if (stat.size === 0) {
							throw new CliDomainError(
								"CORRUPT_SOURCE_BLOB",
								`Source file '${filePath}' is empty (0 bytes).`,
								422,
							);
						}
						rawBuffer = await fs.readFile(filePath);
					} catch (err: unknown) {
						if (err instanceof CliDomainError) throw err;
						if (
							(err instanceof Error &&
								"code" in err &&
								(err as { code: string }).code === "ENOENT") ||
							String(err).includes("ENOENT")
						) {
							throw new CliDomainError(
								"FILE_NOT_FOUND",
								`Source file not found: ${filePath}`,
								404,
							);
						}
						throw new CliDomainError(
							"SYNTAX_INVALID_ARGUMENT",
							`Failed to read source file '${filePath}': ${err}`,
							400,
						);
					}
					fileName = filePath.split(/[\\/]/).pop() || "upload";
				} else if (options.file_content !== undefined) {
					const strContent = String(options.file_content);
					if (strContent.length === 0) {
						throw new CliDomainError(
							"CORRUPT_SOURCE_BLOB",
							"Source content is empty (0 bytes).",
							422,
						);
					}
					rawBuffer = Buffer.from(strContent, "utf-8");
				} else {
					throw new CliDomainError(
						"SYNTAX_INVALID_ARGUMENT",
						"Missing required --file or source content",
						400,
					);
				}

				const formData = new FormData();
				const blob = new Blob([new Uint8Array(rawBuffer)], {
					type: "application/octet-stream",
				});
				formData.append("file", blob, fileName);
				const isConfidential = Boolean(
					options.confidential ||
						options["do-not-send-to-agent"] ||
						options.do_not_send_to_agent,
				);
				if (isConfidential) {
					formData.append("do_not_send_to_agent", "true");
				}
				if (options.description) {
					formData.append("description", String(options.description));
				}
				return { handled: true, payload: await client.postFormData( `/api/v1/ontology/studio/sessions/${encodeURIComponent(String(sid))}/sources`, formData, ) };
			}

			case "parse": {
				const sid = options.session;
				if (!sid) {
					throw new CliDomainError(
						"SYNTAX_INVALID_ARGUMENT",
						"Missing required --session flag",
						400,
					);
				}
				const body: Record<string, unknown> = {
					ocr_mode: options.ocr_mode || "auto",
				};
				const combinedSourceIds: string[] = [];
				if (options.source_id) {
					combinedSourceIds.push(
						...normalizeStringArrayOption(options.source_id),
					);
				}
				if (options.source_ids) {
					combinedSourceIds.push(
						...normalizeStringArrayOption(options.source_ids),
					);
				}
				if (combinedSourceIds.length > 0) {
					body.source_ids = combinedSourceIds;
				}
				const handle = await client.postJson(
					`/api/v1/ontology/studio/sessions/${encodeURIComponent(String(sid))}/parse`,
					body,
				);
				return { handled: true, payload: await client.waitForStudioJob(String(sid), handle, options) };
			}

			case "clarify": {
				const sid = options.session;
				if (!sid) {
					throw new CliDomainError(
						"SYNTAX_INVALID_ARGUMENT",
						"Missing required --session flag",
						400,
					);
				}
				const body: Record<string, unknown> = {};
				if (options.focus_concept) {
					body.focus_concept = options.focus_concept;
				}
				if (options.max_questions !== undefined) {
					body.max_questions = options.max_questions;
				}
				if (options.mode) {
					body.mode = options.mode;
				}
				const handle = await client.postJson(
					`/api/v1/ontology/studio/sessions/${encodeURIComponent(String(sid))}/clarify`,
					body,
				);
				return { handled: true, payload: await client.waitForStudioJob(String(sid), handle, options) };
			}

			case "answer": {
				const sid = options.session;
				if (!sid) {
					throw new CliDomainError(
						"SYNTAX_INVALID_ARGUMENT",
						"Missing required --session flag",
						400,
					);
				}

				const normalizedItems: Array<Record<string, unknown>> = [];
				let fileExpectedSha: string | undefined;

				if (options.file || options.file_content !== undefined) {
					let parsedRaw: unknown = options.file_content;
					if (parsedRaw === undefined && options.file) {
						parsedRaw = await client.readLocalJsonFile(
							String(options.file),
							"Answers",
						);
					} else if (typeof parsedRaw === "string") {
						try {
							parsedRaw = JSON.parse(parsedRaw);
						} catch (err) {
							throw new CliDomainError(
								"SYNTAX_INVALID_ARGUMENT",
								`Malformed answers JSON: ${err}`,
								400,
							);
						}
					}

					let rawList: unknown[] = [];
					if (Array.isArray(parsedRaw)) {
						rawList = parsedRaw;
					} else if (
						typeof parsedRaw === "object" &&
						parsedRaw !== null &&
						Array.isArray((parsedRaw as Record<string, unknown>).answers)
					) {
						const wrapper = parsedRaw as Record<string, unknown>;
						rawList = wrapper.answers as unknown[];
						if (typeof wrapper.expected_answers_sha256 === "string") {
							fileExpectedSha = wrapper.expected_answers_sha256;
						}
					} else if (typeof parsedRaw === "object" && parsedRaw !== null) {
						// Map dict of `{ [question_id]: value }` to ClarificationAnswerItem[]
						for (const [qid, val] of Object.entries(
							parsedRaw as Record<string, unknown>,
						)) {
							if (Array.isArray(val)) {
								rawList.push({
									question_id: qid,
									selected_options: val.map(String),
								});
							} else if (typeof val === "object" && val !== null) {
								rawList.push({
									question_id: qid,
									...(val as Record<string, unknown>),
								});
							} else {
								rawList.push({
									question_id: qid,
									user_text: String(val ?? ""),
								});
							}
						}
					}

					for (const entry of rawList) {
						if (typeof entry !== "object" || entry === null) {
							throw new CliDomainError(
								"SYNTAX_INVALID_ARGUMENT",
								"Each clarification answer must be an object.",
								400,
							);
						}
						const eObj = entry as Record<string, unknown>;
						const qid = String(eObj.question_id ?? "").trim();
						if (!qid) {
							throw new CliDomainError(
								"SYNTAX_INVALID_ARGUMENT",
								"Clarification answer item is missing 'question_id'.",
								400,
							);
						}
						const selectedOptions = Array.isArray(eObj.selected_options)
							? eObj.selected_options.map(String)
							: eObj.option !== undefined
								? normalizeStringArrayOption(eObj.option)
								: [];
						const userText =
							eObj.user_text !== undefined && eObj.user_text !== null
								? String(eObj.user_text)
								: eObj.answer !== undefined && eObj.answer !== null
									? String(eObj.answer)
									: eObj.text !== undefined && eObj.text !== null
										? String(eObj.text)
										: "";
						normalizedItems.push({
							question_id: qid,
							selected_options: selectedOptions,
							user_text: userText,
						});
					}
				}

				if (options.question_id) {
					const qid = String(options.question_id).trim();
					const selectedOptions = normalizeStringArrayOption(options.option);
					const userText =
						options.text !== undefined && options.text !== null
							? String(options.text)
							: "";
					if (selectedOptions.length === 0 && !userText.trim()) {
						throw new CliDomainError(
							"SYNTAX_INVALID_ARGUMENT",
							"Single-question answer requires --option (-o) and/or --text (-t).",
							400,
						);
					}
					normalizedItems.push({
						question_id: qid,
						selected_options: selectedOptions,
						user_text: userText,
					});
				}

				if (normalizedItems.length === 0) {
					throw new CliDomainError(
						"SYNTAX_INVALID_ARGUMENT",
						"Missing clarification answers: provide --file (-f) or --question-id (-q) with --option/-o or --text/-t.",
						400,
					);
				}

				const explicitAnswersShaRaw =
					options.expected_answers_sha256 !== undefined
						? String(options.expected_answers_sha256)
						: fileExpectedSha;
				let expectedAnswersSha = explicitAnswersShaRaw
					? explicitAnswersShaRaw.trim().replace(/^"|"$/g, "")
					: undefined;

				if (!expectedAnswersSha) {
					const encodedSid = encodeURIComponent(String(sid));
					const currentResp = await client.getJson(
						`/api/v1/ontology/studio/sessions/${encodedSid}/answers`,
					);
					const currentArr = Array.isArray(currentResp)
						? currentResp
						: typeof currentResp === "object" &&
								currentResp !== null &&
								Array.isArray((currentResp as Record<string, unknown>).answers)
							? ((currentResp as Record<string, unknown>).answers as unknown[])
							: [];
					if (currentArr.length > 0) {
						throw new CliDomainError(
							"MISSING_AUTO_COMMIT_PRECONDITION",
							"Existing clarification answers require explicit --expected-answers-sha256 precondition.",
							400,
						);
					}
					if (
						typeof currentResp === "object" &&
						currentResp !== null &&
						!Array.isArray(currentResp) &&
						typeof (currentResp as Record<string, unknown>).answers_sha256 ===
							"string"
					) {
						expectedAnswersSha = String(
							(currentResp as Record<string, unknown>).answers_sha256,
						);
					} else {
						expectedAnswersSha = computeCanonicalAnswersSha256(currentArr);
					}
				}

				const body = {
					answers: normalizedItems,
					expected_answers_sha256: expectedAnswersSha,
				};
				return { handled: true, payload: await client.postJson( `/api/v1/ontology/studio/sessions/${encodeURIComponent(String(sid))}/answers`, body, ) };
			}

			case "construct": {
				const sid = options.session;
				if (!sid) {
					throw new CliDomainError(
						"SYNTAX_INVALID_ARGUMENT",
						"Missing required --session flag",
						400,
					);
				}
				const mode = String(options.mode || "auto");
				const autoCommit = Boolean(options.auto_commit);
				const extraHeaders: Record<string, string> = {};

				const cleanConstructSha =
					options.base_sha256 !== undefined && options.base_sha256 !== null
						? String(options.base_sha256).trim().replace(/^"|"$/g, "")
						: undefined;

				if (autoCommit) {
					if (
						mode !== "deterministic" ||
						!options.base_version ||
						!cleanConstructSha
					) {
						throw new CliDomainError(
							"MISSING_AUTO_COMMIT_PRECONDITION",
							"--auto-commit requires --mode deterministic, --base-version, and --base-sha256.",
							400,
						);
					}
					extraHeaders["If-Match"] = `"${cleanConstructSha}"`;
				}

				const body: Record<string, unknown> = {
					base_version: options.base_version,
					base_sha256: cleanConstructSha,
					mode,
					auto_commit: autoCommit,
				};
				if (options.steering_prompt) {
					body.steering_prompt = options.steering_prompt;
				}
				const handle = await client.postJson(
					`/api/v1/ontology/studio/sessions/${encodeURIComponent(String(sid))}/construct`,
					body,
					extraHeaders,
				);
				return { handled: true, payload: await client.waitForStudioJob(String(sid), handle, options) };
			}

		default:
			return { handled: false };
	}
}
